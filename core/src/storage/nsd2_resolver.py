"""NSD2 device-folder path resolver -- NSD2-1 (2026-08-08).

Given a DeliveryItem, compute the absolute NSD2 folder path where its
documents live. Called by the NSD2 poller (nsd2_poll_task) before it
walks the tree and feeds files to the attachment router.

Folder layout for NSD2 (per architect 2026-08-08):

    <nsd2_root>/                                <- e.g., /mnt/nsd2 (host) or \\105.52.100.215\Share Folder2 (UNC)
    +-- Deliverables - Phone/                   <- item.handset=True
    |   +-- A/                                  <- device_id starts with 'SM-A'
    |   |   +-- <direct sub-folder>/            <- name contains SM-stripped device_id (e.g., 'A015V')
    |   +-- S/                                  <- device_id starts with 'SM-S'
    |   +-- Flip,Fold/                          <- device_id starts with 'SM-F'
    |   +-- X Cover/                            <- device_id starts with 'SM-G'
    +-- Deliverables - Tablet/                  <- item.tablet=True
    |   +-- <recursive search>/                 <- ANY depth; folder name contains SM-stripped code
    +-- Deliverables - Watch/                   <- item.wearable=True
        +-- <recursive search>/                 <- ANY depth; folder name contains SM-stripped code

Never raises. All misses (device_type unset, model_type unmapped,
folder not found on disk) return None with a `NSD2_RESOLVE:`-tagged
WARN log so the poller can skip cleanly + observability catches
misconfigurations.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import Any, Iterator

__all__ = [
    "resolve_nsd2_device_folder",
    "strip_sm_prefix",
    "walk_nsd2_directory",
    "is_excluded_folder_name",
    "DEVICE_TYPE_FOLDER_MAP",
    "PHONE_MODEL_TYPE_FOLDER_MAP",
    "MMK_EXCLUDED_FOLDER_SUBSTRINGS",
    "EXCLUSION_CARRIERS",
    "NSD2_DEFAULT_MAX_FILE_BYTES",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants -- device-type + phone-model-type folder-name maps
# ---------------------------------------------------------------------------


# item.<flag> True -> device_type folder name under nsd2_root
DEVICE_TYPE_FOLDER_MAP: dict[str, str] = {
    "handset":  "Deliverables - Phone",
    "tablet":   "Deliverables - Tablet",
    "wearable": "Deliverables - Watch",
}


# device_id prefix letter (after 'SM-') -> model_type folder name (Phone only).
# Extend here when a new Samsung family lands in the NSD2 tree; unknown
# prefixes return None so the poller logs + skips gracefully.
PHONE_MODEL_TYPE_FOLDER_MAP: dict[str, str] = {
    "A": "A",
    "S": "S",
    "F": "Flip,Fold",
    "G": "X Cover",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def strip_sm_prefix(device_id: str) -> str:
    """Return device_id with the leading 'SM-' stripped (case-insensitive).
    Substring-match against on-disk folder names uses this stripped form.
    e.g. 'SM-A015V' -> 'A015V', 'SM-T307U' -> 'T307U'. Passes through
    unrecognized shapes unchanged."""
    s = (device_id or "").strip()
    if s[:3].upper() == "SM-":
        return s[3:]
    return s


def _device_type_folder(item: Any) -> str | None:
    """Pick exactly one of Phone/Tablet/Watch by checking item.handset /
    item.tablet / item.wearable. Return None + WARN when zero or more
    than one flag is True (indeterminate)."""
    matches: list[str] = []
    for attr, folder in DEVICE_TYPE_FOLDER_MAP.items():
        if bool(getattr(item, attr, False)):
            matches.append(folder)
    if len(matches) == 1:
        return matches[0]
    _log.warning(
        "NSD2_RESOLVE: device_type ambiguous for item=%s handset=%s tablet=%s "
        "wearable=%s (matched %d folders); skipping",
        getattr(item, "delivery_item_id", "?"),
        bool(getattr(item, "handset", False)),
        bool(getattr(item, "tablet", False)),
        bool(getattr(item, "wearable", False)),
        len(matches),
    )
    return None


def _phone_model_type_folder(device_id: str) -> str | None:
    """Phone only: infer model_type folder from device_id first letter
    after the 'SM-' prefix. e.g. 'SM-A015V' -> 'A' -> 'A' folder;
    'SM-F721U' -> 'F' -> 'Flip,Fold' folder. Unknown prefix -> None + WARN."""
    stripped = strip_sm_prefix(device_id)
    if not stripped:
        _log.warning(
            "NSD2_RESOLVE: device_id %r has no content after SM- strip; cannot "
            "infer phone model_type", device_id,
        )
        return None
    prefix = stripped[0].upper()
    folder = PHONE_MODEL_TYPE_FOLDER_MAP.get(prefix)
    if folder is None:
        _log.warning(
            "NSD2_RESOLVE: device_id %r has phone prefix %r not in "
            "PHONE_MODEL_TYPE_FOLDER_MAP=%s; cannot infer model_type",
            device_id, prefix, sorted(PHONE_MODEL_TYPE_FOLDER_MAP.keys()),
        )
    return folder


def _find_direct_subfolder_matching(parent: Path, substring: str) -> Path | None:
    """One-level scan (non-recursive). Returns the first sub-folder of
    `parent` whose name contains `substring` (case-insensitive). None
    if parent doesn't exist or no match."""
    if not parent.is_dir():
        return None
    needle = substring.lower()
    for child in parent.iterdir():
        if child.is_dir() and needle in child.name.lower():
            return child
    return None


