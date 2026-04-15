from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import replace
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from app.schemas.templates import TemplateIntent

_BRACKET_UNIT_RE = re.compile(r"[（(](.*?)[)）]")
_GENERIC_NUMBER_RE = re.compile(r"(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>万亿元|亿元|万元|元|万人|人|万|%)?")
_DATE_RE = re.compile(r"(?P<date>(?:19|20)\d{2}[年/-]\d{1,2}[月/-]\d{1,2}日?)")
_TEXT_VALUE_RE = re.compile(r"(?:为|是|:|：)\s*(?P<value>[^，。；;\n]{2,40})")
# CJK-ASCII boundary space normalization
_CJK_RANGE = r'\u4e00-\u9fff\u3400-\u4dbf'
_COLLAPSE_SPACE_RE = re.compile(
    rf'(?<=[{_CJK_RANGE}])\s+(?=[A-Za-z0-9%])|(?<=[A-Za-z0-9%])\s+(?=[{_CJK_RANGE}])'
)

# Unit compatibility: fields whose value should NOT be in '%'
_MONETARY_UNITS = {'亿元', '万元', '元', '万亿元'}
_POPULATION_UNITS = {'万人', '人', '万'}


def _is_unit_compatible(canonical_name: str, detected_unit: str | None) -> bool:
    """检查检测到的单位是否与字段的标准单位兼容。"""
    if not detected_unit:
        return True
    canonical_unit = FIELD_CANONICAL_UNITS.get(canonical_name)
    if not canonical_unit:
        return True
    # '%' is only valid for percentage fields
    if detected_unit == '%' and canonical_unit != '%':
        return False
    # monetary unit for population field or vice versa
    if canonical_unit in _MONETARY_UNITS and detected_unit in _POPULATION_UNITS:
        return False
    if canonical_unit in _POPULATION_UNITS and detected_unit in _MONETARY_UNITS:
        return False
    # Per-capita fields (canonical unit '元') should not match '亿元' or '万元'
    # because per-capita values are typically small (tens of thousands), not billions.
    if canonical_unit == '元' and detected_unit in {'亿元', '万亿元'}:
        return False
    # Population fields should not match '元' variants
    if canonical_unit == '万人' and detected_unit in _MONETARY_UNITS:
        return False
    return True


