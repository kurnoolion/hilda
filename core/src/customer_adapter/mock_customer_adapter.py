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

from .protocol import CarrierUploadResult

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

    async def health(self) -> dict[str, Any]:
        return {
            "ready": True,
            "customer_id": self.customer_id,
            "ntp_skew_s": None,
        }
