"""The 8 ShopBot tools.

Rule for every tool: the customer is runtime.context.user_id (set by the server), never a
model argument. So even a fooled model can't read or change another customer's data.

Orders and cancellations are two steps: place_order / cancel_order only STAGE a
pending_action in the thread's state; confirm_action runs it, and only when the latest
customer message is a NEW message saying yes.
"""
import json
import re
from uuid import uuid4

from langchain.messages import HumanMessage, ToolMessage
from langchain.tools import ToolRuntime, tool
from langgraph.types import Command

from app.context import MEMORY_LIMIT, ShopContext
from app import db   # db.query(...), not query(...): search_products has a 'query' argument

MAX_RESULTS = 8
MAX_QUANTITY = 10
STOP_WORDS = {"the", "and", "for", "with", "under", "below", "over", "show", "want", "need", "any",
              "some", "have", "you", "your", "me", "my", "a", "an", "of", "in", "to", "rs", "pkr"}
YES = re.compile(r"^\s*(yes|yeah|yep|yup|y|confirm(ed)?|ok(ay)?|sure|haan|han|ji|jee|go ahead|"
                 r"place it|place the order|do it)\b", re.I)
SENSITIVE = re.compile(r"\b(?:\d[ -]?){12,19}\b|password|passcode|\bpin\b|\bcvv\b|\bcnic\b|iban", re.I)


def as_json(data) -> str:
    """Compact tool results keep tokens (and cost) low."""
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)


def user_of(runtime: ToolRuntime[ShopContext]) -> str:
    return runtime.context.user_id


def reply(runtime, text: str, **state_update) -> Command:
    """Return a tool result plus a state update (used to stage or clear pending_action)."""
    return Command(update={**state_update,
                           "messages": [ToolMessage(text, tool_call_id=runtime.tool_call_id)]})


def human_count(runtime) -> int:
    return sum(isinstance(m, HumanMessage) for m in runtime.state.get("messages", []))


# ── catalogue ──

@tool
def search_products(query: str = "", category: str = "", max_price: int | None = None,
                    min_price: int | None = None, in_stock_only: bool = False) -> str:
    """Search the Bazaar Lane catalogue. Use it for EVERY question about products, prices or stock.
    query: words like 'earbuds' or 'gift mother'; category: Clothing, Footwear, Accessories,
    Electronics, Home or Beauty; prices are PKR. Returns up to 8 products."""
    where, params = ["active"], []
    if category:
        where.append("lower(category) = lower(%s)")
        params.append(category.strip())
    if max_price is not None:
        where.append("price <= %s")
        params.append(int(max_price))
    if min_price is not None:
        where.append("price >= %s")
        params.append(int(min_price))
    if in_stock_only:
        where.append("stock > 0")

    # Score each product by how many query words appear in its name, category or description.
    words = [w for w in re.findall(r"[a-z0-9]+", query.lower()) if len(w) > 2 and w not in STOP_WORDS]
    words = [w[:-1] if w.endswith("s") and len(w) > 4 else w for w in words][:6]   # earbuds -> earbud
    score = " + ".join(["(CASE WHEN (name || ' ' || category || ' ' || description) ILIKE %s THEN 1 ELSE 0 END)"]
                       * len(words)) or "0"
    sql = (f"SELECT id, name, category, price AS price_pkr, stock, description, ({score}) AS score "
           f"FROM products WHERE {' AND '.join(where)} ORDER BY score DESC, price ASC LIMIT %s")
    rows = db.query(sql, [f"%{w}%" for w in words] + params + [MAX_RESULTS * 3])

    note = ""
    if words:
        matched = [r for r in rows if r["score"] > 0]
        if not matched:
            note = "No product matched those words; these are other products within the filters."
        rows = matched or rows
    for r in rows:
        r.pop("score")
    if not rows:
        return "No products found. Try a different category or price."
    return as_json({"products": rows[:MAX_RESULTS], "note": note} if note else {"products": rows[:MAX_RESULTS]})


