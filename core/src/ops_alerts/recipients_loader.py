"""recipients.yaml loader -- LOCAL per [D-125] Point 3.

Per [D-127] Ratified 2026-06-26. Loaded once at composition-root time
via `load_recipients(path) -> Recipients`. SIGHUP-triggered reload is
deferred Ph-2.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from core.src.diagnostics import PipelineError


@dataclass(frozen=True)
class Recipients:
    """Parsed + validated recipients.yaml shape."""

    ops_bot_email:           str               # single email destination
    broadcast_corp_ids:      tuple[str, ...]   # N messenger DM destinations
    rate_limit_per_minute:   int | None        # null = no rate limit (Ph-1 default)


def load_recipients(path: Path) -> Recipients:
    """Load + validate recipients.yaml; raises PipelineError(OPS-E001) on failure.

    Construction-time only -- callers should catch + surface gracefully
    if production deployment is missing the file (degraded mode).

    Validation rules:
    - `ops_bot_email`: non-empty string containing "@"
    - `broadcast_corp_ids`: list (possibly empty -- 0 DMs is OK; alert
      still flows via email channel)
    - `rate_limit_per_minute`: null OR positive int
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        raise PipelineError(
            "OPS-E001",
            context={"path": str(path), "reason": "read_failure"},
            cause=e,
        ) from e

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise PipelineError(
            "OPS-E001",
            context={"path": str(path), "reason": "yaml_parse_failure"},
            cause=e,
        ) from e

    if not isinstance(data, dict):
        raise PipelineError(
            "OPS-E001",
            context={"path": str(path), "reason": "yaml_root_not_mapping"},
        )

    ops_bot_email = data.get("ops_bot_email")
    if not isinstance(ops_bot_email, str) or "@" not in ops_bot_email:
        raise PipelineError(
            "OPS-E001",
            context={"path": str(path), "reason": "ops_bot_email_invalid"},
        )

    raw_corp_ids = data.get("broadcast_corp_ids", []) or []
    if not isinstance(raw_corp_ids, list) or not all(
        isinstance(x, str) and x.strip() for x in raw_corp_ids
    ):
        raise PipelineError(
            "OPS-E001",
            context={"path": str(path), "reason": "broadcast_corp_ids_invalid"},
        )

    rate_raw: Any = data.get("rate_limit_per_minute", None)
    if rate_raw is None:
        rate: int | None = None
    elif isinstance(rate_raw, int) and not isinstance(rate_raw, bool) and rate_raw > 0:
        rate = rate_raw
    else:
        raise PipelineError(
            "OPS-E001",
            context={"path": str(path), "reason": "rate_limit_invalid"},
        )

    return Recipients(
        ops_bot_email=ops_bot_email,
        broadcast_corp_ids=tuple(raw_corp_ids),
        rate_limit_per_minute=rate,
    )
