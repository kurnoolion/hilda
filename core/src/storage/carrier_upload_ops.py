"""CARRIER-BATCH-4 (2026-09-20): storage helpers for async batch upload persistence.

Two tables under management here (schema in `db.py`):
  * `carrier_upload_batch`   -- one row per adapter batch dispatch
  * `carrier_upload_triplet` -- one row per file in a batch

All helpers are async, use `session_scope`, and follow the surrounding
pattern (no transaction cross-cutting; each call is its own commit).

See D-217.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update

from core.src.storage.db import (
    CarrierUploadBatchTable,
    CarrierUploadTripletTable,
    session_scope,
)

__all__ = [
    "CarrierUploadBatchRow",
    "CarrierUploadTripletRow",
    "count_batch_triplets_by_status",
    "get_batch",
    "get_triplet",
    "get_triplets_for_batch_item",
    "insert_batch",
    "insert_triplets",
    "list_batches_past_timeout",
    "list_triplets_needing_retry",
    "mark_batch_status",
    "mark_triplet_needs_retry",
    "mark_triplet_result",
    "update_triplet_retry_state",
]

_log = logging.getLogger(__name__)
_session = session_scope


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
    jenkins_build_id: str | None
    dispatch_error_code: str | None
    dispatch_error_detail: str | None


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
        jenkins_build_id=tbl.jenkins_build_id,
        dispatch_error_code=tbl.dispatch_error_code,
        dispatch_error_detail=tbl.dispatch_error_detail,
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
    """Every batch whose timeout_at is in the past AND status is still
    'dispatched' (not yet completed or explicitly timed_out). Reconcile beat
    picks these up to mark unreported triplets as needs_retry and flip the
    batch to timed_out.
    """
    async with _session() as session:
        result = await session.execute(
            select(CarrierUploadBatchTable).where(
                CarrierUploadBatchTable.timeout_at <= now,
                CarrierUploadBatchTable.status == "dispatched",
            )
        )
        return [_batch_row(r) for r in result.scalars().all()]


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


async def mark_triplet_result(
    *,
    triplet_id: str,
    success: bool,
    error: str | None = None,
    completed_at: datetime | None = None,
    now: datetime | None = None,
) -> CarrierUploadTripletRow | None:
    """Callback-endpoint entry point.

    On success: status='succeeded', last_error=None, completed_at set,
    increment the batch's received_triplet_count. Idempotent -- a repeat
    success POST is a no-op (does not double-increment).

    On failure: status='failed', last_error=<bounded text>. retry_count is
    NOT incremented here (the retry beat owns that). received_triplet_count
    still increments so the batch's received count is accurate.

    Returns the updated triplet row (post-update) so the caller can pass it
    to the per-item transition check.
    """
    now = now or datetime.now(timezone.utc)
    async with _session() as session:
        row = await session.get(CarrierUploadTripletTable, triplet_id)
        if row is None:
            return None
        was_terminal_before = row.status in ("succeeded", "failed", "exhausted")
        row.status = "succeeded" if success else "failed"
        row.last_error = None if success else (error or "unspecified")[:512]
        row.completed_at = completed_at or now
        row.updated_at = now
        if not was_terminal_before:
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


async def update_triplet_retry_state(
    *,
    triplet_id: str,
    success: bool,
    error: str | None = None,
    max_retry_count: int,
    now: datetime | None = None,
) -> CarrierUploadTripletRow | None:
    """Retry-beat entry point. Called after per-file upload_attachment
    returns for a triplet in needs_retry/failed state.

    On success: status='succeeded', last_error=None, completed_at set,
      received_triplet_count incremented iff not already terminal.
    On failure: retry_count++. If new retry_count >= max_retry_count ->
      status='exhausted' (ops-alert territory); else status='needs_retry'
      (beat will pick it up again next tick).
    """
    now = now or datetime.now(timezone.utc)
    async with _session() as session:
        row = await session.get(CarrierUploadTripletTable, triplet_id)
        if row is None:
            return None
        was_terminal_before = row.status in ("succeeded", "exhausted")
        if success:
            row.status = "succeeded"
            row.last_error = None
            row.completed_at = now
        else:
            row.retry_count += 1
            row.last_error = (error or "unspecified")[:512]
            if row.retry_count >= max_retry_count:
                row.status = "exhausted"
            else:
                row.status = "needs_retry"
        row.updated_at = now
        if success and not was_terminal_before:
            batch = await session.get(CarrierUploadBatchTable, row.batch_id)
            if batch is not None:
                batch.received_triplet_count += 1
        await session.commit()
        return _triplet_row(row)


async def list_triplets_needing_retry(
    *, limit: int | None = None,
) -> list[CarrierUploadTripletRow]:
    """Any triplet with status in ('needs_retry', 'failed'). Reconcile beat
    consumes these, calls the per-file upload_attachment, then routes the
    result back through update_triplet_retry_state.

    Ordering: oldest updated_at first, so straggling failures don't starve.
    """
    async with _session() as session:
        stmt = select(CarrierUploadTripletTable).where(
            CarrierUploadTripletTable.status.in_(("needs_retry", "failed"))
        ).order_by(CarrierUploadTripletTable.updated_at.asc())
        if limit:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return [_triplet_row(r) for r in result.scalars().all()]
