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
from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["apply_tpm_sp_close_task"]

_log = logging.getLogger(__name__)


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
