from __future__ import annotations

import re
from collections.abc import Iterable

from app.core.catalog import CITY_NAMES, ENTITY_COLUMN_ALIASES, DATE_COLUMN_ALIASES, FIELD_ALIASES, FIELD_CANONICAL_UNITS

_BRACKET_TEXT_RE = re.compile(r"[（(].*?[)）]")
_WHITESPACE_RE = re.compile(r"\s+")
_NUMERIC_RE = re.compile(r"(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>万亿元|亿元|万元|元|万人|人|%)?")
_YEAR_RE = re.compile(r"(?P<year>(?:19|20)\d{2})年")
_CITY_WITH_SUFFIX_RE = re.compile(r"(?P<name>[\u4e00-\u9fff]{2,4})市")

_FIELD_ALIAS_LOOKUP: dict[str, str] = {}
for canonical_name, aliases in FIELD_ALIASES.items():
    normalized_values = {canonical_name, *aliases}
    for alias in normalized_values:
        stripped = _WHITESPACE_RE.sub("", _BRACKET_TEXT_RE.sub("", alias)).lower()
        _FIELD_ALIAS_LOOKUP[stripped] = canonical_name

_ENTITY_COLUMN_LOOKUP = {
    _WHITESPACE_RE.sub("", _BRACKET_TEXT_RE.sub("", name)).lower()
    for name in ENTITY_COLUMN_ALIASES
}


def strip_header_adornments(raw_value: str) -> str:
    """移除表头字符串中的空白和括号提示。    Remove whitespace and bracketed hints from a header string."""

    return _WHITESPACE_RE.sub("", _BRACKET_TEXT_RE.sub("", raw_value or "")).strip()


def normalize_field_name(raw_value: str) -> str | None:
    """将原始表头或别名映射为标准字段名。    Map a raw header or alias string to its canonical field name."""

    if not raw_value:
        return None
    candidate = strip_header_adornments(raw_value).lower()
    return _FIELD_ALIAS_LOOKUP.get(candidate)


def normalize_field_name_or_passthrough(raw_value: str) -> str | None:
    """先尝试标准字段映射，失败时返回清洗后的原始表头名。
    Try canonical mapping first, then fall back to stripped raw header name."""
    canonical = normalize_field_name(raw_value)
    if canonical is not None:
        return canonical
    stripped = strip_header_adornments(raw_value)
    if not stripped or stripped.isdigit() or len(stripped) > 40:
        return None
    return stripped


def is_entity_column(raw_value: str) -> bool:
    """判断某个表头是否表示实体列。    Return whether a header is likely describing the entity column."""

    return strip_header_adornments(raw_value).lower() in _ENTITY_COLUMN_LOOKUP


def normalize_entity_name(raw_value: str) -> str:
    """标准化实体名称，便于跨文档匹配。    Normalize entity text for cross-document matching."""

    candidate = re.sub(r"[\s:：\-_/]+", "", raw_value or "")
    if candidate.endswith("市") and len(candidate) > 2:
        candidate = candidate[:-1]
    if candidate.endswith("省") and len(candidate) > 2:
        candidate = candidate[:-1]
    return candidate


def find_entity_mentions(text: str, extra_candidates: Iterable[str] | None = None) -> list[str]:
    """返回文本片段中的唯一实体提及。    Return unique entity mentions detected in a text snippet."""

    candidates: list[str] = []
    seen: set[str] = set()

    def _push(name: str) -> None:
        """按出现顺序追加一个规范化实体。    Append one normalized entity while preserving order."""

        normalized = normalize_entity_name(name)
        if normalized and normalized not in seen:
            seen.add(normalized)
            candidates.append(normalized)

    for city_name in CITY_NAMES:
        if city_name in text or f"{city_name}市" in text or f"{city_name}省" in text:
            _push(city_name)

    for match in _CITY_WITH_SUFFIX_RE.finditer(text):
        _push(match.group("name"))

    if extra_candidates:
        for candidate in extra_candidates:
            normalized = normalize_entity_name(candidate)
            if normalized and (normalized in text or f"{normalized}市" in text):
                _push(normalized)

    return candidates


def infer_year(text: str) -> int | None:
    """推断文本中首次出现的年份。    Infer the first four-digit year mentioned in text."""

    match = _YEAR_RE.search(text)
    if not match:
        return None
    return int(match.group("year"))


