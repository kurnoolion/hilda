"""routing_resolution.py: FR-82 / FR-83 routing-resolution task bodies.

Tasks:
- REASSIGN_DOCUMENT_TO_WORK_ITEM (FR-83) -- delegates to
  tracker.reassign_document_to_workitem.
- PROPAGATE_TAGS_TO_ACTIVE_TRACKERS (FR-82) -- delegates to
  tracker.propagate_tags_to_active_trackers (nested tag-set per architect lock
  2026-06-20).
- REARM_DEADLINE_PROXIMITY (FR-11) -- workflow_engine-owned; logs the re-arm
  event (concrete rule_engine cache invalidation lands when scheduler tick
  source activates).
"""
from __future__ import annotations

from typing import Any

from core.src.rule_engine import ActionKind
from core.src.tracker import (
    propagate_tags_to_active_trackers,
    reassign_document_to_workitem,
)

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = [
    "reassign_document_to_work_item_task",
    "propagate_tags_to_active_trackers_task",
    "rearm_deadline_proximity_task",
]


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.routing_resolution.reassign_document_to_work_item")
def reassign_document_to_work_item_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """REASSIGN_DOCUMENT_TO_WORK_ITEM -> tracker.reassign_document_to_workitem.

    params:
      - file_hash: str
      - source_item_id: str (default WI)
      - target_item_id: str (TPM-chosen)
      - original_filename: str (optional; for [D-039] Step 1 slug derivation)
    """
    deps = get_task_deps()
    file_hash = params["file_hash"]
    source_item_id = params["source_item_id"]
    target_item_id = params["target_item_id"]
    original_filename = params.get("original_filename", "")
    pm_id = event_context.get("pm_id") or event_context.get("invoked_by_pm_id") or "tpm_unknown"

    result = reassign_document_to_workitem(
        file_hash=file_hash,
        source_item_id=source_item_id,
        target_item_id=target_item_id,
        pm_id=pm_id,
        storage=deps.storage,
        sp_writer=deps.sp_writer,
        audit=deps.audit,
        event_context=event_context,
        original_filename=original_filename,
    )
    return {
        "file_hash":                result.file_hash,
        "target_item_id":           result.target_item_id,
        "revision_classification":  result.revision_classification,
        "rev_number":               result.rev_number,
        "rederived_doc_type":       result.rederived_doc_type.value,
        "upload_dispatched":        result.upload_dispatched,
    }


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.routing_resolution.propagate_tags_to_active_trackers")
def propagate_tags_to_active_trackers_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """PROPAGATE_TAGS_TO_ACTIVE_TRACKERS -> tracker.propagate_tags_to_active_trackers.

    params:
      - customer_id: str (was customer_slug pre-[D-091])
      - tg_name: str
      - item_no: int
      - new_tags: list[list[str]] | None (nested tag-set per FR-82 architect lock
        2026-06-20)
    """
    deps = get_task_deps()
    customer_id = params.get("customer_id") or event_context.get("customer_id")
    if customer_id is None:
        raise ValueError("propagate_tags requires customer_id")
    pm_id = event_context.get("pm_id") or "system"

    result = propagate_tags_to_active_trackers(
        customer_id=customer_id,
        tg_name=params["tg_name"],
        item_no=int(params["item_no"]),
        new_tags=params.get("new_tags"),
        pm_id=pm_id,
        storage=deps.storage,
        sp_writer=deps.sp_writer,
        audit=deps.audit,
        event_context=event_context,
    )
    return {
        "customer_id":       result.customer_id,
        "tg_name":           result.tg_name,
        "item_no":           result.item_no,
        "propagated_count":  result.propagated_count,
        "skipped_count":     result.skipped_count,
    }


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.routing_resolution.rearm_deadline_proximity")
def rearm_deadline_proximity_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """REARM_DEADLINE_PROXIMITY (FR-11 re-arm on DeadlineMoved sub-trigger per
    [D-085] cascade) -- workflow_engine-owned. Ph-1: logs the re-arm event so
    downstream scheduler tick will pick up the new Milestone.target_date.

    params:
      - milestone_id: str (or fall back to event_context)
    """
    deps = get_task_deps()
    milestone_id = params.get("milestone_id") or event_context.get("milestone_id")
    deps.audit.write_communication_log(
        action_type="deadline_proximity_rearmed",
        delivery_item_id=None,
        attribution={
            "trigger_source":  event_context.get("trigger_source", "automated"),
            "correlation_id":  event_context.get("correlation_id", ""),
            "modified_by":     event_context.get("pm_id", "system"),
        },
        details={"milestone_id": milestone_id},
    )
    return {"milestone_id": milestone_id, "outcome": "rearmed"}


register_task_binding(TaskBinding(
    action_kind=ActionKind.REASSIGN_DOCUMENT_TO_WORK_ITEM,
    celery_task=reassign_document_to_work_item_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.PROPAGATE_TAGS_TO_ACTIVE_TRACKERS,
    celery_task=propagate_tags_to_active_trackers_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.REARM_DEADLINE_PROXIMITY,
    celery_task=rearm_deadline_proximity_task,
    queue="default",
))
