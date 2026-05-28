"""电商查询服务模块 - 封装商品数据库查询功能"""
from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, Optional, List, Dict

from config.settings import settings

from service.query_engine import (
    DEFAULT_DB_PATH,
    DEFAULT_ONTOLOGY_PATH,
    agent_search_from_text,
    agent_search_products,
)


class EcommerceService:
    """电商查询服务封装类"""

    def __init__(self):
        self.db_path = Path(settings.ecommerce_db_path).resolve() if hasattr(settings, 'ecommerce_db_path') else DEFAULT_DB_PATH
        self.ontology_path = DEFAULT_ONTOLOGY_PATH
        self._db_available = False

    def initialize(self):
        """初始化电商服务"""
        try:
            if self.db_path.exists():
                self._db_available = True
                print(f"✅ 电商数据库服务初始化完成")
                print(f"   └── 数据库路径: {self.db_path}")
            else:
                print(f"⚠️  电商数据库未找到: {self.db_path}")
                print(f"   └── 将使用模拟数据模式")
                self._db_available = False
        except Exception as e:
            print(f"❌ 电商服务初始化失败: {str(e)}")
            self._db_available = False

    @property
    def db_available(self) -> bool:
        """检查数据库是否可用"""
        return self._db_available and self.db_path.exists()

    def search_from_text(self, text: str, limit: int = 10, show_skus: bool = False) -> dict[str, Any]:
        """自然语言查询接口
        
        Args:
            text: 自然语言查询文本
            limit: 返回结果数量限制
            show_skus: 是否显示 SKU 详情
        
        Returns:
            查询结果字典
        """
        if not self.db_available:
            return self._mock_search_from_text(text, limit, show_skus)
        
        try:
            return agent_search_from_text(
                text=text,
                db_path=self.db_path,
                ontology_path=self.ontology_path,
                limit=limit,
                show_skus=show_skus
            )
        except Exception as e:
            traceback.print_exc()
            return {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "total": 0,
                "items": []
            }

    def search_products(
        self,
        keyword: Optional[str] = None,
        brand: Optional[str] = None,
        category: Optional[str] = None,
        sub_category: Optional[str] = None,
        attr_filters: Optional[List[Dict[str, str]]] = None,
        limit: int = 10,
        show_skus: bool = False
    ) -> dict[str, Any]:
        """结构化查询接口
        
        Args:
            keyword: 关键词
            brand: 品牌
            category: 分类
            sub_category: 子分类
            attr_filters: 属性过滤器列表
            limit: 返回结果数量限制
            show_skus: 是否显示 SKU 详情
        
        Returns:
            查询结果字典
        """
        if not self.db_available:
            return self._mock_search_products(
                keyword, brand, category, sub_category, attr_filters, limit, show_skus
            )
        
        try:
            return agent_search_products(
                db_path=self.db_path,
                ontology_path=self.ontology_path,
                keyword=keyword,
                brand=brand,
                category=category,
                sub_category=sub_category,
                attr_filters=attr_filters,
                limit=limit,
                show_skus=show_skus
            )
        except Exception as e:
            traceback.print_exc()
            return {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "total": 0,
                "items": []
            }

    def get_tool_spec(self) -> dict[str, Any]:
        """获取工具调用规范
        
        Returns:
            OpenAI 工具调用格式的规范字典
        """
        return {
            "type": "function",
            "function": {
                "name": "query_products",
                "description": "查询本地电商 SQLite 数据库，可以使用自然语言或结构化过滤器。",
                "parameters": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "text": {"type": "string", "description": "自然语言查询文本。"},
                        "keyword": {"type": ["string", "null"], "description": "关键词搜索"},
                        "brand": {"type": ["string", "null"], "description": "品牌过滤"},
                        "category": {"type": ["string", "null"], "description": "产品分类"},
                        "sub_category": {"type": ["string", "null"], "description": "子分类"},
                        "attr_filters": {
                            "type": "array",
                            "items": {
                                "oneOf": [
                                    {
                                        "type": "object",
                                        "required": ["key", "value"],
                                        "properties": {
                                            "key": {"type": "string"},
                                            "value": {"type": "string"},
                                        },
                                        "additionalProperties": False,
                                    },
                                    {
                                        "type": "array",
                                        "minItems": 2,
                                        "maxItems": 2,
                                        "items": {"type": "string"},
                                    },
                                ]
                            },
                            "description": "属性过滤器列表，格式为 [{key, value}] 或 [[key, value]]"
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 10},
                        "show_skus": {"type": "boolean", "default": False},
                    },
                },
            },
        }

    def run_tool(self, tool_name: str, arguments: Optional[Dict[str, Any]] = None) -> dict[str, Any]:
        """执行工具调用
        
        Args:
            tool_name: 工具名称
            arguments: 工具参数
        
        Returns:
            工具执行结果
        """
        if tool_name != "query_products":
            return {
                "ok": False,
                "error": f"未知工具: {tool_name}",
                "query_sql": "",
                "query_params": [],
                "resolved_filters": {},
                "total": 0,
                "items": [],
            }

        args = arguments or {}
        text = args.get("text")
        limit = int(args.get("limit", 10))
        show_skus = bool(args.get("show_skus", False))

        if text:
            return self.search_from_text(text=str(text), limit=limit, show_skus=show_skus)

        attr_filters = self._normalize_attr_filters(args.get("attr_filters"))
        if not any([args.get("keyword"), args.get("brand"), args.get("category"), args.get("sub_category"), attr_filters]):
            return {
                "ok": False,
                "error": "参数为空！你必须提供 text（自然语言查询）或至少一个过滤器(keyword/brand/category/sub_category/attr_filters)。请从用户问题和对话历史中提取商品关键词后重新调用。",
                "query_sql": "",
                "query_params": [],
                "resolved_filters": {},
                "total": 0,
                "items": [],
            }

        return self.search_products(
            keyword=args.get("keyword"),
            brand=args.get("brand"),
            category=args.get("category"),
            sub_category=args.get("sub_category"),
            attr_filters=attr_filters,
            limit=limit,
            show_skus=show_skus,
        )

    @staticmethod
    def _normalize_attr_filters(raw_filters: Any) -> List[Dict[str, str]]:
        """标准化属性过滤器格式"""
        if not raw_filters:
            return []
        resolved: List[Dict[str, str]] = []
        for item in raw_filters:
            if isinstance(item, dict):
                key = str(item.get("key", "")).strip()
                value = str(item.get("value", "")).strip()
            elif isinstance(item, (list, tuple)) and len(item) == 2:
                key = str(item[0]).strip()
                value = str(item[1]).strip()
            else:
                continue
            if key and value:
                resolved.append({"key": key, "value": value})
        return resolved

    def _mock_search_from_text(self, text: str, limit: int, show_skus: bool) -> dict[str, Any]:
        """模拟自然语言查询结果（当数据库不可用时）"""
        return {
            "ok": True,
            "error": None,
            "input_text": text,
            "parsed": {
                "keyword": text,
                "brand": None,
                "category": None,
                "sub_category": None,
                "attr_filters": []
            },
            "query_sql": "",
            "query_params": [],
            "resolved_filters": {
                "keyword": text,
                "limit": limit,
                "show_skus": show_skus
            },
            "total": 0,
            "items": [],
            "message": "电商数据库未配置，返回模拟结果。请配置 ecommerce_db_path 环境变量。"
        }

    def _mock_search_products(
        self,
        keyword: Optional[str],
        brand: Optional[str],
        category: Optional[str],
        sub_category: Optional[str],
        attr_filters: Optional[List[Dict[str, str]]],
        limit: int,
        show_skus: bool
    ) -> dict[str, Any]:
        """模拟结构化查询结果（当数据库不可用时）"""
        return {
            "ok": True,
            "error": None,
            "query_sql": "",
            "query_params": [],
            "resolved_filters": {
                "keyword": keyword,
                "brand": brand,
                "category": category,
                "sub_category": sub_category,
                "attr_filters": attr_filters or [],
                "limit": limit,
                "show_skus": show_skus
            },
            "total": 0,
            "items": [],
            "message": "电商数据库未配置，返回模拟结果。请配置 ecommerce_db_path 环境变量。"
        }

    def close(self):
        """关闭服务"""
        print("✅ 电商服务已关闭")


# 创建全局电商服务实例
ecommerce_service = EcommerceService()