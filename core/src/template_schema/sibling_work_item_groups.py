"""HWPL-SIBLING-1 (2026-09-13) -- loader for the sibling-work-item cascade
config.

Config file per customer:
  customizations/template_schemas/<CUSTOMER>/sibling_work_item_groups.yaml

Shape:
  target_milestone: P1
  groups:
    - tg_name: "HW PL"
      anchor:  <item_no>
      siblings: [<item_no>, <item_no>, ...]

Loader is best-effort:
  * missing file       -> [] (feature off for this customer; caller no-ops)
  * malformed yaml     -> [] + WARN (never raises upstream)
  * missing keys       -> block skipped + WARN
  * duplicate sibling  -> first-group-wins + WARN (per architect
                          2026-09-13: multi-source not a production shape)

The loader is process-cached per customer_id; a container restart is
required to pick up yaml edits (matches milestone_item_mapping.py).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "SiblingGroup",
    "get_sibling_groups",
    "sibling_group_for_anchor",
]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SiblingGroup:
    """One anchor -> siblings cascade block."""
    tg_name: str
    anchor: int
    siblings: tuple[int, ...]


# process-cache: customer_id -> (target_milestone, tuple[SiblingGroup, ...])
_CACHE: dict[str, tuple[str, tuple[SiblingGroup, ...]]] = {}


def _yaml_path(customer_id: str) -> Path:
    # Mirrors milestone_item_mapping.py: repo-root/customizations/...
    from core.src.template_schema import template_lookup
    root = getattr(template_lookup, "_CUSTOMIZATIONS_ROOT", None)
    if root is None:
        root = Path(__file__).resolve().parents[3] / "customizations"
    return Path(root) / "template_schemas" / customer_id / "sibling_work_item_groups.yaml"


def _load_from_disk(customer_id: str) -> tuple[str, tuple[SiblingGroup, ...]]:
    path = _yaml_path(customer_id)
    if not path.exists():
        _log.info(
            "sibling_work_item_groups: no config file for customer=%s at %s "
            "-- feature off for this customer",
            customer_id, path,
        )
        return ("", ())
    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "sibling_work_item_groups: yaml load failed customer=%s path=%s: "
            "%s: %s -- treating as empty",
            customer_id, path, type(exc).__name__, str(exc)[:200],
        )
        return ("", ())

    target_milestone = str(raw.get("target_milestone") or "").strip()
    groups_raw = raw.get("groups") or []
    if not isinstance(groups_raw, list):
        _log.warning(
            "sibling_work_item_groups: 'groups' is not a list customer=%s "
            "-- treating as empty",
            customer_id,
        )
        return (target_milestone, ())

    seen_items: set[int] = set()
    groups: list[SiblingGroup] = []
    for idx, block in enumerate(groups_raw):
        if not isinstance(block, dict):
            _log.warning(
                "sibling_work_item_groups: block #%d is not a dict customer=%s "
                "-- skipping",
                idx, customer_id,
            )
            continue
        tg_name = str(block.get("tg_name") or "").strip()
        anchor = block.get("anchor")
        siblings = block.get("siblings") or []
        if not tg_name or anchor is None:
            _log.warning(
                "sibling_work_item_groups: block #%d missing tg_name / anchor "
                "customer=%s -- skipping (block=%r)",
                idx, customer_id, block,
            )
            continue
        try:
            anchor_i = int(anchor)
            siblings_i = tuple(int(s) for s in siblings)
        except (TypeError, ValueError) as exc:
            _log.warning(
                "sibling_work_item_groups: block #%d non-int item_no "
                "customer=%s: %s -- skipping",
                idx, customer_id, exc,
            )
            continue
        # Multi-source guard: no sibling may belong to two groups (per
        # architect 2026-09-13: not a production shape). First wins.
        clash = {anchor_i, *siblings_i} & seen_items
        if clash:
            _log.warning(
                "sibling_work_item_groups: block #%d shares item(s) %s with "
                "an earlier block customer=%s -- skipping (multi-source not "
                "supported)",
                idx, sorted(clash), customer_id,
            )
            continue
        seen_items.add(anchor_i)
        seen_items.update(siblings_i)
        groups.append(SiblingGroup(
            tg_name=tg_name, anchor=anchor_i, siblings=siblings_i,
        ))

    return (target_milestone, tuple(groups))


def get_sibling_groups(customer_id: str) -> tuple[str, tuple[SiblingGroup, ...]]:
    """Return (target_milestone, groups) for `customer_id`.

    Process-cached. Empty tuple => feature off for this customer.
    Never raises.
    """
    hit = _CACHE.get(customer_id)
    if hit is not None:
        return hit
    val = _load_from_disk(customer_id)
    _CACHE[customer_id] = val
    return val


def sibling_group_for_anchor(
    customer_id: str, tg_name: str, anchor_item_no: int,
) -> SiblingGroup | None:
    """Return the group whose anchor + tg_name match, or None."""
    _, groups = get_sibling_groups(customer_id)
    tg_key = (tg_name or "").strip().lower()
    for g in groups:
        if g.anchor == anchor_item_no and g.tg_name.strip().lower() == tg_key:
            return g
    return None


def _clear_cache_for_tests() -> None:
    """Test helper -- forgets the process cache so a fresh yaml is loaded."""
    _CACHE.clear()
