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
    DeliveryState.NOT_STARTED: frozenset({
        DeliveryState.OPEN,
        DeliveryState.CLOSED,
        DeliveryState.CLOSE_IN_PROGRESS,   # CIP-1 2026-07-28
    }),

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
        DeliveryState.CLOSE_IN_PROGRESS,        # CIP-1 2026-07-28
    }),

    # OutreachSent: owner can now report status (Delayed/Blocked) or send docs
    # (DocumentReceived) or close intent (OwnerClosed — Confirmation items).
    #
    # CLOSE-1 (2026-07-28): + CLOSED. TPM's "Close All Items" milestone action
    # is authoritative and must be able to force-close from any active state.
    # Guard 5 (DEF-20) still gates: only trigger_source in
    # ('manual_tpm_override', 'tpm_button') allowed; automated close from this
    # state remains rejected as policy violation. Legality broadens, policy
    # unchanged.
    DeliveryState.OUTREACH_SENT: frozenset({
        DeliveryState.DOCUMENT_RECEIVED,
        DeliveryState.OWNER_CLOSED,
        DeliveryState.DELAYED,
        DeliveryState.BLOCKED,
        DeliveryState.CLOSED,               # CLOSE-1
        DeliveryState.CLOSE_IN_PROGRESS,    # CIP-1
    }),

    # DocumentReceived: docs have arrived; close gates (doc_count + reviews)
    # checked by OwnerClosed 2-condition guard. Owner can still report status.
    # CLOSE-1 (2026-07-28): + CLOSED for TPM force-close (see OUTREACH_SENT).
    # CIP-1 (2026-07-28): + CLOSE_IN_PROGRESS for per-item TPM close via SP UI.
    DeliveryState.DOCUMENT_RECEIVED: frozenset({
        DeliveryState.OWNER_CLOSED,
        DeliveryState.DELAYED,
        DeliveryState.BLOCKED,
        DeliveryState.CLOSED,               # CLOSE-1
        DeliveryState.CLOSE_IN_PROGRESS,    # CIP-1
    }),

    # OwnerClosed: transient — auto-advances to UnderPMReview within same task
    # body. Ph-2 multi-revision FR-66 forks here; Ph-1 single-revision flow has
    # zero observable duration.
    # CLOSE-1 (2026-07-28): + CLOSED for TPM force-close short-circuiting the
    # transient auto-advance (accepted per architect 2026-07-28: TPM final word).
    # CIP-1 (2026-07-28): + CLOSE_IN_PROGRESS for per-item TPM close via SP UI.
    DeliveryState.OWNER_CLOSED: frozenset({
        DeliveryState.UNDER_PM_REVIEW,
        DeliveryState.CLOSED,               # CLOSE-1
        DeliveryState.CLOSE_IN_PROGRESS,    # CIP-1
    }),

    # UnderPMReview: PM evaluates; PMApproval gate fires READY_FOR_SUBMISSION.
    # Owner can still report Delayed/Blocked at this stage.
    # CLOSE-1 (2026-07-28): + CLOSED for TPM force-close (see OUTREACH_SENT).
    # CIP-1 (2026-07-28): + CLOSE_IN_PROGRESS for per-item TPM close via SP UI.
    DeliveryState.UNDER_PM_REVIEW: frozenset({
        DeliveryState.READY_FOR_SUBMISSION,
        DeliveryState.DELAYED,
        DeliveryState.BLOCKED,
        DeliveryState.CLOSED,               # CLOSE-1
        DeliveryState.CLOSE_IN_PROGRESS,    # CIP-1
    }),

    # ReadyForSubmission: PM-approved; awaits FR-18 carrier dispatch. Direct →
    # CLOSED allowed only for no_customer_upload=True items (skip carrier
    # upload entirely) per FR-7 + DEF-20 carve-out; guard enforces.
    # CIP-1 (2026-07-28): + CLOSE_IN_PROGRESS for per-item TPM close via SP UI.
    DeliveryState.READY_FOR_SUBMISSION: frozenset({
        DeliveryState.SUBMITTED_TO_CUSTOMER,
        DeliveryState.CLOSED,
        DeliveryState.DELAYED,
        DeliveryState.BLOCKED,
        DeliveryState.CLOSE_IN_PROGRESS,    # CIP-1
    }),

    # SubmittedToCustomer: dispatched. CLOSED via TPM Mark Closed (FR-7 +
    # DEF-20 manual). Rewind to DocumentReceived/OutreachSent for customer RFI
    # / re-submission — guard requires TPM attribution (TRK-E006 if automated).
    # CIP-1 (2026-07-28): + CLOSE_IN_PROGRESS for per-item TPM close via SP UI.
    DeliveryState.SUBMITTED_TO_CUSTOMER: frozenset({
        DeliveryState.CLOSED,
        DeliveryState.DOCUMENT_RECEIVED,
        DeliveryState.OUTREACH_SENT,
        DeliveryState.CLOSE_IN_PROGRESS,    # CIP-1
    }),

    # CLOSED: terminal.
    DeliveryState.CLOSED: frozenset(),

    # CIP-1 (2026-07-28): CloseInProgress is a 1-hop transient. SP UI writes
    # this the moment TPM clicks Close on a single item; SP UI treats it same
    # as CLOSED for button visibility (Start Collection disabled), serializing
    # TPM intent immediately without waiting for HILDA's ~90s alert-processing
    # cycle. HILDA's apply_tpm_sp_close_in_progress_task advances to CLOSED
    # on the SP CHANGED alert. Only legal exit is CLOSED -- no back-out, no
    # sideways moves. Guard 5 (DEF-20) accepts trigger_source
    # ('manual_tpm_override' | 'tpm_button') for the advance, same as any
    # other CLOSED transition; task uses bypass_guards=True defensively so a
    # policy tweak elsewhere can't strand items at CloseInProgress.
    DeliveryState.CLOSE_IN_PROGRESS: frozenset({DeliveryState.CLOSED}),

    # Delayed / Blocked: off-path holding. Resume to previous active state
    # (OPEN excluded — see Open row above).
    # CLOSE-1 (2026-07-28): + CLOSED for TPM force-close (see OUTREACH_SENT).
    # CIP-1 (2026-07-28): + CLOSE_IN_PROGRESS for per-item TPM close via SP UI.
    DeliveryState.DELAYED: frozenset({
        DeliveryState.OUTREACH_SENT,
        DeliveryState.DOCUMENT_RECEIVED,
        DeliveryState.UNDER_PM_REVIEW,
        DeliveryState.READY_FOR_SUBMISSION,
        DeliveryState.CLOSED,               # CLOSE-1
        DeliveryState.CLOSE_IN_PROGRESS,    # CIP-1
    }),
    DeliveryState.BLOCKED: frozenset({
        DeliveryState.OUTREACH_SENT,
        DeliveryState.DOCUMENT_RECEIVED,
        DeliveryState.UNDER_PM_REVIEW,
        DeliveryState.READY_FOR_SUBMISSION,
        DeliveryState.CLOSED,               # CLOSE-1
        DeliveryState.CLOSE_IN_PROGRESS,    # CIP-1
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
