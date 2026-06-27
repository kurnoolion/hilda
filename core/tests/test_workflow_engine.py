"""workflow_engine foundation tests (Celery app + config + registry + dispatcher
+ manual_trigger). Task bodies + trigger_sources + polling tests land with
subsequent commits as those modules build.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from celery import Celery

from core.src.diagnostics.error_codes import PipelineError
from core.src.rule_engine import (
    ActionKind,
    EntityRef,
    Rule,
    RuleAction,
    RuleEngine,
    RuleKind,
    RuleMatch,
    RuleScope,
    RuleSet,
    TriggerEvent,
    TriggerKind,
)
from core.src.workflow_engine import (
    ACTION_KIND_TO_TASK,
    DispatchResult,
    RetryPolicy,
    TaskBinding,
    TriggerDispatcher,
    WorkflowEngineConfig,
    build_celery_app,
    build_chain_from_rule_match,
    expected_action_kinds_ph1,
    handle_manual_trigger,
    hilda_celery_app,
    lookup_for_manual_trigger,
    register_task_binding,
    registry_complete,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _eager_celery():
    """Run Celery tasks synchronously for tests; no broker / no result backend needed."""
    original_backend = hilda_celery_app.conf.result_backend
    hilda_celery_app.conf.task_always_eager = True
    hilda_celery_app.conf.task_eager_propagates = True
    hilda_celery_app.conf.result_backend = "cache+memory://"
    yield
    hilda_celery_app.conf.task_always_eager = False
    hilda_celery_app.conf.task_eager_propagates = False
    hilda_celery_app.conf.result_backend = original_backend


@pytest.fixture
def _registry_snapshot():
    """Snapshot + restore the registry so test mutations don't leak."""
    snapshot = dict(ACTION_KIND_TO_TASK)
    yield
    ACTION_KIND_TO_TASK.clear()
    ACTION_KIND_TO_TASK.update(snapshot)


def make_event(
    trigger: TriggerKind = TriggerKind.STATE_CHANGE,
    delivery_item_id: str | None = "I-1234",
    customer_id: str = "MMK",
    device_id: str | None = "MODEL-A",
    sub_trigger: str | None = None,
    correlation_id: str = "corr-001",
) -> TriggerEvent:
    return TriggerEvent(
        trigger=trigger,
        sub_trigger=sub_trigger,
        entity_ref=EntityRef(
            customer_id=customer_id,
            device_id=device_id,
            milestone_id="M-1001",
            delivery_item_id=delivery_item_id,
        ),
        field_deltas={"delivery_state": ("Open", "OutreachSent")},
        timestamp=datetime(2026, 6, 23, 14, 30, tzinfo=timezone.utc),
        correlation_id=correlation_id,
    )


def make_rule(rule_id: str = "test_rule",
              actions: tuple[RuleAction, ...] = ()) -> Rule:
    if not actions:
        actions = (
            RuleAction(kind=ActionKind.NOTIFY_PM, params={"urgency": "low"}, sequence=0),
        )
    return Rule(
        rule_id=rule_id,
        kind=RuleKind.TRIGGER_ACTION,
        scope=RuleScope.GLOBAL,
        scope_keys={},
        source="yaml",
        source_file="/tmp/test.yaml",
        source_tier=RuleScope.GLOBAL,
        trigger=TriggerKind.STATE_CHANGE,
        actions=actions,
    )


def make_match(rule_id: str = "test_rule",
               pause_state: str = "active",
               actions: tuple[RuleAction, ...] = ()) -> RuleMatch:
    if not actions:
        actions = (
            RuleAction(kind=ActionKind.NOTIFY_PM, params={"urgency": "low"}, sequence=0),
        )
    return RuleMatch(
        rule_id=rule_id,
        matched_scope=RuleScope.GLOBAL,
        actions=actions,
        pause_state=pause_state,
        override_source="yaml",
        correlation_id="corr-001",
    )


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self):
        cfg = WorkflowEngineConfig()
        assert cfg.broker_url.startswith("redis://")
        assert cfg.worker_concurrency_default == 4
        assert cfg.beat_default_interval_s == 300
        assert cfg.task_default_max_retries == 5

    def test_from_sources_env_override(self, monkeypatch):
        monkeypatch.setenv("HILDA_WORKFLOW_ENGINE_WORKER_CONCURRENCY_DEFAULT", "8")
        cfg = WorkflowEngineConfig.from_sources()
        assert cfg.worker_concurrency_default == 8

    def test_from_sources_cli_overrides_env(self, monkeypatch):
        monkeypatch.setenv("HILDA_WORKFLOW_ENGINE_BEAT_DEFAULT_INTERVAL_S", "600")
        cfg = WorkflowEngineConfig.from_sources(cli_overrides={"beat_default_interval_s": 900})
        assert cfg.beat_default_interval_s == 900


