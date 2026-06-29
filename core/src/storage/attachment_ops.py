"""Inbound attachment storage ops -- Step 5.5 cascade (architect 2026-06-29).

Two new async ops needed by `workflow_engine.tasks.inbound_attachment`:

1. `write_attachment_bytes(nsd_path, content)` -- writes attachment bytes to
   the resolved NSD path via aiofiles. Creates parent directories on demand.
   The path comes from `NSDPath.internal_classified()` /
   `internal_staged_classification()` / `internal_staged_revision()` /
   `internal_default_workitem()` per FR-86 4-path matrix.

2. `increment_doc_count_received(delivery_item_id)` -- atomic `+= 1` on the
   delivery_item.doc_count_received counter. Sourced by FR-7 doc_count_reached
   gate in tracker.guards Guard 2 (non-Confirmation OwnerClosed entry).

Existing async ops (in document_ops.py, unchanged here) are reused:
- add_document_index_row
- add_document_item_association
- get_document_index_row_by_hash (Step 0 dedup; called inside Fr52AttachmentRouter)
- find_doc_id_slugs_for_item (Step C; Ph-2 deferred)

Architect direction 2026-06-29 design pass:
- Ph-1 first pass: NSD write + DocumentIndexRow + DocumentItemAssociation +
  doc_count_received increment + AttachmentReceived event emission.
- Branch B (router item routing): substring match on item_description only.
- Step C (new-vs-revision): Ph-2 deferred.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiofiles
from sqlalchemy import update

from core.src.diagnostics.error_codes import PipelineError
from core.src.storage.db import DeliveryItemTable
from core.src.storage.engine import session_scope
from core.src.storage.nsd import NSDPath

__all__ = [
    "write_attachment_bytes",
    "increment_doc_count_received",
]

logger = logging.getLogger(__name__)


async def write_attachment_bytes(
    nsd_path: NSDPath,
    content: bytes,
) -> Path:
    """Write attachment bytes to the resolved NSD path.

    Creates parent directories on demand. Returns the absolute local Path
    written. Raises STR-E005 on IO failure (caller best-effort wraps).

    Per [D-013] all NSD writes go through hilda-svc AD service account;
    container's bind-mount or SMB mount per deploy/MODULE.md provides the
    actual filesystem access.
    """
    local_path = nsd_path.to_local()
    try:
        local_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(local_path, "wb") as f:
            await f.write(content)
    except OSError as exc:
        raise PipelineError(
            "STR-E005",
            context={
                "path": str(local_path)[:200],
                "reason": f"{type(exc).__name__}: {str(exc)[:120]}",
            },
            cause=exc,
        )
    logger.info(
        "attachment_write: bytes=%d path=%s",
        len(content), str(local_path)[-80:],  # bounded path log per NFR-2
    )
    return local_path


async def increment_doc_count_received(
    delivery_item_id: str,
    by: int = 1,
) -> int:
    """Atomic doc_count_received increment on delivery_item row.

    Returns the new counter value. Silently no-ops (returns 0) when the row
    doesn't exist -- defensive; caller logs the symptom upstream.

    Used by inbound_attachment.process_inbound_attachments_task after every
    successful per-item association write so the FR-7 doc_count_reached gate
    (guards.py Guard 2) advances over time as docs arrive.
    """
    if by <= 0:
        return 0
    async with session_scope() as session:
        # Atomic UPDATE ... SET doc_count_received = doc_count_received + N
        # RETURNING doc_count_received -- single round-trip, no read-modify-write race.
        stmt = (
            update(DeliveryItemTable)
            .where(DeliveryItemTable.item_id == delivery_item_id)
            .values(doc_count_received=DeliveryItemTable.doc_count_received + by)
            .returning(DeliveryItemTable.doc_count_received)
        )
        result = await session.execute(stmt)
        row = result.first()
        await session.commit()
        if row is None:
            logger.warning(
                "doc_count_received_increment: item_id=%s not found; no-op",
                delivery_item_id,
            )
            return 0
        new_count = int(row[0])
        logger.info(
            "doc_count_received: item=%s new_count=%d (by=%d)",
            delivery_item_id, new_count, by,
        )
        return new_count
