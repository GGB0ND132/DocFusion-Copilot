from __future__ import annotations

from typing import Protocol

from app.models.domain import (
    ConversationRecord,
    DocumentBlock,
    DocumentRecord,
    DocumentStatus,
    FactRecord,
    TaskRecord,
    TaskStatus,
    TemplateResultRecord,
)


class Repository(Protocol):
    """仓储协议，定义服务层需要的持久化能力。
    Repository protocol that defines the persistence capabilities required by services.
    """

    def add_document(self, record: DocumentRecord) -> DocumentRecord:
        """保存文档记录。
        Persist a document record.
        """

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        """按 id 查询文档记录。
        Fetch a document record by id.
        """

    def list_documents(self, status: DocumentStatus | None = None) -> list[DocumentRecord]:
        """列出文档记录，可按状态过滤。
        List document records, optionally filtered by status.
        """

    def update_document(
        self,
        doc_id: str,
        *,
        status: DocumentStatus | None = None,
        metadata_updates: dict[str, object] | None = None,
    ) -> DocumentRecord | None:
        """更新文档状态或元数据。
        Update document status or metadata.
        """

    def replace_blocks(self, doc_id: str, blocks: list[DocumentBlock]) -> None:
        """替换文档的全部解析块。
        Replace all parsed blocks for a document.
        """

    def list_blocks(self, doc_id: str, *, limit: int | None = None, offset: int = 0) -> list[DocumentBlock]:
        """列出文档的解析块，支持可选分页。
        List parsed blocks for a document, with optional pagination.
        """

    def count_blocks(self, doc_id: str) -> int:
        """返回文档解析块总数。
        Return the total number of parsed blocks for a document.
        """

    def upsert_task(self, task: TaskRecord) -> TaskRecord:
        """插入或更新任务记录。
        Insert or update a task record.
        """

    def get_task(self, task_id: str) -> TaskRecord | None:
        """按 id 查询任务记录。
        Fetch a task record by id.
        """

    def update_task(
        self,
        task_id: str,
        *,
        status: TaskStatus | None = None,
        progress: float | None = None,
        message: str | None = None,
        error: str | None = None,
        result_updates: dict[str, object] | None = None,
    ) -> TaskRecord | None:
        """更新任务状态。
        Update task status fields.
        """

    def add_facts(self, facts: list[FactRecord]) -> list[FactRecord]:
        """批量保存事实记录。
        Persist fact records in batch.
        """

    def list_facts(
        self,
        *,
        entity_name: str | None = None,
        field_name: str | None = None,
        status: str | None = None,
        min_confidence: float | None = None,
        canonical_only: bool = False,
        document_ids: set[str] | None = None,
    ) -> list[FactRecord]:
        """列出事实记录，可按多条件过滤。
        List fact records with optional filters.
        """

    def get_fact(self, fact_id: str) -> FactRecord | None:
        """按 id 查询事实记录。
        Fetch a fact record by id.
        """

    def update_fact(
        self,
        fact_id: str,
        *,
        status: str | None = None,
        metadata_updates: dict[str, object] | None = None,
    ) -> FactRecord | None:
        """更新事实状态或附加元数据。    Update a fact status or attach metadata."""

    def get_fact_block(self, fact_id: str) -> DocumentBlock | None:
        """获取事实对应的来源块。
        Fetch the source block referenced by a fact.
        """

    def save_template_result(self, result: TemplateResultRecord) -> TemplateResultRecord:
        """保存模板回填结果。
        Persist a template fill result.
        """

    def get_template_result(self, task_id: str) -> TemplateResultRecord | None:
        """按任务 id 查询模板回填结果。
        Fetch a template fill result by task id.
        """

    def list_template_results(self) -> list[TemplateResultRecord]:
        """列出全部模板回填结果。
        List all template fill results.
        """

    def delete_document(self, doc_id: str) -> DocumentRecord | None:
        """删除文档及其关联的 Block 和 Fact（级联删除），返回被删除的文档记录。
        Delete a document and cascade-remove its blocks and facts. Returns the deleted record or None.
        """

    # ── Conversation CRUD ──

    def create_conversation(self, record: ConversationRecord) -> ConversationRecord:
        """创建对话记录。    Persist a new conversation record."""

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        """按 id 查询对话记录。    Fetch a conversation by id."""

    def update_conversation(self, record: ConversationRecord) -> ConversationRecord | None:
        """更新对话记录。    Update an existing conversation record."""

    def list_conversations(self) -> list[ConversationRecord]:
        """列出全部对话，按更新时间倒序。    List all conversations ordered by updated_at DESC."""

    def delete_conversation(self, conversation_id: str) -> ConversationRecord | None:
        """删除对话记录。    Delete a conversation record."""
