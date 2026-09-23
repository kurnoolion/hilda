"""MockCustomerAdapter -- in-process mock for tests + --mock CLI mode.

No binding, no credential_service, no selenium, no Chromium. Returns canned
CarrierUploadResult per registered (device_id, milestone_name, target_dir,
filename) tuple.

NFR-2: same privacy convention as the real adapter -- never log credentials
or file content.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .protocol import (
    BatchDispatchResult,
    BatchJobStatus,
    BatchKillResult,
    CarrierUploadResult,
    UploadTriplet,
)

__all__ = ["MockCustomerAdapter"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class MockCustomerAdapter:
    """In-process mock honoring the CustomerAdapter Protocol."""

    source_system: str = "mock_customer"
    customer_id: str = "mock_customer"

    def __init__(self) -> None:
        # Key: (device_id, milestone_name, target_dir, filename) -> CarrierUploadResult.
        self._registered: dict[tuple[str, str, str, str], CarrierUploadResult] = {}
        # Default behavior when unknown: return success=False with CAD-E004.
        self._default_success: bool = False
        # Call log for test assertions.
        self.calls: list[tuple[str, str, str, str]] = []
        # CARRIER-BATCH-6 (2026-09-20): captures for the batch surface so
        # tests can assert on what was dispatched (and simulate callbacks).
        self.batch_calls: list[dict[str, Any]] = []
        # If set to True, upload_attachments_batch returns dispatched=False
        # with CAD-E004 for the whole batch (simulates uploader-side outage).
        self._batch_dispatch_fails: bool = False
        # CARRIER-RETRY-2 (2026-09-23): job-control surface. Defaults mirror
        # the base adapter's unsupported-hook behaviour (completed, killable)
        # so existing tests see no change; retry tests flip them.
        self._job_completed_code: int = 0
        self._job_probe_raises: bool = False
        self._kill_code: int = 0
        # Call logs for retry-path assertions.
        self.job_status_calls: list[dict[str, Any]] = []
        self.kill_calls: list[dict[str, Any]] = []

    def register_upload_result(
        self,
        device_id: str,
        milestone_name: str,
        target_dir: str,
        filename: str,
        result: CarrierUploadResult,
    ) -> None:
        self._registered[(device_id, milestone_name, target_dir, filename)] = result

    def set_default_success(self, success: bool) -> None:
        """Configure fallback for unregistered tuples (default False)."""
        self._default_success = success

    async def upload_attachment(
        self,
        device_id: str,
        milestone_name: str,
        source_dir: Path,        # accepted but unused
        target_dir: str,
        filename: str,
        customer_delivery_info: str = "drive.google.com",  # default for tests; D-126
    ) -> CarrierUploadResult:
        # Per D-126 cascade 2026-06-26: mock validates customer_delivery_info
        # non-empty (matches GoogleDriveBaseAdapter behavior).
        if not customer_delivery_info:
            now = _utc_now()
            return CarrierUploadResult(
                success=False,
                uploaded_filename=filename,
                device_id=device_id,
                milestone_name=milestone_name,
                target_dir=target_dir,
                upload_started_at=now,
                upload_completed_at=now,
                error_code="CAD-E010",
                error_detail="customer_delivery_info_missing",
            )
        key = (device_id, milestone_name, target_dir, filename)
        self.calls.append(key)
        registered = self._registered.get(key)
        if registered is not None:
            return registered
        now = _utc_now()
        return CarrierUploadResult(
            success=self._default_success,
            uploaded_filename=filename,
            device_id=device_id,
            milestone_name=milestone_name,
            target_dir=target_dir,
            upload_started_at=now,
            upload_completed_at=now,
            error_code=None if self._default_success else "CAD-E004",
            error_detail=None if self._default_success else "mock_unregistered",
        )

    def set_batch_dispatch_fails(self, fails: bool) -> None:
        """Simulate an uploader-side dispatch failure for the batch path."""
        self._batch_dispatch_fails = fails

    async def upload_attachments_batch(
        self,
        *,
        device_id: str,
        milestone_name: str,
        triplets: list[UploadTriplet],
        customer_delivery_info: str = "drive.google.com",
        callback_url: str = "",
        batch_id: str | None = None,
    ) -> BatchDispatchResult:
        """CARRIER-BATCH-6: mock async batch. Captures the call for test
        assertions and does NOT drive per-file callbacks -- tests that want to
        exercise the callback endpoint POST to it directly."""
        import uuid as _uuid
        now = _utc_now()
        if not batch_id:
            batch_id = f"BATCH-{_uuid.uuid4().hex[:16]}"
        if not customer_delivery_info:
            return BatchDispatchResult(
                dispatched=False, batch_id=batch_id, dispatched_at=now,
                expected_triplet_count=len(triplets),
                error_code="CAD-E010",
                error_detail="customer_delivery_info_missing",
            )
        self.batch_calls.append({
            "batch_id":               batch_id,
            "device_id":              device_id,
            "milestone_name":         milestone_name,
            "triplets":               list(triplets),
            "customer_delivery_info": customer_delivery_info,
            "callback_url":           callback_url,
        })
        if self._batch_dispatch_fails:
            return BatchDispatchResult(
                dispatched=False, batch_id=batch_id, dispatched_at=now,
                expected_triplet_count=len(triplets),
                error_code="CAD-E004", error_detail="mock_dispatch_fail",
            )
        return BatchDispatchResult(
            dispatched=True, batch_id=batch_id, dispatched_at=now,
            expected_triplet_count=len(triplets),
            jenkins_build_id=f"mock-build-{batch_id[-8:]}",
        )

    # -- CARRIER-RETRY-2 job control ------------------------------------

    def set_job_completed_code(self, code: int) -> None:
        """0 = job finished (re-dispatch allowed); non-zero = still running."""
        self._job_completed_code = code

    def set_job_probe_raises(self, raises: bool) -> None:
        """Simulate an unreachable job-status API (probe_failed path)."""
        self._job_probe_raises = raises

    def set_kill_code(self, code: int) -> None:
        """0 = kill succeeded; non-zero = kill failed."""
        self._kill_code = code

    async def is_batch_job_completed(
        self, *, batch_id: str, jenkins_build_id: str | None = None,
    ) -> BatchJobStatus:
        self.job_status_calls.append(
            {"batch_id": batch_id, "jenkins_build_id": jenkins_build_id}
        )
        if self._job_probe_raises:
            return BatchJobStatus(
                completed=False, raw_code=None, probe_failed=True,
                error_detail="mock_probe_failure",
            )
        code = self._job_completed_code
        return BatchJobStatus(completed=(code == 0), raw_code=code)

    async def kill_batch_job(
        self, *, batch_id: str, jenkins_build_id: str | None = None,
    ) -> BatchKillResult:
        self.kill_calls.append(
            {"batch_id": batch_id, "jenkins_build_id": jenkins_build_id}
        )
        code = self._kill_code
        return BatchKillResult(killed=(code == 0), raw_code=code)

    async def health(self) -> dict[str, Any]:
        return {
            "ready": True,
            "customer_id": self.customer_id,
            "ntp_skew_s": None,
        }
