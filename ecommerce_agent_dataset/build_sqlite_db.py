#!/usr/bin/env python3
"""Build the SQLite side of the ecommerce dataset.

How to call:
    python build_sqlite_db.py --data-root . --db-path ecommerce.db
    python build_sqlite_db.py --data-root . --db-path ecommerce.db --full-rebuild

Programmatic use:
    from build_sqlite_db import build_sqlite_database
    build_sqlite_database(Path("."), Path("ecommerce.db"), incremental=True)
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path
from typing import Any

from ecommerce_ingest_core import (
    DEFAULT_DB_PATH,
    ROOT_DIR,
    ProductRecord,
    discover_json_files,
    full_rebuild,
    load_product,
    normalize_attr_key,
    normalize_text,
    parse_float,
)


def connect_sqlite(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.execute("PRAGMA temp_store=MEMORY;")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS products (
            product_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            brand TEXT NOT NULL,
            brand_norm TEXT NOT NULL,
            category TEXT NOT NULL,
            sub_category TEXT NOT NULL,
            base_price REAL NOT NULL,
            image_path TEXT,
            marketing_desc TEXT,
            source_file TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS skus (
            sku_id TEXT PRIMARY KEY,
            product_id TEXT NOT NULL,
            price REAL NOT NULL,
            sku_label TEXT,
            source_file TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS sku_attributes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sku_id TEXT NOT NULL,
            product_id TEXT NOT NULL,
            attr_key_raw TEXT NOT NULL,
            attr_key_norm TEXT NOT NULL,
            attr_value_raw TEXT NOT NULL,
            attr_value_norm TEXT NOT NULL,
            FOREIGN KEY (sku_id) REFERENCES skus(sku_id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS product_faqs (
            faq_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            question TEXT NOT NULL,
            answer TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS product_reviews (
            review_id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id TEXT NOT NULL,
            nickname TEXT,
            rating INTEGER,
            content TEXT NOT NULL,
            FOREIGN KEY (product_id) REFERENCES products(product_id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS ingest_state (
            product_id TEXT PRIMARY KEY,
            source_file TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS product_search_docs (
            product_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            brand TEXT NOT NULL,
            combined_text TEXT NOT NULL,
            source_hash TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
    )

    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_category ON products(category);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_brand_norm ON products(brand_norm);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_products_base_price ON products(base_price);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_skus_product_id ON skus(product_id);")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sku_attr_key_value ON sku_attributes(attr_key_norm, attr_value_norm);"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_faq_product_id ON product_faqs(product_id);")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_reviews_product_id ON product_reviews(product_id);")


def existing_state(conn: sqlite3.Connection) -> dict[str, sqlite3.Row]:
    rows = conn.execute("SELECT product_id, source_hash, source_file FROM ingest_state").fetchall()
    return {row["product_id"]: row for row in rows}


def delete_product(conn: sqlite3.Connection, product_id: str) -> None:
    conn.execute("DELETE FROM products WHERE product_id = ?", (product_id,))
    conn.execute("DELETE FROM ingest_state WHERE product_id = ?", (product_id,))
    conn.execute("DELETE FROM product_search_docs WHERE product_id = ?", (product_id,))


def insert_product(conn: sqlite3.Connection, record: ProductRecord) -> None:
    conn.execute(
        """
        INSERT INTO products (
            product_id, title, brand, brand_norm, category, sub_category,
            base_price, image_path, marketing_desc, source_file, source_hash, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            record.product_id,
            record.title,
            record.brand,
            record.brand_norm,
            record.category,
            record.sub_category,
            record.base_price,
            record.image_path,
            record.marketing_desc,
            record.source_file,
            record.source_hash,
            record.updated_at,
        ),
    )
    conn.execute(
        "INSERT OR REPLACE INTO ingest_state (product_id, source_file, source_hash, updated_at) VALUES (?, ?, ?, ?)",
        (record.product_id, record.source_file, record.source_hash, record.updated_at),
    )


