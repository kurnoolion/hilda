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
    "get_expected_item_count_for_milestone",
    "get_drr_version",
    "get_drr_section_grouping",
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


def get_expected_item_count_for_milestone(
    customer_id: str, device_id: str, milestone_id: str,
) -> int | None:
    """SETUP-3 (2026-07-29): return the count of work_items the template
    defines for (customer, device, milestone). Used by
    setup_complete_notification's expected-count gate to avoid the
    partial-import false-positive that fires "N items ready" mid-import.

    Semantics:
      * Returns len(milestone.work_items) if template cached AND device_id
        is in milestone.devices (or the field is absent -- non-strict).
      * Returns 0 if device_id is explicitly scoped out (this device
        legitimately has no items for this milestone).
      * Returns None if template not cached / milestone not present /
        work_items missing -- caller falls back to an SP read.

    Notes:
      * Counts entries (not max item_no) so reserved-but-skipped item_no
        gaps (e.g., #85 reserved but #86 skipped in yaml) don't inflate
        the expected count.
      * INCLUDES the Default WI. Per architect 2026-07-29: template.yaml
        already carries the Default WI (single row with item_type=Default);
        Setup Deliverables writes it to SP alongside the rest; HILDA's
        instantiate_default_work_item no-ops when the row already exists.
        Template count == SP row count == Postgres row count; no Default-WI
        offset to worry about on the Postgres-side comparison.
    """
    template = _CACHE.get(customer_id)
    if template is None:
        return None

    milestones = template.get("milestones") or {}
    if isinstance(milestones, list):
        milestones = {
            m.get("milestone_id"): m
            for m in milestones
            if isinstance(m, dict) and m.get("milestone_id")
        }
    if not isinstance(milestones, dict):
        return None

    milestone = milestones.get(milestone_id)
    if not isinstance(milestone, dict):
        return None

    scope = milestone.get("devices")
    if isinstance(scope, list) and scope and device_id not in scope:
        return 0

    work_items = milestone.get("work_items") or []
    if not isinstance(work_items, list):
        return None
    return len(work_items)


# ---------------------------------------------------------------------------
# DRR-V2-1 (Ph-1 2026-08-03): DRR-Excel-generation helpers
# ---------------------------------------------------------------------------
#
# The final DRR Excel deliverable (D-158 sibling / DRR-V2 cascade) needs
# two structural pieces from template.yaml that don't fit the existing
# get_workitem contract:
#
#   1. Customer-specific `{customer_id}_template_version` at the template
#      root (e.g. `MMK_template_version: 5.7`, `ATT_template_version: 3.4`).
#      Rendered in the DRR excel header as "DRR Version <N>".
#
#   2. Per-item `parent` + `P1_yellow_marker` under each DRR milestone
#      work_item — `parent` names the section header row ("Product
#      Documentation Review", "Pre-Submission items", etc.);
#      `P1_yellow_marker` flags whether the row gets yellow highlighting
#      in the Ph-1 Submission Gating banner.
#
# These helpers are intentionally read-only + tolerant (missing template
# returns None; caller decides fail-loud). The excel builder (DRR-V2-5)
# is where "parent MUST be present" is enforced -- keeping the check
# there lets the fail-loud message be excel-context-rich.


def get_drr_version(customer_id: str) -> str | None:
    """Return the customer's DRR template version string from template.yaml
    root -- key format `{customer_id}_template_version`. Returns None when
    the template isn't cached OR the key is absent OR the value is empty.

    Generic per-customer: `MMK_template_version`, `ATT_template_version`,
    etc. Caller (DRR excel builder) renders as `DRR Version <N>` in the
    header block; missing value emits blank + warning per architect ask
    (2026-08-03 spec Q6).
    """
    template = _CACHE.get(customer_id)
    if template is None:
        return None
    key = f"{customer_id}_template_version"
    val = template.get(key)
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def list_known_devices(customer_id: str) -> list[str] | None:
    """Return the customer's whitelist of known device_ids from
    template.yaml's `devices:` block (dict keys per [D-091]).

    DEV-FILTER-1 (2026-08-06): SP UI engineer's test environment feeds
    sample-device SP alerts into the shared inbox. The email_polling
    filter uses this helper to drop any SP alert whose `project_model`
    (device_id) isn't in the customer's template.yaml devices block --
    keeps Postgres clean for the real devices only.

    Returns:
      * None  — customer's template.yaml not cached (fallback: caller
                should PASS THROUGH the alert, not filter, so a config
                miss doesn't accidentally drop real alerts).
      * []    — template exists but `devices:` block absent or empty
                (fallback: caller should PASS THROUGH too — treating
                empty as "any device allowed" avoids surprise drops
                during template-migration windows).
      * list  — the device_ids the customer declares.
    """
    template = _CACHE.get(customer_id)
    if template is None:
        return None
    devices = template.get("devices")
    if not isinstance(devices, dict):
        return []
    return [str(k) for k in devices.keys() if k]


