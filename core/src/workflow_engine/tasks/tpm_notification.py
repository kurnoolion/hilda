"""tpm_notification.py -- scheduled DRR closure final-status email to TPM.

Per architect 2026-07-15 serialization+notification design pass:
  Two emails per (customer, device, milestone) tuple, both at 00:00
  US/Eastern:
    * Email 1: on `milestone.target_date - 1`  (day-before nag)
    * Email 2: on `milestone.target_date`      (day-of final status)

Subject: `[<Carrier>]\\[<Device>][<Milestone>] DRR closure final status`
Body: HTML rendered from templates/tpm_drr_closure.j2 (greeting +
      Open/Closed/Completion% summary table + per-TG pending-count
      table filtered to rows with #items pending > 0). No inline
      `DRR closure final status for ...` narrative line (subject
      already carries context).
Attachment: DRR_<Carrier>_<Device>_<Milestone>_final.xlsx --
      per-item detail with Item No / Item Title / Open-or-Closed /
      Owner Comment columns (drr_report_excel.build_drr_report_excel).

Beat scheduling: hilda-beat fires this task every N seconds (default
300 = 5 min) per tpm_notification_config.beat_interval_seconds. Each
tick iterates all customers (enumerated from customer.yaml files under
customizations/sharepoint_config/customers/), reads their Milestones SP
list, and per (customer, device, milestone) row decides:

  * Is now within the day-of send window for target_date?
    -> send day-of email if not already sent (audit-log idempotency).
  * Is now within the day-before send window for (target_date - 1)?
    -> send day-before email if not already sent.
  * Was the send window missed entirely (yesterday's target_date, no
    audit row of a send)?
    -> fire ops alert if config.ops_alert_on_missed_window=true.

Idempotency: guarded via CommunicationLog action_type='tpm_drr_notification_sent'
lookup keyed on (customer_id, device_id, milestone_id, phase='day_of' | 'day_before').

The whole task no-ops when config.enabled=false (kill switch for ops).
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import yaml

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.task_deps import get_task_deps
from core.src.workflow_engine.tpm_notification_config import TpmNotificationConfig

__all__ = ["tpm_notification_tick_task"]

_log = logging.getLogger(__name__)


# Phase markers -- keep in sync with audit-log action_type discriminator.
_PHASE_DAY_BEFORE = "day_before"
_PHASE_DAY_OF     = "day_of"

# CommunicationLog action_type marker for idempotency lookups.
_AUDIT_ACTION_SENT   = "tpm_drr_notification_sent"
_AUDIT_ACTION_MISSED = "tpm_drr_notification_missed_window"
_AUDIT_ACTION_NO_TARGET_DATE = "tpm_drr_notification_missing_target_date"


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.tpm_notification.tick")
def tpm_notification_tick_task(
    params: dict[str, Any] | None = None,
    event_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Beat tick body. See module docstring."""
    cfg = TpmNotificationConfig.from_sources()
    if not cfg.enabled:
        return {"outcome": "disabled", "customers_scanned": 0}

    deps = get_task_deps()
    if deps.sp_writer is None:
        _log.info("tpm_notification_tick: sp_writer unavailable; skipping tick")
        return {"outcome": "no_sp_writer", "customers_scanned": 0}

    tz = ZoneInfo(cfg.timezone)
    now_local = datetime.now(tz)

    customers = _list_customer_ids()
    customers_scanned = 0
    sends_attempted   = 0
    sends_succeeded   = 0
    ops_alerts_fired  = 0

    for customer_id in customers:
        customers_scanned += 1
        try:
            milestones = _read_milestones(deps, customer_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "tpm_notification_tick: milestones read failed for customer=%s: %s: %s",
                customer_id, type(exc).__name__, str(exc)[:120],
            )
            continue

        for row in milestones:
            milestone_id = _row_get(row, "milestone_id") or _row_get(row, "Title") or ""
            device_id = _row_get(row, "project_model") or _row_get(row, "device_id") or ""
            target_date_raw = _row_get(row, "target_date")
            if not (milestone_id and device_id):
                continue
            target_date = _parse_target_date(target_date_raw)
            if target_date is None:
                _handle_missing_target_date(
                    deps, cfg, customer_id, device_id, milestone_id,
                )
                continue

            # Determine phase(s) that apply "now" per the send-window rules.
            for phase, phase_date in (
                (_PHASE_DAY_BEFORE, target_date - timedelta(days=1)),
                (_PHASE_DAY_OF,     target_date),
            ):
                classification = _classify_window(
                    now_local=now_local,
                    phase_date=phase_date,
                    window_minutes=cfg.window_minutes,
                    strict_only=cfg.strict_only,
                )
                if classification == "not_yet":
                    continue
                if _already_sent(deps, customer_id, device_id, milestone_id, phase):
                    continue

                if classification == "in_window":
                    sends_attempted += 1
                    ok = _send_notification(
                        deps=deps,
                        customer_id=customer_id,
                        device_id=device_id,
                        milestone_id=milestone_id,
                        target_date=target_date,
                        phase=phase,
                        now_local=now_local,
                    )
                    if ok:
                        sends_succeeded += 1
                elif classification == "missed":
                    if cfg.ops_alert_on_missed_window:
                        _fire_missed_window_alert(
                            deps=deps,
                            customer_id=customer_id,
                            device_id=device_id,
                            milestone_id=milestone_id,
                            phase_date=phase_date,
                            phase=phase,
                            now_local=now_local,
                        )
                        ops_alerts_fired += 1

    _log.info(
        "tpm_notification_tick_done: customers=%d attempted=%d succeeded=%d "
        "ops_alerts=%d now=%s tz=%s",
        customers_scanned, sends_attempted, sends_succeeded,
        ops_alerts_fired, now_local.isoformat(), cfg.timezone,
    )
    return {
        "outcome":           "fired",
        "customers_scanned": customers_scanned,
        "sends_attempted":   sends_attempted,
        "sends_succeeded":   sends_succeeded,
        "ops_alerts_fired":  ops_alerts_fired,
    }


