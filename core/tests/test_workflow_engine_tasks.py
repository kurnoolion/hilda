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

    def create_delivery_item(self, item):
        """[D-118] strict-boundary: import a Deliverable from SP ADDED alert
        into HILDA local storage. Added 2026-06-26."""
        item_id = getattr(item, "delivery_item_id", None) or (
            f"{getattr(item, 'customer_id', '?')}-"
            f"{getattr(item, 'device_id', '?')}-"
            f"{getattr(item, 'milestone_id', '?')}-"
            f"{getattr(item, 'item_no', '?')}"
        )
        if item_id in self.items:
            raise ValueError(f"delivery_item already exists: {item_id}")
        if not hasattr(item, "delivery_item_id"):
            try:
                item.delivery_item_id = item_id  # type: ignore[attr-defined]
            except (AttributeError, TypeError):
                pass
        self.items[item_id] = item
        self.writes.append(("create", item_id, None))
        return item_id

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
        # Per [D-118] Chunk 5: HILDA no longer writes Default WI to SP.
        # sp_writer should have zero "create" entries; storage carries the
        # local tracker with HILDA-synthesized composite-key id.
        creates = [w for w in deps.sp_writer.writes if w[0] == "create"]
        assert creates == []


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


class _FakeAsyncEmailSender:
    def __init__(self):
        self.sent: list[dict] = []
    async def send(self, to, cc, subject, body, in_reply_to=None):
        self.sent.append({"to": to, "subject": subject})
        return "msg-id-test-001"


class _FakeAsyncMessenger:
    def __init__(self, return_value=True):
        self._rv = return_value
        self.sent: list[tuple[str, str]] = []
    async def send(self, owner_corp_id, message):
        self.sent.append((owner_corp_id, message))
        return self._rv


class _FakeAsyncCustomerAdapter:
    def __init__(self, success=True):
        self._success = success
        self.calls: list[dict] = []
    async def upload_attachment(self, *, device_id, milestone_name, source_dir,
                                target_dir, filename, customer_delivery_info):
        self.calls.append({
            "device_id": device_id, "milestone_name": milestone_name,
            "filename": filename, "target_dir": target_dir,
            "customer_delivery_info": customer_delivery_info,
        })
        return SimpleNamespace(success=self._success, error_code=None if self._success else "CAD-E004")


