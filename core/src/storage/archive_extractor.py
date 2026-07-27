"""Archive extraction abstraction — .zip via stdlib zipfile, .7z via py7zr.

Introduced with D-155 (2026-07-26) — archives (.zip, .7z) are treated as
containers only. Callers extract inner entries and process each independently;
outer archive gets no router matching or doc_type classification.

Public API:
  _MAX_COMPRESSED_BYTES   — 300 MB cap on incoming archive size.
  _MAX_DECOMPRESSED_BYTES — 500 MB cap on TOTAL bytes across all entries.
                            Guards against decompression bombs / solid 7z with
                            10x+ compression ratios.
  extract_archive(filename, content) -> ExtractResult
      Returns None-shaped result for non-archive filenames or hard failures;
      caller decides whether to save the outer as opaque + skip inner processing.

Extension gating: only `.zip` uses zipfile path; only `.7z` uses py7zr path.
OOXML docs (.xlsx / .docx / .pptx) are ZIP-formatted internally — DO NOT
treat them as archives here; ZIP-only-by-extension guard prevents the router
bug that dumped `[Content_Types].xml` into the view tree in 2026-07-22.

py7zr is lazy-imported so non-corp deploys that never see 7z files pay no
import cost.
"""
from __future__ import annotations

import io
import logging
import zipfile
from dataclasses import dataclass
from pathlib import PurePosixPath

__all__ = [
    "ExtractResult",
    "ExtractedEntry",
    "extract_archive",
    "is_archive_filename",
    "safe_relative_parts",
    "MAX_COMPRESSED_BYTES",
    "MAX_DECOMPRESSED_BYTES",
]

_log = logging.getLogger(__name__)

MAX_COMPRESSED_BYTES = 300 * 1024 * 1024      # 300 MB (unchanged from ZIP cap)
MAX_DECOMPRESSED_BYTES = 500 * 1024 * 1024    # 500 MB total across entries

_ZIP_MAGIC = b"PK\x03\x04"
_SEVENZ_MAGIC = b"7z\xbc\xaf\x27\x1c"


@dataclass(frozen=True)
class ExtractedEntry:
    """One file extracted from an archive. relative_parts is the folder tree
    within the archive (already zip-slip-sanitized). content is the raw bytes."""
    relative_parts: tuple[str, ...]
    content: bytes


@dataclass(frozen=True)
class ExtractResult:
    """Outcome of an extraction attempt.

    Semantics:
      status='extracted'    entries populated; caller processes each independently.
      status='not_archive'  filename ext not archive; caller treats as regular file.
      status='oversized'    compressed bytes > MAX_COMPRESSED_BYTES; caller
                            saves outer archive only, no extraction.
      status='decompressed_oversized' inner entries exceed MAX_DECOMPRESSED_BYTES
                            in aggregate; caller saves outer only.
      status='password_protected'  archive requires password; caller saves outer only.
      status='bad_archive'  magic present or extension matches but archive is
                            malformed; caller saves outer only.
      status='library_missing'  py7zr not installed and archive is .7z; caller
                            saves outer only.
    """
    status: str
    entries: tuple[ExtractedEntry, ...] = ()
    reason: str = ""


def is_archive_filename(filename: str) -> bool:
    """Extension-only check. Content magic is verified inside extract_archive."""
    lower = (filename or "").lower()
    return lower.endswith(".zip") or lower.endswith(".7z")


def extract_archive(filename: str, content: bytes) -> ExtractResult:
    """Extract archive if extension is .zip or .7z. See ExtractResult for status semantics.

    NEVER raises — all failure modes map to a status value. Empty content or
    missing filename returns 'not_archive'.
    """
    if not filename or not isinstance(content, (bytes, bytearray)):
        return ExtractResult(status="not_archive")
    lower = filename.lower()
    if lower.endswith(".zip"):
        return _extract_zip(filename, bytes(content))
    if lower.endswith(".7z"):
        return _extract_7z(filename, bytes(content))
    return ExtractResult(status="not_archive")