def _find_recursive_folder_matching(root: Path, substring: str) -> Path | None:
    """Arbitrary-depth scan. Returns the first folder under `root` (at
    any depth) whose name contains `substring` (case-insensitive).
    None if root doesn't exist or no match. Uses os.walk-style traversal
    via Path.rglob for consistent Unix + Windows behavior."""
    if not root.is_dir():
        return None
    needle = substring.lower()
    for candidate in root.rglob("*"):
        if candidate.is_dir() and needle in candidate.name.lower():
            return candidate
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def resolve_nsd2_device_folder(item: Any, nsd2_root: Path) -> Path | None:
    """Resolve the NSD2 folder holding documents for `item`. Returns
    absolute Path when every hop resolves; None + WARN on any miss.

    Traversal by device_type:
      * Phone   -> <root>/Deliverables - Phone/<model_type>/<direct sub-folder>
      * Tablet  -> <root>/Deliverables - Tablet/<recursive folder>
      * Watch   -> <root>/Deliverables - Watch/<recursive folder>

    For Phone the model-type folder is chosen from the device_id's
    prefix letter after 'SM-'; the leaf model folder is a DIRECT child
    whose name contains the SM-stripped device_id (e.g., 'A015V' in
    a folder named 'A015V (A01)').

    For Tablet + Watch there is no model_type mid-folder; the leaf is
    found by RECURSIVE search under the device-type folder for a folder
    whose name contains the SM-stripped device_id."""
    delivery_item_id = getattr(item, "delivery_item_id", "?")
    device_id = getattr(item, "device_id", "") or ""
    if not device_id:
        _log.warning(
            "NSD2_RESOLVE: item=%s has empty device_id; cannot resolve NSD2 folder",
            delivery_item_id,
        )
        return None

    device_type_folder = _device_type_folder(item)
    if device_type_folder is None:
        return None  # already WARN-logged in helper

    device_type_path = nsd2_root / device_type_folder
    if not device_type_path.is_dir():
        _log.warning(
            "NSD2_RESOLVE: device_type folder missing on disk root=%s "
            "device_type=%r item=%s device=%s",
            nsd2_root, device_type_folder, delivery_item_id, device_id,
        )
        return None

    stripped = strip_sm_prefix(device_id)

    if device_type_folder == "Deliverables - Phone":
        model_type = _phone_model_type_folder(device_id)
        if model_type is None:
            return None
        model_type_path = device_type_path / model_type
        if not model_type_path.is_dir():
            _log.warning(
                "NSD2_RESOLVE: model_type folder missing on disk parent=%s "
                "model_type=%r item=%s device=%s",
                device_type_path, model_type, delivery_item_id, device_id,
            )
            return None
        leaf = _find_direct_subfolder_matching(model_type_path, stripped)
        if leaf is None:
            _log.warning(
                "NSD2_RESOLVE: no direct sub-folder under %s contains %r "
                "item=%s device=%s",
                model_type_path, stripped, delivery_item_id, device_id,
            )
            return None
        _log.info(
            "NSD2_RESOLVE: item=%s device=%s -> %s",
            delivery_item_id, device_id, leaf,
        )
        return leaf

    # Tablet or Watch: recursive search under device_type folder
    leaf = _find_recursive_folder_matching(device_type_path, stripped)
    if leaf is None:
        _log.warning(
            "NSD2_RESOLVE: no recursive folder under %s contains %r "
            "item=%s device=%s",
            device_type_path, stripped, delivery_item_id, device_id,
        )
        return None
    _log.info(
        "NSD2_RESOLVE: item=%s device=%s -> %s",
        delivery_item_id, device_id, leaf,
    )
    return leaf


# ---------------------------------------------------------------------------
# NSD2-2 (2026-08-08): recursive walker + carrier-scoped exclusion filter
# ---------------------------------------------------------------------------


# Carrier-specific excluded subfolder substrings. Applied when the poller's
# customer_id is in EXCLUSION_CARRIERS. Substring match on the folder NAME
# (not full path); case-insensitive; matches at ANY depth in the tree.
# Motivating rule (architect 2026-08-08): under MMK (Verizon), NSD2 tree
# also holds folders for other carriers/customers that HILDA must not
# ingest. Names carry the tell-tale carrier codes.
MMK_EXCLUDED_FOLDER_SUBSTRINGS: tuple[str, ...] = (
    "CCT", "CHA", "DISH", "DSH", "TFN", "STG",
    "Comcast", "Charter", "Tracfone", "VZW SE", "Strategic",
)

