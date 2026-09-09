"""HILDA-side documents view — storage helpers per D-150 Ph-1.

The view tree lives on NSD at `view/<customer_id>/<device_id>/<milestone_id>/<tg_name>/...`
(distinct from the FR-86 `internal/` tree, which is item-scoped). Every save
event archives the previous current file as a sibling `.v<N>` and records a
row in `document_version`. Landing page + browse UI + WOPI Host build on top.

Ph-1 scope (per architect 2026-07-22):
  * All docs received by tg_name land here (via attachment router post-Chunk-3)
  * Zip archives auto-extract preserving folder tree (Chunk 3)
  * Overwrite of an existing file = new version (Ph-1: keep history; no restore UI)
  * Default WI unrouted docs are EXCLUDED from this view entirely
  * HILDA-generated outbound docs are EXCLUDED (view is inbound-only)

Ph-2 (not implemented):
  * Version restore / diff UI
  * Concurrent-edit lock coordination (Ph-1 relies on OnlyOffice's own WOPI lock)
  * Route Closed-item late arrivals to Default WI instead of the Closed item
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath

from sqlalchemy import select, update

from core.src.diagnostics.error_codes import PipelineError
from core.src.storage.db import CommunicationLogTable, DocumentVersionTable, session_scope
from core.src.storage.models import DocumentVersionRow, NSDPathType
from core.src.storage.nsd import NSDPath, read_file, write_file
from core.src.template_schema import DocType

_log = logging.getLogger(__name__)

__all__ = [
    "DocumentEventRow",
    "TgFolderEntry",
    "TgFileEntry",
    "get_current_version",
    "get_version_by_num",
    "list_document_events",
    "list_files_in_tg",
    "list_tg_names_for_scope",
    "list_versions_for_file",
    "save_view_document",
]


_session = session_scope


# D-152 (2026-07-24): corp Exchange DLP/IRM wraps some in-transit attachments
# with NASCA. Wrapped bytes start with the ASCII marker `<## ` (0x3c 0x23 0x23
# 0x20). Empirically confirmed 2026-07-24: same file arriving via SCP is clean,
# same file arriving via corp email → OMADM_BOT mailbox is wrapped. Legacy
# .doc/.xls formats are wrapped by policy; modern OOXML .docx/.xlsx come clean.
# We sniff at save time so downstream UI can gate the Edit button off (OnlyOffice
# has no NASCA agent inside the container and cannot decrypt).
_NASCA_MAGIC = b"<## "


@dataclass(frozen=True)
class DocumentEventRow:
    """One audit event on a file in the view tree — surface to the History UI
    per D-150 Chunk 7. Sourced from CommunicationLog rows written by the
    dashboard's `_audit()` helper (external_message_id == view_relative_path).

    `details` holds the full serialized attribution+details dict from the audit
    row so the History template can render context like version_num on saves
    or protocol on POSTs.
    """

    timestamp: datetime
    action_type: str
    user_id: str | None
    details: dict


@dataclass(frozen=True)
class TgFolderEntry:
    """Directory-shaped entry returned by list_tg_names_for_scope for the landing
    page. `file_count` is a running count of files stored under that tg_name
    (all versions across all files) — useful for the landing page to show
    "reports (12 files)" etc. without a per-tg drill-down."""

    tg_name: str
    file_count: int
    last_saved_at: datetime | None


@dataclass(frozen=True)
class TgFileEntry:
    """File-shaped entry returned by list_files_in_tg for browse pages.
    `view_relative_path` is the current-version location; `filename` is
    the basename (from the trailing segment)."""

    view_relative_path: str
    filename: str
    size_bytes: int
    version_count: int
    last_saved_at: datetime
    last_saved_by: str
    # D-152: True when the current file is NASCA/IRM-wrapped (bytes start with
    # `<## `). Dashboard hides the Edit button and shows Download-only.
    is_drm_wrapped: bool = False
    # MERGE-1 (2026-07-28): True when the current version is authored by the
    # owner (`saved_by == "auto"`) AND at least one prior version was authored
    # by a TPM/human (`saved_by != "auto"`). Signals to TPM that a fresh owner
    # copy landed on top of edits they had made -- manual merge required.
    # False when: single version; all versions owner-authored (nothing edited);
    # or the latest version is TPM/human-authored (TPM has already merged /
    # re-edited on top of the owner copy, no outstanding merge).
    #
    # MERGE-2 (2026-08-30): the predicate above is unchanged, but its SCOPE
    # widened from one view path to the whole revision FAMILY
    # (delivery_item_id + doc_id_slug). An owner resending under a decorated
    # filename (`report_v2.xlsx`) lands on a DIFFERENT view path, so the
    # path-scoped rule saw two unrelated single-version files and never fired
    # -- the TPM's edit on `report.xlsx` was silently superseded. Now the
    # predicate reads the family's WINNING revision as `current` and the union
    # of `saved_by` across every version of every path in the family as
    # history. Per user rule 2026-08-30 the marker is rendered on the path the
    # TPM actually edited (they recognize their own work); the other revisions
    # sit alongside it for merging. Families with a single path behave exactly
    # as before, and paths with no resolvable family fall back to the original
    # path-scoped predicate.
    needs_merge: bool = False
    # MERGE-2 (2026-08-30): True for every NON-winning revision of a family --
    # i.e. this path is an older revision that upload will not select. The TG
    # view badges these and withholds the Edit link so a TPM cannot edit a
    # stale revision (`/browse/edit` enforces the same rule server-side).
    # Always False for single-path families and for family-less paths.
    is_superseded: bool = False
    # RECLASS-1 (2026-08-24): doc_type + file_hash + is_staged surfaced to the
    # TG-view template so TPM can spot Unresolved rows and click Reclassify.
    # `doc_type` values include "" / "unresolved" (classification miss) +
    # concrete types (TestReport, TechReport, Waiver, ...). RECLASS-BUGFIX-2
    # (2026-08-26): `is_staged` derives from `doc_type in ("", "unresolved")`
    # -- the classification source of truth on document_index. Earlier version
    # keyed off document_item_association.nsd_path_type=STAGED_NOT_CLASSIFIED
    # which is a filesystem-location concern; when the two got out of sync
    # (stale assoc, partial reclassify, archive-inner ingest edge cases) the
    # UI showed "<real doc_type> -- not classified" (contradictory) + the
    # Reclassify dropdown on already-classified docs. `file_hash` is the
    # sha256 already stored on document_version; used by the reclassify POST
    # handler as the primary key into tpm_resolve_doc_type.
    doc_type: str = ""
    file_hash: str = ""
    is_staged: bool = False
    # DOCTYPE-MISALIGN-UI-1 (2026-09-03): True when ANY association for this
    # document sits at nsd_path_type=STAGED_NOT_CLASSIFIED.
    #
    # Distinct from `is_staged`, which RECLASS-BUGFIX-2 redefined to mean
    # "doc_type is '' / unresolved". That redefinition collapsed two different
    # states into one flag and left the second one invisible:
    #
    #   unclassified  doc_type=""/unresolved   -> is_staged=True   (has UI)
    #   MISALIGNED    doc_type concrete, but not FR-86-aligned with the
    #                 routed item's item_type -> is_staged=FALSE  (had NO UI)
    #
    # The misaligned case is the dangerous one: the row renders a real
    # doc_type and looks complete, while `list_upload_files_for_item` filters
    # it out of the carrier submission entirely. Live example: a file matching
    # `.*volte.*` classified test_report, routed to an MNO-UX item whose
    # item_type is compliance_certification_release_notes, and vanished from
    # the upload set with nothing on screen to say so.
    #
    # Derived from the SAME predicate the uploader uses (nsd_path_type vs
    # CLASSIFIED) so the badge and the upload decision cannot drift apart --
    # which is the property RECLASS-BUGFIX-2 gave up. Kept as a SECOND flag
    # rather than redefining is_staged, so the contradictory
    # "<real doc_type> -- not classified" labels that bugfix removed stay gone.
    #
    # NOT set for the other two reasons a doc is legitimately skipped at
    # upload -- doc_type=waiver, and superseded revisions -- both of which are
    # intentional and already have their own indicators.
    is_staged_not_classified: bool = False
    # DRRP1-DEST-1 (2026-09-08): when this document's item maps forward to a
    # later milestone, a human-readable note naming where the destination came
    # from ("via DRR -> P1 #14"). Kept OUT of carrier_destination so that
    # field stays a clean path -- UPLOAD-BUNDLE-1 uses it verbatim as a zip
    # entry path, and UPLOAD-MANIFEST-1 as a collision key.
    migrated_to: str = ""
    # RECLASS-UI-SCOPE-1 (2026-08-27): item_type + allowed_doc_types drive
    # per-row scoping of the Reclassify dropdown. `item_type` is the
    # winning routed item's item_type (via document_item_association ->
    # delivery_item join; under D-155 one-doc-one-item this is a single
    # value). `allowed_doc_types` is the list of doc_type strings FR-86
    # accepts for that item_type; template renders one <option> per entry.
    # Both empty when: no assoc yet (vintage doc), or multiple assocs with
    # no intersection (rare N-way with incompatible slots).
    item_type: str = ""
    allowed_doc_types: tuple[str, ...] = ()
    # UPLOAD-DEST-1 (2026-09-06): where this document lands on the carrier, or
    # why it will not be delivered.
    #
    # The TPM's actual question is "where does this end up on Google Drive",
    # not "which work-item is it on". Until now nothing in the UI answered it,
    # and the answer was only computable inside submit_to_carrier's item loop
    # -- so a document could be silently excluded (DOCTYPE-MISALIGN-UI-1) or
    # land in an unexpected folder with no way to check beforehand.
    #
    # `carrier_destination` is the full <target_folder>[/<subdir>]/<filename>
    # path, computed through storage.upload_plan -- the SAME functions the
    # uploader calls, so display and delivery cannot drift.
    #
    # `upload_excluded_reason` is non-empty exactly when the file will NOT be
    # uploaded, and then carrier_destination is "". The reasons mirror the
    # uploader's own skips: waiver, archive container, superseded revision,
    # staged (not classified), and item-not-deliverable (no_customer_upload or
    # no target_folder). Rendering the reason rather than omitting the row is
    # the point -- the excluded files are the ones a TPM needs to see.
    carrier_destination: str = ""
    upload_excluded_reason: str = ""
    # UPLOAD-FOLDER-OVERRIDE-1 (2026-09-06): the TPM-chosen folder currently in
    # force for this document, or "" when the work item's own target_folder
    # applies. Surfaced so the row can show that it has been redirected and
    # offer a Reset -- and so carrier_destination above reflects the redirect
    # rather than the item's folder.
    target_folder_override: str = ""


# ---------------------------------------------------------------------------
# Save (versioned write)
# ---------------------------------------------------------------------------


async def save_view_document(
    *,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    tg_name: str,
    relative_parts: tuple[str, ...],
    content: bytes,
    saved_by: str,
    source: str = "editor",
) -> DocumentVersionRow:
    """Write file bytes to the view tree at
    `view/<customer>/<device>/<milestone>/<tg>/<relative_parts>` and record a
    new version row.

    Behavior:
      * If no file exists at that path yet, writes with version_num=1.
      * If a file exists, renames the current file to `<name>.v<N>` (using
        the current version_num of the existing is_current row), writes the
        new bytes as the new current file, and inserts a new version_num=N+1
        row with is_current=True. Previous current-version row is flipped to
        is_current=False.
      * Bytes-identical re-save is a no-op at the file level (NSDPath.write_file
        idempotency) BUT still records a new version row — the audit signal
        matters even when content didn't change.

    Returns the new DocumentVersionRow (with the freshly-generated version_id).
    """
    if not relative_parts:
        raise PipelineError(
            "STR-E004",
            context={"reason": "save_view_document requires non-empty relative_parts"},
        )
    filename = relative_parts[-1]
    current_path = NSDPath.view_tree(
        customer_id, device_id, milestone_id, tg_name, *relative_parts,
    )
    view_relative = current_path.to_relative()

    async with _session() as session:
        prior_current = await _get_current_row(session, view_relative)
        next_version_num = 1 if prior_current is None else prior_current.version_num + 1

        # If prior current exists, archive the on-disk file to `<name>.v<N>`
        # BEFORE writing new bytes on top. NSD `write_file` is atomic (temp +
        # rename) so a crash between archive + new-write leaves the archive in
        # place — Ph-1 accepts brief inconsistency during Ph-1 (no cross-crash
        # recovery). Ph-2 revisit.
        if prior_current is not None:
            await _archive_current_to_sibling(current_path, prior_current.version_num)

        await _write_bytes(current_path, content)

        # Flip prior current to non-current
        if prior_current is not None:
            await session.execute(
                update(DocumentVersionTable)
                .where(DocumentVersionTable.version_id == prior_current.version_id)
                .values(is_current=False)
            )

        # Insert the new current row
        now = datetime.now(timezone.utc)
        sha = hashlib.sha256(content).hexdigest()
        # D-152 DRM sniff: OnlyOffice can't decrypt NASCA-wrapped bytes; the
        # dashboard checks this flag to gate the Edit button off. Editor
        # save-backs (source="editor") should never be wrapped in practice —
        # OnlyOffice writes cleartext — but we sniff unconditionally so a
        # corrupt/spoofed round-trip still lands with the flag set.
        is_drm = content.startswith(_NASCA_MAGIC)
        new_row = DocumentVersionRow(
            version_id=uuid.uuid4().hex,
            view_relative_path=view_relative,
            customer_id=customer_id,
            device_id=device_id,
            milestone_id=milestone_id,
            tg_name=tg_name,
            filename=filename,
            version_num=next_version_num,
            is_current=True,
            size_bytes=len(content),
            sha256=sha,
            saved_at=now,
            saved_by=saved_by,
            source=source,
            is_drm_wrapped=is_drm,
        )
        session.add(_row_to_table(new_row))
        await session.commit()
        return new_row


async def _archive_current_to_sibling(current_path: NSDPath, current_version_num: int) -> None:
    """Rename current file to `<name>.v<N>` before overwriting with new bytes.
    Best-effort — if current file doesn't actually exist on disk (row exists
    but file lost), we skip the archive rather than fail the save.
    """
    import asyncio

    src = current_path.to_local()
    if not await asyncio.to_thread(src.exists):
        return
    sibling = NSDPath.view_version_sibling(current_path, current_version_num).to_local()
    try:
        await asyncio.to_thread(src.replace, sibling)
    except OSError as exc:
        raise PipelineError(
            "STR-E004",
            context={"path": current_path.to_relative(), "reason": f"archive rename failed: {exc}"[:120]},
            cause=exc,
        )


async def _write_bytes(path: NSDPath, content: bytes) -> None:
    """Wrap NSDPath.write_file's async-iterator contract with plain bytes."""
    async def _one_chunk():
        yield content
    await write_file(path, _one_chunk())


