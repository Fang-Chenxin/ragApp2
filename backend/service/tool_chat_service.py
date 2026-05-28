"""工具调用聊天服务模块 - 封装 SQLite 商品搜索工具对话逻辑"""
from __future__ import annotations

import json
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from .llm_service import LLMService
from .sqlite_product_query_tool import get_tool_spec, run_tool
from .rag_service import VectorStore


class ToolChatService:
    """工具调用聊天服务封装类"""

    def __init__(self, vector_store: VectorStore, llm: LLMService):
        self.vector_store = vector_store
        self.llm = llm

    async def chat_with_tools(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        max_tool_calls: int = 5
    ) -> Dict[str, Any]:
        """使用原生 function calling 进行对话，返回 reply 和各环节耗时"""
        timings: Dict[str, Any] = {}
        t_total_start = time.perf_counter()

        print(f"\n{'='*60}")
        print(f"[chat_with_tools] 开始处理请求")
        print(f"  用户问题: {user_query}")
        print(f"  历史消息数: {len(conversation_history) if conversation_history else 0}")
        print(f"  最大工具调用轮数: {max_tool_calls}")
        print(f"{'='*60}")

        if not self.llm.connected:
            print(f"  ❌ LLM 服务未连接")
            return {
                "reply": "LLM 服务未连接，请检查 LLM_API_KEY 配置。",
                "timings": timings,
            }

        t0 = time.perf_counter()
        context_docs = self.vector_store.query(user_query)
        context_text = "\n".join([str(doc) for doc in context_docs])
        elapsed = round(time.perf_counter() - t0, 3)
        timings["vector_search"] = elapsed
        print(f"\n  [1] 向量检索完成 | 耗时: {elapsed}s")
        print(f"      检索到 {len(context_docs)} 条知识库文档")
        if context_docs:
            for i, doc in enumerate(context_docs[:3]):
                preview = str(doc)[:100].replace('\n', ' ')
                print(f"      文档[{i}]: {preview}...")

        system_prompt = (
            "你是一个智能商品搜索助手，可以使用工具查询商品信息。\n\n"
            f"参考知识库内容：\n{context_text}\n\n"
            f"{self._build_history_context(conversation_history, user_query)}\n\n"
            "## 调用工具规则（严格遵守）\n"
            "1. 调用 query_products 时，必须提供有效的查询参数（text、keyword、brand 等），禁止传空参数 {}。\n"
            "2. 如果用户的问题引用了对话历史中的商品（如'这几个''上面的''那款''这个牌子'），"
            "   你必须从上方「最近对话中的商品信息」中提取品牌名、商品名等关键词作为 text 参数。\n"
            "3. 仅当需要查询商品信息时才调用工具，如果不需要查询，直接回答用户问题。\n"
            "4. 工具调用结果会自动返回给你，用于生成最终回答。"
        )

        messages: list[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": user_query})
        print(f"  构建消息列表: {len(messages)} 条 (含 system + 历史 + 当前问题)")

        tools = [get_tool_spec()]

        llm_call_total = 0.0
        tool_call_total = 0.0
        llm_rounds = 0
        tool_rounds = 0
        consecutive_empty_params = 0

        for round_idx in range(max_tool_calls):
            print(f"\n  ── LLM 第 {round_idx + 1} 轮调用 ──")
            print(f"      发送消息数: {len(messages)}")

            t1 = time.perf_counter()
            try:
                response = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                )
            except Exception as e:
                elapsed = round(time.perf_counter() - t1, 3)
                print(f"      ❌ LLM 调用异常 | 耗时: {elapsed}s | {type(e).__name__}: {e}")
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

            print(f"      LLM 响应完成 | 耗时: {elapsed}s{usage_info}")

            if assistant_message.content:
                content_preview = assistant_message.content[:500].replace('\n', '\n      │ ')
                print(f"      LLM 回复内容:")
                print(f"      │ {content_preview}")
                if len(assistant_message.content) > 500:
                    print(f"      │ ... (共 {len(assistant_message.content)} 字符)")
            else:
                print(f"      LLM 回复内容: (空，仅工具调用)")

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
                print(f"      ✅ 无工具调用，直接返回文本")
                print(f"      回复预览: {reply_preview}...")
                timings["llm_calls"] = round(llm_call_total, 3)
                timings["llm_rounds"] = llm_rounds
                timings["tool_calls"] = round(tool_call_total, 3)
                timings["tool_rounds"] = tool_rounds
                timings["total"] = round(time.perf_counter() - t_total_start, 3)
                self._print_timings_summary(timings)
                return {
                    "reply": assistant_message.content or "",
                    "timings": timings,
                }

            print(f"      🔧 触发 {len(assistant_message.tool_calls)} 个工具调用:")
            t2 = time.perf_counter()
            round_has_empty = False
            for tc in assistant_message.tool_calls:
                tool_name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}

                print(f"         → 工具: {tool_name}")
                print(f"           参数: {json.dumps(arguments, ensure_ascii=False)[:300]}")

                has_valid_param = any(arguments.get(k) for k in ["text", "keyword", "brand", "category", "sub_category", "attr_filters"])
                if not has_valid_param:
                    round_has_empty = True

                tool_start = time.perf_counter()
                result = run_tool(tool_name, arguments)
                tool_elapsed = round(time.perf_counter() - tool_start, 3)

                result_total = result.get("total", 0) if isinstance(result, dict) else 0
                result_ok = result.get("ok", None) if isinstance(result, dict) else None
                print(f"           结果: ok={result_ok}, total={result_total}, 耗时={tool_elapsed}s")

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
                print(f"      ⚠️  检测到空参数调用 (连续 {consecutive_empty_params} 次)")
                if consecutive_empty_params >= 2:
                    print(f"      🛑 断路器触发：连续空参数，提前退出工具循环，转为纯文本回复")
                    break
            else:
                consecutive_empty_params = 0

        print(f"\n  ── 工具调用轮数已耗尽，执行最终纯文本 LLM 调用 ──")
        print(f"      发送消息数: {len(messages)}")
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
            print(f"      LLM 响应完成 | 耗时: {elapsed}s{usage_info}")
        except Exception as e:
            elapsed = round(time.perf_counter() - t3, 3)
            print(f"      ❌ 最终 LLM 调用异常 | 耗时: {elapsed}s | {type(e).__name__}: {e}")
            reply = f"LLM 调用失败: {type(e).__name__}: {e}"
        llm_call_total += time.perf_counter() - t3
        llm_rounds += 1

        if reply:
            content_preview = reply[:500].replace('\n', '\n      │ ')
            print(f"      最终回复内容:")
            print(f"      │ {content_preview}")
            if len(reply) > 500:
                print(f"      │ ... (共 {len(reply)} 字符)")

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
        max_tool_calls: int = 5
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """使用原生 function calling 进行对话，流式返回结果"""
        timings: Dict[str, Any] = {}
        t_total_start = time.perf_counter()

        print(f"\n{'='*60}")
        print(f"[chat_with_tools_stream] 开始处理请求")
        print(f"  用户问题: {user_query}")
        print(f"  历史消息数: {len(conversation_history) if conversation_history else 0}")
        print(f"  最大工具调用轮数: {max_tool_calls}")
        print(f"{'='*60}")

        if not self.llm.connected:
            print(f"  ❌ LLM 服务未连接")
            yield {
                "type": "error",
                "content": "LLM 服务未连接，请检查 LLM_API_KEY 配置。",
                "timings": timings,
            }
            return

        t0 = time.perf_counter()
        context_docs = self.vector_store.query(user_query)
        context_text = "\n".join([str(doc) for doc in context_docs])
        elapsed = round(time.perf_counter() - t0, 3)
        timings["vector_search"] = elapsed
        print(f"\n  [1] 向量检索完成 | 耗时: {elapsed}s")
        print(f"      检索到 {len(context_docs)} 条知识库文档")
        if context_docs:
            for i, doc in enumerate(context_docs[:3]):
                preview = str(doc)[:100].replace('\n', ' ')
                print(f"      文档[{i}]: {preview}...")

        system_prompt = (
            "你是一个智能商品搜索助手，可以使用工具查询商品信息。\n\n"
            f"参考知识库内容：\n{context_text}\n\n"
            f"{self._build_history_context(conversation_history, user_query)}\n\n"
            "## 调用工具规则（严格遵守）\n"
            "1. 调用 query_products 时，必须提供有效的查询参数（text、keyword、brand 等），禁止传空参数 {}。\n"
            "2. 如果用户的问题引用了对话历史中的商品（如'这几个''上面的''那款''这个牌子'），"
            "   你必须从上方「最近对话中的商品信息」中提取品牌名、商品名等关键词作为 text 参数。\n"
            "3. 仅当需要查询商品信息时才调用工具，如果不需要查询，直接回答用户问题。\n"
            "4. 工具调用结果会自动返回给你，用于生成最终回答。"
        )

        messages: list[Dict[str, Any]] = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": user_query})
        print(f"  构建消息列表: {len(messages)} 条 (含 system + 历史 + 当前问题)")

        tools = [get_tool_spec()]

        llm_call_total = 0.0
        tool_call_total = 0.0
        llm_rounds = 0
        tool_rounds = 0
        consecutive_empty_params = 0

        for round_idx in range(max_tool_calls):
            print(f"\n  ── LLM 第 {round_idx + 1} 轮调用 ──")
            print(f"      发送消息数: {len(messages)}")

            t1 = time.perf_counter()
            try:
                response = await self.llm.chat_with_tools(
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                )
            except Exception as e:
                elapsed = round(time.perf_counter() - t1, 3)
                print(f"      ❌ LLM 调用异常 | 耗时: {elapsed}s | {type(e).__name__}: {e}")
                timings["llm_calls"] = round(llm_call_total + elapsed, 3)
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

            print(f"      LLM 响应完成 | 耗时: {elapsed}s{usage_info}")

            if assistant_message.content:
                content_preview = assistant_message.content[:500].replace('\n', '\n      │ ')
                print(f"      LLM 回复内容:")
                print(f"      │ {content_preview}")
                if len(assistant_message.content) > 500:
                    print(f"      │ ... (共 {len(assistant_message.content)} 字符)")
            else:
                print(f"      LLM 回复内容: (空，仅工具调用)")

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
                print(f"      ✅ 无工具调用，开始流式返回文本")
                print(f"      回复预览: {reply_preview}...")

                timings["llm_calls"] = round(llm_call_total, 3)
                timings["llm_rounds"] = llm_rounds
                timings["tool_calls"] = round(tool_call_total, 3)
                timings["tool_rounds"] = tool_rounds

                async for chunk in self.llm.chat_stream(messages[:-1] + [{"role": "user", "content": user_query}]):
                    yield {
                        "type": "content",
                        "content": chunk,
                        "timings": None,
                    }

                timings["total"] = round(time.perf_counter() - t_total_start, 3)
                self._print_timings_summary(timings)
                yield {
                    "type": "done",
                    "content": "",
                    "timings": timings,
                }
                return

            print(f"      🔧 触发 {len(assistant_message.tool_calls)} 个工具调用:")
            t2 = time.perf_counter()
            round_has_empty = False
            for tc in assistant_message.tool_calls:
                tool_name = tc.function.name
                try:
                    arguments = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    arguments = {}

                print(f"         → 工具: {tool_name}")
                print(f"           参数: {json.dumps(arguments, ensure_ascii=False)[:300]}")

                has_valid_param = any(arguments.get(k) for k in ["text", "keyword", "brand", "category", "sub_category", "attr_filters"])
                if not has_valid_param:
                    round_has_empty = True

                tool_start = time.perf_counter()
                result = run_tool(tool_name, arguments)
                tool_elapsed = round(time.perf_counter() - tool_start, 3)

                result_total = result.get("total", 0) if isinstance(result, dict) else 0
                result_ok = result.get("ok", None) if isinstance(result, dict) else None
                print(f"           结果: ok={result_ok}, total={result_total}, 耗时={tool_elapsed}s")

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
                print(f"      ⚠️  检测到空参数调用 (连续 {consecutive_empty_params} 次)")
                if consecutive_empty_params >= 2:
                    print(f"      🛑 断路器触发：连续空参数，提前退出工具循环，转为纯文本回复")
                    break
            else:
                consecutive_empty_params = 0

        print(f"\n  ── 工具调用轮数已耗尽，执行最终流式 LLM 调用 ──")
        print(f"      发送消息数: {len(messages)}")
        t3 = time.perf_counter()

        timings["llm_calls"] = round(llm_call_total, 3)
        timings["llm_rounds"] = llm_rounds
        timings["tool_calls"] = round(tool_call_total, 3)
        timings["tool_rounds"] = tool_rounds

        try:
            async for chunk in self.llm.chat_stream(messages):
                yield {
                    "type": "content",
                    "content": chunk,
                    "timings": None,
                }
            elapsed = round(time.perf_counter() - t3, 3)
            print(f"      LLM 流式响应完成 | 耗时: {elapsed}s")
        except Exception as e:
            elapsed = round(time.perf_counter() - t3, 3)
            print(f"      ❌ 最终 LLM 调用异常 | 耗时: {elapsed}s | {type(e).__name__}: {e}")
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
        print(f"\n  {'─'*40}")
        print(f"  耗时汇总:")
        print(f"    向量检索: {timings.get('vector_search', '-')}s")
        print(f"    LLM推理: {timings.get('llm_calls', '-')}s ({timings.get('llm_rounds', '?')}轮)")
        print(f"    工具查询: {timings.get('tool_calls', '-')}s ({timings.get('tool_rounds', '?')}轮)")
        print(f"    总计:     {timings.get('total', '-')}s")
        print(f"  {'─'*40}")
        print(f"{'='*60}\n")

    @staticmethod
    def _build_history_context(
        conversation_history: Optional[List[Dict[str, str]]],
        user_query: str
    ) -> str:
        """从对话历史和工具结果中提取商品信息，注入 system prompt 帮助 LLM 定位关键词"""
        if not conversation_history:
            return ""

        import re

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