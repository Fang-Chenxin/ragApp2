"""商品检索内部模块。

阅读入口:
- `engine.py`: ontology 解析、规则搜索和 SQL 查询构造
- `sqlite_search.py`: SQLite 商品搜索服务对象
- `query_tool.py`: OpenAI Function Calling 工具定义与执行分发
"""

from . import query_tool
from .sqlite_search import SQLiteProductSearchService, sqlite_product_search_service

__all__ = [
    "query_tool",
    "SQLiteProductSearchService",
    "sqlite_product_search_service",
]

