from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


class DocumentStatus(StrEnum):
    """上传源文档的生命周期状态。
    Lifecycle states for an uploaded source document.
    """

    uploaded = "uploaded"
    parsing = "parsing"
    parsed = "parsed"
    failed = "failed"


class TaskStatus(StrEnum):
    """后端异步任务的执行状态。
    Execution states for asynchronous backend tasks.
    """

    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"


class TaskType(StrEnum):
    """当前支持的异步任务类别。
    Supported asynchronous task categories.
    """

    parse_document = "parse_document"
    fill_template = "fill_template"


@dataclass(slots=True)
class DocumentRecord:
    """已上传文档的持久化元数据。
    Persisted metadata for an uploaded document.
    """

    doc_id: str
    file_name: str
    stored_path: str
    doc_type: str
    upload_time: datetime
    status: DocumentStatus = DocumentStatus.uploaded
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class DocumentBlock:
    """从文档中抽取出的标准化结构块。
    Normalized structural block extracted from a document.
    """

    block_id: str
    doc_id: str
    block_type: str
    text: str
    section_path: list[str] = field(default_factory=list)
    page_or_index: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class FactRecord:
    """从单个文档块中抽取出的结构化事实。
    Structured fact extracted from one document block.
    """

    fact_id: str
    entity_type: str
    entity_name: str
    field_name: str
    value_num: float | None
    value_text: str
    unit: str | None
    year: int | None
    source_doc_id: str
    source_block_id: str
    source_span: str
    confidence: float
    conflict_group_id: str | None = None
    is_canonical: bool = False
    status: str = "confirmed"
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class TaskRecord:
    """异步任务运行状态快照。
    Runtime status snapshot for an asynchronous job.
    """

    task_id: str
    task_type: TaskType
    status: TaskStatus
    created_at: datetime
    updated_at: datetime
    progress: float = 0.0
    message: str = ""
    error: str | None = None
    result: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class FilledCellRecord:
    """模板工作簿中单个已回填单元格的追溯信息。
    Trace information for one cell populated in a template workbook.
    """

    sheet_name: str
    cell_ref: str
    entity_name: str
    field_name: str
    value: str | float | int
    fact_id: str
    confidence: float
    evidence_text: str = ""


@dataclass(slots=True)
class TemplateResultRecord:
    """单个模板回填结果的元数据。
    Metadata for one completed template filling result.
    """

    task_id: str
    template_name: str
    output_path: str
    output_file_name: str
    created_at: datetime
    fill_mode: str
    document_ids: list[str]
    filled_cells: list[FilledCellRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConversationRecord:
    """对话历史记录。
    Persistent conversation history record.
    """

    conversation_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    messages: list[dict[str, object]] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
