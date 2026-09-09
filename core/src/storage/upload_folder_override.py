"""UPLOAD-FOLDER-OVERRIDE-1 (2026-09-06): TPM-chosen carrier folder.

A TPM cares WHERE a document lands on Google Drive, not which work item holds
it. This lets them redirect one document to any folder already defined by a
work item in the same technology group, without moving files, changing
associations, or touching delivery state.

Two invariants shape the whole module:

  family scope   The override is written to EVERY association whose document
                 shares the selected document's `doc_id_slug`. Keying it on a
                 single file_hash would mean a TPM redirects rev1, the owner
                 sends rev2, and rev2 silently uploads to the original folder.
                 The unit of TPM intent is the revision family, not the file.

  offered set    Only folders that already exist on a work item in the same TG
                 can be chosen. The TPM cannot invent a destination, so this
                 cannot paper over a template gap -- a folder that ought to
                 exist but does not still has to be added to template.yaml.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select

from core.src.storage.db import (
    DeliveryItemTable,
    DocumentIndexTable,
    DocumentItemAssociationTable,
    session_scope,
)

__all__ = [
    "FolderOption",
    "clear_upload_folder_override",
    "list_folder_options_for_tg",
    "set_upload_folder_override",
]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class FolderOption:
    """One selectable carrier folder, with the work items that declare it.

    `item_nos` is plural because a folder is frequently owned by several items
    -- P1 #14 and #20 both point at
    `RF Parametric Data/Documentation/FCC Package`. That plurality is itself
    the argument for overriding the folder rather than re-assigning the item:
    picking this option says nothing about which item was meant.
    """

    target_folder: str
    item_nos: tuple[int, ...]

    @property
    def label(self) -> str:
        """`<folder>  (#10, #14)` -- item numbers only. Titles will not fit the
        column, and the folder is what the TPM is actually choosing."""
        nos = ", ".join("#" + str(n) for n in self.item_nos)
        return self.target_folder + "  (" + nos + ")" if nos else self.target_folder


async def list_folder_options_for_tg(
    customer_id: str, device_id: str, milestone_id: str, tg_name: str,
) -> list[FolderOption]:
    """Distinct non-empty target_folders across this TG's work items.

    Scoped to the TG rather than the milestone per architect 2026-09-06: a
    document lives under exactly one TG folder in the view tree per [D-153],
    so offering another TG's folders would invite a cross-TG mis-delivery.
    Items marked no_customer_upload are excluded -- their folder is not a place
    documents go.
    """
    async with session_scope() as session:
        rows = (await session.execute(
            select(DeliveryItemTable.item_no, DeliveryItemTable.target_folder)
            .where(
                DeliveryItemTable.customer_id == customer_id,
                DeliveryItemTable.device_id == device_id,
                DeliveryItemTable.milestone_id == milestone_id,
                DeliveryItemTable.tg_name == tg_name,
                DeliveryItemTable.no_customer_upload.is_(False),
            )
            .order_by(DeliveryItemTable.item_no)
        )).all()
    by_folder: dict[str, list[int]] = {}
    for item_no, folder in rows:
        f = (folder or "").strip()
        if f:
            by_folder.setdefault(f, []).append(int(item_no or 0))
    return [
        FolderOption(target_folder=f, item_nos=tuple(nos))
        for f, nos in sorted(by_folder.items())
    ]


async def _family_hashes(session, file_hash: str) -> list[str]:
    """Every file_hash sharing this document's `doc_id_slug` + milestone.

    A document with no index row, or no slug (staged fill row), is its own
    single-member family -- matching how list_upload_files_for_item groups.
    """
    doc = await session.get(DocumentIndexTable, file_hash)
    if doc is None or not doc.doc_id_slug:
        return [file_hash]
    rows = (await session.execute(
        select(DocumentIndexTable.file_hash).where(
            DocumentIndexTable.doc_id_slug == doc.doc_id_slug,
            DocumentIndexTable.milestone_id == doc.milestone_id,
        )
    )).all()
    return [h for (h,) in rows] or [file_hash]


async def set_upload_folder_override(
    *,
    file_hash: str,
    delivery_item_id: str,
    target_folder: str,
    tpm_id: str,
) -> int:
    """Redirect a document, and its whole revision family, to `target_folder`.

    Returns the number of association rows updated. Zero means nothing matched
    -- the caller should surface that rather than report success.

    The caller validates `target_folder` against `list_folder_options_for_tg`;
    this function does not re-derive the TG.
    """
    folder = (target_folder or "").strip()
    if not folder:
        raise ValueError(
            "target_folder must be non-empty; use clear_upload_folder_override"
        )

    async with session_scope() as session:
        hashes = await _family_hashes(session, file_hash)
        rows = (await session.execute(
            select(DocumentItemAssociationTable).where(
                DocumentItemAssociationTable.file_hash.in_(hashes),
                DocumentItemAssociationTable.delivery_item_id == delivery_item_id,
            )
        )).scalars().all()
        for r in rows:
            r.upload_target_folder_override = folder
        await session.commit()
        updated = len(rows)

    await _audit(
        delivery_item_id=delivery_item_id, tpm_id=tpm_id,
        summary=(
            "upload_target_folder_override -> " + repr(folder)
            + " (" + str(updated) + " row(s), family of " + str(len(hashes)) + ")"
        ),
        file_hash=file_hash, folder=folder,
    )
    _log.warning(
        "UPLOAD_FOLDER_OVERRIDE: set item=%s file_hash=%s folder=%r "
        "family=%d rows=%d by=%s",
        delivery_item_id, file_hash[:12], folder, len(hashes), updated, tpm_id,
    )
    return updated


async def clear_upload_folder_override(
    *, file_hash: str, delivery_item_id: str, tpm_id: str,
) -> int:
    """Revert the family to its work item's own target_folder."""
    async with session_scope() as session:
        hashes = await _family_hashes(session, file_hash)
        rows = (await session.execute(
            select(DocumentItemAssociationTable).where(
                DocumentItemAssociationTable.file_hash.in_(hashes),
                DocumentItemAssociationTable.delivery_item_id == delivery_item_id,
            )
        )).scalars().all()
        for r in rows:
            r.upload_target_folder_override = None
        await session.commit()
        updated = len(rows)

    await _audit(
        delivery_item_id=delivery_item_id, tpm_id=tpm_id,
        summary="upload_target_folder_override cleared ("
                + str(updated) + " row(s))",
        file_hash=file_hash, folder="",
    )
    _log.warning(
        "UPLOAD_FOLDER_OVERRIDE: cleared item=%s file_hash=%s rows=%d by=%s",
        delivery_item_id, file_hash[:12], updated, tpm_id,
    )
    return updated


async def _audit(*, delivery_item_id: str, tpm_id: str, summary: str,
                 file_hash: str, folder: str) -> None:
    """Every override is audited: it changes what the carrier receives, and
    the change leaves no trace in the document itself."""
    from core.src.storage import audit_ops
    from core.src.storage.models import Channel, CommunicationLogRow, Direction
    try:
        await audit_ops.log_communication(CommunicationLogRow(
            log_id=uuid.uuid4().hex,
            channel=Channel.SHAREPOINT,
            direction=Direction.INBOUND,
            timestamp=datetime.now(timezone.utc),
            delivery_item_id=delivery_item_id,
            credential_id=tpm_id,
            action_type="set_upload_folder_override",
            summary=summary,
            attachments=[{"file_hash": file_hash, "target_folder": folder}],
        ))
    except Exception as exc:  # noqa: BLE001
        # The override is already committed; losing the audit row must not fail
        # the operation, but it should be loud.
        _log.warning(
            "UPLOAD_FOLDER_OVERRIDE: audit write failed item=%s: %s: %s",
            delivery_item_id, type(exc).__name__, str(exc)[:120],
        )
