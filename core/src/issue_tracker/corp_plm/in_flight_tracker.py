"""InFlightDownloadTracker -- per Q6 architect direction 2026-06-25.

Prevents duplicate concurrent downloads keyed on (plm_id, file_id) per Q6 + Q7.

Ph-1: in-memory asyncio.Lock-guarded dict.
Ph-2: Postgres-backed for restart resilience (TODO).
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator


class InFlightDownloadTracker:
    """Coordinates concurrent download attempts on the same `(plm_id, file_id)` key.

    Usage:
        tracker = InFlightDownloadTracker()
        async with tracker.acquire((plm_id, file_id)) as acquired:
            if not acquired:
                # ITR-W003 in-flight skip -- duplicate concurrent download
                return
            ok = await adapter.download_file(...)
        # release is automatic on context exit

    Invariants:
      - First concurrent acquire on a key returns acquired=True;
        subsequent concurrent acquires on the same key return acquired=False
        until the first releases.
      - Release is automatic on async-context exit (even on exception).
      - `in_flight()` returns the current keyset for diagnostics/tests.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._in_flight: set[tuple[str, str]] = set()

    def in_flight(self) -> set[tuple[str, str]]:
        """Returns a snapshot of in-flight keys. For diagnostics + tests."""
        return set(self._in_flight)

    @asynccontextmanager
    async def acquire(self, key: tuple[str, str]) -> AsyncIterator[bool]:
        """Async context manager. Yields True if the key was claimed (caller may
        proceed); False if the key was already in flight (caller should skip
        and log ITR-W003)."""
        acquired = False
        async with self._lock:
            if key not in self._in_flight:
                self._in_flight.add(key)
                acquired = True
        try:
            yield acquired
        finally:
            if acquired:
                async with self._lock:
                    self._in_flight.discard(key)
