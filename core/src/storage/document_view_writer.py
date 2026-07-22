"""HILDA-side documents view — write side per D-150 Chunk 3.

Called by workflow_engine.tasks.inbound_attachment after a successful
attachment routing to persist the file to the tg-scoped view tree.

Ph-1 rules (per architect 2026-07-22):
  * Zip archives auto-extract preserving folder structure (recursive OK).
  * Zip original also saved to the view tree (per architect: keep both).
  * Overwrite of an existing file = new version (save_view_document handles).
  * Default WI (item_type='default') attachments are EXCLUDED from view.
  * Max zip size: 300MB.
  * Zip-slip guard: any entry name with `..` segment or absolute path skipped.

The writer is best-effort from the router's perspective: any failure logs
a warning and does not fail the outer routing task. Attachment routing is
authoritative for tracking; view tree is a UX layer.
"""
from __future__ import annotations

import io
import logging
import zipfile
from pathlib import PurePosixPath

from core.src.storage.document_view_ops import save_view_document

__all__ = ["write_attachment_to_view_tree", "MAX_ZIP_SIZE_BYTES"]

_log = logging.getLogger(__name__)


MAX_ZIP_SIZE_BYTES = 300 * 1024 * 1024  # 300 MB — architect cap 2026-07-22
_ZIP_MAGIC = b"PK\x03\x04"


async def write_attachment_to_view_tree(
    *,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    tg_name: str,
    item_type: str,
    filename: str,
    content: bytes,
    saved_by: str = "auto",
) -> list[str]:
    """Persist an attachment to the tg-scoped view tree per D-150.

    Behavior:
      * item_type == 'default' -> skip entirely (view excludes Default WI docs).
      * tg_name empty / None -> skip (no destination tg directory).
      * content is a zip (magic-byte detect):
         - Store the original zip as `<filename>` (audit / re-download).
         - Extract each entry preserving folders; skip zip-slip attempts.
         - Save each extracted entry via save_view_document (versioning if
           the same relative path already exists).
      * content is not a zip: save once at `<filename>`.

    Returns list of view_relative_paths written (empty if skipped). Best-effort
    on all failure modes -- a warning is logged for skips.
    """
    if (item_type or "").lower() == "default":
        return []
    if not tg_name:
        _log.info(
            "view_tree_writer: skip empty tg_name (customer=%s device=%s milestone=%s file=%s)",
            customer_id, device_id, milestone_id, filename,
        )
        return []
    written: list[str] = []

    is_zip = content[:4] == _ZIP_MAGIC if len(content) >= 4 else False

    if not is_zip:
        try:
            row = await save_view_document(
                customer_id=customer_id, device_id=device_id,
                milestone_id=milestone_id, tg_name=tg_name,
                relative_parts=(filename,),
                content=content, saved_by=saved_by, source="router",
            )
            written.append(row.view_relative_path)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "view_tree_writer: save failed for %s: %s: %s",
                filename, type(exc).__name__, str(exc)[:120],
            )
        return written

    # Zip path -- size cap first (before decompression)
    if len(content) > MAX_ZIP_SIZE_BYTES:
        _log.warning(
            "view_tree_writer: zip exceeds cap %d>%d bytes (file=%s) -- skipping extraction; "
            "saving original zip only",
            len(content), MAX_ZIP_SIZE_BYTES, filename,
        )
        try:
            row = await save_view_document(
                customer_id=customer_id, device_id=device_id,
                milestone_id=milestone_id, tg_name=tg_name,
                relative_parts=(filename,),
                content=content, saved_by=saved_by, source="router",
            )
            written.append(row.view_relative_path)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "view_tree_writer: save oversized zip failed for %s: %s: %s",
                filename, type(exc).__name__, str(exc)[:120],
            )
        return written

    # Save the original zip first (preserved for audit + re-download)
    try:
        orig = await save_view_document(
            customer_id=customer_id, device_id=device_id,
            milestone_id=milestone_id, tg_name=tg_name,
            relative_parts=(filename,),
            content=content, saved_by=saved_by, source="router",
        )
        written.append(orig.view_relative_path)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "view_tree_writer: save original zip failed for %s: %s: %s",
            filename, type(exc).__name__, str(exc)[:120],
        )
        # continue with extraction anyway

    # Extract + save each entry
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        _log.warning(
            "view_tree_writer: zip magic present but archive is malformed (file=%s) -- "
            "kept original only, no extraction",
            filename,
        )
        return written

    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            entry_parts = _safe_relative_parts(info.filename)
            if entry_parts is None:
                _log.warning(
                    "view_tree_writer: zip-slip attempt skipped: entry=%r file=%s",
                    info.filename, filename,
                )
                continue
            try:
                entry_bytes = zf.read(info)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "view_tree_writer: zip entry read failed entry=%r file=%s: %s: %s",
                    info.filename, filename, type(exc).__name__, str(exc)[:120],
                )
                continue
            try:
                row = await save_view_document(
                    customer_id=customer_id, device_id=device_id,
                    milestone_id=milestone_id, tg_name=tg_name,
                    relative_parts=entry_parts,
                    content=entry_bytes, saved_by=saved_by, source="zip_extract",
                )
                written.append(row.view_relative_path)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "view_tree_writer: save zip entry failed entry=%r file=%s: %s: %s",
                    info.filename, filename, type(exc).__name__, str(exc)[:120],
                )

    return written


def _safe_relative_parts(entry_name: str) -> tuple[str, ...] | None:
    """Zip-slip protection. Reject:
      * Absolute paths (leading / or Windows drive letter)
      * Any `..` segment (path escape)
      * Empty component after normalization

    Returns None if unsafe. Otherwise returns the folder-tree tuple ready
    for save_view_document's relative_parts arg.
    """
    if not entry_name:
        return None
    # Normalize windows separators
    normalized = entry_name.replace("\\", "/")
    p = PurePosixPath(normalized)
    if p.is_absolute():
        return None
    parts = [seg for seg in p.parts if seg not in ("", ".")]
    if not parts:
        return None
    if any(seg == ".." for seg in parts):
        return None
    return tuple(parts)
