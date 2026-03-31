# DocFusion Copilot — 项目开发任务书

---

## 一、项目概述

**赛题**：A23 基于大语言模型的文档理解与多源数据融合系统  
**系统名称**：DocFusion Copilot（文档智融助手）  
**详细方案**：见 [A23_LLM_DocFusion_Solution.md](A23_LLM_DocFusion_Solution.md)

### 核心目标

将 docx / md / xlsx / txt 多源非结构化文档自动解析为结构化事实（Fact），存入 PostgreSQL 数据库，再根据用户上传的 Word / Excel 模板自动理解表头语义、查询事实库并回填，同时支持通过自然语言指令对文档进行操作。

### 比赛评价方式

1. 先一次性上传全部测试文档（5 docx + 3 md + 5 xlsx + 3 txt）
2. 逐个上传 5 个模板文件，系统自动填写
3. 每个模板准确率 ≥ 80%，响应时间 ≤ 90 秒
4. 比较平均准确率（差距 > 2% 看准确率，否则看响应时间）

---

## 二、已实现内容

### 2.1 后端技术栈

| 层级 | 技术 | 说明 |
|---|---|---|
| Web 框架 | **FastAPI** + uvicorn | REST API，CORS 中间件已配置 |
| 数据库 | **PostgreSQL** + SQLAlchemy 2.0 + psycopg 3 | ORM 映射 documents / document_blocks / facts 等表，启动时自动建表 |
| LLM | **OpenAI 兼容 API**（DeepSeek deepseek-chat）+ **instructor** | 结构化 JSON 输出，意图识别，字段理解，模板填充 |
| 异步任务 | **ThreadPoolExecutor**（4 workers）| 进程内线程池 |
| 配置管理 | dataclass Settings + `.env` + python-dotenv | 环境变量驱动，零硬编码 |
| 依赖注入 | ServiceContainer 单例（@lru_cache）| 13 个核心服务统一注册 |

### 2.2 后端已实现服务

| 服务 | 功能 |
|---|---|
| **DocumentService** | 文档上传（单个/批量）→ 按格式分发解析 → 生成 Block → 触发 Fact 抽取 |
| **4 种 Parser** | DocxParser / MarkdownParser / PlainTextParser / XlsxParser，结构感知切分 |
| **FactExtractionService** | 从 Block 中逐行抽取实体、字段、数值、单位、日期，含 Excel 序列号日期解析 |
| **FactService** | Fact 查询、审核、canonical 标记重算 |
| **TemplateService** | 解析 xlsx/docx 模板语义 → 字段归一化 → 查事实库 → LLM 辅助填充 → 写回文件 |
| **AgentService** | 规则 + OpenAI 双策略意图规划：识别 intent / entities / fields，支持 extract / fill / replace / query 等意图 |
| **DocumentInteractionService** | 自然语言文档操作入口：摘要生成、格式重排、内容编辑、模板回填调度 |
| **TraceService** | Fact 来源追溯（文档 → Block → 原文片段 → 置信度） |
| **BenchmarkService** | 模板填充精度评估、Fact 评测 |

### 2.3 后端 API 端点

| 路径 | 功能 |
|---|---|
| `POST /api/v1/documents/upload` | 文档上传（单个/批量） |
| `GET /api/v1/documents` | 文档列表 / 详情 / Block / Fact 查询 |
| `GET /api/v1/tasks/{task_id}` | 异步任务状态查询 |
| `POST /api/v1/templates/fill` | 模板填充提交 |
| `GET /api/v1/templates/result/{task_id}` | 下载填充结果 |
| `POST /api/v1/agent/chat` | Agent 意图规划 |
| `POST /api/v1/agent/execute` | Agent 执行（JSON / multipart） |
| `GET /api/v1/agent/artifacts/{file}` | 下载产物文件 |
| `GET /api/v1/facts/{fact_id}/trace` | Fact 追溯 |
| `POST /api/v1/benchmarks/*` | 评测接口 |

### 2.4 前端技术栈

| 层级 | 技术 | 说明 |
|---|---|---|
| 框架 | **React 19** + TypeScript 5.8 | 函数组件 + Hooks |
| 构建 | **Vite 6** | 开发热更新 + 生产构建 |
| 路由 | react-router-dom 7 | 两页面路由 |
| 样式 | **Tailwind CSS 3** + tailwindcss-animate | shadcn/ui 风格 HSL CSS 变量 |
| 组件 | **Radix UI** primitives（badge / button / card / input / scroll-area / separator / tabs / tooltip） | shadcn/ui 模式封装 |
| 状态管理 | **Zustand 5** | 单 store，管理上传文档 / 任务快照 / 模板选择 / 追溯面板 |
| 通知 | sonner | Toast 通知 |
| 图标 | lucide-react | 矢量图标 |

### 2.5 前端已实现页面

| 页面 | 功能 |
|---|---|
| **WorkspacePage（工作台）** | 三栏：左侧文档管理（文件列表 + 批量上传 + 状态指示），中栏文档预览（Block 列表 + 层级路径），右栏解析结果（事实卡片 / JSON / 文档信息 三个 Tab） |
| **AgentPage（Agent 对话）** | 聊天式对话页：左侧对话流（支持模板上传 → 模板回填 或 自然语言 → Agent 执行），右侧面板（回填结果 + 进度条 / 上下文信息 / 追溯查询 三个 Tab） |
| **AppShell（布局）** | 左侧窄边栏导航（工作台 / Agent 两个入口） |

### 2.6 关键 Bug 修复记录

