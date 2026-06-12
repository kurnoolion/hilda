"""Startup collision audit: RUL-W001 when distinct rule_ids both write UpdateState on the
same trigger (configuration smell — multiple rules competing to set delivery_state).

TRIGGER_ACTION-only — polling_schedule rules can't UpdateState. rule_engine does NOT block
load; emits warnings for ops triage.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import combinations

from core.src.diagnostics import format_code

from .models import ActionKind, Rule, RuleKind, TriggerKind

__all__ = ["CollisionFinding", "collision_audit_update_state"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CollisionFinding:
    rule_id_a: str
    rule_id_b: str
    trigger: TriggerKind
    scope_keys: tuple[tuple[str, str], ...]  # the more specific of the pair's scope_keys


def _scopes_compatible(a: dict[str, str], b: dict[str, str]) -> bool:
    """Two rules can fire for the same entity when one's scope_keys is a subset of the
    other's (e.g. a Global rule and a carrier-alpha Customer rule)."""
    items_a, items_b = set(a.items()), set(b.items())
    return items_a <= items_b or items_b <= items_a


def collision_audit_update_state(
    rules_by_trigger: dict[TriggerKind, list[Rule]],
) -> list[CollisionFinding]:
    """For each trigger, find pairs of distinct rule_ids (with overlapping scope) that both
    produce an UPDATE_STATE action. Logs RUL-W001 per collision pair. Pure — no IO."""
    findings: list[CollisionFinding] = []
    for trigger, rules in rules_by_trigger.items():
        writers = [
            r for r in rules
            if r.kind is RuleKind.TRIGGER_ACTION
            and any(a.kind is ActionKind.UPDATE_STATE for a in r.actions)
        ]
        for r1, r2 in combinations(writers, 2):
            if r1.rule_id == r2.rule_id:
                continue  # same rule_id at different tiers is the FR-30 ladder, not a collision
            if not _scopes_compatible(r1.scope_keys, r2.scope_keys):
                continue
            more_specific = r1 if len(r1.scope_keys) >= len(r2.scope_keys) else r2
            finding = CollisionFinding(
                rule_id_a=min(r1.rule_id, r2.rule_id),
                rule_id_b=max(r1.rule_id, r2.rule_id),
                trigger=trigger,
                scope_keys=tuple(sorted(more_specific.scope_keys.items())),
            )
            if finding not in findings:
                findings.append(finding)
    for f in findings:
        logger.warning(
            "RUL-W001: " + format_code(
                "RUL-W001", r1=f.rule_id_a, r2=f.rule_id_b,
                trigger=f.trigger.value, keys=str(dict(f.scope_keys)),
            )
        )
    return findings
