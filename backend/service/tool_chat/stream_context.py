"""流式工具聊天 Pipeline 上下文对象。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


class _StreamTaskGroup:
    """记录一次流式请求期间创建的后台任务，便于请求结束时统一取消。"""

    def __init__(self):
        """初始化任务列表。"""
        self._tasks: list[asyncio.Task[Any]] = []

    def create(self, coro) -> asyncio.Task[Any]:
        """创建后台任务并登记，返回任务对象供阶段等待结果。"""
        task = asyncio.create_task(coro)
        self._tasks.append(task)
        return task

    async def cancel_pending(self) -> None:
        """取消所有尚未完成的后台任务，避免客户端断开后继续消耗资源。"""
        pending = [task for task in self._tasks if not task.done()]
        if not pending:
            return
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)


@dataclass
class _StreamPipelineContext:
    """流式导购请求的跨阶段状态容器。"""
    user_query: str
    conversation_history: Optional[List[Dict[str, str]]]
    max_tool_calls: int
    model: Optional[str]
    model_config: Optional[Dict[str, Any]]
    timings: Dict[str, Any]
    t_total_start: float
    messages: list[Dict[str, Any]]
    tools: list[Dict[str, Any]]
    task_group: _StreamTaskGroup
    analysis_queue: asyncio.Queue[Dict[str, Any]] = field(default_factory=asyncio.Queue)
    analysis_text: str = ""
    analysis_elapsed: float = 0.0
    analysis_done: bool = False
    context_docs: List[Any] = field(default_factory=list)
    context_text: str = ""
    rag_sources: List[Dict[str, Any]] = field(default_factory=list)
    final_system_prompt: str = ""
    direct_selected_products: List[Dict[str, Any]] = field(default_factory=list)
    direct_query_elapsed: float = 0.0
    direct_query_error: Optional[str] = None
    llm_call_total: float = 0.0
    tool_call_total: float = 0.0
    llm_rounds: int = 0
    tool_rounds: int = 0
    consecutive_empty_params: int = 0
    tool_results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    tool_call_order: list[str] = field(default_factory=list)
    parallel_branch_start: float = 0.0
    analysis_task: Optional[asyncio.Task[tuple[str, float]]] = None
    direct_selected_products_task: Optional[asyncio.Task[tuple[List[Dict[str, Any]], float, Optional[str]]]] = None
    first_tool_planning_task: Optional[asyncio.Task[tuple[Any, Optional[Exception], float, float]]] = None
    completed: bool = False
