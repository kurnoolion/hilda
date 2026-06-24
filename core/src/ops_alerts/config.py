"""OpsAlertsConfig -- composition-root config for ops_alerts.

Per [D-127] Ratified 2026-06-26.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class OpsAlertsConfig:
    """Composition-root config.

    `recipients_yaml_path` is LOCAL per [D-125] Point 3 -- production file
    lives on architect's Linux deployment box; public github gets
    sanitized placeholder showing format only.
    """

    recipients_yaml_path: Path
    # Subject prefix template -- {severity} is upper-cased automatically.
    subject_prefix_template: str = "[{severity}] HILDA: {source} {error_code}"
    # Per-(source, error_code) rolling window length in seconds (rate limiter).
    rate_limit_window_seconds: int = 60
    # Max length of a single context value before truncation (per NFR-2).
    context_value_max_chars: int = 200
    # Local ops-log file path for fire-and-forget fallback when both
    # email + messenger fail (best-effort; never raises if write fails).
    ops_log_fallback_path: Path | None = None
