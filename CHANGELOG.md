# Changelog

## [v1.0.9] - 2026-06-08

### Added
- ✨ 新增 LLM 搜索计划功能，用于结构化商品搜索和 direct/fallback 判定 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)
- ✨ 新增直接匹配判断逻辑，识别用户点名的具体商品并优先推荐 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)
- ✨ 新增替代品优先级选择，当无直接匹配时选择最接近的替代品 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)

### Changed
- 🔧 优化关键词搜索算法，增强混合内容拆分和同义词扩展 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)
- 🔧 优化商品匹配逻辑，支持基于搜索计划的品类过滤 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)
- 🔧 增强系统提示词，规范 fallback 商品的描述 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)
- 🔧 调整 LLM 重排超时时间从 6 秒增加到 30 秒，提升稳定性 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)
- 🔧 改进调试日志格式，增强可维护性 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)

### Fixed
- 🛡️ 修复商品搜索缺乏结构化规划导致推荐不准确的问题 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)
- 🛡️ 修复关键词搜索对混合内容拆分不充分的问题 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)
- 🛡️ 修复无法区分直接匹配商品和替代品的问题 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)
- 🛡️ 修复 fallback 商品描述不准确的问题 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)
- 🛡️ 修复 LLM 重排超时频繁导致搜索失败的问题 - [详细文档](docs/变更摘要/LLM搜索计划与商品匹配优化.md)

---

## [v1.0.8] - 2026-06-08

### Added
- ✨ 新增火山方舟多模态外部向量化接入，后端可固定使用 `/embeddings/multimodal` 构建和查询 RAG 索引 - [详细文档](docs/变更摘要/外部向量化模型接入与状态展示.md)
- 📱 新增 Android 聊天页向量模型状态展示，通过 `/health` 显示当前 embedding 连接状态 - [详细文档](docs/变更摘要/外部向量化模型接入与状态展示.md)

### Changed
- 🔧 Chroma 构建脚本复用后端 embedding 配置，避免索引构建和线上检索使用不同向量模型 - [详细文档](docs/变更摘要/外部向量化模型接入与状态展示.md)
- 🔧 固定 Chroma 与 SQLite 默认路径到项目内电商数据集，减少不同启动目录导致的错误查找 - [详细文档](docs/变更摘要/外部向量化模型接入与状态展示.md)
- 📝 更新 README 和环境变量模板，补充外部向量化模型配置与重建索引说明 - [详细文档](docs/变更摘要/外部向量化模型接入与状态展示.md)

### Fixed
- 🛡️ 改进 Chroma embedding function 冲突提示，明确要求按当前 embedding 模型重建索引 - [详细文档](docs/变更摘要/外部向量化模型接入与状态展示.md)

---

## [v1.0.7] - 2026-06-07

### Added
- 🧪 新增历史并发写入、流式中断保存、流式任务取消和工具规划消息顺序测试 - [详细文档](docs/变更摘要/后端稳定性与测试同步优化.md)

### Changed
- 🔧 优化对话历史 JSON 写入，增加文件锁与原子写入，提升并发安全性 - [详细文档](docs/变更摘要/后端稳定性与测试同步优化.md)
- 🔧 优化流式聊天历史保存逻辑，异常或断连后仍保存已生成回复 - [详细文档](docs/变更摘要/后端稳定性与测试同步优化.md)
- 🔧 优化工具聊天服务任务管理，统一取消流式期间未完成后台任务 - [详细文档](docs/变更摘要/后端稳定性与测试同步优化.md)
- 🔧 将工具聊天服务按流式 Pipeline、RAG、prompt、目标商品选择和 trace 格式化拆分为 `tool_chat/` 职责型子包，并继续拆分流式上下文、基础阶段、工具循环和最终回复 - [详细文档](docs/变更摘要/后端稳定性与测试同步优化.md)
- 🔧 将商品检索实现收拢到 `product_search/` 职责型子包，区分查询引擎、SQLite 搜索服务和 Function Calling 工具 - [详细文档](docs/变更摘要/后端稳定性与测试同步优化.md)
- 📝 新增后端服务文件查找说明，记录顶层服务入口、职责型子包和常见问题定位路径 - [详细文档](docs/后端服务文件查找说明.md)
- 🔧 抽取工具规划和最终回复消息构造逻辑，减少流式和非流式路径重复代码 - [详细文档](docs/变更摘要/后端稳定性与测试同步优化.md)
- 🔧 优化 ontology 加载和反向索引构建缓存，减少重复 IO 和重复计算 - [详细文档](docs/变更摘要/后端稳定性与测试同步优化.md)
- 🧪 同步 RAG 和真实工具聊天测试口径，以正式版结构化事件和 metadata 为准 - [详细文档](docs/变更摘要/后端稳定性与测试同步优化.md)

