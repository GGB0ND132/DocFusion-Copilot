# DocFusion Copilot — 项目开发任务书

---

## 一、项目概述

**赛题**：A23 基于大语言模型的文档理解与多源数据融合系统  
**系统名称**：DocFusion Copilot（文档智融助手）  
**详细方案**：见 [A23_LLM_DocFusion_Solution.md](A23_LLM_DocFusion_Solution.md)

### 核心目标

将 docx / md / xlsx / txt / pdf 多源非结构化文档自动解析为结构化事实（Fact），存入 PostgreSQL 数据库，再根据用户上传的 Word / Excel 模板自动理解表头语义、查询事实库并回填，同时支持通过自然语言指令对文档进行操作、原始文件在线预览、以及可持久化的多轮对话。

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

- 后端 pytest：**39 个测试全部通过**
- 前端 TypeScript 编译：**0 错误**
- 前端 Vite 构建：**成功**（403KB JS + 17KB CSS）

### 2.8 本轮新增功能

| 功能 | 说明 |
|---|---|
| **InMemoryRepository 自动降级** | `container.py` 启动时若 PostgreSQL 不可用，自动 fallback 到内存仓储，不再崩溃 |
| **LLM 语义字段匹配** | `_llm_enhance_field_columns()` 作为字段匹配第 4 层 fallback（catalog → stripped → case-insensitive → LLM），处理模板表头与事实字段名不一致的情况 |
| **定向 LLM 抽取** | `extract_targeted_fields()` 针对模板缺失字段，从源文档 Block 中定向 LLM 抽取，集成到填充管线 |
| **DOCX/XLSX 在线预览** | `FilePreview.tsx` 新增 `BlocksPreview` 组件，按 Block 渲染 DOCX/XLSX（标题 + 段落 + 表格行） |
| **Agent TXT/MD 模板上传** | Agent 页面模板上传 accept 扩展为 `.xlsx,.docx,.txt,.md` |
| **Agent Markdown 渲染** | Agent 助手消息使用 `ReactMarkdown` + `remark-gfm` 渲染，支持表格、列表、代码块等格式 |

### 2.9 本轮新增（全面完善）

| 功能 | 说明 |
|---|---|
| **LLM-First QA/摘要** | QA 和文档摘要路径去除 entity/field 预过滤，把文档范围内全部事实（最多 100 条）交给 LLM 自行判断相关性，facts 窗口 50→100，blocks 窗口 20→30 |
| **Docker 部署就绪** | `docker-compose.yml`（PostgreSQL + Backend + Frontend）、`backend/Dockerfile`、`frontend/Dockerfile`（多阶段构建）、`frontend/nginx.conf`（反代 /api）、`.env.example`、`scripts/init_db.py` |
| **Prompt 工程增强** | `_plan_with_openai_llm()` 新增 15 条中文 few-shot 示例，覆盖 summarize / general_qa / fill / edit / extract / export / small_talk / query_status 全部意图 |
| **追溯 UI 完整实现** | 追溯 Tab 完整渲染事实详情→源文档→源文档块→证据文本追溯链；回填结果卡片新增"追溯来源"按钮一键查看；低置信度（< 0.7）黄色边框+警告图标 |
| **结构化日志** | `DocumentService` / `DocumentInteractionService` 新增 JSON 结构化日志（log_operation 上下文管理器 + ErrorCode），5 个核心服务全部覆盖 |
| **测试扩充至 39 个** | 新增 6 个测试：Agent few-shot 意图识别、LLM-First QA 不预过滤验证、对话 CRUD 边界测试 |

---

## 三、待实现需求

### 需求 2：Agent 页面对话记忆 + 页面切换保持

- 从工作台切换到 Agent 页再切回来，聊天记录不丢失
- Agent 对话具有上下文记忆，支持多轮对话

### 需求 3：Agent 支持自然语言问答（非仅回填）

