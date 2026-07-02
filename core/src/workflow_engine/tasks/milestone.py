"""milestone.py: milestone-level orchestration task bodies.

Tasks:
- MILESTONE_STORAGE_CLEANUP (FR-76) -- delegates to `storage` cleanup helper (stub
  in Ph-1 until storage implements; logs + returns).
- HALT_MILESTONE_POLLING (FR-74) -- workflow_engine-owned; modifies beat schedule
  (stub Ph-1 since beat_schedule.py is Ph-2 forward-looking per D3 cascade).
- FINAL_SWEEP (FR-74) -- workflow_engine-owned; one-shot poll burst (stub Ph-1).
- close_all_items (FR-64 Option (b) per tracker D12 cascade 2026-06-23 + tracker
  Worked Example 2): iterates CLOSE-eligible items in a milestone and calls
  `tracker.update_delivery_state(target=CLOSED, trigger_source="tpm_button")`
  per item. ACTIVE Ph-1.
"""
from __future__ import annotations

from typing import Any

from core.src.rule_engine import ActionKind
from core.src.tracker import DeliveryState, update_delivery_state

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = [
    "milestone_storage_cleanup_task",
    "halt_milestone_polling_task",
    "final_sweep_task",
    "close_all_items_task",
]


# CLOSE-eligible states per tracker MODULE.md FR-64 Option (b) Worked Example:
# SUBMITTED_TO_CUSTOMER + (READY_FOR_SUBMISSION when no_customer_upload=True).
_CLOSE_ELIGIBLE_PRIMARY = DeliveryState.SUBMITTED_TO_CUSTOMER


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.milestone.milestone_storage_cleanup")
def milestone_storage_cleanup_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """MILESTONE_STORAGE_CLEANUP per FR-76. Ph-1 stub: logs the event; real cleanup
    delegates to a storage helper that lands when storage adds the surface.

    params: optional (milestone_id falls back to event_context).
    """
    milestone_id = params.get("milestone_id") or event_context.get("milestone_id")
    deps = get_task_deps()
    deps.audit.write_communication_log(
        action_type="milestone_storage_cleanup_requested",
        delivery_item_id=None,
        attribution={
            "trigger_source":  event_context.get("trigger_source", "automated"),
            "correlation_id":  event_context.get("correlation_id", ""),
            "modified_by":     event_context.get("pm_id", "system"),
        },
        details={"milestone_id": milestone_id, "ph1_stub": True},
    )
    return {"milestone_id": milestone_id, "outcome": "logged_stub"}


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.milestone.halt_milestone_polling")
def halt_milestone_polling_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """HALT_MILESTONE_POLLING per FR-74. Ph-1 stub -- real implementation modifies
    Celery-beat schedule; beat_schedule.py is Ph-2 forward-looking per D3 cascade."""
    milestone_id = params.get("milestone_id") or event_context.get("milestone_id")
    return {"milestone_id": milestone_id, "outcome": "logged_stub"}


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.milestone.final_sweep")
def final_sweep_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """FINAL_SWEEP per FR-74. Ph-1 stub -- real implementation chains one-shot polls."""
    milestone_id = params.get("milestone_id") or event_context.get("milestone_id")
    return {"milestone_id": milestone_id, "outcome": "logged_stub"}


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.milestone.close_all_items")
def close_all_items_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """FR-64 Option (b) HILDA-owned per-item cascade for Close All Items.

    Triggered when sp_alert_parser detects `closed_all_items_triggered_at` written
    on a Milestones SP list row. Iterates over CLOSE-eligible items in the
    milestone and calls tracker.update_delivery_state per item.

    Per tracker MODULE.md FR-64 Option (b) lock 2026-06-20 + tracker D12 cascade
    2026-06-23: trigger_source="tpm_button"; per-item CLOSED transitions go
    through normal guards (which require tpm_button | manual_tpm_override
    attribution for CLOSED per DEF-20).

    params:
      - milestone_id: str (or fall back to event_context)
      - pm_id: str (TPM attribution; or fall back to event_context.pm_id)

    Returns dict with eligible_count + closed_count + skipped_count.
    """
    deps = get_task_deps()
    milestone_id = params.get("milestone_id") or event_context.get("milestone_id")
    if milestone_id is None:
        raise ValueError("close_all_items requires milestone_id")

    pm_id = params.get("pm_id") or event_context.get("pm_id") or "tpm_unknown"

    # Storage Protocol optional helper: list_items_for_milestone(milestone_id, states=...)
    list_items = getattr(deps.storage, "list_items_for_milestone", None)
    if list_items is None:
        # No way to enumerate eligible items; log + return.
        deps.audit.write_communication_log(
            action_type="close_all_items_no_storage_helper",
            delivery_item_id=None,
            attribution={
                "trigger_source":  "tpm_button",
                "correlation_id":  event_context.get("correlation_id", ""),
                "modified_by":     pm_id,
            },
            details={"milestone_id": milestone_id, "ph1_stub": True},
        )
        return {
            "milestone_id":    milestone_id,
            "eligible_count":  0,
            "closed_count":    0,
            "skipped_count":   0,
            "outcome":         "no_storage_helper_stub",
        }

    candidates = list_items(milestone_id, states={
        DeliveryState.SUBMITTED_TO_CUSTOMER,
        DeliveryState.READY_FOR_SUBMISSION,
    })

    eligible = []
    for item in candidates:
        state = getattr(item, "delivery_state", None)
        if state == DeliveryState.SUBMITTED_TO_CUSTOMER:
            eligible.append(item)
        elif state == DeliveryState.READY_FOR_SUBMISSION and getattr(item, "no_customer_upload", False):
            eligible.append(item)

    closed = 0
    skipped = 0
    per_item_ctx = dict(event_context)
    per_item_ctx["trigger_source"] = "tpm_button"
    per_item_ctx["pm_id"] = pm_id
    for item in eligible:
        item_id = getattr(item, "item_id", None) or getattr(item, "delivery_item_id", None)
        if item_id is None:
            skipped += 1
            continue
        result = update_delivery_state(
            delivery_item_id=item_id,
            target_state=DeliveryState.CLOSED,
            params={"closed_via": "fr64_batch"},
            event_context=per_item_ctx,
            storage=deps.storage,
            sp_writer=deps.sp_writer,
            audit=deps.audit,
        )
        if result.outcome in ("transitioned", "no_op_idempotent"):
            closed += 1
        else:
            skipped += 1

    return {
        "milestone_id":   milestone_id,
        "eligible_count": len(eligible),
        "closed_count":   closed,
        "skipped_count":  skipped,
        "outcome":        "completed",
    }


register_task_binding(TaskBinding(
    action_kind=ActionKind.MILESTONE_STORAGE_CLEANUP,
    celery_task=milestone_storage_cleanup_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.HALT_MILESTONE_POLLING,
    celery_task=halt_milestone_polling_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.FINAL_SWEEP,
    celery_task=final_sweep_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.CLOSE_ALL_ITEMS,
    celery_task=close_all_items_task,
    queue="default",
))
