# Changelog

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
- 🛡️ 修复服务器地址配置缺少 URL 格式验证的问题 - 确保 URL 必须以 http:// 或 https:// 开头
