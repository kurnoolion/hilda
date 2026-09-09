"""DRRP1-1 (2026-09-01) -- cross-milestone work-item mapping.

Documents collected during one milestone are sometimes the same artifacts a
LATER milestone has to submit. For MMK, deliverables received against DRR
work-items are submitted to the carrier as part of the P1 milestone: DRR itself
never uploads (every mapped DRR item is `no_customer_upload: true` /
`target_folder: null` in template.yaml), so without a mapping those documents
would be collected and then never delivered.

This module owns the per-carrier mapping that expresses that relationship, read
from:

    customizations/template_schemas/<customer_id>/milestone_item_mapping.yaml

Shape (comments are free text and ignored -- the mapping itself is int -> int):

    mappings:
      - source_milestone: DRR
        target_milestone: P1
        pairs:
          77: 2       # CPM    Sustainability
          50: 10      # HW PL  LTE OTA
          ...

`pairs` maps a SOURCE `item_no` to a TARGET `item_no`. Both sides are resolved
against the same customer's template, and `item_no` is unique per
(customer, device, milestone) per [D-091], so the pair plus the milestone names
identifies the two work-items unambiguously.

Deliberately NOT a copy mechanism. Nothing here writes to `document_index`,
`document_item_association` or `document_version`; callers resolve the source
item's documents at read time. A document therefore exists once, indexed under
the milestone it actually arrived in.

Best-effort throughout, matching `template_lookup`: a missing or malformed file
logs a warning and yields an empty mapping, so a carrier without one simply has
no cross-milestone behaviour rather than a broken submission path.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "MappingBlock",
    "clear_cache",
    "load_all_mappings",
    "load_customer_mapping",
    "get_source_item_no",
    "get_source_item_nos",
    "get_target_item_no",
    "get_mapping_blocks",
    "MAPPING_FILENAME",
]

_log = logging.getLogger(__name__)

MAPPING_FILENAME = "milestone_item_mapping.yaml"

# customer_id -> list of validated blocks. Empty list is a legitimate cached
# value (file absent or empty) and is distinct from a missing key (never
# loaded), which is why callers go through the accessors below.
_CACHE: dict[str, list["MappingBlock"]] = {}


class MappingBlock:
    """One source-milestone -> target-milestone mapping, already validated.

    `pairs` is source item_no -> target item_no; `reverse` inverts it. Both
    directions are precomputed because submit_to_carrier resolves target ->
    source (which DRR items feed this P1 item?) while diagnostics and the UI
    resolve source -> target.

    `reverse` maps a target to a LIST of sources, in mapping-file order. Fan-in
    is legitimate: a TG can track two source work-items separately and still
    deliver both into one carrier folder, which is what MMK does for MNO-IOT
    (DRR #35 and #60 both feed P1 #23). The forward direction stays single-
    valued -- one source item cannot deliver to two places.
    """

    __slots__ = ("source_milestone", "target_milestone", "pairs", "reverse")

    def __init__(
        self,
        source_milestone: str,
        target_milestone: str,
        pairs: dict[int, int],
    ) -> None:
        self.source_milestone = source_milestone
        self.target_milestone = target_milestone
        self.pairs = pairs
        reverse: dict[int, list[int]] = {}
        for s, t in pairs.items():
            reverse.setdefault(t, []).append(s)
        self.reverse = reverse

    def __repr__(self) -> str:  # pragma: no cover -- diagnostics only
        return (
            f"MappingBlock({self.source_milestone}->{self.target_milestone}, "
            f"{len(self.pairs)} pairs)"
        )


def clear_cache() -> None:
    """Test / re-init hook. Not used in prod paths."""
    _CACHE.clear()


def _default_base_dir() -> Path:
    # Repo root is 3 levels above this file's dir (core/src/template_schema).
    return (
        Path(__file__).resolve().parents[3] / "customizations" / "template_schemas"
    )


def load_all_mappings(base_dir: Path | None = None) -> dict[str, bool]:
    """Walk customizations/template_schemas/*/milestone_item_mapping.yaml.

    Returns a per-customer load-result map for observability. A carrier
    directory with no mapping file is skipped entirely (absent from the result)
    rather than recorded as a failure -- most carriers will not have one.
    """
    if base_dir is None:
        base_dir = _default_base_dir()

    results: dict[str, bool] = {}
    if not base_dir.is_dir():
        _log.warning(
            "milestone_item_mapping: base_dir=%s not a directory; none loaded",
            base_dir,
        )
        return results

    for customer_dir in sorted(base_dir.iterdir()):
        if not customer_dir.is_dir():
            continue
        path = customer_dir / MAPPING_FILENAME
        if not path.exists():
            continue
        results[customer_dir.name] = load_customer_mapping(
            customer_dir.name, path,
        )

    _log.info(
        "milestone_item_mapping: loaded %d/%d customer mappings (base_dir=%s)",
        sum(1 for ok in results.values() if ok), len(results), base_dir,
    )
    return results


def load_customer_mapping(
    customer_id: str,
    mapping_path: Path | None = None,
) -> bool:
    """Load + validate one carrier's mapping into the cache.

    Returns True when the file parsed and at least the structure was valid.
    Individual bad pairs are dropped with a warning rather than failing the
    whole file -- a typo in one line must not silently disable delivery for the
    other 23 pairs. On a hard failure the cache slot is set to an EMPTY list, so
    lookups return None instead of falling through to a stale mapping.
    """
    if mapping_path is None:
        mapping_path = _default_base_dir() / customer_id / MAPPING_FILENAME

    if not mapping_path.exists():
        _log.info(
            "milestone_item_mapping: no %s for customer_id=%s (path=%s) -- "
            "no cross-milestone mapping for this carrier",
            MAPPING_FILENAME, customer_id, mapping_path,
        )
        _CACHE[customer_id] = []
        return True

    try:
        with mapping_path.open("r", encoding="utf-8") as f:
            parsed = yaml.safe_load(f)
    except Exception as exc:  # noqa: BLE001 -- best-effort
        _log.warning(
            "milestone_item_mapping: yaml load failed for customer_id=%s: %s: %s",
            customer_id, type(exc).__name__, str(exc)[:160],
        )
        _CACHE[customer_id] = []
        return False

    if parsed is None:
        _CACHE[customer_id] = []
        return True
    if not isinstance(parsed, dict):
        _log.warning(
            "milestone_item_mapping: %s for customer_id=%s is not a mapping "
            "(got %s)",
            MAPPING_FILENAME, customer_id, type(parsed).__name__,
        )
        _CACHE[customer_id] = []
        return False

    raw_blocks = parsed.get("mappings")
    if raw_blocks is None:
        _log.warning(
            "milestone_item_mapping: customer_id=%s has no top-level "
            "`mappings:` key -- treating as empty",
            customer_id,
        )
        _CACHE[customer_id] = []
        return False

    blocks = _validate_blocks(customer_id, raw_blocks)
    _CACHE[customer_id] = blocks
    _log.info(
        "milestone_item_mapping: customer_id=%s loaded %d block(s), %d pair(s) "
        "total from %s",
        customer_id, len(blocks), sum(len(b.pairs) for b in blocks), mapping_path,
    )
    return True


def _validate_blocks(customer_id: str, raw_blocks: Any) -> list[MappingBlock]:
    """Structural validation. Every rejection is WARN-logged with the carrier
    and the offending value, because a silently-dropped pair means documents
    that never reach the carrier."""
    if not isinstance(raw_blocks, list):
        _log.warning(
            "milestone_item_mapping: customer_id=%s `mappings` is %s, expected "
            "a list -- treating as empty",
            customer_id, type(raw_blocks).__name__,
        )
        return []

    blocks: list[MappingBlock] = []
    seen_routes: set[tuple[str, str]] = set()

    for idx, raw in enumerate(raw_blocks):
        if not isinstance(raw, dict):
            _log.warning(
                "milestone_item_mapping: customer_id=%s mappings[%d] is %s, "
                "expected a mapping -- skipped",
                customer_id, idx, type(raw).__name__,
            )
            continue

        src = str(raw.get("source_milestone") or "").strip()
        dst = str(raw.get("target_milestone") or "").strip()
        if not src or not dst:
            _log.warning(
                "milestone_item_mapping: customer_id=%s mappings[%d] missing "
                "source_milestone/target_milestone -- skipped",
                customer_id, idx,
            )
            continue
        if src == dst:
            _log.warning(
                "milestone_item_mapping: customer_id=%s mappings[%d] maps "
                "milestone %r to itself -- skipped",
                customer_id, idx, src,
            )
            continue
        if (src, dst) in seen_routes:
            _log.warning(
                "milestone_item_mapping: customer_id=%s duplicate route "
                "%s->%s at mappings[%d] -- skipped (first one wins)",
                customer_id, src, dst, idx,
            )
            continue

        raw_pairs = raw.get("pairs")
        if not isinstance(raw_pairs, dict):
            _log.warning(
                "milestone_item_mapping: customer_id=%s mappings[%d] (%s->%s) "
                "`pairs` is %s, expected a mapping -- skipped",
                customer_id, idx, src, dst, type(raw_pairs).__name__,
            )
            continue

        pairs: dict[int, int] = {}
        targets_seen: dict[int, list[int]] = {}   # target item_no -> sources
        for rk, rv in raw_pairs.items():
            try:
                s_no, t_no = int(rk), int(rv)
            except (TypeError, ValueError):
                _log.warning(
                    "milestone_item_mapping: customer_id=%s %s->%s pair "
                    "%r: %r is not an integer pair -- skipped",
                    customer_id, src, dst, rk, rv,
                )
                continue
            if s_no in pairs:
                # YAML dicts can't actually hold a duplicate key, but a
                # `77:` and `'77':` pair would collide after int() -- catch it
                # rather than let one silently overwrite the other.
                _log.warning(
                    "milestone_item_mapping: customer_id=%s %s->%s source "
                    "item_no=%d appears twice -- keeping the first (%d), "
                    "ignoring %d",
                    customer_id, src, dst, s_no, pairs[s_no], t_no,
                )
                continue
            if t_no in targets_seen:
                # Fan-in is allowed: two source work-items, tracked separately,
                # can deliver into one target's folder. Logged because uploads
                # are FLAT (UPLOAD-FLAT-1) -- identically-named files from the
                # contributing items land on the same carrier path and the
                # later one overwrites, with nothing else to announce it.
                _log.warning(
                    "milestone_item_mapping: customer_id=%s %s->%s target "
                    "item_no=%d is fed by MULTIPLE sources %s -- their "
                    "documents merge into one folder; identically-named files "
                    "will collide on upload",
                    customer_id, src, dst, t_no,
                    sorted(targets_seen[t_no] + [s_no]),
                )
            pairs[s_no] = t_no
            targets_seen.setdefault(t_no, []).append(s_no)

        if not pairs:
            _log.warning(
                "milestone_item_mapping: customer_id=%s mappings[%d] (%s->%s) "
                "has no usable pairs -- skipped",
                customer_id, idx, src, dst,
            )
            continue

        seen_routes.add((src, dst))
        blocks.append(MappingBlock(src, dst, pairs))

    return blocks


def get_mapping_blocks(customer_id: str) -> list[MappingBlock]:
    """All validated blocks for a carrier. Empty list when the carrier has no
    mapping file, the file failed to load, or it was never loaded -- callers
    treat all three the same way (no cross-milestone behaviour)."""
    return list(_CACHE.get(customer_id) or [])


def get_source_item_nos(
    *,
    customer_id: str,
    target_milestone: str,
    target_item_no: int,
) -> list[tuple[str, int]]:
    """Reverse lookup: which (source_milestone, source_item_no) pairs feed this
    target work-item?

    This is the direction submit_to_carrier needs -- it iterates the P1
    milestone's items and asks what upstream DRR items, if any, contribute
    documents. Returns [] when nothing maps to it.

    A target may be fed by more than one source; results are in mapping-file
    order so the contribution order is stable across restarts.
    """
    out: list[tuple[str, int]] = []
    for block in _CACHE.get(customer_id) or []:
        if block.target_milestone != target_milestone:
            continue
        for src_no in block.reverse.get(int(target_item_no)) or ():
            out.append((block.source_milestone, src_no))
    return out


def get_source_item_no(
    *,
    customer_id: str,
    target_milestone: str,
    target_item_no: int,
) -> tuple[str, int] | None:
    """First source feeding this target, or None.

    Retained for callers that only need to know WHETHER a target is mapped.
    Anything that collects documents must use `get_source_item_nos` -- taking
    only the first would silently drop the other contributors' files.
    """
    found = get_source_item_nos(
        customer_id=customer_id,
        target_milestone=target_milestone,
        target_item_no=target_item_no,
    )
    return found[0] if found else None


def get_target_item_no(
    *,
    customer_id: str,
    source_milestone: str,
    source_item_no: int,
) -> tuple[str, int] | None:
    """Forward lookup: which (target_milestone, target_item_no) does this
    source work-item feed? Used by diagnostics and by the source-side UI to
    explain where a document will ultimately be submitted."""
    for block in _CACHE.get(customer_id) or []:
        if block.source_milestone != source_milestone:
            continue
        tgt_no = block.pairs.get(int(source_item_no))
        if tgt_no is not None:
            return block.target_milestone, tgt_no
    return None
