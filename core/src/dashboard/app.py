"""FastAPI application for HILDA dashboard.

Routes per dashboard MODULE.md Public surface (post 2026-06-23 cascade):
- GET  /docs/{delivery_item_id}              -- FR-57 document section (HTML or JSON via Accept)
- GET  /dl/{scoped_token}                    -- FR-61 HILDA-mediated download
- POST /milestone/{milestone_id}/refresh     -- FR-56 soft-poll trigger
- GET  /milestone/{milestone_id}/refresh/status -- FR-56 status check
- GET  /admin/overrides                      -- FR-31 admin view (Ph-1 empty per D1 cascade)

Variant A per D-074: server-side HTML rendering via Jinja2; SP UI engineer
renders bare link-out anchors; browser top-level navigation. JSON only on
Accept: application/json.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Header, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from core.src.diagnostics import format_code
from core.src.storage import (
    get_documents_for_item,
    list_active_overrides,
    list_associations_for_item,
    make_download_token,
    read_file,
    resolve_download_token,
)
from core.src.template_schema import ItemType

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


def build_app(
    config: DashboardConfig | None = None,
    refresh_state: MilestoneRefreshState | None = None,
    dispatcher: Any = None,                       # workflow_engine.TriggerDispatcher; None ok for Ph-1 dev without broker
) -> FastAPI:
    """Construct the dashboard FastAPI app.

    `config`: DashboardConfig instance; defaults to DashboardConfig.from_sources().
    `refresh_state`: shared state for FR-56 rate limit + dedup; tests pass a fresh
        instance per test.
    `dispatcher`: optional workflow_engine.TriggerDispatcher for FR-56 dispatch.
        If None, refresh endpoint returns 503 (workflow_engine not wired yet).
    """
    cfg = config or DashboardConfig.from_sources()
    state = refresh_state or MilestoneRefreshState()
    templates = Jinja2Templates(directory=str(cfg.jinja_templates_dir))

    app = FastAPI(title="HILDA Dashboard", version="0.1.0")

    def _auth(request: Request) -> AuthPrincipal:
        return require_authenticated_principal(request, cfg)

    # ---- Document enumeration / rendering (FR-57 / FR-59 / FR-60) ----

    @app.get("/docs/{delivery_item_id}", response_class=HTMLResponse, response_model=None)
    async def get_document_section(
        delivery_item_id: str,
        request: Request,
        accept: str | None = Header(None),
        principal: AuthPrincipal = Depends(_auth),
    ):
        """FR-57 -- Document section. HTML default; JSON if Accept: application/json."""
        # Storage Protocol: get_documents_for_item returns list[DocumentIndexRow]
        try:
            docs = await get_documents_for_item(delivery_item_id)
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=format_code("DSH-E001", item_id=delivery_item_id),
            )

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
                # Per D7 cascade 2026-06-23: llm_review_findings is None in Ph-1 early drop
                # (review_required=false on all items per architect lock 2026-06-19 + llm
                # Ph-1 phasing per architect direction 2026-06-22 -- REVIEW_DOCUMENT is
                # Ph-1 next pass + runtime-dormant). Template renders placeholder when None.
                "parser_result":         getattr(doc, "parser_result", None),
                "llm_review_findings":   getattr(doc, "llm_review_findings", None),
                "inferred_tg_name":      getattr(assoc, "inferred_tg_name", None) if assoc else None,
                "nsd_path_type":         (assoc.nsd_path_type.value if assoc and hasattr(assoc.nsd_path_type, "value")
                                          else str(getattr(assoc, "nsd_path_type", "")) if assoc else None),
            })

        # JSON path -- HILDA-internal admin tools only
        if accept and "application/json" in accept.lower():
            return JSONResponse(content=rendered_docs)

        # HTML path -- FR-58 Confirmation skip per item_type="Confirmation" PascalCase
        # (per SP UI engineer lock 2026-06-23 + D2 cascade). The delivery item's
        # item_type is read via storage; if no item exists OR item_type=Confirmation,
        # render empty doc_section per FR-58.
        is_confirmation = False
        # Inspect first doc's owning item via list_associations and an out-of-band item_type
        # lookup -- production has a get_delivery_item helper; Ph-1 dashboard inspects
        # the first association's parent item shape if present.
        # For Ph-1: render empty section when no docs (typical for Confirmation items)
        # since the SP-side item_type isn't on DocumentIndexRow; production dashboard
        # would query storage.get_delivery_item(delivery_item_id) here.
        if not rendered_docs:
            is_confirmation = True   # Likely Confirmation; render empty per FR-58

        return templates.TemplateResponse(
            request=request,
            name="doc_section.html",
            context={
                "delivery_item_id": delivery_item_id,
                "docs":             rendered_docs,
                "is_confirmation":  is_confirmation,
                "principal":        principal,
                "confirmation_value": ItemType.CONFIRMATION.value,    # "Confirmation"
            },
        )

    # ---- Mediated download (FR-61) ----

    @app.get("/dl/{scoped_token}")
    async def download_file(scoped_token: str) -> StreamingResponse:
        """FR-61 -- HILDA-mediated download. Token resolves to (file_hash,
        delivery_item_id, NSDPath); streams file via storage.read_file.
        Returns DSH-E002 friendly page on expired/invalid token (HTTP 410)."""
        try:
            file_hash, delivery_item_id, nsd_path = await resolve_download_token(scoped_token)
        except Exception:
            # Render friendly expired-token page
            html = """
            <!DOCTYPE html>
            <html lang="en"><head><meta charset="utf-8"><title>Link expired</title></head>
            <body><h1>Link expired</h1>
            <p>This download link has expired or is invalid. Please return to the document section to refresh.</p>
            </body></html>
            """.strip()
            return HTMLResponse(content=html, status_code=status.HTTP_410_GONE)

        # Resolve filename from association/index
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
        # Rate limit / dedup check
        existing = state.get(milestone_id)
        if existing is not None and not state.can_dispatch(milestone_id, cfg.refresh_rate_limit_seconds):
            return {
                "milestone_id": milestone_id,
                "task_ids":     existing[1],
                "deduped":      True,
                "rate_limited": True,
            }

        # Without a workflow_engine.TriggerDispatcher injected, return 503
        if dispatcher is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Dispatcher not wired (workflow_engine TriggerDispatcher unavailable)",
            )

        # Construct the RefreshRequested TriggerEvent. Since rule_engine doesn't have
        # a RefreshRequested TriggerKind, we use STATE_CHANGE (no entity_ref state delta)
        # as a Ph-1 placeholder; concrete production wiring may introduce a dedicated
        # TriggerKind.MILESTONE_REFRESH when sp_alert_parser landed.
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
