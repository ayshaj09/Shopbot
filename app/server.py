"""FastAPI server: the graded /chat and /health, the store pages' /products and /me/*,
and the seller dashboard's /admin/* (needs the X-Admin-Token header).

Run locally:  uvicorn app.server:app --reload --port 7860
"""
import hmac
import json
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from langchain.messages import AIMessage, HumanMessage
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.store.postgres import PostgresStore
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app import context as cfg
from app.agent import build_graph
from app.context import ShopContext
from app.db import get_pool, query, query_one, setup_database
from app.tools import memory_namespace

log = logging.getLogger("shopbot")
logging.basicConfig(level=logging.INFO)

ERROR_REPLY = "Sorry, ShopBot had a technical problem. Please try again in a moment."
RATE_REPLY = "You're sending messages very fast. Please wait a minute and try again."
STATUSES = {"placed", "shipped", "delivered", "cancelled"}

state = {}   # graph and store, set at startup. Tests may put a fake chat model in state["model"].


@asynccontextmanager
async def lifespan(_app):
    """Open the database, create tables and LangGraph's checkpoint/store tables, build the graph."""
    pool = get_pool()
    setup_database()
    checkpointer, store = PostgresSaver(pool), PostgresStore(pool)
    checkpointer.setup()
    store.setup()
    state["store"] = store
    state["graph"] = build_graph(model=state.get("model"), checkpointer=checkpointer, store=store)
    yield


app = FastAPI(title="ShopBot API", lifespan=lifespan)
origins = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False,
                   allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["Content-Type", "X-Admin-Token"])


# ── helpers ──

def thread_key(user_id: str, thread_id: str) -> str:
    """'user_id::thread_id'. ':' is escaped in both parts so two different users can never
    produce the same key (e.g. user 'a::b' + thread 'c' vs user 'a' + thread 'b::c')."""
    esc = lambda s: s.replace("%", "%25").replace(":", "%3A")
    return f"{esc(user_id)}::{esc(thread_id)}"


def graph_config(user_id: str, thread_id: str) -> dict:
    return {"configurable": {"thread_id": thread_key(user_id, thread_id)}}


_recent = defaultdict(deque)


def rate_limited(user_id: str) -> bool:
    """Simple in-memory per-user limit (protects the $2 model budget)."""
    if cfg.RATE_LIMIT_PER_MIN <= 0:
        return False
    now, window = time.time(), _recent[user_id]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= cfg.RATE_LIMIT_PER_MIN:
        return True
    window.append(now)
    return False


def clean_id(value, name: str) -> str:
    value = str(value or "").strip()
    if not value or len(value) > 128:
        raise ValueError(f"{name} must be 1 to 128 characters")
    return value


def log_chat(user_id, thread_id, message, reply, tool_calls, tokens, latency_ms, blocked=False, error=False):
    try:
        query("INSERT INTO chat_logs (user_id, thread_id, user_message, reply, tool_calls, tokens_in, tokens_out, "
              "latency_ms, blocked, error) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id",
              [user_id, thread_id, message[:4000], reply[:4000], json.dumps(tool_calls), tokens["in"], tokens["out"],
               latency_ms, blocked, error])
    except Exception:
        log.exception("could not write chat log")


# ── graded endpoints ──

@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"


@app.post("/chat")
async def chat(request: Request):
    """Always answers with {"reply", "tool_calls", "tokens"}, even for bad input or errors."""
    started = time.perf_counter()
    empty = {"in": 0, "out": 0}
    try:
        body = await request.json()
        user_id = clean_id(body.get("user_id"), "user_id")
        thread_id = clean_id(body.get("thread_id"), "thread_id")
        message = str(body.get("message") or "").strip()
        if not message:
            raise ValueError("message is empty")
    except Exception as e:
        return JSONResponse({"reply": f"Sorry, I couldn't read that request ({e}).", "tool_calls": [], "tokens": empty})

    if rate_limited(user_id):
        return {"reply": RATE_REPLY, "tool_calls": [], "tokens": empty}

    # The graph is synchronous (psycopg pool); run it off the event loop.
    result = await run_in_threadpool(run_turn, user_id, thread_id, message)
    latency_ms = int((time.perf_counter() - started) * 1000)
    log_chat(user_id, thread_id, message, result["reply"], result["tool_calls"], result["tokens"], latency_ms,
             blocked=result.pop("_blocked", False), error=result.pop("_error", False))
    return result


