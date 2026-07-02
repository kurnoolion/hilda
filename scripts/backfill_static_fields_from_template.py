"""backfill_static_fields_from_template.py -- one-shot backfill for existing rows.

Added 2026-07-02 per architect design pass. Fixes DeliveryItem rows in Postgres
that were imported before the template.yaml merge cascade landed -- specifically
those that show doc_count=0 / target_folder=None / tracking_modality=[] etc. in
Postgres because SP alert body_kvs was missing these fields at setup time.

Live-observed symptom 2026-07-02 15:35:16,196-198:
    WARNING doc_count consistency violation - item 'MMK-SM-S671U1-P1-N' has
    doc_count=0 but len(item_description)=1 per FR-82 architect lock 2026-06-20

Runs the same template-merge logic as import_deliverable_tracker_task's
_build_delivery_item but as a null-guarded update on existing rows: only fills
fields that are currently null/empty/default. Non-null / non-default Postgres
values are preserved (so a legitimate TPM edit made post-import isn't clobbered
by the template default).

Usage:
    # From repo root, with HILDA env vars for DB pointing at prod Postgres:
    python -m scripts.backfill_static_fields_from_template
    # Or dry-run first to see what would change:
    python -m scripts.backfill_static_fields_from_template --dry-run
    # Scope to one customer:
    python -m scripts.backfill_static_fields_from_template --customer MMK
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Repo-root on sys.path when invoked as a plain script (avoids ImportError
# for `core.src.*` without `python -m`).
_repo_root = Path(__file__).resolve().parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
_log = logging.getLogger("backfill")


# Template-authoritative fields (never SP-edited in Ph-1 practice):
_STR_FIELDS_AUTHORITATIVE = ("item_type", "tg_path_id", "item_path_id")
_INT_FIELDS_AUTHORITATIVE = ("doc_count",)
_LIST_FIELDS_AUTHORITATIVE = ("tracking_modality",)
_BOOL_FIELDS_AUTHORITATIVE = (
    "milestone_gating",
    "handset", "tablet", "wearable", "ir", "osmr", "rmr", "hmr_smr",
)

# Template-seeded but SP-editable (backfill only if Postgres is at default):
_STR_FIELDS_SEEDED = ("target_folder",)
_BOOL_FIELDS_SEEDED = ("no_customer_upload", "force_tracking_enabled", "review_required")


def _is_str_empty(v) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _is_list_empty(v) -> bool:
    return v is None or (isinstance(v, list) and len(v) == 0)


def _compute_updates(row, tmpl: dict, *, seeded_defaults: dict[str, object]) -> dict[str, object]:
    """Return updates dict for one row, respecting null-guard semantics.

    row: sqlalchemy row (or DeliveryItemBase); accessed via getattr.
    tmpl: work_item dict from template_lookup.get_workitem.
    seeded_defaults: model defaults for SP-editable fields; only backfill if
        Postgres value equals the model default (i.e., untouched since import).
    """
    updates: dict[str, object] = {}

    for key in _STR_FIELDS_AUTHORITATIVE:
        cur = getattr(row, key, None)
        tmpl_v = tmpl.get(key)
        if _is_str_empty(cur) and tmpl_v is not None and str(tmpl_v).strip() != "":
            updates[key] = str(tmpl_v)

    for key in _INT_FIELDS_AUTHORITATIVE:
        cur = getattr(row, key, None)
        tmpl_v = tmpl.get(key)
        # doc_count: backfill if current is None OR 0 and template says non-zero.
        # Also backfill 0 -> 0 case is fine (idempotent no-op via `cur != tmpl_v`).
        if tmpl_v is None:
            continue
        try:
            tmpl_int = int(tmpl_v)
        except (TypeError, ValueError):
            continue
        cur_int = int(cur) if cur is not None else 0
        # Backfill only if current appears "unset" (0) OR mismatches template
        # and current is 0 (default). Non-zero legitimate values (e.g., TPM
        # edited doc_count in SP) are preserved by requiring cur == 0.
        if cur_int == 0 and tmpl_int != 0:
            updates[key] = tmpl_int

    for key in _LIST_FIELDS_AUTHORITATIVE:
        cur = getattr(row, key, None)
        tmpl_v = tmpl.get(key)
        if _is_list_empty(cur) and isinstance(tmpl_v, list) and tmpl_v:
            updates[key] = [str(x) for x in tmpl_v]

    for key in _BOOL_FIELDS_AUTHORITATIVE:
        # Backfill bools only when template value is present.
        tmpl_v = tmpl.get(key)
        if tmpl_v is None:
            continue
        cur = getattr(row, key, None)
        # Template-authoritative -- overwrite if differs from template. (Bools
        # don't have a null-guard concept; template is truth for these.)
        if bool(cur) != bool(tmpl_v):
            updates[key] = bool(tmpl_v)

    # item_description: template list-of-lists; backfill if current is None / empty.
    cur_desc = getattr(row, "item_description", None)
    tmpl_desc = tmpl.get("item_description")
    if _is_list_empty(cur_desc) and isinstance(tmpl_desc, list) and tmpl_desc:
        updates["item_description"] = tmpl_desc

    # Template-seeded + SP-editable: only backfill if Postgres value matches
    # the pre-cascade model default (i.e., untouched since import; no TPM edit).
    for key in _STR_FIELDS_SEEDED:
        cur = getattr(row, key, None)
        tmpl_v = tmpl.get(key)
        default = seeded_defaults.get(key)
        if cur == default and tmpl_v is not None and str(tmpl_v).strip() != "":
            updates[key] = str(tmpl_v)

    for key in _BOOL_FIELDS_SEEDED:
        cur = getattr(row, key, None)
        tmpl_v = tmpl.get(key)
        default = seeded_defaults.get(key)
        if tmpl_v is None:
            continue
        if cur == default and bool(tmpl_v) != bool(default):
            updates[key] = bool(tmpl_v)

    return updates


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show updates without writing")
    parser.add_argument("--customer", type=str, default=None, help="Limit to one customer_id")
    args = parser.parse_args()

    # Load templates
    from core.src.template_schema import template_lookup
    template_lookup.load_all_customer_templates()

    # Storage
    from core.src.storage._sync_bridge import run_async_sync
    from core.src.storage.config import GlobalStorageConfig
    from core.src.storage.db import DeliveryItemTable, configure_engine, get_session

    cfg = GlobalStorageConfig.from_sources()
    configure_engine(url=cfg.db_url)

    # Iterate all rows via async session.
    from sqlalchemy import select

    seeded_defaults = {
        "target_folder":          None,
        "no_customer_upload":     False,
        "force_tracking_enabled": True,   # per DeliveryItemBase model default
        "review_required":        False,
    }

    scanned = 0
    updated = 0
    skipped_no_template = 0
    skipped_no_updates = 0
    per_field_counts: dict[str, int] = {}

    async def _run():
        nonlocal scanned, updated, skipped_no_template, skipped_no_updates
        async with get_session() as session:
            stmt = select(DeliveryItemTable)
            if args.customer:
                stmt = stmt.where(DeliveryItemTable.customer_id == args.customer)
            result = await session.execute(stmt)
            rows = result.scalars().all()
            for row in rows:
                scanned += 1
                customer_id = getattr(row, "customer_id", None)
                device_id = getattr(row, "device_id", None)
                milestone_id = getattr(row, "milestone_id", None)
                item_no = getattr(row, "item_no", None)
                if not (customer_id and device_id and milestone_id and item_no is not None):
                    skipped_no_template += 1
                    continue
                tmpl = template_lookup.get_workitem(
                    customer_id=customer_id,
                    device_id=device_id,
                    milestone_id=milestone_id,
                    item_no=int(item_no),
                )
                if tmpl is None:
                    skipped_no_template += 1
                    continue
                updates = _compute_updates(row, tmpl, seeded_defaults=seeded_defaults)
                if not updates:
                    skipped_no_updates += 1
                    continue
                for k in updates:
                    per_field_counts[k] = per_field_counts.get(k, 0) + 1
                _log.info(
                    "row item_id=%s updates=%s",
                    getattr(row, "item_id", "?"), updates,
                )
                if not args.dry_run:
                    for k, v in updates.items():
                        setattr(row, k, v)
                updated += 1
            if not args.dry_run:
                await session.commit()

    run_async_sync(_run)

    _log.info(
        "backfill %s: scanned=%d updated=%d skipped_no_template=%d skipped_no_updates=%d",
        "DRY-RUN" if args.dry_run else "APPLIED",
        scanned, updated, skipped_no_template, skipped_no_updates,
    )
    if per_field_counts:
        _log.info("backfill per-field counts: %s", dict(sorted(per_field_counts.items())))
    return 0


if __name__ == "__main__":
    sys.exit(main())
