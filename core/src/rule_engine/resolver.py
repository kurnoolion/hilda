"""Scope-precedence resolver per FR-30 + FR-31.

Per (entity_ref, rule_id): Device tier replaces Customer replaces Global; the FR-31
item-level Postgres override (via the OverrideStore seam, D-DRAFT-1) replaces the whole
resolved YAML ladder for that specific item.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from core.src.diagnostics import PipelineError, format_code
from core.src.template_schema import RuleScope

from .loader import RuleSet
from .models import EntityRef, PollingScheduleTier, Rule, RuleKind, TriggerKind

__all__ = ["resolve_polling_schedule_for_item", "resolve_rules_for_entity"]

logger = logging.getLogger(__name__)

# Ph-1 scope: FR-31 sub-2 supports only polling_schedule breakpoint overrides; arbitrary
# rule-parameter overrides are Ph-2 (MODULE.md ## Deferred).
_PH1_OVERRIDE_KEYS = {"tiers"}


def _trigger_matches(rule: Rule, trigger: TriggerKind, sub_trigger: str | None) -> bool:
    if rule.trigger is not trigger:
        return False
    if trigger is TriggerKind.ITEM_MODIFIED:
        return rule.sub_trigger == sub_trigger
    return True


def _ladder(rule_set: RuleSet, entity_ref: EntityRef) -> list[tuple[RuleScope, dict[str, str]]]:
    """The FR-30 tiers applicable to this entity, least-specific first."""
    tiers: list[tuple[RuleScope, dict[str, str]]] = [(RuleScope.GLOBAL, {})]
    tiers.append((RuleScope.CUSTOMER, {"customer_slug": entity_ref.customer_slug}))
    if entity_ref.device_slug is not None:
        tiers.append(
            (RuleScope.DEVICE, {"customer_slug": entity_ref.customer_slug, "device_slug": entity_ref.device_slug})
        )
    return tiers


def _apply_item_override(rule_set: RuleSet, entity_ref: EntityRef, rule: Rule) -> Rule:
    """FR-31 step 4: overlay the item-level override, if one exists for (item, rule_id).

    Ph-1 applies only the polling-tiers replacement. An override row whose payload carries
    no Ph-1-applicable keys for the rule's kind is left unapplied (logged at debug — Ph-2
    expands the parameter surface)."""
    override = rule_set.override_for(entity_ref.delivery_item_id, rule.rule_id)
    if override is None:
        return rule

    payload = override.override_payload
    for key in sorted(set(payload) - _PH1_OVERRIDE_KEYS):
        # RUL-W007 per architect ruling 2026-06-12: a typo'd payload key must not be a
        # silent no-op.
        logger.warning(
            "RUL-W007: " + format_code(
                "RUL-W007", key=key, rule_id=rule.rule_id, delivery_item_id=override.delivery_item_id,
            )
        )

    if rule.kind is RuleKind.POLLING_SCHEDULE and "tiers" in payload:
        tiers = tuple(
            PollingScheduleTier(
                days_before_deadline=t.get("days_before_deadline"),
                interval_minutes=int(t["interval_minutes"]),
            )
            for t in payload["tiers"]
        )
        # source flips to postgres_override; source_tier keeps the overridden YAML tier so the
        # FR-31 sub-2 SP UI can render "overridden from <tier> default" (Worked Example 3).
        return replace(
            rule,
            source="postgres_override",
            source_file=None,
            tiers=tiers,
        )
    return rule


def resolve_rules_for_entity(
    rule_set: RuleSet,
    entity_ref: EntityRef,
    trigger: TriggerKind,
    sub_trigger: str | None = None,
) -> list[Rule]:
    """Effective TRIGGER_ACTION rule set for the entity at the trigger per FR-30:
    1. Collect Global rules (kind=TRIGGER_ACTION, matching trigger + sub_trigger).
    2. Overlay Customer rules (per rule_id, customer tier replaces global).
    3. Overlay Device rules (per rule_id, device tier replaces customer/global).
    4. Overlay FR-31 item-level overrides via the OverrideStore seam (Ph-1: polling-tiers
       only, so trigger-action rules are normally untouched here).
    POLLING_SCHEDULE rules are NOT returned — use resolve_polling_schedule_for_item."""
    resolved: dict[str, Rule] = {}
    order: list[str] = []
    for scope, scope_keys in _ladder(rule_set, entity_ref):
        for rule in rule_set.rules_for_scope(scope, scope_keys):
            if rule.kind is not RuleKind.TRIGGER_ACTION:
                continue
            if not _trigger_matches(rule, trigger, sub_trigger):
                continue
            if rule.rule_id not in resolved:
                order.append(rule.rule_id)
            resolved[rule.rule_id] = rule

    return [_apply_item_override(rule_set, entity_ref, resolved[rule_id]) for rule_id in order]


def resolve_polling_schedule_for_item(
    rule_set: RuleSet,
    entity_ref: EntityRef,
    rule_id: str = "default_polling_schedule",
) -> Rule:
    """Resolved POLLING_SCHEDULE rule for the item per the same FR-30 ladder + FR-31 item
    override. Called by workflow_engine when rescheduling per-item polling tasks (FR-26 PLM
    polling, FR-55 NSD polling). Raises RUL-E002 if no rule with the given rule_id exists at
    any tier."""
    winner: Rule | None = None
    for scope, scope_keys in _ladder(rule_set, entity_ref):
        for rule in rule_set.rules_for_scope(scope, scope_keys):
            if rule.kind is RuleKind.POLLING_SCHEDULE and rule.rule_id == rule_id:
                winner = rule
    if winner is None:
        raise PipelineError("RUL-E002", {"field": "polling_schedule", "rule_id": rule_id, "path": "<resolution>"})
    return _apply_item_override(rule_set, entity_ref, winner)
