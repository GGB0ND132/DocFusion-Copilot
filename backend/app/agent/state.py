"""Agent State 定义。"""

from __future__ import annotations

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage
from typing_extensions import TypedDict


class AgentState(TypedDict):
    """LangGraph agent 的运行时状态。"""

    # 完整对话消息列表（LangGraph 自动管理 append）
    messages: Annotated[list[BaseMessage], add_messages]

    # 当前会话 ID（对应前端 context_id / conversation_id）
    context_id: str | None

    # Agent 执行过程中收集的事实（供响应返回）
    facts: list[dict[str, Any]]

    # Agent 执行过程中生成的产物文件
    artifacts: list[dict[str, Any]]

    # 异步任务 ID（模板回填等长任务）
    task_id: str | None

    # 上传的模板文件路径（multipart 场景）
    template_file_path: str | None

    # 上传的模板文件名
    template_name: str | None

    # 用户要求文本（模板回填场景）
    user_requirement: str

    # 关联的文档 ID 列表
    document_ids: list[str]

    # 回填模式
    fill_mode: str

    # 自动匹配
    auto_match: bool
