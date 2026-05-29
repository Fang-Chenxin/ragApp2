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


class DummyLLM:
    connected = True

    def __init__(self):
        self.chat_stream_calls = 0
        self.chat_with_tools_calls = 0

    async def chat_stream(self, messages):
        self.chat_stream_calls += 1
        if self.chat_stream_calls == 1:
            yield "内部需求分析：用户想先快速确认需求方向。"
        else:
            yield "最终回复：我可以帮你继续细化需求。"

    async def chat_with_tools(self, messages, tools=None, tool_choice="auto", temperature=None, thinking_type="enabled", reasoning_effort="medium"):
        self.chat_with_tools_calls += 1
        return DummyResponse("最终回复：我可以帮你继续细化需求。")


class ToolChatServiceStreamingTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = ToolChatService(DummyVectorStore(), DummyLLM())

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


if __name__ == "__main__":
    unittest.main()
