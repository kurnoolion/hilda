"""ops_alerts Protocol surface -- OpsAlerts + Severity + OpsAlertResult.

Per [D-127] Ratified 2026-06-26. Single ingress for HILDA-internal
anomaly/failure signals; fans out to ONE email (HILDA OPS BOT alias) +
N messenger DMs (broadcast_corp_ids per recipients.yaml).

Fire-and-forget surface: `emit_alert(...)` MUST NOT raise to the caller
per [D-127] Invariant 4. Internal failures are caught + recorded in
OpsAlertResult.error_codes + best-effort logged.

See core/src/ops_alerts/MODULE.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

__all__ = ["Severity", "OpsAlertResult", "OpsAlerts"]


class Severity(str, Enum):
    """Per [D-127] Q1 lock 2026-06-26 -- visual-only differentiation."""

    INFO     = "info"
    WARNING  = "warning"
    ERROR    = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class OpsAlertResult:
    """Per [D-127] -- result reports per-channel outcome (debug aid only).

    Callers MUST NOT branch on this; emit_alert is fire-and-forget per
    Invariant 4. The result is for assertions in tests + --diagnostic
    introspection.
    """

    email_sent:              bool                # delivered to ops_bot_email
    messenger_dm_count:      int                 # corp_ids the DM landed on
    suppressed_by_rate_limit: bool               # rate limiter dropped this call
    error_codes:             list[str] = field(default_factory=list)


@runtime_checkable
class OpsAlerts(Protocol):
    """Fire-and-forget alert emitter per [D-127] Invariant 4.

    Implementations (Ph-1):
    - `OpsAlertsService` (concrete; depends on EmailSender + MessengerAdapter)
    - `MockOpsAlerts` (tests; captures emit_alert calls in self.calls)
    """

    async def emit_alert(
        self,
        source: str,
        error_code: str,
        context: dict[str, Any],
        severity: Severity = Severity.WARNING,
    ) -> OpsAlertResult:
        """Emit one alert to the configured recipient set.

        Args:
            source: free-text origin tag (e.g. "issue_tracker.ITR-W004").
            error_code: the source-module's error_code carried in the alert
                payload (e.g. "ITR-W004"). NOT validated here -- callers may
                pass any registered code. ops_alerts emits its own OPS-EXXX
                codes only on alert-channel failures.
            context: bounded key-value payload per NFR-2. Values >200 chars
                truncated; credential fields (`password`, `totp_seed`,
                `totp_code`, etc.) redacted to `<REDACTED>`.
            severity: drives subject prefix tag + HTML body badge color.
                Routing does NOT vary by severity per [D-127] Q1 lock.

        Returns:
            OpsAlertResult -- never raises per Invariant 4. Internal
            failures captured in `error_codes` (OPS-EXXX prefix).
        """
        ...

    async def health(self) -> dict[str, Any]:
        """Returns {ready, ops_bot_email, broadcast_corp_id_count,
        rate_limit_per_minute}. Best-effort; never raises."""
        ...
