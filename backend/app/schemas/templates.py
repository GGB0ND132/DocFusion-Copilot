from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.common import APIModel


# ---------------------------------------------------------------------------
# API response schemas
# ---------------------------------------------------------------------------


class TemplateFillAcceptedResponse(APIModel):
    """模板回填任务入队后的响应结构。
    Response returned after a template fill task is queued.
    """

    task_id: str
    status: str
    template_name: str
    document_set_id: str | None = None
    auto_match: bool = True


# ---------------------------------------------------------------------------
# Template intent analysis schemas  (Phase 1 – 模板意图分析)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class FieldRequirement:
    """模板中单个待填字段的语义需求描述。
    Semantic requirement for one field that needs to be filled in the template.
    """

    name: str
    """字段在模板表头中的原样名称（或归一化后的标准名）。"""
    description: str = ""
    """LLM 对该字段含义的简短描述。"""
    data_type: str = "number"
    """期望数据类型: number | text | percentage | date"""
    unit: str = ""
    """期望单位，如 '亿元', '万人', '%'"""
    example_value: str = ""
    """LLM 从模板样例行中识别到的示例值。"""
    is_computed: bool = False
    """是否为计算字段（如增长率=本年值/上年值-1）。"""
    computation_hint: str = ""
    """计算规则描述（仅 is_computed=True 时有意义）。"""


@dataclass(slots=True)
class TemplateIntent:
    """LLM 对一个模板的深度理解结果。
    Deep understanding of a template produced by LLM analysis.

    设计目标：不依赖静态 catalog，完全由 LLM 从模板结构中获得。
    """

    required_fields: list[FieldRequirement] = field(default_factory=list)
    """该模板需要填充的所有字段及其语义需求。"""
    entity_dimension: str = ""
    """行维度: '城市' / '国家' / '年份' / '产品' / '省份' 等。"""
    data_granularity: str = ""
    """时间粒度: '年度' / '月度' / '日度' / '无' 等。"""
    aggregation_hints: list[str] = field(default_factory=list)
    """汇总提示，如 ['按城市汇总', '取最新年份'] 等。"""
    relationship_hints: list[str] = field(default_factory=list)
    """字段间计算关系，如 ['增长率 = 本年值/上年值 - 1'] 等。"""
    raw_headers: list[str] = field(default_factory=list)
    """模板原始表头列表，用于回填阶段列映射。"""
    template_description: str = ""
    """LLM 对整个模板用途的一句话概括。"""
    date_filter: tuple[str | None, str | None] = (None, None)
    """从 user_requirement 中解析出的日期范围 (date_from, date_to)，ISO 格式。"""
    entity_filter: list[str] = field(default_factory=list)
    """从 user_requirement 中解析出的目标实体列表，如 ['济南', '青岛']。"""



