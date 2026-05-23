"""API 路由层 - 聊天相关接口"""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import List

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    """聊天消息模型"""
    role: str
    content: str


class ChatRequest(BaseModel):
    """聊天请求模型"""
    messages: List[ChatMessage]
    user_query: str


class ChatResponse(BaseModel):
    """聊天响应模型"""
    reply: str


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """聊天接口 - 使用 RAG 进行智能对话

    Args:
        request: 聊天请求，包含对话历史和当前问题

    Returns:
        AI 回复

    Raises:
        HTTPException: 处理过程中的错误
    """
    try:
        # 在运行时动态导入服务模块（避免模块加载时服务未初始化的问题）
        from service.rag_service import rag_service
        from service.llm_service import llm_service
        
        # 检查 RAG 服务是否初始化
        if not rag_service:
            raise HTTPException(
                status_code=500,
                detail="RAG 服务未初始化，请检查服务器配置"
            )

        # 检查 LLM 服务是否连接成功
        if not llm_service.connected:
            # 返回模拟回复
            return ChatResponse(
                reply=f"您好！我收到了您的消息：'{request.user_query}'。\n\n"
                      f"这是模拟回复。要使用真实的AI对话功能，请配置 LLM_API_KEY 环境变量。"
            )

        # 构建对话历史（排除系统消息）
        history = [
            {"role": msg.role, "content": msg.content}
            for msg in request.messages
            if msg.role != "system"
        ]

        # 调用 RAG 服务
        reply = await rag_service.chat_with_rag(
            user_query=request.user_query,
            conversation_history=history
        )

        return ChatResponse(reply=reply)

    except Exception as e:
        error_msg = str(e)
        print(f"聊天接口错误: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)
