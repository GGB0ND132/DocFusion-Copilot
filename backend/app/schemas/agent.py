from __future__ import annotations

from datetime import datetime

from pydantic import Field

from app.schemas.common import APIModel, FactResponse


class AgentExecuteRequest(APIModel):
    """代理执行接口的输入载荷。
    Input payload for the agent execution endpoint.
    """

    message: str = Field(min_length=1)
    context_id: str | None = None
    document_ids: list[str] = Field(default_factory=list)
    document_set_id: str | None = None
    fill_mode: str = "canonical"
    auto_match: bool = True


class AgentExecutionArtifactResponse(APIModel):
    """代理执行产物的文件元数据。
    File artifact metadata produced by an agent execution.
    """

    doc_id: str
    operation: str
    file_name: str
    output_path: str
    change_count: int | None = None


class AgentExecuteResponse(APIModel):
    """代理执行接口返回的结果结构。
    Result payload returned by the agent execution endpoint.
    """

    intent: str
    entities: list[str]
    fields: list[str]
    target: str
    need_db_store: bool
    context_id: str | None
    preview: list[dict[str, object]]
    edits: list[dict[str, str]]
    planner: str
    execution_type: str
    summary: str
    facts: list[FactResponse]
    artifacts: list[AgentExecutionArtifactResponse]
    document_ids: list[str]
    task_id: str | None = None
    task_status: str | None = None
    template_name: str | None = None


# ── Conversation schemas ──


class ConversationResponse(APIModel):
    """对话记录返回体。"""

    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[dict[str, object]] = Field(default_factory=list)
    metadata: dict[str, object] = Field(default_factory=dict)


class ConversationCreateRequest(APIModel):
    """创建对话请求体。"""

    title: str = ""
    metadata: dict[str, object] = Field(default_factory=dict)


class ConversationUpdateRequest(APIModel):
    """更新对话请求体。"""

    title: str | None = None
    messages: list[dict[str, object]] | None = None
    metadata: dict[str, object] | None = None