### Fixed
- 🛡️ 修复非流式工具查询同步执行可能阻塞事件循环的问题 - [详细文档](docs/变更摘要/后端稳定性与测试同步优化.md)
- 🛡️ 修复 SQLite 商品精确回查连接生命周期不清晰的问题 - [详细文档](docs/变更摘要/后端稳定性与测试同步优化.md)
- 🛡️ 修复模型发现并发请求可能重复访问外部模型源的问题 - [详细文档](docs/变更摘要/后端稳定性与测试同步优化.md)

---

## [v1.0.6] - 2026-06-03

### Changed
- 🔧 优化 RAG 服务初始化流程，改进 Embedding 服务配置和错误处理 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)
- 🔧 优化 LLM 服务 HTTP 客户端配置，增加超时设置和连接池管理 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)
- 🔧 优化商品搜索服务数据库可用性检查和查询结果格式化 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)
- 🔧 优化商品查询工具参数定义和自然语言查询支持 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)
- 🔧 优化工具聊天服务 RAG 来源提取和上下文文档格式化 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)
- 🔧 优化 API 层请求参数验证和模型配置处理 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)
- 🔧 优化配置管理环境变量读取和模型配置解析 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)
- 🔧 优化日志配置过滤器和控制台输出格式 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)
- 📱 优化 Android 聊天适配器消息绑定和流式内容更新 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)
- 📱 优化 Android 主界面会话切换和模型配置处理 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)
- 🧹 清理冗余测试文件 `test/test_tool_chat_service_streaming.py` - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)

### Fixed
- 🛡️ 修复 HTTP 客户端超时配置不当导致的长请求中断问题 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)
- 🛡️ 修复日志过滤器过于严格导致关键信息被过滤的问题 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)
- 🛡️ 修复 Android 端会话切换时可能出现的界面卡顿问题 - [详细文档](docs/变更摘要/后端服务优化与Android端稳定性改进.md)

---

## [v1.0.5] - 2026-06-03

### Added
- ✨ 新增导购流程并行化原型，需求分析、第一轮工具规划、SQLite 直查与 RAG 链路可重叠执行 - [详细文档](docs/变更摘要/导购流程并行化与目标商品白名单.md)
- ✨ 新增目标商品白名单结构，最终推荐基于后端确定的 `selected_products` 生成 - [详细文档](docs/变更摘要/导购流程并行化与目标商品白名单.md)
- ✨ 新增 RAG 来源事件 `rag_sources`，流式接口可透传知识库来源商品和片段信息 - [详细文档](docs/变更摘要/导购流程并行化与目标商品白名单.md)
- 🧪 新增真实 RAG 向量查询测试，验证电商商品知识库路径、metadata 和上下文格式化 - [详细文档](docs/变更摘要/导购流程并行化与目标商品白名单.md)

### Changed
- 🔧 拆分需求分析、商品查询规划、最终导购回复三类 prompt，工具查询不再依赖 RAG 核验结果 - [详细文档](docs/变更摘要/导购流程并行化与目标商品白名单.md)
- 🔧 最终导购回复在存在目标商品时强制走受约束生成，避免直接放行工具规划轮自由回复 - [详细文档](docs/变更摘要/导购流程并行化与目标商品白名单.md)
- 🔧 默认 Chroma 和 SQLite 路径切换到项目内电商数据集，并新增 RAG 检索超时与 LLM 核验配置 - [详细文档](docs/变更摘要/导购流程并行化与目标商品白名单.md)
- 🔧 RAG 服务支持返回带来源 metadata 的向量检索结果，并统一格式化商品知识上下文 - [详细文档](docs/变更摘要/导购流程并行化与目标商品白名单.md)
- 📝 更新变更摘要撰写规范，要求每次同步维护项目介绍说明末尾版本信息 - [详细文档](docs/变更摘要/导购流程并行化与目标商品白名单.md)
- 🧪 更新真实流式测试追踪，使用并行分支展示阶段，并补充并行重叠耗时说明 - [详细文档](docs/变更摘要/导购流程并行化与目标商品白名单.md)

### Fixed
- 🛡️ 修复显式传入 `temperature=0.0` 时被默认温度覆盖的问题 - [详细文档](docs/变更摘要/导购流程并行化与目标商品白名单.md)

---

## [v1.0.4] - 2026-06-02

### Added
- ✨ 新增服务端模型列表接口，Android 端可刷新并选择服务端提供的模型 - [详细文档](docs/变更摘要/模型选择与自定义模型管理.md)
- ✨ 新增 Android 当前模型选择栏，支持服务端模型与本机自定义模型切换 - [详细文档](docs/变更摘要/模型选择与自定义模型管理.md)
- ✨ 新增自定义模型添加、编辑、删除能力，支持 OpenAI-compatible 模型连接配置 - [详细文档](docs/变更摘要/模型选择与自定义模型管理.md)

