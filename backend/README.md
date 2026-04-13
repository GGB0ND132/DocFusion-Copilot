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

<<<<<<< HEAD
1. 启动报数据库连接错误：先检查 `DOCFUSION_DATABASE_URL`，或临时切回内存仓储。
2. 跨域报错：检查 `DOCFUSION_CORS_ALLOW_ORIGINS` 是否包含前端地址。
3. 模型调用失败：检查 API Key、Base URL、模型名与超时配置。
=======
后端当前必须连接 PostgreSQL。默认连接串是：

```text
postgresql+psycopg://postgres:postgres@127.0.0.1:5432/docfusion_copilot
```

如果你本机已经安装 PostgreSQL，建议先确认服务已启动，然后准备数据库。

#### 3.1 使用 `psql` 直接创建数据库

如果你的 `postgres` 用户密码就是 `postgres`，可以直接执行：

```powershell
psql -U postgres -h 127.0.0.1 -p 5432 -d postgres -c "CREATE DATABASE docfusion_copilot;"
```

如果报“数据库已存在”，可以忽略。

#### 3.2 如果你想把 `postgres` 用户密码改成 `postgres`

先进入 PostgreSQL：

```powershell
psql -U postgres -h 127.0.0.1 -p 5432 -d postgres
```

再执行：

```sql
ALTER USER postgres WITH PASSWORD 'postgres';
CREATE DATABASE docfusion_copilot;
```

然后用 `\q` 退出。

如果你不想改密码，也可以保留现有密码，只要把下面的 `DOCFUSION_DATABASE_URL` 改成真实密码即可。

### 4. 配置环境变量

#### 4.1 推荐：使用 `.env` 文件（一次配置，长期生效）

仓库已提供示例文件 `.env.example`，复制并修改即可：

```powershell
Copy-Item backend\.env.example backend\.env
```

然后用编辑器打开 `backend/.env`，填入你的真实值：

```dotenv
# 数据库（必填，改成你的真实密码）
DOCFUSION_DATABASE_URL=postgresql+psycopg://postgres:你的密码@127.0.0.1:5432/docfusion_copilot

# LLM（可选，不填则回退到本地规则）
DOCFUSION_OPENAI_API_KEY=sk-xxx
DOCFUSION_OPENAI_BASE_URL=https://your-openai-compatible-endpoint/v1
DOCFUSION_OPENAI_MODEL=gpt-4o-mini
```

> `.env` 文件已被 `.gitignore` 排除，不会被提交到仓库。

完整变量列表见 `.env.example`。

#### 4.2 备选：手动设置 PowerShell 环境变量

如果不想用 `.env` 文件，也可以在当前终端手动设置（仅对当前会话生效）：

```powershell
# 数据库
$env:DOCFUSION_DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/docfusion_copilot"

# LLM（可选）
$env:DOCFUSION_OPENAI_API_KEY="your_api_key"
$env:DOCFUSION_OPENAI_BASE_URL="https://your-openai-compatible-endpoint/v1"
$env:DOCFUSION_OPENAI_MODEL="gpt-4o-mini"

# CORS
$env:DOCFUSION_CORS_ALLOW_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"
```

#### 4.3 配置说明

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `DOCFUSION_DATABASE_URL` | 是 | `postgres:postgres@localhost:5432/docfusion_copilot` | PostgreSQL 连接串 |
| `DOCFUSION_HOST` | 否 | `127.0.0.1` | 后端监听地址 |
| `DOCFUSION_PORT` | 否 | `8000` | 后端监听端口 |
| `DOCFUSION_MAX_WORKERS` | 否 | `4` | 异步任务线程池大小 |
| `DOCFUSION_OPENAI_API_KEY` | 否 | 空 | OpenAI 兼容 API 密钥 |
| `DOCFUSION_OPENAI_BASE_URL` | 否 | 空 | OpenAI 兼容 API 地址 |
| `DOCFUSION_OPENAI_MODEL` | 否 | 空 | 模型名称 |
| `DOCFUSION_OPENAI_TIMEOUT_SECONDS` | 否 | `90` | LLM 请求超时（秒） |
| `DOCFUSION_CORS_ALLOW_ORIGINS` | 否 | `localhost:3000,5173,8080` | 允许跨域的前端源 |

- 不配置 OpenAI 也可运行，系统会使用本地规则完成解析、匹配和回填
- 配置后，`agent/chat`、文档摘要和模板文档匹配会优先尝试调用 LLM
- 仓库只保留 `.env.example`，不内置真实 `api_key`

### 5. 启动后端

推荐直接用：

```powershell
python backend\app\main.py
```

也可以用 `uvicorn`：

```powershell
uvicorn app.main:app --app-dir backend --reload
```

启动成功后访问：

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/docs`

#### 5.1 用 PowerShell 快速验证健康检查

```powershell
Invoke-WebRequest http://127.0.0.1:8000/health
```

如果返回里包含：

```json
{"status":"ok"}
```

说明后端已经正常启动。

### 6. 常见报错排查

#### 6.1 PostgreSQL 密码认证失败

如果看到类似：

```text
password authentication failed for user "postgres"
```

说明你的数据库真实密码不是连接串里写的那个值。要么修改 `DOCFUSION_DATABASE_URL`，要么在 PostgreSQL 里改密码。

#### 6.2 数据库不存在

如果看到类似：

```text
database "docfusion_copilot" does not exist
```

说明你还没创建数据库，回到上面的第 3 步执行 `CREATE DATABASE docfusion_copilot;`。

#### 6.3 端口占用

如果 `8000` 端口被占用，可以这样启动：

```powershell
uvicorn app.main:app --app-dir backend --reload --port 8001
```

对应地，前端的 `VITE_API_BASE_URL` 也要改成 `http://127.0.0.1:8001`。

### 7. 运行测试

```powershell
python -m unittest discover backend/tests -v
```

## 赛题相关建议用法

1. 用 `POST /api/v1/documents/upload-batch` 一次上传一批文档，并给这批文档分配 `document_set_id`
2. 轮询 `GET /api/v1/tasks/{task_id}`，确认文档解析完成
3. 对每个模板调用 `POST /api/v1/templates/fill`
4. 在模板请求中传同一个 `document_set_id`
5. 轮询模板任务状态，读取 `result.elapsed_seconds`、`result.matched_document_ids`
6. 用 `GET /api/v1/templates/result/{task_id}` 下载回填后的模板

## 当前边界

- 模板自动匹配已经可用，但仍以规则为主，OpenAI 语义匹配依赖你后续补充配置
- `agent/execute` 已支持编辑、格式整理、摘要、事实查询，也支持携带 `template_file` 的自然语言回填入口
- 目前异步执行是线程池，不是正式消息队列
- OCR 扫描件仍未接入
>>>>>>> 2552b228659033d875a73d402eceb5449821552e
