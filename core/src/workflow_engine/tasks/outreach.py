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


def _record_reminder_attempt(deps, delivery_item_id: str | None) -> int | None:
    """Increment item.reminder_count + stamp last_reminder_triggered_at after
    a SEND_REMINDER task body fires.

    Added 2026-06-27 per [D-118] Chunk 4 rule-walk-through Finding 1 -- prior
    code never incremented reminder_count, so the FR-10 cadence ladder
    (Rule 2a: count<1 -> 1st reminder; Rule 2b: 1<=count<2 -> 2nd reminder)
    would loop forever on Rule 2a and never escalate to Rule 2b. Now the
    counter advances each task invocation regardless of email-send success
    (audit-only mode still advances cadence -- otherwise un-wired dev setups
    would never exit Rule 2a).

    Also stamps last_reminder_triggered_at per NFR-21 §5 amendment 2026-06-21
    (HILDA + SP UI dual-writer; this is HILDA's write).

    Returns the new reminder_count, or None if delivery_item_id missing /
    storage write failed (non-fatal -- caller still audit-logs).
    """
    if not delivery_item_id:
        return None
    try:
        item = deps.storage.get_delivery_item(delivery_item_id)
        prior = getattr(item, "reminder_count", 0) or 0
        new_count = prior + 1
        from datetime import datetime, timezone
        deps.storage.update_delivery_item(
            delivery_item_id,
            {
                "reminder_count":             new_count,
                "last_reminder_triggered_at": datetime.now(timezone.utc),
            },
        )
        return new_count
    except Exception:  # noqa: BLE001 -- non-fatal; audit-only fall-through
        return None


def _resolve_recipient(deps, event_context: dict[str, Any], params: dict[str, Any]) -> str | None:
    """Resolve outreach recipient per [D-080] preference chain.

    Path A precedence (architect 2026-06-27: SP is source of truth):
      1. Explicit params.recipient                 (rule YAML can pin)
      2. **SP-side owner_corp_usa_email**          (live; via sp_writer)
      3. **SP-side owner_corp_email**              (live fallback)
      4. Storage DeliveryItem.owner_corp_usa_email (offline fallback if SP
         unreachable -- typically None today since HILDA doesn't replicate
         SP fields; kept for testability + transitional state)
      5. Storage DeliveryItem.owner_corp_email     (offline fallback)
      6. event_context.owner_corp_usa_email        (legacy callers / fixtures)
      7. None -> caller writes audit-only row, skips send

    Reading SP at fire-time means TPM mid-flight owner edits in SP are
    automatically honored without HILDA-side persistence. Failure modes
    (network, SP outage, schema mismatch) fall through silently to the
    storage/event_context fallbacks rather than blocking the email.
    """
    explicit = params.get("recipient")
    if explicit:
        return explicit

    delivery_item_id = event_context.get("delivery_item_id")
    item = None
    if delivery_item_id:
        try:
            item = deps.storage.get_delivery_item(delivery_item_id)
        except Exception:  # noqa: BLE001 -- storage miss is non-fatal
            item = None

    # Path A: SP read at fire-time
    if item is not None and deps.sp_writer is not None:
        sp_owner = _read_owner_from_sp(deps, item)
        if sp_owner:
            return sp_owner

    # Fallback 1: storage-cached owner identity (typically NULL in current
    # Ph-1 since HILDA doesn't replicate SP fields).
    if item is not None:
        from_storage = (
            getattr(item, "owner_corp_usa_email", None)
            or getattr(item, "owner_corp_email", None)
        )
        if from_storage:
            return from_storage

    # Fallback 2: event_context (legacy callers + tests that pre-populate)
    return event_context.get("owner_corp_usa_email")


