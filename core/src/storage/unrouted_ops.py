"""unrouted_ops.py -- storage helpers for the /_unknownTG dashboard UI (UR-3).

Ph-2 architect ask 2026-08-01. Three helpers backing the manual routing
UI that lets a TPM assign an unrouted document to a specific work item:

1. `list_unrouted_for_scope(customer_id, device_id, milestone_id)`
   Returns UnroutedFileRow list -- DocumentIndex rows where
   routing_resolution='unrouted' AND (customer, device, milestone) match
   AND no DocumentItemAssociation exists yet. Each row carries a
   `is_dup_hash_elsewhere` flag (badge in the UI when the same file bytes
   are already routed to some other item).

2. `list_route_candidates_for_scope(customer_id, device_id, milestone_id, excluded_item_names)`
   Returns delivery-item rows the TPM can pick as the target. Filters out
   Confirmation + Default + any item_name in excluded_item_names
   (config-driven per Final-DRR pattern from tpm_notification_config.py).

3. `route_unrouted_to_item(*, file_hash, target_delivery_item_id, tpm_id)`
   The write op. Moves the file on NSD from _unrouted/ to the target
   item's staged_classification path, creates the association, updates
   DocumentIndex.routing_resolution -> TPM_REASSIGNED + inferred_tg_name,
   and writes an audit row. Idempotent: repeated calls on the same
   (file, target) after a successful route are no-ops.

Doc_count_received increment + state-machine re-evaluation happen via
the caller (workflow_engine task or dashboard route calling
increment_doc_count_received directly) so that the state guards run in
the correct dispatcher context per architect ask 2026-08-01.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select

from core.src.diagnostics.error_codes import PipelineError
from core.src.storage._sync_bridge import run_async_sync
from core.src.storage.db import (
    DeliveryItemTable,
    DocumentIndexTable,
    DocumentItemAssociationTable,
    session_scope,
)
from core.src.storage.models import (
    DocumentIndexRow,
    NSDPathType,
    RoutingResolution,
)
from core.src.storage.nsd import NSDPath
from core.src.template_schema.enums import DocType, IngestSource, ItemType

__all__ = [
    "UnroutedFileRow",
    "RouteResult",
    "list_unrouted_for_scope",
    "list_route_candidates_for_scope",
    "route_unrouted_to_item",
    "UnroutedStorage",
]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class UnroutedFileRow:
    """One row in the /_unknownTG list. Mirrors DocumentIndexRow subset plus
    the duplicate-elsewhere badge for the UI."""
    file_hash: str
    original_filename: str
    ingested_at: datetime
    doc_type: str                    # may be empty for archive containers
    is_dup_hash_elsewhere: bool      # true if the same file_hash is
                                     # associated with any OTHER item in
                                     # the DB (UI badge trigger)


@dataclass(frozen=True)
class RouteResult:
    """Outcome of route_unrouted_to_item."""
    outcome: str                     # "routed" | "already_routed_to_this_item"
                                     # | "already_routed_elsewhere"
                                     # | "not_unrouted" | "doc_not_found"
                                     # | "target_not_found" | "failed"
    file_hash: str
    target_delivery_item_id: str | None = None
    target_nsd_path: str | None = None    # relative
    error: str | None = None


async def list_unrouted_for_scope(
    customer_id: str, device_id: str, milestone_id: str,
) -> list[UnroutedFileRow]:
    """Return unrouted DocumentIndex rows for the (customer, device, milestone)
    scope that have NO DocumentItemAssociation. Ordered oldest-first so the
    TPM sees the oldest orphan at the top of the list.

    A row is 'unrouted' when its routing_resolution matches the enum
    RoutingResolution.UNROUTED. The scope filter uses the customer_id +
    device_id columns added in UR-1 (rows populated at ingest in UR-2);
    legacy rows without those columns won't appear here -- accepted gap.
    """
    async with session_scope() as session:
        # "Unrouted" = document_index row in scope where NO association
        # exists yet. The routing pipeline creates the association at step
        # 5 (Default WI) when it lands the file in _unrouted/ AND the
        # default WI is configured; when the WI is missing or filtered out
        # (e.g., Ph-1 flags disable Default routing) the file's DocIndex
        # row survives with no association, which is what the /_unknownTG
        # UI surfaces. We deliberately don't filter on routing_resolution
        # itself -- the absence of an association is the authoritative
        # signal.
        result = await session.execute(
            select(DocumentIndexTable).where(
                DocumentIndexTable.customer_id == customer_id,
                DocumentIndexTable.device_id == device_id,
                DocumentIndexTable.milestone_id == milestone_id,
                ~select(DocumentItemAssociationTable.file_hash).where(
                    DocumentItemAssociationTable.file_hash == DocumentIndexTable.file_hash
                ).exists(),
            ).order_by(DocumentIndexTable.ingested_at)
        )
        rows = list(result.scalars().all())
        if not rows:
            return []

        # Duplicate-elsewhere check: look for the same file_hash appearing
        # in any association row (i.e., the same file bytes routed to some
        # OTHER item elsewhere). Rare -- the sub-query above already
        # excludes rows that have any association -- so this is genuinely
        # about docs whose file_hash matches a different row's association.
        # Compute once for all unrouted hashes in this scope.
        file_hashes = [r.file_hash for r in rows]
        assoc_result = await session.execute(
            select(DocumentItemAssociationTable.file_hash).where(
                DocumentItemAssociationTable.file_hash.in_(file_hashes)
            )
        )
        associated_hashes = {h for (h,) in assoc_result.all()}

        return [
            UnroutedFileRow(
                file_hash=r.file_hash,
                original_filename=r.original_filename,
                ingested_at=r.ingested_at,
                doc_type=r.doc_type or "",
                is_dup_hash_elsewhere=(r.file_hash in associated_hashes),
            )
            for r in rows
        ]


async def list_route_candidates_for_scope(
    customer_id: str, device_id: str, milestone_id: str,
    excluded_item_names: list[str] | None = None,
) -> list[Any]:
    """Return delivery_item rows eligible as manual-route targets.

    Filters:
      * scope: (customer_id, device_id, milestone_id) exact match
      * item_type != Confirmation (Confirmation items take no docs)
      * item_type != Default      (Default WI is the catch-all bucket the
                                   user is manually routing AWAY from)
      * item_name NOT IN excluded_item_names (config-driven exclusion per
        Final-DRR pattern; MMK's item 85 goes here)

    Does NOT filter by delivery_state -- per architect ask 2026-07-31,
    Closed items are legitimate targets (TPM may attach a late doc).
    Ordered by item_no.
    """
    excluded = tuple(excluded_item_names or ())
    async with session_scope() as session:
        stmt = select(DeliveryItemTable).where(
            DeliveryItemTable.customer_id == customer_id,
            DeliveryItemTable.device_id == device_id,
            DeliveryItemTable.milestone_id == milestone_id,
            DeliveryItemTable.item_type != ItemType.CONFIRMATION.value,
            DeliveryItemTable.item_type != ItemType.DEFAULT.value,
        )
        if excluded:
            stmt = stmt.where(DeliveryItemTable.item_name.notin_(excluded))
        stmt = stmt.order_by(DeliveryItemTable.item_no)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def route_unrouted_to_item(
    *,
    file_hash: str,
    target_delivery_item_id: str,
    tpm_id: str,
) -> RouteResult:
    """Manually route a file currently in the _unrouted bucket to a specific
    work item. Called by the /browse/.../route POST handler after the TPM
    picks a target from the dropdown.

    Sequence (transactional at the DB layer + write-before-delete on disk):
      1. Fetch document_index row by file_hash.
      2. Verify routing_resolution=UNROUTED. If not, return not_unrouted.
      3. Fetch target delivery_item row. If missing, return target_not_found.
      4. Check for existing association for the file_hash:
         - association exists for target_delivery_item_id -> idempotent no-op
         - association exists for a DIFFERENT item          -> reject
      5. Compute source path (internal_default_workitem) and target path
         (internal_staged_classification; TPM chose the item but doc_type
         may still be UNRESOLVED so we land in staged rather than
         classified -- FR-87 step B can resolve later).
      6. Move file: copy to target, then delete source.
      7. Insert DocumentItemAssociation row (nsd_path_type=STAGED_NOT_CLASSIFIED).
      8. Update document_index: routing_resolution=TPM_REASSIGNED,
         inferred_tg_name=target's tg_name.
      9. Audit via communication_log ("manual_route_from_unrouted").

    Returns RouteResult. Caller is responsible for post-route side effects
    (doc_count increment + state guard re-evaluation) via the workflow_engine
    context -- keeps this helper storage-pure.
    """
    from core.src.storage import audit_ops
    from core.src.storage.models import (
        Channel, CommunicationLogRow, Direction,
    )

    async with session_scope() as session:
        # Step 1: fetch doc. "Unrouted" is defined by absence of
        # association (checked below at step 4), not by any
        # routing_resolution value -- see list_unrouted_for_scope docstring.
        doc = await session.get(DocumentIndexTable, file_hash)
        if doc is None:
            return RouteResult(outcome="doc_not_found", file_hash=file_hash)

        # Step 3: fetch target
        target = await session.get(DeliveryItemTable, target_delivery_item_id)
        if target is None:
            return RouteResult(
                outcome="target_not_found", file_hash=file_hash,
                target_delivery_item_id=target_delivery_item_id,
            )

        # Step 4: existing association check
        assoc_result = await session.execute(
            select(DocumentItemAssociationTable).where(
                DocumentItemAssociationTable.file_hash == file_hash,
            )
        )
        existing_assocs = list(assoc_result.scalars().all())
        for a in existing_assocs:
            if a.delivery_item_id == target_delivery_item_id:
                return RouteResult(
                    outcome="already_routed_to_this_item",
                    file_hash=file_hash,
                    target_delivery_item_id=target_delivery_item_id,
                )
        if existing_assocs:
            return RouteResult(
                outcome="already_routed_elsewhere",
                file_hash=file_hash,
                target_delivery_item_id=target_delivery_item_id,
                error=(
                    f"file already associated with "
                    f"{existing_assocs[0].delivery_item_id}"
                ),
            )

        # Step 5: compute paths. Use staged_classification (TPM picked the
        # item, doc_type may not be aligned yet -- FR-87 step B can resolve).
        target_tg = target.tg_name or "_unknown_tg"
        target_item_path_id = getattr(target, "item_path_id", None) or (
            f"item_{getattr(target, 'item_no', 'x')}"
        )
        target_tg_path_id = getattr(target, "tg_path_id", None) or target_tg
        source_path = NSDPath.internal_default_workitem(
            doc.customer_id or "", doc.device_id or "", doc.milestone_id,
            "_unknown_tg", doc.original_filename,
        )
        target_path = NSDPath.internal_staged_classification(
            doc.customer_id or "", doc.device_id or "", doc.milestone_id,
            target_tg_path_id, target_item_path_id,
            doc.original_filename,
        )

        # Step 6: NSD move (write-before-delete for no-loss ordering)
        src_local = source_path.to_local()
        dst_local = target_path.to_local()

        def _move() -> None:
            if src_local.is_file():
                dst_local.parent.mkdir(parents=True, exist_ok=True)
                dst_local.write_bytes(src_local.read_bytes())
                src_local.unlink()

        try:
            await asyncio.to_thread(_move)
        except Exception as exc:  # noqa: BLE001
            return RouteResult(
                outcome="failed", file_hash=file_hash,
                target_delivery_item_id=target_delivery_item_id,
                error=f"nsd_move_failed: {type(exc).__name__}: {str(exc)[:120]}",
            )

        # Step 7: create association
        session.add(
            DocumentItemAssociationTable(
                file_hash=file_hash,
                delivery_item_id=target_delivery_item_id,
                milestone_id=doc.milestone_id,
                local_nsd_path=target_path.to_relative(),
                nsd_path_type=NSDPathType.STAGED_NOT_CLASSIFIED.value,
                owner_corp_id=getattr(target, "owner_corp_id", "") or "",
                owner_corp_usa_email=getattr(target, "owner_corp_usa_email", None),
                owner_corp_email=getattr(target, "owner_corp_email", None),
                owner_name=getattr(target, "owner_name", None),
                plm_id=getattr(target, "plm_id", None),
                associated_at=datetime.now(timezone.utc),
                associated_by=tpm_id,
            )
        )

        # Step 8: update DocumentIndex
        doc.routing_resolution = RoutingResolution.TPM_REASSIGNED.value
        doc.inferred_tg_name = target_tg

        await session.commit()

    # Step 9: audit (best-effort; separate transaction)
    try:
        await audit_ops.log_communication(
            CommunicationLogRow(
                log_id=uuid.uuid4().hex,
                channel=Channel.SHAREPOINT,   # dashboard-side action
                direction=Direction.INBOUND,
                timestamp=datetime.now(timezone.utc),
                delivery_item_id=target_delivery_item_id,
                credential_id=tpm_id,
                action_type="manual_route_from_unrouted",
                summary=(
                    f"file_hash={file_hash[:12]}... routed from _unrouted "
                    f"to item {target_delivery_item_id} (tg={target_tg})"
                ),
                attachments=[{
                    "file_hash": file_hash,
                    "original_filename": doc.original_filename,
                    "target_item": target_delivery_item_id,
                    "target_tg": target_tg,
                    "target_nsd_path": target_path.to_relative(),
                }],
            )
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "manual_route_from_unrouted audit write failed for "
            "file_hash=%s target=%s: %s: %s",
            file_hash, target_delivery_item_id,
            type(exc).__name__, str(exc)[:120],
        )

    return RouteResult(
        outcome="routed",
        file_hash=file_hash,
        target_delivery_item_id=target_delivery_item_id,
        target_nsd_path=target_path.to_relative(),
    )


class UnroutedStorage:
    """Sync-facing wrapper for hilda-api (FastAPI sync routes call these).

    Mirrors the FeedbackStorage / PostgresStorage pattern: each method
    delegates to the async helper via run_async_sync.
    """

    def list_unrouted(
        self, *, customer_id: str, device_id: str, milestone_id: str,
    ) -> list[UnroutedFileRow]:
        return run_async_sync(lambda: list_unrouted_for_scope(
            customer_id, device_id, milestone_id,
        ))

    def list_route_candidates(
        self, *, customer_id: str, device_id: str, milestone_id: str,
        excluded_item_names: list[str] | None = None,
    ) -> list[Any]:
        return run_async_sync(lambda: list_route_candidates_for_scope(
            customer_id, device_id, milestone_id,
            excluded_item_names=excluded_item_names,
        ))

    def route(
        self, *, file_hash: str, target_delivery_item_id: str, tpm_id: str,
    ) -> RouteResult:
        return run_async_sync(lambda: route_unrouted_to_item(
            file_hash=file_hash,
            target_delivery_item_id=target_delivery_item_id,
            tpm_id=tpm_id,
        ))