class TestOutreachTasks:
    """SEND_INITIAL_OUTREACH + SEND_REMINDER + NOTIFY_NEW_OWNER -- Ph-1 wire-up."""

    def test_send_initial_outreach_audit_only_when_no_email_sender(self, deps):
        from core.src.workflow_engine.tasks.outreach import send_initial_outreach_task
        with override_task_deps(deps):
            result = send_initial_outreach_task.apply_async(
                args=({"template": "std_outreach"},
                      ctx(owner_corp_usa_email="alice@corp.example"))
            ).get()
        assert result["outcome"] == "audit_only"
        assert result["message_id"] is None
        logs = [a for a in deps.audit.logs if a[0] == "send_initial_outreach"]
        assert len(logs) == 1
        assert logs[0][3]["send_skipped"] is True

    def test_send_initial_outreach_dispatches_when_email_sender_wired(self):
        email = _FakeAsyncEmailSender()
        d = TaskDeps(
            storage=MockStorage(), sp_writer=MockSp(), audit=MockAudit(),
            email_sender=email,
        )
        from core.src.workflow_engine.tasks.outreach import send_initial_outreach_task
        with override_task_deps(d):
            result = send_initial_outreach_task.apply_async(
                args=({"template": "std_outreach"},
                      ctx(owner_corp_usa_email="alice@corp.example"))
            ).get()
        assert result["outcome"] == "sent"
        assert result["message_id"] == "msg-id-test-001"
        assert len(email.sent) == 1
        assert email.sent[0]["to"] == ["alice@corp.example"]

    def test_send_reminder_includes_count(self):
        email = _FakeAsyncEmailSender()
        d = TaskDeps(
            storage=MockStorage(), sp_writer=MockSp(), audit=MockAudit(),
            email_sender=email,
        )
        from core.src.workflow_engine.tasks.outreach import send_reminder_task
        with override_task_deps(d):
            result = send_reminder_task.apply_async(
                args=({"reminder_count": 2},
                      ctx(owner_corp_usa_email="bob@corp.example"))
            ).get()
        assert result["outcome"] == "sent"
        assert result["reminder_count"] == 2
        assert "#2" in email.sent[0]["subject"]

    def test_notify_new_owner_audit_only_path(self, deps):
        from core.src.workflow_engine.tasks.outreach import notify_new_owner_task
        with override_task_deps(deps):
            result = notify_new_owner_task.apply_async(
                args=({}, ctx(owner_corp_usa_email="newowner@corp.example"))
            ).get()
        assert result["outcome"] == "audit_only"
        logs = [a for a in deps.audit.logs if a[0] == "notify_new_owner"]
        assert len(logs) == 1

    # ---- _resolve_recipient helper precedence tests -- [D-118] Chunk 4 wireup ----

    def test_resolve_recipient_prefers_storage_owner_corp_usa_email(self):
        """Storage-side owner_corp_usa_email beats event_context fallback."""
        email = _FakeAsyncEmailSender()
        storage = MockStorage()
        storage.items["I-1234"] = SimpleNamespace(
            owner_corp_usa_email="storage-usa@corp.example",
            owner_corp_email="storage-fallback@corp.example",
        )
        d = TaskDeps(
            storage=storage, sp_writer=MockSp(), audit=MockAudit(),
            email_sender=email,
        )
        from core.src.workflow_engine.tasks.outreach import send_initial_outreach_task
        with override_task_deps(d):
            send_initial_outreach_task.apply_async(
                args=({}, ctx(owner_corp_usa_email="ctx-fallback@corp.example"))
            ).get()
        assert email.sent[0]["to"] == ["storage-usa@corp.example"]

    def test_resolve_recipient_falls_back_to_owner_corp_email(self):
        """When storage owner_corp_usa_email is None, falls back to owner_corp_email per [D-080]."""
        email = _FakeAsyncEmailSender()
        storage = MockStorage()
        storage.items["I-1234"] = SimpleNamespace(
            owner_corp_usa_email=None,
            owner_corp_email="storage-fallback@corp.example",
        )
        d = TaskDeps(
            storage=storage, sp_writer=MockSp(), audit=MockAudit(),
            email_sender=email,
        )
        from core.src.workflow_engine.tasks.outreach import send_initial_outreach_task
        with override_task_deps(d):
            send_initial_outreach_task.apply_async(args=({}, ctx())).get()
        assert email.sent[0]["to"] == ["storage-fallback@corp.example"]

    def test_resolve_recipient_explicit_param_wins(self):
        """params.recipient pins the value above storage."""
        email = _FakeAsyncEmailSender()
        storage = MockStorage()
        storage.items["I-1234"] = SimpleNamespace(
            owner_corp_usa_email="storage@corp.example",
            owner_corp_email=None,
        )
        d = TaskDeps(
            storage=storage, sp_writer=MockSp(), audit=MockAudit(),
            email_sender=email,
        )
        from core.src.workflow_engine.tasks.outreach import send_initial_outreach_task
        with override_task_deps(d):
            send_initial_outreach_task.apply_async(
                args=({"recipient": "pinned@corp.example"}, ctx())
            ).get()
        assert email.sent[0]["to"] == ["pinned@corp.example"]

    def test_resolve_recipient_audit_only_when_all_sources_empty(self):
        """No params + no storage + no event_context fallback -> audit-only."""
        email = _FakeAsyncEmailSender()
        d = TaskDeps(
            storage=MockStorage(), sp_writer=MockSp(), audit=MockAudit(),
            email_sender=email,
        )
        # storage.items has no I-1234 entry; event_context has no owner_corp_usa_email.
        from core.src.workflow_engine.tasks.outreach import send_initial_outreach_task
        with override_task_deps(d):
            result = send_initial_outreach_task.apply_async(args=({}, ctx())).get()
        assert result["outcome"] == "audit_only"
        assert email.sent == []

    # ---- _record_reminder_attempt FR-10 cadence counter advancement ----

    def test_send_reminder_increments_reminder_count_in_storage(self):
        """Each send_reminder_task call advances item.reminder_count by 1 +
        stamps last_reminder_triggered_at per NFR-21 §5 amendment."""
        email = _FakeAsyncEmailSender()
        storage = MockStorage()
        storage.items["I-1234"] = SimpleNamespace(
            owner_corp_usa_email="owner@corp.example",
            owner_corp_email=None,
            reminder_count=0,
            last_reminder_triggered_at=None,
        )
        d = TaskDeps(
            storage=storage, sp_writer=MockSp(), audit=MockAudit(),
            email_sender=email,
        )
        from core.src.workflow_engine.tasks.outreach import send_reminder_task
        with override_task_deps(d):
            # First reminder
            r1 = send_reminder_task.apply_async(args=({}, ctx())).get()
            # Second reminder (simulating next LastContactThreshold fire)
            r2 = send_reminder_task.apply_async(args=({}, ctx())).get()
        # reminder_count advances 0 -> 1 -> 2 on storage
        assert storage.items["I-1234"].reminder_count == 2
        assert storage.items["I-1234"].last_reminder_triggered_at is not None
        # Task return values reflect the actual cadence number, not the default
        assert r1["reminder_count"] == 1
        assert r2["reminder_count"] == 2
        # Email subjects show the right cadence number
        assert "#1" in email.sent[0]["subject"]
        assert "#2" in email.sent[1]["subject"]

    def test_send_reminder_increments_even_when_audit_only(self):
        """No email_sender wired -> still advances cadence (otherwise unwired
        dev/test setups would loop Rule 2a forever)."""
        storage = MockStorage()
        storage.items["I-1234"] = SimpleNamespace(
            owner_corp_usa_email=None,
            owner_corp_email=None,
            reminder_count=5,
            last_reminder_triggered_at=None,
        )
        d = TaskDeps(storage=storage, sp_writer=MockSp(), audit=MockAudit())
        from core.src.workflow_engine.tasks.outreach import send_reminder_task
        with override_task_deps(d):
            result = send_reminder_task.apply_async(args=({}, ctx())).get()
        assert result["outcome"] == "audit_only"
        assert storage.items["I-1234"].reminder_count == 6  # still advanced

    def test_send_reminder_handles_missing_delivery_item_id_gracefully(self):
        """No delivery_item_id in event_context -> no storage update; falls back
        to params.reminder_count for subject/audit."""
        email = _FakeAsyncEmailSender()
        d = TaskDeps(
            storage=MockStorage(), sp_writer=MockSp(), audit=MockAudit(),
            email_sender=email,
        )
        from core.src.workflow_engine.tasks.outreach import send_reminder_task
        ctx_no_id = ctx()
        ctx_no_id.pop("delivery_item_id", None)
        with override_task_deps(d):
            result = send_reminder_task.apply_async(
                args=({"reminder_count": 3, "recipient": "x@y"}, ctx_no_id)
            ).get()
        # Falls back to params value because storage update skipped
        assert result["reminder_count"] == 3


