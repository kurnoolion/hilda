"""apply_pm_approval task -- PM approval handler per architect 2026-06-28
design pass.

Pattern A (SP-authoritative) per [D-068] + 2026-06-28 architect lock: SP UI
engineer's PM Approval button atomically writes 3 fields in ONE SP transaction
(delivery_state="ReadyForSubmission" + pm_approval_at=<now> + pm_approval_pm_id=
<PM AD account>). HILDA receives the SP CHANGED alert with all 3 in field_deltas
and MIRRORS them to local Postgres. HILDA does NOT run the state-machine
transition cascade -- guards (doc_count_reached + all_reviews_complete +
TPM sanity check) are enforced ON THE SP SIDE before the button is even
clickable; HILDA trusts SP/TPM authority.

Cascade wiring:
- Dispatcher's _refine_sub_trigger detects pm_approval_at OR pm_approval_pm_id
  in field_deltas -> sub_trigger="PmApproved" (added 2026-06-28).
- Rule advance_to_ready_for_submission_on_pm_approval (in
  customizations/rules/global/automation_rules.yaml) matches
  trigger=ItemModified + sub_trigger=PmApproved + condition delivery_state=
  UnderPMReview (snapshot value, pre-CHANGE; per dispatcher._enrich_event
  promotion of canonical fields into derived_fields).
- Rule actions: ApplyPMApproval (this task) + QueueSubmission (FR-18).

Trade-off vs Pattern B (HILDA-authoritative state-machine transition):
- (A) bypasses HILDA's _has_pm_approval / doc_count_reached guards. Acceptable
  because SP UI engineer's button visibility + TPM judgment already gate these.
- (A) skips audit row from tracker.transitions's state_transition action_type.
  Replaced by this task's pm_approval audit row, attributed to the PM who
  clicked (pm_approval_pm_id is corp email format e.g. abc@corp.com per
  architect lock).
- (A) is simpler -- single field-mirror write vs full transition+guard+
  transition cascade. Fewer moving parts; less to break in production.
"""
from __future__ import annotations

import logging
from typing import Any

from core.src.rule_engine import ActionKind
from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["apply_pm_approval_task"]

_log = logging.getLogger(__name__)


