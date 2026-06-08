#!/usr/bin/env python3
"""校验 search_semantics 目录下所有 JSON 数据文件的结构完整性。

用法:
    python test/validate_search_semantics.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_DATA_DIR = Path(__file__).resolve().parent.parent / "ecommerce_agent_dataset" / "search_semantics"
ERRORS: list[str] = []


def err(msg: str) -> None:
    ERRORS.append(msg)
    print(f"  ❌ {msg}")


def ok(msg: str) -> None:
    print(f"  ✅ {msg}")


def validate_product_concepts() -> None:
    """校验 product_concepts.json。"""
    path = _DATA_DIR / "product_concepts.json"
    print(f"\n📋 校验 {path.name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        err("根节点必须是对象")
        return
    for name, concept in data.items():
        if not isinstance(concept, dict):
            err(f"「{name}」值必须是对象")
            continue
        for key in ("direct_terms", "fallback_terms"):
            val = concept.get(key)
            if not isinstance(val, list):
                err(f"「{name}.{key}」必须是数组")
            elif not all(isinstance(t, str) for t in val):
                err(f"「{name}.{key}」数组元素必须是字符串")
        cat = concept.get("category", "")
        if cat and cat not in ("美妆护肤", "数码电子", "服饰运动", "食品饮料", ""):
            err(f"「{name}.category」值 '{cat}' 不在四大类目中")
    ok(f"共 {len(data)} 个概念条目")


def validate_category_aliases() -> None:
    """校验 category_aliases.json。"""
    path = _DATA_DIR / "category_aliases.json"
    print(f"\n📋 校验 {path.name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        err("根节点必须是对象")
        return
    for name, alias in data.items():
        if not isinstance(alias, dict):
            err(f"「{name}」值必须是对象")
            continue
        cat = alias.get("category", "")
        sub = alias.get("sub_category", "")
        if not cat:
            err(f"「{name}」缺少 category")
        if not sub:
            err(f"「{name}」缺少 sub_category")
        aliases = alias.get("aliases", [])
        if not isinstance(aliases, list):
            err(f"「{name}.aliases」必须是数组")
    ok(f"共 {len(data)} 个品类别名条目")


def validate_fallback_relations() -> None:
    """校验 fallback_relations.json。"""
    path = _DATA_DIR / "fallback_relations.json"
    print(f"\n📋 校验 {path.name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        err("根节点必须是对象")
        return
    for name, relation in data.items():
        if not isinstance(relation, dict):
            err(f"「{name}」值必须是对象")
            continue
        acceptable = relation.get("acceptable", [])
        if not isinstance(acceptable, list):
            err(f"「{name}.acceptable」必须是数组")
        else:
            for i, item in enumerate(acceptable):
                if not isinstance(item, dict):
                    err(f"「{name}.acceptable[{i}]」必须是对象")
                elif not item.get("category"):
                    err(f"「{name}.acceptable[{i}]」缺少 category")
        forbidden = relation.get("forbidden_categories", [])
        if not isinstance(forbidden, list):
            err(f"「{name}.forbidden_categories」必须是数组")
    ok(f"共 {len(data)} 个 fallback 关系条目")


def validate_brand_model_aliases() -> None:
    """校验 brand_model_aliases.json。"""
    path = _DATA_DIR / "brand_model_aliases.json"
    print(f"\n📋 校验 {path.name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        err("根节点必须是对象")
        return
    for name, model in data.items():
        if not isinstance(model, dict):
            err(f"「{name}」值必须是对象")
            continue
        if not model.get("brand"):
            err(f"「{name}」缺少 brand")
        if not isinstance(model.get("direct_terms", []), list):
            err(f"「{name}.direct_terms」必须是数组")
        strict = model.get("strict_direct", False)
        if not isinstance(strict, bool):
            err(f"「{name}.strict_direct」必须是布尔值")
    ok(f"共 {len(data)} 个品牌型号条目")


def validate_scenario_tags() -> None:
    """校验 scenario_tags.json。"""
    path = _DATA_DIR / "scenario_tags.json"
    print(f"\n📋 校验 {path.name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        err("根节点必须是对象")
        return
    for name, tag in data.items():
        if not isinstance(tag, dict):
            err(f"「{name}」值必须是对象")
            continue
        for key in ("query_terms", "preferred_sub_categories", "ranking_hints"):
            if not isinstance(tag.get(key, []), list):
                err(f"「{name}.{key}」必须是数组")
    ok(f"共 {len(data)} 个场景标签条目")


def validate_regression_cases() -> None:
    """校验 regression_cases.json。"""
    path = _DATA_DIR / "regression_cases.json"
    print(f"\n📋 校验 {path.name}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        err("根节点必须是数组")
        return
    ids_seen: set[str] = set()
    for i, case in enumerate(data):
        if not isinstance(case, dict):
            err(f"[{i}] 条目必须是对象")
            continue
        case_id = case.get("id", "")
        if not case_id:
            err(f"[{i}] 缺少 id")
        elif case_id in ids_seen:
            err(f"[{i}] id '{case_id}' 重复")
        else:
            ids_seen.add(case_id)
        if not case.get("query"):
            err(f"[{i}] 缺少 query")
        expect = case.get("expect", {})
        if not isinstance(expect, dict):
            err(f"[{i}].expect 必须是对象")
    ok(f"共 {len(data)} 条回归用例")


def main() -> int:
    print("🔍 search_semantics 数据校验")
    print(f"   目录: {_DATA_DIR}")

    if not _DATA_DIR.exists():
        print(f"\n❌ 目录不存在: {_DATA_DIR}")
        return 1

    validate_product_concepts()
    validate_category_aliases()
    validate_fallback_relations()
    validate_brand_model_aliases()
    validate_scenario_tags()
    validate_regression_cases()

    print(f"\n{'='*50}")
    if ERRORS:
        print(f"❌ 发现 {len(ERRORS)} 个问题:")
        for e in ERRORS:
            print(f"   - {e}")
        return 1
    else:
        print("✅ 所有校验通过")
        return 0


if __name__ == "__main__":
    sys.exit(main())
