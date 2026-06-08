"""流式工具对话 Pipeline - 拆分 ToolChatService 的阶段化流式编排。"""
from __future__ import annotations

import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from ..product_search.query_tool import get_tool_spec
from .stream_context import _StreamPipelineContext, _StreamTaskGroup
from .stream_final import ToolChatStreamFinalMixin
from .stream_stages import ToolChatStreamStagesMixin
from .stream_tool_loop import ToolChatStreamToolLoopMixin
from config.logging_config import get_logger
from config.settings import settings

logger = get_logger("service.tool_chat")


class ToolChatStreamMixin(ToolChatStreamStagesMixin, ToolChatStreamFinalMixin, ToolChatStreamToolLoopMixin):
    """阶段化流式工具对话编排。

    本 mixin 只负责入口和阶段顺序；每个阶段的具体逻辑拆在相邻模块中，
    这样 API 层可以统一消费事件，而内部可以继续扩展阶段。
    """

    async def chat_with_tools_stream(
        self,
        user_query: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
        max_tool_calls: int = 5,
        model: Optional[str] = None,
        model_config: Optional[Dict[str, Any]] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """使用原生 function calling 进行对话，按阶段流式返回 SSE 事件 payload。"""
        timings: Dict[str, Any] = {"parallel_enabled": settings.tool_chat_parallel_enabled}
        t_total_start = time.perf_counter()

        logger.debug("═══ [chat_with_tools_stream] 开始处理请求")
        logger.debug("  用户问题: %s", user_query)
        logger.debug("  历史消息数: %s", len(conversation_history) if conversation_history else 0)
        logger.debug("  最大工具调用轮数: %s", max_tool_calls)
        logger.debug("  使用模型: %s", model or (model_config or {}).get("id") or getattr(self.llm, "model", "default"))
        logger.debug("  并行流程: %s", "启用" if settings.tool_chat_parallel_enabled else "关闭")

        if not self.llm.connected and not (model_config or {}).get("api_key"):
            logger.warning("  LLM 服务未连接")
            yield {
                "type": "error",
                "content": "LLM 服务未连接，请检查 LLM_API_KEY 配置。",
                "timings": timings,
            }
            return

        # 上下文保存一轮请求的所有可变状态，避免阶段函数靠大量参数传递。
        ctx = _StreamPipelineContext(
            user_query=user_query,
            conversation_history=conversation_history,
            max_tool_calls=max_tool_calls,
            model=model,
            model_config=model_config,
            timings=timings,
            t_total_start=t_total_start,
            messages=self._build_tool_planning_messages(conversation_history, user_query),
            tools=[get_tool_spec()],
            task_group=_StreamTaskGroup(),
        )

        # 阶段顺序即导购主流程；阶段函数可以通过 `ctx.completed` 提前终止后续步骤。
        pipeline = [
            self._stream_stage_parallel_bootstrap,
            self._stream_stage_rag_pipeline,
            self._stream_stage_finish_analysis,
            self._stream_stage_direct_product_query,
            self._stream_stage_tool_loop,
            self._stream_stage_final_reply,
        ]

        try:
            for stage in pipeline:
                if ctx.completed:
                    break
                async for event in stage(ctx):
                    yield event
        finally:
            # 客户端断开或任一阶段提前返回时，取消尚未完成的并行分析/查询任务。
            await ctx.task_group.cancel_pending()
