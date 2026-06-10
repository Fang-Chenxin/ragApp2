# 智能电商导购对话系统

这是一个 **Android 原生客户端 + FastAPI 后端 + RAG 知识库 + SQLite 商品库 + LLM 工具调用** 的智能电商导购应用。用户可以像真实购物一样连续提问，系统会自动分析需求、检索知识库、查询商品数据库、约束目标商品白名单，并以流式方式在 Android 端展示导购回复和商品卡片。

项目当前重点不是通用闲聊，而是电商导购场景：支持“随便看看 / 明确购买”购买阶段识别、多轮追问承接、品牌和成分排除、多商品对比决策、替代推荐、模型切换、多会话历史管理和异常降级处理。

---

## 技术架构

| 层级 | 技术 / 模块 | 说明 |
| --- | --- | --- |
| Android 客户端 | Kotlin 原生 Android | 非 Web 套壳，负责聊天、模型选择、会话列表、Markdown/表格/商品卡片展示 |
| 后端服务 | FastAPI + SSE | 提供聊天、模型列表、会话管理、知识库和商品搜索接口 |
| 大模型 | 火山方舟 / OpenAI-compatible | 支持服务端模型列表、本机自定义模型配置和流式输出 |
| RAG | Chroma 本地向量库 | 商品知识片段检索、RAG 来源展示、LLM rerank 核验 |
| 商品库 | SQLite | 商品、SKU、属性、FAQ、评价、图片和落地页查询 |
| 业务语义表 | search_semantics + attribute_ontology | 商品概念同义词、品类别名、fallback 关系、品牌型号、场景标签和属性标准化 |
| 导购链路 | SearchPlan + query_products 工具 | 结构化解析用户需求，过滤候选商品，生成受约束最终回复 |
| 数据与测试 | ecommerce_agent_dataset / test | 商品数据、语义表、Chroma 构建脚本和回归测试 |

---

## 项目亮点 / 创新点

1. **RAG + 商品库白名单约束的导购生成机制**  
   系统不是单纯让大模型自由推荐，而是先通过 RAG、SQLite 商品库和工具调用召回候选，再生成目标商品白名单，最终回复只能围绕已校验商品展开，降低商品幻觉和“编造不存在商品”的风险。

2. **面向真实购物语义的 SearchPlan 导购规划**  
   后端会把用户自然语言解析成结构化 SearchPlan，自动识别“随便看看/明确购买”、多轮追问、价格偏好、品牌/成分排除和对比决策等场景。相比普通问答机器人，更贴近真实导购里的连续筛选和取舍过程。

3. **移动端友好的结构化对比展示**  
   多商品对比时，后端生成标准 Markdown 对比表，Android 端再解析成“推荐排序 + 商品卡片矩阵”，避免传统表格在手机屏幕上列宽过窄、文字重叠、阅读困难的问题，更适合移动端导购决策。

---

## 对话处理流程

一轮导购请求的主要链路如下：

```text
用户输入
→ 需求分析：生成 Android 端可折叠的“已思考”内容
→ RAG 检索：从 Chroma 商品知识库召回片段
→ RAG 核验：使用 LLM 检查片段相关性并过滤
→ SearchPlan：解析目标商品、类目、排除条件、价格偏好、对比意图
→ 语义表修正：注入同义词、品类别名、fallback 规则、场景标签和属性标准化
→ SQLite 直查 / query_products 工具：召回结构化商品候选
→ 目标商品白名单：合并 direct/fallback 候选并执行约束过滤
→ 最终回复：只基于白名单商品生成导购建议
→ Android 展示：流式正文、需求分析、商品卡片、对比卡片矩阵
→ 历史保存：保存用户消息、助手回复、思考内容和商品清单
```

最终回复阶段会检查模型是否提到非白名单商品。如果直接回复包含清单外商品，后端会强制进入受约束最终回复，降低商品幻觉风险。

### 并行处理机制

导购链路不是完全串行执行。后端在 `parallel_bootstrap` 阶段会尽早并行启动几条互不依赖的 LLM 分支，后续阶段再接力执行 RAG 检索和 SQLite 直查：

- 需求分析 LLM：生成 Android 端可折叠的“已思考”内容。
- SearchPlan 结构化规划：解析目标商品、品类、排除条件、价格偏好和对比意图。
- 首轮工具规划：提前让 LLM 准备 `query_products` 工具调用。
- RAG 检索与核验：在 RAG 阶段执行向量检索和 LLM rerank，等待期间可穿插输出需求分析增量。
- SQLite 直查：SearchPlan 完成后按 query_text 和 direct_terms 召回兜底候选。
- 工具并发执行：同一轮多个 `query_products` 工具调用会并发执行，调试事件按完成顺序输出，回填给 LLM 的工具消息按原调用顺序排列。

