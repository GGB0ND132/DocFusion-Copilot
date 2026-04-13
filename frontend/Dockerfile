# ─────────────────────────────────────────────────────────────
# DocFusion Copilot — Frontend Dockerfile（多阶段构建）
# Stage 1: Node 构建 Vite 产物
# Stage 2: nginx 提供静态文件服务
# ─────────────────────────────────────────────────────────────

# ── Stage 1: Build ──────────────────────────────────────────
FROM node:20-slim AS builder

WORKDIR /app

# 先装依赖（利用缓存层）
COPY package.json package-lock.json ./
RUN npm ci

# 拷贝源码
COPY . .

# VITE_API_BASE_URL 由 docker-compose build.args 传入
# 注意：这是浏览器实际发请求的地址（宿主机可访问），不是容器内地址
ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=$VITE_API_BASE_URL

RUN npm run build

# ── Stage 2: Serve ──────────────────────────────────────────
FROM nginx:1.27-alpine

# 拷贝构建产物
COPY --from=builder /app/dist /usr/share/nginx/html

# 拷贝 SPA 路由配置
COPY nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
