"""CARRIER-BATCH-8 (2026-09-20) / CARRIER-RETRY-3 (2026-09-23): batch-upload
reconciler.

Runs on the celery beat (default every 15 min per
CustomerAdapterConfig.batch_retry_interval_seconds).

D-218 replaced the per-file retry drain with a BATCH re-dispatch. The old
design fell back to `adapter.upload_attachment` per stranded file, which
re-paid the 4-minute Selenium login for each one -- the exact cost
CARRIER-BATCH existed to remove. Retry is now: re-dispatch the batch's
still-pending subset as one new Jenkins job under the SAME batch_id.

Per batch, per tick:

  1. COMPLETION GATE. Past `timeout_at` is not proof the job finished -- a
     300-file job can legitimately outrun the window. Ask the uploader via
     `is_batch_job_completed`. Non-zero means still running; leave the batch
     alone and look again next tick.

  2. KILL IF STALLED. If the job is STILL not completed once the batch passes
     `kill_at`, it is presumed stuck. Call `kill_batch_job` before touching
     anything -- two live jobs writing the same Drive folder would duplicate
     uploads. A failed kill blocks this tick's re-dispatch.

  3. RE-DISPATCH. Flip unreported triplets to needs_retry, build the pending
     subset, mint a FRESH callback URL (per-attempt token TTL), and dispatch.
     Only on a confirmed dispatch does `begin_batch_redispatch` burn a retry
     slot -- a failed dispatch leaves the batch where it was for next tick.

  4. GIVE UP. At `retry_count >= batch_max_retry_count` the pending triplets
     go to 'exhausted' and the batch with them. Affected items stay in
     ReadyForSubmission for the TPM.

  5. ALERT SWEEP. One aggregated ops alert per dead batch (architect call
     2026-09-23 -- not one per file), covering both 'exhausted' and
     'permanent_failure'. `alerted_at` on the batch row makes it exactly-once
     across beat restarts.

Batches at 'failed_dispatch' are admitted by the same path: before D-218 they
were never revisited and their triplets sat at 'dispatched' forever.

See D-217, D-218.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["carrier_upload_reconcile_task"]

_log = logging.getLogger(__name__)


def _as_utc(dt: datetime) -> datetime:
    """Normalise a stored deadline to an aware UTC datetime before comparing
    it to `now`.

    Postgres (DateTime(timezone=True)) hands back aware datetimes, but SQLite
    -- the test backend -- drops the offset and returns naive ones, and
    comparing the two raises TypeError. Stored deadlines are always written
    as UTC, so attaching UTC to a naive value is a faithful restore rather
    than a guess.
    """
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


@hilda_celery_app.task(
    name="core.src.workflow_engine.tasks.carrier_upload_reconcile."
         "carrier_upload_reconcile",
)
def carrier_upload_reconcile_task(
    params: dict[str, Any] | None = None,
    event_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Beat task entry -- no params, no event_context needed. Returns a
    stats dict so celery ops can grep on the return."""
    import asyncio
    try:
        return asyncio.run(_run_reconcile())
    except RuntimeError as exc:
        # Loop-lifecycle fallback (Python 3.12+ / non-main threads).
        msg = str(exc).lower()
        if "no current event loop" not in msg and "event loop is closed" not in msg:
            raise
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(_run_reconcile())
        finally:
            new_loop.close()


async def _run_reconcile() -> dict[str, Any]:
    from core.src.customer_adapter.config import CustomerAdapterConfig
    from core.src.storage import carrier_upload_ops as _cu

    cfg = CustomerAdapterConfig.from_sources()
    now = datetime.now(timezone.utc)

    stats: dict[str, int] = {
        "batches_examined":       0,
        "batches_job_running":    0,
        "batches_killed":         0,
        "batches_kill_failed":    0,
        "triplets_marked_retry":  0,
        "redispatch_attempts":    0,
        "redispatch_ok":          0,
        "redispatch_failed":      0,
        "batches_exhausted":      0,
        "triplets_exhausted":     0,
        "batches_settled":        0,
        "alerts_sent":            0,
        "alerts_failed":          0,
    }

    deps = get_task_deps()
    adapter = getattr(deps, "customer_adapter", None)
    max_retries = int(cfg.batch_max_retry_count)

    for batch in await _cu.list_batches_past_timeout(now):
        stats["batches_examined"] += 1
        await _reconcile_one_batch(
            batch=batch, deps=deps, adapter=adapter, cfg=cfg,
            max_retries=max_retries, stats=stats,
        )

    # -- Settle any batch whose triplets all reported while we weren't
    # looking (a final callback can land between the sweep and now).
    for batch in await _cu.list_batches_past_timeout(datetime.now(timezone.utc)):
        if await _cu.settle_batch_if_all_reported(batch.batch_id):
            stats["batches_settled"] += 1

    # -- Aggregated ops alerts for dead batches (exactly-once via alerted_at).
    await _sweep_ops_alerts(deps=deps, stats=stats)

    _log.info("carrier_upload_reconcile: done stats=%s", stats)
    return stats


