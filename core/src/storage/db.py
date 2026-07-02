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
    Date,
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
    "DeliveryItemTable",
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
    # Widened to 256 (2026-06-30): real Samsung filenames produce slugs up to
    # ~70 chars (e.g. SM-A176U Qualification Workspace - Qualified Product
    # Details_approval). String(64) caused asyncpg StringDataRightTruncation,
    # which session_scope() wrapped as the (misleading) STR-E001 "Postgres
    # unavailable" error. 256 leaves headroom without affecting the partial
    # unique index uq_doc_slug_rev.
    doc_id_slug: Mapped[str | None] = mapped_column(String(256), nullable=True)
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


class DeliveryItemTable(Base):
    """SQLAlchemy ORM for DeliveryItemBase (template_schema.models). Added
    2026-06-27 per architect direction to wire HILDA's Postgres-backed
    StorageWriter implementation -- the missing piece between sp_alert_parser
    (working) and workflow_engine task bodies (which all need storage).

    Maps DeliveryItemBase Pydantic fields 1:1; nested fields (item_description,
    tracking_modality, email_cc_list, corp_id_list) use JSON columns;
    customer_id + device_id are denormalized indexed columns for fast
    find_items_by_natural_key + list_items_for_milestone lookups.
    """
    __tablename__ = "delivery_item"
    __table_args__ = (
        Index("ix_di_milestone", "milestone_id"),
        Index("ix_di_customer", "customer_id"),
        Index("ix_di_natural_key", "customer_id", "tg_name", "item_no"),  # find_items_by_natural_key
        Index("ix_di_default_per_milestone", "milestone_id", "item_type"),
        # 2026-07-01 architect lock: dashboard /docs/<customer_id>/<sp_id> lookup.
        # sp_id is SP's Deliverables list row Id (int); HILDA's delivery_item_id is
        # the composite string primary key; the two are NOT the same integer. This
        # index makes the dashboard's WHERE customer_id=X AND sp_id=Y lookup O(log n).
        Index("ix_di_customer_sp", "customer_id", "sp_id"),
    )

    # Identity
    item_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    item_no: Mapped[int] = mapped_column(Integer)
    customer_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    device_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    milestone_id: Mapped[str] = mapped_column(String(64))
    # SP Deliverables list row Id per architect lock 2026-07-01.
    # Populated at import_deliverable_tracker_task via SP READ by natural key
    # (carrier + project_model + item_no). Nullable for rows created before this
    # cascade + defensive against transient SP READ failures at import time
    # (dashboard falls back to a 404 when null).
    sp_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    item_name: Mapped[str] = mapped_column(String(256))
    item_type: Mapped[str] = mapped_column(String(64))
    item_description: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list[list[str]] per FR-82

    # State + lifecycle
    delivery_state: Mapped[str] = mapped_column(String(32))
    prior_delivery_state: Mapped[str | None] = mapped_column(String(32), nullable=True)
    expected_completion_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    actual_completion_date: Mapped[datetime | None] = mapped_column(Date, nullable=True)
    item_completion_pct: Mapped[int] = mapped_column(Integer, default=0)

    # 4-field owner identity per [D-080] + [D-086]
    owner_corp_usa_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    owner_corp_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    owner_corp_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # Tracking gates per FR-78 / FR-81
    tracking_modality: Mapped[list] = mapped_column(JSON, default=list)  # multi-value per [D-037]
    force_tracking_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    no_customer_upload: Mapped[bool] = mapped_column(Boolean, default=False)
    milestone_gating: Mapped[bool] = mapped_column(Boolean, default=True)
    review_required: Mapped[bool] = mapped_column(Boolean, default=False)
    review_status: Mapped[str] = mapped_column(String(32), default="not_required")
    manual_triage_required: Mapped[bool] = mapped_column(Boolean, default=False)
    rules_paused: Mapped[bool] = mapped_column(Boolean, default=False)  # FR-31 sub-1

    # FR-7 + FR-10 + NFR-21 §5: counters + outreach timestamps
    doc_count: Mapped[int] = mapped_column(Integer, default=1)
    doc_count_received: Mapped[int] = mapped_column(Integer, default=0)  # FR-28 doc_count_reached derivation source
    reminder_count: Mapped[int] = mapped_column(Integer, default=0)
    last_owner_contacted: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_reminder_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_owner_response_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True))

    # Form-factor flags (7 flags per [D-084])
    handset: Mapped[bool] = mapped_column(Boolean, default=False)
    tablet: Mapped[bool] = mapped_column(Boolean, default=False)
    wearable: Mapped[bool] = mapped_column(Boolean, default=False)
    ir: Mapped[bool] = mapped_column(Boolean, default=False)
    osmr: Mapped[bool] = mapped_column(Boolean, default=False)
    rmr: Mapped[bool] = mapped_column(Boolean, default=False)
    hmr_smr: Mapped[bool] = mapped_column(Boolean, default=False)

    # Carrier-portal delivery (per-item denorm per [D-126])
    customer_delivery_info: Mapped[str | None] = mapped_column(String(512), nullable=True)
    customer_delivery_credential_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # FR-77 routing
    ingress_folder: Mapped[str | None] = mapped_column(String(512), nullable=True)
    target_folder: Mapped[str | None] = mapped_column(String(512), nullable=True)

    # FR-78 path components
    sort_order: Mapped[int] = mapped_column(Integer)
    path_id: Mapped[str] = mapped_column(String(128))
    item_path_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tg_path_id: Mapped[str | None] = mapped_column(String(128), nullable=True)

    # TG-denormalized per [D-051]
    tg_name: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    ingress_nsd: Mapped[str] = mapped_column(String(32), default="None")
    folder_routing_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    tg_email_group_alias: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tg_owner_name: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tg_owner_corp_usa_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tg_owner_corp_email: Mapped[str | None] = mapped_column(String(256), nullable=True)
    tg_owner_corp_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    corp_id_list: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # Other denormalized fields
    actual_item_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    plm_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    owner_status_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_cc_list: Mapped[list | None] = mapped_column(JSON, nullable=True)  # list[dict]

    # JIRA polling state (Ph-2 per FR-25 (b))
    jira_open_ticket_count: Mapped[int] = mapped_column(Integer, default=0)
    jira_ticket_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)

    # PM-approval per [D-068]
    pm_approval_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    pm_approval_pm_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # owner_intent_closed_at -- see DeliveryItemBase docstring; persisted
    # when owner replies "Closed" but doc_count_not_reached blocks transition.
    # Reconcile rule auto-advances state when docs catch up. Architect
    # 2026-06-29 race-resolution.
    owner_intent_closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )

    # TPM-resolution per FR-83 / FR-87
    tpm_reassignment_target_item_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


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
    elif resolved.startswith("postgresql"):
        # NullPool for postgres -- each connection created fresh per query +
        # disposed after. Trades a small perf cost for compatibility with the
        # sync_bridge.run_async_sync pattern (used by PostgresStorage +
        # PostgresAuditWriter under Celery prefork): every asyncio.run() call
        # creates a new event loop, and asyncpg connections from a previous
        # loop's pool can't be reused (raises InterfaceError). NullPool
        # bypasses pool reuse entirely. Added 2026-06-27 per live diagnosis
        # on architect's Linux box.
        from sqlalchemy.pool import NullPool

        kwargs["poolclass"] = NullPool
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
    rolled back (best-effort) and re-raised as a PipelineError; PipelineError
    passes through untranslated. The ops modules use this exclusively.

    Error classification (2026-06-30):
    - Data / integrity errors (constraint violation, NOT NULL, string truncation,
      bad enum, FK miss, etc.) -> STR-E003. These are deterministic — retrying
      won't help; caller logic / schema needs to change.
    - Everything else (connection drop, asyncpg interface error, unknown) ->
      STR-E001. These may be transient and worth a bounded retry by caller.

    Pre-2026-06-30 behavior: ALL exceptions wrapped as STR-E001 ("Postgres
    unavailable or session failure"), which falsely implied transience and
    masked deterministic constraint bugs as retryable connection blips.
    Surfaced when a 70-char doc_id_slug overflowed the 64-char column and the
    caller's retry loop burned 3 attempts on what was actually a column-width
    bug.
    """
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
            code = _classify_db_exception(exc)
            if code == "STR-E003":
                raise PipelineError(
                    "STR-E003",
                    context={"entity": "session_scope", "reason": str(exc)[:120]},
                    cause=exc,
                )
            raise PipelineError("STR-E001", context={"reason": str(exc)[:120]}, cause=exc)


def _classify_db_exception(exc: BaseException) -> str:
    """Return 'STR-E003' for deterministic data/integrity errors, 'STR-E001'
    for transient/unknown errors. Matches by exception class name so we don't
    have to import asyncpg / SQLAlchemy exception hierarchies at module top.

    Deterministic (STR-E003): DataError, IntegrityError, ProgrammingError,
      StringDataRightTruncation, NotNullViolation, UniqueViolation,
      ForeignKeyViolation, CheckViolation, InvalidTextRepresentation, ...
    Transient/unknown (STR-E001): InterfaceError, ConnectionDoesNotExistError,
      OperationalError (without integrity nesting), TimeoutError, ...

    Walks __cause__/__context__ since SQLAlchemy wraps asyncpg errors.
    """
    DETERMINISTIC_TOKENS = (
        "DataError",
        "IntegrityError",
        "ProgrammingError",
        "Truncation",          # StringDataRightTruncationError
        "NotNullViolation",
        "UniqueViolation",
        "ForeignKeyViolation",
        "CheckViolation",
        "InvalidText",         # InvalidTextRepresentationError (bad enum)
        "Constraint",
    )
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = type(current).__name__
        if any(tok in name for tok in DETERMINISTIC_TOKENS):
            return "STR-E003"
        current = current.__cause__ or current.__context__
    return "STR-E001"


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
