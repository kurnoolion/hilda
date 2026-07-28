"""Canonical state-machine for DeliveryItem.delivery_state.

Single source of truth for the 11-state transition matrix per FR-7 + FR-28.
Pure data + pure predicate — no IO, no mutation. Importable by guards,
transitions, and any read-only consumer (dashboard, SP UI preflight queries).

DeliveryState enum is re-exported from `template_schema` (canonical definition).
The LEGAL_TRANSITIONS matrix is owned here.

Anchors FR-7 (11 states + happy-path 8-state traversal + Delayed/Blocked off-path),
FR-28 (OwnerStatusConfirmed + PMApproval + MilestoneAllClosed triggers), DEF-20
(Ph-1 CLOSED transitions are TPM-manual only — guards enforce trigger_source).
"""
from __future__ import annotations

from core.src.template_schema import DeliveryState

__all__ = ["DeliveryState", "LEGAL_TRANSITIONS", "transition_legal"]


# The legal transition matrix — immutable global. Adding a transition is a code
# change + ADR. Format: from_state → frozenset of legal target states (excluding
# self; idempotent no-op transitions are handled by `transition_legal` separately).
#
# Rationale per per-row comments below. Off-path DELAYED/BLOCKED entry semantics
# per FR-7 + FR-28 OwnerStatusConfirmed: reachable from OutreachSent onwards
# (owner has not yet received outreach from OPEN, so cannot report status).
# Resume from DELAYED/BLOCKED returns to one of the four active states; the
# specific resume target is encoded in DeliveryItem.prior_delivery_state.
LEGAL_TRANSITIONS: dict[DeliveryState, frozenset[DeliveryState]] = {
    # Not Started: TPM has loaded the customer template into SP and the item row
    # exists, but HILDA tracking has not yet been kicked off. Start Collection
    # (FR-8) transitions to Open.
    #
    # NOT_STARTED -> CLOSED added 2026-07-21 per D-149: TPM early-close of
    # not-applicable items during the brief window between "Setup Deliverables"
    # click and HILDA's D-144 auto-transition (NS -> Open). Guard 5 already
    # accepts trigger_source in ('tpm_button', 'manual_tpm_override') from any
    # from-state (except the RFS + no_customer_upload=False block), so no
    # guard change is needed. Symmetric belt-and-suspenders with the
    # OPEN -> CLOSED path used for the same early-close intent when auto-
    # transition has already landed.
    DeliveryState.NOT_STARTED: frozenset({DeliveryState.OPEN, DeliveryState.CLOSED}),

    # Open: HILDA has armed tracking; outreach not yet sent. Owner cannot report
    # Delayed/Blocked because they haven't been contacted yet.
    #
    # OPEN -> CLOSED added 2026-07-15 for the Default WI auto-close shortcut:
    # Default work items go Not Started -> Open at import time (import task's
    # auto-transition), then straight to Closed when all non-Default items in
    # the (customer, device, milestone) scope reach ReadyForSubmission
    # (apply_pm_approval_task's post-transition sweep). Default WI has no
    # OutreachSent/DocumentReceived/OwnerClosed/UnderPMReview lifecycle since
    # it has no owner and no PM approval flow; the shortcut avoids fabricating
    # artificial intermediate states. Guard 4 for CLOSED (DEF-20) requires
    # trigger_source='tpm_button' | 'manual_tpm_override' | 'automated' with
    # rule_id='default_wi_auto_close_on_all_ready' -- the sweep uses the
    # last form (attribution captured in the audit row).
    #
    # OPEN -> SUBMITTED_TO_CUSTOMER added 2026-07-28 per STATE-1 (Ph-1 blocker):
    # HILDA-generated deliverables like the "Final DRR status excel deliverable
    # for carrier" item (D-148) never go through the owner reply / PM approval
    # cycle — they sit at Open until HILDA's day-of tpm_notification tick
    # generates the Excel + sends it to the TPM. At that moment HILDA IS the
    # authoritative "submitted to carrier" event source (per architect 2026-07-18
    # semantics: TPM forwards the HILDA-generated Excel to the carrier). Prior
    # LEGAL_TRANSITIONS[OPEN] omitted this edge, so Guard 1 (legality) rejected
    # the transition BEFORE Guard 4's existing trigger_source='tpm_drr_final_
    # deliverable' carve-out could allow it. Guard 4 (line ~232 in guards.py)
    # already gates SubmittedToCustomer on trigger_source in
    # ('submit_to_carrier_task', 'tpm_drr_final_deliverable') so no other
    # trigger source can accidentally exploit this new legal edge.
    DeliveryState.OPEN: frozenset({
        DeliveryState.OUTREACH_SENT,
        DeliveryState.CLOSED,
        DeliveryState.SUBMITTED_TO_CUSTOMER,   # STATE-1 2026-07-28: D-148 final-deliverable path
    }),

    # OutreachSent: owner can now report status (Delayed/Blocked) or send docs
    # (DocumentReceived) or close intent (OwnerClosed — Confirmation items).
    DeliveryState.OUTREACH_SENT: frozenset({
        DeliveryState.DOCUMENT_RECEIVED,
        DeliveryState.OWNER_CLOSED,
        DeliveryState.DELAYED,
        DeliveryState.BLOCKED,
    }),

    # DocumentReceived: docs have arrived; close gates (doc_count + reviews)
    # checked by OwnerClosed 2-condition guard. Owner can still report status.
    DeliveryState.DOCUMENT_RECEIVED: frozenset({
        DeliveryState.OWNER_CLOSED,
        DeliveryState.DELAYED,
        DeliveryState.BLOCKED,
    }),

    # OwnerClosed: transient — auto-advances to UnderPMReview within same task
    # body. Ph-2 multi-revision FR-66 forks here; Ph-1 single-revision flow has
    # zero observable duration.
    DeliveryState.OWNER_CLOSED: frozenset({DeliveryState.UNDER_PM_REVIEW}),

    # UnderPMReview: PM evaluates; PMApproval gate fires READY_FOR_SUBMISSION.
    # Owner can still report Delayed/Blocked at this stage.
    DeliveryState.UNDER_PM_REVIEW: frozenset({
        DeliveryState.READY_FOR_SUBMISSION,
        DeliveryState.DELAYED,
        DeliveryState.BLOCKED,
    }),

    # ReadyForSubmission: PM-approved; awaits FR-18 carrier dispatch. Direct →
    # CLOSED allowed only for no_customer_upload=True items (skip carrier
    # upload entirely) per FR-7 + DEF-20 carve-out; guard enforces.
    DeliveryState.READY_FOR_SUBMISSION: frozenset({
        DeliveryState.SUBMITTED_TO_CUSTOMER,
        DeliveryState.CLOSED,
        DeliveryState.DELAYED,
        DeliveryState.BLOCKED,
    }),

    # SubmittedToCustomer: dispatched. CLOSED via TPM Mark Closed (FR-7 +
    # DEF-20 manual). Rewind to DocumentReceived/OutreachSent for customer RFI
    # / re-submission — guard requires TPM attribution (TRK-E006 if automated).
    DeliveryState.SUBMITTED_TO_CUSTOMER: frozenset({
        DeliveryState.CLOSED,
        DeliveryState.DOCUMENT_RECEIVED,
        DeliveryState.OUTREACH_SENT,
    }),

    # CLOSED: terminal.
    DeliveryState.CLOSED: frozenset(),

    # Delayed / Blocked: off-path holding. Resume to previous active state
    # (OPEN excluded — see Open row above).
    DeliveryState.DELAYED: frozenset({
        DeliveryState.OUTREACH_SENT,
        DeliveryState.DOCUMENT_RECEIVED,
        DeliveryState.UNDER_PM_REVIEW,
        DeliveryState.READY_FOR_SUBMISSION,
    }),
    DeliveryState.BLOCKED: frozenset({
        DeliveryState.OUTREACH_SENT,
        DeliveryState.DOCUMENT_RECEIVED,
        DeliveryState.UNDER_PM_REVIEW,
        DeliveryState.READY_FOR_SUBMISSION,
    }),
}


def transition_legal(from_state: DeliveryState, to_state: DeliveryState) -> bool:
    """Returns True iff `to_state` is a legal transition target from `from_state`.

    Pure function; no side effects. Idempotent no-op transitions
    (`from_state == to_state`) are always legal — callers that detect a no-op
    short-circuit before invoking guards / writing state.

    Used by:
    - `guards.check_transition_guards` (first guard predicate)
    - `transitions.update_delivery_state` (legality check before guards)
    - `dashboard` / SP UI engineer's preflight: "would this transition be
      structurally allowed?" — separate from guard predicates.
    """
    if from_state == to_state:
        return True
    return to_state in LEGAL_TRANSITIONS[from_state]
