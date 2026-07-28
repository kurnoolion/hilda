"""apply_tpm_sp_close_task — mirror SP-authored `delivery_state=Closed` to
local Postgres per architect 2026-07-23.

Scope: TPM clicks Close on an item in SP UI (typically before Start
Collection for a not-applicable item, but the code works from any prior
state — SP is authoritative). SP UI writes `delivery_state="Closed"` to the
SP row, SP fires CHANGED alert. HILDA's dispatcher promotes `delivery_state`
into derived_fields via the standard sync_deliverable_fields pipeline; but
the sync task's whitelist intentionally excludes `delivery_state` (echo
defense against HILDA's own writes). This task fills that gap.

Pattern A per [D-068]: SP is authoritative for `delivery_state` when
sourced from SP UI. HILDA mirrors the field to local Postgres
unconditionally — no state machine legality check, no Guard 5 attribution
enforcement. The CHANGED alert itself IS the authority (SP won't fire a
CHANGED alert unless something actually wrote to the SP row).

Ph-1 simplification per architect 2026-07-23:
- Fire whenever the new value of `delivery_state` in the field_deltas
  is exactly "Closed" (case-insensitive).
- Direct-write Postgres delivery_state='Closed' + set actual_completion_date
  to now.
- Audit: action_type='tpm_sp_close_synced' with the prior value + correlation_id.
- No SP writeback (SP already has Closed — it's the source).
- No state machine post-transition cascade fires. Ph-1 TPM early-close is
  self-contained; Ph-2 revisit if closing an item mid-pipeline should fire
  downstream rules.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.src.rule_engine import ActionKind
from core.src.template_schema.enums import DeliveryState
from core.src.tracker.transitions import update_delivery_state
from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["apply_tpm_sp_close_task", "apply_tpm_sp_close_in_progress_task"]

_log = logging.getLogger(__name__)


def _extract_delta_new_value(deltas: dict[str, Any], key: str) -> tuple[Any, Any]:
    """Return (prior_value, new_value) from a field_deltas entry. Tolerant to
    (prior, new) tuple/list serialization AND bare-scalar dispatch shapes.
    Shared by apply_tpm_sp_close_task + apply_tpm_sp_close_in_progress_task."""
    prior_value = None
    new_value = None
    if key in deltas:
        entry = deltas[key]
        if isinstance(entry, (tuple, list)) and len(entry) >= 2:
            prior_value = entry[0]
            new_value = entry[1]
        else:
            new_value = entry
    return prior_value, new_value


@hilda_celery_app.task(
    name="core.src.workflow_engine.tasks.tpm_sp_close.apply_tpm_sp_close",
)
def apply_tpm_sp_close_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """Mirror SP-authored delivery_state='Closed' to local Postgres.

    Guarded by the matching rule's condition (merged.delivery_state='Closed').
    Idempotent: repeated invocations write the same value; safe under Celery
    at-least-once retry semantics.

    Returns telemetry dict with outcome + delivery_item_id.
    """
    deps = get_task_deps()
    delivery_item_id = event_context.get("delivery_item_id")
    if not delivery_item_id:
        _log.warning("apply_tpm_sp_close: missing delivery_item_id in event_context")
        return {"outcome": "skipped_missing_item_id"}

    # Extract prior value from field_deltas for audit context (tolerant to
    # tuple/list serialization via Celery JSON).
    deltas = event_context.get("field_deltas") or {}
    prior_value = None
    new_value = None
    if "delivery_state" in deltas:
        entry = deltas["delivery_state"]
        if isinstance(entry, (tuple, list)) and len(entry) >= 2:
            prior_value = entry[0]
            new_value = entry[1]
        else:
            new_value = entry

    # Defensive: only fire if the new value is literally "Closed" (matches
    # the YAML rule condition; guards against a stray dispatch).
    if not (isinstance(new_value, str) and new_value.strip().lower() == "closed"):
        _log.info(
            "apply_tpm_sp_close: skipping delivery_item_id=%s — new value %r is not 'Closed'",
            delivery_item_id, new_value,
        )
        return {
            "outcome": "skipped_not_closed",
            "delivery_item_id": delivery_item_id,
            "new_value": new_value,
        }

    now_utc = datetime.now(timezone.utc)
    try:
        deps.storage.update_delivery_item(delivery_item_id, {
            "delivery_state": "Closed",
            "actual_completion_date": now_utc.date(),
            "last_updated": now_utc,
        })
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "apply_tpm_sp_close: storage write failed for delivery_item_id=%s: %s: %s",
            delivery_item_id, type(exc).__name__, str(exc)[:120],
        )
        return {
            "outcome": "storage_write_failed",
            "delivery_item_id": delivery_item_id,
            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
        }

    # Audit — best-effort; failure must NOT roll back the mirror write.
    try:
        deps.audit.write_communication_log(
            action_type="tpm_sp_close_synced",
            delivery_item_id=delivery_item_id,
            attribution={
                "trigger_source": "sp_ui_delivery_state_write",
                "correlation_id": event_context.get("correlation_id", ""),
                "modified_by":    "sp_ui:tpm_click",
            },
            details={
                "delivery_item_id": delivery_item_id,
                "prior_value":      prior_value,
                "new_value":        "Closed",
                "synced_at":        now_utc.isoformat(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "apply_tpm_sp_close: audit write failed for delivery_item_id=%s: %s",
            delivery_item_id, str(exc)[:120],
        )

    _log.info(
        "apply_tpm_sp_close: synced delivery_item_id=%s prior=%s -> Closed",
        delivery_item_id, prior_value,
    )
    return {
        "outcome":          "synced",
        "delivery_item_id": delivery_item_id,
        "prior_value":      prior_value,
    }


# Register binding so the workflow_engine dispatcher routes
# APPLY_TPM_SP_CLOSE rule actions to this Celery task.
register_task_binding(TaskBinding(
    action_kind=ActionKind.APPLY_TPM_SP_CLOSE,
    celery_task=apply_tpm_sp_close_task,
    queue="default",
))


# ---------------------------------------------------------------------------
# CIP-1 (2026-07-28): apply_tpm_sp_close_in_progress_task -- 2-hop
# ---------------------------------------------------------------------------
#
# Per-item TPM close serialization design 2026-07-28. Prior flow:
#   TPM clicks Close in SP UI -> SP row delivery_state='Closed' immediately
#   (~90s until HILDA sees the CHANGED alert + processes) -> during that
#   window, other clicks in SP UI (Start Collection, etc.) still enabled.
# New flow:
#   TPM clicks Close -> SP UI writes delivery_state='CloseInProgress' -> SP
#   UI immediately hides Start Collection etc. (button visibility check
#   treats CloseInProgress same as Closed) -> HILDA receives CHANGED alert
#   with new_value='CloseInProgress' -> apply_tpm_sp_close_in_progress_task:
#     Hop 1: mirror CloseInProgress to Postgres (direct storage write, same
#            pattern as apply_tpm_sp_close_task).
#     Hop 2: advance to CLOSED via update_delivery_state with
#            bypass_guards=True + trigger_source='manual_tpm_override'.
#            update_delivery_state handles SP writeback + state_changed_at.
#   -> item lands at CLOSED in Postgres AND SP. SP fires echo CHANGED alert
#   -> dispatched to apply_tpm_sp_close_task (existing) -> idempotent no-op.
#
# Reconciler backstop (CIP-4): sweep items stuck at CloseInProgress for >
# elapsed_threshold_sec and force-advance -- guards against worker crash
# between the two hops. Dormant when config/reconcile.json enabled=false;
# that's fine for Ph-1 (crash window is < 1s).


@hilda_celery_app.task(
    name="core.src.workflow_engine.tasks.tpm_sp_close.apply_tpm_sp_close_in_progress",
)
def apply_tpm_sp_close_in_progress_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """2-hop: mirror CloseInProgress -> advance to CLOSED.

    Only fires when the delivery_state new value in field_deltas is exactly
    'CloseInProgress' (case-insensitive). Idempotent: repeated invocations
    with the same event_context re-hop CLOSE_IN_PROGRESS -> CLOSED; hop 2's
    update_delivery_state no-ops if state already CLOSED.

    Returns telemetry dict with outcome + delivery_item_id + prior_value.
    """
    deps = get_task_deps()
    delivery_item_id = event_context.get("delivery_item_id")
    if not delivery_item_id:
        _log.warning(
            "apply_tpm_sp_close_in_progress: missing delivery_item_id in event_context"
        )
        return {"outcome": "skipped_missing_item_id"}

    deltas = event_context.get("field_deltas") or {}
    prior_value, new_value = _extract_delta_new_value(deltas, "delivery_state")

    # Defensive: only fire on literal 'CloseInProgress' (case-insensitive,
    # stripped). Guards against stray dispatch.
    if not (
        isinstance(new_value, str)
        and new_value.strip().lower() == "closeinprogress"
    ):
        _log.info(
            "apply_tpm_sp_close_in_progress: skipping delivery_item_id=%s "
            "-- new value %r is not 'CloseInProgress'",
            delivery_item_id, new_value,
        )
        return {
            "outcome": "skipped_not_close_in_progress",
            "delivery_item_id": delivery_item_id,
            "new_value": new_value,
        }

    # ---------- Hop 1: mirror CloseInProgress to Postgres ----------
    now_utc = datetime.now(timezone.utc)
    try:
        deps.storage.update_delivery_item(delivery_item_id, {
            "delivery_state": "CloseInProgress",
            "last_updated":   now_utc,
        })
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "apply_tpm_sp_close_in_progress: hop1 storage write failed for "
            "delivery_item_id=%s: %s: %s",
            delivery_item_id, type(exc).__name__, str(exc)[:120],
        )
        return {
            "outcome": "hop1_storage_write_failed",
            "delivery_item_id": delivery_item_id,
            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
        }

    # Audit hop 1 -- best-effort; failure MUST NOT roll back the mirror.
    try:
        deps.audit.write_communication_log(
            action_type="tpm_sp_close_in_progress_mirrored",
            delivery_item_id=delivery_item_id,
            attribution={
                "trigger_source": "sp_ui_delivery_state_write",
                "correlation_id": event_context.get("correlation_id", ""),
                "modified_by":    "sp_ui:tpm_click",
            },
            details={
                "delivery_item_id": delivery_item_id,
                "prior_value":      prior_value,
                "new_value":        "CloseInProgress",
                "synced_at":        now_utc.isoformat(),
                "hop":              1,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "apply_tpm_sp_close_in_progress: hop1 audit failed for "
            "delivery_item_id=%s: %s",
            delivery_item_id, str(exc)[:120],
        )

    # ---------- Hop 2: advance to CLOSED (writes Postgres + SP) ----------
    # bypass_guards=True + trigger_source='manual_tpm_override' is the
    # existing CLOSE-1 escape hatch. Reused here because the CloseInProgress
    # -> CLOSED hop is an authoritative echo of TPM's explicit close intent
    # (TPM already clicked Close; we're finishing the state machine).
    hop2_ctx = {
        **event_context,
        "trigger_source":   "manual_tpm_override",
        "delivery_item_id": delivery_item_id,
    }
    try:
        result = update_delivery_state(
            delivery_item_id=delivery_item_id,
            target_state=DeliveryState.CLOSED,
            params={"closed_via": "close_in_progress_advance"},
            event_context=hop2_ctx,
            storage=deps.storage,
            sp_writer=deps.sp_writer,
            audit=deps.audit,
            bypass_guards=True,
        )
    except Exception as exc:  # noqa: BLE001
        # Hop 2 raise leaves item at CLOSE_IN_PROGRESS in Postgres. The
        # reconciler sync-6 sweeper (CIP-4) picks up stragglers when it's
        # enabled; corp Ph-1 has reconciler disabled so this is a manual
        # SQL cleanup case. Rare (< 1s window, SP write inside hop 2 is
        # already best-effort per D-118).
        _log.error(
            "apply_tpm_sp_close_in_progress: hop2 advance raised for "
            "delivery_item_id=%s: %s: %s (item left at CloseInProgress; "
            "reconciler sync-6 will recover if enabled)",
            delivery_item_id, type(exc).__name__, str(exc)[:120],
        )
        return {
            "outcome": "hop2_advance_raised",
            "delivery_item_id": delivery_item_id,
            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
        }

    if result.outcome not in ("transitioned", "no_op_idempotent"):
        # Guard denial or other non-success. Log + surface -- reconciler
        # sync-6 catches this too.
        _log.warning(
            "apply_tpm_sp_close_in_progress: hop2 advance not transitioned "
            "for delivery_item_id=%s (outcome=%s)",
            delivery_item_id, result.outcome,
        )
        return {
            "outcome": f"hop2_{result.outcome}",
            "delivery_item_id": delivery_item_id,
            "prior_value": prior_value,
        }

    _log.info(
        "apply_tpm_sp_close_in_progress: 2-hop complete delivery_item_id=%s "
        "prior=%s -> CloseInProgress -> Closed",
        delivery_item_id, prior_value,
    )
    return {
        "outcome":          "closed",
        "delivery_item_id": delivery_item_id,
        "prior_value":      prior_value,
    }


register_task_binding(TaskBinding(
    action_kind=ActionKind.APPLY_TPM_SP_CLOSE_IN_PROGRESS,
    celery_task=apply_tpm_sp_close_in_progress_task,
    queue="default",
))
