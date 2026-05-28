#!/usr/bin/env python3
"""Smoke test for the agent query entrypoints.

This script exercises the natural-language entrypoint with representative
queries and prints the parsed structure and top results. It exits non-zero if
any query fails to execute.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# 添加后端路径以导入查询引擎
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from service.query_engine import DEFAULT_DB_PATH, agent_search_from_text


@dataclass(frozen=True)
class SampleCase:
    name: str
    text: str
    min_total: int = 0
    show_skus: bool = False


SAMPLES: list[SampleCase] = [
    SampleCase(name="digital-tablet", text="银色 1TB 的数码平板", min_total=1),
    SampleCase(name="digital-laptop", text="苹果 深空黑 16GB 512GB 笔记本", min_total=1),
    SampleCase(name="fashion-size-color", text="黑色 M 码 瑜伽裤", min_total=1),
    SampleCase(name="beauty-serum", text="控油保湿 30ml 精华", min_total=0),
    SampleCase(name="food-soda", text="500ml 0糖气泡水", min_total=0),
]


def run_case(text: str, db_path: Path, show_skus: bool) -> dict[str, Any]:
    result = agent_search_from_text(text=text, db_path=db_path, limit=5, show_skus=show_skus)
    if not result.get("ok"):
        raise RuntimeError(result.get("error") or "query failed")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test for agent query entrypoints.")
    parser.add_argument("--db-path", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--json", action="store_true", help="Print full JSON output for each sample")
    parser.add_argument("--strict", action="store_true", help="Fail if a sample does not reach min_total")
    args = parser.parse_args()

    failures: list[str] = []
    print(f"{'用例':<22} {'total':>5}  {'状态':<4}  产品ID")
    print("-" * 70)
    for sample in SAMPLES:
        try:
            result = run_case(sample.text, args.db_path, sample.show_skus)
        except Exception as exc:
            failures.append(f"{sample.name}: {type(exc).__name__}: {exc}")
            print(f"{sample.text:<22} {'ERR':>5}  {'FAIL':<4}  {type(exc).__name__}: {exc}")
            continue

        total = int(result.get("total", 0))
        items = result.get("items", [])
        ids = [item.get("product_id") for item in items]
        status = "PASS" if (not args.strict or total >= sample.min_total) else "FAIL"
        print(f"{sample.text:<22} {total:>5}  {status:<4}  {ids}")
        if status == "FAIL":
            failures.append(f"{sample.name}: expected >= {sample.min_total}, got {total}")

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))

    if failures:
        print("\nFAILURES:")
        for item in failures:
            print(f"- {item}")
        return 1
    print(f"\n全部通过 ({len(SAMPLES)}/{len(SAMPLES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())