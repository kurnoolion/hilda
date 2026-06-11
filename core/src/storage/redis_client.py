"""Redis client — broker URL (Ph-1/Ph-2), 24h-capped cache, BATCH-id idempotency.

Import convention (per MODULE.md Key choices, naming correction 2026-06-11):
`import redis.asyncio as redis_async` — explicit naming, used module-wide. The
legacy standalone `aioredis` package was absorbed into redis-py 4.2 and is archived.
Tests inject fakeredis via set_redis_client().
"""
from __future__ import annotations

import os
from typing import Any

from core.src.diagnostics.error_codes import PipelineError
from core.src.storage.models import BatchIdempotencyKey

__all__ = [
    "MAX_CACHE_TTL_SECONDS",
    "cache_delete",
    "cache_get",
    "cache_set",
    "check_batch_idempotency",
    "get_celery_broker_url",
    "record_batch_idempotency",
    "set_redis_client",
]

MAX_CACHE_TTL_SECONDS: int = 86_400  # 24h — [D-012] short-TTL invariant

_client: Any | None = None


def get_celery_broker_url() -> str:
    """Ph-1/Ph-2 broker URL for workflow_engine's Celery init; Ph-3+ moves to
    get_rabbitmq_broker_url per [D-043]."""
    return os.environ.get("HILDA_REDIS_URL", "redis://localhost:6379/0")


def set_redis_client(client: Any) -> None:
    """Inject a client (tests pass fakeredis.aioredis.FakeRedis)."""
    global _client
    _client = client


def _get_client() -> Any:
    global _client
    if _client is None:
        import redis.asyncio as redis_async

        _client = redis_async.from_url(get_celery_broker_url())
    return _client


async def cache_set(key: str, value: bytes, ttl_seconds: int) -> None:
    """Raises STR-E008 when ttl_seconds exceeds the 24h cap — the [D-012] short-TTL
    invariant is enforced at the API boundary."""
    if ttl_seconds > MAX_CACHE_TTL_SECONDS:
        raise PipelineError("STR-E008", context={"ttl_seconds": ttl_seconds})
    await _get_client().set(key, value, ex=ttl_seconds)


async def cache_get(key: str) -> bytes | None:
    return await _get_client().get(key)


async def cache_delete(key: str) -> None:
    """Idempotent — deleting a missing key is a no-op."""
    await _get_client().delete(key)


def _batch_key(batch_id: str, item_index: int) -> str:
    return f"batch_idem:{batch_id}:{item_index}"


async def check_batch_idempotency(batch_id: str, item_index: int) -> str | None:
    """Existing status when (batch_id, item_index) was already recorded; else None."""
    value = await _get_client().get(_batch_key(batch_id, item_index))
    return value.decode() if value is not None else None


async def record_batch_idempotency(key: BatchIdempotencyKey) -> None:
    """Idempotent — re-recording the same (batch_id, item_index, status) is a no-op."""
    if key.ttl_seconds > MAX_CACHE_TTL_SECONDS:
        raise PipelineError("STR-E008", context={"ttl_seconds": key.ttl_seconds})
    await _get_client().set(
        _batch_key(key.batch_id, key.item_index), key.status.encode(), ex=key.ttl_seconds
    )