async def _reconcile_one_batch(
    *,
    batch: Any,
    deps: Any,
    adapter: Any,
    cfg: Any,
    max_retries: int,
    stats: dict[str, int],
) -> None:
    """Steps 1-4 for a single batch. Never raises -- one sick batch must not
    stop the beat from servicing the rest."""
    from core.src.storage import carrier_upload_ops as _cu

    bid = batch.batch_id
    now = datetime.now(timezone.utc)

    # -- Nothing pending? Settle and move on. Covers the race where the last
    # callback landed just after the batch passed timeout_at.
    pending = await _cu.list_pending_triplets_for_batch(bid)
    if not pending:
        if await _cu.settle_batch_if_all_reported(bid):
            stats["batches_settled"] += 1
        return

    # -- Retry ceiling reached: give up on this batch.
    if batch.retry_count >= max_retries:
        moved = await _cu.mark_pending_triplets_exhausted(
            bid,
            reason=f"batch_retries_exhausted after {batch.retry_count} re-dispatch(es)",
            now=now,
        )
        stats["batches_exhausted"] += 1
        stats["triplets_exhausted"] += len(moved)
        _log.warning(
            "carrier_upload_reconcile: batch=%s EXHAUSTED retry_count=%d "
            "pending_files=%d (customer=%s device=%s milestone=%s)",
            bid, batch.retry_count, len(moved),
            batch.customer_id, batch.device_id, batch.milestone_id,
        )
        return

    if adapter is None:
        _log.info(
            "carrier_upload_reconcile: no customer_adapter wired -- "
            "batch=%s left pending", bid,
        )
        return

    # -- Step 1: completion gate. A batch in failed_dispatch never started a
    # job, so there is nothing to wait on or kill; skip straight to dispatch.
    if batch.status != "failed_dispatch":
        try:
            job = await adapter.is_batch_job_completed(
                batch_id=bid, jenkins_build_id=batch.jenkins_build_id,
            )
        except Exception as exc:  # noqa: BLE001
            # Adapters contract to never raise here; treat a rogue raise as
            # "still running" -- the safe direction (no double-upload).
            _log.warning(
                "carrier_upload_reconcile: batch=%s job-status probe raised "
                "%s: %s -- treating as still running",
                bid, type(exc).__name__, str(exc)[:120],
            )
            stats["batches_job_running"] += 1
            return

        if not job.completed:
            # -- Step 2: past kill_at with the job still alive -> tear it down.
            if now < _as_utc(batch.kill_at):
                stats["batches_job_running"] += 1
                _log.info(
                    "carrier_upload_reconcile: batch=%s job still running "
                    "(code=%s probe_failed=%s) -- waiting until kill_at=%s",
                    bid, job.raw_code, job.probe_failed, batch.kill_at,
                )
                await _cu.mark_batch_status(bid, "timed_out")
                return

            kill = await adapter.kill_batch_job(
                batch_id=bid, jenkins_build_id=batch.jenkins_build_id,
            )
            if not kill.killed:
                stats["batches_kill_failed"] += 1
                _log.warning(
                    "carrier_upload_reconcile: batch=%s KILL FAILED "
                    "(code=%s detail=%s) -- re-dispatch blocked this tick",
                    bid, kill.raw_code, kill.error_detail,
                )
                await _cu.mark_batch_status(bid, "timed_out")
                return
            stats["batches_killed"] += 1
            await _cu.mark_batch_status(bid, "timed_out_killed")
            _log.warning(
                "carrier_upload_reconcile: batch=%s stalled job killed "
                "(jenkins_build_id=%s)", bid, batch.jenkins_build_id,
            )

    # -- Step 3: re-dispatch the pending subset.
    #
    # Triplets still at 'dispatched' never reported; mark them needs_retry so
    # the audit trail says WHY they are riding along (vs. a code-1 callback).
    for t in pending:
        if t.status == "dispatched":
            await _cu.mark_triplet_needs_retry(
                t.triplet_id, reason="batch_timeout_no_callback", now=now,
            )
            stats["triplets_marked_retry"] += 1

    await _redispatch_batch(
        batch=batch, pending=pending, deps=deps, adapter=adapter,
        cfg=cfg, stats=stats,
    )


