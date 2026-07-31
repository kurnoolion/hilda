"""feedback_ops.py -- storage helpers for FeedbackTicketTable.

Backs the /feedback/<customer>/<device>/<milestone> early-access UI. Three
operations:
  - create_feedback_ticket: assigns per-scope seq_in_scope + composed
    ticket_id, inserts row. Anti-race via UniqueConstraint on
    (customer, device, milestone, seq_in_scope) with one bounded retry.
  - list_tickets_for_scope: returns tickets for the (customer, device,
    milestone) tuple, newest first, for the view page.
  - get_ticket_by_pk: single-row fetch used by the attachment-download
    route (needs the bytes column).

Ph-1 architect 2026-07-30. Ops-managed status transitions via SQL; TPMs
only submit + view.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.exc import IntegrityError

from core.src.diagnostics.error_codes import PipelineError
from core.src.storage._sync_bridge import run_async_sync
from core.src.storage.db import FeedbackTicketTable, session_scope

__all__ = [
    "create_feedback_ticket",
    "list_tickets_for_scope",
    "get_ticket_by_pk",
    "FeedbackStorage",
]

logger = logging.getLogger(__name__)

_MAX_SEQ_RETRIES = 3  # small bound; 5-TPM concurrency = effectively 0 races


async def create_feedback_ticket(
    *,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    category: str,
    bug_type: str,
    description: str | None,
    attachment_filename: str | None = None,
    attachment_content_type: str | None = None,
    attachment_bytes: bytes | None = None,
) -> FeedbackTicketTable:
    """Insert a new ticket. Computes per-scope seq_in_scope via SELECT
    max(seq)+1 and composes ticket_id as `<customer>-<device>-<milestone>-<seq>`.

    On UniqueConstraint violation (rare race with concurrent submit for the
    same scope), retries up to _MAX_SEQ_RETRIES with the fresh max+1. Raises
    STR-E003 if all retries exhaust.

    Returns the persisted row (with ticket_pk populated).
    """
    now = datetime.now(timezone.utc)
    last_error: Exception | None = None
    for attempt in range(_MAX_SEQ_RETRIES):
        async with session_scope() as session:
            current_max = await session.scalar(
                select(func.max(FeedbackTicketTable.seq_in_scope)).where(
                    FeedbackTicketTable.customer_id == customer_id,
                    FeedbackTicketTable.device_id == device_id,
                    FeedbackTicketTable.milestone_id == milestone_id,
                )
            )
            next_seq = (current_max or 0) + 1
            ticket_id = f"{customer_id}-{device_id}-{milestone_id}-{next_seq}"
            row = FeedbackTicketTable(
                ticket_id=ticket_id,
                seq_in_scope=next_seq,
                customer_id=customer_id,
                device_id=device_id,
                milestone_id=milestone_id,
                category=category,
                bug_type=bug_type,
                description=description,
                attachment_filename=attachment_filename,
                attachment_content_type=attachment_content_type,
                attachment_bytes=attachment_bytes,
                attachment_size=len(attachment_bytes) if attachment_bytes else None,
                status="open",
                created_at=now,
                updated_at=now,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError as exc:
                last_error = exc
                logger.warning(
                    "create_feedback_ticket race on seq attempt=%d: %s: %s -- retrying",
                    attempt + 1, type(exc).__name__, str(exc)[:120],
                )
                continue
            await session.refresh(row)
            return row
    raise PipelineError(
        "STR-E003",
        context={
            "entity": "feedback_ticket",
            "reason": (
                f"seq_in_scope race exhausted {_MAX_SEQ_RETRIES} retries for "
                f"({customer_id}, {device_id}, {milestone_id}): "
                f"{type(last_error).__name__ if last_error else 'unknown'}"
            ),
        },
    )


async def list_tickets_for_scope(
    *,
    customer_id: str,
    device_id: str,
    milestone_id: str,
) -> list[FeedbackTicketTable]:
    """Return tickets for the (customer, device, milestone) tuple, newest
    first. Used by the /feedback view page."""
    async with session_scope() as session:
        result = await session.execute(
            select(FeedbackTicketTable).where(
                FeedbackTicketTable.customer_id == customer_id,
                FeedbackTicketTable.device_id == device_id,
                FeedbackTicketTable.milestone_id == milestone_id,
            ).order_by(
                desc(FeedbackTicketTable.created_at),
                desc(FeedbackTicketTable.seq_in_scope),
            )
        )
        return list(result.scalars().all())


async def get_ticket_by_pk(ticket_pk: int) -> FeedbackTicketTable | None:
    """Fetch one ticket by DB primary key. Used by the attachment-download
    route to load attachment_bytes."""
    async with session_scope() as session:
        return await session.get(FeedbackTicketTable, ticket_pk)


class FeedbackStorage:
    """Sync-facing wrapper for hilda-api (FastAPI sync routes call these).

    Mirrors the delivery_item_ops.py PostgresStorage pattern -- delegate each
    method to the async helper via run_async_sync.
    """

    def create_ticket(
        self,
        *,
        customer_id: str,
        device_id: str,
        milestone_id: str,
        category: str,
        bug_type: str,
        description: str | None,
        attachment_filename: str | None = None,
        attachment_content_type: str | None = None,
        attachment_bytes: bytes | None = None,
    ) -> "FeedbackTicketTable":
        return run_async_sync(lambda: create_feedback_ticket(
            customer_id=customer_id,
            device_id=device_id,
            milestone_id=milestone_id,
            category=category,
            bug_type=bug_type,
            description=description,
            attachment_filename=attachment_filename,
            attachment_content_type=attachment_content_type,
            attachment_bytes=attachment_bytes,
        ))

    def list_tickets(
        self,
        *,
        customer_id: str,
        device_id: str,
        milestone_id: str,
    ) -> list["FeedbackTicketTable"]:
        return run_async_sync(lambda: list_tickets_for_scope(
            customer_id=customer_id,
            device_id=device_id,
            milestone_id=milestone_id,
        ))

    def get_ticket(self, ticket_pk: int) -> "FeedbackTicketTable | None":
        return run_async_sync(lambda: get_ticket_by_pk(ticket_pk))
