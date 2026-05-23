"""LLM 服务层 - 封装大模型调用"""
from openai import AsyncOpenAI
from typing import Optional, Dict, List, AsyncGenerator
from config.settings import settings
import httpx


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
            print("⚠️  LLM API Key 未配置，将使用模拟回复模式")
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
                )
            )
            
            self.client = AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url=self.base_url,
                http_client=http_client
            )
            self.connected = True
            
            masked_key = self._mask_api_key(settings.llm_api_key)
            print(f"✅ LLM 服务初始化完成")
            print(f"   ├── 模型: {self.model}")
            print(f"   ├── 基础 URL: {self.base_url}")
            print(f"   ├── API Key: {masked_key}")
            print(f"   └── 超时配置: 连接30s, 读取120s, 写入30s")
            
        except Exception as e:
            print(f"❌ LLM 服务初始化失败: {str(e)}")
            raise

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        """对 API Key 进行脱敏处理，只显示前后各4位"""
        if len(api_key) <= 8:
            return "******"
        return f"{api_key[:4]}******{api_key[-4:]}"

    async def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None
    ) -> str:
        """调用大模型生成回复

        Args:
            messages: 消息列表，包含 role 和 content
            temperature: 温度参数，控制随机性

        Returns:
            模型生成的回复内容
        """
        if not self.client:
            raise RuntimeError("LLM 客户端未初始化，请配置 LLM_API_KEY")

        temp = temperature or settings.rag_temperature

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temp
        )

        return response.choices[0].message.content or "抱歉，我现在无法回答您的问题。"

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None
    ) -> AsyncGenerator[str, None]:
        """流式调用大模型生成回复

        Args:
            messages: 消息列表，包含 role 和 content
            temperature: 温度参数，控制随机性

        Yields:
            模型生成的回复内容片段
        """
        if not self.client:
            raise RuntimeError("LLM 客户端未初始化，请配置 LLM_API_KEY")

        temp = temperature or settings.rag_temperature

        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temp,
            stream=True
        )

        async for chunk in stream:
            if chunk.choices[0].delta.content is not None:
                yield chunk.choices[0].delta.content

    async def chat_stream_with_thinking(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None
    ) -> AsyncGenerator[Dict[str, str], None]:
        """流式调用大模型生成回复，包含思考过程

        Args:
            messages: 消息列表，包含 role 和 content
            temperature: 温度参数，控制随机性

        Yields:
            Dict包含 "thinking" 或 "content" 键
        """
        if not self.client:
            raise RuntimeError("LLM 客户端未初始化，请配置 LLM_API_KEY")

        temp = temperature or settings.rag_temperature

        stream = await self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            temperature=temp,
            stream=True
        )

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

    def close(self):
        """关闭 LLM 客户端"""
        if self.client:
            self.client = None
            self.connected = False
            print("✅ LLM 服务已关闭")


# 创建全局 LLM 服务实例
llm_service = LLMService()
