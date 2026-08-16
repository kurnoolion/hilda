"""sync_deliverable_fields.py -- Deliverables CHANGED alert -> Postgres merge.

Added 2026-07-02 per architect design pass. Companion to the import-time
template.yaml merge in sp_alert_imports._build_delivery_item. This task handles
the runtime case: TPM edits a template-seeded field in SP UI (target_folder,
no_customer_upload, force_tracking_enabled, review_required, or any of the
template-authoritative fields if edited); SP fires a CHANGED alert with the
new value in body_kvs / field_deltas; this task applies the update to Postgres.

Null-guard (architect lock 2026-07-02): if the SP alert carries a null / empty
value for a tracked field, the update is SKIPPED for that field. Rationale:
SP alerts are unreliable at setup time (fields missing from body_kvs); the
import task already seeded the field from template.yaml. If SP later sends a
null for that field via an unrelated CHANGED alert, we do NOT want to erase
the seeded value. Only non-null SP values overwrite Postgres.

Since we skip on null, we don't need to fetch the current Postgres row -- the
skip preserves whatever's already there. Type-aware "null" per field:
  - int (doc_count, sort_order):  0 or unparseable treated as null
  - str (target_folder, tg_path_id, item_path_id, item_type):  None / "" null
  - bool (no_customer_upload, force_tracking_enabled, review_required,
    milestone_gating, form-factor flags):  key missing OR value empty = null
  - list (tracking_modality, item_description):  empty list = null

Fields NOT touched by this task (SP-authoritative via other tasks / other paths):
  delivery_state, owner_*, tg_* identity fields, owner_status_note,
  pm_approval_at, pm_approval_pm_id, milestone _triggered_at, last_updated,
  item_name, item_completion_pct (Ph-2 per architect 2026-07-02).
"""
from __future__ import annotations

import logging
from typing import Any

from core.src.rule_engine import ActionKind
from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps
from core.src.workflow_engine.tasks.sp_alert_imports import (
    _modality_to_list,
    _parse_item_description,
    _split_owner_list,        # OWNER-2 (2026-08-14): shared helper
    _to_int,
    _yn_to_bool,
)

__all__ = ["sync_deliverable_fields_task"]

_log = logging.getLogger(__name__)


_STR_FIELDS = ("target_folder", "tg_path_id", "item_path_id", "item_type", "path_id",
               # NSD2-7 (2026-08-13): ingress_folder is SP-authoritative per FR-77
               # (per-deployment path, template.yaml intentionally can't seed it).
               # Prior to this addition, post-import TPM edits to ingress_folder
               # silently didn't propagate to Postgres, blocking NSD2 polling for
               # HW PL items whose ingress_folder was set post-import.
               "ingress_folder",
               # PLM-2 (2026-08-14): plm_id + actual_item_info are HILDA-written
               # after the beat-fired PLM poll creates a ticket (via
               # on_prem_client.create_plm_ticket + get_actual_item_info). SP is
               # authoritative once written. Adding to sync allowlist so any TPM
               # SP edit (e.g. manual case-id correction, URL override) round-
               # trips back to Postgres via the Deliverables-CHANGED alert path.
               "plm_id", "actual_item_info")
_BOOL_FIELDS = (
    "no_customer_upload",
    "force_tracking_enabled",
    "review_required",
    "milestone_gating",
    "handset",
    "tablet",
    "wearable",
    "ir",
    "osmr",
    "rmr",
    "hmr_smr",
)


