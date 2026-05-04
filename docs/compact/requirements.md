# Requirements

Last updated: 2026-05-04. Behavioral specs only — project identity and scope live in `PROJECT.md`.

<!--
How to use this file:

- Each requirement has a stable ID. IDs are never reused and never renumbered.
  - New functional requirement → next `FR-N`.
  - New non-functional requirement → next `NFR-N`.
- One sentence per requirement. Active voice. Testable where possible.
- Removed requirements are struck through in place:
    ~~**FR-3** — <original text>~~ (removed YYYY-MM-DD: <reason>)
- Items agreed to postpone go under `## Deferred` — they are not drift.
- `drift-check` reads this file. Keep it current; it is the authority for what the
  system is supposed to do, which design and implementation are checked against.

Source provenance for the v1 set:
- Functional requirements distilled from `docs/compact/design-inputs/HILDA_Design.md`
  (§2 To-Be workflow, §3 Data model, §5 Workflow stages, §7 Communication adapters,
  §8 Orchestration & AI, §9 Human-in-the-Loop matrix, §10 Credential management).
- Non-functional requirements anchored by `[D-002]` (chat-mediated collaboration),
  `[D-003]` (adapter pattern + API Spec Ingestor), `[D-004]` (SharePoint config split),
  `[D-005]` (independent testability), `[D-006]` (SharePoint REST + on-prem AD),
  `[D-007]` (all LLM on-prem), `[D-010]` (Template Schema Ingestor).
-->

## Functional

### Tracker lifecycle & data model

- **FR-1** — System creates a device tracker from one of three inputs: a customer template, an Excel import conforming to the per-customer schema, or manual entry.
- **FR-2** — Tracker creation auto-populates the full Milestone → Deliverable → DeliveryItem hierarchy from the template, with all static fields pre-populated per `HILDA_Design.md` §3.3.
- **FR-3** — PM can add, remove, or reassign DeliveryItems after instantiation without re-creating the tracker.
- **FR-4** — Excel-imported data is validated against the per-customer schema before any SharePoint write.
- **FR-5** — Hierarchical data is enforced as Devices/Milestones/Deliverables/DeliveryItems with uniqueness on (device_id, milestone_name), (milestone_id, deliverable_name), and (deliverable_id, item_name).
- **FR-6** — Milestone and Deliverable status and `completion_pct` are computed from the states of their child DeliveryItems.
- **FR-7** — Item types, tracking modalities, customer delivery modalities, and `delivery_state` values are extensible via configuration without code change.

### Collection kickoff & ongoing tracking

- **FR-8** — PM triggers Start Collection on a tracker to begin automated owner outreach for all open DeliveryItems.
- **FR-9** — Initial owner outreach is sent via the DeliveryItem's `tracking_modality` (Email / Messenger / Internal IssueTracker) with a structured reference tag encoding device, PM, milestone, deliverable, and item; for email, multiple DeliveryItems owned by the same recipient are consolidated into one outbound message per round identified by a stable `BATCH-<id>`, with a per-item structured reply block and per-item `mailto:` quick-update tap-links in the body.
- **FR-10** — Rule engine sends scheduled reminders to owners when `delivery_state = "Open"` and `days_since_last_contact > N` (N is per-rule configurable).
- **FR-11** — Rule engine escalates to owner + PM when `expected_completion_date - today ≤ N` and item is not Closed.
- **FR-12** — Inbound email replies route to the correct DeliveryItems via three convergent paths, all keyed on the `BATCH-<id>`: (a) a structured reply block edited in place by the owner, regex-parsed from the body; (b) per-item `mailto:` tap-links that pre-compose tiny emails parsed from the subject (`[HILDA] BATCH-<id> ITEM-<n> <STATUS>`); (c) free-text replies that match neither path, recorded as comments on every item in the batch and surfaced as a Manual triage flag on the PM dashboard. Status applies are idempotent on `(BATCH-id, item-index, status)`; outbound is sent multipart/alternative and the structured block is ASCII-only. (LLM-based fallback inference is Deferred — see Deferred.)
- **FR-13** — Inbound attachments and HILDA-generated outbound artifacts are stored on the on-prem shared network drive at `\\share\hilda\<customer_slug>\<device_slug>\<milestone_slug>\<deliverable_slug>\<item_slug>\` (slug-encoded immutable path, with `inbound/`, `outbound/`, and `revisions/` subdirectories); the DeliveryItem record holds the link; the dashboard renders attachment links as HILDA-mediated download URLs (`https://hilda.corp/dl/<scoped_token>`), never as direct UNC paths.
- **FR-14** — PM can manually override DeliveryItem dates, owners, comments, and `delivery_state` from the dashboard, and can trigger ad-hoc reminders independent of the scheduled rule cadence.
- **FR-15** — `last_owner_contacted` and `last_updated` timestamps update on every DeliveryItem status change.

