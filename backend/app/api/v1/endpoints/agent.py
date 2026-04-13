from __future__ import annotations

import json
import logging
from pathlib import Path

from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from starlette.datastructures import UploadFile

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app.core.container import get_container
from app.models.domain import ConversationRecord
from app.schemas.agent import (
    AgentChatRequest,
    AgentChatResponse,
    AgentExecuteRequest,
    AgentExecuteResponse,
    ConversationCreateRequest,
    ConversationResponse,
    ConversationUpdateRequest,
)

router = APIRouter()
_logger = logging.getLogger(__name__)


# ── LangGraph agent invocation helpers ──


def _invoke_agent(message: str, context_id: str | None) -> dict:
    """调用 LangGraph agent 并返回结构化结果。"""
    container = get_container()
    graph = container.agent_graph
    repo = container.repository

    # 加载对话历史
    history_messages: list = []
    if context_id:
        record = repo.get_conversation(context_id)
        if record and record.messages:
            for msg in record.messages:
                role = msg.get("role", "user")
                content = str(msg.get("content", ""))
                if role == "assistant":
                    history_messages.append(AIMessage(content=content))
                else:
                    history_messages.append(HumanMessage(content=content))

    history_messages.append(HumanMessage(content=message))

    # 调用 graph
    result = graph.invoke({"messages": history_messages})
    response_messages = result.get("messages", [])

    # 提取最终 AI 回复
    assistant_reply = ""
    tool_names_used: list[str] = []
    fill_template_action: dict | None = None
    for msg in response_messages:
        if isinstance(msg, AIMessage):
            if msg.tool_calls:
                tool_names_used.extend(tc["name"] for tc in msg.tool_calls)
            if msg.content and not msg.tool_calls:
                assistant_reply = msg.content
        # 扫描 ToolMessage 寻找 fill_template action 标记
        if isinstance(msg, ToolMessage) and msg.content:
            try:
                parsed = json.loads(msg.content) if isinstance(msg.content, str) else None
                if isinstance(parsed, dict) and parsed.get("_action") == "fill_template":
                    fill_template_action = parsed
            except (json.JSONDecodeError, TypeError):
                pass

    # 如果没有纯文本回复，取最后一个 AIMessage 的 content
    if not assistant_reply:
        for msg in reversed(response_messages):
            if isinstance(msg, AIMessage) and msg.content:
                assistant_reply = msg.content
                break

    # 推断 intent
    intent = _infer_intent(tool_names_used)

    # 持久化对话
    if context_id:
        _persist_conversation(context_id, message, assistant_reply)

    return {
        "intent": intent,
        "summary": assistant_reply,
        "tool_names_used": tool_names_used,
        "context_id": context_id,
        "fill_template_action": fill_template_action,
    }


def _infer_intent(tool_names: list[str]) -> str:
    """从工具调用名称推断意图。"""
    if not tool_names:
        return "general_qa"
    tool_set = set(tool_names)
    if "fill_template" in tool_set:
        return "extract_and_fill_template"
    if "extract_facts" in tool_set:
        return "extract_facts"
    if "search_facts" in tool_set or "vector_search" in tool_set:
        return "query_facts"
    if "edit_document" in tool_set:
        return "edit_document"
    if "summarize_documents" in tool_set:
        return "summarize_document"
    if "trace_fact" in tool_set:
        return "trace_fact"
    return "general_qa"


def _try_submit_fill_from_action(
    action: dict, document_ids: list[str]
) -> tuple[str | None, str | None]:
    """当 agent 通过 fill_template 工具返回 action 标记时，
    尝试在已上传文档中定位模板文件并提交真实的回填任务。

    Returns (task_id, template_name) or (None, None).
    """
    container = get_container()
    repo = container.repository
    settings = container.settings

    # 1. 从 action 中获取 template_name 提示（LLM 提供的可能不精确）
    requested_name = (action.get("template_name") or "").strip()

    # 2. 搜索已上传的文档中扩展名为 .xlsx/.docx 的模板文件
    supported_exts = settings.supported_template_extensions
    all_docs = repo.list_documents()
    candidates = [
        doc for doc in all_docs
        if Path(doc.file_name).suffix.lower() in supported_exts
    ]

    if not candidates:
        _logger.warning("fill_template action 未找到任何已上传的模板文件")
        return None, None

    # 3. 尝试按名称精确/模糊匹配
    matched_doc = None
    if requested_name:
        # 精确匹配文件名
        for doc in candidates:
            if doc.file_name == requested_name:
                matched_doc = doc
                break
        # 模糊匹配（文件名包含关键字或关键字包含文件名）
        if not matched_doc:
            for doc in candidates:
                if requested_name in doc.file_name or doc.file_name in requested_name:
                    matched_doc = doc
                    break
    # 4. 兜底：取第一个模板文件
    if not matched_doc:
        matched_doc = candidates[0]

    # 5. 读取模板文件内容
    stored = Path(matched_doc.stored_path)
    if not stored.exists():
        _logger.warning("模板文件不存在: %s", stored)
        return None, None

    template_content = stored.read_bytes()
    template_name = matched_doc.file_name

    # 6. 调用 submit_fill_task
    try:
        task = container.template_service.submit_fill_task(
            template_name=template_name,
            content=template_content,
            fill_mode=action.get("fill_mode", "canonical"),
            document_ids=document_ids or action.get("document_ids") or None,
            auto_match=action.get("auto_match", True),
            user_requirement=str(action.get("user_requirement", "")),
        )
        _logger.info("fill_template action 已提交任务: %s (模板: %s)", task.task_id, template_name)
        return task.task_id, template_name
    except Exception as exc:
        _logger.error("fill_template action 提交失败: %s", exc)
        return None, None


