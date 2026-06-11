"""Credential data types. Anchors [D-019] [D-038]; serves FR-51, FR-42, NFR-3, NFR-4.

See core/src/credential_service/MODULE.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

__all__ = ["AuthType", "Credential", "SystemType", "SYSTEM_ENV_PREFIX"]

AuthType = Literal["api_token", "basic", "ntlm", "kerberos", "oauth2_bearer"]


class SystemType(str, Enum):
    """Bounded set of external system kinds credential_service serves credentials for.

    Closed enum by design (not an extensible registry): credential system types
    correspond to HILDA modules; adding one is a code change anyway.
    Tri-backend LLM split per [D-052] impl note 2026-06-08.
    """

    ISSUE_TRACKER = "issue_tracker"      # corp PLM (via corp_plm_gateway) + customer JIRA
    MESSENGER = "messenger"              # corp messenger (via corp_messenger_gateway)
    CUSTOMER = "customer"                # customer portal / submission system per [D-054]
    EMAIL = "email"                      # IMAP/SMTP mailbox per [D-016]
    SHAREPOINT = "sharepoint"            # SP service account (NTLM/Kerberos)
    LLM_OLLAMA_A4000 = "llm_ollama_a4000"  # Ollama on RTX A4000 box (lab subnet)
    LLM_VLLM_DGX = "llm_vllm_dgx"          # vLLM on DGX Spark box (lab subnet)
    LLM_CORP_LLM = "llm_corp_llm"          # corp on-prem LLM (off-lab) per [D-007]


# Env-var prefix per system inside its decrypted .enc.env, e.g. HILDA_ITR_AUTH_TYPE.
# Module-prefix abbreviations where one exists (issue_tracker.enc.env carries HILDA_ITR_*
# per MODULE.md file layout); LLM backends use their full system_type value uppercased.
SYSTEM_ENV_PREFIX: dict[SystemType, str] = {
    SystemType.ISSUE_TRACKER: "ITR",
    SystemType.MESSENGER: "MSG",
    SystemType.CUSTOMER: "CAD",
    SystemType.EMAIL: "EML",
    SystemType.SHAREPOINT: "SHP",
    SystemType.LLM_OLLAMA_A4000: "LLM_OLLAMA_A4000",
    SystemType.LLM_VLLM_DGX: "LLM_VLLM_DGX",
    SystemType.LLM_CORP_LLM: "LLM_CORP_LLM",
}


@dataclass(frozen=True)
class Credential:
    """One credential as served to adapters.

    Exactly one value-carrier group is set per credential, by auth_type:
      api_token     → api_token
      basic, ntlm   → username + password
      kerberos      → keytab_path
      oauth2_bearer → bearer
    """

    pm_id: str               # PM attribution token (Ph-1/Ph-2: ops-team identifier)
    system_type: str         # SystemType value
    auth_type: AuthType
    api_token: str | None = None
    username: str | None = None
    password: str | None = None   # never logged, never __repr__'d
    keytab_path: Path | None = None
    bearer: str | None = None
    expires_at: datetime | None = None  # Ph-3+ Vault populates; Ph-1/Ph-2 always None

    def __repr__(self) -> str:
        """Identifiers only — no secret material. Used by diagnostics dumps."""
        return (
            f"Credential(pm_id={self.pm_id!r}, system_type={self.system_type!r}, "
            f"auth_type={self.auth_type!r})"
        )

    __str__ = __repr__

    def value_carriers_consistent(self) -> bool:
        """True when the value carriers populated match auth_type's requirement."""
        required: dict[str, tuple[str, ...]] = {
            "api_token": ("api_token",),
            "basic": ("username", "password"),
            "ntlm": ("username", "password"),
            "kerberos": ("keytab_path",),
            "oauth2_bearer": ("bearer",),
        }
        carriers = ("api_token", "username", "password", "keytab_path", "bearer")
        needed = required[self.auth_type]
        for carrier in carriers:
            value = getattr(self, carrier)
            if carrier in needed and value is None:
                return False
            if carrier not in needed and value is not None:
                return False
        return True
