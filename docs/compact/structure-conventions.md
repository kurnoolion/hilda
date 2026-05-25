# Structure conventions

Defines the repository layout, what counts as a "module", and how Python visibility maps to `pub` / `internal` for the `regen-map` skill.

Single language: **Python** (FastAPI + asyncio + Celery) for the containerized HILDA services (Docker Compose Ph-1/Ph-2 per `[D-026]`; MicroK8s single-node Ph-3+ per `[D-022]` / `[D-043]`). Workflow engine is **Celery + Redis broker (Ph-1/Ph-2) / Celery + RabbitMQ Quorum Queues (Ph-3+)** per `[D-022]`; Temporal is deferred to Ph-3+ if durable multi-step orchestration becomes necessary. Layout mirrors `the reference implementation` per `[D-001]` — that project is the live reference for the three-tier convention. Edit as conventions evolve.

## Top-level layout

```
<repo_root>/
├── core/                                       # AI-generated; manual edits exceptional [D-001]
│   ├── __init__.py
│   ├── src/                                    # Python source — one MODULE.md per package
│   │   ├── __init__.py
│   │   └── <module>/
│   │       ├── __init__.py
│   │       ├── MODULE.md
│   │       ├── <module>_cli.py                 # CLI entrypoint (when applicable)
│   │       └── ...
│   └── tests/                                  # pytest suite (test_<module>.py per package)
│       ├── __init__.py
│       └── test_<module>.py
├── customizations/                             # AI-scaffolded; humans complete / edit [D-001]
│   ├── __init__.py
│   ├── sharepoint_config/                      # [D-004] / [D-020]: per-deployment SP binding
│   │   └── <deployment>.yaml                   # site URL, list names, column internal names, web-part wiring, SP-alert subscriber address
│   ├── template_schemas/                       # FR-39/40/41: per-customer template definitions
│   │   └── <customer_slug>/
│   │       ├── template.yaml                   # milestones → delivery-items hierarchy
│   │       ├── tg_groups.yaml                  # tg_name → {tg_owner, email_group_alias, corp_id_list, default_cc_list}
│   │       └── parser_schema.yaml              # per-customer test-report parser spec [D-011]
│   ├── rules/                                  # FR-30: AutomationRules as YAML (3-tier resolution)
│   │   ├── global/
│   │   │   └── defaults.yaml
│   │   └── <customer_slug>/
│   │       ├── customer_rules.yaml
│   │       └── <device_slug>/
│   │           └── device_rules.yaml
│   ├── test_report_parsers/                    # [D-011]: per-customer generated parsers
│   │   └── <customer_slug>/
│   ├── customer_adapters/                      # per-customer submission-system adapters
│   │   └── <customer_slug>/
│   ├── messenger/                              # [D-009] / [D-016]: proprietary messenger adapters
│   │   └── <proprietary>_adapter.py
│   ├── issue_tracker/                          # [D-003]: proprietary corp PLM adapter
│   │   └── <proprietary>_adapter.py
│   └── <other>/                                # additional scaffolded customizations
│       ├── __init__.py
│       ├── MODULE.md
│       └── tests/
├── config/                                     # Per-module install-time settings [D-001]
│   ├── global.json                             # Cross-module settings: env name, log levels, feature flags, hilda.corp base URL, default timeouts
│   ├── <module>.json                           # Per-module JSON; one file per module needing config
│   └── README.md                               # What each file controls, who reads it; env-var override pattern documented here
├── sharepoint/                                 # SharePoint 2017 classic web parts (non-Python) — owned by the SP UI engineer; see "SharePoint UI boundary" section below
│   └── ...                                     # Documented from src/sharepoint_integration/MODULE.md
├── deploy/                                     # Deployment artifacts (per SYSTEM.md §5 / §8)
│   ├── compose/                                # Ph-1/Ph-2 Docker Compose
│   │   ├── docker-compose.yaml                 # Base Compose file (all services)
│   │   ├── docker-compose.dev.yaml             # Dev overrides (mock-sharepoint profile, debug ports)
│   │   └── .env.example                        # Env var template (actual .env sops-encrypted per [D-038])
│   ├── secrets/                                # sops-encrypted .env files per [D-038]
│   │   └── <service>.env.enc
│   ├── scripts/                                # Operational scripts
│   │   └── deploy.sh                           # git pull → sops --decrypt → docker compose pull → up -d → alembic upgrade
│   ├── charts/hilda/                           # Ph-3+ Helm chart placeholder per [D-024]
│   │   └── README.md                           # Placeholder in Ph-1/Ph-2; populated when migrating to MicroK8s
│   ├── grafana/dashboards/                     # Dashboards-as-code per [D-023]
│   ├── prometheus/alerts/                      # Alerts-as-code per [D-023]
│   └── Dockerfile                              # Single HILDA image (all 4 hilda-* services share it)
├── docs/compact/                               # COMPACT state files
├── .claude/                                    # COMPACT skills (when vendored)
├── CLAUDE.md  README.md  CONTRIBUTING.md
├── requirements.txt
├── pyproject.toml                              # If used for tooling / poetry / build config
└── (operational scripts and metadata)
```

