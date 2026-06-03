"""配置管理模块"""
import os
from pathlib import Path
from pydantic_settings import BaseSettings
from typing import Any, Optional


PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """应用配置类，使用 Pydantic Settings 管理环境变量"""

    # LLM 配置 - 统一使用 LLM_API_KEY 命名，兼容多种模型
    llm_api_key: str = ""  # 空字符串表示未配置，避免明文默认值
    # 火山方舟账号通常需要使用自定义接入点 ID（ep-xxx），不要直接使用无权限的标准模型 ID。
    llm_model: str = "ep-20260514111645-lmgt2"
    llm_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    # 客户端模型选择列表。这里写什么，客户端就展示什么；建议在 backend/.env 中配置真实可用项。
    available_llm_models: list[dict[str, Any] | str] = [
        {
            "id": "ep-20260514111645-lmgt2",
            "name": "豆包导购助手",
            "source": "火山方舟",
            "base_url": "https://ark.cn-beijing.volces.com/api/v3",
            "api_key_env": "ARK_API_KEY",
        },
    ]

    @staticmethod
    def _strip_env_value(value: str) -> str:
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            return value[1:-1]
        return value

    @classmethod
    def _read_dotenv_value(cls, key: str) -> str:
        env_file = cls.Config.env_file
        if not env_file or not os.path.exists(env_file):
            return ""

        try:
            with open(env_file, "r", encoding=cls.Config.env_file_encoding) as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    env_key, env_value = line.split("=", 1)
                    if env_key.strip() == key:
                        return cls._strip_env_value(env_value)
        except OSError:
            return ""
        return ""

    @classmethod
    def _get_env_value(cls, key: str) -> str:
        """读取真实环境变量；不存在时再读取 backend/.env 中同名变量。"""
        if not key:
            return ""
        return os.getenv(key, "") or cls._read_dotenv_value(key)

    def _resolve_api_key(self, item: dict[str, Any]) -> str:
        api_key_env = str(item.get("api_key_env") or "").strip()
        if api_key_env:
            return self._get_env_value(api_key_env).strip()

        # 兼容旧配置，但不推荐继续在模型列表里直接写明文 api_key。
        return str(item.get("api_key") or self.llm_api_key).strip()

    @property
    def llm_model_options(self) -> list[dict[str, str]]:
        """归一化服务端显式模型列表，兼容旧的字符串数组配置。"""
        options: list[dict[str, str]] = []
        seen: set[str] = set()
        has_configured_model_list = bool(self.available_llm_models)

        for item in self.available_llm_models:
            if isinstance(item, str):
                model_id = item.strip()
                option = {
                    "id": model_id,
                    "name": model_id,
                    "source": "服务端",
                    "base_url": self.llm_base_url,
                    "api_key": self.llm_api_key,
                }
            elif isinstance(item, dict):
                model_id = str(item.get("id") or "").strip()
                option = {
                    "id": model_id,
                    "name": str(item.get("name") or model_id).strip(),
                    "source": str(item.get("source") or "服务端").strip(),
                    "base_url": str(item.get("base_url") or self.llm_base_url).strip(),
                    "api_key": self._resolve_api_key(item),
                }
            else:
                continue

            if not model_id or model_id in seen:
                continue
            seen.add(model_id)
            options.append(option)

        # 只有未配置 AVAILABLE_LLM_MODELS 时，才把全局 LLM_MODEL 作为兜底选项。
        # 避免「自动发现来源」配置中没有固定 id 时，错误地把一个不可调用的默认模型混入客户端列表。
        if not has_configured_model_list and self.llm_model and self.llm_model not in seen:
            options.insert(0, {
                "id": self.llm_model,
                "name": self.llm_model,
                "source": "服务端默认",
                "base_url": self.llm_base_url,
                "api_key": self.llm_api_key,
            })

        return options

    @property
    def llm_model_discovery_sources(self) -> list[dict[str, Any]]:
        """需要从 /models 自动发现模型的服务端来源。"""
        sources: list[dict[str, str]] = []
        for item in self.available_llm_models:
            if not isinstance(item, dict):
                continue
            should_discover = bool(item.get("discover_models") or item.get("include_models"))
            if not should_discover:
                continue

            base_url = str(item.get("base_url") or self.llm_base_url).strip()
            api_key = self._resolve_api_key(item)
            if not base_url or not api_key:
                continue

            allow_models = item.get("allow_models") or item.get("include_model_ids") or []
            ark_endpoint_only = item.get("ark_endpoint_only")
            if ark_endpoint_only is None:
                ark_endpoint_only = "volces.com" in base_url and not bool(allow_models)

            sources.append({
                "name": str(item.get("name") or item.get("source") or "服务端模型").strip(),
                "source": str(item.get("source") or item.get("name") or "服务端").strip(),
                "base_url": base_url,
                "api_key": api_key,
                "allow_models": allow_models,
                "deny_models": item.get("deny_models") or item.get("exclude_model_ids") or [],
                "collapse_variants": item.get("collapse_variants", True),
                "ark_endpoint_only": ark_endpoint_only,
            })
        return sources

    def get_llm_model_option(self, model_id: Optional[str]) -> Optional[dict[str, str]]:
        """按模型 ID 查找服务端连接配置。"""
        if not model_id:
            model_id = self.llm_model
        for option in self.llm_model_options:
            if option["id"] == model_id:
                return option
        if not self.available_llm_models and model_id == self.llm_model:
            return {
                "id": self.llm_model,
                "name": self.llm_model,
                "source": "服务端默认",
                "base_url": self.llm_base_url,
                "api_key": self.llm_api_key,
            }
        return None

    # Embedding 配置
    use_doubao_embedding: bool = False
    embedding_model: str = "doubao-embedding-vision"
    embedding_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"

    # Chroma 配置
    chroma_path: str = str(PROJECT_ROOT / "ecommerce_agent_dataset" / ".chroma")
    chroma_collection_name: str = "product_knowledge"

    # SQLite 商品数据库配置
    sqlite_product_db_path: str = str(PROJECT_ROOT / "ecommerce_agent_dataset" / "ecommerce.db")

    # 服务器配置
    server_host: str = "0.0.0.0"
    server_port: int = 8000

    # CORS 配置
    cors_origins: list[str] = ["*"]  # 生产环境应限制为特定域名

    # RAG 配置
    rag_top_k: int = 3  # 检索返回的文档数量
    rag_temperature: float = 0.7  # LLM 生成温度
    rag_vector_search_timeout_seconds: float = 3.0  # 向量检索超时时间，避免 Chroma 异常阻塞聊天
    rag_llm_rerank_enabled: bool = True  # 是否使用 LLM 检查 RAG 片段正确性并重排
    rag_llm_rerank_timeout_seconds: float = 6.0  # LLM 检查 RAG 片段的超时时间
    rag_llm_rerank_max_candidates: int = 2  # 送入 LLM 检查的最多候选片段数量
    rag_llm_rerank_skip_single_candidate: bool = True  # 只有 1 条候选时跳过 LLM 检查，直接使用向量结果
    rag_llm_rerank_max_tokens: int = 260  # 限制检查器输出长度，减少等待时间
    rag_trace_content_chars: int = 800  # RAG 日志中保留的知识片段原文字数
    rag_llm_rerank_min_score: int = 2  # 低于该分数的片段会被过滤；1-5 分
    tool_chat_parallel_enabled: bool = True  # 导购流程是否并行执行需求分析、首轮工具规划和商品直查

    # 日志配置
    log_level: str = "DEBUG"  # 文件日志级别：DEBUG / INFO / WARNING / ERROR
    log_file: Optional[str] = "./backend.log"  # 默认写入后端根目录日志文件
    console_log_level: str = "INFO"  # 控制台日志级别，终端只显示 INFO 及以上

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
