"""setup_complete_notification tick — TPM email when a milestone's Setup
Deliverables batch has fully imported per architect ask 2026-07-28 (SETUP-1).

Trigger: SP UI engineer clicks "Setup Deliverables" -> SP UI writes rows for
each work item -> SP fires ADDED alert per row -> HILDA's
import_deliverable_tracker_task creates each delivery_item + auto-transitions
Not Started -> Open (D-144). Batch takes time to settle (many rows, EWS poll
cadence, worker parallelism). TPM has no signal when the whole batch is
complete + ready for Start Collection.

Detection: poll every N seconds. For each (customer, device, milestone) tuple
present in delivery_item, check if EVERY item's delivery_state is past
'Not Started'. If yes AND no 'setup_complete_notified' audit row yet, send
email + write audit. Poll-based (vs hook after import) handles concurrent
Celery workers cleanly -- races settle by "wait until quiescent" naturally.

Idempotency: option A per architect 2026-07-28 -- exactly once per scope.
Later waves of item additions do NOT re-notify. TPM knows to check the
dashboard for late additions. Option B ("delta emails" on count increase)
deferred to Ph-2 if the wave-based setup workflow becomes real.

No side-effect state transitions. This is a purely informational email.
Reuses _read_tpm_email (TPM-1..4 SP User field extraction).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.task_deps import get_task_deps
from core.src.workflow_engine.tpm_notification_config import TpmNotificationConfig

__all__ = ["setup_complete_notification_tick_task"]

_log = logging.getLogger(__name__)

_AUDIT_ACTION_SETUP_COMPLETE = "setup_complete_notified"
_NOT_STARTED_STATE = "Not Started"


@hilda_celery_app.task(
    name="core.src.workflow_engine.tasks.setup_complete_notification.tick",
)
def setup_complete_notification_tick_task(
    params: dict[str, Any] | None = None,
    event_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Beat tick body. See module docstring."""
    cfg = TpmNotificationConfig.from_sources()
    if not cfg.setup_complete_enabled:
        return {"outcome": "disabled", "scopes_scanned": 0}

    deps = get_task_deps()
    if deps.sp_writer is None:
        _log.info(
            "setup_complete_notification: sp_writer unavailable; skipping tick "
            "(TPM email lookup would fail)",
        )
        return {"outcome": "no_sp_writer", "scopes_scanned": 0}
    if deps.storage is None:
        _log.info("setup_complete_notification: storage unavailable; skipping tick")
        return {"outcome": "no_storage", "scopes_scanned": 0}

    # Enumerate all (customer, device, milestone) scopes with delivery_items.
    scopes = _list_scopes(deps)
    scopes_scanned = 0
    sends_attempted = 0
    sends_succeeded = 0

    for customer_id, device_id, milestone_id in scopes:
        scopes_scanned += 1
        try:
            all_items = deps.storage.list_items_for_milestone(milestone_id) or []
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "setup_complete_notification: list_items_for_milestone failed "
                "customer=%s device=%s milestone=%s: %s: %s",
                customer_id, device_id, milestone_id,
                type(exc).__name__, str(exc)[:120],
            )
            continue

        # Filter to the (customer, device) scope; list_items_for_milestone
        # returns all items across devices/customers for the milestone_id.
        scope_items = [
            it for it in all_items
            if (getattr(it, "customer_id", None) or "") == customer_id
            and (getattr(it, "device_id", None) or "") == device_id
        ]
        if not scope_items:
            continue

        # Completion check: every item must be past 'Not Started'.
        any_not_started = any(
            (getattr(it, "delivery_state", None) or "").strip() == _NOT_STARTED_STATE
            for it in scope_items
        )
        if any_not_started:
            continue

        # SETUP-3 (2026-07-29): expected-count gate. Prior behavior fired
        # as soon as every currently-imported item was past 'Not Started'
        # -- but the import cascade is progressive (per-item ADDED alerts).
        # A tick landing mid-import saw only the fast-first Confirmation
        # items (say 4), fired "4 items ready", wrote audit, then subsequent
        # ticks for the same scope hit idempotency skip -- TPM never got
        # the "all 87 ready" email they were waiting for.
        #
        # Fix: compare Postgres scope count to an authoritative "expected"
        # count. TWO sources, in priority order:
        #   1. template.yaml (in-memory, no network, no transient window)
        #      -- authoritative per architect 2026-07-29. Setup Deliverables
        #      writes to SP from this same template, so template count ==
        #      SP row count == Postgres row count once import catches up.
        #   2. SP Deliverables_<customer> row count -- fallback when the
        #      template isn't cached (edge case: first tick after worker
        #      startup before template_lookup loaded, or non-templated
        #      customer). Adds ~200-500ms SP round-trip and has a ~10s
        #      transient during initial SP populate; unavoidable for the
        #      fallback path.
        #
        # Either path returning None -> skip this tick (defensive; next
        # tick retries). Postgres count != expected -> skip + log
        # partial-import diagnostic.
        expected = _get_expected_count(
            deps, customer_id, device_id, milestone_id,
        )
        if expected is None:
            _log.info(
                "setup_complete_notification: expected-count unavailable "
                "customer=%s device=%s milestone=%s -- skipping this tick",
                customer_id, device_id, milestone_id,
            )
            continue
        if len(scope_items) != expected:
            _log.info(
                "setup_complete_notification: partial import, waiting "
                "customer=%s device=%s milestone=%s "
                "(postgres=%d, expected=%d)",
                customer_id, device_id, milestone_id,
                len(scope_items), expected,
            )
            continue

        # Idempotency check: has an audit row already been written for this scope?
        if _already_notified(deps, customer_id, device_id, milestone_id):
            continue

        sends_attempted += 1
        ok = _send_setup_complete(
            deps=deps,
            customer_id=customer_id,
            device_id=device_id,
            milestone_id=milestone_id,
            items=scope_items,
        )
        if ok:
            sends_succeeded += 1

    _log.info(
        "setup_complete_notification_tick_done: scopes_scanned=%d "
        "sends_attempted=%d sends_succeeded=%d",
        scopes_scanned, sends_attempted, sends_succeeded,
    )
    return {
        "outcome":         "fired",
        "scopes_scanned":  scopes_scanned,
        "sends_attempted": sends_attempted,
        "sends_succeeded": sends_succeeded,
    }


