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
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update

from core.src.diagnostics.error_codes import PipelineError
from core.src.storage.db import CommunicationLogTable, DocumentVersionTable, session_scope
from core.src.storage.models import DocumentVersionRow
from core.src.storage.nsd import NSDPath, read_file, write_file

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

        # Count total versions per path for the version_count field
        version_count_by_path: dict[str, int] = {}
        for r in current_rows:
            count_result = await session.execute(
                select(DocumentVersionTable).where(
                    DocumentVersionTable.view_relative_path == r.view_relative_path,
                )
            )
            version_count_by_path[r.view_relative_path] = len(list(count_result.scalars().all()))

    entries = [
        TgFileEntry(
            view_relative_path=r.view_relative_path,
            filename=r.filename,
            size_bytes=r.size_bytes,
            version_count=version_count_by_path.get(r.view_relative_path, 1),
            last_saved_at=r.saved_at,
            last_saved_by=r.saved_by,
            is_drm_wrapped=bool(r.is_drm_wrapped),
        )
        for r in current_rows
    ]
    entries.sort(key=lambda e: e.view_relative_path)
    return entries


# ---------------------------------------------------------------------------
# Bytes IO — reads a specific version's content off NSD
# ---------------------------------------------------------------------------


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
