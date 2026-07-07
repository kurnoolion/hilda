"""submit_to_carrier.py -- SubmitToCarrier ActionKind task per architect
2026-06-30 design pass.

Milestone-scoped orchestrator triggered by
submit_to_carrier_on_milestone_submission_triggered rule when TPM clicks
Submit-to-Carrier in the SP UI and milestone_submission_triggered_at gets
set on the Milestone row. Pattern A (SP-authoritative) per [D-068] -- HILDA
trusts the SP-side button visibility guard (all items in RFS + no_customer_upload
handled by SP UI engineer); no HILDA-side pre-check duplication.

For each delivery item in the milestone:
  - Skip if delivery_state == SubmittedToCustomer (idempotency; task restart)
  - Skip if delivery_state != ReadyForSubmission (out-of-scope for this event)
  - Skip if no_customer_upload==True OR target_folder is None (Confirmation
    + default WI + any per-item override that opts out of upload)
  - List classified DocumentItemAssociation rows (nsd_path_type=classified only)
  - Skip if zero classified files (audit as skip_no_files)
  - Sequentially upload each file via deps.customer_adapter.upload_attachment
    (async under the hood; wrapped with a fresh event loop per Celery task
    per _run_sync bridge convention). source_dir composed as
    <nsd_volume_prefix> + <dirname(local_nsd_path)>; filename = basename.
  - Per-file outcome:
      - True  -> log ok, continue
      - False -> post-verify failed; audit, continue to next file (item stays RFS)
      - raise -> browser/session dead; abort whole task; Celery retries
  - Per-item all True -> transition ReadyForSubmission -> SubmittedToCustomer

Retry policy (architect lock 2026-06-30):
  max_retries=2, retry_backoff=180s, retry_backoff_max=300s, jitter -- 3 total
  attempts at ~t=0, ~t=3min, ~t=8min. On final failure, task moves to Celery
  FAILURE state; the terminal-failure audit stays via what got written on each
  attempt; items remain in ReadyForSubmission (PM dashboard surfaces stuck
  milestone next poll).

Note: the customer_adapter's uploadAttachment (via CustomerAdapter Protocol
+ GoogleDriveBaseAdapter per [D-116]) already exhausts internal retries for
the network/selenium/MFA layer before it returns; HILDA's Celery-level retry
is a safety net for genuine outer-loop crashes (worker restart mid-task,
process kill, redis blip), NOT a retry for adapter-internal transient issues.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from core.src.rule_engine import ActionKind
from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["submit_to_carrier_task"]

_log = logging.getLogger(__name__)


# --- constants (architect lock 2026-06-30) -----------------------------------
_TARGET_STATE_VALUE  = "SubmittedToCustomer"     # DeliveryState enum value
_REQUIRED_FROM_STATE = "ReadyForSubmission"      # DeliveryState enum value


@hilda_celery_app.task(
    bind=True,
    autoretry_for=(Exception,),
    max_retries=2,                   # 3 total attempts (initial + 2 retries)
    retry_backoff=180,               # 180s base, doubled per retry (capped)
    retry_backoff_max=300,           # cap individual delay at 5 minutes
    retry_jitter=True,               # spread across parallel milestones
    name="core.src.workflow_engine.tasks.submit_to_carrier.submit_to_carrier",
)
def submit_to_carrier_task(
    self,
    params: dict[str, Any],
    event_context: dict[str, Any],
) -> dict[str, Any]:
    """SubmitToCarrier -- milestone-scoped upload orchestrator.

    params: unused (rule passes empty dict).
    event_context: standard + customer_id + milestone_id from the
                   Milestones-list CHANGED alert. delivery_item_id is None
                   (milestone-level trigger).

    Returns a summary dict keyed by outcome + per-item counts for telemetry.
    """
    deps = get_task_deps()
    correlation_id = event_context.get("correlation_id", "")
    customer_id    = event_context.get("customer_id")
    milestone_id   = event_context.get("milestone_id")
    # device_id scoping per fix 2026-07-06: SP Milestones has ONE row per
    # (customer, device, milestone) triple; TPM's Submit-to-Carrier click
    # updates ONE row -> ONE alert -> HILDA should upload only THAT device's
    # items. Without device_id filter, all devices' items in the milestone
    # get uploaded on a single-device Submit click.
    device_id      = event_context.get("device_id")

    # -- Identity guard -------------------------------------------------------
    if not customer_id or not milestone_id:
        _log.warning(
            "submit_to_carrier_skip_missing_identity: customer_id=%r milestone_id=%r "
            "correlation_id=%s",
            customer_id, milestone_id, correlation_id,
        )
        return {"outcome": "skipped_missing_identity", "uploaded_items": 0}

    # -- Storage / adapter presence -------------------------------------------
    list_method = getattr(deps.storage, "list_items_for_milestone", None)
    if list_method is None:
        _log.warning(
            "submit_to_carrier_skip_no_storage: milestone_id=%s correlation_id=%s",
            milestone_id, correlation_id,
        )
        return {"outcome": "skipped_no_storage", "uploaded_items": 0}
    if deps.customer_adapter is None:
        _log.warning(
            "submit_to_carrier_skip_no_adapter: milestone_id=%s correlation_id=%s",
            milestone_id, correlation_id,
        )
        _audit(deps, "submit_to_carrier_skipped", None, {
            "reason":         "customer_adapter_not_wired",
            "customer_id":    customer_id,
            "milestone_id":   milestone_id,
            "correlation_id": correlation_id,
        })
        return {"outcome": "skipped_no_adapter", "uploaded_items": 0}

    # -- Item iteration -------------------------------------------------------
    items = list_method(milestone_id, None) or []
    # Apply device_id filter per fix 2026-07-06 (see identity block above).
    if device_id:
        items = [
            it for it in items
            if (getattr(it, "device_id", None) or "") == device_id
        ]
    if not items:
        _log.info(
            "submit_to_carrier_empty_milestone: customer_id=%s milestone_id=%s device_id=%s",
            customer_id, milestone_id, device_id,
        )
        return {"outcome": "fired", "items_scanned": 0, "uploaded_items": 0}

    # Resolve host-side NSD prefix from CustomerAdapterConfig (architect lock
    # 2026-06-30 -- container-vs-host topology stays in config, not task code).
    nsd_prefix = _resolve_nsd_volume_prefix(deps)

    scanned          = len(items)
    skipped_state    = 0    # not in ReadyForSubmission
    skipped_already  = 0    # already SubmittedToCustomer
    skipped_upload   = 0    # no_customer_upload OR target_folder null
    skipped_no_files = 0    # zero classified files
    uploaded_items   = 0
    partial_items    = 0    # at least one False -- item stays in RFS

    for item in items:
        item_id       = getattr(item, "item_id", None) or getattr(item, "delivery_item_id", None)
        state         = getattr(item, "delivery_state", None) or ""
        target_folder = getattr(item, "target_folder", None)
        no_upload     = bool(getattr(item, "no_customer_upload", False))

        if state == _TARGET_STATE_VALUE:
            skipped_already += 1
            _log.info(
                "submit_to_carrier_skip_already_submitted: item=%s state=%s",
                item_id, state,
            )
            continue
        if state != _REQUIRED_FROM_STATE:
            skipped_state += 1
            _log.info(
                "submit_to_carrier_skip_state: item=%s state=%s (expected %s)",
                item_id, state, _REQUIRED_FROM_STATE,
            )
            continue
        if no_upload or not target_folder:
            skipped_upload += 1
            _log.info(
                "submit_to_carrier_skip_no_upload: item=%s no_customer_upload=%s "
                "target_folder=%r",
                item_id, no_upload, target_folder,
            )
            continue

        # -- Files: classified associations only ------------------------------
        assocs = _list_classified(deps, item_id)
        if not assocs:
            skipped_no_files += 1
            _log.info(
                "submit_to_carrier_skip_no_files: item=%s (no classified associations)",
                item_id,
            )
            _audit(deps, "submit_to_carrier_no_files", item_id, {
                "customer_id":    customer_id,
                "milestone_id":   milestone_id,
                "correlation_id": correlation_id,
            })
            continue

        # -- Per-item upload loop ---------------------------------------------
        all_ok       = True
        files_ok     = 0
        files_failed = 0
        device_id    = getattr(item, "device_id", None) or event_context.get("device_id") or ""
        customer_delivery_info = getattr(item, "customer_delivery_info", None) or ""

        for assoc in assocs:
            local_path = getattr(assoc, "local_nsd_path", "") or ""
            source_dir, filename = _resolve_source_dir_and_filename(nsd_prefix, local_path)
            if not filename:
                _log.warning(
                    "submit_to_carrier_skip_bad_path: item=%s local_nsd_path=%r",
                    item_id, local_path[:200],
                )
                files_failed += 1
                all_ok = False
                continue

            try:
                result = _upload_one(
                    deps,
                    device_id=device_id,
                    milestone_id=milestone_id,
                    source_dir=source_dir,
                    target_dir=target_folder,
                    filename=filename,
                    customer_delivery_info=customer_delivery_info,
                )
            except Exception as exc:  # noqa: BLE001
                # Infra failure -- abort task per architect lock. Celery retry
                # will pick up remaining items on the next attempt (already-
                # submitted items skip on re-run via delivery_state check).
                _log.warning(
                    "submit_to_carrier_upload_raised: item=%s file=%s exc=%s",
                    item_id, filename, type(exc).__name__,
                )
                _audit(deps, "submit_to_carrier_upload_raised", item_id, {
                    "customer_id":    customer_id,
                    "milestone_id":   milestone_id,
                    "filename":       filename[:120],
                    "error":          type(exc).__name__,
                    "attempt":        self.request.retries + 1,
                    "correlation_id": correlation_id,
                })
                raise  # Celery autoretry_for catches; final failure -> MaxRetriesExceededError

            ok = bool(getattr(result, "success", False))
            error_code = getattr(result, "error_code", None)
            if ok:
                files_ok += 1
                _audit(deps, "submit_to_carrier_file_ok", item_id, {
                    "customer_id":    customer_id,
                    "milestone_id":   milestone_id,
                    "filename":       filename[:120],
                    "target_dir":     target_folder[:120],
                    "correlation_id": correlation_id,
                })
            else:
                files_failed += 1
                all_ok = False
                _audit(deps, "submit_to_carrier_file_post_verify_failed", item_id, {
                    "customer_id":    customer_id,
                    "milestone_id":   milestone_id,
                    "filename":       filename[:120],
                    "target_dir":     target_folder[:120],
                    "error_code":     error_code or "",
                    "correlation_id": correlation_id,
                })

        # -- Per-item state transition on all-files-success -------------------
        if all_ok and files_ok > 0:
            transitioned = _transition_to_submitted(
                deps,
                item_id=item_id,
                customer_id=customer_id,
                milestone_id=milestone_id,
                correlation_id=correlation_id,
            )
            if transitioned:
                uploaded_items += 1
            else:
                # Files uploaded but state didn't transition -- count as
                # partial so the caller sees the truth. Item stays in RFS;
                # next Submit click will retry the transition (uploads are
                # idempotent per your binding contract).
                partial_items += 1
        else:
            partial_items += 1
            _log.info(
                "submit_to_carrier_partial: item=%s files_ok=%d files_failed=%d "
                "(item stays in %s; will retry on next Submit click)",
                item_id, files_ok, files_failed, _REQUIRED_FROM_STATE,
            )

    _log.info(
        "submit_to_carrier: milestone=%s scanned=%d uploaded=%d partial=%d "
        "skipped_already=%d skipped_state=%d skipped_upload=%d skipped_no_files=%d",
        milestone_id, scanned, uploaded_items, partial_items,
        skipped_already, skipped_state, skipped_upload, skipped_no_files,
    )
    return {
        "outcome":            "fired",
        "milestone_id":       milestone_id,
        "customer_id":        customer_id,
        "items_scanned":      scanned,
        "uploaded_items":     uploaded_items,
        "partial_items":      partial_items,
        "skipped_already":    skipped_already,
        "skipped_state":      skipped_state,
        "skipped_upload":     skipped_upload,
        "skipped_no_files":   skipped_no_files,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_nsd_volume_prefix(deps: Any) -> str:
    """Look up nsd_volume_prefix from CustomerAdapterConfig. Falls back to
    HILDA_CUSTOMER_ADAPTER_NSD_VOLUME_PREFIX env var, then empty string
    (implies local_nsd_path is already absolute or task is running in the
    same container as the adapter).
    """
    cfg = getattr(deps, "customer_adapter_config", None)
    if cfg is not None:
        prefix = getattr(cfg, "nsd_volume_prefix", "") or ""
        if prefix:
            return os.path.expanduser(prefix)
    return os.path.expanduser(
        os.environ.get("HILDA_CUSTOMER_ADAPTER_NSD_VOLUME_PREFIX", "")
    )


def _resolve_source_dir_and_filename(
    nsd_prefix: str, local_nsd_path: str,
) -> tuple[str, str]:
    """Compose absolute source_dir + basename filename for the adapter call.

    local_nsd_path from document_item_association is typically stored as a
    relative path rooted at 'internal/...'. Prepend nsd_prefix (host absolute
    mount root) to get the browser-accessible absolute path. If nsd_prefix is
    empty (same-container adapter), the path is used as-is.
    """
    if not local_nsd_path:
        return "", ""
    if nsd_prefix and not os.path.isabs(local_nsd_path):
        full = os.path.join(nsd_prefix, local_nsd_path)
    else:
        full = local_nsd_path
    p = Path(full)
    return str(p.parent), p.name


def _list_classified(deps: Any, delivery_item_id: str) -> list[Any]:
    """Fetch classified associations for the item via PostgresStorage sync
    wrapper. Falls back to filtering list_associations_for_item if the
    dedicated helper isn't wired (defensive)."""
    dedicated = getattr(deps.storage, "list_classified_associations_for_item", None)
    if dedicated is not None:
        try:
            return list(dedicated(delivery_item_id) or [])
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "submit_to_carrier: list_classified_associations_for_item failed "
                "for item=%s: %s",
                delivery_item_id, type(exc).__name__,
            )
            return []
    # Fallback: pull all, filter by nsd_path_type
    all_lookup = getattr(deps.storage, "list_associations_for_item", None)
    if all_lookup is None:
        return []
    try:
        rows = list(all_lookup(delivery_item_id) or [])
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "submit_to_carrier: list_associations_for_item failed for item=%s: %s",
            delivery_item_id, type(exc).__name__,
        )
        return []
    def _is_classified(r: Any) -> bool:
        val = getattr(r, "nsd_path_type", None)
        return getattr(val, "value", val) == "classified"
    return [r for r in rows if _is_classified(r)]


