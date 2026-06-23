"""FR-31 sub-3 manual-trigger handler.

TPM clicks a per-row SP UI button (Send Reminder, Approve, etc.) -> SP UI engineer
writes a HILDA-managed timestamp column (last_reminder_triggered_at = <now>) ->
SP-alert email fires -> email_service.sp_alert_parser detects the column change ->
dispatches the corresponding ActionKind via this handler (bypasses rule_engine
per workflow_engine MODULE.md Invariant).

Note 2026-06-23: per rule_engine D5 cascade, FR-31 sub-3 is already covered by
the existing per-action button + HILDA-managed timestamp column pattern -- no
generic 'manual_trigger_rule' button needed. Each rule that supports manual
trigger has its own per-row button + timestamp column; sp_alert_parser maps
column-change -> ActionKind and dispatches here.
"""
from __future__ import annotations

from typing import Any

from core.src.diagnostics import PipelineError
from core.src.rule_engine import ActionKind

from .registry import lookup_for_manual_trigger

__all__ = ["handle_manual_trigger"]


def handle_manual_trigger(
    delivery_item_id: str,
    action_kind: ActionKind,
    params: dict[str, Any],
    invoked_by_pm_id: str,
    correlation_id: str,
    customer_id: str | None = None,
    milestone_id: str | None = None,
    device_id: str | None = None,
) -> str:
    """Per FR-31 sub-3: TPM SP UI manual trigger dispatch bypasses rule_engine.

    Looks up action_kind in ACTION_KIND_TO_TASK; calls task.apply_async with the
    `trigger_source='manual'` flag set in event_context so downstream task body
    logs trigger_source='manual' in CommunicationLog.

    Returns the Celery task ID.

    Raises WFL-E001 if action_kind not in registry. State-filter logic (WFL-E002 --
    "action not applicable to item's current state") lives in sp_alert_parser
    upstream, not in this function -- by the time the SP-alert email reaches HILDA,
    the SP UI has already filtered the visible buttons per FR-31 sub-3 spec.
    """
    binding = lookup_for_manual_trigger(action_kind)

    event_context: dict[str, Any] = {
        "correlation_id":   correlation_id,
        "customer_id":      customer_id,                 # per [D-091] slug->id rename
        "device_id":        device_id,                   # per [D-091]
        "milestone_id":     milestone_id,
        "delivery_item_id": delivery_item_id,
        "trigger_source":   "manual",                    # downstream task logs this
        "invoked_by_pm_id": invoked_by_pm_id,
    }

    async_result = binding.celery_task.apply_async(args=(params, event_context))
    return async_result.id
