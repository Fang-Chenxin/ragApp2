from __future__ import annotations

import asyncio
import shutil
import sys
import tempfile
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
from service.tool_chat_service import ToolChatService
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


class ToolChatStreamPipelineTest(unittest.IsolatedAsyncioTestCase):
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
