"""工具聊天服务流式输出回归测试。"""
from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(ROOT, "backend")
sys.path.insert(0, ROOT)
sys.path.insert(0, BACKEND)

from backend.service.tool_chat_service import ToolChatService


class DummyVectorStore:
    def query(self, query_text: str, top_k: int | None = None):
        return ["知识库线索仅用于内部分析"]


class RankedVectorStore:
    def query_with_sources(self, query_text: str, top_k: int | None = None):
        return [
            {
                "id": "wrong",
                "content": "这是一条运动裤评价。",
                "metadata": {
                    "product_id": "p_clothes_006",
                    "title": "Nike 运动长裤",
                    "chunk_type": "review",
                },
                "distance": 0.2,
            },
            {
                "id": "right",
                "content": "这支眉笔适合新手，细头容易描画。",
                "metadata": {
                    "product_id": "p_beauty_025",
                    "title": "花西子螺黛生花眉笔",
                    "chunk_type": "review",
                },
                "distance": 0.4,
            },
        ]


class DummyMessage:
    def __init__(self, content: str, tool_calls=None):
        self.content = content
        self.tool_calls = tool_calls


class DummyChoice:
    def __init__(self, message: DummyMessage):
        self.message = message


class DummyResponse:
    def __init__(self, content: str, tool_calls=None):
        self.choices = [DummyChoice(DummyMessage(content, tool_calls))]
        self.usage = None


class DummyToolFunction:
    def __init__(self, name: str, arguments: str):
        self.name = name
        self.arguments = arguments


class DummyToolCall:
    def __init__(self, tool_call_id: str, name: str, arguments: str):
        self.id = tool_call_id
        self.function = DummyToolFunction(name, arguments)


class DummyLLM:
    connected = True

    def __init__(self):
        self.chat_stream_calls = 0
        self.chat_with_tools_calls = 0

    async def chat_stream(self, messages, model=None, model_config=None):
        self.chat_stream_calls += 1
        if self.chat_stream_calls == 1:
            yield "内部需求分析：用户想先快速确认需求方向。"
        else:
            yield "最终回复：我可以帮你继续细化需求。"

    async def chat(self, messages, temperature=None, model=None, model_config=None):
        return '{"results":[{"id":"right","score":5,"reason":"直接回答眉笔新手需求"},{"id":"wrong","score":1,"reason":"运动裤无关"}]}'

    async def chat_with_tools(self, messages, tools=None, tool_choice="auto", temperature=None, thinking_type="enabled", reasoning_effort="medium", model=None, model_config=None):
        self.chat_with_tools_calls += 1
        return DummyResponse("最终回复：我可以帮你继续细化需求。")


class RankOnlyRerankLLM(DummyLLM):
    async def chat(self, messages, temperature=None, model=None, model_config=None):
        return '{"results":[{"rank":1,"score":5,"reason":"序号命中"}]}'


class ToolCallingLLM(DummyLLM):
    async def chat_stream(self, messages, model=None, model_config=None):
        self.chat_stream_calls += 1
        if self.chat_stream_calls == 1:
            yield "用户想找适合新手的眉笔，我会先查询相关商品。"
        else:
            yield "最终回复：推荐 p_beauty_025，这支眉笔细头好控制，适合新手。"

    async def chat_with_tools(self, messages, tools=None, tool_choice="auto", temperature=None, thinking_type="enabled", reasoning_effort="medium", model=None, model_config=None):
        self.chat_with_tools_calls += 1
        if self.chat_with_tools_calls > 1:
            return DummyResponse("最终回复：推荐 p_beauty_025，这支眉笔细头好控制，适合新手。")
        tool_call = DummyToolCall(
            "call_query_products_1",
            "query_products",
            '{"text":"眉笔","limit":3}',
        )
        return DummyResponse("", [tool_call])


