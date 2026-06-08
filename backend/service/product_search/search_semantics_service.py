"""语义表加载与查询服务 - 管理 search_semantics 目录下的业务知识表。"""
from __future__ import annotations

import json
import functools
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from config.logging_config import get_logger

logger = get_logger("service.search_semantics")

_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "ecommerce_agent_dataset" / "search_semantics"


def _load_json(filename: str) -> Dict[str, Any]:
    """加载单个 JSON 文件，失败时返回空字典。"""
    path = _DATA_DIR / filename
    if not path.exists():
        logger.warning("语义表文件不存在: %s", path)
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("加载语义表失败 %s: %s", path, e)
        return {}


def _load_json_list(filename: str) -> List[Any]:
    """加载 JSON 数组文件，失败时返回空列表。"""
    path = _DATA_DIR / filename
    if not path.exists():
        logger.warning("语义表文件不存在: %s", path)
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("加载语义表失败 %s: %s", path, e)
        return []


class SearchSemanticsService:
    """语义表查询服务，提供商品概念、品类别名、fallback 关系等查询能力。"""

    def __init__(self) -> None:
        self.reload()

    def reload(self) -> None:
        """重新加载所有语义表。"""
        self._product_concepts: Dict[str, Any] = _load_json("product_concepts.json")
        self._category_aliases: Dict[str, Any] = _load_json("category_aliases.json")
        self._fallback_relations: Dict[str, Any] = _load_json("fallback_relations.json")
        self._brand_model_aliases: Dict[str, Any] = _load_json("brand_model_aliases.json")
        self._scenario_tags: Dict[str, Any] = _load_json("scenario_tags.json")
        self._regression_cases: List[Any] = _load_json_list("regression_cases.json")
        logger.info(
            "语义表加载完成: concepts=%d, categories=%d, fallback=%d, brands=%d, scenarios=%d, regression=%d",
            len(self._product_concepts),
            len(self._category_aliases),
            len(self._fallback_relations),
            len(self._brand_model_aliases),
            len(self._scenario_tags),
            len(self._regression_cases),
        )

    # ── 商品概念查询 ──

    def match_product_concepts(self, query: str) -> List[Dict[str, Any]]:
        """从用户 query 中匹配命中的商品概念，返回匹配列表。"""
        query_lower = query.lower()
        matches = []
        for concept_name, concept in self._product_concepts.items():
            all_terms = concept.get("direct_terms", []) + concept.get("fallback_terms", [])
            if any(term.lower() in query_lower for term in all_terms) or concept_name.lower() in query_lower:
                matches.append({"concept": concept_name, **concept})
        return matches

    def get_concept_direct_terms(self, concept_name: str) -> List[str]:
        """获取某个概念的 direct_terms。"""
        concept = self._product_concepts.get(concept_name, {})
        return concept.get("direct_terms", [])

    def get_concept_fallback_terms(self, concept_name: str) -> List[str]:
        """获取某个概念的 fallback_terms。"""
        concept = self._product_concepts.get(concept_name, {})
        return concept.get("fallback_terms", [])

    # ── 品类别名查询 ──

    def match_category_aliases(self, query: str) -> List[Dict[str, Any]]:
        """从用户 query 中匹配品类别名，返回对应的 category/sub_category。"""
        query_lower = query.lower()
        matches = []
        for alias_name, alias_info in self._category_aliases.items():
            all_terms = [alias_name] + alias_info.get("aliases", [])
            if any(term.lower() in query_lower for term in all_terms):
                matches.append({"alias": alias_name, **alias_info})
        return matches

    def resolve_category(self, term: str) -> Optional[Dict[str, str]]:
        """将用户词解析到库内真实 category/sub_category。"""
        term_lower = term.lower()
        for alias_name, alias_info in self._category_aliases.items():
            all_terms = [alias_name] + alias_info.get("aliases", [])
            if any(t.lower() == term_lower for t in all_terms):
                return {
                    "category": alias_info.get("category", ""),
                    "sub_category": alias_info.get("sub_category", ""),
                }
        return None

    # ── Fallback 关系查询 ──

    def get_fallback_relation(self, query: str) -> Optional[Dict[str, Any]]:
        """获取与 query 匹配的 fallback 关系定义。"""
        query_lower = query.lower()
        for term, relation in self._fallback_relations.items():
            if term.lower() in query_lower:
                return {"trigger": term, **relation}
        return None

    def get_forbidden_categories(self, query: str) -> List[str]:
        """获取 query 对应的禁止类目列表。"""
        relation = self.get_fallback_relation(query)
        if relation:
            return relation.get("forbidden_categories", [])
        return []

    # ── 品牌/型号查询 ──

    def match_brand_models(self, query: str) -> List[Dict[str, Any]]:
        """从用户 query 中匹配品牌/型号约束。"""
        query_lower = query.lower()
        matches = []
        for model_name, model_info in self._brand_model_aliases.items():
            all_terms = model_info.get("direct_terms", []) + model_info.get("fallback_terms", [])
            if any(term.lower() in query_lower for term in all_terms) or model_name.lower() in query_lower:
                matches.append({"model": model_name, **model_info})
        return matches

    def is_strict_direct(self, query: str) -> bool:
        """检查 query 是否命中 strict_direct 品牌/型号。"""
        for model_name, model_info in self._brand_model_aliases.items():
            if not model_info.get("strict_direct", False):
                continue
            direct_terms = [t.lower() for t in model_info.get("direct_terms", [])]
            if any(t in query.lower() for t in direct_terms) or model_name.lower() in query.lower():
                return True
        return False

    def get_strict_direct_terms(self, query: str) -> List[str]:
        """获取 query 命中 strict_direct 品牌时的 direct_terms。"""
        query_lower = query.lower()
        for model_name, model_info in self._brand_model_aliases.items():
            if not model_info.get("strict_direct", False):
                continue
            direct_terms = model_info.get("direct_terms", [])
            if any(t.lower() in query_lower for t in direct_terms) or model_name.lower() in query_lower:
                return direct_terms
        return []

    # ── 场景标签查询 ──

    def match_scenario_tags(self, query: str) -> List[Dict[str, Any]]:
        """从用户 query 中匹配场景标签。"""
        query_lower = query.lower()
        matches = []
        for tag_name, tag_info in self._scenario_tags.items():
            query_terms = tag_info.get("query_terms", [])
            if any(term in query_lower for term in query_terms):
                matches.append({"scenario": tag_name, **tag_info})
        return matches

    # ── SearchPlan 增强 ──

    def build_search_plan_hints(self, user_query: str) -> str:
        """根据用户 query 构建注入 SearchPlan prompt 的业务知识提示文本。"""
        hints: List[str] = []

        # 1. 商品概念匹配
        concepts = self.match_product_concepts(user_query)
        if concepts:
            hint_lines = ["### 商品概念知识"]
            for c in concepts:
                hint_lines.append(
                    f"- 「{c['concept']}」: direct_terms={c['direct_terms']}, "
                    f"fallback_terms={c['fallback_terms']}, category={c.get('category', '')}"
                )
            hints.append("\n".join(hint_lines))

        # 2. 品类别名
        categories = self.match_category_aliases(user_query)
        if categories:
            hint_lines = ["### 品类真实值（必须使用）"]
            for c in categories:
                hint_lines.append(
                    f"- 「{c['alias']}」→ category=\"{c['category']}\", sub_category=\"{c['sub_category']}\""
                )
            hints.append("\n".join(hint_lines))

        # 3. Fallback 关系
        fallback = self.get_fallback_relation(user_query)
        if fallback:
            acceptable = fallback.get("acceptable", [])
            forbidden = fallback.get("forbidden_categories", [])
            hint_lines = [f"### Fallback 规则（{fallback['trigger']}）"]
            if acceptable:
                terms_str = "; ".join(
                    f"{a['category']}/{a['sub_category']}→{a['terms']}({a.get('role', '')})"
                    for a in acceptable
                )
                hint_lines.append(f"- acceptable: {terms_str}")
            if forbidden:
                hint_lines.append(f"- forbidden_categories: {forbidden}")
            hints.append("\n".join(hint_lines))

        # 4. 品牌型号
        brands = self.match_brand_models(user_query)
        if brands:
            hint_lines = ["### 品牌/型号约束"]
            for b in brands:
                strict = " [strict_direct]" if b.get("strict_direct") else ""
                hint_lines.append(
                    f"- 「{b['model']}」: brand=\"{b['brand']}\", "
                    f"direct_terms={b['direct_terms']}, fallback_terms={b['fallback_terms']}{strict}"
                )
            hints.append("\n".join(hint_lines))

        # 5. 场景标签
        scenarios = self.match_scenario_tags(user_query)
        if scenarios:
            hint_lines = ["### 场景标签"]
            for s in scenarios:
                hint_lines.append(
                    f"- 「{s['scenario']}」: preferred_sub_categories={s['preferred_sub_categories']}, "
                    f"ranking_hints={s['ranking_hints']}"
                )
            hints.append("\n".join(hint_lines))

        if not hints:
            return ""

        return "\n\n".join(hints)

    # ── 候选过滤增强 ──

    def get_effective_forbidden_categories(self, user_query: str, search_plan: Optional[Dict[str, Any]] = None) -> Set[str]:
        """合并 SearchPlan 和语义表中的禁止类目。"""
        forbidden: Set[str] = set()
        if search_plan:
            for cat in search_plan.get("forbidden_categories", []) or []:
                if cat:
                    forbidden.add(cat)
        # 从 fallback_relations 补充
        for cat in self.get_forbidden_categories(user_query):
            forbidden.add(cat)
        return forbidden

    def get_effective_allowed_categories(self, user_query: str, search_plan: Optional[Dict[str, Any]] = None) -> Set[str]:
        """合并 SearchPlan 和语义表中的允许类目。"""
        allowed: Set[str] = set()
        if search_plan:
            for cat in search_plan.get("allowed_categories", []) or []:
                if cat:
                    allowed.add(cat)
        # 从品类别名补充
        for c in self.match_category_aliases(user_query):
            cat = c.get("category", "")
            if cat:
                allowed.add(cat)
        # 从商品概念补充
        for c in self.match_product_concepts(user_query):
            cat = c.get("category", "")
            if cat:
                allowed.add(cat)
        return allowed

    def get_effective_direct_terms(self, user_query: str, search_plan: Optional[Dict[str, Any]] = None) -> List[str]:
        """合并 SearchPlan 和语义表中的 direct_terms。"""
        terms: List[str] = []
        if search_plan:
            for t in search_plan.get("direct_terms", []) or []:
                t = str(t).strip()
                if t:
                    terms.append(t)
        # 从品牌型号表补充（strict_direct 优先）
        strict_terms = self.get_strict_direct_terms(user_query)
        if strict_terms:
            return strict_terms  # strict 模式下覆盖
        # 从商品概念补充
        for c in self.match_product_concepts(user_query):
            terms.extend(c.get("direct_terms", []))
        return list(dict.fromkeys(terms))  # 去重保序

    def get_effective_fallback_terms(self, user_query: str, search_plan: Optional[Dict[str, Any]] = None) -> List[str]:
        """合并 SearchPlan 和语义表中的 acceptable_fallback_terms。"""
        terms: List[str] = []
        if search_plan:
            for t in search_plan.get("acceptable_fallback_terms", []) or []:
                t = str(t).strip()
                if t:
                    terms.append(t)
        # 从 fallback_relations 补充
        relation = self.get_fallback_relation(user_query)
        if relation:
            for acceptable in relation.get("acceptable", []):
                terms.extend(acceptable.get("terms", []))
        # 从商品概念补充
        for c in self.match_product_concepts(user_query):
            terms.extend(c.get("fallback_terms", []))
        return list(dict.fromkeys(terms))  # 去重保序

    # ── 回归测试数据 ──

    def get_regression_cases(self) -> List[Dict[str, Any]]:
        """获取所有回归测试用例。"""
        return self._regression_cases

    def get_all_sub_categories(self) -> Dict[str, List[str]]:
        """获取所有品类到子品类的映射（从 category_aliases 提取）。"""
        result: Dict[str, List[str]] = {}
        for alias_info in self._category_aliases.values():
            cat = alias_info.get("category", "")
            sub = alias_info.get("sub_category", "")
            if cat and sub:
                result.setdefault(cat, [])
                if sub not in result[cat]:
                    result[cat].append(sub)
        return result


# 全局单例
search_semantics_service = SearchSemanticsService()
