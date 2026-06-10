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


class PurchaseIntentTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sqlite_product_search_service.initialize()

    def test_search_plan_infers_browsing_and_purchase_ready_intents(self):
        browsing_plan = ToolChatService._normalize_search_plan(
            {"target_product": "跑步耳机", "query_text": "跑步耳机"},
            "随便看看有没有适合跑步的耳机",
        )
        purchase_plan = ToolChatService._normalize_search_plan(
            {"target_product": "跑步耳机", "query_text": "跑步耳机"},
            "我想买个跑步耳机",
        )
        recommend_plan = ToolChatService._normalize_search_plan(
            {"target_product": "防晒霜", "query_text": "防晒霜"},
            "推荐一款防晒霜",
        )
        invalid_plan = ToolChatService._normalize_search_plan(
            {"target_product": "防晒霜", "query_text": "防晒霜", "purchase_intent": "maybe"},
            "随便看看防晒霜",
        )

        self.assertEqual(browsing_plan.get("purchase_intent"), "browsing")
        self.assertEqual(purchase_plan.get("purchase_intent"), "purchase_ready")
        self.assertEqual(recommend_plan.get("purchase_intent"), "purchase_ready")
        self.assertEqual(invalid_plan.get("purchase_intent"), "purchase_ready")

    def test_browsing_final_prompt_uses_light_recommendation_guidance(self):
        messages = ToolChatService._build_final_recommendation_messages(
            "你是导购助手。",
            "用户在探索跑步耳机。",
            [],
            "随便看看有没有适合跑步的耳机",
            [],
            {
                "purchase_intent": "browsing",
                "purchase_intent_reason": "用户表达为先浏览、了解或看看可选项",
            },
        )
        system_content = messages[0]["content"]

        self.assertIn("purchase_intent=browsing", system_content)
        self.assertIn("轻推荐、不催买", system_content)
        self.assertIn("可以先看这几个方向", system_content)
        self.assertIn("对比建议", system_content)


class SearchPlanContextExclusionComparisonTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sqlite_product_search_service.initialize()

    def test_search_plan_infers_followup_exclusion_comparison_and_price_fields(self):
        cheaper_plan = ToolChatService._normalize_search_plan(
            {"target_product": "跑步鞋", "query_text": "再便宜点的呢"},
            "再便宜点的呢",
        )
        excluded_plan = ToolChatService._normalize_search_plan(
            {"target_product": "跑步鞋", "query_text": "除了耐克还有什么跑步鞋"},
            "除了耐克还有什么跑步鞋",
        )
        alcohol_plan = ToolChatService._normalize_search_plan(
            {"target_product": "防晒", "query_text": "不要含酒精的防晒"},
            "不要含酒精的防晒",
        )
        comparison_plan = ToolChatService._normalize_search_plan(
            {"target_product": "防晒", "query_text": "对比这两款防晒"},
            "对比这两款防晒，重点看价格和成分",
        )

        self.assertTrue(cheaper_plan.get("is_followup"))
        self.assertEqual(cheaper_plan.get("price_preference"), "cheaper")
        self.assertIn("Nike", excluded_plan.get("excluded_brands", []))
        self.assertNotIn("耐克", excluded_plan.get("query_text", ""))
        self.assertIn("酒精", alcohol_plan.get("excluded_terms", []))
        self.assertTrue(comparison_plan.get("comparison_intent"))
        self.assertIn("价格", comparison_plan.get("comparison_dimensions", []))
        self.assertIn("成分", comparison_plan.get("comparison_dimensions", []))

    def test_target_products_filter_excluded_brand_and_alcohol_with_search_docs(self):
        brand_plan = ToolChatService._normalize_search_plan(
            {
                "target_product": "跑步鞋",
                "query_text": "跑步鞋",
                "allowed_categories": ["服饰运动"],
                "excluded_brands": ["Nike"],
            },
            "除了耐克还有什么跑步鞋",
        )
        brand_targets = ToolChatService._build_target_products(
            direct_products=[
                {"product_id": "p_clothes_007"},
                {"product_id": "p_clothes_004"},
            ],
            tool_products=[],
            user_query="除了耐克还有什么跑步鞋",
            search_plan=brand_plan,
        )

        self.assertTrue(brand_targets)
        self.assertTrue(all("nike" not in (p.get("brand") or "").lower() and "耐克" not in (p.get("brand") or "") for p in brand_targets))

        alcohol_plan = ToolChatService._normalize_search_plan(
            {
                "target_product": "防晒",
                "query_text": "防晒",
                "allowed_categories": ["美妆护肤"],
                "excluded_terms": ["酒精"],
            },
            "不要含酒精的防晒",
        )
        alcohol_targets = ToolChatService._build_target_products(
            direct_products=[
                {"product_id": "p_beauty_010"},
                {"product_id": "p_beauty_006"},
            ],
            tool_products=[],
            user_query="不要含酒精的防晒",
            search_plan=alcohol_plan,
        )

        target_ids = [item.get("product_id") for item in alcohol_targets]
        self.assertIn("p_beauty_006", target_ids)
        self.assertNotIn("p_beauty_010", target_ids)

    def test_price_preference_reorders_candidates(self):
        plan = ToolChatService._normalize_search_plan(
            {
                "target_product": "美妆",
                "query_text": "美妆",
                "price_preference": "cheaper",
            },
            "再便宜点的呢",
        )
        products = [
            {"product_id": "expensive", "base_price": 300},
            {"product_id": "cheap", "base_price": 99},
        ]

        ordered = ToolChatService._apply_price_preference(products, plan)

        self.assertEqual([item["product_id"] for item in ordered], ["cheap", "expensive"])

    def test_comparison_final_prompt_requires_markdown_table(self):
        messages = ToolChatService._build_final_recommendation_messages(
            "你是导购助手。",
            "用户想比较两款防晒。",
            [
                {"rank": 1, "title": "防晒A", "brand": "A", "category": "美妆护肤", "sub_category": "防晒", "base_price": 100, "match_type": "direct"},
                {"rank": 2, "title": "防晒B", "brand": "B", "category": "美妆护肤", "sub_category": "防晒", "base_price": 160, "match_type": "direct"},
            ],
            "对比这两款防晒，重点看价格和成分",
            [],
            {
                "comparison_intent": True,
                "comparison_dimensions": ["价格", "成分"],
                "excluded_terms": ["酒精"],
                "is_followup": True,
                "context_carryover": "上一轮推荐的两款防晒",
            },
        )
        system_content = messages[0]["content"]

        self.assertIn("Markdown 表格", system_content)
        self.assertIn("价格", system_content)
        self.assertIn("成分", system_content)
        self.assertIn("已排除关键词/属性", system_content)
        self.assertIn("多轮追问口径", system_content)


