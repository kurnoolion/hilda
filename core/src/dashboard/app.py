"""FastAPI application for HILDA dashboard.

Routes per dashboard MODULE.md Public surface (post 2026-06-26 cascade):
- GET  /docs/{customer_id}/{sp_id}            -- FR-57 document section (HTML or JSON via Accept)
- POST /docs/{customer_id}/{sp_id}/resolve_reassign      -- FR-87 step (A) TPM reassign
- POST /docs/{customer_id}/{sp_id}/resolve_doc_type      -- FR-87 step (B) TPM doc_type resolve
- GET  /dl/{scoped_token}                    -- FR-61 HILDA-mediated download
- POST /milestone/{milestone_id}/refresh     -- FR-56 soft-poll trigger
- GET  /milestone/{milestone_id}/refresh/status -- FR-56 status check
- GET  /admin/overrides                      -- FR-31 admin view (Ph-1 empty per D1 cascade)

Variant A per [D-074]: server-side HTML rendering via Jinja2; SP UI engineer
renders bare link-out anchors; browser top-level navigation. JSON only on
Accept: application/json.

2026-06-26 cascade gaps fixed (Gap 1/2/6/7/8 + bonus):
- Gap 1: URL pattern reshape /docs/<delivery_item_id> -> /docs/<customer_id>/<sp_id>
  per architect lock 2026-06-26 (sp_id IS SP's Id auto-counter PK; HILDA's
  delivery_item_id IS sp_id).
- Gap 2: FR-87 step (A) + step (B) POST endpoints landed (step C deferred Ph-2
  per [D-039] Step 2 + [D-122]).
- Gap 6: FR-58 Confirmation detection is now AUTHORITATIVE via item_type
  from freshly-fetched SP row (NOT the prior no-docs heuristic).
- Gap 7: Per-load SP READ via SpCrud.get_item per [D-074] (no caching Ph-1).
- Gap 8: build_app accepts sp_crud injection; falls back to mock_sp_rows in
  request.app.state for test/dev modes.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Body, Depends, FastAPI, HTTPException, Header, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

from core.src.diagnostics import format_code
from core.src.storage import (
    get_documents_for_item,
    list_active_overrides,
    list_associations_for_item,
    make_download_token,
    read_file,
    reassign_document_to_workitem,
    resolve_download_token,
    tpm_resolve_doc_type as storage_tpm_resolve_doc_type,
)
from core.src.template_schema import DocType, ItemType

from .auth import AuthPrincipal, require_authenticated_principal
from .config import DashboardConfig

__all__ = ["build_app", "MilestoneRefreshState"]


# Content-Disposition policy per FR-61
_INLINE_EXTENSIONS = {".pdf", ".txt", ".png", ".jpg", ".jpeg", ".gif"}
_INLINE_MIME = {
    ".pdf":  "application/pdf",
    ".txt":  "text/plain",
    ".png":  "image/png",
    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif":  "image/gif",
}


# [D-119] tpm_resolved_doc_type SP field is 4-value (DocType minus UNRESOLVED).
# Mirror the same set used by sp_alert_parser so the direct-POST channel
# enforces the identical validation per [D-122] FR-87 direct-POST architecture.
_TPM_RESOLVED_DOC_TYPE_VALID = frozenset({
    DocType.TEST_REPORT.value,
    DocType.TECH_REPORT.value,
    DocType.WAIVER.value,
    DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
})


# FR-87 POST body schemas per [D-122] direct-POST architecture.
class _Fr87ReassignBody(BaseModel):
    target_item_id: int
    file_hash:      str


class _Fr87ResolveDocTypeBody(BaseModel):
    file_hash:       str
    target_doc_type: str


class MilestoneRefreshState:
    """In-memory per-milestone refresh rate-limit + dedup state.

    Production deployments may swap for Redis-backed state if multi-instance
    hilda-api scaling lands Ph-3+; Ph-1/Ph-2 = single hilda-api instance per
    [D-021] so in-memory is sufficient.
    """

    def __init__(self) -> None:
        # milestone_id -> (last_dispatched_ts, task_ids)
        self._state: dict[str, tuple[float, list[str]]] = {}

    def can_dispatch(self, milestone_id: str, rate_limit_seconds: int) -> bool:
        entry = self._state.get(milestone_id)
        if entry is None:
            return True
        last_ts, _ = entry
        return (time.time() - last_ts) >= rate_limit_seconds

    def record(self, milestone_id: str, task_ids: list[str]) -> None:
        self._state[milestone_id] = (time.time(), task_ids)

    def get(self, milestone_id: str) -> tuple[float, list[str]] | None:
        return self._state.get(milestone_id)


def _content_disposition(filename: str) -> tuple[str, str]:
    """Returns (Content-Disposition value, mime_type) per FR-61 policy."""
    ext = Path(filename).suffix.lower()
    if ext in _INLINE_EXTENSIONS:
        return f'inline; filename="{filename}"', _INLINE_MIME.get(ext, "application/octet-stream")
    return f'attachment; filename="{filename}"', "application/octet-stream"


async def _fetch_sp_row(
    app: FastAPI,
    sp_crud: Any | None,
    customer_id: str,
    sp_id: int,
) -> dict[str, Any] | None:
    """Per-load SP READ per [D-074] Gap 7 (no caching Ph-1).

    Production path: sp_crud is wired; we call SpCrud.get_item with the SP Id.
    Test/dev path: sp_crud is None; we look up the row in
    request.app.state.mock_sp_rows (a dict keyed by (customer_id, sp_id)).
    """
    if sp_crud is not None:
        from core.src.sharepoint_integration.config import ListScope
        return await sp_crud.get_item(
            entity="delivery_items",
            scope=ListScope(customer_id=customer_id),
            item_id=sp_id,
        )
    # Test/dev path: mock_sp_rows on app.state. Missing state -> None (404).
    mock_rows: dict[tuple[str, int], dict[str, Any]] = getattr(
        app.state, "mock_sp_rows", {}
    )
    return mock_rows.get((customer_id, int(sp_id)))


def build_app(
    config: DashboardConfig | None = None,
    refresh_state: MilestoneRefreshState | None = None,
    dispatcher: Any = None,                       # workflow_engine.TriggerDispatcher
    sp_crud: Any | None = None,                   # SpCrud per Gap 8 (None = mock_sp_rows fallback)
    storage: Any | None = None,                   # StorageProtocol for Ph-1 dashboard lookup
) -> FastAPI:
    """Construct the dashboard FastAPI app.

    `config`: DashboardConfig instance; defaults to DashboardConfig.from_sources().
    `refresh_state`: shared state for FR-56 rate limit + dedup; tests pass a fresh
        instance per test.
    `dispatcher`: optional workflow_engine.TriggerDispatcher for FR-56 dispatch.
        If None, refresh endpoint returns 503 (workflow_engine not wired yet).
    `sp_crud`: optional SpCrud instance for per-load SP READ per Gap 8. If None,
        the /docs/<customer_id>/<sp_id> handler falls back to
        request.app.state.mock_sp_rows (dict keyed by (customer_id, sp_id)).
    `storage`: StorageProtocol per architect lock 2026-07-01 -- Ph-1 dashboard
        lookup goes local Postgres via get_by_customer_and_sp_id +
        list_documents_for_item_display, skipping per-load SP READ (which cost
        a roundtrip per page view in prior design). None falls back to
        request.app.state.mock_storage in test mode.
    """
    cfg = config or DashboardConfig.from_sources()
    state = refresh_state or MilestoneRefreshState()
    templates = Jinja2Templates(directory=str(cfg.jinja_templates_dir))

    # 2026-07-01 architect lock: doc_type humanizer for Ph-1 template display.
    # Turns snake_case enum values ("compliance_certification_release_notes")
    # into title-case labels ("Compliance Certification Release Notes").
    def _humanize_doc_type(value: str | None) -> str:
        if not value:
            return ""
        return str(value).replace("_", " ").title()
    templates.env.filters["humanize_doc_type"] = _humanize_doc_type

    app = FastAPI(title="HILDA Dashboard", version="0.1.0")
    # Stash sp_crud + storage on app.state so route handlers + tests can introspect.
    app.state.sp_crud = sp_crud
    app.state.storage = storage
    if not hasattr(app.state, "mock_sp_rows"):
        app.state.mock_sp_rows = {}
    if not hasattr(app.state, "mock_storage"):
        app.state.mock_storage = None

    # ---- Liveness probe for container orchestration ----
    @app.get("/healthz")
    async def healthz() -> dict:
        """Unauthenticated liveness probe used by podman/k8s healthchecks.

        Returns 200 OK if the FastAPI app is alive. Intentionally does NOT
        check downstream deps (DB/Redis/SP) -- those are observed via their
        own container healthchecks per deploy/docker-compose.yml.
        """
        return {"status": "ok", "service": "hilda-api"}

    def _auth(request: Request) -> AuthPrincipal:
        return require_authenticated_principal(request, cfg)

    # ---- Document enumeration / rendering (FR-57 / FR-59 / FR-60) ----

    @app.get(
        "/docs/{customer_id}/{sp_id}",
        response_class=HTMLResponse,
        response_model=None,
    )
    async def get_document_section(
        customer_id: str,
        sp_id: int,
        request: Request,
        accept: str | None = Header(None),
        principal: AuthPrincipal = Depends(_auth),
    ):
        """FR-57 -- Document section. HTML default; JSON if Accept: application/json.

        2026-07-01 architect lock supersedes prior [D-074] Variant A design:
          - sp_id is SP's Deliverables list row Id (int); HILDA's delivery_item_id
            is a composite string primary key -- the two are NOT the same integer.
          - Ph-1 (cfg.ph1_minimal=True): local Postgres lookup by (customer_id,
            sp_id); no per-load SP READ; render 3-column table (#, filename,
            humanized doc_type) via doc_section.html with FR-60/61/87 blocks
            gated {% if not cfg.ph1_minimal %}.
          - Ph-2 (cfg.ph1_minimal=False): retains the prior SP-READ + FR-60/61/87
            code path -- no template changes needed to flip.
        """
        # -------- Ph-1 branch: local Postgres, no SP READ --------
        if cfg.ph1_minimal:
            store = getattr(request.app.state, "storage", None) or getattr(
                request.app.state, "mock_storage", None,
            )
            if store is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="storage_not_wired",
                )
            item = store.get_by_customer_and_sp_id(customer_id, sp_id)
            if item is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"no delivery_item for {customer_id}/{sp_id}",
                )

            # Confirmation items: no document table; explicit placeholder message.
            item_type = getattr(item, "item_type", "")
            is_confirmation = item_type == ItemType.CONFIRMATION.value

            docs_ph1: list[dict[str, Any]] = []
            if not is_confirmation:
                try:
                    rows = store.list_documents_for_item_display(
                        getattr(item, "item_id", None)
                        or getattr(item, "delivery_item_id", None)
                        or "",
                    )
                except Exception:
                    rows = []
                docs_ph1 = [
                    {
                        "original_filename": r[0],
                        "doc_type":          r[1],
                        "ingested_at":       r[2],
                    }
                    for r in (rows or [])
                ]

            return templates.TemplateResponse(
                request=request,
                name="doc_section.html",
                context={
                    "cfg":                cfg,
                    "ph1_minimal":        True,
                    "customer_id":        customer_id,
                    "sp_id":              sp_id,
                    "item":               item,
                    "docs":               docs_ph1,
                    "is_confirmation":    is_confirmation,
                    "principal":          principal,
                    "confirmation_value": ItemType.CONFIRMATION.value,
                },
            )

        # -------- Ph-2 branch: original SP-READ + full FR-60/61/87 path --------
        # Gap 7: per-load SP READ via SpCrud per [D-074]
        sp_row = await _fetch_sp_row(app, sp_crud, customer_id, sp_id)
        if sp_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=format_code("DSH-E001", item_id=f"{customer_id}/{sp_id}"),
            )

        # delivery_item_id IS the SP Id per architect lock 2026-06-26
        delivery_item_id = str(sp_id)

        # Storage Protocol: get_documents_for_item returns list[DocumentIndexRow]
        try:
            docs = await get_documents_for_item(delivery_item_id)
        except Exception:
            docs = []

        # Resolve associations for nsd_path_type + inferred_tg_name surfacing
        try:
            assocs = await list_associations_for_item(delivery_item_id)
        except Exception:
            assocs = []
        assoc_by_hash = {a.file_hash: a for a in assocs}

        # Build per-doc render rows with token URLs
        rendered_docs = []
        for doc in docs:
            token = await make_download_token(doc.file_hash, delivery_item_id,
                                              ttl_seconds=cfg.token_ttl_seconds)
            assoc = assoc_by_hash.get(doc.file_hash)
            rendered_docs.append({
                "doc_type":              doc.doc_type.value if hasattr(doc.doc_type, "value") else str(doc.doc_type),
                "doc_id_slug":           doc.doc_id_slug,
                "rev_number":            doc.rev_number,
                "original_filename":     doc.original_filename,
                "download_url":          f"/dl/{token}",
                "parser_result":         getattr(doc, "parser_result", None),
                "llm_review_findings":   getattr(doc, "llm_review_findings", None),
                "inferred_tg_name":      getattr(assoc, "inferred_tg_name", None) if assoc else None,
                "nsd_path_type":         (assoc.nsd_path_type.value if assoc and hasattr(assoc.nsd_path_type, "value")
                                          else str(getattr(assoc, "nsd_path_type", "")) if assoc else None),
            })

        # JSON path -- HILDA-internal admin tools only
        if accept and "application/json" in accept.lower():
            return JSONResponse(content=rendered_docs)

        # Gap 6: FR-58 Confirmation detection is AUTHORITATIVE via item_type from
        # the freshly-fetched SP row (NOT the prior no-docs heuristic). When the
        # SP row's item_type == "Confirmation" (PascalCase per SP UI engineer lock
        # 2026-06-23 + dashboard D2 cascade) -> render no-doc-section template.
        sp_item_type = sp_row.get("item_type")
        is_confirmation = sp_item_type == ItemType.CONFIRMATION.value

        return templates.TemplateResponse(
            request=request,
            name="doc_section.html",
            context={
                "cfg":                cfg,
                "ph1_minimal":        False,
                "delivery_item_id":   delivery_item_id,
                "customer_id":        customer_id,
                "sp_id":              sp_id,
                "sp_row":             sp_row,
                "docs":               rendered_docs,
                "is_confirmation":    is_confirmation,
                "principal":          principal,
                "confirmation_value": ItemType.CONFIRMATION.value,
            },
        )

    # ---- FR-87 step (A) -- TPM reassign per [D-122] direct-POST architecture ----

    @app.post(
        "/docs/{customer_id}/{sp_id}/resolve_reassign",
        response_class=HTMLResponse,
        response_model=None,
    )
    async def fr87_step_a_reassign(
        customer_id: str,
        sp_id: int,
        request: Request,
        body: _Fr87ReassignBody = Body(...),
        principal: AuthPrincipal = Depends(_auth),
    ):
        target_item_id = body.target_item_id
        file_hash = body.file_hash
        """FR-87 step (A) -- TPM reassigns the document to a different work-item.

        Per [D-122] direct-POST architecture 2026-06-26 (was [D-047] SP-alert email
        round-trip). Body: target_item_id (int = SP Id of target) + file_hash.
        Validates target exists in the same customer's Deliverables list.
        Writes via storage.reassign_document_to_workitem + SP audit field
        tpm_reassignment_target_item_id via SpCrud per [D-064] + [D-117]
        digest dance. Returns 303 redirect to the GET page.

        A->B->C strict ordering enforced at storage layer: storage raises STR-E009
        if the item's nsd_path_type is not in {staged_not_classified,
        staged_not_revision, unrouted}. We surface that as HTTP 409 Conflict.
        """
        # Validate source SP row exists
        sp_row = await _fetch_sp_row(app, sp_crud, customer_id, sp_id)
        if sp_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=format_code("DSH-E001", item_id=f"{customer_id}/{sp_id}"),
            )
        # Validate target SP row exists in the same customer's Deliverables list
        target_row = await _fetch_sp_row(app, sp_crud, customer_id, target_item_id)
        if target_row is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"target item not in customer's Deliverables list: "
                       f"{customer_id}/{target_item_id}",
            )

        source_id = str(sp_id)
        target_id = str(target_item_id)
        pm_id = principal.user_name

        # Caller-resolves discipline: dashboard passes target identity from the
        # freshly-fetched target_row (4-field owner identity + tg_name + plm_id).
        try:
            await reassign_document_to_workitem(
                file_hash=file_hash,
                source_delivery_item_id=source_id,
                target_delivery_item_id=target_id,
                pm_id=pm_id,
                target_tg_name=target_row.get("tg_name"),
                target_owner_corp_id=target_row.get("owner_corp_id", ""),
                target_owner_corp_usa_email=target_row.get("owner_corp_usa_email"),
                target_owner_corp_email=target_row.get("owner_corp_email"),
                target_owner_name=target_row.get("owner_name"),
                target_plm_id=target_row.get("plm_id"),
            )
        except Exception as exc:
            # Surface STR-E009 (state mismatch) as 409 Conflict per A->B->C ordering
            msg = str(exc)
            if "STR-E009" in msg or "state" in msg.lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"FR-87 step (A) rejected: item not in reassign-eligible state ({msg})",
                )
            raise

        # SP audit writeback per [D-064] + [D-117] digest dance.
        # Best-effort: when sp_crud is wired, write tpm_reassignment_target_item_id.
        if sp_crud is not None:
            from core.src.sharepoint_integration.config import ListScope
            try:
                await sp_crud.update_item(
                    entity="delivery_items",
                    scope=ListScope(customer_id=customer_id),
                    item_id=str(sp_id),
                    canonical_fields={"tpm_reassignment_target_item_id": target_item_id},
                )
            except Exception:
                # SP audit writeback is best-effort; storage state already committed.
                pass

        return RedirectResponse(
            url=f"/docs/{customer_id}/{sp_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # ---- FR-87 step (B) -- TPM doc_type resolve per [D-122] ----

    @app.post(
        "/docs/{customer_id}/{sp_id}/resolve_doc_type",
        response_class=HTMLResponse,
        response_model=None,
    )
    async def fr87_step_b_resolve_doc_type(
        customer_id: str,
        sp_id: int,
        request: Request,
        body: _Fr87ResolveDocTypeBody = Body(...),
        principal: AuthPrincipal = Depends(_auth),
    ):
        file_hash = body.file_hash
        target_doc_type = body.target_doc_type
        """FR-87 step (B) -- TPM resolves doc_type for a specific document.

        Per [D-122] direct-POST architecture 2026-06-26. Body: file_hash +
        target_doc_type. Validates target_doc_type in [D-119] 4-value set:
            {test_report, tech_report, waiver, compliance_certification_release_notes}
        (UNRESOLVED is NOT a valid TPM choice per [D-119]).

        A->B->C ordering: step B requires step A to be complete OR the item
        was never in staged_not_classified state (i.e. it was directly staged
        with a known target item). Storage's STR-E009 enforces the state check
        and is surfaced as HTTP 409 Conflict.
        """
        # Validate SP row exists
        sp_row = await _fetch_sp_row(app, sp_crud, customer_id, sp_id)
        if sp_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=format_code("DSH-E001", item_id=f"{customer_id}/{sp_id}"),
            )

        # [D-119] 4-value validation: reject UNRESOLVED + anything off-list.
        if target_doc_type not in _TPM_RESOLVED_DOC_TYPE_VALID:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"FR-87 step (B) rejected: target_doc_type='{target_doc_type}' "
                       f"not in [D-119] 4-value subset",
            )

        # A->B->C ordering check at handler level (defensive, complements storage):
        # step A is complete when sp_row.tpm_reassignment_target_item_id is set
        # OR the item was never staged_not_classified (which storage's STR-E009
        # also catches via state mismatch).
        # NOTE: this is a soft check -- the authoritative check is storage's
        # state validation. We pass the call through.

        delivery_item_id = str(sp_id)
        pm_id = principal.user_name

        try:
            await storage_tpm_resolve_doc_type(
                file_hash=file_hash,
                delivery_item_id=delivery_item_id,
                new_doc_type=DocType(target_doc_type),
                pm_id=pm_id,
            )
        except Exception as exc:
            msg = str(exc)
            if "STR-E009" in msg or "state" in msg.lower():
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=f"FR-87 step (B) rejected: item not in resolve-eligible state ({msg})",
                )
            raise

        # SP audit writeback per [D-064] best-effort
        if sp_crud is not None:
            from core.src.sharepoint_integration.config import ListScope
            try:
                await sp_crud.update_item(
                    entity="delivery_items",
                    scope=ListScope(customer_id=customer_id),
                    item_id=str(sp_id),
                    canonical_fields={"tpm_resolved_doc_type": target_doc_type},
                )
            except Exception:
                pass

        return RedirectResponse(
            url=f"/docs/{customer_id}/{sp_id}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    # NOTE: FR-87 step (C) revision resolution POST is Ph-2 per [D-039] Step 2
    # + architect direction. doc_row_staged.html shows step (C) as Ph-2 placeholder.

    # ---- Mediated download (FR-61) ----

    @app.get("/dl/{scoped_token}")
    async def download_file(scoped_token: str) -> StreamingResponse:
        """FR-61 -- HILDA-mediated download. Token resolves to (file_hash,
        delivery_item_id, NSDPath); streams file via storage.read_file.
        Returns DSH-E002 friendly page on expired/invalid token (HTTP 410)."""
        try:
            file_hash, delivery_item_id, nsd_path = await resolve_download_token(scoped_token)
        except Exception:
            html = """
            <!DOCTYPE html>
            <html lang="en"><head><meta charset="utf-8"><title>Link expired</title></head>
            <body><h1>Link expired</h1>
            <p>This download link has expired or is invalid. Please return to the document section to refresh.</p>
            </body></html>
            """.strip()
            return HTMLResponse(content=html, status_code=status.HTTP_410_GONE)

        try:
            docs = await get_documents_for_item(delivery_item_id)
            doc = next((d for d in docs if d.file_hash == file_hash), None)
            filename = doc.original_filename if doc else "download.bin"
        except Exception:
            filename = "download.bin"

        disposition, mime = _content_disposition(filename)

        async def _stream():
            async for chunk in read_file(nsd_path):
                yield chunk

        return StreamingResponse(
            _stream(),
            media_type=mime,
            headers={"Content-Disposition": disposition},
        )

    # ---- Milestone refresh (FR-56) ----

    @app.post("/milestone/{milestone_id}/refresh", status_code=status.HTTP_202_ACCEPTED)
    async def request_milestone_refresh(
        milestone_id: str,
        principal: AuthPrincipal = Depends(_auth),
    ) -> dict[str, Any]:
        """FR-56 -- Soft-poll trigger via workflow_engine.TriggerDispatcher.

        Per D3 cascade 2026-06-23: dispatcher constructs a RefreshRequested
        TriggerEvent (item-less, milestone-scoped). Rate-limited per FR-56;
        returns 202 with task_ids on dispatch or existing task_ids on dedup.
        """
        existing = state.get(milestone_id)
        if existing is not None and not state.can_dispatch(milestone_id, cfg.refresh_rate_limit_seconds):
            return {
                "milestone_id": milestone_id,
                "task_ids":     existing[1],
                "deduped":      True,
                "rate_limited": True,
            }

        if dispatcher is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Dispatcher not wired (workflow_engine TriggerDispatcher unavailable)",
            )

        from core.src.rule_engine import EntityRef, TriggerEvent, TriggerKind
        event = TriggerEvent(
            trigger=TriggerKind.STATE_CHANGE,
            sub_trigger=None,
            entity_ref=EntityRef(
                customer_id="unknown", milestone_id=milestone_id, delivery_item_id=None,
            ),
            field_deltas={},
            timestamp=datetime.now(timezone.utc),
            correlation_id=f"refresh-{milestone_id}-{int(time.time())}",
        )
        result = dispatcher.dispatch(event)
        state.record(milestone_id, result.scheduled_tasks)
        return {
            "milestone_id": milestone_id,
            "task_ids":     result.scheduled_tasks,
            "matched_count": result.matched_count,
            "deduped":      False,
        }

    @app.get("/milestone/{milestone_id}/refresh/status")
    async def get_refresh_status(
        milestone_id: str,
        principal: AuthPrincipal = Depends(_auth),
    ) -> dict[str, Any]:
        """FR-56 -- Returns the latest soft-poll task status for the milestone."""
        entry = state.get(milestone_id)
        if entry is None:
            return {"milestone_id": milestone_id, "status": "no_refresh_requested", "task_ids": []}
        last_ts, task_ids = entry
        return {
            "milestone_id": milestone_id,
            "status":       "dispatched",
            "task_ids":     task_ids,
            "last_dispatched_at": datetime.fromtimestamp(last_ts, timezone.utc).isoformat(),
        }

    # ---- Admin overrides (FR-31; Ph-1 empty per D1 cascade) ----

    @app.get("/admin/overrides", response_class=HTMLResponse)
    async def list_overrides_route(
        request: Request,
        scope: str | None = None,
        scope_id: str | None = None,
        principal: AuthPrincipal = Depends(_auth),
    ) -> HTMLResponse:
        """FR-31 admin view. Ph-1 per D1 cascade 2026-06-23: storage returns empty
        list (AutomationRuleOverride Postgres consumption deferred to Ph-2)."""
        from core.src.template_schema import RuleScope
        try:
            scope_enum = RuleScope(scope) if scope else None
        except ValueError:
            scope_enum = None
        try:
            overrides = await list_active_overrides(scope=scope_enum, scope_id=scope_id)
        except Exception:
            overrides = []
        return templates.TemplateResponse(
            request=request,
            name="admin_overrides.html",
            context={
                "overrides": overrides,
                "principal": principal,
                "ph1_note":  "No active overrides (Ph-1 -- AutomationRuleOverride Postgres consumption Ph-2 deferred per rule_engine D4 cascade)",
            },
        )

    return app
