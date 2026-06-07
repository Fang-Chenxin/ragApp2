"""流式工具对话 Pipeline - 拆分 ToolChatService 的阶段化流式编排。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from .sqlite_product_query_tool import get_tool_spec
from config.logging_config import get_logger
from config.settings import settings

logger = get_logger("service.tool_chat")


class _StreamTaskGroup:
    """Track background tasks created while a streaming response is active."""

    def __init__(self):
        self._tasks: list[asyncio.Task[Any]] = []

    def create(self, coro) -> asyncio.Task[Any]:
        task = asyncio.create_task(coro)
        self._tasks.append(task)
        return task

    async def cancel_pending(self) -> None:
        pending = [task for task in self._tasks if not task.done()]
        if not pending:
            return
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


@dataclass
class _StreamPipelineContext:
    user_query: str
    conversation_history: Optional[List[Dict[str, str]]]
    max_tool_calls: int
    model: Optional[str]
    model_config: Optional[Dict[str, Any]]
    timings: Dict[str, Any]
    t_total_start: float
    messages: list[Dict[str, Any]]
    tools: list[Dict[str, Any]]
    task_group: _StreamTaskGroup
    analysis_queue: asyncio.Queue[Dict[str, Any]] = field(default_factory=asyncio.Queue)
    analysis_text: str = ""
    analysis_elapsed: float = 0.0
    analysis_done: bool = False
    context_docs: List[Any] = field(default_factory=list)
    context_text: str = ""
    rag_sources: List[Dict[str, Any]] = field(default_factory=list)
    final_system_prompt: str = ""
    direct_selected_products: List[Dict[str, Any]] = field(default_factory=list)
    direct_query_elapsed: float = 0.0
    direct_query_error: Optional[str] = None
    llm_call_total: float = 0.0
    tool_call_total: float = 0.0
    llm_rounds: int = 0
    tool_rounds: int = 0
    consecutive_empty_params: int = 0
    tool_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tool_call_order: list[str] = field(default_factory=list)
    parallel_branch_start: float = 0.0
    analysis_task: Optional[asyncio.Task[tuple[str, float]]] = None
    direct_selected_products_task: Optional[asyncio.Task[tuple[List[Dict[str, Any]], float, Optional[str]]]] = None
    first_tool_planning_task: Optional[asyncio.Task[tuple[Any, Optional[Exception], float, float]]] = None
    completed: bool = False



class ToolChatStreamMixin:
    """阶段化流式工具对话编排。"""

    async def _stream_query_direct_selected_products(
        self,
        ctx: _StreamPipelineContext,
    ) -> tuple[List[Dict[str, Any]], float, Optional[str]]:
        direct_start = time.perf_counter()
        try:
            products = await asyncio.to_thread(self._query_direct_selected_products, ctx.user_query)
            return products, round(time.perf_counter() - direct_start, 3), None
        except Exception as exc:
            error = f"原始需求直查失败：{type(exc).__name__}: {exc}"
            logger.warning(error)
            return [], round(time.perf_counter() - direct_start, 3), error

    async def _stream_run_need_analysis(self, ctx: _StreamPipelineContext) -> tuple[str, float]:
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
        ctx.parallel_branch_start = time.perf_counter()
        ctx.analysis_task = ctx.task_group.create(self._stream_run_need_analysis(ctx))
        if settings.tool_chat_parallel_enabled:
            ctx.direct_selected_products_task = ctx.task_group.create(self._stream_query_direct_selected_products(ctx))
            ctx.first_tool_planning_task = ctx.task_group.create(self._stream_first_tool_planning_call(ctx))

        yield self._status_chunk("正在分析需求", "need_analysis")
        yield self._status_chunk("正在检索知识库", "retrieving_knowledge")

    async def _stream_stage_rag_pipeline(
        self,
        ctx: _StreamPipelineContext,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        t0 = time.perf_counter()
        vector_task = ctx.task_group.create(self._query_context_docs_with_timeout(ctx.user_query))
        while not vector_task.done():
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

    def _stream_selected_product_payloads(
        self,
        ctx: _StreamPipelineContext,
    ) -> tuple[List[Dict[str, Any]], list[str], list[Dict[str, Any]]]:
        tool_selected_products = self._extract_selected_products(ctx.tool_results, ctx.tool_call_order)
        selected_products = self._build_target_products(ctx.direct_selected_products, tool_selected_products, ctx.user_query)
        selected_product_ids = [item["product_id"] for item in selected_products]
        trace_chunk = self._debug_chunk(
            "selected_products",
            "最终目标商品合并结果",
            direct_selected_product_ids=[item["product_id"] for item in ctx.direct_selected_products],
            tool_selected_product_ids=[item["product_id"] for item in tool_selected_products],
            selected_product_ids=selected_product_ids,
            selected_products=selected_products,
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
        ctx.timings["llm_calls"] = round(ctx.llm_call_total, 3)
        ctx.timings["llm_rounds"] = ctx.llm_rounds
        ctx.timings["analysis_calls"] = ctx.timings.get("analysis_calls", 0)
        ctx.timings["tool_calls"] = round(ctx.tool_call_total, 3)
        ctx.timings["tool_rounds"] = ctx.tool_rounds

    async def _stream_stage_direct_reply_finalization(
        self,
        ctx: _StreamPipelineContext,
        assistant_message: Any,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        reply_preview = (assistant_message.content or "")[:200].replace('\n', ' ')
        logger.debug("      ✅ 无工具调用，开始流式返回文本")
        logger.debug("      回复预览: %s...", reply_preview)

        selected_products, selected_product_ids, payloads = self._stream_selected_product_payloads(ctx)
        for payload in payloads:
            yield payload
        yield self._status_chunk("正在整理结果", "organizing_results")

        self._stream_update_tool_timings(ctx)

        final_content = assistant_message.content or ""
        needs_constrained_final = bool(selected_product_ids)
        trace_chunk = self._debug_chunk(
            "organizing_results",
            "最终回复整理检查",
            mode="assistant_direct_reply",
            selected_product_ids=selected_product_ids,
            needs_constrained_final=needs_constrained_final,
        )
        self._log_trace_chunk(trace_chunk)
        yield trace_chunk

        if needs_constrained_final:
            logger.debug("      存在目标商品，基于数据库目标清单流式生成最终回复")
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
                fallback_reply = self._build_deterministic_final_reply(ctx.user_query, selected_products)
                if fallback_reply:
                    yield self._debug_chunk(
                        "organizing_results",
                        "受约束回复为空，改用确定性兜底",
                        selected_product_ids=selected_product_ids,
                    )
                    yield {"type": "content", "content": self._sanitize_user_reply(fallback_reply), "timings": None}
        elif final_content:
            yield {"type": "content", "content": self._sanitize_user_reply(final_content), "timings": None}

        ctx.timings["total"] = round(time.perf_counter() - ctx.t_total_start, 3)
        self._print_timings_summary(ctx.timings)
        yield {"type": "done", "content": "", "timings": ctx.timings}
        ctx.completed = True

    async def _stream_stage_tool_loop(
        self,
        ctx: _StreamPipelineContext,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        for round_idx in range(ctx.max_tool_calls):
            logger.debug("  ── LLM 第 %s 轮调用 ──", round_idx + 1)
            logger.debug("      发送消息数: %s", len(ctx.messages))

            if round_idx == 0:
                if ctx.first_tool_planning_task is not None:
                    response, call_error, elapsed, duration = await ctx.first_tool_planning_task
                else:
                    response, call_error, elapsed, duration = await self._stream_first_tool_planning_call(ctx)
                if settings.tool_chat_parallel_enabled:
                    parallel_wall = max(time.perf_counter() - ctx.parallel_branch_start, 0)
                    parallel_branch_sum = ctx.analysis_elapsed + ctx.direct_query_elapsed + duration
                    ctx.timings["parallel_overlap_saved_estimate"] = round(max(parallel_branch_sum - parallel_wall, 0), 3)
                else:
                    ctx.timings["parallel_overlap_saved_estimate"] = 0
                if call_error:
                    logger.error("      LLM 调用异常 | 耗时: %ss | %s: %s", elapsed, type(call_error).__name__, call_error)
                    ctx.timings["llm_calls"] = round(ctx.llm_call_total + duration, 3)
                    ctx.timings["analysis_calls"] = ctx.timings.get("analysis_calls", 0)
                    ctx.timings["tool_calls"] = round(ctx.tool_call_total, 3)
                    ctx.timings["total"] = round(time.perf_counter() - ctx.t_total_start, 3)
                    yield {
                        "type": "error",
                        "content": f"LLM 调用失败: {type(call_error).__name__}: {call_error}",
                        "timings": ctx.timings,
                    }
                    ctx.completed = True
                    return
                ctx.llm_call_total += duration
            else:
                t1 = time.perf_counter()
                try:
                    response = await self.llm.chat_with_tools(
                        messages=ctx.messages,
                        tools=ctx.tools,
                        tool_choice="auto",
                        model=ctx.model,
                        model_config=ctx.model_config,
                        thinking_type=None,
                        reasoning_effort=None,
                    )
                except Exception as e:
                    elapsed = round(time.perf_counter() - t1, 3)
                    logger.error("      LLM 调用异常 | 耗时: %ss | %s: %s", elapsed, type(e).__name__, e)
                    ctx.timings["llm_calls"] = round(ctx.llm_call_total + elapsed, 3)
                    ctx.timings["analysis_calls"] = ctx.timings.get("analysis_calls", 0)
                    ctx.timings["tool_calls"] = round(ctx.tool_call_total, 3)
                    ctx.timings["total"] = round(time.perf_counter() - ctx.t_total_start, 3)
                    yield {
                        "type": "error",
                        "content": f"LLM 调用失败: {type(e).__name__}: {e}",
                        "timings": ctx.timings,
                    }
                    ctx.completed = True
                    return
                elapsed = round(time.perf_counter() - t1, 3)
                ctx.llm_call_total += time.perf_counter() - t1
            ctx.llm_rounds += 1

            if response is None:
                yield {"type": "error", "content": "LLM 调用失败: 未返回响应", "timings": ctx.timings}
                ctx.completed = True
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

            assistant_payload: Dict[str, Any] = {"role": "assistant", "content": assistant_message.content}
            if assistant_message.tool_calls:
                assistant_payload["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in assistant_message.tool_calls
                ]
            ctx.messages.append(assistant_payload)

            if assistant_message.tool_calls:
                planned_tool_calls = [
                    {
                        "tool_call_id": tc.id,
                        "tool_name": tc.function.name,
                        "arguments": self._parse_tool_arguments(tc.function.arguments or "{}"),
                    }
                    for tc in assistant_message.tool_calls
                ]
                trace_chunk = self._debug_chunk(
                    "llm_tool_plan",
                    f"LLM 第 {round_idx + 1} 轮工具调用计划",
                    round=round_idx + 1,
                    tool_calls=planned_tool_calls,
                    assistant_content=assistant_message.content or "",
                )
                self._log_trace_chunk(trace_chunk)
                yield trace_chunk

            if not assistant_message.tool_calls:
                async for payload in self._stream_stage_direct_reply_finalization(ctx, assistant_message):
                    yield payload
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
            for tc in assistant_message.tool_calls:
                tool_name = tc.function.name
                arguments = self._parse_tool_arguments(tc.function.arguments or "{}")
                logger.debug("         → 工具: %s", tool_name)
                logger.debug("           参数: %s", json.dumps(arguments, ensure_ascii=False)[:300])

                has_valid_param = any(arguments.get(k) for k in ["text", "keyword", "brand", "category", "sub_category", "attr_filters"])
                if not has_valid_param:
                    round_has_empty = True

                ctx.tool_call_order.append(tc.id)
                round_tool_call_order.append(tc.id)
                yield self._status_chunk(
                    f"正在查询商品：{tool_name}",
                    "querying_products",
                    extra={"tool_call_id": tc.id, "tool_name": tool_name},
                )
                tool_tasks.append(ctx.task_group.create(self._run_tool_worker(tc.id, tool_name, arguments)))

            for task in asyncio.as_completed(tool_tasks):
                outcome = await task
                ctx.tool_results[outcome["tool_call_id"]] = outcome
                result = outcome["result"]
                result_total = outcome.get("total", 0)
                result_ok = outcome.get("ok", None)
                logger.debug("           结果: ok=%s, total=%s, 耗时=%ss", result_ok, result_total, outcome.get("elapsed", 0))
                trace_chunk = self._debug_chunk(
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
                self._log_trace_chunk(trace_chunk)
                yield trace_chunk
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
                result = ctx.tool_results.get(tool_call_id, {}).get("result", {})
                ctx.messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "content": json.dumps(result, ensure_ascii=False),
                    }
                )
            ctx.tool_call_total += time.perf_counter() - t2
            ctx.tool_rounds += 1

            if round_has_empty:
                ctx.consecutive_empty_params += 1
                logger.warning("      检测到空参数调用 (连续 %s 次)", ctx.consecutive_empty_params)
                if ctx.consecutive_empty_params >= 2:
                    logger.warning("      断路器触发：连续空参数，提前退出工具循环")
                    break
            else:
                ctx.consecutive_empty_params = 0

            round_tool_selected_products = self._extract_selected_products(ctx.tool_results, ctx.tool_call_order)
            round_selected_products = self._build_target_products(
                ctx.direct_selected_products,
                round_tool_selected_products,
                ctx.user_query,
            )
            if len(round_selected_products) >= 3:
                logger.debug("      已获得 %s 个目标商品，提前进入最终推荐生成", len(round_selected_products))
                yield self._status_chunk(
                    "已找到足够候选，正在整理推荐",
                    "organizing_results",
                    extra={"selected_product_count": len(round_selected_products)},
                )
                break

    async def _stream_stage_final_reply(
        self,
        ctx: _StreamPipelineContext,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        selected_products, selected_product_ids, payloads = self._stream_selected_product_payloads(ctx)
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

    async def chat_with_tools_stream(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        max_tool_calls: int = 5,
        model: Optional[str] = None,
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """使用原生 function calling 进行对话，按阶段流式返回结果。"""
        timings: Dict[str, Any] = {"parallel_enabled": settings.tool_chat_parallel_enabled}
        t_total_start = time.perf_counter()

        logger.debug("═══ [chat_with_tools_stream] 开始处理请求")
        logger.debug("  用户问题: %s", user_query)
        logger.debug("  历史消息数: %s", len(conversation_history) if conversation_history else 0)
        logger.debug("  最大工具调用轮数: %s", max_tool_calls)
        logger.debug("  使用模型: %s", model or (model_config or {}).get("id") or getattr(self.llm, "model", "default"))
        logger.debug("  并行流程: %s", "启用" if settings.tool_chat_parallel_enabled else "关闭")

        if not self.llm.connected and not (model_config or {}).get("api_key"):
            logger.warning("  LLM 服务未连接")
            yield {
                "type": "error",
                "content": "LLM 服务未连接，请检查 LLM_API_KEY 配置。",
                "timings": timings,
            }
            return

        ctx = _StreamPipelineContext(
            user_query=user_query,
            conversation_history=conversation_history,
            max_tool_calls=max_tool_calls,
            model=model,
            model_config=model_config,
            timings=timings,
            t_total_start=t_total_start,
            messages=self._build_tool_planning_messages(conversation_history, user_query),
            tools=[get_tool_spec()],
            task_group=_StreamTaskGroup(),
        )

        pipeline = [
            self._stream_stage_parallel_bootstrap,
            self._stream_stage_rag_pipeline,
            self._stream_stage_finish_analysis,
            self._stream_stage_direct_product_query,
            self._stream_stage_tool_loop,
            self._stream_stage_final_reply,
        ]

        try:
            for stage in pipeline:
                if ctx.completed:
                    break
                async for event in stage(ctx):
                    yield event
        finally:
            await ctx.task_group.cancel_pending()