@hilda_celery_app.task(
    name="core.src.workflow_engine.tasks.sync_deliverable_fields.sync_deliverable_fields",
)
def sync_deliverable_fields_task(
    params: dict[str, Any], event_context: dict[str, Any],
) -> dict[str, Any]:
    """SYNC_DELIVERABLE_FIELDS -- merge Deliverables CHANGED body_kvs into Postgres
    with null-guard per architect 2026-07-02.

    params: unused (rule passes empty dict).
    event_context: dispatcher-built dict carrying:
      - customer_id, delivery_item_id: from EntityRef
      - derived_fields.body_kvs: dict[str, str] from parsed SP body

    Returns dict with outcome + list of fields updated.
    """
    deps = get_task_deps()
    correlation_id = event_context.get("correlation_id", "")
    customer_id = event_context.get("customer_id")
    delivery_item_id = event_context.get("delivery_item_id")

    if not customer_id or not delivery_item_id:
        _log.info(
            "sync_deliverable_fields_skip_missing_identity: customer_id=%r "
            "delivery_item_id=%r correlation_id=%s",
            customer_id, delivery_item_id, correlation_id,
        )
        return {"outcome": "skipped_missing_identity", "fields_updated": 0}

    derived = event_context.get("derived_fields") or {}
    body_kvs: dict[str, str] = derived.get("body_kvs") or {}
    if not body_kvs:
        return {"outcome": "skipped_no_body_kvs", "fields_updated": 0}

    # Storage presence check -- graceful skip matching other tasks' pattern.
    update_fn = getattr(deps.storage, "update_delivery_item", None)
    if update_fn is None:
        _log.warning(
            "sync_deliverable_fields_skip_no_storage: delivery_item_id=%s",
            delivery_item_id,
        )
        return {"outcome": "skipped_no_storage", "fields_updated": 0}

    updates: dict[str, Any] = {}

    # -- int fields (doc_count, sort_order): skip 0 / unparseable per null-guard.
    _merge_int(updates, body_kvs, "doc_count")
    _merge_int(updates, body_kvs, "sort_order")

    # -- str fields: skip None / empty per null-guard.
    for key in _STR_FIELDS:
        _merge_str(updates, body_kvs, key)

    # -- bool fields: skip if key missing OR value empty per null-guard.
    for key in _BOOL_FIELDS:
        _merge_bool(updates, body_kvs, key)

    # -- list fields: tracking_modality (str list) + item_description (list-of-lists).
    _merge_list(updates, body_kvs, "tracking_modality", _modality_to_list)
    _merge_item_description(updates, body_kvs)

    # -- OWNER-2 (2026-08-14): 4 owner-identity fields dual-written per OWNER-1
    # additive schema. SP text column carries semicolon-separated string
    # ("alice@corp; bob@corp"); _split_owner_list parses to list; we set BOTH
    # the singular field (= first parsed entry, backward-compat for pre-OWNER-3
    # readers) AND the _list field (= full parsed list, new authoritative
    # storage). Null-guard applies: if parse yields empty list, skip both --
    # preserves whatever singular/list values are already in Postgres.
    _merge_owner_field(updates, body_kvs, "owner_name",           "owner_name_list")
    _merge_owner_field(updates, body_kvs, "owner_corp_email",     "owner_corp_email_list")
    _merge_owner_field(updates, body_kvs, "owner_corp_usa_email", "owner_corp_usa_email_list")
    _merge_owner_field(updates, body_kvs, "owner_corp_id",        "owner_corp_id_list")

    if not updates:
        return {
            "outcome":         "no_updates",
            "delivery_item_id": delivery_item_id,
            "fields_updated":  0,
        }

    try:
        update_fn(delivery_item_id, updates)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "sync_deliverable_fields_update_failed: delivery_item_id=%s: %s: %s",
            delivery_item_id, type(exc).__name__, str(exc)[:120],
        )
        _audit(deps, "sync_deliverable_fields_failed", delivery_item_id, {
            "customer_id":    customer_id,
            "correlation_id": correlation_id,
            "error":          type(exc).__name__,
            "fields":         list(updates.keys()),
        })
        return {
            "outcome":         "failed",
            "delivery_item_id": delivery_item_id,
            "fields_updated":  0,
        }

    _log.info(
        "sync_deliverable_fields_synced: delivery_item_id=%s fields=%s "
        "correlation_id=%s",
        delivery_item_id, sorted(updates.keys()), correlation_id,
    )
    _audit(deps, "sync_deliverable_fields_synced", delivery_item_id, {
        "customer_id":    customer_id,
        "correlation_id": correlation_id,
        "fields":         sorted(updates.keys()),
    })
    return {
        "outcome":          "synced",
        "delivery_item_id": delivery_item_id,
        "fields_updated":   len(updates),
        "fields":           sorted(updates.keys()),
    }


# ---------------------------------------------------------------------------
# Null-guarded merge helpers
# ---------------------------------------------------------------------------


