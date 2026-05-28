"""配置管理模块"""
import os
from pydantic_settings import BaseSettings
from typing import Optional


class Settings(BaseSettings):
    """应用配置类，使用 Pydantic Settings 管理环境变量"""

    # LLM 配置 - 统一使用 LLM_API_KEY 命名，兼容多种模型
    llm_api_key: str = ""  # 空字符串表示未配置，避免明文默认值
    # 标准模型 ID（doubao-xxx）或自定义接入点 ID（ep-xxx），需要在火山方舟控制台开通
    llm_model: str = "doubao-seed-2-0-lite-260215"
    llm_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"

    # Embedding 配置
    use_doubao_embedding: bool = False
    embedding_model: str = "doubao-embedding-vision"
    embedding_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"

    # Chroma 配置
    chroma_path: str = "./data/chroma"
    chroma_collection_name: str = "agent_knowledge"

    # SQLite 商品数据库配置
    sqlite_product_db_path: str = "../ecommerce_agent_dataset/ecommerce.db"

    # 服务器配置
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # CORS 配置
    cors_origins: list[str] = ["*"]  # 生产环境应限制为特定域名

    # RAG 配置
    rag_top_k: int = 3  # 检索返回的文档数量
    rag_temperature: float = 0.7  # LLM 生成温度

    @property
    def api_key_configured(self) -> bool:
        """检查 API Key 是否已配置"""
        return bool(self.llm_api_key and self.llm_api_key.strip())

    class Config:
        env_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        env_file_encoding = "utf-8"
        extra = "ignore"  # 忽略额外字段


# 全局配置实例
settings = Settings()
