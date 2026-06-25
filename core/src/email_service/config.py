"""email_service operational config -- 3-tier precedence per [D-025] + [D-038].

Four Pydantic models per MODULE.md Configuration section:
- ImapConfig    -- IMAP receiver
- SmtpConfig    -- SMTP sender
- EwsConfig     -- EWS receiver+sender per [D-132] corp Exchange path
- Fr52Config    -- FR-52 / FR-85 / FR-86 router thresholds + NSD root + post-write gates

Plus EmailServiceConfig.mode discriminator (Literal["imap_smtp", "ews", "mock"])
picks the runtime adapter pair per [D-132]; defaults to "imap_smtp" for backward
compatibility with non-corp deployments.

Credentials are NOT in config -- they flow through credential_service per [D-019].
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

__all__ = ["EmailMode", "ImapConfig", "SmtpConfig", "EwsConfig", "Fr52Config", "EmailServiceConfig"]

EmailMode = Literal["imap_smtp", "ews", "mock"]

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


class EwsConfig(BaseModel):
    """Exchange Web Services receiver+sender config per [D-132].

    Corp Exchange (current version per architect 2026-06-25; Chaitanya sample
    used Build(15, 2) which exchangelib's Version helper maps to Exchange 2019)
    is reached via an explicit service_endpoint (autodiscover disabled to avoid
    AD probing churn). Auth is basic-over-TLS with a service account; the
    username + password flow through credential_service per call (NFR-2 -- never
    cached on the adapter instance).

    The receiver polls the inbox per FR-23 deadline-tiered cadence
    (poll_interval_s default 60s); IMAP IDLE / EWS streaming notifications are
    deferred Ph-1 next pass per architect Q3 lock 2026-06-25.

    The sender appends a CommunicationLog row per FR-42 mirroring the SMTP path.
    """

    model_config = ConfigDict(extra="forbid")

    # exchangelib Configuration() inputs
    service_endpoint: str = "https://mail.corp.example/EWS/Exchange.asmx"
    primary_smtp_address: str = "hilda-noreply@corp.example"   # Account(...) email_id
    access_type: Literal["DELEGATE", "IMPERSONATION"] = "DELEGATE"
    auth_type: Literal["basic"] = "basic"                       # OAuth deferred Ph-2
    timeout_s: int = 120

    # Optional version pin -- Chaitanya's sample used Build(15, 2) which is
    # Exchange 2019. None -> exchangelib auto-detects on first call. Per
    # architect 2026-06-25, Chaitanya's pattern works with the current corp
    # server, so we expose this as override but leave None default.
    exchange_build_major: int | None = None
    exchange_build_minor: int | None = None

    # Receiver behavior
    poll_interval_s: int = 60
    inbox_subfolder: str | None = None         # None -> account.inbox; else inbox / <subfolder>
    folder_processed: str = "Processed"        # MOVE target after read
    fetch_limit: int = 50                      # per-poll cap to bound exchangelib query size

    # Sender behavior
    from_addr: str = "hilda-noreply@corp.example"


class Fr52Config(BaseModel):
    """FR-52 5-step routing + FR-85 2-step classification + FR-86 4-path storage
    matrix thresholds + post-write gates per MODULE.md Configuration section."""

    model_config = ConfigDict(extra="forbid")

    # FR-52 thresholds:
    fuzzy_threshold: float = 0.85
    llm_confidence_threshold: float = 0.75

    # FR-85 classification ladder config.
    # Path migration 2026-06-27 architect direction: moved from
    # `customizations/<customer_id>/` -> `customizations/template_schemas/<customer_id>/`
    # for consistency with template.yaml + folder_routing.yaml (all per-customer
    # config files now live under template_schemas/<customer_id>/).
    doc_type_filename_rules_path: Path = Path(
        "customizations/template_schemas/<customer_id>/doc_type_filename_rules.yaml"
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
    """Top-level email_service config -- bundles the sub-configs + adapter mode.

    `mode` per [D-132]: "imap_smtp" picks ImapReceiver+SmtpSender (default; works
    for non-corp deployments + the mock harness); "ews" picks EwsReceiver+EwsSender
    for corp Exchange deployments; "mock" lets the harness wire MockImapReceiver +
    MockSmtpSender (no Mock*Ews* needed -- MockImapReceiver satisfies the EmailReceiver
    Protocol regardless of upstream transport).
    """

    model_config = ConfigDict(extra="forbid")

    mode: EmailMode = "imap_smtp"
    imap: ImapConfig = ImapConfig()
    smtp: SmtpConfig = SmtpConfig()
    ews: EwsConfig = EwsConfig()
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
            ("ews", "service_endpoint"): "HILDA_EML_EWS_SERVICE_ENDPOINT",
            ("ews", "primary_smtp_address"): "HILDA_EML_EWS_PRIMARY_SMTP_ADDRESS",
            ("ews", "from_addr"): "HILDA_EML_EWS_FROM",
        }
        for (section, field_name), env_key in env_map.items():
            if env_key in os.environ:
                data.setdefault(section, {})  # type: ignore[arg-type]
                data[section][field_name] = os.environ[env_key]  # type: ignore[index]
        # Top-level mode env override:
        if "HILDA_EML_MODE" in os.environ:
            data["mode"] = os.environ["HILDA_EML_MODE"]
        for key, value in (cli_overrides or {}).items():
            if value is not None:
                data[key] = value
        return cls(**data)