def run_turn(user_id: str, thread_id: str, message: str) -> dict:
    graph = state["graph"]
    try:
        out = graph.invoke({"messages": [HumanMessage(message)]}, graph_config(user_id, thread_id),
                           context=ShopContext(user_id=user_id))
        messages = out["messages"]
        # New messages = everything after the customer message we just sent.
        start = max(i for i, m in enumerate(messages) if isinstance(m, HumanMessage)) + 1
        new = [m for m in messages[start:] if isinstance(m, AIMessage)]
        tool_calls = [{"name": tc["name"], "args": tc["args"]} for m in new for tc in m.tool_calls]
        tokens = {"in": sum((m.usage_metadata or {}).get("input_tokens", 0) for m in new),
                  "out": sum((m.usage_metadata or {}).get("output_tokens", 0) for m in new)}
        reply = next((m.text for m in reversed(new) if m.text.strip()), "") or \
            "Sorry, I couldn't finish that. Could you say it another way?"
        return {"reply": reply, "tool_calls": tool_calls, "tokens": tokens, "_blocked": bool(out.get("blocked"))}
    except Exception:
        log.exception("chat turn failed")
        return {"reply": ERROR_REPLY, "tool_calls": [], "tokens": {"in": 0, "out": 0}, "_error": True}


# ── customer pages (no login: user_id comes from the email the shopper types) ──

PRODUCT_COLS = "id, name, category, price, stock, description, image_url"


@app.get("/products")
def products(category: str = "", q: str = ""):
    sql, params = f"SELECT {PRODUCT_COLS} FROM products WHERE active", []
    if category:
        sql += " AND lower(category) = lower(%s)"
        params.append(category)
    if q:
        sql += " AND (name ILIKE %s OR description ILIKE %s)"
        params += [f"%{q}%", f"%{q}%"]
    return query(sql + " ORDER BY id", params)


def orders_with_items(where: str, params: list, limit: int = 100) -> list[dict]:
    orders = query(f"SELECT id, user_id, customer_name, status, total, shipping_address, created_at "
                   f"FROM orders {where} ORDER BY id DESC LIMIT {int(limit)}", params)
    if orders:
        items = query("SELECT order_id, name, quantity, unit_price FROM order_items WHERE order_id = ANY(%s) "
                      "ORDER BY id", [[o["id"] for o in orders]])
        for o in orders:
            o["items"] = [{k: i[k] for k in ("name", "quantity", "unit_price")} for i in items if i["order_id"] == o["id"]]
    return orders


@app.get("/me/orders")
def my_orders(user_id: str = Query(..., min_length=1, max_length=128)):
    orders = orders_with_items("WHERE user_id = %s", [user_id])
    for o in orders:
        o.pop("user_id")
        o.pop("customer_name")
    return orders


@app.get("/me/memories")
def my_memories(user_id: str = Query(..., min_length=1, max_length=128)):
    items = state["store"].search(memory_namespace(user_id), limit=100)
    return [{"fact": i.value.get("fact", ""), "saved": i.created_at} for i in items]


@app.delete("/me/memories")
def forget_my_memories(user_id: str = Query(..., min_length=1, max_length=128)):
    store = state["store"]
    items = store.search(memory_namespace(user_id), limit=1000)
    for i in items:
        store.delete(i.namespace, i.key)
    return {"deleted": len(items)}


@app.get("/me/history")
def my_history(user_id: str = Query(..., min_length=1, max_length=128),
               thread_id: str = Query(..., min_length=1, max_length=128)):
    snapshot = state["graph"].get_state(graph_config(user_id, thread_id))
    history = []
    for m in (snapshot.values or {}).get("messages", []):
        if isinstance(m, HumanMessage):
            history.append({"role": "user", "text": m.text})
        elif isinstance(m, AIMessage) and m.text.strip():
            history.append({"role": "assistant", "text": m.text})
    return history


# ── seller dashboard ──

