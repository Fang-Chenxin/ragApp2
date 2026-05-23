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
    user_id: Optional[str] = "default"  # 用户标识
    conv_id: Optional[str] = None  # 会话ID（可选，不指定则使用当前会话）


class ChatResponse(BaseModel):
    """聊天响应模型"""
    reply: str
    history_saved: bool = True  # 标记历史是否保存成功
    conv_id: Optional[str] = None  # 当前会话ID


class ConversationInfo(BaseModel):
    """会话信息模型"""
    conv_id: str
    title: str
    created_at: str
    message_count: int
    last_message: str = ""


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
        
        # 确保用户有会话
        current_conv_id = history_service.ensure_default_conversation(request.user_id)
        
        # 如果指定了会话ID，则使用指定的会话
        if request.conv_id:
            # 验证会话是否属于该用户
            convs = history_service.get_conversations(request.user_id)
            conv_ids = [conv["conv_id"] for conv in convs]
            if request.conv_id in conv_ids:
                current_conv_id = request.conv_id

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
                history_saved=False,
                conv_id=current_conv_id
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
            history_service.save_message(request.user_id, current_conv_id, "user", request.user_query)
            # 保存助手回复
            history_service.save_message(request.user_id, current_conv_id, "assistant", reply)
        except Exception as e:
            print(f"保存对话历史失败: {e}")
            return ChatResponse(reply=reply, history_saved=False, conv_id=current_conv_id)

        return ChatResponse(reply=reply, history_saved=True, conv_id=current_conv_id)

    except Exception as e:
        error_msg = str(e)
        print(f"聊天接口错误: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@router.post("/conversations/{user_id}")
async def create_conversation(user_id: str, title: Optional[str] = None):
    """创建新会话

    Args:
        user_id: 用户标识
        title: 会话标题（可选）

    Returns:
        新创建的会话信息
    """
    try:
        from service.history_service import history_service

        conv_id = history_service.create_conversation(user_id, title)
        
        # 获取创建的会话信息
        convs = history_service.get_conversations(user_id)
        new_conv = next((conv for conv in convs if conv["conv_id"] == conv_id), None)
        
        if new_conv:
            return {
                "status": "success",
                "conversation": new_conv
            }
        else:
            raise HTTPException(status_code=500, detail="创建会话失败")

    except Exception as e:
        error_msg = str(e)
        print(f"创建会话失败: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/conversations/{user_id}")
async def get_conversations(user_id: str):
    """获取用户的所有会话列表

    Args:
        user_id: 用户标识

    Returns:
        会话列表
    """
    try:
        from service.history_service import history_service

        conversations = history_service.get_conversations(user_id)
        current_conv = history_service.get_current_conversation(user_id)

        return {
            "user_id": user_id,
            "current_conv": current_conv,
            "conversations": conversations,
            "count": len(conversations)
        }

    except Exception as e:
        error_msg = str(e)
        print(f"获取会话列表失败: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@router.post("/conversations/{user_id}/switch/{conv_id}")
async def switch_conversation(user_id: str, conv_id: str):
    """切换到指定会话

    Args:
        user_id: 用户标识
        conv_id: 会话ID

    Returns:
        操作结果
    """
    try:
        from service.history_service import history_service

        success = history_service.switch_conversation(user_id, conv_id)

        if success:
            # 获取切换后的会话信息
            convs = history_service.get_conversations(user_id)
            conv = next((c for c in convs if c["conv_id"] == conv_id), None)
            
            return {
                "status": "success",
                "message": f"已切换到会话: {conv['title'] if conv else conv_id}",
                "conversation": conv
            }
        else:
            raise HTTPException(status_code=404, detail=f"会话 {conv_id} 不存在")

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        print(f"切换会话失败: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@router.delete("/conversations/{user_id}/{conv_id}")
async def delete_conversation(user_id: str, conv_id: str):
    """删除指定会话

    Args:
        user_id: 用户标识
        conv_id: 会话ID

    Returns:
        操作结果
    """
    try:
        from service.history_service import history_service

        success = history_service.delete_conversation(user_id, conv_id)

        if success:
            return {
                "status": "success",
                "message": f"会话已删除",
                "current_conv": history_service.get_current_conversation(user_id)
            }
        else:
            raise HTTPException(status_code=404, detail=f"会话 {conv_id} 不存在")

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        print(f"删除会话失败: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@router.put("/conversations/{user_id}/{conv_id}/title")
async def update_conversation_title(user_id: str, conv_id: str, title: str):
    """更新会话标题

    Args:
        user_id: 用户标识
        conv_id: 会话ID
        title: 新标题

    Returns:
        操作结果
    """
    try:
        from service.history_service import history_service

        success = history_service.update_conversation_title(user_id, conv_id, title)

        if success:
            return {
                "status": "success",
                "message": "标题已更新",
                "conv_id": conv_id,
                "title": title
            }
        else:
            raise HTTPException(status_code=404, detail=f"会话 {conv_id} 不存在")

    except HTTPException:
        raise
    except Exception as e:
        error_msg = str(e)
        print(f"更新会话标题失败: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@router.get("/history/{user_id}")
async def get_history(user_id: str, conv_id: Optional[str] = None, limit: Optional[int] = None):
    """获取对话历史

    Args:
        user_id: 用户标识
        conv_id: 会话ID（可选，不指定则使用当前会话）
        limit: 限制返回的消息数量（最近 N 条）

    Returns:
        消息历史列表
    """
    try:
        from service.history_service import history_service

        history = history_service.load_history(user_id, conv_id, limit)
        current_conv = conv_id if conv_id else history_service.get_current_conversation(user_id)

        return {
            "user_id": user_id,
            "conv_id": current_conv,
            "total": len(history),
            "history": history
        }

    except Exception as e:
        error_msg = str(e)
        print(f"获取历史记录失败: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)


@router.delete("/history/{user_id}")
async def clear_history(user_id: str, conv_id: Optional[str] = None):
    """清空指定会话的历史（保留会话）

    Args:
        user_id: 用户标识
        conv_id: 会话ID（可选，不指定则使用当前会话）

    Returns:
        操作结果
    """
    try:
        from service.history_service import history_service

        success = history_service.clear_history(user_id, conv_id)
        current_conv = conv_id if conv_id else history_service.get_current_conversation(user_id)

        if success:
            return {
                "status": "success",
                "message": f"会话历史已清空",
                "conv_id": current_conv
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
async def get_history_count(user_id: str, conv_id: Optional[str] = None):
    """获取消息数量

    Args:
        user_id: 用户标识
        conv_id: 会话ID（可选，不指定则使用当前会话）

    Returns:
        消息数量
    """
    try:
        from service.history_service import history_service

        count = history_service.get_history_count(user_id, conv_id)
        current_conv = conv_id if conv_id else history_service.get_current_conversation(user_id)

        return {
            "user_id": user_id,
            "conv_id": current_conv,
            "count": count
        }

    except Exception as e:
        error_msg = str(e)
        print(f"获取消息数量失败: {error_msg}")
        raise HTTPException(status_code=500, detail=error_msg)
