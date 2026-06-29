"""SQLAlchemy-backed CRUD + query ops for DeliveryItem rows.

Added 2026-06-27 per architect direction during rule-walk-through 2026-06-27:
HILDA's Postgres-backed StorageWriter implementation -- the missing piece
between sp_alert_parser (working) and workflow_engine task bodies (which all
need storage).

Pattern mirrors document_ops.py (async functions + session_scope) + provides
a sync PostgresStorage class that conforms to tracker.StorageWriter Protocol
via asyncio.run wrappers (for sync Celery task body callers).

Surface (StorageWriter Protocol-aligned, 6 methods):
- get_delivery_item(item_id)        -> DeliveryItemBase | None
- create_delivery_item(item)        -> str (item_id)
- update_delivery_item(item_id, fields) -> None
- list_items_for_milestone(milestone_id, states=None) -> list[DeliveryItemBase]
- list_default_workitem_for_milestone(milestone_id) -> DeliveryItemBase | None
- find_items_by_natural_key(customer_id, tg_name, item_no) -> list[DeliveryItemBase]
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from core.src.diagnostics.error_codes import PipelineError
from core.src.storage._sync_bridge import run_async_sync
from core.src.storage.db import DeliveryItemTable, session_scope
from core.src.template_schema import DeliveryItemBase

__all__ = [
    "get_delivery_item",
    "create_delivery_item",
    "update_delivery_item",
    "list_items_for_milestone",
    "list_default_workitem_for_milestone",
    "find_items_by_natural_key",
    "PostgresStorage",
]


# ----------------------------------------------------------------------------
# Row <-> Pydantic conversion
# ----------------------------------------------------------------------------


def _row_to_pydantic(row: DeliveryItemTable) -> DeliveryItemBase:
    """Map ORM row -> DeliveryItemBase. Uses model_config = extra='allow' so
    customer_id + device_id (not on DeliveryItemBase proper) survive
    round-trip."""
    data = {
        col.name: getattr(row, col.name)
        for col in DeliveryItemTable.__table__.columns
    }
    # Pydantic may complain about extra keys; _Base has extra='allow', so OK.
    return DeliveryItemBase(**data)


def _pydantic_to_row_kwargs(item: DeliveryItemBase | Any) -> dict[str, Any]:
    """Map DeliveryItemBase (or duck-typed namespace) -> kwargs dict for
    DeliveryItemTable(**kwargs). Filters to known table columns only so
    arbitrary Pydantic extras don't cause SQL InvalidColumn errors."""
    table_cols = {col.name for col in DeliveryItemTable.__table__.columns}
    kwargs: dict[str, Any] = {}
    for col_name in table_cols:
        val = getattr(item, col_name, None)
        if val is not None:
            kwargs[col_name] = val
    return kwargs


# ----------------------------------------------------------------------------
# Async ops (mirror document_ops.py pattern)
# ----------------------------------------------------------------------------


async def get_delivery_item(item_id: str) -> DeliveryItemBase | None:
    """Read a DeliveryItem by item_id. Returns None when not found."""
    async with session_scope() as session:
        row = await session.get(DeliveryItemTable, item_id)
        if row is None:
            return None
        return _row_to_pydantic(row)


async def create_delivery_item(item: DeliveryItemBase | Any) -> str:
    """Insert a new DeliveryItem row. Raises STR-E003 on duplicate item_id
    (unique-PK violation) -- per [D-118] Chunk 3 idempotency contract: caller
    must find_items_by_natural_key first."""
    kwargs = _pydantic_to_row_kwargs(item)
    item_id = kwargs.get("item_id")
    if not item_id:
        raise PipelineError(
            "STR-E003",
            context={"entity": "delivery_item", "reason": "item_id is required"},
        )
    async with session_scope() as session:
        try:
            row = DeliveryItemTable(**kwargs)
            session.add(row)
            await session.commit()
        except IntegrityError as exc:
            raise PipelineError(
                "STR-E003",
                context={"entity": "delivery_item", "reason": f"unique violation on item_id={item_id}: {type(exc).__name__}"},
            ) from exc
    return item_id


async def update_delivery_item(item_id: str, fields: dict[str, Any]) -> None:
    """Patch existing DeliveryItem fields. Silently no-ops if not found (caller
    can verify via get_delivery_item)."""
    if not fields:
        return
    async with session_scope() as session:
        row = await session.get(DeliveryItemTable, item_id)
        if row is None:
            return
        for k, v in fields.items():
            if hasattr(row, k):
                setattr(row, k, v)
        await session.commit()


async def list_items_for_milestone(
    milestone_id: str, states: list[str] | None = None
) -> list[DeliveryItemBase]:
    """List all items for a milestone, optionally filtered by delivery_state."""
    async with session_scope() as session:
        stmt = select(DeliveryItemTable).where(
            DeliveryItemTable.milestone_id == milestone_id
        )
        if states:
            stmt = stmt.where(DeliveryItemTable.delivery_state.in_(states))
        stmt = stmt.order_by(DeliveryItemTable.sort_order, DeliveryItemTable.item_no)
        rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_pydantic(r) for r in rows]


async def list_default_workitem_for_milestone(
    milestone_id: str,
) -> DeliveryItemBase | None:
    """Return the single Default WI row for the milestone, or None.
    Per FR-78: exactly one Default WI per milestone (item_type='Default')."""
    async with session_scope() as session:
        stmt = select(DeliveryItemTable).where(
            DeliveryItemTable.milestone_id == milestone_id,
            DeliveryItemTable.item_type == "Default",
        ).limit(1)
        row = (await session.execute(stmt)).scalars().first()
        return _row_to_pydantic(row) if row is not None else None


