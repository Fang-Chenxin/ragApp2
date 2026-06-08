"""流式工具聊天最终回复阶段。"""
from __future__ import annotations

import re
import time
from typing import Any, AsyncGenerator, Dict, List

from .stream_context import _StreamPipelineContext
from config.logging_config import get_logger

logger = get_logger("service.tool_chat")


class ToolChatStreamFinalMixin:
    """目标商品合并、最终回复生成和完成事件输出。"""

    def _stream_selected_product_payloads(
        self,
        ctx: _StreamPipelineContext,
    ) -> tuple[List[Dict[str, Any]], list[str], list[Dict[str, Any]]]:
        """合并并校验目标商品，同时生成前端和日志可见的调试 payload。"""
        tool_selected_products = self._extract_selected_products(ctx.tool_results, ctx.tool_call_order)
        rag_selected_products = self._extract_products_from_rag_sources(ctx.rag_sources)
        selected_products = self._build_target_products(
            ctx.direct_selected_products,
            tool_selected_products,
            ctx.user_query,
            search_plan=ctx.search_plan,
            rag_products=rag_selected_products,
        )
        selected_product_ids = [item["product_id"] for item in selected_products]
        trace_chunk = self._debug_chunk(
            "selected_products",
            "最终目标商品合并结果",
            rag_selected_product_ids=[item["product_id"] for item in rag_selected_products],
            direct_selected_product_ids=[item["product_id"] for item in ctx.direct_selected_products],
            tool_selected_product_ids=[item["product_id"] for item in tool_selected_products],
            selected_product_ids=selected_product_ids,
            selected_products=selected_products,
            search_plan=ctx.search_plan,
        )
        self._log_trace_chunk(trace_chunk)
        payloads = [trace_chunk]
        if selected_product_ids:
            payloads.append(
                {
                    "type": "selected_products",
                    "content": "已选定目标商品",
                    "selected_product_ids": selected_product_ids,
                    "selected_products": selected_products,
                    "timings": None,
                }
            )
        self._log_target_products(ctx.user_query, selected_products, "before_final_reply")
        return selected_products, selected_product_ids, payloads

    def _stream_update_tool_timings(self, ctx: _StreamPipelineContext) -> None:
        """把上下文累计耗时同步到最终 timings 字段。"""
        ctx.timings["llm_calls"] = round(ctx.llm_call_total, 3)
        ctx.timings["llm_rounds"] = ctx.llm_rounds
        ctx.timings["analysis_calls"] = ctx.timings.get("analysis_calls", 0)
        ctx.timings["tool_calls"] = round(ctx.tool_call_total, 3)
        ctx.timings["tool_rounds"] = ctx.tool_rounds

    @staticmethod
    def _build_preliminary_reply(selected_products: List[Dict[str, Any]]) -> str:
        """目标商品已确定但最终 LLM 尚未出字时，先给用户一个可见正文。"""
        if not selected_products:
            return ""

        count = len(selected_products)
        has_direct = any(item.get("match_type") == "direct" for item in selected_products)
        if has_direct:
            return f"我先从商品库里筛到了 {count} 款比较贴合的选择，商品卡片先放上来，下面继续给你整理推荐理由和取舍建议。\n\n"
        return f"当前商品库里没有完全直接命中的款式，我先按相邻需求筛到了 {count} 款可参考选择，商品卡片先放上来，下面继续给你说明适合点和取舍。\n\n"

    async def _yield_preliminary_reply_before_products(
        self,
        selected_products: List[Dict[str, Any]],
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """先输出正文片段，再输出商品卡片，避免前端长时间只有卡片或空白。"""
        preliminary_reply = self._build_preliminary_reply(selected_products)
        if preliminary_reply:
            yield {"type": "content", "content": preliminary_reply, "timings": None}

    @staticmethod
    def _direct_reply_covers_products(
        reply_text: str,
        selected_products: List[Dict[str, Any]],
    ) -> bool:
        """检查 LLM 直接回复是否已覆盖所有 primary 目标商品。

        规则：
        - 只校验 role=primary 的商品（核心推荐），supporting/fallback 允许不提及
        - 从标题中提取品牌名和关键词（≥2字的中文词或英文单词），命中任一即可
        - 所有 primary 商品都被覆盖时返回 True
        """
        if not reply_text or not selected_products:
            return False

        primary_products = [
            p for p in selected_products
            if p.get("recommendation_role") == "primary"
        ]
        if not primary_products:
            return False

        reply_lower = reply_text.lower()

        for product in primary_products:
            title = product.get("title", "")
            brand = product.get("brand", "")
            # 用品牌名或标题关键词匹配
            keywords = []
            if brand:
                keywords.append(brand)
            # 从标题提取 ≥2字的中文词 和 英文词
            for token in re.findall(r'[\u4e00-\u9fff]{2,}|[a-zA-Z][a-zA-Z0-9]+', title):
                if len(token) >= 2:
                    keywords.append(token)
            if not keywords:
                continue
            if any(kw.lower() in reply_lower for kw in keywords):
                continue
            # 这个 primary 商品未被覆盖
            logger.debug(
                "      直接回复未覆盖 primary 商品: product_id=%s | 品牌=%s | 关键词=%s",
                product.get("product_id"), brand, keywords[:5],
            )
            return False
        return True

    async def _stream_stage_direct_reply_finalization(
        self,
        ctx: _StreamPipelineContext,
        assistant_message: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """处理 LLM 没有继续调用工具、直接给出文本回复的情况。"""
        reply_preview = (assistant_message.content or "")[:200].replace('\n', ' ')
        logger.debug("      ✅ 无工具调用，开始流式返回文本")
        logger.debug("      回复预览: %s...", reply_preview)

        selected_products, selected_product_ids, payloads = self._stream_selected_product_payloads(ctx)
        async for event in self._yield_preliminary_reply_before_products(selected_products):
            yield event
        for payload in payloads:
            yield payload
        yield self._status_chunk("正在整理结果", "organizing_results")

        self._stream_update_tool_timings(ctx)

        final_content = assistant_message.content or ""
        reply_covers_targets = self._direct_reply_covers_products(final_content, selected_products)
        needs_constrained_final = bool(selected_product_ids) and not reply_covers_targets
        trace_chunk = self._debug_chunk(
            "organizing_results",
            "最终回复整理检查",
            mode="assistant_direct_reply",
            selected_product_ids=selected_product_ids,
            needs_constrained_final=needs_constrained_final,
            reply_covers_targets=reply_covers_targets,
        )
        self._log_trace_chunk(trace_chunk)
        yield trace_chunk

        if needs_constrained_final:
            # LLM 直接回复未覆盖全部 primary 目标商品，需要再生成一次受清单约束的最终回复。
            logger.debug("      存在目标商品，直接回复未完全覆盖，基于数据库目标清单流式生成最终回复")
            final_messages = self._build_final_recommendation_messages(
                ctx.final_system_prompt,
                ctx.analysis_text.strip(),
                selected_products,
                ctx.user_query,
                ctx.conversation_history,
            )

            t_final = time.perf_counter()
            generated_content = ""
            final_chunk_count = 0
            first_final_chunk_logged = False
            try:
                async for chunk in self.llm.chat_stream(final_messages, model=ctx.model, model_config=ctx.model_config):
                    generated_content += chunk
                    final_chunk_count += 1
                    if chunk and not first_final_chunk_logged:
                        logger.info(
                            "[FinalStream] query=%s | mode=constrained_direct | first_delta_after=%ss | chunk_len=%s",
                            ctx.user_query,
                            round(time.perf_counter() - t_final, 3),
                            len(chunk),
                        )
                        first_final_chunk_logged = True
                    sanitized_chunk = self._sanitize_user_reply_chunk(chunk)
                    if sanitized_chunk:
                        yield {"type": "content", "content": sanitized_chunk, "timings": None}
            except Exception as e:
                elapsed = round(time.perf_counter() - t_final, 3)
                logger.error("      受约束最终 LLM 调用异常 | 耗时: %ss | %s: %s", elapsed, type(e).__name__, e)
                yield {
                    "type": "error",
                    "content": f"LLM 调用失败: {type(e).__name__}: {e}",
                    "timings": ctx.timings,
                }
                ctx.completed = True
                return
            ctx.llm_call_total += time.perf_counter() - t_final
            logger.info(
                "[FinalStream] query=%s | mode=constrained_direct | chunks=%s | elapsed=%ss | generated_len=%s",
                ctx.user_query,
                final_chunk_count,
                round(time.perf_counter() - t_final, 3),
                len(generated_content),
            )
            ctx.llm_rounds += 1
            ctx.timings["llm_calls"] = round(ctx.llm_call_total, 3)
            ctx.timings["llm_rounds"] = ctx.llm_rounds
            if not generated_content.strip():
                # 模型流式返回空内容时，用确定性模板保证前端能收到可读推荐。
                fallback_reply = self._build_deterministic_final_reply(ctx.user_query, selected_products)
                if fallback_reply:
                    yield self._debug_chunk(
                        "organizing_results",
                        "受约束回复为空，改用确定性兜底",
                        selected_product_ids=selected_product_ids,
                    )
                    yield {"type": "content", "content": self._sanitize_user_reply(fallback_reply), "timings": None}
        elif final_content:
            if reply_covers_targets:
                logger.debug("      ✅ 直接回复已覆盖 primary 目标商品，跳过受约束重新生成")
            yield {"type": "content", "content": self._sanitize_user_reply(final_content), "timings": None}

        ctx.timings["total"] = round(time.perf_counter() - ctx.t_total_start, 3)
        self._print_timings_summary(ctx.timings)
        yield {"type": "done", "content": "", "timings": ctx.timings}
        ctx.completed = True

    async def _stream_stage_final_reply(
        self,
        ctx: _StreamPipelineContext,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """工具循环结束后的标准最终回复阶段。"""
        selected_products, selected_product_ids, payloads = self._stream_selected_product_payloads(ctx)
        async for event in self._yield_preliminary_reply_before_products(selected_products):
            yield event
        for payload in payloads:
            yield payload

        logger.debug("  ── 工具调用轮数已耗尽，执行最终流式 LLM 调用 ──")
        logger.debug("      发送消息数: %s", len(ctx.messages))
        yield self._status_chunk("正在整理结果", "organizing_results")
        t3 = time.perf_counter()
        self._stream_update_tool_timings(ctx)

        final_messages = self._build_final_recommendation_messages(
            ctx.final_system_prompt,
            ctx.analysis_text.strip(),
            selected_products,
            ctx.user_query,
            ctx.conversation_history,
        )
        trace_chunk = self._debug_chunk(
            "organizing_results",
            "最终整理输入",
            selected_product_ids=selected_product_ids,
            selected_products=selected_products,
            final_message_count=len(final_messages),
        )
        self._log_trace_chunk(trace_chunk)
        yield trace_chunk

        try:
            generated_content = ""
            final_chunk_count = 0
            first_final_chunk_logged = False
            async for chunk in self.llm.chat_stream(final_messages, model=ctx.model, model_config=ctx.model_config):
                generated_content += chunk
                final_chunk_count += 1
                if chunk and not first_final_chunk_logged:
                    logger.info(
                        "[FinalStream] query=%s | mode=final | first_delta_after=%ss | chunk_len=%s",
                        ctx.user_query,
                        round(time.perf_counter() - t3, 3),
                        len(chunk),
                    )
                    first_final_chunk_logged = True
                sanitized_chunk = self._sanitize_user_reply_chunk(chunk)
                if sanitized_chunk:
                    yield {"type": "content", "content": sanitized_chunk, "timings": None}
            if not generated_content.strip():
                # 最终兜底保证 `done` 前至少有机会输出基于数据库商品的确定性文本。
                fallback_reply = self._build_deterministic_final_reply(ctx.user_query, selected_products)
                if fallback_reply:
                    yield self._debug_chunk(
                        "organizing_results",
                        "最终回复为空，改用确定性兜底",
                        selected_product_ids=selected_product_ids,
                    )
                    yield {"type": "content", "content": self._sanitize_user_reply(fallback_reply), "timings": None}
            elapsed = round(time.perf_counter() - t3, 3)
            logger.debug("      LLM 流式响应完成 | 耗时: %ss", elapsed)
            logger.info(
                "[FinalStream] query=%s | mode=final | chunks=%s | elapsed=%ss | generated_len=%s",
                ctx.user_query,
                final_chunk_count,
                elapsed,
                len(generated_content),
            )
        except Exception as e:
            elapsed = round(time.perf_counter() - t3, 3)
            logger.error("      最终 LLM 调用异常 | 耗时: %ss | %s: %s", elapsed, type(e).__name__, e)
            yield {
                "type": "error",
                "content": f"LLM 调用失败: {type(e).__name__}: {e}",
                "timings": ctx.timings,
            }
            ctx.completed = True
            return

        ctx.llm_call_total += time.perf_counter() - t3
        ctx.llm_rounds += 1
        ctx.timings["llm_calls"] = round(ctx.llm_call_total, 3)
        ctx.timings["llm_rounds"] = ctx.llm_rounds
        ctx.timings["total"] = round(time.perf_counter() - ctx.t_total_start, 3)
        self._print_timings_summary(ctx.timings)
        yield {"type": "done", "content": "", "timings": ctx.timings}
        ctx.completed = True