async def _redispatch_batch(
    *,
    batch: Any,
    pending: list[Any],
    deps: Any,
    adapter: Any,
    cfg: Any,
    stats: dict[str, int],
) -> None:
    """Dispatch a fresh uploader job carrying only `pending`, under the same
    batch_id, with a freshly-minted callback URL."""
    from core.src.customer_adapter.protocol import UploadTriplet
    from core.src.dashboard.carrier_upload_routes import mint_batch_callback_url
    from core.src.storage import carrier_upload_ops as _cu

    bid = batch.batch_id
    stats["redispatch_attempts"] += 1

    triplets = [
        UploadTriplet(
            triplet_id=t.triplet_id,
            item_id=t.item_id,
            file_hash=t.file_hash,
            source_dir=t.source_dir,
            filename=t.filename,
            target_dir=t.target_dir,
        )
        for t in pending
    ]

    delivery_info = await _resolve_customer_delivery_info(deps, pending[0].item_id)
    callback_url = mint_batch_callback_url(deps=deps, batch_id=bid, ca_cfg=cfg)

    try:
        result = await adapter.upload_attachments_batch(
            device_id=batch.device_id,
            milestone_name=batch.milestone_id,
            triplets=triplets,
            customer_delivery_info=delivery_info,
            callback_url=callback_url,
            batch_id=bid,
        )
    except Exception as exc:  # noqa: BLE001
        stats["redispatch_failed"] += 1
        _log.warning(
            "carrier_upload_reconcile: batch=%s re-dispatch raised %s: %s",
            bid, type(exc).__name__, str(exc)[:120],
        )
        await _cu.mark_batch_status(bid, "failed_dispatch")
        return

    if not getattr(result, "dispatched", False):
        # Retry slot NOT burned -- the uploader never took the job, so the
        # attempt didn't happen. Next tick tries again; the retry ceiling
        # still bounds how long we keep trying.
        stats["redispatch_failed"] += 1
        _log.warning(
            "carrier_upload_reconcile: batch=%s re-dispatch refused "
            "(error_code=%s detail=%s)",
            bid, getattr(result, "error_code", None),
            getattr(result, "error_detail", None),
        )
        await _cu.mark_batch_status(bid, "failed_dispatch")
        return

    now = datetime.now(timezone.utc)
    new_retry_count = await _cu.begin_batch_redispatch(
        batch_id=bid,
        new_timeout_at=now + timedelta(seconds=int(cfg.batch_timeout_seconds)),
        new_kill_at=now + timedelta(seconds=int(cfg.batch_kill_after_seconds)),
        jenkins_build_id=getattr(result, "jenkins_build_id", None),
    )
    await _cu.bump_triplet_retry_counts(
        [t.triplet_id for t in pending], now=now,
    )
    stats["redispatch_ok"] += 1
    _log.info(
        "carrier_upload_reconcile: batch=%s re-dispatched attempt=%s "
        "pending_files=%d jenkins_build_id=%s",
        bid, new_retry_count, len(pending),
        getattr(result, "jenkins_build_id", None),
    )
    _audit_redispatch(deps, batch, len(pending), new_retry_count, result)


# ---------------------------------------------------------------------------
# Ops alerts (CARRIER-RETRY-5/6)
# ---------------------------------------------------------------------------


async def _sweep_ops_alerts(*, deps: Any, stats: dict[str, int]) -> None:
    """One aggregated alert per dead batch. `alerted_at` is stamped only
    after a successful send, so a failed alert is retried next tick rather
    than silently dropped."""
    from core.src.storage import carrier_upload_ops as _cu

    for batch in await _cu.list_batches_awaiting_alert():
        triplets = await _cu.list_all_triplets_for_batch(batch.batch_id)
        failed = [
            t for t in triplets
            if t.status in ("exhausted", "permanent_failure")
        ]
        sent = await _emit_batch_ops_alert(deps=deps, batch=batch, failed=failed)
        if sent:
            await _cu.mark_batch_alerted(batch.batch_id)
            stats["alerts_sent"] += 1
        else:
            stats["alerts_failed"] += 1


