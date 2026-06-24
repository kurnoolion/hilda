"""FR-78 default work-item instantiation.

One default work-item per milestone per [D-060]. Idempotent: second call
for the same milestone returns the existing row -- safe for Celery
at-least-once retry. Default WI receives documents that fail FR-52
routing (steps 1-4); never appears in template.yaml -- HILDA owns the row.

Default WI inventory is fully hardcoded per architect lock 2026-06-21 +
SP UI engineer enum lock 2026-06-23 (item_type='Default' PascalCase).
None of the 11 fields below is template-author-editable; FR-78 governs.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from core.src.template_schema import DeliveryItemBase, ItemType

from core.src.tracker.protocols import AuditWriter, SpWriter, StorageWriter
from core.src.tracker.state_machine import DeliveryState

__all__ = ["instantiate_default_workitem"]


# FR-78 default work-item hardcoded inventory (architect lock 2026-06-21).
# Keys match template_schema.DeliveryItemBase field names (also the SP-canonical
# column names per [D-065]). None values become NULL on the SP row.
_DEFAULT_WORKITEM_FIELDS: dict[str, Any] = {
    "item_type":              ItemType.DEFAULT.value,    # 'Default' (PascalCase per SP UI engineer lock 2026-06-23)
    "item_no":                0,                          # reserved per FR-5 architect lock
    "tg_name":                "_unrouted",                # sentinel TG (underscore-prefix per FR-86)
    "tg_path_id":             "_unrouted",                # NSD path segment matches tg_name
    "item_path_id":           None,                       # no per-item NSD sub-path
    "delivery_state":         DeliveryState.OPEN.value,   # default WI skips OutreachSent; docs land directly per FR-78
    "tracking_modality":      None,                       # HILDA-internal; no owner outreach
    "doc_count":              0,                          # no expected doc count
    "review_required":        False,                      # never AI-reviewed
    "milestone_gating":       True,                       # MUST be Marked Closed before milestone Completed
    "no_customer_upload":     True,                       # never uploaded to carrier portal
    "force_tracking_enabled": False,                      # default WI ineligible for force-tracking (no owner)
    "owner_corp_usa_email":   None,                       # 4-field owner identity null per [D-080] + [D-086]
    "owner_corp_email":       None,
    "owner_corp_id":          None,                       # PLM grouping key per FR-5 + [D-035] -- null on default WI
    "owner_name":             None,
    "ingress_nsd":            "None",                     # PascalCase per SP UI engineer lock 2026-06-23
    "folder_routing_enabled": False,                      # default WI never participates in FR-77 routing
    "pm_approval_at":         None,                       # never set on default WI Ph-1
    "pm_approval_pm_id":      None,
    "target_folder":          None,                       # no carrier-portal upload destination
    # customer_delivery_modality REMOVED from per-item per D-126 architect Q2 lock 2026-06-26
    # (one modality per customer at CustomerTemplateBase level; no_customer_upload=True is sole upload gate)
    "review_status":          "not_required",             # per template_schema review_status enum
    "item_description":       None,                       # nested tag-set null on default WI per FR-82
    "manual_triage_required": False,
    "rules_paused":           False,                       # default WI never rule-paused per FR-78 + rule_engine D5 cascade 2026-06-23
}


def _now() -> datetime:
    """Single utc-now source -- overridable in tests via monkeypatch."""
    return datetime.now(timezone.utc)


def instantiate_default_workitem(
    milestone_id: str,
    customer_id: str,                                  # was customer_slug per [D-091] slug->id rename
    device_id: str,                                    # was device_slug per [D-091]
    storage: StorageWriter,
    sp_writer: SpWriter,
    audit: AuditWriter,
    event_context: dict[str, Any] | None = None,
    inferred_tg_path_id: str = "_unrouted",            # was inferred_tg_name_slug per [D-091]
) -> DeliveryItemBase:
    """FR-78 default work-item instantiation.

    Called by workflow_engine INSTANTIATE_DEFAULT_WORK_ITEM task body when
    `TrackerCreated` trigger fires for a new milestone (per FR-2).

    Idempotent: if a Default work-item already exists for the milestone,
    returns the existing row without creating a new one (audit-logs
    TRK-W004 informational). Otherwise creates a new row with the FR-78
    hardcoded inventory + identity fields (customer_id / device_id /
    milestone_id), writes via storage + sp_writer, audit-logs the
    creation event.

    Returns the resulting DeliveryItem (newly-created or pre-existing).
    """
    event_context = event_context or {}
    correlation_id = event_context.get("correlation_id", f"default-workitem-{milestone_id}")
    modified_by = event_context.get("pm_id", "system")

    # Idempotency check -- per FR-78 + [D-060] per-MILESTONE constraint.
    existing = storage.list_default_workitem_for_milestone(milestone_id)
    if existing is not None:
        audit.write_communication_log(
            action_type="default_workitem_reused",
            delivery_item_id=getattr(existing, "delivery_item_id", None),
            attribution={
                "trigger_source": event_context.get("trigger_source", "automated"),
                "correlation_id": correlation_id,
                "modified_by": modified_by,
            },
            details={
                "milestone_id": milestone_id,
                "code": "TRK-W004",
            },
        )
        return existing

    # Build the canonical field set: identity + FR-78 inventory + audit fields.
    now = _now()
    canonical_fields: dict[str, Any] = {
        **_DEFAULT_WORKITEM_FIELDS,
        "milestone_id":   milestone_id,
        "customer_id":    customer_id,
        "device_id":      device_id,
        "tg_path_id":     inferred_tg_path_id,    # default '_unrouted'; caller may override for inferred-tg landing per FR-86
        "tg_name":        inferred_tg_path_id,    # match tg_path_id for default WI; the two slugs are equivalent here per FR-86
        "created_at":     now.isoformat(),
        "modified_at":    now.isoformat(),
        "modified_by":    modified_by,
    }

    # SP-side row creation (per [D-064] -- HILDA-managed default WI is the
    # one row-creation case where HILDA writes; setup_milestone owns all
    # other row creation per [D-083] R&R lock).
    sp_item_id = sp_writer.create_item(
        entity="delivery_items",
        scope=event_context.get("sp_scope"),
        canonical_fields=canonical_fields,
    )

    # Storage-side row creation -- mirror the SP write into HILDA Postgres for
    # state-machine + lifecycle bookkeeping. (Concrete storage impl maps
    # canonical_fields onto its DeliveryItem model.)
    canonical_fields["delivery_item_id"] = sp_item_id
    storage.update_delivery_item(sp_item_id, canonical_fields)

    audit.write_communication_log(
        action_type="default_workitem_instantiated",
        delivery_item_id=sp_item_id,
        attribution={
            "trigger_source": event_context.get("trigger_source", "automated"),
            "correlation_id": correlation_id,
            "modified_by": modified_by,
        },
        details={
            "milestone_id":      milestone_id,
            "customer_id":       customer_id,
            "device_id":         device_id,
            "tg_path_id":        inferred_tg_path_id,
        },
    )

    # Return the just-created item. Concrete StorageWriter impls re-read
    # the row to populate audit timestamps + sp-side defaults.
    return storage.get_delivery_item(sp_item_id)