### PM review & resolution path (Stage 4)

- **FR-16** — On test-report upload, the system runs the per-customer test report parser (generated by the Test Report Document Profiler per `[D-011]`) to extract per-item `(item_id, status ∈ {passed, failed, non-applicable, waived, not-started}, [waiver_ref])` tuples, the canonical classifier emits `final | interim` per FR-46, and the PM is presented with the classification + per-item status grid for review and resolution-path determination on unresolved failures.
- **FR-17** — Revised report versions are stored under the DeliveryItem's `revisions/` subdirectory on the shared network drive per FR-13 and re-parsed on upload; the test report classifier (FR-46) re-runs against each new version.
- **FR-46** — A test report is classified `final` iff every item is in `{passed, non-applicable, waived}` AND every `failed` item carries a `waiver_ref` (which reclassifies it as `waived`); otherwise the report is `interim` (anchors `[D-011]`).
- **FR-47** — For every `failed` item without a `waiver_ref` in a test report, the system surfaces the item on the PM dashboard for resolution-path determination (fix-pre-launch / tech report / waiver), feeding FR-16's auto-create logic.
- **FR-48** — When the PM-determined resolution path is `waiver`, the system auto-creates a Waiver DeliveryItem with its own lifecycle; the test report classifier consumes only the existence of `waiver_ref` (boolean), not the waiver's outcome — the TPM (Technical Project Manager) is not the final authority on the waiver path, which is owned by the Waiver DeliveryItem's separate workflow.

### Submission (Stage 5)

- **FR-18** — System assembles the submission package from the relevant DeliveryItems' artifacts on the shared network drive (FR-13) per the customer's `customer_delivery_modality` once all DeliveryItems for a milestone reach the Ready-for-Submission state.
- **FR-19** — Customer adapters implement the surface `{submitItem, getStatus, postComment, uploadAttachment}` and authenticate as the PM using the PM's stored credentials (never a service account).
- **FR-20** — Submission is blocked and queued (with PM dashboard alert) when the PM's credential for the target customer system is missing or expired.

### Customer follow-up & closure (Stage 6)

- **FR-21** — System captures customer feedback from the customer's tracking system and email and surfaces it on the PM dashboard with source + timestamp.
- **FR-22** — DeliveryItem transitions to Closed only on customer approval AND explicit PM confirmation; the Deliverable transitions to Complete when all child DeliveryItems are Closed.

### Communication adapters — Email Service

- **FR-23** — Email Service owns a dedicated mailbox, polls inbound 24/7 (or accepts push notifications from the mail server), and emits outbound on behalf of PMs with the PM's name in the signature and a stable From address.
- **FR-24** — Outbound email subject lines embed the structured reference tag (device, PM, milestone, deliverable, item); the Email Service parses the same tag from inbound replies for routing.

### Communication adapters — IssueTracker (internal)

- **FR-25** — IssueTracker adapter implements the `IssueTracker` Protocol per `[D-008]`; v1 target = Jira (public Jira REST API) wired via `core/src/issue_tracker/jira_adapter.py`.
- **FR-26** — When a DeliveryItem's `tracking_modality = "Internal IssueTracker"`, the adapter creates / links the corresponding issue and syncs status, comments, and attachments via webhook + polling fallback.

### Communication adapters — Messenger

