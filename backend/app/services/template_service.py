from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from app.core.config import Settings
from app.core.openai_client import OpenAIClientError, OpenAICompatibleClient
from app.models.domain import (
    DocumentRecord,
    DocumentStatus,
    FilledCellRecord,
    TaskRecord,
    TaskStatus,
    TaskType,
    TemplateResultRecord,
)
from app.repositories.base import Repository
from app.tasks.executor import TaskExecutor
from app.utils.files import safe_filename
from app.utils.ids import new_id
from app.utils.normalizers import (
    find_entity_mentions,
    format_value,
    is_entity_column,
    normalize_entity_name,
    normalize_field_name,
)
from app.utils.spreadsheet import CellWrite, SpreadsheetDocument, SpreadsheetSheet, apply_xlsx_updates, build_cell_ref, load_xlsx
from app.utils.wordprocessing import WordCellWrite, WordDocument, WordTable, apply_docx_updates, load_docx_tables

_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]{2,24}")
_GENERIC_TEMPLATE_TOKENS = frozenset(
    {
        "template",
        "doc",
        "docx",
        "xlsx",
        "sheet",
        "table",
        "report",
        "form",
        "output",
        "upload",
        "模板",
        "表格",
        "填表",
        "回填",
        "汇总",
        "统计",
        "结果",
        "数据",
        "文档",
    }
)