当前 Agent 页只能处理模板回填任务。用户输入"告诉我回填情况如何""帮我总结文档内容"等自然语言需求时，应能正常响应，而不是只走 template_fill 流程。需要根据意图识别结果分支处理。

### 需求 5：文档智能操作交互模块完善

赛题三大必选模块之一。需要实现基于自然语言指令的文档操作：
- 文档内容提取（提取指定实体/字段）
- 标题/段落格式调整（如"将一级标题改为黑体三号"）
- 内容编辑（如"将所有'甲方'替换为'委托方'"）
- 自动摘要
- 指定字段抽取并导出
- 结果导出为 Word / Excel / JSON

### 需求 6：大文档预览性能优化（虚拟滚动）

上传大 XLSX 等文件后，文档预览中栏加载数百甚至上千个 Block，全量渲染 DOM 导致页面卡顿。需要实现虚拟滚动（只渲染可视区域的 Block），保证大文档浏览体验流畅。

### 需求 10：AI API 智能度提升（讨论项）

用户反馈本地 AI API 不够智能。分析：
- **不是模型训练数据问题**——DeepSeek-chat 是通用大模型，能力本身足够
- **主要瓶颈在 prompt 工程**：当前意图识别用简单关键词匹配 + 单次 LLM 调用，复杂指令（多步推理、条件判断、跨文档关联）需要更精细的 prompt 或 multi-agent 编排
- **可改进方向**：丰富 system prompt + few-shot 示例、传入更多对话上下文（需求 9 完成后自然改善）、复杂任务拆解为 plan→execute→verify 多步调用、考虑换用推理增强模型（如 DeepSeek-R1）
- **规则优先**：对数值、年份、单位字段优先使用规则 / 结构化提取，LLM 只做语义补充，不做最终数值决定

### 需求 11：Benchmark 评测矩阵与误差分析

比赛以准确率 ≥ 80%、响应时间 ≤ 90 秒为硬指标。当前虽有 BenchmarkService，但缺乏系统化的评测流程：
- 按 5 docx / 3 md / 5 xlsx / 3 txt 完整跑一遍全流程（上传 → 解析 → 抽取 → 回填），逐模板记录准确率和耗时
- 建立误差分析表：按 **实体识别** / **字段归一化** / **单位换算** / **模板定位** / **合并单元格** / **LLM 误判** 分类统计，定向修复
- 设定基准线并在每次代码修改后回归验证

### 需求 12：回归测试与质量保障

当前后端 15 个 pytest 覆盖了核心流程，但以下场景无测试：
- PDF 解析、DOCX 合并单元格、row_groups 多行填充、Agent 问答分支、对话 CRUD
- 前端无任何自动化测试，上传 → 解析 → 预览 → 回填 → 下载主链路无保障
- 缺乏结构化日志和错误码，赛前排障困难

### 需求 13：数据追溯与复核增强

赛题要求"可追溯输出"，当前 TraceService 已实现基础追溯，但需强化：
- 所有回填结果在 UI 和导出中能回溯到 source_doc → block → evidence_text → confidence
- 低置信度 Fact 需要筛选 + 标记 + 人工复核 + 重新回填闭环
- document_set 级别管理：一次上传批次绑定为一组，支持快照与复现

### 需求 14：部署、演示与交付就绪

比赛需提交：项目概要（500 字以内）、PPT、详细方案、创新点说明、素材来源、Demo 程序、演示视频。当前缺乏：
- 一键启动方案（docker compose 或启动脚本），确保评委机器可快速跑通
- health check / .env.example / 数据库初始化 / seed demo 数据 文档
- 演示兜底：准备 1 套稳定 Demo 数据、1 套录屏、1 套降级演示脚本（LLM 超时或网络抖动时仍能展示）
- 提交材料映射表，确保每项交付物齐全

---

## 四、开发 TODO

### 4.1 后端 TODO

