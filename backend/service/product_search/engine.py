#!/usr/bin/env python3
"""Query existing SQLite database with ontology-based synonym mapping (no schema changes).

Usage examples:
    python query_sqlite_with_synonyms.py --db-path ecommerce.db --text "银色 1TB 的数码平板"
  python query_sqlite_with_synonyms.py \
    --db-path ecommerce.db \
    --keyword "M5 Pro 笔记本" \
    --brand "苹果"

  python query_sqlite_with_synonyms.py \
    --db-path ecommerce.db \
    --attr "机身颜色:银灰" \
    --attr "固态硬盘容量:1tb" \
    --limit 20

Agent guidance:
    - Prefer `agent_search_by_rule_parsed_text(text=...)` for natural language input.
    - Use `agent_search_products(...)` when the agent already has structured filters.
    - Keep category as a hint if present; the ontology will limit valid attribute families.
    - Use `show_skus=True` only when the user asks for detailed SKU combinations.
"""

from __future__ import annotations

import argparse
import functools
import json
import sqlite3
import traceback
import re
from pathlib import Path
from typing import Any

# 数据文件位于商品数据集目录
_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "ecommerce_agent_dataset"
DEFAULT_DB_PATH = _DATA_DIR / "ecommerce.db"
DEFAULT_ONTOLOGY_PATH = _DATA_DIR / "attribute_ontology.json"
_REVERSE_INDEX_CACHE: dict[int, tuple[dict[str, list[str]], int, dict[str, str]]] = {}


@functools.lru_cache(maxsize=8)
def load_ontology(ontology_path: Path) -> dict[str, Any]:
    """读取商品属性 ontology，并补齐缺省顶层字段。"""
    if not ontology_path.exists():
        return {"brands": {}, "families": {}, "category_scopes": {}, "category_aliases": {}}
    data = json.loads(ontology_path.read_text(encoding="utf-8"))
    return {
        "brands": data.get("brands", {}),
        "families": data.get("families", {}),
        "category_scopes": data.get("category_scopes", {}),
        "category_aliases": data.get("category_aliases", {}),
    }


def normalize(s: str) -> str:
    """把任意字符串压缩空白并去掉首尾空白。"""
    return " ".join(str(s).strip().split())


def build_reverse_index(mapping: dict[str, list[str]]) -> dict[str, str]:
    """把 `标准值 -> 同义词列表` 转成 `任意同义词 -> 标准值`。"""
    cache_key = id(mapping)
    cached = _REVERSE_INDEX_CACHE.get(cache_key)
    if cached and cached[0] is mapping and cached[1] == len(mapping):
        return cached[2]

    reverse: dict[str, str] = {}
    for canonical, variants in mapping.items():
        reverse[normalize(canonical).lower()] = canonical
        for value in variants:
            reverse[normalize(value).lower()] = canonical
    if len(_REVERSE_INDEX_CACHE) > 64:
        _REVERSE_INDEX_CACHE.clear()
    _REVERSE_INDEX_CACHE[cache_key] = (mapping, len(mapping), reverse)
    return reverse


def expand_term(term: str, mapping: dict[str, list[str]]) -> list[str]:
    """根据同义词表扩展一个品牌/属性词，并保持标准值优先。"""
    term_norm = normalize(term)
    reverse = build_reverse_index(mapping)
    canonical = reverse.get(term_norm.lower(), term_norm)
    values = [canonical]
    values.extend(mapping.get(canonical, []))
    # dedupe while preserving order
    seen: set[str] = set()
    ordered: list[str] = []
    for item in values:
        x = normalize(item)
        if not x:
            continue
        key = x.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(x)
    return ordered


def unique_preserve_order(items: list[str]) -> list[str]:
    """按首次出现顺序去重，同时过滤空字符串。"""
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        value = normalize(item)
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(value)
    return ordered


def build_family_indexes(ontology: dict[str, Any]) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
    """建立属性族 key 别名索引，并规范化属性族定义。"""
    families = ontology.get("families", {}) or {}
    alias_index: dict[str, str] = {}
    normalized_families: dict[str, dict[str, Any]] = {}
    for family_name, family_spec in families.items():
        keys = [family_name] + list(family_spec.get("keys", []))
        normalized_families[family_name] = {
            "keys": unique_preserve_order(keys),
            "value_mode": family_spec.get("value_mode", "text"),
            "values": family_spec.get("values", {}),
        }
        for key in keys:
            alias_index[normalize(key).lower()] = family_name
    return alias_index, normalized_families