# ---------------------------------------------------------------------------
# Scope enumeration
# ---------------------------------------------------------------------------


def _list_scopes(deps: Any) -> list[tuple[str, str, str]]:
    """Enumerate distinct (customer_id, device_id, milestone_id) tuples present
    in delivery_item. Storage helper preferred; fall back to querying via a
    milestone-id-first scan if the helper isn't wired.

    Returns list of triples, deduplicated. Empty list on any read failure
    (safer than crashing the tick).
    """
    fn = getattr(deps.storage, "list_scopes", None) or getattr(
        deps.storage, "list_all_scopes", None
    )
    if fn is not None:
        try:
            rows = fn() or []
            return [
                (r["customer_id"], r["device_id"], r["milestone_id"])
                if isinstance(r, dict) else (r[0], r[1], r[2])
                for r in rows
            ]
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "setup_complete_notification: list_scopes helper failed: %s: %s",
                type(exc).__name__, str(exc)[:120],
            )

    # Fallback: raw SQL via storage engine. Uses the SAME async session
    # pattern the rest of the storage layer uses.
    try:
        import asyncio
        from sqlalchemy import select, distinct
        from core.src.storage.db import DeliveryItemTable, get_engine
        from sqlalchemy.ext.asyncio import AsyncSession

        async def _scan():
            engine = get_engine()
            async with AsyncSession(engine) as session:
                stmt = select(
                    distinct(DeliveryItemTable.customer_id),
                    DeliveryItemTable.device_id,
                    DeliveryItemTable.milestone_id,
                )
                rows = (await session.execute(stmt)).all()
                return [
                    (r[0], r[1], r[2]) for r in rows
                    if r[0] and r[1] and r[2]
                ]

        return asyncio.run(_scan())
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "setup_complete_notification: fallback scope scan failed: %s: %s",
            type(exc).__name__, str(exc)[:120],
        )
        return []


