"""SQLite 商品搜索服务模块 - 封装结构化与规则解析查询能力"""
from __future__ import annotations

import traceback
from pathlib import Path
from typing import Any, Optional, List, Dict

from config.settings import settings

from service.query_engine import (
    DEFAULT_DB_PATH,
    DEFAULT_ONTOLOGY_PATH,
    agent_search_by_rule_parsed_text,
    agent_search_products,
)


class SQLiteProductSearchService:
    """SQLite 商品搜索服务封装类"""

    def __init__(self):
        self.db_path = Path(settings.sqlite_product_db_path).resolve() if getattr(settings, "sqlite_product_db_path", None) else DEFAULT_DB_PATH
        self.ontology_path = DEFAULT_ONTOLOGY_PATH
        self._db_available = False

    def initialize(self):
        """初始化 SQLite 商品搜索服务"""
        try:
            if self.db_path.exists():
                self._db_available = True
                print(f"✅ SQLite 商品搜索服务初始化完成")
                print(f"   └── 数据库路径: {self.db_path}")
            else:
                print(f"⚠️  SQLite 商品数据库未找到: {self.db_path}")
                print(f"   └── 将使用模拟数据模式")
                self._db_available = False
        except Exception as e:
            print(f"❌ SQLite 商品搜索服务初始化失败: {str(e)}")
            self._db_available = False

    @property
    def db_available(self) -> bool:
        """检查数据库是否可用"""
        return self._db_available and self.db_path.exists()

    def search_by_rule_parsed_text(self, text: str, limit: int = 10, show_skus: bool = False) -> dict[str, Any]:
        """规则解析的自然语言查询接口
        
        Args:
            text: 自然语言查询文本（通过 query_engine 的规则和词表解析）
            limit: 返回结果数量限制
            show_skus: 是否显示 SKU 详情
        
        Returns:
            查询结果字典，包含 parsed 字段（解析出的 keyword/brand/category/attr_filters）
        """
        if not self.db_available:
            return self._mock_search_by_rule_parsed_text(text, limit, show_skus)
        
        try:
            return agent_search_by_rule_parsed_text(
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

    def _mock_search_by_rule_parsed_text(self, text: str, limit: int, show_skus: bool) -> dict[str, Any]:
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
            "message": "SQLite 商品数据库未配置，返回模拟结果。请配置 sqlite_product_db_path 环境变量。"
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
            "message": "SQLite 商品数据库未配置，返回模拟结果。请配置 sqlite_product_db_path 环境变量。"
        }

    def close(self):
        """关闭服务"""
        print("✅ SQLite 商品搜索服务已关闭")


# 创建全局 SQLite 商品搜索服务实例
sqlite_product_search_service = SQLiteProductSearchService()