# ---------------------------------------------------------------------------
# Customer enumeration
# ---------------------------------------------------------------------------

_CUSTOMER_CONFIG_DIRS = (
    Path("customizations/sharepoint_config/customers"),
    Path("/app/customizations/sharepoint_config/customers"),
)

# DRR-V2-6 (2026-08-05): probe locations for the DRR-header brand logo
# (customizations/branding/<name>.png). Both host + container paths;
# whichever exists wins. Missing → None → excel builder silent-skips
# the image embed and continues.
_BRANDING_DIRS = (
    Path("customizations/branding"),
    Path("/app/customizations/branding"),
)


def _list_customer_ids() -> list[str]:
    """Enumerate customer_id values from customer YAML files.

    Skips example.yaml (template scaffold, not a real customer). Extracts
    customer_id from the YAML's top-level `customer_id:` field; falls back
    to the filename stem when the field is absent.
    """
    for dir_path in _CUSTOMER_CONFIG_DIRS:
        if not dir_path.is_dir():
            continue
        ids: list[str] = []
        for f in sorted(dir_path.glob("*.yaml")):
            if f.name == "example.yaml":
                continue
            try:
                with f.open("r", encoding="utf-8") as fh:
                    data = yaml.safe_load(fh) or {}
                cid = data.get("customer_id") or f.stem
                if cid:
                    ids.append(str(cid))
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "tpm_notification: failed to parse customer config %s: %s: %s",
                    f, type(exc).__name__, str(exc)[:120],
                )
        return ids
    _log.warning(
        "tpm_notification: no customer config dir found (searched %s)",
        [str(p) for p in _CUSTOMER_CONFIG_DIRS],
    )
    return []


# ---------------------------------------------------------------------------
# SP reads
# ---------------------------------------------------------------------------


def _read_milestones(deps: Any, customer_id: str) -> list[dict[str, Any]]:
    """Read the GLOBAL Milestones SP list rows filtered by carrier=customer_id.

    Per [D-083]: Milestones SP list is GLOBAL (single list `Milestones`, not
    `Milestones_<customer_id>`), with a `carrier` column that identifies which
    customer each row belongs to. HILDA reads all rows and filters by carrier
    at task level.

    The customer.yaml `milestones.name` field MUST be set to `Milestones`
    (no _<customer_id> suffix) for the list-name lookup to hit the right
    SP list. Passing ListScope(customer_id=...) here is required by the
    ListScope type but has no effect on the resolved list name when
    customer.yaml says the list is literally "Milestones" (global).
    """
    from core.src.sharepoint_integration.config import ListScope
    rows = deps.sp_writer.get_items(
        entity="milestones",
        scope=ListScope(customer_id=customer_id),
    )
    rows = list(rows or [])
    # Filter rows to this customer only (global list returns all carriers)
    filtered = [
        r for r in rows
        if (_row_get(r, "carrier") or "").strip() == customer_id.strip()
    ]
    _log.info(
        "tpm_notification: read %d milestones (filtered %d out of %d total) "
        "for customer=%s",
        len(filtered), len(rows) - len(filtered), len(rows), customer_id,
    )
    return filtered


def _read_tpm_email(deps: Any, customer_id: str, device_id: str) -> tuple[str | None, str | None]:
    """Look up TPM email + name from Projects_<customer_id> keyed on
    project_model=device_id. Returns (email, display_name); either may be
    None on lookup miss.

    Field-shape handling (added 2026-07-27 per architect observation): the
    TPM column in Projects_<customer_id> is a SP User / PersonOrGroup
    field, not a plain string. SP REST returns it as a nested dict when
    $expand is applied (typical keys: EMail, Title, LoginName, Name) OR
    as a list of such dicts for multi-user fields. The canonical HILDA
    column-map may point `tpm_email` and `tpm_name` at the SAME SP column
    (the TPM person field) and rely on this extractor to pull the right
    sub-value from the shared underlying object.
    """
    from core.src.sharepoint_integration.config import ListScope
    try:
        # expand + extra_select per TPM-3/TPM-4 (2026-07-27): SP 2017 rejects
        # $expand=TPM with HTTP 400 -1 unless paired with $select of TPM
        # sub-fields. Live corp-box probe (2026-07-27):
        #   probe($expand=TPM alone)               -> HTTP 400
        #   probe($expand=TPM + $select=TPM/EMail) -> HTTP OK, returns
        #     TPM={"__metadata":{"type":"SP.Data.UserInfoItem"},"EMail":"..."}
        #
        # SP.Data.UserInfoItem exposes ONLY: Id, Title, EMail, LoginName,
        # ContentType. NAME + "Work email" (UserProfile properties) are NOT
        # available under this expand — the screenshot label "Work email"
        # is the SP UI display for the standard EMail property. Adding
        # TPM/Name or TPM/Work_x0020_email to $select 400s the whole request.
        #
        # Include Device (filter column) explicitly so the row resolves.
        rows = deps.sp_writer.get_items(
            entity="projects",
            scope=ListScope(customer_id=customer_id),
            canonical_filters={"project_model": device_id},
            expand=["TPM"],
            extra_select=[
                "Id",
                "Device",
                "TPM/EMail",
                "TPM/Title",
            ],
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "tpm_notification: projects read failed customer=%s device=%s: %s: %s",
            customer_id, device_id, type(exc).__name__, str(exc)[:120],
        )
        return None, None
    for row in rows or []:
        tpm_email_field = _row_get(row, "tpm_email")
        email, embedded_name = _extract_user_field_email_name(tpm_email_field)
        if not email:
            continue
        # Name preference: (1) name embedded in the same tpm_email User
        # field (SP User fields ship EMail + Title together), (2) separate
        # tpm_name field if column_map defines one distinctly.
        name = embedded_name
        if not name:
            tpm_name_field = _row_get(row, "tpm_name")
            _, name_only = _extract_user_field_email_name(tpm_name_field)
            name = name_only
        return str(email), (str(name) if name else None)
    return None, None


