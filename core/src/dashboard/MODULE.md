# Module: dashboard

> **Status:** Skeleton draft 2026-06-12 (Ph-1; promoted Batch 2 → Batch 1 architecture queue per `D-074` decision today). Sections curated; pending section-by-section user review during the architecture session that opens the `dashboard-v1` strand. Code implementation begins after MODULE.md is signed off + decisions captured.
>
> **Rollback log:**
> - **2026-06-12 (skeleton draft)** — initial MODULE.md created as part of the `dashboard-v1` strand seed; anchors `D-074` (Variant A SP↔HILDA integration — link-out architecture), `D-073` (SP UI engineer manually provisions SP lists), `[D-006]` (Kerberos auth), `[D-064]` (HILDA→SP REST writeback — unchanged; dashboard reads SP via sharepoint_integration but does not writeback itself), NFR-16 (HILDA-mediated download with AD auth), and serves FR-31 (admin overrides view), FR-56 (milestone soft-refresh), FR-57 (document enumeration), FR-58 (Confirmation no-doc-section), FR-59 (document section markup), FR-60 (review-results display), FR-61 (HILDA-mediated download). **OPEN ARCHITECTURAL DECISIONS** below — to be locked during architecture review pass; see `## Architectural decisions to lock`.

## Purpose

HTTP entry point for HILDA — the FastAPI app that backs `hilda-api`. Hosts:
- `GET /docs/<delivery_item_id>` — server-side-rendered HTML document section per FR-57 / FR-59 / FR-60 (consumed by SP web part link-out per `D-074`; never by SP-side JS).
- `GET /dl/<scoped_token>` — HILDA-mediated download endpoint per FR-61 / NFR-16; streams NSD-backed files with content-type-conditional `Content-Disposition`.
- `POST /milestone/<milestone_id>/refresh` — FR-56 soft-poll trigger; enqueues a `workflow_engine` task and returns 202 Accepted.
- `GET /admin/overrides` — FR-31 runtime-override admin view; renders `storage.list_active_overrides` output as HTML for PM/TPM visibility.
- *(Ph-2)* `POST /docs/<delivery_item_id>/upload` — FR-62 SP-UI document upload path; `POST /item/<delivery_item_id>/remind` — FR-65 ad-hoc reminder; FR-87 webhook receiver if `D-064`'s SP-alert email channel ever needs a fallback.

This module's response shape for `/docs/<id>` is the **internal contract** for HILDA's own Jinja renderer; per `D-074` it is NOT a public JSON API for SP-side consumption. SP UI engineer renders bare `<a href>` anchors to `/docs/<delivery_item_id>` and does NOT fetch this endpoint from JS.

