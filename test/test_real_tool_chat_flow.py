"""真实工具聊天完整流程集成测试。

该测试不使用 LLM / 工具 / RAG 模拟桩，会实际调用：
- ecommerce_agent_dataset/.chroma 中的 Chroma RAG 检索
- ecommerce_agent_dataset/ecommerce.db 中的 SQLite 商品库
- 当前环境配置的 OpenAI-compatible LLM

如果本机没有配置可用 API Key，会跳过测试。
"""
from __future__ import annotations

import os
import sys
import asyncio
import importlib
import logging
import re
import unittest
from pathlib import Path
from typing import Any

# ── 抑制 asyncio debug 噪音（"Executing <Task pending...>" 消息）──
# 这些消息由 asyncio.base_events 的 slow callback 检测发出
# 使用 logging.WARNING 无法抑制 WARNING 级别消息，必须用 ERROR
os.environ["PYTHONASYNCIODEBUG"] = "0"
logging.getLogger("asyncio").setLevel(logging.ERROR)

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from config.settings import settings
from service.llm_service import llm_service
from service.rag_service import embedding_service, vector_store
from service.product_search.sqlite_search import sqlite_product_search_service
from service.tool_chat_service import ToolChatService

# ── 抑制 tool_chat_service 的 logger.warning 干扰输出 ──
# 这些警告信息已在 trace 中结构化展示，无需重复打印
logging.getLogger("service.tool_chat").setLevel(logging.ERROR)

tool_chat_service_module = importlib.import_module("service.tool_chat_service")


def _default_model_config_with_key() -> dict[str, str] | None:
    override_model = os.getenv("REAL_TOOL_CHAT_MODEL", "").strip()
    override_api_key = os.getenv("REAL_TOOL_CHAT_API_KEY", "").strip()
    override_base_url = os.getenv("REAL_TOOL_CHAT_BASE_URL", "").strip()
    if override_model and override_api_key:
        return {
            "id": override_model,
            "name": override_model,
            "source": "real-test-env",
            "base_url": override_base_url or settings.llm_base_url,
            "api_key": override_api_key,
        }

    if settings.llm_model and settings.llm_api_key:
        return {
            "id": settings.llm_model,
            "name": settings.llm_model,
            "source": "LLM_MODEL",
            "base_url": settings.llm_base_url,
            "api_key": settings.llm_api_key,
        }

    return None


_trace_state: dict[str, Any] = {}