Note: the SharePoint version is frozen at 2017 (vanilla List views + classic web parts). Pure SharePoint web-part code (XSLT / JavaScript / classic web part assemblies) lives outside `core/` under `sharepoint/`. The Python-side server adapter that talks to SharePoint lives at `core/src/sharepoint_integration/` and documents the `sharepoint/` surface from its own MODULE.md.

### SharePoint UI boundary — who develops what

HILDA spans two distinct codebases with two distinct owners and two distinct deployment paths. They share this repo but never share Python imports:

| Component | Location | Owner | Deployed where, how |
|---|---|---|---|
| **SharePoint 2017 web parts** — XSLT, classic web part assemblies, JavaScript that runs in the PM/TPM browser and calls SP REST API; button-click handlers that modify SP list fields to trigger SP alerts | `sharepoint/` (root) | **SP UI engineer** | Corp SharePoint server, via SP-side mechanism (PowerShell / SP Solution Package / manual upload). Independent of HILDA's Docker deploy. |
| **HILDA Python adapter** — code that consumes SP data via REST API (HILDA → SP outbound + Kerberos), parses inbound SP-alert emails per SYSTEM.md §3.1, writes results back to SP lists | `core/src/sharepoint_integration/` | **HILDA team / dev LLM** | HILDA PC (lab subnet) inside `hilda-api` and `hilda-worker` containers. Docker Compose Ph-1/Ph-2 → MicroK8s Ph-3+. |
| **Mock SP harness** — local fake SP server for HILDA pytest / dev runs; never serves real PMs | `mock-sharepoint` Docker service (dev profile per SYSTEM.md §5) | **HILDA team** | Dev machine / lab subnet only — never deployed to corp net or prod |
| **Per-deployment SP binding** — site URLs, list internal names, column internal names, lookup field IDs, SP-alert subscriber config | `customizations/sharepoint_config/<deployment>.yaml` | Ops / SP UI engineer at deployment time | Bind-mounted into HILDA containers per `[D-025]` |

**Shared contract between the two sides:**
1. The SP list/column schema generated from canonical Pydantic models per `[D-046]` — SP UI engineer reads it (knows what columns are available); HILDA writes to it.
2. The SP alert email format (see SYSTEM.md §3.1) — SP UI engineer's button-click handlers modify list fields that trigger alerts; HILDA's `sp_alert_parser` sub-module in `core/src/email_service/` parses the resulting emails.

**Flow during development:**
1. SP UI engineer prototypes a new button + list field on corp SP
2. They send a sample alert email to the HILDA team (this is the loop that produced the format we used for SYSTEM.md §3.1)
3. HILDA team designs the Python-side parser + Celery task in `core/src/`
4. Mock SP harness lets HILDA pytest validate the parser on synthetic emails
5. SP UI engineer deploys the production web parts to corp SP; HILDA team deploys the Python services to HILDA PC; the loop closes in production

**The HILDA dev LLM does NOT write `sharepoint/` web-part code.** XSLT / classic web part development is the SP UI engineer's domain — they have access to the corp SP environment, which the dev LLM cannot reach per `[D-002]` chat-mediated boundary.