### Changed
- 🔧 聊天接口支持按 `model` / `model_config` 调用不同模型配置 - [详细文档](docs/变更摘要/模型选择与自定义模型管理.md)
- 🔧 LLM 服务和工具聊天流程透传模型配置，支持服务端模型与本地自定义模型共存 - [详细文档](docs/变更摘要/模型选择与自定义模型管理.md)
- 📱 自定义模型表单明确区分显示字段和实际请求字段，并强化必填校验 - [详细文档](docs/变更摘要/模型选择与自定义模型管理.md)

### Fixed
- 🛡️ 修复自定义模型与服务端模型同 ID 时，服务端模型可能被误判为本地模型的问题 - [详细文档](docs/变更摘要/模型选择与自定义模型管理.md)

---

## [v1.0.3] - 2026-06-01

### Changed
- 📱 Android 端重建会话切换后的聊天 Adapter，清理旧 ViewHolder/焦点/复用状态，提升文本选取稳定性 - [详细文档](docs/变更摘要/Android文本选取与会话切换稳定性修复.md)
- 📱 思考区域折叠点击范围缩小到标题行，正文区域优先支持文本选取 - [详细文档](docs/变更摘要/Android文本选取与会话切换稳定性修复.md)
- 🔧 清理 Markdown 链接/点击 Span，避免和原生长按选字冲突 - [详细文档](docs/变更摘要/Android文本选取与会话切换稳定性修复.md)

### Fixed
- 🛡️ 修复从对话列表切换后部分消息正文无法选中的问题 - [详细文档](docs/变更摘要/Android文本选取与会话切换稳定性修复.md)
- 🛡️ 修复重新进入对话时旧会话文本残留并叠加显示的问题 - [详细文档](docs/变更摘要/Android文本选取与会话切换稳定性修复.md)

---

## [v1.0.2] - 2026-05-29

### Changed
- ✨ 移除 `include_thinking` 参数，统一思考/分析数据流为单一 `analysis` 通道 - [详细文档](docs/变更摘要/需求分析折叠展示与思考流程统一.md)
- 📱 Android 端重构助手消息布局：新增可折叠需求分析区域、复制回复按钮、Markdown 渲染支持 - [详细文档](docs/变更摘要/需求分析折叠展示与思考流程统一.md)
- 🔧 需求分析结果为空时自动降级为简化分析兜底 - [详细文档](docs/变更摘要/需求分析折叠展示与思考流程统一.md)
- 🔧 构建最终消息列表时保留 `tool_call_id` 字段，兼容更多 LLM 提供方 - [详细文档](docs/变更摘要/需求分析折叠展示与思考流程统一.md)
- 🔧 移除 Android 端"显示思考过程"和"请求思考内容"两个开关控件 - [详细文档](docs/变更摘要/需求分析折叠展示与思考流程统一.md)
- 🔧 日志级别调整：分析耗时/摘要日志从 `info` 降级为 `debug` - [详细文档](docs/变更摘要/需求分析折叠展示与思考流程统一.md)

### Fixed
- 🛡️ 修复 tool 消息缺少 `tool_call_id` 导致部分 LLM 提供方报错的问题 - [详细文档](docs/变更摘要/需求分析折叠展示与思考流程统一.md)
- 🛡️ 修复消息文本无法选择复制的问题 - 所有消息支持文本选择 - [详细文档](docs/变更摘要/需求分析折叠展示与思考流程统一.md)

---

## [v1.0.1] - 2026-05-29