def _print_chunk(chunk: dict[str, Any]) -> None:
    """实时打印单个 chunk，每个 chunk 到达时立即输出。"""

    def _pprint(prefix: str, items: list[dict[str, Any]], limit: int = 8) -> None:
        for item in items[:limit]:
            print(
                f"      {prefix} id={item.get('product_id') or item.get('id')} "
                f"title={item.get('title')} cat={item.get('category')}/"
                f"{item.get('sub_category')} "
                f"score={(item.get('llm_rerank') or {}).get('score', '-')} "
                f"dist={item.get('distance')}",
                flush=True,
            )

    def _fmt(decision: str) -> str:
        m = {
            "skipped": "跳过（未启用LLM核验或无候选文档）",
            "skipped_non_structured_docs": "跳过（候选文档非结构化）",
            "timeout_keep_vector_order": "LLM核验超时，保留原始向量排序",
            "error_keep_vector_order": "LLM核验出错，保留原始向量排序",
            "parse_failed_keep_vector_order": "LLM返回内容无法解析，保留原始排序",
            "below_threshold_keep_highest_score": "所有片段未达阈值，保留评分最高的片段",
            "no_matched_docs_keep_vector_order": "LLM未匹配到任何片段，保留原始排序",
            "kept_above_threshold": "通过核验，保留了达到阈值的片段",
        }
        return m.get(decision, decision)

    st = _trace_state
    chunk_type = chunk.get("type")
    phase = chunk.get("phase")

    # ── status 事件 ──
    if chunk_type == "status":
        descs = {
            "retrieving_knowledge": "【并行分支A】向量检索：Chroma知识库搜索最相似文档",
            "reranking_knowledge": "【并行分支A】RAG核验：检查知识片段是否相关，过滤无关内容",
            "need_analysis": "【并行分支B】需求分析：LLM基于用户问题/历史生成内部摘要",
            "querying_products": "【并行分支C】商品查询：LLM调用query_products查询SQLite",
            "tool_done": "【并行分支C✓】工具执行完成",
            "organizing_results": "【汇总阶段】最终回复生成：基于目标商品白名单生成推荐",
        }
        print(f"\n{'─' * 72}", flush=True)
        print(f"  {descs.get(phase, chunk.get('content', ''))}", flush=True)
        print(f"{'─' * 72}", flush=True)

    # ── debug 事件 ──
    elif chunk_type == "debug":
        if phase == "vector_search":
            el = chunk.get("elapsed", 0)
            st["vector_search"] = el
            err = chunk.get("error")
            cands = chunk.get("candidates") or []
            if err:
                print(f"  ⚠ 向量检索出错: {err}", flush=True)
            else:
                print(f"  耗时: {el}s", flush=True)
            print(f"  检索到 {len(cands)} 条知识片段候选：", flush=True)
            _pprint("候选", cands)

        elif phase == "rag_rerank":
            decision = chunk.get("decision", "")
            el = chunk.get("elapsed", 0)
            st["rag_rerank"] = el
            min_s = chunk.get("min_score")
            err = chunk.get("error")
            raw = chunk.get("raw_response", "")
            parsed = chunk.get("parsed_results") or []
            matched = chunk.get("matched") or []
            kept = chunk.get("kept") or []
            print(f"  耗时: {el}s  阈值: >= {min_s}分", flush=True)
            print(f"  核验结论: {_fmt(decision)}", flush=True)
            if err:
                print(f"  ⚠ 原因: {err}", flush=True)
            if raw:
                rp = raw.strip()[:300]
                if len(raw.strip()) > 300:
                    rp += "..."
                print(f"  LLM原始返回: {rp}", flush=True)
            if parsed:
                print(f"  LLM评分明细:", flush=True)
                for r in parsed:
                    print(f"    id={r.get('id')}  score={r.get('score')}  reason={r.get('reason')}", flush=True)
            if matched:
                print(f"  匹配结果:", flush=True)
                for m in matched:
                    print(
                        f"    请求id={m.get('requested_id')} → 商品={m.get('product_id')} "
                        f"({m.get('title')})  score={m.get('score')}  {m.get('reason')}",
                        flush=True,
                    )
            if kept:
                print(f"  最终保留 {len(kept)} 条知识片段:", flush=True)
                _pprint("保留", kept)
            else:
                print(f"  最终保留: 0 条（将使用原始向量排序）", flush=True)

        elif phase == "direct_product_query":
            q = chunk.get("query", "")
            el = chunk.get("elapsed", 0)
            err = chunk.get("error")
            ids = chunk.get("selected_product_ids") or []
            prods = chunk.get("selected_products") or []
            print(f"  后台直查耗时: {el}s", flush=True)
            if err:
                print(f"  ⚠ 直查出错: {err}", flush=True)
            print(f"  直查关键词: {q}", flush=True)
            print(f"  直接命中: {len(ids)} 个商品 → {ids}", flush=True)
            if prods:
                _pprint("直查结果", prods)

        elif phase == "llm_tool_plan":
            rn = chunk.get("round", "?")
            ac = chunk.get("assistant_content", "")
            tcs = chunk.get("tool_calls") or []
            print(f"  LLM第{rn}轮工具调用计划:", flush=True)
            if ac:
                cp = ac.strip()[:200]
                if len(ac.strip()) > 200:
                    cp += "..."
                print(f"    LLM思考内容: {cp}", flush=True)
            for c in tcs:
                print(f"    调用 → {c.get('tool_name')}({c.get('arguments')})", flush=True)

        elif phase == "tool_result":
            tn = chunk.get("tool_name", "")
            ok = chunk.get("ok")
            total = chunk.get("total", 0)
            el = chunk.get("elapsed", 0)
            err = chunk.get("error")
            pids = chunk.get("product_ids") or []
            items = chunk.get("items") or []
            status = "成功" if ok else f"失败: {err}"
            print(f"  工具: {tn}  耗时: {el}s  结果: {status}  命中: {total}条", flush=True)
            if pids:
                print(f"  返回商品ID: {pids}", flush=True)
            if items:
                _pprint("查询结果", items)

        elif phase == "selected_products":
            dids = chunk.get("direct_selected_product_ids") or []
            tids = chunk.get("tool_selected_product_ids") or []
            mids = chunk.get("selected_product_ids") or []
            prods = chunk.get("selected_products") or []
            print(f"  来源合并:", flush=True)
            print(f"    直查命中: {dids or '(无)'}", flush=True)
            print(f"    工具命中: {tids or '(无)'}", flush=True)
            print(f"    合并后(去重): {mids}", flush=True)
            if prods:
                for p in prods:
                    print(
                        f"      目标 rank={p.get('rank')} id={p.get('product_id')} "
                        f"source={p.get('source')} role={p.get('recommendation_role')}",
                        flush=True,
                    )
                _pprint("合并结果", prods)

        elif phase == "organizing_results":
            sids = chunk.get("selected_product_ids") or []
            drids = chunk.get("direct_reply_product_ids") or []
            nc = chunk.get("needs_constrained_final")
            fmc = chunk.get("final_message_count")
            mode = chunk.get("mode", "")
            st["selected_ids"] = sids
            st["final_msg_count"] = fmc
            if mode == "assistant_direct_reply":
                st["final_mode"] = "LLM直接回复" if not nc else "受约束最终整理"
                print(f"  LLM在工具调用轮中已直接给出回复文本", flush=True)
                print(f"  回复中引用的商品ID: {drids or '(无)'}", flush=True)
                print(f"  应引用的商品ID: {sids}", flush=True)
                if chunk.get("comparison_requires_constrained_final") is not None:
                    print(f"  对比需求需结构化回复: {chunk.get('comparison_requires_constrained_final')}", flush=True)
                if nc:
                    print(f"  → 回复未覆盖目标商品或需结构化整理，重新生成", flush=True)
                else:
                    print(f"  → 回复已包含选中商品ID，直接使用", flush=True)
            else:
                st["final_mode"] = "受约束最终整理(工具轮耗尽)"
                print(f"  工具调用轮数已耗尽，进入最终回复生成", flush=True)
                print(f"  选中商品ID: {sids}", flush=True)
                print(f"  发送给LLM的消息数: {fmc}", flush=True)
                print(f"  生成方式: 构建含选中商品约束的prompt → LLM流式生成", flush=True)

        else:
            print(f"  [{phase}] {chunk}", flush=True)

    # ── analysis 事件 ──
    elif chunk_type == "analysis":
        summary = chunk.get("summary", "")
        ti = chunk.get("timings") or {}
        at = ti.get("analysis_calls", 0)
        st["analysis_time"] = at
        if at:  # analysis_done 事件才输出耗时
            print(f"  耗时: {at}s", flush=True)
            print(f"  内部需求摘要(不展示给用户): {summary}", flush=True)
        else:
            # analysis_delta 事件：前 3 条显示片段，后续折叠
            st["analysis_chunk_count"] = st.get("analysis_chunk_count", 0) + 1
            cnt = st["analysis_chunk_count"]
            content_delta = chunk.get("content", "")
            if cnt <= 3:
                pv = content_delta.replace("\n", "\\n")[:60]
                print(f"  [分析片段{cnt}] len={len(content_delta)} → {pv}", flush=True)
            elif cnt == 4:
                print(f"  ... (后续分析片段持续流式输出中) ...", flush=True)

    # ── rag_sources 事件 ──
    elif chunk_type == "rag_sources":
        sources = chunk.get("rag_sources") or []
        sids = [s.get("product_id") for s in sources]
        print(f"  经LLM核验后保留的知识来源商品: {sids}", flush=True)
        for s in sources:
            rr = s.get("llm_rerank") or {}
            print(
                f"    {s.get('product_id')} {s.get('title')} "
                f"score={rr.get('score', '-')} reason={rr.get('reason', '')}",
                flush=True,
            )

    # ── selected_products 事件 ──
    elif chunk_type == "selected_products":
        sids = chunk.get("selected_product_ids") or []
        prods = chunk.get("selected_products") or []
        print(f"  最终选中商品ID: {sids}", flush=True)
        if prods:
            _pprint("选中", prods)

    # ── content 事件 ──
    elif chunk_type == "content":
        c = chunk.get("content") or ""
        st.setdefault("content_parts", []).append(c)
        st["content_count"] = st.get("content_count", 0) + 1
        cnt = st["content_count"]
        pv = c.replace("\n", "\\n")[:100]
        if len(c) > 100:
            pv += "..."
        if cnt <= 5:
            print(f"  [流式片段{cnt}] len={len(c)} → {pv}", flush=True)
        elif cnt == 6:
            print(f"  ... (后续片段持续流式输出中) ...", flush=True)

    # ── done 事件 ──
    elif chunk_type == "done":
        timings = chunk.get("timings") or {}
        st["timings"] = timings

    # ── error 事件 ──
    elif chunk_type == "error":
        print(f"\n  ✗ 错误: {chunk.get('content')}", flush=True)


