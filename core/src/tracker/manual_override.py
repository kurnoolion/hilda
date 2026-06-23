"""FR-14 manual TPM field override — generic handler for TPM SP UI field
edits that do NOT have a dedicated ItemModified sub-trigger per FR-28.

Called by workflow_engine task body when `sp_alert_parser` detects a
TPM-manual SP field edit on a field WITHOUT a sub-trigger.

Sub-trigger fields route via rule_engine instead (NOT through this function):
- Any of the 4-field owner identity set
  (owner_corp_usa_email / owner_corp_email / owner_corp_id / owner_name
   per [D-080] + [D-086]) -> ItemModified.OwnerReassigned
- Milestone-level `target_date` edit on Milestones SP list row per [D-083]
  + [D-085] -> ItemModified.DeadlineMoved (cascades to ALL items in milestone
  via FR-11 re-arm semantics)
- `item_description` -> ItemModified.TagsModified (handled via
  tag_propagation.propagate_tags_to_active_trackers)

Fields THIS function handles (FR-14 generic override path):
- delivery_state (routed through update_delivery_state with bypass_guards=True
  + trigger_source='manual_tpm_override')
- doc_count, review_required, no_customer_upload, plm_id,
  actual_item_info (PLM URL per architect direction 2026-06-23), target_folder,
  manual_triage_required (TPM-cleared), and any other ad-hoc TPM corrections.

NFR-2: free-text fields like item_description are tag-list-shaped (not
raw prose); free-text overrides are rejected at sp_alert_parser before
reaching this function.
"""
from __future__ import annotations

from typing import Any

from core.src.tracker.protocols import AuditWriter, SpWriter, StorageWriter
from core.src.tracker.state_machine import DeliveryState
from core.src.tracker.transitions import (
    TransitionResult,
    update_delivery_state,
)

__all__ = ["apply_manual_tpm_override"]


def _coerce_delivery_state(value: Any) -> DeliveryState:
    """Accept either a DeliveryState enum value or its string representation
    (per SP-side Choice column write-back). Raises ValueError on unknown.
    """
    if isinstance(value, DeliveryState):
        return value
    if isinstance(value, str):
        return DeliveryState(value)
    raise ValueError(f"Cannot coerce {value!r} to DeliveryState")


def apply_manual_tpm_override(
    delivery_item_id: str,
    field_deltas: dict[str, tuple[Any, Any]],
    pm_id: str,
    storage: StorageWriter,
    sp_writer: SpWriter,
    audit: AuditWriter,
    event_context: dict[str, Any],
) -> list[TransitionResult]:
    """FR-14 manual TPM field override.

    `field_deltas` is a dict mapping field name to (old_value, new_value).
    The old_value is used for audit comparison; the new_value is what gets
    written. sp_alert_parser captures both from the SP-alert payload.

    Pipeline:
    1. For each field:
       (a) if field == 'delivery_state': call update_delivery_state with
           bypass_guards=True + trigger_source='manual_tpm_override'.
       (b) else: storage.update_delivery_item + sp_writer.update_item.
    2. Audit: per-field CommunicationLog row with action_type='manual_field_override'
       + pm_id + field_name + old_value + new_value (bounded values only;
       NFR-2 enforced at sp_alert_parser layer).
    3. Returns list of TransitionResult (one per state-related delta; empty
       list returned for non-state-only overrides). Caller fires StateChange
       triggers from the dispatch_signals returned.

    The trigger_source on event_context is set to 'manual_tpm_override' by
    the caller (sp_alert_parser routing). If absent, defaults to
    'manual_tpm_override' since this function is the FR-14 manual path
    entry point.
    """
    # Force the trigger_source for downstream guard reads (FR-14 invariant).
    ctx = dict(event_context)
    ctx["trigger_source"] = "manual_tpm_override"
    ctx.setdefault("pm_id", pm_id)
    correlation_id = ctx.get("correlation_id", "")

    transition_results: list[TransitionResult] = []
    non_state_writes: dict[str, Any] = {}

    for field_name, (old_value, new_value) in field_deltas.items():
        if field_name == "delivery_state":
            # Route through state-machine path with bypass_guards.
            target = _coerce_delivery_state(new_value)
            result = update_delivery_state(
                delivery_item_id=delivery_item_id,
                target_state=target,
                params={},
                event_context=ctx,
                storage=storage,
                sp_writer=sp_writer,
                audit=audit,
                bypass_guards=True,
            )
            transition_results.append(result)
            # State-change is logged inside update_delivery_state -- no extra
            # manual_field_override audit row for delivery_state.
            continue

        # Non-state field: collect for single batch write below.
        non_state_writes[field_name] = new_value
        audit.write_communication_log(
            action_type="manual_field_override",
            delivery_item_id=delivery_item_id,
            attribution={
                "trigger_source": "manual_tpm_override",
                "correlation_id": correlation_id,
                "modified_by": pm_id,
            },
            details={
                "field_name": field_name,
                # old_value/new_value are bounded per NFR-2 (sp_alert_parser
                # validates before dispatch). For free-text fields like
                # `actual_item_info` (PLM URL), the value is structurally
                # bounded (URL string) -- but we still cap to a sentinel for
                # audit hygiene if free-text override is detected.
                "old_value": _audit_safe_value(field_name, old_value),
                "new_value": _audit_safe_value(field_name, new_value),
            },
        )

    if non_state_writes:
        storage.update_delivery_item(delivery_item_id, non_state_writes)
        sp_writer.update_item(
            entity="delivery_items",
            scope=ctx.get("sp_scope"),
            item_id=delivery_item_id,
            canonical_fields=non_state_writes,
        )

    return transition_results


# NFR-2: bounded audit values only. For fields that may carry free-text-shaped
# data (PLM URLs, target_folder paths), we record presence + length, not the
# raw value; for enum / numeric / bool fields, we record the value directly.
_BOUNDED_FIELDS: frozenset[str] = frozenset({
    "doc_count",
    "review_required",
    "no_customer_upload",
    "milestone_gating",
    "force_tracking_enabled",
    "manual_triage_required",
    "review_status",
    "ingress_nsd",
    "customer_delivery_modality",
    "item_type",
    "tracking_modality",
    "delivery_state",
    "folder_routing_enabled",
})


def _audit_safe_value(field_name: str, value: Any) -> Any:
    """NFR-2: bound the audit value space. Enum / bool / int values pass
    through; free-text-shaped values are summarized.
    """
    if field_name in _BOUNDED_FIELDS:
        return value
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str):
        return {"len": len(value), "present": True}
    if isinstance(value, list):
        return {"list_len": len(value)}
    return {"type": type(value).__name__}
