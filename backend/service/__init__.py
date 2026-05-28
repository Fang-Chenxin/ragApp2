"""服务层 — 统一管理所有服务的初始化与清理"""
import importlib
from .llm_service import llm_service, LLMService
from .rag_service import (
    rag_service,
    RAGService,
    vector_store,
    VectorStore,
    embedding_service,
    EmbeddingService,
)
from .history_service import history_service, HistoryService


def initialize_services():
    """初始化所有服务（按依赖顺序）"""
    llm_service.initialize()
    embedding_service.initialize()
    vector_store.initialize()

    # rag_service 依赖 vector_store 和 llm_service，需在它们之后初始化
    # 通过 importlib 获取模块对象，直接修改其 rag_service 变量
    _rag_mod = importlib.import_module(".rag_service", package=__name__)
    _rag_mod.rag_service = RAGService(vector_store, llm_service)
    # 同步更新本模块的命名空间，使 "from service import rag_service" 能拿到新实例
    globals()["rag_service"] = _rag_mod.rag_service

    # ecommerce_service 自行初始化
    from .ecommerce_service import ecommerce_service
    ecommerce_service.initialize()

    print("✅ 所有服务初始化完成")


def cleanup_services():
    """清理所有服务"""
    from .ecommerce_service import ecommerce_service
    ecommerce_service.close()
    print("✅ 所有服务已关闭")


__all__ = [
    "llm_service",
    "LLMService",
    "rag_service",
    "RAGService",
    "vector_store",
    "VectorStore",
    "embedding_service",
    "EmbeddingService",
    "initialize_services",
    "cleanup_services",
    "history_service",
    "HistoryService",
]