def _persist_conversation(context_id: str, user_msg: str, assistant_msg: str) -> None:
    """将对话追加到仓储。"""
    repo = get_container().repository
    record = repo.get_conversation(context_id)
    now = datetime.now(timezone.utc)
    if record is None:
        record = ConversationRecord(
            conversation_id=context_id,
            title=user_msg[:30] + ("…" if len(user_msg) > 30 else ""),
            created_at=now,
            updated_at=now,
            messages=[],
        )
        repo.create_conversation(record)

    record.messages.append({"role": "user", "content": user_msg})
    record.messages.append({"role": "assistant", "content": assistant_msg})
    # 限制历史长度
    if len(record.messages) > 40:
        record.messages = record.messages[-30:]
    record.updated_at = now
    if not record.title:
        record.title = user_msg[:30] + ("…" if len(user_msg) > 30 else "")
    repo.update_conversation(record)


# ── Endpoints ──


@router.post("/chat", response_model=AgentChatResponse)
def chat(payload: AgentChatRequest) -> AgentChatResponse:
    """将自然语言请求交给 LangGraph agent 处理。"""
    result = _invoke_agent(payload.message, payload.context_id)
    return AgentChatResponse(
        intent=result["intent"],
        entities=[],
        fields=[],
        target="agent",
        need_db_store=False,
        context_id=result["context_id"],
        preview=[],
        edits=[],
        planner="langgraph",
    )


