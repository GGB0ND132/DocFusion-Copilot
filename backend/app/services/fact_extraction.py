from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import replace

from app.core.catalog import FIELD_ALIASES, FIELD_CANONICAL_UNITS, FIELD_ENTITY_TYPES
from app.core.logging import ErrorCode, get_logger, log_operation
from app.models.domain import DocumentBlock, DocumentRecord, FactRecord
from app.utils.ids import new_id
from app.utils.normalizers import (
    convert_to_canonical_unit,
    extract_numeric_with_unit,
    find_entity_mentions,
    infer_year,
    is_date_column,
    is_entity_column,
    normalize_entity_name,
    normalize_field_name,
    normalize_field_name_or_passthrough,
    parse_date_value,
)

_BRACKET_UNIT_RE = re.compile(r"[（(](.*?)[)）]")
_GENERIC_NUMBER_RE = re.compile(r"(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>万亿元|亿元|万元|元|万人|人|%)?")
_DATE_RE = re.compile(r"(?P<date>(?:19|20)\d{2}[年/-]\d{1,2}[月/-]\d{1,2}日?)")
_TEXT_VALUE_RE = re.compile(r"(?:为|是|:|：)\s*(?P<value>[^，。；;\n]{2,40})")


class FactExtractionService:
    """从标准化文档块中抽取结构化事实。    Extract structured facts from normalized document blocks."""

    _logger = get_logger("fact_extraction")

    def __init__(self) -> None:
        """鍒濆鍖栬〃澶磋鍒欑紦瀛樸€?   Initialize caches for worksheet header profiles."""

        self._table_profile_cache: dict[tuple[str, ...], dict[str, object]] = {}

    def extract(self, document: DocumentRecord, blocks: list[DocumentBlock]) -> list[FactRecord]:
        """执行块级抽取并返回去重后的事实列表。    Run block-level extraction and return deduplicated facts."""

        with log_operation(self._logger, "fact_extract", doc_id=document.doc_id):
            facts: list[FactRecord] = []
            for block in blocks:
                if block.block_type == "table_row":
                    facts.extend(self._extract_from_table_row(document, block))
                    continue
                facts.extend(self._extract_from_text(document, block))
            result = list(self._deduplicate(facts).values())
            if not result:
                self._logger.warning(
                    "No facts extracted",
                    extra={"doc_id": document.doc_id, "error_code": ErrorCode.EXTRACT_NO_FACTS},
                )
            return result

    def _extract_from_table_row(self, document: DocumentRecord, block: DocumentBlock) -> list[FactRecord]:
        """从标准化表格行中抽取结构化事实。
        Extract structured facts from a normalized table row block.
        """

        row_values = block.metadata.get("row_values")
        if not isinstance(row_values, dict):
            return []
        headers = block.metadata.get("headers")
        if not isinstance(headers, list):
            headers = list(row_values.keys())
        profile = self._get_table_profile(headers)

        entity_name = ""
        row_date: str | None = None
        for header in profile["entity_headers"]:
            value = str(row_values.get(header, "")).strip()
            if value:
                entity_name = normalize_entity_name(value)
                break
        if not entity_name:
            for header in profile["fallback_headers"]:
                value = str(row_values.get(header, "")).strip()
                if not value:
                    continue
                mentions = find_entity_mentions(value)
                if mentions:
                    entity_name = mentions[0]
                    break

        for header in profile["date_headers"]:
            value = str(row_values.get(header, "")).strip()
            if value:
                row_date = parse_date_value(value)
                break

        facts: list[FactRecord] = []
        year = infer_year(block.text) or infer_year(document.file_name)

        for header, field_name, header_unit, is_catalog_field in profile["value_columns"]:
            raw_text = str(row_values.get(header, "")).strip()
            if not field_name or not raw_text:
                continue

            entity = entity_name or self._fallback_entity_from_text(block.text)
            if not entity and FIELD_ENTITY_TYPES.get(field_name) == "city":
                continue

            value_num, detected_unit = extract_numeric_with_unit(raw_text)
            if is_catalog_field:
                final_num, final_unit = convert_to_canonical_unit(field_name, value_num, detected_unit or header_unit)
            else:
                final_num = value_num
                final_unit = detected_unit or header_unit

            fact_metadata: dict[str, object] = {}
            if row_date:
                fact_metadata["date"] = row_date

            facts.append(
                FactRecord(
                    fact_id=new_id("fact"),
                    entity_type=FIELD_ENTITY_TYPES.get(field_name, "generic"),
                    entity_name=entity or document.file_name,
                    field_name=field_name,
                    value_num=final_num,
                    value_text=raw_text,
                    unit=final_unit if final_num is not None else None,
                    year=year,
                    source_doc_id=document.doc_id,
                    source_block_id=block.block_id,
                    source_span=block.text,
                    confidence=0.95 if entity else 0.86,
                    status="confirmed",
                    metadata=fact_metadata,
                )
            )
        return facts

    def _get_table_profile(self, headers: list[str]) -> dict[str, object]:
        """基于表头缓存表格抽取规则。
        Cache extraction rules derived from worksheet headers.
        """

        normalized_headers = tuple(str(header).strip() for header in headers)
        cached = self._table_profile_cache.get(normalized_headers)
        if cached is not None:
            return cached

        entity_headers: list[str] = []
        date_headers: list[str] = []
        fallback_headers: list[str] = []
        value_columns: list[tuple[str, str, str | None, bool]] = []

        for header in normalized_headers:
            if not header:
                continue
            if is_entity_column(header):
                entity_headers.append(header)
                continue
            if is_date_column(header):
                date_headers.append(header)
                continue

            field_name = normalize_field_name_or_passthrough(header)
            if not field_name:
                continue

            value_columns.append(
                (
                    header,
                    field_name,
                    self._extract_unit_from_header(header),
                    field_name in FIELD_ENTITY_TYPES or field_name in FIELD_CANONICAL_UNITS,
                )
            )
            if normalize_field_name(header) is None:
                fallback_headers.append(header)

        profile = {
            "entity_headers": tuple(entity_headers),
            "date_headers": tuple(date_headers),
            "fallback_headers": tuple(fallback_headers),
            "value_columns": tuple(value_columns),
        }
        self._table_profile_cache[normalized_headers] = profile
        return profile

    def _extract_from_text(self, document: DocumentRecord, block: DocumentBlock) -> list[FactRecord]:
        """从自由文本段落或标题中抽取事实。    Extract facts from free-form paragraph or heading text."""

        content = block.text
        if not content:
            return []

        entity_positions = self._find_entity_positions(content, block.section_path)
        year = infer_year(content) or infer_year(document.file_name)
        facts: list[FactRecord] = []
        occupied_spans: list[tuple[int, int]] = []

        alias_pairs = [
            (canonical_name, alias)
            for canonical_name, aliases in FIELD_ALIASES.items()
            for alias in {canonical_name, *aliases}
        ]
        alias_pairs.sort(key=lambda item: len(item[1]), reverse=True)

        for canonical_name, alias in alias_pairs:
            for match in re.finditer(re.escape(alias), content, flags=re.IGNORECASE):
                if self._is_overlapping(match.start(), match.end(), occupied_spans):
                    continue

                fact = self._build_text_fact(
                    document=document,
                    block=block,
                    content=content,
                    canonical_name=canonical_name,
                    entity_positions=entity_positions,
                    year=year,
                    match=match,
                )
                if fact is None:
                    continue
                facts.append(fact)
                occupied_spans.append((match.start(), match.end()))
        return facts

    def _build_text_fact(
        self,
        *,
        document: DocumentRecord,
        block: DocumentBlock,
        content: str,
        canonical_name: str,
        entity_positions: list[tuple[int, str]],
        year: int | None,
        match: re.Match[str],
    ) -> FactRecord | None:
        """根据别名命中结果构建一条文本事实。    Build one text fact from a matched alias occurrence."""

        entity_name = self._resolve_nearest_entity(entity_positions, match.start())
        if not entity_name and FIELD_ENTITY_TYPES.get(canonical_name) == "city":
            return None

        value_num: float | None = None
        value_text = ""
        unit: str | None = None

        if canonical_name in {"甲方", "乙方"}:
            value_text = self._find_text_value_after_alias(content, match.end())
            if not value_text:
                return None
        elif canonical_name == "签订日期":
            value_text = self._find_date_after_alias(content, match.end())
            if not value_text:
                return None
        else:
            value_num, detected_unit = self._find_numeric_after_alias(content, match.end())
            if value_num is None:
                return None
            value_num, unit = convert_to_canonical_unit(canonical_name, value_num, detected_unit)
            value_text = content[max(0, match.start() - 16) : min(len(content), match.end() + 24)].strip()

        confidence = 0.88 if entity_name else 0.72
        return FactRecord(
            fact_id=new_id("fact"),
            entity_type=FIELD_ENTITY_TYPES.get(canonical_name, "generic"),
            entity_name=entity_name or document.file_name,
            field_name=canonical_name,
            value_num=value_num,
            value_text=value_text,
            unit=unit,
            year=year,
            source_doc_id=document.doc_id,
            source_block_id=block.block_id,
            source_span=content,
            confidence=confidence,
            status="confirmed" if confidence >= 0.8 else "pending_review",
        )

    def _deduplicate(self, facts: list[FactRecord]) -> OrderedDict[tuple[str, str, str, str], FactRecord]:
        """合并重复事实并保留最高置信度版本。    Collapse duplicate facts while keeping the highest-confidence copy."""

        deduplicated: OrderedDict[tuple[str, str, str, str], FactRecord] = OrderedDict()
        for fact in facts:
            key = (
                fact.entity_name,
                fact.field_name,
                fact.value_text,
                fact.source_block_id,
            )
            existing = deduplicated.get(key)
            if existing is None or fact.confidence > existing.confidence:
                deduplicated[key] = replace(fact)
        return deduplicated

    def _find_entity_positions(self, text: str, section_path: list[str]) -> list[tuple[int, str]]:
        """定位文本中的候选实体位置。    Locate candidate entity mentions and their positions inside text."""

        found: list[tuple[int, str]] = []
        seen: set[str] = set()
        for entity_name in find_entity_mentions(text, section_path):
            position = text.find(entity_name)
            if position >= 0 and entity_name not in seen:
                found.append((position, entity_name))
                seen.add(entity_name)
        for section in section_path:
            normalized = normalize_entity_name(section)
            if normalized and normalized not in seen and normalized in text:
                found.append((text.find(normalized), normalized))
                seen.add(normalized)
        return sorted(found, key=lambda item: item[0])

    def _resolve_nearest_entity(self, positions: list[tuple[int, str]], anchor: int) -> str | None:
        """选择离字段别名最近的实体。    Choose the entity mention closest to a field alias occurrence."""

        if not positions:
            return None
        previous_entities = [item for item in positions if item[0] <= anchor]
        if previous_entities:
            return previous_entities[-1][1]
        return positions[0][1]

    def _find_numeric_after_alias(self, text: str, anchor_end: int) -> tuple[float | None, str | None]:
        """在字段别名后方查找最近数值。    Find the nearest numeric expression after a detected field alias."""

        window = text[anchor_end : min(len(text), anchor_end + 36)]
        match = _GENERIC_NUMBER_RE.search(window)
        if not match or match.group("value") is None:
            return None, None
        return float(match.group("value").replace(",", "")), match.group("unit")

    def _find_date_after_alias(self, text: str, anchor_end: int) -> str:
        """在字段别名后方查找日期值。    Find a date value after a detected field alias."""

        window = text[anchor_end : min(len(text), anchor_end + 40)]
        match = _DATE_RE.search(window)
        return match.group("date") if match else ""

    def _find_text_value_after_alias(self, text: str, anchor_end: int) -> str:
        """在字段别名后方提取短文本值。    Extract a short free-text value after a detected field alias."""

        window = text[anchor_end : min(len(text), anchor_end + 48)]
        match = _TEXT_VALUE_RE.search(window)
        return match.group("value").strip() if match else ""

    def _extract_unit_from_header(self, header: str) -> str | None:
        """从表头中读取单位提示。    Read a unit hint from a table header if one is present."""

        match = _BRACKET_UNIT_RE.search(header)
        if not match:
            return None
        return match.group(1).strip()

    def _fallback_entity_from_text(self, text: str) -> str:
        """回退到文本片段中首个检测到的实体。    Fallback to the first detected entity mention in a text snippet."""

        entities = find_entity_mentions(text)
        return entities[0] if entities else ""

    def _is_overlapping(self, start: int, end: int, spans: list[tuple[int, int]]) -> bool:
        """判断当前命中是否与已接受命中重叠。    Return whether the current match overlaps an accepted span."""

        return any(start < existing_end and end > existing_start for existing_start, existing_end in spans)
