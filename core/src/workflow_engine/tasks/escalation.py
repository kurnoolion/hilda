"""escalation.py: NOTIFY_PM + NOTIFY_HILDA_OPS task bodies (workflow-engine-owned).

These are workflow-engine-owned because they write CommunicationLog rows directly
(no external module ownership). The ESCALATE action delegates to a `messenger`
module that doesn't yet exist (Ph-1 stub registration deferred until messenger
lands).

NOTIFY_PM and NOTIFY_HILDA_OPS surface alerts to dashboard via CommunicationLog
audit rows -- dashboard's PM/ops views query CommunicationLog filtered by
action_type.
"""
from __future__ import annotations

from typing import Any

from core.src.rule_engine import ActionKind

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["notify_pm_task", "notify_hilda_ops_task"]


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.escalation.notify_pm")
def notify_pm_task(params: dict[str, Any], event_context: dict[str, Any]) -> dict[str, Any]:
    """NOTIFY_PM per FR-75 -- writes CommunicationLog row with action_type=notify_pm.

    Dashboard's PM dashboard query filters on this action_type for surfacing.

    params:
      - urgency: str ("low" / "medium" / "high")
      - recipient: str (optional; defaults to assigned PM)
      - reason: str (bounded enum token per NFR-2; no free-text)
    """
    deps = get_task_deps()
    urgency = params.get("urgency", "low")
    recipient = params.get("recipient", "assigned_pm")
    reason = params.get("reason", "rule_fired")
    delivery_item_id = event_context.get("delivery_item_id")

    deps.audit.write_communication_log(
        action_type="notify_pm",
        delivery_item_id=delivery_item_id,
        attribution={
            "trigger_source":  event_context.get("trigger_source", "automated"),
            "correlation_id":  event_context.get("correlation_id", ""),
            "modified_by":     event_context.get("pm_id", "system"),
        },
        details={
            "urgency":          urgency,
            "recipient":        recipient,
            "reason":           reason,
            "milestone_id":     event_context.get("milestone_id"),
        },
    )
    return {"urgency": urgency, "recipient": recipient, "outcome": "logged"}


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.escalation.notify_hilda_ops")
def notify_hilda_ops_task(params: dict[str, Any], event_context: dict[str, Any]) -> dict[str, Any]:
    """NOTIFY_HILDA_OPS per FR-75 (HildaOpsAlert) -- writes CommunicationLog row
    with action_type=notify_hilda_ops. Dashboard's ops view surfaces these for
    HILDA team triage.

    params:
      - severity: str ("warning" / "error")
      - alert_code: str (e.g., "STR-W001" -- bounded; from diagnostics registry)
      - context_summary: dict[str, Any] (bounded keys per NFR-2)
    """
    deps = get_task_deps()
    severity = params.get("severity", "warning")
    alert_code = params.get("alert_code", "UNKNOWN")
    context_summary = params.get("context_summary", {})
    delivery_item_id = event_context.get("delivery_item_id")

    deps.audit.write_communication_log(
        action_type="notify_hilda_ops",
        delivery_item_id=delivery_item_id,
        attribution={
            "trigger_source":  event_context.get("trigger_source", "automated"),
            "correlation_id":  event_context.get("correlation_id", ""),
            "modified_by":     "system",
        },
        details={
            "severity":          severity,
            "alert_code":        alert_code,
            "context_summary":   context_summary,
            "milestone_id":      event_context.get("milestone_id"),
        },
    )
    return {"severity": severity, "alert_code": alert_code, "outcome": "logged"}


register_task_binding(TaskBinding(
    action_kind=ActionKind.NOTIFY_PM,
    celery_task=notify_pm_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.NOTIFY_HILDA_OPS,
    celery_task=notify_hilda_ops_task,
    queue="default",
))
