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
    customer_id="carrier-alpha",
    device_id="smartphone-X",
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
        assert ITEM_MODIFIED_SUB_TRIGGERS_PH1 == {
            "OwnerReassigned", "DeadlineMoved", "TagsModified", "PmApproved",
        }

    def test_action_kind_has_21_members(self):
        # Bumped 18 -> 20 on 2026-06-26 per [D-118] strict-boundary cascade
        # (added IMPORT_DELIVERABLE_TRACKER + KICKOFF_COLLECTION). Bumped
        # 20 -> 21 on 2026-06-28 per architect PM-approval design pass:
        # added APPLY_PM_APPROVAL (Pattern A SP-authoritative mirror per [D-068]).
        assert len(ActionKind) == 21

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


# ---------------------------------------------------------------------------
# Loader / resolver / evaluator / audits / CLI (worked-example tree)
# ---------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

from core.src.diagnostics import PipelineError  # noqa: E402
# Ph-2 forward-looking imports (per D4 cascade 2026-06-23 -- not in Ph-1 __all__ but
# still importable from sub-modules for tests):
from core.src.rule_engine.orphan_audit import orphan_audit_postgres_overrides  # noqa: E402
from core.src.rule_engine.override_store import (  # noqa: E402
    InMemoryOverrideStore,
    ItemOverride,
)
from core.src.rule_engine import (  # noqa: E402
    RuleEngine,
    RuleSet,
    collision_audit_update_state,
    resolve_polling_schedule_for_item,
    resolve_rules_for_entity,
)
from core.src.rule_engine.rule_engine_cli import main as cli_main  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

GLOBAL_YAML = """
rules:
  - rule_id: handle_owner_reassignment
    trigger: ItemModified
    sub_trigger: OwnerReassigned
    condition: null
    actions:
      - kind: NotifyNewOwner
        params: { template: owner_reassignment_notice, channel: email }
      - kind: StartItemCollection
        params: {}
  - rule_id: send_reminder_on_no_contact
    trigger: LastContactThreshold
    condition: { field: reminder_count_unanswered, op: lt, value: 3 }
    actions:
      - kind: SendReminder
        params: { template: standard_owner_reminder, channel: email }
  - rule_id: escalate_after_3_misses
    trigger: LastContactThreshold
    condition: { field: reminder_count_unanswered, op: gte, value: 3 }
    actions:
      - kind: Escalate
        params: { channel: corp_messenger }
      - kind: NotifyPM
        params: { urgency: medium }
  - rule_id: advance_state_on_doc_count_reached
    trigger: AttachmentReceived
    condition: { field: doc_count_reached, op: eq, value: true }
    actions:
      - kind: UpdateState
        params: { target_state: DocumentReceived }
      - kind: TriggerAIReview
        params: {}
  - rule_id: review_on_supplementary_attachment
    trigger: AttachmentReceived
    condition:
      and:
        - { field: doc_count_reached, op: eq, value: false }
        - { field: review_required,   op: eq, value: true  }
    actions:
      - kind: TriggerAIReview
        params: {}

polling_schedules:
  - rule_id: default_polling_schedule
    tiers:
      - { days_before_deadline: null, interval_minutes: 60 }
      - { days_before_deadline: 3,    interval_minutes: 15 }
      - { days_before_deadline: 1,    interval_minutes: 5  }
"""

CUSTOMER_YAML = """
rules:
  - rule_id: send_reminder_on_no_contact          # same rule_id as Global -> OVERRIDES
    trigger: LastContactThreshold
    condition: { field: reminder_count_unanswered, op: lt, value: 3 }
    actions:
      - kind: SendReminder
        params: { template: alpha_branded_reminder, channel: email }
  - rule_id: alpha_cc_tg_lead                      # NEW rule_id -> ADDITIVE
    trigger: LastContactThreshold
    condition: null
    actions:
      - kind: NotifyPM
        params: { recipient: tg_lead, urgency: low }
"""

DEVICE_YAML = """
rules:
  - rule_id: alpha_cc_tg_lead                      # device tier replaces customer tier
    trigger: LastContactThreshold
    condition: null
    actions:
      - kind: NotifyPM
        params: { recipient: device_lead, urgency: high }
"""