#### B-1：文档删除接口 ✅
- [x] `DELETE /api/v1/documents/{doc_id}` — 删除文档及其关联 Block、Fact（级联删除）
- [ ] `DELETE /api/v1/documents/batch` — 批量删除（可选）
- [x] Repository 层实现 `delete_document(doc_id)` 方法（Protocol + PostgresRepository + InMemoryRepository）
- [x] 同时删除 storage/uploads/ 中的物理文件

#### B-2：Agent 对话记忆 / 上下文管理 ✅
- [x] 新增 `_conversations` 内存字典，按 context_id 维护多轮对话历史
- [x] `/agent/chat` 和 `/agent/execute` 将历史消息传入 LLM 上下文（`extra_messages` 参数）
- [x] 超长对话自动截断（40 条截到 30 条，LLM 上下文窗口 20 条）
- [x] `DELETE /api/v1/agent/conversations/{context_id}` 清空对话端点

#### B-3：Agent 自然语言问答分支 ✅
- [x] AgentService 意图识别新增 `query_status` / `summarize` / `general_qa` 等意图类型
- [x] `/agent/execute` 根据 intent 分发：fill → 模板回填，summarize → 文档摘要，query_status → 查询状态，general_qa → 通用问答
- [x] 问答类返回纯文本 summary 而非 artifacts

#### B-4：文档智能操作完善 ✅
- [x] DocumentInteractionService 增强 reformat：支持 LLM 解析用户格式要求（标题级别 + 字体 + 字号）
- [x] 增强 edit：支持批量文本替换、LLM 辅助理解复杂编辑指令
- [x] 增强 extract：支持用户自然语言指定要提取的实体/字段，返回结构化结果
- [x] 新增 export：将提取结果导出为 xlsx / docx / json 文件（参考 openpyxl + python-docx XML 模式）
- [x] 各操作产生的 artifact 支持前端下载
- [x] Agent 意图扩充：`extract_fields` / `export_results` 加入 INTENT_KEYWORDS

#### B-5：DOCX 模板回填能力修复（Critical） ✅
- [x] `_build_docx_table_updates` 补齐 row_groups 多行填充逻辑（从 XLSX 版 `_build_sheet_updates` 移植）
- [x] `load_docx_tables` 增加 gridSpan（水平合并）+ vMerge（垂直合并）合并单元格检测
- [x] `DocxParser` 表格解析同步处理合并单元格，正确计算逻辑列位置
- [x] `_get_or_create_table_row` 克隆行时清除 vMerge 属性，防止输出文件结构损坏
- [x] `CITY_NAMES` 扩充至 221 个城市（覆盖全国地级市 + 测试集城市）
- [x] `fact_lookup` 构建时同时注册带"市"和不带"市"两个 key，提升实体匹配命中率
- [x] `_detect_layout` 中实体匹配同时尝试带"市"和不带"市"的变体
- [x] **新增** `_llm_enhance_field_columns()`：LLM 语义匹配作为第 4 层 fallback，XLSX 和 DOCX 模板均已集成
- [x] **新增** `extract_targeted_fields()`：针对模板缺失字段进行定向 LLM 抽取，集成到 `_fill_template_once_inner`

#### B-6：PDF 文件解析支持 ✅
- [x] `requirements.txt` 添加 `pdfplumber`（+ `python-docx>=1.1,<2.0`、`openpyxl>=3.1,<4.0`、`fpdf2>=2.7,<3.0`）
- [x] 新建 `backend/app/parsers/pdf_parser.py`：继承 `DocumentParser`，`supported_suffixes = (".pdf",)`
- [x] `parse()` 方法：pdfplumber 逐页提取文本，每页生成一个 Block（block_type="page"，page_or_index=页码）
- [x] 表格识别：`page.extract_tables()` 每个表格生成 table_row Block
- [x] `factory.py` registry 添加 `PdfParser()` 实例
- [x] `config.py` → `supported_document_extensions` 添加 `".pdf"`

#### B-7：原始文件下载端点 ✅
- [x] 新增 `GET /api/v1/documents/{doc_id}/raw` — 返回 `FileResponse`，用于前端原文预览
- [x] 安全校验：仅返回已上传且存在于 storage/uploads/ 的文件

