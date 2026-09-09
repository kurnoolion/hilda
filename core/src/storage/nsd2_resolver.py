r"""NSD2 device-folder path resolver -- NSD2-1 (2026-08-08).

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
import re
from pathlib import Path
from typing import Any, Iterator

__all__ = [
    "resolve_nsd2_device_folder",
    "strip_sm_prefix",
    "walk_nsd2_directory",
    "is_excluded_folder_name",
    "is_allowed_root_folder",
    "allowed_root_folders",
    "find_carrier_anchors",
    "NSD2_ANCHOR_SEARCH_MAX_DEPTH",
    "DEVICE_TYPE_FOLDER_MAP",
    "PHONE_MODEL_TYPE_FOLDER_MAP",
    "MMK_EXCLUDED_FOLDER_SUBSTRINGS",
    "EXCLUSION_CARRIERS",
    "CARRIER_ALLOWED_ROOT_FOLDERS",
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


# NSD2-VZW-1 (2026-09-01): carrier -> the ONLY top-level sub-folders of the
# resolved device folder whose contents may be ingested. An entry here turns
# ingestion from a denylist into an allowlist for that carrier, because a
# denylist can only block what someone remembered to enumerate: before this,
# sibling folders like 'ATT', 'TMO' or 'Sprint' passed the filter, and loose
# files sitting directly in the device folder were ingested too (for
# one device that meant a stray workbook plus a 338 MB 'M3 HW
# Deliverables.zip', which the archive extractor then fanned out).
#
# Semantics when a carrier appears here:
#   * depth 0 (direct children of the device folder) -- ONLY folders whose
#     name matches this tuple are descended into. Every other folder, and
#     every loose FILE at this level, is skipped.
#   * depth >= 1 -- no filtering whatsoever. Every file under the allowed
#     folder is yielded at any depth, per architect 2026-09-01: "all files
#     under vzw/ folder are uploaded". The MMK_EXCLUDED_FOLDER_SUBSTRINGS
#     denylist is deliberately NOT applied inside, both because the
#     allowlist has already done its job at the boundary and because that
#     substring match has false positives -- 'CHA' silently prunes
#     'Charging', 'Mechanical', 'Exchange' and 'Chart'.
#
# Matching is case-insensitive and EXACT on the folder name (not substring),
# so 'VZW SE' -- a genuinely different carrier scope -- does not qualify.
CARRIER_ALLOWED_ROOT_FOLDERS: dict[str, tuple[str, ...]] = {
    "MMK": ("VZW", "Verizon"),
}


def allowed_root_folders(customer_id: str) -> tuple[str, ...] | None:
    """Allowed top-level folder names for `customer_id`, or None when the
    carrier has no allowlist (in which case the legacy denylist applies)."""
    return CARRIER_ALLOWED_ROOT_FOLDERS.get(customer_id)


def is_allowed_root_folder(folder_name: str, customer_id: str) -> bool:
    """True when `folder_name` is an allowed top-level folder for this
    carrier. Carriers without an allowlist accept everything (True)."""
    allowed = allowed_root_folders(customer_id)
    if allowed is None:
        return True
    name = (folder_name or "").strip().lower()
    return any(name == a.strip().lower() for a in allowed)

# Per-file size cap. Files larger than this are skipped + WARN-logged
# rather than pulled into memory. 500 MB matches the archive-extractor
# total-decompressed cap; individual owner-uploaded documents this large
# are pathological and worth blocking regardless. Configurable via the
# `max_file_bytes` kwarg on walk_nsd2_directory (NSD2-5 wires the config
# knob).
NSD2_DEFAULT_MAX_FILE_BYTES: int = 500 * 1024 * 1024   # 500 MB


# NSD-DRM-DECRYPT-1 (2026-09-09): DRM-wrapped archives on the NSD share are
# NASCA-encrypted internally -- their contents are garbage until an external
# decrypt step emits a sibling file whose stem contains 'decrypt'
# (e.g. `foo.zip` -> `foo_decrypt.zip`, `bar.7z` -> `bar_decrypt.7z`). Both
# files stay on the share; only the decrypted sibling is worth ingesting.
# Applies to ALL customers -- DRM is not customer-scoped -- and to the archive
# extensions HILDA already opens: .zip, .7z, .rar.
#
# Non-archive files (.pdf, .xlsx, .txt, ...) are unaffected. An archive whose
# stem contains 'decrypt' passes the filter regardless of the source file's
# actual encryption status; the ingest pipeline downstream is responsible
# for the extraction failure if a mislabelled file lies about being decrypted.
_DRM_DECRYPT_STEM_MARKER: str = "decrypt"
_DRM_ARCHIVE_EXTS: tuple[str, ...] = (".zip", ".7z", ".rar")


def _is_drm_wrapped_archive(filename: str) -> bool:
    """Return True when `filename` is an NSD-share archive whose stem does
    NOT contain 'decrypt' (case-insensitive) -- i.e. the pre-decrypt DRM
    original, whose contents are encrypted. See NSD-DRM-DECRYPT-1 above."""
    lowered = (filename or "").lower()
    if not any(lowered.endswith(ext) for ext in _DRM_ARCHIVE_EXTS):
        return False
    dot = lowered.rfind(".")
    stem = lowered[:dot] if dot > 0 else lowered
    return _DRM_DECRYPT_STEM_MARKER not in stem


def _name_tokens(folder_name: str) -> set[str]:
    """Split a folder name into lowercased alphanumeric tokens.
    'Deliverables - DISH Config' -> {'deliverables', 'dish', 'config'}."""
    return {t for t in re.split(r"[^a-z0-9]+", folder_name.lower()) if t}


def is_excluded_folder_name(folder_name: str, customer_id: str) -> bool:
    """Return True when this folder should be skipped for the given
    customer_id. Only carriers in EXCLUSION_CARRIERS get filtered;
    everyone else passes through. Case-insensitive.

    NSD2-VZW-1 (2026-09-01): single-word needles match a WHOLE TOKEN, not a
    bare substring. The old substring test made 'CHA' (Charter) silently
    prune 'Charging', 'Mechanical', 'Exchange' and 'Chart' along with their
    entire subtrees -- plausible HW-deliverable folder names, dropped with
    no production trace. Multi-word needles ('VZW SE', 'Comcast Overrides'
    shapes) still match as a substring of the full name, since a phrase
    cannot be a single token.
    """
    if customer_id not in EXCLUSION_CARRIERS:
        return False
    lowered = folder_name.lower()
    tokens = _name_tokens(folder_name)
    for needle in MMK_EXCLUDED_FOLDER_SUBSTRINGS:
        n = needle.strip().lower()
        if not n:
            continue
        if _name_tokens(n) != {n}:      # phrase / punctuated -> substring
            if n in lowered:
                return True
        elif n in tokens:               # single token -> whole-word match
            return True
    return False


# How many levels below the device folder to search for a carrier partition
# folder. Known layouts put it at depth 0 (S948U: VZW/) or depth 1 (F776U:
# Deliverable/VZW/); 3 is architect-set (2026-09-01) as comfortably past
# both without scanning deep trees.
#
# The cap bounds scan cost; it is not a safety mechanism, and it does not
# fail safe -- exceeding it silently downgrades to ingesting the WHOLE
# device folder. `find_carrier_anchors` therefore WARNs when the search is
# truncated with folders still unexamined, so a layout that outgrows this
# shows up in the log rather than as another carrier's files in Drive.
NSD2_ANCHOR_SEARCH_MAX_DEPTH: int = 3


def find_carrier_anchors(
    root: Path,
    customer_id: str,
    *,
    max_depth: int = NSD2_ANCHOR_SEARCH_MAX_DEPTH,
) -> tuple[list[Path], bool]:
    """Locate the carrier partition folder(s) under `root`.

    Breadth-first, level by level, stopping at the SHALLOWEST level that
    contains an allowlisted folder -- so a genuine 'VZW/' at depth 0 always
    wins over anything deeper. Denylisted folders are never descended into,
    so a 'VZW' nested inside 'STG/' can never become an anchor.

    Returns `(anchors, saw_partition_marker)`:
      * `anchors` -- every allowlisted folder at that shallowest level.
        Empty when the carrier has no allowlist or nothing matched.
      * `saw_partition_marker` -- True when a DENYLISTED folder was seen
        during the search. This distinguishes "partitioned, but this device
        has no VZW folder" (ingest nothing) from "flat device folder, no
        carrier partitions anywhere" (ingest everything).
    """
    allowed = allowed_root_folders(customer_id)
    if allowed is None:
        return [], False

    saw_marker = False
    level: list[Path] = [root]
    for _depth in range(max_depth + 1):
        if not level:
            break
        anchors: list[Path] = []
        next_level: list[Path] = []
        for parent in level:
            try:
                children = [c for c in parent.iterdir() if c.is_dir()]
            except (OSError, PermissionError):
                continue
            for child in children:
                if is_allowed_root_folder(child.name, customer_id):
                    anchors.append(child)
                elif is_excluded_folder_name(child.name, customer_id):
                    # A partition marker, and never descended into.
                    saw_marker = True
                else:
                    next_level.append(child)
        if anchors:
            return sorted(anchors), True
        level = next_level
    if level:
        # Ran out of depth with folders still unexamined. Distinguishes
        # "this device folder genuinely has no carrier folder" from "we
        # stopped looking too early" -- the latter silently downgrades to
        # ingesting the whole device folder, so it must be visible.
        _log.warning(
            "NSD2_WALK: anchor search hit the depth cap (%d) under %s with "
            "%d folder(s) still unexamined (customer=%s). If a %s folder "
            "exists deeper, raise NSD2_ANCHOR_SEARCH_MAX_DEPTH.",
            max_depth, root, len(level), customer_id, list(allowed),
        )
    return [], saw_marker


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
    skipped_drm_wrapped = 0

    # NSD2-VZW-1: three real layouts exist under the same NSD2 tree --
    #   S948U (M3)     -> VZW/ + STG/ + loose files      (partition at depth 0)
    #   F776U (Filp8)  -> Deliverable/VZW/...            (partition at depth 1)
    #   S731U (S25 FE) -> '1. HW Release notes(done)/'   (no partition at all)
    # so the carrier folder is located by search, not assumed to be a direct
    # child. When found, it becomes the ONLY ingest root and nothing below it
    # is filtered. When the device folder has no carrier partition anywhere,
    # gating on 'VZW' would ingest nothing, so we fall back to the legacy
    # denylist walk -- loudly, because that fallback is a guess and the
    # HW PL TG needs a worklist of folders to normalise.
    allowed = allowed_root_folders(customer_id)
    anchors: list[Path] = []
    if allowed is not None:
        anchors, saw_marker = find_carrier_anchors(root, customer_id)
        if anchors:
            _log.warning(
                "NSD2_WALK: carrier anchor(s) for %s -> %s (customer=%s) -- "
                "ingesting only these subtrees",
                root, [a.relative_to(root).as_posix() for a in anchors],
                customer_id,
            )
        else:
            # No carrier folder anywhere in range. Fall back to the legacy
            # denylist walk -- deliberately NOT "ingest nothing", even when a
            # foreign-carrier folder was seen: that signal is weak (a stray
            # 'DISH Config/' deep inside a deliverable folder does not make
            # the device folder carrier-partitioned) and silently ingesting
            # nothing is the worse failure. Known foreign-carrier folders are
            # still pruned by the denylist on this path.
            _log.warning(
                "NSD2_WALK: NONCONFORMING LAYOUT -- %s has no %s folder within "
                "%d levels (customer=%s foreign_carrier_folders_seen=%s). "
                "Falling back to ingesting the whole device folder. Ask the "
                "HW PL TG to place deliverables under a carrier folder.",
                root, list(allowed), NSD2_ANCHOR_SEARCH_MAX_DEPTH,
                customer_id, saw_marker,
            )
            allowed = None  # legacy denylist walk

    gated = bool(anchors)
    # Use os.walk-style traversal via manual recursion so we can PRUNE
    # excluded subtrees before recursing into them (rglob doesn't prune).
    # In gated mode the walk is seeded from the anchors, so everything
    # outside them is unreachable rather than filtered.
    stack: list[tuple[Path, int]] = (
        [(a, 1) for a in anchors] if gated else [(root, 0)]
    )
    while stack:
        current, depth = stack.pop()
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
                    # Inside an anchor nothing is filtered: architect
                    # 2026-09-01, "all files under vzw/ folder are uploaded".
                    if not gated and is_excluded_folder_name(
                        child.name, customer_id
                    ):
                        # Prune whole subtree. WARNING not INFO: the deployed
                        # containers run at WARNING, so an INFO line here made
                        # dropped files invisible in production.
                        skipped_excluded += 1
                        _log.warning(
                            "NSD2_WALK: pruned excluded subfolder %s "
                            "(customer=%s)",
                            child, customer_id,
                        )
                        continue
                    stack.append((child, depth + 1))
                    continue
                if not child.is_file():
                    continue  # symlink, socket, etc.
                # NSD-DRM-DECRYPT-1: skip archives that lack the 'decrypt'
                # stem marker BEFORE stat/read -- the encrypted body is not
                # worth pulling into memory even to reject it. WARN so the
                # skip is visible in production (WARNING is the deployed
                # containers' root level; INFO would be silent).
                if _is_drm_wrapped_archive(child.name):
                    _log.warning(
                        "NSD2_WALK: skipped DRM-wrapped archive %s "
                        "(stem lacks 'decrypt' marker; expecting sibling "
                        "`<stem>_decrypt.<ext>` from the DRM decrypt step) "
                        "customer=%s",
                        child, customer_id,
                    )
                    skipped_drm_wrapped += 1
                    continue
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
        "skipped_excluded=%d skipped_oversized=%d skipped_unreadable=%d "
        "skipped_drm_wrapped=%d anchors=%s mode=%s",
        root, customer_id, yielded,
        skipped_excluded, skipped_oversized, skipped_unreadable,
        skipped_drm_wrapped,
        [a.relative_to(root).as_posix() for a in anchors] if anchors else "-",
        "anchored" if gated else "whole-device-folder",
    )