def resolve_family(key: str, alias_index: dict[str, str]) -> str | None:
    """把原始属性键解析到 ontology 中的属性族名。"""
    return alias_index.get(normalize(key).lower())


def normalize_text_variants(value: str) -> list[str]:
    """为普通文本值生成大小写变体。"""
    return unique_preserve_order([value, value.lower(), value.upper()])


def parse_numeric_unit(value: str) -> tuple[float | None, str | None]:
    """解析带单位的数值文本，如 `1TB`、`500ml`、`42码`。"""
    text = normalize(value).lower().replace(" ", "")

    match = re.match(r"^(\d+(?:\.\d+)?)(tb|gb|g|t|ml|l|mm|cm|m|英寸|寸|inch|in|kg|g|盒|瓶|袋|片|支|个|只|枚|件)?(?:s?sd)?$", text)
    if not match:
        return None, None
    number = float(match.group(1))
    unit = match.group(2) or ""
    return number, unit


def expand_color_variants(value: str, ontology: dict[str, Any]) -> list[str]:
    """按颜色 ontology 扩展颜色同义词，如银灰/银色/灰色。"""
    normalized = normalize(value)
    lower = normalized.lower()
    color_family = ontology.get("families", {}).get("color", {})
    color_aliases = color_family.get("values", {}) or {}
    for canonical, variants in color_aliases.items():
        all_terms = [canonical] + list(variants)
        if lower in {normalize(term).lower() for term in all_terms}:
            return unique_preserve_order(all_terms)
    return unique_preserve_order([normalized])


def expand_numeric_family_variants(value: str, family: str) -> list[str]:
    """按属性族扩展数值单位变体，例如 1TB -> 1024GB/1TB SSD。"""
    number, unit = parse_numeric_unit(value)
    if number is None:
        return unique_preserve_order([normalize(value)])

    if family == "storage":
        gb_value = None
        if unit in {"tb", "t"}:
            gb_value = int(round(number * 1024))
        elif unit in {"gb", "g", ""}:
            gb_value = int(round(number))
        if gb_value is not None:
            tb_value = gb_value / 1024
            variants = [
                f"{gb_value}GB",
                f"{gb_value}GB SSD",
                f"{tb_value:g}TB",
                f"{tb_value:g}TB SSD",
                f"{int(tb_value) if tb_value.is_integer() else tb_value:g}T",
            ]
            return unique_preserve_order(variants)

    if family == "capacity":
        unit_text = unit.upper() if unit else ""
        variants = [
            f"{number:g}{unit_text}" if unit_text else f"{number:g}",
            f"{number:g}{unit_text.lower()}" if unit_text else f"{number:g}",
        ]
        return unique_preserve_order(variants)

    if family == "dimension":
        variants = [
            f"{number:g}英寸",
            f"{number:g}寸",
            f"{number:g}in",
            f"{number:g} inch",
        ]
        return unique_preserve_order(variants)

    if family == "quantity":
        unit_text = unit or ""
        variants = [
            f"{int(number) if number.is_integer() else number:g}{unit_text}",
            f"{int(number) if number.is_integer() else number:g}{unit_text}装" if unit_text else f"{int(number) if number.is_integer() else number:g}件",
            f"{int(number) if number.is_integer() else number:g}",
        ]
        return unique_preserve_order(variants)

    if family == "size":
        variants = [f"{number:g}码", f"{number:g}", f"{number:g}号"]
        return unique_preserve_order(variants)

    return unique_preserve_order([normalize(value)])


def expand_family_value_variants(family: str | None, value: str, ontology: dict[str, Any]) -> list[str]:
    """根据属性族类型扩展属性值候选。"""
    if family is None:
        return unique_preserve_order([normalize(value)])
    family_spec = ontology.get("families", {}).get(family, {})
    mode = family_spec.get("value_mode", "text")
    if mode == "color":
        return expand_color_variants(value, ontology)
    if mode in {"storage", "capacity", "dimension", "quantity", "size"}:
        return expand_numeric_family_variants(value, family)
    values = family_spec.get("values", {}) or {}
    reverse = build_reverse_index(values)
    canonical = reverse.get(normalize(value).lower(), normalize(value))
    variants = [canonical] + list(values.get(canonical, []))
    return unique_preserve_order(variants)


