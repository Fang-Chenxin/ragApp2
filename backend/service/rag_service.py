"""RAG 服务层 - 封装检索增强生成逻辑"""
import chromadb
import httpx
from chromadb.config import Settings
from chromadb.types import Collection
from typing import List, Dict, Optional, Any, AsyncGenerator
from config.settings import settings
from config.logging_config import get_logger
from service.llm_service import llm_service, LLMService

logger = get_logger("service.rag")


class VolcengineMultimodalEmbeddingFunction:
    """Chroma embedding function for Volcengine Ark multimodal embeddings API."""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        dimensions: int = 2048,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self.endpoint = (
            self.base_url
            if self.base_url.endswith("/embeddings/multimodal")
            else f"{self.base_url}/embeddings/multimodal"
        )
        self._client = httpx.Client(
            timeout=httpx.Timeout(connect=30.0, read=120.0, write=30.0, pool=30.0),
            trust_env=False,
        )

    @staticmethod
    def name() -> str:
        """Return a stable Chroma embedding function name."""
        return "volcengine_multimodal"

    def get_config(self) -> Dict[str, Any]:
        """Return non-secret configuration for Chroma collection metadata."""
        return {
            "base_url": self.base_url,
            "model": self.model,
            "dimensions": self.dimensions,
        }

    @staticmethod
    def build_from_config(config: Dict[str, Any]) -> "VolcengineMultimodalEmbeddingFunction":
        """Rebuild the embedding function from Chroma config without persisting secrets."""
        api_key = settings.resolved_embedding_api_key
        if not api_key:
            raise ValueError("外部 Embedding API Key 未配置，无法恢复 Chroma embedding function")
        return VolcengineMultimodalEmbeddingFunction(
            api_key=api_key,
            base_url=str(config.get("base_url") or settings.embedding_base_url),
            model=str(config.get("model") or settings.embedding_model),
            dimensions=int(config.get("dimensions") or settings.embedding_dimensions),
        )

    def is_legacy(self) -> bool:
        """Tell Chroma this embedding function supports the current config protocol."""
        return False

    def default_space(self) -> str:
        """Use cosine distance for semantic embedding search."""
        return "cosine"

    def supported_spaces(self) -> List[str]:
        """Return spaces supported by this embedding function."""
        return ["cosine", "l2", "ip"]

    def __call__(self, input):
        """Return one embedding per input text."""
        embeddings: List[List[float]] = []
        for text in input:
            embeddings.append(self._embed_text(str(text or " ")))
        return embeddings

    def embed_query(self, input):
        """Embed query text for Chroma query paths."""
        return self.__call__(input)

    def _embed_text(self, text: str) -> List[float]:
        payload: Dict[str, Any] = {
            "model": self.model,
            "encoding_format": "float",
            "dimensions": self.dimensions,
            "input": [
                {
                    "type": "text",
                    "text": text,
                }
            ],
        }
        response = self._client.post(
            self.endpoint,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise RuntimeError(
                "火山方舟向量化 API 调用失败："
                f"{response.status_code} {response.text}"
            ) from exc
        body = response.json()
        data = body.get("data")
        if isinstance(data, dict):
            embedding = data.get("embedding")
        elif isinstance(data, list) and data:
            first = data[0]
            embedding = first.get("embedding") if isinstance(first, dict) else None
        else:
            embedding = None

        if not isinstance(embedding, list):
            raise RuntimeError("火山方舟向量化 API 未返回有效 embedding")
        return [float(value) for value in embedding]


class EmbeddingService:
    """Embedding 服务封装类。

    Chroma 可以使用默认本地 embedding，也可以在配置开启后接入 OpenAI-compatible
    外部 embedding 服务。向量模型由后端固定配置，不向客户端提供切换选项。
    """

    def __init__(self):
        """初始化 embedding 客户端状态。"""
        self.client: Optional[VolcengineMultimodalEmbeddingFunction] = None
        self.embedding_function = None
        self.connected = False
        self.provider = "local"
        self.model = "all-MiniLM-L6-v2"
        self.base_url = ""
        self.error = ""

    def initialize(self):
        """初始化 Embedding 服务"""
        self.client = None
        self.embedding_function = None
        self.connected = False
        self.provider = "local"
        self.model = "all-MiniLM-L6-v2"
        self.base_url = ""
        self.error = ""

        if settings.external_embedding_enabled:
            api_key = settings.resolved_embedding_api_key
            if not api_key:
                self.error = "外部 Embedding API Key 未配置，已回退到本地模型"
                logger.warning("⚠️  %s", self.error)
                return

            self.embedding_function = VolcengineMultimodalEmbeddingFunction(
                api_key=api_key,
                base_url=settings.embedding_base_url,
                model=settings.embedding_model,
                dimensions=settings.embedding_dimensions,
            )
            self.client = self.embedding_function
            self.connected = True
            self.provider = "external"
            self.model = settings.embedding_model
            self.base_url = settings.embedding_base_url

            masked_key = self._mask_api_key(api_key)
            logger.info(
                f"✅ 使用外部 {settings.embedding_model} 作为向量模型\n"
                f"   ├── 基础 URL: {settings.embedding_base_url}\n"
                f"   ├── API 路径: /embeddings/multimodal\n"
                f"   ├── 向量维度: {settings.embedding_dimensions}\n"
                f"   └── API Key: {masked_key}"
            )
        else:
            # embedding_function 为 None 时，Chroma 使用自身默认 embedding 函数。
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

    def get_status(self) -> Dict[str, Any]:
        """返回向量化服务连接状态，供健康接口和客户端展示。"""
        return {
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "api_path": "/embeddings/multimodal" if settings.external_embedding_enabled else "",
            "dimensions": settings.embedding_dimensions if settings.external_embedding_enabled else None,
            "external_enabled": settings.external_embedding_enabled,
            "connected": self.connected if settings.external_embedding_enabled else True,
            "status": "connected" if self.connected else ("local" if not settings.external_embedding_enabled else "fallback"),
            "message": self.error or (
                "外部向量化服务已连接" if self.connected else "使用本地向量化模型"
            ),
        }


class VectorStore:
    """Chroma 向量数据库封装类，负责知识片段写入和相似查询。"""

    def __init__(self):
        """初始化 Chroma 客户端和 collection 占位。"""
        self.client: Optional[chromadb.PersistentClient] = None
        self.collection: Optional[Collection] = None

    def initialize(self):
        """初始化向量数据库"""
        if settings.external_embedding_enabled and not embedding_service.get_embedding_function():
            embedding_service.initialize()

        self.client = chromadb.PersistentClient(
            path=settings.chroma_path,
            settings=Settings(anonymized_telemetry=False)
        )

        if settings.external_embedding_enabled and not embedding_service.get_embedding_function():
            embedding_service.initialize()

        embedding_func = embedding_service.get_embedding_function()
        # 有远程 embedding 配置时显式传给 collection；否则沿用 Chroma 默认 embedding。
        try:
            if embedding_func:
                self.collection = self.client.get_or_create_collection(
                    name=settings.chroma_collection_name,
                    embedding_function=embedding_func
                )
            else:
                self.collection = self.client.get_or_create_collection(
                    name=settings.chroma_collection_name
                )
        except ValueError as exc:
            if embedding_func and "Embedding function conflict" in str(exc):
                raise RuntimeError(
                    "Chroma 集合使用的 embedding function 与当前外部向量模型不一致。"
                    f"请使用当前 EMBEDDING_MODEL 重新构建索引：{settings.chroma_path}"
                ) from exc
            raise

        logger.info(
            "✅ 向量数据库初始化完成\n"
            f"   ├── 集合名称: {settings.chroma_collection_name}\n"
            f"   └── 存储路径: {settings.chroma_path}"
        )

    @staticmethod
    def infer_query_category(query_text: str) -> Optional[str]:
        """从用户问题中的显式购物词推断 RAG 品类过滤范围。"""
        query = (query_text or "").lower()
        category_keywords = [
            (
                "数码电子",
                [
                    "数码", "电子", "手机", "平板", "电脑", "笔记本", "耳机", "ipad", "macbook",
                    "屏幕", "芯片", "续航", "高刷", "降噪", "键盘", "鼠标",
                ],
            ),
            (
                "服饰运动",
                [
                    "服饰", "运动", "裤子", "鞋", "衣服", "衣物", "服装", "穿搭", "上衣", "t恤",
                    "短袖", "长袖", "卫衣", "外套", "瑜伽裤", "徒步鞋", "跑步", "训练", "健身",
                    "力量训练", "速干", "透气", "帽", "背包", "腰包",
                ],
            ),
            (
                "美妆护肤",
                [
                    "美妆", "护肤", "彩妆", "精华", "面霜", "眼霜", "洁面", "乳液", "眉笔",
                    "粉底", "口红", "防晒", "散粉", "面膜", "控油", "保湿", "修护",
                ],
            ),
            (
                "食品饮料",
                [
                    "食品", "饮料", "泡面", "茶饮", "气泡水", "方便面", "生活", "零食", "吃的",
                    "喝的", "咖啡", "牛奶", "饼干", "薯片", "果汁",
                ],
            ),
        ]

        scores: Dict[str, int] = {}
        for category, keywords in category_keywords:
            score = sum(1 for keyword in keywords if keyword.lower() in query)
            if score:
                scores[category] = score

        if not scores:
            return None
        return max(scores.items(), key=lambda item: item[1])[0]

    @staticmethod
    def format_results_as_context(results: List[Dict[str, Any]]) -> str:
        """将带来源的检索结果格式化为可注入 prompt 的上下文。"""
        lines: List[str] = []
        for index, item in enumerate(results, start=1):
            metadata = item.get("metadata") or {}
            source_parts = []
            for key, label in [
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

    def query_with_sources(
        self,
        query_text: str,
        top_k: Optional[int] = None,
        category: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """查询相似文档，并返回文档内容、距离和商品来源 metadata。"""
        if not self.collection:
            raise RuntimeError("向量数据库未初始化")

        k = top_k or settings.rag_top_k
        inferred_category = category or self.infer_query_category(query_text)
        where_filter = {"category": inferred_category} if inferred_category else None

        # 先按推断品类过滤，减少“鞋子问题命中手机知识片段”这类跨品类噪声。
        query_kwargs: Dict[str, Any] = {
            "query_texts": [query_text],
            "n_results": k,
            "include": ["documents", "metadatas", "distances"],
        }
        if where_filter:
            query_kwargs["where"] = where_filter

        results = self.collection.query(**query_kwargs)

        documents = results.get("documents") or []
        if (not documents or not documents[0]) and where_filter:
            # 品类过滤没有命中时回退到全库检索，避免过度过滤导致完全无上下文。
            logger.info(
                "[RAGVectorSearch] query=%s | category=%s | filtered_count=0 | fallback=unfiltered",
                query_text,
                inferred_category,
            )
            query_kwargs.pop("where", None)
            results = self.collection.query(**query_kwargs)
            documents = results.get("documents") or []
        if not documents or not documents[0]:
            logger.info(
                "[RAGVectorSearch] query=%s | category=%s | count=0",
                query_text,
                inferred_category or "",
            )
            return []

        ids = (results.get("ids") or [[]])[0]
        metadatas = (results.get("metadatas") or [[]])[0]
        distances = (results.get("distances") or [[]])[0]

        items: List[Dict[str, Any]] = []
        for index, content in enumerate(documents[0]):
            metadata = metadatas[index] if index < len(metadatas) and metadatas[index] else {}
            item = {
                "id": ids[index] if index < len(ids) else "",
                "content": content,
                "metadata": metadata,
                "distance": distances[index] if index < len(distances) else None,
            }
            items.append(item)
            # DEBUG 日志记录向量距离和片段预览，便于判断 RAG 误命中来自检索还是 rerank。
            content_preview = str(content or "")[: max(80, int(settings.rag_trace_content_chars or 800))]
            content_preview = content_preview.replace("\n", " ")
            logger.info(
                "[RAGVectorScore] query=%s | category_filter=%s | rank=%s | id=%s | product_id=%s | title=%s | category=%s/%s | distance=%s | content=%s",
                query_text,
                inferred_category or "",
                index + 1,
                item["id"],
                metadata.get("product_id") or "",
                metadata.get("title") or "",
                metadata.get("category") or "",
                metadata.get("sub_category") or "",
                item["distance"],
                content_preview,
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
            # 简单顺序 ID 适合手动添加知识；批量导入时可传稳定 doc_id 防重复。
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
    """通用 RAG 核心服务，保留给非导购型问答接口使用。"""

    def __init__(self, vector_store: VectorStore, llm: LLMService):
        """注入向量库和 LLM 服务。"""
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

        # 先把知识库片段放入 system，再追加历史和当前问题，保持“知识优先但可正常回答”的语义。
        system_prompt = f"""你是一个智能Agent对话助手。
参考知识库内容：
{context_text}

请基于以上知识库内容和用户进行友好对话，如果知识库中没有相关内容就正常回答用户问题。"""

        messages = [{"role": "system", "content": system_prompt}]

        if conversation_history:
            # 历史消息按原 role 追加，避免把 assistant 回复误当作系统约束。
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
