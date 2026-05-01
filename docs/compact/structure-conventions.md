# Structure conventions

Defines the repository layout, what counts as a "module", and how Python visibility maps to `pub` / `internal` for the `regen-map` skill.

Single language: **Python** (FastAPI + asyncio + Temporal Python SDK for the K8s automation services). Layout mirrors `~/work/nora` per `[D-001]` — that project is the live reference for the three-tier convention. Edit as conventions evolve.

## Top-level layout

```
<repo_root>/
├── core/                              # AI-generated; manual edits exceptional [D-001]
│   ├── __init__.py
│   ├── src/                           # Python source — one MODULE.md per package
│   │   ├── __init__.py
│   │   └── <module>/
│   │       ├── __init__.py
│   │       ├── MODULE.md
│   │       ├── <module>_cli.py        # CLI entrypoint (when applicable)
│   │       └── ...
│   └── tests/                         # pytest suite (test_<module>.py per package)
│       ├── __init__.py
│       └── test_<module>.py
├── customizations/                    # AI-scaffolded; humans complete / edit [D-001]
│   ├── __init__.py
│   └── <name>/
│       ├── __init__.py
│       ├── MODULE.md
│       ├── tests/                     # Co-located tests
│       │   └── __init__.py
│       └── ...
├── config/                            # Per-module install-time settings [D-001]
│   ├── <module>.json                  # JSON; one file per module needing config
│   └── README.md                      # What each file controls, who reads it
├── sharepoint/                        # SharePoint 2017 classic web parts (non-Python)
│   └── ...                            # Documented from src/sharepoint_integration/MODULE.md
├── docs/compact/                      # COMPACT state files
├── .claude/                           # COMPACT skills (when vendored)
├── CLAUDE.md  README.md  CONTRIBUTING.md
├── requirements.txt
└── (operational scripts and metadata)
```

Note: the SharePoint version is frozen at 2017 (vanilla List views + classic web parts). Pure SharePoint web-part code (XSLT / JavaScript / classic web part assemblies) lives outside `core/` under `sharepoint/`. The Python-side server adapter that talks to SharePoint lives at `core/src/sharepoint_integration/` and documents the `sharepoint/` surface from its own MODULE.md.

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

- `IssueTracker` `[D-003]` — intermediate primitives: `open_issue`, `close_issue`, `add_comment`, `upload_file`, etc. Implementations: `JiraAdapter` under `core/src/issue_tracker/jira_adapter.py` (public spec); proprietary internal adapters under `customizations/issue_tracker/` (generated by the API Spec Ingestor).
- `Messenger` `[D-003]` — intermediate primitives: `send_message`, `receive_message_as_rest`, etc. Implementations under `core/src/messenger/` for any public reference (TBD: Slack / Teams or none in v1) and `customizations/messenger/` for proprietary internal messengers (generated by the Ingestor).
- `SharePointBackend` / SharePoint config schema `[D-004]` — typed config (Pydantic) defined in `core/src/sharepoint_integration/`; deployment-specific values (site URLs, list internal names, lookup field IDs, document library paths, web-part wiring) supplied from `customizations/sharepoint_config/`.
- `CustomerAdapter` — per-customer external-system adapter (`submit_item`, `get_status`, `post_comment`, `upload_attachment`). Implementations under `core/src/customer_adapters/` (Jira, generic email, our file storage) and `customizations/customer_adapters/` for sensitive per-customer connectors.
- `LLMProvider` — abstracts the runtime LLM Gateway client; implementations under `core/src/llm/` for default providers (corp-proxied or on-prem) and `customizations/llm/` for any proprietary or per-deployment provider. **Distinct** from the on-prem code-generation LLM consumed by the API Spec Ingestor (`core/src/api_spec_ingestor/`); that one has its own configuration surface and is invoked off the runtime path.
- `CredentialBackend` — abstracts the secrets store (`Vault`, `K8s Sealed Secrets`, etc.); a stable interface lets the deployment swap backends without touching call sites.
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
- **UI-blocking is never an issue at the adapter layer.** All long-running adapter calls go through Temporal workflows; the UI awaits workflow completion via SharePoint List status updates, not direct adapter calls. Direct UI invocation is limited to fast operations (`get_issue`, `get_message`) or fast `--mock` mode for harness tests.

### Ingestor / Profiler pattern — proprietary inputs to on-prem code generation

HILDA has **three Ingestor / Profiler modules**, all following the same pattern: dev LLM cannot read the proprietary input; an on-prem open-source LLM (Gemma3:12b / Qwen / configurable per `[D-007]`) ingests it and produces concrete artifacts under `customizations/`. All three ship diagnostic CLIs emitting compact RPT / MET / QC reports per `[D-002]` so the dev can debug ingestion / generation without exposing the proprietary content.

