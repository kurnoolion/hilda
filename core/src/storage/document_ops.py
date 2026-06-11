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
    "list_associations_for_file",
    "list_associations_for_item",
    "list_documents_for_milestone",
    "list_revisions",
    "make_download_token",
    "reassign_document_to_workitem",
    "resolve_download_token",
    "set_is_final",
    "update_association_plm_attachment",
    "update_review_findings",
]


_session = session_scope


def _row_to_model(row: DocumentIndexTable) -> DocumentIndexRow:
    return DocumentIndexRow(
        file_hash=row.file_hash,
        milestone_id=row.milestone_id,
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
        owner_email=row.owner_email,
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
                owner_email=assoc.owner_email,
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


async def fan_out_plm_associations(file_hash: str) -> list[PLMFanOutTarget]:
    """DISTINCT (owner_email, plm_id) pairs across the file's associations — one PLM
    upload per pair per FR-79. plm_id=None targets are included (STR-W006 signal)."""
    assocs = await list_associations_for_file(file_hash)
    grouped: dict[tuple[str, str | None], int] = {}
    for assoc in assocs:
        key = (assoc.owner_email, assoc.plm_id)
        grouped[key] = grouped.get(key, 0) + 1
    return [
        PLMFanOutTarget(owner_email=owner, plm_id=plm, item_count=count)
        for (owner, plm), count in sorted(grouped.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
    ]


async def update_association_plm_attachment(
    file_hash: str, delivery_item_id: str, plm_attachment_id: str, upload_timestamp: datetime
) -> None:
    """Replicates the upload result across all rows sharing the target row's
    (owner_email, plm_id) within the milestone (FR-79 case (a)) in one transaction."""
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
                DocumentItemAssociationTable.owner_email == target.owner_email,
                DocumentItemAssociationTable.plm_id == target.plm_id,
            )
            .values(plm_attachment_id=plm_attachment_id, upload_timestamp=upload_timestamp)
        )
        await session.commit()


async def reassign_document_to_workitem(
    file_hash: str, source_delivery_item_id: str, target_delivery_item_id: str, pm_id: str,
    *, target_tg_name: str | None, target_owner_email: str, target_plm_id: str | None = None,
) -> None:
    """FR-83 TPM-manual reassignment. Adds the target association (classified path for
    the target item), moves the NSD file, removes the source association, updates
    routing_resolution + inferred_tg_name, and writes the audit row — transactionally
    at the index layer; the NSD move is write-before-delete to avoid data loss.

    Target-item attributes (tg_name / owner_email / plm_id) are explicit parameters —
    the CALLER resolves them from SharePoint before invoking (architect decision
    2026-06-11: storage holds no DeliveryItem mirror; entity resolution belongs to
    workflow_engine task bodies via sharepoint_integration)."""
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
        carrier_slug, device_slug, milestone_slug = src_segments[1], src_segments[2], src_segments[3]

        # Target NSD path: classified path for the target item using the doc's identity.
        # When doc_id_slug/rev are unresolved (staged/unrouted source), the file lands on
        # the target's staged_classification path pending FR-87 resolution.
        if doc is not None and doc.doc_id_slug is not None and doc.rev_number is not None:
            target_path = NSDPath.internal_classified(
                carrier_slug, device_slug, milestone_slug, target_tg_name or "_unknown_tg",
                target_delivery_item_id, doc.doc_type, doc.doc_id_slug, doc.rev_number,
            )
            target_type = NSDPathType.CLASSIFIED
        else:
            target_path = NSDPath.internal_staged_classification(
                carrier_slug, device_slug, milestone_slug,
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
                owner_email=target_owner_email,
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
