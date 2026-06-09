# LLM搜索计划与商品匹配优化 - 变更说明

## 变更时间
2026-06-08

## 变更概述
本次变更主要优化了商品搜索和匹配的准确性与智能性，包括：
- 新增 LLM 生成的搜索计划功能，用于结构化商品搜索
- 改进关键词搜索算法，增强混合内容拆分和同义词扩展
- 新增直接匹配和替代品匹配逻辑，提升推荐准确性
- 调整 LLM 重排超时时间，提升稳定性
- 改进调试日志格式，增强可维护性

---

## 后端变更

### 1. 搜索计划功能 (`backend/service/tool_chat/prompts.py`)
- **新增方法**: `_build_search_plan_messages()`
- **核心逻辑**:
  - 构造商品搜索结构化计划子任务消息
  - 定义搜索计划 JSON schema，包含目标商品、类目、检索词、直接匹配词、可接受替代词等
  - 为 LLM 提供清晰的搜索计划生成指令

### 2. 搜索计划解析与标准化 (`backend/service/tool_chat/product_selection.py`)
- **新增方法**: `_normalize_search_plan()` 和 `_parse_search_plan_content()`
- **核心逻辑**:
  - 标准化 LLM 生成的商品搜索计划
  - 从 LLM 文本中解析 SearchPlan JSON，支持 Markdown 代码块格式
  - 提供有效的搜索计划或返回 None

### 3. 关键词搜索优化 (`backend/service/product_search/engine.py`)
- **修改位置**: `build_keyword_terms()` 和新增 `strip_intent_words()`、`score_keyword_match()`
- **新增/优化要点**:
  - 增强混合内容拆分（英文+中文+数字）
  - 改进中文二元组拆分，支持重叠二元组
  - 新增术语扩展映射（如 "ipad" 扩展到 ["iPad", "平板", "Apple"]）
  - 新增意图词移除功能，过滤导购问句中的语气词
  - 新增关键词相关性打分，用于商品排序

### 4. 商品匹配逻辑优化 (`backend/service/tool_chat/product_selection.py`)
- **新增方法**: `_is_direct_product_match()` 和 `_prefer_closest_fallbacks()`
- **核心逻辑**:
  - 新增直接匹配判断，识别用户点名的具体商品
  - 新增替代品优先级选择，当无直接匹配时选择最接近的替代品
  - 改进商品约束过滤，支持基于搜索计划的品类过滤
  - 增强系统提示词，规范 fallback 商品的描述

### 5. 流式处理阶段优化 (`backend/service/tool_chat/stream_stages.py`)
- **新增方法**: `_stream_run_search_plan()`
- **核心逻辑**:
  - 用 LLM 生成商品搜索结构化计划
  - 失败时返回 None，让规则兜底接管
  - 与需求分析、工具规划并行执行

### 6. 超时时间调整 (`backend/config/settings.py`)
- **修改位置**: `rag_llm_rerank_timeout_seconds`
- **变更**: 从 6.0 秒增加到 30.0 秒
- **目的**: 提升 LLM 重排的稳定性，避免超时中断

### 7. 调试日志改进 (`backend/service/tool_chat/trace.py`)
- **修改位置**: `_format_trace_item()` 和各阶段日志格式
- **新增/优化要点**:
  - 在商品日志中添加 match_type 字段
  - 改进召回阶段日志格式，使用"召回"替代"直查"
  - 新增搜索计划阶段日志格式
  - 改进目标商品阶段日志格式

---

## 核心问题修复清单
| 问题描述 | 修复状态 |
|---|---|
| 商品搜索缺乏结构化规划，导致推荐不准确 | ✅ 已修复（新增 LLM 搜索计划功能） |
| 关键词搜索对混合内容拆分不充分 | ✅ 已修复（增强拆分算法） |
| 无法区分直接匹配商品和替代品 | ✅ 已修复（新增直接匹配判断） |
| fallback 商品描述不准确 | ✅ 已修复（增强系统提示词规范） |
| LLM 重排超时频繁导致搜索失败 | ✅ 已修复（增加超时时间） |

---

## 用户使用说明
1. 系统现在会先用 LLM 生成搜索计划，然后根据计划执行商品搜索
2. 搜索结果会区分直接匹配商品和替代品，优先推荐直接匹配的商品
3. 当没有直接匹配的商品时，系统会推荐相邻品类的替代品，并明确说明
4. 商品推荐的准确性得到提升，特别是对具体商品型号的识别

---

## 参考文件
- [backend/service/tool_chat/prompts.py](../../backend/service/tool_chat/prompts.py)
- [backend/service/tool_chat/product_selection.py](../../backend/service/tool_chat/product_selection.py)
- [backend/service/product_search/engine.py](../../backend/service/product_search/engine.py)
- [backend/service/tool_chat/stream_stages.py](../../backend/service/tool_chat/stream_stages.py)
- [backend/config/settings.py](../../backend/config/settings.py)
- [backend/service/tool_chat/trace.py](../../backend/service/tool_chat/trace.py)
