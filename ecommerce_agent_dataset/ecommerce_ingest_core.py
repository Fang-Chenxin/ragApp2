#!/usr/bin/env python3
"""Shared ingest core for ecommerce JSON datasets.

This module holds the common normalization, loading, SQLite schema, and Chroma
indexing logic used by the dedicated build_sqlite_db.py and build_chroma_db.py
entrypoints.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import chromadb  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    chromadb = None


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = ROOT_DIR / "ecommerce.db"
DEFAULT_CHROMA_PATH = ROOT_DIR / ".chroma"

ATTR_KEY_NORMALIZATION = {
    "机身颜色": "颜色",
    "色号": "颜色",
    "颜色": "颜色",
    "款式": "款型",
    "款型": "款型",
    "产品规格": "规格",
    "规格": "规格",
    "包装类型": "包装",
    "包装": "包装",
    "每箱数量": "数量",
    "整箱数量": "数量",
    "数量": "数量",
    "净含量": "容量",
    "容量": "容量",
    "内存容量": "存储",
    "运行内存": "存储",
    "固态硬盘容量": "存储",
    "存储": "存储",
    "芯片": "芯片",
    "芯片型号": "芯片",
    "鞋楦": "鞋楦",
    "鞋楦类型": "鞋楦",
    "适用性别": "适用性别",
    "屏幕尺寸": "屏幕尺寸",
    "网络版本": "网络版本",
    "版本": "版本",
    "口味": "口味",
}

BRAND_NORMALIZATION = {
    "Apple 苹果": "Apple",
    "华为": "华为",
    "The Ordinary": "The Ordinary",
    "露露乐蒙": "Lululemon",
    "迈乐": "Merrell",
    "农夫山泉": "农夫山泉",
    "元气森林": "元气森林",
    "日清": "日清",
}


@dataclass(slots=True)
class ProductRecord:
    product_id: str
    title: str
    brand: str
    brand_norm: str
    category: str
    sub_category: str
    base_price: float
    image_path: str
    marketing_desc: str
    source_file: str
    source_hash: str
    updated_at: str
    skus: list[dict[str, Any]]
    faqs: list[dict[str, Any]]
    reviews: list[dict[str, Any]]


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_brand(value: Any) -> str:
    brand = normalize_text(value)
    if not brand:
        return ""
    if brand in BRAND_NORMALIZATION:
        return BRAND_NORMALIZATION[brand]
    if " " in brand:
        first_token = brand.split(" ", 1)[0].strip()
        if first_token:
            return first_token
    return brand


def normalize_attr_key(value: Any) -> str:
    key = normalize_text(value)
    return ATTR_KEY_NORMALIZATION.get(key, key)


def parse_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def discover_json_files(data_root: Path) -> list[Path]:
    files = [path for path in data_root.rglob("*.json") if "data" in path.parts]
    return sorted(files)


def data_root_from_path(path: Path) -> Path:
    current = path.resolve()
    while current.parent != current:
        if current.name in {"1_美妆护肤", "2_数码电子", "3_服饰运动", "4_食品生活"}:
            return current.parent
        current = current.parent
    return path.parent


def load_product(path: Path) -> ProductRecord:
    raw = json.loads(path.read_text(encoding="utf-8"))
    source_hash = sha256_file(path)
    updated_at = now_iso()

    skus = raw.get("skus") or []
    rag = raw.get("rag_knowledge") or {}
    faqs = rag.get("official_faq") or []
    reviews = rag.get("user_reviews") or []
    marketing_desc = normalize_text(rag.get("marketing_description"))

    return ProductRecord(
        product_id=normalize_text(raw.get("product_id")),
        title=normalize_text(raw.get("title")),
        brand=normalize_text(raw.get("brand")),
        brand_norm=normalize_brand(raw.get("brand")),
        category=normalize_text(raw.get("category")),
        sub_category=normalize_text(raw.get("sub_category")),
        base_price=parse_float(raw.get("base_price")),
        image_path=normalize_text(raw.get("image_path")),
        marketing_desc=marketing_desc,
        source_file=str(path.relative_to(data_root_from_path(path))),
        source_hash=source_hash,
        updated_at=updated_at,
        skus=skus if isinstance(skus, list) else [],
        faqs=faqs if isinstance(faqs, list) else [],
        reviews=reviews if isinstance(reviews, list) else [],
    )


def full_rebuild(db_path: Path | None = None, chroma_path: Path | None = None) -> None:
    if db_path is not None and db_path.exists():
        db_path.unlink()
    if chroma_path is not None:
        shutil.rmtree(chroma_path, ignore_errors=True)
