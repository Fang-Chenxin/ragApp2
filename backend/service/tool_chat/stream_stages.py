"""流式工具聊天基础阶段。"""
from __future__ import annotations

import asyncio
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from .stream_context import _StreamPipelineContext
from config.logging_config import get_logger
from config.settings import settings

logger = get_logger("service.tool_chat")


class ToolChatStreamStagesMixin:
    """需求分析、RAG、商品直查和阶段收尾。"""

    async def _stream_query_direct_selected_products(
        self,
        ctx: _StreamPipelineContext,
    ) -> tuple[List[Dict[str, Any]], float, Optional[str]]:
        """用原始用户问题直接查一次商品库，给最终目标商品提供兜底候选。"""
        direct_start = time.perf_counter()
        try:
            products = await asyncio.to_thread(self._query_direct_selected_products, ctx.user_query)
            return products, round(time.perf_counter() - direct_start, 3), None
        except Exception as exc:
            error = f"原始需求直查失败：{type(exc).__name__}: {exc}"
            logger.warning(error)
            return [], round(time.perf_counter() - direct_start, 3), error

    async def _stream_run_need_analysis(self, ctx: _StreamPipelineContext) -> tuple[str, float]:
        """流式生成需求分析，并把增量片段写入上下文队列供其他阶段穿插输出。"""
        analysis_text = ""
        analysis_start = time.perf_counter()
        first_delta_logged = False
        try:
            analysis_messages = self._build_need_analysis_messages(ctx.conversation_history, ctx.user_query)
            async for chunk in self.llm.chat_stream(analysis_messages, model=ctx.model, model_config=ctx.model_config):
                if chunk:
                    if not first_delta_logged:
                        logger.info(
                            "[AnalysisStream] query=%s | first_delta_after=%ss | chunk_len=%s",
                            ctx.user_query,
                            round(time.perf_counter() - analysis_start, 3),
                            len(chunk),
                        )
                        first_delta_logged = True
                    analysis_text += chunk
                    await ctx.analysis_queue.put({"type": "analysis_delta", "content": chunk})
        except Exception as exc:
            # 需求分析只影响展示和最终 prompt，不应阻塞导购主流程。
            analysis_text = self._build_need_analysis_summary(ctx.user_query, ctx.conversation_history)
            await ctx.analysis_queue.put({"type": "analysis_delta", "content": analysis_text})
            logger.warning("      需求分析生成失败，已切换为简化分析: %s", type(exc).__name__)

        if not analysis_text.strip():
            analysis_text = self._build_need_analysis_summary(ctx.user_query, ctx.conversation_history)
            await ctx.analysis_queue.put({"type": "analysis_delta", "content": analysis_text})
            logger.debug("      分析结果为空，已使用简化分析兜底")
        elapsed = round(time.perf_counter() - analysis_start, 3)
        await ctx.analysis_queue.put({"type": "analysis_done", "content": analysis_text, "elapsed": elapsed})
        return analysis_text, elapsed

    def _stream_emit_analysis_event(
        self,
        ctx: _StreamPipelineContext,
        event: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """把内部分析队列事件转换成 API 层可直接转发的 `analysis` payload。"""
        event_type = event.get("type")
        if event_type == "analysis_delta":
            content = str(event.get("content") or "")
            if not content:
                return None
            ctx.analysis_text += content
            return {
                "type": "analysis",
                "content": content,
                "summary": ctx.analysis_text[:200].replace("\n", " "),
                "timings": None,
            }
        if event_type == "analysis_done":
            ctx.analysis_done = True
            ctx.analysis_elapsed = float(event.get("elapsed") or 0)
            ctx.timings["analysis_calls"] = ctx.analysis_elapsed
            return {
                "type": "analysis",
                "content": "",
                "summary": ctx.analysis_text[:200].replace("\n", " "),
                "timings": {"analysis_calls": ctx.analysis_elapsed},
            }
        return None

    async def _stream_drain_analysis_queue(
        self,
        ctx: _StreamPipelineContext,
        *,
        wait_until_done: bool = False,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """排空分析队列；需要时等待分析任务完成后再返回。"""
        while wait_until_done and not ctx.analysis_done:
            event = await ctx.analysis_queue.get()
            payload = self._stream_emit_analysis_event(ctx, event)
            if payload:
                yield payload

        while not ctx.analysis_queue.empty():
            payload = self._stream_emit_analysis_event(ctx, await ctx.analysis_queue.get())
            if payload:
                yield payload

    async def _stream_first_tool_planning_call(
        self,
        ctx: _StreamPipelineContext,
    ) -> tuple[Any, Optional[Exception], float, float]:
        """执行首轮工具规划调用，供并行流程提前启动。"""
        call_start = time.perf_counter()
        try:
            response = await self.llm.chat_with_tools(
                messages=[dict(message) for message in ctx.messages],
                tools=ctx.tools,
                tool_choice="auto",
                model=ctx.model,
                model_config=ctx.model_config,
                thinking_type=None,
                reasoning_effort=None,
            )
            duration = time.perf_counter() - call_start
            return response, None, round(duration, 3), duration
        except Exception as exc:
            duration = time.perf_counter() - call_start
            return None, exc, round(duration, 3), duration

    async def _stream_stage_parallel_bootstrap(
        self,
        ctx: _StreamPipelineContext,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """启动可并行的后台分支，并向前端发送初始状态。"""
        ctx.parallel_branch_start = time.perf_counter()
        ctx.analysis_task = ctx.task_group.create(self._stream_run_need_analysis(ctx))
        if settings.tool_chat_parallel_enabled:
            # 三个分支互不依赖：需求分析用于展示，直查用于兜底，首轮 LLM 用于规划工具。
            ctx.direct_selected_products_task = ctx.task_group.create(self._stream_query_direct_selected_products(ctx))
            ctx.first_tool_planning_task = ctx.task_group.create(self._stream_first_tool_planning_call(ctx))

        yield self._status_chunk("正在分析需求", "need_analysis")
        yield self._status_chunk("正在检索知识库", "retrieving_knowledge")

    async def _stream_stage_rag_pipeline(
        self,
        ctx: _StreamPipelineContext,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """执行向量检索和可选 LLM rerank，同时穿插输出需求分析增量。"""
        t0 = time.perf_counter()
        vector_task = ctx.task_group.create(self._query_context_docs_with_timeout(ctx.user_query))
        while not vector_task.done():
            # 检索期间不要让前端空等，优先输出已经生成的需求分析片段。
            try:
                event = await asyncio.wait_for(ctx.analysis_queue.get(), timeout=0.2)
                payload = self._stream_emit_analysis_event(ctx, event)
                if payload:
                    yield payload
            except asyncio.TimeoutError:
                pass
        ctx.context_docs, vector_error = await vector_task
        async for payload in self._stream_drain_analysis_queue(ctx):
            yield payload

        ctx.context_text = self._format_context_docs(ctx.context_docs)
        ctx.rag_sources = self._extract_rag_sources(ctx.context_docs)
        elapsed = round(time.perf_counter() - t0, 3)
        ctx.timings["vector_search"] = elapsed
        if vector_error:
            ctx.timings["vector_search_error"] = vector_error
        logger.debug("  [1] 向量检索完成 | 耗时: %ss", elapsed)
        logger.debug("      检索到 %s 条知识库文档", len(ctx.context_docs))
        if ctx.context_docs:
            for i, doc in enumerate(ctx.context_docs[:3]):
                preview = str(doc)[:100].replace('\n', ' ')
                logger.debug("      文档[%s]: %s...", i, preview)
        trace_chunk = self._debug_chunk(
            "vector_search",
            "向量检索结果",
            query=ctx.user_query,
            elapsed=elapsed,
            error=vector_error,
            candidates=self._summarize_context_docs(ctx.context_docs),
        )
        self._log_trace_chunk(trace_chunk)
        yield trace_chunk

        if ctx.context_docs and settings.rag_llm_rerank_enabled:
            yield self._status_chunk("正在校验知识库", "reranking_knowledge")
        t_rerank = time.perf_counter()
        rerank_task = ctx.task_group.create(self._rerank_context_docs_with_llm(
            ctx.user_query,
            ctx.context_docs,
            model=ctx.model,
            model_config=ctx.model_config,
        ))
        while not rerank_task.done():
            try:
                event = await asyncio.wait_for(ctx.analysis_queue.get(), timeout=0.2)
                payload = self._stream_emit_analysis_event(ctx, event)
                if payload:
                    yield payload
            except asyncio.TimeoutError:
                pass
        ctx.context_docs, rerank_error, rerank_debug = await rerank_task
        async for payload in self._stream_drain_analysis_queue(ctx):
            yield payload

        rerank_elapsed = round(time.perf_counter() - t_rerank, 3)
        ctx.timings["rag_rerank"] = rerank_elapsed
        if rerank_error:
            ctx.timings["rag_rerank_error"] = rerank_error
        if ctx.context_docs:
            logger.debug("  [1.5] LLM RAG 检查完成 | 耗时: %ss | 保留 %s 条", rerank_elapsed, len(ctx.context_docs))

        trace_chunk = self._debug_chunk(
            "rag_rerank",
            "RAG LLM 检查明细",
            **rerank_debug,
            elapsed=rerank_elapsed,
        )
        self._log_trace_chunk(trace_chunk)
        yield trace_chunk

        ctx.context_text = self._format_context_docs(ctx.context_docs)
        ctx.rag_sources = self._extract_rag_sources(ctx.context_docs)
        if ctx.rag_sources:
            # rag_sources 给前端展示“知识来源商品”，不同于最终推荐目标商品。
            yield {
                "type": "rag_sources",
                "content": "已定位知识来源商品",
                "rag_sources": ctx.rag_sources,
                "timings": None,
            }

    async def _stream_stage_finish_analysis(
        self,
        ctx: _StreamPipelineContext,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """等待需求分析结束，并构造最终回复阶段使用的 system prompt。"""
        async for payload in self._stream_drain_analysis_queue(ctx, wait_until_done=True):
            yield payload
        if ctx.analysis_task is not None:
            await ctx.analysis_task
        logger.debug("      分析耗时: %ss", ctx.analysis_elapsed)
        logger.debug("      分析摘要: %s", ctx.analysis_text[:200].replace("\n", " "))
        logger.debug("      分析完整内容:\n%s", ctx.analysis_text)

        ctx.final_system_prompt = self._build_system_prompt(ctx.context_text, ctx.conversation_history, ctx.user_query)
        logger.debug("  构建消息列表: %s 条 (含 system + 历史 + 当前问题)", len(ctx.messages))

    async def _stream_stage_direct_product_query(
        self,
        ctx: _StreamPipelineContext,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """收集原始需求直查结果并输出 debug 事件。"""
        if ctx.direct_selected_products_task is not None:
            ctx.direct_selected_products, ctx.direct_query_elapsed, ctx.direct_query_error = await ctx.direct_selected_products_task
        else:
            ctx.direct_selected_products, ctx.direct_query_elapsed, ctx.direct_query_error = await self._stream_query_direct_selected_products(ctx)
        trace_chunk = self._debug_chunk(
            "direct_product_query",
            "原始需求直查 SQLite 商品库",
            query=self._build_direct_product_query_text(ctx.user_query),
            elapsed=ctx.direct_query_elapsed,
            error=ctx.direct_query_error,
            selected_product_ids=[item["product_id"] for item in ctx.direct_selected_products],
            selected_products=ctx.direct_selected_products,
        )
        self._log_trace_chunk(trace_chunk)
        yield trace_chunk
