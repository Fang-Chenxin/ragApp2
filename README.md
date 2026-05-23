# Agent 对话应用完整系统

## 技术架构
✅ **后端**: FastAPI (Python)
✅ **向量数据库**: Chroma (本地持久化)
✅ **大模型**: Doubao-Seed-2.0-lite
✅ **移动端**: Kotlin 100% 原生 Android 应用
✅ **非 Web 套壳方案，完全原生体验**

## 项目结构
```
.
├── backend/                  # FastAPI 后端服务
│   ├── main.py              # 后端主逻辑
│   └── requirements.txt     # Python 依赖
└── android_app/             # Kotlin Android 原生应用
    ├── app/
    │   ├── src/main/
    │   │   ├── java/com/example/agentchat/
    │   │   │   ├── MainActivity.kt      # 主界面入口
    │   │   │   └── ChatAdapter.kt       # 聊天列表适配器
    │   │   └── res/layout/
    │   │       ├── activity_main.xml          # 主页面布局
    │   │       ├── item_chat_user.xml        # 用户消息气泡
    │   │       └── item_chat_assistant.xml   # 助手消息气泡
    │   └── build.gradle.kts
    └── build.gradle.kts
```

## 快速启动后端
```bash
cd backend
pip install -r requirements.txt
export DOUBAO_API_KEY="您的豆包API Key"
python main.py
```
后端服务将运行在 `http://0.0.0.0:8000`

## Android 原生应用开发
用 Android Studio 直接打开 `android_app/` 目录
- 点击右上角的设置图标可动态配置后端服务器地址
- 地址保存后无需重新编译即可生效
- 支持 http:// 和 https:// 协议的地址
