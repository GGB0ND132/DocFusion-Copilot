from __future__ import annotations

import re
from collections import OrderedDict
from dataclasses import replace

from app.core.catalog import FIELD_ALIASES, FIELD_ENTITY_TYPES
from app.models.domain import DocumentBlock, DocumentRecord, FactRecord
from app.utils.ids import new_id
from app.utils.normalizers import (
    convert_to_canonical_unit,
    extract_numeric_with_unit,
    find_entity_mentions,
    infer_year,
    is_entity_column,
    normalize_entity_name,
    normalize_field_name,
)

_BRACKET_UNIT_RE = re.compile(r"[（(](.*?)[)）]")
_GENERIC_NUMBER_RE = re.compile(
    r"(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>万份|份|万人|万余人|亿|万|人|亿元|万元|元|万例|例|%)?"
)
_DATE_RE = re.compile(r"(?P<date>(?:19|20)\d{2}[年/-]\d{1,2}[月/-]\d{1,2}日?)")
_TEXT_VALUE_RE = re.compile(r"(?:为|是|:|：)\s*(?P<value>[^，。；;\n]{2,40})")
_REGION_AT_START_RE = re.compile(
    r"^\s*(?P<name>(?:内蒙古自治区|广西壮族自治区|宁夏回族自治区|新疆维吾尔自治区|西藏自治区|"
    r"香港特别行政区|澳门特别行政区|[\u4e00-\u9fff]{2,12}(?:省|市|自治区|特别行政区|兵团)))"
)
_POPULATION_RE = re.compile(
    r"(?:常住)?人口(?:约|達|达到|为|约为|高达)?\s*(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>亿|万人|万余人|万|人)"
)
_PER_CAPITA_GDP_RE = re.compile(
    r"人均\s*GDP(?:约|達|达到|达|高达|为|约为)?\s*(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>万元|元|万)"
)
_DAILY_TEST_RE = re.compile(
    r"(?:当日)?(?:核酸)?检测量(?:约|達|达到|达|约为|提升至|升至)?\s*(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>万份|份|万)"
)
_CASE_SEGMENT_RE = re.compile(r"(?:新增|报告)\s*(?P<body>\d[^。；;\n]{0,80})")
_CASE_VALUE_RE = re.compile(r"(?P<value>\d+)\s*例")
_ZERO_CASE_RE = re.compile(r"(?:全零报告|零新增|无新增(?:确诊|病例|疫情)?|无疫情新增)")
_TEXT_COMPATIBLE_FIELDS = frozenset(
    {
        "GDP总量",
        "常住人口",
        "人均GDP",
        "一般公共预算收入",
        "合同金额",
        "签订日期",
        "甲方",
        "乙方",
        "每日检测数",
        "病例数",
        "大洲",
    }
)
_CONTINENT_HINTS: tuple[tuple[str, str], ...] = (
    ("亚洲", "亚洲"),
    ("asia", "亚洲"),
    ("欧洲", "欧洲"),
    ("europe", "欧洲"),
    ("北美洲", "北美洲"),
    ("north america", "北美洲"),
    ("南美洲", "南美洲"),
    ("south america", "南美洲"),
    ("非洲", "非洲"),
    ("africa", "非洲"),
    ("大洋洲", "大洋洲"),
    ("oceania", "大洋洲"),
)


