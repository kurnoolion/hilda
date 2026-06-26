"""Dependency-injection Protocols for tracker.

Decouples tracker from concrete `storage` / `sharepoint_integration` /
audit-log implementations. Concrete classes (StorageWriter ← `storage`,
SpWriter ← `sharepoint_integration.SpCrud` per [D-064], AuditWriter ←
`storage.write_communication_log` per FR-42) implement these Protocols
via structural typing — no `isinstance` checks needed.

Rejected alternatives:
- Module-level singletons → test isolation broken; can't swap impls.
- Factory functions inside tracker → couples tracker to concrete classes.

Architect decision 2026-06-10 — matches [D-008] IssueTracker + [D-009]
Messenger boundary convention used elsewhere in HILDA.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from core.src.template_schema import DeliveryItemBase, DeliveryState

__all__ = ["StorageWriter", "SpWriter", "AuditWriter"]


class StorageWriter(Protocol):
    """Subset of `storage` module's interface that tracker depends on.

    Implementations must be safe to call from inside an async Celery task
    body; the concrete `storage` impl uses `async`/`await` but tracker
    wraps via the workflow_engine layer.
    """

    def get_delivery_item(self, delivery_item_id: str) -> DeliveryItemBase: ...

    def create_delivery_item(
        self,
        item: DeliveryItemBase,
    ) -> str:
        """Create a new DeliveryItem row from SP-imported state.

        Added 2026-06-26 per [D-118] strict-boundary cascade: SP UI engineer
        owns ALL SP row creation; HILDA imports the Deliverable into local
        storage when the SP ADDED alert arrives. Caller (import_deliverable_
        tracker task) is responsible for idempotency check via
        find_items_by_natural_key before calling create.

        Returns the delivery_item_id (composite-key-derived) of the newly
        created row.

        Implementation MUST raise on unique-constraint violation
        (customer_id + device_id + milestone_id + item_no) -- callers expect
        ValueError or similar; tracker.import_deliverable_tracker handles
        the race condition by catching + treating as "already exists".
        """
        ...

    def write_delivery_state(
        self,
        delivery_item_id: str,
        new_state: DeliveryState,
        modified_at: datetime,
        modified_by: str,
    ) -> None: ...

    def update_delivery_item(
        self,
        delivery_item_id: str,
        fields: dict[str, Any],
    ) -> None: ...

    def update_document_item_association(
        self,
        file_hash: str,
        fields: dict[str, Any],
    ) -> None: ...

    def update_document_index_row(
        self,
        file_hash: str,
        fields: dict[str, Any],
    ) -> None: ...

    def find_items_by_natural_key(
        self,
        customer_id: str,
        tg_name: str,
        item_no: int,
        only_active: bool = True,
    ) -> list[DeliveryItemBase]: ...    # customer_id per [D-091] slug->id rename

    def list_default_workitem_for_milestone(
        self,
        milestone_id: str,
    ) -> DeliveryItemBase | None: ...


class SpWriter(Protocol):
    """Subset of `sharepoint_integration.SpCrud` per [D-064] HILDA->SP REST
    sole writeback channel. Tracker never writes to SP directly.
    """

    def update_item(
        self,
        entity: str,
        scope: Any,                    # ListScope from sharepoint_integration; Any to avoid hard import
        item_id: str,
        canonical_fields: dict[str, Any],
    ) -> None: ...

    def create_item(
        self,
        entity: str,
        scope: Any,
        canonical_fields: dict[str, Any],
    ) -> str: ...                       # returns the new item_id


class AuditWriter(Protocol):
    """Writes CommunicationLog rows per FR-42. Concrete impl: `storage.write_communication_log`.

    `action_type` is a bounded enum token (e.g., 'state_transition',
    'transition_guard_denied', 'default_workitem_instantiated', etc.).
    `attribution` carries pm_id / correlation_id / trigger_source.
    `details` carries from_state / to_state / blocking_conditions / etc.

    NFR-2: no raw customer-data values, no owner-reply prose, no document
    content — bounded enum tokens + opaque IDs + timestamps only.
    """

    def write_communication_log(
        self,
        action_type: str,
        delivery_item_id: str | None,
        attribution: dict[str, str],
        details: dict[str, Any],
    ) -> None: ...
