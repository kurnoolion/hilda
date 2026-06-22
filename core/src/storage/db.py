"""SQLAlchemy 2.x async engine, session management, and ORM tables.

Dialects: postgresql+asyncpg in deployment (HILDA_STORAGE_DB_URL), sqlite+aiosqlite
for --mock / tests. JSON columns use the dialect-portable sqlalchemy JSON type.
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import AsyncIterator

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from core.src.diagnostics.error_codes import PipelineError

__all__ = [
    "Base",
    "AutomationRuleOverrideTable",
    "CommunicationLogTable",
    "DocumentIndexTable",
    "DocumentItemAssociationTable",
    "TGFolderRoutingTable",
    "TagCatalogTable",
    "configure_engine",
    "get_engine",
    "get_session",
    "init_db",
    "session_scope",
]


class Base(DeclarativeBase):
    pass


class DocumentIndexTable(Base):
    __tablename__ = "document_index"
    __table_args__ = (
        # Partial unique index per the staged-fill lifecycle Invariant (2026-06-11):
        # FR-57's exactly-one-file guarantee applies only to RESOLVED rows; any number
        # of staged rows with NULL slug/rev may co-exist (SQL NULL doesn't deduplicate).
        Index(
            "uq_doc_slug_rev",
            "milestone_id",
            "doc_id_slug",
            "rev_number",
            unique=True,
            postgresql_where=text("doc_id_slug IS NOT NULL AND rev_number IS NOT NULL"),
            sqlite_where=text("doc_id_slug IS NOT NULL AND rev_number IS NOT NULL"),
        ),
    )

    file_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    milestone_id: Mapped[str] = mapped_column(String(64), index=True)
    doc_type: Mapped[str] = mapped_column(String(64))
    doc_id_slug: Mapped[str | None] = mapped_column(String(64), nullable=True)
    rev_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ingest_source: Mapped[str] = mapped_column(String(32))
    original_filename: Mapped[str] = mapped_column(String(512))
    first_page_excerpt: Mapped[str] = mapped_column(Text, default="")
    is_final: Mapped[bool] = mapped_column(Boolean, default=False)
    parser_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    llm_review_findings: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    from_zip: Mapped[bool] = mapped_column(Boolean, default=False)
    source_zip_filename: Mapped[str | None] = mapped_column(String(512), nullable=True)
    inferred_tg_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    routing_resolution: Mapped[str] = mapped_column(String(32))
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DocumentItemAssociationTable(Base):
    __tablename__ = "document_item_association"
    __table_args__ = (
        Index("ix_assoc_item", "delivery_item_id"),
        Index("ix_assoc_milestone", "milestone_id"),
        Index("ix_assoc_path_type", "nsd_path_type"),  # STR-W007 stale-staged queries
        Index("ix_assoc_owner_corp_id", "owner_corp_id"),  # FR-79 PLM fan-out grouping per FR-5 + [D-035]
    )

    file_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    delivery_item_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    milestone_id: Mapped[str] = mapped_column(String(64))
    local_nsd_path: Mapped[str] = mapped_column(String(1024))
    nsd_path_type: Mapped[str] = mapped_column(String(32))
    # 4-field owner identity per FR-88 + [D-080] + [D-086] (cascade 2026-06-21);
    # owner_corp_id is PLM grouping key per FR-5 + [D-035]:
    owner_corp_id: Mapped[str] = mapped_column(String(128))
    owner_corp_usa_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    owner_corp_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    plm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    plm_attachment_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    upload_timestamp: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    associated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    associated_by: Mapped[str] = mapped_column(String(128), default="auto")


class CommunicationLogTable(Base):
    __tablename__ = "communication_log"
    __table_args__ = (
        Index("ix_comm_item", "delivery_item_id"),
        Index("ix_comm_ts", "timestamp"),
    )

    log_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    channel: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(16))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivery_item_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sender: Mapped[str | None] = mapped_column(String(256), nullable=True)
    recipients: Mapped[str | None] = mapped_column(Text, nullable=True)
    subject: Mapped[str | None] = mapped_column(Text, nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    external_message_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    credential_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    action_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    attachments: Mapped[list] = mapped_column(JSON, default=list)


class TGFolderRoutingTable(Base):
    __tablename__ = "tg_folder_routing"

    milestone_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tg_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    ingress_folder: Mapped[str] = mapped_column(String(512), primary_key=True)
    item_no: Mapped[int] = mapped_column(Integer)
    routing_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class TagCatalogTable(Base):
    __tablename__ = "tag_catalog"

    customer_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    tag: Mapped[str] = mapped_column(String(128), primary_key=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    color: Mapped[str | None] = mapped_column(String(16), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AutomationRuleOverrideTable(Base):
    __tablename__ = "automation_rule_override"

    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    scope_id: Mapped[str] = mapped_column(String(64), primary_key=True, default="")  # "" = Global (NULL not allowed in PK)
    rule_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    parameter_name: Mapped[str] = mapped_column(String(128), primary_key=True)
    parameter_value: Mapped[str] = mapped_column(Text)
    set_by_pm_id: Mapped[str] = mapped_column(String(128))
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


# --- Engine / session management ----------------------------------------------

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def configure_engine(url: str | None = None, *, echo: bool = False) -> AsyncEngine:
    """(Re)configure the module-level engine. Tests pass sqlite+aiosqlite URLs;
    deployment reads HILDA_STORAGE_DB_URL (postgresql+asyncpg)."""
    global _engine, _sessionmaker
    resolved = url or os.environ.get(
        "HILDA_STORAGE_DB_URL", "postgresql+asyncpg://hilda@localhost:5432/hilda"
    )
    kwargs: dict = {"echo": echo}
    if resolved.startswith("sqlite") and ":memory:" in resolved:
        # In-memory SQLite is per-connection; a static pool keeps every session on
        # the one connection that holds the schema (mock/test mode only).
        from sqlalchemy.pool import StaticPool

        kwargs["poolclass"] = StaticPool
    _engine = create_async_engine(resolved, **kwargs)
    _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_engine() -> AsyncEngine:
    if _engine is None:
        return configure_engine()
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """Plain session iterator — usage: `async for session in get_session(): ...`.
    NOTE: body exceptions cannot be intercepted here (async-generator close
    semantics); error conversion to STR-E001 lives in session_scope()."""
    if _sessionmaker is None:
        configure_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session


@asynccontextmanager
async def session_scope() -> AsyncIterator[AsyncSession]:
    """Session context with the module's error contract: unexpected DB errors are
    rolled back (best-effort) and re-raised as STR-E001 PipelineError; PipelineError
    passes through untranslated. The ops modules use this exclusively."""
    if _sessionmaker is None:
        configure_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        try:
            yield session
        except PipelineError:
            await _safe_rollback(session)
            raise
        except Exception as exc:
            await _safe_rollback(session)
            raise PipelineError("STR-E001", context={"reason": str(exc)[:120]}, cause=exc)


async def _safe_rollback(session: AsyncSession) -> None:
    """Rollback that never masks the original error — a failed commit may have
    already invalidated the connection (e.g. sqlite closes it)."""
    try:
        await session.rollback()
    except Exception:
        pass


async def init_db() -> None:
    """Creates schema if missing — dev/test only; deployment uses Alembic."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
