# DocFusion Copilot

> A23 赛题：基于大语言模型的文档理解与多源数据融合系统

将 docx / md / xlsx / txt / pdf 多源非结构化文档自动解析为结构化事实，存入数据库，再根据用户上传的 Word / Excel 模板自动理解表头语义、查询事实库并回填，同时支持自然语言指令交互操作文档。

## 核心架构

系统采用**"先建库、再秒填"**的两阶段设计，针对比赛流程优化：

| 阶段 | 处理内容 |
|---|---|
| **阶段一：文档预处理** | 上传文档 → 结构解析 → 实体/字段抽取 → 单位归一化 → 多源融合 → 建立索引 |
| **阶段二：模板快速填充** | 理解模板表头语义 → 查询事实库 → 自动回填 → 输出结果与追溯日志 |

核心策略为**"规则约束 + LLM 理解 + 可追溯结构化存储"**的混合方案，LLM 负责语义理解，规则负责精确落地。

## 技术栈

| 层级 | 技术 |
|---|---|
| 后端 | Python 3.11+ · FastAPI · SQLAlchemy 2.0 · PostgreSQL · psycopg 3 |
| 文档解析 | python-docx XML · pdfplumber · openpyxl · Markdown |
| LLM | OpenAI 兼容 API（DeepSeek 等）· instructor 结构化输出 |
| 前端 | React 19 · TypeScript 5.8 · Vite 6 · Tailwind CSS 3 · shadcn/ui · Zustand 5 |
| 异步 | ThreadPoolExecutor（进程内线程池） |

## 功能清单

### 文档处理
- **多格式解析**：docx / md / txt / xlsx / pdf 五种格式，结构感知切分为 Block
- **PDF 表格识别**：逐页提取文本块（page）和表格行块（table_row）
- **DOCX 合并单元格**：正确处理 gridSpan / vMerge 合并单元格
- **事实抽取**：从 Block 中提取实体、字段、数值、单位、年份，自动去重与冲突合并

### 模板回填
- **双格式模板**：xlsx 和 docx 模板均支持
- **四层字段匹配**：catalog 别名表 → 去括号/空格 → 大小写忽略 → **LLM 语义匹配**（`_llm_enhance_field_columns`），逐层 fallback 确保高命中
- **定向 LLM 抽取**：模板缺失字段时，自动调用 `extract_targeted_fields` 从源文档定向补抽
- **自动关联文档**：auto_match 按文档集自动筛选关联文档
- **多行填充**：row_groups 模式支持只有表头的空模板自动生成数据行

### Agent 自然语言交互
- **意图识别**：规则 + OpenAI 双策略，支持 10 种意图（extract_facts / extract_and_fill_template / edit_document / reformat_document / summarize_document / query_status / general_qa / extract_fields / export_results 等）
- **最长关键词匹配**：避免意图歧义
- **文档编辑**：自然语言驱动的查找替换 + LLM 复杂编辑
- **格式整理**：标题层级、字号、字体的 LLM 解析与应用
- **字段提取**：按实体/字段过滤并输出结构化 JSON
- **数据导出**：导出事实库为 JSON / XLSX
- **智能问答**：基于事实库的 QA + 系统状态查询 + 文档摘要

### 对话管理
- **会话持久化**：对话记录存储到仓储，支持 CRUD
- **自动标题**：根据首条用户消息自动生成对话标题
- **历史上下文**：LLM 请求携带最近 20 条消息上下文

### 质量保障
- **来源追溯**：每条事实追溯到原文档 → 原段落 → 原文片段 → 置信度
- **低置信度高亮**：confidence < 0.7 的填充结果在 UI 中高亮警示
- **人工复核**：支持事实状态修正（confirmed / rejected / reviewed）
- **Benchmark 评测**：事实评估 + 模板回填评测 + 错误分类 + Markdown 报告

### 工程化
- **无数据库降级**：PostgreSQL 不可用时自动 fallback 到 InMemoryRepository，系统可完整运行
- **结构化日志**：JSON 格式 + 统一错误码（E1xxx~E4xxx）+ 操作计时
- **Prompt 工程**：9 个 few-shot 示例 + 匹配原则 + 验证步骤

