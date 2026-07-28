"""Sync PostgresAuditWriter conforming to tracker.AuditWriter Protocol.

Added 2026-06-27 per architect direction (storage-wireup strand Chunk 2).
Wraps the existing async audit_ops.log_communication for sync Celery task
body callers; bridges the AuditWriter Protocol surface (4 args: action_type
+ delivery_item_id + attribution + details) to the richer CommunicationLogRow
shape.

Pattern matches PostgresStorage (delivery_item_ops.py): async core +
asyncio.run sync wrappers.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from core.src.storage._sync_bridge import run_async_sync
from core.src.storage.audit_ops import log_communication
from core.src.storage.models import Channel, CommunicationLogRow, Direction

__all__ = ["PostgresAuditWriter"]


class PostgresAuditWriter:
    """Conforms to tracker.AuditWriter Protocol.

    The Protocol signature:
        write_communication_log(
            action_type: str,
            delivery_item_id: str | None,
            attribution: dict[str, str],
            details: dict[str, Any],
        ) -> None

    Maps to CommunicationLogRow:
    - log_id           = synthesized UUID4
    - channel          = "SharePoint" sentinel for HILDA-internal audit
                         (CommunicationLogRow has no "Internal"; SharePoint
                         is the closest existing enum for SP-side context
                         attributions)
    - direction        = Outbound (HILDA emitting the audit row)
    - timestamp        = utcnow
    - delivery_item_id = passed through
    - summary          = compact JSON of attribution + details (bounded
                         enum tokens per NFR-2; no raw customer data per
                         tracker.protocols.AuditWriter docstring)
    - action_type      = passed through
    """

    def write_communication_log(
        self,
        action_type: str,
        delivery_item_id: str | None,
        attribution: dict[str, str],
        details: dict[str, Any],
    ) -> None:
        import json
        summary = json.dumps({
            "attribution": attribution or {},
            "details":     details or {},
        }, default=str, separators=(",", ":"))[:4096]   # bounded length

        row = CommunicationLogRow(
            log_id=str(uuid.uuid4()),
            channel=Channel.SHAREPOINT,        # sentinel for HILDA-internal audit
            direction=Direction.OUTBOUND,
            timestamp=datetime.now(timezone.utc),
            delivery_item_id=delivery_item_id,
            device_id=None,
            sender=attribution.get("modified_by") if attribution else None,
            recipients=None,
            subject=None,
            summary=summary,
            external_message_id=attribution.get("correlation_id") if attribution else None,
            credential_id=None,
            action_type=action_type,
            attachments=[],
        )
        run_async_sync(lambda: log_communication(row))

    def query_communications(
        self,
        action_type: str,
        details_contains: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return communication_log rows matching action_type + a JSON-substring
        containment check on the `summary` column (which stores the serialized
        attribution + details dict from write_communication_log).

        Added 2026-07-28 per SETUP-2 (fixes idempotency spam). Prior state:
        setup_complete_notification's tick + tpm_notification's day-of send
        both call `_already_notified` / `_already_sent` via getattr on this
        surface; when the method didn't exist, both fell into the "audit
        query surface unavailable" branch and defensively re-sent every tick.

        details_contains: dict of key->value pairs. Every entry must appear
        as `"key":"value"` (JSON substring) in the row's summary field.
        Simple substring match -- adequate for the idempotency use case
        (scope keys like customer_id / device_id / milestone_id) without
        needing JSON_EXTRACT or jsonb operators (schema is TEXT, not jsonb).

        Returns list of {log_id, timestamp, action_type, summary} dicts.
        Empty list when no match. Never raises -- audit query failures
        must not crash tick tasks.
        """
        import json
        from sqlalchemy import select
        from core.src.storage.db import CommunicationLogTable, get_engine
        from sqlalchemy.ext.asyncio import AsyncSession

        async def _query() -> list[dict[str, Any]]:
            try:
                engine = get_engine()
                async with AsyncSession(engine) as session:
                    stmt = (
                        select(CommunicationLogTable)
                        .where(CommunicationLogTable.action_type == action_type)
                        .order_by(CommunicationLogTable.timestamp.desc())
                        .limit(limit)
                    )
                    rows = (await session.execute(stmt)).scalars().all()
            except Exception:  # noqa: BLE001
                return []

            # Post-filter in Python -- summary is a JSON-serialized string,
            # not a jsonb column, so no native SQL JSON operators.
            filtered: list[dict[str, Any]] = []
            for r in rows:
                s = r.summary or ""
                if details_contains:
                    match = True
                    for k, v in details_contains.items():
                        # Match `"<key>":"<value>"` or `"<key>": "<value>"`
                        # (JSON separators are `,:` per write path -- no
                        # spaces -- but tolerate spaces defensively).
                        needle_compact = f'"{k}":{json.dumps(v)}'
                        needle_spaced  = f'"{k}": {json.dumps(v)}'
                        if needle_compact not in s and needle_spaced not in s:
                            match = False
                            break
                    if not match:
                        continue
                filtered.append({
                    "log_id":       r.log_id,
                    "timestamp":    r.timestamp,
                    "action_type":  r.action_type,
                    "summary":      r.summary,
                })
            return filtered

        return run_async_sync(_query)
