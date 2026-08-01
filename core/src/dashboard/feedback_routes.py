"""feedback_routes.py -- /feedback/<customer>/<device>/<milestone> UI.

Ph-1 early-access feedback surface (5 TPMs) per architect ask 2026-07-30.
No auth (anyone with URL can view + submit; scoped to 5-user internal cohort);
no SP dependency; direct URL bookmark or share.

Routes:
  GET  /feedback/{customer}/{device}                     -> 302 to /DRR default
  GET  /feedback/{customer}/{device}/{milestone}         -> view + submit page
  POST /feedback/{customer}/{device}/{milestone}/submit  -> creates ticket, 303 back
  GET  /feedback/{customer}/{device}/{milestone}/attachment/{ticket_pk}
                                                         -> streams attachment blob

Submit form:
  - category dropdown: "bug" | "improvement"
  - bug_type dropdown: cascading; when category=bug shows the 9-phase optgroup
    list from config/feedback_bug_types.json; when category=improvement JS
    (or server-side rewrite) forces bug_type = OTHER-OTHER.
  - description textarea: mandatory when category=improvement.
  - attachment file input: optional, single file, 5MB cap.
  - target milestone dropdown: currently ["DRR"] (Ph-1 has one milestone;
    extend list as more milestones ship).

View list: newest-first by created_at, per-scope. Status column reflects
ops-managed lifecycle (open / in-process / closed).
"""
from __future__ import annotations

import logging
import os
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from core.src.dashboard.feedback_config import (
    CATEGORIES,
    CATEGORY_IMPROVEMENT,
    IMPROVEMENT_BUG_TYPE,
    grouped_bug_types,
    is_valid_bug_type,
)
from core.src.storage.feedback_ops import FeedbackStorage

__all__ = ["register_feedback_routes"]


async def _notify_bot_of_new_ticket(
    app: FastAPI, cfg: Any, ticket: Any,
) -> None:
    """Send a best-effort notification email to the BOT self mailbox
    announcing a new ticket. Silent no-op when email_sender or
    credential_service isn't wired (dev / test setups). Any failure is
    swallowed with a warning log -- ticket create already succeeded and
    email is a nudge, not a guarantee.
    """
    sender = getattr(app.state, "email_sender", None)
    cred_svc = getattr(app.state, "credential_service", None)
    if sender is None or cred_svc is None:
        _log.info(
            "feedback notify skipped for ticket_id=%s: email_sender or "
            "credential_service not wired",
            getattr(ticket, "ticket_id", "?"),
        )
        return
    try:
        # Recipient address resolution:
        # 1. HILDA_FEEDBACK_NOTIFY_TO env var if set (ops override; wins).
        # 2. Otherwise derive from EMAIL credential's username, stripping any
        #    "DOMAIN\" AD prefix (Windows Exchange stores the auth username as
        #    "SEA\OMADM_BOT" which is a valid login but NOT a valid RFC5322
        #    recipient address). If the sanitized value has no '@', we can't
        #    guess the domain safely -- log + skip.
        bot_addr = os.environ.get("HILDA_FEEDBACK_NOTIFY_TO", "").strip()
        if not bot_addr:
            cred = await cred_svc.get_credential(pm_id="ops", system_type="email")
            raw = (getattr(cred, "username", None) or "").strip()
            # Strip DOMAIN\ prefix if present (AD login format).
            bot_addr = raw.rsplit("\\", 1)[-1] if "\\" in raw else raw
        if not bot_addr:
            _log.warning(
                "feedback notify skipped for ticket_id=%s: no recipient "
                "address (HILDA_FEEDBACK_NOTIFY_TO unset + EMAIL credential "
                "username empty)",
                getattr(ticket, "ticket_id", "?"),
            )
            return
        if "@" not in bot_addr:
            _log.warning(
                "feedback notify skipped for ticket_id=%s: derived recipient "
                "'%s' has no '@' -- set HILDA_FEEDBACK_NOTIFY_TO to a full "
                "mailbox address like user@domain.tld",
                getattr(ticket, "ticket_id", "?"), bot_addr,
            )
            return
        subject = (
            f"[HILDA Feedback] {ticket.customer_id}/{ticket.device_id}/"
            f"{ticket.milestone_id} {ticket.ticket_id} -- {ticket.category}: "
            f"{ticket.bug_type}"
        )
        base_url = (getattr(cfg, "reverse_proxy_origin", "") or "").rstrip("/")
        view_url = (
            f"{base_url}/feedback/{ticket.customer_id}/{ticket.device_id}/"
            f"{ticket.milestone_id}"
        )
        att_line = (
            f"\nAttachment: {ticket.attachment_filename} "
            f"({ticket.attachment_size or 0} bytes)"
            if ticket.attachment_filename else ""
        )
        body = (
            f"A new feedback ticket has been submitted.\n\n"
            f"Ticket ID:  {ticket.ticket_id}\n"
            f"Scope:      {ticket.customer_id} / {ticket.device_id} / "
            f"{ticket.milestone_id}\n"
            f"Category:   {ticket.category}\n"
            f"Bug type:   {ticket.bug_type}\n"
            f"Status:     {ticket.status}\n"
            f"Created:    {ticket.created_at.isoformat() if ticket.created_at else '?'}"
            f"{att_line}\n\n"
            f"Description:\n{ticket.description or '(none)'}\n\n"
            f"View + manage: {view_url}\n"
        )
        await sender.send(
            to=[bot_addr], cc=[], subject=subject, body=body,
        )
        _log.info(
            "feedback notify sent for ticket_id=%s to %s",
            ticket.ticket_id, bot_addr,
        )
    except Exception as exc:  # noqa: BLE001 -- best-effort per architect ask
        _log.warning(
            "feedback notify FAILED for ticket_id=%s: %s: %s (ticket already "
            "committed; UI submit succeeded)",
            getattr(ticket, "ticket_id", "?"),
            type(exc).__name__, str(exc)[:200],
        )

