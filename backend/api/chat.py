"""API 路由层 - 聊天相关接口"""
from fastapi import APIRouter, HTTPException, Header
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from config.logging_config import get_logger
import asyncio
import fnmatch
import httpx
import json
import re

logger = get_logger("api.chat")
router = APIRouter(prefix="/api", tags=["chat"])
_DISCOVERED_MODEL_CONFIGS: Dict[str, Dict[str, str]] = {}
_DISCOVERY_LOCK = asyncio.Lock()


class ChatMessage(BaseModel):
    """聊天消息模型"""
    role: str
    content: str


class ModelConnectionConfig(BaseModel):
    """客户端本地模型连接配置"""
    id: str
    name: Optional[str] = None
    source: str = "local"
    base_url: Optional[str] = None
    api_key: Optional[str] = None


class ChatRequest(BaseModel):
    """聊天请求模型"""
    messages: List[ChatMessage]
    user_query: str
    user_id: Optional[str] = "default"  # 用户标识
    conv_id: Optional[str] = None  # 会话ID（可选，不指定则使用当前会话）
    model: Optional[str] = None  # 本轮对话使用的模型 ID
    llm_config: Optional[ModelConnectionConfig] = Field(default=None, alias="model_config")  # 客户端本地模型接入配置


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


class ModelInfo(BaseModel):
    """可选模型信息"""
    id: str
    name: str
    source: str = "server"


class ModelsResponse(BaseModel):
    """模型清单响应"""
    default_model: str
    models: List[ModelInfo]


def _public_model_info(item: Dict[str, str]) -> ModelInfo:
    """隐藏 base_url/api_key，只向客户端暴露可展示的模型信息。"""
    return ModelInfo(
        id=item["id"],
        name=item["name"],
        source=item["source"],
    )


def _as_string_list(value: Any) -> List[str]:
    """把模型过滤配置统一转成字符串列表。"""
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    return []


def _matches_any_pattern(model_id: str, patterns: List[str]) -> bool:
    """判断模型 ID 是否命中任意通配符模式。"""
    return any(fnmatch.fnmatch(model_id, pattern) for pattern in patterns)


def _normalize_model_family(model_id: str) -> str:
    """把常见日期/上下文/参数后缀折叠为同一模型族。"""
    normalized = model_id.lower()
    normalized = re.sub(r"[-_:](20\d{2}[-_]?\d{2}[-_]?\d{2}|\d{6,8})$", "", normalized)
    normalized = re.sub(r"[-_:](4k|8k|16k|32k|64k|128k|256k|1m)$", "", normalized)
    normalized = re.sub(r"[-_:](fp8|fp16|bf16|int4|int8|q4|q8)$", "", normalized)
    normalized = re.sub(r"[-_:](free|latest|preview|beta|stable)$", "", normalized)
    return normalized


def _filter_discovered_models(
    models: List[Dict[str, str]],
    *,
    allow_models: List[str],
    deny_models: List[str],
    collapse_variants: bool,
    ark_endpoint_only: bool,
) -> List[Dict[str, str]]:
    """按 allow/deny/方舟接入点规则过滤自动发现的模型。"""
    filtered: List[Dict[str, str]] = []
    for model in models:
        model_id = model["id"]
        if ark_endpoint_only and not model_id.startswith("ep-"):
            continue
        if allow_models and not _matches_any_pattern(model_id, allow_models):
            continue
        if deny_models and _matches_any_pattern(model_id, deny_models):
            continue
        filtered.append(model)

    if not collapse_variants:
        return filtered

    # 某些服务会返回同模型不同日期/上下文版本，这里按模型族折叠为一个展示项。
    by_family: Dict[str, Dict[str, str]] = {}
    for model in filtered:
        family = _normalize_model_family(model["id"])
        current = by_family.get(family)
        if current is None or len(model["id"]) < len(current["id"]):
            by_family[family] = model
    return list(by_family.values())


async def _discover_models_from_sources() -> List[Dict[str, str]]:
    """从服务端配置的 OpenAI-compatible 来源发现模型，并缓存其连接配置。"""
    from config.settings import settings

    if _DISCOVERED_MODEL_CONFIGS:
        return list(_DISCOVERED_MODEL_CONFIGS.values())

    async with _DISCOVERY_LOCK:
        if _DISCOVERED_MODEL_CONFIGS:
            return list(_DISCOVERED_MODEL_CONFIGS.values())

        return await _discover_models_from_sources_unlocked(settings)