Serves NFR-5 (UI confirmation gates — surfaced in the rendered HTML), NFR-16 (AD-authenticated mediated download). Anchors `D-006` (Kerberos/SPNEGO auth via reverse proxy), `D-064` (writeback path is `sharepoint_integration`'s — dashboard does not write back to SP directly), `D-073` (SP lists pre-exist; dashboard does not provision), `D-074` (Variant A — link-out + server-side HTML render).

## Public surface

### HTTP endpoints (FastAPI routes)

```python
# ---- Document enumeration / rendering (FR-57 / FR-59 / FR-60) ----
@app.get("/docs/{delivery_item_id}", response_class=HTMLResponse)
async def get_document_section(
    delivery_item_id: str,
    request: Request,
    accept: str | None = Header(None),
) -> HTMLResponse | JSONResponse:
    """FR-57 — Document section for a DeliveryItem. Default response: server-side
    rendered HTML (Jinja2 template) embedding short-lived `download_url` per row
    via `storage.make_download_token(file_hash, delivery_item_id)`. The HTML is
    intended for top-level browser navigation per `D-074` (SP renders link-out
    anchor; TPM clicks; browser opens new tab to this URL). Confirmation items
    (item_type=CONFIRMATION) render with NO document section per FR-58.

    Content negotiation: if `Accept: application/json`, returns the FR-57 JSON
    shape — `[{doc_type, doc_id_slug, rev_number, original_filename,
    download_url, parser_result, llm_review_findings, inferred_tg_name,
    nsd_path_type}]`. JSON path is for HILDA-internal admin tools, NOT for
    SP-side consumption (which gets HTML).

    Auth: Windows Integrated (Kerberos/SPNEGO) via Negotiate header on the
    upstream request, forwarded by corp reverse proxy. Caller principal is
    extracted via `core/src/auth/` middleware. Ph-1 grants access to any
    authenticated corp AD user per NFR-16 (per-DI ACL deferred per DEF-18).

    Raises: DSH-E001 if delivery_item_id not found; DSH-E003 on auth failure."""

@app.get("/dl/{scoped_token}")
async def download_file(scoped_token: str) -> StreamingResponse:
    """FR-61 — HILDA-mediated download. Calls `storage.resolve_download_token` →
    (file_hash, delivery_item_id, NSDPath); streams the file via
    `storage.read_file(NSDPath)`. Content-Disposition per type:
      - inline:    PDF, plain text, images (display in browser tab)
      - attachment: Office (xlsx/xls/xlsm/docx/doc/pptx/ppt) + other binary
    Token freshness: 300s TTL per FR-61; tokens generated at /docs/<id>
    HTML-render time and embedded in the response (`make_download_token`
    encodes (file_hash, delivery_item_id) + signature + expiry per storage
    MODULE.md Invariant 2026-06-12). Path-agnostic resolution per storage's
    DocumentItemAssociation.local_nsd_path — works regardless of FR-86 4-path
    state. Raises: STR-E007 → DSH-E002 (expired/invalid token; renders TPM-
    friendly "Link expired — please refresh" page, not a 500)."""

@app.post("/milestone/{milestone_id}/refresh", status_code=202)
async def request_milestone_refresh(milestone_id: str) -> dict:
    """FR-56 — Soft-poll trigger. Calls `workflow_engine.enqueue_soft_poll(milestone_id)`,
    returns 202 Accepted with task_id. Rate-limited per-milestone per FR-56
    (default 5 min; configurable via `dashboard.config`). Deduplicated: if a
    poll task for this milestone is already in-flight, returns 202 with the
    existing task_id (no new task enqueued). FR-56 also specifies a 10s
    status-poll endpoint — see `/milestone/{milestone_id}/refresh/status`
    below.

    Raises: DSH-E004 on rate-limit (429 response, not error)."""

@app.get("/milestone/{milestone_id}/refresh/status")
async def get_refresh_status(milestone_id: str) -> dict:
    """FR-56 — Returns the latest soft-poll task status for the milestone.
    Polled by SP web part at 10s intervals until task completion."""

# ---- Admin / ops views (FR-31) ----
@app.get("/admin/overrides", response_class=HTMLResponse)
async def list_overrides(
    scope: Scope | None = None,
    scope_id: str | None = None,
) -> HTMLResponse:
    """FR-31 — Admin view of active AutomationRuleOverride rows. Calls
    `storage.list_active_overrides(scope, scope_id)`, renders HTML table with
    set_by_pm_id attribution + expires_at. Read-only; no edit/delete UI in Ph-1
    (use `rule_engine` CLI for changes). PM/TPM-accessible; restricted to
    authenticated corp AD users per NFR-16."""

# ---- Configuration ----
class DashboardConfig(BaseModel):
    """Operational config — environment-switching values only.

    Loaded via 3-tier precedence: CLI > HILDA_DASHBOARD_<FIELD> env > config/dashboard.json > defaults.
    """
    bind_host: str = "0.0.0.0"
    bind_port: int = 8443
    reverse_proxy_origin: str   # e.g. "https://hilda-proxy.corp" — for log/audit + future CORS allowlist
    refresh_rate_limit_seconds: int = 300   # FR-56 default 5 min
    token_ttl_seconds: int = 300            # FR-61 default 300s
    static_files_dir: Path | None = None    # for serving CSS/images (Ph-1: minimal inline; Ph-2 may externalize)
    jinja_templates_dir: Path               # where the Jinja templates live (Ph-1: core/src/dashboard/templates/)

def get_dashboard_config() -> DashboardConfig: ...
```

### Jinja templates (canonical layout)

```
core/src/dashboard/templates/
  base.html                  # site chrome; HTML head + corp-style header
  doc_section.html           # FR-57 / FR-59 / FR-60 — document section per DI
  doc_row.html               # included from doc_section.html; one per document
  doc_row_staged.html        # variant for nsd_path_type ∈ {staged_*, unrouted} — surfaces FR-87 button info
  token_expired.html         # DSH-E002 friendly error
  admin_overrides.html       # FR-31 admin view
  status_partials/*.html     # /milestone/<id>/refresh/status JSON-or-HTML fragments
```

### Error codes (registered in `core/src/diagnostics/error_codes.py` per `[D-002]`)

```
DSH-E001  Delivery item not found
DSH-E002  Download token invalid or expired (renders friendly page; HTTP 410)
DSH-E003  Authentication failed (no Negotiate header / Kerberos validation failed; HTTP 401)
DSH-E004  Refresh rate limit hit (HTTP 429; not actually an error — informational)
DSH-W001  Reverse proxy did not forward Negotiate header (degraded mode warning; surface in /admin diagnostic)
DSH-W002  Static-asset cache miss (Ph-2 cold-cache warning)
```

## Invariants

- **All routes require authenticated corp AD identity** per NFR-16. The Kerberos/SPNEGO middleware extracts the principal from the upstream `Authorization: Negotiate` header (forwarded by corp reverse proxy). Anonymous requests get HTTP 401 with no body content. Ph-1 grants any authenticated corp AD user access to `/docs/<id>` + `/dl/<token>`; per-DI ACL deferred per DEF-18.
- **`/docs/<id>` is HTML by default; JSON only on `Accept: application/json`** — SP UI never gets JSON because per `D-074` SP renders bare link-out anchors and the browser does top-level navigation (which gets HTML). JSON is reserved for HILDA-internal admin tools.
- **Download tokens are never persisted by dashboard** — they're computed at HTML-render time via `storage.make_download_token(file_hash, delivery_item_id)`, embedded in the HTML response, and live in the TPM's browser tab for 300s. Token URLs never cross the SP↔HILDA boundary; SP never sees them.
- **Token resolution is path-agnostic** per storage MODULE.md Invariant 2026-06-12 — dashboard's `/dl/<token>` endpoint relies on `storage.resolve_download_token` doing the fresh NSD path lookup at request time. Stable across FR-87 step B + step C resolution; invalidated by FR-83 work-item reassignment + TTL expiry. DSH-E002 is the user-facing rendering of STR-E007.
- **Reverse proxy is trusted; client identity headers are NOT** — dashboard MUST validate Kerberos from the proxy-forwarded Negotiate, and MUST NOT trust client-supplied `X-Authenticated-User` / `X-User-Email` headers. Reverse-proxy origin allowlist on source IP enforced.
- **No writeback to SP from dashboard** — all SP state writes go through `sharepoint_integration` per `D-064`. Dashboard is read-side only (renders + downloads + admin views); the POST endpoints write to HILDA-local state (`workflow_engine.enqueue_soft_poll`), not to SP.
- **No NSD path leakage in responses** — token URLs are opaque; NSD paths never appear in HTML or JSON. The 4 FR-86 path types (`classified` / `staged_*` / `unrouted`) are surfaced as `nsd_path_type` badge labels, not raw paths.
- **Confirmation items render with NO document section** per FR-58 — `item_type=CONFIRMATION` short-circuits the document fetch + Jinja partial.
- **CORS allowlist is empty in Ph-1** — no cross-origin XHR consumers per `D-074`. Future JSON consumers (HILDA-internal admin tools) require an explicit allowlist add via `DashboardConfig.cors_origins` (not in Ph-1 config).
- **Server-side rendered HTML only — no SPA, no client-side framework** — Ph-1 + Ph-2; SPA reconsideration deferred to Ph-3+.
- **Error-code contract**: all module errors raised as `PipelineError` with `DSH-E001..` codes registered in `core/src/diagnostics/error_codes.py` per `[D-002]` + `[D-017]`. Compact reports (RPT/MET/QC) emitted per `[D-002]` use only counts, status flags, and bounded enum tokens — never file content or proprietary identifiers.

## Architectural decisions to lock (open — for review during architecture session)

These 6 decisions need to be locked during the architecture session that opens `dashboard-v1`. Capture as MODULE.md edits + (where decision-worthy) DECISIONS.md entries.

1. **HTML rendering engine** — Jinja2 (recommended; FastAPI-native, server-side only, minimal) vs alternative. Likely soft-flag — no D-XXX.
2. **Reverse-proxy identity-forwarding mechanism** — (a) Kerberos delegation: re-forward the Negotiate handshake to dashboard; (b) Proxy validates + sets `X-Authenticated-User` header with strict source-IP allowlist on dashboard. Choice depends on what corp reverse proxy supports. **Likely D-XXX** — affects auth implementation across all dashboard routes.
3. **Cross-cutting `core/src/auth/` module vs dashboard-local middleware** — Recommend separate `core/src/auth/` MODULE.md since future HTTP surfaces (FR-62 upload endpoint, FR-87 webhook receiver Ph-2) will need the same Kerberos validation. **Likely D-XXX** — affects future module boundaries.
4. **`/docs/<id>` content negotiation** — HTML only vs HTML + JSON via `Accept` header. Recommend HTML default + JSON if `Accept: application/json` (FastAPI handles cleanly; Ph-1 cost ~zero; future-proofs for HILDA-internal admin tools). Soft-flag.
5. **Token-expiry UX** — TPM lands on expired-token page: (a) auto-redirect back to `/docs/<delivery_item_id>` for fresh tokens; (b) "click here to refresh" link. Recommend (a) — better UX, no orphan state. Soft-flag.
6. **CORS allowlist convention** — Even though Ph-1 has no JSON consumers per `D-074`, document the convention so it doesn't get pattern-broken by a future ad-hoc addition. Document as Invariant + `DashboardConfig.cors_origins` field default empty. Soft-flag.

## Key choices

- **Variant A per `D-074`** — server-side HTML rendering via Jinja2; SP UI engineer renders link-out anchors; browser top-level navigation; NOT SP-page-JS cross-origin XHR (which is blocked by corp policy in our environment).
- **Kerberos/SPNEGO auth per `[D-006]`** — Windows Integrated Auth via corp reverse proxy; no custom token issuance; PM browsers auto-attach Kerberos tickets when `hilda-proxy.corp` is in their Local Intranet zone (group-policy-deployed).
- **NSD read via storage's `read_file(NSDPath)`** — dashboard does NOT have its own SMB / NSD-mount logic; reuses storage's already-mounted filesystem per storage's NSD-IO model (Invariant 2026-06-12 — host-mounted at `HILDA_NSD_MOUNT_ROOT`).
- **Per-DI ACL deferred per DEF-18 (Ph-3+)** — Ph-1 + Ph-2 = any authenticated corp AD user can fetch any DI's docs; matches NFR-16 framing.
- **Storage-canonical schema per `[D-046]`** — dashboard renders against Pydantic models owned by `core/src/storage/models.py`; never imports raw SQLAlchemy ORM.
- **No SPA / no client-side framework** in Ph-1/Ph-2 — server-side render only; reconsideration deferred to Ph-3+.
- **Jinja2 chosen** (vs Flask templates / Mako / React-SSR) — FastAPI-native via `fastapi.templating.Jinja2Templates`; minimal; no client runtime; matches "server-side rendered HTML only" Invariant.

## Non-goals

- **NOT an SP UI engineer surface** per `D-074` — SP UI engineer gets a server-rendered HTML page; not a JSON API contract for SP-side consumption.
- **NOT the SP REST writeback channel** — that's `sharepoint_integration`'s domain per `[D-064]`. Dashboard is read-side + download + admin-view only.
- **NOT the SP-alert email channel parser** — those come via email per `[D-047]`; `email_service.sp_alert_parser` handles them. Dashboard does not have an SP-alert inbound surface.
- **NOT a credential admin UI** — credential management is `credential_service`'s CLI surface per `[D-019]` / `[D-038]`. Dashboard's `/admin/overrides` is for rule overrides only.
- **NOT a per-DI ACL surface in Ph-1** — DEF-18 defers per-DI access control; Ph-1 trusts any authenticated corp AD user.
- **NOT a SP list provisioner** — that's SP UI engineer's manual ceremony per `D-073`.
- **NOT a SPA / client-side state engine** — server-side render fresh on every nav.
- **NOT the rule-engine evaluator** — rule_engine evaluates per `[D-022]`; dashboard only renders rule_engine's output (admin overrides view).

## Depends on

- `core/src/storage/` — `list_documents_for_milestone` / `get_documents_for_item` / `make_download_token` / `resolve_download_token` / `read_file(NSDPath)` / `list_active_overrides` / `list_associations_for_item` per storage's Public surface
- `core/src/workflow_engine/` — `enqueue_soft_poll(milestone_id)` for FR-56 POST endpoint
- `core/src/template_schema/` — for enum rendering (DeliveryState, DocType, ItemType, RoutingResolution) + `RuleScope` enum for admin overrides view
- *(NEW, candidate)* `core/src/auth/` — Kerberos/SPNEGO middleware shared by dashboard + future HTTP surfaces (FR-62 upload, FR-87 webhook receiver). Decision to split into separate MODULE.md is OPEN — see `## Architectural decisions to lock` item 3.
- `core/src/diagnostics/` — `DSH-*` error codes registered + RPT/MET/QC compact-report schemas

## Depended on by

- *(none yet in Ph-1)* — dashboard is a leaf module (UI layer). Test fixtures may import. Possible future Ph-2 consumers: an `ops_dashboard` extension showing HILDA health metrics (out of scope for Ph-1).

## Deferred

- **Per-DI ACL** per DEF-18 — Ph-3+; Ph-1/Ph-2 trusts any authenticated corp AD user
- **FR-62 SP UI document upload endpoint** — `POST /docs/<delivery_item_id>/upload`; Ph-2 (FR-62 is Ph-2)
- **FR-60 expandable revision history** — `GET /docs/<delivery_item_id>?all_revisions=true`; Ph-2
- **FR-65 ad-hoc reminder via dashboard** — `POST /item/<delivery_item_id>/remind`; Ph-2 if SP UI uses dashboard for it (FR-65 alternative: SP-side field write triggers HILDA via SP-alert per `[D-047]` — TBD whether dashboard needs this endpoint at all)
- **Iframe embedding variant per `D-074`** — Ph-2 polish IF TPMs report "new tab" UX is disruptive; would require `Content-Security-Policy: frame-ancestors https://sp2017.corp` response header configuration
- **Single Page Application** — Ph-3+ reconsideration; Ph-1/Ph-2 = server-side render only
- **Per-tenant theming / branding** — Ph-3+ (when HILDA hosts multiple customer deployments)
- **GraphQL endpoint** — Ph-3+ if HILDA-internal admin tools demand complex query composition (Ph-1/Ph-2 = REST JSON via Accept negotiation if needed)
- **Audit log endpoint** — Ph-2 (`GET /admin/audit?since=<datetime>` querying `storage.query_communications`)
- **Static asset CDN** — Ph-3+; Ph-1/Ph-2 serves inline + small static dir
- **Health-check / metrics endpoints** — `/health` + `/metrics` per Prometheus convention; defer decision to ops phase (likely lives here but may be a separate sub-module)

## Test interface per `[D-005]`

- CLI: `python -m core.src.dashboard.dashboard_cli --diagnostic` — emits compact RPT/MET report (routes registered, recent error counts, token-resolve latency p50/p95, template-render error counts) per `[D-002]`.
- Mock harness: `python -m core.src.dashboard.dashboard_cli --serve --mock` — runs the FastAPI app against a mock storage (in-memory `DocumentIndexRow` fixtures + mock NSD with seed files) without requiring real Postgres / NSD mount. Useful for SP UI engineer's smoke testing the link-out flow.
- Pytest: `core/tests/test_dashboard.py` — Starlette `httpx.TestClient` against the app; mocks Kerberos middleware via dependency-injection seam (DI principal pre-set in test fixture). Covers FR-57 HTML render, FR-61 token resolve, FR-56 enqueue, FR-31 admin view, FR-58 confirmation no-doc-section, expired-token UX.

<!-- BEGIN:STRUCTURE -->
[DRAFT] No code present yet (only this MODULE.md skeleton) — architecture-phase doc-first design intent. Structure regeneration skipped per regen-map spec; will populate from code on first `/switch-phase development` pass.
<!-- END:STRUCTURE -->