class FactExtractionService:
    """从标准化文档块中抽取结构化事实。    Extract structured facts from normalized document blocks."""

    def extract(self, document: DocumentRecord, blocks: list[DocumentBlock]) -> list[FactRecord]:
        """执行块级抽取并返回去重后的事实列表。    Run block-level extraction and return deduplicated facts."""

        document_context = self._build_document_context(blocks)
        facts: list[FactRecord] = []
        for block in blocks:
            if block.block_type == "table_row":
                facts.extend(self._extract_from_table_row(document, block))
                continue
            facts.extend(self._extract_from_text(document, block, document_context))
        return list(self._deduplicate(facts).values())

    def _build_document_context(self, blocks: list[DocumentBlock]) -> dict[str, str]:
        """预先提取文档级上下文，例如大洲提示。    Precompute document-level context such as continent hints."""

        preview_text = "\n".join(block.text for block in blocks[:5] if block.text)
        lowered_preview = preview_text.lower()
        for raw_hint, canonical_hint in _CONTINENT_HINTS:
            if raw_hint in lowered_preview or raw_hint in preview_text:
                return {"continent": canonical_hint}
        return {}

    def _extract_from_table_row(self, document: DocumentRecord, block: DocumentBlock) -> list[FactRecord]:
        """从标准化表格行块中抽取事实。    Extract facts from a normalized table row block."""

        row_values = block.metadata.get("row_values")
        if not isinstance(row_values, dict):
            return []

        entity_name = self._resolve_table_row_entity_name(row_values, block.text)
        facts: list[FactRecord] = []
        year = infer_year(block.text) or infer_year(document.file_name)

        for header, raw_value in row_values.items():
            field_name = normalize_field_name(str(header))
            raw_text = str(raw_value).strip()
            if not field_name or not raw_text:
                continue

            entity = entity_name or self._fallback_entity_from_text(block.text)
            if not entity and FIELD_ENTITY_TYPES.get(field_name) in {"city", "region"}:
                continue

            header_unit = self._extract_unit_from_header(str(header))
            value_num, detected_unit = extract_numeric_with_unit(raw_text)
            final_num, final_unit = convert_to_canonical_unit(field_name, value_num, detected_unit or header_unit)
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
                )
            )
        return facts

    def _resolve_table_row_entity_name(self, row_values: dict[object, object], row_text: str) -> str:
        """为一行结构化表格数据挑选最适合作为实体主键的列值。    Pick the most suitable cell value in one structured row as the entity key."""

        entity_candidates: list[tuple[int, int, str]] = []
        for header, value in row_values.items():
            raw_header = str(header)
            raw_value = str(value).strip()
            if not raw_value:
                continue
            if is_entity_column(raw_header):
                entity_candidates.append(
                    (
                        self._score_entity_header(raw_header),
                        len(raw_value),
                        normalize_entity_name(raw_value),
                    )
                )
                continue
            if normalize_field_name(raw_header) is None:
                mentions = find_entity_mentions(raw_value)
                if mentions:
                    entity_candidates.append((1, len(mentions[0]), mentions[0]))

        if entity_candidates:
            entity_candidates.sort(reverse=True)
            return entity_candidates[0][2]

        mentions = find_entity_mentions(row_text)
        return mentions[0] if mentions else ""

    def _score_entity_header(self, header: str) -> int:
        """按表头语义为实体列候选打分。    Score entity-header candidates so specific object-name columns win over generic region columns."""

        normalized = header.strip().lower()
        if any(token in normalized for token in ("站点名称", "站点", "监测点", "监测站", "点位")):
            return 100
        if any(token in normalized for token in ("企业", "公司", "单位", "项目", "名称")):
            return 70
        if any(token in normalized for token in ("国家/地区", "国家地区", "省份", "地区")):
            return 60
        if any(token in normalized for token in ("区", "县", "区域")):
            return 40
        if any(token in normalized for token in ("城市", "地市")):
            return 20
        return 10

    def _extract_from_text(
        self,
        document: DocumentRecord,
        block: DocumentBlock,
        document_context: dict[str, str],
    ) -> list[FactRecord]:
        """从自由文本段落或标题中抽取事实。    Extract facts from free-form paragraph or heading text."""

        content = block.text
        if not content:
            return []

        facts: list[FactRecord] = []
        facts.extend(self._extract_epidemic_summary_facts(document, block, document_context))

        entity_positions = self._find_entity_positions(content, block.section_path)
        primary_entity = self._resolve_primary_entity(content, entity_positions)
        year = infer_year(content) or infer_year(document.file_name)
        occupied_spans: list[tuple[int, int]] = []

        alias_pairs = [
            (canonical_name, alias)
            for canonical_name, aliases in FIELD_ALIASES.items()
            if canonical_name in _TEXT_COMPATIBLE_FIELDS
            for alias in {canonical_name, *aliases}
        ]
        alias_pairs.sort(key=lambda item: len(item[1]), reverse=True)

        for canonical_name, alias in alias_pairs:
            for match in re.finditer(re.escape(alias), content, flags=re.IGNORECASE):
                if self._is_overlapping(match.start(), match.end(), occupied_spans):
                    continue
                if self._should_skip_alias_match(content, canonical_name, alias, match):
                    continue

                fact = self._build_text_fact(
                    document=document,
                    block=block,
                    content=content,
                    canonical_name=canonical_name,
                    entity_positions=entity_positions,
                    primary_entity=primary_entity,
                    year=year,
                    match=match,
                )
                if fact is None:
                    continue
                facts.append(fact)
                occupied_spans.append((match.start(), match.end()))
        return facts

    def _extract_epidemic_summary_facts(
        self,
        document: DocumentRecord,
        block: DocumentBlock,
        document_context: dict[str, str],
    ) -> list[FactRecord]:
        """从疫情综述段落中抽取人口、人均 GDP、检测数、病例数和大洲。    Extract population, per-capita GDP, test counts, case counts and continent from epidemic summary paragraphs."""

        content = block.text.strip()
        if not content:
            return []

        entity_name = self._extract_region_at_text_start(content)
        if not entity_name:
            return []

        facts: list[FactRecord] = []
        year = infer_year(content) or infer_year(document.file_name)

        population_match = _POPULATION_RE.search(content)
        if population_match:
            facts.append(
                self._build_numeric_fact(
                    document=document,
                    block=block,
                    entity_name=entity_name,
                    field_name="常住人口",
                    year=year,
                    raw_value=population_match.group("value"),
                    raw_unit=population_match.group("unit"),
                    confidence=0.96,
                )
            )

        per_capita_match = _PER_CAPITA_GDP_RE.search(content)
        if per_capita_match:
            facts.append(
                self._build_numeric_fact(
                    document=document,
                    block=block,
                    entity_name=entity_name,
                    field_name="人均GDP",
                    year=year,
                    raw_value=per_capita_match.group("value"),
                    raw_unit=per_capita_match.group("unit"),
                    confidence=0.96,
                )
            )

        daily_test_match = _DAILY_TEST_RE.search(content)
        if daily_test_match:
            facts.append(
                self._build_numeric_fact(
                    document=document,
                    block=block,
                    entity_name=entity_name,
                    field_name="每日检测数",
                    year=year,
                    raw_value=daily_test_match.group("value"),
                    raw_unit=daily_test_match.group("unit"),
                    confidence=0.95,
                )
            )

        case_count = self._extract_case_count(content)
        if case_count is not None:
            facts.append(
                FactRecord(
                    fact_id=new_id("fact"),
                    entity_type=FIELD_ENTITY_TYPES.get("病例数", "generic"),
                    entity_name=entity_name,
                    field_name="病例数",
                    value_num=case_count,
                    value_text=content,
                    unit="例",
                    year=year,
                    source_doc_id=document.doc_id,
                    source_block_id=block.block_id,
                    source_span=content,
                    confidence=0.95,
                    status="confirmed",
                )
            )

        continent = document_context.get("continent")
        if continent and facts:
            facts.append(
                FactRecord(
                    fact_id=new_id("fact"),
                    entity_type=FIELD_ENTITY_TYPES.get("大洲", "generic"),
                    entity_name=entity_name,
                    field_name="大洲",
                    value_num=None,
                    value_text=continent,
                    unit=None,
                    year=year,
                    source_doc_id=document.doc_id,
                    source_block_id=block.block_id,
                    source_span=content,
                    confidence=0.9,
                    status="confirmed",
                )
            )
        return facts

    def _build_numeric_fact(
        self,
        *,
        document: DocumentRecord,
        block: DocumentBlock,
        entity_name: str,
        field_name: str,
        year: int | None,
        raw_value: str,
        raw_unit: str | None,
        confidence: float,
    ) -> FactRecord:
        """构造一条数值型事实。    Build one numeric fact record."""

        value_num = float(raw_value.replace(",", ""))
        final_num, final_unit = convert_to_canonical_unit(field_name, value_num, raw_unit)
        return FactRecord(
            fact_id=new_id("fact"),
            entity_type=FIELD_ENTITY_TYPES.get(field_name, "generic"),
            entity_name=entity_name,
            field_name=field_name,
            value_num=final_num,
            value_text=f"{raw_value}{raw_unit or ''}",
            unit=final_unit,
            year=year,
            source_doc_id=document.doc_id,
            source_block_id=block.block_id,
            source_span=block.text,
            confidence=confidence,
            status="confirmed",
        )

    def _extract_region_at_text_start(self, content: str) -> str:
        """识别段首主语地区名称。    Identify the leading region name at the start of a paragraph."""

        match = _REGION_AT_START_RE.search(content)
        if not match:
            return ""
        return normalize_entity_name(match.group("name"))

    def _extract_case_count(self, content: str) -> float | None:
        """从疫情描述中抽取病例数；若存在明确“零报告”表述则返回 0。    Extract case counts from epidemic text and return 0 for explicit zero-report expressions."""

        case_total = 0
        found_numeric_cases = False
        for segment in _CASE_SEGMENT_RE.finditer(content):
            values = [int(match.group("value")) for match in _CASE_VALUE_RE.finditer(segment.group("body"))]
            if not values:
                continue
            case_total += sum(values)
            found_numeric_cases = True
        if found_numeric_cases:
            return float(case_total)
        if _ZERO_CASE_RE.search(content):
            return 0.0
        return None

    def _build_text_fact(
        self,
        *,
        document: DocumentRecord,
        block: DocumentBlock,
        content: str,
        canonical_name: str,
        entity_positions: list[tuple[int, str]],
        primary_entity: str | None,
        year: int | None,
        match: re.Match[str],
    ) -> FactRecord | None:
        """根据别名命中结果构建一条文本事实。    Build one text fact from a matched alias occurrence."""

        entity_name = primary_entity or self._resolve_nearest_entity(entity_positions, match.start())
        if not entity_name and FIELD_ENTITY_TYPES.get(canonical_name) in {"city", "region"}:
            return None

        value_num: float | None = None
        value_text = ""
        unit: str | None = None

        if canonical_name in {"甲方", "乙方", "大洲"}:
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

        confidence = 0.9 if primary_entity else 0.82 if entity_name else 0.72
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

    def _should_skip_alias_match(
        self,
        content: str,
        canonical_name: str,
        alias: str,
        match: re.Match[str],
    ) -> bool:
        """过滤明显会被误解的别名命中，例如将“人均 GDP”误判为“GDP总量”。    Filter obviously misleading alias matches, such as interpreting per-capita GDP as total GDP."""

        if canonical_name != "GDP总量":
            return False
        if alias.lower() != "gdp":
            return False
        prefix = content[max(0, match.start() - 4) : match.start()].replace(" ", "")
        return "人均" in prefix or prefix.endswith("均")

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

    def _resolve_primary_entity(self, content: str, positions: list[tuple[int, str]]) -> str | None:
        """优先返回段落开头的主题实体。    Prefer the leading subject entity of a paragraph."""

        leading_entity = self._extract_region_at_text_start(content)
        if leading_entity:
            return leading_entity
        if not positions:
            return None
        first_position, first_entity = positions[0]
        if first_position <= 8:
            return first_entity
        return None

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

        window = text[anchor_end : min(len(text), anchor_end + 48)]
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
