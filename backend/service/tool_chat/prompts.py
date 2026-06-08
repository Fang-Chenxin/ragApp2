"""工具聊天 Prompt 与历史上下文构造。"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..product_search.sqlite_search import sqlite_product_search_service


class ToolChatPromptMixin:
    """需求分析、工具规划和最终回复 prompt 构造能力。"""

    @staticmethod
    def _build_need_analysis_messages(
        conversation_history: Optional[List[Dict[str, str]]],
        user_query: str,
    ) -> List[Dict[str, str]]:
        """构造需求分析子任务消息，输出给前端展示并作为最终推荐摘要。"""
        history_context = ToolChatPromptMixin._build_history_context(conversation_history, user_query)
        return [
            {
                "role": "system",
                "content": (
                    "你是导购助手的需求分析子角色。请只基于用户问题和历史对话，"
                    "用自然、简洁、像人在解释需求的方式，输出 1-3 句需求分析。"
                    "要求：1) 说明用户真正想解决什么；2) 说明你准备优先检索的方向；"
                    "3) 如果明显是场景/体验诉求，直接说成场景型购物需求；4) 不要列商品，不要写工具过程，不要编号；"
                    "5) 不要假设知识库或商品库已经命中了任何商品。\n\n"
                    f"{history_context}"
                ),
            },
            {
                "role": "user",
                "content": f"用户问题：{user_query}\n\n请给出需求分析。",
            },
        ]

    @staticmethod
    def _build_need_analysis_summary(user_query: str, conversation_history: Optional[List[Dict[str, str]]] = None) -> str:
        """LLM 分析失败或为空时的规则兜底摘要。"""
        query = user_query.strip()

        scene_tags: list[str] = []
        if any(keyword in query for keyword in ["流畅", "更快", "更稳", "对战", "高手", "体验", "游戏", "高刷", "降噪", "续航", "轻薄", "学习", "办公"]):
            scene_tags.append("场景型需求")
        if any(keyword in query for keyword in ["手机", "平板", "笔记本", "耳机", "电脑", "数码", "电子"]):
            scene_tags.append("数码电子")
        if any(keyword in query for keyword in ["品牌", "苹果", "华为", "小米", "联想", "三星", "索尼", "飞利浦"]):
            scene_tags.append("品牌/型号约束")

        if not scene_tags:
            scene_tags.append("通用导购")

        history_hint = ""
        if conversation_history:
            last_user = next(
                (msg.get("content", "").strip() for msg in reversed(conversation_history) if msg.get("role") == "user" and msg.get("content")),
                "",
            )
            if last_user:
                history_hint = f"，结合上一轮问题'{last_user[:24]}'继续缩小范围"

        analysis_parts = [
            f"初步判断：这是一个{'、'.join(scene_tags)}问题",
            f"当前关键词：{query[:40] if query else '无'}",
        ]
        if "场景型需求" in scene_tags:
            analysis_parts.append("我会优先把它理解为提升体验的购物需求，先看最能解决场景问题的商品")
        else:
            analysis_parts.append("我会先找直接相关商品，如果没有再转向相邻品类")
        if history_hint:
            analysis_parts.append(history_hint)

        return "；".join(analysis_parts) + "。"


    @staticmethod
    def _build_tool_planning_prompt(conversation_history: Optional[List[Dict[str, str]]], user_query: str) -> str:
        """构造工具规划 system prompt，指导 LLM 把用户需求转成 `query_products` 参数。"""
        return (
            "你是导购助手的商品查询规划子角色。你的任务是把用户需求转成 query_products 工具查询，"
            "不要依赖知识库内容，也不要编造商品事实。\n\n"
            f"{ToolChatPromptMixin._build_history_context(conversation_history, user_query)}\n\n"
            "## 商品查询策略（严格遵守）\n"
            "1. 先判断用户想解决的真实问题，再决定检索方向；不要只盯着用户字面上的词。\n"
            "2. 优先推荐直接相关商品；如果没有直接相商品，必须转向次相关商品或相邻品类，不要只说没有。\n"
            "3. 当用户是在描述目标、场景或体验诉求时，要把需求理解为场景型购物需求，"
            "并结合数据库中真实存在的品类检索可购买商品。\n"
            "4. 如果直搜某个品牌、场景或泛词没有结果，要主动换用更具体的品类、用途、属性或预算重搜；"
            "仍无结果时如实说明并询问用户是否接受相邻品类。\n"
            "5. 当前阶段优先调用工具查询商品库；只有用户问题明显不需要商品检索时，才直接回复。\n"
            "6. 如果工具返回结果，最终回复只能基于工具结果中的商品信息，不要向用户展示内部商品ID。\n\n"
            "## 调用工具规则（严格遵守）\n"
            "1. 调用 query_products 时，必须提供有效的查询参数（text、keyword、brand 等），禁止传空参数 {}。\n"
            "2. 如果用户的问题引用了对话历史中的商品（如'这几个''上面的''那款''这个牌子'），"
            "   你必须从上方「最近对话中的商品信息」中提取品牌名、商品名等关键词作为 text 参数。\n"
            "3. 如果用户的需求是场景型或目标型，优先使用扩展后的场景关键词发起查询，而不是只搜原始名词。\n"
            "4. 如果第一次检索没有直接命中，不要结束对话，要立即转向次相关品类重新组织推荐。"
        )

    @staticmethod
    def _build_tool_planning_messages(
        conversation_history: Optional[List[Dict[str, str]]],
        user_query: str,
    ) -> list[Dict[str, Any]]:
        """构造工具循环的初始 messages：system + 历史 + 当前用户问题。"""
        messages: list[Dict[str, Any]] = [
            {
                "role": "system",
                "content": ToolChatPromptMixin._build_tool_planning_prompt(conversation_history, user_query),
            }
        ]
        if conversation_history:
            for msg in conversation_history:
                messages.append({"role": msg["role"], "content": msg["content"]})
        messages.append({"role": "user", "content": user_query})
        return messages

    @staticmethod
    def _build_system_prompt(context_text: str, conversation_history: Optional[List[Dict[str, str]]], user_query: str) -> str:
        """构造最终回复 system prompt，强调商品事实以 SQLite 工具结果为准。"""
        rag_section = context_text.strip() or "（知识库未命中或无可用上下文，请主要依据商品数据库工具结果回答。）"
        return (
            "你是一个资深导购型商品助手，负责整合商品数据库查询结果和可用知识库线索，"
            "输出面向用户的最终导购建议。\n\n"
            f"参考知识库内容：\n{rag_section}\n\n"
            f"{ToolChatPromptMixin._build_history_context(conversation_history, user_query)}\n\n"
            "## 最终回复策略（严格遵守）\n"
            "1. 商品事实、价格、品牌和品类优先以工具查询结果为准。\n"
            "2. 知识库内容只作为解释商品卖点、适配场景和补充背景的依据；如果知识库为空或无关，不要阻塞推荐。\n"
            "3. 如果 RAG 未命中但工具命中了商品，要正常基于商品数据库结果给出推荐。\n"
            "4. 如果工具和知识库信息冲突，以工具结果中的结构化商品信息为准。\n"
            "5. 最终回答要像导购：先一句话概括用户需求，再给出 3-5 个推荐方向或具体商品，并说明每个推荐为什么相关。\n"
            "6. 不要编造工具结果之外的商品；product_id、sku_id 等内部字段除非用户明确询问，不要在对外回复里展示。"
        )


    @staticmethod
    def _build_history_context(
        conversation_history: Optional[List[Dict[str, str]]],
        user_query: str
    ) -> str:
        """从对话历史和工具结果中提取商品信息，注入 system prompt 帮助 LLM 定位关键词"""
        if not conversation_history:
            return ""

        product_mentions: list[str] = []

        for msg in reversed(conversation_history):
            # 先识别历史中出现的内部 product_id，再回查数据库得到用户可理解的商品名/品牌。
            content = msg.get("content", "")
            if not content:
                continue

            product_ids = re.findall(r'[psc]_[a-z]+_\d+(?:_\d+)?', content)
            product_lookup_ids = [pid for pid in product_ids if pid.startswith("p_")]
            if product_lookup_ids:
                lookup = sqlite_product_search_service.get_products_by_ids(product_lookup_ids)
                if lookup.get("ok"):
                    for item in lookup.get("items") or []:
                        title = str(item.get("title") or "").strip()
                        brand = str(item.get("brand") or "").strip()
                        if title:
                            product_mentions.append(f"名称: {title}")
                        if brand:
                            product_mentions.append(f"品牌: {brand}")

            brands = re.findall(
                r'(华为|小米|苹果|三星|OPPO|vivo|荣耀|联想|戴尔|惠普|'
                r'农夫山泉|元气森林|东鹏|可口可乐|百事|蒙牛|伊利|'
                r'耐克|阿迪达斯|安踏|李宁|优衣库|'
                r'兰蔻|雅诗兰黛|欧莱雅|资生堂|完美日记|花西子|'
                r'索尼|飞利浦|美的|格力|海尔)',
                content
            )

            backtick_names = re.findall(r'`([^`]{2,50})`', content)

            for brand in set(brands):
                product_mentions.append(f"品牌: {brand}")
            for name in backtick_names[:5]:
                product_mentions.append(f"名称: {name}")

            if len(product_mentions) >= 6:
                break

        if not product_mentions:
            return ""

        # 去重后注入 prompt，让“上面那几款/这个牌子”这类追问能转成有效工具参数。
        seen: set[str] = set()
        unique: list[str] = []
        for item in product_mentions:
            if item not in seen:
                seen.add(item)
                unique.append(item)

        context = "## 最近对话中的商品信息（供工具调用参考）\n"
        context += "\n".join(f"- {item}" for item in unique[:10])
        context += f"\n\n用户当前问题可能引用以上商品，请据此构造查询参数。"
        return context
