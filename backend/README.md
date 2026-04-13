# Backend

DocFusion Copilot 后端基于 FastAPI，负责文档解析、事实抽取、模板回填、Agent 执行与评测。

## 职责边界

- 文档上传与异步解析（docx/md/txt/xlsx/pdf）
- 事实抽取、归一化、冲突处理与查询
- 模板回填（xlsx/docx）
- Agent 执行（问答、摘要、提取、回填、导出）
- 追溯与人工复核
- Benchmark 评测

## 环境准备

```bash
cd backend
python -m venv venv
# Windows
venv\\Scripts\\activate
# Linux/Mac
# source venv/bin/activate
pip install -r requirements.txt
```

推荐 Python 3.11+。

## 配置

常用环境变量：

- `DOCFUSION_OPENAI_API_KEY`
- `DOCFUSION_OPENAI_BASE_URL`
- `DOCFUSION_OPENAI_MODEL`
- `DOCFUSION_DATABASE_URL`
- `DOCFUSION_CORS_ALLOW_ORIGINS`

说明：
- 配置 PostgreSQL 时使用持久化仓储。
- 数据库不可用时，可回退到内存仓储（重启后数据会丢失）。

## 启动

```bash
uvicorn app.main:app --reload --port 8000
```

验证：

```bash
curl http://127.0.0.1:8000/health
```

## API 概览

### Documents

- `POST /api/v1/documents/upload`
- `POST /api/v1/documents/upload-batch`
- `GET /api/v1/documents`
- `GET /api/v1/documents/{doc_id}`
- `GET /api/v1/documents/{doc_id}/blocks`
- `GET /api/v1/documents/{doc_id}/facts`
- `GET /api/v1/documents/{doc_id}/raw`
- `DELETE /api/v1/documents/{doc_id}`
- `POST /api/v1/documents/batch-delete`

### Templates

- `POST /api/v1/templates/fill`
- `GET /api/v1/templates/result/{task_id}`

### Agent

- `POST /api/v1/agent/chat`
- `POST /api/v1/agent/execute`
- `GET /api/v1/agent/artifacts/{file_name}`
- `GET /api/v1/agent/conversations`
- `POST /api/v1/agent/conversations`
- `GET /api/v1/agent/conversations/{id}`
- `PUT /api/v1/agent/conversations/{id}`
- `DELETE /api/v1/agent/conversations/{id}`

### Facts / Trace

- `GET /api/v1/facts`
- `GET /api/v1/facts/low-confidence`
- `PATCH /api/v1/facts/{fact_id}/review`
- `GET /api/v1/facts/{fact_id}/trace`

### Tasks / Benchmarks

- `GET /api/v1/tasks/{task_id}`
- `POST /api/v1/benchmarks/facts/evaluate`
- `POST /api/v1/benchmarks/templates/fill`
- `GET /api/v1/benchmarks/reports/{task_id}`

## 测试与脚本

运行测试：

```bash
python -m pytest tests -v
```

按用户要求批量生成测试集结果：

```bash
python generate_testset_by_requirements.py
```

脚本输入目录：
- [测试集/包含模板文件](../测试集/包含模板文件)

脚本输出目录：
- 在每个场景目录内生成 `*-按用户要求结果` 文件。

## 常见问题

1. 启动报数据库连接错误：先检查 `DOCFUSION_DATABASE_URL`，或临时切回内存仓储。
2. 跨域报错：检查 `DOCFUSION_CORS_ALLOW_ORIGINS` 是否包含前端地址。
3. 模型调用失败：检查 API Key、Base URL、模型名与超时配置。