# 3 fields the SP UI engineer's button writes atomically; HILDA mirrors all 3.
_PM_APPROVAL_MIRROR_FIELDS = (
    "delivery_state",
    "pm_approval_at",
    "pm_approval_pm_id",
)


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.pm_approval.apply_pm_approval")
def apply_pm_approval_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """ApplyPMApproval -- Pattern A (SP-authoritative) mirror per architect
    2026-06-28 design lock + [D-068].

    Reads the new values from event_context['field_deltas'] (set by
    sp_alert_parser + dispatcher) and writes them to the local DeliveryItem
    row via storage.update_delivery_item. Audits action_type=pm_approval with
    attribution.modified_by = pm_approval_pm_id (PM corp email, e.g.
    abc@corp.com). No state-machine transition; HILDA trusts SP / TPM
    authority per Pattern A.

    Idempotency: re-running on the same alert is safe because update_delivery_item
    is a field-level patch; writing the same values is a no-op at the row level.

    params: unused; all needed values come from event_context.
    event_context: standard (correlation_id, delivery_item_id, trigger_source,
                   ...) + field_deltas dict from dispatcher.

    Returns: dict with outcome marker + mirrored field names for telemetry.
    """
    # DEBUG-INSTRUMENTATION 2026-06-30
    _log.info(
        "DBG apply_pm_approval_task ENTRY corr_id=%s delivery_item_id=%s "
        "field_deltas_keys=%s sub_trigger=%s",
        event_context.get("correlation_id"),
        event_context.get("delivery_item_id"),
        sorted((event_context.get("field_deltas") or {}).keys())[:12],
        event_context.get("sub_trigger"),
    )
    deps = get_task_deps()

    delivery_item_id = event_context.get("delivery_item_id")
    if not delivery_item_id:
        _log.warning("apply_pm_approval: missing delivery_item_id in event_context")
        return {"outcome": "skipped_missing_item_id", "fields_mirrored": []}

    # field_deltas shape per rule_engine.models.TriggerEvent:
    # dict[str, tuple[Any, Any]] where the tuple is (old_value, new_value).
    deltas = event_context.get("field_deltas") or {}
    if not deltas:
        _log.warning(
            "apply_pm_approval: empty field_deltas for item=%s; nothing to mirror",
            delivery_item_id,
        )
        return {"outcome": "skipped_no_deltas", "fields_mirrored": []}

    # Extract the 3 SP-authored field new-values. Tolerate any subset
    # (defensive: if SP writes fewer than 3 in some flow, mirror what's there).
    mirror_fields: dict[str, Any] = {}
    for field_name in _PM_APPROVAL_MIRROR_FIELDS:
        if field_name not in deltas:
            continue
        delta_tuple = deltas[field_name]
        # field_deltas can be tuple (old, new) per protocol OR plain value when
        # serialized through Celery JSON (tuples become lists). Handle both.
        if isinstance(delta_tuple, (tuple, list)) and len(delta_tuple) >= 2:
            new_value = delta_tuple[1]
        else:
            new_value = delta_tuple  # plain value fallback
        mirror_fields[field_name] = new_value

    if not mirror_fields:
        _log.warning(
            "apply_pm_approval: no PM-approval fields in deltas for item=%s; "
            "delta_keys=%s",
            delivery_item_id, sorted(deltas.keys())[:8],
        )
        return {"outcome": "skipped_no_pm_fields", "fields_mirrored": []}

    # Mirror to local Postgres. Field-level patch; SP-side already wrote the
    # canonical values per [D-068] atomic 3-field discipline.
    try:
        deps.storage.update_delivery_item(delivery_item_id, mirror_fields)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "apply_pm_approval: storage write failed for item=%s: %s: %s",
            delivery_item_id, type(exc).__name__, str(exc)[:120],
        )
        return {
            "outcome": "storage_write_failed",
            "delivery_item_id": delivery_item_id,
            "error": f"{type(exc).__name__}: {str(exc)[:120]}",
        }

    # Audit per architect lock: action_type=pm_approval; attribution.modified_by
    # = pm_approval_pm_id (PM corp email, e.g. abc@corp.com); details capture
    # the 3 mirrored values + source marker for SQL discoverability.
    pm_id_email = mirror_fields.get("pm_approval_pm_id") or "system:pm_approval_unattributed"
    try:
        deps.audit.write_communication_log(
            action_type="pm_approval",
            delivery_item_id=delivery_item_id,
            attribution={
                "trigger_source": event_context.get("trigger_source", "automated"),
                "correlation_id": event_context.get("correlation_id", ""),
                "modified_by":    pm_id_email,
            },
            details={
                "delivery_state":    mirror_fields.get("delivery_state"),
                "pm_approval_at":    str(mirror_fields.get("pm_approval_at") or ""),
                "pm_approval_pm_id": pm_id_email,
                "source":            "sp_atomic_write",
                "pattern":           "A",  # Pattern A per architect 2026-06-28
            },
        )
    except Exception as exc:  # noqa: BLE001
        # Audit failure must NOT roll back the Postgres mirror write -- the
        # state change is already in place; log and continue.
        _log.warning(
            "apply_pm_approval: audit write failed for item=%s: %s",
            delivery_item_id, str(exc)[:120],
        )

    _log.info(
        "apply_pm_approval: item=%s mirrored=%s pm_id=%s",
        delivery_item_id, sorted(mirror_fields.keys()), pm_id_email,
    )
    return {
        "outcome":          "applied",
        "delivery_item_id": delivery_item_id,
        "fields_mirrored":  sorted(mirror_fields.keys()),
        "pm_approval_pm_id": pm_id_email,
    }


# Register binding so workflow_engine.dispatcher routes APPLY_PM_APPROVAL
# rule actions to this Celery task.
register_task_binding(TaskBinding(
    action_kind=ActionKind.APPLY_PM_APPROVAL,
    celery_task=apply_pm_approval_task,
    queue="default",
))