## 目录结构

```
├── backend/                    # FastAPI 后端
│   ├── app/
│   │   ├── api/v1/endpoints/   # REST 端点（documents / tasks / templates / agent / facts / benchmarks）
│   │   ├── core/               # config / container / openai_client / catalog / logging
│   │   ├── models/             # 领域模型（Document / Block / Fact / Task / Conversation / TemplateResult）
│   │   ├── parsers/            # 5 种文档解析器（docx / md / txt / xlsx / pdf）
│   │   ├── repositories/       # Repository 协议 + InMemory / PostgreSQL 实现
│   │   ├── schemas/            # Pydantic 请求/响应模型
│   │   ├── services/           # 业务服务（document / fact / template / agent / trace / benchmark 等）
│   │   ├── tasks/              # 异步任务执行器
│   │   └── utils/              # 工具函数（归一化 / 文件 / ID / 评测 / 电子表格 / Word 处理）
│   ├── storage/                # 运行时文件（uploads / temp / outputs）
│   ├── tests/                  # 24 个 pytest 测试
│   └── requirements.txt
├── frontend/                   # React + TypeScript 前端
│   ├── src/
│   │   ├── components/ui/      # shadcn/ui 组件库
│   │   ├── layouts/            # AppShell 双面板布局
│   │   ├── pages/              # WorkspacePage（文档管理）/ AgentPage（Agent 对话）
│   │   ├── services/           # 类型安全的 API 客户端
│   │   └── stores/             # Zustand 状态管理
│   └── package.json
├── 测试集/                      # 比赛测试数据、模板与用户要求
├── A23_LLM_DocFusion_Solution.md  # 详细技术方案文档
└── task.md                     # 开发任务书
```

## 环境要求

- **Python** 3.11+
- **Node.js** 18+（含 npm）
- **PostgreSQL** 14+（可选；未配置时自动使用内存仓储，功能完整但重启后数据丢失）
- **LLM API Key**（DeepSeek 或其他 OpenAI 兼容服务）

## 快速开始

### 1. 后端

```bash
cd backend
python -m venv venv

# Windows
.\venv\Scripts\Activate.ps1
# Linux / Mac
source venv/bin/activate

pip install -r requirements.txt
```

在 `backend/.env` 中填写配置：

```env
# LLM（必填）
DOCFUSION_OPENAI_API_KEY=sk-你的key
DOCFUSION_OPENAI_BASE_URL=https://api.deepseek.com
DOCFUSION_OPENAI_MODEL=deepseek-chat

# 数据库（可选，默认使用内存仓储）
DOCFUSION_DATABASE_URL=postgresql+psycopg://postgres:密码@127.0.0.1:5432/docfusion_copilot

# 可选
DOCFUSION_OPENAI_TIMEOUT_SECONDS=45
DOCFUSION_DATABASE_ECHO=false
```

启动：

```bash
uvicorn app.main:app --reload --port 8000
```

API 地址：`http://localhost:8000`，接口前缀 `/api/v1`。

### 2. 前端

```bash
cd frontend
npm install
npm run dev
```

开发服务器默认在 `http://localhost:5173`，后端 CORS 已预配置允许该地址。

### 3. 生产构建

```bash
cd frontend
npm run build     # 输出到 dist/
npm run preview   # 预览构建结果
```

## 环境变量

| 变量名 | 默认值 | 说明 |
|---|---|---|
| `DOCFUSION_OPENAI_API_KEY` | *（必填）* | OpenAI 兼容 API Key |
| `DOCFUSION_OPENAI_BASE_URL` | — | API 地址（如 `https://api.deepseek.com`） |
| `DOCFUSION_OPENAI_MODEL` | `gpt-4o-mini` | 使用的模型名称 |
| `DOCFUSION_OPENAI_TIMEOUT_SECONDS` | `45` | 请求超时秒数 |
| `DOCFUSION_DATABASE_URL` | `postgresql+psycopg://...` | PostgreSQL 连接字符串 |
| `DOCFUSION_DATABASE_ECHO` | `false` | 是否打印 SQL 日志 |
| `DOCFUSION_CORS_ALLOW_ORIGINS` | `localhost:3000,5173,8080` | 允许的 CORS 来源 |