# ---------------------------------------------------------------------------
# Celery app tests
# ---------------------------------------------------------------------------


class TestCeleryApp:
    def test_hilda_celery_app_is_celery_instance(self):
        assert isinstance(hilda_celery_app, Celery)
        assert hilda_celery_app.main == "hilda"

    def test_queue_routing_defined(self):
        routes = hilda_celery_app.conf.task_routes
        assert "*" in routes
        assert routes["*"]["queue"] == "default"
        assert routes["core.src.workflow_engine.tasks.review.trigger_ai_review"]["queue"] == "llm_calls"
        assert routes["core.src.workflow_engine.tasks.submission.queue_submission"]["queue"] == "browser_automation"

    def test_build_celery_app_with_custom_config(self):
        cfg = WorkflowEngineConfig(broker_url="redis://custom:6379/1")
        app = build_celery_app(cfg)
        assert app.conf.broker_url == "redis://custom:6379/1"


# ---------------------------------------------------------------------------
# Registry tests
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_expected_20_action_kinds(self):
        # Bumped 18 -> 20 on 2026-06-26 per [D-118] strict-boundary cascade:
        # IMPORT_DELIVERABLE_TRACKER + KICKOFF_COLLECTION added.
        assert len(expected_action_kinds_ph1()) == 20

    def test_registry_initially_empty_or_partial(self, _registry_snapshot):
        # In a fresh test run with the registry snapshotted, we can clear and verify
        ACTION_KIND_TO_TASK.clear()
        assert not registry_complete()

    def test_register_and_lookup(self, _registry_snapshot):
        ACTION_KIND_TO_TASK.clear()

        @hilda_celery_app.task
        def dummy_task(params, event_context):
            return {"ok": True}

        binding = TaskBinding(
            action_kind=ActionKind.NOTIFY_PM,
            celery_task=dummy_task,
            queue="default",
            retry_policy=RetryPolicy(max_retries=3),
        )
        register_task_binding(binding)
        assert ACTION_KIND_TO_TASK[ActionKind.NOTIFY_PM] is binding

        # Lookup ok
        result = lookup_for_manual_trigger(ActionKind.NOTIFY_PM)
        assert result is binding

    def test_register_duplicate_same_binding_noop(self, _registry_snapshot):
        ACTION_KIND_TO_TASK.clear()

        @hilda_celery_app.task
        def dummy_task(params, event_context):
            return {}

        binding = TaskBinding(action_kind=ActionKind.NOTIFY_PM, celery_task=dummy_task, queue="default")
        register_task_binding(binding)
        register_task_binding(binding)
        assert len(ACTION_KIND_TO_TASK) == 1

    def test_register_conflicting_binding_raises(self, _registry_snapshot):
        ACTION_KIND_TO_TASK.clear()

        @hilda_celery_app.task
        def t1(params, event_context):
            return {}

        @hilda_celery_app.task
        def t2(params, event_context):
            return {}

        register_task_binding(TaskBinding(action_kind=ActionKind.NOTIFY_PM, celery_task=t1, queue="default"))
        with pytest.raises(ValueError, match="already registered"):
            register_task_binding(TaskBinding(action_kind=ActionKind.NOTIFY_PM, celery_task=t2, queue="default"))

    def test_lookup_missing_raises_wfl_e001(self, _registry_snapshot):
        ACTION_KIND_TO_TASK.clear()
        with pytest.raises(PipelineError) as exc:
            lookup_for_manual_trigger(ActionKind.SEND_REMINDER)
        assert exc.value.code_id == "WFL-E001"

    def test_build_chain_single_action(self, _registry_snapshot):
        ACTION_KIND_TO_TASK.clear()

        @hilda_celery_app.task
        def task_a(params, event_context):
            return {"executed": "a", "params": params}

        register_task_binding(TaskBinding(action_kind=ActionKind.NOTIFY_PM, celery_task=task_a, queue="default"))
        match = make_match(actions=(
            RuleAction(kind=ActionKind.NOTIFY_PM, params={"urgency": "low"}, sequence=0),
        ))
        chain = build_chain_from_rule_match(match, event_context={"correlation_id": "c-1"})
        # Eager mode -> apply_async returns AsyncResult with executed value
        result = chain.apply_async().get()
        assert result["executed"] == "a"

    def test_build_chain_multi_action(self, _registry_snapshot):
        ACTION_KIND_TO_TASK.clear()

        @hilda_celery_app.task
        def task_a(params, event_context):
            return {"step": "a"}

        @hilda_celery_app.task
        def task_b(params, event_context, previous_result=None):
            return {"step": "b", "previous": previous_result.get("step") if previous_result else None}

        register_task_binding(TaskBinding(action_kind=ActionKind.UPDATE_STATE, celery_task=task_a, queue="default"))
        register_task_binding(TaskBinding(action_kind=ActionKind.TRIGGER_AI_REVIEW, celery_task=task_b, queue="llm_calls"))
        match = make_match(actions=(
            RuleAction(kind=ActionKind.UPDATE_STATE, params={"target_state": "DocumentReceived"}, sequence=0),
            RuleAction(kind=ActionKind.TRIGGER_AI_REVIEW, params={}, sequence=1),
        ))
        chain = build_chain_from_rule_match(match, event_context={"correlation_id": "c-1"})
        result = chain.apply_async().get()
        assert result["step"] == "b"

    def test_build_chain_unknown_action_kind_raises(self, _registry_snapshot):
        ACTION_KIND_TO_TASK.clear()
        match = make_match(actions=(
            RuleAction(kind=ActionKind.NOTIFY_PM, params={}, sequence=0),
        ))
        with pytest.raises(PipelineError) as exc:
            build_chain_from_rule_match(match, event_context={})
        assert exc.value.code_id == "WFL-E001"

    def test_build_chain_empty_actions_raises(self):
        # Bypass make_match's default-fallback by constructing a RuleMatch directly.
        empty_match = RuleMatch(
            rule_id="empty_rule", matched_scope=RuleScope.GLOBAL, actions=(),
            pause_state="active", override_source="yaml", correlation_id="c-1",
        )
        with pytest.raises(ValueError, match="empty actions"):
            build_chain_from_rule_match(empty_match, event_context={})


