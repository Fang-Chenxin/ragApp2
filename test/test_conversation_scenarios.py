#!/usr/bin/env python3
"""多场景对话测试 - 覆盖各种商品查询情况，直观展示处理过程和最终回复。

用法:
    python test/test_conversation_scenarios.py                    # 运行全部场景
    python test/test_conversation_scenarios.py --scenario direct  # 运行指定场景
    python test/test_conversation_scenarios.py --list             # 列出所有场景
    python test/test_conversation_scenarios.py --quiet            # 只看最终回复和结果

需要可用的 LLM 配置（REAL_TOOL_CHAT_MODEL / LLM_MODEL + LLM_API_KEY）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
import logging
from pathlib import Path
from typing import Any

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

logging.getLogger("service.tool_chat").setLevel(logging.ERROR)

# 复用 test_real_tool_chat_flow.py 的打印函数
from test_real_tool_chat_flow import (
    _default_model_config_with_key,
    _print_chunk,
    _print_summary,
    _trace_state,
)


# ══════════════════════════════════════════════════════════════════════
#  测试场景定义
# ══════════════════════════════════════════════════════════════════════

SCENARIOS = [
    # ── 直接匹配 ──
    {
        "id": "direct_ipad",
        "name": "直接匹配 - iPad",
        "query": "想买iPad平板学习用",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["数码电子"],
            "forbidden_categories": ["食品饮料", "美妆护肤", "服饰运动"],
            "reply_should_contain": ["iPad"],
        },
    },
    {
        "id": "direct_sunscreen",
        "name": "直接匹配 - 防晒霜",
        "query": "有没有好用的防晒霜推荐",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["美妆护肤"],
            "forbidden_categories": ["食品饮料", "数码电子", "服饰运动"],
        },
    },
    {
        "id": "direct_running_shoes",
        "name": "直接匹配 - 跑步鞋",
        "query": "推荐跑步鞋",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["服饰运动"],
            "forbidden_categories": ["食品饮料", "数码电子", "美妆护肤"],
        },
    },
    {
        "id": "direct_coffee",
        "name": "直接匹配 - 咖啡",
        "query": "推荐咖啡",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["食品饮料"],
            "forbidden_categories": ["美妆护肤", "数码电子", "服饰运动"],
        },
    },
    # ── 品牌型号约束 ──
    {
        "id": "brand_macbook",
        "name": "品牌约束 - MacBook",
        "query": "推荐一台MacBook办公用",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["数码电子"],
            "forbidden_categories": ["食品饮料", "美妆护肤", "服饰运动"],
            "reply_should_contain": ["MacBook"],
        },
    },
    # ── Fallback（无直接匹配） ──
    {
        "id": "fallback_game_laptop",
        "name": "Fallback - 游戏本（库中无游戏本，应推荐笔记本）",
        "query": "想要游戏本打游戏",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["数码电子"],
            "forbidden_categories": ["食品饮料", "美妆护肤", "服饰运动"],
        },
    },
    {
        "id": "fallback_lipstick",
        "name": "Fallback - 口红（库中无口红，应推荐唇釉）",
        "query": "推荐一支口红",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["美妆护肤"],
            "forbidden_categories": ["食品饮料", "数码电子", "服饰运动"],
        },
    },
    # ── 场景型需求 ──
    {
        "id": "scenario_sensitive_skin",
        "name": "场景需求 - 敏感肌防晒",
        "query": "敏感肌适合什么防晒",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["美妆护肤"],
            "forbidden_categories": ["食品饮料", "数码电子", "服饰运动"],
        },
    },
    {
        "id": "scenario_learning",
        "name": "场景需求 - 学习平板",
        "query": "买个平板给孩子上网课用",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["数码电子"],
            "forbidden_categories": ["食品饮料", "美妆护肤", "服饰运动"],
        },
    },
    # ── 口语化表达 ──
    {
        "id": "colloquial_tshirt",
        "name": "口语化 - T恤",
        "query": "有没有便宜点的T恤",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["服饰运动"],
            "forbidden_categories": ["食品饮料", "数码电子", "美妆护肤"],
        },
    },
    {
        "id": "colloquial_yogurt",
        "name": "口语化 - 酸奶",
        "query": "有没有酸奶推荐",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["食品饮料"],
            "forbidden_categories": ["美妆护肤", "数码电子", "服饰运动"],
        },
    },
    # ── 跨品类干扰 ──
    {
        "id": "cross_meat_bread",
        "name": "跨品类 - 肉松面包（SQLite 可能召回酸奶/咖啡，需过滤）",
        "query": "想买肉松面包",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["食品饮料"],
            "forbidden_categories": ["美妆护肤", "数码电子", "服饰运动"],
        },
    },
    # ── 无库存 ──
    {
        "id": "no_stock_cat_food",
        "name": "无库存 - 猫粮",
        "query": "有没有猫粮",
        "expect": {
            "min_products": 0,
            "forbidden_categories": ["美妆护肤", "数码电子", "服饰运动", "食品饮料"],
        },
    },
    # ── 多轮对话（第二轮追问） ──
    {
        "id": "multi_turn_followup",
        "name": "多轮对话 - 追问推荐",
        "query": "那有没有适合跑步时听的耳机",
        "history": [
            {"role": "user", "content": "推荐跑步鞋"},
            {"role": "assistant", "content": "为您推荐以下跑步鞋：Nike Air Zoom Pegasus 41..."},
        ],
        "expect": {
            "min_products": 1,
            "allowed_categories": ["数码电子"],
            "forbidden_categories": ["食品饮料", "美妆护肤"],
        },
    },
    {
        "id": "multi_turn_cheaper",
        "name": "多轮对话 - 更便宜的追问",
        "query": "再便宜点的呢？",
        "history": [
            {"role": "user", "content": "推荐跑步鞋"},
            {"role": "assistant", "content": "可以先看 Nike Air Zoom Pegasus 41，参考价899元，品类：服饰运动/跑步鞋。"},
        ],
        "expect": {
            "min_products": 1,
            "allowed_categories": ["服饰运动"],
            "forbidden_categories": ["食品饮料", "数码电子", "美妆护肤"],
        },
    },
    {
        "id": "exclude_nike",
        "name": "反选排除 - 除了耐克",
        "query": "除了耐克还有什么跑步鞋或运动装备？",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["服饰运动"],
            "forbidden_categories": ["食品饮料", "数码电子", "美妆护肤"],
            "forbidden_brands": ["耐克", "Nike"],
        },
    },
    {
        "id": "exclude_alcohol_sunscreen",
        "name": "反选排除 - 不含酒精防晒",
        "query": "不要含酒精的防晒，有什么推荐？",
        "expect": {
            "min_products": 1,
            "allowed_categories": ["美妆护肤"],
            "forbidden_categories": ["食品饮料", "数码电子", "服饰运动"],
            "forbidden_product_ids": ["p_beauty_010"],
        },
    },
    {
        "id": "compare_sunscreens",
        "name": "多商品对比 - 防晒对比",
        "query": "对比两款防晒，重点看价格和成分",
        "expect": {
            "min_products": 2,
            "allowed_categories": ["美妆护肤"],
            "forbidden_categories": ["食品饮料", "数码电子", "服饰运动"],
            "reply_should_contain": ["|"],
        },
    },
]


# ══════════════════════════════════════════════════════════════════════
#  场景运行器
# ══════════════════════════════════════════════════════════════════════

def _extract_final_content(chunks: list[dict[str, Any]]) -> str:
    """从 chunks 中提取最终回复全文。"""
    return "".join(
        chunk.get("content", "") for chunk in chunks if chunk.get("type") == "content"
    )


def _extract_selected_products(chunks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从 chunks 中提取最终选中商品。"""
    for chunk in chunks:
        if chunk.get("type") == "selected_products":
            return chunk.get("selected_products") or []
    # 从 debug 阶段提取
    for chunk in chunks:
        if chunk.get("type") == "debug" and chunk.get("phase") == "selected_products":
            return chunk.get("selected_products") or []
    return []