# ---------------------------------------------------------------------------
# Read (current + historical versions)
# ---------------------------------------------------------------------------


async def get_current_version(view_relative_path: str) -> DocumentVersionRow | None:
    """Return the current-version row for a given file path, or None."""
    async with _session() as session:
        row = await _get_current_row(session, view_relative_path)
        return _table_to_row(row) if row else None


async def get_version_by_num(view_relative_path: str, version_num: int) -> DocumentVersionRow | None:
    """Return a specific historical version row by (path, version_num)."""
    async with _session() as session:
        result = await session.execute(
            select(DocumentVersionTable).where(
                DocumentVersionTable.view_relative_path == view_relative_path,
                DocumentVersionTable.version_num == version_num,
            )
        )
        row = result.scalar_one_or_none()
        return _table_to_row(row) if row else None


async def list_versions_for_file(view_relative_path: str) -> list[DocumentVersionRow]:
    """Full version history for a file, newest first."""
    async with _session() as session:
        result = await session.execute(
            select(DocumentVersionTable)
            .where(DocumentVersionTable.view_relative_path == view_relative_path)
            .order_by(DocumentVersionTable.version_num.desc())
        )
        return [_table_to_row(r) for r in result.scalars().all()]


# ---------------------------------------------------------------------------
# Landing page + browse
# ---------------------------------------------------------------------------