# ---------------------------------------------------------------------------
# Dispatcher tests
# ---------------------------------------------------------------------------


class TestDispatcher:
    def test_dispatch_no_matches_returns_empty(self, _registry_snapshot):
        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = []
        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine)
        result = dispatcher.dispatch(make_event())
        assert isinstance(result, DispatchResult)
        assert result.matched_count == 0
        assert result.scheduled_tasks == []
        assert result.skipped_matches == []

    def test_dispatch_single_match_schedules_chain(self, _registry_snapshot):
        ACTION_KIND_TO_TASK.clear()

        @hilda_celery_app.task
        def dummy(params, event_context):
            return {"ok": True}

        register_task_binding(TaskBinding(action_kind=ActionKind.NOTIFY_PM, celery_task=dummy, queue="default"))

        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = [make_match()]
        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine)
        result = dispatcher.dispatch(make_event())
        assert result.matched_count == 1
        assert len(result.scheduled_tasks) == 1
        assert result.skipped_matches == []

    def test_dispatch_paused_match_skipped_with_wfl_w001(self, _registry_snapshot, caplog):
        ACTION_KIND_TO_TASK.clear()

        @hilda_celery_app.task
        def dummy(params, event_context):
            return {"ok": True}

        register_task_binding(TaskBinding(action_kind=ActionKind.NOTIFY_PM, celery_task=dummy, queue="default"))
        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = [make_match(pause_state="paused")]
        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine)
        with caplog.at_level("WARNING"):
            result = dispatcher.dispatch(make_event())
        assert result.matched_count == 1
        assert result.scheduled_tasks == []
        assert ("test_rule", "paused") in result.skipped_matches
        assert any("WFL-W001" in r.message for r in caplog.records)

    def test_dispatch_fetches_item_snapshot_when_storage_provided(self, _registry_snapshot):
        ACTION_KIND_TO_TASK.clear()

        @hilda_celery_app.task
        def dummy(params, event_context):
            return {"ok": True}

        register_task_binding(TaskBinding(action_kind=ActionKind.NOTIFY_PM, celery_task=dummy, queue="default"))

        # Storage returns an item with rules_paused=False
        item = SimpleNamespace(rules_paused=False, delivery_item_id="I-1234")
        mock_storage = MagicMock()
        mock_storage.get_delivery_item.return_value = item

        # rule_engine should receive item_snapshot in evaluate call
        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = [make_match()]

        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine, storage=mock_storage)
        dispatcher.dispatch(make_event(delivery_item_id="I-1234"))

        mock_storage.get_delivery_item.assert_called_once_with("I-1234")
        _, kwargs = mock_rule_engine.evaluate.call_args
        assert kwargs.get("item_snapshot") is item

    def test_dispatch_item_less_event_skips_storage_fetch(self, _registry_snapshot):
        mock_storage = MagicMock()
        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = []
        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine, storage=mock_storage)
        dispatcher.dispatch(make_event(delivery_item_id=None))
        mock_storage.get_delivery_item.assert_not_called()
        _, kwargs = mock_rule_engine.evaluate.call_args
        assert kwargs.get("item_snapshot") is None

    # ---- Cold-start enrichment: item snapshot promoted into derived_fields ----
    # 2026-06-27 per architect rule-walk-through finding.

    def test_dispatch_enriches_event_derived_fields_from_item_snapshot(self, _registry_snapshot):
        """item.delivery_state / force_tracking_enabled / item_type / reminder_count
        get promoted into event.derived_fields so condition evaluator can read them."""
        item = SimpleNamespace(
            delivery_item_id="I-1234",
            customer_id="MMK",
            device_id="MODEL-A",
            milestone_id="M-1001",
            delivery_state="Outreach Sent",
            force_tracking_enabled=True,
            item_type="test_tech_waiver_report",
            reminder_count=1,
            tg_name="MNO-ETM",
            owner_corp_email="owner@corp.example",
            rules_paused=False,
        )
        mock_storage = MagicMock()
        mock_storage.get_delivery_item.return_value = item
        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = []

        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine, storage=mock_storage)
        dispatcher.dispatch(make_event(delivery_item_id="I-1234"))

        # Inspect the enriched event passed to rule_engine.evaluate
        args, _ = mock_rule_engine.evaluate.call_args
        enriched_event = args[0]
        assert enriched_event.derived_fields["delivery_state"] == "Outreach Sent"
        assert enriched_event.derived_fields["force_tracking_enabled"] is True
        assert enriched_event.derived_fields["item_type"] == "test_tech_waiver_report"
        assert enriched_event.derived_fields["reminder_count"] == 1
        assert enriched_event.derived_fields["tg_name"] == "MNO-ETM"
        assert enriched_event.derived_fields["owner_corp_email"] == "owner@corp.example"

    def test_dispatch_caller_supplied_derived_fields_win_over_item_snapshot(
        self, _registry_snapshot
    ):
        """sp_alert_parser's body_kvs / kickoff_collection's kickoff_source must
        not be clobbered by item snapshot fields with the same name."""
        item = SimpleNamespace(
            delivery_item_id="I-1234",
            item_type="storage_value",      # would be promoted...
            tg_name="storage_tg",
            rules_paused=False,
        )
        mock_storage = MagicMock()
        mock_storage.get_delivery_item.return_value = item
        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = []

        # Event carries caller-supplied derived_fields that overlap with item fields
        event = TriggerEvent(
            trigger=TriggerKind.ITEM_CREATED,
            sub_trigger=None,
            entity_ref=EntityRef(customer_id="MMK", device_id="MODEL-A",
                                 milestone_id="M-1001", delivery_item_id="I-1234"),
            field_deltas=None,
            timestamp=datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            correlation_id="corr-002",
            derived_fields={"item_type": "caller_value", "kickoff_source": "kickoff_collection_task"},
        )

        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine, storage=mock_storage)
        dispatcher.dispatch(event)

        args, _ = mock_rule_engine.evaluate.call_args
        enriched_event = args[0]
        # Caller-supplied item_type wins
        assert enriched_event.derived_fields["item_type"] == "caller_value"
        # Caller-supplied kickoff_source preserved
        assert enriched_event.derived_fields["kickoff_source"] == "kickoff_collection_task"
        # Storage tg_name (not in caller-supplied) gets promoted
        assert enriched_event.derived_fields["tg_name"] == "storage_tg"

    def test_dispatch_item_less_event_skips_enrichment(self, _registry_snapshot):
        """No item_snapshot -> no enrichment; event passes through unchanged."""
        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = []
        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine)

        original_derived = {"some_caller_fact": "value"}
        event = TriggerEvent(
            trigger=TriggerKind.MILESTONE_ALL_CLOSED,
            sub_trigger=None,
            entity_ref=EntityRef(customer_id="MMK", milestone_id="M-1001"),
            field_deltas=None,
            timestamp=datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            correlation_id="corr-003",
            derived_fields=original_derived,
        )
        dispatcher.dispatch(event)
        args, _ = mock_rule_engine.evaluate.call_args
        # Same derived_fields (no enrichment because no item snapshot)
        assert args[0].derived_fields == original_derived

    # ---- Derived facts: doc_count_reached (Chunk A 2026-06-27) ----

    def test_dispatch_derives_doc_count_reached_true(self, _registry_snapshot):
        """item.doc_count_received >= item.doc_count -> doc_count_reached=True
        per FR-28 OwnerStatusConfirmed condition (i)."""
        item = SimpleNamespace(
            delivery_item_id="I-1234",
            doc_count=2,
            doc_count_received=2,
            rules_paused=False,
        )
        mock_storage = MagicMock()
        mock_storage.get_delivery_item.return_value = item
        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = []
        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine, storage=mock_storage)
        dispatcher.dispatch(make_event(delivery_item_id="I-1234"))
        args, _ = mock_rule_engine.evaluate.call_args
        assert args[0].derived_fields["doc_count_reached"] is True

    def test_dispatch_derives_doc_count_reached_false(self, _registry_snapshot):
        """received < expected -> doc_count_reached=False."""
        item = SimpleNamespace(
            delivery_item_id="I-1234",
            doc_count=3,
            doc_count_received=1,
            rules_paused=False,
        )
        mock_storage = MagicMock()
        mock_storage.get_delivery_item.return_value = item
        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = []
        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine, storage=mock_storage)
        dispatcher.dispatch(make_event(delivery_item_id="I-1234"))
        args, _ = mock_rule_engine.evaluate.call_args
        assert args[0].derived_fields["doc_count_reached"] is False

    def test_dispatch_derives_doc_count_reached_handles_missing_attrs(
        self, _registry_snapshot
    ):
        """Item without doc_count fields -> derives False safely (0 >= 0)."""
        item = SimpleNamespace(delivery_item_id="I-1234", rules_paused=False)
        mock_storage = MagicMock()
        mock_storage.get_delivery_item.return_value = item
        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = []
        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine, storage=mock_storage)
        dispatcher.dispatch(make_event(delivery_item_id="I-1234"))
        args, _ = mock_rule_engine.evaluate.call_args
        # 0 received >= 0 expected -> True (vacuously satisfied when no docs expected).
        assert args[0].derived_fields["doc_count_reached"] is True

    def test_dispatch_caller_supplied_doc_count_reached_wins(
        self, _registry_snapshot
    ):
        """Caller-supplied doc_count_reached overrides the derivation."""
        item = SimpleNamespace(
            delivery_item_id="I-1234",
            doc_count=5,
            doc_count_received=1,   # would derive False...
            rules_paused=False,
        )
        mock_storage = MagicMock()
        mock_storage.get_delivery_item.return_value = item
        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = []

        event = TriggerEvent(
            trigger=TriggerKind.STATE_CHANGE,
            sub_trigger=None,
            entity_ref=EntityRef(customer_id="MMK", device_id="MODEL-A",
                                 milestone_id="M-1001", delivery_item_id="I-1234"),
            field_deltas=None,
            timestamp=datetime(2026, 6, 27, 12, 0, tzinfo=timezone.utc),
            correlation_id="corr-004",
            derived_fields={"doc_count_reached": True},   # ...but caller forces True
        )

        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine, storage=mock_storage)
        dispatcher.dispatch(event)
        args, _ = mock_rule_engine.evaluate.call_args
        assert args[0].derived_fields["doc_count_reached"] is True

    def test_dispatch_event_context_uses_id_keys_per_d091(self, _registry_snapshot):
        """Per D1 cascade 2026-06-23: event_context uses customer_id / device_id
        (was customer_slug / device_slug)."""
        ACTION_KIND_TO_TASK.clear()
        captured_context = {}

        @hilda_celery_app.task
        def capture(params, event_context):
            captured_context.update(event_context)
            return {}

        register_task_binding(TaskBinding(action_kind=ActionKind.NOTIFY_PM, celery_task=capture, queue="default"))
        mock_rule_engine = MagicMock(spec=RuleEngine)
        mock_rule_engine.evaluate.return_value = [make_match()]
        dispatcher = TriggerDispatcher(rule_engine=mock_rule_engine)
        dispatcher.dispatch(make_event(customer_id="MMK", device_id="MODEL-A"))
        assert captured_context["customer_id"] == "MMK"
        assert captured_context["device_id"] == "MODEL-A"
        assert "customer_slug" not in captured_context
        assert "device_slug" not in captured_context