def allowed_families_for_category(category: str | None, ontology: dict[str, Any]) -> set[str] | None:
    """返回某个品类允许使用的属性族集合；无品类时不过滤。"""
    if not category:
        return None
    scopes = ontology.get("category_scopes", {}) or {}
    families = scopes.get(category)
    if not families:
        return None
    return set(families)


def build_keyword_terms(text: str) -> list[str]:
    """构造 FTS/LIKE 关键词列表，中文长词会额外拆成二字片段。"""
    cleaned = normalize(text)
    if not cleaned:
        return []

    # Split on common delimiters first
    parts = re.split(r"[\s,，.。;；:：/\\|\-]+", cleaned)
    parts = [p for p in parts if p]
    for part in list(parts):
        mixed_parts = re.findall(r"[A-Za-z][A-Za-z0-9+]*|[\u4e00-\u9fff]+|\d+(?:\.\d+)?", part)
        parts.extend(mixed_parts)

    # For Chinese-only parts with no delimiters, also split into 2-char bigrams
    # so FTS/LIKE can match partial substrings (e.g. "美白润肤" → "美白","润肤")
    sub_parts: list[str] = []
    for part in parts:
        if re.fullmatch(r"[\u4e00-\u9fff]+", part) and len(part) > 2:
            for i in range(0, len(part) - 1):
                sub_parts.append(part[i : i + 2])

    terms = [cleaned]
    terms.extend(parts)
    terms.extend(sub_parts)
    expansion_map = {
        "ipad": ["iPad", "平板", "Apple"],
        "平板": ["平板电脑", "Pad", "iPad"],
        "游戏本": ["笔记本", "笔记本电脑", "电脑"],
        "口红": ["唇釉", "唇膏", "唇部", "彩妆"],
        "连衣裙": ["裙", "女装", "女士", "瑜伽裤", "裤", "服饰", "服装"],
    }
    cleaned_lower = cleaned.lower()
    for key, values in expansion_map.items():
        if key.lower() in cleaned_lower:
            terms.extend(values)
    return unique_preserve_order(terms)


def strip_intent_words(text: str) -> str:
    """移除导购问句里的意图/语气词，避免它们污染商品关键词。"""
    cleaned = normalize(text)
    if not cleaned:
        return ""

    stop_words = (
        "有没有", "有没", "有没有的", "有吗", "有么", "有没有卖", "有没有推荐",
        "想买", "想要", "我要", "需要", "推荐", "帮我", "帮忙", "看看", "查查", "找找",
        "哪款", "哪种", "什么", "适合", "好吃", "好喝", "好用", "不错", "划算",
        "便宜", "贵吗", "多少钱", "多少价位", "学习用", "学习", "打游戏", "的",
    )
    for word in stop_words:
        cleaned = cleaned.replace(word, " ")
    return normalize(cleaned)


def score_keyword_match(item: dict[str, Any], keyword: str | None) -> int:
    """给商品按关键词相关性打分，用于 OR 召回后的稳定排序。"""
    cleaned = strip_intent_words(keyword or "")
    if not cleaned:
        return 0

    title = normalize(str(item.get("title") or ""))
    brand = normalize(str(item.get("brand") or ""))
    category = normalize(str(item.get("category") or ""))
    sub_category = normalize(str(item.get("sub_category") or ""))
    searchable = normalize(" ".join([title, brand, category, sub_category]))

    score = 0
    if cleaned and cleaned in title:
        score += 100
    elif cleaned and cleaned in searchable:
        score += 60

    terms = [term for term in build_keyword_terms(cleaned) if len(term) >= 2]
    for term in terms:
        if term in title:
            score += 20
        elif term in sub_category or term in category:
            score += 12
        elif term in searchable:
            score += 4

    return score


def build_category_reverse_index(ontology: dict[str, Any]) -> dict[str, str]:
    """建立品类及其别名到标准品类名的反向索引。"""
    reverse: dict[str, str] = {}
    category_aliases = ontology.get("category_aliases", {}) or {}
    for canonical, aliases in category_aliases.items():
        reverse[normalize(canonical).lower()] = canonical
        for alias in aliases:
            reverse[normalize(alias).lower()] = canonical
    for canonical in ontology.get("category_scopes", {}) or {}:
        reverse[normalize(canonical).lower()] = canonical
    return reverse


