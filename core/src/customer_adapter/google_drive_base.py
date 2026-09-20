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
from .protocol import (
    AuditWriter,
    BatchDispatchResult,
    CarrierUploadResult,
    UploadTriplet,
)
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
        customer_delivery_info: str,
    ) -> CarrierUploadResult:
        """Upload one file to the customer's Google Drive folder.

        Flow per [D-116] D12 + D13 (B-α) + D-126 cascade 2026-06-26:
        0. Validate customer_delivery_info non-empty -- raises CAD-E010 if missing
           (data-config error per architect Q3 lock 2026-06-26).
        1. Resolve `Credential` (auth_type=basic_totp) via credential_service.
        2. Generate `totp_code` via pyotp.TOTP(cred.totp_seed).now().
        3. Best-effort NTP skew check (if config.diagnostic_ntp_check); CAD-W005 on drift.
        4. Invoke binding's `uploadAttachment(...)` via `_invoke_binding` with 9th arg
           customer_delivery_info per D-126 -- binding composes
           <customer_delivery_info>/<device_id>/<milestone_name>/<target_dir>/<filename>
           internally per (B-α).
        5. Wrap bool return into CarrierUploadResult.
        6. Emit CommunicationLog row per FR-42 (best-effort; non-blocking).
        7. Return result.

        On binding raise (network / selenium / auth-MFA / file-not-found):
        catch, wrap into CarrierUploadResult(success=False, error_code=CAD-EXXX),
        log + emit, return.

        NEVER logs cred.password, cred.totp_seed, or totp_code.
        """
        started = _utc_now()

        # -- Step 0: validate customer_delivery_info per D-126 + architect Q3 lock --
        if not customer_delivery_info:
            completed = _utc_now()
            result = CarrierUploadResult(
                success=False,
                uploaded_filename=filename,
                device_id=device_id,
                milestone_name=milestone_name,
                target_dir=target_dir,
                upload_started_at=started,
                upload_completed_at=completed,
                error_code="CAD-E010",
                error_detail="customer_delivery_info_missing",
            )
            self._emit_log(result, latency_ms=_latency_ms(started, completed))
            return result

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
                customer_delivery_info=customer_delivery_info,
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
        customer_delivery_info: str,
    ) -> bool:
        """Invoke the per-customer Google Drive binding.

        9th arg `customer_delivery_info` added per D-126 cascade 2026-06-26
        (closes [D-116] D13 follow-up) -- per-row value from Deliverables SP
        list (e.g., "drive.google.com"); binding composes full URL
        `<customer_delivery_info>/<device_id>/<milestone_name>/<target_dir>/<filename>`
        internally per (B-α). Replaces the previous binding-baked customer-root
        framing.

        ABSTRACT in the base class -- per-customer subclass at
        `customizations/customer_adapter/<customer_id>_adapter.py` MUST override
        this to import + call the concrete binding (the user's pre-existing
        selenium-backed module per [D-116] D11/D12).

        Subclass implementation pattern (9-arg signature per D-126):
        ```python
        async def _invoke_binding(self, *, device_id, milestone_name,
                                  source_dir, target_dir, filename,
                                  pm_id, pm_password, totp_code,
                                  customer_delivery_info) -> bool:
            from <binding_module> import uploadAttachment  # noqa: N802
            return await asyncio.to_thread(
                uploadAttachment,
                device_id, milestone_name, str(source_dir),
                target_dir, filename, pm_id, pm_password, totp_code,
                customer_delivery_info,
            )
        ```

        Per [D-027]: HILDA's base class raises NotImplementedError (CAD-E009)
        rather than importing the binding directly -- proprietary binding
        internals never land on public github per NFR-2.
        """
        raise NotImplementedError(
            f"per-customer subclass missing for '{self.customer_id}'"
        )

    # ------------------------------------------------------------------
    # CARRIER-BATCH-5 (2026-09-20): async batch orchestrator
    # ------------------------------------------------------------------

    async def upload_attachments_batch(
        self,
        *,
        device_id: str,
        milestone_name: str,
        triplets: list[UploadTriplet],
        customer_delivery_info: str,
        callback_url: str,
        batch_id: str | None = None,
    ) -> BatchDispatchResult:
        """See CustomerAdapter.upload_attachments_batch docstring.

        Orchestrator:
          1. Validate customer_delivery_info (CAD-E010 mirror per D-126).
          2. Use caller-provided batch_id or mint one. Persist carrier_upload_batch row.
          3. Persist one carrier_upload_triplet row per triplet.
          4. Resolve credentials ONCE for the whole batch.
          5. Generate TOTP ONCE. Best-effort NTP skew check.
          6. Delegate to `_invoke_binding_batch` (subclass overrides for the
             fast Jenkins-batch path; default loops per-triplet via
             _invoke_binding).
          7. On dispatch failure: mark batch failed_dispatch, return
             BatchDispatchResult(dispatched=False, error_code=...).
          8. Emit one CommunicationLog "carrier_upload_batch_dispatched" row.

        `batch_id` is provided by the caller (submit_to_carrier_task) so
        the HMAC-signed callback_url baked with that id matches the
        persisted batch. Callers pre-mint the batch_id to build the URL,
        pass both in. When None, the adapter mints its own (test fixture
        path).

        Per-file outcomes flow back via the callback endpoint or (for the
        default fallback path) inline within _invoke_binding_batch.
        """
        import uuid as _uuid
        from datetime import timedelta

        started = _utc_now()
        if not batch_id:
            batch_id = f"BATCH-{_uuid.uuid4().hex[:16]}"
        timeout_at = started + timedelta(seconds=self._config.batch_timeout_seconds)

        if not customer_delivery_info:
            return BatchDispatchResult(
                dispatched=False, batch_id=batch_id, dispatched_at=started,
                expected_triplet_count=len(triplets),
                error_code="CAD-E010",
                error_detail="customer_delivery_info_missing",
            )

        # -- Persist batch + triplet rows BEFORE calling the binding so a
        # crashed dispatch still leaves durable state the reconcile beat can
        # pick up.
        from core.src.storage import carrier_upload_ops as _cu
        await _cu.insert_batch(
            batch_id=batch_id, customer_id=self.customer_id,
            device_id=device_id, milestone_id=milestone_name,
            dispatched_at=started, expected_triplet_count=len(triplets),
            timeout_at=timeout_at,
        )
        await _cu.insert_triplets([
            {
                "triplet_id":  t.triplet_id,
                "batch_id":    batch_id,
                "item_id":     t.item_id,
                "file_hash":   t.file_hash,
                "filename":    t.filename,
                "target_dir":  t.target_dir,
                "source_dir":  t.source_dir,
                "updated_at":  started,
            }
            for t in triplets
        ])

        if not triplets:
            # Zero-triplet is defensive per user 2026-09-20 (SP UI enforces
            # >= 1 RFS item before submit). Mark batch complete and return.
            await _cu.mark_batch_status(batch_id, "complete")
            return BatchDispatchResult(
                dispatched=True, batch_id=batch_id, dispatched_at=started,
                expected_triplet_count=0,
            )

        # -- Resolve credentials + TOTP + NTP skew ONCE for the whole batch.
        try:
            cred: Credential = await self._credentials.get_credential(
                self.pm_id, SystemType.CUSTOMER.value, customer_id=self.customer_id,
            )
        except PipelineError as exc:
            await _cu.mark_batch_status(batch_id, "failed_dispatch")
            return BatchDispatchResult(
                dispatched=False, batch_id=batch_id, dispatched_at=started,
                expected_triplet_count=len(triplets),
                error_code="CAD-E008", error_detail=exc.code_id,
            )
        if cred.auth_type != "basic_totp" or not cred.totp_seed:
            await _cu.mark_batch_status(batch_id, "failed_dispatch")
            return BatchDispatchResult(
                dispatched=False, batch_id=batch_id, dispatched_at=started,
                expected_triplet_count=len(triplets),
                error_code="CAD-E008", error_detail="auth_type_mismatch",
            )
        totp_code = current_totp(cred.totp_seed)
        skew_warning: float | None = None
        if self._config.diagnostic_ntp_check:
            skew = await asyncio.to_thread(ntp_skew_seconds)
            if skew is not None and skew > self._config.ntp_skew_warn_s:
                skew_warning = skew

        # -- Delegate to _invoke_binding_batch (subclass or default loop).
        try:
            jenkins_build_id = await self._invoke_binding_batch(
                device_id=device_id,
                milestone_name=milestone_name,
                triplets=triplets,
                pm_id=cred.username or self.pm_id,
                pm_password=cred.password or "",
                totp_code=totp_code,
                customer_delivery_info=customer_delivery_info,
                callback_url=callback_url,
                batch_id=batch_id,
            )
            del totp_code
        except NotImplementedError as exc:
            del totp_code
            await _cu.mark_batch_status(batch_id, "failed_dispatch")
            return BatchDispatchResult(
                dispatched=False, batch_id=batch_id, dispatched_at=started,
                expected_triplet_count=len(triplets),
                error_code="CAD-E009",
                error_detail=(str(exc)[:64] or "binding_batch_not_implemented"),
            )
        except Exception:  # noqa: BLE001
            del totp_code
            await _cu.mark_batch_status(batch_id, "failed_dispatch")
            return BatchDispatchResult(
                dispatched=False, batch_id=batch_id, dispatched_at=started,
                expected_triplet_count=len(triplets),
                error_code="CAD-E004", error_detail="binding_batch_failure",
            )

        # -- Record jenkins_build_id if the binding returned one.
        if jenkins_build_id:
            await _cu.mark_batch_status(
                batch_id, "dispatched", jenkins_build_id=jenkins_build_id,
            )

        # -- Emit one audit row for the batch (best-effort, NFR-2 compliant).
        self._emit_batch_log(
            batch_id=batch_id, device_id=device_id, milestone_name=milestone_name,
            expected=len(triplets), latency_ms=_latency_ms(started, _utc_now()),
            jenkins_build_id=jenkins_build_id, ntp_skew_warning_s=skew_warning,
        )

        return BatchDispatchResult(
            dispatched=True, batch_id=batch_id, dispatched_at=started,
            expected_triplet_count=len(triplets),
            jenkins_build_id=jenkins_build_id,
        )

    async def _invoke_binding_batch(
        self,
        *,
        device_id: str,
        milestone_name: str,
        triplets: list[UploadTriplet],
        pm_id: str,
        pm_password: str,
        totp_code: str,
        customer_delivery_info: str,
        callback_url: str,
        batch_id: str,
    ) -> str | None:
        """Dispatch the batch to the uploader. Returns Jenkins build id (or
        None if the uploader doesn't provide one).

        DEFAULT IMPLEMENTATION: safe correctness fallback for adapters that
        haven't implemented a fast Jenkins-batch path. Loops per-triplet
        calling `_invoke_binding` (the SLOW per-file path); on each result,
        updates the triplet row via carrier_upload_ops.mark_triplet_result so
        the reconcile beat can pick up per-item transitions on its next tick.

        Same total wall-clock latency as today's per-file loop -- this
        preserves correctness while corp-side rolls out the batch binding
        (or for tests without a real Jenkins). The corp-side subclass
        overrides this with a single Jenkins job dispatch.
        """
        from core.src.storage import carrier_upload_ops as _cu
        from pathlib import Path

        for t in triplets:
            success = False
            error: str | None = None
            try:
                ok = await self._invoke_binding(
                    device_id=device_id,
                    milestone_name=milestone_name,
                    source_dir=Path(t.source_dir),
                    target_dir=t.target_dir,
                    filename=t.filename,
                    pm_id=pm_id,
                    pm_password=pm_password,
                    totp_code=totp_code,
                    customer_delivery_info=customer_delivery_info,
                )
                success = bool(ok)
                if not success:
                    error = "post_verify_failed"
            except NotImplementedError as exc:
                error = f"binding_not_implemented: {str(exc)[:64]}"
            except TimeoutError:
                error = "binding_timeout"
            except FileNotFoundError:
                error = "source_file_missing"
            except Exception:  # noqa: BLE001
                error = "binding_failure"
            await _cu.mark_triplet_result(
                triplet_id=t.triplet_id, success=success, error=error,
            )

        # After the loop, mark the batch complete.
        await _cu.mark_batch_status(batch_id, "complete")
        return None  # no Jenkins build id in the fallback path

    def _emit_batch_log(
        self,
        *,
        batch_id: str,
        device_id: str,
        milestone_name: str,
        expected: int,
        latency_ms: int,
        jenkins_build_id: str | None,
        ntp_skew_warning_s: float | None,
    ) -> None:
        """One CommunicationLog row per batch dispatch. Per-file rows are
        written when callbacks arrive (dashboard route) or per-triplet during
        the fallback loop (handled via mark_triplet_result caller).
        """
        if self._audit is None:
            return
        details: dict[str, Any] = {
            "customer_id":            self.customer_id,
            "device_id":              device_id,
            "milestone_name":         milestone_name,
            "batch_id":               batch_id,
            "expected_triplet_count": expected,
            "dispatch_latency_ms":    latency_ms,
        }
        if jenkins_build_id:
            details["jenkins_build_id"] = jenkins_build_id
        if ntp_skew_warning_s is not None:
            details["ntp_skew_warning_s"] = round(ntp_skew_warning_s, 2)
        try:
            self._audit.write_communication_log(
                action_type="carrier_upload_batch_dispatched",
                delivery_item_id=None,
                attribution={
                    "pm_id":       self.pm_id,
                    "customer_id": self.customer_id,
                },
                details=details,
            )
        except Exception:
            pass

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
