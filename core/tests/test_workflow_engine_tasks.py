"""workflow_engine task body tests -- delegating to tracker + workflow-owned
escalation tasks.

Covers state.py + milestone.py + routing_resolution.py + escalation.py.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.src.rule_engine import ActionKind
from core.src.template_schema import DocType, ItemType
from core.src.tracker import DeliveryState
from core.src.workflow_engine import (
    ACTION_KIND_TO_TASK,
    TaskDeps,
    hilda_celery_app,
    override_task_deps,
)
from core.src.workflow_engine.tasks.escalation import (
    notify_hilda_ops_task,
    notify_pm_task,
)
from core.src.workflow_engine.tasks.milestone import (
    close_all_items_task,
    final_sweep_task,
    halt_milestone_polling_task,
    milestone_storage_cleanup_task,
)
from core.src.workflow_engine.tasks.routing_resolution import (
    propagate_tags_to_active_trackers_task,
    reassign_document_to_work_item_task,
    rearm_deadline_proximity_task,
)
from core.src.workflow_engine.tasks.state import (
    instantiate_default_work_item_task,
    update_state_task,
)


# ---------------------------------------------------------------------------
# Mocks
# ---------------------------------------------------------------------------


class MockStorage:
    def __init__(self):
        self.items = {}
        self.di_updates = []
        self.writes = []
        self.default_wi_by_milestone = {}
        self.doc_index_by_hash = {}
        self.doc_slugs_by_item = {}
        self.upload_enqueued = []
        self.target_folder_result = None
        self.list_items_response = []

    def get_delivery_item(self, item_id):
        return self.items[item_id]

    def write_delivery_state(self, delivery_item_id, new_state, modified_at, modified_by):
        self.items[delivery_item_id].delivery_state = new_state
        self.writes.append(("state", delivery_item_id, new_state.value))

    def update_delivery_item(self, id, fields):
        ns = self.items.setdefault(id, SimpleNamespace())
        for k, v in fields.items():
            setattr(ns, k, v)
        self.di_updates.append((id, dict(fields)))

    def update_document_item_association(self, *a, **k):
        pass

    def update_document_index_row(self, *a, **k):
        pass

    def find_items_by_natural_key(self, customer_id, tg_name, item_no, only_active=True):
        return [i for i in self.items.values()
                if getattr(i, "customer_id", None) == customer_id
                and getattr(i, "tg_name", None) == tg_name
                and getattr(i, "item_no", None) == item_no]

    def list_default_workitem_for_milestone(self, milestone_id):
        return self.default_wi_by_milestone.get(milestone_id)

    def list_items_for_milestone(self, milestone_id, states):
        return self.list_items_response

    # Optional helpers for reassignment -- must filter by target_item_id + doc_type
    # to match real storage behavior (hash dedup only when same target + doc_type)
    def find_doc_index_row_by_hash(self, file_hash, target_item_id, doc_type):
        row = self.doc_index_by_hash.get(file_hash)
        if row is None:
            return None
        if (getattr(row, "delivery_item_id", None) == target_item_id
                and getattr(row, "doc_type", None) == doc_type.value):
            return row
        return None

    def list_doc_id_slugs_for_item_doc_type(self, target_item_id, doc_type):
        return self.doc_slugs_by_item.get((target_item_id, doc_type), [])

    def get_doc_index_row(self, file_hash):
        return self.doc_index_by_hash.get(file_hash)

    def resolve_target_folder(self, target_item_id, file_hash):
        return self.target_folder_result

    def enqueue_upload_attachment(self, file_hash, delivery_item_id, target_folder, rev_number):
        self.upload_enqueued.append((file_hash, delivery_item_id, target_folder, rev_number))


class MockSp:
    def __init__(self):
        self.writes = []
        self._next_id = 5000

    def update_item(self, entity, scope, item_id, canonical_fields):
        self.writes.append(("update", entity, item_id, dict(canonical_fields)))

    def create_item(self, entity, scope, canonical_fields):
        new_id = f"SP-{self._next_id}"
        self._next_id += 1
        self.writes.append(("create", entity, new_id, dict(canonical_fields)))
        return new_id


class MockAudit:
    def __init__(self):
        self.logs = []

    def write_communication_log(self, action_type, delivery_item_id, attribution, details):
        self.logs.append((action_type, delivery_item_id, attribution, details))


def mk_item(state, **kw):
    base = dict(
        delivery_state=state, item_type="test_tech_waiver_report",
        no_customer_upload=False, doc_count=1, doc_count_received=1,
        review_required=False, pm_approval_at=None, prior_delivery_state=None,
        carrier_upload_complete=False, review_status="not_required",
    )
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def deps():
    return TaskDeps(storage=MockStorage(), sp_writer=MockSp(), audit=MockAudit())


@pytest.fixture(autouse=True)
def _eager_celery():
    original = hilda_celery_app.conf.result_backend
    hilda_celery_app.conf.task_always_eager = True
    hilda_celery_app.conf.task_eager_propagates = True
    hilda_celery_app.conf.result_backend = "cache+memory://"
    yield
    hilda_celery_app.conf.task_always_eager = False
    hilda_celery_app.conf.task_eager_propagates = False
    hilda_celery_app.conf.result_backend = original


def ctx(**kw):
    base = dict(
        correlation_id="c-001", pm_id="pm-1", trigger_source="automated",
        delivery_item_id="I-1234", milestone_id="M-1", customer_id="MMK",
        device_id="MODEL-A",
    )
    base.update(kw)
    return base


# ---------------------------------------------------------------------------
# state.py tests
# ---------------------------------------------------------------------------


class TestStateTasks:
    def test_update_state_happy_path(self, deps):
        deps.storage.items["I-1234"] = mk_item(DeliveryState.OPEN)
        with override_task_deps(deps):
            result = update_state_task.apply_async(
                args=({"target_state": "Outreach Sent"}, ctx())
            ).get()
        assert result["outcome"] == "transitioned"
        assert result["to_state"] == "Outreach Sent"

    def test_update_state_idempotent_noop(self, deps):
        deps.storage.items["I-1234"] = mk_item(DeliveryState.OPEN)
        with override_task_deps(deps):
            result = update_state_task.apply_async(
                args=({"target_state": "Open"}, ctx())
            ).get()
        assert result["outcome"] == "no_op_idempotent"

    def test_update_state_requires_delivery_item_id(self, deps):
        with override_task_deps(deps):
            with pytest.raises(Exception, match="delivery_item_id"):
                update_state_task.apply_async(
                    args=({"target_state": "Open"}, ctx(delivery_item_id=None))
                ).get()

    def test_instantiate_default_work_item_happy_path(self, deps):
        with override_task_deps(deps):
            result = instantiate_default_work_item_task.apply_async(
                args=({}, ctx())
            ).get()
        assert result["outcome"] == "instantiated"
        assert result["milestone_id"] == "M-1"
        creates = [w for w in deps.sp_writer.writes if w[0] == "create"]
        assert len(creates) == 1


# ---------------------------------------------------------------------------
# milestone.py tests
# ---------------------------------------------------------------------------


class TestMilestoneTasks:
    def test_milestone_storage_cleanup_stub_logs(self, deps):
        with override_task_deps(deps):
            result = milestone_storage_cleanup_task.apply_async(args=({}, ctx())).get()
        assert result["outcome"] == "logged_stub"
        assert result["milestone_id"] == "M-1"
        assert any(a[0] == "milestone_storage_cleanup_requested" for a in deps.audit.logs)

    def test_halt_milestone_polling_stub(self, deps):
        with override_task_deps(deps):
            result = halt_milestone_polling_task.apply_async(args=({}, ctx())).get()
        assert result["outcome"] == "logged_stub"

    def test_final_sweep_stub(self, deps):
        with override_task_deps(deps):
            result = final_sweep_task.apply_async(args=({}, ctx())).get()
        assert result["outcome"] == "logged_stub"

    def test_close_all_items_no_storage_helper_returns_stub(self, deps):
        # storage exposes list_items_for_milestone but returns empty
        deps.storage.list_items_response = []
        with override_task_deps(deps):
            result = close_all_items_task.apply_async(args=({}, ctx())).get()
        assert result["eligible_count"] == 0
        assert result["closed_count"] == 0
        assert result["outcome"] == "completed"

    def test_close_all_items_iterates_eligible(self, deps):
        # Two CLOSE-eligible items: one SUBMITTED, one READY_FOR_SUBMISSION with no_customer_upload=True
        i1 = mk_item(DeliveryState.SUBMITTED_TO_CUSTOMER, delivery_item_id="I-A")
        i2 = mk_item(
            DeliveryState.READY_FOR_SUBMISSION, no_customer_upload=True,
            pm_approval_at=datetime.now(timezone.utc), delivery_item_id="I-B",
        )
        deps.storage.items["I-A"] = i1
        deps.storage.items["I-B"] = i2
        deps.storage.list_items_response = [i1, i2]
        with override_task_deps(deps):
            result = close_all_items_task.apply_async(args=({}, ctx())).get()
        assert result["eligible_count"] == 2
        assert result["closed_count"] == 2
        assert result["outcome"] == "completed"


# ---------------------------------------------------------------------------
# routing_resolution.py tests
# ---------------------------------------------------------------------------


class TestRoutingResolutionTasks:
    def test_reassign_document_happy_path(self, deps):
        deps.storage.items["D-1"] = SimpleNamespace(
            item_type=ItemType.DEFAULT.value, tpm_reassignment_target_item_id="T-1",
        )
        deps.storage.items["T-1"] = SimpleNamespace(
            item_type=ItemType.TEST_TECH_WAIVER_REPORT.value, no_customer_upload=False,
        )
        deps.storage.doc_index_by_hash["fh-a"] = SimpleNamespace(
            file_hash="fh-a", doc_type=DocType.TEST_REPORT.value, delivery_item_id="D-1",
        )
        deps.storage.target_folder_result = "TestReports/Power"
        with override_task_deps(deps):
            result = reassign_document_to_work_item_task.apply_async(
                args=(
                    {"file_hash": "fh-a", "source_item_id": "D-1", "target_item_id": "T-1",
                     "original_filename": "power.pdf"},
                    ctx(trigger_source="tpm_button"),
                )
            ).get()
        assert result["revision_classification"] == "new_document"
        assert result["target_item_id"] == "T-1"
        assert result["upload_dispatched"] is True

    def test_propagate_tags_happy_path(self, deps):
        deps.storage.items["I-A"] = SimpleNamespace(
            delivery_item_id="I-A", delivery_state=DeliveryState.OPEN,
            customer_id="MMK", tg_name="APPS", item_no=7, item_description=None,
        )
        with override_task_deps(deps):
            result = propagate_tags_to_active_trackers_task.apply_async(
                args=(
                    {"tg_name": "APPS", "item_no": 7,
                     "new_tags": [["Cloud", "Cloud Storage"]]},
                    ctx(customer_id="MMK"),
                )
            ).get()
        assert result["customer_id"] == "MMK"
        assert result["propagated_count"] == 1

    def test_rearm_deadline_proximity_logs(self, deps):
        with override_task_deps(deps):
            result = rearm_deadline_proximity_task.apply_async(args=({}, ctx())).get()
        assert result["outcome"] == "rearmed"
        assert any(a[0] == "deadline_proximity_rearmed" for a in deps.audit.logs)


# ---------------------------------------------------------------------------
# escalation.py tests
# ---------------------------------------------------------------------------


class TestEscalationTasks:
    def test_notify_pm_writes_audit_log(self, deps):
        with override_task_deps(deps):
            result = notify_pm_task.apply_async(
                args=({"urgency": "medium", "reason": "deadline_proximity"}, ctx())
            ).get()
        assert result["outcome"] == "logged"
        notify_logs = [a for a in deps.audit.logs if a[0] == "notify_pm"]
        assert len(notify_logs) == 1
        assert notify_logs[0][3]["urgency"] == "medium"

    def test_notify_hilda_ops_writes_audit_log(self, deps):
        with override_task_deps(deps):
            result = notify_hilda_ops_task.apply_async(
                args=({"severity": "warning", "alert_code": "STR-W001"}, ctx())
            ).get()
        assert result["outcome"] == "logged"
        ops_logs = [a for a in deps.audit.logs if a[0] == "notify_hilda_ops"]
        assert len(ops_logs) == 1
        assert ops_logs[0][3]["alert_code"] == "STR-W001"


# ---------------------------------------------------------------------------
# Registry integration check
# ---------------------------------------------------------------------------


class TestRegistryIntegration:
    def test_state_actions_registered(self):
        assert ActionKind.UPDATE_STATE in ACTION_KIND_TO_TASK
        assert ActionKind.INSTANTIATE_DEFAULT_WORK_ITEM in ACTION_KIND_TO_TASK

    def test_milestone_actions_registered(self):
        assert ActionKind.MILESTONE_STORAGE_CLEANUP in ACTION_KIND_TO_TASK
        assert ActionKind.HALT_MILESTONE_POLLING in ACTION_KIND_TO_TASK
        assert ActionKind.FINAL_SWEEP in ACTION_KIND_TO_TASK

    def test_routing_resolution_actions_registered(self):
        assert ActionKind.REASSIGN_DOCUMENT_TO_WORK_ITEM in ACTION_KIND_TO_TASK
        assert ActionKind.PROPAGATE_TAGS_TO_ACTIVE_TRACKERS in ACTION_KIND_TO_TASK
        assert ActionKind.REARM_DEADLINE_PROXIMITY in ACTION_KIND_TO_TASK

    def test_escalation_actions_registered(self):
        assert ActionKind.NOTIFY_PM in ACTION_KIND_TO_TASK
        assert ActionKind.NOTIFY_HILDA_OPS in ACTION_KIND_TO_TASK

    def test_10_of_18_action_kinds_registered_now(self):
        # state(2) + milestone(3) + routing_resolution(3) + escalation(2) = 10
        # The other 8 (SEND_REMINDER, SEND_INITIAL_OUTREACH, NOTIFY_NEW_OWNER,
        # ESCALATE, TRIGGER_PARSER, TRIGGER_AI_REVIEW, QUEUE_SUBMISSION,
        # START_ITEM_COLLECTION) await downstream modules.
        assert len(ACTION_KIND_TO_TASK) == 10
