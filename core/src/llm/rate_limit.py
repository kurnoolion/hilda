"""Per-backend rate limiting per [D-052] — fixed-window counters, NO automatic spillover.

Lab Ollama/vLLM are unlimited (all rate_limit_* None → no windows → acquire is a no-op).
`corp_llm` enforces per-minute/hour/day limits: emits LLG-W005 when a window is approaching
exhaustion (logged, informational), and RAISES LLG-W006 when a window is exhausted — the
task is deferred for the caller (Celery) to reschedule. The gateway never silently routes to a
runner-up backend, because the per-task A/B quality result does not generalize across backends.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Callable

from core.src.diagnostics.error_codes import PipelineError, format_code

__all__ = ["BackendRateLimiter"]

logger = logging.getLogger(__name__)


class _FixedWindow:
    """Counts consumptions in the current window; auto-resets when the window elapses."""

    def __init__(self, limit: int, seconds: float, clock: Callable[[], float]) -> None:
        self.limit = limit
        self.seconds = seconds
        self._clock = clock
        self._count = 0
        self._start = clock()

    def _maybe_reset(self) -> None:
        now = self._clock()
        if now - self._start >= self.seconds:
            self._count = 0
            self._start = now

    def remaining(self) -> int:
        self._maybe_reset()
        return self.limit - self._count

    def consume(self) -> None:
        self._maybe_reset()
        self._count += 1

    @property
    def used(self) -> int:
        self._maybe_reset()
        return self._count

    def retry_after(self) -> int:
        return max(0, math.ceil(self.seconds - (self._clock() - self._start)))


class BackendRateLimiter:
    """Built from a backend's rate_limit_per_{minute,hour,day}. No windows → unlimited."""

    def __init__(
        self,
        backend_name: str,
        per_minute: int | None = None,
        per_hour: int | None = None,
        per_day: int | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.backend_name = backend_name
        self._minute = _FixedWindow(per_minute, 60.0, clock) if per_minute else None
        self._windows: list[_FixedWindow] = [
            w for w in (
                self._minute,
                _FixedWindow(per_hour, 3600.0, clock) if per_hour else None,
                _FixedWindow(per_day, 86400.0, clock) if per_day else None,
            ) if w is not None
        ]

    def acquire(self, task_value: str) -> None:
        """Check all configured windows; raise LLG-W006 (no spillover) if any is exhausted,
        log LLG-W005 if the minute window is approaching exhaustion, else consume one slot."""
        if not self._windows:
            return  # unlimited (lab backend)

        # 1. Exhaustion check BEFORE consuming — raise (no spillover), don't consume.
        for w in self._windows:
            if w.remaining() <= 0:
                wait = w.retry_after()
                logger.warning("LLG-W006: " + format_code("LLG-W006", task=task_value, n=str(wait)))
                raise PipelineError("LLG-W006", context={"task": task_value, "n": str(wait)})

        # 2. Consume one slot in every window.
        for w in self._windows:
            w.consume()

        # 3. Approaching check AFTER consuming — warn when this call leaves few slots.
        if self._minute is not None:
            threshold = max(1, math.ceil(self._minute.limit * 0.2))
            if self._minute.remaining() <= threshold:
                logger.warning("LLG-W005: " + format_code(
                    "LLG-W005", used=str(self._minute.used), limit_per_min=str(self._minute.limit),
                    n=str(self._minute.used)))