class TestSubmissionTasks:
    """ESCALATE + START_ITEM_COLLECTION + QUEUE_SUBMISSION -- Ph-1 wire-up."""

    def test_escalate_audit_only_when_no_messenger(self, deps):
        from core.src.workflow_engine.tasks.submission import escalate_task
        with override_task_deps(deps):
            result = escalate_task.apply_async(
                args=({"escalation_reason": "reminder_cadence_exhausted"},
                      ctx(owner_corp_id="alice"))
            ).get()
        assert result["outcome"] == "audit_only"
        assert result["delivered"] is False

    def test_escalate_dispatches_when_messenger_wired(self):
        m = _FakeAsyncMessenger(return_value=True)
        d = TaskDeps(
            storage=MockStorage(), sp_writer=MockSp(), audit=MockAudit(),
            messenger=m,
        )
        from core.src.workflow_engine.tasks.submission import escalate_task
        with override_task_deps(d):
            result = escalate_task.apply_async(
                args=({"escalation_reason": "deadline_proximity"},
                      ctx(owner_corp_id="alice"))
            ).get()
        assert result["outcome"] == "delivered"
        assert m.sent == [("alice", m.sent[0][1])]

    def test_start_item_collection_writes_audit(self, deps):
        from core.src.workflow_engine.tasks.submission import start_item_collection_task
        with override_task_deps(deps):
            result = start_item_collection_task.apply_async(
                args=({}, ctx())
            ).get()
        assert result["outcome"] == "audit_written"
        assert result["target_state"] == "Outreach Sent"
        logs = [a for a in deps.audit.logs if a[0] == "start_item_collection"]
        assert len(logs) == 1

    def test_queue_submission_audit_only_when_no_customer_adapter(self, deps):
        from core.src.workflow_engine.tasks.submission import queue_submission_task
        with override_task_deps(deps):
            result = queue_submission_task.apply_async(
                args=({"source_dir": "/tmp", "filename": "x.pdf",
                       "target_dir": "Submissions", "customer_delivery_info": "drive.google.com"},
                      ctx())
            ).get()
        assert result["outcome"] == "audit_only"
        assert result["upload_success"] is False

    def test_queue_submission_uploads_when_customer_adapter_wired(self):
        ca = _FakeAsyncCustomerAdapter(success=True)
        d = TaskDeps(
            storage=MockStorage(), sp_writer=MockSp(), audit=MockAudit(),
            customer_adapter=ca,
        )
        from core.src.workflow_engine.tasks.submission import queue_submission_task
        with override_task_deps(d):
            result = queue_submission_task.apply_async(
                args=({"source_dir": "/tmp", "filename": "x.pdf",
                       "target_dir": "Submissions",
                       "customer_delivery_info": "drive.google.com"},
                      ctx(device_id="MODEL-A", milestone_name="P1"))
            ).get()
        assert result["outcome"] == "uploaded"
        assert result["upload_success"] is True
        assert len(ca.calls) == 1
        assert ca.calls[0]["device_id"] == "MODEL-A"


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

    def test_sp_alert_imports_actions_registered(self):
        # [D-118] strict-boundary cascade added 2026-06-26:
        # IMPORT_DELIVERABLE_TRACKER + KICKOFF_COLLECTION.
        # Bodies are stubs in Chunk 2 (registry slot reserved); real impls
        # land in Chunks 3 + 4.
        assert ActionKind.IMPORT_DELIVERABLE_TRACKER in ACTION_KIND_TO_TASK
        assert ActionKind.KICKOFF_COLLECTION in ACTION_KIND_TO_TASK

    def test_18_of_20_action_kinds_registered_now(self):
        # state(2) + milestone(3) + routing_resolution(3) + escalation(2) +
        # outreach(3: SEND_INITIAL_OUTREACH, SEND_REMINDER, NOTIFY_NEW_OWNER) +
        # submission(3: ESCALATE, START_ITEM_COLLECTION, QUEUE_SUBMISSION) +
        # sp_alert_imports(2: IMPORT_DELIVERABLE_TRACKER, KICKOFF_COLLECTION
        #                  added 2026-06-26 per [D-118] cascade) = 18.
        # Remaining 2 await downstream module integration:
        # TRIGGER_PARSER + TRIGGER_AI_REVIEW (llm Ph-1 next pass).
        assert len(ACTION_KIND_TO_TASK) == 18

    def test_outreach_actions_registered(self):
        assert ActionKind.SEND_INITIAL_OUTREACH in ACTION_KIND_TO_TASK
        assert ActionKind.SEND_REMINDER in ACTION_KIND_TO_TASK
        assert ActionKind.NOTIFY_NEW_OWNER in ACTION_KIND_TO_TASK

    def test_submission_actions_registered(self):
        assert ActionKind.ESCALATE in ACTION_KIND_TO_TASK
        assert ActionKind.START_ITEM_COLLECTION in ACTION_KIND_TO_TASK
        assert ActionKind.QUEUE_SUBMISSION in ACTION_KIND_TO_TASK


