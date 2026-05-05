"""Slug convention for path_slug fields. Anchors [D-013]."""
from __future__ import annotations

import re

from core.src.diagnostics import PipelineError

SLUG_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_-]+$")
_SLUG_REPLACE = re.compile(r"[^a-zA-Z0-9]+")
_MAX_SLUG_LEN = 64


def validate_slug(value: str) -> str:
    """Validator for path_slug fields. Raises TSC-E004 if pattern fails."""
    if not isinstance(value, str) or not SLUG_PATTERN.match(value):
        raise PipelineError("TSC-E004", context={"value": value})
    return value


def make_slug(human_name: str) -> str:
    """Deterministic: lower-case, replace non-alphanumeric with '-', truncate.

    Minted at entity-creation; never recomputed on rename. Stored value is authoritative.
    """
    if not human_name:
        raise PipelineError("TSC-E004", context={"value": human_name})
    lowered = human_name.lower()
    replaced = _SLUG_REPLACE.sub("-", lowered).strip("-")
    truncated = replaced[:_MAX_SLUG_LEN]
    if not truncated:
        raise PipelineError("TSC-E004", context={"value": human_name})
    return truncated