def detect_category_from_text(text: str, ontology: dict[str, Any]) -> str | None:
    """从自然语言文本中识别最具体的商品品类。"""
    reverse = build_category_reverse_index(ontology)
    text_norm = normalize(text).lower()
    candidates: list[tuple[int, str]] = []
    for token, canonical in reverse.items():
        if token and token in text_norm:
            candidates.append((len(token), canonical))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def detect_brand_from_text(text: str, ontology: dict[str, Any]) -> str | None:
    """从自然语言文本中识别品牌标准名。"""
    brands = ontology.get("brands", {}) or {}
    reverse = build_reverse_index(brands)
    text_norm = normalize(text).lower()
    candidates: list[tuple[int, str]] = []
    for token, canonical in reverse.items():
        if token and token in text_norm:
            candidates.append((len(token), canonical))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def detect_family_value_from_text(family: str, text: str, ontology: dict[str, Any]) -> str | None:
    """从自然语言文本中识别某个属性族的值。"""
    family_spec = ontology.get("families", {}).get(family, {}) or {}
    value_mode = family_spec.get("value_mode", "text")
    text_norm = normalize(text)
    text_lower = text_norm.lower()

    if value_mode == "color":
        values = family_spec.get("values", {}) or {}
        for canonical, aliases in values.items():
            candidates = [canonical] + list(aliases)
            if any(normalize(candidate).lower() in text_lower for candidate in candidates):
                return canonical
        return None

    if value_mode in {"storage", "capacity", "dimension", "quantity", "size"}:
        patterns = {
            "storage": r"(\d+(?:\.\d+)?\s*(?:tb|gb|g|t)(?:\s*ssd)?)",
            "capacity": r"(\d+(?:\.\d+)?\s*(?:ml|l|kg|克|斤|升))",
            "dimension": r"(\d+(?:\.\d+)?\s*(?:英寸|寸|inch|in))",
            "quantity": r"(\d+(?:\.\d+)?\s*(?:盒|瓶|袋|片|支|个|件|装))",
            "size": r"(\d+(?:\.\d+)?\s*(?:号|码))",
        }
        pattern = patterns.get(value_mode)
        if pattern:
            match = re.search(pattern, text_lower)
            if match:
                return normalize(match.group(1))
        return None

    values = family_spec.get("values", {}) or {}
    reverse = build_reverse_index(values)
    for token, canonical in reverse.items():
        if token and token in text_lower:
            return canonical
    return None


def extract_query_from_text(text: str, ontology: dict[str, Any]) -> dict[str, Any]:
    """把自然语言查询解析成结构化过滤器。"""
    category = detect_category_from_text(text, ontology)
    brand = detect_brand_from_text(text, ontology)
    category_scopes = ontology.get("category_scopes", {}) or {}
    allowed_families = set(category_scopes.get(category, [])) if category else None
    families = ontology.get("families", {}) or {}

    attr_filters: list[dict[str, str]] = []
    for family_name, family_spec in families.items():
        if allowed_families is not None and family_name not in allowed_families:
            continue
        value = detect_family_value_from_text(family_name, text, ontology)
        if value is None:
            continue
        key = family_spec.get("keys", [family_name])[0]
        attr_filters.append({"key": key, "value": value})

    # Fallback keyword: 移除已识别出的品牌/品类/属性词和导购意图词，剩余文本作为全文检索关键词。
    keyword = strip_intent_words(text)
    for token in [category or "", brand or ""]:
        if token:
            keyword = keyword.replace(token, " ")
    category_aliases = ontology.get("category_aliases", {}) or {}
    if category:
        for alias in [category] + list(category_aliases.get(category, [])):
            keyword = keyword.replace(alias, " ")
    for item in attr_filters:
        keyword = keyword.replace(item["key"], " ")
        keyword = keyword.replace(item["value"], " ")
    keyword = normalize(keyword)

    return {
        "keyword": keyword or None,
        "brand": brand,
        "category": category,
        "sub_category": None,
        "attr_filters": attr_filters,
    }


