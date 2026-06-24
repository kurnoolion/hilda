"""messenger operational config -- 3-tier precedence per [D-025] + [D-038].

Per architect Q-M2 / Q-M3 lock 2026-06-25:
- daily_limit_per_owner = 3 (Q-M2)
- max_message_bytes = 4000 (Q-M2)
- bot_signature = "anonymous HILDA BOT" (Q-M3)

Credentials are NOT in config -- they flow through credential_service per [D-019]
if the binding requires them (typically the binding manages its own auth).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

__all__ = ["MessengerConfig"]

_DEFAULT_CONFIG_PATH = Path("config/messenger.json")


def _default_templates_dir() -> Path:
    return Path(__file__).resolve().parent / "templates"


class MessengerConfig(BaseModel):
    """Messenger module config per Q-M2 / Q-M3 architect lock 2026-06-25."""

    model_config = ConfigDict(extra="forbid")

    daily_limit_per_owner: int = 3                # Q-M2 hard cap per owner_corp_id per day
    max_message_bytes: int = 4000                 # Q-M2 hard cap per message
    retry_attempts: int = 3
    retry_backoff_seconds: float = 30.0
    bot_signature: str = "-- HILDA BOT"           # Q-M3 anonymous attribution
    templates_dir: Path = Path("core/src/messenger/templates")

    @classmethod
    def from_sources(
        cls,
        config_path: Path | None = None,
        cli_overrides: dict[str, object] | None = None,
    ) -> "MessengerConfig":
        """3-tier precedence: CLI > env > config file > defaults."""
        data: dict[str, object] = {}
        path = config_path or _DEFAULT_CONFIG_PATH
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data.update(json.load(f))
        env_map = {
            "daily_limit_per_owner": "HILDA_MSG_DAILY_LIMIT",
            "max_message_bytes": "HILDA_MSG_MAX_BYTES",
            "retry_attempts": "HILDA_MSG_RETRY_ATTEMPTS",
            "retry_backoff_seconds": "HILDA_MSG_RETRY_BACKOFF",
            "bot_signature": "HILDA_MSG_BOT_SIGNATURE",
        }
        for field_name, env_key in env_map.items():
            if env_key in os.environ:
                data[field_name] = os.environ[env_key]
        for key, value in (cli_overrides or {}).items():
            if value is not None:
                data[key] = value
        if "templates_dir" not in data:
            data["templates_dir"] = _default_templates_dir()
        return cls(**data)