## Module definition

**Core modules.** Each directory under `core/src/` that contains an `__init__.py` is a core module. Its `MODULE.md` lives at `core/src/<module>/MODULE.md`. Tests live at `core/tests/test_<module>.py`. **Every functional module is independently testable per `[D-005]`** — it ships a `<module>_cli.py` with a `main()` function invoked as `python -m core.src.<module>.<module>_cli`, supporting at minimum `--diagnostic` (emits compact RPT / MET / QC per `[D-002]`) and `--mock` / `--dry-run` for any side-effect operations (sending email, posting to issue trackers, contacting customer systems, mutating SharePoint). UI / web-facing modules substitute a mock web harness — a local test server or `httpx.TestClient`-style entry point that exercises the module against mock SharePoint data without requiring the production environment.

**Customization modules.** Each top-level directory under `customizations/` is a customization module. Its `MODULE.md` lives at `customizations/<name>/MODULE.md`. Tests are co-located: `customizations/<name>/tests/`.

Nested packages (e.g. `core/src/web/routes/`) are treated as part of their parent module's contract unless their public surface is large enough to warrant splitting; in that case they may be promoted to first-class modules with their own MODULE.md. Promotion is a hard-flag event — log a DECISIONS entry.

Files at the repo root (`requirements.txt`, operational scripts, etc.) are operational metadata, not modules — they do not get MODULE.md files and are not included in `MAP.md`.

## Visibility mapping

Python has no language-level `pub` / `internal` distinction. The convention for this project:

- **pub**: top-level identifiers (`class Foo`, `def bar`, module-level constants) whose name does not start with an underscore
- **internal**: identifiers whose name starts with a single underscore (`_helper`, `_InternalType`); names absent from `__all__` when `__all__` is used; dunder-prefixed names (`__name`, name-mangled within classes)
- **pub (curated)**: identifiers re-exported through the module's `__init__.py` — either via explicit imports or via `__all__`. When a module uses `__all__`, the curated list is authoritative for its public surface.

When a module's `__init__.py` is empty, the public surface is the union of un-underscored top-level identifiers across all `.py` files in that module's directory.

**Public surface of a core module is computed from `core/src/<module>/` only.** Customizations expose their own public surface via their own MODULE.md; they are not part of any core module's surface, even when they implement Protocols defined in core.

## Protocol boundaries

Durable contracts that span the core/customizations boundary are expressed as `typing.Protocol` (structural typing) defined in `core/`. Implementations live in dedicated modules and clients import the Protocol, not the implementation. Concrete Protocols for HILDA (refined during architecture as each module is drafted):

