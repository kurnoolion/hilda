"""tracker test suite — state machine + guards + transitions + default WI
+ reassignment + tag propagation + manual override + CLI.

Tests are organized by sub-module. Mocks live at the top; each class group
is a pytest TestSomething wrapper for grouping in test output.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from core.src.diagnostics.error_codes import PipelineError
from core.src.template_schema import DocType, ItemType
from core.src.tracker import (
    DeliveryState,
    GuardResult,
    LEGAL_TRANSITIONS,
    ReassignmentResult,
    TagPropagationResult,
    TransitionResult,
    advance_on_doc_count_reached,
    apply_manual_tpm_override,
    auto_advance_owner_closed_to_under_pm_review,
    check_transition_guards,
    instantiate_default_workitem,
    list_blocking_conditions_for_state,
    propagate_tags_to_active_trackers,
    reassign_document_to_workitem,
    transition_legal,
    update_delivery_state,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class MockStorage:
    def __init__(self):
        self.items = {}
        self.writes = []
        self.assoc_updates = []
        self.doc_index_by_hash = {}
        self.doc_slugs_by_item = {}
        self.upload_enqueued = []
        self.default_wi_by_milestone = {}
        self.rederive_result = None
        self.target_folder_result = None

    def get_delivery_item(self, id):
        return self.items[id]

    def create_delivery_item(self, item):
        """[D-118] strict-boundary: import a Deliverable from SP ADDED alert
        into HILDA local storage. Added 2026-06-26."""
        # Synthesize a delivery_item_id from composite key per [D-091]
        item_id = getattr(item, "delivery_item_id", None) or (
            f"{getattr(item, 'customer_id', '?')}-"
            f"{getattr(item, 'device_id', '?')}-"
            f"{getattr(item, 'milestone_id', '?')}-"
            f"{getattr(item, 'item_no', '?')}"
        )
        if item_id in self.items:
            raise ValueError(f"delivery_item already exists: {item_id}")
        # Allow duck-typed SimpleNamespace OR DeliveryItemBase
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
        self.writes.append(("update", id, dict(fields)))

    def update_document_item_association(self, file_hash, fields):
        self.assoc_updates.append((file_hash, dict(fields)))

    def update_document_index_row(self, file_hash, fields):
        if file_hash in self.doc_index_by_hash:
            for k, v in fields.items():
                setattr(self.doc_index_by_hash[file_hash], k, v)

    def find_items_by_natural_key(self, customer_id, tg_name, item_no, only_active=True):
        return [
            i for i in self.items.values()
            if getattr(i, "customer_id", None) == customer_id
            and getattr(i, "tg_name", None) == tg_name
            and getattr(i, "item_no", None) == item_no
        ]

    def list_default_workitem_for_milestone(self, milestone_id):
        return self.default_wi_by_milestone.get(milestone_id)

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

    def rederive_doc_type_for_file(self, file_hash, target_item_id):
        return self.rederive_result

    def resolve_target_folder(self, target_item_id, file_hash):
        return self.target_folder_result

    def enqueue_upload_attachment(self, file_hash, delivery_item_id, target_folder, rev_number):
        self.upload_enqueued.append((file_hash, delivery_item_id, target_folder, rev_number))


class MockSp:
    def __init__(self):
        self.writes = []
        self._next_id = 1000
        # Per-call lookup results keyed by frozenset(filters.items()).
        # Default: empty list -> caller treats as "no SP row found" and skips
        # writeback. Tests that want to exercise the SP-writeback path seed
        # this dict explicitly. Phase B 2026-06-28: tracker.transitions
        # _sp_writeback_field_updates calls get_items() to resolve _sp_id
        # before update_item.
        self.get_items_responses: dict = {}

    def update_item(self, entity, scope, item_id, canonical_fields):
        self.writes.append(("update", entity, item_id, dict(canonical_fields)))

    def create_item(self, entity, scope, canonical_fields):
        new_id = f"SP-{self._next_id}"
        self._next_id += 1
        self.writes.append(("create", entity, new_id, dict(canonical_fields)))
        return new_id

    def get_items(self, entity, scope, canonical_filters=None):
        key = frozenset((canonical_filters or {}).items())
        return self.get_items_responses.get(key, [])


class MockAudit:
    def __init__(self):
        self.logs = []

    def write_communication_log(self, action_type, delivery_item_id, attribution, details):
        self.logs.append((action_type, delivery_item_id, attribution, details))


def mk_item(state, **kw):
    base = dict(
        delivery_state=state,
        item_type=ItemType.TEST_TECH_WAIVER_REPORT.value,
        no_customer_upload=False,
        doc_count=1,
        doc_count_received=1,
        review_required=False,
        pm_approval_at=None,
        prior_delivery_state=None,
        carrier_upload_complete=False,
        review_status="not_required",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def ctx(**kw):
    base = dict(correlation_id="c-001", pm_id="pm-1", trigger_source="automated")
    base.update(kw)
    return base


@pytest.fixture
def writers():
    return MockStorage(), MockSp(), MockAudit()


# ---------------------------------------------------------------------------
# state_machine tests
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_11_states(self):
        assert len(DeliveryState) == 11

    def test_legal_transitions_keys_match_states(self):
        assert set(LEGAL_TRANSITIONS.keys()) == set(DeliveryState)

    def test_closed_terminal(self):
        assert LEGAL_TRANSITIONS[DeliveryState.CLOSED] == frozenset()

    def test_open_no_offpath(self):
        # Owner cannot report Delayed/Blocked before receiving outreach.
        assert DeliveryState.DELAYED not in LEGAL_TRANSITIONS[DeliveryState.OPEN]
        assert DeliveryState.BLOCKED not in LEGAL_TRANSITIONS[DeliveryState.OPEN]

    def test_delayed_blocked_no_direct_swap(self):
        assert DeliveryState.BLOCKED not in LEGAL_TRANSITIONS[DeliveryState.DELAYED]
        assert DeliveryState.DELAYED not in LEGAL_TRANSITIONS[DeliveryState.BLOCKED]

    def test_owner_closed_transient_target_is_under_pm_review(self):
        assert LEGAL_TRANSITIONS[DeliveryState.OWNER_CLOSED] == frozenset({DeliveryState.UNDER_PM_REVIEW})

    def test_submitted_rewind_targets(self):
        assert LEGAL_TRANSITIONS[DeliveryState.SUBMITTED_TO_CUSTOMER] == frozenset({
            DeliveryState.CLOSED,
            DeliveryState.DOCUMENT_RECEIVED,
            DeliveryState.OUTREACH_SENT,
        })

    def test_transition_legal_idempotent_noop(self):
        for s in DeliveryState:
            assert transition_legal(s, s)

    def test_transition_legal_closed_terminal(self):
        for s in DeliveryState:
            if s != DeliveryState.CLOSED:
                assert not transition_legal(DeliveryState.CLOSED, s)

    def test_total_transitions(self):
        # Sanity: 28 transitions excluding idempotent no-ops.
        total = sum(len(targets) for targets in LEGAL_TRANSITIONS.values())
        assert total == 28


# ---------------------------------------------------------------------------
# guards tests
# ---------------------------------------------------------------------------


class TestGuards:
    def test_legal_transition_allowed(self):
        r = check_transition_guards(mk_item(DeliveryState.OPEN), DeliveryState.OUTREACH_SENT)
        assert r.allowed
        assert not r.blocking_conditions

    def test_illegal_transition_blocked(self):
        r = check_transition_guards(mk_item(DeliveryState.CLOSED), DeliveryState.OPEN)
        assert not r.allowed
        assert r.reason == "illegal_transition"

    def test_owner_closed_doc_count_blocks(self):
        r = check_transition_guards(
            mk_item(DeliveryState.OUTREACH_SENT, doc_count=3, doc_count_received=1),
            DeliveryState.OWNER_CLOSED,
        )
        assert not r.allowed
        assert "doc_count_not_reached" in r.blocking_conditions

    def test_owner_closed_confirmation_carveout(self):
        # Confirmation items bypass doc_count check per FR-7 + FR-25 (b)
        r = check_transition_guards(
            mk_item(
                DeliveryState.OUTREACH_SENT,
                item_type=ItemType.CONFIRMATION.value,
                doc_count=3,
                doc_count_received=0,
            ),
            DeliveryState.OWNER_CLOSED,
        )
        assert r.allowed

    def test_owner_closed_reviews_pending_blocks(self):
        r = check_transition_guards(
            mk_item(
                DeliveryState.DOCUMENT_RECEIVED,
                doc_count=1,
                doc_count_received=1,
                review_required=True,
                review_status="pending",
            ),
            DeliveryState.OWNER_CLOSED,
        )
        assert not r.allowed
        assert "reviews_pending" in r.blocking_conditions

    def test_pm_approval_required_for_ready_for_submission(self):
        r = check_transition_guards(
            mk_item(DeliveryState.UNDER_PM_REVIEW),
            DeliveryState.READY_FOR_SUBMISSION,
        )
        assert not r.allowed
        assert "pm_approval_required" in r.blocking_conditions

    def test_pm_approval_present_allows_ready_for_submission(self):
        r = check_transition_guards(
            mk_item(
                DeliveryState.UNDER_PM_REVIEW,
                pm_approval_at=datetime.now(timezone.utc),
            ),
            DeliveryState.READY_FOR_SUBMISSION,
        )
        assert r.allowed

    def test_closed_requires_tpm_attribution(self):
        r = check_transition_guards(
            mk_item(DeliveryState.SUBMITTED_TO_CUSTOMER),
            DeliveryState.CLOSED,
            trigger_source="automated",
        )
        assert not r.allowed
        assert r.reason == "closed_requires_tpm_attribution"

    def test_closed_tpm_button_allowed(self):
        r = check_transition_guards(
            mk_item(DeliveryState.SUBMITTED_TO_CUSTOMER),
            DeliveryState.CLOSED,
            trigger_source="tpm_button",
        )
        assert r.allowed

    def test_ready_to_closed_only_for_no_customer_upload(self):
        # no_customer_upload=False: direct close blocked
        r = check_transition_guards(
            mk_item(DeliveryState.READY_FOR_SUBMISSION, no_customer_upload=False),
            DeliveryState.CLOSED,
            trigger_source="tpm_button",
        )
        assert not r.allowed
        assert "no_customer_upload_false_blocks_direct_close" in r.blocking_conditions

        # no_customer_upload=True: direct close allowed
        r2 = check_transition_guards(
            mk_item(DeliveryState.READY_FOR_SUBMISSION, no_customer_upload=True),
            DeliveryState.CLOSED,
            trigger_source="tpm_button",
        )
        assert r2.allowed

    def test_submitted_rewind_requires_tpm(self):
        r = check_transition_guards(
            mk_item(DeliveryState.SUBMITTED_TO_CUSTOMER),
            DeliveryState.OUTREACH_SENT,
            trigger_source="automated",
        )
        assert not r.allowed
        assert r.reason == "rewind_requires_tpm_attribution"

    def test_bypass_guards_only_legality_check(self):
        # Even without pm_approval_at, bypass_guards skips the guard.
        r = check_transition_guards(
            mk_item(DeliveryState.UNDER_PM_REVIEW),
            DeliveryState.READY_FOR_SUBMISSION,
            bypass_guards=True,
        )
        assert r.allowed
        assert r.reason == "bypass_guards_manual_tpm_override"

    def test_bypass_guards_still_blocks_illegal(self):
        # Structural legality still enforced under bypass.
        r = check_transition_guards(
            mk_item(DeliveryState.CLOSED),
            DeliveryState.OPEN,
            bypass_guards=True,
        )
        assert not r.allowed

    def test_trk_w008_delayed_resume_mismatch_warning(self):
        # Automated resume to a state different from prior_delivery_state -> warning
        item = mk_item(
            DeliveryState.DELAYED,
            prior_delivery_state=DeliveryState.OUTREACH_SENT,
        )
        r = check_transition_guards(item, DeliveryState.UNDER_PM_REVIEW)
        assert r.allowed   # non-blocking warning
        assert any("TRK-W008" in w for w in r.warnings)

    def test_trk_w008_tpm_override_no_warning(self):
        item = mk_item(
            DeliveryState.DELAYED,
            prior_delivery_state=DeliveryState.OUTREACH_SENT,
        )
        r = check_transition_guards(
            item,
            DeliveryState.UNDER_PM_REVIEW,
            trigger_source="manual_tpm_override",
        )
        assert r.allowed
        assert not any("TRK-W008" in w for w in r.warnings)

    def test_submitted_to_customer_requires_upload_complete(self):
        r = check_transition_guards(
            mk_item(DeliveryState.READY_FOR_SUBMISSION, pm_approval_at=datetime.now(timezone.utc),
                    carrier_upload_complete=False),
            DeliveryState.SUBMITTED_TO_CUSTOMER,
        )
        assert not r.allowed
        assert "carrier_upload_incomplete" in r.blocking_conditions

    def test_list_blocking_conditions_convenience_wrapper(self):
        conds = list_blocking_conditions_for_state(
            mk_item(DeliveryState.UNDER_PM_REVIEW),
            DeliveryState.READY_FOR_SUBMISSION,
        )
        assert "pm_approval_required" in conds


# ---------------------------------------------------------------------------
# transitions tests
# ---------------------------------------------------------------------------


class TestTransitions:
    def test_happy_path_open_to_outreach_sent(self, writers):
        storage, sp, audit = writers
        storage.items["I-1"] = mk_item(DeliveryState.OPEN)
        r = update_delivery_state("I-1", DeliveryState.OUTREACH_SENT, {}, ctx(),
                                  storage, sp, audit)
        assert r.outcome == "transitioned"
        assert r.dispatch_signal is not None
        assert any(w[0] == "state" and w[2] == "Outreach Sent" for w in storage.writes)

    def test_idempotent_noop(self, writers):
        storage, sp, audit = writers
        storage.items["I-2"] = mk_item(DeliveryState.OPEN)
        r = update_delivery_state("I-2", DeliveryState.OPEN, {}, ctx(), storage, sp, audit)
        assert r.outcome == "no_op_idempotent"
        assert r.dispatch_signal is None
        assert any(a[0] == "state_transition_idempotent_noop" for a in audit.logs)

    def test_guard_denied(self, writers):
        storage, sp, audit = writers
        storage.items["I-3"] = mk_item(DeliveryState.UNDER_PM_REVIEW)
        r = update_delivery_state("I-3", DeliveryState.READY_FOR_SUBMISSION, {},
                                  ctx(), storage, sp, audit)
        assert r.outcome == "guard_denied"
        assert "pm_approval_required" in r.guard_result.blocking_conditions

    def test_illegal_transition(self, writers):
        storage, sp, audit = writers
        storage.items["I-3b"] = mk_item(DeliveryState.CLOSED)
        r = update_delivery_state("I-3b", DeliveryState.OPEN, {}, ctx(), storage, sp, audit)
        assert r.outcome == "illegal_transition"

    def test_owner_closed_inline_auto_advance(self, writers):
        storage, sp, audit = writers
        storage.items["I-4"] = mk_item(DeliveryState.DOCUMENT_RECEIVED)
        r = update_delivery_state("I-4", DeliveryState.OWNER_CLOSED, {}, ctx(),
                                  storage, sp, audit)
        assert r.outcome == "transitioned"
        assert r.to_state == DeliveryState.UNDER_PM_REVIEW
        state_writes = [w for w in storage.writes if w[0] == "state"]
        assert [w[2] for w in state_writes] == ["Owner Closed", "Under PM Review"]

    def test_prior_delivery_state_set_on_delayed_entry(self, writers):
        storage, sp, audit = writers
        storage.items["I-5"] = mk_item(DeliveryState.OUTREACH_SENT)
        update_delivery_state("I-5", DeliveryState.DELAYED, {}, ctx(), storage, sp, audit)
        update_writes = [w for w in storage.writes if w[0] == "update"]
        assert any(w[2].get("prior_delivery_state") == "Outreach Sent" for w in update_writes)

    def test_prior_delivery_state_cleared_on_resume(self, writers):
        storage, sp, audit = writers
        storage.items["I-6"] = mk_item(
            DeliveryState.DELAYED, prior_delivery_state=DeliveryState.OUTREACH_SENT,
        )
        update_delivery_state(
            "I-6", DeliveryState.OUTREACH_SENT, {},
            ctx(trigger_source="manual_tpm_override"), storage, sp, audit,
        )
        update_writes = [w for w in storage.writes if w[0] == "update"]
        assert any(
            "prior_delivery_state" in w[2] and w[2]["prior_delivery_state"] is None
            for w in update_writes
        )

    def test_pm_approval_cleared_on_under_pm_review_entry(self, writers):
        storage, sp, audit = writers
        storage.items["I-7"] = mk_item(
            DeliveryState.DOCUMENT_RECEIVED,
            pm_approval_at=datetime.now(timezone.utc),
            pm_approval_pm_id="pm-stale",
        )
        # OWNER_CLOSED auto-advances to UNDER_PM_REVIEW, which clears approval.
        update_delivery_state("I-7", DeliveryState.OWNER_CLOSED, {}, ctx(),
                              storage, sp, audit)
        update_writes = [w for w in storage.writes if w[0] == "update"]
        upm_writes = [w for w in update_writes if w[2].get("delivery_state") == "Under PM Review"]
        assert upm_writes
        assert upm_writes[0][2].get("pm_approval_at") is None
        assert upm_writes[0][2].get("pm_approval_pm_id") is None

    def test_sp_writeback_two_step_natural_key_lookup(self, writers):
        """[D-137] / architect 2026-06-28: HILDA-driven state transitions must
        reflect back to SP via natural-key -> _sp_id lookup + update_item.
        Without this fix, SP UI engineer's button-visibility logic (which
        depends on delivery_state being current in SP) breaks for all
        HILDA-driven transitions (NS->Open->Outreach Sent->Owner Closed->
        Under PM Review)."""
        storage, sp, audit = writers
        storage.items["MMK-DEV-MS-9"] = mk_item(
            DeliveryState.OPEN,
            customer_id="MMK", device_id="DEV", milestone_id="MS", item_no=9,
        )
        # Seed MockSp.get_items to return an SP row with _sp_id=42 when
        # asked for natural key (item_no=9, milestone_id=MS, project_model=DEV).
        sp.get_items_responses[frozenset({
            ("item_no", 9), ("milestone_id", "MS"), ("project_model", "DEV"),
        })] = [{"_sp_id": 42, "item_no": 9}]

        update_delivery_state(
            "MMK-DEV-MS-9", DeliveryState.OUTREACH_SENT, {},
            ctx(), storage, sp, audit,
        )
        sp_updates = [w for w in sp.writes if w[0] == "update"]
        assert sp_updates, "expected SP update_item to be called"
        # update_item called with the SP integer Id, NOT HILDA's composite slug
        assert sp_updates[0][2] == "42"
        assert sp_updates[0][3].get("delivery_state") == "Outreach Sent"

    def test_sp_writeback_skips_when_no_sp_row_matches(self, writers):
        """Natural-key lookup returns []; writeback skipped, Postgres write
        still proceeds. Best-effort per [D-118]."""
        storage, sp, audit = writers
        storage.items["UNKNOWN-1"] = mk_item(
            DeliveryState.OPEN,
            customer_id="MMK", device_id="DEV", milestone_id="MS", item_no=99,
        )
        # MockSp.get_items_responses left empty -> returns [] for all keys.
        r = update_delivery_state(
            "UNKNOWN-1", DeliveryState.OUTREACH_SENT, {},
            ctx(), storage, sp, audit,
        )
        # Postgres transition succeeded
        assert r.outcome == "transitioned"
        # SP update_item NOT called
        sp_updates = [w for w in sp.writes if w[0] == "update"]
        assert not sp_updates

    def test_sp_writeback_skips_when_customer_id_missing(self, writers):
        """Item without customer_id -> writeback skipped, Postgres still works.
        Defensive against legacy rows pre-customer_id-stamping."""
        storage, sp, audit = writers
        storage.items["LEGACY-1"] = mk_item(
            DeliveryState.OPEN, customer_id=None,
        )
        r = update_delivery_state(
            "LEGACY-1", DeliveryState.OUTREACH_SENT, {},
            ctx(), storage, sp, audit,
        )
        assert r.outcome == "transitioned"
        sp_updates = [w for w in sp.writes if w[0] == "update"]
        assert not sp_updates

    def test_bypass_guards_with_wrong_trigger_source_raises_e004(self, writers):
        storage, sp, audit = writers
        storage.items["I-8"] = mk_item(DeliveryState.UNDER_PM_REVIEW)
        with pytest.raises(PipelineError) as exc:
            update_delivery_state(
                "I-8", DeliveryState.READY_FOR_SUBMISSION, {},
                ctx(trigger_source="automated"), storage, sp, audit, bypass_guards=True,
            )
        assert exc.value.code_id == "TRK-E004"

    def test_advance_on_doc_count_reached_wrapper(self, writers):
        storage, sp, audit = writers
        storage.items["I-9"] = mk_item(DeliveryState.OUTREACH_SENT)
        r = advance_on_doc_count_reached("I-9", storage, sp, audit, ctx())
        assert r.outcome == "transitioned"
        assert r.to_state == DeliveryState.DOCUMENT_RECEIVED


# ---------------------------------------------------------------------------
# default_workitem tests
# ---------------------------------------------------------------------------


class TestDefaultWorkitem:
    def test_creates_with_fr78_inventory(self, writers):
        storage, sp, audit = writers
        instantiate_default_workitem("M-1", "MMK", "MODEL-A", storage, sp, audit,
                                     event_context=ctx())
        # Per [D-118] Chunk 5: SP UI engineer owns SP row creation; HILDA
        # only writes the local tracker. sp.writes should be empty for
        # creates (no sp_writer.create_item call).
        creates = [w for w in sp.writes if w[0] == "create"]
        assert creates == []
        # Storage now carries the row with HILDA-synthesized composite-key id.
        expected_id = "MMK-MODEL-A-M-1-default"
        assert expected_id in storage.items
        item = storage.items[expected_id]
        assert item.item_type == "Default"
        assert item.tg_name == "_unrouted"
        assert item.tg_path_id == "_unrouted"
        assert item.item_path_id is None
        assert item.item_no == 0
        assert item.delivery_state == "Open"
        assert item.no_customer_upload is True
        assert item.force_tracking_enabled is False
        assert item.milestone_gating is True
        assert item.review_required is False
        assert item.doc_count == 0
        assert item.ingress_nsd == "None"
        assert item.owner_corp_id is None
        assert item.owner_corp_usa_email is None
        assert item.customer_id == "MMK"
        assert item.device_id == "MODEL-A"
        assert item.milestone_id == "M-1"

    def test_idempotent_when_already_exists(self, writers):
        storage, sp, audit = writers
        existing = SimpleNamespace(delivery_item_id="SP-existing", item_type="Default")
        storage.items["SP-existing"] = existing
        storage.default_wi_by_milestone["M-2"] = existing
        result = instantiate_default_workitem("M-2", "MMK", "MODEL-A",
                                              storage, sp, audit, event_context=ctx())
        assert result is existing
        assert not any(w[0] == "create" for w in sp.writes)
        assert any(a[0] == "default_workitem_reused" and a[3].get("code") == "TRK-W004"
                   for a in audit.logs)


# ---------------------------------------------------------------------------
# reassignment tests
# ---------------------------------------------------------------------------


class TestReassignment:
    def _setup(self, writers, *, target_item_type=ItemType.TEST_TECH_WAIVER_REPORT.value,
               file_hash="fh-a", doc_type=DocType.TEST_REPORT,
               default_id="D-1", target_id="T-1"):
        storage, sp, audit = writers
        storage.items[default_id] = SimpleNamespace(
            item_type=ItemType.DEFAULT.value, tpm_reassignment_target_item_id=target_id,
        )
        storage.items[target_id] = SimpleNamespace(
            item_type=target_item_type, no_customer_upload=False,
        )
        storage.doc_index_by_hash[file_hash] = SimpleNamespace(
            file_hash=file_hash, doc_type=doc_type.value, delivery_item_id=default_id,
        )
        storage.target_folder_result = "TestReports/Power"
        return storage, sp, audit

    def test_happy_path_new_document(self, writers):
        storage, sp, audit = self._setup(writers)
        r = reassign_document_to_workitem(
            file_hash="fh-a", source_item_id="D-1", target_item_id="T-1", pm_id="pm-1",
            storage=storage, sp_writer=sp, audit=audit,
            event_context=ctx(trigger_source="tpm_button"),
            original_filename="report-a.pdf",
        )
        assert r.revision_classification == "new_document"
        assert r.rev_number == 1
        assert r.fr77_re_routed is True
        assert r.upload_dispatched is True
        assert len(storage.upload_enqueued) == 1

    def test_e002_source_not_default(self, writers):
        storage, sp, audit = writers
        storage.items["NOT-DEF"] = SimpleNamespace(item_type=ItemType.TEST_TECH_WAIVER_REPORT.value)
        storage.items["T"] = SimpleNamespace(item_type=ItemType.TEST_TECH_WAIVER_REPORT.value)
        with pytest.raises(PipelineError) as exc:
            reassign_document_to_workitem(
                file_hash="fh", source_item_id="NOT-DEF", target_item_id="T", pm_id="pm-1",
                storage=storage, sp_writer=sp, audit=audit, event_context=ctx(),
            )
        assert exc.value.code_id == "TRK-E002"

    def test_e003_fr86_alignment_violation(self, writers):
        storage, sp, audit = self._setup(
            writers, target_item_type=ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
            doc_type=DocType.TEST_REPORT,
        )
        with pytest.raises(PipelineError) as exc:
            reassign_document_to_workitem(
                file_hash="fh-a", source_item_id="D-1", target_item_id="T-1", pm_id="pm-1",
                storage=storage, sp_writer=sp, audit=audit, event_context=ctx(),
            )
        assert exc.value.code_id == "TRK-E003"

    def test_d039_step_0_hash_dedup(self, writers):
        storage, sp, audit = writers
        storage.items["D"] = SimpleNamespace(item_type=ItemType.DEFAULT.value)
        storage.items["T"] = SimpleNamespace(item_type=ItemType.TEST_TECH_WAIVER_REPORT.value,
                                             no_customer_upload=False)
        # Pre-existing row on the TARGET with matching hash + doc_type -> dedup.
        storage.doc_index_by_hash["fh"] = SimpleNamespace(
            file_hash="fh", doc_type=DocType.TEST_REPORT.value, delivery_item_id="T",
            doc_id_slug="existing-slug", rev_number=2,
        )
        storage.target_folder_result = "TestReports/Power"
        r = reassign_document_to_workitem(
            file_hash="fh", source_item_id="D", target_item_id="T", pm_id="pm-1",
            storage=storage, sp_writer=sp, audit=audit, event_context=ctx(),
            original_filename="dup.pdf",
        )
        assert r.revision_classification == "duplicate_skipped"
        assert r.rev_number == 2
        assert r.upload_dispatched is False

    def test_d039_step_1_slug_match_revision(self, writers):
        storage, sp, audit = self._setup(writers)
        storage.doc_slugs_by_item[("T-1", DocType.TEST_REPORT)] = [("power_report", 1)]
        r = reassign_document_to_workitem(
            file_hash="fh-a", source_item_id="D-1", target_item_id="T-1", pm_id="pm-1",
            storage=storage, sp_writer=sp, audit=audit, event_context=ctx(),
            original_filename="power_report.pdf",
        )
        assert r.revision_classification == "revision_of"
        assert r.revision_of_doc_id_slug == "power_report"
        assert r.rev_number == 2

    def test_trk_w007_ambiguous_slug_ph2_deferral(self, writers):
        storage, sp, audit = self._setup(writers)
        storage.doc_slugs_by_item[("T-1", DocType.TEST_REPORT)] = [
            ("slug-x", 1), ("slug-y", 2),
        ]
        r = reassign_document_to_workitem(
            file_hash="fh-a", source_item_id="D-1", target_item_id="T-1", pm_id="pm-1",
            storage=storage, sp_writer=sp, audit=audit, event_context=ctx(),
            original_filename="ambiguous.pdf",
        )
        assert r.revision_classification == "new_document"
        # W007 warning in audit row details
        log = [a for a in audit.logs if a[0] == "tpm_reassign_to_workitem"][0]
        assert any("TRK-W007" in w for w in log[3].get("warnings", []))


# ---------------------------------------------------------------------------
# tag_propagation tests
# ---------------------------------------------------------------------------


class TestTagPropagation:
    def _mk_active_items(self, storage):
        for iid, state in [
            ("I-A", DeliveryState.OPEN),
            ("I-B", DeliveryState.OUTREACH_SENT),
            ("I-C", DeliveryState.SUBMITTED_TO_CUSTOMER),
        ]:
            storage.items[iid] = SimpleNamespace(
                delivery_item_id=iid, delivery_state=state,
                customer_id="MMK", tg_name="APPS", item_no=7,
                item_description=None,
            )

    def test_propagates_to_active_skips_terminal(self, writers):
        storage, sp, audit = writers
        self._mk_active_items(storage)
        r = propagate_tags_to_active_trackers(
            customer_id="MMK", tg_name="APPS", item_no=7,
            new_tags=[["Cloud", "Cloud Storage"], ["Backup"]],
            pm_id="pm-1", storage=storage, sp_writer=sp, audit=audit,
            event_context=ctx(trigger_source="automated"),
        )
        assert r.propagated_count == 2
        assert r.skipped_count == 1
        assert "I-C" in r.skipped_item_ids
        assert any(a[0] == "tag_propagation_skipped_terminal" for a in audit.logs)

    def test_idempotent_case_insensitive(self, writers):
        storage, sp, audit = writers
        self._mk_active_items(storage)
        propagate_tags_to_active_trackers(
            customer_id="MMK", tg_name="APPS", item_no=7,
            new_tags=[["Cloud", "Cloud Storage"], ["Backup"]],
            pm_id="pm-1", storage=storage, sp_writer=sp, audit=audit,
            event_context=ctx(trigger_source="automated"),
        )
        r2 = propagate_tags_to_active_trackers(
            customer_id="MMK", tg_name="APPS", item_no=7,
            new_tags=[["Cloud", "cloud storage"], ["backup"]],   # different case
            pm_id="pm-1", storage=storage, sp_writer=sp, audit=audit,
            event_context=ctx(trigger_source="automated"),
        )
        assert r2.propagated_count == 0   # idempotent re-run

    def test_dedup_within_and_across_groups(self, writers):
        storage, sp, audit = writers
        storage.items["I-D"] = SimpleNamespace(
            delivery_item_id="I-D", delivery_state=DeliveryState.OPEN,
            customer_id="MMK", tg_name="APPS", item_no=7,
            item_description=None,
        )
        r = propagate_tags_to_active_trackers(
            customer_id="MMK", tg_name="APPS", item_no=7,
            new_tags=[["Cloud", "cloud", "  cloud  "], ["Cloud", "cloud"], ["Backup"]],
            pm_id="pm-1", storage=storage, sp_writer=sp, audit=audit,
            event_context=ctx(trigger_source="automated"),
        )
        # After dedup: synonym group "Cloud" dedup'd to 1 tag; outer dedup removes second
        # identical group -> 2 unique groups total.
        assert len(r.new_tags) == 2


# ---------------------------------------------------------------------------
# manual_override tests
# ---------------------------------------------------------------------------


class TestManualOverride:
    def test_delivery_state_routed_through_bypass_guards(self, writers):
        storage, sp, audit = writers
        storage.items["I-M1"] = mk_item(DeliveryState.UNDER_PM_REVIEW)
        results = apply_manual_tpm_override(
            "I-M1",
            field_deltas={
                "delivery_state": (
                    DeliveryState.UNDER_PM_REVIEW, DeliveryState.READY_FOR_SUBMISSION,
                ),
            },
            pm_id="pm-1", storage=storage, sp_writer=sp, audit=audit, event_context=ctx(),
        )
        assert len(results) == 1
        assert results[0].outcome == "transitioned"
        assert results[0].to_state == DeliveryState.READY_FOR_SUBMISSION

    def test_non_state_fields_batch_write(self, writers):
        storage, sp, audit = writers
        storage.items["I-M2"] = SimpleNamespace(delivery_state=DeliveryState.OPEN)
        results = apply_manual_tpm_override(
            "I-M2",
            field_deltas={
                "doc_count": (5, 10),
                "no_customer_upload": (False, True),
            },
            pm_id="pm-1", storage=storage, sp_writer=sp, audit=audit, event_context=ctx(),
        )
        assert results == []
        # Both fields written in one batch
        di_updates = [w for w in storage.writes if w[0] == "update"]
        assert any(
            w[2] == {"doc_count": 10, "no_customer_upload": True}
            for w in di_updates
        )

    def test_freetext_field_audit_summarized(self, writers):
        storage, sp, audit = writers
        storage.items["I-M3"] = SimpleNamespace(delivery_state=DeliveryState.OPEN)
        apply_manual_tpm_override(
            "I-M3",
            field_deltas={"target_folder": (None, "Compliance/Bluetooth_SIG")},
            pm_id="pm-1", storage=storage, sp_writer=sp, audit=audit, event_context=ctx(),
        )
        log = [a for a in audit.logs if a[0] == "manual_field_override"][0]
        # Free-text field summarized, not raw value
        assert isinstance(log[3]["new_value"], dict)
        assert log[3]["new_value"]["present"] is True

    def test_bounded_field_audit_passthrough(self, writers):
        storage, sp, audit = writers
        storage.items["I-M4"] = SimpleNamespace(delivery_state=DeliveryState.OPEN)
        apply_manual_tpm_override(
            "I-M4",
            field_deltas={"review_required": (False, True)},
            pm_id="pm-1", storage=storage, sp_writer=sp, audit=audit, event_context=ctx(),
        )
        log = [a for a in audit.logs if a[0] == "manual_field_override"][0]
        assert log[3]["new_value"] is True


# ---------------------------------------------------------------------------
# Error code registration smoke check (TRK-E001..E006 + TRK-W001..W008)
# ---------------------------------------------------------------------------


class TestErrorCodes:
    def test_all_14_trk_codes_registered(self):
        from core.src.diagnostics.error_codes import ERROR_CODES
        expected = {f"TRK-E00{i}" for i in range(1, 7)} | {f"TRK-W00{i}" for i in range(1, 9)}
        present = {code for code in ERROR_CODES if code.startswith("TRK-")}
        assert expected == present, f"Missing: {expected - present}; extra: {present - expected}"
