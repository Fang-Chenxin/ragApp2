from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from api.chat import ChatMessage, ChatRequest, chat_endpoint
from service.history_service import HistoryService
from service.product_search.query_tool import run_tool
from service.product_search.sqlite_search import sqlite_product_search_service
from service.tool_chat_service import ToolChatService
from service.tool_chat.stream_context import _StreamPipelineContext
from service.tool_chat.stream_context import _StreamTaskGroup
from service.tool_chat.stream_pipeline import settings as tool_chat_settings


class HistoryServiceConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="history-service-test-")
        self.service = HistoryService()
        self.service.storage_path = self.temp_dir

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_save_message_keeps_all_concurrent_writes(self):
        user_id = "concurrent-user"
        conv_id = self.service.create_conversation(user_id, "并发测试")

        def save(index: int) -> None:
            self.service.save_message(user_id, conv_id, "user", f"message-{index}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(save, range(40)))

        history = self.service.load_history(user_id, conv_id)
        contents = {item["content"] for item in history}

        self.assertEqual(len(history), 40)
        self.assertEqual(contents, {f"message-{index}" for index in range(40)})
        self.assertEqual(self.service.get_history_count(user_id, conv_id), 40)
        conversation = next(item for item in self.service.get_conversations(user_id) if item["conv_id"] == conv_id)
        self.assertEqual(conversation["message_count"], 40)


class StreamHistorySaveTest(unittest.IsolatedAsyncioTestCase):
    async def test_stream_error_after_content_still_saves_partial_reply(self):
        history_service = HistoryService()
        temp_dir = tempfile.mkdtemp(prefix="stream-history-test-")
        history_service.storage_path = temp_dir

        class FakeToolChatService:
            async def chat_with_tools_stream(self, **kwargs):
                yield {"type": "analysis", "content": "分析内容", "summary": "分析摘要"}
                yield {"type": "content", "content": "已经生成的回复", "timings": None}
                raise RuntimeError("stream interrupted")

        fake_llm_service = SimpleNamespace(connected=False)
        request = ChatRequest(
            messages=[ChatMessage(role="user", content="之前的问题")],
            user_query="帮我推荐手机",
            user_id="stream-user",
            model_config={
                "id": "fake-model",
                "name": "fake-model",
                "source": "test",
                "base_url": "http://localhost/v1",
                "api_key": "test-key",
            },
        )

        try:
            with patch("service.history_service", history_service), \
                 patch("service.tool_chat_service", FakeToolChatService()), \
                 patch("service.llm_service", fake_llm_service):
                response = await chat_endpoint(request)
                chunks = []
                async for chunk in response.body_iterator:
                    chunks.append(chunk.decode("utf-8") if isinstance(chunk, bytes) else chunk)

            self.assertTrue(any("stream interrupted" in chunk for chunk in chunks))
            conv_id = history_service.get_current_conversation("stream-user")
            saved = history_service.load_history("stream-user", conv_id)
            self.assertEqual([item["role"] for item in saved], ["user", "assistant"])
            self.assertEqual(saved[0]["content"], "帮我推荐手机")
            self.assertEqual(saved[1]["content"], "已经生成的回复")
            self.assertEqual(saved[1]["thinking"], "分析内容")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


class StreamTaskGroupTest(unittest.IsolatedAsyncioTestCase):
    async def test_cancel_pending_cancels_unfinished_tasks(self):
        task_group = _StreamTaskGroup()
        cancelled = False

        async def wait_forever():
            nonlocal cancelled
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled = True
                raise

        task = task_group.create(wait_forever())
        await asyncio.sleep(0)

        await task_group.cancel_pending()

        self.assertTrue(task.cancelled())
        self.assertTrue(cancelled)


class ToolPlanningMessagesTest(unittest.TestCase):
    def test_build_tool_planning_messages_keeps_expected_order(self):
        messages = ToolChatService._build_tool_planning_messages(
            [{"role": "assistant", "content": "上一轮回复"}],
            "当前问题",
        )

        self.assertEqual([item["role"] for item in messages], ["system", "assistant", "user"])
        self.assertIn("商品查询规划子角色", messages[0]["content"])
        self.assertEqual(messages[1]["content"], "上一轮回复")
        self.assertEqual(messages[2]["content"], "当前问题")


class CategoryValidationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sqlite_product_search_service.initialize()
        if not sqlite_product_search_service.get_category_tree():
            raise unittest.SkipTest("SQLite 商品数据库不可用，跳过类目枚举校验测试")

    def test_tool_rejects_unknown_sub_category(self):
        result = run_tool(
            "query_products",
            {
                "text": "华为生态设备",
                "category": "数码电子",
                "sub_category": "智能数码硬件",
            },
        )

        self.assertFalse(result.get("ok"))
        self.assertTrue(result.get("validation_error"))
        self.assertIn("不在数据库子类目范围内", result.get("error", ""))

    def test_search_plan_clears_unknown_sub_category(self):
        plan = ToolChatService._normalize_search_plan(
            {
                "target_product": "华为生态设备",
                "target_category": "数码电子",
                "target_sub_category": "智能数码硬件",
                "query_text": "华为生态设备",
                "allowed_categories": ["数码电子"],
            },
            "华为生态设备",
        )

        self.assertIsNotNone(plan)
        self.assertEqual(plan.get("target_sub_category"), "")


class ToolChatStreamPipelineTest(unittest.IsolatedAsyncioTestCase):
    async def test_final_stage_streams_text_before_product_cards(self):
        class FakeLLM:
            connected = True
            model = "fake"

            async def chat_stream(self, messages, model=None, model_config=None):
                yield "最终推荐正文"

        service = ToolChatService(vector_store=SimpleNamespace(), llm=FakeLLM())
        selected_products = [
            {
                "rank": 1,
                "product_id": "p_test_001",
                "title": "测试办公设备",
                "brand": "测试品牌",
                "category": "数码电子",
                "sub_category": "办公设备",
                "base_price": 1999,
                "match_type": "direct",
            }
        ]

        def fake_selected_payloads(ctx):
            return selected_products, ["p_test_001"], [
                {
                    "type": "selected_products",
                    "content": "已选定目标商品",
                    "selected_product_ids": ["p_test_001"],
                    "selected_products": selected_products,
                    "timings": None,
                }
            ]

        service._stream_selected_product_payloads = fake_selected_payloads
        ctx = _StreamPipelineContext(
            user_query="办公室行政好用设备",
            conversation_history=[],
            max_tool_calls=5,
            model="fake",
            model_config={"api_key": "test"},
            timings={},
            t_total_start=time.perf_counter(),
            messages=[],
            tools=[],
            task_group=_StreamTaskGroup(),
            final_system_prompt="你是导购助手。",
        )

        chunks = [chunk async for chunk in service._stream_stage_final_reply(ctx)]
        content_index = next(i for i, chunk in enumerate(chunks) if chunk.get("type") == "content")
        products_index = next(i for i, chunk in enumerate(chunks) if chunk.get("type") == "selected_products")

        self.assertLess(content_index, products_index)
        self.assertIn("商品卡片先放上来", chunks[content_index].get("content", ""))

    async def test_pipeline_stream_can_complete_without_tools(self):
        class FakeLLM:
            connected = True
            model = "fake"

            async def chat_stream(self, messages, model=None, model_config=None):
                yield "分析"

            async def chat_with_tools(self, **kwargs):
                message = SimpleNamespace(content="这是直接回复", tool_calls=None)
                return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=None)

        service = ToolChatService(vector_store=SimpleNamespace(), llm=FakeLLM())

        async def fake_context_docs(query):
            return [], None

        async def fake_rerank(query, docs, model=None, model_config=None):
            return docs, None, {"results": [], "kept": []}

        async def fake_direct_query(ctx):
            return [], 0.0, None

        service._query_context_docs_with_timeout = fake_context_docs
        service._rerank_context_docs_with_llm = fake_rerank
        service._stream_query_direct_selected_products = fake_direct_query

        with patch.object(tool_chat_settings, "tool_chat_parallel_enabled", False):
            chunks = [
                chunk
                async for chunk in service.chat_with_tools_stream(
                    "随便聊聊",
                    conversation_history=[],
                    model="fake",
                    model_config={"api_key": "test"},
                )
            ]

        self.assertTrue(any(chunk.get("type") == "content" for chunk in chunks))
        self.assertEqual(chunks[-1].get("type"), "done")
        self.assertIn("vector_search", chunks[-1].get("timings") or {})


if __name__ == "__main__":
    unittest.main()