async def list_tg_names_for_scope(
    customer_id: str, device_id: str, milestone_id: str,
) -> list[TgFolderEntry]:
    """Landing-page listing: distinct tg_names in this (customer, device,
    milestone) scope with per-tg file/version counts.

    Returned entries sorted alphabetically by tg_name for stable rendering.
    """
    async with _session() as session:
        result = await session.execute(
            select(DocumentVersionTable).where(
                DocumentVersionTable.customer_id == customer_id,
                DocumentVersionTable.device_id == device_id,
                DocumentVersionTable.milestone_id == milestone_id,
                DocumentVersionTable.is_current.is_(True),
            )
        )
        rows = list(result.scalars().all())

    per_tg: dict[str, list[DocumentVersionTable]] = {}
    for r in rows:
        per_tg.setdefault(r.tg_name, []).append(r)
    entries: list[TgFolderEntry] = []
    for tg, group in per_tg.items():
        last = max((g.saved_at for g in group), default=None)
        entries.append(TgFolderEntry(tg_name=tg, file_count=len(group), last_saved_at=last))
    entries.sort(key=lambda e: e.tg_name)
    return entries


async def list_files_in_tg(
    customer_id: str, device_id: str, milestone_id: str, tg_name: str,
) -> list[TgFileEntry]:
    """Flat list of current-version files under a given tg_name for the
    browse UI. Per architect Q4 lock 2026-07-22: flat list, all docs across
    all work items with this tg_name.

    Zip-extracted files preserve their relative path within the tg tree
    (`<tg>/<zip-internal-folder>/<file>`) — the view_relative_path field
    captures the full path; UI can group by first-segment on client side if
    it wants to render subdirectories.
    """
    # RECLASS-1 (2026-08-24): batch-fetch doc_type + is_staged from the
    # sibling tables (document_index, document_item_association) keyed by
    # file_hash. Only one round-trip per table (WHERE file_hash IN <set>).
    from core.src.storage.db import (
        DeliveryItemTable as _DelItm,
        DocumentIndexTable as _DocIx,
        DocumentItemAssociationTable as _DocAssoc,
    )

    async with _session() as session:
        result = await session.execute(
            select(DocumentVersionTable).where(
                DocumentVersionTable.customer_id == customer_id,
                DocumentVersionTable.device_id == device_id,
                DocumentVersionTable.milestone_id == milestone_id,
                DocumentVersionTable.tg_name == tg_name,
                DocumentVersionTable.is_current.is_(True),
            )
        )
        current_rows = list(result.scalars().all())

        # Count total versions per path for the version_count field.
        # Also collect the set of saved_by values across ALL versions per path
        # so MERGE-1 can compute needs_merge (owner-on-top-of-TPM-edit signal).
        version_count_by_path: dict[str, int] = {}
        saved_by_set_by_path: dict[str, set[str]] = {}
        # MERGE-2 (2026-08-30): every version's sha256 per path. A TPM edit
        # creates a document_version row with a fresh sha256 and NO
        # document_index row, so the family join has to match associations
        # against ANY of a path's version hashes -- the ingest-time (v1) hash
        # is the one that carries the association. Same join MTR-1 uses.
        sha_set_by_path: dict[str, set[str]] = {}
        for r in current_rows:
            all_versions_result = await session.execute(
                select(DocumentVersionTable).where(
                    DocumentVersionTable.view_relative_path == r.view_relative_path,
                )
            )
            all_versions = list(all_versions_result.scalars().all())
            version_count_by_path[r.view_relative_path] = len(all_versions)
            saved_by_set_by_path[r.view_relative_path] = {
                (v.saved_by or "") for v in all_versions
            }
            sha_set_by_path[r.view_relative_path] = {
                v.sha256 for v in all_versions if v.sha256
            }

        # Every version hash across every path in this TG. MERGE-2 and
        # RECLASS-DISPLAY-1 both key off this rather than the current version.
        all_version_hashes = list({
            h for shas in sha_set_by_path.values() for h in shas
        })

        # RECLASS-1 (2026-08-24) / RECLASS-BUGFIX-2 (2026-08-26): batch-resolve
        # doc_type from document_index. is_staged is derived from doc_type
        # alone (see TgFileEntry docstring); no longer joins to
        # document_item_association.nsd_path_type.
        #
        # RECLASS-DISPLAY-1 (2026-08-31): widened from the CURRENT version's
        # sha256 to ALL of a path's version hashes. A TPM browser edit writes a
        # document_version row with a fresh sha256 and NO document_index row,
        # so a current-version lookup missed and the TG view rendered an empty
        # Doc Type for every edited file. Worse, `is_staged` then resolved via
        # the "__missing__" sentinel to False, so the Reclassify control was
        # withheld too -- the row showed no classification and offered no way
        # to set one. The classification was never lost; only the lookup was
        # pointed at the wrong hash. A row represents a DOCUMENT, so it
        # resolves against the document's indexed hash, not the byte-identity
        # of whichever version happens to be current.
        doc_meta_by_hash: dict[str, tuple[str, int]] = {}   # hash -> (doc_type, rev)
        # UPLOAD-DEST-1: from_zip decides whether the view path's folder
        # segments are carrier structure or NSD noise (UPLOAD-FLAT-1). Read
        # off the same row, no extra query.
        from_zip_by_hash: dict[str, bool] = {}
        if all_version_hashes:
            ix_rows = (await session.execute(
                select(_DocIx.file_hash, _DocIx.doc_type, _DocIx.rev_number,
                       _DocIx.from_zip)
                .where(_DocIx.file_hash.in_(all_version_hashes))
            )).all()
            doc_meta_by_hash = {
                h: ((dt or ""), int(rv or 0)) for h, dt, rv, _fz in ix_rows
            }
            from_zip_by_hash = {h: bool(fz) for h, _dt, _rv, fz in ix_rows}

        # RECLASS-UI-SCOPE-1 (2026-08-27): resolve routed item's item_type per
        # file, so template can render the Reclassify dropdown scoped to
        # FR-86-aligned options. Join document_item_association -> delivery_item.
        # Under D-155 one-doc-one-item this yields a single item_type; on rare
        # N-way we take the intersection of allowed sets (empty if incompatible).
        item_types_by_hash: dict[str, set[str]] = {}
        # DOCTYPE-MISALIGN-UI-1: nsd_path_type comes off the SAME row, so this
        # costs no extra query. See TgFileEntry.is_staged_not_classified.
        staged_by_hash: dict[str, bool] = {}
        # UPLOAD-DEST-1: the routed item's carrier folder + deliverability,
        # off the same join. Under D-155 one-doc-one-item this is a single
        # value per hash; on a rare N-way we keep the first seen, which
        # matches how item_type already collapses.
        target_folder_by_hash: dict[str, str] = {}
        no_upload_by_hash: dict[str, bool] = {}
        override_by_hash: dict[str, str] = {}
        # DRRP1-DEST-1: item_no comes off the same join, so resolving the
        # forward mapping costs one extra query for the whole page rather
        # than one per file.
        item_no_by_hash: dict[str, int] = {}
        migrated_folder_by_hash: dict[str, str] = {}
        migrated_note_by_hash: dict[str, str] = {}
        if all_version_hashes:
            assoc_rows = (await session.execute(
                select(_DocAssoc.file_hash, _DelItm.item_type,
                       _DocAssoc.nsd_path_type, _DelItm.target_folder,
                       _DelItm.no_customer_upload,
                       _DocAssoc.upload_target_folder_override,
                       _DelItm.item_no)
                .join(_DelItm, _DocAssoc.delivery_item_id == _DelItm.item_id)
                .where(_DocAssoc.file_hash.in_(all_version_hashes))
            )).all()
            for fh, it, npt, tgt, no_up, ov, ino in assoc_rows:
                if it:
                    item_types_by_hash.setdefault(fh, set()).add(it)
                if (npt or "") == NSDPathType.STAGED_NOT_CLASSIFIED.value:
                    staged_by_hash[fh] = True
                target_folder_by_hash.setdefault(fh, (tgt or "").strip())
                no_upload_by_hash.setdefault(fh, bool(no_up))
                override_by_hash.setdefault(fh, (ov or "").strip())
                if ino is not None:
                    item_no_by_hash.setdefault(fh, int(ino))

            # Resolve each source item that maps forward to its TARGET item's
            # folder. Grouped by (milestone, item_no) so N documents sharing
            # one mapped item cost one row, not N.
            from core.src.template_schema import (
                milestone_item_mapping as _mim,
            )
            wanted: dict[tuple[str, int], list[str]] = {}
            for fh, ino in item_no_by_hash.items():
                mapped = _mim.get_target_item_no(
                    customer_id=customer_id,
                    source_milestone=milestone_id,
                    source_item_no=ino,
                )
                if mapped is not None:
                    wanted.setdefault(mapped, []).append(fh)
            if wanted:
                tgt_rows = (await session.execute(
                    select(_DelItm.milestone_id, _DelItm.item_no,
                           _DelItm.target_folder)
                    .where(
                        _DelItm.customer_id == customer_id,
                        _DelItm.device_id == device_id,
                        _DelItm.milestone_id.in_({m for m, _ in wanted}),
                        _DelItm.item_no.in_({n for _, n in wanted}),
                    )
                )).all()
                folder_by_key = {
                    (m, int(n)): (f or "").strip() for m, n, f in tgt_rows
                }
                for (tgt_ms, tgt_no), hashes in wanted.items():
                    folder = folder_by_key.get((tgt_ms, tgt_no), "")
                    if not folder:
                        # Mapped, but the target item has no folder (or does
                        # not exist for this device). Leave the exclusion
                        # reason intact rather than inventing a destination.
                        continue
                    note = f"via {milestone_id} → {tgt_ms} #{tgt_no}"
                    for fh in hashes:
                        migrated_folder_by_hash[fh] = folder
                        migrated_note_by_hash[fh] = note

        # MERGE-2 (2026-08-30): resolve each view path to its revision family
        # (delivery_item_id, doc_id_slug) and that path's revision number.
        # Keyed off ALL version hashes, not just the current one -- see the
        # sha_set_by_path comment above.
        family_by_path: dict[str, tuple[str, str]] = {}
        rev_by_path: dict[str, int] = {}
        if all_version_hashes:
            fam_rows = (await session.execute(
                select(
                    _DocIx.file_hash,
                    _DocIx.doc_id_slug,
                    _DocIx.rev_number,
                    _DocAssoc.delivery_item_id,
                )
                .join(_DocAssoc, _DocAssoc.file_hash == _DocIx.file_hash)
                .where(
                    _DocIx.file_hash.in_(all_version_hashes),
                    _DocIx.doc_id_slug.is_not(None),
                    _DocIx.rev_number.is_not(None),
                )
            )).all()
            fam_by_hash: dict[str, tuple[str, str, int]] = {}
            for fh, slug, rev, item_id in fam_rows:
                if not item_id or not slug or rev is None:
                    continue
                # One doc may associate to several items (rare N-way); take a
                # deterministic winner so family keys stay stable across calls.
                prev = fam_by_hash.get(fh)
                if prev is None or (item_id, slug) < (prev[0], prev[1]):
                    fam_by_hash[fh] = (item_id, slug, int(rev))
            for path, shas in sha_set_by_path.items():
                best: tuple[str, str, int] | None = None
                for h in shas:
                    cand = fam_by_hash.get(h)
                    if cand is None:
                        continue
                    # A same-filename resend puts BOTH revisions on one path;
                    # the path's effective revision is the highest it holds.
                    if best is None or cand[2] > best[2]:
                        best = cand
                if best is not None:
                    family_by_path[path] = (best[0], best[1])
                    rev_by_path[path] = best[2]

    # Group paths by family and pick each family's winning (highest) revision.
    paths_by_family: dict[tuple[str, str], list[str]] = {}
    for path, fam in family_by_path.items():
        paths_by_family.setdefault(fam, []).append(path)
    winner_by_family: dict[tuple[str, str], str] = {
        fam: max(paths, key=lambda p: (rev_by_path.get(p, 0), p))
        for fam, paths in paths_by_family.items()
    }
    current_saved_by_by_path = {
        r.view_relative_path: (r.saved_by or "") for r in current_rows
    }

    def _needs_merge(current_saved_by: str, all_saved_by: set[str]) -> bool:
        # Current must be owner AND at least one prior version must be
        # non-owner (TPM/human). Owner sentinel = "auto"; TPM = "unknown" or
        # any real corp_id (see _pretty_by in document_view_routes.py).
        if current_saved_by != "auto":
            return False
        return any(sb != "auto" for sb in all_saved_by)

    def _family_needs_merge(fam: tuple[str, str]) -> bool:
        """MERGE-2: the MERGE-1 predicate applied at family scope -- the
        winning revision's current version is owner-authored while somewhere
        in the family a human version exists that it did not incorporate."""
        winner = winner_by_family[fam]
        history: set[str] = set()
        for p in paths_by_family[fam]:
            history |= saved_by_set_by_path.get(p, set())
        return _needs_merge(current_saved_by_by_path.get(winner, ""), history)

    def _merge_flags(path: str, current_saved_by: str) -> tuple[bool, bool]:
        """Return (needs_merge, is_superseded) for one view path."""
        fam = family_by_path.get(path)
        if fam is None:
            # No resolvable family (vintage doc, staged row with no slug, or
            # no association yet) -- keep the original path-scoped behaviour.
            return (
                _needs_merge(
                    current_saved_by, saved_by_set_by_path.get(path, set())
                ),
                False,
            )
        superseded = winner_by_family[fam] != path
        if not _family_needs_merge(fam):
            return False, superseded
        # Per the user's 2026-08-30 rule the marker rides the revision the TPM
        # actually edited, not the winner -- that is the content they need to
        # carry forward, and they recognize their own work. In a single-path
        # family this is the same row the path-scoped rule would have flagged.
        touched_by_human = any(
            sb != "auto" for sb in saved_by_set_by_path.get(path, set())
        )
        return touched_by_human, superseded

    def _allowed_for_item_types(item_types: set[str]) -> tuple[str, ...]:
        """RECLASS-UI-SCOPE-1: intersection of FR-86-aligned doc_type sets
        across all routed items for this file. Mirrors
        Fr52AttachmentRouter._fr86_aligned. Under D-155 one-doc-one-item the
        set is a singleton; multi-item is rare N-way. Confirmation + default
        item_types accept ANY doc_type -> all 4."""
        from core.src.template_schema.enums import DocType, ItemType
        ALL = (
            DocType.TEST_REPORT.value,
            DocType.TECH_REPORT.value,
            DocType.WAIVER.value,
            DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
        )
        TTWR = (
            DocType.TEST_REPORT.value,
            DocType.TECH_REPORT.value,
            DocType.WAIVER.value,
        )
        RELNOTES = (DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,)
        per_item: list[tuple[str, ...]] = []
        for it in item_types:
            if it == ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value:
                per_item.append(RELNOTES)
            elif it == ItemType.TEST_TECH_WAIVER_REPORT.value:
                per_item.append(TTWR)
            elif it in (ItemType.CONFIRMATION.value, ItemType.DEFAULT.value):
                per_item.append(ALL)
            else:
                per_item.append(())  # unknown item_type -> no options
        if not per_item:
            return ()
        # Intersection preserving canonical order.
        common = set(per_item[0])
        for opts in per_item[1:]:
            common &= set(opts)
        return tuple(dt for dt in ALL if dt in common)

    def _indexed_hash(path: str) -> str:
        """RECLASS-DISPLAY-1: the hash that carries this document's identity --
        the highest-revision version of this path that has a document_index
        row. Empty when the path has no index row at all (vintage / pre-D-150
        documents), which the sentinel below treats as "not reclassifiable"."""
        best = ""
        best_rev = -1
        for h in sha_set_by_path.get(path, set()):
            meta = doc_meta_by_hash.get(h)
            if meta is not None and meta[1] > best_rev:
                best, best_rev = h, meta[1]
        return best

    merge_flags_by_path = {
        r.view_relative_path: _merge_flags(r.view_relative_path, r.saved_by or "")
        for r in current_rows
    }
    indexed_hash_by_path = {
        r.view_relative_path: _indexed_hash(r.view_relative_path)
        for r in current_rows
    }

    # UPLOAD-DEST-1: resolved per row before the comprehension -- the
    # expression is too long to inline readably alongside the existing
    # indexed-hash lookups, and both outputs come from one call.
    dest_by_path: dict[str, tuple[str, str]] = {}
    for r in current_rows:
        vrp = r.view_relative_path
        ih = indexed_hash_by_path[vrp]
        dest_by_path[vrp] = resolve_carrier_destination(
            view_relative_path=vrp,
            filename=r.filename,
            doc_type=doc_meta_by_hash.get(ih, ("", 0))[0],
            from_zip=from_zip_by_hash.get(ih, False),
            target_folder=target_folder_by_hash.get(ih, ""),
            no_customer_upload=no_upload_by_hash.get(ih, False),
            is_superseded=merge_flags_by_path[vrp][1],
            is_staged_not_classified=staged_by_hash.get(ih, False),
            target_folder_override=override_by_hash.get(ih, ""),
            item_type=next(iter(item_types_by_hash.get(ih, set())), ""),
            migrated_target_folder=migrated_folder_by_hash.get(ih, ""),
        )

    entries = [
        TgFileEntry(
            view_relative_path=r.view_relative_path,
            filename=r.filename,
            size_bytes=r.size_bytes,
            version_count=version_count_by_path.get(r.view_relative_path, 1),
            last_saved_at=r.saved_at,
            last_saved_by=r.saved_by,
            is_drm_wrapped=bool(r.is_drm_wrapped),
            needs_merge=merge_flags_by_path[r.view_relative_path][0],
            is_superseded=merge_flags_by_path[r.view_relative_path][1],
            doc_type=doc_meta_by_hash.get(
                indexed_hash_by_path[r.view_relative_path], ("", 0)
            )[0],
            # RECLASS-DISPLAY-1: the INDEXED hash, not the current version's.
            # The reclassify POST uses this as the primary key into
            # tpm_resolve_doc_type, and a TPM-edited version has no
            # document_index row to resolve against. Falls back to the current
            # sha256 when nothing is indexed, so vintage rows keep a stable id.
            file_hash=indexed_hash_by_path[r.view_relative_path] or (r.sha256 or ""),
            # RECLASS-BUGFIX-2 (2026-08-26): sentinel distinguishes "no
            # document_index row" (legacy/vintage doc -- don't offer
            # Reclassify) from "index row present with unresolved doc_type"
            # (offer Reclassify).
            is_staged=(
                doc_meta_by_hash[indexed_hash_by_path[r.view_relative_path]][0]
                if indexed_hash_by_path[r.view_relative_path] in doc_meta_by_hash
                else "__missing__"
            ) in ("", "unresolved"),
            # DOCTYPE-MISALIGN-UI-1: resolved against the INDEXED hash for the
            # same reason as is_staged/doc_type -- a TPM-edited version has a
            # fresh sha256 and no association of its own.
            is_staged_not_classified=staged_by_hash.get(
                indexed_hash_by_path[r.view_relative_path], False
            ),
            item_type=(next(iter(item_types_by_hash.get(
                indexed_hash_by_path[r.view_relative_path], set())), "")),
            allowed_doc_types=_allowed_for_item_types(
                item_types_by_hash.get(
                    indexed_hash_by_path[r.view_relative_path], set())
            ),
            migrated_to=migrated_note_by_hash.get(
                indexed_hash_by_path[r.view_relative_path], ""),
            carrier_destination=dest_by_path[r.view_relative_path][0],
            upload_excluded_reason=dest_by_path[r.view_relative_path][1],
            target_folder_override=override_by_hash.get(
                indexed_hash_by_path[r.view_relative_path], ""),
        )
        for r in current_rows
    ]
    # SORT-DEST-1 (2026-09-08): documents that will NOT be delivered sort
    # first. On a 14-file page the two rows needing a TPM decision are the
    # whole point of the page, and ordered by path they scatter among the
    # rows that are already fine. The secondary key groups like reasons
    # together -- all staged, then all waivers -- so one kind of action
    # clears a contiguous run, and the delivered group reads in
    # carrier-folder order. view_relative_path stays the final tiebreak, so
    # ordering within a group is unchanged and still deterministic.
    entries.sort(key=lambda e: (
        0 if e.upload_excluded_reason else 1,
        (e.upload_excluded_reason or e.carrier_destination or "").lower(),
        e.view_relative_path,
    ))
    return entries