@tool
def get_product_details(product_id: int) -> str:
    """Full details of one product by its id (#number)."""
    row = db.query_one("SELECT id, name, category, price AS price_pkr, stock, description "
                    "FROM products WHERE id = %s AND active", [product_id])
    return as_json(row) if row else f"No product with id {product_id}."


# ── orders (two-step, confirmed by the customer) ──

@tool
def place_order(items: list[dict], shipping_address: str, customer_name: str,
                runtime: ToolRuntime[ShopContext]) -> Command:
    """Prepare an order. items: [{"product_id": 9, "quantity": 1}]. Needs the full shipping address
    and the customer's name. This does NOT create the order: show the summary and ask the customer
    to reply yes; then call confirm_action."""
    if not items:
        return reply(runtime, "Error: no items. Ask which product and how many.")
    if len(shipping_address.strip()) < 10:
        return reply(runtime, "Error: the shipping address is missing or too short. Ask for the full address.")
    lines, total = [], 0
    for item in items[:10]:
        pid, qty = int(item.get("product_id", 0)), int(item.get("quantity", 1))
        if not 1 <= qty <= MAX_QUANTITY:
            return reply(runtime, f"Error: quantity must be 1 to {MAX_QUANTITY}.")
        p = db.query_one("SELECT id, name, price, stock FROM products WHERE id = %s AND active", [pid])
        if not p:
            return reply(runtime, f"Error: no product with id {pid}.")
        if p["stock"] < qty:
            return reply(runtime, f"Error: {p['name']} has only {p['stock']} in stock.")
        lines.append({"product_id": p["id"], "name": p["name"], "quantity": qty, "unit_price": p["price"]})
        total += p["price"] * qty
    pending = {"type": "order", "items": lines, "total": total,
               "shipping_address": shipping_address.strip()[:300], "customer_name": customer_name.strip()[:80],
               "asked_at": human_count(runtime)}
    summary = {"items": lines, "total_pkr": total, "ship_to": pending["shipping_address"],
               "name": pending["customer_name"], "payment": "cash on delivery", "delivery": "3 to 5 days"}
    return reply(runtime, "CONFIRMATION_REQUIRED. Show this summary and ask the customer to reply yes: "
                 + as_json(summary), pending_action=pending)


@tool
def cancel_order(order_id: int, runtime: ToolRuntime[ShopContext]) -> Command:
    """Prepare cancelling one of the customer's own orders (only status 'placed').
    Ask the customer to reply yes; then call confirm_action."""
    order = db.query_one("SELECT id, status, total FROM orders WHERE id = %s AND user_id = %s",
                      [order_id, user_of(runtime)])
    if not order:
        return reply(runtime, f"You have no order #{order_id}.")
    if order["status"] != "placed":
        return reply(runtime, f"Order #{order_id} is {order['status']} and can't be cancelled now.")
    pending = {"type": "cancel", "order_id": order["id"], "total": order["total"], "asked_at": human_count(runtime)}
    return reply(runtime, f"CONFIRMATION_REQUIRED. Ask the customer to reply yes to cancel order "
                 f"#{order_id} (Rs {order['total']:,}).", pending_action=pending)


