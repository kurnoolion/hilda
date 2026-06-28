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
    """KICKOFF_COLLECTION -> for each Not-Started tracker in the milestone:
    group by owner, send ONE outreach email per owner with all their items in
    a single multi-row table, transition each item Not Started -> Open ->
    Outreach Sent.

    Triggered by Milestones CHANGED alert with field_deltas containing
    milestone_collection_started_at (per architect direction 2026-06-26).

    Architect Step 5 2026-06-28 restructure -- two problems fixed in one pass:
      (1) Re-fire on already-processed items: prior implementation dispatched
          ItemCreated for every eligible item regardless of delivery_state.
          Items already in Outreach Sent re-fired the cascade -> Outreach
          Sent -> Open transitions were illegal -> noisy errors. Fix: filter
          eligibility to delivery_state == "Not Started" only. Re-clicking
          Start Collection is now safely idempotent.
      (2) One email per item, not one per owner: prior implementation
          dispatched ItemCreated per item, the rule chain ran per item, owner
          got N emails. Architect Step 5 design: one email per owner with the
          multi-row table the owner edits in their reply. Fix: group eligible
          items by owner_corp_usa_email (live from SP via Path A), render one
          outreach_table.j2 with all rows, send one email.

    Side effect: send_initial_outreach_on_collection_start rule no longer
    fires (kickoff sends the email + transitions inline). The rule can stay
    in the YAML dormant until a use case (manual TPM trigger) needs it.

    Outcomes returned via dict:
      - "skipped_missing_identity" -- event_context missing customer/milestone
      - "skipped_no_storage"       -- storage method missing
      - "fired"                    -- counts per owner_groups / items / emails_sent
    """
    deps = get_task_deps()

    customer_id = event_context.get("customer_id")
    milestone_id = event_context.get("milestone_id")
    if not customer_id or not milestone_id:
        logger.warning(
            "kickoff_collection_skip_missing_identity: customer_id=%r milestone_id=%r",
            customer_id, milestone_id,
        )
        return {"outcome": "skipped_missing_identity", "emails_sent": 0}

    list_method = getattr(deps.storage, "list_items_for_milestone", None)
    if list_method is None:
        logger.warning("kickoff_collection_skip_no_storage_method")
        return {"outcome": "skipped_no_storage", "emails_sent": 0}

    items = list_method(milestone_id, None) or []
    if not items:
        logger.info(
            "kickoff_collection_empty_milestone: customer_id=%s milestone_id=%s",
            customer_id, milestone_id,
        )
        return {"outcome": "fired", "emails_sent": 0, "items_scanned": 0}

    # Eligibility filter: force_tracking_enabled=true AND delivery_state="Not Started".
    # Confirmation items are INCLUDED per FR-58 corrected 2026-06-28.
    eligible = [
        item for item in items
        if getattr(item, "force_tracking_enabled", False) is True
        and (getattr(item, "delivery_state", None) or "") == "Not Started"
    ]
    if not eligible:
        logger.info(
            "kickoff_collection_no_eligible_items: customer_id=%s milestone_id=%s "
            "items_scanned=%d (none in Not Started)",
            customer_id, milestone_id, len(items),
        )
        return {
            "outcome":        "fired",
            "emails_sent":    0,
            "items_scanned":  len(items),
            "items_eligible": 0,
        }

    # Lazy imports (workflow_engine.tasks transitively imports tracker in
    # other flows; keeping this here avoids circular-import friction).
    from core.src.tracker import DeliveryState
    from core.src.tracker.transitions import update_delivery_state
    from core.src.workflow_engine.tasks.outreach import (
        _send_batch_outreach_email,
    )
    import uuid as _uuid

    correlation_id = event_context.get("correlation_id", str(_uuid.uuid4()))

    # -- Owner resolution via Path A SP batch read ---
    # One SP query returns all milestone items in one shot; we lookup owner
    # identity per (milestone_id, project_model, item_no) tuple. Fallbacks
    # ensure the cascade doesn't block if SP is unreachable -- the per-item
    # owner_corp_email column on the local snapshot (None today) is the
    # final fallback.
    owner_map = _resolve_owners_for_eligible(deps, customer_id, milestone_id, eligible)

    # Group eligible items by owner_corp_usa_email (preferred per [D-080]).
    # Items with no resolvable owner go into a __no_owner__ group -- they
    # still get state-transitioned but no email is sent for that group.
    owner_groups: dict[str, list[Any]] = {}
    for item in eligible:
        item_id = getattr(item, "item_id", None) or getattr(item, "delivery_item_id", None)
        owner_info = owner_map.get(item_id) or {}
        owner_key = (
            owner_info.get("owner_corp_usa_email")
            or owner_info.get("owner_corp_email")
            or "__no_owner__"
        )
        owner_groups.setdefault(owner_key, []).append(item)

    emails_sent = 0
    items_transitioned = 0
    items_failed = 0

    for owner_key, group_items in owner_groups.items():
        # Build identity dict for the template (owner_name from SP).
        sample_owner_info = owner_map.get(
            getattr(group_items[0], "item_id", "")
            or getattr(group_items[0], "delivery_item_id", ""),
            {},
        )
        owner_identity = {
            "owner_corp_usa_email": sample_owner_info.get("owner_corp_usa_email") or owner_key,
            "owner_corp_email":     sample_owner_info.get("owner_corp_email"),
            "owner_corp_id":        sample_owner_info.get("owner_corp_id"),
            "owner_name":           sample_owner_info.get("owner_name"),
        }
        # Build item dicts for the table render.
        item_dicts = [
            {
                "item_no":   getattr(it, "item_no", None),
                "item_name": getattr(it, "item_name", None) or f"Item {getattr(it, 'item_no', '?')}",
            }
            for it in group_items
        ]
        # Deterministic batch_id from (correlation_id, owner_key) prefix.
        batch_seed = f"{correlation_id}-{owner_key}"
        batch_id = f"BATCH-{_uuid.uuid5(_uuid.NAMESPACE_URL, batch_seed).hex[:10]}"

        # Step 1: transition each item NS -> Open.
        for item in group_items:
            item_id = getattr(item, "item_id", None) or getattr(item, "delivery_item_id", None)
            try:
                update_delivery_state(
                    delivery_item_id=item_id,
                    target_state=DeliveryState.OPEN,
                    params={},
                    event_context={
                        "correlation_id":   correlation_id,
                        "customer_id":      customer_id,
                        "milestone_id":     milestone_id,
                        "delivery_item_id": item_id,
                        "trigger_source":   "kickoff_collection_task",
                    },
                    storage=deps.storage,
                    sp_writer=deps.sp_writer,
                    audit=deps.audit,
                    bypass_guards=False,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "kickoff_collection: NS->Open failed for item=%s: %s: %s",
                    item_id, type(exc).__name__, str(exc)[:120],
                )
                items_failed += 1

        # Step 2: send the batch email (skipped when owner is unresolved).
        message_id: str | None = None
        if owner_key != "__no_owner__" and deps.email_sender is not None:
            try:
                message_id = _send_batch_outreach_email(
                    deps=deps,
                    owner_identity=owner_identity,
                    items=item_dicts,
                    batch_id=batch_id,
                    recipient=owner_key,
                )
                if message_id:
                    emails_sent += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "kickoff_collection: batch outreach send failed for owner=%s: %s: %s",
                    owner_key, type(exc).__name__, str(exc)[:120],
                )

        # Step 3: audit each item with send_initial_outreach (recipient + batch_id +
        # message_id common to the whole group) and transition Open -> Outreach Sent.
        for item in group_items:
            item_id = getattr(item, "item_id", None) or getattr(item, "delivery_item_id", None)
            deps.audit.write_communication_log(
                action_type="send_initial_outreach",
                delivery_item_id=item_id,
                attribution={
                    "trigger_source": "kickoff_collection_task",
                    "correlation_id": correlation_id,
                    "modified_by":    "system",
                },
                details={
                    "template":     "outreach_table",
                    "channel":      "email",
                    "recipient":    owner_key if owner_key != "__no_owner__" else None,
                    "batch_id":     batch_id,
                    "batch_size":   len(group_items),
                    "milestone_id": milestone_id,
                    "message_id":   message_id,
                    "send_skipped": message_id is None,
                    # item_no added 2026-06-28 so Phase B owner_reply_task can
                    # resolve (batch_id, item_no) -> delivery_item_id without
                    # round-tripping the delivery_item row. Lookup falls back
                    # to the live row's item_no when this key is absent
                    # (back-compat with rows audited before this commit).
                    "item_no":      getattr(item, "item_no", None),
                },
            )
            try:
                update_delivery_state(
                    delivery_item_id=item_id,
                    target_state=DeliveryState.OUTREACH_SENT,
                    params={},
                    event_context={
                        "correlation_id":   correlation_id,
                        "customer_id":      customer_id,
                        "milestone_id":     milestone_id,
                        "delivery_item_id": item_id,
                        "trigger_source":   "kickoff_collection_task",
                    },
                    storage=deps.storage,
                    sp_writer=deps.sp_writer,
                    audit=deps.audit,
                    bypass_guards=False,
                )
                items_transitioned += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "kickoff_collection: Open->Outreach Sent failed for item=%s: %s: %s",
                    item_id, type(exc).__name__, str(exc)[:120],
                )
                items_failed += 1

    # -- Audit log the kickoff aggregate --
    deps.audit.write_communication_log(
        action_type="collection_kickoff_dispatched",
        delivery_item_id=None,
        attribution={
            "correlation_id": correlation_id,
            "trigger_source": "milestone_collection_started",
        },
        details={
            "customer_id":       customer_id,
            "milestone_id":      milestone_id,
            "items_scanned":     str(len(items)),
            "items_eligible":    str(len(eligible)),
            "owner_groups":      str(len(owner_groups)),
            "emails_sent":       str(emails_sent),
            "items_transitioned": str(items_transitioned),
            "items_failed":      str(items_failed),
        },
    )

    logger.info(
        "kickoff_collection_fired: customer_id=%s milestone_id=%s "
        "items_scanned=%d eligible=%d owner_groups=%d emails_sent=%d "
        "items_transitioned=%d items_failed=%d",
        customer_id, milestone_id, len(items), len(eligible), len(owner_groups),
        emails_sent, items_transitioned, items_failed,
    )
    return {
        "outcome":            "fired",
        "items_scanned":      len(items),
        "items_eligible":     len(eligible),
        "owner_groups":       len(owner_groups),
        "emails_sent":        emails_sent,
        "items_transitioned": items_transitioned,
        "items_failed":       items_failed,
    }