# ---------------------------------------------------------------------------
# Bytes IO — reads a specific version's content off NSD
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ItemUploadFile:
    """UPLOAD-VIEW-1 (2026-08-30): one file to submit to the carrier for a
    delivery item -- the resolved winner of one revision family.

    `relative_path` is NSD-share-relative; the caller prepends the host mount
    prefix. `is_view` distinguishes the two sources:

      * True  -- the view tree (`view/<c>/<d>/<m>/<tg>/...`). The file at that
        path IS the current version, so a TPM edit is what uploads. Any leading
        folder segments came from an archive's internal structure and should be
        recreated under the carrier target folder.
      * False -- fallback to the association's internal-tree path, used when no
        view-tree row matches (items with no tg_name, Default WI, or documents
        that predate D-150). Preserves the pre-UPLOAD-VIEW-1 behaviour so
        nothing that uploads today stops uploading.
    """

    relative_path: str
    filename: str
    doc_type: str
    is_view: bool
    # UPLOAD-FLAT-1 (2026-08-31): True when this document was extracted from an
    # archive. Only then are its leading path segments real carrier structure;
    # a file that arrived standalone inside an NSD folder must upload FLAT.
    # The view path cannot distinguish the two on its own.
    from_zip: bool = False
    # DRRP1-1 chunk 2 (2026-09-01): set when this file belongs to a DIFFERENT
    # milestone's work-item and is being submitted on this item's behalf per
    # milestone_item_mapping. Empty / 0 for an item's own documents. Carried so
    # the audit trail and the UI can say "from DRR #50" rather than presenting
    # a borrowed document as if it had arrived here.
    migrated_from_milestone: str = ""
    migrated_from_item_no: int = 0
    # UPLOAD-FOLDER-OVERRIDE-1 (2026-09-06): TPM-chosen carrier folder that
    # REPLACES the work item's target_folder for this document. Empty means
    # "use the item's folder". The archive-derived subdir still rides
    # underneath either way, so zip structure survives a redirect.
    target_folder_override: str = ""