def _extract_user_field_email_name(
    field: Any,
) -> tuple[str | None, str | None]:
    """Extract (email, display_name) from a SP field value.

    Handled shapes:
      * None or empty string   → (None, None)
      * str                    → (str, None) — assume already an email
      * dict                   → look up common User/PersonOrGroup keys:
                                   email:  EMail | Email | email | mail
                                   name:   Title | DisplayName | Name
      * list of dicts          → recurse on first non-empty entry
                                 (SP multi-user field takes the first)
      * anything else          → (None, None)

    All key lookups are case-preserving; SP's canonical shape uses
    PascalCase (EMail / Title / etc.) — the lowercase fallbacks cover
    hand-authored dicts / test fixtures / non-SP sources.
    """
    if field is None or field == "":
        return None, None
    if isinstance(field, str):
        # Strip whitespace; treat leftover as email if non-empty
        s = field.strip()
        return (s if s else None), None
    if isinstance(field, list):
        for item in field:
            email, name = _extract_user_field_email_name(item)
            if email:
                return email, name
        return None, None
    if isinstance(field, dict):
        # Email keys in preference order. Corp SP profile expansion returns
        # 'Work email' (with space, from the SP UI schema) or 'WorkEmail'
        # (JSON camelCase from UserProfile properties); standard SP
        # PersonOrGroup $expand returns 'EMail'. Try all common shapes.
        email = (
            field.get("Work email")
            or field.get("WorkEmail")
            or field.get("EMail")
            or field.get("Email")
            or field.get("email")
            or field.get("mail")
        )
        # Name keys in preference order:
        #   Title / DisplayName — normally clean but corp SP-2017 populates
        #     these with the Distinguished-Name shape too ("Thendral Arasu
        #     Panneer Selvam/Device Management/MNOs Lab/Senior Professional/
        #     Samsung Electronics"), so apply the same `/`-split as `Name`.
        #   Name — DN-shape with `/` delimiters.
        #   First name + Last name — compose when nothing else is set.
        # SETUP-6 (2026-07-29): all sources go through _split_dn so
        # downstream greetings ("Dear <name>,") always get just the
        # person's name -- not the full org path.
        name = (
            field.get("Title")
            or field.get("DisplayName")
            or field.get("Name")
        )
        if isinstance(name, str) and name.strip():
            name = name.split("/", 1)[0].strip()
        else:
            name = None
        if not name:
            first = (field.get("First name") or field.get("FirstName") or "").strip()
            last = (field.get("Last name") or field.get("LastName") or "").strip()
            composed = (first + " " + last).strip()
            if composed:
                name = composed
        return (
            str(email).strip() if email else None,
            str(name).strip() if name else None,
        )
    return None, None


# ---------------------------------------------------------------------------
# DRR-V2-4 helpers — MOVED to core.src.email_service.outbound.drr_v2_context
# (DRR-DL-1a 2026-08-06) so the dashboard container (celery-free) can
# call the same code path. Re-exported here for backward compat.
# ---------------------------------------------------------------------------

from core.src.email_service.outbound.drr_v2_context import (
    MILESTONE_HEADER_FIELDS as _MILESTONE_HEADER_FIELDS,
    PROJECT_HEADER_FIELDS as _PROJECT_HEADER_FIELDS,
    read_milestone_headers as _read_milestone_headers,
    read_project_headers as _read_project_headers,
)


# ---------------------------------------------------------------------------
# DRR-V2-6 helpers — MOVED to core.src.email_service.outbound.drr_v2_context
# (DRR-DL-1a 2026-08-06). Re-exported for BC.
# ---------------------------------------------------------------------------

from core.src.email_service.outbound.drr_v2_context import (
    resolve_logo_path as _resolve_logo_path,
    build_drr_v2_context as _build_drr_v2_context,
)


# ---------------------------------------------------------------------------
# Send-window logic
# ---------------------------------------------------------------------------


def _classify_window(
    *,
    now_local: datetime,
    phase_date: date,
    window_minutes: int,
    strict_only: bool,
) -> str:
    """Return 'in_window' / 'missed' / 'not_yet'.

    Window is [phase_date 00:00, phase_date 00:00 + window_minutes] in the
    task's configured timezone. Anything after the window on phase_date
    OR on a later date is "missed" (strict_only=True) or "in_window"
    (strict_only=False, fires whenever detected on phase_date).
    """
    tz = now_local.tzinfo
    window_start = datetime.combine(phase_date, dt_time.min, tzinfo=tz)
    window_end = window_start + timedelta(minutes=window_minutes)
    if now_local < window_start:
        return "not_yet"
    if now_local <= window_end:
        return "in_window"
    # Past the strict window. Under strict_only=False, still count today as
    # in-window; otherwise mark as missed.
    if not strict_only and now_local.date() == phase_date:
        return "in_window"
    return "missed"


