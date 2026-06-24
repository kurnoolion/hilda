"""email_service operational config -- 3-tier precedence per [D-025] + [D-038].

Three Pydantic models per MODULE.md Configuration section:
- ImapConfig    -- IMAP receiver
- SmtpConfig    -- SMTP sender
- Fr52Config    -- FR-52 / FR-85 / FR-86 router thresholds + NSD root + post-write gates

Credentials are NOT in config -- they flow through credential_service per [D-019].
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict

__all__ = ["ImapConfig", "SmtpConfig", "Fr52Config", "EmailServiceConfig"]

_DEFAULT_CONFIG_PATH = Path("config/email_service.json")


class ImapConfig(BaseModel):
    """IMAP receiver config per FR-23 + [D-016] + MODULE.md."""

    model_config = ConfigDict(extra="forbid")

    host: str = "localhost"
    port: int = 993
    use_idle: bool = True            # falls back to poll when False or unsupported
    poll_interval_s: int = 60        # short-poll fallback cadence
    mailbox: str = "INBOX"
    folder_processed: str = "INBOX/processed"   # IMAP MOVE target after read


class SmtpConfig(BaseModel):
    """SMTP sender config per FR-9 / FR-10."""

    model_config = ConfigDict(extra="forbid")

    host: str = "localhost"
    port: int = 587
    use_tls: bool = True
    from_addr: str = "hilda@corp.example"


class Fr52Config(BaseModel):
    """FR-52 5-step routing + FR-85 2-step classification + FR-86 4-path storage
    matrix thresholds + post-write gates per MODULE.md Configuration section."""

    model_config = ConfigDict(extra="forbid")

    # FR-52 thresholds:
    fuzzy_threshold: float = 0.85
    llm_confidence_threshold: float = 0.75

    # FR-85 classification ladder config:
    doc_type_filename_rules_path: Path = Path(
        "customizations/<customer_id>/doc_type_filename_rules.yaml"
    )
    doc_type_classifier_threshold: float = 0.85

    # FR-79 over-routing detection threshold (count-based per 2026-06-09 review):
    route_attachment_max_matches_threshold: int = 10

    # FR-86 / Step F post-write gates:
    plm_upload_enabled: bool = True
    review_required_enabled: bool = True

    # NSD root (passes to NSDPath helpers indirectly via storage):
    nsd_root: Path = Path("/mnt/hilda/internal")


class EmailServiceConfig(BaseModel):
    """Top-level email_service config -- bundles the three sub-configs."""

    model_config = ConfigDict(extra="forbid")

    imap: ImapConfig = ImapConfig()
    smtp: SmtpConfig = SmtpConfig()
    fr52: Fr52Config = Fr52Config()

    @classmethod
    def from_sources(
        cls,
        config_path: Path | None = None,
        cli_overrides: dict[str, object] | None = None,
    ) -> "EmailServiceConfig":
        """3-tier precedence: CLI > env > config file > defaults."""
        data: dict[str, object] = {}
        path = config_path or _DEFAULT_CONFIG_PATH
        if path.exists():
            with path.open("r", encoding="utf-8") as f:
                data.update(json.load(f))
        # env vars (HILDA_EML_*) override file:
        env_map = {
            ("imap", "host"): "HILDA_EML_IMAP_HOST",
            ("imap", "port"): "HILDA_EML_IMAP_PORT",
            ("smtp", "host"): "HILDA_EML_SMTP_HOST",
            ("smtp", "port"): "HILDA_EML_SMTP_PORT",
            ("smtp", "from_addr"): "HILDA_EML_SMTP_FROM",
        }
        for (section, field_name), env_key in env_map.items():
            if env_key in os.environ:
                data.setdefault(section, {})  # type: ignore[arg-type]
                data[section][field_name] = os.environ[env_key]  # type: ignore[index]
        for key, value in (cli_overrides or {}).items():
            if value is not None:
                data[key] = value
        return cls(**data)
