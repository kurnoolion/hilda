"""sharepoint_integration config: GlobalSharePointConfig + ListScope.

Operational nora 3-tier (CLI → env → config/sharepoint_integration.json).
Anchors [D-004], [D-006], [D-020].
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


@dataclass(frozen=True)
class ListScope:
    """Scope for list-name + column-map lookup. customer_slug required;
    device_slug non-None triggers device-level override lookup."""

    customer_slug: str
    device_slug: str | None = None


_AUTH_LITERAL = Literal["none", "ntlm", "kerberos"]


class GlobalSharePointConfig(BaseModel):
    """Operational config — environment-switching values only.

    Customer-specific list names + column maps live in customizations/sharepoint_config/,
    NOT here.
    """

    model_config = ConfigDict(extra="forbid")

    site_url: str = Field(..., description="SP 2017 site URL, e.g. https://sp2017.corp/sites/hilda")
    auth_type: _AUTH_LITERAL = "ntlm"
    username: str | None = None
    password: str | None = Field(default=None, repr=False)
    keytab_path: Path | None = None
    timeout_seconds: int = 30
    max_retries: int = 3
    retry_backoff_seconds: float = 1.0
    page_size: int = 100

    def __repr__(self) -> str:
        return (
            f"GlobalSharePointConfig(site_url={self.site_url!r}, "
            f"auth_type={self.auth_type!r}, username={self.username!r}, "
            f"timeout_seconds={self.timeout_seconds}, "
            f"max_retries={self.max_retries}, page_size={self.page_size})"
        )

    def __str__(self) -> str:
        return self.__repr__()

    @classmethod
    def from_sources(
        cls,
        config_path: Path | None = None,
        cli_overrides: dict[str, object] | None = None,
        env_prefix: str = "HILDA_SP_",
    ) -> "GlobalSharePointConfig":
        """3-tier precedence: CLI overrides > env vars > config file > defaults."""
        data: dict[str, object] = {}
        if config_path and config_path.exists():
            with config_path.open("r", encoding="utf-8") as f:
                data.update(json.load(f))
        # Env vars
        for field_name in cls.model_fields:
            env_key = f"{env_prefix}{field_name.upper()}"
            if env_key in os.environ:
                raw = os.environ[env_key]
                data[field_name] = _coerce(field_name, raw)
        # CLI overrides
        if cli_overrides:
            for k, v in cli_overrides.items():
                if v is not None:
                    data[k] = v
        return cls.model_validate(data)


def _coerce(field_name: str, raw: str) -> object:
    """Coerce env-var string to type implied by the field."""
    int_fields = {"timeout_seconds", "max_retries", "page_size"}
    float_fields = {"retry_backoff_seconds"}
    if field_name in int_fields:
        return int(raw)
    if field_name in float_fields:
        return float(raw)
    if field_name == "keytab_path":
        return Path(raw)
    return raw
