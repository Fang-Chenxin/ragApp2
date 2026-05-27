#!/usr/bin/env python3
"""Build the Chroma semantic index for rag_knowledge fields.

How to call:
    python build_chroma_db.py --data-root . --chroma-path .chroma
    python build_chroma_db.py --data-root . --chroma-path .chroma --full-rebuild

Programmatic use:
    from build_chroma_db import build_chroma_database
    build_chroma_database(Path("."), Path(".chroma"))
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

try:
    import chromadb  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    chromadb = None

from ecommerce_ingest_core import (
    DEFAULT_CHROMA_PATH,
    ROOT_DIR,
    ProductRecord,
    discover_json_files,
    full_rebuild,
    load_product,
    normalize_text,
)


def build_chroma(records: list[ProductRecord], chroma_path: Path) -> None:
    if chromadb is None:
        print("[warn] chromadb is not installed, skip Chroma build.")
        return

    chroma_path.mkdir(parents=True, exist_ok=True)
    client = chromadb.PersistentClient(path=str(chroma_path))
    collection_name = "product_knowledge"
    try:
        client.delete_collection(collection_name)
    except Exception:
        pass
    collection = client.get_or_create_collection(collection_name)

    ids: list[str] = []
    documents: list[str] = []
    metadatas: list[dict[str, Any]] = []

    for record in records:
        if record.marketing_desc:
            ids.append(f"{record.product_id}::marketing")
            documents.append(record.marketing_desc)
            metadatas.append(
                {
                    "product_id": record.product_id,
                    "chunk_type": "marketing",
                    "title": record.title,
                    "brand": record.brand_norm or record.brand,
                    "category": record.category,
                    "sub_category": record.sub_category,
                    "source_hash": record.source_hash,
                }
            )
        for index, item in enumerate(record.faqs, start=1):
            if not isinstance(item, dict):
                continue
            question = normalize_text(item.get("question"))
            answer = normalize_text(item.get("answer"))
            if not question and not answer:
                continue
            ids.append(f"{record.product_id}::faq::{index}")
            documents.append(f"Q: {question}\nA: {answer}")
            metadatas.append(
                {
                    "product_id": record.product_id,
                    "chunk_type": "faq",
                    "title": record.title,
                    "brand": record.brand_norm or record.brand,
                    "category": record.category,
                    "sub_category": record.sub_category,
                    "source_hash": record.source_hash,
                }
            )
        for index, item in enumerate(record.reviews, start=1):
            if not isinstance(item, dict):
                continue
            content = normalize_text(item.get("content"))
            if not content:
                continue
            ids.append(f"{record.product_id}::review::{index}")
            documents.append(content)
            metadatas.append(
                {
                    "product_id": record.product_id,
                    "chunk_type": "review",
                    "title": record.title,
                    "brand": record.brand_norm or record.brand,
                    "category": record.category,
                    "sub_category": record.sub_category,
                    "source_hash": record.source_hash,
                }
            )

    if ids:
        collection.add(ids=ids, documents=documents, metadatas=metadatas)


def build_chroma_database(data_root: Path, chroma_path: Path) -> None:
    """Build or refresh the Chroma semantic index from rag_knowledge fields.

    Args:
        data_root: Dataset root containing the category folders.
        chroma_path: Chroma persistence directory.
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

    build_chroma(records, chroma_path)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Chroma index from ecommerce JSON files.")
    parser.add_argument(
        "--data-root",
        type=Path,
        default=ROOT_DIR,
        help="Dataset root containing 1_美妆护肤, 2_数码电子, ...",
    )
    parser.add_argument(
        "--chroma-path",
        type=Path,
        default=DEFAULT_CHROMA_PATH,
        help="Chroma persistence directory",
    )
    parser.add_argument(
        "--full-rebuild",
        action="store_true",
        help="Drop and rebuild the Chroma index from scratch before ingesting.",
    )
    return parser.parse_args()


def main() -> None:
    """CLI entrypoint for building the Chroma index."""
    args = parse_args()
    data_root = args.data_root.resolve()
    chroma_path = args.chroma_path.resolve()

    if args.full_rebuild:
        full_rebuild(chroma_path=chroma_path)

    build_chroma_database(data_root=data_root, chroma_path=chroma_path)
    print(f"Chroma index built successfully: {chroma_path}")


if __name__ == "__main__":
    main()
