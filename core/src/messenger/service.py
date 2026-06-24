"""MessengerService -- top-level orchestrator for FR-10 corp-messenger escalation.

Public surface that `workflow_engine`'s ESCALATE ActionKind task body calls
(per architect Q-M4 lock 2026-06-25 -- rule_engine -> workflow_engine ESCALATE
-> messenger.send_escalation).

Full Ph-1 flow per architect direction:
  1. Check daily limit (Q-M2). Exceeded -> emit MSG-W001 + audit-log the
     blocked attempt + return SendResult(success=False, error_code='MSG-W001').
  2. Compose the message via composer.compose_escalation (Q-M6 lock --
     composition lives in messenger, NOT email_service).
  3. Validate <= max_message_bytes; truncate + emit MSG-W002 if over (composer
     handles the truncation; this layer emits the warning + flags it).
  4. Invoke adapter.send(owner_corp_id, message) with retry per config.
  5. Record the CommunicationLog entry via daily_checker.record_send (Q-M5).
  6. Return SendResult.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from core.src.messenger.composer import compose_escalation
from core.src.messenger.config import MessengerConfig
from core.src.messenger.daily_limit import DailyLimitChecker
from core.src.messenger.protocol import EscalationReason, MessengerAdapter, SendResult
from core.src.messenger.utils import redact_owner_corp_id

__all__ = ["MessengerService"]

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MessengerService:
    """Composes + checks daily-limit + invokes adapter + logs. Caller-facing."""

    def __init__(
        self,
        adapter: MessengerAdapter,
        daily_checker: DailyLimitChecker,
        config: MessengerConfig,
    ) -> None:
        self._adapter = adapter
        self._daily = daily_checker
        self._config = config

    async def send_escalation(
        self,
        item: Any,
        batch_id: str,
        reason: EscalationReason,
        milestone_ctx: dict[str, Any],
    ) -> SendResult:
        """Send one escalation message to one owner. See module docstring."""
        ts_now = _utcnow()
        owner_corp_id = _attr(item, "owner_corp_id")
        if not owner_corp_id:
            # Defensive -- caller should pre-filter, but never crash on bad input.
            return SendResult(
                success=False,
                owner_corp_id="<missing>",
                message_bytes=0,
                error_code="MSG-E001",
                sent_at=ts_now,
            )
        redacted = redact_owner_corp_id(owner_corp_id)
        delivery_item_id = _attr(item, "item_id")
        device_id = _attr(item, "device_id") or milestone_ctx.get("device_id")

        # --- Step 1: daily-limit gate (Q-M2) ---
        if not await self._daily.can_send(owner_corp_id, ts_now):
            logger.warning(
                "messenger_blocked: code=MSG-W001 owner=%s daily_limit=%d",
                redacted, self._config.daily_limit_per_owner,
            )
            await self._daily.record_send(
                owner_corp_id=owner_corp_id,
                message_bytes=0,
                success=False,
                ts_now=ts_now,
                delivery_item_id=delivery_item_id,
                device_id=device_id,
                batch_id=batch_id,
                reason=reason.value,
                error_code="MSG-W001",
            )
            return SendResult(
                success=False,
                owner_corp_id=owner_corp_id,
                message_bytes=0,
                error_code="MSG-W001",
                sent_at=ts_now,
            )

        # --- Step 2/3: compose + truncate (Q-M6) ---
        try:
            composed = compose_escalation(
                config=self._config,
                owner_name=str(milestone_ctx.get("owner_name", "")),
                deliverable_count=int(milestone_ctx.get("deliverable_count", 1)),
                milestone_name=str(milestone_ctx.get("milestone_name", "")),
                device_id=str(device_id or ""),
                deadline_date=str(milestone_ctx.get("deadline_date", "")),
                corp_plm_link=str(milestone_ctx.get("corp_plm_link", "")),
                reminder_count=int(milestone_ctx.get("reminder_count", 0)),
                batch_id=batch_id,
                reason=reason.value,
                owner_id_for_error=redacted,
            )
        except Exception as exc:  # noqa: BLE001 -- MSG-E002 path
            logger.warning(
                "messenger_compose_failed: code=MSG-E002 owner=%s reason=%s",
                redacted, type(exc).__name__,
            )
            await self._daily.record_send(
                owner_corp_id=owner_corp_id,
                message_bytes=0,
                success=False,
                ts_now=ts_now,
                delivery_item_id=delivery_item_id,
                device_id=device_id,
                batch_id=batch_id,
                reason=reason.value,
                error_code="MSG-E002",
            )
            return SendResult(
                success=False,
                owner_corp_id=owner_corp_id,
                message_bytes=0,
                error_code="MSG-E002",
                sent_at=ts_now,
            )

        if composed.truncated:
            logger.warning(
                "messenger_truncated: code=MSG-W002 owner=%s original=%d max=%d",
                redacted, composed.original_bytes, self._config.max_message_bytes,
            )

        # --- Step 4: invoke adapter with retry ---
        ok = await self._send_with_retry(owner_corp_id, composed.message, redacted)

        # --- Step 5: audit-log ---
        error_code = None if ok else "MSG-E001"
        await self._daily.record_send(
            owner_corp_id=owner_corp_id,
            message_bytes=composed.rendered_bytes,
            success=ok,
            ts_now=ts_now,
            delivery_item_id=delivery_item_id,
            device_id=device_id,
            batch_id=batch_id,
            reason=reason.value,
            error_code=error_code,
        )

        # --- Step 6: SendResult ---
        return SendResult(
            success=ok,
            owner_corp_id=owner_corp_id,
            message_bytes=composed.rendered_bytes,
            error_code=error_code,
            sent_at=ts_now,
        )

    async def _send_with_retry(
        self, owner_corp_id: str, message: str, redacted: str
    ) -> bool:
        max_attempts = max(1, int(self._config.retry_attempts))
        backoff = float(self._config.retry_backoff_seconds)
        for attempt in range(1, max_attempts + 1):
            ok = await self._adapter.send(owner_corp_id, message)
            if ok:
                return True
            if attempt < max_attempts:
                logger.warning(
                    "messenger_retry: code=MSG-W003 owner=%s attempt=%d/%d",
                    redacted, attempt, max_attempts,
                )
                # Tests configure backoff=0 to short-circuit; tiny sleeps stay async.
                if backoff > 0:
                    await asyncio.sleep(backoff)
        return False


def _attr(item: Any, name: str) -> Any:
    """Read either attribute or dict key from an item-shaped object."""
    if hasattr(item, name):
        return getattr(item, name)
    if isinstance(item, dict):
        return item.get(name)
    return None
