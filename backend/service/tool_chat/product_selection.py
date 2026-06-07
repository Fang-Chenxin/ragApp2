"""工具聊天目标商品选择与最终回复辅助。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ..product_search.query_tool import run_tool
from ..product_search.sqlite_search import sqlite_product_search_service
from config.logging_config import get_logger

logger = get_logger("service.tool_chat")


class ToolChatProductSelectionMixin:
    """目标商品提取、合并、校验和对外回复清洗能力。"""

    @staticmethod
    def _extract_selected_products(
        tool_results: Dict[str, Dict[str, Any]],
        tool_call_order: List[str],
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        for tool_call_id in tool_call_order:
            outcome = tool_results.get(tool_call_id) or {}
            result = outcome.get("result") or {}
            if not isinstance(result, dict):
                continue

            items = result.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                product_id = str(item.get("product_id") or item.get("id") or "").strip()
                if not product_id or product_id in seen_ids:
                    continue
                seen_ids.add(product_id)
                selected.append(
                    {
                        "product_id": product_id,
                        "title": str(item.get("title") or item.get("name") or ""),
                        "brand": str(item.get("brand") or ""),
                        "category": str(item.get("category") or ""),
                        "sub_category": str(item.get("sub_category") or ""),
                        "base_price": item.get("base_price") or item.get("price"),
                    }
                )
                if len(selected) >= limit:
                    return selected

        return selected

    @staticmethod
    def _extract_products_from_tool_result(result: Dict[str, Any], limit: int = 5) -> List[Dict[str, Any]]:
        selected: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        items = result.get("items") if isinstance(result, dict) else []
        if not isinstance(items, list):
            return selected

        for item in items:
            if not isinstance(item, dict):
                continue
            product_id = str(item.get("product_id") or item.get("id") or "").strip()
            if not product_id or product_id in seen_ids:
                continue
            seen_ids.add(product_id)
            selected.append(
                {
                    "product_id": product_id,
                    "title": str(item.get("title") or item.get("name") or ""),
                    "brand": str(item.get("brand") or ""),
                    "category": str(item.get("category") or ""),
                    "sub_category": str(item.get("sub_category") or ""),
                    "base_price": item.get("base_price") or item.get("price"),
                }
            )
            if len(selected) >= limit:
                break

        return selected

    @staticmethod
    def _merge_selected_products(*groups: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
        merged: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        for group in groups:
            for item in group:
                product_id = str(item.get("product_id") or "").strip()
                if not product_id or product_id in seen_ids:
                    continue
                seen_ids.add(product_id)
                merged.append(item)
                if len(merged) >= limit:
                    return merged

        return merged

    @staticmethod
    def _build_target_products(
        direct_products: List[Dict[str, Any]],
        tool_products: List[Dict[str, Any]],
        user_query: str = "",
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        candidates: List[Dict[str, Any]] = []
        candidate_meta: Dict[str, Dict[str, Any]] = {}

        for source, group in [("tool_query", tool_products), ("direct_query", direct_products)]:
            for item in group:
                product_id = str(item.get("product_id") or "").strip()
                if not product_id or product_id in candidate_meta:
                    continue
                candidate_meta[product_id] = {
                    "source": source,
                    "recommendation_role": "primary" if not candidates else "supporting",
                }
                candidates.append(item)
                if len(candidates) >= limit:
                    break
            if len(candidates) >= limit:
                break

        product_ids = [str(item.get("product_id") or "").strip() for item in candidates if item.get("product_id")]
        verification = sqlite_product_search_service.get_products_by_ids(product_ids)
        if not verification.get("ok"):
            logger.warning("目标商品数据库校验失败: %s", verification.get("error"))
            return []

        targets: List[Dict[str, Any]] = []
        for db_item in verification.get("items") or []:
            product_id = str(db_item.get("product_id") or "").strip()
            if not product_id:
                continue
            meta = candidate_meta.get(product_id) or {}
            target = {
                "rank": len(targets) + 1,
                "product_id": product_id,
                "title": str(db_item.get("title") or ""),
                "brand": str(db_item.get("brand") or ""),
                "category": str(db_item.get("category") or ""),
                "sub_category": str(db_item.get("sub_category") or ""),
                "base_price": db_item.get("base_price"),
                "image_path": db_item.get("image_path"),
                "marketing_desc": str(db_item.get("marketing_desc") or "")[:500],
                "source": str(meta.get("source") or "database"),
                "recommendation_role": str(meta.get("recommendation_role") or ("primary" if not targets else "supporting")),
            }
            if not ToolChatProductSelectionMixin._matches_user_product_constraints(user_query, target):
                logger.info(
                    "[TargetProductsFilter] query=%s | filtered product_id=%s title=%s category=%s/%s",
                    user_query,
                    product_id,
                    target["title"],
                    target["category"],
                    target["sub_category"],
                )
                continue
            target["rank"] = len(targets) + 1
            targets.append(target)

        return targets

    @staticmethod
    def _matches_user_product_constraints(user_query: str, product: Dict[str, Any]) -> bool:
        """按用户显式品类约束过滤目标商品，避免泛检索结果混入最终推荐。"""
        query = user_query or ""
        title = str(product.get("title") or "")
        category = str(product.get("category") or "")
        sub_category = str(product.get("sub_category") or "")
        searchable = f"{title} {category} {sub_category}"

        clothing_terms = (
            "服装", "衣服", "衣物", "服饰", "穿搭", "上衣", "T恤", "短袖", "裤", "裙", "卫衣",
            "运动", "训练", "健身", "力量训练", "速干", "透气",
        )
        explicit_clothing = any(term in query for term in clothing_terms)
        explicitly_accessory_or_shoe = any(term in query for term in ("帽", "鞋", "包", "背包", "腰包"))
        if explicit_clothing:
            if category != "服饰运动":
                return False
            if not explicitly_accessory_or_shoe and any(term in searchable for term in ("帽", "跑步鞋", "徒步鞋", "鞋")):
                return False
            garment_terms = ("T恤", "短袖", "上衣", "裤", "裙", "卫衣", "外套", "服装", "衣服", "服饰", "速干")
            return any(term in searchable for term in garment_terms)

        if any(term in query for term in ("美妆", "护肤", "彩妆")) and category != "美妆护肤":
            return False
        if any(term in query for term in ("数码", "电子", "手机", "平板", "电脑", "笔记本", "耳机")) and category != "数码电子":
            return False
        if any(term in query for term in ("食品", "饮料", "零食", "吃的", "喝的")) and category != "食品饮料":
            return False

        return True

    @staticmethod
    def _log_target_products(user_query: str, selected_products: List[Dict[str, Any]], stage: str) -> None:
        if not selected_products:
            logger.info("[TargetProducts] stage=%s | query=%s | none", stage, user_query)
            return
        logger.info("[TargetProducts] stage=%s | query=%s | count=%s", stage, user_query, len(selected_products))
        for item in selected_products:
            logger.info(
                "[TargetProducts] rank=%s | product_id=%s | title=%s | brand=%s | category=%s/%s | price=%s | source=%s | role=%s",
                item.get("rank"),
                item.get("product_id"),
                item.get("title"),
                item.get("brand"),
                item.get("category"),
                item.get("sub_category"),
                item.get("base_price"),
                item.get("source"),
                item.get("recommendation_role"),
            )

    @staticmethod
    def _build_direct_product_query_text(user_query: str) -> str:
        return user_query.strip()

    @classmethod
    def _query_direct_selected_products(cls, user_query: str, limit: int = 5) -> List[Dict[str, Any]]:
        query_text = cls._build_direct_product_query_text(user_query)
        if not query_text:
            return []

        result = run_tool("query_products", {"text": query_text, "limit": limit})
        if not isinstance(result, dict) or not result.get("ok") or result.get("total", 0) <= 0:
            return []

        return cls._extract_products_from_tool_result(result, limit=limit)

    @staticmethod
    def _build_deterministic_final_reply(
        user_query: str,
        selected_products: List[Dict[str, Any]],
    ) -> str:
        if not selected_products:
            return ""

        lines = ["我根据商品库里已核验的商品，先给你整理一个稳妥选择："]
        for index, item in enumerate(selected_products[:3], start=1):
            title = item.get("title") or "命中商品"
            brand = item.get("brand") or ""
            sub_category = item.get("sub_category") or item.get("category") or ""
            price = item.get("base_price")
            details = [title]
            if brand:
                details.append(f"品牌：{brand}")
            if sub_category:
                details.append(f"品类：{sub_category}")
            if price is not None:
                details.append(f"参考价：{price}")
            lines.append(f"{index}. " + "，".join(details))

        lines.append("这些商品都来自当前商品数据库，建议优先结合你的预算、使用场景和品牌偏好再做取舍。")

        return "\n".join(lines)

    @staticmethod
    def _sanitize_user_reply(content: str) -> str:
        """移除对用户选购无帮助的内部商品/SKU 标识。"""
        text = content or ""
        text = re.sub(r"(?im)^\s*(?:商品\s*ID|product_id|sku_id|SKU)\s*[:：].*$", "", text)
        text = re.sub(r"（\s*(?:商品\s*ID|product_id|sku_id|SKU)\s*[:：]\s*[^）]+）", "", text, flags=re.I)
        text = re.sub(r"\(\s*(?:商品\s*ID|product_id|sku_id|SKU)\s*[:：]\s*[^)]+\)", "", text, flags=re.I)
        text = re.sub(r"(?:商品\s*ID|product_id|sku_id|SKU)\s*[:：]\s*[psc]_[a-z]+_\d+(?:_\d+)?", "", text, flags=re.I)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    @staticmethod
    def _sanitize_user_reply_chunk(content: str) -> str:
        """流式片段清洗，保留片段原有空白以免破坏 Markdown 排版。"""
        text = content or ""
        text = re.sub(r"（\s*(?:商品\s*ID|product_id|sku_id|SKU)\s*[:：]\s*[^）]+）", "", text, flags=re.I)
        text = re.sub(r"\(\s*(?:商品\s*ID|product_id|sku_id|SKU)\s*[:：]\s*[^)]+\)", "", text, flags=re.I)
        text = re.sub(r"(?:商品\s*ID|product_id|sku_id|SKU)\s*[:：]\s*[psc]_[a-z]+_\d+(?:_\d+)?", "", text, flags=re.I)
        return text

    @staticmethod
    def _build_selected_products_context(selected_products: List[Dict[str, Any]]) -> str:
        if not selected_products:
            return "未找到明确命中的商品候选。"

        public_products: List[Dict[str, Any]] = []
        for item in selected_products:
            public_products.append(
                {
                    "rank": item.get("rank"),
                    "title": item.get("title"),
                    "brand": item.get("brand"),
                    "category": item.get("category"),
                    "sub_category": item.get("sub_category"),
                    "base_price": item.get("base_price"),
                    "marketing_desc": item.get("marketing_desc"),
                }
            )

        return (
            "## 内部目标商品清单（已从商品数据库校验存在）\n"
            "```json\n"
            f"{json.dumps(public_products, ensure_ascii=False, indent=2)}\n"
            "```"
        )

    @staticmethod
    def _build_final_recommendation_messages(
        system_prompt: str,
        analysis_text: str,
        selected_products: List[Dict[str, Any]],
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> List[Dict[str, str]]:
        selected_context = ToolChatProductSelectionMixin._build_selected_products_context(selected_products)
        final_guidance = (
            "你现在进入最终导购推荐阶段。\n"
            "请基于前面的需求分析和内部目标商品清单，输出面向用户的最终推荐。\n"
            "要求：1) 开头先用一句话确认用户需求；2) 围绕目标商品给出推荐理由、适配场景和取舍建议；"
            "3) 不要重复长篇需求分析；4) 推荐要自然、像真人导购；"
            "5) 只能推荐内部目标商品清单中的商品，不要编造或改用清单之外的商品；"
            "6) 商品事实以内部目标商品清单 JSON 和工具结果为准；"
            "7) product_id、sku_id、source、recommendation_role 是内部核对字段，除非用户明确询问，不要在对外回复里展示。\n\n"
            f"需求分析摘要：\n{analysis_text or '（无）'}\n\n"
            f"{selected_context}"
        )
        messages: List[Dict[str, str]] = [
            {
                "role": "system",
                "content": system_prompt + "\n\n" + final_guidance,
            }
        ]
        if conversation_history:
            for msg in conversation_history:
                role = msg.get("role")
                content = msg.get("content")
                if role in {"user", "assistant"} and content:
                    messages.append({"role": role, "content": content})
        messages.append({"role": "user", "content": user_query})
        return messages