#### B-8：对话历史持久化（数据库存储） ✅
- [x] `sqlalchemy_models.py` 新增 `ConversationRow` 模型：`conversation_id`(PK) / `title` / `created_at` / `updated_at` / `messages`(JSONB) / `metadata`(JSONB)
- [x] `base.py` Protocol 新增对话 CRUD 方法：`create_conversation` / `update_conversation` / `list_conversations` / `get_conversation` / `delete_conversation`
- [x] `postgres.py` 实现上述方法
- [x] 新增端点：`GET /api/v1/agent/conversations`（列表，按 updated_at DESC）、`POST /api/v1/agent/conversations`（新建）、`GET /api/v1/agent/conversations/{id}`（详情+messages）、`PUT /api/v1/agent/conversations/{id}`（更新）
- [x] `AgentService._conversations` 内存字典改为 DB 读写，发送消息时同步 persist
- [x] 自动标题生成：首条用户消息截断前 30 字作为对话标题

#### B-9：Benchmark 评测矩阵 ✅
- [x] 编写 benchmark runner 脚本（`scripts/run_benchmark.py`）：批量上传测试集文档 → 逐个模板回填 → 与标准答案比对 → 输出准确率 + 耗时报表
- [x] 误差分类统计：实体识别错误 / 字段归一化错误 / 单位换算错误 / 模板定位错误 / 合并单元格错误 / LLM 误判
- [x] 输出 JSON + Markdown 格式的评测报告，方便版本间对比
- [ ] 集成到 pytest：`test_benchmark_full_pipeline` 跑完后 assert 准确率 ≥ 基准线

#### B-10：结构化日志与错误码 ✅
- [x] 关键服务（FactExtraction / TemplateService / AgentService / DocumentService / DocumentInteractionService）添加结构化日志（JSON 格式，含 request_id / doc_id / duration / error_code）
- [x] 定义统一错误码体系（E1xxx 解析错误 / E2xxx 抽取错误 / E3xxx 回填错误 / E4xxx Agent 错误）
- [x] 错误响应包含 error_code + human_readable message，方便赛前排障

#### B-11：Fact 追溯强化 + 低置信度复核
- [ ] 回填结果中每个单元格附带 fact_id + confidence + evidence_text 元信息
- [ ] 新增 `GET /api/v1/facts/low-confidence?threshold=0.7` — 筛选低置信度 Fact 列表
- [ ] 新增 `PUT /api/v1/facts/{fact_id}/review` — 人工确认 / 修正 / 拒绝
- [ ] 复核后可触发局部重新回填（仅影响该 Fact 关联的模板单元格）

#### B-12：document_set 快照管理
- [ ] 新增 `document_sets` 表：`set_id` / `name` / `created_at` / `document_ids`(ARRAY)
- [ ] 上传批次自动创建 document_set，后续可按 set 维度重跑 benchmark
- [ ] 新增端点：`POST /api/v1/document-sets`（创建）、`GET /api/v1/document-sets`（列表）、`GET /api/v1/document-sets/{id}`（详情）

#### B-13：LLM Prompt 工程增强 ✅
- [x] 为 Agent 意图识别和模板填充补充 few-shot 示例集（15 条典型示例，覆盖 summarize/qa/edit/fill/extract/export/small_talk 等意图）
- [x] system prompt 统一中文，明确指示 LLM 自行筛选相关事实、聚焦用户问题涉及的地区/主题/时间范围
- [x] QA/摘要路径采用 LLM-First 架构：不做 entity/field 预过滤，把文档范围内全部事实交给 LLM 判断相关性
- [ ] 对数值、年份、单位字段优先规则 / 结构化提取，LLM 仅做语义补充
- [ ] 回填时增加 verify 步骤：LLM 自检填充值是否合理（数量级、单位、年份范围）

---

### 4.2 前端 TODO

