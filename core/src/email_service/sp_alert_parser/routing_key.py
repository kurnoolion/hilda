"""SP-alert body routing-key extractor per [D-047] + FR-87.

Body carries key:value pairs identifying the changed entity. Routing key
shape: (project_id, milestone_name, item_number). Used by parser.py to
resolve to a SP list + row.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["extract_routing_key"]


_KV_LINE_RE = re.compile(
    r"^\s*(?P<key>[A-Za-z][A-Za-z0-9_]*)\s*[:=]\s*(?P<value>.+?)\s*$",
    re.MULTILINE,
)


@dataclass(frozen=True)
class _RoutingKey:
    project_id: str | None
    milestone_name: str | None
    item_number: int | None


def extract_routing_key(
    body: str,
) -> tuple[str | None, str | None, int | None]:
    """Extract (project_id, milestone_name, item_number) from a SP alert body.

    Field aliases accepted (case-insensitive):
      - project_id    : ProjectID, project_id, Project_ID
      - milestone     : MinorMilestone, Milestone, milestone_name
      - item_number   : ItemNumber, item_no, ItemNo

    Returns (None, None, None) if the body has no key:value lines at all.
    """
    if not body:
        return (None, None, None)

    project_id: str | None = None
    milestone_name: str | None = None
    item_number: int | None = None

    project_keys = {"projectid", "project_id"}
    milestone_keys = {"minormilestone", "milestone", "milestone_name"}
    item_keys = {"itemnumber", "item_no", "itemno", "item_number"}

    for m in _KV_LINE_RE.finditer(body):
        key = m.group("key").strip().lower()
        value = m.group("value").strip()
        if key in project_keys and project_id is None:
            project_id = value
        elif key in milestone_keys and milestone_name is None:
            milestone_name = value
        elif key in item_keys and item_number is None:
            try:
                item_number = int(value)
            except (TypeError, ValueError):
                item_number = None

    return (project_id, milestone_name, item_number)