def parse_attr_filters(raw_filters: list[str]) -> list[tuple[str, str]]:
    """解析 CLI `--attr key:value` 参数。"""
    pairs: list[tuple[str, str]] = []
    for raw in raw_filters:
        if ":" not in raw:
            raise ValueError(f"Invalid --attr format: {raw}. Expected key:value")
        key, value = raw.split(":", 1)
        k, v = normalize(key), normalize(value)
        if not k or not v:
            raise ValueError(f"Invalid --attr format: {raw}. Empty key/value")
        pairs.append((k, v))
    return pairs


def build_query(
    keyword: str | None,
    brand: str | None,
    category: str | None,
    sub_category: str | None,
    attr_filters: list[tuple[str, str]],
    ontology: dict[str, Any],
    family_alias_index: dict[str, str],
    limit: int,
    keyword_mode: str = "fts",
) -> tuple[str, list[Any]]:
    """根据结构化过滤器生成商品查询 SQL 和参数。"""
    sql = [
        "SELECT p.product_id, p.title, p.brand, p.brand_norm, p.category, p.sub_category, p.base_price",
        "FROM products p",
    ]
    params: list[Any] = []
    where: list[str] = []
    allowed_families = allowed_families_for_category(category, ontology)

    if keyword:
        # 关键词优先使用 FTS；上层在无结果时会用 LIKE 再查一次。
        keyword_terms = build_keyword_terms(keyword)
        if keyword_mode == "like":
            like_clauses = []
            for term in keyword_terms:
                pattern = f"%{term}%"
                like_clauses.extend([
                    "d.title LIKE ?",
                    "d.brand LIKE ?",
                    "d.combined_text LIKE ?",
                ])
                params.extend([pattern, pattern, pattern])
            where.append(
                "EXISTS (SELECT 1 FROM product_search_docs d WHERE d.product_id = p.product_id AND ("
                + " OR ".join(like_clauses)
                + "))"
            )
        else:
            match_expr = " OR ".join(
                [f'"{t.replace(chr(34), chr(34)+chr(34))}"' for t in keyword_terms]
            )
            where.append(
                "EXISTS (SELECT 1 FROM product_search_fts f WHERE f.product_id = p.product_id AND f.combined_text MATCH ?)"
            )
            params.append(match_expr)

    if brand:
        brands = expand_term(brand, ontology.get("brands", {}))
        placeholders = ",".join(["?"] * len(brands))
        where.append(f"(p.brand IN ({placeholders}) OR p.brand_norm IN ({placeholders}))")
        params.extend(brands)
        params.extend(brands)

    if category:
        where.append("p.category = ?")
        params.append(category)

    if sub_category:
        where.append("p.sub_category = ?")
        params.append(sub_category)

    if attr_filters:
        sku_level_clauses: list[str] = []
        for key, value in attr_filters:
            # 属性过滤落在 SKU 级别，同一商品只要存在满足所有属性的 SKU 即命中。
            family = resolve_family(key, family_alias_index)
            if allowed_families is not None and family is not None and family not in allowed_families:
                # Skip families outside the category scope rather than mixing them in.
                continue

            if family is not None:
                key_terms = ontology.get("families", {}).get(family, {}).get("keys", [key])
                value_terms = expand_family_value_variants(family, value, ontology)
            else:
                key_terms = [key]
                value_terms = [value]

            key_placeholders = ",".join(["?"] * len(key_terms))
            value_placeholders = ",".join(["?"] * len(value_terms))

            sku_level_clauses.append(
                """
                EXISTS (
                  SELECT 1 FROM sku_attributes sa
                  WHERE sa.product_id = s.product_id
                    AND sa.sku_id = s.sku_id
                    AND (sa.attr_key_raw IN ({kph}) OR sa.attr_key_norm IN ({kph}))
                    AND (sa.attr_value_raw IN ({vph}) OR sa.attr_value_norm IN ({vph}))
                )
                """.replace("{kph}", key_placeholders).replace("{vph}", value_placeholders)
            )
            params.extend(key_terms)
            params.extend(key_terms)
            params.extend(value_terms)
            params.extend(value_terms)

        where.append(
            "EXISTS (SELECT 1 FROM skus s WHERE s.product_id = p.product_id AND "
            + " AND ".join(f"({c.strip()})" for c in sku_level_clauses)
            + ")"
        )

    if where:
        sql.append("WHERE " + " AND ".join(f"({c.strip()})" for c in where))

    sql.append("ORDER BY p.base_price ASC")
    sql.append("LIMIT ?")
    params.append(limit)

    return "\n".join(sql), params