def _check_expectations(
    scenario: dict,
    chunks: list[dict[str, Any]],
    final_content: str,
    selected_products: list[dict[str, Any]],
) -> list[str]:
    """检查场景预期是否满足，返回错误列表。"""
    errors: list[str] = []
    expect = scenario.get("expect", {})

    # 检查错误事件
    error_chunks = [c for c in chunks if c.get("type") == "error"]
    if error_chunks:
        errors.append(f"流程出现错误: {error_chunks[0].get('content', '')}")
        return errors

    # 检查商品数量
    min_products = expect.get("min_products", 0)
    if len(selected_products) < min_products:
        errors.append(f"期望至少 {min_products} 个商品，实际 {len(selected_products)} 个")

    # 检查允许类目
    allowed_categories = expect.get("allowed_categories", [])
    if allowed_categories and selected_products:
        for p in selected_products:
            cat = p.get("category", "")
            if cat and cat not in allowed_categories:
                errors.append(f"商品 {p.get('product_id')} 类目 '{cat}' 不在 allowed_categories {allowed_categories} 中")

    # 检查禁止类目
    forbidden_categories = expect.get("forbidden_categories", [])
    if forbidden_categories and selected_products:
        for p in selected_products:
            cat = p.get("category", "")
            if cat in forbidden_categories:
                errors.append(f"商品 {p.get('product_id')} 命中禁止类目 '{cat}'")

    forbidden_brands = expect.get("forbidden_brands", [])
    if forbidden_brands and selected_products:
        for p in selected_products:
            brand = p.get("brand", "")
            if any(item.lower() in brand.lower() for item in forbidden_brands):
                errors.append(f"商品 {p.get('product_id')} 命中禁止品牌 '{brand}'")

    forbidden_product_ids = set(expect.get("forbidden_product_ids", []))
    if forbidden_product_ids and selected_products:
        for p in selected_products:
            if p.get("product_id") in forbidden_product_ids:
                errors.append(f"商品 {p.get('product_id')} 命中禁止商品")

    # 检查回复内容
    reply_should_contain = expect.get("reply_should_contain", [])
    for keyword in reply_should_contain:
        if keyword.lower() not in final_content.lower():
            errors.append(f"回复中缺少关键词 '{keyword}'")

    return errors


