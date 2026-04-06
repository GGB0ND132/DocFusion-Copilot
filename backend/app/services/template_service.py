from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from app.core.config import Settings
from app.core.logging import ErrorCode, get_logger, log_operation
from app.core.openai_client import OpenAIClientError, OpenAICompatibleClient
from app.models.domain import (
    DocumentRecord,
    FactRecord,
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
    is_date_column,
    is_entity_column,
    normalize_entity_name,
    normalize_field_name,
    normalize_field_name_or_passthrough,
    parse_date_range_from_text,
    strip_header_adornments,
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

    _logger = get_logger("template_service")

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
        user_requirement: str = "",
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
                "user_requirement": user_requirement,
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
            user_requirement,
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
        user_requirement: str = "",
    ) -> TemplateResultRecord:
        """同步执行一次模板回填并返回结果。
        Execute one template fill synchronously and return the result.
        """
        with log_operation(self._logger, "template_fill", task_id=task_id):
            return self._fill_template_once_inner(
                task_id=task_id,
                template_name=template_name,
                template_path=template_path,
                fill_mode=fill_mode,
                document_ids=document_ids,
                output_file_name=output_file_name,
                persist_result=persist_result,
                user_requirement=user_requirement,
            )

    def _fill_template_once_inner(
        self,
        *,
        task_id: str,
        template_name: str,
        template_path: Path,
        fill_mode: str,
        document_ids: list[str],
        output_file_name: str | None = None,
        persist_result: bool = True,
        user_requirement: str = "",
    ) -> TemplateResultRecord:
        """同步执行一次模板回填并返回结果。
        Execute one template fill synchronously and return the result.
        """
        suffix = template_path.suffix.lower()
        if suffix not in {".xlsx", ".docx"}:
            raise ValueError(f"Unsupported template type: {suffix}")

        facts = self._repository.list_facts(canonical_only=False, document_ids=set(document_ids))

        # Parse user_requirement for date range filtering
        date_from, date_to = parse_date_range_from_text(user_requirement) if user_requirement else (None, None)
        if date_from or date_to:
            facts = self._filter_facts_by_date(facts, date_from, date_to)

        # Build row-oriented groups: each source block → one template row
        row_groups = self._build_row_groups(facts)
        fact_lookup = self._build_fact_lookup(facts)
        unique_entities = list(dict.fromkeys(fact.entity_name for fact in facts if fact.entity_name))
        resolved_output_file_name = output_file_name or f"{task_id}_{safe_filename(template_name)}"
        output_path = self._settings.outputs_dir / resolved_output_file_name

        if suffix == ".xlsx":
            filled_cells = self._fill_xlsx_template(
                template_path=template_path,
                output_path=output_path,
                fact_lookup=fact_lookup,
                unique_entities=unique_entities,
                row_groups=row_groups,
            )
        else:
            filled_cells = self._fill_docx_template(
                template_path=template_path,
                output_path=output_path,
                fact_lookup=fact_lookup,
                unique_entities=unique_entities,
                row_groups=row_groups,
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
            warnings=self._verify_filled_cells(filled_cells, facts),
        )
        if persist_result:
            self._repository.save_template_result(result)
        return result

    @staticmethod
    def _filter_facts_by_date(
        facts: list[FactRecord],
        date_from: str | None,
        date_to: str | None,
    ) -> list[FactRecord]:
        """按日期范围过滤事实，只保留 metadata 中有 date 且在范围内的事实。
        无 date metadata 的事实照常保留（如人口等静态字段）。"""
        filtered: list[FactRecord] = []
        for fact in facts:
            fact_date = fact.metadata.get("date") if fact.metadata else None
            if fact_date is None:
                # Static fields without date — always keep
                filtered.append(fact)
                continue
            if date_from and str(fact_date) < date_from:
                continue
            if date_to and str(fact_date) > date_to:
                continue
            filtered.append(fact)
        return filtered

    @staticmethod
    def _build_row_groups(facts: list[FactRecord]) -> list[dict[str, FactRecord]]:
        """将事实按 source_block_id 分组，每组代表源数据的一行。
        Group facts by source_block_id, each group = one source row."""
        from collections import defaultdict
        groups: dict[str, dict[str, FactRecord]] = defaultdict(dict)
        block_entity: dict[str, str] = {}
        for fact in facts:
            key = fact.source_block_id
            groups[key][fact.field_name] = fact
            if fact.entity_name:
                block_entity[key] = fact.entity_name
        # Return as list of {field_name: fact}, ordered by block_id for stability
        result: list[dict[str, FactRecord]] = []
        for block_id in sorted(groups.keys()):
            group = groups[block_id]
            # Attach entity_name from block to all facts in group
            entity = block_entity.get(block_id, "")
            group["__entity__"] = type("_EntityHolder", (), {"entity_name": entity})()  # type: ignore[arg-type]
            result.append(group)
        return result

    @staticmethod
    def _verify_filled_cells(
        filled_cells: list[FilledCellRecord],
        facts: list[FactRecord],
    ) -> list[str]:
        """校验回填值的合理性，返回警告列表。
        Verify that filled values are reasonable and return a list of warnings."""
        warnings: list[str] = []
        fact_by_id = {fact.fact_id: fact for fact in facts}
        for cell in filled_cells:
            fact = fact_by_id.get(cell.fact_id)
            if fact is None:
                continue
            # Check for suspicious numeric magnitude
            if fact.value_num is not None:
                if fact.value_num < 0 and fact.field_name not in ("增长率", "增速"):
                    warnings.append(f"{cell.cell_ref}: {fact.field_name} 值为负数 ({fact.value_num})，请核查")
                if abs(fact.value_num) > 1e12:
                    warnings.append(f"{cell.cell_ref}: {fact.field_name} 值极大 ({fact.value_num})，可能存在单位换算问题")
            # Check year reasonableness
            if fact.year is not None and (fact.year < 1950 or fact.year > 2030):
                warnings.append(f"{cell.cell_ref}: 年份 {fact.year} 超出合理范围 [1950, 2030]")
            # Check low confidence
            if fact.confidence is not None and fact.confidence < 0.6:
                warnings.append(f"{cell.cell_ref}: {fact.field_name} 置信度仅 {fact.confidence:.2f}，建议复核")
        return warnings

    def resolve_document_ids(
        self,
        document_set_id: str | None,
        document_ids: list[str] | None,
    ) -> list[str]:
        """将显式文档列表或文档批次解析为具体文档 id 列表。
        Resolve an explicit document list or document batch into concrete document ids.
        """
        parsed_documents = [
            document
            for document in self._repository.list_documents()
            if document.status == "parsed" and not bool(document.metadata.get("skip_fact_extraction"))
        ]
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
        user_requirement: str = "",
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
                user_requirement=user_requirement,
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
            raise ValueError("当前没有可用于回填的已解析源文档，请先上传并完成解析。")

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
                "禁止返回候选列表中不存在的 document_id。\n\n"
                "匹配原则：\n"
                "1. 优先匹配实体名（城市名、地区、机构）一致的文档。\n"
                "2. 其次匹配字段名（GDP、常住人口、AQI 等）重合度高的文档。\n"
                "3. 若模板涉及多城市汇总，可选多个文档。\n"
                "4. 若模板涉及时间过滤（如 2020/7/1~2020/8/31），选择含该时段数据的文档。\n"
                "5. 文档名或文本摘要中包含模板关键词的优先。\n"
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
                    field_name = normalize_field_name_or_passthrough(value)
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
                    field_name = normalize_field_name_or_passthrough(value)
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
        Register both with and without "市" suffix for robust city matching.
        """
        fact_lookup: dict[tuple[str, str], object] = {}
        for fact in sorted(facts, key=lambda item: item.confidence, reverse=True):
            fact_lookup.setdefault((fact.entity_name, fact.field_name), fact)
            # Also register the "市" variant for city entity matching
            entity = fact.entity_name
            if entity:
                if entity.endswith("市"):
                    alt = entity[:-1]
                else:
                    alt = entity + "市"
                fact_lookup.setdefault((alt, fact.field_name), fact)
        return fact_lookup

    def _fill_xlsx_template(
        self,
        *,
        template_path: Path,
        output_path: Path,
        fact_lookup: dict[tuple[str, str], object],
        unique_entities: list[str],
        row_groups: list[dict[str, object]] | None = None,
    ) -> list[FilledCellRecord]:
        """执行 XLSX 模板回填。
        Execute XLSX template filling.
        """
        workbook = load_xlsx(template_path)
        updates: list[CellWrite] = []
        filled_cells: list[FilledCellRecord] = []
        known_field_names = {key[1] for key in fact_lookup}
        for sheet in workbook.sheets:
            header_row, entity_column, field_columns = self._detect_layout(sheet, known_field_names)
            if header_row is None or entity_column is None or not field_columns:
                continue
            sheet_updates, sheet_filled_cells = self._build_sheet_updates(
                sheet=sheet,
                header_row=header_row,
                entity_column=entity_column,
                field_columns=field_columns,
                fact_lookup=fact_lookup,
                unique_entities=unique_entities,
                row_groups=row_groups,
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
        row_groups: list[dict[str, object]] | None = None,
    ) -> list[FilledCellRecord]:
        """执行 DOCX 表格模板回填。
        Execute DOCX table-template filling.
        """
        document = load_docx_tables(template_path)
        updates: list[WordCellWrite] = []
        filled_cells: list[FilledCellRecord] = []
        known_field_names = {key[1] for key in fact_lookup}
        for table in document.tables:
            header_row, entity_column, field_columns = self._detect_layout(table, known_field_names)
            if header_row is None or entity_column is None or not field_columns:
                continue
            table_updates, table_filled_cells = self._build_docx_table_updates(
                table=table,
                header_row=header_row,
                entity_column=entity_column,
                field_columns=field_columns,
                fact_lookup=fact_lookup,
                unique_entities=unique_entities,
                row_groups=row_groups,
            )
            updates.extend(table_updates)
            filled_cells.extend(table_filled_cells)
        apply_docx_updates(template_path, output_path, updates)
        return filled_cells

    def _detect_layout(
        self,
        sheet: SpreadsheetSheet | WordTable,
        known_field_names: set[str] | None = None,
    ) -> tuple[int | None, int | None, list[tuple[int, str]]]:
        """推断表格的表头行、实体列和目标字段列。
        Infer the header row, entity column and target field columns of a table.
        """
        known = known_field_names or set()
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
                    continue
                # Dynamic: match stripped header against known fact field names
                stripped = strip_header_adornments(raw_value)
                if stripped and stripped in known:
                    field_columns.append((column_index, stripped))
                    score += 2
                    continue
                # Fuzzy: case-insensitive match
                if stripped:
                    stripped_lower = stripped.lower()
                    for kf in known:
                        if kf.lower() == stripped_lower:
                            field_columns.append((column_index, kf))
                            score += 2
                            break
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
        row_groups: list[dict[str, object]] | None = None,
    ) -> tuple[list[CellWrite], list[FilledCellRecord]]:
        """将事实结果转换为单个工作表的具体单元格写入操作。
        Convert facts into concrete cell writes for one worksheet.
        """
        rows_after_header = [row for row in sheet.rows if row.row_index > header_row]
        updates: list[CellWrite] = []
        filled_cells: list[FilledCellRecord] = []
        next_row_index = max([header_row, *(row.row_index for row in rows_after_header)], default=header_row) + 1

        def _format_cell(fact: object) -> str | float | int:
            value = fact.value_num if fact.value_num is not None else fact.value_text
            if isinstance(value, float) and not value.is_integer():
                return float(format_value(value))
            elif isinstance(value, float):
                return int(value)
            return value

        def write_row(row_index: int, entity_name: str, row_fact_map: dict[str, object] | None, write_entity_cell: bool) -> None:
            if write_entity_cell:
                updates.append(CellWrite(sheet_name=sheet.name, cell_ref=build_cell_ref(row_index, entity_column), value=entity_name))
            for column_index, field_name in field_columns:
                fact = None
                if row_fact_map:
                    fact = row_fact_map.get(field_name)
                if fact is None:
                    fact = fact_lookup.get((entity_name, field_name))
                if fact is None:
                    continue
                cell_value = _format_cell(fact)
                cell_ref = build_cell_ref(row_index, column_index)
                updates.append(CellWrite(sheet_name=sheet.name, cell_ref=cell_ref, value=cell_value))
                filled_cells.append(FilledCellRecord(
                    sheet_name=sheet.name, cell_ref=cell_ref, entity_name=entity_name,
                    field_name=field_name, value=cell_value, fact_id=fact.fact_id, confidence=fact.confidence,
                    evidence_text=fact.source_span[:200] if fact.source_span else "",
                ))

        # If we have row_groups (multi-row data like COVID-19), use them for empty templates
        if row_groups and not rows_after_header:
            field_name_set = {fn for _, fn in field_columns}
            for group in row_groups:
                entity_holder = group.get("__entity__")
                entity_name = getattr(entity_holder, "entity_name", "") if entity_holder else ""
                # Check if this group has any matching fields
                has_match = any(fn in group for fn in field_name_set)
                if not has_match:
                    continue
                write_row(next_row_index, entity_name, group, True)
                next_row_index += 1
            return updates, filled_cells

        # Standard flow: fill existing rows then append unassigned entities
        assigned_entities: list[str] = []
        entity_cursor = 0

        for row in rows_after_header:
            entity_value = row.values[entity_column - 1] if len(row.values) >= entity_column else ""
            normalized_entity = normalize_entity_name(entity_value) if entity_value else ""
            if not normalized_entity and entity_cursor < len(unique_entities):
                normalized_entity = unique_entities[entity_cursor]
                entity_cursor += 1
                write_row(row.row_index, normalized_entity, None, True)
            elif normalized_entity:
                write_row(row.row_index, normalized_entity, None, False)
            if normalized_entity:
                assigned_entities.append(normalized_entity)

        for entity_name in unique_entities:
            if entity_name in assigned_entities:
                continue
            write_row(next_row_index, entity_name, None, True)
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
        row_groups: list[dict[str, object]] | None = None,
    ) -> tuple[list[WordCellWrite], list[FilledCellRecord]]:
        """将事实结果转换为 Word 表格中的单元格写入。
        Convert facts into concrete DOCX table cell writes.
        """
        rows_after_header = [row for row in table.rows if row.row_index > header_row]
        updates: list[WordCellWrite] = []
        filled_cells: list[FilledCellRecord] = []
        next_row_index = max([header_row, *(row.row_index for row in rows_after_header)], default=header_row) + 1

        def _format_cell(fact: object) -> str | float | int:
            value = fact.value_num if fact.value_num is not None else fact.value_text
            if isinstance(value, float) and not value.is_integer():
                return float(format_value(value))
            elif isinstance(value, float):
                return int(value)
            return value

        def write_row(row_index: int, entity_name: str, row_fact_map: dict[str, object] | None, write_entity_cell: bool) -> None:
            if write_entity_cell:
                updates.append(WordCellWrite(table_index=table.table_index, row_index=row_index, column_index=entity_column, value=entity_name))
            for column_index, field_name in field_columns:
                fact = None
                if row_fact_map:
                    fact = row_fact_map.get(field_name)
                if fact is None:
                    fact = fact_lookup.get((entity_name, field_name))
                if fact is None:
                    continue
                cell_value = _format_cell(fact)
                updates.append(WordCellWrite(table_index=table.table_index, row_index=row_index, column_index=column_index, value=cell_value))
                filled_cells.append(FilledCellRecord(
                    sheet_name=table.name, cell_ref=f"R{row_index}C{column_index}", entity_name=entity_name,
                    field_name=field_name, value=cell_value, fact_id=fact.fact_id, confidence=fact.confidence,
                    evidence_text=fact.source_span[:200] if fact.source_span else "",
                ))

        # Standard flow: fill existing rows then append unassigned entities
        assigned_entities: list[str] = []
        entity_cursor = 0

        for row in rows_after_header:
            entity_value = row.values[entity_column - 1] if len(row.values) >= entity_column else ""
            normalized_entity = normalize_entity_name(entity_value) if entity_value else ""
            if not normalized_entity and entity_cursor < len(unique_entities):
                normalized_entity = unique_entities[entity_cursor]
                entity_cursor += 1
                write_row(row.row_index, normalized_entity, None, True)
            elif normalized_entity:
                write_row(row.row_index, normalized_entity, None, False)
            if normalized_entity:
                assigned_entities.append(normalized_entity)

        for entity_name in unique_entities:
            if entity_name in assigned_entities:
                continue
            write_row(next_row_index, entity_name, None, True)
            next_row_index += 1

        return updates, filled_cells
