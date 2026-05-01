# Status

**Active phase**: requirements
**Last updated**: 2026-05-01
**Last drift-check**: never (greenfield — no FR/NFR or MODULE.md baseline yet)

## Done

- 2026-04-30 Project initialized via `/project-init` (greenfield with design-input preflight; `HILDA_Design.md` imported into `docs/compact/design-inputs/`)
- 2026-04-30 D-001 captured (three-tier code organization: `core/` + `customizations/` + `config/`); `structure-conventions.md` updated to mirror `~/work/nora`'s layout (refs: D-001)
- 2026-04-30 D-002 captured (chat-mediated collaboration: stable error codes + compact RPT/MET/FIX/QC reports; no-proprietary-content hard invariant); `structure-conventions.md` Chat-mediated collaboration section added; PROJECT.md Constraints + all three phase prompts wired (refs: D-002)
- 2026-04-30 D-003 captured (adapter pattern for proprietary systems: intermediate primitives + on-prem code-generated adapters; dev LLM never reads proprietary specs); v1 issue tracking = Jira via IssueTracker Protocol; structure-conventions.md Protocol boundaries refined; PROJECT.md three-tier LLM access model added; phases/architecture.md + development.md wired with hard invariants (refs: D-003)
- 2026-04-30 D-004 captured (SharePoint integration split: standard API mechanics in core, deployment-specific config in customizations); structure-conventions.md + phases updated (refs: D-004)
- 2026-04-30 D-005 captured (every module independently testable through an appropriate interface: CLI for functional, mock web harness for UI; --mock/--dry-run for side-effect modules; --diagnostic emits compact reports per D-002); structure-conventions.md Module definition + Module doc schema updated; PROJECT.md Constraints + phases/architecture.md + development.md + requirements.md TODO comment wired (refs: D-005)
- 2026-04-30 D-006 captured (SharePoint REST API + on-prem AD auth, NTLM/Kerberos; supersedes Microsoft Graph references in HILDA_Design.md); resolves PROJECT.md Open question #1 (refs: D-006)
- 2026-04-30 D-007 captured (all LLM hosting on-premises — both runtime LLM and API Spec Ingestor LLM); resolves PROJECT.md Open question #2 (refs: D-007)
- 2026-04-30 D-008 captured (IssueTracker intermediate-primitive Protocol: async-native over sync proprietary APIs; ITR- error code prefix; full method surface + Pydantic data classes); structure-conventions.md sync-API wrapping convention added; resolves PROJECT.md Intermediate-primitive Protocol design Open question (refs: D-008)
- 2026-04-30 D-009 captured (Messenger intermediate-primitive Protocol: async-native over sync proprietary APIs; MSG- error code prefix; full method surface + Pydantic data classes); shares AttachmentInput with IssueTracker (refs: D-009)
- 2026-04-30 D-010 captured (Excel/Template Schema Ingestor — proprietary customer-template schemas processed on-prem; parallel pattern to D-003 API Spec Ingestor; dev LLM never reads proprietary template schemas); PROJECT.md three-tier LLM access model + Constraints updated; partially resolves customer-template-authoring Open question (refs: D-010)
- 2026-04-30 5 unresolved Open questions backlogged from PROJECT.md to STATUS.md Flags (eval-data channel; customer template authoring path normalization; browser-automation versioning; API Spec Ingestor input format; v1 messenger choice). New backlog entry added: Template Schema Ingestor input format (refs: D-010).
- 2026-05-01 `requirements.md` initial v1 draft populated from `HILDA_Design.md` + decisions D-001..D-010: 45 FR, 15 NFR, 13 Deferred items grouped by subsystem.
- 2026-05-01 Requirements redlines applied per session feedback: FR-9 (BATCH-id email consolidation), FR-12 (three-path inbound routing — structured block / `mailto:` tap-links / PM triage; idempotency on `(BATCH-id, item-index, status)`), FR-13 (shared network drive `\\share\hilda\...` supersedes SharePoint Document Libraries), FR-16/17 (parser + classifier driven by D-011), FR-18 (submission package read from NW drive), NFR-8 (SP scope = Lists + classic web parts only), NFR-12/13 (extended to D-011); new FR-46 (final|interim classification rule), FR-47 (failed-without-waiver dashboard surface), FR-48 (auto-create Waiver DeliveryItem; TPM is not waiver-path authority), NFR-16 (NW drive read/write boundary — `hilda-svc` writes only, HILDA-mediated reads only).
- 2026-05-01 D-011 captured (Test Report Document Profiler — proprietary historical test reports across Excel/Word/PDF processed on-prem; parallel pattern to D-003 + D-010; canonical classification rule owned in `core/src/test_report/`); structure-conventions.md Ingestor section generalized to "Ingestor / Profiler pattern — three modules"; PROJECT.md three-tier LLM access model updated; PMs / TPMs (Technical Project Managers) terminology clarified in Contributors table (refs: D-011).
- 2026-05-01 Reclassified runtime vs dev/test invariants per session feedback: FR-43 → NFR-17 (compact-reports invariant); FR-44 → NFR-18 (error-codes invariant); FR-45 split into FR-49 (runtime `--diagnostic` for ops + RPT emission) and NFR-19 (dev/test `--mock` / `--dry-run` / mock web harness); section "Audit & chat-mediated collaboration" renamed "Audit & runtime diagnostics".
- 2026-05-01 D-012 captured (Multi-item email status updates — three-path design: structured reply block / per-item `mailto:` tap-links / PM manual triage; idempotent on `(BATCH-id, item-index, status)`; outbound multipart/alternative, ASCII-only structured block; authority for FR-9, FR-12).
- 2026-05-01 D-013 captured (Shared network drive — single `hilda-svc` AD service account writes, HILDA-mediated reads via `https://hilda.corp/dl/<token>`, no per-customer AD groups in v1; path convention `\\share\hilda\<customer>\<device>\<milestone>\<deliverable>\<item>\` with `inbound/`/`outbound/`/`revisions/`; supersedes SharePoint Document Libraries; authority for FR-13, FR-17, FR-18, NFR-8, NFR-16).

## In progress

*(empty — add items as `- Item — started YYYY-MM-DD`)*

## Next

- Continue requirements iteration when user provides additional input. Current set is initial v1: 43 active FR + 19 NFR + 13 Deferred (3 struck-through reclassifications). User signaled more input may follow on 2026-05-01.
- Fill remaining `docs/compact/PROJECT.md` TODOs (one-line / Problem / Users / In scope v1 / Out of scope / Success criteria) on requirements continuation, sourced from `HILDA_Design.md` §1, §2, §13, §15.
- Open questions in `PROJECT.md`: all original entries resolved across sessions (SharePoint API → D-006; LLM hosting → D-007; intermediate-Protocol design → D-008+D-009; customer-template authoring partial → D-010). 5 sub-questions backlogged below in Flags. No active blocking questions.
- Ratify or supersede the reference-imported sub-conventions in `structure-conventions.md` during architecture: (a) cross-tier `core ↔ customizations` deps allowed in both directions; (b) one config file per module under `config/<module>.json` for install-time settings, runtime data stays out. Each warrants its own DECISIONS entry if confirmed unchanged or if revised.
- Decide where the central `error_codes.py` registry lives in HILDA's three-tier layout (likely a dedicated diagnostics / observability module under `core/src/`) — first architecture-phase decisions per `[D-002]`. Each module gets its 3-letter prefix as it is drafted.
- Resolve API Spec Ingestor input format (Open question #6) before Ingestor architecture: OpenAPI / Swagger vs. company-internal format vs. both. Affects `core/src/api_spec_ingestor/` design and the spec-format adapter sub-module.
- Decide v1 messenger target (Open question #7): proprietary internal messenger (would exercise the Ingestor end-to-end as part of v1 acceptance) or a public reference (Slack / Teams) first.
- Architecture-phase choice for the Test Report Profiler (`[D-011]`): on-prem PDF text-extraction path (`pdfplumber` / `pypdf` / `pymupdf`) and legacy `doc` handler (`antiword` / LibreOffice headless conversion).

## Flags

- `structure-conventions.md` references `~/work/nora` as the live reference for layout details (cross-tier deps, config rules, Protocol-boundary pattern). If nora's conventions evolve, HILDA's `structure-conventions.md` does NOT auto-track — it's a one-time import, captured in D-001's "Why". Re-verify alignment when reviewing the conventions file.

- **[BACKLOGGED 2026-04-30]** **Eval-data channel for runtime AI quality review** — how do PM corrections of AI assessments (tech-report quality reviews, customer-response drafts, message classifications) flow back into the system to improve checklists / prompts? No explicit pipeline in the design doc. Revisit when AI/LLM modules are designed in architecture phase, or when the first runtime LLM behavior needs tuning post-deployment.

- **[BACKLOGGED 2026-04-30]** **Customer template authoring path normalization** — PM team leads use both SharePoint UI (web part forms) and Microsoft Excel (template upload). Per `[D-010]`, the Excel template schema is generated by the Template Schema Ingestor. Sub-question: does the system normalize the SharePoint-UI authoring path and Excel-upload path into one canonical representation, or maintain two parallel ingestion paths feeding a common normalized schema? Revisit during architecture when designing `core/src/template_schema/` and the SharePoint-UI authoring web parts.

- **[BACKLOGGED 2026-04-30]** **Browser-automation customer adapters** — when a customer portal's HTML changes (no API available; using Playwright), do we ship versioned adapters with change-detection alerts, or accept manual failure → ticket? Revisit when designing customer adapters during architecture; out of v1 scope per `HILDA_Design.md` §13 Phase 4.

- **[BACKLOGGED 2026-04-30]** **API Spec Ingestor input format** `[D-003]` — what spec format(s) does the Ingestor accept? OpenAPI / Swagger? A company-internal format? Both? Recommendation: accept OpenAPI 3.x as canonical and provide a preprocessing pass (also LLM-driven, on-prem) that converts other formats into OpenAPI 3.x before the main code-generation pipeline. Revisit when designing `core/src/api_spec_ingestor/` in architecture phase.

- **[BACKLOGGED 2026-04-30]** **v1 messenger choice** `[D-009]` — issue-tracking v1 = Jira (public, hand-wireable). v1 messenger target: proprietary internal messenger (would exercise the API Spec Ingestor end-to-end as part of v1 acceptance) or a public reference (Slack / Teams) first? Revisit when scoping v1 milestones during requirements phase or early architecture.

- **[BACKLOGGED 2026-04-30]** **Template Schema Ingestor input format** `[D-010]` — Excel cell-layout convention for proprietary schema specs. Sub-question: standardized worksheet structure (e.g., one sheet per entity type with predictable column headers) vs. free-form Excel that the Ingestor's LLM extracts via inference? Resolve during architecture phase before starting `core/src/template_schema_ingestor/` implementation.
