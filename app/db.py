"""Postgres (Neon) connection pool, schema and the 24 demo products.

One pool is shared by the app tables, the LangGraph checkpointer (short-term memory)
and the LangGraph store (long-term memory). Both LangGraph classes need autocommit
and dict rows, so the pool is opened with those settings.
"""
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.context import DATABASE_URL

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id          SERIAL PRIMARY KEY,
    name        TEXT NOT NULL,
    category    TEXT NOT NULL,
    price       INTEGER NOT NULL CHECK (price >= 0),          -- PKR
    stock       INTEGER NOT NULL DEFAULT 0 CHECK (stock >= 0),
    description TEXT NOT NULL DEFAULT '',
    image_url   TEXT NOT NULL DEFAULT '',
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS orders (
    id               SERIAL PRIMARY KEY,
    user_id          TEXT NOT NULL,
    customer_name    TEXT NOT NULL DEFAULT '',
    shipping_address TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'placed',          -- placed | shipped | delivered | cancelled
    total            INTEGER NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS orders_user_idx ON orders (user_id);
CREATE TABLE IF NOT EXISTS order_items (
    id         SERIAL PRIMARY KEY,
    order_id   INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    product_id INTEGER NOT NULL REFERENCES products(id),
    name       TEXT NOT NULL,
    quantity   INTEGER NOT NULL CHECK (quantity > 0),
    unit_price INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS chat_logs (
    id           SERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL,
    thread_id    TEXT NOT NULL,
    user_message TEXT NOT NULL,
    reply        TEXT NOT NULL,
    tool_calls   JSONB NOT NULL DEFAULT '[]',
    tokens_in    INTEGER NOT NULL DEFAULT 0,
    tokens_out   INTEGER NOT NULL DEFAULT 0,
    latency_ms   INTEGER NOT NULL DEFAULT 0,
    blocked      BOOLEAN NOT NULL DEFAULT FALSE,
    error        BOOLEAN NOT NULL DEFAULT FALSE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS chat_logs_user_idx ON chat_logs (user_id, created_at);
"""

# (id, name, category, price PKR, stock, description). Ids are fixed so tests.json can rely on them.
DEMO_PRODUCTS = [
    (1, "Lawn Kurta", "Clothing", 3200, 15, "Printed summer lawn kurta, soft breathable cotton. Sizes S to XL."),
    (2, "Embroidered Pashmina Shawl", "Clothing", 4500, 8, "Warm wool-blend shawl with hand embroidery. A classic winter gift."),
    (3, "Men's Cotton Shalwar Kameez", "Clothing", 3800, 12, "Wash-and-wear cotton suit in white, beige or navy. Sizes M to XXL."),
    (4, "Kids' Winter Hoodie", "Clothing", 2200, 10, "Fleece-lined hoodie for ages 4 to 12."),
    (5, "Hand-embroidered Khussa", "Footwear", 2800, 14, "Traditional flat khussa with gold thread work. Sizes 36 to 41."),
    (6, "Peshawari Chappal", "Footwear", 3900, 9, "Handmade leather Peshawari chappal. Sizes 7 to 11."),
    (7, "Women's Comfort Sandals", "Footwear", 2400, 11, "Cushioned everyday sandals with a soft strap. Sizes 36 to 41."),
    (8, "Running Shoes", "Footwear", 8900, 6, "Lightweight running shoes with a cushioned sole and breathable mesh. Sizes 7 to 11."),
    (9, "Leather Wallet", "Accessories", 1800, 20, "Slim hand-stitched genuine leather wallet with 6 card slots."),
    (10, "Embroidered Tote Bag", "Accessories", 2600, 7, "Roomy cotton tote with Sindhi embroidery."),
    (11, "Silver Jhumka Earrings", "Accessories", 3500, 5, "Oxidised silver-plated jhumkas, light enough for all-day wear."),
    (12, "Classic Analog Watch", "Accessories", 6500, 4, "Stainless steel analog watch with a leather strap, water resistant."),
    (13, "Wireless Earbuds", "Electronics", 5900, 18, "Bluetooth 5.3 earbuds, 24-hour battery with case, noise reduction on calls."),
    (14, "20000mAh Power Bank", "Electronics", 4800, 10, "Fast-charging power bank with two USB ports and USB-C."),
    (15, "Smart Fitness Band", "Electronics", 7500, 3, "Step, heart-rate and sleep tracking. 10-day battery."),
    (16, "Bluetooth Speaker", "Electronics", 6900, 8, "Portable waterproof speaker with deep bass, 12-hour battery."),
    (17, "Scented Candle Set", "Home", 2500, 12, "Set of 3 soy candles: rose, jasmine and oud. Lovely as a gift."),
    (18, "Ceramic Tea Set", "Home", 3600, 6, "Six cups, saucers and a teapot with a hand-painted floral pattern."),
    (19, "Cotton Bedsheet Set", "Home", 4200, 9, "Double bedsheet with two pillow covers, 100% cotton."),
    (20, "Brass Table Lamp", "Home", 5200, 4, "Polished brass lamp with a linen shade."),
    (21, "Blue Pottery Vase", "Home", 3400, 0, "Multani blue pottery vase, handmade. Currently out of stock."),
    (22, "Rose Attar", "Beauty", 1500, 25, "Alcohol-free rose attar, 12 ml."),
    (23, "Herbal Hair Oil", "Beauty", 950, 30, "Amla, coconut and almond hair oil, 200 ml."),
    (24, "Skincare Gift Box", "Beauty", 3800, 7, "Face wash, toner and moisturiser in a gift box. A popular present for mothers."),
]

pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    """Open the shared pool once. prepare_threshold=None keeps it working behind Neon's pooler."""
    global pool
    if pool is None:
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL is not set")
        pool = ConnectionPool(
            DATABASE_URL, min_size=1, max_size=10, open=True,
            kwargs={"autocommit": True, "prepare_threshold": None, "row_factory": dict_row},
        )
    return pool


def setup_database() -> None:
    """Create tables and seed the demo products the first time the app starts."""
    with get_pool().connection() as conn:
        conn.execute(SCHEMA)
        if conn.execute("SELECT count(*) AS n FROM products").fetchone()["n"] == 0:
            with conn.transaction():
                for p in DEMO_PRODUCTS:
                    conn.execute(
                        "INSERT INTO products (id, name, category, price, stock, description) "
                        "VALUES (%s, %s, %s, %s, %s, %s)", p)
                # keep the id sequence ahead of the fixed demo ids
                conn.execute("SELECT setval('products_id_seq', (SELECT max(id) FROM products))")


def query(sql: str, params=None) -> list[dict]:
    with get_pool().connection() as conn:
        return conn.execute(sql, params).fetchall()


def query_one(sql: str, params=None) -> dict | None:
    rows = query(sql, params)
    return rows[0] if rows else None