- `IssueTracker` `[D-008]` — intermediate primitives: `open_issue`, `close_issue`, `add_comment`, `upload_file`, etc. Implementations: customer Jira adapter under `core/src/issue_tracker/jira_adapter.py` (public REST spec; outbound polling per FR-25); proprietary corp PLM adapter under `customizations/issue_tracker/` (generated by the API Spec Ingestor per `[D-003]`; **HILDA calls via `corp_plm_gateway` on the PLM gateway PC** per SYSTEM.md §3 — not directly to corp PLM).
- `Messenger` `[D-009]` — intermediate primitives: `send_message`, `receive_message_as_rest`, etc. Implementations: Slack adapter under `core/src/messenger/slack_adapter.py` (`slack_sdk`); proprietary internal messenger adapter under `customizations/messenger/<proprietary>_adapter.py` (generated by the Ingestor per `[D-016]`; **HILDA calls via `corp_messenger_gateway` on the reverse-proxy PC** per SYSTEM.md §3 — not directly to corp messenger).
- `SharePointBackend` / SharePoint config schema `[D-004]` — typed config (Pydantic) defined in `core/src/sharepoint_integration/`; deployment-specific values (site URLs, list internal names, lookup field IDs, web-part wiring, SP-alert subscriber address and change-trigger settings per SYSTEM.md §3.1) supplied from `customizations/sharepoint_config/<deployment>.yaml`. Note: document storage is **not** SP config — all artifacts live on NSD per `[D-013]` / `[D-041]`; NSD configuration surface is owned by `core/src/storage/` or a dedicated NSD-client module (architecture-phase decision).
- `CustomerAdapter` — per-customer external-system adapter (`submit_item`, `get_status`, `post_comment`, `upload_attachment`). Implementations under `core/src/customer_adapters/` (Jira, generic email, our file storage) and `customizations/customer_adapters/` for sensitive per-customer connectors.
- `LLMProvider` — abstracts the runtime LLM Gateway client; implementations under `core/src/llm/` for default providers (corp-proxied or on-prem) and `customizations/llm/` for any proprietary or per-deployment provider. **Distinct** from the on-prem code-generation LLM consumed by the API Spec Ingestor (`core/src/api_spec_ingestor/`); that one has its own configuration surface and is invoked off the runtime path.
- `CredentialBackend` — abstracts the secrets store: **sops-encrypted env files** with age keys (Ph-1/Ph-2 per `[D-019]` v1 / `[D-038]`); **HashiCorp Vault** (Ph-3+ per `[D-019]` v2). A stable interface lets the deployment swap backends without touching call sites. Ph-1/Ph-2 holds a shared HILDA ops-team credential set per customer system (not per-PM); Ph-3+ adds per-PM credential blobs in Vault per DEF-14.
- `TrackingChannel` — abstracts the inbound/outbound communication adapters (Email, Messenger, Issue Tracker) so the rule engine treats them uniformly. May reduce to a thin façade over `Messenger` and `IssueTracker` plus a separate Email adapter — confirmed during architecture.

Changing a Protocol signature is a hard-flag event — log a `D-XXX` entry and switch back to architecture phase before implementing.

### Sync-API wrapping convention `[D-008]` `[D-009]`

Many proprietary APIs HILDA integrates with — internal issue trackers, internal messengers, some customer systems — are sync/blocking. Protocol surfaces (`IssueTracker`, `Messenger`, etc.) are async-native, so adapters that wrap sync libraries follow this convention:

