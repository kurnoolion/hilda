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

__all__ = [
    "AuditWriter",
    "BatchDispatchResult",
    "CarrierUploadResult",
    "CustomerAdapter",
    "UploadTriplet",
]


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


# ---------------------------------------------------------------------------
# CARRIER-BATCH-1 (2026-09-20): async batch upload — one adapter call per
# (customer, device, milestone) scope carrying N triplets, dispatched to the
# corp-side Jenkins uploader in one job. Jenkins uploads in-session (one
# login/crawl per unique target_dir, then bulk upload) and POSTs per-file
# results back to a signed HILDA callback URL. See D-217.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UploadTriplet:
    """One file's worth of instructions for the batch uploader.

    Variant X locked 2026-09-20: `source_dir` is the corp-box directory where
    the file physically lives; `filename` is the file's basename (never a
    prefixed path); `target_dir` is the Drive-side folder tree beneath
    <binding-root>/<device_id>/<milestone_name>/, and MUST already include any
    archive subdir when from_zip=True (HILDA appends it before dispatch).

    Uploader reads bytes at `<source_dir>/<filename>` locally and writes to
    `<target_dir>/<filename>` on Drive, mkdir-navigating `target_dir` as needed.

    `triplet_id` / `item_id` / `file_hash` are HILDA-side tags: `triplet_id`
    is echoed by the uploader in every per-file callback so HILDA can map
    results back to the exact triplet even when two triplets share filename +
    target_dir. `item_id` + `file_hash` let HILDA aggregate results per item
    for the RFS -> SubmittedToCustomer transition, and let the retry beat
    look up the original persist location if a per-file retry is needed.
    """

    triplet_id: str            # HILDA-minted opaque id per triplet; echoed back by uploader
    item_id: str               # delivery_item_id this file belongs to
    file_hash: str             # SHA-256 hex of the file bytes at ingest time
    source_dir: str            # corp-box absolute local directory holding the file
    filename: str              # basename only (never a prefixed path)
    target_dir: str            # Drive subdirectory tree beneath <binding-root>/<device>/<milestone>


@dataclass(frozen=True)
class BatchDispatchResult:
    """Return shape from CustomerAdapter.upload_attachments_batch.

    The batch call is NON-BLOCKING — it dispatches to the uploader and
    returns immediately after the uploader accepts the job. Per-file
    outcomes arrive asynchronously via the HILDA callback endpoint, keyed
    by `triplet_id`. The retry beat handles anything that hasn't reported
    by `timeout_at`.

    `dispatched=True` and non-empty `batch_id` mean the uploader accepted
    the job — no promise about individual file outcomes. `dispatched=False`
    means the adapter never got the job to the uploader (cred failure,
    network, binding raise); HILDA records the batch as `failed_dispatch`
    and every triplet is immediately eligible for per-file retry.
    """

    dispatched: bool                     # True = uploader accepted the batch
    batch_id: str                        # HILDA-minted opaque id (BATCH-<uuid>)
    dispatched_at: datetime              # UTC when HILDA persisted the batch row
    expected_triplet_count: int          # len(triplets) at dispatch
    error_code: str | None = None        # CAD-EXXX when dispatched=False
    error_detail: str | None = None      # bounded enum token (NFR-2)
    jenkins_build_id: str | None = None  # optional cross-ref to uploader's own job id


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
        """Upload ONE file to the customer's Drive folder (slow per-file path).

        Retained as the retry-fallback path for the async batch dispatched via
        `upload_attachments_batch` — the reconcile beat calls this method
        per-file for any triplet that timed out or failed under the batch.
        Also the sole path used by adapters that don't override
        `_invoke_binding_batch` for a fast Jenkins-batch job.
        """
        ...

    async def upload_attachments_batch(
        self,
        *,
        device_id: str,                        # Model_No shared across all triplets in the batch
        milestone_name: str,                   # milestone YAML key shared across all triplets
        triplets: list["UploadTriplet"],       # one per file to upload
        customer_delivery_info: str,           # per-row from Deliverables SP list (all triplets share it)
        callback_url: str,                     # HMAC-signed HILDA endpoint; uploader POSTs per-file results here
        batch_id: str | None = None,           # caller-provided so callback_url HMAC id matches persisted row
    ) -> "BatchDispatchResult":
        """Dispatch a batch upload to the corp-side uploader (Jenkins).

        Non-blocking: returns after the uploader accepts the job (typically
        seconds), NOT after uploads complete. Per-file outcomes flow back to
        HILDA asynchronously via `callback_url` — one POST per triplet,
        HMAC-verified on the receiving endpoint, echoing `triplet_id` so
        HILDA maps each result to the exact triplet it dispatched.

        Batch scope: one call per (customer, device_id, milestone_name) —
        the uploader groups triplets internally by unique `target_dir` and
        pays the Selenium login + folder navigation cost once per unique
        Drive folder rather than per file. A 300-file batch that lands in
        25 distinct target_dirs pays that cost 25x, not 300x.

        Credentials (pm_id, pm_password, totp_code) are resolved by the
        adapter from credential_service once for the whole batch and passed
        to the binding as batch-scope args. Never per-triplet.

        On dispatch failure (cred error, network, binding raise), returns
        BatchDispatchResult(dispatched=False, error_code=CAD-EXXX). The
        caller (submit_to_carrier_task) marks every triplet as needing
        per-file retry immediately; the reconcile beat picks them up on
        the next tick.
        """
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
