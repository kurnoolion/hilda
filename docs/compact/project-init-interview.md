# Project-init interview — HILDA / DeliverableHub

Captured 2026-04-30 by `/project-init` (greenfield init with design-input preflight). This file is the source of truth for `/project-init --re-init`.

## Design inputs

Imported during preflight into `docs/compact/design-inputs/`:

- `HILDA_Design.md` — full solution proposal (~930 lines): executive summary, current vs. to-be workflow, data model, customer templates, generalized workflow, two-pillar architecture (SharePoint + on-prem K8s), communication adapters, orchestration & automation engine, human-in-the-loop design, credential management, deployment architecture, configurability tiers, implementation roadmap, risks, success metrics.

The requirements and architecture phases consult these as starting proposals, not authoritative specs.

---

## Topic 1 — What we're building

**DeliverableHub** (codename: HILDA — Human-In-the-Loop Deliverable Automation) is a configuration-driven platform that automates the end-to-end deliverable lifecycle for Project Managers managing connected-device certification programs across multiple customers. It replaces today's manual workflow (Excel + email + messenger + multiple issue trackers) with template-driven trackers, rule-based automation, AI-assisted quality review, and per-customer submission adapters.

Two-pillar architecture:

- **SharePoint** — PM-facing dashboard, data store (SharePoint Lists as DB tables), document repository (test reports, tech reports, waivers, software binaries).
- **On-prem Kubernetes (25-node cluster)** — automation services: workflow engine, communication adapters, AI/LLM agents, credential vault, Email Service.

The system is configuration-driven: each customer's process is captured once as a reusable template (milestones → deliverables → delivery items, with static fields pre-populated). PMs instantiate device trackers from templates; automation handles routine tracking, follow-up, and submission.

---

## Topic 2 — How we're building

**Implementation language**: **Python** (FastAPI + asyncio + Temporal Python SDK) for all K8s automation services.

**SharePoint constraints**: SharePoint version is **frozen at 2017** (likely SharePoint Server 2016 or SP2019 patched at a 2017 level). UI layer uses **vanilla SharePoint List views + custom (classic) web parts** — no SPFx modern, no Power Apps. This is a hard constraint that affects the integration strategy (see Topic 4 / Open question on Microsoft Graph vs. SharePoint REST API).

**Module convention (Python)**:
- A **module** = a directory under `src/` containing `__init__.py`. The module's contract lives at `src/<module>/MODULE.md`.
- Sub-packages are not separate modules unless they have their own `MODULE.md`.