### Changed
- 🔧 修改 [backend/service/sqlite_product_query_tool.py](file:///home/fang/Documents/trae_projects/ragApp/backend/service/sqlite_product_query_tool.py) - 优化商品搜索降级与属性过滤器规范 - [详细文档](docs/变更摘要/后端商品搜索服务优化.md)


## [v1.0.0] - 2026-05-28

### Added
- ✨ 实现完整导购聊天服务，支持需求分析、商品搜索、结果整理全流程 - [详细文档](docs/变更摘要/导购聊天服务与流式交互优化.md)
- ✨ 新增变更摘要文档规范更新，明确项目介绍说明文档更新要求 - [详细文档](docs/变更摘要/变更摘要文档规范更新.md)
- ✨ 新增多对话管理功能，支持创建、切换、删除会话 - [详细文档](docs/变更摘要/多对话页面.md)
- ✨ 新增思考过程展示功能，支持开关控制显示/隐藏 - [详细文档](docs/变更摘要/思考过程持久化与显示优化.md)
- ✨ 新增电商商品查询工具，支持 LLM 自动调用 - [详细文档](docs/变更摘要/电商数据库服务.md)
- ✨ 新增全链路性能监控，统计向量检索、LLM推理、工具查询耗时 - [详细文档](docs/变更摘要/查询工具接入后端.md)
- ✨ 新增 [EcommerceService](file:///home/fang/Documents/trae_projects/ragApp/backend/service/ecommerce_service.py) - 电商数据库查询服务
- ✨ 新增 [QueryEngine](file:///home/fang/Documents/trae_projects/ragApp/backend/service/query_engine.py) - 商品查询引擎
- ✨ 新增 `/api/ecommerce` 路由，提供自然语言搜索和结构化查询接口
- ✨ 新增服务层统一管理 (`backend/service/__init__.py`)
- ✨ 新增原生 OpenAI Function Calling 支持
- ✨ 新增项目介绍说明文档 - [项目介绍说明.md](docs/项目介绍说明.md)

### Changed
- 🔧 修改 [rag_service.py](file:///home/fang/Documents/trae_projects/ragApp/backend/service/rag_service.py) - 重构工具调用机制，升级为原生 Function Calling
- 🔧 修改 [llm_service.py](file:///home/fang/Documents/trae_projects/ragApp/backend/service/llm_service.py) - 新增 `chat_with_tools()` 方法
- 🔧 修改 [history_service.py](file:///home/fang/Documents/trae_projects/ragApp/backend/service/history_service.py) - 升级为多会话存储架构，所有方法新增 `conv_id` 参数
- 🔧 修改 [chat.py](file:///home/fang/Documents/trae_projects/ragApp/backend/api/chat.py) - API 响应新增 `timings` 字段，接口重构
- 🔧 修改 [settings.py](file:///home/fang/Documents/trae_projects/ragApp/backend/config/settings.py) - 新增电商数据库路径配置，更新默认模型 ID
- 🔧 修改 [main.py](file:///home/fang/Documents/trae_projects/ragApp/backend/main.py) - 统一服务初始化管理
- 🔧 修改导入方式 - 统一为 `from service import xxx`
- 🔧 将查询引擎从 `ecommerce_agent_dataset/` 迁移到 `backend/service/`
- 🔧 新增测试脚本目录 `test/`

### Fixed
- 🛡️ 修复关闭思考显示按钮时不保存思考过程的问题 - 后台全程记录
- 🛡️ 修复流式输出后思考内容自动错误消失的问题 - 思考气泡永久保留
- 🛡️ 修复历史记录读取后后续对话触发 400 错误的问题 - 三重防护纯净化历史消息
- 🛡️ 修复 thinking 字段混入大模型对话记忆的问题 - 前后端双重剥离

---

## [Unreleased] - 2026-05-24

### Added
- ✨ 新增 [ConfigManager](file:///home/fang/Documents/trae_projects/ragApp/android_app/app/src/main/java/com/example/agentchat/ConfigManager.kt) - 统一管理后端服务器地址 - [详细文档](docs/变更摘要/后端地址可配置化.md)
- ✨ 新增"服务器设置"界面，支持动态配置地址 - [详细文档](docs/变更摘要/后端地址可配置化.md)
- ✨ 新增设置图标 (ic_settings.xml)
- 📝 新增变更摘要文档规范 - [变更摘要文档撰写规范.md](docs/变更摘要文档撰写规范.md)

### Changed
- 🔧 修改 [network_security_config.xml](file:///home/fang/Documents/trae_projects/ragApp/android_app/app/src/main/res/xml/network_security_config.xml) - 更新网络安全配置
- 🔧 修改 [MainActivity.kt](file:///home/fang/Documents/trae_projects/ragApp/android_app/app/src/main/java/com/example/agentchat/MainActivity.kt) - 移除硬编码 BACKEND_URL，改用 ConfigManager
- 🔧 修改 [ConversationsActivity.kt](file:///home/fang/Documents/trae_projects/ragApp/android_app/app/src/main/java/com/example/agentchat/ConversationsActivity.kt) - 改用 ConfigManager
- 🔧 修改 [menu_main.xml](file:///home/fang/Documents/trae_projects/ragApp/android_app/app/src/main/res/menu/menu_main.xml) - 添加服务器设置菜单项
- 📝 更新 [README.md](file:///home/fang/Documents/trae_projects/ragApp/README.md) - 更新 Android 应用开发说明

### Fixed
- 🛡️ 修复点击当前空对话会被错误删除的问题 - [详细文档](docs/变更摘要/对话列表点击当前空对话误删除修复.md)
- 🛡️ 修复服务器地址配置缺少 URL 格式验证的问题 - 确保 URL 必须以 http:// 或 https:// 开头