# ---------------------------------------------------------------------------
# Audit-log idempotency
# ---------------------------------------------------------------------------


def _already_sent(
    deps: Any, customer_id: str, device_id: str, milestone_id: str, phase: str,
) -> bool:
    """Look up the audit log to see if a notification with this phase marker
    was already logged for this (customer, device, milestone) tuple."""
    query_fn = getattr(deps.audit, "query_communications", None) or getattr(
        deps.storage, "query_communications", None
    )
    if query_fn is None:
        # Fallback: no audit query surface; be conservative and re-send.
        # (Better to double-send than to skip forever if the query API isn't
        # wired.)
        _log.info(
            "tpm_notification: audit query surface unavailable; cannot enforce "
            "idempotency for customer=%s device=%s milestone=%s phase=%s",
            customer_id, device_id, milestone_id, phase,
        )
        return False
    try:
        rows = query_fn(
            action_type=_AUDIT_ACTION_SENT,
            details_contains={
                "customer_id":  customer_id,
                "device_id":    device_id,
                "milestone_id": milestone_id,
                "phase":        phase,
            },
        )
        return bool(rows)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "tpm_notification: idempotency query failed: %s: %s",
            type(exc).__name__, str(exc)[:120],
        )
        # Defensive: same as no query surface -- allow re-send rather than block.
        return False


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


