# Frontend

前端基于 React + Vite + TypeScript，提供工作台与 Agent 两个主交互页面。

## 技术栈

- React 19 + TypeScript 5.8
- Vite 6
- Tailwind CSS 3.4
- Zustand 5（状态管理）
- Radix UI / shadcn 风格组件
- Lucide React（图标）
- Sonner（Toast 通知）
- React Resizable Panels（面板布局）
- React PDF / React Markdown（文档预览）

## 页面与功能

### 工作台 (WorkspacePage)

- 上传文档（支持批量）
- 查看文档列表、解析状态
- 查看解析后的 Block 与事实
- 文档预览（PDF / Markdown）

### Agent (AgentPage)

- 自然语言问答与操作执行
- 会话管理：创建、切换、删除、批量管理
- 模板回填：上传模板 → 选择源文档（带搜索过滤 + 全选）→ 提交回填
- 多任务并发：同时执行多个回填任务，独立追踪进度
- 右侧面板三 Tab：
  - **回填结果**：已填充单元格列表，含置信度与追溯按钮
  - **上下文**：当前文档集、模板信息
  - **任务**：所有回填任务卡片（状态、进度条、耗时、下载）
- 结果文件下载

### 关键组件

- `DocumentSelectDialog`：源文档选择对话框，支持搜索过滤和全选/取消全选
- `FilePreview`：文档预览

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

| 变量 | 说明 | 示例 |
|------|------|------|
| `VITE_API_BASE_URL` | 后端地址 | `http://127.0.0.1:8000` |

## 联调最小闭环

1. 后端启动并健康检查通过。
2. 前端上传文档，轮询解析状态至完成。
3. 在 Agent 页面上传模板并发送需求。
4. 在文档选择对话框中搜索并勾选源文档。
5. 在「任务」Tab 查看回填进度。
6. 下载回填结果文件。

## 排障建议

1. 页面请求失败：先确认 `VITE_API_BASE_URL` 指向正确的后端地址。
2. 浏览器跨域错误：检查后端 `DOCFUSION_CORS_ALLOW_ORIGINS` 是否包含前端地址。
3. 页面空白或编译错误：执行 `npm run build` 查看具体报错。