# ---------------------------------------------------------------------------
# SETUP-3 (2026-07-29): expected-count -- template-first, SP fallback
# ---------------------------------------------------------------------------


def _get_expected_count(
    deps: Any, customer_id: str, device_id: str, milestone_id: str,
) -> int | None:
    """Return the count of items TPM expects to have imported for
    (customer, device, milestone). Priority:

      1. template.yaml (in-memory via template_lookup) -- authoritative
         per architect 2026-07-29. Setup Deliverables writes SP from this
         same template; Default WI is included; template count == SP row
         count == Postgres row count once import catches up. Zero network.
      2. SP Deliverables_<customer> row count -- fallback when template
         not cached (edge: first tick post-worker-boot before
         template_lookup._CACHE populated, or customer with no template).

    Returns None on both-sources-fail; caller treats as "skip this tick".
    """
    # 1. Template first.
    try:
        from core.src.template_schema.template_lookup import (
            get_expected_item_count_for_milestone,
        )
        n = get_expected_item_count_for_milestone(customer_id, device_id, milestone_id)
        if n is not None:
            return n
    except Exception as exc:  # noqa: BLE001
        _log.info(
            "setup_complete_notification: template lookup raised "
            "customer=%s milestone=%s: %s -- falling back to SP",
            customer_id, milestone_id, type(exc).__name__,
        )

    # 2. SP fallback (adds ~200-500ms + has ~10s transient during initial
    #    populate; template path is preferred and normally always available).
    from core.src.sharepoint_integration.config import ListScope
    try:
        rows = deps.sp_writer.get_items(
            entity="delivery_items",
            scope=ListScope(customer_id=customer_id),
            canonical_filters={
                "project_model": device_id,
                "milestone_id":  milestone_id,
            },
        ) or []
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "setup_complete_notification: SP delivery_items fallback read "
            "failed customer=%s device=%s milestone=%s: %s: %s",
            customer_id, device_id, milestone_id,
            type(exc).__name__, str(exc)[:120],
        )
        return None
    return len(rows)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def _already_notified(
    deps: Any, customer_id: str, device_id: str, milestone_id: str,
) -> bool:
    """Return True if the 'setup_complete_notified' audit row exists for
    this scope. Falls through to False on missing query surface (better to
    double-send than never send). Same tolerance shape as
    tpm_notification._already_sent."""
    query_fn = getattr(deps.audit, "query_communications", None) or getattr(
        deps.storage, "query_communications", None
    )
    if query_fn is None:
        _log.info(
            "setup_complete_notification: audit query surface unavailable; "
            "cannot enforce idempotency for customer=%s device=%s milestone=%s",
            customer_id, device_id, milestone_id,
        )
        return False
    try:
        rows = query_fn(
            action_type=_AUDIT_ACTION_SETUP_COMPLETE,
            details_contains={
                "customer_id": customer_id,
                "device_id": device_id,
                "milestone_id": milestone_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "setup_complete_notification: audit query failed customer=%s "
            "device=%s milestone=%s: %s: %s",
            customer_id, device_id, milestone_id,
            type(exc).__name__, str(exc)[:120],
        )
        return False
    return bool(rows)


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


def _send_setup_complete(
    *,
    deps: Any,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    items: list[Any],
) -> bool:
    """Build + send the setup-complete email. Writes audit on success."""
    if deps.email_sender is None:
        _log.info(
            "setup_complete_notification: email_sender not wired; skipping "
            "send for customer=%s device=%s milestone=%s",
            customer_id, device_id, milestone_id,
        )
        return False

    # Reuse tpm_notification._read_tpm_email (TPM-1..4: SP User field
    # extraction handles the nested EMail/Title shape).
    from core.src.workflow_engine.tasks.tpm_notification import _read_tpm_email
    tpm_email, tpm_name = _read_tpm_email(deps, customer_id, device_id)
    if not tpm_email:
        _log.warning(
            "setup_complete_notification: TPM email missing in Projects list "
            "for customer=%s device=%s -- skipping send",
            customer_id, device_id,
        )
        return False

    subject = _build_subject(customer_id, device_id, milestone_id, items)
    body_html = _build_body(
        tpm_name=tpm_name,
        customer_id=customer_id,
        device_id=device_id,
        milestone_id=milestone_id,
        items=items,
    )

    try:
        from core.src.workflow_engine.tasks.tpm_notification import _send_via_email_sender
        message_id = _send_via_email_sender(
            deps=deps,
            to=tpm_email,
            subject=subject,
            body_html=body_html,
            attachments=[],
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "setup_complete_notification_send_failed: customer=%s device=%s "
            "milestone=%s: %s: %s",
            customer_id, device_id, milestone_id,
            type(exc).__name__, str(exc)[:120],
        )
        return False

    # Audit on success; subsequent ticks skip via _already_notified.
    try:
        deps.audit.write_communication_log(
            action_type=_AUDIT_ACTION_SETUP_COMPLETE,
            delivery_item_id=None,
            attribution={
                "trigger_source": "automated",
                "correlation_id": f"setup_complete:{customer_id}:{device_id}:{milestone_id}",
                "modified_by": "system:setup_complete_notification",
            },
            details={
                "customer_id":   customer_id,
                "device_id":     device_id,
                "milestone_id":  milestone_id,
                "item_count":    len(items),
                "tpm_email":     tpm_email,
                "message_id":    message_id,
                "sent_at_utc":   datetime.now(timezone.utc).isoformat(),
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "setup_complete_notification: audit write failed customer=%s "
            "device=%s milestone=%s: %s: %s",
            customer_id, device_id, milestone_id,
            type(exc).__name__, str(exc)[:120],
        )
        # Failure to audit means the next tick will re-send. Not great, but
        # better to double-send than to miss the notification entirely.

    _log.info(
        "setup_complete_notification_sent: customer=%s device=%s milestone=%s "
        "item_count=%d message_id=%s",
        customer_id, device_id, milestone_id, len(items), message_id,
    )
    return True


def _build_subject(
    customer_id: str, device_id: str, milestone_id: str, items: list[Any],
) -> str:
    return (
        f"[{customer_id}/{device_id}/{milestone_id}] "
        f"Setup deliverables complete — {len(items)} items ready"
    )


def _build_body(
    *,
    tpm_name: str | None,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    items: list[Any],
) -> str:
    """Small HTML body with per-TG breakdown."""
    # Group items by tg_name for the summary table.
    tg_counts: dict[str, int] = {}
    for it in items:
        tg = (getattr(it, "tg_name", None) or "(no TG)").strip() or "(no TG)"
        tg_counts[tg] = tg_counts.get(tg, 0) + 1

    rows_html = "\n".join(
        f"<tr><td style='padding:4px 12px;border:1px solid #ddd'>{tg}</td>"
        f"<td style='padding:4px 12px;border:1px solid #ddd;text-align:right'>{n}</td></tr>"
        for tg, n in sorted(tg_counts.items())
    )
    greeting = f"Dear {tpm_name}," if tpm_name else "Hi,"
    return (
        "<html><body style='font-family:sans-serif'>"
        f"<p>{greeting}</p>"
        f"<p>HILDA has finished importing all Setup Deliverables rows "
        f"for <b>{customer_id} / {device_id} / {milestone_id}</b>. "
        f"All <b>{len(items)}</b> items are past the initial <i>Not Started</i> "
        "state and ready for you to click Start Collection whenever you're ready.</p>"
        "<h4>Per-Technology-Group breakdown</h4>"
        "<table style='border-collapse:collapse;font-size:14px'>"
        "<thead><tr>"
        "<th style='padding:4px 12px;border:1px solid #ddd;text-align:left'>Technology Group</th>"
        "<th style='padding:4px 12px;border:1px solid #ddd;text-align:right'>Items</th>"
        "</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        "</table>"
        "<p style='margin-top:16px;color:#666;font-size:12px'>"
        "Note: HILDA sends this email once per (customer, device, milestone). "
        "If you add more items after this notification, please check the dashboard "
        "for the updated total — no follow-up email will fire automatically."
        "</p>"
        "</body></html>"
    )
