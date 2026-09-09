"""Carrier-destination path resolution, shared by the uploader and the UI.

UPLOAD-PLAN-1 (2026-09-06). Everything here answers one question: given a
resolved upload file, WHERE on the carrier does it land?

    <target_folder>[/<subdir>]/<filename>

Extracted verbatim from `workflow_engine.tasks.submit_to_carrier`, which owned
this logic privately. It moved because three consumers now need the same
answer and must not drift apart:

  * submit_to_carrier -- performs the upload
  * the TG document view -- shows the destination per document
  * the download-all preview -- lets a TPM check placement BEFORE submitting

A preview that computes paths differently from the uploader is worse than no
preview, because it manufactures confidence. That failure already happened
once in a different guise: DOCTYPE-MISALIGN-UI-1 (2026-09-03) found the TG
view calling a document classified while `list_upload_files_for_item` was
silently dropping it from the submission, because the two read different
predicates. Same class of bug, so the same remedy -- one function, three
callers.

This module's own imports are stdlib-only (pathlib), so the dashboard can use
it without pulling in Celery. Note that importing it still runs
core.src.storage.__init__, which does bring SQLAlchemy -- unavoidable, and
already true of the dashboard via document_view_ops. Celery is the dependency
that would genuinely be new, and an AST test keeps the import list stdlib so
this can be relocated out of core.src.storage later if that matters.
"""
from __future__ import annotations

from pathlib import PurePosixPath

__all__ = [
    "ARCHIVE_EXTS",
    "carrier_subdir",
    "effective_target_dir",
    "plm_subdir_prefix_from_local_path",
    "sanitize_subdir_segment",
    "view_subdir_prefix",
]


# UPLOAD-SUBDIR-PLM-1 (2026-08-27): archive-container segments to strip from a
# subdir prefix. Compared lowercased against segment suffixes so `report.zip`,
# `data.7z` and `foo.RAR` all trigger. Extend when a new archive type gets
# first-class ingest support.
ARCHIVE_EXTS: tuple[str, ...] = (".zip", ".7z", ".rar")

# Path-hostile characters replaced defensively before a subdir reaches the
# carrier binding. Backslashes become underscores so Windows-authored archives
# cannot make the drive binding mis-parse; control characters are stripped.
# Spaces and Unicode letters are preserved -- an "i am c" folder must survive
# intact.
_SUBDIR_HOSTILE_CHARS: tuple[str, ...] = ("\\",)


def sanitize_subdir_segment(seg: str) -> str:
    """Defensive cleanup of one subdir segment per UPLOAD-SUBDIR-PLM-1 #3.

    Backslashes -> underscore, control characters dropped, trailing dots and
    spaces stripped (hostile on Windows filesystems). Everything else,
    including spaces and non-ASCII letters, survives verbatim.
    """
    result = seg
    for ch in _SUBDIR_HOSTILE_CHARS:
        result = result.replace(ch, "_")
    result = "".join(c for c in result if ord(c) >= 32)
    return result.rstrip(". ")


def plm_subdir_prefix_from_local_path(local_nsd_path: str) -> str:
    """UPLOAD-SUBDIR-PLM-1 (2026-08-27) -- subdir for an INTERNAL-tree file.

    Anchors at the `rev<N>` or `_staged_classification` segment and keeps
    everything after it except the basename, dropping archive-container
    segments. Used only on the internal-tree fallback path, where no view-tree
    row exists for the document.

    Examples:
      internal/.../rev1/a.pdf                                     -> ''
      internal/.../rev1/b.zip/i am c/d.pdf                        -> 'i am c'
      internal/.../rev1/outer.zip/inner.zip/x/y.pdf               -> 'x'
      internal/.../rev1/report.7z/folder/nested/file.pdf          -> 'folder/nested'
      internal/.../_staged_classification/report.zip/folder/x.pdf -> 'folder'
    """
    parts = PurePosixPath(local_nsd_path).parts
    if not parts:
        return ""
    root_idx = -1
    for i, seg in enumerate(parts):
        if seg.startswith("rev") or seg == "_staged_classification":
            root_idx = i
            break
    if root_idx < 0 or root_idx >= len(parts) - 1:
        return ""
    return _join_clean(parts[root_idx + 1 : -1])


def view_subdir_prefix(view_relative_path: str) -> str:
    """UPLOAD-VIEW-1 (2026-08-30) -- subdir for a VIEW-tree file.

    The view tree is `view/<customer>/<device>/<milestone>/<tg>/<*parts>`, and
    the view writer already materialised each archive's internal folders as
    real directories under the TG root, so the subdir is everything after the
    5-segment scope prefix minus the basename.

    Note the tree is TG-scoped with NO item segment -- which is why moving a
    document between work-items inside one TG leaves this value unchanged.

    Archive-name segments are stripped. NEST-1 prefixes a nested archive's
    entries with the archive's own filename, so `outer.zip` -> `b.zip` ->
    `i am c/d.pdf` reaches the view tree as `.../b.zip/i am c/d.pdf`; the
    carrier should see `i am c/d.pdf`, since container names are not part of
    the delivered structure.

    Examples (scope prefix elided):
      <tg>/a.pdf                         -> ''
      <tg>/i am c/d.pdf                  -> 'i am c'
      <tg>/b.zip/i am c/d.pdf            -> 'i am c'
      <tg>/report.7z/folder/nested/x.pdf -> 'folder/nested'
    """
    parts = PurePosixPath(view_relative_path).parts
    # Need scope (5) + at least a basename; anything shorter has no subdir.
    if len(parts) < 6 or parts[0] != "view":
        return ""
    return _join_clean(parts[5:-1])


def _join_clean(segments) -> str:
    """Drop archive-container segments, sanitize the rest, join with '/'."""
    kept: list[str] = []
    for seg in segments:
        if any(seg.lower().endswith(ext) for ext in ARCHIVE_EXTS):
            continue
        clean = sanitize_subdir_segment(seg)
        if clean:
            kept.append(clean)
    return "/".join(kept)


def carrier_subdir(
    *,
    relative_path: str,
    is_view: bool,
    from_zip: bool,
    ingest_source: str = "",
) -> str:
    """The subdir that rides under `target_folder` for one upload file.

    Mirrors the branch that lived inline in submit_to_carrier's item loop:

      * view-tree file  -- subdir ONLY when the document came from an archive.
        UPLOAD-FLAT-1 (2026-08-31): NSD ingest passes the share-relative path
        as the filename, so an ordinary file sitting in an NSD folder also
        carries segments -- and recreating those on the carrier is wrong. The
        path cannot distinguish the two cases on its own (NEST-1 only prefixes
        the archive name at depth >= 1, so a top-level zip's entries look
        identical to NSD folders), so the decision is gated on
        `document_index.from_zip`, written at ingest by the archive path only.
      * internal-tree file -- UPLOAD-SUBDIR-PLM-1 still governs, unchanged and
        PLM-only, so items with no view-tree presence behave exactly as they
        did before UPLOAD-VIEW-1.
    """
    if is_view:
        return view_subdir_prefix(relative_path) if from_zip else ""
    if ingest_source == "CorporatePLM":
        return plm_subdir_prefix_from_local_path(relative_path)
    return ""


def effective_target_dir(target_folder: str, subdir: str) -> str:
    """Compose the carrier directory a file lands in.

    Kept trivial and separate so the override introduced by
    UPLOAD-FOLDER-OVERRIDE-1 has exactly one place to substitute
    `target_folder`, and the subdir keeps riding underneath it either way.
    """
    base = (target_folder or "").rstrip("/")
    if not subdir:
        return base
    return f"{base}/{subdir}"
