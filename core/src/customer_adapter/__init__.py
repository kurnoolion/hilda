"""customer_adapter -- single Protocol-mediated surface for HILDA's outbound
customer submission per FR-19 / FR-42 / FR-57 / FR-77 / FR-80.

Per [D-116] Ratified 2026-06-25 thin-wrapper strategy: `GoogleDriveBaseAdapter`
is a Protocol-conformant thin wrapper around the user's pre-existing
selenium-backed Google Drive binding. HILDA owns Protocol contract +
`CarrierUploadResult` shape + `CommunicationLog` per FR-42 + per-call
credential composition (3-tuple + pyotp TOTP) + CAD-W005 clock-skew warning.
Per-customer subclass at `customizations/customer_adapter/<customer_id>_adapter.py`
overrides `_invoke_binding(...)` with the concrete binding-import + call body
(filled in by Cline on Work PC per [D-027] Teacher/Student split).

See `core/src/customer_adapter/MODULE.md`.
"""
from core.src.customer_adapter.config import CustomerAdapterConfig, CustomerCredEntry
from core.src.customer_adapter.google_drive_base import GoogleDriveBaseAdapter
from core.src.customer_adapter.mock_customer_adapter import MockCustomerAdapter
from core.src.customer_adapter.protocol import (
    AuditWriter,
    CarrierUploadResult,
    CustomerAdapter,
)
from core.src.customer_adapter.totp import current_totp, ntp_skew_seconds

__all__ = [
    "AuditWriter",
    "CarrierUploadResult",
    "CustomerAdapter",
    "CustomerAdapterConfig",
    "CustomerCredEntry",
    "GoogleDriveBaseAdapter",
    "MockCustomerAdapter",
    "build_credential_service",
    "current_totp",
    "ntp_skew_seconds",
]


def build_credential_service(cfg: CustomerAdapterConfig):
    """Ph-1 credential-service factory per architect 2026-07-01.

    Chooses the credential backend based on config shape:
      - If cfg.customers is populated -> JsonFileCredentialService
        (plaintext JSON per customer in customer_adapter.json; simplest;
        mirrors sharepoint_integration.json pattern).
      - Otherwise -> SopsCredentialService (sops-encrypted env files under
        /etc/hilda/credentials/customer/<customer_id>.enc.env; backwards
        compatible for existing deploys).

    Called from the per-customer subclass instantiation site
    (customizations/customer_adapter/<customer_id>_adapter.py):

        cfg = CustomerAdapterConfig.from_sources()
        adapter = MMKGoogleDriveAdapter(
            config=cfg,
            credential_service=build_credential_service(cfg),
            audit_writer=<sink>,
        )

    Callers that want a specific service (tests, --diagnostic CLI) can
    construct it directly and bypass this factory.
    """
    if cfg.customers:
        from core.src.credential_service.service import JsonFileCredentialService
        return JsonFileCredentialService(cfg.customers)
    import os
    from pathlib import Path
    from core.src.credential_service.service import SopsCredentialService
    age_key_env = os.environ.get("SOPS_AGE_KEY_FILE")
    kwargs: dict[str, object] = {}
    if age_key_env:
        kwargs["age_key_path"] = Path(age_key_env)
    return SopsCredentialService(**kwargs)
