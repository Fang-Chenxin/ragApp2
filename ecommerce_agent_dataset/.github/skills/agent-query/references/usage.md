# Agent Query Usage Reference

## Entry Points

- `agent_search_from_text(text=...)`: preferred when the user gives a natural-language request.
- `agent_search_products(...)`: preferred when the agent already has structured filters.

## Result Fields

- `ok`: execution success flag
- `error`: error message when `ok` is false
- `parsed`: extracted natural-language structure
- `query_sql`: executed SQL
- `query_params`: SQL parameters
- `resolved_filters`: final filters used by the query layer
- `total`: matched product count
- `items`: matched products

## Agent Handling Rules

- Use `show_skus=True` only when the user needs SKU-level detail.
- If `total` is 0, report that the dataset does not contain a match.
- If `parsed.attr_filters` exists, summarize the normalized attribute families in plain language.
- Prefer concise product summaries instead of raw JSON unless the user requests the full payload.

## Smoke Test Inputs

- `银色 1TB 的数码平板`
- `苹果 深空黑 16GB 512GB 笔记本`
- `黑色 M 码 瑜伽裤`
- `控油保湿 30ml 精华`
- `500ml 0糖气泡水`
