"""UPLOAD-BUNDLE-1 (2026-09-06): download a milestone's submission as a zip.

Reproduces, on the TPM's own machine, the exact directory tree a
Submit-to-Carrier would create on Google Drive -- so placement can be checked
by opening folders rather than by reading a list.

Entry paths in the zip ARE the carrier destinations, taken verbatim from the
manifest. Nothing here recomputes a path: the manifest already resolves them
through storage.upload_plan, the same functions the uploader calls, and
duplicating that logic is how a preview starts lying.

Only files the manifest marks as included are written. Excluded ones (waiver,
archive container, superseded revision, staged, undeliverable item) are
counted and reported, not silently dropped -- their absence from the zip is
the point, and the count is how a TPM notices.

Written to a caller-supplied path rather than assembled in memory: a
milestone can hold gigabytes (a single 338 MB archive already exists in NSD),
and buffering that in the API container would take it down.
"""
from __future__ import annotations

import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

__all__ = [
    "BundleResult",
    "DEFAULT_MAX_BUNDLE_BYTES",
    "build_manifest_zip",
    "estimate_bundle_bytes",
]

_log = logging.getLogger(__name__)

# Refuse to build beyond this. A preview is a convenience; letting a TPM
# accidentally ask the API container to assemble 20 GB is not. Overridable per
# call so a deployment can raise it deliberately.
DEFAULT_MAX_BUNDLE_BYTES: int = 2 * 1024 * 1024 * 1024   # 2 GB


@dataclass
class BundleResult:
    files_written: int = 0
    bytes_written: int = 0
    files_excluded: int = 0
    files_missing: int = 0
    # Two included files resolving to the SAME carrier path. Real: P1 #14 and
    # #20 both point at 'RF Parametric Data/Documentation/FCC Package', so
    # identically-named documents from those items collide on the drive. The
    # zip disambiguates with a numeric suffix; the carrier upload would not,
    # so a non-empty list here is a genuine finding to act on.
    collisions: list[str] = field(default_factory=list)
    oversized: bool = False
    total_estimated_bytes: int = 0


def _local_path(nsd_relative: str) -> Path | None:
    from core.src.storage.nsd import NSDPath
    try:
        return NSDPath.from_relative(nsd_relative).to_local()
    except Exception:  # noqa: BLE001 -- a bad stored path must not abort the run
        return None


def estimate_bundle_bytes(manifest) -> int:
    """Sum on-disk sizes of the files that would be written. Missing files
    count as zero -- they are reported separately rather than guessed at."""
    total = 0
    for item in manifest.items:
        for row in item.rows:
            if row.excluded_reason:
                continue
            p = _local_path(row.source_path)
            if p is not None and p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    return total


def build_manifest_zip(
    manifest,
    out_path: Path,
    *,
    max_bytes: int = DEFAULT_MAX_BUNDLE_BYTES,
) -> BundleResult:
    """Write `manifest`'s included files to `out_path` under their carrier
    paths. Returns counts; never raises for a single unreadable file.

    ZIP_STORED, not deflate: the payload is overwhelmingly pdf/xlsx/docx, all
    already compressed, so deflating spends CPU for almost no size win -- and
    this runs in the API container while a TPM waits.
    """
    result = BundleResult()
    result.total_estimated_bytes = estimate_bundle_bytes(manifest)
    if result.total_estimated_bytes > max_bytes:
        result.oversized = True
        _log.warning(
            "UPLOAD_BUNDLE: refused scope=%s/%s/%s estimated=%d > cap=%d",
            manifest.customer_id, manifest.device_id, manifest.milestone_id,
            result.total_estimated_bytes, max_bytes,
        )
        return result

    seen: dict[str, int] = {}
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for item in manifest.items:
            for row in item.rows:
                if row.excluded_reason:
                    result.files_excluded += 1
                    continue
                src = _local_path(row.source_path)
                if src is None or not src.is_file():
                    result.files_missing += 1
                    _log.warning(
                        "UPLOAD_BUNDLE: source missing item=#%s path=%s",
                        item.item_no, row.source_path,
                    )
                    continue
                arc = row.carrier_destination
                if arc in seen:
                    seen[arc] += 1
                    result.collisions.append(arc)
                    stem, dot, ext = arc.rpartition(".")
                    suffix = f" ({seen[arc]})"
                    arc = (stem + suffix + dot + ext) if dot else arc + suffix
                    _log.warning(
                        "UPLOAD_BUNDLE: destination collision -- two documents "
                        "resolve to %r; zip disambiguates but the carrier "
                        "upload would NOT",
                        row.carrier_destination,
                    )
                else:
                    seen[arc] = 1
                try:
                    zf.write(src, arcname=arc)
                    result.files_written += 1
                    result.bytes_written += src.stat().st_size
                except OSError as exc:
                    result.files_missing += 1
                    _log.warning(
                        "UPLOAD_BUNDLE: read failed path=%s: %s: %s",
                        row.source_path, type(exc).__name__, str(exc)[:120],
                    )

        # A manifest with nothing to write still produces a valid archive
        # carrying the explanation, so the TPM gets an answer either way.
        if result.files_written == 0:
            zf.writestr(
                "README.txt",
                "No documents were resolved for submission in "
                f"{manifest.customer_id}/{manifest.device_id}/"
                f"{manifest.milestone_id}.\n"
                f"{result.files_excluded} file(s) are excluded; open the "
                "Submission preview page to see why.\n",
            )

    _log.warning(
        "UPLOAD_BUNDLE: built scope=%s/%s/%s written=%d bytes=%d excluded=%d "
        "missing=%d collisions=%d",
        manifest.customer_id, manifest.device_id, manifest.milestone_id,
        result.files_written, result.bytes_written, result.files_excluded,
        result.files_missing, len(result.collisions),
    )
    return result