def _send_notification(
    *,
    deps: Any,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    target_date: date,
    phase: str,
    now_local: datetime,
) -> bool:
    """Build the summary + Excel + body, send via email_sender, audit on success."""
    if deps.email_sender is None:
        _log.info(
            "tpm_notification: email_sender not wired; skipping send for "
            "customer=%s device=%s milestone=%s phase=%s",
            customer_id, device_id, milestone_id, phase,
        )
        return False

    tpm_email, tpm_name = _read_tpm_email(deps, customer_id, device_id)
    if not tpm_email:
        _log.warning(
            "tpm_notification: TPM email missing in Projects list for "
            "customer=%s device=%s -- skipping send",
            customer_id, device_id,
        )
        return False

    # Load per-scope items from Postgres for the summary + Excel + per-TG table.
    items = _fetch_scope_items(deps, customer_id, device_id, milestone_id)
    if not items:
        _log.info(
            "tpm_notification: no items in Postgres scope customer=%s device=%s "
            "milestone=%s; skipping (nothing to report)",
            customer_id, device_id, milestone_id,
        )
        return False

    # DRR-V2-8j (2026-08-08): overlay live SP `comment` values from
    # deliverables_<customer> onto the loaded items so the Checklist
    # Remarks column reflects what TPM most recently typed in SP.
    # Mirrors the dashboard's Download route (D-162 live-SP-read
    # pattern); without this, beat-tick emails render Remarks blank
    # for rows where the SP alert -> sync-task -> Postgres pipeline
    # hasn't propagated the latest comment yet. Best-effort: SP read
    # failure returns empty dict + WARN log; Postgres value stays.
    try:
        from core.src.email_service.outbound.drr_v2_context import (
            read_deliverables_comments,
        )
        sp_comments = read_deliverables_comments(
            deps, customer_id, device_id, milestone_id,
        )
        overlaid = 0
        for _it in items:
            _no = getattr(_it, "item_no", None)
            if isinstance(_no, int) and _no in sp_comments:
                try:
                    _it.comment = sp_comments[_no]
                    overlaid += 1
                except Exception:  # noqa: BLE001 -- frozen models fall through
                    pass
        _log.warning(
            "DRR_TICK_COMMENT: SP comment overlay: %d/%d items updated "
            "from live SP customer=%s device=%s milestone=%s",
            overlaid, len(items), customer_id, device_id, milestone_id,
        )
    except Exception as _c_exc:  # noqa: BLE001
        _log.warning(
            "DRR_TICK_COMMENT: overlay failed customer=%s device=%s "
            "milestone=%s: %s: %s -- Remarks column will use Postgres values",
            customer_id, device_id, milestone_id,
            type(_c_exc).__name__, str(_c_exc)[:120],
        )

    summary, pending_by_tg = _compute_summary_and_pending(items)

    # Compose body.
    body_html = _render_body(
        tpm_name=tpm_name,
        summary=summary,
        pending_by_tg=pending_by_tg,
    )

    # Build Excel attachment bytes -- DRR-V2 shape (2026-08-05 cascade).
    # Context builder composes drr_version + section_grouping +
    # milestone/project header dicts + logo path from template_lookup +
    # SP reads. When section_grouping is None (customer not migrated
    # yet), builder falls back to legacy 4-column flat sheet.
    from core.src.email_service.outbound.drr_report_excel import build_drr_report_excel
    from core.src.email_service.outbound.drr_v2_context import (
        read_apps_tg_xlsx_bytes_sync,
    )
    drr_ctx = _build_drr_v2_context(deps, customer_id, device_id, milestone_id)
    # DRR-V2-8g (2026-08-07): beat-tick path must also fetch the APPS TG
    # xlsx so Applications tab renders with real data, not the empty
    # placeholder. Same source as dashboard's Download route.
    # DRR-V2-8h (2026-08-08): loud entry/exit log so ops can confirm this
    # path executed via `podman logs hilda-worker | grep DRR_TICK_8G`.
    _log.warning(
        "DRR_TICK_8G: about to fetch APPS xlsx for beat-tick DRR email "
        "customer=%s device=%s milestone=%s",
        customer_id, device_id, milestone_id,
    )
    try:
        apps_bytes = read_apps_tg_xlsx_bytes_sync(customer_id, device_id, milestone_id)
    except Exception as _apps_exc:  # noqa: BLE001
        _log.warning(
            "DRR_TICK_8G: APPS fetch raised (should never happen; helper "
            "is supposed to swallow): %s: %s -- falling back to None",
            type(_apps_exc).__name__, str(_apps_exc)[:200],
        )
        apps_bytes = None
    _log.warning(
        "DRR_TICK_8G: APPS fetch returned bytes=%s customer=%s device=%s milestone=%s",
        (f"{len(apps_bytes)}B" if apps_bytes else "None"),
        customer_id, device_id, milestone_id,
    )
    xlsx_bytes = build_drr_report_excel(
        items=items, applications_sheet_bytes=apps_bytes, **drr_ctx,
    )
    xlsx_filename = f"DRR_{customer_id}_{device_id}_{milestone_id}_final.xlsx"

    subject = _build_subject(customer_id, device_id, milestone_id)

    # Send via sync-bridged call (Celery worker is sync).
    try:
        message_id = _send_via_email_sender(
            deps=deps,
            to=tpm_email,
            subject=subject,
            body_html=body_html,
            attachments=[(
                xlsx_filename,
                xlsx_bytes,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )],
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "tpm_notification_send_failed: customer=%s device=%s milestone=%s "
            "phase=%s error=%s: %s",
            customer_id, device_id, milestone_id, phase,
            type(exc).__name__, str(exc)[:120],
        )
        return False

    # Audit success -- idempotency check reads this on subsequent ticks.
    _audit(deps, _AUDIT_ACTION_SENT, details={
        "customer_id":       customer_id,
        "device_id":         device_id,
        "milestone_id":      milestone_id,
        "phase":             phase,
        "target_date":       target_date.isoformat(),
        "sent_at_local":     now_local.isoformat(),
        "tpm_email":         tpm_email,
        "message_id":        message_id,
        "open_count":        summary["open_count"],
        "closed_count":      summary["closed_count"],
        "completion_percent": summary["completion_percent"],
    })
    _log.info(
        "tpm_notification_sent: customer=%s device=%s milestone=%s phase=%s "
        "message_id=%s open=%d closed=%d completion=%d%%",
        customer_id, device_id, milestone_id, phase, message_id,
        summary["open_count"], summary["closed_count"], summary["completion_percent"],
    )

    # -- Final DRR deliverable state transition per architect 2026-07-18 -----
    # On day-of send for configured milestones (DRR by default), transition
    # the item named cfg.final_deliverable_item_name to SubmittedToCustomer;
    # if that succeeds, close the Default WI (Ph-2 gate respected).
    # Semantics: HILDA-generated Excel sent to TPM == carrier deliverable
    # submitted from HILDA's perspective (TPM forwards to carrier).
    # Best-effort: any failure logs a warning but does not fail the send.
    #
    # CFG-1 fix (2026-07-28): reload TpmNotificationConfig here — cfg was
    # previously referenced as a free var from the enclosing tick body,
    # but _send_notification runs in its own scope and cfg was never
    # threaded in. Live 2026-07-28 corp box: the send succeeded end-to-end
    # (Excel landed in TPM's mailbox) but this side-effect crashed with
    # NameError, preventing item_no=<final_deliverable_item_name> from
    # advancing to SubmittedToCustomer. Reload is cheap (JSON file read
    # + Pydantic parse) and lets ops flip flags without a code deploy.
    try:
        cfg = TpmNotificationConfig.from_sources()
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "tpm_notification: cfg reload failed in send side-effect "
            "customer=%s device=%s milestone=%s: %s: %s -- skipping final "
            "deliverable transition",
            customer_id, device_id, milestone_id,
            type(exc).__name__, str(exc)[:120],
        )
        return True

    if phase == _PHASE_DAY_OF and milestone_id in cfg.final_deliverable_milestone_names:
        _transition_final_deliverable_and_close_default_wi(
            deps=deps,
            cfg=cfg,
            items=items,
            customer_id=customer_id,
            device_id=device_id,
            milestone_id=milestone_id,
            correlation_id=f"tpm_notification:{customer_id}:{device_id}:{milestone_id}:day_of",
        )

    return True


def _send_via_email_sender(
    *,
    deps: Any,
    to: str,
    subject: str,
    body_html: str,
    attachments: list[tuple[str, bytes, str]],
) -> str:
    """Sync bridge to the async EmailSender.send(...)."""
    import asyncio
    coro = deps.email_sender.send(
        to=[to],
        cc=[],
        subject=subject,
        body=body_html,
        attachments=attachments,
    )
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        return loop.run_until_complete(coro)
    except RuntimeError:
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()


# ---------------------------------------------------------------------------
# Final DRR deliverable transition + Default WI close (day-of side effect)
# ---------------------------------------------------------------------------


