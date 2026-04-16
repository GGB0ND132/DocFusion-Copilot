"""Phase 3: 基于 TemplateIntent 的统一回填逻辑。

接收 Phase 1 (TemplateIntent) 和 Phase 2 (抽取事实) 的输出，
将事实映射到模板单元格并写出文件。

包含 Step 8 的抽取结果校验与二次抽取（retry）逻辑。
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.models.domain import FactRecord, FilledCellRecord
from app.schemas.templates import TemplateIntent
from app.utils.normalizers import (
    is_entity_column,
    normalize_entity_name,
    normalize_field_name,
    normalize_field_name_or_passthrough,
    strip_header_adornments,
)

if TYPE_CHECKING:
    from app.core.openai_client import OpenAICompatibleClient
    from app.services.fact_extraction import FactExtractionService
    from app.models.domain import DocumentRecord, DocumentBlock

logger = logging.getLogger("docfusion.template_filler")

_AGGREGATE_ENTITY_NAMES = frozenset({
    "全国", "合计", "总计", "平均", "全省", "全市", "总体",
    "全区", "均值", "中位数", "最大值", "最小值",
})


# ---------------------------------------------------------------------------
# Fact lookup construction
# ---------------------------------------------------------------------------

def _build_fact_lookup(facts: list[FactRecord]) -> dict[tuple[str, str], FactRecord]:
    """构建 (entity, field) → 最高置信度 Fact 索引，同时注册"市"变体。"""
    lookup: dict[tuple[str, str], FactRecord] = {}
    for fact in sorted(facts, key=lambda f: f.confidence, reverse=True):
        lookup.setdefault((fact.entity_name, fact.field_name), fact)
        entity = fact.entity_name
        if entity:
            alt = entity[:-1] if entity.endswith("市") else entity + "市"
            lookup.setdefault((alt, fact.field_name), fact)
    return lookup


def _collect_unique_entities(facts: list[FactRecord]) -> list[str]:
    """从事实中收集去重的非聚合行实体名。"""
    seen: set[str] = set()
    result: list[str] = []
    for f in facts:
        e = f.entity_name
        if e and e.strip() not in _AGGREGATE_ENTITY_NAMES and e not in seen:
            seen.add(e)
            result.append(e)
    return result


# ---------------------------------------------------------------------------
# Column-to-field mapping: connect template headers → FieldRequirement.name
# ---------------------------------------------------------------------------

def _build_header_field_map(
    headers: list[str],
    intent: TemplateIntent,
) -> dict[int, str]:
    """将模板表头列索引映射到 TemplateIntent 中的 field name。

    Returns {column_index → field_name} (0-based column index)
    """
    # Build a lookup: lowercase name → FieldRequirement.name
    intent_names_lower = {fr.name.lower(): fr.name for fr in intent.required_fields}
    mapping: dict[int, str] = {}
    entity_col: int | None = None
    for idx, header in enumerate(headers):
        stripped = strip_header_adornments(header)
        if not stripped:
            continue
        if is_entity_column(header):
            entity_col = idx
            continue
        # Exact match against intent field names (case-insensitive)
        if stripped.lower() in intent_names_lower:
            mapping[idx] = intent_names_lower[stripped.lower()]
            continue
        # Try canonical normalization
        canonical = normalize_field_name(stripped)
        if canonical and canonical.lower() in intent_names_lower:
            mapping[idx] = intent_names_lower[canonical.lower()]
            continue
        # Passthrough match
        passthrough = normalize_field_name_or_passthrough(stripped)
        if passthrough and passthrough.lower() in intent_names_lower:
            mapping[idx] = intent_names_lower[passthrough.lower()]
    return mapping


# ---------------------------------------------------------------------------
# XLSX fill
# ---------------------------------------------------------------------------

def _fill_xlsx_by_intent(
    template_path: Path,
    output_path: Path,
    intent: TemplateIntent,
    facts: list[FactRecord],
) -> list[FilledCellRecord]:
    """基于 TemplateIntent 回填 XLSX 模板。"""
    from app.utils.spreadsheet import CellWrite, apply_xlsx_updates, build_cell_ref, load_xlsx

    wb = load_xlsx(template_path)
    all_updates: list[CellWrite] = []
    all_filled: list[FilledCellRecord] = []
    fact_lookup = _build_fact_lookup(facts)
    unique_entities = _collect_unique_entities(facts)

    for sheet in wb.sheets:
        if not sheet.rows:
            continue
        # Find header row — first row
        header_row = sheet.rows[0]
        headers = [str(v or "").strip() for v in header_row.values]
        header_field_map = _build_header_field_map(headers, intent)
        if not header_field_map:
            continue

        # Detect entity column — first column matching is_entity_column, or default to 0
        entity_col = None
        for idx, h in enumerate(headers):
            if is_entity_column(h):
                entity_col = idx
                break
        if entity_col is None:
            entity_col = 0

        data_start_row = header_row.row_index + 1

        # Determine row-entity assignment
        # If template already has entity names in the entity column, use them
        existing_entities: list[tuple[int, str]] = []
        for row in sheet.rows[1:]:
            if row.row_index < data_start_row:
                continue
            entity_val = str(row.values[entity_col] if entity_col < len(row.values) else "").strip()
            if entity_val:
                existing_entities.append((row.row_index, entity_val))

        if existing_entities:
            # Template has pre-filled entity names → match facts to them
            for row_idx, entity_name in existing_entities:
                norm_entity = normalize_entity_name(entity_name) or entity_name
                for col_idx, field_name in header_field_map.items():
                    fact = fact_lookup.get((norm_entity, field_name)) or fact_lookup.get((entity_name, field_name))
                    if fact is None:
                        continue
                    cell_value = fact.value_num if fact.value_num is not None else fact.value_text
                    cell_ref = build_cell_ref(row_idx, col_idx + 1)
                    all_updates.append(CellWrite(sheet_name=sheet.name, cell_ref=cell_ref, value=cell_value))
                    all_filled.append(FilledCellRecord(
                        sheet_name=sheet.name,
                        cell_ref=cell_ref,
                        entity_name=entity_name,
                        field_name=field_name,
                        value=cell_value,
                        fact_id=fact.fact_id,
                        confidence=fact.confidence,
                        evidence_text=fact.metadata.get("evidence_text", fact.source_span[:100]) if fact.metadata else fact.source_span[:100],
                    ))
        else:
            # Template is blank → fill with all unique entities
            for row_offset, entity_name in enumerate(unique_entities):
                target_row = data_start_row + row_offset
                # Write entity name
                entity_ref = build_cell_ref(target_row, entity_col + 1)
                all_updates.append(CellWrite(sheet_name=sheet.name, cell_ref=entity_ref, value=entity_name))
                # Write field values
                for col_idx, field_name in header_field_map.items():
                    fact = fact_lookup.get((entity_name, field_name))
                    if fact is None:
                        continue
                    cell_value = fact.value_num if fact.value_num is not None else fact.value_text
                    cell_ref = build_cell_ref(target_row, col_idx + 1)
                    all_updates.append(CellWrite(sheet_name=sheet.name, cell_ref=cell_ref, value=cell_value))
                    all_filled.append(FilledCellRecord(
                        sheet_name=sheet.name,
                        cell_ref=cell_ref,
                        entity_name=entity_name,
                        field_name=field_name,
                        value=cell_value,
                        fact_id=fact.fact_id,
                        confidence=fact.confidence,
                        evidence_text=fact.metadata.get("evidence_text", fact.source_span[:100]) if fact.metadata else fact.source_span[:100],
                    ))

    if all_updates:
        apply_xlsx_updates(template_path, output_path, all_updates)
    return all_filled


# ---------------------------------------------------------------------------
# DOCX fill
# ---------------------------------------------------------------------------

def _fill_docx_by_intent(
    template_path: Path,
    output_path: Path,
    intent: TemplateIntent,
    facts: list[FactRecord],
) -> list[FilledCellRecord]:
    """基于 TemplateIntent 回填 DOCX 表格模板。"""
    from app.utils.wordprocessing import WordCellWrite, apply_docx_updates, load_docx_tables

    doc = load_docx_tables(template_path)
    all_updates: list[WordCellWrite] = []
    all_filled: list[FilledCellRecord] = []
    fact_lookup = _build_fact_lookup(facts)
    unique_entities = _collect_unique_entities(facts)

    for table in doc.tables:
        if not table.rows:
            continue
        header_row = table.rows[0]
        headers = [str(v or "").strip() for v in header_row.values]
        header_field_map = _build_header_field_map(headers, intent)
        if not header_field_map:
            continue

        entity_col = None
        for idx, h in enumerate(headers):
            if is_entity_column(h):
                entity_col = idx
                break
        if entity_col is None:
            entity_col = 0

        data_start_row = header_row.row_index + 1

        # Check for pre-filled entities
        existing_entities: list[tuple[int, str]] = []
        for row in table.rows[1:]:
            if row.row_index < data_start_row:
                continue
            entity_val = str(row.values[entity_col] if entity_col < len(row.values) else "").strip()
            if entity_val:
                existing_entities.append((row.row_index, entity_val))

        if existing_entities:
            for row_idx, entity_name in existing_entities:
                norm_entity = normalize_entity_name(entity_name) or entity_name
                for col_idx, field_name in header_field_map.items():
                    fact = fact_lookup.get((norm_entity, field_name)) or fact_lookup.get((entity_name, field_name))
                    if fact is None:
                        continue
                    cell_value = fact.value_num if fact.value_num is not None else fact.value_text
                    all_updates.append(WordCellWrite(
                        table_index=table.table_index,
                        row_index=row_idx,
                        column_index=col_idx,
                        value=cell_value,
                    ))
                    all_filled.append(FilledCellRecord(
                        sheet_name="",
                        cell_ref=f"T{table.table_index}R{row_idx}C{col_idx}",
                        entity_name=entity_name,
                        field_name=field_name,
                        value=cell_value,
                        fact_id=fact.fact_id,
                        confidence=fact.confidence,
                        evidence_text=fact.metadata.get("evidence_text", fact.source_span[:100]) if fact.metadata else fact.source_span[:100],
                    ))
        else:
            for row_offset, entity_name in enumerate(unique_entities):
                target_row = data_start_row + row_offset
                all_updates.append(WordCellWrite(
                    table_index=table.table_index,
                    row_index=target_row,
                    column_index=entity_col,
                    value=entity_name,
                ))
                for col_idx, field_name in header_field_map.items():
                    fact = fact_lookup.get((entity_name, field_name))
                    if fact is None:
                        continue
                    cell_value = fact.value_num if fact.value_num is not None else fact.value_text
                    all_updates.append(WordCellWrite(
                        table_index=table.table_index,
                        row_index=target_row,
                        column_index=col_idx,
                        value=cell_value,
                    ))
                    all_filled.append(FilledCellRecord(
                        sheet_name="",
                        cell_ref=f"T{table.table_index}R{target_row}C{col_idx}",
                        entity_name=entity_name,
                        field_name=field_name,
                        value=cell_value,
                        fact_id=fact.fact_id,
                        confidence=fact.confidence,
                        evidence_text=fact.metadata.get("evidence_text", fact.source_span[:100]) if fact.metadata else fact.source_span[:100],
                    ))

    if all_updates:
        apply_docx_updates(template_path, output_path, all_updates)
    return all_filled


# ---------------------------------------------------------------------------
# Step 9: Validation & retry
# ---------------------------------------------------------------------------

def _validate_and_retry(
    intent: TemplateIntent,
    facts: list[FactRecord],
    filled_cells: list[FilledCellRecord],
    extraction_service: "FactExtractionService",
    documents: list["DocumentRecord"],
    blocks_by_doc: dict[str, list["DocumentBlock"]],
) -> tuple[list[FactRecord], list[str]]:
    """校验抽取结果，对缺失字段触发二次抽取。

    Returns (补充后的 facts, warnings 列表)。
    """
    warnings: list[str] = []
    filled_field_names = {c.field_name for c in filled_cells}
    required_names = {fr.name for fr in intent.required_fields if not fr.is_computed}
    missing = required_names - filled_field_names

    if not missing:
        # Check low-confidence cells
        low_conf = [c for c in filled_cells if c.confidence < 0.7]
        for c in low_conf:
            warnings.append(
                f"低置信度: {c.entity_name}/{c.field_name} = {c.value} (confidence={c.confidence:.2f})"
            )
        return facts, warnings

    warnings.append(f"首次回填后仍缺失 {len(missing)} 个字段: {', '.join(sorted(missing)[:10])}")

    # Retry: broader context window extraction
    logger.info("Validation retry: %d missing fields, running broader extraction", len(missing))
    missing_field_reqs = [fr for fr in intent.required_fields if fr.name in missing]
    retry_facts: list[FactRecord] = []
    for doc in documents[:5]:
        blocks = blocks_by_doc.get(doc.doc_id, [])
        if not blocks:
            continue
        new_facts = extraction_service._intent_extract_single(
            intent, missing_field_reqs, doc, blocks,
        )
        retry_facts.extend(new_facts)

    if retry_facts:
        warnings.append(f"二次抽取补充 {len(retry_facts)} 条事实")
        facts = facts + retry_facts

    # Check low-confidence
    low_conf = [c for c in filled_cells if c.confidence < 0.7]
    for c in low_conf:
        warnings.append(
            f"低置信度: {c.entity_name}/{c.field_name} = {c.value} (confidence={c.confidence:.2f})"
        )

    return facts, warnings


# ---------------------------------------------------------------------------
# Public API: fill_by_intent
# ---------------------------------------------------------------------------

def fill_by_intent(
    *,
    intent: TemplateIntent,
    facts: list[FactRecord],
    template_path: Path,
    output_path: Path,
    extraction_service: "FactExtractionService | None" = None,
    documents: "list[DocumentRecord] | None" = None,
    blocks_by_doc: "dict[str, list[DocumentBlock]] | None" = None,
) -> tuple[list[FilledCellRecord], list[str]]:
    """Phase 3 核心: 基于 TemplateIntent + 抽取事实完成模板回填。

    Returns (filled_cells, warnings)
    """
    suffix = template_path.suffix.lower()

    if suffix == ".xlsx":
        filled = _fill_xlsx_by_intent(template_path, output_path, intent, facts)
    elif suffix == ".docx":
        filled = _fill_docx_by_intent(template_path, output_path, intent, facts)
    else:
        raise ValueError(f"Unsupported template type: {suffix}")

    # Step 9: validation & retry
    warnings: list[str] = []
    if extraction_service is not None and documents and blocks_by_doc:
        facts, warnings = _validate_and_retry(
            intent, facts, filled, extraction_service, documents, blocks_by_doc,
        )
        if warnings and any("二次抽取补充" in w for w in warnings):
            # Re-fill with augmented facts
            if suffix == ".xlsx":
                filled = _fill_xlsx_by_intent(template_path, output_path, intent, facts)
            else:
                filled = _fill_docx_by_intent(template_path, output_path, intent, facts)

    logger.info("fill_by_intent: %d cells filled, %d warnings", len(filled), len(warnings))
    return filled, warnings