async def _emit_batch_ops_alert(
    *, deps: Any, batch: Any, failed: list[Any],
) -> bool:
    """Send the aggregated alert for one dead batch. Returns True when the
    alert is durably out (so `alerted_at` may be stamped).

    Two sinks, both best-effort:
      * ops_alerts service (email to the ops recipients) when wired;
      * communication_log audit row, always -- the durable record.

    Success is defined by the AUDIT row, not the email: the audit row is
    HILDA's own record and the thing an operator greps. If ops_alerts isn't
    wired (or its send fails) the batch still stops being re-alerted, because
    re-sending a log line every 15 minutes forever helps nobody.
    """
    # Bounded, NFR-2-safe payload: filenames + target dirs only, no content.
    context = {
        "batch_id":        batch.batch_id,
        "customer_id":     batch.customer_id,
        "device_id":       batch.device_id,
        "milestone_id":    batch.milestone_id,
        "batch_status":    batch.status,
        "retry_count":     batch.retry_count,
        "expected_files":  batch.expected_triplet_count,
        "failed_files":    len(failed),
        "item_ids":        sorted({t.item_id for t in failed}),
        # Cap the file list -- a 300-file batch must not produce a 300-line
        # alert email. The full picture lives in carrier_upload_triplet.
        "filenames":       [t.filename for t in failed[:25]],
        "filenames_truncated": max(0, len(failed) - 25),
        "last_error":      (failed[0].last_error if failed else None) or "",
    }
    error_code = (
        "CUR-E002" if batch.status == "permanent_failure" else "CUR-E001"
    )

    alerts = getattr(deps, "ops_alerts", None)
    if alerts is not None:
        try:
            from core.src.ops_alerts.protocol import Severity
            await alerts.emit_alert(
                source="carrier_upload_reconcile_task",
                error_code=error_code,
                context=context,
                severity=Severity.ERROR,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "carrier_upload_reconcile: ops_alerts emit failed batch=%s %s: %s",
                batch.batch_id, type(exc).__name__, str(exc)[:120],
            )

    audit = getattr(deps, "audit", None)
    if audit is None:
        _log.error(
            "CUR-E001 carrier upload batch dead: batch=%s status=%s "
            "failed_files=%d -- no audit sink wired",
            batch.batch_id, batch.status, len(failed),
        )
        return False
    try:
        audit.write_communication_log(
            action_type="carrier_upload_batch_failed",
            delivery_item_id=None,
            attribution={
                "trigger_source": "carrier_upload_reconcile_task",
                "correlation_id": batch.batch_id,
                "modified_by":    "system:carrier_upload_reconcile",
            },
            details={"error_code": error_code, **context},
        )
        return True
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "carrier_upload_reconcile: ops alert audit failed batch=%s: %s",
            batch.batch_id, str(exc)[:120],
        )
        return False


def _audit_redispatch(
    deps: Any, batch: Any, pending_count: int, attempt: int | None, result: Any,
) -> None:
    """One audit row per re-dispatch so the retry history is reconstructable
    from communication_log alone."""
    audit = getattr(deps, "audit", None)
    if audit is None:
        return
    try:
        audit.write_communication_log(
            action_type="carrier_upload_batch_redispatched",
            delivery_item_id=None,
            attribution={
                "trigger_source": "carrier_upload_reconcile_task",
                "correlation_id": batch.batch_id,
                "modified_by":    "system:carrier_upload_reconcile",
            },
            details={
                "batch_id":         batch.batch_id,
                "customer_id":      batch.customer_id,
                "device_id":        batch.device_id,
                "milestone_id":     batch.milestone_id,
                "attempt":          attempt,
                "pending_files":    pending_count,
                "jenkins_build_id": getattr(result, "jenkins_build_id", None) or "",
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "carrier_upload_reconcile: re-dispatch audit failed: %s", str(exc)[:120],
        )


async def _resolve_customer_delivery_info(deps: Any, item_id: str) -> str:
    """Look up the delivery item and return its customer_delivery_info string.

    Falls back to empty string on lookup failure -- the adapter then returns
    CAD-E010 for the whole batch and the re-dispatch is recorded as refused,
    which is the correct outcome for a mis-configured item.
    """
    try:
        it = deps.storage.get_delivery_item(item_id)
    except Exception:  # noqa: BLE001
        return ""
    return getattr(it, "customer_delivery_info", "") or ""


# No TaskBinding -- beat-driven only; scheduled in celery_app.py.
