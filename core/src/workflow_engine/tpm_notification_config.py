"""tpm_notification_config.py -- 3-tier precedence per [D-025] + [D-038].

CLI > HILDA_TPM_NOTIFICATION_<FIELD> env > config/tpm_notification.json > defaults.
Mirrors reconcile_config.py + dashboard/config.py patterns.

Per architect 2026-07-15: TPM receives a DRR closure final-status email at
00:00 US Eastern on `milestone.target_date - 1` and again at 00:00 US
Eastern on `milestone.target_date`. Delivery window is bounded by
`window_minutes` around midnight to accommodate hilda-beat's actual firing
schedule (default 300s beat -> up to 5 min slack); a missed window fires
an ops alert when `ops_alert_on_missed_window=true`. Ops can flip
`enabled=false` to short-circuit the whole notification path without a
code deploy.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

__all__ = ["TpmNotificationConfig"]

_DEFAULT_CONFIG_PATH = Path("config/tpm_notification.json")

_ENV_MAP = {
    "enabled":                        "HILDA_TPM_NOTIFICATION_ENABLED",
    "beat_interval_seconds":          "HILDA_TPM_NOTIFICATION_BEAT_INTERVAL_SECONDS",
    "timezone":                       "HILDA_TPM_NOTIFICATION_TIMEZONE",
    "window_minutes":                 "HILDA_TPM_NOTIFICATION_WINDOW_MINUTES",
    "strict_only":                    "HILDA_TPM_NOTIFICATION_STRICT_ONLY",
    "ops_alert_on_missed_window":     "HILDA_TPM_NOTIFICATION_OPS_ALERT_ON_MISSED_WINDOW",
    "ops_alert_on_missing_target_date": "HILDA_TPM_NOTIFICATION_OPS_ALERT_ON_MISSING_TARGET_DATE",
}


class TpmNotificationConfig(BaseModel):
    """Read at task-body invocation via `from_sources()` (not cached in-process
    -- cheap to re-parse per tick; ops can flip flags without restarting
    hilda-beat)."""

    model_config = ConfigDict(extra="forbid")

    enabled:                        bool  = True
    beat_interval_seconds:          int   = 300                    # 5 min tick
    timezone:                       str   = "America/New_York"     # US Eastern per architect 2026-07-15
    window_minutes:                 int   = 10                     # 10-min slop around 00:00
    strict_only:                    bool  = True                   # per architect ask
    ops_alert_on_missed_window:     bool  = True
    ops_alert_on_missing_target_date: bool = True

    @classmethod
    def from_sources(
        cls,
        config_path: Path | None = None,
        cli_overrides: dict[str, object] | None = None,
    ) -> "TpmNotificationConfig":
        """3-tier precedence: CLI > env > config file > defaults."""
        data: dict[str, object] = {}
        path = config_path or _DEFAULT_CONFIG_PATH
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data.update(json.load(f))
        for field_name, env_key in _ENV_MAP.items():
            if env_key in os.environ:
                data[field_name] = os.environ[env_key]
        for key, value in (cli_overrides or {}).items():
            data[key] = value
        return cls(**data)