**API Spec Ingestor `[D-003]`** — `core/src/api_spec_ingestor/`. Reads proprietary REST API specs for proprietary issue trackers and messengers; produces Python adapter modules conforming to the `IssueTracker` `[D-008]` / `Messenger` `[D-009]` Protocols at `customizations/<system>/<proprietary>_adapter.py`. v1 also processes the public Jira OpenAPI spec for `core/src/issue_tracker/jira_adapter.py` as the Ingestor's first end-to-end exercise.

**Template Schema Ingestor `[D-010]`** — `core/src/template_schema_ingestor/`. Reads proprietary customer-template Excel schema specs (column structure, field extensions, validation rules, enumerated values, customer-specific automation-rule overrides); produces, under `customizations/template_schemas/<customer>/`: (a) Pydantic validators conforming to the generic meta-schema in `core/src/template_schema/`; (b) Excel parsers / column mappers; (c) SharePoint-List column-mapping configs feeding `customizations/sharepoint_config/`; (d) customer-specific `AutomationRules` configurations consumed by the runtime rule engine.

**Test Report Document Profiler `[D-011]`** — `core/src/test_report_profiler/`. Reads historical proprietary test reports across mixed file types (Excel: `xlsx` / `xls` / `xlsm` / `csv`; Word: `doc` / `docx`; PDF) and emits per-customer parsers + classification artifacts into `customizations/test_report_parsers/<customer>/`. Generated runtime artifacts are deterministic Python parsers (no runtime LLM) — per-format parsers emit `(item_id, status, [waiver_ref], [comment])` tuples; the canonical `final | interim` classifier and status enum live in `core/src/test_report/`. Classification rule: a report is **`final`** iff every item is in `{passed, non-applicable, waived}` AND every `failed` item carries a `waiver_ref` (reclassifying it as `waived`); otherwise the report is **`interim`**.

**Hard invariant**: the dev LLM never reads proprietary API specs `[D-003]`, proprietary customer-template schemas `[D-010]`, or proprietary historical test reports `[D-011]`; never sees their content via tool calls; and does not request, summarize, or paraphrase their structure. It interacts only with: the public meta-schemas and intermediate Protocols defined in `core/`; the Ingestors' / Profiler's compact diagnostic output; and the generated artifacts already committed under `customizations/`. Captured as a phase-prompt invariant in `phases/development.md`.

## Cross-tier dependency rules

**Reference-imported from nora (subject to ratification via a dedicated DECISIONS entry during architecture).** `core/` and `customizations/` may import each other freely. Real dep flow is bidirectional: core defines Protocols that customizations implement; customizations expose data (e.g., per-customer templates, AutomationRules) that core's rule engine and adapters consume. Commits don't mark AI vs. human authorship — directory implies it. Manual edits to core are exceptional, not forbidden.

`drift-check` and `regen-map` accept cross-tier edges in either direction. There is no CI rule for "AI-only commits to core" — code review governs.

## Config format

**Reference-imported from nora (subject to ratification via a dedicated DECISIONS entry during architecture).** One JSON file per module under `config/<module>.json` for install-time deploy settings (default endpoints, model names, timeouts, host/port bindings). Modules read only their own config file. New module config = new file under `config/`.

**Runtime data is not config.** AutomationRules, customer templates, AI checklists, and PM credentials are runtime data managed by PM team leads / PMs / Vault — they live in SharePoint (or Vault for credentials), not in `config/`. Repo-side YAML seed/fixture forms for these (when present, e.g., for tests or initial provisioning) live alongside the relevant module under `core/src/<module>/fixtures/` or `customizations/<name>/fixtures/`, not under `config/`.

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

The dev LLM cannot reach production. Every cross-boundary artifact and every service failure must be expressible in a compact, paste-friendly form so PMs / devs can drop it into chat verbatim. Reference implementation: `~/work/nora/core/src/pipeline/error_codes.py` and `~/work/nora/core/src/pipeline/report.py`.

**Error codes**:
- Format: `{MODULE}-{SEVERITY}{NUMBER}` — e.g., `EML-E001`, `WFL-W003`, `CRD-E012`.
- `MODULE` is a 3-letter prefix per service / module (assigned during architecture as each module is drafted; one prefix per module, registered once in the central registry).
- `SEVERITY`: `E` (error) or `W` (warning).
- `NUMBER`: 3-digit, monotonic within a (module, severity) pair.
- All codes registered in a single `error_codes.py` (exact path TBD during architecture; one registry per repo) using an `ErrorDef`-style dataclass with `code` / `message` / `hint` and a `PipelineError`-style exception with `code` / `context` / `cause`. Mirror nora's structure.

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
