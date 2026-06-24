"""Per-(source, error_code) rolling-window rate limiter.

Per [D-127] Q2 lock 2026-06-26: rate_limit_per_minute int|None;
null = pass-through (Ph-1 default); N>0 = at most N alerts per
(source, error_code) per rolling 60-second window.

No summary alert at window end (deferred Ph-2).
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import datetime, timezone


class RateLimiter:
    """In-memory rolling-window per-(source, error_code) limiter.

    Time source is injected so tests can drive deterministic clocks; default
    is utcnow.
    """

    def __init__(
        self,
        rate_limit_per_minute: int | None,
        window_seconds: int = 60,
        clock: "callable[[], datetime] | None" = None,
    ) -> None:
        self._limit = rate_limit_per_minute
        self._window = window_seconds
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._timestamps: dict[tuple[str, str], deque[datetime]] = defaultdict(deque)

    def allow(self, source: str, error_code: str) -> bool:
        """Returns True if the call may proceed; False if suppressed.

        When `rate_limit_per_minute is None`, always returns True. When set
        to N, evicts timestamps older than `window_seconds` from the bucket
        first, then checks `len < N`. If allowed, appends current timestamp.
        """
        if self._limit is None:
            return True
        key = (source, error_code)
        now = self._clock()
        bucket = self._timestamps[key]
        cutoff_age = self._window
        # Evict expired entries from the front
        while bucket and (now - bucket[0]).total_seconds() > cutoff_age:
            bucket.popleft()
        if len(bucket) >= self._limit:
            return False
        bucket.append(now)
        return True
