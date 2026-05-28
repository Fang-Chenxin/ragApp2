"""API 路由层 - 知识库管理接口"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, Dict, Any

router = APIRouter(prefix="/api", tags=["knowledge"])


class AddKnowledgeRequest(BaseModel):
    """添加知识请求模型"""
    content: str
    metadata: Optional[Dict[str, Any]] = None


class AddKnowledgeResponse(BaseModel):
    """添加知识响应模型"""
    status: str
    message: str
    doc_id: Optional[str] = None


class KnowledgeStatsResponse(BaseModel):
    """知识库统计响应模型"""
    total_documents: int
    collection_name: str


@router.post("/add_knowledge", response_model=AddKnowledgeResponse)
async def add_knowledge(request: AddKnowledgeRequest):
    """添加知识到向量数据库

    Args:
        request: 包含内容和元数据的请求

    Returns:
        添加结果

    Raises:
        HTTPException: 处理过程中的错误
    """
    try:
        from service import vector_store

        if not vector_store:
            raise HTTPException(
                status_code=500,
                detail="向量数据库未初始化"
            )

        # 添加文档
        doc_id = vector_store.add_document(
            content=request.content,
            metadata=request.metadata
        )

        return AddKnowledgeResponse(
            status="success",
            message="知识添加成功",
            doc_id=doc_id
        )

    except Exception as e:
        error_msg = str(e)
        print(f"添加知识错误: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/knowledge/stats", response_model=KnowledgeStatsResponse)
async def get_knowledge_stats():
    """获取知识库统计信息

    Returns:
        知识库统计信息

    Raises:
        HTTPException: 处理过程中的错误
    """
    try:
        from service import vector_store
        from config.settings import settings

        if not vector_store:
            raise HTTPException(
                status_code=500,
                detail="向量数据库未初始化"
            )

        return KnowledgeStatsResponse(
            total_documents=vector_store.get_count(),
            collection_name=settings.chroma_collection_name
        )

    except Exception as e:
        error_msg = str(e)
        print(f"获取统计信息错误: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)