class TemplateService:
    """处理模板上传、模板理解、文档匹配和单元格级回填。
    Handle template uploads, template understanding, document matching and cell-level filling.
    """

    def __init__(
        self,
        repository: Repository,
        executor: TaskExecutor,
        settings: Settings,
        openai_client: OpenAICompatibleClient,
    ) -> None:
        """初始化模板回填与匹配流程所需依赖。
        Initialize dependencies used by template filling and matching workflows.
        """
        self._repository = repository
        self._executor = executor
        self._settings = settings
        self._openai_client = openai_client

    def submit_fill_task(
        self,
        *,
        template_name: str,
        content: bytes,
        fill_mode: str = "canonical",
        document_set_id: str | None = None,
        document_ids: list[str] | None = None,
        auto_match: bool = True,
    ) -> TaskRecord:
        """保存模板上传内容并加入异步回填队列。
        Persist a template upload and enqueue asynchronous filling work.
        """
        suffix = Path(template_name).suffix.lower()
        if suffix not in self._settings.supported_template_extensions:
            raise ValueError(f"Unsupported template type: {suffix}")

        task = TaskRecord(
            task_id=new_id("task"),
            task_type=TaskType.fill_template,
            status=TaskStatus.queued,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            message="Template received and queued for filling.",
            result={
                "template_name": template_name,
                "document_set_id": document_set_id,
                "requested_document_ids": list(document_ids or []),
                "auto_match": auto_match,
            },
        )

        stored_name = f"{task.task_id}_{safe_filename(template_name)}"
        template_path = self._settings.temp_dir / stored_name
        template_path.write_bytes(content)

        self._repository.upsert_task(task)
        self._executor.submit(
            task.task_id,
            self._fill_template,
            task.task_id,
            template_name,
            template_path,
            fill_mode,
            document_set_id,
            list(document_ids or []),
            auto_match,
        )
        return task

    def get_result(self, task_id: str) -> TemplateResultRecord | None:
        """返回回填任务对应的完成结果。
        Return the completed result associated with a fill task.
        """
        return self._repository.get_template_result(task_id)

    def fill_template_once(
        self,
        *,
        task_id: str,
        template_name: str,
        template_path: Path,
        fill_mode: str,
        document_ids: list[str],
        output_file_name: str | None = None,
        persist_result: bool = True,
    ) -> TemplateResultRecord:
        """同步执行一次模板回填并返回结果。
        Execute one template fill synchronously and return the result.
        """
        suffix = template_path.suffix.lower()
        if suffix not in {".xlsx", ".docx"}:
            raise ValueError(f"Unsupported template type: {suffix}")

        facts = self._repository.list_facts(canonical_only=(fill_mode == "canonical"), document_ids=set(document_ids))
        fact_lookup = self._build_fact_lookup(facts)
        unique_entities = self._build_entity_fill_order(document_ids, facts)
        resolved_output_file_name = output_file_name or f"{task_id}_{safe_filename(template_name)}"
        output_path = self._settings.outputs_dir / resolved_output_file_name

        if suffix == ".xlsx":
            filled_cells = self._fill_xlsx_template(
                template_path=template_path,
                output_path=output_path,
                fact_lookup=fact_lookup,
                unique_entities=unique_entities,
            )
        else:
            filled_cells = self._fill_docx_template(
                template_path=template_path,
                output_path=output_path,
                fact_lookup=fact_lookup,
                unique_entities=unique_entities,
            )

        result = TemplateResultRecord(
            task_id=task_id,
            template_name=template_name,
            output_path=str(output_path),
            output_file_name=resolved_output_file_name,
            created_at=datetime.now(timezone.utc),
            fill_mode=fill_mode,
            document_ids=document_ids,
            filled_cells=filled_cells,
        )
        if persist_result:
            self._repository.save_template_result(result)
        return result

    def resolve_document_ids(
        self,
        document_set_id: str | None,
        document_ids: list[str] | None,
    ) -> list[str]:
        """将显式文档列表或文档批次解析为具体文档 id 列表。
        Resolve an explicit document list or document batch into concrete document ids.
        """
        parsed_documents = self._repository.list_documents(status=DocumentStatus.parsed)
        if document_ids:
            allowed_ids = {document.doc_id for document in parsed_documents}
            return [doc_id for doc_id in document_ids if doc_id in allowed_ids]
        if document_set_id and document_set_id not in {"default", "all"}:
            scoped_ids = [
                document.doc_id
                for document in parsed_documents
                if str(document.metadata.get("document_set_id", "")).strip() == document_set_id
            ]
            if scoped_ids:
                return scoped_ids
            split_ids = [item.strip() for item in document_set_id.split(",") if item.strip()]
            if split_ids:
                allowed_ids = {document.doc_id for document in parsed_documents}
                return [doc_id for doc_id in split_ids if doc_id in allowed_ids]
        return [document.doc_id for document in parsed_documents]

    def _fill_template(
        self,
        task_id: str,
        template_name: str,
        template_path: Path,
        fill_mode: str,
        document_set_id: str | None,
        document_ids: list[str],
        auto_match: bool,
    ) -> None:
        """执行单个排队模板任务的文档匹配与回填流程。
        Execute the document-matching and fill pipeline for one queued template task.
        """
        self._repository.update_task(
            task_id,
            status=TaskStatus.running,
            progress=0.05,
            message="Analysing template structure.",
        )
        started_at = perf_counter()
        try:
            matched_document_ids, match_payload = self._resolve_fill_documents(
                template_name=template_name,
                template_path=template_path,
                document_set_id=document_set_id,
                explicit_document_ids=document_ids,
                auto_match=auto_match,
            )
            self._repository.update_task(
                task_id,
                progress=0.3,
                message="Matched template to candidate source documents.",
                result_updates=match_payload,
            )
            result = self.fill_template_once(
                task_id=task_id,
                template_name=template_name,
                template_path=template_path,
                fill_mode=fill_mode,
                document_ids=matched_document_ids,
                output_file_name=f"{task_id}_{safe_filename(template_name)}",
                persist_result=True,
            )
            elapsed_seconds = round(perf_counter() - started_at, 4)
            self._repository.update_task(
                task_id,
                status=TaskStatus.succeeded,
                progress=1.0,
                message="Template filled successfully.",
                result_updates={
                    **match_payload,
                    "output_file_name": result.output_file_name,
                    "filled_cells": len(result.filled_cells),
                    "elapsed_seconds": elapsed_seconds,
                },
            )
        except Exception as exc:
            self._repository.update_task(
                task_id,
                status=TaskStatus.failed,
                progress=1.0,
                message="Template filling failed.",
                error=str(exc),
            )

    def _resolve_fill_documents(
        self,
        *,
        template_name: str,
        template_path: Path,
        document_set_id: str | None,
        explicit_document_ids: list[str],
        auto_match: bool,
    ) -> tuple[list[str], dict[str, object]]:
        """为一次模板回填任务解析最终文档范围。
        Resolve the final document scope for one template-fill task.
        """
        if explicit_document_ids:
            resolved_ids = self.resolve_document_ids(None, explicit_document_ids)
            if not resolved_ids:
                raise ValueError("No parsed documents matched the explicit document_ids.")
            return resolved_ids, {
                "match_mode": "explicit",
                "matched_document_ids": resolved_ids,
                "match_candidates": [],
            }

        candidate_ids = self.resolve_document_ids(document_set_id, None)
        if not candidate_ids:
            raise ValueError("No parsed documents are available for template filling.")

        if not auto_match:
            return candidate_ids, {
                "match_mode": "scope_all",
                "matched_document_ids": candidate_ids,
                "match_candidates": [],
            }

        profile = self._build_template_profile(template_name, template_path)
        candidates = self._build_document_match_cards(candidate_ids)
        matched_ids, match_mode, reason, match_candidates = self._match_documents(profile, candidates)
        return matched_ids, {
            "template_profile": profile,
            "match_mode": match_mode,
            "match_reason": reason,
            "matched_document_ids": matched_ids,
            "match_candidates": match_candidates,
        }

    def _build_template_profile(self, template_name: str, template_path: Path) -> dict[str, object]:
        """提取模板名称、字段、实体和文本提示，供匹配与解释使用。
        Extract template name, fields, entities and text hints for matching and explainability.
        """
        texts: list[str] = [Path(template_name).stem]
        field_names: set[str] = set()
        entity_names: set[str] = set()
        suffix = template_path.suffix.lower()

        if suffix == ".xlsx":
            document = load_xlsx(template_path)
            texts.extend(self._collect_xlsx_texts(document))
            field_names.update(self._extract_xlsx_field_names(document))
            entity_names.update(self._extract_xlsx_entity_names(document))
        else:
            document = load_docx_tables(template_path)
            texts.extend(self._collect_docx_texts(document))
            field_names.update(self._extract_docx_field_names(document))
            entity_names.update(self._extract_docx_entity_names(document))

        merged_text = "\n".join(texts)
        entity_names.update(find_entity_mentions(merged_text))
        keywords = self._extract_keywords(texts)
        sample_texts = [text for text in texts if text][:12]
        return {
            "template_name": template_name,
            "template_type": suffix.lstrip("."),
            "field_names": sorted(field_names),
            "entity_names": sorted(entity_names),
            "keywords": keywords[:20],
            "sample_texts": sample_texts,
        }

    def _match_documents(
        self,
        profile: dict[str, object],
        candidates: list[dict[str, object]],
    ) -> tuple[list[str], str, str, list[dict[str, object]]]:
        """根据模板画像和候选文档摘要选择最相关的文档。
        Select the most relevant documents from candidate summaries using the template profile.
        """
        if len(candidates) == 1:
            doc_id = str(candidates[0]["doc_id"])
            return [doc_id], "single_candidate", "Only one parsed document is available.", candidates

        if self._openai_client.is_configured:
            try:
                return self._match_documents_with_openai(profile, candidates)
            except OpenAIClientError:
                pass
        return self._match_documents_with_rules(profile, candidates)

    def _match_documents_with_openai(
        self,
        profile: dict[str, object],
        candidates: list[dict[str, object]],
    ) -> tuple[list[str], str, str, list[dict[str, object]]]:
        """使用 OpenAI-compatible 接口做模板到文档的语义匹配。
        Use an OpenAI-compatible API to semantically match a template to documents.
        """
        payload = self._openai_client.create_json_completion(
            system_prompt=(
                "你是文档融合系统中的模板匹配器。"
                "你的任务是根据模板名称、模板表头和样本文本，选择最相关的源文档。"
                "如果模板需要跨多个文档汇总，则可以返回多个 document_ids。"
                "禁止返回候选列表中不存在的 document_id。"
            ),
            user_prompt=(
                f"模板画像:\n{profile}\n\n"
                f"候选文档摘要:\n{candidates}\n\n"
                '请输出 JSON: {"document_ids": ["..."], "reason": "..."}'
            ),
            json_schema={
                "type": "object",
                "properties": {
                    "document_ids": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": ["document_ids", "reason"],
                "additionalProperties": False,
            },
        )
        candidate_id_set = {str(item["doc_id"]) for item in candidates}
        matched_ids = [
            str(doc_id)
            for doc_id in payload.get("document_ids", [])
            if isinstance(doc_id, str) and doc_id in candidate_id_set
        ]
        if not matched_ids:
            return self._match_documents_with_rules(profile, candidates)
        reason = str(payload.get("reason", "")).strip() or "Matched by OpenAI."
        return matched_ids, "openai", reason, candidates

    def _match_documents_with_rules(
        self,
        profile: dict[str, object],
        candidates: list[dict[str, object]],
    ) -> tuple[list[str], str, str, list[dict[str, object]]]:
        """使用可解释的规则分数挑选相关文档。
        Pick relevant documents with explainable rule-based scoring.
        """
        desired_fields = {str(value) for value in profile.get("field_names", []) if str(value)}
        desired_entities = {normalize_entity_name(str(value)) for value in profile.get("entity_names", []) if str(value)}
        desired_keywords = [str(value) for value in profile.get("keywords", []) if str(value)]

        scored_candidates: list[dict[str, object]] = []
        for candidate in candidates:
            field_hits = sorted(desired_fields & set(candidate.get("field_names", [])))
            entity_hits = sorted(desired_entities & {normalize_entity_name(name) for name in candidate.get("entity_names", [])})
            keyword_hits = [
                keyword
                for keyword in desired_keywords
                if keyword and (
                    keyword.lower() in str(candidate.get("file_name", "")).lower()
                    or keyword.lower() in str(candidate.get("text_preview", "")).lower()
                )
            ]
            score = 3.0 * len(field_hits) + 4.0 * len(entity_hits) + min(2.0, 0.5 * len(keyword_hits))
            scored_candidate = dict(candidate)
            scored_candidate["score"] = round(score, 4)
            scored_candidate["field_hits"] = field_hits
            scored_candidate["entity_hits"] = entity_hits
            scored_candidate["keyword_hits"] = keyword_hits[:8]
            scored_candidates.append(scored_candidate)

        scored_candidates.sort(key=lambda item: (float(item["score"]), str(item["doc_id"])), reverse=True)
        matched = [item for item in scored_candidates if item["entity_hits"]]
        if not matched and desired_fields:
            matched = [item for item in scored_candidates if item["field_hits"]]
        if not matched:
            positive = [item for item in scored_candidates if float(item["score"]) > 0]
            if positive:
                cutoff = max(float(positive[0]["score"]) * 0.5, 1.0)
                matched = [item for item in positive if float(item["score"]) >= cutoff]

        if not matched:
            return (
                [str(item["doc_id"]) for item in scored_candidates],
                "fallback_all",
                "No strong match signal was found, so all scoped parsed documents were used.",
                scored_candidates,
            )

        if desired_entities:
            reason = "Matched documents share explicit entity names found in the template."
        elif desired_fields:
            reason = "Matched documents expose canonical facts for the fields requested by the template."
        else:
            reason = "Matched documents scored highest on template-name and content hints."
        return [str(item["doc_id"]) for item in matched], "rules", reason, scored_candidates

    def _build_document_match_cards(self, document_ids: list[str]) -> list[dict[str, object]]:
        """为候选文档构建可序列化的匹配摘要卡片。
        Build serializable matching summary cards for candidate documents.
        """
        cards: list[dict[str, object]] = []
        for doc_id in document_ids:
            document = self._repository.get_document(doc_id)
            if document is not None:
                cards.append(self._build_document_match_card(document))
        return cards

    def _build_document_match_card(self, document: DocumentRecord) -> dict[str, object]:
        """汇总单个文档的事实字段、实体和内容预览。
        Summarize fact fields, entities and content previews for one document.
        """
        facts = self._repository.list_facts(canonical_only=True, document_ids={document.doc_id})
        blocks = self._repository.list_blocks(document.doc_id)
        return {
            "doc_id": document.doc_id,
            "file_name": document.file_name,
            "doc_type": document.doc_type,
            "document_set_id": document.metadata.get("document_set_id"),
            "field_names": sorted({fact.field_name for fact in facts}),
            "entity_names": sorted({fact.entity_name for fact in facts if fact.entity_name}),
            "fact_count": len(facts),
            "block_count": len(blocks),
            "text_preview": "\n".join(block.text[:120] for block in blocks[:4]),
        }

    def _collect_xlsx_texts(self, document: SpreadsheetDocument) -> list[str]:
        """提取模板工作簿中的文本单元格，用于字段识别和匹配。
        Collect textual workbook cells for field detection and matching.
        """
        values: list[str] = []
        for sheet in document.sheets:
            values.append(sheet.name)
            for row in sheet.rows[:20]:
                for value in row.values[:12]:
                    stripped = str(value).strip()
                    if stripped:
                        values.append(stripped)
        return values

    def _collect_docx_texts(self, document: WordDocument) -> list[str]:
        """提取模板 Word 表格中的可见文本，用于字段识别和匹配。
        Collect visible DOCX table text for field detection and matching.
        """
        values: list[str] = []
        for table in document.tables:
            values.append(table.name)
            for row in table.rows[:20]:
                for value in row.values[:12]:
                    stripped = str(value).strip()
                    if stripped:
                        values.append(stripped)
        return values

    def _extract_xlsx_field_names(self, document: SpreadsheetDocument) -> set[str]:
        """从 XLSX 模板中提取标准字段名。
        Extract canonical field names from an XLSX template.
        """
        field_names: set[str] = set()
        for sheet in document.sheets:
            for row in sheet.rows[:10]:
                for value in row.values:
                    field_name = normalize_field_name(value)
                    if field_name:
                        field_names.add(field_name)
        return field_names

    def _extract_docx_field_names(self, document: WordDocument) -> set[str]:
        """从 DOCX 模板中提取标准字段名。
        Extract canonical field names from a DOCX template.
        """
        field_names: set[str] = set()
        for table in document.tables:
            for row in table.rows[:10]:
                for value in row.values:
                    field_name = normalize_field_name(value)
                    if field_name:
                        field_names.add(field_name)
        return field_names

    def _extract_xlsx_entity_names(self, document: SpreadsheetDocument) -> set[str]:
        """从 XLSX 模板中的实体列和文本提示提取实体名。
        Extract entity names from entity columns and text hints in an XLSX template.
        """
        entities: set[str] = set()
        for sheet in document.sheets:
            header_row, entity_column, _ = self._detect_layout(sheet)
            if header_row is None or entity_column is None:
                continue
            for row in sheet.rows:
                if row.row_index <= header_row or len(row.values) < entity_column:
                    continue
                value = row.values[entity_column - 1].strip()
                if value:
                    entities.add(normalize_entity_name(value))
        return {entity for entity in entities if entity}

    def _extract_docx_entity_names(self, document: WordDocument) -> set[str]:
        """从 DOCX 模板中的实体列和文本提示提取实体名。
        Extract entity names from entity columns and text hints in a DOCX template.
        """
        entities: set[str] = set()
        for table in document.tables:
            header_row, entity_column, _ = self._detect_layout(table)
            if header_row is None or entity_column is None:
                continue
            for row in table.rows:
                if row.row_index <= header_row or len(row.values) < entity_column:
                    continue
                value = row.values[entity_column - 1].strip()
                if value:
                    entities.add(normalize_entity_name(value))
        return {entity for entity in entities if entity}

    def _extract_keywords(self, values: list[str]) -> list[str]:
        """从模板名称和内容中提取简短关键词，供规则匹配使用。
        Extract short keywords from template names and contents for rule matching.
        """
        keywords: list[str] = []
        seen: set[str] = set()
        for value in values:
            normalized_value = str(value or "").replace("_", " ").replace("-", " ")
            for token in _TOKEN_RE.findall(normalized_value):
                lowered = token.lower()
                if lowered.isdigit() or lowered in seen or lowered in _GENERIC_TEMPLATE_TOKENS:
                    continue
                seen.add(lowered)
                keywords.append(token)
        return keywords

    def _build_fact_lookup(self, facts: list[object]) -> dict[tuple[str, str], object]:
        """构建实体字段到事实的最高置信度索引。
        Build a highest-confidence fact index keyed by entity and field.
        """
        fact_lookup: dict[tuple[str, str], object] = {}
        for fact in sorted(facts, key=lambda item: item.confidence, reverse=True):
            fact_lookup.setdefault((fact.entity_name, fact.field_name), fact)
        return fact_lookup

    def _entity_has_matching_fields(
        self,
        entity_name: str,
        field_columns: list[tuple[int, str]],
        fact_lookup: dict[tuple[str, str], object],
    ) -> bool:
        """判断实体是否至少能填充当前模板中的一个目标字段。    Return whether an entity can fill at least one requested field in the current template."""

        return any((entity_name, field_name) in fact_lookup for _column_index, field_name in field_columns)

    def _build_entity_fill_order(self, document_ids: list[str], facts: list[object]) -> list[str]:
        """按文档与块出现顺序生成实体回填顺序。    Build the entity fill order based on document and block appearance order."""

        block_order: dict[tuple[str, str], tuple[int, int, int]] = {}
        for document_index, doc_id in enumerate(document_ids):
            for block_index, block in enumerate(self._repository.list_blocks(doc_id)):
                page_or_index = block.page_or_index if block.page_or_index is not None else block_index
                block_order[(doc_id, block.block_id)] = (document_index, page_or_index, block_index)

        entity_positions: dict[str, tuple[int, int, int, str]] = {}
        for fact in facts:
            if not fact.entity_name:
                continue
            position = block_order.get(
                (fact.source_doc_id, fact.source_block_id),
                (document_ids.index(fact.source_doc_id) if fact.source_doc_id in document_ids else len(document_ids), 10**9, 10**9),
            )
            ranked_position = (*position, fact.entity_name)
            existing_position = entity_positions.get(fact.entity_name)
            if existing_position is None or ranked_position < existing_position:
                entity_positions[fact.entity_name] = ranked_position

        return [entity_name for entity_name, _position in sorted(entity_positions.items(), key=lambda item: item[1])]

    def _fill_xlsx_template(
        self,
        *,
        template_path: Path,
        output_path: Path,
        fact_lookup: dict[tuple[str, str], object],
        unique_entities: list[str],
    ) -> list[FilledCellRecord]:
        """执行 XLSX 模板回填。
        Execute XLSX template filling.
        """
        workbook = load_xlsx(template_path)
        updates: list[CellWrite] = []
        filled_cells: list[FilledCellRecord] = []
        for sheet in workbook.sheets:
            header_row, entity_column, field_columns = self._detect_layout(sheet)
            if header_row is None or entity_column is None or not field_columns:
                continue
            sheet_updates, sheet_filled_cells = self._build_sheet_updates(
                sheet=sheet,
                header_row=header_row,
                entity_column=entity_column,
                field_columns=field_columns,
                fact_lookup=fact_lookup,
                unique_entities=unique_entities,
            )
            updates.extend(sheet_updates)
            filled_cells.extend(sheet_filled_cells)
        apply_xlsx_updates(template_path, output_path, updates)
        return filled_cells

    def _fill_docx_template(
        self,
        *,
        template_path: Path,
        output_path: Path,
        fact_lookup: dict[tuple[str, str], object],
        unique_entities: list[str],
    ) -> list[FilledCellRecord]:
        """执行 DOCX 表格模板回填。
        Execute DOCX table-template filling.
        """
        document = load_docx_tables(template_path)
        updates: list[WordCellWrite] = []
        filled_cells: list[FilledCellRecord] = []
        eligible_tables: list[tuple[WordTable, int, int, list[tuple[int, str]]]] = []
        for table in document.tables:
            header_row, entity_column, field_columns = self._detect_layout(table)
            if header_row is None or entity_column is None or not field_columns:
                continue
            eligible_tables.append((table, header_row, entity_column, field_columns))

        remaining_entities = list(unique_entities)
        for index, (table, header_row, entity_column, field_columns) in enumerate(eligible_tables):
            table_updates, table_filled_cells, consumed_entities = self._build_docx_table_updates(
                table=table,
                header_row=header_row,
                entity_column=entity_column,
                field_columns=field_columns,
                fact_lookup=fact_lookup,
                unique_entities=remaining_entities,
                append_remaining_entities=index == len(eligible_tables) - 1,
            )
            if consumed_entities:
                consumed_set = set(consumed_entities)
                remaining_entities = [entity for entity in remaining_entities if entity not in consumed_set]
            updates.extend(table_updates)
            filled_cells.extend(table_filled_cells)
        apply_docx_updates(template_path, output_path, updates)
        return filled_cells

    def _detect_layout(self, sheet: SpreadsheetSheet | WordTable) -> tuple[int | None, int | None, list[tuple[int, str]]]:
        """推断表格的表头行、实体列和目标字段列。
        Infer the header row, entity column and target field columns of a table.
        """
        best_row_index: int | None = None
        best_score = -1
        best_fields: list[tuple[int, str]] = []
        best_entity_column: int | None = None

        for row in sheet.rows[:10]:
            score = 0
            entity_column: int | None = None
            field_columns: list[tuple[int, str]] = []
            for column_index, raw_value in enumerate(row.values, start=1):
                if is_entity_column(raw_value):
                    entity_column = column_index
                    score += 3
                field_name = normalize_field_name(raw_value)
                if field_name:
                    field_columns.append((column_index, field_name))
                    score += 2
            if score > best_score and field_columns:
                best_row_index = row.row_index
                best_score = score
                best_fields = field_columns
                best_entity_column = entity_column or 1

        return best_row_index, best_entity_column, best_fields

    def _build_sheet_updates(
        self,
        *,
        sheet: SpreadsheetSheet,
        header_row: int,
        entity_column: int,
        field_columns: list[tuple[int, str]],
        fact_lookup: dict[tuple[str, str], object],
        unique_entities: list[str],
    ) -> tuple[list[CellWrite], list[FilledCellRecord]]:
        """将事实结果转换为单个工作表的具体单元格写入操作。
        Convert facts into concrete cell writes for one worksheet.
        """
        rows_after_header = [row for row in sheet.rows if row.row_index > header_row]
        updates: list[CellWrite] = []
        filled_cells: list[FilledCellRecord] = []

        assigned_entities: list[str] = []
        entity_cursor = 0
        next_row_index = max([header_row, *(row.row_index for row in rows_after_header)], default=header_row) + 1

        def write_row(row_index: int, entity_name: str, write_entity_cell: bool) -> None:
            """为目标工作表中的一个实体行追加单元格写入操作。
            Append cell writes for one entity row in the target worksheet.
            """
            if write_entity_cell:
                updates.append(
                    CellWrite(
                        sheet_name=sheet.name,
                        cell_ref=build_cell_ref(row_index, entity_column),
                        value=entity_name,
                    )
                )
            for column_index, field_name in field_columns:
                fact = fact_lookup.get((entity_name, field_name))
                if fact is None:
                    continue
                value = fact.value_num if fact.value_num is not None else fact.value_text
                if isinstance(value, float) and not value.is_integer():
                    cell_value: str | float = float(format_value(value))
                elif isinstance(value, float):
                    cell_value = int(value)
                else:
                    cell_value = value
                cell_ref = build_cell_ref(row_index, column_index)
                updates.append(CellWrite(sheet_name=sheet.name, cell_ref=cell_ref, value=cell_value))
                filled_cells.append(
                    FilledCellRecord(
                        sheet_name=sheet.name,
                        cell_ref=cell_ref,
                        entity_name=entity_name,
                        field_name=field_name,
                        value=cell_value,
                        fact_id=fact.fact_id,
                        confidence=fact.confidence,
                    )
                )

        for row in rows_after_header:
            entity_value = row.values[entity_column - 1] if len(row.values) >= entity_column else ""
            normalized_entity = normalize_entity_name(entity_value) if entity_value else ""
            if not normalized_entity and entity_cursor < len(unique_entities):
                while entity_cursor < len(unique_entities):
                    candidate_entity = unique_entities[entity_cursor]
                    entity_cursor += 1
                    if not self._entity_has_matching_fields(candidate_entity, field_columns, fact_lookup):
                        continue
                    normalized_entity = candidate_entity
                    write_row(row.row_index, normalized_entity, True)
                    break
            elif normalized_entity:
                write_row(row.row_index, normalized_entity, False)
            if normalized_entity:
                assigned_entities.append(normalized_entity)

        for entity_name in unique_entities:
            if entity_name in assigned_entities:
                continue
            if not self._entity_has_matching_fields(entity_name, field_columns, fact_lookup):
                continue
            write_row(next_row_index, entity_name, True)
            next_row_index += 1

        return updates, filled_cells

    def _build_docx_table_updates(
        self,
        *,
        table: WordTable,
        header_row: int,
        entity_column: int,
        field_columns: list[tuple[int, str]],
        fact_lookup: dict[tuple[str, str], object],
        unique_entities: list[str],
        append_remaining_entities: bool,
    ) -> tuple[list[WordCellWrite], list[FilledCellRecord], list[str]]:
        """将事实结果转换为 Word 表格中的单元格写入。
        Convert facts into concrete DOCX table cell writes.
        """
        rows_after_header = [row for row in table.rows if row.row_index > header_row]
        updates: list[WordCellWrite] = []
        filled_cells: list[FilledCellRecord] = []
        consumed_entities: list[str] = []

        assigned_entities: list[str] = []
        entity_cursor = 0
        next_row_index = max([header_row, *(row.row_index for row in rows_after_header)], default=header_row) + 1

        def write_row(row_index: int, entity_name: str, write_entity_cell: bool) -> None:
            """向 Word 表格中的一行写入实体与字段值。
            Write one entity row into a DOCX table.
            """
            if write_entity_cell:
                updates.append(
                    WordCellWrite(
                        table_index=table.table_index,
                        row_index=row_index,
                        column_index=entity_column,
                        value=entity_name,
                    )
                )
            for column_index, field_name in field_columns:
                fact = fact_lookup.get((entity_name, field_name))
                if fact is None:
                    continue
                value = fact.value_num if fact.value_num is not None else fact.value_text
                if isinstance(value, float) and not value.is_integer():
                    cell_value: str | float = float(format_value(value))
                elif isinstance(value, float):
                    cell_value = int(value)
                else:
                    cell_value = value
                updates.append(
                    WordCellWrite(
                        table_index=table.table_index,
                        row_index=row_index,
                        column_index=column_index,
                        value=cell_value,
                    )
                )
                filled_cells.append(
                    FilledCellRecord(
                        sheet_name=table.name,
                        cell_ref=f"R{row_index}C{column_index}",
                        entity_name=entity_name,
                        field_name=field_name,
                        value=cell_value,
                        fact_id=fact.fact_id,
                        confidence=fact.confidence,
                    )
                )

        for row in rows_after_header:
            entity_value = row.values[entity_column - 1] if len(row.values) >= entity_column else ""
            normalized_entity = normalize_entity_name(entity_value) if entity_value else ""
            if not normalized_entity and entity_cursor < len(unique_entities):
                while entity_cursor < len(unique_entities):
                    candidate_entity = unique_entities[entity_cursor]
                    entity_cursor += 1
                    if not self._entity_has_matching_fields(candidate_entity, field_columns, fact_lookup):
                        continue
                    normalized_entity = candidate_entity
                    write_row(row.row_index, normalized_entity, True)
                    break
            elif normalized_entity:
                write_row(row.row_index, normalized_entity, False)
            if normalized_entity:
                assigned_entities.append(normalized_entity)
                if normalized_entity in unique_entities and normalized_entity not in consumed_entities:
                    consumed_entities.append(normalized_entity)

        if append_remaining_entities:
            for entity_name in unique_entities:
                if entity_name in assigned_entities:
                    continue
                if not self._entity_has_matching_fields(entity_name, field_columns, fact_lookup):
                    continue
                write_row(next_row_index, entity_name, True)
                consumed_entities.append(entity_name)
                next_row_index += 1

        return updates, filled_cells, consumed_entities
