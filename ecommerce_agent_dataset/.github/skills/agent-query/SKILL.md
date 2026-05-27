---
name: agent-query
description: 'Query ecommerce products from the local SQLite database using natural language or structured filters. Use for Chinese product search, brand/category/attribute extraction, SKU lookup, and agent-friendly result interpretation.'
argument-hint: 'natural language query or structured filters'
user-invocable: true
disable-model-invocation: false
---

# Agent Query Skill

Use this skill when you need to answer product lookup requests against the local ecommerce database in this workspace.

## What This Skill Does

- Converts natural language product requests into structured filters
- Calls the local query entrypoint documented in [usage](./references/usage.md)
- Returns products, parsed filters, and optional SKU details
- Keeps the database unchanged and performs synonym / ontology expansion only at query time

## When to Use

Use this skill for requests like:

- "银色 1TB 的数码平板"
- "苹果 深空黑 16GB 512GB 笔记本"
- "黑色 M 码 瑜伽裤"
- "500ml 0糖气泡水"
- "查一下某个品牌下某类商品"
- "帮我看这个商品有哪些 SKU"

## Recommended Call Flow

1. Prefer `agent_search_from_text(text=...)` for natural language input.
2. If the user already provides structured filters, use `agent_search_products(...)` directly.
3. Inspect the returned `parsed` field to verify category, brand, and attribute family extraction.
4. Use `show_skus=True` only when the user explicitly needs SKU-level detail.
5. If the first result set is empty, keep the agent response honest and mention that the query may be too narrow or the dataset may not contain a matching item.

## Available Query Entrypoints

### Natural Language

Use this when the user gives a full sentence or mixed hints.

```python
from query_sqlite_with_synonyms import agent_search_from_text, DEFAULT_DB_PATH

result = agent_search_from_text(
    text="银色 1TB 的数码平板",
    db_path=DEFAULT_DB_PATH,
    limit=5,
    show_skus=False,
)
```

### Structured Filters

Use this when filters are already known.

```python
from query_sqlite_with_synonyms import agent_search_products, DEFAULT_DB_PATH

result = agent_search_products(
    db_path=DEFAULT_DB_PATH,
    keyword="笔记本",
    brand="Apple",
    category="数码电子",
    attr_filters=[{"key": "颜色", "value": "深空黑"}, {"key": "存储", "value": "1tb"}],
    limit=5,
    show_skus=True,
)
```

## How To Interpret Results

The returned object contains:

- `ok`: whether the query executed successfully
- `error`: the error message when `ok` is false
- `parsed`: the natural-language extraction result when using `agent_search_from_text`
- `query_sql`: the SQL statement that was executed
- `query_params`: the SQL parameters
- `resolved_filters`: the final structured filters
- `total`: number of matched products
- `items`: matched products

When `show_skus=True`, each item includes a `skus` array with SKU attribute details.

## Agent Response Rules

- If `ok` is false, report the failure briefly and do not invent a result.
- If `total` is 0, say the query did not match this dataset.
- If `parsed.category` is present, use it as the main category explanation.
- If `parsed.attr_filters` is present, summarize them in human-readable form.
- If `items` contains multiple products, present the top matches in a compact list.
- If SKU details are present, mention the key SKU differences instead of dumping raw JSON unless the user explicitly asks for raw output.

## Typical Query Patterns

### Product Lookup

Use for matching a product by brand, category, and attribute hints.

### SKU Drill-Down

Use for cases where the same product has multiple variants and the user wants the exact SKU composition.

### Attribute Family Resolution

Use the ontology-backed query layer to normalize terms such as:

- color families
- storage and capacity terms
- dimension and size terms
- packaging and flavor terms

## Quick Test Cases

These are good smoke-test inputs for the skill:

- `银色 1TB 的数码平板`
- `苹果 深空黑 16GB 512GB 笔记本`
- `黑色 M 码 瑜伽裤`
- `控油保湿 30ml 精华`
- `500ml 0糖气泡水`

For local verification, run [agent_query_smoke_test.py](../../agent_query_smoke_test.py).

## Suggested Agent Behavior

When a user asks for a product query, respond in this order:

1. Restate the interpreted query briefly.
2. Show the matched products or say none were found.
3. Mention any notable extracted filters.
4. If needed, ask a follow-up only when the query is too ambiguous to be useful.
