"""CARRIER-BATCH-8 (2026-09-20): async batch-upload reconciler.

Runs on the celery beat (default every 15 min per
CustomerAdapterConfig.batch_retry_interval_seconds). Two responsibilities:

  1. TIMEOUT SWEEP -- any batch past its `timeout_at` with status still
     'dispatched' has its unreported triplets flipped to 'needs_retry' and
     the batch itself flipped to 'timed_out'. This is NOT the give-up
     point; retry attempts still run for each affected triplet.

  2. RETRY DRAIN -- for every triplet in 'needs_retry' or 'failed' state
     with retry_count < max_retry_count, call the PER-FILE
     adapter.upload_attachment(...) (existing slow path -- proven, single-
     file semantics). On success: mark triplet succeeded + check per-item
     completion + transition RFS -> SubmittedToCustomer if this was the
     last. On failure: retry_count++. When retry_count >= max, mark
     'exhausted' + emit ops alert; item stays in RFS for TPM.

Also incidentally: if a batch's received_triplet_count reaches
expected_triplet_count via retries, mark it complete.

See D-217.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["carrier_upload_reconcile_task"]

_log = logging.getLogger(__name__)


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
        "batches_timed_out":      0,
        "triplets_marked_retry":  0,
        "retry_attempts":         0,
        "retry_success":          0,
        "retry_failed":           0,
        "retry_exhausted":        0,
        "batches_completed":      0,
        "items_transitioned":     0,
    }

    deps = get_task_deps()

    # ------------------------------------------------------------------
    # Step 1: timeout sweep
    # ------------------------------------------------------------------
    timed_out = await _cu.list_batches_past_timeout(now)
    for batch in timed_out:
        # All triplets for this batch (any status). Any still in
        # 'dispatched' -> the uploader never callback-reported them; flip
        # to needs_retry.
        all_triplets = await _list_all_triplets_for_batch(batch.batch_id)
        for t in all_triplets:
            if t.status == "dispatched":
                await _cu.mark_triplet_needs_retry(
                    t.triplet_id, reason="batch_timeout_no_callback", now=now,
                )
                stats["triplets_marked_retry"] += 1
        await _cu.mark_batch_status(batch.batch_id, "timed_out")
        stats["batches_timed_out"] += 1
        _log.info(
            "carrier_upload_reconcile: batch=%s timed out (customer=%s device=%s milestone=%s)",
            batch.batch_id, batch.customer_id, batch.device_id, batch.milestone_id,
        )

    # ------------------------------------------------------------------
    # Step 2: retry drain -- per-file fallback for needs_retry/failed
    # ------------------------------------------------------------------
    max_retries = int(cfg.batch_max_retry_count)
    if deps.customer_adapter is None:
        _log.info(
            "carrier_upload_reconcile: no customer_adapter wired -- skipping retry drain",
        )
    else:
        retry_candidates = await _cu.list_triplets_needing_retry(limit=200)
        for t in retry_candidates:
            if t.retry_count >= max_retries:
                # Belt-and-suspenders; the update helper flips to exhausted
                # on the (retry_count == max) transition, but if we ever
                # land a row here without an attempt run, catch it.
                continue

            batch = await _cu.get_batch(t.batch_id)
            if batch is None:
                continue

            stats["retry_attempts"] += 1
            success = False
            error: str | None = None
            try:
                result = await deps.customer_adapter.upload_attachment(
                    device_id=batch.device_id,
                    milestone_name=batch.milestone_id,
                    source_dir=Path(t.source_dir),
                    target_dir=t.target_dir,
                    filename=t.filename,
                    customer_delivery_info=await _resolve_customer_delivery_info(
                        deps, t.item_id,
                    ),
                )
                success = bool(getattr(result, "success", False))
                if not success:
                    error = (
                        getattr(result, "error_code", None)
                        or "unknown_upload_failure"
                    )
            except Exception as exc:  # noqa: BLE001
                error = f"{type(exc).__name__}: {str(exc)[:120]}"

            updated = await _cu.update_triplet_retry_state(
                triplet_id=t.triplet_id, success=success, error=error,
                max_retry_count=max_retries, now=datetime.now(timezone.utc),
            )
            if updated is None:
                continue

            if success:
                stats["retry_success"] += 1
                # Callback endpoint's helper is exactly what we need here.
                from core.src.dashboard.carrier_upload_routes import (
                    _transition_item_if_all_succeeded,
                )
                await _transition_item_if_all_succeeded(
                    deps=deps, batch_id=updated.batch_id,
                    item_id=updated.item_id,
                    correlation_id=updated.batch_id,
                )
                stats["items_transitioned"] += 0  # best-effort; transition may no-op
            else:
                if updated.status == "exhausted":
                    stats["retry_exhausted"] += 1
                    _emit_ops_alert_exhausted(deps, updated)
                else:
                    stats["retry_failed"] += 1

    # ------------------------------------------------------------------
    # Step 3: mark batches complete when received catches up
    # ------------------------------------------------------------------
    #
    # A batch can drain to received == expected via callbacks OR via retry
    # successes; both callers already flip the batch. But belt-and-suspenders:
    # for any batch with status in ('dispatched', 'timed_out') whose count
    # matches, flip complete.
    from core.src.storage.db import CarrierUploadBatchTable, session_scope
    from sqlalchemy import select
    async with session_scope() as session:
        result = await session.execute(
            select(CarrierUploadBatchTable).where(
                CarrierUploadBatchTable.status.in_(("dispatched", "timed_out"))
            )
        )
        for row in result.scalars().all():
            if row.received_triplet_count >= row.expected_triplet_count:
                row.status = "complete"
                stats["batches_completed"] += 1
        await session.commit()

    _log.info("carrier_upload_reconcile: done stats=%s", stats)
    return stats


async def _list_all_triplets_for_batch(batch_id: str) -> list[Any]:
    """All triplets for one batch (any status)."""
    from core.src.storage.db import CarrierUploadTripletTable, session_scope
    from sqlalchemy import select
    async with session_scope() as session:
        result = await session.execute(
            select(CarrierUploadTripletTable).where(
                CarrierUploadTripletTable.batch_id == batch_id,
            )
        )
        # Return the ORM rows directly; caller only reads .status and
        # .triplet_id off them.
        return list(result.scalars().all())


async def _resolve_customer_delivery_info(deps: Any, item_id: str) -> str:
    """Look up the delivery item and return its customer_delivery_info string.

    Falls back to empty string on lookup failure -- upload_attachment then
    surfaces CAD-E010 for that triplet and the retry counter climbs to
    exhaustion, which triggers an ops alert (correct outcome for a mis-
    configured item).
    """
    try:
        it = deps.storage.get_delivery_item(item_id)
    except Exception:  # noqa: BLE001
        return ""
    return getattr(it, "customer_delivery_info", "") or ""


def _emit_ops_alert_exhausted(deps: Any, triplet: Any) -> None:
    """Ops alert row -- item stays in RFS; TPM must intervene."""
    if deps is None or getattr(deps, "audit", None) is None:
        return
    try:
        deps.audit.write_communication_log(
            action_type="carrier_upload_max_retries_exhausted",
            delivery_item_id=triplet.item_id,
            attribution={
                "trigger_source": "carrier_upload_reconcile_task",
                "correlation_id": triplet.batch_id,
            },
            details={
                "batch_id":    triplet.batch_id,
                "triplet_id":  triplet.triplet_id,
                "filename":    triplet.filename,
                "target_dir":  triplet.target_dir,
                "retry_count": triplet.retry_count,
                "last_error":  triplet.last_error or "",
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "carrier_upload_reconcile: ops alert audit failed: %s",
            str(exc)[:120],
        )


# No TaskBinding -- beat-driven only; scheduled in celery_app.py.