- **FR-50** — Messenger adapter implements the `Messenger` Protocol per `[D-009]`; v1 targets are **Slack** (adapter at `core/src/messenger/slack_adapter.py`, Slack Web API via `slack_sdk`) and the **proprietary internal messenger** (adapter at `customizations/messenger/<proprietary>_adapter.py`, generated by the API Spec Ingestor per `[D-003]` as its first end-to-end exercise in v1); both adapters must pass the same `Messenger` Protocol contract test suite (anchors `[D-016]`).

### Communication adapters — Customer systems (pluggable)

- **FR-27** — Customer adapters are registered via configuration (AutomationRules + per-customer config) including endpoint URL, field mappings, and outbound templates; adding a new customer requires no code change in `core/`.

### Rule engine

- **FR-28** — Rule engine executes IF/THEN AutomationRules with triggers on item creation, state change, deadline proximity, and attachment upload.
- **FR-29** — Rule actions include `SendReminder`, `Escalate`, `UpdateState`, `TriggerAIReview`, and `QueueSubmission`.
- **FR-30** — Rules are scopeable to Global, Customer, or Device level and are customer-agnostic in shape (referencing modality fields, not hard-coded channels).
- **FR-31** — PM can pause, customize, or manually trigger any rule-driven action on any tracker.

### Credentials

- **FR-32** — PM registers per-system credentials via the secure UI using OAuth2, API token, or basic auth; for OAuth2 the PM is redirected to the external system's consent page and never enters a password into HILDA.
- **FR-33** — Credentials are encrypted at rest (AES-256) in the secrets store (Vault or K8s Sealed Secrets); SharePoint stores only credential metadata (`credential_id`, `user_id`, `system_type`, `status`).
- **FR-34** — Credentials are decrypted in-memory only at the Credential Service request boundary; never cached, written to disk, or written to logs.
- **FR-35** — Credential Service is the only pod with access to the secrets store; service-to-service auth is via K8s service accounts and mTLS.
- **FR-36** — Credential Health Monitor proactively refreshes OAuth2 tokens within a configurable expiry window (default 24h) and validates non-OAuth credentials with lightweight test calls (e.g., `get current user`).
- **FR-37** — PM can revoke any credential at any time; revocation takes effect immediately for all subsequent automation actions.
- **FR-38** — System auto-associates the PM's credentials to DeliveryItems based on `customer_delivery_modality` at tracker creation, flags missing required credentials, and re-associates on PM reassignment.

### Templates & three-tier configuration

- **FR-39** — PM team leads author customer templates via one of two separately maintained paths — SharePoint UI (live editing via classic web-part forms) or Microsoft Excel upload (file conforming to the per-customer schema generated by the Template Schema Ingestor `[D-010]`); TPMs choose between the paths per workflow preference, and both produce identical internal data model representations (anchors `[D-014]`).
- **FR-40** — Customer templates define standard milestones, deliverables, and DeliveryItems with all static fields pre-populated and are versioned (`template_version`).
- **FR-41** — Configuration overrides apply at three runtime tiers — Global / Customer / Device — without code change or redeploy; onboarding a new customer or new device is a configuration change, not a deployment.

### Audit & runtime diagnostics

- **FR-42** — Every external action (email send, message post, issue create/update, customer-system call, credential retrieval / refresh / use) is recorded in `CommunicationLog` with attribution to the originating PM, target system, action type, and DeliveryItem reference; credential material is never logged.
- **FR-49** — Every functional module exposes a `--diagnostic` mode runnable in production (`python -m core.src.<module>.<module>_cli --diagnostic`) that emits a compact RPT report of the module's runtime state without restarting the service — usable by ops to inspect a live deployment and shareable in chat for joint diagnosis (anchors `[D-002]` `[D-005]`).
- ~~**FR-43** — Every functional module emits compact RPT / MET / FIX / QC reports per `[D-002]` containing only counts, status flags, and bounded enum tokens — no proprietary content (test report fragments, tech report prose, waiver text, customer feedback, R&D reply prose, customer-system payloads, or PM credentials).~~ (moved 2026-05-01: reclassified as NFR-17 — chat-mediated collaboration invariant.)
- ~~**FR-44** — Every service / module failure raises a registered error code from the central `error_codes.py` registry in the format `{MODULE}-{E|W}{NNN}` per `[D-002]`.~~ (moved 2026-05-01: reclassified as NFR-18 — chat-mediated collaboration invariant.)
- ~~**FR-45** — Every functional module ships `<module>_cli.py` with `--diagnostic` (emits compact reports) and, for side-effect-bearing modules, `--mock` / `--dry-run`; every UI / web-facing module ships a mock web harness exercising it without production SharePoint access per `[D-005]`.~~ (split 2026-05-01: runtime `--diagnostic` for ops + RPT emission → FR-49; dev/test `--mock` / `--dry-run` + mock web harness → NFR-19.)

