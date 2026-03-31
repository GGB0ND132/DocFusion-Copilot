from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy import Engine, create_engine, delete, select
from sqlalchemy.orm import Session, sessionmaker

from app.models.domain import (
    DocumentBlock,
    DocumentRecord,
    DocumentStatus,
    FactRecord,
    FilledCellRecord,
    TaskRecord,
    TaskStatus,
    TemplateResultRecord,
)
from app.repositories.sqlalchemy_models import (
    Base,
    DocumentBlockRow,
    DocumentRow,
    FactRow,
    TaskRow,
    TemplateResultRow,
)


class PostgresRepository:
    """基于 PostgreSQL 的 SQLAlchemy 仓储实现。
    PostgreSQL-backed repository implementation using SQLAlchemy.
    """

    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        """用数据库连接串初始化 PostgreSQL 仓储。
        Initialize the PostgreSQL repository with a database URL.
        """
        self._engine: Engine = create_engine(
            database_url,
            echo=echo,
            future=True,
            pool_pre_ping=True,
        )
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False, future=True)

    def initialize(self) -> None:
        """创建当前仓储运行所需的数据库表。
        Create the database tables required by the repository.
        """
        Base.metadata.create_all(self._engine)

    @contextmanager
    def _session(self) -> Iterator[Session]:
        """为单次仓储操作提供事务会话。
        Provide a transactional session for one repository operation.
        """
        session = self._session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def add_document(self, record: DocumentRecord) -> DocumentRecord:
        """保存文档记录。
        Persist a document record.
        """
        with self._session() as session:
            session.add(self._document_to_row(record))
            return self._clone_document(record)

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        """按 id 查询文档记录。
        Fetch a document record by id.
        """
        with self._session() as session:
            row = session.get(DocumentRow, doc_id)
            return self._document_from_row(row) if row else None

    def list_documents(self, status: DocumentStatus | None = None) -> list[DocumentRecord]:
        """列出文档记录，可按状态过滤。
        List document records, optionally filtered by status.
        """
        with self._session() as session:
            stmt = select(DocumentRow).order_by(DocumentRow.upload_time.desc())
            if status is not None:
                stmt = stmt.where(DocumentRow.status == str(status))
            return [self._document_from_row(row) for row in session.scalars(stmt).all()]

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
        with self._session() as session:
            row = session.get(DocumentRow, doc_id)
            if row is None:
                return None
            if status is not None:
                row.status = str(status)
            if metadata_updates:
                row.metadata_json = {**(row.metadata_json or {}), **metadata_updates}
            session.add(row)
            session.flush()
            return self._document_from_row(row)

    def replace_blocks(self, doc_id: str, blocks: list[DocumentBlock]) -> None:
        """替换文档的全部解析块。
        Replace all parsed blocks for a document.
        """
        with self._session() as session:
            session.execute(delete(DocumentBlockRow).where(DocumentBlockRow.doc_id == doc_id))
            session.add_all(self._block_to_row(block) for block in blocks)

    def list_blocks(self, doc_id: str) -> list[DocumentBlock]:
        """列出文档的全部解析块。
        List all parsed blocks for a document.
        """
        with self._session() as session:
            stmt = (
                select(DocumentBlockRow)
                .where(DocumentBlockRow.doc_id == doc_id)
                .order_by(DocumentBlockRow.page_or_index.asc().nulls_last(), DocumentBlockRow.block_id.asc())
            )
            return [self._block_from_row(row) for row in session.scalars(stmt).all()]

    def upsert_task(self, task: TaskRecord) -> TaskRecord:
        """插入或更新任务记录。
        Insert or update a task record.
        """
        with self._session() as session:
            row = session.get(TaskRow, task.task_id)
            if row is None:
                row = self._task_to_row(task)
            else:
                row.task_type = str(task.task_type)
                row.status = str(task.status)
                row.created_at = task.created_at
                row.updated_at = task.updated_at
                row.progress = task.progress
                row.message = task.message
                row.error = task.error
                row.result_json = dict(task.result)
            session.add(row)
            session.flush()
            return self._task_from_row(row)

    def get_task(self, task_id: str) -> TaskRecord | None:
        """按 id 查询任务记录。
        Fetch a task record by id.
        """
        with self._session() as session:
            row = session.get(TaskRow, task_id)
            return self._task_from_row(row) if row else None

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
        with self._session() as session:
            row = session.get(TaskRow, task_id)
            if row is None:
                return None
            if status is not None:
                row.status = str(status)
            if progress is not None:
                row.progress = progress
            if message is not None:
                row.message = message
            if error is not None:
                row.error = error
            if result_updates:
                row.result_json = {**(row.result_json or {}), **result_updates}
            row.updated_at = datetime.now(timezone.utc)
            session.add(row)
            session.flush()
            return self._task_from_row(row)

    def add_facts(self, facts: list[FactRecord]) -> list[FactRecord]:
        """批量保存事实记录。
        Persist fact records in batch.
        """
        if not facts:
            return []

        with self._session() as session:
            session.add_all(self._fact_to_row(fact) for fact in facts)
            session.flush()
            affected_groups = {
                (fact.entity_type, fact.entity_name, fact.field_name, fact.year, fact.unit)
                for fact in facts
            }
            self._recompute_canonical_flags(session, affected_groups)
            reloaded = [
                session.get(FactRow, fact.fact_id)
                for fact in facts
            ]
            return [self._fact_from_row(row) for row in reloaded if row is not None]

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
        if document_ids is not None and not document_ids:
            return []

        with self._session() as session:
            stmt = select(FactRow)
            if entity_name is not None:
                stmt = stmt.where(FactRow.entity_name == entity_name)
            if field_name is not None:
                stmt = stmt.where(FactRow.field_name == field_name)
            if status is not None:
                stmt = stmt.where(FactRow.status == status)
            if min_confidence is not None:
                stmt = stmt.where(FactRow.confidence >= min_confidence)
            if canonical_only:
                stmt = stmt.where(FactRow.is_canonical.is_(True))
            if document_ids is not None:
                stmt = stmt.where(FactRow.source_doc_id.in_(sorted(document_ids)))
            stmt = stmt.order_by(FactRow.confidence.desc(), FactRow.fact_id.asc())
            return [self._fact_from_row(row) for row in session.scalars(stmt).all()]

    def get_fact(self, fact_id: str) -> FactRecord | None:
        """按 id 查询事实记录。
        Fetch a fact record by id.
        """
        with self._session() as session:
            row = session.get(FactRow, fact_id)
            return self._fact_from_row(row) if row else None

    def update_fact(
        self,
        fact_id: str,
        *,
        status: str | None = None,
        metadata_updates: dict[str, object] | None = None,
    ) -> FactRecord | None:
        """更新事实状态或元数据并重算 canonical 结果。    Update one fact and recompute canonical winners."""

        with self._session() as session:
            row = session.get(FactRow, fact_id)
            if row is None:
                return None
            if status is not None:
                row.status = status
            if metadata_updates:
                row.metadata_json = {**(row.metadata_json or {}), **metadata_updates}
            session.add(row)
            session.flush()
            affected_groups = {
                (row.entity_type, row.entity_name, row.field_name, row.year, row.unit),
            }
            self._recompute_canonical_flags(session, affected_groups)
            session.flush()
            return self._fact_from_row(row)

    def get_fact_block(self, fact_id: str) -> DocumentBlock | None:
        """获取事实对应的来源块。
        Fetch the source block referenced by a fact.
        """
        with self._session() as session:
            fact_row = session.get(FactRow, fact_id)
            if fact_row is None:
                return None
            stmt = select(DocumentBlockRow).where(
                DocumentBlockRow.doc_id == fact_row.source_doc_id,
                DocumentBlockRow.block_id == fact_row.source_block_id,
            )
            row = session.scalar(stmt)
            return self._block_from_row(row) if row else None

    def save_template_result(self, result: TemplateResultRecord) -> TemplateResultRecord:
        """保存模板回填结果。
        Persist a template fill result.
        """
        with self._session() as session:
            row = session.get(TemplateResultRow, result.task_id)
            if row is None:
                row = self._template_result_to_row(result)
            else:
                row.template_name = result.template_name
                row.output_path = result.output_path
                row.output_file_name = result.output_file_name
                row.created_at = result.created_at
                row.fill_mode = result.fill_mode
                row.document_ids = list(result.document_ids)
                row.filled_cells = [self._filled_cell_to_dict(cell) for cell in result.filled_cells]
            session.add(row)
            session.flush()
            return self._template_result_from_row(row)

    def get_template_result(self, task_id: str) -> TemplateResultRecord | None:
        """按任务 id 查询模板回填结果。
        Fetch a template fill result by task id.
        """
        with self._session() as session:
            row = session.get(TemplateResultRow, task_id)
            return self._template_result_from_row(row) if row else None

    def list_template_results(self) -> list[TemplateResultRecord]:
        """列出全部模板回填结果。
        List all template fill results.
        """
        with self._session() as session:
            stmt = select(TemplateResultRow).order_by(TemplateResultRow.created_at.desc())
            return [self._template_result_from_row(row) for row in session.scalars(stmt).all()]

    def _recompute_canonical_flags(
        self,
        session: Session,
        affected_groups: set[tuple[str, str, str, int | None, str | None]],
    ) -> None:
        """重算受影响冲突组的 canonical 事实标记。
        Recompute canonical fact flags for affected conflict groups.
        """
        for entity_type, entity_name, field_name, year, unit in affected_groups:
            conflict_group_id = f"{entity_type}::{entity_name}::{field_name}::{year}::{unit}"
            stmt = select(FactRow).where(
                FactRow.entity_type == entity_type,
                FactRow.entity_name == entity_name,
                FactRow.field_name == field_name,
            )
            # NULL-safe comparisons: SQL requires IS NULL instead of = NULL
            if year is None:
                stmt = stmt.where(FactRow.year.is_(None))
            else:
                stmt = stmt.where(FactRow.year == year)
            if unit is None:
                stmt = stmt.where(FactRow.unit.is_(None))
            else:
                stmt = stmt.where(FactRow.unit == unit)
            stmt = stmt.order_by(FactRow.confidence.desc(), FactRow.value_num.is_not(None).desc(), FactRow.source_doc_id.desc())
            rows = list(session.scalars(stmt).all())
            for row in rows:
                row.is_canonical = False
                row.conflict_group_id = conflict_group_id
                session.add(row)
            ordered_rows = [row for row in rows if row.status != "rejected"]
            for index, row in enumerate(ordered_rows):
                row.is_canonical = index == 0
                session.add(row)

    @staticmethod
    def _clone_document(record: DocumentRecord) -> DocumentRecord:
        """复制文档领域对象，避免共享可变引用。
        Clone a document domain object to avoid shared mutable references.
        """
        return DocumentRecord(
            doc_id=record.doc_id,
            file_name=record.file_name,
            stored_path=record.stored_path,
            doc_type=record.doc_type,
            upload_time=record.upload_time,
            status=record.status,
            metadata=dict(record.metadata),
        )

    @staticmethod
    def _document_to_row(record: DocumentRecord) -> DocumentRow:
        """将文档领域对象转换为 ORM 行对象。
        Convert a document domain object into an ORM row object.
        """
        return DocumentRow(
            doc_id=record.doc_id,
            file_name=record.file_name,
            stored_path=record.stored_path,
            doc_type=record.doc_type,
            upload_time=record.upload_time,
            status=str(record.status),
            metadata_json=dict(record.metadata),
        )

    @staticmethod
    def _document_from_row(row: DocumentRow) -> DocumentRecord:
        """将 ORM 文档行转换为领域对象。
        Convert an ORM document row into a domain object.
        """
        return DocumentRecord(
            doc_id=row.doc_id,
            file_name=row.file_name,
            stored_path=row.stored_path,
            doc_type=row.doc_type,
            upload_time=row.upload_time,
            status=DocumentStatus(row.status),
            metadata=dict(row.metadata_json or {}),
        )

    @staticmethod
    def _block_to_row(block: DocumentBlock) -> DocumentBlockRow:
        """将文档块领域对象转换为 ORM 行对象。
        Convert a document block domain object into an ORM row object.
        """
        return DocumentBlockRow(
            block_id=block.block_id,
            doc_id=block.doc_id,
            block_type=block.block_type,
            text=block.text,
            section_path=list(block.section_path),
            page_or_index=block.page_or_index,
            metadata_json=dict(block.metadata),
        )

    @staticmethod
    def _block_from_row(row: DocumentBlockRow) -> DocumentBlock:
        """将 ORM 文档块行转换为领域对象。
        Convert an ORM document block row into a domain object.
        """
        return DocumentBlock(
            block_id=row.block_id,
            doc_id=row.doc_id,
            block_type=row.block_type,
            text=row.text,
            section_path=list(row.section_path or []),
            page_or_index=row.page_or_index,
            metadata=dict(row.metadata_json or {}),
        )

    @staticmethod
    def _task_to_row(task: TaskRecord) -> TaskRow:
        """将任务领域对象转换为 ORM 行对象。
        Convert a task domain object into an ORM row object.
        """
        return TaskRow(
            task_id=task.task_id,
            task_type=str(task.task_type),
            status=str(task.status),
            created_at=task.created_at,
            updated_at=task.updated_at,
            progress=task.progress,
            message=task.message,
            error=task.error,
            result_json=dict(task.result),
        )

    @staticmethod
    def _task_from_row(row: TaskRow) -> TaskRecord:
        """将 ORM 任务行转换为领域对象。
        Convert an ORM task row into a domain object.
        """
        from app.models.domain import TaskType

        return TaskRecord(
            task_id=row.task_id,
            task_type=TaskType(row.task_type),
            status=TaskStatus(row.status),
            created_at=row.created_at,
            updated_at=row.updated_at,
            progress=row.progress,
            message=row.message,
            error=row.error,
            result=dict(row.result_json or {}),
        )

    @staticmethod
    def _fact_to_row(fact: FactRecord) -> FactRow:
        """将事实领域对象转换为 ORM 行对象。
        Convert a fact domain object into an ORM row object.
        """
        return FactRow(
            fact_id=fact.fact_id,
            entity_type=fact.entity_type,
            entity_name=fact.entity_name,
            field_name=fact.field_name,
            value_num=fact.value_num,
            value_text=fact.value_text,
            unit=fact.unit,
            year=fact.year,
            source_doc_id=fact.source_doc_id,
            source_block_id=fact.source_block_id,
            source_span=fact.source_span,
            confidence=fact.confidence,
            conflict_group_id=fact.conflict_group_id,
            is_canonical=fact.is_canonical,
            status=fact.status,
            metadata_json=dict(fact.metadata),
        )

    @staticmethod
    def _fact_from_row(row: FactRow) -> FactRecord:
        """将 ORM 事实行转换为领域对象。
        Convert an ORM fact row into a domain object.
        """
        return FactRecord(
            fact_id=row.fact_id,
            entity_type=row.entity_type,
            entity_name=row.entity_name,
            field_name=row.field_name,
            value_num=row.value_num,
            value_text=row.value_text,
            unit=row.unit,
            year=row.year,
            source_doc_id=row.source_doc_id,
            source_block_id=row.source_block_id,
            source_span=row.source_span,
            confidence=row.confidence,
            conflict_group_id=row.conflict_group_id,
            is_canonical=row.is_canonical,
            status=row.status,
            metadata=dict(row.metadata_json or {}),
        )

    @staticmethod
    def _template_result_to_row(result: TemplateResultRecord) -> TemplateResultRow:
        """将模板回填结果领域对象转换为 ORM 行对象。
        Convert a template result domain object into an ORM row object.
        """
        return TemplateResultRow(
            task_id=result.task_id,
            template_name=result.template_name,
            output_path=result.output_path,
            output_file_name=result.output_file_name,
            created_at=result.created_at,
            fill_mode=result.fill_mode,
            document_ids=list(result.document_ids),
            filled_cells=[PostgresRepository._filled_cell_to_dict(cell) for cell in result.filled_cells],
        )

    @staticmethod
    def _template_result_from_row(row: TemplateResultRow) -> TemplateResultRecord:
        """将 ORM 模板回填结果行转换为领域对象。
        Convert an ORM template result row into a domain object.
        """
        return TemplateResultRecord(
            task_id=row.task_id,
            template_name=row.template_name,
            output_path=row.output_path,
            output_file_name=row.output_file_name,
            created_at=row.created_at,
            fill_mode=row.fill_mode,
            document_ids=list(row.document_ids or []),
            filled_cells=[
                FilledCellRecord(**cell_dict)
                for cell_dict in (row.filled_cells or [])
            ],
        )

    @staticmethod
    def _filled_cell_to_dict(cell: FilledCellRecord) -> dict[str, object]:
        """将单元格回填记录转换为 JSON 可序列化字典。
        Convert a filled-cell record into a JSON-serializable dictionary.
        """
        return asdict(cell)
