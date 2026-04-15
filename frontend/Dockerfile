# ─────────────────────────────────────────────────────────────
# DocFusion Copilot — Frontend Dockerfile
# 构建上下文: frontend/
# 多阶段构建：Node 构建 → Nginx 静态服务
# ─────────────────────────────────────────────────────────────

# ── Stage 1: 构建 ──
FROM node:20-alpine AS builder

ARG VITE_API_BASE_URL=http://localhost:8000
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm install --frozen-lockfile || npm install

COPY . .
RUN npm run build

# ── Stage 2: Nginx 静态服务 ──
FROM nginx:alpine

COPY --from=builder /app/dist /usr/share/nginx/html

# SPA fallback: 所有路由都返回 index.html
RUN printf 'server {\n  listen 80;\n  location / {\n    root /usr/share/nginx/html;\n    try_files $uri $uri/ /index.html;\n  }\n}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]