class FactExtractionService:
    """从标准化文档块中抽取结构化事实。    Extract structured facts from normalized document blocks."""

    _logger = get_logger("fact_extraction")

    def __init__(self, openai_client: object | None = None) -> None:
        """初始化表头规则缓存。   Initialize caches for worksheet header profiles."""

        self._table_profile_cache: dict[tuple[str, ...], dict[str, object]] = {}
        self._openai_client = openai_client

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
            if not result and blocks:
                llm_facts = self._extract_with_llm_fallback(document, blocks)
                if llm_facts:
                    result = list(self._deduplicate(llm_facts).values())
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

        original_content = block.text
        if not original_content:
            return []

        # Collapse spaces at CJK-ASCII boundaries so aliases like 'GDP总量'
        # match text like 'GDP 总量' or '人均 GDP'.
        content = _COLLAPSE_SPACE_RE.sub('', original_content)

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
                if self._is_alias_part_of_longer_field(content, match, canonical_name):
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
            # If forward-found unit is incompatible with this field, discard and try backward
            if value_num is not None and not _is_unit_compatible(canonical_name, detected_unit):
                value_num, detected_unit = None, None
            if value_num is None:
                value_num, detected_unit = self._find_numeric_before_alias(content, match.start())
            # Validate backward result too
            if value_num is not None and not _is_unit_compatible(canonical_name, detected_unit):
                return None
            if value_num is None:
                return None
            value_num, unit = convert_to_canonical_unit(canonical_name, value_num, detected_unit)
            value_text = content[max(0, match.start() - 24) : min(len(content), match.end() + 24)].strip()

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

        window = text[anchor_end : min(len(text), anchor_end + 40)]
        match = _GENERIC_NUMBER_RE.search(window)
        if not match or match.group("value") is None:
            return None, None
        return float(match.group("value").replace(",", "")), match.group("unit")

    def _find_numeric_before_alias(self, text: str, anchor_start: int) -> tuple[float | None, str | None]:
        """在字段别名前方查找最近数值（中文常将数值置于字段名前）。
        Find the nearest numeric expression before a detected field alias.
        Chinese text often places values before field names, e.g. '56,708.71亿元的GDP总量'.
        """

        before_start = max(0, anchor_start - 40)
        window = text[before_start:anchor_start]
        matches = list(_GENERIC_NUMBER_RE.finditer(window))
        if not matches:
            return None, None
        last = matches[-1]
        value_str = last.group("value")
        if value_str is None:
            return None, None
        num = float(value_str.replace(",", ""))
        # Skip if it looks like a year (e.g. 2025)
        if last.group("unit") is None and 1900 <= num <= 2100 and num == int(num):
            if len(matches) >= 2:
                last = matches[-2]
                value_str = last.group("value")
                if value_str is None:
                    return None, None
                num = float(value_str.replace(",", ""))
            else:
                return None, None
        return num, last.group("unit")

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

    def _is_alias_part_of_longer_field(self, content: str, match: re.Match[str], canonical_name: str) -> bool:
        """检测短别名是否属于文本中另一个更长字段名的一部分。
        Detect whether a short alias is embedded in a longer, different field name in the text.

        Example: alias 'GDP' inside '人均GDP' should be skipped because '人均GDP' is a separate field.
        Note: content has already been space-normalized, so '人均 GDP' becomes '人均GDP'.
        """
        context_start = max(0, match.start() - 8)
        context_end = min(len(content), match.end() + 8)
        context = content[context_start:context_end].lower()
        matched_text = match.group().lower()
        for other_canonical, other_aliases in FIELD_ALIASES.items():
            if other_canonical == canonical_name:
                continue
            for other_alias in {other_canonical, *other_aliases}:
                if len(other_alias) <= len(matched_text):
                    continue
                if matched_text in other_alias.lower() and other_alias.lower() in context:
                    return True
        return False

    def _extract_with_llm_fallback(self, document: DocumentRecord, blocks: list[DocumentBlock]) -> list[FactRecord]:
        """当规则引擎未抽取到事实时，使用 LLM 进行语义抽取。
        Use LLM to extract facts when the rule engine yields nothing.
        """

        if self._openai_client is None or not getattr(self._openai_client, "is_configured", False):
            return []

        text_blocks = [b for b in blocks if b.block_type in {"paragraph", "heading", "page"}]
        if not text_blocks:
            return []

        preview = "\n".join(b.text[:400] for b in text_blocks[:20])[:6000]
        year = infer_year(preview) or infer_year(document.file_name)

        try:
            from app.core.openai_client import OpenAIClientError
            payload = self._openai_client.create_json_completion(
                system_prompt=(
                    "你是高精度结构化信息抽取引擎。从给定文本中提取所有可量化的事实。\n"
                    "严格规则：\n"
                    "1. 每条事实必须包含 entity_name（实体名，如城市/国家/公司/人名等）、field_name（指标名）、value（原始数值，保留原文精度）、unit（原文单位）。\n"
                    "2. value 必须是文本中明确出现的原始数值，不要做单位换算或四舍五入。\n"
                    "3. 不同名称的指标是不同字段，即使含义相似也不要混淆。名称中含有'人均'、'总量'、'增速'等修饰词时，它们分别代表不同指标。\n"
                    "4. entity_name 应使用文本中的实体简称（如城市名不带'市'后缀），不要使用文档名。\n"
                    "5. 只输出文本中明确给出的数据，绝不编造。尽可能多地提取（最多200条）。\n"
                ),
                user_prompt=f"文档名: {document.file_name}\n\n文本内容:\n{preview}",
                json_schema={
                    "type": "object",
                    "properties": {
                        "facts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "entity_name": {"type": "string"},
                                    "field_name": {"type": "string"},
                                    "value": {"type": "string"},
                                    "unit": {"type": "string"},
                                },
                                "required": ["entity_name", "field_name", "value"],
                            },
                        },
                    },
                    "required": ["facts"],
                    "additionalProperties": False,
                },
            )
        except Exception:
            self._logger.debug("LLM fact extraction fallback failed", exc_info=True)
            return []

        raw_facts = payload.get("facts", [])
        if not isinstance(raw_facts, list):
            return []

        results: list[FactRecord] = []
        first_block_id = blocks[0].block_id if blocks else ""
        for item in raw_facts:
            if not isinstance(item, dict):
                continue
            entity = str(item.get("entity_name", "")).strip()
            field = str(item.get("field_name", "")).strip()
            raw_value = str(item.get("value", "")).strip()
            unit = str(item.get("unit", "")).strip() or None
            if not entity or not field or not raw_value:
                continue

            value_num, detected_unit = extract_numeric_with_unit(raw_value)
            if value_num is not None:
                canonical_field = normalize_field_name(field)
                if canonical_field:
                    value_num, unit = convert_to_canonical_unit(canonical_field, value_num, detected_unit or unit)
                    field = canonical_field

            entity = normalize_entity_name(entity) or entity

            results.append(
                FactRecord(
                    fact_id=new_id("fact"),
                    entity_type=FIELD_ENTITY_TYPES.get(field, "generic"),
                    entity_name=entity,
                    field_name=field,
                    value_num=value_num,
                    value_text=raw_value,
                    unit=unit if value_num is not None else None,
                    year=year,
                    source_doc_id=document.doc_id,
                    source_block_id=first_block_id,
                    source_span=preview[:200],
                    confidence=0.75,
                    status="pending_review",
                    metadata={"extraction_method": "llm_fallback"},
                )
            )

        self._logger.info(
            "LLM fallback extracted %d facts",
            len(results),
            extra={"doc_id": document.doc_id},
        )
        return results

    def extract_targeted_fields(
        self,
        document: DocumentRecord,
        blocks: list[DocumentBlock],
        target_fields: list[str],
        target_entities: list[str] | None = None,
    ) -> list[FactRecord]:
        """针对模板需要但 fact_store 缺失的字段，定向调用 LLM 提取。
        Extract specific fields requested by a template using targeted LLM queries.
        """
        if not self._openai_client or not getattr(self._openai_client, "is_configured", False):
            return []
        if not target_fields or not blocks:
            return []

        text_blocks = [b for b in blocks if b.block_type in {"paragraph", "heading", "page", "table_row"}]
        if not text_blocks:
            return []

        preview = "\n".join(b.text[:400] for b in text_blocks[:25])[:8000]
        year = infer_year(preview) or infer_year(document.file_name)
        entity_hint = ""
        if target_entities:
            entity_hint = f"\n目标实体列表（请为每个实体提取所有目标字段的值）:\n{', '.join(target_entities[:100])}"

        # Build field-unit hints from catalog
        field_unit_hints: list[str] = []
        for field in target_fields[:50]:
            canonical_unit = FIELD_CANONICAL_UNITS.get(field, "")
            if canonical_unit:
                field_unit_hints.append(f"  - {field}（标准单位: {canonical_unit}）")
            else:
                field_unit_hints.append(f"  - {field}")
        fields_description = "\n".join(field_unit_hints)

        try:
            payload = self._openai_client.create_json_completion(
                system_prompt=(
                    "你是高精度结构化信息定向抽取引擎。\n"
                    "严格规则：\n"
                    "1. 从文本中精确查找用户指定的目标字段对应的数值。\n"
                    "2. value 必须填写文本中原始出现的数值（保留原文精度），不要换算单位。\n"
                    "3. unit 必须填写文本中该数值紧跟的原始单位。\n"
                    "4. entity_name 应使用文本中的实体简称（如城市名不带'市'后缀）。\n"
                    "5. field_name 必须严格使用下方目标字段列表中给出的名称，不要自创。\n"
                    "6. 不同名称的指标是不同字段。注意每个目标字段旁标注的标准单位，它提示了该字段的量级和类型，帮助你区分相似指标。\n"
                    "7. 只输出文本中明确给出的数据，绝不编造。为每个实体尽可能提取所有目标字段。\n"
                ),
                user_prompt=(
                    f"文档名: {document.file_name}\n"
                    f"目标字段:\n{fields_description}"
                    f"{entity_hint}\n\n"
                    f"文本内容:\n{preview}"
                ),
                json_schema={
                    "type": "object",
                    "properties": {
                        "facts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "entity_name": {"type": "string"},
                                    "field_name": {"type": "string"},
                                    "value": {"type": "string"},
                                    "unit": {"type": "string"},
                                },
                                "required": ["entity_name", "field_name", "value"],
                            },
                        },
                    },
                    "required": ["facts"],
                    "additionalProperties": False,
                },
            )
        except Exception:
            self._logger.debug("Targeted LLM extraction failed", exc_info=True)
            return []

        raw_facts = payload.get("facts", [])
        if not isinstance(raw_facts, list):
            return []

        # Only accept facts whose field_name matches one of the target fields
        target_set_lower = {f.lower() for f in target_fields}
        results: list[FactRecord] = []
        first_block_id = blocks[0].block_id if blocks else ""

        for item in raw_facts:
            if not isinstance(item, dict):
                continue
            entity = str(item.get("entity_name", "")).strip()
            field = str(item.get("field_name", "")).strip()
            raw_value = str(item.get("value", "")).strip()
            unit = str(item.get("unit", "")).strip() or None
            if not entity or not field or not raw_value:
                continue
            if field.lower() not in target_set_lower:
                continue

            value_num, detected_unit = extract_numeric_with_unit(raw_value)
            # Resolve field name to canonical form if possible
            canonical_field = normalize_field_name(field)
            resolved_field = canonical_field or field
            if resolved_field.lower() not in target_set_lower:
                resolved_field = field
            # Convert to canonical unit using the resolved field name
            final_unit = detected_unit or unit
            if value_num is not None and canonical_field:
                value_num, final_unit = convert_to_canonical_unit(canonical_field, value_num, final_unit)
            entity = normalize_entity_name(entity) or entity

            results.append(
                FactRecord(
                    fact_id=new_id("fact"),
                    entity_type=FIELD_ENTITY_TYPES.get(resolved_field, "generic"),
                    entity_name=entity,
                    field_name=resolved_field,
                    value_num=value_num,
                    value_text=raw_value,
                    unit=final_unit if value_num is not None else None,
                    year=year,
                    source_doc_id=document.doc_id,
                    source_block_id=first_block_id,
                    source_span=preview[:200],
                    confidence=0.90,
                    status="confirmed",
                    metadata={"extraction_method": "llm_targeted"},
                )
            )

        self._logger.info(
            "Targeted LLM extracted %d facts for %d requested fields",
            len(results),
            len(target_fields),
            extra={"doc_id": document.doc_id},
        )
        return results

    # ------------------------------------------------------------------
    # Phase 2: Intent-driven extraction  (模板驱动的意图抽取)
    # ------------------------------------------------------------------

    def extract_by_intent(
        self,
        intent: "TemplateIntent",
        documents: list[DocumentRecord],
        blocks_by_doc: dict[str, list[DocumentBlock]],
        *,
        concat_mode: bool = False,
    ) -> list[FactRecord]:
        """基于 TemplateIntent 对源文档做针对性抽取。

        Phase 2 核心函数，区别于旧版 ``extract()`` 的两个关键改进：
        1. prompt 中包含完整的字段语义描述（名称+描述+类型+单位+示例），
           而非仅仅传字段名。
        2. FC-2: 支持两种多文档抽取策略：
           - ``concat_mode=False`` (默认) → 逐文档抽取，控制上下文长度。
           - ``concat_mode=True``         → 拼接所有文档后一次抽取，
             适用于大表格/少量文档场景。

        Parameters
        ----------
        intent : TemplateIntent
            Phase 1 分析得到的模板意图。
        documents : list[DocumentRecord]
            源文档列表。
        blocks_by_doc : dict[str, list[DocumentBlock]]
            按 doc_id 索引的文档块。
        concat_mode : bool
            FC-2: 是否使用拼接模式。

        Returns
        -------
        list[FactRecord]
            抽取到的事实列表（带 evidence metadata）。
        """
        from app.schemas.templates import TemplateIntent  # noqa: F811

        if not intent.required_fields:
            return []

        # Step A: 规则先行 — 仅对结构化块做快速规则抽取（不触发 LLM fallback）
        rule_facts: list[FactRecord] = []
        for doc in documents:
            blocks = blocks_by_doc.get(doc.doc_id, [])
            for block in blocks:
                if block.block_type == "table_row":
                    rule_facts.extend(self._extract_from_table_row(doc, block))
                else:
                    rule_facts.extend(self._extract_from_text(doc, block))
        rule_facts = list(self._deduplicate(rule_facts).values())

        # ── Fact 级过滤: 根据 intent 的 date_filter / entity_filter 缩减数据 ──
        pre_filter_count = len(rule_facts)
        rule_facts = self._apply_intent_filters(rule_facts, intent)
        if len(rule_facts) != pre_filter_count:
            self._logger.info(
                "Intent filter: %d → %d facts (date=%s, entities=%s)",
                pre_filter_count, len(rule_facts),
                intent.date_filter, intent.entity_filter,
            )

        # 已抽取到的字段集合
        extracted_fields = {f.field_name for f in rule_facts}
        # 计算仍缺失的字段
        missing_fields = [
            fr for fr in intent.required_fields
            if fr.name not in extracted_fields and not fr.is_computed
        ]

        if not missing_fields:
            # 所有字段都已被规则抽取命中
            return self._enrich_evidence(rule_facts, "rule")

        # Step B: LLM 定向抽取缺失字段
        if not self._openai_client or not getattr(self._openai_client, "is_configured", False):
            return self._enrich_evidence(rule_facts, "rule")

        llm_facts: list[FactRecord] = []
        if concat_mode:
            # FC-2: 拼接模式 — 所有文档合并为一个大上下文
            llm_facts.extend(self._intent_extract_concat(intent, missing_fields, documents, blocks_by_doc))
        else:
            # FC-2: 逐文档模式（默认）
            for doc in documents[:5]:
                blocks = blocks_by_doc.get(doc.doc_id, [])
                if not blocks:
                    continue
                doc_facts = self._intent_extract_single(intent, missing_fields, doc, blocks)
                llm_facts.extend(doc_facts)
                # 更新缺失字段
                extracted_fields.update(f.field_name for f in doc_facts)
                missing_fields = [
                    fr for fr in missing_fields
                    if fr.name not in extracted_fields
                ]
                if not missing_fields:
                    break

        all_facts = rule_facts + llm_facts
        return list(self._deduplicate(all_facts).values())

    @staticmethod
    def _apply_intent_filters(
        facts: list[FactRecord],
        intent: "TemplateIntent",
    ) -> list[FactRecord]:
        """按 intent 的 date_filter 和 entity_filter 过滤 facts。

        - date_filter: 检查 fact.metadata['date'] 或 fact.year
        - entity_filter: 检查 fact.entity_name 是否包含任一目标实体
        无约束或无相关 metadata 的 fact 保留（宁可多不可漏）。
        """
        from app.schemas.templates import TemplateIntent  # noqa: F811

        date_from, date_to = intent.date_filter if intent.date_filter else (None, None)
        entity_targets = intent.entity_filter or []

        if not date_from and not date_to and not entity_targets:
            return facts

        filtered: list[FactRecord] = []
        entity_set = {e.lower() for e in entity_targets}

        for fact in facts:
            # ── Date check ──
            if date_from or date_to:
                fact_date = (fact.metadata.get("date") if fact.metadata else None)
                fact_year = fact.year
                if fact_date:
                    date_str = str(fact_date)
                    if date_from and date_str < date_from:
                        continue
                    if date_to and date_str > date_to:
                        continue
                elif fact_year is not None:
                    if date_from and fact_year < int(date_from[:4]):
                        continue
                    if date_to and fact_year > int(date_to[:4]):
                        continue
                # 无日期信息 → 保留

            # ── Entity check ──
            if entity_targets:
                raw_entity = (fact.entity_name or "").lower()
                # Safe suffix stripping: only remove trailing 市/县/区/省 if result >= 2 chars
                entity_name = raw_entity
                if len(entity_name) >= 3 and entity_name[-1] in "市县区省":
                    entity_name = entity_name[:-1]
                if entity_name and entity_name not in entity_set:
                    # 部分匹配兜底 (如 "济南高新区" 包含 "济南")
                    if not any(t in entity_name or entity_name in t for t in entity_set):
                        continue

            filtered.append(fact)
        return filtered

    def _intent_extract_single(
        self,
        intent: "TemplateIntent",
        missing_fields: list,
        document: DocumentRecord,
        blocks: list[DocumentBlock],
    ) -> list[FactRecord]:
        """对单个文档做基于 intent 的 LLM 定向抽取。"""
        preview = self._build_intent_preview(blocks, intent)
        year = infer_year(preview) or infer_year(document.file_name)
        return self._run_intent_llm(
            missing_fields=missing_fields,
            intent=intent,
            preview=preview,
            year=year,
            document=document,
            first_block_id=blocks[0].block_id if blocks else "",
        )

    def _intent_extract_concat(
        self,
        intent: "TemplateIntent",
        missing_fields: list,
        documents: list[DocumentRecord],
        blocks_by_doc: dict[str, list[DocumentBlock]],
    ) -> list[FactRecord]:
        """FC-2 拼接模式: 合并多文档上下文后一次 LLM 调用。"""
        all_text_parts: list[str] = []
        first_block_id = ""
        first_doc = documents[0] if documents else None
        for doc in documents[:5]:
            blocks = blocks_by_doc.get(doc.doc_id, [])
            if not blocks:
                continue
            if not first_block_id and blocks:
                first_block_id = blocks[0].block_id
            doc_preview = self._build_intent_preview(blocks, intent)
            all_text_parts.append(f"=== 文档: {doc.file_name} ===\n{doc_preview}")
        combined = "\n\n".join(all_text_parts)[:12000]
        year = None
        if first_doc:
            year = infer_year(combined) or infer_year(first_doc.file_name)
        return self._run_intent_llm(
            missing_fields=missing_fields,
            intent=intent,
            preview=combined,
            year=year,
            document=first_doc,
            first_block_id=first_block_id,
        )

    def _build_intent_preview(
        self,
        blocks: list[DocumentBlock],
        intent: "TemplateIntent",
    ) -> str:
        """Phase 2 Step 5: 针对 intent 的实体维度做智能分段预览，
        而非盲目截断前 N 字符。
        """
        entity_dim = (intent.entity_dimension or "").lower()
        # 对结构化块（表格行），优先包含
        table_blocks = [b for b in blocks if b.block_type == "table_row"]
        text_blocks = [b for b in blocks if b.block_type in {"paragraph", "heading", "page"}]

        parts: list[str] = []
        # 先放表头区域（含实体维度关键词的块优先）
        for b in table_blocks[:200]:
            parts.append(b.text[:500])
        # 再放文本块，优先包含实体维度关键词的段落
        if entity_dim:
            prioritized = sorted(
                text_blocks,
                key=lambda b: (entity_dim not in b.text.lower(), b.page_or_index or 0),
            )
        else:
            prioritized = text_blocks
        for b in prioritized[:30]:
            parts.append(b.text[:500])

        return "\n".join(parts)[:10000]

    def _run_intent_llm(
        self,
        *,
        missing_fields: list,
        intent: "TemplateIntent",
        preview: str,
        year: int | None,
        document: "DocumentRecord | None",
        first_block_id: str,
    ) -> list[FactRecord]:
        """发送带有完整 FieldRequirement 语义信息的 LLM 定向抽取请求。"""
        # 构建带语义描述的字段列表
        field_lines: list[str] = []
        for fr in missing_fields[:60]:
            line = f"  - {fr.name}"
            if fr.description:
                line += f"（含义: {fr.description}）"
            if fr.unit:
                line += f"（标准单位: {fr.unit}）"
            if fr.data_type:
                line += f"（类型: {fr.data_type}）"
            if fr.example_value:
                line += f"（示例: {fr.example_value}）"
            field_lines.append(line)
        fields_description = "\n".join(field_lines)

        entity_hint = ""
        if intent.entity_dimension:
            entity_hint = f"\n行维度: {intent.entity_dimension}（即每行对应一个{intent.entity_dimension}）"

        doc_name = document.file_name if document else "unknown"
        doc_id = document.doc_id if document else ""

        try:
            payload = self._openai_client.create_json_completion(
                system_prompt=(
                    "你是高精度结构化信息定向抽取引擎。\n"
                    "严格规则：\n"
                    "1. 从文本中精确查找用户指定的目标字段对应的数值。\n"
                    "2. value 必须填写文本中原始出现的数值（保留原文精度），不要换算单位。\n"
                    "3. unit 必须填写文本中该数值紧跟的原始单位。\n"
                    "4. entity_name 应使用文本中的实体简称（如城市名不带'市'后缀）。\n"
                    "5. field_name 必须严格使用下方目标字段列表中给出的名称，不要自创。\n"
                    "6. 注意每个字段旁标注的含义描述、标准单位和数据类型，它们帮你区分相似指标。\n"
                    "7. 只输出文本中明确给出的数据，绝不编造。为每个实体尽可能提取所有目标字段。\n"
                    "8. evidence 字段请填写包含该数据的原文片段（20~80字），用于溯源。\n"
                ),
                user_prompt=(
                    f"文档名: {doc_name}\n"
                    f"目标字段:\n{fields_description}"
                    f"{entity_hint}\n\n"
                    f"文本内容:\n{preview}"
                ),
                json_schema={
                    "type": "object",
                    "properties": {
                        "facts": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "entity_name": {"type": "string"},
                                    "field_name": {"type": "string"},
                                    "value": {"type": "string"},
                                    "unit": {"type": "string"},
                                    "evidence": {"type": "string"},
                                },
                                "required": ["entity_name", "field_name", "value"],
                            },
                        },
                    },
                    "required": ["facts"],
                    "additionalProperties": False,
                },
            )
        except Exception:
            self._logger.debug("Intent-driven LLM extraction failed", exc_info=True)
            return []

        raw_facts = payload.get("facts", [])
        if not isinstance(raw_facts, list):
            return []

        target_set_lower = {fr.name.lower() for fr in missing_fields}
        results: list[FactRecord] = []
        for item in raw_facts:
            if not isinstance(item, dict):
                continue
            entity = str(item.get("entity_name", "")).strip()
            field = str(item.get("field_name", "")).strip()
            raw_value = str(item.get("value", "")).strip()
            unit = str(item.get("unit", "")).strip() or None
            evidence = str(item.get("evidence", "")).strip()
            if not entity or not field or not raw_value:
                continue
            if field.lower() not in target_set_lower:
                continue

            value_num, detected_unit = extract_numeric_with_unit(raw_value)
            canonical_field = normalize_field_name(field)
            resolved_field = canonical_field or field
            if resolved_field.lower() not in target_set_lower:
                resolved_field = field
            final_unit = detected_unit or unit
            if value_num is not None and canonical_field:
                value_num, final_unit = convert_to_canonical_unit(canonical_field, value_num, final_unit)
            entity = normalize_entity_name(entity) or entity

            results.append(
                FactRecord(
                    fact_id=new_id("fact"),
                    entity_type=FIELD_ENTITY_TYPES.get(resolved_field, "generic"),
                    entity_name=entity,
                    field_name=resolved_field,
                    value_num=value_num,
                    value_text=raw_value,
                    unit=final_unit if value_num is not None else None,
                    year=year,
                    source_doc_id=doc_id,
                    source_block_id=first_block_id,
                    source_span=preview[:200],
                    confidence=0.92,
                    status="confirmed",
                    metadata={
                        "extraction_method": "llm_intent_driven",
                        "evidence_text": evidence[:200] if evidence else "",
                    },
                )
            )

        self._logger.info(
            "Intent-driven LLM extracted %d facts for %d missing fields",
            len(results),
            len(missing_fields),
        )
        return results

    @staticmethod
    def _enrich_evidence(facts: list[FactRecord], method: str) -> list[FactRecord]:
        """Step 6: 为已有事实补充 extraction_method metadata。"""
        for fact in facts:
            if not fact.metadata:
                fact.metadata = {}
            if "extraction_method" not in fact.metadata:
                fact.metadata["extraction_method"] = method
            if "evidence_text" not in fact.metadata:
                fact.metadata["evidence_text"] = fact.source_span[:200] if fact.source_span else ""
        return facts
