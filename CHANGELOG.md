# Changelog

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
