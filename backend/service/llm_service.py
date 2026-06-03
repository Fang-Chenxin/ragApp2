"""LLM 服务层 - 封装大模型调用"""
from openai import AsyncOpenAI
from typing import Any, Optional, Dict, List, AsyncGenerator
from config.settings import settings
from config.logging_config import get_logger
import httpx

logger = get_logger("service.llm")


class LLMService:
    """大模型服务封装类"""

    def __init__(self):
        self.client: Optional[AsyncOpenAI] = None
        self.model = settings.llm_model
        self.base_url = settings.llm_base_url
        self.connected = False

    def initialize(self):
        """初始化 LLM 客户端"""
        if not settings.api_key_configured:
            logger.warning("⚠️  LLM API Key 未配置，将使用模拟回复模式")
            return

        try:
            http_client = httpx.AsyncClient(
                timeout=httpx.Timeout(
                    connect=30.0,
                    read=120.0,
                    write=30.0,
                    pool=30.0
                ),
                limits=httpx.Limits(
                    max_connections=100,
                    max_keepalive_connections=20
                ),
                trust_env=False
            )
            
            self.client = AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url=self.base_url,
                http_client=http_client
            )
            self.connected = True
            
            masked_key = self._mask_api_key(settings.llm_api_key)
            logger.info(
                "✅ LLM 服务初始化完成\n"
                f"   ├── 模型: {self.model}\n"
                f"   ├── 基础 URL: {self.base_url}\n"
                f"   ├── API Key: {masked_key}\n"
                "   └── 超时配置: 连接30s, 读取120s, 写入30s"
            )
            
        except Exception as e:
            logger.error("❌ LLM 服务初始化失败: %s", e)
            raise

    @staticmethod
    def _create_http_client() -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(
                connect=30.0,
                read=120.0,
                write=30.0,
                pool=30.0
            ),
            limits=httpx.Limits(
                max_connections=100,
                max_keepalive_connections=20
            ),
            trust_env=False
        )

    def _resolve_client(
        self,
        model_config: Optional[Dict[str, Any]] = None,
    ) -> tuple[AsyncOpenAI, Optional[httpx.AsyncClient]]:
        """返回本次调用使用的客户端；本地模型配置会创建临时客户端。"""
        api_key = (model_config or {}).get("api_key") or settings.llm_api_key
        base_url = (model_config or {}).get("base_url") or self.base_url
        needs_temp_client = bool(model_config) and (
            base_url != self.base_url or api_key != settings.llm_api_key
        )

        if api_key and needs_temp_client:
            http_client = self._create_http_client()
            return (
                AsyncOpenAI(
                    api_key=api_key,
                    base_url=base_url,
                    http_client=http_client,
                ),
                http_client,
            )

        if not self.client:
            if api_key:
                http_client = self._create_http_client()
                return (
                    AsyncOpenAI(
                        api_key=api_key,
                        base_url=base_url,
                        http_client=http_client,
                    ),
                    http_client,
                )
            raise RuntimeError("LLM 客户端未初始化，请配置 LLM_API_KEY")

        return self.client, None

    @staticmethod
    async def _close_temp_http_client(http_client: Optional[httpx.AsyncClient]):
        if http_client:
            await http_client.aclose()

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        """对 API Key 进行脱敏处理，只显示前后各4位"""
        if len(api_key) <= 8:
            return "******"
        return f"{api_key[:4]}******{api_key[-4:]}"

    @staticmethod
    def _apply_thinking_params(
        kwargs: Dict[str, Any],
        thinking_type: Optional[str] = None,
        reasoning_effort: Optional[str] = None,
    ) -> Dict[str, Any]:
        """为 Chat API 请求补充深度思考参数"""
        extra_body = dict(kwargs.get("extra_body") or {})
        if thinking_type:
            extra_body["thinking"] = {"type": thinking_type}
        if reasoning_effort:
            extra_body["reasoning_effort"] = reasoning_effort
        if extra_body:
            kwargs["extra_body"] = extra_body
        return kwargs

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        model: Optional[str] = None,
        model_config: Optional[Dict[str, Any]] = None,
        max_tokens: Optional[int] = None,
    ) -> str:
        """调用大模型生成回复

        Args:
            messages: 消息列表，包含 role 和 content
            temperature: 温度参数，控制随机性

        Returns:
            模型生成的回复内容
        """
        temp = settings.rag_temperature if temperature is None else temperature
        client, temp_http_client = self._resolve_client(model_config)

        try:
            kwargs: Dict[str, Any] = {
                "model": (model_config or {}).get("id") or model or self.model,
                "messages": messages,
                "temperature": temp,
            }
            if max_tokens:
                kwargs["max_tokens"] = max_tokens
            response = await client.chat.completions.create(**kwargs)
        finally:
            await self._close_temp_http_client(temp_http_client)

        return response.choices[0].message.content or "抱歉，我现在无法回答您的问题。"

    async def chat_with_tools(
        self,
        messages: List[Dict[str, str]],
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: str = "auto",
        temperature: Optional[float] = None,
        thinking_type: Optional[str] = "enabled",
        reasoning_effort: Optional[str] = "medium",
        model: Optional[str] = None,
        model_config: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """调用大模型，支持原生 function calling

        Args:
            messages: 消息列表
            tools: OpenAI 格式的工具定义列表
            tool_choice: 工具选择策略 ("auto", "none", "required")
            temperature: 温度参数

        Returns:
            完整的 ChatCompletion response 对象
        """
        temp = settings.rag_temperature if temperature is None else temperature
        client, temp_http_client = self._resolve_client(model_config)

        kwargs: Dict[str, Any] = {
            "model": (model_config or {}).get("id") or model or self.model,
            "messages": messages,
            "temperature": temp,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice

        self._apply_thinking_params(kwargs, thinking_type, reasoning_effort)

        try:
            return await client.chat.completions.create(**kwargs)
        finally:
            await self._close_temp_http_client(temp_http_client)

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        model: Optional[str] = None,
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[str, None]:
        """流式调用大模型生成回复

        Args:
            messages: 消息列表，包含 role 和 content
            temperature: 温度参数，控制随机性

        Yields:
            模型生成的回复内容片段
        """
        temp = settings.rag_temperature if temperature is None else temperature
        client, temp_http_client = self._resolve_client(model_config)

        try:
            stream = await client.chat.completions.create(
                model=(model_config or {}).get("id") or model or self.model,
                messages=messages,
                temperature=temp,
                stream=True
            )

            async for chunk in stream:
                if chunk.choices[0].delta.content is not None:
                    yield chunk.choices[0].delta.content
        finally:
            await self._close_temp_http_client(temp_http_client)

    async def chat_stream_with_thinking(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        thinking_type: Optional[str] = "enabled",
        reasoning_effort: Optional[str] = "medium",
        model: Optional[str] = None,
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, str], None]:
        """流式调用大模型生成回复，包含思考过程

        Args:
            messages: 消息列表，包含 role 和 content
            temperature: 温度参数，控制随机性

        Yields:
            Dict包含 "thinking" 或 "content" 键
        """
        temp = settings.rag_temperature if temperature is None else temperature
        client, temp_http_client = self._resolve_client(model_config)

        stream_kwargs: Dict[str, Any] = {
            "model": (model_config or {}).get("id") or model or self.model,
            "messages": messages,
            "temperature": temp,
            "stream": True,
        }
        self._apply_thinking_params(stream_kwargs, thinking_type, reasoning_effort)

        try:
            stream = await client.chat.completions.create(**stream_kwargs)

            async for chunk in stream:
                delta = chunk.choices[0].delta
                
                # 兼容多种主流思考过程字段名 (reasoning, reasoning_content, thought 等)
                thinking_content = None
                if hasattr(delta, 'reasoning') and delta.reasoning:
                    thinking_content = delta.reasoning
                elif hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                    thinking_content = delta.reasoning_content
                elif hasattr(delta, 'thought') and delta.thought:
                    thinking_content = delta.thought
                
                # 也尝试从 chunk 的原始字典结构中查找思考字段
                if not thinking_content:
                    chunk_dict = chunk.model_dump(exclude_unset=True) if hasattr(chunk, 'model_dump') else chunk
                    choices = chunk_dict.get('choices', [])
                    if choices and len(choices) > 0:
                        d = choices[0].get('delta', {}) if isinstance(choices[0], dict) else {}
                        for key in ['reasoning', 'reasoning_content', 'thought']:
                            if key in d and d[key]:
                                thinking_content = d[key]
                                break
                
                if thinking_content:
                    yield {"thinking": thinking_content}
                
                # 提取内容
                if delta.content:
                    yield {"content": delta.content}
        finally:
            await self._close_temp_http_client(temp_http_client)

    def close(self):
        """关闭 LLM 客户端"""
        if self.client:
            self.client = None
            self.connected = False
            logger.info("LLM 服务已关闭")


# 创建全局 LLM 服务实例
llm_service = LLMService()
