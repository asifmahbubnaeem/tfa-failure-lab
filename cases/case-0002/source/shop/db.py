"""Postgres access. One short connection per call: simple, and a stopped database shows up
immediately as a failing request instead of a stale pool."""

import os
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

PRODUCTS = [
    # slug, name, price, description. Prices chosen so text order differs from number order.
    ("notebook-set", "Notebook Set", "9.99", "Three dotted notebooks for test plans and scribbles."),
    ("trail-water-bottle", "Trail Water Bottle", "12.49", "Insulated steel bottle, keeps water cold for a day."),
    ("canvas-tote-bag", "Canvas Tote Bag", "15.99", "Heavy canvas tote with an inside pocket."),
    ("wireless-mouse", "Wireless Mouse", "24.99", "Quiet clicks, two-month battery."),
    ("desk-lamp", "Desk Lamp", "34.50", "Warm LED lamp with a dimmer."),
    ("mechanical-keyboard", "Mechanical Keyboard", "79.00", "Tactile switches and a detachable cable."),
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    id SERIAL PRIMARY KEY,
    slug TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    price NUMERIC(10, 2) NOT NULL,
    description TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS cart_items (
    cart_id TEXT NOT NULL,
    product_id INT NOT NULL REFERENCES products(id),
    PRIMARY KEY (cart_id, product_id)
);
CREATE TABLE IF NOT EXISTS orders (
    id SERIAL PRIMARY KEY,
    username TEXT NOT NULL,
    item_total NUMERIC(10, 2) NOT NULL,
    tax NUMERIC(10, 2) NOT NULL,
    total NUMERIC(10, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def connect():
    return psycopg.connect(
        os.environ["DATABASE_URL"], connect_timeout=3, autocommit=True, row_factory=dict_row
    )


def init() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)
        for slug, name, price, desc in PRODUCTS:
            conn.execute(
                "INSERT INTO products (slug, name, price, description) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (slug) DO NOTHING",
                (slug, name, Decimal(price), desc),
            )


def products() -> list[dict]:
    with connect() as conn:
        return conn.execute("SELECT id, slug, name, price, description FROM products ORDER BY name").fetchall()


def cart(cart_id: str) -> list[dict]:
    with connect() as conn:
        return conn.execute(
            "SELECT p.id, p.slug, p.name, p.price, p.description FROM cart_items c "
            "JOIN products p ON p.id = c.product_id WHERE c.cart_id = %s ORDER BY p.name",
            (cart_id,),
        ).fetchall()


def add_to_cart(cart_id: str, slug: str) -> None:
    with connect() as conn:
        conn.execute(
            "INSERT INTO cart_items (cart_id, product_id) "
            "SELECT %s, id FROM products WHERE slug = %s ON CONFLICT DO NOTHING",
            (cart_id, slug),
        )


def remove_from_cart(cart_id: str, slug: str) -> None:
    with connect() as conn:
        conn.execute(
            "DELETE FROM cart_items WHERE cart_id = %s "
            "AND product_id = (SELECT id FROM products WHERE slug = %s)",
            (cart_id, slug),
        )


def clear_cart(cart_id: str) -> None:
    with connect() as conn:
        conn.execute("DELETE FROM cart_items WHERE cart_id = %s", (cart_id,))


def create_order(username: str, item_total: Decimal, tax: Decimal, total: Decimal) -> int:
    with connect() as conn:
        row = conn.execute(
            "INSERT INTO orders (username, item_total, tax, total) VALUES (%s, %s, %s, %s) RETURNING id",
            (username, item_total, tax, total),
        ).fetchone()
        return row["id"]


def ping() -> None:
    with connect() as conn:
        conn.execute("SELECT 1")
