from __future__ import annotations

from collections import defaultdict
from dataclasses import replace

from app.models.domain import FactRecord


def select_scope_canonical_facts(facts: list[FactRecord]) -> list[FactRecord]:
    """在当前文档作用域内挑选每个冲突组的代表事实，并将返回结果标记为 canonical。    Select one representative fact per conflict group within the current document scope and mark returned facts as canonical."""

    grouped_facts: dict[str, list[FactRecord]] = defaultdict(list)
    for fact in facts:
        if fact.status == "rejected":
            continue
        grouped_facts[_build_scope_group_key(fact)].append(fact)

    canonical_facts: list[FactRecord] = []
    for group_facts in grouped_facts.values():
        if not group_facts:
            continue
        winner = _pick_scope_winner(group_facts)
        canonical_facts.append(
            replace(
                winner,
                conflict_group_id=_build_scope_group_key(winner),
                is_canonical=True,
            )
        )
    return _sort_scope_facts(canonical_facts)


def _build_scope_group_key(fact: FactRecord) -> str:
    """为作用域内 canonical 选优构造稳定的冲突组键。    Build a stable conflict-group key for scope-local canonical selection."""

    return (
        fact.conflict_group_id
        or f"{fact.entity_type}::{fact.entity_name}::{fact.field_name}::{fact.year}::{fact.unit}"
    )


def _pick_scope_winner(group_facts: list[FactRecord]) -> FactRecord:
    """按与仓储一致的优先级规则挑选作用域内赢家。    Pick the scope-local winner using the same priority rules as repository canonical ranking."""

    ordered_facts = list(group_facts)
    ordered_facts.sort(key=lambda item: item.fact_id)
    ordered_facts.sort(key=lambda item: item.source_doc_id, reverse=True)
    ordered_facts.sort(key=lambda item: 1 if item.value_num is not None else 0, reverse=True)
    ordered_facts.sort(key=lambda item: item.confidence, reverse=True)
    return ordered_facts[0]


def _sort_scope_facts(facts: list[FactRecord]) -> list[FactRecord]:
    """为返回结果提供稳定、可预期的排序。    Apply a stable, predictable ordering to returned scope-local facts."""

    ordered_facts = list(facts)
    ordered_facts.sort(key=lambda item: item.fact_id)
    ordered_facts.sort(key=lambda item: item.confidence, reverse=True)
    return ordered_facts