async def list_upload_files_for_item(delivery_item_id: str) -> list[ItemUploadFile]:
    """UPLOAD-VIEW-1 (2026-08-30): resolve exactly what should be uploaded to
    the carrier for one delivery item -- one file per revision family, at its
    current version.

    Two distinct axes have to collapse here, and conflating them is what the
    prior implementation got wrong:

      * REVISION (document_index) -- an owner resend creates a new index row +
        association. Iterating associations therefore uploaded EVERY revision,
        each under the same filename. We group by doc_id_slug and keep one
        winner per family: `is_final` when the TPM has pinned one per FR-66,
        otherwise the highest rev_number.
      * VERSION (document_version) -- a TPM browser edit creates a new
        view-tree version with a fresh sha256 and NO index row. The file living
        at the view path is always the current version, so resolving the winner
        to its view path automatically yields the TPM's edit rather than the
        as-received bytes.

    Excluded here rather than at the call site:
      * doc_type == waiver -- waivers are never uploaded, any milestone
        (user lock 2026-08-30).
      * archive containers -- the view tree stores the original .zip/.7z
        alongside its extracted contents for audit/re-download; the carrier
        gets the contents, never the container.
    """
    from core.src.storage.db import (
        DocumentIndexTable as _DocIx,
        DocumentItemAssociationTable as _DocAssoc,
    )
    from core.src.storage.archive_extractor import is_archive_filename

    async with _session() as session:
        assoc_rows = (await session.execute(
            select(
                _DocAssoc.file_hash,
                _DocAssoc.local_nsd_path,
                _DocAssoc.upload_target_folder_override,
            ).where(
                _DocAssoc.delivery_item_id == delivery_item_id,
                _DocAssoc.nsd_path_type == NSDPathType.CLASSIFIED.value,
            )
        )).all()
        if not assoc_rows:
            return []
        local_path_by_hash = {fh: (p or "") for fh, p, _ov in assoc_rows}
        override_by_hash = {fh: (ov or "") for fh, _p, ov in assoc_rows}
        hashes = list(local_path_by_hash)

        ix_rows = (await session.execute(
            select(
                _DocIx.file_hash,
                _DocIx.doc_id_slug,
                _DocIx.rev_number,
                _DocIx.doc_type,
                _DocIx.is_final,
                _DocIx.original_filename,
                _DocIx.from_zip,
            ).where(_DocIx.file_hash.in_(hashes))
        )).all()

        # Group into revision families. A hash with no slug (staged-fill row,
        # or no index row at all) is its own single-member family, keyed on the
        # hash so it can never collide with a real slug.
        families: dict[str, list[tuple]] = {}
        seen_hashes: set[str] = set()
        for fh, slug, rev, doc_type, is_final, orig_name, from_zip in ix_rows:
            seen_hashes.add(fh)
            key = slug or f"__hash__{fh}"
            families.setdefault(key, []).append(
                (fh, int(rev or 0), doc_type or "", bool(is_final),
                 orig_name or "", bool(from_zip))
            )
        for fh in hashes:
            if fh not in seen_hashes:
                families[f"__hash__{fh}"] = [(fh, 0, "", False, "", False)]

        winners: list[tuple] = []
        for members in families.values():
            pinned = [m for m in members if m[3]]        # is_final per FR-66
            pool = pinned or members
            winners.append(max(pool, key=lambda m: (m[1], m[0])))

        winner_hashes = [w[0] for w in winners]
        view_path_by_hash: dict[str, str] = {}
        if winner_hashes:
            dv_rows = (await session.execute(
                select(
                    DocumentVersionTable.sha256,
                    DocumentVersionTable.view_relative_path,
                ).where(DocumentVersionTable.sha256.in_(winner_hashes))
            )).all()
            for sha, vrp in dv_rows:
                if sha and vrp:
                    view_path_by_hash.setdefault(sha, vrp)

    out: list[ItemUploadFile] = []
    for fh, _rev, doc_type, _is_final, orig_name, from_zip in winners:
        if doc_type == DocType.WAIVER.value:
            continue
        if doc_type == DocType.ARCHIVE_CONTAINER.value:
            continue
        view_path = view_path_by_hash.get(fh, "")
        relative_path = view_path or local_path_by_hash.get(fh, "")
        if not relative_path:
            continue
        filename = PurePosixPath(relative_path).name or orig_name
        if not filename or is_archive_filename(filename):
            continue
        out.append(ItemUploadFile(
            relative_path=relative_path,
            filename=filename,
            doc_type=doc_type,
            is_view=bool(view_path),
            from_zip=from_zip,
            target_folder_override=override_by_hash.get(fh, ""),
        ))
    out.sort(key=lambda f: f.relative_path)
    return out


