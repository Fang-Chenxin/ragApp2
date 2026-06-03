"""工具调用聊天服务模块 - 封装 SQLite 商品搜索工具对话逻辑"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from .llm_service import LLMService
from .sqlite_product_query_tool import get_tool_spec, run_tool
from .rag_service import VectorStore
from config.logging_config import get_logger
from config.settings import settings

logger = get_logger("service.tool_chat")


class ToolChatService:
    """工具调用聊天服务封装类"""

    def __init__(self, vector_store: VectorStore, llm: LLMService):
        self.vector_store = vector_store
        self.llm = llm

    @staticmethod
    def _status_chunk(
        content: str,
        phase: str,
        *,
        agent: str = "shopping_agent",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "type": "status",
            "content": content,
            "phase": phase,
            "agent": agent,
            "timings": None,
        }
        if extra:
            payload.update(extra)
        return payload

    @staticmethod
    def _format_context_docs(context_docs: List[Any]) -> str:
        lines: list[str] = []
        for index, item in enumerate(context_docs, start=1):
            if not isinstance(item, dict):
                lines.append(str(item))
                continue

            metadata = item.get("metadata") or {}
            source_parts = []
            for key, label in [
                ("product_id", "商品ID"),
                ("title", "商品"),
                ("brand", "品牌"),
                ("category", "分类"),
                ("sub_category", "子分类"),
                ("chunk_type", "片段类型"),
            ]:
                value = metadata.get(key)
                if value:
                    source_parts.append(f"{label}: {value}")

            source_text = " | ".join(source_parts) or f"片段ID: {item.get('id', '')}"
            lines.append(f"[知识片段 {index} | {source_text}]\n{item.get('content', '')}")

        return "\n\n".join(lines)

    @staticmethod
    def _extract_rag_sources(context_docs: List[Any]) -> List[Dict[str, Any]]:
        sources: list[Dict[str, Any]] = []
        seen: set[str] = set()

        for item in context_docs:
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") or {}
            product_id = str(metadata.get("product_id") or "").strip()
            source_id = str(item.get("id") or item.get("rank") or item.get("source_id") or item.get("product_id") or "").strip()
            dedupe_key = product_id or source_id
            if not dedupe_key or dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            sources.append(
                {
                    "product_id": product_id,
                    "source_id": source_id,
                    "title": str(metadata.get("title") or ""),
                    "brand": str(metadata.get("brand") or ""),
                    "category": str(metadata.get("category") or ""),
                    "sub_category": str(metadata.get("sub_category") or ""),
                    "chunk_type": str(metadata.get("chunk_type") or ""),
                    "distance": item.get("distance"),
                    "llm_rerank": item.get("llm_rerank"),
                }
            )

        return sources

    @staticmethod
    def _summarize_context_docs(context_docs: List[Any]) -> List[Dict[str, Any]]:
        summaries: list[Dict[str, Any]] = []
        for index, item in enumerate(context_docs, start=1):
            if not isinstance(item, dict):
                summaries.append(
                    {
                        "rank": index,
                        "id": str(index),
                        "preview": str(item)[:160],
                    }
                )
                continue
            metadata = item.get("metadata") or {}
            summaries.append(
                {
                    "rank": index,
                    "id": str(item.get("id") or index),
                    "product_id": str(metadata.get("product_id") or ""),
                    "title": str(metadata.get("title") or ""),
                    "category": str(metadata.get("category") or ""),
                    "sub_category": str(metadata.get("sub_category") or ""),
                    "chunk_type": str(metadata.get("chunk_type") or ""),
                    "distance": item.get("distance"),
                    "preview": str(item.get("content") or "")[:160],
                    "llm_rerank": item.get("llm_rerank"),
                }
            )
        return summaries

    @staticmethod
    def _debug_chunk(phase: str, title: str, **extra: Any) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "type": "debug",
            "phase": phase,
            "content": title,
            "title": title,
            "timings": None,
        }
        payload.update(extra)
        return payload

    def _query_context_docs(self, user_query: str) -> List[Any]:
        if hasattr(self.vector_store, "query_with_sources"):
            return self.vector_store.query_with_sources(user_query)
        return self.vector_store.query(user_query)

    async def _query_context_docs_with_timeout(self, user_query: str) -> tuple[List[Any], Optional[str]]:
        try:
            context_docs = await asyncio.wait_for(
                asyncio.to_thread(self._query_context_docs, user_query),
                timeout=settings.rag_vector_search_timeout_seconds,
            )
            return context_docs, None
        except asyncio.TimeoutError:
            error = f"向量检索超时（>{settings.rag_vector_search_timeout_seconds}s），已跳过知识库上下文"
            logger.warning(error)
            return [], error
        except Exception as exc:
            error = f"向量检索失败：{type(exc).__name__}: {exc}"
            logger.warning(error)
            return [], error

    @staticmethod
    def _build_rag_rerank_messages(user_query: str, context_docs: List[Any]) -> List[Dict[str, str]]:
        candidates: list[Dict[str, Any]] = []
        for index, item in enumerate(context_docs, start=1):
            if isinstance(item, dict):
                metadata = item.get("metadata") or {}
                candidates.append(
                    {
                        "rank": index,
                        "id": item.get("id") or str(index),
                        "product_id": metadata.get("product_id") or "",
                        "title": metadata.get("title") or "",
                        "brand": metadata.get("brand") or "",
                        "category": metadata.get("category") or "",
                        "sub_category": metadata.get("sub_category") or "",
                        "chunk_type": metadata.get("chunk_type") or "",
                        "content": str(item.get("content") or "")[:700],
                    }
                )
            else:
                candidates.append(
                    {
                        "rank": index,
                        "id": str(index),
                        "content": str(item)[:700],
                    }
                )

        return [
            {
                "role": "system",
                "content": (
                    "你是 RAG 检索质量评审器。请判断每个候选知识片段是否能帮助回答用户问题，"
                    "并按相关性从高到低排序。只输出 JSON，不要输出解释。"
                    "输出格式：{\"results\":[{\"id\":\"候选id\",\"score\":1-5,\"reason\":\"简短原因\"}]}。"
                    "id 必须原样复制候选中的 id 字段；如果不确定 id，可输出候选 rank。"
                    "score=5 表示直接命中，3 表示有一定帮助，1 表示基本无关。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"用户问题：{user_query}\n\n"
                    f"候选片段：\n{json.dumps(candidates, ensure_ascii=False)}"
                ),
            },
        ]

    @staticmethod
    def _parse_rag_rerank_response(raw_text: str) -> List[Dict[str, Any]]:
        text = (raw_text or "").strip()
        if not text:
            return []

        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text)
            text = re.sub(r"\s*```$", "", text)

        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", text, flags=re.S)
            if not match:
                return []
            try:
                payload = json.loads(match.group(0))
            except json.JSONDecodeError:
                return []

        raw_results = payload.get("results") if isinstance(payload, dict) else payload
        if not isinstance(raw_results, list):
            return []

        parsed: list[Dict[str, Any]] = []
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            source_id = str(item.get("id") or item.get("rank") or item.get("source_id") or item.get("product_id") or "").strip()
            if not source_id:
                continue
            try:
                score = int(float(item.get("score", 0)))
            except (TypeError, ValueError):
                score = 0
            parsed.append(
                {
                    "id": source_id,
                    "score": max(1, min(score, 5)),
                    "reason": str(item.get("reason") or "")[:160],
                }
            )

        return parsed

    async def _rerank_context_docs_with_llm(
        self,
        user_query: str,
        context_docs: List[Any],
        *,
        model: Optional[str] = None,
        model_config: Optional[Dict[str, Any]] = None,
    ) -> tuple[List[Any], Optional[str], Dict[str, Any]]:
        debug: Dict[str, Any] = {
            "query": user_query,
            "min_score": settings.rag_llm_rerank_min_score,
            "candidates": self._summarize_context_docs(context_docs),
            "raw_response": "",
            "parsed_results": [],
            "matched": [],
            "kept": [],
            "decision": "",
            "error": None,
        }
        if not settings.rag_llm_rerank_enabled or not context_docs:
            debug["decision"] = "skipped"
            return context_docs, None, debug
        if not any(isinstance(item, dict) for item in context_docs):
            debug["decision"] = "skipped_non_structured_docs"
            return context_docs, None, debug

        try:
            raw_text = await asyncio.wait_for(
                self.llm.chat(
                    self._build_rag_rerank_messages(user_query, context_docs),
                    temperature=0.0,
                    model=model,
                    model_config=model_config,
                ),
                timeout=settings.rag_llm_rerank_timeout_seconds,
            )
            debug["raw_response"] = raw_text
        except asyncio.TimeoutError:
            error = f"LLM RAG 检查超时（>{settings.rag_llm_rerank_timeout_seconds}s），已保留原向量排序"
            logger.warning(error)
            debug["decision"] = "timeout_keep_vector_order"
            debug["error"] = error
            debug["kept"] = self._summarize_context_docs(context_docs)
            return context_docs, error, debug
        except Exception as exc:
            error = f"LLM RAG 检查失败：{type(exc).__name__}: {exc}"
            logger.warning(error)
            debug["decision"] = "error_keep_vector_order"
            debug["error"] = error
            debug["kept"] = self._summarize_context_docs(context_docs)
            return context_docs, error, debug

        rerank_results = self._parse_rag_rerank_response(raw_text)
        debug["parsed_results"] = rerank_results
        if not rerank_results:
            error = "LLM RAG 检查结果无法解析，已保留原向量排序"
            logger.warning(error)
            debug["decision"] = "parse_failed_keep_vector_order"
            debug["error"] = error
            debug["kept"] = self._summarize_context_docs(context_docs)
            return context_docs, error, debug

        docs_by_id: Dict[str, Any] = {}
        for index, item in enumerate(context_docs, start=1):
            if not isinstance(item, dict):
                continue
            metadata = item.get("metadata") or {}
            aliases = [
                str(index),
                f"候选{index}",
                f"候选 {index}",
                f"知识片段{index}",
                f"知识片段 {index}",
                f"第{index}条",
                str(item.get("id") or ""),
                str(metadata.get("product_id") or ""),
                str(metadata.get("title") or ""),
            ]
            for alias in aliases:
                alias = alias.strip()
                if alias:
                    docs_by_id[alias] = item
        used_ids: set[str] = set()
        reranked_docs: list[Any] = []
        matched_docs: list[Any] = []

        for result in rerank_results:
            source_id = result["id"]
            doc = docs_by_id.get(source_id)
            if not doc:
                number_match = re.search(r"\d+", source_id)
                if number_match:
                    doc = docs_by_id.get(number_match.group(0))
            if not doc or source_id in used_ids:
                continue
            used_ids.add(source_id)
            doc = dict(doc)
            doc["llm_rerank"] = {
                "score": result["score"],
                "reason": result["reason"],
            }
            matched_docs.append(doc)
            metadata = doc.get("metadata") or {}
            debug["matched"].append(
                {
                    "requested_id": source_id,
                    "product_id": str(metadata.get("product_id") or ""),
                    "title": str(metadata.get("title") or ""),
                    "score": result["score"],
                    "reason": result["reason"],
                }
            )
            if result["score"] >= settings.rag_llm_rerank_min_score:
                reranked_docs.append(doc)

        if not reranked_docs and matched_docs:
            max_score = max(
                int((item.get("llm_rerank") or {}).get("score") or 0)
                for item in matched_docs
            )
            fallback_docs = [
                item for item in matched_docs
                if int((item.get("llm_rerank") or {}).get("score") or 0) == max_score
            ]
            error = "LLM RAG 检查未达到保留阈值，已保留评分最高片段"
            logger.warning(error)
            kept_docs = fallback_docs or context_docs
            debug["decision"] = "below_threshold_keep_highest_score"
            debug["error"] = error
            debug["kept"] = self._summarize_context_docs(kept_docs)
            return kept_docs, error, debug

        if not reranked_docs:
            error = "LLM RAG 检查未匹配到可保留片段，已保留原向量排序"
            logger.warning(error)
            debug["decision"] = "no_matched_docs_keep_vector_order"
            debug["error"] = error
            debug["kept"] = self._summarize_context_docs(context_docs)
            return context_docs, error, debug

        debug["decision"] = "kept_above_threshold"
        debug["kept"] = self._summarize_context_docs(reranked_docs)
        return reranked_docs, None, debug

    @staticmethod
    def _build_need_analysis_messages(
        conversation_history: Optional[List[Dict[str, str]]],
        user_query: str,
    ) -> List[Dict[str, str]]:
        history_context = ToolChatService._build_history_context(conversation_history, user_query)
        return [
            {
                "role": "system",
                "content": (
                    "你是导购助手的需求分析子角色。请只基于用户问题和历史对话，"
                    "用自然、简洁、像人在解释需求的方式，输出 1-3 句需求分析。"
                    "要求：1) 说明用户真正想解决什么；2) 说明你准备优先检索的方向；"
                    "3) 如果明显是场景/体验诉求，直接说成场景型购物需求；4) 不要列商品，不要写工具过程，不要编号；"
                    "5) 不要假设知识库或商品库已经命中了任何商品。\n\n"
                    f"{history_context}"
                ),
            },
            {
                "role": "user",
                "content": f"用户问题：{user_query}\n\n请给出需求分析。",
            },
        ]

    @staticmethod
    def _build_need_analysis_summary(user_query: str, conversation_history: Optional[List[Dict[str, str]]] = None) -> str:
        query = user_query.strip()

        scene_tags: list[str] = []
        if any(keyword in query for keyword in ["流畅", "更快", "更稳", "对战", "高手", "体验", "游戏", "高刷", "降噪", "续航", "轻薄", "学习", "办公"]):
            scene_tags.append("场景型需求")
        if any(keyword in query for keyword in ["手机", "平板", "笔记本", "耳机", "电脑", "数码", "电子"]):
            scene_tags.append("数码电子")
        if any(keyword in query for keyword in ["品牌", "苹果", "华为", "小米", "联想", "三星", "索尼", "飞利浦"]):
            scene_tags.append("品牌/型号约束")

        if not scene_tags:
            scene_tags.append("通用导购")

        history_hint = ""
        if conversation_history:
            last_user = next(
                (msg.get("content", "").strip() for msg in reversed(conversation_history) if msg.get("role") == "user" and msg.get("content")),
                "",
            )
            if last_user:
                history_hint = f"，结合上一轮问题'{last_user[:24]}'继续缩小范围"

        analysis_parts = [
            f"初步判断：这是一个{'、'.join(scene_tags)}问题",
            f"当前关键词：{query[:40] if query else '无'}",
        ]
        if "场景型需求" in scene_tags:
            analysis_parts.append("我会优先把它理解为提升体验的购物需求，先看最能解决场景问题的商品")
        else:
            analysis_parts.append("我会先找直接相关商品，如果没有再转向相邻品类")
        if history_hint:
            analysis_parts.append(history_hint)

        return "；".join(analysis_parts) + "。"

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
        limit: int = 5,
    ) -> List[Dict[str, Any]]:
        targets: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        for source, group in [("direct_query", direct_products), ("tool_query", tool_products)]:
            for item in group:
                product_id = str(item.get("product_id") or "").strip()
                if not product_id or product_id in seen_ids:
                    continue
                seen_ids.add(product_id)
                target = {
                    "rank": len(targets) + 1,
                    "product_id": product_id,
                    "title": str(item.get("title") or ""),
                    "brand": str(item.get("brand") or ""),
                    "category": str(item.get("category") or ""),
                    "sub_category": str(item.get("sub_category") or ""),
                    "base_price": item.get("base_price") or item.get("price"),
                    "source": source,
                    "recommendation_role": "primary" if not targets else "supporting",
                }
                targets.append(target)
                if len(targets) >= limit:
                    return targets

        return targets

    @staticmethod
    def _build_direct_product_query_text(user_query: str) -> str:
        query = user_query.strip()
        direct_keywords = [
            "眉笔",
            "口红",
            "粉底",
            "粉饼",
            "蜜粉",
            "眼霜",
            "面霜",
            "精华",
            "洁面",
            "手机",
            "平板",
            "笔记本",
            "电脑",
            "耳机",
            "零食",
            "牛奶",
            "饮料",
            "运动裤",
            "T恤",
        ]
        matched = [keyword for keyword in direct_keywords if keyword in query]
        return " ".join(matched) if matched else query

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

        lines = ["\n\n基于商品数据库命中的候选，我为你补充可核对的商品ID："]
        for index, item in enumerate(selected_products[:3], start=1):
            title = item.get("title") or "命中商品"
            product_id = item.get("product_id") or ""
            brand = item.get("brand") or ""
            sub_category = item.get("sub_category") or item.get("category") or ""
            price = item.get("base_price")
            details = [f"{title}（商品ID：{product_id}）"]
            if brand:
                details.append(f"品牌：{brand}")
            if sub_category:
                details.append(f"品类：{sub_category}")
            if price is not None:
                details.append(f"参考价：{price}")
            lines.append(f"{index}. " + "，".join(details))

        if "眉笔" in user_query:
            lines.append("这类细头、显色适中的眉笔更适合新手少量多次描画，出错后也更容易调整。")
        else:
            lines.append("建议优先从上面的商品ID里选择，方便和数据库查询结果保持一致。")

        return "\n".join(lines)

    @staticmethod
    def _build_selected_products_context(selected_products: List[Dict[str, Any]]) -> str:
        if not selected_products:
            return "未找到明确命中的商品候选。"

        return (
            "## 目标商品白名单（最终推荐必须且只能引用这些 product_id）\n"
            "```json\n"
            f"{json.dumps(selected_products, ensure_ascii=False, indent=2)}\n"
            "```"
        )

    @staticmethod
    def _build_final_recommendation_messages(
        system_prompt: str,
        analysis_text: str,
        selected_products: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        selected_context = ToolChatService._build_selected_products_context(selected_products)
        final_guidance = (
            "你现在进入最终导购推荐阶段。\n"
            "请基于前面的需求分析和目标商品白名单，输出面向用户的最终推荐。\n"
            "要求：1) 开头先用一句话确认用户需求；2) 必须明确输出目标商品ID，便于外部系统核对；"
            "3) 围绕目标商品给出推荐理由、适配场景和取舍建议；4) 不要重复长篇需求分析；"
            "5) 推荐要自然、像真人导购；6) 如果存在目标商品白名单，最终推荐必须且只能引用白名单里的 product_id，"
            "不要编造或改用白名单之外的商品ID；7) 商品事实以白名单 JSON 和工具结果为准。\n\n"
            f"需求分析摘要：\n{analysis_text or '（无）'}\n\n"
            f"{selected_context}"
        )
        return [
            {
                "role": "system",
                "content": system_prompt + "\n\n" + final_guidance,
            }
        ]
    @staticmethod
    def _parse_tool_arguments(arguments_text: str) -> Dict[str, Any]:
        try:
            return json.loads(arguments_text or "{}")
        except json.JSONDecodeError:
            return {}

    @staticmethod
    def _build_tool_planning_prompt(conversation_history: Optional[List[Dict[str, str]]], user_query: str) -> str:
        return (
            "你是导购助手的商品查询规划子角色。你的任务是把用户需求转成 query_products 工具查询，"
            "不要依赖知识库内容，也不要编造商品事实。\n\n"
            f"{ToolChatService._build_history_context(conversation_history, user_query)}\n\n"
            "## 商品查询策略（严格遵守）\n"
            "1. 先判断用户想解决的真实问题，再决定检索方向；不要只盯着用户字面上的词。\n"
            "2. 优先推荐直接相关商品；如果没有直接相商品，必须转向次相关商品或相邻品类，不要只说没有。\n"
            "3. 当用户是在描述目标、场景或体验诉求时，例如'我要成为XX高手'、'想提升对战体验'、'想要更流畅'，\n"
            "   要把需求理解为场景型购物需求，优先考虑数码电子类的手机、平板、笔记本、耳机等提升体验的商品。\n"
            "4. 如果直搜某个品牌、游戏或泛词没有结果，要主动改用场景词重搜，例如'适合玩<场景>的游戏电子产品'、\n"
            "   '提升<场景>体验的数码电子产品'、'高刷平板'、'游戏手机'、'降噪耳机'、'轻薄本'。\n"
            "5. 当前阶段优先调用工具查询商品库；只有用户问题明显不需要商品检索时，才直接回复。\n"
            "6. 如果工具返回结果，最终回复只能基于工具结果中的商品ID和商品信息。\n\n"
            "## 调用工具规则（严格遵守）\n"
            "1. 调用 query_products 时，必须提供有效的查询参数（text、keyword、brand 等），禁止传空参数 {}。\n"
            "2. 如果用户的问题引用了对话历史中的商品（如'这几个''上面的''那款''这个牌子'），"
            "   你必须从上方「最近对话中的商品信息」中提取品牌名、商品名等关键词作为 text 参数。\n"
            "3. 如果用户的需求是场景型或目标型，优先使用扩展后的场景关键词发起查询，而不是只搜原始名词。\n"
            "4. 如果第一次检索没有直接命中，不要结束对话，要立即转向次相关品类重新组织推荐。"
        )

    @staticmethod
    def _build_system_prompt(context_text: str, conversation_history: Optional[List[Dict[str, str]]], user_query: str) -> str:
        rag_section = context_text.strip() or "（知识库未命中或无可用上下文，请主要依据商品数据库工具结果回答。）"
        return (
            "你是一个资深导购型商品助手，负责整合商品数据库查询结果和可用知识库线索，"
            "输出面向用户的最终导购建议。\n\n"
            f"参考知识库内容：\n{rag_section}\n\n"
            f"{ToolChatService._build_history_context(conversation_history, user_query)}\n\n"
            "## 最终回复策略（严格遵守）\n"
            "1. 商品事实、商品ID、价格、品牌和品类优先以工具查询结果为准。\n"
            "2. 知识库内容只作为解释商品卖点、适配场景和补充背景的依据；如果知识库为空或无关，不要阻塞推荐。\n"
            "3. 如果 RAG 未命中但工具命中了商品，要正常基于商品数据库结果给出推荐。\n"
            "4. 如果工具和知识库信息冲突，以工具结果中的结构化商品信息为准。\n"
            "5. 最终回答要像导购：先一句话概括用户需求，再给出 3-5 个推荐方向或具体商品，并说明每个推荐为什么相关。\n"
            "6. 不要编造工具结果之外的商品ID。"
        )

    async def _run_tool_worker(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        tool_start = time.perf_counter()
        try:
            result = await asyncio.to_thread(run_tool, tool_name, arguments)
            elapsed = round(time.perf_counter() - tool_start, 3)
            return {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "result": result,
                "ok": result.get("ok") if isinstance(result, dict) else None,
                "total": result.get("total", 0) if isinstance(result, dict) else 0,
                "elapsed": elapsed,
                "error": None,
            }
        except Exception as exc:
            elapsed = round(time.perf_counter() - tool_start, 3)
            error_result = {
                "ok": False,
                "error": f"{type(exc).__name__}: {exc}",
                "total": 0,
                "items": [],
            }
            return {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "result": error_result,
                "ok": False,
                "total": 0,
                "elapsed": elapsed,
                "error": f"{type(exc).__name__}: {exc}",
            }

    async def chat_with_tools(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        max_tool_calls: int = 5,
        model: Optional[str] = None,
        model_config: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """使用原生 function calling 进行对话，返回 reply 和各环节耗时"""
        timings: Dict[str, Any] = {}
        t_total_start = time.perf_counter()

        logger.debug("═══ [chat_with_tools] 开始处理请求")

        logger.debug("  用户问题: %s", user_query)
        logger.debug("  历史消息数: %s", len(conversation_history) if conversation_history else 0)
        logger.debug("  最大工具调用轮数: %s", max_tool_calls)
        logger.debug("  使用模型: %s", model or (model_config or {}).get("id") or getattr(self.llm, "model", "default"))


        if not self.llm.connected and not (model_config or {}).get("api_key"):
            logger.warning("  LLM 服务未连接")
            return {
                "reply": "LLM 服务未连接，请检查 LLM_API_KEY 配置。",
                "timings": timings,
            }

        t0 = time.perf_counter()
        context_docs, vector_error = await self._query_context_docs_with_timeout(user_query)
        context_text = self._format_context_docs(context_docs)
        rag_sources = self._extract_rag_sources(context_docs)
        elapsed = round(time.perf_counter() - t0, 3)
        timings["vector_search"] = elapsed
        if vector_error:
            timings["vector_search_error"] = vector_error
        logger.debug("  [1] 向量检索完成 | 耗时: %ss", elapsed)
        logger.debug("      检索到 %s 条知识库文档", len(context_docs))
        if context_docs:
            for i, doc in enumerate(context_docs[:3]):
                preview = str(doc)[:100].replace('\n', ' ')
                logger.debug("      文档[%s]: %s...", i, preview)
        t_rerank = time.perf_counter()
        context_docs, rerank_error, _rerank_debug = await self._rerank_context_docs_with_llm(
            user_query,
            context_docs,
            model=model,
            model_config=model_config,
        )
        rerank_elapsed = round(time.perf_counter() - t_rerank, 3)
        timings["rag_rerank"] = rerank_elapsed
        if rerank_error:
            timings["rag_rerank_error"] = rerank_error
        if context_docs:
            logger.debug("  [1.5] LLM RAG 检查完成 | 耗时: %ss | 保留 %s 条", rerank_elapsed, len(context_docs))

        context_text = self._format_context_docs(context_docs)
        rag_sources = self._extract_rag_sources(context_docs)

        system_prompt = self._build_tool_planning_prompt(conversation_history, user_query)
        final_system_prompt = self._build_system_prompt(context_text, conversation_history, user_query)

        messages: list[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": user_query})
        logger.debug("  构建消息列表: %s 条 (含 system + 历史 + 当前问题)", len(messages))

        tools = [get_tool_spec()]

        llm_call_total = 0.0
        tool_call_total = 0.0
        llm_rounds = 0
        tool_rounds = 0
        consecutive_empty_params = 0

        for round_idx in range(max_tool_calls):
            logger.debug("  ── LLM 第 %s 轮调用 ──", round_idx + 1)
            logger.debug("      发送消息数: %s", len(messages))
            t1 = time.perf_counter()
            try:
                response = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    model=model,
                    model_config=model_config,
                )
            except Exception as e:
                elapsed = round(time.perf_counter() - t1, 3)
                logger.error("      LLM 调用异常 | 耗时: %ss | %s: %s", elapsed, type(e).__name__, e)
                timings["llm_calls"] = round(llm_call_total + elapsed, 3)
                timings["tool_calls"] = round(tool_call_total, 3)
                timings["total"] = round(time.perf_counter() - t_total_start, 3)
                return {
                    "reply": f"LLM 调用失败: {type(e).__name__}: {e}",
                    "timings": timings,
                }
            elapsed = round(time.perf_counter() - t1, 3)
            llm_call_total += time.perf_counter() - t1
            llm_rounds += 1

            assistant_message = response.choices[0].message

            usage = getattr(response, 'usage', None)
            usage_info = ""
            if usage:
                usage_info = f" | prompt_tokens={usage.prompt_tokens}, completion_tokens={usage.completion_tokens}"

            logger.debug("      LLM 响应完成 | 耗时: %ss%s", elapsed, usage_info)

            if assistant_message.content:
                content_preview = assistant_message.content[:500].replace('\n', '\n      │ ')
                logger.debug("      LLM 回复内容:")
                logger.debug("      │ %s", content_preview)
                if len(assistant_message.content) > 500:
                    logger.debug("      │ ... (共 %s 字符)", len(assistant_message.content))
            else:
                logger.debug("      LLM 回复内容: (空，仅工具调用)")

            assistant_payload: Dict[str, Any] = {
                "role": "assistant",
                "content": assistant_message.content,
            }
            if assistant_message.tool_calls:
                assistant_payload["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in assistant_message.tool_calls
                ]
            messages.append(assistant_payload)

            if not assistant_message.tool_calls:
                reply_preview = (assistant_message.content or "")[:200].replace('\n', ' ')
                logger.debug("      ✅ 无工具调用，直接返回文本")
                logger.debug("      回复预览: %s...", reply_preview)
                # LLM 已返回最终内容，无需再做额外调用
                timings["llm_calls"] = round(llm_call_total, 3)
                timings["llm_rounds"] = llm_rounds
                timings["tool_calls"] = round(tool_call_total, 3)
                timings["tool_rounds"] = tool_rounds
                timings["total"] = round(time.perf_counter() - t_total_start, 3)
                self._print_timings_summary(timings)
                return {"reply": assistant_message.content or "", "timings": timings, "rag_sources": rag_sources}

            logger.debug("      🔧 触发 %s 个工具调用:", len(assistant_message.tool_calls))
            t2 = time.perf_counter()
            round_has_empty = False
            for tc in assistant_message.tool_calls:
                tool_name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}

                logger.debug("         → 工具: %s", tool_name)
                logger.debug("           参数: %s", json.dumps(arguments, ensure_ascii=False)[:300])

                has_valid_param = any(arguments.get(k) for k in ["text", "keyword", "brand", "category", "sub_category", "attr_filters"])
                if not has_valid_param:
                    round_has_empty = True

                tool_start = time.perf_counter()
                result = run_tool(tool_name, arguments)
                tool_elapsed = round(time.perf_counter() - tool_start, 3)

                result_total = result.get("total", 0) if isinstance(result, dict) else 0
                result_ok = result.get("ok", None) if isinstance(result, dict) else None
                logger.debug("           结果: ok=%s, total=%s, 耗时=%ss", result_ok, result_total, tool_elapsed)

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            tool_call_total += time.perf_counter() - t2
            tool_rounds += 1

            if round_has_empty:
                consecutive_empty_params += 1
                logger.warning("      检测到空参数调用 (连续 %s 次)", consecutive_empty_params)
                if consecutive_empty_params >= 2:
                    logger.warning("      断路器触发：连续空参数，提前退出工具循环")
                    break
            else:
                consecutive_empty_params = 0

        logger.debug("  ── 工具调用轮数已耗尽，执行最终纯文本 LLM 调用 ──")
        logger.debug("      发送消息数: %s", len(messages))
        t3 = time.perf_counter()
        try:
            final_messages = [dict(message) for message in messages]
            if final_messages and final_messages[0].get("role") == "system":
                final_messages[0]["content"] = final_system_prompt
            final_response = await self.llm.chat_with_tools(
                messages=final_messages,
                tools=tools,
                tool_choice="none",
                model=model,
                model_config=model_config,
            )
            reply = final_response.choices[0].message.content or ""
            elapsed = round(time.perf_counter() - t3, 3)
            usage = getattr(final_response, 'usage', None)
            usage_info = ""
            if usage:
                usage_info = f" | prompt_tokens={usage.prompt_tokens}, completion_tokens={usage.completion_tokens}"
            logger.debug("      LLM 响应完成 | 耗时: %ss%s", elapsed, usage_info)
        except Exception as e:
            elapsed = round(time.perf_counter() - t3, 3)
            logger.error("      最终 LLM 调用异常 | 耗时: %ss | %s: %s", elapsed, type(e).__name__, e)
            reply = f"LLM 调用失败: {type(e).__name__}: {e}"
        llm_call_total += time.perf_counter() - t3
        llm_rounds += 1

        if reply:
            content_preview = reply[:500].replace('\n', '\n      │ ')
            logger.debug("      最终回复内容:")
            logger.debug("      │ %s", content_preview)
            if len(reply) > 500:
                logger.debug("      │ ... (共 %s 字符)", len(reply))

        timings["llm_calls"] = round(llm_call_total, 3)
        timings["llm_rounds"] = llm_rounds
        timings["tool_calls"] = round(tool_call_total, 3)
        timings["tool_rounds"] = tool_rounds
        timings["total"] = round(time.perf_counter() - t_total_start, 3)
        self._print_timings_summary(timings)
        return {
            "reply": reply,
            "timings": timings,
            "rag_sources": rag_sources,
        }

    async def chat_with_tools_stream(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        max_tool_calls: int = 5,
        model: Optional[str] = None,
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """使用原生 function calling 进行对话，流式返回结果"""
        timings: Dict[str, Any] = {}
        t_total_start = time.perf_counter()

        logger.debug("═══ [chat_with_tools_stream] 开始处理请求")
        logger.debug("  用户问题: %s", user_query)
        logger.debug("  历史消息数: %s", len(conversation_history) if conversation_history else 0)
        logger.debug("  最大工具调用轮数: %s", max_tool_calls)
        logger.debug("  使用模型: %s", model or (model_config or {}).get("id") or getattr(self.llm, "model", "default"))

        if not self.llm.connected and not (model_config or {}).get("api_key"):
            logger.warning("  LLM 服务未连接")
            yield {
                "type": "error",
                "content": "LLM 服务未连接，请检查 LLM_API_KEY 配置。",
                "timings": timings,
            }
            return

        async def _query_direct_selected_products_async() -> tuple[List[Dict[str, Any]], float, Optional[str]]:
            direct_start = time.perf_counter()
            try:
                products = await asyncio.to_thread(self._query_direct_selected_products, user_query)
                return products, round(time.perf_counter() - direct_start, 3), None
            except Exception as exc:
                error = f"原始需求直查失败：{type(exc).__name__}: {exc}"
                logger.warning(error)
                return [], round(time.perf_counter() - direct_start, 3), error

        direct_selected_products_task = asyncio.create_task(_query_direct_selected_products_async())

        tool_planning_prompt = self._build_tool_planning_prompt(conversation_history, user_query)
        messages: list[Dict[str, Any]] = [{"role": "system", "content": tool_planning_prompt}]
        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_query})
        tools = [get_tool_spec()]

        async def _run_need_analysis() -> tuple[str, float]:
            analysis_text = ""
            analysis_start = time.perf_counter()
            try:
                analysis_messages = self._build_need_analysis_messages(conversation_history, user_query)
                async for chunk in self.llm.chat_stream(analysis_messages, model=model, model_config=model_config):
                    if chunk:
                        analysis_text += chunk
            except Exception as exc:
                analysis_text = self._build_need_analysis_summary(user_query, conversation_history)
                logger.warning("      需求分析生成失败，已切换为简化分析: %s", type(exc).__name__)

            if not analysis_text.strip():
                analysis_text = self._build_need_analysis_summary(user_query, conversation_history)
                logger.debug("      分析结果为空，已使用简化分析兜底")
            return analysis_text, round(time.perf_counter() - analysis_start, 3)

        async def _run_first_tool_planning_call() -> tuple[Any, Optional[Exception], float, float]:
            call_start = time.perf_counter()
            try:
                response = await self.llm.chat_with_tools(
                    messages=[dict(message) for message in messages],
                    tools=tools,
                    tool_choice="auto",
                    model=model,
                    model_config=model_config,
                )
                duration = time.perf_counter() - call_start
                return response, None, round(duration, 3), duration
            except Exception as exc:
                duration = time.perf_counter() - call_start
                return None, exc, round(duration, 3), duration

        analysis_task = asyncio.create_task(_run_need_analysis())
        first_tool_planning_task = asyncio.create_task(_run_first_tool_planning_call())

        yield self._status_chunk("正在检索知识库", "retrieving_knowledge")
        t0 = time.perf_counter()
        context_docs, vector_error = await self._query_context_docs_with_timeout(user_query)
        context_text = self._format_context_docs(context_docs)
        rag_sources = self._extract_rag_sources(context_docs)
        elapsed = round(time.perf_counter() - t0, 3)
        timings["vector_search"] = elapsed
        if vector_error:
            timings["vector_search_error"] = vector_error
        logger.debug("  [1] 向量检索完成 | 耗时: %ss", elapsed)
        logger.debug("      检索到 %s 条知识库文档", len(context_docs))
        if context_docs:
            for i, doc in enumerate(context_docs[:3]):
                preview = str(doc)[:100].replace('\n', ' ')
                logger.debug("      文档[%s]: %s...", i, preview)
        yield self._debug_chunk(
            "vector_search",
            "向量检索结果",
            query=user_query,
            elapsed=elapsed,
            error=vector_error,
            candidates=self._summarize_context_docs(context_docs),
        )

        if context_docs and settings.rag_llm_rerank_enabled:
            yield self._status_chunk("正在校验知识库", "reranking_knowledge")
        t_rerank = time.perf_counter()
        context_docs, rerank_error, rerank_debug = await self._rerank_context_docs_with_llm(
            user_query,
            context_docs,
            model=model,
            model_config=model_config,
        )
        rerank_elapsed = round(time.perf_counter() - t_rerank, 3)
        timings["rag_rerank"] = rerank_elapsed
        if rerank_error:
            timings["rag_rerank_error"] = rerank_error
        if context_docs:
            logger.debug("  [1.5] LLM RAG 检查完成 | 耗时: %ss | 保留 %s 条", rerank_elapsed, len(context_docs))

        yield self._debug_chunk(
            "rag_rerank",
            "RAG LLM 检查明细",
            **rerank_debug,
            elapsed=rerank_elapsed,
        )

        context_text = self._format_context_docs(context_docs)
        rag_sources = self._extract_rag_sources(context_docs)
        if rag_sources:
            yield {
                "type": "rag_sources",
                "content": "已定位知识来源商品",
                "rag_sources": rag_sources,
                "timings": None,
            }

        logger.debug("      [2] 开始 LLM 需求分析流")
        yield self._status_chunk("正在分析需求", "need_analysis")

        analysis_text, analysis_elapsed = await analysis_task
        timings["analysis_calls"] = analysis_elapsed
        logger.debug("      分析耗时: %ss", analysis_elapsed)
        logger.debug("      分析摘要: %s", analysis_text[:200].replace("\n", " "))
        logger.debug("      分析完整内容:\n%s", analysis_text)
        yield {
            "type": "analysis",
            "content": analysis_text,
            "summary": analysis_text[:200].replace("\n", " "),
            "timings": {"analysis_calls": analysis_elapsed},
        }

        final_system_prompt = self._build_system_prompt(context_text, conversation_history, user_query)
        logger.debug("  构建消息列表: %s 条 (含 system + 历史 + 当前问题)", len(messages))

        llm_call_total = 0.0
        tool_call_total = 0.0
        llm_rounds = 0
        tool_rounds = 0
        consecutive_empty_params = 0

        tool_results: Dict[str, Dict[str, Any]] = {}
        tool_call_order: list[str] = []
        direct_selected_products, direct_query_elapsed, direct_query_error = await direct_selected_products_task
        yield self._debug_chunk(
            "direct_product_query",
            "原始需求直查 SQLite 商品库",
            query=self._build_direct_product_query_text(user_query),
            elapsed=direct_query_elapsed,
            error=direct_query_error,
            selected_product_ids=[item["product_id"] for item in direct_selected_products],
            selected_products=direct_selected_products,
        )

        for round_idx in range(max_tool_calls):
            logger.debug("  ── LLM 第 %s 轮调用 ──", round_idx + 1)
            logger.debug("      发送消息数: %s", len(messages))

            if round_idx == 0:
                response, call_error, elapsed, duration = await first_tool_planning_task
                if call_error:
                    logger.error("      LLM 调用异常 | 耗时: %ss | %s: %s", elapsed, type(call_error).__name__, call_error)
                    timings["llm_calls"] = round(llm_call_total + duration, 3)
                    timings["analysis_calls"] = timings.get("analysis_calls", 0)
                    timings["tool_calls"] = round(tool_call_total, 3)
                    timings["total"] = round(time.perf_counter() - t_total_start, 3)
                    yield {
                        "type": "error",
                        "content": f"LLM 调用失败: {type(call_error).__name__}: {call_error}",
                        "timings": timings,
                    }
                    return
                llm_call_total += duration
            else:
                t1 = time.perf_counter()
                try:
                    response = await self.llm.chat_with_tools(
                        messages=messages,
                        tools=tools,
                        tool_choice="auto",
                        model=model,
                        model_config=model_config,
                    )
                except Exception as e:
                    elapsed = round(time.perf_counter() - t1, 3)
                    logger.error("      LLM 调用异常 | 耗时: %ss | %s: %s", elapsed, type(e).__name__, e)
                    timings["llm_calls"] = round(llm_call_total + elapsed, 3)
                    timings["analysis_calls"] = timings.get("analysis_calls", 0)
                    timings["tool_calls"] = round(tool_call_total, 3)
                    timings["total"] = round(time.perf_counter() - t_total_start, 3)
                    yield {
                        "type": "error",
                        "content": f"LLM 调用失败: {type(e).__name__}: {e}",
                        "timings": timings,
                    }
                    return
                elapsed = round(time.perf_counter() - t1, 3)
                llm_call_total += time.perf_counter() - t1
            llm_rounds += 1

            if response is None:
                yield {
                    "type": "error",
                    "content": "LLM 调用失败: 未返回响应",
                    "timings": timings,
                }
                return

            assistant_message = response.choices[0].message

            usage = getattr(response, 'usage', None)
            usage_info = ""
            if usage:
                usage_info = f" | prompt_tokens={usage.prompt_tokens}, completion_tokens={usage.completion_tokens}"

            logger.debug("      LLM 响应完成 | 耗时: %ss%s", elapsed, usage_info)

            if assistant_message.content:
                content_preview = assistant_message.content[:500].replace('\n', '\n      │ ')
                logger.debug("      LLM 回复内容:")
                logger.debug("      │ %s", content_preview)
                if len(assistant_message.content) > 500:
                    logger.debug("      │ ... (共 %s 字符)", len(assistant_message.content))
            else:
                logger.debug("      LLM 回复内容: (空，仅工具调用)")

            assistant_payload: Dict[str, Any] = {
                "role": "assistant",
                "content": assistant_message.content,
            }
            if assistant_message.tool_calls:
                assistant_payload["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in assistant_message.tool_calls
                ]
            messages.append(assistant_payload)

            if assistant_message.tool_calls:
                planned_tool_calls = []
                for tc in assistant_message.tool_calls:
                    planned_tool_calls.append(
                        {
                            "tool_call_id": tc.id,
                            "tool_name": tc.function.name,
                            "arguments": self._parse_tool_arguments(tc.function.arguments or "{}"),
                        }
                    )
                yield self._debug_chunk(
                    "llm_tool_plan",
                    f"LLM 第 {round_idx + 1} 轮工具调用计划",
                    round=round_idx + 1,
                    tool_calls=planned_tool_calls,
                    assistant_content=assistant_message.content or "",
                )

            if not assistant_message.tool_calls:
                reply_preview = (assistant_message.content or "")[:200].replace('\n', ' ')
                logger.debug("      ✅ 无工具调用，开始流式返回文本")
                logger.debug("      回复预览: %s...", reply_preview)

                tool_selected_products = self._extract_selected_products(tool_results, tool_call_order)
                selected_products = self._build_target_products(direct_selected_products, tool_selected_products)
                selected_product_ids = [item["product_id"] for item in selected_products]
                yield self._debug_chunk(
                    "selected_products",
                    "最终目标商品合并结果",
                    direct_selected_product_ids=[item["product_id"] for item in direct_selected_products],
                    tool_selected_product_ids=[
                        item["product_id"]
                        for item in tool_selected_products
                    ],
                    selected_product_ids=selected_product_ids,
                    selected_products=selected_products,
                )
                if selected_product_ids:
                    yield {
                        "type": "selected_products",
                        "content": f"已选中商品ID：{', '.join(selected_product_ids)}",
                        "selected_product_ids": selected_product_ids,
                        "selected_products": selected_products,
                        "timings": None,
                    }

                yield self._status_chunk("正在整理结果", "organizing_results")

                timings["llm_calls"] = round(llm_call_total, 3)
                timings["llm_rounds"] = llm_rounds
                timings["analysis_calls"] = timings.get("analysis_calls", 0)
                timings["tool_calls"] = round(tool_call_total, 3)
                timings["tool_rounds"] = tool_rounds

                final_content = assistant_message.content or ""
                needs_constrained_final = bool(selected_product_ids)
                yield self._debug_chunk(
                    "organizing_results",
                    "最终回复整理检查",
                    mode="assistant_direct_reply",
                    selected_product_ids=selected_product_ids,
                    direct_reply_product_ids=re.findall(r"p_[a-z]+_\d+", final_content),
                    needs_constrained_final=needs_constrained_final,
                )

                if needs_constrained_final:
                    logger.debug("      存在目标商品，切换为受约束最终整理")
                    final_messages = []
                    for msg in messages:
                        if msg.get("role") == "system":
                            continue
                        entry: Dict[str, Any] = {"role": msg.get("role"), "content": msg.get("content")}
                        if msg.get("role") == "tool" and msg.get("tool_call_id"):
                            entry["tool_call_id"] = msg.get("tool_call_id")
                        final_messages.append(entry)
                    final_messages = self._build_final_recommendation_messages(
                        final_system_prompt,
                        analysis_text.strip(),
                        selected_products,
                    ) + final_messages

                    t_final = time.perf_counter()
                    generated_content = ""
                    try:
                        async for chunk in self.llm.chat_stream(final_messages, model=model, model_config=model_config):
                            generated_content += chunk
                            yield {"type": "content", "content": chunk, "timings": None}
                    except Exception as e:
                        elapsed = round(time.perf_counter() - t_final, 3)
                        logger.error("      受约束最终 LLM 调用异常 | 耗时: %ss | %s: %s", elapsed, type(e).__name__, e)
                        yield {
                            "type": "error",
                            "content": f"LLM 调用失败: {type(e).__name__}: {e}",
                            "timings": timings,
                        }
                        return
                    llm_call_total += time.perf_counter() - t_final
                    llm_rounds += 1
                    timings["llm_calls"] = round(llm_call_total, 3)
                    timings["llm_rounds"] = llm_rounds
                    if not any(product_id in generated_content for product_id in selected_product_ids):
                        fallback_reply = self._build_deterministic_final_reply(user_query, selected_products)
                        if fallback_reply:
                            yield self._debug_chunk(
                                "organizing_results",
                                "受约束回复仍未引用候选ID，追加确定性兜底",
                                selected_product_ids=selected_product_ids,
                                generated_product_ids=re.findall(r"p_[a-z]+_\d+", generated_content),
                            )
                            yield {"type": "content", "content": fallback_reply, "timings": None}
                elif final_content:
                    yield {"type": "content", "content": final_content, "timings": None}

                timings["total"] = round(time.perf_counter() - t_total_start, 3)
                self._print_timings_summary(timings)
                yield {
                    "type": "done",
                    "content": "",
                    "timings": timings,
                }
                return

            logger.debug("      🔧 触发 %s 个工具调用:", len(assistant_message.tool_calls))
            yield self._status_chunk(
                "正在查询商品",
                "querying_products",
                extra={"tool_calls": len(assistant_message.tool_calls)},
            )

            t2 = time.perf_counter()
            round_has_empty = False
            tool_tasks: list[asyncio.Task[Dict[str, Any]]] = []
            round_tool_call_order: list[str] = []
            tool_call_meta: Dict[str, Dict[str, Any]] = {}
            for tc in assistant_message.tool_calls:
                tool_name = tc.function.name
                arguments = self._parse_tool_arguments(tc.function.arguments or "{}")

                logger.debug("         → 工具: %s", tool_name)
                logger.debug("           参数: %s", json.dumps(arguments, ensure_ascii=False)[:300])

                has_valid_param = any(arguments.get(k) for k in ["text", "keyword", "brand", "category", "sub_category", "attr_filters"])
                if not has_valid_param:
                    round_has_empty = True

                tool_call_order.append(tc.id)
                round_tool_call_order.append(tc.id)
                tool_call_meta[tc.id] = {
                    "tool_name": tool_name,
                    "arguments": arguments,
                }
                yield self._status_chunk(
                    f"正在查询商品：{tool_name}",
                    "querying_products",
                    extra={"tool_call_id": tc.id, "tool_name": tool_name},
                )
                tool_tasks.append(asyncio.create_task(self._run_tool_worker(tc.id, tool_name, arguments)))

            for task in asyncio.as_completed(tool_tasks):
                outcome = await task
                tool_results[outcome["tool_call_id"]] = outcome
                result = outcome["result"]
                result_total = outcome.get("total", 0)
                result_ok = outcome.get("ok", None)
                logger.debug("           结果: ok=%s, total=%s, 耗时=%ss", result_ok, result_total, outcome.get("elapsed", 0))
                yield self._debug_chunk(
                    "tool_result",
                    f"工具结果：{outcome['tool_name']}",
                    tool_call_id=outcome["tool_call_id"],
                    tool_name=outcome["tool_name"],
                    arguments=outcome.get("arguments") or {},
                    ok=result_ok,
                    total=result_total,
                    elapsed=outcome.get("elapsed", 0),
                    error=outcome.get("error"),
                    parsed=(result or {}).get("parsed") if isinstance(result, dict) else None,
                    query_sql=(result or {}).get("query_sql") if isinstance(result, dict) else None,
                    product_ids=[
                        str(item.get("product_id") or item.get("id") or "")
                        for item in ((result or {}).get("items") or [])[:10]
                        if isinstance(item, dict)
                    ] if isinstance(result, dict) else [],
                    items=[
                        {
                            "product_id": str(item.get("product_id") or item.get("id") or ""),
                            "title": str(item.get("title") or item.get("name") or ""),
                            "brand": str(item.get("brand") or ""),
                            "category": str(item.get("category") or ""),
                            "sub_category": str(item.get("sub_category") or ""),
                            "base_price": item.get("base_price") or item.get("price"),
                        }
                        for item in ((result or {}).get("items") or [])[:10]
                        if isinstance(item, dict)
                    ] if isinstance(result, dict) else [],
                )
                if outcome.get("error"):
                    yield self._status_chunk(
                        f"查询失败：{outcome['tool_name']}",
                        "tool_error",
                        extra={
                            "tool_call_id": outcome["tool_call_id"],
                            "tool_name": outcome["tool_name"],
                            "error": outcome["error"],
                        },
                    )
                else:
                    yield self._status_chunk(
                        f"查询完成：{outcome['tool_name']}",
                        "tool_done",
                        extra={
                            "tool_call_id": outcome["tool_call_id"],
                            "tool_name": outcome["tool_name"],
                            "ok": result_ok,
                            "total": result_total,
                            "elapsed": outcome.get("elapsed", 0),
                        },
                    )

            for tool_call_id in round_tool_call_order:
                result = tool_results.get(tool_call_id, {}).get("result", {})
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            tool_call_total += time.perf_counter() - t2
            tool_rounds += 1

            if round_has_empty:
                consecutive_empty_params += 1
                logger.warning("      检测到空参数调用 (连续 %s 次)", consecutive_empty_params)
                if consecutive_empty_params >= 2:
                    logger.warning("      断路器触发：连续空参数，提前退出工具循环")
                    break
            else:
                consecutive_empty_params = 0

        tool_selected_products = self._extract_selected_products(tool_results, tool_call_order)
        selected_products = self._build_target_products(direct_selected_products, tool_selected_products)
        selected_product_ids = [item["product_id"] for item in selected_products]
        yield self._debug_chunk(
            "selected_products",
            "最终目标商品合并结果",
            direct_selected_product_ids=[item["product_id"] for item in direct_selected_products],
            tool_selected_product_ids=[
                item["product_id"]
                for item in tool_selected_products
            ],
            selected_product_ids=selected_product_ids,
            selected_products=selected_products,
        )
        if selected_product_ids:
            yield {
                "type": "selected_products",
                "content": f"已选中商品ID：{', '.join(selected_product_ids)}",
                "selected_product_ids": selected_product_ids,
                "selected_products": selected_products,
                "timings": None,
            }

        logger.debug("  ── 工具调用轮数已耗尽，执行最终流式 LLM 调用 ──")
        logger.debug("      发送消息数: %s", len(messages))
        yield self._status_chunk("正在整理结果", "organizing_results")
        t3 = time.perf_counter()

        timings["llm_calls"] = round(llm_call_total, 3)
        timings["llm_rounds"] = llm_rounds
        timings["analysis_calls"] = timings.get("analysis_calls", 0)
        timings["tool_calls"] = round(tool_call_total, 3)
        timings["tool_rounds"] = tool_rounds

        final_messages = []
        for msg in messages:
            if msg.get("role") == "system":
                continue
            entry: Dict[str, Any] = {"role": msg.get("role"), "content": msg.get("content")}
            # 保留 tool 消息的 tool_call_id，部分 LLM 提供方要求该字段
            if msg.get("role") == "tool" and msg.get("tool_call_id"):
                entry["tool_call_id"] = msg.get("tool_call_id")
            final_messages.append(entry)
        final_messages = self._build_final_recommendation_messages(final_system_prompt, analysis_text.strip(), selected_products) + final_messages
        yield self._debug_chunk(
            "organizing_results",
            "最终整理输入",
            selected_product_ids=selected_product_ids,
            selected_products=selected_products,
            final_message_count=len(final_messages),
        )

        try:
            generated_content = ""
            async for chunk in self.llm.chat_stream(final_messages, model=model, model_config=model_config):
                generated_content += chunk
                yield {
                    "type": "content",
                    "content": chunk,
                    "timings": None,
                }
            if selected_product_ids and not any(product_id in generated_content for product_id in selected_product_ids):
                fallback_reply = self._build_deterministic_final_reply(user_query, selected_products)
                if fallback_reply:
                    yield self._debug_chunk(
                        "organizing_results",
                        "最终回复未引用候选ID，追加确定性兜底",
                        selected_product_ids=selected_product_ids,
                        generated_product_ids=re.findall(r"p_[a-z]+_\d+", generated_content),
                    )
                    yield {
                        "type": "content",
                        "content": fallback_reply,
                        "timings": None,
                    }
            elapsed = round(time.perf_counter() - t3, 3)
            logger.debug("      LLM 流式响应完成 | 耗时: %ss", elapsed)
        except Exception as e:
            elapsed = round(time.perf_counter() - t3, 3)
            logger.error("      最终 LLM 调用异常 | 耗时: %ss | %s: %s", elapsed, type(e).__name__, e)
            yield {
                "type": "error",
                "content": f"LLM 调用失败: {type(e).__name__}: {e}",
                "timings": timings,
            }
            return

        llm_call_total += time.perf_counter() - t3
        llm_rounds += 1
        timings["llm_calls"] = round(llm_call_total, 3)
        timings["llm_rounds"] = llm_rounds
        timings["total"] = round(time.perf_counter() - t_total_start, 3)
        self._print_timings_summary(timings)

        yield {
            "type": "done",
            "content": "",
            "timings": timings,
        }

    @staticmethod
    def _print_timings_summary(timings: Dict[str, Any]):
        """打印耗时汇总"""
        logger.debug("  ────────────────────────────────────")
        logger.debug("  耗时汇总:")
        logger.debug("    分析: %ss", timings.get('analysis_calls', '-'))
        logger.debug("    向量检索: %ss", timings.get('vector_search', '-'))
        logger.debug("    RAG检查: %ss", timings.get('rag_rerank', '-'))
        logger.debug("    LLM推理: %ss (%s轮)", timings.get('llm_calls', '-'), timings.get('llm_rounds', '?'))
        logger.debug("    工具查询: %ss (%s轮)", timings.get('tool_calls', '-'), timings.get('tool_rounds', '?'))
        logger.debug("    总计:     %ss", timings.get('total', '-'))
        logger.debug("  ────────────────────────────────────")

    @staticmethod
    def _build_history_context(
        conversation_history: Optional[List[Dict[str, str]]],
        user_query: str
    ) -> str:
        """从对话历史和工具结果中提取商品信息，注入 system prompt 帮助 LLM 定位关键词"""
        if not conversation_history:
            return ""

        product_mentions: list[str] = []

        for msg in reversed(conversation_history):
            content = msg.get("content", "")
            if not content:
                continue

            product_ids = re.findall(r'[psc]_[a-z]+_\d+(?:_\d+)?', content)

            brands = re.findall(
                r'(华为|小米|苹果|三星|OPPO|vivo|荣耀|联想|戴尔|惠普|'
                r'农夫山泉|元气森林|东鹏|可口可乐|百事|蒙牛|伊利|'
                r'耐克|阿迪达斯|安踏|李宁|优衣库|'
                r'兰蔻|雅诗兰黛|欧莱雅|资生堂|完美日记|花西子|'
                r'索尼|飞利浦|美的|格力|海尔)',
                content
            )

            backtick_names = re.findall(r'`([^`]{2,50})`', content)

            for pid in product_ids:
                product_mentions.append(f"商品ID: {pid}")
            for brand in set(brands):
                product_mentions.append(f"品牌: {brand}")
            for name in backtick_names[:5]:
                product_mentions.append(f"名称: {name}")

            if len(product_mentions) >= 6:
                break

        if not product_mentions:
            return ""

        seen: set[str] = set()
        unique: list[str] = []
        for item in product_mentions:
            if item not in seen:
                seen.add(item)
                unique.append(item)

        context = "## 最近对话中的商品信息（供工具调用参考）\n"
        context += "\n".join(f"- {item}" for item in unique[:10])
        context += f"\n\n用户当前问题可能引用以上商品，请据此构造查询参数。"
        return context


tool_chat_service: Optional[ToolChatService] = None
