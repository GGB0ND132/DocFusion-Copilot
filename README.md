# DocFusion Copilot

A23 赛题项目：基于大语言模型的文档理解与多源数据融合系统。

系统采用两阶段流程：
1. 阶段一（建库）：上传文档并解析为 Block，抽取 Fact，归一化后入库。
2. 阶段二（秒填）：上传模板，理解字段语义，查询事实库并自动回填。

详细架构与赛题方案见 [A23_LLM_DocFusion_Solution.md](A23_LLM_DocFusion_Solution.md)。

## 仓库结构

- [backend](backend): FastAPI 后端，包含解析、抽取、回填、Agent、评测。
- [frontend](frontend): React 前端，包含工作台与 Agent 页面。
- [测试集](测试集): 比赛测试数据与模板场景。
- [task.md](task.md): 当前任务账本与优先级。

## 核心能力

- 多格式文档解析：docx / md / txt / xlsx / pdf。
- 事实抽取与融合：实体、字段、数值、单位、年份标准化。
- 模板回填：支持 xlsx 与 docx。
- 追溯链路：Fact -> Source Document -> Block -> Evidence。
- Agent 交互：问答、摘要、提取、回填、导出。

## 快速启动

### 方式一：本地启动

后端：

```bash
cd backend
python -m venv venv
# Windows
venv\\Scripts\\activate
# Linux/Mac
# source venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

前端：

```bash
cd frontend
npm install
npm run dev
```

### 方式二：Docker

```bash
# 在仓库根目录
# 先准备 .env
# 再启动
# 若 compose 在 compose/ 子目录，请在该目录执行

docker compose up --build
```

## 常用命令

后端健康检查：

```bash
curl http://127.0.0.1:8000/health
```

后端测试：

```bash
cd backend
python -m pytest tests -v
```

前端构建：

```bash
cd frontend
npm run build
```

## 测试集按用户要求生成脚本

已提供脚本 [backend/generate_testset_by_requirements.py](backend/generate_testset_by_requirements.py)，按 [测试集/包含模板文件](测试集/包含模板文件) 中每个场景的 [用户要求.txt](测试集/包含模板文件/README.txt) 生成结果文件。

运行：

```bash
cd backend
python generate_testset_by_requirements.py
```

默认输出：
- 城市经济场景：`*-模板-按用户要求结果.xlsx`
- 山东空气场景：`*-模板-按用户要求结果.docx`
- COVID 场景：`*-模板-按用户要求结果.xlsx`

## 文档导航

- 项目总体方案：[A23_LLM_DocFusion_Solution.md](A23_LLM_DocFusion_Solution.md)
- 后端说明：[backend/README.md](backend/README.md)
- 前端说明：[frontend/README.md](frontend/README.md)
- 开发任务账本：[task.md](task.md)
