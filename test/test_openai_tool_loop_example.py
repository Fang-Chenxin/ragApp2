"""OpenAI Python SDK example for local tool calling.

This shows the exact loop you need when your agent program uses the OpenAI
library directly:
1. Send messages and tool specs to OpenAI.
2. If the model requests `query_products`, execute it locally.
3. Send the tool result back as a tool message.
4. Repeat until the model returns a final answer.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from openai import APIConnectionError, APITimeoutError, OpenAI

# 添加后端路径以导入查询引擎
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))
from service.product_search.engine import agent_search_by_rule_parsed_text, agent_search_products

TOOL_NAME = "query_products"
TOOL_SPEC: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": TOOL_NAME,
        "description": (
            "Query the local SQLite product database. "
            "PREFER structured parameters (category/sub_category/brand/attr_filters) over the text parameter. "
            "Only use 'text' when the user input is already a concrete product spec like 'silver 1TB tablet'. "
            "For vague queries like 'recommend a phone', extract category='数码电子', sub_category='智能手机', etc."
        ),
        "parameters": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "text": {"type": "string", "description": "Concise product spec like '银色1TB平板'. Do NOT use for vague/recommendation queries — use structured params instead."},
                "keyword": {"type": ["string", "null"], "description": "Product keyword for text search, e.g. '手机', '笔记本'"},
                "brand": {"type": ["string", "null"], "description": "Brand name, e.g. 'Apple', '华为'"},
                "category": {"type": ["string", "null"], "description": "Main category, exactly one of: 数码电子, 服饰运动, 美妆护肤, 食品饮料"},
                "sub_category": {"type": ["string", "null"], "description": "Sub category, e.g. 智能手机, 笔记本电脑, 平板电脑, 瑜伽裤, 精华"},
                "attr_filters": {
                    "type": "array",
                    "items": {
                        "oneOf": [
                            {
                                "type": "object",
                                "required": ["key", "value"],
                                "properties": {
                                    "key": {"type": "string"},
                                    "value": {"type": "string"},
                                },
                                "additionalProperties": False,
                            },
                            {
                                "type": "array",
                                "minItems": 2,
                                "maxItems": 2,
                                "items": {"type": "string"},
                            },
                        ]
                    },
                },
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                "show_skus": {"type": "boolean", "default": True},
            },
        },
    },
}


def list_tools() -> list[dict[str, Any]]:
    return [TOOL_SPEC]


def _normalize_attr_filters(raw_filters: Any) -> list[dict[str, str]]:
    if not raw_filters:
        return []
    resolved: list[dict[str, str]] = []
    for item in raw_filters:
        if isinstance(item, dict):
            key = str(item.get("key", "")).strip()
            value = str(item.get("value", "")).strip()
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            key = str(item[0]).strip()
            value = str(item[1]).strip()
        else:
            raise ValueError(f"Invalid attr_filters item: {item!r}")
        if not key or not value:
            raise ValueError(f"Invalid attr_filters item: {item!r}")
        resolved.append({"key": key, "value": value})
    return resolved


def run_tool(tool_name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    if tool_name != TOOL_NAME:
        return {
            "ok": False,
            "error": f"Unknown tool: {tool_name}",
            "query_sql": "",
            "query_params": [],
            "resolved_filters": {},
            "total": 0,
            "items": [],
        }

    args = arguments or {}
    text = args.get("text")
    limit = int(args.get("limit", 10))
    show_skus = bool(args.get("show_skus", True))

    if text:
        return agent_search_by_rule_parsed_text(text=str(text), limit=limit, show_skus=show_skus)

    attr_filters = _normalize_attr_filters(args.get("attr_filters"))
    if not any([args.get("keyword"), args.get("brand"), args.get("category"), args.get("sub_category"), attr_filters]):
        return {
            "ok": False,
            "error": "query_products requires either text or at least one structured filter",
            "query_sql": "",
            "query_params": [],
            "resolved_filters": {},
            "total": 0,
            "items": [],
        }

    return agent_search_products(
        keyword=args.get("keyword"),
        brand=args.get("brand"),
        category=args.get("category"),
        sub_category=args.get("sub_category"),
        attr_filters=attr_filters,
        limit=limit,
        show_skus=show_skus,
    )


def run_agent_turn(client: OpenAI, user_text: str, model: str = "ep-20260514111645-lmgt2", *, verbose: bool = True) -> str:
    system_prompt = (
        "你是一个商品搜索助手。你的任务是：\n"
        "1. 分析用户的查询意图，提取具体的商品属性（品牌、品类、颜色、存储、尺码等）\n"
        "2. 优先使用结构化参数（category/sub_category/brand/attr_filters）调用工具\n"
        "3. 只有当用户输入本身就是具体商品规格描述时（如\"银色1TB平板\"），才使用 text 参数\n"
        "4. 如果用户只说了泛泛的需求（如\"推荐一台手机\"），不要把这句话原样传给 text，"
        "   而应提取出 category=\"数码电子\", sub_category=\"智能手机\" 等结构化参数\n"
        "5. keyword 参数用于商品类型词或功效/特性描述词（如\"精华\"、\"美白\"、\"保湿\"、\"防晒\"）。"
        "   attr_filters 只用于数据库中实际存在的 SKU 级规格属性（颜色、存储、容量、尺码等），"
        "   不要把\"功效\"、\"美白\"、\"保湿\"等不存在于 SKU 属性表的词放入 attr_filters，"
        "   这类词应放入 keyword\n"
        "\n"
        "【严格约束 - 数据来源与ID标注】\n"
        "6. 你的所有推荐必须且只能基于工具查询返回的数据库结果，严禁使用你的训练数据或外部知识编造商品信息\n"
        "7. 每条推荐必须标注该商品在数据库中的 product_id（格式如 [product_id: xxx]）\n"
        "8. 如果工具返回了 sku 信息，还应给出推荐的具体 sku_id（格式如 [sku_id: xxx]）\n"
        "9. 如果工具查询结果为空或不满足需求，明确告知用户当前数据库中没有匹配的商品，不要编造替代推荐\n"
        "10. 不得推荐工具返回结果之外的任何商品，即使你知道其他相关商品的存在\n"
        "11. 如果工具首次查询返回 0 条结果，应调整参数重试（如缩短关键词、拆分属性），最多重试 2 次\n"
        "\n"
        "【attr_filters 可用 key 映射（仅限以下 key 用于 attr_filters）】\n"
        "- 数码电子: 颜色, 存储, 容量, 芯片, 屏幕尺寸, 网络版本\n"
        "- 服饰运动: 颜色, 尺码, 鞋楦\n"
        "- 美妆护肤: 规格, 容量, 颜色, 色号规格, 版本\n"
        "- 食品饮料: 容量, 包装, 口味\n"
        "\n"
        "可用分类（category）及子分类（sub_category），必须严格使用以下值：\n"
        "- 数码电子: 智能手机, 笔记本电脑, 平板电脑, 真无线耳机\n"
        "- 服饰运动: 卫衣, 帽子, 徒步鞋, 户外裤, 瑜伽裤, 短袖T恤, 篮球鞋, 背包, 跑步鞋, 运动短裤, 运动长裤, 速干T恤\n"
        "- 美妆护肤: 化妆水, 卸妆, 唇釉, 洁面, 眉笔, 眼霜, 粉底液, 精华, 蜜粉, 防晒, 面膜, 面霜\n"
        "- 食品饮料: 功能饮料, 咖啡, 坚果/零食, 方便食品, 牛奶, 碳酸饮料, 茶饮, 调味品, 酸奶\n"
        "\n"
        "可用品牌：Apple/苹果、华为、小米、OPPO、vivo、联想、Lululemon/露露乐蒙、Merrell/迈乐、Nike/耐克、阿迪达斯、安踏、李宁、特步、迪卡侬、萨洛蒙、HOKA、北面/The North Face、始祖鸟、优衣库、Osprey、兰蔻、雅诗兰黛、SK-II、资生堂、科颜氏、理肤泉、珀莱雅、玉兰油、芳珂、薇诺娜、花西子、完美日记、安热沙、方里、珊珂、AHC、The Ordinary、巴黎欧莱雅、蒙牛、伊利、金典、纯甄、三只松鼠、良品铺子、百草味、三顿半、农夫山泉、元气森林、东方树叶、可口可乐、红牛、东鹏、康师傅、统一、日清、雀巢、海天、李锦记\n"
        "\n"
        "输出格式示例：\n"
        "1. 【商品名】 - ¥价格\n"
        "   product_id: xxx | sku_id: xxx\n"
        "   推荐理由：..."
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_text},
    ]
    tools = list_tools()

    for turn in range(5):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
        )
        assistant_message = response.choices[0].message

        assistant_payload: dict[str, Any] = {"role": "assistant", "content": assistant_message.content}
        if assistant_message.tool_calls:
            assistant_payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": call.type,
                    "function": {
                        "name": call.function.name,
                        "arguments": call.function.arguments,
                    },
                }
                for call in assistant_message.tool_calls
            ]
        messages.append(assistant_payload)

        if not assistant_message.tool_calls:
            if verbose:
                print(f"\n{'='*60}")
                print(f"[Turn {turn + 1}] LLM 未请求工具调用，直接返回最终回答：")
                print(f"{'='*60}")
                print(assistant_message.content or "(空)")
            return assistant_message.content or ""

        for call in assistant_message.tool_calls:
            if verbose:
                print(f"\n{'='*60}")
                print(f"[Turn {turn + 1}] LLM 请求工具调用：")
                print(f"  工具名: {call.function.name}")
                print(f"  原始参数: {call.function.arguments}")

            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError as exc:
                if verbose:
                    print(f"  ❌ 参数解析失败: {exc}")
                tool_result = {
                    "ok": False,
                    "error": f"Invalid tool arguments JSON: {exc}",
                    "query_sql": "",
                    "query_params": [],
                    "resolved_filters": {},
                    "total": 0,
                    "items": [],
                }
            else:
                if verbose:
                    print(f"  解析后参数: {json.dumps(arguments, ensure_ascii=False)}")
                tool_result = run_tool(call.function.name, arguments)

            if verbose:
                print(f"\n  ── 工具执行结果 ──")
                print(f"  ok: {tool_result.get('ok')}")
                if tool_result.get("error"):
                    print(f"  error: {tool_result['error']}")
                if tool_result.get("query_sql"):
                    print(f"  SQL: {tool_result['query_sql']}")
                if tool_result.get("query_params"):
                    print(f"  参数: {tool_result['query_params']}")
                print(f"  命中: {tool_result.get('total', 0)} 条")
                for item in tool_result.get("items", [])[:3]:
                    name = item.get("name", "?")
                    pid = item.get("product_id", "?")
                    print(f"    - [{pid}] {name}")
                if (tool_result.get("total") or 0) > 3:
                    print(f"    ... 共 {tool_result['total']} 条")

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": call.id,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                }
            )

    raise RuntimeError("Tool-calling loop did not converge within 5 steps")


def build_client(timeout_seconds: float) -> OpenAI:
    """Create an OpenAI client and fail early if the API key is missing."""

    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            "LLM_API_KEY is not set. Export it first, for example: export LLM_API_KEY=..."
        )
    base_url = os.environ.get("LLM_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
    return OpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)


def main() -> int:
    import time

    parser = argparse.ArgumentParser(description="测试 LLM 工具调用能力")
    parser.add_argument("--model", default="ep-20260514111645-lmgt2", help="模型名称")
    parser.add_argument("--prompt", default="银色 1TB 的数码平板", help="用户查询")
    parser.add_argument("--timeout-seconds", type=float, default=60.0, help="请求超时(秒)")
    parser.add_argument("--quiet", action="store_true", help="只输出最终回答")
    args = parser.parse_args()

    print(f"模型: {args.model}")
    print(f"查询: {args.prompt}")
    print(f"超时: {args.timeout_seconds}s")

    t0 = time.time()
    try:
        client = build_client(args.timeout_seconds)
        answer = run_agent_turn(client, args.prompt, model=args.model, verbose=not args.quiet)
    except (APITimeoutError, APIConnectionError) as exc:
        print(f"\n❌ API 连接失败或超时: {exc}")
        return 2
    except RuntimeError as exc:
        print(f"\n❌ {exc}")
        return 1

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"最终回答（耗时 {elapsed:.1f}s）：")
    print(f"{'='*60}")
    print(answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