@pytest.fixture()
def rules_tree(tmp_path: Path) -> Path:
    rules_dir = tmp_path / "rules"
    (rules_dir / "global").mkdir(parents=True)
    (rules_dir / "global" / "defaults.yaml").write_text(GLOBAL_YAML)
    (rules_dir / "carrier-alpha").mkdir()
    (rules_dir / "carrier-alpha" / "customer_rules.yaml").write_text(CUSTOMER_YAML)
    (rules_dir / "carrier-alpha" / "smartphone-X").mkdir()
    (rules_dir / "carrier-alpha" / "smartphone-X" / "device_rules.yaml").write_text(DEVICE_YAML)
    return rules_dir


def event_for(trigger: TriggerKind, *, sub_trigger=None, field_deltas=None, derived_fields=None,
              entity: EntityRef = ENTITY) -> TriggerEvent:
    return TriggerEvent(
        trigger=trigger,
        sub_trigger=sub_trigger,
        entity_ref=entity,
        field_deltas=field_deltas,
        timestamp=datetime(2026, 6, 12, 14, 30),
        correlation_id="evt-test",
        derived_fields=derived_fields,
    )


class TestLoader:
    def test_loads_all_tiers(self, rules_tree):
        rs = RuleSet.load(rules_tree)
        assert len(rs.rules_for_scope(RuleScope.GLOBAL, {})) == 6  # 5 trigger + 1 polling
        assert len(rs.rules_for_scope(RuleScope.CUSTOMER, {"customer_id": "carrier-alpha"})) == 2
        assert len(rs.rules_for_scope(RuleScope.DEVICE,
                                      {"customer_id": "carrier-alpha", "device_id": "smartphone-X"})) == 1
        assert "default_polling_schedule" in rs.all_rule_ids()

    def test_repo_sample_global_defaults_loads(self):
        rs = RuleSet.load(REPO_ROOT / "customizations" / "rules")
        # defaults.yaml: 5 trigger_action rules (Worked Examples 1-3 +
        # advance_state_on_doc_count_reached + review_on_supplementary_attachment)
        # + 1 polling_schedule (default_polling_schedule) = 6.
        # automation_rules.yaml: 17 trigger_action rules + 2 polling_schedules = 19.
        #   automation_rules history: was 18 then 16 (removed FR-87 + AI-pass);
        #   architect 2026-06-28 changes: removed
        #   instantiate_default_work_item_on_tracker_created per [D-118]/[D-135]
        #   strict-boundary (SP UI engineer creates Default WI; HILDA imports);
        #   added import_deliverable_tracker_on_sp_add +
        #   kickoff_collection_on_milestone_started for the alert-driven listener
        #   = net +1 over the prior 16 + 2 polling = 18 baseline -> 19 now.
        # Total: 6 + 19 = 25. Bumped to 26 on 2026-06-29 architect Step 5.5:
        # added reconcile_owner_intent_on_doc_count_reached to automation_rules
        # (race-resolution rule per Q1 design pass -- catches owner Closed
        # intent persisted when doc_count_not_reached guard-denied earlier).
        # Dropped to 25 on 2026-06-29 architect Ph-2 defer: commented out
        # review_on_supplementary_attachment in defaults.yaml since it was a
        # pure TriggerAIReview wrapper and AI review is Ph-2 (no TaskBinding;
        # was causing WFL-E001 dispatch failures). Restore when the AI review
        # binding lands. The advance_state_on_doc_count_reached rule's
        # TriggerAIReview action was also commented but the rule itself stays
        # (UpdateState remains).
        assert len(rs.all_rules()) == 25
        assert not rs.collision_findings

    def test_duplicate_rule_id_same_bucket_raises_e001(self, rules_tree):
        extra = rules_tree / "global" / "zz_dup.yaml"
        extra.write_text(
            "rules:\n  - rule_id: send_reminder_on_no_contact\n    trigger: LastContactThreshold\n"
            "    actions:\n      - kind: SendReminder\n        params: {}\n"
        )
        with pytest.raises(PipelineError) as exc:
            RuleSet.load(rules_tree)
        assert exc.value.code_id == "RUL-E001"

    def test_same_rule_id_across_tiers_is_not_duplicate(self, rules_tree):
        RuleSet.load(rules_tree)  # send_reminder_on_no_contact exists at Global + Customer

    def test_missing_required_field_raises_e002(self, tmp_path):
        d = tmp_path / "rules" / "global"
        d.mkdir(parents=True)
        (d / "bad.yaml").write_text("rules:\n  - rule_id: r1\n    actions:\n      - kind: SendReminder\n")
        with pytest.raises(PipelineError) as exc:
            RuleSet.load(tmp_path / "rules")
        assert exc.value.code_id == "RUL-E002"

    def test_unknown_trigger_raises_e003(self, tmp_path):
        d = tmp_path / "rules" / "global"
        d.mkdir(parents=True)
        (d / "bad.yaml").write_text(
            "rules:\n  - rule_id: r1\n    trigger: NotATrigger\n    actions:\n      - kind: SendReminder\n"
        )
        with pytest.raises(PipelineError) as exc:
            RuleSet.load(tmp_path / "rules")
        assert exc.value.code_id == "RUL-E003"

    def test_unknown_action_raises_e004(self, tmp_path):
        d = tmp_path / "rules" / "global"
        d.mkdir(parents=True)
        (d / "bad.yaml").write_text(
            "rules:\n  - rule_id: r1\n    trigger: StateChange\n    actions:\n      - kind: LaunchMissiles\n"
        )
        with pytest.raises(PipelineError) as exc:
            RuleSet.load(tmp_path / "rules")
        assert exc.value.code_id == "RUL-E004"

    def test_item_modified_without_sub_trigger_is_shape_error(self, tmp_path):
        d = tmp_path / "rules" / "global"
        d.mkdir(parents=True)
        (d / "bad.yaml").write_text(
            "rules:\n  - rule_id: r1\n    trigger: ItemModified\n    actions:\n      - kind: SendReminder\n"
        )
        with pytest.raises(PipelineError) as exc:
            RuleSet.load(tmp_path / "rules")
        assert exc.value.code_id == "RUL-E002"

    def test_override_store_failure_raises_e005(self, rules_tree):
        class BrokenStore:
            def list_active(self):
                raise ConnectionError("pg down")

        with pytest.raises(PipelineError) as exc:
            RuleSet.load(rules_tree, BrokenStore())
        assert exc.value.code_id == "RUL-E005"

    def test_same_rule_id_across_kinds_warns_w008(self, tmp_path, caplog):
        d = tmp_path / "rules" / "global"
        d.mkdir(parents=True)
        (d / "both.yaml").write_text(
            "rules:\n"
            "  - rule_id: dual_use_id\n"
            "    trigger: StateChange\n"
            "    actions:\n"
            "      - kind: NotifyPM\n"
            "        params: {}\n"
            "polling_schedules:\n"
            "  - rule_id: dual_use_id\n"
            "    tiers:\n"
            "      - { days_before_deadline: null, interval_minutes: 60 }\n"
        )
        with caplog.at_level("WARNING"):
            rs = RuleSet.load(tmp_path / "rules")  # legal — uniqueness key includes kind
        assert "dual_use_id" in rs.all_rule_ids()
        assert any("RUL-W008" in r.message and "dual_use_id" in r.message for r in caplog.records)

    def test_reload_picks_up_yaml_change(self, rules_tree):
        rs = RuleSet.load(rules_tree)
        assert len(rs.rules_for_scope(RuleScope.GLOBAL, {})) == 6
        (rules_tree / "global" / "extra.yaml").write_text(
            "rules:\n  - rule_id: extra_rule\n    trigger: StateChange\n"
            "    actions:\n      - kind: NotifyPM\n        params: {}\n"
        )
        rs.reload()
        assert "extra_rule" in rs.all_rule_ids()


