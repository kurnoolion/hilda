"""Document index + association operations per FR-13/17/52/55/57/79/83 and FR-61 tokens."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import uuid
from datetime import datetime, timezone

from sqlalchemy import delete, select, update

from core.src.diagnostics.error_codes import PipelineError
from core.src.storage.db import (
    DocumentIndexTable,
    DocumentItemAssociationTable,
    session_scope,
)
from core.src.storage.models import (
    Channel,
    CommunicationLogRow,
    Direction,
    DocumentIndexRow,
    DocumentItemAssociation,
    NSDPathType,
    PLMFanOutTarget,
    RevisionResolution,
    RoutingResolution,
)
from core.src.storage.nsd import NSDPath
from core.src.template_schema import DocType, IngestSource

__all__ = [
    "add_document_index_row",
    "add_document_item_association",
    "delete_document_item_association",
    "fan_out_plm_associations",
    "find_doc_id_slugs_for_item",
    "get_document_index_row_by_hash",
    "get_document_index_row_by_slug",
    "get_documents_for_item",
    "get_local_nsd_path_for_file_hash",
    "item_has_association",
    "list_associations_for_file",
    "list_associations_for_item",
    "list_classified_associations_for_item",
    "list_documents_for_item_display",
    "list_documents_for_milestone",
    "list_revisions",
    "make_download_token",
    "reassign_document_to_workitem",
    "resolve_download_token",
    "set_is_final",
    "tpm_resolve_doc_type",
    "tpm_resolve_revision",
    "update_association_plm_attachment",
    "update_review_findings",
]


_session = session_scope


def _row_to_model(row: DocumentIndexTable) -> DocumentIndexRow:
    return DocumentIndexRow(
        file_hash=row.file_hash,
        milestone_id=row.milestone_id,
        # UR-1 (Ph-2 2026-08-01): hydrate carrier + device_id back into the model.
        customer_id=row.customer_id,
        device_id=row.device_id,
        doc_type=DocType(row.doc_type),
        doc_id_slug=row.doc_id_slug,
        rev_number=row.rev_number,
        ingest_source=IngestSource(row.ingest_source),
        original_filename=row.original_filename,
        first_page_excerpt=row.first_page_excerpt,
        is_final=row.is_final,
        parser_result=row.parser_result,
        llm_review_findings=row.llm_review_findings,
        from_zip=row.from_zip,
        source_zip_filename=row.source_zip_filename,
        inferred_tg_name=row.inferred_tg_name,
        routing_resolution=RoutingResolution(row.routing_resolution),
        ingested_at=row.ingested_at,
    )


def _assoc_to_model(row: DocumentItemAssociationTable) -> DocumentItemAssociation:
    return DocumentItemAssociation(
        file_hash=row.file_hash,
        delivery_item_id=row.delivery_item_id,
        milestone_id=row.milestone_id,
        local_nsd_path=row.local_nsd_path,
        nsd_path_type=NSDPathType(row.nsd_path_type),
        owner_corp_id=row.owner_corp_id,
        owner_corp_usa_email=row.owner_corp_usa_email,
        owner_corp_email=row.owner_corp_email,
        owner_name=row.owner_name,
        plm_id=row.plm_id,
        plm_attachment_id=row.plm_attachment_id,
        upload_timestamp=row.upload_timestamp,
        associated_at=row.associated_at,
        associated_by=row.associated_by,
    )


# --- DocumentIndex operations ---------------------------------------------------


async def add_document_index_row(row: DocumentIndexRow) -> None:
    """Idempotent on file_hash — re-ingest of the same physical file is a no-op;
    per-ingest fields are first-write-wins (cross-channel idempotency)."""
    async with _session() as session:
        existing = await session.get(DocumentIndexTable, row.file_hash)
        if existing is not None:
            return
        session.add(
            DocumentIndexTable(
                file_hash=row.file_hash,
                milestone_id=row.milestone_id,
                # UR-1 (Ph-2 2026-08-01): carrier + device scoping for /_unknownTG UI.
                customer_id=row.customer_id,
                device_id=row.device_id,
                doc_type=row.doc_type.value,
                doc_id_slug=row.doc_id_slug,
                rev_number=row.rev_number,
                ingest_source=row.ingest_source.value,
                original_filename=row.original_filename,
                first_page_excerpt=row.first_page_excerpt,
                is_final=row.is_final,
                parser_result=row.parser_result,
                llm_review_findings=row.llm_review_findings,
                from_zip=row.from_zip,
                source_zip_filename=row.source_zip_filename,
                inferred_tg_name=row.inferred_tg_name,
                routing_resolution=row.routing_resolution.value,
                ingested_at=row.ingested_at,
            )
        )
        await session.commit()


async def get_document_index_row_by_hash(file_hash: str) -> DocumentIndexRow | None:
    async with _session() as session:
        row = await session.get(DocumentIndexTable, file_hash)
        return _row_to_model(row) if row else None


async def get_document_index_row_by_slug(
    milestone_id: str, doc_id_slug: str, rev_number: int
) -> DocumentIndexRow | None:
    """FR-57 lookup via the secondary unique constraint."""
    async with _session() as session:
        result = await session.execute(
            select(DocumentIndexTable).where(
                DocumentIndexTable.milestone_id == milestone_id,
                DocumentIndexTable.doc_id_slug == doc_id_slug,
                DocumentIndexTable.rev_number == rev_number,
            )
        )
        row = result.scalar_one_or_none()
        return _row_to_model(row) if row else None


async def find_doc_id_slugs_for_item(delivery_item_id: str, doc_type: DocType) -> list[str]:
    """[D-039] Step 1 slug match + Step 2 NEW_DOCUMENT short-circuit (empty result)."""
    async with _session() as session:
        result = await session.execute(
            select(DocumentIndexTable.doc_id_slug)
            .join(
                DocumentItemAssociationTable,
                DocumentItemAssociationTable.file_hash == DocumentIndexTable.file_hash,
            )
            .where(
                DocumentItemAssociationTable.delivery_item_id == delivery_item_id,
                DocumentIndexTable.doc_type == doc_type.value,
                DocumentIndexTable.doc_id_slug.is_not(None),
            )
            .distinct()
        )
        return sorted(s for (s,) in result.all())


async def list_revisions(milestone_id: str, doc_id_slug: str) -> list[DocumentIndexRow]:
    """All revisions of a (milestone, doc_id_slug) family, ordered by rev_number.
    Returns only RESOLVED rows — staged-fill rows with NULL doc_id_slug are excluded
    (not yet members of any revision family per the staged-fill lifecycle Invariant)."""
    async with _session() as session:
        result = await session.execute(
            select(DocumentIndexTable)
            .where(
                DocumentIndexTable.milestone_id == milestone_id,
                DocumentIndexTable.doc_id_slug == doc_id_slug,
                DocumentIndexTable.doc_id_slug.is_not(None),
            )
            .order_by(DocumentIndexTable.rev_number)
        )
        return [_row_to_model(r) for r in result.scalars().all()]


async def update_review_findings(
    file_hash: str, parser_result: dict | None, llm_review_findings: dict | None
) -> None:
    async with _session() as session:
        row = await session.get(DocumentIndexTable, file_hash)
        if row is None:
            raise PipelineError("STR-E002", context={"entity": "DocumentIndexRow", "key": file_hash})
        row.parser_result = parser_result
        row.llm_review_findings = llm_review_findings
        await session.commit()


async def set_is_final(file_hash: str, is_final: bool) -> None:
    """FR-66 — setting True auto-clears is_final on all sibling revisions of the
    same (milestone_id, doc_id_slug) family, in one transaction."""
    async with _session() as session:
        row = await session.get(DocumentIndexTable, file_hash)
        if row is None:
            raise PipelineError("STR-E002", context={"entity": "DocumentIndexRow", "key": file_hash})
        if is_final and row.doc_id_slug is not None:
            await session.execute(
                update(DocumentIndexTable)
                .where(
                    DocumentIndexTable.milestone_id == row.milestone_id,
                    DocumentIndexTable.doc_id_slug == row.doc_id_slug,
                    DocumentIndexTable.file_hash != file_hash,
                )
                .values(is_final=False)
            )
        row.is_final = is_final
        await session.commit()


async def get_documents_for_item(delivery_item_id: str) -> list[DocumentIndexRow]:
    async with _session() as session:
        result = await session.execute(
            select(DocumentIndexTable)
            .join(
                DocumentItemAssociationTable,
                DocumentItemAssociationTable.file_hash == DocumentIndexTable.file_hash,
            )
            .where(DocumentItemAssociationTable.delivery_item_id == delivery_item_id)
            .order_by(DocumentIndexTable.ingested_at)
        )
        return [_row_to_model(r) for r in result.scalars().all()]


async def list_documents_for_milestone(
    milestone_id: str, doc_type: DocType | None = None, is_final_only: bool = False
) -> list[DocumentIndexRow]:
    async with _session() as session:
        stmt = select(DocumentIndexTable).where(DocumentIndexTable.milestone_id == milestone_id)
        if doc_type is not None:
            stmt = stmt.where(DocumentIndexTable.doc_type == doc_type.value)
        if is_final_only:
            stmt = stmt.where(DocumentIndexTable.is_final.is_(True))
        result = await session.execute(stmt.order_by(DocumentIndexTable.ingested_at))
        return [_row_to_model(r) for r in result.scalars().all()]


# --- DocumentItemAssociation operations ------------------------------------------


async def add_document_item_association(assoc: DocumentItemAssociation) -> None:
    """Idempotent on (file_hash, delivery_item_id). Raises STR-E005 when the file
    already associates with a different milestone (FR-79 same-milestone invariant).
    Caller owns the NSD copy/move at local_nsd_path; this writes the index row only."""
    async with _session() as session:
        existing = await session.get(
            DocumentItemAssociationTable, (assoc.file_hash, assoc.delivery_item_id)
        )
        if existing is not None:
            return
        other = await session.execute(
            select(DocumentItemAssociationTable.milestone_id)
            .where(DocumentItemAssociationTable.file_hash == assoc.file_hash)
            .limit(1)
        )
        bound = other.scalar_one_or_none()
        if bound is not None and bound != assoc.milestone_id:
            raise PipelineError(
                "STR-E005",
                context={"file_hash": assoc.file_hash, "existing_milestone": bound},
            )
        session.add(
            DocumentItemAssociationTable(
                file_hash=assoc.file_hash,
                delivery_item_id=assoc.delivery_item_id,
                milestone_id=assoc.milestone_id,
                local_nsd_path=assoc.local_nsd_path,
                nsd_path_type=assoc.nsd_path_type.value,
                owner_corp_id=assoc.owner_corp_id,
                owner_corp_usa_email=assoc.owner_corp_usa_email,
                owner_corp_email=assoc.owner_corp_email,
                owner_name=assoc.owner_name,
                plm_id=assoc.plm_id,
                plm_attachment_id=assoc.plm_attachment_id,
                upload_timestamp=assoc.upload_timestamp,
                associated_at=assoc.associated_at,
                associated_by=assoc.associated_by,
            )
        )
        await session.commit()


async def delete_document_item_association(
    file_hash: str, delivery_item_id: str, *, delete_file: bool = True
) -> None:
    """Removes the M:M row; when delete_file=True also removes the per-item NSD copy.
    The DocumentIndexRow is never auto-deleted (orphans surface as STR-W005)."""
    async with _session() as session:
        row = await session.get(DocumentItemAssociationTable, (file_hash, delivery_item_id))
        if row is None:
            return
        nsd_path = row.local_nsd_path
        await session.delete(row)
        await session.commit()

    if delete_file:
        import asyncio

        local = NSDPath.from_relative(nsd_path).to_local()

        def _rm():
            try:
                local.unlink(missing_ok=True)
            except OSError:
                pass  # missing copy is not an error for index cleanup

        await asyncio.to_thread(_rm)


async def item_has_association(file_hash: str, delivery_item_id: str) -> bool:
    """Return True iff a DocumentItemAssociation already exists for this
    (file_hash, delivery_item_id) pair.

    Consumed by Fr52AttachmentRouter Step 0 per the cross-device-shared-file
    fix 2026-07-07: when a file_hash is already indexed and re-arrives (e.g.
    same regulatory certificate attached across multiple devices' work items),
    the router still routes to items on the current device -- but must filter
    out items that already carry an association for this file so they don't
    get double-counted in doc_count_received.
    """
    async with _session() as session:
        row = await session.get(
            DocumentItemAssociationTable, (file_hash, delivery_item_id)
        )
        return row is not None


async def get_local_nsd_path_for_file_hash(file_hash: str) -> str | None:
    """Return any existing local_nsd_path recorded for this file_hash.

    Consumed by inbound_attachment._persist_routed_attachment per the
    cross-device-shared-file fix 2026-07-07: when is_duplicate=True and new
    items need associations, the new rows should point at the ORIGINAL stored
    location (bytes were written on first arrival, not re-written on the
    duplicate). Returns None when no association exists yet (caller falls
    back to normal _resolve_nsd_path).
    """
    async with _session() as session:
        result = await session.execute(
            select(DocumentItemAssociationTable.local_nsd_path)
            .where(DocumentItemAssociationTable.file_hash == file_hash)
            .limit(1)
        )
        return result.scalar_one_or_none()


async def list_associations_for_file(file_hash: str) -> list[DocumentItemAssociation]:
    async with _session() as session:
        result = await session.execute(
            select(DocumentItemAssociationTable)
            .where(DocumentItemAssociationTable.file_hash == file_hash)
            .order_by(DocumentItemAssociationTable.delivery_item_id)
        )
        return [_assoc_to_model(r) for r in result.scalars().all()]


async def list_associations_for_item(delivery_item_id: str) -> list[DocumentItemAssociation]:
    async with _session() as session:
        result = await session.execute(
            select(DocumentItemAssociationTable)
            .where(DocumentItemAssociationTable.delivery_item_id == delivery_item_id)
            .order_by(DocumentItemAssociationTable.file_hash)
        )
        return [_assoc_to_model(r) for r in result.scalars().all()]


async def list_documents_for_item_display(
    delivery_item_id: str,
) -> list[tuple[str, str, "datetime"]]:
    """Dashboard Ph-1 helper per architect lock 2026-07-01.

    Returns per-item classified documents as a list of
    (original_filename, doc_type, ingested_at) tuples ordered by
    ingested_at DESC (newest first).

    Cheap projection -- no DocumentIndexRow / Association object hydration --
    since the dashboard only renders these three columns in Ph-1.
    Non-classified (staged / default_workitem / unrouted) rows are excluded
    because Ph-1 dashboard shows what's ACTUALLY delivered, not what's in
    triage.
    """
    async with _session() as session:
        stmt = (
            select(
                DocumentIndexTable.original_filename,
                DocumentIndexTable.doc_type,
                DocumentIndexTable.ingested_at,
            )
            .join(
                DocumentItemAssociationTable,
                DocumentItemAssociationTable.file_hash == DocumentIndexTable.file_hash,
            )
            .where(
                DocumentItemAssociationTable.delivery_item_id == delivery_item_id,
                DocumentItemAssociationTable.nsd_path_type == NSDPathType.CLASSIFIED.value,
            )
            .order_by(DocumentIndexTable.ingested_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.all())


async def list_classified_associations_for_item(
    delivery_item_id: str,
) -> list[DocumentItemAssociation]:
    """Filter of list_associations_for_item returning only nsd_path_type=CLASSIFIED
    rows -- the submit-to-carrier upload scope per architect 2026-06-30.

    Ordered by file_hash for deterministic sequencing across task runs.
    Non-classified rows (staged_*, default_workitem, unrouted) are excluded --
    those shouldn't reach a carrier."""
    async with _session() as session:
        result = await session.execute(
            select(DocumentItemAssociationTable)
            .where(
                DocumentItemAssociationTable.delivery_item_id == delivery_item_id,
                DocumentItemAssociationTable.nsd_path_type == NSDPathType.CLASSIFIED.value,
            )
            .order_by(DocumentItemAssociationTable.file_hash)
        )
        return [_assoc_to_model(r) for r in result.scalars().all()]


async def fan_out_plm_associations(file_hash: str) -> list[PLMFanOutTarget]:
    """DISTINCT (owner_corp_id, plm_id) pairs across the file's associations — one PLM
    upload per pair per FR-79. plm_id=None targets are included (STR-W006 signal).
    Grouping key changed 2026-06-21 from owner_email to owner_corp_id per FR-5 +
    [D-035] (PLM-issue-per-(device, milestone, owner_corp_id) tuple architect lock)."""
    assocs = await list_associations_for_file(file_hash)
    # Group by (owner_corp_id, plm_id); preserve representative email/name fields
    # from the first-seen association in each group for informational pass-through.
    grouped: dict[tuple[str, str | None], dict] = {}
    for assoc in assocs:
        key = (assoc.owner_corp_id, assoc.plm_id)
        entry = grouped.setdefault(key, {
            "owner_corp_id": assoc.owner_corp_id,
            "owner_corp_usa_email": assoc.owner_corp_usa_email,
            "owner_corp_email": assoc.owner_corp_email,
            "owner_name": assoc.owner_name,
            "plm_id": assoc.plm_id,
            "item_count": 0,
        })
        entry["item_count"] += 1
    return [
        PLMFanOutTarget(**entry)
        for (_corp_id, _plm), entry in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
    ]


async def update_association_plm_attachment(
    file_hash: str, delivery_item_id: str, plm_attachment_id: str, upload_timestamp: datetime
) -> None:
    """Replicates the upload result across all rows sharing the target row's
    (owner_corp_id, plm_id) within the milestone (FR-79 case (a)) in one transaction.
    Grouping key changed 2026-06-21 from owner_email to owner_corp_id per FR-5 +
    [D-035] architect lock."""
    async with _session() as session:
        target = await session.get(DocumentItemAssociationTable, (file_hash, delivery_item_id))
        if target is None:
            raise PipelineError(
                "STR-E002",
                context={"entity": "DocumentItemAssociation", "key": f"{file_hash}/{delivery_item_id}"},
            )
        await session.execute(
            update(DocumentItemAssociationTable)
            .where(
                DocumentItemAssociationTable.file_hash == file_hash,
                DocumentItemAssociationTable.milestone_id == target.milestone_id,
                DocumentItemAssociationTable.owner_corp_id == target.owner_corp_id,
                DocumentItemAssociationTable.plm_id == target.plm_id,
            )
            .values(plm_attachment_id=plm_attachment_id, upload_timestamp=upload_timestamp)
        )
        await session.commit()


async def reassign_document_to_workitem(
    file_hash: str, source_delivery_item_id: str, target_delivery_item_id: str, pm_id: str,
    *, target_tg_name: str | None,
    target_owner_corp_id: str,
    target_owner_corp_usa_email: str | None = None,
    target_owner_corp_email: str | None = None,
    target_owner_name: str | None = None,
    target_plm_id: str | None = None,
) -> None:
    """FR-83 TPM-manual reassignment. Adds the target association (classified path for
    the target item), moves the NSD file, removes the source association, updates
    routing_resolution + inferred_tg_name, and writes the audit row — transactionally
    at the index layer; the NSD move is write-before-delete to avoid data loss.

    Target-item attributes (tg_name / 4-field owner identity / plm_id) are explicit
    parameters — the CALLER resolves them from SharePoint before invoking (architect
    decision 2026-06-11: storage holds no DeliveryItem mirror; entity resolution
    belongs to workflow_engine task bodies via sharepoint_integration).
    Owner identity is 4-field per FR-88 cascade 2026-06-21; target_owner_corp_id
    is required (PLM grouping key per FR-5 + [D-035])."""
    from core.src.storage import audit_ops  # local import — avoids cycle at module load

    async with _session() as session:
        source = await session.get(
            DocumentItemAssociationTable, (file_hash, source_delivery_item_id)
        )
        if source is None:
            raise PipelineError(
                "STR-E002",
                context={"entity": "DocumentItemAssociation", "key": f"{file_hash}/{source_delivery_item_id}"},
            )
        doc = await session.get(DocumentIndexTable, file_hash)

        old_tg = doc.inferred_tg_name if doc else None
        source_unc = source.local_nsd_path

        # Carrier/device/milestone slugs come from the source path's internal-tree
        # segments: ("internal", <carrier>, <device>, <milestone>, ...).
        src_segments = NSDPath.from_relative(source_unc).segments
        if len(src_segments) < 4 or src_segments[0] != "internal":
            raise PipelineError(
                "STR-E004",
                context={"path": source_unc, "reason": "source association path not in internal tree"},
            )
        customer_id, device_id, milestone_name = src_segments[1], src_segments[2], src_segments[3]

        # Target NSD path: classified path for the target item using the doc's identity.
        # When doc_id_slug/rev are unresolved (staged/unrouted source), the file lands on
        # the target's staged_classification path pending FR-87 resolution.
        if doc is not None and doc.doc_id_slug is not None and doc.rev_number is not None:
            target_path = NSDPath.internal_classified(
                customer_id, device_id, milestone_name, target_tg_name or "_unknown_tg",
                target_delivery_item_id, doc.doc_type, doc.doc_id_slug, doc.rev_number,
            )
            target_type = NSDPathType.CLASSIFIED
        else:
            target_path = NSDPath.internal_staged_classification(
                customer_id, device_id, milestone_name,
                target_tg_name or "_unknown_tg", target_delivery_item_id,
                doc.original_filename if doc else file_hash,
            )
            target_type = NSDPathType.STAGED_NOT_CLASSIFIED

        session.add(
            DocumentItemAssociationTable(
                file_hash=file_hash,
                delivery_item_id=target_delivery_item_id,
                milestone_id=source.milestone_id,
                local_nsd_path=target_path.to_relative(),
                nsd_path_type=target_type.value,
                owner_corp_id=target_owner_corp_id,
                owner_corp_usa_email=target_owner_corp_usa_email,
                owner_corp_email=target_owner_corp_email,
                owner_name=target_owner_name,
                plm_id=target_plm_id,
                associated_at=datetime.now(timezone.utc),
                associated_by=pm_id,
            )
        )
        await session.delete(source)
        if doc is not None:
            doc.routing_resolution = RoutingResolution.TPM_REASSIGNED.value
            doc.inferred_tg_name = target_tg_name
        await session.commit()

    # NSD move: write target copy before removing source (no-loss ordering).
    import asyncio

    src_local = NSDPath.from_relative(source_unc).to_local()
    dst_local = target_path.to_local()

    def _move():
        if src_local.is_file():
            dst_local.parent.mkdir(parents=True, exist_ok=True)
            dst_local.write_bytes(src_local.read_bytes())
            src_local.unlink()

    await asyncio.to_thread(_move)

    await audit_ops.log_communication(
        CommunicationLogRow(
            log_id=uuid.uuid4().hex,
            channel=Channel.SHAREPOINT,
            direction=Direction.INBOUND,
            timestamp=datetime.now(timezone.utc),
            delivery_item_id=target_delivery_item_id,
            credential_id=pm_id,
            action_type="reassign_to_workitem",
            summary=f"inferred_tg_name: {old_tg} -> {target_tg_name}",
            attachments=[{"file_hash": file_hash, "source_item": source_delivery_item_id,
                          "target_item": target_delivery_item_id}],
        )
    )


# --- FR-87 TPM staged-document resolution (steps B + C) ----------------------------


def _internal_segments(relative_path: str) -> tuple[str, str, str, str, str]:
    """Extract (carrier, device, milestone, tg, item) from an internal-tree staged path.
    Staged paths share the prefix internal/<carrier>/<device>/<milestone>/<tg>/<item>/..."""
    segs = NSDPath.from_relative(relative_path).segments
    if len(segs) < 6 or segs[0] != "internal":
        raise PipelineError(
            "STR-E004", context={"path": relative_path, "reason": "not a staged internal-tree path"}
        )
    return segs[1], segs[2], segs[3], segs[4], segs[5]


async def _move_nsd_file(source_rel: str, target_rel: str) -> None:
    """Copy source→target then delete source (NSD-move-precedes-DB-write per FR-87 spec:
    if the later DB write fails, the file is at target while the row still reads STAGED_*,
    which the next reconcile/TPM-retry detects — readers never see a CLASSIFIED row whose
    file is missing)."""
    import asyncio

    src_local = NSDPath.from_relative(source_rel).to_local()
    dst_local = NSDPath.from_relative(target_rel).to_local()

    def _move():
        if src_local.is_file():
            dst_local.parent.mkdir(parents=True, exist_ok=True)
            dst_local.write_bytes(src_local.read_bytes())
            src_local.unlink()

    await asyncio.to_thread(_move)


async def tpm_resolve_doc_type(
    file_hash: str,
    delivery_item_id: str,
    new_doc_type: DocType,
    *,
    doc_id_slug: str | None = None,
    rev_number: int | None = None,
    pm_id: str,
) -> None:
    """FR-87 step (B) — TPM picks doc_type for a file at `staged_not_classification`.

    Caller (workflow_engine task body, after `email_service.sp_alert_parser` decodes the
    SP-alert email) owns all upstream resolution per the Option A / caller-resolves
    discipline: it re-runs `[D-039]` via the `llm` module (storage is NOT the [D-039]
    runner) and passes resolved values:
      - [D-039] PASSED   → pass doc_id_slug + rev_number (both non-NULL); file moves
        staged_not_classification → classified; nsd_path_type → CLASSIFIED.
      - [D-039] AMBIGUOUS→ omit both (both NULL); file moves staged_not_classification →
        staged_not_revision; nsd_path_type → STAGED_NOT_REVISION; awaits step (C).

    Both doc_id_slug + rev_number together or neither — asymmetric None raises STR-E010 at
    the boundary. State mismatch (file not at staged_not_classification) raises STR-E009.
    Idempotent on (file_hash, delivery_item_id, target_state): re-call at target state is a
    no-op warning STR-W008. NSD move precedes DB write (crash-safety, see _move_nsd_file).
    Does NOT run [D-039], does NOT fire FR-77 (caller orchestrates per Non-goals).
    """
    from core.src.storage import audit_ops  # local import — avoids cycle at module load

    if (doc_id_slug is None) != (rev_number is None):
        raise PipelineError("STR-E010", context={})

    resolved = doc_id_slug is not None
    target_type = NSDPathType.CLASSIFIED if resolved else NSDPathType.STAGED_NOT_REVISION

    async with _session() as session:
        assoc = await session.get(DocumentItemAssociationTable, (file_hash, delivery_item_id))
        if assoc is None:
            raise PipelineError(
                "STR-E002",
                context={"entity": "DocumentItemAssociation", "key": f"{file_hash}/{delivery_item_id}"},
            )
        if assoc.nsd_path_type == target_type.value:
            await audit_W008(file_hash, delivery_item_id, "tpm_resolve_doc_type")
            return
        if assoc.nsd_path_type != NSDPathType.STAGED_NOT_CLASSIFIED.value:
            raise PipelineError(
                "STR-E009",
                context={"file_hash": file_hash, "delivery_item_id": delivery_item_id,
                         "expected": NSDPathType.STAGED_NOT_CLASSIFIED.value, "actual": assoc.nsd_path_type},
            )
        doc = await session.get(DocumentIndexTable, file_hash)
        carrier, device, milestone, tg, item = _internal_segments(assoc.local_nsd_path)
        original = doc.original_filename if doc else file_hash
        if resolved:
            # RECLASS-BUGFIX (2026-08-24): pass original_filename so the target
            # path ends with .../revN/<filename> (a file inside the revN dir),
            # NOT .../revN (a file named literally 'revN' at the doc_id_slug
            # level). NSDPath.internal_classified defaults original_filename=''
            # to the directory-only form for relocation helpers; TPM reclassify
            # is a real file move -> must supply the leaf.
            target = NSDPath.internal_classified(
                carrier, device, milestone, tg, item,
                new_doc_type.value, doc_id_slug, rev_number, original,
            )
        else:
            target = NSDPath.internal_staged_revision(
                carrier, device, milestone, tg, item, new_doc_type.value, original,
            )
        source_rel = assoc.local_nsd_path
        target_rel = target.to_relative()

    await _move_nsd_file(source_rel, target_rel)

    async with _session() as session:
        doc = await session.get(DocumentIndexTable, file_hash)
        if doc is not None:
            doc.doc_type = new_doc_type.value
            if resolved:
                doc.doc_id_slug = doc_id_slug
                doc.rev_number = rev_number
        assoc = await session.get(DocumentItemAssociationTable, (file_hash, delivery_item_id))
        if assoc is not None:
            assoc.local_nsd_path = target_rel
            assoc.nsd_path_type = target_type.value
        await session.commit()

    await audit_ops.log_communication(
        CommunicationLogRow(
            log_id=uuid.uuid4().hex, channel=Channel.SHAREPOINT, direction=Direction.INBOUND,
            timestamp=datetime.now(timezone.utc), delivery_item_id=delivery_item_id,
            credential_id=pm_id, action_type="tpm_resolve_doc_type",
            summary=f"doc_type={new_doc_type.value}; {'classified' if resolved else 'staged_not_revision'}",
            attachments=[{"file_hash": file_hash}],
        )
    )


async def tpm_resolve_revision(
    file_hash: str,
    delivery_item_id: str,
    resolution: "RevisionResolution",
    *,
    pm_id: str,
) -> None:
    """FR-87 step (C) — TPM picks revision resolution for a file at `staged_not_revision`.

    Caller passes `RevisionResolution.new()` (storage assigns doc_id_slug from
    original_filename per [D-039] Step 0 + rev_number=1) or
    `RevisionResolution.revision_of(doc_id_slug=...)` (storage sets that slug + computes
    rev_number = MAX(family) + 1 atomically). File moves staged_not_revision → classified;
    nsd_path_type → CLASSIFIED. State mismatch raises STR-E009; idempotent re-call (already
    CLASSIFIED) warns STR-W008. NSD move precedes DB write. Does NOT fire FR-77.
    """
    from core.src.storage import audit_ops  # local import — avoids cycle at module load
    from core.src.template_schema import make_slug

    async with _session() as session:
        assoc = await session.get(DocumentItemAssociationTable, (file_hash, delivery_item_id))
        if assoc is None:
            raise PipelineError(
                "STR-E002",
                context={"entity": "DocumentItemAssociation", "key": f"{file_hash}/{delivery_item_id}"},
            )
        if assoc.nsd_path_type == NSDPathType.CLASSIFIED.value:
            await audit_W008(file_hash, delivery_item_id, "tpm_resolve_revision")
            return
        if assoc.nsd_path_type != NSDPathType.STAGED_NOT_REVISION.value:
            raise PipelineError(
                "STR-E009",
                context={"file_hash": file_hash, "delivery_item_id": delivery_item_id,
                         "expected": NSDPathType.STAGED_NOT_REVISION.value, "actual": assoc.nsd_path_type},
            )
        doc = await session.get(DocumentIndexTable, file_hash)
        if doc is None:
            raise PipelineError("STR-E002", context={"entity": "DocumentIndexRow", "key": file_hash})

        if resolution.kind == "new":
            new_slug = make_slug(doc.original_filename)
            new_rev = 1
        else:
            new_slug = resolution.revised_doc_id_slug
            fam = await session.execute(
                select(DocumentIndexTable.rev_number).where(
                    DocumentIndexTable.milestone_id == doc.milestone_id,
                    DocumentIndexTable.doc_id_slug == new_slug,
                )
            )
            existing = [r for (r,) in fam.all() if r is not None]
            new_rev = (max(existing) + 1) if existing else 1

        carrier, device, milestone, tg, item = _internal_segments(assoc.local_nsd_path)
        target = NSDPath.internal_classified(
            carrier, device, milestone, tg, item, doc.doc_type, new_slug, new_rev,
        )
        source_rel = assoc.local_nsd_path
        target_rel = target.to_relative()
        summary = (f"NEW: doc_id_slug={new_slug}" if resolution.kind == "new"
                   else f"REVISION_OF: {new_slug} -> rev={new_rev}")

    await _move_nsd_file(source_rel, target_rel)

    async with _session() as session:
        doc = await session.get(DocumentIndexTable, file_hash)
        if doc is not None:
            doc.doc_id_slug = new_slug
            doc.rev_number = new_rev
        assoc = await session.get(DocumentItemAssociationTable, (file_hash, delivery_item_id))
        if assoc is not None:
            assoc.local_nsd_path = target_rel
            assoc.nsd_path_type = NSDPathType.CLASSIFIED.value
        await session.commit()

    await audit_ops.log_communication(
        CommunicationLogRow(
            log_id=uuid.uuid4().hex, channel=Channel.SHAREPOINT, direction=Direction.INBOUND,
            timestamp=datetime.now(timezone.utc), delivery_item_id=delivery_item_id,
            credential_id=pm_id, action_type="tpm_resolve_revision",
            summary=summary, attachments=[{"file_hash": file_hash}],
        )
    )


async def audit_W008(file_hash: str, delivery_item_id: str, action: str) -> None:
    """Emit the STR-W008 idempotent-re-call warning to CommunicationLog (informational)."""
    from core.src.storage import audit_ops  # local import — avoids cycle at module load

    await audit_ops.log_communication(
        CommunicationLogRow(
            log_id=uuid.uuid4().hex, channel=Channel.SHAREPOINT, direction=Direction.INBOUND,
            timestamp=datetime.now(timezone.utc), delivery_item_id=delivery_item_id,
            action_type=action, summary="STR-W008 idempotent re-call — already at target state",
            attachments=[{"file_hash": file_hash}],
        )
    )


# --- Download tokens (FR-61 / NFR-16) ----------------------------------------------

_TOKEN_TTL_DEFAULT = 300


def _token_secret() -> bytes:
    return os.environ.get("HILDA_DOWNLOAD_TOKEN_SECRET", "hilda-dev-secret").encode()


async def make_download_token(
    file_hash: str, delivery_item_id: str, ttl_seconds: int = _TOKEN_TTL_DEFAULT
) -> str:
    """Short-lived HMAC token bound to one (file, item) association; never persisted."""
    payload = json.dumps(
        {"h": file_hash, "i": delivery_item_id, "exp": int(time.time()) + ttl_seconds},
        separators=(",", ":"),
    ).encode()
    sig = hmac.new(_token_secret(), payload, hashlib.sha256).digest()[:16]
    return base64.urlsafe_b64encode(payload + sig).decode().rstrip("=")


async def resolve_download_token(token: str) -> tuple[str, str, NSDPath]:
    """Verify signature + TTL; resolve to the per-item NSD path. STR-E007 on failure."""
    try:
        raw = base64.urlsafe_b64decode(token + "=" * (-len(token) % 4))
        payload, sig = raw[:-16], raw[-16:]
        if not hmac.compare_digest(sig, hmac.new(_token_secret(), payload, hashlib.sha256).digest()[:16]):
            raise ValueError("bad signature")
        data = json.loads(payload)
        if data["exp"] < time.time():
            raise ValueError("expired")
        file_hash, item_id = data["h"], data["i"]
    except (ValueError, KeyError, json.JSONDecodeError) as exc:
        raise PipelineError("STR-E007", cause=exc)

    async with _session() as session:
        assoc = await session.get(DocumentItemAssociationTable, (file_hash, item_id))
        if assoc is None:
            raise PipelineError("STR-E007")
        return file_hash, item_id, NSDPath.from_relative(assoc.local_nsd_path)
