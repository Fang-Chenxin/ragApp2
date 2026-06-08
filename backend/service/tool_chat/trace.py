"""工具聊天调试事件与 RAG 上下文格式化辅助。"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from config.logging_config import get_logger
from config.settings import settings

logger = get_logger("service.tool_chat")


class ToolChatTraceMixin:
    """调试事件、状态事件和上下文摘要格式化能力。"""

    @staticmethod
    def _status_chunk(
        content: str,
        phase: str,
        *,
        agent: str = "shopping_agent",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """生成给前端展示进度的 status 事件。"""
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
        """把 RAG 片段转成最终 prompt 可读的上下文文本。"""
        lines: list[str] = []
        for index, item in enumerate(context_docs, start=1):
            if not isinstance(item, dict):
                lines.append(str(item))
                continue

            metadata = item.get("metadata") or {}
            source_parts = []
            for key, label in [
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
        """从结构化 RAG 片段中抽取前端可展示的来源商品。"""
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
        """生成日志/debug 事件中的 RAG 候选摘要，避免直接塞入过长片段。"""
        summaries: list[Dict[str, Any]] = []
        content_limit = max(80, int(settings.rag_trace_content_chars or 800))
        for index, item in enumerate(context_docs, start=1):
            if not isinstance(item, dict):
                content = str(item)
                summaries.append(
                    {
                        "rank": index,
                        "id": str(index),
                        "preview": content[:160],
                        "content": content[:content_limit],
                    }
                )
                continue
            metadata = item.get("metadata") or {}
            content = str(item.get("content") or "")
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
                    "preview": content[:160],
                    "content": content[:content_limit],
                    "llm_rerank": item.get("llm_rerank"),
                }
            )
        return summaries

    @staticmethod
    def _debug_chunk(phase: str, title: str, **extra: Any) -> Dict[str, Any]:
        """生成内部调试事件，API 层通常不展示正文但日志会完整记录。"""
        payload: Dict[str, Any] = {
            "type": "debug",
            "phase": phase,
            "content": title,
            "title": title,
            "timings": None,
        }
        payload.update(extra)
        return payload

    @staticmethod
    def _format_trace_item(prefix: str, item: Dict[str, Any]) -> str:
        """格式化单条商品/知识片段摘要日志。"""
        match_type = item.get("match_type")
        match_part = f" match={match_type}" if match_type else ""
        return (
            f"      {prefix} id={item.get('product_id') or item.get('id')} "
            f"title={item.get('title')} cat={item.get('category')}/"
            f"{item.get('sub_category')} "
            f"score={(item.get('llm_rerank') or {}).get('score', '-')} "
            f"dist={item.get('distance')}"
            f"{match_part}"
        )

    @staticmethod
    def _format_trace_content(item: Dict[str, Any]) -> str:
        """格式化知识片段原文预览日志。"""
        content = str(item.get("content") or item.get("preview") or "").strip()
        if not content:
            return ""
        return f"        原文: {content.replace(chr(10), ' ')}"

    @staticmethod
    def _format_rag_decision(decision: str) -> str:
        """把内部 rerank 决策码翻译成日志可读文本。"""
        decisions = {
            "skipped": "跳过（未启用LLM核验或无候选文档）",
            "skipped_single_candidate": "跳过（只有1条候选，直接使用向量结果）",
            "skipped_non_structured_docs": "跳过（候选文档非结构化）",
            "timeout_keep_vector_order": "LLM核验超时，保留原始向量排序",
            "error_keep_vector_order": "LLM核验出错，保留原始向量排序",
            "parse_failed_keep_vector_order": "LLM返回内容无法解析，保留原始排序",
            "below_threshold_drop_all": "所有片段未达阈值，已丢弃RAG上下文",
            "below_threshold_keep_highest_score": "所有片段未达阈值，保留评分最高的片段",
            "no_matched_docs_keep_vector_order": "LLM未匹配到任何片段，保留原始排序",
            "kept_above_threshold": "通过核验，保留了达到阈值的片段",
        }
        return decisions.get(decision, decision)

    @classmethod
    def _log_trace_chunk(cls, chunk: Dict[str, Any]) -> None:
        """按 debug chunk 的 phase 输出结构化可读日志。"""
        if not logger.isEnabledFor(10):
            return

        phase = chunk.get("phase", "")
        lines = ["", "─" * 72, f"  {chunk.get('title') or chunk.get('content') or phase}", "─" * 72]

        if phase == "vector_search":
            lines.append(f"  耗时: {chunk.get('elapsed', 0)}s")
            if chunk.get("error"):
                lines.append(f"  警告: {chunk.get('error')}")
            candidates = chunk.get("candidates") or []
            lines.append(f"  检索到 {len(candidates)} 条知识片段候选:")
            for item in candidates[:8]:
                lines.append(cls._format_trace_item("候选", item))
                content_line = cls._format_trace_content(item)
                if content_line:
                    lines.append(content_line)

        elif phase == "rag_rerank":
            lines.append(f"  耗时: {chunk.get('elapsed', 0)}s  阈值: >= {chunk.get('min_score')}分")
            lines.append(f"  核验结论: {cls._format_rag_decision(str(chunk.get('decision') or ''))}")
            if chunk.get("error"):
                lines.append(f"  原因: {chunk.get('error')}")
            parsed = chunk.get("parsed_results") or []
            if parsed:
                lines.append("  LLM评分明细:")
                for item in parsed:
                    lines.append(f"    id={item.get('id')} score={item.get('score')} reason={item.get('reason')}")
            kept = chunk.get("kept") or []
            lines.append(f"  最终保留 {len(kept)} 条知识片段:")
            for item in kept[:8]:
                lines.append(cls._format_trace_item("保留", item))
                content_line = cls._format_trace_content(item)
                if content_line:
                    lines.append(content_line)

        elif phase == "direct_product_query":
            lines.append(f"  后台召回耗时: {chunk.get('elapsed', 0)}s")
            if chunk.get("error"):
                lines.append(f"  警告: {chunk.get('error')}")
            lines.append(f"  召回关键词: {chunk.get('query', '')}")
            lines.append(f"  召回候选: {len(chunk.get('selected_product_ids') or [])} 个商品 -> {chunk.get('selected_product_ids') or []}")
            lines.extend(cls._format_trace_item("召回结果", item) for item in (chunk.get("selected_products") or [])[:8])

        elif phase == "search_plan":
            lines.append(f"  计划耗时: {chunk.get('elapsed', 0)}s")
            if chunk.get("error"):
                lines.append(f"  警告: {chunk.get('error')}")
            plan = chunk.get("search_plan") or {}
            if plan:
                lines.append(f"  目标商品: {plan.get('target_product')}")
                lines.append(f"  主检索词: {plan.get('query_text')}")
                lines.append(f"  直接命中词: {plan.get('direct_terms') or []}")
                lines.append(f"  可接受替代: {plan.get('acceptable_fallback_terms') or []}")
                lines.append(f"  允许类目: {plan.get('allowed_categories') or []}")
                lines.append(f"  禁止类目: {plan.get('forbidden_categories') or []}")

        elif phase == "tool_result":
            status = "成功" if chunk.get("ok") else f"失败: {chunk.get('error')}"
            lines.append(
                f"  工具: {chunk.get('tool_name')} 耗时: {chunk.get('elapsed', 0)}s "
                f"结果: {status} 命中: {chunk.get('total', 0)}条"
            )
            if chunk.get("original_arguments") is not None and chunk.get("original_arguments") != chunk.get("arguments"):
                lines.append(f"  原始参数: {chunk.get('original_arguments')}")
                lines.append(f"  执行参数: {chunk.get('arguments')}")
            if chunk.get("argument_enrichment_reasons"):
                lines.append(f"  参数补全原因: {'；'.join(chunk.get('argument_enrichment_reasons') or [])}")
            if chunk.get("parsed"):
                lines.append(f"  工具解析: {chunk.get('parsed')}")
            product_ids = chunk.get("product_ids") or []
            if product_ids:
                lines.append(f"  返回商品ID: {product_ids}")
            lines.extend(cls._format_trace_item("查询结果", item) for item in (chunk.get("items") or [])[:8])

        elif phase == "llm_tool_plan":
            lines.append(f"  LLM第{chunk.get('round', '?')}轮工具调用计划:")
            assistant_content = str(chunk.get("assistant_content") or "").strip()
            if assistant_content:
                lines.append(f"    LLM内容: {assistant_content[:200]}")
            for call in chunk.get("tool_calls") or []:
                if call.get("original_arguments") != call.get("arguments"):
                    lines.append(f"    原始调用 -> {call.get('tool_name')}({call.get('original_arguments')})")
                    lines.append(f"    执行调用 -> {call.get('tool_name')}({call.get('arguments')})")
                else:
                    lines.append(f"    调用 -> {call.get('tool_name')}({call.get('arguments')})")
                if call.get("argument_enrichment_reasons"):
                    lines.append(f"      参数补全原因: {'；'.join(call.get('argument_enrichment_reasons') or [])}")
                if call.get("search_plan_reason"):
                    lines.append(f"      SearchPlan理由: {call.get('search_plan_reason')}")

        elif phase == "selected_products":
            lines.append(f"  RAG候选: {chunk.get('rag_selected_product_ids') or '(无)'}")
            lines.append(f"  原始召回候选: {chunk.get('direct_selected_product_ids') or '(无)'}")
            lines.append(f"  工具召回候选: {chunk.get('tool_selected_product_ids') or '(无)'}")
            lines.append(f"  最终目标商品: {chunk.get('selected_product_ids') or []}")
            lines.extend(cls._format_trace_item("合并结果", item) for item in (chunk.get("selected_products") or [])[:8])

        elif phase == "organizing_results":
            lines.append(f"  回复模式: {chunk.get('mode') or 'final'}")
            lines.append(f"  目标商品: {chunk.get('selected_product_ids') or []}")
            if chunk.get("final_message_count") is not None:
                lines.append(f"  最终消息数: {chunk.get('final_message_count')}")

        else:
            lines.append(json.dumps({k: v for k, v in chunk.items() if k != "timings"}, ensure_ascii=False, default=str)[:1000])

        logger.debug("\n".join(lines))


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
