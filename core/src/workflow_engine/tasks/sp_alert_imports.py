"""sp_alert_imports.py: IMPORT_DELIVERABLE_TRACKER + KICKOFF_COLLECTION task bodies.

Added 2026-06-26 per [D-118] strict-boundary cascade: SP UI engineer owns ALL
SP row creation (Milestones + Deliverables + Default WI). HILDA's role becomes
listen-only -- import each Deliverable into local storage on SP ADDED alert,
then fire ItemCreated TriggerEvents when TPM clicks Start Collection.

This module hosts:
- import_deliverable_tracker_task: parses SP ADDED alert body_kvs into a
  DeliveryItemBase + creates the local tracker via storage.create_delivery_item
  (Protocol method added in Chunk 1 / commit 62fa8ce).
- kickoff_collection_task: reads all DeliveryItem trackers for the milestone,
  fires ItemCreated TriggerEvents per matching tracker (force_tracking_enabled
  AND item_type != Confirmation). This is what fires the
  send_initial_outreach_on_collection_start rule.

Both bodies are STUBS in Chunk 2 (this commit) -- they register their
TaskBindings with the workflow_engine registry but raise NotImplementedError
when invoked. Real implementations land in Chunks 3 + 4.
"""
from __future__ import annotations

from typing import Any

from core.src.rule_engine import ActionKind

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding

__all__ = ["import_deliverable_tracker_task", "kickoff_collection_task"]


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.sp_alert_imports.import_deliverable_tracker")
def import_deliverable_tracker_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """IMPORT_DELIVERABLE_TRACKER -> create local DeliveryItem tracker from SP
    ADDED alert body_kvs.

    params: (none consumed from rule_engine; all needed values come from
            event_context which carries the parsed alert's body_kvs +
            routing_key per sp_alert_parser TriggerEvent shape)

    event_context: standard event_context plus:
      - customer_id: str
      - milestone_id: str
      - body_kvs: dict[str, str]  (parsed SP alert body fields)
      - device_id: str  (resolved from body project_model or template lookup)

    Chunk 2 stub: raises NotImplementedError. Real body lands in Chunk 3.
    """
    raise NotImplementedError(
        "import_deliverable_tracker_task body is a Chunk 2 stub; "
        "real implementation lands in Chunk 3 of [D-118] cascade per "
        "STATUS.md 2026-06-26 EVENING Flag item (g)."
    )


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.sp_alert_imports.kickoff_collection")
def kickoff_collection_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """KICKOFF_COLLECTION -> fire ItemCreated TriggerEvents for all Deliverable
    trackers in the milestone that should receive outreach.

    Triggered by Milestones CHANGED alert with field_deltas containing
    milestone_collection_started_at (per architect direction 2026-06-26).

    params:
      - target_state: str (optional; default "Outreach Sent" per [D-124])

    event_context: standard event_context plus:
      - customer_id: str
      - milestone_id: str
      - field_deltas: dict[str, str] (includes milestone_collection_started_at)

    Chunk 2 stub: raises NotImplementedError. Real body lands in Chunk 4.
    """
    raise NotImplementedError(
        "kickoff_collection_task body is a Chunk 2 stub; "
        "real implementation lands in Chunk 4 of [D-118] cascade per "
        "STATUS.md 2026-06-26 EVENING Flag item (g)."
    )


register_task_binding(TaskBinding(
    action_kind=ActionKind.IMPORT_DELIVERABLE_TRACKER,
    celery_task=import_deliverable_tracker_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.KICKOFF_COLLECTION,
    celery_task=kickoff_collection_task,
    queue="default",
))
