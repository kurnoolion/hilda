"""outreach.py: SEND_INITIAL_OUTREACH + SEND_REMINDER + NOTIFY_NEW_OWNER task bodies.

Per FR-9 (initial outreach) + FR-10 (reminders) + FR-2/FR-83 (new owner notify).
Each task delegates to email_service.EmailSender for actual SMTP send; on Ph-1
worker setups without email_sender wired into TaskDeps, the task degrades to
audit-only (CommunicationLog row written, send skipped).

Pattern matches escalation.py (notify_pm_task / notify_hilda_ops_task) for
audit-log shape; extends with conditional downstream-module dispatch.
"""
from __future__ import annotations

import logging
from typing import Any

from core.src.rule_engine import ActionKind

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = [
    "send_initial_outreach_task",
    "send_reminder_task",
    "notify_new_owner_task",
]

_log = logging.getLogger(__name__)


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.outreach.send_initial_outreach")
def send_initial_outreach_task(
    params: dict[str, Any], event_context: dict[str, Any]
) -> dict[str, Any]:
    """SEND_INITIAL_OUTREACH per FR-9 -- send first outreach email to owner(s).

    params:
      - template:  str (e.g., "standard_owner_outreach")
      - channel:   str (default "email"; "messenger" Ph-2 fallback)
      - recipient: str (optional override; defaults to DI owner_corp_usa_email)

    event_context: standard (correlation_id, customer_id, milestone_id,
    delivery_item_id, trigger_source).

    Behavior:
    - Resolves recipient from event_context.recipient OR DI owner identity.
    - When deps.email_sender is wired: composes outreach via email_service
      compose_outreach + dispatches via email_sender.send -> Message-ID.
    - When deps.email_sender is None (Ph-1 worker not fully wired): audit-only.
    """
    deps = get_task_deps()
    template = params.get("template", "standard_owner_outreach")
    channel = params.get("channel", "email")
    recipient = params.get("recipient") or event_context.get("owner_corp_usa_email")
    delivery_item_id = event_context.get("delivery_item_id")

    message_id = None
    if channel == "email" and deps.email_sender is not None and recipient:
        try:
            message_id = _send_email(
                deps,
                to=recipient,
                subject=f"[HILDA] Document request -- BATCH-{event_context.get('correlation_id', '')[:8]}",
                body_marker=f"send_initial_outreach: template={template}",
            )
        except Exception as e:  # noqa: BLE001 -- audit-best-effort
            _log.warning("send_initial_outreach email send failed: %s", type(e).__name__)

    deps.audit.write_communication_log(
        action_type="send_initial_outreach",
        delivery_item_id=delivery_item_id,
        attribution={
            "trigger_source": event_context.get("trigger_source", "automated"),
            "correlation_id": event_context.get("correlation_id", ""),
            "modified_by":    event_context.get("pm_id", "system"),
        },
        details={
            "template":      template,
            "channel":       channel,
            "recipient":     recipient,
            "milestone_id":  event_context.get("milestone_id"),
            "message_id":    message_id,
            "send_skipped":  message_id is None,
        },
    )
    return {
        "template":     template,
        "channel":      channel,
        "recipient":    recipient,
        "message_id":   message_id,
        "outcome":      "sent" if message_id else "audit_only",
    }


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.outreach.send_reminder")
def send_reminder_task(
    params: dict[str, Any], event_context: dict[str, Any]
) -> dict[str, Any]:
    """SEND_REMINDER per FR-10 -- send Nth reminder email reusing BATCH-id for thread continuity.

    params:
      - template:        str (e.g., "standard_owner_reminder")
      - channel:         str (default "email")
      - reminder_count:  int (1-indexed; passed to template for "1st/2nd/3rd reminder" copy)

    event_context: standard.

    Behavior matches send_initial_outreach (audit-only when email_sender None).
    """
    deps = get_task_deps()
    template = params.get("template", "standard_owner_reminder")
    channel = params.get("channel", "email")
    reminder_count = params.get("reminder_count", 1)
    recipient = params.get("recipient") or event_context.get("owner_corp_usa_email")
    delivery_item_id = event_context.get("delivery_item_id")

    message_id = None
    if channel == "email" and deps.email_sender is not None and recipient:
        try:
            message_id = _send_email(
                deps,
                to=recipient,
                subject=f"[HILDA] Reminder #{reminder_count} -- BATCH-{event_context.get('correlation_id', '')[:8]}",
                body_marker=f"send_reminder: template={template} count={reminder_count}",
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("send_reminder email send failed: %s", type(e).__name__)

    deps.audit.write_communication_log(
        action_type="send_reminder",
        delivery_item_id=delivery_item_id,
        attribution={
            "trigger_source": event_context.get("trigger_source", "automated"),
            "correlation_id": event_context.get("correlation_id", ""),
            "modified_by":    "system",
        },
        details={
            "template":       template,
            "channel":        channel,
            "recipient":      recipient,
            "reminder_count": reminder_count,
            "milestone_id":   event_context.get("milestone_id"),
            "message_id":     message_id,
            "send_skipped":   message_id is None,
        },
    )
    return {
        "template":       template,
        "channel":        channel,
        "reminder_count": reminder_count,
        "recipient":      recipient,
        "message_id":     message_id,
        "outcome":        "sent" if message_id else "audit_only",
    }


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.outreach.notify_new_owner")
def notify_new_owner_task(
    params: dict[str, Any], event_context: dict[str, Any]
) -> dict[str, Any]:
    """NOTIFY_NEW_OWNER per FR-2 + FR-83 -- email new owner after reassignment.

    Fired by rule_engine when owner_corp_id (or related owner field) appears in
    field_deltas of an ItemModified TriggerEvent per sp_alert_parser 2026-06-27
    cascade.

    params:
      - template: str (default "owner_reassignment_notice")
      - channel:  str (default "email")

    event_context: standard + new owner identity already denormalized in
    event_context (owner_corp_usa_email / owner_corp_id).
    """
    deps = get_task_deps()
    template = params.get("template", "owner_reassignment_notice")
    channel = params.get("channel", "email")
    recipient = params.get("recipient") or event_context.get("owner_corp_usa_email")
    delivery_item_id = event_context.get("delivery_item_id")

    message_id = None
    if channel == "email" and deps.email_sender is not None and recipient:
        try:
            message_id = _send_email(
                deps,
                to=recipient,
                subject=f"[HILDA] You've been assigned a deliverable -- {event_context.get('item_title', '')}",
                body_marker=f"notify_new_owner: template={template}",
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("notify_new_owner email send failed: %s", type(e).__name__)

    deps.audit.write_communication_log(
        action_type="notify_new_owner",
        delivery_item_id=delivery_item_id,
        attribution={
            "trigger_source": event_context.get("trigger_source", "automated"),
            "correlation_id": event_context.get("correlation_id", ""),
            "modified_by":    "system",
        },
        details={
            "template":     template,
            "channel":      channel,
            "recipient":    recipient,
            "milestone_id": event_context.get("milestone_id"),
            "message_id":   message_id,
            "send_skipped": message_id is None,
        },
    )
    return {
        "template":   template,
        "channel":    channel,
        "recipient":  recipient,
        "message_id": message_id,
        "outcome":    "sent" if message_id else "audit_only",
    }


# ---------------------------------------------------------------------------
# Internal -- single async-call helper bridging the email_service Protocol
# (async) to Celery's sync task body.
# ---------------------------------------------------------------------------


def _send_email(deps: Any, *, to: str, subject: str, body_marker: str) -> str:
    """Sync-bridge to deps.email_sender.send(...). Returns Message-ID.

    Body composition Ph-1: minimal marker string. Real composer (Jinja2 templates
    + per-customer variables) lands when worker boot wires the full compose_*
    helpers from email_service per integration test cycle.
    """
    import asyncio

    coro = deps.email_sender.send(
        to=[to],
        cc=[],
        subject=subject,
        body=body_marker,
    )
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Fallback: run in new loop -- Celery worker is sync.
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        return loop.run_until_complete(coro)
    except RuntimeError:
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


register_task_binding(TaskBinding(
    action_kind=ActionKind.SEND_INITIAL_OUTREACH,
    celery_task=send_initial_outreach_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.SEND_REMINDER,
    celery_task=send_reminder_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.NOTIFY_NEW_OWNER,
    celery_task=notify_new_owner_task,
    queue="default",
))
