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
    """SpAlertParser expects a storage with log_communication; production
    polling task doesn't need parser-side audit logging (dispatcher's chain
    writes its own CommunicationLog rows). NoOp keeps the parser API happy."""

    async def log_communication(self, row: Any) -> None:
        return None


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
    parse_failures = 0

    for m in msgs:
        try:
            kind = classify(m)
        except Exception:  # noqa: BLE001
            kind = None
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
        "poll_ews_inbox: messages_fetched=%d sp_alerts=%d dispatched=%d parse_failures=%d",
        len(msgs), sp_alerts, dispatched, parse_failures,
    )
    return {
        "messages_fetched": len(msgs),
        "sp_alerts": sp_alerts,
        "dispatched": dispatched,
        "skipped_no_dispatcher": False,
        "parse_failures": parse_failures,
    }