# ===========================================================================
# TestImportDeliverableTracker -- [D-118] Chunk 3
# ===========================================================================


def _mk_import_event_context(
    *,
    sub_trigger: str = "added",
    customer_id: str = "MMK",
    milestone_id: str = "P1",
    body_kvs: dict | None = None,
    item_title: str = "Device Readiness Review",
) -> dict:
    """Build event_context shape that sp_alert_parser + dispatcher would
    produce per [D-118] Chunk 3 plumbing for a Deliverable ADDED alert."""
    default_body = {
        "Title": "Device Readiness Review",
        "carrier": "MMK",
        "project_id": "2350",
        "project_model": "SM-S671U1",
        "milestone_name": "P1",
        "milestone_id": "201",
        "item_no": "5",
        "item_type": "test_tech_waiver_report",
        "delivery_state": "Not Started",
        "owner_name": "Test Owner",
        "owner_corp_email": "owner@corp.example",
        "owner_corp_usa_email": "owner.usa@corp.example",
        "owner_corp_id": "owner_corp_id",
        "tg_name": "MNO-ETM",
        "tracking_modality": "Email",
        "force_tracking_enabled": "Yes",
        "no_customer_upload": "No",
        "review_required": "No",
        "milestone_gating": "Yes",
        "doc_count": "1",
        "sort_order": "5",
    }
    body = body_kvs if body_kvs is not None else default_body
    return {
        "correlation_id": "test-correlation-id",
        "customer_id":    customer_id,
        "milestone_id":   milestone_id,
        "device_id":      None,
        "delivery_item_id": None,
        "trigger":        "ItemModified",
        "sub_trigger":    sub_trigger,
        "timestamp":      "2026-06-27T10:00:00+00:00",
        "derived_fields": {
            "action_type": sub_trigger,
            "list_name":   "Deliverables",
            "item_title":  item_title,
            "body_kvs":    body,
            "routing_key": {
                "project_id":     "2350",
                "milestone_name": milestone_id,
                "item_number":    5,
                "list_suffix":    customer_id,
            },
        },
    }


