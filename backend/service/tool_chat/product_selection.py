"""工具聊天目标商品选择与最终回复辅助。"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..product_search.engine import build_keyword_terms, strip_intent_words
from ..product_search.query_tool import run_tool
from ..product_search.sqlite_search import sqlite_product_search_service
from ..product_search.search_semantics_service import search_semantics_service
from config.logging_config import get_logger

logger = get_logger("service.tool_chat")


class ToolChatProductSelectionMixin:
    """目标商品提取、合并、校验和对外回复清洗能力。"""

    @staticmethod
    def _normalize_search_plan(raw_plan: Any, user_query: str = "") -> Optional[Dict[str, Any]]:
        """标准化 LLM 生成的商品搜索计划；无效时返回 None。"""
        if not isinstance(raw_plan, dict):
            return None

        def text_value(key: str) -> str:
            return str(raw_plan.get(key) or "").strip()

        def list_value(key: str) -> List[str]:
            value = raw_plan.get(key)
            if isinstance(value, str):
                return [value.strip()] if value.strip() else []
            if isinstance(value, list):
                return [str(item).strip() for item in value if str(item).strip()]
            return []

        plan = {
            "target_product": text_value("target_product") or user_query.strip(),
            "target_category": text_value("target_category"),
            "target_sub_category": text_value("target_sub_category"),
            "query_text": text_value("query_text") or text_value("target_product") or user_query.strip(),
            "fallback_query_texts": list_value("fallback_query_texts"),
            "direct_terms": list_value("direct_terms"),
            "acceptable_fallback_terms": list_value("acceptable_fallback_terms"),
            "allowed_categories": list_value("allowed_categories"),
            "forbidden_categories": list_value("forbidden_categories"),
            "fallback_notice_required": bool(raw_plan.get("fallback_notice_required", True)),
            "reason": text_value("reason"),
        }
        if not plan["direct_terms"] and plan["target_product"]:
            plan["direct_terms"] = [plan["target_product"]]
        # 用语义表修正 LLM 输出，确保品类值和约束来自业务知识而非 LLM 猜测
        plan = ToolChatProductSelectionMixin._apply_semantic_corrections(plan, user_query)
        return plan

    @staticmethod
    def _apply_semantic_corrections(plan: Dict[str, Any], user_query: str) -> Dict[str, Any]:
        """用语义表对 LLM 生成的 SearchPlan 做 deterministic 修正。"""
        query = user_query or plan.get("target_product", "")
        category_tree = sqlite_product_search_service.get_category_tree()
        valid_categories = set(category_tree.keys())
        valid_sub_categories = {sub for subs in category_tree.values() for sub in subs}

        # 1. 品类别名修正：确保 target_category/target_sub_category 使用库内真实值
        if not plan.get("target_category") or not plan.get("target_sub_category"):
            resolved = search_semantics_service.resolve_category(query)
            if resolved:
                if not plan.get("target_category"):
                    plan["target_category"] = resolved["category"]
                if not plan.get("target_sub_category"):
                    plan["target_sub_category"] = resolved["sub_category"]

        # 1b. 校验 LLM 输出的 category/sub_category 是否为数据库真实枚举，非真实值则清空。
        # 防止 LLM 输出 "智能数码硬件"、"笔记本电脑、平板电脑" 或 "数码产品" 等伪类目。
        if valid_categories:
            current_category = str(plan.get("target_category") or "").strip()
            if current_category and current_category not in valid_categories:
                logger.info("[SearchPlan修正] target_category='%s' 非数据库真实值，已清空", current_category)
                plan["target_category"] = ""

        current_sub = plan.get("target_sub_category", "")
        if current_sub:
            target_category = str(plan.get("target_category") or "").strip()
            if valid_sub_categories and current_sub not in valid_sub_categories:
                logger.info("[SearchPlan修正] target_sub_category='%s' 非数据库真实值，已清空", current_sub)
                plan["target_sub_category"] = ""
            elif target_category and category_tree and current_sub not in category_tree.get(target_category, []):
                logger.info(
                    "[SearchPlan修正] target_sub_category='%s' 不属于 target_category='%s'，已清空",
                    current_sub,
                    target_category,
                )
                plan["target_sub_category"] = ""

        # 2. 品牌型号修正：strict_direct 品牌必须包含在 direct_terms 中
        strict_terms = search_semantics_service.get_strict_direct_terms(query)
        if strict_terms:
            existing = set(t.lower() for t in plan.get("direct_terms", []))
            for t in strict_terms:
                if t.lower() not in existing:
                    plan.setdefault("direct_terms", []).append(t)

        # 2b. 商品概念修正：把语义表的 direct_terms 注入，替代含意图词的原始 query
        concepts = search_semantics_service.match_product_concepts(query)
        if concepts:
            concept_terms: list[str] = []
            for c in concepts:
                concept_terms.extend(c.get("direct_terms", []))
            if concept_terms:
                existing = set(t.lower() for t in plan.get("direct_terms", []))
                for t in concept_terms:
                    if t.lower() not in existing:
                        plan.setdefault("direct_terms", []).append(t)

        # 3. Fallback 关系修正：注入 forbidden_categories
        semantic_forbidden = search_semantics_service.get_forbidden_categories(query)
        if semantic_forbidden:
            existing_forbidden = set(plan.get("forbidden_categories", []))
            for cat in semantic_forbidden:
                if cat not in existing_forbidden:
                    plan.setdefault("forbidden_categories", []).append(cat)

        # 4. 品类约束修正：从语义表补充 allowed_categories
        if not plan.get("allowed_categories"):
            semantic_allowed = search_semantics_service.get_effective_allowed_categories(query, None)
            if semantic_allowed:
                plan["allowed_categories"] = list(semantic_allowed)

        # 5. 如果 allowed_categories 明确且只有一个，自动推断 forbidden_categories
        all_categories = list(valid_categories) if valid_categories else ["美妆护肤", "数码电子", "服饰运动", "食品饮料"]
        if valid_categories:
            plan["allowed_categories"] = [
                cat for cat in plan.get("allowed_categories", [])
                if cat in valid_categories
            ]
            plan["forbidden_categories"] = [
                cat for cat in plan.get("forbidden_categories", [])
                if cat in valid_categories
            ]
        allowed = plan.get("allowed_categories", [])
        existing_forbidden = set(plan.get("forbidden_categories", []))
        if allowed and len(allowed) == 1:
            target_cat = allowed[0]
            for cat in all_categories:
                if cat != target_cat and cat not in existing_forbidden:
                    plan.setdefault("forbidden_categories", []).append(cat)

        return plan

    @staticmethod
    def _parse_search_plan_content(content: str, user_query: str = "") -> Optional[Dict[str, Any]]:
        """从 LLM 文本中解析 SearchPlan JSON。"""
        text = (content or "").strip()
        if not text:
            return None
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
        try:
            return ToolChatProductSelectionMixin._normalize_search_plan(json.loads(text), user_query)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if not match:
                return None
            try:
                return ToolChatProductSelectionMixin._normalize_search_plan(json.loads(match.group(0)), user_query)
            except json.JSONDecodeError:
                return None

    @staticmethod
    def _extract_selected_products(
        tool_results: Dict[str, Dict[str, Any]],
        tool_call_order: List[str],
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """按工具调用顺序提取商品摘要，保持 LLM 查询的优先级顺序。"""
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
        """从单次工具结果中提取去重后的商品摘要。"""
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
    def _extract_products_from_rag_sources(rag_sources: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
        """从 RAG 来源商品中提取可回查的候选摘要。"""
        selected: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        for item in rag_sources:
            if not isinstance(item, dict):
                continue
            product_id = str(item.get("product_id") or "").strip()
            if not product_id or product_id in seen_ids:
                continue
            seen_ids.add(product_id)
            selected.append(
                {
                    "product_id": product_id,
                    "title": str(item.get("title") or ""),
                    "brand": str(item.get("brand") or ""),
                    "category": str(item.get("category") or ""),
                    "sub_category": str(item.get("sub_category") or ""),
                    "base_price": item.get("base_price") or item.get("price"),
                    "rag_distance": item.get("distance"),
                    "rag_rerank": item.get("llm_rerank"),
                }
            )
            if len(selected) >= limit:
                break

        return selected

    @staticmethod
    def _enrich_tool_arguments_with_search_plan(
        arguments: Dict[str, Any],
        search_plan: Optional[Dict[str, Any]],
    ) -> tuple[Dict[str, Any], List[str]]:
        """用 SearchPlan 给 LLM 工具参数补确定性约束，并返回补全原因。"""
        enriched = dict(arguments or {})
        reasons: List[str] = []
        if not search_plan:
            return enriched, reasons

        allowed_categories = [
            str(item).strip()
            for item in (search_plan.get("allowed_categories") or [])
            if str(item).strip()
        ]
        if len(allowed_categories) == 1 and not enriched.get("category"):
            enriched["category"] = allowed_categories[0]
            reasons.append(f"SearchPlan 限定 allowed_categories={allowed_categories}，自动补 category")

        target_sub_category = ToolChatProductSelectionMixin._single_search_plan_sub_category(search_plan)
        raw_sub_category = str(search_plan.get("target_sub_category") or "").strip()
        if target_sub_category and not enriched.get("sub_category"):
            enriched["sub_category"] = target_sub_category
            reasons.append(f"SearchPlan 指定 target_sub_category={target_sub_category}，自动补 sub_category")
        elif raw_sub_category and not target_sub_category:
            reasons.append(f"SearchPlan.target_sub_category={raw_sub_category} 包含多个或非精确子类目，跳过 sub_category 精确过滤")

        if not enriched.get("text") and not enriched.get("keyword"):
            query_text = str(search_plan.get("query_text") or "").strip()
            if query_text:
                enriched["text"] = query_text
                reasons.append("工具参数缺少 text/keyword，使用 SearchPlan.query_text 兜底")

        return enriched, reasons

    @staticmethod
    def _single_search_plan_sub_category(search_plan: Optional[Dict[str, Any]]) -> str:
        """仅当 SearchPlan 子类目是单个精确值时返回，避免多值字符串误作 SQL 等值过滤。"""
        if not search_plan:
            return ""
        value = str(search_plan.get("target_sub_category") or "").strip()
        if not value:
            return ""
        if re.search(r"[、,，/／|｜;；\s]+", value):
            return ""
        category = str(search_plan.get("target_category") or "").strip()
        if sqlite_product_search_service.validate_category_filters(category=category or None, sub_category=value):
            return ""
        return value

    @staticmethod
    def _merge_selected_products(*groups: List[Dict[str, Any]], limit: int = 5) -> List[Dict[str, Any]]:
        """按传入组顺序合并商品，保留首次出现的 product_id。"""
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
        search_plan: Optional[Dict[str, Any]] = None,
        rag_products: Optional[List[Dict[str, Any]]] = None,
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        """生成最终可推荐商品白名单。

        先合并候选，再按 product_id 回查数据库，最后应用用户显式品类约束。
        最终回复只能围绕这里返回的商品生成。
        """
        candidates: List[Dict[str, Any]] = []
        candidate_meta: Dict[str, Dict[str, Any]] = {}

        # RAG 已经过向量召回和 LLM rerank，适合作为语义强相关候选；
        # 工具查询和 SearchPlan 直查继续提供商品库维度的补充。
        for source, group in [("rag_context", rag_products or []), ("tool_query", tool_products), ("direct_query", direct_products)]:
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
        default_sku_ids = sqlite_product_search_service.get_default_sku_ids_by_product_ids(product_ids)

        targets: List[Dict[str, Any]] = []
        for db_item in verification.get("items") or []:
            # 以数据库回查结果为准，避免工具结果中的截断字段或脏字段进入最终 prompt。
            product_id = str(db_item.get("product_id") or "").strip()
            if not product_id:
                continue
            meta = candidate_meta.get(product_id) or {}
            image_path = str(db_item.get("image_path") or "")
            sku_id = str(meta.get("sku_id") or db_item.get("sku_id") or default_sku_ids.get(product_id) or "")
            landing_url = f"/api/product-search/products/{product_id}/page"
            if sku_id:
                landing_url = f"{landing_url}?sku_id={quote(sku_id, safe='')}"
            target = {
                "rank": len(targets) + 1,
                "product_id": product_id,
                "sku_id": sku_id,
                "title": str(db_item.get("title") or ""),
                "brand": str(db_item.get("brand") or ""),
                "category": str(db_item.get("category") or ""),
                "sub_category": str(db_item.get("sub_category") or ""),
                "base_price": db_item.get("base_price"),
                "image_path": image_path,
                "marketing_desc": str(db_item.get("marketing_desc") or "")[:500],
                "source": str(meta.get("source") or "database"),
                "recommendation_role": str(meta.get("recommendation_role") or ("primary" if not targets else "supporting")),
                "image_url": f"/api/product-search/images/{quote(image_path, safe='/')}" if image_path else "",
                "landing_url": landing_url,
            }
            direct_match = ToolChatProductSelectionMixin._is_direct_product_match(user_query, target, search_plan)
            target["match_type"] = "direct" if direct_match else "fallback"
            target["match_note"] = (
                "直接匹配用户要找的商品关键词"
                if direct_match
                else "非直接匹配商品，作为相邻品类或场景替代推荐"
            )
            rejection_reason = ToolChatProductSelectionMixin._product_constraint_rejection_reason(user_query, target, search_plan)
            if rejection_reason:
                logger.info(
                    "[TargetProductsFilter] query=%s | filtered product_id=%s title=%s category=%s/%s | reason=%s",
                    user_query,
                    product_id,
                    target["title"],
                    target["category"],
                    target["sub_category"],
                    rejection_reason,
                )
                continue
            target["rank"] = len(targets) + 1
            targets.append(target)

        direct_targets = [item for item in targets if item.get("match_type") == "direct"]
        if direct_targets:
            targets = direct_targets
            for index, item in enumerate(targets, start=1):
                item["rank"] = index
                item["recommendation_role"] = "primary" if index == 1 else "supporting"
        else:
            targets = ToolChatProductSelectionMixin._prefer_closest_fallbacks(user_query, targets, search_plan)
            for index, item in enumerate(targets, start=1):
                item["rank"] = index
                item["recommendation_role"] = "fallback_primary" if index == 1 else "fallback_supporting"

        return targets

    @staticmethod
    def _is_direct_product_match(
        user_query: str,
        product: Dict[str, Any],
        search_plan: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """判断候选是否直接匹配用户点名的商品，而不是宽泛替代品。"""
        cleaned_query = strip_intent_words(user_query or "")
        if not cleaned_query:
            return False

        title = str(product.get("title") or "")
        category = str(product.get("category") or "")
        sub_category = str(product.get("sub_category") or "")
        searchable = f"{title} {category} {sub_category}"
        searchable_lower = searchable.lower()
        query_lower = cleaned_query.lower()

        specific_model_tokens = ("ipad", "iphone", "macbook", "airpods", "matebook", "thinkpad", "thinkbook")
        # 从品牌型号表动态扩展 strict_direct 模型词
        brand_models = search_semantics_service.match_brand_models(cleaned_query)
        for bm in brand_models:
            for dt in bm.get("direct_terms", []):
                if dt.lower() not in specific_model_tokens:
                    specific_model_tokens = (*specific_model_tokens, dt.lower())
        requested_specific_tokens = [token for token in specific_model_tokens if token in query_lower]
        if requested_specific_tokens:
            # strict_direct 模式：必须标题/品牌含该词
            if search_semantics_service.is_strict_direct(cleaned_query):
                return any(token in searchable_lower for token in requested_specific_tokens)
            return any(token in searchable_lower for token in requested_specific_tokens)

        if search_plan:
            direct_terms = [str(term).strip().lower() for term in (search_plan.get("direct_terms") or []) if str(term).strip()]
            if direct_terms and any(term in searchable_lower for term in direct_terms):
                return True

        if cleaned_query in searchable:
            return True

        strong_tokens = re.findall(r"[A-Za-z][A-Za-z0-9+\\-]*", cleaned_query)
        if strong_tokens and any(token.lower() in searchable_lower for token in strong_tokens):
            return True

        direct_aliases = {
            "平板": ("平板", "pad", "ipad"),
            "笔记本": ("笔记本", "notebook", "macbook", "matebook", "thinkpad", "thinkbook"),
            "防晒": ("防晒",),
            "跑步鞋": ("跑步鞋", "跑鞋"),
            "肉松饼": ("肉松饼",),
        }
        for query_term, aliases in direct_aliases.items():
            if query_term in query_lower and any(alias.lower() in searchable_lower for alias in aliases):
                return True

        terms = [term for term in build_keyword_terms(cleaned_query) if len(term) >= 2 and term != cleaned_query]
        terms = [term for term in terms if term not in {"推荐", "好吃", "好喝", "好用", "便宜", "划算"}]
        if len(terms) >= 2:
            return all(term in searchable for term in terms[:3])
        if len(terms) == 1:
            return terms[0] in searchable
        return False

    @staticmethod
    def _matches_user_product_constraints(
        user_query: str,
        product: Dict[str, Any],
        search_plan: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """按用户显式品类约束过滤目标商品，避免泛检索结果混入最终推荐。"""
        return ToolChatProductSelectionMixin._product_constraint_rejection_reason(user_query, product, search_plan) is None

    @staticmethod
    def _product_constraint_rejection_reason(
        user_query: str,
        product: Dict[str, Any],
        search_plan: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        """返回候选商品被过滤的原因；未过滤返回 None。"""
        query = user_query or ""
        title = str(product.get("title") or "")
        category = str(product.get("category") or "")
        sub_category = str(product.get("sub_category") or "")
        searchable = f"{title} {category} {sub_category}"

        # 语义表兜底：用 fallback_relations 的 forbidden_categories 过滤
        semantic_forbidden = search_semantics_service.get_forbidden_categories(query)
        if semantic_forbidden and category in semantic_forbidden:
            return f"命中语义禁止类目: {category}"

        if search_plan:
            allowed_categories = [str(item).strip() for item in (search_plan.get("allowed_categories") or []) if str(item).strip()]
            forbidden_categories = [str(item).strip() for item in (search_plan.get("forbidden_categories") or []) if str(item).strip()]
            if allowed_categories and category not in allowed_categories:
                return f"不在 SearchPlan 允许类目 {allowed_categories} 内"
            if forbidden_categories and category in forbidden_categories:
                return f"命中 SearchPlan 禁止类目 {forbidden_categories}"

            fallback_terms = [str(term).strip().lower() for term in (search_plan.get("acceptable_fallback_terms") or []) if str(term).strip()]
            direct_terms = [str(term).strip().lower() for term in (search_plan.get("direct_terms") or []) if str(term).strip()]
            semantic_terms = fallback_terms + direct_terms
            # 仅当没有 allowed_categories 约束时才用 semantic_terms 过滤，
            # 避免 fallback 结果（如唇釉、笔记本电脑）被误杀
            if semantic_terms and not allowed_categories and not any(term in searchable.lower() for term in semantic_terms):
                return f"未命中 direct/fallback 语义词 {semantic_terms}"

        beauty_terms = (
            "美妆", "护肤", "彩妆", "防晒", "防晒霜", "口红", "唇釉", "唇膏", "粉底", "眉笔",
            "蜜粉", "散粉", "卸妆", "洁面", "洗面奶", "面膜", "面霜", "眼霜", "精华",
            "洗发水", "洗发露", "护发", "沐浴露", "爽肤水", "化妆水",
        )
        if any(term in query for term in beauty_terms) and category != "美妆护肤":
            return "用户显式美妆需求，但候选不是美妆护肤"

        digital_terms = (
            "数码", "电子", "手机", "平板", "ipad", "iPad", "电脑", "笔记本", "游戏本",
            "轻薄本", "耳机", "蓝牙", "macbook", "MacBook", "matebook", "MateBook",
        )
        if any(term in query for term in digital_terms) and category != "数码电子":
            return "用户显式数码需求，但候选不是数码电子"

        clothing_terms = (
            "服装", "衣服", "衣物", "服饰", "穿搭", "上衣", "T恤", "短袖", "裤", "裙", "连衣裙", "卫衣",
            "运动", "训练", "健身", "力量训练", "速干", "透气",
        )
        explicit_clothing = any(term in query for term in clothing_terms)
        explicitly_accessory_or_shoe = any(term in query for term in ("帽", "鞋", "包", "背包", "腰包"))
        if explicit_clothing:
            if category != "服饰运动":
                return "用户显式服饰需求，但候选不是服饰运动"
            if not explicitly_accessory_or_shoe and any(term in searchable for term in ("帽", "跑步鞋", "徒步鞋", "鞋")):
                return "用户要服装，候选偏配饰/鞋类"
            garment_terms = ("T恤", "短袖", "上衣", "裤", "裙", "卫衣", "外套", "服装", "衣服", "服饰", "速干")
            if not any(term in searchable for term in garment_terms):
                return "服饰候选未命中衣物词"
            return None

        food_terms = (
            "食品", "饮料", "零食", "吃的", "喝的", "好吃", "好喝", "甜甜圈", "面包", "肉松",
            "糕点", "点心", "早餐", "代餐", "饼干", "饼", "酸奶", "牛奶", "咖啡", "茶", "方便面",
        )
        if any(term in query for term in food_terms) and category != "食品饮料":
            return "用户显式食品需求，但候选不是食品饮料"

        return None

    @staticmethod
    def _prefer_closest_fallbacks(
        user_query: str,
        products: List[Dict[str, Any]],
        search_plan: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """没有 direct 时，若存在明显更近的替代品，只保留近邻候选。"""
        if search_plan:
            fallback_terms = [str(term).strip().lower() for term in (search_plan.get("acceptable_fallback_terms") or []) if str(term).strip()]
            if fallback_terms:
                preferred = []
                for item in products:
                    searchable = (
                        f"{item.get('title') or ''} {item.get('category') or ''} {item.get('sub_category') or ''}"
                    ).lower()
                    if any(term in searchable for term in fallback_terms):
                        preferred.append(item)
                if preferred:
                    return preferred

        query = user_query or ""

        # 语义表兜底：用 fallback_relations 的 acceptable 规则过滤
        relation = search_semantics_service.get_fallback_relation(query)
        if relation:
            acceptable = relation.get("acceptable", [])
            if acceptable:
                preferred = []
                for item in products:
                    item_cat = str(item.get("category") or "")
                    item_sub = str(item.get("sub_category") or "")
                    searchable = (
                        f"{item.get('title') or ''} {item_cat} {item_sub}"
                    ).lower()
                    for acc in acceptable:
                        acc_cat = acc.get("category", "")
                        acc_sub = acc.get("sub_category", "")
                        acc_terms = [t.lower() for t in acc.get("terms", [])]
                        if item_cat == acc_cat and (not acc_sub or item_sub == acc_sub):
                            preferred.append(item)
                            break
                        if any(t in searchable for t in acc_terms):
                            preferred.append(item)
                            break
                if preferred:
                    return preferred

        preference_groups = [
            (("口红", "唇膏", "唇彩"), ("口红", "唇釉", "唇膏", "唇部", "彩妆")),
            (("游戏本",), ("笔记本", "电脑", "macbook", "matebook", "thinkpad", "thinkbook")),
            (("连衣裙",), ("连衣裙", "半身裙", "裙", "女装", "女士", "瑜伽裤", "裤")),
        ]
        for triggers, preferred_terms in preference_groups:
            if not any(term in query for term in triggers):
                continue
            preferred: List[Dict[str, Any]] = []
            for item in products:
                searchable = (
                    f"{item.get('title') or ''} {item.get('category') or ''} {item.get('sub_category') or ''}"
                ).lower()
                if any(term.lower() in searchable for term in preferred_terms):
                    preferred.append(item)
            if preferred:
                return preferred
        return products

    @staticmethod
    def _log_target_products(user_query: str, selected_products: List[Dict[str, Any]], stage: str) -> None:
        """记录最终候选商品，便于追踪推荐是否来自数据库白名单。"""
        if not selected_products:
            logger.info("[TargetProducts] stage=%s | query=%s | none", stage, user_query)
            return
        logger.info("[TargetProducts] stage=%s | query=%s | count=%s", stage, user_query, len(selected_products))
        for item in selected_products:
            logger.info(
                "[TargetProducts] rank=%s | product_id=%s | title=%s | brand=%s | category=%s/%s | price=%s | source=%s | role=%s | match=%s",
                item.get("rank"),
                item.get("product_id"),
                item.get("title"),
                item.get("brand"),
                item.get("category"),
                item.get("sub_category"),
                item.get("base_price"),
                item.get("source"),
                item.get("recommendation_role"),
                item.get("match_type"),
            )

    @staticmethod
    def _build_direct_product_query_text(user_query: str) -> str:
        """构造后台直查的自然语言查询文本，目前直接使用原始用户问题。"""
        return user_query.strip()

    @classmethod
    def _query_direct_selected_products(
        cls,
        user_query: str,
        limit: int = 5,
        search_plan: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """根据 LLM SearchPlan 召回商品；计划不可用时用原始问题兜底。"""
        query_texts: List[str] = []
        if search_plan:
            for text in [search_plan.get("query_text"), *(search_plan.get("fallback_query_texts") or [])]:
                clean_text = str(text or "").strip()
                if clean_text and clean_text not in query_texts:
                    query_texts.append(clean_text)
        if not query_texts:
            query_text = cls._build_direct_product_query_text(user_query)
            if query_text:
                query_texts.append(query_text)
        if not query_texts:
            return []

        selected: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()
        for query_text in query_texts:
            arguments: Dict[str, Any] = {"text": query_text, "limit": max(limit, 10)}
            if search_plan:
                allowed_categories = [str(item).strip() for item in (search_plan.get("allowed_categories") or []) if str(item).strip()]
                if len(allowed_categories) == 1:
                    arguments["category"] = allowed_categories[0]
                target_sub_category = cls._single_search_plan_sub_category(search_plan)
                if target_sub_category:
                    arguments["sub_category"] = target_sub_category
            result = run_tool("query_products", arguments)
            if not isinstance(result, dict) or not result.get("ok") or result.get("total", 0) <= 0:
                continue
            for item in cls._extract_products_from_tool_result(result, limit=max(limit, 10)):
                product_id = str(item.get("product_id") or "").strip()
                if not product_id or product_id in seen_ids:
                    continue
                seen_ids.add(product_id)
                selected.append(item)
                if len(selected) >= limit:
                    return selected

        return selected

    @staticmethod
    def _build_deterministic_final_reply(
        user_query: str,
        selected_products: List[Dict[str, Any]],
    ) -> str:
        """构造无需再调用 LLM 的兜底推荐文本。"""
        if not selected_products:
            return ""

        lines = ["我根据商品库里已核验的商品，先给你整理一个稳妥选择："]
        if selected_products and not any(item.get("match_type") == "direct" for item in selected_products):
            lines = ["当前商品库里没有查到直接匹配的商品，我先按相邻品类给你整理几个可替代选择："]
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
        """把目标商品清单转成最终推荐 prompt 中的内部 JSON 上下文。"""
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
                    "match_type": item.get("match_type"),
                    "match_note": item.get("match_note"),
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
        """构造最终推荐 LLM 消息，强约束只能推荐已校验目标商品。"""
        selected_context = ToolChatProductSelectionMixin._build_selected_products_context(selected_products)
        final_guidance = (
            "你现在进入最终导购推荐阶段。\n"
            "请基于前面的需求分析和内部目标商品清单，输出面向用户的最终推荐。\n"
            "要求：1) 开头先用一句话确认用户需求；2) 围绕目标商品给出推荐理由、适配场景和取舍建议；"
            "3) 不要重复长篇需求分析；4) 推荐要自然、像真人导购；"
            "5) 只能推荐内部目标商品清单中的商品，不要编造或改用清单之外的商品；"
            "6) 商品事实以内部目标商品清单 JSON 和工具结果为准；"
            "7) 如果清单中所有商品的 match_type 都是 fallback，必须先明确说明当前商品库没有查到直接匹配商品，"
            "再把这些商品作为相邻品类、搭配场景或替代选择推荐，不能暗示它们就是用户点名的商品；"
            "8) 描述 fallback 商品时必须尊重商品真实品类：酸奶、牛奶、咖啡、茶饮、气泡水、功能饮料只能说成搭配饮品或补充选择，"
            "不能说成点心、糕点、面包、甜甜圈，也不能声称它们和用户点名食品口感相近；肉松饼这类点心可以说是风味相邻的替代点心；"
            "9) 用户要游戏本但只有 fallback 笔记本时，必须说明没有专门游戏本；非游戏本笔记本只能说可做轻度游戏、日常娱乐或一般性能参考，"
            "不能承诺高配置游戏、大型游戏流畅、专业电竞或重度游戏体验，除非商品标题明确写了游戏本/电竞/独显；"
            "10) 用户要连衣裙但只有 fallback 服饰时，不能把男款T恤、运动短裤等说成连衣裙替代；只可作为普通服饰搭配参考；"
            "11) fallback 结尾不要说“这些都是同类商品/点心/直接可选”，只说“可作为搭配或替代参考”；"
            "12) 如果存在 direct 商品，优先推荐 direct 商品，不要混入 fallback 商品；"
            "13) product_id、sku_id、source、recommendation_role、match_type、match_note 是内部核对字段，除非用户明确询问，不要在对外回复里展示。\n\n"
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
