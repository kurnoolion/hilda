"""storage operational config — 3-tier precedence per structure-conventions Config format.

Mirrors sharepoint_integration's GlobalSharePointConfig pattern:
CLI overrides > env vars (HILDA_<FIELD>) > config/storage.json > defaults.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["GlobalStorageConfig", "get_storage_config", "set_storage_config"]

_DEFAULT_CONFIG_PATH = Path("config/storage.json")

# nsd_mount_root keeps its historical env name (HILDA_NSD_MOUNT_ROOT) rather than
# the generic HILDA_STORAGE_ prefix — it predates this config class and is named
# in MODULE.md / [D-013]-aligned docs.
_ENV_MAP = {
    "nsd_mount_root": "HILDA_NSD_MOUNT_ROOT",
    "db_url": "HILDA_STORAGE_DB_URL",
    "redis_url": "HILDA_REDIS_URL",
}


class GlobalStorageConfig(BaseModel):
    """Operational values only — runtime/domain data lives in customizations/."""

    model_config = ConfigDict(extra="forbid")

    nsd_mount_root: Path = Field(
        default=Path("/nsd"),
        description="Host-level cifs/smb3 mount of the corp NSD share per [D-013]; "
        "bind-mounted into containers per [D-025].",
    )
    db_url: str = "postgresql+asyncpg://hilda@localhost:5432/hilda"
    redis_url: str = "redis://localhost:6379/0"

    @classmethod
    def from_sources(
        cls,
        config_path: Path | None = None,
        cli_overrides: dict[str, object] | None = None,
    ) -> "GlobalStorageConfig":
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


_config: GlobalStorageConfig | None = None


def get_storage_config() -> GlobalStorageConfig:
    """Process-wide config; resolved lazily on first use (env changes in tests are
    honored because tests call set_storage_config(None) or set explicit configs)."""
    global _config
    if _config is None:
        _config = GlobalStorageConfig.from_sources()
    return _config


def set_storage_config(config: GlobalStorageConfig | None) -> None:
    """**TEST / CLI-MOCK ONLY** — replaces the module-level cached config.

    Production code MUST NOT call this — `get_storage_config()` auto-loads from
    the 3-tier sources once at first access and cannot be safely swapped mid-run
    while async NSD IO is in-flight (file handles, atomic temp-rename windows, and
    any cached path-prefix derivations would desync). Test fixtures call this in
    setup; the CLI mock calls it to inject the temp mount. No other caller is allowed.
    """
    global _config
    _config = config