def _transition_final_deliverable_and_close_default_wi(
    *,
    deps: Any,
    cfg: TpmNotificationConfig,
    items: list[Any],
    customer_id: str,
    device_id: str,
    milestone_id: str,
    correlation_id: str,
) -> None:
    """After a successful day-of DRR send: transition the "Final DRR status
    excel deliverable for carrier" item to SubmittedToCustomer, then close
    the Default WI in scope. Per architect 2026-07-18.

    Behavior:
      1. Find item(s) with item_name == cfg.final_deliverable_item_name in
         the (customer, device, milestone) scope. Transition to
         SUBMITTED_TO_CUSTOMER via trigger_source='tpm_drr_final_deliverable'
         (Guard 4 trust list per 2026-07-18). If already at
         SubmittedToCustomer/Closed, treat as success (idempotent).
      2. If step 1 produced at least one successful (or already-terminal)
         transition, close all Default WIs in scope (Open -> Closed).
         Ph-2 gate: Default WI with classified attachments -> skip + log
         'default_wi_close_deferred_ph2_pending' (matching pm_approval.py's
         sweep semantics).

    Unlike apply_pm_approval_task's sweep (which gates Default WI close on
    all non-Default items being terminal), this close is UNCONDITIONAL --
    "rest" items still at OutreachSent/DocumentReceived are expected at
    DRR target_date per architect 2026-07-18. Close All Items (FR-64) is
    the TPM's later trigger to close the remaining pipeline items.

    Best-effort throughout: exceptions are logged, not raised. The outer
    email send has already succeeded and been audited.
    """
    from core.src.tracker import DeliveryState as _DS
    from core.src.tracker.transitions import update_delivery_state as _uds
    from core.src.template_schema.enums import ItemType as _IT

    target_name = cfg.final_deliverable_item_name

    matched = [
        it for it in items
        if (getattr(it, "item_name", None) or "") == target_name
    ]
    if not matched:
        _log.warning(
            "tpm_drr_final_deliverable: item name %r not found in scope "
            "customer=%s device=%s milestone=%s -- template may be missing "
            "this row; skipping transition + Default WI close",
            target_name, customer_id, device_id, milestone_id,
        )
        return
    if len(matched) > 1:
        _log.warning(
            "tpm_drr_final_deliverable: %d items match name %r in scope "
            "customer=%s device=%s milestone=%s -- transitioning all",
            len(matched), target_name, customer_id, device_id, milestone_id,
        )

    transition_ok = False
    for final_item in matched:
        fi_id = (
            getattr(final_item, "item_id", None)
            or getattr(final_item, "delivery_item_id", None)
        )
        if not fi_id:
            continue
        current_state = getattr(final_item, "delivery_state", None) or ""
        if current_state in (_DS.SUBMITTED_TO_CUSTOMER.value, _DS.CLOSED.value):
            _log.info(
                "tpm_drr_final_deliverable: item=%s already at %s; "
                "treating as success (idempotent)",
                fi_id, current_state,
            )
            transition_ok = True
            continue
        try:
            _result = _uds(
                delivery_item_id=fi_id,
                target_state=_DS.SUBMITTED_TO_CUSTOMER,
                params={},
                event_context={
                    "correlation_id":   correlation_id,
                    "delivery_item_id": fi_id,
                    "trigger_source":   "tpm_drr_final_deliverable",
                    "rule_id":          "tpm_drr_final_deliverable_on_day_of_send",
                },
                storage=deps.storage,
                sp_writer=deps.sp_writer,
                audit=deps.audit,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "tpm_drr_final_deliverable_transition_failed: item=%s "
                "(customer=%s device=%s milestone=%s) from=%s: %s: %s",
                fi_id, customer_id, device_id, milestone_id, current_state,
                type(exc).__name__, str(exc)[:120],
            )
            continue

        # STATE-1 fix (2026-07-28): _uds() does NOT raise on guard denial --
        # it returns a TransitionResult with outcome='guard_denied' /
        # 'illegal_transition' / 'no_op_idempotent' / 'transitioned' and
        # writes the appropriate audit row. Prior caller treated no-exception
        # as success; that logged "Open -> SubmittedToCustomer" success even
        # when Guard 1 rejected because OPEN->SUBMITTED_TO_CUSTOMER wasn't
        # in LEGAL_TRANSITIONS. Item_no=85 stayed at Open in Postgres/SP
        # while the log claimed a successful transition. Now inspect the
        # outcome and only treat 'transitioned' or 'no_op_idempotent' as
        # success; any other outcome logs the real reason.
        _outcome = getattr(_result, "outcome", "unknown") if _result is not None else "unknown"
        if _outcome in ("transitioned", "no_op_idempotent"):
            transition_ok = True
            _log.info(
                "tpm_drr_final_deliverable_transitioned: item=%s "
                "(customer=%s device=%s milestone=%s) %s -> SubmittedToCustomer "
                "outcome=%s",
                fi_id, customer_id, device_id, milestone_id, current_state, _outcome,
            )
        else:
            _reason = ""
            _gr = getattr(_result, "guard_result", None)
            if _gr is not None:
                _reason = getattr(_gr, "reason", "") or ""
            _log.warning(
                "tpm_drr_final_deliverable_not_transitioned: item=%s "
                "(customer=%s device=%s milestone=%s) from=%s outcome=%s reason=%r",
                fi_id, customer_id, device_id, milestone_id, current_state,
                _outcome, _reason,
            )

    if not transition_ok:
        _log.info(
            "tpm_drr_final_deliverable: no successful transitions; "
            "skipping Default WI close (customer=%s device=%s milestone=%s)",
            customer_id, device_id, milestone_id,
        )
        return

    # Default WI close -- unconditional per architect 2026-07-18 (Ph-2 gate
    # respected). "Rest" items still at OutreachSent are expected at DRR
    # target_date; Close All Items (FR-64) closes them later.
    default_wis = [
        it for it in items
        if (getattr(it, "item_type", None) or "") == _IT.DEFAULT.value
    ]
    for dwi in default_wis:
        dwi_id = (
            getattr(dwi, "item_id", None)
            or getattr(dwi, "delivery_item_id", None)
        )
        if not dwi_id:
            continue
        if (getattr(dwi, "delivery_state", None) or "") == _DS.CLOSED.value:
            continue
        # Ph-2 gate -- classified attachments pending routing.
        try:
            classified = deps.storage.list_classified_associations_for_item(dwi_id) or []
        except Exception:  # noqa: BLE001
            classified = []
        if classified:
            _log.info(
                "default_wi_close_deferred_ph2_pending: default_wi=%s "
                "classified_count=%d (customer=%s device=%s milestone=%s) "
                "-- deferred on DRR day-of send",
                dwi_id, len(classified),
                customer_id, device_id, milestone_id,
            )
            continue
        try:
            _dwi_result = _uds(
                delivery_item_id=dwi_id,
                target_state=_DS.CLOSED,
                params={},
                event_context={
                    "correlation_id":   correlation_id,
                    "delivery_item_id": dwi_id,
                    "trigger_source":   "automated",
                    "rule_id":          "default_wi_auto_close_on_drr_final_deliverable_send",
                },
                storage=deps.storage,
                sp_writer=deps.sp_writer,
                audit=deps.audit,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "default_wi_auto_close_on_drr_send_failed: default_wi=%s "
                "(customer=%s device=%s milestone=%s): %s: %s",
                dwi_id, customer_id, device_id, milestone_id,
                type(exc).__name__, str(exc)[:120],
            )
            continue

        # STATE-1 fix (2026-07-28): inspect TransitionResult.outcome; _uds
        # doesn't raise on guard denial. Prior caller logged success even
        # when the transition was actually rejected.
        _dwi_outcome = (
            getattr(_dwi_result, "outcome", "unknown")
            if _dwi_result is not None else "unknown"
        )
        if _dwi_outcome in ("transitioned", "no_op_idempotent"):
            _log.info(
                "default_wi_auto_closed_on_drr_send: default_wi=%s "
                "(customer=%s device=%s milestone=%s) outcome=%s",
                dwi_id, customer_id, device_id, milestone_id, _dwi_outcome,
            )
        else:
            _dwi_reason = ""
            _dwi_gr = getattr(_dwi_result, "guard_result", None)
            if _dwi_gr is not None:
                _dwi_reason = getattr(_dwi_gr, "reason", "") or ""
            _log.warning(
                "default_wi_auto_close_on_drr_send_not_transitioned: "
                "default_wi=%s (customer=%s device=%s milestone=%s) "
                "outcome=%s reason=%r",
                dwi_id, customer_id, device_id, milestone_id,
                _dwi_outcome, _dwi_reason,
            )


