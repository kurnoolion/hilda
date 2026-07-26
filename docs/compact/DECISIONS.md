# Decisions

<!--
Template (keep entries tight — this file is always in context):

## D-XXX: Short title
**Status**: Active · **Date**: YYYY-MM-DD
**Decision**: What was chosen.
**Why**: Reason; rejected alternatives inline (vs X: ...).
**Consequences**: What this forces or rules out.
-->

> **Terminology mapping** (global; applies to all ADR bodies below): **"v1"** = **Ph-1/Ph-2** (current implementation phases per `[D-026]` Docker Compose on bare-metal Linux PC). **"v2"** = **Ph-3+** (deferred future per `[D-022]` / `[D-043]` MicroK8s + RabbitMQ Quorum Queues migration). Individual ADR implementation notes may further specify per-decision (search for "v1 → Ph" or "v2 → Ph" markers within ADR impl-note chains). **ADR Decision/Why/Consequences bodies are preserved verbatim per the append-only convention `[D-002]`**; impl notes carry the terminology evolution chain. The phase model (`[Ph-1]` Docker Compose lab-subnet; `[Ph-2]` adds Ph-1 + 2nd customer; `[Ph-3+]` MicroK8s + Vault + customer scale-out) is the durable terminology going forward — all new ADRs and FR amendments use `Ph-1` / `Ph-2` / `Ph-3+` exclusively.

---

## D-001: Three-tier code organization — `core/` + `customizations/` + `config/`
**Status**: Active · **Date**: 2026-04-30
**Decision**: `core/` = AI-generated source (`core/src/`, `core/tests/`). `customizations/` = AI-scaffolded code humans complete or own. `config/` = per-module settings.
**Why**: Makes the AI/human collaboration boundary explicit in the filesystem; lets `drift-check` / `regen-map` apply per-zone rules. Mirrors the proven layout from the reference implementation (a prior internal decision).
**Consequences**: All MODULE.md paths follow `core/src/<module>/MODULE.md` (core) or `customizations/<name>/MODULE.md` (customizations). CLI entrypoints invoked as `python -m core.src.<module>.<module>_cli`. In greenfield (current state — no code yet), this is convention-only; no reorg session needed. Public surface of a core module is computed from `core/src/<module>` symbols only (customizations expose their own MODULE.md). See `docs/compact/structure-conventions.md` for full layout. Cross-tier dependency semantics and the one-config-file-per-module rule are noted in `structure-conventions.md` as reference-imported from the reference implementation; ratify or supersede with dedicated DECISIONS entries during the architecture phase.

---

## D-002: Chat-mediated collaboration — stable error codes + compact reports
**Status**: Active · **Date**: 2026-04-30
**Decision**: (a) Every service/module failure emits a stable prefixed error code (e.g., `EML-`, `MSG-`, `ITR-`, `WFL-`, `CRD-`, `LLG-`, `SHP-`, `SUB-`, `AUD-`, `RUL-`, …; the canonical list is registered in a single `error_codes.py` module — exact path TBD during architecture, canonical reference at `the reference implementation/core/src/pipeline/error_codes.py`). Format `{MODULE}-{SEVERITY}{NUMBER}` (E=error, W=warning, 3-digit). Logs persist locally; raw logs never leave the on-prem environment. (b) Every cross-boundary artifact has a paired compact format — **RPT** (run / activity report), **MET** (metrics), **FIX** (corrections — what a PM changed), **QC** (quality check — fixed-field, numbers + Y/N). One record per line; **no proprietary content** (no fragments of customer test reports, tech reports, waivers, customer feedback, customer-system payloads, PM credentials, R&D reply text, or any document/email content). (c) QC templates are fixed-field — numbers + Y/N + bounded enum tokens; never free-prose summaries of proprietary content.
**Why**: The dev LLM cannot see production artifacts (NFR per Topic 5 / D-001 zoning). Compact pasteable reports + stable error codes are the only viable joint debugging surface across the air-gap. Mirrors the reference implementation's D-012 — proven pattern. No-proprietary-content is a hard invariant.
**Consequences**: Every new artifact type ships an error-code prefix + compact schema + QC template. `drift-check` and `close-session` hard-flag any artifact missing these. Authority for the remote-collaboration NFRs in `requirements.md`. Tests must include negative cases verifying no proprietary content leaks into RPT / MET / FIX / QC outputs or error messages. Per-module `<module>_cli.py` entrypoints expose a diagnostic mode that emits compact reports for the chat surface.

**Implementation note (2026-05-26 — registry path resolved; placeholder convention + Teacher/Student boundary cross-referenced)**: (1) The central `error_codes.py` registry path is now resolved to `core/src/diagnostics/error_codes.py` per `[D-017]` (Option A — standalone leaf node; 20 module prefixes pre-registered per the 2026-05-26 update to D-017). (2) The no-proprietary-content invariant is formalized as **11 redaction categories** in `.clinerules/02-content-safety.md` (CUST/DEV/PM/SPURL/LIST/COL/TMPL/TRPT/SYS/MIL/CSLUG). (3) Placeholder convention for proprietary identifiers in `customizations/` scaffolds documented at `cline-playbooks/placeholder-convention.md` (`<SYS_N>`, `<CUST_N>`, `<URL_N>`, etc.) — anchors this D-002 boundary. (4) Teacher↔Student LLM scaffold per `[D-027]` is the operational realization of the chat-mediated boundary: Claude (Teacher, Personal PC) sees only compact RPT/MET/FIX/QC; Cline (Student, Work PC) runs against real proprietary inputs.

---

## D-003: Adapter pattern for proprietary systems — intermediate primitives + on-prem code-generated adapters
**Status**: Active · **Date**: 2026-04-30
**Decision**: For each external system class HILDA integrates with (issue tracking, messenger, customer systems beyond the SharePoint frame), `core/` defines a typing.Protocol of intermediate primitives (e.g., `IssueTracker.open_issue / close_issue / add_comment / upload_file`; `Messenger.send_message / receive_message_as_rest`). Core code uses only the Protocol; concrete adapters bridge it to the actual REST API. Adapters for **public / popular** systems (e.g., Jira) live under `core/src/<system>/<vendor>_adapter.py`. Adapters for **proprietary** company-internal systems are produced by an on-prem **API Spec Ingestor** module (`core/src/api_spec_ingestor/`) that reads the proprietary API spec, runs an open-source LLM (e.g., Gemma3:12b, Qwen — configurable; never the dev LLM / Claude), and emits an adapter into `customizations/<system>/<proprietary>_adapter.py`. The Ingestor has its own diagnostic CLI per `[D-002]` emitting compact RPT / MET / QC reports the user can paste into chat to debug ingestion / generation issues without exposing the spec. **Hard invariant: the dev LLM (Claude) never reads proprietary API specs** — it sees only intermediate primitives, the Ingestor's compact diagnostic output, and the generated adapter code (which is reviewable customization-zone output).
**Why**: Proprietary specs are corporate IP. The dev LLM cannot legally see them. Decoupling via intermediate primitives + on-prem spec ingestion preserves Claude-assisted velocity for everything that's not the spec itself, while keeping spec material on-prem. Vs. single-LLM with redaction: too brittle and revealing — adapter shape itself can leak structure. Vs. fully hand-coded adapters: scales linearly with adapter count and gets stale on every spec revision.
**Consequences**: Three module classes per integration surface — (1) Protocol in `core/src/<system>/`, (2) public-vendor adapter (if v1 supports one) under `core/src/<system>/`, (3) proprietary-vendor adapter under `customizations/<system>/`, generated by the Ingestor. The Ingestor module is itself standard `core/src/` Python (its code is not proprietary; only its inputs are). Dev LLM may write, refactor, and test the Ingestor's orchestration logic, prompt scaffolding, output parsing, and diagnostic CLI. Dev LLM may NOT request, read, or summarize the proprietary spec files; this is a phase-prompt invariant in development.md. **v1 issue-tracking scope**: Jira adapter wired via the `IssueTracker` Protocol (Jira's spec is public; the Jira adapter is in `core/src/issue_tracker/jira_adapter.py`, optionally generated from the public Jira OpenAPI spec by the same Ingestor).
**Implementation note (2026-05-14)**: v1 issue-tracking scope updated — the proprietary corp PLM adapter is Ph-1 and is generated by the API Spec Ingestor in v1 alongside the proprietary messenger adapter (per `[D-016]`; DEF-6 promoted to Ph-1). The `IssueTracker` Protocol serves corp PLM (document storage, issue creation, polling) in Ph-1 and CustomerJIRA (customer-facing closure polling) in Ph-2. The `core/src/issue_tracker/jira_adapter.py` Jira adapter (public spec) remains in `core/` for the CustomerJIRA modality when it is built in Ph-2 per DEF-7.

**Implementation note (2026-05-26 — terminology clarification)**: "v1" in the Decision body and the 2026-05-14 impl note is the canonical predecessor of "Ph-1" — current terminology is **Ph-1/Ph-2/Ph-3+**. Phase references: `[D-031]` defers Excel-related Ingestor consumption to Ph-2; `[D-029]` impl note 2026-05-13 promotes DEF-1 to Ph-1. Corp PLM adapter accessed via `corp_plm_gateway` per `[D-021]` impl note 2026-05-24 (HILDA team-owned app on PLM gateway PC; corp net) — the `core/src/issue_tracker/` adapter is an in-process HTTP client of the gateway, not a direct corp-PLM client. Customer JIRA adapter accessed directly via outbound HILDA polling per FR-25 (rewritten 2026-05-20).

---

## D-004: SharePoint integration — standard API in `core/`, deployment-specific config in `customizations/`
**Status**: Active · **Date**: 2026-04-30
**Decision**: SharePoint integration uses the standard SharePoint API surface (likely SharePoint REST API + on-prem AD auth given the 2017-frozen constraint — final API choice resolved by Open question #1). The API mechanics live under `core/src/sharepoint_integration/` (auth, request signing, list CRUD, document library uploads, web-part data binding). Company-specific deployment information — site URLs, list internal names, lookup-column field IDs, custom column mappings, document library paths, classic web part wiring — lives under `customizations/sharepoint_config/` and is loaded at startup. Core never has SharePoint instance details hard-coded.
**Why**: SharePoint integration mechanics are stable and Claude-friendly; deployment specifics are per-environment and sensitive (internal site URLs, list IDs reveal corporate structure). Same air-gap principle as `[D-003]` — invariant code in `core/`, sensitive config in `customizations/`.
**Consequences**: `customizations/sharepoint_config/` defines a typed config schema (likely a Pydantic model in `core/`) and provides per-deployment values. Dev LLM may write the schema and the consumption code in `core/`; humans / on-prem ops fill the values in `customizations/`. The split parallels the issue-tracker / messenger pattern but does not need a Spec Ingestor — SharePoint's API surface is the standard, public Microsoft surface.

**Implementation note (2026-05-26 — document libraries removed; Open question #1 resolved)**: (1) "document library uploads" and "document library paths" are **removed from scope** per `[D-013]` (2026-05-01) and `[D-041]` (2026-05-17) — HILDA does not use SharePoint Document Libraries for artifact storage. All documents live on the NSD; access via the HILDA-mediated download endpoint per FR-61 / NFR-16. `sharepoint_integration` module owns SP REST API + List CRUD + web-part wiring only; NSD client is in the `storage` module per its 2026-05-26 MODULE.md draft. (2) The "Open question #1" reference (SharePoint API surface) is **resolved by `[D-006]`** (2026-04-30) — SP REST API + NTLM/Kerberos. (3) SP-side deployment configuration also includes the SP-alert subscriber address + "Anything changes" trigger setting per `[D-047]` (2026-05-24) — these live in `customizations/sharepoint_config/<deployment>.yaml` alongside list/column maps.

---

## D-005: Every module independently testable through an appropriate interface
**Status**: Active · **Date**: 2026-04-30
**Decision**: Every module in HILDA's workflow pipeline ships an interface that exercises it independently of the running system: (a) **functional modules** in `core/src/` (workflow engine activities, adapters, services, the API Spec Ingestor, etc.) ship `<module>_cli.py` with `main()` invokable as `python -m core.src.<module>.<module>_cli` (per `[D-001]` CLI convention); (b) **UI / web-facing modules** (SharePoint web parts, the dashboard data-binding code, any future internal-tooling web UI) ship a **mock web harness** — a local test server / `httpx.TestClient`-style harness that exercises the module against mock SharePoint List data without requiring the production SharePoint environment; (c) **side-effect-bearing modules** (Email Service, customer adapters, messenger, issue tracker) implement `--mock` / `--dry-run` modes so the CLI can be safely exercised against fixture data; (d) every test interface supports `--diagnostic` and emits compact RPT / MET / QC reports per `[D-002]`.
**Why**: The dev LLM cannot reach production. Independent testability is the only way to validate a module without orchestrating the whole system. Per-module CLIs let the dev / user paste a compact report into chat for joint debugging — closes the loop with `[D-002]`. Mock harnesses for UI modules avoid coupling test cycles to SharePoint 2017 availability and let UI behavior be exercised offline. Vs. unit-tests-only: unit tests don't catch wiring / IO / async-ordering bugs the CLI surfaces. Vs. integration-only-via-orchestrator: makes failures hard to attribute and slow to reproduce.
**Consequences**: Every MODULE.md curated section names its test interface (CLI command line + flags, or mock-harness entry point) under Public surface or in a dedicated paragraph. `drift-check` / `close-session` hard-flag any new `core/src/` module without a `<module>_cli.py` (or, for UI modules, without a documented mock harness). Adds dev cost per module — accepted as the price of joint-debug velocity. Anchors paired NFRs in `requirements.md`: independent CLI / mock-harness presence; `--mock` mode for side-effect modules; `--diagnostic` emits compact-report-compliant output. All test interfaces are themselves subject to `[D-002]`'s no-proprietary-content invariant.

**Implementation note (2026-05-26 — concrete mock harness identified)**: The "mock web harness" abstraction is realized concretely as the **`mock-sharepoint` Docker Compose service** running `core/src/sharepoint_integration/mock_server.py` — a FastAPI app exposing SP REST endpoints + an HTML browser UI over an in-memory store, landed 2026-05-05 with 22 contract tests. Other UI-facing modules follow the same pattern: `httpx.TestClient`-style harness against in-memory mocks. SP-interacting modules point at the mock via `HILDA_SP_SITE_URL=http://mock-sharepoint:8765` in the Docker Compose dev profile. The mock harness is **dev/test only** — never deployed to production.

---

## D-006: SharePoint integration uses REST API + on-prem AD auth (NTLM / Kerberos)
**Status**: Active · **Date**: 2026-04-30
**Decision**: `core/src/sharepoint_integration/` integrates with SharePoint via the **SharePoint REST API** and authenticates using **on-premises Active Directory** (NTLM / Kerberos against the AD-joined SharePoint server). Microsoft Graph API is **not** used. Resolves Open question #1.
**Why**: SharePoint version is frozen at 2017 (vanilla List views + classic web parts; SP Server 2016 / 2019 era). Microsoft Graph against on-prem SP is partial / unreliable in this era and assumes Azure AD identity, which on-prem deployments don't have. SharePoint REST API is fully supported on on-prem SP since 2013, exposes Lists / Document Libraries / web-part wiring, and integrates natively with on-prem AD via NTLM or Kerberos tickets. Vs. SOAP web services: REST is simpler, JSON-native, better Python tooling. Vs. CSOM: requires .NET interop.
**Consequences**: Python-side dependency: `requests-ntlm` (NTLM) and / or `requests-kerberos` (Kerberos) for auth; `httpx` or `requests` for transport. PM credential model (`[per project credential service to be defined in architecture]`) must support AD credentials (domain\user + password, or Kerberos ticket cache). The `HILDA_Design.md` references to "Microsoft Graph API" are superseded — `core/src/sharepoint_integration/` uses SharePoint REST API. Document this supersession in the relevant MODULE.md. SharePoint REST API rate limits and `$select` / `$filter` semantics shape how the integration module batches reads and writes.

**Implementation note (2026-05-26 — credential service path resolved; SP→HILDA HTTP firewall clarification)**: (1) The `[per project credential service to be defined in architecture]` placeholder is **resolved**: credential service is defined by `[D-019]` (2026-05-04 interface + 2026-05-24 impl note for Ph-1/Ph-2 mechanism) — Ph-1/Ph-2 uses sops-encrypted env files per `[D-038]` (single shared HILDA ops-team credential set per customer system); Ph-3+ uses HashiCorp Vault per DEF-14 / `[D-019]` v2 (per-PM credential blobs). The `get_credential(pm_id, system_type)` interface is preserved across phases. (2) The **HILDA → SP outbound** REST + NTLM/Kerberos pathway described here is unchanged. However, **SP → HILDA HTTP inbound is firewall-blocked unconditionally** per `[D-047]` (2026-05-24): the only SP → HILDA channel is the SP-alert email per `[D-047]`'s `sp_alert_parser` sub-module of `email_service`. SharePoint integration design must respect this directional asymmetry — outbound REST works fine; the SP-side cannot push to HILDA directly over HTTP.

---

**Implementation note (2026-06-14 — promoted from strand `dashboard-v1`)**: Corp environment standardizes on **NTLM** for both HILDA→SP REST and HILDA→NSD SMB authentication. The "NTLM / Kerberos" placeholder in this Decision's original text is replaced with NTLM-only. HILDA→SP REST uses `requests_ntlm.HttpNtlmAuth` (or equivalent NTLM credential injector) with `hilda-svc` AD service account credentials per `[D-019]` / `[D-038]` sops-encrypted env file discipline. HILDA→NSD SMB uses `cifs-utils` mount option `sec=ntlmssp` with `~/.smbcredentials` (username + password file mode 600, ops-provisioned). No Kerberos keytab + no krb5 ticket cache on HILDA PC — Kerberos infrastructure is not deployed in this environment. Browser→HILDA-proxy auth (per `[D-074]` Windows Integrated Auth) is separate and unaffected — that channel uses corp browser's native Negotiate (which may fall back to NTLM or use Kerberos depending on browser + GPO config; HILDA-side accepts both via the reverse proxy's auth handler). FR-2, FR-13, FR-73, FR-84, NFR-8, NFR-10 updated 2026-06-14 to reflect NTLM-only on HILDA→SP and HILDA→NSD paths. Anchors `[D-019]` (credential service discipline — NTLM credentials are HILDA-local secrets per `[D-038]` sops), `[D-038]` (sops-encrypted env file holds the NTLM username/password), `[D-074]` (browser→HILDA-proxy is separate auth scope; impl note doesn't cover it).

## D-007: All LLM hosting is on-premises — runtime AND code-generation
**Status**: Active · **Date**: 2026-04-30
**Decision**: Both LLM tiers in HILDA run on-premises:
1. **Runtime LLM** (DeliverableHub's internal LLM doing message classification, tech-report / waiver quality review, customer-response drafting, status summarization) — hosted on-prem (likely the same K8s cluster); accessed via the `LLMProvider` Protocol from `core/src/llm/`.
2. **API Spec Ingestor LLM** per `[D-003]` (the open-source LLM used to ingest proprietary specs and generate adapters) — also on-prem; configurable model (Gemma3:12b, Qwen, etc.).
No corp-proxy-to-public-cloud LLM is used. Resolves Open question #2.
**Why**: Data sensitivity policy. Test reports, tech reports, waivers, customer feedback, customer-system payloads, R&D reply prose, and proprietary API specs all contain corporate IP that cannot leave on-prem boundaries. Public-cloud LLM via corp proxy is theoretically possible but the policy boundary is cleaner if no LLM call ever leaves the network. Vs. corp-proxied public LLM: removes a class of accidental leaks via proxy misconfig. Vs. mixed model: simpler operational model — one hosting story for both LLMs.
**Consequences**: Hardware provisioning required for on-prem LLM serving (likely vLLM or Ollama on the K8s cluster, or dedicated GPU nodes). Model selection and inference quality become first-class capacity-planning concerns. Latency floor higher than public-cloud LLMs in some cases — workflow timeouts and Temporal activity heartbeats must be sized accordingly. Compatibility with the runtime LLM's quality requirements (classification accuracy, tech-report review utility) must be validated against the chosen on-prem model — capture as a follow-up architecture-phase concern. The `LLMProvider` Protocol and `LLMGateway` module gain a config knob for which on-prem model serves which task (smaller / faster for classification; larger / more capable for quality review and drafting). Cost model becomes capex (GPU hardware) + opex (electricity / cluster ops), not per-token.

**Implementation note (2026-05-26 — Ph-1/Ph-2 platform + Celery substitution)**: (1) "K8s cluster" references in the Decision body are stale for Ph-1/Ph-2 — actual platform is **Docker Compose on bare-metal Linux PC** per `[D-026]`; "K8s cluster" applies to Ph-3+ where MicroK8s lands per `[D-043]`. (2) "vLLM or Ollama on the K8s cluster, or dedicated GPU nodes" — Ph-1/Ph-2 reality: on-prem LLM accessed via the **`hilda-llm-gateway` Docker Compose container** per `[D-021]` (sole egress workload for both runtime inference and code-gen). Ph-3+: same container deployed as a MicroK8s Deployment. Model hosting location (GPU host, model server location, model-serving stack) is orthogonal to HILDA's orchestrator choice. (3) "Temporal activity heartbeats must be sized accordingly" — **Temporal is deferred** per `[D-022]` / `[D-043]`; actual workflow engine is **Celery + Redis broker (Ph-1/Ph-2) / Celery + RabbitMQ Quorum Queues (Ph-3+ per `[D-043]`)**. Replace "Temporal activity heartbeats" with **Celery task `soft_time_limit` / `time_limit`** and broker visibility timeout for retry semantics. (4) LLM hosting choice (corp-proxied vs on-prem model) remains decided as on-prem only by this Decision; no terminology change to that core invariant.

---

## D-008: `IssueTracker` intermediate-primitive Protocol — async surface over sync proprietary APIs
**Status**: Active · **Date**: 2026-04-30
**Decision**: `core/src/issue_tracker/` defines an async `IssueTracker` Protocol (`typing.Protocol`, structural) with the following surface (Python pseudo-signature; full Pydantic data classes accompany):

```python
class IssueTracker(Protocol):
    source_system: str  # adapter declares its tag, e.g., "jira-public", "proprietary-issue-tracker"

    async def create_issue(self, project: str, summary: str, description: str,
                           fields: dict[str, Any] | None = None,
                           attachments: list[AttachmentInput] | None = None,
                           idempotency_key: str | None = None,
                           timeout_s: float | None = None) -> IssueRef: ...
    async def get_issue(self, ref: IssueRef, timeout_s: float | None = None) -> Issue: ...
    async def update_issue(self, ref: IssueRef, updates: dict[str, Any],
                           timeout_s: float | None = None) -> None: ...
    async def transition_issue(self, ref: IssueRef, transition: str,
                               timeout_s: float | None = None) -> None: ...
    async def close_issue(self, ref: IssueRef, resolution: str,
                          timeout_s: float | None = None) -> None: ...
    async def add_comment(self, ref: IssueRef, body: str,
                          attachments: list[AttachmentInput] | None = None,
                          idempotency_key: str | None = None,
                          timeout_s: float | None = None) -> CommentRef: ...
    async def upload_attachment(self, ref: IssueRef, file: AttachmentInput,
                                timeout_s: float | None = None) -> AttachmentRef: ...
    async def delete_attachment(self, ref: IssueRef, attachment_ref: AttachmentRef,
                                timeout_s: float | None = None) -> None: ...  # added [D-040]/FR-67: PLM stale attachment deletion
    async def search(self, query: IssueQuery, timeout_s: float | None = None) -> AsyncIterator[IssueRef]: ...
    async def list_recent_changes(self, ref: IssueRef, since: datetime,
                                  timeout_s: float | None = None) -> AsyncIterator[IssueChange]: ...
    async def register_webhook(self, callback_url: str, events: list[str], secret: str,
                               timeout_s: float | None = None) -> WebhookRef: ...
    async def poll_changes(self, since: datetime,
                           timeout_s: float | None = None) -> AsyncIterator[IssueChange]: ...  # webhook-fallback
```

Data classes: `IssueRef`, `Issue`, `IssueChange`, `IssueQuery`, `CommentRef`, `AttachmentRef`, `AttachmentInput`, `WebhookRef` — all Pydantic; every reference type carries `source_system: str` for `CommunicationLog` routing. `AttachmentInput` accepts `Path | AsyncIterable[bytes]` to support streaming uploads. Error model: `IssueTrackerError(code, context, cause)` with prefix `ITR-` per `[D-002]`; mapped codes include `ITR-E001 unauthorized`, `ITR-E002 not_found`, `ITR-E003 conflict`, `ITR-W001 rate_limited`. Idempotency keys are required-when-applicable on all mutating methods; adapters dedupe at proprietary-API layer when supported, or in a local seen-key cache otherwise.
**Why**: Proprietary issue-tracker APIs are sync/blocking (per user input). HILDA's stack is FastAPI + asyncio + Temporal — async-native at the Protocol layer is mandatory; sync at this layer would force `asyncio.to_thread` calls into every caller. Adapters that wrap sync libraries do the wrapping internally per the sync-API wrapping convention in `structure-conventions.md`. UI-blocking is avoided because (a) async I/O lets the event loop service other requests while a single op runs in a thread, (b) any multi-second op is orchestrated by Temporal workflows — the UI awaits workflow completion via SharePoint List status updates, not a direct adapter call. Generic query DSL (`IssueQuery`) is a defined minimum subset (project, status, updated_after, assignee, labels) all adapters must support; richer queries best-effort.
**Consequences**: `core/src/issue_tracker/__init__.py` exports the Protocol + data classes. `core/src/issue_tracker/jira_adapter.py` is the v1 public-vendor implementation. `customizations/issue_tracker/<proprietary>_adapter.py` is Ingestor-generated per `[D-003]`. Cancellation contract is documented as best-effort (cancelling the awaiter cancels the asyncio task; the underlying sync thread runs to completion — Python cannot safely kill threads). `transition_issue`'s state vocabulary: `open`, `in_progress`, `blocked`, `resolved`, `closed`, plus `custom:<name>` escape hatch — adapters map to native workflow states. Webhook secret rotation is Deferred for v1. `core/src/issue_tracker/issue_tracker_cli.py` ships per `[D-005]` with `--diagnostic`, `--mock`, `--dry-run`. Error-code prefix `ITR-` registered in central `error_codes.py` per `[D-002]` (path TBD architecture).

**Implementation note (2026-05-26 — Ph-1 terminology, Celery substitution, registry path, gateway routing)**: (1) "v1" → **Ph-1** throughout. (2) "All long-running adapter calls go through Temporal workflows; the UI awaits workflow completion via SharePoint List status updates, not a direct adapter call" — **Temporal is deferred** per `[D-022]`; actual mechanism is **Celery tasks** dispatched via `workflow_engine`'s `@hilda_task` decorator. UI awaits via SP REST polling of SP list fields that HILDA writes back to as the task progresses (per `[D-047]` SP-alert channel + SYSTEM.md §3.1). The async-over-sync wrapping pattern (`asyncio.to_thread` for sync vendor libraries) is unchanged. (3) "central `error_codes.py` per `[D-002]` (path TBD architecture)" — resolved to `core/src/diagnostics/error_codes.py` per `[D-017]`; `ITR-` prefix pre-registered. (4) Corp PLM adapter is the proprietary IssueTracker implementation per `[D-003]` impl note 2026-05-14 (DEF-6 promoted to Ph-1). HILDA accesses corp PLM via the **`corp_plm_gateway`** application on the dedicated PLM gateway PC (corp net) per `[D-021]` impl note 2026-05-24 — `core/src/issue_tracker/` adapter is the in-process client of the gateway, not a direct corp-PLM client. The two-stage IO convention from `structure-conventions.md` applies. (5) `delete_attachment` method added later per `[D-040]` / FR-67 (PLM stale-attachment deletion) — inline comment in the Protocol surface above documents this addition. (6) "Webhook secret rotation Deferred for v1" — remains deferred; "v1" → "Ph-1/Ph-2".

---

## D-009: `Messenger` intermediate-primitive Protocol — async surface over sync proprietary APIs
**Status**: Active · **Date**: 2026-04-30
**Decision**: `core/src/messenger/` defines an async `Messenger` Protocol with the following surface:

```python
class Messenger(Protocol):
    source_system: str  # e.g., "slack", "teams", "proprietary-messenger"

    async def send_message(self, channel_or_user: str, text: str,
                           thread_id: str | None = None,
                           attachments: list[AttachmentInput] | None = None,
                           idempotency_key: str | None = None,
                           timeout_s: float | None = None) -> MessageRef: ...
    async def reply_in_thread(self, thread_id: str, text: str,
                              attachments: list[AttachmentInput] | None = None,
                              idempotency_key: str | None = None,
                              timeout_s: float | None = None) -> MessageRef: ...
    async def get_message(self, ref: MessageRef, timeout_s: float | None = None) -> Message: ...
    async def list_thread(self, thread_id: str, since: datetime | None = None,
                          timeout_s: float | None = None) -> AsyncIterator[Message]: ...
    async def list_recent(self, channel: str, since: datetime,
                          timeout_s: float | None = None) -> AsyncIterator[Message]: ...
    async def upload_file(self, channel_or_user: str, file: AttachmentInput,
                          comment: str | None = None,
                          idempotency_key: str | None = None,
                          timeout_s: float | None = None) -> FileRef: ...
    async def react(self, ref: MessageRef, emoji: str,
                    timeout_s: float | None = None) -> None: ...
    async def register_webhook(self, callback_url: str, secret: str,
                               timeout_s: float | None = None) -> WebhookRef: ...
    async def poll_inbound(self, since: datetime,
                           timeout_s: float | None = None) -> AsyncIterator[Message]: ...  # webhook-fallback
```

Data classes: `MessageRef`, `Message`, `FileRef`, `WebhookRef`, `AttachmentInput` (shared with `IssueTracker` per `[D-008]`) — Pydantic; `source_system: str` on every reference. Error model: `MessengerError(code, context, cause)` with prefix `MSG-` per `[D-002]`; codes include `MSG-E001 unauthorized`, `MSG-E002 channel_not_found`, `MSG-E003 message_too_large`, `MSG-W001 rate_limited`. Idempotency keys on all sending methods.
**Why**: Same async-over-sync rationale as `[D-008]`. Inbound channel symmetry — webhook + polling pair so adapters work whether the proprietary system pushes or only allows pull. Rule engine and `CommunicationLog` ingestion are Protocol-agnostic — they consume `AsyncIterator[Message]` whichever way it's produced.
**Consequences**: `core/src/messenger/__init__.py` exports Protocol + data classes. `core/src/messenger/messenger_cli.py` per `[D-005]`. `customizations/messenger/<proprietary>_adapter.py` is Ingestor-generated per `[D-003]`. v1 messenger choice (proprietary first vs. public reference first) remains an Open question in PROJECT.md. Error-code prefix `MSG-` registered in central `error_codes.py` per `[D-002]`. Webhook secret rotation Deferred for v1.

**Implementation note (2026-05-26 — messenger choice resolved, registry path, gateway routing)**: (1) "v1 messenger choice (proprietary first vs. public reference first) remains an Open question in PROJECT.md" — **resolved by `[D-016]`** (2026-05-04): Ph-1 ships **two adapters** — Slack (public, `core/src/messenger/slack_adapter.py`, via `slack_sdk`) + proprietary internal messenger (`customizations/messenger/<proprietary>_adapter.py`, Ingestor-generated per `[D-003]` impl note 2026-05-14). (2) "v1" → **Ph-1** throughout. (3) "central `error_codes.py` per `[D-002]`" — path resolved to `core/src/diagnostics/error_codes.py` per `[D-017]`; `MSG-` prefix pre-registered. (4) Corp messenger adapter accessed via the **`corp_messenger_gateway`** application on the reverse-proxy PC (corp net) per `[D-021]` impl note 2026-05-24 — `core/src/messenger/<proprietary>_adapter.py` is the in-process HTTP client of the gateway for the proprietary messenger. Slack adapter remains direct outbound (Slack Web API). (5) FR-54 (corp messenger inbound processing — runtime LLM classification + manual triage flag) is **Ph-2** per `[D-029]` impl note 2026-05-13. Ph-1 corp messenger use is outbound-only (escalation per FR-10). (6) "Webhook secret rotation Deferred for v1" — remains deferred; "v1" → "Ph-1/Ph-2".

---

## D-010: Excel / Template Schema Ingestor — proprietary customer-template schemas processed on-prem
**Status**: Active · **Date**: 2026-04-30
**Decision**: HILDA's customer deliverable templates (Devices → Milestones → Deliverables → DeliveryItems with customer-specific column structures, field extensions, validation rules, enumerated values, and customer-specific automation-rule parameters) carry **proprietary schema variations** that cannot be shared with the dev LLM. A new `core/src/template_schema_ingestor/` module — parallel pattern to the API Spec Ingestor `[D-003]` — runs on-premises, reads the proprietary Excel schema spec, runs an on-prem open-source LLM (Gemma3:12b / Qwen / configurable per `[D-007]`), and emits into `customizations/template_schemas/`: (a) Pydantic validators for each customer's template shape; (b) Excel parsers / column mappers for the customer's Excel template format; (c) SharePoint-List column-mapping configs feeding `[D-004]`'s `customizations/sharepoint_config/`; (d) customer-specific `AutomationRules` configurations consumed by the runtime rule engine. The Ingestor exposes a diagnostic CLI per `[D-002]` emitting compact RPT / MET / QC reports the dev pastes into chat to debug ingestion / generation issues without exposing the schema. **Hard invariant: the dev LLM (Claude) never reads proprietary customer-template schemas, never sees their content via tool calls, and does not request, summarize, or paraphrase their structure.** It works only with: the generic meta-schema (the standard Device / Milestone / Deliverable / DeliveryItem entity hierarchy from `HILDA_Design.md` §3 — public, in design-inputs); the Ingestor's compact diagnostic output (no schema content); and the generated artifacts already committed under `customizations/template_schemas/`.
**Why**: Customer-specific template schemas reveal customer process structure, customer-specific certification requirements, internal R&D taxonomy, and customer-specific automation triggers — all corporate IP. The pattern from `[D-003]` (intermediate primitives + on-prem code-generated artifacts) extends naturally: Claude works with the public meta-schema and writes the Ingestor; the Ingestor processes proprietary customer schemas on-prem and produces concrete artifacts in `customizations/`. Vs. asking PMs to manually write Pydantic validators per customer: doesn't scale, drifts from Excel templates, requires Python skills outside the PM team. Vs. embedding customer schemas in `core/`: violates the no-proprietary-content invariant and couples the codebase to per-customer corporate IP.
**Consequences**: Three module classes per template-customer integration — (1) generic meta-schema in `core/src/template_schema/` (public, defines the Device/Milestone/Deliverable/DeliveryItem entity types as Pydantic base models); (2) the Ingestor at `core/src/template_schema_ingestor/` (its code is not proprietary; only its inputs are); (3) per-customer concrete schema + validators + parsers + automation-rule configs at `customizations/template_schemas/<customer>/`, generated by the Ingestor. The runtime workflow engine (Temporal activities, rule engine, SharePoint integration) reads from `customizations/template_schemas/` at startup and parameterizes itself per active customer. The Ingestor's input format (Excel cell layout convention for the schema spec — column listings, enumerated values, validation rules, automation-rule overrides) is TBD architecture phase; document the convention in `core/src/template_schema_ingestor/MODULE.md`. `template_schema_ingestor_cli.py` ships per `[D-005]` with `--diagnostic`, `--mock`, `--dry-run`. Error-code prefix (likely `TSI-` for Template Schema Ingestor) registered in central `error_codes.py` per `[D-002]`. The `[D-003]` development.md invariant — dev LLM refuses to engage with proprietary spec content if pasted into chat — extends to proprietary template schema content (any customer-specific column listings, field extensions, automation-rule definitions). The Excel-import path mentioned by PM team leads in Topic 3 (template authoring via SharePoint UI + Excel) is fed by this Ingestor: PMs upload Excel templates whose structure conforms to the per-customer schema generated by the Ingestor at deployment time.

**Implementation note (2026-05-26 — Deliverable removed; Excel paths Ph-2; modes resolved; registry path; TG-group fields)**: (1) "Devices → Milestones → Deliverables → DeliveryItems" entity hierarchy is stale: **Deliverable level removed** per `[D-028]` (2026-05-12). Current canonical hierarchy: **Device → Milestone → DeliveryItem grouped by `tg_name`**. The generic meta-schema in `core/src/template_schema/` reflects this — `DeliverableBase` Pydantic model removed; `DeliveryItem` parents directly to `Milestone`. (2) Both Excel-based paths are **deferred to Ph-2** per `[D-031]` (FR-1 path b + FR-39 path b); the Template Schema Ingestor has no Ph-1 use case and is a Ph-2 module. DEF-15 (Excel import schema validation) anchors the deferral. (3) Ingestor input format **resolved by `[D-018]`** (three modes — schema-file YAML / row-offset + LLM column resolution / full LLM infer); the YAML schema descriptor format is normative in `core/src/template_schema_ingestor/MODULE.md`. (4) `error_codes.py` path resolved to `core/src/diagnostics/error_codes.py` per `[D-017]`; `TSI-` prefix pre-registered. (5) "Temporal activities" in the Consequences body → **Celery tasks** per `[D-022]`. (6) Customer template storage location remains **OPEN per `[D-042]`** (2026-05-21). (7) TG-group fields (`tg_owner_name`, `tg_owner_email`, `email_group_alias`, `corp_id_list`, `default_cc_list`) added at template-source level per FR-2 / FR-71 (2026-05-19 + 2026-05-22) — Ingestor output must emit per-TG metadata blocks (e.g., `tg_groups.yaml`) in addition to per-item structure.

---

## D-011: Test Report Document Profiler — proprietary historical reports processed on-prem
**Status**: Active · **Date**: 2026-05-01
**Decision**: A new on-prem module `core/src/test_report_profiler/` ingests historical proprietary test reports across mixed file types — Excel (`xlsx`, `xls`, `xlsm`, `csv`), Word (`doc`, `docx`), and PDF — and emits per-customer parsers + classification artifacts into `customizations/test_report_parsers/<customer>/`. The Profiler runs an open-source LLM on-prem (Gemma3:12b / Qwen / configurable per `[D-007]`) to extract: where the per-item status table lives in the report (sheet/range/headers for Excel; section/heading for Word; page region for PDF); the customer's status vocabulary mapped to the canonical enum `{passed, failed, non-applicable, waived, not-started}`; item-id and item-name conventions; waiver-reference detection conventions (column or comment-text pattern). Generated runtime artifacts are deterministic Python parsers (no runtime LLM): per-file-type parsers emitting `(item_id, status, [waiver_ref], [comment])` tuples plus a `final | interim` classifier. Canonical classification rule (owned in `core/src/test_report/`, not the Profiler): a report is **`final`** iff every item is in `{passed, non-applicable, waived}` AND every `failed` item carries a `waiver_ref` (which reclassifies it as `waived`); otherwise the report is **`interim`**. Waiver outcomes are out of scope for the classifier — they live in the separate Waiver DeliveryItem lifecycle; the TPM (Technical Project Manager) is not the final authority on waiver path resolution.
**Why**: Customer test-report formats are proprietary — their structure reveals customer process, certification taxonomy, and R&D classification. Same air-gap rationale as `[D-003]` and `[D-010]`. Vs. hand-written per-customer parsers: doesn't scale and drifts when customers update formats. Vs. one generic parser: formats vary too much (different file types, different status vocabularies, different waiver-marking conventions). Vs. runtime LLM classification: latency, determinism, and the on-prem-data invariant all push the LLM call to build time; runtime stays deterministic. Third instance of the on-prem Ingestor / Profiler pattern.
**Consequences**: Three module classes per customer test-report integration — (1) `core/src/test_report/` — generic classifier interface, canonical status enum, classification rule, runtime entry point; (2) `core/src/test_report_profiler/` — the Profiler (its code is `core/`-eligible; only its inputs are proprietary); (3) `customizations/test_report_parsers/<customer>/` — generated per-customer parsers + format adapters (one per file type) + customer-specific status-vocabulary maps. The Profiler ships `test_report_profiler_cli.py` per `[D-005]` with `--diagnostic`, `--mock`, `--dry-run`; compact RPT/MET/QC reports emit only counts (items profiled, status enum size, waiver-ref detection rate, format adapter dispatch counts) — no proprietary content. Error-code prefixes: `TRP-` (Profiler), `TRC-` (runtime classifier). Dev LLM never reads historical test reports — the development.md invariant from `[D-003]` / `[D-010]` extends to historical test reports; Claude refuses to engage with pasted test-report content. v1 input scope: all five Excel variants + Word `doc/docx` + PDF; one input adapter per file type within the Profiler. Per-file-type generated parsers land at `customizations/test_report_parsers/<customer>/<format>_parser.py`.

---

## D-012: Multi-item email status updates — three-path design with BATCH-id idempotency
**Status**: Active · **Date**: 2026-05-01
**Decision**: Outbound email outreach for delivery items consolidates all items owned by the same recipient into one message per round, identified by a stable `BATCH-<id>`. Inbound replies route to the correct DeliveryItems via three convergent paths, all keyed on the BATCH id: (a) **Structured reply block** in the body, anchored on machine-readable markers (`========== HILDA STATUS UPDATE ==========`, `BATCH: <id>`, `========== END HILDA UPDATE ==========`) that survive quote prefixes, mobile font / whitespace mangling, HTML stripping, and top-posting; owner edits status tokens (`OPEN` → `DONE | OPEN | DELAYED | BLOCKED`) and optional `comment[N]:` lines in place; parser is regex-only over extracted text. (b) **Per-item `mailto:` tap-links** at the bottom of the same email — `mailto:hilda-inbox@company.com?subject=[HILDA] BATCH-<id> ITEM-<n> <STATUS>`; tap pre-composes a tiny email; subject parser routes. (c) **PM manual triage** — free-text replies that match neither parser are recorded as comments on every item in the batch and surface a `Manual triage` flag on the PM dashboard. Status applies are idempotent on `(BATCH-id, item-index, status)`. Outbound is multipart/alternative (HTML + plaintext); the structured block in plaintext is ASCII-only. LLM-based inference for free-text replies is deferred (DEF-1).
**Why**: Recipients are external (different org / location), so no SharePoint web-form URL is reachable to them — status capture must work over email. Per-item emails are out (recipient spam). Single consolidated email + multi-item inline editing is the only viable shape. The hardest surface — disambiguating which item a free-text edit refers to — is sidestepped: structured paths give deterministic capture; free-text falls back to PM triage rather than ambiguous auto-update. Vs. machine-readable footer only (`ITEM-A=Done; ITEM-B=Open`): owners often don't preserve the format on reply (top-posting, mobile clients, quoting); empirically high error rate. Vs. structured-block only: no mobile-friendly shortcut. Vs. one email per item: spams recipients. Vs. LLM classification at runtime: pushed to v2 (DEF-1) to keep v1 deterministic and on-prem-only.
**Consequences**: Email Service module owns BATCH-id assignment, outbound template generation (HTML + plaintext alternatives), inbound parser with three-path dispatch, and idempotency cache keyed on `(BATCH-id, item-index, status)`. Rule engine outbound emits per-recipient batches, not per-item; reminder cadence is per-batch, not per-item. Dashboard owns the `Manual triage` surface for free-text replies. ASCII-only constraint on the structured block (no unicode arrows / box-drawing) — parser robustness > formatting polish. Subject format `[HILDA] BATCH-<id> — Status update needed: <N> items` preserved as a secondary anchor across `Re:` chains. Negative tests required: format-break tolerance (quote prefixes, whitespace collapse, HTML→text conversion), idempotency under duplicate replies, no-status-applied-on-mismatch under mangled BATCH ids. Authority for FR-9, FR-12 in `requirements.md`.
**Implementation note (2026-05-12)**: Path (b) — per-item `mailto:` tap-links — is Ph-2. Rationale: owners with multiple items reply once using path (a); tap-links are a convenience shortcut for single-item quick updates, not the primary workflow. Ph-1 implements paths (a) and (c) only; outbound email template omits tap-links in Ph-1. Path (b) design is unchanged — deferred, not removed.

**Implementation note (2026-05-26 — DEF-1 promoted to Ph-1; idempotency cache concrete location)**: (1) "LLM-based inference for free-text replies is deferred (DEF-1)" — **DEF-1 promoted to Ph-1** per `[D-029]` impl note 2026-05-13 + `[D-033]` + `[D-034]`. FR-12 path (c) is now rule-based parsing → runtime LLM fallback; when the email also carries attachments, a single fused LLM call covers both message classification and attachment routing per `[D-034]`. PM manual triage remains as the below-threshold fallback. (2) The BATCH-id idempotency cache is implemented in **Redis with TTL ≤24h** per `[D-022]` cache role + `core/src/storage/MODULE.md` `BatchIdempotencyKey` Pydantic model. Durable BATCH-id metadata (which BATCH-id was sent in which outbound email; recipients; items included) lives in `CommunicationLog` in Postgres — append-only audit per NFR-6. Two stores, two roles: Postgres for durability + audit; Redis for short-TTL idempotency. (3) Sender email attribution per `[D-036]` (2026-05-13): inbound `sender_email` is recorded in `CommunicationLog`; mismatch with the registered owner email for the BATCH-id is a soft `Sender mismatch` flag on the PM dashboard, not a hard rejection.

---

## D-017: Central diagnostics module at `core/src/diagnostics/` (Option A — standalone leaf node)
**Status**: Active · **Date**: 2026-05-04
**Decision**: A standalone `core/src/diagnostics/` module owns all cross-cutting observability contracts for HILDA: (a) central `error_codes.py` registry mapping every 3-letter module prefix + severity + number to a stable `ErrorCode`; (b) `report.py` — `ReportRecord` dataclass + `ReportWriter` for the four compact report types (RPT / MET / FIX / QC) the dev-LLM collaboration surface requires; (c) `qc.py` — `QCTemplate` base class enforcing fixed-field-only (int / float / bool / bounded enum) QC records with no free-text fields. Every other module imports from `diagnostics`; `diagnostics` imports nothing from HILDA (pure leaf node). All 18 module error-code prefixes are pre-registered in `error_codes.py` at first MODULE.md draft time.
**Why**: The `[D-002]` compact-reports invariant is cross-cutting — every module needs it from day one. Centralizing avoids per-module reinvention, prevents prefix collisions, and gives the dev a single pasteable `--diagnostic` output to inspect the full registry state. Option A (standalone) vs. Option B (inline per module / aggregator re-export): Option B defers the prefix-collision check to integration time and makes the registry non-discoverable without reading every module. Option A makes `diagnostics` the import root, creating a clean, cycle-free dependency leaf. Reference: `the reference implementation/core/src/pipeline/error_codes.py` and `report.py`.
**Consequences**: `diagnostics` is the first module drafted and the last to depend on anything else. Its `PREFIX_REGISTRY` dict is the canonical source of truth for all 3-letter prefixes — adding a new module = adding one entry here first, then in its own MODULE.md. The `QCTemplate` base class enforces the no-free-text invariant at the type level; any field declared as `str` (free-text) raises a `TypeError` at class definition time. `diagnostics_cli.py` (`--diagnostic`, `--validate`) is the first CLI in the project and a smoke-test that all prefixes are registered without collision.

**Implementation note (2026-05-26 — module count update)**: "All 18 module error-code prefixes" is now **20 modules** per `[D-021]` impl note 2026-05-24. Two new corp-side gateway modules added: **`corp_messenger_gateway`** (reverse-proxy PC; HILDA-team-owned; receives corp Slack webhooks; forwards to `hilda-api`) and **`corp_plm_gateway`** (PLM gateway PC; HILDA-team-owned; bridges HILDA outbound to corp PLM; relays PLM events back). Two prefixes need registration in `PREFIX_REGISTRY`: **`CMG-`** (corp_messenger_gateway) and **`CPG-`** (corp_plm_gateway). PREFIX_REGISTRY update is a one-line change per prefix; assignments + first error codes (e.g., `CMG-E001` unauthorized, `CPG-E001` gateway-unreachable) drafted alongside the corp-side MODULE.md files when those land (architecture-phase action item).

---

## D-014: Customer template authoring — two separate ingestion paths, TPM-selectable
**Status**: Active · **Date**: 2026-05-04
**Decision**: The system maintains two separately supported customer template authoring paths: (a) **SharePoint-UI path** — TPMs author and edit templates directly through SharePoint classic web-part forms (live editing, structured List fields per `[D-006]`); (b) **Excel upload path** — TPMs upload Microsoft Excel template files conforming to the per-customer schema generated by the Template Schema Ingestor `[D-010]`. TPMs choose between the two paths per workflow preference. The system does not normalize them into a single canonical authoring format. Both paths must produce identical internal data model representations.
**Why**: The two paths serve different workflow contexts — UI for iterative on-the-fly editing; Excel for bulk authoring, copy-paste from existing tools, and PM team familiarity with Excel-native workflows. Normalizing into one path would degrade whichever path is demoted. Vs. UI only: Excel upload is existing PM muscle memory; removing it creates adoption friction. Vs. Excel only: SharePoint UI is the primary daily edit surface; removing it reduces accessibility. Vs. normalizing internally: adds ingestion complexity for no runtime benefit — both paths already land in the same internal data model.
**Consequences**: Both paths must pass through the same Pydantic base models in `core/src/template_schema/`; the Template Schema Ingestor `[D-010]` defines the Excel schema; the SharePoint List schema defines the UI path's structure. FR-39 updated to reflect the two-path choice. `drift-check` should verify both paths produce equivalent model output. Resolves backlogged Flag "Customer template authoring path normalization" from `STATUS.md`.

**Implementation note (2026-05-26 — Excel path Ph-2; storage location open; TG-group fields)**: (1) **FR-39 path (b) Excel upload is deferred to Ph-2** per `[D-031]` (2026-05-12) + DEF-15. Ph-1 authoring path is the **SharePoint UI** (FR-39 path a) only. The two-path support described in the Decision body remains the design intent — Ph-1 ships path (a); path (b) lands at Ph-2 alongside the Template Schema Ingestor. (2) **Customer template storage location remains OPEN per `[D-042]`** (2026-05-21): YAML under `customizations/template_schemas/<customer_slug>/` (option A — version-controlled, ops/developer-edited) vs SharePoint List (option B — TPM self-edit). Leaning is option A for Ph-1/Ph-2; DEF-11 self-service wizard is the long-term TPM authoring path. Resolve before `template_schema_ingestor/MODULE.md` is drafted (Ph-2 module). (3) TG-group fields added to template structure per FR-2 / FR-71 (2026-05-19 + 2026-05-22) — both authoring paths must surface `tg_owner_name`, `tg_owner_email`, `email_group_alias`, `corp_id_list`, `default_cc_list` per `tg_name` group; storage in TGGroups SP list per `sharepoint/REQUIREMENTS.md §2.8`.

---

## D-015: API Spec Ingestor input format — OpenAPI 3.x canonical with preprocessing pass
**Status**: Active · **Date**: 2026-05-04
**Decision**: The API Spec Ingestor (`core/src/api_spec_ingestor/` per `[D-003]`) accepts **OpenAPI 3.x** as its canonical input format. Other formats (Swagger 2.x, company-internal formats, RAML, custom docs) are first converted to OpenAPI 3.x by an on-prem LLM-driven **preprocessing pass** (open-source model per `[D-007]`) before the main adapter-code-generation pipeline runs. The preprocessing pass is a sub-module within the Ingestor and emits a compact RPT per `[D-002]` indicating format detected and conversion confidence.
**Why**: OpenAPI 3.x is the widest-supported industry-standard spec format; using it as canonical keeps the main adapter-generation pipeline schema-stable. Vs. accepting all formats natively in the main pipeline: couples the generator to every input format variation. Vs. OpenAPI-only with no preprocessing: rejects valid company-internal specs the LLM can translate. Vs. Swagger 2.x as canonical: OpenAPI 3.x is a superset and the current industry standard.
**Consequences**: `core/src/api_spec_ingestor/` gains a `spec_normalizer.py` sub-module wrapping the preprocessing pass. CLI `--diagnostic` output reports original format detected and normalized OpenAPI 3.x doc size. Resolves backlogged Flag "API Spec Ingestor input format" from `STATUS.md`.

---

## D-016: v1 messenger targets — Slack (public reference) + proprietary internal messenger
**Status**: Active · **Date**: 2026-05-04
**Decision**: v1 ships two messenger adapters wired through the `Messenger` Protocol `[D-009]`: (a) **Slack** — adapter at `core/src/messenger/slack_adapter.py`, Slack Web API via `slack_sdk`; chosen as the public reference over Teams because setup is simpler (bot token + signing secret vs. Azure AD + M365 tenant), `slack_sdk.WebClient` is mockable without infrastructure, and unit tests carry no Azure dependency; (b) **proprietary internal messenger** — adapter at `customizations/messenger/<proprietary>_adapter.py`, generated by the API Spec Ingestor `[D-003]` as its first end-to-end production run in v1 (validates the Ingestor pipeline and the Protocol abstraction in one step). Both adapters implement the same `Messenger` Protocol; the rule engine and `CommunicationLog` are adapter-agnostic.
**Why**: Shipping both in v1 validates that the `Messenger` Protocol genuinely decouples adapters — the Ingestor-generated proprietary adapter must pass the same contract test suite as the hand-written Slack adapter. Including the proprietary adapter in v1 also exercises the API Spec Ingestor end-to-end, which was otherwise untested in v1. Vs. Slack only: defers the Ingestor's first real run to v2. Vs. proprietary only: harder to unit-test; no clean public reference for contract validation. Vs. Teams instead of Slack: Azure AD/M365 dependency adds setup friction for unit tests.
**Consequences**: `core/src/messenger/slack_adapter.py` — hand-written, `core/`-eligible, parallel to `jira_adapter.py`. Both adapters' test suites run against the same `Messenger` Protocol contract fixtures. DEF-5 and DEF-6 revisit triggers updated in `requirements.md`. Error-code prefix `MSG-` registered per `[D-002]`. FR-50 added to `requirements.md`. Resolves backlogged Flag "v1 messenger choice" from `STATUS.md`.

**Implementation note (2026-05-26 — terminology + corp messenger gateway routing)**: (1) "v1" → **Ph-1** throughout. (2) Slack adapter `core/src/messenger/slack_adapter.py` — direct outbound from `hilda-worker` to Slack Web API; no gateway. (3) Proprietary internal messenger adapter `customizations/messenger/<proprietary>_adapter.py` (Ingestor-generated per `[D-003]`) — **HILDA does not call the corp messenger directly**. The adapter is the in-process client of the **`corp_messenger_gateway`** application running on the reverse-proxy PC (corp net) per `[D-021]` impl note 2026-05-24. Outbound flow: `hilda-worker` → `corp_messenger_gateway` (corp net, IP/port whitelisted to receive corp Slack webhooks) → corp Slack. Inbound flow per FR-54 (Ph-2): corp Slack webhook → `corp_messenger_gateway` → forwards to `hilda-api/webhooks/messenger` over the lab subnet. (4) Both adapters pass the same `Messenger` Protocol contract test suite per `[D-005]` + `[D-009]`. (5) DEF-6 promoted to Ph-1 per `[D-003]` impl note 2026-05-14 — the corp PLM IssueTracker adapter is the second Ingestor-generated proprietary adapter (alongside the proprietary messenger), exercising the API Spec Ingestor end-to-end in v1.

---

## D-019: credential_service v1 — K8s Secrets / ops-provisioned; full Vault-backed implementation deferred to v2
**Status**: Active · **Date**: 2026-05-04
**Decision**: v1 `credential_service` is a thin K8s Secrets reader. Ops provisions one K8s Secret per PM per system type at deploy time; the module exposes a stable `get_credential(pm_id, system_type) -> Credential` interface identical to what v2 will expose. Credentials are never logged or written to disk. No PM self-service registration UI, no Vault integration, no OAuth2 refresh loop, no health monitor, no mTLS between callers in v1. FR-32–FR-38 deferred to v2 as DEF-14; FR-51 captures the v1 behaviour. NFR-3 (per-PM isolation) and NFR-4 (encryption at rest via etcd, TLS in transit) still apply at v1 level.
**Why**: Full Vault-backed PM credential management is significant infrastructure — Vault HA, mTLS, OAuth2 grant flows, health-monitor CronJob, PM revocation UI. Building it in v1 delays the core workflow automation with no immediate PM-facing benefit (v1 has one customer, one PM team, ops-managed credentials). K8s Secrets with etcd encryption are adequate for a controlled on-prem cluster in v1. The stable interface (`get_credential`) ensures v2 is a backend swap behind the same call sites, not a refactor of every adapter. Option B (no per-PM credentials — service account only) was rejected because it breaks NFR-5 (PM approval attribution) and NFR-6 (per-PM audit trail).
**Consequences**: `credential_service` module exists in v1 with a simplified implementation; callers (`issue_tracker`, `messenger`, `customer_adapter`, `email_service`, `workflow_engine`) never need to change when v2 swaps in Vault. Ops runbook required: how to provision, rotate, and emergency-revoke a PM's K8s Secret. `credential_service` v1 MODULE.md documents the K8s Secret naming convention (`hilda-cred-{pm_id}-{system_type}`) and the read path. FR-51 replaces FR-32–FR-38 in v1; DEF-14 captures the full v2 scope.

**Implementation note (2026-05-24 — terminology + mechanism update)**: (1) "v1 / v2" terminology is superseded by **Ph-1/Ph-2 / Ph-3+**. (2) The K8s Secrets v1 mechanism in the Decision body is **superseded by `[D-038]` sops-encrypted env files** for Ph-1/Ph-2 (Docker Compose on bare-metal PC per `[D-026]` does not host K8s Secrets). The K8s Secret naming convention `hilda-cred-{pm_id}-{system_type}` is preserved as the Ph-3+ MicroK8s target shape. (3) **Per-PM credentials are not the Ph-1/Ph-2 model**: per PROJECT.md Constraints (2026-05-24 update), Ph-1/Ph-2 uses a **shared HILDA ops-team credential set per customer system** (one `.env` per service), with the `get_credential(pm_id, system_type)` interface preserved on the surface but returning the ops-team credential under the hood. Per-PM credential provisioning lands at Ph-3+ alongside Vault per DEF-14. (4) DEF-14 framing "deferred to v2" → "deferred to Ph-3+." NFR-3 (per-PM isolation) is technically not enforced at the credential layer in Ph-1/Ph-2 since all adapter actions appear under the shared ops-team identity in external systems — Ph-1/Ph-2 attribution model is documented in PROJECT.md Constraints. Stable `get_credential` interface preserved; no caller-side change between Ph-1/Ph-2 and Ph-3+.

---

## D-018: Template Schema Ingestor input format — three modes (schema-file / row-offset / infer)
**Status**: Active · **Date**: 2026-05-04
**Decision**: The `template_schema_ingestor` CLI accepts three `--mode` values, representing escalating LLM involvement: (a) **`schema-file`** — a YAML descriptor explicitly maps customer Excel column names to canonical fields, types, and validation rules; no LLM involved; fully deterministic and CI-testable. (b) **`row-offset`** — column headers are at a known row N (passed via `--header-row N`); the on-prem LLM does lightweight column-name → canonical-field resolution only; no full document inference. (c) **`infer`** — the on-prem LLM reads the full template document, discovers hierarchy layout, header location, and all column mappings, and emits a `CustomerSchema` with a generated `schema.yaml`. All three modes produce an identical `CustomerSchema` Pydantic model (defined in `core/src/template_schema/`) as output. YAML is the schema-file format (over JSON): human-readable with inline comments, low friction for PM team leads to review and edit. Recommended production workflow: run `--mode infer` once during customer onboarding to bootstrap `schema.yaml`, commit it to `customizations/template_schemas/<customer>/`, then switch to `--mode schema-file` for all subsequent re-ingestions. Resolves backlogged Flag "Template Schema Ingestor input format" from `STATUS.md`.
**Why**: Single-mode designs force a choice between LLM dependency (always infer) and manual labour (always schema-file). Three modes let teams start fast (infer), validate the output (commit schema.yaml), and run deterministically in production (schema-file) — matching actual onboarding workflows. YAML over JSON: inline comments let PM team leads annotate unusual column mappings without touching code; still machine-parseable. Row-offset mode handles the common case where header location is known but column names are customer-specific jargon — cheaper than full inference. Vs. two modes (schema-file + infer only): row-offset covers a high-frequency case (structured but non-standard Excel) without paying full-inference LLM cost.
**Consequences**: `template_schema_ingestor` ships `--mode schema-file|row-offset|infer`; `--header-row N` applies to row-offset (required) and infer (optional hint). YAML schema descriptor format is defined in `core/src/template_schema_ingestor/MODULE.md` with a normative example. The `CustomerSchema` Pydantic model lives in `core/src/template_schema/` so runtime modules can load it without importing the ingestor. The "infer-once → commit → schema-file" workflow is the documented onboarding path; `--mode infer --dry-run` previews the generated `schema.yaml` without writing to `customizations/`. Authority for FR-39 (`[D-014]`) and TSI error-code prefix in `error_codes.py`.

---

## D-013: Shared network drive — `hilda-svc` writes, HILDA-mediated reads, no per-customer AD groups in v1
**Status**: Active · **Date**: 2026-05-01
**Decision**: SharePoint cannot handle binary attachment sizes for HILDA's deliverables, so attachments and HILDA-generated artifacts (test reports, tech reports, waivers, customer submission packages) are stored on an on-prem shared network drive, **not** in SharePoint Document Libraries. The drive is SMB-mounted on the HILDA Linux host; Windows-side authentication is on-prem AD. Access model: (a) **Writes** — a single dedicated AD service account `CORP\hilda-svc` is the only principal with `Modify` on `\\share\hilda\`; the Linux SMB mount uses this account's credentials (Kerberos keytab preferred over password); all HILDA writes go through this mount. (b) **Reads** — PMs read attachments exclusively via the HILDA dashboard, which renders attachment links as `https://hilda.corp/dl/<scoped_token>`; the download endpoint authenticates the PM via on-prem AD (NTLM / Kerberos), authorizes against the DeliveryItem's ACL, reads from the network drive as `hilda-svc`, and streams the file to the browser. **Direct UNC paths are not exposed to PMs and are not embedded in any HTML rendered by `core/`.** (c) **Windows ACL** — `CORP\hilda-svc` = Modify; `Domain Admins` = Full (operational); everyone else = none. **No per-customer or per-device AD groups in v1.** Path convention: `\\share\hilda\<customer_slug>\<device_slug>\<milestone_slug>\<deliverable_slug>\<item_slug>\` with `inbound/`, `outbound/`, `revisions/` subdirectories; slugs are `[a-zA-Z0-9_-]+`, minted at entity-creation, immutable on rename, stored on the entity record.
**Why**: SharePoint 2017 Document Libraries cannot handle the file sizes HILDA attachments will reach. The on-prem network drive is the natural alternative. Of the access-model options: vs. direct UNC paths in dashboard links — forwarding or copy-paste leaks the path; bypasses HILDA's audit trail (link generation logged but not access); ACL surface grows linearly with customers (one AD group per customer is the natural granularity). Vs. per-customer AD groups + direct UNC — useful only when ops or admins need raw browse access; v1 has no such requirement. Vs. hybrid (mediated reads + admin-only direct UNC) — more moving parts; v1 has no admin browse-access requirement to justify the complexity. The chosen design: minimal ACL surface (one principal forever), full audit trail through `CommunicationLog` (NFR-6), no path leakage, simple revoke (HILDA-side per-PM ACL change, no AD group changes).
**Consequences**: HILDA ships a download endpoint (likely a FastAPI route at `core/src/storage/` or under the dashboard backend) implementing PM auth + per-DeliveryItem ACL + streaming reads. Storage Protocol surface is `core/`-defined: `put_file(path) -> link`, `get_file_for_download(link, pm_identity) -> stream`, `list_files(item_ref)`, etc. SharePoint integration scope shrinks to Lists + classic web parts only; Document Libraries drop out of the integration surface (NFR-8). Audit logging extends to file reads — every download endpoint hit emits a `CommunicationLog` entry (PM, DeliveryItem, file, timestamp). Slug encoding becomes a cross-cutting convention; the entity model gains a `path_slug` field per Customer / Device / Milestone / Deliverable / DeliveryItem. Authority for FR-13, FR-17, FR-18, NFR-8, NFR-16 in `requirements.md`. PDF support requires architecture-phase choice of the on-prem PDF text-extraction path (`pdfplumber` / `pypdf` / `pymupdf`); Word support requires `python-docx` for `docx` and a separate path for legacy `doc` (likely `antiword` or LibreOffice headless conversion).
**Implementation note (2026-05-17)**: NSD `inbound/` write access — owners with the `NetworkSharedDrive` tracking modality need direct filesystem write access to their item's `inbound/` folder. The v1 ACL model above (`hilda-svc = Modify; everyone else = none`) must be relaxed to grant owners write access to their assigned item paths. The mechanism (shared write group, per-owner AD group, or other) is to be confirmed with corp infra; per-owner ACL granularity (restricting each owner to only their own item path) is deferred to Ph-3 per DEF-16 — v1 uses a shared write group for all NSD owners as the simplest viable approach. Filesystem identity attribution in `CommunicationLog` is also deferred to Ph-3 per DEF-16.
**Implementation note (2026-05-14)**: Path convention updated — `<deliverable_slug>` path segment removed (D-028 removed the Deliverable level; path is now `\\share\hilda\<carrier_slug>\<device_slug>\<milestone_slug>\<item_slug>\`); `revisions/` subdirectory replaced by `<doc_type_slug>/<doc_id_slug>/revN/` classified audit storage (N=1 for initial receipt; N≥2 for subsequent revisions per FR-17). Storage model superseded: corp PLM is the source of truth for all owner deliverables per D-035 and FR-13 (rewrite 2026-05-14); NSD serves as an ingest channel (`inbound/` = NetworkSharedDrive drop zone) and local audit trail, not the authoritative artifact store. D-013 remains the authority for the access model (`hilda-svc` writes, HILDA-mediated reads, no per-customer AD groups) and NFR-16 — those are unchanged.

**Implementation note (2026-05-26 — terminology + D-040 supersession + D-041 assembly source + hilda-svc clarification)**: (1) "v1" → **Ph-1/Ph-2 / Ph-3+** throughout the Decision body. (2) **NSD as authoritative artifact store** is partially restored per `[D-040]` (2026-05-17): NSD classified path is the **in-progress source of truth** for owner deliverables; PLM is the **submitted-deliverables source of truth** only (post-OwnerClosed + TPM approval). The 2026-05-14 impl note's "NSD serves as ingest channel and local audit trail, not the authoritative artifact store" is correct for Ph-1; in Ph-2, NSD is authoritative for in-progress documents. (3) **Submission assembly source is NSD** in both phases per `[D-041]` (2026-05-17) — HILDA downloads from NSD before dispatching to the customer portal. (4) **`hilda-svc` is an Active Directory service account** (`CORP\hilda-svc`), **not** a HILDA container or process. The Linux SMB mount on HILDA PC authenticates to the corp file server using the `hilda-svc` Kerberos keytab; from the corp file server's perspective, every write appears as `hilda-svc`. Per-PM attribution lives in `CommunicationLog` (application layer) per NFR-6, not in the filesystem ACL. Definition formalized in `core/src/storage/MODULE.md` Invariants 2026-05-26. (5) Per-owner NSD `inbound/` ACL + filesystem identity attribution remain deferred to Ph-3 per DEF-16 (as the existing 2026-05-17 impl note above states).

---

## D-020: sharepoint_integration — SpClient / SharePointListProvider Protocol separation
**Status**: Active · **Date**: 2026-05-04
**Decision**: `core/src/sharepoint_integration/` separates two orthogonal concerns: (a) **SpClient** — the raw async SP REST HTTP client; owns NTLM/Kerberos authentication, SP REST URL patterns, pagination, and retry logic; takes SP-native list names and SP internal column names; has no knowledge of HILDA entities. (b) **SharePointListProvider Protocol** — a pure lookup service (no HTTP, no side effects); given a HILDA entity type and a `ListScope(customer_slug, device_slug)`, returns the SP list name and column map for that scope; implemented in `customizations/` but its Protocol definition ships in `core/`. A boilerplate `FileBasedListProvider` implementation ships in `core/` and reads from `customizations/sharepoint_config/customers/<slug>.yaml` (list names + column maps) and `customizations/sharepoint_config/devices/special_devices.yaml` (device-level list overrides); scope lookup precedence is device override → customer config → `SHP-E002`. **`list_crud.py` (class `SpCrud`) is the sole compositor** — it accepts any `SharePointListProvider` implementation, translates canonical field names to SP columns via the provider, and delegates wire calls to `SpClient`. All other HILDA modules call `SpCrud` exclusively; no module calls `SpClient` or `SharePointListProvider` directly. Operational config (site URL, auth type, timeouts) follows the reference implementation 3-tier pattern (CLI arg → env var → `config/sharepoint_integration.json`); customer SP list names and column maps are business config living in `customizations/sharepoint_config/` and are **not** in `config/`.
**Why**: Without separation: the SP mechanics and the HILDA-entity→SP-column routing are entangled; swapping auth method requires touching entity-routing code and vice versa; unit-testing entity routing requires a live SP instance. Separating them: `SpClient` is independently testable with an `httpx.MockTransport` stub; `SharePointListProvider` is testable with pure Python dict comparisons; `SpCrud` is testable by injecting both mocks. Customer deployments override only the YAML data (list names), not the Python code. Vs. embedding list names in `config/sharepoint_integration.json`: config/ is for environment-switching values (site URL changes between dev/prod); list names are fixed per customer, not per environment — wrong axis of variation. Vs. all-in-one `SharePointClient` class: no seam to inject mock provider; business config and auth config entangled.
**Consequences**: `customizations/sharepoint_config/` is the canonical location for all customer-specific SP list name + column map YAML. Any customization that provides a non-file-based provider (e.g. DB-backed, API-backed) implements `SharePointListProvider` and is injected at startup. `FileBasedListProvider` reload triggers: on startup and on explicit admin signal (no hot-reload in v1). SHP error-code prefix registered in `diagnostics/error_codes.py`. `sharepoint_integration_cli.py` ships `--diagnostic` (live SP connectivity + list reachability), `--mock` (`httpx.MockTransport` stub for all-local testing), `--dry-run --customer <slug>` (logs SP operations, no writes).

---

## D-021: Process granularity v1 — modular monolith with three deployable workloads (`hilda-api`, `hilda-worker`, `hilda-llm-gateway`)
**Status**: Active · **Date**: 2026-05-06
**Decision**: HILDA v1 ships as **one container image** containing all 18 `core/src/` modules + all `customizations/`, run as **three K8s Deployments** with different start commands: (a) **`hilda-api`** — FastAPI/uvicorn process; serves the dashboard backend, SP-mediated download endpoint per `[D-013]`, and inbound webhook receivers (messenger / issue-tracker callbacks); 2 replicas; ingress-exposed at `https://hilda.corp/`. (b) **`hilda-worker`** — async-job runner (Celery- or RQ-style; specific engine decided in `D-XXX` per `SYSTEM.md` §4); executes scheduled rule firings, email mailbox polling, ingestor jobs, customer-adapter polling, and any blocking IO that should not contend with API request handling; 2 replicas + 1 beat/scheduler singleton. (c) **`hilda-llm-gateway`** — thin process that fronts both the runtime LLM `[D-007]` and the on-prem code-generation LLM; the only pod authorized to egress to the corp LLM proxy; owns rate-limiting, retry policy, prompt-template loading, and the LLM API key K8s Secret; 2 replicas. **All other modules — `sharepoint_integration`, `email_service`, `messenger`, `issue_tracker`, `tracker`, `rule_engine`, `workflow_engine`, `template_schema`, `template_schema_ingestor`, `api_spec_ingestor`, `test_report_profiler`, `customer_adapter`, `storage`, `credential_service`, `diagnostics`, `dashboard`** — live in-process inside `hilda-api` and/or `hilda-worker` as Python imports, no separate pods. `credential_service` v1 is in-process per `[D-019]` (no Vault pod in v1). Per-customer adapter pods are deferred until customer #2 onboards (out of scope per `PROJECT.md` `DEF-8`). Infrastructure workloads — Postgres (StatefulSet), Redis (Deployment) — remain separate in their own right (standard for any topology). Supersedes `HILDA_Design.md` §11's 12-deployment microservices inventory; that table is preserved as the v2+ target shape but is wrong-sized for v1's one-customer / small-team scope.
**Why**: `HILDA_Design.md` §11's microservices design optimizes for two pressures HILDA v1 does not have: independent scaling per module (one customer, low volume) and per-team ownership (single small dev team, names TBD per `PROJECT.md` Contributors). Twelve Deployments means twelve sets of: Helm sub-charts + values, image build/scan/push, RBAC, NetworkPolicy, liveness/readiness probes, integration-test wiring, log/metrics scrape configs. That overhead is real and pays back nothing at v1 scale. Pure modular monolith (one Deployment) was rejected because three concerns genuinely want process boundaries: (i) blocking IO — email mailbox polling, customer-adapter polling, scheduled rule firings — should not compete with low-latency API request handling; (ii) the runtime LLM is the slowest, riskiest, highest-blast-radius external dependency, and isolating its egress simplifies corp-proxy network policy and contains LLM-side failures from cascading into API pods; (iii) the worker process needs Celery/RQ semantics (long-running tasks, retries with backoff, scheduled triggers) that don't fit the FastAPI lifecycle cleanly. The three-process split addresses all three with the minimum number of pods. Module boundaries inside the monolith remain enforced through Protocol seams already established by `[D-008]` (IssueTracker), `[D-009]` (Messenger), `[D-019]` (credential_service), `[D-020]` (sharepoint_integration); a future v2 split of any module to its own pod is mechanical (extract module + add a thin REST surface), not a refactor — Protocol call sites stay unchanged. Vs. splitting `email_service` to its own pod for 24/7 polling: the polling cadence is minutes, not seconds; running it as a Celery beat task inside `hilda-worker` is sufficient. Vs. splitting `credential_service` to its own pod (per `HILDA_Design.md`): `[D-019]` simplified credentials to K8s Secrets in v1; a separate Deployment buys nothing when the implementation is a `kubectl get secret` wrapper. Vs. splitting per-customer adapters: only one customer in v1; second customer triggers the split.
**Consequences**: One `Dockerfile` produces one image; three Helm Deployment templates differ only in `command:` and resource limits. Configuration follows the reference implementation 3-tier per `[D-020]` already established in `sharepoint_integration` — same shape replicated for every module's `config/<module>.json`. K8s Secrets per `[D-019]` are mounted only into the pods that need each one (e.g., LLM API key only into `hilda-llm-gateway`). The three pods share Postgres and Redis as their coordination substrate; no in-cluster service-to-service HTTP between HILDA pods in v1 except API → llm-gateway. Each module's `MODULE.md` declares which workload(s) host it (api / worker / llm-gateway / multiple) — added as a curated subsection alongside `Depends on`. `regen-map` extends to render workload assignment in `MAP.md`. Test interfaces per `[D-005]` continue to work unchanged — every module ships its CLI / mock harness, exercising it in-process. v2 split path (per-module microservice migration) starts by promoting a Protocol's existing in-process implementation to the server side of a thin REST surface; client side stays at the same import path, so call sites do not change. SYSTEM.md §2 moves from TBD to Decided and links here; SYSTEM.md §3 communication matrix and §5 deployment topology resolve as direct consequences. SYSTEM.md "Conflicts with HILDA_Design.md" entry C3 is now Resolved.

**Implementation note (2026-05-24 — workload count, module count, host count, terminology)**: Per the 2026-05-23 → 2026-05-24 architecture review, the framing in the Decision body is updated as follows: (1) "v1 / v2" → **Ph-1/Ph-2 / Ph-3+**. (2) **Four workloads, not three** — `hilda-beat` (Celery beat singleton) is split out as its own start-command / container per `[D-022]` requirements (singleton replica count distinct from `hilda-worker`'s scaling). The 4-workload table in SYSTEM.md §2 is authoritative. (3) **Twenty modules, not eighteen** — two new corp-side gateway modules added: `corp_messenger_gateway` (HILDA team-owned, runs on the corp-net reverse-proxy PC; receives corp Slack webhooks; forwards to `hilda-api`) and `corp_plm_gateway` (HILDA team-owned, runs on the dedicated PLM gateway PC; bridges HILDA outbound calls to corp PLM). These are deployed on corp-net intake PCs, not on the HILDA PC; they have independent deployment lifecycles. See SYSTEM.md §2.1 for the full 20-module roster. (4) **Three HILDA-owned hosts, not one bare-metal PC** — HILDA PC (lab subnet, runs the 4-workload Docker Compose stack + postgres + redis = 6 containers) + reverse-proxy PC (corp net, hosts the messenger intake app + IT-admin's generic reverse-proxy routes for `hilda.corp/dl/*` and `/status/*`) + PLM gateway PC (corp net, hosts the PLM gateway app). See SYSTEM.md §3 boundary clarification. The "modular monolith on a single bare-metal PC" framing in the Decision body is **incomplete** — the modular-monolith characterization still holds for HILDA PC's 4 application containers, but the full HILDA-owned deployment surface spans 3 hosts. SYSTEM.md C3/C5 Conflicts table entries updated accordingly.

---

## D-022: Workflow engine v1 — Celery + Redis broker + Postgres result backend (Temporal deferred to v2)
**Status**: Active · **Date**: 2026-05-06
**Decision**: HILDA v1 uses **Celery** as its async-task framework, with **Redis as the broker** and **PostgreSQL as the result / state backend**. **Celery beat** (the singleton scheduler) runs as a separate K8s Deployment (`hilda-beat`, replicas=1) alongside `hilda-worker` per `[D-021]`, reading the active schedule from the SharePoint `AutomationRules` list at startup and on a refresh signal. The schedule defines cron-style triggers for time-based rules (reminders, escalations, mailbox poll, customer-adapter poll). Event-triggered rules (inbound webhook, attachment-received, PM-approval-clicked) enqueue Celery tasks directly from the originating handler in `hilda-api`. The `core/src/workflow_engine/` module owns: the Celery app singleton, task decorators (`@hilda_task`) that wrap rule executions with `[D-002]` error-code reporting and structured logging, the beat schedule loader (reads `AutomationRules` via `SpCrud`), and the rule dispatcher (matches an event to one or more rules and enqueues tasks). The `core/src/rule_engine/` module owns pure rule-condition evaluation (no Celery imports); `workflow_engine` is the dispatcher that calls into it. **`HILDA_Design.md` §11's `Workflow Engine (Temporal)` StatefulSet is removed from v1 deployment topology**; `Temporal Workers` are subsumed by `hilda-worker`. Temporal is a v2+ candidate if rule sets evolve into multi-step durable orchestrations with cross-step state and time-travel debugging needs.
**Why**: HILDA's actual workflow surface — enumerated in `HILDA_Design.md` §8.1 — is **single-step state transitions triggered by time or events**: "send reminder when LastOwnerContacted > N days," "trigger quality review on attachment received," "queue for submission on PM approval." The state lives in SharePoint List rows; each rule firing reads SP, evaluates a condition, performs one action (send email / update column / call adapter), writes back to SP. None of this is a multi-day Temporal workflow with cross-step durable state. The closest thing to multi-step is the customer-submission flow (detect ready → human gate → submit), but it's two single-step actions stitched together by SP state, not a workflow object. Temporal's strengths — durable state machines, time-travel debugging, signal/query semantics, workflow versioning — are paid for in operational cost (3-node StatefulSet + worker tier + dedicated history database) that v1 has no demand for. Vs. **APScheduler in-process**: rejected because the scheduler must survive pod restart cleanly; APScheduler with a Postgres jobstore is workable but is single-process by design (only one beat instance can run, like Celery beat) and has weaker semantics around lost task results during restart. Vs. **RQ (Python Redis Queue)**: simpler than Celery but lacks a robust scheduled-trigger story (rq-scheduler exists but is less battle-tested); HILDA needs scheduled triggers heavily for reminders/polling. Vs. **Celery with Postgres broker**: cleaner (one infra dependency) but Celery's Postgres broker has historically been less robust than its Redis broker; Redis is already in the stack per `HILDA_Design.md` §11, so the marginal cost of using Redis as broker is zero. v2 trigger to revisit Temporal: rule set crosses ~30 rules with explicit cross-rule dependencies, OR a workflow emerges that genuinely needs durable multi-step state (e.g., compliance-audit submission with multi-week regulator-side state machine).
**Consequences**: `core/src/workflow_engine/MODULE.md` (when drafted) names: Celery app at `core.src.workflow_engine.celery_app`; task decorator `@hilda_task(rule_id, error_code_prefix)`; beat schedule loader `load_schedule_from_sp(crud) -> dict[str, ScheduleEntry]`; dispatcher `dispatch(event) -> list[AsyncResult]`. WFL error-code prefix already pre-registered in `diagnostics/error_codes.py`. `hilda-worker` and `hilda-beat` Deployments share the `hilda-worker` start command path with different argv (`celery -A workflow_engine worker` vs `celery -A workflow_engine beat`). Redis (one Deployment) gains a documented role as Celery broker in addition to dedup-cache from `[D-012]`. Postgres schema (owned by `core/src/storage/`) gains a `celery_taskmeta` table — Alembic migration when `storage/MODULE.md` is drafted. SP `AutomationRules` list rows include `cron_expression` columns for scheduled rules; rule reload happens on startup and on `SIGHUP`-triggered refresh (no hot-reload of code). All rule executions emit a paired RPT compact report per `[D-002]` keyed by `(rule_id, run_id)`; failure surfaces as a WFL-coded `PipelineError` and a FIX entry if PM intervention required. SYSTEM.md §4 workflow-engine question moves from TBD to Decided; SYSTEM.md §3 inter-component-comms matrix `Redis-backed queue (Celery/RQ)` row is concretized to `Celery via Redis broker, results in Postgres`; SYSTEM.md §5 deployment topology adds `hilda-beat` as a singleton Deployment; SYSTEM.md "Conflicts with HILDA_Design.md" entry C4 is now Resolved.

**Implementation note (2026-05-22 — supersedes beat schedule loader above per FR-30 drift resolution)**: Rule storage was moved to YAML files under `customizations/rules/` per `FR-30`. `hilda-beat` loads the schedule from YAML rule files (`customizations/rules/global/defaults.yaml`, per-customer `customer_rules.yaml`, per-device `device_rules.yaml`) at startup, applying Device → Customer → Global resolution order; bind-mounted per `[D-025]`, reloaded at startup. PM/TPM runtime overrides are read from Postgres (per FR-31 sub-capability 2) and take precedence over all YAML tiers. The beat schedule loader function name `load_schedule_from_sp` and SP `AutomationRules` list as the schedule source are superseded — loader reads from YAML files and Postgres override table instead. No change to Celery task architecture, broker, result backend, or `@hilda_task` decorator.

**Implementation note (2026-05-24 — Ph-3+ broker migration + terminology)**: (1) "v1 / v2" → **Ph-1/Ph-2 / Ph-3+** throughout. (2) **Ph-3+ broker migration**: Redis-as-broker is replaced by **RabbitMQ Quorum Queues** at Ph-3+ per `[D-043]`. Redis retains its cache role in Ph-3+ but is no longer the Celery broker. The Celery task architecture, decorator surface (`@hilda_task`), result backend (Postgres), and call sites are unchanged across the broker swap. (3) Temporal "deferred to v2" → "deferred to Ph-3+, possibly never adopted" — current trigger to revisit is the same as before (cross-rule dependencies + durable multi-step state needs), and Ph-3+ MicroK8s deployment per `[D-043]` does not by itself require Temporal.

---

## D-023: Observability v1 — light stack reusing cluster defaults; dashboards/alerts as code under `deploy/`
**Status**: Active · **Date**: 2026-05-06
**Decision**: HILDA v1 owns and operates **no observability infrastructure of its own** — instead it produces standard signals that the existing on-prem cluster's o11y stack consumes, and ships dashboard / alert definitions **as code** in `deploy/grafana/dashboards/` and `deploy/prometheus/alerts/` so corp Grafana / Prometheus can import them. Three signal channels: (a) **Structured JSON logs to stdout** — every pod (`hilda-api`, `hilda-worker`, `hilda-beat`, `hilda-llm-gateway`) logs JSON via `python-json-logger`-style formatter; cluster default log forwarder picks them up and ships to whatever corp log store exists (Splunk / Elastic / Loki — HILDA does not specify). Required fields per log line: `ts`, `level`, `pod`, `module`, `error_code` (when applicable per `[D-002]`), `run_id`, `pm_id` (when applicable, never the credential), plus the message. (b) **Prometheus metrics on `/metrics`** — every pod exposes a Prometheus scrape endpoint via `prometheus_client` (or `prometheus-fastapi-instrumentator` for `hilda-api`). Required v1 metric families: `hilda_request_total{path, method, status}` (api request counter), `hilda_celery_tasks_total{task, status}` (worker task counter), `hilda_pipeline_errors_total{code}` (the `[D-002]` integration — every `PipelineError` raise increments this), `hilda_llm_calls_total{model, status}`, `hilda_sp_request_total{status}`, `hilda_credential_expiry_seconds{system_type}`, `hilda_queue_depth{queue}`, `hilda_adapter_retry_total{adapter, outcome}`, plus per-family latency histograms (`*_duration_seconds`). (c) **Compact reports via `[D-002]`** — RPT / MET / FIX / QC continue as the domain audit trail, persisted in `CommunicationLog` (Postgres, owned by `core/src/storage/`); these are app-domain artifacts and remain orthogonal to the o11y signals above. **No HILDA-owned distributed tracing in v1** — added as a follow-up `D-XXX` if cross-pod debugging pain emerges (most likely first surface: `hilda-api` → Celery enqueue → `hilda-worker` execution → `hilda-llm-gateway` LLM call). **No HILDA-owned Grafana / Loki / Tempo / OTel collector pods.** Dashboards-as-code: `deploy/grafana/dashboards/system_overview.json`, `error_codes.json` (panels keyed on `hilda_pipeline_errors_total` by `code`), `workers_and_queues.json`, `llm_gateway.json`, `sharepoint_integration.json` — checked into git and importable into corp Grafana via Grafana's import API or HILDA's deploy job. Alert rules-as-code: `deploy/prometheus/alerts/hilda.yaml` with rules per `[D-002]` error-code class (e.g. `SHP-E*` rate > N/min over 5m → page; `WFL-E*` rate spike → ticket).
**Why**: `architecture.md` calls for "medium" observability — meaningful instrumentation at pain points without overbuilding metrics infra v1 doesn't need. The corp cluster almost certainly already runs a log forwarder + Prometheus + some Grafana installation; the team is small (`PROJECT.md` Contributors all TBD); spinning up a HILDA-owned Loki/Prom/Tempo/Grafana stack is 5+ extra workloads to operate with no signal-quality payoff. The dashboards-as-code commitment captures the real value of "owned" o11y (curated panels in git, reviewable in PR, deployable to test/prod) without paying for the pods. `[D-002]` already produces the highest-value signal — error codes with structured context — so the o11y stack's job is mostly to display and alert on it. Vs. **Option B (full HILDA-owned stack)**: 5 extra workloads (Loki + Promtail + Prometheus + Grafana + Tempo + OTel) for capabilities corp infra likely already provides; rejected because v1 ops capacity is the constraint, not signal coverage. Vs. **Option C (hybrid: reuse corp logs/Prom but own Grafana + Tempo)**: 2 extra workloads buys traces + git-controlled dashboards; rejected for v1 because (a) tracing demand is not yet evidenced, (b) git-controlled dashboards are achieved by the as-code pattern without owning Grafana itself. Vs. shipping no dashboards / alerts: rejected because o11y signals with no consumption surface are dead code. The chosen design surfaces every signal HILDA needs while keeping the operational footprint at zero new pods.
**Consequences**: A new shared module `core/src/observability/` (or extension of `diagnostics/`) provides the Prometheus client setup + log formatter + standard metric registry; every workload imports it on startup. `[D-002]`'s `PipelineError` raise path automatically increments `hilda_pipeline_errors_total{code}` (instrumented at `error_codes.py` level — one-shot wiring). `hilda-api`, `hilda-worker`, `hilda-llm-gateway` Helm Deployment templates each gain a `containerPort: 9090` for `/metrics` and a Prometheus `ServiceMonitor` (or scrape annotation, depending on what corp Prom uses). `deploy/grafana/dashboards/` and `deploy/prometheus/alerts/` are part of the deploy artifact and provisioned alongside the chart. CI lints these (`promtool check rules`, `jsonnet --eval` if dashboards become jsonnet). Logs must never include credential material, customer-feedback prose, or report content per the no-proprietary-content invariant `[D-002]` + `PROJECT.md` Constraints — log review is part of `/drift-check`. v2 trigger to revisit: cross-pod debugging pain → distributed tracing follow-up `D-XXX`; corp Grafana shows scaling pain on dashboards → consider HILDA-owned Grafana. SYSTEM.md §6 moves from TBD to Decided; SYSTEM.md Open Question #3 is closed.

**Implementation note (2026-05-26 — Docker Compose Ph-1/Ph-2; pods → containers; ServiceMonitor → direct scrape)**: (1) "v1" → **Ph-1/Ph-2 / Ph-3+** throughout. (2) "K8s cluster", "pods", "Helm Deployment templates", "ServiceMonitor" terminology: Ph-1/Ph-2 actual platform is **Docker Compose on bare-metal Linux PC** per `[D-026]`; "pods" → "containers"; "Helm Deployment templates" → `docker-compose.yaml` service entries; ServiceMonitor (K8s Prometheus Operator concept) → **direct Prometheus scrape via Docker DNS** in Ph-1/Ph-2 (corp Prometheus targets the HILDA PC's exposed `/metrics` ports directly, or a host-level scrape config). Ph-3+ MicroK8s per `[D-043]`: pods + ServiceMonitor terminology applies. (3) Per-container `/metrics` endpoint via `prometheus_client` is unchanged across phases — same Python instrumentation, different scrape mechanism. (4) Corp Grafana / Prometheus consumption pattern (dashboards-as-code under `deploy/grafana/dashboards/`; alert rules-as-code under `deploy/prometheus/alerts/`) is unchanged across phases — same dashboard JSON, same alert YAML. (5) Distributed tracing remains deferred. (6) `hilda_credential_expiry_seconds` instrumentation is **Ph-3+ only** per DEF-14 Credential Health Monitor; not implemented in Ph-1/Ph-2 (shared ops-team credentials have no per-PM expiry to track). (7) Module count: 20 modules per `[D-021]` impl note 2026-05-24 (added `corp_messenger_gateway` + `corp_plm_gateway`); both corp-side gateway modules also emit `/metrics` and log to stdout JSON.

---

## D-024: CI/CD shape v1 — tool-agnostic pipeline contract + single umbrella Helm chart with per-environment values
**Status**: Active · **Date**: 2026-05-06
**Decision**: HILDA's CI/CD captures the durable shape independent of which corp tools host it. Specific corp-tool selections (CI runner, image registry, GitOps tool, environment topology) remain a backlogged Flag in `STATUS.md` to be filled in after consultation with corp ops; they fit into this shape as parameters, not redesigns. **Pipeline shape (uniform, tool-agnostic):** (a) **On every PR** — lint (`ruff` / `mypy` / `black`) + unit tests + integration tests against in-process mock SP server via `httpx.ASGITransport` (existing pytest suite, currently 101 tests) + image build + image vulnerability scan (Trivy / Grype / corp scanner). PR is mergeable iff all stages pass. (b) **On merge to `main`** — tag image with git SHA (`hilda:<git-sha-short>`), push to corp registry, deploy chart to **test env** with `values-test.yaml`, run smoke tests against test env (mock SP + real Postgres + real Redis), update test env's HEAD pointer. (c) **Promotion to prod** — manual: re-tag the SHA-tagged image with semver (`hilda:1.0.0`), deploy chart to **prod env** with `values-prod.yaml`, run smoke tests against prod (real SP via NTLM/Kerberos once corp AD lab access lands), gate on success. (d) **Image versioning** — SHA tag for dev/test (immutable, traceable to commit); semver tag for releases (`MAJOR.MINOR.PATCH` per semver.org); both tags coexist in the registry; `latest` tag is **not used** in any cluster manifest to prevent accidental drift. **Helm chart structure:** one umbrella chart at `deploy/charts/hilda/` containing all three v1 Deployment templates (`hilda-api`, `hilda-worker` + `hilda-beat`, `hilda-llm-gateway`) per `[D-021]`, plus shared resources (Service, Ingress, ServiceAccount, NetworkPolicy, ServiceMonitor for `[D-023]`). Environment-specific values files: `values-dev.yaml` (local kind / minikube — runs against mock SP + ephemeral Postgres/Redis), `values-test.yaml` (test cluster / namespace — mock SP + real Postgres/Redis), `values-prod.yaml` (real SP via NTLM/Kerberos + production Postgres/Redis). The `values-*.yaml` files are checked into the repo; secret values are **not** — those are K8s Secrets per `[D-019]`, provisioned by ops, referenced by name from the chart. **Test environment specifically runs the mock SP server as a sidecar / separate Deployment** (`mock-sharepoint:<sha>`, the existing `core.src.sharepoint_integration.mock_server.app`) so test-env pods point at it via `HILDA_SP_SITE_URL=http://mock-sharepoint:8765`. **Per-workload sub-charts rejected**: one umbrella chart for v1 because all three workloads share the same image, deploy together, and version together; sub-charts add Helm template-resolution overhead with no payoff at three deployments. (e) **Backlogged tool-bound choices** — CI runner (GitHub Actions / GitLab CI / Jenkins / corp-specific), image registry (Harbor / Artifactory / Nexus / corp-specific), GitOps tool (ArgoCD / Flux / none — CI-driven `helm upgrade`), environment topology (separate clusters vs separate namespaces in one cluster).
**Why**: Separating the pipeline **shape** (what stages run, what artifacts they produce, what the deploy unit looks like) from the **tools** (which CI runs the stages, which registry holds the image, which mechanism syncs to cluster) lets HILDA commit to durable architectural choices now without blocking on corp ops scheduling. The shape is decision-worthy in its own right: the PR-time vs merge-time vs promote-time split, the image-versioning convention, the umbrella-chart-with-per-env-values structure, the test-env-uses-mock-SP pattern — these survive any CI-tool swap. Vs. waiting for full corp-ops consultation: blocks every deploy-time architectural choice (Helm structure, env values, test-env wiring) until a meeting; rejected. Vs. full per-workload sub-charts: Helm sub-chart machinery solves problems v1 doesn't have (independent versioning of components, third-party reuse); rejected. Vs. raw K8s manifests + Kustomize overlays: Kustomize is fine for "deploy this same shape to N customers" but HILDA v1 has one customer; Helm's templating is more mature for the "values per environment" axis we actually have. Vs. using `latest` tag: rejected as a hard rule because `latest` defeats traceability and rollback; SHA tags for dev/test (immutable, debuggable) and semver for releases (deliberate human action) is the standard pattern. Vs. CI-deployed prod: explicitly **manual** prod promotion because v1 has one customer + small team + frozen SP infrastructure — automated prod deploys multiply blast radius without saving meaningful time at v1 cadence.
**Consequences**: `deploy/charts/hilda/` lives at repo root with `Chart.yaml`, `values.yaml` (defaults), `values-dev.yaml` / `values-test.yaml` / `values-prod.yaml`, and `templates/` containing one Deployment per workload + Service + Ingress + NetworkPolicy + ServiceMonitor per `[D-023]`. `Dockerfile` lives at repo root, builds one image used by all three Deployments (different `command:` per workload). `deploy/grafana/dashboards/` and `deploy/prometheus/alerts/` from `[D-023]` ship alongside the chart. CI pipeline definition file (`.github/workflows/ci.yml` / `.gitlab-ci.yml` / `Jenkinsfile` — TBD) implements the pipeline shape; a `STATUS.md` Flag tracks the tool selection until resolved by a follow-up `D-XXX`. Mock SP server image (`mock-sharepoint:<sha>`) is built from the same repo and pushed to the same registry; only used in dev / test envs, never prod. Smoke-test suite (separate from unit/integration tests) lives at `core/tests/smoke/` and runs against a deployed env via the SP REST surface — to be drafted when test env is provisioned. Versioning policy: tag every merge with SHA; cut a semver release at meaningful milestones (manual decision; release notes in `CHANGELOG.md` — file to be created at first release). v2 triggers to revisit: per-customer chart instances if customer N customizes deeply enough to need its own values file (today this lives in `customizations/sharepoint_config/`, not in Helm values); per-workload sub-charts if any workload's release cadence diverges materially from the others. SYSTEM.md §8 moves from TBD-shape to Decided-shape with tool choices remaining as a tracked Flag; SYSTEM.md Open Question #4 split into resolved (shape) + open (tool selection); SYSTEM.md Open Question #5 (Helm chart granularity) is closed by this decision.

**Implementation note (2026-05-24 — Helm pinned to Ph-3+; Ph-1/Ph-2 = basic CI + manual deploy)**: (1) "v1 / v2" → **Ph-1/Ph-2 / Ph-3+** throughout. (2) The Helm-based CD pipeline described in the Decision body **assumes K8s** and is therefore **pinned to Ph-3+** (alongside the MicroK8s migration per `[D-022]` / `[D-026]` / `[D-043]`). (3) **Ph-1/Ph-2 CI/CD shape is split**: CI-only (lint + unit tests + integration tests against mock SP + image build + vulnerability scan) on PR / merge to main; **deployment is manual / scripted** — `deploy/scripts/deploy.sh` runs on the bare-metal HILDA PC: `git pull` → `sops --decrypt` per `[D-038]` → `docker compose pull` → `docker compose up -d` → `docker compose run --rm hilda-api alembic upgrade head`. (4) No promote-to-prod stage in Ph-1/Ph-2 — operator-driven deploys directly to the single bare-metal host. The full multi-environment Helm pipeline (with `values-dev` / `values-test` / `values-prod` files, smoke tests, manual semver promotion) lands at Ph-3+. (5) Tool-bound choices flagged in the Decision body (CI runner, image registry, GitOps tool, environment topology) remain backlogged in `STATUS.md`; resolution depends on the Ph-3+ MicroK8s tool selection at that time. (6) `deploy/charts/hilda/` remains a placeholder (`README.md` only) until Ph-3+; the `deploy/compose/` tree (Docker Compose + sops-encrypted env files + `deploy.sh`) is the Ph-1/Ph-2 deploy artifact.

---

## D-025: Customer YAML mount — v1 Docker bind-mount; v2 K8s ConfigMap
**Status**: Active · **Date**: 2026-05-08
**Decision**: `customizations/sharepoint_config/` (customer list/column maps per `[D-004]` + `[D-020]`) is mounted into HILDA containers at `/app/customizations/sharepoint_config/` via a Docker Compose bind-mount in v1 (the directory lives in the repo on the bare-metal host). In v2 K8s it becomes a ConfigMap mounted at the same container path. The mount path is controlled by `HILDA_CUSTOMIZATIONS_DIR` env var (default `/app/customizations/sharepoint_config/`); `FileBasedListProvider` reads from this path regardless of how it was injected.
**Why**: Bind-mount and ConfigMap are the same abstraction — host directory injected into container at a configurable path — with different mechanisms. The bind-mount approach (v1) avoids image rebuild when a new customer YAML is added (customer YAML is not baked in); for a single-machine deployment this is as lightweight as possible. Image-baked was rejected: adding a new customer requires a rebuild and push cycle even when only a YAML file changed. ConfigMap (v2) gives identical semantics in K8s. Using the same env var and container path in both v1 and v2 means zero code change at migration time.
**Consequences**: `FileBasedListProvider` constructor accepts a base path defaulting to `HILDA_CUSTOMIZATIONS_DIR`. `docker-compose.yaml` mounts `./customizations/sharepoint_config/` as a read-only bind-mount into all three HILDA application services. K8s migration: replace the bind-mount volume with a ConfigMap volume; no Python change. Resolves SYSTEM.md Open Question #6.

**Implementation note (2026-05-26 — Ph terminology; expanded scope beyond sharepoint_config)**: (1) "v1 / v2" → **Ph-1/Ph-2 / Ph-3+** throughout. Ph-3+ ConfigMap is specifically **MicroK8s ConfigMap** per `[D-043]`. (2) Scope of the bind-mount pattern is **expanded beyond `customizations/sharepoint_config/`** — the same bind-mount mechanism applies to all `customizations/` sub-directories that HILDA services read at runtime: `customizations/sharepoint_config/<deployment>.yaml` per `[D-004]` / `[D-020]`; `customizations/rules/{global,<customer>,<customer>/<device>}/` per FR-30 (AutomationRules YAML, Device → Customer → Global resolution); `customizations/template_schemas/<customer>/` per FR-39/40/41 + `[D-018]`; `customizations/test_report_parsers/<customer>/` per `[D-011]`; `customizations/customer_adapters/<customer>/`; `customizations/messenger/<proprietary>_adapter.py` per `[D-016]`; `customizations/issue_tracker/<proprietary>_adapter.py` per `[D-003]`. (3) `HILDA_CUSTOMIZATIONS_DIR` env var defaults to `/app/customizations/` (root of the customizations tree), not just `/app/customizations/sharepoint_config/` — each consumer module resolves its own sub-path under this root. (4) Number of HILDA-side workloads that read from this mount: all four (`hilda-api`, `hilda-worker`, `hilda-beat`, `hilda-llm-gateway`) per `[D-021]` impl note 2026-05-24.

---

## D-026: v1 deployment platform — Docker Compose on single bare-metal Linux PC
**Status**: Active · **Date**: 2026-05-08
**Decision**: HILDA v1 runs on a **single bare-metal Linux PC** using **Docker Compose** as the orchestration layer. This supersedes the K8s-specific deployment mechanisms in `[D-021]` (three K8s Deployments + Helm chart), `[D-024]` (Helm chart structure), and `[D-019]` (K8s Secrets) **for v1 only**. The process boundaries (hilda-api / hilda-worker + hilda-beat / hilda-llm-gateway), container image (one `Dockerfile`, one image), task architecture (Celery + Redis + Postgres per `[D-022]`), and observability signals (structured logs + `/metrics` per `[D-023]`) are **unchanged**. Secrets are per-service `.env` files at `/etc/hilda/<service>.env` on the host, provisioned by ops, gitignored; env var names are identical to what v2 K8s Secrets will set. `deploy/compose/docker-compose.yaml` is the v1 deploy artifact; `deploy/charts/hilda/` Helm chart from `[D-024]` is preserved as a v2+ placeholder (README only in v1). A `deploy/scripts/deploy.sh` script handles: `git pull` → `docker compose pull` → `docker compose up -d` → `docker compose run --rm hilda-api alembic upgrade head`. K8s migration path: Docker Compose service names mirror intended K8s ClusterIP Service names (zero rename); `kompose convert` produces base K8s manifests; Nginx container → Ingress controller; env_file → K8s Secrets; bind-mounts → PVCs / ConfigMaps; replica counts scale up to `[D-021]` targets.
**Why**: v1 is one customer, one small team, one machine. K8s overhead at this scale — cluster provisioning (control-plane, CNI, CSI, etcd, kubelet), Helm chart + values files, RBAC, NetworkPolicy, cert-manager, ingress controller — is disproportionate and provides no payoff: one replica per service is sufficient, no independent scaling needed, no multi-team pod ownership. Docker Compose gives identical container isolation, identical DNS naming (service name = hostname), identical env-var config pattern, and identical image artifacts as K8s — minimizing migration friction when scale demands it. vs. raw systemd processes: systemd avoids Docker daemon overhead but is not containerized, so K8s migration would require writing Dockerfiles, adjusting paths, and verifying runtime parity from scratch; with Compose the containers already exist. vs. Docker Swarm: adds clustering semantics that add no value for one machine. vs. K8s with single-node cluster (e.g., k3s / kind): still requires etcd + kubelet + kube-proxy; adds operational complexity for a single developer managing a single PC. The "design for K8s migration" requirement is met by naming, env var, and volume conventions — not by running K8s itself.
**Consequences**: `deploy/compose/docker-compose.yaml` is the primary deploy artifact for v1. `deploy/charts/hilda/` exists as a v2 placeholder. All env var names (`HILDA_SP_*`, `HILDA_DB_URL`, `HILDA_REDIS_URL`, `HILDA_LLM_*`) are chosen to be identical in Compose env_file and K8s Secret so no code changes on migration. Docker Compose service names (`hilda-api`, `hilda-worker`, `hilda-beat`, `hilda-llm-gateway`, `postgres`, `redis`) are chosen to match intended K8s Service names. SYSTEM.md §2, §5, §7, §8, §9 updated to reflect bare-metal Compose v1 with K8s v2 notes throughout. `[D-019]`'s K8s Secret naming convention is v2-only; v1 credential mechanism is `.env` files with identical env var names. `[D-023]`'s references to "pods" and "ServiceMonitor" apply to v2; v1 equivalent is containers + Prometheus scrape via Docker service DNS. SYSTEM.md "Conflicts with HILDA_Design.md" entry C5 added. Resolves SYSTEM.md Open Question #9.

**Implementation note (2026-05-24 — 3-host deployment surface + MicroK8s specificity + terminology)**: (1) "v1 / v2" → **Ph-1/Ph-2 / Ph-3+** throughout. (2) **"Single bare-metal Linux PC" framing is incomplete** — the HILDA-owned deployment surface in Ph-1/Ph-2 is **3 hosts**, not 1: (a) **HILDA PC** (lab subnet) runs the 4-workload Docker Compose stack (hilda-api / hilda-worker / hilda-beat / hilda-llm-gateway) + postgres + redis = 6 containers, per the body of this Decision; (b) **Reverse-proxy PC** (corp net) hosts the IT-admin's generic reverse proxy plus the HILDA-team-owned `corp_messenger_gateway` application (corp Slack webhook intake → forwards to `hilda-api`) and routes `hilda.corp/dl/*` + `hilda.corp/status/*` to `hilda-api` for PM browser → HILDA HTTP traffic; (c) **PLM gateway PC** (corp net) hosts the HILDA-team-owned `corp_plm_gateway` application (bridges HILDA outbound to corp PLM; relays PLM events back). Both corp-side PCs run HILDA-team-maintained code and have independent deployment lifecycles. See SYSTEM.md §3 boundary clarification + §5 deployment topology. (3) **K8s migration target = MicroK8s single-node specifically**, per `[D-043]` — not generic K8s. The K8s migration notes throughout the body now refer to MicroK8s + RabbitMQ (replacing Redis-as-broker) + Rook/Ceph + MetalLB per `[D-043]`. (4) Service-name convention (HILDA PC services match intended MicroK8s ClusterIP names) is preserved; corp-side gateway PCs are orchestrator-independent and persist across the Ph-3+ migration unchanged. (5) `[D-019]` reference: K8s Secrets naming convention is Ph-3+ only; Ph-1/Ph-2 credential mechanism is **sops-encrypted `.env` files** per `[D-038]` (the K8s Secrets cross-reference in the Decision body is corrected here — Ph-1/Ph-2 = sops, not plaintext `.env`).

---

## D-028: Flatten entity hierarchy — remove Deliverable level; group DeliveryItems by tg_name
**Status**: Active · **Date**: 2026-05-12
**Decision**: Remove the `Deliverable` intermediate level from the entity hierarchy. `DeliveryItems` now parent directly to `Milestone` (FK `milestone_id`). Visual and logical grouping of items within a milestone is handled by the `tg_name` (Technical Group Name) field on each `DeliveryItem` (e.g., "Hardware", "Software"). Additional data model changes in the same session: (a) `ItemType.Binary` renamed to `Confirmation` — items closed by owner reply with no artifact; (b) new `DeliveryItem` fields: `item_no` (sequential int within milestone), `tg_name`, `owner_status_note` (latest inbound owner update, auto-populated), `actual_completion_date` (auto-set on → Closed); (c) new `Milestone` field: `email_cc_list` (JSON array of `{name, email, role}`) — per-milestone CC distribution for all email communications. The `Deliverables` SharePoint List is removed from the data model.
**Why**: PMs confirmed the Deliverable grouping level does not map to their working model — items belong directly to a milestone and are mentally grouped by technical group (TG), not by a named deliverable bucket. Removing the level reduces tracker creation complexity, flattens the SP List hierarchy by one hop, and makes the hierarchy match the TG-based view PMs already use. `ItemType.Binary` was renamed to avoid ambiguity with `SoftwareBinary`; "Confirmation" precisely captures the semantics (owner reply closes the item, no artifact). CC list at Milestone level (not Device level) gives per-milestone flexibility without per-item overhead.
**Consequences**: `Deliverables` SP List removed; `DeliveryItems.deliverable_id` → `milestone_id`; unique constraints updated to `(milestone_id, item_name)` and `(milestone_id, item_no)`; `CustomerSchema.entity_hierarchy` no longer includes a "deliverable" entity config; `DeliverableBase` Pydantic model removed from `core/src/template_schema/`; `MilestoneBase` gains `email_cc_list`; `DeliveryItemBase` gains `item_no`, `tg_name`, `owner_status_note`, `actual_completion_date` and drops `deliverable_id`; `ItemType.BINARY` → `CONFIRMATION` in enums and registry. `requirements.md` FR-2, FR-5, FR-6, FR-9, FR-13, FR-22, FR-40 updated to reflect flattened hierarchy. `template_schema` code (`enums.py`, `models.py`) needs updating before next development trip.

**Implementation note (2026-05-26 — TG-group fields added subsequent to D-028)**: Subsequent additions to the DeliveryItem / TG-group data model after this Decision:

- **2026-05-19** (FR-2 / FR-71 Owner Discovery Function): `tg_owner_name` + `tg_owner_email` added at the TG-group level (one per `(milestone_id, tg_name)`); per-DeliveryItem `email_cc_list` added (overridable per-item from per-TG `default_cc_list`); per-DeliveryItem `doc_count`, `review_required`, `review_status`, `item_completion_pct` added.
- **2026-05-22** (FR-2): `email_group_alias` + `corp_id_list` added at the TG-group level — `email_group_alias` is the TG's corporate email distribution alias (replaces individual owner emails for TG outreach when set); `corp_id_list` is the complete corp-ID list of TG members (replaces individual owner corp-IDs for messenger escalation when set).

These **TG-group fields are NOT per-DeliveryItem** — they apply to all items sharing a `tg_name` within a milestone. Storage model: separate **`TGGroups` SP list** per `sharepoint/REQUIREMENTS.md §2.8` (one row per `(milestone_id, tg_name)`) + source data in customer template YAML at `customizations/template_schemas/<customer>/tg_groups.yaml` per FR-2. DeliveryItem retains just `tg_name` as the foreign-key-like label; the SP UI does a lookup to TGGroups for display per `sharepoint/REQUIREMENTS.md §3.1`. The `email_cc_list` field on `MilestoneBase` per the original D-028 is superseded by the per-DeliveryItem `email_cc_list` (added FR-2 2026-05-19) — milestone-level CC is no longer used; per-TG `default_cc_list` pre-populates per-item `email_cc_list` at tracker-creation time.

---

## D-027: Teacher↔Cline collaboration — one-way git bridge; compact reports as the return channel
**Status**: Active · **Date**: 2026-05-08
**Decision**: The work PC can **read but not write** `origin` (github.com). The git topology is a **one-way bridge**: Teacher LLM pushes scaffolds to `origin`; `sync-work.sh` pulls from `origin` and pushes to `company` (internal GitHub); Cline pulls from `company`, completes TODOs, and pushes back to `company`. Teacher never reads Cline's completed code via git — the only return channel is the **compact redacted report** (ITR-RPT, ASI-RPT, etc.) that the user hand-types into the Teacher chat. `utils/git-sync/sync-work.sh` is a 4-step one-way bridge: fetch company → merge company → fetch origin → merge origin → push company. It does not push to origin. `.clinerules/01-role.md` documents this constraint explicitly.
**Why**: Corporate network policy blocks outbound push to github.com from the work PC. An attempted two-way sync (push-to-origin step added then reverted) confirmed the constraint is hard. The Teacher/Student protocol was already designed for this: Teacher designs from compact reports (never from proprietary content), so no return git channel is architecturally required. The compact report is sufficient for Teacher to write the next scaffold.
**Consequences**: `sync-work.sh` must never gain a push-to-origin step. Cline trip prompts always end with "push to company, tell user to run sync-work.sh." Teacher's next scaffold is always based on the compact report, not on reading completed adapter code. Proprietary implementation details (completed TODOs, real system names in env vars) stay on `company` and never reach `origin` by construction.

**Implementation note (2026-05-24 — clarified topology + git identity history)**:

D-027's original framing is correct as written. This note clarifies operational details and resolves an apparent contradiction in the git history.

(1) Topology confirmed by `git-sync` README (added to repo as scripts + documentation):

```
Personal PC ──(push)──► github.com (public repo / intermediary)
                          │
                          └──(pull)──► Work PC ──(push)──► company git
```

- Personal PC = Claude (Teacher). Pushes scaffolds + docs + `core/src/` + all MODULE.md files to github.com.
- github.com (single repo, github.com/kurnoolion/hilda) plays two roles: receives from Personal PC; serves to Work PC via HTTPS read-only pulls.
- Work PC = Cline (Student). Pulls from github.com (no auth needed for public repo). Completes scaffold TODOs against proprietary inputs. Pushes only to company internal git via SSH.
- Company internal git is unreachable from Personal PC. The air-gap in D-027's Decision body is preserved.

(2) Personal PC git identity transition (historical, no architectural impact): Earlier commits on Personal PC used `kurnoolion <mohanreddy.duggi@gmail.com>` as the git identity. From 2026-05-22 onward, Personal PC commits use `ai-math-01 <p.kingofbreeze@gmail.com>` per session-by-session preference. Both identities represent the same actor (Claude operating Personal PC). The kurnoolion-authored commits visible in github.com main branch (e.g. `cb40100` defecttrack_adapter scaffold; `1855f95` issue_tracker MODULE.md) were authored by Claude on Personal PC, NOT by Cline on Work PC.

(3) Work split between Teacher and Student:

**Claude (Personal PC, pushes to github.com)**:
- `core/src/*` — non-proprietary code (Pydantic models, Protocol definitions, FastAPI app, Celery workers, mock SP harness, diagnostics, dashboard, storage, all adapters that don't require proprietary specs)
- All docs under `docs/compact/*` — requirements.md, DECISIONS.md, PROJECT.md, STATUS.md, SYSTEM.md, MAP.md, structure-conventions.md, `design-inputs/HILDA_Design.md`, all `phases/*.md` files
- MODULE.md for all modules — including `customizations/<name>/MODULE.md` (these document the structure and contract, no proprietary content)
- Scaffolds for `customizations/<name>/<adapter>.py` with `# TODO` markers and `<SYS0>`-style placeholder identifiers for proprietary system names

**Cline (Work PC, pushes to company internal git only)**:
- Completed TODO markers in `customizations/<name>/<adapter>.py` — fills in real proprietary API endpoints, real status maps, real transition maps, etc., using on-prem access to the proprietary spec
- Real customer template YAML output from Template Schema Ingestor `[D-010]` run against real proprietary Excel
- Real customer test report parser output from Test Report Profiler `[D-011]` run against real proprietary historical reports
- All of this stays on company internal git; never on github.com

(4) Script tooling (per the git-sync README):

- `setup-personal.sh` — one-time setup on Personal PC: clones github.com repo, sets commit identity per-repo, verifies remote
- `setup-work.sh` — one-time setup on Work PC: clones github.com over HTTPS, sets commit identity (company), adds `company` SSH remote, pushes state
- `sync-personal.sh` — Personal PC daily: stage + commit + push to github.com
- `sync-work.sh` — Work PC daily: pull colleague changes from company git, pull Claude's new work from github.com, merge if both changed, push merged state back to company git ONLY (never to github.com)
- `git-debug.sh` — pattern-matches git errors and suggests workflow-specific fixes; never runs destructive commands

(5) The Decision body's invariant ("Work PC can read but not write origin; Teacher never reads Cline's completed code via git") is unchanged. The compact RPT/MET/FIX/QC return channel per `[D-002]` remains the only way Teacher learns about Cline's completed work and proprietary specifics.

(6) Implication for the apparent "Cline commits in origin" anomaly: there are none. All commits in `origin/main` are by Claude (Personal PC), regardless of which historical git identity (`kurnoolion` or `ai-math-01`) is shown in `git log --format=%an`. Cline's completed work is on company internal git and is never visible to Claude via any git path.

---

## D-029: Runtime LLM promoted to Ph-1 scope for two specific functions
**Status**: Active · **Date**: 2026-05-12
**Decision**: The runtime LLM is promoted to Ph-1 scope for exactly two functions: (a) attachment-to-item routing via first-page content inspection (FR-52), and (b) initial one-pass quality review of test reports, tech reports, and waivers against per-customer checklists (FR-53). All other runtime LLM functions — DEF-1 (free-text message classification), DEF-3 (LLM-drafted customer responses), DEF-4 (status summarisation) — remain Ph-2.
**Why**: Both FR-52 and FR-53 address mainstream workflows that cannot be deferred or routed to PM triage. Attachment routing without LLM requires either filename regex (unreliable — lab filenames like `A0000-23456.docx` carry no semantic content) or owner-annotated reply blocks (friction on every email cycle). Document quality review without LLM means PM manually reviews every inbound document in Ph-1. Both tasks are narrow, low-risk LLM operations: FR-52 inspects first-page content only; FR-53 checks against a pre-generated checklist. Neither requires full document deep-read or free-text generation. Keeping DEF-1/DEF-3/DEF-4 deferred preserves the Ph-1 deterministic processing boundary for email routing and keeps the runtime LLM scope small and testable.
**Consequences**: Runtime LLM module (`core/src/llm/` or equivalent) needs a Ph-1 implementation scoped to FR-52 and FR-53 surface only. MODULE.md for the runtime LLM module must explicitly partition Ph-1 vs Ph-2 surface in its Public surface section. DEF-1, DEF-3, DEF-4 remain deferred. The `hilda-llm-gateway` workload (per `[D-021]`) is in-scope for Ph-1 deployment, but its Ph-1 API surface is narrow: document excerpt → item match, document excerpt + checklist → findings list.
**Implementation note (2026-05-13)**: DEF-1 (free-text message classification) promoted to Ph-1 as the FR-12 path (c) LLM fallback (see `[D-034]`). Runtime LLM Ph-1 scope is now three functions: FR-52 attachment routing (two-tier per `[D-033]`), FR-53 initial document review, and FR-12 path (c) classification fallback. DEF-3 and DEF-4 remain Ph-2.

**Implementation note (2026-06-05 — three-function framing confirmed against 4-TaskKind / 5-step-pipeline state)**: The 2026-05-28 requirements update further refined the LLM surface: (1) FR-52 attachment routing evolved from the two-tier formulation (per `[D-033]`) to a **5-step pipeline** (anchors `[D-053]`) — strict-substring → fuzzy → folder-template → LLM `ROUTE_ATTACHMENT` → staged-to-default-work-item; (2) `CLASSIFY_DOC_TYPE` TaskKind was **removed** per `[D-052]` impl note 2026-05-28b (`doc_type` now derived from `item.item_type` 1:1 per `[D-053]`); (3) `CLASSIFY_DOC` (D-039 Step 2 new-vs-revision LLM) is Ph-2-only and lives under FR-17 revision handling, not under the Ph-1 LLM scope. **Net**: the **three Ph-1 LLM functions framing remains correct** — (a) FR-52 attachment routing via `ROUTE_ATTACHMENT` TaskKind (one of 4 Ph-1 TaskKinds); (b) FR-53 document review via `REVIEW_DOCUMENT` TaskKind; (c) FR-12 path (c) message classification via `CLASSIFY_MESSAGE` TaskKind (which becomes a fused message+attachment LLM call when attachments are present per `[D-034]`). The 5-step pipeline and 4-TaskKind partition are sub-decision granularity under FR-52, not new LLM functions.

---

## D-030: Inbound email attachment routing — runtime LLM first-page content inspection
**Status**: Active · **Date**: 2026-05-12
**Decision**: When an inbound email contains attachments, the system extracts first-page / header content from each file and calls the runtime LLM with: (a) the extracted text, (b) the list of candidate DeliveryItem names, descriptions, and `item_type` values from the batch. The LLM returns a ranked item match with a confidence score. Matches above a configurable threshold are auto-routed — the attachment is written to the matched DeliveryItem's `inbound/` subdirectory on the shared network drive (per FR-13) and the DeliveryItem record is updated. Matches below threshold are surfaced on the PM dashboard for manual assignment only.
**Why**: Three alternatives were considered and rejected. (1) Filename regex: lab-generated filenames are opaque (e.g., `A0000-23456.docx`); any pattern set produces false positives and false negatives on real data. (2) Structured reply block with owner-typed attachment annotation: adds non-trivial friction to every email reply cycle; errors (typos, omissions) require manual correction; rejected as a Ph-1 primary path, acceptable as a future enhancement. (3) Attachment index in reply block (owner types "1"/"2" next to items): lighter than typing filenames but still owner burden; first-page LLM inspection removes the burden entirely for the common case. First-page content (document title, test scope header, technology area) contains enough semantic signal to match item names reliably; the LLM sees only document excerpt + item name list — bounded, non-deep proprietary exposure.
**Consequences**: Email Service (or a dedicated attachment-router sub-module) owns first-page extraction and LLM call per inbound email. Confidence threshold is a configurable rule parameter. PM dashboard gets a manual-assignment surface for low-confidence cases (exception path). Negative tests required: opaque filename + clear title page → correct auto-route; ambiguous content below threshold → PM assignment surface triggered; no false auto-routes accepted. Authority for FR-52.

**Implementation note (2026-05-26 — superseded by D-033 + path convention update)**: (1) **This Decision is superseded by `[D-033]`** (2026-05-13): the LLM-first design described here is replaced by a **two-tier approach** — fuzzy match (first-page text against item names/descriptions/`item_type`) → LLM fallback only when fuzzy match cannot resolve. Driver: ~100 reports per major milestone makes LLM-on-every-attachment costly; first-page conventions in test/tech reports give reliable fuzzy match in the common case. (2) Write path "`inbound/` subdirectory" is stale — current path convention is `<doc_type_slug>/<doc_id_slug>/revN/` (classified storage) per `[D-013]` impl note 2026-05-14 + `[D-039]` (SHA-256 + Tier-2 LLM duplicate/revision classification). `inbound/` is the NSD owner drop zone only; HILDA writes classified content to the `internal/` tree. (3) When the triggering email also fires FR-12 path (c), attachment routing is subsumed into the **fused LLM call** per `[D-034]` (single call covers message classification + attachment routing); the two-tier path applies only for FR-12 path (a)/(b) emails with attachments. PLM-polled documents always use the standalone two-tier path per `[D-033]` impl note 2026-05-13.

**Implementation note (2026-06-05 — supersession chain extended through FR-52 5-step pipeline)**: The supersession chain for inbound email attachment routing is now: **D-030 (LLM-first) → D-033 (two-tier fuzzy+LLM) → FR-52 amendment 2026-05-28 (5-step pipeline per `[D-053]`)**. The current routing is per FR-52: (1) strict substring on `item_description` comma-separated tags → (2) fuzzy match on `item_name` → (3) source-folder → work-item template (TG opt-in) → (4) LLM `ROUTE_ATTACHMENT` → (5) staged to default work-item. `doc_type` is no longer LLM-inferred (removed per `[D-052]` impl note 2026-05-28b) — it derives 1:1 from `item.item_type` per `[D-053]`. The "owner-typed attachment annotation" rejection in the original Why remains correct rationale. Path conventions are now per FR-13 amendment 2026-05-28 (`<NSD1>/internal/<carrier>/<device>/<milestone>/<tg>/<item>/<doc_type>/<doc_id_slug>/revN/`).

---

## D-031: Template Schema Ingestor deferred to Ph-2 — Excel paths consistently out of Ph-1
**Status**: Active · **Date**: 2026-05-12
**Decision**: Both Excel-based workflow paths are deferred to Ph-2 as a consistent set: (a) FR-1 path (b) — tracker creation from Excel import; (b) FR-39 path (b) — customer template authoring via Excel upload. The Template Schema Ingestor module (`[D-010]`, `[D-018]`) has no Ph-1 use case and is a Ph-2 module. Ph-1 uses SharePoint UI for template authoring (FR-39 path a) and template-based or manual entry for tracker creation (FR-1 paths a and c).
**Why**: FR-4 (Excel import schema validation before SharePoint write) was already deferred to DEF-15. Completing the tracker-creation Excel path without the validation gate would mean unguarded writes to SharePoint — worse than deferral. Template authoring via Excel (FR-39 path b) requires the Ingestor to generate the per-customer schema first; with FR-1 path (b) also deferred, the Ingestor has no Ph-1 consumer at all. Deferring together creates a clean scope boundary: Ph-1 is SharePoint-UI-native for all authoring and creation surfaces; Ph-2 adds the full Excel round-trip (Ingestor generates schema, PM team lead uploads, tracker can be imported from Excel). Splitting — deferring one Excel path but not the other — would leave the Ingestor partially deployed for one use case, adding deployment complexity for limited Ph-1 payoff.
**Consequences**: Template Schema Ingestor module is Ph-2 — no MODULE.md, no implementation, no deployment in Ph-1. `[D-014]` (two-path authoring) and `[D-018]` (Ingestor input format) remain valid design intent for Ph-2; neither needs to be superseded. FR-1 and FR-39 carry explicit per-path `[Ph-1]`/`[Ph-2]` tags to make the boundary visible in requirements. DEF-15 revisit trigger updated: "when Excel import is in scope for Ph-2."

---

## D-032: DEF-2 Ph-1 boundary — initial one-pass LLM document review only; feedback workflow deferred
**Status**: Active · **Date**: 2026-05-12
**Decision**: DEF-2 (LLM tech-report and waiver quality review with PM-actionable feedback) is partially promoted to Ph-1. Ph-1 scope (FR-53): runtime LLM performs one initial quality review of an inbound test report, tech report, or waiver against the per-customer checklist generated by the Test Report Profiler (`[D-011]`); findings are displayed on the PM dashboard. Ph-1 explicitly excludes: tracking what the PM does with the findings; managing PM-to-owner revision communication; maintaining document revision history; re-running the review on subsequent document versions. Those behaviors remain deferred as DEF-2 remainder alongside FR-17 (revision tracking, Ph-2).
**Why**: PMs need LLM findings at document receipt time — without Ph-1 review, every Ph-1 PM manually reads every inbound document before deciding on next steps. The initial review is a bounded, read-only LLM operation: checklist + document excerpt → findings list. No downstream state changes, no revision tracking, no multi-round LLM calls. The feedback workflow (PM reviews findings → contacts owner → owner submits revision → system tracks revision → LLM re-reviews) is qualitatively more complex: it requires document versioning, revision history data model, and multi-round LLM orchestration — appropriate as a Ph-2 capability built on the Ph-1 baseline. Splitting the boundary avoids blocking the simple, high-value case (initial findings) on the complex case (full revision workflow).
**Consequences**: FR-53 delivers initial findings display only — no "track response," no revision queue, no re-review trigger in Ph-1. FR-17 (revised versions stored + re-parsed) remains Ph-2. DEF-2 remainder scope: PM feedback tracking, owner revision communication, multi-version re-review — revisit Ph-2. Runtime LLM Ph-1 API surface includes `review_document(excerpt, checklist) → findings` but not `re_review_revision(...)` or feedback-tracking endpoints.

---

## D-033: Attachment routing — two-tier (fuzzy match → LLM fallback); scale ~100 reports per milestone
**Status**: Active · **Date**: 2026-05-13
**Decision**: FR-52 attachment routing uses a two-tier approach, superseding `[D-030]`'s LLM-first design: (1) **Fuzzy match (text extraction)** — extract first-page text from each attachment and fuzzy-match against item name, description, and `item_type` within the batch; high-confidence matches are applied without an LLM call; (2) **LLM fallback** — only for attachments where fuzzy matching cannot resolve the mapping, call the runtime LLM with the extracted content + batch DeliveryItem list to determine the match. The two-tier process is bypassed entirely when the triggering email also fires FR-12 path (c) — in that case, attachment routing is subsumed into the fused LLM call (see `[D-034]`).
**Why**: ~100 reports per major milestone makes LLM-on-every-attachment costly and slow. Test reports and tech reports carry consistent first-page conventions (document title, test scope, technology area, customer reference number) that are sufficient for reliable fuzzy matching in the common case, with no LLM call. The LLM fallback handles residual cases where document headers are ambiguous or follow lab-internal naming (e.g., `A0000-23456.docx` with no title page). Two-tier balances cost, latency, and accuracy: cheap fast path for the majority, accurate fallback for the minority. `[D-030]`'s core insight — first-page content contains enough semantic signal — is preserved; the new insight is to extract that signal cheaply via fuzzy match first and pay LLM cost only when needed.
**Consequences**: Attachment-router sub-module (within Email Service or standalone) implements: (a) first-page text extractor per file type (PDF → `pdfplumber`/`pymupdf`; Word → `python-docx`; Excel → header-row extraction); (b) fuzzy-match scorer against DeliveryItem name + description + `item_type`; (c) LLM call only for unresolved cases, same interface as `[D-030]`. Fuzzy and LLM confidence thresholds are independently configurable rule parameters. PM dashboard manual-assignment surface receives: LLM-below-threshold cases and any truly unresolvable attachments. Negative tests required: opaque filename + clear title page → correct fuzzy match, no LLM call emitted; ambiguous title page → LLM fallback fires; unresolvable → PM assignment surface. Authority for FR-52.
**Implementation note (2026-05-13)**: Scope extended to cover corp PLM poll source (FR-26) in addition to email attachments (FR-12). PLM preserves owner-assigned filenames — filename contributes meaningful fuzzy-match signal for PLM-polled documents; email attachment filenames may be opaque lab IDs where first-page text is the primary signal. The fused LLM call (`[D-034]`) applies only to email path (c); PLM-polled documents always use the standalone two-tier path. FR-52 is the single routing mechanism for all inbound document sources.

---

## D-035: Corp PLM as document source of truth; one issue per (owner × milestone); HILDA auto-upload on document write
**Status**: Active · **Date**: 2026-05-13
**Decision**: Corp PLM is the authoritative document repository for all owner deliverables. Design: (a) **Issue granularity** — one PLM issue is created per (owner × milestone) at tracker creation, not per DeliveryItem; all DeliveryItems for the same (owner, milestone) share the same `plm_id` (denormalized on DeliveryItem); (b) **Auto-upload** — on every document write to a DeliveryItem's `inbound/` folder — from any source (email attachment routed by FR-52, direct owner drop-folder write detected by FR-55) — HILDA automatically attaches the file to the owner's PLM issue via the IssueTracker adapter; (c) **Document-only** — corp PLM stores documents only; it does not track item status back to HILDA; status flows via email (FR-12), messenger (FR-54), or customer API (FR-21); (d) **Owner direct access** — owners have filesystem write access to the `inbound/` subdirectory of their assigned items and may also upload via the PLM system interface directly.
**Why**: PLM is the existing corporate system of record — documents stored only on the shared drive without PLM registration are invisible to the broader organisation (quality teams, customer compliance review, audits). One issue per (owner × milestone) mirrors how PLM tracks work: by assignee within a milestone scope, not by individual sub-items. Keeping PLM document-only avoids duplicating status state between PLM and HILDA's SharePoint Lists, which would create a two-master consistency problem. Auto-upload removes PM burden — without it, PM would need to manually attach each document to PLM after it lands on the shared drive.
**Consequences**: FR-2 creates PLM issues at tracker creation per (owner × milestone). FR-5 adds `plm_id` uniqueness invariant: same value for all (milestone_id, owner) DeliveryItems. FR-13 updated to include auto-upload trigger on every `inbound/` write. FR-25 updated: IssueTracker Protocol serves dual role (corp PLM + internal Jira). FR-26 rewritten: PLM issue lifecycle (document-only, one per owner×milestone) separated from InternalJira tracking_modality (per-item status sync). Corp PLM adapter is generated by API Spec Ingestor per `[D-003]` (proprietary system). `plm_id` field on DeliveryItem is denormalized — constraint enforced at write time: all items created for the same (owner, milestone) receive the same `plm_id`; the `plm_id` for an (owner, milestone) pair is minted at tracker creation when the first PLM issue for that pair is opened.
**Implementation note (2026-05-13)**: PLM document flow direction depends on `tracking_modality`: when `CorporatePLM` is in the modality list, owner uploads to PLM → HILDA polls PLM → copies to shared drive (pull); when `CorporatePLM` is NOT in the modality list (Email, NetworkSharedDrive), HILDA receives documents first → auto-pushes to PLM (push). IssueTracker Protocol serves corp PLM (document pull/push) and customer JIRA (closure polling) only — no internal Jira tracking in v1. `tracking_modality` is multi-value per DeliveryItem; five values: Email, CorporateMessenger, CorporatePLM, NetworkSharedDrive, CustomerJIRA. Network drive path convention confirmed as `\\share\hilda\<carrier_slug>\<device_slug>\<milestone_slug>\<item_slug>\` (carrier = customer/certification body); `revisions/` subdirectory holds subsequent versions of documents already present in `inbound/`, version-distinguished by timestamp or version prefix.
**Implementation note (2026-05-14 — PLM creation timing)**: PLM issue creation moved from tracker creation (FR-2) to collection kickoff (FR-8) — HILDA creates one PLM issue per (owner × milestone) when PM/TPM triggers Start Collection from the SharePoint milestone view, not when the template is first loaded. FR-2 no longer creates PLM issues; FR-5 `plm_id` invariant unchanged. Creation is idempotent — re-triggering does not duplicate issues.
**Implementation note (2026-05-14)**: NSD path convention updated per FR-13 rewrite — `inbound/` is the NSD-channel drop zone only (not the primary write location for all sources); initial document receipt for all ingest sources (email, PLM poll, NSD) is written to `<doc_type_slug>/<doc_id_slug>/rev1/`; subsequent revisions go to `<doc_type_slug>/<doc_id_slug>/revN/` (N ≥ 2); revision numbering is sequential by upload order — no timestamp or version prefix in the folder name.

**Implementation note (2026-05-26 — D-040 supersession explicit; delete_attachment + Ph-2 deferred upload)**: (1) **`[D-040]` (2026-05-17) supersedes the universal PLM-source-of-truth model for Ph-2**: NSD classified path is the source of truth for in-progress owner deliverables; PLM is the source of truth for **submitted deliverables only** (post-OwnerClosed + TPM approval). This Decision (D-035) governs Ph-1 mechanics (immediate PLM upload on document write) + issue-granularity rules (one issue per `(owner × milestone)`) — both unchanged in Ph-2. (2) **Submission assembly source is NSD** per `[D-041]` (2026-05-17) — assembly never reads from PLM. (3) `delete_attachment` method added to `IssueTracker` Protocol `[D-008]` per `[D-040]` / FR-67 (PLM stale-attachment deletion when superseded revisions need removal after a re-submission with updated documents). (4) **Ph-2 deferred PLM upload** fires via FR-68 sync verification loop after OwnerClosed + TPM approval; only the `is_final = true` revision is uploaded (intermediate revisions stay on NSD only — PLM never sees them). (5) Corp PLM access in HILDA goes through the `corp_plm_gateway` application on the PLM gateway PC per `[D-021]` impl note 2026-05-24 — `core/src/issue_tracker/<proprietary>_adapter.py` (Ingestor-generated) is an in-process HTTP client of the gateway, not a direct corp-PLM client.

---

## D-036: Inbound email sender validation — attribution only, not a hard rejection
**Status**: Active · **Date**: 2026-05-13
**Decision**: The sender email address of every inbound email is captured in `CommunicationLog` for attribution alongside the `BATCH-id`. If the sender does not match the registered owner email for that BATCH-id, the status update is applied and a `Sender mismatch` note is surfaced on the PM dashboard for review. The system never rejects an inbound update solely on sender mismatch. PMs can configure stricter enforcement via `AutomationRules` if their workflow demands it.
**Why**: Rejecting on sender mismatch breaks legitimate scenarios: owner replies from a mobile alias vs. desktop address; owner's assistant replies on their behalf; PM forwards the email and owner replies to the forward. `BATCH-id` is already the authoritative routing key — sender email is supplementary signal. Hard rejection would silently drop valid updates, which is worse than accepting a false positive. Attribution-only keeps the audit trail complete (`CommunicationLog` records who actually sent each update) while leaving edge-case enforcement to configurable rules rather than hard-coded behaviour. Vs. hard gate: too brittle for multi-device / delegated-reply scenarios. Vs. no validation at all: loses attribution signal and makes spoofed BATCH-id replies invisible to the PM.
**Consequences**: Email Service records `sender_email` on every inbound `CommunicationLog` entry. Mismatch detection is a rule condition available in `AutomationRules` (action: surface flag on dashboard). Negative tests required: mismatch → update applied + flag surfaced; match → no flag; strict rule configured → PM-defined action fires on mismatch. Authority for FR-12 sender-attribution paragraph and FR-24.

---

## D-037: tracking_modality is a multi-value list per DeliveryItem; five v1 values
**Status**: Active · **Date**: 2026-05-13
**Decision**: `tracking_modality` is stored as a list (not a single enum value) per DeliveryItem. Five values in v1: `Email` (status + documents via email), `CorporateMessenger` (status only; no attachments), `CorporatePLM` (documents only; owner uploads to PLM; HILDA polls and copies to shared drive; document arrival triggers state transition), `NetworkSharedDrive` (documents only; owner drops in shared drive `inbound/` folder; HILDA polls; document arrival triggers state transition), `CustomerJIRA` (HILDA polls customer JIRA API for waiver/issue closure status; no outbound; no attachments). `CorporateMessenger` escalation (FR-10) is automatic when email goes unanswered and is not a modality value itself. Valid combinations require at least one status-capable modality for items needing status tracking and at least one document-capable modality for items with artifact deliverables.
**Why**: A single-enum model cannot represent items that receive status via one channel and documents via another — the common case (e.g., owner replies by email for status but uploads test reports directly to PLM or the shared drive). Making it a list lets each modality declare its own inbound handler and polling behaviour independently. Vs. separate `status_modality` + `document_modality` fields: two fields add schema complexity without benefit — a list with clear per-value semantics is simpler and extensible. Vs. single enum with composite values (e.g., `Email+PLM`): combinatorial explosion as values are added.
**Consequences**: `DeliveryItem.tracking_modality` stored as an array column or junction table — architecture-phase choice. FR-7 declares extensibility. Each modality value maps to one or more inbound handlers: `Email` → FR-12; `CorporateMessenger` → FR-54; `CorporatePLM` → FR-26 PLM poll; `NetworkSharedDrive` → FR-55 folder poll; `CustomerJIRA` → FR-21 JIRA poll. Rule engine and outbound logic branch on modality list membership. `template_schema/enums.py` `TrackingModality` registry updated to reflect five values before next development trip. Authority for FR-7.

**Implementation note (2026-05-26 — FR-21 deferred; FR-25 rewritten; FR-54 Ph-2; storage choice resolved)**: (1) "`CustomerJIRA` → FR-21 JIRA poll" is stale — **FR-21 was deferred to DEF-19** during the 2026-05-19 architecture review (automated customer feedback capture deferred to Ph-3+; TPM manually monitors the carrier portal in Ph-1/Ph-2). (2) `CustomerJIRA` modality polling is now **FR-25** (rewritten 2026-05-20): per-device-model polling (milestone-agnostic); HILDA polls customer JIRA REST API at deadline-tiered cadence per the shared `polling_schedule` AutomationRule; live open-ticket list surfaced on PM dashboard per `(carrier_slug, device_slug)` pair; close-intent flow advances item to `OwnerClosed` on owner email + JIRA re-query (JIRA closure is advisory, not a hard gate). (3) "`CorporateMessenger` → FR-54" — FR-54 (corp messenger inbound processing — runtime LLM classification + manual triage flag) is **Ph-2** per `[D-029]` impl note 2026-05-13; Ph-1 corp messenger use is outbound-only (escalation per FR-10). (4) `tracking_modality` storage choice — **resolved**: stored as a JSON array on the `DeliveryItem` Pydantic model + Postgres mirror column per `core/src/storage/MODULE.md` (2026-05-26 draft); no separate junction table needed at Ph-1/Ph-2 scale.

---

## D-034: Fused LLM call for FR-12 path (c) + attachments
**Status**: Active · **Date**: 2026-05-13
**Decision**: When FR-12 path (c) fires on an email that also contains attachments, attachment routing (FR-52) is subsumed into the same LLM call as the path (c) message classification. A single **fused call** receives: (a) the email body text; (b) first-page excerpts from each attachment; (c) the batch DeliveryItem list. The LLM resolves both the message classification (status/comment → which item) and the attachment routing (which attachment → which item) in one pass. The FR-52 two-tier process (`[D-033]`) runs only when the triggering email does NOT fire path (c) — i.e., when path (a) or path (b) handles the email body but the email also carries attachments.
**Why**: When an owner sends a free-text email with attachments, the email body often explains what the attachments are (e.g., "see attached updated test report for item 3"). This cross-signal — body context + attachment content together — gives higher classification and routing accuracy than two independent calls. A fused call also costs less (one LLM call vs. two) and avoids race-condition risks between message-classification and attachment-routing writes. The two-tier fuzzy-match path (`[D-033]`) still applies for path (a)/(b) emails with attachments, where the body is already structured and no LLM classification is needed.
**Consequences**: Email Service dispatch logic: (1) attempt path (a) structured block parse — if matched, process body; run two-tier attachment routing per `[D-033]` for any attachments; (2) attempt path (b) subject-line tap-link parse — if matched, same; (3) if neither (path c): gather first-page attachment excerpts; fire single fused LLM call covering body + excerpts + item list; apply results for both message classification and attachment routing. PM manual-assignment surface covers: path (c) below-threshold message classification, path (c) unresolved attachment routing, and `[D-033]` fuzzy+LLM-unresolved cases from path (a)/(b) emails. Negative tests required: path (a) match + attachments → two-tier only, no fused call; path (c) no attachments → classification-only LLM call; path (c) + attachments → fused call; fused-call split confidence → auto-apply high-confidence results, PM surface low-confidence results. Authority for FR-12 path (c) and FR-52 fusion case.

---

## D-039: Content hash (SHA-256) + Tier 2 LLM for document duplicate detection and new-vs-revision classification
**Status**: Active · **Date**: 2026-05-17
**Decision**: HILDA uses SHA-256 content hash (`file_hash`) combined with a two-tier slug + LLM classification pipeline for document duplicate detection and new-vs-revision classification at ingest time. The same four-step pipeline applies to **all** ingest sources — email, NSD `inbound/`, and PLM poll (PLM-polled documents no longer use a separate `(file_name, timestamp)` mechanism). Classification steps:

- **Step 0 — Exact duplicate check**: `file_hash` matches an existing document index row for `(delivery_item_id, doc_type)` → exact duplicate: skip, log in `CommunicationLog`, notify PM.
- **Step 1 — Slug match**: slugify `original_filename` → `candidate_slug`; if `candidate_slug` matches an existing `doc_id_slug` for this `(delivery_item_id, doc_type)` → revision (`revN`, N = max `rev_number` + 1); write to `<doc_type_slug>/<doc_id_slug>/revN/`.
- **Step 2 — Tier 2 LLM identity comparison** (fires when Step 1 has no slug match): HILDA calls the on-prem code-generation LLM with the incoming document's `first_page_excerpt` (first-page text extracted at ingest time) and the list of existing `(doc_id_slug, first_page_excerpt)` pairs for this `(delivery_item_id, doc_type)` from the document index. The LLM returns one of: `REVISION:<doc_id_slug>` (the incoming document is a revised version of an existing `doc_id_slug`, e.g. the owner re-named the file `filename1_v2.pdf` but the content is the same document as `filename1.pdf`); or `NEW_DOCUMENT` (no content match found). REVISION result → write to `revN/` under the matched `doc_id_slug`; NEW_DOCUMENT result → write to `rev1/` under `candidate_slug` as a new `doc_id_slug`.
- **Step 3 — Staged/ambiguous** (fires when Tier 2 LLM confidence is below threshold or returns ambiguous): document is held in `<doc_type_slug>/<doc_id_slug>/staged/` on the NSD; `Document classification ambiguous` flag surfaced on the PM dashboard for manual assignment; PM resolves and HILDA moves to `revN/` or `rev1/` accordingly.

Document index fields: `file_hash` (SHA-256 hex digest) and `first_page_excerpt` (first-page text extracted at ingest, stored at rev1 for each `doc_id_slug` and updated on each revision) are stored after `original_filename`. `is_final` (boolean, set by TPM approval via FR-66 or implicit for Ph-1 rev1) is stored after `first_page_excerpt`.

**Why**: Filename alone is unreliable — owners use opaque lab IDs and common variant naming patterns (`filename_v2.pdf`, `filename_latest.pdf`) that slug-normalisation cannot resolve to the same `doc_id_slug`. The same file frequently arrives simultaneously via email and PLM poll. Step 0 (content hash) is the cheapest and most reliable duplicate guard; Step 1 (slug match) handles the common case of stable filenames at zero LLM cost; Step 2 (Tier 2 LLM) handles the residual variant-name cases reliably using semantic first-page content comparison; Step 3 (staged/) avoids corrupt classification when the LLM is uncertain. Using the same pipeline for all three ingest channels (email, NSD, PLM poll) eliminates the previous inconsistency where PLM used a `(file_name, timestamp)` mechanism that did not detect cross-channel duplicates. Vs. filename + file size: still fails for same-size files with minor content changes. Vs. timestamp: channel-dependent, unreliable across email/PLM/NSD.
**Consequences**: `file_hash`, `first_page_excerpt`, and `is_final` added to document index schema after `original_filename`. Hash and first-page text computed at ingest time before message queue enqueue. Tier 2 LLM used is the on-prem code-generation LLM per `[D-007]` (the same model used by the Ingestors / Profiler — not the runtime LLM). `first_page_excerpt` is stored once at rev1 for a new `doc_id_slug`; subsequent revisions store their own `first_page_excerpt` so the comparison uses all historical first pages. Staged store tracks `(file_hash, original_filename, ingest_source, delivery_item_id, doc_type, first_page_excerpt)` pending PM resolution. PLM-polled document classification is aligned with email/NSD — no separate mechanism. Authority for FR-17, FR-26, FR-52, FR-55 Ph-2 classification behavior.

---

## D-040: Source-of-truth split — NSD classified path for in-progress; PLM for submitted deliverables
**Status**: Active · **Date**: 2026-05-17
**Decision**: Two distinct sources of truth exist for owner deliverables, gated by lifecycle phase: (a) **NSD classified path (`<doc_type_slug>/<doc_id_slug>/revN/`)** is the source of truth for in-progress owner deliverables — from initial ingest through `OwnerClosed` and TPM approval; all reads (downloads, assembly, review) use this path; `[Ph-2]` PLM upload is deferred until after `OwnerClosed` + TPM approval; (b) **Corp PLM** is the source of truth for submitted deliverables only — it holds the approved final revision after the deferred upload fires post-`OwnerClosed` + TPM approval (FR-68), and becomes the customer-portal mirror after dispatch (FR-18). This supersedes `[D-035]`'s PLM-as-universal-source-of-truth model for Ph-2; Ph-1 behavior (immediate PLM upload for all sources) is unchanged and `[D-035]` governs Ph-1.
**Why**: In Ph-2, multiple revisions accumulate in NSD before the owner confirms done. Uploading every revision to PLM immediately creates audit noise in the customer-facing PLM system and complicates the stale-attachment problem (multiple `revN` files attached to one PLM issue, customer sees all revisions). Deferring PLM upload until the TPM-approved final revision is selected (FR-66) means PLM always holds exactly the file that was submitted — a clean, auditable mirror of the customer portal. NSD serves as the working storage and version history; PLM serves as the submission record. Vs. PLM-as-always-source-of-truth: PLM availability becomes a dependency for all reads including dev/test; revision noise visible to customers; stale-attachment cleanup needed before every submission. Vs. NSD-as-always-source-of-truth (no PLM sync): PLM is the existing corporate system of record; not syncing would make submitted deliverables invisible to quality teams and auditors.
**Consequences**: FR-13, FR-26, FR-52, FR-55 updated with Ph-1/Ph-2 PLM upload phase split. FR-18 assembly source updated to NSD classified path for both phases. FR-61 download links resolve to NSD classified path. FR-68 sync verification loop uploads final revision to PLM post-dispatch. D-035 governs Ph-1 mechanics and issue-granularity rules — those are unchanged.

---

## D-041: Assembly source is NSD classified path in both phases
**Status**: Active · **Date**: 2026-05-17
**Decision**: Submission package assembly (FR-18) reads documents from the **NSD classified path** (`<doc_type_slug>/<doc_id_slug>/revN/`) in both Ph-1 and Ph-2, not from PLM. HILDA downloads selected files from NSD to the HILDA PC (where HILDA services run) and assembles the package locally before dispatching to the customer portal. `[Ph-2]` only revisions with `is_final = true` are eligible for assembly. The customer portal directory structure (file naming, folder layout) per customer is defined in `customizations/<customer_slug>/portal_structure.yaml` (FR-69). After successful dispatch, the deferred PLM upload fires via the FR-68 sync verification loop.
**Why**: PLM availability is not guaranteed at dispatch time — depending on PLM as the assembly source introduces a hard runtime dependency on the PLM system being accessible and the correct attachment being queryable exactly at submission. NSD (locally mounted on the HILDA PC) has no such availability concern. Additionally, in Ph-2 the PLM upload is deferred to after OwnerClosed + TPM approval, so the final approved revision may not yet be in PLM when FR-63 Submit to Carrier fires. Using NSD as the assembly source removes the circular dependency between assembly and PLM upload. Vs. PLM-as-assembly-source (current Ph-1 behavior in FR-18): creates dependency on PLM availability at assembly time; incompatible with Ph-2 deferred PLM upload. Vs. assembling from whatever source first: introduces channel inconsistency.
**Consequences**: FR-18 updated: assembly source = NSD `<doc_type_slug>/<doc_id_slug>/revN/`; HILDA PC downloads from NSD first, then dispatches. FR-61 download links already resolve to NSD classified path (consistent). `[D-035]` PLM source-of-truth statement applies post-submission only (for audit, customer access, and PLM-side visibility) — not at assembly time.

---

## D-042: Customer template storage and TPM authoring path — OPEN design question
**Status**: Open · **Date**: 2026-05-21
**Question**: How are customer templates (the YAML/JSON files defining standard milestones and DeliveryItems per customer) stored, and how does a TPM create or edit one without developer involvement?
**Context**: FR-39/FR-40 define template authoring. Two options identified during requirements review: (A) YAML files under `customizations/template_schemas/<customer_slug>/` — version-controlled, ops/developer edits them, consistent with `portal_structure.yaml` pattern; TPM cannot self-edit in Ph-1/Ph-2; (B) SharePoint List — TPM edits directly via SP UI forms; not version-controlled.
**Leaning**: Option A for Ph-1/Ph-2 — templates change infrequently (new customer or new device model); ops involvement is acceptable; TPM day-to-day adjustments go through FR-3 (add/remove items on live tracker) and FR-14 (field overrides), not template edits; DEF-11 self-service wizard (v4) is the long-term TPM authoring path.
**Must resolve during architecture**: (1) Exact YAML schema for template files (milestones, items, tg_name groups, static field defaults, doc_count, tg_owner, default_cc_list); (2) how HILDA reads the template at tracker creation (FR-1/FR-2) — file-based loader vs SP List; (3) whether a new device model requires a new template file or parameterises an existing one; (4) ops workflow for adding a new customer template.
**Anchors**: FR-1, FR-2, FR-39, FR-40, DEF-11.

---

## D-043: Message broker — Redis (Ph-1/Ph-2) → RabbitMQ + Rook/Ceph + MetalLB (Ph-3)
**Status**: Active · **Date**: 2026-05-22
**Decision**: Ph-1 and Ph-2 use **Redis as the Celery broker** in addition to its cache/dedup role (per `[D-022]`). Ph-3 MicroK8s migration replaces Redis as broker with **RabbitMQ Quorum Queues** deployed via the RabbitMQ Cluster Operator. Redis is retained in Ph-3 as cache-only. Rook/Ceph RBD PVCs provide durable block storage per RabbitMQ node. MetalLB provides the `LoadBalancer` VIP for external access on bare metal; internal cluster traffic remains `ClusterIP`.
**Why Redis for Ph-1/Ph-2**: Single bare-metal PC, Docker Compose, low task throughput. Redis is already in the stack (per `[D-022]`); marginal cost as broker is zero. No need for HA broker guarantees at single-machine scale.
**Why RabbitMQ for Ph-3**: In an HA multi-node K8s cluster, RabbitMQ Quorum Queues use Raft consensus — a message is acknowledged only after a majority of nodes confirm the write; in-flight messages survive node failure. Redis broker uses async replication; messages in-flight during a failover can be lost. RabbitMQ Cluster Operator on K8s is the official, mature HA path. Rook/Ceph gives each RabbitMQ node durable storage that survives pod eviction and node loss. MetalLB is required for `LoadBalancer` service type on bare metal (no cloud provider).
**Celery result backend**: Postgres in both phases — unchanged from `[D-022]`; Redis is not used as result backend.
**Temporal**: Deferred to Ph-3+ per `[D-022]`; if Temporal is adopted, RabbitMQ + Temporal Workers may replace Celery + hilda-beat for durable orchestration — to be evaluated at Ph-3 architecture.
**Migration path Ph-2 → Ph-3**: Change `HILDA_CELERY_BROKER_URL` from `redis://...` to `amqp://...`; no Python code change required (Celery broker is URL-selected); deploy RabbitMQ Operator + cluster; decommission Redis broker role.
**Anchors**: `[D-022]`, `[D-026]`, NFR-15.

---

## D-038: v1 secrets encryption at rest — sops with age keys
**Status**: Active · **Date**: 2026-05-13
**Decision**: v1 `.env` files containing PM credentials and service secrets are encrypted at rest using `sops` with `age` as the key provider. The age private key lives at `/etc/hilda/age.key` on the host (`chmod 400`, owned by the HILDA service user). Encrypted `.env` files may be stored in the repo — no plaintext secrets committed. The credential service decrypts at container startup into process memory via `sops --decrypt`. No Vault dependency in v1. mTLS for service-to-service communication remains a v2 K8s target per `[D-021]` / `[D-026]`.
**Why**: `[D-026]` established v1 = Docker Compose + `.env` files, which are plaintext by default. Plaintext credential files on a shared ops machine handling real PM credentials for customer certification systems violates the spirit of NFR-4. Full Vault (DEF-14) is a v2+ concern. `sops` with `age` provides AES-256-GCM encryption at rest with minimal operational overhead: key generation is a one-time op (`age-keygen`), encrypt/decrypt is a single CLI call, and sops-encrypted files are diff-friendly (values encrypted, keys in plaintext) making them reviewable in git. The age key is the only plaintext secret on the filesystem — a smaller, simpler attack surface than per-file plaintext secrets. Vs. Docker Compose native secrets (tmpfs mount): marginally better runtime isolation but still requires plaintext values on the host at provisioning time; no encryption at rest. Vs. ansible-vault: equivalent strength but ansible-ecosystem-specific; sops is standalone. Vs. accepted plaintext: rejected — on-prem machine with PM credentials for external customer systems warrants at-rest encryption in v1.
**Consequences**: Ops deploy workflow: `age-keygen -o /etc/hilda/age.key` once at first deploy; `sops --encrypt --age <pubkey> secrets.env > secrets.enc.env` per secrets file at provisioning time; `deploy.sh` calls `sops --decrypt secrets.enc.env` at startup to expose decrypted values as container environment variables. `sops` binary installed on the host as an ops tooling dependency (not a Python package). NFR-4 updated to reflect sops/age at-rest encryption + TLS for external comms + mTLS deferred to v2. FR-51 updated: "K8s Secrets" → "sops-encrypted `.env` files per `[D-038]`". Authority for NFR-4 and FR-51.

**Implementation note (2026-05-26 — Ph terminology; mTLS Ph-3+)**: (1) "v1" → **Ph-1/Ph-2 / Ph-3+** throughout. (2) "mTLS for service-to-service communication remains a v2 K8s target per `[D-021]` / `[D-026]`" — **mTLS is a Ph-3+ MicroK8s target** per `[D-021]` impl note 2026-05-24 + `[D-043]`. (3) sops + age remains the Ph-1/Ph-2 mechanism unchanged; Ph-3+ migration path: sops-encrypted env files → HashiCorp Vault per `[D-019]` v2 / DEF-14 (per-PM credential blobs). Env var names remain identical across the transition; `credential_service.get_credential(pm_id, system_type)` interface is preserved per `[D-019]` impl note 2026-05-24. (4) The age key on the HILDA PC filesystem (`/etc/hilda/age.key`) is the single secret that gates all other sops-encrypted material; rotation procedure is an ops runbook item — capture during `core/src/credential_service/MODULE.md` drafting.

---

## D-044: Documentation boundary policy — HILDA_Design.md / requirements.md / DECISIONS.md
**Status**: Active · **Date**: 2026-05-24
**Decision**: The three primary docs under `docs/compact/` carry distinct, non-overlapping roles. (a) **HILDA_Design.md** (under `design-inputs/`) is the conceptual / narrative design — WHAT the system is and WHY, including entity hierarchy, architecture diagrams, deployment topology, workflow stage narratives, configurability model, and roadmap. It is the onboarding doc for humans. (b) **requirements.md** is the testable behavioral spec — WHAT must work, with stable FR/NFR IDs, `[Ph-N]` phase tags, exact enums, lifecycle transitions, validation rules, and algorithm specifics. It is the build spec; `drift-check` reads it; it is authoritative for "did we build what we said?". (c) **DECISIONS.md** carries architectural decisions (ADRs) with stable `[D-NNN]` anchors and immutable history. Where HILDA_Design.md prose duplicates mechanics from requirements.md, the design doc uses a one-line summary plus an explicit "per FR-N" reference rather than restating the detail. Detailed semantics (enums, edge cases, retry rules, classification algorithms) belong in requirements.md, not HILDA_Design.md. When the same concept needs anchoring rationale, it lives in DECISIONS.md with a `[D-NNN]` reference.
**Why**: Without an explicit boundary, conceptual prose and testable specs drift apart and the same fact gets restated in slightly different ways across files. Mid-architecture review (2026-05-23 → 2026-05-24) we hit several drifts where HILDA_Design.md prose was stale relative to FR-2, FR-7, FR-9, FR-13 because both files tried to own the detail. Establishing the boundary lets each doc do one job and removes the ambient duplication risk. Vs. consolidating into a single doc: loses the FR/NFR ID anchoring needed by `drift-check` and the ADR anchoring needed by historical traceability. Vs. letting HILDA_Design.md become exhaustively prescriptive: makes onboarding unreadable and creates two competing sources of truth for behavior.
**Consequences**: All future updates to HILDA_Design.md prefer "per FR-N" / "per `[D-NNN]`" references over restating mechanics. `drift-check` rules (requirements mode) treat HILDA_Design.md as a non-authoritative narrative — drift is detected against `requirements.md` and `DECISIONS.md`, not against HILDA_Design.md prose. When a concept newly requires anchoring, the order is: add or update the FR/NFR in `requirements.md` first, add a `[D-NNN]` ADR if rationale needs locking, then summarize in HILDA_Design.md with references. Skill descriptions in `.claude/skills/` align with this — `close-session` writes journal entries → drafts decisions for `DECISIONS.md`; `drift-check` reconciles `requirements.md` against design inputs; HILDA_Design.md is updated by humans (or LLM under human direction) as the narrative drifts.

---

## D-045: Schema / content boundary invariant — data model gated by code release; YAML config gated by customer workflow
**Status**: Active · **Date**: 2026-05-24
**Decision**: HILDA's configurable surface splits cleanly into two zones with different governance: (a) **Zone A — code-release-gated (data model schema)**: new columns on SharePoint Lists, new `item_type` / `delivery_state` / `tracking_modality` / `doc_type` enum values, new entities. These require a versioned HILDA code release that touches `core/src` (Pydantic / SQLAlchemy models), SharePoint List provisioning, PostgreSQL mirror migration, YAML template-schema spec, template loader, and downstream consumers. Gated by HILDA dev/ops team. (b) **Zone B — YAML-edit-gated (configuration content within the existing schema)**: new customer template instance, new automation rule, new TG group, modified CC list, adjusted polling schedule, customer-specific submission format. These are routine edits under `customizations/` with no code change. Gated by the template-authoring / customer-config workflow per FR-30, FR-39/40/41, `[D-014]`. SharePoint admins **cannot** add columns by clicking in the SP UI; any field not in the canonical schema is not picked up by HILDA services.
**Why**: Without this boundary, "configurable" creeps into "schema-mutable" and accumulates as ungoverned data-model changes that the code base doesn't know about — silent failure modes (loader writes drop the field) or mismatched mirror/SP state. Mid-architecture review (2026-05-24) the question came up: when a new field is added, does it go in SP List + YAML? The clean answer is: schema changes are never YAML-only; they are a release. This locks down the invariant before implementation phase. Vs. dynamic-schema-from-YAML: rejected — adds an entire schema-evolution runtime that compromises type safety, drift-check, and provisioning automation for marginal flexibility gain. Vs. SP-UI-driven schema editing: rejected — bypasses code review, breaks Postgres mirror, breaks rule engine consumers.
**Consequences**: `HILDA_Design.md §3.5` formalizes the schema/content boundary and documents the release-time field-addition checklist. The YAML files in `customizations/` are deliberately scoped to "content within existing schema." Any new schema element triggers a coordinated release: canonical schema → SP provisioning → Postgres migration → YAML schema spec → loader → consumers. `drift-check` rules in the architecture and implementation modes detect schema-shape drift between `core/src` Pydantic models and (a) the SP List provisioning script, (b) the YAML template-schema spec, (c) the Postgres migration history. Anchors `D-046` (canonical schema source) and `D-044` (doc boundary).

---

## D-046: Canonical schema source — Pydantic models in `core/src` as single source of truth
**Status**: Active · **Date**: 2026-05-24
**Decision**: The single source of truth for HILDA's data-model schema is the **Pydantic model definitions** under `core/src/<module>/models.py` (exact module layout finalized during architecture phase). Three downstream artifacts are **generated from** the Pydantic models at release time, not maintained by hand: (a) **SharePoint List provisioning script** — emits the columns, types, and indexes for each SP List; (b) **PostgreSQL DDL / alembic migration** — for the Postgres mirror tables and the FR-31 runtime-override tables; (c) **YAML template-schema spec** — the JSON-Schema or Pydantic-derived spec that validates customer template YAML files at load time (per `[D-010]`, `[D-014]`, FR-39/40/41). A CI gate runs the generators on every PR and fails the build if (1) generated artifacts disagree with the checked-in versions or (2) a customer YAML file contains a field not present in the generated schema spec.
**Why**: Per `[D-045]`, schema evolution touches at least four artifacts. Hand-maintaining all four invites drift — a field is added to Pydantic but the SP provisioning script is forgotten, or the YAML schema spec lags behind. Code generation from a single source eliminates the entire class of drift. Pydantic is chosen as the source (vs. raw JSON Schema, raw SQLAlchemy, dataclasses) because: it's already the standard for SharePoint config validation per `[D-004]`, it's Python-native (the rest of HILDA is Python), it supports both serialization (JSON Schema export) and ORM-style consumption, and the dev LLM is fluent in Pydantic conventions. Vs. JSON Schema as source: rejected — Python consumers would need an extra binding layer; less ergonomic for the rest of the codebase. Vs. SQLAlchemy as source: rejected — too DB-centric; less natural for non-DB consumers (YAML validation, SP provisioning).
**Consequences**: A new field on DeliveryItems follows this flow (per `[D-045]` checklist): (1) update Pydantic model; (2) run `generate-sp-schema` → updates SP List provisioning script; (3) run `alembic revision --autogenerate` → updates Postgres migration; (4) run `generate-yaml-schema` → updates YAML template-schema spec under `customizations/template_schemas/_schema.yaml` (or equivalent); (5) update existing customer YAML files with default values for the new field; (6) update template loader and consumers; (7) publish versioned release. CI gates: schema-sync check on every PR; YAML-validation check against the generated spec on every customer-YAML PR. The Schema Evolution sub-section in `HILDA_Design.md §3.5` is updated to reference this ADR. Adds a small amount of release-time tooling (generators + CI gates) but eliminates the much larger cost of silent schema drift in production. Anchors `D-045`.

---

## D-047: SP → HILDA notification channel — SP alert email + `sp_alert_parser` + IMAP IDLE primary
**Status**: Active · **Date**: 2026-05-24
**Decision**: SharePoint 2017 → HILDA HTTP is **unconditionally firewall-blocked** by the corp/lab network boundary: SP server (corp intranet) cannot POST to `hilda-api` on the HILDA PC (lab subnet), and PM corp browsers also cannot directly XHR to `hilda-api`. The **only** SP → HILDA notification channel is **email**, delivered via SharePoint 2017's **built-in alert feature** configured on the deliverable-tracker list. Flow: (1) PM/TPM action in SP UI (direct list-field edit or button click — buttons are wired to modify a list field on click) → (2) SP alert fires → structured notification email to the HILDA dedicated mailbox (sender format `<List Display Name> <sharepoint@<corp-domain>>`) → (3) HILDA `email_service` consumes via **IMAP IDLE on Exchange (primary, ~1–2 s latency)** or **short-interval polling (5–10 s fallback)** if Exchange admin disables IDLE → (4) `sp_alert_parser` sub-module under `core/src/email_service/` deterministically extracts the structured key:value body (no LLM; rule-based regex) and the action verb from the sub-header (`has been added | modified | deleted`) → (5) parser routes the email to a DeliveryItem via the composite natural key `(ProjectID, MinorMilestone, ItemNumber)` per FR-5 uniqueness constraints → (6) dispatches a Celery task corresponding to the action (`start_collection`, `approve_item`, `manual_field_override`, etc.). HILDA writes results back to SP via outbound SP REST API + NTLM/Kerberos per `[D-006]` (outbound is always allowed). The PM web part on the corp workstation sees the write-back by polling SP REST API (corp-to-corp, sub-second), not by polling HILDA directly. The **FR-23 deadline-tiered polling schedule** is a third-tier fallback only, not the primary mechanism — interactive PM UX latency is bounded by the IMAP IDLE / short-poll path (~5–15 s end-to-end), not the deadline cadence (5–60 min). SP-side configuration requirement: the alert must be set to **"Send Alerts for These Changes: Anything changes"** so all TPM field edits fire alerts (not just specific columns); otherwise FR-14 manual overrides silently miss HILDA.
**Why**: HILDA_Design.md §6 assumes SP can be reached directly from SP-side workflow / Power Automate over HTTPS. The corp/lab network boundary makes that impossible regardless of HILDA PC's physical location — the constraint is the firewall posture, not on-prem-vs-outside-premises. Email is the only bidirectional channel that survives the firewall: HILDA can poll the corp Exchange mailbox outbound (allowed), and SP can deliver to the mailbox via Exchange. Vs. a corp-side reverse-proxy bridging SP → HILDA HTTP: rejected — would require an inbound firewall rule allowing corp → HILDA PC, which corp security policy does not grant for the HILDA service. Vs. a HILDA-initiated outbound tunnel (reverse-tunnel / Cloudflare-Tunnel pattern) carrying SP notifications: technically viable but adds operational machinery (tunnel daemon, persistent connection, monitoring) that email already provides at zero added infrastructure. Vs. HILDA polling SP REST API to detect changes: rejected — N×M polling (N items × M poll cycles) generates orders of magnitude more SP API traffic than alert emails, hits NFR-8 rate limits, and adds detection latency proportional to the poll interval. The SP-alert-email channel uses existing infrastructure (corp Exchange + SP's built-in alert feature) and delivers near-real-time latency via IMAP IDLE; no HILDA-side code change required if Exchange admin disables IDLE (degrades to short-poll). Linux IMAP IDLE is fully supported (`imapclient` library — standard production usage); the "Outlook desktop VBScript" pattern from prior projects is Outlook-client-side and doesn't transfer, but IMAP IDLE provides equivalent push semantics server-side. Choosing this channel commits HILDA to a stable, parseable email format (subject `Alert_<ListName>_<Suffix> - <ItemTitle>`, deterministic key:value body, action verb in sub-header) — confirmed against a live SP alert sample during the 2026-05-24 review.
**Consequences**: (1) **New sub-module** `core/src/email_service/sp_alert_parser.py` — deterministic regex parser for SP alert subject + body; emits structured `SPAlertEvent` Pydantic model with action verb + routing key + field deltas; routes to Celery task dispatch. Error-code prefix shared with `email_service` (EML-*). (2) **PM browser → HILDA HTTP for fast read operations** remains valid but routes through the **IT-admin's existing corp-side reverse proxy** (separate machine in corp net) — the proxy serves `hilda.corp/dl/*` and `hilda.corp/status/*` and forwards to `hilda-api` on the lab subnet via a narrow firewall exception scoped to that one proxy host. See SYSTEM.md §3 + `[D-013]` (NSD-mediated downloads) for the download path. (3) **FR-23 deadline-tiered polling_schedule** retained but downgraded in role: IMAP IDLE is primary; short-interval polling (5–10 s) is fallback; deadline-tiered cadence is third-tier fallback for the no-IDLE no-short-poll case (used for owner-reply emails where minutes-of-latency is acceptable). (4) **Schema/routing-key dependency**: the routing key `(ProjectID, MinorMilestone, ItemNumber)` requires SP-side `ItemNumber` to be **immutable** for the life of a delivery item; if SP UI engineer confirms otherwise (re-numberable when items are added/removed), an immutable `item_guid` column must be added to the SP list and included in the alert body — flagged as architecture-phase action item. (5) **SP deployment runbook requirement**: alert configured for **"Anything changes"** trigger; alert subscriber address = HILDA dedicated mailbox; alert format kept stable across SP migrations. (6) **Schema gap surfaced from alert sample**: `MilestoneGating` field appears in SP alert emails but is not yet in HILDA's DeliveryItem schema — flagged as architecture-phase action item. (7) **Prototype-vs-`requirements.md` field-name mapping**: SP alert fields use names like `ItemNumber`, `TeamName`, `DeliveryType` while `requirements.md` uses `item_no`, `tg_name`, `item_type`; resolved either by aligning prototype names to canonical schema or by a name-mapping translation layer in `sp_alert_parser` — architecture-phase decision. (8) SYSTEM.md §3.1 documents the channel; SYSTEM.md Conflicts table C6 marks this Decision as the resolution of the HILDA_Design.md §6 / §7 SP-direct-call assumption. (9) Anchors `[D-006]` (SP REST API + Kerberos outbound from HILDA — unchanged), `[D-021]` impl note 2026-05-24 (4-workload split adds `sp_alert_parser` capability to the worker), `[D-013]` (NSD-mediated downloads via the corp-side reverse proxy — separate but related architecture).

**Implementation note (2026-06-10 — see `[D-064]` for the outbound sister)**: This Decision (`D-047`) defines the **INBOUND** SP→HILDA notification channel (SP-alert email → `sp_alert_parser` → HILDA Celery dispatch). The **OUTBOUND** HILDA→SP state writeback channel is defined by `[D-064]` (HILDA→SP REST as the sole HILDA-initiated state writeback channel; firewall-asymmetric — outbound is unconstrained). Together `[D-047]` + `[D-064]` define the complete HILDA ↔ SP bidirectional channel discipline: SP→HILDA via email; HILDA→SP via REST. Neither direction has an alternative mechanism. SP UI surfaces HILDA's writes via focus-aware refresh (per SP UI engineer 2026-06-10); FR-87 TPM-resolution buttons round-trip is SP-UI-button → SP-field-write → SP-alert email (this channel, D-047) → `sp_alert_parser` → HILDA Celery dispatch → state mutation → SP REST writeback (D-064) → SP UI focus-refresh picks up the change.

---

## D-048: Multi-revision version-selection workflow — owner-mediated via corp messenger (Ph-2)
**Status**: Active · **Date**: 2026-05-26
**Decision**: When a DeliveryItem enters the transient `OwnerClosed` state in Ph-2 and the document index holds **multiple `revN` candidates** for the same `doc_id_slug` (per `[D-039]` revision classification + FR-17), the **owner** selects the final revision (the one that goes to the customer) via a corp messenger interactive flow before the item can advance to `UnderPMReview`. Mechanism per FR-66: HILDA fires the `TriggerVersionSelection` rule action on `OwnerClosed` entry (the transient fork per FR-7); `messenger` module sends a structured outbound message via the proprietary internal messenger adapter (routed through `corp_messenger_gateway` per `[D-021]` impl note 2026-05-24) listing each `doc_id_slug` with all `revN` candidates (file size, upload date, original_filename, first-page excerpt for context); owner replies with a structured selection (one `revN` per `doc_id_slug`); `messenger`'s reply parser → `storage` module `set_is_final()` sets `is_final = true` on the selected revision and `is_final = false` on all other revisions for that `doc_id_slug`. Once all `doc_id_slug` selections are confirmed, the item advances to `UnderPMReview`. TPM can override `is_final` in the SP UI per FR-56 Ph-2 — selecting a different revision sets `is_final = true` on that revision (atomically false on peers). In Ph-1, only one revision per `doc_id_slug` exists; `is_final = true` is auto-set; no version-selection flow runs; the item advances directly to `UnderPMReview`.
**Why**: Multiple alternatives were considered: (a) **Auto-pick latest revision by upload timestamp**: rejected — the owner is the authority on which revision is the intended final, not the system. Latest-by-timestamp incorrectly elevates a quick re-upload that happens to be later. (b) **TPM picks in SP UI**: rejected as the primary mechanism — TPM doesn't have direct visibility into owner intent; TPM would have to communicate offline with the owner anyway. Retained as an override path per FR-56 Ph-2 (the escape hatch when owner is unreachable). (c) **Auto-pick + manual TPM revert**: rejected — false-positive corrections after the fact are more disruptive than asking the owner up-front; the customer-portal mirror would briefly hold the wrong revision. (d) **Voting / committee selection**: out of scope for the workflow scale (one delivery engineer per item). The chosen corp messenger flow leverages the existing channel (FR-50 / FR-54), matches the owner-mediated authority model already established for `OwnerClosed` confirmation, and places version selection at the lifecycle gate — once at close, not on every revision. The transient-fork placement in FR-7 means single-revision items (the Ph-1 norm) skip the flow entirely.
**Consequences**: `messenger` Protocol per `[D-009]` gains a structured-request pattern (either as a new `send_structured_request` method or layered via `send_message` + `list_thread` + a parser sub-module). `storage` module per its 2026-05-26 MODULE.md draft: `set_is_final(delivery_item_id, doc_type, doc_id_slug, rev_number, is_final)` atomically updates the selected revision to `true` and all others for the same `(delivery_item_id, doc_type, doc_id_slug)` to `false`. `rule_engine` per FR-28: `TriggerVersionSelection` action; `WaitForVersionSelection` is a transient sub-state under `OwnerClosed`. State machine: `OwnerClosed` is transient → forks immediately to (a) `UnderPMReview` if single revision per `doc_id_slug` (Ph-1 case + Ph-2 single-rev case), or (b) `WaitForVersionSelection` if multi-rev (Ph-2). Timeout handling: configurable window (e.g., 48 hours); on expiry HILDA surfaces "VersionSelection pending" flag on PM dashboard; TPM override is the manual escape hatch. `corp_messenger_gateway` routing per `[D-021]` impl note: HILDA → corp Slack outbound goes through the gateway on the reverse-proxy PC; reply comes back inbound through the same gateway. Authority for FR-66. Depends on `[D-009]`, `[D-016]`, `[D-039]`, `[D-040]`.

---

## D-049: Owner Discovery Function (ODF) + per-TG-group field model
**Status**: Active · **Date**: 2026-05-26
**Decision**: Each `tg_name` group within a milestone carries metadata (`tg_owner_name`, `tg_owner_email`, `email_group_alias`, `corp_id_list`, `default_cc_list`) that is **TG-group-level — not per-DeliveryItem** — and applies to all items sharing that `tg_name` within the milestone. Source data lives in per-customer YAML at `customizations/template_schemas/<customer>/tg_groups.yaml` (per FR-2 / FR-71); runtime storage is the **TGGroups SP list per `[D-051]`**; lookup natural key is `(milestone_id, tg_name)`. The Owner Discovery Function (ODF) workflow per FR-71 (Ph-2): HILDA fires ODF at tracker creation, after per-milestone DeliveryItems are materialized but before Start Collection. One corp messenger message per TG group is sent to the TG's `corp_id_list` (or `tg_owner_email` via email if `email_group_alias` is set instead of individual outreach). The message lists all DeliveryItems in that TG with their currently-assigned `owner` (the delivery engineer per item, distinct from `tg_owner`). The TG coordinator (the recipient) replies with one of three structured forms: `confirmed` (no change — accept current assignments); per-engineer reassignment (`<item_id_or_name>: <old_owner> -> <new_owner>`; within-TG only — engineers don't work across TGs); or "tg_owner is also the delivery engineer for items X, Y, Z" (informational PM dashboard flag with confirmation prompt). On timeout, PM dashboard flag surfaces and the tracker proceeds with template defaults. SP UI surface per FR-71 + sharepoint/REQUIREMENTS.md §3.1: TG header rendered above each `tg_name` grouping displays current ODF status (Confirmed / Updated / Timed out / Pending); TPM can manually override `tg_owner` and other TG metadata via the TGGroups SP list edit form before ODF fires.
**Why**: Multiple alternatives were considered: (a) **Manual TPM entry per-engineer at tracker creation**: rejected — TPM doesn't know real-time R&D engineer assignments; this is exactly what the TG coordinator does day-to-day. (b) **No discovery; template defaults always**: rejected — template defaults go stale as R&D engineers change roles; first-month assignments drift; results in HILDA outreach hitting wrong owners. (c) **Per-item messenger discovery (one message per item)**: rejected — `N` items × `M` engineers messages overwhelms the channel; TG-level grouping is the natural scale of authority (a TG coordinator knows their TG's assignments). (d) **Per-TG messenger discovery (chosen)**: one message per TG coordinator; aggregates all items in the TG; reply is structured per-engineer; matches how R&D leads actually manage engineer assignments in practice. The "TG-group field is NOT per-DeliveryItem" model emerged from this design choice: items inherit TG metadata via the lookup; per-item duplication would be 20-40× redundant and 20-40× consistency risk on TG-coordinator changes.
**Consequences**: New TGGroups SP list per `[D-051]` (one row per `(milestone_id, tg_name)`). `customizations/template_schemas/<customer>/tg_groups.yaml` YAML schema additions per FR-2 + FR-71. `messenger` Protocol per `[D-009]` used for ODF outbound + structured reply parsing. `corp_messenger_gateway` routing per `[D-021]` impl note 2026-05-24 (HILDA → corp messenger outbound goes through the gateway). `rule_engine` per FR-28: new `TriggerODF` action; ODF status state per TG group. `HILDA_Design.md §3` data model extended with TG-group fields per `[D-028]` impl note 2026-05-26. `sharepoint/REQUIREMENTS.md §2.8` (TGGroups SP list) + `§3.1` (TG header rendering) added 2026-05-26 commit `1c102a9`. Ph-1 reality: ODF does not fire (Ph-2 only); HILDA uses template `tg_owner_name` / `tg_owner_email` as-is; TPM may still override via TGGroups SP list edit. **Anchors the TG-group field model that affects FR-2, FR-9 (outbound batch CC selection — `email_group_alias` replaces individual `owner_email` for TG outreach when set), FR-10 (corp messenger escalation — `corp_id_list` replaces individual owner corp-ID when set), and FR-14 (TPM override surface).** Depends on `[D-009]`, `[D-016]`, `[D-021]`, `[D-028]`, `[D-051]`.

---

## D-050: ZIP ingestion + 5-area NSD structure
**Status**: Active · **Date**: 2026-05-26
**Decision**: When owners deliver ZIP archives via any ingest channel (Email attachment, NSD `inbound/` drop, corp PLM upload), HILDA preserves both the original ZIP and per-file extracted content; routing applies per-extracted-file via the standard FR-52 two-tier classification `[D-033]`. Five-area NSD structure per FR-13 (rewritten 2026-05-21), all under `<carrier>/<device>/<milestone>/<tg_name>/<item>/`:

1. **`<item>/<original_zip_filename>.zip`** — NSD-sourced ZIPs (item known from owner's drop folder per FR-13 two-tree); written at item root after HILDA picks up from `inbound/`. Carrier submission per FR-18 Ph-2 includes these when `is_final` rule applies at the ZIP level.
2. **`<tg>/un-resolved-zip/<original_zip_filename>.zip`** — Email- or PLM-sourced ZIPs; TG-scoped because the item is not always knowable at ZIP receipt time (a single ZIP can contain files for multiple items in the same TG). Permanently stored here (never moved). NOT submitted to carrier directly — used for routing-resolution audit only.
3. **`<tg>/<item>/<doc_type>/<doc_id_slug>/revN/`** — classified storage for individual files extracted from ZIPs OR received non-ZIP. Same path as the standard non-ZIP ingest write path (per `[D-013]` impl note 2026-05-14 + `[D-039]`). Document index marks `from_zip = true` + `source_zip_filename` for extracted files.
4. **`<tg>/<item>/<doc_type>/staged/<original_filename>`** — `[D-039]` Tier-2-ambiguous holding; PM resolves.
5. **`<tg>/<item>/outbound/`** — HILDA-generated artifacts only.

The ZIP itself functions as a temporary container during routing: HILDA extracts on receipt, runs each contained file through FR-52 with the same routing logic as non-ZIP attachments (fuzzy → LLM fallback), writes classified content per area 3, and preserves the original archive per area 1 or 2 depending on source.

**Why**: Multiple storage layout alternatives were considered: (a) **Single global ZIP store** (no per-item or per-TG split): rejected — Email/PLM ZIPs frequently span multiple items in the same TG (one owner uploads a single archive for their week's work); a global store loses the routing context needed at audit time. (b) **Per-item only** (no TG-scope fallback): rejected — Email/PLM ZIPs can't always be routed to a specific item at receipt (filename may be opaque, body may be ambiguous); a fallback is required, and per-TG is the natural fallback (one TG = one delivery engineer = one ZIP author typically). (c) **Per-TG only** (no per-item area): rejected for NSD-sourced ZIPs — when the owner explicitly drops a ZIP into a specific item's `inbound/` folder, discarding that signal is wrong; the item context is the strongest possible routing signal. (d) **Extract-and-discard** (no original ZIP retention): rejected — carrier submission audit may require the original archive (the customer sometimes wants the artifact as delivered); revision history may need the original to reproduce extraction outcomes. (e) **5-area split (chosen)**: preserves all signals — per-item context for NSD-sourced ZIPs, per-TG fallback for Email/PLM-sourced ZIPs, original archive retention at the receipt context, individual file classified storage via the standard pipeline. The ZIP-as-temporary-container model gives the best balance of audit + routing + submission. Same-filename detection across ZIPs (FR-72) flags the case where two ZIPs in the same TG contain a file with the same name — surfaces PM triage rather than silent overwrite.

**Consequences**: `storage` module per its 2026-05-26 MODULE.md draft: `NSDPath.internal_zip_store(item_slug, original_zip_filename)` + `NSDPath.internal_un_resolved_zip(tg_name_slug, original_zip_filename)` constructors implement the five-area path conventions. `email_service` per its forthcoming MODULE.md: ZIP attachment detection on inbound emails; routes to the FR-72 ZIP ingestion sub-flow instead of the standard FR-52 two-tier classification (the two-tier applies to extracted files within the ZIP). Document index fields per FR-13 + `[D-039]`: `from_zip: bool`, `source_zip_filename: str | None`. Customer adapter (FR-18) Ph-2 assembly logic: includes per-item ZIPs from area 1 alongside `is_final = true` individual extracted-doc revisions; un-resolved-zip ZIPs (area 2) are explicitly NOT submitted; the `is_final` rule applies to ZIP archives at the archive level (TPM marks ZIP-as-final via the same selection mechanism as individual docs per `[D-048]`). Ph-1 ZIP support is limited: PLM-modality customers may receive ZIPs via PLM polling; Ph-1 strategy is to surface a PM-triage flag and defer extraction to Ph-2 (per FR-72 `[Ph-2]` tag). Authority for FR-72 + the FR-13 5-area NSD structure. Depends on `[D-033]`, `[D-039]`, `[D-013]`, `[D-048]`.

---

## D-051: TGGroups SP list — normalize as separate list (Option A) over denormalized columns
**Status**: Active · **Date**: 2026-05-26
**Decision**: TG-group metadata (`tg_owner_name`, `tg_owner_email`, `email_group_alias`, `corp_id_list`, `default_cc_list`) is stored in a **separate SharePoint list called `TGGroups`** with one row per `(milestone_id, tg_name)`. The DeliveryItems SP list retains only `tg_name` (the foreign-key-like label, already in §2.4 per the existing schema); SP UI does a lookup to TGGroups for display per `sharepoint/REQUIREMENTS.md §3.1` (TG header rendering above each `tg_name` grouping in the milestone view). HILDA reads from TGGroups at runtime via the same SP REST API path used for the other 7 lists (per `[D-006]` / `[D-020]`). **Auto-population**: HILDA creates TGGroups rows at tracker creation, reading values from `customizations/template_schemas/<customer>/tg_groups.yaml` per `[D-049]` + FR-2 / FR-71. **TPM override**: TPM edits TGGroups rows in SP UI to override per FR-71 ODF — SP alert fires on the edit, HILDA picks up the change via the `[D-047]` SP-alert channel and updates its in-memory state + writes a `CommunicationLog` entry per NFR-6. **Unique constraint**: `(milestone_id, tg_name)` enforced at SP-list creation time; SP UI prevents duplicates. **TGGroups schema columns** (per `sharepoint/REQUIREMENTS.md §2.8`): `tg_group_id` (PK, auto), `milestone_id` (lookup → Milestones, required), `tg_name` (string, required), `tg_owner_name` (string), `tg_owner_email` (string), `email_group_alias` (string, nullable), `corp_id_list` (multi-line text JSON, nullable), `default_cc_list` (multi-line text JSON, nullable).
**Why**: Three storage alternatives were considered (documented in `sharepoint/REQUIREMENTS.md §2.8` and earlier in the 2026-05-26 chat): **(A) Normalize as separate SP list (chosen)** — one row per `(milestone_id, tg_name)`; clean separation; matches the schema/content boundary per `[D-045]`; SP UI lookup is a standard SP 2017 feature; edits touch one row. **(B) Denormalize onto every DeliveryItem row** (add the five fields to DeliveryItems): rejected — for a TG with 20+ items, the same `tg_owner_email` would appear 20× in the DeliveryItems list; updates touch every item; SP alert noise on every TG-coordinator change (multiplied across items); risk of inconsistent values if SP alert propagation misses peer items. **(C) Keep TG-group fields in YAML only** (no SP storage): rejected — breaks FR-71 (TPM cannot override `tg_owner` in SP UI before ODF fires); the customer-config workflow per `[D-014]` doesn't accommodate runtime TPM-driven changes. Option A also aligns with `[D-046]` canonical schema source — Pydantic models for `TGGroup` live in `core/src/template_schema/` (alongside `Milestone`, `DeliveryItem` entities); the SP List provisioning script, Postgres mirror DDL, and YAML schema spec for `tg_groups.yaml` are all generated from this single Pydantic source. Vs. option B which would have proliferated five fields across an already-wide DeliveryItems schema (20+ columns).
**Consequences**: **8 SharePoint lists in Ph-1** (was 7): added TGGroups per `sharepoint/REQUIREMENTS.md §2.8` (commit `1c102a9` 2026-05-26). SP alert subscription: HILDA mailbox subscribed to TGGroups with "Anything changes" trigger per `[D-047]`. Auto-population at tracker creation by HILDA via `sharepoint_integration` REST API + `tracker` module's tracker-creation flow. TPM edits trigger SP alerts → `sp_alert_parser` (in `email_service`) per `[D-047]` → HILDA updates in-memory state + `CommunicationLog` entry per NFR-6. Pydantic `TGGroup` model in `core/src/template_schema/`; emits SP List provisioning script per `[D-046]`. Cascade-on-delete semantics: when a milestone is deleted, its TGGroups rows are deleted (SP cascade). Anchors FR-71 (ODF) + FR-2 (TG-group field model) + `sharepoint/REQUIREMENTS.md §2.8`. Depends on `[D-028]`, `[D-045]`, `[D-046]`, `[D-047]`, `[D-049]`. **Open item (per `sharepoint/REQUIREMENTS.md §12` #7)**: confirm with SP UI engineer the exact JSON shape for `corp_id_list` and `default_cc_list` (column listings, types); whether the `(milestone_id, tg_name)` unique constraint is enforced SP-side or HILDA-side; the auto-population mechanism (HILDA pushes via REST after tracker creation — most likely answer, consistent with how Devices / Milestones / DeliveryItems are populated).

**Implementation note (2026-06-12 — TGGroups removed as separate SP list; denormalized onto DeliveryItems per SP UI engineer 2026-06-10 review)**: Original D-051 chose Option A (separate TGGroups SP list, normalized) over Option B (denormalized onto DeliveryItems). On 2026-06-10 SP UI engineer review proposed reversing to Option B: denormalize TG metadata fields (`tg_name`, `ingress_nsd`, `tracking_modality`, `email_group_alias`, `tg_owner_name`, `tg_owner_email`, `default_cc_list`, `folder_routing_enabled`, `tracking_enabled`) onto each DeliveryItems row. Architect accepted 2026-06-12 per amended rationale: SP 2017 classic web parts don't compose cross-list joins cleanly without server-side custom code; SPFx would add deployment complexity SP UI engineer is avoiding; one-SP-list-to-view-and-edit beats the original "no row-level duplication" goal in the corp-SP-2017 constraint environment. Updated framing: **7-list SP layout** (was 8); TGGroups column block removed from `customizations/sharepoint_config/<customer_slug>.yaml`; the 9 TG fields are now SP columns on DeliveryItems (read-only display mirrors at the SP-UI level — TPM SP UI MUST NOT allow editing TG columns on DI rows, as that would diverge from siblings in the same TG and from YAML). Source-of-truth remains customer YAML at `customizations/template_schemas/<customer_slug>/tg_groups.yaml` per template_schema/`TGGroupBase` Pydantic model (unchanged). HILDA always reads TG metadata from YAML at runtime (FileBasedListProvider), NEVER from SP DI rows. SP-side denormalized TG columns are write-once-at-DI-creation via `[D-064]` writeback (tracker → sharepoint_integration); YAML-to-SP TG-field sync on rare YAML change re-writes all DI rows for the affected TG via tracker writeback (write amplification accepted as TG fields are onboarding-time-immutable in practice). The original Option-B rejection rationale (TG-coordinator change touches every item; alert noise multiplied; inconsistency risk if SP alert propagation misses peer items) is no longer load-bearing given the YAML-as-source-of-truth model: TG-field changes go through the YAML→tracker→DI-rewrite path with HILDA orchestration, not via per-DI-row TPM edits. Original Option-A's TGGroups SP list provisioning is gone; original consequence "8 SharePoint lists in Ph-1" is amended to 7. Captured in `customizations/sharepoint_config/MODULE.md` 2026-06-12 (rollback log + Invariants D-DRAFT-Y + Key choices `[D-051]` amendment + Non-goals). Co-decided with D-073 (SP UI engineer manual provisioning) — same SP UI engineer 2026-06-10 review absorption.

---

## D-052: Dual-backend runtime LLM — local Ollama + corp on-prem LLM, empirical per-TaskKind routing, no automatic spillover
**Status**: Active · **Date**: 2026-05-28
**Decision**: HILDA's runtime LLM module (`core/src/llm/`) supports **two on-prem backends** in Ph-1/Ph-2: (a) **local Ollama** running on the HILDA PC GPU (Ph-1 dev: NVIDIA RTX A4000 16 GB; Ph-1 production: NVIDIA DGX Spark with 128 GB unified memory, in setup), serving open models (`gemma3:12b`, `qwen3:8b-q4_k_m`, etc.); free per call; concurrency bounded by VRAM and per-model serialization; no rate limit. (b) **Corp on-prem LLM** running on corporate infrastructure (corp data center; reachable from the HILDA lab subnet); exposes chat + agentic REST APIs; no per-call cost; rate-limited per minute/hour/day per corp policy. Both backends satisfy `[D-007]` "on-prem only" — the corporate network boundary is the constraint, not the specific host. The `LLMGatewayServer` (per `[D-021]`, sole LLM-egress workload) holds a `task_backend_map: dict[TaskKind, str]` and `task_model_map: dict[TaskKind, str]` as **env-config**, not Python defaults. **Backend assignment per TaskKind is empirical**: each `(TaskKind, backend, model)` pairing is locked only after measured-quality A/B testing on representative real fixtures. No code-level precedence (no rule that "harder tasks go to corp LLM" or vice versa). **No automatic backend spillover**: if a TaskKind's assigned backend is rate-limited or down, the task queues with `LLG-W006` emission — silent fallback to the unassigned backend is rejected because the quality gate that locked the pairing does not generalize. Spillover surfaces as a visible diagnostic for workflow_engine / PM-dashboard handling; if a TaskKind has been validated against multiple backends and ops wants fallback enabled, that becomes an explicit env-config policy per TaskKind, not implicit gateway behavior.
**Why**: The corp on-prem LLM became available alongside local Ollama on the HILDA PC; the question was how to route work between them. Three alternatives considered: **(α) Code-level precedence — heavy tasks (REVIEW_DOCUMENT) → corp LLM; light tasks → Ollama**: rejected based on empirical evidence — corp LLM agentic APIs tested poorly on comprehensive test report analysis (test case enumeration, pass/fail counting) when document content was not pre-chunked into tables. The "harder task → bigger model" heuristic fails when the corp LLM's strengths (chat-style structured-input tasks) don't map onto HILDA's specific runtime-LLM workload shapes (full-document review against checklist). **(β) Single-backend (Ollama only) — ignore corp LLM**: rejected — corp LLM may genuinely outperform Ollama on some TaskKinds (CLASSIFY_MESSAGE / ROUTE_ATTACHMENT could benefit from corp LLM's broader training); pre-committing to Ollama-only sacrifices quality lift without evidence. **(γ) Empirical routing (chosen)** — neither backend gets default precedence; each TaskKind locked after A/B testing. Captures the quality variation between backends without assuming which wins per task. Aligns with `[D-045]`'s "schema-vs-content boundary" — backend choice is config (operational tuning), not code (architectural commitment). On no-automatic-spillover: corp LLM rate limits + no per-call cost means quota exhaustion is a real Ph-1 risk; the temptation is to spill over to Ollama on quota hit. Rejected because (1) the empirical quality validation that justified routing a TaskKind to corp LLM does not transfer to Ollama — the Ollama fallback may silently degrade output quality without surfacing to PM; (2) workflow_engine retry semantics (Celery backoff) already handle transient rate-limit hits without code-level fallback logic; (3) PM dashboard visibility (`LLG-W006`) on persistent rate-limit pressure lets ops make an informed env-config change (raise quota / shift backends) rather than have the system silently drift.
**Consequences**: `core/src/llm/MODULE.md` updated 2026-05-28: `LLMGatewayServer.__init__` takes `backends: dict[str, BackendConfig]` + `task_backend_map` + `task_model_map`; `BackendConfig` carries endpoint URL, credential key, and per-window rate-limit settings. Token-bucket rate limiter per backend inside the gateway. New error codes `LLG-E004` (non-on-prem endpoint rejected at startup), `LLG-E006` (missing task_backend_map entry), `LLG-W004` (cold-load latency warning), `LLG-W005` (rate limit approaching), `LLG-W006` (rate limit exceeded, no spillover). Per-TaskKind A/B testing becomes a Ph-1 acceptance gate before production lock-in — captured as a STATUS.md Flag. Tentative defaults at MODULE.md drafting time: all four Ph-1 TaskKinds on `ollama` until A/B run completes; production env-config supersedes after testing. FR-16 / FR-46 test report parsing remain rule-based per `[D-011]` — they are not LLM calls and are unaffected by this decision (clarified in `llm/MODULE.md` Non-goals). `credential_service` SystemType.LLM_GATEWAY now serves credentials for both backends — one credential per backend, keyed by `BackendConfig.credential_key`. Ph-3+ Vault migration path unchanged. `corp_llm` endpoint URL must resolve to a corp on-prem host; `LLMGatewayServer` validates at startup per `[D-007]` + this decision. Authority for `core/src/llm/MODULE.md` 2026-05-28 backend abstraction, `task_backend_map` / `task_model_map` env-config pattern, and the empirical-routing principle. Depends on `[D-007]`, `[D-021]`, `[D-029]`, `[D-033]`, `[D-034]`, `[D-039]`, `[D-045]`.

**Implementation note (2026-05-28 — TaskKind enum finalized at 5; CLASSIFY_DOC schema bug fix)**: (1) The 4-TaskKind list in the Decision body's Consequences is **expanded to 5** with the addition of `CLASSIFY_DOC_TYPE` — the LLM call invoked by FR-52 / FR-55 when filename rules don't resolve `doc_type ∈ {test_report, tech_report, waiver}`. Previously implicit inside `ROUTE_ATTACHMENT`'s prompt; promoted to its own TaskKind for independent A/B-testability per the empirical-routing principle in this Decision. (2) The `CLASSIFY_DOC` schema in the initial 2026-05-28 `llm/MODULE.md` draft was mislabeled — input/output shaped like doc_type classification but commented as D-039 Step 2 (new vs revision). Schema corrected to D-039 Step 2 semantics: input = `(new_doc_first_page_excerpt, list[ExistingDocCandidate])`; output = `(verdict ∈ {REVISION, NEW_DOCUMENT}, revision_of: doc_id_slug | None, confidence)`. Caller routes below-threshold confidence to D-039 Step 3 `staged/` + PM dashboard flag per FR-52. (3) Per-TaskKind A/B-test gate now covers **5 TaskKinds**: `CLASSIFY_DOC_TYPE`, `ROUTE_ATTACHMENT`, `CLASSIFY_DOC`, `REVIEW_DOCUMENT`, `CLASSIFY_MESSAGE`. Tentative defaults unchanged: all 5 on `ollama` pending A/B testing. `[D-029]` "three Ph-1 functions" framing remains correct — the TaskKind expansion is sub-decision granularity within FR-52 attachment routing.


**Implementation note (2026-05-28b — CLASSIFY_DOC_TYPE removed; 5 TaskKinds → 4)**: Per FR-52 amendment 2026-05-28 and `[D-053]` (1:1 `ItemType` → `DocType` derivation + item homogeneity invariant), **runtime doc_type classification is no longer needed**. The `CLASSIFY_DOC_TYPE` TaskKind added by the previous impl note ("TaskKind enum finalized at 5") is **REMOVED**. Ph-1 TaskKinds revert to 4: `ROUTE_ATTACHMENT` (FR-52 step 4 — item routing LLM), `CLASSIFY_DOC` (D-039 Step 2 — new-vs-revision LLM), `REVIEW_DOCUMENT` (FR-53 — checklist review LLM), `CLASSIFY_MESSAGE` (FR-12 path c — message intent LLM). The "tentative model + backend assignments" table in `llm/MODULE.md` drops the `CLASSIFY_DOC_TYPE` row when next updated. Per-TaskKind A/B-test gate still applies to the 4 remaining TaskKinds (no rollback of the empirical-routing principle). **Schema changes**: `ClassifyDocTypeInput` / `ClassifyDocTypeOutput` Pydantic models removed from `schemas.py`; `classify_doc_type.j2` prompt template removed from `templates/`. **doc_type derivation is now Python logic on `item.item_type`** — no LLM call. See `[D-053]` for the 1:1 mapping. *(Note: `CLASSIFY_DOC_TYPE` TaskKind is RESTORED in 2026-06-08 per `[D-053]` impl note 2026-06-08 — corrected model reinstates runtime doc_type classification per FR-85 2-step ladder; Ph-1 TaskKinds now back to 5. This 2026-05-28b removal is superseded; preserved here for chronology per `[D-002]` append-only.)*

**Implementation note (2026-06-07 — dual → tri-backend expansion; backend enum widened to 3)**: The Decision body's two-backend framing (local Ollama on HILDA PC + corp on-prem LLM) is **expanded to three backends** after hardware topology clarification 2026-06-07: (a) `ollama_a4000` — Ollama running on a **separate Linux box** with NVIDIA RTX A4000 16 GB (Ph-1 dev tier; swap-on-demand model loading); (b) `vllm_dgx` — **vLLM** running on a **third separate machine** (NVIDIA DGX Spark with 128 GB unified memory; Ph-1 production tier; all-hot model serving + continuous batching); (c) `corp_llm` — corp on-prem LLM endpoint unchanged. **All three satisfy `[D-007]`** — the corporate network boundary is the on-prem constraint; multi-host topology within the corp network is fine. The Decision body's text "running on the HILDA PC GPU" is **corrected**: in Ph-1 production, Ollama and vLLM both run on **dedicated GPU hosts separate from HILDA PC** (HILDA PC has no GPU; runs `hilda-api` / `hilda-worker` / `hilda-llm-gateway` only). **`BackendConfig.name` Literal expanded**: `"ollama"` → `Literal["ollama_a4000", "vllm_dgx", "corp_llm"]`. New informational fields on `BackendConfig`: `cold_load_expected: bool` (True for Ollama swap-on-demand; False for vLLM all-hot) + `supports_batching: bool` (True for vLLM continuous batching). **A/B-test gate becomes 3-way per TaskKind** (was 2-way) — was already updated in STATUS.md A/B-test-gate flag. **Model catalog policy**: same Ollama-format models (`qwen3:8b-q4_k_m`, `gemma3:12b`) on both local backends for direct A/B comparability; larger models (`gemma3:27b`, `qwen2.5:32b`, `llama3.1:70b`) added to `vllm_dgx` only if small-model A/B unsatisfactory. **Empirical-routing principle unchanged** — no code-level precedence among the 3 backends; each TaskKind locks `(backend, model)` per A/B winner. **No-automatic-spillover unchanged** — `LLG-W006` surfaces rate-limit/outage; no silent fallback. **`credential_service` SystemType** split correspondingly: `LLM_GATEWAY` → `LLM_OLLAMA_A4000` / `LLM_VLLM_DGX` / `LLM_CORP_LLM` (per `credential_service/MODULE.md` 2026-06-09 drift fix). See `[D-059]` for the full tri-backend Decision body (this impl note records the corresponding body-supplement on `[D-052]` per `[D-002]` append-only).

---

## D-053: Routing model — two-pipeline framing (work-item + target-folder), default work-item, multi-item association, ItemType.DEFAULT
**Status**: Active · **Date**: 2026-05-28
**Decision**: HILDA's document routing splits into **two independent pipelines** that may be invoked sequentially per inbound document: **Type-1 (FR-52) — Document → Work-item**: 5-step pipeline — (1) strict substring on `item_description` (comma-separated tag list; all tags must appear in filename); (2) fuzzy match on `item_name` via rapidfuzz; (3) source-folder → work-item template (TG opt-in via `folder_routing_enabled`); (4) LLM `ROUTE_ATTACHMENT` with first-page excerpt; (5) staged → milestone's default work-item. **Type-2 (FR-77) — Document → Customer portal target folder**: 2-step pipeline — (1) source-folder → target-folder template (TG opt-in); (2) work-item-id → target-folder fallback. No carrier upload when item is default work-item. `doc_type` is **derived from `item.item_type`** via 1:1 mapping (TestReport → test_report; TechReport → tech_report; Waiver → waiver; all others → default). New enum value `ItemType.DEFAULT` added. **No runtime doc_type LLM classification** — the item type IS the doc_type. **Item homogeneity invariant**: items committed to `item_type = TestReport` accept only test_report documents; mismatched routing surfaces `EML-W005` + staged. **Default work-item**: every milestone auto-instantiates one (`item_type = Default`; `tg_name = "_unrouted"`); accumulates unrouted documents; does NOT gate carrier submission of other items but DOES gate milestone closure (per FR-74 threshold). **Multi-item association**: M:M join table `DocumentItemAssociation(document_index_row_id, delivery_item_id, association_kind: Literal["primary", "secondary"])`. One file can route to multiple items via tag overlap; SP UI shows primary item with download link, secondary items with reference link.
**Why**: Five alternative framings considered: **(α) Single unified routing pipeline determining item + target folder in one pass**: rejected — Type-1's filename-first priority is opposite to Type-2's folder-first priority; conflating them produces tangled fallback logic and hides per-pipeline failure modes from analysis. **(β) `doc_type` as runtime LLM classification (CLASSIFY_DOC_TYPE TaskKind)**: rejected after 4-bucket simplification — item homogeneity (one item = one doc_type) makes runtime classification redundant; item type IS the doc_type. Saves LLM call cost + simplifies surface. CLASSIFY_DOC_TYPE TaskKind removed per `[D-052]` impl note 2026-05-28b. **(γ) Per-item NSD ingress folders (current FR-13 model)**: rejected by owners with 40+ items per milestone — flat folder structure required. **(δ) Single-item-per-document routing**: rejected — real test reports cover multiple items via test-case overlap (a "VoLTE handover regression" report satisfies items I-VoLTE and I-handover simultaneously). **(ε) No default work-item; all unrouted → PM dashboard inbox**: rejected — PMs become routing-resolution bottleneck; default work-item is HILDA-native sentinel that doesn't pollute PM mental model with "unsorted incoming." Default work-item gating milestone closure (not carrier submission of other items): this split is deliberate — carrier submission proceeds independently for resolved items; milestone closure requires nothing is silently dropped.
**Consequences**: `ItemType` enum gains `Default` (7 values total) in `template_schema/MODULE.md`. `DocType` stays 4-valued (test_report / tech_report / waiver / default); compliance docs / certification docs / release notes all fold into `default` per the 1:1 derivation. `DocumentItemAssociation` table added to storage module (new Pydantic model + Alembic migration). `DocumentIndexRow` gains `unrouted_source_path: str | None`. `llm/MODULE.md`: `CLASSIFY_DOC_TYPE` TaskKind removed (5 → 4 Ph-1 TaskKinds); see `[D-052]` impl note 2026-05-28b. `template_schema/MODULE.md`: `ItemType` expanded; `item_description` semantic shifts to comma-separated tag list; new `TGGroupBase` fields (`ingress_nsd: Literal["NSD1", "NSD2"]`, `folder_routing_enabled: bool = False`). `email_service/MODULE.md`: FR-52 5-step pipeline + FR-77 2-step pipeline both spec'd in `attachment_router.py`. `storage/MODULE.md`: `DocumentItemAssociation` table + `unrouted_source_path` field on `DocumentIndexRow`. New customizations file: `customizations/template_schemas/<customer_slug>/folder_routing.yaml` per TG; works alongside existing `customizations/<customer_slug>/portal_structure.yaml` (FR-69) which retains carrier-mechanics role (file_overwrite_policy, revision_file_suffix_pattern). Two YAMLs with distinct concerns — locked option (B). New FRs: FR-77, FR-78, FR-79, FR-82, FR-83. Anchors FR-52 amendment, FR-77 / FR-78 / FR-79 / FR-82 / FR-83 (new). Depends on `[D-013]`, `[D-028]`, `[D-033]`, `[D-037]`, `[D-039]`, `[D-049]`, `[D-050]`, `[D-052]`.

---

## D-054: Customer-portal upload destination — symmetric `no_customer_upload` flag + Google-Drive-only Ph-1/Ph-2 customer_adapter
**Status**: Active · **Date**: 2026-05-28
**Decision**: HILDA pushes **individual files only** (never zip archives) to two distinct destinations as part of standard ingest-side processing: **(a) PLM upload** — internal corp coordination per FR-13; one PLM issue per (owner × milestone) per FR-8; each individual file attached as a flat PLM attachment (no folder structure — PLM is issue tracker, not file system). **(b) Carrier portal upload** — external customer submission destination per FR-18; folder structure preserved on carrier side via FR-77 Type-2 routing. Both destinations receive **only individual files**, regardless of ingest channel (Email, NSD1/2, PLM-source, TPM via FR-62 Ph-2). Zip files are HILDA-side audit-only (`_zip_audit/`); never uploaded anywhere. Version tree operates on individual files only. A single per-item boolean flag **`no_customer_upload: bool = False`** controls BOTH destinations symmetrically — when `True`, both PLM upload AND carrier portal upload are suppressed for that item. Item advances to `Closed` via TPM-manual click after OwnerClosed guards are met (FR-7 amendment + DEF-20 carve-out). **Customer adapter Ph-1/Ph-2 scope**: **Google Drive only** (carriers accepting submissions via shared Google Drive folders). Web portal and JIRA-as-customer-portal flavors deferred to Ph-3+. Per-carrier adapter implementations live at `customizations/customer_adapter/<carrier_slug>_adapter.py` + `<carrier_slug>_adapter_config.yaml`; the `CustomerAdapter` Protocol stays in `core/src/customer_adapter/protocol.py`. No `core/` reference implementations in Ph-1/Ph-2. **PLM-Carrier hash sync invariant** (FR-68 extended): every file uploaded to carrier portal must hash-match a file present on PLM (top-level attachment OR member of a zip attachment on PLM, extracted+hashed at verification time). Non-match emits `ITR-W003 — PLM-Carrier hash mismatch on file '{filename}' item '{item_id}'`; PM dashboard surfaces for manual reconciliation.
**Why**: Four alternative framings considered: **(α) Zip uploaded to PLM as audit completeness**: rejected per 2026-05-28 user clarification — zip-level versioning would introduce "which zip version is final?" complexity. Cleaner model: PLM holds the same individual files that go to carrier; zip is HILDA-side audit only. **(β) Independent `no_plm_upload` + `no_carrier_upload` flags**: rejected as unnecessary complexity. The two destinations are paired in practice — items where HILDA doesn't push to carrier are items where HILDA doesn't push to PLM (manual TPM handling end-to-end). Symmetric flag matches operational reality. **(γ) Customer_adapter in `core/` alongside IssueTracker pattern**: rejected because each customer's Google Drive folder layout is deployment-specific. Placement in `customizations/` matches the proprietary-IssueTracker pattern (`customizations/issue_tracker/`) and aligns with `[D-003]` adapter-generation flow. **(δ) Web portal + JIRA-as-customer-portal in Ph-1**: rejected as scope expansion not warranted for current customers; deferred to Ph-3+ when new customer onboarding requires. PLM-Carrier hash sync as Ph-1 (not Ph-2): hash sync verification is cheap (post-dispatch Celery task; one hash per file); catches misrouted-file bugs early; supports NFR-15 reliability.
**Consequences**: New FR-80 (`no_customer_upload` per-item flag; symmetric across PLM + carrier). FR-7 amended: TPM-Mark-Closed manual transition with guard enforcement; DEF-20 carve-out (only this specific manual path lifted; other auto-Closed paths remain deferred). FR-13 amended: dual NSD; zip never uploaded; individual files only to PLM. FR-18 amended: carrier individual files only; folder structure preserved per FR-77. FR-19 amended: Ph-1/Ph-2 = Google Drive only; `customizations/` placement. FR-68 extended: PLM-Carrier hash sync. `customer_adapter/MODULE.md` (forthcoming): Google Drive only Ph-1/Ph-2 surface; `customizations/` placement. New customizations layout: `customizations/customer_adapter/<carrier_slug>_adapter.py` + `<carrier_slug>_adapter_config.yaml`. Depends on `[D-013]`, `[D-019]`, `[D-028]`, `[D-040]`, `[D-041]`.

**Implementation note (2026-06-05 — Google Drive REST API unavailable; browser-automation implementation)**: The Decision body assumes Google Drive is accessible via a programmatic API surface (REST / SDK). **This is not the case** per corp/carrier policy constraint surfaced 2026-06-05: Google Drive REST API is not available. **Implementation switches to browser-automation libraries** — `selenium` (default) or equivalent (`playwright`, `puppeteer`); each carrier's adapter drives a headless Chromium browser instance authenticated as the PM via stored session cookies / SAML token / configured login flow per the carrier's `customizations/customer_adapter/<carrier_slug>_adapter_config.yaml`. **Operational consequences**: (a) per-upload latency is **10–100× slower** than a REST-API equivalent — the `hilda-worker` Celery task pool that drives `submitItem` / `uploadAttachment` calls scales the per-carrier worker concurrency to available browser sessions; (b) Google Drive UI selectors are **versioned in adapter config** to absorb upstream UI changes without code release — selector-version bumps are deploy-time updates to the carrier's `*_adapter_config.yaml`; (c) `getStatus` and `postComment` may be limited or fully unavailable when the carrier portal surfaces these only in rendered HTML (no underlying API endpoint to scrape) — per-carrier capability flag in adapter config; adapter raises `NotImplementedError` for unsupported operations and `customer_adapter` callers must handle gracefully; (d) **Chromium binary is a hard ops dependency** on the HILDA PC — installed via `docker-compose` image build or pre-installed on the host (sops-encrypted `.env` configures path). Anchors FR-19 amendment 2026-06-05.

---

## Implementation notes appended 2026-05-28 (batch — supplement to D-053 / D-054)

The following impl notes belong to existing ADRs (D-013, D-039, D-050); appended here as a batch for the 2026-05-28 requirements update. Each note references its anchor ADR explicitly; future readers of D-013/D-039/D-050 should consult this section.

### `[D-013]` Implementation note (2026-05-28 — dual-NSD topology + flat ingress)

HILDA operates with **two SMB-mounted NSDs** in Ph-1 — **NSD1** (bidirectional: hosts HILDA `internal/` tree + ingress) and **NSD2** (ingress-only). Both authenticate via the same `hilda-svc` Kerberos keytab; both polled identically per the same `polling_schedule` AutomationRule. NSD2-sourced documents are classified into NSD1's `internal/` tree (cross-drive write); NSD2 originals remain in place forever (no archival). **Owner inbound flat folder structure** replaces the prior per-item folder model: `\\NSD_X\<carrier>\<device>\<milestone>\<Folder-N>\...` — owners with 40+ items per milestone rejected the per-item folder model (`\\share\hilda\inbound\<carrier>\<device>\<milestone>\<item_slug>\`). Folder names are TG-assigned, not item-assigned; per-item identification now goes through FR-52 5-step pipeline rather than folder-path lookup. Internal tree path conventions revised: NSD1 `internal/` tree retains classified storage hierarchy + `_zip_audit/` + `staged/` + `outbound/` per `[D-050]` impl note 2026-05-28. Access model per the original Decision body unchanged for NSD1 (`hilda-svc` = Modify; HILDA-mediated downloads via `https://hilda.corp/dl/<scoped_token>`); NSD2 is read-only from HILDA's perspective (no writes).

### `[D-039]` Implementation note (2026-05-28 — item routing precedes D-039 sub-classification; folder match as FR-52 step 3)

Per FR-52 5-step pipeline rewrite + `[D-053]` two-pipeline framing, the `[D-039]` 4-step classification order is refined: **(1) Item routing fires FIRST** via FR-52 steps 1–4 (substring → fuzzy → folder template → LLM ROUTE_ATTACHMENT). Item identification is the prerequisite for D-039. **(2) D-039 Step 0** (hash dedup) applied after item is known: hash match within `(delivery_item_id, doc_type)` → duplicate; skip. **(3) D-039 Step 1** (slug match) applied within `(delivery_item_id, doc_type)` scope. **(4) D-039 Step 2** (LLM `CLASSIFY_DOC` for ambiguous slug match): unchanged. **(5) D-039 Step 3** (staged for unresolved new-vs-revision): unchanged. **When item routing fails entirely** (FR-52 step 5), the document is associated with default work-item (FR-78); D-039 does not apply for default-work-item-associated documents (no revision tracking on default work-item since they're inherently unrouted — revision tracking resumes when TPM reassigns the document via FR-83). **Folder match** adds a new layer: source-folder → work-item via `folder_routing.yaml` template fires as FR-52 step 3 (after substring + fuzzy fail). This is independent of D-039 sub-classification on new-vs-revision. **`CLASSIFY_DOC` TaskKind** (D-039 Step 2 LLM) remains in scope per `[D-052]` impl note 2026-05-28b; only `CLASSIFY_DOC_TYPE` TaskKind was removed.

### `[D-050]` Implementation note (2026-05-28 — simplified `_zip_audit/` + classified + staged + outbound structure)

The 5-area structure in the original Decision body is simplified to **4 areas** at TG level: **(1) `_zip_audit/`** — zip files + co-located extracted contents under `<tg>/_zip_audit/<zip_filename>.zip` + `<tg>/_zip_audit/<zip_filename>/<extracted-tree>/`. Replaces the prior `zip-store` + `un-resolved-zip` 2-area split. **Audit-only; never uploaded to PLM or carrier portal.** **(2) Classified** — `<tg>/<item>/<doc_type>/<doc_id_slug>/revN/` for individual files post-FR-52 routing. **(3) Staged** — `<tg>/<item>/<doc_type>/staged/` for `[D-039]` Step 3 ambiguous new-vs-revision holding; PM/TPM triage. **(4) Outbound** — `<tg>/<item>/outbound/` for HILDA-generated artifacts (QC reports, etc.). **Areas removed from the original Decision body**: per-item ZIP root (b1) — no per-item NSD ingress now; obsolete per `[D-013]` impl note 2026-05-28. TG-level `un-resolved-zip` — merged into `_zip_audit/`. Per-item ZIP retention — replaced by single TG-level `_zip_audit/`. **Carrier portal upload (FR-18, FR-77)** and **PLM upload (FR-13)** operate exclusively on individual files from area 2 (classified); area 1 (`_zip_audit/`) is audit-only and never uploaded. **Version tracking on individual files only** (per FR-17 amendment 2026-05-28): zip-level versioning is irrelevant; D-039 + `[D-048]` (Ph-2) apply per individual file. Same-filename detection across zips emits `EML-W006` per FR-72.

### `[D-053]` Implementation note (2026-06-08 — `doc_type` derivation REVERSED; classification per content; 4-value ItemType; 5-value DocType; 4-path storage matrix; SP UI strict A→B→C resolution)

**Body-preserving correction.** The Decision body's claim that `doc_type` is **derived from `item.item_type` via 1:1 mapping** and that "the item type IS the doc_type" is **withdrawn**. The Why-section bullet (β) rejecting "`doc_type` as runtime LLM classification (CLASSIFY_DOC_TYPE TaskKind)" is also withdrawn. Per requirements-phase user-led redesign 2026-06-08 (13+ turn iterative refinement), a single non-Confirmation, non-Default work-item CAN receive multiple distinct doc_types — runtime classification per inbound document is required. The corrected model (locked in `requirements.md` FR-7 + FR-85 + FR-86 + FR-87 + amendments to FR-16 / FR-52 / FR-53 / FR-78 / FR-83):

**(a) ItemType enum collapses to 4 values** (from the prior 7-value variant): `Confirmation` (no documents — Yes/No reply only), `TEST_TECH_WAIVER_REPORT` (receives any of `{test_report, tech_report, waiver}`; `review_required = true`), `COMPLIANCE_CERTIFICATION_RELEASE_NOTES` (receives `compliance_certification_release_notes` documents; `review_required = false`), `Default` (auto-instantiated default work-item per milestone per FR-78; receives any document whose target work-item is not resolved).

**(b) DocType enum expands to 5 values**: `test_report`, `tech_report`, `waiver`, `compliance_certification_release_notes` (rename of the prior 4-value enum's `default` — same semantic: catch-all for compliance / certification / release_notes bundled), `unresolved` (new; for low-confidence classifications + Default-routed docs whose classification has not been auto-resolved).

**(c) Alignment invariant** — `TEST_TECH_WAIVER_REPORT` items hold doc_type ∈ `{test_report, tech_report, waiver}`; `COMPLIANCE_CERTIFICATION_RELEASE_NOTES` items hold doc_type = `compliance_certification_release_notes`; `Default` items hold any of 5; `Confirmation` items hold no documents. Misaligned (item_type, doc_type) pairs at ingest land on the `staged-not-classified` NSD path (per (e)) for TPM SP UI resolution.

**(d) 2-step classification ladder (FR-85)**: **Step 1 filename regex** runs against per-customer rules (`customizations/<customer_slug>/doc_type_filename_rules.yaml`) covering all 4 actionable doc_types `{test_report, tech_report, waiver, compliance_certification_release_notes}` — top single-match wins; multi-match falls through to Step 2. **Step 2 LLM `CLASSIFY_DOC_TYPE`** fires only when Step 1 fails — candidate set restricted to `{test_report, tech_report, waiver}` (3 values; LLM never returns `compliance_certification_release_notes` — that doc_type is detected by regex only); below confidence threshold (default 0.85) → doc_type = `unresolved`. Classification runs independently of FR-52 work-item routing (no dependency in either direction; ordering and parallelization are architecture-phase decisions).

**(e) 4-path NSD storage matrix (FR-86)**: **(unrouted)** `internal/<carrier>/<device>/<milestone>/<inferred_tg_name>/_unrouted/<original_filename>` — when work-item lands on `Default` per FR-52 step 5; `[D-039]` SKIPPED at ingest. **(classified)** `internal/<carrier>/<device>/<milestone>/<tg>/<item>/<doc_type>/<doc_id_slug>/revN/` — when (item_type, doc_type) aligned AND `[D-039]` revision pass. **(staged-not-revision-determined)** `internal/.../<item>/<doc_type>/_staged_revision/<original_filename>` — when aligned BUT `[D-039]` ambiguous. **(staged-not-classified)** `internal/.../<item>/_staged_classification/<original_filename>` — when misaligned (item_type ≠ doc_type's allowed item-set, OR doc_type = `unresolved` on non-Default item).

**(f) `[D-039]` skip optimization**: `[D-039]` revision determination is SKIPPED at ingest when (i) routing lands on `Default` OR (ii) doc_type = `unresolved`. Deferred to FR-83 reassignment time (for case i) or FR-87 step (B) doc_type resolution time (for case ii), at which point `[D-039]` runs against the resolved (item, doc_type) family. Saves LLM cost on the unrouted-path docs (typical 5-10% of inbound).

**(g) `[D-039]` family scope within `compliance_certification_release_notes` doc_type**: revision families are keyed by `(item, doc_type, doc_id_slug)` per existing `[D-039]` spec — filename slug differentiates compliance vs certification vs release_notes within the bundle (no separate `doc_subtype` field; cross-sub-type slug collisions are accepted as edge case).

**(h) SP UI strict-order resolution A → B → C (FR-87)**: for documents in the 3 staged NSD paths, TPM resolves outstanding ambiguities in strict order — **(A)** Reassign work-item (Default → TEST_TECH_WAIVER_REPORT or COMPLIANCE_CERTIFICATION_RELEASE_NOTES); **(B)** Resolve doc_type (unresolved OR misaligned); **(C)** Resolve revision (NEW / Revision of X). Step C gated on Step B. **Each TPM action reaches HILDA backend via the FR-84 SP-alert email channel** (SP UI does not call hilda-api directly per FR-84). FR-83 reassignment from default work-item follows the same A→B→C flow — no auto-override of doc_type (supersedes the prior FR-83 step (3) auto-derivation interpretation).

**(i) `doc_count` semantic** unchanged but corrected guidance: counts `test_report` documents only — if owner submits only waiver / tech_report, state does not advance. Template author must set `doc_count = 0` for waiver-only / tech_report-only deliverables — owner-done confirmation then advances state via the vacuous-guard path; received documents are stored but not required by gating. Splitting into separate work-items does not help (all actionable items share `TEST_TECH_WAIVER_REPORT` item_type and same test_report-only doc_count semantic).

**`EML-W005` retirement**: the prior "routing/type mismatch" warning is **removed** — replaced by the storage-matrix landing rule (misalignment → `staged-not-classified` path; not an error).

**Cascading consequences** (for MODULE.md re-cascade in architecture phase per STATUS.md In-progress entry 2026-06-08): `template_schema/MODULE.md` — `ItemType` enum collapses 7→4; `DocType` enum expands 4→5; strike "1:1 derivation" docstring; add alignment invariant; restore `CLASSIFY_DOC_TYPE` TaskKind reference. `storage/MODULE.md` — `NSDPath.internal_default_workitem()` signature add `inferred_tg_name` param (per `[D-060]` impl note 2026-06-08); add 2 new NSDPath helpers (`internal_staged_classification`, `internal_staged_revision_for_classified`); `DocumentIndexRow.doc_type` becomes `Optional[DocType]` allowing UNRESOLVED state; document the FR-86 storage matrix; add `STR-W007` (unrecognized doc_type) if needed. `llm/MODULE.md` — restore `CLASSIFY_DOC_TYPE` TaskKind; add ClassifyDocTypeInput/Output schemas with restricted candidate set; revert "Not a doc_type classifier" Non-goal; tentative-assignments table 4 → 5 rows.

**Supersedes**: the prior `[D-053]` impl note 2026-05-28b which removed `CLASSIFY_DOC_TYPE` and stated the 1:1 derivation framing. Per `[D-002]` append-only convention, the original Decision/Why/Consequences bodies above are preserved verbatim; this impl note is the authoritative correction.

## D-055: File-centric `DocumentIndexRow` + symmetric M:M `DocumentItemAssociation`
**Status**: Active · **Date**: 2026-06-07
**Decision**: `DocumentIndexRow` is the canonical file table — one row per physical document, PK = `file_hash`, holding file-content + per-ingest properties only (parser_result, llm_review_findings, inferred_tg_name, routing_resolution, ingested_at, doc_type). `DocumentItemAssociation` is a symmetric M:M (composite PK `(file_hash, delivery_item_id)`) holding per-(file, item) properties (local_classified_path, plm_id, plm_attachment_id, owner_email, upload_timestamp). No primary/secondary distinction — all associations equal. The same file may occupy multiple NSD classified paths simultaneously, one per associated item. FR-57 natural-key lookup preserved via secondary unique constraint `(milestone_id, doc_id_slug, rev_number)` on `DocumentIndexRow`. Replaces the initial muddled draft that split "primary" vs "secondary" associations across two tables with redundant `is_primary_association` flags and dual-write hazards on `plm_attachment_id`.
**Why**: The muddled primary-on-DocumentIndexRow + secondary-on-M:M alternative created a dual-write hazard on `plm_attachment_id` (the same field present in two tables for the primary case) and made `is_primary_association` trivially redundant (always True on DocumentIndexRow by construction). A milestone-shared-path alternative (one canonical NSD home column) was also rejected because the file physically lives at multiple NSD paths under FR-79 (one per item's classified path). The symmetric M:M model gives clean single source of truth per field, uniform query semantics, and reduces FR-83 reassignment to simple M:M row add/remove. Per-association data semantically belongs on the M:M; per-file data semantically belongs on `DocumentIndexRow`.
**Consequences**: FR-57 natural key shifts from `(delivery_item_id, doc_type, doc_id_slug, rev_number)` to `file_hash` + secondary `(milestone_id, doc_id_slug, rev_number)`; per-item queries require JOIN through the M:M (`get_documents_for_item(delivery_item_id)` helper added). One file in 2 work-items = 1 `DocumentIndexRow` + 2 `DocumentItemAssociation` rows + 2 NSD path copies. PLM fan-out cleanly modeled as `DISTINCT (owner_email, plm_id)` over M:M rows (`fan_out_plm_associations` returns `PLMFanOutTarget` list). Cross-milestone associations deferred to Ph-3+ (Ph-1/Ph-2 enforces same-milestone via STR-E005). FR-83 TPM reassignment becomes a single transaction adding a target association + removing source.

### `[D-055]` Implementation note (2026-06-09 — `NSDPathType` enum column added + `local_classified_path` → `local_nsd_path` rename)

**Field rename + new explicit state column on `DocumentItemAssociation`.** The Decision body's claim that the M:M row holds `local_classified_path: Path` is **superseded** by `local_nsd_path: Path` (rename) — the prior name implied "classified path only" but the field actually holds whichever of the 4 FR-86 NSD path types the file is currently at (classified | staged_revision | staged_classification | unrouted). Plus a new explicit-state column `nsd_path_type: NSDPathType` (4-value enum: `CLASSIFIED`, `STAGED_NOT_CLASSIFIED`, `STAGED_NOT_REVISION`, `UNROUTED`) is added next to `local_nsd_path` so the row's path-state is queryable without string-parsing the path. This enables indexed `STR-W007` stale-staged-document queries and clean FR-86 storage-matrix state-transition logic when TPM saves via FR-87 SP UI. Each association row's `nsd_path_type` is independent — the same file_hash can be in different `nsd_path_type` states across different rows simultaneously. The "same file may occupy 2+ NSD paths simultaneously" rationale in the original Consequences body is preserved verbatim. Cascade: 10 references to `local_classified_path` updated to `local_nsd_path` across `storage/MODULE.md`. Storage-only change — does NOT propagate to requirements.md or template_schema/MODULE.md (NSDPathType is a storage-implementation concern; FR-86's 4 path types are the requirements-side spec).

## D-056: Composite PK `(file_hash, delivery_item_id)` on `DocumentItemAssociation` (no synthetic `association_id`)
**Status**: Active · **Date**: 2026-06-07
**Decision**: The M:M `DocumentItemAssociation` uses a composite natural PK `(file_hash, delivery_item_id)`. No synthetic `association_id` surrogate column. `CommunicationLog` audit entries reference the pair directly in `attachments[]` instead of via an opaque ID.
**Why**: A synthetic `association_id` would require an additional column plus a uniqueness constraint on the composite anyway (to enforce one-association-per-(file, item)) — it adds storage with no semantic benefit. Audit-log references using opaque IDs would obscure interpretability; the natural pair is immediately readable. No risk of dangling synthetic IDs after cascading deletes. Idempotency comes for free from the natural-key uniqueness.
**Consequences**: All M:M-touching API methods take `(file_hash, delivery_item_id)` as the identity pair — `add_document_item_association`, `update_association_plm_attachment`, `delete_document_item_association`, `reassign_document_to_workitem`, `make_download_token`. `CommunicationLog.attachments[]` references the pair when logging FR-79 fan-out, FR-83 reassignment, FR-67 PLM cleanup. Cascading delete (item or file removed) leaves no orphan IDs.

## D-057: `download_url` never persisted; computed via short-lived per-(file, item) token
**Status**: Active · **Date**: 2026-06-07
**Decision**: `DocumentIndexRow` does NOT store a `download_url` field. FR-61 HILDA-mediated downloads are served via on-demand tokens generated at page-render time by `storage.make_download_token(file_hash, delivery_item_id, ttl_seconds=300)`. The token encodes `(file_hash, delivery_item_id)` + signature + expiry; opaque to caller. `resolve_download_token` verifies signature/TTL and returns the per-association `NSDPath` for the hilda-api `/dl/<token>` endpoint to stream from. The `NSDPath.to_download_token()` helper (which existed on the legacy single-association model) was removed.
**Why**: With the M:M model (per `[D-055]`), the same file may live at multiple NSD paths (one per item), so a single stored URL is ambiguous (which item's path does it point to?). Per-association persisted URLs would multiply across associations and need rotation on TTL expiry, defeating FR-61's short-lived intent. Compute-on-demand uses the M:M row already loaded for dashboard page rendering — no extra query cost. The token's per-(file, item) scope means each item's view of a shared document resolves to that item's own NSD copy at click time, which is exactly the right semantics for audit (CommunicationLog records which item's view served the download). Worker-internal submission assembly (FR-41 / FR-73) reads NSD via SMB directly through `storage.read_file(NSDPath)` and never needs an HTTP-URL layer.
**Consequences**: Two new storage API methods (`make_download_token`, `resolve_download_token`) + new error code `STR-E007` (invalid/expired download token). Dashboard module's page-render code calls `make_download_token` per displayed document. hilda-api `/dl/<token>` endpoint resolves token → reads NSDPath → streams to browser. No token persistence, no stale-URL invalidation on path migration. SP UI individual-download flow uses this; submission-assembly worker (FR-41/FR-73) does not.

## D-058: Cross-channel document idempotency on `file_hash` (first-arrival-wins on per-ingest fields)
**Status**: Active · **Date**: 2026-06-07
**Decision**: `add_document_index_row(file_hash)` is idempotent — a second arrival of the same SHA-256 returns the existing row unchanged. Per-ingest fields (`ingest_source`, `inferred_tg_name`, `routing_resolution`, `ingested_at`) are first-write-wins. Subsequent arrivals via different channels do NOT overwrite these fields. The CommunicationLog still records every arrival event for audit, but the `DocumentIndexRow` is created exactly once per `file_hash`.
**Why**: A document with the same SHA-256 can legitimately arrive via multiple channels (Email + PLM, NSD + Email, etc.). "This document is already known; subsequent arrivals are confirmations, not updates" matches operator intuition and FR-23 three-tier inbound semantics. Last-arrival-wins would silently overwrite the original ingest provenance and confuse audit ("when did it actually arrive?"). A multi-arrival history table would add complexity not justified at Ph-1/Ph-2 scale — the CommunicationLog already records every arrival. Idempotency is essential for Celery retry safety where the same task body may execute multiple times.
**Consequences**: `DocumentIndexRow.ingest_source` records the channel of FIRST ingest only. The CommunicationLog query surface (with `file_hash` filter added per the API surface review) provides the "all arrivals of this file" view. `DocumentItemAssociation` can still add NEW associations for subsequent items even when the file is already known — supports FR-79 multi-item case where the same file is later linked to additional work-items. Retry semantics: Celery task replay across the FR-23 tiers (IMAP IDLE / short-poll / deadline-tiered) does not duplicate rows.

## D-059: Tri-backend LLM serving — `ollama_a4000` + `vllm_dgx` + `corp_llm`
**Status**: Active · **Date**: 2026-06-07
**Decision**: `LLMGatewayServer.BackendConfig.name: Literal["ollama_a4000", "vllm_dgx", "corp_llm"]` — three runtime LLM backends, each on its own host, each with a distinct capability/throughput profile. Each TaskKind's backend is chosen by per-task A/B-test winner (env-config; no code-level precedence). No automatic spillover between backends on rate-limit or outage. Expands the `[D-052]` dual-backend framing (Ollama + corp_llm) to tri-backend after user clarification on hardware topology: RTX A4000 lives on a separate Linux box (Ollama), DGX Spark on a third separate machine (vLLM with 128 GB unified memory), corp on-prem LLM at the corp endpoint. Same Ollama-format models served on both local backends for direct A/B comparability; larger models added to `vllm_dgx` only if small-model A/B results unsatisfactory.
**Why**: A4000 and DGX Spark have complementary, not redundant, profiles. A4000's GDDR6 ~448 GB/s wins per-token throughput for fits-on-A4000 models (autoregressive inference is bandwidth-bound). DGX Spark's 128 GB unified gives capacity for 30B+ models the A4000 cannot hold + vLLM continuous batching wins at concurrency. The 128 GB unified RAM is NOT a throughput advantage for small models because LPDDR5X (~273 GB/s) < GDDR6 (~448 GB/s) — capacity-vs-bandwidth caveat. corp_llm chat + agentic APIs may win specific TaskKinds in A/B and should remain available. A "DGX-only-in-production" alternative was rejected because it would forfeit A4000's per-token throughput advantage for the common case (small-model latency-sensitive TaskKinds). "Drop corp_llm" was rejected because the A/B test gate is the empirical decision mechanism. "Automatic spillover" was rejected because A/B results don't generalize across backends — silent fallback to a non-A/B-validated backend would deliver degraded quality without a gate.
**Consequences**: 3-way A/B test matrix per TaskKind (was 2-way). Model catalog policy: same Ollama-format models (`qwen3:8b-q4_k_m`, `gemma3:12b`) on both local backends for direct comparability; larger models (`gemma3:27b`, `qwen2.5:32b`, `llama3.1:70b`) added to `vllm_dgx` only if small-model A/B unsatisfactory. `[D-052]` ADR body needs impl note 2026-06-07 appended per `[D-002]` append-only convention (flagged in STATUS.md). STATUS.md A/B-test-gate flag rewritten for tri-backend. `BackendConfig` gains `cold_load_expected` (True for Ollama swap-on-demand; False for vLLM all-hot) and `supports_batching` (True for vLLM continuous batching) informational fields. Hardware topology: HILDA PC (no GPU; runs hilda-api / hilda-worker / hilda-llm-gateway) + A4000 box + DGX Spark box + corp_llm endpoint = 4 distinct hosts. `hilda-llm-gateway` proxies outbound to all three model endpoints. Rate-limit spillover surfaces as `LLG-W006` for ops visibility — operator decides whether to manually reroute.

## D-060: Default work-item is per-MILESTONE (not per-TG); TG-of-document on `DocumentIndexRow.inferred_tg_name`
**Status**: Active · **Date**: 2026-06-07
**Decision**: Per FR-78, every milestone auto-instantiates exactly one default work-item (`ItemType.DEFAULT`, `tg_name = "_unrouted"` sentinel, no owner). NOT per-(milestone, tg_name). The TG of an inbound document is always knowable from the channel (NSD ingress_nsd / email_group_alias / PLM-id reverse-lookup) and is recorded on `DocumentIndexRow.inferred_tg_name` (per-ingest). FR-83 TPM-manual reassignment uses `inferred_tg_name` to shortlist candidate work-items within the resolved TG. `DefaultWorkItemConfig` lives on `MilestoneBase.default_work_item_config` (per-milestone scope), NOT on `TGGroupBase`.
**Why**: A per-TG default work-item would multiply unnecessarily — every TG in every milestone gets one, most of which would remain empty (most documents route successfully through FR-52 steps 1-4). The TG is always resolvable from the inbound channel without requiring a TG-scoped landing entity. Per-milestone default keeps the SP-list footprint low. FR-83 reassignment becomes straightforward: filter candidate items by `inferred_tg_name` within the milestone. The `_unrouted` sentinel tg_name on the default work-item is a flag, not a real TG — the document's real TG lives on `DocumentIndexRow.inferred_tg_name` adjacent to the file content.
**Consequences**: `NSDPath.internal_default_workitem` uses the `_unrouted` sentinel under the milestone path (no TG segment). `DocumentIndexRow.inferred_tg_name` is required (nullable only for SP-UI direct upload where TG is explicit on the targeted DeliveryItem). `DefaultWorkItemConfig.tg_name: Literal["_unrouted"]` sentinel — the config model cannot encode a real TG. FR-83 `reassign_document_to_workitem` also syncs `DocumentIndexRow.inferred_tg_name` to the target item's tg_name (with old→new note in CommunicationLog) so the document's recorded TG matches its current landing item after reassignment.

### `[D-060]` Implementation note (2026-06-09 — channel→TG resolution lives in each ingest module per channel; email-channel resolver added at `email_service.tg_resolver`)

**Per-channel resolver placement per module boundary discipline.** The Decision body specifies channel-to-TG resolution rules (NSD ingress_nsd / email_group_alias / owner_email lookup / PLM-id reverse-lookup) but leaves the implementation site unclear. Per the 2026-06-09 cascade against `[D-053]` impl note 2026-06-08 + a same-day user-review correction: **each ingest module owns the channel-to-TG resolver for its own channel** — the resolver is NOT a single shared sub-module across all 4 channels (that would violate module boundaries; `email_service` doesn't poll NSD or PLM and shouldn't import their resolution logic).

**Per-channel resolver locations**:
- **Email channel** → `core/src/email_service/inbound/tg_resolver.py` (added 2026-06-09). Resolves from To/CC list (matches `TGGroupBase.email_group_alias` first when set) then sender owner_email reverse-lookup via TGGroupBase.
- **NSD channel** → resolver lives in the NSD polling module (Ph-1 scope: likely `storage`'s NSD client OR a future dedicated NSD ingest module). Reads `TGGroupBase.ingress_nsd` (NSD1 vs NSD2) from the inbound mount path.
- **Corp PLM channel** → resolver lives in `issue_tracker` (corp PLM polling). Reverse-looks up `DeliveryItemBase.plm_id` to find the (owner × milestone) pair → TG via the DeliveryItem.
- **SP UI direct upload** → no resolution needed (TG explicit on the targeted DeliveryItem). The SP UI form carries the item_id; HILDA reads `DeliveryItem.tg_name` directly. Returns the explicit tg_name (NOT `inferred_tg_name`, since it's direct not inferred).

**Common contract**: each channel-owning module sets `DocumentIndexRow.inferred_tg_name` when calling `storage.add_document_index_row(...)`. Storage's `NSDPath.internal_default_workitem()` consumes the resolved name as `inferred_tg_name_slug` per the prior `[D-060]` impl note 2026-06-08. The Decision body's "knowable from the inbound channel" claim is preserved verbatim — this impl note clarifies per-channel ownership.

**Supersedes the prior draft of this impl note** (initial 2026-06-09 wording incorrectly placed all 4 channel resolvers in `email_service.tg_resolver`, violating module boundaries; corrected 2026-06-09 same day via user review).

### `[D-060]` Implementation note (2026-06-08 — `inferred_tg_name` surfaced in unrouted NSD path)

**Path-organization extension.** Per FR-86 storage matrix (locked 2026-06-08 — see `[D-053]` impl note 2026-06-08), the unrouted NSD path is corrected from the original Consequences-section claim **`internal/.../<milestone>/_unrouted/<filename>`** (no TG segment) to **`internal/<carrier>/<device>/<milestone>/<inferred_tg_name>/_unrouted/<original_filename>`**. The `inferred_tg_name` segment is the channel-resolved TG (NSD ingress_nsd / email_group_alias / PLM-id reverse-lookup per this ADR's Decision body) — known at ingest even when work-item routing fails. Reason for the extension: enables TPM browsing of unrouted docs grouped by TG (better UX than a single-folder catch-all per milestone). The original Decision/Why claims that `inferred_tg_name` lives on `DocumentIndexRow` (not on the path) are **partially superseded** — `inferred_tg_name` is now on BOTH the row AND the path. The row remains the authoritative source; the path mirrors it for filesystem-level grouping. Cascading consequence: `storage/MODULE.md` `NSDPath.internal_default_workitem()` helper signature must add an `inferred_tg_name` parameter (flagged in STATUS.md In-progress 2026-06-08 MODULE.md cascade entry). The `_unrouted` sentinel remains in the path as the final segment indicating "no specific work-item resolved within this TG" — distinct from "TG resolved AND work-item resolved" which uses the full `<tg>/<item>/<doc_type>/<doc_id_slug>/revN/` pattern. The `DefaultWorkItemConfig.tg_name: Literal["_unrouted"]` sentinel on the work-item entity is unchanged — the SP-list-side work-item is still per-milestone with `_unrouted` as its tg_name; the path-side TG segment uses `inferred_tg_name` which is per-document.

## D-061: `ingress_folder` (inbound) vs `target_folder` (outbound) naming discipline
**Status**: Active · **Date**: 2026-06-07
**Decision**: Strict naming discipline across HILDA: `ingress_folder` = INBOUND, HILDA-PC-side NSD folder path under `TGGroupBase.ingress_nsd` (FR-77 Type-2 routing source); `target_folder` = OUTBOUND, customer-portal upload destination (FR-73 / FR-19 carrier-facing submission). The two namespaces are NEVER conflated in storage APIs, models, or path helpers. `TGFolderRoutingRow.ingress_folder`, `FolderRoutingEntry.ingress_folder`, `NSDPath.ingress_folder()` all denote inbound; outbound paths do not use NSDPath (customer portals are external). `DeliveryItemBase` carries both fields explicitly because they answer different questions.
**Why**: The two directions have completely different operational contexts — HILDA writes to ingress; customer portal owns target. Conflating the names risks bugs where, e.g., a folder-routing pipeline accidentally reads `target_folder` (outbound) and tries to route a document by it, or an outbound submission writes to an `ingress_folder` and contaminates the inbound pipeline. The naming discipline forces engineers to think about direction at every reference, making mistakes obvious at review time rather than at runtime. The original draft used `target_folder` for both purposes and was caught during the M:M storage refactor review.
**Consequences**: requirements.md cleanup flagged in STATUS.md (FR-77 prose currently uses "target_folder" for inbound — naming sweep queued). Code review checklist: any new field/path named `target_*` or `ingress_*` must clearly identify direction in its docstring. Customer-portal upload destinations (target_folder) are external to NSDPath entirely — they're customer-adapter concerns. `NSDPath.ingress_folder()` is inbound-only.

## D-062: `AutomationRuleOverride.rule_id` is a soft-FK to YAML; base rules YAML + overrides Postgres
**Status**: Active · **Date**: 2026-06-07
**Decision**: Base automation rules live in `customizations/rules/<customer_slug>.yaml` (FR-30; version-controlled, customer-extensible, loaded by `rule_engine` at startup). Runtime overrides (FR-31; PM/TPM SP UI edits) live in Postgres as `AutomationRuleOverride` rows. `AutomationRuleOverride.rule_id` is a soft foreign key to the YAML-defined rule_id — no DB-level FK constraint. At startup, `rule_engine` cross-references override rule_ids against loaded YAML rule_ids; orphans (override exists but rule_id absent from YAML) raise `STR-W004` warning and are ignored at evaluation time. Storage exposes `list_all_override_rule_ids()` to support the audit. Effective-value precedence per FR-30: Device override > Customer override > Global override > YAML base.
**Why**: Moving base rules to Postgres would force a separate provisioning step + duplicate the YAML-as-source-of-truth pattern already used elsewhere (customer schemas, folder routing, tag catalog). The config-as-code discipline `[D-031]` is intentional. Soft-FK preserves that discipline while still letting overrides reference rules. A hard DB-level FK would prevent PMs from temporarily disabling a rule in YAML during ops without losing override values — orphans-as-warnings instead of orphans-as-errors lets ops work continue. The rule_engine's startup audit catches genuinely dangling references without blocking evaluation. Override write-time enforcement is impossible (YAML isn't in Postgres); the audit-at-startup pattern is the closest substitute.
**Consequences**: Storage cannot enforce referential integrity at override write time. `rule_engine` startup performs `list_all_override_rule_ids()` + YAML cross-reference → `STR-W004` on mismatch. Orphan override = warning, not error; the override persists and rule_engine ignores it at evaluation. `set_override` / `clear_override` emit `CommunicationLog` audit entries with `credential_id=pm_id` for FR-31 audit accountability. `rule_engine` cache eviction on override change (via `storage.cache_delete`) keeps effective-parameter snapshots current.

## D-063: `RouteAttachmentOutput` returns `list[RouteAttachmentMatch]` (multi-match per FR-79)
**Status**: Active · **Date**: 2026-06-09
**Decision**: The `llm.ROUTE_ATTACHMENT` TaskKind's output schema returns `list[RouteAttachmentMatch]` (zero-or-more matches) instead of a single `(item_id, confidence)` pair. Each match has its own `(item_id, confidence)` typed in a `RouteAttachmentMatch` Pydantic model. Empty list = no above-threshold match → caller falls through to FR-52 step 5 (default work-item per FR-78); non-empty list = one DocumentItemAssociation row written per match per `[D-055]` symmetric M:M (FR-79 multi-item association). Replaces the prior single-pair schema in `llm/MODULE.md` `RouteAttachmentOutput`. `email_service.RoutedAttachment` is updated to carry `matches: list[AttachmentItemMatch]` mirroring this contract (adds a `RoutingResolution` source field per match for diagnostics).
**Why**: The single-pair schema forced an arbitrary "winner-takes-all" decision at the LLM layer, losing the FR-79 multi-item association signal — yet FR-52 step 4 prose explicitly says "above-threshold matches are committed" (plural). A regression test report covering VoLTE + handover, or a compliance certificate satisfying both Item-A and Item-B, must be allowed to land on both items per FR-79. The list-of-typed-matches design (a) preserves multi-match correctly, (b) keeps the contract typed (no tuple unpacking), (c) carries per-match confidence for ops visibility, and (d) allows the caller (email_service Fr52AttachmentRouter) to commit all matches in one transaction. A new `LLG-W008` (per-task warning) and `EML-W007` (per-attachment warning) surface "over-routing" when summed confidence exceeds a customer-specific threshold (default 2.0 across N matches) so ops can monitor false-multi-item without blocking the FR-79 contract. Considered (a) keeping single-pair with optional "alternate_matches" — rejected for asymmetry; (b) returning a generator/stream — rejected for prompt-template complexity (LLM must enumerate then we stream). The list-typed approach is the cleanest fit for `[D-055]` symmetric M:M.
**Consequences**: `llm.RouteAttachmentOutput.matches: list[RouteAttachmentMatch]` (typed model). `llm.RouteAttachmentMatch` new Pydantic model. `email_service.RoutedAttachment.matches: list[AttachmentItemMatch]` (typed model + per-match source from `RoutingResolution` enum). `email_service.AttachmentItemMatch` new dataclass. `LLG-W008` registered in `llm/MODULE.md` error codes. `EML-W007` registered in `email_service/MODULE.md` error codes. `Fr52Config.route_attachment_over_routing_threshold: float = 2.0` (default; configurable per customer). LLM prompt templates for `ROUTE_ATTACHMENT` updated to instruct multi-match output. Caller MUST NOT re-filter — the LLM-side threshold is the contract; over-routing is a warning, not a block. Depends on `[D-055]` (symmetric M:M permits N rows per file_hash); `[D-056]` (composite PK); FR-79 (multi-item association).

---

## D-064: HILDA→SP REST as sole HILDA-initiated state writeback channel — one-way state writeback by firewall architecture
**Status**: Active · **Date**: 2026-06-10
**Decision**: HILDA→SP REST is the **sole HILDA-initiated state writeback channel** in Ph-1/Ph-2/Ph-3+ per FR-84. Every HILDA-side state mutation that PMs/TPMs need to see in SP UI — most importantly `DeliveryItemBase.delivery_state` transitions, `CommunicationLog` row inserts, FR-87 TPM resolution fields (`tpm_reassignment_target_item_id`, `tpm_resolved_doc_type`, `tpm_revision_resolution`) — flows through `core/src/sharepoint_integration/list_crud.py:SpCrud.update_item` / `create_item`. SP UI surfaces them via its §8.1 REST polling (5–10 s delta render). HILDA does NOT expose any inbound HTTP endpoint reachable from SP; SP→HILDA flows reach HILDA only via SP-alert email per `[D-047]`. This is **one-way state writeback by architecture, not by choice** — corp/lab firewall blocks SP→HILDA HTTP unconditionally.
**Why**: Three alternatives considered. **(α) Bidirectional REST — HILDA exposes a webhook SP UI pushes state into**: rejected — corp/lab firewall blocks inbound HTTP to HILDA PC unconditionally; would require IT exception for a publicly-accessible HILDA endpoint, conflicting with `[D-007]` on-prem boundary; even if granted, the round-trip from SP UI → HILDA webhook → HILDA DB → SP REST back to update SP UI's view is no faster than the chosen pattern. **(β) Push-via-email back to SP — HILDA sends state-mutation requests to a designated SP inbox that triggers SP workflow handlers**: rejected — SP has no inbound email handler for state writes; SP UI engineer would have to build a custom SP workflow per state-change action (orders of magnitude more SP-side work); email channel is high-latency (minutes) and unreliable for state-machine semantics. **(γ) HILDA→SP REST + SP UI polling (chosen)** — HILDA owns the write contract via SpCrud; SP UI engineer's mental model stays simple ("SP REST is read-write, SP UI polls"); matches existing FR-84 outbound writeback discipline; round-trip latency bounded by polling interval (5–10 s), acceptable for non-real-time PM/TPM workflows.
**Consequences**: SP UI uses **focus-aware refresh** per SP UI engineer 2026-06-10: re-fetch list deltas on tab/window focus-gain (Page Visibility API + focus event); in-focus refresh strategy (continuous interval / interaction-triggered / hybrid) is the SP UI engineer's implementation choice; backgrounded tab does nothing (no SP server load when user isn't looking). SP UI MUST refresh on focus-gain to surface HILDA's writes that arrived while the tab was backgrounded. `core/src/sharepoint_integration` is on the hot path for every PM/TPM-visible state change — its availability gates state visibility, not just data correctness. `CommunicationLog` rows become visible to SP UI only after HILDA's `SpCrud.create_item` returns. FR-87 TPM resolution buttons: TPM is necessarily in-focus when clicking; SP UI engineer chooses whether to optimistically render or show "Saving…" until the in-focus refresh strategy picks up HILDA's writeback. Either is acceptable — `[D-064]` doesn't constrain. For long-running FR-73 carrier-package submissions, the optional `hilda.corp/status/<milestone>/submission` endpoint (per SP REQUIREMENTS §8.2) is the **only** in-flight progress channel HILDA→SP UI other than focus-driven SP REST re-fetch (browser → corp reverse-proxy → hilda-api; outbound from corp). HILDA gains no offsetting capability if firewall policy ever changes — this is architecture-by-constraint, with a sealed upside ceiling. Anchors FR-84, FR-87, SP REQUIREMENTS §8.1; cross-refs `[D-007]` (on-prem boundary), `[D-047]` (SP-alert SP→HILDA inbound channel), `[D-021]` (workflow split: `hilda-api` handles SP-mutating writes; `hilda-worker` triggers them via Celery).

---

## D-065: SP Choice-column allowed values owned by SP UI engineer — no HILDA introspection or auto-sync
**Status**: Active · **Date**: 2026-06-10
**Decision**: SP UI engineer owns SP Choice-column allowed values; HILDA's `sharepoint_integration` does NOT introspect, validate, or auto-sync them. When HILDA's canonical enums change (e.g., 4-value `ItemType` per `[D-053]` 2026-06-08 — `Confirmation` / `TEST_TECH_WAIVER_REPORT` / `COMPLIANCE_CERTIFICATION_RELEASE_NOTES` / `Default`; 5-value `DocType`; 11-value `delivery_state` per requirements 2026-06-08), HILDA architect notifies SP UI engineer (current channel: the `docs/sp_ui_engineer/HILDA_SP_Schema.xlsx` workbook's "Choice values" column, refreshed when enums change), and SP UI engineer updates the SP-side Choice column allowed values via SP UI. The column-map YAML (`customizations/sharepoint_config/customers/<slug>.yaml`) addresses the column by name only; allowed-values mismatch surfaces at runtime as `SHP-E001` from the SP REST API (HTTP 400 on invalid value), never as silent dropped data.
**Why**: Three alternatives considered. **(α) HILDA auto-syncs enum values to SP Choice columns via SP REST PATCH on column definitions**: rejected — SP 2017 column-schema mutation via REST is buggy and requires elevated permissions distinct from row-level write permissions, coupling HILDA's runtime service account to column-schema admin creates a credential blast-radius problem; also conflates enum source-of-truth: if both HILDA enums AND SP Choice values are authoritative, divergence on either side is dangerous, and centralizing on HILDA-as-truth still doesn't solve the SP UI engineer's rendering dependency on SP-side configuration HILDA doesn't model. **(β) HILDA introspects SP Choice values at startup and validates against canonical enums**: rejected — adds 8 lists × multiple Choice columns each = many startup probes that gate readiness; `SHP-E001` at first runtime write is a faster signal anyway (caught in dev/test, not production startup); doesn't fix anything (still requires SP UI engineer to update the values — introspection just moves detection earlier, doesn't avoid the manual update). **(γ) SP UI engineer owns SP-side enum values; HILDA notifies via documentation channel (chosen)** — clean ownership boundary matching `[D-004]` (HILDA owns canonical schema in `core/src/template_schema/`; SP UI engineer owns SP-side representation in `customizations/sharepoint_config/` + SP itself); HILDA architect signals enum changes via the Excel workbook (or successor canonical doc); `SHP-E001` runtime safety net catches drift on either side.
**Consequences**: `core/src/sharepoint_integration/MODULE.md` Non-goal added: "Not a SP Choice-field value synchronizer." **Process item** — when HILDA team modifies an enum in `core/src/template_schema/`, the change MUST be reflected in the Excel workbook + a notification to SP UI engineer; otherwise SP-side rendering drifts and SP UI engineer's writes fail. `SHP-E001` at runtime is the safety net but not a substitute for the notification — if SP UI engineer hasn't updated SP Choice values, any HILDA write of the new enum value fails with HTTP 400. `HILDA_SP_Schema.xlsx` becomes the canonical enum→SP-Choice-values communication channel; its currency matters operationally. Future automation candidate (Ph-3+): a CI check that diffs `core/src/template_schema/enums.py` against the Excel workbook's Choice-values columns and flags drift in PR review — defers to that horizon. Anchors `[D-004]` (HILDA vs SP UI engineer ownership boundary), FR-7 (ItemType/DocType enum authority), `[D-053]` (4-value ItemType + 5-value DocType corrected model 2026-06-08), `[D-051]` (8-list SP layout).

---

## D-066: Cross-rule firing is independent; ordering guaranteed only within a single rule's actions
**Status**: Active · **Date**: 2026-06-10
**Decision**: When multiple rules attach to the same `TriggerKind` (different `rule_id`s, distinct conditions or additive customer/device-tier additions), `rule_engine.evaluate()` returns each matching rule as an independent `RuleMatch`. `workflow_engine` schedules each `RuleMatch`'s action chain as an **independent Celery task chain**. **Ordering is guaranteed only WITHIN a single rule's `actions` list** (YAML declaration order = execution order, via Celery `chain`). **Across-rule execution order is NOT guaranteed** — multiple `RuleMatch` chains run concurrently. No priority field, no first-match-wins, no score-based ranking, no `depends_on` cross-rule dependency declarations. Configuration smell when ops needs cross-rule order: merge into one rule, OR have the second rule subscribe to a downstream trigger (e.g., `StateChange` after `UpdateState`) — these are the two escape hatches. `UpdateState` collision (distinct rule_ids both writing `delivery_state` on the same trigger) emits `RUL-W001` at YAML load time for ops triage.
**Why**: Four alternative cross-rule ordering mechanisms considered: **(α) Priority field per rule** (`priority: int`): rejected — adds an opaque ranking ops must mentally reconcile when reading YAML across scope tiers; cross-tier priority precedence (does Customer's priority win over Global's?) is its own design rabbit hole; doesn't address the actual ordering need (which is "after rule A's effect lands, do B" — a downstream-trigger problem, not a priority problem). **(β) First-match-wins** (rules evaluated in YAML order; first match terminates): rejected — kills additive customer-tier customization (Customer can't add a new rule on the same trigger without inheriting Global's full action list); makes scope-tier additivity confusing ("does Customer's rule replace Global's, or just override the rule_id?"); requires ops to think about YAML file order as semantically significant, which is fragile across refactors. **(γ) Score-based ranking**: rejected for the same reasons as priority field + opacity (how is the score computed? who computes it?). **(δ) `depends_on: <rule_id>` DAG**: rejected as unjustified complexity for an unproven need — exhaustive search of HILDA's FR-28/FR-29 catalog finds NO Ph-1 use case where cross-rule ordering is required (all ordered cases live within a single rule's action list per the FR spec — e.g., `OwnerReassigned` → `NotifyNewOwner` then `StartItemCollection` is one rule with two ordered actions, not two rules with a depends-on edge). Adding a DAG layer would impose a complexity tax on every ops reading of every YAML file, paying for a problem we don't have. **(ε) Chosen — intra-rule ordering only, cross-rule independence**: matches FR-30's "most-specific wins **per rule ID**" framing (the disambiguator is rule_id, not trigger, so multi-rule per trigger is the natural composition unit); makes YAML reading mechanical (one rule = one ordered effect; you don't have to reconcile across-rule precedence); ops escape hatches (merge rules / use downstream trigger / Ph-3+ `depends_on` if proven needed) preserve future flexibility without paying complexity tax now.
**Consequences**: `rule_engine.evaluate()` returns `list[RuleMatch]` with **unordered semantics across RuleMatches** — `workflow_engine` MUST treat each as an independent Celery chain (no `chain([rm1.actions, rm2.actions])`). When ops needs "rule B after rule A": (a) merge them into one rule (cleanest); (b) have B subscribe to a downstream trigger A's effect generates (e.g., `StateChange` triggered by A's `UpdateState`); (c) accept that A and B run concurrently if either escape hatch doesn't apply (rare per FR-28/FR-29 catalog scan). `UpdateState` collision detection: rule_engine.load() runs `collision_audit_update_state` per `[D-062]`-style structural audit; emits `RUL-W001` for any trigger where two distinct rule_ids both produce `UPDATE_STATE` actions targeting `delivery_state`; ops triages YAML (does NOT auto-block load). Documentation impact: rule_engine MODULE.md Invariant — "Cross-rule firing is independent"; rule_engine MODULE.md Key choices — "per-trigger ordered action lists, no priority/no first-match/no score"; rule_engine MODULE.md Worked examples — Example 2 (LastContactThreshold multi-rule additive composition) demonstrates concurrent firing of 2 independent RuleMatches. **If a future Ph-3+ HILDA need surfaces for cross-rule ordering**, options: (1) introduce explicit `depends_on` DAG (preferred — most readable); (2) introduce `priority` field with documented within-trigger semantics; (3) use trigger chaining via downstream events. Defer to that horizon; no Ph-1/Ph-2 cost paid for hypothetical future. Anchors `[D-022]` (rule_engine pure-evaluator boundary — workflow_engine owns scheduling), FR-28 (13 trigger kinds), FR-29 (18 Ph-1 action kinds), FR-30 (scope ladder per-rule_id), `[D-062]` (audit ownership pattern). Cross-refs `RUL-W001` (collision warning code).

---

## D-067: Customer RFI rewind from `SubmittedToCustomer` — explicit rewind transitions to `DocumentReceived` / `OutreachSent` requiring TPM attribution
**Status**: Active · **Date**: 2026-06-10
**Decision**: `tracker.state_machine.LEGAL_TRANSITIONS` extends `SubmittedToCustomer`'s legal-target set from `{Closed}` to `{Closed, DocumentReceived, OutreachSent}`. The two new transitions support customer RFI (Request For Information) and re-submission scenarios: TPM clicks "Reopen for Additional Doc" (rewind to `DocumentReceived` — additional/replacement document needed) or "Reopen for New Outreach" (rewind to `OutreachSent` — different deliverable; restart owner outreach). Both rewind transitions require TPM attribution at guard time: `trigger_source ∈ {"manual_tpm_override", "tpm_button"}`. Automated rules (`trigger_source="automated"`) MUST NOT rewind a SUBMITTED item — `TRK-E006` if attempted. Each rewind writes a `CommunicationLog` row with `action_type=state_rewind_for_rfi` + `prior_carrier_submission_ref` (the FR-18 dispatch record's correlation_id) so the audit chain remains traceable across re-submissions. SP UI engineer surfaces the two rewind buttons in the Trigger Action dropdown, state-filtered to appear only when `delivery_state == SubmittedToCustomer`.
**Why**: Three alternatives considered. **(α) New `NEEDS_REWORK` state** (add 12th `delivery_state` value; SubmittedToCustomer → NeedsRework → DocumentReceived/OutreachSent): rejected — adds a state to the canonical 11-value enum (impacts template_schema + Excel workbook + SP Choice values + every downstream consumer); SP UI engineer would build new color-coded badge + alert wiring for a state that only applies in the rare RFI scenario; the explicit-state model is over-engineered for an action that's already conceptually "go back to an earlier state". **(β) Defer to Ph-2 entirely; Ph-1 TPMs explicitly told "create a new sub-item for re-submission"**: rejected — RFI is a real Ph-1 production requirement (carriers commonly request additional documentation post-submission); forcing TPMs to create new items would (i) fragment the audit chain across two items with no parent link, (ii) require duplicating template + owner + state-machine setup work, (iii) add UX friction that conflicts with NFR-1 reliability/usability. **(γ) Chosen — explicit rewind transitions on the existing state machine, gated by TPM attribution**: cleanest state-machine model; rewind path is first-class; audit-flag distinction (`action_type=state_rewind_for_rfi` in CommunicationLog + `prior_carrier_submission_ref` correlation chain) preserves traceability; defensive against automated rule misfires (TRK-E006 rejects automated rewinds); the 2 new transitions align symmetrically with the FR-87 SP UI engineer surface pattern (state-filtered buttons that map 1:1 to tracker state changes).
**Consequences**: `core/src/tracker/state_machine.LEGAL_TRANSITIONS` row for `SubmittedToCustomer` gains 2 targets (count rises from 1 → 3). New error code `TRK-E006` registered in `diagnostics.PREFIX_REGISTRY`. New guard #8 in `tracker.guards.check_transition_guards` — "Rewind from SubmittedToCustomer requires TPM attribution" — enforces `trigger_source ∈ {"manual_tpm_override", "tpm_button"}` for the 2 rewind paths; `trigger_source="automated"` rejected with `TRK-E006`. New CommunicationLog `action_type=state_rewind_for_rfi` enum value (bounded; no proprietary content per NFR-2 / `[D-002]`). New per-row column on CommunicationLog: `prior_carrier_submission_ref` (correlation_id link to the original FR-18 dispatch row). Re-traversal semantic: rewind → `DocumentReceived` re-traverses through `OwnerClosed` (transient) → `UnderPMReview` (requires fresh `pm_approval_at` per `[D-068]`) → `ReadyForSubmission` → `SubmittedToCustomer` (re-submitted). `customer_adapter.upload_attachment` invoked on re-submission for any new/revised files; FR-68 hash-sync idempotency handles "same file already uploaded" via duplicate-skip. SP UI engineer's Ph-1 work: add 2 buttons in Trigger Action dropdown ("Reopen for Additional Doc" → `tpm_button` action verb `rewind_to_document_received`; "Reopen for New Outreach" → `tpm_button` action verb `rewind_to_outreach_sent`); state-filtered to appear when `delivery_state == SubmittedToCustomer`. PM dashboard surfacing: TRK-E006 rejections logged for ops triage if any rule unexpectedly attempts a rewind. **`[D-068]` clearing discipline applies**: rewind path MUST clear `pm_approval_at` so re-traversal of UnderPMReview requires fresh PM approval. Anchors `[D-022]` (rule_engine boundary — tracker enforces TPM attribution as the rule_engine cannot distinguish manual from automated at evaluation time), `[D-064]` (state writebacks via SpCrud), `[D-066]` (StateChange downstream-trigger dispatch on each successful rewind), FR-7 (state machine), FR-14 (manual TPM field overrides), FR-18 (carrier submission), FR-87 (SP UI engineer button surfaces).

---

## D-068: PM approval recorded via `pm_approval_at` field on `DeliveryItem` — Option B (field-based) over CommunicationLog query (Option C) or implicit (Option D)
**Status**: Active · **Date**: 2026-06-10
**Decision**: HILDA records the per-item PM approval event (FR-28 PMApproval trigger) as 2 fields on `DeliveryItemBase`: `pm_approval_at: datetime | None` + `pm_approval_pm_id: str | None`. The PMApproval-trigger flow's `workflow_engine.tasks.state.update_state` Celery task body sets these fields via `storage.update_delivery_item` BEFORE invoking `tracker.update_delivery_state(target=ReadyForSubmission)`. `tracker.guards.check_transition_guards` guard #3 reads `item.pm_approval_at` from the in-memory item snapshot (already fetched as the first guard-pipeline step); transition is allowed only when `pm_approval_at` is non-None. A `CommunicationLog` row (`action_type=pm_approval`) is written in parallel via the `AuditWriter` Protocol so the full audit chain is preserved. The fields are CLEARED (`pm_approval_at=None`, `pm_approval_pm_id=None`) on: (a) entry to `UnderPMReview` (auto-advance from `OwnerClosed` — fresh review cycle starts), (b) all rewind transitions from `SubmittedToCustomer` per `[D-067]` (re-traversal of `UnderPMReview` requires fresh PM approval), (c) `Delayed`/`Blocked` return to `UnderPMReview`. `TRK-W006` is emitted if the guard finds `pm_approval_at` non-None on an item that has just entered `UnderPMReview` — defensive detection of a missed clear.
**Why**: Three alternatives considered. **(α) Implicit — state-machine constraints alone**: don't record approval; trust that the `UnderPMReview → ReadyForSubmission` transition is legal only when the caller is the PMApproval-triggered rule's `UpdateState` action: rejected — defensively weak; a rule misfire (e.g., a buggy `UpdateState(target=ReadyForSubmission)` from an unrelated rule whose conditions happen to match) could silently advance items past `UnderPMReview` without genuine PM input; NFR-5's "PM-approval gate before any customer-facing action" loses its enforcement; debug-after-incident would be hard because there's no per-item record of who approved when. **(β) CommunicationLog query in the guard**: guard runs SQL — "is there a `CommunicationLog` row for this item with `action_type=pm_approval` AND `timestamp > most_recent_transition_into_UnderPMReview.timestamp`?": rejected — (i) requires extending `StorageWriter` Protocol with a new method `recent_pm_approval_exists(item_id)`; (ii) per-guard SQL roundtrip adds DB cost on every state-transition check (hot path; tracker target <50 ms per transition); (iii) complex "most-recent UnderPMReview entry" filter logic is fragile to schema changes; (iv) re-traversal correctness requires careful filter — would otherwise allow a stale approval from a prior UnderPMReview cycle to pass the guard. **(γ) Chosen — Option B field on DeliveryItem**: 2 fields on `DeliveryItemBase`; guard reads from already-fetched item snapshot (zero extra DB cost); explicit clear discipline on UnderPMReview entry + rewind + DELAYED/BLOCKED return covers re-traversal correctness; defensive against missed clears via TRK-W006; naturally surfaces "is this item awaiting PM approval?" on PM dashboard via single field read; `CommunicationLog` row still written in parallel for full audit chain (Option C's audit benefit preserved without Option C's query cost).
**Consequences**: `core/src/template_schema/MODULE.md` adds `pm_approval_at: datetime | None` + `pm_approval_pm_id: str | None` to `DeliveryItemBase` Public surface. Postgres `delivery_items` table gets 2 new columns via Alembic migration when development phase lands. `HILDA_SP_Schema.xlsx` adds 2 SP columns to the delivery_items tab: `PM_Approval_At` (DateTime SP type) + `PM_Approval_PM_Id` (User SP type). `customizations/sharepoint_config/customers/<slug>.yaml` example schema gains 2 mapping entries. `core/src/workflow_engine` `tasks/state.py` Celery task for the PMApproval action's `UPDATE_STATE` step must set `pm_approval_at = utcnow()` + `pm_approval_pm_id = event_context.pm_id` BEFORE invoking `tracker.update_delivery_state(target=ReadyForSubmission)` — this is workflow_engine's responsibility, not tracker's. `core/src/tracker.transitions.update_delivery_state` clearing discipline: on entry to `UnderPMReview` (from `OwnerClosed` auto-advance) AND on rewind transitions per `[D-067]` AND on `Delayed`/`Blocked` return to `UnderPMReview`, set `pm_approval_at=None` + `pm_approval_pm_id=None` in the same transaction. New error code `TRK-W006` registered in `diagnostics.PREFIX_REGISTRY` — "pm_approval_at not cleared on entry to UnderPMReview; defensive clear discipline missed". Guard #3 wording in `tracker.guards`: "READY_FOR_SUBMISSION requires `item.pm_approval_at` non-None"; `blocking_conditions=['pm_approval_required']` if absent. PM dashboard surfaces `pm_approval_required` blocking condition via existing TRK-W001 path. SP UI engineer's Ph-1 work: add 2 SP columns + alert config (Anything-changes) so PMApproval field writes propagate per FR-84; surface "Awaiting PM approval" badge on items in `UnderPMReview` where `pm_approval_at = None`. STATUS.md Flag list extends with template_schema + workbook + sharepoint_config follow-ups. Anchors `[D-022]` (tracker pure-evaluator boundary preserved — guard reads from item snapshot; no module-load cycle), `[D-064]` (pm_approval_at written via `SpWriter`/SpCrud per HILDA→SP REST), `[D-066]` (no impact on rule_engine), FR-28 PMApproval, NFR-5 (PM-approval gate before customer-facing action). Cross-refs `[D-067]` (rewind path's pm_approval_at clear discipline).

**Implementation note (2026-06-12 — pm_approval_at + pm_approval_pm_id write owner moved from HILDA workflow_engine to SP UI engineer's web part)**: SP UI engineer's 2026-06-12 review of the DeliveryItem field-visibility workbook surfaced an operationally simpler write model: when TPM clicks the Approve button in SP UI, SP UI engineer's web part code writes `delivery_state = ReadyForSubmission` + `pm_approval_at = NOW()` + `pm_approval_pm_id = <current PM/TPM>` atomically in ONE SP transaction (SP-side single-list-row write). SP-alert per `[D-047]` fires with the field deltas. HILDA's `email_service.sp_alert_parser` reads the 3 SP fields directly from the alert payload and propagates downstream (storage update + audit log + tracker guards). **HILDA workflow_engine PMApproval task body NO LONGER writes pm_approval_at + pm_approval_pm_id** — those fields are SP-side-authoritative; HILDA is read-only on them from SP. Tracker guard #3 still applies: `delivery_state = ReadyForSubmission` requires non-NULL `pm_approval_at` — but the check happens AFTER HILDA reads the values from SP, not BEFORE HILDA writes them. Clearing discipline on entry to `UnderPMReview` + rewind transitions per `[D-067]` + DELAYED/BLOCKED return STILL applies: HILDA writes back the cleared values to SP via `[D-064]` REST writeback (HILDA → SP is unrestricted per `[D-006]`). Why move write ownership SP-side: (i) operationally simpler — one SP transaction vs SP write + alert + HILDA workflow_engine task body; (ii) eliminates a HILDA workflow_engine task that was mostly trivial wrapping; (iii) matches SP UI engineer's natural "this button updates these fields" mental model. Trade-off acknowledged: SP UI engineer must enforce the atomic 3-field write discipline (not single-field writes); SP-side validation that pm_approval_pm_id resolves to the AD-authenticated current user. New error code `DSH-W005` (defensive: SP wrote `delivery_state=ReadyForSubmission` without setting pm_approval_at; HILDA detects on alert ingest; emits warning). Anchors unchanged from original Decision; modified Consequences: workflow_engine `tasks/state.py` no longer writes these fields (one less responsibility); tracker.guards.check_transition_guards still reads from item snapshot (same as before); supersedes the "BEFORE invoking tracker.update_delivery_state" sequencing in original Consequences (sequencing moves SP-side; HILDA-side is read-then-apply). FR-2 + FR-56 (column model) + sharepoint/REQUIREMENTS.md §2.4 + customizations/sharepoint_config/MODULE.md updated 2026-06-12 to reflect write owner. Maintains `[D-068]` field-based Decision (Option B over Option C / Option D) verbatim — only the WRITER changes, not the WHERE-STORED choice.

## D-069: Credential reload trigger is SIGHUP-only — HTTP admin endpoint deliberately deferred (Ph-1/Ph-2)
**Status**: Active · **Date**: 2026-06-11
**Decision**: `SopsCredentialService.reload()` (re-decrypt all `.enc.env` files + atomic cache swap after ops rotation per `[D-038]`) is triggered exclusively by SIGHUP per workload container. No HTTP admin endpoint is built in Ph-1/Ph-2. Each of the 4 workload entrypoints (`hilda-api` / `hilda-worker` / `hilda-beat` / `hilda-llm-gateway` per `[D-021]`) calls `service.install_sighup_handler()` at startup alongside `await service.load()`; the handler is a Windows-safe no-op returning False where SIGHUP / `add_signal_handler` is unavailable (production runtime is the Linux HILDA PC per `[D-026]`). Ops rotation is one command via `deploy/scripts/reload-credentials.sh` (to be added when `deploy/` lands): `docker kill -s HUP` across the 4 containers with per-container status reporting. The 2026-05-27 MODULE.md review's "SIGHUP / admin endpoint" phrasing is hereby resolved to SIGHUP-only; reload() remains never wired to auth-error scenarios (a 401/403 from an external system means stale external state, not a stale local cache).
**Why**: (a) Rotation is a rare, deliberate ops event on a single host where ops already has shell access (manual deploy model per `[D-024]` impl note). (b) Ops-edit → SIGHUP → `reload()` → atomic swap is the established project idiom — identical to the `[D-025]` rules reload (`customizations/rules` + `rule_engine.RuleSet.reload()`) and `workflow_engine.reload_beat_schedule()`, both designed 2026-06-10; credentials extend the same pattern from 2 containers to 4. (c) Alternatives rejected: **HTTP endpoints on all 4 workloads** — `hilda-worker`/`hilda-beat` are Celery processes with no HTTP server; adding listeners + ports creates new attack surface for near-zero traffic. **`hilda-api` endpoint enqueueing a Celery task** — structurally wrong for cache invalidation: a task executes on ONE worker process while the cache is per-process across all four; it would refresh one process and silently leave three stale. **`hilda-api` endpoint with sibling fan-out** — introduces an api→siblings control dependency plus partial-failure aggregation that exists only for this rare event. (d) Fully reversible — an endpoint can be added later if a remote-trigger need materializes; Ph-3+ Vault (DEF-14 proactive refresh via `Credential.expires_at`) retires the reload mechanism anyway. Thundering-herd risk from the 2026-05-27 review does not apply: 4 deliberate ~100ms sops decrypts at rotation time, never auth-error-triggered.
**Consequences**: Ops rotation runbook is shell-only (no dashboard button) through Ph-2; `deploy/scripts/reload-credentials.sh` must be added to the deploy tree and the `[D-038]` rotation runbook. Workload entrypoints (when written) call `install_sighup_handler()` + `load()` at startup — negative test required per entrypoint. `credential_service/MODULE.md` reload() docstring updated to SIGHUP-only. CRD-W002 ("reload triggered by SIGHUP — cache rebuilt") is the observable signal in structured logs.

Promoted from strand: credential-service-v1-implementation on 2026-06-11

## D-070: `.enc.env` internal env-var layout — `HILDA_<PREFIX>_*` convention with per-auth-type carrier fields
**Status**: Active · **Date**: 2026-06-11
**Decision**: Inside each decrypted credential file (`/etc/hilda/credentials/<system_type>.enc.env` per `[D-038]`), variables follow `HILDA_<PREFIX>_<FIELD>` where `<PREFIX>` comes from `SYSTEM_ENV_PREFIX` in `core/src/credential_service/protocol.py`: module-prefix abbreviation where one exists (`ITR`, `MSG`, `CAD`, `EML`, `SHP`) and the uppercased system_type for the LLM backends (`LLM_OLLAMA_A4000`, `LLM_VLLM_DGX`, `LLM_CORP_LLM` per `[D-052]` tri-backend). Fields: `AUTH_TYPE` (required when any credential is declared; one of `api_token|basic|ntlm|kerberos|oauth2_bearer`) plus the carriers that auth_type requires (`API_TOKEN`; `USERNAME`+`PASSWORD`; `KEYTAB_PATH`; `BEARER`), optional `PM_ID` (default `ops-team`) and `EXPIRES_AT` (ISO-8601; Ph-3+ Vault hook). An empty or carrier-free file declares no credential — legal for the no-auth lab LLM backends; lookups for that system raise CRD-E001. A declared-but-incomplete credential raises CRD-E004 naming the missing field.
**Why**: Per-system prefixes (vs a single generic `HILDA_CRED_*`) keep variables self-describing in ops tooling and match the MODULE.md's pre-existing `HILDA_ITR_*` example; reusing module prefixes where they exist matches the error-code prefix discipline per `[D-017]`. Empty-file-as-no-credential (vs hard error) honors the MODULE.md file-layout note that lab LLM backend files "may be empty / no-auth in default lab deployment" — the alternative (error on empty) would force ops to author dummy credentials for no-auth backends.
**Consequences**: The `[D-038]` ops runbook documents this layout verbatim; changing it later is a coordinated ops + code change. `credential_service_cli.py --validate --system <type>` is the ops-side conformance check for authored files (CRD-QC fixed-field output; `auth_type=none` covers the file-absent case). The layout is the parse contract for `SopsCredentialService._build_credential`; Ph-3+ `VaultCredentialService` replaces the file layout entirely (Vault path `secret/hilda/<pm_id>/<system_type>` per `[D-019]`) without touching callers.

Promoted from strand: credential-service-v1-implementation on 2026-06-11

## D-071: Storage holds no DeliveryItem mirror — entity resolution is the caller's job (caller-resolves discipline)
**Status**: Active · **Date**: 2026-06-11
**Decision**: `core/src/storage/` stores no DeliveryItem schema. Storage APIs that need DeliveryItem attributes receive them as explicit parameters from the caller, which resolves them from SharePoint first: (a) the `DeliveryItemMirrorTable` + `upsert_delivery_item_mirror` from the initial implementation are removed; (b) `get_default_work_item_for_milestone` is removed from storage entirely — the FR-52 caller resolves the milestone's `item_type=Default` work-item via `sharepoint_integration` and fires `INSTANTIATE_DEFAULT_WORK_ITEM` (STR-W003) when absent; (c) `reassign_document_to_workitem` (FR-83) takes explicit `target_tg_name` / `target_owner_email` / `target_plm_id` keyword params; (d) `set_folder_routing_for_tg` (FR-77) takes a required `valid_item_nos: set[int]`. In all cases the workflow_engine task body performs the SP `get_items` lookup BEFORE calling storage.
**Why**: Keeps `storage/MODULE.md` clean of DeliveryItem schema; avoids a bidirectional tracker↔storage dependency (tracker already depends on storage); avoids the single-writer discipline burden a mirror imposes (who upserts, when, what staleness is tolerable); SP remains the one canonical entity store per `[D-064]` writeback model. The minimal-mirror alternative was rejected as scope creep that grows silently as more entity attributes get requested.
**Consequences**: workflow_engine task bodies (FR-83 reassignment, FR-77 routing-table update, FR-52 step-5 default-work-item landing) each perform one SP read before the storage call — acceptable latency on TPM-triggered paths. Affected storage signatures are keyword-explicit so future entity attributes arrive as new params, not hidden lookups. STR-W003 remains registered as the caller-side "default work-item missing" signal. Anchors the caller-resolves precedent reused by `[D-072]` and the FR-87 `tpm_resolve_*` primitives.

Promoted from strand: storage-v1 on 2026-06-11

## D-072: `doc_id_slug` / `rev_number` nullable with staged-fill lifecycle; partial unique index replaces full UNIQUE
**Status**: Active · **Date**: 2026-06-11
**Decision**: `DocumentIndexRow.doc_id_slug` and `rev_number` are nullable. Staged-fill lifecycle invariant: both are NULL between ingest and resolution (classification + `[D-039]` revision determination); both are populated together atomically when the file moves to the `classified` path; never reverted to NULL once set. The secondary uniqueness becomes a **partial unique index** `(milestone_id, doc_id_slug, rev_number) WHERE doc_id_slug IS NOT NULL AND rev_number IS NOT NULL` (not a full UNIQUE — SQL NULL does not deduplicate, so any number of staged-NULL rows may legitimately co-exist). NULL-handling contract: `get_document_index_row_by_slug` cannot find staged rows by design (use `get_document_index_row_by_hash` or `nsd_path_type` queries); `list_revisions` filters NULL-slug rows out.
**Why**: The FR-86 storage-matrix docstring (which required NULL-while-staged) was correct; the non-null field declaration — carried over from the pre-`[D-053]` 2026-05-24 initial draft, before staged-fill timing existed — was the bug. Nullable-with-lifecycle beats sentinel values (an "_unresolved" slug would pollute the slug namespace and the partial-index contract). The partial unique index preserves the FR-57 exactly-one-file lookup guarantee for resolved rows while allowing the pre-resolution staged rows to co-exist.
**Consequences**: Storage-only — no `requirements.md` change (FR-86 already specified the timing), no `template_schema` change. Callers must treat NULL as the "pre-resolution" state, not an error. Migration note: the architect's patch specified a separate Alembic revision (relax NOT NULL + swap the full UNIQUE for the partial index); since the `0001` baseline had never shipped (no deployed DB), the change was folded into the metadata-driven baseline instead — acknowledged at land time. Anchors the staged-fill state model the FR-87 `tpm_resolve_*` primitives transition through.

Promoted from strand: storage-v1 on 2026-06-11

---

## D-073: SP UI engineer manually provisions SP lists from customer YAML — HILDA does not call REST to create lists
**Status**: Active · **Date**: 2026-06-12
**Decision**: SP UI engineer reads `customizations/sharepoint_config/<customer_slug>.yaml` at customer-deployment time and hand-creates SP lists + columns in SP UI directly, including configuring SP-alert email triggers + custom SP tasks (workflows, custom field types, alert-trigger configuration). HILDA does NOT call REST to create lists; `sharepoint_integration` REST writeback per `[D-064]` is unchanged (HILDA writes existing rows in already-provisioned lists, does not create lists or columns). `tracker` module assumes SP lists pre-exist when running. The `customizations/sharepoint_config/<customer_slug>.yaml` artifact is a **READ input** for SP UI engineer's manual provisioning ceremony, not a HILDA-consumed provisioning input.
**Why**: Two mechanisms were considered. **(a) HILDA-automated REST provisioning**: `tracker` module reads YAML → calls `sharepoint_integration` REST to create lists and columns programmatically. Rejected — corp SP-2017's SP-alert email triggers + custom SP tasks (workflows, custom field types, alert-trigger configuration) are load-bearing for `[D-047]` (SP→HILDA SP-alert email channel) but cannot be expressed via the REST API surface that HILDA's `sharepoint_integration` exposes. Path (a) would create SP lists structurally but leave them non-functional for the SP-alert path; SP UI engineer would still need to hand-edit each provisioned list to attach alerts and configure SP-side workflows. Path (a) buys nothing on top of (b). **(b) SP UI engineer manual (chosen)**: structurally necessary given the SP-2017 constraint; absorbs the alert/workflow configuration step as part of the same ceremony; eliminates the dual-source coordination problem.
**Consequences**: `tracker` module's contract assumes SP lists pre-exist; no list-provisioning code surface lands in `tracker` or `sharepoint_integration`. `sharepoint_integration/MODULE.md` REST writeback semantics unchanged; no list-creation API path added (Public surface stable). `customizations/sharepoint_config/<customer_slug>.yaml` semantic clarified: it's an SP-UI-engineer-readable spec, not a HILDA provisioning input. Customer onboarding flow gains an explicit SP UI engineer ceremony step: read YAML → create SP lists in SP UI → configure SP-alert triggers + custom tasks. Any YAML change to canonical-field set (new fields, renames, removed fields) requires SP UI engineer to manually update SP lists before HILDA can write to them — coordination via `docs/sp_ui_engineer/HILDA_SP_Schema.xlsx` comm channel per `[D-065]`. List-provisioning automation is structurally deferred indefinitely (not "Ph-3+ TODO") — revisiting would require either an SP version upgrade exposing alert config via REST, or a different SP→HILDA notification channel that doesn't need SP-alerts. Captured in `customizations/sharepoint_config/MODULE.md` Invariants + Key choices + Non-goals 2026-06-12. Anchors `[D-001]`, `[D-004]`, `[D-006]`, `[D-020]`, `[D-047]`, `[D-051]`, `[D-064]`, `[D-065]`. Depended on by `tracker` (assumes lists pre-exist), `sharepoint_integration` (no list-creation surface).

---

**Implementation note (2026-06-15 — per-carrier flat-list provisioning model; supersedes the 2026-06-14 4-list impl note)**: SP UI engineer's manual provisioning ceremony per this Decision targets **one flat SP list per customer** named `Tasks_<customer_id>` (e.g., `Tasks_MMK`). The ceremony per customer: create the SP list with all canonical columns (customer / device / (device, milestone) / milestone / TG / work-item per `[D-077]` impl note 2026-06-15 column inventory); configure SP-alert subscription with "Anything changes" trigger per `[D-047]` / FR-84; set role-based access (`customer_id`, `customer_jira_url`, `model` = `device_id`, `project_id`, `assigned_pm_id` ops-editable only; other columns per FR-56 column model). **Edit discipline (load-bearing)**: the 5 ops-editable-only fields above are TPM-read-only on every row; misconfig on `customer_jira_url` would break FR-25 CustomerJIRA polling; misconfig on `customer_id` / `model` would break HILDA's NSD path construction (FR-13) and folder routing (FR-77). Field-level role restriction is the SP web part's responsibility per the SP UI engineer's existing role-based control pattern (same pattern used for `pm_approval_at` / `last_reminder_triggered_at` SP-managed audit fields). Authoritative SP-side schema = `docs/sp_ui_engineer/SP_lists_authoritative.xlsx` (single tab per customer; deprecated `HILDA_SP_Schema.xlsx`). Anchors: `[D-019]`, `[D-020]`, `[D-047]`, `[D-064]` (writeback to `Tasks_<customer_id>`), `[D-068]` (SP-side audit field write pattern), `[D-077]` (per-carrier flat-list coupling).

## D-074: SP↔HILDA integration via Variant A — HILDA server-renders document pages; SP renders link-out; no SP-side cross-origin XHR
**Status**: Active · **Date**: 2026-06-12
**Decision**: SP UI consumes HILDA's document-enumeration / download surface via **top-level browser navigation**, not SP-page-JS cross-origin fetch. SP web parts (classic content editor / script editor, or SPFx) render per-DeliveryItem **"View Documents"** links → `<a href="https://hilda-proxy.corp/docs/<delivery_item_id>" target="_blank">`. TPM clicks → browser opens new tab → corp reverse proxy forwards → HILDA's `dashboard` module **server-side-renders the document section as HTML** (Jinja2 recommended for Ph-1) with embedded short-lived download tokens. TPM clicks download link → browser does its own direct GET to `/dl/<scoped_token>` → HILDA streams the file. Auth on both endpoints is Windows Integrated (Kerberos / SPNEGO) — auto-attached by browser; requires `hilda-proxy.corp` in TPMs' Local Intranet zone via group policy. Two clicks total per download (open HILDA tab + click download link); single-click download is not architecturally achievable given the constraint below.
**Why**: Three integration mechanisms were considered. **(α) SP web-part JS `fetch()` to HILDA — cross-origin XHR with `credentials: 'include'`**: rejected based on direct empirical test — fails in user's corp environment. Likely root cause is corp SharePoint farm's Content Security Policy (`connect-src` restricted to SP origins only) or corp network ACL blocking SP-origin browser tabs from reaching `hilda.corp` directly. Either way, the JS-fetch path is structurally closed for this deployment and fighting the corp policy layer is out of scope. **(β) Iframe embedding**: SP web part renders `<iframe src="https://hilda-proxy.corp/docs/<di_id>">`; HILDA emits `Content-Security-Policy: frame-ancestors https://sp2017.corp` permitting SP to iframe it. Viable but defers visual integration polish to Ph-2 (TPMs report whether "new tab" UX is disruptive enough to warrant the iframe-policy work); not chosen for Ph-1 because the new-tab Variant A is simpler and the UX difference is a polish concern, not a correctness concern. **(γ) Variant A — top-level browser navigation (chosen)**: SP renders link; browser handles the GET; HILDA server-renders HTML. Zero browser-policy concerns; standard TPM browser-UX pattern; the JSON contract from FR-57 becomes an internal-only data shape for HILDA's own `dashboard` module's server-side rendering, NOT a public surface called by SP-side JS.
**Consequences**: `dashboard` module gains **HTML server-side rendering responsibility** (Jinja2 templates for FR-59 document section, FR-60 review-results display, FR-61 download endpoint inline + attachment dispositions, FR-31 admin overrides view) — not just JSON-API responsibility. FR-57's JSON shape becomes an internal contract for `dashboard`'s own server-side renderer; CORS allowlist not needed Ph-1 for the document-enumeration endpoint (no cross-origin XHR consumer). SP UI engineer's web part is simpler — renders bare hrefs, no fetch logic, no fallback handling. Auth via Windows Integrated (Kerberos/SPNEGO) covers both `/docs/<id>` and `/dl/<token>` endpoints identically (top-level GET; browser auto-attaches Negotiate). Token freshness: tokens generated at HILDA HTML-render time, embedded in the response, 300s TTL; never crosses SP↔HILDA boundary; TPM-naturally-reopens-HILDA-after-FR-87-resolution gives fresh tokens for free. Two-click UX is accepted as Ph-1 cost; iframe Variant B reserved as Ph-2 polish IF TPM feedback warrants. The original FR-57 framing "JSON consumed by SP-side JS" is superseded — request shape (single GET returns metadata with embedded download_url) preserved; consumer shifts from SP-side JS to HILDA-internal Jinja templates. Captured in `dashboard/MODULE.md` when drafted (next architecture session; promoted Batch 2 → Batch 1 same session). Anchors `[D-006]` (Kerberos auth), `[D-064]` (writeback unchanged — this is the OTHER direction), NFR-16 (HILDA-mediated download). Depended on by future `core/src/auth/` module (Kerberos middleware shared by dashboard + downstream HTTP endpoints). FR-61 requirements.md text reword (queued 2026-06-12) needed to reflect the path-agnostic resolution semantic — distinct from this Decision; FR-61's existing prose predates the 2026-06-08 FR-86 4-path cascade.

## D-075: FR-87 TPM-resolution UX moves from SP-side field write to HILDA-tab same-origin form POST
**Status**: Active · **Date**: 2026-06-14


**Context**: FR-87 (TPM document resolution — strict order A → B → C) was originally specified with SP-side field write semantics: TPM clicks button in SP UI → SP writes `tpm_reassignment_target_item_id` / `tpm_resolved_doc_type` / `tpm_revision_resolution` field on the DeliveryItem SP row → SP-alert fires per `[D-047]` → `email_service.sp_alert_parser` routes to a HILDA-side resolution handler → HILDA processes + writes back to SP. This matched the FR-84 SP→HILDA channel pattern used by every other SP button (Start Collection, Submit, Close All Items, Send Reminder, Approve, Refresh).

**Decision**: For FR-87 specifically, TPM-resolution buttons move from SP UI to HILDA's rendered document section (per FR-59 / `[D-074]` Variant A). TPM views document in HILDA tab; FR-87 button surfaces inline on document rows where `nsd_path_type ∈ {staged_not_classified, staged_not_revision, unrouted}`; clicking does a **same-origin form POST** from HILDA tab to HILDA's dashboard endpoint (`/docs/<delivery_item_id>/reassign`, `/resolve-doc-type`, `/resolve-revision`); HILDA processes directly + writes back to SP via `[D-064]` REST writeback as **read-only audit columns** (TPM-editable input semantics removed from the 3 SP fields). No SP-alert round-trip for FR-87.

**Why**:
- **(a)** TPM needs to SEE the document content before resolving (especially step B doc_type and step C revision picks). Document is in HILDA's rendered tab per FR-59 / `[D-074]` Variant A; rendering the same document in BOTH the SP item dialog AND HILDA tab would be duplicate effort and stale-state risk.
- **(b)** SP-side field write was UX-awkward for per-document actions on a per-item SP dialog — one DeliveryItem can have multiple documents in different staged paths; SP per-item dialog can't cleanly surface per-document buttons.
- **(c)** HILDA-tab same-origin POST is unblocked by corp policy per `[D-074]` (the cross-origin XHR ban only applies SP→HILDA; HILDA→HILDA same-origin is unrestricted). No firewall fight, no SP-alert latency.
- **(d)** Invalid (item_type, doc_type) combinations can be rejected at the dashboard endpoint with a form-redisplay error — better UX than the SP-alert round-trip model where invalid saves silently landed on staged-not-classified.
- **(e)** Eliminates one SP-alert action verb mapping per `sharepoint/REQUIREMENTS.md §7.4` (3 verbs gone: `tpm_reassign_to_workitem` / `tpm_resolve_doc_type` / `tpm_resolve_revision`).

**Rejected alternatives**:
- **(α) Keep SP-side field writes (original FR-87 model)**: rejected — per-document buttons in per-item SP dialog UX problem (b); SP-alert latency adds 5-15s to TPM round-trip vs near-instant HILDA-tab POST; invalid-pair UX is worse.
- **(β) Render FR-87 dropdowns BOTH in SP UI and HILDA tab**: rejected — duplicate rendering effort, dual source-of-truth risk on what TPM picked.
- **(γ) Dedicated FR-87 SP web part separate from milestone view**: rejected — same UX problems as (α); doesn't help.

**Consequences**:
- Dashboard module gains 3 new POST endpoints: `/docs/<delivery_item_id>/reassign`, `/resolve-doc-type`, `/resolve-revision` (added to dashboard MODULE.md as part of dashboard-v1 strand work; soft-flag because additive Public surface).
- The 3 SP fields `tpm_reassignment_target_item_id` / `tpm_resolved_doc_type` / `tpm_revision_resolution` become **read-only audit display columns** in SP DeliveryItems list — TPM-editable input semantics removed. SP UI engineer applies column-level read-only permission. Schema discipline added to `customizations/sharepoint_config/MODULE.md` 2026-06-12 (part of D-DRAFT-FR87).
- `sharepoint/REQUIREMENTS.md §4.9 / §4.10 / §4.11` need rework parallel to this FR-87 rewrite (buttons live in HILDA tab, not SP web part).
- `sharepoint/REQUIREMENTS.md §7.4` (SP-alert action-verb conventions) — 3 verbs for FR-87 are obsoleted; remove from §7.4 (sp_alert_parser no longer needs to recognize them).
- `email_service.sp_alert_parser` Ph-1 implementation does NOT need FR-87 action-verb handlers (reduces email_service module's Ph-1 surface area slightly).
- Dashboard MODULE.md adds 3 new error codes: DSH-E005 (FR-87 step A invalid target item), DSH-E006 (FR-87 step B invalid doc_type for item_type), DSH-E007 (FR-87 step C revision picker mismatch) — to be locked during dashboard architecture review.
- Storage's `tpm_resolve_doc_type` + `tpm_resolve_revision` + `reassign_document_to_workitem` storage APIs (already landed per `D-071` / `D-072`) are unchanged — the same APIs serve both the old SP-alert model (if revived) and the new HILDA-tab POST model. The strand work for FR-87 is purely in dashboard (new endpoints) + sharepoint_config (column permission discipline).

**Anchors**: `[D-074]` (Variant A SP↔HILDA integration); `[D-053]` impl note 2026-06-08 (FR-87 strict A → B → C); `[D-047]` (SP-alert channel — FR-87 no longer uses it); `[D-064]` (HILDA→SP REST writeback — used for audit-column updates after FR-87 click); `[D-006]` (on-prem AD auth — NTLM per 2026-06-14 impl note; covers HILDA-tab same-origin POST authentication via the same Negotiate flow).

Promoted from strand: dashboard-v1 on 2026-06-14

---

## D-076: FR-87 button POST handler is sync-validate-and-enqueue; async tail runs in workflow_engine; async-tail errors surface via 3 channels
**Status**: Active · **Date**: 2026-06-14


**Context**: D-DRAFT-FR87 locked that FR-87 buttons move to HILDA-tab same-origin POST. The dashboard endpoint POST handler must decide: (a) block on full FR-87 processing (sync to TPM, including the `[D-039]` Step 2 LLM re-run for Step B which is 10-30s) and return either form-redisplay-error or 303-redirect-with-final-state; OR (b) do only sync work (validation + immediate storage write + workflow_engine task enqueue) and return 303 in <500ms, with the async tail completing in workflow_engine and errors surfacing via separate UX channels after the redirect. The original D-DRAFT-FR87 wording conflated these — "invalid choices rejected at the endpoint" was vague about whether all errors block or only validation errors block.

**Decision**: **Option (b) — sync-validate-and-enqueue.** Dashboard's FR-87 POST handlers do **only** synchronous work:
- Validate auth, Choice-value membership, FR-86 alignment, target item (Step A), `doc_id_slug` existence (Step C), FR-87 button token freshness
- Call storage's `reassign_document_to_workitem` / `tpm_resolve_doc_type` / `tpm_resolve_revision` for the immediate DB row update + NSD file move (sub-second)
- Enqueue workflow_engine task for the async tail
- Return 303 redirect to `GET /docs/<delivery_item_id>`

Net latency target: **sync POST handler returns 303 in <500ms** (validation + sub-second storage + sub-second enqueue). Async tail in workflow_engine: `[D-039]` Step 2 LLM re-run (Step B only; 10-30s typical); FR-86 storage matrix re-run; final NSD path move if `[D-039]` resolves; FR-77 carrier-upload if doc reaches classified (subject to `no_customer_upload`); `[D-064]` SP writeback for audit columns; review pipeline trigger if `review_required=true`.

**Sync errors surface as form-redisplay**: DSH-E005 (target item invalid Step A), DSH-E006 (FR-86 alignment violation Step B), DSH-E007 (revision picker mismatch Step C), STR-E005 (cross-milestone association), STR-E007 (expired button token).

**Async-tail errors surface to TPM via three channels** (in priority order):
- **(α) Primary — inline document-row badge on next `/docs/<delivery_item_id>` visit**: dashboard renders the row with a status badge read from CommunicationLog query on `(file_hash, delivery_item_id)` — e.g., "🔴 `[D-039]` re-run failed: LLM gateway timeout. Retry?" with retry button.
- **(β) Secondary — top-of-page banner on `/docs/<delivery_item_id>`**: for the most recent FR-87 action within N minutes (configurable; default 5 min), show banner: "Last action `tpm_resolve_doc_type` had a downstream error: <message>. [Retry] [Dismiss]". Per-TPM session.
- **(γ) Escalation — TPM email**: when async failure is unrecoverable AND > N minutes elapsed since submit (configurable; default 10 min), send TPM email with error details + retry link. Reserved for genuinely-stuck cases — too noisy as primary channel.

**Why option (b)**:
- **(a) sync model**: rejected — 10-30s blocked HTTP response is poor UX (corp reverse proxy may timeout; blocks dashboard worker thread; user perceives "frozen page"; not consistent with HILDA's async-by-default workflow_engine pattern). The cleaner either-or outcome is appealing but the latency cost is too high.
- **(b) async-with-status (chosen)**: matches HILDA's overall async pattern; sub-500ms HTTP response keeps reverse proxy happy; TPM experience: click → immediate redirect → "processing" badge → eventual completion (via focus refresh OR new tab opens). The three-channel async-error UX (badge / banner / email) gracefully handles real-world async failures including LLM timeouts and SP writeback latency.

**Rejected alternatives**:
- **(γ) WebSocket/SSE push notifications** for async completion: rejected — adds JS state engine to dashboard's "no SPA, no client-side framework" Invariant; same-origin would work in HILDA tab, but the badge-on-next-render approach is simpler and matches server-render-only discipline.
- **(δ) Synchronous AJAX polling from HILDA tab to dashboard for async status**: rejected — same reason as γ; introduces client-side JS state.
- **(ε) Long-polling**: rejected — same.

**Consequences**:
- Dashboard's FR-87 POST handlers MUST be sub-500ms-budget; LLM calls + FR-77 + SP writeback MUST be in workflow_engine task bodies, not in dashboard's sync path.
- Dashboard adds inline-badge + top-of-page-banner rendering to `GET /docs/<delivery_item_id>` — reads CommunicationLog for status of recent FR-87 actions on docs in this DeliveryItem; renders the appropriate badge/banner.
- workflow_engine task body for FR-87 async tail emits CommunicationLog rows on success AND failure (with detailed error code + message); these rows drive the dashboard's badge/banner rendering.
- New error codes for the async-tail outcomes: `DSH-W003` (FR-87 async tail in progress; informational), `DSH-W004` (FR-87 async tail failed; surfaced as badge); existing `LLG-W006` (LLM rate-limit; surfaced as badge for Step B specifically); existing `STR-W007` (stale-staged-document) might fire if NSD path move fails post-commit.
- TPM email integration for escalation channel (γ): uses existing `email_service` outbound capability; new email template for FR-87 stuck-resolution notification; rate-limited (one email per (TPM, document) pair per 24h).
- FR-87 button token freshness check: tokens generated at HTML-render time (per FR-61 download token pattern); 300s TTL; STR-E007 if expired (form-redisplay with "session expired" + auto-redirect to fresh `/docs/<id>`).
- Async-tail errors that occur DURING the workflow_engine task body retry per workflow_engine's standard Celery retry policy; only after retries exhaust does the error become user-surfaced via channels (α)/(β)/(γ).

**Anchors**: `[D-022]` (Celery + Redis broker — workflow_engine async pattern); `[D-074]` (Variant A SP↔HILDA integration); `[D-039]` (LLM revision determination — Step 2 is the long-haul work in FR-87 Step B); `[D-064]` (HILDA→SP REST writeback for audit columns); D-DRAFT-FR87 (parent decision; this one refines the sync/async boundary).

Promoted from strand: dashboard-v1 on 2026-06-14

---

## D-077: HILDA runtime SP coupling = 4 lists (Customers + Devices + Milestones + DeliveryItems); SP-list-row IDs resolve customer/device slugs at SP-alert receive — no YAML for customer/device data
**Status**: Active · **Date**: 2026-06-14


**Context**: SP UI engineer 2026-06-12 review surfaced that HILDA's Linux service layer currently has runtime read/write dependencies on 6 SP lists. Earlier 2026-06-12 ratifications already eliminated User + PMCredential SP-list dependencies. User initially proposed (2026-06-12 D-DRAFT-Z v1) moving Customer + Device data to `customer.yaml` and denormalizing `customer_id` + `device_id` onto Milestone SP rows. **2026-06-13 review** of the SP-alert routing key `(ProjectID, MinorMilestone, ItemNumber)` showed `MinorMilestone` is uniquely scoped to a (customer, device) per FR-5 but neither slug is in the tuple — `sp_alert_parser` cannot map alert → slugs without an extra step. **2026-06-14 review** of pruned customer.yaml showed it carried only 2 leaf values (`customer_jira_url` + `assigned_pm_id`) under a folder name already encoding `customer_id` — the file didn't earn its own existence. User proposed promoting Customers + Devices SP lists from "SP-display-only" to "HILDA-readable" — slug values + the 2 leaf fields all live on existing SP rows.

**Decision**: HILDA's Linux service layer runtime SP coupling is **4 SP lists**: Customers + Devices + Milestones + DeliveryItems (read scope); writeback (per `[D-064]`) is Milestones + DeliveryItems only — HILDA does NOT write Customers or Devices SP rows (ops edits those directly via SP UI). Users + PMCredentials remain SP-display-only (HILDA reads neither at runtime). **No `customer.yaml` file is created**; customer + device data lives entirely on SP. SP-alert resolution flow: (1) `sp_alert_parser` extracts SP row IDs from the alert; (2) does SP REST GET on Customers row by `customer_id` → reads `customer_id` + `customer_jira_url`; (3) does SP REST GET on Devices row by `device_id` → reads `device_id` + `assigned_pm_id`; (4) caches reads for the alert dispatch duration to amortize batched per-item alerts in the same milestone.

**SP-list schema additions** (SP UI engineer adds, ops-editable only):
- **Customers SP list** (2 new columns): `customer_id` (HILDA-readable identifier; ops sets at customer-onboarding), `customer_jira_url` (FR-25 base URL).
- **Devices SP list** (2 new columns): `device_id` (HILDA-readable identifier), `assigned_pm_id` (PM identity for FR-19/FR-25/FR-51 credentialed external calls; per FR-25, PM ≡ TPM in this deployment).
- TPM cannot edit any of these 4 fields (misconfig on `customer_jira_url` would break FR-25 polling; misconfig on slugs would break HILDA's NSD path construction).

**Why**:
- **(a) Single source of truth**: all customer + device data lives on SP. Ops edits SP rows directly via TPM SP UI's ops-role view — no git commit + bind-mount + SIGHUP cycle for changing `customer_jira_url` or `assigned_pm_id`. TPM, ops, and HILDA all see the same row.
- **(b) Eliminates customer.yaml**: the file would have carried only 2 leaf values per pruned 2026-06-14 schema; not worth a separate file + loader + reload path.
- **(c) Solves the slug-resolution gap**: SP-alert tuple doesn't encode slugs; HILDA reads them from SP rows directly. Cost: 2 SP REST GETs per alert (~100ms); amortized via per-dispatch cache.
- **(d) Cleaner SP UI engineer ownership** for Customer/Device UX (validation, permissions, presentation); HILDA contract becomes "we read 4 lists, write to 2, never touch Users/PMCredentials."
- **(e) Closes the open question** about who maintains Customer/Device rows: ops via SP UI (with role-based control restricting HILDA-readable fields to ops, not TPM).

**Rejected alternatives**:
- **(α) D-DRAFT-Z v1 (customer.yaml + denormalized slugs on Milestone)**: rejected — customer.yaml carries 2 leaves under a folder name that already encodes customer_id (doesn't earn its file); denormalization is ops-coordination overhead with no offsetting HILDA benefit (HILDA can resolve via Customers+Devices SP-list reads under same caching budget).
- **(β) Encode customer_id + device_id in the SP-alert routing tuple itself (expand 3-key → 5-key)**: viable but requires SP UI engineer to inject 2 extra fields into every SP-alert email template; brittle to SP-alert config drift; loses SP as single point of truth (slugs would live in 2 places: SP row + alert payload).
- **(γ) HILDA-written Title encoding (write `Title = "<customer_id>__<device_id>__<milestone_name>"` on Milestone rows; parse from alert subject)**: viable; zero new SP columns; but Title becomes HILDA-format-coupled, fragile across SP UI engineer's display changes.
- **(δ) Keep HILDA reading all 6 SP lists**: rejected — Users + PMCredentials are SP-display-only by `[D-019]` discipline; expanding scope back is unjustified.

**Consequences**:
- **No** `customer.yaml` file; **no** `customizations/template_schemas/<customer_id>/customer.yaml` directory pattern for customer data. (The 2-YAML-per-customer model becomes `template.yaml` + `tg_groups.yaml` only per FR-40.)
- HILDA's runtime SP coupling grows from D-DRAFT-Z v1's 2-list to **4-list** (Customers + Devices added). SP REST GET budget: 2 extra reads per SP-alert dispatch; cached for batch duration.
- `sharepoint_integration.SpCrud.get_items` for Customers + Devices becomes called at runtime; for Users + PMCredentials remains not called.
- `Milestone` SP list does NOT gain `customer_id` + `device_id` columns (was the D-DRAFT-Z v1 plan; reversed 2026-06-14). Milestone has `device_id` lookup → Devices row carries slug.
- `HILDA_SP_Schema.xlsx` Milestones tab: any prior `customer_id` / `device_id` rows are dropped. Customers tab gains `customer_id` row (xlsx row 76); Devices tab gains `device_id` + `assigned_pm_id` rows (xlsx rows 79-80). Confirmed in 2026-06-14 xlsx review.
- `customizations/sharepoint_config/customers/example.yaml` (SP-side schema mapping): Customers + Devices sections include the new columns; Milestones section drops `customer_id` + `device_id` denormalization. Architecture-phase cascade.
- SP UI engineer's role-based control (already confirmed per xlsx row 13 owner_corp-usa_email pattern) restricts edit access to the 4 new HILDA-readable fields to ops role only (TPM role sees them read-only).
- TPM-runtime edits to Customer/Device SP rows DO fire HILDA-bound SP-alerts on these lists (alerts trigger HILDA cache invalidation for the affected row).
- Ops workflow for Customer/Device changes: ops edits SP row via SP UI → SP-alert fires → HILDA invalidates cache for that customer_id/device_id → next alert refetches. No git/YAML/SIGHUP.
- Multiple FR rewrites already in place: FR-2 (no customer.yaml; tracker creation resolves via SP reads), FR-13/FR-31/FR-77 (slug source is SP via alert-driven cache, not YAML), FR-40 (2-YAML schema: template + tg_groups), FR-84 (no Milestone denormalization).

**Anchors**: `[D-001]`, `[D-004]`, `[D-006]`, `[D-019]` (credential discipline — PMCredential SP list stays HILDA-unread), `[D-020]` (SharePointListProvider — extends to Customers + Devices SP), `[D-047]` (SP-alert channel; resolution-via-SP-read pattern documented in FR-84), `[D-051]` impl note (TG denormalization — unchanged; separate concern from customer/device), `[D-064]` (writeback Milestones + DeliveryItems only — unchanged), `[D-068]` (SP-side audit field write pattern — generalized), `[D-071]` (storage doesn't mirror DI; caller-resolves applies to Customer+Device too), `[D-073]` (SP UI engineer provisions — extended via impl note 2026-06-14 for 4 new columns).

**Supersedes**: D-DRAFT-Z v1 (2026-06-12; 2-list scope + customer.yaml + Milestone denormalization).

Promoted from strand: dashboard-v1 on 2026-06-14

---

**Implementation note (2026-06-14 — promoted from strand `dashboard-v1`)**: **Amendment 2026-06-14 — joint Device key + 5-field routing + slug→id + single template.yaml**:
1. **Devices SP list PK = `ProjectID`** (corp-assigned external value, e.g., `1001`; not auto-Counter). `model` is a field column (= `device_id` in template.yaml, e.g., `MODEL-A`). Device lookup at SP-alert receive is direct PK lookup by ProjectID; `Model` cross-validated against the row's `model` column for integrity (log `EML-W008` on mismatch but don't fail). Supersedes joint-key wording — same Model may appear across multiple ProjectID rows but lookup is single-key by PK.
2. **SP-alert payload routing key** expands from 3-tuple `(ProjectID, MinorMilestone, ItemNumber)` to **5-field set**: `customer_id` (value from subject suffix in `Alert_Tasks_<customer_id>` — example value `MMK` in subject `Alert_Tasks_MMK - NVIOT - AGPS Test Results`; note that `MMK` is a customer_id VALUE, not a field name), plus `Model`, `ProjectID`, `MinorMilestone`, `ItemNumber` (all body fields).
3. **Naming convention**: `customer_slug` → `customer_id`, `device_slug` → `device_id`, `milestone_slug` → `milestone_id`, `item_path_slug` → `item_path_id`, `tg_path_slug` → `tg_path_id` throughout FRs + storage + SP-config. SP-list Counter PKs (auto-generated Integer) referred to as `customer_pk` / `device_pk` / `milestone_pk` / `item_pk` to avoid collision with business-identifier `_id` namespace.
4. **Single template.yaml per customer** at `customizations/template_schemas/<customer_id>/template.yaml` (was 2 files: template.yaml + tg_groups.yaml). Hierarchical structure: `<customer_id> → <device_id> → <milestone_id> → work_items[]`. TG metadata fields denormalized per-work-item with HILDA-validated equality discipline (same `tg_name` group within a milestone must have identical TG-field values across all items; `TSC-W005` warning on divergence). tg_groups.yaml is obsolete.
5. **Customer-onboarding seed flow**: template.yaml is the deployment-time SEED for all SP rows (Customers + Devices + Milestones + DeliveryItems); HILDA reads + writes initial rows via `[D-064]` on ops bootstrap command/SIGHUP. Existing SP rows never overwritten (SP is canonical for in-flight state); template-reload diff writes NEW rows only (for new device launch / new milestone).

**Implementation note (2026-06-14 — promoted from strand `dashboard-v1`)**: **Final R&R lock 2026-06-14 (supersedes earlier same-day drafts)**:
1. **SP UI engineer = SOLE SP row-creation authority**. setup_milestone TPM task per (customer, device) creates Customer + Device + Milestone + DeliveryItem rows using template.yaml as field/default reference. SP UI engineer assigns project_id (Devices PK) at row create.
2. **HILDA NEVER creates SP rows** — at any time, under any condition. HILDA = state transitions + audit-field writes on existing rows via [D-064]; never SpCrud.create_item.
3. **Template.yaml change semantics**: (a) change has no effect on active milestones; (b) SIGHUP HILDA after template.yaml change has no row-creation effect; (c) no on-demand work-item addition to active milestones — template.yaml edits apply only to FUTURE setup_milestone runs.
4. **Hierarchy**: customer to milestones (with work-items, shared across all devices) + devices (separate, per-device metadata only). NOT customer to device to milestone. Work-items per (customer, milestone) — single source — applied to every (device, milestone) combination at setup_milestone time.
5. **Devices PK = project_id** (not joint key, not auto-Counter). model is a field column carrying the device_id value (e.g., MODEL-A). Same model may appear across multiple ProjectID rows. Device lookup at SP-alert receive: direct PK GET by ProjectID; Model cross-validated against model column (log EML-W008 on mismatch).
6. **Template.yaml does NOT contain project_id** — SP UI engineer assigns at Devices SP row creation.
**Why cleaner**: (a) sharp R&R boundary — SP UI engineer owns row creation; HILDA owns state transitions; zero collision risk; (b) HILDA is structurally insulated from template.yaml edits — no diff logic, no bootstrap/diff mode detection, no SIGHUP-driven write path; (c) milestones-at-customer-level eliminates the M*D*W work-item duplication problem when devices share milestones; (d) ProjectID-as-PK matches corp convention.

**Implementation note (2026-06-15 — per-carrier flat-list model; supersedes the 2026-06-14 normalized 4-list Decision body above as the operative runtime model)**: SP UI engineer 2026-06-15 disclosed his actual SP implementation is **1 flat SP list per customer (carrier)** named `Tasks_<customer_id>` (e.g., `Tasks_MMK`, `Tasks_CARRIER1`). Each row represents ONE work-item (DeliveryItem) and carries every customer-, device-, (device, milestone)-, milestone-, TG-, and work-item-level field as columns — duplicated by scope:
- **Customer-level** (same value across the entire list): `customer_id`, `customer_jira_url`
- **Device-level** (same value across all rows for a device): `model` (= `device_id` in template.yaml), `project_id` (SP-auto-generated when a new device is added to a carrier)
- **(device, milestone)-level** (same value across all rows sharing the same device + milestone): `assigned_pm_id` (different milestones for the same device may have different assigned PM)
- **Milestone-level** (same value across all rows in the same milestone): `minorMilestone` (= `milestone_id`), `target_date`, `Milestone.status`, `milestone_collection_started_at`, `milestone_submission_triggered_at`, `close_all_items_triggered_at`, `refresh_requested_at`, `download_package_request_timestamp`, `download_package_url`, `download_package_status`, `download_package_generated_at`
- **TG-level** (same value across all rows in the same TG within a (device, milestone)): `tg_path_id`, `tg_owner_corp_usa_email`, `tg_owner_corp_email`, `tg_owner_corp_id`, `tg_email_group_alias`, `corp_id_list`, `default_cc_list`, `folder_routing_enabled`, `tracking_enabled`, `ingress_nsd`
- **Work-item-level** (unique per row): `item_no`, `item_name`, `item_path_id`, `item_type`, `delivery_state`, `tracking_modality`, `owner_corp_usa_email`, `owner_corp_email`, `owner_corp_id`, `last_owner_contacted`, `last_reminder_triggered_at`, `actual_completion_date`, `pm_approval_at`, `pm_approval_pm_id`, `doc_count`, `review_required`, `is_milestone_gating`, `no_customer_upload`, form-factor flags, `comment`, etc.

**HILDA read pattern**: REST `$filter` on column values — `?$filter=minorMilestone eq 'LE-2' and item_no eq 7` returns exactly one row carrying all fields. No SP Counter PK navigation, no foreign-key chains.

**HILDA write pattern**: milestone-scoped writes (Start Collection, Submit to Carrier, Close All Items, Refresh, Download Package, Milestone.status) touch **every row in the milestone** (N rows × 1 column write). Each per-row write fires an SP-alert; HILDA processes the first alert in the burst and deduplicates the rest via FR-11 / `[D-082]` cascade-dedup. SP UI engineer's web part performs the same N-row write pattern on TPM button clicks.

**SP list provisioning**: SP UI engineer's `setup_milestone` web part provisions `Tasks_<customer_id>` (columns + alert subscription + role-based access per `[D-073]`) the first time it runs for a new customer; subsequent runs write rows into the existing list.

**Trade-off accepted**: heavy column duplication + N-row write amplification on milestone-scoped writes vs. simpler SP provisioning (one list per customer, no FK chains for SP UI engineer to manage) + simpler HILDA reads (single filter query per SP-alert).

**Ph-2 plan**: normalize the per-customer flat list into N SP lists (Customers + Devices + Milestones + DeliveryItems with FK chains, milestone-sentinel-row pattern, per-tier permissions). Deferred from Ph-1 to keep SP provisioning simple; tracked in STATUS.md Flags.

**Cascade FRs/NFRs**: FR-2 (R&R lock + flat-list structure), FR-5 (uniqueness `(model, minorMilestone, item_no)` within `Tasks_<customer_id>`), FR-6 (`Milestone.status` N-row writeback), FR-9 (`assigned_pm_id` source = work-item row column scoped per (device, milestone)), FR-13 (NSD path values read from work-item row directly), FR-40 (`sharepoint_config` per-customer mapping), FR-84 (single filter GET resolution; N-row duplication + dedup for milestone-scoped writes), NFR-21 (per-carrier flat-list read + same-list write + cascade-dedup).

**Cascade artifacts**: `docs/sp_ui_engineer/SP_UI_button_actions.md` (milestone-scoped buttons write to every row in the milestone; HILDA deduplicates alerts); `docs/sp_ui_engineer/SP_lists_authoritative.xlsx` (single tab per customer enumerating columns; `HILDA_SP_Schema.xlsx` deprecated 2026-06-15); `customizations/sharepoint_config/<customer_id>.yaml` (single mapping per customer).

**Anchors**: `[D-073]` (SP UI engineer provisioning — impl note 2026-06-15 covers `Tasks_<customer_id>` per-customer ceremony), `[D-064]` (REST writeback — N-row writes for milestone-scoped columns), `[D-071]` (caller-resolves), `[D-082]` (cascade-dedup pattern — milestone-scoped write dedup primary user).

## D-078: `is_milestone_gating` semantic activated in FR-64 (was vestigial)
**Status**: Active · **Date**: 2026-06-14


**Context**: SP UI engineer 2026-06-10 review surfaced that `is_milestone_gating` field on `template_schema.DeliveryItemBase` (renamed from `milestone_gating` per template_schema/MODULE.md Invariant 2026-06-12) carried no functional effect — all items gated milestone closure regardless of flag value per the original FR-64 enablement check ("all items in milestone are in `{SubmittedToCustomer, Closed}` AND at least one in `SubmittedToCustomer`"). User asked: "let us make this field is_milestone_gating for milestone closure."

**Decision**: FR-64 Close All Items enablement check changes to use `is_milestone_gating`: **enabled when all `is_milestone_gating=true` DeliveryItems in the milestone are in `{SubmittedToCustomer, Closed}` AND at least one `is_milestone_gating=true` item is in `SubmittedToCustomer`**. Items with `is_milestone_gating=false` do NOT block enablement and may remain in any non-blocking state when the action fires. On activation, closure scope still includes all `SubmittedToCustomer` items in the milestone regardless of gating flag (flag affects enablement only, not closure action scope). Field is **YAML-only / NOT TPM-editable** — ops/PM team identifies critical-for-closure items at template creation time.

**Why**:
- All-items-gate model is unnecessarily strict for milestones with optional waivers, advisory items, sustaining test reports, etc. that ops/PM team identifies as non-critical for milestone closure
- Field already existed (carried over from prior schema; renamed 2026-06-12); needed a functional purpose
- Matches user intent ("make this field for milestone closure")
- Closure scope kept unchanged (all SubmittedToCustomer items close regardless) — gating affects enablement, not action — keeps existing TPM Close All Items behavior aligned with NFR-5 PM-approval-gate semantic
- Rejected alternatives: **(α) keep vestigial** — wasted schema field; **(β) extend closure scope to only-gating items** — leaves non-gating items in SubmittedToCustomer stuck open after Close All; TPM would have to manually close each via FR-14; operational regression.

**Consequences**:
- FR-64 enablement check changes per Decision above; original wording preserved struck-through per requirements.md ID-stability convention
- Ops/PM template-authors must consciously identify critical-for-closure items at template creation (`is_milestone_gating: true` for items that MUST be closed before milestone close)
- `is_milestone_gating` stays YAML-only (NOT TPM-editable per user ratification — locked in FR-56 column model bucket (a))
- `customizations/sharepoint_config/MODULE.md` example schema + `HILDA_SP_Schema.xlsx` DeliveryItems tab already reflect `is_milestone_gating` rename (committed 2026-06-12)
- `template_schema/MODULE.md` Invariant (rename) already documents the new functional semantic via cross-reference to FR-64
- New canonical state for the field: was orphan; now load-bearing for FR-64 Close All Items button enablement

**Anchors**: FR-64 (rewritten 2026-06-12), template_schema/MODULE.md Invariant 2026-06-12 (`is_milestone_gating` rename + immutability), `[D-068]` (PM approval recording — unaffected; gating is closure-time concern, not approval-time).

Promoted from strand: dashboard-v1 on 2026-06-14

**Impl note 2026-06-19** — field name reverted from `is_milestone_gating` back to `milestone_gating` to align with SP UI engineer's `SP_lists_authoritative.xlsx` Workitems list row 9 column name (no `is_` prefix). Semantic decision per D-078 unchanged — `milestone_gating = true` items gate `MilestoneAllClosed` per FR-64; flag is YAML-only / NOT TPM-editable. Cascade applied to `docs/compact/requirements.md` (12 occurrences) + `docs/sp_ui_engineer/SP_UI_button_actions.md`. `template_schema/MODULE.md` Invariant + downstream code reflect this on next regen-map / development phase sweep. Prior `is_milestone_gating` references in this DECISIONS.md (historical D-077 + D-078 body) left as-is per append-only history discipline; this impl note is the canonical pointer to the current name.

---

## D-079: ReadyForSubmission added to FR-62 upload allowed states with revert-to-UnderPMReview semantic
**Status**: Active · **Date**: 2026-06-14


**Context**: FR-62 (Ph-2 dashboard-rendered upload form per `[D-074]`) originally allowed uploads only when `delivery_state ∈ {DocumentReceived, UnderPMReview, SubmittedToCustomer}` plus the `Open` state when `tracking_enabled=False` per FR-81 no-tracking-TG fallback. User 2026-06-12 surfaced real TPM use case: late doc upload needed after PM approval but before FR-63 Submit fires (supplementary test report; customer-induced revision discovered late; doc_count overflow due to ambiguous original spec; miscellaneous artifact TPM realizes is missing).

**Decision**: `ReadyForSubmission` added to FR-62 allowed states. State transition on upload from `ReadyForSubmission` **reverts to `UnderPMReview`** (matches SubmittedToCustomer revert pattern). HILDA clears `pm_approval_at` + `pm_approval_pm_id` per `[D-068]` impl note 2026-06-12 clearing discipline. TPM must re-approve before next FR-63 Submit fires.

**Why**:
- Real TPM use case: late additional docs needed before submission cycle (waiting until SubmittedToCustomer to upload then revert is operationally annoying — TPM should pre-empt)
- Revert pattern matches SubmittedToCustomer (already established): stale PM approval becomes invalid when item content changes; PM must re-approve fresh state
- Clearing pm_approval_at + pm_approval_pm_id maintains audit consistency per `[D-068]` impl note clearing discipline
- Maintains NFR-5 PM-approval gate (no submission without fresh approval)
- Rejected alternatives: **(α) don't allow upload in ReadyForSubmission** — forces TPM to wait until SubmittedToCustomer (then upload triggers revert); operationally clunky; **(β) allow upload without revert** — silently invalidates PM approval; defeats NFR-5 PM-approval-gate-before-customer-facing semantic.

**Consequences**:
- FR-62 enabled state set grows from 3 to 4 states (`DocumentReceived`, `UnderPMReview`, `ReadyForSubmission`, `SubmittedToCustomer`); plus Open state for no-tracking TG per FR-81
- ReadyForSubmission → UnderPMReview transition added to delivery_state machine
- HILDA must clear `pm_approval_at` + `pm_approval_pm_id` on the transition (matches `[D-068]` impl note clearing discipline for rewind paths per `[D-067]` + entry to UnderPMReview discipline)
- TPM must re-approve before next FR-63 Submit fires
- Tracker MODULE.md state-machine + transitions code must be updated to include this transition (Ph-2 dev — tracker module is currently in design only)
- FR-87 step (A) reassignment doesn't change semantic (already covered by FR-83); FR-62 upload is distinct from FR-87 reassignment

**Anchors**: FR-62 (Ph-2; rewritten 2026-06-12), `[D-068]` impl note 2026-06-12 (pm_approval clearing discipline on rewind paths), `[D-067]` (customer RFI rewind from SubmittedToCustomer — sets the precedent pattern for revert-on-content-change), NFR-5 (PM-approval gate before customer-facing action — preserved).

Promoted from strand: dashboard-v1 on 2026-06-14

---

## D-080: owner_email split into corp_usa_email + corp_email (per-item AND TG-level)
**Status**: Active · **Date**: 2026-06-14


**Context**: SP UI engineer 2026-06-10 review proposed splitting the single `owner_email` field on `template_schema.DeliveryItemBase` into two distinct email fields to handle corp's USA-vs-non-USA owner population: `owner_corp_usa_email` (SP Person/Group field, AD-resolved against corp-USA AD directory) + `owner_corp_email` (free text, for non-USA owners or AD-unresolved cases). Same split applies to `template_schema.TGGroupBase`: `tg_owner_email` → `tg_owner_corp_usa_email` + `tg_owner_corp_email`.

**Decision**: `template_schema.DeliveryItemBase.owner_email` is removed; replaced by `owner_corp_usa_email: str | None = None` + `owner_corp_email: str | None = None`. Same split on `TGGroupBase` (`tg_owner_email` removed; `tg_owner_corp_usa_email` + `tg_owner_corp_email` added). **HILDA preference rule** (per FR-2 + FR-9 2026-06-12): for outreach/identity, use `corp_usa_email` if set; else fall back to `corp_email`. **Owner-change event semantics**: a write to EITHER `owner_corp_usa_email` OR `owner_corp_email` constitutes an owner change event (fires `OwnerReassigned` rule per rule_engine MODULE.md; HILDA re-resolves canonical identity per preference rule + updates `owner_name` display via SP Person/Group AD lookup + writes CommunicationLog with `action_type=owner_reassigned`). A write to `owner_corp_id` alone is NOT an owner change event (corp_id is identifier metadata; not principal identity for outreach). Same semantics at TG-scope for tg_owner_* triple.

**Why**:
- Corp AD Person/Group field type auto-resolves only USA-corp emails (xxx@corp-usa.com); non-USA corp employees use different email format (xxx@corp.com) and don't resolve via the same SP AD picker
- Single owner_email field would force either AD-only constraint (excludes non-USA owners — operational dead-end) or free-text (loses AD validation for USA owners — typo risk, no auto-resolution of owner_name)
- Splitting provides both: validated AD-resolved identity for USA owners (via Person/Group SP field type) + flexibility for non-USA owners (free-text)
- Preference rule (corp_usa_email > corp_email) gives unambiguous runtime owner identity for HILDA outreach + PLM assignment
- Owner-change event semantics define when downstream rules (OwnerReassigned, FR-9 outreach re-fire, FR-71 ODF re-fire) trigger — change to either email = re-evaluate; change to corp_id alone = identifier metadata only
- Rejected alternatives: **(α) single owner_email Person/Group + free-text fallback marker** — confusing schema (which field is authoritative?); **(β) keep owner_email + add is_non_usa boolean flag** — duplicate fields with shared semantic; redundant; **(γ) separate SP list for non-USA owners** — partitioning overhead + complicates HILDA lookups + breaks model consistency.

**Consequences**:
- `template_schema/models.py` updates (Order (A) architecture-phase batch queued in STATUS Flag 2026-06-12):
  - `DeliveryItemBase`: remove `owner_email`; add `owner_corp_usa_email: str | None = None` + `owner_corp_email: str | None = None`
  - `TGGroupBase`: remove `tg_owner_email`; add `tg_owner_corp_usa_email: str | None = None` + `tg_owner_corp_email: str | None = None`
- `customizations/sharepoint_config/MODULE.md` example schema updates (delivery_items + denormalized TG columns)
- `customizations/sharepoint_config/customers/example.yaml` updates
- `HILDA_SP_Schema.xlsx` DeliveryItems + TGGroups tab updates (already partially reflected in SP UI engineer's revision shared via Google Sheets)
- `core/tests/test_template_schema.py` updates for new owner field set
- HILDA outreach code uses preference rule (corp_usa_email if set; else corp_email)
- `OwnerReassigned` rule fires on either email field change (not on corp_id alone)
- `owner_name` display auto-resolved by SP from corp_usa_email Person/Group field at SP-side; HILDA reads from DI row at runtime (denormalized read-only mirror)
- FR-2 (rewritten 2026-06-12) documents the owner identity model; FR-9 (rewritten 2026-06-12) documents the preference rule; FR-71 (rewritten 2026-06-12) uses the specific field names
- Anchors `[D-051]` impl note 2026-06-12 (TG denormalization pattern; tg_owner_* fields are denormalized read-only mirrors on DI rows)

**Anchors**: FR-2 (owner identity model 2026-06-12), FR-9 (outreach preference rule 2026-06-12), FR-71 (ODF specific field names 2026-06-12), `[D-051]` impl note 2026-06-12 (TG denormalization), `[D-065]` (SP UI engineer owns Person/Group field type + AD lookup mechanism for corp_usa_email).

Promoted from strand: dashboard-v1 on 2026-06-14

---

## D-081: Form factor flag set expands from 5 to 7 canonical bools (drr + ir_ffw_p1 added)
**Status**: Active · **Date**: 2026-06-14


**Context**: SP UI engineer 2026-06-10 review surfaced 2 additional form factor flag fields beyond the canonical 5 (`handset`, `tablet`, `wearable`, `mr`, `hmr_smr`): `drr` + `ir_ffw_p1` (Python-friendly rename from SP display name "ir/ffw/p1"). These are customer-specific device classifications increasingly common across customer deployments.

**Decision**: Add `drr: bool = False` + `ir_ffw_p1: bool = False` to `template_schema.DeliveryItemBase` as canonical fields. Rename `ir/ffw/p1` SP display → `ir_ffw_p1` Python identifier (underscores + lowercase required for Python attribute syntax + Pydantic compatibility). Total canonical form factor flags: **7** (was 5). Per FR-56 column model 2026-06-12, all 7 are `may_show` / not TPM-editable (YAML-loaded template-fixed flags).

**Why**:
- Customer-specific device classifications (`drr`, `ir_ffw_p1`) are now common enough across customer deployments to warrant canonical schema inclusion
- 7 flags is still small and manageable; no concrete burden to growing the set when new customer classifications emerge
- Customer-extensible registry alternative was considered but rejected — these are bool flags, not enum values; bool flags don't extend cleanly via a registry pattern (which maps strings → display labels)
- HILDA's Pydantic model needs awareness of these fields for type-safe runtime access by routing rules (rule_engine conditions reference form factor flags per FR-7)
- Rejected alternatives: **(α) keep flags in customer-extensible YAML config only** — HILDA's Pydantic DeliveryItemBase wouldn't know about them; loses type safety + IDE support + Pydantic validation; **(β) registry-based extensible flags** — over-engineered for bool flags; complicates rule_engine evaluator + storage layer for marginal gain; **(γ) consolidate flags into a single multi-value enum/list** — breaks back-compat with existing flag-by-name conventions in rule conditions.

**Consequences**:
- `template_schema/models.py` DeliveryItemBase gains 2 new bool fields with `False` default (Order (A) architecture-phase batch)
- Customer onboardings populate these from template YAML at tracker creation per FR-2
- Routing rules in YAML (`customizations/rules/<customer>/`) can reference `drr` / `ir_ffw_p1` in conditions per FR-7 + rule_engine MODULE.md
- `HILDA_SP_Schema.xlsx` DeliveryItems tab + `example.yaml` + `sharepoint_config/MODULE.md` example schema updated (mostly already documented in STATUS Flag 2026-06-12)
- `core/tests/test_template_schema.py` adds field-existence + default-value coverage for new fields
- Future form factor additions follow same canonical-addition pattern (not registry-extensible)
- FR-7 mentions form factor flags as a set; the 5→7 expansion is captured in FR-56 column model bucket (b) 2026-06-12 ("7 flags total")

**Anchors**: FR-7 (form factor scope; extensible-via-configuration mention), FR-56 column model 2026-06-12 (bucket b lists 7 flags), template_schema/MODULE.md (canonical schema location), `[D-046]` (canonical schema source — Pydantic models in template_schema).

Promoted from strand: dashboard-v1 on 2026-06-14

---

## D-082: `Milestone.target_date` is sole TPM-editable date; SP-side cascade with sp_alert_parser dedup discipline
**Status**: Active · **Date**: 2026-06-14


**Context**: 2026-06-14 SP UI engineer xlsx review (row 68) surfaced that TPM-editing per-DeliveryItem `expected_completion_date` independently is operationally regressive — for a milestone with N items (often 40+), TPM would need N edits to shift all items to a new target date. User locked: `Milestone.target_date` is the SOLE TPM-editable date; all items in a milestone share the same target_date. This requires a cascade mechanism + a dedup discipline because SP-alerts fire per-row.

**Decision**: `Milestone.target_date` is the sole TPM-editable date for any milestone. **Cascade flow**: (1) TPM edits `Milestone.target_date` in SP UI → (2) SP UI engineer's web part atomically writes the new value to the Milestone row AND propagates to each child DeliveryItem's `expected_completion_date` field in a multi-row SP-side write → (3) the milestone-level write fires one SP-alert; each per-DI write also fires its own SP-alert (SP-alert engine is per-row); → (4) HILDA's `email_service.sp_alert_parser` processes ONLY the Milestone `target_date` change alert and cascades the new value to its in-memory state for all DIs in the milestone; **per-DI `expected_completion_date` change alerts triggered by the same edit burst are deduplicated and ignored**. Per-item `expected_completion_date` editing is NOT exposed in TPM SP UI; FR-14 amended 2026-06-14 to drop per-item date override.

**Why**:
- **(a) Operational simplicity for TPM**: one edit shifts all items in a milestone. Matches the natural model "milestone target slips → all items slip together."
- **(b) Eliminates per-item date divergence**: prevents TPM from accidentally creating items with mismatched target_dates that escape FR-11 escalation in confusing ways.
- **(c) SP-side cascade keeps SP single-source-of-truth**: SP web part owns the per-DI write fan-out; HILDA learns the new state via the milestone alert without participating in the fan-out.
- **(d) Dedup discipline avoids HILDA over-processing**: a 40-item milestone's target_date edit fires 1 milestone alert + 40 DI alerts; without dedup, HILDA would process 41 logical changes for 1 user action.

**Rejected alternatives**:
- **(α) Per-item date editing retained (current FR-14 wording)**: rejected per user 2026-06-14 — operationally regressive; no use case.
- **(β) HILDA owns the cascade (SP UI engineer writes milestone only; HILDA reads alert + fans out per-DI writes via `[D-064]`)**: rejected — SP UI engineer's web part is already at the point of edit (atomic SP-side write is cheaper than HILDA round-trip); HILDA cascade introduces a window where Milestone.target_date and DI.expected_completion_date are temporarily out of sync.
- **(γ) No DI mirror of target_date; HILDA computes deadline math from Milestone.target_date at runtime**: viable but loses backward-compat with existing FR-11/FR-26/FR-55/FR-23 `polling_schedule` evaluation which reads `expected_completion_date` per item (deadline-tiered intervals); rewrite scope too large for marginal benefit.

**Consequences**:
- FR-11 amended 2026-06-14 to document cascade + dedup discipline.
- FR-14 amended 2026-06-14 to drop per-item `expected_completion_date` override (replaced by milestone-target_date-only edit path).
- `sp_alert_parser` gains dedup logic: when a Milestone target_date change alert is in the same alert batch as per-DI `expected_completion_date` change alerts for items in that milestone, the per-DI alerts are ignored. Alert-batch grouping mechanism: SP-alerts arrive via IMAP IDLE / short-poll; alerts with the same `Modified` timestamp ±N seconds and shared milestone scope are grouped per architecture-phase detail.
- SP UI engineer's web part implements the atomic SP-side multi-row write on Milestone.target_date edit; failure to propagate to all DIs leaves the milestone in a partially-cascaded state requiring TPM re-edit (acceptable Ph-1 failure mode).
- Per-DI `expected_completion_date` field on DeliveryItems SP list becomes HILDA-managed (read by sp_alert_parser cache; written by SP UI engineer's cascade). TPM SP UI MUST NOT expose this field as editable.
- DEF-N (Ph-3+): if cross-item date divergence is ever needed (e.g., one item slips while others stay), a per-item date override mechanism can be re-introduced via FR-14 — gated on actual operational need.

**Anchors**: FR-2 (target_date set at tracker creation), FR-11 (deadline escalation per `expected_completion_date`), FR-14 (TPM overrides — date dropped from list), `[D-047]` (SP-alert channel — dedup is sp_alert_parser responsibility), `[D-064]` (SP-side cascade write is SP UI engineer's, not HILDA's), `[D-068]` impl note 2026-06-12 (SP-side button-write discipline — same pattern generalized to multi-row cascade).

Promoted from strand: dashboard-v1 on 2026-06-14

**Impl note 2026-06-18 — `[D-082]` cascade-dedup effective obsoletion post `[D-083]` + `[D-085]`**: the original `[D-082]` cascade-dedup mechanism in `sp_alert_parser` was designed for the `Tasks_<customer_id>` flat-table architecture where milestone-level button clicks fired N SP-alerts (one per work-item row) that needed to be coalesced to a single milestone-scoped event. Post `[D-083]` 2-list architecture: milestone-level button writes target the single Milestones SP list row (one SP-alert per click; no N-row burst). Post `[D-085]`: `expected_completion_date` removed from Deliverables; target_date edits no longer cascade-write to N rows. `[D-082]` cascade-dedup now has **zero Ph-1 use cases** as a result. Implementation should retain the `sp_alert_parser` dedup hook as a defensive guard against accidental N-row bursts (test coverage preserved) but the operational expectation is single-row writes per FR-84 Source A (Deliverables) + Source B (Milestones) routing. If a future Ph-2 normalization re-introduces N-row writes, `[D-082]` becomes relevant again — flag at that architecture decision point.

---

## D-083: SP architecture — 2-list per-customer + global Milestone list + existing Project lookup; supersedes `[D-077]` Ph-1 flat-table
**Status**: Active · **Date**: 2026-06-17

**Context**: 2026-06-17 SP UI engineer surfaced a revised SP design (Google Sheets at `1gqmD9QVLQjuJ08Wc7zc7KI3jKQhr9MjH`) that separates milestone-level concerns from per-DeliveryItem rows. Prior `[D-077]` flat-table model denormalized milestone-level fields (`status`, `target_date`, milestone-level button timestamps, `download_package_*`, etc.) across every DeliveryItem row in `Tasks_<customer_id>`. Operationally this produced N-row write amplification on milestone-level button clicks + required `[D-082]` cascade-dedup logic in HILDA's `sp_alert_parser`. SP UI engineer's revised design eliminates this denormalization by introducing a separate Milestone SP list.

**Decision**: HILDA's runtime SP coupling consists of:
- **Per-customer Work Items list**: `Deliverables_<customer_id>` (one list per customer; ~60 columns; one row per DeliveryItem). Replaces `Tasks_<customer_id>` naming.
- **Global Milestone list**: `Milestones` (one list shared across all customers; 15 columns; one row per milestone; scoped by `carrier` column).
- **Existing global Project list**: `project_id → TPM (assigned_pm_id)` lookup; reused as-is.

**HILDA service-layer config** (NOT in SP):
- `template.yaml` (per-customer): structural manifest + `customer_jira_url` (moved from SP).

**Lookup chain at runtime**:
1. `template.yaml` → `(milestone_id, device_id)` maps to `(milestone_name, project_model)`.
2. `Deliverables_<customer_id>` → query rows where `(milestone_name, project_model)` → returns `project_id` (denormalized column).
3. `Projects` → query `project_id` → returns `TPM` (assigned_pm_id).

**Why**:
- **(a) Eliminates write amplification**: milestone-level button writes target a single Milestone row instead of N DeliveryItem rows; obsoletes `[D-082]` cascade-dedup for these cases.
- **(b) Clean separation of concerns**: milestone-level state lives at milestone level; per-item state at item level. Matches mental model.
- **(c) Simpler SP-alert routing**: per-list alert dispatching is cleaner than per-row burst deduplication.
- **(d) Reuses existing Project list**: PM lookup chain is established; no new lookup infrastructure needed.

**Rejected alternatives**:
- **(α) Retain `[D-077]` flat-table**: rejected — write amplification + `[D-082]` complexity not worth the join-avoidance gain.
- **(β) Per-customer Milestone list (`Milestones_<customer_id>`)**: rejected — milestone-level operations are cross-customer-portable; global list with `carrier` column scoping simpler.

**Consequences**:
- `[D-077]` flat-table architecture superseded.
- `[D-082]` cascade-dedup scope significantly reduced; possibly obsoleted entirely after `expected_completion_date` removal per `[D-085]`.
- FR-2, FR-5, FR-6, FR-11, FR-25(b), FR-40, FR-56, FR-63, FR-64, FR-73, FR-84 rewritten to reflect 2-list architecture.
- `Tasks_<customer_id>` → `Deliverables_<customer_id>` global sweep across all FR references + SP_UI_button_actions.md + auto-memory.
- `SP_lists_authoritative.xlsx` replaced (architecture-phase task).
- Auto-memory `project_sp_architecture_ph1.md` rewritten.

**Anchors**: FR-2, FR-40, FR-56, FR-84, `[D-064]`, `[D-065]`, `[D-068]`, `[D-077]` (superseded), `[D-082]` (scope reduced), `[D-085]`, `[D-086]`, `[D-087]`.

---

## D-084: Form factor flag set — `handset / tablet / wearable / ir / osmr / rmr / hmr_smr` (7 flags); supersedes `[D-081]`
**Status**: Active · **Date**: 2026-06-17

**Context**: 2026-06-17 SP UI engineer's revised SP schema (per `[D-083]` Google Sheets) lists the canonical form factor flags as `handset / tablet / wearable / ir / osmr / rmr / hmr_smr`. Prior `[D-081]` lock had `handset / tablet / wearable / drr / ir_ffw_p1 / mr / hmr_smr`. The 3 differing flag names (`drr → ir`, `ir_ffw_p1 → osmr`, `mr → rmr`) reflect SP UI engineer's customer-domain-correct naming.

**Decision**: Canonical 7 form factor flags (Boolean each) on DeliveryItem rows:
`handset`, `tablet`, `wearable`, `ir`, `osmr`, `rmr`, `hmr_smr`.

**Why**:
- **(a) SP UI engineer is the customer-domain authority for form factor terminology**.
- **(b) Names align with carrier-side device-classification vocabulary**.

**Rejected alternatives**:
- **(α) Retain `[D-081]` names**: rejected — names were placeholder pending SP UI engineer ratification; new names supersede.

**Consequences**:
- `[D-081]` superseded.
- `template_schema/models.py` `DeliveryItemBase` Pydantic schema updates: `drr → ir`, `ir_ffw_p1 → osmr`, `mr → rmr` (cascade flagged in STATUS.md for architecture-phase code update).
- FR-40 template.yaml schema updated.
- `customizations/sharepoint_config/customers/<customer_id>.yaml` column mappings updated.
- Auto-memory `project_sp_architecture_ph1.md` updated.

**Anchors**: FR-40, `[D-081]` (superseded), `[D-083]`.

---

## D-085: `Milestone.target_date` is the sole authoritative deadline; per-item `expected_completion_date` removed from Ph-1 schema; obsoletes `[D-082]` cascade-dedup for date propagation
**Status**: Active · **Date**: 2026-06-17

**Context**: 2026-06-17 architect reviewed the revised SP schema (per `[D-083]`) and questioned whether per-item `expected_completion_date` is unnecessary duplication of `Milestone.target_date`. Per Ph-1 + Ph-2 lock: all items in a milestone share the same target_date (no per-item override exposed per FR-11 / FR-14 lock). The per-item field is a pure denormalized copy. Removing it eliminates both the SP-side cascade write (40-row write on TPM date edit) AND the corresponding `[D-082]` HILDA-side dedup logic.

**Decision**: `Milestone.target_date` is the sole authoritative deadline for all DeliveryItems in that milestone in Ph-1. The `expected_completion_date` column is removed from the `Deliverables_<customer_id>` schema. HILDA reads `Milestone.target_date` directly from the Milestone SP list for deadline-proximity rule evaluation (FR-11 DeadlineProximity, FR-23/FR-25/FR-26/FR-55 polling_schedule). SP UI displays milestone target_date from the Milestone list at row render time (join cost negligible — Milestone list is already read for status banner display).

**Why**:
- **(a) Eliminates pure duplication**: no per-item variation in Ph-1; the column was always a denormalized copy.
- **(b) Eliminates N-row cascade writes**: TPM target_date edit no longer triggers SP-side fan-out to N DeliveryItem rows.
- **(c) Obsoletes `[D-082]` cascade-dedup logic** in `sp_alert_parser` for date propagation: only one Milestone-row alert fires; no per-DI alerts to dedup.
- **(d) Single source of truth**: removes risk of Milestone.target_date and DI.expected_completion_date drifting out of sync due to partial cascade failure.

**Rejected alternatives**:
- **(α) Retain `expected_completion_date` per `[D-082]`**: rejected — duplication has no operational benefit in Ph-1; cascade complexity not justified.
- **(γ) SP calculated column (Milestone.target_date lookup)**: rejected — cross-list calculated columns in SharePoint have constraints; explicit join at render time simpler.

**Consequences**:
- `[D-082]` cascade-dedup for date propagation obsoleted; `[D-082]` may have zero Ph-1 use cases after `[D-083]` milestone-button single-row writes (verify during architecture phase).
- FR-11 rewritten: DeadlineProximity rule reads `Milestone.target_date` directly; no per-item denorm.
- FR-14 simplified: target_date edit goes via milestone view only; no SP cascade to N rows.
- FR-56 (a) `expected_completion_date` column removed from Mandatory display list.
- Ph-2 per-item override (if needed): add `expected_completion_date_override` column with NULL semantic (= use milestone.target_date); deferred unless operational need surfaces.

**Anchors**: FR-11, FR-14, FR-56, `[D-082]` (cascade-dedup obsoleted for this case), `[D-083]`.

**Impl note 2026-06-18 — `daily_status_tick` AutomationRule is distinct from `polling_schedule` (FR-6 + FR-23 cross-FR cadence clarification)**: `Milestone.target_date < today` flips at midnight without any DI state change, so a daily evaluation trigger is required to catch the `Delayed` status transition per FR-6 precedence ladder rung (2). This trigger is implemented as a **separate `daily_status_tick` AutomationRule** (default fire time: 00:05 local corp time) — NOT a tier of `polling_schedule`. **Why separate**: `polling_schedule` (per FR-23 cross-FR cadence consistency lock — shared canonical definition across FR-23 Email Tier 3 / FR-25 CustomerJIRA + CorporatePLM / FR-26 PLM / FR-55 NSD) controls polling-channel interval rules — `{days_before_deadline, interval_minutes}` breakpoints determining how often HILDA polls each channel. The daily tick is a different concern entirely — a once-per-day rule-engine fire that triggers `Milestone.status` recompute for all active milestones (those with `milestone_collection_started_at IS NOT NULL AND status ≠ 'Completed'` on the Milestones SP list per FR-2 bootstrap). Conflating the two would muddy `polling_schedule`'s domain (polling cadence) with status recompute scheduling. Implementation lives in `rule_engine` per `[D-066]` rule-evaluation discipline; consumed by `workflow_engine` per `[D-022]`.

---

## D-086: All SP email / corp_id / owner_name columns are STR free-form text; no corp AD resolution / Person-Group SP fields / auto-derivation
**Status**: Active · **Date**: 2026-06-17

**Context**: 2026-06-17 architect clarified during SP architecture lock (per `[D-083]`) that all SP-side email columns (item owner emails, TG owner emails, `email_cc_list`, `tg_email_group_alias`), `owner_corp_id`, and `owner_name` fields are SP **STR free-form text** — NOT SP Person/Group fields. TPM types whatever they want; HILDA does NOT validate against corp AD; HILDA does NOT auto-derive `owner_corp_id` from `owner_corp_usa_email`'s local-part. Prior FR-56 (a) wording locked `owner_corp_usa_email` as SP Person/Group with AD resolution + `owner_corp_id` as auto-derived; this is now stale.

**Decision**: All SP email / identity columns are STR free-form text. Specifically:
- `owner_name`, `owner_corp_usa_email`, `owner_corp_email`, `owner_corp_id`: free-form text on Deliverables_<customer_id> rows.
- `tg_owner_name`, `tg_owner_corp_usa_email`, `tg_owner_corp_id`, `tg_email_group_alias`: free-form text (5 free-form text fields total at TG level; no `tg_owner_corp_email` field — never existed in prior version per architect 2026-06-17).
- `email_cc_list`: free-form text (comma-separated emails).

HILDA's owner-email preference rule SURVIVES (use `owner_corp_usa_email` if set; else `owner_corp_email`) per FR-9 + `[D-080]`; only the AD-validation + auto-derivation aspects are removed.

**Why**:
- **(a) Real-world support for non-AD-resolvable identities** (non-USA owners, external collaborators, role mailboxes, TG aliases).
- **(b) Simplifies SP UI engineer's schema** (STR columns instead of Person/Group with AD lookups).
- **(c) HILDA stays out of corp directory dependency** (no Graph API / AD lookup needed at SP-write time).
- **(d) TPM autonomy** — TPM types what works; HILDA trusts.

**Rejected alternatives**:
- **(α) Retain Person/Group SP field for `owner_corp_usa_email`**: rejected — fails for non-USA owners + role mailboxes + TG aliases.
- **(β) HILDA-side AD validation at SP-write time**: rejected — adds corp directory dependency for marginal validation benefit; TPM mistakes surface naturally as bounce errors per FR-23 `EML-W001`/`EML-E002`.

**Consequences**:
- FR-2 owner identity section rewritten: remove AD resolution + auto-derivation language.
- FR-56 (a) Mandatory display owner block rewritten: all STR free-form (no Person/Group distinction).
- FR-9 owner-email preference rule preserved; AD-validation language removed.
- FR-88 owner identity model: 3-field preserved; AD-resolution language removed.
- HILDA's owner-name display in FR-56 / FR-59 / FR-60 reads `owner_name` column directly (no AD lookup at render time).
- TG owner: 5 free-form text fields ratified (`tg_owner_corp_email` does NOT exist; only `corp_usa_email + corp_id + name + email_group_alias + tg_path_id`).

**Anchors**: FR-2, FR-9, FR-56, FR-88, `[D-080]` (preference rule preserved; AD aspects removed), `[D-083]`.

---

## D-087: `customer_delivery_credential_id` field removed from SP Deliverables_<customer_id> schema; credential lookup is FR-19/FR-51 per-(PM, carrier) sops files
**Status**: Active · **Date**: 2026-06-17

**Context**: 2026-06-17 architect reviewed `customer_delivery_credential_id` column in SP UI engineer's revised schema (per `[D-083]`). The field appears to be a legacy from older credential-storage models (pre-`[D-038]` v3 + pre-FR-19/FR-51 4-pattern lock). Per current FR-19 + FR-51 lock 2026-06-17: customer adapter credentials live at `customizations/credentials/<pm_id>/<carrier_slug>.env.sops` (per-PM-per-carrier sops files; pattern (b)). HILDA's `credential_service` resolves at adapter instantiation time via `get_credential(PerPerson(pm_id), system_type="google_drive")`. A per-item credential-ID column is redundant and inconsistent with this lookup model.

**Decision**: Remove `customer_delivery_credential_id` column from `Deliverables_<customer_id>` schema. HILDA looks up customer-delivery credentials via the (assigned_pm_id, carrier_slug) tuple at adapter instantiation time per FR-19/FR-51 pattern (b).

**Why**:
- **(a) Redundant** with FR-19/FR-51 per-(PM, carrier) sops file naming.
- **(b) Inconsistent** with the 4-pattern identity-model lock (`[D-038]` v3 + FR-51).
- **(c) Schema noise**: never displayed; no operational use.
- **(d) Single source of truth**: credentials live in sops env files; SP-side credential references would create dual-source-of-truth risk.

**Rejected alternatives**:
- **(α) Retain column for backward compatibility**: rejected — no operational consumers; schema bloat.
- **(β) Repurpose as per-customer credential ref**: rejected — per-customer credentials would conflict with per-(PM, carrier) FR-19/FR-51 lock; no new use case.

**Consequences**:
- FR-19 / FR-51 / FR-40 schema updated.
- SP UI engineer's xlsx updated (column removed).
- No HILDA-side code changes (no consumers of this field).

**Anchors**: FR-19, FR-51, `[D-038]` v3, `[D-083]`.

---

---

## D-088: `Projects.TPM` is a SharePoint Person/Group column; HILDA extracts `(assigned_pm_id, pm_display_name, pm_email)` 3-tuple from sub-fields

**Date**: 2026-06-19
**Status**: Ratified

**Context**: Prior text (FR-2 PM identity lookup chain, FR-8 step 2 PM-attribution, FR-9 outreach attribution, FR-19 PM credential storage, FR-25 (b) JIRA self-outreach, FR-65 Send Reminder) treated `TPM` / `assigned_pm_id` as a single scalar string. SP UI engineer's `SP_lists_authoritative.xlsx` clarified 2026-06-19 (Google Sheets refresh + architect screenshot of SP User Information dialog) that the Projects SP list's `TPM` column is a SharePoint Person/Group column (multi-field user record), not a STR. HILDA's lookup-chain wording needs to specify which sub-fields drive which downstream uses to prevent ambiguity at architecture / development phase.

**Decision**: At Projects-SP-list cache time, HILDA resolves the `TPM` Person/Group value to a 3-tuple `(assigned_pm_id, pm_display_name, pm_email)` extracted from these sub-fields:

| Tuple member | Sub-field | Example | Downstream use |
|---|---|---|---|
| `assigned_pm_id` | `User name` (preferred); fallback to email-local-part of `Work email` when `User name` is null | `y.vasilyev` | FR-19 credential path slug (`customizations/credentials/<assigned_pm_id>/<carrier_slug>.env.sops`); CommunicationLog attribution; rule-engine PM-routing keys |
| `pm_display_name` | `Name` (single field; NOT `First name + Last name`) | `Yury Vasilyev` | FR-9 outreach signature; FR-65 Send Reminder display; dashboard rendering |
| `pm_email` | `Work email` | `y.vasilyev@partner.samsung.com` | FR-25 (b) CustomerJIRA self-outreach recipient; outreach signature reference (HILDA From stays team mailbox per FR-23) |

All other Person/Group sub-fields (`Account` SP-internal claim, `SIP Address`, `Picture`, `Department`, `Title`, `First name`, `Last name`, `Mobile phone`, `Work phone`, `Web site`, `Ask Me About`, `Office`, `About me`, `Picture Timestamp`, `Picture Placeholder State`, `Picture Exchange Sync State`, `OtherMail`) are ignored by HILDA.

**Why**:
- (a) Single-scalar treatment was an unwitting simplification of the actual SP schema; codifying the 3-tuple now prevents later drift between HILDA's PM-resolution code and SP reality.
- (b) `User name` as `assigned_pm_id` preferred over email-local-part: PMs may be partner/contractor identities (e.g., `@partner.samsung.com`) whose email-local-part can be ambiguous; `User name` is the SP-supplied corp directory identifier and is the stable key. Email-local-part fallback retained for robustness when `User name` is null (rare).
- (c) `Name` single field used as `pm_display_name`: avoids first/last-name concatenation logic and locale-specific name-order ambiguity. Matches what SP renders by default.
- (d) `Work email` as `pm_email`: single canonical email field for the PM identity; partner-domain emails are supported.

**Consequences**:
- (a) FR-2 PM identity lookup chain rewritten to specify 3-tuple extraction.
- (b) FR-8 step 2 PM-attribution to CommunicationLog references `assigned_pm_id` per `[D-088]`.
- (c) FR-9 outreach attribution references `pm_display_name` + `pm_email` (not bare `assigned_pm_id`).
- (d) FR-19 credential path slug uses `<assigned_pm_id>` per `[D-088]`.
- (e) FR-25 (b) JIRA self-outreach recipient uses `pm_email`.
- (f) FR-65 Send Reminder display references `pm_display_name`.
- (g) HILDA's Projects-list cache structure carries the 3-tuple per `project_id` (architecture phase to confirm cache shape — likely `dict[project_id, tuple[project_model, assigned_pm_id, pm_display_name, pm_email]]`).
- (h) Sheet row 85 `TPM` Data Type column should be updated from `STR` to `Person/Group` (architect-side correction; tracked as STATUS Flag).

**Anchors**: FR-2, FR-8 step 2, FR-9, FR-19, FR-25 (b), FR-65, `[D-083]` (Projects list reuse), `[D-086]` (owner identity free-form text — TPM is the only SP-side identity column that is intentionally NOT free-form because PMs are corp-AD-bound while owners are free-form).

**Related**: STATUS Flag closure — "Project list column alignment with SP UI engineer" (closed via this decision). STATUS Flag open — "Sheet row 85 TPM Data Type STR→Person/Group" (architect side).

---

## D-089: FR-56 (f) Refresh + FR-73 Download Package deferred to Ph-2

**Date**: 2026-06-19
**Status**: Ratified

**Context**: During the FR-56 deep-scan + STATUS Flag review session 2026-06-19, the architect observed that the milestone-level Refresh button (FR-56 (f)) and Download Package button (FR-73) introduce significant Ph-1 complexity: 5 milestone-level HILDA-write fields with self-loop feedback potential (`refresh_requested_at` + 4 `download_package_*` columns), a rate-limited soft-poll Celery task chain, and on-demand zip-assembly with `<scoped_token>` URL generation. The architect proposed dropping both buttons from Ph-1 SP UI scope to minimize the SP UI engineer's Ph-1 deliverable + reduce HILDA-side self-loop suppression surface area.

**Decision**: Refresh button (FR-56 (f); writes `refresh_requested_at`) AND Download Package button (FR-73; writes `download_package_request_timestamp` + 3 HILDA-writeback fields `download_package_url` / `_status` / `_generated_at`) are **deferred to Ph-2**. SP UI engineer's Ph-1 milestone-level button set is reduced to 4: Start Collection / Submit to Carrier / Close All Items / target_date inline edit.

**Why**:
- (a) TPM on-demand poll trigger (Refresh) is cosmetic — FR-23/FR-25/FR-26/FR-55 cadence-driven polling is sufficient for Ph-1 operational needs.
- (b) Download Package zip is for TPM offline use / audit — NOT used by Submit to Carrier (which dispatches individual files via FR-19 adapter per `[D-054]`); not operationally critical for Ph-1.
- (c) Removes 5 HILDA-write fields from Ph-1 Milestones-row scope → reduces self-loop suppression surface area + simplifies Ph-1 dispatch table to 4 fields.
- (d) Ph-1 wants minimum-viable SP UI engineer Ph-1 deliverable; Refresh + Download Package can ship Ph-2 without operational regression.

**Consequences**:
- (a) `docs/sp_ui_engineer/SP_UI_button_actions.md` sections for Refresh + Download Package marked `[Ph-2]` (2026-06-19 changelog entry).
- (b) `SP_lists_authoritative.xlsx` column H + field summary marks `refresh_requested_at` + 4 `download_package_*` fields as `[Ph-2]`.
- (c) FR-56 (f) entire section marked `[Ph-2]` deferred.
- (d) FR-84 Source B dispatch table shrinks from 6 fields to 4 (Start Collection / Submit to Carrier / Close All Items / target_date).
- (e) If TPM needs on-demand zip download in Ph-1, falls back to ops-mediated extraction from NSD `internal/` tree (no HILDA-side UI path).

**Anchors**: FR-56 (e/f), FR-73, FR-84 Source B dispatch table, `SP_UI_button_actions.md` changelog 2026-06-19, `SP_lists_authoritative.xlsx` column H `[Ph-2]` markers.

---

## D-090: FR-66 corp messenger inbound locked OUT; replacement mechanism deferred to Ph-2 architecture

**Date**: 2026-06-19
**Status**: Ratified

**Context**: `[D-090-preceded by FR-50 outbound-only messenger lock]` ratified 2026-06-17 established that HILDA NEVER processes inbound from corp messenger — messenger is outbound-only FR-11 escalation channel. FR-66 Ph-2 multi-revision version-selection workflow was originally specified as "owner selects the final revision via corp messenger" — direct conflict with FR-50. During FR-66 deep-scan 2026-06-19, the architect confirmed: lock OUT messenger inbound; defer the replacement mechanism choice to Ph-2 architecture phase.

**Decision**: FR-66 `TriggerVersionSelection` Ph-2 mechanism **MUST NOT use corp messenger inbound**. Replacement mechanism is deferred to Ph-2 architecture phase. Candidate channels:
- (a) Email reply with structured selection block parsed per FR-12 (analogous to BATCH-id discipline)
- (b) SP UI form (requires Ph-2 owner SP write permissions — separate provisioning concern)
- (c) HILDA-rendered tab per `[D-074]` pattern with token-scoped owner access

Architect picks (a), (b), (c), or alternative at Ph-2 architecture review.

**Why**:
- (a) FR-50 outbound-only lock is load-bearing per FR-9, FR-10, FR-11, FR-12, FR-65 cascade — an exception for FR-66 would unwind across multiple FRs.
- (b) Multiple Ph-2 inbound mechanisms exist that don't violate FR-50 (email already proven via FR-12; SP UI form / HILDA-rendered tab parallel existing Ph-2 surfaces).
- (c) Decision is reversible at Ph-2 architecture review — locking the prohibition + deferring the mechanism choice avoids premature commitment.

**Consequences**:
- (a) FR-7 / FR-9 / FR-12 / FR-25 / FR-28 / FR-29 / FR-56 / FR-13 cross-references swept to align with the lock 2026-06-19.
- (b) FR-66 workflow body preserved (revision listing, `is_final` atomic write, timeout handling) — only the inbound mechanism is deferred.
- (c) STATUS Flag opened: "FR-66 mechanism choice — Ph-2 architecture phase".
- (d) FR-29 `TriggerVersionSelection` action wording updated to reference the deferred mechanism.

**Anchors**: FR-50 outbound-only lock, FR-66 multi-rev version selection, FR-7 Ph-2 multi-rev fork, FR-9 Ph-2 outreach mechanism, FR-28 `TriggerVersionSelection` action, FR-56 (g) Ph-2 deferred per-item actions.

---

## D-091: FR-40 template.yaml YAML key reshape — `<milestone_id>` → `<milestone_name>`

**Date**: 2026-06-19
**Status**: Ratified

**Context**: During the FR-40 deep-scan 2026-06-19, multiple findings surfaced about the template.yaml schema's milestone identifier:
- (a) The YAML key `<milestone_id>` (e.g., `LE-2`) was claimed in the schema to map to SP Milestones row `Id` (SP auto-Counter INTEGER PK) — incorrect; the value actually lands in SP `Title` (STR) on Milestones list + SP `milestone_name` (STR) on Deliverables list per `[D-065]` mapping.
- (b) A redundant child field `milestone_name:` inside the `<milestone_id>:` YAML block — duplicates the key.
- (c) Inconsistency with the `<device_id>:` convention (where the YAML key value IS the template canonical name; SP-internal column name `project_model` is different per `[D-065]`).

Architect direction: drop both the `<milestone_id>` YAML key AND the `milestone_name:` field; replace with `<milestone_name>` YAML key as the canonical milestone identifier (aligned with `<device_id>` pattern).

**Decision**: Template.yaml milestone identifier is the **YAML key `<milestone_name>`** (e.g., `LE-2`). The value lands in:
- SP Milestones `Title` column (STR) per `[D-065]` mapping
- SP Deliverables `milestone_name` column (STR) per `[D-065]` mapping

SP `Id` (Milestones auto-Counter INTEGER PK) + Deliverables `milestone_id` (INTEGER FK to Milestones.Id) are SP-managed integers — NOT sourced from template.yaml. The redundant `milestone_name:` child field is removed from the YAML schema.

**Why**:
- (a) Aligns with the `<device_id>` template.yaml convention — YAML key carries the canonical identifier value; SP-internal column may have a different name per `[D-065]` mapping.
- (b) Eliminates the redundant `milestone_name:` field that duplicated the YAML key.
- (c) Correctly anchors the canonical → SP-internal mapping: template.yaml `<milestone_name>` → SP `Title` (Milestones) + SP `milestone_name` (Deliverables); SP `milestone_id` is INTEGER FK and NOT a HILDA-internal logical tuple key.
- (d) Forces all HILDA-internal logical tuples to use STR `milestone_name` consistently — fixes cascade-drift across FR-8 / FR-12 / FR-25 / FR-26 / FR-28 / FR-84 / FR-13 (5+ FRs swept this session).

**Consequences**:
- (a) Template.yaml schema reshape — drops `<milestone_id>:` YAML key + `milestone_name:` field.
- (b) Cross-FR sweep: HILDA-internal tuples `(device_id, milestone_id, owner_corp_id)` → `(device_id, milestone_name, owner_corp_id)` across FR-8 / FR-12 / FR-25 / FR-26 / FR-28 / FR-84 / FR-13.
- (c) `[D-065]` mapping reaffirmed as authoritative for canonical → SP-internal name translation.
- (d) FR-2 / FR-6 `$filter` examples updated: stale `milestone_id eq 'LE-2'` → `milestone_name eq 'LE-2'` (Deliverables) / `Title eq 'LE-2'` (Milestones).
- (e) `template_schema/MODULE.md` Pydantic schema + downstream code (`core/src/template_schema/models.py`) require dev-phase cascade to drop `milestone_id` field + rename milestone block key.

**Anchors**: FR-40 template.yaml schema, FR-2 $filter examples, FR-6 cross-list join discipline, FR-25 PLM tuple keys, FR-12 BATCH-id mapping, FR-26 polling target tuples, FR-84 routing key, `[D-065]` canonical → SP-internal mapping.

---

## D-092: CustomerJIRA Ph-1 / Ph-2 boundary — Ph-1 polling internal-only; Ph-2 adds SP write-back

**Date**: 2026-06-19
**Status**: Ratified

**Context**: During the FR-25 (b) + FR-9 + FR-12 cross-FR cascade 2026-06-19, the SP write-back semantics for CustomerJIRA polling results were ambiguous. Two SP columns (`jira_open_ticket_count` INTEGER + `jira_ticket_summary_json` STR) were added to xlsx Workitems list this session for SP UI engineer's per-row ticket-count badge display; the question was which phase HILDA writes them in.

**Decision**: 
- **Ph-1**: HILDA polls customer JIRA per cadence per FR-25 (b); polling results stay in **HILDA Postgres + CommunicationLog only** — NO SP write-back. PM checks JIRA portal directly using `<customer_jira_url>` from outreach body.
- **Ph-2**: HILDA additionally writes `jira_open_ticket_count` + `jira_ticket_summary_json` columns to Deliverables row via `[D-064]` REST writeback for SP UI engineer's per-row ticket-count badge + tooltip display.
- **Both phases**: End-to-end state machine workflow for JIRA-only items MUST cycle via close-intent reply email path (`Open → OutreachSent → UnderPMReview → ReadyForSubmission → SubmittedToCustomer → Closed`). The SP columns are PURELY informational/UI display for Ph-2 TPM context ("why the work item moved to Closed state") — NOT a gating condition for state advancement.

**Why**:
- (a) Minimizes Ph-1 SP-column scope + self-loop suppression burden (2 fewer HILDA-write Deliverables columns in Ph-1).
- (b) State advancement is identical in both phases — the SP write-back is purely UI display, not state-machine relevant.
- (c) Allows Ph-1 to ship without requiring SP UI engineer ticket-badge rendering (defer Ph-2 work).
- (d) PM has access to JIRA portal directly in Ph-1 via `<customer_jira_url>` in outreach body — no operational regression.

**Consequences**:
- (a) FR-25 (b), FR-9 CustomerJIRA-only block, FR-12 sender match all carry explicit Ph-1/Ph-2 split language.
- (b) `jira_open_ticket_count` + `jira_ticket_summary_json` columns marked `[Ph-2]` in `SP_lists_authoritative.xlsx` Workitems list.
- (c) FR-25 (b) close-intent path exclusivity lock preserved (state machine cycles via email reply per FR-12 in both phases).
- (d) STATUS Flag closed: SP_UI_button_actions.md sync for CustomerJIRA ticket-count display — defer Ph-2 architecture.
- (e) Ph-2 SP write-back fires SP-alerts back to HILDA per FR-84 Source A — suppressed via FR-84 self-loop mechanism (HILDA SP service account actor identity match).

**Anchors**: FR-25 (b), FR-9 CustomerJIRA-only block, FR-12 sender match + path resolution, FR-84 self-loop suppression, `SP_lists_authoritative.xlsx` Workitems list Ph-2 columns.

---

## D-093: `owner_corp_email` (not `owner_corp_usa_email`) matches `TPM.Work_email` for FR-25 (b) CustomerJIRA-only role-collapse template constraint

**Date**: 2026-06-19
**Status**: Ratified

**Context**: Original FR-25 (b) text (predating this session; preserved through the 2026-06-17 rewrite) stated: "when item_type = Confirmation AND tracking_modality = [CustomerJIRA] only, the `owner_corp_usa_email` field MUST match the milestone's assigned_pm_id resolved email at setup_milestone time — mismatch raises `TSC-W006`". During the FR-25 deep-scan 2026-06-19, the architect corrected this constraint: corp SP TPM Person/Group field carries a corp email (e.g., `@samsung.com`, `@partner.samsung.com`), NOT a USA-domain email. So `owner_corp_email` (not `_usa_`) is the correct field to match `TPM.Work_email`.

**Decision**: For CustomerJIRA-only Confirmation items, the role-collapse template constraint TSC-W006 requires:
- **`owner_corp_email` MUST match `TPM.Work_email`** (= `pm_email` per `[D-088]` 3-tuple) at `setup_milestone` time
- `owner_corp_usa_email` is optional (may be empty for non-USA-based PMs); if set, must also resolve to the same PM identity

Preference rule per FR-9 + `[D-080]` + FR-88 unchanged (`owner_corp_usa_email` preferred when set; `owner_corp_email` fallback) — applies to outreach attribution. The CustomerJIRA-only constraint specifies which owner email field MUST equal PM email at template authoring time (distinct from runtime addressing).

**Why**:
- (a) Corp SP TPM Person/Group field carries corp domain emails (not USA-domain) — the previous `owner_corp_usa_email` constraint was structurally wrong.
- (b) `owner_corp_email` is the universal corp-email field (any domain, any region); `owner_corp_usa_email` is the USA-specific override.
- (c) PM identity per `[D-088]` 3-tuple = `TPM.Work_email` is the canonical PM email source; matching `owner_corp_email` to it preserves the role-collapse intent (same person owns the JIRA-only item and approves it).
- (d) Preserves the FR-9 + `[D-080]` outreach preference rule (`owner_corp_usa_email` preferred when set) — does NOT affect outreach addressing behavior; only the role-collapse template authoring constraint changes.

**Consequences**:
- (a) FR-25 (b) template constraint TSC-W006 wording updated.
- (b) `template_schema/MODULE.md` validator semantics updated: when `item_type = Confirmation` AND `tracking_modality = [CustomerJIRA]` only, the `owner_corp_email` field MUST match `pm_email` per `[D-088]` at setup_milestone — mismatch raises TSC-W006.
- (c) FR-7 + FR-9 + FR-12 cross-references to CustomerJIRA role-collapse use `pm_email = TPM.Work_email` per `[D-088]`; they don't specify which `owner_corp_*_email` matches at template authoring time, so no cross-FR change needed beyond FR-25 (b).
- (d) Cross-checked: only FR-25 (b) had the explicit "owner_corp_usa_email" claim — no other FR needs updating.
- (e) `template_schema/MODULE.md` Pydantic validator + downstream code (`core/src/template_schema/models.py` TSC-W006 raise) require dev-phase cascade to update the matched field name.

**Anchors**: FR-25 (b) TSC-W006, FR-9 CustomerJIRA-only block, FR-7 CustomerJIRA-only state machine, `[D-080]` preference rule, FR-88 owner identity model, `[D-088]` PM identity model.

---

## D-094: `item_type` enum lowercase_snake_case rename — SUPERSEDED 2026-06-23 by SP UI engineer mixed-case lock

**Date**: 2026-06-20
**Status**: Superseded (by SP UI engineer enum lock 2026-06-23 — captured here for audit; the SUPERSEDING decision is documented in Consequences)

**Context**: During the 2026-06-20 28-FR deep-scan sweep, the architect locked `item_type` enum values from PascalCase/mixed-case to `lowercase_snake_case` across ~190 spec sites (`Confirmation` → `confirmation`, `Default` → `default`, plus the long-named categories `test_tech_waiver_report` and `compliance_certification_release_notes`). Motivation was cross-cascade consistency with the slug→id rename `[D-091]` and Python enum-style alignment. Cascade landed in `template_schema/MODULE.md` (commit `9c39a0e`) + Pydantic models (commit `e3b0cb5`) + rule_engine condition expressions + requirements.md inline.

**Decision**: `ItemType` enum values rendered as `lowercase_snake_case` across requirements.md, MODULE.md cascade, Pydantic models, SP-side configuration, rule-engine condition expressions, and template.yaml authoring: `confirmation`, `test_tech_waiver_report`, `compliance_certification_release_notes`, `default`.

**Why**:
- (a) Consistency with the `[D-091]` slug→id global rename direction (snake_case throughout HILDA-canonical surface).
- (b) Python-native enum style; reduces translation friction between Pydantic + JSON-serialized event payloads + rule-engine condition strings.
- (c) Eliminates per-spec-site case ambiguity surfaced during the 28-FR deep-scan.

**Consequences**:
- (a) ~190 requirements.md sites rewritten 2026-06-20 (commit `fc41eb0`).
- (b) `template_schema/enums.py` ItemType values lowercased (commit `e3b0cb5`).
- (c) Rule-engine condition expressions reference lowercase values; MODULE.md cascade applied (commit `20aa181`).
- (d) **SUPERSEDED 2026-06-23** by SP UI engineer enum lock (commit `649f64a`): short-label categories `Confirmation` + `Default` reverted to **PascalCase** at SP UI engineer's request (SP-column Choice values render better as short PascalCase labels); long-named categories `test_tech_waiver_report` + `compliance_certification_release_notes` remain `snake_case`. Resulting mixed-case canonical enum: `Confirmation` / `test_tech_waiver_report` / `compliance_certification_release_notes` / `Default`. Cascade applied to `template_schema` (commit `649f64a`) + dashboard (commit `7dee1ed` D2) + rule_engine (commit `20aa181` D11). Future ADR may formalize the mixed-case lock if it diverges further; for now the supersession is captured here.

**Anchors**: FR-7, FR-58, requirements.md item_type enum, `[D-091]` (slug→id alignment direction), `template_schema/MODULE.md`, commits `fc41eb0` / `e3b0cb5` / `649f64a` / `7dee1ed` / `20aa181`.

---

## D-095: Ph-1 setup-window owner-editability + FR-40 owner_corp_email exclusion + TSC-W006 runtime-at-Start-Collection + FR-71 ODF write policy

**Date**: 2026-06-20
**Status**: Ratified

**Context**: Multiple FRs touched owner identity authoring boundaries during the 2026-06-20 28-FR sweep (FR-88 owner identity model + FR-2 owner-field SP-write surface + FR-25 (b) CustomerJIRA role-collapse + FR-71 Owner Discovery Function + FR-84 SP-alert routing + FR-40 template.yaml owner fields). The architect locked a unified "Ph-1 setup-window owner-editability" rule to resolve when owners are author-supplied vs runtime-discovered vs operator-edited.

**Decision**: Ph-1 owner-editability window: TPM may edit owner identity fields (4-field set per FR-88 + `[D-080]`) on the SP UI BEFORE clicking **Start Collection** (FR-8 trigger). Once Start Collection fires, owner fields become read-only for the duration of the collection cycle (FR-3 / DEF-22 lock; Ph-2 introduces owner-change capability). Concrete sub-locks:
- **FR-40 owner_corp_email exclusion**: `owner_corp_email` is NOT carried in `template.yaml` — owners are author-supplied by TPM in SP UI before Start Collection, not template-time-authored.
- **TSC-W006 timing**: runtime validator fires at Start Collection time (not template-load time) per FR-25 (b) CustomerJIRA-only role-collapse `owner_corp_email = TPM.Work_email` check per `[D-093]`.
- **FR-71 ODF write policy**: Ph-2 Owner Discovery Function results write back to the same 4-field set; Ph-1 has no ODF (owners are TPM-supplied).

**Why**:
- (a) Resolves the cross-FR cascade conflict between "owners come from template.yaml" (FR-40 implied) vs "owners come from SP-UI TPM authoring" (FR-2 + FR-88 lock) — locks the latter.
- (b) `owner_corp_email` exclusion from template.yaml prevents author-time identity-binding for fields that are inherently TPM-authored on real-world rosters.
- (c) Timing TSC-W006 at Start Collection (not template-load) lets TPM author owners freely without template-load false-positives.
- (d) Ph-1 deferral of ODF + owner change matches the "minimal viable Ph-1" reduction theme of the cascade (see `[D-089]`, `[D-092]`, `[D-103]`).

**Consequences**:
- (a) `template_schema/MODULE.md` + Pydantic `DeliveryItemBase`: 4-field owner identity with all fields nullable + author-time-nullable allowed (commit `9c39a0e` D2, `e3b0cb5`).
- (b) `template_schema` Pydantic validator TSC-W006 fires from a runtime hook (called by `tracker.start_collection` / FR-8) rather than at YAML load.
- (c) `customizations/template_schemas/MMK/template.yaml` example: all owner fields null (commit `9c39a0e`).
- (d) Ph-1 TPM SP UI capability inventory (STATUS line 319) explicitly excludes owner field edits after Start Collection per FR-3 / DEF-22.
- (e) FR-71 ODF write policy: Ph-2 only.

**Anchors**: FR-2, FR-3 (DEF-22), FR-8, FR-25 (b), FR-40, FR-71, FR-84, FR-88, `[D-080]` (owner email split), `[D-086]` (free-form text owner identity), `[D-093]` (FR-25 (b) role-collapse), `template_schema/MODULE.md`, commits `fc41eb0` / `9c39a0e`.

---

## D-096: FR-82 nested tag-set model — JSON list-of-lists + TSC-W007 subset validator + TSC-W008 doc_count derivation

**Date**: 2026-06-20
**Status**: Ratified

**Context**: During the 2026-06-20 28-FR sweep, FR-82 `item_description` field semantic was locked. Prior model treated `item_description` as a flat semicolon-separated tag list (used by FR-52 strict-substring + fuzzy-match routing pipeline steps 1 and 2). The architect refined this to a **nested tag-set model** (JSON `list[list[str]]`) representing per-document expected-tag-set groups — needed for items where a single work-item covers multiple expected document deliveries with distinct tag sets.

**Decision**: `DeliveryItemBase.item_description` field is typed `list[list[str]]` (JSON-serialized for SP storage). Each inner list represents one expected document's tag set; the outer list enumerates all expected documents under the work-item. Two derived Pydantic validators:
- **TSC-W007** — subset detection: any inner tag-set that is a strict subset of another inner tag-set in the same item is a likely template-authoring error (one document strictly subsumes another's tag set); warn at template load.
- **TSC-W008** — `doc_count` consistency: `doc_count` (per-row field on Deliverables_<customer_id>) MUST equal `len(item_description)` (number of expected documents). Emit warning when mismatch (TPM authored 2 tag-sets but doc_count = 3, etc.).

**Why**:
- (a) Real-world need: a work-item like "Compliance docs" may legitimately expect multiple deliverables, each with its own tag-set (`[["Sustainability"], ["Bluetooth_SIG"], ["WHQL"]]`).
- (b) The previous flat semicolon-separated tag model conflated per-document tag groups (couldn't represent a work-item expecting both `[A, B]` AND `[C, D]` as separate documents).
- (c) Subset detection (W007) flags a common authoring mistake where one inner tag-set is a superset of another.
- (d) `doc_count` consistency (W008) lets the SP UI engineer correctly populate doc_count per row from template-authored data without manual count drift.

**Consequences**:
- (a) Pydantic `DeliveryItemBase.item_description: list[list[str]]` (commit `e3b0cb5`).
- (b) New error codes TSC-W007 + TSC-W008 registered in `template_schema/error_codes.py` + diagnostics catalog (commits `9c39a0e` D13, `e3b0cb5`).
- (c) `milestones_workitems_fields_values.xlsx` Deliverables tab updated to JSON-serialized format (`[["Sustainability"]]` not `'Sustainability'`); STATUS flag 2026-06-20 enumerates row-by-row corrections.
- (d) FR-52 routing pipeline updated: strict-substring + fuzzy-match steps consume the flattened union of all inner tag-sets per item.
- (e) `customizations/template_schemas/MMK/template.yaml` items use nested form (commit `9c39a0e`).

**Anchors**: FR-52, FR-82, TSC-W007, TSC-W008, `template_schema/MODULE.md`, commits `fc41eb0` / `9c39a0e` / `e3b0cb5`.

---

## D-097: `customer_delivery_info` base-URL + `delivery_path_template` per-customer folder expansion (FR-69)

**Date**: 2026-06-20
**Status**: Ratified

**Context**: During the 2026-06-20 FR-69 sweep, the architect locked the customer-side carrier-delivery path composition model. Prior FR-69 framed carrier folder structure as a per-customer `portal_structure.yaml`. The lock simplified to: (a) `customer_delivery_info` = base URL (e.g., `drive.google.com`) authored at customer template level; (b) `delivery_path_template` = per-customer Jinja-style path template authored at customer level; (c) per-item `target_folder` = template-author-supplied sub-path; (d) `customer_adapter` consumes the fully-resolved path string composed by the `workflow_engine.tasks/submission` task body.

**Decision**: Path composition contract for FR-19/FR-77 customer delivery upload:

```
final_path = customer_delivery_info
           + delivery_path_template_expanded({project_model, milestone_name})
           + target_folder
```

`customer_delivery_info` + `delivery_path_template` are author-time fields on `CustomerTemplateBase`. `target_folder` is per-item on `DeliveryItemBase` (template-author-supplied). `workflow_engine` composes the final path string; `customer_adapter.upload_attachment(target_folder=<resolved>)` receives a fully-resolved string and does NOT itself compose paths.

Concrete example: `drive.google.com/OEM_Folder1/OEM_Folder2/SM-S901U/P1/Compliance/Sustainability`.

**Why**:
- (a) Three-level composition cleanly separates customer-level constants (base URL + folder skeleton) from per-item leaf paths.
- (b) Pushes path composition to `workflow_engine` (single integrator point) rather than `customer_adapter` (per-customer subclass); reduces per-adapter duplication.
- (c) `delivery_path_template` Jinja-style supports `{project_model}` + `{milestone_name}` substitution for path patterns shared across milestones.
- (d) Per-item `target_folder` lets the template author assign each work-item to a specific carrier sub-folder (e.g., compliance docs to `Compliance/`; reports to `TestReports/Power`).

**Consequences**:
- (a) `CustomerTemplateBase` gains `customer_delivery_info: str` + `delivery_path_template: str` (commit `9c39a0e` D10, `e3b0cb5`).
- (b) `DeliveryItemBase.target_folder` redefined as template-author-supplied (NOT HILDA-resolved) per NFR-21 §6 amendment 2026-06-21 (commit `9c39a0e`).
- (c) `workflow_engine.tasks/submission.QUEUE_SUBMISSION` composes the final path (per customer_adapter D5 cascade, commit `a833b85`).
- (d) `customer_adapter.upload_attachment` signature: `target_folder: str` (fully-resolved); does not compose.
- (e) MMK example: `delivery_path_template="OEM_Folder1/OEM_Folder2/{project_model}/{milestone_name}"`, `customer_delivery_info="drive.google.com"`.

**Anchors**: FR-19, FR-69, FR-77, NFR-21 §6 (amendment 2026-06-21), `customer_adapter/MODULE.md`, `workflow_engine/MODULE.md`, commits `fc41eb0` / `9c39a0e` / `a833b85`.

---

## D-098: FR-68 hash-match logic DROPPED — uploadAttachment return + UI file-exists check sufficient

**Date**: 2026-06-20
**Status**: Ratified

**Context**: FR-68 (PLM-NSD sync verification post-dispatch) historically specified byte-level hash-matching between PLM-uploaded artifacts and NSD-stored artifacts to confirm carrier-side delivery integrity. During the 2026-06-20 sweep, the architect directed that byte-level hash-match logic is out of scope for Ph-1; verification is sufficient via two simpler signals.

**Decision**: FR-68 Ph-1 verification = upload-success markers only:
- **(i) PLM side**: `IssueTracker.uploadAttachment` return result (issue_tracker domain) — adapter-reported success confirms PLM-side store.
- **(ii) Carrier side**: per-folder list-files check (customer_adapter domain) via the Google Drive API binding — confirms file presence on carrier portal.

Byte-level hash-match (originally FR-68) is dropped from Ph-1 scope. New `customer_adapter.CarrierCapabilityFlags.supports_upload_success_verification` field (Ph-1 Google Drive = True) gates the per-folder list-files check. `supports_hash_verification` retained as Ph-2 forward-looking flag but is not required for FR-68 Ph-1 verification.

**Why**:
- (a) Byte-level hash-match required reading files back from both PLM + carrier post-upload — significant operational complexity for marginal verification benefit.
- (b) Adapter-reported `uploadAttachment` success + carrier-side list-files presence already cover the failure modes operations cares about (upload didn't happen / file didn't land on carrier).
- (c) Hash mismatch as a distinct failure mode (file uploaded but corrupted in transit) is exceedingly rare on enterprise networks; HILDA's compact-report discipline and ops re-upload path can handle it post-hoc if it surfaces.
- (d) Drops one cross-system verification dependency that would have required bidirectional file-read from corp_plm_gateway + customer_adapter.

**Consequences**:
- (a) `[D-054]` PLM-Carrier hash-sync anchor narrowed to "individual files only, never zips" semantic (customer_adapter D4 cascade, commit `a833b85`).
- (b) `customer_adapter/MODULE.md` `CarrierCapabilityFlags` adds `supports_upload_success_verification: bool` (Ph-1 Google Drive = True); `supports_hash_verification` reserved Ph-2 (commit `a833b85` D3).
- (c) FR-68 prose retains the carrier-delivery-confirmation intent; specific mechanism = uploadAttachment return + list-files only.
- (d) No `corp_plm_gateway` ↔ `customer_adapter` cross-system file-read coordination required.

**Anchors**: FR-19, FR-68, `[D-054]` (carrier-package semantic; impl note 2026-06-20), `customer_adapter/MODULE.md`, `issue_tracker/MODULE.md`, commit `a833b85`.

---

## D-099: FR-81 option (a) — `force_tracking_enabled` sole per-item field + SP BOOL binary semantic with column-default = true

**Date**: 2026-06-20
**Status**: Ratified

**Context**: FR-81 originally proposed two competing per-item tracking-control models: (a) single per-item `force_tracking_enabled: bool` SP column with column-default = `true`; (b) per-TG `tracking_enabled: bool` + per-item override. During the 2026-06-20 sweep, the architect locked option (a). All items default to tracking-enabled at the SP-column level; the explicit per-item exception is the default work-item (force_tracking_enabled = false per FR-78 inventory).

**Decision**: `DeliveryItemBase.force_tracking_enabled: bool` is the sole per-item tracking-control field. SP-side semantics: BOOL column with column-default = `true` (TPM does NOT have to set it explicitly on every row). The per-TG `tracking_enabled` flag is REMOVED from the template schema; the default work-item is the one explicit exception with `force_tracking_enabled = false` per FR-78 hardcoded inventory.

**Why**:
- (a) Single per-item field removes the per-TG/per-item priority resolution ambiguity that option (b) introduced.
- (b) Column-default = true preserves the operational invariant "tracking is on unless explicitly disabled" — no TPM authoring burden on the common path.
- (c) Default work-item exemption is the one operational pattern that needs `force_tracking_enabled = false` (it exists as a holding bucket for routed-but-unclaimed documents per FR-78; cannot enter the tracking state machine itself).
- (d) Removes the per-TG flag, which collided with the `[D-051]` denormalization architect lock (TG fields denormalized onto DeliveryItemBase).

**Consequences**:
- (a) `template_schema` Pydantic field `force_tracking_enabled: bool = True` on `DeliveryItemBase` (commit `9c39a0e` D5+D6, `e3b0cb5`).
- (b) Per-TG `tracking_enabled` field REMOVED from schema.
- (c) `DefaultWorkItemConfig.force_tracking_enabled = False` per FR-78 hardcoded inventory (commit `9c39a0e` D9, STATUS line 289).
- (d) SP column provisioning: `force_tracking_enabled` BOOL with column-default = true (SP UI engineer xlsx).
- (e) `milestones_workitems_fields_values.xlsx` default WI revert: `force_tracking_enabled = false` (commit `d0aede1`).

**Anchors**: FR-78, FR-81, `[D-051]` (TG denormalization), `template_schema/MODULE.md`, commits `fc41eb0` / `9c39a0e` / `e3b0cb5` / `d0aede1`.

---

## D-100: FR-64 Option (b) — HILDA-owned per-item cascade for Close All Items

**Date**: 2026-06-20
**Status**: Ratified

**Context**: FR-64 (Close All Items milestone-level button) had two competing implementation options surfaced in prior reviews: (a) SP UI engineer's button writes a single `closed_all_items_triggered_at` timestamp on Milestones row; HILDA reads the alert and treats it as a milestone-scoped close signal. (b) HILDA enumerates all CLOSE-eligible items in the milestone and issues per-item `update_delivery_state(target=CLOSED)` calls. The 2026-06-20 architect lock chose option (b) — HILDA owns the per-item cascade explicitly.

**Decision**: FR-64 Close All Items implementation = Option (b): SP UI engineer's button writes the single timestamp on Milestones row → fires SP-alert → HILDA's `workflow_engine.tasks/milestone.close_all_items` task body iterates CLOSE-eligible items in the milestone and calls `tracker.update_delivery_state(target=CLOSED, trigger_source="tpm_button")` per item.

**Why**:
- (a) Per-item cascade preserves the state-machine integrity: every CLOSE transition flows through `tracker.update_delivery_state` with the same guards, audit, and `CommunicationLog` discipline as any other transition.
- (b) Milestone-scoped close as a separate state-machine event would require introducing a milestone-state-machine concept that HILDA doesn't otherwise have.
- (c) Per-item cascade lets each item's CLOSE eligibility be evaluated independently (some items may already be CLOSED; some may be in a guard-rejecting state).
- (d) Audit clarity: each per-item CLOSE shows up as a discrete CommunicationLog row attributable to the milestone-level button press.

**Consequences**:
- (a) `workflow_engine/tasks/milestone.py` `close_all_items` task body iterates CLOSE-eligible items per FR-64 Option (b) (commit `96a498f`).
- (b) `tracker/MODULE.md` D12 cascade documents the per-item iteration contract (commit `e5f186e` — tracker arch revisit D12).
- (c) `rule_engine` evaluates `MilestoneAllClosed` downstream as a separate trigger (commit `20aa181` D12); not coupled to FR-64 directly.
- (d) SP-side Milestones row still carries `closed_all_items_triggered_at` (HILDA-read, SP UI engineer-written per button click).

**Anchors**: FR-64, `tracker/MODULE.md`, `workflow_engine/MODULE.md`, `rule_engine/MODULE.md`, commits `fc41eb0` / `e5f186e` / `20aa181` / `96a498f` / `1e7e8a0`.

---

## D-101: `tpm_resolved_doc_type` STR → LIST Choice conversion (defense-in-depth)

**Date**: 2026-06-20
**Status**: Ratified

**Context**: During the 2026-06-20 sweep, the SP-column data type for `tpm_resolved_doc_type` (per-item TPM-supplied doc_type when FR-87 step (B) re-classification fires) was changed from STR free-form to LIST Choice. Motivation: defense-in-depth — restrict TPM-authored doc_type values to the canonical 5-value `DocType` enum surface (`test_report`, `tech_report`, `waiver`, `compliance_certification_release_notes`, `unresolved`), preventing typo-class TPM input that would silently fail FR-86 alignment-invariant checks downstream.

**Decision**: SP column `tpm_resolved_doc_type` data type = LIST Choice over the 5-value DocType enum (matching the canonical `DocType` Pydantic enum surface). TPM SP UI selects from a dropdown rather than typing free-form text.

**Why**:
- (a) Defense-in-depth: STR free-form would let TPM type `'TestReport'` or `'test report'` or any variant; alignment-invariant validation would fail silently or surface confusing downstream errors.
- (b) SP Choice column natively renders a constrained dropdown for the SP UI — better UX for TPM authoring.
- (c) Mirrors the canonical 5-value DocType enum surface already defined in `template_schema`.
- (d) Reduces error-code surface area: no need for a "doc_type-resolution-invalid" warning code if SP-side input is constrained.

**Consequences**:
- (a) SP UI engineer's `SP_lists_authoritative.xlsx` Deliverables tab `tpm_resolved_doc_type` data type = LIST (Choice over 5 canonical DocType values).
- (b) `template_schema` Pydantic `DeliveryItemBase.tpm_resolved_doc_type: DocType | None` (constrained enum; commit `e3b0cb5`).
- (c) FR-87 step (B) doc_type re-classification consumes the constrained value; no normalization layer needed.
- (d) Cross-FR cascade swept this session per commit `fc41eb0` (`tpm_resolved_doc_type STR->LIST Choice`).

**Anchors**: FR-86, FR-87 step (B), `template_schema/MODULE.md`, `SP_lists_authoritative.xlsx`, commits `fc41eb0` / `e3b0cb5`.

---

## D-102: `corp_id_list` + `email_cc_list` semi-colon separator convention

**Date**: 2026-06-20
**Status**: Ratified

**Context**: Two SP-side STR free-form fields carrying delimited multi-value lists: `corp_id_list` (TG-level corp IDs for messenger escalation per FR-71) and `email_cc_list` (per-item CC distribution per FR-9 outreach). Prior implicit convention was comma-separated. The 2026-06-20 sweep locked the delimiter to **semi-colon** to align with corp messenger / corp Outlook conventions where comma is often part of the displayed identity field (e.g., "Last, First" in display names) and semi-colon is the standard recipient separator.

**Decision**: `corp_id_list` and `email_cc_list` SP STR free-form columns use **semi-colon (`;`)** as the value separator. HILDA parsers split on `;` (trimming whitespace) when consuming these fields.

**Why**:
- (a) Aligns with corp Outlook / corp messenger conventions (semi-colon is the standard recipient separator in corp email tooling).
- (b) Comma can appear within an individual value (e.g., display-name conventions); semi-colon does not.
- (c) Operational consistency: TPMs already type recipient lists with semi-colons in their daily email workflow.

**Consequences**:
- (a) `email_service` outbound composers split `email_cc_list` on `;` (when implemented — pending email_service Module #12 revisit).
- (b) Messenger escalation per FR-71 splits `corp_id_list` on `;`.
- (c) SP UI engineer documents `;` as the canonical separator in column-help-text on the SP column.
- (d) Cross-FR sweep applied per commit `fc41eb0` (`corp_id_list + email_cc_list semi-colon separator`).

**Anchors**: FR-9, FR-71, `[D-086]` (free-form text identity discipline), `template_schema/MODULE.md`, commit `fc41eb0`.

---

## D-103: SP_UI Start Collection enablement narrowed (In Progress removed) + Close All Items single trigger + HILDA cascade

**Date**: 2026-06-20
**Status**: Ratified

**Context**: During the 2026-06-20 sweep, the SP UI engineer's button-enablement rules were refined for two milestone-level buttons:
- **Start Collection** (FR-8 trigger): enablement narrowed — the "In Progress" milestone-state intermediate-enablement was removed; button is enabled only in the well-defined pre-Start-Collection window.
- **Close All Items** (FR-64): reframed as a single milestone-level trigger that fires the HILDA-owned per-item cascade per `[D-100]`, rather than the SP UI engineer pre-enumerating per-item buttons.

**Decision**: Refined SP_UI button enablement and trigger model:
- **Start Collection** is enabled only in the explicit pre-Start-Collection state window (milestone status NOT in {InProgress, Completed}); "In Progress" enablement removed (was previously allowed mid-collection for re-triggering — superseded by the FR-8 idempotent re-trigger pattern).
- **Close All Items** is a single milestone-level trigger; SP UI engineer writes one `closed_all_items_triggered_at` timestamp; HILDA cascades per-item CLOSE via `[D-100]` Option (b).

**Why**:
- (a) Eliminates the "In Progress" intermediate-enablement window for Start Collection — removes a state-machine corner case where the button could fire mid-collection.
- (b) FR-8 idempotent re-trigger discipline (per FR-2 R&R lock) means re-triggering Start Collection mid-collection is operationally redundant; SP-side enablement enforcement is the cleaner mechanism.
- (c) Close All Items single-trigger pattern aligns with `[D-100]` Option (b) HILDA-owned cascade — SP UI engineer ships one button + one timestamp; HILDA owns the per-item iteration.
- (d) Reduces SP UI engineer's Ph-1 deliverable surface area; consolidates with the `[D-089]` Refresh + Download Package Ph-2 deferral theme.

**Consequences**:
- (a) `docs/sp_ui_engineer/SP_UI_button_actions.md` Start Collection enablement rules updated to remove In Progress (commit `fc41eb0`).
- (b) Close All Items section reframes as Option (b) HILDA-owned cascade (commit `fc41eb0`).
- (c) FR-8 + FR-64 spec text updated.
- (d) Downstream `workflow_engine.tasks/milestone.close_all_items` implements the cascade (commit `96a498f`).
- (e) `tracker` Ph-1 dev consumes the per-item cascade (commit `2377f28`).

**Anchors**: FR-8, FR-64, `[D-089]` (Refresh + Download Package Ph-2 deferral; sibling SP UI engineer Ph-1 scope reduction), `[D-100]` (Option (b) cascade), `SP_UI_button_actions.md`, commits `fc41eb0` / `96a498f` / `2377f28`.

---

## D-104: Projects per-customer SP list (supersedes `[D-083]` global Projects + `[D-088]` global Projects.TPM lookup)

**Date**: 2026-06-21
**Status**: Ratified

**Context**: `[D-083]` (2026-06-17) established the Ph-1 SP architecture lock with two per-customer lists (`Deliverables_<customer_id>` + global `Milestones`) plus a **global** `Projects` list reused for `project_id → TPM` lookup. `[D-088]` (2026-06-19) ratified the global Projects.TPM 3-tuple resolution pattern. During the 2026-06-21 NFR-21 deep-scan + Module-by-module revisit session, the architect identified the global Projects framing as load-bearingly wrong: `project_id` is a corp-PLM-assigned key whose namespace is per-customer; reusing a single global Projects list across customers would produce cross-customer key collisions. Lock: Projects becomes per-customer (`Projects_<customer_id>`), preserving Milestones global asymmetry (milestone names like `LE-2` legitimately repeat across carriers; composite uniqueness is what matters there).

**Decision**: `Projects_<customer_id>` SP list per customer (not global Projects). Schema unchanged: each row carries `(Id=project_id, project_model, TPM)` 3-tuple. HILDA caches one entry per customer at bootstrap. `Deliverables_<customer_id>.project_id` is a FK into `Projects_<customer_id>.Id`. Milestones list remains global per intentional asymmetry (composite uniqueness `(carrier, project_id, project_model, Title)` enforced by SP UI engineer).

**Why**:
- (a) `project_id` namespace is per-customer (corp-PLM-assigned per customer relationship); global Projects would produce cross-customer collisions.
- (b) Per-customer Projects mirrors per-customer Deliverables_<customer_id> + per-customer credential / template / config scoping convention established across `[D-077]` / `[D-083]` / `[D-038]` v3.
- (c) Milestones global asymmetry is intentional: milestone names (`LE-2`, `P1`) are shared vocabulary across carriers — global list + composite uniqueness is the cleaner model.
- (d) Resolves cross-FR cascade staleness (~11 load-bearing FRs swept across requirements.md commits `0301bcd` + `17a8e54`).

**Consequences**:
- (a) `NFR-21` amendment §(1)-(6) 2026-06-21 locks 3-list-per-customer scoping (commit `d0aede1`).
- (b) FR-2, FR-9, FR-11, FR-14, FR-25 (b), FR-77, FR-84 rewritten to reference `Projects_<customer_id>` (commits `0301bcd` + `17a8e54`).
- (c) `[D-088]` Projects.TPM 3-tuple lookup pattern PRESERVED but scoped per-customer (`Projects_<customer_id>` query for `project_id → (assigned_pm_id, pm_display_name, pm_email)`).
- (d) HILDA Projects cache: one entry per customer (`dict[customer_id, dict[project_id, 3-tuple]]`), not single global cache.
- (e) Multiple MODULE.md anchor updates: rule_engine D10, workflow_engine D11, dashboard D6 add `[D-083]` for per-customer scope (commits `20aa181` / `1e7e8a0` / `7dee1ed`).

**Anchors**: FR-2, FR-9, FR-11, FR-14, FR-25 (b), FR-77, FR-84, NFR-21 amendment §(1)-(6), `[D-077]` (4-list runtime SP coupling), `[D-083]` (3-list architecture; supersession), `[D-088]` (Projects.TPM 3-tuple lookup; preserved but per-customer scoped), commits `d0aede1` / `0301bcd` / `17a8e54`.

---

## D-105: 4-field owner identity model — `owner_corp_id` as PLM grouping key (supersedes single `owner_email` field)

**Date**: 2026-06-21
**Status**: Ratified

**Context**: Prior owner identity model carried a single `owner_email` field on `DeliveryItemBase` + `DocumentItemAssociation` + `PLMFanOutTarget`. `[D-080]` (2026-06-13/14/15) split `owner_email` into `owner_corp_usa_email` + `owner_corp_email` (per FR-88) to support non-USA-domain owners. During the 2026-06-21 storage MODULE.md revisit (`[D-106]` follow-on), the architect locked the **full 4-field owner identity** (`owner_corp_id`, `owner_corp_usa_email`, `owner_corp_email`, `owner_name`) with **`owner_corp_id` as the PLM grouping key** per FR-5 + FR-8 step 2 + `[D-035]`. Email fields are informational/outreach; `owner_corp_id` is the stable corp-directory identifier that FR-79 PLM fan-out aggregates on.

**Decision**: Owner identity model = 4 fields on `DeliveryItemBase` + `DocumentItemAssociation` + `PLMFanOutTarget`:
- `owner_corp_id: str` — corp directory identifier; **load-bearing PLM grouping key** for FR-79 fan-out (replaces email-based grouping).
- `owner_corp_usa_email: str | None` — preferred outreach (FR-9 + `[D-080]`).
- `owner_corp_email: str | None` — fallback outreach.
- `owner_name: str | None` — display.

FR-79 PLM fan-out groups by `(owner_corp_id, plm_id)` (not `(owner_email, plm_id)`). Storage helpers (`fan_out_plm_associations`, `update_association_plm_attachment`) filter on `owner_corp_id`.

**Why**:
- (a) Email-based PLM grouping breaks when an owner's email changes mid-collection (uncommon but possible; corp directory updates).
- (b) `owner_corp_id` is the stable corp-directory identifier — uniqueness preserved across email updates.
- (c) Email fields per `[D-080]` cover the outreach addressing concern; `owner_corp_id` covers the identity-uniqueness concern.
- (d) `owner_name` for display preserves the human-readable surface.
- (e) Anchors FR-5 ("owner identity is the corp-directory identifier") + FR-8 step 2 (PLM issue creation per owner × milestone uses corp_id as the key) + `[D-035]` (PLM source-of-truth).

**Consequences**:
- (a) `DeliveryItemBase` 4-field owner (commit `9c39a0e` D2, `e3b0cb5`).
- (b) `DocumentItemAssociation` 4-field owner — column added + index on `owner_corp_id` for FR-79 grouping (commit `4029102`).
- (c) `PLMFanOutTarget` grouping key `owner_corp_id` (commit `4029102`).
- (d) `fan_out_plm_associations()` returns DISTINCT `(owner_corp_id, plm_id)` pairs (commit `4029102`).
- (e) `reassign_document_to_workitem` signature: target_owner_corp_id required (PLM key); other 3 fields optional informational (commit `4029102`).
- (f) Storage Invariants updated: "PLM fan-out per-(owner_corp_id, PLM) pair" (commit `d9f108b` D1).
- (g) `rule_engine` `OwnerReassigned` sub-trigger detects any of the 4-field set changing (commit `20aa181` D2).

**Anchors**: FR-5, FR-8 step 2, FR-79, FR-88, `[D-035]` (PLM source-of-truth + grouping key origin), `[D-080]` (owner email split; preserved as subset of this 4-field set), `[D-086]` (free-form text identity discipline), `storage/MODULE.md`, `template_schema/MODULE.md`, commits `9c39a0e` / `e3b0cb5` / `d9f108b` / `4029102`.

---

## D-106: `TGGroupBase` Pydantic model DROPPED — TG fields denormalized onto `DeliveryItemBase`

**Date**: 2026-06-21
**Status**: Ratified

**Context**: `[D-051]` (2026-05-26) originally normalized TG-group fields into a separate `TGGroupBase` Pydantic model + (briefly) a separate `TGGroups` SP list. The 2026-06-12 session impl-noted on `[D-051]` that the SP list was dropped in favor of denormalization onto DeliveryItems columns (per `[D-073]` direction). During the 2026-06-21 Module-by-module revisit session, the architect extended the lock end-to-end: the `TGGroupBase` Pydantic model itself is **DROPPED**; TG fields live denormalized on `DeliveryItemBase`. Storage no longer reads `TGGroupBase` rows; storage consumes TG values via caller-supplied params or via the denormalized DeliveryItemBase fields.

**Decision**: `TGGroupBase` Pydantic model is **DROPPED** from `template_schema`. All TG-level fields are denormalized onto `DeliveryItemBase`:
- `tg_email_group_alias`
- `tg_owner_corp_usa_email`, `tg_owner_corp_email`, `tg_owner_corp_id`, `tg_owner_name`
- `corp_id_list`
- `ingress_nsd`, `folder_routing_enabled`
- `tg_path_id`

Storage's `TGFolderRoutingRow` cache refresh trigger = `DeliveryItemBase` TG-denormalized field update (not TGGroupBase row update). `tg_name` FK comment references `DeliveryItemBase.tg_name` (denormalized) not `TGGroupBase`.

**Why**:
- (a) Architect-stated denormalization preference for SP-side simplicity — fewer SP lists, fewer cross-list joins.
- (b) `[D-051]` + 2026-06-12 impl note already removed the SP-side TGGroups list; keeping the Pydantic model produced a phantom layer with no SP-side authority.
- (c) Denormalization invariant (TSC-W005 TG-equality validator across siblings sharing `tg_name`) enforces consistency at template-load time without requiring a separate model.
- (d) Simplifies storage: no separate `TGGroupBase` row reads; caller supplies TG params or reads from DeliveryItemBase.

**Consequences**:
- (a) `template_schema/MODULE.md` Pydantic `TGGroupBase` REMOVED (commit `9c39a0e` D3+D14).
- (b) `template_schema/__init__.py` exports cleaned (commit `e3b0cb5`).
- (c) `template_schema/tests` adds `test_tg_group_base_dropped` (asserts not importable) (commit `e3b0cb5`).
- (d) `storage/MODULE.md` Depends-on line: explicit note "TGGroupBase DROPPED" + caller-supplied TG params discipline (commit `d9f108b` D3).
- (e) `rule_engine/MODULE.md` Depends-on: TGGroupBase removed (commit `20aa181` D8).
- (f) `TSC-W005` TG-equality validator continues to enforce consistency across siblings sharing `tg_name` per FR-40 denormalization discipline.

**Anchors**: FR-40, FR-71, `[D-051]` (TGGroups normalization; superseded by this end-to-end denormalization lock), `[D-073]` (SP UI engineer hand-off direction; TG columns denormalized onto DeliveryItems), `template_schema/MODULE.md`, `storage/MODULE.md`, commits `9c39a0e` / `e3b0cb5` / `d9f108b` / `20aa181`.

---

## D-107: `credential_service` scope-aware routing — `CredentialScope` enum + `SYSTEM_CRED_SCOPE` map + `SYSTEM_SUBTREE` map

**Date**: 2026-06-21
**Status**: Ratified

**Context**: Prior credential layout was a flat sops directory keyed by `(pm_id, system_type)`. Multiple FR locks during the 2026-06-19 to 2026-06-21 cascade revealed credential-scope heterogeneity: customer JIRA per FR-25 (b) needs per-(account, customer) scoping (carrier governs the account identity); customer Google Drive per FR-19 needs per-customer scoping (Ph-1/Ph-2 shared HILDA ops-team identity per customer); corp PLM gateway + corp messenger gateway use IP-allowlist + identity-assertion per FR-25 (a) + FR-51 pattern (d), so no credential lookup applies (`NO_CREDENTIAL`); email + sharepoint + LLM systems use a single shared HILDA service credential (`SHARED`). The flat sops layout could not express these distinctions. Architect direction 2026-06-21 introduced a scope-aware routing layer.

**Decision**: `credential_service` introduces:
- **`CredentialScope` enum**: `SHARED` | `PER_CUSTOMER` | `PER_ACCOUNT_PER_CUSTOMER` | `NO_CREDENTIAL`.
- **`SYSTEM_CRED_SCOPE` map** (authoritative per-system scope routing):
  - `ISSUE_TRACKER` → `PER_ACCOUNT_PER_CUSTOMER` (customer JIRA per FR-25 (b))
  - `CUSTOMER` → `PER_CUSTOMER` (Google Drive per FR-19/77)
  - `MESSENGER` → `NO_CREDENTIAL` (corp messenger gateway IP-allowlist)
  - `EMAIL` / `SHAREPOINT` / `LLM_*` → `SHARED`
- **`SYSTEM_SUBTREE` map**: filesystem subtree names per scope (e.g., `customer_jira` for PER_ACCOUNT_PER_CUSTOMER; `customer` for PER_CUSTOMER).
- **`get_credential(pm_id, system_type, customer_id=None)`** signature: routes based on `SYSTEM_CRED_SCOPE[system_type]`; raises `CRD-E005` if `customer_id` required-but-missing.

Filesystem layout examples:
- `env_dir/email.enc.env` (SHARED)
- `env_dir/customer/<customer_id>.enc.env` (PER_CUSTOMER)
- `env_dir/customer_jira/<account_id>/<customer_id>.enc.env` (PER_ACCOUNT_PER_CUSTOMER)

**Why**:
- (a) Resolves the 4 distinct credential-scope patterns surfaced across FR-19 / FR-25 (a)+(b) / FR-51 patterns (a)-(d) into a single uniform routing layer.
- (b) `NO_CREDENTIAL` explicit handling: corp PLM / corp messenger callers receive a clear `CRD-E001` instead of silently looking up missing files; documents the IP-allowlist + identity-assertion mechanism per FR-25 (a) + FR-51 pattern (d).
- (c) `PER_ACCOUNT_PER_CUSTOMER` cleanly captures customer JIRA's carrier-governed account identity (account_id overrides pm_id in the file).
- (d) Preserves backward-compat default (`customer_id=None`) for the SHARED systems already in use.

**Consequences**:
- (a) `credential_service/protocol.py` defines `CredentialScope` + `SYSTEM_CRED_SCOPE` + `SYSTEM_SUBTREE` (commit `fd61ec7`).
- (b) `customizations/credentials/MODULE.md` documents the layout (commit `77020ed`).
- (c) `corp_plm_gateway.env.sops` + `corp_messenger_gateway.env.sops` removed from layout; SystemType enum values kept for forward-compat (commit `77020ed`).
- (d) `CRD-E005` new error code added; `CRD-E001` message extended to include `customer_id` (commit `77020ed`).
- (e) Diagnostics PREFIX_REGISTRY: `CAD` → `CSA` rename (customer_adapter prefix alignment; commit `864041e`).
- (f) Downstream cascade: customer_adapter D7 cascade reads `get_credential(pm_id, system_type, customer_id=...)` per `SystemType.CUSTOMER` (commit `a833b85` D7).

**Anchors**: FR-19, FR-25 (a)+(b), FR-51 patterns (a)-(d), FR-77, `[D-019]` (credential service Protocol; preserved interface contract), `[D-038]` (sops-encrypted env file discipline; layout extended), `credential_service/MODULE.md`, `customizations/credentials/MODULE.md`, commits `77020ed` / `fd61ec7` / `864041e` / `a833b85`.

---

## D-108: `rules_paused` SP column on Deliverables for FR-31 sub-1 per-item pause mechanism

**Date**: 2026-06-23
**Status**: Ratified

**Context**: FR-31 sub-1 (per-item pause/resume of automation rules) lacked a concrete SP-side mechanism. The 2026-06-21 storage D5 flag enumerated 3 candidate resolutions: (a) extend AutomationRuleOverride Scope enum to include `Item`; (b) new per-item table `AutomationRuleItemOverride`; (c) separate pause-flag column on Deliverables_<customer_id>. The 2026-06-23 rule_engine arch revisit chose option (c) — simplest mechanism + cleanest separation from per-item parameter override.

**Decision**: New SP BOOL column `rules_paused: bool` on `Deliverables_<customer_id>` (default = false). Per-item pause = direct SP-column read via item snapshot; no Protocol + no separate table. `tracker.DeliveryItemBase` exposes `rules_paused: bool`. `rule_engine.RuleEngine.evaluate(event, item_snapshot=...)` reads `item_snapshot.rules_paused` and short-circuits when true (paused matches flagged via `WFL-W001` then skipped).

**Why**:
- (a) Per-item pause is fundamentally different from per-item parameter override (binary on/off vs structured parameter mutation); overloading AutomationRuleOverride.Scope = Item would muddy the override semantic.
- (b) Direct SP-column read eliminates the need for a Protocol abstraction (`PauseStateLookup`) — the item snapshot already flows through the dispatcher.
- (c) Simplest possible mechanism: TPM toggles the column in SP UI; HILDA reads it on the next event evaluation.
- (d) Cross-module cascade lockstep with `[D-112]` (PauseStateLookup Protocol DROPPED) and `[D-113]` (TriggerDispatcher item_snapshot flow).

**Consequences**:
- (a) `template_schema.DeliveryItemBase.rules_paused: bool = False` (pre-req mini-cascade per rule_engine commit `b6c13a6`).
- (b) SP UI engineer adds `rules_paused` BOOL column to `Deliverables_<customer_id>` schema; SP-side toggle UI (Ph-2 per architect lock per Flag STATUS line 319).
- (c) `rule_engine` reads via `item_snapshot.rules_paused`; PauseStateLookup Protocol dropped (commit `20aa181` D5; `b6c13a6`).
- (d) `workflow_engine.TriggerDispatcher` threads item snapshot before `evaluate()`; pause check fires upstream (commit `1e7e8a0` D2; `11f5e5d`).
- (e) Resolves storage D5 flag (STATUS line 291).

**Anchors**: FR-31 sub-1, `[D-066]` (rule-evaluation discipline), `[D-112]` (PauseStateLookup Protocol DROPPED), `[D-113]` (TriggerDispatcher item_snapshot flow), `rule_engine/MODULE.md` D5, `workflow_engine/MODULE.md` D2, commits `20aa181` / `b6c13a6` / `11f5e5d` / `1e7e8a0`.

---

## D-109: AutomationRuleOverride Postgres-override consumption DEFERRED to Ph-2

**Date**: 2026-06-23
**Status**: Ratified

**Context**: `AutomationRuleOverride` Postgres table + consumption layer was specified for FR-31 sub-2 (per-customer / per-device runtime parameter overrides on top of YAML rules). During the 2026-06-23 rule_engine arch revisit, the architect directed Ph-1 Postgres-override consumption is deferred. Ph-1 early-drop has a single mock customer; per-customer / per-device runtime tuning is not needed; YAML edit + service restart is sufficient for Ph-1 operational profile.

**Decision**: `AutomationRuleOverride` Postgres consumption is DEFERRED to Ph-2. Ph-1 reads polling intervals + rule parameters exclusively from Global YAML rules (`customizations/rules/global/*.yaml`). The Postgres `automation_rule_overrides` table + `OverrideStore` + `ItemOverride` + `InMemoryOverrideStore` Pydantic surface remains in the code (Ph-2 forward-looking) but is dropped from Ph-1 public `__init__.py` surface.

**Why**:
- (a) Ph-1 early drop = single mock customer; per-customer / per-device runtime parameter tuning is YAGNI.
- (b) YAML edit + service restart is the operational profile for Ph-1; no need for hot-tuning mid-run.
- (c) Removes a Ph-1 surface (Postgres table provisioning + storage helper + orphan-audit semantic) without losing the Ph-2 path.
- (d) Aligns with the broader 2026-06-23 Ph-1 narrowing theme (`[D-110]` SIGHUP deferral, `[D-111]` per-customer YAML deferral).

**Consequences**:
- (a) `rule_engine/MODULE.md` D4 cascade (commit `20aa181`).
- (b) `rule_engine/__init__.py` drops Ph-2 forward-looking surface from `__all__` (commit `b6c13a6`); still importable from sub-modules for tests + Ph-2 dev.
- (c) `workflow_engine` D4 cascade: Ph-1 polling intervals come from Global YAML only (commit `1e7e8a0`).
- (d) `dashboard` D1 cascade: `/admin/overrides` endpoint stays in code but renders empty table Ph-1 with "No active overrides (Ph-1)" message (commits `7dee1ed` D1, `dc31949`).
- (e) Existing storage Postgres tables + helpers preserved Ph-2 forward-looking; `list_active_overrides` returns empty in Ph-1.

**Anchors**: FR-31 sub-2, `rule_engine/MODULE.md` D4, `workflow_engine/MODULE.md` D4, `dashboard/MODULE.md` D1, commits `20aa181` / `b6c13a6` / `1e7e8a0` / `7dee1ed` / `dc31949`.

---

## D-110: SIGHUP YAML hot-reload DEFERRED to Ph-2

**Date**: 2026-06-23
**Status**: Ratified

**Context**: Earlier spec text (FR-30 I1; STATUS Flag line 311) referenced HILDA-side `SIGHUP` signal handling for ops-triggered YAML rule + template hot-reload. During the 2026-06-23 rule_engine arch revisit, the architect deferred SIGHUP YAML hot-reload to Ph-2; Ph-1 loads rules at startup only.

**Decision**: SIGHUP YAML hot-reload is DEFERRED to Ph-2. Ph-1 `rule_engine` loads `customizations/rules/global/*.yaml` at startup ONLY. YAML or rule changes require Celery worker + hilda-beat restart. `workflow_engine.beat_schedule` likewise builds at startup only Ph-1.

**Why**:
- (a) Ph-1 operational profile = early drop with infrequent rule changes; service restart is acceptable.
- (b) SIGHUP handler implementation surface (signal handler + thread-safe atomic rule swap + active-evaluation race handling) is non-trivial; YAGNI for Ph-1.
- (c) Resolves STATUS Flag line 311 (SIGHUP implementation confirmation pending) by deferring the requirement.
- (d) Aligns with Ph-1 narrowing theme (`[D-109]`, `[D-111]`).

**Consequences**:
- (a) `rule_engine/MODULE.md` D6 cascade (commit `20aa181`).
- (b) `workflow_engine/MODULE.md` D3 cascade: Ph-1 build_beat_schedule runs at hilda-beat startup only (commit `1e7e8a0`).
- (c) FR-30 I1 SIGHUP reference effectively deferred Ph-2 (spec text update pending; STATUS Flag line 311 closed by this ADR).
- (d) Ops runbook: rule/template change → restart `hilda-worker` + `hilda-beat` containers.

**Anchors**: FR-30 I1, `rule_engine/MODULE.md` D6, `workflow_engine/MODULE.md` D3, STATUS Flag line 311, commits `20aa181` / `1e7e8a0`.

---

## D-111: Per-customer + per-device YAML rule directories DEFERRED to Ph-2

**Date**: 2026-06-23
**Status**: Ratified

**Context**: FR-30 rule storage layout originally specified a 3-tier YAML resolution: Global / Customer / Device. The 2026-06-23 rule_engine arch revisit deferred Customer + Device tiers to Ph-2; Ph-1 reads only `customizations/rules/global/*.yaml`.

**Decision**: Ph-1 rule loading reads ONLY `customizations/rules/global/*.yaml`. Customer-tier (`customizations/rules/<customer_id>/*.yaml`) and Device-tier (`customizations/rules/<customer_id>/<device_id>/*.yaml`) directories are DEFERRED to Ph-2. The 3-tier ladder code in `rule_engine.resolver` is preserved (fires only if non-global subdirs are populated, which Ph-1 ops contract avoids) but documented as Ph-2 forward-looking.

**Why**:
- (a) Ph-1 early drop = single mock customer; per-customer / per-device rule customization is not yet operationally needed.
- (b) Ph-1 ops contract: populate only `customizations/rules/global/` directory.
- (c) Preserves the ladder code (no architectural rollback) — Ph-2 activation is "populate the subdirs" with no code changes.
- (d) Aligns with Ph-1 narrowing theme (`[D-109]`, `[D-110]`).

**Consequences**:
- (a) `rule_engine/MODULE.md` D7 cascade (commit `20aa181`).
- (b) `rule_engine.loader` module docstring: Ph-1 = Global-only directory walk; Customer/Device tier walking forward-looking (commit `b6c13a6`).
- (c) `rule_engine.resolver._ladder()` Ph-1 = Global-only resolution; Customer + Device tiers remain forward-looking (commit `b6c13a6`).
- (d) Ops runbook: rule changes → edit `customizations/rules/global/*.yaml` + restart workers (per `[D-110]`).

**Anchors**: FR-30, `rule_engine/MODULE.md` D7, commits `20aa181` / `b6c13a6`.

---
## D-113: `TriggerDispatcher` `item_snapshot` flow — rule_engine receives snapshot from caller (D12 cascade)

**Date**: 2026-06-23
**Status**: Ratified

**Context**: Prior `rule_engine` evaluator design assumed `RuleEngine` would either look up item state internally or accept a per-rule pause-lookup callback. The 2026-06-23 rule_engine + workflow_engine cascade (with `[D-108]` `rules_paused` SP column + `[D-112]` PauseStateLookup drop) introduced a cleaner pattern: `TriggerDispatcher` fetches the item snapshot from storage BEFORE calling `rule_engine.evaluate(event, item_snapshot=item)`. Storage round-trip happens once at the dispatcher level; rule_engine becomes purely functional.

**Decision**: `TriggerDispatcher.dispatch(event)` flow:
1. Construct `EntityRef` from `event_context`.
2. Fetch item snapshot from storage Protocol (`storage.get_delivery_item(...)`) BEFORE rule evaluation. Item-less events (milestone-scoped) skip this step.
3. Call `rule_engine.evaluate(event, item_snapshot=item)` with the snapshot threaded through.
4. Pass snapshot into Celery event_context so downstream task bodies don't re-read from storage.

`rule_engine.RuleEngine` is purely functional in evaluation — does not touch storage. Pause check + rule conditions all read from `item_snapshot`.

**Why**:
- (a) Single storage round-trip per event vs N round-trips inside rule conditions — performance win.
- (b) `rule_engine` becomes purely functional in evaluation; trivially testable with `SimpleNamespace(rules_paused=...)` fixtures.
- (c) Item-less events (milestone-scoped triggers like `MilestoneAllClosed`) cleanly skip the storage fetch.
- (d) Snapshot threaded into Celery event_context lets downstream task bodies avoid re-fetching the same item state.
- (e) Aligns with `[D-108]` + `[D-112]` cascade.

**Consequences**:
- (a) `workflow_engine.dispatcher.TriggerDispatcher` flow per spec above (commit `11f5e5d`, `1e7e8a0` D12).
- (b) `rule_engine.RuleEngine.evaluate(event, item_snapshot=...)` signature change (commit `b6c13a6`).
- (c) Tests verify: item-snapshot threading + paused-skip + item-less-event skip-fetch (commit `11f5e5d` TestDispatcher).
- (d) Event payloads passed to Celery task bodies carry item_snapshot reference (avoid re-fetch).
- (e) Storage Protocol on `TriggerDispatcher.__init__`: optional `storage` Protocol param (commit `11f5e5d`).

**Anchors**: FR-31 sub-1, `[D-108]` (`rules_paused` SP column), `[D-112]` (PauseStateLookup drop), `workflow_engine/MODULE.md` D2 + D12, `rule_engine/MODULE.md` D5, commits `1e7e8a0` / `b6c13a6` / `11f5e5d`.

---

## D-114: Dashboard Ph-1 auth — reverse-proxy header-trust (decision 2 option (b)); production source-IP allowlist required

**Date**: 2026-06-24
**Status**: Ratified

**Context**: `dashboard/MODULE.md` "Architectural decisions to lock" enumerated 6 open decisions during 2026-06-12 design. Decision 2 (reverse-proxy identity-forwarding mechanism) had two candidates: (a) Kerberos re-forward via Negotiate to dashboard backend; (b) corp reverse proxy validates Kerberos at the proxy edge + sets `X-Authenticated-User` trusted header that dashboard reads. During Ph-1 dashboard dev 2026-06-24, the architect locked option (b).

**Decision**: Dashboard Ph-1 auth model = **reverse-proxy header-trust**:
- Corp reverse proxy validates user Kerberos / SPNEGO at the proxy edge.
- Proxy sets `X-Authenticated-User` (+ optional `X-User-Email`) header on the forwarded request.
- Dashboard reads the trusted header; does NOT re-validate Kerberos.
- Dashboard `auth.py` middleware rejects client-supplied `X-Authenticated-User` headers (must come from proxy only).
- **Production deployment MUST enforce source-IP allowlist at proxy + network layer** — the trust relationship is "this request came from the corp reverse proxy, not from a direct client".
- `mock_auth=True` config bypasses auth for tests + `--serve --mock` developer flow.

**Why**:
- (a) Kerberos re-forward (option (a)) requires dashboard backend to participate in the SPNEGO handshake — significant complexity (Python Kerberos library + krb5 keytab on HILDA PC).
- (b) Header-trust pattern is standard for corp reverse-proxy + backend service deployments; corp infra already supports Kerberos validation at proxy edge.
- (c) Source-IP allowlist at proxy + network layer is the standard mitigation for header-trust spoofing.
- (d) Defers Kerberos infrastructure deployment on HILDA PC (no krb5 keytab + no ticket cache needed; matches the broader NTLM-only stance per `[D-006]` impl note 2026-06-14).

**Consequences**:
- (a) `dashboard.auth` middleware reads `X-Authenticated-User` + `X-User-Email` from request headers; rejects client-supplied variants (commit `dc31949`).
- (b) `DashboardConfig.mock_auth` field gates the bypass for tests (commit `dc31949`).
- (c) Production deployment runbook: source-IP allowlist at proxy + network layer (operational invariant).
- (d) STATUS Flag line 329 dashboard architecture deferred items (b) (reverse-proxy identity-forwarding) RESOLVED by this ADR.
- (e) Test coverage: 401 unauth in production mode + proxy-forwarded identity accepted (commit `dc31949` TestGetDocumentSection).

**Anchors**: FR-57, FR-59, FR-60, FR-61, `[D-006]` (NTLM auth posture; sibling discipline), `[D-074]` (browser → hilda-proxy auth scope), `dashboard/MODULE.md` "Architectural decisions to lock" decision 2, commits `dc31949` / `7dee1ed` / `97e8b37`.

---

## D-115: Dashboard-local auth middleware — defer cross-cutting `core/src/auth/` module split until FR-62 Ph-2

**Date**: 2026-06-24
**Status**: Ratified

**Context**: `dashboard/MODULE.md` decision 3 enumerated a candidate `core/src/auth/` cross-cutting module for Kerberos / SPNEGO middleware shared by dashboard + future HTTP endpoints (including FR-62 PM/TPM upload surface in Ph-2). The Ph-1 dashboard dev session 2026-06-24 chose to defer the module split until FR-62 forces the question.

**Decision**: Auth middleware lives dashboard-local (`core/src/dashboard/auth.py`) for Ph-1. The cross-cutting `core/src/auth/` module split is DEFERRED until FR-62 (Ph-2 PM/TPM upload endpoint) forces the question.

**Why**:
- (a) Ph-1 has only one HTTP-facing module (dashboard); cross-cutting auth module would be over-engineered.
- (b) Header-trust pattern (per `[D-114]`) is simple enough that duplication when FR-62 lands is acceptable; refactor-to-shared-module is straightforward.
- (c) FR-62 Ph-2 introduces upload endpoint with stricter validation requirements (may differ from dashboard read-only model); premature shared-module abstraction could constrain Ph-2 design.
- (d) Minimizes Ph-1 surface; aligns with the broader Ph-1 narrowing theme.

**Consequences**:
- (a) `core/src/dashboard/auth.py` implements header-trust middleware (commit `dc31949`).
- (b) No `core/src/auth/` module created in Ph-1.
- (c) Ph-2 trigger: when FR-62 upload endpoint design begins, evaluate auth-module extraction (target modules: dashboard + FR-62 upload + any other Ph-2 HTTP endpoints).
- (d) STATUS Flag line 329 dashboard architecture deferred item (e) (cross-cutting `core/src/auth/` MODULE.md candidate) RESOLVED as Ph-2 deferral by this ADR.

**Anchors**: FR-57, FR-62 (Ph-2 trigger), `[D-114]` (header-trust auth pattern), `dashboard/MODULE.md` "Architectural decisions to lock" decision 3, commits `dc31949` / `7dee1ed`.

---


---

## D-112: `PauseStateLookup` Protocol DROPPED — replaced by `rules_paused` SP column read

**Date**: 2026-06-23
**Status**: Ratified

**Context**: `PauseStateLookup` Protocol was specified during earlier rule_engine + storage MODULE.md design as the abstraction layer for FR-31 sub-1 per-item pause-state reads. The 2026-06-23 rule_engine arch revisit (with `[D-108]` `rules_paused` SP-column mechanism) eliminated the need for the Protocol entirely — item snapshot already flows through the dispatcher and carries the boolean.

**Decision**: `PauseStateLookup` Protocol is DROPPED. Replaced by direct read of `item_snapshot.rules_paused` (per `[D-108]`). `rule_engine.RuleEngine` constructor no longer takes a `pause_lookup` parameter. `rule_engine.evaluate(event, item_snapshot=...)` reads `item_snapshot.rules_paused` directly. Item-less events pass `item_snapshot=None` and skip the pause check entirely.

**Why**:
- (a) Adding the Protocol abstraction on top of the new `rules_paused` SP column would be ceremony without value — the item snapshot already carries the field.
- (b) Simplifies `RuleEngine` construction (1-arg constructor vs 2-arg with lookup).
- (c) Eliminates `NoPauseState` no-op implementation + InMemoryOverrideStore-style fixtures.
- (d) Aligns with `[D-113]` TriggerDispatcher item_snapshot flow.

**Consequences**:
- (a) `rule_engine.evaluator.RuleEngine` constructor: `pause_lookup` parameter removed; new `item_snapshot` kwarg on `evaluate()` + `explain()` (commit `b6c13a6`).
- (b) `rule_engine/__init__.py`: `PauseStateLookup` + `NoPauseState` dropped from `__all__` (commit `b6c13a6`); stale import comment removed (D-112 cleanup commit).
- (b2) `core/src/rule_engine/pause_state.py` DELETED entirely per D-112 cleanup (no live imports remain after 2026-06-23 cascade; file was orphan dead code).
- (c) `workflow_engine.TriggerDispatcher.__init__` drops `pause_lookup` param (commit `11f5e5d`, `1e7e8a0` D2).
- (d) Tests: `test_paused_item_flagged_not_dropped` rewritten to pass `item_snapshot=SimpleNamespace(rules_paused=True)`; `test_milestone_pause_is_ph2_deferred` verifies item-less events skip pause check (commit `b6c13a6`).
- (e) `diagnostics_cli.py`: `NoPauseState` import dropped; `RuleEngine()` 1-arg constructor (commit `b6c13a6`).

**Anchors**: FR-31 sub-1, `[D-108]` (`rules_paused` SP column origin), `[D-113]` (TriggerDispatcher item_snapshot flow; sibling decision), `rule_engine/MODULE.md` D5, commits `20aa181` / `b6c13a6` / `11f5e5d`.


---

## D-116: customer_adapter thin-wrapper strategy — HILDA Protocol contract wraps user's pre-existing self-contained Google Drive binding

**Date**: 2026-06-25
**Status**: Ratified

**Context**: Original `[D-054]` 2026-06-05 implementation note positioned HILDA as owner of the full Google Drive selenium / playwright headless Chromium stack for FR-19 customer-delivery upload — selenium amendment + `GoogleDriveBaseAdapter` base class + selector versioning + Chromium operational dependency + PM session pool + capability flags all in HILDA scope. During the 2026-06-24 customer_adapter arch revisit (commit `a833b85`), the architect surfaced a pre-existing Google Drive binding (developed independently on Work PC, self-contained, selenium-backed) that can serve as the actual upload mechanism. The 2026-06-25 Q&A loop locked the final binding contract.

**Decision**: HILDA's `GoogleDriveBaseAdapter` is a **Protocol-conformant thin wrapper** around the user's pre-existing Google Drive binding. The binding's final API:

```
uploadAttachment(Model_No, milestone_name, source_dir, target_dir,
                 filename, pm_id, pm_password, totp_code) -> bool
```

8 args. Bool return (`True` = uploaded successfully; `False` = upload completed but post-verify failed). Raises on infrastructure failure (network / selenium timeout / auth rejected / MFA failed / file not found at `source_dir/filename`). Binding auto-creates `target_dir` under `<customer-baked-root>/<Model_No>/<milestone_name>/` if missing.

Ownership split:
- **HILDA owns**: `CustomerAdapter` Protocol contract + `CarrierUploadResult` shape + `CommunicationLog` per FR-42 + per-call credential composition (3-tuple resolution + pyotp TOTP code generation) + clock-skew diagnostic warning (CAD-W005).
- **Binding owns**: the actual selenium-backed Google Drive automation (session login, MFA, UI selectors, target-folder creation, post-upload verification) — self-contained per architect direction 2026-06-25.

Path composition boundary (per (B-α) lock 2026-06-25):
- HILDA passes identifier **components** (`Model_No`, `milestone_name`, `target_dir`) — NOT a fully-resolved Drive path; binding composes the Drive path internally. Customer-specific Drive folder naming conventions stay in Cline's domain (per `[D-027]` Teacher/Student split).
- HILDA passes fully-resolved local SOURCE path (`source_dir`); binding reads `<source_dir>/<filename>` to get file bytes.

Customer identity: NO `customer_id` arg on the binding. Per-customer subclass at `customizations/customer_adapter/<customer_id>_adapter.py` carries the customer's Drive root path baked in; one binding instance per customer.

Credential model: `credential_service.get_credential(pm_id, SystemType.CUSTOMER, customer_id=...)` returns `CustomerCredential(user_id, password, totp_seed)` — a 3-tuple. HILDA generates the current 6-digit TOTP code at upload time via `pyotp.TOTP(totp_seed).now()`; passes (`user_id`, `password`, `totp_code`) to the binding per call. No session-cookie blob; no HILDA-side session pool. Long-lived secret (seed) stays in credential_service vault per `[D-038]`; short-lived TOTP code is ephemeral in memory per upload.

Per `[D-027]` Teacher/Student: HILDA-side Protocol scaffold + thin wrapper authored Personal PC (Claude); concrete binding call body wired by Cline on Work PC. No proprietary API details land on public GitHub per NFR-2.

**Why**:
- (a) **Avoids duplication** — user's binding already works; rewriting it in HILDA is redundant + slower delivery.
- (b) **Air-gap discipline preserved** — proprietary Google Drive selectors + binding internals stay on Work PC; HILDA's public scaffold carries only the abstract Protocol contract.
- (c) **Self-contained binding simplifies HILDA scope dramatically** — `session_manager.py`, `selector_loader.py`, `capability_flags.py` become Ph-2 forward-looking only; Ph-1 module is just thin protocol + credential composition + binding call. Net ~400 lines vs original ~600-800 estimate.
- (d) **Per-call credentials work for the shared HILDA ops-team Google identity** per `[D-019]` — ops provisions one shared PM account; sops blob stores `(user_id, password, totp_seed)`; all uploads run as this identity Ph-1/Ph-2.
- (e) **Component-pass (B-α) over fully-resolved path (B-β)** — customer-specific Drive folder naming conventions (model identifier format, depth, customer-OEM prefix) stay in Cline's domain (binding-internal); HILDA's FR-77 composition burden simplifies; abstracts away differences between customers without forcing HILDA to know each customer's quirks.
- (f) **Bool return + raises Ph-1; carrier_file_id / carrier_file_url deferred Ph-2** — extracting Google Drive file URLs via selenium is fragmented (drive.google.com vs docs.google.com depending on file type) and selector-fragile; not worth Ph-1 cost. Dashboard FR-57 degrades gracefully to "Uploaded — verify in Google Drive".
- (g) **Reverses `[D-054]` 2026-06-05 impl note** — the HILDA-owns-selenium-stack claim is no longer Ph-1 scope; thin-wrapper pattern replaces it.

**Consequences**:
- (a) `customer_adapter/MODULE.md` 2026-06-25 cascade revisits commit `a833b85`'s D5 (FR-77 target-side path composition reverts to component-pass per (B-α)) + drops `session_manager.py` / `selector_loader.py` / `capability_flags.py` from Ph-1 Sub-modules (mark Ph-2 forward-looking) + updates `CustomerAdapter` Protocol signature + Ph-1 `CarrierUploadResult` shape.
- (b) `credential_service/MODULE.md` adds `CustomerCredential` 3-tuple shape (`user_id` + `password` + `totp_seed`); sops blob format note for `SystemType.CUSTOMER`.
- (c) New 3rd-party dependency: `pyotp` (pure-Python MIT-licensed; ~200 lines; HILDA host installation).
- (d) New CAD-W005 error code — NTP clock-skew warning if `abs(time.time() - ntp_server_time) > 25s`; surfaced by `--diagnostic` mode + emitted at upload time if detected. TOTP windows are typically ±30s so 25s threshold leaves margin.
- (e) `dashboard/MODULE.md` FR-57 rendering: Ph-1 fallback for null `carrier_file_url` shows "Uploaded — verify in Google Drive" text instead of clickable link; revisit Ph-2 when file-URL extraction strategy locks.
- (f) `workflow_engine.tasks.submission.QUEUE_SUBMISSION` task body remains stub-pending until customer_adapter Ph-1 dev lands; once ready, task body composes the 3-tuple credential + TOTP code + identifier components + invokes binding via Protocol.
- (g) `[D-054]` selenium / headless-Chromium / Chromium-operational-dependency claims SUPERSEDED for Ph-1 (binding's responsibility); Ph-2+ may revisit if a non-binding-backed customer modality (e.g., web portal scraping for a non-Google-Drive carrier) is needed — `WebPortalBaseAdapter` peer per `[D-054]`.
- (h) Operational documentation: HILDA host requires NTP-synced clock + binding's Google account requires MFA via TOTP authenticator app (seed captured during MFA setup + stored in sops vault).

**Anchors**: FR-19, FR-42, FR-57, FR-77, NFR-2, `[D-019]` (shared ops-team identity Ph-1/Ph-2), `[D-027]` (Teacher/Student LLM scaffold split — load-bearing for ownership boundary), `[D-038]` (sops-encrypted credential vault), `[D-054]` (selenium amendment; this ADR supersedes the 2026-06-05 impl note for Ph-1), `[D-107]` (credential_service scope-aware routing), `customer_adapter/MODULE.md` (D5 + 2026-06-25 revisit), commit `a833b85`.

---

## D-117: SP 2017 REST write pattern — requests-ntlm + lazy digest dance + MERGE pseudo-verb + `__metadata` wrapper

**Date**: 2026-06-25
**Status**: Ratified

**Context**: SP 2017 REST writes require (a) NTLM authentication; (b) per-session CSRF token (`X-RequestDigest`); (c) `__metadata` type wrapper on bodies (`SP.Data.<ListName>_x005f_<customer_id>ListItem` discriminator); (d) `X-Http-Method: MERGE` + `IF-MATCH: *` pseudo-verb for updates. No async-native NTLM library handles this end-to-end. During Module #11 dev 2026-06-25, the architect shared production-validated `requests-ntlm`-based snippets (per-Celery-task session + 3-step digest dance + MERGE pattern); placeholder `httpx-async` SpClient was replaced with the validated pattern (commit `cb4e182`).

**Decision**: SP 2017 REST writes use a bundle of 6 sub-locks:
- (a) `requests-ntlm` (sync) wrapped in `asyncio.to_thread` per `[D-008]` sync-API-wrapping convention.
- (b) `GlobalSharePointConfig.username` stores the FULL `corp\<user>` NT4-style literal — no separate domain field.
- (c) Lazy digest acquisition: first call triggers the 3-step dance (GET site URL → capture `Set-Cookie` → POST `/_api/contextinfo` → cache `FormDigestValue`). Subsequent calls reuse the cached digest until a 403 response triggers a single regen + retry-once.
- (d) One `SpSession` instance per Celery task — no session pool keyed by `pm_id`, no shared session across workers.
- (e) `__metadata` wrapper + `_x005f_` (underscores) / `_x0020_` (spaces) encoding for SP 2017 OData type discriminator owned by `SpSession.list_item_type()` helper.
- (f) `customer_id` flows as explicit kw-only param through `SpClient.create_list_item` / `update_list_item` / `batch_*` (not parsed from list_name) to compose the `__metadata.type` string `SP.Data.<base>_x005f_<customer_id>ListItem`.

**Why**:
- (a) `requests-ntlm` over async-native — multiple async-native NTLM approaches were tried during pre-2026-06-25 dev; none worked reliably end-to-end, particularly for the `FormDigestValue` acquisition step. `requests-ntlm` is the only validated production path.
- (b) Full `corp\<user>` literal — only SP requires the `corp\\` NT4 prefix; corp PLM consumes raw `owner_corp_id` (no prefix); corp messenger doesn't use `owner_corp_id`; email service uses USA-domain emails. Carrying the literal in the config simplifies SP's single requirement without leaking the prefix discipline to other modules.
- (c) Lazy + 403-refresh over proactive expiry tracking — `FormDigestValue` lifetime varies by SP server config (typically 30 min but configurable); proactive tracking would need server-config inspection. 403-refresh is "always correct regardless of server config" + simpler code.
- (d) Per-Celery-task session over pool — under Ph-1/Ph-2 load (single mock customer + modest milestone count + maybe 10-50 writes per beat tick), the per-task digest dance overhead (~2 extra HTTP round-trips per session) is negligible vs the operational simplicity of stateless task execution. Pooling would introduce shared-state-across-workers concerns (lock contention, digest expiry race conditions).
- (e)(f) `__metadata` wrapper + `customer_id` explicit param — composing the type discriminator string requires both the list base name + customer_id; parsing list_name to extract customer_id would be fragile (per-customer naming pattern `<base>_<customer_id>` couples HILDA to a string convention vs an explicit data contract).

**Consequences**:
- (a) New 3rd-party dependency: `requests-ntlm` (pure-Python, MIT-licensed).
- (b) Async-native rewrite is a non-trivial future refactor — defer until Ph-3+ or until a maintained httpx-NTLM library emerges that handles the full SP 2017 lifecycle.
- (c) Per-task sessions amplify under burst load — set Celery `worker_prefetch_multiplier=1` (or similar throttle) so N concurrent tasks don't trigger N concurrent digest dances against the same SP host within seconds.
- (d) `MERGE` + `IF-MATCH: *` means unconditional overwrites — concurrent TPM edits to the same SP row + concurrent HILDA writes will silently last-write-wins. No optimistic-concurrency safety; acceptable given low-contention Ph-1/Ph-2 ops profile.
- (e) `core/src/sharepoint_integration/sp_session.py` (251 lines) + `mock_server/client.py` `MockSpSession` (70 lines) implement the pattern + tests; `MockSpSession` drives the REAL digest dance against in-process FastAPI mock endpoints (no production-vs-test divergence in session lifecycle).
- (f) NTLM auth + cookie + `FormDigestValue` never logged / `__repr__`-d / emitted to compact reports per NFR-2; test asserts password absence from audit rows.
- (g) `mock_server/app.py` extended with `POST /_api/contextinfo` endpoint + Set-Cookie issuance + 403-on-missing-digest test path + `/__force_403_next_writes__` test knob for digest-refresh path coverage.

**Anchors**: FR-30, FR-84 (`[D-064]` writeback channel), `[D-006]` (SP REST + NTLM/Kerberos on-prem), `[D-008]` (sync-API wrapping), `[D-051]` (was 8-list, superseded by Module #11 architect Q1 2026-06-25 -> 3-list per-customer), `[D-091]` (slug -> id rename), `[D-104]` (Projects per-customer), `sharepoint_integration/MODULE.md` 2026-06-25 cascade, commit `cb4e182`.

---

## D-118: SP list/column provisioning is SP UI engineer's manual responsibility — HILDA does NOT call REST to create lists (formerly D-DRAFT-X)

**Date**: 2026-06-25 (decision originally surfaced 2026-06-12 SP UI engineer review; ratified 2026-06-25 architect Q&A confirmation)
**Status**: Ratified

**Context**: During the 2026-06-12 SP UI engineer review absorption (commits `4da9900` + `48f884c`), the question of who provisions SP lists + columns surfaced: HILDA's `tracker` module reading customer YAML + calling SP REST to create lists, vs SP UI engineer manually creating them via SP UI from the customer YAML as input. Held as `D-DRAFT-X` pending close-session ratification across multiple sessions. Architect Q&A 2026-06-25 confirmed the decision + provided the missing **Why** + **Consequences** + **Rejected alternative** fields.

**Decision**: SP UI engineer manually creates SP lists + columns from the per-customer `customizations/sharepoint_config/customers/<customer_id>.yaml` (canonical -> SP internal name mapping) during the customer-deployment ceremony — including SP-alert email subscriptions and any custom SP-side workflows. HILDA does NOT call REST to create lists or columns. `tracker` module assumes SP lists pre-exist at runtime; SP REST writes per `[D-064]` target only existing rows in already-provisioned lists.

**Why**:
- (a) Corp SP-2017 SP-alert email triggers + custom SP tasks (workflows, custom field types) cannot be expressed via REST API. SP UI engineer must use SP UI for those anyway.
- (b) "Owner of SP module is SP UI engineer; HILDA uses SP services" — clean responsibility boundary. Hybrid (REST for lists + SP UI for alerts/workflows) would split ownership across two integration paths + risk column-name drift between REST-created and SP-UI-created columns.
- (c) Customer YAML serves as the shared comm artifact between HILDA architect and SP UI engineer (alongside `docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` per architect Q5 lock 2026-06-25 + D-117).

**Rejected alternative**: HILDA provisions every milestone of every carrier — `tracker` would auto-create rows in `Milestones_<customer_id>` + `Deliverables_<customer_id>` lists at HILDA startup; TPM would then choose from those pre-populated milestones during the SP UI setup workflow. Rejected on single-responsibility principle: HILDA-owns-runtime + SP-UI-engineer-owns-creation is cleaner than dual-ownership of row creation.

**Consequences**:
- (a) Customer onboarding requires explicit SP UI engineer ceremony step — not "deploy-and-run". Onboarding cadence is bottlenecked on SP UI engineer availability.
- (b) YAML changes are 2-step coordination: HILDA architect updates `customizations/sharepoint_config/customers/<customer_id>.yaml`; SP UI engineer adds the corresponding SP column. Release ordering is HILDA-architect -> SP-UI-engineer -> HILDA-deploy. No atomic deploy.
- (c) `docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` (per Module #11 architect Q5 2026-06-25) is the load-bearing canonical-name-to-SP-internal-column comm channel.
- (d) HILDA's `tracker` module fails fast with `SHP-E001` (HTTP 400 from SP) when SP columns are missing — easier debugging than silent dropped data.
- (e) `customizations/sharepoint_config/customers/<customer_id>.yaml` is the SP UI engineer's READ input; HILDA never writes to it at runtime.
- (f) The 6 SP lists outside HILDA's 3-list scope (TasksTemplate / Tasks / Trials / Activities / Email / SP-side CommunicationLog per Module #11 Q1 lock 2026-06-25) are also SP UI engineer's domain — neither HILDA nor `customizations/sharepoint_config/` map them.

**Anchors**: `[D-020]` (SharePointListProvider Protocol — FileBasedListProvider serves canonical -> SP-internal mapping), `[D-064]` (HILDA -> SP REST writeback channel; uses already-provisioned lists), `[D-065]` (SP UI engineer owns SP Choice values), `[D-073]` (HILDA doesn't provision via REST — earlier impl note), `[D-077]` (4-list reversal; HILDA reads customer/device data from SP rows the SP UI engineer pre-populates), `sharepoint_config/MODULE.md` 2026-06-12 rollback log (D-DRAFT-X), `sharepoint_integration/MODULE.md` Invariants 2026-06-25.

---

## D-119: `tpm_resolved_doc_type` is HILDA-managed 4-value Choice column — DocType enum minus `unresolved`

**Date**: 2026-06-25
**Status**: Ratified

**Context**: Module #11 cascade 2026-06-25 surfaced an unclear relationship between `item_type` (deliverable categorization, 4 values per `[D-094]` SUPERSEDED 2026-06-23 SP UI engineer mixed-case lock) and `tpm_resolved_doc_type` (TPM's manual override for a document's classification per FR-87 step B). Both reference `[D-053]` 5-value `DocType` enum in places but the field-by-field semantics were not explicit. Code already aligned with the architect's intent (`enums.py:79-96` has the 5-value DocType lowercase snake_case enum); ADR was missing to capture the field-vs-enum semantics.

**Decision**: `DocType` and `tpm_resolved_doc_type` are SEPARATE concepts:
- **`DocType` enum** (5 values, lowercase snake_case per `[D-053]` impl note 2026-06-08): `{test_report, tech_report, waiver, compliance_certification_release_notes, unresolved}`. Classifies a specific document file. `unresolved` is the residual state HILDA's service layer assigns when (a) FR-85 Step 1 filename-regex classification fails AND (b) FR-85 Step 2 LLM CLASSIFY_DOC_TYPE returns low-confidence (per FR-86 storage matrix Default-routed-undetermined path).
- **`tpm_resolved_doc_type` field** (on `DeliveryItemBase`; SP Choice column with 4 allowed values, lowercase snake_case): `{test_report, tech_report, waiver, compliance_certification_release_notes}` — `DocType` MINUS `unresolved`. TPM uses FR-87 step (B) HILDA-rendered web page button to resolve an `unresolved` doc into one of the 4 concrete values. TPM cannot select `unresolved` (that is HILDA's classifier-failure marker, not a TPM-meaningful choice).
- **HILDA owns the full lifecycle** of `tpm_resolved_doc_type` — both write (`[D-064]` REST writeback after FR-87 step B button click) AND read (FR-87 page re-render). Therefore the values are HILDA-canonical lowercase snake_case (NOT SP UI engineer mixed-case per `[D-094]` SUPERSEDED).
- **`item_type` is unrelated** — `item_type` classifies the work item / deliverable row (4 values per `[D-094]` SUPERSEDED 2026-06-23 mixed-case: `{Confirmation, test_tech_waiver_report, compliance_certification_release_notes, Default}`); `tpm_resolved_doc_type` classifies a specific document attached to the deliverable. They happen to share `compliance_certification_release_notes` as a value name but otherwise different value sets + different semantic levels.

**Why**:
- (a) `unresolved` is meaningful in HILDA's classifier output but meaningless as a TPM choice — exposing it would let TPM "resolve" a doc to "I do not know what it is", defeating the purpose of FR-87 step (B).
- (b) HILDA owns the read+write lifecycle, so the values are HILDA-canonical (lowercase snake_case per `[D-053]`) — SP UI engineer's PascalCase preference (per `[D-094]` SUPERSEDED for item_type short labels) does not apply here because the SP UI engineer is not the rendering or editing party. SP Choice column allowed values still owned by SP UI engineer per `[D-065]` but they are populated to match HILDA's 4 lowercase values per the comm artifact.
- (c) Separating from `item_type` keeps the work-item-categorization concern (which SP UI engineer renders on the SP milestone view per FR-56) distinct from the document-resolution concern (which HILDA renders on the FR-87 web page per `[D-074]`).

**Consequences**:
- (a) SP Choice column `TPM_x0020_Resolved_x0020_DocType` (per `customizations/sharepoint_config/customers/<customer_id>.yaml` `delivery_items.columns.tpm_resolved_doc_type`) has 4 allowed values: `{test_report, tech_report, waiver, compliance_certification_release_notes}`. SP UI engineer provisions per `[D-065]` from HILDA's spec via the `milestones_workitems_fields_values.xlsx` comm channel.
- (b) HILDA's FR-87 step (B) button submit handler writes one of the 4 values via `[D-064]` REST writeback; HILDA's FR-87 page renderer reads it back via SP REST GET on per-page-refresh per `[D-074]` link-out + per-load READ.
- (c) `template_schema/enums.py` `DocType` enum stays the 5-value source-of-truth; a `TpmResolvedDocType` enum could be added for type-safety on the HILDA writer side (4-value subset), OR HILDA can validate the value at write time (DocType.UNRESOLVED rejected). Either implementation path is acceptable; current `template_schema/models.py:362` uses `str | None`.
- (d) FR-86 storage matrix continues to route Default-routed-undetermined docs into `_staged_revision/` with `doc_type = unresolved` for TPM resolution per FR-87.

**Anchors**: FR-85 (doc_type classification 2-step ladder), FR-86 (storage matrix + alignment-mismatch routing), FR-87 step (B) (Resolve doc_type TPM button + HILDA-rendered web page per `[D-074]`), `[D-053]` (5-value DocType impl note 2026-06-08), `[D-064]` (HILDA -> SP REST writeback), `[D-065]` (SP UI engineer owns Choice values), `[D-074]` (HILDA-rendered link-out for FR-87 surfaces), `[D-094]` SUPERSEDED (item_type SP UI engineer mixed-case 2026-06-23 — UNRELATED to tpm_resolved_doc_type), `template_schema/enums.py:79-96` (DocType 5-value enum already correct), `template_schema/MODULE.md:362` (tpm_resolved_doc_type field declaration).

---

## D-120: Corp PLM 5-API thin-wrapper + tpm_corp_id-as-attribution + in-flight (plm_id, fileID) tracking

**Date**: 2026-06-25
**Status**: Ratified

**Context**: HILDA's `issue_tracker` module needs a corp PLM integration for FR-26 polling (HILDA polls corp PLM for owner-uploaded documents) + FR-77 fan-out (HILDA uploads owner-ingested docs back to corp PLM) + createPLM at tracker provisioning + closePLM at milestone closure. The architect shared via screenshot 2026-06-25 the 5 corp PLM APIs available as already-existing corp services: `createPLM`, `closePLM`, `getdocumentslist`, `downloadFile`, `uploadFile` (uploadFile added in correction after initial 4-API spec). These services are corp-proprietary; HILDA cannot land the concrete binding-call code on public github per NFR-2 and `[D-027]` Teacher/Student split.

**Decision**: `issue_tracker.corp_plm.CorpPlmAdapter` is a Protocol-conformant thin wrapper around the 5 already-available corp PLM APIs. HILDA-side scaffold provides abstract `_invoke_create_plm` / `_invoke_close_plm` / `_invoke_get_documents_list` / `_invoke_download_file` / `_invoke_upload_file` methods that raise `ITR-E001` by default. Per-customer subclass at `customizations/issue_tracker/<customer_id>_corp_plm_adapter.py` filled in by Cline on Work PC per `[D-027]`. Bundle of 7 architect-Q-locked sub-decisions:
- (Q1) `createPLM` fires from `workflow_engine.tasks.lifecycle.PROVISION_TRACKER` ActionKind on tracker creation OR `START_ITEM_COLLECTION` ActionKind on Start Collection (FR-8). Returned `plm_id` written back to `Deliverables_<customer_id>` SP row via `SpClient` digest dance per `[D-117]`.
- (Q2) `closePLM` fires from `workflow_engine.tasks.milestone.FINAL_SWEEP` ActionKind.
- (Q3) PLM polling cadence is deadline-tiered per FR-23-style pattern. Default ladder: >14 days = 60 min; 7-14 days = 30 min; 3-7 days = 15 min; <3 days = 5 min; deadline-day = 1 min. Applied per active DeliveryItem with plm_id set + delivery_state ∈ {Open, OutreachSent, DocumentReceived, OwnerClosed}.
- (Q4) `tpm_corp_id` is the local part of `Projects.TPM` work email (read from `Projects_<customer_id>` SP row per `[D-088]` 3-tuple). PER-CUSTOMER (NOT shared HILDA ops-team identity per `[D-019]`); attribution parameter only, not credential. e.g., `abc@corp.com` -> `abc`. Actual auth flows via corp_plm_gateway PC per FR-25 (a) no-credential pattern.
- (Q5) Error handling Ph-1: retry with exponential backoff + log `ITR-W004` opaquely. After N=5 failed retries: notify HILDA OPS alert -- mechanism TBD architect discussion. Ph-2: detailed error code mapping.
- (Q6) HILDA tracks in-flight downloads per `(plm_id, file_id)` to prevent duplicate concurrent calls. Per `InFlightDownloadTracker` asyncio.Lock-guarded dict Ph-1 (Postgres-backed Ph-2).
- (Q7) BOTH `document_id` AND `file_id` required for `downloadFile`. Both persist on `DocumentIndexRow`.

**Why**:
- (a) Thin wrapper over reimplement -- corp PLM client services are already available + battle-tested; rewriting in HILDA is redundant + duplicates auth complexity.
- (b) `[D-027]` Teacher/Student boundary preserved -- proprietary API binding details stay on Work PC; HILDA's public scaffold carries only abstract Protocol contract + standard discipline (retry / in-flight / CommunicationLog audit).
- (c) tpm_corp_id as attribution-not-credential -- corp PLM API accepts tpm_corp_id as ACTION ATTRIBUTION (recorded in PLM as actor); HILDA's actual auth flows via corp_plm_gateway PC per `[D-019]` no-credential pattern. Decouples HILDA's identity model from corp PLM's auth scheme; per-customer TPM identity flows naturally through Projects.TPM column.
- (d) In-flight tracking per Q6 -- with 5-min polling cadence on near-deadline items + occasional slow downloads (500MB+ files), two concurrent polls can both kick off download for same file. HILDA-side file_hash dedup is the *eventual* safety net; in-flight tracking is the *concurrent* safety net.
- (e) Bundle vs separate ADRs -- the 7 sub-decisions are tightly coupled (Q1+Q2 share workflow_engine + SP write-back; Q3+Q6 share polling architecture; Q4+Q5 share gateway + error-handling). Splitting fragments operational story.

**Consequences**:
- (a) `issue_tracker/corp_plm/adapter.py` raises `ITR-E001` by default; production deployment REQUIRES Cline to land concrete subclass on Work PC with 5 binding calls. Tests use `MockCorpPlmAdapter` end-to-end.
- (b) `tpm_corp_id` derivation lives in `issue_tracker.utils.derive_tpm_corp_id(projects_tpm_email)`. Used by workflow_engine.tasks.lifecycle.PROVISION_TRACKER body + corp_plm_poller.
- (c) HILDA OPS alert mechanism is TBD -- `ITR-W004` is emitted but the OPS notification channel (email? messenger? dashboard alert pane?) is pending architect discussion.
- (d) `InFlightDownloadTracker` is in-memory Ph-1 -- not restart-resilient. If HILDA restarts mid-download, second poll's downloadFile may double-download. Ph-2 Postgres-backed `download_in_progress_at` timestamp solves this.
- (e) Deadline-tiered polling cadence amplifies near-deadline -- ~1 poll/min on deadline-day means up to 1 binding call/min per active item. Combined with Ph-1 expected load (single mock customer + modest milestone count), this is fine.
- (f) `getdocumentslist` returns FULL list every poll -- no "documents since timestamp" filter. HILDA computes new docs as `current_set - DocumentIndexRow_set`; O(N) per poll but fine for Ph-1 (typical milestone has <50 docs).
- (g) `customizations/issue_tracker/example_corp_plm_adapter.py` ships as per-customer scaffold with `# TODO(cline)` markers.

**Anchors**: FR-25 (a), FR-26, FR-77, FR-68 (per `[D-098]` narrowing), `[D-019]` (shared ops-team identity Ph-1/Ph-2; corp PLM differs per Q4), `[D-027]` Teacher/Student, `[D-088]` 3-tuple PM resolution, `[D-091]` slug -> id, `[D-092]` customer JIRA Ph-1 informational, `[D-098]` hash-match dropped, `[D-117]` SP NTLM digest-dance for plm_id writeback, `[D-118]` SP UI engineer provisioning boundary, `issue_tracker/MODULE.md` 2026-06-25 cascade, commit `5c1ab7e`.

---

## D-121: Messenger module ownership boundary -- composition + send + daily-limit in messenger NOT email_service

**Date**: 2026-06-25
**Status**: Ratified

**Context**: HILDA's FR-10 cross-channel escalation reaches owners via corp messenger when email reminders don't yield response. Pre-2026-06-25 design had `email_service/outbound/composer_escalation.py` composing the message + a future `messenger` module just sending it. Architect Q-M6 lock 2026-06-25 redirected: messenger module OWNS its own composition + send + daily-limit; email_service stays focused on the email channel only. Plus the architect locked sendMessage's operational constraints (Q-M1..M5) via the screenshot of the `bool sendMessage(owner_corp_id, message)` API.

**Decision**: Messenger is its own module (separate from email_service) owning the corp messenger channel end-to-end:
- `core/src/messenger/protocol.py` -- MessengerAdapter Protocol (1 method: `send(owner_corp_id, message) -> bool`).
- `core/src/messenger/corp_messenger/adapter.py` -- CorpMessengerAdapter thin wrapper around the gateway's `sendMessage` API per `[D-027]` (abstract `_invoke_send_message` raises `MSG-E003` by default; Cline fills binding on Work PC).
- `core/src/messenger/composer.py` -- compose_escalation renders Jinja2 templates + 4K-byte truncation per Q-M2.
- `core/src/messenger/daily_limit.py` -- DailyLimitChecker enforces ≤3 messages per owner_corp_id per day per Q-M2; queries CommunicationLog + blocks 4th send with `MSG-W001`.
- `core/src/messenger/service.py` -- MessengerService is the orchestrator (composes + daily-limit-checks + invokes adapter + audits + retries) -- the ESCALATE ActionKind's task body entry point.

Plus architect Q&A locks bundled here:
- (Q-M1) `sendMessage` returns true when owner RECEIVES (stronger than gateway-accepted).
- (Q-M2) `message` ≤ 4000 bytes (composer truncates with `MSG-W002`). Per owner_corp_id: ≤ 3 messages per day (blocked with `MSG-W001`).
- (Q-M3) Sender appears as "anonymous HILDA BOT" -- no TPM identity prepended; template signs as `— HILDA BOT`.
- (Q-M4) Escalation trigger is rule-driven via AutomationRules + YAML config. Rule: trigger=ReminderSent; conditions=[reminder_count>=2, days_to_deadline<=3]; actions=[ESCALATE]. Flow: rule_engine evaluates -> workflow_engine ESCALATE ActionKind -> messenger.send_escalation.
- (Q-M5) CommunicationLog audit per FR-42 with bool-only outcome; no message_id thread continuity Ph-1.
- (Q-M6) Messenger module OWNS composition + send + daily-limit -- NOT email_service.

**Why**:
- (a) Single-responsibility -- messenger as channel ≠ email as channel. Composing prose for corp messenger uses different tone/length/template surface vs email (4K cap vs no cap; no subject vs subject; no thread vs threaded). Mixing in email_service muddies both modules' purposes.
- (b) Composition near the channel -- 4K truncation + HILDA BOT signature + Jinja2 template surface live where channel constraints live. If composition lived in email_service, constraints leak across module boundaries.
- (c) Daily-limit at messenger level -- DailyLimitChecker queries CommunicationLog for SAME channel + SAME owner_corp_id today; logically belongs in channel-owner module.
- (d) workflow_engine ESCALATE ActionKind has clean caller surface -- task body calls `messenger.send_escalation(item, batch_id, reason, milestone_ctx) -> SendResult`. ESCALATE doesn't know templates or daily-limits.
- (e) anonymous HILDA BOT over per-TPM identity -- corp messenger gateway doesn't expose per-TPM "from" attribution at gateway layer; messages flow from a single bot account. Per-TPM impersonation would need additional gateway plumbing + auth tokens; out of Ph-1 scope.
- (f) bool-only return without message_id Ph-1 -- corp messenger gateway returns bool only; no message-id callback. Acceptable Ph-1 because escalation is one-shot.

**Consequences**:
- (a) `core/src/email_service/outbound/composer_escalation.py` is now VESTIGIAL -- Ph-1 stub kept compatible until messenger landed; **remove in follow-up next session**.
- (b) workflow_engine.tasks.escalation.ESCALATE ActionKind task body delegates to `messenger.service.MessengerService.send_escalation`.
- (c) `customizations/messenger/example_corp_messenger_adapter.py` ships as per-customer scaffold with `# TODO(cline)` markers.
- (d) `CommunicationLog.channel = 'corp_messenger'` joins existing channel enum; daily-limit check is a SELECT COUNT query gated by this channel value.
- (e) 4K truncation + 3/day cap are HARD constraints at messenger boundary; never bypassed via config flags. If ops needs to send urgent message beyond limits, ops manually messages out-of-band.
- (f) **No retry on bool=False from gateway** -- adapter raises if exception occurs, but bool=False means gateway responded with "failure to deliver"; retrying would re-send (could create duplicate notifications). Bool=False is final.
- (g) Template variables documented in `REQUIRED_TEMPLATE_VARS` and asserted at composition time -- changing template requires updating REQUIRED set.

**Anchors**: FR-9, FR-10 (reminders + cross-channel escalation), FR-31 (rule-driven), FR-42 (CommunicationLog audit), FR-50 (corp messenger outbound -- previously deferred to Ph-2; now Ph-1 per Q-M6 lock), `[D-019]` shared HILDA ops-team identity, `[D-025]` config 3-tier, `[D-027]` Teacher/Student, `messenger/MODULE.md`, commit `04d3e8d`.

---

## D-122: FR-87 step A/B/C TPM resolution is HILDA-rendered-page direct POST -- NOT SP-alert mediated, NOT rule_engine triggered

**Date**: 2026-06-25
**Status**: Ratified (correction applied; cascade pending next session per STATUS Flag)

**Context**: FR-87 has 3 TPM resolution steps: (A) Reassign work-item; (B) Resolve doc_type; (C) Resolve revision. Multiple sessions of MODULE.md design + this session's email_service + automation_rules.yaml work assumed the flow was: TPM clicks SP UI button -> SP alert email lands -> `sp_alert_parser` extracts the change -> rule_engine fires a trigger -> workflow_engine dispatches action. The email_service `sp_alert_parser/parser.py` was built with `tpm_reassign_to_workitem` / `tpm_resolve_doc_type` / `tpm_resolve_revision` action handlers; automation_rules.yaml had 3 FR-87 rules using `ItemModified.TagsModified` as a workaround trigger sub-type. Architect Q3 clarification 2026-06-25 corrected this fundamentally: **FR-87 step A/B/C occurs within HILDA's INTERNAL TAB BROWSER PAGE (the dashboard FR-57 page that opens in a new tab from SP via `[D-074]` link-out), NOT in SP context.**

**Decision**: FR-87 step A/B/C TPM resolution buttons live on `dashboard`'s HILDA-rendered page. Flow:
1. TPM opens HILDA's dashboard page via SP link-out: `hilda.corp/docs/<delivery_item_id>` per FR-57.
2. HILDA-rendered page shows document section + 3 FR-87 resolution buttons when applicable.
3. TPM clicks button -> POST to hilda-api endpoint (NOT SP write, NOT SP alert email).
4. Dashboard POST handler updates storage (DocumentIndexRow + DocumentItemAssociation + DeliveryItemBase as applicable) AND writes resolution fields back to SP `Deliverables_<customer_id>` row via SpCrud digest dance per `[D-064]` + `[D-117]`.

NO `sp_alert_parser` handler dispatch. NO `rule_engine` trigger sub-type. NO `workflow_engine` ActionKind for FR-87 specifically.

**Why**:
- (a) HILDA-rendered page already exists per `[D-074]` link-out -- FR-57 document section page is HILDA's own surface; adding 3 POST endpoints is the minimum-surface-area path.
- (b) TPM ergonomics -- TPM is already on HILDA page when reviewing doc; round-tripping the resolution through SP + SP-alert + email-poll adds 60-300s latency + multiple network hops + brittle parsing. Direct POST is instant.
- (c) No SP write necessary for the action itself -- SP fields `tpm_reassignment_target_item_id` / `tpm_resolved_doc_type` / `tpm_revision_resolution` are *audit-only* per `[D-068]` impl note. HILDA's own storage is source of truth; SP writeback is for SP UI visibility.
- (d) rule_engine doesn't need new trigger sub-types -- workaround `ItemModified.TagsModified` had loose semantics (would fire on any tag edit). Avoiding rule_engine trigger entirely is cleaner than adding `TpmReassigned` / `TpmResolvedDocType` / `TpmResolvedRevision` sub-triggers.
- (e) email_service / sp_alert_parser stays simpler -- removing 3 FR-87 action handlers shrinks sp_alert_parser's responsibility surface to original `[D-047]` scope.
- (f) dashboard module already owns FR-57 / FR-61 / FR-87 button info display -- placing POST handlers there keeps FR-87 ownership in one module.

**Consequences**:
- (a) **CORRECTION CASCADE PENDING NEXT SESSION** (~1-2 hr; TOP PRIORITY in STATUS Next):
  1. Remove 3 FR-87 rules from `customizations/rules/global/automation_rules.yaml`.
  2. Remove FR-87 handlers from `core/src/email_service/sp_alert_parser/parser.py`.
  3. Update `core/src/email_service/MODULE.md` to drop FR-87 references in sp_alert_parser narrative.
  4. Add 3 POST endpoints to `core/src/dashboard/app.py`:
     - `POST /docs/<delivery_item_id>/resolve_reassign` (step A)
     - `POST /docs/<delivery_item_id>/resolve_doc_type` (step B)
     - `POST /docs/<delivery_item_id>/resolve_revision` (step C)
  5. Handlers validate inputs (step B value MUST be in 4-value `tpm_resolved_doc_type` set per `[D-119]`) -> update storage -> SP writeback via SpCrud per `[D-064]` + `[D-117]`.
  6. Update `core/src/dashboard/MODULE.md` to document FR-87 POST routes as part of dashboard Public surface.
  7. Update tests (`test_email_service.py` removes FR-87 handler tests; `test_dashboard.py` adds 6+ FR-87 POST tests).
- (b) A->B->C strict ordering invariant preserved -- enforced at dashboard POST handler level via storage state checks, not via SP-alert ordering.
- (c) `[D-119]` `tpm_resolved_doc_type` 4-value validation lives in POST handler -- HILDA-rendered page already constrains TPM choice; POST handler validates as defense-in-depth.
- (d) No new rule_engine trigger sub-types or ActionKinds -- avoids trigger-taxonomy churn that `ItemModified.TpmResolved*` route would have caused.
- (e) **Backward-incompatible removal** -- FR-87 handlers shipped in commit `0dfb1d4`. Removing is technically a Public surface contraction but no caller exists yet (SP-alert FR-87 path was never wired into workflow_engine), so removal is safe pre-production.
- (f) dashboard now owns FR-87 lifecycle end-to-end -- read (FR-57 + FR-87 button info display) + write (3 new POST endpoints). Single-module responsibility.

**Anchors**: FR-57, FR-58, FR-60, FR-61, FR-74 per `[D-068]` / `[D-077]`, FR-87 (A->B->C strict ordering), `[D-047]` SP alert email channel (sp_alert_parser ORIGINAL scope), `[D-064]` HILDA -> SP REST writeback, `[D-068]` audit-only SP fields for TPM resolutions, `[D-074]` HILDA-rendered link-out + per-load READ, `[D-117]` SP NTLM digest-dance for FR-87 POST writeback, `[D-119]` tpm_resolved_doc_type 4-value, `dashboard/MODULE.md` (will be updated next session), `email_service/MODULE.md` (will drop FR-87 refs next session), `automation_rules.yaml` (3 FR-87 rules to be removed next session), commit `0dfb1d4` (FR-87 handlers to be removed), commit `1caa106` (FR-87 rules to be removed).

---

## D-123: Dashboard `/docs/<customer_id>/<sp_id>` URL + per-load SP READ + no caching + no per-PM auth Ph-1

**Date**: 2026-06-26
**Status**: Ratified

**Context**: The dashboard module's "View Documents" page (FR-57/61) is the TPM-facing surface that opens in a new browser tab when TPM clicks the SP UI engineer's `<a href>` link per `[D-074]` link-out architecture. The original dashboard Ph-1 dev (commit `dc31949`) used a single-segment `/docs/<delivery_item_id>` route + read documents only from HILDA storage + no SP READ at page-load. Yesterday's compliance review surfaced 5 gaps in this design vs the SP UI engineer's actual link convention + per-load READ requirement per `[D-074]`. Architect Q1-Q3 + Q9 locks 2026-06-26 (after running live SP REST probe) resolved all gaps.

**Decision**: Dashboard's "View Documents" flow bundles 4 sub-decisions:
- (a) **URL pattern**: `GET /docs/{customer_id}/{sp_id}` -- 2-segment (was single-segment). `<customer_id>` resolves the SP list name (`Deliverables_<customer_id>`); `<sp_id>` IS SP's `Id` auto-counter PK = HILDA's `delivery_item_id` (same integer, same key). SP UI engineer emits links as `https://proxy-hilda.net/docs/<customer_id>/<sp_id>`.
- (b) **Per-load SP READ**: every `GET /docs/...` call fetches the canonical Deliverables row fresh from SP via `SpCrud.get_item(entity="delivery_items", scope=ListScope(customer_id), item_id=sp_id)` per `[D-074]` Variant A. Returns sp_row that drives: FR-58 Confirmation skip (`item_type == "Confirmation"`); FR-60 review findings rendering; FR-87 button surface (when staged); freshness indicator (`Modified` timestamp).
- (c) **No caching Ph-1**: every page load triggers 1 SP REST call. SP handles ~20/min during a TPM's busy day fine. Caching Ph-3+ if telemetry justifies.
- (d) **No per-PM authorization Ph-1**: all authenticated TPMs (via Kerberos through corp reverse proxy + `X-Authenticated-User` header per `[D-114]` + `[D-115]`) can read all customers. Per-PM access narrowing is Ph-2/Ph-3+ via Vault per `[D-019]` v2.
- (e) **`Projects.TPM` User-field via `$expand`** per Q9: HILDA fetches `?$expand=TPM&$select=Id,Title,TPM/EMail,TPM/Title` -> response carries nested `{"TPM": {"EMail": "abc@corp.com", "Title": "..."}}`. HILDA derives `tpm_corp_id = row["TPM"]["EMail"].split("@")[0]` per `[D-088]` 3-tuple. `SpClient.get_list_items` + `SpCrud.get_item` got `expand` + `extra_select` kwargs (commit `2f791d6`).

**Why**:
- (a) URL pattern matches SP UI engineer's link convention -- single-segment route would 404 against the natural `<a href="/docs/<customer_id>/<sp_id>">` rendering. Plus `<customer_id>` in the URL gives HILDA the list-scope it needs without round-trip lookups.
- (b) Per-load READ over local-cache -- TPM expects fresh state (e.g., another TPM just updated `delivery_state`; the row's audit fields just changed). HILDA storage doesn't necessarily reflect SP-side TPM edits (e.g., owner-status note edits go through email_service's sp_alert_parser asynchronously). The authoritative state is SP at click time.
- (c) No caching Ph-1 -- under Ph-1 single mock customer + modest TPM concurrency, the SP load is negligible. Caching adds correctness risk (stale-while-edit windows) for marginal perf gain.
- (d) No per-PM auth Ph-1 -- Ph-1 has a single mock customer; per-PM access is operationally unnecessary. Adds DB schema + cross-cutting permission middleware for Ph-1 marginal value. Vault-backed per-PM ACL lands Ph-3+ per `[D-019]` v2.
- (e) `$expand` for User fields over `/_api/web/siteusers(<id>)` two-step -- single REST request; OData-native; no extra round-trips. `SpClient` kwarg-additive change (backward-compatible).

**Consequences**:
- (a) `core/src/dashboard/app.py` `GET /docs/{customer_id}/{sp_id}` (2-segment) per commit `f8289f2`.
- (b) `SpCrud.get_item` added in same commit; called by `get_document_section`.
- (c) `build_app` takes `sp_crud=None` for test mode (falls back to `app.state.mock_sp_rows`) -- production deploy injects real `SpCrud`.
- (d) Dashboard `_fetch_sp_row()` raises HTTP 404 if SpCrud returns None (row doesn't exist in SP).
- (e) FR-58 Confirmation detection now authoritative via `sp_row["item_type"]` (was heuristic "no docs -> Confirmation"; commit `f8289f2` Gap 6).
- (f) Pagination `$top + __next` continuation works (resolves `[D-117]` Q4 OPEN architectural question -- confirmed by architect's live probe).
- (g) SP cookie capture returns None in this corp SP deployment -- NTLM auth flows through `requests.Session` per-request anyway; SpSession tolerates gracefully.
- (h) `[D-122]` FR-87 POST endpoints (step A reassign + step B doc_type) wire to SP audit-field writeback via SpCrud per `[D-064]` + `[D-117]` digest dance. Step C (revision resolution) explicitly NOT routed Ph-1 per `[D-039]` Step 2 + architect direction.
- (i) Dashboard test count 20 -> 37 (+17 new tests covering Gap 1+2+6+7+8).

**Anchors**: FR-57, FR-58, FR-60, FR-61, FR-87, `[D-019]`, `[D-039]`, `[D-064]`, `[D-074]`, `[D-088]`, `[D-114]`, `[D-115]`, `[D-117]`, `[D-119]`, `[D-122]`, `dashboard/MODULE.md`, `sharepoint_integration/sp_client.py` + `list_crud.py`, commit `f8289f2` + commit `2f791d6`.

---

## D-124: `DeliveryState` enum value strings match SP display (PascalCase + spaces) -- direction (α)

**Date**: 2026-06-26
**Status**: Partially superseded by `[D-138]` 2026-06-28 -- the title wording ("PascalCase + spaces") was a typo; the Decision block of this ADR already showed no-space PascalCase values verbatim (`"OutreachSent"`, `"OwnerClosed"`, etc.). D-138 re-codifies the original intent + corrects enum-file drift that landed space-bearing values between ratification and 2026-06-28.

**Context**: HILDA's canonical `DeliveryState` enum (in `core/src/template_schema/enums.py`) originally used SCREAMING_SNAKE_CASE Python attribute names + various value-string conventions (e.g., `OUTREACH_SENT = "OutreachSent"` no-space PascalCase). The SP UI engineer's actual SP Choice column values are PascalCase WITH spaces (e.g., `"Not Started"`, `"OutreachSent"`, `"OwnerClosed"`) per architect's live SP REST probe 2026-06-26 (showed `delivery_state = "Not Started"`). The mismatch meant HILDA's runtime evaluator (rule_engine condition matching) would never fire against real SP data.

**Decision**: Update `DeliveryState` enum **value strings** to match SP display verbatim -- PascalCase + spaces. Python attribute names stay SCREAMING_SNAKE_CASE (Python convention). Direction (α):

```python
class DeliveryState(str, Enum):
    NOT_STARTED            = "Not Started"
    OPEN                   = "Open"
    OUTREACH_SENT          = "OutreachSent"
    DOCUMENT_RECEIVED      = "DocumentReceived"
    OWNER_CLOSED           = "OwnerClosed"
    UNDER_PM_REVIEW        = "UnderPMReview"
    READY_FOR_SUBMISSION   = "ReadyForSubmission"
    SUBMITTED_TO_CUSTOMER  = "SubmittedToCustomer"
    CLOSED                 = "Closed"
    DELAYED                = "Delayed"
    BLOCKED                = "Blocked"
```

Cascade: `customizations/rules/global/automation_rules.yaml` condition values updated to match. 4 test files updated to match.

**Why**:
- (a) Single source of truth -- SP UI engineer's Choice column values are the operational truth (TPMs see + edit them in SP UI). HILDA's enum should mirror, not diverge.
- (b) Matches the same pattern as `[D-094]` SUPERSEDED 2026-06-23 item_type lock -- short-label categories Confirmation/Default PascalCase; long names snake_case. DeliveryState extends the same discipline: enum value strings = SP display strings.
- (c) Direction (α) over (β) translation layer -- a translation layer (`SP_TO_CANONICAL_STATE = {"Not Started": "OPEN", ...}`) at the SP-READ boundary adds an indirection HILDA developers must remember + maintain. Direction (α) removes the indirection entirely.
- (d) rule_engine condition matching works out of the box -- rules can reference `delivery_state in ["OutreachSent", "DocumentReceived"]` and match runtime events directly.
- (e) Avoids future drift between HILDA enum values + SP Choice values -- when SP UI engineer adds a new state (e.g., "Awaiting Approval"), HILDA enum mirrors verbatim; no mapping table sync.

**Consequences**:
- (a) `core/src/template_schema/enums.py` `DeliveryState` value strings updated (commit `f8289f2`).
- (b) `customizations/rules/global/automation_rules.yaml` condition values updated to PascalCase-with-spaces; orphan values OwnerResponseReceived / OutreachReminded / AIReviewed removed in same cascade (commit `057b33d`).
- (c) 4 test files updated for new string values: `test_template_schema.py` + `test_tracker.py` + `test_workflow_engine_tasks.py` + `test_dashboard.py`.
- (d) String values containing spaces require quoting in YAML (`value: "OutreachSent"` not `value: OutreachSent`) -- caught + fixed during cascade.
- (e) `[D-094]` SUPERSEDED + `[D-119]` `tpm_resolved_doc_type` (4-value lowercase snake_case for HILDA-owned page) precedents both confirm: enum value strings = SP-display strings where SP UI is the authoritative surface; canonical lowercase snake_case where HILDA owns full read+write (FR-87 page).
- (f) No translation layer at SP-READ boundary -- HILDA's evaluator reads `row["delivery_state"]` and matches enum values directly.
- (g) Future SP-side Choice value addition: HILDA architect adds the new enum member with matching value string; no broader cascade.

**Anchors**: FR-7, FR-28, `[D-094]` SUPERSEDED, `[D-119]`, `template_schema/enums.py`, `automation_rules.yaml`, commit `f8289f2` + commit `057b33d`.

---

## D-125: Point 3 policy -- corp-SP-derived YAML mappings stay LOCAL; public github gets sanitized placeholder

**Date**: 2026-06-26
**Status**: Ratified

**Context**: The customer.yaml (was mock_customer.yaml) file in `customizations/sharepoint_config/customers/` is the SP-integration column-name translation table (HILDA canonical -> SP internal). Per `[D-027]` Teacher/Student precedent, corp-proprietary content (binding code) stays out of public github. Architect raised 2026-06-26 that this principle also applies to YAML files containing SP column mappings derived from the corp SP probe -- column names + structure derived from the architect's live corp environment shouldn't go to public github even if values use placeholder identifiers, because the column-name set itself reflects corp design decisions.

**Decision**: Three-tier policy for `customizations/` content boundary:
- (a) **Architecture YAML / rule_engine YAML** (HILDA logic; no corp data) -> safe for public github. Examples: `automation_rules.yaml`, `defaults.yaml`. Architect-written.
- (b) **Corp-SP-derived YAML mappings** (e.g., `customer.yaml` SP column maps, future per-customer template overrides reflecting real customer schemas) -> stay LOCAL. Architect maintains the production version on the Linux deployment box. Public github copy is a privacy-clean SAMPLE / RUNTIME PLACEHOLDER with placeholder identifiers (`mock_customer` / MODEL-A / etc.) + header note documenting Point 3 policy.
- (c) **HILDA Python code** (`SpClient` enhancements, dashboard handlers, etc.) -> safe for public github. Corp-API binding bodies filled in by Cline on Work PC per `[D-027]`.

For (b) workflow: Claude generates corrected YAML content in chat -> architect copies/pastes to local + deploys -> Claude does NOT commit corp-derived content to public github. Public github customer.yaml stays as sanitized placeholder showing the YAML SHAPE with placeholder identifiers + header policy note.

**Why**:
- (a) Defense-in-depth on NFR-2 + `[D-027]` -- even sanitized placeholder values can leak corp design through column names + structure (which fields exist, which are Required, what types). Corp SP UI engineer's design choices are reflected in the column inventory.
- (b) Architect-controlled deployment gate -- production YAML lives on the architect's Linux box; architect controls when SP UI engineer changes flow into HILDA runtime. Public github customer.yaml is a Ph-1 reference sample, not a deployment artifact.
- (c) Distinct from architecture YAML -- rule_engine `automation_rules.yaml` is HILDA-architectural (state machine + outreach cadence + escalation logic); not derived from corp data. Stays in github for ADR-style traceability.
- (d) Workflow cleanliness over git-side ACL -- alternative would be a private fork of `customizations/` or `.gitignore` patterns. Both add operational complexity + drift risk. Chat-generation + manual deploy is simpler + already aligns with `[D-027]` Teacher/Student pattern.

**Consequences**:
- (a) `customizations/sharepoint_config/customers/mock_customer.yaml` renamed -> `customer.yaml` (single file per deployment per architect Q1 lock 2026-06-26).
- (b) Header note added: "production customer.yaml lives LOCALLY only (not on public github); architect maintains the corp-SP-derived column-name mappings on the Linux deployment box".
- (c) v1 of customer.yaml (yesterday's Tier 1 P0 YAML batch) had `_x0020_` encoding bugs from Claude's guessing -- left in github as a runtime placeholder; corrected v2 generated in chat for architect's local deployment per Point 3.
- (d) Production customer.yaml on Linux deployment box: incorporates architect's live SP probe column inventory + the post-2026-06-26 corrections (form-factor flags dropped, customer_delivery_credential_id removed, customer_delivery_info added per-row, milestone_id dropped pending SP UI engineer correction, etc.).
- (e) Future corp-derived YAML files (e.g., per-customer `customizations/template_schemas/<customer_id>/template.yaml` with real customer data) follow the same pattern: Claude generates in chat -> architect maintains locally -> public github gets sanitized placeholder.
- (f) Test fixtures stay in public github -- `core/tests/fixtures/sharepoint_config/customers/test_customer.yaml` uses placeholder identifiers; doesn't derive from corp SP; safe for github.
- (g) `[D-027]` precedent honored -- Teacher/Student split extended from Python binding bodies (corp PLM / corp messenger / Google Drive adapters) to corp-SP-derived YAML mappings.
- (h) `customer_adapter/MODULE.md` D13 cascade follow-up (per-row `customer_delivery_info` per architect 2026-06-26) tracked via STATUS Flag -- when applied, the corrected YAML mapping for `customer_delivery_info` column lives on architect's Linux box, not in github.

**Anchors**: NFR-2, `[D-027]`, `[D-038]`, `[D-091]`, `[D-104]`, `[D-122]`, `customizations/sharepoint_config/customers/customer.yaml`, commit `2f791d6`.

---

## D-126: customer_delivery_info per-row + customer_delivery_modality per-customer + CAD-E010 -- closes [D-116] D13 cascade follow-up

**Date**: 2026-06-26
**Status**: Ratified

**Context**: `[D-116]` D13 (2026-06-25) locked the customer_adapter binding API at 8 args with the "Drive root baked in per-customer subclass" pattern (B-α). Architect's live SP REST probe 2026-06-26 surfaced that `customer_delivery_info` (e.g., "drive.google.com") is actually a **per-row column on the Deliverables SP list** -- different items for the same customer can ship to different Drive roots (e.g., per project). The original "binding-baked customer-root" framing therefore needed to be relaxed. `customer_delivery_modality` (e.g., "GoogleDrive") was ALSO modeled per-item in template.yaml + as a required field on `DeliveryItemBase` Pydantic model -- with `"None"` doubling as an upload-gate signal, redundant with FR-80's `no_customer_upload` flag. Architect Q1+Q2+Q3 locks 2026-06-26 (afternoon) resolved both.

**Decision**: Three-part cascade closing `[D-116]` D13 follow-up:

- **(Q1) Binding API: 9th positional arg `customer_delivery_info`** -- per-row value flows through from SP Deliverables row → workflow_engine submission task → `CustomerAdapter.upload_attachment(...)` → `GoogleDriveBaseAdapter._invoke_binding(...)` → user's pre-existing binding. Binding composes the full Drive URL internally per (B-α) preserved: `<customer_delivery_info>/<device_id>/<milestone_name>/<target_dir>/<filename>`. Customer-baked-root framing relaxed -- the per-customer subclass no longer hard-codes the Drive root; it flows in per call. `customer_delivery_credential_id` REMOVED entirely per `[D-019]` shared HILDA ops-team identity (no per-row credential needed). `MockCustomerAdapter.upload_attachment` accepts the kwarg with `"drive.google.com"` default for test ergonomics.

- **(Q2) `customer_delivery_modality` moved from `DeliveryItemBase` (per-item) to `CustomerTemplateBase` (per-customer top-level)** -- one modality per customer (matches the per-customer subclass pattern at `customizations/customer_adapter/<customer_id>_adapter.py`; modality is subclass-implicit at runtime). Field on `CustomerTemplateBase` is `str | None = None`, validated against `CustomerDeliveryModalityRegistry`. Removed from: `DeliveryItemBase` model + validator; `DefaultWorkItemConfig` dict in `tracker/default_workitem.py`; `manual_override.py` overridable field list; template.yaml per-item entries (14 dropped in mock_customer.yaml; equivalent local-paste for MMK per `[D-125]` Point 3). The `customer_delivery_modality = "None"` value was redundant with `no_customer_upload=True` per FR-80; consolidated -- `no_customer_upload` is now sole upload gate.

- **(Q3) `CAD-E010 "customer_delivery_info_missing"`** -- validation in `GoogleDriveBaseAdapter.upload_attachment` step 0: if `customer_delivery_info` is None/empty AND the upload is attempted (i.e., callers passed `no_customer_upload=False` upstream), surface `CAD-E010` with `error_detail="customer_delivery_info_missing"`, emit CommunicationLog row, return. Data-config error -- SP UI engineer must provision the field when upload is expected.

**Why**:
- (a) **Per-row `customer_delivery_info` over baked-in subclass root** -- real-world Drive routing varies per project/milestone within a customer (live SP probe confirmed); baking customer-root in the subclass would lose that flexibility + force re-deployment to add a project.
- (b) **Per-customer `customer_delivery_modality` over per-item** -- modality is bound to which adapter Python class runs (per `[D-116]` D11 per-customer subclass); a single customer never ships SOME items via Google Drive + OTHERS via a different modality in Ph-1. Per-item entries were redundant noise; consolidating saves 14+ YAML lines per template + removes a misleading per-item validation surface.
- (c) **Direction (α): binding 9th arg, NOT HILDA pre-composing the full URL** -- preserves the (B-α) lock from `[D-116]` D13: binding owns Drive-side conventions (folder auto-creation, naming idiosyncrasies, etc.). HILDA flows COMPONENTS; binding composes. Adding a kwarg is the minimum-disruption shape; HILDA pre-composing would require collapsing Model_No + milestone_name + target_dir into a single resolved string + lose the binding's auto-mkdir-on-missing semantics.
- (d) **CAD-E010 strict over silent skip** -- per FR-80, `no_customer_upload=False` means we EXPECT to upload. Missing `customer_delivery_info` at that point is a data-config bug, not "carrier didn't provide destination yet" (use `no_customer_upload=True` for that). Strict error surfaces to ops; silent skip would be invisible.
- (e) **`no_customer_upload` over `customer_delivery_modality = "None"` as upload gate** -- two signals for the same predicate creates dual-truth risk (one says skip, the other says upload). FR-80 names `no_customer_upload` as the gate; modality being null was an accident-of-history. Consolidating removes the dual-truth.
- (f) **Point 3 policy applied to template.yaml cascade** -- `customizations/template_schemas/mock_customer/template.yaml` is sanitized placeholder, safe for public github (14 per-item line deletions + 1 top-level addition committed). `customizations/template_schemas/MMK/template.yaml` is corp-derived; matching transformation generated in chat for architect's local Linux box per `[D-125]` Point 3 policy.

**Consequences**:
- (a) `core/src/customer_adapter/protocol.py` `CustomerAdapter.upload_attachment` Protocol signature: 9 positional args (added `customer_delivery_info`).
- (b) `core/src/customer_adapter/google_drive_base.py` `GoogleDriveBaseAdapter.upload_attachment` + `_invoke_binding` extended; Step 0 CAD-E010 validation added.
- (c) `core/src/customer_adapter/mock_customer_adapter.py` `MockCustomerAdapter.upload_attachment` accepts `customer_delivery_info: str = "drive.google.com"` default + emits CAD-E010 on empty.
- (d) `core/src/customer_adapter/diagnostics_cli.py` `--mock` invocation passes the kwarg.
- (e) `customizations/customer_adapter/example_adapter.py` scaffold subclass `_invoke_binding` updated to 9-arg signature + TODO(cline) docs.
- (f) `core/src/diagnostics/error_codes.py` `CAD-E010` registered (14 CAD codes → 15).
- (g) `core/tests/test_customer_adapter.py` updated for 9-arg + `_SuccessAdapter._invoke_binding` signature + CAD count assertion 14 → 15. 30/30 customer_adapter tests pass.
- (h) `core/src/template_schema/models.py` `customer_delivery_modality` REMOVED from `DeliveryItemBase` + validator; ADDED to `CustomerTemplateBase` as `str | None = None` + validator.
- (i) `core/src/tracker/default_workitem.py` `customer_delivery_modality` key REMOVED from `_DEFAULT_WI_VALUES` dict.
- (j) `core/src/tracker/manual_override.py` `customer_delivery_modality` REMOVED from overridable field list.
- (k) `core/tests/test_template_schema.py` per-item fixture + assertion REMOVED (field no longer schema-enforced on DeliveryItemBase); the `test_customer_delivery_modality_4_values_per_d054` enum value test STAYS (enum unchanged).
- (l) `customizations/template_schemas/mock_customer/template.yaml` -- 14 per-item lines deleted; 1 top-level `customer_delivery_modality: GoogleDrive` added.
- (m) `customizations/template_schemas/MMK/template.yaml` -- matching transformation generated in chat for architect's local paste per `[D-125]` Point 3 (10 per-item lines to delete; 1 top-level to add).
- (n) `customizations/sharepoint_config/customers/customer.yaml` -- `customer_delivery_modality` SP column mapping commented out (no longer per-row on SP; HILDA reads from template.yaml).
- (o) `core/src/customer_adapter/MODULE.md` rollback log: D-126 cascade CLOSED entry.
- (p) `[D-116]` D13 follow-up flag in STATUS.md cleared.
- (q) Test suite 755 → 755 (unchanged net; additive 9th arg + field removal where present + new CAD-E010 test cell + adjusted CAD count assertion).
- (r) **Net code size**: ~80 lines code + ~30 lines MODULE.md + 14 YAML deletions in mock_customer.yaml + 1 YAML addition. Well within architect's original "small ~50-100 lines" estimate from 2026-06-26 morning D13 flag.

**Anchors**: FR-77 (carrier-portal path composition), FR-80 (`no_customer_upload` upload gate), `[D-019]` (shared HILDA ops-team identity Ph-1/Ph-2), `[D-027]` (Teacher/Student split — binding bodies stay local), `[D-085]` (target_date authoritative deadline -- unrelated but in same neighborhood), `[D-088]` (3-tuple PM resolution), `[D-094]` SUPERSEDED (mixed-case enum precedent), `[D-104]` (Projects per-customer), `[D-116]` D11 + D12 + D13 (binding API + B-α lock), `[D-117]` (SpSession digest dance), `[D-119]` (4-value `tpm_resolved_doc_type`), `[D-123]` (dashboard `/docs/{customer_id}/{sp_id}` URL + `$expand` for User fields), `[D-125]` (Point 3 policy -- corp-derived YAML stays LOCAL), `customer_adapter/MODULE.md`, `template_schema/MODULE.md`, `tracker/MODULE.md`, `email_service/MODULE.md`, `customizations/customer_adapter/example_adapter.py`, `customizations/template_schemas/mock_customer/template.yaml`, `customizations/sharepoint_config/customers/customer.yaml`, this commit.

---

## D-127: ops_alerts module -- single ingress for HILDA-internal anomaly/failure signals (1 email + N messenger DMs)

**Date**: 2026-06-26
**Status**: Ratified

**Context**: Across the 2026-06-25 + 2026-06-26 architect-review sessions, multiple signal sites surfaced that needed a destination for HILDA-internal anomaly/failure alerts: ITR-W004 (PLM N-retries-exhausted per issue_tracker Q5 lock 2026-06-25); FR-87 SP audit writeback silent failures (best-effort try/except per `[D-064]` + `[D-117]`); and an open expectation of "more operational signals likely to surface" as Ph-1 dev lands across modules. The accumulated "HILDA OPS alert mechanism" TODO was flagged in STATUS for separate architect-discussion sessions per close-session 2026-06-26 morning. Architect's design lock evening 2026-06-26 chose ONE module to own the alert-emit surface, fanning out to TWO channels.

**Decision**: Create new module `core/src/ops_alerts/` per the standard HILDA module-boundary discipline. Public surface: `OpsAlerts.emit_alert(source, error_code, context, severity) -> OpsAlertResult`. Fan-out shape per architect Q1+Q2+Q3+Q4 locks 2026-06-26:

- **(Q1) Single recipient set; severity surfaces visually**:
  - Channel A: ONE email to `ops_bot_email` (HILDA OPS BOT alias, single SMTP destination) per alert
  - Channel B: N messenger DMs to each `corp_id` in `broadcast_corp_ids` (e.g., `["y.yikilev", "a.john"]`) per alert
  - Same recipient set for all severities (info / warning / error / critical)
  - Subject prefix tag `[<SEVERITY>] HILDA: <source> <error_code>` (plain text per RFC 5322; works on every mail client)
  - HTML body badge color-coded: `red` (critical, error), `orange` (warning), `gray` (info)
  - Messenger DM body = email plaintext part WITHOUT color/badge (per architect "no differentiation in corp messenger communication")

- **(Q2) Rate limiter `int | None`; default null**:
  - `rate_limit_per_minute: null` in `customizations/ops_alerts/recipients.yaml` = no rate limit (Ph-1 default; every call fans out)
  - `rate_limit_per_minute: N` (positive int) = at most N alerts per (source, error_code) tuple per rolling 60-second window
  - Excess alerts return `OpsAlertResult.suppressed_by_rate_limit=True` with no fan-out
  - NO summary alert at end of window (Ph-1 simplicity; deferred Ph-2)

- **(Q3) Recipients config LOCAL per [D-125] Point 3**:
  - File: `customizations/ops_alerts/recipients.yaml`
  - Contains corp_ids → stays on architect's Linux deployment box
  - Public github gets sanitized placeholder showing format only

- **(Q4) ALL HILDA module failure sites wire `emit_alert(...)`** (Ph-1 baseline conservative at known-loud sites):
  - issue_tracker: ITR-W004 + other ITR-EXXX
  - customer_adapter: CAD-E004 / CAD-E005 / CAD-E008 / CAD-E009 / CAD-E010
  - sharepoint_integration: SHP-E001 / SHP-E004
  - workflow_engine: WFE-EXXX task body retry-exhausted
  - rule_engine: rule eval failures
  - tracker: state transition failures
  - storage: NSD failures
  - credential_service: vault/sops failures
  - llm: classifier failures
  - dashboard: FR-87 SP audit writeback silent failures
  - **Excluded** (recursion guard): email_service, messenger, diagnostics (alerts about email/messenger failures cannot use email/messenger; diagnostics is a foundation)

Fire-and-forget API: `emit_alert` MUST NOT raise to the caller -- internal failures (recipients.yaml load error, email send failure, messenger send failure) are caught + recorded in `OpsAlertResult.error_codes` + best-effort logged to local NSD ops-log file. The signal site continues unaffected. New error code prefix `OPS-EXXX` registered in diagnostics: `OPS-E001` recipients_yaml_load_failure; `OPS-E002` email_send_failure; `OPS-E003` messenger_dm_fanout_partial; `OPS-E004` messenger_dm_fanout_total; `OPS-E005` context_payload_too_large; `OPS-W001` rate_limit_suppressed; `OPS-W002` credential_field_redacted.

**Why**:
- (a) **Single dedicated module** over **helper function in diagnostics / email_service / messenger** -- diagnostics is a foundation every module depends on (helper there would invert dep graph); email_service + messenger are channel-owners (asymmetric fit for fan-out). New module isolates fan-out logic + recipients config + rate limiter cleanly. ~150 lines justifies the boundary.
- (b) **Same recipient set for all severities (Q1)** over **severity-tiered routing** -- keeps Ph-1 simple + matches architect's actual ops surface (one BOT email, one broadcast list). Severity-driven routing IS captured in Deferred for Ph-2 if ops surface grows (e.g., dedicated on-call corp_id list for critical-only). Avoids premature complexity.
- (c) **Subject prefix tag + HTML body badge** over **subject-line color** -- RFC 5322 email subjects are plain text; `[<SEVERITY>]` prefix is universal across all mail clients (Outlook / Gmail / Mac Mail / terminal mutt). Subject tag enables inbox-scan + filter rules; HTML body badge enables visual severity recognition on open. Architect approved dual pattern 2026-06-26 evening.
- (d) **Per-(source, error_code) rate limit window** over **per-source** OR **per-(source, error_code, severity)** -- granularity matters: a 50× burst of ITR-W004 is rate-limit-worthy; but if the same source emits a separate ITR-E002 simultaneously, both signals are operationally distinct + both should land. Per-severity adds noise + matches no real operational need.
- (e) **Rolling 60-second window** over **fixed 1-min buckets** -- slightly more complex impl but avoids the "59 alerts at bucket boundary second" pathology. Default null skips this entirely.
- (f) **Fire-and-forget API surface** over **raises-on-failure** -- alerts must NEVER break the system they monitor. `OpsAlertResult` is a debug aid only; callers should NOT branch on it.
- (g) **`emit_alert` accepts plain primitives (str + dict)** over **typed payload objects** -- keeps every caller's import surface tiny (no DeliveryItemBase / Credential / SpClient references); avoids circular-dep risk; bounded `context` dict per NFR-2 also prevents callers from accidentally dumping large objects.
- (h) **Messenger + email_service recursion guard at module level** over **runtime detection** -- design-time guarantee that messenger never imports ops_alerts (enforced via dependency review). Simpler than runtime detection + provably no-recursion.
- (i) **LOCAL recipients.yaml** per `[D-125]` Point 3 -- contains real corp_ids; architect maintains on Linux deployment box; same precedent as customer.yaml + MMK/template.yaml.
- (j) **OPS-E prefix in diagnostics** -- distinct namespace for "the alert channel itself failed"; distinguishes from the alert's PAYLOAD (which carries the source-module's own error code).
- (k) **Sync recipients.yaml load at module construction** over **per-alert reload** -- recipients are stable across deployment lifetime; SIGHUP-triggered reload deferred Ph-2.

**Consequences**:
- (a) **New module dir**: `core/src/ops_alerts/` with `MODULE.md` drafted 2026-06-26 (this commit). Code lands next session per architecture → development phase discipline.
- (b) **Public surface** Ph-1: `OpsAlerts` Protocol, `Severity` enum, `OpsAlertResult` dataclass, `OpsAlertsService` concrete impl, `MockOpsAlerts` test double, `build_ops_alerts(...)` composition helper.
- (c) **Sub-modules** Ph-1: `protocol.py`, `service.py`, `composer.py`, `rate_limiter.py`, `recipients_loader.py`, `mock_ops_alerts.py`, `config.py`. ~150 lines core + ~25 lines mock + ~120 lines tests.
- (d) **New config file**: `customizations/ops_alerts/recipients.yaml` -- LOCAL on architect's Linux box per `[D-125]`; public github gets sanitized placeholder.
- (e) **New error prefix** `OPS-EXXX` registered in `diagnostics/error_codes.py` `PREFIX_REGISTRY` -- 5 errors + 2 warnings Ph-1.
- (f) **Dependency graph additions** Ph-1:
  - ops_alerts DEPENDS ON: email_service + messenger + credential_service + diagnostics
  - issue_tracker / customer_adapter / sharepoint_integration / workflow_engine / rule_engine / tracker / storage / credential_service / llm / dashboard each ADD outbound dep on ops_alerts (10 modules)
  - email_service / messenger / diagnostics do NOT depend on ops_alerts (recursion guard)
- (g) **MAP.md regen** required after Ph-1 dev lands -- new module + 10 inbound edges + 4 outbound edges.
- (h) **Module count** grows: 13 → 14 dev-complete modules + ops_alerts arch-draft (no LLM module yet either).
- (i) **STATUS Flag cleared**: "HILDA OPS alert mechanism" TODO removed from accumulated architect-discussion list (was item 1 of 4 in commit `2f791d6`).
- (j) **Call-site wiring scope** Ph-1: conservative baseline at known-loud sites (Errors only, not Warnings/Info initially) with growth path Ph-2+. Each site is one async call: `await self._ops_alerts.emit_alert(source=..., error_code=..., context={...}, severity=Severity.ERROR)`. No other site change.
- (k) **Test fixture**: `core/tests/fixtures/ops_alerts/test_recipients.yaml` -- placeholder ops_bot_email + 2 broadcast_corp_ids + `rate_limit_per_minute: null`; safe for public github.
- (l) **Deferred items** (Ph-2+ forward-looking): severity-driven routing; per-source recipient overrides; fingerprint-based dedup; summary alert at rate-limit window end; Slack / PagerDuty / SMS channels; SIGHUP-triggered reload; acknowledge / mute workflow; alert correlation; metrics surface.

**Anchors**: NFR-2 (bounded context payload + no proprietary content), `[D-019]` (shared HILDA ops-team identity Ph-1/Ph-2 -- same SMTP + messenger account for OPS BOT), `[D-027]` (Teacher/Student split -- recipients.yaml LOCAL), `[D-064]` (HILDA → SP REST writeback secondary channel -- failed writebacks emit via ops_alerts), `[D-117]` (SpSession NTLM digest dance -- failed digests emit via ops_alerts), `[D-122]` (FR-87 direct POST -- SP audit writeback silent failures emit via ops_alerts), `[D-125]` (Point 3 policy -- recipients.yaml LOCAL), `core/src/ops_alerts/MODULE.md`, this commit.

---

## D-128: sp_alert_parser real-format cascade — actual SP alert subject + body shape

**Date**: 2026-06-25
**Status**: Ratified

**Context**: The pre-2026-06-25 sp_alert_parser implementation per `[D-047]` assumed SP alert email subjects matched the pattern `Alert_<List>_<Suffix> - <ItemTitle>` (with a literal `Alert_` prefix + always-present customer-suffix). Architect's 6 real SP alert screenshots 2026-06-25 (Milestone add/change/delete + Deliverable add/change/delete) revealed the assumption was WRONG: SP alerts have NO `Alert_` prefix, and the Milestones list is GLOBAL (no per-customer suffix) per architect lock 2026-06-21. Body shape was also richer than the pre-2026-06-25 parser handled: header line carries action verb (`<Title> has been added/changed/deleted`); modified fields carry an inline `Edited` marker with a leading `- ` separator (e.g. `owner_corp_id: - t.arasu Edited`); empty body fields appear as bare `key:` with no value. Plus: SP can resend the SAME alert (duplicate Message-IDs), and corp SP fires "changed" alerts that contain NO Edited markers (no-op changes) that should be silently dropped.

**Decision**: Comprehensive cascade applied 2026-06-25 (commit `7bf9e4c`):

- **Subject regex rewrite**: drop `Alert_` prefix; make customer suffix OPTIONAL (Milestones is global, no suffix; Deliverables/Projects are per-customer). New pattern:
  ```python
  r"^(?P<list>Milestones|Projects|Deliverables)(?:_(?P<suffix>[A-Za-z0-9]+))?\s*-\s*(?P<title>.+)$"
  ```
- **Customer_id derivation**: for Deliverables/Projects from subject suffix; for Milestones from body `carrier:` field.
- **Milestone_name derivation**: for Milestones alerts the Title from subject IS the milestone_name (no separate `milestone_name:` body field).
- **Action verb extraction**: parse `<Title> has been (added|changed|deleted)` header line → set `action_type`.
- **Field-delta extraction**: lines with `Edited` suffix marker → populate `TriggerEvent.field_deltas` per `[D-047]` (NEW values only per architect Q5 lock — no extra SP REST roundtrip for OLD values).
- **Body parser rewrite**: line-by-line via `splitlines()` (was multi-line regex; empty fields caused multi-line bleed); empty fields captured as `""` per architect Q3 lock.
- **Message-ID LRU dedup**: bounded set (size 1024, TTL 10 min) per architect 2026-06-25 (corp SP can resend alerts).
- **No-op-change drop**: `changed` action with empty `field_deltas` → silently dropped (no TriggerEvent emitted).
- **Projects Ph-1 drop**: SP UI engineer has not enabled Projects alerts Ph-1 per architect Q1 lock; if such an alert arrives, drop with INFO log (Ph-2 target).

**Why**:
- (a) **Real format observed > old assumption** — architect's screenshots are the operational truth; pre-2026-06-25 regex would silently drop EVERY production SP alert as "out-of-scope" → entire FR-84 + NFR-21 dual-writer + rule_engine ItemModified pipeline was non-functional under the wrong assumption.
- (b) **Field-delta extraction unlocks rule_engine richness** — without per-field deltas, rule_engine rules could only match "an item changed" not "owner_corp_id changed" or "milestone_collection_started_at changed"; with deltas, the NFR-21 dual-writer integration loop works (TPM clicks Start Collection → SP UI engineer writes `milestone_collection_started_at` → SP alert fires with `Edited` marker → HILDA's rule_engine matches `field_deltas_contains: [milestone_collection_started_at]` → fires SEND_INITIAL_OUTREACH). Architect Q4 lock: this is THE point of the alert channel.
- (c) **NEW values only over OLD+NEW** — old-value capture would require extra SP REST roundtrip per alert; alerts arrive in volume; round-trip latency dominates. Architect Q5: Ph-1 acceptable.
- (d) **Per-line body parsing over multi-line regex** — original `\s*$` greedy match consumed `\n` for empty fields and bled into next-line content. Demo: empty `owner_status_note:` captured the subsequent `owner_name: - Thendral Arasu Edited` line as its value. Per-line `splitlines()` parsing eliminates the failure mode.
- (e) **Message-ID dedup over per-alert dedup** — alerts arriving from SP carry RFC 5322 Message-IDs; deduplication on this stable identifier is cheap + effective. Per architect 2026-06-25: corp SP can resend same alert (retry / cluster failover behaviors).
- (f) **No-op-change drop over emit-anyway** — `changed` alert with no `Edited` markers contains no information rule_engine can match against; emitting a TriggerEvent with empty `field_deltas` wastes downstream cycles + pollutes audit. Silent drop with INFO log preserves observability.
- (g) **Projects Ph-1 drop over Ph-1 handling** — SP UI engineer hasn't subscribed Projects alerts in Ph-1; if one does arrive, dropping cleanly avoids surprise pipeline behavior. Architect Q1: Ph-2 target — TPM project changes are rare.

**Consequences**:
- (a) `core/src/email_service/sp_alert_parser/parser.py` rewritten (~280 lines vs original ~140) -- new `_BODY_KV_LINE_RE` per-line parser; `_ALERT_SUBJECT_RE` new pattern; `_ACTION_VERB_RE` header parser; `_EDITED_SUFFIX_RE` + `_LEADING_DASH_RE` value cleanup; `_MessageIdDedup` LRU class.
- (b) `core/src/email_service/inbound/classifier.py` `SP_ALERT_SUBJECT_RE` updated to match the same new pattern -- classifier dispatch unchanged.
- (c) `core/src/email_service/MODULE.md` narrative rewritten for real format + Q1-Q5 + 2 operational guards; 3 stale subject-format mentions corrected.
- (d) 14 new regression test cases in `test_email_service.py::TestSpAlertParserRegression` (exact bodies from architect's 4 screenshots: M-add, M-change, M-delete, D-add, D-change, D-delete; plus dedup + no-op-drop + Projects-drop + edited-value-cleanup + action-verb cases). Plus 4 existing tests updated to new format.
- (e) `ParsedSpAlert.action_type` now carries `"added"` / `"changed"` / `"deleted"` (was `None` before); `ParsedSpAlert.field_deltas` new field carrying the `Edited`-marker subset of body_kvs.
- (f) `SpAlertParser` constructor accepts optional `dedup_max_size` + `dedup_ttl` kwargs for test injection.
- (g) Tests: 797 → 812 (+15 net).

**Anchors**: `[D-047]` (SP alert email channel + TriggerEvent field_deltas), `[D-118]` (SP UI engineer provisioning boundary), `[D-122]` (FR-87 direct POST architecture -- FR-87 step A/B handlers removed earlier today), FR-84 (SP→HILDA inbound email channel), NFR-21 §5 (HILDA + SP UI dual-writer for `milestone_collection_started_at` + similar runtime timestamps -- THIS cascade's primary motivator), `core/src/email_service/sp_alert_parser/parser.py`, `core/src/email_service/MODULE.md`, commit `7bf9e4c`.

---

## D-129: Podman selected over Docker for Ph-1 deployment runtime

**Date**: 2026-06-25
**Status**: Ratified

**Context**: HILDA Ph-1 deployment per `[D-026]` targets Docker Compose on a single bare-metal Linux PC (6 containers: hilda-api + hilda-worker + hilda-beat + hilda-llm-gateway + postgres + redis). The "Docker" in `[D-026]` was generic shorthand for "OCI-compatible container runtime + compose-syntax orchestration" -- the specific runtime was undetermined pending corp-environment validation. Architect's corp Linux box validation 2026-06-25 (Ubuntu 24.04.4 LTS, omadm-HP-Z640-Workstation) revealed two operationally decisive corp-environment behaviors: (a) **Docker CE was installable AND ran the daemon** but FAILED at `docker run --rm hello-world` with connection-reset-by-peer on `auth.docker.io` (Docker's anonymous token endpoint behind Cloudflare `172.64.144.78`); the corp firewall has TLS-fingerprint-based rules that block Docker's specific request pattern but PERMIT Podman's pattern; (b) **Podman 4.9.3 from Ubuntu noble apt repos installed cleanly without sudo on subsequent ops + completed all 8 smoke tests (hello-world, multi-container compose stack with postgres + redis service-name DNS, outbound HTTPS to corp SP returning expected NTLM challenge, Selenium-controlled Chromium loading example.com + google.com)**. Architect also asked whether other runtimes (containerd+nerdctl, K3s, OpenShift, LXD, native systemd) were viable alternatives -- none preferred over Podman for Ph-1's small 6-container scale per `[D-026]` (K8s is `[D-021]` Ph-3+ target).

**Decision**: Podman 4.9.3 + podman-compose 1.0.6 selected as Ph-1 + Ph-2 deployment runtime. `[D-026]` updated to read "OCI runtime: Podman" in place of generic "Docker". `deploy/docker-compose.yml` syntax stays standard compose v3.x format -- runtime-interchangeable at the file level; Docker remains a fallback if corp firewall policy on `auth.docker.io` is lifted or if alternative image registry is provisioned.

**Why**:
- (a) **Validated empirically in corp env** -- Docker hits a corp firewall block that Podman doesn't (today's commit `[no commit, post-session validation]`). Production deployments need reliable image pulls; this isn't a "Docker is slower" trade-off, it's a "Docker can't pull images" hard failure.
- (b) **Rootless by default → IT approval delta** -- Podman's daemon-less rootless model requires no `docker` group membership, no privileged background service, no special user-namespace setup. Corp security teams typically approve Podman faster than Docker for this reason.
- (c) **systemd-native integration** -- `podman generate systemd` emits unit files directly; matches HILDA's `[D-026]` Ph-1/Ph-2 ops pattern ("`git pull` → `sops --decrypt` → start service"). Docker's `docker-compose` wrapper service is an extra layer.
- (d) **Same OCI image format + same compose syntax** -- Phase D1 Dockerfile + docker-compose.yml are 100% interchangeable between Podman and Docker. Runtime swap (e.g., Phase D3+ to MicroK8s per `[D-021]`) doesn't require rewriting compose files; only orchestrator binding changes.
- (e) **Selenium + Chromium validated identically** -- architect's flagged concern about Chromium-sandbox-in-rootless-Podman was retracted today: Selenium-controlled Chromium in rootless Podman works identically to Docker rootful (both need `--no-sandbox` Chromium flag; that's container-paradigm-level, not runtime-specific). See D-130 for Debian-base requirement.
- (f) **No daemon = no privileged background service** = smaller attack surface in a corp env that's already showing it cares about Docker-specific traffic patterns. If a HILDA worker container is later compromised, blast radius is limited to `omadm` user not root.
- (g) **`podman-compose` is third-party Python (caveat acknowledged)** -- not Red Hat's own tooling; less battle-tested than Docker Compose v2. Today's smoke test validated the exact pattern HILDA uses (healthcheck-gated startup, service-name DNS, container-to-container TCP); track upstream `podman-compose` issues if encountered. Phase D3+ migration to MicroK8s per `[D-021]` retires this caveat (K8s = native compose-equivalent via Kustomize / Helm).
- (h) **No alternative runtime was materially better** -- containerd+nerdctl is technically valid but adds no benefit over Podman for HILDA. LXD/Incus is a different paradigm (system containers) requiring HILDA architectural rewrite. K3s/Minikube is skip-ahead Ph-3+ per `[D-021]`. systemd-nspawn is too low-level (no compose ecosystem). Native systemd services (Plan B) is ~30% more ops work + retains all Docker/Podman concerns minus the container isolation benefit.

**Consequences**:
- (a) `[D-026]` updated: "OCI runtime: Podman (corp-environment validated 2026-06-25)" replaces "Docker Compose".
- (b) Phase D1 Dockerfile content authoring -- targets either runtime; commit messages + ops runbooks reference Podman commands.
- (c) `deploy/MODULE.md` (Phase D1) includes operations runbook for `podman build` / `podman-compose up -d` / `podman generate systemd` workflows.
- (d) sops decryption + bind-mount + systemd integration documented for Podman first; Docker fallback noted.
- (e) No code change to HILDA modules -- all runtime-agnostic.
- (f) Corp IT engagement: any future Docker-runtime adoption requires resolving the `auth.docker.io` firewall block first (corp registry mirror OR allowlist rule for Docker traffic pattern).
- (g) Phase D2 (architect-led integration on Linux box) uses Podman exclusively unless corp policy changes.
- (h) Phase D3+ migration target per `[D-021]` MicroK8s + Helm -- Podman remains valid bridge runtime; K8s eventually subsumes Podman's role.

**Anchors**: `[D-021]` (MicroK8s Ph-3+ target), `[D-022]` (Celery broker per worker shape), `[D-024]` (CI/CD pipeline shape with Helm chart Ph-3+), `[D-025]` (3-tier config: CLI > env > config/<module>.json), `[D-026]` (Docker Compose 6-container deployment -- now updated: "OCI runtime: Podman"), `[D-038]` (sops-encrypted credentials), `[D-019]` v1 (shared HILDA ops-team identity), `core/src/email_service/MODULE.md` (Ph-1 single-bare-metal-Linux topology assumption), validation logs from corp Linux box (2026-06-25 Ubuntu 24.04 / omadm-HP-Z640-Workstation), this session's smoke-test results.

---

## D-130: HILDA worker container base = Debian (`python:3.11-slim-bookworm`); Chromium binary at `/usr/lib/chromium/chromium`

**Date**: 2026-06-25
**Status**: Ratified

**Context**: HILDA's `customer_adapter` per `[D-116]` D11-D14 uses the architect's selenium-backed Google Drive binding (Chromium driven via Selenium WebDriver). For the binding to run inside the `hilda-worker` container per `[D-026]` 6-container architecture, the container image must include Chromium + chromedriver. Architect validated container Chromium 2026-06-25 with a Selenium sanity test (sp_alert_parser cascade testing methodology) on the corp Linux box (Ubuntu 24.04.4 LTS). **Two surprising discoveries**: (a) Ubuntu 24.04 noble's `chromium-browser` + `chromium-chromedriver` apt packages are **snap-only transitional stubs** -- they install metapackage stubs that depend on snapd being available in the host, BUT snapd is NOT available inside containers; result: `chromedriver` starts but Chromium binary doesn't actually exist; `SessionNotCreatedException: chromedriver unexpectedly exited`. (b) Switching base image to Debian bookworm (`python:3.11-slim-bookworm`) which ships REAL .deb Chromium fixed the install BUT introduced a second discovery: Debian's `apt install chromium` places the binary as `/usr/bin/chromium` (a 5KB shell wrapper) + `/usr/lib/chromium/chromium` (the 273MB actual ELF binary). chromedriver's binary-existence check rejected the wrapper; explicit `binary_location = "/usr/lib/chromium/chromium"` in Selenium options resolved it. After both fixes, Selenium-controlled Chromium loaded `example.com` (title="Example Domain") + `www.google.com` (title="Google") successfully from inside rootless Podman.

**Decision**: Phase D1 `Dockerfile.hilda-worker` base = `python:3.11-slim-bookworm` (Debian 12). Chromium dependencies via `apt install chromium chromium-driver`. Selenium config in customer_adapter binding sets `opts.binary_location = "/usr/lib/chromium/chromium"` (the real binary, NOT the `/usr/bin/chromium` wrapper). Selenium standard container flags: `--headless=new`, `--no-sandbox`, `--disable-gpu`, `--disable-dev-shm-usage`. Same base applies to `hilda-llm-gateway` if it ships any browser-based component.

**Why**:
- (a) **Empirically validated** -- the exact two-discovery cascade above was the actual test cycle today; jumping straight to "use Debian + point to real binary" saves a Phase D2 deployment-time surprise.
- (b) **Ubuntu 24.04 snap-transition is not reversible** -- Canonical's policy for Ubuntu 24.04+ is that desktop apps like Chromium move to snap-only packaging. There is no "real .deb chromium for Ubuntu noble" via official channels; PPA workarounds (e.g., third-party Chromium PPAs) add corp-trust questions. Debian's policy of shipping real .deb is operationally simpler.
- (c) **Debian bookworm is conservative + stable** -- HILDA's container base should prioritize predictability over OS feature recency. `python:3.11-slim-bookworm` from official Docker Hub library is widely deployed.
- (d) **Direct path to real binary over wrapper** -- the wrapper at `/usr/bin/chromium` sets up env vars + execs the real binary. Selenium's chromedriver binary-existence check rejected the wrapper as a non-binary. Pointing to `/usr/lib/chromium/chromium` directly bypasses the issue.
- (e) **Selenium standard flags over Selenium Manager** -- Selenium 4.x's bundled Selenium Manager auto-downloads matching chromedriver but requires outbound network access at runtime (problematic in air-gapped Phase D3+ deploys); explicit apt-installed chromedriver + matching Chromium is reproducible at image-build time.

**Consequences**:
- (a) `Dockerfile.hilda-worker` Phase D1 base = `python:3.11-slim-bookworm`. apt installs include `chromium chromium-driver ca-certificates fonts-liberation`.
- (b) `Dockerfile.hilda-api` + `Dockerfile.hilda-beat` Phase D1 -- same base for consistency unless they have NO browser need; for now standardize on bookworm.
- (c) `customer_adapter/example_adapter.py` scaffold + actual production binding (LOCAL per `[D-027]`) must configure Selenium with `opts.binary_location = "/usr/lib/chromium/chromium"`.
- (d) Image size: ~400 MB base + ~270 MB Chromium = ~670 MB per worker image. Acceptable Ph-1; revisit Ph-3+ if multi-customer concurrent scaling demands smaller worker images.
- (e) Selenium WebDriver version pinned via apt -- on each Debian release upgrade, Chromium + chromedriver versions update together (apt guarantees matched pair).
- (f) No code change to HILDA modules -- this is a Phase D1 Dockerfile concern.
- (g) When the architect's actual selenium-backed binding code is filled in by Cline per `[D-027]` Teacher/Student on Work PC, the binding's Selenium config must match this binary_location convention.
- (h) Phase D3+ migration target per `[D-021]` MicroK8s + Helm chart -- container base image choice carries forward unchanged; Helm just orchestrates the same OCI image.

**Anchors**: `[D-021]` (MicroK8s Ph-3+ target -- base image unchanged), `[D-026]` (Docker Compose 6-container deployment), `[D-027]` (Teacher/Student split for proprietary binding code), `[D-038]` (sops-encrypted credentials -- separate path), `[D-054]` (browser automation per [D-054] impl note 2026-06-05 -- Chromium binary requirement; per `[D-116]` D17 now binding-side concern), `[D-116]` D11-D17 (selenium-backed Google Drive binding via thin wrapper), `[D-129]` (Podman runtime -- this decision applies regardless of Podman/Docker choice; runtime-agnostic), validation logs from corp Linux box (2026-06-25 Selenium sanity test).

---

## D-132: EWS adapter for `email_service` -- corp Exchange wire path with mode discriminator

**Date**: 2026-06-25
**Status**: Ratified

**Context**: HILDA `email_service` ships an IMAP receiver + SMTP sender pair per `[D-016]` for the mailbox channel. Phase D2 first bring-up on the corp Linux box (`omadm-HP-Z640-Workstation`) confirmed that **corp Exchange has IMAP/SMTP DISABLED at the server**. The only mail wire path available on-prem is **Exchange Web Services (EWS)**. Architect colleague Chaitanya Kamsu provided a validated 216-line `ExchangeMailService` reference using `exchangelib` (`Configuration(credentials, service_endpoint, auth_type='basic', version=Version(Build(15,2)))` + `Account(primary_smtp_address, autodiscover=False, access_type=DELEGATE)` + `account.inbox.filter(Q(...))`). `Build(15, 2)` confirms current corp Exchange Server. Per architect 2026-06-25 lock: `[D-016]` "rejected EWS" rationale (Graph API external surface, NFR-1) does NOT apply to on-prem EWS -- Graph is cloud SaaS; EWS is on-prem SOAP. The two paths must coexist (non-corp + dev + mock deployments stay IMAP/SMTP; corp deployment switches to EWS) without forking the `email_service` Public surface.

**Decision**: Add `EwsReceiver` + `EwsSender` adapter pair conforming to the existing `EmailReceiver` + `EmailSender` Protocol surfaces (no Protocol changes); pick at runtime via `EmailServiceConfig.mode: Literal["imap_smtp", "ews", "mock"]` discriminator with factory functions `build_receiver(cfg, cred)` + `build_sender(cfg, cred)` in `email_service/__init__.py`. Library: `exchangelib>=5.2,<6` lazy-imported inside `_fetch_sync` / `_send_sync`. Authentication: basic-over-TLS with a service account (`autodiscover=False`; explicit `service_endpoint` URL). Inbound mechanism: **polling Ph-1** at `EwsConfig.poll_interval_s` (default 60s); EWS streaming notifications deferred Ph-1 next pass. Attachment write-to-disk does NOT happen at the receiver (in-memory bytes on `InboundAttachment.content`; FR-86 storage matrix is the legitimate landing site).

Per architect Q1-Q4 locks 2026-06-25:
- **Q1**: coexist (mode discriminator) over replace (drop IMAP/SMTP code)
- **Q2**: `exchangelib` over raw SOAP / `suds-jurko` (matches Chaitanya proven sample)
- **Q3**: polling Ph-1 over streaming (lower latency benefit does not justify long-lived TCP + reconnect-retry complexity at this scale; FR-23 deadline-tiered polling cadence already covers Ph-1)
- **Q4**: basic auth + service account (current Exchange server supports this; OAuth deferred Ph-2)

**Why**:
- (a) **Corp environment forces EWS** -- IMAP/SMTP unavailable at the server. Not a preference; a constraint.
- (b) **NFR-1 still honored** -- on-prem EWS is internal SOAP, not external SaaS. `[D-016]` Graph rejection rationale does not extend to EWS.
- (c) **Mode discriminator preserves flexibility** -- non-corp deployments (mock harness, future cloud / SaaS environments, future non-Samsung customers) keep IMAP/SMTP support. Single email_service module serves multiple deployment archetypes.
- (d) **Protocol surfaces unchanged** -- `EmailReceiver` + `EmailSender` are wire-format-substitutable. Downstream callers (`workflow_engine` ActionKinds, sp_alert_parser, attachment_router, composers) do not see the swap. Tests for the downstream path keep using `MockImapReceiver`/`MockSmtpSender` regardless of production mode.
- (e) **exchangelib over raw SOAP** -- Chaitanya corp-validated pattern uses exchangelib; mirroring it avoids re-deriving 5+ years of EWS-quirk handling. Library has explicit Build/Version helpers + correct DELEGATE semantics + native attachment + filter Q-object support. Raw SOAP would be 3x the line count + harder to maintain.
- (f) **Polling over streaming Ph-1** -- streaming notifications (`exchangelib.SyncFolderItems`) give sub-second latency but require long-lived TCP + retry handling on connection drops + SOAP fault recovery. Polling at 60s is sufficient for HILDA owner-reply turn-around (most replies expected hours/days). Streaming is a Ph-1 next pass enhancement when latency justifies the complexity.
- (g) **Basic auth + service account Ph-1** -- Exchange Server supports basic auth; service account in `customizations/credentials/email.sops.yaml` per `[D-038]`. OAuth deferred Ph-2 because (i) basic works today, (ii) OAuth setup requires Exchange admin coordination for the service account app registration, (iii) Chaitanya pattern is basic -- match the validated approach.
- (h) **Lazy import** -- `exchangelib` is a heavy dependency (transitively depends on `lxml`, `dnspython`, `tzdata`, `cached_property`, `oauthlib`); non-EWS deployments should not pay the install + import cost. Wrapped in `try: from exchangelib import ...` inside `_fetch_sync` + `_send_sync`; raises `EML-E009` on missing dependency.
- (i) **No `@retry` decorator on `_send_sync`** -- `workflow_engine` ActionKinds are the source of truth for retries per `[D-022]`. Double-layered retry (decorator + workflow_engine retry policy) is anti-pattern: retries multiply, audit ambiguity, observability fog.
- (j) **`EwsConfig.fetch_limit` bounds query size** -- `account.inbox.filter(...)[:N]` slice caps each poll exchangelib query (default 50). Prevents pathological backlog drain.

**Consequences**:
- (a) New file `core/src/email_service/inbound/ews_receiver.py` (~280 lines): `EwsReceiver` class + `_fetch_sync` + `_mark_sync` + `_to_inbound`.
- (b) New file `core/src/email_service/outbound/ews_sender.py` (~140 lines): `EwsSender` class + `_send_sync`.
- (c) `core/src/email_service/config.py` adds `EmailMode` Literal type + `EwsConfig` Pydantic model + `EmailServiceConfig.mode` field (default `"imap_smtp"` for backward compat) + `EmailServiceConfig.ews` sub-config.
- (d) `core/src/email_service/__init__.py` adds factory functions `build_receiver(cfg, cred)` + `build_sender(cfg, cred)` that dispatch on `cfg.mode`. `mode == "mock"` raises `ValueError` to surface mis-wiring early (mock harness wires `MockImapReceiver` / `MockSmtpSender` directly).
- (e) `core/src/email_service/mocks.py` adds `MockEwsReceiver` + `MockEwsSender` for shape-parity testing (same Protocol surface; in-memory fixtures).
- (f) `requirements.txt` adds `exchangelib>=5.2,<6`.
- (g) New error codes `EML-E008` (EWS auth rejected) + `EML-E009` (EWS transport failure) registered in `core/src/diagnostics/error_codes.py`.
- (h) Tests: `core/tests/test_email_service.py` gains `TestEwsReceiver` (8 tests) + `TestEwsSender` (4 tests) + `TestAdapterFactory` (3 tests) + `TestMockEws` (3 tests) + 3 new `TestConfig` cases (ews defaults + mode switch + invalid-mode rejection). Total +20 tests; suite goes from 798 -> 818.
- (i) `core/src/email_service/MODULE.md` Sub-modules tree + Key choices (`[D-016]` partially superseded) + Error codes section updated; status header notes the 2026-06-25 evening EWS landing.
- (j) Phase D2 deployment env (`config/email_service.json` on corp Linux box) can now flip `"mode": "ews"` + populate `EwsConfig.service_endpoint` + `primary_smtp_address` once IT provisions the EWS service account / shared mailbox (carry-forward in STATUS Flag).
- (k) Streaming notifications + OAuth + multi-tenant Exchange Online deferred Ph-1 next pass / Ph-2 (one-line entries in `email_service/MODULE.md` Deferred section).
- (l) `[D-016]` Key choices entry marked PARTIALLY SUPERSEDED for corp Exchange path (IMAP/SMTP retained for non-corp + dev + mock; mode picks at runtime).
- (m) No SP UI changes; no FR additions; no downstream module changes (workflow_engine + sp_alert_parser + attachment_router + composers all consume the Protocol surface unchanged).

**Anchors**: `[D-016]` (IMAP/SMTP partially superseded), `[D-022]` (workflow_engine retry source-of-truth), `[D-025]` (3-tier config + hot-reload), `[D-038]` (sops-encrypted credentials), `[D-107]` (credential_service scope-aware; EMAIL stays SHARED), Chaitanya Kamsu `ExchangeMailService` reference (validated against current corp Exchange Server), NFR-1 (no SaaS LLM / external surface; on-prem EWS honors), NFR-2 (no credential material in logs; per-call credential resolution), FR-9 / FR-10 / FR-12 / FR-23 / FR-24 (mailbox channel callers; Protocol-substitutable), `core/src/email_service/MODULE.md` Key choices, commits from 2026-06-25 evening Phase D2 + EWS landing session.
                          timeout_s: float | None = None) -> FileRef: ...
    async def react(self, ref: MessageRef, emoji: str,
                    timeout_s: float | None = None) -> None: ...
    async def register_webhook(self, callback_url: str, secret: str,
                               timeout_s: float | None = None) -> WebhookRef: ...
    async def poll_inbound(self, since: datetime,
                           timeout_s: float | None = None) -> AsyncIterator[Message]: ...  # webhook-fallback
```

Data classes: `MessageRef`, `Message`, `FileRef`, `WebhookRef`, `AttachmentInput` (shared with `IssueTracker` per `[D-008]`) — Pydantic; `source_system: str` on every reference. Error model: `MessengerError(code, context, cause)` with prefix `MSG-` per `[D-002]`; codes include `MSG-E001 unauthorized`, `MSG-E002 channel_not_found`, `MSG-E003 message_too_large`, `MSG-W001 rate_limited`. Idempotency keys on all sending methods.
**Why**: Same async-over-sync rationale as `[D-008]`. Inbound channel symmetry — webhook + polling pair so adapters work whether the proprietary system pushes or only allows pull. Rule engine and `CommunicationLog` ingestion are Protocol-agnostic — they consume `AsyncIterator[Message]` whichever way it's produced.
**Consequences**: `core/src/messenger/__init__.py` exports Protocol + data classes. `core/src/messenger/messenger_cli.py` per `[D-005]`. `customizations/messenger/<proprietary>_adapter.py` is Ingestor-generated per `[D-003]`. v1 messenger choice (proprietary first vs. public reference first) remains an Open question in PROJECT.md. Error-code prefix `MSG-` registered in central `error_codes.py` per `[D-002]`. Webhook secret rotation Deferred for v1.

---

## D-010: Excel / Template Schema Ingestor — proprietary customer-template schemas processed on-prem
**Status**: Active · **Date**: 2026-04-30
**Decision**: HILDA's customer deliverable templates (Devices → Milestones → Deliverables → DeliveryItems with customer-specific column structures, field extensions, validation rules, enumerated values, and customer-specific automation-rule parameters) carry **proprietary schema variations** that cannot be shared with the dev LLM. A new `core/src/template_schema_ingestor/` module — parallel pattern to the API Spec Ingestor `[D-003]` — runs on-premises, reads the proprietary Excel schema spec, runs an on-prem open-source LLM (Gemma3:12b / Qwen / configurable per `[D-007]`), and emits into `customizations/template_schemas/`: (a) Pydantic validators for each customer's template shape; (b) Excel parsers / column mappers for the customer's Excel template format; (c) SharePoint-List column-mapping configs feeding `[D-004]`'s `customizations/sharepoint_config/`; (d) customer-specific `AutomationRules` configurations consumed by the runtime rule engine. The Ingestor exposes a diagnostic CLI per `[D-002]` emitting compact RPT / MET / QC reports the dev pastes into chat to debug ingestion / generation issues without exposing the schema. **Hard invariant: the dev LLM (Claude) never reads proprietary customer-template schemas, never sees their content via tool calls, and does not request, summarize, or paraphrase their structure.** It works only with: the generic meta-schema (the standard Device / Milestone / Deliverable / DeliveryItem entity hierarchy from `HILDA_Design.md` §3 — public, in design-inputs); the Ingestor's compact diagnostic output (no schema content); and the generated artifacts already committed under `customizations/template_schemas/`.
**Why**: Customer-specific template schemas reveal customer process structure, customer-specific certification requirements, internal R&D taxonomy, and customer-specific automation triggers — all corporate IP. The pattern from `[D-003]` (intermediate primitives + on-prem code-generated artifacts) extends naturally: Claude works with the public meta-schema and writes the Ingestor; the Ingestor processes proprietary customer schemas on-prem and produces concrete artifacts in `customizations/`. Vs. asking PMs to manually write Pydantic validators per customer: doesn't scale, drifts from Excel templates, requires Python skills outside the PM team. Vs. embedding customer schemas in `core/`: violates the no-proprietary-content invariant and couples the codebase to per-customer corporate IP.
**Consequences**: Three module classes per template-customer integration — (1) generic meta-schema in `core/src/template_schema/` (public, defines the Device/Milestone/Deliverable/DeliveryItem entity types as Pydantic base models); (2) the Ingestor at `core/src/template_schema_ingestor/` (its code is not proprietary; only its inputs are); (3) per-customer concrete schema + validators + parsers + automation-rule configs at `customizations/template_schemas/<customer>/`, generated by the Ingestor. The runtime workflow engine (Temporal activities, rule engine, SharePoint integration) reads from `customizations/template_schemas/` at startup and parameterizes itself per active customer. The Ingestor's input format (Excel cell layout convention for the schema spec — column listings, enumerated values, validation rules, automation-rule overrides) is TBD architecture phase; document the convention in `core/src/template_schema_ingestor/MODULE.md`. `template_schema_ingestor_cli.py` ships per `[D-005]` with `--diagnostic`, `--mock`, `--dry-run`. Error-code prefix (likely `TSI-` for Template Schema Ingestor) registered in central `error_codes.py` per `[D-002]`. The `[D-003]` development.md invariant — dev LLM refuses to engage with proprietary spec content if pasted into chat — extends to proprietary template schema content (any customer-specific column listings, field extensions, automation-rule definitions). The Excel-import path mentioned by PM team leads in Topic 3 (template authoring via SharePoint UI + Excel) is fed by this Ingestor: PMs upload Excel templates whose structure conforms to the per-customer schema generated by the Ingestor at deployment time.

---

## D-011: Test Report Document Profiler — proprietary historical reports processed on-prem
**Status**: Active · **Date**: 2026-05-01
**Decision**: A new on-prem module `core/src/test_report_profiler/` ingests historical proprietary test reports across mixed file types — Excel (`xlsx`, `xls`, `xlsm`, `csv`), Word (`doc`, `docx`), and PDF — and emits per-customer parsers + classification artifacts into `customizations/test_report_parsers/<customer>/`. The Profiler runs an open-source LLM on-prem (Gemma3:12b / Qwen / configurable per `[D-007]`) to extract: where the per-item status table lives in the report (sheet/range/headers for Excel; section/heading for Word; page region for PDF); the customer's status vocabulary mapped to the canonical enum `{passed, failed, non-applicable, waived, not-started}`; item-id and item-name conventions; waiver-reference detection conventions (column or comment-text pattern). Generated runtime artifacts are deterministic Python parsers (no runtime LLM): per-file-type parsers emitting `(item_id, status, [waiver_ref], [comment])` tuples plus a `final | interim` classifier. Canonical classification rule (owned in `core/src/test_report/`, not the Profiler): a report is **`final`** iff every item is in `{passed, non-applicable, waived}` AND every `failed` item carries a `waiver_ref` (which reclassifies it as `waived`); otherwise the report is **`interim`**. Waiver outcomes are out of scope for the classifier — they live in the separate Waiver DeliveryItem lifecycle; the TPM (Technical Project Manager) is not the final authority on waiver path resolution.
**Why**: Customer test-report formats are proprietary — their structure reveals customer process, certification taxonomy, and R&D classification. Same air-gap rationale as `[D-003]` and `[D-010]`. Vs. hand-written per-customer parsers: doesn't scale and drifts when customers update formats. Vs. one generic parser: formats vary too much (different file types, different status vocabularies, different waiver-marking conventions). Vs. runtime LLM classification: latency, determinism, and the on-prem-data invariant all push the LLM call to build time; runtime stays deterministic. Third instance of the on-prem Ingestor / Profiler pattern.
**Consequences**: Three module classes per customer test-report integration — (1) `core/src/test_report/` — generic classifier interface, canonical status enum, classification rule, runtime entry point; (2) `core/src/test_report_profiler/` — the Profiler (its code is `core/`-eligible; only its inputs are proprietary); (3) `customizations/test_report_parsers/<customer>/` — generated per-customer parsers + format adapters (one per file type) + customer-specific status-vocabulary maps. The Profiler ships `test_report_profiler_cli.py` per `[D-005]` with `--diagnostic`, `--mock`, `--dry-run`; compact RPT/MET/QC reports emit only counts (items profiled, status enum size, waiver-ref detection rate, format adapter dispatch counts) — no proprietary content. Error-code prefixes: `TRP-` (Profiler), `TRC-` (runtime classifier). Dev LLM never reads historical test reports — the development.md invariant from `[D-003]` / `[D-010]` extends to historical test reports; Claude refuses to engage with pasted test-report content. v1 input scope: all five Excel variants + Word `doc/docx` + PDF; one input adapter per file type within the Profiler. Per-file-type generated parsers land at `customizations/test_report_parsers/<customer>/<format>_parser.py`.

---

## D-012: Multi-item email status updates — three-path design with BATCH-id idempotency
**Status**: Active · **Date**: 2026-05-01
**Decision**: Outbound email outreach for delivery items consolidates all items owned by the same recipient into one message per round, identified by a stable `BATCH-<id>`. Inbound replies route to the correct DeliveryItems via three convergent paths, all keyed on the BATCH id: (a) **Structured reply block** in the body, anchored on machine-readable markers (`========== HILDA STATUS UPDATE ==========`, `BATCH: <id>`, `========== END HILDA UPDATE ==========`) that survive quote prefixes, mobile font / whitespace mangling, HTML stripping, and top-posting; owner edits status tokens (`OPEN` → `DONE | OPEN | DELAYED | BLOCKED`) and optional `comment[N]:` lines in place; parser is regex-only over extracted text. (b) **Per-item `mailto:` tap-links** at the bottom of the same email — `mailto:hilda-inbox@company.com?subject=[HILDA] BATCH-<id> ITEM-<n> <STATUS>`; tap pre-composes a tiny email; subject parser routes. (c) **PM manual triage** — free-text replies that match neither parser are recorded as comments on every item in the batch and surface a `Manual triage` flag on the PM dashboard. Status applies are idempotent on `(BATCH-id, item-index, status)`. Outbound is multipart/alternative (HTML + plaintext); the structured block in plaintext is ASCII-only. LLM-based inference for free-text replies is deferred (DEF-1).
**Why**: Recipients are external (different org / location), so no SharePoint web-form URL is reachable to them — status capture must work over email. Per-item emails are out (recipient spam). Single consolidated email + multi-item inline editing is the only viable shape. The hardest surface — disambiguating which item a free-text edit refers to — is sidestepped: structured paths give deterministic capture; free-text falls back to PM triage rather than ambiguous auto-update. Vs. machine-readable footer only (`ITEM-A=Done; ITEM-B=Open`): owners often don't preserve the format on reply (top-posting, mobile clients, quoting); empirically high error rate. Vs. structured-block only: no mobile-friendly shortcut. Vs. one email per item: spams recipients. Vs. LLM classification at runtime: pushed to v2 (DEF-1) to keep v1 deterministic and on-prem-only.
**Consequences**: Email Service module owns BATCH-id assignment, outbound template generation (HTML + plaintext alternatives), inbound parser with three-path dispatch, and idempotency cache keyed on `(BATCH-id, item-index, status)`. Rule engine outbound emits per-recipient batches, not per-item; reminder cadence is per-batch, not per-item. Dashboard owns the `Manual triage` surface for free-text replies. ASCII-only constraint on the structured block (no unicode arrows / box-drawing) — parser robustness > formatting polish. Subject format `[HILDA] BATCH-<id> — Status update needed: <N> items` preserved as a secondary anchor across `Re:` chains. Negative tests required: format-break tolerance (quote prefixes, whitespace collapse, HTML→text conversion), idempotency under duplicate replies, no-status-applied-on-mismatch under mangled BATCH ids. Authority for FR-9, FR-12 in `requirements.md`.

---

## D-017: Central diagnostics module at `core/src/diagnostics/` (Option A — standalone leaf node)
**Status**: Active · **Date**: 2026-05-04
**Decision**: A standalone `core/src/diagnostics/` module owns all cross-cutting observability contracts for HILDA: (a) central `error_codes.py` registry mapping every 3-letter module prefix + severity + number to a stable `ErrorCode`; (b) `report.py` — `ReportRecord` dataclass + `ReportWriter` for the four compact report types (RPT / MET / FIX / QC) the dev-LLM collaboration surface requires; (c) `qc.py` — `QCTemplate` base class enforcing fixed-field-only (int / float / bool / bounded enum) QC records with no free-text fields. Every other module imports from `diagnostics`; `diagnostics` imports nothing from HILDA (pure leaf node). All 18 module error-code prefixes are pre-registered in `error_codes.py` at first MODULE.md draft time.
**Why**: The `[D-002]` compact-reports invariant is cross-cutting — every module needs it from day one. Centralizing avoids per-module reinvention, prevents prefix collisions, and gives the dev a single pasteable `--diagnostic` output to inspect the full registry state. Option A (standalone) vs. Option B (inline per module / aggregator re-export): Option B defers the prefix-collision check to integration time and makes the registry non-discoverable without reading every module. Option A makes `diagnostics` the import root, creating a clean, cycle-free dependency leaf. Reference: `~/work/nora/core/src/pipeline/error_codes.py` and `report.py`.
**Consequences**: `diagnostics` is the first module drafted and the last to depend on anything else. Its `PREFIX_REGISTRY` dict is the canonical source of truth for all 3-letter prefixes — adding a new module = adding one entry here first, then in its own MODULE.md. The `QCTemplate` base class enforces the no-free-text invariant at the type level; any field declared as `str` (free-text) raises a `TypeError` at class definition time. `diagnostics_cli.py` (`--diagnostic`, `--validate`) is the first CLI in the project and a smoke-test that all prefixes are registered without collision.

---

## D-014: Customer template authoring — two separate ingestion paths, TPM-selectable
**Status**: Active · **Date**: 2026-05-04
**Decision**: The system maintains two separately supported customer template authoring paths: (a) **SharePoint-UI path** — TPMs author and edit templates directly through SharePoint classic web-part forms (live editing, structured List fields per `[D-006]`); (b) **Excel upload path** — TPMs upload Microsoft Excel template files conforming to the per-customer schema generated by the Template Schema Ingestor `[D-010]`. TPMs choose between the two paths per workflow preference. The system does not normalize them into a single canonical authoring format. Both paths must produce identical internal data model representations.
**Why**: The two paths serve different workflow contexts — UI for iterative on-the-fly editing; Excel for bulk authoring, copy-paste from existing tools, and PM team familiarity with Excel-native workflows. Normalizing into one path would degrade whichever path is demoted. Vs. UI only: Excel upload is existing PM muscle memory; removing it creates adoption friction. Vs. Excel only: SharePoint UI is the primary daily edit surface; removing it reduces accessibility. Vs. normalizing internally: adds ingestion complexity for no runtime benefit — both paths already land in the same internal data model.
**Consequences**: Both paths must pass through the same Pydantic base models in `core/src/template_schema/`; the Template Schema Ingestor `[D-010]` defines the Excel schema; the SharePoint List schema defines the UI path's structure. FR-39 updated to reflect the two-path choice. `drift-check` should verify both paths produce equivalent model output. Resolves backlogged Flag "Customer template authoring path normalization" from `STATUS.md`.

---

## D-015: API Spec Ingestor input format — OpenAPI 3.x canonical with preprocessing pass
**Status**: Active · **Date**: 2026-05-04
**Decision**: The API Spec Ingestor (`core/src/api_spec_ingestor/` per `[D-003]`) accepts **OpenAPI 3.x** as its canonical input format. Other formats (Swagger 2.x, company-internal formats, RAML, custom docs) are first converted to OpenAPI 3.x by an on-prem LLM-driven **preprocessing pass** (open-source model per `[D-007]`) before the main adapter-code-generation pipeline runs. The preprocessing pass is a sub-module within the Ingestor and emits a compact RPT per `[D-002]` indicating format detected and conversion confidence.
**Why**: OpenAPI 3.x is the widest-supported industry-standard spec format; using it as canonical keeps the main adapter-generation pipeline schema-stable. Vs. accepting all formats natively in the main pipeline: couples the generator to every input format variation. Vs. OpenAPI-only with no preprocessing: rejects valid company-internal specs the LLM can translate. Vs. Swagger 2.x as canonical: OpenAPI 3.x is a superset and the current industry standard.
**Consequences**: `core/src/api_spec_ingestor/` gains a `spec_normalizer.py` sub-module wrapping the preprocessing pass. CLI `--diagnostic` output reports original format detected and normalized OpenAPI 3.x doc size. Resolves backlogged Flag "API Spec Ingestor input format" from `STATUS.md`.

---

## D-016: v1 messenger targets — Slack (public reference) + proprietary internal messenger
**Status**: Active · **Date**: 2026-05-04
**Decision**: v1 ships two messenger adapters wired through the `Messenger` Protocol `[D-009]`: (a) **Slack** — adapter at `core/src/messenger/slack_adapter.py`, Slack Web API via `slack_sdk`; chosen as the public reference over Teams because setup is simpler (bot token + signing secret vs. Azure AD + M365 tenant), `slack_sdk.WebClient` is mockable without infrastructure, and unit tests carry no Azure dependency; (b) **proprietary internal messenger** — adapter at `customizations/messenger/<proprietary>_adapter.py`, generated by the API Spec Ingestor `[D-003]` as its first end-to-end production run in v1 (validates the Ingestor pipeline and the Protocol abstraction in one step). Both adapters implement the same `Messenger` Protocol; the rule engine and `CommunicationLog` are adapter-agnostic.
**Why**: Shipping both in v1 validates that the `Messenger` Protocol genuinely decouples adapters — the Ingestor-generated proprietary adapter must pass the same contract test suite as the hand-written Slack adapter. Including the proprietary adapter in v1 also exercises the API Spec Ingestor end-to-end, which was otherwise untested in v1. Vs. Slack only: defers the Ingestor's first real run to v2. Vs. proprietary only: harder to unit-test; no clean public reference for contract validation. Vs. Teams instead of Slack: Azure AD/M365 dependency adds setup friction for unit tests.
**Consequences**: `core/src/messenger/slack_adapter.py` — hand-written, `core/`-eligible, parallel to `jira_adapter.py`. Both adapters' test suites run against the same `Messenger` Protocol contract fixtures. DEF-5 and DEF-6 revisit triggers updated in `requirements.md`. Error-code prefix `MSG-` registered per `[D-002]`. FR-50 added to `requirements.md`. Resolves backlogged Flag "v1 messenger choice" from `STATUS.md`.

---

## D-019: credential_service v1 — K8s Secrets / ops-provisioned; full Vault-backed implementation deferred to v2
**Status**: Active · **Date**: 2026-05-04
**Decision**: v1 `credential_service` is a thin K8s Secrets reader. Ops provisions one K8s Secret per PM per system type at deploy time; the module exposes a stable `get_credential(pm_id, system_type) -> Credential` interface identical to what v2 will expose. Credentials are never logged or written to disk. No PM self-service registration UI, no Vault integration, no OAuth2 refresh loop, no health monitor, no mTLS between callers in v1. FR-32–FR-38 deferred to v2 as DEF-14; FR-51 captures the v1 behaviour. NFR-3 (per-PM isolation) and NFR-4 (encryption at rest via etcd, TLS in transit) still apply at v1 level.
**Why**: Full Vault-backed PM credential management is significant infrastructure — Vault HA, mTLS, OAuth2 grant flows, health-monitor CronJob, PM revocation UI. Building it in v1 delays the core workflow automation with no immediate PM-facing benefit (v1 has one customer, one PM team, ops-managed credentials). K8s Secrets with etcd encryption are adequate for a controlled on-prem cluster in v1. The stable interface (`get_credential`) ensures v2 is a backend swap behind the same call sites, not a refactor of every adapter. Option B (no per-PM credentials — service account only) was rejected because it breaks NFR-5 (PM approval attribution) and NFR-6 (per-PM audit trail).
**Consequences**: `credential_service` module exists in v1 with a simplified implementation; callers (`issue_tracker`, `messenger`, `customer_adapter`, `email_service`, `workflow_engine`) never need to change when v2 swaps in Vault. Ops runbook required: how to provision, rotate, and emergency-revoke a PM's K8s Secret. `credential_service` v1 MODULE.md documents the K8s Secret naming convention (`hilda-cred-{pm_id}-{system_type}`) and the read path. FR-51 replaces FR-32–FR-38 in v1; DEF-14 captures the full v2 scope.

---

## D-018: Template Schema Ingestor input format — three modes (schema-file / row-offset / infer)
**Status**: Active · **Date**: 2026-05-04
**Decision**: The `template_schema_ingestor` CLI accepts three `--mode` values, representing escalating LLM involvement: (a) **`schema-file`** — a YAML descriptor explicitly maps customer Excel column names to canonical fields, types, and validation rules; no LLM involved; fully deterministic and CI-testable. (b) **`row-offset`** — column headers are at a known row N (passed via `--header-row N`); the on-prem LLM does lightweight column-name → canonical-field resolution only; no full document inference. (c) **`infer`** — the on-prem LLM reads the full template document, discovers hierarchy layout, header location, and all column mappings, and emits a `CustomerSchema` with a generated `schema.yaml`. All three modes produce an identical `CustomerSchema` Pydantic model (defined in `core/src/template_schema/`) as output. YAML is the schema-file format (over JSON): human-readable with inline comments, low friction for PM team leads to review and edit. Recommended production workflow: run `--mode infer` once during customer onboarding to bootstrap `schema.yaml`, commit it to `customizations/template_schemas/<customer>/`, then switch to `--mode schema-file` for all subsequent re-ingestions. Resolves backlogged Flag "Template Schema Ingestor input format" from `STATUS.md`.
**Why**: Single-mode designs force a choice between LLM dependency (always infer) and manual labour (always schema-file). Three modes let teams start fast (infer), validate the output (commit schema.yaml), and run deterministically in production (schema-file) — matching actual onboarding workflows. YAML over JSON: inline comments let PM team leads annotate unusual column mappings without touching code; still machine-parseable. Row-offset mode handles the common case where header location is known but column names are customer-specific jargon — cheaper than full inference. Vs. two modes (schema-file + infer only): row-offset covers a high-frequency case (structured but non-standard Excel) without paying full-inference LLM cost.
**Consequences**: `template_schema_ingestor` ships `--mode schema-file|row-offset|infer`; `--header-row N` applies to row-offset (required) and infer (optional hint). YAML schema descriptor format is defined in `core/src/template_schema_ingestor/MODULE.md` with a normative example. The `CustomerSchema` Pydantic model lives in `core/src/template_schema/` so runtime modules can load it without importing the ingestor. The "infer-once → commit → schema-file" workflow is the documented onboarding path; `--mode infer --dry-run` previews the generated `schema.yaml` without writing to `customizations/`. Authority for FR-39 (`[D-014]`) and TSI error-code prefix in `error_codes.py`.

---

## D-013: Shared network drive — `hilda-svc` writes, HILDA-mediated reads, no per-customer AD groups in v1
**Status**: Active · **Date**: 2026-05-01
**Decision**: SharePoint cannot handle binary attachment sizes for HILDA's deliverables, so attachments and HILDA-generated artifacts (test reports, tech reports, waivers, customer submission packages) are stored on an on-prem shared network drive, **not** in SharePoint Document Libraries. The drive is SMB-mounted on the HILDA Linux host; Windows-side authentication is on-prem AD. Access model: (a) **Writes** — a single dedicated AD service account `CORP\hilda-svc` is the only principal with `Modify` on `\\share\hilda\`; the Linux SMB mount uses this account's credentials (Kerberos keytab preferred over password); all HILDA writes go through this mount. (b) **Reads** — PMs read attachments exclusively via the HILDA dashboard, which renders attachment links as `https://hilda.corp/dl/<scoped_token>`; the download endpoint authenticates the PM via on-prem AD (NTLM / Kerberos), authorizes against the DeliveryItem's ACL, reads from the network drive as `hilda-svc`, and streams the file to the browser. **Direct UNC paths are not exposed to PMs and are not embedded in any HTML rendered by `core/`.** (c) **Windows ACL** — `CORP\hilda-svc` = Modify; `Domain Admins` = Full (operational); everyone else = none. **No per-customer or per-device AD groups in v1.** Path convention: `\\share\hilda\<customer_slug>\<device_slug>\<milestone_slug>\<deliverable_slug>\<item_slug>\` with `inbound/`, `outbound/`, `revisions/` subdirectories; slugs are `[a-zA-Z0-9_-]+`, minted at entity-creation, immutable on rename, stored on the entity record.
**Why**: SharePoint 2017 Document Libraries cannot handle the file sizes HILDA attachments will reach. The on-prem network drive is the natural alternative. Of the access-model options: vs. direct UNC paths in dashboard links — forwarding or copy-paste leaks the path; bypasses HILDA's audit trail (link generation logged but not access); ACL surface grows linearly with customers (one AD group per customer is the natural granularity). Vs. per-customer AD groups + direct UNC — useful only when ops or admins need raw browse access; v1 has no such requirement. Vs. hybrid (mediated reads + admin-only direct UNC) — more moving parts; v1 has no admin browse-access requirement to justify the complexity. The chosen design: minimal ACL surface (one principal forever), full audit trail through `CommunicationLog` (NFR-6), no path leakage, simple revoke (HILDA-side per-PM ACL change, no AD group changes).
**Consequences**: HILDA ships a download endpoint (likely a FastAPI route at `core/src/storage/` or under the dashboard backend) implementing PM auth + per-DeliveryItem ACL + streaming reads. Storage Protocol surface is `core/`-defined: `put_file(path) -> link`, `get_file_for_download(link, pm_identity) -> stream`, `list_files(item_ref)`, etc. SharePoint integration scope shrinks to Lists + classic web parts only; Document Libraries drop out of the integration surface (NFR-8). Audit logging extends to file reads — every download endpoint hit emits a `CommunicationLog` entry (PM, DeliveryItem, file, timestamp). Slug encoding becomes a cross-cutting convention; the entity model gains a `path_slug` field per Customer / Device / Milestone / Deliverable / DeliveryItem. Authority for FR-13, FR-17, FR-18, NFR-8, NFR-16 in `requirements.md`. PDF support requires architecture-phase choice of the on-prem PDF text-extraction path (`pdfplumber` / `pypdf` / `pymupdf`); Word support requires `python-docx` for `docx` and a separate path for legacy `doc` (likely `antiword` or LibreOffice headless conversion).

---

## D-020: sharepoint_integration — SpClient / SharePointListProvider Protocol separation
**Status**: Active · **Date**: 2026-05-04
**Decision**: `core/src/sharepoint_integration/` separates two orthogonal concerns: (a) **SpClient** — the raw async SP REST HTTP client; owns NTLM/Kerberos authentication, SP REST URL patterns, pagination, and retry logic; takes SP-native list names and SP internal column names; has no knowledge of HILDA entities. (b) **SharePointListProvider Protocol** — a pure lookup service (no HTTP, no side effects); given a HILDA entity type and a `ListScope(customer_slug, device_slug)`, returns the SP list name and column map for that scope; implemented in `customizations/` but its Protocol definition ships in `core/`. A boilerplate `FileBasedListProvider` implementation ships in `core/` and reads from `customizations/sharepoint_config/customers/<slug>.yaml` (list names + column maps) and `customizations/sharepoint_config/devices/special_devices.yaml` (device-level list overrides); scope lookup precedence is device override → customer config → `SHP-E002`. **`list_crud.py` (class `SpCrud`) is the sole compositor** — it accepts any `SharePointListProvider` implementation, translates canonical field names to SP columns via the provider, and delegates wire calls to `SpClient`. All other HILDA modules call `SpCrud` exclusively; no module calls `SpClient` or `SharePointListProvider` directly. Operational config (site URL, auth type, timeouts) follows the nora 3-tier pattern (CLI arg → env var → `config/sharepoint_integration.json`); customer SP list names and column maps are business config living in `customizations/sharepoint_config/` and are **not** in `config/`.
**Why**: Without separation: the SP mechanics and the HILDA-entity→SP-column routing are entangled; swapping auth method requires touching entity-routing code and vice versa; unit-testing entity routing requires a live SP instance. Separating them: `SpClient` is independently testable with an `httpx.MockTransport` stub; `SharePointListProvider` is testable with pure Python dict comparisons; `SpCrud` is testable by injecting both mocks. Customer deployments override only the YAML data (list names), not the Python code. Vs. embedding list names in `config/sharepoint_integration.json`: config/ is for environment-switching values (site URL changes between dev/prod); list names are fixed per customer, not per environment — wrong axis of variation. Vs. all-in-one `SharePointClient` class: no seam to inject mock provider; business config and auth config entangled.
**Consequences**: `customizations/sharepoint_config/` is the canonical location for all customer-specific SP list name + column map YAML. Any customization that provides a non-file-based provider (e.g. DB-backed, API-backed) implements `SharePointListProvider` and is injected at startup. `FileBasedListProvider` reload triggers: on startup and on explicit admin signal (no hot-reload in v1). SHP error-code prefix registered in `diagnostics/error_codes.py`. `sharepoint_integration_cli.py` ships `--diagnostic` (live SP connectivity + list reachability), `--mock` (`httpx.MockTransport` stub for all-local testing), `--dry-run --customer <slug>` (logs SP operations, no writes).

---

## D-021: Process granularity v1 — modular monolith with three deployable workloads (`hilda-api`, `hilda-worker`, `hilda-llm-gateway`)
**Status**: Active · **Date**: 2026-05-06
**Decision**: HILDA v1 ships as **one container image** containing all 18 `core/src/` modules + all `customizations/`, run as **three K8s Deployments** with different start commands: (a) **`hilda-api`** — FastAPI/uvicorn process; serves the dashboard backend, SP-mediated download endpoint per `[D-013]`, and inbound webhook receivers (messenger / issue-tracker callbacks); 2 replicas; ingress-exposed at `https://hilda.corp/`. (b) **`hilda-worker`** — async-job runner (Celery- or RQ-style; specific engine decided in `D-XXX` per `SYSTEM.md` §4); executes scheduled rule firings, email mailbox polling, ingestor jobs, customer-adapter polling, and any blocking IO that should not contend with API request handling; 2 replicas + 1 beat/scheduler singleton. (c) **`hilda-llm-gateway`** — thin process that fronts both the runtime LLM `[D-007]` and the on-prem code-generation LLM; the only pod authorized to egress to the corp LLM proxy; owns rate-limiting, retry policy, prompt-template loading, and the LLM API key K8s Secret; 2 replicas. **All other modules — `sharepoint_integration`, `email_service`, `messenger`, `issue_tracker`, `tracker`, `rule_engine`, `workflow_engine`, `template_schema`, `template_schema_ingestor`, `api_spec_ingestor`, `test_report_profiler`, `customer_adapter`, `storage`, `credential_service`, `diagnostics`, `dashboard`** — live in-process inside `hilda-api` and/or `hilda-worker` as Python imports, no separate pods. `credential_service` v1 is in-process per `[D-019]` (no Vault pod in v1). Per-customer adapter pods are deferred until customer #2 onboards (out of scope per `PROJECT.md` `DEF-8`). Infrastructure workloads — Postgres (StatefulSet), Redis (Deployment) — remain separate in their own right (standard for any topology). Supersedes `HILDA_Design.md` §11's 12-deployment microservices inventory; that table is preserved as the v2+ target shape but is wrong-sized for v1's one-customer / small-team scope.
**Why**: `HILDA_Design.md` §11's microservices design optimizes for two pressures HILDA v1 does not have: independent scaling per module (one customer, low volume) and per-team ownership (single small dev team, names TBD per `PROJECT.md` Contributors). Twelve Deployments means twelve sets of: Helm sub-charts + values, image build/scan/push, RBAC, NetworkPolicy, liveness/readiness probes, integration-test wiring, log/metrics scrape configs. That overhead is real and pays back nothing at v1 scale. Pure modular monolith (one Deployment) was rejected because three concerns genuinely want process boundaries: (i) blocking IO — email mailbox polling, customer-adapter polling, scheduled rule firings — should not compete with low-latency API request handling; (ii) the runtime LLM is the slowest, riskiest, highest-blast-radius external dependency, and isolating its egress simplifies corp-proxy network policy and contains LLM-side failures from cascading into API pods; (iii) the worker process needs Celery/RQ semantics (long-running tasks, retries with backoff, scheduled triggers) that don't fit the FastAPI lifecycle cleanly. The three-process split addresses all three with the minimum number of pods. Module boundaries inside the monolith remain enforced through Protocol seams already established by `[D-008]` (IssueTracker), `[D-009]` (Messenger), `[D-019]` (credential_service), `[D-020]` (sharepoint_integration); a future v2 split of any module to its own pod is mechanical (extract module + add a thin REST surface), not a refactor — Protocol call sites stay unchanged. Vs. splitting `email_service` to its own pod for 24/7 polling: the polling cadence is minutes, not seconds; running it as a Celery beat task inside `hilda-worker` is sufficient. Vs. splitting `credential_service` to its own pod (per `HILDA_Design.md`): `[D-019]` simplified credentials to K8s Secrets in v1; a separate Deployment buys nothing when the implementation is a `kubectl get secret` wrapper. Vs. splitting per-customer adapters: only one customer in v1; second customer triggers the split.
**Consequences**: One `Dockerfile` produces one image; three Helm Deployment templates differ only in `command:` and resource limits. Configuration follows the nora 3-tier per `[D-020]` already established in `sharepoint_integration` — same shape replicated for every module's `config/<module>.json`. K8s Secrets per `[D-019]` are mounted only into the pods that need each one (e.g., LLM API key only into `hilda-llm-gateway`). The three pods share Postgres and Redis as their coordination substrate; no in-cluster service-to-service HTTP between HILDA pods in v1 except API → llm-gateway. Each module's `MODULE.md` declares which workload(s) host it (api / worker / llm-gateway / multiple) — added as a curated subsection alongside `Depends on`. `regen-map` extends to render workload assignment in `MAP.md`. Test interfaces per `[D-005]` continue to work unchanged — every module ships its CLI / mock harness, exercising it in-process. v2 split path (per-module microservice migration) starts by promoting a Protocol's existing in-process implementation to the server side of a thin REST surface; client side stays at the same import path, so call sites do not change. SYSTEM.md §2 moves from TBD to Decided and links here; SYSTEM.md §3 communication matrix and §5 deployment topology resolve as direct consequences. SYSTEM.md "Conflicts with HILDA_Design.md" entry C3 is now Resolved.

---

## D-022: Workflow engine v1 — Celery + Redis broker + Postgres result backend (Temporal deferred to v2)
**Status**: Active · **Date**: 2026-05-06
**Decision**: HILDA v1 uses **Celery** as its async-task framework, with **Redis as the broker** and **PostgreSQL as the result / state backend**. **Celery beat** (the singleton scheduler) runs as a separate K8s Deployment (`hilda-beat`, replicas=1) alongside `hilda-worker` per `[D-021]`, reading the active schedule from the SharePoint `AutomationRules` list at startup and on a refresh signal. The schedule defines cron-style triggers for time-based rules (reminders, escalations, mailbox poll, customer-adapter poll). Event-triggered rules (inbound webhook, attachment-received, PM-approval-clicked) enqueue Celery tasks directly from the originating handler in `hilda-api`. The `core/src/workflow_engine/` module owns: the Celery app singleton, task decorators (`@hilda_task`) that wrap rule executions with `[D-002]` error-code reporting and structured logging, the beat schedule loader (reads `AutomationRules` via `SpCrud`), and the rule dispatcher (matches an event to one or more rules and enqueues tasks). The `core/src/rule_engine/` module owns pure rule-condition evaluation (no Celery imports); `workflow_engine` is the dispatcher that calls into it. **`HILDA_Design.md` §11's `Workflow Engine (Temporal)` StatefulSet is removed from v1 deployment topology**; `Temporal Workers` are subsumed by `hilda-worker`. Temporal is a v2+ candidate if rule sets evolve into multi-step durable orchestrations with cross-step state and time-travel debugging needs.
**Why**: HILDA's actual workflow surface — enumerated in `HILDA_Design.md` §8.1 — is **single-step state transitions triggered by time or events**: "send reminder when LastOwnerContacted > N days," "trigger quality review on attachment received," "queue for submission on PM approval." The state lives in SharePoint List rows; each rule firing reads SP, evaluates a condition, performs one action (send email / update column / call adapter), writes back to SP. None of this is a multi-day Temporal workflow with cross-step durable state. The closest thing to multi-step is the customer-submission flow (detect ready → human gate → submit), but it's two single-step actions stitched together by SP state, not a workflow object. Temporal's strengths — durable state machines, time-travel debugging, signal/query semantics, workflow versioning — are paid for in operational cost (3-node StatefulSet + worker tier + dedicated history database) that v1 has no demand for. Vs. **APScheduler in-process**: rejected because the scheduler must survive pod restart cleanly; APScheduler with a Postgres jobstore is workable but is single-process by design (only one beat instance can run, like Celery beat) and has weaker semantics around lost task results during restart. Vs. **RQ (Python Redis Queue)**: simpler than Celery but lacks a robust scheduled-trigger story (rq-scheduler exists but is less battle-tested); HILDA needs scheduled triggers heavily for reminders/polling. Vs. **Celery with Postgres broker**: cleaner (one infra dependency) but Celery's Postgres broker has historically been less robust than its Redis broker; Redis is already in the stack per `HILDA_Design.md` §11, so the marginal cost of using Redis as broker is zero. v2 trigger to revisit Temporal: rule set crosses ~30 rules with explicit cross-rule dependencies, OR a workflow emerges that genuinely needs durable multi-step state (e.g., compliance-audit submission with multi-week regulator-side state machine).
**Consequences**: `core/src/workflow_engine/MODULE.md` (when drafted) names: Celery app at `core.src.workflow_engine.celery_app`; task decorator `@hilda_task(rule_id, error_code_prefix)`; beat schedule loader `load_schedule_from_sp(crud) -> dict[str, ScheduleEntry]`; dispatcher `dispatch(event) -> list[AsyncResult]`. WFL error-code prefix already pre-registered in `diagnostics/error_codes.py`. `hilda-worker` and `hilda-beat` Deployments share the `hilda-worker` start command path with different argv (`celery -A workflow_engine worker` vs `celery -A workflow_engine beat`). Redis (one Deployment) gains a documented role as Celery broker in addition to dedup-cache from `[D-012]`. Postgres schema (owned by `core/src/storage/`) gains a `celery_taskmeta` table — Alembic migration when `storage/MODULE.md` is drafted. SP `AutomationRules` list rows include `cron_expression` columns for scheduled rules; rule reload happens on startup and on `SIGHUP`-triggered refresh (no hot-reload of code). All rule executions emit a paired RPT compact report per `[D-002]` keyed by `(rule_id, run_id)`; failure surfaces as a WFL-coded `PipelineError` and a FIX entry if PM intervention required. SYSTEM.md §4 workflow-engine question moves from TBD to Decided; SYSTEM.md §3 inter-component-comms matrix `Redis-backed queue (Celery/RQ)` row is concretized to `Celery via Redis broker, results in Postgres`; SYSTEM.md §5 deployment topology adds `hilda-beat` as a singleton Deployment; SYSTEM.md "Conflicts with HILDA_Design.md" entry C4 is now Resolved.

---

## D-023: Observability v1 — light stack reusing cluster defaults; dashboards/alerts as code under `deploy/`
**Status**: Active · **Date**: 2026-05-06
**Decision**: HILDA v1 owns and operates **no observability infrastructure of its own** — instead it produces standard signals that the existing on-prem cluster's o11y stack consumes, and ships dashboard / alert definitions **as code** in `deploy/grafana/dashboards/` and `deploy/prometheus/alerts/` so corp Grafana / Prometheus can import them. Three signal channels: (a) **Structured JSON logs to stdout** — every pod (`hilda-api`, `hilda-worker`, `hilda-beat`, `hilda-llm-gateway`) logs JSON via `python-json-logger`-style formatter; cluster default log forwarder picks them up and ships to whatever corp log store exists (Splunk / Elastic / Loki — HILDA does not specify). Required fields per log line: `ts`, `level`, `pod`, `module`, `error_code` (when applicable per `[D-002]`), `run_id`, `pm_id` (when applicable, never the credential), plus the message. (b) **Prometheus metrics on `/metrics`** — every pod exposes a Prometheus scrape endpoint via `prometheus_client` (or `prometheus-fastapi-instrumentator` for `hilda-api`). Required v1 metric families: `hilda_request_total{path, method, status}` (api request counter), `hilda_celery_tasks_total{task, status}` (worker task counter), `hilda_pipeline_errors_total{code}` (the `[D-002]` integration — every `PipelineError` raise increments this), `hilda_llm_calls_total{model, status}`, `hilda_sp_request_total{status}`, `hilda_credential_expiry_seconds{system_type}`, `hilda_queue_depth{queue}`, `hilda_adapter_retry_total{adapter, outcome}`, plus per-family latency histograms (`*_duration_seconds`). (c) **Compact reports via `[D-002]`** — RPT / MET / FIX / QC continue as the domain audit trail, persisted in `CommunicationLog` (Postgres, owned by `core/src/storage/`); these are app-domain artifacts and remain orthogonal to the o11y signals above. **No HILDA-owned distributed tracing in v1** — added as a follow-up `D-XXX` if cross-pod debugging pain emerges (most likely first surface: `hilda-api` → Celery enqueue → `hilda-worker` execution → `hilda-llm-gateway` LLM call). **No HILDA-owned Grafana / Loki / Tempo / OTel collector pods.** Dashboards-as-code: `deploy/grafana/dashboards/system_overview.json`, `error_codes.json` (panels keyed on `hilda_pipeline_errors_total` by `code`), `workers_and_queues.json`, `llm_gateway.json`, `sharepoint_integration.json` — checked into git and importable into corp Grafana via Grafana's import API or HILDA's deploy job. Alert rules-as-code: `deploy/prometheus/alerts/hilda.yaml` with rules per `[D-002]` error-code class (e.g. `SHP-E*` rate > N/min over 5m → page; `WFL-E*` rate spike → ticket).
**Why**: `architecture.md` calls for "medium" observability — meaningful instrumentation at pain points without overbuilding metrics infra v1 doesn't need. The corp cluster almost certainly already runs a log forwarder + Prometheus + some Grafana installation; the team is small (`PROJECT.md` Contributors all TBD); spinning up a HILDA-owned Loki/Prom/Tempo/Grafana stack is 5+ extra workloads to operate with no signal-quality payoff. The dashboards-as-code commitment captures the real value of "owned" o11y (curated panels in git, reviewable in PR, deployable to test/prod) without paying for the pods. `[D-002]` already produces the highest-value signal — error codes with structured context — so the o11y stack's job is mostly to display and alert on it. Vs. **Option B (full HILDA-owned stack)**: 5 extra workloads (Loki + Promtail + Prometheus + Grafana + Tempo + OTel) for capabilities corp infra likely already provides; rejected because v1 ops capacity is the constraint, not signal coverage. Vs. **Option C (hybrid: reuse corp logs/Prom but own Grafana + Tempo)**: 2 extra workloads buys traces + git-controlled dashboards; rejected for v1 because (a) tracing demand is not yet evidenced, (b) git-controlled dashboards are achieved by the as-code pattern without owning Grafana itself. Vs. shipping no dashboards / alerts: rejected because o11y signals with no consumption surface are dead code. The chosen design surfaces every signal HILDA needs while keeping the operational footprint at zero new pods.
**Consequences**: A new shared module `core/src/observability/` (or extension of `diagnostics/`) provides the Prometheus client setup + log formatter + standard metric registry; every workload imports it on startup. `[D-002]`'s `PipelineError` raise path automatically increments `hilda_pipeline_errors_total{code}` (instrumented at `error_codes.py` level — one-shot wiring). `hilda-api`, `hilda-worker`, `hilda-llm-gateway` Helm Deployment templates each gain a `containerPort: 9090` for `/metrics` and a Prometheus `ServiceMonitor` (or scrape annotation, depending on what corp Prom uses). `deploy/grafana/dashboards/` and `deploy/prometheus/alerts/` are part of the deploy artifact and provisioned alongside the chart. CI lints these (`promtool check rules`, `jsonnet --eval` if dashboards become jsonnet). Logs must never include credential material, customer-feedback prose, or report content per the no-proprietary-content invariant `[D-002]` + `PROJECT.md` Constraints — log review is part of `/drift-check`. v2 trigger to revisit: cross-pod debugging pain → distributed tracing follow-up `D-XXX`; corp Grafana shows scaling pain on dashboards → consider HILDA-owned Grafana. SYSTEM.md §6 moves from TBD to Decided; SYSTEM.md Open Question #3 is closed.

---

## D-024: CI/CD shape v1 — tool-agnostic pipeline contract + single umbrella Helm chart with per-environment values
**Status**: Active · **Date**: 2026-05-06
**Decision**: HILDA's CI/CD captures the durable shape independent of which corp tools host it. Specific corp-tool selections (CI runner, image registry, GitOps tool, environment topology) remain a backlogged Flag in `STATUS.md` to be filled in after consultation with corp ops; they fit into this shape as parameters, not redesigns. **Pipeline shape (uniform, tool-agnostic):** (a) **On every PR** — lint (`ruff` / `mypy` / `black`) + unit tests + integration tests against in-process mock SP server via `httpx.ASGITransport` (existing pytest suite, currently 101 tests) + image build + image vulnerability scan (Trivy / Grype / corp scanner). PR is mergeable iff all stages pass. (b) **On merge to `main`** — tag image with git SHA (`hilda:<git-sha-short>`), push to corp registry, deploy chart to **test env** with `values-test.yaml`, run smoke tests against test env (mock SP + real Postgres + real Redis), update test env's HEAD pointer. (c) **Promotion to prod** — manual: re-tag the SHA-tagged image with semver (`hilda:1.0.0`), deploy chart to **prod env** with `values-prod.yaml`, run smoke tests against prod (real SP via NTLM/Kerberos once corp AD lab access lands), gate on success. (d) **Image versioning** — SHA tag for dev/test (immutable, traceable to commit); semver tag for releases (`MAJOR.MINOR.PATCH` per semver.org); both tags coexist in the registry; `latest` tag is **not used** in any cluster manifest to prevent accidental drift. **Helm chart structure:** one umbrella chart at `deploy/charts/hilda/` containing all three v1 Deployment templates (`hilda-api`, `hilda-worker` + `hilda-beat`, `hilda-llm-gateway`) per `[D-021]`, plus shared resources (Service, Ingress, ServiceAccount, NetworkPolicy, ServiceMonitor for `[D-023]`). Environment-specific values files: `values-dev.yaml` (local kind / minikube — runs against mock SP + ephemeral Postgres/Redis), `values-test.yaml` (test cluster / namespace — mock SP + real Postgres/Redis), `values-prod.yaml` (real SP via NTLM/Kerberos + production Postgres/Redis). The `values-*.yaml` files are checked into the repo; secret values are **not** — those are K8s Secrets per `[D-019]`, provisioned by ops, referenced by name from the chart. **Test environment specifically runs the mock SP server as a sidecar / separate Deployment** (`mock-sharepoint:<sha>`, the existing `core.src.sharepoint_integration.mock_server.app`) so test-env pods point at it via `HILDA_SP_SITE_URL=http://mock-sharepoint:8765`. **Per-workload sub-charts rejected**: one umbrella chart for v1 because all three workloads share the same image, deploy together, and version together; sub-charts add Helm template-resolution overhead with no payoff at three deployments. (e) **Backlogged tool-bound choices** — CI runner (GitHub Actions / GitLab CI / Jenkins / corp-specific), image registry (Harbor / Artifactory / Nexus / corp-specific), GitOps tool (ArgoCD / Flux / none — CI-driven `helm upgrade`), environment topology (separate clusters vs separate namespaces in one cluster).
**Why**: Separating the pipeline **shape** (what stages run, what artifacts they produce, what the deploy unit looks like) from the **tools** (which CI runs the stages, which registry holds the image, which mechanism syncs to cluster) lets HILDA commit to durable architectural choices now without blocking on corp ops scheduling. The shape is decision-worthy in its own right: the PR-time vs merge-time vs promote-time split, the image-versioning convention, the umbrella-chart-with-per-env-values structure, the test-env-uses-mock-SP pattern — these survive any CI-tool swap. Vs. waiting for full corp-ops consultation: blocks every deploy-time architectural choice (Helm structure, env values, test-env wiring) until a meeting; rejected. Vs. full per-workload sub-charts: Helm sub-chart machinery solves problems v1 doesn't have (independent versioning of components, third-party reuse); rejected. Vs. raw K8s manifests + Kustomize overlays: Kustomize is fine for "deploy this same shape to N customers" but HILDA v1 has one customer; Helm's templating is more mature for the "values per environment" axis we actually have. Vs. using `latest` tag: rejected as a hard rule because `latest` defeats traceability and rollback; SHA tags for dev/test (immutable, debuggable) and semver for releases (deliberate human action) is the standard pattern. Vs. CI-deployed prod: explicitly **manual** prod promotion because v1 has one customer + small team + frozen SP infrastructure — automated prod deploys multiply blast radius without saving meaningful time at v1 cadence.
**Consequences**: `deploy/charts/hilda/` lives at repo root with `Chart.yaml`, `values.yaml` (defaults), `values-dev.yaml` / `values-test.yaml` / `values-prod.yaml`, and `templates/` containing one Deployment per workload + Service + Ingress + NetworkPolicy + ServiceMonitor per `[D-023]`. `Dockerfile` lives at repo root, builds one image used by all three Deployments (different `command:` per workload). `deploy/grafana/dashboards/` and `deploy/prometheus/alerts/` from `[D-023]` ship alongside the chart. CI pipeline definition file (`.github/workflows/ci.yml` / `.gitlab-ci.yml` / `Jenkinsfile` — TBD) implements the pipeline shape; a `STATUS.md` Flag tracks the tool selection until resolved by a follow-up `D-XXX`. Mock SP server image (`mock-sharepoint:<sha>`) is built from the same repo and pushed to the same registry; only used in dev / test envs, never prod. Smoke-test suite (separate from unit/integration tests) lives at `core/tests/smoke/` and runs against a deployed env via the SP REST surface — to be drafted when test env is provisioned. Versioning policy: tag every merge with SHA; cut a semver release at meaningful milestones (manual decision; release notes in `CHANGELOG.md` — file to be created at first release). v2 triggers to revisit: per-customer chart instances if customer N customizes deeply enough to need its own values file (today this lives in `customizations/sharepoint_config/`, not in Helm values); per-workload sub-charts if any workload's release cadence diverges materially from the others. SYSTEM.md §8 moves from TBD-shape to Decided-shape with tool choices remaining as a tracked Flag; SYSTEM.md Open Question #4 split into resolved (shape) + open (tool selection); SYSTEM.md Open Question #5 (Helm chart granularity) is closed by this decision.

---

## D-025: Customer YAML mount — v1 Docker bind-mount; v2 K8s ConfigMap
**Status**: Active · **Date**: 2026-05-08
**Decision**: `customizations/sharepoint_config/` (customer list/column maps per `[D-004]` + `[D-020]`) is mounted into HILDA containers at `/app/customizations/sharepoint_config/` via a Docker Compose bind-mount in v1 (the directory lives in the repo on the bare-metal host). In v2 K8s it becomes a ConfigMap mounted at the same container path. The mount path is controlled by `HILDA_CUSTOMIZATIONS_DIR` env var (default `/app/customizations/sharepoint_config/`); `FileBasedListProvider` reads from this path regardless of how it was injected.
**Why**: Bind-mount and ConfigMap are the same abstraction — host directory injected into container at a configurable path — with different mechanisms. The bind-mount approach (v1) avoids image rebuild when a new customer YAML is added (customer YAML is not baked in); for a single-machine deployment this is as lightweight as possible. Image-baked was rejected: adding a new customer requires a rebuild and push cycle even when only a YAML file changed. ConfigMap (v2) gives identical semantics in K8s. Using the same env var and container path in both v1 and v2 means zero code change at migration time.
**Consequences**: `FileBasedListProvider` constructor accepts a base path defaulting to `HILDA_CUSTOMIZATIONS_DIR`. `docker-compose.yaml` mounts `./customizations/sharepoint_config/` as a read-only bind-mount into all three HILDA application services. K8s migration: replace the bind-mount volume with a ConfigMap volume; no Python change. Resolves SYSTEM.md Open Question #6.

---

## D-026: v1 deployment platform — Docker Compose on single bare-metal Linux PC
**Status**: Active · **Date**: 2026-05-08
**Decision**: HILDA v1 runs on a **single bare-metal Linux PC** using **Docker Compose** as the orchestration layer. This supersedes the K8s-specific deployment mechanisms in `[D-021]` (three K8s Deployments + Helm chart), `[D-024]` (Helm chart structure), and `[D-019]` (K8s Secrets) **for v1 only**. The process boundaries (hilda-api / hilda-worker + hilda-beat / hilda-llm-gateway), container image (one `Dockerfile`, one image), task architecture (Celery + Redis + Postgres per `[D-022]`), and observability signals (structured logs + `/metrics` per `[D-023]`) are **unchanged**. Secrets are per-service `.env` files at `/etc/hilda/<service>.env` on the host, provisioned by ops, gitignored; env var names are identical to what v2 K8s Secrets will set. `deploy/compose/docker-compose.yaml` is the v1 deploy artifact; `deploy/charts/hilda/` Helm chart from `[D-024]` is preserved as a v2+ placeholder (README only in v1). A `deploy/scripts/deploy.sh` script handles: `git pull` → `docker compose pull` → `docker compose up -d` → `docker compose run --rm hilda-api alembic upgrade head`. K8s migration path: Docker Compose service names mirror intended K8s ClusterIP Service names (zero rename); `kompose convert` produces base K8s manifests; Nginx container → Ingress controller; env_file → K8s Secrets; bind-mounts → PVCs / ConfigMaps; replica counts scale up to `[D-021]` targets.
**Why**: v1 is one customer, one small team, one machine. K8s overhead at this scale — cluster provisioning (control-plane, CNI, CSI, etcd, kubelet), Helm chart + values files, RBAC, NetworkPolicy, cert-manager, ingress controller — is disproportionate and provides no payoff: one replica per service is sufficient, no independent scaling needed, no multi-team pod ownership. Docker Compose gives identical container isolation, identical DNS naming (service name = hostname), identical env-var config pattern, and identical image artifacts as K8s — minimizing migration friction when scale demands it. vs. raw systemd processes: systemd avoids Docker daemon overhead but is not containerized, so K8s migration would require writing Dockerfiles, adjusting paths, and verifying runtime parity from scratch; with Compose the containers already exist. vs. Docker Swarm: adds clustering semantics that add no value for one machine. vs. K8s with single-node cluster (e.g., k3s / kind): still requires etcd + kubelet + kube-proxy; adds operational complexity for a single developer managing a single PC. The "design for K8s migration" requirement is met by naming, env var, and volume conventions — not by running K8s itself.
**Consequences**: `deploy/compose/docker-compose.yaml` is the primary deploy artifact for v1. `deploy/charts/hilda/` exists as a v2 placeholder. All env var names (`HILDA_SP_*`, `HILDA_DB_URL`, `HILDA_REDIS_URL`, `HILDA_LLM_*`) are chosen to be identical in Compose env_file and K8s Secret so no code changes on migration. Docker Compose service names (`hilda-api`, `hilda-worker`, `hilda-beat`, `hilda-llm-gateway`, `postgres`, `redis`) are chosen to match intended K8s Service names. SYSTEM.md §2, §5, §7, §8, §9 updated to reflect bare-metal Compose v1 with K8s v2 notes throughout. `[D-019]`'s K8s Secret naming convention is v2-only; v1 credential mechanism is `.env` files with identical env var names. `[D-023]`'s references to "pods" and "ServiceMonitor" apply to v2; v1 equivalent is containers + Prometheus scrape via Docker service DNS. SYSTEM.md "Conflicts with HILDA_Design.md" entry C5 added. Resolves SYSTEM.md Open Question #9.

---

## D-027: Teacher↔Cline collaboration — one-way git bridge; compact reports as the return channel
**Status**: Active · **Date**: 2026-05-08
**Decision**: The work PC can **read but not write** `origin` (github.com). The git topology is a **one-way bridge**: Teacher LLM pushes scaffolds to `origin`; `sync-work.sh` pulls from `origin` and pushes to `company` (internal GitHub); Cline pulls from `company`, completes TODOs, and pushes back to `company`. Teacher never reads Cline's completed code via git — the only return channel is the **compact redacted report** (ITR-RPT, ASI-RPT, etc.) that the user hand-types into the Teacher chat. `utils/git-sync/sync-work.sh` is a 4-step one-way bridge: fetch company → merge company → fetch origin → merge origin → push company. It does not push to origin. `.clinerules/01-role.md` documents this constraint explicitly.
**Why**: Corporate network policy blocks outbound push to github.com from the work PC. An attempted two-way sync (push-to-origin step added then reverted) confirmed the constraint is hard. The Teacher/Student protocol was already designed for this: Teacher designs from compact reports (never from proprietary content), so no return git channel is architecturally required. The compact report is sufficient for Teacher to write the next scaffold.
**Consequences**: `sync-work.sh` must never gain a push-to-origin step. Cline trip prompts always end with "push to company, tell user to run sync-work.sh." Teacher's next scaffold is always based on the compact report, not on reading completed adapter code. Proprietary implementation details (completed TODOs, real system names in env vars) stay on `company` and never reach `origin` by construction.

---

## D-120: Corp PLM 5-API thin-wrapper + tpm_corp_id-as-attribution + in-flight (plm_id, fileID) tracking

**Date**: 2026-06-25
**Status**: Ratified

**Context**: HILDA's `issue_tracker` module needs a corp PLM integration for FR-26 polling (HILDA polls corp PLM for owner-uploaded documents) + FR-77 fan-out (HILDA uploads owner-ingested docs back to corp PLM) + createPLM at tracker provisioning + closePLM at milestone closure. The architect shared via screenshot 2026-06-25 the 5 corp PLM APIs available as already-existing corp services: `createPLM`, `closePLM`, `getdocumentslist`, `downloadFile`, `uploadFile` (uploadFile added in correction after initial 4-API spec). These services are corp-proprietary; HILDA cannot land the concrete binding-call code on public github per NFR-2 and `[D-027]` Teacher/Student split.

**Decision**: `issue_tracker.corp_plm.CorpPlmAdapter` is a Protocol-conformant thin wrapper around the 5 already-available corp PLM APIs (`createPLM` / `closePLM` / `getdocumentslist` / `downloadFile` / `uploadFile`). HILDA-side scaffold provides abstract `_invoke_create_plm` / `_invoke_close_plm` / `_invoke_get_documents_list` / `_invoke_download_file` / `_invoke_upload_file` methods that raise `ITR-E001` by default (NotImplementedError). Per-customer subclass at `customizations/issue_tracker/<customer_id>_corp_plm_adapter.py` filled in by Cline on Work PC per `[D-027]`; concrete binding-call body imports the corp PLM client library + invokes per the architect's spec. Bundle of 7 architect-Q-locked sub-decisions:
- (Q1) `createPLM` fires from `workflow_engine.tasks.lifecycle.PROVISION_TRACKER` ActionKind on tracker creation OR `START_ITEM_COLLECTION` ActionKind on Start Collection (FR-8). HILDA auto-provisions PLM tickets when work-item is provisioned. Returned `plm_id` written back to `Deliverables_<customer_id>` SP row via `SpClient` digest dance per `[D-117]`.
- (Q2) `closePLM` fires from `workflow_engine.tasks.milestone.FINAL_SWEEP` ActionKind when milestone closure cascade completes.
- (Q3) PLM polling cadence is deadline-tiered per FR-23-style pattern (as milestone deadline nears, polling frequency increases). Default ladder (configurable): >14 days = 60 min; 7-14 days = 30 min; 3-7 days = 15 min; <3 days = 5 min; deadline-day = 1 min. Applied via `corp_plm_poller` per active `DeliveryItem` with `plm_id` set + `delivery_state ∈ {Open, OutreachSent, DocumentReceived, OwnerClosed}`.
- (Q4) `tpm_corp_id` is the local part of `Projects.TPM` work email (read from `Projects_<customer_id>` SP row per `[D-088]` 3-tuple lookup). PER-CUSTOMER (NOT the shared HILDA ops-team identity per `[D-019]`); attribution parameter only, not credential. e.g., `Projects.TPM = "abc@corp.com"` → `tpm_corp_id = "abc"`. Actual auth flows via `corp_plm_gateway` PC per FR-25 (a) no-credential pattern.
- (Q5) Error handling Ph-1: retry with exponential backoff + log `ITR-W004` opaquely. After N=5 failed retries: notify HILDA OPS alert -- mechanism TBD architect discussion. Ph-2: detailed error code mapping.
- (Q6) HILDA tracks in-flight downloads per `(plm_id, file_id)` to prevent duplicate concurrent calls. Per `InFlightDownloadTracker` -- `asyncio.Lock`-guarded dict Ph-1 (Postgres-backed Ph-2 for restart resilience).
- (Q7) BOTH `document_id` AND `file_id` required for `downloadFile`. Both persist on HILDA's `DocumentIndexRow` per file.

**Why**:
- (a) **Thin wrapper over reimplement** -- corp PLM client services are already available + battle-tested in the corp environment; rewriting in HILDA is redundant + slower delivery + duplicates auth complexity.
- (b) **`[D-027]` Teacher/Student boundary preserved** -- proprietary API binding details stay on Work PC; HILDA's public scaffold carries only the abstract Protocol contract + standard discipline (retry / in-flight / CommunicationLog audit).
- (c) **tpm_corp_id as attribution-not-credential** -- corp PLM API accepts tpm_corp_id as ACTION ATTRIBUTION (recorded in PLM as the actor); HILDA's actual auth flows via corp_plm_gateway PC per `[D-019]` no-credential pattern. Decouples HILDA's identity model from corp PLM's auth scheme; per-customer TPM identity flows naturally through Projects.TPM column.
- (d) **In-flight tracking per Q6** -- with 5-min polling cadence on near-deadline items + occasional slow downloads (500MB+ files), two concurrent polls can both kick off download for same file. HILDA-side dedup via file_hash dedup in DocumentIndexRow is the *eventual* safety net; in-flight tracking is the *concurrent* safety net.
- (e) **Bundle vs separate ADRs** -- the 7 sub-decisions are tightly coupled (Q1 + Q2 share the workflow_engine + sharepoint_integration write-back path; Q3 + Q6 share the polling architecture; Q4 + Q5 share the gateway + error-handling pattern). Splitting would fragment the operational story.

**Consequences**:
- (a) `issue_tracker/corp_plm/adapter.py` (HILDA scaffold) raises `ITR-E001` by default; production deployment REQUIRES Cline to land concrete subclass on Work PC with the 5 binding calls. Tests use `MockCorpPlmAdapter` end-to-end without binding.
- (b) `tpm_corp_id` derivation lives in `issue_tracker.utils.derive_tpm_corp_id(projects_tpm_email)` (strip domain via split-on-`@`). Used by `workflow_engine.tasks.lifecycle.PROVISION_TRACKER` ActionKind body + `corp_plm_poller`.
- (c) **HILDA OPS alert mechanism is TBD** -- `ITR-W004` is emitted but the OPS notification channel (email? messenger? dashboard alert pane?) is pending next architect discussion. Until then, ops monitors via `--diagnostic` mode + ITR-RPT logs.
- (d) `InFlightDownloadTracker` is in-memory Ph-1 -- not restart-resilient. If HILDA restarts mid-download, the in-flight key is lost; the second poll's downloadFile call may double-download. Ph-2 Postgres-backed `download_in_progress_at` timestamp on `DocumentIndexRow` solves this; noted in `issue_tracker/MODULE.md` Deferred.
- (e) **Deadline-tiered polling cadence amplifies near-deadline** -- ~1 poll/min on deadline-day means up to 1 binding call/min per active item. Combined with HILDA's expected Ph-1 load (single mock customer + modest milestone count), this is fine; under high-customer-count Ph-3+, consider tier flattening or cap.
- (f) `getdocumentslist` returns the FULL list every poll -- no "documents since timestamp" filter. HILDA computes new docs as `current_set - DocumentIndexRow_set`; this is O(N) per poll but fine for Ph-1 (typical milestone has <50 docs).
- (g) `customizations/issue_tracker/example_corp_plm_adapter.py` ships as a per-customer scaffold with `# TODO(cline)` markers showing the 5 binding-import + invocation patterns.

**Anchors**: FR-25 (a) (no-credential pattern), FR-26 (PLM polling + fan-out), FR-77 (HILDA-to-PLM upload via uploadFile), FR-68 (upload-success verification per `[D-098]` narrowing), `[D-019]` (shared ops-team identity Ph-1/Ph-2; corp PLM differs per Q4), `[D-027]` (Teacher/Student LLM scaffold split -- load-bearing for ownership boundary), `[D-088]` (3-tuple PM resolution from Projects.TPM), `[D-091]` (slug -> id rename), `[D-092]` (customer JIRA Ph-1 informational only), `[D-098]` (FR-68 hash-match dropped), `[D-117]` (SP NTLM digest-dance for plm_id writeback), `[D-118]` (SP UI engineer provisioning boundary -- HILDA does NOT create SP lists; PLM rows are HILDA-created), `issue_tracker/MODULE.md` 2026-06-25 cascade, commit `5c1ab7e`.

---

## D-121: Messenger module ownership boundary -- composition + send + daily-limit in messenger NOT email_service

**Date**: 2026-06-25
**Status**: Ratified

**Context**: HILDA's FR-10 cross-channel escalation reaches owners via corp messenger when email reminders don't yield response. Pre-2026-06-25 design had `core/src/email_service/outbound/composer_escalation.py` composing the escalation message + a future `messenger` module just sending it. Architect Q-M6 lock 2026-06-25 redirected: messenger module OWNS its own composition (Jinja2 templates) + send + daily-limit; email_service stays focused on the email channel only. Plus the architect locked sendMessage's operational constraints (Q-M1..M5) via the shared screenshot of the `bool sendMessage(owner_corp_id, message)` API.

**Decision**: Messenger module is its own module (separate from `email_service`) owning the corp messenger channel end-to-end:
- `core/src/messenger/protocol.py` -- `MessengerAdapter` Protocol (1 method: `send(owner_corp_id, message) -> bool`).
- `core/src/messenger/corp_messenger/adapter.py` -- `CorpMessengerAdapter` thin wrapper around the corp messenger gateway's `sendMessage` API per `[D-027]` Teacher/Student (abstract `_invoke_send_message` raises `MSG-E003` by default; Cline fills binding on Work PC).
- `core/src/messenger/composer.py` -- compose_escalation renders Jinja2 templates with bounded variable set + 4K-byte truncation per Q-M2.
- `core/src/messenger/daily_limit.py` -- DailyLimitChecker enforces ≤3 messages per `owner_corp_id` per day per Q-M2; queries `CommunicationLog` (channel=corp_messenger, direction=outbound, today's date) to count + blocks 4th send with `MSG-W001`.
- `core/src/messenger/service.py` -- MessengerService is the orchestrator (composes + daily-limit-checks + invokes adapter + audits + retries) -- the ESCALATE ActionKind's task body entry point.

Plus the architect Q&A locks bundled here:
- (Q-M1) `sendMessage` returns true when owner RECEIVES message (not just gateway-accepted). HILDA can rely on bool semantics; no read-receipt callback needed Ph-1.
- (Q-M2) `message` ≤ 4000 bytes (composer truncates with `MSG-W002` if exceeded). Per `owner_corp_id`: ≤ 3 messages per day (DailyLimitChecker blocks with `MSG-W001`).
- (Q-M3) Sender appears as "anonymous HILDA BOT" -- no TPM identity prepended. Affects message composition: no preamble like "Hi, this is HILDA on behalf of TPM X" needed; template signs as `— HILDA BOT`.
- (Q-M4) Escalation trigger is rule-driven via AutomationRules (FR-31) + YAML config. Rule example: `name: escalate_to_messenger_after_2_reminders; trigger: ReminderSent; conditions: [reminder_count >= 2, days_to_deadline <= 3, delivery_state in [OutreachSent, OutreachReminded]]; actions: [ESCALATE: {channel: corp_messenger, reason: post_reminder}]`. Workflow: rule_engine evaluates -> workflow_engine ESCALATE ActionKind dispatches -> messenger.send_escalation.
- (Q-M5) CommunicationLog audit per FR-42 with bool-only outcome; no message_id for thread continuity Ph-1. Acceptable.
- (Q-M6) Messenger module OWNS composition (Jinja2 templates) + send + daily-limit -- NOT email_service.

**Why**:
- (a) **Single-responsibility** -- messenger as a channel ≠ email as a channel. Composing prose for corp messenger uses a different tone/length/template surface vs email (4K cap vs no cap; no subject vs subject; no thread vs threaded). Mixing in email_service muddies both modules' purposes.
- (b) **Composition near the channel** -- the 4K truncation rule + the HILDA BOT signature convention + the Jinja2 template surface live where the channel constraints live. If composition lived in email_service, those constraints leak across module boundaries.
- (c) **Daily-limit at messenger level** -- DailyLimitChecker queries CommunicationLog for SAME channel + SAME owner_corp_id today; logically belongs in the channel-owner module.
- (d) **Workflow_engine ESCALATE ActionKind has clean caller surface** -- task body calls `messenger.send_escalation(item, batch_id, reason, milestone_ctx) -> SendResult`. ESCALATE doesn't know about templates or daily-limits; it just dispatches.
- (e) **anonymous HILDA BOT over per-TPM identity** -- corp messenger gateway doesn't expose per-TPM "from" attribution at the gateway layer; messages flow from a single bot account. Per-TPM impersonation would need additional gateway plumbing + auth tokens; out of Ph-1 scope.
- (f) **bool-only return without message_id Ph-1** -- corp messenger gateway returns bool only; no message-id callback. HILDA can't reference the sent message for thread continuity. Acceptable Ph-1 because escalation is one-shot (no follow-up messages in the same thread); Ph-2 could add message_id support if gateway adds it.

**Consequences**:
- (a) `core/src/email_service/outbound/composer_escalation.py` is now VESTIGIAL -- Ph-1 stub dict was kept compatible until messenger landed; **remove in follow-up sweep next session**. (Flagged in STATUS Next.)
- (b) workflow_engine.tasks.escalation.ESCALATE ActionKind task body delegates to `messenger.service.MessengerService.send_escalation`. Existing ESCALATE registration in workflow_engine ActionKind enum was already in place; just rewires the call site.
- (c) `customizations/messenger/example_corp_messenger_adapter.py` ships as a per-customer scaffold with `# TODO(cline)` markers per `[D-027]`.
- (d) `CommunicationLog.channel = 'corp_messenger'` joins the existing channel enum; daily-limit check is a SELECT COUNT query gated by this channel value.
- (e) 4K truncation + 3/day cap are HARD constraints at messenger boundary; never bypassed via config flags (no "emergency send override"). Operationally: if ops needs to send urgent message beyond limits, ops manually messages out-of-band; HILDA doesn't have an override path.
- (f) **No retry on bool=False from gateway** -- adapter raises if exception occurs, but bool=False means gateway responded successfully with "failure to deliver"; retrying would re-send (could create duplicate notifications). Bool=False is final.
- (g) Template variables documented in `REQUIRED_TEMPLATE_VARS` and asserted at composition time -- changing template requires either (i) including all REQUIRED vars OR (ii) updating the REQUIRED set.

**Anchors**: FR-9 (outreach), FR-10 (reminders + cross-channel escalation), FR-31 (rule-driven), FR-42 (CommunicationLog audit), FR-50 (corp messenger outbound -- previously deferred to Ph-2 in early-2026 sessions; now Ph-1 per Q-M6 lock), `[D-019]` (shared HILDA ops-team identity Ph-1/Ph-2; HILDA BOT identity here), `[D-025]` (config 3-tier), `[D-027]` (Teacher/Student LLM scaffold split), `[D-042]` (vestigial / superseded -- FR-54 corp messenger inbound replies deferred Ph-2 per architect direction 2026-06-19; superseded by `[D-092]` framing), `messenger/MODULE.md`, commit `04d3e8d`.

---

## D-122: FR-87 step A/B/C TPM resolution is HILDA-rendered-page direct POST -- NOT SP-alert mediated, NOT rule_engine triggered

**Date**: 2026-06-25
**Status**: Ratified (correction applied; cascade pending next session per STATUS Flag)

**Context**: FR-87 has 3 TPM resolution steps: (A) Reassign work-item; (B) Resolve doc_type; (C) Resolve revision. Multiple sessions of MODULE.md design + this session's email_service + automation_rules.yaml work assumed the flow was: TPM clicks SP UI button → SP alert email lands in HILDA → `sp_alert_parser` extracts the change → rule_engine fires a trigger → workflow_engine dispatches action. The email_service `sp_alert_parser/parser.py` was built with `tpm_reassign_to_workitem` / `tpm_resolve_doc_type` / `tpm_resolve_revision` action handlers; the automation_rules.yaml had 3 FR-87 rules using `ItemModified.TagsModified` as a workaround trigger sub-type. Architect's Q3 clarification 2026-06-25 corrected this fundamentally: **FR-87 step A/B/C resolution occurs within HILDA's INTERNAL TAB BROWSER PAGE (the dashboard FR-57 page that opens in a new tab from SP via `[D-074]` link-out), NOT in SP context.**

**Decision**: FR-87 step A/B/C TPM resolution buttons live on `core/src/dashboard/`'s HILDA-rendered page (per `[D-074]` link-out from SP). The flow is:
1. TPM opens HILDA's dashboard page via SP link-out: `hilda.corp/docs/<delivery_item_id>` per FR-57.
2. The HILDA-rendered page shows the document section + 3 FR-87 resolution buttons (A reassign; B resolve doc_type; C resolve revision) when applicable.
3. TPM clicks a button → POST to `hilda-api` endpoint (NOT SP write, NOT SP alert email).
4. Dashboard's POST handler (in `core/src/dashboard/app.py`) updates `storage` (DocumentIndexRow + DocumentItemAssociation + DeliveryItemBase as applicable) AND writes the resolution fields back to SP `Deliverables_<customer_id>` row via `SpCrud` digest dance per `[D-064]` + `[D-117]` writeback.

NO `sp_alert_parser` handler dispatch. NO `rule_engine` trigger sub-type. NO `workflow_engine` ActionKind for FR-87 specifically. FR-87 is purely a dashboard-owned direct-POST flow.

**Why**:
- (a) **HILDA-rendered page already exists per `[D-074]` link-out** -- the FR-57 document section page is HILDA's own surface; adding 3 POST endpoints is the minimum-surface-area path.
- (b) **TPM ergonomics** -- TPM is already on the HILDA page when reviewing the doc; round-tripping the resolution through SP + SP-alert + email-poll adds 60-300s latency + multiple network hops + brittle parsing. Direct POST is instant.
- (c) **No SP write necessary for the action itself** -- the SP fields `tpm_reassignment_target_item_id` / `tpm_resolved_doc_type` / `tpm_revision_resolution` are *audit-only* per [D-068] impl note for SP-side audit pattern. HILDA's own storage is the source of truth; SP writeback is for SP UI visibility.
- (d) **rule_engine doesn't need a new trigger sub-type** -- the workaround `ItemModified.TagsModified` had loose semantics (would fire on any tag edit, not just FR-87). Avoiding the rule_engine trigger entirely is cleaner than adding new sub-triggers (`TpmReassigned` / `TpmResolvedDocType` / `TpmResolvedRevision`).
- (e) **email_service / sp_alert_parser stays simpler** -- removing 3 FR-87 action handlers shrinks sp_alert_parser's responsibility surface to the original `[D-047]` scope (entity-change SP-alert routing only).
- (f) **dashboard module already owns FR-57 / FR-61 / FR-87 button info display** -- placing the POST handlers there keeps FR-87 ownership in one module.

**Consequences**:
- (a) **CORRECTION CASCADE PENDING NEXT SESSION** (~1-2 hr focused work; flagged TOP PRIORITY in STATUS Next):
  1. Remove 3 FR-87 rules from `customizations/rules/global/automation_rules.yaml`.
  2. Remove FR-87 handlers (`tpm_reassign_to_workitem` / `tpm_resolve_doc_type` / `tpm_resolve_revision`) from `core/src/email_service/sp_alert_parser/parser.py`.
  3. Update `core/src/email_service/MODULE.md` to drop FR-87 references in `sp_alert_parser` narrative (keep only the `[D-047]` original scope).
  4. Add 3 POST endpoints to `core/src/dashboard/app.py`:
     - `POST /docs/<delivery_item_id>/resolve_reassign` (step A)
     - `POST /docs/<delivery_item_id>/resolve_doc_type` (step B)
     - `POST /docs/<delivery_item_id>/resolve_revision` (step C)
  5. Handlers: validate inputs (especially step B value MUST be in 4-value `tpm_resolved_doc_type` set per `[D-119]`; step A target item must exist; step C verdict must be in `{NEW_DOCUMENT, REVISION_OF}`) → update storage → write back to SP via SpCrud per `[D-064]` + `[D-117]` digest dance.
  6. Update `core/src/dashboard/MODULE.md` to document FR-87 POST routes as part of dashboard's Public surface.
  7. Update tests (`test_email_service.py` removes FR-87 handler tests; `test_dashboard.py` adds 6+ tests covering FR-87 POST happy paths + validation failures).
- (b) **A→B→C strict ordering invariant preserved** -- FR-87's per-doc resolution ordering (step A reassignment MUST complete before step B doc_type resolution before step C revision resolution per FR-87 lock) is enforced at the dashboard POST handler level via storage state checks, not via SP-alert ordering.
- (c) **[D-119] `tpm_resolved_doc_type` 4-value validation lives in the POST handler** -- HILDA-rendered page already constrains TPM choice to the 4-value set via the UI; POST handler validates as defense-in-depth.
- (d) **No new rule_engine trigger sub-types or ActionKinds** -- avoids the trigger-taxonomy churn that the `ItemModified.TpmResolved*` route would have caused.
- (e) **Backward-incompatible removal** -- the FR-87 action handlers in `sp_alert_parser/parser.py` shipped in commit `0dfb1d4` (this session). Removing them is technically a Public surface contraction but no caller exists yet (the SP-alert FR-87 path was never wired into workflow_engine), so removal is safe pre-production.
- (f) **dashboard now owns FR-87 lifecycle end-to-end** -- read (FR-57 + FR-87 button info display) + write (3 new POST endpoints). Single-module responsibility.

**Anchors**: FR-57 (HILDA-rendered document section), FR-58 (Confirmation skip), FR-60 (review findings), FR-61 (mediated download), FR-74 (HILDA-tab POST + audit-only SP fields) per [D-068] / [D-077], FR-87 (TPM resolution A→B→C strict ordering), `[D-047]` (SP alert email channel -- sp_alert_parser ORIGINAL scope), `[D-064]` (HILDA → SP REST writeback channel), `[D-068]` (audit-only SP fields for TPM resolutions), `[D-074]` (HILDA-rendered link-out + per-load READ), `[D-117]` (SP NTLM digest-dance for FR-87 POST writeback), `[D-119]` (tpm_resolved_doc_type 4-value Choice), `dashboard/MODULE.md` (will be updated in next-session correction cascade), `email_service/MODULE.md` (will drop FR-87 references in next-session correction cascade), `automation_rules.yaml` (3 FR-87 rules will be removed in next-session correction cascade), commit `0dfb1d4` (email_service handlers landed; to be removed), commit `1caa106` (FR-87 rules landed; to be removed).

---

## D-123: Dashboard `/docs/<customer_id>/<sp_id>` URL + per-load SP READ + no caching + no per-PM auth Ph-1

**Date**: 2026-06-26
**Status**: Ratified

**Context**: The dashboard module's "View Documents" page (FR-57/61) is the TPM-facing surface that opens in a new browser tab when TPM clicks the SP UI engineer's `<a href>` link per `[D-074]` link-out architecture. The original dashboard Ph-1 dev (commit `dc31949`) used a single-segment `/docs/<delivery_item_id>` route + read documents only from HILDA storage + no SP READ at page-load. Yesterday's compliance review surfaced 5 gaps in this design vs the SP UI engineer's actual link convention + per-load READ requirement per `[D-074]`. Architect Q1-Q3 + Q9 locks 2026-06-26 (after running live SP REST probe) resolved all gaps.

**Decision**: Dashboard's "View Documents" flow bundles 4 sub-decisions:
- (a) **URL pattern**: `GET /docs/{customer_id}/{sp_id}` — 2-segment (was single-segment). `<customer_id>` resolves the SP list name (`Deliverables_<customer_id>`); `<sp_id>` IS SP's `Id` auto-counter PK = HILDA's `delivery_item_id` (same integer, same key). SP UI engineer emits links as `https://proxy-hilda.net/docs/<customer_id>/<sp_id>`.
- (b) **Per-load SP READ**: every `GET /docs/...` call fetches the canonical Deliverables row fresh from SP via `SpCrud.get_item(entity="delivery_items", scope=ListScope(customer_id), item_id=sp_id)` per `[D-074]` Variant A. Returns sp_row that drives: FR-58 Confirmation skip (`item_type == "Confirmation"`); FR-60 review findings rendering; FR-87 button surface (when staged); freshness indicator (`Modified` timestamp).
- (c) **No caching Ph-1**: every page load triggers 1 SP REST call. SP handles ~20/min during a TPM's busy day fine. Caching Ph-3+ if telemetry justifies.
- (d) **No per-PM authorization Ph-1**: all authenticated TPMs (via Kerberos through corp reverse proxy + `X-Authenticated-User` header per `[D-114]` + `[D-115]`) can read all customers. Per-PM access narrowing is Ph-2/Ph-3+ via Vault per `[D-019]` v2.
- (e) **`Projects.TPM` User-field via `$expand`** per Q9: HILDA fetches `?$expand=TPM&$select=Id,Title,TPM/EMail,TPM/Title` → response carries nested `{"TPM": {"EMail": "abc@corp.com", "Title": "..."}}`. HILDA derives `tpm_corp_id = row["TPM"]["EMail"].split("@")[0]` per `[D-088]` 3-tuple. `SpClient.get_list_items` + `SpCrud.get_item` got `expand` + `extra_select` kwargs (commit `2f791d6`).

**Why**:
- (a) **URL pattern matches SP UI engineer's link convention** — single-segment route would 404 against the natural `<a href="/docs/<customer_id>/<sp_id>">` rendering. Plus `<customer_id>` in the URL gives HILDA the list-scope it needs without round-trip lookups.
- (b) **Per-load READ over local-cache** — TPM expects fresh state (e.g., another TPM just updated `delivery_state`; the row's audit fields just changed). HILDA storage doesn't necessarily reflect SP-side TPM edits (e.g., owner-status note edits go through email_service's sp_alert_parser asynchronously). The authoritative state is SP at click time.
- (c) **No caching Ph-1 over even a 5-minute cache** — under Ph-1 single mock customer + modest TPM concurrency, the SP load is negligible. Caching adds correctness risk (stale-while-edit windows) for marginal perf gain.
- (d) **No per-PM auth Ph-1 over PR-style per-PM ACL** — Ph-1 has a single mock customer; per-PM access is operationally unnecessary. Adds DB schema + cross-cutting permission middleware for Ph-1 marginal value. Vault-backed per-PM ACL lands Ph-3+ per `[D-019]` v2.
- (e) **`$expand` for User fields over `/_api/web/siteusers(<id>)` two-step** — single REST request; OData-native; no extra round-trips. `SpClient` kwarg-additive change (backward-compatible).

**Consequences**:
- (a) `core/src/dashboard/app.py` `GET /docs/{customer_id}/{sp_id}` (2-segment) per commit `f8289f2`.
- (b) `SpCrud.get_item` added in same commit; called by `get_document_section`.
- (c) `build_app` takes `sp_crud=None` for test mode (falls back to `app.state.mock_sp_rows`) — production deploy injects real `SpCrud`.
- (d) Dashboard `_fetch_sp_row()` raises HTTP 404 if SpCrud returns None (row doesn't exist in SP).
- (e) FR-58 Confirmation detection now authoritative via `sp_row["item_type"]` (was heuristic "no docs → Confirmation"; commit `f8289f2` Gap 6).
- (f) Pagination `$top + __next` continuation works (resolves `[D-117]` Q4 OPEN architectural question — confirmed by architect's live probe).
- (g) SP cookie capture returns None in this corp SP deployment — NTLM auth flows through `requests.Session` per-request anyway; SpSession tolerates gracefully.
- (h) `[D-122]` FR-87 POST endpoints (step A reassign + step B doc_type) wire to SP audit-field writeback via SpCrud per `[D-064]` + `[D-117]` digest dance. Step C (revision resolution) explicitly NOT routed Ph-1 per `[D-039]` Step 2 + architect direction.
- (i) Dashboard test count 20 → 37 (+17 new tests covering Gap 1+2+6+7+8).

**Anchors**: FR-57 (document section), FR-58 (Confirmation skip), FR-60 (review findings), FR-61 (mediated download), FR-87 (TPM resolution), `[D-019]` (shared HILDA ops-team identity Ph-1/Ph-2), `[D-039]` (revision determination Step 1 deterministic + Step 2 LLM Ph-2), `[D-064]` (HILDA → SP REST writeback), `[D-074]` (link-out + per-load READ), `[D-088]` (3-tuple PM resolution), `[D-114]` (header-trust auth), `[D-115]` (dashboard-local auth middleware), `[D-117]` (SpSession NTLM digest dance), `[D-119]` (`tpm_resolved_doc_type` 4-value), `[D-122]` (FR-87 direct-POST architecture), `dashboard/MODULE.md`, `sharepoint_integration/sp_client.py` + `list_crud.py`, commit `f8289f2` + commit `2f791d6`.

---

## D-124: `DeliveryState` enum value strings match SP display (PascalCase + spaces) -- direction (α)

**Date**: 2026-06-26
**Status**: Ratified

**Context**: HILDA's canonical `DeliveryState` enum (in `core/src/template_schema/enums.py`) originally used SCREAMING_SNAKE_CASE both for Python attribute names AND value strings (e.g., `OUTREACH_SENT = "OutreachSent"` no-space PascalCase + variations). The SP UI engineer's actual SP Choice column values are PascalCase with spaces (e.g., `"Not Started"`, `"OutreachSent"`, `"OwnerClosed"`) per architect's live SP REST probe 2026-06-26 (showed `delivery_state = "Not Started"`). The mismatch meant HILDA's runtime evaluator (rule_engine condition matching) would never fire against real SP data.

**Decision**: Update `DeliveryState` enum **value strings** to match SP display verbatim — PascalCase + spaces. Python attribute names stay SCREAMING_SNAKE_CASE (Python convention). Direction (α):

```python
class DeliveryState(str, Enum):
    NOT_STARTED            = "Not Started"
    OPEN                   = "Open"
    OUTREACH_SENT          = "OutreachSent"
    DOCUMENT_RECEIVED      = "DocumentReceived"
    OWNER_CLOSED           = "OwnerClosed"
    UNDER_PM_REVIEW        = "UnderPMReview"
    READY_FOR_SUBMISSION   = "ReadyForSubmission"
    SUBMITTED_TO_CUSTOMER  = "SubmittedToCustomer"
    CLOSED                 = "Closed"
    DELAYED                = "Delayed"
    BLOCKED                = "Blocked"
```

Cascade: `customizations/rules/global/automation_rules.yaml` condition values updated to match (e.g., `delivery_state in [OutreachSent, ...]` not `[OutreachSent, ...]`). All test files updated to match. 4 test files touched + 1 production code file.

**Why**:
- (a) **Single source of truth** -- SP UI engineer's Choice column values are the operational truth (TPMs see + edit them in SP UI). HILDA's enum should mirror, not diverge.
- (b) **Matches the same pattern as `[D-094]` SUPERSEDED 2026-06-23 item_type lock** -- short-label categories Confirmation/Default PascalCase; long names snake_case. DeliveryState extends the same discipline: enum value strings = SP display strings.
- (c) **Direction (α) over (β) translation layer** -- a translation layer (`SP_TO_CANONICAL_STATE = {"Not Started": "OPEN", ...}`) at the SP-READ boundary adds an indirection HILDA developers must remember + maintain. Direction (α) removes the indirection entirely.
- (d) **rule_engine condition matching works out of the box** -- rules can reference `delivery_state in ["OutreachSent", "DocumentReceived"]` and match runtime events directly.
- (e) **Avoids future drift between HILDA enum values + SP Choice values** -- when SP UI engineer adds a new state (e.g., "Awaiting Approval"), HILDA enum mirrors verbatim; no mapping table sync.

**Consequences**:
- (a) `core/src/template_schema/enums.py` `DeliveryState` value strings updated (commit `f8289f2`).
- (b) `customizations/rules/global/automation_rules.yaml` condition values updated to PascalCase-with-spaces; orphan values OwnerResponseReceived / OutreachReminded / AIReviewed removed in same cascade (commit `057b33d`).
- (c) 4 test files updated for new string values: `test_template_schema.py` + `test_tracker.py` + `test_workflow_engine_tasks.py` + `test_dashboard.py`.
- (d) String values containing spaces require quoting in YAML (`value: "OutreachSent"` not `value: OutreachSent`) -- caught + fixed during cascade.
- (e) `[D-094]` SUPERSEDED + `[D-119]` `tpm_resolved_doc_type` (4-value lowercase snake_case for HILDA-owned page) precedents both confirm: enum value strings = SP-display strings where SP UI is the authoritative surface; canonical lowercase snake_case where HILDA owns full read+write (FR-87 page).
- (f) No translation layer at SP-READ boundary -- HILDA's evaluator reads `row["delivery_state"]` and matches enum values directly.
- (g) Future SP-side Choice value addition: HILDA architect adds the new enum member with matching value string; no broader cascade.

**Anchors**: FR-7 (state machine), FR-28 (state transition triggers), `[D-094]` SUPERSEDED (item_type mixed-case lock 2026-06-23 -- same precedent for direction α), `[D-119]` (`tpm_resolved_doc_type` 4-value lowercase for HILDA-owned surface), `template_schema/enums.py` `DeliveryState`, `automation_rules.yaml` condition values, commit `f8289f2` + commit `057b33d`.

---

## D-125: Point 3 policy -- corp-SP-derived YAML mappings stay LOCAL; public github gets sanitized placeholder

**Date**: 2026-06-26
**Status**: Ratified

**Context**: The customer.yaml (was mock_customer.yaml) file in `customizations/sharepoint_config/customers/` is the SP-integration column-name translation table (HILDA canonical → SP internal). Per `[D-027]` Teacher/Student precedent, corp-proprietary content (binding code) stays out of public github. Architect raised 2026-06-26 that this principle also applies to YAML files containing SP column mappings derived from the corp SP probe -- column names + structure derived from the architect's live corp environment shouldn't go to public github even if values use placeholder identifiers, because the column-name set itself reflects corp design decisions.

**Decision**: Three-tier policy for `customizations/` content boundary:
- **(a) Architecture YAML / rule_engine YAML** (HILDA logic; no corp data) → safe for public github. Examples: `automation_rules.yaml`, `defaults.yaml`. Architect-written.
- **(b) Corp-SP-derived YAML mappings** (e.g., `customer.yaml` SP column maps, future per-customer template overrides reflecting real customer schemas) → **stay LOCAL**. Architect maintains the production version on the Linux deployment box. Public github copy is a privacy-clean SAMPLE / RUNTIME PLACEHOLDER with placeholder identifiers (`mock_customer` / MODEL-A / etc.) + header note documenting Point 3 policy.
- **(c) HILDA Python code** (`SpClient` enhancements, dashboard handlers, etc.) → safe for public github. Corp-API binding bodies filled in by Cline on Work PC per `[D-027]`.

For (b) workflow: I (Claude) generate corrected YAML content in chat → architect copies/pastes to local + deploys → I do NOT commit corp-derived content to public github. Public github customer.yaml stays as sanitized placeholder showing the YAML SHAPE with placeholder identifiers + header policy note.

**Why**:
- (a) **Defense-in-depth on NFR-2 + `[D-027]`** -- even sanitized placeholder values can leak corp design through column names + structure (which fields exist, which are Required, what types). Corp SP UI engineer's design choices are reflected in the column inventory.
- (b) **Architect-controlled deployment gate** -- production YAML lives on the architect's Linux box; architect controls when SP UI engineer changes flow into HILDA runtime. Public github customer.yaml is a Ph-1 reference sample, not a deployment artifact.
- (c) **Distinct from architecture YAML** -- rule_engine `automation_rules.yaml` is HILDA-architectural (state machine + outreach cadence + escalation logic); not derived from corp data. Stays in github for ADR-style traceability.
- (d) **Workflow cleanliness over git-side ACL** -- alternative would be a private fork of `customizations/` or `.gitignore` patterns. Both add operational complexity + drift risk. Chat-generation + manual deploy is simpler + already aligns with `[D-027]` Teacher/Student pattern.

**Consequences**:
- (a) `customizations/sharepoint_config/customers/mock_customer.yaml` renamed → `customer.yaml` (single file per deployment per architect Q1 lock 2026-06-26).
- (b) Header note added: "production customer.yaml lives LOCALLY only (not on public github); architect maintains the corp-SP-derived column-name mappings on the Linux deployment box".
- (c) v1 of customer.yaml (yesterday's Tier 1 P0 YAML batch) had `_x0020_` encoding bugs from my guessing -- left in github as a runtime placeholder; corrected v2 generated in chat for architect's local deployment per Point 3.
- (d) Production customer.yaml on Linux deployment box: incorporates architect's live SP probe column inventory + the post-2026-06-26 corrections (form-factor flags dropped, customer_delivery_credential_id removed, customer_delivery_info added per-row, milestone_id dropped pending SP UI engineer correction, etc.).
- (e) Future corp-derived YAML files (e.g., per-customer `customizations/template_schemas/<customer_id>/template.yaml` with real customer data) follow the same pattern: I generate in chat → architect maintains locally → public github gets sanitized placeholder.
- (f) Test fixtures stay in public github -- `core/tests/fixtures/sharepoint_config/customers/test_customer.yaml` uses placeholder identifiers; doesn't derive from corp SP; safe for github.
- (g) `[D-027]` precedent honored -- Teacher/Student split extended from Python binding bodies (corp PLM / corp messenger / Google Drive adapters) to corp-SP-derived YAML mappings.
- (h) `customer_adapter/MODULE.md` D13 cascade follow-up (per-row `customer_delivery_info` per architect 2026-06-26) tracked via STATUS Flag -- when applied, the corrected YAML mapping for `customer_delivery_info` column lives on architect's Linux box, not in github.

**Anchors**: NFR-2 (no proprietary content), `[D-027]` (Teacher/Student LLM scaffold split), `[D-038]` (sops-encrypted credentials -- separate but related "credentials stay local" precedent), `[D-091]` (slug → id rename throughout), `[D-104]` (Projects per-customer), `[D-122]` (FR-87 direct POST), `customizations/sharepoint_config/customers/customer.yaml` (sanitized placeholder + Point 3 header), commit `2f791d6`.

---

## D-133: Layer-2 content-hash dedup for SpAlertParser (amends D-128 Message-ID dedup)

**Date**: 2026-06-26
**Status**: Ratified

**Context**: D-128 (2026-06-25) introduced Message-ID-based dedup for SP alerts: SpAlertParser holds an LRU+TTL set of RFC 5322 `Message-ID` headers, dropping any duplicate. The dedup catches SP server retries (same Message-ID re-delivered after timeout). Phase D2 EWS smoke test on corp Linux box 2026-06-26 against real corp Exchange + real corp SP surfaced a SECOND duplicate-source failure mode that Message-ID dedup can NOT catch: **corp SP fires duplicate alert emails with DIFFERENT Message-IDs but byte-identical payload**. Architect's smoke output captured two `Milestones - P1 added` alerts with Message-IDs `36e3047440b849b2b05ba3fbbe424913@seaex05` vs `41aca1c2a670415e8925db0711171bfd@seaex05`, identical received timestamp (`12:13:25+00:00`), identical routing_key, identical action_type, identical body_kvs -- same logical event, two emails. Without Layer-2 defense, HILDA would process the same event twice (rule_engine eval twice, action enqueue twice, potentially double-fire of SEND_OUTREACH / NOTIFY_NEW_OWNER / etc.).

**Decision**: Add Layer-2 content-hash dedup to SpAlertParser. SHA256 over canonical pipe-joined key from `(list_name, list_suffix, action_type, project_id, milestone_name, item_number, sorted(body_kvs))`. Stored in a second `_LruTtlSet` instance on the parser (same default bounds as Layer-1: 1024 entries + 10-min TTL). Checked AFTER all parsing succeeds but BEFORE returning `ParsedSpAlert`. Drop log line is `sp_alert_duplicate_content` (distinct from Layer-1's `sp_alert_duplicate_message_id`) for ops attribution.

**Why**:
- (a) **Two-layer defense over single-layer** -- Message-ID dedup catches one failure mode (retries with preserved Message-ID); content-hash catches another (duplicate-send with new Message-IDs). Both observed in real corp data 2026-06-26. Neither catches the other case.
- (b) **In-memory over Redis-persistent** -- Ph-1 single-worker per [D-026]; SP duplicate-sends arrive within seconds (well within 10-min TTL). Redis-backed dedup is Ph-2 cross-restart + multi-replica concern; deferred until production telemetry justifies. In-memory adds zero infra dependency.
- (c) **Content-hash over time-window** -- content-hash precisely identifies "same logical event"; time-window-based dedup would either be too narrow (miss late duplicates) or too wide (catch legitimate re-edits within the window). Hash gives correctness independent of arrival timing.
- (d) **Parser-level dedup over downstream idempotency** -- parser is the FIRST module where the duplicate is recognizable (after body parse extracts the canonical content). Dropping at parser avoids cascade waste (rule_engine evaluation, ActionKind dispatch, Celery task enqueue, etc.). Downstream idempotency is still a defense-in-depth concern but shouldn't be the only defense.
- (e) **SHA256 over MD5/SHA1** -- Python stdlib; collision-resistant for in-memory dedup; trivial perf cost; matches industry default. MD5/SHA1 would also work but no reason to use weaker.
- (f) **Same `_LruTtlSet` implementation as Layer-1** -- refactored `_MessageIdDedup` -> `_LruTtlSet` generic class; both layers use identical semantics. Reduces code duplication; documents that dedup is "set of strings with LRU+TTL eviction" regardless of what the strings represent.

**Consequences**:
- (a) SpAlertParser holds TWO `_LruTtlSet` instances (`_id_dedup` + `_content_dedup`). Memory ~80 KB per set at cap; ~160 KB total. Negligible.
- (b) Worker restart wipes both dedup states. Acceptable Ph-1 per [D-026] single-worker; documented as Ph-2 enhancement (Redis or Postgres for cross-restart tolerance).
- (c) Multi-replica worker scaling (Ph-3+ per [D-026] queue-filter pattern) would have each replica with its own dedup state -> could miss cross-replica duplicates. Tracked as Ph-2/Ph-3+ enhancement.
- (d) New test `test_dedup_drops_duplicate_content_different_message_ids` asserts Layer-2 behavior; existing `test_dedup_does_not_drop_different_message_ids` renamed to `test_dedup_does_not_drop_different_content_different_message_ids` with body fixture tweak so msg2 has different `milestone_id` (different content-hash).
- (e) Reusable pattern -- `_LruTtlSet` + content-hash dedup approach can be applied to other duplicate-source data quality issues (e.g., future ops_alerts events from same producer).
- (f) Drop log lines distinguished (`sp_alert_duplicate_message_id` vs `sp_alert_duplicate_content`) for ops attribution + Ph-2 telemetry.
- (g) D-128's existing Message-ID-only dedup narrative remains correct; this ADR EXTENDS D-128 with the second layer rather than superseding.

**Anchors**: `[D-026]` (single-worker single-instance Ph-1 topology), `[D-118]` (SP UI engineer provisioning boundary -- SP-side fix not feasible), `[D-128]` (Message-ID dedup -- extended here), `[D-132]` (EWS adapter that surfaced this -- Phase D2 production validation), commit `f3c5cc1`, smoke test output 2026-06-26.

---

## D-134: Real SP alert body format -- multi-space separator + dict-based routing_key extraction (amends D-128 narrative)

**Date**: 2026-06-26
**Status**: Ratified

**Context**: D-128 (2026-06-25) was the first cascade to capture the real corp SP alert format based on architect's 6 screenshots showing Milestone + Deliverable Add/Change/Delete bodies. D-128's `_BODY_KV_LINE_RE` required `[:=]` (colon or equals) between key + value, and `extract_routing_key` re-parsed the raw body with a separate MULTILINE regex. Phase D2 EWS smoke test 2026-06-26 against REAL corp Exchange + REAL corp SP data surfaced TWO format details D-128's screenshot-based analysis missed:
- (a) Corp SP CHANGE-alert bodies render NON-EDITED fields with multi-space separators (no colon) -- only the `Edited`-marked field keeps the colon. Example: `Title    P1\r\n\r\ncarrier    MMK\r\n\r\nproject_id    2350\r\n\r\n...\r\n\r\ntarget_date:    - 6/30/2026  Edited\r\n`. Result: D-128's colon-only regex matched ONLY the Edited line; body_kvs had 1 key instead of 7 on the Milestone CHANGE smoke test.
- (b) The two parsers in sp_alert_parser (parser.py for body_kvs, routing_key.py for project_id+milestone_name+item_number) used DIFFERENT regex strategies on the same body. The `routing_key.py` MULTILINE finditer pattern missed `project_id: 2350` on a 32-key Deliverable body that `parser.py`'s splitlines+match pattern correctly extracted -- divergent regexes giving divergent results on the same input.

**Decision**: Two-part fix capturing the real SP format:
1. **`_BODY_KV_LINE_RE` accepts BOTH separators**: existing `\s*[:=]\s*` (colon/equals) OR new `\s{2,}` (multi-whitespace, >=2 chars). The 2+ floor for the no-colon form rejects single inter-word spaces in header chrome (`Thendral Arasu Panneer Selvam`) preventing false-positive matches on names + sentences.
2. **`extract_routing_key` refactored to consume `body_kvs` dict**: signature changed from `extract_routing_key(body: str)` to `extract_routing_key(body_kvs: Mapping[str, str])`. parser.py already had body_kvs computed at the call site; passing it eliminates the divergent re-parse + works correctly for any body shape that `_parse_body` correctly parsed. Empty-string values treated as absent (SP encodes "no value" as `""` on optional fields per architect Q3 2026-06-27).

**Why**:
- (a) **Multi-space tolerance matches real corp SP output** -- observed empirically 2026-06-26; D-128's screenshot-based analysis missed it because the screenshots showed rendered Outlook HTML, not the EWS plain-text body. Real-data validation revealed the gap.
- (b) **`\s{2,}` lower bound over `\s+`** -- single inter-word spaces in header chrome would false-positive (e.g., `Thendral Arasu` would match `key=Thendral, value=Arasu Panneer...`). Architect's smoke test verified field labels use 3+ spaces; ordinary text uses single spaces. 2+ threshold gives safety margin.
- (c) **Dict-based routing_key over secondary regex** -- eliminates divergence between two parsers on the same input. `parser.py._parse_body` already produces a stable canonical body_kvs dict; routing_key extraction is a pure dict lookup over that. Single source of truth for body parsing.
- (d) **Case-insensitive dict lookup over case-sensitive** -- SP field names appear in mixed cases across alert types (Title vs lowercase others); aliases (project_id / ProjectID / Project_ID) merge cleanly with `lower()` normalization at lookup.
- (e) **Empty-string treated as absent** -- SP encodes "no value" as `""` on optional fields per architect Q3 2026-06-27. Treating `""` as a valid project_id would let HILDA emit TriggerEvents with empty routing keys downstream -- pointless. `_first_nonempty` helper makes the policy explicit.

**Consequences**:
- (a) `_BODY_KV_LINE_RE` regex updated; backward-compatible (colon-based bodies still match -- confirmed via existing test fixtures + ADD-alert smoke output).
- (b) `extract_routing_key` API signature changed from string -> dict. All in-tree callers updated (parser.py + tests). No external callers per grep.
- (c) New tests: `test_multispace_separator_milestone_change` (exact 7-key body from real corp data); `test_single_space_does_not_match_kv` (regression -- sentence-style names skipped); `test_routing_key_extractor_handles_aliases` (updated to dict input); `test_routing_key_extractor_empty_input`; `test_routing_key_extractor_empty_string_values_treated_as_absent`; `test_routing_key_extractor_32_key_deliverable_body` (regression -- exact 32-key Deliverable body).
- (d) The `sp_alert_missing_customer_id` warning no longer fires for Milestone CHANGE alerts that include the `carrier:` field in body (was always there; pre-fix parser couldn't extract it because of colon-only requirement).
- (e) D-128's narrative is amended: the alert body format is RICHER than the screenshot-based 6 samples suggested. Future format-discovery cascades should validate against EWS-fetched raw bodies, not Outlook-rendered screenshots.
- (f) Pattern reusable for other email body parsers if HILDA later integrates with services that have similar multi-format rendering quirks.

**Anchors**: `[D-047]` (SP alert email channel), `[D-128]` (sp_alert_parser real-format cascade -- amended here), `[D-132]` (EWS adapter that surfaced this -- Phase D2 production validation), `[D-118]` (SP UI engineer provisioning boundary -- fix is HILDA-side parser, not SP-side template change), commits `2f348d4` (dict refactor) + `9b5e62e` (multi-space separator), smoke test output 2026-06-26.

## D-135: [D-118] strict-boundary cascade -- HILDA as alert-driven listener; SP UI engineer owns ALL SP row creation

**Date**: 2026-06-28
**Status**: Ratified

**Context**: [D-118] (2026-06-23) introduced the principle that the SP UI engineer owns SharePoint row provisioning -- HILDA should not write `Milestones` / `Deliverables` / `Projects` rows. Pre-2026-06-26 implementation partially honored this: most rows were created via SP UI button handlers, BUT `tracker.instantiate_default_workitem` called `sp_writer.create_item` to write the Default WI row into `Deliverables_<customer_id>` per FR-78. Architectural discovery 2026-06-26 PM (during attempted "Setup Milestone" wiring): the symmetric `setup_milestone` task does NOT exist (only a placeholder comment in `tracker/default_workitem.py:125`), and the asymmetry between "Default WI created by HILDA" vs "all other rows created by SP UI engineer" was a design accident, not an intentional split. Architect direction 2026-06-26 PM: collapse the asymmetry -- SP UI engineer creates ALL SP rows (Milestones + Deliverables + Default WI); HILDA's role becomes purely alert-driven listener (import each Deliverable into HILDA local storage when SP fires ADDED alert; fire ItemCreated TriggerEvents when TPM clicks Start Collection). The 5-chunk implementation cascade landed across 2026-06-26 PM and 2026-06-27 + live-validated 2026-06-28.

**Decision**: HILDA never writes SP rows. The boundary is strict:
- SP UI engineer owns all CREATE on `Milestones`, `Deliverables_<customer_id>`, `Projects` (Ph-2).
- HILDA reacts to SP ADDED / CHANGED / DELETED alerts via `sp_alert_parser` + `apply_owner_reply_task` + ItemModified rule cascade.
- HILDA writes to SP only via best-effort field updates (delivery_state, owner_status_note) using two-step natural-key->_sp_id lookup per D-137 -- and these writes are explicitly best-effort (try/except, log + continue, no transaction rollback). Postgres is authoritative per [D-118]; SP UI rendering refreshes on next page load.

Implementation cascade (5 chunks):
- Chunk 1 (`62fa8ce`, 2026-06-26): Add `StorageWriter.create_delivery_item` Protocol method + Mock impls. Sets up the local-import path that replaces SP-create.
- Chunk 2 (`70b256a`, 2026-06-26): Add `IMPORT_DELIVERABLE_TRACKER` + `KICKOFF_COLLECTION` ActionKinds + stub task bindings in `core/src/workflow_engine/tasks/sp_alert_imports.py`.
- Chunk 3 (`69c1d68`, 2026-06-26 PM): Real `import_deliverable_tracker_task` body -- parses SP ADDED alert body_kvs into DeliveryItemBase, resolves device_id, idempotency via `find_items_by_natural_key`, calls `storage.create_delivery_item`.
- Chunk 4 (`5196a4a`, 2026-06-26 PM; rewritten 2026-06-28 `ddca41d`): Real `kickoff_collection_task` body -- filters by `force_tracking_enabled=true AND delivery_state="Not Started"`, groups by owner via Path A SP batch-read, sends ONE batch outreach email per owner with multi-row HTML table, transitions each item NS->Open->OutreachSent inline.
- Chunk 5 (`4ed2e0e`, 2026-06-26 PM): Remove `sp_writer.create_item` from `instantiate_default_workitem`. The Default WI row is now created by the SP UI engineer's "Setup Deliverables" button, same as every other Deliverable row.

YAML companion (architect-driven on Linux box): two new rules added to `customizations/rules/global/automation_rules.yaml` -- `import_deliverable_tracker_on_sp_add` (trigger=ItemModified + list_name=Deliverables + action_type=added) and `kickoff_collection_on_milestone_started` (trigger=ItemModified + list_name=Milestones + action_type=changed + field_deltas contains_key=milestone_collection_started_at).

End-to-end validation 2026-06-28: TPM "Setup Milestone" in SP UI -> SP fires ADDED -> HILDA logs (no action); TPM "Setup Deliverables" -> SP UI engineer creates 10 + 1 Default WI rows -> SP fires 11 ADDED -> HILDA imports 11 trackers; TPM "Start Collection" -> SP fires CHANGED with milestone_collection_started_at delta -> HILDA's kickoff_collection groups by owner + sends batch outreach + transitions all eligible items to OutreachSent. Live-validated against real corp Exchange + real corp SP for customer MMK on device SM-S671U1.

**Why**:
- (a) **Cleaner separation of responsibility** -- one writer (SP UI engineer) for all SP rows; one reader (HILDA) for all SP alerts. Eliminates the prior "HILDA writes some SP rows" asymmetry that confused both teams. SP UI engineer is the team with deep familiarity with SP REST quirks (column-type mappings, Person/Group field shapes, list-name capitalization); HILDA writing into SP introduces a second source of SP write traffic with different code paths and different failure modes.
- (b) **Idempotency moves to HILDA's local store** -- previously HILDA's "import this Deliverable" was conflated with "create the Default WI row in SP". Now both are clean: SP UI engineer creates ALL rows once (idempotency on SP UI engineer's side); HILDA imports each ADDED alert into local storage idempotently via `find_items_by_natural_key`. Re-firing of ADDED alerts (during SP retries, etc.) doesn't create duplicate trackers because HILDA short-circuits on natural-key match.
- (c) **Live-validatable** -- the strict boundary means we can test the SP-side and HILDA-side independently. SP UI engineer validates their CREATE flow via SP UI alone (no HILDA needed); HILDA team validates the listener flow via injecting test alerts (no SP-create-permission needed). Both teams unblock faster.
- (d) **Default WI is just another Deliverable** -- the Default WI row's only special property is that `item_type="Default"` triggers different routing in FR-78; its CREATE path is structurally identical to any other Deliverable. Treating it as such removes a special case from `tracker.instantiate_default_workitem` (which is now effectively a no-op for SP, only ensuring the local-storage row exists when the alert fires).
- (e) **Failure-mode containment** -- if HILDA's SP-write code ever has a bug, it can corrupt SP rows the SP UI engineer is also reading/editing. With HILDA write-out of the picture, HILDA-side bugs can only affect HILDA's local Postgres + audit; SP stays clean. (Best-effort field-update writes per D-137 are scoped to specific fields like `delivery_state` and `owner_status_note`; they cannot create or delete rows.)

**Consequences**:
- (a) `tracker.instantiate_default_workitem` no longer calls `sp_writer.create_item`; only ensures the local-storage tracker exists. Tests in `test_tracker.py` updated accordingly.
- (b) `core/src/workflow_engine/tasks/sp_alert_imports.py` is the canonical entry point for SP ADDED + CHANGED alerts driving HILDA state. Two task bodies (`import_deliverable_tracker_task` + `kickoff_collection_task`) plus their associated rules in `customizations/rules/global/automation_rules.yaml` form the alert-driven listener.
- (c) `ActionKind` enum grew 18 -> 20 (`IMPORT_DELIVERABLE_TRACKER` + `KICKOFF_COLLECTION`). Forward-compat: future SP alert action_types (e.g. PROJECT_DELETED) will add more ActionKinds rather than mutate existing flows.
- (d) `kickoff_collection_task` filter clause matters: items past Not Started skip eligibility (re-click of Start Collection after first run is idempotent, no duplicate emails); items with `force_tracking_enabled=false` skip eligibility (Default WI + any owner-disabled item). FR-58 correction during live test 2026-06-28: Confirmation items DO receive outreach -- the prior `item_type != Confirmation` skip was wrong; removed from `send_initial_outreach_on_collection_start` rule + kickoff_collection eligibility filter.
- (e) `Path A SP-read at fire-time` (D-DRAFT candidate from this window, not separately ratified) -- kickoff reads owner identity from SP rather than local storage so mid-flight TPM owner edits in SP are honored without HILDA-side replication. Trade-off: extra SP round-trip per kickoff vs always-fresh owner identity. Worth the cost given low kickoff frequency.
- (f) SP UI engineer coordination items pending: ADD `rules_paused: Choice(Yes/No)` column to Deliverables_<customer_id> per [D-108]; REMOVE vestigial `expected_completion_date` per [D-085]; CORRECT or REMOVE `milestone_id` integer column per [D-090] -- already in prior STATUS Flags, unchanged by this ADR.
- (g) MODULE.md updates required (carried to next session): tracker/MODULE.md Key choices updated 2026-06-28 (this commit); workflow_engine/MODULE.md Sub-modules tree should add `sp_alert_imports.py` + `owner_reply.py`; email_service/MODULE.md Sub-modules tree should add `body_parser_table.py` + `outreach_table.j2`.
- (h) Tests: 905/905 passing at HEAD with the 5-chunk cascade fully landed + Phase B owner-reply handler integrated. 7 stale tests from prior Step-5-Phase-A rewrites repaired + 2 new pre-kickoff coverage tests added.

**Anchors**: `[D-026]` (single-worker Ph-1 topology), `[D-064]` (HILDA -> SP REST writeback channel), `[D-080]` (owner identity field precedence), `[D-118]` (SP UI engineer provisioning boundary -- this ADR is the cascade-complete ratification), commits `62fa8ce` (Chunk 1), `70b256a` (Chunk 2), `69c1d68` (Chunk 3), `5196a4a` (Chunk 4), `4ed2e0e` (Chunk 5), `ddca41d` (Chunk 4 rewrite for multi-owner), `9538926` (outreach_table.j2 + ews_sender HTML wrap), STATUS Done entries 2026-06-26 PM + 2026-06-26 PM continuation + 2026-06-28.

---

## D-136: Owner-reply parser anchor = substring batch_id check (not literal `HILDA-BATCH-ID:` regex)

**Date**: 2026-06-28
**Status**: Ratified

**Context**: Phase B owner-reply handler (`core/src/email_service/inbound/body_parser_table.py`, commit `adf6630`, 2026-06-28) parses owner replies to HILDA's HTML-table outreach email. The original outreach template (`outreach_table.j2`) emits a tagged anchor `<p>HILDA-BATCH-ID: BATCH-<id></p>` so the inbound parser can confirm "this reply is to this specific outreach batch" before scanning for the editable table. Initial parser implementation used a literal regex `HILDA-BATCH-ID:\s*(BATCH-[A-Za-z0-9]+)` on the raw HTML to extract and validate the batch_id against the subject-derived value. Architect's live test 2026-06-28 against real corp Outlook reply consistently returned `unparseable` despite the diagnostic showing `body_html_len=15984 html_has_anchor=True html_has_table_tag=True html_has_batch_id=True` -- i.e., both the literal `HILDA-BATCH-ID` token AND the literal `BATCH-32100bde75` token were present in the body, but the regex failed to match them together. Two iterative attempts failed in production (commits `982003b` raw-HTML regex with widened separator; `af756f8` text-extracted regex via bs4 `get_text()`). Root cause discovered: Outlook reply rendering inserts arbitrary inline markup between the `HILDA-BATCH-ID:` label and the `BATCH-<id>` token (`<span style="mso-spacerun:yes">&nbsp;</span><o:p></o:p>`, sometimes mid-line wrapping, sometimes quoted-history content from the original email chain), and additionally Outlook can split the label and token across far-apart positions in the rendered HTML when re-quoting the original message. No regex with reasonable `[\s\S]{0,N}?` lookahead can both (a) catch all real-world Outlook variations and (b) avoid false-positive matches to unrelated occurrences elsewhere in the body.

**Decision**: Drop the literal `HILDA-BATCH-ID:` regex entirely. Replace with a plain substring check: `if batch_id not in body: return None`. The classifier already confirmed the SUBJECT carries `BATCH-<id>` (that's what made it OWNER_REPLY in the first place via `classifier.BATCH_ID_RE.search(subject)`); the body parser only needs to confirm the body references the SAME batch_id. Safety against stray-table matches in an unrelated reply is preserved by `_find_hilda_table_rows`, which iterates all `<table>` elements and requires the first row to have header cells matching `item_no` AND `status` (`owner_status_note` and `item_title` are optional). A body containing the batch_id but no qualifying table returns None at the table-finding stage.

**Why**:
- (a) **Real-world adversarial input** -- Outlook is not the only reply renderer; corp environments may also have Outlook Web, Outlook mobile, GMail, custom MUAs, mobile gateways. Each can mangle HTML differently. A regex tied to specific markup proximity is brittle; substring check is renderer-independent.
- (b) **Defense-in-depth is already there** -- the classifier subject check is the FIRST anchor (BATCH-id MUST be in subject for the message to be classified OWNER_REPLY at all); table-header validation is the THIRD anchor (must have item_no + status headers). The body anchor check is the MIDDLE layer; collapsing it from "regex match" to "substring presence" weakens it minimally because the surrounding layers compensate.
- (c) **Two prior attempts failed** -- commits `982003b` (text-extract regex with widened separator class) and `af756f8` (this decision) document the cost of attempting to make the regex robust. After two iterations failing live test, the simpler check is the right call.
- (d) **Cheaper to compute** -- substring check is O(n) on body length; regex with lookahead can be O(n^2) on adversarial input. Owner replies are bounded in size but the cost-benefit is one-sided.
- (e) **False-positive risk is bounded** -- the only way the substring check produces a false positive is: a body that is NOT a reply to our outreach but contains `BATCH-<id>` AND contains a qualifying HTML table with item_no + status headers. This is implausible without active adversary (and the classifier would have already rejected the message if the subject didn't carry BATCH-id). Internal HILDA-to-HILDA reply chains (e.g. PM forwarding) would correctly trigger the parser because they DO reference our batch.

**Consequences**:
- (a) `body_parser_table.py` simpler: no regex import for anchor; the `_BATCH_ANCHOR_RE` constant is removed. The regex was lines 50-53; replaced with substring check at the same site.
- (b) `outreach_table.j2` still emits the `<p>HILDA-BATCH-ID: {{ batch_id }}</p>` anchor -- it's still useful for the human reader and for downstream telemetry / log search ("find me all communications referencing BATCH-xxx"), even though the parser no longer treats it as a structural anchor. Removing the anchor from the template is a separate decision (not made here -- the anchor remains for human observability).
- (c) `test_returns_none_when_anchor_missing` test renamed to `test_returns_none_when_batch_id_absent_from_body` and rewritten to use a different rendered batch_id rather than removing the literal label. This is the only regression-test impact.
- (d) New test `test_outlook_mangled_anchor_still_parses` pins a body shape with `HILDA-BATCH-ID:<span mso-spacerun>&nbsp;</span><o:p></o:p>BATCH-32100bde75` plus a separately-positioned `BATCH-32100bde75` and asserts parse succeeds (commit `982003b` added the test; remains valid under D-136 substring check).
- (e) `_find_hilda_table_rows` becomes the load-bearing defense against false-positive table matches. It already does the right thing (header-content match required). If we ever introduce a new owner-facing email that also contains an HTML table with `item_no` + `status` headers, this parser would match against THAT table if it shared a batch_id -- but no such overlap exists in Ph-1 and unlikely in Ph-2. Tracked as a forward-compat consideration only.
- (f) Pattern generalizable: for inbound message parsers in proprietary-MUA-mangled bodies, prefer "substring presence + structural content match" over "regex on layout proximity". Reusable design heuristic for Ph-2 owner-reply table variants (Confirmation Yes/No template per FR-25(b)) and any other inbound HTML parsing.

**Anchors**: `[D-118]` (Phase B is owner-reply roundtrip atop the strict-boundary cascade -- see D-135), FR-12 (owner reply parser cascade -- path (a) extended from text-only to HTML table), commits `adf6630` (original parser with strict regex), `982003b` (text-extract attempt -- failed live), `af756f8` (substring-check decision -- this ADR), regression test `test_outlook_mangled_anchor_still_parses` in `core/tests/test_email_service.py`.

---

## D-137: HILDA -> SP field-write pattern = two-step natural-key -> _sp_id lookup before update

**Date**: 2026-06-28
**Status**: Ratified

**Context**: HILDA's `delivery_item_id` is a composite slug formed from `{customer_id}-{device_id}-{milestone_id}-{item_no}` (e.g. `MMK-SM-S671U1-P1-2`). SharePoint's row identity is an integer auto-counter `Id` (e.g. `42`) -- a separate naming dimension. The HILDA `delivery_item_id` is convenient for HILDA's audit trail + cross-module references (Postgres + audit + log lines all use it as a stable identifier across writes), but SharePoint's REST endpoint `/_api/web/lists/getbytitle('Deliverables_<customer_id>')/items({Id})` requires the integer `Id` for PATCH/UPDATE operations. Pre-2026-06-28, HILDA's only HILDA->SP write call site was `tracker.transitions.update_delivery_state` for state writeback; this passed HILDA's `delivery_item_id` directly to `sp_writer.update_item` and the write was wrapped in best-effort try/except (per [D-118]) -- failures logged a warning and did NOT block the Postgres-authoritative state change. Live observation 2026-06-28: those SP writes were silently failing in production (HTTP 400 from `int(item_id)` failing in `list_crud.update_item`), but nobody noticed because the best-effort path swallowed the error. Phase B introduced a SECOND HILDA->SP write call site: `owner_reply._write_note_only` for `owner_status_note` updates triggered by owner replies with status="Open" + non-empty note. Architect's live test 2026-06-28 explicitly required this note to land in SP (scenario (b)) -- so the silent failure pattern no longer worked.

**Decision**: HILDA->SP field-write call sites use a two-step pattern:
1. **Lookup**: `sp_writer.get_items(entity, scope, canonical_filters)` with the row's natural key (`item_no` + `milestone_id` + `project_model` for Deliverables; equivalent natural-key tuple for Milestones / Projects). Returns rows with `_sp_id` field populated from SP's `Id` column.
2. **Write**: `sp_writer.update_item(entity, scope, item_id=str(rows[0]['_sp_id']), canonical_fields={...})` with the SP integer Id from step 1 + the canonical field to update.

Best-effort semantics preserved per [D-118] / [D-064]: Postgres write happens FIRST (authoritative); SP write happens SECOND (advisory). SP write failure does NOT roll back the Postgres write. Failure modes logged in audit's `sp_error` field: `missing_customer_id_on_item` / `no_natural_key_filters_available` / `no_sp_row_matches_natural_key:{filters}` / `sp_row_missing_sp_id` / `<Exception>:<msg>`. Audit's `sp_written: true|false` flag is the single field downstream readers grep for to confirm SP propagation success.

Established implementation: `owner_reply._write_note_only` (commit `fed0b45`). Same pattern applies to `tracker.transitions.update_delivery_state` SP writeback when it's upgraded from current best-effort-with-known-failure to best-effort-with-actual-success (out of scope of this ADR; deferred Ph-1 follow-up).

**Why**:
- (a) **SP REST endpoint constraint** -- SharePoint's REST API uses integer `Id` for row addressing; no workaround at the REST layer. We must resolve to the integer before calling the endpoint.
- (b) **Natural key is the SP UI engineer's contract** -- per D-135 / [D-118] strict-boundary, SP UI engineer owns row creation. They guarantee `(item_no, milestone_id, project_model)` is unique per row in `Deliverables_<customer_id>`; HILDA can lookup by that tuple deterministically. The integer `Id` is SP's internal counter; we don't track it because we don't need to (each lookup re-resolves freshly, no stale-ID cache to invalidate).
- (c) **Two-step over caching the SP Id** -- alternative was: at import-time (commit `7553b51`), also store the SP `_sp_id` on the local DeliveryItem row, then HILDA->SP writes use the cached Id directly (one round-trip instead of two). Rejected because (i) requires a schema column addition + alembic migration + import-path change; (ii) introduces cache-staleness risk if SP rows are ever recreated with a new Id (corner case but real -- SP UI engineer might delete + re-create a row); (iii) extra round-trip per HILDA->SP write is negligible (Ph-1 volume is single-digit per minute; SP REST latency dominates either way). Two-step is simpler and avoids the cache.
- (d) **Failure surface narrower** -- the lookup step has its own failure mode (`no_sp_row_matches_natural_key`); if the natural key produces zero matches, we audit + abort cleanly without attempting an HTTP write that would 404. The prior direct-write code path didn't have this signal; failure manifested as HTTP 400 from list_crud's `int(item_id)` call.
- (e) **Best-effort semantics preserved** -- Postgres remains authoritative per [D-118]. The two-step ADDS robustness to the SP write attempt; it doesn't change the contract that SP write failure is non-fatal. The PM dashboard reads from Postgres, so the PM-visible state is correct regardless of SP propagation success.
- (f) **Pattern reusability** -- any future HILDA->SP field write (e.g. if Ph-2 ever needs HILDA to write deadlines, statuses, tag updates, etc.) follows the same two-step shape. Establishes a single mental model for HILDA->SP traffic.

**Consequences**:
- (a) `core/src/workflow_engine/tasks/owner_reply.py::_write_note_only` is the canonical reference implementation (commit `fed0b45`). Pattern: read item snapshot from Postgres -> build ListScope -> build natural-key filters from item's `customer_id` + `milestone_id` + `device_id` + `item_no` -> `sp_writer.get_items(...)` -> `sp_writer.update_item(..., item_id=str(rows[0]['_sp_id']))`.
- (b) Audit row structure for HILDA->SP writes:
    - `details.sp_written`: boolean -- true if the write succeeded, false otherwise.
    - `details.sp_error`: string when `sp_written=false` -- distinguishes the failure mode.
  Convention applies to owner_reply_note_written audit rows (and future HILDA->SP audit rows).
- (c) Pre-existing best-effort SP writeback in `tracker.transitions.update_delivery_state` (`a8dbb7e`, 2026-06-28) is unchanged by this ADR -- it still uses the direct-write pattern and silently fails. Upgrading it to D-137 two-step is a deferred Ph-1 follow-up (sized ~30 min); current state-transition SP writeback is functionally absent because the direct write 400s. Acceptable for Ph-1: PM dashboard reads from Postgres; SP UI engineer will add a "refresh from HILDA" button if needed in a future iteration.
- (d) New `SpCrudWriter.get_items(entity, scope, canonical_filters)` sync wrapper added in commit `fe9dd16` (2026-06-27) for Path A SP-read at fire-time in kickoff_collection; D-137 reuses this same method for the lookup step. No new methods needed.
- (e) Tests: `test_workflow_engine_tasks.py` and parser tests cover the success path; failure-mode audit fields (`sp_error`) are exercised via the integration tests when natural-key match fails (test path coverage to add when tracker.transitions upgrade lands).
- (f) Performance: 2 SP REST round-trips per HILDA->SP write (one GET + one PATCH). At Ph-1 volume (single-digit writes per minute, typically one per owner reply note), this is well within SP's rate budget. If Ph-3+ HILDA->SP write volume grows, caching `_sp_id` on the local row (rejected alternative above) is a viable optimization -- but not before telemetry justifies.

**Anchors**: `[D-064]` (HILDA -> SP REST writeback channel), `[D-118]` (SP UI engineer provisioning boundary -- HILDA->SP writes are field-only, never row-create), `[D-135]` (strict-boundary cascade -- this ADR is the pattern for HILDA->SP writes that the cascade allows), commits `fe9dd16` (get_items sync wrapper), `fed0b45` (_write_note_only two-step implementation -- canonical reference), `a8dbb7e` (tracker.transitions best-effort SP writeback -- unchanged here, upgrade deferred).

## D-138: `DeliveryState` enum value strings = SP UI Choice column verbatim (re-aligns D-124 intent after drift)

**Date**: 2026-06-28
**Status**: Ratified
**Supersedes**: `[D-124]` heading wording (Decision-block content was already correct)

**Context**: Architect screenshot 2026-06-28 of the SP UI Delivery State Choice column shows 11 values: `Blocked / Closed / Delayed / DocumentReceived / Not Started / Open / OutreachSent / OwnerClosed / ReadyForSubmission / SubmittedToCustomer / UnderPMReview`. Multi-word values are PascalCase-NO-space (`OutreachSent`, `OwnerClosed`, `UnderPMReview`, `DocumentReceived`, `ReadyForSubmission`, `SubmittedToCustomer`); the two values that ARE naturally two words in English use a space (`Not Started`); single-word values are plain (`Open` / `Closed` / `Delayed` / `Blocked`).

Pre-D-138, HILDA's `core/src/template_schema/enums.py` `DeliveryState` enum had **space-bearing** values (`"Outreach Sent"`, `"Owner Closed"`, etc.). This caused two production issues:
- (a) **State writeback to SP silently failed** for multi-word values: SP rejects the Choice value with HTTP 400 because `"Outreach Sent"` is not in SP's allowed Choice list (`OutreachSent` is). Combined with `[D-137]` deferred SP-writeback being already broken (architect found 2026-06-28 mid-PM-approval design pass; fixed in commit `f9542fa`), the entire HILDA-driven NS->Open->OutreachSent->OwnerClosed->UnderPMReview cascade was invisible in SP UI.
- (b) **SP-alert parser inbound flow** -- when an SP CHANGED alert delivered `delivery_state: OutreachSent` from SP, HILDA's local row got that value, but HILDA's own state-machine rules referenced `"Outreach Sent"` (space) for matching, causing mismatches in rule evaluation.

**Historical note on D-124 drift**: `[D-124]` (2026-06-26) was titled "DeliveryState enum value strings match SP display (PascalCase + spaces) -- direction (α)". The heading wording said "with spaces", but the Decision block code sample inside D-124 actually showed no-space PascalCase (`"OutreachSent"`, `"OwnerClosed"`, `"UnderPMReview"`, etc.). So D-124's INTENT was always no-space PascalCase matching SP UI; the title was a typo/misnomer. Sometime between D-124 ratification and this cascade, the enum file got drifted to space-bearing values -- likely via an over-literal reading of D-124's title rather than its Decision block. D-138 re-aligns enum + cascade with D-124's original Decision-block intent, and corrects the title-vs-body inconsistency by issuing a fresh ADR rather than amending D-124 in-place.

**Decision**: `DeliveryState` enum value strings match the SP UI Choice column verbatim:

```python
class DeliveryState(str, Enum):
    NOT_STARTED           = "Not Started"           # SP UI: "Not Started"
    OPEN                  = "Open"                  # SP UI: "Open"
    OUTREACH_SENT         = "OutreachSent"          # SP UI: "OutreachSent"
    DOCUMENT_RECEIVED     = "DocumentReceived"      # SP UI: "DocumentReceived"
    OWNER_CLOSED          = "OwnerClosed"           # SP UI: "OwnerClosed"
    UNDER_PM_REVIEW       = "UnderPMReview"         # SP UI: "UnderPMReview"
    READY_FOR_SUBMISSION  = "ReadyForSubmission"    # SP UI: "ReadyForSubmission"
    SUBMITTED_TO_CUSTOMER = "SubmittedToCustomer"   # SP UI: "SubmittedToCustomer"
    CLOSED                = "Closed"                # SP UI: "Closed"
    DELAYED               = "Delayed"               # SP UI: "Delayed"
    BLOCKED               = "Blocked"               # SP UI: "Blocked"
```

Per architect direction 2026-06-28: "at this time, SP UI list values cannot be changed - hilda to modify". HILDA aligns to SP UI; not the other way around. This treats the SP UI engineer's Choice column as the authoritative source per [D-118] strict boundary.

**Cascade scope**: 80 occurrences across 18 files updated by mechanical sed. Files touched:
- Production code (6): `template_schema/enums.py`, `tracker/transitions.py`, `storage/delivery_item_ops.py`, `workflow_engine/tasks/sp_alert_imports.py`, `workflow_engine/tasks/submission.py`, `email_service/MODULE.md` (narrative).
- MODULE.md docs (4): `dashboard/MODULE.md`, `email_service/MODULE.md`, `template_schema/MODULE.md`, `tracker/MODULE.md`.
- Tests (5): `test_storage_wireup_smoke.py`, `test_template_schema.py`, `test_tracker.py`, `test_workflow_engine.py`, `test_workflow_engine_tasks.py`.
- Customizations YAML (2): `customizations/rules/global/automation_rules.yaml` rule condition values; `customizations/rules/global/defaults.yaml` defaults.
- Compact docs (2): `docs/compact/DECISIONS.md` (this commit + back-references), `docs/compact/STATUS.md` (Done entries narrative).

908/908 tests passing post-cascade.

**Why**:
- (a) **SP UI is the operational source of truth** -- per [D-118] strict-boundary, SP UI engineer owns SP row provisioning + Choice column value enumeration. HILDA aligns to SP; not the other way around. If SP UI engineer ever changes the Choice values (e.g., for a new customer's installation), HILDA's enum is the cascade point -- single source.
- (b) **Direction (α) over translation layer** -- same rationale as [D-124] (e) holds: no per-write / per-read translation indirection at the SP boundary. The `SharePointListProvider` already does column-name translation; layering a value-map on top adds complexity that pays off only when multiple SP installations have divergent Choice values. Defer that to Ph-2 if/when a second customer surfaces a different Choice convention.
- (c) **Audit log readability trade-off acknowledged** -- `"UnderPMReview"` is slightly harder to skim than `"Under PM Review"` in audit logs; the trade-off is operational alignment > human readability. Future ops dashboards can format the value for display if needed.
- (d) **Path A over Path B** -- the alternative (keep HILDA's space-bearing enum + per-customer Choice value-map in `SharePointListProvider`) was rejected for Ph-1 because (i) single customer (MMK) deployment; (ii) value-map adds 2 new translation sites (read path + write path) with their own test surface; (iii) Ph-2 can introduce value-map cleanly if needed by adding `value_map: delivery_state: {"Outreach Sent": "OutreachSent"}` to `customer.yaml` and updating the provider to consume it.
- (e) **D-124 title-vs-body inconsistency resolved via fresh ADR** -- amending [D-124] in-place would lose audit history. D-138 supersedes the title wording and re-codifies what D-124's Decision block always said.

**Consequences**:
- (a) `core/src/template_schema/enums.py` enum value strings = SP UI Choice values verbatim.
- (b) `tracker.transitions.update_delivery_state` SP writeback (newly fixed in commit `f9542fa` per [D-137]) now writes the correct Choice value to SP -- HILDA-driven state transitions (NS->Open->OutreachSent->OwnerClosed->UnderPMReview->ReadyForSubmission->SubmittedToCustomer->Closed) propagate visibly to SP UI.
- (c) `sp_alert_parser` inbound: when SP CHANGED alert carries `delivery_state: OutreachSent` value, it matches HILDA's enum directly; no translation step. Rule conditions like `value: OutreachSent` in `automation_rules.yaml` match cleanly.
- (d) Audit log values now show no-space form (`{"to_state": "OutreachSent"}` rather than `{"to_state": "Outreach Sent"}`). Existing audit rows pre-D-138 retain their old form; ops queries spanning the cascade boundary need to look for both spellings during the transition window.
- (e) `customizations/rules/global/automation_rules.yaml` condition values updated (`value: OutreachSent` etc.); `defaults.yaml` likewise. These rules continue to evaluate correctly because they reference the same value strings the enum now uses.
- (f) MODULE.md narrative text in 4 modules updated for consistency -- doc readers see the same spellings code uses.
- (g) STATUS.md prior Done entries narrative was updated by the cascade sed -- historical entries now say "OutreachSent" etc. This is a minor documentation hygiene change (entries describe past work in the present terminology); the underlying decisions are unchanged.
- (h) D-124 in DECISIONS.md is unchanged except for its title wording's drift now being formally captured by D-138. Future readers see D-124's Decision block was always correct; D-138 documents the title-correction + re-cascade.
- (i) Reusable pattern: when SP UI engineer adds a new state, HILDA enum mirrors the value verbatim; no separate translation table to maintain.

**Anchors**: `[D-118]` (SP UI engineer owns SP definitions; HILDA aligns), `[D-124]` (Decision-block intent re-codified; title-wording superseded), `[D-135]` (strict-boundary cascade complete -- D-138 is the value-string companion piece), `[D-137]` (SP writeback two-step pattern -- D-138 is the value-string the pattern writes), commits `f9542fa` (D-137 implementation that surfaced this) + the D-138 cascade commit.

## D-139: PM approval Pattern A doctrine — SP-authoritative multi-field atomic mirror

**Date**: 2026-06-28
**Status**: Ratified

**Context**: SP UI engineer's Approve button on the Deliverables SP UI needs to atomically transition an item to `ReadyForSubmission` with PM identity attribution. HILDA's rule engine would normally require state-machine transitions through guards. Whether HILDA or SP is the state-transition authority for approval determines the whole plumbing shape.

**Decision**: SP is state-authoritative for PM approval. SP UI engineer's button writes atomically to 3 fields (`delivery_state=ReadyForSubmission` + `pm_approval_at` + `pm_approval_pm_id`) in a single SP POST. HILDA reads the CHANGED alert, dispatcher `_PM_APPROVAL_DELTA_FIELDS` refinement discriminates the sub-trigger into `PmApproved`, `apply_pm_approval_task` mirrors the 3-tuple into Postgres WITHOUT invoking state-machine guards. Generalized as **"Pattern A doctrine"** — reusable for other SP-atomic-write flows.

**Why**:
- (a) **HILDA-authoritative rejected**: SP writes "TPM wants to approve" flag → HILDA runs guards → HILDA writes final state back to SP. Round-trip latency for TPM feedback; guard failures at HILDA leave SP in inconsistent visible state; atomicity of the 3-field write only holds at SP-side (single POST); if HILDA is authoritative, guards may pass but writeback may fail, producing asymmetric state.
- (b) **HILDA-drives-approval-workflow entirely rejected**: would require HILDA-native TPM UI, which is Path B (deferred post-Ph-1 per [D-143] discussion).
- (c) **Extension of [D-068]** (SP is source of truth for state) into a complete implementation doctrine.

**Consequences**:
- (a) HILDA's state-machine guards are NOT the authority for PM-approval-driven transitions; SP UI engineer's button visibility logic IS the guard.
- (b) Pattern applies to other SP-authoritative multi-field writes; PM approval is the reference case.
- (c) Documentation of when-to-use-Pattern-A becomes an architectural rule going forward.
- (d) `apply_pm_approval_task` becomes the exemplar for Pattern A tasks; future SP-authoritative mirror tasks follow the same shape (read delta → mirror to Postgres → no guards).

**Anchors**: `[D-068]` (SP is source of truth for state), `[D-138]` (DeliveryState enum values verbatim), `[D-140]` (companion — HILDA-authoritative pattern for submit-to-carrier). Commits `3874041`, `2ca7a2a`, `032ad19`, `3ffcddb`.

## D-140: Guard 4 trusts `trigger_source` — HILDA-authoritative submit-to-carrier authority

**Date**: 2026-07-01
**Status**: Ratified

**Context**: `submit_to_carrier_task` performs per-item file uploads via `customer_adapter`; after all-files-success, transitions item RFS → SubmittedToCustomer. Guard 4 needs to know upload completed successfully before permitting the transition. Initial implementation wrote `carrier_upload_complete=True` to storage before transitioning; the field is not a column on DeliveryItemTable; `update_delivery_item`'s hasattr guard silently skipped the write; Guard 4 read False; blocked the transition; task's counter still incremented — a silent-failure chain hidden across 6 layers (surfaced by live smoke 2026-07-01 as "uploaded_items=9 but DB rows stayed in RFS").

**Decision**: Guard 4 trusts `trigger_source='submit_to_carrier_task'` (and by extension `'sync_backfill_submit_to_carrier'` per [D-142] reconciler) as authoritative evidence of upload completion. The task IS the authority — per-item transitions happen only after `all_ok and files_ok > 0` at the task-body level. Other trigger_sources still hit the fallback `carrier_upload_complete` flag check for defensive purposes.

**Why**:
- (a) **Adding a real `carrier_upload_complete` column rejected**: (i) the flag is derived data (function of upload attempts + all-files-success), storing it makes it a persistence surface with sync burden; (ii) reconciler-driven syncs would still need trigger_source discrimination anyway; (iii) test surface is simpler when the "authority" is a code path, not a data column.
- (b) **Removing Guard 4 entirely rejected**: guards are the type-safety layer for state transitions; removing = losing invariant enforcement for all callers, including future ones.
- (c) **Companion to [D-139]** — where D-139 handles SP-authoritative transitions (SP writes atomically, HILDA mirrors), D-140 handles HILDA-authoritative transitions (HILDA task drives the workflow, HILDA task is the guard authority).

**Consequences**:
- (a) Guard 4 now inspects `trigger_source`. Adding new authoritative-transition contexts requires appending to the trust-list.
- (b) Establishes a pattern for other guards to follow — trigger_source-driven trust vs data-column trust.
- (c) Trigger_source spoofing risk is bounded to internal task authors, not external inputs (SP alerts can't inject trigger_source).
- (d) Reconciliation cascade tasks per [D-142] can safely re-invoke submit_to_carrier_task with `trigger_source='sync_backfill_submit_to_carrier'` and expect Guard 4 to accept.

**Anchors**: `[D-068]`, `[D-139]` (SP-authoritative companion), `[D-142]` (reconciliation cascade uses this trust pattern). Commit `5cbc383`.

## D-141: template.yaml is authoritative for structural DeliveryItem fields at import; body_kvs is fallback

**Date**: 2026-07-02
**Status**: Ratified

**Context**: SP ADDED alert body_kvs is missing template-defined fields at Deliverables setup time (SP UI engineer's setup script may leave fields at column defaults; SP alert body may drop them entirely for various reasons per [D-143] SP-alert lossiness). Import task previously mapped body_kvs to DeliveryItemBase fields directly; result: `doc_count=0`, `target_folder=None`, `tracking_modality=[]`, `milestone_gating=default`, etc. → downstream cascades broken (FR-82 doc_count consistency violation warnings; CAD-E010 upload-guard failures; kickoff eligibility miscalculated).

**Decision**: At import time, HILDA reads `template.yaml` as the authoritative source for structural DeliveryItem fields. Three-bucket split:

**Template-authoritative** (template value wins; body_kvs is fallback if template lookup misses):
`doc_count`, `tracking_modality`, `milestone_gating`, `item_type`, `item_description`, `tg_path_id`, `item_path_id`, form-factor flags (`handset` / `tablet` / `wearable` / `ir` / `osmr` / `rmr` / `hmr_smr`), `customer_delivery_info` (root-level, denormalized per item), `delivery_path_template` (root-level).

**Template-seeded + SP-editable** (template seeds initial value; SP CHANGED alerts override at runtime subject to null-guard):
`no_customer_upload`, `force_tracking_enabled`, `review_required`, `target_folder`.

**SP-only** (body_kvs authoritative; template does not participate):
`delivery_state`, `owner_*` (4-field identity), `tg_*` (identity fields), `owner_status_note`, `pm_approval_*`, milestone `*_triggered_at`, `last_updated`, `item_name`, `item_completion_pct`.

body_kvs is fallback for the first two buckets when template lookup misses. SP CHANGED alerts continue to override at runtime for the second bucket subject to null-guard (int=0 / str=None|`""` / list=[] / bool=key-missing → skip; skip preserves current value).

**Why**:
- (a) **SP-authoritative for everything rejected**: SP alerts are lossy per [D-143]; SP UI engineer's setup script may not populate every field; template.yaml is the design-time source of truth for what a work item structurally IS.
- (b) **"Fetch structural fields from SP on demand at import" rejected**: SP throttling at high volume; template.yaml is already loaded in memory for other purposes; adds SP round-trip per import for data that doesn't change.
- (c) **Using SP as fallback and template as primary WITHOUT null-guard rejected**: TPM's legitimate edits post-import would be silently ignored on any Deliverables CHANGED alert that omitted the edited field.
- (d) **Enables Path B evaluation** (per [D-143] Consequences): template.yaml is already the source; a HILDA-native TPM UI writes to template.yaml OR to a HILDA-native structural store, no data-model change required.

**Consequences**:
- (a) All structural fields must be authored in template.yaml; SP UI engineer's setup script becomes a projection layer that translates template.yaml into SP rows.
- (b) Any TPM edit of a structural field in SP UI post-setup is silently ignored by the import path (propagated by `sync_deliverable_fields_task` for the editable bucket only).
- (c) Backfill script must run once for existing Postgres rows imported pre-cascade.
- (d) `template_lookup` module becomes a load-bearing runtime component; template.yaml loading failure at boot degrades import to fallback-only path (documented in module).
- (e) New ActionKind `SYNC_DELIVERABLE_FIELDS` + rule + task cover the runtime edit path with null-guarded merge.
- (f) FR-82 doc_count consistency warnings self-resolve post backfill run (template.yaml enforces `doc_count == len(item_description)` at validation time).

**Anchors**: `[D-068]`, `[D-118]` (SP UI engineer owns row creation), `[D-142]` (reconciliation cascade for lost ADDED alerts), `[D-143]` (SP alerts are best-effort). Commits `8a60abb`, `5b09af4`.

## D-142: 5-sync reconciliation architecture — email fast-path + reconciliation backstop

**Date**: 2026-07-02
**Status**: Ratified (design; implementation deferred to next session)

**Context**: Per [D-143], SharePoint alert emails are lossy at burst (250 concurrent alerts = 3-15% expected loss). No delivery SLA from Microsoft. Current architecture fails silently when alerts are dropped: no Postgres row created, no kickoff fires, no state transitions happen. TPM has no visible signal — the milestone just doesn't progress. Manual re-trigger by TPM is UX-unacceptable for compliance work.

**Decision**: 5 reconciliation sync tasks running on Celery beat schedule, each covering one drift class:

| Sync | Detects | Fires |
|---|---|---|
| sync-1 `delivery_item_count` | SP Deliverables_<customer_id> has rows not in Postgres | `import_deliverable_tracker_task` per missing item |
| sync-2 `milestone-start-collection` | SP `milestone_collection_started_at` set >5min but items still Not Started | `kickoff_collection_task` for the milestone |
| sync-3 `deliverable-approved` | SP delivery_state=RFS + `pm_approval_at` set + >5min elapsed but Postgres still UnderPMReview | `apply_pm_approval_task` per item |
| sync-4 `milestone-submit-to-carrier` | SP `milestone_submission_triggered_at` set >5min but items still RFS | `submit_to_carrier_task` for the milestone |
| sync-5 `milestone-close-all-items` | SP `closed_all_items_triggered_at` set >5min but items still SubmittedToCustomer (or RFS + no_customer_upload) | `close_all_items_task` for the milestone |

Each is a Celery beat task with 5-min default interval (2-min for sync-3). Each invokes the SAME task body the email path invokes, with `trigger_source="sync_backfill_*"` for audit differentiation. Each terminates naturally when the drift condition doesn't apply (no persistent stopped flag needed — task naturally no-ops on next tick when in sync).

Trigger predicate: "**at least one** item still in the pre-transition state" (not "all") so partial-completion cases converge cleanly on subsequent ticks.

**Why**:
- (a) **SP REST Change Notifications (webhooks) rejected**: requires public HTTPS endpoint HILDA doesn't have (corp reverse-proxy provisioning, TLS cert, IT ticket, cross-team scope).
- (b) **SP-side polling as the only path (drop emails) rejected**: HILDA's mailbox is the natural funnel for corp Exchange; polling SP directly is redundant for the 85-97% happy path.
- (c) **Manual TPM re-trigger rejected**: unacceptable UX for compliance tracking.
- (d) **Full HILDA-native UI (Path B) deferred**: correct long-term but out-of-scope for Ph-1; reconciliation is the Ph-1 backstop.
- (e) **Per-sync-type beat entries chosen over single-reconciler beat entry**: simpler to configure/toggle each independently; per-sync interval tuning; per-sync retry-limit policy.

**Consequences**:
- (a) HILDA now formally assumes SP alerts are lossy (companion to [D-143]).
- (b) All future SP-driven features need reconciliation coverage or explicit rationale for skipping.
- (c) Config file `reconcile.json` becomes ops surface; interval + retry-limit + escalation policy are ops-tunable.
- (d) Retry-limit + ops_alerts escalation needed for sync-4/5 to prevent infinite loops on permanently failing uploads / transitions (open question flagged for cascade implementation).
- (e) Post-cascade rollout, HILDA is materially more resilient to SP alert delivery variance.
- (f) Establishes pattern: any authoritative-recovery task pairs an email-driven task with a reconciler that invokes the same task body via a distinct trigger_source.
- (g) Each task body must be idempotent (already is; validated via existing state-filter guards); reconciler safe to re-invoke.

**Anchors**: `[D-068]`, `[D-139]`, `[D-140]` (both provide the trigger_source trust patterns reconciler uses), `[D-141]`, `[D-143]` (SP-alerts-are-best-effort stance this cascade implements).

## D-143: SP alerts are best-effort — email fast-path + reconciliation backstop as architectural stance

**Date**: 2026-07-02
**Status**: Ratified

**Context**: Multi-week live-testing on corp Exchange + corp SP surfaced repeated instances of SP alert delivery variance: alert coalescing, body truncation, delayed delivery, occasional drops, no-Edited-marker forms. Corp Exchange has no IMAP/SMTP; EWS via basic auth against service account is the only channel. Microsoft's SP alerts are documented as best-effort. Setup-Deliverables at 5-carrier × 50-item burst is projected to lose 3-15% of alerts.

**Decision**: HILDA officially treats SP alerts as best-effort notification signal, not authoritative delivery contract. Email is fast-path (sub-minute latency when it works, covers 85-97% of alerts). Reconciliation is authoritative backstop (5-min drift window at worst per [D-142]). Every SP-driven state transition needs BOTH an email-path handler AND a reconciliation sweep.

**Why**:
- (a) **Continuing to trust email alerts as authoritative rejected**: real-world losses observed at even low volume; 250-concurrent-alert burst will drop 3-15%; silent data loss is unacceptable for compliance tracking; no operational visibility into which alerts were dropped.
- (b) **Reconciliation-only (drop email fast-path entirely) rejected**: email latency IS acceptable for the 85-97% happy path; adding reconciliation on top doesn't preclude the fast-path; reconciliation-only would push all state transitions to a 5-min floor.
- (c) **Delivery acknowledgment protocol between SP and HILDA (mail-received handshake) rejected**: out of scope; SP has no such API.

**Consequences**:
- (a) Formalization of "SP alert is a hint, not a contract" throughout HILDA architecture.
- (b) Every new SP-driven feature reviewed for reconciliation coverage.
- (c) **Path B (HILDA-native TPM UI) becomes strategically attractive** because it eliminates this whole class of problem — the "SP alert reliability" concern doesn't exist for HILDA-owned state. Ph-1 ships on Path A (this ADR + [D-142]); Path B evaluation flagged for post-Ph-1.
- (d) Short-term: ship Ph-1 with reconciliation cascade per [D-142] + operate for months to gather data on real loss rate.
- (e) Long-term: Path B evaluation based on operational experience (STATUS Flag captured).
- (f) This ADR is the architectural stance; [D-142] is the specific implementation.
- (g) Companion to [D-141] — SP alerts being lossy is why template.yaml at import is authoritative for structural fields (SP might not deliver them).

**Anchors**: `[D-047]` (SP-alert email channel), `[D-118]`, `[D-141]` (template.yaml authority as a consequence of this stance), `[D-142]` (reconciliation cascade as the implementation).

## D-144: Setup Deliverables serialization — HILDA auto-transitions Not Started → Open at import + widens kickoff filter

**Date**: 2026-07-15
**Status**: Ratified

**Context**: SP alerts are delivered out of order. TPM setting up a milestone clicks Setup Deliverables (SP creates Deliverables rows + fires ADDED alerts) then clicks Start Tracking (SP sets `milestone_collection_started_at` + fires Milestones CHANGED alert). If the Milestones alert arrives before HILDA has finished importing all Deliverables ADDED alerts, `kickoff_collection_task` scans zero items in Postgres and silently no-ops — architect saw this on the 2026-07-07 fresh-restart smoke test (7 items imported AFTER kickoff fired, kickoff emitted `kickoff_collection_empty_milestone`). Manual re-click of Start Tracking recovers, but the flow is fragile.

**Decision**: HILDA's `import_deliverable_tracker_task` after `create_delivery_item` + sp_id populate now calls `tracker.update_delivery_state(target=OPEN)` which writes Open to both Postgres AND SP-side via `_sp_writeback_field_updates`. Applies to ALL item types — Confirmation, non-Confirmation, and Default work item — uniformly. `kickoff_collection_task` eligibility filter widened from `delivery_state == "Not Started"` to `delivery_state in ("Not Started", "Open")` so items already advanced by import are still picked up. SP UI engineer coordinates on the other side: Start Tracking button visibility gates on all items being Open (not Not Started).

**Why**:
- (a) **Leave Not Started as-is + wait for retry (rejected)**: fragile UX; TPM sees "click, nothing happens, click again" as broken system; not acceptable for compliance workflow.
- (b) **Serialize on HILDA side by blocking Milestones alert processing until Deliverables imports settle (rejected)**: SP alerts are not ordered; there's no reliable "all Deliverables done" signal from SP side; HILDA would need a timer that could race just as badly.
- (c) **SP UI engineer gates Start Tracking button on all items Open (chosen with this ADR)**: shifts the "wait" to the SP UI, which has the actual visibility on all rows in the list; HILDA does its part by making Open visible on SP as soon as import finishes; SP UI can enforce the invariant deterministically.
- (d) Applies to Default WI + Confirmation uniformly per architect ask: keeps SP UI button-gating logic simple (single state check across all items) rather than special-casing item types.

**Consequences**:
- (a) SP UI engineer must implement the Start Tracking button gate. Communicated per architect ask 2026-07-15; flagged in STATUS as pending coordination.
- (b) `kickoff_collection_task` becomes idempotent-safe for items already advanced to Open (existing `no_op_idempotent` state machine handles the NS→Open re-attempt cleanly).
- (c) Setup Deliverables now writes to SP twice per item: once by SP UI to create the row, once by HILDA to advance delivery_state to Open. Extra SP writes at burst risk hitting SP throttling; mitigated by the fact that each write is a separate alert-triggered task, spread over seconds.
- (d) Partial-transition failure mode: if HILDA writes Open to Postgres but SP writeback fails, Postgres shows Open and SP shows Not Started. Kickoff filter accepting both states covers this — the item is still eligible; reconcile task (per [D-142]) would repair the SP divergence later.
- (e) Setup for Path B (HILDA-native TPM UI per [D-143]): when HILDA owns the workflow, this pattern (HILDA-driven state writeback to SP for UI gating) generalizes to Submit-to-Customer and Close-All-Items too — see [D-145] and [D-146] following.

**Anchors**: `[D-068]`, `[D-118]` (SP UI engineer owns SP-side row creation), `[D-141]` (template.yaml is authoritative for structural fields), `[D-143]` (SP alerts are best-effort — motivates HILDA taking over serialization). Commit `a29859c`.

## D-145: PM Approval — HILDA drives ReadyForSubmission writeback + Confirmation 2-hop close + QueueSubmission removed from rule

**Date**: 2026-07-15
**Status**: Ratified

**Context**: Under original Pattern A per [D-068]/[D-139], SP UI's Approve button atomically writes 3 fields (`delivery_state=ReadyForSubmission` + `pm_approval_at` + `pm_approval_pm_id`); HILDA mirrors passively to Postgres. Consequence: Submit-to-Customer button on SP becomes clickable immediately after Approve, before HILDA's mirror has landed. Architect 2026-07-08 live test showed the race: TPM approved 10 items then clicked Submit-to-Customer immediately; `submit_to_carrier_task` scanned 5 items still at UnderPMReview locally (mirror not yet done), skipped them, uploaded only 5. Confirmation items also had no clean route to Closed — sitting at UnderPMReview / ReadyForSubmission indefinitely, out of sync with reality. And the PM-approval rule fired both `ApplyPMApproval` + `QueueSubmission` per item, meaning per-item carrier uploads on approval AND per-milestone upload on Submit-to-Customer click — double-upload risk.

**Decision**:
1. **SP UI Approve button now writes only 2 fields**: `pm_approval_at` + `pm_approval_pm_id` (no delivery_state).
2. **HILDA's `apply_pm_approval_task` drives delivery_state**: after mirroring the 2 SP fields to Postgres, calls `tracker.update_delivery_state(target=ReadyForSubmission)` which writes RFS to both Postgres and SP via `_sp_writeback_field_updates`.
3. **Confirmation items get an additional 2nd hop**: RFS → Closed with `trigger_source='tpm_button'` (satisfies DEF-20; passes `no_customer_upload=True` guard). Confirmation items never dwell at RFS since they have no carrier upload.
4. **`QueueSubmission` action removed** from `advance_to_ready_for_submission_on_pm_approval` rule. `ApplyPMApproval` is the sole action. Carrier upload is now solely TPM-triggered via the milestone-level Submit-to-Customer button (`submit_to_carrier_on_milestone_submission_triggered` rule).
5. Backward-compat: if SP writes delivery_state atomically (legacy 3-field path), HILDA's mirror sets Postgres to RFS; the follow-on `update_delivery_state(target=RFS)` returns `no_op_idempotent` and skips SP writeback. Both paths converge.

**Why**:
- (a) **Keep legacy 3-field atomic write (rejected)**: race between per-item Approve mirrors and milestone-level Submit-to-Customer click was observed in production. SP UI has no way to gate the Submit button on all items being at RFS if SP writes RFS itself immediately — the state IS RFS on SP the moment Approve is clicked, before HILDA even sees the alert.
- (b) **Serialize on HILDA side by adding a lock/queue (rejected)**: adds complexity + timeout / recovery paths; doesn't solve the core problem of SP UI needing visible state for button gating.
- (c) **HILDA drives delivery_state (chosen)**: same pattern as [D-144] for Setup Deliverables. SP UI can gate Submit-to-Customer button visibility on delivery_state=RFS being visible on SP; that's only true after HILDA has finished its per-item mirror + transition. Deterministic ordering guaranteed by SP UI gate.
- (d) **Direct UnderPMReview → Closed edge for Confirmation (rejected)**: would require state machine schema change. Existing UnderPMReview → RFS → Closed edges both exist and both have appropriate guards; 2-hop uses them cleanly. `trigger_source='tpm_button'` on the RFS → Closed hop satisfies Guard 5 DEF-20 attribution (PM approval on Confirmation carries TPM intent semantically).
- (e) **Keep QueueSubmission fired per-item on approval (rejected)**: pre-Ph-1 rationale was FR-18-per-item; but the actual production carrier upload path is `submit_to_carrier_task` triggered by the milestone-level Submit-to-Customer button. Firing both = double upload risk (mitigated only by the fact that `QueueSubmission` per-item params usually didn't resolve to real files under this rule's minimal params). Regression scan confirmed: task binding + enum + ActionKind + tests all intact; only the YAML action line removed. Any future rule can still dispatch QUEUE_SUBMISSION.

**Consequences**:
- (a) SP UI engineer must update Approve button behavior to write 2 fields instead of 3, AND update Submit-to-Customer button visibility to gate on all items showing delivery_state=RFS. Communicated per architect ask 2026-07-15; flagged in STATUS as pending coordination.
- (b) Pattern A boundary shifts: previously "SP writes atomic 3-field; HILDA mirrors" per [D-068]; now "SP writes intent (2 fields); HILDA drives state writeback (delivery_state)". Successor pattern under the same Pattern A doctrine (SP-authoritative for the intent signal, HILDA-authoritative for state machine). D-068/D-139 remain valid as the doctrine; this is the Ph-1-specific mechanism refinement.
- (c) Guards.py Guard 5 (DEF-20 TPM attribution) continues to require `no_customer_upload=True` on RFS → Closed; Confirmation items have this template-invariant so the transition passes cleanly (confirmed in MMK template.yaml + mock_customer template.yaml).
- (d) `advance_to_ready_for_submission_on_pm_approval` rule now has 1 action (`ApplyPMApproval`) instead of 2. Rule engine + workflow_engine dispatch continue to work.
- (e) Backward compat means existing customer deployments where SP still writes 3 fields (legacy) continue to work without SP UI engineer coordination — HILDA's second transition is a no-op idempotent. Migration can be phased per customer.
- (f) If HILDA writeback to SP fails after Postgres transition succeeds: Postgres shows RFS, SP shows UnderPMReview, Submit-to-Customer button never appears on SP. Recovery via reconcile task per [D-142]/[D-143] OR manual SP field edit.

**Anchors**: `[D-068]`, `[D-139]` (Pattern A doctrine), `[D-140]` (HILDA-authoritative Guard 4 trust pattern that this ADR extends), `[D-143]` (SP alerts best-effort motivates HILDA taking over state writeback), `[D-144]` (companion serialization at Setup Deliverables). Commit `339bf6a`.

## D-146: Default WI auto-close via state_machine OPEN → CLOSED edge + Guard 5 carve-out + PM-approval sweep

**Date**: 2026-07-15
**Status**: Ratified

**Context**: Under [D-144] all item types (including Default work item) transition Not Started → Open at import. Default WI has no owner, no PM approval, no carrier upload — it's a placeholder for unrouted attachments. Once all non-Default items in the (customer, device, milestone) scope reach terminal states (ReadyForSubmission / SubmittedToCustomer / Closed), Default WI should also close as the terminal cleanup step. Without automation, Default WI sits at Open forever and the milestone is never "done" from SP's or HILDA's view.

**Decision**:
1. **state_machine.LEGAL_TRANSITIONS[OPEN]** gains CLOSED as legal target (in addition to existing OUTREACH_SENT). Default WI takes the OPEN → CLOSED shortcut since it has no OutreachSent / DocumentReceived / OwnerClosed / UnderPMReview lifecycle.
2. **guards.py Guard 5** (DEF-20 TPM-attribution required for CLOSED) gets a carve-out: accept `trigger_source='automated'` specifically for `item_type=='Default' AND from_state==OPEN`. Rationale: Default WI has no TPM-clickable button; requiring TPM attribution would demand a button that serves no user purpose. Attribution captured via `rule_id='default_wi_auto_close_on_all_ready'` in the audit row.
3. **`apply_pm_approval_task` gains an end-of-task sweep**: after per-item transitions land (RFS or Confirmation-close), scan Postgres for the (customer, device, milestone) triple; if all non-Default items are in `{ReadyForSubmission, SubmittedToCustomer, Closed}`, locate the Default WI and transition Open → Closed via automated trigger.
4. **Ph-2 gate**: skip auto-close if Default WI has any classified attachments (documents pending routing / doc_type classification / revision resolution); log `default_wi_close_deferred_ph2_pending`. Those cases need TPM/dashboard resolution before terminal close.

**Why**:
- (a) **Leave Default WI at Open indefinitely (rejected)**: milestone would never appear "done"; SP UI's Close-All-Items button + status reporting would show inconsistent state.
- (b) **Transition Default WI through full 8-state lifecycle (Open → OutreachSent → ... → Closed) (rejected)**: Default WI has no owner, no outreach, no docs by design; the intermediate states are fabricated and add noise.
- (c) **Add direct UnderPMReview → CLOSED edge to state machine for Default WI (rejected)**: Default WI never reaches UnderPMReview under [D-144] because it's excluded from kickoff outreach (per prior `kickoff_collection_task` filter). It sits at Open. Adding an edge from UnderPMReview would be a phantom path.
- (d) **OPEN → CLOSED direct edge (chosen)**: minimal state machine amendment; single new edge; guarded by Guard 5 carve-out to prevent misuse (only automated + Default from Open, everyone else still needs TPM attribution).
- (e) **Fire auto-close via a new rule + `DefaultWIAutoClose` ActionKind (considered)**: cleaner conceptually but adds new rule + new action + new task + rule condition logic. Inline sweep in `apply_pm_approval_task` is 40 lines and reuses the existing transition machinery. Trade-off: sweep runs per PM-approval alert (idempotent no-op except on the last item's approval); rule-based would fire once on a milestone-terminal event. Inline chosen for Ph-1 minimality; can refactor to rule-based post-Ph-1 without changing the state machine.
- (f) **Ph-2 gate on classified attachments**: Default WI is the destination for unrouted attachments today. If any land there, they need TPM to reassign or resolve doc_type before Default WI can close. Auto-closing on top of unresolved documents would strand them.

**Consequences**:
- (a) New `state_machine.LEGAL_TRANSITIONS[OPEN]` value visible to guards + `transition_legal` + dashboard preflight queries. Callers that only expected OUTREACH_SENT from OPEN now see CLOSED as legal too — but they'd only ever hit that transition via the specific Default-WI + automated + Guard 5 path.
- (b) Guard 5 carve-out is item-type-scoped + from-state-scoped + trigger-source-scoped. Non-Default items still require `manual_tpm_override` or `tpm_button` for CLOSED. DEF-20 policy intact for the general case.
- (c) `apply_pm_approval_task` end-of-task sweep runs per PM-approval alert. On approvals 1-9 of 10 items, the sweep detects "not all non-Default at terminal" and no-ops. On the 10th item's approval, the sweep transitions Default WI. If the 10th approval was for a Confirmation item (which goes straight to Closed via 2-hop per [D-145]), the sweep still runs and detects the terminal condition.
- (d) Race consideration: if two PM-approval alerts land near-simultaneously (both are the "last" non-Default item to transition), both sweeps could try to close Default WI. `update_delivery_state(target=Closed)` returns `no_op_idempotent` on the second call (item already at Closed). Safe.
- (e) Default WI attachments feature Ph-2 gate — when TPM reassigns unrouted docs to non-Default items in Ph-2, the reassignment code path should re-invoke the sweep OR fire a milestone-level completion check. Deferred to Ph-2 implementation of unrouted-doc TPM triage.
- (f) SP-side: Default WI now shows Closed on SP row when the milestone completes. SP UI's milestone-status roll-up (if implemented) will correctly reflect "all items closed".

**Anchors**: `[D-100]` (FR-64 Close-All-Items HILDA-owned per-item cascade — same architectural pattern of HILDA-driven bulk state transitions on milestone terminal events), `[D-144]` (Default WI reaches Open at import), `[D-145]` (companion PM-approval writeback — sweep executes at the end of the same task). Commit `ca1f108`.

## D-147: Scheduled TPM DRR closure notification — cron-based, target_date-anchored, US/Eastern, two-fire window, WOPI-agnostic

**Date**: 2026-07-15
**Status**: Ratified

**Context**: TPMs need a summary email at the DRR (Deliverable Readiness Review) closure milestone target date so they can catch pending items before the deadline hits. Two fires per (customer, device, milestone): on `target_date - 1` at 00:00 US/Eastern (day-before nag) and on `target_date` at 00:00 US/Eastern (day-of final status). Body must show aggregate Open/Closed counts + Completion% + per-TG pending breakdown (rows with count > 0 only); Excel attachment must show per-item detail with columns Item No / Item Title / Open-or-Closed Status / Owner Comment. Subject format: `[<Carrier>]\[<Device>][<Milestone>] DRR closure final status`. TPM email lives on the Projects_<customer_id> SP list keyed on `project_model=device_id`. "Closed" for the aggregate math counts states in {Closed, ReadyForSubmission, SubmittedToCustomer} — i.e., any item that has passed PM approval, not just terminal-Closed.

**Decision**:
1. **Cron-based Celery beat task** (`tpm_notification.tick`) runs every 300s per `config/tpm_notification.json.beat_interval_seconds`.
2. **Task iterates customer YAMLs** under `customizations/sharepoint_config/customers/`, reads Milestones list per customer via `sp_writer.get_items(entity="milestones", ...)`, per-row parses `target_date` and classifies now-in-US/Eastern vs the two send windows.
3. **Window classifier**: for each phase date (`target_date - 1` and `target_date`), compares `now_local` vs `[phase_date 00:00, phase_date 00:00 + window_minutes]`. `not_yet` / `in_window` / `missed`. Strict mode (default): past window → missed. Lenient mode: same-day-past-window → still in_window.
4. **Idempotency** via `CommunicationLog` `action_type='tpm_drr_notification_sent'` lookup keyed on details containing `{customer_id, device_id, milestone_id, phase}`. If audit query surface is unavailable, defaults to allowing re-send (safer than skipping forever).
5. **Missed-window and missing-target_date behavior**: opt-in ops alerts via distinct audit action types (`tpm_drr_notification_missed_window` / `tpm_drr_notification_missing_target_date`) so ops can grep. Both default on.
6. **Excel via openpyxl** (already declared in requirements.txt from earlier era). Body via new Jinja template `tpm_drr_closure.j2`. TPM email/name resolved from Projects_<customer_id> list keyed on `project_model=device_id`.
7. **EmailSender protocol extended** with optional `attachments=[(filename, bytes, mime)]` parameter. Backward-compatible (default None). SmtpSender uses `EmailMessage.add_attachment`; EwsSender uses `exchangelib.FileAttachment`. All existing call sites unaffected.

**Why**:
- (a) **Event-driven (fire when Default WI closes per [D-146]) (rejected as sole mechanism)**: fires at most once per milestone; TPM has no day-before nag; doesn't help TPMs with late items. Also fires whenever the last item lands regardless of clock — not target-date-anchored.
- (b) **Both event-driven AND scheduled (rejected as premature)**: adds complexity of two paths + reconciliation between them. Ph-1 uses scheduled only; can add event-triggered path in Ph-2 if data shows TPMs need it sooner than target_date.
- (c) **Scheduled + target_date-anchored (chosen)**: matches architect ask literally; two fires give day-before nag + day-of final status; cron backing means no dependency on any specific state-transition event landing on time; TPM sees a predictable ritual.
- (d) **US/Eastern timezone hardcoded (rejected)**: corp may deploy in other timezones later; config-driven timezone is trivially cheap.
- (e) **Strict window with configurable `strict_only` toggle**: strict-only default matches architect ask ("keep it 12 A.M."); lenient toggle preserves flexibility if ops finds hilda-beat missing 00:00 fires repeatedly.
- (f) **"Closed" for the aggregate includes RFS + SubmittedToCustomer (chosen per architect)**: architect specified `open=5, closed=45` example math where 45/(5+45)=90% completion. Non-Confirmation items sitting at RFS after PM approval are "as done as they'll get before carrier submission" and should count as closed from TPM's perspective.
- (g) **Body has no "DRR closure final status for X/Y/Z" narrative line (chosen per architect)**: subject already carries routing context; body opens straight to the summary tables.
- (h) **Per-TG pending table filters to count > 0 (chosen per architect)**: matches screenshot format; zero-pending TGs don't need TPM attention.
- (i) **Attachments parameter on EmailSender protocol (chosen)**: 3 alternatives considered: (i.a) bypass the sender abstraction and use smtplib directly in the notification task — leaks SMTP details out of the abstraction and complicates EWS-based deployments; (i.b) new SecondarySender protocol just for attachments — over-engineered for one call site; (i.c) extend the existing Send() signature — chosen; smallest surface change, backward-compatible default, both SmtpSender and EwsSender have well-known APIs for adding attachments.
- (j) **Config kill switch (`enabled=false`)**: any scheduled outbound-email feature needs an ops-flippable off switch. Same pattern as `config/reconcile.json` per [D-142].

**Consequences**:
- (a) hilda-beat container gains a new scheduled task; existing beat entries (`poll_ews_inbox_60s`, `reconcile_all_300s`) unaffected. Beat interval configurable per environment.
- (b) EmailSender protocol change is minor but IS a public API change — any HILDA-internal or future 3rd-party sender implementation must accept the optional parameter. Backward-compat via default None preserves existing behavior for callers who don't pass it.
- (c) Ops alerts on missed window write audit rows but do not currently dispatch NotifyHildaOps action — the ops-alert surface here is grep-based rather than push-based. If push-notification is needed later, upgrade to `NotifyHildaOps` action_kind dispatch (existing binding).
- (d) TPM email lookup requires Projects_<customer_id> list to have `tpm_email` column populated and a row keyed on `project_model=device_id`. If missing, task logs warning + skips send. No new SP list creation required — Projects list already exists per [D-104].
- (e) Timezone dependency on `zoneinfo` (Python 3.9+ stdlib) + IANA timezone data. Container image already includes both.
- (f) `openpyxl` dependency was already in requirements.txt from earlier Excel-spec-ingest work — no new dep.
- (g) Test surface: no baseline notification tests exist. First real fire is at 00:00 US/Eastern on some milestone's target_date-1. Smoke test path documented in STATUS Flags (window_minutes=1440 + strict_only=false + beat_interval=60 for early trigger).
- (h) Corp SP 2017 deployment must expose `target_date` on the Milestones list body (verified via [D-085] cascade; parser promotes it into field_deltas per Milestones-promoted-tuple mechanism).

**Anchors**: `[D-085]` (Milestones target_date field), `[D-104]` (Projects_<customer_id> list — TPM email source), `[D-144]`, `[D-145]`, `[D-146]` (companion serialization ADRs from this session). Commit `e33e2cf`.

## D-148: Final DRR deliverable auto-transition on day-of TPM Excel send + unconditional Default WI close in DRR scope

**Date**: 2026-07-21
**Status**: Ratified

**Context**: [D-147] defines a scheduled TPM DRR closure email that fires twice per (customer, device, milestone) — day-before (target_date-1) and day-of (target_date), both at 00:00 US/Eastern — with an Excel attachment covering per-item status. Semantically, the Excel HILDA generates and sends to the TPM IS the final DRR deliverable that the TPM forwards to the carrier. The DRR milestone template carries a specific work item named `"Final DRR status excel deliverable for carrier"` (per-device, one instance per (customer, device, DRR) scope) whose lifecycle should reflect this: when HILDA sends the Excel on target_date, that item's `delivery_state` must move to `SubmittedToCustomer`, and the corresponding Default WI should close as a terminal cleanup — because from HILDA's ledger perspective the (customer, device, DRR) scope is "the Excel deliverable is out; the pipeline items still open are TPM's to force-close via the milestone-level Close All Items". This behavior applies **only** to milestones named `"DRR"`; other milestones (P1, PVT, etc.) do not use this pattern.

**Decision**:
1. **Guard 4 (`SUBMITTED_TO_CUSTOMER`) trust list extension**: `guards.py` accepts a new authoritative `trigger_source='tpm_drr_final_deliverable'` alongside the existing `'submit_to_carrier_task'`. Same rationale as [D-140]'s carve-out — the caller IS the authority on whether the "carrier deliverable submitted" event occurred; there's no `carrier_upload_complete` flag to check because no `customer_adapter` upload happens on this path.
2. **`tpm_notification.py` post-send side effect** (day-of only): after successful `_send_notification` audit write, if `phase == day_of` AND `milestone_id in cfg.final_deliverable_milestone_names`, invoke `_transition_final_deliverable_and_close_default_wi`:
   - Find work item(s) with `item_name == cfg.final_deliverable_item_name` in the (customer, device, milestone) scope already fetched for the summary. If already at `SubmittedToCustomer` or `Closed`, treat as idempotent success. Otherwise call `update_delivery_state(target=SUBMITTED_TO_CUSTOMER, trigger_source='tpm_drr_final_deliverable', rule_id='tpm_drr_final_deliverable_on_day_of_send')` — writes to both Postgres and SP.
   - If step 1 yielded at least one successful (or already-terminal) transition, close all Default WIs in scope (Open → Closed) via `trigger_source='automated', rule_id='default_wi_auto_close_on_drr_final_deliverable_send'`. **Ph-2 gate** matches [D-146]: skip + log `default_wi_close_deferred_ph2_pending` if Default WI has classified attachments.
3. **Config-driven** via 2 new `TpmNotificationConfig` fields:
   - `final_deliverable_item_name: str = "Final DRR status excel deliverable for carrier"`
   - `final_deliverable_milestone_names: list[str] = ["DRR"]` (env override via comma-separated string, handled by a `field_validator`)
4. **Missing-item behavior**: log warning + skip the whole side effect (both the item transition AND the Default WI close). The Excel send itself still succeeds and is audited normally.
5. **Best-effort throughout**: any transition failure logs a warning; nothing raises. The primary contract (`_send_notification` returning `True` after email send + audit) is preserved.

**Why**:
- (a) **Trigger on day-of only, not day-before (chosen)**: day-before is a nag; day-of is the actual "final" event. Under (both) the second fire would see the item already at SubmittedToCustomer and no-op safely, but adds audit noise. Under (day-before only) TPM would see item at SubmittedToCustomer while the deadline is still a day away — confusing state.
- (b) **New trusted `trigger_source` (chosen) vs `bypass_guards=True + trigger_source='manual_tpm_override'` (rejected)**: matches Pattern A/B doctrine per [D-140] / [D-142]. Explicit named trigger attributes the event in the audit log so it's greppable; bypass_guards would hide the semantic under a generic override.
- (c) **Unconditional Default WI close (chosen) vs conditional-on-all-non-Default-terminal (rejected)**: per architect 2026-07-21, at DRR target_date the "rest of the items" category is expected to include items still at `OutreachSent` (owner never responded). Requiring all-non-Default-terminal (as `apply_pm_approval_task`'s sweep does per [D-146]) would strand Default WI at Open indefinitely on any DRR run where owner replies didn't complete. Semantics differ from the PM-approval sweep: THIS close fires because "the Excel is out"; the pipeline items' state is TPM's business via Close All Items (FR-64) afterwards.
- (d) **Config-driven item name + milestone list (chosen) vs hardcoded (rejected)**: matches the pattern of the other `TpmNotificationConfig` fields; future-proofs for renaming the item or adding more milestones (e.g., a `Final PVT status ...` item on PVT milestones) without a code change.
- (e) **Missing-item skips the full side effect (chosen) vs partial (rejected)**: user semantic is "Final DRR item moving to SubmittedToCustomer closes Default WI". Without the item transition, closing Default WI would be an orphan action.
- (f) **Ph-2 gate matches [D-146]** rather than inventing a new bypass rule: consistent Default-WI-close policy across both trigger paths (PM-approval sweep + DRR day-of send); classified attachments still block terminal close in both.
- (g) **Scope A only this session (chosen) vs Scope B (widen `close_all_items_task` for OutreachSent → CLOSED) (rejected as premature)**: Scope B is a meaningful policy shift on Close All Items semantics — from "close items that finished the pipeline" to "force-close everything regardless of pipeline state" — and deserves its own review and ADR. Deferred; may escalate if the OutreachSent-stuck-item pattern shows up in practice on real DRR runs.

**Consequences**:
- (a) Guard 4 trust list expansion is a genuine surface change to `check_transition_guards` behavior — the new `'tpm_drr_final_deliverable'` trigger is now a legitimate way to reach `SUBMITTED_TO_CUSTOMER`. Only `tpm_notification.py`'s helper uses it currently; other callers (rule dispatcher, submit_to_carrier_task, TPM UI buttons) unaffected.
- (b) Default WI in DRR scope now has TWO auto-close trigger paths: [D-146]'s conditional PM-approval sweep + this ADR's unconditional day-of send. Whichever fires first wins; the second becomes `no_op_idempotent` at the state machine layer. Safe under both orderings.
- (c) `TpmNotificationConfig` gains 2 fields with `field_validator` for list env override. Backward-compatible defaults ship with the code; existing `config/tpm_notification.json` deployments continue to work without config edits. Env override: `HILDA_TPM_NOTIFICATION_FINAL_DELIVERABLE_MILESTONE_NAMES=DRR,PVT` for a hypothetical two-milestone rollout.
- (d) Correlation ID for the post-send transitions: `"tpm_notification:<customer>:<device>:<milestone>:day_of"` — stable per (customer, device, milestone) day-of fire, allowing trace-log correlation across the email send + item transition + Default WI close.
- (e) Ops-visible audit trail: three distinct events land per successful DRR day-of fire per device — `tpm_drr_notification_sent` (existing per [D-147]) + `state_transition` for the Final DRR item (SubmittedToCustomer) + `state_transition` for the Default WI (Closed if Ph-2 gate clears, otherwise the deferred log line). Grepping any one of these correlates cleanly to the trigger.
- (f) SP-side observation: on target_date at 00:00 US/Eastern (± window_minutes), the DRR device's Deliverables_<customer_id> row for the Final DRR item flips to `SubmittedToCustomer` and the Default WI row flips to `Closed`. TPM's Close All Items click on the milestone dashboard then closes the remaining SubmittedToCustomer items (via existing FR-64 path). OutreachSent items — if any — are NOT closed by that click (Scope B deferred); TPM force-closes them via per-item Mark Closed if desired.
- (g) Race with SP echo alerts on the two writebacks: HILDA writes delivery_state to SP → SP fires CHANGED alert → HILDA parses it. `sync_deliverable_fields_on_changed`'s whitelist filter (delivery_state not in `_STR_FIELDS ∪ _BOOL_FIELDS ∪ int/list bucket`) silently drops the echo per the [D-145] mirror pattern. Safe.
- (h) Template-authoring dependency: the DRR template.yaml (or equivalent SP `Deliverables_Template_<customer_id>` row) MUST include an item with `item_name = "Final DRR status excel deliverable for carrier"` AND `no_customer_upload = False` (else Guard 4 line 236 rejects the transition). If the item is missing entirely, the day-of send still succeeds and audits, but the state transition + Default WI close silently skip with a WARNING log. This is a template-configuration concern, not a runtime failure.

**Anchors**: `[D-140]` (Guard 4 trust list pattern — `submit_to_carrier_task` precedent), `[D-142]` (config kill-switch + `sync_backfill` trust extension pattern), `[D-146]` (companion Default WI auto-close via pm_approval sweep — same Ph-2 gate; semantics differ on gating condition), `[D-147]` (parent scheduled TPM DRR notification — this ADR is the post-send side effect).

## D-149: TPM early-close of not-applicable items — NOT_STARTED → CLOSED legal edge

**Date**: 2026-07-21
**Status**: Ratified

**Context**: Some template work items are not applicable to a given (customer, device, milestone) triple — e.g., an LTE test item on a WiFi-only device, or a PLM item on a milestone that skips the PLM gate this cycle. TPM knows this at Setup Deliverables time and needs to close such items BEFORE Start Tracking fires outreach to the assigned owner. Two possible SP UI states at the moment of TPM's Close click:

1. **`Open`** — HILDA's [D-144] auto-transition (Not Started → Open, inside `import_deliverable_tracker_task`) has already landed and written back to SP. Normal case.
2. **`Not Started`** — brief race window between "TPM Setup Deliverables click writes rows with default `delivery_state=Not Started`" and "HILDA's D-144 auto-transition writeback lands". Typically <1 second, but SP UI polling / TPM click speed can hit it.

State (1) is already legal per [D-146]'s `OPEN → CLOSED` edge (added for Default WI auto-close, but Guard 5 explicitly accepts `trigger_source in ('tpm_button', 'manual_tpm_override')` from any from-state, so a TPM click from OPEN passes cleanly). State (2) is blocked at the structural layer: `LEGAL_TRANSITIONS[NOT_STARTED] = {OPEN}` per pre-D-149 state machine, and `transition_legal` returns False before guards even run.

**Decision**:
1. **Extend `state_machine.LEGAL_TRANSITIONS[NOT_STARTED]`** from `frozenset({OPEN})` to `frozenset({OPEN, CLOSED})`. Single-line state-machine amendment.
2. **Guard 5 needs no change**. Existing carve-out logic:
   - `_is_default_wi_auto_close` = `item_type='Default' AND from=OPEN AND trigger='automated'` — unchanged; doesn't fire for TPM early-close from NOT_STARTED because from-state and trigger both differ.
   - `trigger_source in ('manual_tpm_override', 'tpm_button')` allowed from any from-state (except the RFS + `no_customer_upload=False` block that only checks `from_state == READY_FOR_SUBMISSION`).
   - TPM click from `NOT_STARTED` with `trigger_source='tpm_button'` passes Guard 5 unchanged.
3. **SP UI engineer coordination remains valuable but no longer strictly required**: SP UI still SHOULD gate the Close button on `delivery_state in ('Not Started', 'Open')` for UX clarity, but HILDA now accepts the transition from either state — belt-and-suspenders coverage of the race.

**Why**:
- (a) **State-machine edge (chosen) vs SP-UI gate only (rejected)**: SP-UI-gate-only leaves HILDA structurally rejecting a semantically-valid TPM intent during the race window; the button would need to become greyed-out briefly, or the click would silently fail. Belt-and-suspenders is cheap (one line) and eliminates any race-window ambiguity.
- (b) **No new trusted `trigger_source` needed**: unlike [D-140] (`submit_to_carrier_task`) or [D-148] (`tpm_drr_final_deliverable`), this transition IS driven by the TPM's own click via the existing `tpm_button` trigger source. Adding a new named trigger would fragment attribution for what is semantically the same operator action as `OPEN → CLOSED`.
- (c) **No Guard 5 rewrite (chosen) vs restructuring Guard 5 around from-state (rejected)**: current Guard 5 is compact and already models the correct policy — "CLOSED requires TPM attribution unless Default-WI-auto-close carve-out applies". A restructure would risk regressing tested transitions.
- (d) **Symmetry with [D-146]'s OPEN → CLOSED edge**: [D-146] added that edge primarily for Default WI auto-close, but its Guard 5 carve-out is item-type-scoped; the general OPEN → CLOSED path for non-Default items via TPM button was implicitly usable. D-149 formalizes the NOT_STARTED analogue for the pre-D-144-window case, keeping the policy symmetric across the two pre-outreach states.
- (e) **Explicit rejection of "let TPMs Delete SP rows for not-applicable items" (alternative)**: SP row deletion would drop the template-authored intent (making it invisible that this item was ever considered), lose the audit trail, and confuse downstream reporting (item counts wouldn't match template expectations). TPM early-close preserves the row + audit + reporting, marks it Closed, and excludes it from outreach/PM-approval/carrier-upload cascades.

**Consequences**:
- (a) `LEGAL_TRANSITIONS[NOT_STARTED]` gains one legal target. Every consumer of the matrix (guards, dashboard preflight queries, admin CLIs) now sees CLOSED as reachable from NOT_STARTED. Only the specific Guard 5 policy path (`tpm_button` + non-RFS from-state) actually permits the transition; automated triggers still get rejected at Guard 5 with `closed_requires_tpm_attribution`.
- (b) **Downstream cascade behaviors validated** for early-closed items (whether via OPEN or NOT_STARTED path):
   - `kickoff_collection_task` filter (`{Not Started, Open}` per [D-144]) naturally excludes CLOSED — no outreach fires.
   - [D-146] `apply_pm_approval_task` sweep counts CLOSED as terminal — early-closed items don't block Default WI auto-close.
   - [D-147] TPM DRR Excel counts CLOSED via `CLOSED_LIKE_STATES = {Closed, ReadyForSubmission, SubmittedToCustomer}` — early-closed items show as Closed in the report.
   - [D-148] Final DRR deliverable transition doesn't touch these items (they don't match the configured `final_deliverable_item_name`).
   - FR-64 Close All Items filter `{ReadyForSubmission, SubmittedToCustomer}` doesn't attempt to re-close already-Closed items.
- (c) **Ph-2 concern surfaced** (logged as STATUS Flag, not part of D-149 scope): attachment router (`_persist_routed_attachment` + `_classify_doc_type`) does not currently check `delivery_state == Closed` before associating an attachment. If an owner sends a late reply after TPM early-close with an attachment matching the item's item_no/subject regex, the attachment would associate to the Closed item (invisible in TPM dashboard's active-item view) or fall through to Default WI. Ph-2 fix: skip Closed items in the router; re-fall-back to Default WI classification. Rare — TPM early-close implies "owner won't send anything for this" — but worth fixing for hygiene.
- (d) **Audit-log attribution preserved**: TPM's click generates a normal `state_transition` audit row with `trigger_source='tpm_button'` + PM/TPM ID captured in `attribution.modified_by`. Same greppability as OPEN → CLOSED early-closes; both anchor to the same operator intent.
- (e) **No config knob needed**: the transition is entirely policy-driven (state machine + Guard 5), no configurable behavior. If a future customer required blocking early-close (e.g., strict template-inflexibility policy), that would be a new Guard predicate + config, not a rollback of this edge.
- (f) **Confirmation items early-close cleanly**: [D-145] 2-hop UnderPMReview → RFS → Closed for Confirmation items assumes PM approval fired; early-close skips that chain entirely. Correct — the item was never applicable, so there's no owner-reply-closes semantic to honor.
- (g) **Default WI early-close**: TPM CAN early-close a Default WI via this path (item_type check happens only in the auto-close carve-out, not in the TPM-attribution acceptance). Would only happen if TPM decides the milestone doesn't need a catch-all for unrouted docs — unusual but not forbidden. Risk: any attachments arriving later that would have gone to Default WI now have nowhere to land. Consistent with the Ph-2 attachment-router Flag above.

**Anchors**: `[D-144]` (import task NS → Open auto-transition — creates the race window this ADR closes), `[D-146]` (companion OPEN → CLOSED edge — symmetric coverage of the pre-outreach window), Guard 5 in [guards.py](core/src/tracker/guards.py) (DEF-20 TPM-attribution policy — unchanged).


## D-150: HILDA-side documents view -- tg-scoped browser with OnlyOffice editor embed + versioned NSD storage

**Date**: 2026-07-22
**Status**: Ratified

**Context**: TPMs need a browser-based view of documents received per milestone, organized by tg_name. Zip archives auto-extract preserving folder structure. Files open in the browser: Word/Excel via OnlyOffice; PDF/HTML native; others download. Every save = new version. Every view/edit/save/download logs to audit. Ph-1 scope: no version resolution UI, no concurrent-edit UX beyond WOPI single-editor lock, no .msg handling, no PDF edit. Deploy: OnlyOffice Community 8.0 in Podman behind local nginx sidecar on corp-allowed 8443 port.

**Decision**:
1. Storage tg-scoped view tree at NSD view/<customer>/<device>/<milestone>/<tg>/<relative-parts>. NSDPath.view_tree() factory + NSDPath.view_version_sibling() for <name>.v<N> archived naming.
2. Versioning via new document_version table (Alembic 0002) with is_current + version_num per view_relative_path. save_view_document() archives current to .v<N> sibling, writes new bytes, flips is_current, inserts new row.
3. Zip auto-extract via write_attachment_to_view_tree() called from inbound_attachment post-persist. Deduped by (customer, device, milestone, tg_name). Zip magic detect, 300MB cap, zip-slip guard (skip .. or absolute). item_type=default and empty tg_name skipped.
4. Deploy: OnlyOffice Community 8.0 + nginx-hilda sidecar on 8443. Shared JWT_SECRET between OnlyOffice env and HILDA dashboard.wopi_jwt_secret. proxy_redirect / /office/ + sub_filter for OnlyOffices absolute-path redirects.
5. UI: /browse/{c}/{d}/{m}/ (landing), /browse/{c}/{d}/{m}/tg/{tg}/ (flat file list per architect Q4 lock), /browse/edit|view|download/{token}. Extension dispatch: xlsx/docx/xlsm/doc/xls/pptx/ppt -> editor; pdf/html/htm/txt/csv/md -> native; else download-only. HMAC-signed 30-min scoped tokens.
6. WOPI Host: 3 endpoints under /wopi/files/{file_id} (CheckFileInfo GET / bytes GET / bytes POST-save). HS256 JWT verification. file_id is urlsafe-base64 of view_relative_path.
7. Audit: 4 new CommunicationLog action_types: document_viewed, document_edit_opened, document_saved, document_downloaded. Once per file-open per architect Q8 lock; per-save; per-download.

**Why**:
- (a) tg-scoped chosen over item-scoped: architect Q4 lock -- TPMs think tg_name, not per-item; item-scoped would duplicate FR-56 Doc Section.
- (b) OnlyOffice over Collabora/MS-Web/PDF-convert: AGPL acceptable; lighter container; better xlsx/docx fidelity; documented WOPI tutorial. Others fail requirement or need Volume License Server.
- (c) nginx sub-path proxying over subdomain: corp VPN allowlist only has 8443; no DNS/cert for subdomain. Tradeoff: sub_filter for asset paths + proxy_redirect for absolute redirects; fragile if OnlyOffice ships new paths, works on 8.0.
- (d) HMAC scoped tokens over session cookies: WOPI needs access_token query param; matches FR-61 download-token pattern. Cookies would need CORS/SameSite negotiation.
- (e) Single-editor WOPI lock: OnlyOffice built-in; Ph-1. Optimistic would need diff/merge UI out of Ph-1 scope.
- (f) Every save = new version, no restore Ph-1: monotonic version_num; prior current becomes .v<N> sibling; audit intact. Ph-2 adds restore UI.
- (g) Default WI excluded: architect Q5. Default WI is unrouted-triage path, not tg-organized deliverables.
- (h) Original zip preserved AND extracted: architect Q7. Same-name inside zip = new version.
- (i) 300MB cap: architect Q7 zip-bomb defense. Adjustable via MAX_ZIP_SIZE_BYTES.
- (j) .msg skipped: architect Q6. No browser stack renders .msg; extract-msg not worth Ph-1 effort.

**Consequences**:
- (a) ~2150 lines added across storage + workflow_engine + dashboard; 47 new tests; 3 Jinja templates.
- (b) OnlyOffice JWT_SECRET MUST be pasted into BOTH docker-compose.yml OnlyOffice env AND dashboard.json wopi_jwt_secret. Mismatch = 401 on every save.
- (c) NSD storage grows: raw + extracted + every .v<N> sibling. No auto-prune Ph-1.
- (d) OnlyOffice built-in lock guards single-user edit; second user sees banner.
- (e) WOPI callback URL is HILDA public origin (reverse_proxy_origin). OnlyOffice server-side fetch goes over same nginx path.
- (f) HILDA_DASHBOARD_REVERSE_PROXY_ORIGIN load-bearing for WOPI URL construction; must be set at deploy (http://105.52.91.33:8443).
- (g) Ph-2: attachment router skip Closed items per D-149 STATUS Flag; view-tree writer inherits same behavior.
- (h) New audit action_types in CommunicationLog; ops greps need updating.
- (i) Pre-existing TestPh1DocSection failures (2) verified unrelated to D-150 via git stash.
- (j) Chunk 1 deploy topology (nginx sidecar + OnlyOffice) landed as manual config on corp box, not in git. Dockerfile.hilda-api hardcodes --port 8443; workaround via compose command: override.

**Anchors**: [D-013] (NSD share convention), [D-114] (dashboard reverse-proxy header), [D-148] / [D-149] (companion ADRs in this session cluster). Commits: beded18 (Chunk 2 storage), fa188fb (Chunk 3 zip extract), plus Chunks 4-7 in the followup commit.


## D-151: TG-scoped attachment routing refinements — TG=1 shortcut + `["default"]` tiebreaker/fallback

**Date**: 2026-07-22
**Status**: Ratified

**Context**: The Ph-1 substring-only attachment router (`ph1_first_pass_substring_only=True` per prior architect direction) currently runs one substring pass on all candidate items. Two operationally-common cases produce poor outcomes:
1. A TG with exactly 1 work item still requires the doc to match one of that item's `item_description` tags to route to it — but semantically, if there is only 1 target, the routing is implicit.
2. When Step 1 matches multiple items within a TG (all sharing a distinctive tag) or matches none of them, the doc falls to the milestone-level Default WI — burying it in the unrouted-triage path when the TG author intended a specific catch-all inside the TG.

**Decision**: 4-stage TG-scoped routing per architect 2026-07-22.

1. **Stage 0 — TG=1 shortcut**: if a TG contains exactly 1 non-Default work item, route directly to it. Skip `item_description` parsing entirely. Resolution: `TG_SINGLE_ITEM`.
2. **Stage 1 — substring match within TG**:
   - Single match → route to it. Resolution: `SUBSTRING_MATCH`.
   - N>1 matches AND one has `["default"]` tag-set entry → route to it. Resolution: `TG_DEFAULT_MULTIMATCH`.
   - N>1 matches AND none has `["default"]` → fall through (return no match for this TG).
   - 0 matches → Stage 2.
3. **Stage 2 — TG-default fallback**: if any item in this TG has `["default"]` as a standalone tag-set entry → route to it. Resolution: `TG_DEFAULT_NOMATCH`.
4. **Stage 3 — milestone Default WI (legacy)**: only reached if no TG produced a match. Resolution: `STAGED_DEFAULT`.

**`"default"` tag semantics** (per architect Q6 lock):
- Reserved literal — no legitimate document filename should contain the string `"default"`.
- Must appear as its OWN singleton tag-set entry: `["default"]`. Cannot be mixed with other tags in the same tag-set.
- Valid: `[["waiver"], ["default"]]` (two tag-sets, `["default"]` standalone), `[["default"]]` (single tag-set).
- Invalid: `[["waiver", "default"]]`, `[["default", "sig_report"]]` — reject at template load via `DeliveryItemBase._v_default_tag_isolation` model validator.
- Case-insensitive: `"Default"`, `"DEFAULT"` also reserved.

**Precedence across multiple TGs**: `TG_DEFAULT_MULTIMATCH` > `SUBSTRING_MATCH` > `TG_SINGLE_ITEM` > `TG_DEFAULT_NOMATCH`. Strong evidence (a tie broken by TG-default in a matched TG) beats weaker evidence (a TG catching purely as fallback). If multiple TGs return matches at the same precedence tier, all matches accumulate — caller decides via existing over-routing threshold.

**Why**:
- (a) **TG=1 shortcut over always-parse (rejected)**: for TGs with a single item, requiring the doc filename to match one of the item's `item_description` tags before routing is friction with no upside — the destination is unambiguous. Shortcut simplifies the mental model for template authors ("if there's only one item, don't bother tagging").
- (b) **`["default"]` tag semantics over `default_item: bool` field (rejected)**: keeping it as a tag-set entry means the same YAML structure (item_description) drives all routing decisions. A separate boolean would fragment the template author mental model.
- (c) **Reserved literal for `"default"` (chosen)**: the word "default" is unlikely to appear in real document filenames (per architect Q6 assertion); reserving it lets the runtime treat `["default"]` as an unambiguous marker. If a filename does contain "default" and matches only the TG-default item, that's a clean single match under Stage 1 and wins — consistent with Q4.
- (d) **Must be standalone tag-set (chosen)**: forbids `["waiver", "default"]` because it creates ambiguity — is that "a waiver document that is also the TG default", or "a specific waiver match"? Standalone enforcement makes the marker unambiguous.
- (e) **Reject multiple `["default"]`-tagged items per TG at template load (Q5-a)**: >1 "default"-tagged items in the same TG is a template configuration error. Runtime defensively takes the first-in-iteration-order winner + logs a warning; formal load-time validator deferred.
- (f) **Stage 2 fires BEFORE milestone Default WI (chosen)**: TG-default is a REAL work item (typed `test_tech_waiver_report`, `waiver`, etc.) inside the TG — the TG author intended it as the catch-all for that TG. Milestone Default WI is a global unrouted-triage path, semantically weaker.
- (g) **Under `ph1_first_pass_substring_only=True`, Stages 2-4 (fuzzy / folder / LLM) remain skipped**: no change to Ph-1 first-pass scope; new TG-scoped logic sits entirely within the substring pass.

**Consequences**:
- (a) **`RoutingResolution` enum grows by 3**: `TG_SINGLE_ITEM` / `TG_DEFAULT_MULTIMATCH` / `TG_DEFAULT_NOMATCH`. Grep-friendly for ops observability.
- (b) **`DeliveryItemBase` gets a new model_validator** (`_v_default_tag_isolation`) that rejects mixed tag-sets containing "default". Existing valid templates unchanged.
- (c) **Router core method `_tg_scoped_route` inserted before Step B1**. Groups candidates by `tg_name`; per TG runs the 4-stage pipeline via `_route_within_tg`. Under `ph1_first_pass_substring_only=True`, if `_tg_scoped_route` returns empty, the router jumps directly to milestone Default WI (B5), skipping B2/B3/B4.
- (d) **Test suite update**: 4 existing router tests had implicit TG=1 (single item in candidates); now require a decoy sibling to exercise Step 1 / fuzzy / LLM / Default-WI-fallback paths as before. 18 new tests cover the 4-stage pipeline + validator.
- (e) **No SP schema change, no NSD path change, no DB migration**. All refinement lives in the router + template validator.
- (f) **Multiple `["default"]`-tagged items in same TG**: runtime accepts (with first-wins + warning); template-load-time rejection deferred to a follow-up cleanup — current runtime is safe.
- (g) **Interaction with future fuzzy / LLM stages (Ph-2)**: when `ph1_first_pass_substring_only=False` in Ph-2, `_tg_scoped_route` still runs first. If empty, fuzzy / folder / LLM run as before. TG-default becomes an intermediate fallback layer between substring and fuzzy.

**Anchors**: **FR-52** (5-step routing pipeline), **FR-78** (milestone Default WI legacy fallback), **FR-82** (item_description nested tag-set model).


## D-152: Email-path IRM wrapping blocks in-browser Edit — magic-byte sniff at ingest, UI + route gate on wrapped files

**Date**: 2026-07-24
**Status**: Ratified

**Context**: Empirical finding during D-150 corp deployment. Same office file, two ingress paths — divergent results:

| Path | Result |
|---|---|
| Windows workstation → SCP → Linux view-tree | Clean (OLE magic `d0cf11e0a1b11ae1` for .doc, PK zip magic for .docx/.xlsx) |
| Windows Outlook → SMTP → OMADM_BOT mailbox → HILDA email poll → view-tree | Wrapped (`<## NASC...` marker prepended, real payload encrypted) |

Corp Exchange / DLP applies **NASCA IRM (Information Rights Management)** to attachments in transit — even internal-to-internal. Empirical scope: legacy `.doc` and `.xls` binary formats get wrapped; modern OOXML `.docx` / `.xlsx` / `.pptx` come through clean. Wrapped payload can only be decrypted by the corp NASCA agent installed on end-user Windows machines; the OnlyOffice container has no such agent. OnlyOffice on a wrapped file silently returns "Other error" after ~10s spin, no useful log.

Ph-1 owner-reply attachments (FR-52 / FR-85 / FR-86) arrive exclusively via email → any `.doc` / `.xls` in that flow will be wrapped and un-editable in-browser.

**Decision**: Magic-byte sniff at save time (`content.startswith(b"<## ")`); persist `is_drm_wrapped: bool` on `document_version`; dashboard UI and `/browse/edit/{token}` both gate on the flag.

1. **Storage** — `DocumentVersionRow` + `DocumentVersionTable` get `is_drm_wrapped: bool = False`. `save_view_document` computes the flag from the incoming bytes (`_NASCA_MAGIC = b"<## "`) and persists on every version row. `TgFileEntry` propagates the flag to the browse listing.
2. **Dashboard listing** — per-file rendering computes `effective_mode = "download" if is_drm_wrapped else _open_mode_for(filename)`. Emits an extra `download_token` alongside `open_token` so the template can always render a Download link.
3. **Template** — wrapped rows show a 🔒 DRM badge (with tooltip explaining the corp IRM behavior) and Download-only. Non-wrapped rows behave as before (Edit for editor-mode, View for native, Download universally).
4. **`/browse/edit/{token}` belt-and-suspenders** — reads first 4 bytes of the current version; if `<## ` returns 415 HTML page with a Download link and audits `document_edit_blocked_drm`. Sits AFTER the config-503 short-circuit so misconfigured deploys still return 503 quickly without a disk read.

**Why**:
- (a) **Magic-byte sniff over extension-based blocklist (rejected)**: extensions lie (see D-150 Chunk 3 zip magic detection). NASCA can wrap any format; magic-byte sniff is authoritative and cheap.
- (b) **Sniff at save time over sniff at open time (chosen)**: browse listings would otherwise need to open every file to render; O(N) disk reads per page load. Persisting the flag on `document_version` keeps listings a single DB query.
- (c) **Also sniff on editor save-back (chosen)**: `save_view_document` is called by both router (attachment ingest) and WOPI PUT (editor save). Editor saves should never produce wrapped bytes — but sniffing unconditionally guards against future flows we haven't thought of.
- (d) **Belt-and-suspenders route check (chosen)**: bookmarked edit tokens or direct-URL callers can bypass the UI gate. 415 at the route is the definitive contract; UI gate is a latency optimization.
- (e) **Download-only over "reject entirely" (chosen)**: TPMs still need access to the wrapped file — they can decrypt it locally in corp-provisioned Office. Blocking download would be strictly worse than status quo.
- (f) **Ph-2 defer for server-side NASCA decrypt integration**: requires corp security engagement (API/CLI availability, key material provisioning, deployment hardening). Non-trivial cross-team work. Ph-1 ships the constraint; Ph-2 revisits if TPM friction warrants.

**Consequences**:
- (a) **Schema change**: `document_version.is_drm_wrapped BOOLEAN DEFAULT FALSE` — additive; existing rows default `false`, which is safe for non-wrapped files and mildly optimistic for wrapped ones (they'll show Edit until the next save re-sniffs). Backfill script deferred; empirically the corpus is small enough that a one-time re-ingest fixes it.
- (b) **Model surface change**: `DocumentVersionRow.is_drm_wrapped`, `TgFileEntry.is_drm_wrapped` — additive, defaults preserve callers.
- (c) **Dashboard route emits `download_token` alongside `open_token`** for every row (always, not just wrapped). Template renders Download universally; simpler than conditional token generation.
- (d) **New audit action_type**: `document_edit_blocked_drm` fires when `/browse/edit` short-circuits on wrapped bytes. Ops grep patterns updated.
- (e) **In-browser Edit remains available** for OOXML (which corp NASCA doesn't wrap) and for any legacy files that arrived through non-email paths (SCP, manual ingest, future direct-SP-fetch). Not a total lockout.
- (f) **UX signal**: 🔒 DRM badge is visible in the tg-files listing; users understand at a glance which files require local-Office edit vs in-browser.
- (g) **Ph-2 open question tracked in STATUS.md**: "NASCA server-side decrypt integration (corp API TBD)".
- (h) **Test suite**: 4 new tests in `TestDrmWrappedFiles` covering (i) NASCA sniff on save, (ii) clean-bytes flag stays false, (iii) listing renders badge + gates Edit, (iv) `/browse/edit` returns 415 with Download link.

**Anchors**: [D-150] (HILDA-side documents view + WOPI Host), **FR-52** (attachment router), **FR-85** / **FR-86** (owner-reply attachment persistence). Discovery credit: empirical test comparing SCP path vs email path 2026-07-24 (architect direct observation).


## D-153: Attachment routing — cross-TG constraint (one doc lives under one TG folder)

**Date**: 2026-07-25
**Status**: Ratified

**Context**: The D-150 view tree stores every document under a single TG folder: `view/<customer>/<device>/<milestone>/<tg_name>/<...>`. There is no physical path where a document lives in two TGs at once. The `/browse/<c>/<d>/<m>/tg/<tg>/` UI + WOPI + versions + history all key off that single-TG path.

The D-151 attachment router's aggregation was designed for fan-out — a document that legitimately matches items in multiple TGs (via `SUBSTRING_MATCH` in each TG, or `TG_SINGLE_ITEM` shortcut in multiple 1-item TGs) would return matches for BOTH items, and the persistence layer would create `DocumentItemAssociation` rows for each. But the underlying NSD write is single-path — the doc bytes land in ONE TG folder — so the second association pointed to bytes physically outside its own TG scope.

Empirical trigger (architect Doc 2/3 review 2026-07-25): the earlier Ph-1 gate on `TG_SINGLE_ITEM` (commit `9d4db3a`) fixed the "solo-item TG catches unmatched docs" failure mode, but architect noted that even cross-TG substring fan-out is incorrect for the same reason — a doc can only physically live under one TG folder, so routing to multiple TGs at once creates broken references from the second TG onwards.

**Decision**: In `_tg_scoped_route`, enforce the constraint that a document routes to items in **at most one TG**. Any cross-TG involvement collapses to the milestone Default WI (`STAGED_DEFAULT`) for TPM triage.

Aggregation algorithm:

1. Per TG, run `_route_within_tg` AND compute `has_substring_evidence = _any_substring_hit(filename, items_in_tg)`.
2. Count TGs with substring evidence:
   - **>1 TG has evidence** → return empty; caller falls to Default WI. (This is the cross-TG rule; rules 2 and 5 in the architect's numbering.)
   - **Exactly 1 TG has evidence** → use that TG's resolution. If the TG's resolution is `None` (intra-TG multi-match with no `["default"]` tiebreaker, per rule 1) → return empty; caller falls to Default WI. Otherwise return the single match.
   - **0 TGs have evidence** → try `TG_SINGLE_ITEM` (Ph-2 only; Ph-1 gate already disables it). If exactly 1 TG contributed a `TG_SINGLE_ITEM` result, route there. If 0 or >1 → return empty.

Rules covered (architect enumeration 2026-07-25):

1. **Intra-TG multi-match, no `["default"]`** → Default WI (`return []`).
2. **Cross-TG: multiple single-matches across different TGs** → Default WI (new — was fan-out under D-151).
3. **No match anywhere** → Default WI.
4. **Ph-1 vs Ph-2 `TG_SINGLE_ITEM`** — Ph-1 disables; Ph-2 enables but only when exactly 1 TG contributes.
5. **Cross-TG: multi-match across TGs** → Default WI (new; was `TG_DEFAULT_MULTIMATCH`-wins-cross-TG under D-151).

**Why**:
- (a) **View-tree physical constraint** is the actual driver. A doc lands at exactly one NSD path; routing metadata that suggests otherwise creates dangling references and confuses the dashboard listing (a file appearing in TG-A's list actually stored under TG-B has no discoverable path).
- (b) **Consistency principle**: if we can't route a doc unambiguously to a single destination, we don't guess — Default WI is the correct place for TPM disambiguation. Silent fan-out was noise, not signal.
- (c) **Rule 1 rewritten to converge with cross-TG rule**: intra-TG multi-match with no `["default"]` tiebreaker now returns empty (Default WI) unconditionally instead of the pre-D-151 "return None + hope another TG contributes" fall-through. Same principle — no confident single destination → Default WI.
- (d) **`TG_DEFAULT_MULTIMATCH` cross-TG behavior changed**: pre-D-153, a TG-A `TG_DEFAULT_MULTIMATCH` beat a TG-B `SUBSTRING_MATCH`. Post-D-153, both TGs having evidence collapses to Default WI. Simpler mental model — "any ambiguity across TGs → Default WI".
- (e) **Owner-scoped candidate filtering (Ph-2)** would make cross-TG scenarios impossible by construction (candidates = only the reply-sender's items, and one owner owns items in one TG). Until that lands, D-153 enforces the constraint at the router layer as a safety net.

**Consequences**:
- (a) **Behavior change from D-151**: some previously fan-out scenarios now go to Default WI. Ph-2 test `test_two_tg1_tgs_no_substring_hits_both_via_fallback` was inverted (2 items → empty); new tests added for the D-153 constraint.
- (b) **New method `_any_substring_hit`**: mirrors Stage 1 iteration; slightly redundant compute (called per-TG on the aggregation pass in addition to `_route_within_tg`'s own substring loop). Acceptable — router candidate lists are small (< 50 items typically).
- (c) **Routing telemetry**: `RoutingResolution.SUBSTRING_MATCH` is used as the "no-match sentinel" return value when the router falls through to Default WI (unchanged from pre-D-153). Consider a distinct `RoutingResolution.CROSS_TG_AMBIGUITY` value in a future cleanup for cleaner audit signal.
- (d) **Test suite**: 5 new tests in `TestTgScopedRoute` covering the D-153 cases (cross-TG single-matches, cross-TG default-multimatch, intra-TG multi-with-cross-TG-single, intra-TG multi alone, and single-TG-evidence happy path). 29/29 passing + 1 skipped (TG_DEFAULT_NOMATCH still Ph-2).
- (e) **Ph-2 restore note**: when owner-scoped candidate filtering lands, this constraint becomes structurally impossible to violate (one owner = one TG). The D-153 aggregation code stays as belt-and-suspenders — cheap correctness guarantee even if the owner-scoping filter regresses.

**Anchors**: [D-150] (view-tree TG folder scoping is the physical constraint), [D-151] (attachment routing pipeline this refines), **FR-52** (5-step routing), **FR-79** (previously called for multi-item fan-out; D-153 constrains that to intra-TG only). Discovery credit: architect Doc 2/3 review 2026-07-25 (empirical Ph-1 early-access trace).


## D-154: Reserved literal `all-15-digits-imei` for IMEI-shaped Excel filenames

**Date**: 2026-07-26
**Status**: Ratified

**Context**: Certain HW PL work items receive documents whose filenames are exactly a 15-digit IMEI (International Mobile Equipment Identity) plus an Excel extension — e.g., `357123456789012.xlsx`. Each doc has a DIFFERENT IMEI (one per handset), so no static substring in `item_description` can cover the case. Template authors have no way to enumerate "all possible IMEIs" — they need a shape-based match, not a substring-based one.

Ph-1 uses a static substring routing pass (D-151 + D-153); introducing a full regex-per-item extension mechanism is broader than needed for the observed scenario. The concrete requirement is narrow: **IMEI-shaped filename + Excel extension → route to a specific item**.

**Decision**: Recognize a new reserved literal `"all-15-digits-imei"` in `item_description`, mirroring the D-151 `"default"` pattern:

1. **Template shape** — the literal appears as a standalone tag-group entry:
   ```yaml
   item_description:
     - ["all-15-digits-imei"]
   ```
   Cannot be mixed with other tags in the same group (validator rejects `["imei", "all-15-digits-imei"]` at template load — same isolation rule as `["default"]`).

2. **Router match** — when scanning item_description tag-groups during Stage 1 substring pass, the reserved literal group matches iff the filename basename satisfies:
   - Exactly 15 ASCII digits followed by an Excel extension, one of `.xls` / `.xlsx` / `.xlsm` / `.xlsb`
   - Regex: `^\d{15}\.(xls|xlsx|xlsm|xlsb)$` (case-insensitive; filename is lowercased upstream)
   - Anything else (14 digits, 16 digits, alpha prefix, PDF extension, etc.) does NOT match this group

3. **Interaction with existing pipeline**:
   - IMEI-match counts as a normal SUBSTRING_MATCH for the item — D-151 tiebreakers and D-153 cross-TG constraint apply unchanged
   - An item can have BOTH the IMEI reserved literal AND regular substring tags:
     ```yaml
     item_description:
       - ["imei"]                  # normal substring: filename contains "imei"
       - ["all-15-digits-imei"]    # reserved literal: filename is 15-digit Excel
     ```
     Item matches on EITHER path (OR semantics between groups).
   - Cross-TG rule (D-153) still applies: if an IMEI-shaped filename also has substring evidence in another TG, the file collapses to Default WI. Prevents accidental fan-out.

**Why**:
- (a) **Narrow surface** — a reserved literal handles the concrete case without introducing a new schema column, Pydantic field, DB migration, or template-shape extension. Reuses the existing tag-group iteration.
- (b) **Semantic clarity for template authors** — `["all-15-digits-imei"]` reads as "this item receives all-15-digit-IMEI Excel files", closer to natural language than a regex would be. Regex `^\d{15}\.(xls|xlsx|xlsm|xlsb)$` is opaque and error-prone (missed anchor, wrong escape, catastrophic backtracking).
- (c) **Precedent from D-151** — the `"default"` reserved literal established the same shape. Consistent mental model: "some strings in item_description are ordinary substring tags; some are reserved literals with special-case matching semantics; validator enforces isolation."
- (d) **Excel-extension gate is intentional** — filenames that happen to be 15 digits with a non-Excel extension (e.g., `123456789012345.pdf`) don't automatically match. Prevents false positives from unrelated numeric IDs (order numbers, ticket IDs) that happen to be 15 digits. Extensible in a future ADR if other formats need coverage.
- (e) **Ph-2 escape hatch retained** — if IMEI proves to be one of many dynamic-identifier patterns (serial numbers, timestamps, part numbers), we upgrade to a proper `filename_pattern: str` regex field on the template (Option A from the 2026-07-26 architect discussion) and migrate all reserved literals to the generic mechanism. For now, one reserved literal is cheaper than the schema change.

**Consequences**:
- (a) **New reserved literal** — the constant `Fr52AttachmentRouter._IMEI_XLS_TAG = "all-15-digits-imei"` lives in `attachment_router.py` alongside the compiled regex `_IMEI_XLS_REGEX`.
- (b) **Router refactor** — the ad-hoc "any group matches" loop in Stage 1 + `_any_substring_hit` (D-153) now delegates to `_any_group_matches(filename, groups)`. Single source of truth for what "a group matches this filename" means, including reserved literals. Zero behavior change for existing substring tags.
- (c) **Validator extension** — `DeliveryItemBase._v_default_tag_isolation` (D-151) generalized to check a `_RESERVED_LITERALS` set including both `"default"` and `"all-15-digits-imei"`. Same error shape either way.
- (d) **Template authors** — for the IMEI Excel item (Ph-1 MMK/DRR item_no=39), template.yaml gets `item_description: [["all-15-digits-imei"]]`. That single entry replaces trying to enumerate 15-digit patterns.
- (e) **Extension list is intentional not exhaustive** — .xlsx, .xls, .xlsm, .xlsb covered. If IMEI files ever arrive as .csv or .docx, add to `_IMEI_XLS_REGEX` alternation with a follow-up commit. Small enough that ad-hoc extension is fine.
- (f) **Test suite** — 7 new tests in `TestImeiExcelReservedLiteral` (all extensions, uppercase ext, 14/16-digit rejection, alpha-prefix rejection, cross-TG-collapse per D-153) + 2 in `TestImeiTagValidation` (isolation validator). Total router test count 39 → verifies existing D-151/D-153 behavior unbroken.
- (g) **Extensibility path**: if we accumulate 3+ reserved literals, refactor to a `_RESERVED_LITERAL_MATCHERS` dict `{tag_string: matcher_fn}` and register per-literal. For 2 literals a bespoke branch reads cleaner.

**Anchors**: [D-151] (reserved literal pattern, isolation rule), [D-153] (cross-TG constraint still applies to IMEI matches), **FR-82** (item_description tag semantics). Discovery credit: architect Ph-1 early-access observation of HW PL IMEI Excel filenames 2026-07-26.
