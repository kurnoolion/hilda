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
from .models import Rule, RuleAction, RuleMatch, TriggerEvent
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


def _eval_leaf(cond: dict[str, Any], event: TriggerEvent, rule_id: str) -> bool:
    op = cond.get("op")
    if op not in _LEAF_OPS:
        raise _UnsupportedOperator(str(op))
    field_name = str(cond.get("field"))
    actual = _event_field(event, field_name)
    if actual is _MISSING:
        # Fail-closed, but visibly — a typo'd field name must not silently kill a rule
        # forever (RUL-W006 per architect ruling 2026-06-12).
        logger.warning("RUL-W006: " + format_code("RUL-W006", rule_id=rule_id, field=field_name))
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


def _eval_condition(cond: dict[str, Any] | None, event: TriggerEvent, rule_id: str) -> bool:
    if cond is None:
        return True
    if "and" in cond:
        return all(_eval_condition(sub, event, rule_id) for sub in cond["and"])
    if "or" in cond:
        return any(_eval_condition(sub, event, rule_id) for sub in cond["or"])
    return _eval_leaf(cond, event, rule_id)


class RuleEngine:
    """Consumes a RuleSet snapshot. Pure per evaluation.

    Per D5 cascade 2026-06-23: PauseStateLookup Protocol DROPPED from Ph-1. Pause state
    is read directly from `item_snapshot.rules_paused` passed by the caller (workflow_engine
    task body reads the item from storage before invoking the evaluator). Item-less events
    (MilestoneAllClosed, CredentialExpired, etc.) skip the pause check entirely.
    """

    def __init__(self, rule_set: RuleSet) -> None:
        self._rule_set = rule_set

    # -- internals ----------------------------------------------------------

    def _condition_matches(self, rule: Rule, event: TriggerEvent) -> bool:
        try:
            return _eval_condition(rule.condition, event, rule.rule_id)
        except _UnsupportedOperator as exc:
            logger.warning("RUL-W005: " + format_code("RUL-W005", op=exc.op, rule_id=rule.rule_id))
            return False

    def _pause_state(self, rule: Rule, event: TriggerEvent, item_snapshot: Any) -> str:
        """Ph-1 pause check per D5 cascade 2026-06-23: read item_snapshot.rules_paused if
        the caller supplied a snapshot AND the event carries a delivery_item_id. Item-less
        triggers skip the pause check (active by default)."""
        ref = event.entity_ref
        if ref.delivery_item_id is None or item_snapshot is None:
            return "active"
        if getattr(item_snapshot, "rules_paused", False):
            logger.warning(
                "RUL-W003: " + format_code(
                    "RUL-W003", rule_id=rule.rule_id, delivery_item_id=ref.delivery_item_id,
                )
            )
            return "paused"
        return "active"

    # -- public surface ------------------------------------------------------

    def evaluate(self, event: TriggerEvent, item_snapshot: Any = None) -> list[RuleMatch]:
        """Pure function. Returns ALL matching rules for the trigger, with intra-rule action
        order preserved. Across-RuleMatch ordering is NOT guaranteed -- workflow_engine treats
        each RuleMatch as an independent Celery task chain.

        `item_snapshot` (Ph-1, NEW per D5 cascade 2026-06-23): when the event carries a
        delivery_item_id, caller passes the item snapshot (DeliveryItemBase or compatible
        SimpleNamespace) so evaluator can read `item_snapshot.rules_paused` for FR-31 sub-1
        per-item pause. If `item_snapshot.rules_paused` is True, all matches are returned
        with pause_state='paused' -- workflow_engine skips the chain by default policy.
        Item-less events (e.g., MilestoneAllClosed) pass item_snapshot=None and skip the
        pause check entirely."""
        matches: list[RuleMatch] = []
        resolved = resolve_rules_for_entity(self._rule_set, event.entity_ref, event.trigger, event.sub_trigger)
        # DEBUG-INSTRUMENTATION 2026-06-30 -- remove after PM-approve cascade
        # debugging is complete.
        import logging as _dbg_logging
        _dbg_log = _dbg_logging.getLogger(__name__)
        _dbg_log.info(
            "DBG rule_engine.evaluate ENTRY corr_id=%s trigger=%s sub_trigger=%s "
            "resolved_rule_ids=%s",
            getattr(event, 'correlation_id', '?'),
            getattr(event.trigger, 'value', event.trigger),
            event.sub_trigger,
            [r.rule_id for r in resolved],
        )
        for rule in resolved:
            cond_ok = self._condition_matches(rule, event)
            _dbg_log.info(
                "DBG rule_engine.evaluate RULE corr_id=%s rule_id=%s condition_match=%s",
                getattr(event, 'correlation_id', '?'), rule.rule_id, cond_ok,
            )
            if not cond_ok:
                continue
            matches.append(
                RuleMatch(
                    rule_id=rule.rule_id,
                    matched_scope=rule.scope,
                    actions=rule.actions,
                    pause_state=self._pause_state(rule, event, item_snapshot),  # type: ignore[arg-type]
                    override_source=rule.source,
                    correlation_id=event.correlation_id,
                )
            )
        _dbg_log.info(
            "DBG rule_engine.evaluate RESULT corr_id=%s matches=%d rule_ids=%s",
            getattr(event, 'correlation_id', '?'),
            len(matches),
            [m.rule_id for m in matches],
        )
        return matches

    def explain(self, event: TriggerEvent, item_snapshot: Any = None) -> list[dict[str, Any]]:
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
                    "pause_state": self._pause_state(rule, event, item_snapshot) if matched else None,
                    "actions": [a.kind.value for a in rule.actions],
                }
            )
        return trace
