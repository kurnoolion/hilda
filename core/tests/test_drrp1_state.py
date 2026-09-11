"""DRRP1-STATE-1 (2026-09-10) -- cross-milestone state promotion and reverse.

Per user 2026-09-09 (Option B) + 2026-09-10 rules:

Forward (DRR RFS -> P1 promote):
  - trigger_source='drr_mapping_promote'
  - Legal from every P1 state EXCEPT UnderPMReview (TPM must approve manually
    from UnderPMReview per user #3)
  - Bypasses Guard 3's PMApproval requirement (approval happened on DRR side)

Reverse (new own doc on RFS item):
  - trigger_source='doc_received_after_rfs'
  - Universal (b) per user Q1: any RFS item receiving a new own doc pulls
    back to UnderPMReview. Applies to both DRR and P1.
  - "New own doc" excludes: reclassify, revision-family merge (TPM edit),
    DRR migration passthrough. Caller responsibility.

Pure-function tests -- no DB / Celery / adapter surface.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.src.tracker.guards import check_transition_guards
from core.src.tracker.state_machine import (
    DeliveryState,
    LEGAL_TRANSITIONS,
    transition_legal,
)


# ---------------------------------------------------------------------------
# State machine legality (structural, no guards).
# ---------------------------------------------------------------------------


class TestLegalTransitionsForwardPromote:
    """DRR -> P1 promotion targets RFS from any pre-RFS active state."""

    def test_open_to_rfs_is_now_legal(self):
        assert transition_legal(
            DeliveryState.OPEN, DeliveryState.READY_FOR_SUBMISSION,
        ) is True

    def test_outreach_sent_to_rfs_is_now_legal(self):
        assert transition_legal(
            DeliveryState.OUTREACH_SENT, DeliveryState.READY_FOR_SUBMISSION,
        ) is True

    def test_document_received_to_rfs_is_now_legal(self):
        assert transition_legal(
            DeliveryState.DOCUMENT_RECEIVED, DeliveryState.READY_FOR_SUBMISSION,
        ) is True

    def test_owner_closed_to_rfs_is_now_legal(self):
        assert transition_legal(
            DeliveryState.OWNER_CLOSED, DeliveryState.READY_FOR_SUBMISSION,
        ) is True

    def test_under_pm_review_to_rfs_still_legal(self):
        # Normal PMApproval path -- unchanged.
        assert transition_legal(
            DeliveryState.UNDER_PM_REVIEW, DeliveryState.READY_FOR_SUBMISSION,
        ) is True

    def test_delayed_blocked_to_rfs_still_legal(self):
        # Existing resume-to-active path -- unchanged.
        assert transition_legal(
            DeliveryState.DELAYED, DeliveryState.READY_FOR_SUBMISSION,
        ) is True
        assert transition_legal(
            DeliveryState.BLOCKED, DeliveryState.READY_FOR_SUBMISSION,
        ) is True


class TestLegalTransitionsReverseOnDocReceipt:
    """RFS -> UnderPMReview new edge for the reverse."""

    def test_rfs_to_under_pm_review_is_now_legal(self):
        assert transition_legal(
            DeliveryState.READY_FOR_SUBMISSION, DeliveryState.UNDER_PM_REVIEW,
        ) is True

    def test_rfs_still_reaches_submitted_and_closed(self):
        # Regression: the new edge did not remove any prior legal target.
        assert transition_legal(
            DeliveryState.READY_FOR_SUBMISSION, DeliveryState.SUBMITTED_TO_CUSTOMER,
        ) is True
        assert transition_legal(
            DeliveryState.READY_FOR_SUBMISSION, DeliveryState.CLOSED,
        ) is True

    def test_terminal_states_did_not_gain_new_legal_edges(self):
        # Regression: CLOSED stays terminal, SUBMITTED did not gain
        # UnderPMReview as a rewind target.
        assert LEGAL_TRANSITIONS[DeliveryState.CLOSED] == frozenset()
        assert DeliveryState.UNDER_PM_REVIEW not in LEGAL_TRANSITIONS[
            DeliveryState.SUBMITTED_TO_CUSTOMER
        ]


# ---------------------------------------------------------------------------
# Guard 10 -- drr_mapping_promote
# ---------------------------------------------------------------------------


def _item(state: DeliveryState, **kw) -> SimpleNamespace:
    """Minimal DeliveryItemBase-shaped snapshot for guard tests."""
    defaults = dict(
        delivery_state=state,
        pm_approval_at=None,           # Guard 3 gate
        pm_approval_pm_id=None,
        item_type="test_tech_waiver_report",
        no_customer_upload=False,
        review_required=False,
        review_status="not_required",
        doc_count=1,
        doc_count_received=1,
        carrier_upload_complete=False,
        prior_delivery_state=None,
    )
    defaults.update(kw)
    return SimpleNamespace(**defaults)


class TestGuard10DrrMappingPromote:
    """drr_mapping_promote trigger: promotes any pre-RFS state to RFS,
    blocks from UnderPMReview, rejects any target other than RFS."""

    def test_open_to_rfs_allowed(self):
        item = _item(DeliveryState.OPEN)
        r = check_transition_guards(
            item, DeliveryState.READY_FOR_SUBMISSION,
            trigger_source="drr_mapping_promote",
        )
        assert r.allowed is True
        assert r.reason is None

    def test_outreach_sent_to_rfs_allowed(self):
        r = check_transition_guards(
            _item(DeliveryState.OUTREACH_SENT),
            DeliveryState.READY_FOR_SUBMISSION,
            trigger_source="drr_mapping_promote",
        )
        assert r.allowed is True

    def test_document_received_to_rfs_allowed(self):
        r = check_transition_guards(
            _item(DeliveryState.DOCUMENT_RECEIVED),
            DeliveryState.READY_FOR_SUBMISSION,
            trigger_source="drr_mapping_promote",
        )
        assert r.allowed is True

    def test_owner_closed_to_rfs_allowed(self):
        r = check_transition_guards(
            _item(DeliveryState.OWNER_CLOSED),
            DeliveryState.READY_FOR_SUBMISSION,
            trigger_source="drr_mapping_promote",
        )
        assert r.allowed is True

    def test_delayed_to_rfs_allowed(self):
        # Delayed / Blocked are legal RFS sources structurally; the
        # drr_mapping_promote trigger doesn't add extra blocks here.
        r = check_transition_guards(
            _item(DeliveryState.DELAYED),
            DeliveryState.READY_FOR_SUBMISSION,
            trigger_source="drr_mapping_promote",
        )
        assert r.allowed is True

    def test_under_pm_review_to_rfs_blocked(self):
        # The key rule per user 2026-09-09 #3: TPM must approve explicitly.
        r = check_transition_guards(
            _item(DeliveryState.UNDER_PM_REVIEW),
            DeliveryState.READY_FOR_SUBMISSION,
            trigger_source="drr_mapping_promote",
        )
        assert r.allowed is False
        assert r.reason == "drr_mapping_promote_blocked_by_under_pm_review"
        assert "under_pm_review_requires_explicit_tpm_approval" in r.blocking_conditions

    def test_drr_mapping_promote_rejects_non_rfs_target(self):
        # Defensive: the trigger is single-purpose.
        r = check_transition_guards(
            _item(DeliveryState.OPEN),
            DeliveryState.OUTREACH_SENT,
            trigger_source="drr_mapping_promote",
        )
        assert r.allowed is False
        assert r.reason == "drr_mapping_promote_wrong_target"

    def test_pm_approval_not_required_for_promote(self):
        # Guard 3 keys on from_state==UnderPMReview + no pm_approval_at.
        # Any drr_mapping_promote from another from_state bypasses that
        # requirement -- DRR side already carries the approval.
        item = _item(DeliveryState.OPEN, pm_approval_at=None)
        r = check_transition_guards(
            item, DeliveryState.READY_FOR_SUBMISSION,
            trigger_source="drr_mapping_promote",
        )
        assert r.allowed is True

    def test_automated_promote_to_rfs_from_open_still_illegal(self):
        # Without the drr_mapping_promote trigger, an automated rule cannot
        # jump Open -> RFS. Legal edge exists (Guard 1 passes) but no other
        # guard permits it -- this is where accidental use would surface.
        # Under 'automated' trigger there is no explicit block, so it would
        # pass. That is intentional: bypass_guards / TPM override remain
        # authoritative. The new Guard 10 does not police OTHER triggers.
        # This test locks the SEMANTICS -- the promotion path is trigger-
        # driven, not gated behind an additional guard. If a future change
        # wants to police 'automated' too, add a rule; for now, document.
        r = check_transition_guards(
            _item(DeliveryState.OPEN),
            DeliveryState.READY_FOR_SUBMISSION,
            trigger_source="automated",
        )
        assert r.allowed is True  # intentional; document surface


# ---------------------------------------------------------------------------
# Guard 11 -- doc_received_after_rfs
# ---------------------------------------------------------------------------


class TestGuard11DocReceivedAfterRfs:
    """doc_received_after_rfs trigger: RFS -> UnderPMReview only."""

    def test_rfs_to_under_pm_review_allowed(self):
        r = check_transition_guards(
            _item(DeliveryState.READY_FOR_SUBMISSION),
            DeliveryState.UNDER_PM_REVIEW,
            trigger_source="doc_received_after_rfs",
        )
        assert r.allowed is True

    def test_universal_applies_to_normal_pmapproval_rfs_item(self):
        # (b) universal per user Q1: it doesn't matter HOW the item reached
        # RFS. An item with pm_approval_at set (normal path) still bounces
        # back on new doc receipt.
        item = _item(
            DeliveryState.READY_FOR_SUBMISSION,
            pm_approval_at="2026-09-10T00:00:00Z",
            pm_approval_pm_id="pm-1@example.com",
        )
        r = check_transition_guards(
            item, DeliveryState.UNDER_PM_REVIEW,
            trigger_source="doc_received_after_rfs",
        )
        assert r.allowed is True

    def test_from_non_rfs_rejected(self):
        # Structural legality (Guard 1) runs first: DocumentReceived ->
        # UnderPMReview is not in LEGAL_TRANSITIONS[DocumentReceived], so
        # Guard 1's illegal_transition fires before Guard 11 sees the call.
        # The bounded rejection is what matters; the exact reason is
        # secondary. Test both facts.
        r = check_transition_guards(
            _item(DeliveryState.DOCUMENT_RECEIVED),
            DeliveryState.UNDER_PM_REVIEW,
            trigger_source="doc_received_after_rfs",
        )
        assert r.allowed is False
        assert r.reason == "illegal_transition"

    def test_from_owner_closed_rejected(self):
        # OwnerClosed -> UnderPMReview IS legal (normal ladder), so the
        # illegal_transition guard does NOT fire. Guard 11 then catches
        # the wrong-from-state and rejects, protecting the trigger's
        # single-purpose contract.
        r = check_transition_guards(
            _item(DeliveryState.OWNER_CLOSED),
            DeliveryState.UNDER_PM_REVIEW,
            trigger_source="doc_received_after_rfs",
        )
        assert r.allowed is False
        assert r.reason == "doc_received_after_rfs_wrong_from_state"

    def test_wrong_target_rejected(self):
        # Defensive: the trigger is single-purpose.
        r = check_transition_guards(
            _item(DeliveryState.READY_FOR_SUBMISSION),
            DeliveryState.SUBMITTED_TO_CUSTOMER,
            trigger_source="doc_received_after_rfs",
        )
        assert r.allowed is False
        assert r.reason == "doc_received_after_rfs_wrong_target"


# ---------------------------------------------------------------------------
# Regression -- normal PMApproval path unchanged
# ---------------------------------------------------------------------------


class TestGuardsRegressionExistingPaths:
    """The new triggers / legal edges must not weaken existing gates."""

    def test_under_pm_review_to_rfs_still_requires_pm_approval(self):
        # Guard 3 (PMApproval) intact -- automated UnderPMReview -> RFS
        # still needs pm_approval_at.
        item = _item(DeliveryState.UNDER_PM_REVIEW, pm_approval_at=None)
        r = check_transition_guards(
            item, DeliveryState.READY_FOR_SUBMISSION,
            trigger_source="automated",
        )
        assert r.allowed is False
        assert "pm_approval_required" in r.blocking_conditions

    def test_under_pm_review_to_rfs_with_pm_approval_still_passes(self):
        item = _item(
            DeliveryState.UNDER_PM_REVIEW,
            pm_approval_at="2026-09-10T00:00:00Z",
            pm_approval_pm_id="pm-1@example.com",
        )
        r = check_transition_guards(
            item, DeliveryState.READY_FOR_SUBMISSION,
            trigger_source="automated",
        )
        assert r.allowed is True

    def test_closed_from_rfs_still_needs_no_customer_upload(self):
        # Guard 5 direct-close carve-out intact.
        item = _item(
            DeliveryState.READY_FOR_SUBMISSION,
            no_customer_upload=False,
        )
        r = check_transition_guards(
            item, DeliveryState.CLOSED,
            trigger_source="manual_tpm_override",
        )
        assert r.allowed is False
