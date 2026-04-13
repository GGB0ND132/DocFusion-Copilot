# ─────────────────────────────────────────────────────────────
# DocFusion Copilot — Backend Dockerfile
# 构建上下文: backend/
#
# 关键：WORKDIR 必须是 /workspace/backend，使 config.py 中
#   Path(__file__).resolve().parents[3] == /workspace  (workspace_root)
#   Path(__file__).resolve().parents[2] == /workspace/backend (backend_dir)
# 从而 storage_dir = /workspace/backend/storage ← 与 Volume 挂载一致
# ─────────────────────────────────────────────────────────────
FROM python:3.11-slim

# 系统依赖：gcc/libpq 供 psycopg & pdfplumber 编译
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/backend

# 先单独 COPY 依赖文件，利用 Docker layer 缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再 COPY 源代码
COPY . .

# 确保运行时目录存在（volume 挂载前的保底兜底）
RUN mkdir -p storage/uploads storage/outputs storage/temp

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