async def run_scenario(
    scenario: dict,
    service: ToolChatService,
    model_config: dict,
    quiet: bool = False,
) -> dict:
    """运行单个测试场景，返回结果。"""
    scenario_id = scenario["id"]
    scenario_name = scenario["name"]
    query = scenario["query"]
    history = scenario.get("history")

    if not quiet:
        print(f"\n{'═' * 72}", flush=True)
        print(f"  场景: [{scenario_id}] {scenario_name}", flush=True)
        print(f"  查询: \"{query}\"", flush=True)
        if history:
            print(f"  历史: {len(history)} 条对话", flush=True)
            for h in history:
                print(f"    [{h['role']}] {h['content'][:60]}...", flush=True)
        print(f"{'═' * 72}", flush=True)

    # 重置 trace 状态
    _trace_state.clear()

    collected: list[dict[str, Any]] = []

    try:
        async for chunk in service.chat_with_tools_stream(
            user_query=query,
            conversation_history=history,
            max_tool_calls=3,
            model=model_config.get("id"),
            model_config=model_config,
        ):
            collected.append(chunk)
            if not quiet:
                _print_chunk(chunk)
            if chunk.get("type") == "error":
                break
    except Exception as exc:
        return {
            "id": scenario_id,
            "name": scenario_name,
            "passed": False,
            "errors": [f"异常: {type(exc).__name__}: {exc}"],
            "final_content": "",
            "selected_products": [],
        }

    final_content = _extract_final_content(collected)
    selected_products = _extract_selected_products(collected)

    if not quiet:
        _print_summary(collected)

    errors = _check_expectations(scenario, collected, final_content, selected_products)

    # 打印结果
    passed = len(errors) == 0
    status = "✅ 通过" if passed else "❌ 失败"
    print(f"\n  ── 结果: {status} ──", flush=True)
    if selected_products:
        print(f"  选中商品 ({len(selected_products)} 条):", flush=True)
        for p in selected_products:
            print(
                f"    [{p.get('match_type', '?')}] {p.get('product_id')} | "
                f"{p.get('title', '')[:30]} | {p.get('category')}/{p.get('sub_category')}",
                flush=True,
            )
    else:
        print(f"  选中商品: 无", flush=True)

    if errors:
        for e in errors:
            print(f"    ❌ {e}", flush=True)

    return {
        "id": scenario_id,
        "name": scenario_name,
        "passed": passed,
        "errors": errors,
        "final_content": final_content,
        "selected_products": selected_products,
    }