async def _discover_models_from_sources_unlocked(settings) -> List[Dict[str, str]]:
    """执行实际模型发现；调用方负责并发保护。"""
    discovered: List[Dict[str, str]] = []
    for source in settings.llm_model_discovery_sources:
        models_url = source["base_url"].rstrip("/") + "/models"
        try:
            # 这里直接访问 OpenAI-compatible /models，仅用于生成客户端可选列表。
            async with httpx.AsyncClient(timeout=20.0, trust_env=False) as client:
                response = await client.get(
                    models_url,
                    headers={"Authorization": f"Bearer {source['api_key']}"},
                )
                response.raise_for_status()
                payload = response.json()
        except Exception as exc:
            logger.warning("发现模型失败 source=%s url=%s error=%s", source["source"], models_url, exc)
            continue

        data = payload.get("data", []) if isinstance(payload, dict) else []
        source_models: List[Dict[str, str]] = []
        for model in data:
            if isinstance(model, str):
                model_id = model.strip()
                model_name = model_id
            elif isinstance(model, dict):
                model_id = str(model.get("id") or "").strip()
                model_name = str(model.get("name") or model.get("id") or model_id).strip()
            else:
                continue

            if not model_id:
                continue

            config = {
                "id": model_id,
                "name": model_name or model_id,
                "source": source["source"],
                "base_url": source["base_url"],
                "api_key": source["api_key"],
            }
            source_models.append(config)

        source_models = _filter_discovered_models(
            source_models,
            allow_models=_as_string_list(source.get("allow_models")),
            deny_models=_as_string_list(source.get("deny_models")),
            collapse_variants=bool(source.get("collapse_variants", True)),
            ark_endpoint_only=bool(source.get("ark_endpoint_only", False)),
        )

        for config in source_models:
            _DISCOVERED_MODEL_CONFIGS[config["id"]] = config
            discovered.append(config)

    return discovered


async def _get_server_model_config(model_id: Optional[str]) -> Optional[Dict[str, str]]:
    """查找指定模型的服务端连接配置，必要时触发一次自动发现。"""
    from config.settings import settings

    server_model_config = settings.get_llm_model_option(model_id)
    if server_model_config:
        return server_model_config

    if model_id and model_id in _DISCOVERED_MODEL_CONFIGS:
        return _DISCOVERED_MODEL_CONFIGS[model_id]

    if model_id:
        await _discover_models_from_sources()
        return _DISCOVERED_MODEL_CONFIGS.get(model_id)

    return None


async def _get_available_server_models() -> List[Dict[str, str]]:
    """返回客户端可选择的服务端模型，已去重。"""
    from config.settings import settings

    all_models = settings.llm_model_options + await _discover_models_from_sources()
    seen: set[str] = set()
    available: List[Dict[str, str]] = []
    for item in all_models:
        model_id = item["id"]
        if model_id in seen:
            continue
        seen.add(model_id)
        available.append(item)
    return available


def _select_default_model(models: List[Dict[str, str]], configured_default: str) -> str:
    """默认模型必须来自可选列表；否则选第一个可用项，避免返回不可调用的幽灵默认值。"""
    if configured_default and any(item["id"] == configured_default for item in models):
        return configured_default
    if models:
        return models[0]["id"]
    return ""


@router.get("/models", response_model=ModelsResponse)
async def list_models():
    """返回服务端固定可选模型，客户端可在本地追加自定义模型。"""
    from config.settings import settings

    available_models = await _get_available_server_models()
    public_models = [_public_model_info(item) for item in available_models]

    return ModelsResponse(
        default_model=_select_default_model(available_models, settings.llm_model),
        models=public_models,
    )