def _read_owner_from_sp(deps, item: Any) -> str | None:
    """Read live owner identity from SP via sp_writer.get_items.

    Best-effort: returns None on any failure (network, no match, schema
    mismatch). Caller falls back to storage / event_context.

    Filter strategy: scope by customer_id (selects Deliverables_<customer_id>
    list), then filter by item_no. In the architect's current MMK Ph-1 test
    setup (one milestone P1, item_nos 1..11) this returns the unique row.
    Multi-milestone deployments where item_no is not globally unique within
    the customer's Deliverables list will need to add milestone_name (or
    equivalent) to the canonical_filters once the SP column-map is known.
    """
    from core.src.sharepoint_integration.config import ListScope
    customer_id = getattr(item, "customer_id", None)
    item_no = getattr(item, "item_no", None)
    if not customer_id or item_no is None:
        return None
    try:
        scope = ListScope(customer_id=customer_id)
        rows = deps.sp_writer.get_items(
            entity="delivery_items",
            scope=scope,
            canonical_filters={"item_no": item_no},
        )
    except Exception as exc:  # noqa: BLE001 -- SP read is best-effort
        _log.warning(
            "_resolve_recipient: SP read failed for customer_id=%s item_no=%s: %s",
            customer_id, item_no, type(exc).__name__,
        )
        return None
    if not rows:
        _log.info(
            "_resolve_recipient: SP returned no rows for customer_id=%s item_no=%s",
            customer_id, item_no,
        )
        return None
    if len(rows) > 1:
        _log.warning(
            "_resolve_recipient: SP returned %d rows for customer_id=%s item_no=%s; "
            "using first. Multi-milestone deployments should add milestone discriminator "
            "to canonical_filters once SP schema mapping is known.",
            len(rows), customer_id, item_no,
        )
    row = rows[0]
    return row.get("owner_corp_usa_email") or row.get("owner_corp_email")


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
    delivery_item_id = event_context.get("delivery_item_id")
    recipient = _resolve_recipient(deps, event_context, params)

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
    delivery_item_id = event_context.get("delivery_item_id")
    recipient = _resolve_recipient(deps, event_context, params)

    # Advance FR-10 cadence counter BEFORE send so audit log + email subject
    # reflect the actual cadence number (1st reminder -> count=1, 2nd -> 2).
    # Falls back to the params.reminder_count for tests that pre-set it +
    # for legacy callers that don't have storage-side reminder_count.
    new_count = _record_reminder_attempt(deps, delivery_item_id)
    reminder_count = new_count if new_count is not None else params.get("reminder_count", 1)

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

    Collection-started gate (architect 2026-06-27): if the milestone has not
    yet had collection kicked off (item.delivery_state == "Not Started"), we
    DO NOT send the reassignment notice. Reasoning: an owner edited during
    setup is just data-entry; the new owner will receive the initial outreach
    naturally when the TPM later clicks Start Collection -- that fires
    kickoff_collection_on_milestone_started -> ItemCreated ->
    send_initial_outreach_on_collection_start, which reads the (then-current)
    SP-side owner identity. Sending a reassignment notice now would be a
    confusing duplicate email.

    params:
      - template: str (default "owner_reassignment_notice")
      - channel:  str (default "email")

    event_context: standard + new owner identity already denormalized in
    event_context (owner_corp_usa_email / owner_corp_id).
    """
    deps = get_task_deps()
    template = params.get("template", "owner_reassignment_notice")
    channel = params.get("channel", "email")
    delivery_item_id = event_context.get("delivery_item_id")

    # ---- Collection-started gate ----
    # Per architect 2026-06-27: do not email new owner if collection hasn't
    # kicked off yet. Use item.delivery_state as the milestone-level proxy:
    # KICKOFF_COLLECTION transitions every tracker out of "Not Started" when
    # the TPM clicks Start Collection. While at least one item remains in
    # "Not Started", treat the milestone as pre-kickoff.
    item_snapshot: Any = None
    if delivery_item_id and deps.storage is not None:
        try:
            item_snapshot = deps.storage.get_delivery_item(delivery_item_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning("notify_new_owner: snapshot fetch failed for item=%s: %s",
                         delivery_item_id, type(exc).__name__)
    pre_kickoff = (
        item_snapshot is None
        or getattr(item_snapshot, "delivery_state", None) in (None, "Not Started")
    )
    if pre_kickoff:
        _log.info(
            "notify_new_owner: collection not started for item=%s; deferring -- "
            "new owner will receive initial outreach when TPM kicks off collection",
            delivery_item_id,
        )
        deps.audit.write_communication_log(
            action_type="notify_new_owner",
            delivery_item_id=delivery_item_id,
            attribution={
                "trigger_source": event_context.get("trigger_source", "automated"),
                "correlation_id": event_context.get("correlation_id", ""),
                "modified_by":    "system",
            },
            details={
                "outcome":      "deferred_collection_not_started",
                "template":     template,
                "channel":      channel,
                "milestone_id": event_context.get("milestone_id"),
                "send_skipped": True,
            },
        )
        return {
            "template":   template,
            "channel":    channel,
            "recipient":  None,
            "message_id": None,
            "outcome":    "deferred_collection_not_started",
        }

    recipient = _resolve_recipient(deps, event_context, params)

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
