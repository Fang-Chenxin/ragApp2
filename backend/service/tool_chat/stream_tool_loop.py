"""流式工具聊天工具规划与执行循环。"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncGenerator, Dict

from .stream_context import _StreamPipelineContext
from config.logging_config import get_logger
from config.settings import settings

logger = get_logger("service.tool_chat")


class ToolChatStreamToolLoopMixin:
    """LLM 工具规划、工具执行、结果归档和提前退出判断。"""

    async def _stream_stage_tool_loop(
        self,
        ctx: _StreamPipelineContext,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """运行多轮“LLM 规划 -> SQLite 工具执行 -> 工具结果回填”的循环。"""
        for round_idx in range(ctx.max_tool_calls):
            logger.debug("  ── LLM 第 %s 轮调用 ──", round_idx + 1)
            logger.debug("      发送消息数: %s", len(ctx.messages))

            if round_idx == 0:
                # 首轮可能已经在 bootstrap 阶段并行开始，这里只需要等待结果。
                if ctx.first_tool_planning_task is not None:
                    response, call_error, elapsed, duration = await ctx.first_tool_planning_task
                else:
                    response, call_error, elapsed, duration = await self._stream_first_tool_planning_call(ctx)
                if settings.tool_chat_parallel_enabled:
                    # 粗略估算并行节省时间，便于观察并行流程是否真的有收益。
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
                # 第二轮以后必须等待上一轮工具结果进入 messages 后再让 LLM 继续规划。
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
                # 将 SDK 对象转成普通 dict，后续可安全 append 到 OpenAI messages。
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
                # LLM 认为无需继续查库时，进入“直接回复整理”阶段。
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
                # 同一轮多个工具调用并发执行，减少 SQLite 查询等待时间。
                tool_tasks.append(ctx.task_group.create(self._run_tool_worker(tc.id, tool_name, arguments)))

            for task in asyncio.as_completed(tool_tasks):
                # 先把完成的工具结果作为 debug/status 事件发给前端；消息回填稍后按原调用顺序进行。
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
                # OpenAI 工具协议要求 assistant tool_calls 后按 tool_call_id 回填 tool 消息。
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
                # 连续空参数代表模型没有从历史中抽出有效查询词，继续循环通常只会浪费时间。
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
                search_plan=ctx.search_plan,
            )
            if len(round_selected_products) >= 3:
                # 已有足够可核验商品时提前停止工具循环，避免模型继续泛化查询。
                logger.debug("      已获得 %s 个目标商品，提前进入最终推荐生成", len(round_selected_products))
                yield self._status_chunk(
                    "已找到足够候选，正在整理推荐",
                    "organizing_results",
                    extra={"selected_product_count": len(round_selected_products)},
                )
                break
