"""API 路由层 - 聊天相关接口"""
from fastapi import APIRouter, HTTPException, Header
from pydantic import BaseModel
from typing import List, Optional

router = APIRouter(prefix="/api", tags=["chat"])


class ChatMessage(BaseModel):
    """聊天消息模型"""
    role: str
    content: str


class ChatRequest(BaseModel):
    """聊天请求模型"""
    messages: List[ChatMessage]
    user_query: str
    user_id: Optional[str] = "default"  # 用户标识，用于保存对话历史


class ChatResponse(BaseModel):
    """聊天响应模型"""
    reply: str
    history_saved: bool = True  # 标记历史是否保存成功


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    """聊天接口 - 使用 RAG 进行智能对话，自动保存对话历史

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
        from service.history_service import history_service
        
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
                      f"这是模拟回复。要使用真实的AI对话功能，请配置 LLM_API_KEY 环境变量。",
                history_saved=False
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

        # 保存对话历史
        try:
            # 保存用户消息
            history_service.save_message(request.user_id, "user", request.user_query)
            # 保存助手回复
            history_service.save_message(request.user_id, "assistant", reply)
        except Exception as e:
            print(f"保存对话历史失败: {e}")
            return ChatResponse(reply=reply, history_saved=False)

        return ChatResponse(reply=reply, history_saved=True)

    except Exception as e:
        error_msg = str(e)
        print(f"聊天接口错误: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/history/{user_id}")
async def get_history(user_id: str, limit: Optional[int] = None):
    """获取用户对话历史

    Args:
        user_id: 用户标识
        limit: 限制返回的消息数量（最近 N 条）

    Returns:
        消息历史列表

    Raises:
        HTTPException: 处理过程中的错误
    """
    try:
        from service.history_service import history_service

        history = history_service.load_history(user_id, limit)

        return {
            "user_id": user_id,
            "total": len(history),
            "history": history
        }

    except Exception as e:
        error_msg = str(e)
        print(f"获取历史记录失败: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@router.delete("/history/{user_id}")
async def clear_history(user_id: str):
    """清空用户对话历史

    Args:
        user_id: 用户标识

    Returns:
        操作结果

    Raises:
        HTTPException: 处理过程中的错误
    """
    try:
        from service.history_service import history_service

        success = history_service.clear_history(user_id)

        if success:
            return {
                "status": "success",
                "message": f"用户 {user_id} 的对话历史已清空"
            }
        else:
            raise HTTPException(
                status_code=500,
                detail="清空历史记录失败"
            )

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        print(f"清空历史记录失败: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/history/{user_id}/count")
async def get_history_count(user_id: str):
    """获取用户消息数量

    Args:
        user_id: 用户标识

    Returns:
        消息数量
    """
    try:
        from service.history_service import history_service

        count = history_service.get_history_count(user_id)

        return {
            "user_id": user_id,
            "count": count
        }

    except Exception as e:
        error_msg = str(e)
        print(f"获取消息数量失败: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)
