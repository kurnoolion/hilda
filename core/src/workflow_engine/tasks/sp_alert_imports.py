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


def _parse_item_description(raw: Any) -> list | None:
    """Parse FR-82 item_description value into list-of-lists (AND-of-OR groups).

    Architect 2026-06-29: SP list column type is 'Note' (multi-line text);
    SP serializes the value as the literal Python/JSON representation, e.g.
    [["Sustainability"]] or [['SDoc'],['Qualification','Product']]. In SP
    alert email body_kvs this arrives as a string; via SP REST it MAY arrive
    as the parsed list already (canonical_field map dependent).

    Use ast.literal_eval to handle both single- and double-quoted forms
    safely (json.loads rejects single quotes). Returns None for empty /
    unparseable inputs; the Fr52 Step B1 matcher's _extract_tag_groups treats
    None as 'skip this candidate' which is the desired behaviour.
    """
    if raw is None:
        return None
    if isinstance(raw, list):
        # Already parsed (e.g. via SP REST canonical_field map). Return as-is;
        # _extract_tag_groups will normalize the shape.
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text or text.lower() in ("null", "none"):
            return None
        try:
            import ast
            value = ast.literal_eval(text)
            if isinstance(value, list):
                return value
        except (ValueError, SyntaxError):
            pass
    return None


