"""Sync SpCrudWriter conforming to tracker.SpWriter Protocol.

Added 2026-06-27 per architect direction (storage-wireup strand Chunk 3).
Wraps the existing async SpCrud (list_crud.py) for sync Celery task body
callers. Same dual-mode pattern as PostgresStorage + PostgresAuditWriter.

Per [D-064]: HILDA -> SP REST is the sole writeback channel. tracker
exports SpWriter Protocol; SpCrudWriter is the canonical concrete impl
the bootstrap installs.
"""
from __future__ import annotations

from typing import Any

from core.src.sharepoint_integration.list_crud import SpCrud
from core.src.storage._sync_bridge import run_async_sync

__all__ = ["SpCrudWriter"]


class SpCrudWriter:
    """Conforms to tracker.SpWriter Protocol. Sync wrapper around async SpCrud.

    Construct with a pre-built SpCrud instance (which itself requires
    SpClient + SharePointListProvider; deployment's bootstrap script wires
    those up from creds + config).

    Methods:
    - update_item(entity, scope, item_id, canonical_fields) -> None
    - create_item(entity, scope, canonical_fields) -> str (new item_id)

    Both delegate to SpCrud's async ops via the thread-bridged sync helper.
    """

    def __init__(self, sp_crud: SpCrud) -> None:
        self._crud = sp_crud

    def update_item(
        self,
        entity: str,
        scope: Any,                      # ListScope from sharepoint_integration
        item_id: str,
        canonical_fields: dict[str, Any],
    ) -> None:
        run_async_sync(
            lambda: self._crud.update_item(entity, scope, item_id, canonical_fields)
        )

    def create_item(
        self,
        entity: str,
        scope: Any,
        canonical_fields: dict[str, Any],
    ) -> str:
        return run_async_sync(
            lambda: self._crud.create_item(entity, scope, canonical_fields)
        )
