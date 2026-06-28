"""sp_alert_imports.py: IMPORT_DELIVERABLE_TRACKER + KICKOFF_COLLECTION task bodies.

Added 2026-06-26 per [D-118] strict-boundary cascade: SP UI engineer owns ALL
SP row creation (Milestones + Deliverables + Default WI). HILDA's role becomes
listen-only -- import each Deliverable into local storage on SP ADDED alert,
then fire ItemCreated TriggerEvents when TPM clicks Start Collection.

This module hosts:
- import_deliverable_tracker_task: parses SP ADDED alert body_kvs (delivered
  via TriggerEvent.derived_fields per [D-118] Chunk 3 plumbing) into a
  DeliveryItemBase + creates the local tracker via storage.create_delivery_item.
  Idempotent: skips if (customer_id, tg_name, item_no) already exists.
- kickoff_collection_task: reads all DeliveryItem trackers for the milestone,
  fires ItemCreated TriggerEvents per matching tracker (force_tracking_enabled
  AND item_type != Confirmation). This is what fires the
  send_initial_outreach_on_collection_start rule.

Chunk 3 (this commit): import_deliverable_tracker_task real body landed.
Chunk 4: kickoff_collection_task real body still stub -- next pass.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from core.src.rule_engine import ActionKind
from core.src.template_schema import DeliveryItemBase

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["import_deliverable_tracker_task", "kickoff_collection_task"]

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# body_kvs -> DeliveryItemBase field-mapping helpers
# ---------------------------------------------------------------------------

def _yn_to_bool(value: str | None, default: bool = False) -> bool:
    """SP renders bool fields as Choice(Yes/No) strings per architect Q3 lock
    2026-06-21 + xlsx convention. Empty/None -> default."""
    if value is None or value == "":
        return default
    return value.strip().lower() in ("yes", "true", "1")


def _modality_to_list(value: str | None) -> list[str]:
    """tracking_modality is MULTI-VALUE per [D-037]; SP renders as semi-colon-
    separated string. Empty/None -> empty list (the SP Choice MULTI column
    convention)."""
    if not value:
        return []
    return [m.strip() for m in re.split(r"[;,]", value) if m.strip()]


def _to_int(value: str | None, default: int = 0) -> int:
    """Parse int from body_kv string; default on missing/invalid."""
    if value is None or value == "":
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_delivery_item(
    *,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    body_kvs: dict[str, str],
    item_title: str,
) -> DeliveryItemBase:
    """Map body_kvs (parsed SP ADDED alert) to a DeliveryItemBase Pydantic
    model. Critical fields are mapped from body; non-critical fields use
    sensible defaults (most are Optional[None] or have model-defined defaults).

    Per [D-118] Chunk 3 Ph-1 first-pass: covers the fields needed for
    end-to-end outreach + classification + routing test. Remaining 25+
    fields (form-factor flags, JIRA polling state, PM-approval timestamps,
    FR-87 TPM-resolution fields, etc.) use DeliveryItemBase model defaults
    -- mapping from body_kvs is Ph-1 next pass enhancement when those fields
    are operationally exercised.
    """
    item_no = _to_int(body_kvs.get("item_no"))
    # Synthesize composite-key item_id per [D-091] (customer_id-device_id-
    # milestone_id-item_no); storage may override with its own scheme.
    item_id = f"{customer_id}-{device_id}-{milestone_id}-{item_no}"

    return DeliveryItemBase(
        # Identity:
        item_id=item_id,
        item_no=item_no,
        milestone_id=milestone_id,
        # customer_id + device_id ride as Pydantic extras (model_config
        # extra="allow"); _pydantic_to_row_kwargs picks them up via getattr
        # so DeliveryItemTable.customer_id + .device_id columns get written
        # (NOT NULL on storage for downstream natural-key + SP-read lookups).
        # Fix 2026-06-27 architect Step 4: prior code only used these two
        # params to compose item_id but never assigned them to the model,
        # leaving columns NULL on insert -- Path A SP-read at fire-time
        # then early-exited because item.customer_id was None.
        customer_id=customer_id,
        device_id=device_id,
        item_name=item_title or body_kvs.get("Title", f"Item {item_no}"),
        item_type=body_kvs.get("item_type", "Default"),
        # State:
        delivery_state=body_kvs.get("delivery_state", "Not Started"),
        item_completion_pct=_to_int(body_kvs.get("item_completion_pct"), 0),
        # Owner identity per [D-105] 4-field:
        owner_corp_usa_email=(body_kvs.get("owner_corp_usa_email") or None),
        owner_corp_email=(body_kvs.get("owner_corp_email") or None),
        owner_corp_id=(body_kvs.get("owner_corp_id") or None),
        owner_name=(body_kvs.get("owner_name") or None),
        # TG-denormalized per [D-106]:
        tg_name=(body_kvs.get("tg_name") or None),
        tg_email_group_alias=(body_kvs.get("tg_email_group_alias") or None),
        tg_owner_name=(body_kvs.get("tg_owner_name") or None),
        tg_owner_corp_usa_email=(body_kvs.get("tg_owner_corp_usa_email") or None),
        tg_owner_corp_email=(body_kvs.get("tg_owner_corp_email") or None),
        tg_owner_corp_id=(body_kvs.get("tg_owner_corp_id") or None),
        # Tracking gates per FR-81 + FR-78:
        tracking_modality=_modality_to_list(body_kvs.get("tracking_modality")),
        force_tracking_enabled=_yn_to_bool(body_kvs.get("force_tracking_enabled"), default=True),
        no_customer_upload=_yn_to_bool(body_kvs.get("no_customer_upload"), default=False),
        # Review gates per FR-7 / FR-53 / FR-70:
        review_required=_yn_to_bool(body_kvs.get("review_required"), default=False),
        # Milestone gating per FR-78:
        milestone_gating=_yn_to_bool(body_kvs.get("milestone_gating"), default=True),
        # Routing fields per FR-77:
        ingress_folder=(body_kvs.get("ingress_folder") or None),
        target_folder=(body_kvs.get("target_folder") or None),
        # Path components per FR-78:
        path_id=body_kvs.get("path_id", f"item_{item_no}"),
        # FR-7 doc_count + sort_order:
        doc_count=_to_int(body_kvs.get("doc_count"), 1),
        sort_order=_to_int(body_kvs.get("sort_order"), item_no),
        # Timestamps:
        last_updated=datetime.now(timezone.utc),
        # FR-87 / FR-83 + form-factor flags + JIRA / PM-approval fields all
        # use model defaults (None / False / 0) until operationally exercised.
    )


# ---------------------------------------------------------------------------
# Task bodies
# ---------------------------------------------------------------------------


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.sp_alert_imports.import_deliverable_tracker")
def import_deliverable_tracker_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """IMPORT_DELIVERABLE_TRACKER -> create local DeliveryItem tracker from SP
    ADDED alert body_kvs (per [D-118] Chunk 3).

    params: (none consumed; all needed values come from event_context built
            by sp_alert_parser's TriggerEvent + dispatcher._build_event_context
            per Chunks 3a + 3b)

    event_context: dispatcher-built dict carrying:
      - sub_trigger: should be "added" (from sp_alert_parser action_type)
      - customer_id, milestone_id: from EntityRef
      - derived_fields.body_kvs: dict[str, str] from parsed SP body
      - derived_fields.routing_key: dict with project_id / milestone_name /
                                     item_number / list_suffix
      - derived_fields.item_title: from parsed SP subject Title

    Returns dict with outcome marker:
      - "skipped_non_added"  -- sub_trigger wasn't "added" (no-op for changed/deleted)
      - "skipped_no_body_kvs" -- derived_fields.body_kvs missing/empty (data issue)
      - "skipped_missing_identity" -- can't synthesize composite key
      - "already_exists"     -- idempotent re-import (returns existing delivery_item_id)
      - "imported"           -- fresh create succeeded
    """
    deps = get_task_deps()

    # -- Action-type guard: only ADDED triggers import --
    sub_trigger = event_context.get("sub_trigger")
    if sub_trigger != "added":
        logger.info(
            "import_deliverable_tracker_skip_non_added: sub_trigger=%s",
            sub_trigger,
        )
        return {"outcome": "skipped_non_added", "sub_trigger": sub_trigger}

    # -- Extract derived_fields per Chunks 3a + 3b plumbing --
    derived = event_context.get("derived_fields") or {}
    body_kvs = derived.get("body_kvs") or {}
    routing_key = derived.get("routing_key") or {}
    item_title = derived.get("item_title") or ""

    if not body_kvs:
        logger.warning(
            "import_deliverable_tracker_skip_no_body_kvs: customer_id=%s milestone_id=%s",
            event_context.get("customer_id"),
            event_context.get("milestone_id"),
        )
        return {"outcome": "skipped_no_body_kvs"}

    # -- Resolve identity --
    customer_id = event_context.get("customer_id") or routing_key.get("list_suffix")
    milestone_id = event_context.get("milestone_id") or routing_key.get("milestone_name")
    # device_id resolved from body project_model per architect direction 2026-06-26
    # (Ph-1 simplification: device_id = project_model literal).
    device_id = body_kvs.get("project_model", "")
    item_no = _to_int(body_kvs.get("item_no"))

    if not (customer_id and milestone_id and device_id and item_no):
        logger.warning(
            "import_deliverable_tracker_skip_missing_identity: "
            "customer_id=%r milestone_id=%r device_id=%r item_no=%r",
            customer_id, milestone_id, device_id, item_no,
        )
        return {
            "outcome": "skipped_missing_identity",
            "customer_id": customer_id,
            "milestone_id": milestone_id,
            "device_id": device_id,
            "item_no": item_no,
        }

    # -- Idempotency check via natural key --
    tg_name = body_kvs.get("tg_name", "")
    existing = deps.storage.find_items_by_natural_key(
        customer_id=customer_id,
        tg_name=tg_name,
        item_no=item_no,
    )
    if existing:
        existing_id = getattr(existing[0], "item_id", None) or getattr(
            existing[0], "delivery_item_id", None
        )
        logger.info(
            "import_deliverable_tracker_already_exists: customer_id=%s "
            "tg_name=%s item_no=%s existing_id=%s",
            customer_id, tg_name, item_no, existing_id,
        )
        deps.audit.write_communication_log(
            action_type="deliverable_tracker_already_exists",
            delivery_item_id=existing_id,
            attribution={
                "correlation_id": event_context.get("correlation_id", "?"),
                "trigger_source": "sp_alert_import",
            },
            details={
                "customer_id": customer_id,
                "milestone_id": milestone_id,
                "tg_name": tg_name,
                "item_no": str(item_no),
            },
        )
        return {"outcome": "already_exists", "delivery_item_id": existing_id}

    # -- Build + create --
    item = _build_delivery_item(
        customer_id=customer_id,
        device_id=device_id,
        milestone_id=milestone_id,
        body_kvs=body_kvs,
        item_title=item_title,
    )
    new_id = deps.storage.create_delivery_item(item)

    deps.audit.write_communication_log(
        action_type="deliverable_tracker_imported",
        delivery_item_id=new_id,
        attribution={
            "correlation_id": event_context.get("correlation_id", "?"),
            "trigger_source": "sp_alert_import",
        },
        details={
            "customer_id": customer_id,
            "milestone_id": milestone_id,
            "device_id": device_id,
            "tg_name": tg_name,
            "item_no": str(item_no),
            "item_type": item.item_type,
            "tracking_modality": ",".join(item.tracking_modality),
        },
    )

    logger.info(
        "import_deliverable_tracker_imported: customer_id=%s milestone_id=%s "
        "device_id=%s item_no=%s delivery_item_id=%s",
        customer_id, milestone_id, device_id, item_no, new_id,
    )
    return {"outcome": "imported", "delivery_item_id": new_id}


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.sp_alert_imports.kickoff_collection")
def kickoff_collection_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """KICKOFF_COLLECTION -> fire ItemCreated TriggerEvents for all Deliverable
    trackers in the milestone that should receive outreach.

    Triggered by Milestones CHANGED alert with field_deltas containing
    milestone_collection_started_at (per architect direction 2026-06-26).

    params: (none consumed)

    event_context: dispatcher-built dict carrying:
      - customer_id, milestone_id: from EntityRef
      - correlation_id: for tracing through the downstream chain

    Filters trackers per FR-8 + send_initial_outreach_on_collection_start
    rule conditions:
      - force_tracking_enabled == True
      - item_type != "Confirmation"   (per FR-58)

    For each matching tracker, dispatches a fresh ItemCreated TriggerEvent
    via deps.dispatcher.dispatch(...). The dispatcher then runs rule_engine
    evaluation -> if rule matches, schedules SEND_INITIAL_OUTREACH +
    UPDATE_STATE actions per the rule's action list.

    Outcomes returned via dict:
      - "skipped_no_dispatcher" -- deps.dispatcher is None (worker not wired)
      - "skipped_no_storage"    -- storage method missing (smoke test path)
      - "fired"                 -- N ItemCreated events dispatched (returns count)
    """
    deps = get_task_deps()

    if deps.dispatcher is None:
        logger.warning(
            "kickoff_collection_skip_no_dispatcher: customer_id=%s milestone_id=%s",
            event_context.get("customer_id"),
            event_context.get("milestone_id"),
        )
        return {"outcome": "skipped_no_dispatcher", "events_fired": 0}

    customer_id = event_context.get("customer_id")
    milestone_id = event_context.get("milestone_id")
    if not customer_id or not milestone_id:
        logger.warning(
            "kickoff_collection_skip_missing_identity: customer_id=%r milestone_id=%r",
            customer_id, milestone_id,
        )
        return {"outcome": "skipped_missing_identity", "events_fired": 0}

    # Storage method per FR-78 + STATUS.md 2026-06-23 D7 cascade. Duck-typed
    # lookup avoids hard Protocol declaration (existing MockStorage already has
    # this method; concrete storage impl will too).
    list_method = getattr(deps.storage, "list_items_for_milestone", None)
    if list_method is None:
        logger.warning("kickoff_collection_skip_no_storage_method")
        return {"outcome": "skipped_no_storage", "events_fired": 0}

    # Read all trackers for the milestone (states=None -> all states).
    items = list_method(milestone_id, None) or []
    if not items:
        logger.info(
            "kickoff_collection_empty_milestone: customer_id=%s milestone_id=%s",
            customer_id, milestone_id,
        )
        return {"outcome": "fired", "events_fired": 0, "items_scanned": 0}

    # Filter for outreach-eligible items per FR-8 + rule conditions:
    #   force_tracking_enabled == True AND item_type != "Confirmation"
    # (FR-58 explicitly skips Confirmation items from outreach.)
    eligible = [
        item for item in items
        if getattr(item, "force_tracking_enabled", False) is True
        and getattr(item, "item_type", "") != "Confirmation"
    ]

    if not eligible:
        logger.info(
            "kickoff_collection_no_eligible_items: customer_id=%s milestone_id=%s "
            "items_scanned=%d",
            customer_id, milestone_id, len(items),
        )
        return {
            "outcome": "fired",
            "events_fired": 0,
            "items_scanned": len(items),
            "items_eligible": 0,
        }

    # -- Dispatch one ItemCreated event per eligible tracker --
    # Lazy-import to avoid circular dep (rule_engine imports workflow_engine in
    # some flows; lazy keeps task-body import clean).
    from core.src.rule_engine import EntityRef, TriggerEvent, TriggerKind
    import uuid as _uuid

    events_fired = 0
    correlation_id = event_context.get("correlation_id", str(_uuid.uuid4()))

    for item in eligible:
        item_id = getattr(item, "item_id", None) or getattr(
            item, "delivery_item_id", None
        )
        device_id = getattr(item, "device_id", None)
        event = TriggerEvent(
            trigger=TriggerKind.ITEM_CREATED,
            sub_trigger=None,
            entity_ref=EntityRef(
                customer_id=customer_id,
                device_id=device_id,
                milestone_id=milestone_id,
                delivery_item_id=item_id,
            ),
            field_deltas=None,
            timestamp=datetime.now(timezone.utc),
            correlation_id=correlation_id,
            derived_fields={
                "kickoff_source": "kickoff_collection_task",
                "item_no":         getattr(item, "item_no", None),
                "item_type":       getattr(item, "item_type", None),
                "tg_name":         getattr(item, "tg_name", None),
                "owner_corp_email": getattr(item, "owner_corp_email", None),
            },
        )
        deps.dispatcher.dispatch(event)
        events_fired += 1

    # -- Audit log the kickoff --
    deps.audit.write_communication_log(
        action_type="collection_kickoff_dispatched",
        delivery_item_id=None,
        attribution={
            "correlation_id": correlation_id,
            "trigger_source": "milestone_collection_started",
        },
        details={
            "customer_id": customer_id,
            "milestone_id": milestone_id,
            "events_fired": str(events_fired),
            "items_scanned": str(len(items)),
            "items_eligible": str(len(eligible)),
        },
    )

    logger.info(
        "kickoff_collection_fired: customer_id=%s milestone_id=%s "
        "items_scanned=%d eligible=%d events_fired=%d",
        customer_id, milestone_id, len(items), len(eligible), events_fired,
    )
    return {
        "outcome":        "fired",
        "events_fired":   events_fired,
        "items_scanned":  len(items),
        "items_eligible": len(eligible),
    }


register_task_binding(TaskBinding(
    action_kind=ActionKind.IMPORT_DELIVERABLE_TRACKER,
    celery_task=import_deliverable_tracker_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.KICKOFF_COLLECTION,
    celery_task=kickoff_collection_task,
    queue="default",
))