def _build_delivery_item(
    *,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    body_kvs: dict[str, str],
    item_title: str,
) -> DeliveryItemBase:
    """Map body_kvs (parsed SP ADDED alert) + template.yaml (cached lookup)
    to a DeliveryItemBase Pydantic model.

    Sourcing rules per architect design pass 2026-07-02:
      - Template-authoritative fields (doc_count, tracking_modality,
        milestone_gating, item_type, item_description, tg_path_id,
        item_path_id, form-factor flags): read from template.yaml at import.
        SP alert body_kvs values are unreliable at setup time (SP omits them
        from the ADDED alert body). Later CHANGED alerts with Edited markers
        can still override via the sync task (chunk 4) with a null-guard.
      - Template-seeded + SP-editable (no_customer_upload, force_tracking_enabled,
        review_required, target_folder): read from template.yaml at import;
        SP CHANGED alerts override at runtime.
      - SP-only fields (delivery_state, owner_*, tg_* identity fields,
        owner_status_note, pm_approval_*): sourced from body_kvs; no template
        involvement.

    Graceful degradation: if template lookup misses (customer without a
    template file, template load failure, milestone/item not in template),
    fall back to body_kvs-only mapping -- matches pre-2026-07-02 behavior so
    we never fail-hard the import for a lookup miss.
    """
    item_no = _to_int(body_kvs.get("item_no"))
    # Synthesize composite-key item_id per [D-091] (customer_id-device_id-
    # milestone_id-item_no); storage may override with its own scheme.
    item_id = f"{customer_id}-{device_id}-{milestone_id}-{item_no}"

    # -- Template lookup (2026-07-02 architect design pass) -------------------
    # Best-effort: falls back to None if template not cached / not found. Every
    # helper below tolerates tmpl=None and reverts to body_kvs / model defaults.
    from core.src.template_schema import template_lookup
    tmpl = template_lookup.get_workitem(
        customer_id=customer_id,
        device_id=device_id,
        milestone_id=milestone_id,
        item_no=item_no,
    )

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
        # -- SP-only: item_name is SP-editable at runtime; body wins --
        item_name=item_title or body_kvs.get("Title") or (tmpl.get("item_name") if tmpl else None) or f"Item {item_no}",
        # -- Template-authoritative: item_type, item_description, tracking_modality,
        #    milestone_gating, tg_path_id, item_path_id, form-factor flags. --
        item_type=(tmpl.get("item_type") if tmpl else None) or body_kvs.get("item_type") or "Default",
        # -- SP-only state fields (dynamic; never in template) --
        delivery_state=body_kvs.get("delivery_state", "Not Started"),
        item_completion_pct=_to_int(body_kvs.get("item_completion_pct"), 0),
        # Owner identity per [D-105] 4-field -- SP-authoritative (TPM reassignable):
        owner_corp_usa_email=(body_kvs.get("owner_corp_usa_email") or None),
        owner_corp_email=(body_kvs.get("owner_corp_email") or None),
        owner_corp_id=(body_kvs.get("owner_corp_id") or None),
        owner_name=(body_kvs.get("owner_name") or None),
        # TG-denormalized per [D-106] -- SP-authoritative (Owner-Group cascade):
        tg_name=(body_kvs.get("tg_name") or None),
        tg_email_group_alias=(body_kvs.get("tg_email_group_alias") or None),
        tg_owner_name=(body_kvs.get("tg_owner_name") or None),
        tg_owner_corp_usa_email=(body_kvs.get("tg_owner_corp_usa_email") or None),
        tg_owner_corp_email=(body_kvs.get("tg_owner_corp_email") or None),
        tg_owner_corp_id=(body_kvs.get("tg_owner_corp_id") or None),
        # Tracking gates per FR-81 + FR-78 -- template-authoritative (modality) +
        # template-seeded + SP-editable (force_tracking_enabled, no_customer_upload):
        tracking_modality=_tmpl_list(tmpl, "tracking_modality") or _modality_to_list(body_kvs.get("tracking_modality")),
        force_tracking_enabled=_tmpl_bool(tmpl, "force_tracking_enabled", body_kvs, default=True),
        no_customer_upload=_tmpl_bool(tmpl, "no_customer_upload", body_kvs, default=False),
        # Review gates per FR-7 / FR-53 / FR-70 -- template-seeded + SP-editable:
        review_required=_tmpl_bool(tmpl, "review_required", body_kvs, default=False),
        # Milestone gating per FR-78 -- template-authoritative:
        milestone_gating=_tmpl_bool(tmpl, "milestone_gating", body_kvs, default=True),
        # Routing fields per FR-77 -- ingress_folder SP-only; target_folder
        # template-seeded + SP-editable:
        ingress_folder=(body_kvs.get("ingress_folder") or None),
        target_folder=_tmpl_str(tmpl, "target_folder", body_kvs),
        # Customer-level GDrive base URL denormalized per-item at import.
        # Fix 2026-07-02: previously not populated at import -- customer_adapter's
        # google_drive_base.upload_attachment raises CAD-E010 when this field is
        # empty on the item (data-config error per D-126 architect Q3 lock
        # 2026-06-26). Seeded from customer-level `customer_delivery_info` in
        # template.yaml; body_kvs override if SP row carries a per-row value.
        customer_delivery_info=(
            (body_kvs.get("customer_delivery_info") or None)
            or template_lookup.get_customer_delivery_info(customer_id)
        ),
        # Path components per FR-78 -- template-authoritative:
        path_id=(tmpl.get("path_id") if tmpl else None) or body_kvs.get("path_id") or f"item_{item_no}",
        tg_path_id=_tmpl_str(tmpl, "tg_path_id", body_kvs),
        item_path_id=_tmpl_str(tmpl, "item_path_id", body_kvs),
        # FR-82 item_description tags (AND-of-OR groups for FR-52 Step B1) --
        # template-authoritative. TSC-W008: doc_count MUST equal len(item_description)
        # for deliverable items; template.yaml validation guarantees this, so sourcing
        # both from template naturally cures the "doc_count=0 but len(item_description)=N"
        # consistency warnings observed in 2026-07-02 live smoke.
        item_description=_parse_item_description(tmpl.get("item_description")) if tmpl else _parse_item_description(body_kvs.get("item_description")),
        # FR-7 doc_count -- template-authoritative:
        doc_count=(int(tmpl["doc_count"]) if tmpl and tmpl.get("doc_count") is not None else _to_int(body_kvs.get("doc_count"), 1)),
        # sort_order -- template-seeded (falls back to item_no):
        sort_order=(int(tmpl["sort_order"]) if tmpl and tmpl.get("sort_order") is not None else _to_int(body_kvs.get("sort_order"), item_no)),
        # Form-factor flags per [D-084] -- template-authoritative:
        handset=_tmpl_bool(tmpl, "handset", body_kvs, default=False),
        tablet=_tmpl_bool(tmpl, "tablet", body_kvs, default=False),
        wearable=_tmpl_bool(tmpl, "wearable", body_kvs, default=False),
        ir=_tmpl_bool(tmpl, "ir", body_kvs, default=False),
        osmr=_tmpl_bool(tmpl, "osmr", body_kvs, default=False),
        rmr=_tmpl_bool(tmpl, "rmr", body_kvs, default=False),
        hmr_smr=_tmpl_bool(tmpl, "hmr_smr", body_kvs, default=False),
        # Timestamps:
        last_updated=datetime.now(timezone.utc),
        # FR-87 / FR-83 + JIRA / PM-approval fields use model defaults
        # (None / False / 0) until operationally exercised.
    )


