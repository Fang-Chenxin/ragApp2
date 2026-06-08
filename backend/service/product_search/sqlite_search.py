"""SQLite 商品搜索服务模块 - 封装结构化与规则解析查询能力"""
from __future__ import annotations

import traceback
import sqlite3
from pathlib import Path
from typing import Any, Optional, List, Dict

from config.settings import settings
from config.logging_config import get_logger

from .engine import (
    DEFAULT_DB_PATH,
    DEFAULT_ONTOLOGY_PATH,
    agent_search_by_rule_parsed_text,
    agent_search_products,
)

logger = get_logger("service.sqlite_product")


class SQLiteProductSearchService:
    """SQLite 商品搜索服务封装类，给 API 和工具调用提供稳定接口。"""

    def __init__(self):
        """解析数据库和 ontology 路径；真实可用性在 `initialize()` 中检查。"""
        self.db_path = Path(settings.sqlite_product_db_path).resolve() if getattr(settings, "sqlite_product_db_path", None) else DEFAULT_DB_PATH
        self.ontology_path = DEFAULT_ONTOLOGY_PATH
        self._db_available = False
        self._category_tree_cache: Optional[Dict[str, List[str]]] = None

    def initialize(self):
        """初始化 SQLite 商品搜索服务"""
        try:
            if self.db_path.exists():
                self._db_available = True
                self._category_tree_cache = None
                logger.info(
                    "✅ SQLite 商品搜索服务初始化完成\n"
                    f"   └── 数据库路径: {self.db_path}"
                )
            else:
                logger.warning(
                    f"⚠️  SQLite 商品数据库未找到: {self.db_path}\n"
                    "   └── 将使用模拟数据模式"
                )
                self._db_available = False
                self._category_tree_cache = None
        except Exception as e:
            logger.error("❌ SQLite 商品搜索服务初始化失败: %s", e)
            self._db_available = False
            self._category_tree_cache = None

    @property
    def db_available(self) -> bool:
        """检查数据库是否可用"""
        return self._db_available and self.db_path.exists()

    def get_category_tree(self) -> Dict[str, List[str]]:
        """从数据库读取真实 category -> sub_category 枚举。"""
        if self._category_tree_cache is not None:
            return self._category_tree_cache
        if not self.db_available:
            self._category_tree_cache = {}
            return self._category_tree_cache

        conn: Optional[sqlite3.Connection] = None
        tree: Dict[str, List[str]] = {}
        try:
            conn = sqlite3.connect(self.db_path)
            rows = conn.execute(
                "SELECT DISTINCT category, sub_category FROM products ORDER BY category, sub_category"
            ).fetchall()
            for category, sub_category in rows:
                category_text = str(category or "").strip()
                sub_text = str(sub_category or "").strip()
                if not category_text:
                    continue
                tree.setdefault(category_text, [])
                if sub_text and sub_text not in tree[category_text]:
                    tree[category_text].append(sub_text)
        except Exception as e:
            logger.error("读取商品类目枚举失败: %s", e)
            tree = {}
        finally:
            if conn is not None:
                conn.close()

        self._category_tree_cache = tree
        return tree

    def validate_category_filters(
        self,
        category: Optional[str] = None,
        sub_category: Optional[str] = None,
    ) -> List[str]:
        """校验结构化类目过滤值是否属于数据库真实枚举。"""
        tree = self.get_category_tree()
        if not tree:
            return []

        errors: List[str] = []
        category_text = str(category or "").strip()
        sub_text = str(sub_category or "").strip()

        if category_text and category_text not in tree:
            errors.append(f"category='{category_text}' 不在数据库类目范围内")

        if sub_text:
            valid_subs = {sub for subs in tree.values() for sub in subs}
            if sub_text not in valid_subs:
                errors.append(f"sub_category='{sub_text}' 不在数据库子类目范围内")
            elif category_text and category_text in tree and sub_text not in tree[category_text]:
                errors.append(f"sub_category='{sub_text}' 不属于 category='{category_text}'")

        return errors

    def search_by_rule_parsed_text(self, text: str, limit: int = 10, show_skus: bool = False) -> dict[str, Any]:
        """规则解析的自然语言查询接口
        
        Args:
            text: 自然语言查询文本（通过 product_search.engine 的规则和词表解析）
            limit: 返回结果数量限制
            show_skus: 是否显示 SKU 详情
        
        Returns:
            查询结果字典，包含 parsed 字段（解析出的 keyword/brand/category/attr_filters）
        """
        if not self.db_available:
            # 后端可在没有商品库的环境启动，方便联调其他模块。
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
            # 数据库缺失时返回统一结构，调用方不必处理 None。
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

    def get_products_by_ids(self, product_ids: List[str]) -> dict[str, Any]:
        """按 product_id 精确回查商品，作为目标商品白名单的数据库存在性校验。"""
        clean_ids: List[str] = []
        seen: set[str] = set()
        for product_id in product_ids:
            value = str(product_id or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            clean_ids.append(value)

        if not clean_ids:
            return {"ok": True, "error": None, "total": 0, "items": []}

        if not self.db_available:
            return {
                "ok": False,
                "error": "SQLite 商品数据库未配置，无法校验目标商品是否存在。",
                "total": 0,
                "items": [],
            }

        # 使用参数占位符避免把 product_id 拼进 SQL。
        placeholders = ",".join(["?"] * len(clean_ids))
        sql = (
            "SELECT product_id, title, brand, category, sub_category, base_price, image_path, marketing_desc "
            f"FROM products WHERE product_id IN ({placeholders})"
        )
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, clean_ids).fetchall()
        except Exception as e:
            traceback.print_exc()
            return {
                "ok": False,
                "error": f"{type(e).__name__}: {e}",
                "total": 0,
                "items": [],
            }
        finally:
            if conn is not None:
                conn.close()

        # 按输入 ID 顺序返回，保证最终推荐顺序不被 SQLite IN 查询打乱。
        by_id = {str(row["product_id"]): dict(row) for row in rows}
        ordered_items = [by_id[product_id] for product_id in clean_ids if product_id in by_id]
        return {
            "ok": True,
            "error": None,
            "total": len(ordered_items),
            "items": ordered_items,
            "missing_product_ids": [product_id for product_id in clean_ids if product_id not in by_id],
        }

    def get_default_sku_ids_by_product_ids(self, product_ids: List[str]) -> dict[str, str]:
        """按商品基础价匹配默认 SKU；无同价 SKU 时返回该商品首个 SKU。"""
        clean_ids: List[str] = []
        seen: set[str] = set()
        for product_id in product_ids:
            value = str(product_id or "").strip()
            if not value or value in seen:
                continue
            seen.add(value)
            clean_ids.append(value)

        if not clean_ids or not self.db_available:
            return {}

        placeholders = ",".join(["?"] * len(clean_ids))
        sql = (
            "SELECT p.product_id, p.base_price, s.sku_id, s.price "
            "FROM products p "
            "JOIN skus s ON s.product_id = p.product_id "
            f"WHERE p.product_id IN ({placeholders}) "
            "ORDER BY p.product_id, s.sku_id"
        )
        conn: Optional[sqlite3.Connection] = None
        try:
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, clean_ids).fetchall()
        except Exception:
            traceback.print_exc()
            return {}
        finally:
            if conn is not None:
                conn.close()

        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in rows:
            grouped.setdefault(str(row["product_id"]), []).append(dict(row))

        selected: dict[str, str] = {}
        for product_id in clean_ids:
            sku_rows = grouped.get(product_id) or []
            if not sku_rows:
                continue
            matched = None
            for row in sku_rows:
                try:
                    if float(row.get("price")) == float(row.get("base_price")):
                        matched = row
                        break
                except (TypeError, ValueError):
                    continue
            selected[product_id] = str((matched or sku_rows[0]).get("sku_id") or "")
        return selected

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
        """关闭服务；当前没有持久连接，仅输出生命周期日志。"""
        logger.info("SQLite 商品搜索服务已关闭")


# 创建全局 SQLite 商品搜索服务实例
sqlite_product_search_service = SQLiteProductSearchService()
