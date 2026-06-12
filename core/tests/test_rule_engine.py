"""Tests for rule_engine models + polling_schedule (Ph-1, rule-engine-v1 strand)."""
from __future__ import annotations

from datetime import datetime

import pytest

from core.src.diagnostics import PREFIX_REGISTRY, get_code
from core.src.rule_engine import (
    ITEM_MODIFIED_SUB_TRIGGERS_PH1,
    ActionKind,
    EntityRef,
    PollingScheduleTier,
    Rule,
    RuleAction,
    RuleKind,
    RuleMatch,
    RuleScope,
    TriggerEvent,
    TriggerKind,
    evaluate_polling_schedule,
)
from core.src.template_schema import RuleScope as CanonicalRuleScope

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ENTITY = EntityRef(
    customer_slug="carrier-alpha",
    device_slug="smartphone-X",
    milestone_id="M-1001",
    delivery_item_id="I-1234",
)


def make_action(kind: ActionKind = ActionKind.SEND_REMINDER, seq: int = 0) -> RuleAction:
    return RuleAction(kind=kind, params={"template": "standard_owner_reminder"}, sequence=seq)


def make_trigger_rule(**overrides) -> Rule:
    defaults = dict(
        rule_id="send_reminder_on_no_contact",
        kind=RuleKind.TRIGGER_ACTION,
        scope=RuleScope.GLOBAL,
        scope_keys={},
        source="yaml",
        source_file="customizations/rules/global/defaults.yaml",
        source_tier=RuleScope.GLOBAL,
        trigger=TriggerKind.LAST_CONTACT_THRESHOLD,
        actions=(make_action(),),
    )
    defaults.update(overrides)
    return Rule(**defaults)


def make_polling_rule(**overrides) -> Rule:
    defaults = dict(
        rule_id="default_polling_schedule",
        kind=RuleKind.POLLING_SCHEDULE,
        scope=RuleScope.GLOBAL,
        scope_keys={},
        source="yaml",
        source_file="customizations/rules/global/defaults.yaml",
        source_tier=RuleScope.GLOBAL,
        tiers=(
            PollingScheduleTier(days_before_deadline=None, interval_minutes=60),
            PollingScheduleTier(days_before_deadline=3, interval_minutes=15),
            PollingScheduleTier(days_before_deadline=1, interval_minutes=5),
        ),
    )
    defaults.update(overrides)
    return Rule(**defaults)


# ---------------------------------------------------------------------------
# Enums + canonical RuleScope reuse
# ---------------------------------------------------------------------------


class TestEnums:
    def test_trigger_kind_has_13_members(self):
        # 13 enum members; FR-28's "15 Ph-1 triggers" counts the 3 ItemModified sub-triggers.
        assert len(TriggerKind) == 13

    def test_item_modified_sub_triggers_ph1(self):
        assert ITEM_MODIFIED_SUB_TRIGGERS_PH1 == {"OwnerReassigned", "DeadlineMoved", "TagsModified"}

    def test_action_kind_has_18_members(self):
        assert len(ActionKind) == 18

    def test_action_kind_excludes_ph2_actions(self):
        ph2 = {"CancelOutstanding", "NotifyOwnerDocCountPending", "TriggerVersionSelection",
               "TriggerPLMCleanup", "TriggerODF", "SendOwnerRoutingQuery"}
        assert ph2.isdisjoint({a.value for a in ActionKind})

    def test_rule_scope_is_canonical_template_schema_enum(self):
        # Architect ruling 2026-06-12: no local lowercase duplicate.
        assert RuleScope is CanonicalRuleScope
        assert RuleScope.GLOBAL.value == "Global"
        assert RuleScope.CUSTOMER.value == "Customer"
        assert RuleScope.DEVICE.value == "Device"

    def test_rule_kind_values(self):
        assert RuleKind.TRIGGER_ACTION.value == "trigger_action"
        assert RuleKind.POLLING_SCHEDULE.value == "polling_schedule"


# ---------------------------------------------------------------------------
# Error-code registration
# ---------------------------------------------------------------------------


class TestErrorCodes:
    def test_rul_prefix_registered(self):
        assert PREFIX_REGISTRY["RUL"] == "rule_engine"

    @pytest.mark.parametrize("code", [
        "RUL-E001", "RUL-E002", "RUL-E003", "RUL-E004", "RUL-E005",
        "RUL-W001", "RUL-W002", "RUL-W003", "RUL-W004", "RUL-W005",
    ])
    def test_codes_registered(self, code):
        assert get_code(code).code == code

    def test_error_recoverability(self):
        assert not get_code("RUL-E001").recoverable
        assert get_code("RUL-W001").recoverable


# ---------------------------------------------------------------------------
# Rule shape discriminator (MODULE.md Invariant: Rule shape discriminator)
# ---------------------------------------------------------------------------


