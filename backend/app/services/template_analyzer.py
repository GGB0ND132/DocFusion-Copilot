"""模板意图分析器  —  Phase 1: Template Intent Analysis.

用 LLM 深度理解模板结构，输出结构化的 ``TemplateIntent``，
驱动后续的针对性抽取 (Phase 2) 与智能回填 (Phase 3)。

FC-1: 分析结果按 (模板文件内容 hash) 缓存，同一模板不重复分析。
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.schemas.templates import FieldRequirement, TemplateIntent

if TYPE_CHECKING:
    from app.core.openai_client import OpenAICompatibleClient

logger = logging.getLogger("docfusion.template_analyzer")

# ---------------------------------------------------------------------------
# FC-1: In-memory LRU cache keyed by template content hash
# ---------------------------------------------------------------------------
_INTENT_CACHE: dict[str, TemplateIntent] = {}
_INTENT_CACHE_MAX = 64


def _content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cache_get(key: str) -> TemplateIntent | None:
    return _INTENT_CACHE.get(key)


def _cache_put(key: str, intent: TemplateIntent) -> None:
    if len(_INTENT_CACHE) >= _INTENT_CACHE_MAX:
        # evict oldest
        oldest = next(iter(_INTENT_CACHE))
        _INTENT_CACHE.pop(oldest, None)
    _INTENT_CACHE[key] = intent


# ---------------------------------------------------------------------------
# Template structure extraction (rule-based, no LLM)
# ---------------------------------------------------------------------------

def _read_template_structure(template_path: Path) -> dict:
    """读取模板的完整结构：表头、样例行、合并单元格信息等。
    Returns a dict with 'headers', 'sample_rows', 'merged_cells', 'paragraphs'.
    """
    suffix = template_path.suffix.lower()
    result: dict = {
        "headers": [],
        "sample_rows": [],
        "merged_cells": [],
        "paragraphs": [],
        "format": suffix.lstrip("."),
    }

    if suffix == ".xlsx":
        from app.utils.spreadsheet import load_xlsx
        wb = load_xlsx(template_path)
        all_headers: list[str] = []
        seen_headers: set[str] = set()
        for sheet in wb.sheets:
            if not sheet.rows:
                continue
            header_row = sheet.rows[0]
            headers = [v.strip() for v in header_row.values if v and v.strip()]
            for h in headers:
                if h not in seen_headers:
                    seen_headers.add(h)
                    all_headers.append(h)
            for row in sheet.rows[1:6]:  # up to 5 sample rows per sheet
                row_data = {}
                for i, val in enumerate(row.values):
                    if i < len(headers) and headers[i]:
                        row_data[headers[i]] = val
                if any(v for v in row_data.values()):
                    result["sample_rows"].append(row_data)
        result["headers"] = all_headers

    elif suffix == ".docx":
        from app.utils.wordprocessing import load_docx_tables
        doc = load_docx_tables(template_path)
        all_headers_docx: list[str] = []
        seen_headers_docx: set[str] = set()
        for table in doc.tables:
            if not table.rows:
                continue
            header_row = table.rows[0]
            headers = [v.strip() for v in header_row.values if v and v.strip()]
            if headers:
                for h in headers:
                    if h not in seen_headers_docx:
                        seen_headers_docx.add(h)
                        all_headers_docx.append(h)
                for row in table.rows[1:6]:
                    row_data = {}
                    for i, val in enumerate(row.values):
                        if i < len(headers) and headers[i]:
                            row_data[headers[i]] = str(val or "").strip()
                    if any(v for v in row_data.values()):
                        result["sample_rows"].append(row_data)
        result["headers"] = all_headers_docx
    return result


# ---------------------------------------------------------------------------
# LLM-based intent analysis
# ---------------------------------------------------------------------------

_ANALYSIS_SYSTEM_PROMPT = """\
你是高精度模板分析引擎。用户给你一个待回填模板的结构信息，你需要深度理解模板意图。

## 输出 JSON 规则
严格按以下 schema 输出，不要添加额外字段。
{
  "template_description": "对模板整体用途的一句话概括",
  "entity_dimension": "行维度，如 '城市'/'国家'/'年份'/'产品'/'省份'/'日期' 等",
  "data_granularity": "时间粒度: '年度'/'月度'/'日度'/'无'",
  "aggregation_hints": ["汇总提示列表"],
  "relationship_hints": ["字段间计算关系，如 '增长率 = 本年值/上年值 - 1'"],
  "required_fields": [
    {
      "name": "原始表头名",
      "description": "该字段含义的简短描述",
      "data_type": "number|text|percentage|date",
      "unit": "期望单位如 '亿元'/'万人'/'%'，无则空字符串",
      "example_value": "从样例行识别到的示例值，无则空字符串",
      "is_computed": false,
      "computation_hint": ""
    }
  ]
}

