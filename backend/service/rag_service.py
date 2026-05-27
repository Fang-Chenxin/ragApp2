"""RAG 服务层 - 封装检索增强生成逻辑"""
import chromadb
import json
import re
from chromadb.config import Settings
from chromadb.types import Collection
from typing import List, Dict, Optional, Any, AsyncGenerator, TYPE_CHECKING
from openai import AsyncOpenAI
from config.settings import settings
from service.llm_service import llm_service, LLMService

if TYPE_CHECKING:
    pass


class EmbeddingService:
    """Embedding 服务封装类"""

    def __init__(self):
        self.client: Optional[AsyncOpenAI] = None
        self.embedding_function = None
        self.connected = False

    def initialize(self):
        """初始化 Embedding 服务"""
        if settings.use_doubao_embedding:
            if not settings.api_key_configured:
                print("⚠️  LLM API Key 未配置，无法使用豆包 Embedding")
                print("✅ 使用本地免费 all-MiniLM-L6-v2 Embedding 模型")
                return

            from chromadb.utils import embedding_functions

            self.embedding_function = embedding_functions.OpenAIEmbeddingFunction(
                api_key=settings.llm_api_key,
                api_base=settings.embedding_base_url,
                model_name=settings.embedding_model
            )

            masked_key = self._mask_api_key(settings.llm_api_key)
            print(f"✅ 使用豆包 {settings.embedding_model} 作为向量模型")
            print(f"   ├── 基础 URL: {settings.embedding_base_url}")
            print(f"   └── API Key: {masked_key}")

            self.client = AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.embedding_base_url
            )
            self.connected = True
        else:
            print("✅ 使用本地免费 all-MiniLM-L6-v2 Embedding 模型")

    @staticmethod
    def _mask_api_key(api_key: str) -> str:
        """对 API Key 进行脱敏处理"""
        if len(api_key) <= 8:
            return "******"
        return f"{api_key[:4]}******{api_key[-4:]}"

    def get_embedding_function(self):
        """获取 Embedding 函数"""
        return self.embedding_function


class VectorStore:
    """向量数据库封装类"""

    def __init__(self):
        self.client: Optional[chromadb.PersistentClient] = None
        self.collection: Optional[Collection] = None

    def initialize(self):
        """初始化向量数据库"""
        self.client = chromadb.PersistentClient(
            path=settings.chroma_path,
            settings=Settings(anonymized_telemetry=False)
        )

        embedding_func = embedding_service.get_embedding_function()
        if embedding_func:
            self.collection = self.client.get_or_create_collection(
                name=settings.chroma_collection_name,
                embedding_function=embedding_func
            )
        else:
            self.collection = self.client.get_or_create_collection(
                name=settings.chroma_collection_name
            )

        print(f"✅ 向量数据库初始化完成")
        print(f"   ├── 集合名称: {settings.chroma_collection_name}")
        print(f"   └── 存储路径: {settings.chroma_path}")

    def query(self, query_text: str, top_k: Optional[int] = None) -> List[str]:
        """查询相似文档"""
        if not self.collection:
            raise RuntimeError("向量数据库未初始化")

        k = top_k or settings.rag_top_k

        results = self.collection.query(
            query_texts=[query_text],
            n_results=k
        )

        if results['documents'] and results['documents'][0]:
            return results['documents'][0]
        return []

    def add_document(
        self,
        content: str,
        metadata: Optional[Dict[str, Any]] = None,
        doc_id: Optional[str] = None
    ) -> str:
        """添加文档到向量数据库"""
        if not self.collection:
            raise RuntimeError("向量数据库未初始化")

        if not doc_id:
            doc_id = f"doc_{self.collection.count() + 1}"

        self.collection.add(
            documents=[content],
            metadatas=[metadata or {}],
            ids=[doc_id]
        )

        return doc_id

    def get_count(self) -> int:
        """获取集合中的文档数量"""
        if not self.collection:
            return 0
        return self.collection.count()


