"""RAG 服务层 - 封装检索增强生成逻辑"""
import chromadb
from chromadb.config import Settings
from chromadb.types import Collection
from typing import List, Dict, Optional, Any, TYPE_CHECKING
from openai import AsyncOpenAI
from config.settings import settings
from service.llm_service import llm_service, LLMService

# 仅在类型检查时导入（避免循环导入）
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
            # 检查 API Key 是否配置
            if not settings.api_key_configured:
                print("⚠️  LLM API Key 未配置，无法使用豆包 Embedding，将回退到本地模型")
                print("✅ 使用本地免费 all-MiniLM-L6-v2 Embedding 模型")
                return

            from chromadb.utils import embedding_functions

            self.embedding_function = embedding_functions.OpenAIEmbeddingFunction(
                api_key=settings.llm_api_key,
                api_base=settings.embedding_base_url,
                model_name=settings.embedding_model
            )
            
            # 输出连接信息
            masked_key = self._mask_api_key(settings.llm_api_key)
            print(f"✅ 使用豆包 {settings.embedding_model} 作为向量模型")
            print(f"   ├── 基础 URL: {settings.embedding_base_url}")
            print(f"   └── API Key: {masked_key}")

            # 同时初始化 OpenAI 客户端用于生成
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

        # 获取或创建集合
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
        """查询相似文档

        Args:
            query_text: 查询文本
            top_k: 返回的文档数量

        Returns:
            文档列表
        """
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
        """添加文档到向量数据库

        Args:
            content: 文档内容
            metadata: 文档元数据
            doc_id: 文档 ID，不提供则自动生成

        Returns:
            文档 ID
        """
        if not self.collection:
            raise RuntimeError("向量数据库未初始化")

        # 自动生成文档 ID
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
        """使用 RAG 进行对话

        Args:
            user_query: 用户问题
            conversation_history: 对话历史

        Returns:
            AI 回复
        """
        # 1. 检索相关文档
        context_docs = self.vector_store.query(user_query)
        context_text = "\n".join([str(doc) for doc in context_docs])

        # 2. 构建系统提示词
        system_prompt = f"""你是一个智能Agent对话助手。
参考知识库内容：
{context_text}

请基于以上知识库内容和用户进行友好对话，如果知识库中没有相关内容就正常回答用户问题。"""

        # 3. 构建消息列表
        messages = [{"role": "system", "content": system_prompt}]

        # 添加对话历史
        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})

        # 添加当前用户问题
        messages.append({"role": "user", "content": user_query})

        # 4. 调用 LLM 生成回复
        reply = await self.llm.chat(messages)
        return reply


# 创建全局服务实例
embedding_service = EmbeddingService()
vector_store = VectorStore()
rag_service: Optional[RAGService] = None


def initialize_services():
    """初始化所有服务"""
    print("🔧 开始初始化服务...")
    
    # 初始化 Embedding 服务
    embedding_service.initialize()
    
    # 初始化向量数据库
    vector_store.initialize()
    
    # 初始化 LLM 服务
    llm_service.initialize()
    
    # 创建 RAG 服务实例
    global rag_service
    rag_service = RAGService(vector_store, llm_service)

    print("\n🎉 所有 RAG 服务初始化完成")


def cleanup_services():
    """清理所有服务"""
    llm_service.close()
    print("✅ 所有 RAG 服务已清理")
