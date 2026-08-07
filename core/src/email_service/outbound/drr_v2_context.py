"""DRR-V2 context builder + SP-read helpers — celery-FREE.

Extracted from workflow_engine/tasks/tpm_notification.py 2026-08-06
(DRR-DL-1a) so the dashboard container (which doesn't ship celery) can
call the same code path the beat tick uses. tpm_notification.py now
re-exports these names for backward compat.

Contract: pure functions, no side effects other than logging. Every
helper takes a `deps` object with a `sp_writer` attribute exposing
`get_items(entity=..., scope=ListScope(customer_id=...), ...)`.
Never raises — SP transport failure / row miss / blank field returns
None + WARN log so downstream callers (excel builder, one-shot script,
dashboard route) can render blank cells / degrade gracefully.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

__all__ = [
    "MILESTONE_HEADER_FIELDS",
    "PROJECT_HEADER_FIELDS",
    "BRANDING_DIRS",
    "read_milestones",
    "read_milestone_headers",
    "read_project_headers",
    "read_deliverables_comments",
    "resolve_logo_path",
    "build_drr_v2_context",
    "row_get",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

MILESTONE_HEADER_FIELDS: tuple[str, ...] = (
    "fld_lockdown_date",
    "req_version",
    "target_date",
)

PROJECT_HEADER_FIELDS: tuple[str, ...] = (
    "LE",
    "FFW",
)

# Probe locations for the DRR-header brand logo. Both host + container
# paths; whichever exists wins.
BRANDING_DIRS: tuple[Path, ...] = (
    Path("customizations/branding"),
    Path("/app/customizations/branding"),
)


# ---------------------------------------------------------------------------
# Row-shape helper (SP rows may arrive as dict OR SimpleNamespace-like)
# ---------------------------------------------------------------------------


def row_get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


# ---------------------------------------------------------------------------
# SP reads
# ---------------------------------------------------------------------------


def read_milestones(deps: Any, customer_id: str) -> list[dict[str, Any]]:
    """Read the GLOBAL Milestones SP list rows filtered by carrier=customer_id."""
    from core.src.sharepoint_integration.config import ListScope
    rows = deps.sp_writer.get_items(
        entity="milestones",
        scope=ListScope(customer_id=customer_id),
    )
    rows = list(rows or [])
    filtered = [
        r for r in rows
        if (row_get(r, "carrier") or "").strip() == customer_id.strip()
    ]
    _log.info(
        "drr_v2_context: read %d milestones (filtered %d out of %d total) "
        "for customer=%s",
        len(filtered), len(rows) - len(filtered), len(rows), customer_id,
    )
    return filtered


def read_milestone_headers(
    deps: Any,
    customer_id: str,
    device_id: str,
    milestone_id: str,
) -> dict[str, Any]:
    """Read fld_lockdown_date + req_version + target_date from the Milestones
    SP row keyed by (carrier=customer_id, device=device_id, milestone=milestone_id).
    Never raises. SP transport failures / row misses -> dict of Nones + WARN."""
    empty = {k: None for k in MILESTONE_HEADER_FIELDS}
    try:
        rows = read_milestones(deps, customer_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "drr_v2_context: read_milestone_headers: milestones read failed "
            "customer=%s device=%s milestone=%s: %s: %s",
            customer_id, device_id, milestone_id,
            type(exc).__name__, str(exc)[:120],
        )
        return empty

    device_key = (device_id or "").strip()
    milestone_key = (milestone_id or "").strip()
    match = None
    for r in rows:
        r_device = (
            row_get(r, "project_model")
            or row_get(r, "device_id")
            or ""
        )
        r_milestone = (
            row_get(r, "milestone_id")
            or row_get(r, "Title")
            or ""
        )
        if str(r_device).strip() == device_key and str(r_milestone).strip() == milestone_key:
            match = r
            break
    if match is None:
        _log.warning(
            "drr_v2_context: read_milestone_headers: no milestone row matched "
            "customer=%s device=%s milestone=%s (scanned %d row(s))",
            customer_id, device_id, milestone_id, len(rows),
        )
        return empty

    out: dict[str, Any] = {}
    for field in MILESTONE_HEADER_FIELDS:
        raw = row_get(match, field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            _log.warning(
                "drr_v2_context: read_milestone_headers: field %r blank on "
                "milestone customer=%s device=%s milestone=%s -- DRR header "
                "cell will render blank",
                field, customer_id, device_id, milestone_id,
            )
            out[field] = None
        else:
            out[field] = raw
    return out


def read_project_headers(
    deps: Any,
    customer_id: str,
    device_id: str,
) -> dict[str, Any]:
    """Read LE + FFW from the Projects_<customer_id> row keyed on
    project_model=device_id. Never raises."""
    from core.src.sharepoint_integration.config import ListScope

    empty = {k: None for k in PROJECT_HEADER_FIELDS}
    try:
        rows = deps.sp_writer.get_items(
            entity="projects",
            scope=ListScope(customer_id=customer_id),
            canonical_filters={"project_model": device_id},
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "drr_v2_context: read_project_headers: projects read failed "
            "customer=%s device=%s: %s: %s",
            customer_id, device_id,
            type(exc).__name__, str(exc)[:120],
        )
        return empty

    rows = list(rows or [])
    if not rows:
        _log.warning(
            "drr_v2_context: read_project_headers: no Projects row matched "
            "customer=%s device=%s -- DRR header LE + FFW cells will render blank",
            customer_id, device_id,
        )
        return empty

    match = rows[0]
    out: dict[str, Any] = {}
    for field in PROJECT_HEADER_FIELDS:
        raw = row_get(match, field)
        if raw is None or (isinstance(raw, str) and not raw.strip()):
            _log.warning(
                "drr_v2_context: read_project_headers: field %r blank on "
                "project customer=%s device=%s -- DRR header cell will render blank",
                field, customer_id, device_id,
            )
            out[field] = None
        else:
            out[field] = raw
    return out


def read_deliverables_comments(
    deps: Any,
    customer_id: str,
    device_id: str,
    milestone_id: str,
) -> dict[int, str]:
    """Fetch fresh TPM-authored `comment` values from the deliverables_<customer_id>
    SP list, keyed by item_no. Used by the DRR-V2 excel Remarks column so
    the download reflects what TPM most recently typed in SP without
    depending on the Deliverables-CHANGED alert path having synced to
    Postgres yet.

    COMMENT-SYNC-1 (2026-08-07): direct SP read at download time,
    matching the header-block pattern (read_milestone_headers /
    read_project_headers). Trade-off: one extra SP call per Download
    click; always fresh, no alert-sync dependency.

    Never raises. SP transport failure / row miss -> empty dict + WARN.
    Rows missing item_no or comment are silently skipped.
    """
    from core.src.sharepoint_integration.config import ListScope

    empty: dict[int, str] = {}
    try:
        rows = deps.sp_writer.get_items(
            entity="delivery_items",
            scope=ListScope(customer_id=customer_id),
            canonical_filters={
                "project_model": device_id,
                # Deliverables_<customer> canonical key is `milestone_id`
                # (customer.yaml maps it to the SP internal column, usually
                # "milestone_name"). Do NOT use `milestone_name` here --
                # that's the Milestones-list canonical.
                "milestone_id": milestone_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "drr_v2_context: read_deliverables_comments: SP read failed "
            "customer=%s device=%s milestone=%s: %s: %s -- Remarks column "
            "will fall back to Postgres comment values",
            customer_id, device_id, milestone_id,
            type(exc).__name__, str(exc)[:120],
        )
        return empty

    out: dict[int, str] = {}
    scanned = 0
    for r in rows or []:
        scanned += 1
        raw_no = row_get(r, "item_no")
        try:
            item_no = int(raw_no) if raw_no is not None else None
        except (TypeError, ValueError):
            item_no = None
        if item_no is None:
            continue
        raw_comment = row_get(r, "comment")
        if raw_comment is None:
            continue
        s = str(raw_comment).strip()
        if not s:
            continue
        out[item_no] = s
    _log.info(
        "drr_v2_context: read_deliverables_comments: %d non-empty comments "
        "from %d rows for customer=%s device=%s milestone=%s",
        len(out), scanned, customer_id, device_id, milestone_id,
    )
    return out


def resolve_logo_path(customer_id: str) -> Path | None:
    """Resolve the DRR-header logo path from template.yaml's
    `drr_branding_logo` filename against the BRANDING_DIRS probe paths."""
    from core.src.template_schema import template_lookup

    filename = template_lookup.get_drr_logo_filename(customer_id)
    if not filename:
        return None
    for base in BRANDING_DIRS:
        candidate = base / filename
        if candidate.is_file():
            return candidate
    _log.warning(
        "drr_v2_context: DRR logo %r declared in template but not found "
        "under %s -- excel will render without logo",
        filename, [str(b) for b in BRANDING_DIRS],
    )
    return None


def build_drr_v2_context(
    deps: Any,
    customer_id: str,
    device_id: str,
    milestone_id: str,
) -> dict[str, Any]:
    """Compose every kwarg build_drr_report_excel() needs in DRR-V2 mode.

    Returned dict is meant to be spread directly into the builder:

        ctx = build_drr_v2_context(deps, customer, device, milestone)
        xlsx_bytes = build_drr_report_excel(items=items, **ctx)

    When template_lookup can't resolve section_grouping (missing template
    / missing milestone / template not cached), returned dict carries
    section_grouping=None so the builder falls back to the legacy
    4-column flat sheet.
    """
    from core.src.template_schema import template_lookup

    section_grouping = template_lookup.get_drr_section_grouping(
        customer_id, device_id, milestone_id,
    )
    drr_version = template_lookup.get_drr_version(customer_id)
    milestone_headers = read_milestone_headers(
        deps, customer_id, device_id, milestone_id,
    )
    project_headers = read_project_headers(deps, customer_id, device_id)
    logo_path = resolve_logo_path(customer_id)

    return {
        "customer_id":       customer_id,
        "device_id":         device_id,
        "milestone_id":      milestone_id,
        "section_grouping":  section_grouping,
        "drr_version":       drr_version,
        "milestone_headers": milestone_headers,
        "project_headers":   project_headers,
        "logo_path":         str(logo_path) if logo_path else None,
    }
