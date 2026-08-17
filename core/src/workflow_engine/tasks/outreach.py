"""outreach.py: SEND_INITIAL_OUTREACH + SEND_REMINDER + NOTIFY_NEW_OWNER task bodies.

Per FR-9 (initial outreach) + FR-10 (reminders) + FR-2/FR-83 (new owner notify).
Each task delegates to email_service.EmailSender for actual SMTP send; on Ph-1
worker setups without email_sender wired into TaskDeps, the task degrades to
audit-only (CommunicationLog row written, send skipped).

Pattern matches escalation.py (notify_pm_task / notify_hilda_ops_task) for
audit-log shape; extends with conditional downstream-module dispatch.
"""
from __future__ import annotations

import logging
from typing import Any

from core.src.rule_engine import ActionKind

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.registry import TaskBinding, register_task_binding
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = [
    "send_initial_outreach_task",
    "send_reminder_task",
    "notify_new_owner_task",
    "modality_display",           # MOD-1 (2026-08-17): outreach column helper
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MOD-1 (2026-08-17): Tracking Modality column value
# ---------------------------------------------------------------------------
# Per architect 2026-08-17: outreach email gets a "Tracking Modality" column
# so owner sees HOW HILDA receives their docs per item. Method of OUTREACH is
# always Email; the column describes the RECEPTION channel per item. Owner
# must reply "Closed" for ALL items regardless of modality (existing D-138
# behavior; no gate change).
#
# Mapping (architect ask 2026-08-17):
#   Email               -> "email"
#   NetworkSharedDrive  -> "Network Shared Drive"
#   CustomerJIRA        -> "Verizon JIRA"   (hardcoded per architect for P1
#                          milestone; MMK is the only live customer today.
#                          TODO: generalize to per-customer label when a
#                          non-Verizon customer needs CustomerJIRA support.)
#   CorporatePLM        -> the plm_id string (e.g. "P260804-92345") when
#                          populated; else "CorporatePLM (pending)" -- this
#                          "pending" case should not normally appear post-
#                          PLMKO-2 (kickoff synchronously creates before
#                          firing outreach), but the fallback stays for
#                          plm-create-failed edge cases so the owner still
#                          gets useful context.


_MODALITY_STATIC_LABELS = {
    "Email":              "email",
    "NetworkSharedDrive": "Network Shared Drive",
    "CustomerJIRA":       "Verizon JIRA",
}


def modality_display(modality: Any, plm_id: str | None) -> str:
    """Render one item's Tracking Modality column value for the outreach
    table. `modality` is the item's tracking_modality field -- per D-037
    it's list[str] but scalar-str is tolerated (defensive). Per architect
    2026-08-17 a single item has exactly ONE modality value.

    Returns "" for missing/empty modality (renders as empty cell). Returns
    the raw modality string as fallback for unknown values so the column
    still shows something meaningful during future modality additions."""
    # Normalize modality to a single scalar string (first entry of list,
    # or the scalar itself).
    value = ""
    if isinstance(modality, str):
        value = modality.strip()
    elif isinstance(modality, (list, tuple)):
        for entry in modality:
            if entry and str(entry).strip():
                value = str(entry).strip()
                break
    if not value:
        return ""
    if value == "CorporatePLM":
        plm = (plm_id or "").strip()
        return plm if plm else "CorporatePLM (pending)"
    return _MODALITY_STATIC_LABELS.get(value, value)


def _record_reminder_attempt(deps, delivery_item_id: str | None) -> int | None:
    """Increment item.reminder_count + stamp last_reminder_triggered_at after
    a SEND_REMINDER task body fires.

    Added 2026-06-27 per [D-118] Chunk 4 rule-walk-through Finding 1 -- prior
    code never incremented reminder_count, so the FR-10 cadence ladder
    (Rule 2a: count<1 -> 1st reminder; Rule 2b: 1<=count<2 -> 2nd reminder)
    would loop forever on Rule 2a and never escalate to Rule 2b. Now the
    counter advances each task invocation regardless of email-send success
    (audit-only mode still advances cadence -- otherwise un-wired dev setups
    would never exit Rule 2a).

    Also stamps last_reminder_triggered_at per NFR-21 §5 amendment 2026-06-21
    (HILDA + SP UI dual-writer; this is HILDA's write).

    Returns the new reminder_count, or None if delivery_item_id missing /
    storage write failed (non-fatal -- caller still audit-logs).
    """
    if not delivery_item_id:
        return None
    try:
        item = deps.storage.get_delivery_item(delivery_item_id)
        prior = getattr(item, "reminder_count", 0) or 0
        new_count = prior + 1
        from datetime import datetime, timezone
        deps.storage.update_delivery_item(
            delivery_item_id,
            {
                "reminder_count":             new_count,
                "last_reminder_triggered_at": datetime.now(timezone.utc),
            },
        )
        return new_count
    except Exception:  # noqa: BLE001 -- non-fatal; audit-only fall-through
        return None


def _resolve_recipient(deps, event_context: dict[str, Any], params: dict[str, Any]) -> str | None:
    """DEPRECATED (OWNER-3, 2026-08-14): kept for pre-existing callers /
    tests. Delegates to _resolve_recipients() and returns first entry or
    None. All new code should use _resolve_recipients() to get the full
    multi-owner list."""
    recipients = _resolve_recipients(deps, event_context, params)
    return recipients[0] if recipients else None


def _resolve_recipients(
    deps, event_context: dict[str, Any], params: dict[str, Any],
) -> list[str]:
    """OWNER-3 (2026-08-14) + OWNER-7 (2026-08-16): resolve outreach recipient
    LIST per multi-owner semantics. Returns [] if no owner is resolvable
    (caller writes audit-only row + skips send).

    Path A precedence (architect 2026-06-27, extended for lists 2026-08-14,
    simplified for B-final-B 2026-08-16 -- owner_* is now list-typed on
    DeliveryItemBase so no more singular fallback rungs):
      1. Explicit params.recipient                     (rule YAML can pin -- always single)
      2. **SP-side owner_corp_usa_email** as list      (live; via sp_writer; preferred per [D-080])
      3. **SP-side owner_corp_email** as list          (live fallback)
      4. Storage DeliveryItem.owner_corp_usa_email     (offline fallback; JSON list post-OWNER-7)
      5. Storage DeliveryItem.owner_corp_email         (offline fallback; JSON list)
      6. event_context.owner_corp_usa_email            (legacy callers / fixtures) as single-element list
      7. []  -> caller writes audit-only row, skips send

    Reading SP at fire-time means TPM mid-flight owner edits in SP are
    automatically honored without HILDA-side persistence. Failure modes
    (network, SP outage, schema mismatch) fall through silently to the
    storage/event_context fallbacks rather than blocking the email.

    Multi-owner semantics per architect 2026-08-14: TPMs may type
    'alice@corp; bob@corp' in the SP text column; both go in TO of ONE
    outreach email; any owner can reply and it's attributed correctly
    (OWNER-4). SP text column parsing uses _split_owner_list which
    accepts both ';' (SP convention) and ',' (TPM tolerance).
    """
    explicit = params.get("recipient")
    if explicit:
        return [explicit]

    delivery_item_id = event_context.get("delivery_item_id")
    item = None
    if delivery_item_id:
        try:
            item = deps.storage.get_delivery_item(delivery_item_id)
        except Exception:  # noqa: BLE001 -- storage miss is non-fatal
            item = None

    # Path A: SP read at fire-time (returns list already-parsed)
    if item is not None and deps.sp_writer is not None:
        sp_owners = _read_owner_list_from_sp(deps, item)
        if sp_owners:
            return sp_owners

    # Fallback: storage-cached owner list (post-OWNER-7, unsuffixed IS the list).
    if item is not None:
        list_field = (
            getattr(item, "owner_corp_usa_email", None)
            or getattr(item, "owner_corp_email", None)
        )
        if list_field:
            return list(list_field)

    # Fallback 2: event_context (legacy callers + tests that pre-populate).
    from_event = event_context.get("owner_corp_usa_email")
    if from_event:
        return [from_event]

    return []


def _read_owner_from_sp(deps, item: Any) -> str | None:
    """DEPRECATED (OWNER-3, 2026-08-14): use _read_owner_list_from_sp() for
    multi-owner support. This wrapper returns first entry or None."""
    owners = _read_owner_list_from_sp(deps, item)
    return owners[0] if owners else None


def _read_owner_list_from_sp(deps, item: Any) -> list[str]:
    """OWNER-3 (2026-08-14): read live owner identity LIST from SP.

    SP text column may contain a semicolon-separated string like
    'alice@corp; bob@corp' when multiple owners share the item. Returns
    parsed list via _split_owner_list; falls back to owner_corp_email if
    owner_corp_usa_email is empty (per [D-080] preference).

    Best-effort: returns [] on any failure (network, no match, schema
    mismatch). Caller falls back to storage / event_context.

    Natural key per architect 2026-06-27 Step 4 probe + field-map table:
      - Milestone instance: (carrier, milestone_name, project_id_or_project_model)
        is unique. carrier is implicit in scope=ListScope(customer_id=...).
      - Delivery item:      milestone-instance key + item_no as a selector
        (item_no is a row attribute, not part of the milestone natural key).

    HILDA storage -> canonical_filters mapping:
      - item.milestone_id  -> canonical "milestone_id"  -> SP "milestone_name" (text, "P1")
      - item.device_id     -> canonical "project_model" -> SP "project_model"  (text, "SM-S671U1")
      - item.item_no       -> canonical "item_no"       -> SP "item_no"        (number, 1)

    Filtering by all three returns exactly the target row even when the
    customer's Deliverables list spans multiple projects / milestones.
    """
    from core.src.sharepoint_integration.config import ListScope
    from core.src.workflow_engine.tasks.sp_alert_imports import _split_owner_list
    customer_id = getattr(item, "customer_id", None)
    milestone_id = getattr(item, "milestone_id", None)
    device_id = getattr(item, "device_id", None)
    item_no = getattr(item, "item_no", None)
    if not customer_id or item_no is None:
        return []
    filters: dict[str, Any] = {"item_no": item_no}
    if milestone_id:
        filters["milestone_id"] = milestone_id
    if device_id:
        filters["project_model"] = device_id
    try:
        scope = ListScope(customer_id=customer_id)
        rows = deps.sp_writer.get_items(
            entity="delivery_items",
            scope=scope,
            canonical_filters=filters,
        )
    except Exception as exc:  # noqa: BLE001 -- SP read is best-effort
        _log.warning(
            "_resolve_recipients: SP read failed for customer_id=%s milestone_id=%s "
            "project_model=%s item_no=%s: %s",
            customer_id, milestone_id, device_id, item_no, type(exc).__name__,
        )
        return []
    if not rows:
        _log.info(
            "_resolve_recipients: SP returned no rows for customer_id=%s milestone_id=%s "
            "project_model=%s item_no=%s",
            customer_id, milestone_id, device_id, item_no,
        )
        return []
    if len(rows) > 1:
        _log.warning(
            "_resolve_recipients: SP returned %d rows for customer_id=%s milestone_id=%s "
            "project_model=%s item_no=%s; using first. Natural key "
            "(customer, milestone, project_model) + item_no should be unique -- "
            "check MMK column-map or schema for duplicates.",
            len(rows), customer_id, milestone_id, device_id, item_no,
        )
    row = rows[0]
    # OWNER-3: parse both singular columns (SP text with ';' delimiter for
    # multi-owner) and return the first non-empty list. _split_owner_list
    # tolerates single-value strings ('alice@corp' -> ['alice@corp']) as
    # well as multi-value strings ('alice@corp; bob@corp' -> [both]).
    usa_owners = _split_owner_list(row.get("owner_corp_usa_email"))
    if usa_owners:
        return usa_owners
    return _split_owner_list(row.get("owner_corp_email"))


@hilda_celery_app.task(
    name="core.src.workflow_engine.tasks.outreach.send_initial_outreach",
    bind=True,
    max_retries=3,
    default_retry_delay=30,
)
def send_initial_outreach_task(
    self, params: dict[str, Any], event_context: dict[str, Any]
) -> dict[str, Any]:
    """SEND_INITIAL_OUTREACH per FR-9 -- send first outreach email to owner(s).

    params:
      - template:  str (e.g., "standard_owner_outreach")
      - channel:   str (default "email"; "messenger" Ph-2 fallback)
      - recipient: str (optional override; defaults to DI owner_corp_usa_email)

    event_context: standard (correlation_id, customer_id, milestone_id,
    delivery_item_id, trigger_source).

    Behavior:
    - Resolves recipient from event_context.recipient OR DI owner identity.
    - When deps.email_sender is wired: composes outreach via email_service
      compose_outreach + dispatches via email_sender.send -> Message-ID.
    - When deps.email_sender is None (Ph-1 worker not fully wired): audit-only.
    """
    deps = get_task_deps()
    template = params.get("template", "outreach_table")
    channel = params.get("channel", "email")
    delivery_item_id = event_context.get("delivery_item_id")
    # OWNER-3 (2026-08-14): resolve full owner LIST; ONE email goes to ALL
    # of them per architect direction. `recipient` (singular) kept as
    # first-of-list for legacy audit/log paths + template BC.
    recipients = _resolve_recipients(deps, event_context, params)
    recipient = recipients[0] if recipients else None

    # Generate BATCH-id deterministically from correlation_id so the inbound
    # reply parser can correlate the reply back to delivery_item_id via the
    # communication_log audit row. Uses the first 10 hex chars of correlation
    # (sufficient entropy for FR-24 BATCH-id token uniqueness within a
    # milestone's outreach window).
    correlation_id = event_context.get("correlation_id", "")
    batch_id = f"BATCH-{correlation_id.replace('-', '')[:10]}"

    # Resolve owner identity for template rendering (owner_name) AND fetch the
    # current SP-side item row for item_name. Best-effort -- on any failure,
    # fall back to recipient-only / minimal-render path.
    owner_identity, item_for_template = _fetch_template_inputs(
        deps, delivery_item_id, recipient,
    )

    message_id = None
    if channel == "email" and deps.email_sender is not None and recipients:
        try:
            body_html = _render_outreach_table(
                owner_identity=owner_identity,
                items=[item_for_template] if item_for_template else [],
                batch_id=batch_id,
            )
            # Subject enriched 2026-07-08: TPM asked for customer/device/milestone
            # in the subject line so inbox scans surface routing context without
            # opening the email. All values sourced from event_context populated
            # by the dispatcher (customer_id/milestone_id) or the fetched item
            # for template (device_id).
            _cust = event_context.get("customer_id") or ""
            _mile = event_context.get("milestone_id") or ""
            _dev = ""
            if item_for_template:
                _dev = (
                    item_for_template.get("device_id")
                    or item_for_template.get("project_model")
                    or ""
                )
            _ctx = " / ".join(p for p in (_cust, _dev, _mile) if p)
            _subject_prefix = f"[HILDA] {_ctx}" if _ctx else "[HILDA]"
            message_id = _send_email(
                deps,
                to=recipients,
                subject=f"{_subject_prefix} -- Status request -- {batch_id}",
                body_marker=body_html,
            )
        except Exception as e:  # noqa: BLE001
            # REL-1 (2026-07-25) — audit-before-raise fix for GAP 1: prior code
            # swallowed the send exception and returned outcome="audit_only",
            # which made the downstream chain UpdateState task advance the item
            # to OutreachSent even though NO email was actually sent. Owner never
            # got contacted; TPM discovered days later during reminder cadence.
            #
            # New behavior: write a failure audit row so HILDA OPS + TPM history
            # show the attempted send + retry story, THEN re-raise so Celery
            # retries (max_retries=3, delay=30s) AND the chain breaks — the
            # UpdateState task never runs while the send is still failing.
            # After max_retries exhausts, the task fails permanently, item stays
            # at Open, and HILDA OPS can query CommunicationLog for
            # action_type='send_initial_outreach_failed' to find stuck items.
            _log.warning(
                "send_initial_outreach email send failed: %s: %s "
                "(attempt %s of %s)",
                type(e).__name__, str(e)[:120],
                self.request.retries + 1,
                (self.max_retries or 0) + 1,
            )
            try:
                deps.audit.write_communication_log(
                    action_type="send_initial_outreach_failed",
                    delivery_item_id=delivery_item_id,
                    attribution={
                        "trigger_source": event_context.get("trigger_source", "automated"),
                        "correlation_id": correlation_id,
                        "modified_by":    event_context.get("pm_id", "system"),
                    },
                    details={
                        "template":      template,
                        "channel":       channel,
                        "recipient":     recipient,
                        "batch_id":      batch_id,
                        "milestone_id":  event_context.get("milestone_id"),
                        "error_type":    type(e).__name__,
                        "error":         str(e)[:200],
                        "retry_attempt": self.request.retries + 1,
                        "max_retries":   (self.max_retries or 0) + 1,
                    },
                )
            except Exception as audit_exc:  # noqa: BLE001
                # Audit itself failed — log but don't mask the original send error.
                _log.warning(
                    "send_initial_outreach_failed audit write ALSO failed: %s: %s",
                    type(audit_exc).__name__, str(audit_exc)[:120],
                )
            # Re-raise so Celery retries and the chain UpdateState task does NOT
            # run. Item stays at Open; on final give-up state stays at Open with
            # the failure audit trail visible in CommunicationLog.
            raise

    deps.audit.write_communication_log(
        action_type="send_initial_outreach",
        delivery_item_id=delivery_item_id,
        attribution={
            "trigger_source": event_context.get("trigger_source", "automated"),
            "correlation_id": correlation_id,
            "modified_by":    event_context.get("pm_id", "system"),
        },
        details={
            "template":      template,
            "channel":       channel,
            "recipient":     recipient,
            "batch_id":      batch_id,
            "milestone_id":  event_context.get("milestone_id"),
            "message_id":    message_id,
            "send_skipped":  message_id is None,
        },
    )
    return {
        "template":     template,
        "channel":      channel,
        "recipient":    recipient,
        "batch_id":     batch_id,
        "message_id":   message_id,
        "outcome":      "sent" if message_id else "audit_only",
    }


def _fetch_template_inputs(
    deps, delivery_item_id: str | None, recipient: str | None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Resolve owner identity dict + SP-side item row dict for the outreach
    template render. Best-effort: returns minimal stubs on lookup failure so
    the email still sends (owner_name='Owner', item_name='Item ?').

    Identity dict shape per [D-105]:
      {owner_corp_usa_email, owner_corp_email, owner_corp_id, owner_name}
    Item dict shape for template (subset):
      {item_no, item_name}
    """
    owner_identity: dict[str, Any] = {
        "owner_corp_usa_email": recipient,
        "owner_corp_email":     None,
        "owner_corp_id":        None,
        "owner_name":           None,
        # OWNER-3 (2026-08-14): multi-owner display list -- Jinja template
        # renders greeting as "Hi Alice, Bob, Carol,". Populated from SP
        # owner_name via _split_owner_list below; empty [] on lookup failure
        # falls back to singular "owner_name" via `or "Owner"` in template.
        "owner_names":          [],
    }
    item_for_template: dict[str, Any] | None = None
    if not delivery_item_id or deps.storage is None:
        return owner_identity, item_for_template

    try:
        item = deps.storage.get_delivery_item(delivery_item_id)
    except Exception:  # noqa: BLE001
        return owner_identity, item_for_template
    if item is None:
        return owner_identity, item_for_template

    # Always populate item shape from storage snapshot (cheap, no SP call).
    # MOD-1 (2026-08-17): also carry tracking_modality + plm_id so the
    # outreach template's Tracking Modality column renders per-row.
    item_for_template = {
        "item_no":           getattr(item, "item_no", None),
        "item_name":         getattr(item, "item_name", None) or f"Item {getattr(item, 'item_no', '?')}",
        "tracking_modality": getattr(item, "tracking_modality", None),
        "plm_id":            getattr(item, "plm_id", None) or "",
    }

    # SP-read for live owner_name (matches Path A: SP is source of truth for
    # owner identity). Best-effort -- never blocks the send.
    if deps.sp_writer is not None:
        try:
            from core.src.sharepoint_integration.config import ListScope
            scope = ListScope(customer_id=getattr(item, "customer_id", "") or "")
            filters: dict[str, Any] = {}
            if getattr(item, "milestone_id", None):
                filters["milestone_id"] = item.milestone_id
            if getattr(item, "device_id", None):
                filters["project_model"] = item.device_id
            if getattr(item, "item_no", None) is not None:
                filters["item_no"] = item.item_no
            if filters:
                rows = deps.sp_writer.get_items(
                    entity="delivery_items", scope=scope, canonical_filters=filters,
                )
                if rows:
                    from core.src.workflow_engine.tasks.sp_alert_imports import _split_owner_list
                    r = rows[0]
                    owner_identity["owner_name"] = r.get("owner_name") or None
                    owner_identity["owner_corp_id"] = r.get("owner_corp_id") or None
                    owner_identity["owner_corp_email"] = (
                        r.get("owner_corp_email") or owner_identity["owner_corp_email"]
                    )
                    # OWNER-3: multi-owner name list for greeting rendering.
                    owner_identity["owner_names"] = _split_owner_list(r.get("owner_name"))
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "_fetch_template_inputs: SP owner lookup failed for item=%s: %s",
                delivery_item_id, type(exc).__name__,
            )

    return owner_identity, item_for_template


def _send_batch_outreach_email(
    *,
    deps,
    owner_identity: dict[str, Any],
    items: list[dict[str, Any]],
    batch_id: str,
    recipient: str | list[str],
) -> str | None:
    """Render outreach_table.j2 with N item rows and send ONE email to the
    owner. Returns the EWS Message-ID on success, None on send failure.

    Called from sp_alert_imports.kickoff_collection_task after it has
    resolved owner identity via Path A SP batch-read and grouped items by
    owner. Centralized here so the template+send pair stays in the outreach
    module rather than leaking into sp_alert_imports.

    Architect Step 5 design 2026-06-28: one email per owner with all the
    owner's items in a single table, not one email per item.
    """
    body_html = _render_outreach_table(
        owner_identity=owner_identity,
        items=items,
        batch_id=batch_id,
    )
    # Subject enriched 2026-07-08 (parity with per-item send_initial_outreach):
    # customer / device / milestone taken from items[0]. All items in the batch
    # share (customer, device, milestone) by construction -- kickoff groups by
    # owner AND (implicitly) by device+milestone since it iterates the milestone.
    _first = items[0] if items else {}
    _cust = _first.get("customer_id") or ""
    _dev = _first.get("device_id") or _first.get("project_model") or ""
    _mile = _first.get("milestone_id") or ""
    _ctx = " / ".join(p for p in (_cust, _dev, _mile) if p)
    _subject_prefix = f"[HILDA] {_ctx}" if _ctx else "[HILDA]"
    try:
        return _send_email(
            deps,
            to=recipient,
            subject=f"{_subject_prefix} -- Status request -- {batch_id}",
            body_marker=body_html,
        )
    except Exception as e:  # noqa: BLE001
        _log.warning(
            "_send_batch_outreach_email: send failed for owner=%s batch=%s items=%d: %s: %s",
            recipient, batch_id, len(items), type(e).__name__, str(e)[:120],
        )
        return None


def _render_outreach_table(
    *,
    owner_identity: dict[str, Any],
    items: list[dict[str, Any]],
    batch_id: str,
) -> str:
    """Render outreach_table.j2 -> HTML string for the email body.

    The template includes:
      - greeting using owner.owner_name (falls back to "Owner" inside the j2)
      - HILDA-BATCH-ID anchor span (parser-readable)
      - <table> with one row per item (item_no, item_name, tracking_modality
        display, status=Open, completion date, note=blank)
      - status legend + reply instructions

    MOD-1 (2026-08-17): before render, computes each item's `modality_display`
    field via modality_display() so the template just prints it verbatim.
    Callers may pre-populate `tracking_modality` + `plm_id` on each item
    dict; missing = renders as empty cell.

    Caller passes already-resolved data; this function does NOT do any IO.
    """
    from jinja2 import Environment, PackageLoader, select_autoescape

    # Enrich items with modality_display so the Jinja template stays simple
    # (no Python logic in .j2). Non-destructive per row (shallow copy).
    enriched_items = []
    for it in items:
        row = dict(it) if isinstance(it, dict) else {"item_no": None, "item_name": None}
        row["modality_display"] = modality_display(
            row.get("tracking_modality"), row.get("plm_id"),
        )
        enriched_items.append(row)

    env = Environment(
        loader=PackageLoader("core.src.email_service", "templates"),
        autoescape=select_autoescape(["html", "j2"]),
    )
    template = env.get_template("outreach_table.j2")
    return template.render(
        owner=owner_identity,
        items=enriched_items,
        batch_id=batch_id,
    )


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.outreach.send_reminder")
def send_reminder_task(
    params: dict[str, Any], event_context: dict[str, Any]
) -> dict[str, Any]:
    """SEND_REMINDER per FR-10 -- send Nth reminder email reusing BATCH-id for thread continuity.

    params:
      - template:        str (e.g., "standard_owner_reminder")
      - channel:         str (default "email")
      - reminder_count:  int (1-indexed; passed to template for "1st/2nd/3rd reminder" copy)

    event_context: standard.

    Behavior matches send_initial_outreach (audit-only when email_sender None).
    """
    deps = get_task_deps()
    template = params.get("template", "standard_owner_reminder")
    channel = params.get("channel", "email")
    delivery_item_id = event_context.get("delivery_item_id")
    # OWNER-3 (2026-08-14): multi-owner reminder -- ONE email to all owners.
    recipients = _resolve_recipients(deps, event_context, params)
    recipient = recipients[0] if recipients else None

    # Advance FR-10 cadence counter BEFORE send so audit log + email subject
    # reflect the actual cadence number (1st reminder -> count=1, 2nd -> 2).
    # Falls back to the params.reminder_count for tests that pre-set it +
    # for legacy callers that don't have storage-side reminder_count.
    new_count = _record_reminder_attempt(deps, delivery_item_id)
    reminder_count = new_count if new_count is not None else params.get("reminder_count", 1)

    message_id = None
    if channel == "email" and deps.email_sender is not None and recipients:
        try:
            message_id = _send_email(
                deps,
                to=recipients,
                subject=f"[HILDA] Reminder #{reminder_count} -- BATCH-{event_context.get('correlation_id', '')[:8]}",
                body_marker=f"send_reminder: template={template} count={reminder_count}",
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("send_reminder email send failed: %s", type(e).__name__)

    deps.audit.write_communication_log(
        action_type="send_reminder",
        delivery_item_id=delivery_item_id,
        attribution={
            "trigger_source": event_context.get("trigger_source", "automated"),
            "correlation_id": event_context.get("correlation_id", ""),
            "modified_by":    "system",
        },
        details={
            "template":       template,
            "channel":        channel,
            "recipient":      recipient,
            "reminder_count": reminder_count,
            "milestone_id":   event_context.get("milestone_id"),
            "message_id":     message_id,
            "send_skipped":   message_id is None,
        },
    )
    return {
        "template":       template,
        "channel":        channel,
        "reminder_count": reminder_count,
        "recipient":      recipient,
        "message_id":     message_id,
        "outcome":        "sent" if message_id else "audit_only",
    }


@hilda_celery_app.task(name="core.src.workflow_engine.tasks.outreach.notify_new_owner")
def notify_new_owner_task(
    params: dict[str, Any], event_context: dict[str, Any]
) -> dict[str, Any]:
    """NOTIFY_NEW_OWNER per FR-2 + FR-83 -- email new owner after reassignment.

    Fired by rule_engine when owner_corp_id (or related owner field) appears in
    field_deltas of an ItemModified TriggerEvent per sp_alert_parser 2026-06-27
    cascade.

    Collection-started gate (architect 2026-06-27): if the milestone has not
    yet had collection kicked off (item.delivery_state == "Not Started"), we
    DO NOT send the reassignment notice. Reasoning: an owner edited during
    setup is just data-entry; the new owner will receive the initial outreach
    naturally when the TPM later clicks Start Collection -- that fires
    kickoff_collection_on_milestone_started -> ItemCreated ->
    send_initial_outreach_on_collection_start, which reads the (then-current)
    SP-side owner identity. Sending a reassignment notice now would be a
    confusing duplicate email.

    params:
      - template: str (default "owner_reassignment_notice")
      - channel:  str (default "email")

    event_context: standard + new owner identity already denormalized in
    event_context (owner_corp_usa_email / owner_corp_id).
    """
    deps = get_task_deps()
    template = params.get("template", "owner_reassignment_notice")
    channel = params.get("channel", "email")
    delivery_item_id = event_context.get("delivery_item_id")

    # ---- Collection-started gate ----
    # Per architect 2026-06-27: do not email new owner if collection hasn't
    # kicked off yet. Use item.delivery_state as the milestone-level proxy:
    # KICKOFF_COLLECTION transitions every tracker out of "Not Started" when
    # the TPM clicks Start Collection. While at least one item remains in
    # "Not Started", treat the milestone as pre-kickoff.
    item_snapshot: Any = None
    if delivery_item_id and deps.storage is not None:
        try:
            item_snapshot = deps.storage.get_delivery_item(delivery_item_id)
        except Exception as exc:  # noqa: BLE001
            _log.warning("notify_new_owner: snapshot fetch failed for item=%s: %s",
                         delivery_item_id, type(exc).__name__)
    pre_kickoff = (
        item_snapshot is None
        or getattr(item_snapshot, "delivery_state", None) in (None, "Not Started")
    )
    if pre_kickoff:
        _log.info(
            "notify_new_owner: collection not started for item=%s; deferring -- "
            "new owner will receive initial outreach when TPM kicks off collection",
            delivery_item_id,
        )
        deps.audit.write_communication_log(
            action_type="notify_new_owner",
            delivery_item_id=delivery_item_id,
            attribution={
                "trigger_source": event_context.get("trigger_source", "automated"),
                "correlation_id": event_context.get("correlation_id", ""),
                "modified_by":    "system",
            },
            details={
                "outcome":      "deferred_collection_not_started",
                "template":     template,
                "channel":      channel,
                "milestone_id": event_context.get("milestone_id"),
                "send_skipped": True,
            },
        )
        return {
            "template":   template,
            "channel":    channel,
            "recipient":  None,
            "message_id": None,
            "outcome":    "deferred_collection_not_started",
        }

    # OWNER-3 (2026-08-14): multi-owner reassignment notice -- ONE email to all.
    recipients = _resolve_recipients(deps, event_context, params)
    recipient = recipients[0] if recipients else None

    message_id = None
    if channel == "email" and deps.email_sender is not None and recipients:
        try:
            message_id = _send_email(
                deps,
                to=recipients,
                subject=f"[HILDA] You've been assigned a deliverable -- {event_context.get('item_title', '')}",
                body_marker=f"notify_new_owner: template={template}",
            )
        except Exception as e:  # noqa: BLE001
            _log.warning("notify_new_owner email send failed: %s", type(e).__name__)

    deps.audit.write_communication_log(
        action_type="notify_new_owner",
        delivery_item_id=delivery_item_id,
        attribution={
            "trigger_source": event_context.get("trigger_source", "automated"),
            "correlation_id": event_context.get("correlation_id", ""),
            "modified_by":    "system",
        },
        details={
            "template":     template,
            "channel":      channel,
            "recipient":    recipient,
            "milestone_id": event_context.get("milestone_id"),
            "message_id":   message_id,
            "send_skipped": message_id is None,
        },
    )
    return {
        "template":   template,
        "channel":    channel,
        "recipient":  recipient,
        "message_id": message_id,
        "outcome":    "sent" if message_id else "audit_only",
    }


# ---------------------------------------------------------------------------
# Internal -- single async-call helper bridging the email_service Protocol
# (async) to Celery's sync task body.
# ---------------------------------------------------------------------------


def _send_email(deps: Any, *, to: list[str] | str, subject: str, body_marker: str) -> str:
    """Sync-bridge to deps.email_sender.send(...). Returns Message-ID.

    OWNER-3 (2026-08-14): `to` accepts either a single string (backward compat
    for pre-migration callers) OR a list of strings (multi-owner outreach --
    all recipients in TO of ONE email; any owner can reply per architect
    direction). Single string is wrapped in a single-element list.

    Body composition Ph-1: minimal marker string. Real composer (Jinja2 templates
    + per-customer variables) lands when worker boot wires the full compose_*
    helpers from email_service per integration test cycle.
    """
    import asyncio

    to_list = [to] if isinstance(to, str) else list(to)
    coro = deps.email_sender.send(
        to=to_list,
        cc=[],
        subject=subject,
        body=body_marker,
    )
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            # Fallback: run in new loop -- Celery worker is sync.
            new_loop = asyncio.new_event_loop()
            try:
                return new_loop.run_until_complete(coro)
            finally:
                new_loop.close()
        return loop.run_until_complete(coro)
    except RuntimeError as exc:
        # RUNTIMEERR-1 (2026-08-08): only catch loop-lifecycle RuntimeErrors
        # (asyncio.get_event_loop() raising "no current event loop" on
        # Python 3.12+ / non-main threads; loop already closed). Any
        # RuntimeError raised BY the coroutine itself (e.g., adapter
        # auth-expiry, invalid state) must propagate -- re-awaiting the
        # already-consumed coro on a new loop would only replace the real
        # error with the confusing "cannot reuse already awaited coroutine".
        msg = str(exc).lower()
        if "no current event loop" not in msg and "event loop is closed" not in msg:
            raise
        new_loop = asyncio.new_event_loop()
        try:
            return new_loop.run_until_complete(coro)
        finally:
            new_loop.close()


# ---------------------------------------------------------------------------
# Bindings
# ---------------------------------------------------------------------------


register_task_binding(TaskBinding(
    action_kind=ActionKind.SEND_INITIAL_OUTREACH,
    celery_task=send_initial_outreach_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.SEND_REMINDER,
    celery_task=send_reminder_task,
    queue="default",
))
register_task_binding(TaskBinding(
    action_kind=ActionKind.NOTIFY_NEW_OWNER,
    celery_task=notify_new_owner_task,
    queue="default",
))
