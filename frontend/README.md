# Frontend

前端基于 React + Vite + TypeScript，提供工作台与 Agent 两个主交互页面。

## 技术栈

- React 19
- TypeScript
- Vite
- Tailwind CSS
- Zustand
- Radix UI / shadcn 风格组件

## 页面职责

- 工作台：上传文档、查看解析块、查看事实与状态。
- Agent：自然语言问答、执行操作、模板回填、下载产物。

## 开发启动

```bash
cd frontend
npm install
npm run dev
```

构建：

```bash
npm run build
npm run preview
```

## 环境变量

- `VITE_API_BASE_URL`，例如：`http://127.0.0.1:8000`

## 联调最小闭环

1. 后端启动并健康检查通过。
2. 前端上传文档并拿到 `task_id`。
3. 轮询任务状态直至解析完成。
4. 在 Agent 或模板流程中发起回填。
5. 下载回填结果并检查追溯信息。

## 常用接口（前端高频）

- `POST /api/v1/documents/upload`
- `POST /api/v1/documents/upload-batch`
- `GET /api/v1/tasks/{task_id}`
- `POST /api/v1/templates/fill`
- `GET /api/v1/templates/result/{task_id}`
- `GET /api/v1/facts`
- `GET /api/v1/facts/{fact_id}/trace`
- `POST /api/v1/agent/chat`
- `POST /api/v1/agent/execute`

更多接口说明见 [backend/README.md](../backend/README.md)。

## 排障建议

1. 页面请求失败：先确认 `VITE_API_BASE_URL`。
2. 浏览器跨域错误：检查后端 CORS 配置与当前前端地址是否匹配。
3. 页面空白或编译错误：执行 `npm run build` 查看具体报错。
