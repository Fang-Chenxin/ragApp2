# Agent 对话应用完整系统

## 技术架构
✅ **后端**: FastAPI (Python)
✅ **向量数据库**: Chroma (本地持久化)
✅ **大模型**: Doubao-Seed-2.0-lite / 火山方舟
✅ **移动端**: Kotlin 100% 原生 Android 应用
✅ **非 Web 套壳方案，完全原生体验**

## 项目结构
```
.
├── backend/                  # FastAPI 后端服务
│   ├── api/                  # API 路由层
│   │   ├── chat.py          # 对话相关接口
│   │   └── knowledge.py     # 知识库管理接口
│   ├── config/              # 配置管理
│   │   └── settings.py      # 环境变量配置
│   ├── service/             # 业务逻辑层
│   │   ├── llm_service.py   # LLM 服务
│   │   ├── rag_service.py   # RAG 检索服务
│   │   └── history_service.py # 对话历史服务
│   ├── main.py              # 后端主入口
│   ├── requirements.txt     # Python 依赖
│   ├── .env.example         # 环境变量模板
│   └── test_api.sh          # API 测试脚本
├── android_app/             # Kotlin Android 原生应用
│   ├── app/src/main/java/com/example/agentchat/
│   │   ├── MainActivity.kt              # 主界面入口
│   │   ├── ChatAdapter.kt               # 聊天列表适配器
│   │   ├── ConversationsActivity.kt     # 对话列表页面
│   │   └── ConfigManager.kt             # 配置管理
│   ├── app/src/main/res/layout/
│   │   ├── activity_main.xml            # 主页面布局
│   │   ├── activity_conversations.xml   # 对话列表布局
│   │   ├── item_chat_user.xml           # 用户消息气泡
│   │   ├── item_chat_assistant.xml      # 助手消息气泡
│   │   └── item_chat_thinking.xml       # 思考过程展示
│   └── gradle/                          # Gradle 配置
├── docs/                    # 文档目录
│   └── 变更摘要/            # 变更记录
├── test/                    # 测试脚本
├── CHANGELOG.md             # 变更日志
└── .gitignore               # Git 忽略配置
```

## 快速启动后端
```bash
cd backend
pip install -r requirements.txt
export LLM_API_KEY="您的 API Key"
python main.py
```
后端服务将运行在 `http://0.0.0.0:8000`

### 环境变量配置
复制 `.env.example` 为 `.env` 并配置：
```bash
cp .env.example .env
# 编辑 .env 文件，设置 LLM_API_KEY 等配置
```

## Android 原生应用开发
用 Android Studio 直接打开 `android_app/` 目录
- 点击右上角的设置图标可动态配置后端服务器地址
- 地址保存后无需重新编译即可生效
- 支持 http:// 和 https:// 协议的地址

## 功能特性
- ✅ 多对话管理（新建、切换、删除对话）
- ✅ RAG 知识库检索
- ✅ 需求分析展示
- ✅ 流式响应支持
- ✅ 后端地址可配置化