# ---------------------------------------------------------------------------
# Template-vs-body merge helpers (2026-07-02 architect design pass)
# ---------------------------------------------------------------------------
# Each helper follows the same pattern: TEMPLATE wins at import time; body_kvs
# is fallback when template is missing OR template doesn't carry the key.
# Type-aware "null" detection:
#   - int: 0 treated as null (indistinguishable from parser default)
#   - str: None or "" treated as null
#   - list: empty list treated as null
#   - bool: template value used verbatim if present (False is legitimate);
#           body_kvs fallback checks key presence to distinguish missing from
#           explicit "No".


def _tmpl_bool(
    tmpl: dict[str, Any] | None,
    key: str,
    body_kvs: dict[str, str],
    *,
    default: bool,
) -> bool:
    """Template value wins if present (True or False both count as present).
    Body_kvs fallback via key presence (distinguishes missing from explicit
    'No' -> False). Model default is last resort.
    """
    if tmpl is not None and key in tmpl and tmpl[key] is not None:
        return bool(tmpl[key])
    if key in body_kvs and body_kvs[key]:
        return _yn_to_bool(body_kvs[key], default=default)
    return default


def _tmpl_str(
    tmpl: dict[str, Any] | None,
    key: str,
    body_kvs: dict[str, str],
) -> str | None:
    """Template string wins if non-empty; body_kvs fallback; None if both empty."""
    if tmpl is not None:
        v = tmpl.get(key)
        if v:
            return str(v)
    return body_kvs.get(key) or None