class RAGService:
    """RAG 核心服务 - 整合检索和生成"""

    def __init__(self, vector_store: VectorStore, llm: LLMService):
        self.vector_store = vector_store
        self.llm = llm
        self.tools = []

    def register_tool(self, tool_spec: dict):
        """注册工具"""
        self.tools.append(tool_spec)

    async def chat_with_rag(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> str:
        """使用 RAG 进行对话"""
        context_docs = self.vector_store.query(user_query)
        context_text = "\n".join([str(doc) for doc in context_docs])

        system_prompt = f"""你是一个智能Agent对话助手。
参考知识库内容：
{context_text}

请基于以上知识库内容和用户进行友好对话，如果知识库中没有相关内容就正常回答用户问题。"""

        messages = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": user_query})

        reply = await self.llm.chat(messages)
        return reply

    async def chat_with_rag_stream(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> AsyncGenerator[str, None]:
        """使用 RAG 进行流式对话"""
        context_docs = self.vector_store.query(user_query)
        context_text = "\n".join([str(doc) for doc in context_docs])

        system_prompt = f"""你是一个智能Agent对话助手。
参考知识库内容：
{context_text}

请基于以上知识库内容和用户进行友好对话，如果知识库中没有相关内容就正常回答用户问题。"""

        messages = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": user_query})

        async for chunk in self.llm.chat_stream(messages):
            yield chunk

    async def chat_with_rag_stream_with_thinking(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> AsyncGenerator[Dict[str, str], None]:
        """使用 RAG 进行流式对话，包含思考过程"""
        context_docs = self.vector_store.query(user_query)
        context_text = "\n".join([str(doc) for doc in context_docs])

        system_prompt = f"""你是一个智能Agent对话助手。
参考知识库内容：
{context_text}

请基于以上知识库内容和用户进行友好对话，如果知识库中没有相关内容就正常回答用户问题。"""

        messages = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": user_query})

        async for chunk in self.llm.chat_stream_with_thinking(messages):
            yield chunk

    async def chat_with_tools(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        max_tool_calls: int = 5
    ) -> str:
        """使用工具调用进行对话"""
        from service.ecommerce_service import ecommerce_service

        context_docs = self.vector_store.query(user_query)
        context_text = "\n".join([str(doc) for doc in context_docs])

        system_prompt = f"""你是一个智能电商助手，可以使用工具查询商品信息。

参考知识库内容：
{context_text}

## 可用工具

| 工具名称 | 描述 |
|---------|------|
| query_products | 查询商品数据库，支持自然语言搜索 |

## 工具调用格式

如果需要调用工具，请使用 JSON 格式输出工具调用，格式如下：
```json
{{"tool_name": "query_products", "arguments": {{"text": "用户的自然语言查询"}}}}
```

## 注意事项

1. 仅当需要查询商品信息时才调用工具
2. 如果不需要查询，可以直接回答用户问题
3. 工具调用结果会自动返回给你，用于生成最终回答

现在请处理用户的问题：
"""

        messages = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": user_query})

        for _ in range(max_tool_calls):
            reply = await self.llm.chat(messages)

            tool_calls = self._parse_tool_calls(reply)

            if not tool_calls:
                return reply

            tool_results = []
            for tool_call in tool_calls:
                tool_name = tool_call.get("tool_name")
                arguments = tool_call.get("arguments", {})

                result = ecommerce_service.run_tool(tool_name, arguments)
                tool_results.append({
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "result": result
                })

            tool_result_text = "\n--- 工具调用结果 ---\n"
            for tr in tool_results:
                result_str = json.dumps(tr["result"], ensure_ascii=False, indent=2)
                tool_result_text += f"工具: {tr['tool_name']}\n参数: {json.dumps(tr['arguments'], ensure_ascii=False)}\n结果: {result_str}\n\n"

            messages.append({"role": "assistant", "content": reply})
            messages.append({"role": "user", "content": tool_result_text})

        final_reply = await self.llm.chat(messages)
        return final_reply

    def _parse_tool_calls(self, text: str) -> List[Dict[str, Any]]:
        """解析 LLM 返回中的工具调用"""
        tool_calls = []

        pattern = r'\{"tool_name":\s*"([^"]+)"[^}]*"arguments":\s*({[^}]*})\}'
        matches = re.findall(pattern, text)

        for match in matches:
            tool_name = match[0]
            try:
                arguments = json.loads(match[1])
                tool_calls.append({
                    "tool_name": tool_name,
                    "arguments": arguments
                })
            except json.JSONDecodeError:
                try:
                    arguments_str = match[1].replace("'", "\"")
                    arguments = json.loads(arguments_str)
                    tool_calls.append({
                        "tool_name": tool_name,
                        "arguments": arguments
                    })
                except:
                    pass

        return tool_calls


# 全局服务实例
embedding_service = EmbeddingService()
vector_store = VectorStore()
rag_service: Optional[RAGService] = None


def initialize_services():
    """初始化所有服务"""
    embedding_service.initialize()
    vector_store.initialize()

    global rag_service
    rag_service = RAGService(vector_store, llm_service)

    print("✅ RAG 服务初始化完成")


def cleanup_services():
    """清理所有服务"""
    print("✅ RAG 服务已关闭")
