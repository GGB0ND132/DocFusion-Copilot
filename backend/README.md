# 后端说明

`DocFusion Copilot` 后端当前覆盖了赛题所要求的三条核心链路：

- 文档智能操作交互：自然语言查询事实、摘要、基础排版整理、文本替换
- 非结构化文档信息提取：上传 `docx / md / txt / xlsx` 后异步解析、抽取 facts、存入 PostgreSQL
- 表格自定义数据填写：上传 `xlsx / docx` 模板后，自动匹配相关文档并完成回填

当前版本额外补了几项对赛题评测很关键的能力：

- `document_set_id` 批次隔离，支持“先上传整批文档，再逐个上传模板”
- 模板到文档的自动匹配，优先规则匹配，配置 OpenAI-compatible 后可启用语义匹配
- 普通模板回填任务记录 `elapsed_seconds`
- `agent/execute` 同时支持 JSON 执行和 `multipart/form-data` 模板上传执行
- 事实评测与模板基准测试
- fact 追溯和人工复核

## 目录

- `app/main.py`
  FastAPI 入口
- `app/api/v1`
  REST API
- `app/core`
  配置、依赖容器、OpenAI-compatible 客户端模板
- `app/parsers`
  `docx / md / txt / xlsx` 解析器
- `app/services`
  文档处理、事实抽取、模板匹配与回填、自然语言执行、评测
- `app/repositories`
  PostgreSQL 仓储实现
- `tests`
  核心业务回归测试

## 主要接口

- `POST /api/v1/documents/upload`
- `POST /api/v1/documents/upload-batch`
- `GET /api/v1/documents`
- `GET /api/v1/documents/{doc_id}`
- `GET /api/v1/documents/{doc_id}/blocks`
- `GET /api/v1/documents/{doc_id}/facts`
- `GET /api/v1/tasks/{task_id}`
- `GET /api/v1/facts`
- `PATCH /api/v1/facts/{fact_id}/review`
- `GET /api/v1/facts/{fact_id}/trace`
- `POST /api/v1/templates/fill`
- `GET /api/v1/templates/result/{task_id}`
- `POST /api/v1/agent/chat`
- `POST /api/v1/agent/execute`
- `GET /api/v1/agent/artifacts/{file_name}`
- `POST /api/v1/benchmarks/facts/evaluate`
- `POST /api/v1/benchmarks/templates/fill`
- `GET /api/v1/benchmarks/reports/{task_id}`

## 本地启动

以下步骤按 Windows PowerShell 编写，默认不使用 Docker。

### 1. 创建并激活虚拟环境

在仓库根目录执行：

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
```

如果 PowerShell 阻止脚本执行，可先在当前终端放开本次会话：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
```

### 2. 安装后端依赖

```powershell
pip install -r backend\requirements.txt
```

### 3. 准备本地 PostgreSQL

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

### 4. 配置当前终端环境变量

以下命令只对当前 PowerShell 会话生效。

#### 4.1 数据库

```powershell
$env:DOCFUSION_DATABASE_URL="postgresql+psycopg://postgres:postgres@127.0.0.1:5432/docfusion_copilot"
$env:DOCFUSION_DATABASE_ECHO="false"
```

如果你的 PostgreSQL 密码不是 `postgres`，请把连接串中的密码改成真实值。

#### 4.2 CORS

如果前端本地跑在 Vite 默认端口，通常这样就够了：

```powershell
$env:DOCFUSION_CORS_ALLOW_ORIGINS="http://localhost:5173,http://127.0.0.1:5173"
$env:DOCFUSION_CORS_ALLOW_METHODS="*"
$env:DOCFUSION_CORS_ALLOW_HEADERS="*"
$env:DOCFUSION_CORS_ALLOW_CREDENTIALS="false"
```

#### 4.3 OpenAI-compatible，可选

如果当前先不接模型，这一步可以跳过，后端会回退到本地规则逻辑。

```powershell
$env:DOCFUSION_OPENAI_API_KEY="your_api_key"
$env:DOCFUSION_OPENAI_BASE_URL="https://your-openai-compatible-endpoint/v1"
$env:DOCFUSION_OPENAI_MODEL="gpt-5-mini"
$env:DOCFUSION_OPENAI_TIMEOUT_SECONDS="45"
```

说明：

- 不配置 OpenAI 也可运行，系统会使用本地规则完成解析、匹配和回填
- 配置后，`agent/chat`、文档摘要和模板文档匹配会优先尝试调用 OpenAI-compatible 接口
- 仓库只保留接口模板，不内置真实 `api_key` 和 `base_url`
- 后端已启用 `CORSMiddleware`，默认放行常见本地开发源：`3000 / 5173 / 8080`

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
