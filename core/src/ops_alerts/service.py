"""OpsAlertsService -- concrete OpsAlerts impl.

Per [D-127] Ratified 2026-06-26. Composes alert payload + applies rate
limit + fans out to ONE email (EmailSender) + N messenger DMs
(MessengerAdapter). Fire-and-forget per Invariant 4 -- never raises.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from core.src.ops_alerts.composer import compose_alert
from core.src.ops_alerts.config import OpsAlertsConfig
from core.src.ops_alerts.protocol import OpsAlertResult, Severity
from core.src.ops_alerts.rate_limiter import RateLimiter
from core.src.ops_alerts.recipients_loader import Recipients

_log = logging.getLogger(__name__)


@runtime_checkable
class _EmailSenderLike(Protocol):
    """Subset of email_service.EmailSender used here -- decoupled per
    [D-127] Invariant 9 (no inbound dep on email_service typing)."""

    async def send(
        self,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> str: ...


@runtime_checkable
class _MessengerAdapterLike(Protocol):
    """Subset of messenger.MessengerAdapter used here."""

    async def send(self, owner_corp_id: str, message: str) -> bool: ...


class OpsAlertsService:
    """Fire-and-forget alert emitter. Constructed via build_ops_alerts."""

    def __init__(
        self,
        config: OpsAlertsConfig,
        recipients: Recipients,
        email_sender: _EmailSenderLike | None,
        messenger: _MessengerAdapterLike | None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._config = config
        self._recipients = recipients
        self._email = email_sender
        self._messenger = messenger
        self._rate = rate_limiter or RateLimiter(
            recipients.rate_limit_per_minute,
            window_seconds=config.rate_limit_window_seconds,
        )

    async def emit_alert(
        self,
        source: str,
        error_code: str,
        context: dict[str, Any],
        severity: Severity = Severity.WARNING,
    ) -> OpsAlertResult:
        """Per [D-127] Invariant 4: NEVER raises to caller."""
        # -- Rate limit gate --
        if not self._rate.allow(source, error_code):
            return OpsAlertResult(
                email_sent=False,
                messenger_dm_count=0,
                suppressed_by_rate_limit=True,
                error_codes=[],
            )

        composed = compose_alert(
            source=source,
            error_code=error_code,
            context=context,
            severity=severity,
            subject_prefix_template=self._config.subject_prefix_template,
            context_value_max_chars=self._config.context_value_max_chars,
        )

        # -- Fan-out --
        err_codes: list[str] = []
        email_sent = await self._send_email(composed.subject, composed.html_body, err_codes)
        dm_count = await self._send_messenger_dms(composed.plain_body, err_codes)

        # -- Best-effort local-log fallback when both channels fail --
        if not email_sent and dm_count == 0 and self._config.ops_log_fallback_path:
            self._append_local_log(composed.subject, composed.plain_body)

        return OpsAlertResult(
            email_sent=email_sent,
            messenger_dm_count=dm_count,
            suppressed_by_rate_limit=False,
            error_codes=err_codes,
        )

    async def health(self) -> dict[str, Any]:
        return {
            "ready": self._email is not None or self._messenger is not None,
            "ops_bot_email": self._recipients.ops_bot_email,
            "broadcast_corp_id_count": len(self._recipients.broadcast_corp_ids),
            "rate_limit_per_minute": self._recipients.rate_limit_per_minute,
        }

    # -- Internal channel dispatch -- always catches; never raises --

    async def _send_email(
        self, subject: str, html_body: str, err_codes: list[str]
    ) -> bool:
        if self._email is None:
            return False
        try:
            await self._email.send(
                to=[self._recipients.ops_bot_email],
                cc=[],
                subject=subject,
                body=html_body,
            )
            return True
        except Exception as e:  # noqa: BLE001 -- Invariant 4 fire-and-forget
            _log.warning("ops_alerts email send failed: %s", type(e).__name__)
            err_codes.append("OPS-E002")
            return False

    async def _send_messenger_dms(
        self, plain_body: str, err_codes: list[str]
    ) -> int:
        if self._messenger is None or not self._recipients.broadcast_corp_ids:
            return 0
        delivered = 0
        failed = 0
        for corp_id in self._recipients.broadcast_corp_ids:
            try:
                ok = await self._messenger.send(corp_id, plain_body)
                if ok:
                    delivered += 1
                else:
                    failed += 1
            except Exception as e:  # noqa: BLE001 -- Invariant 4
                _log.warning(
                    "ops_alerts messenger DM failed for %s: %s",
                    corp_id, type(e).__name__,
                )
                failed += 1
        if failed and delivered:
            err_codes.append("OPS-E003")
        elif failed and not delivered:
            err_codes.append("OPS-E004")
        return delivered

    def _append_local_log(self, subject: str, plain_body: str) -> None:
        """Best-effort fallback per [D-127] Invariant 4 -- never raises."""
        path = self._config.ops_log_fallback_path
        if path is None:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8") as f:
                f.write(f"--- {subject}\n{plain_body}\n\n")
        except Exception as e:  # noqa: BLE001
            _log.warning("ops_alerts local-log fallback failed: %s", type(e).__name__)


def build_ops_alerts(
    config: OpsAlertsConfig,
    recipients: Recipients,
    *,
    email_sender: _EmailSenderLike | None,
    messenger: _MessengerAdapterLike | None,
    rate_limiter: RateLimiter | None = None,
) -> OpsAlertsService:
    """Composition-root helper -- standard wire-up.

    For production deploy: pass real EmailSender + MessengerAdapter.
    For test mode: pass None for either channel + use MockOpsAlerts to
    capture calls directly.
    """
    return OpsAlertsService(
        config=config,
        recipients=recipients,
        email_sender=email_sender,
        messenger=messenger,
        rate_limiter=rate_limiter,
    )