因此日志中的分析、RAG、LLM、工具调用等阶段耗时不能简单相加等于总耗时，因为这些任务存在重叠。后端会记录 `parallel_enabled`、`parallel_overlap_saved_estimate`、`vector_search`、`rag_rerank`、`llm_calls`、`tool_calls` 和 `total` 等字段，用于观察并行效果。客户端断开或流程提前结束时，后台未完成任务会被统一取消，避免资源浪费。

---

## 语义表与同义词策略

项目没有把导购业务知识散落在代码里，而是集中维护在 `ecommerce_agent_dataset/search_semantics/` 和 `ecommerce_agent_dataset/attribute_ontology.json` 中。整体原则是：**LLM 负责语义规划，语义表提供稳定业务边界，代码负责执行和校验**。

`search_semantics/` 主要服务 SearchPlan 和商品召回：

| 文件 | 作用 |
| --- | --- |
| `product_concepts.json` | 商品概念同义词，例如“防晒霜”映射到防晒霜、防晒乳、防晒等 direct_terms，并配置隔离露、防晒喷雾等 fallback_terms |
| `category_aliases.json` | 将用户口语品类映射到数据库真实 `category/sub_category`，例如“跑鞋”映射到服饰运动/跑步鞋 |
| `fallback_relations.json` | 定义无直接商品时可接受的替代品和禁止类目，例如“肉松面包”可转肉松饼/糕点，但禁止推荐美妆、数码、服饰 |
| `brand_model_aliases.json` | 管理品牌/型号别名和 strict_direct 约束，避免 iPad、MacBook 等具体型号被普通平板/笔记本误判 |
| `scenario_tags.json` | 将“学习、跑步、敏感肌、早餐”等场景映射到偏好子类目和排序提示 |
| `regression_cases.json` | 固化语义表回归用例，防止扩表后破坏已有检索策略 |

`attribute_ontology.json` 主要用于品牌、属性和规格标准化：

- 统一品牌别名，例如 Nike/耐克、Apple/苹果、安热沙/anessa。
- 统一属性族，例如颜色、容量、尺码、存储、包装、口味等。
- 按类目限定可用属性范围，减少错误属性进入商品过滤。

语义表会在 SearchPlan 阶段被注入 prompt，也会在后处理阶段做确定性修正，例如补全真实品类、注入 direct_terms、合并 forbidden_categories、扩展 strict_direct 词和 fallback 优先级。这样既保留 LLM 的自然语言理解能力，又避免完全依赖模型猜测数据库字段。

---

## 搜索与召回策略

系统不是单一搜索入口，而是多路召回后统一校验。不同搜索方式的分工如下：

| 搜索方式 | 作用 | 简单说明 |
| --- | --- | --- |
| RAG 向量检索 | 召回商品知识片段 | 用于补充卖点、场景、评价和解释依据，不直接替代商品库事实 |
| SearchPlan 语义规划 | 生成结构化检索约束 | 把“再便宜点”“不要含酒精”“除了耐克”这类口语转成目标商品、排除项、价格偏好和对比意图 |
| SQLite 直查 | 快速召回真实商品候选 | 根据 `query_text`、`direct_terms`、类目和属性约束查询本地商品库，作为稳定兜底 |
| `query_products` 工具查询 | 让 LLM 发起结构化商品检索 | 适合多轮补充、类目过滤、价格/属性约束和多商品候选扩展 |
| fallback 替代搜索 | 无直接命中时寻找相邻商品 | 例如“肉松面包”无货时可转向肉松饼/糕点，同时禁止跳到错误大类 |
| 白名单回查 | 最终推荐前校验商品 | 用真实 `product_id` 合并、去重、过滤和排序，最终回复只围绕校验后的商品展开 |

---

## 当前核心能力

