"""DeliveryItem state transitions — the public mutating surface of tracker.

Consumed by workflow_engine UPDATE_STATE / INSTANTIATE_DEFAULT_WORK_ITEM /
REASSIGN_DOCUMENT_TO_WORK_ITEM / PROPAGATE_TAGS_TO_ACTIVE_TRACKERS task
bodies. Pure library — side effects go through injected `StorageWriter` /
`SpWriter` / `AuditWriter` Protocols (see `protocols.py`).

Key invariants enforced here:
- Idempotency: re-running update_delivery_state on an item already at
  target_state returns `no_op_idempotent` without writes (Celery
  at-least-once retry safety).
- `prior_delivery_state` write on DELAYED/BLOCKED entry; clear on resume.
- `pm_approval_at` + `pm_approval_pm_id` clearing on UNDER_PM_REVIEW entry,
  SUBMITTED_TO_CUSTOMER rewind, and DELAYED/BLOCKED return to UNDER_PM_REVIEW.
- OWNER_CLOSED transient: inline auto-advance to UNDER_PM_REVIEW after
  successful OWNER_CLOSED transition (Ph-1 single-revision flow per FR-7;
  Ph-2 FR-66 multi-revision will fork via `_post_owner_closed_target` helper).
- `StateChange` trigger is fired by the CALLER (workflow_engine task body),
  NOT by tracker — per [D-066] downstream-trigger pattern + [D-022] boundary
  (avoids tracker -> workflow_engine import cycle).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal

from core.src.diagnostics.error_codes import PipelineError
from core.src.template_schema import DeliveryItemBase

logger = logging.getLogger(__name__)

from core.src.tracker.guards import (
    GuardResult,
    TriggerSource,
    check_transition_guards,
)
from core.src.tracker.protocols import AuditWriter, SpWriter, StorageWriter
from core.src.tracker.state_machine import DeliveryState

__all__ = [
    "TransitionOutcome",
    "TransitionResult",
    "StateChangeDispatchSignal",
    "update_delivery_state",
    "advance_on_doc_count_reached",
    "auto_advance_owner_closed_to_under_pm_review",
]


TransitionOutcome = Literal[
    "transitioned",
    "no_op_idempotent",
    "guard_denied",
    "illegal_transition",
]


@dataclass(frozen=True)
class StateChangeDispatchSignal:
    """Returned on successful state change. Caller fires the canonical
    StateChange TriggerEvent via workflow_engine.trigger_sources after
    transaction commit, per [D-066]. tracker never directly invokes
    workflow_engine -- keeps dependency direction one-way.
    """

    delivery_item_id: str
    old_state: DeliveryState
    new_state: DeliveryState
    correlation_id: str
    trigger_source: TriggerSource


@dataclass(frozen=True)
class TransitionResult:
    """Result of an attempted transition.

    - `transitioned` -> caller fires StateChange via `dispatch_signal`
    - `no_op_idempotent` -> Celery retry collapsed; no StateChange fired
    - `guard_denied` -> blocking_conditions populated; PM dashboard surfaces
    - `illegal_transition` -> TRK-E001 emitted; not retriable
    """

    item_id: str
    from_state: DeliveryState
    to_state: DeliveryState
    outcome: TransitionOutcome
    guard_result: GuardResult | None
    dispatch_signal: StateChangeDispatchSignal | None
    correlation_id: str


# ---------------------------------------------------------------------------
# Helpers (private)
# ---------------------------------------------------------------------------


def _now() -> datetime:
    """Single utc-now source — overridable in tests via monkeypatch."""
    return datetime.now(timezone.utc)


def _post_owner_closed_target(item: DeliveryItemBase) -> DeliveryState:
    """Ph-1: always UNDER_PM_REVIEW (single-revision happy path per FR-7).

    Ph-2 extension point: when FR-66 multi-revision is enabled, this helper
    inspects revision count for any doc_id_slug on the item and forks to
    `AwaitingVersionSelection` (new transient state) when >1 revision exists.
    Ph-1 keeps the function trivial -- the orchestration in
    `auto_advance_owner_closed_to_under_pm_review` is unchanged for Ph-2.
    """
    return DeliveryState.UNDER_PM_REVIEW


def _correlation_id_of(event_context: dict[str, Any]) -> str:
    """Extract correlation_id from event_context. Caller (workflow_engine)
    is responsible for generating + threading. Raise if missing -- defensive
    against silent attribution loss in audit chain.
    """
    cid = event_context.get("correlation_id")
    if not cid:
        raise ValueError("event_context missing required 'correlation_id'")
    return str(cid)


def _modified_by_of(event_context: dict[str, Any], trigger_source: TriggerSource) -> str:
    """Determine `modified_by` audit string:
    - automated -> 'system:<rule_id>' if rule_id present, else 'system'
    - manual_tpm_override / tpm_button -> pm_id
    """
    if trigger_source == "automated":
        rule_id = event_context.get("rule_id")
        return f"system:{rule_id}" if rule_id else "system"
    pm_id = event_context.get("pm_id")
    return pm_id or "tpm_unknown"


def _sp_writeback_field_updates(
    *,
    sp_writer: SpWriter,
    item: DeliveryItemBase,
    customer_id: str,
    delivery_item_id: str,
    field_updates: dict[str, Any],
) -> None:
    """Two-step SP write per [D-137]: natural-key lookup -> update_item with
    SP's integer Id. Caller wraps in try/except for best-effort semantics
    per [D-118]. Returns silently when:
      - sp_writer lacks get_items (mock path / older SpWriter impl);
      - no natural-key filters can be built from the item;
      - no SP row matches the filters;
      - the matched row has no _sp_id.
    Each silent return is logged at INFO so ops can grep failure modes.
    """
    from core.src.sharepoint_integration.config import ListScope

    get_items = getattr(sp_writer, "get_items", None)
    if get_items is None:
        logger.info(
            "update_delivery_state: sp_writer has no get_items method "
            "(mock or older impl); skipping SP writeback for item=%s",
            delivery_item_id,
        )
        return

    scope = ListScope(customer_id=customer_id)
    filters: dict[str, Any] = {}
    item_no = getattr(item, "item_no", None)
    milestone_id = getattr(item, "milestone_id", None)
    device_id = (
        getattr(item, "device_id", None)
        or getattr(item, "project_model", None)
    )
    if item_no is not None:
        filters["item_no"] = item_no
    if milestone_id:
        filters["milestone_id"] = milestone_id
    if device_id:
        filters["project_model"] = device_id

    if not filters:
        logger.info(
            "update_delivery_state: no natural-key filters for item=%s; "
            "skipping SP writeback (item missing item_no + milestone_id + device_id)",
            delivery_item_id,
        )
        return

    rows = get_items(
        entity="delivery_items",
        scope=scope,
        canonical_filters=filters,
    )
    if not rows:
        logger.info(
            "update_delivery_state: SP row not found by natural-key %s for item=%s; "
            "skipping SP writeback",
            filters, delivery_item_id,
        )
        return

    sp_id_raw = rows[0].get("_sp_id")
    if sp_id_raw is None:
        logger.info(
            "update_delivery_state: SP row matched but missing _sp_id for item=%s",
            delivery_item_id,
        )
        return

    sp_writer.update_item(
        entity="delivery_items",
        scope=scope,
        item_id=str(sp_id_raw),
        canonical_fields=field_updates,
    )


def _build_field_updates(
    item: DeliveryItemBase,
    target_state: DeliveryState,
    pm_id: str | None,
    trigger_source: TriggerSource,
) -> dict[str, Any]:
    """Build the dict of DeliveryItem field updates that accompany the state
    write -- prior_delivery_state set/clear + pm_approval_at clearing.

    Returns the SP-side canonical field map. Caller passes through to
    `sp_writer.update_item` so the writeback is one REST call (per
    [D-064] + FR-12 'one REST batch' invariant).
    """
    updates: dict[str, Any] = {"delivery_state": target_state.value}
    from_state = item.delivery_state

    # ---- prior_delivery_state discipline per FR-12 + FR-7 Delayed/Blocked exit paths ----
    if target_state in (DeliveryState.DELAYED, DeliveryState.BLOCKED):
        # Entry to off-path: capture current active state for resume.
        updates["prior_delivery_state"] = from_state.value
    elif from_state in (DeliveryState.DELAYED, DeliveryState.BLOCKED):
        # Exit off-path: clear prior_delivery_state (resume to active).
        updates["prior_delivery_state"] = None

    # ---- pm_approval_at clearing discipline (defensive against stale approval) ----
    # Cleared on: (a) entry to UNDER_PM_REVIEW from any path -- fresh review cycle,
    # (b) rewind from SUBMITTED_TO_CUSTOMER, (c) DELAYED/BLOCKED return to UNDER_PM_REVIEW
    # (covered by case (a) -- entry to UNDER_PM_REVIEW catches all three).
    if target_state == DeliveryState.UNDER_PM_REVIEW:
        updates["pm_approval_at"] = None
        updates["pm_approval_pm_id"] = None
    elif from_state == DeliveryState.SUBMITTED_TO_CUSTOMER and target_state in (
        DeliveryState.DOCUMENT_RECEIVED,
        DeliveryState.OUTREACH_SENT,
    ):
        # Rewind: clear approval; if PM later re-approves, fresh entry to UNDER_PM_REVIEW
        # will re-clear (idempotent).
        updates["pm_approval_at"] = None
        updates["pm_approval_pm_id"] = None

    return updates


# ---------------------------------------------------------------------------
# Public surface
# ---------------------------------------------------------------------------


def update_delivery_state(
    delivery_item_id: str,
    target_state: DeliveryState,
    params: dict[str, Any],
    event_context: dict[str, Any],
    storage: StorageWriter,
    sp_writer: SpWriter,
    audit: AuditWriter,
    bypass_guards: bool = False,
) -> TransitionResult:
    """Canonical state-transition function consumed by workflow_engine
    UPDATE_STATE task body.

    Pipeline (idempotent against at-least-once delivery):
    1. Read current item snapshot via storage.get_delivery_item.
    2. If item.delivery_state == target_state -> return no_op_idempotent
       (Celery retry safety).
    3. If bypass_guards=True AND trigger_source != 'manual_tpm_override',
       raise TRK-E004 (defensive against rule misfire).
    4. Run check_transition_guards.
    5. If GuardResult.allowed=False: emit TRK-W001 via audit; return
       guard_denied / illegal_transition.
    6. Apply atomic updates (sequential calls; caller wraps in DB
       transaction at the workflow_engine task body layer):
       (a) storage.update_delivery_item(item_id, field_updates)
       (b) storage.write_delivery_state(item_id, target_state, ...)
       (c) sp_writer.update_item(...) -- SP writeback per [D-064]
       (d) audit.write_communication_log(action_type='state_transition', ...)
    7. If target_state == OWNER_CLOSED, inline auto-advance to UNDER_PM_REVIEW
       (transient state; see _post_owner_closed_target Ph-2 fork).
       Returns the dispatch_signal of the LAST transition (the auto-advanced
       target), so caller fires StateChange against the final observable
       state -- NOT against OWNER_CLOSED which has zero observable duration.
    8. Return TransitionResult with dispatch_signal -- caller fires StateChange.
    """
    item = storage.get_delivery_item(delivery_item_id)
    # Coerce from string (PostgresStorage returns varchar text) -> enum.
    # DeliveryState is `class DeliveryState(str, Enum)` so DeliveryState("Not Started")
    # constructs the enum directly. Fix 2026-06-27 architect Step 4 Start Collection:
    # 'str' object has no attribute 'value' at transitions.py:273 (from_state.value)
    # because the ORM read returned the column's stored varchar, not an enum instance.
    from_state = item.delivery_state
    if not isinstance(from_state, DeliveryState):
        try:
            from_state = DeliveryState(from_state)
        except (ValueError, TypeError):
            # Unknown stored string -- treat as no-op rather than crash the cascade.
            # Surface via audit so the bad row is visible without breaking the worker.
            raise PipelineError(  # noqa: TRY301
                "TRK-E001",
                context={"reason": f"unknown delivery_state {from_state!r} on item {delivery_item_id}"},
            )
    trigger_source: TriggerSource = event_context.get("trigger_source", "automated")
    correlation_id = _correlation_id_of(event_context)
    pm_id = event_context.get("pm_id")

    # Step 2: idempotent no-op short-circuit
    if from_state == target_state:
        audit.write_communication_log(
            action_type="state_transition_idempotent_noop",
            delivery_item_id=delivery_item_id,
            attribution={
                "trigger_source": trigger_source,
                "correlation_id": correlation_id,
                "modified_by": _modified_by_of(event_context, trigger_source),
            },
            details={"state": target_state.value, "code": "TRK-W003"},
        )
        return TransitionResult(
            item_id=delivery_item_id,
            from_state=from_state,
            to_state=target_state,
            outcome="no_op_idempotent",
            guard_result=None,
            dispatch_signal=None,
            correlation_id=correlation_id,
        )

    # Step 3: defensive bypass_guards trigger_source check (TRK-E004)
    if bypass_guards and trigger_source != "manual_tpm_override":
        raise PipelineError("TRK-E004", context={"source": trigger_source})

    # Step 4: guards
    guard_result = check_transition_guards(
        item=item,
        target_state=target_state,
        trigger_source=trigger_source,
        bypass_guards=bypass_guards,
    )

    # Step 5: handle deny / illegal
    if not guard_result.allowed:
        outcome: TransitionOutcome = (
            "illegal_transition" if guard_result.reason == "illegal_transition" else "guard_denied"
        )
        audit.write_communication_log(
            action_type="transition_guard_denied" if outcome == "guard_denied" else "illegal_transition",
            delivery_item_id=delivery_item_id,
            attribution={
                "trigger_source": trigger_source,
                "correlation_id": correlation_id,
                "modified_by": _modified_by_of(event_context, trigger_source),
            },
            details={
                "from_state": from_state.value,
                "to_state": target_state.value,
                "reason": guard_result.reason or "",
                "blocking_conditions": guard_result.blocking_conditions,
                "code": "TRK-W001" if outcome == "guard_denied" else "TRK-E001",
            },
        )
        return TransitionResult(
            item_id=delivery_item_id,
            from_state=from_state,
            to_state=target_state,
            outcome=outcome,
            guard_result=guard_result,
            dispatch_signal=None,
            correlation_id=correlation_id,
        )

    # Step 6: atomic transaction (sequential calls; outer transaction at task-body layer)
    now = _now()
    modified_by = _modified_by_of(event_context, trigger_source)
    field_updates = _build_field_updates(item, target_state, pm_id, trigger_source)

    storage.update_delivery_item(delivery_item_id, field_updates)
    storage.write_delivery_state(
        delivery_item_id=delivery_item_id,
        new_state=target_state,
        modified_at=now,
        modified_by=modified_by,
    )
    # SP-side reflection of the new state -- best-effort per [D-118] strict
    # boundary: Postgres is authoritative; SP write failure logs + continues,
    # NEVER rolls back the Postgres state change. The state cascade must proceed.
    #
    # Pattern per [D-137] (ratified 2026-06-28): two-step write -- first
    # resolve SP's integer Id via natural-key lookup, then update_item with
    # that Id. SP REST /items({Id}) expects an integer auto-counter, not
    # HILDA's composite delivery_item_id slug; prior direct-write code 400'd
    # silently (architect live-debug discovery 2026-06-28).
    #
    # Scope is derived from item.customer_id (read at top of this function).
    # Previously read event_context['sp_scope'] which dispatcher-driven and
    # kickoff-driven flows never set -- the entire SP-write branch silently
    # skipped, so HILDA-driven state transitions (NS->Open->Outreach Sent ->
    # Owner Closed -> Under PM Review) never reflected in SP. Surfaced by
    # architect 2026-06-28 mid-PM-approval design pass: SP UI engineer's
    # PM Approval button visibility logic depends on delivery_state being
    # current in SP.
    if sp_writer is not None:
        try:
            from core.src.sharepoint_integration.config import ListScope
            customer_id = getattr(item, "customer_id", None)
            if customer_id:
                _sp_writeback_field_updates(
                    sp_writer=sp_writer,
                    item=item,
                    customer_id=customer_id,
                    delivery_item_id=delivery_item_id,
                    field_updates=field_updates,
                )
        except Exception as exc:  # noqa: BLE001 -- best-effort, non-fatal
            logger.warning(
                "update_delivery_state: SP-side update_item failed for item=%s: %s: %s",
                delivery_item_id, type(exc).__name__, str(exc)[:120],
            )
    audit_details: dict[str, Any] = {
        "from_state": from_state.value,
        "to_state": target_state.value,
        "modified_at": now.isoformat(),
    }
    if guard_result.warnings:
        audit_details["warnings"] = guard_result.warnings
    audit.write_communication_log(
        action_type="state_transition",
        delivery_item_id=delivery_item_id,
        attribution={
            "trigger_source": trigger_source,
            "correlation_id": correlation_id,
            "modified_by": modified_by,
        },
        details=audit_details,
    )

    dispatch_signal = StateChangeDispatchSignal(
        delivery_item_id=delivery_item_id,
        old_state=from_state,
        new_state=target_state,
        correlation_id=correlation_id,
        trigger_source=trigger_source,
    )

    # Step 7: OWNER_CLOSED inline auto-advance (transient state)
    if target_state == DeliveryState.OWNER_CLOSED:
        auto_advance_result = auto_advance_owner_closed_to_under_pm_review(
            delivery_item_id=delivery_item_id,
            storage=storage,
            sp_writer=sp_writer,
            audit=audit,
            event_context=event_context,
        )
        if auto_advance_result.outcome == "transitioned":
            # Return the FINAL dispatch_signal so caller fires StateChange against
            # the observable state (UNDER_PM_REVIEW), not the transient OWNER_CLOSED.
            return TransitionResult(
                item_id=delivery_item_id,
                from_state=from_state,
                to_state=auto_advance_result.to_state,
                outcome="transitioned",
                guard_result=guard_result,
                dispatch_signal=auto_advance_result.dispatch_signal,
                correlation_id=correlation_id,
            )
        # Auto-advance failed (guard denied / illegal) -- surface the original
        # OWNER_CLOSED transition as transitioned, plus the failure via audit
        # (already logged inside auto_advance_owner_closed_to_under_pm_review).

    return TransitionResult(
        item_id=delivery_item_id,
        from_state=from_state,
        to_state=target_state,
        outcome="transitioned",
        guard_result=guard_result,
        dispatch_signal=dispatch_signal,
        correlation_id=correlation_id,
    )


def advance_on_doc_count_reached(
    delivery_item_id: str,
    storage: StorageWriter,
    sp_writer: SpWriter,
    audit: AuditWriter,
    event_context: dict[str, Any],
) -> TransitionResult:
    """Convenience wrapper for AttachmentReceived task body when doc_count
    is met for test_report items per FR-28. Equivalent to
    `update_delivery_state(target_state=DOCUMENT_RECEIVED)` -- guards
    inline-check doc_count via the OwnerClosed guard's prerequisite logic.
    """
    return update_delivery_state(
        delivery_item_id=delivery_item_id,
        target_state=DeliveryState.DOCUMENT_RECEIVED,
        params={},
        event_context=event_context,
        storage=storage,
        sp_writer=sp_writer,
        audit=audit,
    )


def auto_advance_owner_closed_to_under_pm_review(
    delivery_item_id: str,
    storage: StorageWriter,
    sp_writer: SpWriter,
    audit: AuditWriter,
    event_context: dict[str, Any],
) -> TransitionResult:
    """Transient-state auto-advance per FR-7. Called inline by
    `update_delivery_state` immediately after a successful OWNER_CLOSED
    transition completes -- OWNER_CLOSED has zero observable duration in Ph-1.

    Ph-2 extension: when FR-66 multi-revision is enabled,
    `_post_owner_closed_target(item)` may return `AwaitingVersionSelection`
    instead of `UNDER_PM_REVIEW`. The orchestration here is unchanged.
    """
    item = storage.get_delivery_item(delivery_item_id)
    target = _post_owner_closed_target(item)
    return update_delivery_state(
        delivery_item_id=delivery_item_id,
        target_state=target,
        params={},
        event_context={**event_context, "auto_advance_origin": "owner_closed"},
        storage=storage,
        sp_writer=sp_writer,
        audit=audit,
    )
