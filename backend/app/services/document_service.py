from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from app.core.config import Settings
from app.core.logging import ErrorCode, get_logger
from app.models.domain import DocumentRecord, DocumentStatus, TaskRecord, TaskStatus, TaskType
from app.parsers.factory import ParserRegistry
from app.repositories.base import Repository
from app.services.fact_extraction import FactExtractionService
from app.tasks.executor import TaskExecutor
from app.utils.files import safe_filename
from app.utils.ids import new_id


class DocumentService:
    """处理文档上传、解析任务和事实抽取编排。
    Handle document uploads, parsing tasks and fact extraction orchestration.
    """

    _logger = get_logger("document_service")

    def __init__(
        self,
        repository: Repository,
        parser_registry: ParserRegistry,
        extraction_service: FactExtractionService,
        executor: TaskExecutor,
        settings: Settings,
        embedding_service: object | None = None,
    ) -> None:
        """初始化文档处理流程所需依赖。
        Initialize dependencies required for document-processing workflows.
        """
        self._repository = repository
        self._parser_registry = parser_registry
        self._extraction_service = extraction_service
        self._executor = executor
        self._settings = settings
        self._embedding_service = embedding_service

    def upload_document(
        self,
        file_name: str,
        content: bytes,
        document_set_id: str | None = None,
    ) -> tuple[DocumentRecord, TaskRecord]:
        """保存上传文件并加入异步解析流程。
        Persist an uploaded file and enqueue asynchronous parsing work.
        """
        suffix = Path(file_name).suffix.lower()
        if suffix not in self._settings.supported_document_extensions:
            raise ValueError(f"Unsupported document type: {suffix}")

        doc_id = new_id("doc")
        stored_name = f"{doc_id}_{safe_filename(file_name)}"
        stored_path = self._settings.uploads_dir / stored_name
        stored_path.write_bytes(content)
        document_metadata = self._build_document_metadata(file_name, content, document_set_id)

        document = DocumentRecord(
            doc_id=doc_id,
            file_name=file_name,
            stored_path=str(stored_path),
            doc_type=suffix.lstrip("."),
            upload_time=datetime.now(timezone.utc),
            status=DocumentStatus.uploaded,
            metadata=document_metadata,
        )
        task = TaskRecord(
            task_id=new_id("task"),
            task_type=TaskType.parse_document,
            status=TaskStatus.queued,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            message="Document received and queued for parsing.",
            result={"document_id": doc_id},
        )

        self._repository.add_document(document)
        self._repository.upsert_task(task)
        self._executor.submit(task.task_id, self._process_document, document.doc_id, stored_path, task.task_id)
        return document, task

    def delete_document(self, doc_id: str) -> DocumentRecord:
        """删除文档及其关联数据，同时清理物理文件。
        Delete a document, cascade-remove associated data and clean up the stored file.
        """
        record = self._repository.delete_document(doc_id)
        if record is None:
            raise ValueError(f"Document not found: {doc_id}")
        stored = Path(record.stored_path)
        if stored.exists():
            stored.unlink(missing_ok=True)
        return record

    def list_documents(self) -> list[DocumentRecord]:
        """返回仓储中当前全部文档。
        Return all documents currently stored in the repository.
        """
        return self._repository.list_documents()

    def get_document(self, doc_id: str) -> DocumentRecord | None:
        """按 id 查询文档。
        Fetch a document by id.
        """
        return self._repository.get_document(doc_id)

    def get_document_facts(
        self,
        doc_id: str,
        *,
        canonical_only: bool = False,
        status: str | None = None,
        min_confidence: float | None = None,
    ) -> list:
        """返回指定文档关联的事实记录。
        Return all fact records associated with a document.
        """
        return self._repository.list_facts(
            canonical_only=canonical_only,
            document_ids={doc_id},
            status=status,
            min_confidence=min_confidence,
        )

    def _process_document(self, doc_id: str, file_path: Path, task_id: str) -> None:
        """解析单个上传文档、抽取事实并更新任务状态。
        Parse one uploaded document, extract facts and update task state.
        """
        self._logger.info("document_parse started", extra={"doc_id": doc_id, "task_id": task_id})
        started_at = perf_counter()
        self._repository.update_document(doc_id, status=DocumentStatus.parsing)
        self._repository.update_task(
            task_id,
            status=TaskStatus.running,
            progress=0.1,
            message="Parsing document structure.",
        )
        try:
            parse_started_at = perf_counter()
            blocks = self._parser_registry.parse(file_path, doc_id)
            parse_elapsed = round(perf_counter() - parse_started_at, 3)
            self._repository.replace_blocks(doc_id, blocks)
            self._repository.update_task(
                task_id,
                progress=0.55,
                message=f"Parsed {len(blocks)} blocks in {parse_elapsed:.2f}s, extracting facts.",
                result_updates={
                    "block_count": len(blocks),
                    "parse_seconds": parse_elapsed,
                },
            )

            document = self._repository.get_document(doc_id)
            if document is None:
                raise RuntimeError(f"Document {doc_id} disappeared during parsing.")

            if self._should_skip_fact_extraction(document, block_count=len(blocks)):
                total_elapsed = round(perf_counter() - started_at, 3)
                self._repository.update_document(
                    doc_id,
                    status=DocumentStatus.parsed,
                    metadata_updates={
                        "block_count": len(blocks),
                        "fact_count": 0,
                        "parse_seconds": parse_elapsed,
                        "total_seconds": total_elapsed,
                        "processing_note": "Fact extraction skipped (instruction text or large spreadsheet).",
                    },
                )
                self._repository.update_task(
                    task_id,
                    status=TaskStatus.succeeded,
                    progress=1.0,
                    message=f"Document parsed in {total_elapsed:.2f}s, fact extraction skipped.",
                    result_updates={
                        "fact_count": 0,
                        "total_seconds": total_elapsed,
                        "skipped_fact_extraction": True,
                    },
                )
                return

            extraction_started_at = perf_counter()
            facts = self._extraction_service.extract(document, blocks)
            extraction_elapsed = round(perf_counter() - extraction_started_at, 3)
            storage_started_at = perf_counter()
            stored_facts = self._repository.add_facts(facts)
            storage_elapsed = round(perf_counter() - storage_started_at, 3)
            total_elapsed = round(perf_counter() - started_at, 3)
            self._repository.update_document(
                doc_id,
                status=DocumentStatus.parsed,
                metadata_updates={
                    "block_count": len(blocks),
                    "fact_count": len(stored_facts),
                    "parse_seconds": parse_elapsed,
                    "extract_seconds": extraction_elapsed,
                    "store_seconds": storage_elapsed,
                    "total_seconds": total_elapsed,
                },
            )
            self._repository.update_task(
                task_id,
                status=TaskStatus.succeeded,
                progress=1.0,
                message=f"Document parsed successfully in {total_elapsed:.2f}s.",
                result_updates={
                    "fact_count": len(stored_facts),
                    "extract_seconds": extraction_elapsed,
                    "store_seconds": storage_elapsed,
                    "total_seconds": total_elapsed,
                },
            )
            self._logger.info(
                f"document_parse completed in {total_elapsed:.2f}s",
                extra={"doc_id": doc_id, "task_id": task_id, "duration_ms": round(total_elapsed * 1000, 1)},
            )

            # 后台异步生成向量嵌入（不阻塞解析管道，不影响 parsed 状态）
            self._embed_blocks_async(blocks, file_name=document.file_name)
        except Exception as exc:
            self._logger.error(
                f"document_parse failed: {exc}",
                extra={"doc_id": doc_id, "task_id": task_id, "error_code": ErrorCode.PARSE_READ_FAILURE},
            )
            self._repository.update_document(doc_id, status=DocumentStatus.failed)
            self._repository.update_task(
                task_id,
                status=TaskStatus.failed,
                progress=1.0,
                message="Document parsing failed.",
                error=str(exc),
            )

    def _embed_blocks_async(self, blocks: list, *, file_name: str = "") -> None:
        """在后台线程中为文档块生成向量嵌入，不阻塞主解析流程。"""
        if self._embedding_service is None or not blocks:
            return

        def _run() -> None:
            try:
                count = self._embedding_service.embed_blocks(blocks, file_name=file_name)
                self._logger.info("Background embedding completed: %d blocks (file=%s)", count, file_name)
            except Exception as exc:
                self._logger.warning("Background embedding failed: %s", exc)

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def _build_document_metadata(
        self,
        file_name: str,
        content: bytes,
        document_set_id: str | None,
    ) -> dict[str, object]:
        """构建文档元数据并识别测试集中的提示词 TXT。
        Build document metadata and detect prompt/instruction TXT files from the competition dataset.
        """

        metadata: dict[str, object] = {"document_set_id": document_set_id} if document_set_id else {}
        if self._is_instruction_text(file_name, content):
            metadata.update(
                {
                    "document_role": "prompt_instruction",
                    "skip_fact_extraction": True,
                }
            )
        else:
            metadata.setdefault("document_role", "source_document")
        return metadata

    def _is_instruction_text(self, file_name: str, content: bytes) -> bool:
        """判断 TXT 文件是否更像比赛附带的提示词说明，而不是事实来源文档。
        Decide whether a TXT file looks like competition prompt instructions instead of a source document.
        """

        normalized_name = Path(file_name).name.lower()
        if not normalized_name.endswith(".txt"):
            return False
        if normalized_name in {"readme.txt", "README.txt".lower()}:
            return True

        preview = content[:2048].decode("utf-8", errors="ignore").lower()
        instruction_markers = [
            "提示词",
            "prompt",
            "根据以下要求",
            "请根据",
            "输出要求",
            "填报说明",
        ]
        if "README" in file_name.upper():
            return True
        return any(marker in preview for marker in instruction_markers)

    def _should_skip_fact_extraction(self, document: DocumentRecord, *, block_count: int = 0) -> bool:
        """判断该文档是否应跳过事实抽取。
        Return whether fact extraction should be skipped for this document.
        Skip when: (1) metadata explicitly says so (prompt/instruction text),
        or (2) large spreadsheet (>5000 blocks) — direct_search uses blocks directly.
        """
        if document.metadata.get("skip_fact_extraction"):
            return True
        # Large spreadsheets: fact extraction is very slow and unnecessary;
        # direct_search will use block metadata (row_values) directly.
        if document.doc_type in ("xlsx", "xls", "csv") and block_count > 2000:
            self._logger.info(
                "Skipping fact extraction for large spreadsheet: doc_type=%s, blocks=%d",
                document.doc_type, block_count,
            )
            return True
        return False
