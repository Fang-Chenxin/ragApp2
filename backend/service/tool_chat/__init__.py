"""工具聊天服务内部模块。

阅读入口:
- `stream_pipeline.py`: 流式入口和阶段顺序编排
- `stream_context.py`: 流式共享上下文和后台任务组
- `stream_stages.py`: 需求分析、RAG、商品直查等基础阶段
- `stream_tool_loop.py`: 工具规划、工具执行和结果归档循环
- `stream_final.py`: 目标商品合并和最终回复阶段
- `rag.py`: 知识库检索与 LLM rerank
- `prompts.py`: 需求分析、工具规划和最终回复 prompt
- `product_selection.py`: 目标商品提取、校验和回复清洗
- `trace.py`: status/debug 事件和调试日志格式化
"""