def fetch_skus(conn: sqlite3.Connection, product_id: str) -> list[dict[str, Any]]:
    """读取某个商品的 SKU 价格和属性明细。"""
    sku_rows = conn.execute(
        "SELECT sku_id, price FROM skus WHERE product_id = ? ORDER BY sku_id", (product_id,)
    ).fetchall()
    results: list[dict[str, Any]] = []
    for sku_id, price in sku_rows:
        attrs = conn.execute(
            """
            SELECT attr_key_norm, attr_value_norm
            FROM sku_attributes
            WHERE product_id = ? AND sku_id = ?
            ORDER BY id
            """,
            (product_id, sku_id),
        ).fetchall()
        results.append(
            {
                "sku_id": sku_id,
                "price": price,
                "attributes": [{"key": k, "value": v} for k, v in attrs],
            }
        )
    return results


def agent_search_products(
    *,
    db_path: str | Path = DEFAULT_DB_PATH,
    ontology_path: str | Path = DEFAULT_ONTOLOGY_PATH,
    keyword: str | None = None,
    brand: str | None = None,
    category: str | None = None,
    sub_category: str | None = None,
    attr_filters: list[dict[str, str]] | list[tuple[str, str]] | None = None,
    limit: int = 10,
    show_skus: bool = False,
) -> dict[str, Any]:
    """Agent-friendly query interface.

    This function keeps the DB unchanged and applies synonym expansion during
    query construction. It returns a stable JSON-serializable object that is
    easy for agents to consume.

    Returns:
        {
          "ok": bool,
          "error": str | None,
          "query_sql": str,
          "query_params": list,
          "resolved_filters": { ... },
          "total": int,
          "items": list
        }
    """
    resolved_attr_filters: list[tuple[str, str]] = []
    input_attr_filters = attr_filters or []

    # 统一兼容 tuple 和 dict 两种属性过滤器格式。
    for item in input_attr_filters:
        if isinstance(item, tuple) and len(item) == 2:
            key, value = normalize(item[0]), normalize(item[1])
            if key and value:
                resolved_attr_filters.append((key, value))
            continue
        if isinstance(item, dict):
            key = normalize(item.get("key", ""))
            value = normalize(item.get("value", ""))
            if key and value:
                resolved_attr_filters.append((key, value))
            continue
        return {
            "ok": False,
            "error": f"Invalid attr_filters item: {item}",
            "query_sql": "",
            "query_params": [],
            "resolved_filters": {},
            "total": 0,
            "items": [],
        }

    db_path_obj = Path(db_path)
    ontology_path_obj = Path(ontology_path)

    if not db_path_obj.exists():
        return {
            "ok": False,
            "error": f"Database not found: {db_path_obj}",
            "query_sql": "",
            "query_params": [],
            "resolved_filters": {},
            "total": 0,
            "items": [],
        }

    try:
        ontology = load_ontology(ontology_path_obj)
        family_alias_index, _ = build_family_indexes(ontology)
        sql, params = build_query(
            keyword=normalize(keyword) if keyword else None,
            brand=normalize(brand) if brand else None,
            category=normalize(category) if category else None,
            sub_category=normalize(sub_category) if sub_category else None,
            attr_filters=resolved_attr_filters,
            ontology=ontology,
            family_alias_index=family_alias_index,
            limit=max(1, int(limit)),
        )

        conn = sqlite3.connect(db_path_obj)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(sql, params).fetchall()
            if not rows and keyword:
                # FTS 对分词敏感，空结果时用 LIKE 做一次宽松回退。
                fallback_sql, fallback_params = build_query(
                    keyword=normalize(keyword),
                    brand=normalize(brand) if brand else None,
                    category=normalize(category) if category else None,
                    sub_category=normalize(sub_category) if sub_category else None,
                    attr_filters=resolved_attr_filters,
                    ontology=ontology,
                    family_alias_index=family_alias_index,
                    limit=max(1, int(limit)),
                    keyword_mode="like",
                )
                rows = conn.execute(fallback_sql, fallback_params).fetchall()
            items: list[dict[str, Any]] = []
            for row in rows:
                obj: dict[str, Any] = dict(row)
                if show_skus:
                    obj["skus"] = fetch_skus(conn, row["product_id"])
                items.append(obj)
            if keyword:
                items.sort(key=lambda obj: (-score_keyword_match(obj, keyword), obj.get("base_price") or 0))
        finally:
            conn.close()

        return {
            "ok": True,
            "error": None,
            "query_sql": sql,
            "query_params": params,
            "resolved_filters": {
                "keyword": keyword,
                "brand": brand,
                "category": category,
                "sub_category": sub_category,
                "attr_filters": [{"key": k, "value": v} for k, v in resolved_attr_filters],
                "ontology_path": str(ontology_path_obj),
                "limit": max(1, int(limit)),
                "show_skus": bool(show_skus),
            },
            "total": len(items),
            "items": items,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc(),
            "query_sql": "",
            "query_params": [],
            "resolved_filters": {
                "keyword": keyword,
                "brand": brand,
                "category": category,
                "sub_category": sub_category,
                "attr_filters": [{"key": k, "value": v} for k, v in resolved_attr_filters],
                "limit": max(1, int(limit)),
                "show_skus": bool(show_skus),
            },
            "total": 0,
            "items": [],
        }