OVERRIDE = ItemOverride(
    delivery_item_id="I-1234",
    rule_id="default_polling_schedule",
    override_payload={"tiers": [
        {"days_before_deadline": None, "interval_minutes": 30},
        {"days_before_deadline": 3, "interval_minutes": 10},
        {"days_before_deadline": 1, "interval_minutes": 2},
    ]},
    created_by_pm_id="tpm-003",
    source_tier=RuleScope.GLOBAL,
    created_at=datetime(2026, 6, 10, 13, 0),
)


class TestResolver:
    def test_we2_ladder_override_and_additive(self, rules_tree):
        rs = RuleSet.load(rules_tree)
        entity = EntityRef(customer_id="carrier-alpha")  # no device tier
        rules = resolve_rules_for_entity(rs, entity, TriggerKind.LAST_CONTACT_THRESHOLD)
        by_id = {r.rule_id: r for r in rules}
        assert set(by_id) == {"send_reminder_on_no_contact", "escalate_after_3_misses", "alpha_cc_tg_lead"}
        # customer tier replaced global per rule_id (branded template)
        assert by_id["send_reminder_on_no_contact"].scope is RuleScope.CUSTOMER
        assert by_id["send_reminder_on_no_contact"].actions[0].params["template"] == "alpha_branded_reminder"
        # no customer override -> global wins
        assert by_id["escalate_after_3_misses"].scope is RuleScope.GLOBAL

    def test_device_tier_replaces_customer(self, rules_tree):
        rs = RuleSet.load(rules_tree)
        rules = resolve_rules_for_entity(rs, ENTITY, TriggerKind.LAST_CONTACT_THRESHOLD)
        by_id = {r.rule_id: r for r in rules}
        assert by_id["alpha_cc_tg_lead"].scope is RuleScope.DEVICE
        assert by_id["alpha_cc_tg_lead"].actions[0].params["recipient"] == "device_lead"

    def test_sub_trigger_discrimination(self, rules_tree):
        rs = RuleSet.load(rules_tree)
        assert resolve_rules_for_entity(rs, ENTITY, TriggerKind.ITEM_MODIFIED, "OwnerReassigned")
        assert not resolve_rules_for_entity(rs, ENTITY, TriggerKind.ITEM_MODIFIED, "DeadlineMoved")

    def test_polling_rules_not_returned_for_triggers(self, rules_tree):
        rs = RuleSet.load(rules_tree)
        for trigger in TriggerKind:
            sub = "OwnerReassigned" if trigger is TriggerKind.ITEM_MODIFIED else None
            for rule in resolve_rules_for_entity(rs, ENTITY, trigger, sub):
                assert rule.kind is RuleKind.TRIGGER_ACTION

    def test_polling_schedule_resolution_without_override(self, rules_tree):
        rs = RuleSet.load(rules_tree)
        rule = resolve_polling_schedule_for_item(rs, ENTITY)
        assert rule.source == "yaml"
        assert evaluate_polling_schedule(rule.tiers, 2) == 15

    def test_we3_polling_schedule_item_override(self, rules_tree):
        rs = RuleSet.load(rules_tree, InMemoryOverrideStore([OVERRIDE]))
        rule = resolve_polling_schedule_for_item(rs, ENTITY)
        assert rule.source == "postgres_override"
        assert rule.source_tier is RuleScope.GLOBAL  # "overridden from global default"
        assert evaluate_polling_schedule(rule.tiers, 2) == 10
        # other item unaffected
        other = EntityRef(customer_id="carrier-alpha", delivery_item_id="I-9999")
        assert resolve_polling_schedule_for_item(rs, other).source == "yaml"

    def test_unknown_polling_rule_id_raises_e002(self, rules_tree):
        rs = RuleSet.load(rules_tree)
        with pytest.raises(PipelineError) as exc:
            resolve_polling_schedule_for_item(rs, ENTITY, rule_id="no_such_schedule")
        assert exc.value.code_id == "RUL-E002"

    def test_unsupported_override_payload_key_warns_w007(self, rules_tree, caplog):
        typo_override = ItemOverride(
            delivery_item_id="I-1234",
            rule_id="default_polling_schedule",
            override_payload={"teirs": [{"days_before_deadline": None, "interval_minutes": 30}]},  # typo'd key
            created_by_pm_id="tpm-003",
            source_tier=RuleScope.GLOBAL,
            created_at=datetime(2026, 6, 12),
        )
        rs = RuleSet.load(rules_tree, InMemoryOverrideStore([typo_override]))
        with caplog.at_level("WARNING"):
            rule = resolve_polling_schedule_for_item(rs, ENTITY)
        # typo'd key is ignored, not a silent no-op: YAML rule unchanged + RUL-W007 logged
        assert rule.source == "yaml"
        assert any("RUL-W007" in r.message and "teirs" in r.message for r in caplog.records)


