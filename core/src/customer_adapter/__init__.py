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
from core.src.customer_adapter.config import CustomerAdapterConfig
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
    "GoogleDriveBaseAdapter",
    "MockCustomerAdapter",
    "current_totp",
    "ntp_skew_seconds",
]
