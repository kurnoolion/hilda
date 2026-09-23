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
    "BatchJobStatus",
    "BatchKillResult",
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


@dataclass(frozen=True)
class BatchJobStatus:
    """Return shape from CustomerAdapter.is_batch_job_completed.

    CARRIER-RETRY-2 (2026-09-23, D-218). Wraps the corp-side
    `is_jenkins_build_completed` API, whose contract is an integer return:
    0 = the build has finished (whatever its outcome), non-zero = still
    running / unknown / probe failed. HILDA treats ONLY 0 as "safe to
    re-dispatch"; every non-zero value means the job may still be holding a
    Selenium session against the same Drive folder, and re-dispatching on top
    of it risks duplicate uploads.

    `probe_failed=True` distinguishes "the API answered non-zero" from "we
    couldn't reach the API at all". Both block re-dispatch, but only the
    latter is an adapter/network problem worth alerting on separately.
    """

    completed: bool                  # True iff the underlying API returned 0
    raw_code: int | None             # verbatim API return; None when probe_failed
    probe_failed: bool = False       # adapter/network error reaching the API
    error_detail: str | None = None  # bounded token (NFR-2)


@dataclass(frozen=True)
class BatchKillResult:
    """Return shape from CustomerAdapter.kill_batch_job.

    Wraps the corp-side `kill_jenkins_job` API (0 = killed successfully).
    HILDA calls this only when `is_batch_job_completed` still reports
    not-completed at the batch's `kill_at` deadline — i.e. the job is
    presumed stalled and must be torn down before the pending subset is
    re-dispatched under the same batch_id.
    """

    killed: bool                     # True iff the underlying API returned 0
    raw_code: int | None             # verbatim API return; None when the call raised
    error_detail: str | None = None  # bounded token (NFR-2)


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
        """DEPRECATED (CARRIER-UNIFY / D-218) — upload ONE file, slow path.

        No longer called by any HILDA task. Until D-218 this was the
        retry-fallback for triplets that timed out under a batch; retry is now
        a re-dispatch of the batch's pending subset through
        `upload_attachments_batch`, so a single-file upload is just a batch of
        one — same batch_id/triplet_id plumbing, same callback path, one code
        path to maintain instead of two.

        Retained on the Protocol so existing per-customer subclasses under
        `customizations/customer_adapter/` keep satisfying it without edits.
        Implementations may keep it, or raise NotImplementedError.
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
        BatchDispatchResult(dispatched=False, error_code=CAD-EXXX). HILDA
        persists the batch as `failed_dispatch`; the reconcile beat admits
        that state and re-dispatches on the next tick, up to
        batch_max_retry_count.

        RE-DISPATCH (CARRIER-RETRY-3): the beat calls this method again with
        the SAME `batch_id` and only the still-pending triplets, plus a
        freshly-minted `callback_url` (the old HMAC token has a per-attempt
        TTL). Implementations must not assume `triplets` is the batch's
        original full set.
        """
        ...

    async def is_batch_job_completed(
        self,
        *,
        batch_id: str,
        jenkins_build_id: str | None = None,
    ) -> "BatchJobStatus":
        """Has the uploader's job for this batch finished?

        CARRIER-RETRY-2 (D-218). Wraps the corp-side
        `is_jenkins_build_completed` API. The reconcile beat calls this before
        every re-dispatch: a batch passing `timeout_at` is NOT by itself proof
        the job is done — with ~300 files a legitimately-slow job can outrun
        the window, and re-dispatching under it would double-upload.

        Only `completed=True` (API returned 0) authorises a re-dispatch. On
        any non-zero code the beat waits; once the batch also passes
        `kill_at` it calls `kill_batch_job` first.

        Never raises — probe failures come back as
        BatchJobStatus(completed=False, probe_failed=True).
        """
        ...

    async def kill_batch_job(
        self,
        *,
        batch_id: str,
        jenkins_build_id: str | None = None,
    ) -> "BatchKillResult":
        """Tear down a stalled uploader job so the batch can be re-dispatched.

        CARRIER-RETRY-2 (D-218). Wraps the corp-side `kill_jenkins_job` API.
        Called only from the reconcile beat, only when
        `is_batch_job_completed` still reports not-completed at the batch's
        `kill_at` deadline.

        Never raises — failures come back as
        BatchKillResult(killed=False, error_detail=...). A failed kill blocks
        the re-dispatch for that tick rather than risking two live jobs
        writing to the same Drive folder.
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
