"""MockOpsAlerts -- test double per [D-127].

Captures every emit_alert call in self.calls for assertions; never
performs I/O. Implements the OpsAlerts Protocol.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.src.ops_alerts.protocol import OpsAlertResult, Severity


@dataclass
class _CapturedCall:
    source:      str
    error_code:  str
    context:     dict[str, Any]
    severity:    Severity


class MockOpsAlerts:
    """Test double -- captures + replays.

    Configure return-result behavior via `default_result` (applied to every
    call) or `set_next_result(...)` (one-shot override for the next call).
    """

    def __init__(
        self,
        *,
        default_result: OpsAlertResult | None = None,
    ) -> None:
        self.calls: list[_CapturedCall] = []
        self._default = default_result or OpsAlertResult(
            email_sent=True,
            messenger_dm_count=1,
            suppressed_by_rate_limit=False,
        )
        self._queue: list[OpsAlertResult] = []

    def set_next_result(self, result: OpsAlertResult) -> None:
        """Queue a one-shot result for the next emit_alert call."""
        self._queue.append(result)

    async def emit_alert(
        self,
        source: str,
        error_code: str,
        context: dict[str, Any],
        severity: Severity = Severity.WARNING,
    ) -> OpsAlertResult:
        self.calls.append(
            _CapturedCall(
                source=source,
                error_code=error_code,
                context=dict(context),
                severity=severity,
            )
        )
        if self._queue:
            return self._queue.pop(0)
        return self._default

    async def health(self) -> dict[str, Any]:
        return {
            "ready": True,
            "ops_bot_email": "mock-ops-bot@example.com",
            "broadcast_corp_id_count": 2,
            "rate_limit_per_minute": None,
        }
