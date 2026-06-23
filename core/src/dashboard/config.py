"""dashboard operational config — 3-tier precedence per [D-025] + [D-038].

CLI > HILDA_DASHBOARD_<FIELD> env > config/dashboard.json > defaults.
Mirrors rule_engine.config / storage.config patterns.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

__all__ = ["DashboardConfig"]

_DEFAULT_CONFIG_PATH = Path("config/dashboard.json")
_DEFAULT_TEMPLATES_DIR = Path(__file__).parent / "templates"

_ENV_MAP = {
    "bind_host":                  "HILDA_DASHBOARD_BIND_HOST",
    "bind_port":                  "HILDA_DASHBOARD_BIND_PORT",
    "reverse_proxy_origin":       "HILDA_DASHBOARD_REVERSE_PROXY_ORIGIN",
    "refresh_rate_limit_seconds": "HILDA_DASHBOARD_REFRESH_RATE_LIMIT_SECONDS",
    "token_ttl_seconds":          "HILDA_DASHBOARD_TOKEN_TTL_SECONDS",
    "static_files_dir":           "HILDA_DASHBOARD_STATIC_FILES_DIR",
    "jinja_templates_dir":        "HILDA_DASHBOARD_JINJA_TEMPLATES_DIR",
    "mock_auth":                  "HILDA_DASHBOARD_MOCK_AUTH",
}


class DashboardConfig(BaseModel):
    """Operational config -- environment-switching values only."""

    model_config = ConfigDict(extra="forbid")

    bind_host:                  str        = "0.0.0.0"
    bind_port:                  int        = 8443
    reverse_proxy_origin:       str        = "https://hilda-proxy.corp"
    refresh_rate_limit_seconds: int        = 300                       # FR-56 default 5 min
    token_ttl_seconds:          int        = 300                       # FR-61 default 300 s
    static_files_dir:           Path | None = None
    jinja_templates_dir:        Path        = _DEFAULT_TEMPLATES_DIR
    mock_auth:                  bool        = False                    # Ph-1 mock harness flag; production = False
    # Ph-1 per D6 cascade 2026-06-23: CORS allowlist empty; future JSON consumers add via this field
    cors_origins:               tuple[str, ...] = ()

    @classmethod
    def from_sources(
        cls,
        config_path: Path | None = None,
        cli_overrides: dict[str, object] | None = None,
    ) -> "DashboardConfig":
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
