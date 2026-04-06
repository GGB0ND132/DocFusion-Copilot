from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类。
    SQLAlchemy declarative base class.
    """


class DocumentRow(Base):
    """文档表 ORM 模型。
    ORM model for the documents table.
    """

    __tablename__ = "documents"

    doc_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    doc_type: Mapped[str] = mapped_column(String(32), nullable=False)
    upload_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class DocumentBlockRow(Base):
    """文档块表 ORM 模型。
    ORM model for the document_blocks table.
    """

    __tablename__ = "document_blocks"

    block_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("documents.doc_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    block_type: Mapped[str] = mapped_column(String(64), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    section_path: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    page_or_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)


class FactRow(Base):
    """事实表 ORM 模型。
    ORM model for the facts table.
    """

    __tablename__ = "facts"

    fact_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_name: Mapped[str] = mapped_column(String(255), nullable=False)
    field_name: Mapped[str] = mapped_column(String(255), nullable=False)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str] = mapped_column(Text, nullable=False)
    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_doc_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("documents.doc_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_block_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    source_span: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    conflict_group_id: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)
    is_canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, index=True)
    status: Mapped[str] = mapped_column(String(64), nullable=False, default="confirmed")
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)

    __table_args__ = (
        Index("ix_facts_lookup", "entity_name", "field_name", "year", "unit"),
        Index("ix_facts_group_lookup_model", "entity_type", "entity_name", "field_name", "year", "unit"),
    )


class TaskRow(Base):
    """任务表 ORM 模型。
    ORM model for the tasks table.
    """

    __tablename__ = "tasks"

    task_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    message: Mapped[str] = mapped_column(Text, nullable=False, default="")
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column("result", JSONB, nullable=False, default=dict)


class TemplateResultRow(Base):
    """模板回填结果表 ORM 模型。
    ORM model for the template_results table.
    """

    __tablename__ = "template_results"

    task_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("tasks.task_id", ondelete="CASCADE"),
        primary_key=True,
    )
    template_name: Mapped[str] = mapped_column(String(512), nullable=False)
    output_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    output_file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    fill_mode: Mapped[str] = mapped_column(String(64), nullable=False)
    document_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    filled_cells: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)


class ConversationRow(Base):
    """瀵硅瘽琛?ORM 妯″瀷銆?
    ORM model for the conversations table.
    """

    __tablename__ = "conversations"

    conversation_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    messages: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    metadata_json: Mapped[dict[str, Any]] = mapped_column("metadata", JSONB, nullable=False, default=dict)