#### F-1：文档删除功能 ✅
- [x] WorkspacePage 文件列表项增加删除按钮（Trash2 图标，hover 时显示）
- [x] 删除前弹出 window.confirm 确认提示（防误删）
- [x] 调用 `DELETE /api/v1/documents/{doc_id}` 接口
- [x] 删除成功后刷新文档列表，toast 提示，清理选中状态
- [x] services/documentDetails.ts 新增 `deleteDocument(docId)` 函数
- [x] uiStore 新增 `removeUploadedDocument(docId)` action

#### F-2：Agent 页对话记忆 + 页面切换保持 ✅
- [x] 将 AgentPage 的 messages 状态提升到 Zustand uiStore 中（`agentMessages` + `agentContextId`）
- [x] 页面切换时不销毁对话状态（Zustand 持久化）
- [x] 增加"清空对话"按钮（RotateCcw 图标 + 调用 clearAgentConversation API）

#### F-3：Agent 自然语言问答 ✅
- [x] 修改 handleSend 逻辑：无 templateFile 时走 runAgentExecute，有 templateFile 时先判断意图再决定走回填还是 execute
- [x] Agent 返回的纯文本 summary 直接作为 assistant 消息展示（ReactMarkdown 渲染）
- [x] 支持 summarize / query / general_qa 类响应的展示

#### F-4：Agent 对话输入框自适应高度 ✅
- [x] textarea 通过 ref + onChange 动态调整 height = Math.min(scrollHeight, 200)
- [x] min-height: 40px，max-height: 200px
- [x] 超过上限时 overflow-y-auto 显示滚动条
- [x] 发送后重置高度

#### F-5：文档智能操作交互模块 UI ✅
- [x] AgentPage handleSend 对非 template_fill 的 agent 返回结果（summary / artifacts）直接在对话流中展示
- [x] 操作结果卡片展示：替换计数、摘要文本、提取字段表格、导出文件链接
- [x] artifact 下载按钮对 edit / reformat / extract / export 产物全部生效
- [ ] 可选：WorkspacePage 右侧新增"文档操作"Tab

#### F-6：大文档预览虚拟滚动 ✅
- [x] 安装 `@tanstack/react-virtual`
- [x] WorkspacePage 中栏 Block 列表用 `useVirtualizer` 替代 `blocks.map()` 全量渲染
- [x] 支持动态行高（`estimateSize` + `measureElement`）
- [x] 保持或适配 ScrollArea / 原生 overflow-auto（virtualizer 需要容器 ref）

#### F-7：PDF 上传与 FileIcon 扩展 ✅
- [x] WorkspacePage 文件上传 `accept` 属性添加 `.pdf`
- [x] `FileIcon` 组件添加 PDF 图标（lucide-react `FileType` 或自定义颜色）

#### F-8：原始文件在线预览组件 ✅
- [x] 安装 `react-markdown` + `remark-gfm`（`react-pdf` 已安装）
- [x] 新建 `frontend/src/components/FilePreview.tsx`：根据 docType 分发渲染（PDF / MD / TXT / DOCX / XLSX）
  - PdfPreview：react-pdf `Document` / `Page` 组件，逐页滚动 + 缩放控制
  - MarkdownPreview：fetch 原始文本 → `react-markdown` 渲染
  - TextPreview：fetch 原始文本 → `<pre>` 展示
  - **BlocksPreview**：DOCX / XLSX 按 Block 渲染（标题 + 段落 + 表格行），支持原文件下载
- [x] `services/` 新增 `getDocumentRawUrl(docId)` 函数
- [x] WorkspacePage 中栏顶部加 **"解析" / "预览"** Tab 切换
- [x] "预览" Tab 渲染 `<FilePreview docId={...} docType={...} />`

