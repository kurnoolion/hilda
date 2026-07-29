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
- find_items_by_natural_key(customer_id, tg_name, item_no, device_id=None) -> list[DeliveryItemBase]
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from core.src.diagnostics.error_codes import PipelineError
from core.src.storage._sync_bridge import run_async_sync
from core.src.storage.db import (
    CommunicationLogTable,
    DeliveryItemTable,
    DocumentIndexTable,
    DocumentItemAssociationTable,
    DocumentVersionTable,
    session_scope,
)
from core.src.template_schema import DeliveryItemBase

_log = logging.getLogger(__name__)

__all__ = [
    "get_delivery_item",
    "create_delivery_item",
    "update_delivery_item",
    "list_items_for_milestone",
    "list_default_workitem_for_milestone",
    "find_items_by_natural_key",
    "delete_milestone_cascade",
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
    # DEBUG-1 (2026-07-26 architect ask): catch-all item_description write log.
    # Fires regardless of the upstream caller, so we see item_description on
    # every INSERT path (import, backfill, manual). Empty writes are flagged.
    _desc = kwargs.get("item_description")
    _log.info(
        "delivery_item INSERT: item_id=%s item_no=%s item_description=%r%s",
        item_id, kwargs.get("item_no"), _desc,
        " [WARN empty!]" if not _desc else "",
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
    # DEBUG-1 (2026-07-26 architect ask): catch-all item_description write log.
    # Only fires when the UPDATE touches item_description -- typical field
    # updates (delivery_state, doc_count, owner_status_note) skip this log
    # to keep it targeted.
    if "item_description" in fields:
        _desc = fields["item_description"]
        _log.info(
            "delivery_item UPDATE item_description: item_id=%s value=%r%s",
            item_id, _desc,
            " [WARN empty!]" if not _desc else "",
        )
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


async def get_by_customer_and_sp_id(
    customer_id: str, sp_id: int,
) -> DeliveryItemBase | None:
    """Dashboard /docs/<customer_id>/<sp_id> lookup per architect lock 2026-07-01.
    Returns the DeliveryItem whose sp_id matches SP's Deliverables list row Id.
    Returns None when no row matches -- dashboard responds 404."""
    async with session_scope() as session:
        stmt = select(DeliveryItemTable).where(
            DeliveryItemTable.customer_id == customer_id,
            DeliveryItemTable.sp_id == sp_id,
        ).limit(1)
        row = (await session.execute(stmt)).scalars().first()
        return _row_to_pydantic(row) if row is not None else None


async def find_items_by_natural_key(
    customer_id: str, tg_name: str, item_no: int,
    device_id: str | None = None,
) -> list[DeliveryItemBase]:
    """Idempotency lookup per [D-118] Chunk 3.

    Two callers with DIFFERENT semantics:
      - sp_alert_imports (import idempotency): pass device_id -- lookup is
        scoped to (customer, device, tg, item_no). Prevents cross-device
        dedup when TPM sets up N devices and SP fires N ADDED alerts per
        work_item -- previously the (customer, tg, item_no)-only lookup
        treated device 2..N as duplicates and dropped them.
      - tag_propagation (FR-82): pass device_id=None -- INTENTIONALLY spans
        devices so a tag update propagates to all devices' copies of the
        matching work_item per tracker/MODULE.md.

    device_id is keyword-only when supplied and defaults to None to preserve
    backward compat with the FR-82 caller.
    """
    async with session_scope() as session:
        conditions = [
            DeliveryItemTable.customer_id == customer_id,
            DeliveryItemTable.tg_name == tg_name,
            DeliveryItemTable.item_no == item_no,
        ]
        if device_id is not None:
            conditions.append(DeliveryItemTable.device_id == device_id)
        stmt = select(DeliveryItemTable).where(*conditions)
        rows = (await session.execute(stmt)).scalars().all()
        return [_row_to_pydantic(r) for r in rows]


# ----------------------------------------------------------------------------
# MDEL-1 (2026-07-28): Milestone-scoped Postgres cascade delete
# ----------------------------------------------------------------------------


async def delete_milestone_cascade(
    customer_id: str, device_id: str, milestone_id: str,
) -> dict[str, Any]:
    """Aggressive cleanup of all Postgres rows for the (customer, device,
    milestone) scope. Called by apply_milestone_delete_task on a Milestones-
    list DELETE alert.

    Ordering:
      1. Enumerate delivery_item.item_id in scope.
      2. Enumerate file_hash values referenced by those items' associations.
      3. DELETE document_item_association WHERE delivery_item_id IN (item_ids).
      4. Re-query surviving associations for the same file_hashes -- any hash
         still referenced by some OTHER milestone's item is kept in
         document_index (dedup preservation).
      5. DELETE document_version WHERE (customer, device, milestone) matches.
      6. DELETE communication_log WHERE delivery_item_id IN (item_ids).
      7. DELETE document_index for hashes that became orphaned in step 4.
      8. DELETE delivery_item rows.

    Returns a summary dict of counts + the orphaned file_hash list (caller
    uses that for optional file-hash-scoped internal-storage cleanup).

    Idempotent: repeated invocation on an already-cleaned scope returns
    zeros. No FK cascades exist in the schema -- explicit deletes only.

    NOT WRAPPED IN A TRANSACTION at this layer; session_scope commits
    incrementally. A crash mid-cleanup leaves partial state (some rows
    deleted, others not). Acceptable per architect 2026-07-28: Ph-1 test
    scenario, worst case is a re-run of the delete alert (idempotent).
    """
    async with session_scope() as session:
        item_ids_result = await session.execute(
            select(DeliveryItemTable.item_id).where(
                DeliveryItemTable.customer_id == customer_id,
                DeliveryItemTable.device_id == device_id,
                DeliveryItemTable.milestone_id == milestone_id,
            )
        )
        item_ids: list[str] = list(item_ids_result.scalars().all())

        if not item_ids:
            return {
                "items_deleted":    0,
                "assocs_deleted":   0,
                "audit_deleted":    0,
                "versions_deleted": 0,
                "index_deleted":    0,
                "orphan_hashes":    [],
            }

        # Step 2: hashes referenced by items in this scope (may be shared
        # with other milestones -- checked post-delete in step 4).
        hashes_result = await session.execute(
            select(DocumentItemAssociationTable.file_hash).where(
                DocumentItemAssociationTable.delivery_item_id.in_(item_ids)
            ).distinct()
        )
        hashes_this_scope: set[str] = set(hashes_result.scalars().all())

        # Step 3: delete the associations for our items.
        assocs_del = await session.execute(
            delete(DocumentItemAssociationTable).where(
                DocumentItemAssociationTable.delivery_item_id.in_(item_ids)
            )
        )
        assocs_deleted = assocs_del.rowcount or 0

        # Step 4: refcount check for document_index orphans.
        orphan_hashes: list[str] = []
        if hashes_this_scope:
            still_ref_result = await session.execute(
                select(DocumentItemAssociationTable.file_hash).where(
                    DocumentItemAssociationTable.file_hash.in_(hashes_this_scope)
                ).distinct()
            )
            still_referenced = set(still_ref_result.scalars().all())
            orphan_hashes = sorted(hashes_this_scope - still_referenced)

        # Step 5: document_version rows for this scope (view tree).
        versions_del = await session.execute(
            delete(DocumentVersionTable).where(
                DocumentVersionTable.customer_id == customer_id,
                DocumentVersionTable.device_id == device_id,
                DocumentVersionTable.milestone_id == milestone_id,
            )
        )
        versions_deleted = versions_del.rowcount or 0

        # Step 6: communication_log rows for our items (audit for THIS scope
        # goes away; global/non-item audits are preserved).
        audit_del = await session.execute(
            delete(CommunicationLogTable).where(
                CommunicationLogTable.delivery_item_id.in_(item_ids)
            )
        )
        audit_deleted = audit_del.rowcount or 0

        # Step 7: document_index for orphaned hashes.
        index_deleted = 0
        if orphan_hashes:
            index_del = await session.execute(
                delete(DocumentIndexTable).where(
                    DocumentIndexTable.file_hash.in_(orphan_hashes)
                )
            )
            index_deleted = index_del.rowcount or 0

        # Step 8: delivery_item rows themselves.
        items_del = await session.execute(
            delete(DeliveryItemTable).where(
                DeliveryItemTable.item_id.in_(item_ids)
            )
        )
        items_deleted = items_del.rowcount or 0

        # Commit -- session_scope() only rolls back on error; without an
        # explicit commit every delete() above stays staged and rolls back
        # when the session context exits, leaving Postgres unchanged while
        # the return values (SQLAlchemy's as-if rowcounts) report success.
        # Bug caught in first production test 2026-07-28: task returned
        # cascade_completed with counts but 87 delivery_item rows survived.
        await session.commit()

        return {
            "items_deleted":    items_deleted,
            "assocs_deleted":   assocs_deleted,
            "audit_deleted":    audit_deleted,
            "versions_deleted": versions_deleted,
            "index_deleted":    index_deleted,
            "orphan_hashes":    orphan_hashes,
        }


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
        self, *, customer_id: str, tg_name: str, item_no: int,
        device_id: str | None = None,
    ) -> list[DeliveryItemBase]:
        return run_async_sync(
            lambda: find_items_by_natural_key(
                customer_id, tg_name, item_no, device_id=device_id,
            )
        )

    def get_by_customer_and_sp_id(
        self, customer_id: str, sp_id: int,
    ) -> DeliveryItemBase | None:
        """Sync wrapper for the dashboard's /docs/<customer_id>/<sp_id> lookup."""
        return run_async_sync(
            lambda: get_by_customer_and_sp_id(customer_id, sp_id)
        )

    def delete_milestone_cascade(
        self, customer_id: str, device_id: str, milestone_id: str,
    ) -> dict[str, Any]:
        """MDEL-1 (2026-07-28): sync wrapper for apply_milestone_delete_task."""
        return run_async_sync(
            lambda: delete_milestone_cascade(customer_id, device_id, milestone_id)
        )

    # ----------------------------------------------------------------------
    # Inbound attachment ops (Step 5.5 cascade, architect 2026-06-29)
    # ----------------------------------------------------------------------

    def list_classified_associations_for_item(
        self, delivery_item_id: str,
    ) -> list[Any]:
        """Sync wrapper for submit_to_carrier cascade -- returns only
        DocumentItemAssociation rows with nsd_path_type='classified',
        ordered by file_hash for deterministic sequencing."""
        from core.src.storage.document_ops import (
            list_classified_associations_for_item as _list,
        )
        return run_async_sync(lambda: _list(delivery_item_id))

    def list_documents_for_item_display(
        self, delivery_item_id: str,
    ) -> list[Any]:
        """Sync wrapper for dashboard Ph-1: (original_filename, doc_type,
        ingested_at) tuples ordered by ingested_at DESC, classified-only."""
        from core.src.storage.document_ops import (
            list_documents_for_item_display as _list,
        )
        return run_async_sync(lambda: _list(delivery_item_id))

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

    def get_local_nsd_path_for_file_hash(self, file_hash: str) -> str | None:
        """Sync wrapper for get_local_nsd_path_for_file_hash. Added 2026-07-07
        for cross-device shared-file fix: when the same file re-arrives for
        new items, _persist_routed_attachment reuses the original NSD path so
        associations point at the actual stored bytes."""
        from core.src.storage.document_ops import get_local_nsd_path_for_file_hash as _get
        return run_async_sync(lambda: _get(file_hash))

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
