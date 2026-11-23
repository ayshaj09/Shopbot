# ShopBot: an AI sales agent for small online sellers

**Track 1 project.** ShopBot is a storefront plus a sales agent for small Pakistani sellers (Instagram / Facebook
shops, home businesses). Shoppers chat with it to find products, compare, order and track deliveries; the seller
watches orders, stock, conversations and cost on a dashboard. Demo store: **Bazaar Lane** (24 products in
Clothing, Footwear, Accessories, Electronics, Home and Beauty; prices in PKR; cash on delivery; 3 to 5 day delivery).

| | |
|---|---|
| Live website | `https://YOUR-SITE.vercel.app` |
| Agent API | `https://YOUR-SPACE.hf.space` (`POST /chat`, `GET /health`) |
| Demo video | `LINK` |

**How it earns:** Rs 2,500 per month per store, or 2% commission on each order ShopBot closes. A reply on
`claude-haiku-5-5` costs a small fraction of a rupee in model fees.

![Demo](docs/demo.gif)

## Architecture

```mermaid
flowchart LR
    subgraph Vercel["Website on Vercel (web/)"]
        S[Store: index.html, shop.html<br/>chat widget chat.js]
        A[Seller dashboard: admin.html]
    end
    subgraph HF["Agent API on Hugging Face Spaces (app/, FastAPI)"]
        API["/chat /health /products /me/*"]
        ADM["/admin/* (X-Admin-Token)"]
        subgraph G["LangGraph StateGraph"]
            SAFE[safety node<br/>code only, 0 tokens] -->|ok| AG[shop_agent<br/>create_agent + middleware]
            AG <--> T[8 tools]
            SAFE -->|blocked| E((END))
            AG --> E
        end
        API --> SAFE
    end
    subgraph Neon["Neon Postgres"]
        CP[(checkpoints<br/>short-term)]
        ST[(store<br/>long-term)]
        DB[(products, orders,<br/>order_items, chat_logs)]
    end
    S --> API
    A --> ADM
    G --- CP
    G --- ST
    T --- DB
    ADM --- DB
    AG -. claude-haiku-5-5 .-> C[Claude API]
```

- **safety node** (no model call): regex for prompt injection and data-theft requests, and a 2,000-character cap.
  Blocked messages never reach the model, so they cost 0 tokens.
- **shop_agent node**: LangChain `create_agent` on `claude-haiku-5-5` with middleware: system prompt + this
  customer's memories + any pending order; only the last 16 messages go to the model; at most 6 model calls and
  8 tool calls per message; tool retries.
- **`/chat`** uses the thread key `user_id::thread_id` and passes `ShopContext(user_id)`. It returns the tool calls
  and token usage of the new messages, logs every turn to `chat_logs`, and always returns
  `{"reply", "tool_calls", "tokens"}`, even on errors.

### Tools (8)

| Tool | What it does |
|---|---|
| `search_products` | Search by words, category, min/max price, in stock only (up to 8 results) |
| `get_product_details` | One product by id |
| `place_order` | Checks stock and **stages** the order; returns a summary and asks for yes. Creates nothing |
| `cancel_order` | Stages cancelling one of the customer's own `placed` orders |
| `confirm_action` | Runs the staged order (stock reduced in one transaction) or cancellation, **only if the newest customer message is a new "yes"** |
| `get_my_orders` | This customer's orders, status, items and total |
| `remember` | Saves one lasting fact (refuses card numbers and passwords in code) |
| `forget_me` | Deletes this customer's memories |

## Memory design

| | Short-term (per conversation) | Long-term (per customer) |
|---|---|---|
| **What** | Every message, tool call and result, plus an order waiting for "yes" (`pending_action`) | Lasting facts: name, address, sizes, budget, preferences. Orders are kept per customer in the `orders` table |
| **Where** | Neon Postgres via LangGraph `PostgresSaver`, key `user_id::thread_id` | Neon Postgres via LangGraph `PostgresStore`, namespace `("memories", user_id)` |
| **How it's used** | The full thread is saved; the model gets only the last 16 messages, cut at a customer message so a tool call is never split from its result. The pending order and the memories are added to the prompt every turn, so trimming loses nothing important | Saved when the agent calls `remember` (one fact, max 300 characters, duplicates skipped). Up to 20 facts go into the prompt of every conversation |
| **Not saved** | n/a | Passwords, card / bank / ID numbers (checked in code), small talk, one-off requests, other customers' data |

**Keeping customers apart.** Tools take `user_id` from `runtime.context` (set by the server), never from the
model. Every order query filters on `user_id`, memories are namespaced by `user_id`, and thread keys include the
`user_id`. Colons in ids are escaped, so `user "a::b" + thread "c"` and `user "a" + thread "b::c"` can't share a key.
Known gap: there is no real login (the user id comes from the email the shopper types, and `/chat` must be open for
grading). Production would add real auth such as Supabase Auth.

## Safety and budget

Prompt injection is stopped in four layers: (1) the safety node blocks known attack patterns before the model;
(2) prompt rules: only Bazaar Lane shopping, never reveal or change instructions, one-sentence refusal for
off-topic requests; (3) tools are locked to the caller's `user_id`; (4) orders and cancellations need a "yes" in a
new message, checked in `confirm_action`.