def _resolve_owners_for_eligible(
    deps, customer_id: str, milestone_id: str, eligible: list[Any],
) -> dict[str, dict[str, Any]]:
    """SP-batch-read all eligible items' owner identities in one query.

    Returns map: item_id -> {owner_corp_usa_email, owner_corp_email,
                             owner_corp_id, owner_name}

    Best-effort: failures yield an empty map (callers fall back to None
    owner per item -> __no_owner__ bucket -> no email sent for those).
    """
    out: dict[str, dict[str, Any]] = {}
    if deps.sp_writer is None or not eligible:
        return out

    # Build per-item lookup key (project_model, item_no). Group by project_model
    # so we can issue one SP query per device variant within the milestone.
    by_device: dict[str, list[Any]] = {}
    for item in eligible:
        device_id = getattr(item, "device_id", None)
        if device_id:
            by_device.setdefault(device_id, []).append(item)

    from core.src.sharepoint_integration.config import ListScope
    scope = ListScope(customer_id=customer_id)

    for device_id, group in by_device.items():
        try:
            rows = deps.sp_writer.get_items(
                entity="delivery_items",
                scope=scope,
                canonical_filters={
                    "milestone_id":  milestone_id,
                    "project_model": device_id,
                },
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kickoff_collection: SP batch read failed for device=%s: %s",
                device_id, type(exc).__name__,
            )
            continue

        # Build (item_no -> SP row) lookup, then back-fill our items.
        sp_by_item_no: dict[Any, dict[str, Any]] = {
            r.get("item_no"): r for r in rows if r.get("item_no") is not None
        }
        for item in group:
            item_id = getattr(item, "item_id", None) or getattr(item, "delivery_item_id", None)
            item_no = getattr(item, "item_no", None)
            r = sp_by_item_no.get(item_no)
            if r is None:
                continue
            out[item_id] = {
                "owner_corp_usa_email": r.get("owner_corp_usa_email"),
                "owner_corp_email":     r.get("owner_corp_email"),
                "owner_corp_id":        r.get("owner_corp_id"),
                "owner_name":           r.get("owner_name"),
            }
    return out


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