- 多会话管理：新建、切换、删除会话，历史消息和思考内容可恢复。
- 模型切换：Android 端可刷新 `/api/models`，一键切换服务端模型。
- 自定义模型：支持本机添加、编辑、删除 OpenAI-compatible 模型配置。
- 向量模型状态：Android 顶部通过 `/health` 展示后端 embedding 状态。
- RAG 商品知识检索：Chroma 召回商品知识片段，并支持 LLM rerank。
- SQLite 商品搜索：支持自然语言搜索、结构化搜索、商品详情和商品落地页。
- 语义表驱动搜索：通过商品概念、品类别名、fallback、品牌型号和场景标签提升召回准确性。
- SearchPlan 导购规划：识别购买阶段、追问、排除、价格偏好和对比意图。
- 目标商品白名单：最终推荐只基于校验后的目标商品。
- 多商品对比：后端生成标准 Markdown 对比表，Android 转为“推荐排序 + 商品卡片矩阵”。
- Markdown 展示：支持普通 Markdown、表格扩展和消息文本选择复制。
- 异常处理：后端不可达、模型未配置、工具参数错误、RAG 超时和流式中断均有降级或提示。

---

## 已设计的导购模式

这些模式没有单独的 UI 开关，而是由后端 SearchPlan 根据用户自然语言自动识别。

| 模式 | 示例 | 行为 |
| --- | --- | --- |
| 探索浏览 | `随便看看有没有适合跑步的耳机` | 识别为 `browsing`，轻推荐、不催买 |
| 明确购买 | `推荐一款防晒霜` | 识别为 `purchase_ready`，给出明确推荐优先级 |
| 多轮追问 | `再便宜点的呢？` | 承接上一轮品类或商品候选 |
| 价格偏好 | `方便面买哪一种更划算` | 候选排序偏向更低价或性价比 |
| 品牌排除 | `除了耐克还有什么跑步鞋` | 排除 Nike/耐克商品 |
| 成分排除 | `不要含酒精的防晒` | 排除明确含酒精商品 |
| 指定维度对比 | `对比这两款防晒，重点看价格和成分` | 对比维度包含价格、成分 |
| 替代推荐 | `肉松面包` | 无直接命中时转向相邻品类或替代商品 |

---

## 项目结构

```text
ragApp/
├── backend/                         # FastAPI 后端服务
│   ├── api/                         # API 路由层
│   │   ├── chat.py                  # 聊天、模型列表、会话和历史接口
│   │   ├── knowledge.py             # 知识库接口
│   │   └── sqlite_product_search.py # 商品搜索、详情、落地页接口
│   ├── config/
│   │   ├── settings.py              # 环境变量和服务配置
│   │   └── logging_config.py        # 日志配置
│   ├── service/
│   │   ├── llm_service.py           # LLM 调用封装
│   │   ├── rag_service.py           # Chroma / embedding / RAG 服务
│   │   ├── history_service.py       # 多会话历史存储
│   │   ├── tool_chat_service.py     # 导购工具聊天入口
│   │   ├── tool_chat/               # 流式编排、Prompt、RAG、商品选择、Trace
│   │   └── product_search/          # SQLite 搜索、语义表、query_products 工具
│   ├── .env.example                 # 后端环境变量模板
│   └── main.py                      # 后端启动入口
├── android_app/                     # Kotlin 原生 Android 应用
│   ├── app/src/main/java/com/example/agentchat/
│   │   ├── MainActivity.kt          # 主聊天页
│   │   ├── ChatAdapter.kt           # 消息、Markdown、表格和商品卡片渲染
│   │   ├── ConversationsActivity.kt # 会话列表页
│   │   └── ConfigManager.kt         # 后端地址与模型配置管理
│   └── app/src/main/res/layout/     # Android 布局文件
├── ecommerce_agent_dataset/         # 电商商品库、语义表和 Chroma 构建脚本
│   ├── ecommerce.db
│   ├── attribute_ontology.json
│   ├── build_chroma_db.py
│   └── search_semantics/
├── docs/                            # 架构、验收和变更摘要文档
├── test/                            # 单元测试、回归测试和真实流程测试
├── requirements.txt                 # Python 依赖入口
└── CHANGELOG.md
```

---

## 快速启动后端

推荐使用 Python 3.10.x。Python 依赖统一由根目录 `requirements.txt` 管理。

```bash
python3.10 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp backend/.env.example backend/.env
# 编辑 backend/.env，配置 LLM_API_KEY 或 ARK_API_KEY 等密钥
cd backend
python main.py
```

默认服务地址：

```text
http://0.0.0.0:8000
```

常用检查地址：

```text
GET http://127.0.0.1:8000/
GET http://127.0.0.1:8000/health
GET http://127.0.0.1:8000/docs
GET http://127.0.0.1:8000/api/models
GET http://127.0.0.1:8000/api/product-search/health
```

---

## 后端配置说明

配置文件模板位于 `backend/.env.example`，实际配置放在 `backend/.env`。

### LLM 配置

```bash
LLM_API_KEY=...
LLM_MODEL=ep-xxx
LLM_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
```

