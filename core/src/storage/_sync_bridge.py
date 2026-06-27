"""Sync-to-async bridge helper shared by PostgresStorage + PostgresAuditWriter.

Celery task bodies are sync; storage ops are async. asyncio.run() works when
called from a true sync context (no running loop), but raises if there's a
running loop (e.g. an async test calling the sync wrapper).

This helper picks the right path automatically:
- No running loop -> asyncio.run (cheapest)
- Running loop -> spawn a thread + new event loop in it (escapes the parent loop)

Same pattern is documented in CPython issues + used by sqlalchemy's
async-to-sync wrappers. Production Celery worker bodies hit the no-loop fast
path; tests hit the thread path when running under pytest-asyncio.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
from typing import Any, Awaitable, Callable

__all__ = ["run_async_sync"]


def run_async_sync(coro_func: Callable[[], Awaitable[Any]]) -> Any:
    """Run an async coroutine to completion from any context.

    coro_func: zero-arg callable that returns a coroutine. Passed as a
    callable (not a coroutine) so the coroutine is created fresh in the
    right event loop -- coroutines bind to the loop at creation time.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        # No running loop in this thread -> normal asyncio.run
        return asyncio.run(coro_func())

    # Running loop in this thread -> spawn a worker thread with its own loop
    def _thread_runner() -> Any:
        return asyncio.run(coro_func())

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(_thread_runner).result()
