"""CARRIER-BATCH-4 (2026-09-20): storage helpers for async batch upload persistence.

Two tables under management here (schema in `db.py`):
  * `carrier_upload_batch`   -- one row per adapter batch dispatch
  * `carrier_upload_triplet` -- one row per file in a batch

All helpers are async, use `session_scope`, and follow the surrounding
pattern (no transaction cross-cutting; each call is its own commit).

CARRIER-RETRY (2026-09-23, D-218) reshaped the retry model:
  * Retry is a BATCH-level re-dispatch of the still-pending subset, reusing
    the same batch_id, NOT a per-file fallback to upload_attachment.
  * The uploader reports an integer `error_code` per callback:
      0 = success, 1 = retryable, 2 = permanent fault (no retry).
  * A batch-level callback may carry error_code=2 with empty triplet fields,
    meaning the whole job died (e.g. Drive login failure).

See D-217, D-218.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from core.src.storage.db import (
    CarrierUploadBatchTable,
    CarrierUploadTripletTable,
    session_scope,
)

__all__ = [
    "CarrierUploadBatchRow",
    "CarrierUploadTripletRow",
    "PENDING_TRIPLET_STATUSES",
    "TERMINAL_TRIPLET_STATUSES",
    "begin_batch_redispatch",
    "bump_triplet_retry_counts",
    "count_batch_triplets_by_status",
    "get_batch",
    "get_triplet",
    "get_triplets_for_batch_item",
    "insert_batch",
    "insert_triplets",
    "list_all_triplets_for_batch",
    "list_batches_awaiting_alert",
    "list_batches_past_timeout",
    "list_pending_triplets_for_batch",
    "list_triplets_needing_retry",
    "mark_batch_alerted",
    "mark_batch_permanent_failure",
    "mark_batch_status",
    "mark_pending_triplets_exhausted",
    "mark_triplet_needs_retry",
    "mark_triplet_result",
    "settle_batch_if_all_reported",
    "update_triplet_retry_state",
]

_log = logging.getLogger(__name__)
_session = session_scope

# A triplet is "pending" while it still owes us a terminal outcome. These are
# exactly the rows a re-dispatch carries into the next Jenkins job.
PENDING_TRIPLET_STATUSES = ("dispatched", "needs_retry", "failed")
# Terminal: never re-dispatched, never re-counted into received_triplet_count.
TERMINAL_TRIPLET_STATUSES = ("succeeded", "permanent_failure", "exhausted")


# ---------------------------------------------------------------------------
# DTO row shapes (frozen — read-only projections)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CarrierUploadBatchRow:
    batch_id: str
    customer_id: str
    device_id: str
    milestone_id: str
    dispatched_at: datetime
    expected_triplet_count: int
    received_triplet_count: int
    status: str
    timeout_at: datetime
    kill_at: datetime
    retry_count: int
    jenkins_build_id: str | None
    dispatch_error_code: str | None
    dispatch_error_detail: str | None
    alerted_at: datetime | None


@dataclass(frozen=True)
class CarrierUploadTripletRow:
    triplet_id: str
    batch_id: str
    item_id: str
    file_hash: str
    filename: str
    target_dir: str
    source_dir: str
    status: str
    retry_count: int
    last_error: str | None
    last_error_code: int | None
    completed_at: datetime | None
    updated_at: datetime


def _batch_row(tbl: CarrierUploadBatchTable) -> CarrierUploadBatchRow:
    return CarrierUploadBatchRow(
        batch_id=tbl.batch_id,
        customer_id=tbl.customer_id,
        device_id=tbl.device_id,
        milestone_id=tbl.milestone_id,
        dispatched_at=tbl.dispatched_at,
        expected_triplet_count=tbl.expected_triplet_count,
        received_triplet_count=tbl.received_triplet_count,
        status=tbl.status,
        timeout_at=tbl.timeout_at,
        kill_at=tbl.kill_at,
        retry_count=tbl.retry_count,
        jenkins_build_id=tbl.jenkins_build_id,
        dispatch_error_code=tbl.dispatch_error_code,
        dispatch_error_detail=tbl.dispatch_error_detail,
        alerted_at=tbl.alerted_at,
    )


def _triplet_row(tbl: CarrierUploadTripletTable) -> CarrierUploadTripletRow:
    return CarrierUploadTripletRow(
        triplet_id=tbl.triplet_id,
        batch_id=tbl.batch_id,
        item_id=tbl.item_id,
        file_hash=tbl.file_hash,
        filename=tbl.filename,
        target_dir=tbl.target_dir,
        source_dir=tbl.source_dir,
        status=tbl.status,
        retry_count=tbl.retry_count,
        last_error=tbl.last_error,
        last_error_code=tbl.last_error_code,
        completed_at=tbl.completed_at,
        updated_at=tbl.updated_at,
    )


# ---------------------------------------------------------------------------
# Batch inserts / reads
# ---------------------------------------------------------------------------


async def insert_batch(
    *,
    batch_id: str,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    dispatched_at: datetime,
    expected_triplet_count: int,
    timeout_at: datetime,
    kill_at: datetime,
    jenkins_build_id: str | None = None,
    status: str = "dispatched",
    dispatch_error_code: str | None = None,
    dispatch_error_detail: str | None = None,
) -> None:
    """Idempotent on batch_id -- re-insert with the same batch_id is a no-op."""
    async with _session() as session:
        existing = await session.get(CarrierUploadBatchTable, batch_id)
        if existing is not None:
            return
        session.add(CarrierUploadBatchTable(
            batch_id=batch_id,
            customer_id=customer_id,
            device_id=device_id,
            milestone_id=milestone_id,
            dispatched_at=dispatched_at,
            expected_triplet_count=expected_triplet_count,
            received_triplet_count=0,
            status=status,
            timeout_at=timeout_at,
            kill_at=kill_at,
            retry_count=0,
            jenkins_build_id=jenkins_build_id,
            dispatch_error_code=dispatch_error_code,
            dispatch_error_detail=dispatch_error_detail,
        ))
        await session.commit()


async def get_batch(batch_id: str) -> CarrierUploadBatchRow | None:
    async with _session() as session:
        row = await session.get(CarrierUploadBatchTable, batch_id)
        return _batch_row(row) if row else None


async def mark_batch_status(
    batch_id: str, status: str, *, jenkins_build_id: str | None = None,
) -> None:
    """Set batch.status (and optionally jenkins_build_id) unconditionally.

    Callers responsible for the state-machine ordering (dispatched ->
    complete | timed_out | failed_dispatch).
    """
    async with _session() as session:
        row = await session.get(CarrierUploadBatchTable, batch_id)
        if row is None:
            return
        row.status = status
        if jenkins_build_id is not None:
            row.jenkins_build_id = jenkins_build_id
        await session.commit()


async def list_batches_past_timeout(now: datetime) -> list[CarrierUploadBatchRow]:
    """Batches the reconcile beat must look at this tick.

    Three admission paths (CARRIER-RETRY-3):

      1. status='dispatched' AND timeout_at <= now
         -- the uploader had its window; ask is_batch_job_completed().
      2. status IN ('timed_out', 'timed_out_killed')
         -- a prior tick decided a re-dispatch was owed but the dispatch
            itself didn't stick. Retried immediately, no extra wait.
      3. status='failed_dispatch'
         -- the adapter never got the job to the uploader. Before
            CARRIER-RETRY these batches were never revisited and their
            triplets sat at 'dispatched' forever (orphaned-triplet bug);
            the batch row is the retry unit now, so they're admitted here.

    Batches at 'complete', 'permanent_failure' and 'exhausted' are terminal
    and never returned. The retry_count ceiling is enforced by the beat (it
    needs the config value), not by this query.
    """
    async with _session() as session:
        result = await session.execute(
            select(CarrierUploadBatchTable).where(
                CarrierUploadBatchTable.status.in_(
                    ("dispatched", "timed_out", "timed_out_killed", "failed_dispatch")
                ),
                # Only the 'dispatched' branch is time-gated; the other three
                # are already-decided states awaiting a re-dispatch.
                (CarrierUploadBatchTable.status != "dispatched")
                | (CarrierUploadBatchTable.timeout_at <= now),
            )
        )
        return [_batch_row(r) for r in result.scalars().all()]


async def mark_batch_permanent_failure(
    batch_id: str, *, detail: str | None = None, now: datetime | None = None,
) -> int:
    """Batch-level error_code=2 landed (e.g. Drive login died).

    Flips the batch to 'permanent_failure' and drags every still-pending
    triplet to 'permanent_failure' with it -- none of them are retryable,
    because the fault is the job's, not the file's. Returns the number of
    triplets moved so the caller can size the aggregated ops alert.
    """
    now = now or datetime.now(timezone.utc)
    async with _session() as session:
        batch = await session.get(CarrierUploadBatchTable, batch_id)
        if batch is None:
            return 0
        batch.status = "permanent_failure"
        if detail:
            batch.dispatch_error_detail = detail[:256]
        batch.dispatch_error_code = "2"
        result = await session.execute(
            select(CarrierUploadTripletTable).where(
                CarrierUploadTripletTable.batch_id == batch_id,
                CarrierUploadTripletTable.status.in_(PENDING_TRIPLET_STATUSES),
            )
        )
        moved = 0
        for row in result.scalars().all():
            row.status = "permanent_failure"
            row.last_error_code = 2
            row.last_error = (detail or "batch-level permanent fault")[:512]
            row.completed_at = now
            row.updated_at = now
            moved += 1
        await session.commit()
        return moved


async def mark_pending_triplets_exhausted(
    batch_id: str, *, reason: str, now: datetime | None = None,
) -> list[CarrierUploadTripletRow]:
    """Give-up path: batch hit batch_max_retry_count with files still pending.

    Moves every pending triplet to 'exhausted' and the batch with it, then
    returns the moved rows so the beat can compose ONE aggregated ops alert
    per batch (per the architect's 2026-09-23 call -- not one alert per file).
    """
    now = now or datetime.now(timezone.utc)
    async with _session() as session:
        result = await session.execute(
            select(CarrierUploadTripletTable).where(
                CarrierUploadTripletTable.batch_id == batch_id,
                CarrierUploadTripletTable.status.in_(PENDING_TRIPLET_STATUSES),
            )
        )
        moved: list[CarrierUploadTripletRow] = []
        for row in result.scalars().all():
            row.status = "exhausted"
            row.last_error = reason[:512]
            row.updated_at = now
            moved.append(_triplet_row(row))
        batch = await session.get(CarrierUploadBatchTable, batch_id)
        if batch is not None:
            batch.status = "exhausted"
        await session.commit()
        return moved


async def settle_batch_if_all_reported(
    batch_id: str,
) -> str | None:
    """Close out a batch once no triplet is pending any more.

    Called from the callback endpoint after each per-file result. Picks the
    batch's terminal status from what the triplets actually say:
      * any 'permanent_failure' -> batch 'permanent_failure' (ops alert owed)
      * any 'exhausted'         -> batch 'exhausted'         (ops alert owed)
      * otherwise               -> batch 'complete'          (clean finish)

    Returns the status written, or None if triplets are still pending (or the
    batch is unknown). Never downgrades an already-terminal batch.
    """
    async with _session() as session:
        batch = await session.get(CarrierUploadBatchTable, batch_id)
        if batch is None:
            return None
        if batch.status in ("complete", "permanent_failure", "exhausted"):
            return None
        result = await session.execute(
            select(CarrierUploadTripletTable.status).where(
                CarrierUploadTripletTable.batch_id == batch_id,
            )
        )
        statuses = [s for (s,) in result.all()]
        if any(s in PENDING_TRIPLET_STATUSES for s in statuses):
            return None
        if "permanent_failure" in statuses:
            new_status = "permanent_failure"
        elif "exhausted" in statuses:
            new_status = "exhausted"
        else:
            new_status = "complete"
        batch.status = new_status
        await session.commit()
        return new_status


async def list_batches_awaiting_alert() -> list[CarrierUploadBatchRow]:
    """Batches that died and haven't had their ops alert sent yet.

    One aggregated alert per batch (architect call 2026-09-23 -- not one per
    file), so the sweep is keyed on the batch row's `alerted_at` rather than
    on anything per-triplet.
    """
    async with _session() as session:
        result = await session.execute(
            select(CarrierUploadBatchTable).where(
                CarrierUploadBatchTable.status.in_(("permanent_failure", "exhausted")),
                CarrierUploadBatchTable.alerted_at.is_(None),
            ).order_by(CarrierUploadBatchTable.dispatched_at.asc())
        )
        return [_batch_row(r) for r in result.scalars().all()]


async def mark_batch_alerted(
    batch_id: str, *, now: datetime | None = None,
) -> None:
    """Stamp alerted_at so the sweep never re-sends. Written only AFTER the
    alert service reports success -- a failed send leaves the row eligible
    for the next tick."""
    now = now or datetime.now(timezone.utc)
    async with _session() as session:
        batch = await session.get(CarrierUploadBatchTable, batch_id)
        if batch is None:
            return
        batch.alerted_at = now
        await session.commit()


async def begin_batch_redispatch(
    *,
    batch_id: str,
    new_timeout_at: datetime,
    new_kill_at: datetime,
    jenkins_build_id: str | None = None,
) -> int | None:
    """Arm a batch for its next attempt: retry_count++, status back to
    'dispatched', fresh deadlines, new Jenkins build id.

    Called AFTER the adapter confirms the re-dispatch went out, so a failed
    re-dispatch doesn't burn a retry slot -- the batch stays in its
    timed_out/failed_dispatch state and the next tick tries again.

    Returns the new retry_count, or None if the batch is gone.
    """
    async with _session() as session:
        batch = await session.get(CarrierUploadBatchTable, batch_id)
        if batch is None:
            return None
        batch.retry_count += 1
        batch.status = "dispatched"
        batch.timeout_at = new_timeout_at
        batch.kill_at = new_kill_at
        if jenkins_build_id is not None:
            batch.jenkins_build_id = jenkins_build_id
        batch.dispatch_error_code = None
        batch.dispatch_error_detail = None
        await session.commit()
        return batch.retry_count


# ---------------------------------------------------------------------------
# Triplet inserts / reads / updates
# ---------------------------------------------------------------------------


async def insert_triplets(
    triplets: list[dict[str, Any]],
) -> None:
    """Bulk-insert triplet rows. Each dict must carry:
      triplet_id, batch_id, item_id, file_hash, filename, target_dir,
      source_dir, updated_at
    (status defaults to 'dispatched', retry_count to 0).

    Idempotent per triplet_id -- re-insert of an existing id is a no-op.
    """
    if not triplets:
        return
    async with _session() as session:
        existing_ids = set()
        ids = [t["triplet_id"] for t in triplets]
        for tid in ids:
            row = await session.get(CarrierUploadTripletTable, tid)
            if row is not None:
                existing_ids.add(tid)
        for t in triplets:
            if t["triplet_id"] in existing_ids:
                continue
            session.add(CarrierUploadTripletTable(
                triplet_id=t["triplet_id"],
                batch_id=t["batch_id"],
                item_id=t["item_id"],
                file_hash=t["file_hash"],
                filename=t["filename"],
                target_dir=t["target_dir"],
                source_dir=t["source_dir"],
                status=t.get("status", "dispatched"),
                retry_count=t.get("retry_count", 0),
                last_error=t.get("last_error"),
                last_error_code=t.get("last_error_code"),
                completed_at=t.get("completed_at"),
                updated_at=t["updated_at"],
            ))
        await session.commit()


async def get_triplet(triplet_id: str) -> CarrierUploadTripletRow | None:
    async with _session() as session:
        row = await session.get(CarrierUploadTripletTable, triplet_id)
        return _triplet_row(row) if row else None


async def get_triplets_for_batch_item(
    batch_id: str, item_id: str,
) -> list[CarrierUploadTripletRow]:
    """All triplets for a given (batch, item). Callback endpoint uses this to
    check whether the last file for an item just landed, so it can transition
    RFS -> SubmittedToCustomer.
    """
    async with _session() as session:
        result = await session.execute(
            select(CarrierUploadTripletTable).where(
                CarrierUploadTripletTable.batch_id == batch_id,
                CarrierUploadTripletTable.item_id == item_id,
            )
        )
        return [_triplet_row(r) for r in result.scalars().all()]


async def count_batch_triplets_by_status(
    batch_id: str,
) -> dict[str, int]:
    """Aggregate status counts for a batch. Used by the reconcile beat to
    mark a batch as `complete` when non-terminal counts reach zero, and by
    ops queries."""
    async with _session() as session:
        result = await session.execute(
            select(CarrierUploadTripletTable.status).where(
                CarrierUploadTripletTable.batch_id == batch_id,
            )
        )
        counts: dict[str, int] = {}
        for (st,) in result.all():
            counts[st] = counts.get(st, 0) + 1
        return counts


_ERROR_CODE_TO_STATUS = {
    0: "succeeded",
    1: "needs_retry",
    2: "permanent_failure",
}


async def mark_triplet_result(
    *,
    triplet_id: str,
    error_code: int,
    error: str | None = None,
    completed_at: datetime | None = None,
    now: datetime | None = None,
) -> CarrierUploadTripletRow | None:
    """Callback-endpoint entry point, keyed on the uploader's `error_code`.

      0 -> 'succeeded'         last_error cleared, completed_at set
      1 -> 'needs_retry'       carried into the batch's next re-dispatch
      2 -> 'permanent_failure' TERMINAL; never re-dispatched

    Any unrecognised code is treated as 1 (retryable) -- a mystery code from
    a future uploader build should cost us a retry, not a silently dropped
    file.

    `received_triplet_count` counts files the uploader has REPORTED ON, and
    only increments on the first non-pending transition, so a repeat POST for
    the same triplet can't double-count. A 1 -> re-dispatch -> 0 sequence
    leaves the row pending in between, so the later success does increment.

    Returns the updated row so the caller can run the per-item
    RFS -> SubmittedToCustomer check.
    """
    now = now or datetime.now(timezone.utc)
    new_status = _ERROR_CODE_TO_STATUS.get(error_code, "needs_retry")
    async with _session() as session:
        row = await session.get(CarrierUploadTripletTable, triplet_id)
        if row is None:
            return None
        was_terminal_before = row.status in TERMINAL_TRIPLET_STATUSES
        row.status = new_status
        row.last_error_code = error_code
        row.last_error = None if new_status == "succeeded" else (error or "unspecified")[:512]
        if new_status in TERMINAL_TRIPLET_STATUSES:
            row.completed_at = completed_at or now
        row.updated_at = now
        if new_status in TERMINAL_TRIPLET_STATUSES and not was_terminal_before:
            batch = await session.get(CarrierUploadBatchTable, row.batch_id)
            if batch is not None:
                batch.received_triplet_count += 1
        await session.commit()
        return _triplet_row(row)


async def mark_triplet_needs_retry(
    triplet_id: str, *, reason: str | None = None, now: datetime | None = None,
) -> CarrierUploadTripletRow | None:
    """Reconcile-beat entry point for a batch-timeout sweep. Sets status to
    needs_retry without incrementing retry_count -- the actual retry
    attempt does that."""
    now = now or datetime.now(timezone.utc)
    async with _session() as session:
        row = await session.get(CarrierUploadTripletTable, triplet_id)
        if row is None:
            return None
        row.status = "needs_retry"
        if reason:
            row.last_error = reason[:512]
        row.updated_at = now
        await session.commit()
        return _triplet_row(row)


async def list_all_triplets_for_batch(
    batch_id: str,
) -> list[CarrierUploadTripletRow]:
    """Every triplet in a batch, any status. Used by the ops-alert sweep,
    which needs the terminal-failure rows the pending query excludes."""
    async with _session() as session:
        result = await session.execute(
            select(CarrierUploadTripletTable).where(
                CarrierUploadTripletTable.batch_id == batch_id,
            ).order_by(CarrierUploadTripletTable.filename.asc())
        )
        return [_triplet_row(r) for r in result.scalars().all()]


async def list_pending_triplets_for_batch(
    batch_id: str,
) -> list[CarrierUploadTripletRow]:
    """The still-pending subset of a batch -- exactly what the next
    re-dispatch carries.

    Per the architect's 2026-09-23 call ("reuse same batch_id for retries,
    but .json file will have only entries of files that are yet to be
    uploaded"), the re-dispatched job's triplet manifest is built from this
    list, NOT from the batch's original full set.

    Ordering: oldest updated_at first, so stragglers don't starve behind
    freshly-failed rows.
    """
    async with _session() as session:
        result = await session.execute(
            select(CarrierUploadTripletTable).where(
                CarrierUploadTripletTable.batch_id == batch_id,
                CarrierUploadTripletTable.status.in_(PENDING_TRIPLET_STATUSES),
            ).order_by(CarrierUploadTripletTable.updated_at.asc())
        )
        return [_triplet_row(r) for r in result.scalars().all()]


async def bump_triplet_retry_counts(
    triplet_ids: list[str], *, now: datetime | None = None,
) -> None:
    """Informational per-file attempt counter, bumped when a triplet rides
    along in a re-dispatch. The give-up decision lives at batch level
    (CarrierUploadBatchTable.retry_count); this is for ops forensics --
    "which file has been dragged through the most jobs?"."""
    if not triplet_ids:
        return
    now = now or datetime.now(timezone.utc)
    async with _session() as session:
        result = await session.execute(
            select(CarrierUploadTripletTable).where(
                CarrierUploadTripletTable.triplet_id.in_(triplet_ids)
            )
        )
        for row in result.scalars().all():
            row.retry_count += 1
            row.status = "dispatched"
            row.updated_at = now
        await session.commit()


# --- Deprecated (CARRIER-UNIFY / D-218) ------------------------------------
# The per-file retry path is gone: retry is a batch re-dispatch now. These two
# remain only so in-flight rows written by the pre-D-218 beat stay readable,
# and so the deprecation is visible rather than a silent deletion.


async def update_triplet_retry_state(
    *,
    triplet_id: str,
    success: bool,
    error: str | None = None,
    max_retry_count: int,
    now: datetime | None = None,
) -> CarrierUploadTripletRow | None:
    """DEPRECATED -- per-file retry via upload_attachment no longer runs.
    Use mark_triplet_result (callbacks) + begin_batch_redispatch (retries)."""
    _log.warning(
        "CUO-W001 update_triplet_retry_state is deprecated (D-218); "
        "retry is batch-scoped -- triplet_id=%s", triplet_id,
    )
    return await mark_triplet_result(
        triplet_id=triplet_id,
        error_code=0 if success else 1,
        error=error,
        now=now,
    )


async def list_triplets_needing_retry(
    *, limit: int | None = None,
) -> list[CarrierUploadTripletRow]:
    """DEPRECATED -- use list_pending_triplets_for_batch. Retained for ops
    queries that want a cross-batch view of what's still owed."""
    async with _session() as session:
        stmt = select(CarrierUploadTripletTable).where(
            CarrierUploadTripletTable.status.in_(("needs_retry", "failed"))
        ).order_by(CarrierUploadTripletTable.updated_at.asc())
        if limit:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return [_triplet_row(r) for r in result.scalars().all()]