@router.post("/execute", response_model=AgentExecuteResponse)
async def execute(request: Request) -> AgentExecuteResponse:
    """执行一个自然语言描述的后端文档操作。"""
    content_type = request.headers.get("content-type", "").lower()
    if "multipart/form-data" in content_type:
        payload = await _parse_multipart_execute_request(request)
    else:
        payload = AgentExecuteRequest.model_validate(await request.json()).model_dump()
        payload["template_name"] = None
        payload["template_content"] = None

    message = payload["message"]
    context_id = payload.get("context_id")
    document_ids = payload.get("document_ids") or []

    # 处理模板上传：先保存模板文件，后续 agent 可使用
    template_name = payload.get("template_name")
    template_content = payload.get("template_content")
    task_id = None

    if template_name and template_content:
        # 直接走模板回填服务
        try:
            container = get_container()
            task = container.template_service.submit_fill_task(
                template_name=template_name,
                content=template_content,
                fill_mode=payload.get("fill_mode", "canonical"),
                document_ids=document_ids or None,
                auto_match=payload.get("auto_match", True),
                user_requirement=str(payload.get("user_requirement", "")),
            )
            task_id = task.task_id
            result = _invoke_agent(
                f"用户上传了模板 {template_name} 并要求回填。任务ID={task_id}，请告知用户任务已提交。",
                context_id,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    else:
        # 附加文档上下文信息到消息中
        enriched_message = message
        if document_ids:
            enriched_message += f"\n[当前选中的文档IDs: {document_ids}]"
        result = _invoke_agent(enriched_message, context_id)

        # 如果 agent 调用了 fill_template 工具，尝试从已上传文档中定位模板并提交真实任务
        action = result.get("fill_template_action")
        if action and not task_id:
            task_id, template_name = _try_submit_fill_from_action(action, document_ids)

    return AgentExecuteResponse(
        intent=result["intent"],
        entities=[],
        fields=[],
        target="agent",
        need_db_store=False,
        context_id=result["context_id"],
        preview=[],
        edits=[],
        planner="langgraph",
        execution_type="agent",
        summary=result["summary"],
        facts=[],
        artifacts=[],
        document_ids=document_ids,
        task_id=task_id,
        task_status="queued" if task_id else None,
        template_name=template_name,
    )


@router.get("/artifacts/{file_name}")
def download_artifact(file_name: str) -> FileResponse:
    """下载自然语言执行后生成的产物文件。    Download an artifact generated by agent execution."""

    if file_name != Path(file_name).name:
        raise HTTPException(status_code=400, detail="Invalid artifact file name.")

    outputs_dir = get_container().settings.outputs_dir.resolve()
    artifact_path = (outputs_dir / file_name).resolve()
    if artifact_path.parent != outputs_dir or not artifact_path.exists():
        raise HTTPException(status_code=404, detail="Artifact not found.")

    media_type = _guess_media_type(artifact_path.suffix.lower())
    return FileResponse(path=artifact_path, filename=file_name, media_type=media_type)


@router.delete("/conversations/{context_id}")
def clear_conversation(context_id: str) -> dict:
    """清空指定对话的历史记录。"""
    get_container().repository.delete_conversation(context_id)
    return {"context_id": context_id, "cleared": True}


# ── Conversation CRUD ──

@router.get("/conversations", response_model=list[ConversationResponse])
def list_conversations() -> list[ConversationResponse]:
    """列出全部对话记录（按更新时间倒序）。"""
    records = get_container().repository.list_conversations()
    return [ConversationResponse.model_validate(asdict(record)) for record in records]


@router.post("/conversations", response_model=ConversationResponse, status_code=201)
def create_conversation(payload: ConversationCreateRequest) -> ConversationResponse:
    """创建新对话。    Create a new conversation."""
    from app.utils.ids import new_id
    from app.models.domain import ConversationRecord

    now = datetime.now(timezone.utc)
    record = ConversationRecord(
        conversation_id=new_id("conv"),
        title=payload.title or "",
        created_at=now,
        updated_at=now,
        metadata=dict(payload.metadata),
    )
    saved = get_container().repository.create_conversation(record)
    return ConversationResponse.model_validate(asdict(saved))


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
def get_conversation(conversation_id: str) -> ConversationResponse:
    """获取单个对话详情。    Get a single conversation by id."""
    record = get_container().repository.get_conversation(conversation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return ConversationResponse.model_validate(asdict(record))


@router.put("/conversations/{conversation_id}", response_model=ConversationResponse)
def update_conversation(conversation_id: str, payload: ConversationUpdateRequest) -> ConversationResponse:
    """更新对话。    Update an existing conversation."""
    repo = get_container().repository
    record = repo.get_conversation(conversation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    if payload.title is not None:
        record.title = payload.title
    if payload.messages is not None:
        record.messages = payload.messages
    if payload.metadata is not None:
        record.metadata = dict(payload.metadata)
    record.updated_at = datetime.now(timezone.utc)
    updated = repo.update_conversation(record)
    if updated is None:
        raise HTTPException(status_code=404, detail="Conversation not found.")
    return ConversationResponse.model_validate(asdict(updated))


async def _parse_multipart_execute_request(request: Request) -> dict[str, object]:
    """解析 `agent/execute` 的 multipart 请求体。
    Parse the multipart request body used by `agent/execute`.
    """
    form = await request.form()
    message = str(form.get("message", "")).strip()
    if not message:
        raise HTTPException(status_code=400, detail="Missing execute message.")

    template_file = form.get("template_file")
    if template_file is not None and not isinstance(template_file, UploadFile):
        raise HTTPException(status_code=400, detail="Invalid template file payload.")

    template_content = await template_file.read() if template_file is not None else None
    return {
        "message": message,
        "context_id": _as_optional_string(form.get("context_id")),
        "document_ids": _parse_document_ids(form.get("document_ids")),
        "document_set_id": _as_optional_string(form.get("document_set_id")),
        "fill_mode": _as_optional_string(form.get("fill_mode")) or "canonical",
        "auto_match": _parse_bool(form.get("auto_match"), default=True),
        "template_name": template_file.filename if template_file is not None else None,
        "template_content": template_content,
        "user_requirement": str(form.get("user_requirement", "")).strip(),
    }


def _parse_document_ids(raw_value: object) -> list[str]:
    """解析 multipart 文本中的文档 id 列表。
    Parse a list of document ids from multipart text content.
    """
    if raw_value is None:
        return []
    text = str(raw_value).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail="Invalid JSON in document_ids.") from exc
        if not isinstance(payload, list):
            raise HTTPException(status_code=400, detail="document_ids JSON must be a list.")
        return [str(item).strip() for item in payload if str(item).strip()]
    return [item.strip() for item in text.split(",") if item.strip()]


def _parse_bool(raw_value: object, *, default: bool) -> bool:
    """解析 multipart 表单中的布尔文本。
    Parse boolean-like multipart form text.
    """
    if raw_value is None:
        return default
    normalized = str(raw_value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise HTTPException(status_code=400, detail=f"Invalid boolean value: {raw_value}")


def _as_optional_string(raw_value: object) -> str | None:
    """将表单值规范化为可选字符串。
    Normalize a form value into an optional string.
    """
    if raw_value is None:
        return None
    text = str(raw_value).strip()
    return text or None


def _guess_media_type(suffix: str) -> str:
    """根据文件后缀推断下载内容类型。    Guess a download media type from a file suffix."""

    if suffix == ".docx":
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if suffix == ".xlsx":
        return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if suffix == ".md":
        return "text/markdown; charset=utf-8"
    if suffix == ".txt":
        return "text/plain; charset=utf-8"
    if suffix == ".json":
        return "application/json"
    return "application/octet-stream"