def _print_summary(chunks: list[dict[str, Any]]) -> None:
    """流程结束后打印最终回复和耗时汇总。"""
    st = _trace_state
    timings = st.get("timings") or {}
    total_time = timings.get("total", 0)
    llm_rounds = timings.get("llm_rounds", "?")
    tool_rounds = timings.get("tool_rounds", "?")
    llm_calls = timings.get("llm_calls", 0)
    tool_calls_time = timings.get("tool_calls", 0)
    analysis_time = timings.get("analysis_calls", 0)
    vector_time = timings.get("vector_search", 0)
    rerank_time = timings.get("rag_rerank", 0)
    measured_parts = sum(
        value
        for value in [
            analysis_time,
            llm_calls,
            tool_calls_time,
            vector_time,
            rerank_time,
        ]
        if isinstance(value, (int, float))
    )
    timing_delta = (
        round(total_time - measured_parts, 3)
        if isinstance(total_time, (int, float))
        else "?"
    )

    # ── 完成信息 ──
    print(f"\n{'=' * 72}", flush=True)
    print(f"  流程完成", flush=True)
    print(f"{'=' * 72}", flush=True)
    print(f"  总耗时: {total_time}s", flush=True)
    print(f"  需求分析LLM: {analysis_time}s", flush=True)
    print(f"  工具/回复LLM: {llm_calls}s ({llm_rounds}轮)", flush=True)
    print(f"  工具查询合计: {tool_calls_time}s ({tool_rounds}轮)", flush=True)
    print(f"  向量检索: {vector_time}s", flush=True)
    print(f"  RAG核验LLM: {rerank_time}s", flush=True)
    print(f"  各阶段串行相加: {round(measured_parts, 3)}s", flush=True)
    if isinstance(timing_delta, (int, float)) and timing_delta < 0:
        print(f"  并行重叠节省估算: {round(abs(timing_delta), 3)}s", flush=True)
    else:
        print(f"  未归类/流式消费开销: {timing_delta}s", flush=True)
    if timings.get("vector_search_error"):
        print(f"  ⚠ 向量检索错误: {timings['vector_search_error']}", flush=True)
    if timings.get("rag_rerank_error"):
        print(f"  ⚠ RAG核验错误: {timings['rag_rerank_error']}", flush=True)

    # ── 最终回复全文 ──
    content_parts = st.get("content_parts", [])
    content_count = st.get("content_count", 0)
    final_mode = st.get("final_mode", "")
    final_content = "".join(content_parts)
    if final_content:
        print(f"\n{'─' * 72}", flush=True)
        print("  最终回复生成方式说明:", flush=True)
        print(f"{'─' * 72}", flush=True)
        print(f"  生成模式: {final_mode or 'LLM在工具调用轮中直接回复'}", flush=True)
        print(f"  流式片段数: {content_count}", flush=True)
        print(f"\n  生成过程:", flush=True)
        print(f"    1. 系统提示词 = 导购策略 + 知识库上下文 + 需求分析摘要", flush=True)
        print(f"    2. 消息历史 = system + 对话历史 + 用户问题 + 工具调用结果", flush=True)
        if final_mode and "受约束" in final_mode:
            print(f"    3. LLM直接回复未引用选中商品ID，触发受约束重新生成", flush=True)
            print(f"    4. 构建新prompt，要求LLM只能引用选中商品列表中的ID", flush=True)
        else:
            print(f"    3. LLM在工具调用轮中直接生成了回复，且已包含选中商品ID", flush=True)
            print(f"    4. 无需额外整理，直接使用该回复", flush=True)
        print(f"\n{'─' * 72}", flush=True)
        print("  最终回复全文:", flush=True)
        print(f"{'─' * 72}", flush=True)
        print(final_content, flush=True)


class RealToolChatFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_streaming_tool_chat_flow(self):
        model_config = _default_model_config_with_key()
        if not model_config:
            self.skipTest(
                "未找到默认 LLM 配置。请配置 LLM_MODEL、LLM_BASE_URL 和 LLM_API_KEY。"
            )

        print("\n" + "=" * 72)
        print("  真实完整流程配置")
        print("=" * 72)
        print(f"  模型: {model_config.get('id')}")
        print(f"  API:  {model_config.get('base_url')}")
        print(f"  Chroma: {settings.chroma_path} (集合: {settings.chroma_collection_name})")
        print(f"  SQLite: {settings.sqlite_product_db_path}")
        print(f"  服务: {tool_chat_service_module.__file__}")

        llm_service.initialize()
        embedding_service.initialize()
        vector_store.initialize()
        sqlite_product_search_service.initialize()

        try:
            preflight_reply = await asyncio.wait_for(
                llm_service.chat(
                    [{"role": "user", "content": "请只回复 OK，用于连通性测试。"}],
                    temperature=0.0,
                    model=model_config.get("id"),
                    model_config=model_config,
                ),
                timeout=30,
            )
            print(f"  LLM预检: {preflight_reply[:80]}")
        except Exception as exc:
            self.fail(
                "真实 LLM 预检失败，无法开始完整流程。"
                "这通常表示当前 REAL_TOOL_CHAT_MODEL/AVAILABLE_LLM_MODELS 指向了无权限或不可调用的模型。\n"
                f"model={model_config.get('id')}\n"
                f"base_url={model_config.get('base_url')}\n"
                f"error={type(exc).__name__}: {exc}\n"
                "可用示例：REAL_TOOL_CHAT_MODEL=你的可调用模型 "
                "REAL_TOOL_CHAT_BASE_URL=https://... "
                "REAL_TOOL_CHAT_API_KEY=... "
                "python test/test_real_tool_chat_flow.py -v"
            )

        self.assertGreater(vector_store.get_count(), 0, "RAG Chroma 集合为空")
        self.assertTrue(sqlite_product_search_service.db_available, "SQLite 商品数据库不可用")

        service = ToolChatService(vector_store, llm_service)

        # ── 实时流式输出：边收集边打印 ──
        collected: list[dict[str, Any]] = []
        print(f"\n{'=' * 72}")
        print("  真实工具聊天完整流程 —— 流式事件追踪")
        print(f"{'=' * 72}")

        async for chunk in service.chat_with_tools_stream(
            "我是新手，想买一支好上手的眉笔。请先检索知识库，再查询商品数据库，最后推荐具体商品并给出商品ID。",
            max_tool_calls=3,
            model=model_config.get("id"),
            model_config=model_config,
        ):
            collected.append(chunk)
            _print_chunk(chunk)
            if chunk.get("type") == "error":
                break

        _print_summary(collected)

        errors = [chunk for chunk in collected if chunk.get("type") == "error"]
        self.assertFalse(errors, f"真实流程出现错误: {errors}")

        phases = [chunk.get("phase") for chunk in collected if chunk.get("phase")]
        self.assertIn("retrieving_knowledge", phases)
        self.assertIn("need_analysis", phases)
        self.assertIn("querying_products", phases)
        self.assertIn("tool_done", phases)
        self.assertIn("organizing_results", phases)

        vector_debug_chunks = [
            chunk for chunk in collected
            if chunk.get("type") == "debug" and chunk.get("phase") == "vector_search"
        ]
        rerank_debug_chunks = [
            chunk for chunk in collected
            if chunk.get("type") == "debug" and chunk.get("phase") == "rag_rerank"
        ]
        self.assertTrue(vector_debug_chunks, "未返回 RAG 向量检索明细")
        self.assertTrue(rerank_debug_chunks, "未返回 RAG 核验明细")

        selected_product_chunks = [chunk for chunk in collected if chunk.get("type") == "selected_products"]
        if not selected_product_chunks:
            final_reply_for_debug = "".join(chunk.get("content", "") for chunk in collected if chunk.get("type") == "content")
            reply_product_ids = re.findall(r"p_[a-z]+_\d+", final_reply_for_debug)
            print(f"- debug_reply_product_ids={reply_product_ids}")
        self.assertTrue(selected_product_chunks, "未返回工具选中商品")
        selected_ids = selected_product_chunks[-1].get("selected_product_ids") or []
        self.assertTrue(selected_ids, "工具查询没有选中任何商品ID")

        final_reply = "".join(chunk.get("content", "") for chunk in collected if chunk.get("type") == "content")
        self.assertTrue(final_reply.strip(), "未生成最终回复")
        self.assertIn("p_beauty_025", selected_ids, "结构化选中商品未包含本轮核心眉笔商品")

        done_chunks = [chunk for chunk in collected if chunk.get("type") == "done"]
        self.assertTrue(done_chunks, "流式流程未正常结束")
        timings = done_chunks[-1].get("timings") or {}
        self.assertIn("vector_search", timings)
        self.assertIn("rag_rerank", timings)
        self.assertIn("tool_calls", timings)


if __name__ == "__main__":
    unittest.main()
