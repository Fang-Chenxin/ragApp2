"""
Agent 对话应用后端服务
使用 FastAPI + ChromaDB + RAG 架构
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import sys

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# 添加项目根目录，使商品数据集可作为正式包导入
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config.settings import settings
from config.logging_config import setup_logging, get_logger
from service import initialize_services, cleanup_services, llm_service, vector_store, embedding_service
from api.chat import router as chat_router
from api.knowledge import router as knowledge_router
from api.sqlite_product_search import router as sqlite_product_search_router

# 初始化日志（必须在所有模块导入之后）
setup_logging(log_level=settings.log_level, log_file=settings.log_file, console_level=settings.console_log_level)
logger = get_logger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理。

    FastAPI 在启动时进入此上下文，先初始化服务层的全局单例；
    退出时再释放服务资源。API 路由中依赖的 `service.*` 对象都在这里完成装配。
    """
    logger.info(
        "🚀 正在启动服务...\n"
        "✅ LLM 服务初始化中...\n"
        "✅ Embedding 服务初始化中...\n"
        "✅ 向量库初始化中...\n"
        "✅ SQLite 商品搜索服务初始化中..."
    )

    # 初始化所有服务（LLM、Embedding、向量库、SQLite 商品搜索）
    try:
        initialize_services()
        logger.info(
            "✅ 服务启动成功\n"
            f"  ├── 服务地址: http://{settings.server_host}:{settings.server_port}\n"
            "  └── 文档地址: /docs"
        )
    except Exception as e:
        logger.error("❌ 服务启动失败: %s", e)
        raise

    yield

    # 清理资源
    logger.info("🛑 正在关闭服务...")
    cleanup_services()
    logger.info("✅ 服务已关闭")


# 创建 FastAPI 应用实例
app = FastAPI(
    title="Agent 对话应用后端",
    description="基于 RAG 架构的智能对话系统",
    version="1.0.0",
    lifespan=lifespan
)

# 配置 CORS 中间件
# 安全性改进：将 CORS origins 从 "*" 改为配置化管理
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(chat_router)
app.include_router(knowledge_router)
app.include_router(sqlite_product_search_router)


@app.get("/")
async def root():
    """返回服务基本信息，用于浏览器直接访问根路径时确认后端已启动。"""
    return {
        "message": "Agent 对话应用后端服务运行正常！",
        "version": "1.0.0",
        "docs": "/docs"
    }


@app.get("/health")
async def health_check():
    """健康检查端点

    用于 Kubernetes/负载均衡器的健康探测
    """

    # 这里保持轻量检查，只读取内存状态和向量库计数，避免健康探针触发外部 LLM 请求。
    return {
        "status": "healthy",
        "services": {
            "vector_store": vector_store is not None,
            "llm_client": llm_service.client is not None,
            "embedding": embedding_service.get_status(),
        },
        "stats": {
            "total_documents": vector_store.get_count() if vector_store else 0
        }
    }


if __name__ == "__main__":
    import uvicorn

    os.makedirs(settings.chroma_path, exist_ok=True)

    uvicorn.run(
        app,
        host=settings.server_host,
        port=settings.server_port,
        log_level="info",
        timeout_keep_alive=120,
    )