class PausedItems:
    def __init__(self, items=(), milestones=()):
        self.items, self.milestones = set(items), set(milestones)

    def is_item_paused(self, delivery_item_id: str) -> bool:
        return delivery_item_id in self.items

    def is_milestone_paused(self, milestone_id: str) -> bool:
        return milestone_id in self.milestones


class TestEvaluator:
    def test_we1_single_rule_two_ordered_actions(self, rules_tree):
        engine = RuleEngine(RuleSet.load(rules_tree))
        matches = engine.evaluate(event_for(
            TriggerKind.ITEM_MODIFIED, sub_trigger="OwnerReassigned",
            field_deltas={"owner_email": ("old@example.com", "new@example.com")},
        ))
        assert len(matches) == 1
        assert [a.kind for a in matches[0].actions] == [ActionKind.NOTIFY_NEW_OWNER, ActionKind.START_ITEM_COLLECTION]
        assert matches[0].pause_state == "active"
        assert matches[0].correlation_id == "evt-test"

    def test_we2_two_matches_with_conditions(self, rules_tree):
        engine = RuleEngine(RuleSet.load(rules_tree))
        entity = EntityRef(customer_id="carrier-alpha", delivery_item_id="I-1234")
        matches = engine.evaluate(event_for(
            TriggerKind.LAST_CONTACT_THRESHOLD,
            field_deltas={"reminder_count_unanswered": (1, 2)},  # condition reads new value 2
            entity=entity,
        ))
        assert {m.rule_id for m in matches} == {"send_reminder_on_no_contact", "alpha_cc_tg_lead"}
        reminder = next(m for m in matches if m.rule_id == "send_reminder_on_no_contact")
        assert reminder.matched_scope is RuleScope.CUSTOMER
        assert reminder.actions[0].params["template"] == "alpha_branded_reminder"

    def test_we2_escalation_at_threshold(self, rules_tree):
        engine = RuleEngine(RuleSet.load(rules_tree))
        matches = engine.evaluate(event_for(
            TriggerKind.LAST_CONTACT_THRESHOLD,
            field_deltas={"reminder_count_unanswered": (2, 3)},
            entity=EntityRef(customer_id="carrier-alpha"),
        ))
        assert {m.rule_id for m in matches} == {"escalate_after_3_misses", "alpha_cc_tg_lead"}

    def test_we3_derived_fields_and_condition(self, rules_tree, caplog):
        engine = RuleEngine(RuleSet.load(rules_tree))
        matches = engine.evaluate(event_for(
            TriggerKind.ATTACHMENT_RECEIVED,
            field_deltas={"doc_count_received": (4, 5)},
            derived_fields={"doc_count_reached": True, "review_required": True},
        ))
        assert [m.rule_id for m in matches] == ["advance_state_on_doc_count_reached"]
        assert [a.kind for a in matches[0].actions] == [ActionKind.UPDATE_STATE, ActionKind.TRIGGER_AI_REVIEW]

    def test_paused_item_flagged_not_dropped(self, rules_tree, caplog):
        """Per D5 cascade 2026-06-23: pause check reads item_snapshot.rules_paused
        directly; no PauseStateLookup Protocol."""
        from types import SimpleNamespace
        engine = RuleEngine(RuleSet.load(rules_tree))
        paused_item = SimpleNamespace(rules_paused=True)
        with caplog.at_level("WARNING"):
            matches = engine.evaluate(
                event_for(TriggerKind.ITEM_MODIFIED, sub_trigger="OwnerReassigned"),
                item_snapshot=paused_item,
            )
        assert len(matches) == 1
        assert matches[0].pause_state == "paused"
        assert any("RUL-W003" in r.message for r in caplog.records)

    def test_milestone_pause_is_ph2_deferred(self, rules_tree):
        """Per D5 cascade 2026-06-23: milestone-level rules_paused column is Ph-2
        deferred. Ph-1 milestone-pause UX requires bulk write to all per-item
        rules_paused fields. Test verifies Ph-1 behavior: item-less events skip the
        pause check entirely."""
        entity = EntityRef(customer_id="carrier-alpha", milestone_id="M-1001")
        engine = RuleEngine(RuleSet.load(rules_tree))
        matches = engine.evaluate(event_for(
            TriggerKind.LAST_CONTACT_THRESHOLD,
            field_deltas={"reminder_count_unanswered": (0, 1)},
            entity=entity,
        ))
        # Item-less event (no delivery_item_id on EntityRef) -> pause check skipped
        assert matches and all(m.pause_state == "active" for m in matches)

    def test_unsupported_operator_skips_rule_with_w005(self, tmp_path, caplog):
        d = tmp_path / "rules" / "global"
        d.mkdir(parents=True)
        (d / "r.yaml").write_text(
            "rules:\n  - rule_id: regex_rule\n    trigger: StateChange\n"
            "    condition: { field: x, op: regex, value: 'a.*' }\n"
            "    actions:\n      - kind: NotifyPM\n        params: {}\n"
        )
        engine = RuleEngine(RuleSet.load(tmp_path / "rules"))
        with caplog.at_level("WARNING"):
            matches = engine.evaluate(event_for(TriggerKind.STATE_CHANGE, derived_fields={"x": "abc"}))
        assert matches == []
        assert any("RUL-W005" in r.message for r in caplog.records)

    def test_missing_condition_field_fails_closed_with_w006(self, rules_tree, caplog):
        engine = RuleEngine(RuleSet.load(rules_tree))
        with caplog.at_level("WARNING"):
            matches = engine.evaluate(event_for(TriggerKind.LAST_CONTACT_THRESHOLD,
                                                entity=EntityRef(customer_id="carrier-alpha")))
        # conditional rules can't evaluate without the field; unconditional alpha_cc still fires
        assert {m.rule_id for m in matches} == {"alpha_cc_tg_lead"}
        # fail-closed but visible: RUL-W006 per architect ruling 2026-06-12
        w006 = [r.message for r in caplog.records if "RUL-W006" in r.message]
        assert any("reminder_count_unanswered" in m for m in w006)

    def test_explain_trace(self, rules_tree):
        engine = RuleEngine(RuleSet.load(rules_tree))
        trace = engine.explain(event_for(
            TriggerKind.LAST_CONTACT_THRESHOLD,
            field_deltas={"reminder_count_unanswered": (1, 2)},
            entity=EntityRef(customer_id="carrier-alpha"),
        ))
        by_id = {t["rule_id"]: t for t in trace}
        assert by_id["send_reminder_on_no_contact"]["matched"] is True
        assert by_id["send_reminder_on_no_contact"]["winning_scope"] == "Customer"
        assert by_id["escalate_after_3_misses"]["matched"] is False
        assert by_id["alpha_cc_tg_lead"]["actions"] == ["NotifyPM"]


