from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from app.core.config import Settings
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

    def __init__(
        self,
        repository: Repository,
        parser_registry: ParserRegistry,
        extraction_service: FactExtractionService,
        executor: TaskExecutor,
        settings: Settings,
    ) -> None:
        """初始化文档处理流程所需依赖。
        Initialize dependencies required for document-processing workflows.
        """
        self._repository = repository
        self._parser_registry = parser_registry
        self._extraction_service = extraction_service
        self._executor = executor
        self._settings = settings

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

    def get_document_blocks(self, doc_id: str) -> list:
        """返回指定文档的全部解析块。
        Return all parsed blocks for a document.
        """
        return self._repository.list_blocks(doc_id)

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

            if self._should_skip_fact_extraction(document):
                total_elapsed = round(perf_counter() - started_at, 3)
                self._repository.update_document(
                    doc_id,
                    status=DocumentStatus.parsed,
                    metadata_updates={
                        "block_count": len(blocks),
                        "fact_count": 0,
                        "parse_seconds": parse_elapsed,
                        "total_seconds": total_elapsed,
                        "processing_note": "Prompt/instruction text detected, skipped fact extraction.",
                    },
                )
                self._repository.update_task(
                    task_id,
                    status=TaskStatus.succeeded,
                    progress=1.0,
                    message="Instruction text parsed and excluded from fact extraction.",
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
        except Exception as exc:
            self._repository.update_document(doc_id, status=DocumentStatus.failed)
            self._repository.update_task(
                task_id,
                status=TaskStatus.failed,
                progress=1.0,
                message="Document parsing failed.",
                error=str(exc),
            )

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
        return any(marker in preview for marker in instruction_markers) and "README" in file_name.upper()

    def _should_skip_fact_extraction(self, document: DocumentRecord) -> bool:
        """判断该文档是否应跳过事实抽取。
        Return whether fact extraction should be skipped for this document.
        """

        return bool(document.metadata.get("skip_fact_extraction"))
