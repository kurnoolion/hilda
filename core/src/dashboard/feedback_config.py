"""feedback_config.py -- bug-type registry for the /feedback/* early-access UI.

Loads `config/feedback_bug_types.json` at first call; exposes both grouped
(for HTML optgroup rendering) + flat (for validation + display) views.

Ops edits the JSON + hilda-api restart. No hot-reload.

Ph-1 corp early-access surface (5 TPMs); 9 workflow-phase categories +
'OTHER' catch-all per architect ask 2026-07-30. Category 'improvement'
always resolves to bug_type='OTHER' -- the free-text description is the
payload.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

__all__ = [
    "CATEGORY_BUG",
    "CATEGORY_IMPROVEMENT",
    "CATEGORIES",
    "IMPROVEMENT_BUG_TYPE",
    "load_bug_types",
    "flat_bug_types",
    "grouped_bug_types",
    "is_valid_bug_type",
    "clear_cache",
]

CATEGORY_BUG = "bug"
CATEGORY_IMPROVEMENT = "improvement"
CATEGORIES: tuple[str, ...] = (CATEGORY_BUG, CATEGORY_IMPROVEMENT)

# Improvement always resolves to a single bug_type per architect spec.
IMPROVEMENT_BUG_TYPE = "OTHER-OTHER"

_DEFAULT_CONFIG_PATH = Path("config/feedback_bug_types.json")

_log = logging.getLogger(__name__)


@lru_cache(maxsize=8)
def load_bug_types(path_str: str | None = None) -> dict[str, list[str]]:
    """Load JSON + return {phase: [description, ...]}. Cached by path string.

    Raises:
        FileNotFoundError -- config file missing.
        ValueError -- structural malformation (missing top key, empty list,
                      non-string entry).
    """
    p = Path(path_str) if path_str else _DEFAULT_CONFIG_PATH
    if not p.exists():
        raise FileNotFoundError(
            f"feedback_config: bug-types config missing at {p} -- "
            f"add config/feedback_bug_types.json or set path override"
        )
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    groups = data.get("bug_types_by_category")
    if not isinstance(groups, dict) or not groups:
        raise ValueError(
            f"feedback_config: 'bug_types_by_category' missing or empty in {p}"
        )
    for phase, items in groups.items():
        if not isinstance(phase, str) or not phase.strip():
            raise ValueError(
                f"feedback_config: invalid phase key {phase!r} in {p}"
            )
        if not isinstance(items, list) or not items:
            raise ValueError(
                f"feedback_config: phase {phase!r} must have non-empty list in {p}"
            )
        for item in items:
            if not isinstance(item, str) or not item.strip():
                raise ValueError(
                    f"feedback_config: phase {phase!r} has non-string entry {item!r} in {p}"
                )
    return groups


def grouped_bug_types(path: Path | None = None) -> dict[str, list[str]]:
    """Return {phase: [description, ...]} for optgroup rendering."""
    return dict(load_bug_types(str(path) if path else None))


def flat_bug_types(path: Path | None = None) -> list[str]:
    """Return flat list of 'PHASE-description' composed strings.

    Order: phases in JSON insertion order (Python 3.7+ dict guarantee);
    descriptions in the order given per phase.
    """
    result: list[str] = []
    for phase, items in load_bug_types(str(path) if path else None).items():
        for item in items:
            result.append(f"{phase}-{item}")
    return result


def is_valid_bug_type(bug_type: str, path: Path | None = None) -> bool:
    """Case-sensitive membership check against the composed 'PHASE-description'
    strings. Used by the submit route to validate form input."""
    return bug_type in flat_bug_types(path)


def clear_cache() -> None:
    """Test hook -- flush the lru_cache so subsequent calls re-read from disk."""
    load_bug_types.cache_clear()
