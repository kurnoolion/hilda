"""HILDA-side documents view — write side per D-150 Chunk 3.

Called by workflow_engine.tasks.inbound_attachment after a successful
attachment routing to persist the file to the tg-scoped view tree.

Ph-1 rules (per architect 2026-07-22):
  * Zip archives auto-extract preserving folder structure (recursive OK).
  * Zip original also saved to the view tree (per architect: keep both).
  * Overwrite of an existing file = new version (save_view_document handles).
  * Default WI (item_type='default') attachments are EXCLUDED from view.
  * Max compressed archive size: MAX_COMPRESSED_BYTES (300 MB).
  * Max decompressed total: MAX_DECOMPRESSED_BYTES (500 MB) per D-155.
  * Zip-slip guard: any entry name with `..` segment or absolute path skipped.

D-155 2026-07-26 — extraction supports both .zip and .7z through
storage.archive_extractor. Outer archive routing/dedup moved to the caller
in workflow_engine.tasks.inbound_attachment.

The writer is best-effort from the router's perspective: any failure logs
a warning and does not fail the outer routing task. Attachment routing is
authoritative for tracking; view tree is a UX layer.
"""
from __future__ import annotations

import logging

from core.src.storage.archive_extractor import (
    MAX_COMPRESSED_BYTES,
    MAX_DECOMPRESSED_BYTES,
    extract_archive,
    is_archive_filename,
    safe_relative_parts,
)
from core.src.storage.document_view_ops import save_view_document

__all__ = [
    "write_attachment_to_view_tree",
    "MAX_ZIP_SIZE_BYTES",
    "MAX_COMPRESSED_BYTES",
    "MAX_DECOMPRESSED_BYTES",
]

_log = logging.getLogger(__name__)

# Kept as legacy alias so callers/tests importing the old name still resolve.
MAX_ZIP_SIZE_BYTES = MAX_COMPRESSED_BYTES


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
      * filename is an archive (.zip / .7z): save the original at TG root
        (audit / re-download), then extract each entry preserving folders
        and save independently. Zip-slip skipped, password/oversize/bad
        archive keep the original only.
      * filename is not an archive: save once at `<filename>`.

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

    # Non-archive branch: single save. Filenames MAY carry subdir path
    # (e.g. "subfolder/report.pdf") when the caller synthesized the
    # attachment from an archive inner entry (D-155 2026-07-26). Split on
    # `/` so save_view_document receives proper folder-tree parts; single-
    # segment filenames pass through as before. safe_relative_parts also
    # zip-slip-sanitizes any accidental `..` in the path.
    if not is_archive_filename(filename):
        parts = safe_relative_parts(filename) or (filename,)
        try:
            row = await save_view_document(
                customer_id=customer_id, device_id=device_id,
                milestone_id=milestone_id, tg_name=tg_name,
                relative_parts=parts,
                content=content, saved_by=saved_by, source="router",
            )
            written.append(row.view_relative_path)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "view_tree_writer: save failed for %s: %s: %s",
                filename, type(exc).__name__, str(exc)[:120],
            )
        return written

    # Archive branch: always save the outer archive first (audit + re-download).
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
            "view_tree_writer: save original archive failed for %s: %s: %s",
            filename, type(exc).__name__, str(exc)[:120],
        )
        # continue with extraction anyway — outer save is best-effort.

    # Extract inner entries via shared extractor abstraction.
    result = extract_archive(filename, content)
    if result.status != "extracted":
        _log.warning(
            "view_tree_writer: archive extraction skipped (file=%s status=%s reason=%s) "
            "-- kept original only",
            filename, result.status, result.reason,
        )
        return written

    for entry in result.entries:
        try:
            row = await save_view_document(
                customer_id=customer_id, device_id=device_id,
                milestone_id=milestone_id, tg_name=tg_name,
                relative_parts=entry.relative_parts,
                content=entry.content, saved_by=saved_by, source="archive_extract",
            )
            written.append(row.view_relative_path)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "view_tree_writer: save archive entry failed entry=%r file=%s: %s: %s",
                "/".join(entry.relative_parts), filename,
                type(exc).__name__, str(exc)[:120],
            )

    return written


# Legacy shim — some callers/tests may import this. Delegates to shared helper.
def _safe_relative_parts(entry_name: str) -> tuple[str, ...] | None:
    return safe_relative_parts(entry_name)