- **Sync calls run inside `asyncio.to_thread(...)` or a dedicated `ThreadPoolExecutor`.** Default to `asyncio.to_thread` for simplicity. Use a dedicated `ThreadPoolExecutor` (configured via the adapter's constructor or its `config/<module>.json`) when the adapter needs to bound concurrency, isolate failures, or share a pool across operations.
- **Cancellation is best-effort.** Cancelling the awaiter cancels the asyncio task; the wrapping thread runs to completion. Document this in the adapter's MODULE.md Invariants. Don't promise hard cancellation — Python cannot safely kill threads.
- **Every method accepts `timeout_s: float | None`.** When set, the adapter wraps the sync call with `asyncio.wait_for`, treating timeout as the cancellation signal (best-effort per above). When `None`, the adapter falls back to its configured default timeout (set in `config/<module>.json`).
- **Streaming uploads.** `AttachmentInput` accepts `Path | AsyncIterable[bytes]`. Adapters consuming `AsyncIterable[bytes]` from inside a sync wrapper bridge via a shared `queue.Queue` — async producer puts, sync consumer reads — or use a small async-to-sync adapter pattern that buffers.
- **Progress reporting from sync to async.** When a long sync operation needs to surface progress (e.g., for a CLI `--diagnostic` output), the wrapping thread writes to a `queue.Queue` (sync-safe) that an async pump task drains. Only required for operations exceeding ~10s; shorter ones report on completion only.
- **No global mutable state in adapters.** Adapters are constructed with their config + thread pool and operate as instance state only. Module-level singletons that hide a thread pool are forbidden.
- **Two-stage IO for corp-system adapters (corp messenger, corp PLM).** Per SYSTEM.md §3, the HILDA-side adapter is a thin sync HTTP client of a HILDA-team-owned gateway app running on a corp-net intake PC (`corp_messenger_gateway` on the reverse-proxy PC; `corp_plm_gateway` on the PLM gateway PC). The gateway app is the actual sync client of the corp system (corp Slack / corp PLM). From HILDA's adapter perspective, the call is one sync HTTP request wrapped in `asyncio.to_thread`; the gateway app absorbs the latency of talking to the corp system. **`timeout_s` must be honored on the gateway side too** — the gateway app either propagates the timeout to its backend call or enforces it locally and returns a structured HTTP 504 + error code to HILDA. For inbound (corp system → HILDA), the gateway app POSTs to `hilda-api`'s receive endpoint (`/webhooks/messenger`, `/webhooks/plm`) over the lab subnet; `hilda-api` validates source IP and dispatches into the relevant module. Customer JIRA (`FR-25`) is the exception — polling-based outbound from HILDA only, no gateway PC; runs in `asyncio.to_thread`.
- **UI-blocking is never an issue at the adapter layer.** All long-running adapter calls go through **Celery tasks** per `[D-022]`; the SP UI awaits task completion by polling SP list fields that HILDA writes back to as the task progresses (per SYSTEM.md §3.1 — corp browser → SP REST polling pattern). Direct UI invocation (via the IT-admin's reverse proxy → `hilda-api`) is limited to fast operations (`get_issue`, `get_message`, document-download token resolution per FR-61) or fast `--mock` mode for harness tests.

### Ingestor / Profiler pattern — proprietary inputs to on-prem code generation

HILDA has **three Ingestor / Profiler modules**, all following the same pattern: dev LLM cannot read the proprietary input; an on-prem open-source LLM (Gemma3:12b / Qwen / configurable per `[D-007]`) ingests it and produces concrete artifacts under `customizations/`. All three ship diagnostic CLIs emitting compact RPT / MET / QC reports per `[D-002]` so the dev can debug ingestion / generation without exposing the proprietary content.

**API Spec Ingestor `[D-003]`** — `core/src/api_spec_ingestor/`. Reads proprietary REST API specs for proprietary issue trackers and messengers; produces Python adapter modules conforming to the `IssueTracker` `[D-008]` / `Messenger` `[D-009]` Protocols at `customizations/<system>/<proprietary>_adapter.py`. v1 also processes the public Jira OpenAPI spec for `core/src/issue_tracker/jira_adapter.py` as the Ingestor's first end-to-end exercise.

**Template Schema Ingestor `[D-010]`** — `core/src/template_schema_ingestor/`. Reads proprietary customer-template Excel schema specs (column structure, field extensions, validation rules, enumerated values, customer-specific automation-rule overrides); produces, under `customizations/template_schemas/<customer>/`: (a) Pydantic validators conforming to the generic meta-schema in `core/src/template_schema/`; (b) Excel parsers / column mappers; (c) SharePoint-List column-mapping configs feeding `customizations/sharepoint_config/`; (d) customer-specific `AutomationRules` configurations consumed by the runtime rule engine.

**Test Report Document Profiler `[D-011]`** — `core/src/test_report_profiler/`. Reads historical proprietary test reports across mixed file types (Excel: `xlsx` / `xls` / `xlsm` / `csv`; Word: `doc` / `docx`; PDF) and emits per-customer parsers + classification artifacts into `customizations/test_report_parsers/<customer>/`. Generated runtime artifacts are deterministic Python parsers (no runtime LLM) — per-format parsers emit `(item_id, status, [waiver_ref], [comment])` tuples; the canonical `final | interim` classifier and status enum live in `core/src/test_report/`. Classification rule: a report is **`final`** iff every item is in `{passed, non-applicable, waived}` AND every `failed` item carries a `waiver_ref` (reclassifying it as `waived`); otherwise the report is **`interim`**.

**Hard invariant**: the dev LLM never reads proprietary API specs `[D-003]`, proprietary customer-template schemas `[D-010]`, or proprietary historical test reports `[D-011]`; never sees their content via tool calls; and does not request, summarize, or paraphrase their structure. It interacts only with: the public meta-schemas and intermediate Protocols defined in `core/`; the Ingestors' / Profiler's compact diagnostic output; and the generated artifacts already committed under `customizations/`. Captured as a phase-prompt invariant in `phases/development.md`.

**Placeholder convention for proprietary identifiers in `customizations/` scaffolds**: when Claude writes a scaffold under `customizations/` that needs to reference a proprietary external system, customer, device, or person, the proprietary identifier never appears in the scaffold itself. Stable placeholders `<SYS0>`, `<CUST0>`, `<DEV0>`, `<TG0>`, `<PERSON0>`, `<URL0>` (numbered per-file) are used instead, with a placeholder registry comment block at the top of each scaffold mapping each placeholder to a non-proprietary slug. Real values flow in at runtime via env vars / sops-encrypted `.env` files per `[D-038]` / customer YAML. Full convention in `cline-playbooks/placeholder-convention.md` — anchored by `[D-002]` and `[D-027]`.

## Cross-tier dependency rules

**Reference-imported from the reference implementation (subject to ratification via a dedicated DECISIONS entry during architecture).** `core/` and `customizations/` may import each other freely. Real dep flow is bidirectional: core defines Protocols that customizations implement; customizations expose data (e.g., per-customer templates, AutomationRules) that core's rule engine and adapters consume. Commits don't mark AI vs. human authorship — directory implies it. Manual edits to core are exceptional, not forbidden.

`drift-check` and `regen-map` accept cross-tier edges in either direction. There is no CI rule for "AI-only commits to core" — code review governs.

## Config format

**Reference-imported from the reference implementation (subject to ratification via a dedicated DECISIONS entry during architecture).** Two tiers under `config/`:

- **`config/global.json`** — cross-module install-time settings: env name (`dev` / `test` / `prod`), log level defaults, feature flags, `hilda.corp` base URL, default timeouts, shared file-path roots. Read by any module that needs cross-cutting context.
- **`config/<module>.json`** — per-module install-time settings (default endpoints, model names, module-specific timeouts, host/port bindings). One file per module needing config. Modules read their own config file plus `global.json`. New module config = new file under `config/`.

**Env-var override pattern.** Both `global.json` and per-module configs follow the 3-tier precedence already implemented in `sharepoint_integration` via `from_sources()`: **CLI overrides > env vars > config file > defaults**. Env var naming is `HILDA_<MODULE>_<KEY>` (e.g., `HILDA_SHAREPOINT_SITE_URL`, `HILDA_LLM_API_KEY`). This lets sops-encrypted `.env` files per `[D-038]` override JSON defaults at deploy time without rebuilding the image. Per-environment values come from env vars, **not** from environment-nested directories under `config/`.

**Runtime data is not config.** Customer templates (`customizations/template_schemas/`), AutomationRules (`customizations/rules/`), AI checklists, and PM credentials are **runtime / domain data** managed by PM team leads / PMs / sops or Vault. They live under `customizations/` (templates and rules) or in the secrets backend (credentials), not in `config/`. Repo-side YAML seed/fixture forms for tests live alongside the relevant module under `core/src/<module>/fixtures/` or `customizations/<name>/fixtures/`, not under `config/`.

## Description source

Used by `regen-map` to generate per-file one-liners in the **Project File Structure** section of `MAP.md`:

- `*.py`: first line of the module-level docstring. If absent, no description.
- `*.sh`: first line of the top comment block after the shebang. If absent, no description.
- `*.json` / `*.yaml` / `*.yml`: first line if it's a comment (`# ...` for YAML; JSON has no comment syntax — fall back to a sibling `<file>.md` description if present).
- Directories with `MODULE.md`: first sentence of the **Purpose** section.
- Other files / directories: no automatic description (path-only row).

Rows are alphabetical within each directory; files and directories intermix alphabetically.

## Module doc schema

Each module has a `MODULE.md` with the following curated sections (plus a regen-only Structure section):

- **Owner** *(optional)* — single contributor owning the module; omit if shared or unassigned.
- **Purpose** — 1-2 sentences. Cite served `FR-N` / `NFR-N` IDs from `requirements.md` where applicable.
- **Public surface** — signatures + semantics. For core modules, includes Protocol implementations callers rely on. For customizations modules, calls out which Protocols (defined in core) the customization implements. **Includes the test interface**: CLI invocation line + flags (e.g., `python -m core.src.<module>.<module>_cli --diagnostic --mock`) for functional modules, or the mock-harness entry point for UI modules. Required per `[D-005]`.
- **Invariants** — what callers can count on (concurrency / asyncio behavior, state lifecycle, ordering, idempotency, error-code contract).
- **Key choices** — each linked to DECISIONS.md by `[D-XXX]`.
- **Non-goals** — deliberate omissions.
- **Structure** — regen-only; bounded by `<!-- BEGIN:STRUCTURE -->` / `<!-- END:STRUCTURE -->`; never hand-edited.
- **Depends on** / **Depended on by** — links to other MODULE.md files. Cross-tier edges (core ↔ customizations) are valid in either direction.
- **Deferred** *(optional)* — planned-but-unbuilt behaviors for this module. Read by `drift-check` to classify matching items as `[DEFERRED]` instead of drift.

## Depends on / Depended on by — semantics

These sections capture **either** direct code imports **or** artifact / data consumption (e.g., a module reading JSON / SharePoint List rows produced by another module). A module may legitimately declare a peer as a dependency without importing any of its symbols, when the coupling is through a shared on-disk artifact or a SharePoint List. `regen-map` and `drift-check` treat both forms as valid — a declared-but-not-imported edge is not flagged as drift on its own.

## Chat-mediated collaboration conventions `[D-002]`

The dev LLM cannot reach production. Every cross-boundary artifact and every service failure must be expressible in a compact, paste-friendly form so PMs / devs can drop it into chat verbatim. Reference implementation: `the reference implementation/core/src/pipeline/error_codes.py` and `the reference implementation/core/src/pipeline/report.py`.

**Error codes**:
- Format: `{MODULE}-{SEVERITY}{NUMBER}` — e.g., `EML-E001`, `WFL-W003`, `CRD-E012`.
- `MODULE` is a 3-letter prefix per service / module (assigned during architecture as each module is drafted; one prefix per module, registered once in the central registry).
- `SEVERITY`: `E` (error) or `W` (warning).
- `NUMBER`: 3-digit, monotonic within a (module, severity) pair.
- All codes registered in a single `error_codes.py` (exact path TBD during architecture; one registry per repo) using an `ErrorDef`-style dataclass with `code` / `message` / `hint` and a `PipelineError`-style exception with `code` / `context` / `cause`. Mirror the reference implementation's structure.

**Compact report types** (one record per line, no proprietary content):
- `RPT` — run / activity report. Per-stage / per-service line: `{prefix} {status} {elapsed} {key=value compact stats}`. Example: `EML OK 12s sent=42 bounced=1 parse_fail=0`.
- `MET` — metrics snapshot for the chat surface. One key=value per line; aggregate counters, latency percentiles (when relevant), queue depths. No payload content.
- `FIX` — correction feedback. PM types this when overriding an AI assessment / drafted response / customer template field / AutomationRule. Format: `FIX {env} {artifact}` then bounded fields per `FIX_TEMPLATES` (added/removed/renamed counts, optional bounded notes).
- `QC` — quality check. Fixed-field: numbers + Y/N + bounded enum tokens. Used when a PM reviews an AI assessment or an automated submission preview and signals back "is each axis OK?" Never a free-prose summary of the artifact content.

**Hard invariants**:
- Compact reports never contain customer test report fragments, tech report content, waiver text, customer email/RFI body, customer-system payloads, R&D reply prose, PM credential material, or any other proprietary document content.
- Every long-running service exposes a diagnostic mode (`<module>_cli.py --diagnostic` or equivalent) that emits its compact RPT block.
- Every new artifact type is born with an error-code prefix + compact schema + QC template — not retrofitted later.

`drift-check` and `close-session` hard-flag MODULE.md or code that introduces an artifact type without the paired error-code prefix, compact schema, and QC template.

## Retrofit skeleton sentinel

Not applicable — this project was greenfield-initialized, not retrofitted. If a future `--retrofit` pass occurs, MODULE.md files seeded by it will begin with the marker `<!-- retrofit: skeleton -->`. While present, `close-session` treats curated-section edits as expected (not hard flags). The marker is removed once the MODULE.md is fully curated; from that point, normal audit rules apply.
