"""ops_digest.py -- UR-8 (Ph-2 2026-08-01): weekly ops digest of unrouted files.

Beat task fires every N seconds (default 604800 = 7d per
TpmNotificationConfig.ops_unrouted_digest_beat_interval_seconds). Each
tick:

  1. Enumerate every (customer, device, milestone) scope with document_index
     rows via storage.unrouted_ops.list_all_unrouted_scopes.
  2. For each scope, count unrouted docs (count_unrouted_for_scope).
  3. Aggregate scopes with count >= cfg.ops_unrouted_digest_min_count into
     one HTML email; subject carries the grand total; body renders a per-
     scope table with links back to the UR-5 /_unknownTG/ page.
  4. Send to cfg.ops_unrouted_digest_recipient via deps.email_sender.
  5. Write a single audit row (`ops_unrouted_digest_sent`) so history is
     preserved. Idempotency is time-based only -- next beat = next digest;
     no per-scope idempotency (ops WANT the periodic reminder).

Short-circuits with a specific outcome when:
  * cfg.ops_unrouted_digest_enabled=false
  * deps.email_sender / deps.storage unavailable
  * cfg.ops_unrouted_digest_recipient empty (still-scaffolded deploy)
  * aggregate total < min_count (nothing to shout about this week)
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.task_deps import get_task_deps
from core.src.workflow_engine.tpm_notification_config import TpmNotificationConfig

__all__ = ["ops_unrouted_digest_tick_task"]

_log = logging.getLogger(__name__)

_AUDIT_ACTION_DIGEST_SENT = "ops_unrouted_digest_sent"


@hilda_celery_app.task(
    name="core.src.workflow_engine.tasks.ops_digest.ops_unrouted_digest_tick",
)
def ops_unrouted_digest_tick_task(
    params: dict[str, Any] | None = None,
    event_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Beat tick body. See module docstring."""
    cfg = TpmNotificationConfig.from_sources()
    if not cfg.ops_unrouted_digest_enabled:
        return {"outcome": "disabled", "scopes_scanned": 0}
    if not cfg.ops_unrouted_digest_recipient:
        _log.info(
            "ops_unrouted_digest: no recipient configured; skipping tick "
            "(set HILDA_TPM_NOTIFICATION_OPS_UNROUTED_DIGEST_RECIPIENT)",
        )
        return {"outcome": "no_recipient", "scopes_scanned": 0}

    deps = get_task_deps()
    if deps.email_sender is None:
        _log.info("ops_unrouted_digest: email_sender unavailable; skipping tick")
        return {"outcome": "no_email_sender", "scopes_scanned": 0}

    # Async storage helpers -- run via a fresh loop so the tick body stays sync.
    import asyncio

    async def _collect() -> list[dict[str, Any]]:
        from core.src.storage.unrouted_ops import (
            count_unrouted_for_scope, list_all_unrouted_scopes,
        )
        scopes = await list_all_unrouted_scopes()
        rows: list[dict[str, Any]] = []
        for c, d, m in scopes:
            n = await count_unrouted_for_scope(c, d, m)
            if n >= cfg.ops_unrouted_digest_min_count:
                rows.append({
                    "customer_id":  c,
                    "device_id":    d,
                    "milestone_id": m,
                    "unrouted":     n,
                })
        return rows

    try:
        scope_rows = asyncio.run(_collect())
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "ops_unrouted_digest: collect failed: %s: %s",
            type(exc).__name__, str(exc)[:200],
        )
        return {"outcome": "collect_failed", "scopes_scanned": 0}

    scopes_scanned = len(scope_rows)
    total_unrouted = sum(r["unrouted"] for r in scope_rows)

    if scopes_scanned == 0:
        _log.info(
            "ops_unrouted_digest: no scopes over min_count=%d; nothing to send",
            cfg.ops_unrouted_digest_min_count,
        )
        return {
            "outcome": "no_scopes_over_threshold",
            "scopes_scanned": 0,
            "total_unrouted": 0,
        }

    subject = _build_subject(total_unrouted, scopes_scanned)
    body = _build_body(scope_rows, total_unrouted)
    try:
        from core.src.workflow_engine.tasks.tpm_notification import (
            _send_via_email_sender,
        )
        message_id = _send_via_email_sender(
            deps=deps,
            to=cfg.ops_unrouted_digest_recipient,
            subject=subject,
            body_html=body,
            attachments=[],
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "ops_unrouted_digest_send_failed: %s: %s",
            type(exc).__name__, str(exc)[:200],
        )
        return {
            "outcome": "send_failed",
            "scopes_scanned": scopes_scanned,
            "total_unrouted": total_unrouted,
        }

    _audit_send(deps, scope_rows, total_unrouted, message_id)
    _log.info(
        "ops_unrouted_digest_sent: scopes=%d total_unrouted=%d recipient=%s "
        "message_id=%s",
        scopes_scanned, total_unrouted,
        cfg.ops_unrouted_digest_recipient, message_id,
    )
    return {
        "outcome":        "sent",
        "scopes_scanned": scopes_scanned,
        "total_unrouted": total_unrouted,
        "message_id":     message_id,
    }


