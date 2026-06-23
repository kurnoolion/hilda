"""Pre-transition guard predicates for DeliveryItem state transitions.

Pure functions: take an immutable item snapshot + target state + trigger
metadata, return a GuardResult. No mutation, no IO, no side effects —
guards are safe to call from `dashboard` / SP UI preflight queries
("would this transition be allowed?") as well as from `transitions`.

Guards implemented (Ph-1):
  1. transition_legal (state-machine structural legality)
  2. OwnerClosed 2-condition guard per FR-28 OwnerStatusConfirmed
  3. READY_FOR_SUBMISSION requires pm_approval_at non-None (PMApproval gate per NFR-5)
  4. SubmittedToCustomer requires upload completion per FR-18 (read from item)
  5. CLOSED in Ph-1 requires TPM attribution per FR-7 + DEF-20
  6. DELAYED / BLOCKED unconditional from active states (only FR-7 modality + transition_legal check)
  7. OWNER_CLOSED -> UNDER_PM_REVIEW auto-advance (no extra guard)
  8. Rewind from SUBMITTED_TO_CUSTOMER requires TPM attribution
  9. (new 2026-06-23) DELAYED/BLOCKED auto-resume target must match prior_delivery_state
     for automated transitions -- TRK-W008 warning if mismatch

`bypass_guards=True` (FR-14 manual TPM override): all guards skipped except
transition_legal -- legality is structural, not policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from core.src.template_schema import DeliveryItemBase, ItemType

from core.src.tracker.state_machine import DeliveryState, transition_legal

__all__ = [
    "GuardResult",
    "TriggerSource",
    "check_transition_guards",
    "list_blocking_conditions_for_state",
]


TriggerSource = Literal["automated", "manual_tpm_override", "tpm_button"]


@dataclass(frozen=True)
class GuardResult:
    """Outcome of a pre-transition guard check.

    `reason` is a bounded enum token + brief context (NFR-2 — never raw
    customer-data values). `blocking_conditions` lists every unmet guard
    predicate so the PM/TPM dashboard can surface the full picture
    instead of just the first failure.

    `warnings` carries non-blocking signals (e.g., TRK-W008 resume-target
    mismatch) — caller may proceed but should log the code.
    """

    allowed: bool
    reason: str | None
    blocking_conditions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Constants extracted from FR-7 / FR-86 alignment matrix for use in guards
# ---------------------------------------------------------------------------

_ACTIVE_NON_OFFPATH_STATES: frozenset[DeliveryState] = frozenset({
    DeliveryState.OUTREACH_SENT,
    DeliveryState.DOCUMENT_RECEIVED,
    DeliveryState.UNDER_PM_REVIEW,
    DeliveryState.READY_FOR_SUBMISSION,
})


def _has_pm_approval(item: DeliveryItemBase) -> bool:
    """PM approval gate per FR-28 PMApproval + NFR-5. Approval is recorded
    as `pm_approval_at` (datetime) + `pm_approval_pm_id` (str) on the item.
    The PMApproval-trigger task body sets these BEFORE invoking
    `transitions.update_delivery_state(target=READY_FOR_SUBMISSION)`.
    """
    return getattr(item, "pm_approval_at", None) is not None


def _doc_count_reached(item: DeliveryItemBase) -> bool:
    """FR-28 OwnerStatusConfirmed condition (i): doc_count_received >= doc_count.
    `doc_count_received` is HILDA-side counter on DeliveryItem (incremented per
    FR-52 successful routing); `doc_count` is template-author expected count.
    """
    received = getattr(item, "doc_count_received", 0) or 0
    expected = getattr(item, "doc_count", 0) or 0
    return received >= expected


def _all_reviews_complete(item: DeliveryItemBase) -> bool:
    """FR-28 OwnerStatusConfirmed condition (ii): when review_required=True,
    all received docs+revisions reviewed. Implementation reads `review_status`
    field which is a 4-value enum {pending, complete, not_required, failed}.

    'complete' or 'not_required' or 'failed' all count as 'not pending' for
    the guard -- a failed review is still terminal (TPM resolves separately).
    Only 'pending' blocks OWNER_CLOSED transition.
    """
    if not getattr(item, "review_required", False):
        return True
    status = getattr(item, "review_status", "pending")
    return status != "pending"


def _is_confirmation(item: DeliveryItemBase) -> bool:
    """item_type == 'Confirmation' per SP UI engineer lock 2026-06-23 (PascalCase)."""
    return getattr(item, "item_type", None) == ItemType.CONFIRMATION.value


def _is_no_customer_upload(item: DeliveryItemBase) -> bool:
    """FR-7 + DEF-20 carve-out: items with no_customer_upload=True can reach
    CLOSED from READY_FOR_SUBMISSION directly (skip carrier dispatch).
    """
    return bool(getattr(item, "no_customer_upload", False))


def _carrier_upload_complete(item: DeliveryItemBase) -> bool:
    """FR-18 condition: all in-scope files uploaded to carrier. Read as
    HILDA-side boolean flag set by customer_adapter on successful FR-18
    dispatch. Implementation looks for `carrier_upload_complete` attribute
    on the snapshot (set by FR-68 verification loop A).
    """
    return bool(getattr(item, "carrier_upload_complete", False))


# ---------------------------------------------------------------------------
# Main guard entry point
# ---------------------------------------------------------------------------


def check_transition_guards(
    item: DeliveryItemBase,
    target_state: DeliveryState,
    trigger_source: TriggerSource = "automated",
    bypass_guards: bool = False,
) -> GuardResult:
    """Returns GuardResult indicating whether (item, target_state) transition
    is allowed under current item snapshot.

    Pure function; no mutation. See module docstring for the guard list.

    `bypass_guards=True` (FR-14 manual TPM override): only transition_legal
    runs; all other guards skipped. Requires trigger_source='manual_tpm_override'
    per Invariant -- if it's set from another trigger_source, that's a bug
    and the CALLER (transitions.update_delivery_state) emits TRK-E004; this
    function does not enforce the rule (separation of concerns -- structural
    vs policy).
    """
    from_state = item.delivery_state

    # ---------- Always: structural legality ----------
    if not transition_legal(from_state, target_state):
        return GuardResult(
            allowed=False,
            reason="illegal_transition",
            blocking_conditions=["transition_not_in_LEGAL_TRANSITIONS"],
        )

    # ---------- FR-14 bypass path ----------
    if bypass_guards:
        # Legality already confirmed above; manual TPM override skips the rest.
        return GuardResult(allowed=True, reason="bypass_guards_manual_tpm_override")

    blocking: list[str] = []
    warnings: list[str] = []

    # ---------- Guard 5: CLOSED in Ph-1 requires TPM attribution (DEF-20) ----------
    if target_state == DeliveryState.CLOSED:
        if trigger_source not in ("manual_tpm_override", "tpm_button"):
            return GuardResult(
                allowed=False,
                reason="closed_requires_tpm_attribution",
                blocking_conditions=["automated_close_forbidden_per_def20"],
            )
        # READY_FOR_SUBMISSION -> CLOSED allowed only for no_customer_upload=True
        if from_state == DeliveryState.READY_FOR_SUBMISSION and not _is_no_customer_upload(item):
            return GuardResult(
                allowed=False,
                reason="ready_to_closed_requires_no_customer_upload",
                blocking_conditions=["no_customer_upload_false_blocks_direct_close"],
            )

    # ---------- Guard 8: Rewind from SUBMITTED_TO_CUSTOMER requires TPM attribution ----------
    if from_state == DeliveryState.SUBMITTED_TO_CUSTOMER and target_state in (
        DeliveryState.DOCUMENT_RECEIVED,
        DeliveryState.OUTREACH_SENT,
    ):
        if trigger_source not in ("manual_tpm_override", "tpm_button"):
            return GuardResult(
                allowed=False,
                reason="rewind_requires_tpm_attribution",
                blocking_conditions=["automated_rewind_of_submitted_forbidden"],
            )

    # ---------- Guard 2: OwnerClosed 2-condition guard per FR-28 ----------
    if target_state == DeliveryState.OWNER_CLOSED:
        if _is_confirmation(item):
            # FR-7 Confirmation carve-out: no doc/review requirement.
            # (In normal Ph-1 operation Confirmation items skip OWNER_CLOSED entirely
            # per FR-25 (b) role-collapse; this branch is a backstop.)
            pass
        else:
            if not _doc_count_reached(item):
                blocking.append("doc_count_not_reached")
            if not _all_reviews_complete(item):
                blocking.append("reviews_pending")

    # ---------- Guard 3: READY_FOR_SUBMISSION requires PMApproval ----------
    if target_state == DeliveryState.READY_FOR_SUBMISSION and from_state == DeliveryState.UNDER_PM_REVIEW:
        if not _has_pm_approval(item):
            blocking.append("pm_approval_required")

    # ---------- Guard 4: SubmittedToCustomer requires upload completion ----------
    if target_state == DeliveryState.SUBMITTED_TO_CUSTOMER:
        if _is_no_customer_upload(item):
            # Items with no_customer_upload=True should never reach SUBMITTED_TO_CUSTOMER
            # in the first place -- the path is READY_FOR_SUBMISSION -> CLOSED direct.
            blocking.append("submitted_path_invalid_for_no_customer_upload")
        elif not _carrier_upload_complete(item):
            blocking.append("carrier_upload_incomplete")

    # ---------- Guard 9 (NEW 2026-06-23): Delayed/Blocked resume target ----------
    if from_state in (DeliveryState.DELAYED, DeliveryState.BLOCKED) and target_state in _ACTIVE_NON_OFFPATH_STATES:
        if trigger_source == "automated":
            prior = getattr(item, "prior_delivery_state", None)
            if prior is not None and prior != target_state:
                warnings.append(
                    f"TRK-W008:prior_delivery_state={prior};target={target_state.value}"
                )
                # Non-blocking: the structural matrix permits resume to any active
                # state; the warning surfaces a likely-misconfigured automated rule.
                # TPM override (trigger_source != 'automated') bypasses this entirely.

    if blocking:
        return GuardResult(
            allowed=False,
            reason="guard_denied",
            blocking_conditions=blocking,
            warnings=warnings,
        )
    return GuardResult(
        allowed=True,
        reason=None,
        warnings=warnings,
    )


def list_blocking_conditions_for_state(
    item: DeliveryItemBase,
    target_state: DeliveryState,
) -> list[str]:
    """Convenience wrapper for SP UI / dashboard surfacing — returns the
    human-readable list of conditions blocking the transition (empty list if
    allowed). Pure; mirrors `check_transition_guards` with default
    `trigger_source='automated'` and `bypass_guards=False`.
    """
    result = check_transition_guards(item, target_state)
    return list(result.blocking_conditions)
