"""customer_adapter Protocol surface — CustomerAdapter + CarrierUploadResult.

Per [D-116] Ratified 2026-06-25 thin-wrapper strategy. HILDA owns the Protocol
contract + the result shape; binding owns selenium / session / MFA / UI
selectors / target-folder creation / post-upload verification.

See core/src/customer_adapter/MODULE.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

__all__ = ["CarrierUploadResult", "CustomerAdapter", "AuditWriter"]


@dataclass(frozen=True)
class CarrierUploadResult:
    """Per FR-19 + FR-42 + FR-57 -- return shape from CustomerAdapter.upload_attachment.

    Ph-1 shape per [D-116] D16 cascade 2026-06-25: success bool + minimal audit
    metadata; carrier_file_id + carrier_file_url are None Ph-1 (binding returns
    bool only; Drive URL extraction deferred Ph-2 per 5-pattern URL fragmentation).
    """

    success: bool                      # True = uploaded + post-verified by binding
                                       # False = binding completed but post-verify failed
                                       # False also when binding raised + we wrapped
    uploaded_filename: str             # original filename as uploaded (preserved per FR-57)
    device_id: str                     # Model_No passed to the binding
    milestone_name: str                # milestone YAML key passed to the binding
    target_dir: str                    # Drive subdirectory passed to the binding (per-item target_folder)
    upload_started_at: datetime
    upload_completed_at: datetime
    error_code: str | None = None      # CAD-EXXX when success=False or raised
    error_detail: str | None = None    # bounded enum token (NFR-2 -- no proprietary content)
    # Ph-2 forward-looking fields (NOT populated in Ph-1; binding returns only bool):
    carrier_file_id: str | None = None    # Google Drive file ID; Ph-2 per D16 cascade
    carrier_file_url: str | None = None   # Drive viewer URL; Ph-2 per D16 cascade


@runtime_checkable
class CustomerAdapter(Protocol):
    """All callers depend on this Protocol, not on a concrete subclass.

    Implementations:
    - `GoogleDriveBaseAdapter` (Ph-1 reference thin-wrapper)
    - per-customer subclasses under `customizations/customer_adapter/`
    - `MockCustomerAdapter` (tests)

    Per [D-116] D13 (B-α) lock 2026-06-25 -- HILDA passes IDENTIFIER COMPONENTS
    for the Drive target side; binding composes the full Drive path internally
    per (customer_delivery_info, Model_No, milestone_name, target_dir, filename)
    -- per architect lock 2026-06-26 (D-126 cascade closing [D-116] D13 follow-up):
    customer_delivery_info is per-row on the Deliverables SP list (e.g.,
    "drive.google.com"), passed as 9th arg; replaces the previous binding-baked
    customer-root framing. customer_id is implicit via the per-customer subclass
    instance. customer_delivery_modality (e.g., "GoogleDrive") moved to
    template.yaml per-customer config (no longer per-row). Credentials (pm_id,
    pm_password, totp_code) are resolved + injected by the adapter from
    credential_service per call; NEVER passed to Protocol-level callers.

    Raises CAD-E010 when customer_delivery_info is None/empty AND
    no_customer_upload=False (data-config error -- SP UI engineer must
    provision the field when upload is expected).
    """

    source_system: str          # immutable; equals customer_id

    async def upload_attachment(
        self,
        device_id: str,                  # Model_No; e.g., "MODEL-A"
        milestone_name: str,             # milestone YAML key; e.g., "P1"
        source_dir: Path,                # LOCAL NSD directory holding the file
        target_dir: str,                 # Drive subdirectory under <customer_delivery_info>/<Model_No>/<milestone_name>/
        filename: str,                   # basename only; e.g., "abc.report"
        customer_delivery_info: str,     # per-row from Deliverables SP list per D-126; e.g., "drive.google.com"
    ) -> CarrierUploadResult:
        """Upload one file to the customer's Drive folder. See class docstring."""
        ...

    async def health(self) -> dict[str, Any]:
        """Returns {ready: bool, customer_id: str, ntp_skew_s: float | None}.

        Used by --diagnostic CLI mode. Best-effort NTP probe; never raises.
        """
        ...


class AuditWriter(Protocol):
    """Subset of `storage` audit-log interface this module depends on.

    Decouples customer_adapter from concrete storage impl (matches the
    tracker.AuditWriter convention). Concrete impl: `storage.log_communication`
    via a thin shim. Optional injection -- if None passed at construction,
    log emission is silently skipped (Ph-1 graceful for tests + air-gapped rigs).

    NFR-2: only bounded enum tokens + opaque IDs + sizes pass through here.
    NEVER pm_password, NEVER totp_seed, NEVER totp_code, NEVER carrier UI text.
    """

    def write_communication_log(
        self,
        action_type: str,
        delivery_item_id: str | None,
        attribution: dict[str, str],
        details: dict[str, Any],
    ) -> None: ...