class TestImportDeliverableTracker:
    """[D-118] Chunk 3: import_deliverable_tracker_task body."""

    def test_happy_path_imports_deliverable(self, deps):
        from core.src.workflow_engine.tasks.sp_alert_imports import (
            import_deliverable_tracker_task,
        )
        with override_task_deps(deps):
            ctx = _mk_import_event_context()
            result = import_deliverable_tracker_task({}, ctx)
        assert result["outcome"] == "imported"
        assert "delivery_item_id" in result
        # Audit log written:
        assert any(
            log[0] == "deliverable_tracker_imported" for log in deps.audit.logs
        )
        # Storage shows new item:
        assert len(deps.storage.items) == 1
        # No SP write (per [D-118]): SP UI engineer owns row creation; HILDA
        # only writes the local tracker, never calls sp_writer.create_item.
        create_calls = [w for w in deps.sp_writer.writes if w[0] == "create"]
        assert create_calls == []

    def test_idempotent_re_import_on_existing(self, deps):
        from core.src.workflow_engine.tasks.sp_alert_imports import (
            import_deliverable_tracker_task,
        )
        # Pre-seed an existing item matching the natural key
        # (customer_id=MMK, tg_name=MNO-ETM, item_no=5):
        existing = SimpleNamespace(
            item_id="MMK-SM-S671U1-P1-5",
            customer_id="MMK",
            tg_name="MNO-ETM",
            item_no=5,
        )
        deps.storage.items["MMK-SM-S671U1-P1-5"] = existing

        with override_task_deps(deps):
            ctx = _mk_import_event_context()
            result = import_deliverable_tracker_task({}, ctx)
        assert result["outcome"] == "already_exists"
        # Storage count unchanged (no fresh create):
        assert len(deps.storage.items) == 1
        # Audit log marks "already_exists":
        assert any(
            log[0] == "deliverable_tracker_already_exists" for log in deps.audit.logs
        )

    def test_skips_non_added_sub_trigger(self, deps):
        from core.src.workflow_engine.tasks.sp_alert_imports import (
            import_deliverable_tracker_task,
        )
        with override_task_deps(deps):
            ctx = _mk_import_event_context(sub_trigger="changed")
            result = import_deliverable_tracker_task({}, ctx)
        assert result["outcome"] == "skipped_non_added"
        assert len(deps.storage.items) == 0
        assert deps.audit.logs == []

    def test_skips_missing_body_kvs(self, deps):
        from core.src.workflow_engine.tasks.sp_alert_imports import (
            import_deliverable_tracker_task,
        )
        with override_task_deps(deps):
            ctx = _mk_import_event_context()
            # Strip body_kvs:
            ctx["derived_fields"]["body_kvs"] = {}
            result = import_deliverable_tracker_task({}, ctx)
        assert result["outcome"] == "skipped_no_body_kvs"
        assert len(deps.storage.items) == 0

    def test_skips_missing_identity(self, deps):
        from core.src.workflow_engine.tasks.sp_alert_imports import (
            import_deliverable_tracker_task,
        )
        with override_task_deps(deps):
            # Body missing project_model AND item_no:
            ctx = _mk_import_event_context(body_kvs={
                "Title": "Some Item",
                "item_type": "test_tech_waiver_report",
                "delivery_state": "Not Started",
                # NOTE: no project_model, no item_no
            })
            result = import_deliverable_tracker_task({}, ctx)
        assert result["outcome"] == "skipped_missing_identity"
        assert len(deps.storage.items) == 0

    def test_critical_field_mapping(self, deps):
        """Verify body_kvs string fields land correctly on DeliveryItemBase."""
        from core.src.workflow_engine.tasks.sp_alert_imports import (
            import_deliverable_tracker_task,
        )
        with override_task_deps(deps):
            ctx = _mk_import_event_context()
            result = import_deliverable_tracker_task({}, ctx)
        assert result["outcome"] == "imported"
        item = deps.storage.items[result["delivery_item_id"]]
        assert item.item_no == 5
        assert item.item_type == "test_tech_waiver_report"
        assert item.owner_corp_email == "owner@corp.example"
        assert item.tg_name == "MNO-ETM"
        assert item.tracking_modality == ["Email"]
        assert item.force_tracking_enabled is True       # "Yes" -> True
        assert item.no_customer_upload is False          # "No" -> False
        assert item.review_required is False
        assert item.milestone_gating is True
        assert item.delivery_state == "Not Started"