def resolve_carrier_destination(
    *,
    view_relative_path: str,
    filename: str,
    doc_type: str,
    from_zip: bool,
    target_folder: str,
    no_customer_upload: bool,
    is_superseded: bool,
    is_staged_not_classified: bool,
    target_folder_override: str = "",
    item_type: str = "",
    migrated_target_folder: str = "",
) -> tuple[str, str]:
    """UPLOAD-DEST-1: `(carrier_destination, upload_excluded_reason)`.

    Exactly one of the two is non-empty. The exclusion checks mirror what
    `list_upload_files_for_item` and `submit_to_carrier` actually skip, in the
    order a TPM would want to hear them -- the most specific, most surprising
    reason first.

    Deliberately NOT checked here: the item's delivery_state. submit_to_carrier
    only uploads items in ReadyForSubmission, but that is a "not yet" rather
    than a "never", and applying it would blank the whole view before a
    submission -- exactly when someone wants to look. State is surfaced
    per-item by the caller instead.
    """
    from core.src.storage.upload_plan import carrier_subdir, effective_target_dir

    if doc_type == DocType.WAIVER.value:
        return "", "waiver — never uploaded, any milestone"
    if doc_type == DocType.ARCHIVE_CONTAINER.value:
        return "", "archive container — the carrier gets its contents, not the container"
    if is_superseded:
        return "", "superseded revision — only the latest revision is submitted"
    if is_staged_not_classified:
        # STAGED-REASON-1 (2026-09-08): `is_staged_not_classified` reports the
        # STORED nsd_path_type, which is not proof of a misalignment -- a
        # manual route wrote that state unconditionally (UNROUTED-ALIGNED-1),
        # so a file whose doc_type genuinely aligns could carry it. Naming
        # both values makes the claim checkable, and the aligned case gets a
        # message that says what to do instead of a diagnosis that is false.
        from core.src.email_service.inbound.attachment_router import (
            Fr52AttachmentRouter as _Fr52,
        )
        if item_type and doc_type and _Fr52._fr86_aligned(item_type, doc_type):
            return "", (
                f"staged — doc type {doc_type} DOES match item_type "
                f"{item_type}; parked in staged classification. Reclassify "
                f"to release it for submission"
            )
        return "", (
            f"staged — doc type {doc_type or '(unresolved)'} does not match "
            f"item_type {item_type or '(unknown)'}"
        )
    subdir = carrier_subdir(
        relative_path=view_relative_path, is_view=True, from_zip=from_zip,
    )
    override = (target_folder_override or "").strip()

    def _path(folder: str) -> tuple[str, str]:
        # UPLOAD-FOLDER-OVERRIDE-1: same precedence as submit_to_carrier.
        directory = effective_target_dir(override or folder, subdir)
        return (f"{directory}/{filename}" if directory else filename), ""

    # DRRP1-DEST-1 (2026-09-08): every mapped DRR item is no_customer_upload
    # by design -- DRR itself never submits. But its documents DO reach the
    # carrier, under the mapped target milestone, via
    # list_migrated_upload_files_for_item. Reporting "not uploaded" was true
    # of DRR in isolation and the opposite of what actually happens, on the
    # one milestone whose whole purpose is feeding P1 -- and it was the only
    # place a TPM could have seen the real destination.
    if no_customer_upload and migrated_target_folder:
        return _path(migrated_target_folder)
    if no_customer_upload:
        return "", "work item is marked no_customer_upload"
    if not target_folder and not override:
        return "", "work item has no target_folder"
    return _path(target_folder)