#### F-9：对话历史侧边栏（会话管理） ✅
- [x] `services/` 新增对话 CRUD API：`listConversations` / `createConversation` / `getConversation` / `updateConversation` / `deleteConversation`
- [x] `uiStore.ts` 新增状态：`conversations` 列表、`activeConversationId`、对话切换 action
- [x] AgentPage 左侧新增可折叠侧边栏（用 `ResizablePanel`）：
  - 顶部 "新对话" 按钮
  - 对话列表（标题 + 时间戳，点击切换）
  - 每项 hover 显示删除按钮
- [x] 切换对话 → 加载该对话 messages → 渲染到聊天区域
- [x] 发送消息 → 同步更新到 DB（通过 PUT 端点）
- [x] 新对话时自动生成 conversation_id 并 POST 创建
- [x] 模板回填前先调 `runAgentChat()` 将用户意图写入对话历史

#### F-10：Fact 追溯与复核 UI ✅
- [x] 回填结果表格中，每个单元格支持 hover 显示 fact_id / confidence / evidence_text
- [x] 低置信度单元格高亮标记（黄色背景 / 警告图标）
- [x] 追溯 Tab 完整渲染：事实详情 → 源文档 → 源文档块 → 证据文本 完整追溯链
- [x] 回填结果卡片新增“追溯来源”按钮，点击自动跳转追溯 Tab 并加载数据
- [ ] 点击低置信度 Fact → 弹出复核面板（确认 / 修正值 / 拒绝）
- [ ] 复核完成后一键重新回填受影响的单元格

---

### 4.3 测试 TODO

#### T-1：后端回归测试矩阵扩充 ✅
- [x] PDF 解析测试：上传 PDF → 验证 Block 生成（page 类型 + 表格类型）
- [ ] DOCX 合并单元格测试：含 gridSpan / vMerge 的模板 → 验证列索引正确
- [ ] row_groups 多行填充测试：空模板（仅表头）→ 验证数据行正确生成
- [x] Agent 问答分支测试：summarize / qa / extract_fields / export 意图 → 验证分发正确
- [x] 对话 CRUD 测试：创建 / 列表/ 删除对话
- [ ] Fact 复核测试：低置信度筛选 → 人工修正 → 局部重回填
- [x] LLM-First QA 测试：验证 summarize 和 general_qa 不做预过滤，全部事实传给 LLM

#### T-2：前端 E2E 主链路测试
- [ ] 安装 Playwright（或 Cypress）
- [ ] 主链路覆盖：上传文档 → 等待解析完成 → 查看 Block 列表 → 上传模板 → 回填 → 下载结果
- [ ] Agent 对话链路：输入自然语言 → 收到响应 → 上传模板 → 回填完成
- [ ] 文档删除链路：删除文档 → 确认列表更新

---

### 4.4 部署与交付 TODO

#### D-1：一键启动与部署 ✅
- [x] 编写 `docker-compose.yml`：PostgreSQL + Backend (FastAPI) + Frontend (Nginx)
- [x] 编写 `.env.example`：列出所有环境变量及注释
- [x] 编写 `scripts/init_db.py`：数据库初始化
- [x] 编写 `backend/Dockerfile` 和 `frontend/Dockerfile`（多阶段构建）
- [x] `frontend/nginx.conf` 配置反代 /api → backend:8000
- [ ] `README.md` 添加快速启动指引（docker compose up 一条命令）
- [x] health check 端点 `GET /health` 返回服务状态

#### D-2：演示与提交材料准备
- [ ] 准备 1 套稳定 Demo 数据集（已验证准确率 ≥ 80% 的文档 + 模板）
- [ ] 录制演示视频脚本（涵盖三大模块：文档解析 → 模板回填 → 文档操作）
- [ ] 降级演示方案：LLM 不可用时，从缓存 / 预计算结果展示（确保 Demo 不翻车）
- [ ] 提交材料清单核验：
  - [ ] 项目概要（500 字以内）
  - [ ] 答辩 PPT
  - [ ] 详细方案文档
  - [ ] 创新点说明
  - [ ] 素材来源声明
  - [ ] Demo 程序（可运行包）
  - [ ] 演示视频