# ══════════════════════════════════════════════════════════════════════
#  主入口
# ══════════════════════════════════════════════════════════════════════

async def main() -> int:
    parser = argparse.ArgumentParser(description="多场景对话测试")
    parser.add_argument("--scenario", "-s", help="只运行指定 id 的场景")
    parser.add_argument("--list", "-l", action="store_true", help="列出所有场景")
    parser.add_argument("--quiet", "-q", action="store_true", help="只看最终回复和结果")
    args = parser.parse_args()

    if args.list:
        print("📋 可用测试场景:")
        for s in SCENARIOS:
            print(f"  {s['id']:25s} {s['name']}")
        return 0

    model_config = _default_model_config_with_key()
    if not model_config:
        print("❌ 未找到 LLM 配置。请设置 REAL_TOOL_CHAT_MODEL / LLM_MODEL + LLM_API_KEY。")
        return 1

    print(f"🧪 多场景对话测试", flush=True)
    print(f"   模型: {model_config.get('id')}", flush=True)
    print(f"   API:  {model_config.get('base_url')}", flush=True)

    llm_service.initialize()
    embedding_service.initialize()
    vector_store.initialize()
    sqlite_product_search_service.initialize()

    service = ToolChatService(vector_store, llm_service)

    scenarios = SCENARIOS
    if args.scenario:
        scenarios = [s for s in SCENARIOS if s["id"] == args.scenario]
        if not scenarios:
            print(f"❌ 未找到场景 '{args.scenario}'，可用: {[s['id'] for s in SCENARIOS]}")
            return 1

    results: list[dict] = []
    for scenario in scenarios:
        result = await run_scenario(scenario, service, model_config, quiet=args.quiet)
        results.append(result)

    # ── 汇总 ──
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])
    total = len(results)

    print(f"\n{'═' * 72}", flush=True)
    print(f"📊 测试汇总: {passed}/{total} 通过, {failed} 失败", flush=True)
    print(f"{'═' * 72}", flush=True)

    for r in results:
        status = "✅" if r["passed"] else "❌"
        product_count = len(r["selected_products"])
        categories = list(set(p.get("category", "") for p in r["selected_products"]))
        cat_str = ", ".join(categories) if categories else "无"
        print(f"  {status} [{r['id']}] {r['name']}", flush=True)
        print(f"       商品: {product_count}个 ({cat_str})", flush=True)
        if r["errors"]:
            for e in r["errors"]:
                print(f"       ❌ {e}", flush=True)

    if failed:
        print(f"\n❌ {failed} 个场景失败", flush=True)
        return 1
    else:
        print(f"\n✅ 全部通过", flush=True)
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
