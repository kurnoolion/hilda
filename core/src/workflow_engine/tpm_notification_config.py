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

from pydantic import BaseModel, ConfigDict, field_validator

__all__ = ["TpmNotificationConfig"]

_DEFAULT_CONFIG_PATH = Path("config/tpm_notification.json")

_ENV_MAP = {
    "enabled":                          "HILDA_TPM_NOTIFICATION_ENABLED",
    "beat_interval_seconds":            "HILDA_TPM_NOTIFICATION_BEAT_INTERVAL_SECONDS",
    "timezone":                         "HILDA_TPM_NOTIFICATION_TIMEZONE",
    "window_minutes":                   "HILDA_TPM_NOTIFICATION_WINDOW_MINUTES",
    "strict_only":                      "HILDA_TPM_NOTIFICATION_STRICT_ONLY",
    "ops_alert_on_missed_window":       "HILDA_TPM_NOTIFICATION_OPS_ALERT_ON_MISSED_WINDOW",
    "ops_alert_on_missing_target_date": "HILDA_TPM_NOTIFICATION_OPS_ALERT_ON_MISSING_TARGET_DATE",
    "final_deliverable_item_name":      "HILDA_TPM_NOTIFICATION_FINAL_DELIVERABLE_ITEM_NAME",
    "final_deliverable_milestone_names": "HILDA_TPM_NOTIFICATION_FINAL_DELIVERABLE_MILESTONE_NAMES",
    "setup_complete_enabled":              "HILDA_TPM_NOTIFICATION_SETUP_COMPLETE_ENABLED",
    "setup_complete_beat_interval_seconds": "HILDA_TPM_NOTIFICATION_SETUP_COMPLETE_BEAT_INTERVAL_SECONDS",
    # UR-8 (Ph-2 2026-08-01): ops weekly digest for unrouted (_unknownTG) files.
    "ops_unrouted_digest_enabled":                "HILDA_TPM_NOTIFICATION_OPS_UNROUTED_DIGEST_ENABLED",
    "ops_unrouted_digest_beat_interval_seconds":  "HILDA_TPM_NOTIFICATION_OPS_UNROUTED_DIGEST_BEAT_INTERVAL_SECONDS",
    "ops_unrouted_digest_recipient":              "HILDA_TPM_NOTIFICATION_OPS_UNROUTED_DIGEST_RECIPIENT",
    "ops_unrouted_digest_min_count":              "HILDA_TPM_NOTIFICATION_OPS_UNROUTED_DIGEST_MIN_COUNT",
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

    # Final DRR status deliverable transition per architect 2026-07-18:
    # on day-of Excel send, the work item named `final_deliverable_item_name`
    # in each (customer, device, milestone) scope where milestone_id is in
    # `final_deliverable_milestone_names` is transitioned SubmittedToCustomer
    # (and the matching Default WI is closed, Ph-2 gate respected).
    final_deliverable_item_name:      str       = "Final DRR status excel deliverable for carrier"
    final_deliverable_milestone_names: list[str] = ["DRR"]

    # SETUP-1 (2026-07-28): setup-complete notification per architect ask.
    # Beat task fires every N seconds; for each (customer, device, milestone)
    # with delivery_items in Postgres, if every item.delivery_state is past
    # 'Not Started' (i.e., HILDA's D-144 auto-transition to Open finished for
    # all items in scope), send a completion email to the TPM. Idempotent
    # per scope via audit row 'setup_complete_notified' -- fires exactly once
    # per scope. If TPM adds items in a later wave, they're not re-notified
    # (option A per architect 2026-07-28; option B "delta emails" deferred).
    setup_complete_enabled:                bool = True
    setup_complete_beat_interval_seconds:  int  = 60

    # UR-8 (Ph-2 2026-08-01) ops weekly digest for unrouted files. Beat task
    # scans every (customer, device, milestone) scope with document_index
    # rows and aggregates the /_unknownTG bucket counts into one email to
    # `ops_unrouted_digest_recipient`. Weekly cadence keeps ops signal-to-
    # noise high; per-scope zeroes are suppressed. When the recipient is
    # empty the tick short-circuits (same enabled=false semantics).
    ops_unrouted_digest_enabled:               bool = True
    ops_unrouted_digest_beat_interval_seconds: int  = 604800    # 7 days
    ops_unrouted_digest_recipient:             str  = ""
    ops_unrouted_digest_min_count:             int  = 1         # send only when total >= this

    @field_validator("final_deliverable_milestone_names", mode="before")
    @classmethod
    def _parse_milestone_names(cls, v):
        """Accept comma-separated string (from env var) or list."""
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

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
