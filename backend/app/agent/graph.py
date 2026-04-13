"""LangGraph StateGraph — agent 核心决策循环。"""

from __future__ import annotations

import logging
from typing import Any, Literal

from langchain_core.messages import AIMessage, SystemMessage
from langgraph.graph import END, StateGraph
from langgraph.prebuilt import ToolNode

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.state import AgentState

logger = logging.getLogger(__name__)


def build_graph(
    *,
    chat_model: Any,
    tools: list,
) -> Any:
    """构建 DocFusion agent 的 LangGraph 编译图。

    Parameters
    ----------
    chat_model : ChatOpenAI
        绑定了 DeepSeek API 的 langchain chat model。
    tools : list
        由 create_tools() 创建的 tool 函数列表。

    Returns
    -------
    CompiledGraph
    """
    model_with_tools = chat_model.bind_tools(tools)

    def agent_node(state: AgentState) -> dict[str, Any]:
        """调用 LLM 并决定下一步动作。"""
        messages = state["messages"]
        # 确保系统提示在消息列表开头
        if not messages or not isinstance(messages[0], SystemMessage):
            messages = [SystemMessage(content=SYSTEM_PROMPT)] + list(messages)
        response = model_with_tools.invoke(messages)
        return {"messages": [response]}

    def should_continue(state: AgentState) -> Literal["tools", "end"]:
        """判断 agent 是否需要继续调用工具。"""
        last_message = state["messages"][-1]
        if isinstance(last_message, AIMessage) and last_message.tool_calls:
            return "tools"
        return "end"

    # 构建图
    workflow = StateGraph(AgentState)
    workflow.add_node("agent", agent_node)
    workflow.add_node("tools", ToolNode(tools))

    workflow.set_entry_point("agent")
    workflow.add_conditional_edges(
        "agent",
        should_continue,
        {"tools": "tools", "end": END},
    )
    workflow.add_edge("tools", "agent")

    return workflow.compile()