class TestAudits:
    def test_collision_detected_across_compatible_scopes(self, caplog):
        r1 = make_trigger_rule(rule_id="state_writer_a", trigger=TriggerKind.ATTACHMENT_RECEIVED,
                               actions=(make_action(ActionKind.UPDATE_STATE),))
        r2 = make_trigger_rule(rule_id="state_writer_b", trigger=TriggerKind.ATTACHMENT_RECEIVED,
                               scope=RuleScope.CUSTOMER, scope_keys={"customer_id": "carrier-alpha"},
                               source_tier=RuleScope.CUSTOMER,
                               actions=(make_action(ActionKind.UPDATE_STATE),))
        with caplog.at_level("WARNING"):
            findings = collision_audit_update_state({TriggerKind.ATTACHMENT_RECEIVED: [r1, r2]})
        assert len(findings) == 1
        assert {findings[0].rule_id_a, findings[0].rule_id_b} == {"state_writer_a", "state_writer_b"}
        assert any("RUL-W001" in r.message for r in caplog.records)

    def test_same_rule_id_across_tiers_is_ladder_not_collision(self):
        r1 = make_trigger_rule(rule_id="w", trigger=TriggerKind.STATE_CHANGE,
                               actions=(make_action(ActionKind.UPDATE_STATE),))
        r2 = make_trigger_rule(rule_id="w", trigger=TriggerKind.STATE_CHANGE,
                               scope=RuleScope.CUSTOMER, scope_keys={"customer_id": "c1"},
                               source_tier=RuleScope.CUSTOMER,
                               actions=(make_action(ActionKind.UPDATE_STATE),))
        assert collision_audit_update_state({TriggerKind.STATE_CHANGE: [r1, r2]}) == []

    def test_disjoint_customer_scopes_no_collision(self):
        r1 = make_trigger_rule(rule_id="a", trigger=TriggerKind.STATE_CHANGE,
                               scope=RuleScope.CUSTOMER, scope_keys={"customer_id": "c1"},
                               source_tier=RuleScope.CUSTOMER,
                               actions=(make_action(ActionKind.UPDATE_STATE),))
        r2 = make_trigger_rule(rule_id="b", trigger=TriggerKind.STATE_CHANGE,
                               scope=RuleScope.CUSTOMER, scope_keys={"customer_id": "c2"},
                               source_tier=RuleScope.CUSTOMER,
                               actions=(make_action(ActionKind.UPDATE_STATE),))
        assert collision_audit_update_state({TriggerKind.STATE_CHANGE: [r1, r2]}) == []

    def test_orphan_override_flagged_w002(self, caplog):
        orphan = ItemOverride(
            delivery_item_id="I-1", rule_id="ghost_rule", override_payload={},
            created_by_pm_id="tpm-1", source_tier=RuleScope.GLOBAL,
            created_at=datetime(2026, 6, 12),
        )
        with caplog.at_level("WARNING"):
            findings = orphan_audit_postgres_overrides({"real_rule"}, [orphan])
        assert findings == [type(findings[0])(rule_id="ghost_rule", delivery_item_id="I-1")]
        assert any("RUL-W002" in r.message for r in caplog.records)

    def test_known_override_not_flagged(self):
        ok = ItemOverride(
            delivery_item_id="I-1", rule_id="real_rule", override_payload={},
            created_by_pm_id="tpm-1", source_tier=RuleScope.GLOBAL,
            created_at=datetime(2026, 6, 12),
        )
        assert orphan_audit_postgres_overrides({"real_rule"}, [ok]) == []

    def test_loader_runs_audits_and_stores_findings(self, rules_tree):
        rs = RuleSet.load(rules_tree, InMemoryOverrideStore([ItemOverride(
            delivery_item_id="I-1", rule_id="ghost_rule", override_payload={},
            created_by_pm_id="tpm-1", source_tier=RuleScope.GLOBAL,
            created_at=datetime(2026, 6, 12),
        )]))
        assert len(rs.orphan_findings) == 1
        assert rs.collision_findings == []