def get_drr_logo_filename(customer_id: str) -> str | None:
    """Return the DRR-header logo filename from template.yaml root-level
    `drr_branding_logo` key (e.g. `verizon.png`). Returns None when the
    template isn't cached OR the key is absent OR the value is empty.

    DRR-V2-6 (2026-08-05): the customer may be delivering a
    carrier-branded DRR excel (MMK -> Verizon), so the brand name is
    controlled by template.yaml rather than derived from customer_id.
    Caller (tpm_notification._resolve_logo_path) resolves the filename
    against customizations/branding/ probe paths; missing file → None →
    excel builder silent-skips the image embed.
    """
    template = _CACHE.get(customer_id)
    if template is None:
        return None
    val = template.get("drr_branding_logo")
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def get_drr_section_grouping(
    customer_id: str, device_id: str, milestone_id: str,
) -> list[dict[str, Any]] | None:
    """Group work_items by their `parent` field, preserving item_no order.

    Returns a list of {'section': <parent name>, 'work_items': [<wi>, ...]}
    dicts in the order of first-occurrence -- section header emitted when
    the current item's `parent` differs from the previous item's `parent`.

    Excludes item#85 (Final DRR excel deliverable), item#86 (Ph-1-only
    non-DRR-docs placeholder — item_name "Stadium, Private Network, Skylo,
    DR"; used to let TPM ingest non-DRR docs during Ph-1 program) and
    item#87 (Default WI) per architect ask 2026-08-03 spec Q1 + 2026-08-04
    #86 addition — they are not part of the checklist Verizon sees. If
    those item_no values ever change per customer, revisit this filter.

    Returns None when template not cached or milestone not present. Missing
    or empty `parent` on an item is NOT enforced here — caller (DRR excel
    builder) fail-louds with the missing-parent item_nos so the error
    message is excel-context-rich (architect ask Q3).

    Sort key: int(item_no) ascending. Non-numeric item_no items sort last
    (defensive; shouldn't happen in a healthy template).
    """
    template = _CACHE.get(customer_id)
    if template is None:
        return None

    milestones = template.get("milestones") or {}
    if isinstance(milestones, list):
        milestones = {
            m.get("milestone_id"): m
            for m in milestones
            if isinstance(m, dict) and m.get("milestone_id")
        }
    if not isinstance(milestones, dict):
        return None

    milestone = milestones.get(milestone_id)
    if not isinstance(milestone, dict):
        return None

    scope = milestone.get("devices")
    if isinstance(scope, list) and scope and device_id not in scope:
        return None

    raw = milestone.get("work_items") or []
    if not isinstance(raw, list):
        return None

    # Filter: exclude item#85 + item#86 + item#87 per architect ask; sort by item_no.
    _EXCLUDED_ITEM_NOS = {85, 86, 87}
    filtered: list[dict[str, Any]] = []
    for wi in raw:
        if not isinstance(wi, dict):
            continue
        try:
            n = int(wi.get("item_no", -1))
        except (TypeError, ValueError):
            n = -1
        if n in _EXCLUDED_ITEM_NOS:
            continue
        filtered.append(wi)

    def _sort_key(wi: dict[str, Any]) -> tuple[int, int]:
        try:
            return (0, int(wi.get("item_no", 999999)))
        except (TypeError, ValueError):
            return (1, 999999)

    filtered.sort(key=_sort_key)

    # Group-by-parent preserving first-occurrence order. Iterating a sorted
    # list and starting a new group whenever `parent` differs from the
    # previous item's parent is the correct emit order for the excel's
    # section-header-then-rows pattern -- items sharing a `parent` are
    # already adjacent by item_no assumption (template author responsibility).
    groups: list[dict[str, Any]] = []
    prev_parent: str | None = "__sentinel__"
    for wi in filtered:
        parent = wi.get("parent")
        if parent != prev_parent:
            groups.append({"section": parent, "work_items": []})
            prev_parent = parent
        groups[-1]["work_items"].append(wi)

    return groups