def _tmpl_list(tmpl: dict[str, Any] | None, key: str) -> list[str]:
    """Template list wins if non-empty; caller falls back to body_kvs parse."""
    if tmpl is None:
        return []
    v = tmpl.get(key)
    if isinstance(v, list) and v:
        return [str(x) for x in v]
    return []


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
    # Fix 2026-07-03: device_id added to prevent cross-device dedup at import.
    # Prior (customer, tg, item_no) key incorrectly matched item_no=1 across
    # 10 devices, dropping 9 of 10 ADDED alerts as "already_exists" per live
    # test (SM-S671U1 + 9 more Samsung SKUs setup burst -> 105 emails arrived
    # but only 11 rows landed in Postgres). Same natural key without device_id
    # is still used by FR-82 tag_propagation which INTENTIONALLY spans devices.
    tg_name = body_kvs.get("tg_name", "")
    existing = deps.storage.find_items_by_natural_key(
        customer_id=customer_id,
        device_id=device_id,
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

    # -- Populate sp_id (SP Deliverables list row Id) via natural-key READ --
    # 2026-07-01 architect lock: sp_id is not carried in the ADDED alert body,
    # so we do a single SP READ post-import to resolve it. Powers the dashboard
    # /docs/<customer_id>/<sp_id> URL. Best-effort -- import succeeds even when
    # this fails; sp_id stays NULL and dashboard is unreachable for that row
    # until a backfill runs. Import failure would falsely block the whole
    # tracker for a dashboard-only concern.
    if deps.sp_writer is not None:
        try:
            from core.src.sharepoint_integration.config import ListScope
            sp_rows = deps.sp_writer.get_items(
                entity="delivery_items",
                scope=ListScope(customer_id=customer_id),
                canonical_filters={
                    "project_model": device_id,
                    "item_no":       item_no,
                },
            )
            resolved_sp_id = None
            for r in sp_rows or []:
                v = r.get("_sp_id") or r.get("Id") or r.get("ID")
                if v is not None:
                    try:
                        resolved_sp_id = int(v)
                        break
                    except (ValueError, TypeError):
                        continue
            if resolved_sp_id is not None:
                deps.storage.update_delivery_item(new_id, {"sp_id": resolved_sp_id})
                logger.info(
                    "import_deliverable_tracker_sp_id_resolved: item_id=%s sp_id=%s",
                    new_id, resolved_sp_id,
                )
            else:
                logger.warning(
                    "import_deliverable_tracker_sp_id_absent: item_id=%s -- "
                    "SP returned %d rows for (project_model=%s, item_no=%s) but "
                    "none carried _sp_id/Id/ID; dashboard link unreachable "
                    "until backfill.",
                    new_id, len(sp_rows or []), device_id, item_no,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "import_deliverable_tracker_sp_id_failed: item_id=%s: %s: %s",
                new_id, type(exc).__name__, str(exc)[:120],
            )

    # -- Auto-transition Not Started -> Open (serialization per architect
    # 2026-07-15). Two-phase Ph-1 setup flow:
    #   1. TPM clicks Setup Deliverables in SP -> SP creates Deliverables
    #      rows with delivery_state=Not Started per FR-81 default -> SP fires
    #      ADDED alerts -> HILDA imports (this task) -> HILDA transitions
    #      each item to Open in BOTH Postgres AND SP (via update_delivery_state).
    #   2. SP UI engineer gates Start Tracking button visibility on all items
    #      being Open -> TPM clicks Start Tracking -> SP sets
    #      milestone_collection_started_at -> HILDA kickoff_collection_task
    #      transitions Open -> OutreachSent and sends outreach.
    #
    # Applies to ALL item types including Confirmation and Default work items
    # per architect ask (moves everything to Open uniformly; kickoff still
    # excludes Default from outreach eligibility per FR-78, and Confirmation
    # items also get outreach per FR-58 corrected 2026-06-28).
    #
    # Best-effort: transition failure logs a warning but does NOT roll back
    # the import. Postgres row exists at delivery_state=Not Started; a manual
    # bump / reconcile can recover.
    try:
        from core.src.tracker import DeliveryState as _DS
        from core.src.tracker.transitions import update_delivery_state as _uds
        _uds(
            delivery_item_id=new_id,
            target_state=_DS.OPEN,
            params={},
            event_context={
                "correlation_id":  event_context.get("correlation_id", "?"),
                "customer_id":     customer_id,
                "milestone_id":    milestone_id,
                "delivery_item_id": new_id,
                "trigger_source":  "automated",
                "rule_id":         "import_deliverable_tracker",
            },
            storage=deps.storage,
            sp_writer=deps.sp_writer,
            audit=deps.audit,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "import_deliverable_tracker: NS->Open transition failed for "
            "item_id=%s: %s: %s (postgres row remains at Not Started; "
            "kickoff filter accepts both states as of 2026-07-15 so this "
            "does not block the milestone cascade)",
            new_id, type(exc).__name__, str(exc)[:120],
        )

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
    OutreachSent.

    Triggered by Milestones CHANGED alert with field_deltas containing
    milestone_collection_started_at (per architect direction 2026-06-26).

    Architect Step 5 2026-06-28 restructure -- two problems fixed in one pass:
      (1) Re-fire on already-processed items: prior implementation dispatched
          ItemCreated for every eligible item regardless of delivery_state.
          Items already in OutreachSent re-fired the cascade -> Outreach
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
    # Fix 2026-07-06: device_id scoping. SP Milestones has ONE row per
    # (customer, device, milestone) triple; TPM clicking Start Collection
    # updates only ONE row (one device) -> ONE alert -> HILDA should kick
    # off only THAT device's items in the milestone. Without device_id
    # scoping, list_items_for_milestone(milestone_id) returns ALL devices'
    # items and outreach fires for all of them -- observed on live test
    # 2026-07-06 (TPM started collection for SM-R921U only; kickoff
    # transitioned all 10 devices' items to OutreachSent).
    device_id = event_context.get("device_id")
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

    # Eligibility filter: force_tracking_enabled=true AND delivery_state in
    # ("Not Started", "Open") AND item_type != "Default".
    #
    # Confirmation items are INCLUDED per FR-58 corrected 2026-06-28.
    #
    # Not Started + Open accepted per architect 2026-07-15 serialization ask:
    # import_deliverable_tracker_task now auto-transitions Not Started -> Open
    # on ADDED alert so SP UI can gate Start Tracking button on items being at
    # Open. Kickoff must therefore treat Open as eligible; the internal
    # transition step still runs NS->Open which becomes an idempotent no-op
    # for items already advanced. Leaving Not Started in the filter preserves
    # backward compat with rows imported before this change AND handles the
    # rare case where import's auto-transition partially failed (Postgres
    # write ok, SP write failed) -- the item stays at Not Started in Postgres
    # and would otherwise be stranded here.
    #
    # Default work items EXCLUDED per architect 2026-07-02: default WI has FR-78
    # hardcoded invariant force_tracking_enabled=False, no owner, no outreach
    # surface. But the SP UI engineer's script that creates the default WI row
    # may leave force_tracking_enabled at the SP column default (True per
    # FR-81) instead of explicitly setting False, and the parser can't
    # distinguish "field missing from body_kvs" from "field explicitly True".
    # Result: default WI leaked into eligible, transitioned NS -> Open ->
    # OutreachSent silently (no email sent because __no_owner__ bucket).
    # Explicit item_type guard here is defense-in-depth against SP-side
    # field-default drift.
    _ELIGIBLE_START_STATES = ("Not Started", "Open")
    eligible = [
        item for item in items
        if getattr(item, "force_tracking_enabled", False) is True
        and (getattr(item, "delivery_state", None) or "") in _ELIGIBLE_START_STATES
        and (getattr(item, "item_type", None) or "") != "Default"
        # device_id scoping per fix 2026-07-06 (see top-of-function comment):
        # kickoff only touches items for the device whose Milestone row was
        # edited. When device_id is None in event_context (defensive; the
        # dispatcher always populates it for Milestones alerts), fall back
        # to milestone-wide behavior to preserve historical semantics.
        and (device_id is None
             or (getattr(item, "device_id", None) or "") == device_id)
    ]
    if not eligible:
        logger.info(
            "kickoff_collection_no_eligible_items: customer_id=%s milestone_id=%s "
            "items_scanned=%d (none in Not Started/Open)",
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

    # Back-fill metadata fields (item_description, tg_path_id, item_path_id)
    # from SP to local row. Architect 2026-06-29: prior ADDED import code path
    # didn't carry these, leaving Fr52 substring matcher blind. Refresh here
    # at kickoff time -- one SP read already happened above for owner identity;
    # we just persist 3 additional fields from the same response. Best-effort
    # per-item; failures don't block kickoff.
    backfilled = 0
    for item in eligible:
        item_id = getattr(item, "item_id", None) or getattr(item, "delivery_item_id", None)
        if not item_id:
            continue
        info = owner_map.get(item_id) or {}
        updates: dict[str, Any] = {}
        if info.get("item_description") is not None:
            updates["item_description"] = info["item_description"]
        if info.get("tg_path_id"):
            updates["tg_path_id"] = info["tg_path_id"]
        if info.get("item_path_id"):
            updates["item_path_id"] = info["item_path_id"]
        if not updates:
            continue
        try:
            deps.storage.update_delivery_item(item_id, updates)
            backfilled += 1
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "kickoff_collection: metadata back-fill failed for item=%s: %s",
                item_id, type(exc).__name__,
            )
    if backfilled:
        logger.info(
            "kickoff_collection: metadata back-filled from SP for %d/%d items",
            backfilled, len(eligible),
        )

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
        # 2026-07-08: propagate customer_id / device_id / milestone_id per-row
        # so the outreach subject enrichment in _send_batch_outreach_email can
        # surface them without a signature change. All items in the batch
        # share the same triple by construction (kickoff scopes to the
        # milestone alert's (customer, device, milestone) triple), but the
        # per-row copy is harmless and keeps the template + email code
        # independent of the surrounding task's scope.
        item_dicts = [
            {
                "item_no":      getattr(it, "item_no", None),
                "item_name":    getattr(it, "item_name", None) or f"Item {getattr(it, 'item_no', '?')}",
                "customer_id":  customer_id,
                "device_id":    device_id
                                or getattr(it, "device_id", None)
                                or getattr(it, "project_model", None),
                "milestone_id": milestone_id,
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
        # message_id common to the whole group) and transition Open -> OutreachSent.
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
                    "kickoff_collection: Open->OutreachSent failed for item=%s: %s: %s",
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
                # Metadata fields back-filled at kickoff per architect 2026-06-29:
                # initial ADDED import may have left these NULL (older code path);
                # kickoff is the natural point to refresh from SP since the same
                # SP batch read happens here anyway.
                "item_description":     _parse_item_description(r.get("item_description")),
                "tg_path_id":           r.get("tg_path_id"),
                "item_path_id":         r.get("item_path_id"),
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
