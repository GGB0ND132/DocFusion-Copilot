# DocFusion Copilot

> A23 赛题：基于大语言模型的文档理解与多源数据融合系统

将 docx / md / xlsx / txt 多源非结构化文档自动解析为结构化事实，存入 PostgreSQL，再根据用户上传的 Word / Excel 模板自动理解表头语义、查询事实库并回填，同时支持自然语言指令操作文档。

## 目录结构

```
├── backend/          # FastAPI 后端
│   ├── app/
│   │   ├── api/v1/   # REST 端点
│   │   ├── core/     # 配置、DI 容器、LLM 客户端
│   │   ├── models/   # 领域模型
│   │   ├── parsers/  # docx/md/txt/xlsx 解析器
│   │   ├── repositories/  # PostgreSQL ORM
│   │   ├── schemas/  # API 请求/响应模型
│   │   ├── services/ # 业务逻辑（13 个服务）
│   │   ├── tasks/    # 异步任务执行器
│   │   └── utils/    # 工具函数
│   ├── storage/      # 运行时文件（uploads/temp/outputs）
│   ├── tests/        # pytest 测试
│   └── requirements.txt
├── frontend/         # React 前端
│   ├── src/
│   │   ├── components/ui/  # shadcn/ui 组件
│   │   ├── layouts/        # AppShell 布局
│   │   ├── pages/          # WorkspacePage / AgentPage
│   │   ├── services/       # API 客户端
│   │   └── stores/         # Zustand 状态管理
│   └── package.json
├── 测试集/            # 比赛测试数据与模板
├── A23_LLM_DocFusion_Solution.md  # 详细技术方案
└── task.md           # 开发任务书与 TODO
```

## 技术栈

| 层级 | 技术 |
|---|---|
| 后端 | FastAPI · SQLAlchemy 2.0 · PostgreSQL · psycopg 3 |
| LLM | OpenAI 兼容 API（DeepSeek）· instructor 结构化输出 |
| 前端 | React 19 · TypeScript 5.8 · Vite 6 · Tailwind CSS 3 · Radix UI · Zustand 5 |
| 异步 | ThreadPoolExecutor（进程内线程池） |

## 环境要求

- **Python** 3.11+
- **Node.js** 18+（含 npm）
- **PostgreSQL** 14+（需提前安装并创建数据库）
- **LLM API Key**（DeepSeek 或其他 OpenAI 兼容服务）

## 快速开始

### 1. 后端

```bash
cd backend

# 创建虚拟环境
python -m venv venv

# 激活虚拟环境
# Windows:
.\venv\Scripts\Activate.ps1
# Linux/Mac:
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

在 `backend/` 目录`.env` 文件中填写配置：

```env
# 数据库
DOCFUSION_DATABASE_URL=postgresql+psycopg://postgres:你的密码@127.0.0.1:5432/数据库名

# LLM（OpenAI 兼容 API）
DOCFUSION_OPENAI_API_KEY=sk-你的key
DOCFUSION_OPENAI_BASE_URL=https://api.deepseek.com
DOCFUSION_OPENAI_MODEL=deepseek-chat

# 可选配置
DOCFUSION_OPENAI_TIMEOUT_SECONDS=45
DOCFUSION_DATABASE_ECHO=false
```

启动后端：

```bash
uvicorn app.main:app --reload --port 8000
```

后端启动时会自动创建数据库表。API 地址：`http://localhost:8000`，接口前缀 `/api/v1`。

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

## 主要功能

- **文档解析**：支持 docx / md / txt / xlsx 四种格式，结构感知切分为 Block
- **事实抽取**：从 Block 中提取实体、字段、数值、单位、日期，存入 PostgreSQL
- **模板回填**：xlsx / docx 两种模板，字段归一化 + 实体匹配 + LLM 辅助填充
- **Agent 对话**：规则 + OpenAI 双策略意图规划，支持模板回填与自然语言操作
- **来源追溯**：每条事实可追溯到原文档、原段落、原文片段与置信度
- **评测系统**：内置 Benchmark 服务，评估模板填充精度

## API 概览

| 端点 | 功能 |
|---|---|
| `POST /api/v1/documents/upload` | 文档上传（单个/批量） |
| `GET /api/v1/documents` | 文档列表与查询 |
| `GET /api/v1/tasks/{task_id}` | 异步任务状态 |
| `POST /api/v1/templates/fill` | 模板填充 |
| `GET /api/v1/templates/result/{task_id}` | 下载填充结果 |
| `POST /api/v1/agent/chat` | Agent 意图规划 |
| `POST /api/v1/agent/execute` | Agent 执行 |
| `GET /api/v1/facts/{fact_id}/trace` | 事实追溯 |

## 测试

```bash
cd backend
python -m pytest tests/ -v
```

当前 15 个测试全部通过。