## 注意事项
1. required_fields 应覆盖模板中**所有待填列**（即非序号、非固定标题的数据列）。
2. 对于看起来是计算得出的字段（如增长率、占比），设 is_computed=true 并给出 computation_hint。
3. entity_dimension 应识别模板行的主维度（第一列通常是实体列）。
4. 如果模板有日期列或时间列，data_granularity 应反映其粒度。
5. 只根据模板结构推断，不要编造数据。
"""

_ANALYSIS_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "template_description": {"type": "string"},
        "entity_dimension": {"type": "string"},
        "data_granularity": {"type": "string"},
        "aggregation_hints": {"type": "array", "items": {"type": "string"}},
        "relationship_hints": {"type": "array", "items": {"type": "string"}},
        "required_fields": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "description": {"type": "string"},
                    "data_type": {"type": "string"},
                    "unit": {"type": "string"},
                    "example_value": {"type": "string"},
                    "is_computed": {"type": "boolean"},
                    "computation_hint": {"type": "string"},
                },
                "required": ["name", "description", "data_type"],
            },
        },
    },
    "required": [
        "template_description",
        "entity_dimension",
        "data_granularity",
        "required_fields",
    ],
    "additionalProperties": False,
}


def _build_user_prompt(
    structure: dict,
    user_requirement: str,
) -> str:
    """组装发给 LLM 的用户 prompt。"""
    parts: list[str] = []
    parts.append(f"## 模板格式: {structure['format']}")
    parts.append(f"## 表头列名\n{structure['headers']}")
    if structure["sample_rows"]:
        parts.append(f"## 样例行（前几行已有数据）\n{json.dumps(structure['sample_rows'], ensure_ascii=False, indent=2)}")
    else:
        parts.append("## 样例行\n（模板为空白，无已有数据）")
    if user_requirement:
        parts.append(f"## 用户需求\n{user_requirement}")
    return "\n\n".join(parts)


def _parse_llm_response(payload: dict) -> TemplateIntent:
    """将 LLM JSON 响应解析为 TemplateIntent dataclass。"""
    fields: list[FieldRequirement] = []
    for f in payload.get("required_fields", []):
        fields.append(FieldRequirement(
            name=f.get("name", ""),
            description=f.get("description", ""),
            data_type=f.get("data_type", "number"),
            unit=f.get("unit", ""),
            example_value=f.get("example_value", ""),
            is_computed=f.get("is_computed", False),
            computation_hint=f.get("computation_hint", ""),
        ))
    return TemplateIntent(
        required_fields=fields,
        entity_dimension=payload.get("entity_dimension", ""),
        data_granularity=payload.get("data_granularity", ""),
        aggregation_hints=payload.get("aggregation_hints", []),
        relationship_hints=payload.get("relationship_hints", []),
        raw_headers=payload.get("_raw_headers", []),
        template_description=payload.get("template_description", ""),
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def analyze_template(
    template_path: Path,
    openai_client: "OpenAICompatibleClient",
    *,
    user_requirement: str = "",
    bypass_cache: bool = False,
) -> TemplateIntent:
    """分析模板结构并返回 TemplateIntent。

    FC-1: 结果按模板文件内容 hash 缓存；同模板+不同 user_requirement
    会产生不同缓存条目（requirement 参与 hash）。
    """
    # --- FC-1: cache lookup ---
    raw_bytes = template_path.read_bytes()
    cache_key = _content_hash(raw_bytes + user_requirement.encode("utf-8"))
    if not bypass_cache:
        cached = _cache_get(cache_key)
        if cached is not None:
            logger.info("Template intent cache hit: %s", cache_key[:12])
            return cached

    # --- Read template structure ---
    structure = _read_template_structure(template_path)
    if not structure["headers"]:
        logger.warning("No headers found in template: %s", template_path.name)
        return TemplateIntent(raw_headers=[])

    # --- LLM analysis ---
    if not openai_client.is_configured:
        logger.warning("OpenAI client not configured; returning rule-only TemplateIntent")
        fallback = _fallback_rule_only(structure)
        _enrich_intent_with_constraints(fallback, user_requirement)
        return fallback

    user_prompt = _build_user_prompt(structure, user_requirement)

    try:
        payload = openai_client.create_json_completion(
            system_prompt=_ANALYSIS_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            json_schema=_ANALYSIS_JSON_SCHEMA,
        )
    except Exception:
        logger.warning("LLM template analysis failed, falling back to rules", exc_info=True)
        fallback = _fallback_rule_only(structure)
        _enrich_intent_with_constraints(fallback, user_requirement)
        return fallback

    intent = _parse_llm_response(payload)
    intent.raw_headers = structure["headers"]

    # --- 从 user_requirement 解析约束 ---
    _enrich_intent_with_constraints(intent, user_requirement)

    # --- FC-1: cache store ---
    _cache_put(cache_key, intent)
    logger.info(
        "Template analyzed: %d fields, entity_dim=%s, granularity=%s, date_filter=%s, entity_filter=%s",
        len(intent.required_fields),
        intent.entity_dimension,
        intent.data_granularity,
        intent.date_filter,
        intent.entity_filter,
    )
    return intent


def _enrich_intent_with_constraints(intent: TemplateIntent, user_requirement: str) -> None:
    """从 user_requirement 文本中解析日期/实体约束并填充到 intent 上。"""
    if not user_requirement:
        return
    from app.utils.normalizers import (
        parse_date_range_from_text,
        parse_entity_filter_from_text,
        parse_year_filter_from_text,
    )
    # 日期范围 (ISO)
    date_from, date_to = parse_date_range_from_text(user_requirement)
    if date_from or date_to:
        intent.date_filter = (date_from, date_to)
    else:
        # 回退到年份解析 — 转为 ISO 首日/末日
        y_from, y_to = parse_year_filter_from_text(user_requirement)
        if y_from is not None:
            intent.date_filter = (f"{y_from}-01-01", f"{y_to}-12-31" if y_to else f"{y_from}-12-31")
    # 实体列表
    entities = parse_entity_filter_from_text(user_requirement)
    if entities:
        intent.entity_filter = entities


def _fallback_rule_only(structure: dict) -> TemplateIntent:
    """LLM 不可用时的纯规则回退：每个表头列作为一个 FieldRequirement。"""
    from app.utils.normalizers import normalize_field_name_or_passthrough
    fields: list[FieldRequirement] = []
    for header in structure.get("headers", []):
        canonical = normalize_field_name_or_passthrough(header)
        if canonical:
            fields.append(FieldRequirement(name=canonical, description=header))
    return TemplateIntent(
        required_fields=fields,
        raw_headers=structure.get("headers", []),
        template_description="(rule-only fallback)",
    )
