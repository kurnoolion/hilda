"""OverrideStore seam — FR-31 item-level runtime overrides.

Per the 2026-06-12 architect ruling (rule-engine-v1 D-DRAFT-1): FR-30/FR-31 specify
*item-level* override records that beat the whole resolved YAML ladder. The landed
storage.AutomationRuleOverride is scope-level (drift, being reconciled by the architect) —
rule_engine therefore does NOT import it. This module owns the seam: the item-level
ItemOverride type + the injected OverrideStore Protocol. The reconciled storage module
implements OverrideStore later; tests inject InMemoryOverrideStore.

IO discipline per the pure-evaluator invariant: OverrideStore is consulted only at
RuleSet.load() / reload() time (snapshot), never per-evaluate.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol, Sequence, runtime_checkable

from core.src.template_schema import RuleScope

__all__ = ["InMemoryOverrideStore", "ItemOverride", "OverrideStore"]


@dataclass(frozen=True)
class ItemOverride:
    """One FR-31 runtime override: per (delivery_item_id, rule_id), beats all YAML tiers
    for that item. Worked-Example-3 shape per rule_engine/MODULE.md.

    Ph-1 override_payload supports only the polling_schedule breakpoint replacement:
    {"tiers": [{"days_before_deadline": int|None, "interval_minutes": int}, ...]}.
    Ph-2 expands to arbitrary rule-parameter overrides (MODULE.md ## Deferred)."""

    delivery_item_id: str
    rule_id: str
    override_payload: dict[str, Any]
    created_by_pm_id: str
    source_tier: RuleScope  # tier the override is overriding — FR-31 sub-2 "overridden from X" UI surfacing
    created_at: datetime


@runtime_checkable
class OverrideStore(Protocol):
    """Injected provider of active item-level overrides. Consulted at load()/reload() only."""

    def list_active(self) -> Sequence[ItemOverride]: ...


class InMemoryOverrideStore:
    """Fixture/test implementation of OverrideStore."""

    def __init__(self, overrides: Sequence[ItemOverride] = ()) -> None:
        self._overrides = tuple(overrides)

    def list_active(self) -> Sequence[ItemOverride]:
        return self._overrides