# ---------------------------------------------------------------------------
# Email content
# ---------------------------------------------------------------------------


def _build_subject(total: int, scopes_over_threshold: int) -> str:
    return (
        f"[HILDA] Unrouted files digest -- {total} across "
        f"{scopes_over_threshold} scope(s)"
    )


def _build_body(scope_rows: list[dict[str, Any]], total: int) -> str:
    """Simple HTML table -- one row per scope over threshold, sorted by count
    desc so the loudest offenders sit at the top. The link goes to the UR-5
    /_unknownTG/ page for that scope; the dashboard host is templated at
    runtime by the receiving mail client (no cfg here to avoid circular
    coupling with dashboard config)."""
    rows_sorted = sorted(scope_rows, key=lambda r: r["unrouted"], reverse=True)
    row_html = "\n".join(
        f"<tr><td>{r['customer_id']}</td><td>{r['device_id']}</td>"
        f"<td>{r['milestone_id']}</td><td style='text-align:right'>{r['unrouted']}</td>"
        f"<td><a href='/browse/{r['customer_id']}/{r['device_id']}/"
        f"{r['milestone_id']}/_unknownTG/'>triage</a></td></tr>"
        for r in rows_sorted
    )
    return (
        "<html><body>"
        "<p>Weekly HILDA unrouted-files digest. Each row lists a "
        "(customer, device, milestone) scope with files parked in the "
        "<code>_unknownTG</code> bucket -- the router could not match them "
        "to a specific work item. Click <em>triage</em> to open the manual-"
        "routing UI.</p>"
        f"<p><b>Total unrouted:</b> {total}</p>"
        "<table border='1' cellspacing='0' cellpadding='6' "
        "style='border-collapse:collapse'>"
        "<thead><tr><th>Customer</th><th>Device</th><th>Milestone</th>"
        "<th>Unrouted</th><th>Action</th></tr></thead>"
        f"<tbody>{row_html}</tbody></table>"
        "</body></html>"
    )


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def _audit_send(
    deps: Any, scope_rows: list[dict[str, Any]], total: int, message_id: str,
) -> None:
    if deps.audit is None:
        return
    try:
        deps.audit.write_communication_log(
            action_type=_AUDIT_ACTION_DIGEST_SENT,
            delivery_item_id=None,
            attribution={
                "trigger_source": "beat:ops_unrouted_digest",
                "correlation_id": f"ops_unrouted_digest:{datetime.now(timezone.utc).date().isoformat()}",
                "modified_by":    "system:ops_unrouted_digest",
            },
            details={
                "scopes_reported":  len(scope_rows),
                "total_unrouted":   total,
                "message_id":       message_id,
                "sent_at_utc":      datetime.now(timezone.utc).isoformat(),
                "top_scopes":       sorted(
                    scope_rows, key=lambda r: r["unrouted"], reverse=True,
                )[:10],
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "ops_unrouted_digest: audit write failed: %s: %s",
            type(exc).__name__, str(exc)[:120],
        )