class TestRuleShapeValidation:
    def test_valid_trigger_action_rule(self):
        rule = make_trigger_rule()
        assert rule.kind is RuleKind.TRIGGER_ACTION
        assert rule.actions[0].kind is ActionKind.SEND_REMINDER

    def test_valid_polling_schedule_rule(self):
        rule = make_polling_rule()
        assert rule.kind is RuleKind.POLLING_SCHEDULE
        assert len(rule.tiers) == 3

    def test_trigger_action_requires_trigger(self):
        with pytest.raises(ValueError, match="requires a trigger"):
            make_trigger_rule(trigger=None)

    def test_trigger_action_requires_actions(self):
        with pytest.raises(ValueError, match="non-empty actions"):
            make_trigger_rule(actions=())

    def test_trigger_action_rejects_tiers(self):
        with pytest.raises(ValueError, match="must not carry polling tiers"):
            make_trigger_rule(tiers=(PollingScheduleTier(None, 60),))

    def test_item_modified_requires_sub_trigger(self):
        with pytest.raises(ValueError, match="requires sub_trigger"):
            make_trigger_rule(trigger=TriggerKind.ITEM_MODIFIED, sub_trigger=None)

    def test_item_modified_with_sub_trigger_ok(self):
        rule = make_trigger_rule(trigger=TriggerKind.ITEM_MODIFIED, sub_trigger="OwnerReassigned")
        assert rule.sub_trigger == "OwnerReassigned"

    def test_sub_trigger_invalid_on_other_triggers(self):
        with pytest.raises(ValueError, match="only valid with trigger=ItemModified"):
            make_trigger_rule(trigger=TriggerKind.STATE_CHANGE, sub_trigger="OwnerReassigned")

    def test_polling_schedule_requires_tiers(self):
        with pytest.raises(ValueError, match="non-empty tiers"):
            make_polling_rule(tiers=())

    def test_polling_schedule_rejects_trigger_action_fields(self):
        with pytest.raises(ValueError, match="must not carry"):
            make_polling_rule(trigger=TriggerKind.ATTACHMENT_RECEIVED)
        with pytest.raises(ValueError, match="must not carry"):
            make_polling_rule(actions=(make_action(),))
        with pytest.raises(ValueError, match="must not carry"):
            make_polling_rule(condition={"field": "doc_type", "op": "eq", "value": "test_report"})

    def test_rule_is_frozen(self):
        rule = make_trigger_rule()
        with pytest.raises(AttributeError):
            rule.rule_id = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TriggerEvent / RuleMatch construction
# ---------------------------------------------------------------------------


class TestEventAndMatch:
    def test_trigger_event_construction(self):
        event = TriggerEvent(
            trigger=TriggerKind.ITEM_MODIFIED,
            sub_trigger="OwnerReassigned",
            entity_ref=ENTITY,
            field_deltas={"owner_email": ("old@example.com", "new@example.com")},
            timestamp=datetime(2026, 6, 12, 14, 30),
            correlation_id="evt-abc123",
        )
        assert event.entity_ref.delivery_item_id == "I-1234"

    def test_rule_match_carries_ordered_actions_verbatim(self):
        actions = (make_action(ActionKind.NOTIFY_NEW_OWNER, 0), make_action(ActionKind.START_ITEM_COLLECTION, 1))
        match = RuleMatch(
            rule_id="handle_owner_reassignment",
            matched_scope=RuleScope.GLOBAL,
            actions=actions,
            pause_state="active",
            override_source="yaml",
            correlation_id="evt-abc123",
        )
        assert [a.kind for a in match.actions] == [ActionKind.NOTIFY_NEW_OWNER, ActionKind.START_ITEM_COLLECTION]
        assert [a.sequence for a in match.actions] == [0, 1]


# ---------------------------------------------------------------------------
# polling_schedule tier math (MODULE.md worked example)
# ---------------------------------------------------------------------------

TIERS = (
    PollingScheduleTier(days_before_deadline=None, interval_minutes=60),
    PollingScheduleTier(days_before_deadline=3, interval_minutes=15),
    PollingScheduleTier(days_before_deadline=1, interval_minutes=5),
)


class TestEvaluatePollingSchedule:
    @pytest.mark.parametrize("days,expected", [
        (10, 60),  # beyond all breakpoints -> baseline
        (4, 60),   # MODULE.md example
        (3, 15),   # boundary: 3 <= 3
        (2, 15),   # MODULE.md example
        (1, 5),    # boundary: 1 <= 1
        (0, 5),    # MODULE.md example
        (-2, 5),   # past deadline -> tightest tier still applies
    ])
    def test_worked_example(self, days, expected):
        assert evaluate_polling_schedule(TIERS, days) == expected

    def test_tier_input_order_is_irrelevant(self):
        shuffled = (TIERS[2], TIERS[0], TIERS[1])
        assert evaluate_polling_schedule(shuffled, 2) == 15

    def test_baseline_only(self):
        baseline_only = (PollingScheduleTier(None, 30),)
        assert evaluate_polling_schedule(baseline_only, 0) == 30

    def test_missing_baseline_warns_rul_w004_and_falls_back(self, caplog):
        no_baseline = (PollingScheduleTier(3, 15),)
        with caplog.at_level("WARNING"):
            # No covering tier (days=10 > 3) -> default fallback
            assert evaluate_polling_schedule(no_baseline, 10, rule_id="default_polling_schedule") == 60
        assert any("RUL-W004" in r.message for r in caplog.records)
        assert any("default_polling_schedule" in r.message for r in caplog.records)

    def test_missing_baseline_with_covering_tier_uses_tier(self, caplog):
        no_baseline = (PollingScheduleTier(3, 15),)
        with caplog.at_level("WARNING"):
            assert evaluate_polling_schedule(no_baseline, 2) == 15
        assert any("RUL-W004" in r.message for r in caplog.records)

    def test_custom_default_baseline(self):
        assert evaluate_polling_schedule((), 5, default_baseline_minutes=45) == 45

    def test_fr31_override_tiers_shape(self):
        # Worked Example 3: TPM per-item override tiers (30, 10, 2)
        override = (
            PollingScheduleTier(None, 30),
            PollingScheduleTier(3, 10),
            PollingScheduleTier(1, 2),
        )
        assert evaluate_polling_schedule(override, 2) == 10
