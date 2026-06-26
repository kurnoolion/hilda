"""SP-alert body routing-key extractor per [D-047] + FR-87.

Body carries key:value pairs identifying the changed entity. Routing key
shape: (project_id, milestone_name, item_number). Used by parser.py to
resolve to a SP list + row.

Refactored 2026-06-26 to consume the already-parsed `body_kvs` dict from
SpAlertParser._parse_body() rather than re-parsing the raw body with a
separate (subtly different) MULTILINE regex. Surfaced during corp Linux
box Phase D2 smoke test: 32-key Deliverable body had `project_id: 2350`
which the body parser correctly extracted into body_kvs but the
routing_key extractor's MULTILINE regex missed -- leading to None where
"2350" was expected. Consuming the same dict eliminates the divergence.
"""
from __future__ import annotations

from typing import Iterable, Mapping

__all__ = ["extract_routing_key"]


# Field-name aliases per architect-confirmed corp SP body shapes. All lookups
# are case-insensitive (body parser preserves case; we normalize at lookup).
_PROJECT_KEYS: tuple[str, ...] = ("project_id", "projectid")
_MILESTONE_KEYS: tuple[str, ...] = ("milestone_name", "minormilestone", "milestone")
_ITEM_KEYS: tuple[str, ...] = ("item_no", "itemnumber", "itemno", "item_number")


def _first_nonempty(lower_kvs: Mapping[str, str], aliases: Iterable[str]) -> str | None:
    """Return the first non-empty value found among the alias list."""
    for k in aliases:
        v = lower_kvs.get(k)
        if v:                # treat empty string as absent
            return v
    return None


def extract_routing_key(
    body_kvs: Mapping[str, str],
) -> tuple[str | None, str | None, int | None]:
    """Extract (project_id, milestone_name, item_number) from a parsed body_kvs dict.

    Field aliases accepted (case-insensitive):
      - project_id    : project_id, ProjectID, Project_ID
      - milestone     : milestone_name, MinorMilestone, Milestone
      - item_number   : item_no, ItemNumber, ItemNo, item_number

    Returns (None, None, None) if body_kvs is empty or has no matching keys.
    Empty-string values are treated as absent (SP encodes "no value" as the
    empty string on optional fields per architect Q3 2026-06-27 body shape).
    """
    if not body_kvs:
        return (None, None, None)

    # Case-insensitive lookup -- normalize keys once.
    lower_kvs = {k.lower(): v for k, v in body_kvs.items()}

    project_id = _first_nonempty(lower_kvs, _PROJECT_KEYS)
    milestone_name = _first_nonempty(lower_kvs, _MILESTONE_KEYS)

    item_number: int | None = None
    item_raw = _first_nonempty(lower_kvs, _ITEM_KEYS)
    if item_raw is not None:
        try:
            item_number = int(item_raw)
        except (TypeError, ValueError):
            item_number = None

    return (project_id, milestone_name, item_number)
