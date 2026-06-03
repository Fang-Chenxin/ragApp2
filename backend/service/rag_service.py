"""RAG 服务层 - 封装检索增强生成逻辑"""
import chromadb
from chromadb.config import Settings
from chromadb.types import Collection
from typing import List, Dict, Optional, Any, AsyncGenerator
from openai import AsyncOpenAI
from config.settings import settings
from config.logging_config import get_logger
from service.llm_service import llm_service, LLMService

logger = get_logger("service.rag")


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
                logger.warning("⚠️  LLM API Key 未配置，无法使用豆包 Embedding，回退到本地模型")
                return

            from chromadb.utils import embedding_functions

            self.embedding_function = embedding_functions.OpenAIEmbeddingFunction(
                api_key=settings.llm_api_key,
                api_base=settings.embedding_base_url,
                model_name=settings.embedding_model
            )

            masked_key = self._mask_api_key(settings.llm_api_key)
            logger.info(
                f"✅ 使用豆包 {settings.embedding_model} 作为向量模型\n"
                f"   ├── 基础 URL: {settings.embedding_base_url}\n"
                f"   └── API Key: {masked_key}"
            )

            self.client = AsyncOpenAI(
                api_key=settings.llm_api_key,
                base_url=settings.embedding_base_url
            )
            self.connected = True
        else:
            logger.info("✅ 使用本地免费 all-MiniLM-L6-v2 Embedding 模型")

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

        logger.info(
            "✅ 向量数据库初始化完成\n"
            f"   ├── 集合名称: {settings.chroma_collection_name}\n"
            f"   └── 存储路径: {settings.chroma_path}"
        )

    @staticmethod
    def format_results_as_context(results: List[Dict[str, Any]]) -> str:
        """将带来源的检索结果格式化为可注入 prompt 的上下文。"""
        lines: List[str] = []
        for index, item in enumerate(results, start=1):
            metadata = item.get("metadata") or {}
            source_parts = []
            for key, label in [
                ("product_id", "商品ID"),
                ("title", "商品"),
                ("brand", "品牌"),
                ("category", "分类"),
                ("sub_category", "子分类"),
                ("chunk_type", "片段类型"),
            ]:
                value = metadata.get(key)
                if value:
                    source_parts.append(f"{label}: {value}")

            source_text = " | ".join(source_parts) or f"片段ID: {item.get('id', '')}"
            lines.append(f"[知识片段 {index} | {source_text}]\n{item.get('content', '')}")

        return "\n\n".join(lines)

    def query_with_sources(self, query_text: str, top_k: Optional[int] = None) -> List[Dict[str, Any]]:
        """查询相似文档，并返回文档内容、距离和商品来源 metadata。"""
        if not self.collection:
            raise RuntimeError("向量数据库未初始化")

        k = top_k or settings.rag_top_k

        results = self.collection.query(
            query_texts=[query_text],
            n_results=k,
            include=["documents", "metadatas", "distances"],
        )

        documents = results.get("documents") or []
        if not documents or not documents[0]:
            return []

        ids = (results.get("ids") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        items: List[Dict[str, Any]] = []
        for index, content in enumerate(documents[0]):
            metadata = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
            items.append(
                {
                    "id": ids[index] if index < len(ids) else "",
                    "content": content,
                    "metadata": metadata,
                    "distance": distances[index] if index < len(distances) else None,
                }
            )

        return items

    def query(self, query_text: str, top_k: Optional[int] = None) -> List[str]:
        """查询相似文档，兼容旧调用，仅返回文档文本。"""
        return [item["content"] for item in self.query_with_sources(query_text, top_k)]

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

    async def chat_with_rag(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> str:
        """使用 RAG 进行对话"""
        context_docs = self.vector_store.query_with_sources(user_query)
        context_text = self.vector_store.format_results_as_context(context_docs)

        system_prompt = f"""你是一个智能Agent对话助手。
参考知识库内容：
{context_text}

请基于以上知识库内容和用户进行友好对话，如果知识库中没有相关内容就正常回答用户问题。"""

        messages = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})

        messages.append({"role": "user", "content": user_query})

        try:
            reply = await self.llm.chat(messages)
            return reply
        except Exception as e:
            return f"LLM 调用失败: {type(e).__name__}: {e}"

    async def chat_with_rag_stream(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None
    ) -> AsyncGenerator[str, None]:
        """使用 RAG 进行流式对话"""
        context_docs = self.vector_store.query_with_sources(user_query)
        context_text = self.vector_store.format_results_as_context(context_docs)

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
        context_docs = self.vector_store.query_with_sources(user_query)
        context_text = self.vector_store.format_results_as_context(context_docs)

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


# 全局服务实例
embedding_service = EmbeddingService()
vector_store = VectorStore()
rag_service: Optional[RAGService] = None