EXCLUSION_CARRIERS: frozenset[str] = frozenset({"MMK"})

# Per-file size cap. Files larger than this are skipped + WARN-logged
# rather than pulled into memory. 500 MB matches the archive-extractor
# total-decompressed cap; individual owner-uploaded documents this large
# are pathological and worth blocking regardless. Configurable via the
# `max_file_bytes` kwarg on walk_nsd2_directory (NSD2-5 wires the config
# knob).
NSD2_DEFAULT_MAX_FILE_BYTES: int = 500 * 1024 * 1024   # 500 MB


def is_excluded_folder_name(folder_name: str, customer_id: str) -> bool:
    """Return True when this folder should be skipped for the given
    customer_id. Only carriers in EXCLUSION_CARRIERS get filtered;
    everyone else passes through. Case-insensitive substring match.
    """
    if customer_id not in EXCLUSION_CARRIERS:
        return False
    lowered = folder_name.lower()
    return any(needle.lower() in lowered for needle in MMK_EXCLUDED_FOLDER_SUBSTRINGS)


def walk_nsd2_directory(
    root: Path,
    customer_id: str,
    *,
    max_file_bytes: int = NSD2_DEFAULT_MAX_FILE_BYTES,
) -> Iterator[tuple[str, bytes, str]]:
    """Recursively walk `root`, yielding every file that survives filtering.

    Filters:
      * Excluded subfolders (per `is_excluded_folder_name`) are PRUNED
        entirely -- their contents never surface.
      * Files larger than `max_file_bytes` skipped + WARN-logged.
      * Unreadable files (permission, transient SMB error) skipped +
        WARN-logged; the walk continues.

    Yields (relative_path_str, file_bytes, sha256_hex) for each file,
    where relative_path is expressed with forward-slash separators
    relative to `root` (matches the ZIP-1 / NEST-1 pattern of
    'subdir1/subdir2/leaf.pdf' filename convention so the attachment
    router treats the file as if it were an extracted inner-archive
    entry).

    Never raises. If `root` doesn't exist or is not a directory, yields
    nothing (WARN once).
    """
    if not root.is_dir():
        _log.warning(
            "NSD2_WALK: root does not exist or is not a directory: %s "
            "(customer=%s)",
            root, customer_id,
        )
        return

    yielded = 0
    skipped_excluded = 0
    skipped_oversized = 0
    skipped_unreadable = 0
    # Use os.walk-style traversal via manual recursion so we can PRUNE
    # excluded subtrees before recursing into them (rglob doesn't prune).
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            children = list(current.iterdir())
        except (OSError, PermissionError) as exc:
            _log.warning(
                "NSD2_WALK: cannot list %s: %s: %s -- skipping subtree",
                current, type(exc).__name__, str(exc)[:120],
            )
            continue
        for child in children:
            try:
                if child.is_dir():
                    if is_excluded_folder_name(child.name, customer_id):
                        # Prune whole subtree
                        skipped_excluded += 1
                        _log.info(
                            "NSD2_WALK: pruned excluded subfolder %s "
                            "(customer=%s)",
                            child, customer_id,
                        )
                        continue
                    stack.append(child)
                    continue
                if not child.is_file():
                    continue  # symlink, socket, etc.
                # File — size check first (avoid reading giant files)
                try:
                    size = child.stat().st_size
                except (OSError, PermissionError) as exc:
                    _log.warning(
                        "NSD2_WALK: cannot stat %s: %s: %s",
                        child, type(exc).__name__, str(exc)[:120],
                    )
                    skipped_unreadable += 1
                    continue
                if size > max_file_bytes:
                    _log.warning(
                        "NSD2_WALK: skipped oversized file %s "
                        "(%d bytes > cap %d)",
                        child, size, max_file_bytes,
                    )
                    skipped_oversized += 1
                    continue
                # Read + hash
                try:
                    data = child.read_bytes()
                except (OSError, PermissionError) as exc:
                    _log.warning(
                        "NSD2_WALK: cannot read %s: %s: %s",
                        child, type(exc).__name__, str(exc)[:120],
                    )
                    skipped_unreadable += 1
                    continue
                sha = hashlib.sha256(data).hexdigest()
                rel = child.relative_to(root).as_posix()
                yielded += 1
                yield (rel, data, sha)
            except Exception as exc:  # noqa: BLE001 -- last-resort defense
                _log.warning(
                    "NSD2_WALK: unexpected error processing %s: %s: %s",
                    child, type(exc).__name__, str(exc)[:120],
                )
                continue

    _log.warning(
        "NSD2_WALK: root=%s customer=%s summary yielded=%d "
        "skipped_excluded=%d skipped_oversized=%d skipped_unreadable=%d",
        root, customer_id, yielded,
        skipped_excluded, skipped_oversized, skipped_unreadable,
    )