async def _find_item_by_scope(
    session, customer_id: str, device_id: str, milestone_id: str, item_no: int,
):
    """DRRP1-1: resolve one delivery_item row by its natural scope.

    Deliberately a query rather than composing the `[D-091]` item_id string
    (`{customer}-{device}-{milestone}-{item_no}`): device ids contain hyphens
    (`SM-S671U1`), so the composite key cannot be parsed back apart, and
    composing it forward would bake the id scheme into yet another call site.
    """
    from core.src.storage.db import DeliveryItemTable as _DelItm
    return (await session.execute(
        select(_DelItm).where(
            _DelItm.customer_id == customer_id,
            _DelItm.device_id == device_id,
            _DelItm.milestone_id == milestone_id,
            _DelItm.item_no == int(item_no),
        ).limit(1)
    )).scalars().first()


@dataclass(frozen=True)
class MigrationSource:
    """DRRP1-1 chunk 4 (2026-09-01): the resolved source work-item that feeds a
    target item, per `milestone_item_mapping`.

    Exists as its own type because TWO consumers need the same resolution and
    must not drift: `submit_to_carrier` (which needs the source's uploadable
    FILES) and the dashboard document section (which needs the source's
    document INDEX rows + associations, so it can reuse the item's own
    rendering path verbatim rather than re-deriving filenames and download
    tokens from a different shape).
    """

    item_id: str
    milestone: str
    item_no: int
    customer_id: str
    device_id: str
    target_milestone: str
    target_item_no: int


async def resolve_migration_sources(
    target_item_id: str,
) -> list[MigrationSource]:
    """DRRP1-1: which work-items, if any, feed `target_item_id`?

    A target may be fed by more than one source — MMK maps both DRR #35 and
    DRR #60 onto P1 #23 — so this returns every contributor, in mapping-file
    order. Callers that collect documents MUST use this rather than
    `resolve_migration_source`, which reports only the first.

    Returns [] on every miss — unmapped target, unknown target, no mapping
    loaded — silently, because an unmapped item is the overwhelmingly normal
    case. The single WARN is a mapping that names a work-item this device does
    not have: that means the carrier's mapping and its `template.yaml` have
    drifted, or the source milestone was never set up for this device, and
    both are worth surfacing rather than swallowing.
    """
    from core.src.template_schema import milestone_item_mapping as _mim

    async with _session() as session:
        from core.src.storage.db import DeliveryItemTable as _DelItm
        target = (await session.execute(
            select(_DelItm).where(_DelItm.item_id == target_item_id).limit(1)
        )).scalars().first()
        if target is None:
            _log.info(
                "DRRP1-1: target item %r not found -- no migration source",
                target_item_id,
            )
            return []

        customer_id = target.customer_id or ""
        device_id = target.device_id or ""
        milestone_id = target.milestone_id or ""
        item_no = int(target.item_no or 0)

        mapped = _mim.get_source_item_nos(
            customer_id=customer_id,
            target_milestone=milestone_id,
            target_item_no=item_no,
        )

        out: list[MigrationSource] = []
        for source_milestone, source_item_no in mapped:
            source = await _find_item_by_scope(
                session, customer_id, device_id, source_milestone,
                source_item_no,
            )
            if source is None:
                # One absent contributor must not suppress the others.
                _log.warning(
                    "DRRP1-1: mapping %s#%d -> %s#%d, but no %s item exists "
                    "for customer=%s device=%s -- nothing to migrate from it",
                    source_milestone, source_item_no, milestone_id, item_no,
                    source_milestone, customer_id, device_id,
                )
                continue

            out.append(MigrationSource(
                item_id=source.item_id,
                milestone=source_milestone,
                item_no=source_item_no,
                customer_id=customer_id,
                device_id=device_id,
                target_milestone=milestone_id,
                target_item_no=item_no,
            ))
        return out


async def resolve_migration_source(
    target_item_id: str,
) -> MigrationSource | None:
    """First work-item feeding `target_item_id`, or None.

    Kept for callers that only need to know WHETHER this target is fed from
    another milestone. Document collection goes through
    `resolve_migration_sources`.
    """
    found = await resolve_migration_sources(target_item_id)
    return found[0] if found else None


async def list_migrated_upload_files_for_item(
    target_item_id: str,
) -> list[ItemUploadFile]:
    """DRRP1-1 chunk 2 (2026-09-01): documents another milestone's work-item
    contributes to THIS item's carrier submission.

    Per `milestone_item_mapping`, deliverables collected against a source
    milestone's work-item (DRR) are submitted as part of a later milestone
    (P1). The source item never uploads on its own path -- mapped DRR items are
    `no_customer_upload: true` -- so this is the only route those documents take
    to the carrier.

    Resolution: target item row -> reverse mapping lookup -> source item rows ->
    delegate to `list_upload_files_for_item` per source. Delegating rather than
    reimplementing means the source item's documents arrive already collapsed to
    one file per revision family, resolved to each family winner's CURRENT
    version, with waivers and archive containers excluded and the internal-tree
    fallback intact.

    More than one source may feed the target (MMK maps DRR #35 and #60 onto
    P1 #23); every contributor's files are returned, each tagged with the
    source it came from. Revision-family collapsing is per source item, so two
    contributors offering the same filename both survive to upload, where the
    flat path makes them collide -- that case is WARN-logged here.

    Returns [] on every miss, silently, because the user's contract is that a
    mapping expresses an expectation and not a guarantee: "whatever mapped file
    is available from DRM, those are uploaded". Misses are logged at DEBUG-ish
    INFO for traceability but are not warnings -- an unmapped item, or a mapped
    item that has simply not received its document yet, is the normal case for
    most of a milestone's life.

    Creates NO rows. The returned files still live in the source milestone's
    trees and remain indexed under the source milestone.
    """
    sources = await resolve_migration_sources(target_item_id)
    if not sources:
        return []

    tagged: list[ItemUploadFile] = []
    for src in sources:
        files = await list_upload_files_for_item(src.item_id)
        if not files:
            _log.info(
                "DRRP1-1: %s#%d has no uploadable documents for customer=%s "
                "device=%s -- nothing to migrate to %s#%d",
                src.milestone, src.item_no, src.customer_id, src.device_id,
                src.target_milestone, src.target_item_no,
            )
            continue

        contributed = [
            ItemUploadFile(
                relative_path=f.relative_path,
                filename=f.filename,
                doc_type=f.doc_type,
                is_view=f.is_view,
                from_zip=f.from_zip,
                migrated_from_milestone=src.milestone,
                migrated_from_item_no=src.item_no,
            )
            for f in files
        ]
        _log.warning(
            "DRRP1-1: migrating %d file(s) from %s#%d to %s#%d "
            "(customer=%s device=%s)",
            len(contributed), src.milestone, src.item_no,
            src.target_milestone, src.target_item_no,
            src.customer_id, src.device_id,
        )
        tagged.extend(contributed)

    # Uploads are flat, so two contributors offering the same filename would
    # resolve to one carrier path and the later would overwrite. Name them
    # here -- this is the only place both contributions are visible at once.
    if len(sources) > 1:
        seen: dict[str, str] = {}
        for f in tagged:
            key = f.filename.strip().lower()
            owner = f"{f.migrated_from_milestone}#{f.migrated_from_item_no}"
            if key in seen and seen[key] != owner:
                _log.warning(
                    "DRRP1-1: filename %r contributed to %s by BOTH %s and "
                    "%s -- flat upload means one will overwrite the other",
                    f.filename, target_item_id, seen[key], owner,
                )
            seen.setdefault(key, owner)

    return tagged