def _upload_one(
    deps: Any,
    *,
    device_id: str,
    milestone_id: str,
    source_dir: str,
    target_dir: str,
    filename: str,
    customer_delivery_info: str,
) -> Any:
    """Bridge the async CustomerAdapter.upload_attachment call into the sync
    Celery task body. Uses a fresh event loop per call (matches the pattern
    in submission._run_sync).
    """
    import asyncio
    coro = deps.customer_adapter.upload_attachment(
        device_id=device_id,
        milestone_name=milestone_id,
        source_dir=Path(source_dir),
        target_dir=target_dir,
        filename=filename,
        customer_delivery_info=customer_delivery_info,
    )
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        return loop.run_until_complete(coro)
    except RuntimeError:
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()


def _transition_to_submitted(
    deps: Any,
    *,
    item_id: str,
    customer_id: str,
    milestone_id: str,
    correlation_id: str,
) -> bool:
    """Transition item RFS -> SubmittedToCustomer via tracker.transitions.
    Returns True on successful transition, False on failure so the caller can
    avoid falsely counting a failed transition as an upload success.

    Lazy import: workflow_engine.tasks transitively imports tracker in other
    flows; keeping this local avoids circular-import friction (matches the
    kickoff_collection_task pattern).

    Guard 4 (guards.py) trusts trigger_source='submit_to_carrier_task' as
    authoritative evidence of upload completion, so we don't need to write
    a carrier_upload_complete flag first (the flag isn't a column on
    DeliveryItemTable in this codebase; prior write attempts silently
    no-op'd via update_delivery_item's hasattr check)."""
    from core.src.tracker import DeliveryState
    from core.src.tracker.transitions import update_delivery_state

    try:
        transition_result = update_delivery_state(
            delivery_item_id=item_id,
            target_state=DeliveryState.SUBMITTED_TO_CUSTOMER,
            params={},
            event_context={
                "correlation_id":   correlation_id,
                "customer_id":      customer_id,
                "milestone_id":     milestone_id,
                "delivery_item_id": item_id,
                "trigger_source":   "submit_to_carrier_task",
            },
            storage=deps.storage,
            sp_writer=deps.sp_writer,
            audit=deps.audit,
            bypass_guards=False,
        )
    except Exception as exc:  # noqa: BLE001
        # State transition failure is loud but non-fatal for the outer
        # iteration -- next Submit click / retry can re-run.
        _log.warning(
            "submit_to_carrier: state transition raised for item=%s: %s",
            item_id, type(exc).__name__,
        )
        _audit(deps, "submit_to_carrier_transition_failed", item_id, {
            "customer_id":    customer_id,
            "milestone_id":   milestone_id,
            "error":          type(exc).__name__,
            "correlation_id": correlation_id,
        })
        return False
    # update_delivery_state can return without raising when the transition is
    # guard_denied. Inspect the TransitionResult.outcome so we don't falsely
    # count a blocked transition as uploaded_items++ in the caller.
    outcome = getattr(transition_result, "outcome", None)
    if outcome not in ("transitioned", "no_op_idempotent"):
        _log.warning(
            "submit_to_carrier: state transition non-success for item=%s "
            "outcome=%s",
            item_id, outcome,
        )
        _audit(deps, "submit_to_carrier_transition_denied", item_id, {
            "customer_id":    customer_id,
            "milestone_id":   milestone_id,
            "outcome":        str(outcome),
            "correlation_id": correlation_id,
        })
        return False
    return True


def _audit(
    deps: Any,
    action_type: str,
    delivery_item_id: str | None,
    details: dict[str, Any],
) -> None:
    """Best-effort audit writer -- failures never break the outer flow."""
    if deps.audit is None:
        return
    try:
        deps.audit.write_communication_log(
            action_type=action_type,
            delivery_item_id=delivery_item_id,
            attribution={
                "trigger_source": details.get("trigger_source", "automated"),
                "correlation_id": details.get("correlation_id", ""),
                "modified_by":    "system",
            },
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("submit_to_carrier: audit write failed: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------

register_task_binding(TaskBinding(
    action_kind=ActionKind.SUBMIT_TO_CARRIER,
    celery_task=submit_to_carrier_task,
    # Same queue as other adapter-touching tasks per MODULE.md queue topology
    # (browser_automation -- 10-100x slower than REST per [D-054]; 1 worker
    # per host per session pool). Selecting this queue keeps parallelism
    # correct for the selenium session pool.
    queue="browser_automation",
))
