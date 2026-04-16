# Backend

DocFusion Copilot 后端基于 FastAPI，负责文档解析、事实抽取、模板回填、Agent 执行与评测。

## 环境准备

- Python 3.11+
- PostgreSQL 15+

```bash
cd backend
python -m venv .venv
# Windows
.venv\Scripts\activate
pip install -r requirements.txt
```

## 数据库

后端必须连接 PostgreSQL。默认连接串：

```text
postgresql+psycopg://postgres:postgres@127.0.0.1:5432/docfusion_copilot
```

创建数据库：

```bash
psql -U postgres -h 127.0.0.1 -c "CREATE DATABASE docfusion_copilot;"
```

## 配置

复制 `.env.example` 为 `.env` 并编辑：

```bash
cp .env.example .env
```

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `DOCFUSION_DATABASE_URL` | 是 | `postgres:postgres@localhost:5432/docfusion_copilot` | PostgreSQL 连接串 |
| `DOCFUSION_HOST` | 否 | `127.0.0.1` | 监听地址 |
| `DOCFUSION_PORT` | 否 | `8000` | 监听端口 |
| `DOCFUSION_MAX_WORKERS` | 否 | `4` | 异步任务线程池大小 |
| `DOCFUSION_OPENAI_API_KEY` | 否 | — | OpenAI 兼容 API 密钥 |
| `DOCFUSION_OPENAI_BASE_URL` | 否 | — | OpenAI 兼容 API 地址 |
| `DOCFUSION_OPENAI_MODEL` | 否 | — | 模型名称 |
| `DOCFUSION_OPENAI_TIMEOUT_SECONDS` | 否 | `90` | LLM 请求超时（秒） |
| `DOCFUSION_CORS_ALLOW_ORIGINS` | 否 | `localhost:3000,5173,8080` | 允许跨域的前端源 |

不配置 OpenAI 也可运行，系统会使用本地规则完成解析、匹配和回填。

## 启动

```bash
python app/main.py
```

或：

```bash
uvicorn app.main:app --reload --port 8000
```

验证：`http://127.0.0.1:8000/health` 返回 `{"status":"ok"}`。

## 服务架构

| 服务 | 职责 |
|------|------|
| `document_service` | 文档上传、解析调度、状态管理 |
| `fact_extraction` | LLM 驱动的事实抽取 |
| `fact_service` | 事实 CRUD、归一化、冲突处理 |
| `template_analyzer` | 模板结构解析与字段映射 |
| `template_filler` | 模板回填执行与单元格赋值（规则通路） |
| `llm_transform` | 统一 LLM 代码生成 + 沙箱执行的回填主通路，处理 xlsx / docx 模板 |
| `template_service` | 模板任务调度、结果检索与导出 |
| `embedding_service` | 文档向量化与检索 |
| `trace_service` | 事实来源追溯链 |

> 默认走 `llm_transform` 主通路：一次 LLM 调用生成 pandas 代码，在受限沙箱内执行抽取/聚合/回填；`template_filler` 作为规则回退使用。

## API 概览

### Documents

- `POST /api/v1/documents/upload` — 上传单个文档
- `POST /api/v1/documents/upload-batch` — 批量上传
- `GET /api/v1/documents` — 文档列表
- `GET /api/v1/documents/{doc_id}` — 文档详情
- `GET /api/v1/documents/{doc_id}/blocks` — 文档块
- `GET /api/v1/documents/{doc_id}/facts` — 文档事实
- `GET /api/v1/documents/{doc_id}/raw` — 原始文件
- `DELETE /api/v1/documents/{doc_id}` — 删除
- `POST /api/v1/documents/batch-delete` — 批量删除
- `POST /api/v1/documents/reindex-embeddings` — 重建向量索引

### Templates

- `POST /api/v1/templates/suggest-documents` — 候选源文档推荐
- `POST /api/v1/templates/fill` — 提交回填任务
- `GET /api/v1/templates/result/{task_id}` — 下载回填结果

### Agent

- `POST /api/v1/agent/chat` — 对话
- `POST /api/v1/agent/execute` — 执行操作（含模板回填）
- `GET /api/v1/agent/artifacts/{file_name}` — 下载产物
- `GET /api/v1/agent/conversations` — 会话列表
- `POST /api/v1/agent/conversations` — 创建会话
- `GET /api/v1/agent/conversations/{id}` — 会话详情
- `PUT /api/v1/agent/conversations/{id}` — 更新会话
- `DELETE /api/v1/agent/conversations/{id}` — 删除会话

### Facts / Trace

- `GET /api/v1/facts` — 事实列表
- `GET /api/v1/facts/low-confidence` — 低置信度事实
- `PATCH /api/v1/facts/{fact_id}/review` — 人工复核
- `GET /api/v1/facts/{fact_id}/trace` — 事实追溯

### Tasks

- `GET /api/v1/tasks/{task_id}` — 任务状态查询

## 测试与评测

```bash
# 单元 / 集成测试
python -m pytest tests -v

# 端到端基准（针对测试集/包含模板文件 下每个场景跑一遍回填并输出报告）
# 需后端已启动
python scripts/run_benchmark.py
```

报告输出到 `storage/benchmark_reports/`，每次回填任务还会在 `storage/outputs/` 下写入
`debug_task_{task_id}_{模板名}_{时间戳}.txt`，内含模板 schema、源文档摘要、用户指令、
LLM 生成的代码，便于排查。

## 常见问题

1. **密码认证失败**：检查 `DOCFUSION_DATABASE_URL` 中的密码与 PostgreSQL 实际密码是否一致。
2. **数据库不存在**：执行 `CREATE DATABASE docfusion_copilot;`。
3. **端口占用**：改用 `--port 8001`，前端 `VITE_API_BASE_URL` 同步修改。
4. **跨域报错**：检查 `DOCFUSION_CORS_ALLOW_ORIGINS` 是否包含前端地址。
5. **模型调用失败**：检查 API Key、Base URL、模型名与超时配置。