def extract_numeric_with_unit(raw_value: str) -> tuple[float | None, str | None]:
    """从文本中提取数值和单位。    Extract one numeric value and optional unit from text."""

    if not raw_value:
        return None, None
    match = _NUMERIC_RE.search(raw_value.replace("，", ","))
    if not match:
        return None, None
    number = float(match.group("value").replace(",", ""))
    unit = match.group("unit")
    return number, unit


def convert_to_canonical_unit(
    field_name: str,
    value_num: float | None,
    unit: str | None,
) -> tuple[float | None, str | None]:
    """将数值转换为字段标准单位。    Convert a value into the canonical unit configured for a field."""

    if value_num is None:
        return None, FIELD_CANONICAL_UNITS.get(field_name, unit)

    canonical_unit = FIELD_CANONICAL_UNITS.get(field_name, unit)
    if not unit or not canonical_unit or unit == canonical_unit:
        return value_num, canonical_unit

    if field_name in {"GDP总量", "一般公共预算收入"}:
        if unit == "万亿元":
            return value_num * 10000, "亿元"
        if unit == "万元":
            return value_num / 10000, "亿元"
        if unit == "元":
            return value_num / 100000000, "亿元"
        if unit == "亿元":
            return value_num, "亿元"

    if field_name == "常住人口":
        if unit == "人":
            return value_num / 10000, "万人"
        if unit == "万人":
            return value_num, "万人"

    if field_name in {"人均GDP", "合同金额"}:
        if unit == "万元":
            return value_num * 10000, "元"
        if unit == "亿元":
            return value_num * 100000000, "元"
        if unit == "元":
            return value_num, "元"

    return value_num, canonical_unit


def format_value(value: float | None) -> str:
    """将数值格式化为紧凑字符串。    Format a numeric value into a compact human-readable string."""

    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")


# ── Date column / Excel serial utilities ──

_DATE_COLUMN_LOOKUP = {
    _WHITESPACE_RE.sub("", _BRACKET_TEXT_RE.sub("", name)).lower()
    for name in DATE_COLUMN_ALIASES
}

_EXCEL_EPOCH = 25569  # days between 1899-12-30 and 1970-01-01
_ISO_DATE_RE = re.compile(r"(?P<y>(?:19|20)\d{2})[/\-](?P<m>\d{1,2})[/\-](?P<d>\d{1,2})")


def is_date_column(raw_value: str) -> bool:
    """判断某个表头是否表示日期/时间列。"""
    return strip_header_adornments(raw_value).lower() in _DATE_COLUMN_LOOKUP


def excel_serial_to_iso(serial: float | int) -> str | None:
    """将 Excel 日期序列号转换为 ISO 日期字符串 YYYY-MM-DD，返回 None 表示无效。"""
    from datetime import datetime, timedelta
    try:
        serial_f = float(serial)
        if serial_f < 1 or serial_f > 200000:
            return None
        dt = datetime(1899, 12, 30) + timedelta(days=serial_f)
        return dt.strftime("%Y-%m-%d")
    except (ValueError, OverflowError):
        return None


def parse_date_value(raw: str) -> str | None:
    """尝试将原始值解析为 ISO 日期字符串。支持 Excel 序列号和多种字符串格式。"""
    stripped = raw.strip()
    if not stripped:
        return None
    # Try ISO-like date string first
    m = _ISO_DATE_RE.search(stripped)
    if m:
        return f"{m.group('y')}-{int(m.group('m')):02d}-{int(m.group('d')):02d}"
    # Try full datetime with time component (e.g., "2025-11-25 09:00:00.0")
    if re.match(r"\d{4}-\d{1,2}-\d{1,2}\s+\d", stripped):
        m2 = _ISO_DATE_RE.search(stripped)
        if m2:
            return f"{m2.group('y')}-{int(m2.group('m')):02d}-{int(m2.group('d')):02d}"
    # Try Excel serial number
    try:
        serial = float(stripped.replace(",", ""))
        return excel_serial_to_iso(serial)
    except ValueError:
        return None


def parse_date_range_from_text(text: str) -> tuple[str | None, str | None]:
    """从用户需求文本中提取日期范围 (date_from, date_to)，返回 ISO 字符串。"""
    dates: list[str] = []
    for m in _ISO_DATE_RE.finditer(text):
        iso = f"{m.group('y')}-{int(m.group('m')):02d}-{int(m.group('d')):02d}"
        dates.append(iso)
    if len(dates) >= 2:
        dates.sort()
        return dates[0], dates[-1]
    if len(dates) == 1:
        return dates[0], dates[0]
    return None, None
