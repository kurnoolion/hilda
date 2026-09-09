"""UPLOAD-MANIFEST-1 (2026-09-06): what a Submit-to-Carrier would actually do.

A TPM clicks Submit in the SP UI and finds out where documents landed
afterwards. This builds the same answer beforehand, for a whole milestone:
one row per file, with its source path, its carrier destination, and -- for
anything that will NOT be delivered -- the reason.

Fidelity is the entire requirement. A preview that computes a different
answer from the uploader is worse than no preview, because it manufactures
confidence. So the INCLUDED rows come from exactly the functions
submit_to_carrier calls (`list_upload_files_for_item` +
`list_migrated_upload_files_for_item`), and their destinations from
`storage.upload_plan`. Nothing here re-derives a path.

Two levels of exclusion exist, and they are reported differently because they
are discovered differently:

  file-level   waiver / archive container / superseded revision / staged.
               `list_upload_files_for_item` drops these silently, so they are
               recovered from `list_files_in_tg`, which already computes a
               reason per document (UPLOAD-DEST-1).

  item-level   no_customer_upload, no target_folder, or a delivery_state that
               is not ReadyForSubmission. The first two make every file on the
               item undeliverable; the third is a "not yet" and is reported as
               a per-item warning rather than an exclusion, because it is the
               normal state before a submission and blanking the manifest then
               would defeat the point.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from core.src.storage.upload_plan import carrier_subdir, effective_target_dir

__all__ = [
    "ManifestItem",
    "ManifestRow",
    "MilestoneUploadManifest",
    "build_milestone_manifest",
]

_log = logging.getLogger(__name__)

# submit_to_carrier only uploads items in exactly this state.
READY_STATE = "ReadyForSubmission"


@dataclass(frozen=True)
class ManifestRow:
    """One file. `carrier_destination` is set iff `excluded_reason` is empty."""

    filename: str
    source_path: str                # NSD-share-relative (view or internal tree)
    doc_type: str
    carrier_destination: str = ""
    excluded_reason: str = ""
    # "DRR #50" when this file is contributed by another milestone's work-item
    # per milestone_item_mapping; empty for the item's own documents. Kept
    # visible because a borrowed document appearing under a P1 item is
    # otherwise indistinguishable from one that arrived there.
    migrated_from: str = ""


@dataclass(frozen=True)
class ManifestItem:
    """One work-item and the files it would submit."""

    item_no: int
    item_id: str
    tg_name: str
    item_name: str
    delivery_state: str
    target_folder: str
    no_customer_upload: bool
    rows: list[ManifestRow] = field(default_factory=list)

    @property
    def state_ready(self) -> bool:
        return self.delivery_state == READY_STATE

    @property
    def deliverable(self) -> bool:
        """Item-level gate, independent of state: could this item EVER upload?"""
        return bool(self.target_folder) and not self.no_customer_upload

    @property
    def included_count(self) -> int:
        return sum(1 for r in self.rows if not r.excluded_reason)

    @property
    def excluded_count(self) -> int:
        return sum(1 for r in self.rows if r.excluded_reason)


@dataclass(frozen=True)
class MilestoneUploadManifest:
    customer_id: str
    device_id: str
    milestone_id: str
    items: list[ManifestItem] = field(default_factory=list)

    @property
    def total_included(self) -> int:
        return sum(i.included_count for i in self.items)

    @property
    def total_excluded(self) -> int:
        return sum(i.excluded_count for i in self.items)

    @property
    def items_not_ready(self) -> list[ManifestItem]:
        """Deliverable items holding files that a Submit right now would skip
        purely because of state. The single most useful line in the preview:
        'these 12 files are correct but will not go yet.'"""
        return [
            i for i in self.items
            if i.deliverable and not i.state_ready and i.included_count
        ]


async def build_milestone_manifest(
    customer_id: str, device_id: str, milestone_id: str,
) -> MilestoneUploadManifest:
    """Resolve every file a Submit-to-Carrier would send for this milestone.

    Never raises on a per-item failure: one bad item logs a WARNING and yields
    an empty row list rather than losing the whole preview.
    """
    from sqlalchemy import select

    from core.src.storage.db import DeliveryItemTable, session_scope
    from core.src.storage.document_view_ops import (
        list_files_in_tg,
        list_migrated_upload_files_for_item,
        list_upload_files_for_item,
    )

    async with session_scope() as session:
        item_rows = (await session.execute(
            select(DeliveryItemTable).where(
                DeliveryItemTable.customer_id == customer_id,
                DeliveryItemTable.device_id == device_id,
                DeliveryItemTable.milestone_id == milestone_id,
            ).order_by(DeliveryItemTable.sort_order, DeliveryItemTable.item_no)
        )).scalars().all()

    # File-level exclusions, recovered per TG. list_upload_files_for_item drops
    # them silently, and they are exactly what a TPM needs to see.
    excluded_by_path: dict[str, tuple[str, str, str]] = {}   # path -> (name, doc_type, reason)
    for tg in sorted({(r.tg_name or "").strip() for r in item_rows} - {""}):
        try:
            for f in await list_files_in_tg(
                customer_id=customer_id, device_id=device_id,
                milestone_id=milestone_id, tg_name=tg,
            ):
                if f.upload_excluded_reason:
                    excluded_by_path[f.view_relative_path] = (
                        f.filename, f.doc_type, f.upload_excluded_reason,
                    )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "UPLOAD_MANIFEST: TG listing failed tg=%s scope=%s/%s/%s: %s: %s",
                tg, customer_id, device_id, milestone_id,
                type(exc).__name__, str(exc)[:120],
            )

    items: list[ManifestItem] = []
    claimed: set[str] = set()
    for row in item_rows:
        target_folder = (row.target_folder or "").strip()
        no_upload = bool(row.no_customer_upload)
        rows: list[ManifestRow] = []
        try:
            own = await list_upload_files_for_item(row.item_id)
            migrated = await list_migrated_upload_files_for_item(row.item_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "UPLOAD_MANIFEST: resolve failed item=%s: %s: %s",
                row.item_id, type(exc).__name__, str(exc)[:120],
            )
            own, migrated = [], []

        for f in list(own) + list(migrated):
            claimed.add(f.relative_path)
            src = ""
            if f.migrated_from_milestone:
                src = f"{f.migrated_from_milestone} #{f.migrated_from_item_no}"
            if no_upload:
                rows.append(ManifestRow(
                    filename=f.filename, source_path=f.relative_path,
                    doc_type=f.doc_type, migrated_from=src,
                    excluded_reason="work item is marked no_customer_upload",
                ))
                continue
            if not target_folder:
                rows.append(ManifestRow(
                    filename=f.filename, source_path=f.relative_path,
                    doc_type=f.doc_type, migrated_from=src,
                    excluded_reason="work item has no target_folder",
                ))
                continue
            subdir = carrier_subdir(
                relative_path=f.relative_path,
                is_view=f.is_view,
                from_zip=f.from_zip,
            )
            # UPLOAD-FOLDER-OVERRIDE-1: same precedence as submit_to_carrier --
            # a TPM-chosen folder replaces the item's, the subdir still rides
            # underneath. The preview must show the redirect, or it stops
            # matching what a Submit would do.
            override = (f.target_folder_override or "").strip()
            directory = effective_target_dir(override or target_folder, subdir)
            rows.append(ManifestRow(
                filename=f.filename,
                source_path=f.relative_path,
                doc_type=f.doc_type,
                migrated_from=src,
                carrier_destination=(
                    f"{directory}/{f.filename}" if directory else f.filename
                ),
            ))

        items.append(ManifestItem(
            item_no=int(row.item_no or 0),
            item_id=row.item_id,
            tg_name=(row.tg_name or "").strip(),
            item_name=(row.item_name or ""),
            delivery_state=(row.delivery_state or ""),
            target_folder=target_folder,
            no_customer_upload=no_upload,
            rows=rows,
        ))

    # Anything the uploader dropped that no item claimed, appended to its TG's
    # first item so it is visible somewhere rather than vanishing.
    unclaimed = {p: v for p, v in excluded_by_path.items() if p not in claimed}
    if unclaimed:
        by_tg: dict[str, ManifestItem] = {}
        for it in items:
            by_tg.setdefault(it.tg_name, it)
        for path, (name, doc_type, reason) in sorted(unclaimed.items()):
            tg = _tg_from_view_path(path)
            host = by_tg.get(tg) or (items[0] if items else None)
            if host is None:
                continue
            host.rows.append(ManifestRow(
                filename=name, source_path=path, doc_type=doc_type,
                excluded_reason=reason,
            ))

    manifest = MilestoneUploadManifest(
        customer_id=customer_id, device_id=device_id,
        milestone_id=milestone_id, items=items,
    )
    _log.warning(
        "UPLOAD_MANIFEST: scope=%s/%s/%s items=%d included=%d excluded=%d "
        "items_not_ready=%d",
        customer_id, device_id, milestone_id, len(items),
        manifest.total_included, manifest.total_excluded,
        len(manifest.items_not_ready),
    )
    return manifest


def _tg_from_view_path(view_relative_path: str) -> str:
    """`view/<customer>/<device>/<milestone>/<tg>/...` -> `<tg>`, else ''."""
    parts = view_relative_path.split("/")
    return parts[4] if len(parts) > 4 and parts[0] == "view" else ""