**Visibility mapping (Python)**:
- Top-level names not prefixed `_` → `pub` (part of the module's public surface)
- Names prefixed `_` → `internal`
- Names listed in `__all__` (when present) take precedence as the canonical pub set
- Names prefixed `__` (dunder, name-mangled) → `internal`

**Other infra (from design doc)**:
- Workflow engine: Temporal (StatefulSet, Temporal Python SDK)
- PostgreSQL (mirror of hot SharePoint tables for query perf; Temporal state)
- Redis (cache, queues, pub/sub)
- HashiCorp Vault (or K8s Sealed Secrets) for PM credentials — encrypted at rest, never in SharePoint
- LLM Gateway (corp-proxied or on-prem model)
- Browser automation: Playwright (preferred) for non-API customer portals
- Per-customer adapters (Jira REST, etc.) — pluggable, common interface
- Helm + CI/CD; on-prem K8s, no public cloud

---

## Topic 3 — Stakeholder map & contribution surfaces

Names are **TBD** — team not yet staffed at named-individual level. No additional stakeholders beyond the seven below.

| # | Stakeholder / Role | Tech comfort | Contribution type | Required interface | Feedback loop |
|---|---|---|---|---|---|
| 1 | Backend / platform devs (Python, K8s) | High | Code, MODULE.md curated edits, DECISIONS entries, adapter implementations | Direct git file edit | PR review |
| 2 | PM team leads (domain experts) | Medium | Customer-template authoring, AutomationRules tuning, AI checklist authoring | **SharePoint UI + Microsoft Excel** (template upload) | UI submit / Excel upload → SharePoint Lists |
| 3 | PMs (primary end users) | Medium | Bug reports, UX feedback, eval data (correct AI classifications, edit AI drafts) | DeliverableHub SharePoint UI + issue tracker | Telemetry + ticket triage |
| 4 | R&D owners (deliverable producers) | Mixed | Indirect — they reply to automated requests via their existing tools; reply patterns *are* the eval data | Email / messenger / internal issue tracker (no DH UI) | Capture-and-curate via CommunicationLog |
| 5 | Platform / infra / security ops | High | K8s deployment, Vault config, network policies, secrets review | Helm / YAML / Vault CLI | Infra PR review |
| 6 | QA | Medium | End-to-end test scenarios, customer-adapter contract tests, eval datasets | Test files + CI; structured YAML for fixtures | CI signal |
| 7 | Customers (external) | n/a | Implicit — submission feedback, RFIs received via their systems | Their own systems (Jira / portal / email) | Customer adapter ingestion |

**Cross-topic check resolved**: Row 2 (PM team leads) authoring templates via SharePoint UI + Excel is consistent with Topic 2's frozen-2017 SharePoint stack. Excel upload is handled by a server-side import endpoint on the K8s cluster that parses and writes to SharePoint Lists.

---

## Topic 4 — Domain constraints

- **Data sensitivity**: corporate IP (test reports, tech reports, waivers, customer credentials, customer feedback). All services on-prem; no public-cloud LLM unless via corp proxy or on-prem model.
- **Compliance / audit**: every external action attributable to a specific PM. Credentials are per-PM, not service accounts. Full audit trail in `CommunicationLog` SharePoint List.
- **Scale**: modest — multiple customers × dozens of devices × hundreds of delivery items per device. Not real-time; near-real-time is sufficient.
- **Reliability**: customer submission must be auditable and reversible (PM-approved). No silent automated submissions.
- **SharePoint version frozen at 2017** — non-negotiable infrastructure constraint.

---

## Topic 5 — LLM access model

**Confirmed: dev-time LLM has NO direct access to production data, runtime SharePoint, or real customer artifacts. Test data is synthetic.**

This makes HILDA a **limited-access project for the development LLM**. Phase prompts must bake in remote-collaboration patterns:

- Diagnostic CLIs that produce compact pasteable reports (stage pass/fail, timing, counts, error codes)
- Structural fingerprints for artifacts (counts, distributions, hash digests — no content)
- Fixed-field quality-check templates per artifact type (numbers + Y/N, no prose)
- Contribution file formats (YAML / line-oriented text) for human overrides the pipeline reads as additions/corrections

Note: at runtime, the DeliverableHub LLM (the one inside the system doing classification, review, drafting) DOES have structured access to SharePoint data, attachments, and CommunicationLog — that's a separate LLM with a separate access model.

---

## Topic 6 — Pain points

**Things the dev AI assistant should catch**:
- Cross-channel reference-tag inconsistency (DH-* tag drift across email / messenger / issue tracker)
- Missing PM-approval gate on any outbound customer action — **the human-in-the-loop checkpoint is mandatory; no exceptions**
- Hard-coded customer-specific logic that should live in customer templates or `AutomationRules`
- Plaintext credentials anywhere — code, logs, SharePoint columns, error messages
- LLM outputs reaching customers without human-in-the-loop review
- Silent automation stalls (e.g., expired credentials masked as "no activity")
- SharePoint List performance gotchas at scale (un-indexed columns, view threshold violations)
- Customer-adapter brittleness (portals change without notice; non-API customers via browser automation are flaky)
- LLM hallucination in tech-report review and customer-response drafting
- Multi-channel context fragmentation (email + messenger + issue tracker for the same item)
- Template rigidity when a new customer process doesn't fit the model

---

## Topic 7 — Artifact preferences

- **Docs**: markdown (`.md`) with tables, code fences, and ASCII / Mermaid diagrams. Match the style of `HILDA_Design.md`.
- **Requirements**: markdown with `FR-N` / `NFR-N` flat numbering (COMPACT default).
- **Configuration**: YAML for customer templates, AutomationRules, eval fixtures (diff-friendly in PR review). SharePoint stores the runtime form as JSON; YAML is the repo source.
- **Decisions**: ADR-style markdown (`D-XXX`), append-only.
- **Data fixtures / synthetic test data**: YAML or line-oriented text.

---

## Team experience with AI-assisted dev

**Advanced.** The team is experienced with AI-assisted development workflows. Phase prompts use the "experienced-team phrasing" from the EIP calibration table — terse, direct, "match effort to risk," "challenge weak premises directly," "surface hidden unknowns" — not the gentler newer-team phrasings.

---

## Open questions surfaced during interview

These will be carried forward into `PROJECT.md` Open questions:

1. **SharePoint API surface**: design doc names "Microsoft Graph API" but the on-prem SharePoint version is frozen at 2017. Graph against on-prem 2016/2019 is unusual / partially supported. Likely needs to switch to **SharePoint REST API + on-prem AD auth** instead. Resolve before starting adapter work.
2. **LLM hosting**: corp proxy to a public LLM, or on-prem model? Driven by data-sensitivity policy. Resolve in requirements / architecture.
3. **Eval-data channel for AI quality review**: how do PM corrections of AI assessments flow back into the system to improve checklists / prompts? No explicit pipeline in the design doc. Resolve before AI/LLM modules are designed.
4. **Customer template authoring path**: design doc lists three creation methods (template / Excel / manual). For PM team leads (Topic 3 row 2), is the canonical authoring path SharePoint UI, Excel upload, or both equally? (Answered: both.) Open sub-question — does the system normalize one to the other, or maintain two parallel ingestion paths?
5. **Browser-automation customer adapters** (Playwright): how to handle customer portal HTML changes — versioned adapters with change-detection alerts, or manual failure → ticket?
