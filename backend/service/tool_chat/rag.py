"""工具聊天 RAG 检索与 LLM 核验辅助。"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any, Dict, List, Optional

from config.logging_config import get_logger
from config.settings import settings

logger = get_logger("service.tool_chat")


class ToolChatRagMixin:
    """知识库检索、上下文构造和 RAG rerank 能力。"""

    def _query_context_docs(self, user_query: str) -> List[Any]:
        """兼容新旧向量库接口，优先返回带来源 metadata 的结构化片段。"""
        if hasattr(self.vector_store, "query_with_sources"):
            return self.vector_store.query_with_sources(user_query)
        return self.vector_store.query(user_query)

    async def _query_context_docs_with_timeout(self, user_query: str) -> tuple[List[Any], Optional[str]]:
        """在线程中执行向量检索，并用配置超时保护聊天主流程。"""
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
        """构造 RAG 候选片段评分 prompt，要求 LLM 只返回 JSON。"""
        candidates: list[Dict[str, Any]] = []
        max_candidates = max(1, int(settings.rag_llm_rerank_max_candidates or len(context_docs)))
        for index, item in enumerate(context_docs[:max_candidates], start=1):
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
                        "content": str(item.get("content") or "")[:500],
                    }
                )
            else:
                candidates.append(
                    {
                        "rank": index,
                        "id": str(index),
                        "content": str(item)[:500],
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
        """解析 LLM rerank 返回的 JSON，容忍 markdown code fence 和额外文本。"""
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
        """用 LLM 检查 RAG 片段相关性，并返回过滤后的片段与调试信息。"""
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
            # 未开启或无候选时直接沿用原结果。
            debug["decision"] = "skipped"
            return context_docs, None, debug
        if settings.rag_llm_rerank_skip_single_candidate and len(context_docs) <= 1:
            debug["decision"] = "skipped_single_candidate"
            debug["kept"] = self._summarize_context_docs(context_docs)
            return context_docs, None, debug
        if not any(isinstance(item, dict) for item in context_docs):
            debug["decision"] = "skipped_non_structured_docs"
            return context_docs, None, debug

        rerank_docs = context_docs[:max(1, int(settings.rag_llm_rerank_max_candidates or len(context_docs)))]
        tail_docs = context_docs[len(rerank_docs):]

        try:
            raw_text = await asyncio.wait_for(
                self.llm.chat(
                    self._build_rag_rerank_messages(user_query, rerank_docs),
                    temperature=0.0,
                    model=model,
                    model_config=model_config,
                    max_tokens=settings.rag_llm_rerank_max_tokens,
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
        for index, item in enumerate(rerank_docs, start=1):
            # LLM 可能返回候选序号、片段 id、商品 id 或标题，这里统一建立别名索引。
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

        for matched in debug["matched"]:
            logger.info(
                "[RAGRerankScore] query=%s | requested_id=%s | product_id=%s | title=%s | score=%s | min_score=%s | kept=%s | reason=%s",
                user_query,
                matched.get("requested_id", ""),
                matched.get("product_id", ""),
                matched.get("title", ""),
                matched.get("score", ""),
                settings.rag_llm_rerank_min_score,
                matched.get("score", 0) >= settings.rag_llm_rerank_min_score,
                matched.get("reason", ""),
            )

        if not reranked_docs and matched_docs:
            # 有匹配但都低于阈值，说明知识库上下文可能会误导最终回复，直接丢弃。
            error = "LLM RAG 检查未达到保留阈值，已丢弃知识库上下文"
            logger.warning(error)
            debug["decision"] = "below_threshold_drop_all"
            debug["error"] = error
            debug["kept"] = []
            return [], error, debug

        if not reranked_docs:
            error = "LLM RAG 检查未匹配到可保留片段，已保留原向量排序"
            logger.warning(error)
            debug["decision"] = "no_matched_docs_keep_vector_order"
            debug["error"] = error
            debug["kept"] = self._summarize_context_docs(context_docs)
            return context_docs, error, debug

        debug["decision"] = "kept_above_threshold"
        # 未送入 rerank 的尾部片段保持原向量顺序追加，避免 max_candidates 截断所有上下文。
        final_docs = reranked_docs + tail_docs
        debug["kept"] = self._summarize_context_docs(final_docs)
        return final_docs, None, debug
