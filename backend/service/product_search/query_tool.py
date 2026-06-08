"""SQLite 商品查询工具模块 - 负责 OpenAI 工具定义与执行分发"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .sqlite_search import sqlite_product_search_service


def get_tool_spec() -> dict[str, Any]:
    """获取 SQLite 商品查询工具的 OpenAI Function Calling 规范。"""
    category_tree = sqlite_product_search_service.get_category_tree()
    category_schema: dict[str, Any] = {"type": ["string", "null"], "description": "产品分类，必须使用商品数据库中的真实一级类目"}
    sub_category_schema: dict[str, Any] = {"type": ["string", "null"], "description": "子分类，必须使用商品数据库中的真实二级类目"}
    if category_tree:
        category_schema["enum"] = [None, *sorted(category_tree.keys())]
        sub_categories = sorted({sub for subs in category_tree.values() for sub in subs})
        sub_category_schema["enum"] = [None, *sub_categories]

    return {
        "type": "function",
        "function": {
            "name": "query_products",
            "description": "查询本地 SQLite 商品数据库，可以使用自然语言或结构化过滤器。导购场景下，优先围绕用户真实需求检索；如果直搜无结果，改用相邻场景或次相关品类继续搜索。",
            "parameters": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "text": {"type": "string", "description": "自然语言查询文本。"},
                    "keyword": {"type": ["string", "null"], "description": "关键词搜索"},
                    "brand": {"type": ["string", "null"], "description": "品牌过滤"},
                    "category": category_schema,
                    "sub_category": sub_category_schema,
                    "attr_filters": {
                        "type": "array",
                        "items": {
                            "oneOf": [
                                {
                                    "type": "object",
                                    "required": ["key", "value"],
                                    "properties": {
                                        "key": {"type": "string"},
                                        "value": {"type": "string"},
                                    },
                                    "additionalProperties": False,
                                },
                                {
                                    "type": "array",
                                    "minItems": 2,
                                    "maxItems": 2,
                                    "items": {"type": "string"},
                                },
                            ]
                        },
                        "description": "属性过滤器列表，格式为 [{key, value}] 或 [[key, value]]"
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                    "show_skus": {"type": "boolean", "default": False},
                },
            },
        },
    }


def run_tool(tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> dict[str, Any]:
    """执行 SQLite 商品查询工具调用，并返回统一 JSON 结果。"""
    if tool_name != "query_products":
        return {
            "ok": False,
            "error": f"未知工具: {tool_name}",
            "query_sql": "",
            "query_params": [],
            "resolved_filters": {},
            "total": 0,
            "items": [],
        }

    args = arguments or {}
    text = args.get("text")
    limit = int(args.get("limit", 10))
    show_skus = bool(args.get("show_skus", False))

    attr_filters = _normalize_attr_filters(args.get("attr_filters"))
    category_errors = sqlite_product_search_service.validate_category_filters(
        category=args.get("category"),
        sub_category=args.get("sub_category"),
    )
    if category_errors:
        return {
            "ok": False,
            "error": "；".join(category_errors),
            "validation_error": True,
            "query_sql": "",
            "query_params": [],
            "resolved_filters": {
                "category": args.get("category"),
                "sub_category": args.get("sub_category"),
            },
            "total": 0,
            "items": [],
        }

    structured_filters = {
        "keyword": args.get("keyword"),
        "brand": args.get("brand"),
        "category": args.get("category"),
        "sub_category": args.get("sub_category"),
        "attr_filters": attr_filters,
    }

    if text and any(structured_filters.values()):
        # LLM/SearchPlan 可能同时给出自然语言 text 和结构化约束。
        # 这种情况下保留约束，避免泛词查询跨类目召回不相关商品。
        result = sqlite_product_search_service.search_products(
            keyword=str(text),
            brand=args.get("brand"),
            category=args.get("category"),
            sub_category=args.get("sub_category"),
            attr_filters=attr_filters,
            limit=limit,
            show_skus=show_skus,
        )
        if isinstance(result, dict):
            result["input_text"] = str(text)
            result["parsed"] = {
                "keyword": str(text),
                "brand": args.get("brand"),
                "category": args.get("category"),
                "sub_category": args.get("sub_category"),
                "attr_filters": attr_filters,
                "source": "structured_text_filters",
            }
        return result

    if text:
        # 自然语言入口会自动解析品牌/品类/属性，最适合 LLM 直接传用户需求。
        result = sqlite_product_search_service.search_by_rule_parsed_text(text=str(text), limit=limit, show_skus=show_skus)
        if isinstance(result, dict) and result.get("ok") and result.get("total", 0) == 0:
            result["match_type"] = "direct_no_result"
            result["recommendation_hint"] = "未找到直接相关商品，请结合用户意图调整关键词或询问更具体的品类、品牌、预算。"
        return result

    if not any([args.get("keyword"), args.get("brand"), args.get("category"), args.get("sub_category"), attr_filters]):
        # 空参数会让模型看到明确错误，从而下一轮尝试提取关键词，而不是默默返回空结果。
        return {
            "ok": False,
            "error": "参数为空！你必须提供 text（自然语言查询）或至少一个过滤器(keyword/brand/category/sub_category/attr_filters)。请从用户问题和对话历史中提取商品关键词后重新调用。",
            "query_sql": "",
            "query_params": [],
            "resolved_filters": {},
            "total": 0,
            "items": [],
        }

    return sqlite_product_search_service.search_products(
        keyword=args.get("keyword"),
        brand=args.get("brand"),
        category=args.get("category"),
        sub_category=args.get("sub_category"),
        attr_filters=attr_filters,
        limit=limit,
        show_skus=show_skus,
    )


def _normalize_attr_filters(raw_filters: Any) -> List[Dict[str, str]]:
    """标准化属性过滤器格式，兼容 dict 和 `[key, value]` 两种工具参数。"""
    if not raw_filters:
        return []
    resolved: List[Dict[str, str]] = []
    for item in raw_filters:
        if isinstance(item, dict):
            key = str(item.get("key", "")).strip()
            value = str(item.get("value", "")).strip()
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            key = str(item[0]).strip()
            value = str(item[1]).strip()
        else:
            continue
        if key and value:
            resolved.append({"key": key, "value": value})
    return resolved
