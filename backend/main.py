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

from config.settings import settings
from service.llm_service import llm_service
from service.rag_service import initialize_services, cleanup_services
from api.chat import router as chat_router
from api.knowledge import router as knowledge_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理

    使用 FastAPI 的 lifespan 管理器替代之前的 global 变量模式，
    确保服务启动和关闭时的资源管理。
    """
    print("🚀 正在启动服务...")

    # 初始化服务
    try:
        initialize_services()
        print("✅ 服务启动成功")
    except Exception as e:
        print(f"❌ 服务启动失败: {e}")
        raise

    yield

    # 清理资源
    print("🛑 正在关闭服务...")
    cleanup_services()
    print("✅ 服务已关闭")


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


@app.get("/")
async def root():
    """根路径 - 服务健康检查"""
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
    from service.rag_service import vector_store

    return {
        "status": "healthy",
        "services": {
            "vector_store": vector_store is not None,
            "llm_client": llm_service.client is not None
        },
        "stats": {
            "total_documents": vector_store.get_count() if vector_store else 0
        }
    }


if __name__ == "__main__":
    import uvicorn

    # 确保数据目录存在
    os.makedirs(settings.chroma_path, exist_ok=True)

    # 运行服务
    uvicorn.run(
        app,
        host=settings.server_host,
        port=settings.server_port,
        log_level="info"
    )