# ===========================================================================
# TestKickoffCollection -- [D-118] Chunk 4
# ===========================================================================


class MockDispatcher:
    """Minimal TriggerDispatcher impl for kickoff_collection tests. Records all
    dispatched TriggerEvents for assertion."""

    def __init__(self):
        self.dispatched = []

    def dispatch(self, event):
        self.dispatched.append(event)


def _mk_kickoff_event_context(
    *,
    customer_id: str = "MMK",
    milestone_id: str = "P1",
) -> dict:
    return {
        "correlation_id": "kickoff-correlation-id",
        "customer_id":    customer_id,
        "milestone_id":   milestone_id,
        "device_id":      None,
        "delivery_item_id": None,
        "trigger":        "ItemModified",
        "sub_trigger":    "changed",
        "timestamp":      "2026-06-27T11:00:00+00:00",
        "derived_fields": {
            "action_type": "changed",
            "list_name":   "Milestones",
            "item_title":  "P1",
            "body_kvs":    {"milestone_collection_started_at": "2026-06-27T10:00Z"},
            "routing_key": {"milestone_name": milestone_id, "list_suffix": customer_id},
        },
    }


def _mk_tracker(item_no, item_type, force_tracking_enabled=True, **kw):
    """Build a SimpleNamespace tracker matching the shape kickoff_collection
    expects to read from storage."""
    base = dict(
        item_id=f"MMK-SM-S671U1-P1-{item_no}",
        delivery_item_id=f"MMK-SM-S671U1-P1-{item_no}",
        item_no=item_no,
        item_type=item_type,
        force_tracking_enabled=force_tracking_enabled,
        tg_name=kw.pop("tg_name", "MNO-ETM"),
        device_id=kw.pop("device_id", "SM-S671U1"),
        owner_corp_email=kw.pop("owner_corp_email", "owner@corp.example"),
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestKickoffCollection:
    """[D-118] Chunk 4: kickoff_collection_task body."""

    def test_happy_path_fires_events_for_eligible_items(self, deps):
        """6 trackers (1 Confirmation, 1 force_tracking=False, 4 eligible) ->
        4 ItemCreated events dispatched."""
        from core.src.workflow_engine.tasks.sp_alert_imports import (
            kickoff_collection_task,
        )

        trackers = [
            _mk_tracker(1, "Confirmation", force_tracking_enabled=True),   # excluded -- Confirmation per FR-58
            _mk_tracker(2, "compliance_certification_release_notes", force_tracking_enabled=True),
            _mk_tracker(5, "test_tech_waiver_report", force_tracking_enabled=True),
            _mk_tracker(7, "test_tech_waiver_report", force_tracking_enabled=True),
            _mk_tracker(8, "test_tech_waiver_report", force_tracking_enabled=False),  # excluded -- force_tracking=False
            _mk_tracker(11, "Default", force_tracking_enabled=False),      # excluded -- Default WI per FR-78
        ]
        deps.storage.list_items_response = trackers
        deps_with_dispatcher = TaskDeps(
            storage=deps.storage, sp_writer=deps.sp_writer, audit=deps.audit,
            dispatcher=MockDispatcher(),
        )

        with override_task_deps(deps_with_dispatcher):
            ctx = _mk_kickoff_event_context()
            result = kickoff_collection_task({}, ctx)

        assert result["outcome"] == "fired"
        assert result["events_fired"] == 3       # items 2, 5, 7
        assert result["items_scanned"] == 6
        assert result["items_eligible"] == 3
        # Verify dispatched events:
        dispatched = deps_with_dispatcher.dispatcher.dispatched
        assert len(dispatched) == 3
        for event in dispatched:
            assert event.trigger.value == "ItemCreated"
            assert event.entity_ref.customer_id == "MMK"
            assert event.entity_ref.milestone_id == "P1"

    def test_empty_milestone_fires_zero_events(self, deps):
        """No trackers in storage -> 0 events fired, outcome still 'fired'."""
        from core.src.workflow_engine.tasks.sp_alert_imports import (
            kickoff_collection_task,
        )
        deps.storage.list_items_response = []
        deps_with_dispatcher = TaskDeps(
            storage=deps.storage, sp_writer=deps.sp_writer, audit=deps.audit,
            dispatcher=MockDispatcher(),
        )
        with override_task_deps(deps_with_dispatcher):
            result = kickoff_collection_task({}, _mk_kickoff_event_context())
        assert result["outcome"] == "fired"
        assert result["events_fired"] == 0
        assert result["items_scanned"] == 0

    def test_all_confirmation_fires_zero_events(self, deps):
        """All trackers are Confirmation -> 0 events fired (all filtered per FR-58)."""
        from core.src.workflow_engine.tasks.sp_alert_imports import (
            kickoff_collection_task,
        )
        deps.storage.list_items_response = [
            _mk_tracker(1, "Confirmation"),
            _mk_tracker(2, "Confirmation"),
        ]
        deps_with_dispatcher = TaskDeps(
            storage=deps.storage, sp_writer=deps.sp_writer, audit=deps.audit,
            dispatcher=MockDispatcher(),
        )
        with override_task_deps(deps_with_dispatcher):
            result = kickoff_collection_task({}, _mk_kickoff_event_context())
        assert result["outcome"] == "fired"
        assert result["events_fired"] == 0
        assert result["items_scanned"] == 2
        assert result["items_eligible"] == 0

    def test_skips_when_dispatcher_missing(self, deps):
        """deps.dispatcher is None (worker not wired) -> skipped outcome."""
        from core.src.workflow_engine.tasks.sp_alert_imports import (
            kickoff_collection_task,
        )
        # deps.dispatcher is None by default
        with override_task_deps(deps):
            result = kickoff_collection_task({}, _mk_kickoff_event_context())
        assert result["outcome"] == "skipped_no_dispatcher"
        assert result["events_fired"] == 0
