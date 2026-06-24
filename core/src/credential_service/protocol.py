"""Credential data types. Anchors [D-019] [D-038]; serves FR-51, FR-42, NFR-3, NFR-4.

See core/src/credential_service/MODULE.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal

__all__ = [
    "AuthType",
    "Credential",
    "CredentialScope",
    "SystemType",
    "SYSTEM_CRED_SCOPE",
    "SYSTEM_ENV_PREFIX",
    "SYSTEM_SUBTREE",
]

AuthType = Literal["api_token", "basic", "basic_totp", "ntlm", "kerberos", "oauth2_bearer"]


class SystemType(str, Enum):
    """Bounded set of external system kinds credential_service serves credentials for.

    Closed enum by design (not an extensible registry): credential system types
    correspond to HILDA modules; adding one is a code change anyway.
    Tri-backend LLM split per [D-052] impl note 2026-06-08.
    """

    # Per-system credential scope (architect lock 2026-06-21):
    ISSUE_TRACKER = "issue_tracker"      # customer JIRA only in Ph-1 (per-(account,customer) per FR-25 (b));
                                          # corp PLM is NO-credential pattern (d) per FR-25 (a) — lookups raise CRD-E001
    MESSENGER = "messenger"              # NO HILDA-credential Ph-1/Ph-2 — IP-allowlist + gateway-side auth;
                                          # kept in enum for forward-compat; lookups raise CRD-E001
    CUSTOMER = "customer"                # customer portal / submission system per [D-054]; per-customer (Ph-1: Google Drive per FR-19/77)
    EMAIL = "email"                      # IMAP/SMTP mailbox per [D-016]; single shared
    SHAREPOINT = "sharepoint"            # SP service account (NTLM/Kerberos); single shared
    LLM_OLLAMA_A4000 = "llm_ollama_a4000"  # Ollama on RTX A4000 box (lab subnet); single shared
    LLM_VLLM_DGX = "llm_vllm_dgx"          # vLLM on DGX Spark box (lab subnet); single shared
    LLM_CORP_LLM = "llm_corp_llm"          # corp on-prem LLM (off-lab) per [D-007]; single shared


class CredentialScope(str, Enum):
    """Per-system credential lookup scope.

    Added 2026-06-21 per FR-25 (b) cascade lock 2026-06-19 (per-(account, customer)
    customer JIRA) + FR-19/77 (per-customer Google Drive) + architect lock 2026-06-21
    (no-HILDA-credential for corp PLM via ISSUE_TRACKER pattern (d) + corp messenger).
    """

    SHARED = "shared"
    """Single shared ops-team credential at env_dir/<system>.enc.env."""

    PER_ACCOUNT_PER_CUSTOMER = "per_account_per_customer"
    """Per-(account_id, customer_id) credential at
    env_dir/<subtree>/<account_id>/<customer_id>.enc.env. Used for customer JIRA
    per FR-25 (b) — each carrier requires identifiable individual accounts for
    audit per carrier governance."""

    PER_CUSTOMER = "per_customer"
    """Per-customer credential at env_dir/<subtree>/<customer_id>.enc.env. Used
    for customer adapter (Google Drive per FR-19/77) — Ph-1 single carrier MMK
    expands trivially when additional carriers onboard."""

    NO_CREDENTIAL = "no_credential"
    """No HILDA-side credential — auth handled gateway-side via IP-allowlist +
    fixed-key infrastructure. HILDA passes corp identity as API parameter, not
    credential. Callers must use the IP-allowlist + identity-assertion pattern
    instead; get_credential() raises CRD-E001 for these system_types."""


# Per-system credential scope — controls how SopsCredentialService scans for
# credential files in env_dir and how get_credential routes lookups.
# Anchored in FR-25 (a)/(b) + FR-19/77 + architect lock 2026-06-21.
SYSTEM_CRED_SCOPE: dict[SystemType, CredentialScope] = {
    # customer JIRA only in Ph-1; corp PLM uses pattern (d) per FR-25 (a):
    SystemType.ISSUE_TRACKER: CredentialScope.PER_ACCOUNT_PER_CUSTOMER,
    # corp messenger gateway uses IP-allowlist + gateway-side auth:
    SystemType.MESSENGER: CredentialScope.NO_CREDENTIAL,
    # customer portal upload (Ph-1: Google Drive per FR-19/77):
    SystemType.CUSTOMER: CredentialScope.PER_CUSTOMER,
    # Single shared (HILDA-team scope) below:
    SystemType.EMAIL: CredentialScope.SHARED,
    SystemType.SHAREPOINT: CredentialScope.SHARED,
    SystemType.LLM_OLLAMA_A4000: CredentialScope.SHARED,
    SystemType.LLM_VLLM_DGX: CredentialScope.SHARED,
    SystemType.LLM_CORP_LLM: CredentialScope.SHARED,
}


# Subtree directory name per per-(account, customer) / per-customer system.
# When scope == PER_ACCOUNT_PER_CUSTOMER: env_dir/<subtree>/<account_id>/<customer_id>.enc.env
# When scope == PER_CUSTOMER: env_dir/<subtree>/<customer_id>.enc.env
SYSTEM_SUBTREE: dict[SystemType, str] = {
    SystemType.ISSUE_TRACKER: "customer_jira",
    SystemType.CUSTOMER: "customer",
}


# Env-var prefix per system inside its decrypted .enc.env, e.g. HILDA_ITR_AUTH_TYPE.
# Module-prefix abbreviations where one exists (issue_tracker.enc.env carries HILDA_ITR_*
# per MODULE.md file layout); LLM backends use their full system_type value uppercased.
SYSTEM_ENV_PREFIX: dict[SystemType, str] = {
    SystemType.ISSUE_TRACKER: "ITR",
    SystemType.MESSENGER: "MSG",
    SystemType.CUSTOMER: "CSA",  # renamed from CAD on 2026-06-21 — align with diagnostics PREFIX_REGISTRY
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
    # basic_totp (added per [D-116] D15 2026-06-25 — customer_adapter Google Drive):
    # long-lived base32 seed captured during MFA setup; HILDA generates ephemeral
    # 6-digit code per upload via pyotp.TOTP(totp_seed).now(). NEVER logged.
    totp_seed: str | None = None
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
            "basic_totp": ("username", "password", "totp_seed"),
            "ntlm": ("username", "password"),
            "kerberos": ("keytab_path",),
            "oauth2_bearer": ("bearer",),
        }
        carriers = ("api_token", "username", "password", "keytab_path", "bearer", "totp_seed")
        needed = required[self.auth_type]
        for carrier in carriers:
            value = getattr(self, carrier)
            if carrier in needed and value is None:
                return False
            if carrier not in needed and value is not None:
                return False
        return True