## Non-functional

### Data sensitivity & boundary

- **NFR-1** — All HILDA services run on-premises; no public-cloud LLM and no SaaS LLM calls (anchors `[D-007]`).
- **NFR-2** — Compact reports, error messages, and logs that leave the on-prem environment contain no proprietary content (anchors `[D-002]`); negative tests verify the invariant for every artifact type.

### Credential & security

- **NFR-3** — Per-PM credential isolation — each PM's credentials are stored under their own path in the secrets store; no cross-PM credential access by the application or by ops.
- **NFR-4** — Credential material is encrypted at rest (AES-256) and in transit (TLS); service-to-service auth uses mTLS.

### PM approval & accountability

- **NFR-5** — No customer-facing outbound action — submission, post-to-customer-comment, customer email, customer adapter call — is executed without an explicit PM-approval signal that is recorded in `CommunicationLog`.
- **NFR-6** — Every external action is attributable to a specific PM (no service-account actions); `CommunicationLog` is append-only and complete.

### SharePoint constraint

- **NFR-7** — SharePoint deployment-specific values (site URLs, list internal names, lookup field IDs, library paths) live exclusively in `customizations/sharepoint_config/` and are loaded at startup; `core/` contains no hard-coded SharePoint instance values (anchors `[D-004]`).
- **NFR-8** — SharePoint integration uses the SharePoint REST API + on-prem AD auth (NTLM / Kerberos) against SharePoint 2017; integration scope is List CRUD + classic web parts only — binary artifacts (attachments, reports, submission packages) are stored on the shared network drive per FR-13, not in SharePoint Document Libraries (anchors `[D-006]`).

### Latency & reliability

- **NFR-9** — DeliveryItem state changes propagate from owner reply to PM dashboard in under 60 seconds end-to-end (webhook preferred; polling fallback ≤ 30 s).
- **NFR-10** — Email Service polls or receives 24/7; transient external failures use exponential backoff and never fail silently — every failure produces a registered error code per `[D-002]`.
- **NFR-11** — When a PM credential is expired or missing, the dependent automation step is queued (not lost) and the PM is alerted via the dashboard and an out-of-band channel.

### Adapter pattern & build-time data boundary

- **NFR-12** — The dev LLM has no access to proprietary REST API specs (`[D-003]`), proprietary customer-template Excel schemas (`[D-010]`), or proprietary historical test reports (`[D-011]`); all three classes of input are processed exclusively by their on-prem Ingestors / Profiler using an open-source LLM.
- **NFR-13** — Generated proprietary adapters, per-customer template artifacts, and per-customer test-report parsers live exclusively under `customizations/`; `core/` contains only the typed Protocols, the Ingestors / Profiler themselves, and public-vendor adapters (anchors `[D-001]` `[D-003]` `[D-010]` `[D-011]`).

### Extensibility

- **NFR-14** — The data model supports adding new item types, tracking modalities, customer delivery modalities, and delivery states via configuration without schema migrations (anchors §3.3 extensibility intent).

### High availability

- **NFR-15** — Workflow Engine, Temporal Workers, Email Service, and Credential Service are deployed with HA replicas (per `HILDA_Design.md` §11); single-pod failure does not interrupt automation.

### Shared network drive boundary

