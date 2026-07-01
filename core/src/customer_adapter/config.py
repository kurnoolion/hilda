"""customer_adapter operational config -- 3-tier precedence per [D-025] + [D-038].

Slimmed per [D-116] D14 cascade 2026-06-25 -- binding owns selenium / Chromium /
session pool / upload timeout internally. HILDA-side config carries only:
- customizations_dir (where per-customer subclass modules live)
- ntp_skew_warn_s   (CAD-W005 threshold)
- diagnostic_ntp_check (disable in air-gapped test rigs)
- nsd_volume_prefix (2026-06-30 architect lock -- absolute-path composition
  for source_dir passed to uploadAttachment; document_item_association stores
  paths as "internal/<customer>/..." relative, but the adapter runs against
  the host filesystem and needs full absolute paths. Prepending this prefix
  keeps the container-vs-host topology in config rather than task code.)
- customers (2026-07-01 architect lock -- per-customer credentials for the
  Ph-1 JSON credential-storage pattern, replacing the sops-encrypted env
  files for customer_adapter. Fed to JsonFileCredentialService which
  satisfies the same CredentialService Protocol the adapter depends on --
  GoogleDriveBaseAdapter code path unchanged. Mirrors the plaintext-JSON
  simplicity of sharepoint_integration.json.)
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["CustomerAdapterConfig", "CustomerCredEntry"]

_DEFAULT_CONFIG_PATH = Path("config/customer_adapter.json")

_ENV_MAP = {
    "customizations_dir":    "HILDA_CUSTOMER_ADAPTER_CUSTOMIZATIONS_DIR",
    "ntp_skew_warn_s":       "HILDA_CUSTOMER_ADAPTER_NTP_SKEW_WARN_S",
    "diagnostic_ntp_check":  "HILDA_CUSTOMER_ADAPTER_DIAGNOSTIC_NTP_CHECK",
    "nsd_volume_prefix":     "HILDA_CUSTOMER_ADAPTER_NSD_VOLUME_PREFIX",
    # customers deliberately excluded from _ENV_MAP -- secrets don't come
    # through the env layer; JSON file + filesystem-permissions-based
    # confidentiality only.
}


class CustomerCredEntry(BaseModel):
    """One customer's Google Drive credential set for the Ph-1 JSON path.

    Field names mirror `credential_service.Credential` exactly so the JSON ->
    Credential lift in JsonFileCredentialService is trivial. All 4 fields
    required for the basic_totp auth flow used by the customer adapter:

      pm_id      -- PM attribution token (surfaces in CommunicationLog rows)
      username   -- Google account user_id (passed as pm_id to uploadAttachment)
      password   -- Plaintext (passed as pm_password to uploadAttachment)
      totp_seed  -- Long-lived base32 MFA seed. HILDA generates the ephemeral
                    6-digit totp_code per call via pyotp.TOTP(totp_seed).now().
                    NEVER logged.

    All secret fields carry `repr=False` so `str(config)` in error paths
    doesn't leak them to logs. NFR-2 discipline.
    """
    model_config = ConfigDict(extra="forbid")

    pm_id:     str
    username:  str
    password:  str = Field(repr=False)
    totp_seed: str = Field(repr=False)


class CustomerAdapterConfig(BaseModel):
    """Operational config -- environment-switching values only."""

    model_config = ConfigDict(extra="forbid")

    customizations_dir:   Path  = Path("/etc/hilda/customizations/customer_adapter")
    ntp_skew_warn_s:      float = 25.0     # CAD-W005 threshold; TOTP window ~30s
    diagnostic_ntp_check: bool  = True     # disable in air-gapped test rigs
    # 2026-06-30: host-side absolute-path prefix for NSD volume. Prepended to
    # `local_nsd_path` from document_item_association (which stores relative
    # paths like "internal/MMK/SM-S671U1/...") before passing source_dir to
    # the adapter's uploadAttachment(). Empty string = no prefix (tests /
    # already-absolute paths / same-container adapter).
    nsd_volume_prefix:    str   = ""
    # 2026-07-01: per-customer credentials for the Ph-1 JSON path. Keyed by
    # customer_id (matches CustomerTemplateBase.customer_id). Empty default
    # falls back to SopsCredentialService via bootstrap wiring, preserving
    # backwards compatibility for existing sops-based deploys.
    customers: dict[str, CustomerCredEntry] = Field(default_factory=dict)

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