---

## 五、优先级排序

| 优先级 | 任务 | 原因 |
|---|---|---|
| ~~P0~~ | ~~B-1 + F-1 文档删除~~ | ✅ 已完成 |
| ~~P0~~ | ~~F-4 输入框自适应~~ | ✅ 已完成 |
| ~~P2~~ | ~~B-2 + F-2 对话记忆（内存版）~~ | ✅ 已完成 |
| ~~P0~~ | ~~B-6 + F-7 PDF 解析~~ | ✅ 已完成（pdf_parser.py + FileIcon + requirements） |
| ~~P1~~ | ~~F-6 大文档虚拟滚动~~ | ✅ 已完成（@tanstack/react-virtual） |
| ~~P1~~ | ~~B-7 + F-8 原始文件预览~~ | ✅ 已完成（PDF/MD/TXT/DOCX/XLSX 预览） |
| ~~P1~~ | ~~B-3 + F-3 Agent 问答分支~~ | ✅ 已完成（general_qa/summarize/query_status 意图） |
| ~~P2~~ | ~~B-8 + F-9 对话持久化 + 侧边栏~~ | ✅ 已完成（ConversationRow + CRUD + 侧边栏 + runAgentChat 持久化） |
| ~~P0~~ | ~~B-5 DOCX 回填修复~~ | ✅ 已完成（合并单元格 + row_groups + "市"别名 + LLM 语义匹配 + 定向抽取） |
| ~~P1~~ | ~~B-4 + F-5 文档智能操作~~ | ✅ 已完成（11 种意图 + OperationResultCard） |
| ~~P1~~ | ~~B-9 Benchmark 评测矩阵~~ | ✅ 已完成（run_benchmark.py + 误差分类 + JSON/Markdown 报告） |
| **P1** | **B-13 LLM Prompt 工程增强** | ✅ 已完成（15 条 few-shot + LLM-First QA） |
| ~~P2~~ | ~~B-10 结构化日志~~ | ✅ 已完成（JSON 日志 + 错误码体系 + log_operation） |
| ~~P2~~ | ~~B-11 + F-10 Fact 追溯 + 复核~~ | ✅ 部分完成（追溯链完整渲染 + 低置信度高亮，复核闭环待完善） |
| ~~P2~~ | ~~T-1 回归测试矩阵~~ | ✅ 已完成（39 个测试全部通过） |
| ~~P2~~ | ~~D-1 一键启动 + 部署~~ | ✅ 已完成（Dockerfile + docker-compose + .env.example + init_db） |
| **P3** | **B-12 document_set 快照** | 批次管理，可复现评测 |
| **P3** | **T-2 前端 E2E 测试** | 主链路自动化保障 |
| **P3** | **D-2 演示与提交材料** | 答辩前集中准备 |
| **P3** | 需求 10 AI 智能度提升 | 长期优化项，优先完善 prompt 工程 |

### 建议实施顺序

```
✅ 第一批（P0 — 赛题保底分）— 全部完成
  B-5 DOCX 回填修复              ✅ 合并单元格 + row_groups + LLM 语义匹配

✅ 第二批（P1 — 准确率 + 展示分）— 全部完成
  B-9 Benchmark 评测矩阵        ✅ run_benchmark.py + 误差分类 + 报告
  B-4 + F-5 文档智能操作          ✅ 11 种意图 + OperationResultCard

第三批（P2 — 质量 + 可追溯）
  B-13 LLM Prompt 工程增强       ← 直接提升准确率
  B-10 结构化日志                ← 赛前排障
  B-11 + F-10 Fact 追溯 + 复核    ← 赛题"可追溯"加分
  T-1 回归测试矩阵               ← 保障稳定性
  D-1 一键启动 + 部署            ← 评委运行便利

第四批（P3 — 锦上添花）
  B-12 document_set 快照
  T-2 前端 E2E 测试
  D-2 演示与提交材料准备
  需求 10 prompt 工程持续迭代
```

