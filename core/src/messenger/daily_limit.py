"""DailyLimitChecker -- enforces Q-M2 3-messages-per-owner-per-day cap.

Per architect Q-M2 lock 2026-06-25 + Q-M5 audit lock:
- HILDA pre-checks the daily count via CommunicationLog before calling sendMessage.
- A 4th attempt for a given owner_corp_id within the same UTC calendar day is
  blocked at the messenger module level + emits MSG-W001.
- record_send writes one CommunicationLog row per attempt (success OR blocked /
  failure -- the audit trail records intent + outcome).

Storage protocol (duck-typed) -- caller injects an object with:
  - `log_communication(row: CommunicationLogRow) -> Awaitable[None]`
  - `query_communications(*, channel, action_type, since, until, ...) -> Awaitable[list]`

The InMemoryStorage test fixture + the real `core.src.storage.audit_ops` module
both satisfy this surface (the test fixture exposes a `communications` list +
sync filtering shim wrapping query_communications).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from core.src.messenger.config import MessengerConfig

__all__ = ["MessengerStorageProtocol", "DailyLimitChecker"]


class MessengerStorageProtocol(Protocol):
    """Subset of `storage` audit-log interface this module depends on."""

    async def log_communication(self, row: Any) -> None: ...

    async def query_communications(
        self,
        *,
        channel: Any = None,
        action_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        **kwargs: Any,
    ) -> list[Any]: ...


def _day_window(ts_now: datetime) -> tuple[datetime, datetime]:
    """Return (today_start_utc, today_end_utc) for the calendar day containing ts_now."""
    ts = ts_now.astimezone(timezone.utc) if ts_now.tzinfo else ts_now.replace(tzinfo=timezone.utc)
    day_start = ts.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    return day_start, day_end


class DailyLimitChecker:
    """Pre-check + audit-write helper for the Q-M2 daily-limit invariant."""

    ACTION_TYPE = "messenger_escalation"

    def __init__(
        self,
        storage: MessengerStorageProtocol,
        config: MessengerConfig,
    ) -> None:
        self._storage = storage
        self._config = config

    async def can_send(self, owner_corp_id: str, ts_now: datetime) -> bool:
        """True when count of today's outbound messenger sends for this owner is
        below `config.daily_limit_per_owner`. False when 4th attempt would
        breach the cap.
        """
        count = await self._count_today(owner_corp_id, ts_now)
        return count < self._config.daily_limit_per_owner

    async def current_count(self, owner_corp_id: str, ts_now: datetime) -> int:
        """Today's outbound messenger send count for one owner. Exposed for
        diagnostics + tests."""
        return await self._count_today(owner_corp_id, ts_now)

    async def record_send(
        self,
        owner_corp_id: str,
        message_bytes: int,
        success: bool,
        ts_now: datetime,
        delivery_item_id: str | None = None,
        device_id: str | None = None,
        batch_id: str | None = None,
        reason: str | None = None,
        error_code: str | None = None,
    ) -> None:
        """Write one CommunicationLog row per FR-42 / Q-M5.

        Per Q-M5 lock 2026-06-25: bool-only outcome captured; no external
        message_id for thread continuity Ph-1. Acceptable.

        Privacy per NFR-2: `summary` includes bounded enum tokens + size bucket
        ONLY -- never the rendered message body. `recipients` carries the raw
        owner_corp_id (audit storage; NOT a compact report).
        """
        # Lazy import -- keep daily_limit.py importable without a fully-initialized
        # storage layer (e.g., when running --mock CLI mode).
        from core.src.storage.models import Channel, CommunicationLogRow, Direction
        size_bucket = self._size_bucket(message_bytes)
        summary_parts = [
            f"channel=corp_messenger",
            f"reason={reason or 'unspecified'}",
            f"size={size_bucket}",
            f"success={'true' if success else 'false'}",
        ]
        if error_code:
            summary_parts.append(f"error_code={error_code}")
        row = CommunicationLogRow(
            log_id=uuid.uuid4().hex,
            channel=Channel.MESSENGER,
            direction=Direction.OUTBOUND,
            timestamp=ts_now,
            delivery_item_id=delivery_item_id,
            device_id=device_id,
            sender=None,                               # Q-M3 anonymous HILDA BOT
            recipients=owner_corp_id,                  # raw -- audit only
            subject=None,
            summary="|".join(summary_parts),
            external_message_id=None,                  # Q-M5 -- no message_id Ph-1
            credential_id=None,
            action_type=self.ACTION_TYPE,
            attachments=[
                {"batch_id": batch_id} if batch_id else {},
            ],
        )
        await self._storage.log_communication(row)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _count_today(self, owner_corp_id: str, ts_now: datetime) -> int:
        day_start, day_end = _day_window(ts_now)
        from core.src.storage.models import Channel
        rows = await self._storage.query_communications(
            channel=Channel.MESSENGER,
            action_type=self.ACTION_TYPE,
            since=day_start,
            until=day_end,
        )
        # Filter by owner_corp_id client-side -- query_communications doesn't
        # accept `recipients` directly (NFR-6 audit-query surface).
        return sum(1 for r in rows if _row_recipient(r) == owner_corp_id)

    @staticmethod
    def _size_bucket(n: int) -> str:
        if n < 500:
            return "xs"
        if n < 1500:
            return "s"
        if n < 3000:
            return "m"
        return "l"


def _row_recipient(row: Any) -> str | None:
    """Read `.recipients` off a CommunicationLogRow or dict-shaped row."""
    return getattr(row, "recipients", None) or (
        row.get("recipients") if isinstance(row, dict) else None
    )