def _extract_zip(filename: str, content: bytes) -> ExtractResult:
    if len(content) < 4 or content[:4] != _ZIP_MAGIC:
        return ExtractResult(
            status="bad_archive",
            reason="zip extension but PK magic bytes absent",
        )
    if len(content) > MAX_COMPRESSED_BYTES:
        return ExtractResult(
            status="oversized",
            reason=f"compressed={len(content)} > cap={MAX_COMPRESSED_BYTES}",
        )
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as e:
        return ExtractResult(
            status="bad_archive",
            reason=f"zipfile.BadZipFile: {str(e)[:120]}",
        )
    entries: list[ExtractedEntry] = []
    total_decompressed = 0
    with zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            # Reject password-protected entries early.
            # zipfile's flag_bits & 0x1 indicates encryption.
            if info.flag_bits & 0x1:
                return ExtractResult(
                    status="password_protected",
                    reason=f"encrypted entry: {info.filename!r}",
                )
            # Decompressed-size projection using info.file_size (declared)
            # BEFORE reading. Prevents allocating for a bomb.
            total_decompressed += info.file_size
            if total_decompressed > MAX_DECOMPRESSED_BYTES:
                return ExtractResult(
                    status="decompressed_oversized",
                    reason=f"declared decompressed total >= {total_decompressed} > cap={MAX_DECOMPRESSED_BYTES}",
                )
            parts = safe_relative_parts(info.filename)
            if parts is None:
                _log.warning(
                    "extract_archive: zip-slip skipped entry=%r file=%s",
                    info.filename, filename,
                )
                continue
            try:
                data = zf.read(info)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "extract_archive: zip read entry=%r file=%s failed: %s: %s",
                    info.filename, filename, type(exc).__name__, str(exc)[:120],
                )
                continue
            entries.append(ExtractedEntry(relative_parts=parts, content=data))
    return ExtractResult(status="extracted", entries=tuple(entries))


def _extract_7z(filename: str, content: bytes) -> ExtractResult:
    if len(content) < 6 or content[:6] != _SEVENZ_MAGIC:
        return ExtractResult(
            status="bad_archive",
            reason="7z extension but 7z magic bytes absent",
        )
    if len(content) > MAX_COMPRESSED_BYTES:
        return ExtractResult(
            status="oversized",
            reason=f"compressed={len(content)} > cap={MAX_COMPRESSED_BYTES}",
        )
    try:
        import py7zr  # lazy import
    except ImportError:
        _log.warning(
            "extract_archive: py7zr not installed; cannot extract .7z (file=%s)",
            filename,
        )
        return ExtractResult(status="library_missing", reason="py7zr not installed")

    try:
        archive = py7zr.SevenZipFile(io.BytesIO(content), mode="r")
    except py7zr.PasswordRequired:
        return ExtractResult(
            status="password_protected",
            reason="7z archive requires password",
        )
    except py7zr.exceptions.Bad7zFile as e:
        return ExtractResult(
            status="bad_archive",
            reason=f"py7zr.Bad7zFile: {str(e)[:120]}",
        )
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(
            status="bad_archive",
            reason=f"{type(exc).__name__}: {str(exc)[:120]}",
        )

    entries: list[ExtractedEntry] = []
    total_decompressed = 0
    try:
        with archive:
            # Declared uncompressed size check before we read into RAM.
            try:
                for info in archive.list():
                    if info.is_directory:
                        continue
                    total_decompressed += (info.uncompressed or 0)
                    if total_decompressed > MAX_DECOMPRESSED_BYTES:
                        return ExtractResult(
                            status="decompressed_oversized",
                            reason=f"declared decompressed total >= {total_decompressed} > cap={MAX_DECOMPRESSED_BYTES}",
                        )
            except Exception:  # noqa: BLE001
                # If the size check itself fails, fall through and rely on
                # readall() memory allocation — worst case we get an OOM,
                # but that's the same failure mode as an unchecked read.
                pass

            # readall() decompresses ALL entries to memory as {name: BytesIO}.
            # Memory footprint is bounded by the total_decompressed check above.
            extracted = archive.readall() or {}
    except py7zr.PasswordRequired:
        # Some 7z archives lazy-check password on read, not on open.
        return ExtractResult(
            status="password_protected",
            reason="7z archive requires password (raised on read)",
        )
    except Exception as exc:  # noqa: BLE001
        return ExtractResult(
            status="bad_archive",
            reason=f"7z read failed: {type(exc).__name__}: {str(exc)[:120]}",
        )

    for name, bio in extracted.items():
        parts = safe_relative_parts(name)
        if parts is None:
            _log.warning(
                "extract_archive: 7z zip-slip-style skip entry=%r file=%s",
                name, filename,
            )
            continue
        try:
            data = bio.read() if hasattr(bio, "read") else bytes(bio or b"")
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "extract_archive: 7z read entry=%r file=%s failed: %s: %s",
                name, filename, type(exc).__name__, str(exc)[:120],
            )
            continue
        entries.append(ExtractedEntry(relative_parts=parts, content=data))

    return ExtractResult(status="extracted", entries=tuple(entries))


def safe_relative_parts(entry_name: str) -> tuple[str, ...] | None:
    """Zip-slip protection. Reject:
      * Absolute paths (leading / or Windows drive letter)
      * Any `..` segment (path escape)
      * Empty component after normalization

    Returns None if unsafe. Otherwise returns folder-tree tuple ready for
    save_view_document's relative_parts arg.
    """
    if not entry_name:
        return None
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
