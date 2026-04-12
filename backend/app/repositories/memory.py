from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from threading import RLock

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


class InMemoryRepository:
    """比赛版后端使用的线程安全内存仓储。
    Thread-safe in-memory repository used by the MVP backend.
    """

    def __init__(self) -> None:
        """初始化文档、事实、任务和结果的空内存存储。
        Initialize empty in-memory stores for documents, facts, tasks and results.
        """
        self._lock = RLock()
        self._documents: dict[str, DocumentRecord] = {}
        self._blocks_by_doc: dict[str, list[DocumentBlock]] = defaultdict(list)
        self._facts: dict[str, FactRecord] = {}
        self._tasks: dict[str, TaskRecord] = {}
        self._template_results: dict[str, TemplateResultRecord] = {}
        self._conversations: dict[str, ConversationRecord] = {}

    def add_document(self, record: DocumentRecord) -> DocumentRecord:
        """存储文档记录并返回脱离引用的副本。
        Store a document record and return a detached copy.
        """
        with self._lock:
            self._documents[record.doc_id] = replace(record)
            return replace(record)

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        """按 id 返回单个文档记录。
        Return one document record by id if it exists.
        """
        with self._lock:
            record = self._documents.get(doc_id)
            return replace(record) if record else None

    def list_documents(self, status: DocumentStatus | None = None) -> list[DocumentRecord]:
        """列出文档记录，可按状态过滤。
        List stored documents, optionally filtered by status.
        """
        with self._lock:
            documents = list(self._documents.values())
            if status is not None:
                documents = [record for record in documents if record.status == status]
            return [replace(record) for record in documents]

    def delete_document(self, doc_id: str) -> DocumentRecord | None:
        """删除文档及其关联的 Block 和 Fact，返回被删除的记录。
        Delete a document and its associated blocks and facts.
        """
        with self._lock:
            record = self._documents.pop(doc_id, None)
            if record is None:
                return None
            self._blocks_by_doc.pop(doc_id, None)
            fact_ids_to_remove = [
                fid for fid, fact in self._facts.items() if fact.source_doc_id == doc_id
            ]
            for fid in fact_ids_to_remove:
                del self._facts[fid]
            if fact_ids_to_remove:
                self._recompute_canonical_flags()
            return replace(record)

    def update_document(
        self,
        doc_id: str,
        *,
        status: DocumentStatus | None = None,
        metadata_updates: dict[str, object] | None = None,
    ) -> DocumentRecord | None:
        """更新文档状态或元数据并返回最新快照。
        Update document status or metadata and return the latest snapshot.
        """
        with self._lock:
            record = self._documents.get(doc_id)
            if not record:
                return None
            if status is not None:
                record.status = status
            if metadata_updates:
                record.metadata.update(metadata_updates)
            return replace(record)

    def replace_blocks(self, doc_id: str, blocks: list[DocumentBlock]) -> None:
        """替换指定文档关联的全部解析块。
        Replace all parsed blocks associated with a document.
        """
        with self._lock:
            self._blocks_by_doc[doc_id] = [replace(block) for block in blocks]

    def list_blocks(self, doc_id: str, *, limit: int | None = None, offset: int = 0) -> list[DocumentBlock]:
        """返回指定文档的解析块，支持可选分页。
        Return parsed blocks for a given document, with optional pagination.
        """
        with self._lock:
            all_blocks = [replace(block) for block in self._blocks_by_doc.get(doc_id, [])]
            sliced = all_blocks[offset:] if limit is None else all_blocks[offset:offset + limit]
            return sliced

    def count_blocks(self, doc_id: str) -> int:
        """返回指定文档的解析块总数。"""
        with self._lock:
            return len(self._blocks_by_doc.get(doc_id, []))

    def upsert_task(self, task: TaskRecord) -> TaskRecord:
        """插入或替换任务记录并返回副本。
        Insert or replace a task record and return a detached copy.
        """
        with self._lock:
            self._tasks[task.task_id] = replace(task)
            return replace(task)

    def get_task(self, task_id: str) -> TaskRecord | None:
        """按 id 返回单个任务记录。
        Return one task record by id if present.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            return replace(task) if task else None

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
        """更新任务进度字段并返回刷新后的任务快照。
        Update task progress fields and return the refreshed task snapshot.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if status is not None:
                task.status = status
            if progress is not None:
                task.progress = progress
            if message is not None:
                task.message = message
            if error is not None:
                task.error = error
            if result_updates:
                task.result.update(result_updates)
            task.updated_at = datetime.now(timezone.utc)
            return replace(task)

    def delete_facts_by_doc_id(self, doc_id: str) -> int:
        """删除指定文档的全部事实记录，返回删除条数。"""
        with self._lock:
            to_delete = [fid for fid, f in self._facts.items() if f.source_doc_id == doc_id]
            for fid in to_delete:
                del self._facts[fid]
            if to_delete:
                self._recompute_canonical_flags()
            return len(to_delete)

    def add_facts(self, facts: list[FactRecord]) -> list[FactRecord]:
        """存储事实、重算 canonical 结果并返回副本。
        Store facts, recompute canonical winners and return stored copies.
        """
        with self._lock:
            for fact in facts:
                self._facts[fact.fact_id] = replace(fact)
            self._recompute_canonical_flags()
            return [replace(self._facts[fact.fact_id]) for fact in facts]

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
        """列出事实，可按实体、字段、canonical 状态或文档过滤。
        List facts with optional filtering by entity, field, canonical state or documents.
        """
        with self._lock:
            facts = list(self._facts.values())
            if entity_name is not None:
                facts = [fact for fact in facts if fact.entity_name == entity_name]
            if field_name is not None:
                facts = [fact for fact in facts if fact.field_name == field_name]
            if status is not None:
                facts = [fact for fact in facts if fact.status == status]
            if min_confidence is not None:
                facts = [fact for fact in facts if fact.confidence >= min_confidence]
            if canonical_only:
                facts = [fact for fact in facts if fact.is_canonical]
            if document_ids is not None:
                if not document_ids:
                    return []
                facts = [fact for fact in facts if fact.source_doc_id in document_ids]
            return [replace(fact) for fact in facts]

    def get_fact(self, fact_id: str) -> FactRecord | None:
        """按 id 返回单个事实记录。
        Return one fact by id if it exists.
        """
        with self._lock:
            fact = self._facts.get(fact_id)
            return replace(fact) if fact else None

    def update_fact(
        self,
        fact_id: str,
        *,
        status: str | None = None,
        metadata_updates: dict[str, object] | None = None,
    ) -> FactRecord | None:
        """更新事实状态或元数据并刷新 canonical 标记。    Update one fact and refresh canonical selection."""

        with self._lock:
            fact = self._facts.get(fact_id)
            if not fact:
                return None
            if status is not None:
                fact.status = status
            if metadata_updates:
                fact.metadata.update(metadata_updates)
            self._recompute_canonical_flags()
            return replace(fact)

    def get_fact_block(self, fact_id: str) -> DocumentBlock | None:
        """返回与事实关联的来源文档块。
        Return the source block associated with a fact if it can be found.
        """
        with self._lock:
            fact = self._facts.get(fact_id)
            if not fact:
                return None
            blocks = self._blocks_by_doc.get(fact.source_doc_id, [])
            for block in blocks:
                if block.block_id == fact.source_block_id:
                    return replace(block)
            return None

    def save_template_result(self, result: TemplateResultRecord) -> TemplateResultRecord:
        """保存单个模板回填结果并返回副本。
        Persist one completed template result and return a detached copy.
        """
        with self._lock:
            self._template_results[result.task_id] = deepcopy(result)
            return deepcopy(result)

    def get_template_result(self, task_id: str) -> TemplateResultRecord | None:
        """返回任务对应的模板回填结果。
        Return the template result stored for a task if present.
        """
        with self._lock:
            result = self._template_results.get(task_id)
            return deepcopy(result) if result else None

    def list_template_results(self) -> list[TemplateResultRecord]:
        """列出全部已完成的模板回填结果。
        List all completed template fill results.
        """
        with self._lock:
            return [deepcopy(result) for result in self._template_results.values()]

    # ── Conversation CRUD ──

    def create_conversation(self, record: ConversationRecord) -> ConversationRecord:
        """创建对话记录。    Persist a new conversation record."""
        with self._lock:
            self._conversations[record.conversation_id] = deepcopy(record)
            return deepcopy(record)

    def get_conversation(self, conversation_id: str) -> ConversationRecord | None:
        """按 id 查询对话记录。    Fetch a conversation by id."""
        with self._lock:
            record = self._conversations.get(conversation_id)
            return deepcopy(record) if record else None

    def update_conversation(self, record: ConversationRecord) -> ConversationRecord | None:
        """更新对话记录。    Update an existing conversation record."""
        with self._lock:
            if record.conversation_id not in self._conversations:
                return None
            self._conversations[record.conversation_id] = deepcopy(record)
            return deepcopy(record)

    def list_conversations(self) -> list[ConversationRecord]:
        """列出全部对话，按更新时间倒序。    List all conversations ordered by updated_at DESC."""
        with self._lock:
            records = sorted(
                self._conversations.values(),
                key=lambda r: r.updated_at,
                reverse=True,
            )
            return [deepcopy(r) for r in records]

    def delete_conversation(self, conversation_id: str) -> ConversationRecord | None:
        """删除对话记录。    Delete a conversation record."""
        with self._lock:
            record = self._conversations.pop(conversation_id, None)
            return deepcopy(record) if record else None

    def _recompute_canonical_flags(self) -> None:
        """将每个冲突组中置信度最高的事实标记为 canonical。
        Mark the highest-confidence fact in each conflict group as canonical.
        """
        grouped: dict[tuple[str, str, str, int | None, str | None], list[FactRecord]] = defaultdict(list)
        for fact in self._facts.values():
            key = (fact.entity_type, fact.entity_name, fact.field_name, fact.year, fact.unit)
            grouped[key].append(fact)

        for group_key, facts in grouped.items():
            ordered = sorted(
                (fact for fact in facts if fact.status != "rejected"),
                key=lambda item: (item.confidence, 1 if item.value_num is not None else 0, item.source_doc_id),
                reverse=True,
            )
            conflict_group_id = (
                f"{group_key[0]}::{group_key[1]}::{group_key[2]}::{group_key[3]}::{group_key[4]}"
            )
            for fact in facts:
                fact.is_canonical = False
                fact.conflict_group_id = conflict_group_id
            for index, fact in enumerate(ordered):
                fact.is_canonical = index == 0
