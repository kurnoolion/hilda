"""reconcile_config.py -- 3-tier precedence per [D-025] + [D-038].

CLI > HILDA_RECONCILE_<FIELD> env > config/reconcile.json > defaults.
Mirrors dashboard/config.py + rule_engine/config.py + storage/config.py patterns.

Per D-142/D-143: reconciliation cascade is opt-in-per-sync + interval-tunable
per environment. Ops can flip `enabled=false` on any sync-type or on the
top-level `enabled` flag to short-circuit the reconciler entirely without a
code deploy.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

__all__ = ["ReconcileConfig", "SyncTypeConfig"]

_DEFAULT_CONFIG_PATH = Path("config/reconcile.json")

_TOPLEVEL_ENV_MAP = {
    "enabled":      "HILDA_RECONCILE_ENABLED",
    "interval_sec": "HILDA_RECONCILE_INTERVAL_SEC",
}


class SyncTypeConfig(BaseModel):
    """Per-sync knobs. sync-1 uses elapsed_threshold_sec=0 (fires immediately
    when guard predicate satisfied). sync-2/3/4/5 default to 300s (5 min) --
    the reconciler backs off that long after the trigger timestamp before
    firing, to avoid racing with the still-in-flight email path.
    """

    model_config = ConfigDict(extra="forbid")

    enabled:               bool = True
    elapsed_threshold_sec: int  = 300


class ReconcileConfig(BaseModel):
    """Meta-reconciler config. Read at task-body invocation via `from_sources()`
    (not cached in-process -- cheap to re-parse per tick; ops can flip flags
    without restarting hilda-beat)."""

    model_config = ConfigDict(extra="forbid")

    enabled:      bool = True
    interval_sec: int  = 300     # meta-reconciler beat interval

    sync_1_delivery_item_count: SyncTypeConfig = SyncTypeConfig(
        enabled=True, elapsed_threshold_sec=0
    )
    sync_2_start_collection: SyncTypeConfig = SyncTypeConfig(
        enabled=True, elapsed_threshold_sec=300
    )
    sync_3_pm_approval: SyncTypeConfig = SyncTypeConfig(
        enabled=True, elapsed_threshold_sec=300
    )
    sync_4_submit_to_carrier: SyncTypeConfig = SyncTypeConfig(
        enabled=True, elapsed_threshold_sec=300
    )
    sync_5_close_all_items: SyncTypeConfig = SyncTypeConfig(
        enabled=True, elapsed_threshold_sec=300
    )
    # CIP-4 (2026-07-28): stuck-CloseInProgress sweeper. The 2-hop task
    # apply_tpm_sp_close_in_progress_task commits Postgres=CloseInProgress
    # in hop 1 THEN advances to Closed in hop 2. Worker crash between the
    # two hops leaves the item at CloseInProgress. sync-6 scans for such
    # stragglers and force-advances via update_delivery_state(bypass_guards=
    # True, trigger_source='manual_tpm_override'). Dormant when the
    # top-level enabled=false (corp Ph-1 default is OFF -- accepted risk
    # since crash window is <1s). 300s default threshold: real 2-hop
    # completes in ~1s, so anything older than 5 minutes is genuinely
    # stuck (not just racing the reconciler).
    sync_6_close_in_progress: SyncTypeConfig = SyncTypeConfig(
        enabled=True, elapsed_threshold_sec=300
    )

    @classmethod
    def from_sources(
        cls,
        config_path: Path | None = None,
        cli_overrides: dict[str, object] | None = None,
    ) -> "ReconcileConfig":
        """3-tier precedence: CLI > env > config file > defaults."""
        data: dict[str, object] = {}
        path = config_path or _DEFAULT_CONFIG_PATH
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data.update(json.load(f))
        for field_name, env_key in _TOPLEVEL_ENV_MAP.items():
            if env_key in os.environ:
                raw = os.environ[env_key]
                # Coerce bool / int for known types
                if field_name == "enabled":
                    data[field_name] = raw.strip().lower() in ("1", "true", "yes", "on")
                elif field_name == "interval_sec":
                    try:
                        data[field_name] = int(raw)
                    except (TypeError, ValueError):
                        pass
                else:
                    data[field_name] = raw
        for key, value in (cli_overrides or {}).items():
            if value is not None:
                data[key] = value
        return cls(**data)
