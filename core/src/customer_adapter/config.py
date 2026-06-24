"""customer_adapter operational config -- 3-tier precedence per [D-025] + [D-038].

Slimmed per [D-116] D14 cascade 2026-06-25 -- binding owns selenium / Chromium /
session pool / upload timeout internally. HILDA-side config carries only:
- customizations_dir (where per-customer subclass modules live)
- ntp_skew_warn_s   (CAD-W005 threshold)
- diagnostic_ntp_check (disable in air-gapped test rigs)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

__all__ = ["CustomerAdapterConfig"]

_DEFAULT_CONFIG_PATH = Path("config/customer_adapter.json")

_ENV_MAP = {
    "customizations_dir":    "HILDA_CUSTOMER_ADAPTER_CUSTOMIZATIONS_DIR",
    "ntp_skew_warn_s":       "HILDA_CUSTOMER_ADAPTER_NTP_SKEW_WARN_S",
    "diagnostic_ntp_check":  "HILDA_CUSTOMER_ADAPTER_DIAGNOSTIC_NTP_CHECK",
}


class CustomerAdapterConfig(BaseModel):
    """Operational config -- environment-switching values only."""

    model_config = ConfigDict(extra="forbid")

    customizations_dir:   Path  = Path("/etc/hilda/customizations/customer_adapter")
    ntp_skew_warn_s:      float = 25.0     # CAD-W005 threshold; TOTP window ~30s
    diagnostic_ntp_check: bool  = True     # disable in air-gapped test rigs

    @classmethod
    def from_sources(
        cls,
        config_path: Path | None = None,
        cli_overrides: dict[str, object] | None = None,
    ) -> "CustomerAdapterConfig":
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
            if value is not None:
                data[key] = value
        return cls(**data)