def require_admin(x_admin_token: str = Header(default="")):
    if not cfg.ADMIN_TOKEN or not hmac.compare_digest(x_admin_token.encode(), cfg.ADMIN_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Invalid admin token")


admin = [Depends(require_admin)]


@app.get("/admin/stats", dependencies=admin)
def admin_stats():
    o = query_one("SELECT count(*) FILTER (WHERE status <> 'cancelled') AS orders, "
                  "coalesce(sum(total) FILTER (WHERE status <> 'cancelled'), 0) AS revenue, "
                  "count(*) FILTER (WHERE status IN ('placed', 'shipped')) AS open_orders FROM orders")
    c = query_one("SELECT count(*) AS messages, count(DISTINCT (user_id, thread_id)) AS conversations, "
                  "coalesce(sum(tokens_in), 0) AS tokens_in, coalesce(sum(tokens_out), 0) AS tokens_out, "
                  "coalesce(round(avg(latency_ms) FILTER (WHERE NOT blocked)), 0) AS avg_latency_ms, "
                  "count(*) FILTER (WHERE blocked) AS blocked FROM chat_logs")
    users = query_one("SELECT count(*) AS n FROM (SELECT user_id FROM chat_logs UNION SELECT user_id FROM orders) u")
    cost = (c["tokens_in"] * cfg.PRICE_IN_PER_M + c["tokens_out"] * cfg.PRICE_OUT_PER_M) / 1_000_000
    low = query("SELECT id, name, stock FROM products WHERE active AND stock <= 5 ORDER BY stock, id")
    return {**o, **{k: int(v) for k, v in c.items()}, "users": users["n"],
            "est_cost_usd": round(cost, 4), "low_stock": low}


@app.get("/admin/orders", dependencies=admin)
def admin_orders():
    return orders_with_items("", [], limit=300)


class StatusIn(BaseModel):
    status: str


@app.patch("/admin/orders/{order_id}", dependencies=admin)
def admin_update_order(order_id: int, body: StatusIn):
    status = body.status.lower().strip()
    if status not in STATUSES:
        raise HTTPException(400, f"status must be one of {sorted(STATUSES)}")
    with get_pool().connection() as conn, conn.transaction():
        old = conn.execute("SELECT status FROM orders WHERE id = %s FOR UPDATE", [order_id]).fetchone()
        if not old:
            raise HTTPException(404, "Order not found")
        conn.execute("UPDATE orders SET status = %s WHERE id = %s", [status, order_id])
        if status == "cancelled" and old["status"] != "cancelled":   # put the stock back
            conn.execute("UPDATE products p SET stock = p.stock + i.quantity FROM order_items i "
                         "WHERE i.order_id = %s AND p.id = i.product_id", [order_id])
    return {"id": order_id, "status": status}


@app.get("/admin/products", dependencies=admin)
def admin_products():
    return query(f"SELECT {PRODUCT_COLS}, active FROM products ORDER BY id")


class ProductIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(min_length=1, max_length=60)
    price: int = Field(ge=0, le=10_000_000)
    stock: int = Field(ge=0, le=100_000)
    description: str = Field(default="", max_length=1000)
    image_url: str = Field(default="", max_length=500)


class ProductPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, min_length=1, max_length=60)
    price: int | None = Field(default=None, ge=0, le=10_000_000)
    stock: int | None = Field(default=None, ge=0, le=100_000)
    description: str | None = Field(default=None, max_length=1000)
    image_url: str | None = Field(default=None, max_length=500)
    active: bool | None = None


@app.post("/admin/products", dependencies=admin)
def admin_add_product(body: ProductIn):
    return query_one(f"INSERT INTO products (name, category, price, stock, description, image_url) "
                     f"VALUES (%s,%s,%s,%s,%s,%s) RETURNING {PRODUCT_COLS}, active",
                     [body.name, body.category, body.price, body.stock, body.description, body.image_url])


@app.patch("/admin/products/{product_id}", dependencies=admin)
def admin_update_product(product_id: int, body: ProductPatch):
    changes = body.model_dump(exclude_none=True)
    if not changes:
        raise HTTPException(400, "Nothing to update")
    sets = ", ".join(f"{k} = %s" for k in changes)   # keys come from the model above, not the request
    row = query_one(f"UPDATE products SET {sets} WHERE id = %s RETURNING {PRODUCT_COLS}, active",
                    [*changes.values(), product_id])
    if not row:
        raise HTTPException(404, "Product not found")
    return row


@app.get("/admin/conversations", dependencies=admin)
def admin_conversations(limit: int = Query(300, ge=1, le=1000)):
    return query("SELECT user_id, thread_id, user_message, reply, tool_calls, tokens_in, tokens_out, latency_ms, "
                 "blocked, created_at FROM chat_logs ORDER BY id DESC LIMIT %s", [limit])


@app.get("/admin/customers", dependencies=admin)
def admin_customers():
    rows = query("""
        WITH users AS (SELECT user_id FROM chat_logs UNION SELECT user_id FROM orders)
        SELECT u.user_id,
               (SELECT count(DISTINCT thread_id) FROM chat_logs c WHERE c.user_id = u.user_id) AS conversations,
               (SELECT count(*) FROM chat_logs c WHERE c.user_id = u.user_id) AS messages,
               (SELECT count(*) FROM orders o WHERE o.user_id = u.user_id) AS orders,
               greatest((SELECT max(created_at) FROM chat_logs c WHERE c.user_id = u.user_id),
                        (SELECT max(created_at) FROM orders o WHERE o.user_id = u.user_id)) AS last_seen
        FROM users u ORDER BY last_seen DESC NULLS LAST LIMIT 300""")
    store = state["store"]
    for r in rows:
        r["memories"] = [i.value.get("fact", "") for i in store.search(memory_namespace(r["user_id"]), limit=20)]
    return rows

