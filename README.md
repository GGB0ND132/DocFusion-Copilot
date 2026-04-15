# DocFusion Copilot

A23 赛题项目：基于大语言模型的文档理解与多源数据融合系统。

上传多格式文档（docx / xlsx / md / txt / pdf），自动解析并抽取结构化事实，再上传模板即可一键回填，支持追溯每个填充值的来源证据。

## 两阶段流程

1. **建库**：上传文档 → 解析为 Block → 抽取 Fact → 归一化入库。
2. **秒填**：上传模板 → 理解字段语义 → 查询事实库 → 自动回填并下载。

详细架构与赛题方案见 [A23_LLM_DocFusion_Solution.md](A23_LLM_DocFusion_Solution.md)。

## 仓库结构

```text
backend/          FastAPI 后端：解析、抽取、回填、Agent、评测
frontend/         React 前端：工作台 + Agent 页面
task.md           当前任务账本与优先级
测试集/            比赛测试数据与模板场景
项目文档资料/       方案文档与竞赛素材
tools/            辅助工具（docx 导出等）
```

## 核心能力

| 能力 | 说明 |
|------|------|
| 多格式解析 | docx / xlsx / md / txt / pdf |
| 事实抽取 | 实体、字段、数值、单位、年份标准化 |
| 模板回填 | 支持 xlsx 与 docx，90 秒内完成 |
| 多任务并发 | 同时提交多个回填任务，独立追踪进度 |
| 追溯链路 | Fact → Source Document → Block → Evidence |
| Agent 交互 | 问答、摘要、提取、回填、导出 |
| 会话管理 | 多轮对话上下文保持，历史对话列表 |

## 快速启动

### 前置要求

- Python 3.11+
- Node.js 18+
- PostgreSQL 15+（必需）

### 后端

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
# Linux/Mac
# source .venv/bin/activate
pip install -r requirements.txt

# 复制并编辑环境变量
cp .env.example .env
# 编辑 .env 填入数据库连接串和 LLM API Key

python app/main.py
```

### 前端

```bash
cd frontend
npm install
npm run dev
```

> [!TIP]
> 后端启动后访问 `http://127.0.0.1:8000/docs` 查看交互式 API 文档。

## 常用命令

```bash
# 后端健康检查
curl http://127.0.0.1:8000/health

# 后端测试
cd backend && python -m pytest tests -v

# 前端构建
cd frontend && npm run build

# 测试集按用户要求批量生成
cd backend && python generate_testset_by_requirements.py
```

## 文档导航

- 项目总体方案：[A23_LLM_DocFusion_Solution.md](A23_LLM_DocFusion_Solution.md)
- 后端说明：[backend/README.md](backend/README.md)
- 前端说明：[frontend/README.md](frontend/README.md)
- 开发任务账本：[task.md](task.md)