# ---------------------------------------------------------------------------
# Manual trigger tests
# ---------------------------------------------------------------------------


class TestManualTrigger:
    def test_handle_manual_trigger_dispatches(self, _registry_snapshot):
        ACTION_KIND_TO_TASK.clear()
        captured = {}

        @hilda_celery_app.task
        def reminder_task(params, event_context):
            captured.update(event_context)
            return {"reminder_sent": True}

        register_task_binding(TaskBinding(
            action_kind=ActionKind.SEND_REMINDER, celery_task=reminder_task, queue="default",
        ))
        task_id = handle_manual_trigger(
            delivery_item_id="I-1234",
            action_kind=ActionKind.SEND_REMINDER,
            params={"template": "standard_reminder"},
            invoked_by_pm_id="pm-001",
            correlation_id="corr-002",
            customer_id="MMK",
            milestone_id="M-1001",
        )
        assert task_id is not None
        assert captured["trigger_source"] == "manual"
        assert captured["invoked_by_pm_id"] == "pm-001"
        assert captured["customer_id"] == "MMK"
        assert captured["delivery_item_id"] == "I-1234"

    def test_handle_manual_trigger_missing_registry_raises(self, _registry_snapshot):
        ACTION_KIND_TO_TASK.clear()
        with pytest.raises(PipelineError) as exc:
            handle_manual_trigger(
                delivery_item_id="I-1234",
                action_kind=ActionKind.SEND_REMINDER,
                params={},
                invoked_by_pm_id="pm-001",
                correlation_id="corr-003",
            )
        assert exc.value.code_id == "WFL-E001"


# ---------------------------------------------------------------------------
# Error code registration tests
# ---------------------------------------------------------------------------


class TestErrorCodes:
    def test_all_11_wfl_codes_registered(self):
        from core.src.diagnostics.error_codes import ERROR_CODES
        expected = {f"WFL-E00{i}" for i in range(1, 7)} | {f"WFL-W00{i}" for i in range(1, 6)}
        present = {c for c in ERROR_CODES if c.startswith("WFL-")}
        assert expected == present, f"Missing: {expected - present}; extra: {present - expected}"
