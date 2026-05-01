# Project: HILDA / DeliverableHub

*Identity: who / why / scope boundaries. Behavioral specs (FR / NFR) live in `requirements.md`.*

**One-line**: TODO — populate during requirements phase from `docs/compact/design-inputs/HILDA_Design.md` (executive summary).

**Problem**: TODO — populate during requirements phase. Starting material: §1 Executive Summary and §2 Current vs. Automated Workflow in `HILDA_Design.md`.

**Users**: TODO — populate during requirements phase. Primary: Project Managers managing connected-device certification programs across multiple customers. Secondary: PM team leads, R&D delivery owners, customers (external).

**In scope for v1**:
- TODO — populate during requirements phase from `HILDA_Design.md` §13 Implementation Roadmap (Phase 1 — Foundation, Months 1-3).
- *Pre-resolved (per `[D-003]`)*: issue-tracking v1 = **Jira adapter** wired through the `IssueTracker` intermediate-primitive Protocol. Adapter lives at `core/src/issue_tracker/jira_adapter.py`. Optionally generated from the public Jira OpenAPI spec by the API Spec Ingestor as the Ingestor's first end-to-end exercise.

**Out of scope (explicit non-goals)**:
- TODO — populate during requirements phase. Candidates from `HILDA_Design.md`: browser-automation customer adapters (Phase 4); LLM feedback-loop learning from PM edits (Phase 4); self-service customer-template wizard (Phase 4).

**Success criteria**: TODO — populate during requirements phase. Starting material: `HILDA_Design.md` §15 Success Metrics (time-to-submission reduction, reminder-to-response time, report rework rate, PM hours per device, template reuse rate, customer follow-up turnaround, onboarding time).

**Constraints** *(maintained alongside Open questions; some may become NFRs)*:
- All services run on-premises; no public-cloud LLM unless via corp proxy or on-prem model. Test reports, tech reports, waivers, customer credentials are corporate IP.
- SharePoint version is **frozen at 2017** — vanilla SharePoint List views + classic web parts only; no SPFx modern, no Power Apps.
- Every external action must be attributable to a specific PM. Per-PM credentials (no service accounts) for both internal and customer systems.
- No customer-facing outbound action runs without explicit PM approval (human-in-the-loop checkpoint is mandatory).
- **Three-tier LLM access model** — three distinct LLMs with non-overlapping access:
  1. **Dev LLM (Claude or similar)** — assists with code, design, tests. **NO access** to production SharePoint, real customer artifacts, PM credentials, or proprietary API specs. Test data is synthetic. Joint debugging via compact RPT / MET / FIX / QC reports per `[D-002]`.
  2. **On-prem code-generation LLM (Gemma3:12b / Qwen / similar — open source, configurable)** — used by three Ingestor / Profiler modules that all run on company premises and emit into `customizations/`: (i) **API Spec Ingestor** `[D-003]` — proprietary issue-tracker / messenger API specs → adapter code; (ii) **Template Schema Ingestor** `[D-010]` — proprietary customer-template Excel schemas → Pydantic validators, Excel parsers, SharePoint-List column mappings, customer-specific AutomationRules; (iii) **Test Report Document Profiler** `[D-011]` — proprietary historical test reports (Excel / Word / PDF) → per-customer test report parsers and `final | interim` classification artifacts. Each ships its own diagnostic CLI emitting compact reports the dev can paste into Claude-chat to debug ingestion / generation without exposing the proprietary content.
  3. **Runtime LLM (DeliverableHub's internal LLM doing classification, quality review, response drafting)** — runs in the K8s cluster with structured access to SharePoint data, attachments, and `CommunicationLog`. Operates at runtime, not at build time. Hosting choice (corp proxy vs. on-prem model) is Open question #2.
- The dev LLM **never** reads proprietary API specs (`[D-003]`), proprietary customer-template schemas (`[D-010]`), or proprietary historical test reports (`[D-011]`). Phase-prompt enforced.
- **Every module is independently testable** through an appropriate interface: functional modules ship `<module>_cli.py`; UI / web-facing modules ship a mock web harness. Side-effect-bearing modules implement `--mock` / `--dry-run`. All test interfaces support `--diagnostic` and emit compact reports per `[D-002]`. See `[D-005]`.
- **Three-tier code organization** — `core/` (AI-generated source) + `customizations/` (AI-scaffolded, human-completed) + `config/` (per-module install-time settings). All MODULE.md paths follow `core/src/<module>/MODULE.md` or `customizations/<name>/MODULE.md`. CLI invoked as `python -m core.src.<module>.<module>_cli`. See `[D-001]` and `docs/compact/structure-conventions.md` for the full layout (mirrored from `~/work/nora`).
- **Chat-mediated collaboration is the only debugging surface** between dev LLM and production. Every service / module failure emits a stable prefixed error code (`{MODULE}-{E|W}{NNN}`) registered in a central registry. Every cross-boundary artifact ships a paired compact format — **RPT** (run / activity report), **MET** (metrics), **FIX** (PM corrections), **QC** (quality check, fixed-field). **Hard invariant: no proprietary content** in compact reports, error messages, or logs that leave the on-prem environment — no customer test report fragments, tech report content, waiver text, customer feedback, R&D reply prose, or PM credential material. See `[D-002]` and `docs/compact/structure-conventions.md` (Chat-mediated collaboration conventions).

**Open questions** *(maintained during Requirements phase; removed when resolved, deferred, or backlogged)*:
- ~~**SharePoint API surface**~~ — *resolved 2026-04-30 by `[D-006]`*
- ~~**Runtime LLM hosting**~~ — *resolved 2026-04-30 by `[D-007]`*
- ~~**Intermediate-primitive Protocol design**~~ — *resolved 2026-04-30 by `[D-008]` + `[D-009]`*
- ~~**Customer template authoring**~~ — *partially resolved 2026-04-30 by `[D-010]`: Excel template structure is per-customer schema, generated by the Template Schema Ingestor; PMs upload Excel templates conforming to that schema. The "normalize-vs-parallel" sub-question is deferred to architecture phase.*
- *All other Open questions backlogged 2026-04-30 to `STATUS.md` Flags.*

**Contributors**:

| Stakeholder / Role | Contributes | Interface | Feedback loop |
|---|---|---|---|
| Backend / platform devs (Python, K8s) — name TBD | Code, MODULE.md curated edits, DECISIONS entries, adapter implementations | Direct git file edit | PR review |
| PM team leads (domain experts) — name TBD | Customer-template authoring, AutomationRules tuning, AI checklist authoring | SharePoint UI + Microsoft Excel (template upload) | UI submit / Excel upload → SharePoint Lists |
| PMs / Technical Project Managers (TPMs) — primary end users — names TBD | Bug reports, UX feedback, eval data (correct AI classifications, edit AI drafts) | DeliverableHub SharePoint UI + issue tracker | Telemetry + ticket triage |
| R&D delivery owners — many, names TBD | Indirect — replies to automated requests via existing tools; reply patterns are eval data | Email / messenger / internal issue tracker (no DH UI) | Capture-and-curate via CommunicationLog |
| Platform / infra / security ops — name TBD | K8s deployment, Vault config, network policies, secrets review | Helm / YAML / Vault CLI | Infra PR review |
| QA — name TBD | End-to-end test scenarios, customer-adapter contract tests, eval datasets | Test files + CI; structured YAML for fixtures | CI signal |
| Customers (external) | Implicit — submission feedback, RFIs received via their systems | Their own systems (Jira / portal / email) | Customer adapter ingestion |

*Names are TBD pending team staffing. Per-module ownership lands in `MODULE.md` files during the architecture phase.*
