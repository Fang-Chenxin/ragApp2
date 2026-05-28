"""SQLite 商品查询工具模块 - 负责 OpenAI 工具定义与执行分发"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from .sqlite_product_search_service import sqlite_product_search_service


def get_tool_spec() -> dict[str, Any]:
    """获取 SQLite 商品查询工具的 OpenAI 规范"""
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
                    "category": {"type": ["string", "null"], "description": "产品分类"},
                    "sub_category": {"type": ["string", "null"], "description": "子分类"},
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
    """执行 SQLite 商品查询工具调用"""
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

    if text:
        result = sqlite_product_search_service.search_by_rule_parsed_text(text=str(text), limit=limit, show_skus=show_skus)
        if isinstance(result, dict) and result.get("ok") and result.get("total", 0) == 0:
            fallback_text = _build_secondary_recommendation_text(str(text))
            if fallback_text and fallback_text != str(text):
                fallback_result = sqlite_product_search_service.search_by_rule_parsed_text(
                    text=fallback_text,
                    limit=limit,
                    show_skus=show_skus,
                )
                if isinstance(fallback_result, dict) and fallback_result.get("ok") and fallback_result.get("total", 0) > 0:
                    fallback_result["primary_query_text"] = str(text)
                    fallback_result["fallback_query_text"] = fallback_text
                    fallback_result["match_type"] = "secondary_recommendation"
                    fallback_result["message"] = "未找到直接相关商品，已切换为次相关商品推荐。"
                    return fallback_result

            result["match_type"] = "direct_no_result"
            result["recommendation_hint"] = "未找到直接相关商品，建议转向数码电子等次相关品类进行推荐。"
        return result

    attr_filters = _normalize_attr_filters(args.get("attr_filters"))
    if not any([args.get("keyword"), args.get("brand"), args.get("category"), args.get("sub_category"), attr_filters]):
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
    """标准化属性过滤器格式"""
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


def _build_secondary_recommendation_text(text: str) -> Optional[str]:
    normalized = str(text).strip()
    if not normalized:
        return None

    guide_keywords = (
        "游戏",
        "高手",
        "对战",
        "上分",
        "练习",
        "流畅",
        "运行",
        "电竞",
        "手感",
        "帧率",
        "性能",
        "续航",
        "洛克王国",
        "王者",
        "吃鸡",
    )
    if not any(keyword in normalized for keyword in guide_keywords):
        return None

    scene = _extract_secondary_scene(normalized)
    if not scene:
        return None

    return f"适合玩{scene}的游戏电子产品"


def _extract_secondary_scene(text: str) -> Optional[str]:
    if "洛克王国" in text:
        return "洛克王国"

    import re

    patterns = [
        r"成为([^，。！？\s]{2,20}?高手)",
        r"玩([^，。！？\s]{2,20})",
        r"适合([^，。！？\s]{2,20})",
        r"提升([^，。！？\s]{2,20})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            scene = match.group(1).strip()
            if scene:
                return scene

    if len(text) <= 12:
        return text

    return text[:12].strip()
