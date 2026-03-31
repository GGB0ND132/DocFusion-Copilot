from __future__ import annotations

from pydantic import BaseModel, Field


class FieldMapping(BaseModel):
    """Maps a template field name to the closest source data field name."""
    template_field: str = Field(description="Field name from the template header")
    source_field: str = Field(description="Best matching field name from source data")
    confidence: float = Field(ge=0.0, le=1.0, description="Confidence of the mapping")


class FieldMappingList(BaseModel):
    """List of field mappings between template and source data."""
    mappings: list[FieldMapping] = Field(default_factory=list)


class DateRange(BaseModel):
    """Extracted date range from user requirements."""
    date_from: str | None = Field(default=None, description="Start date in ISO format (YYYY-MM-DD)")
    date_to: str | None = Field(default=None, description="End date in ISO format (YYYY-MM-DD)")


class UserRequirement(BaseModel):
    """Structured user requirement extracted from natural language."""
    intent: str = Field(description="Primary intent: fill_template, query_facts, summarize, edit_document")
    entities: list[str] = Field(default_factory=list, description="Entity names mentioned (cities, countries, etc.)")
    fields: list[str] = Field(default_factory=list, description="Field names mentioned")
    date_range: DateRange | None = Field(default=None, description="Date range filter if mentioned")
    aggregation: str | None = Field(default=None, description="Aggregation method: sum, avg, last, max, or None")
    extra_conditions: str = Field(default="", description="Any other conditions or filters")


class AgentPlan(BaseModel):
    """Structured agent execution plan."""
    intent: str = Field(description="Primary intent: fill_template, query_facts, summarize, edit_document, reformat")
    entities: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    date_range: DateRange | None = Field(default=None)
    aggregation: str | None = Field(default=None)
    document_keywords: list[str] = Field(default_factory=list, description="Keywords to help match source documents")
    reasoning: str = Field(default="", description="Brief reasoning for the plan")


class AggregationPlan(BaseModel):
    """How to aggregate multiple rows for the same entity."""
    field_name: str
    method: str = Field(description="sum, avg, last, max, min, count")


class AggregationPlanList(BaseModel):
    """List of aggregation plans for different fields."""
    plans: list[AggregationPlan] = Field(default_factory=list)


class EntityColumnDetection(BaseModel):
    """LLM-assisted entity column detection for a table."""
    entity_column_index: int | None = Field(default=None, description="1-based column index of the entity column, or None")
    entity_column_name: str = Field(default="", description="Header name of the entity column")
    date_column_index: int | None = Field(default=None, description="1-based column index of the date column, or None")
    date_column_name: str = Field(default="", description="Header name of the date column")
    reasoning: str = Field(default="")
