"""服务层 — 统一管理所有服务的初始化与清理"""
import importlib
from config.logging_config import get_logger
from .llm_service import llm_service, LLMService
from .rag_service import (
    rag_service,
    RAGService,
    vector_store,
    VectorStore,
    embedding_service,
    EmbeddingService,
)
from .tool_chat_service import (
    tool_chat_service,
    ToolChatService,
)
from .product_search import query_tool as sqlite_product_query_tool
from .product_search.sqlite_search import (
    sqlite_product_search_service,
    SQLiteProductSearchService,
)
from .history_service import history_service, HistoryService

logger = get_logger("service")


def initialize_services():
    """初始化所有服务（按依赖顺序）。

    这里维护全局单例的装配关系：先准备底层客户端，再创建依赖这些客户端的组合服务。
    API 层从本模块导入服务对象，因此更新 globals 可以让旧导入方式继续工作。
    """
    llm_service.initialize()
    embedding_service.initialize()
    vector_store.initialize()

    # rag_service 依赖 vector_store 和 llm_service，需在它们之后初始化。
    _rag_mod = importlib.import_module(".rag_service", package=__name__)
    _rag_mod.rag_service = RAGService(vector_store, llm_service)
    globals()["rag_service"] = _rag_mod.rag_service

    # sqlite_product_search_service 自行初始化，数据库缺失时会进入模拟结果模式。
    sqlite_product_search_service.initialize()

    # tool_chat_service 依赖向量库、LLM 和工具模块
    _tool_mod = importlib.import_module(".tool_chat_service", package=__name__)
    _tool_mod.tool_chat_service = ToolChatService(vector_store, llm_service)
    globals()["tool_chat_service"] = _tool_mod.tool_chat_service

    logger.info("✅ 所有服务初始化完成")


def cleanup_services():
    """清理所有服务。

    当前只有 SQLite 服务暴露 close 钩子；LLM httpx 客户端由进程退出时回收。
    """
    sqlite_product_search_service.close()
    logger.info("✅ 所有服务已关闭")


__all__ = [
    "llm_service",
    "LLMService",
    "rag_service",
    "RAGService",
    "vector_store",
    "VectorStore",
    "embedding_service",
    "EmbeddingService",
    "sqlite_product_query_tool",
    "sqlite_product_search_service",
    "SQLiteProductSearchService",
    "tool_chat_service",
    "ToolChatService",
    "initialize_services",
    "cleanup_services",
    "history_service",
    "HistoryService",
]
