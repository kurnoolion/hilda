"""rule_engine operational config — 3-tier precedence per [D-025] + [D-038].

Mirrors storage's GlobalStorageConfig pattern:
CLI overrides > env vars (HILDA_RULE_ENGINE_<FIELD>) > config/rule_engine.json > defaults.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

__all__ = ["RuleEngineConfig"]

_DEFAULT_CONFIG_PATH = Path("config/rule_engine.json")

_ENV_MAP = {
    "rules_dir": "HILDA_RULE_ENGINE_RULES_DIR",
    "postgres_override_cache_ttl_s": "HILDA_RULE_ENGINE_OVERRIDE_CACHE_TTL_S",
    "default_baseline_minutes": "HILDA_RULE_ENGINE_DEFAULT_BASELINE_MINUTES",
    "collision_audit_on_load": "HILDA_RULE_ENGINE_COLLISION_AUDIT_ON_LOAD",
    "orphan_audit_on_load": "HILDA_RULE_ENGINE_ORPHAN_AUDIT_ON_LOAD",
}


class RuleEngineConfig(BaseModel):
    """Operational values only — rule content lives in customizations/rules/."""

    model_config = ConfigDict(extra="forbid")

    rules_dir: Path = Path("/etc/hilda/customizations/rules")
    postgres_override_cache_ttl_s: int = 60  # per-item cache TTL
    default_baseline_minutes: int = 60  # used when polling_schedule baseline tier missing (RUL-W004)
    collision_audit_on_load: bool = True  # emit RUL-W001 at load; ops may disable in test environments
    orphan_audit_on_load: bool = True  # emit RUL-W002 at load

    @classmethod
    def from_sources(
        cls,
        config_path: Path | None = None,
        cli_overrides: dict[str, object] | None = None,
    ) -> "RuleEngineConfig":
        """3-tier precedence: CLI overrides > env vars > config file > defaults."""
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