class MultiRoundToolCallingLLM(ToolCallingLLM):
    async def chat_with_tools(self, messages, tools=None, tool_choice="auto", temperature=None, thinking_type="enabled", reasoning_effort="medium", model=None, model_config=None):
        self.chat_with_tools_calls += 1
        if self.chat_with_tools_calls == 1:
            return DummyResponse("", [
                DummyToolCall("call_beauty", "query_products", '{"text":"眉笔","limit":3}')
            ])
        if self.chat_with_tools_calls == 2:
            return DummyResponse("", [
                DummyToolCall("call_food", "query_products", '{"text":"零食","limit":3}')
            ])
        return DummyResponse("最终回复：推荐 p_beauty_025，这支眉笔细头好控制，适合新手。")


class MissingIdFinalLLM(ToolCallingLLM):
    async def chat_stream(self, messages, model=None, model_config=None):
        self.chat_stream_calls += 1
        if self.chat_stream_calls == 1:
            yield "用户想找适合新手的眉笔，我会先查询相关商品。"
        else:
            yield "最终回复：推荐 p_beauty_025，这支眉笔细头好控制，适合新手。"

    async def chat_with_tools(self, messages, tools=None, tool_choice="auto", temperature=None, thinking_type="enabled", reasoning_effort="medium", model=None, model_config=None):
        self.chat_with_tools_calls += 1
        if self.chat_with_tools_calls > 1:
            return DummyResponse("最终回复：推荐花西子螺黛生花眉笔，细头好控制，适合新手。")
        return DummyResponse("", [
            DummyToolCall("call_beauty", "query_products", '{"text":"眉笔","limit":3}')
        ])


class MaxRoundWrongFinalLLM(ToolCallingLLM):
    async def chat_stream(self, messages, model=None, model_config=None):
        self.chat_stream_calls += 1
        if self.chat_stream_calls == 1:
            yield "用户想找适合新手的眉笔，我会先查询相关商品。"
        else:
            yield "错误最终回复：推荐 p_beauty_009。"

    async def chat_with_tools(self, messages, tools=None, tool_choice="auto", temperature=None, thinking_type="enabled", reasoning_effort="medium", model=None, model_config=None):
        self.chat_with_tools_calls += 1
        return DummyResponse("", [
            DummyToolCall(f"call_food_{self.chat_with_tools_calls}", "query_products", '{"text":"零食","limit":3}')
        ])


class ToolChatServiceStreamingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = ToolChatService(DummyVectorStore(), DummyLLM())

    @staticmethod
    def _print_chunk_trace(title: str, chunks: list[dict]):
        print(f"\n{title}")
        for index, chunk in enumerate(chunks, start=1):
            chunk_type = chunk.get("type")
            phase = chunk.get("phase")
            if chunk_type == "status":
                print(f"{index}. status phase={phase} content={chunk.get('content')}")
            elif chunk_type == "analysis":
                print(f"{index}. analysis summary={chunk.get('summary')}")
            elif chunk_type == "rag_sources":
                sources = chunk.get("rag_sources") or []
                source_ids = [source.get("product_id") for source in sources]
                print(f"{index}. rag_sources product_ids={source_ids}")
            elif chunk_type == "debug":
                print(f"{index}. debug phase={phase} title={chunk.get('title')}")
            elif chunk_type == "selected_products":
                print(f"{index}. selected_products ids={chunk.get('selected_product_ids')}")
            elif chunk_type == "content":
                print(f"{index}. content {chunk.get('content')}")
            elif chunk_type == "done":
                print(f"{index}. done timings={chunk.get('timings')}")
            else:
                print(f"{index}. {chunk_type} phase={phase} content={chunk.get('content')}")

    async def test_need_analysis_text_is_not_streamed_as_user_content(self):
        chunks = []
        async for chunk in self.service.chat_with_tools_stream("我饿了"):
            chunks.append(chunk)

        content_chunks = [chunk["content"] for chunk in chunks if chunk.get("type") == "content"]
        self.assertTrue(content_chunks)
        self.assertNotIn("内部需求分析", "".join(content_chunks))
        self.assertIn("最终回复", "".join(content_chunks))

    async def test_need_analysis_text_is_not_returned_by_non_streaming_chat(self):
        result = await self.service.chat_with_tools("我饿了")

        self.assertIn("最终回复", result["reply"])
        self.assertNotIn("中间分析不应直接展示给用户", result["reply"])

    async def test_normal_tool_dialogue_streams_final_response(self):
        service = ToolChatService(DummyVectorStore(), ToolCallingLLM())

        async def fake_tool_worker(tool_call_id, tool_name, arguments):
            return {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "result": {
                    "ok": True,
                    "total": 1,
                    "items": [
                        {
                            "product_id": "p_beauty_025",
                            "title": "花西子螺黛生花眉笔",
                            "brand": "花西子",
                            "category": "美妆护肤",
                            "sub_category": "眉笔",
                            "base_price": 89.0,
                        }
                    ],
                },
                "ok": True,
                "total": 1,
                "elapsed": 0.001,
                "error": None,
            }

        service._run_tool_worker = fake_tool_worker

        chunks = []
        async for chunk in service.chat_with_tools_stream("新手适合用什么眉笔", max_tool_calls=3):
            chunks.append(chunk)

        self._print_chunk_trace("正常工具对话流式事件:", chunks)

        tool_done_chunks = [chunk for chunk in chunks if chunk.get("phase") == "tool_done"]
        selected_product_chunks = [chunk for chunk in chunks if chunk.get("type") == "selected_products"]
        content = "".join(chunk["content"] for chunk in chunks if chunk.get("type") == "content")
        phases = [chunk.get("phase") for chunk in chunks if chunk.get("phase")]

        self.assertIn("retrieving_knowledge", phases)
        self.assertIn("need_analysis", phases)
        self.assertIn("querying_products", phases)
        self.assertIn("tool_done", phases)
        self.assertIn("organizing_results", phases)
        self.assertTrue(tool_done_chunks)
        self.assertTrue(selected_product_chunks)
        self.assertIn("p_beauty_025", selected_product_chunks[0]["selected_product_ids"])
        self.assertIn("最终回复", content)
        self.assertEqual(service.llm.chat_with_tools_calls, 2)
        self.assertEqual(service.llm.chat_stream_calls, 2)

    async def test_streaming_selected_products_accumulates_multiple_tool_rounds(self):
        service = ToolChatService(DummyVectorStore(), MultiRoundToolCallingLLM())

        async def fake_tool_worker(tool_call_id, tool_name, arguments):
            if tool_call_id == "call_beauty":
                item = {
                    "product_id": "p_beauty_025",
                    "title": "花西子螺黛生花眉笔",
                    "brand": "花西子",
                    "category": "美妆护肤",
                    "sub_category": "眉笔",
                    "base_price": 89.0,
                }
            else:
                item = {
                    "product_id": "p_food_003",
                    "title": "测试零食",
                    "brand": "测试品牌",
                    "category": "食品饮料",
                    "sub_category": "零食",
                    "base_price": 19.9,
                }
            return {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "result": {"ok": True, "total": 1, "items": [item]},
                "ok": True,
                "total": 1,
                "elapsed": 0.001,
                "error": None,
            }

        service._run_tool_worker = fake_tool_worker

        chunks = []
        async for chunk in service.chat_with_tools_stream("新手适合用什么眉笔", max_tool_calls=3):
            chunks.append(chunk)

        selected_product_chunks = [chunk for chunk in chunks if chunk.get("type") == "selected_products"]
        self.assertTrue(selected_product_chunks)
        selected_ids = selected_product_chunks[-1]["selected_product_ids"]
        self.assertIn("p_beauty_025", selected_ids)
        self.assertIn("p_food_003", selected_ids)
        self.assertEqual(selected_ids.index("p_beauty_025"), 0)

    async def test_streaming_regenerates_final_when_selected_id_is_missing(self):
        service = ToolChatService(DummyVectorStore(), MissingIdFinalLLM())

        async def fake_tool_worker(tool_call_id, tool_name, arguments):
            return {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "result": {
                    "ok": True,
                    "total": 1,
                    "items": [
                        {
                            "product_id": "p_beauty_025",
                            "title": "花西子螺黛生花眉笔",
                            "brand": "花西子",
                            "category": "美妆护肤",
                            "sub_category": "眉笔",
                            "base_price": 89.0,
                        }
                    ],
                },
                "ok": True,
                "total": 1,
                "elapsed": 0.001,
                "error": None,
            }

        service._run_tool_worker = fake_tool_worker

        chunks = []
        async for chunk in service.chat_with_tools_stream("新手适合用什么眉笔", max_tool_calls=3):
            chunks.append(chunk)

        content = "".join(chunk["content"] for chunk in chunks if chunk.get("type") == "content")
        self.assertIn("p_beauty_025", content)
        self.assertEqual(service.llm.chat_stream_calls, 2)

    async def test_streaming_appends_fallback_when_max_round_final_uses_wrong_id(self):
        service = ToolChatService(DummyVectorStore(), MaxRoundWrongFinalLLM())
        service._query_direct_selected_products = lambda user_query: [
            {
                "product_id": "p_beauty_025",
                "title": "花西子螺黛生花眉笔",
                "brand": "花西子",
                "category": "美妆护肤",
                "sub_category": "眉笔",
                "base_price": 89.0,
            }
        ]

        async def fake_tool_worker(tool_call_id, tool_name, arguments):
            return {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "result": {
                    "ok": True,
                    "total": 1,
                    "items": [
                        {
                            "product_id": "p_food_013",
                            "title": "测试零食",
                            "brand": "测试品牌",
                            "category": "食品饮料",
                            "sub_category": "零食",
                            "base_price": 19.9,
                        }
                    ],
                },
                "ok": True,
                "total": 1,
                "elapsed": 0.001,
                "error": None,
            }

        service._run_tool_worker = fake_tool_worker

        chunks = []
        async for chunk in service.chat_with_tools_stream("新手适合用什么眉笔", max_tool_calls=2):
            chunks.append(chunk)

        selected_chunks = [chunk for chunk in chunks if chunk.get("type") == "selected_products"]
        content = "".join(chunk["content"] for chunk in chunks if chunk.get("type") == "content")
        self.assertTrue(selected_chunks)
        self.assertEqual(selected_chunks[-1]["selected_product_ids"][0], "p_beauty_025")
        self.assertIn("p_beauty_025", content)

    async def test_llm_rerank_filters_and_reorders_rag_sources(self):
        service = ToolChatService(RankedVectorStore(), DummyLLM())

        chunks = []
        async for chunk in service.chat_with_tools_stream("新手适合用什么眉笔"):
            chunks.append(chunk)

        self._print_chunk_trace("LLM RAG 检查与重排事件:", chunks)

        rag_source_chunks = [chunk for chunk in chunks if chunk.get("type") == "rag_sources"]
        self.assertTrue(rag_source_chunks)

        sources = rag_source_chunks[0]["rag_sources"]
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]["product_id"], "p_beauty_025")
        self.assertEqual(sources[0]["llm_rerank"]["score"], 5)

    async def test_llm_rerank_accepts_rank_identifier(self):
        service = ToolChatService(RankedVectorStore(), RankOnlyRerankLLM())

        context_docs = await service._query_context_docs_with_timeout("新手适合用什么眉笔")
        reranked_docs, rerank_error, rerank_debug = await service._rerank_context_docs_with_llm(
            "新手适合用什么眉笔",
            context_docs[0],
        )

        self.assertIsNone(rerank_error)
        self.assertTrue(reranked_docs)
        self.assertEqual(rerank_debug["decision"], "kept_above_threshold")
        self.assertEqual(reranked_docs[0]["metadata"]["product_id"], "p_clothes_006")
        self.assertEqual(reranked_docs[0]["llm_rerank"]["score"], 5)


if __name__ == "__main__":
    unittest.main()
