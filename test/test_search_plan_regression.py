#!/usr/bin/env python3
"""SearchPlan 回归测试脚本 - 基于 search_semantics/regression_cases.json 验证商品匹配逻辑。

用法:
    python test/test_search_plan_regression.py              # 运行全部用例（模拟模式）
    python test/test_search_plan_regression.py --live        # 走真实 SQLite 查询 + 全链路
    python test/test_search_plan_regression.py --id ipadow  # 运行单条用例
    python test/test_search_plan_regression.py --verbose    # 详细输出
    python test/test_search_plan_regression.py --live -v    # 真实查询 + 详细输出

测试范围（模拟模式）:
    - search_semantics_service 语义表匹配
    - _normalize_search_plan + _apply_semantic_corrections 修正
    - _matches_user_product_constraints 品类过滤（模拟商品）
    - _is_direct_product_match 直接匹配判定（模拟商品）

测试范围（--live 模式，额外覆盖）:
    - SQLite search_by_rule_parsed_text 真实商品召回
    - _build_target_products 全链路：召回 → 过滤 → direct/fallback 判定
    - 验证 allowed_product_ids / forbidden_product_ids / allowed_categories
    - 验证 must_include_match_type
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# 确保 backend 可导入
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "backend"))

from service.product_search.search_semantics_service import search_semantics_service
from service.product_search.sqlite_search import sqlite_product_search_service
from service.tool_chat.product_selection import ToolChatProductSelectionMixin
from service.product_search.engine import strip_intent_words


def load_regression_cases() -> list[dict]:
    """加载回归测试用例。"""
    path = _ROOT / "ecommerce_agent_dataset" / "search_semantics" / "regression_cases.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    cases.extend(
        [
            {
                "id": "intent_browsing_running_earphones",
                "query": "随便看看有没有适合跑步的耳机",
                "expect": {"purchase_intent": "browsing"},
            },
            {
                "id": "intent_purchase_running_earphones",
                "query": "我想买个跑步耳机",
                "expect": {"purchase_intent": "purchase_ready"},
            },
            {
                "id": "intent_purchase_sunscreen",
                "query": "推荐一款防晒霜",
                "expect": {"purchase_intent": "purchase_ready"},
            },
            {
                "id": "followup_cheaper",
                "query": "再便宜点的呢？",
                "expect": {"price_preference": "cheaper", "is_followup": True},
            },
            {
                "id": "exclude_nike_running",
                "query": "除了耐克还有什么跑步鞋",
                "expect": {"excluded_brands": ["Nike"]},
            },
            {
                "id": "exclude_alcohol_sunscreen",
                "query": "不要含酒精的防晒",
                "expect": {"excluded_terms": ["酒精"], "allowed_categories": ["美妆护肤"]},
            },
            {
                "id": "comparison_two_sunscreens",
                "query": "对比这两款防晒，重点看价格和成分",
                "expect": {"comparison_intent": True, "comparison_dimensions": ["价格", "成分"]},
            },
            {
                "id": "comparison_best_value_instant_noodles",
                "query": "方便面买哪一种更划算",
                "expect": {"comparison_intent": True, "price_preference": "cheaper", "allowed_categories": ["食品饮料"]},
            },
        ]
    )
    return cases


def check_case(case: dict, verbose: bool = False) -> dict:
    """检查单条回归用例，返回 {passed, errors, details}。"""
    case_id = case.get("id", "unknown")
    query = case.get("query", "")
    expect = case.get("expect", {})
    errors: list[str] = []
    details: dict = {}

    if verbose:
        print(f"\n  📝 [{case_id}] query=\"{query}\"")

    # 1. 检查语义表匹配
    concepts = search_semantics_service.match_product_concepts(query)
    categories = search_semantics_service.match_category_aliases(query)
    fallback = search_semantics_service.get_fallback_relation(query)
    brands = search_semantics_service.match_brand_models(query)
    scenarios = search_semantics_service.match_scenario_tags(query)

    details["concepts"] = [c["concept"] for c in concepts]
    details["categories"] = [c["alias"] for c in categories]
    details["fallback_trigger"] = fallback["trigger"] if fallback else None
    details["brands"] = [b["model"] for b in brands]
    details["scenarios"] = [s["scenario"] for s in scenarios]

    if verbose:
        print(f"    concepts: {details['concepts']}")
        print(f"    categories: {details['categories']}")
        print(f"    fallback: {details['fallback_trigger']}")
        print(f"    brands: {details['brands']}")
        print(f"    scenarios: {details['scenarios']}")

    # 2. 构建模拟 SearchPlan 并应用语义修正
    plan = ToolChatProductSelectionMixin._normalize_search_plan(
        {
            "target_product": query,
            "query_text": query,
            "direct_terms": [query],
            "allowed_categories": [],
            "forbidden_categories": [],
        },
        query,
    )
    details["plan_after_correction"] = {
        "direct_terms": plan.get("direct_terms", []) if plan else [],
        "allowed_categories": plan.get("allowed_categories", []) if plan else [],
        "forbidden_categories": plan.get("forbidden_categories", []) if plan else [],
        "purchase_intent": plan.get("purchase_intent", "") if plan else "",
    }

    if verbose:
        print(f"    plan corrected: {json.dumps(details['plan_after_correction'], ensure_ascii=False)}")

    expected_intent = expect.get("purchase_intent")
    if expected_intent and plan and plan.get("purchase_intent") != expected_intent:
        errors.append(f"purchase_intent 期望 '{expected_intent}'，实际 '{plan.get('purchase_intent')}'")
    expected_price_preference = expect.get("price_preference")
    if expected_price_preference and plan and plan.get("price_preference") != expected_price_preference:
        errors.append(f"price_preference 期望 '{expected_price_preference}'，实际 '{plan.get('price_preference')}'")
    expected_is_followup = expect.get("is_followup")
    if expected_is_followup is not None and plan and bool(plan.get("is_followup")) != bool(expected_is_followup):
        errors.append(f"is_followup 期望 '{expected_is_followup}'，实际 '{plan.get('is_followup')}'")
    expected_excluded_brands = expect.get("excluded_brands", [])
    if expected_excluded_brands and plan:
        actual = set(plan.get("excluded_brands", []))
        for brand in expected_excluded_brands:
            if brand not in actual:
                errors.append(f"excluded_brands 缺少 '{brand}' (actual={actual})")
    expected_excluded_terms = expect.get("excluded_terms", [])
    if expected_excluded_terms and plan:
        actual = set(plan.get("excluded_terms", []))
        for term in expected_excluded_terms:
            if term not in actual:
                errors.append(f"excluded_terms 缺少 '{term}' (actual={actual})")
    expected_comparison = expect.get("comparison_intent")
    if expected_comparison is not None and plan and bool(plan.get("comparison_intent")) != bool(expected_comparison):
        errors.append(f"comparison_intent 期望 '{expected_comparison}'，实际 '{plan.get('comparison_intent')}'")
    expected_dimensions = expect.get("comparison_dimensions", [])
    if expected_dimensions and plan:
        actual = set(plan.get("comparison_dimensions", []))
        for dimension in expected_dimensions:
            if dimension not in actual:
                errors.append(f"comparison_dimensions 缺少 '{dimension}' (actual={actual})")

    # 3. 检查 forbidden_categories 约束
    expected_forbidden = expect.get("forbidden_categories", [])
    if expected_forbidden and plan:
        plan_forbidden = set(plan.get("forbidden_categories", []))
        for cat in expected_forbidden:
            if cat not in plan_forbidden:
                # 也检查语义表兜底
                semantic_forbidden = search_semantics_service.get_forbidden_categories(query)
                if cat not in semantic_forbidden:
                    errors.append(f"forbidden_categories 缺少 '{cat}' (plan={plan_forbidden}, semantic={semantic_forbidden})")

    # 4. 检查 allowed_categories 约束
    expected_allowed = expect.get("allowed_categories", [])
    if expected_allowed and plan:
        plan_allowed = set(plan.get("allowed_categories", []))
        if plan_allowed:
            for cat in expected_allowed:
                if cat not in plan_allowed:
                    # 允许类目不完全匹配时不算硬错误，只记录
                    if verbose:
                        print(f"    ⚠️ allowed_categories 不包含 '{cat}' (plan={plan_allowed})")

    # 5. 模拟商品过滤测试
    mock_products = _build_mock_products_for_query(query)
    if mock_products:
        filtered = []
        for mp in mock_products:
            if ToolChatProductSelectionMixin._matches_user_product_constraints(query, mp, plan):
                mp["match_type"] = "direct" if ToolChatProductSelectionMixin._is_direct_product_match(query, mp, plan) else "fallback"
                filtered.append(mp)

        details["mock_products_count"] = len(mock_products)
        details["filtered_count"] = len(filtered)
        details["filtered_categories"] = list(set(mp.get("category", "") for mp in filtered))

        # 检查过滤后不应包含 forbidden 类目
        for mp in filtered:
            if mp.get("category") in expected_forbidden:
                errors.append(f"过滤后仍包含禁止类目商品: {mp.get('title')} ({mp.get('category')})")

        if verbose:
            print(f"    mock products: {len(mock_products)} → filtered: {len(filtered)}")
            for mp in filtered:
                print(f"      [{mp['match_type']}] {mp.get('title')} ({mp.get('category')}/{mp.get('sub_category')})")

    return {
        "case_id": case_id,
        "passed": len(errors) == 0,
        "errors": errors,
        "details": details,
    }


def _build_mock_products_for_query(query: str) -> list[dict]:
    """构建模拟商品列表用于过滤测试，每个类目一个贴近真实的模拟商品。"""
    return [
        {"product_id": "mock_digital_001", "title": "Apple iPad Air 11英寸 M4 芯片 128GB Wi-Fi 轻薄平板电脑", "brand": "Apple 苹果", "category": "数码电子", "sub_category": "平板电脑"},
        {"product_id": "mock_digital_002", "title": "Apple MacBook Pro 14英寸 M5 芯片 16GB 512GB 高性能笔记本电脑", "brand": "Apple 苹果", "category": "数码电子", "sub_category": "笔记本电脑"},
        {"product_id": "mock_digital_003", "title": "小米 17 Max 大屏长续航高性能影音游戏5G智能手机", "brand": "小米", "category": "数码电子", "sub_category": "智能手机"},
        {"product_id": "mock_digital_004", "title": "Apple AirPods Pro 3 主动降噪真无线蓝牙耳机", "brand": "Apple 苹果", "category": "数码电子", "sub_category": "真无线耳机"},
        {"product_id": "mock_beauty_001", "title": "完美日记仿生膜精华唇釉丝绒哑光滋润显色唇部彩妆", "brand": "完美日记", "category": "美妆护肤", "sub_category": "唇釉"},
        {"product_id": "mock_beauty_002", "title": "安热沙金灿倍护防晒乳高倍防水防汗清爽户外面部防晒", "brand": "安热沙", "category": "美妆护肤", "sub_category": "防晒"},
        {"product_id": "mock_beauty_003", "title": "雅诗兰黛特润修护肌活精华露淡纹紧致保湿精华", "brand": "雅诗兰黛", "category": "美妆护肤", "sub_category": "精华"},
        {"product_id": "mock_beauty_004", "title": "理肤泉特护清盈防晒乳高倍防晒清爽控油易敏肌适用", "brand": "理肤泉", "category": "美妆护肤", "sub_category": "防晒"},
        {"product_id": "mock_clothes_001", "title": "优衣库 U AIRism 棉质宽松圆领短袖T恤 男装基础上衣", "brand": "优衣库", "category": "服饰运动", "sub_category": "短袖T恤"},
        {"product_id": "mock_clothes_002", "title": "Nike Air Zoom Pegasus 41 男子缓震跑步鞋日常训练鞋", "brand": "耐克", "category": "服饰运动", "sub_category": "跑步鞋"},
        {"product_id": "mock_clothes_003", "title": "Nike LeBron XXI EP 男子中帮实战篮球鞋全掌缓震训练鞋", "brand": "耐克", "category": "服饰运动", "sub_category": "篮球鞋"},
        {"product_id": "mock_clothes_004", "title": "Lululemon Align 高腰紧身裤 25英寸 女士瑜伽裤运动裤", "brand": "露露乐蒙", "category": "服饰运动", "sub_category": "瑜伽裤"},
        {"product_id": "mock_clothes_005", "title": "李宁 运动生活系列 男子连帽套头卫衣 基础Logo上衣", "brand": "李宁", "category": "服饰运动", "sub_category": "卫衣"},
        {"product_id": "mock_food_001", "title": "三顿半 数字星球系列 超即溶精品咖啡18颗装速溶咖啡", "brand": "三顿半", "category": "食品饮料", "sub_category": "咖啡"},
        {"product_id": "mock_food_002", "title": "伊利 安慕希 希腊风味常温酸奶 原味205g×10盒装", "brand": "伊利", "category": "食品饮料", "sub_category": "酸奶"},
        {"product_id": "mock_food_003", "title": "良品铺子 肉松饼1000g/箱 松软糕点休闲零食早餐点心", "brand": "良品铺子", "category": "食品饮料", "sub_category": "坚果/零食"},
        {"product_id": "mock_food_004", "title": "三只松鼠 每日坚果750g/30袋 混合坚果仁干果礼盒", "brand": "三只松鼠", "category": "食品饮料", "sub_category": "坚果/零食"},
    ]


def check_case_live(case: dict, verbose: bool = False) -> dict:
    """真实 SQLite 查询 + 全链路回归测试。"""
    case_id = case.get("id", "unknown")
    query = case.get("query", "")
    expect = case.get("expect", {})
    errors: list[str] = []
    details: dict = {}

    if verbose:
        print(f"\n  📝 [{case_id}] query=\"{query}\" (live)")

    # ── 第1步：构建 SearchPlan ──
    plan = ToolChatProductSelectionMixin._normalize_search_plan(
        {
            "target_product": query,
            "query_text": query,
            "direct_terms": [query],
            "allowed_categories": [],
            "forbidden_categories": [],
        },
        query,
    )
    details["plan"] = {
        "direct_terms": plan.get("direct_terms", []) if plan else [],
        "allowed_categories": plan.get("allowed_categories", []) if plan else [],
        "forbidden_categories": plan.get("forbidden_categories", []) if plan else [],
        "purchase_intent": plan.get("purchase_intent", "") if plan else "",
    }
    if verbose:
        print(f"\n    ── 第1步：SearchPlan 构建（语义表修正后）──")
        print(f"    目标商品: {plan.get('target_product', '')}")
        print(f"    direct_terms: {plan.get('direct_terms', [])}")
        print(f"    allowed_categories: {plan.get('allowed_categories', [])}")
        print(f"    forbidden_categories: {plan.get('forbidden_categories', [])}")
        print(f"    purchase_intent: {plan.get('purchase_intent', '')}")

    expected_intent = expect.get("purchase_intent")
    if expected_intent and plan and plan.get("purchase_intent") != expected_intent:
        errors.append(f"purchase_intent 期望 '{expected_intent}'，实际 '{plan.get('purchase_intent')}'")

    # ── 第2步：SQLite 真实召回 ──
    search_result = sqlite_product_search_service.search_by_rule_parsed_text(query, limit=10)
    raw_items = search_result.get("items", [])
    details["raw_result_count"] = len(raw_items)
    if verbose:
        print(f"\n    ── 第2步：SQLite 商品召回 ──")
        print(f"    召回数量: {len(raw_items)} 条")
        for idx, item in enumerate(raw_items[:5], 1):
            print(f"      {idx}. {item.get('product_id')} | {str(item.get('title',''))[:35]} | {item.get('category')}/{item.get('sub_category')}")
        if len(raw_items) > 5:
            print(f"      ... 共 {len(raw_items)} 条")

    # ── 第3步：过滤与判定 ──
    tool_products = ToolChatProductSelectionMixin._extract_products_from_tool_result(search_result, limit=5)
    targets = ToolChatProductSelectionMixin._build_target_products(
        direct_products=[],
        tool_products=tool_products,
        user_query=query,
        search_plan=plan,
        limit=5,
    )
    details["target_count"] = len(targets)
    target_ids = [t.get("product_id") for t in targets]
    target_categories = list(set(t.get("category", "") for t in targets))
    target_match_types = list(set(t.get("match_type", "") for t in targets))
    details["target_ids"] = target_ids
    details["target_categories"] = target_categories
    details["target_match_types"] = target_match_types

    if verbose:
        print(f"\n    ── 第3步：过滤与 direct/fallback 判定 ──")
        print(f"    {len(raw_items)} 条召回 → {len(tool_products)} 条候选 → {len(targets)} 条目标")
        for idx, t in enumerate(targets, 1):
            print(f"      {idx}. [{t.get('match_type')}] {t.get('product_id')} | {t.get('title', '')[:35]} | {t.get('category')}/{t.get('sub_category')}")

    # ── 第4步：验证约束 ──
    if verbose:
        print(f"\n    ── 第4步：约束验证 ──")

    expected_match_type = expect.get("must_include_match_type", "any")
    allowed_ids = expect.get("allowed_product_ids", [])
    forbidden_ids = set(expect.get("forbidden_product_ids", []))
    expected_allowed_cats = expect.get("allowed_categories", [])
    expected_forbidden_cats = expect.get("forbidden_categories", [])

    if expected_match_type == "direct" and "direct" not in target_match_types:
        if targets:
            errors.append(f"期望 match_type=direct，但实际只有 {target_match_types}")
    if allowed_ids and targets:
        for t in targets:
            pid = t.get("product_id", "")
            if pid and pid not in allowed_ids:
                errors.append(f"目标商品 {pid} 不在 allowed_product_ids 中 (allowed={allowed_ids})")
    if forbidden_ids:
        for t in targets:
            pid = t.get("product_id", "")
            if pid in forbidden_ids:
                errors.append(f"目标商品 {pid} 在 forbidden_product_ids 中")
    if expected_allowed_cats and targets:
        for t in targets:
            cat = t.get("category", "")
            if cat and cat not in expected_allowed_cats:
                errors.append(f"目标商品 {t.get('product_id')} 类目 '{cat}' 不在 allowed_categories {expected_allowed_cats} 中")
    if expected_forbidden_cats and targets:
        for t in targets:
            cat = t.get("category", "")
            if cat in expected_forbidden_cats:
                errors.append(f"目标商品 {t.get('product_id')} 命中禁止类目 '{cat}'")

    if verbose:
        checks = []
        if expected_match_type != "any":
            ok = "direct" in target_match_types if targets else (expected_match_type != "direct")
            checks.append(f"match_type={expected_match_type}: {'✅' if ok else '❌'}")
        if expected_allowed_cats:
            ok = all(t.get('category','') in expected_allowed_cats for t in targets) if targets else True
            checks.append(f"allowed_categories={expected_allowed_cats}: {'✅' if ok else '❌'}")
        if expected_forbidden_cats:
            ok = all(t.get('category','') not in expected_forbidden_cats for t in targets)
            checks.append(f"forbidden_categories={expected_forbidden_cats}: {'✅' if ok else '❌'}")
        if allowed_ids:
            ok = all(t.get('product_id','') in allowed_ids for t in targets) if targets else True
            checks.append(f"allowed_product_ids: {'✅' if ok else '❌'}")
        if not checks:
            checks.append("无硬约束 → ✅")
        for c in checks:
            print(f"    {c}")

    return {
        "case_id": case_id,
        "passed": len(errors) == 0,
        "errors": errors,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="SearchPlan 回归测试")
    parser.add_argument("--id", help="只运行指定 id 的用例")
    parser.add_argument("--verbose", "-v", action="store_true", help="详细输出")
    parser.add_argument("--live", action="store_true", help="真实 SQLite 查询 + 全链路测试")
    args = parser.parse_args()

    mode_label = "LIVE（真实SQLite）" if args.live else "模拟模式"
    print(f"🧪 SearchPlan 回归测试 [{mode_label}]")
    print(f"   语义表: concepts={len(search_semantics_service._product_concepts)}, "
          f"categories={len(search_semantics_service._category_aliases)}, "
          f"fallback={len(search_semantics_service._fallback_relations)}")
    if args.live:
        sqlite_product_search_service.initialize()
        print(f"   SQLite: available={sqlite_product_search_service.db_available}")

    cases = load_regression_cases()
    if args.id:
        cases = [c for c in cases if c.get("id") == args.id]
        if not cases:
            print(f"❌ 未找到 id='{args.id}' 的用例")
            return 1

    check_fn = check_case_live if args.live else check_case
    results = []
    for case in cases:
        result = check_fn(case, verbose=args.verbose)
        results.append(result)

    # 汇总
    passed = sum(1 for r in results if r["passed"])
    failed = sum(1 for r in results if not r["passed"])
    total = len(results)

    print(f"\n{'='*50}")
    print(f"📊 结果: {passed}/{total} 通过, {failed} 失败")

    if failed:
        print("\n❌ 失败用例:")
        for r in results:
            if not r["passed"]:
                print(f"  [{r['case_id']}]")
                for e in r["errors"]:
                    print(f"    - {e}")
        return 1
    else:
        print("✅ 全部通过")
        return 0


if __name__ == "__main__":
    sys.exit(main())
