"""apply_owner_reply task -- Phase B 2026-06-28 inbound owner-reply handler.

Wired into email_polling.poll_ews_inbox_task: for each msg classified as
EmailKind.OWNER_REPLY the polling task enqueues this task with the InboundMessage
payload. This task:

1. Extracts BATCH-<id> from the message subject (re-runs classifier's regex).
2. Looks up the original send_initial_outreach audit rows for that batch_id ->
   yields (item_no -> delivery_item_id, owner_email, ...) mapping.
3. Tries the HTML table parser first (parse_table_block), falls back to the
   text structured-block parser (parse_structured_block). Returns StructuredReplyBlock.
4. For each PerItemReplyUpdate:
     - status=OPEN + non-empty note  -> write owner_status_note, no transition
     - status=OPEN + empty note      -> audit no-op
     - status=OWNER_CLOSED|BLOCKED|DELAYED -> update_delivery_state to that target
     - unknown symbol -> audit `owner_reply_unknown_status`, skip
5. Audits one `apply_owner_reply` row per parsed PerItemReplyUpdate BEFORE the
   action, so the SQL view of who-replied-what is preserved even if the action
   fails or guard-denies.

Per architect 2026-06-28: OWNER_CLOSED auto-advance to UNDER_PM_REVIEW is the
caller's concern -- tracker.update_delivery_state does that inline. The guards
on OWNER_CLOSED entry (guards.py Guard 2) enforce doc_count_reached +
all_reviews_complete for non-Confirmation items; Confirmation items bypass.
Owner-claimed-Closed without sufficient docs lands in `guard_denied` audit;
state unchanged.

Per [D-105] 4-field owner identity: sender_match comes from the parser's
resolve_sender_match call against the expected_items list. We do NOT enforce
sender_match=="owner" -- TG-alias and CC senders can legitimately answer for
the owner; the audit captures sender_match for downstream review.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from core.src.tracker import DeliveryState
from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["apply_owner_reply_task"]

_log = logging.getLogger(__name__)


# Maps parser's SCREAMING_SNAKE symbol -> canonical DeliveryState enum.
# OPEN is intentionally absent -- parser returns OPEN for "no change to report",
# the task treats it as note-only or audit-only (no state transition).
_SYMBOL_TO_STATE: dict[str, DeliveryState] = {
    "OWNER_CLOSED": DeliveryState.OWNER_CLOSED,
    "BLOCKED":      DeliveryState.BLOCKED,
    "DELAYED":      DeliveryState.DELAYED,
}

# Window for BATCH-id lookup. Outreach batches older than this are considered
# stale (owner replied after weeks); the lookup returns nothing and the task
# audits `owner_reply_batch_not_found`. Tunable if real owner-reply lag exceeds.
_BATCH_LOOKUP_WINDOW_DAYS = 60


@hilda_celery_app.task(
    name="core.src.workflow_engine.tasks.owner_reply.apply_owner_reply",
    bind=True,
    max_retries=2,
    default_retry_delay=30,
)
def apply_owner_reply_task(self, msg_payload: dict[str, Any]) -> dict[str, Any]:
    """Apply one owner-reply email's parsed updates to delivery_item rows.

    msg_payload: dict-serialized InboundMessage fields needed for parsing.
    Keys expected: subject, body_text, body_html, sender, cc_addrs,
    message_id, received_at_iso (optional).

    Returns dict for telemetry / pytest assertions:
      batch_id, rows_parsed, transitioned, guard_denied, illegal,
      notes_written, skipped_unknown_status, sender_match
    """
    try:
        return asyncio.run(_async_apply_owner_reply(msg_payload))
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "apply_owner_reply_task failed: %s: %s",
            type(exc).__name__, str(exc)[:200],
        )
        try:
            raise self.retry(exc=exc)
        except Exception:  # noqa: BLE001
            return {"error": f"{type(exc).__name__}: {str(exc)[:120]}"}


async def _async_apply_owner_reply(msg_payload: dict[str, Any]) -> dict[str, Any]:
    from core.src.email_service.inbound.body_parser_structured import parse_structured_block
    from core.src.email_service.inbound.body_parser_table import parse_table_block
    from core.src.email_service.inbound.classifier import BATCH_ID_RE
    from core.src.email_service.protocol import InboundAttachment, InboundMessage

    # Hydrate InboundMessage from payload dict. body_html / cc_addrs are
    # optional; missing -> empty defaults that the parsers tolerate.
    received_at_iso = msg_payload.get("received_at_iso")
    received_at = (
        datetime.fromisoformat(received_at_iso) if received_at_iso
        else datetime.now(timezone.utc)
    )
    msg = InboundMessage(
        message_id=msg_payload.get("message_id", ""),
        received_at=received_at,
        sender=msg_payload.get("sender", ""),
        to_addrs=tuple(msg_payload.get("to_addrs") or ()),
        cc_addrs=tuple(msg_payload.get("cc_addrs") or ()),
        subject=msg_payload.get("subject", ""),
        body_text=msg_payload.get("body_text", "") or "",
        body_html=msg_payload.get("body_html"),
        attachments=tuple(InboundAttachment(**a) for a in msg_payload.get("attachments") or ()),
    )

    # Extract BATCH-id from subject (same regex the classifier uses).
    m = BATCH_ID_RE.search(msg.subject or "")
    if m is None:
        return {"error": "owner_reply_missing_batch_id_in_subject",
                "subject": msg.subject[:80] if msg.subject else ""}
    batch_id = m.group(0)

    correlation_id = str(uuid.uuid4())
    deps = get_task_deps()

    # Look up the batch's items via the audit trail. expected_items follows the
    # shape body_parser_structured.resolve_sender_match expects (item_no +
    # owner identity fields).
    expected_items = await _lookup_batch_items(batch_id)
    if not expected_items:
        await _audit(deps, "owner_reply_batch_not_found", None, {
            "batch_id":   batch_id,
            "subject":    (msg.subject or "")[:120],
            "sender":     msg.sender,
            "message_id": msg.message_id,
            "correlation_id": correlation_id,
        })
        return {"batch_id": batch_id, "rows_parsed": 0,
                "error": "batch_not_found"}

    # Parse: try HTML table first, then text structured block.
    block = parse_table_block(msg, batch_id, expected_items)
    parser_used = "table"
    if block is None:
        block = parse_structured_block(msg, batch_id, expected_items)
        parser_used = "structured"
    if block is None:
        # Structured diagnostic so next "unparseable" is one log line away
        # from root cause. Don't store body content (NFR-2) -- presence flags
        # + small contains-check probes only.
        body_html = msg.body_html or ""
        body_text = msg.body_text or ""
        diag = {
            "batch_id":           batch_id,
            "message_id":         msg.message_id,
            "subject":            (msg.subject or "")[:120],
            "sender":             msg.sender,
            "body_html_len":      len(body_html),
            "body_text_len":      len(body_text),
            "html_has_anchor":    "HILDA-BATCH-ID" in body_html,
            "text_has_anchor":    "HILDA-BATCH-ID" in body_text,
            "html_has_table_tag": "<table" in body_html.lower(),
            "html_has_batch_id":  batch_id in body_html,
            "text_has_batch_id":  batch_id in body_text,
            "correlation_id":     correlation_id,
        }
        _log.info("owner_reply_unparseable diag: %r", diag)
        await _audit(deps, "owner_reply_unparseable", None, diag)
        return {"batch_id": batch_id, "rows_parsed": 0,
                "error": "unparseable"}

    # Build item_no -> delivery_item_id map for dispatch.
    item_no_to_id: dict[int, str] = {
        int(it["item_no"]): str(it["delivery_item_id"])
        for it in expected_items if it.get("item_no") is not None and it.get("delivery_item_id")
    }

    rows_parsed = len(block.per_item_updates)
    transitioned = 0
    guard_denied = 0
    illegal = 0
    notes_written = 0
    no_op_notes = 0
    skipped_unknown = 0
    skipped_unmapped = 0

    for upd in block.per_item_updates:
        delivery_item_id = item_no_to_id.get(upd.item_no)
        common = {
            "batch_id":         batch_id,
            "item_no":          upd.item_no,
            "parser":           parser_used,
            "sender":           msg.sender,
            "sender_match":     block.sender_match,
            "message_id":       msg.message_id,
            "delivery_state_symbol": upd.delivery_state,
            "owner_status_note":     upd.owner_status_note or "",
            "correlation_id":   correlation_id,
        }
        if delivery_item_id is None:
            skipped_unmapped += 1
            await _audit(deps, "owner_reply_item_not_in_batch",
                         None, common)
            continue

        # Audit BEFORE the action so the reply is recorded even if the
        # action fails / guard-denies / illegal-transitions.
        await _audit(deps, "apply_owner_reply", delivery_item_id, common)

        symbol = upd.delivery_state
        if symbol == "OPEN":
            if upd.owner_status_note:
                # Note-only path: persist owner_status_note, no transition.
                # Per architect 2026-06-28 design table.
                await _write_note_only(
                    deps, delivery_item_id, upd.owner_status_note,
                    correlation_id=correlation_id, common=common,
                )
                notes_written += 1
            else:
                no_op_notes += 1
                await _audit(deps, "owner_reply_no_change_no_note",
                             delivery_item_id, common)
            continue

        target_state = _SYMBOL_TO_STATE.get(symbol)
        if target_state is None:
            skipped_unknown += 1
            await _audit(deps, "owner_reply_unknown_status",
                         delivery_item_id, common)
            continue

        # Dispatch state transition via tracker. event_context per the
        # update_state_task convention.
        outcome = _apply_transition(
            deps=deps,
            delivery_item_id=delivery_item_id,
            target_state=target_state,
            note=upd.owner_status_note,
            correlation_id=correlation_id,
            batch_id=batch_id,
            sender=msg.sender,
        )
        if outcome == "transitioned":
            transitioned += 1
            # If the reply also carried a note, persist it alongside the
            # state change so the PM sees the owner's explanation.
            if upd.owner_status_note:
                await _write_note_only(
                    deps, delivery_item_id, upd.owner_status_note,
                    correlation_id=correlation_id, common=common,
                )
                notes_written += 1
        elif outcome == "guard_denied":
            guard_denied += 1
        elif outcome == "illegal_transition":
            illegal += 1
        # no_op_idempotent counts as success-equivalent for telemetry purposes

    _log.info(
        "apply_owner_reply: batch=%s parsed=%d transitioned=%d "
        "guard_denied=%d illegal=%d notes_written=%d unknown=%d "
        "unmapped=%d parser=%s",
        batch_id, rows_parsed, transitioned, guard_denied, illegal,
        notes_written, skipped_unknown, skipped_unmapped, parser_used,
    )
    return {
        "batch_id":               batch_id,
        "rows_parsed":            rows_parsed,
        "transitioned":           transitioned,
        "guard_denied":           guard_denied,
        "illegal":                illegal,
        "notes_written":          notes_written,
        "no_op_notes":            no_op_notes,
        "skipped_unknown_status": skipped_unknown,
        "skipped_unmapped":       skipped_unmapped,
        "sender_match":           block.sender_match,
        "parser_used":            parser_used,
    }


def _apply_transition(
    *,
    deps: Any,
    delivery_item_id: str,
    target_state: DeliveryState,
    note: str | None,
    correlation_id: str,
    batch_id: str,
    sender: str,
) -> str:
    """Wrap tracker.update_delivery_state. Returns outcome string."""
    from core.src.tracker import update_delivery_state

    event_context: dict[str, Any] = {
        "correlation_id":  correlation_id,
        "trigger_source":  "automated",
        "rule_id":         "owner_reply",
        "delivery_item_id": delivery_item_id,
        "batch_id":         batch_id,
        "sender":           sender,
    }
    params: dict[str, Any] = {"target_state": target_state.value}
    if note:
        params["owner_status_note"] = note

    result = update_delivery_state(
        delivery_item_id=delivery_item_id,
        target_state=target_state,
        params=params,
        event_context=event_context,
        storage=deps.storage,
        sp_writer=deps.sp_writer,
        audit=deps.audit,
    )
    return result.outcome


async def _write_note_only(
    deps: Any,
    delivery_item_id: str,
    note: str,
    *,
    correlation_id: str,
    common: dict[str, Any],
) -> None:
    """Persist owner_status_note on the delivery_item row without state change.

    Writes BOTH Postgres (via PostgresStorage.update_delivery_item) AND
    SharePoint (via sp_writer.update_item) so the owner's note is visible in
    the SP UI alongside the row state. SP write is best-effort -- Postgres is
    authoritative per [D-118] strict-boundary; SP failure logs + audits but
    does NOT roll back the Postgres write.

    Per architect scenario 2026-06-28: owner replies with status=Open +
    owner_status_note -> SP must show the note alongside the (unchanged)
    status field so the PM dashboard reflects owner intent immediately.
    """
    # Postgres write (authoritative). PostgresStorage.update_delivery_item
    # runs under run_async_sync so it's safe to call from this async context.
    try:
        deps.storage.update_delivery_item(
            delivery_item_id, {"owner_status_note": note}
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "owner_reply note-only write failed for item=%s: %s",
            delivery_item_id, str(exc)[:120],
        )
        await _audit(deps, "owner_reply_note_write_failed",
                     delivery_item_id, {**common, "error": str(exc)[:120]})
        return

    # SharePoint write (best-effort). Build scope from item's customer_id.
    # If sp_writer isn't wired (Ph-1 dev) OR customer_id missing OR SP throws,
    # log + continue -- the Postgres write above already succeeded so the PM
    # dashboard (which reads Postgres) is in sync.
    sp_written = False
    sp_error: str | None = None
    if deps.sp_writer is not None:
        try:
            from core.src.sharepoint_integration.config import ListScope
            item = deps.storage.get_delivery_item(delivery_item_id)
            customer_id = getattr(item, "customer_id", None) if item else None
            if customer_id:
                deps.sp_writer.update_item(
                    entity="delivery_items",
                    scope=ListScope(customer_id=customer_id),
                    item_id=delivery_item_id,
                    canonical_fields={"owner_status_note": note},
                )
                sp_written = True
            else:
                sp_error = "missing_customer_id_on_item"
        except Exception as exc:  # noqa: BLE001
            sp_error = f"{type(exc).__name__}: {str(exc)[:120]}"
            _log.warning(
                "owner_reply SP note write failed for item=%s: %s",
                delivery_item_id, sp_error,
            )

    audit_details = {**common, "sp_written": sp_written}
    if sp_error is not None:
        audit_details["sp_error"] = sp_error
    await _audit(deps, "owner_reply_note_written",
                 delivery_item_id, audit_details)


async def _audit(
    deps: Any,
    action_type: str,
    delivery_item_id: str | None,
    details: dict[str, Any],
) -> None:
    """Wrap deps.audit.write_communication_log with the standard attribution
    block. The audit writer is sync; called inside async context here as a
    bounded helper -- it does its own run_async_sync under the hood per
    PostgresAuditWriter pattern.
    """
    attribution = {
        "trigger_source": "automated",
        "correlation_id": details.get("correlation_id", ""),
        "modified_by":    "system:owner_reply",
    }
    try:
        deps.audit.write_communication_log(
            action_type=action_type,
            delivery_item_id=delivery_item_id,
            attribution=attribution,
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        # Audit failure must never crash the task body -- log + continue.
        _log.warning(
            "owner_reply audit write failed: action=%s err=%s",
            action_type, str(exc)[:120],
        )


async def _lookup_batch_items(batch_id: str) -> list[dict[str, Any]]:
    """Resolve a BATCH-id to the list of items it covered + their owner identity.

    Reads the audit trail: each item in a batch generated one
    send_initial_outreach row with details.batch_id == batch_id and
    details.item_no, details.recipient, plus the delivery_item_id on the row.
    For owner-identity matching we read the corresponding delivery_item rows
    so resolve_sender_match has the 4-field [D-105] identity available.

    Returns expected_items list shape:
      [{item_no, delivery_item_id, owner_corp_usa_email, owner_corp_email,
        tg_email_group_alias}, ...]
    """
    from core.src.storage.audit_ops import query_communications
    from core.src.storage.delivery_item_ops import get_delivery_item

    since = datetime.now(timezone.utc) - timedelta(days=_BATCH_LOOKUP_WINDOW_DAYS)
    rows = await query_communications(
        action_type="send_initial_outreach",
        since=since,
        limit=1000,
    )
    # Python-side filter on the JSON-encoded summary -- batch_id lives at
    # details.batch_id. Avoids JSONB-specific SQL so sqlite tests still work.
    matched: list[dict[str, Any]] = []
    seen_item_ids: set[str] = set()
    scanned = 0
    parse_failed = 0
    no_delivery_item_id = 0
    no_item_no = 0
    item_row_missing = 0
    for r in rows:
        summary = r.summary
        if not summary or batch_id not in summary:
            continue
        scanned += 1
        try:
            payload = json.loads(summary)
        except (ValueError, TypeError):
            parse_failed += 1
            continue
        details = payload.get("details") if isinstance(payload, dict) else None
        if not isinstance(details, dict):
            parse_failed += 1
            continue
        if details.get("batch_id") != batch_id:
            continue
        delivery_item_id = r.delivery_item_id
        if not delivery_item_id or delivery_item_id in seen_item_ids:
            no_delivery_item_id += 1
            continue
        seen_item_ids.add(delivery_item_id)

        # Hydrate from the live delivery_item row -- gives us both the
        # owner identity (for sender_match) and item_no (so we don't depend
        # on kickoff having written item_no into details, which it didn't
        # in the original Step 5 Phase A audit shape). Architect live test
        # 2026-06-28: "batch_not_found" was the symptom of this depencency.
        item = await get_delivery_item(delivery_item_id)
        if item is None:
            item_row_missing += 1
            continue
        # Prefer the live row's item_no; fall back to details.item_no (added
        # 2026-06-28 forward) only if the row doesn't expose it.
        item_no_val: int | None
        raw_from_row = getattr(item, "item_no", None)
        raw_from_details = details.get("item_no")
        try:
            if raw_from_row is not None:
                item_no_val = int(raw_from_row)
            elif raw_from_details is not None:
                item_no_val = int(raw_from_details)
            else:
                item_no_val = None
        except (ValueError, TypeError):
            item_no_val = None
        if item_no_val is None:
            no_item_no += 1
            continue

        matched.append({
            "item_no":              item_no_val,
            "delivery_item_id":     delivery_item_id,
            "owner_corp_usa_email": getattr(item, "owner_corp_usa_email", None),
            "owner_corp_email":     getattr(item, "owner_corp_email", None),
            "tg_email_group_alias": getattr(item, "tg_email_group_alias", None)
                                    or getattr(item, "email_group_alias", None),
        })

    if not matched:
        _log.info(
            "owner_reply lookup empty: batch=%s scanned=%d parse_failed=%d "
            "no_delivery_item_id=%d no_item_no=%d item_row_missing=%d total_audit_rows=%d",
            batch_id, scanned, parse_failed, no_delivery_item_id,
            no_item_no, item_row_missing, len(rows),
        )
    return matched