async def is_superseded_revision(view_relative_path: str) -> bool:
    """MERGE-2 (2026-08-30): True when this view path is a NON-winning revision
    of its family -- an older revision that carrier upload will not select.

    Deliberately delegates to `list_files_in_tg` rather than re-deriving the
    family join: the `/browse/edit` guard must agree with what the TG view
    showed, and a second implementation would drift. Cost is one TG listing
    per edit-open, which is user-triggered, not a background hot path.

    Returns False for anything unresolvable (malformed path, missing scope,
    no family) -- the guard fails OPEN so a lookup glitch never blocks a
    legitimate edit. The UI is the primary control; this is belt-and-braces
    against a direct URL hit with a stale token.
    """
    segments = [s for s in view_relative_path.replace("\\", "/").split("/") if s]
    # view/<customer>/<device>/<milestone>/<tg>/<...at least one filename...>
    if len(segments) < 6 or segments[0] != "view":
        return False
    _, customer_id, device_id, milestone_id, tg_name = segments[:5]
    try:
        files = await list_files_in_tg(
            customer_id=customer_id, device_id=device_id,
            milestone_id=milestone_id, tg_name=tg_name,
        )
    except Exception:  # noqa: BLE001
        return False
    for f in files:
        if f.view_relative_path == view_relative_path:
            return f.is_superseded
    return False


async def read_current_version_bytes(view_relative_path: str) -> bytes:
    """Return the byte contents of the current file at view_relative_path.
    Streams under the hood; buffers into memory for the caller since view-tree
    files fit in a WOPI-editable envelope (~300MB cap at the zip layer).
    """
    path = NSDPath.from_relative(view_relative_path)
    chunks: list[bytes] = []
    async for chunk in read_file(path):
        chunks.append(chunk)
    return b"".join(chunks)


async def read_version_bytes(view_relative_path: str, version_num: int) -> bytes:
    """Return the byte contents of a SPECIFIC historical version.

    If version_num matches the current-version row, reads the file at the
    view_relative_path directly. Otherwise reads the archived sibling at
    `<view_relative_path>.v<version_num>`.
    """
    current = await get_current_version(view_relative_path)
    if current is None:
        raise PipelineError(
            "STR-E004",
            context={"path": view_relative_path, "reason": "no such view-tree file"},
        )
    if version_num == current.version_num:
        return await read_current_version_bytes(view_relative_path)
    if version_num > current.version_num:
        raise PipelineError(
            "STR-E004",
            context={
                "path": view_relative_path,
                "version_num": version_num,
                "current": current.version_num,
                "reason": "requested version > current",
            },
        )
    current_path = NSDPath.from_relative(view_relative_path)
    sibling = NSDPath.view_version_sibling(current_path, version_num)
    chunks: list[bytes] = []
    async for chunk in read_file(sibling):
        chunks.append(chunk)
    return b"".join(chunks)


# ---------------------------------------------------------------------------
# Audit event history — for D-150 Chunk 7 History UI
# ---------------------------------------------------------------------------


# The action_types emitted by `_audit()` in dashboard.document_view_routes.
# Kept as a module-level constant so tests can grep it and future D-150 audit
# additions have one canonical list to extend.
_DOCUMENT_VIEW_ACTION_TYPES = (
    "document_viewed",
    "document_edit_opened",
    "document_saved",
    "document_downloaded",
    "document_edit_blocked_drm",  # D-152
)


async def list_document_events(view_relative_path: str) -> list[DocumentEventRow]:
    """All audit events written by the /browse/* + /wopi/* routes for a given
    file, newest-first.

    The audit writer (`_audit()` in dashboard.document_view_routes) stashes
    `view_relative_path` into CommunicationLog.external_message_id via the
    attribution.correlation_id path — that is our filter key. We further filter
    to the D-150 action_types so unrelated CommunicationLog rows that happened
    to share a correlation_id can never leak into the file's history.
    """
    import json

    async with _session() as session:
        result = await session.execute(
            select(CommunicationLogTable).where(
                CommunicationLogTable.external_message_id == view_relative_path,
                CommunicationLogTable.action_type.in_(_DOCUMENT_VIEW_ACTION_TYPES),
            ).order_by(CommunicationLogTable.timestamp.desc())
        )
        rows = list(result.scalars().all())

    events: list[DocumentEventRow] = []
    for r in rows:
        # summary is the JSON blob written by AuditWriterImpl:
        #   {"attribution": {...}, "details": {...}}
        parsed: dict = {}
        try:
            parsed = json.loads(r.summary) if r.summary else {}
        except (json.JSONDecodeError, TypeError):
            parsed = {}
        details = parsed.get("details") if isinstance(parsed, dict) else {}
        events.append(DocumentEventRow(
            timestamp=r.timestamp,
            action_type=r.action_type or "",
            user_id=r.sender,
            details=details if isinstance(details, dict) else {},
        ))
    return events


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


async def _get_current_row(session, view_relative_path: str) -> DocumentVersionTable | None:
    """Return the is_current=True row for this path if any."""
    result = await session.execute(
        select(DocumentVersionTable).where(
            DocumentVersionTable.view_relative_path == view_relative_path,
            DocumentVersionTable.is_current.is_(True),
        )
    )
    return result.scalar_one_or_none()


def _row_to_table(row: DocumentVersionRow) -> DocumentVersionTable:
    return DocumentVersionTable(
        version_id=row.version_id,
        view_relative_path=row.view_relative_path,
        customer_id=row.customer_id,
        device_id=row.device_id,
        milestone_id=row.milestone_id,
        tg_name=row.tg_name,
        filename=row.filename,
        version_num=row.version_num,
        is_current=row.is_current,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        saved_at=row.saved_at,
        saved_by=row.saved_by,
        source=row.source,
        is_drm_wrapped=row.is_drm_wrapped,
    )


def _table_to_row(t: DocumentVersionTable) -> DocumentVersionRow:
    return DocumentVersionRow(
        version_id=t.version_id,
        view_relative_path=t.view_relative_path,
        customer_id=t.customer_id,
        device_id=t.device_id,
        milestone_id=t.milestone_id,
        tg_name=t.tg_name,
        filename=t.filename,
        version_num=t.version_num,
        is_current=t.is_current,
        size_bytes=t.size_bytes,
        sha256=t.sha256,
        saved_at=t.saved_at,
        saved_by=t.saved_by,
        source=t.source,
        is_drm_wrapped=t.is_drm_wrapped,
    )