- **NFR-16** — Reads from the shared network drive (FR-13) go exclusively through the HILDA-mediated download endpoint, which authenticates the PM via on-prem AD and authorizes against the DeliveryItem's ACL; writes go exclusively as the dedicated `hilda-svc` AD service account from the HILDA host's SMB mount; direct UNC paths are not exposed to PMs and are not embedded in any HTML rendered by `core/`.

### Chat-mediated collaboration invariants

- **NFR-17** — Every functional module emits compact RPT / MET / FIX / QC reports per `[D-002]` containing only counts, status flags, and bounded enum tokens — no proprietary content (test report fragments, tech report prose, waiver text, customer feedback, R&D reply prose, customer-system payloads, or PM credentials); negative tests verify the invariant for every artifact type.
- **NFR-18** — Every service / module failure raises a registered error code from the central `error_codes.py` registry in the format `{MODULE}-{E|W}{NNN}` per `[D-002]`; codes are stable across deployments and serve as the keys for runtime alerts, runbooks, and chat-shared diagnostics.

### Test interface invariants

- **NFR-19** — Every functional module that performs side-effect operations ships `--mock` and `--dry-run` test modes routing to fixtures / null-sinks without external IO; every UI / web-facing module ships a mock web harness exercising it against mock SharePoint List data without production-environment access (anchors `[D-005]`).

## Deferred

<!--
Requirements explicitly postponed. Not drift. Drift-check surfaces these as notes.

Entry format:
- **<id>** — <requirement> (deferred: <why> — revisit: <trigger or date>)
-->

- **DEF-1** — LLM-based inbound message classification fallback when reference-tag parsing fails (`HILDA_Design.md` §7.1, §8.2b) (deferred: runtime LLM module is Phase 2; v1 routes by tag only with manual-triage surface — revisit: when `core/src/llm/` is designed).
- **DEF-2** — LLM tech-report and waiver quality review with PM-actionable feedback (§8.2a) (deferred: same as DEF-1 — revisit: same).
- **DEF-3** — LLM-drafted customer responses (RAG-grounded, PM-approval-gated) (§8.2c) (deferred: same — revisit: same).
- **DEF-4** — LLM natural-language status summarization (§8.2d) (deferred: same — revisit: same).
- **DEF-5** — Messenger adapter full feature set (§7.2) — complete thread management, file upload, webhook secret rotation (deferred: v1 ships Slack + proprietary adapters per `[D-016]` with core `send / receive / list_thread` surface only; full feature set is v2 — revisit: post-v1 adapter acceptance testing).
- **DEF-6** — Proprietary internal IssueTracker adapter generated by the API Spec Ingestor end-to-end (`[D-003]`) (deferred: v1 IssueTracker target is Jira; the Ingestor is exercised in v1 for the proprietary messenger adapter per `[D-016]` — proprietary IssueTracker adapter is v2 — revisit: when a proprietary internal IssueTracker is in scope).
- **DEF-7** — Customer Jira adapter (Jira-as-customer-system; distinct from internal Jira-as-IssueTracker per FR-25) (`HILDA_Design.md` §7.4) (deferred: v2 first customer adapter — revisit: Phase 2 scoping).
- **DEF-8** — Multi-customer scale-out (2nd / 3rd customer adapters, parallel deployments) (§13 Phase 3) (deferred: v3 — revisit: post-v1 retro).
- **DEF-9** — Advanced dashboard views (Kanban boards, cross-device matrix, charts) (§6.2, §13 Phase 3) (deferred: v3 — revisit: same).
- **DEF-10** — Browser-automation customer adapters (Playwright / Selenium) for customers without APIs (§7.4, §13 Phase 4) (deferred: v4 — revisit: when a customer without API support is in scope).
- **DEF-11** — Self-service customer-template wizard (§13 Phase 4) (deferred: v4 — revisit: post-v3).
- **DEF-12** — LLM feedback loop learning from PM corrections to AI drafts (§13 Phase 4) (deferred: v4 — revisit: linked to STATUS.md Flag "Eval-data channel").
- **DEF-13** — Advanced analytics (cycle time per item type, customer SLAs, R&D performance) (§13 Phase 4) (deferred: v4 — revisit: same).