_log = logging.getLogger(__name__)

_MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024   # 5 MB
_DEFAULT_MILESTONE = "DRR"

# Ph-1: hardcoded milestone dropdown -- MMK template.yaml declares only DRR.
# Extend this list as new milestones are added to the customer template. A
# Ph-2 pass can read from template_lookup._CACHE per (customer, device) but
# that requires template_lookup bootstrap in hilda-api which is currently
# only wired in hilda-worker.
_MILESTONE_DROPDOWN_OPTIONS: tuple[str, ...] = ("DRR",)


def register_feedback_routes(
    app: FastAPI,
    cfg: Any,
    templates: Jinja2Templates,
    feedback_storage: FeedbackStorage | None = None,
) -> None:
    """Wire /feedback/* routes onto the given FastAPI app.

    `feedback_storage`: injectable for tests; falls back to a fresh
    FeedbackStorage() (production hits the same Postgres engine as the rest
    of dashboard via the shared storage.db singleton)."""
    fs = feedback_storage or FeedbackStorage()
    # Stash on state so tests can introspect + override without app rebuild.
    app.state.feedback_storage = fs

    @app.get(
        "/feedback/{customer}/{device}",
        response_class=HTMLResponse,
        response_model=None,
    )
    async def redirect_to_default_milestone(customer: str, device: str):
        """Bare-scope URL redirects to the default milestone landing page."""
        return RedirectResponse(
            url=f"/feedback/{customer}/{device}/{_DEFAULT_MILESTONE}",
            status_code=status.HTTP_302_FOUND,
        )

    @app.get(
        "/feedback/{customer}/{device}/{milestone}",
        response_class=HTMLResponse,
        response_model=None,
    )
    async def view_feedback_page(
        customer: str, device: str, milestone: str, request: Request,
    ):
        """View + submit landing. Renders form (top) + tickets table (bottom)
        for the scope. No auth."""
        store: FeedbackStorage = request.app.state.feedback_storage
        try:
            tickets = store.list_tickets(
                customer_id=customer, device_id=device, milestone_id=milestone,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "feedback view list failed for %s/%s/%s: %s: %s",
                customer, device, milestone, type(exc).__name__, str(exc)[:120],
            )
            tickets = []
        return templates.TemplateResponse(
            request=request,
            name="feedback/page.html",
            context={
                "customer":             customer,
                "device":               device,
                "milestone":            milestone,
                "milestone_options":    list(_MILESTONE_DROPDOWN_OPTIONS),
                "categories":           list(CATEGORIES),
                "bug_types_grouped":    grouped_bug_types(),
                "improvement_bug_type": IMPROVEMENT_BUG_TYPE,
                "tickets":              tickets,
                "max_attachment_mb":    _MAX_ATTACHMENT_BYTES // (1024 * 1024),
            },
        )

    @app.post(
        "/feedback/{customer}/{device}/{milestone}/submit",
        response_class=HTMLResponse,
        response_model=None,
    )
    async def submit_feedback(
        customer: str, device: str, milestone: str, request: Request,
        category: str = Form(...),
        bug_type: str = Form(...),
        description: str = Form(""),
        target_milestone: str = Form(...),
        attachment: UploadFile | None = File(None),
    ):
        """Create a new ticket for the scope. Multipart form (attachment file).

        Category=improvement forces bug_type=OTHER-OTHER server-side (defensive;
        the form JS also does this) and requires a non-empty description.
        Attachment is optional; capped at 5MB.

        Redirects (303) to the view page for the submitted target_milestone
        (may differ from URL's milestone when the TPM changes the dropdown)."""
        # -- Validate category --
        if category not in CATEGORIES:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid category: {category!r}",
            )

        # -- Improvement always resolves to OTHER-OTHER; overwrite whatever
        # the form sent so a JS bug can't submit a garbage bug_type paired
        # with category=improvement. --
        if category == CATEGORY_IMPROVEMENT:
            bug_type = IMPROVEMENT_BUG_TYPE

        # -- Validate bug_type against the shipped registry --
        if not is_valid_bug_type(bug_type):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid bug_type: {bug_type!r}",
            )

        # -- Description required for improvement, optional for bug --
        description_clean = (description or "").strip() or None
        if category == CATEGORY_IMPROVEMENT and not description_clean:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="description is required when category=improvement",
            )

        # -- Validate target milestone against allowed list --
        if target_milestone not in _MILESTONE_DROPDOWN_OPTIONS:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid target_milestone: {target_milestone!r}",
            )

        # -- Attachment (optional, 5MB cap) --
        att_filename: str | None = None
        att_content_type: str | None = None
        att_bytes: bytes | None = None
        if attachment is not None and attachment.filename:
            data = await attachment.read()
            if len(data) > _MAX_ATTACHMENT_BYTES:
                raise HTTPException(
                    status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    detail=(
                        f"attachment exceeds {_MAX_ATTACHMENT_BYTES // (1024 * 1024)}MB "
                        f"limit (uploaded {len(data)} bytes)"
                    ),
                )
            if data:
                att_bytes = data
                att_filename = attachment.filename
                att_content_type = attachment.content_type or "application/octet-stream"

        store: FeedbackStorage = request.app.state.feedback_storage
        try:
            ticket = store.create_ticket(
                customer_id=customer,
                device_id=device,
                milestone_id=target_milestone,
                category=category,
                bug_type=bug_type,
                description=description_clean,
                attachment_filename=att_filename,
                attachment_content_type=att_content_type,
                attachment_bytes=att_bytes,
            )
        except Exception as exc:  # noqa: BLE001
            _log.error(
                "feedback submit failed for %s/%s/%s: %s: %s",
                customer, device, target_milestone,
                type(exc).__name__, str(exc)[:200],
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"failed to create ticket: {type(exc).__name__}",
            )

        # FB-5: best-effort notification to BOT self mailbox. Ticket already
        # committed to Postgres above -- email failure never fails the submit.
        await _notify_bot_of_new_ticket(request.app, cfg, ticket)

        return RedirectResponse(
            url=f"/feedback/{customer}/{device}/{target_milestone}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    @app.get(
        "/feedback/{customer}/{device}/{milestone}/attachment/{ticket_pk}",
        response_model=None,
    )
    async def download_attachment(
        customer: str, device: str, milestone: str, ticket_pk: int,
        request: Request,
    ):
        """Stream a ticket's attachment blob. 404 on missing ticket, missing
        attachment, or scope mismatch (defensive against URL enumeration --
        attachment_pk in a different customer/device/milestone URL returns
        404, not the wrong scope's data)."""
        store: FeedbackStorage = request.app.state.feedback_storage
        try:
            ticket = store.get_ticket(ticket_pk)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "feedback attachment lookup failed for pk=%s: %s: %s",
                ticket_pk, type(exc).__name__, str(exc)[:120],
            )
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="attachment lookup failed",
            )
        if ticket is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ticket_pk={ticket_pk} not found",
            )
        if not (
            ticket.customer_id == customer
            and ticket.device_id == device
            and ticket.milestone_id == milestone
        ):
            # Scope mismatch: don't leak which scope the ticket actually
            # belongs to; 404 as if the ticket didn't exist.
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ticket_pk={ticket_pk} not found in this scope",
            )
        if not ticket.attachment_bytes:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"ticket_pk={ticket_pk} has no attachment",
            )

        filename = ticket.attachment_filename or "attachment.bin"
        content_type = ticket.attachment_content_type or "application/octet-stream"

        def _iter():
            yield ticket.attachment_bytes

        return StreamingResponse(
            _iter(),
            media_type=content_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