class ToolChatStreamPipelineTest(unittest.IsolatedAsyncioTestCase):
    def test_direct_reply_detects_non_whitelisted_product_mentions(self):
        selected_products = [
            {
                "product_id": "p_food_010",
                "title": "良品铺子 肉松饼1000g/箱 松软糕点休闲零食早餐代餐点心",
                "brand": "良品铺子",
                "recommendation_role": "primary",
            }
        ]
        candidate_products = [
            *selected_products,
            {
                "product_id": "p_food_009",
                "title": "三只松鼠 每日坚果750g/30袋 混合坚果仁干果礼盒独立小包装",
                "brand": "三只松鼠",
            },
        ]
        reply = "推荐良品铺子肉松饼，另外三只松鼠每日坚果也适合办公室采购。"

        leaked = ToolChatService._mentioned_non_whitelisted_products(reply, selected_products, candidate_products)
        covers = ToolChatService._direct_reply_covers_products(reply, selected_products, candidate_products)

        self.assertEqual([item.get("product_id") for item in leaked], ["p_food_009"])
        self.assertFalse(covers)

    def test_direct_reply_mention_detection_uses_candidate_distinctive_terms(self):
        selected_products = [
            {
                "product_id": "p_food_004",
                "title": "元气森林 0糖0脂0卡 白桃味气泡水480ml 碳酸饮料即饮苏打型饮品",
                "brand": "元气森林",
                "recommendation_role": "primary",
            }
        ]
        candidate_products = [
            *selected_products,
            {
                "product_id": "p_food_024",
                "title": "元气森林 白葡萄味 苏打气泡水 480ml×12 0糖0脂0卡",
                "brand": "元气森林",
            },
        ]

        generic_reply = "元气森林气泡水都挺清爽，0糖0脂0卡，适合办公室日常喝。"
        specific_reply = "元气森林白葡萄味整箱更适合囤货。"

        generic_leaked = ToolChatService._mentioned_non_whitelisted_products(generic_reply, selected_products, candidate_products)
        specific_leaked = ToolChatService._mentioned_non_whitelisted_products(specific_reply, selected_products, candidate_products)

        self.assertEqual(generic_leaked, [])
        self.assertEqual([item.get("product_id") for item in specific_leaked], ["p_food_024"])

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

    async def test_comparison_direct_reply_forces_constrained_markdown_final(self):
        class FakeLLM:
            connected = True
            model = "fake"

            async def chat_stream(self, messages, model=None, model_config=None):
                self.messages = messages
                yield "| 维度 | 袋装五连包 | 桶装整箱 |\n| --- | --- | --- |\n| 价格 | 18.9元 | 45元 |"

        fake_llm = FakeLLM()
        service = ToolChatService(vector_store=SimpleNamespace(), llm=fake_llm)
        selected_products = [
            {
                "rank": 1,
                "product_id": "p_food_021",
                "title": "康师傅 红烧牛肉面 方便面袋装 114g×5 袋 五连包整袋",
                "brand": "康师傅",
                "category": "食品饮料",
                "sub_category": "方便食品",
                "base_price": 18.9,
                "match_type": "direct",
                "recommendation_role": "primary",
            },
            {
                "rank": 2,
                "product_id": "p_food_011",
                "title": "康师傅 经典红烧牛肉面110g*12桶装方便面泡面速食面整箱装",
                "brand": "康师傅",
                "category": "食品饮料",
                "sub_category": "方便食品",
                "base_price": 45,
                "match_type": "direct",
                "recommendation_role": "supporting",
            },
        ]

        def fake_selected_payloads(ctx):
            return selected_products, ["p_food_021", "p_food_011"], [
                {
                    "type": "selected_products",
                    "content": "已选定目标商品",
                    "selected_product_ids": ["p_food_021", "p_food_011"],
                    "selected_products": selected_products,
                    "timings": None,
                }
            ]

        service._stream_selected_product_payloads = fake_selected_payloads
        ctx = _StreamPipelineContext(
            user_query="两种红烧牛肉面哪一个更划算",
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
            search_plan={
                "comparison_intent": True,
                "comparison_dimensions": ["价格", "品类/规格", "推荐结论"],
            },
        )
        direct_message = SimpleNamespace(
            content="康师傅袋装五连包更划算，桶装整箱更适合出行。",
            tool_calls=None,
        )

        chunks = [chunk async for chunk in service._stream_stage_direct_reply_finalization(ctx, direct_message)]
        organizing = next(
            chunk
            for chunk in chunks
            if chunk.get("phase") == "organizing_results" and chunk.get("mode") == "assistant_direct_reply"
        )
        content = "".join(chunk.get("content", "") for chunk in chunks if chunk.get("type") == "content")

        self.assertTrue(organizing.get("reply_covers_targets"))
        self.assertTrue(organizing.get("comparison_requires_constrained_final"))
        self.assertTrue(organizing.get("needs_constrained_final"))
        self.assertIn("| 维度 |", content)

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

        async def fake_context_docs(query, conversation_history=None):
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