@router.post("/chat")
@router.post("/chat/stream")
async def chat_endpoint(request: ChatRequest):
    """流式聊天接口（与 `/chat/stream` 等价） - 推荐统一使用流式调用以便客户端实时接收预览与调试信息"""
    try:
        from service import tool_chat_service, llm_service, history_service

        current_conv_id = history_service.ensure_default_conversation(request.user_id)

        if request.conv_id:
            # 客户端传入会话 ID 时只允许切换到该用户已存在的会话。
            convs = history_service.get_conversations(request.user_id)
            conv_ids = [conv["conv_id"] for conv in convs]
            if request.conv_id in conv_ids:
                current_conv_id = request.conv_id

        if not tool_chat_service:
            raise HTTPException(
                status_code=500,
                detail="SQLite 商品搜索聊天服务未初始化，请检查服务器配置"
            )

        server_model_config = await _get_server_model_config(request.model)
        local_model_config = request.llm_config.model_dump() if request.llm_config else None
        # 客户端本地模型配置优先级最高，服务端只负责透传本轮连接信息。
        active_model_config = local_model_config or server_model_config

        if request.model and not active_model_config:
            # 模型 ID 不可用时仍返回 SSE，前端可按同一协议处理错误和结束。
            async def unknown_model_stream():
                """输出模型不存在的单条 SSE 错误事件。"""
                data = {
                    "error": f"模型未配置或未发现：{request.model}。请刷新模型列表并选择可用模型。",
                    "done": True
                }
                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

            return StreamingResponse(
                unknown_model_stream(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                }
            )

        if not request.model and not active_model_config:
            from config.settings import settings

            if settings.available_llm_models:
                async def no_model_stream():
                    """输出模型列表配置存在但没有可用模型的 SSE 错误事件。"""
                    data = {
                        "error": "未找到可用模型。请检查 AVAILABLE_LLM_MODELS、api_key_env 和模型发现过滤配置。",
                        "done": True
                    }
                    yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                return StreamingResponse(
                    no_model_stream(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                    }
                )

        if not llm_service.connected and not (active_model_config and active_model_config.get("api_key")):
            # 无任何可用 API Key 时进入模拟流，便于前端在未配置 LLM 时仍能联调。
            async def mock_stream():
                """输出未配置 LLM 时的模拟 SSE 回复。"""
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
            # 丢弃 system 消息，避免客户端系统提示覆盖后端导购安全/工具约束。
            {"role": msg.role, "content": msg.content}
            for msg in request.messages
            if msg.role != "system"
        ]

        async def generate_stream():
            """把服务层事件转换为前端 SSE JSON，并在结束时保存历史。"""
            full_reply = ""
            full_analysis = ""
            analysis_summary = ""
            timings = None
            history_saved = False
            collected_products = []

            def save_history_once() -> bool:
                """保存本轮用户问题、助手回复、需求分析和商品清单；保证最多执行一次。"""
                nonlocal history_saved
                if history_saved or not full_reply.strip():
                    return history_saved

                try:
                    thinking_to_save = full_analysis.strip() or analysis_summary.strip() or None
                    logger.info(
                        "[ThinkingSave] user=%s | full_analysis_len=%d | summary_len=%d | thinking_to_save=%s | products_count=%d",
                        request.user_id,
                        len(full_analysis),
                        len(analysis_summary),
                        "有" if thinking_to_save else "无",
                        len(collected_products),
                    )
                    history_service.save_message(request.user_id, current_conv_id, "user", request.user_query)
                    history_service.save_message(
                        request.user_id,
                        current_conv_id,
                        "assistant",
                        full_reply,
                        thinking=thinking_to_save,
                        selected_products=collected_products if collected_products else None,
                    )
                    if thinking_to_save:
                        logger.debug("分析完整内容:\n%s", thinking_to_save)
                    history_saved = True
                except Exception as e:
                    logger.error("保存对话历史失败: %s", e)
                    history_saved = False
                return history_saved

            try:
                async for chunk in tool_chat_service.chat_with_tools_stream(
                    user_query=request.user_query,
                    conversation_history=history,
                    model=request.model,
                    model_config=active_model_config,
                ):
                    chunk_type = chunk.get("type")

                    if chunk_type == "status":
                        # status 事件只表示阶段进度，不计入最终回复文本。
                        data = {
                            "status": chunk.get("content", ""),
                            "phase": chunk.get("phase"),
                            "agent": chunk.get("agent"),
                            "conv_id": current_conv_id,
                            "done": False,
                        }
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                    elif chunk_type == "analysis":
                        # analysis 同时用于前端展示“思考/需求分析”和历史中的 thinking 字段。
                        analysis_content = chunk.get("content", "")
                        summary_content = chunk.get("summary", "")
                        if analysis_content:
                            full_analysis += analysis_content
                        if summary_content:
                            analysis_summary = summary_content
                        data = {
                            "analysis": analysis_content,
                            "summary": summary_content,
                            "conv_id": current_conv_id,
                            "done": False,
                        }
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                    elif chunk_type == "selected_products":
                        products = chunk.get("selected_products", [])
                        if products:
                            collected_products.clear()
                            collected_products.extend(products)
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

                    elif chunk_type == "rag_sources":
                        data = {
                            "status": chunk.get("content", ""),
                            "phase": "rag_sources",
                            "agent": "shopping_agent",
                            "rag_sources": chunk.get("rag_sources", []),
                            "conv_id": current_conv_id,
                            "done": False,
                        }
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                    if chunk_type == "content":
                        # content 是真正展示给用户并保存到历史的助手回复。
                        content = chunk.get("content", "")
                        full_reply += content
                        data = {
                            "content": content,
                            "conv_id": current_conv_id,
                            "done": False
                        }
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                    elif chunk_type == "done":
                        # done 到达后先通知前端保存中，再落盘历史，最后发送最终 done。
                        timings = chunk.get("timings")

                        if timings:
                            logger.info(
                                "[Timings] user=%s | 分析=%ss | 向量检索=%ss | RAG检查=%ss | LLM推理=%ss(%s轮) | SQLite工具查询=%ss(%s轮) | 总计=%ss",
                                request.user_id,
                                timings.get('analysis_calls', '-'),
                                timings.get('vector_search', '-'),
                                timings.get('rag_rerank', '-'),
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

                        save_history_once()

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
            finally:
                # 如果生成器异常退出但已经产生回复，尽量补一次历史保存。
                if not history_saved and full_reply.strip():
                    save_history_once()

        return StreamingResponse(
            generate_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            }
        )

    except Exception as e:
        error_msg = str(e)
        logger.error("聊天接口错误: %s", error_msg)
        raise HTTPException(status_code=500, detail=error_msg)


# `/chat/stream` 保留为兼容别名，返回格式与 `/chat` 完全一致。


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