# ---------------------------------------------------------------------------
# Summary + per-TG pending computation
# ---------------------------------------------------------------------------


def _compute_summary_and_pending(items: Iterable[Any]) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Return (summary_dict, pending_by_tg_list).

    summary_dict: {open_count, closed_count, completion_percent}
    pending_by_tg_list: [{tg_name, pending_count}, ...] rows with pending_count > 0
    """
    from core.src.email_service.outbound.drr_report_excel import CLOSED_LIKE_STATES

    open_count = 0
    closed_count = 0
    pending_per_tg: dict[str, int] = {}

    for it in items:
        state = getattr(it, "delivery_state", None) or ""
        tg = (getattr(it, "tg_name", None) or "(unassigned)").strip()
        if state in CLOSED_LIKE_STATES:
            closed_count += 1
        else:
            open_count += 1
            pending_per_tg[tg] = pending_per_tg.get(tg, 0) + 1

    total = open_count + closed_count
    completion_percent = int(round(closed_count / total * 100)) if total > 0 else 0
    summary = {
        "open_count":         open_count,
        "closed_count":       closed_count,
        "completion_percent": completion_percent,
    }
    pending_by_tg = [
        {"tg_name": tg, "pending_count": count}
        for tg, count in sorted(pending_per_tg.items(), key=lambda kv: (-kv[1], kv[0]))
        if count > 0
    ]
    return summary, pending_by_tg


# ---------------------------------------------------------------------------
# Scope reads
# ---------------------------------------------------------------------------


def _fetch_scope_items(
    deps: Any, customer_id: str, device_id: str, milestone_id: str,
) -> list[Any]:
    """Return items in the (customer, device, milestone) triple."""
    fn = getattr(deps.storage, "list_items_for_milestone", None)
    if fn is None:
        return []
    try:
        all_items = fn(milestone_id) or []
    except Exception as exc:  # noqa: BLE001
        # DBG-TICK-1 (2026-07-27): the prior str(exc)[:120] truncation hid
        # the actual asyncpg root cause. Unwrap the PipelineError to reach
        # the underlying DBAPI exception + log the full traceback + walk the
        # __cause__/__context__ chain. The same code path runs green from
        # a fresh Python invocation but fails inside Celery ForkPoolWorker
        # tick context -- symptom points at event-loop / connection-pool
        # lifecycle bug (asyncpg connection tied to a closed loop).
        import traceback as _tb
        _log.warning(
            "tpm_notification: list_items_for_milestone failed customer=%s "
            "milestone=%s: %s: %s",
            customer_id, milestone_id, type(exc).__name__, str(exc)[:120],
        )
        # Log FULL exception details on separate lines so nothing gets
        # truncated by logfmt / log-shipper trimming.
        _log.warning(
            "DBG_TICK exception_class=%s.%s full_str=%r",
            type(exc).__module__, type(exc).__name__, str(exc),
        )
        current = exc.__cause__ or exc.__context__
        depth = 1
        while current is not None and depth < 8:
            _log.warning(
                "DBG_TICK   cause[%d]=%s.%s: %r",
                depth, type(current).__module__, type(current).__name__,
                str(current),
            )
            current = current.__cause__ or current.__context__
            depth += 1
        _log.warning("DBG_TICK traceback:\n%s", _tb.format_exc())
        # Also log event-loop + engine state to help diagnose async-context
        # lifecycle issues (loop closed / connection stale).
        try:
            import asyncio as _asy
            _log.warning(
                "DBG_TICK asyncio_state: current_loop=%r running=%s",
                _asy.get_event_loop_policy().get_event_loop(),
                False,  # sync context; if we could reach here loop isn't running
            )
        except Exception:  # noqa: BLE001
            _log.warning("DBG_TICK asyncio_state: <could not introspect>")
        try:
            from core.src.storage.db import get_engine as _get_engine
            _eng = _get_engine()
            _pool = _eng.pool if _eng is not None else None
            _log.warning(
                "DBG_TICK engine=%r pool=%r pool_status=%s",
                _eng, _pool,
                _pool.status() if _pool is not None else "<no pool>",
            )
        except Exception as _ex:  # noqa: BLE001
            _log.warning("DBG_TICK engine_state introspection failed: %s: %s",
                         type(_ex).__name__, str(_ex)[:120])
        return []
    return [
        it for it in all_items
        if getattr(it, "customer_id", None) == customer_id
        and getattr(it, "device_id", None) == device_id
    ]


# ---------------------------------------------------------------------------
# Body render (Jinja2)
# ---------------------------------------------------------------------------


def _render_body(
    *, tpm_name: str | None, summary: dict[str, int],
    pending_by_tg: list[dict[str, Any]],
) -> str:
    from jinja2 import Environment, FileSystemLoader, select_autoescape
    # Templates live at core/src/email_service/templates -- same directory as
    # outreach_table.j2. Two candidate paths accommodate dev + container.
    tmpl_dirs = [
        Path("core/src/email_service/templates"),
        Path("/app/core/src/email_service/templates"),
    ]
    existing = [str(d) for d in tmpl_dirs if d.is_dir()]
    if not existing:
        # Ultimate fallback -- minimal inline body.
        return (
            f"<html><body><p>Dear {tpm_name or 'TPM'},</p>"
            f"<p>Open: {summary['open_count']}, Closed: {summary['closed_count']}, "
            f"Completion: {summary['completion_percent']}%</p>"
            f"<p>Regards, HILDA</p></body></html>"
        )
    env = Environment(
        loader=FileSystemLoader(existing),
        autoescape=select_autoescape(["html", "j2"]),
    )
    tmpl = env.get_template("tpm_drr_closure.j2")
    return tmpl.render(
        tpm_name=tpm_name,
        summary=summary,
        pending_by_tg=pending_by_tg,
    )


def _build_subject(customer_id: str, device_id: str, milestone_id: str) -> str:
    # Literal from architect 2026-07-15:
    #   [Carrier]\[Device][Milestone] DRR closure final status
    return f"[{customer_id}]\\[{device_id}][{milestone_id}] DRR closure final status"


# ---------------------------------------------------------------------------
# Ops alert paths
# ---------------------------------------------------------------------------


def _fire_missed_window_alert(
    *, deps: Any, customer_id: str, device_id: str, milestone_id: str,
    phase_date: date, phase: str, now_local: datetime,
) -> None:
    """Write an audit row that hilda ops can grep for + attempt to enqueue
    NotifyHildaOps if the action is bindable. Best-effort."""
    _audit(deps, _AUDIT_ACTION_MISSED, details={
        "customer_id":   customer_id,
        "device_id":     device_id,
        "milestone_id":  milestone_id,
        "phase":         phase,
        "phase_date":    phase_date.isoformat(),
        "detected_at_local": now_local.isoformat(),
    })
    _log.warning(
        "tpm_notification_missed_window: customer=%s device=%s milestone=%s "
        "phase=%s phase_date=%s detected=%s -- ops alert emitted",
        customer_id, device_id, milestone_id, phase, phase_date.isoformat(),
        now_local.isoformat(),
    )


def _handle_missing_target_date(
    deps: Any, cfg: TpmNotificationConfig,
    customer_id: str, device_id: str, milestone_id: str,
) -> None:
    if not cfg.ops_alert_on_missing_target_date:
        return
    _audit(deps, _AUDIT_ACTION_NO_TARGET_DATE, details={
        "customer_id":  customer_id,
        "device_id":    device_id,
        "milestone_id": milestone_id,
    })
    # Log emission silenced 2026-07-30: TPMs are actively setting up milestones
    # and target_date is routinely absent during that window. Audit row still
    # written so history is preserved for later analysis.


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------


def _audit(deps: Any, action_type: str, *, details: dict[str, Any]) -> None:
    if deps.audit is None:
        return
    try:
        deps.audit.write_communication_log(
            action_type=action_type,
            delivery_item_id=None,
            attribution={
                "trigger_source": "beat:tpm_notification",
                "correlation_id": "",
                "modified_by":    "system",
            },
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "tpm_notification: audit write failed action=%s: %s",
            action_type, type(exc).__name__,
        )


def _row_get(row: Any, key: str) -> Any:
    """Tolerate both dict-shaped SP rows and attribute-shaped ones."""
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _parse_target_date(raw: Any) -> date | None:
    """Parse SP target_date value into a Python date. Tolerates date,
    datetime, ISO string, and 'M/D/YYYY' format (SP default)."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, str):
        s = raw.strip()
        # Try ISO first (2026-07-15 / 2026-07-15T00:00:00 / with tz)
        try:
            return date.fromisoformat(s[:10])
        except ValueError:
            pass
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m-%d-%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    return None