也可以在 `backend/config/settings.py` 的 `available_llm_models` 中声明服务端可选模型。模型项支持 `id`、`name`、`source`、`base_url` 和 `api_key_env`。Android 端通过 `/api/models` 获取模型列表。

### Embedding / RAG 配置

RAG 向量化模型由后端固定使用，Android 端只展示状态，不提供切换入口。

```bash
USE_EXTERNAL_EMBEDDING=false
USE_DOUBAO_EMBEDDING=false
EMBEDDING_MODEL=doubao-embedding-vision-251215
EMBEDDING_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
EMBEDDING_DIMENSIONS=2048
EMBEDDING_API_KEY=
EMBEDDING_API_KEY_ENV=ARK_API_KEY
```

如切换 embedding 模型或维度，需要用同一配置重建 Chroma 索引：

```bash
cd ecommerce_agent_dataset
python3 build_chroma_db.py --full-rebuild
```

### 数据库与服务配置

默认路径已指向项目内数据集：

```bash
CHROMA_PATH=../ecommerce_agent_dataset/.chroma
CHROMA_COLLECTION_NAME=product_knowledge
SQLITE_PRODUCT_DB_PATH=../ecommerce_agent_dataset/ecommerce.db
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
CORS_ORIGINS=["*"]
```

`SQLITE_PRODUCT_DB_PATH` 在代码中有默认值；如果不单独配置，会使用项目内 `ecommerce_agent_dataset/ecommerce.db`。

---

## Android 原生应用开发

1. 用 Android Studio 打开 `android_app/`。
2. 等待 Gradle 同步完成。
3. 在聊天页设置中配置后端地址，例如：

```text
http://10.0.2.2:8000      # Android 模拟器访问宿主机
http://192.168.x.x:8000   # 真机访问局域网后端
```

4. 聊天页顶部“当前模型”可刷新并切换服务端模型，也可添加本机自定义模型。
5. 聊天页顶部“向量模型状态”会显示后端 embedding 服务状态。

Android 主要依赖由 Gradle 管理，包括 OkHttp、Gson、RecyclerView、Material 和 Markwon 表格扩展。

---

## 常用接口

| 接口 | 方法 | 说明 |
| --- | --- | --- |
| `/health` | GET | 后端健康检查，包含 embedding 状态 |
| `/api/models` | GET | 获取服务端可选聊天模型 |
| `/api/chat` | POST | 非流式聊天入口 |
| `/api/chat/stream` | POST | SSE 流式导购聊天入口 |
| `/api/conversations/{user_id}` | GET/POST | 获取或创建会话 |
| `/api/conversations/{user_id}/switch/{conv_id}` | POST | 切换会话 |
| `/api/conversations/{user_id}/{conv_id}` | DELETE | 删除会话 |
| `/api/history/{user_id}` | GET/DELETE | 获取或清空历史 |
| `/api/add_knowledge` | POST | 添加知识片段到 Chroma |
| `/api/knowledge/stats` | GET | 查看知识库统计 |
| `/api/product-search/search/text` | GET | 商品自然语言搜索 |
| `/api/product-search/search` | POST | 商品结构化搜索 |
| `/api/product-search/tool/spec` | GET | 查看 `query_products` 工具定义 |
| `/api/product-search/tool/run` | POST | 调试执行商品工具 |
| `/api/product-search/products/{product_id}` | GET | 商品详情 |
| `/api/product-search/products/{product_id}/page` | GET | 商品落地页 |

---

## 测试与验证

后端核心测试：

```bash
python3 -m unittest discover -s test -p 'test_history_and_stream_safety.py'
python3 test/test_search_plan_regression.py
```

SearchPlan 真实 SQLite 链路：

```bash
python3 test/test_search_plan_regression.py --live --verbose
```

Android Debug 构建：

```bash
env GRADLE_USER_HOME=/home/fang/Documents/trae_projects/ragApp/android_app/.gradle \
  /home/fang/Documents/trae_projects/ragApp/gradle-home/gradle-8.2/bin/gradle \
  --no-daemon -Djava.net.preferIPv4Stack=true :app:assembleDebug
```

---

## 相关文档

- [项目介绍说明](docs/项目介绍说明.md)
- [对话工作流说明文档](docs/对话工作流说明文档.md)
- [系统架构与处理流程总结](docs/系统架构与处理流程总结.md)
- [验收演示视频脚本](docs/项目验收/验收演示视频脚本.md)
- [交付材料与代码规范说明](docs/项目验收/交付材料与代码规范说明.md)
- [后端服务文件查找说明](docs/后端服务文件查找说明.md)