async def find_items_by_natural_key(
    customer_id: str, tg_name: str, item_no: int
) -> list[DeliveryItemBase]:
    """Idempotency lookup per [D-118] Chunk 3:
    (customer_id, tg_name, item_no) is the natural key SP uses to identify a
    Deliverable row pre-import."""
    async with session_scope() as session:
        stmt = select(DeliveryItemTable).where(
            DeliveryItemTable.customer_id == customer_id,
            DeliveryItemTable.tg_name == tg_name,
            DeliveryItemTable.item_no == item_no,
        )
        rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_pydantic(r) for r in rows]


# ----------------------------------------------------------------------------
# Sync wrapper conforming to StorageWriter Protocol -- for Celery task bodies
# ----------------------------------------------------------------------------


class PostgresStorage:
    """Sync wrapper around the async ops above, conforming to
    tracker.StorageWriter Protocol.

    Celery task bodies (workflow_engine.tasks.*) are sync; this wrapper bridges
    by running each async op in a fresh event loop via asyncio.run. Same
    pattern used in workflow_engine.tasks.submission._resolve_upload_params.

    Constructor takes no args -- assumes configure_engine() has already been
    called (typically in bootstrap_task_deps() or a per-deployment startup
    script). Tests pass sqlite+aiosqlite URLs; deployment reads
    HILDA_STORAGE_DB_URL (postgres).
    """

    def get_delivery_item(self, item_id: str) -> DeliveryItemBase | None:
        return run_async_sync(lambda: get_delivery_item(item_id))

    def create_delivery_item(self, item: DeliveryItemBase | Any) -> str:
        return run_async_sync(lambda: create_delivery_item(item))

    def update_delivery_item(self, item_id: str, fields: dict[str, Any]) -> None:
        return run_async_sync(lambda: update_delivery_item(item_id, fields))

    def write_delivery_state(
        self,
        *,
        delivery_item_id: str,
        new_state: Any,            # DeliveryState enum
        modified_at: Any,          # datetime
        modified_by: str | None = None,
    ) -> None:
        """No-op stub on PostgresStorage for the Ph-1 cascade.

        tracker.transitions.update_delivery_state calls this alongside
        update_delivery_item to record a state-history audit row in a
        separate table. That table isn't yet wired in storage (deferred);
        the current-state column on delivery_item IS already persisted by
        update_delivery_item via field_updates['delivery_state'], so this
        no-op keeps the transition cascade unblocked. State-history audit
        will land when a state_transition_log ORM table + ops are added.

        Architect Step 4 unblock 2026-06-28: prior AttributeError
        'PostgresStorage object has no attribute write_delivery_state'
        crashed every kickoff_collection NS->Open + every downstream
        UpdateState Open->OutreachSent, breaking the cascade midway
        between send_initial_outreach (already written) and the
        state-transition audit.
        """
        return None

    def list_items_for_milestone(
        self, milestone_id: str, states: list[str] | None = None
    ) -> list[DeliveryItemBase]:
        return run_async_sync(lambda: list_items_for_milestone(milestone_id, states))

    def list_default_workitem_for_milestone(
        self, milestone_id: str
    ) -> DeliveryItemBase | None:
        return run_async_sync(lambda: list_default_workitem_for_milestone(milestone_id))

    def find_items_by_natural_key(
        self, *, customer_id: str, tg_name: str, item_no: int
    ) -> list[DeliveryItemBase]:
        return run_async_sync(
            lambda: find_items_by_natural_key(customer_id, tg_name, item_no)
        )

    # ----------------------------------------------------------------------
    # Inbound attachment ops (Step 5.5 cascade, architect 2026-06-29)
    # ----------------------------------------------------------------------

    def add_document_index_row(self, row: Any) -> None:
        """Sync wrapper around document_ops.add_document_index_row."""
        from core.src.storage.document_ops import add_document_index_row as _add
        return run_async_sync(lambda: _add(row))

    def add_document_item_association(self, assoc: Any) -> None:
        """Sync wrapper around document_ops.add_document_item_association."""
        from core.src.storage.document_ops import add_document_item_association as _add
        return run_async_sync(lambda: _add(assoc))

    def get_document_index_row_by_hash(self, file_hash: str) -> Any:
        """Sync wrapper for Step 0 dedup lookup (called by Fr52AttachmentRouter)."""
        from core.src.storage.document_ops import get_document_index_row_by_hash as _get
        return run_async_sync(lambda: _get(file_hash))

    def find_doc_id_slugs_for_item(self, delivery_item_id: str, doc_type: Any) -> list[str]:
        """Sync wrapper for Step C slug match (Ph-2 path; sync wrapper added now for completeness)."""
        from core.src.storage.document_ops import find_doc_id_slugs_for_item as _find
        return run_async_sync(lambda: _find(delivery_item_id, doc_type))

    def write_attachment_bytes(self, nsd_path: Any, content: bytes) -> Any:
        """Sync wrapper around attachment_ops.write_attachment_bytes."""
        from core.src.storage.attachment_ops import write_attachment_bytes as _write
        return run_async_sync(lambda: _write(nsd_path, content))

    def increment_doc_count_received(
        self, delivery_item_id: str, by: int = 1,
    ) -> int:
        """Sync wrapper around attachment_ops.increment_doc_count_received."""
        from core.src.storage.attachment_ops import increment_doc_count_received as _inc
        return run_async_sync(lambda: _inc(delivery_item_id, by=by))
