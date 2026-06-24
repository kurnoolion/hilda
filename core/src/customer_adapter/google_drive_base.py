"""GoogleDriveBaseAdapter -- thin-wrapper reference class per [D-116] Ratified 2026-06-25.

Per-customer subclass at `customizations/customer_adapter/<customer_id>_adapter.py`
carries the customer-baked Drive root + the concrete `uploadAttachment(...)` call
body. HILDA owns Protocol contract + per-call credential composition + CAD-W005
clock-skew warning + CommunicationLog discipline per FR-42. Binding owns selenium /
session login / MFA / UI selectors / target-folder auto-creation / post-upload
verification.

NFR-2: pm_password + totp_seed + totp_code NEVER logged. CommunicationLog +
ReportRecord fields carry only bounded enum tokens + opaque IDs + sizes + counts.

Per [D-008] async pattern: `upload_attachment` is `async def`; the binding's
sync selenium internals are invoked via `asyncio.to_thread`.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.src.credential_service.protocol import (
    Credential,
    SystemType,
)
from core.src.diagnostics.error_codes import PipelineError

from .config import CustomerAdapterConfig
from .protocol import AuditWriter, CarrierUploadResult
from .totp import current_totp, ntp_skew_seconds

__all__ = ["GoogleDriveBaseAdapter"]


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class GoogleDriveBaseAdapter:
    """Thin-wrapper reference class for Google Drive customer submissions.

    Per-customer subclass overrides `customer_id` + `pm_id` class vars AND
    overrides `_invoke_binding(...)` with the concrete binding import + call
    (per [D-027] Teacher/Student split -- Cline on Work PC fills this in).

    The base class composes credentials (3-tuple + pyotp TOTP code) + calls
    `_invoke_binding` + wraps the bool return into a `CarrierUploadResult` +
    emits the CommunicationLog row.
    """

    # Subclass overrides these to bake in the customer identity.
    source_system: str = ""        # equals customer_id
    customer_id: str = ""
    pm_id: str = ""                # shared HILDA ops-team Google account user_id

    def __init__(
        self,
        config: CustomerAdapterConfig,
        credential_service: Any,
        audit_writer: AuditWriter | None = None,
    ) -> None:
        """Construct the adapter.

        Per [D-116] D14 cascade: NO session pool, NO selector pack, NO
        capability flags -- all binding-internal.

        Args:
          config: CustomerAdapterConfig (carries ntp_skew_warn_s + diagnostic_ntp_check).
          credential_service: anything with async `get_credential(pm_id, system_type, customer_id=None)`
            returning a `Credential`. Concrete impl: SopsCredentialService or MockCredentialService.
          audit_writer: optional AuditWriter for CommunicationLog emission per FR-42.
            If None, log emission is silently skipped (Ph-1 graceful for tests).
        """
        self._config = config
        self._credentials = credential_service
        self._audit = audit_writer

    async def upload_attachment(
        self,
        device_id: str,
        milestone_name: str,
        source_dir: Path,
        target_dir: str,
        filename: str,
    ) -> CarrierUploadResult:
        """Upload one file to the customer's Google Drive folder.

        Flow per [D-116] D12 + D13 (B-α):
        1. Resolve `Credential` (auth_type=basic_totp) via credential_service.
        2. Generate `totp_code` via pyotp.TOTP(cred.totp_seed).now().
        3. Best-effort NTP skew check (if config.diagnostic_ntp_check); CAD-W005 on drift.
        4. Invoke binding's `uploadAttachment(...)` via `_invoke_binding` (wrapped in
           asyncio.to_thread by the subclass since binding's selenium is sync).
        5. Wrap bool return into CarrierUploadResult.
        6. Emit CommunicationLog row per FR-42 (best-effort; non-blocking).
        7. Return result.

        On binding raise (network / selenium / auth-MFA / file-not-found):
        catch, wrap into CarrierUploadResult(success=False, error_code=CAD-EXXX),
        log + emit, return.

        NEVER logs cred.password, cred.totp_seed, or totp_code.
        """
        started = _utc_now()

        # -- Step 1: resolve credentials --
        try:
            cred: Credential = await self._credentials.get_credential(
                self.pm_id, SystemType.CUSTOMER.value, customer_id=self.customer_id,
            )
        except PipelineError as exc:
            completed = _utc_now()
            result = CarrierUploadResult(
                success=False,
                uploaded_filename=filename,
                device_id=device_id,
                milestone_name=milestone_name,
                target_dir=target_dir,
                upload_started_at=started,
                upload_completed_at=completed,
                error_code="CAD-E008",
                error_detail=exc.code_id,
            )
            self._emit_log(result, latency_ms=_latency_ms(started, completed))
            return result

        if cred.auth_type != "basic_totp" or not cred.totp_seed:
            completed = _utc_now()
            result = CarrierUploadResult(
                success=False,
                uploaded_filename=filename,
                device_id=device_id,
                milestone_name=milestone_name,
                target_dir=target_dir,
                upload_started_at=started,
                upload_completed_at=completed,
                error_code="CAD-E008",
                error_detail="auth_type_mismatch",
            )
            self._emit_log(result, latency_ms=_latency_ms(started, completed))
            return result

        # -- Step 2: generate TOTP code (ephemeral; never logged) --
        totp_code = current_totp(cred.totp_seed)

        # -- Step 3: best-effort NTP skew check (CAD-W005 surfaces in audit details) --
        skew_warning: float | None = None
        if self._config.diagnostic_ntp_check:
            skew = await asyncio.to_thread(ntp_skew_seconds)
            if skew is not None and skew > self._config.ntp_skew_warn_s:
                skew_warning = skew

        # -- Step 4: invoke binding (per-customer subclass overrides _invoke_binding) --
        try:
            ok = await self._invoke_binding(
                device_id=device_id,
                milestone_name=milestone_name,
                source_dir=source_dir,
                target_dir=target_dir,
                filename=filename,
                pm_id=cred.username or self.pm_id,
                pm_password=cred.password or "",
                totp_code=totp_code,
            )
            # Don't keep credential material in local frame longer than needed.
            del totp_code
            completed = _utc_now()
            error_code = None if ok else "CAD-E005"   # post-verify failed bucket
            error_detail = None if ok else "post_verify_failed"
        except NotImplementedError as exc:
            del totp_code
            completed = _utc_now()
            ok = False
            error_code = "CAD-E009"
            error_detail = str(exc)[:64] or "binding_not_implemented"
        except TimeoutError:
            del totp_code
            completed = _utc_now()
            ok = False
            error_code = "CAD-E005"
            error_detail = "binding_timeout"
        except FileNotFoundError:
            del totp_code
            completed = _utc_now()
            ok = False
            error_code = "CAD-E005"
            error_detail = "source_file_missing"
        except Exception:
            # NFR-2: don't leak binding-internal exception messages (may carry
            # proprietary selectors / carrier UI text). Bounded token only.
            del totp_code
            completed = _utc_now()
            ok = False
            error_code = "CAD-E004"
            error_detail = "binding_failure"

        # -- Step 5: wrap result --
        result = CarrierUploadResult(
            success=ok,
            uploaded_filename=filename,
            device_id=device_id,
            milestone_name=milestone_name,
            target_dir=target_dir,
            upload_started_at=started,
            upload_completed_at=completed,
            error_code=error_code,
            error_detail=error_detail,
        )

        # -- Step 6: emit CommunicationLog row per FR-42 (best-effort) --
        self._emit_log(
            result,
            latency_ms=_latency_ms(started, completed),
            ntp_skew_warning_s=skew_warning,
        )

        return result

    async def _invoke_binding(
        self,
        device_id: str,
        milestone_name: str,
        source_dir: Path,
        target_dir: str,
        filename: str,
        pm_id: str,
        pm_password: str,
        totp_code: str,
    ) -> bool:
        """Invoke the per-customer Google Drive binding.

        ABSTRACT in the base class -- per-customer subclass at
        `customizations/customer_adapter/<customer_id>_adapter.py` MUST override
        this to import + call the concrete binding (the user's pre-existing
        selenium-backed module per [D-116] D11/D12).

        Subclass implementation pattern:
        ```python
        async def _invoke_binding(self, *, device_id, milestone_name,
                                  source_dir, target_dir, filename,
                                  pm_id, pm_password, totp_code) -> bool:
            from <binding_module> import uploadAttachment  # noqa: N802
            return await asyncio.to_thread(
                uploadAttachment,
                device_id, milestone_name, str(source_dir),
                target_dir, filename, pm_id, pm_password, totp_code,
            )
        ```

        Per [D-027]: HILDA's base class raises NotImplementedError (CAD-E009)
        rather than importing the binding directly -- proprietary binding
        internals never land on public github per NFR-2.
        """
        raise NotImplementedError(
            f"per-customer subclass missing for '{self.customer_id}'"
        )

    async def health(self) -> dict[str, Any]:
        """Returns {ready: bool, customer_id: str, ntp_skew_s: float | None}.

        Used by `--diagnostic` CLI. Best-effort NTP probe; never raises.
        Does NOT exercise the binding (that requires real credentials + Drive
        access -- see `--invoke` mode).
        """
        skew: float | None = None
        if self._config.diagnostic_ntp_check:
            skew = await asyncio.to_thread(ntp_skew_seconds)
        ready = self.customer_id != "" and self.pm_id != ""
        return {
            "ready": ready,
            "customer_id": self.customer_id,
            "ntp_skew_s": skew,
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_log(
        self,
        result: CarrierUploadResult,
        latency_ms: int,
        ntp_skew_warning_s: float | None = None,
    ) -> None:
        """Emit a CommunicationLog row per FR-42. Best-effort + non-blocking.

        NFR-2: only bounded enum tokens + opaque IDs + sizes + latency.
        NEVER cred.password, cred.totp_seed, or totp_code.
        """
        if self._audit is None:
            return
        details: dict[str, Any] = {
            "customer_id":     self.customer_id,
            "device_id":       result.device_id,
            "milestone_name":  result.milestone_name,
            "target_dir":      result.target_dir,
            "filename":        result.uploaded_filename,
            "success":         result.success,
            "latency_ms":      latency_ms,
        }
        if result.error_code:
            details["error_code"] = result.error_code
            details["error_detail"] = result.error_detail or ""
        if ntp_skew_warning_s is not None:
            details["ntp_skew_warning_s"] = round(ntp_skew_warning_s, 2)
        try:
            self._audit.write_communication_log(
                action_type="carrier_upload",
                delivery_item_id=None,   # caller (workflow_engine) sets correlation downstream
                attribution={
                    "pm_id":       self.pm_id,
                    "customer_id": self.customer_id,
                },
                details=details,
            )
        except Exception:
            # Best-effort -- CommunicationLog failure must not break the
            # upload result return path.
            pass


def _latency_ms(started: datetime, completed: datetime) -> int:
    return int((completed - started).total_seconds() * 1000)
