from __future__ import annotations

import re
from collections.abc import Iterable

from app.core.catalog import CITY_NAMES, ENTITY_COLUMN_ALIASES, FIELD_ALIASES, FIELD_CANONICAL_UNITS

_BRACKET_TEXT_RE = re.compile(r"[（(].*?[)）]")
_WHITESPACE_RE = re.compile(r"\s+")
_NUMERIC_RE = re.compile(
    r"(?P<value>-?\d[\d,]*(?:\.\d+)?)\s*(?P<unit>万份|份|万人|万余人|亿|万|人|亿元|万元|元|万例|例|%)?"
)
_YEAR_RE = re.compile(r"(?P<year>(?:19|20)\d{2})年")
_CITY_WITH_SUFFIX_RE = re.compile(r"(?P<name>[\u4e00-\u9fff]{2,8})市")
_REGION_WITH_SUFFIX_RE = re.compile(
    r"(?P<name>(?:内蒙古|广西壮族|宁夏回族|新疆维吾尔|西藏|香港|澳门|[\u4e00-\u9fff]{2,12}))(?:自治区|特别行政区|省|市|兵团|地区|盟)"
)

_SPECIAL_REGION_NAMES: dict[str, str] = {
    "广西壮族自治区": "广西",
    "宁夏回族自治区": "宁夏",
    "新疆维吾尔自治区": "新疆",
    "西藏自治区": "西藏",
    "内蒙古自治区": "内蒙古",
    "香港特别行政区": "香港",
    "澳门特别行政区": "澳门",
}

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


def is_entity_column(raw_value: str) -> bool:
    """判断某个表头是否表示实体列。    Return whether a header is likely describing the entity column."""

    return strip_header_adornments(raw_value).lower() in _ENTITY_COLUMN_LOOKUP


def normalize_entity_name(raw_value: str) -> str:
    """标准化实体名称，便于跨文档匹配。    Normalize entity text for cross-document matching."""

    candidate = re.sub(r"[\s:：,，、;；\-_/]+", "", raw_value or "")
    if not candidate:
        return ""
    if candidate in _SPECIAL_REGION_NAMES:
        return _SPECIAL_REGION_NAMES[candidate]
    for raw_name, normalized_name in _SPECIAL_REGION_NAMES.items():
        if candidate.endswith(raw_name):
            return normalized_name
    for suffix in ("特别行政区", "自治区", "兵团", "地区", "自治州", "盟"):
        if candidate.endswith(suffix) and len(candidate) > len(suffix):
            return candidate[: -len(suffix)]
    if candidate.endswith(("省", "市")) and len(candidate) > 2:
        return candidate[:-1]
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

    for entity_name in CITY_NAMES:
        if entity_name in text:
            _push(entity_name)

    for match in _REGION_WITH_SUFFIX_RE.finditer(text):
        _push(match.group("name"))

    for match in _CITY_WITH_SUFFIX_RE.finditer(text):
        _push(match.group("name"))

    if extra_candidates:
        for candidate in extra_candidates:
            normalized = normalize_entity_name(candidate)
            if normalized and normalized in text:
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
    match = _NUMERIC_RE.search(raw_value.replace("，", "").replace(",", ""))
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
    normalized_unit = (unit or "").strip()
    if not normalized_unit or not canonical_unit or normalized_unit == canonical_unit:
        return value_num, canonical_unit

    if field_name in {"GDP总量", "一般公共预算收入"}:
        if normalized_unit == "万亿元":
            return value_num * 10000, "亿元"
        if normalized_unit == "万元":
            return value_num / 10000, "亿元"
        if normalized_unit == "元":
            return value_num / 100000000, "亿元"
        if normalized_unit == "亿元":
            return value_num, "亿元"

    if field_name == "常住人口":
        if normalized_unit == "亿":
            return value_num * 10000, "万人"
        if normalized_unit in {"万", "万人", "万余人"}:
            return value_num, "万人"
        if normalized_unit == "人":
            return value_num / 10000, "万人"

    if field_name in {"人均GDP", "合同金额"}:
        if normalized_unit in {"万", "万元"}:
            return value_num * 10000, "元"
        if normalized_unit == "亿元":
            return value_num * 100000000, "元"
        if normalized_unit == "元":
            return value_num, "元"

    if field_name == "每日检测数":
        if normalized_unit in {"万", "万份"}:
            return value_num, "万份"
        if normalized_unit == "份":
            return value_num / 10000, "万份"

    if field_name == "病例数":
        if normalized_unit in {"万例", "万"}:
            return value_num * 10000, "例"
        if normalized_unit in {"例", ""}:
            return value_num, "例"

    return value_num, canonical_unit


def format_value(value: float | None) -> str:
    """将数值格式化为紧凑字符串。    Format a numeric value into a compact human-readable string."""

    if value is None:
        return ""
    if float(value).is_integer():
        return str(int(value))
    return f"{value:.4f}".rstrip("0").rstrip(".")
