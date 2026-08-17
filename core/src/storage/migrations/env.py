"""Alembic async env — target metadata is storage's Base; URL from config -x or env."""
from __future__ import annotations

import asyncio
import os

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from core.src.storage.db import Base

target_metadata = Base.metadata


def _url() -> str:
    """DB URL lookup order:
      (1) `-x url=...` CLI arg (highest priority; used by ops one-liners)
      (2) `HILDA_DB_URL`         (canonical env var used by hilda-api /
                                 hilda-worker containers per docker-compose)
      (3) `HILDA_STORAGE_DB_URL` (legacy; kept for back-compat with any
                                 dev setup that still sets this name)
      (4) localhost fallback     (bare-metal / offline `alembic --sql`)

    Precedence flipped 2026-08-17 after corp-box ops surfaced that the
    deployed containers set `HILDA_DB_URL` (matching the runtime app's
    config.get_db_url()), not `HILDA_STORAGE_DB_URL`. Prior order picked
    the legacy name first, which meant `alembic upgrade head` inside
    the container fell through to the localhost default and errored with
    ConnectionRefusedError.
    """
    return (
        context.get_x_argument(as_dictionary=True).get("url")
        or os.environ.get("HILDA_DB_URL")
        or os.environ.get("HILDA_STORAGE_DB_URL")
        or "postgresql+asyncpg://hilda@localhost:5432/hilda"
    )


def run_migrations_offline() -> None:
    context.configure(url=_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await engine.dispose()


def run_migrations_online() -> None:
    asyncio.run(_run_async())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