| 问题 | 根因 | 修复 |
|---|---|---|
| 模板回填 fill_blanks = 0 | `_recompute_canonical_flags` 中 `WHERE year = NULL` SQL 永远为 FALSE | 改用 `.is_(None)` 做 NULL 安全比较 |
| "日期" 被误识别为实体列 | ENTITY_COLUMN_ALIASES 包含日期相关词 | 分离出 DATE_COLUMN_ALIASES |
| 空模板（仅表头）无法填充 | 旧逻辑只填已有行 | 新增 row_groups 机制，按 source_block_id 分组生成新行 |

### 2.7 测试状态

- 后端 pytest：**15 个测试全部通过**
- 前端 TypeScript 编译：**0 错误**
- 前端 Vite 构建：**成功**（403KB JS + 17KB CSS）

---

## 三、待实现需求

### 需求 1：删除已上传文件

用户在工作台可以删除已上传的文档及其关联数据（Block、Fact），释放数据库空间并删除本地文件。

### 需求 2：Agent 页面对话记忆 + 页面切换保持

- 从工作台切换到 Agent 页再切回来，聊天记录不丢失
- Agent 对话具有上下文记忆，支持多轮对话

### 需求 3：Agent 支持自然语言问答（非仅回填）

当前 Agent 页只能处理模板回填任务。用户输入"告诉我回填情况如何""帮我总结文档内容"等自然语言需求时，应能正常响应，而不是只走 template_fill 流程。需要根据意图识别结果分支处理。

### 需求 4：Agent 对话输入框自适应高度

输入较多内容时，textarea 应自动增大（设最大高度上限约 200px），避免内容被挤压在单行小框内。

### 需求 5：文档智能操作交互模块完善

赛题三大必选模块之一。需要实现基于自然语言指令的文档操作：
- 文档内容提取（提取指定实体/字段）
- 标题/段落格式调整（如"将一级标题改为黑体三号"）
- 内容编辑（如"将所有'甲方'替换为'委托方'"）
- 自动摘要
- 指定字段抽取并导出
- 结果导出为 Word / Excel / JSON

---

## 四、开发 TODO

### 4.1 后端 TODO

#### B-1：文档删除接口
- [ ] `DELETE /api/v1/documents/{doc_id}` — 删除文档及其关联 Block、Fact（级联删除）
- [ ] `DELETE /api/v1/documents/batch` — 批量删除（可选）
- [ ] Repository 层实现 `delete_document(doc_id)` 方法
- [ ] 同时删除 storage/uploads/ 中的物理文件

#### B-2：Agent 对话记忆 / 上下文管理
- [ ] 新增 `conversation_history` 存储（内存字典或数据库表），按 context_id 维护多轮对话历史
- [ ] `/agent/chat` 和 `/agent/execute` 将历史消息传入 LLM 上下文
- [ ] 超长对话自动截断或摘要压缩

#### B-3：Agent 自然语言问答分支
- [ ] AgentService 意图识别新增 `query_status` / `summarize` / `general_qa` 等意图类型
- [ ] `/agent/execute` 根据 intent 分发：fill → 模板回填，summarize → 文档摘要，query_status → 查询状态，general_qa → 通用问答
- [ ] 问答类返回纯文本 summary 而非 artifacts

#### B-4：文档智能操作完善
- [ ] DocumentInteractionService 增强 reformat：支持指定标题级别 + 字体 + 字号的格式调整
- [ ] 增强 edit：支持批量文本替换、段落增删
- [ ] 增强 extract：支持用户自然语言指定要提取的实体/字段，返回结构化结果
- [ ] 新增 export：将提取结果导出为 xlsx / docx / json 文件
- [ ] 各操作产生的 artifact 支持前端下载

---

### 4.2 前端 TODO

#### F-1：文档删除功能
- [ ] WorkspacePage 文件列表项增加删除按钮（Trash2 图标）
- [ ] 删除前弹出确认提示（防误删）
- [ ] 调用 `DELETE /api/v1/documents/{doc_id}` 接口
- [ ] 删除成功后刷新文档列表，toast 提示
- [ ] services/ 新增 `deleteDocument(docId)` 函数

#### F-2：Agent 页对话记忆 + 页面切换保持
- [ ] 将 AgentPage 的 messages 状态提升到 Zustand uiStore 中
- [ ] 页面切换时不销毁对话状态
- [ ] 增加"清空对话"按钮

#### F-3：Agent 自然语言问答
- [ ] 修改 handleSend 逻辑：无 templateFile 时走 runAgentExecute，有 templateFile 时先判断意图再决定走回填还是 execute
- [ ] Agent 返回的纯文本 summary 直接作为 assistant 消息展示
- [ ] 支持 summarize / query / general_qa 类响应的展示

#### F-4：Agent 对话输入框自适应高度
- [ ] textarea 监听 input 事件，根据 scrollHeight 自动调整高度
- [ ] min-height: 40px，max-height: 200px
- [ ] 超过上限时显示滚动条

#### F-5：文档智能操作交互模块 UI
- [ ] Agent 页支持文档操作类指令的展示（格式调整、文本替换、摘要等）
- [ ] 操作结果以卡片形式展示（如"已将 12 处'甲方'替换为'委托方'"）
- [ ] artifact 下载按钮对文档操作产物生效
- [ ] 可选：WorkspacePage 右侧新增"文档操作"Tab

---

## 五、优先级排序

| 优先级 | 任务 | 原因 |
|---|---|---|
| **P0** | B-1 + F-1 文档删除 | 基础功能缺失，影响日常使用 |
| **P0** | F-4 输入框自适应 | 体验问题，改动小，立即见效 |
| **P1** | B-3 + F-3 Agent 问答分支 | 赛题核心要求，当前 Agent 只能回填 |
| **P1** | B-4 + F-5 文档智能操作 | 赛题三大必选模块之一 |
| **P2** | B-2 + F-2 对话记忆 | 体验优化，非评分关键 |
