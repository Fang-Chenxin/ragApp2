"""API 路由层 - 聊天相关接口"""
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from config.logging_config import get_logger
import json

logger = get_logger("api.chat")
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
    include_thinking: Optional[bool] = False  # 是否包含思考过程


class ChatResponse(BaseModel):
    """聊天响应模型"""
    reply: str
    history_saved: bool = True  # 标记历史是否保存成功
    conv_id: Optional[str] = None  # 当前会话ID
    timings: Optional[Dict[str, Any]] = None  # 各环节耗时（秒）


class ConversationInfo(BaseModel):
    """会话信息模型"""
    conv_id: str
    title: str
    created_at: str
    message_count: int
    last_message: str = ""


@router.post("/chat")
async def chat_endpoint(request: ChatRequest):
    """流式聊天接口（与 `/chat/stream` 等价） - 推荐统一使用流式调用以便客户端实时接收预览与调试信息"""
    try:
        from service import tool_chat_service, llm_service, history_service

        current_conv_id = history_service.ensure_default_conversation(request.user_id)

        if request.conv_id:
            convs = history_service.get_conversations(request.user_id)
            conv_ids = [conv["conv_id"] for conv in convs]
            if request.conv_id in conv_ids:
                current_conv_id = request.conv_id

        if not tool_chat_service:
            raise HTTPException(
                status_code=500,
                detail="SQLite 商品搜索聊天服务未初始化，请检查服务器配置"
            )

        if not llm_service.connected:
            async def mock_stream():
                mock_reply = f"您好！我收到了您的消息：'{request.user_query}'。\n\n这是模拟回复。要使用真实的AI对话功能，请配置 LLM_API_KEY 环境变量。"
                data = {
                    "content": mock_reply,
                    "conv_id": current_conv_id,
                    "history_saved": False,
                    "done": True
                }
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

            return StreamingResponse(
                mock_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                }
            )

        history = [
            {"role": msg.role, "content": msg.content}
            for msg in request.messages
            if msg.role != "system"
        ]

        async def generate_stream():
            full_reply = ""
            full_thinking = ""
            full_analysis = ""
            timings = None
            try:
                async for chunk in tool_chat_service.chat_with_tools_stream(
                    user_query=request.user_query,
                    conversation_history=history,
                    include_thinking=bool(request.include_thinking),
                ):
                    chunk_type = chunk.get("type")

                    if chunk_type == "status":
                        data = {
                            "status": chunk.get("content", ""),
                            "phase": chunk.get("phase"),
                            "agent": chunk.get("agent"),
                            "conv_id": current_conv_id,
                            "done": False,
                        }
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                    elif chunk_type == "analysis":
                        analysis_content = chunk.get("content", "")
                        if analysis_content:
                            full_analysis += analysis_content
                        data = {
                            "analysis": analysis_content,
                            "summary": chunk.get("summary", ""),
                            "conv_id": current_conv_id,
                            "done": False,
                        }
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                    elif chunk_type == "selected_products":
                        data = {
                            "status": chunk.get("content", ""),
                            "phase": "selected_products",
                            "agent": "shopping_agent",
                            "selected_product_ids": chunk.get("selected_product_ids", []),
                            "selected_products": chunk.get("selected_products", []),
                            "conv_id": current_conv_id,
                            "done": False,
                        }
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                    if chunk_type == "content":
                        content = chunk.get("content", "")
                        full_reply += content
                        data = {
                            "content": content,
                            "conv_id": current_conv_id,
                            "done": False
                        }
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                    elif chunk_type == "thinking":
                        thinking_content = chunk.get("content", "")
                        if thinking_content:
                            full_thinking += thinking_content
                        data = {
                            "thinking": thinking_content,
                            "conv_id": current_conv_id,
                            "done": False
                        }
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                    elif chunk_type == "done":
                        timings = chunk.get("timings")

                        if timings:
                            logger.info(
                                "[Timings] user=%s | 分析=%ss | 向量检索=%ss | LLM推理=%ss(%s轮) | SQLite工具查询=%ss(%s轮) | 总计=%ss",
                                request.user_id,
                                timings.get('analysis_calls', '-'),
                                timings.get('vector_search', '-'),
                                timings.get('llm_calls', '-'),
                                timings.get('llm_rounds', '?'),
                                timings.get('tool_calls', '-'),
                                timings.get('tool_rounds', '?'),
                                timings.get('total', '-'),
                            )

                        save_status = {
                            "status": "正在保存历史",
                            "phase": "saving_history",
                            "agent": "shopping_agent",
                            "conv_id": current_conv_id,
                            "done": False,
                        }
                        yield f"data: {json.dumps(save_status, ensure_ascii=False)}\n\n"

                        try:
                            history_service.save_message(request.user_id, current_conv_id, "user", request.user_query)
                            if full_thinking.strip():
                                history_service.save_message(request.user_id, current_conv_id, "assistant", full_reply, thinking=full_thinking)
                            else:
                                history_service.save_message(request.user_id, current_conv_id, "assistant", full_reply)
                            if full_analysis.strip():
                                logger.info("分析摘要: %s", full_analysis[:200].replace('\n', ' '))
                                logger.debug("分析完整内容:\n%s", full_analysis)
                            history_saved = True
                        except Exception as e:
                            logger.error("保存对话历史失败: %s", e)
                            history_saved = False

                        data = {
                            "content": "",
                            "conv_id": current_conv_id,
                            "history_saved": history_saved,
                            "timings": timings,
                            "done": True
                        }
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                        return

                    elif chunk_type == "error":
                        error_content = chunk.get("content", "未知错误")
                        error_data = {
                            "error": error_content,
                            "done": True
                        }
                        yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

                        return

            except Exception as e:
                error_data = {
                    "error": str(e),
                    "done": True
                }
                yield f"data: {json.dumps(error_data, ensure_ascii=False)}\n\n"

        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )

    except Exception as e:
        error_msg = str(e)
        logger.error("聊天接口错误: %s", error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


# 已统一为单一流式端点 `/chat`，`/chat/stream` 已移除。


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
        from service import history_service

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
        logger.error("创建会话失败: %s", error_msg)
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
        from service import history_service

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
        logger.error("获取会话列表失败: %s", error_msg)
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
        from service import history_service

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
        logger.error("切换会话失败: %s", error_msg)
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
        from service import history_service

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
        logger.error("删除会话失败: %s", error_msg)
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
        from service import history_service

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
        logger.error("更新会话标题失败: %s", error_msg)
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
        from service import history_service

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
        logger.error("获取历史记录失败: %s", error_msg)
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
        from service import history_service

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
        logger.error("清空历史记录失败: %s", error_msg)
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
        from service import history_service

        count = history_service.get_history_count(user_id, conv_id)
        current_conv = conv_id if conv_id else history_service.get_current_conversation(user_id)

        return {
            "user_id": user_id,
            "conv_id": current_conv,
            "count": count
        }

    except Exception as e:
        error_msg = str(e)
        logger.error("获取消息数量失败: %s", error_msg)
        raise HTTPException(status_code=500, detail=error_msg)