class TestCLI:
    def test_validate_ok(self, rules_tree, capsys):
        assert cli_main(["--validate", "--rules-dir", str(rules_tree), "--run-id", "run-test"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("QC|RUL|run-test|")
        assert "result=OK" in out

    def test_validate_broken_tree_exits_nonzero(self, tmp_path, capsys):
        d = tmp_path / "rules" / "global"
        d.mkdir(parents=True)
        (d / "bad.yaml").write_text(
            "rules:\n  - rule_id: r1\n    trigger: StateChange\n    actions:\n      - kind: LaunchMissiles\n"
        )
        assert cli_main(["--validate", "--rules-dir", str(tmp_path / "rules"), "--run-id", "run-test"]) == 1
        captured = capsys.readouterr()
        assert "RUL-E004" in captured.err
        assert "result=FAIL" in captured.out

    def test_diagnostic_rpt_line(self, rules_tree, capsys):
        assert cli_main(["--diagnostic", "--rules-dir", str(rules_tree), "--run-id", "run-test"]) == 0
        out = capsys.readouterr().out
        assert out.startswith("RPT|RUL|run-test|")
        assert "rules_total=9" in out          # 6 global + 2 customer + 1 device
        # Bumped 15 -> 16 on 2026-06-28 per architect PM-approval design pass:
        # ITEM_MODIFIED_SUB_TRIGGERS_PH1 grew {OwnerReassigned, DeadlineMoved,
        # TagsModified} -> +PmApproved = 4. _PH1_TRIGGER_COUNT = (13-1)+4 = 16.
        assert "trigger_kinds=16" in out
        # Bumped 18 -> 20 on 2026-06-26 per [D-118] cascade.
        # Bumped 20 -> 21 on 2026-06-28 per architect PM-approval design pass
        # (APPLY_PM_APPROVAL added).
        assert "action_kinds=21" in out
        assert "postgres_overrides=0" in out

    def test_explain_emits_met_and_trace(self, rules_tree, capsys):
        rc = cli_main([
            "--explain", "--rules-dir", str(rules_tree), "--run-id", "run-test",
            "--trigger", "LastContactThreshold",
            "--entity", '{"customer_id": "carrier-alpha"}',
            "--field-deltas", '{"reminder_count_unanswered": [1, 2]}',
        ])
        assert rc == 0
        out = capsys.readouterr().out
        assert "MET|RUL|run-test|" in out
        assert "matched_rules=2" in out
        assert "send_reminder_on_no_contact:customer" in out

    def test_explain_unknown_trigger_errors(self, rules_tree, capsys):
        rc = cli_main([
            "--explain", "--rules-dir", str(rules_tree),
            "--trigger", "NotATrigger", "--entity", '{"customer_id": "c1"}',
        ])
        assert rc == 1
        assert "RUL-E003" in capsys.readouterr().err

    def test_simulate_is_ph3_stub(self, capsys):
        assert cli_main(["--simulate", "candidate.yaml"]) == 2
        assert "Ph-3+" in capsys.readouterr().err