def _merge_int(updates: dict[str, Any], body_kvs: dict[str, str], key: str) -> None:
    """Apply body_kvs[key] as int; skip on 0 / unparseable (null-guard)."""
    if key not in body_kvs:
        return
    raw = body_kvs[key]
    if not raw:
        return
    try:
        val = int(raw)
    except (TypeError, ValueError):
        return
    if val == 0:
        # 0 treated as null-like (indistinguishable from parser default);
        # skip so template-seeded non-zero value in Postgres is preserved.
        return
    updates[key] = val


def _merge_str(updates: dict[str, Any], body_kvs: dict[str, str], key: str) -> None:
    """Apply body_kvs[key] stripped; skip on None / empty (null-guard)."""
    if key not in body_kvs:
        return
    raw = body_kvs[key]
    if not raw:
        return
    val = raw.strip()
    if not val:
        return
    updates[key] = val


def _merge_bool(updates: dict[str, Any], body_kvs: dict[str, str], key: str) -> None:
    """Apply body_kvs[key] as bool; skip on key missing or empty value.

    Since 'Yes'/'No' are both truthy strings, presence-with-non-empty-value is
    the correct signal that SP explicitly sent a value. Empty string = null.
    """
    if key not in body_kvs:
        return
    raw = body_kvs[key]
    if not raw:
        return
    updates[key] = _yn_to_bool(raw)


def _merge_list(
    updates: dict[str, Any],
    body_kvs: dict[str, str],
    key: str,
    parser,
) -> None:
    """Apply parser(body_kvs[key]); skip on empty list (null-guard)."""
    if key not in body_kvs:
        return
    raw = body_kvs[key]
    if not raw:
        return
    val = parser(raw)
    if not val:
        return
    updates[key] = val


def _merge_owner_field(
    updates: dict[str, Any],
    body_kvs: dict[str, str],
    singular_name: str,
    list_name: str,
) -> None:
    """OWNER-2 (2026-08-14): dual-write an owner identity field. Reads the
    SP text column at `singular_name` (e.g. 'owner_corp_email'), parses via
    _split_owner_list (handles ';' + ',' delimiters), and writes:
      updates[singular_name] = parsed[0]              # first entry (BC)
      updates[list_name]     = parsed                 # full parsed list

    Null-guard: if body_kvs key is missing OR parsed list is empty, skip
    BOTH writes (preserves whatever singular/list are already in Postgres).
    Consistent with _merge_str/_merge_list null-preserve semantics.

    OWNER-7 will drop singular_name + rename list_name back to unsuffixed."""
    if singular_name not in body_kvs:
        return
    raw = body_kvs[singular_name]
    parsed = _split_owner_list(raw)
    if not parsed:
        return
    updates[singular_name] = parsed[0]
    updates[list_name] = parsed


def _merge_item_description(updates: dict[str, Any], body_kvs: dict[str, str]) -> None:
    """FR-82 item_description = list of list of str; skip on None / empty."""
    if "item_description" not in body_kvs:
        return
    raw = body_kvs["item_description"]
    val = _parse_item_description(raw)
    # DEBUG-1 (2026-07-26 architect ask): item_description sync provenance.
    # sync_deliverable_fields fires on every Deliverables-CHANGED alert. Empty
    # parse → skip (defends against SP dropping the field mid-cascade). We log
    # both the raw + parsed values so a future empty-write investigation has
    # the full picture of what SP sent us.
    if not val:
        _log.info(
            "item_description SYNC-SKIP: body_raw=%r parsed=%r (empty; not merging into updates)",
            raw, val,
        )
        return
    _log.info(
        "item_description SYNC-MERGE: body_raw=%r parsed=%r (will be written to Postgres)",
        raw, val,
    )
    updates["item_description"] = val


def _audit(
    deps: Any,
    action_type: str,
    delivery_item_id: str | None,
    details: dict[str, Any],
) -> None:
    """Best-effort audit writer -- failures never break the outer flow."""
    if deps.audit is None:
        return
    try:
        deps.audit.write_communication_log(
            action_type=action_type,
            delivery_item_id=delivery_item_id,
            attribution={
                "trigger_source": "sp_alert_sync",
                "correlation_id": details.get("correlation_id", ""),
                "modified_by":    "system",
            },
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "sync_deliverable_fields: audit write failed: %s",
            type(exc).__name__,
        )


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------

register_task_binding(TaskBinding(
    action_kind=ActionKind.SYNC_DELIVERABLE_FIELDS,
    celery_task=sync_deliverable_fields_task,
    queue="default",
))
