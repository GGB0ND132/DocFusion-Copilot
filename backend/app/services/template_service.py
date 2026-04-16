from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime, timezone
from pathlib import Path
from time import perf_counter

from app.core.config import Settings
from app.core.logging import get_logger, log_operation
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
    parse_entity_filter_from_text,
    strip_header_adornments,
)
from app.utils.spreadsheet import CellWrite, SpreadsheetDocument, SpreadsheetSheet, apply_xlsx_updates, build_cell_ref, load_xlsx
from app.utils.wordprocessing import WordCellWrite, WordDocument, WordTable, apply_docx_updates, load_docx_tables

_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]{2,24}")

# 汇总/聚合行实体名，在按城市/地区填充时应过滤掉
_AGGREGATE_ENTITY_NAMES = frozenset({
    "全国", "合计", "总计", "平均", "全省", "全市", "总体",
    "全区", "均值", "中位数", "最大值", "最小值",
})
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
        extraction_service: object | None = None,
        embedding_service: object | None = None,
    ) -> None:
        """初始化模板回填与匹配流程所需依赖。
        Initialize dependencies used by template filling and matching workflows.
        """
        self._repository = repository
        self._executor = executor
        self._settings = settings
        self._openai_client = openai_client
        self._extraction_service = extraction_service
        self._embedding_service = embedding_service

    def suggest_documents(
        self,
        *,
        template_name: str,
        content: bytes,
        document_set_id: str | None = None,
    ) -> dict:
        """分析模板并返回带有匹配分数的候选源文档列表。
        Analyse a template and return scored candidate source documents for user selection.
        """
        suffix = Path(template_name).suffix.lower()
        if suffix not in self._settings.supported_template_extensions:
            raise ValueError(f"Unsupported template type: {suffix}")

        # 保存临时文件用于分析
        temp_name = f"suggest_{new_id('tmp')}_{safe_filename(template_name)}"
        template_path = self._settings.temp_dir / temp_name
        template_path.write_bytes(content)

        try:
            profile = self._build_template_profile(template_name, template_path)
            candidate_ids = self.resolve_document_ids(document_set_id, None)
            if not candidate_ids:
                return {
                    "template_profile": profile,
                    "candidates": [],
                    "message": "当前没有可用的已解析源文档，请先上传并完成解析。",
                }
            candidates = self._build_document_match_cards(candidate_ids)
            _, match_mode, reason, scored_candidates = self._match_documents(profile, candidates)

            # 为每个候选文档添加 recommended 标记
            recommended_ids = set()
            if match_mode not in ("fallback_all",):
                recommended_ids = {
                    str(c["doc_id"]) for c in scored_candidates
                    if c.get("entity_hits") or c.get("field_hits")
                }

            # Normalize scores to 0~1 range
            max_score = max((float(c.get("score", 0)) for c in scored_candidates), default=1.0) or 1.0

            result_candidates = []
            for c in scored_candidates:
                doc_id = str(c["doc_id"])
                raw_score = float(c.get("score", 0))
                result_candidates.append({
                    "doc_id": doc_id,
                    "file_name": str(c.get("file_name", "")),
                    "score": round(raw_score / max_score, 4),
                    "field_hits": list(c.get("field_hits", [])),
                    "entity_hits": list(c.get("entity_hits", []))[:10],
                    "keyword_hits": list(c.get("keyword_hits", []))[:5],
                    "recommended": doc_id in recommended_ids,
                })

            return {
                "template_profile": {
                    "template_name": profile.get("template_name"),
                    "field_names": profile.get("field_names", []),
                    "entity_names": list(profile.get("entity_names", []))[:20],
                },
                "candidates": result_candidates,
                "match_reason": reason,
            }
        finally:
            template_path.unlink(missing_ok=True)

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

    # ── LLM 需求预分析 JSON schema ──
    _FILL_ANALYSIS_SCHEMA: dict = {
        "type": "object",
        "properties": {
            "strategy": {
                "type": "string",
                "enum": ["structured_filter", "llm_transform"],
                "description": "structured_filter=源数据以结构化表格为主,直接筛选填充; llm_transform=源数据为非结构化文本或需要复杂转换,用LLM生成代码",
            },
            "per_table_filters": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "table_index": {"type": "integer"},
                        "entity_filter": {"type": "string", "description": "该表需要的实体/城市名,如'德州市'"},
                        "time_filter": {"type": "string", "description": "该表需要的时间条件,如'2025-11-25 09:00'"},
                    },
                },
                "description": "每张表的筛选条件(仅模板有多表时填写)",
            },
            "date_range_from": {"type": "string", "description": "日期范围起始 YYYY-MM-DD,无则空"},
            "date_range_to": {"type": "string", "description": "日期范围结束 YYYY-MM-DD,无则空"},
            "needed_fields": {
                "type": "array",
                "items": {"type": "string"},
                "description": "模板需要的所有字段名",
            },
        },
        "required": ["strategy", "per_table_filters", "needed_fields"],
        "additionalProperties": False,
    }

    def _analyze_fill_requirements(
        self,
        *,
        user_requirement: str,
        template_path: Path,
        document_ids: list[str],
    ) -> dict:
        """纯规则预分析: 从模板结构提取 needed_fields，不调用 LLM。

        所有语义理解（日期范围、筛选条件、列映射）留给 code gen 一次性处理。
        """
        needed_fields: list[str] = []
        try:
            suffix = template_path.suffix.lower()
            if suffix == ".docx":
                doc = load_docx_tables(template_path)
                for table in doc.tables:
                    if not table.rows:
                        continue
                    headers = (
                        [str(c.value or "").strip() for c in table.rows[0].cells]
                        if hasattr(table.rows[0], "cells")
                        else list(table.rows[0].values)
                    )
                    needed_fields.extend(h for h in headers if h and h not in needed_fields)
            else:
                wb = load_xlsx(template_path)
                for sheet in wb.sheets:
                    if not sheet.rows:
                        continue
                    headers = [v.strip() for v in sheet.rows[0].values if v and v.strip()]
                    needed_fields.extend(h for h in headers if h and h not in needed_fields)
        except Exception as exc:
            self._logger.warning("_analyze_fill_requirements: failed to read template: %s", exc)

        return {
            "strategy": "llm_transform",
            "per_table_filters": [],
            "needed_fields": needed_fields,
        }

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

        resolved_output_file_name = output_file_name or f"{task_id}_{safe_filename(template_name)}"
        output_path = self._settings.outputs_dir / resolved_output_file_name

        # ── Audit: log source document details ──
        for doc_id in document_ids[:5]:
            doc = self._repository.get_document(doc_id)
            if doc is None:
                self._logger.warning("Fill audit: doc_id=%s not found", doc_id)
                continue
            blocks = self._repository.list_blocks(doc_id)
            n_structured = sum(1 for b in blocks if b.metadata and b.metadata.get("row_values"))
            n_text = sum(1 for b in blocks if b.block_type in ("paragraph", "heading"))
            self._logger.info(
                "Fill audit: doc_id=%s type=%s file=%s blocks=%d structured=%d text=%d",
                doc_id, doc.doc_type, doc.file_name, len(blocks), n_structured, n_text,
            )

        # ── Phase 0: 规则预分析 (extract template fields) ──
        fill_analysis = self._analyze_fill_requirements(
            user_requirement=user_requirement,
            template_path=template_path,
            document_ids=document_ids,
        )
        self._logger.info("Fill analysis: %s", fill_analysis)

        # ── Unified pipeline: LLM-driven data transformation ──
        try:
            from app.services.llm_transform import run_llm_transform_pipeline
            llm_result = run_llm_transform_pipeline(
                openai_client=self._openai_client,
                repository=self._repository,
                template_path=template_path,
                output_path=output_path,
                document_ids=document_ids,
                user_requirement=user_requirement,
            )
            if llm_result is not None:
                self._logger.info("LLM transform succeeded with %d cells", len(llm_result))
                result = TemplateResultRecord(
                    task_id=task_id,
                    template_name=template_name,
                    output_path=str(output_path),
                    output_file_name=resolved_output_file_name,
                    created_at=datetime.now(timezone.utc),
                    fill_mode=fill_mode,
                    document_ids=document_ids,
                    filled_cells=llm_result,
                    warnings=[],
                )
                if persist_result:
                    self._repository.save_template_result(result)
                return result
        except Exception as exc:
            self._logger.error("LLM transform pipeline failed, falling back: %s", exc, exc_info=True)

        # ── Fallback C: intent-driven analysis → extraction → fill ──
        if fill_mode in ("canonical", "intent_driven"):
            try:
                _INTENT_TIMEOUT = 75  # seconds — 给 intent pipeline 留 75s，总限 90s
                with ThreadPoolExecutor(max_workers=1) as _pool:
                    _fut = _pool.submit(
                        self._run_intent_driven_pipeline,
                        task_id=task_id,
                        template_name=template_name,
                        template_path=template_path,
                        output_path=output_path,
                        document_ids=document_ids,
                        user_requirement=user_requirement,
                        fill_mode=fill_mode,
                        resolved_output_file_name=resolved_output_file_name,
                        persist_result=persist_result,
                    )
                    intent_result = _fut.result(timeout=_INTENT_TIMEOUT)
                if intent_result is not None:
                    return intent_result
            except FuturesTimeoutError:
                self._logger.error("Intent-driven pipeline timed out after %ds, falling back", _INTENT_TIMEOUT)
            except Exception as exc:
                self._logger.error("Intent-driven pipeline failed, falling back: %s", exc, exc_info=True)

        # ── Slow path: fact-based extraction (fallback) ──
        # Detect template field names to enable targeted extraction
        profile = self._build_template_profile(template_name, template_path)
        template_field_names = set(profile.get("field_names", []))
        template_entity_names = list(profile.get("entity_names", []))

        # Step 1: Collect facts — reuse existing ones, only extract if none exist yet.
        facts: list = []
        for doc_id in document_ids[:5]:
            existing = self._repository.list_facts(canonical_only=False, document_ids={doc_id})
            if existing:
                facts.extend(existing)
            elif self._extraction_service is not None:
                doc = self._repository.get_document(doc_id)
                if doc is None:
                    continue
                blocks = self._repository.list_blocks(doc_id)
                if not blocks:
                    continue
                new_facts = self._extraction_service.extract(doc, blocks)
                if new_facts:
                    saved = self._repository.add_facts(new_facts)
                    facts.extend(saved)

        # Parse user_requirement for date range filtering
        date_from, date_to = parse_date_range_from_text(user_requirement) if user_requirement else (None, None)
        if date_from or date_to:
            facts = self._filter_facts_by_date(facts, date_from, date_to)

        # Step 2: LLM targeted extraction only for genuinely missing fields
        existing_field_names = {f.field_name for f in facts}
        missing_fields = sorted(template_field_names - existing_field_names)
        if missing_fields and self._extraction_service is not None and hasattr(self._extraction_service, "extract_targeted_fields"):
            for doc_id in document_ids[:5]:
                doc = self._repository.get_document(doc_id)
                if doc is None:
                    continue
                blocks = self._repository.list_blocks(doc_id)
                if not blocks:
                    continue
                new_facts = self._extraction_service.extract_targeted_fields(
                    doc, blocks, missing_fields, target_entities=template_entity_names,
                )
                if new_facts:
                    saved = self._repository.add_facts(new_facts)
                    facts.extend(saved)
                    existing_field_names.update(f.field_name for f in saved)
                    missing_fields = sorted(template_field_names - existing_field_names)
                    if not missing_fields:
                        break

        # Build row-oriented groups: each source block → one template row
        row_groups = self._build_row_groups(facts)
        fact_lookup = self._build_fact_lookup(facts)
        all_entities = list(dict.fromkeys(
            fact.entity_name for fact in facts
            if fact.entity_name and fact.entity_name.strip() not in _AGGREGATE_ENTITY_NAMES
        ))
        # Only keep entities that have at least one fact matching a template field
        if template_field_names:
            unique_entities = [
                e for e in all_entities
                if any(fact_lookup.get((e, fn)) for fn in template_field_names)
            ]
        else:
            unique_entities = all_entities

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
    def _filter_rows_by_date(
        rows: list[dict],
        time_col: str,
        date_from: str | None,
        date_to: str | None,
    ) -> list[dict]:
        """按日期范围过滤结构化行数据。支持 YYYY-MM-DD、YYYY/M/D 和 Excel 序列号。
        Filter structured rows by date range on the given time column."""
        if not date_from and not date_to:
            return rows
        filtered: list[dict] = []
        for row in rows:
            raw = str(row.get(time_col, "")).strip()
            if not raw:
                filtered.append(row)
                continue
            # Normalise date: extract YYYY-MM-DD from various formats
            norm = re.sub(r"[/年月]", "-", raw).rstrip("-日").strip()
            # Drop trailing time component (e.g. "2025-11-25 09:00:00.0")
            norm = norm.split()[0] if " " in norm else norm
            parts = norm.split("-")
            try:
                y = int(parts[0])
                m = int(parts[1]) if len(parts) > 1 else 1
                d = int(parts[2]) if len(parts) > 2 else 1
                # Detect Excel serial date numbers (> 2500 year is unreasonable)
                if y > 2500 and len(parts) == 1:
                    from datetime import datetime as _dt, timedelta
                    serial = y  # the entire raw is a serial number
                    base = _dt(1899, 12, 30)  # Excel epoch (with Lotus 1-2-3 bug)
                    dt = base + timedelta(days=serial)
                    normalised = dt.strftime("%Y-%m-%d")
                else:
                    normalised = f"{y:04d}-{m:02d}-{d:02d}"
            except (ValueError, IndexError, OverflowError):
                filtered.append(row)
                continue
            if date_from and normalised < date_from:
                continue
            if date_to and normalised > date_to:
                continue
            filtered.append(row)
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

    # ── Table-context constraint parsing (LLM + regex fallback) ────────────

    _TABLE_CONTEXT_SCHEMA: dict = {
        "type": "object",
        "properties": {
            "primary_entity": {
                "type": "string",
                "description": "本表的主体实体名（如城市名），不含'市/县/区'后缀。仅填写表格数据描述的主体对象，忽略仅作对比参照提及的实体。若无法判断则留空。",
            },
            "date": {
                "type": "string",
                "description": "本表数据对应的日期，格式 YYYY-MM-DD。若无法判断则留空。",
            },
            "hour": {
                "type": "string",
                "description": "本表数据对应的小时（24h 制，如 '09'）。若无法判断则留空。",
            },
        },
        "required": ["primary_entity", "date", "hour"],
        "additionalProperties": False,
    }

    def _parse_table_context(
        self,
        context_text: str,
        candidates: list[str],
    ) -> dict[str, str]:
        """解析表前上下文描述，返回结构化约束 {primary_entity, date, hour}。
        Parse the descriptive text preceding a table into structured constraints.
        Tries LLM first; falls back to regex patterns."""
        result: dict[str, str] = {"primary_entity": "", "date": "", "hour": ""}
        if not context_text:
            return result

        # ── LLM path ──
        if self._openai_client.is_configured:
            try:
                payload = self._openai_client.create_json_completion(
                    system_prompt=(
                        "你是模板解析引擎。给定一段表格前的描述文字和候选实体列表，"
                        "提取该表数据的主体实体（数据描述的对象，非对比参照）、日期和小时。\n"
                        "规则：\n"
                        "1. primary_entity 必须是候选列表中的一个，不含'市/县/区'后缀。\n"
                        "2. 如果文字提到多个城市，只取数据主体（如'记录潍坊市...数据，结构与德州市一致'中主体是潍坊）。\n"
                        "3. date 格式 YYYY-MM-DD；hour 用两位数字如 '09'。\n"
                        "4. 不确定的字段留空字符串。"
                    ),
                    user_prompt=(
                        f"描述文字:\n{context_text}\n\n"
                        f"候选实体: {candidates}\n\n"
                        "请输出 JSON。"
                    ),
                    json_schema=self._TABLE_CONTEXT_SCHEMA,
                )
                entity = str(payload.get("primary_entity", "")).strip().rstrip("市县区")
                if entity and entity in candidates:
                    result["primary_entity"] = entity
                result["date"] = str(payload.get("date", "")).strip()
                result["hour"] = str(payload.get("hour", "")).strip()
                if result["primary_entity"] or result["date"]:
                    return result
            except Exception:
                pass  # Fall through to regex

        # ── Regex fallback: entity ──
        result["primary_entity"] = self._extract_primary_entity_regex(context_text, candidates)

        # ── Regex fallback: time ──
        # Pattern 1: "时间：YYYY-MM-DD HH:..."
        m = re.search(
            r"(?:监测时间|时间|日期|采样时间|检测时间)[：:]\s*"
            r"(\d{4})-(\d{1,2})-(\d{1,2})(?:\s+(\d{2})[:\d.]*)?",
            context_text,
        )
        if not m:
            # Pattern 2: "YYYY年MM月DD日HH:MM" (no label prefix)
            m = re.search(
                r"(\d{4})年(\d{1,2})月(\d{1,2})日(?:\s*(\d{2})[:：]\d{2})?",
                context_text,
            )
        if m:
            result["date"] = f"{int(m.group(1)):04d}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            if m.group(4):
                result["hour"] = m.group(4)

        return result

    @staticmethod
    def _extract_primary_entity_regex(context_text: str, candidates: list[str]) -> str:
        """正则兜底：从上下文提取主体实体。    Regex fallback for primary entity extraction."""
        for c in candidates:
            escaped = re.escape(c)
            patterns = [
                rf"城市[：:]\s*{escaped}(?:市|县|区)?",
                rf"{escaped}(?:市|县|区)?(?:各|的)(?:监测|站点|数据)",
                rf"记录.*?{escaped}(?:市|县|区)?(?:各|的)",
                rf"{escaped}(?:市|县|区)?(?:的|各)\S*?(?:检测|质量|监测|数据|信息)",
            ]
            for pat in patterns:
                if re.search(pat, context_text):
                    return c
        first_sentence = context_text.split("。")[0] if "。" in context_text else context_text
        found = [c for c in candidates if c in first_sentence or f"{c}市" in first_sentence]
        return found[0] if len(found) == 1 else ""

    # ── Time-based row_group filtering ───────────────────────────────────

    @staticmethod
    def _filter_row_groups_by_time(
        target_date: str,
        target_hour: str,
        row_groups: list[dict[str, object]] | None,
    ) -> list[dict[str, object]] | None:
        """按日期和小时过滤 row_groups，不依赖硬编码字段名。
        Filter row_groups by date/hour by scanning all fact values."""
        if not row_groups or not target_date:
            return row_groups

        def _fact_time_val(group: dict) -> str | None:
            """Return the first fact value_text that contains the target date."""
            for key, fact in group.items():
                if key == "__entity__":
                    continue
                val = str(getattr(fact, "value_text", "") or "")
                if target_date in val:
                    return val
            return None

        date_matched: list[dict[str, object]] = []
        time_vals: list[str] = []
        for g in row_groups:
            val = _fact_time_val(g)
            if val is not None:
                date_matched.append(g)
                time_vals.append(val)
        if not date_matched:
            return row_groups

        if target_hour:
            hour_matched = []
            for g, val in zip(date_matched, time_vals):
                hm = re.search(r"(\d{2}):\d{2}", val)
                if hm:
                    hour_matched.append((abs(int(hm.group(1)) - int(target_hour)), g))
            if hour_matched:
                best_delta = min(d for d, _ in hour_matched)
                date_matched = [g for d, g in hour_matched if d == best_delta]

        return date_matched

    # ── Direct-search fast path: query block metadata instead of facts ───

    _COLUMN_MAPPING_SCHEMA: dict = {
        "type": "object",
        "properties": {
            "mappings": {
                "type": "object",
                "description": "模板表头到源数据列名的映射。key=模板表头, value=源数据列名或null",
                "additionalProperties": {"type": ["string", "null"]},
            },
            "entity_column": {
                "type": "string",
                "description": "源数据中表示实体/城市的列名（如'城市'）",
            },
            "time_column": {
                "type": "string",
                "description": "源数据中表示时间/日期的列名（如'监测时间'）",
            },
        },
        "required": ["mappings", "entity_column", "time_column"],
        "additionalProperties": False,
    }

    def _try_direct_search(
        self,
        *,
        template_path: Path,
        output_path: Path,
        document_ids: list[str],
        user_requirement: str,
        fill_analysis: dict | None = None,
    ) -> list[FilledCellRecord] | None:
        """尝试直接从 block metadata 中查找数据填充模板，跳过 fact 系统。
        Try to fill the template by directly querying structured block metadata,
        bypassing the fact extraction pipeline entirely. Returns None if not applicable."""
        suffix = template_path.suffix.lower()

        # Collect structured blocks (table_row with row_values)
        structured_blocks: list[dict] = []
        all_source_headers: list[str] = []
        seen_headers: set[str] = set()
        docs_with_structured_data: set[str] = set()
        for doc_id in document_ids[:5]:
            blocks = self._repository.list_blocks(doc_id)
            for b in blocks:
                rv = b.metadata.get("row_values") if b.metadata else None
                if rv and isinstance(rv, dict):
                    structured_blocks.append(rv)
                    docs_with_structured_data.add(doc_id)
                    headers = b.metadata.get("headers")
                    if headers:
                        for h in headers:
                            if h not in seen_headers:
                                seen_headers.add(h)
                                all_source_headers.append(h)
        source_headers = all_source_headers
        if not structured_blocks or not source_headers:
            return None  # No structured data available — fall back

        # ── Source-type guard: only use direct search when xlsx sources dominate ──
        xlsx_doc_count = 0
        for doc_id in docs_with_structured_data:
            doc = self._repository.get_document(doc_id)
            if doc and doc.doc_type in ("xlsx", "xls", "csv"):
                xlsx_doc_count += 1
        total_docs = len(document_ids[:5])
        xlsx_ratio = xlsx_doc_count / total_docs if total_docs else 0
        if xlsx_ratio < 0.5:
            self._logger.info(
                "Direct search skipped: only %d/%d source docs are spreadsheet (%.0f%%, below 50%% threshold)",
                xlsx_doc_count, total_docs, xlsx_ratio * 100,
            )
            return None

        # Guard: if any selected source lacks structured data, fall back so
        # the LLM transform pipeline can process *all* sources (including text).
        missing_docs = set(document_ids[:5]) - docs_with_structured_data
        if missing_docs:
            self._logger.info(
                "Direct search skipped: %d/%d docs lack structured blocks (%s) — falling back to LLM pipeline",
                len(missing_docs), len(document_ids[:5]),
                sorted(missing_docs),
            )
            return None

        # Parse template tables
        if suffix == ".docx":
            document = load_docx_tables(template_path)
            tables = document.tables
        else:
            workbook = load_xlsx(template_path)
            tables = workbook.sheets  # type: ignore[assignment]

        known_field_names = set(source_headers)

        # ── 前置校验：模板字段与源数据表头必须有足够交集 ──
        # 收集模板所有表头，检查与 source_headers 的匹配度
        template_all_fields: set[str] = set()
        for table in tables:
            _, _, pre_field_columns = self._detect_layout(table, known_field_names)
            template_all_fields.update(fn for _, fn in pre_field_columns)
        if not template_all_fields:
            self._logger.info("Direct search skipped: no template fields detected")
            return None
        overlap = template_all_fields & known_field_names
        overlap_ratio = len(overlap) / len(template_all_fields) if template_all_fields else 0
        self._logger.info(
            "Direct search field overlap: template=%d, source=%d, overlap=%d (%.0f%%)",
            len(template_all_fields), len(known_field_names), len(overlap), overlap_ratio * 100,
        )
        if overlap_ratio < 0.3:
            self._logger.info("Direct search skipped: field overlap too low (%.0f%%)", overlap_ratio * 100)
            return None

        all_updates_xlsx: list[CellWrite] = []
        all_updates_docx: list[WordCellWrite] = []
        all_filled: list[FilledCellRecord] = []

        for table in tables:
            header_row, entity_column, field_columns = self._detect_layout(table, known_field_names)
            if header_row is None or entity_column is None:
                continue
            field_columns = self._llm_enhance_field_columns(table, header_row, field_columns, known_field_names)
            if not field_columns:
                continue

            # Get template header names for mapping
            template_headers = [fn for _, fn in field_columns]

            # LLM maps template headers → source column names (one call)
            col_map = self._build_column_mapping(template_headers, source_headers)
            if not col_map:
                continue

            # Parse per-table constraints from context
            context_text = getattr(table, "context_text", "") or ""
            entity_col_name = col_map.get("__entity_column__", "")
            time_col_name = col_map.get("__time_column__", "")
            constraints = self._parse_table_context(context_text, self._collect_unique_values(structured_blocks, entity_col_name))

            # ── Enrich constraints from fill_analysis per-table filters (LLM pre-analysis) ──
            table_idx = getattr(table, "table_index", None)
            if table_idx is None:
                # xlsx sheets: use enumeration order
                try:
                    table_idx = tables.index(table)
                except (ValueError, AttributeError):
                    table_idx = -1
            if fill_analysis and fill_analysis.get("per_table_filters"):
                for ptf in fill_analysis["per_table_filters"]:
                    if ptf.get("table_index") == table_idx:
                        if not constraints.get("primary_entity") and ptf.get("entity_filter"):
                            entity_str = ptf["entity_filter"].rstrip("市县区")
                            constraints["primary_entity"] = entity_str
                        if not constraints.get("date") and ptf.get("time_filter"):
                            # Extract date and hour from time_filter like "2025-11-25 09:00"
                            tf = ptf["time_filter"]
                            dm = re.match(r"(\d{4}-\d{1,2}-\d{1,2})", tf)
                            if dm:
                                constraints["date"] = dm.group(1)
                            hm = re.search(r"(\d{2}):\d{2}", tf)
                            if hm and not constraints.get("hour"):
                                constraints["hour"] = hm.group(1)
                        break

            # Enrich from fill_analysis date_range if available
            if fill_analysis and not constraints.get("date"):
                ar_from = fill_analysis.get("date_range_from", "")
                ar_to = fill_analysis.get("date_range_to", "")
                if ar_from and ar_from == ar_to:
                    constraints["date"] = ar_from

            # Enrich constraints from user_requirement if table context lacks entity/date
            if user_requirement:
                if not constraints.get("primary_entity") and entity_col_name:
                    ur_entities = parse_entity_filter_from_text(user_requirement)
                    if ur_entities:
                        # For multi-table templates, try to match table context to one entity
                        if len(ur_entities) == 1:
                            constraints["primary_entity"] = ur_entities[0]
                if not constraints.get("date"):
                    ur_date_from, ur_date_to = parse_date_range_from_text(user_requirement)
                    if ur_date_from and ur_date_from == ur_date_to:
                        constraints["date"] = ur_date_from

            # Filter blocks
            matching_rows = self._query_blocks(
                structured_blocks,
                entity_col_name=entity_col_name,
                target_entity=constraints.get("primary_entity", ""),
                time_col_name=time_col_name,
                target_date=constraints.get("date", ""),
                target_hour=constraints.get("hour", ""),
            )

            # Apply user_requirement date range filtering on structured rows
            if user_requirement and time_col_name:
                ur_date_from, ur_date_to = parse_date_range_from_text(user_requirement)
                if ur_date_from or ur_date_to:
                    matching_rows = self._filter_rows_by_date(matching_rows, time_col_name, ur_date_from, ur_date_to)

            # Filter out aggregate/summary rows (全国, 合计, etc.)
            if entity_col_name:
                matching_rows = [
                    r for r in matching_rows
                    if str(r.get(entity_col_name, "")).strip() not in _AGGREGATE_ENTITY_NAMES
                ]

            # Preserve source order — no reordering.
            # The fact store and block queries already return data in document order,
            # which typically matches the reference file's expected row order.

            rows_after_header = [row for row in table.rows if row.row_index > header_row]

            # Fill template
            self._logger.info(
                "Direct search: table=%s, constraints=%s, matched_rows=%d",
                getattr(table, "name", "?"), constraints, len(matching_rows),
            )
            next_row_index = max([header_row, *(r.row_index for r in rows_after_header)], default=header_row) + 1
            for idx, src_row in enumerate(matching_rows):
                target_row_idx = rows_after_header[idx].row_index if idx < len(rows_after_header) else next_row_index + (idx - len(rows_after_header))
                # Write entity cell
                entity_val = str(src_row.get(entity_col_name, ""))
                if suffix == ".docx":
                    all_updates_docx.append(WordCellWrite(
                        table_index=table.table_index, row_index=target_row_idx,
                        column_index=entity_column, value=entity_val,
                    ))
                else:
                    all_updates_xlsx.append(CellWrite(
                        sheet_name=table.name, cell_ref=build_cell_ref(target_row_idx, entity_column), value=entity_val,
                    ))
                # Write field cells
                for col_idx, template_field in field_columns:
                    source_col = col_map.get(template_field)
                    if not source_col:
                        continue
                    raw_val = src_row.get(source_col, "")
                    if not raw_val or str(raw_val).strip() in ("", "None"):
                        continue
                    cell_value: str | float | int = raw_val
                    try:
                        num = float(str(raw_val))
                        cell_value = int(num) if num == int(num) else num
                    except (ValueError, OverflowError):
                        cell_value = str(raw_val)
                    if suffix == ".docx":
                        all_updates_docx.append(WordCellWrite(
                            table_index=table.table_index, row_index=target_row_idx,
                            column_index=col_idx, value=cell_value,
                        ))
                    else:
                        all_updates_xlsx.append(CellWrite(
                            sheet_name=table.name, cell_ref=build_cell_ref(target_row_idx, col_idx), value=cell_value,
                        ))
                    all_filled.append(FilledCellRecord(
                        sheet_name=getattr(table, "name", ""),
                        cell_ref=f"R{target_row_idx}C{col_idx}" if suffix == ".docx" else build_cell_ref(target_row_idx, col_idx),
                        entity_name=entity_val,
                        field_name=template_field,
                        value=cell_value,
                        fact_id="direct_search",
                        confidence=1.0,
                        evidence_text=f"{source_col}={raw_val}",
                    ))

        if not all_filled:
            return None

        if suffix == ".docx":
            apply_docx_updates(template_path, output_path, all_updates_docx)
        else:
            apply_xlsx_updates(template_path, output_path, all_updates_xlsx)
        return all_filled

    def _build_column_mapping(
        self,
        template_headers: list[str],
        source_headers: list[str],
    ) -> dict[str, str]:
        """LLM 一次调用: 模板表头 → 源数据列名映射。
        One LLM call to map template headers to source column names."""
        # Fast path: exact / normalized match
        result: dict[str, str] = {}
        source_lower = {h.lower().strip(): h for h in source_headers}
        # Build canonical-name lookup: normalize each source header and map back
        source_canonical: dict[str, str] = {}
        for h in source_headers:
            canon = normalize_field_name(h)
            if canon and canon not in source_canonical:
                source_canonical[canon] = h
        unmatched: list[str] = []
        for th in template_headers:
            norm = normalize_field_name(th)
            if norm in source_lower:
                result[th] = source_lower[norm]
            elif th.lower().strip() in source_lower:
                result[th] = source_lower[th.lower().strip()]
            elif norm and norm in source_canonical:
                # Match via canonical field name (e.g., AQI → 空气质量指数)
                result[th] = source_canonical[norm]
            else:
                unmatched.append(th)

        if unmatched and self._openai_client.is_configured:
            try:
                payload = self._openai_client.create_json_completion(
                    system_prompt=(
                        "你是字段映射引擎。将模板表头映射到源数据列名。\n"
                        "规则：语义相同即匹配（如'空气质量指数'='AQI'）。无法匹配则填null。\n"
                        "同时识别源数据中的实体列（城市/地区）和时间列。"
                    ),
                    user_prompt=(
                        f"模板表头（待匹配）: {unmatched}\n"
                        f"源数据列名: {source_headers}\n"
                        '输出 JSON: {{"mappings": {{"模板表头": "源列名或null", ...}}, '
                        '"entity_column": "实体列名", "time_column": "时间列名"}}'
                    ),
                    json_schema=self._COLUMN_MAPPING_SCHEMA,
                )
                for th, src in (payload.get("mappings") or {}).items():
                    if src and src in source_headers:
                        result[th] = src
                result["__entity_column__"] = str(payload.get("entity_column", ""))
                result["__time_column__"] = str(payload.get("time_column", ""))
            except Exception:
                pass

        if "__entity_column__" not in result:
            # Guess entity column from common names
            for candidate in ("城市", "city", "国家/地区", "country/region", "地区", "区域", "实体", "国家"):
                if candidate in source_lower:
                    result["__entity_column__"] = source_lower[candidate]
                    break
        if "__time_column__" not in result:
            for candidate in ("监测时间", "时间", "日期", "date", "创建时间"):
                if candidate in source_lower:
                    result["__time_column__"] = source_lower[candidate]
                    break

        self._logger.info(
            "Column mapping result: template=%s → mapping=%s (entity=%s, time=%s)",
            template_headers,
            {k: v for k, v in result.items() if not k.startswith("__")},
            result.get("__entity_column__", "?"),
            result.get("__time_column__", "?"),
        )

        return result

    @staticmethod
    def _collect_unique_values(blocks: list[dict], column: str) -> list[str]:
        """从结构化 blocks 中收集某列的唯一值。"""
        if not column:
            return []
        seen: set[str] = set()
        result: list[str] = []
        for row in blocks:
            val = str(row.get(column, "")).strip().rstrip("市县区")
            if val and val not in seen:
                seen.add(val)
                result.append(val)
        return result

    @staticmethod
    def _query_blocks(
        blocks: list[dict],
        *,
        entity_col_name: str,
        target_entity: str,
        time_col_name: str,
        target_date: str,
        target_hour: str,
    ) -> list[dict]:
        """在结构化 blocks 中按实体和时间筛选。
        Query structured blocks by entity and time constraints."""
        results = blocks
        if target_entity and entity_col_name:
            results = [
                r for r in results
                if target_entity in str(r.get(entity_col_name, ""))
            ]
        if target_date:
            if time_col_name:
                results = [r for r in results if target_date in str(r.get(time_col_name, ""))]
            else:
                results = [r for r in results if any(target_date in str(v) for v in r.values())]
        if target_hour and results:
            hour_scored = []
            for r in results:
                time_val = str(r.get(time_col_name, "")) if time_col_name else ""
                hm = re.search(r"(\d{2}):\d{2}", time_val)
                if hm:
                    hour_scored.append((abs(int(hm.group(1)) - int(target_hour)), r))
            if hour_scored:
                best = min(d for d, _ in hour_scored)
                results = [r for d, r in hour_scored if d == best]
        return results

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
            self._logger.error("Template filling failed: %s", exc, exc_info=True)
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

    def _build_template_intent(
        self,
        template_path: Path,
        user_requirement: str = "",
    ) -> "TemplateIntent":
        """用 LLM 深度分析模板意图（带缓存），用于 intent_driven 模式。
        Deeply analyze the template intent with LLM (cached) for intent-driven mode.
        """
        from app.services.template_analyzer import analyze_template
        return analyze_template(
            template_path,
            self._openai_client,
            user_requirement=user_requirement,
        )

    def _run_intent_driven_pipeline(
        self,
        *,
        task_id: str,
        template_name: str,
        template_path: Path,
        output_path: Path,
        document_ids: list[str],
        user_requirement: str,
        fill_mode: str,
        resolved_output_file_name: str,
        persist_result: bool,
    ) -> TemplateResultRecord | None:
        """Intent-driven 三阶段 pipeline:
        Phase 1 → 模板意图分析
        Phase 2 → 基于意图的针对性抽取
        Phase 3 → 转换回填 + 校验
        """
        from app.services.template_filler import fill_by_intent

        # Phase 1: 模板意图分析
        intent = self._build_template_intent(template_path, user_requirement)
        if not intent.required_fields:
            self._logger.info("Intent-driven: no required fields detected, skipping")
            return None

        self._logger.info(
            "Intent-driven Phase 1: %d fields, entity_dim=%s",
            len(intent.required_fields),
            intent.entity_dimension,
        )

        # Phase 2: 基于意图的针对性抽取
        documents: list[DocumentRecord] = []
        blocks_by_doc: dict[str, list] = {}
        for doc_id in document_ids[:5]:
            doc = self._repository.get_document(doc_id)
            if doc is None:
                continue
            documents.append(doc)
            blocks_by_doc[doc_id] = self._repository.list_blocks(doc_id)

        if not documents:
            self._logger.warning("Intent-driven: no documents available")
            return None

        # ── Block 级预筛选: 按 entity_filter 缩减 blocks ──
        if intent.entity_filter:
            _entity_kw = intent.entity_filter  # e.g. ['济南', '青岛']
            total_before = sum(len(bs) for bs in blocks_by_doc.values())
            for doc_id in list(blocks_by_doc):
                blocks_by_doc[doc_id] = [
                    b for b in blocks_by_doc[doc_id]
                    if any(kw in (b.text or "") for kw in _entity_kw)
                    or b.block_type == "heading"  # 保留标题行供上下文
                ]
            total_after = sum(len(bs) for bs in blocks_by_doc.values())
            self._logger.info(
                "Block pre-filter by entity %s: %d → %d blocks",
                _entity_kw, total_before, total_after,
            )

        # FC-2: 选择抽取策略 — 大表格/少量文档 → 拼接模式
        total_blocks = sum(len(bs) for bs in blocks_by_doc.values())
        concat_mode = len(documents) <= 2 and total_blocks < 500

        if self._extraction_service is not None and hasattr(self._extraction_service, "extract_by_intent"):
            facts = self._extraction_service.extract_by_intent(
                intent,
                documents,
                blocks_by_doc,
                concat_mode=concat_mode,
            )
        else:
            self._logger.warning("Intent-driven: extraction_service unavailable, skipping")
            return None

        self._logger.info("Intent-driven Phase 2: extracted %d facts", len(facts))

        if not facts:
            return None

        # Phase 3: 回填 + 校验
        filled_cells, warnings = fill_by_intent(
            intent=intent,
            facts=facts,
            template_path=template_path,
            output_path=output_path,
            extraction_service=self._extraction_service,
            documents=documents,
            blocks_by_doc=blocks_by_doc,
        )

        if not filled_cells:
            self._logger.info("Intent-driven: fill produced 0 cells, falling back")
            return None

        self._logger.info(
            "Intent-driven Phase 3: %d cells filled, %d warnings",
            len(filled_cells),
            len(warnings),
        )

        result = TemplateResultRecord(
            task_id=task_id,
            template_name=template_name,
            output_path=str(output_path),
            output_file_name=resolved_output_file_name,
            created_at=datetime.now(timezone.utc),
            fill_mode="intent_driven",
            document_ids=document_ids,
            filled_cells=filled_cells,
            warnings=warnings,
        )
        if persist_result:
            self._repository.save_template_result(result)
        return result

    def _match_documents(
        self,
        profile: dict[str, object],
        candidates: list[dict[str, object]],
    ) -> tuple[list[str], str, str, list[dict[str, object]]]:
        """根据模板画像和候选文档摘要选择最相关的文档。
        Select the most relevant documents from candidate summaries using the template profile.

        优先使用向量检索匹配；若向量不可用则回退到 LLM / 规则匹配。
        """
        if len(candidates) == 1:
            doc_id = str(candidates[0]["doc_id"])
            return [doc_id], "single_candidate", "Only one parsed document is available.", candidates

        # ── Step 1: 文件名模糊预过滤 ──
        candidate_id_set = {str(c["doc_id"]) for c in candidates}
        filename_filtered = self._prefilter_by_filename(profile, candidates)
        narrow_ids = filename_filtered if filename_filtered else candidate_id_set

        # ── Step 2: 向量检索匹配（在预过滤后的候选集上） ──
        try:
            vector_result = self._match_documents_with_vector(profile, narrow_ids)
            if vector_result is not None:
                mode = "filename+vector" if filename_filtered else "vector"
                return vector_result[0], mode, vector_result[2], candidates
        except Exception as exc:
            self._logger.debug("Vector matching unavailable: %s", exc)

        # 若文件名预过滤有结果但向量无结果，直接使用文件名结果
        if filename_filtered:
            reason = f"文件名模糊匹配 {len(filename_filtered)} 个文档"
            self._logger.info("Filename pre-filter match: %s", reason)
            return list(filename_filtered), "filename", reason, candidates

        if self._openai_client.is_configured:
            try:
                return self._match_documents_with_openai(profile, candidates)
            except OpenAIClientError:
                pass
        return self._match_documents_with_rules(profile, candidates)

    def _prefilter_by_filename(
        self,
        profile: dict[str, object],
        candidates: list[dict[str, object]],
    ) -> set[str] | None:
        """基于模板名称与文档文件名的关键词重叠进行预过滤。
        Pre-filter candidates by keyword overlap between template name and document filenames.

        Returns a set of matching doc_ids, or None if no filename match found.
        """
        import re
        template_name = str(profile.get("template_name", ""))
        if not template_name:
            return None

        # 从模板名提取有意义的关键词（去除扩展名、'模板'等通用词）
        from pathlib import PurePosixPath
        stem = PurePosixPath(template_name).stem if "." in template_name else template_name
        # 拆分为 token：按非字母数字中文字符分割
        tokens = re.findall(r'[\u4e00-\u9fff]+|[a-zA-Z][\w-]*\d*|[\d]+', stem)
        # 过滤通用词和纯数字（年份等）
        stop_words = {"模板", "数据", "表", "汇总", "报告", "信息", "文件", "sheet", "table"}
        keywords = [t for t in tokens if t.lower() not in stop_words and len(t) >= 2 and not t.isdigit()]
        if not keywords:
            return None

        self._logger.info("Filename pre-filter keywords from template '%s': %s", stem, keywords)

        matched: set[str] = set()
        for c in candidates:
            doc_file = str(c.get("file_name", ""))
            if not doc_file:
                continue
            for kw in keywords:
                if kw.lower() in doc_file.lower():
                    matched.add(str(c["doc_id"]))
                    break

        if matched:
            self._logger.info("Filename pre-filter: %d/%d candidates matched keywords %s",
                              len(matched), len(candidates), keywords)
            return matched
        return None

    def _match_documents_with_vector(
        self,
        profile: dict[str, object],
        candidate_ids: set[str],
    ) -> tuple[list[str], str, str] | None:
        """使用 Embedding 向量检索找到与模板最相关的源文档。
        Use embedding vector search to find the most relevant source documents for the template.

        Returns (matched_ids, mode, reason) or None if vector search unavailable.
        """
        if self._embedding_service is None or not getattr(self._embedding_service, "is_configured", False):
            return None

        # 构造查询文本：模板名称(3x 权重) + 字段名 + 实体名 + 关键词
        query_parts: list[str] = []
        template_name = str(profile.get("template_name", ""))
        if template_name:
            # 文件名信号已注入 block 嵌入，查询端也提高模板名称权重
            from pathlib import PurePosixPath
            stem = PurePosixPath(template_name).stem if "." in template_name else template_name
            query_parts.extend([stem, stem, stem])
        for field_name in profile.get("field_names", [])[:5]:
            query_parts.append(str(field_name))
        for entity_name in profile.get("entity_names", [])[:10]:
            query_parts.append(str(entity_name))
        for keyword in profile.get("keywords", [])[:10]:
            query_parts.append(str(keyword))
        if not query_parts:
            return None

        query_text = " ".join(query_parts)
        self._logger.info("Vector match query: %s", query_text[:200])

        try:
            query_embedding = self._embedding_service.embed_query(query_text)
        except Exception as exc:
            self._logger.warning("Embedding query failed: %s", exc)
            return None

        # 按文档独立检索: 每个候选文档单独 top-k，避免大文档挤占小文档配额
        doc_scores: dict[str, float] = {}  # doc_id -> 平均相似度
        per_doc_k = 5
        min_score = 0.35  # 最低余弦相似度阈值

        for doc_id in candidate_ids:
            results = self._repository.vector_search_blocks(
                query_embedding,
                top_k=per_doc_k,
                document_ids={doc_id},
                min_score=min_score,
            )
            if results:
                scores = [score for _, score in results]
                avg_score = sum(scores) / len(scores)
                doc_scores[doc_id] = avg_score
                self._logger.debug("Vector per-doc: %s → %d hits, avg=%.3f, scores=%s",
                                   doc_id[:12], len(results), avg_score,
                                   [round(s, 3) for s in scores])
            else:
                self._logger.debug("Vector per-doc: %s → 0 hits above min_score=%.2f", doc_id[:12], min_score)

        if not doc_scores:
            return None

        # 按平均得分排序，取前 5 个且得分 >= 0.45 的文档
        score_threshold = 0.45
        sorted_docs = sorted(doc_scores.items(), key=lambda x: x[1], reverse=True)
        matched_ids = [
            doc_id for doc_id, avg in sorted_docs[:5]
            if avg >= score_threshold
        ]
        # 仅保留在候选集中的文档
        matched_ids = [doc_id for doc_id in matched_ids if doc_id in candidate_ids]

        if not matched_ids:
            return None

        reason = f"向量检索匹配 {len(matched_ids)} 个文档（per-doc top-{per_doc_k}, min_score≥{min_score}, avg_threshold≥{score_threshold}, query={query_text[:60]}…）"
        self._logger.info("Vector match: %s, scores=%s", reason, {k[:12]: round(v, 3) for k, v in sorted_docs[:8]})
        return matched_ids, "vector", reason

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
            if header_row is None or entity_column is None:
                continue
            field_columns = self._llm_enhance_field_columns(sheet, header_row, field_columns, known_field_names)
            if not field_columns:
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
            if header_row is None or entity_column is None:
                continue
            field_columns = self._llm_enhance_field_columns(table, header_row, field_columns, known_field_names)
            if not field_columns:
                continue
            # Scope entities and time per table via LLM + regex fallback
            table_entities = unique_entities
            table_row_groups = row_groups
            if table.context_text:
                ctx = self._parse_table_context(table.context_text, unique_entities)
                if ctx["primary_entity"]:
                    table_entities = [ctx["primary_entity"]]
                if ctx["date"]:
                    table_row_groups = self._filter_row_groups_by_time(
                        ctx["date"], ctx["hour"], row_groups,
                    )
            table_updates, table_filled_cells = self._build_docx_table_updates(
                table=table,
                header_row=header_row,
                entity_column=entity_column,
                field_columns=field_columns,
                fact_lookup=fact_lookup,
                unique_entities=table_entities,
                row_groups=table_row_groups,
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

    def _llm_enhance_field_columns(
        self,
        sheet_or_table: SpreadsheetSheet | WordTable,
        header_row: int | None,
        field_columns: list[tuple[int, str]],
        known_field_names: set[str],
    ) -> list[tuple[int, str]]:
        """当三层匹配遗漏列时，用 LLM 做语义字段映射补全。
        Use LLM to semantically match unresolved template headers to known fact field names.
        """
        if header_row is None or not known_field_names:
            return field_columns
        if not self._openai_client.is_configured:
            return field_columns

        # Find header row values
        header_values: list[str] = []
        for row in sheet_or_table.rows:
            if row.row_index == header_row:
                header_values = [str(v).strip() for v in row.values]
                break
        if not header_values:
            return field_columns

        # Identify which columns were already matched
        matched_cols = {col_idx for col_idx, _ in field_columns}
        unmatched: list[tuple[int, str]] = []
        for col_idx, raw_value in enumerate(header_values, start=1):
            if col_idx in matched_cols or not raw_value or is_entity_column(raw_value) or is_date_column(raw_value):
                continue
            stripped = strip_header_adornments(raw_value)
            if stripped and len(stripped) <= 40 and not stripped.isdigit():
                unmatched.append((col_idx, stripped))

        if not unmatched:
            return field_columns

        # Batch send to LLM
        unmatched_names = [name for _, name in unmatched]
        known_list = sorted(known_field_names)[:200]

        try:
            payload = self._openai_client.create_json_completion(
                system_prompt=(
                    "你是高精度数据字段匹配引擎。\n"
                    "规则：\n"
                    "1. 将每个模板表头映射到语义最接近的事实库字段。\n"
                    "2. 匹配时要考虑表头中可能包含的单位提示（如括号内的单位），这有助于区分相似字段。\n"
                    "3. 不同名称的指标是不同字段。名称中含有'人均'、'总量'、'增速'等修饰词时，它们代表不同指标，必须精确匹配到对应字段。\n"
                    "4. 如果表头确实无法匹配任何已有字段（含义完全不同），映射为 null。\n"
                    "5. 只输出 JSON，不要解释。\n"
                ),
                user_prompt=(
                    f"模板表头（待匹配）:\n{unmatched_names}\n\n"
                    f"事实库已有字段:\n{known_list}\n\n"
                    '输出格式: {{"mappings": [{{"header": "表头名", "field": "匹配的字段名或null"}}]}}'
                ),
                json_schema={
                    "type": "object",
                    "properties": {
                        "mappings": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "header": {"type": "string"},
                                    "field": {"type": ["string", "null"]},
                                },
                                "required": ["header", "field"],
                            },
                        },
                    },
                    "required": ["mappings"],
                    "additionalProperties": False,
                },
            )
        except (OpenAIClientError, Exception):
            self._logger.debug("LLM field matching failed", exc_info=True)
            return field_columns

        # Parse result and add newly matched columns
        raw_mappings = payload.get("mappings", [])
        if not isinstance(raw_mappings, list):
            return field_columns

        known_lower = {f.lower(): f for f in known_field_names}
        header_to_col = {name: col_idx for col_idx, name in unmatched}
        extra: list[tuple[int, str]] = []
        for item in raw_mappings:
            if not isinstance(item, dict):
                continue
            header = str(item.get("header", "")).strip()
            field_val = item.get("field")
            if not header or field_val is None:
                continue
            field_str = str(field_val).strip()
            if not field_str:
                continue
            # Validate the LLM's answer exists in known fields
            resolved = known_lower.get(field_str.lower())
            if resolved and header in header_to_col:
                col_idx = header_to_col[header]
                if col_idx not in matched_cols:
                    extra.append((col_idx, resolved))
                    matched_cols.add(col_idx)

        if extra:
            self._logger.info("LLM semantic matching added %d field columns", len(extra))
        return field_columns + extra

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

        # Preserve original entity order from fact extraction (e.g. ranked by GDP)
        # instead of alphabetical sort, so filled rows match source document ordering.

        # Use row_groups when template data area is empty or has only blank placeholder rows
        all_rows_empty = rows_after_header and all(
            not any(v.strip() for v in row.values) for row in rows_after_header
        )
        if row_groups and (not rows_after_header or all_rows_empty):
            field_name_set = {fn for _, fn in field_columns}
            entity_set = set(unique_entities)
            # Require each group to have at least 2 matching fields to avoid sparse rows
            min_fields = min(2, len(field_name_set))
            filtered_groups = [
                g for g in row_groups
                if sum(1 for fn in field_name_set if fn in g) >= min_fields
                and (not entity_set or normalize_entity_name(
                    getattr(g.get("__entity__"), "entity_name", "")) in entity_set)
            ]
            row_idx = 0
            for group in filtered_groups:
                entity_holder = group.get("__entity__")
                entity_name = getattr(entity_holder, "entity_name", "") if entity_holder else ""
                if row_idx < len(rows_after_header):
                    write_row(rows_after_header[row_idx].row_index, entity_name, group, True)
                else:
                    write_row(next_row_index, entity_name, group, True)
                    next_row_index += 1
                row_idx += 1
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

        # Preserve original entity order from fact extraction

        # Use row_groups when template data area is empty or has only blank placeholder rows
        all_rows_empty = rows_after_header and all(
            not any(v.strip() for v in row.values) for row in rows_after_header
        )
        if row_groups and (not rows_after_header or all_rows_empty):
            field_name_set = {fn for _, fn in field_columns}
            entity_set = set(unique_entities)
            # Require each group to have at least 2 matching fields to avoid sparse rows
            min_fields = min(2, len(field_name_set))
            filtered_groups = [
                g for g in row_groups
                if sum(1 for fn in field_name_set if fn in g) >= min_fields
                and (not entity_set or normalize_entity_name(
                    getattr(g.get("__entity__"), "entity_name", "")) in entity_set)
            ]
            row_idx = 0
            for group in filtered_groups:
                entity_holder = group.get("__entity__")
                entity_name = getattr(entity_holder, "entity_name", "") if entity_holder else ""
                if row_idx < len(rows_after_header):
                    write_row(rows_after_header[row_idx].row_index, entity_name, group, True)
                else:
                    write_row(next_row_index, entity_name, group, True)
                    next_row_index += 1
                row_idx += 1
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
