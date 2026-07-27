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
    - get_items(entity, scope, canonical_filters)        -> list of canonical
      field dicts. Added 2026-06-27 per architect Path A "read SP at
      fire-time" -- send_initial_outreach + reminder tasks pull the
      current owner identity from SP rather than HILDA's stored copy, so
      mid-flight TPM owner edits in SP are honored without HILDA-side
      replication.

    All delegate to SpCrud's async ops via the thread-bridged sync helper.
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

    def get_items(
        self,
        entity: str,
        scope: Any,                      # ListScope from sharepoint_integration
        canonical_filters: dict[str, Any] | None = None,
        *,
        expand: list[str] | None = None,
        extra_select: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Sync wrapper around SpCrud.get_items -- canonical-field-out rows.

        Each returned dict carries `_sp_id` (SP's auto-counter Id) alongside
        the customer's canonical fields. Empty list when no match.

        expand + extra_select (added 2026-07-27 per TPM-2): forward User /
        Person field expansion to SP. Without expand, `Projects.TPM` returns
        as `TPMId` (int) and downstream email extraction sees None. Pass
        `expand=["TPM"]` to get the nested User object.
        """
        return run_async_sync(
            lambda: self._crud.get_items(
                entity, scope, canonical_filters,
                expand=expand, extra_select=extra_select,
            )
        )
