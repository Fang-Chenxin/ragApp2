"""工具调用聊天服务模块 - 封装 SQLite 商品搜索工具对话逻辑"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Dict, List, Optional

from .llm_service import LLMService
from .product_search.query_tool import get_tool_spec, run_tool
from .tool_chat.product_selection import ToolChatProductSelectionMixin
from .tool_chat.prompts import ToolChatPromptMixin
from .tool_chat.rag import ToolChatRagMixin
from .tool_chat.stream_pipeline import ToolChatStreamMixin
from .tool_chat.trace import ToolChatTraceMixin
from .rag_service import VectorStore
from config.logging_config import get_logger

logger = get_logger("service.tool_chat")


class ToolChatService(
    ToolChatTraceMixin,
    ToolChatProductSelectionMixin,
    ToolChatPromptMixin,
    ToolChatRagMixin,
    ToolChatStreamMixin,
):
    """导购工具聊天服务。

    通过 mixin 组合导购所需能力：trace、商品选择、prompt、RAG、流式编排。
    API 层只调用本类的 `chat_with_tools_stream()` 或兼容保留的 `chat_with_tools()`。
    """

    def __init__(self, vector_store: VectorStore, llm: LLMService):
        """注入向量库和 LLM 服务，商品工具通过 product_search 模块全局服务执行。"""
        self.vector_store = vector_store
        self.llm = llm

    @staticmethod
    def _parse_tool_arguments(arguments_text: str) -> Dict[str, Any]:
        """解析 LLM 返回的工具参数 JSON；解析失败时返回空 dict 触发后续参数校验。"""
        try:
            return json.loads(arguments_text or "{}")
        except json.JSONDecodeError:
            return {}

    async def _run_tool_worker(
        self,
        tool_call_id: str,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> Dict[str, Any]:
        """在线程池中执行同步商品工具，并把结果包装成统一追踪结构。"""
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


        # 默认 LLM 未连接时，如果本轮没有客户端/发现模型传入的 api_key，就只能返回配置错误。
        if not self.llm.connected and not (model_config or {}).get("api_key"):
            logger.warning("  LLM 服务未连接")
            return {
                "reply": "LLM 服务未连接，请检查 LLM_API_KEY 配置。",
                "timings": timings,
            }

        t0 = time.perf_counter()
        # 非流式路径按顺序执行 RAG 检索和 rerank；流式路径会把部分阶段并行化。
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
        self._log_trace_chunk(self._debug_chunk(
            "vector_search",
            "向量检索结果",
            query=user_query,
            elapsed=elapsed,
            error=vector_error,
            candidates=self._summarize_context_docs(context_docs),
        ))
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
        self._log_trace_chunk(self._debug_chunk(
            "rag_rerank",
            "RAG LLM 检查明细",
            **_rerank_debug,
            elapsed=rerank_elapsed,
        ))

        context_text = self._format_context_docs(context_docs)
        rag_sources = self._extract_rag_sources(context_docs)

        final_system_prompt = self._build_system_prompt(context_text, conversation_history, user_query)

        messages = self._build_tool_planning_messages(conversation_history, user_query)
        logger.debug("  构建消息列表: %s 条 (含 system + 历史 + 当前问题)", len(messages))

        tools = [get_tool_spec()]

        llm_call_total = 0.0
        tool_call_total = 0.0
        llm_rounds = 0
        tool_rounds = 0
        consecutive_empty_params = 0

        for round_idx in range(max_tool_calls):
            # 每一轮都让 LLM 基于已有工具结果决定是否继续查库或直接回答。
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

                # `run_tool` 是同步 SQLite 查询，放到线程中避免阻塞事件循环。
                tool_start = time.perf_counter()
                result = await asyncio.to_thread(run_tool, tool_name, arguments)
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
                # 连续空参数通常代表 prompt/模型陷入循环，提前退出再做最终回复更稳。
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
                # 工具规划 prompt 偏“如何查库”，最终回复前替换成面向用户推荐的系统 prompt。
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



tool_chat_service: Optional[ToolChatService] = None