## API 概览

### 文档

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/documents/upload` | 上传单个文档 |
| POST | `/api/v1/documents/upload-batch` | 批量上传文档 |
| GET | `/api/v1/documents` | 文档列表 |
| GET | `/api/v1/documents/{doc_id}` | 文档详情 |
| GET | `/api/v1/documents/{doc_id}/blocks` | 文档解析块 |
| GET | `/api/v1/documents/{doc_id}/facts` | 文档关联事实 |
| GET | `/api/v1/documents/{doc_id}/raw` | 下载原始文件 |
| DELETE | `/api/v1/documents/{doc_id}` | 删除文档（级联删除 Block 和 Fact） |

### 模板回填

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/templates/fill` | 上传模板并排队回填 |
| GET | `/api/v1/templates/result/{task_id}` | 下载填充结果 |

### Agent 交互

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/agent/chat` | 自然语言 → 意图规划 |
| POST | `/api/v1/agent/execute` | 自然语言 → 执行操作（支持模板上传） |
| GET | `/api/v1/agent/artifacts/{file_name}` | 下载执行产物 |
| GET | `/api/v1/agent/conversations` | 对话列表 |
| POST | `/api/v1/agent/conversations` | 创建对话 |
| GET | `/api/v1/agent/conversations/{id}` | 对话详情 |
| PUT | `/api/v1/agent/conversations/{id}` | 更新对话 |
| DELETE | `/api/v1/agent/conversations/{id}` | 删除对话 |

### 事实与追溯

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/facts` | 事实查询（支持实体/字段/置信度过滤） |
| GET | `/api/v1/facts/low-confidence` | 低置信度事实列表 |
| PATCH | `/api/v1/facts/{fact_id}/review` | 人工复核事实 |
| GET | `/api/v1/facts/{fact_id}/trace` | 来源追溯 |

### 评测

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/benchmarks/facts/evaluate` | 事实评估 |
| POST | `/api/v1/benchmarks/templates/fill` | 模板回填评测 |
| GET | `/api/v1/benchmarks/reports/{task_id}` | 获取评测报告 |

### 任务

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/tasks/{task_id}` | 查询异步任务状态与进度 |

## 测试

```bash
cd backend
python -m pytest tests/ -v
```

当前共 24 个测试，覆盖以下链路：

| 测试类别 | 数量 | 验证内容 |
|---|---|---|
| 文档解析 | 3 | 文本上传 + 事实抽取、PDF 页面块、PDF 表格块 |
| 模板回填 | 4 | XLSX 回填、DOCX 回填、auto_match 文档关联、文档集隔离 |
| Agent 交互 | 5 | 摘要 / 编辑 / 格式整理 / 模板队列 / QA 分支分发 |
| 文档操作 | 2 | 字段提取、数据导出 |
| 事实管理 | 2 | canonical 重选、低置信度筛选 + 人工修正 |
| 评测系统 | 2 | 事实评估准确率、模板 Benchmark + 错误分类 |
| 对话持久化 | 2 | CRUD 生命周期、AgentService 自动持久化 |
| 追溯 | 1 | 事实 → 文档 → 模板使用链路 |
| Benchmark 报告 | 1 | 错误分类 + Markdown 报告生成 |

## 前端页面

| 页面 | 功能 |
|---|---|
| **WorkspacePage** | 文档上传（拖拽 / 批量）、文档列表、任务进度、文件预览（Markdown / 文本 / PDF / DOCX / XLSX） |
| **AgentPage** | 自然语言对话（Markdown 渲染）、模板上传与回填（xlsx/docx/txt/md）、操作结果卡片、填充单元格详情、来源追溯面板、对话历史侧边栏 |

## 详细方案

完整的技术方案、数据模型设计、API 设计与演示范围见 [A23_LLM_DocFusion_Solution.md](A23_LLM_DocFusion_Solution.md)。