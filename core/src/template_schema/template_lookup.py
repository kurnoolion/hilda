"""template_lookup.py -- runtime work-item lookup cache from customer template.yaml.

Added 2026-07-02 per architect design pass: SP alert emails for Deliverables
ADDED events are missing template-defined fields at setup time (doc_count,
target_folder, tracking_modality, milestone_gating, item_type, item_description,
tg_path_id, item_path_id, form-factor flags, plus the SP-editable seeds
no_customer_upload / force_tracking_enabled / review_required). HILDA relies
on template.yaml as the initial-value source; SP alerts are still allowed to
override at runtime (subject to a null-guard so accidental null-back-from-SP
doesn't erase seeded values).

This module is the shared read-only lookup layer used by:
  - import_deliverable_tracker_task._build_delivery_item (initial import merge)
  - sync_deliverable_fields_task (Deliverables CHANGED alert merge)
  - scripts/backfill_static_fields_from_template.py (one-shot backfill of
    existing rows imported before this cascade landed)

Cache is process-local and eager: bootstrap_task_deps calls
load_all_customer_templates() at worker startup so lookups never hit disk.
Reload semantics for Ph-2 multi-customer live in the same shape (walk all
customizations/template_schemas/*/template.yaml at load time).

Best-effort throughout -- a missing / malformed template.yaml logs a warning
and returns None from get_workitem, letting the caller degrade to body_kvs-
only behaviour (matches how the code worked before this cascade landed).
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

__all__ = [
    "load_all_customer_templates",
    "load_customer_template",
    "get_workitem",
    "get_customer_delivery_info",
    "get_delivery_path_template",
    "clear_cache",
]

_log = logging.getLogger(__name__)

# Cache: customer_id -> parsed template dict (raw yaml.safe_load result).
# Populated by load_all_customer_templates() / load_customer_template().
_CACHE: dict[str, dict[str, Any]] = {}


def clear_cache() -> None:
    """Test / re-init hook. Not used in prod paths."""
    _CACHE.clear()


def load_all_customer_templates(
    base_dir: Path | None = None,
) -> dict[str, bool]:
    """Walk customizations/template_schemas/*/template.yaml and cache each.

    Directory name is used as customer_id (matches the convention in
    bootstrap._load_template_yaml). Returns a per-customer load-result map for
    observability -- True means cached; False means load failed (warning logged).
    """
    if base_dir is None:
        # Repo root is 3 levels above this file's dir (core/src/template_schema).
        base_dir = Path(__file__).resolve().parents[3] / "customizations" / "template_schemas"

    results: dict[str, bool] = {}
    if not base_dir.is_dir():
        _log.warning(
            "template_lookup: base_dir=%s not a directory; no templates loaded",
            base_dir,
        )
        return results

    for customer_dir in sorted(base_dir.iterdir()):
        if not customer_dir.is_dir():
            continue
        customer_id = customer_dir.name
        template_path = customer_dir / "template.yaml"
        if not template_path.exists():
            continue
        ok = load_customer_template(customer_id, template_path)
        results[customer_id] = ok

    _log.info(
        "template_lookup: loaded %d/%d customer templates (base_dir=%s)",
        sum(1 for ok in results.values() if ok), len(results), base_dir,
    )
    return results


def load_customer_template(
    customer_id: str,
    template_path: Path | None = None,
) -> bool:
    """Load one customer's template.yaml into the cache. Returns True on
    success, False on any failure (warning logged; cache slot left untouched).
    """
    if template_path is None:
        base_dir = Path(__file__).resolve().parents[3] / "customizations" / "template_schemas"
        template_path = base_dir / customer_id / "template.yaml"

    if not template_path.exists():
        _log.warning(
            "template_lookup: template.yaml not found for customer_id=%s at %s",
            customer_id, template_path,
        )
        return False

    try:
        with template_path.open("r", encoding="utf-8") as f:
            parsed = yaml.safe_load(f)
    except Exception as exc:  # noqa: BLE001 -- best-effort
        _log.warning(
            "template_lookup: yaml load failed for customer_id=%s: %s: %s",
            customer_id, type(exc).__name__, str(exc)[:120],
        )
        return False

    if not isinstance(parsed, dict):
        _log.warning(
            "template_lookup: template.yaml for customer_id=%s is not a mapping "
            "(got %s)",
            customer_id, type(parsed).__name__,
        )
        return False

    _CACHE[customer_id] = parsed
    return True


def get_workitem(
    *,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    item_no: int,
) -> dict[str, Any] | None:
    """Look up a work_item dict by (customer_id, device_id, milestone_id, item_no).

    Template shape (verified from mock_customer/template.yaml 2026-07-02):
      root = { devices: {device_id: {...}}, milestones: {milestone_id: {devices: [...], work_items: [...]}} }

    device_id is used ONLY as a validity check: milestone.devices must contain
    it. work_items themselves are per-milestone (not per-device-milestone) --
    the same list applies to every device the milestone targets. Returning None
    when device_id is not in milestone.devices catches typos / data-drift early.

    Returns the raw work_item dict from the parsed YAML on hit, None on any
    miss (customer/milestone/item not found, device scope mismatch, or template
    not cached).
    """
    template = _CACHE.get(customer_id)
    if template is None:
        _log.info(
            "template_lookup: no cached template for customer_id=%s (call "
            "load_customer_template first; fall-through to body_kvs-only)",
            customer_id,
        )
        return None

    milestones = template.get("milestones") or {}
    if not isinstance(milestones, dict):
        # Some templates might use list form; handle gracefully.
        if isinstance(milestones, list):
            milestones = {
                m.get("milestone_id"): m
                for m in milestones
                if isinstance(m, dict) and m.get("milestone_id")
            }
        else:
            return None

    milestone = milestones.get(milestone_id)
    if not isinstance(milestone, dict):
        return None

    # Optional device-scope check: milestone.devices is a list of device_ids
    # this milestone applies to. Non-strict -- if the field is missing / empty,
    # skip the check (older templates may omit it).
    scope = milestone.get("devices")
    if isinstance(scope, list) and scope and device_id not in scope:
        _log.info(
            "template_lookup: device_id=%s not in milestone %s scope %s "
            "(customer_id=%s item_no=%d) -- template row not applicable",
            device_id, milestone_id, scope, customer_id, item_no,
        )
        return None

    work_items = milestone.get("work_items") or []
    if not isinstance(work_items, list):
        return None
    for wi in work_items:
        if isinstance(wi, dict) and int(wi.get("item_no", -1)) == int(item_no):
            return wi
    return None


def get_customer_delivery_info(customer_id: str) -> str | None:
    """Return customer-level `customer_delivery_info` (GDrive base URL) from
    template.yaml root. Denormalized onto each DeliveryItem at import time so
    submit_to_carrier can pass it to the customer_adapter (which raises
    CAD-E010 if the field is missing/empty on the item).
    """
    template = _CACHE.get(customer_id)
    if template is None:
        return None
    val = template.get("customer_delivery_info")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None


def get_delivery_path_template(customer_id: str) -> str | None:
    """Return customer-level `delivery_path_template` from template.yaml root.
    Used by submit_to_carrier + customer_adapter for path composition.
    """
    template = _CACHE.get(customer_id)
    if template is None:
        return None
    val = template.get("delivery_path_template")
    if isinstance(val, str) and val.strip():
        return val.strip()
    return None
