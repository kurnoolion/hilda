"""dashboard operational config — 3-tier precedence per [D-025] + [D-038].

CLI > HILDA_DASHBOARD_<FIELD> env > config/dashboard.json > defaults.
Mirrors rule_engine.config / storage.config patterns.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, field_validator

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
    "ph1_minimal":                "HILDA_DASHBOARD_PH1_MINIMAL",
    "wopi_jwt_secret":            "HILDA_DASHBOARD_WOPI_JWT_SECRET",
    "onlyoffice_public_url":      "HILDA_DASHBOARD_ONLYOFFICE_PUBLIC_URL",
    "onlyoffice_internal_url":    "HILDA_DASHBOARD_ONLYOFFICE_INTERNAL_URL",
    # UR-4 (Ph-2 2026-08-01): manual routing UI exclusion (/_unknownTG).
    "manual_routing_excluded_item_names":      "HILDA_DASHBOARD_MANUAL_ROUTING_EXCLUDED_ITEM_NAMES",
    "manual_routing_excluded_milestone_names": "HILDA_DASHBOARD_MANUAL_ROUTING_EXCLUDED_MILESTONE_NAMES",
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
    # 2026-07-01 architect lock: Ph-1 renders a minimal document table
    # (#, filename, humanized doc_type) with NO FR-60 review findings,
    # FR-61 mediated download, FR-87 TPM buttons, revision-resolution, or
    # per-load SP READ. Flip to False in Ph-2 without touching templates --
    # the FR-60/61/87 blocks are gated with {% if not cfg.ph1_minimal %}.
    ph1_minimal:                bool        = True

    # D-150 HILDA-side documents view (Ph-1). Empty defaults keep the /browse
    # + /wopi endpoints functional at the routing layer; the OnlyOffice editor
    # embed short-circuits with a "Configure OnlyOffice URL" message when
    # onlyoffice_public_url is empty. Set both URLs + wopi_jwt_secret at
    # deploy time (Chunk 1 topology already has JWT_SECRET on the OnlyOffice
    # container; paste the same value into wopi_jwt_secret below).
    wopi_jwt_secret:            str        = ""
    onlyoffice_public_url:      str        = ""
    onlyoffice_internal_url:    str        = ""

    # UR-4 (Ph-2 2026-08-01) manual routing exclusion — Final-DRR pattern
    # from tpm_notification_config.py. Any DeliveryItem whose `item_name`
    # appears in `manual_routing_excluded_item_names` is filtered out of
    # the /_unknownTG target-picker dropdown (UR-5). Empty default = no
    # exclusion. When `manual_routing_excluded_milestone_names` is non-
    # empty, the item-name exclusion applies ONLY inside those milestones
    # (per architect ask 2026-08-01: MMK's item 85 should be excluded in
    # DRR milestone only, not globally). Empty milestone list = apply the
    # item-name exclusion everywhere.
    manual_routing_excluded_item_names:      list[str] = []
    manual_routing_excluded_milestone_names: list[str] = []

    @field_validator(
        "manual_routing_excluded_item_names",
        "manual_routing_excluded_milestone_names",
        mode="before",
    )
    @classmethod
    def _parse_str_list(cls, v):
        """Accept comma-separated string (from env var) or list. Mirrors
        tpm_notification_config._parse_milestone_names."""
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v

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