def insert_skus(conn: sqlite3.Connection, record: ProductRecord) -> None:
    for sku in record.skus:
        if not isinstance(sku, dict):
            continue
        sku_id = normalize_text(sku.get("sku_id"))
        if not sku_id:
            continue
        price = parse_float(sku.get("price"), record.base_price)
        conn.execute(
            "INSERT OR REPLACE INTO skus (sku_id, product_id, price, sku_label, source_file) VALUES (?, ?, ?, ?, ?)",
            (sku_id, record.product_id, price, normalize_text(sku.get("sku_name") or sku.get("sku_label")), record.source_file),
        )
        properties = sku.get("properties") or {}
        if isinstance(properties, dict):
            for raw_key, raw_value in properties.items():
                key_raw = normalize_text(raw_key)
                value_raw = normalize_text(raw_value)
                if not key_raw or not value_raw:
                    continue
                conn.execute(
                    """
                    INSERT INTO sku_attributes (
                        sku_id, product_id, attr_key_raw, attr_key_norm, attr_value_raw, attr_value_norm
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        sku_id,
                        record.product_id,
                        key_raw,
                        normalize_attr_key(key_raw),
                        value_raw,
                        value_raw,
                    ),
                )


def insert_faqs(conn: sqlite3.Connection, record: ProductRecord) -> None:
    for item in record.faqs:
        if not isinstance(item, dict):
            continue
        question = normalize_text(item.get("question"))
        answer = normalize_text(item.get("answer"))
        if not question or not answer:
            continue
        conn.execute(
            "INSERT INTO product_faqs (product_id, question, answer) VALUES (?, ?, ?)",
            (record.product_id, question, answer),
        )


def insert_reviews(conn: sqlite3.Connection, record: ProductRecord) -> None:
    for item in record.reviews:
        if not isinstance(item, dict):
            continue
        nickname = normalize_text(item.get("nickname"))
        content = normalize_text(item.get("content"))
        if not content:
            continue
        rating_value = item.get("rating")
        rating = int(rating_value) if isinstance(rating_value, (int, float)) else None
        conn.execute(
            "INSERT INTO product_reviews (product_id, nickname, rating, content) VALUES (?, ?, ?, ?)",
            (record.product_id, nickname, rating, content),
        )


def build_combined_text(record: ProductRecord) -> str:
    parts: list[str] = [
        record.title,
        record.brand,
        record.brand_norm,
        record.category,
        record.sub_category,
        record.marketing_desc,
    ]
    for sku in record.skus:
        if not isinstance(sku, dict):
            continue
        properties = sku.get("properties") or {}
        if isinstance(properties, dict):
            for key, value in properties.items():
                parts.append(normalize_text(key))
                parts.append(normalize_text(value))
        parts.append(normalize_text(sku.get("sku_id")))
    for item in record.faqs:
        if isinstance(item, dict):
            parts.append(normalize_text(item.get("question")))
            parts.append(normalize_text(item.get("answer")))
    for item in record.reviews:
        if isinstance(item, dict):
            parts.append(normalize_text(item.get("nickname")))
            parts.append(normalize_text(item.get("content")))
    cleaned = [part for part in parts if part]
    return "\n".join(cleaned)


def refresh_search_docs(conn: sqlite3.Connection, records: list[ProductRecord]) -> None:
    conn.execute("DELETE FROM product_search_docs")
    rows = [
        (
            record.product_id,
            record.title,
            record.brand_norm or record.brand,
            build_combined_text(record),
            record.source_hash,
            record.updated_at,
        )
        for record in records
    ]
    conn.executemany(
        """
        INSERT INTO product_search_docs (
            product_id, title, brand, combined_text, source_hash, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        rows,
    )


def refresh_fts(conn: sqlite3.Connection) -> None:
    conn.execute("DROP TABLE IF EXISTS product_search_fts")
    conn.execute(
        """
        CREATE VIRTUAL TABLE product_search_fts USING fts5(
            product_id UNINDEXED,
            title,
            brand,
            combined_text,
            tokenize = 'unicode61'
        )
        """
    )
    conn.execute(
        """
        INSERT INTO product_search_fts (product_id, title, brand, combined_text)
        SELECT product_id, title, brand, combined_text FROM product_search_docs
        """
    )


def build_sqlite_database(data_root: Path, db_path: Path, incremental: bool = True) -> None:
    """Build or refresh the SQLite database from the dataset JSON files.

    Args:
        data_root: Dataset root containing the category folders.
        db_path: Output SQLite database path.
        incremental: Skip unchanged products when source hashes match.
    """
    json_files = discover_json_files(data_root)
    if not json_files:
        raise SystemExit(f"No JSON files found under: {data_root}")

    records: list[ProductRecord] = []
    for path in json_files:
        record = load_product(path)
        if not record.product_id:
            raise SystemExit(f"Missing product_id in {path}")
        records.append(record)

    conn = connect_sqlite(db_path)
    try:
        ensure_schema(conn)
        with conn:
            current = existing_state(conn)
            seen_ids: set[str] = set()

            for record in records:
                seen_ids.add(record.product_id)
                old = current.get(record.product_id)
                if incremental and old and old["source_hash"] == record.source_hash:
                    continue
                delete_product(conn, record.product_id)
                insert_product(conn, record)
                insert_skus(conn, record)
                insert_faqs(conn, record)
                insert_reviews(conn, record)

            removed_ids = set(current.keys()) - seen_ids
            for product_id in removed_ids:
                delete_product(conn, product_id)

            refresh_search_docs(conn, records)
            refresh_fts(conn)
    finally:
        conn.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build SQLite database from ecommerce JSON files.")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=ROOT_DIR,
        help="Dataset root containing 1_美妆护肤, 2_数码电子, ...",
    )
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH, help="SQLite database path")
    parser.add_argument(
        "--full-rebuild",
        action="store_true",
        help="Drop and rebuild the SQLite database from scratch before ingesting.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint for building the SQLite database."""
    args = parse_args()
    data_root = args.data_root.resolve()
    db_path = args.db_path.resolve()

    if args.full_rebuild:
        full_rebuild(db_path=db_path)

    build_sqlite_database(data_root=data_root, db_path=db_path, incremental=not args.full_rebuild)
    print(f"SQLite database built successfully: {db_path}")


if __name__ == "__main__":
    main()
