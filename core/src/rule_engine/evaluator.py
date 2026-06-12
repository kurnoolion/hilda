"""Pure trigger -> list[RuleMatch] evaluator.

The Public Protocol surface — workflow_engine and ingest pipelines consume RuleEngine.
Side-effect-free given a fixed RuleSet snapshot: no network, no DB, no Celery, no email.

Condition expressions are declarative dicts, NOT arbitrary code (MODULE.md Invariant /
NFR-2): leaf shape {"field": str, "op": <closed set>, "value": Any}; boolean composition
{"and": [...]} / {"or": [...]}. Unsupported operators emit RUL-W005 and skip the rule.

Condition field values resolve from TriggerEvent.derived_fields first (caller-supplied
derived facts, e.g. doc_count_reached — Worked Example 3), then from the new-value side of
TriggerEvent.field_deltas. A field absent from both evaluates the leaf to False.
"""
from __future__ import annotations

import logging
from typing import Any

from core.src.diagnostics import format_code

from .loader import RuleSet
from .models import Rule, RuleMatch, TriggerEvent
from .pause_state import PauseStateLookup
from .resolver import resolve_rules_for_entity

__all__ = ["RuleEngine"]

logger = logging.getLogger(__name__)

_LEAF_OPS = ("eq", "neq", "in", "gt", "gte", "lt", "lte")
_MISSING = object()


class _UnsupportedOperator(Exception):
    def __init__(self, op: str) -> None:
        self.op = op
        super().__init__(op)


def _event_field(event: TriggerEvent, name: str) -> Any:
    if event.derived_fields and name in event.derived_fields:
        return event.derived_fields[name]
    if event.field_deltas and name in event.field_deltas:
        return event.field_deltas[name][1]  # new value
    return _MISSING


def _eval_leaf(cond: dict[str, Any], event: TriggerEvent) -> bool:
    op = cond.get("op")
    if op not in _LEAF_OPS:
        raise _UnsupportedOperator(str(op))
    actual = _event_field(event, str(cond.get("field")))
    if actual is _MISSING:
        return False
    expected = cond.get("value")
    try:
        if op == "eq":
            return bool(actual == expected)
        if op == "neq":
            return bool(actual != expected)
        if op == "in":
            return actual in expected
        if op == "gt":
            return bool(actual > expected)
        if op == "gte":
            return bool(actual >= expected)
        if op == "lt":
            return bool(actual < expected)
        return bool(actual <= expected)  # lte
    except TypeError:
        return False  # incomparable types never match


def _eval_condition(cond: dict[str, Any] | None, event: TriggerEvent) -> bool:
    if cond is None:
        return True
    if "and" in cond:
        return all(_eval_condition(sub, event) for sub in cond["and"])
    if "or" in cond:
        return any(_eval_condition(sub, event) for sub in cond["or"])
    return _eval_leaf(cond, event)


class RuleEngine:
    """Consumes a RuleSet snapshot + injected PauseStateLookup. Pure per evaluation."""

    def __init__(self, rule_set: RuleSet, pause_lookup: PauseStateLookup) -> None:
        self._rule_set = rule_set
        self._pause_lookup = pause_lookup

    # -- internals ----------------------------------------------------------

    def _condition_matches(self, rule: Rule, event: TriggerEvent) -> bool:
        try:
            return _eval_condition(rule.condition, event)
        except _UnsupportedOperator as exc:
            logger.warning("RUL-W005: " + format_code("RUL-W005", op=exc.op, rule_id=rule.rule_id))
            return False

    def _pause_state(self, rule: Rule, event: TriggerEvent) -> str:
        ref = event.entity_ref
        paused = False
        if ref.delivery_item_id is not None and self._pause_lookup.is_item_paused(ref.delivery_item_id):
            paused = True
        elif ref.milestone_id is not None and self._pause_lookup.is_milestone_paused(ref.milestone_id):
            paused = True
        if paused:
            logger.warning(
                "RUL-W003: " + format_code(
                    "RUL-W003", rule_id=rule.rule_id, delivery_item_id=ref.delivery_item_id or "<none>",
                )
            )
            return "paused"
        return "active"

    # -- public surface ------------------------------------------------------

    def evaluate(self, event: TriggerEvent) -> list[RuleMatch]:
        """Pure function. Returns ALL matching rules for the trigger, with intra-rule action
        order preserved. Across-RuleMatch ordering is NOT guaranteed — workflow_engine treats
        each RuleMatch as an independent Celery task chain. Paused items' matches are returned
        with pause_state="paused" — caller decides whether to skip (default: skip)."""
        matches: list[RuleMatch] = []
        resolved = resolve_rules_for_entity(self._rule_set, event.entity_ref, event.trigger, event.sub_trigger)
        for rule in resolved:
            if not self._condition_matches(rule, event):
                continue
            matches.append(
                RuleMatch(
                    rule_id=rule.rule_id,
                    matched_scope=rule.scope,
                    actions=rule.actions,
                    pause_state=self._pause_state(rule, event),  # type: ignore[arg-type]
                    override_source=rule.source,
                    correlation_id=event.correlation_id,
                )
            )
        return matches

    def explain(self, event: TriggerEvent) -> list[dict[str, Any]]:
        """Same resolution as evaluate() PLUS the trace per resolved rule (matched or not):
        which scope tier won, whether an FR-31 override applied, the condition result, the
        pause state, and the ordered action kinds. Used by --explain CLI mode. Pure."""
        trace: list[dict[str, Any]] = []
        resolved = resolve_rules_for_entity(self._rule_set, event.entity_ref, event.trigger, event.sub_trigger)
        for rule in resolved:
            matched = self._condition_matches(rule, event)
            trace.append(
                {
                    "rule_id": rule.rule_id,
                    "winning_scope": rule.scope.value,
                    "source": rule.source,
                    "source_tier": rule.source_tier.value if hasattr(rule.source_tier, "value") else rule.source_tier,
                    "source_file": rule.source_file,
                    "condition": rule.condition,
                    "matched": matched,
                    "pause_state": self._pause_state(rule, event) if matched else None,
                    "actions": [a.kind.value for a in rule.actions],
                }
            )
        return trace
