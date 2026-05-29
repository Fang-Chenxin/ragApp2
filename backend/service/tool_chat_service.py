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
    def _build_need_analysis_messages(
        context_text: str,
        conversation_history: Optional[List[Dict[str, str]]],
        user_query: str,
    ) -> List[Dict[str, str]]:
        history_context = ToolChatService._build_history_context(conversation_history, user_query)
        return [
            {
                "role": "system",
                "content": (
                    "你是导购助手的需求分析子角色。请基于用户问题、历史对话和知识库线索，"
                    "用自然、简洁、像人在解释需求的方式，输出 1-3 句需求分析。"
                    "要求：1) 说明用户真正想解决什么；2) 说明你准备优先检索的方向；"
                    "3) 如果明显是场景/体验诉求，直接说成场景型购物需求；4) 不要列商品，不要写工具过程，不要编号。\n\n"
                    f"知识库线索：\n{context_text}\n\n"
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
    def _build_selected_products_context(selected_products: List[Dict[str, Any]]) -> str:
        if not selected_products:
            return "未找到明确命中的商品候选。"

        lines: list[str] = ["## 已选中商品候选（最终推荐必须引用这些 id）"]
        for item in selected_products:
            parts = [f"id={item.get('product_id', '')}"]
            if item.get("title"):
                parts.append(f"title={item['title']}")
            if item.get("brand"):
                parts.append(f"brand={item['brand']}")
            if item.get("category"):
                parts.append(f"category={item['category']}")
            if item.get("sub_category"):
                parts.append(f"sub_category={item['sub_category']}")
            if item.get("base_price") is not None:
                parts.append(f"price={item['base_price']}")
            lines.append("- " + " | ".join(parts))
        return "\n".join(lines)

    @staticmethod
    def _build_final_recommendation_messages(
        system_prompt: str,
        analysis_text: str,
        selected_products: List[Dict[str, Any]],
    ) -> List[Dict[str, str]]:
        selected_context = ToolChatService._build_selected_products_context(selected_products)
        final_guidance = (
            "你现在进入最终导购推荐阶段。\n"
            "请基于前面的需求分析和已选中的商品候选，输出面向用户的最终推荐。\n"
            "要求：1) 开头先用一句话确认用户需求；2) 明确输出已选中商品ID，便于用户核对；"
            "3) 给出 3-5 个推荐方向或具体商品，并解释为什么相关；4) 不要重复长篇需求分析；"
            "5) 推荐要自然、像真人导购。\n\n"
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
    def _build_system_prompt(context_text: str, conversation_history: Optional[List[Dict[str, str]]], user_query: str) -> str:
        return (
            "你是一个资深导购型商品助手，目标不是机械地回复是否有货，而是先分析用户真实需求，再给出最合适的商品建议。\n\n"
            f"参考知识库内容：\n{context_text}\n\n"
            f"{ToolChatService._build_history_context(conversation_history, user_query)}\n\n"
            "## 导购策略（严格遵守）\n"
            "1. 先判断用户想解决的真实问题，再决定检索方向；不要只盯着用户字面上的词。\n"
            "2. 优先推荐直接相关商品；如果没有直接相商品，必须转向次相关商品或相邻品类，不要只说没有。\n"
            "3. 当用户是在描述目标、场景或体验诉求时，例如'我要成为XX高手'、'想提升对战体验'、'想要更流畅'，\n"
            "   要把需求理解为场景型购物需求，优先考虑数码电子类的手机、平板、笔记本、耳机等提升体验的商品。\n"
            "4. 如果直搜某个品牌、游戏或泛词没有结果，要主动改用场景词重搜，例如'适合玩<场景>的游戏电子产品'、\n"
            "   '提升<场景>体验的数码电子产品'、'高刷平板'、'游戏手机'、'降噪耳机'、'轻薄本'。\n"
            "5. 最终回答要像导购：先一句话概括用户需求，再给出 3-5 个推荐方向或具体商品，并说明每个推荐为什么相关。\n"
            "6. 工具调用结果会自动返回给你，用于生成最终回答。\n\n"
            "## 调用工具规则（严格遵守）\n"
            "1. 调用 query_products 时，必须提供有效的查询参数（text、keyword、brand 等），禁止传空参数 {}。\n"
            "2. 如果用户的问题引用了对话历史中的商品（如'这几个''上面的''那款''这个牌子'），"
            "   你必须从上方「最近对话中的商品信息」中提取品牌名、商品名等关键词作为 text 参数。\n"
            "3. 如果用户的需求是场景型或目标型，优先使用扩展后的场景关键词发起查询，而不是只搜原始名词。\n"
            "4. 如果第一次检索没有直接命中，不要结束对话，要立即转向次相关品类重新组织推荐。"
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
        max_tool_calls: int = 5
    ) -> Dict[str, Any]:
        """使用原生 function calling 进行对话，返回 reply 和各环节耗时"""
        timings: Dict[str, Any] = {}
        t_total_start = time.perf_counter()

        logger.debug("═══ [chat_with_tools] 开始处理请求")

        logger.debug("  用户问题: %s", user_query)
        logger.debug("  历史消息数: %s", len(conversation_history) if conversation_history else 0)
        logger.debug("  最大工具调用轮数: %s", max_tool_calls)


        if not self.llm.connected:
            logger.warning("  LLM 服务未连接")
            return {
                "reply": "LLM 服务未连接，请检查 LLM_API_KEY 配置。",
                "timings": timings,
            }

        t0 = time.perf_counter()
        context_docs = await asyncio.to_thread(self.vector_store.query, user_query)
        context_text = "\n".join([str(doc) for doc in context_docs])
        elapsed = round(time.perf_counter() - t0, 3)
        timings["vector_search"] = elapsed
        logger.debug("  [1] 向量检索完成 | 耗时: %ss", elapsed)
        logger.debug("      检索到 %s 条知识库文档", len(context_docs))
        if context_docs:
            for i, doc in enumerate(context_docs[:3]):
                preview = str(doc)[:100].replace('\n', ' ')
                logger.debug("      文档[%s]: %s...", i, preview)

        system_prompt = self._build_system_prompt(context_text, conversation_history, user_query)

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
                return {"reply": assistant_message.content or "", "timings": timings}

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
            final_response = await self.llm.chat_with_tools(
                messages=messages,
                tools=tools,
                tool_choice="none",
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
        }

    async def chat_with_tools_stream(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        max_tool_calls: int = 5,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """使用原生 function calling 进行对话，流式返回结果"""
        timings: Dict[str, Any] = {}
        t_total_start = time.perf_counter()

        logger.debug("═══ [chat_with_tools_stream] 开始处理请求")
        logger.debug("  用户问题: %s", user_query)
        logger.debug("  历史消息数: %s", len(conversation_history) if conversation_history else 0)
        logger.debug("  最大工具调用轮数: %s", max_tool_calls)

        if not self.llm.connected:
            logger.warning("  LLM 服务未连接")
            yield {
                "type": "error",
                "content": "LLM 服务未连接，请检查 LLM_API_KEY 配置。",
                "timings": timings,
            }
            return

        t0 = time.perf_counter()
        context_docs = await asyncio.to_thread(self.vector_store.query, user_query)
        context_text = "\n".join([str(doc) for doc in context_docs])
        elapsed = round(time.perf_counter() - t0, 3)
        timings["vector_search"] = elapsed
        logger.debug("  [1] 向量检索完成 | 耗时: %ss", elapsed)
        logger.debug("      检索到 %s 条知识库文档", len(context_docs))
        if context_docs:
            for i, doc in enumerate(context_docs[:3]):
                preview = str(doc)[:100].replace('\n', ' ')
                logger.debug("      文档[%s]: %s...", i, preview)

        logger.debug("      [2] 开始 LLM 需求分析流")
        yield self._status_chunk("正在分析需求", "need_analysis")

        analysis_text = ""
        t_analysis_start = time.perf_counter()
        try:
            analysis_messages = self._build_need_analysis_messages(context_text, conversation_history, user_query)
            async for chunk in self.llm.chat_stream(analysis_messages):
                if chunk:
                    analysis_text += chunk
        except Exception as exc:
            analysis_text = self._build_need_analysis_summary(user_query, conversation_history)
            logger.warning("      需求分析生成失败，已切换为简化分析: %s", type(exc).__name__)

        analysis_elapsed = round(time.perf_counter() - t_analysis_start, 3)
        if not analysis_text.strip():
            analysis_text = self._build_need_analysis_summary(user_query, conversation_history)
            logger.debug("      分析结果为空，已使用简化分析兜底")
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

        system_prompt = self._build_system_prompt(context_text, conversation_history, user_query)

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

        tool_results: Dict[str, Dict[str, Any]] = {}
        tool_call_order: list[str] = []

        for round_idx in range(max_tool_calls):
            logger.debug("  ── LLM 第 %s 轮调用 ──", round_idx + 1)
            logger.debug("      发送消息数: %s", len(messages))

            t1 = time.perf_counter()
            try:
                response = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
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
                logger.debug("      ✅ 无工具调用，开始流式返回文本")
                logger.debug("      回复预览: %s...", reply_preview)
                yield self._status_chunk("正在整理结果", "organizing_results")

                timings["llm_calls"] = round(llm_call_total, 3)
                timings["llm_rounds"] = llm_rounds
                timings["analysis_calls"] = timings.get("analysis_calls", 0)
                timings["tool_calls"] = round(tool_call_total, 3)
                timings["tool_rounds"] = tool_rounds

                # LLM 已经在当前轮返回了最终回复内容，直接使用，不再额外调用 LLM
                final_content = assistant_message.content or ""
                if final_content:
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
            tool_call_order: list[str] = []
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

            tool_results: Dict[str, Dict[str, Any]] = {}
            for task in asyncio.as_completed(tool_tasks):
                outcome = await task
                tool_results[outcome["tool_call_id"]] = outcome
                result = outcome["result"]
                result_total = outcome.get("total", 0)
                result_ok = outcome.get("ok", None)
                logger.debug("           结果: ok=%s, total=%s, 耗时=%ss", result_ok, result_total, outcome.get("elapsed", 0))
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

            for tool_call_id in tool_call_order:
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

        selected_products = self._extract_selected_products(tool_results, tool_call_order)
        selected_product_ids = [item["product_id"] for item in selected_products]
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
        final_messages = self._build_final_recommendation_messages(system_prompt, analysis_text.strip(), selected_products) + final_messages

        try:
            async for chunk in self.llm.chat_stream(final_messages):
                yield {
                    "type": "content",
                    "content": chunk,
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