"""Periodic EWS inbox polling -- the production runtime that previously
relied on manual one-shot scripts.

Added 2026-06-27 per architect direction: ews_receiver was historically
invoked via the D-128 smoke-test script; production needed an automated
poller. This task fires every 60s (Celery beat schedule in celery_app.py),
pulls new SP alerts from the OMADM_BOT inbox, parses each one, and
dispatches the resulting TriggerEvent through the wired dispatcher.

Architecture:
- Celery beat fires this task every poll_interval_s seconds (default 60s,
  configurable via EmailServiceConfig.ews.poll_interval_s).
- Task body bridges async EwsReceiver.fetch_once into sync Celery via
  asyncio.run (same pattern used in PostgresStorage / PostgresAuditWriter).
- For each fetched message:
    - classify() -> EmailKind.SP_ALERT? else skip
    - SpAlertParser.parse(m) -> structured event (or None if no-op change)
    - Construct TriggerEvent + dispatch via deps.dispatcher
- Task returns counts for telemetry (RPT line / dashboard).

If deps.dispatcher is None (worker not bootstrapped), task degrades to
audit-only: messages get fetched/parsed but events aren't dispatched.
This matches the graceful-degrade pattern used by other tasks.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["poll_ews_inbox_task"]

_log = logging.getLogger(__name__)


class _NoOpAuditStorage:
    """SpAlertParser's constructor takes a `storage` arg but the parser body
    never calls storage.log_communication anywhere -- the field is unused
    plumbing kept for parser API compatibility. Inbound-alert audit is
    written directly by the polling task body via _audit_inbound_sp_alert
    below (the actual observability seam)."""

    async def log_communication(self, row: Any) -> None:
        return None


async def _audit_inbound_sp_alert(parsed: Any, msg: Any) -> None:
    """Write one CommunicationLog row per parsed SP alert (inbound direction).

    Closes the observability gap revealed during Step 1.5 debug 2026-06-27:
    milestone-add alerts arrived, dispatched=1 per poll, but communication_log
    stayed at 0 rows because no rule matches list_name=Milestones +
    sub_trigger=added (by [D-118] design -- only Deliverables-added has a
    matching IMPORT_DELIVERABLE_TRACKER rule). Previously the inbound event
    was invisible to anyone inspecting Postgres -- only worker logs showed
    receipt.

    summary is bounded + contains only enum-token routing fields per NFR-2;
    no raw body or attachment content is persisted here.
    """
    import json
    from core.src.storage.audit_ops import log_communication
    from core.src.storage.models import Channel, CommunicationLogRow, Direction

    rk = parsed.routing_key
    summary = json.dumps({
        "list":    rk.list_name,
        "suffix":  rk.list_suffix,
        "title":   parsed.item_title,
        "verb":    parsed.action_type,
        "ms":      rk.milestone_name,
        "item_no": rk.item_number,
    }, default=str, separators=(",", ":"))[:1024]

    row = CommunicationLogRow(
        log_id=str(uuid.uuid4()),
        channel=Channel.SHAREPOINT,
        direction=Direction.INBOUND,
        timestamp=datetime.now(timezone.utc),
        delivery_item_id=None,
        device_id=None,
        sender=getattr(msg, "sender", None),
        recipients=None,
        subject=getattr(msg, "subject", None),
        summary=summary,
        external_message_id=getattr(msg, "message_id", None),
        credential_id=None,
        action_type=f"SP_ALERT_{(parsed.action_type or 'UNKNOWN').upper()}",
        attachments=[],
    )
    await log_communication(row)


@hilda_celery_app.task(
    name="core.src.workflow_engine.tasks.email_polling.poll_ews_inbox",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def poll_ews_inbox_task(self) -> dict[str, Any]:
    """Periodic poll: fetch + parse + dispatch SP alerts from the inbox.

    Returns dict with counts:
      - messages_fetched: total inbox messages this poll cycle
      - sp_alerts: messages classified as SP_ALERT
      - audited: SP alerts that wrote a CommunicationLog row before dispatch
        (best-effort; failure does NOT block dispatch). Added 2026-06-27 to
        close the unmatched-event observability gap.
      - dispatched: events successfully dispatched through dispatcher
      - skipped_no_dispatcher: if deps.dispatcher is None
      - parse_failures: messages that failed parse/dispatch
    """
    try:
        return asyncio.run(_async_poll_and_dispatch())
    except Exception as exc:  # noqa: BLE001
        _log.warning("poll_ews_inbox_task failed: %s: %s", type(exc).__name__, str(exc)[:200])
        # Retry once on transient errors; subsequent retries swallowed
        # so the beat schedule keeps firing on the next interval.
        try:
            raise self.retry(exc=exc)
        except Exception:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}


async def _async_poll_and_dispatch() -> dict[str, Any]:
    # Lazy imports keep the task body callable even if email_service /
    # credential_service have partial-init failures at worker boot.
    from core.src.credential_service.service import SopsCredentialService
    from core.src.email_service import build_receiver
    from core.src.email_service.config import EmailServiceConfig
    from core.src.email_service.inbound.classifier import classify
    from core.src.email_service.protocol import EmailKind
    from core.src.email_service.sp_alert_parser import SpAlertParser
    from core.src.rule_engine import EntityRef, TriggerEvent, TriggerKind

    # --- Build receiver ---
    cfg = EmailServiceConfig.from_sources()
    # Honor SOPS_AGE_KEY_FILE env var if set (architect's container has
    # /etc/hilda/age-key/keys.txt; default would point at /etc/hilda/age.key).
    import os
    from pathlib import Path as _P
    age_key_env = os.environ.get("SOPS_AGE_KEY_FILE")
    cred_kwargs: dict[str, Any] = {}
    if age_key_env:
        cred_kwargs["age_key_path"] = _P(age_key_env)
    cred = SopsCredentialService(**cred_kwargs)
    await cred.load()
    recv = build_receiver(cfg, cred)

    # --- Fetch ---
    msgs = await recv.fetch_once()

    # --- Pre-flight deps check ---
    deps = get_task_deps()
    if deps.dispatcher is None:
        _log.warning("poll_ews_inbox: deps.dispatcher is None; messages fetched but not dispatched")
        return {
            "messages_fetched": len(msgs),
            "sp_alerts": 0,
            "dispatched": 0,
            "skipped_no_dispatcher": True,
            "parse_failures": 0,
        }

    # --- Parse + dispatch ---
    parser = SpAlertParser(storage=_NoOpAuditStorage())
    verb_to_trigger = {
        "added":   TriggerKind.ITEM_MODIFIED,
        "changed": TriggerKind.ITEM_MODIFIED,
        "deleted": TriggerKind.ITEM_MODIFIED,
    }

    sp_alerts = 0
    dispatched = 0
    audited = 0
    parse_failures = 0
    owner_replies = 0
    owner_reply_enqueued = 0

    for m in msgs:
        try:
            kind = classify(m)
        except Exception:  # noqa: BLE001
            kind = None
        if kind == EmailKind.OWNER_REPLY:
            owner_replies += 1
            try:
                _enqueue_owner_reply(m)
                owner_reply_enqueued += 1
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "poll_ews_inbox owner-reply enqueue failed: %s: %s",
                    type(exc).__name__, str(exc)[:120],
                )
            continue
        if kind != EmailKind.SP_ALERT:
            continue
        sp_alerts += 1

        try:
            parsed = parser.parse(m)
        except Exception as exc:  # noqa: BLE001
            _log.warning("poll_ews_inbox parse failed for msg %r: %s",
                         getattr(m, "message_id", "?"), str(exc)[:120])
            parse_failures += 1
            continue

        if parsed is None:
            # Silent drop (no-op SP CHANGE or Projects Ph-2)
            continue

        # Write inbound-audit row BEFORE dispatch -- captures unmatched events too.
        try:
            await _audit_inbound_sp_alert(parsed, m)
            audited += 1
        except Exception as exc:  # noqa: BLE001
            _log.warning("poll_ews_inbox audit failed: %s", str(exc)[:120])
            # don't increment parse_failures -- audit is best-effort, dispatch still proceeds

        try:
            event = TriggerEvent(
                trigger=verb_to_trigger.get(parsed.action_type or "", TriggerKind.ITEM_MODIFIED),
                sub_trigger=parsed.action_type,
                entity_ref=EntityRef(
                    customer_id=parsed.routing_key.list_suffix,
                    milestone_id=parsed.routing_key.milestone_name,
                ),
                field_deltas=dict(parsed.field_deltas) if parsed.field_deltas else None,
                timestamp=datetime.now(timezone.utc),
                correlation_id=str(uuid.uuid4()),
                derived_fields={
                    "action_type": parsed.action_type,
                    "list_name":   parsed.routing_key.list_name,
                    "item_title":  parsed.item_title,
                    "body_kvs":    dict(parsed.body_kvs),
                    "routing_key": {
                        "project_id":     parsed.routing_key.project_id,
                        "milestone_name": parsed.routing_key.milestone_name,
                        "item_number":    parsed.routing_key.item_number,
                        "list_suffix":    parsed.routing_key.list_suffix,
                    },
                },
            )
            deps.dispatcher.dispatch(event)
            dispatched += 1
        except Exception as exc:  # noqa: BLE001
            _log.warning("poll_ews_inbox dispatch failed: %s", str(exc)[:120])
            parse_failures += 1

    _log.info(
        "poll_ews_inbox: messages_fetched=%d sp_alerts=%d audited=%d "
        "dispatched=%d owner_replies=%d owner_reply_enqueued=%d "
        "parse_failures=%d",
        len(msgs), sp_alerts, audited, dispatched,
        owner_replies, owner_reply_enqueued, parse_failures,
    )
    return {
        "messages_fetched":       len(msgs),
        "sp_alerts":              sp_alerts,
        "audited":                audited,
        "dispatched":             dispatched,
        "owner_replies":          owner_replies,
        "owner_reply_enqueued":   owner_reply_enqueued,
        "skipped_no_dispatcher":  False,
        "parse_failures":         parse_failures,
    }


def _enqueue_owner_reply(msg: Any) -> None:
    """Build the serialisable payload + enqueue apply_owner_reply_task AND
    (when attachments present) process_inbound_attachments_task.

    Both run in parallel per architect 2026-06-29 Q1 lock (option c). Race
    on doc_count is reconciled via owner_intent_closed_at persistence
    (apply_owner_reply sets it on guard_denied OwnerClosed; reconcile rule
    catches it when doc_count_reached fires from this attachment task).

    Attachments are serialized as dicts (InboundAttachment is a frozen
    dataclass; Celery JSON serializes the dict shape cleanly).
    """
    from core.src.workflow_engine.tasks.inbound_attachment import (
        process_inbound_attachments_task,
    )
    from core.src.workflow_engine.tasks.owner_reply import apply_owner_reply_task

    received_at = getattr(msg, "received_at", None)
    attachments_raw = getattr(msg, "attachments", ()) or ()
    attachments_payload: list[dict[str, Any]] = []
    for a in attachments_raw:
        attachments_payload.append({
            "filename":     getattr(a, "filename", "") or "",
            "content":      getattr(a, "content", b"") or b"",
            "content_type": getattr(a, "content_type", "application/octet-stream"),
            "file_hash":    getattr(a, "file_hash", "") or "",
        })

    base_payload: dict[str, Any] = {
        "message_id":   getattr(msg, "message_id", ""),
        "sender":       getattr(msg, "sender", "") or "",
        "to_addrs":     list(getattr(msg, "to_addrs", ()) or ()),
        "cc_addrs":     list(getattr(msg, "cc_addrs", ()) or ()),
        "subject":      getattr(msg, "subject", "") or "",
        "body_text":    getattr(msg, "body_text", "") or "",
        "body_html":    getattr(msg, "body_html", None),
        "received_at_iso": received_at.isoformat() if received_at else None,
    }

    # Path 1: structural status -- table parse + per-row dispatch. Attachments
    # are NOT in this payload; they go to the parallel task below.
    apply_owner_reply_task.delay(base_payload)

    # Path 2: per-attachment routing + storage write + AttachmentReceived
    # event emission. Only enqueue when attachments exist.
    if attachments_payload:
        attach_payload = {**base_payload, "attachments": attachments_payload}
        process_inbound_attachments_task.delay(attach_payload)