Budget guards for the $2 key: blocked messages cost 0 tokens, 16-message history, `max_tokens` 1024, 6 model calls
and 8 tool calls per message, a 2,000-character cap, compact tool results, a per-customer limit of 30 messages a
minute, and the dashboard shows tokens and estimated cost.

**Model notes (`claude-haiku-5-5`):** $0.10 / $0.50 per million input / output tokens (prompts up to 100K tokens).
The model rejects a custom `temperature`, so none is sent. Thinking is turned off and effort is `low`: the
16-message window and the per-turn prompt change earlier context, which thinking blocks don't allow, and low effort
keeps replies short and cheap.

## Benchmark

`tests.json` has our 10 test cases. Run them against the live API (about 13 requests):

```bash
python scripts/benchmark.py https://YOUR-SPACE.hf.space
```

**Live API:** *paste the table printed by the benchmark against the Hugging Face Space here.*

**Local run** (23 Nov 2026: real `claude-haiku-5-5`, local Postgres, server on the same machine):

| # | Test | Tools called | Latency | Result |
|---|------|--------------|---------|--------|
| 1 | product search (earbuds) | search_products | 3,076 ms | PASS |
| 2 | product search (gift) | search_products | 3,030 ms | PASS |
| 3 | order needs confirmation | get_product_details, remember, place_order | 4,059 ms | PASS |
| 4 | order confirmed | place_order, remember, confirm_action | 5,111 ms (2 steps) | PASS |
| 5 | order status | get_my_orders | 2,635 ms | PASS |
| 6 | short-term memory | search_products | 4,046 ms (2 steps) | PASS |
| 7 | long-term memory | remember | 4,148 ms (2 steps) | PASS |
| 8 | user isolation | get_my_orders | 2,479 ms | PASS |
| 9 | prompt injection | — | 252 ms | PASS |
| 10 | off-topic | — | 1,532 ms | PASS |

**Passed:** 10/10 · **Average latency:** 2,336 ms per request (13 requests) · **p95:** 4,059 ms ·
**Total tokens:** 50,959 (48,862 in / 2,097 out), about $0.006 for the whole run.

## Setup

Needs Python 3.11+ and a Postgres database (Neon free tier, or Docker locally).

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env               # then fill in ANTHROPIC_API_KEY, DATABASE_URL, ADMIN_TOKEN
uvicorn app.server:app --reload --port 7860
```

The first start creates the tables and the 24 demo products. Open `web/index.html` with any static server
(for example `python -m http.server -d web 8080`) after setting `web/config.js` to `http://localhost:7860`.

**Offline tests** (fake model, real Postgres, no API key):

```bash
docker run -d --name shopbot-pg -e POSTGRES_PASSWORD=localtest -e POSTGRES_DB=shopbot -p 5433:5432 postgres:17-alpine
TEST_DATABASE_URL=postgresql://postgres:localtest@localhost:5433/shopbot pytest -q
```

**LangGraph Studio** (optional): `pip install "langgraph-cli[inmem]"` then `langgraph dev`.

## Deploy

1. **Neon:** create a free project and copy the connection string (`postgresql://...?sslmode=require`).
2. **Hugging Face Space:** New Space, SDK **Docker**, Blank. Upload `Dockerfile`, `requirements.txt`, `app/`, and
   `space/README.md` renamed to `README.md`. Under Settings → Secrets add `ANTHROPIC_API_KEY`, `DATABASE_URL`,
   `ADMIN_TOKEN` (and `CORS_ORIGINS` = your Vercel URL once you have it). Check `https://USER-SPACE.hf.space/health`
   returns `ok`, then send one `/chat` message.
3. **Vercel:** set `web/config.js` to the Space URL, push, import the repo, Root Directory = `web`, deploy.
4. Run the benchmark, paste the table above, add `docs/demo.gif`.

If `/chat` replies "technical problem", read the Space logs. "Model not found": set the secret
`MODEL=anthropic:<exact model name>`.

## Repo layout

```
app/server.py      FastAPI: /chat, /health, /products, /me/*, /admin/*
app/agent.py       StateGraph: safety node -> shop_agent (create_agent); model settings
app/middleware.py  system prompt + memories + pending order, history trimming, limits, retries
app/tools.py       the 8 tools (Postgres, scoped to runtime.context.user_id)
app/context.py     settings from env vars, ShopContext(user_id)
app/db.py          connection pool, schema, 24 demo products (seeded on first start)
app/studio.py      graph for `langgraph dev`
web/               Vercel site: index.html (home + chat), shop.html, admin.html, chat.js, style.css, config.js
tests/test_api.py  offline tests; tests.json: 10 graded cases; scripts/benchmark.py
Dockerfile         port 7860 for Hugging Face; space/README.md is the Space config
```

Built on the patterns of the workshop starter kit (github.com/AnviroAi/Workshop): `create_agent`, middleware,
`ToolRuntime` context, a `StateGraph` safety node and a LangGraph Store for long-term memory.