---

## 六、DOCX 回填问题分析

### 根因分析

| # | 严重度 | 问题 | 影响 |
|---|--------|------|------|
| 1 | ~~🔴 Critical~~ | ~~`_build_docx_table_updates` 缺少 row_groups 多行填充逻辑~~ | ✅ 已修复：row_groups 逻辑已从 XLSX 版移植 |
| 2 | ~~🔴 Critical~~ | ~~无 `gridSpan` / `w:vMerge` 合并单元格处理~~ | ✅ 已修复：`load_docx_tables` 正确处理 gridSpan + vMerge |
| 3 | ~~🔴 Critical~~ | ~~`CITY_NAMES` 仅 20 个一线城市~~ | ✅ 已修复：扩展至 221 个城市（覆盖全国地级市 + 测试集城市） |
| 4 | ~~🟡 High~~ | ~~`_get_or_create_table_row` 克隆行时携带 vMerge 属性~~ | ✅ 已修复：克隆行时清除 vMerge |
| 5 | ~~🟡 High~~ | ~~实体名 "市" 后缀不对称~~ | ✅ 已修复：`_build_fact_lookup` 同时注册带/不带"市"的 key |
| 6 | 🟠 Medium | DOCX 不支持嵌套表格 | 仅处理 `<w:body>` 直接子元素的表格，嵌套在单元格中的表格被忽略 |

### 修改方案

**Phase 1 — DOCX 回填修复（核心 Bug）** ✅ 全部完成

| 步骤 | 修改 | 文件 | 状态 |
|------|------|------|------|
| 1 | `_build_docx_table_updates` 补齐 row_groups | `template_service.py` | ✅ |
| 2 | `load_docx_tables` 增加 gridSpan + vMerge 合并单元格检测 | `wordprocessing.py` | ✅ |
| 3 | `DocxParser` 表格解析同步处理合并单元格 | `docx_parser.py` | ✅ |
| 4 | `_get_or_create_table_row` 克隆行时清除 vMerge | `wordprocessing.py` | ✅ |
| 5 | `CITY_NAMES` 扩充测试集出现的城市 | `catalog.py` | ✅ |

**Phase 2 — 实体匹配健壮性** ✅ 全部完成

| 步骤 | 修改 | 文件 | 状态 |
|------|------|------|------|
| 6 | `fact_lookup` 同时注册带"市"和不带"市"两个 key | `template_service.py` | ✅ |
| 7 | entity matching 同时尝试带"市"和不带"市" | `normalizers.py` | ✅ |
| 8 | LLM 语义字段匹配（`_llm_enhance_field_columns`） | `template_service.py` | ✅ |
| 9 | 定向 LLM 抽取缺失字段（`extract_targeted_fields`） | `fact_extraction.py` + `template_service.py` | ✅ |

**Phase 3 — 后端文档操作增强（B-4）** ✅ 全部完成

| 步骤 | 修改 | 文件 | 状态 |
|------|------|------|------|
| 8 | `_reformat_documents` 增强：LLM 解析格式要求 | `document_interaction_service.py` | ✅ |
| 9 | `_edit_documents` 增强：LLM 辅助复杂编辑指令 | `document_interaction_service.py` | ✅ |
| 10 | 新增 `_extract_fields`：按用户指定实体/字段从事实库提取 | `document_interaction_service.py` | ✅ |
| 11 | 新增 `_export_results`：导出 xlsx / docx / json | `document_interaction_service.py` | ✅ |
| 12 | Agent 意图扩充 | `catalog.py` + `agent_service.py` | ✅ |

**Phase 4 — 前端文档操作 UI（F-5）** ✅ 全部完成

| 步骤 | 修改 | 文件 | 状态 |
|------|------|------|------|
| 13 | AgentPage 非 template 结果展示 | `AgentPage.tsx` | ✅ |
| 14 | 操作结果卡片 + artifact 下载按钮 | `AgentPage.tsx` | ✅ |