def agent_search_by_rule_parsed_text(
    *,
    text: str,
    db_path: str | Path = DEFAULT_DB_PATH,
    ontology_path: str | Path = DEFAULT_ONTOLOGY_PATH,
    limit: int = 10,
    show_skus: bool = False,
) -> dict[str, Any]:
    """Agent entrypoint for natural language queries.

    It extracts category, brand, attribute family, and keyword hints from the
    input text, then delegates to `agent_search_products`.
    """
    db_path_obj = Path(db_path)
    ontology_path_obj = Path(ontology_path)
    if not db_path_obj.exists():
        return {
            "ok": False,
            "error": f"Database not found: {db_path_obj}",
            "parsed": {},
            "query_sql": "",
            "query_params": [],
            "resolved_filters": {},
            "total": 0,
            "items": [],
        }

    ontology = load_ontology(ontology_path_obj)
    # 先用规则和 ontology 抽取结构化字段，再复用结构化查询入口。
    parsed = extract_query_from_text(text, ontology)
    result = agent_search_products(
        db_path=db_path_obj,
        ontology_path=ontology_path_obj,
        keyword=parsed.get("keyword"),
        brand=parsed.get("brand"),
        category=parsed.get("category"),
        sub_category=parsed.get("sub_category"),
        attr_filters=parsed.get("attr_filters"),
        limit=limit,
        show_skus=show_skus,
    )
    result["parsed"] = parsed
    result["input_text"] = text
    return result



def main() -> None:
    """命令行调试入口，便于直接验证 SQLite 搜索和 ontology 解析。"""
    parser = argparse.ArgumentParser(description="Query SQLite with synonym mapping without schema changes.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--ontology-path", type=Path, default=DEFAULT_ONTOLOGY_PATH)
    parser.add_argument("--text", type=str, default=None, help="Natural language query text")
    parser.add_argument("--keyword", type=str, default=None, help="Full-text keyword")
    parser.add_argument("--brand", type=str, default=None)
    parser.add_argument("--category", type=str, default=None)
    parser.add_argument("--sub-category", type=str, default=None)
    parser.add_argument("--attr", action="append", default=[], help="Attribute filter in key:value format")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--show-skus", action="store_true", help="Include SKU attribute details")
    args = parser.parse_args()

    if args.text:
        output = agent_search_by_rule_parsed_text(
            text=args.text,
            db_path=args.db_path,
            ontology_path=args.ontology_path,
            limit=args.limit,
            show_skus=args.show_skus,
        )
    else:
        parsed_attrs = parse_attr_filters(args.attr)
        output = agent_search_products(
            db_path=args.db_path,
            ontology_path=args.ontology_path,
            keyword=args.keyword,
            brand=args.brand,
            category=args.category,
            sub_category=args.sub_category,
            attr_filters=parsed_attrs,
            limit=args.limit,
            show_skus=args.show_skus,
        )
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