@tool
def confirm_action(runtime: ToolRuntime[ShopContext]) -> Command:
    """Run the staged order or cancellation. Call ONLY when the customer's latest message says yes
    to the summary you showed."""
    pending = runtime.state.get("pending_action")
    if not pending:
        return reply(runtime, "Nothing is waiting for confirmation.")
    # Enforced in code, not left to the model: the yes must be a NEW customer message after the summary.
    messages = runtime.state.get("messages", [])
    last_human = next((m for m in reversed(messages) if isinstance(m, HumanMessage)), None)
    if human_count(runtime) <= pending.get("asked_at", 0) or not last_human or not YES.match(last_human.text):
        return reply(runtime, "Not confirmed: the customer must reply yes in a new message first.")

    user_id = user_of(runtime)
    if pending["type"] == "cancel":
        with db.get_pool().connection() as conn, conn.transaction():
            row = conn.execute("UPDATE orders SET status = 'cancelled' WHERE id = %s AND user_id = %s "
                               "AND status = 'placed' RETURNING id", [pending["order_id"], user_id]).fetchone()
            if row:   # put the stock back
                conn.execute("UPDATE products p SET stock = p.stock + i.quantity FROM order_items i "
                             "WHERE i.order_id = %s AND p.id = i.product_id", [row["id"]])
        text = (f"Order #{pending['order_id']} is cancelled." if row
                else f"Order #{pending['order_id']} could not be cancelled (it may have shipped).")
        return reply(runtime, text, pending_action=None)

    # Place the order in one transaction: lock the products, check stock, reduce it, save the order.
    try:
        with db.get_pool().connection() as conn, conn.transaction():
            for item in pending["items"]:
                p = conn.execute("SELECT stock, name FROM products WHERE id = %s FOR UPDATE",
                                 [item["product_id"]]).fetchone()
                if not p or p["stock"] < item["quantity"]:
                    raise ValueError(f"{item['name']} is no longer in stock")
                conn.execute("UPDATE products SET stock = stock - %s WHERE id = %s",
                             [item["quantity"], item["product_id"]])
            order = conn.execute(
                "INSERT INTO orders (user_id, customer_name, shipping_address, total) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                [user_id, pending["customer_name"], pending["shipping_address"], pending["total"]]).fetchone()
            for item in pending["items"]:
                conn.execute("INSERT INTO order_items (order_id, product_id, name, quantity, unit_price) "
                             "VALUES (%s, %s, %s, %s, %s)",
                             [order["id"], item["product_id"], item["name"], item["quantity"], item["unit_price"]])
    except ValueError as e:
        return reply(runtime, f"Order not placed: {e}.", pending_action=None)
    return reply(runtime, f"ORDER PLACED: order #{order['id']}, total Rs {pending['total']:,}, cash on "
                 f"delivery in 3 to 5 days, status 'placed'.", pending_action=None)


@tool
def get_my_orders(runtime: ToolRuntime[ShopContext], order_id: int | None = None) -> str:
    """The customer's own orders with status, items and total (newest first)."""
    sql = "SELECT id, status, total AS total_pkr, shipping_address, created_at::date AS date FROM orders WHERE user_id = %s"
    params = [user_of(runtime)]
    if order_id is not None:
        sql += " AND id = %s"
        params.append(order_id)
    orders = db.query(sql + " ORDER BY id DESC LIMIT 10", params)
    if not orders:
        return "This customer has no orders yet."
    for o in orders:
        o["items"] = db.query("SELECT name, quantity FROM order_items WHERE order_id = %s", [o["id"]])
    return as_json(orders)


# ── long-term memory (LangGraph Store, namespace ('memories', user_id)) ──

def memory_namespace(user_id: str) -> tuple:
    return ("memories", user_id)


@tool
def remember(fact: str, runtime: ToolRuntime[ShopContext]) -> str:
    """Save ONE lasting fact about the customer for future chats: name, address, sizes, budget,
    preferences. Never passwords, card/bank/ID numbers, or one-off requests."""
    fact = " ".join(fact.split())[:300]
    if not fact:
        return "Nothing to remember."
    if SENSITIVE.search(fact):   # enforced in code as well as in the prompt
        return "Not saved: never store passwords, card, bank or ID numbers."
    ns = memory_namespace(user_of(runtime))
    existing = runtime.store.search(ns, limit=100)
    if any(i.value.get("fact", "").lower() == fact.lower() for i in existing):
        return "Already remembered."
    if len(existing) >= 100:
        return "Memory is full; ask the customer which facts to forget."
    runtime.store.put(ns, str(uuid4()), {"fact": fact})
    return f"Remembered: {fact}"


@tool
def forget_me(runtime: ToolRuntime[ShopContext]) -> str:
    """Delete everything ShopBot remembers about this customer (when they ask)."""
    ns = memory_namespace(user_of(runtime))
    items = runtime.store.search(ns, limit=1000)
    for item in items:
        runtime.store.delete(item.namespace, item.key)
    return f"Forgot {len(items)} saved fact(s)."


TOOLS = [search_products, get_product_details, place_order, cancel_order, confirm_action,
         get_my_orders, remember, forget_me]
__all__ = ["TOOLS", "memory_namespace", "MEMORY_LIMIT"]
