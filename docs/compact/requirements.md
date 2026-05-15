# Requirements

Last updated: 2026-05-14. Behavioral specs only — project identity and scope live in `PROJECT.md`.

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
  `[D-007]` (all LLM on-prem), `[D-010]` (Template Schema Ingestor),
  `[D-011]` (Test Report Profiler), `[D-012]` (BATCH-id email design),
  `[D-013]` (NW drive ACL), `[D-014]` (two-path template authoring),
  `[D-015]` (API Spec Ingestor input format), `[D-016]` (v1 messenger targets).
-->

## Functional

### Tracker lifecycle & data model

- **FR-1** `[Ph-1]` — System creates a device tracker from one of three inputs: (a) a customer template `[Ph-1]`, (b) an Excel import conforming to the per-customer schema `[Ph-2]` (deferred alongside DEF-15 — requires Template Schema Ingestor), or (c) manual entry `[Ph-2]`.
- **FR-2** `[Ph-1]` — Tracker creation auto-populates the full Milestone → DeliveryItem hierarchy from the template, with all static fields pre-populated per `HILDA_Design.md` §3.3; DeliveryItems are grouped within a milestone by `tg_name` (anchors `[D-028]`); `plm_id` is not set at tracker creation — it is assigned per (owner × milestone) at collection kickoff (FR-8).
- **FR-3** `[Ph-1]` — PM can add, remove, or reassign DeliveryItems after instantiation without re-creating the tracker.
- ~~**FR-4**~~ — *(deferred 2026-05-12 → DEF-15: Excel import schema validation — implementation phase TBD)*
- **FR-5** `[Ph-1]` — Hierarchical data is enforced as Devices/Milestones/DeliveryItems with uniqueness on (device_id, milestone_name), (milestone_id, item_name), and (milestone_id, item_no); no Deliverable intermediate level (anchors `[D-028]`); all DeliveryItems with the same (milestone_id, owner) share the same `plm_id` — the PLM issue is one per (owner × milestone), not per item (anchors `[D-035]`).
- **FR-6** `[Ph-1]` — Milestone status and `completion_pct` are computed from the states of their child DeliveryItems.
- **FR-7** `[Ph-1]` — Item types, tracking modalities, customer delivery modalities, and `delivery_state` values are extensible via configuration without code change; `tracking_modality` is a **multi-value field** (list) per DeliveryItem supporting the following v1 values — `Email` `[Ph-1]` (status + documents via email reply), `CorporateMessenger` `[Ph-2]` (status only; no attachments; not document-capable), `CorporatePLM` `[Ph-1]` (documents only; not status-capable; owner uploads to PLM directly; HILDA polls PLM, downloads, and writes the document file to `<doc_type_slug>/<doc_id_slug>/rev1/`; document arrival triggers state transition), `NetworkSharedDrive` `[Ph-1]` (documents only; not status-capable; owner drops in shared drive `inbound/` folder; HILDA polls; document arrival triggers state transition), `CustomerJIRA` `[Ph-1]` (status only; not document-capable; HILDA polls customer JIRA API for waiver/issue closure status; no outbound; no attachments); valid combinations require at least one status-capable modality (`[Ph-1]` `Email` or `CustomerJIRA`; `[Ph-2]` `CorporateMessenger` added) and at least one document-capable modality (`Email`, `CorporatePLM`, or `NetworkSharedDrive`) for items with artifact deliverables; `CorporateMessenger` escalation (FR-10) is automatic regardless of modality and is not a modality value itself.

### Collection kickoff & ongoing tracking

- **FR-8** `[Ph-1]` — PM or TPM triggers Start Collection from the SharePoint milestone view (FR-56) to begin automated owner outreach for all open DeliveryItems; on activation, HILDA creates one PLM issue per unique (owner × milestone) pair via the IssueTracker adapter and writes the resulting `plm_id` to all DeliveryItems for that (owner, milestone); PLM issue creation is idempotent — re-triggering Start Collection does not create duplicate issues for pairs that already have a `plm_id`.
- **FR-9** `[Ph-1]` — Initial owner outreach is sent via the DeliveryItem's `tracking_modality` (see FR-7 for all values); for `Email` modality, multiple DeliveryItems owned by the same recipient are consolidated into one outbound message per round identified by a stable `BATCH-<id>`, with a per-item structured reply block grouped by `tg_name` `[Ph-1]` and per-item `mailto:` quick-update tap-links `[Ph-2]` in the body; for `CorporateMessenger` modality, no initial outreach message is sent — CorporateMessenger is used exclusively as an escalation channel per FR-10 when email reminders yield no owner response; for `CorporatePLM` and `NetworkSharedDrive` modalities, outreach informs the owner of the expected delivery path (PLM issue reference or shared drive path) — outbound format TBD architecture phase; for `CustomerJIRA` modality, no HILDA-initiated outreach (HILDA polls the customer JIRA passively).
- **FR-10** `[Ph-1]` — Rule engine sends scheduled reminders to owners when `delivery_state = "Open"` and `days_since_last_contact > N` (N is per-rule configurable); if email reminders yield no owner response for a further configurable period (no `last_updated` change after reminder_count ≥ M), the rule engine additionally sends a cross-channel escalation via corp messenger `[Ph-1]` (outbound only — inbound messenger responses handled by FR-54); if the owner has multiple open items in the same milestone, the messenger escalation aggregates all open items into a single message to minimise transactions.
- **FR-11** `[Ph-1]` — Rule engine escalates to owner + PM when `expected_completion_date - today ≤ N` and item is not Closed.
- **FR-12** `[Ph-1]` — Inbound email replies route to the correct DeliveryItems via three convergent paths, all keyed on the `BATCH-<id>`: (a) `[Ph-1]` a structured reply block edited in place by the owner, regex-parsed from the body; (b) `[Ph-2]` per-item `mailto:` tap-links that pre-compose tiny emails parsed from the subject (`[HILDA] BATCH-<id> ITEM-<n> <STATUS>`); (c) `[Ph-1]` free-text replies that match neither path are processed in two stages — (1) rule-based parsing attempts to extract status and comment against item-name tokens and `BATCH-<id>` context; above threshold, auto-apply and notify PM; (2) if rule-based parsing fails, the runtime LLM infers item classification and status — above LLM confidence threshold, auto-apply; below threshold, record as comments on all batch items and surface as a Manual triage flag on the PM dashboard (DEF-1 promoted 2026-05-13); when path (c) fires on an email that also contains attachments, a single fused LLM call processes email body, first-page attachment excerpts, and the batch DeliveryItem list together, covering both message classification and attachment routing in one pass (see FR-52, anchors `[D-034]`). Status applies are idempotent on `(BATCH-id, item-index, status)`; outbound is sent multipart/alternative and the structured block is ASCII-only; inbound email attachments are routed to DeliveryItems per FR-52. The sender email address is captured in `CommunicationLog` for attribution on every inbound path; if the sender does not match the registered owner email for the BATCH-id, the update is applied and a `Sender mismatch` note is surfaced on the PM dashboard for PM review (not a hard rejection); stricter sender-enforcement rules are configurable via `AutomationRules`.
- **FR-13** `[Ph-1]` — Inbound owner deliverables and HILDA-generated internal artifacts are managed via the on-prem shared network drive following the fixed root path `\\share\hilda\<carrier_slug>\<device_slug>\<milestone_slug>\<item_slug>\` (slug-encoded immutable identifiers; "carrier" = customer / certification body in HILDA's domain); **corp PLM is the source of truth for all owner deliverables** — the shared drive is an ingest channel and local audit trail, not the authoritative store (anchors `[D-035]`); PLM issues are created one per (owner × milestone) at tracker creation per FR-2. Each item root path contains three areas: **(a) `inbound/`** — raw drop zone for the `NetworkSharedDrive` ingest channel exclusively; owners with filesystem write access drop documents here; HILDA monitors and processes per FR-55; **(b) `outbound/`** — HILDA-generated internal artifacts only (QC reports, diagnostics, submission-review outputs); these are never owner deliverables and are never written to PLM; **(c) `<doc_type_slug>/<doc_id_slug>/rev1/`** — classified audit storage written by HILDA after routing and PLM upload, for all three ingest sources (email, PLM poll, NSD `inbound/`); `doc_id_slug` is derived by slugifying the first-received filename for that (delivery_item_id, doc_type) pair. Ingest write flows by source: for **email** and **PLM poll**, HILDA classifies the document via FR-52, uploads to the owner's PLM issue via the IssueTracker adapter, records `(delivery_item_id, plm_id, doc_type, doc_id_slug, plm_attachment_id, upload_timestamp)` in the HILDA document index, and writes the document file to `<doc_type_slug>/<doc_id_slug>/rev1/`; for **NSD `inbound/`**, HILDA detects the drop per FR-55, classifies `doc_type`, uploads to the owner's PLM issue, writes the document file to `<doc_type_slug>/<doc_id_slug>/rev1/`, and records in the document index. All download links are HILDA-mediated (`https://hilda.corp/dl/<scoped_token>`) resolving to the PLM attachment — never as direct UNC paths (anchors NFR-16); the DeliveryItem's `actual_item_info` field holds the HILDA document enumeration URL per FR-57. `[Ph-2]` Subsequent revisions are written to `<doc_type_slug>/<doc_id_slug>/revN/` (N ≥ 2) per FR-17.
- **FR-55** `[Ph-1]` — For DeliveryItems whose `tracking_modality includes NetworkSharedDrive`, HILDA monitors the item's `inbound/` drop folder on the shared network drive by configurable-interval polling; when a new file is detected (written directly by an owner with filesystem write access), the item is identified from the folder path (no FR-52 item-routing needed), the file is recorded in `CommunicationLog` with the owner's filesystem identity, HILDA classifies the `doc_type` from filename and first-page text, uploads the document to the owner's PLM issue (PLM = source of truth per FR-13), writes the document file to `<doc_type_slug>/<doc_id_slug>/rev1/`, updates the HILDA document index with `(delivery_item_id, plm_id, doc_type, doc_id_slug, plm_attachment_id, upload_timestamp)`, updates the DeliveryItem record and `actual_item_info` in SharePoint, and triggers a `delivery_state` transition (configurable rule); detection latency is bounded by the configurable poll interval (default ≤ 5 minutes); HILDA does **not** poll drop folders for items where `tracking_modality` does not include `NetworkSharedDrive`. `[Ph-2]` HILDA additionally monitors the item's `<doc_type_slug>/<doc_id_slug>/revN/` folders by configurable-interval polling; detection of a new file written directly by an owner triggers the revision path per FR-17(b) — re-parse, classifier re-run, subsequent LLM review, and SharePoint update.
- **FR-52** `[Ph-1]` — When one or more documents arrive from any inbound source — email attachment (via FR-12) or corp PLM poll (via FR-26) — the system routes each document to the corresponding DeliveryItem via a two-tier process (anchors `[D-033]`): (1) extract filename and first-page text from each document and fuzzy-match against item name, description, and `item_type` within the (owner × milestone) batch — PLM preserves owner-assigned filenames which contribute additional match signal; email attachment filenames may be opaque lab IDs in which case first-page text is the primary signal; high-confidence fuzzy matches are applied directly; (2) for documents where fuzzy matching cannot resolve the mapping, the runtime LLM inspects the filename and first-page content together with the batch DeliveryItem list to determine the match; when the triggering email also fires FR-12 path (c), email attachment routing is subsumed into the single fused LLM call for that path and the two-tier process does not run separately for those attachments (anchors `[D-034]`); PLM-polled documents always use the standalone two-tier path (no fused call); `[Ph-2]` after item and document_type are resolved via the two-tier process, the system checks whether the (item, document_type) pair already has a document recorded in the HILDA document index — if so, the incoming document is treated as a revision and routed to `<doc_type_slug>/<doc_id_slug>/revN/` per FR-17 instead of `rev1/`; document_type is inferred from the filename and first-page classification already performed during routing; confirmed matches trigger: (1) upload to the owner's PLM issue via the IssueTracker adapter; (2) the document file written to `<doc_type_slug>/<doc_id_slug>/rev1/` under the item's shared drive path per FR-13; (3) the HILDA document index updated with `(delivery_item_id, plm_id, doc_type, doc_id_slug, plm_attachment_id, upload_timestamp)`; (4) the DeliveryItem record and `actual_item_info` updated in SharePoint; unresolved documents are surfaced on the PM dashboard for manual assignment.
- **FR-14** `[Ph-1]` — PM can manually override DeliveryItem dates, owners, comments, and `delivery_state` from the dashboard, and can trigger ad-hoc reminders independent of the scheduled rule cadence.
- **FR-15** `[Ph-1]` — `last_owner_contacted` and `last_updated` timestamps update on every DeliveryItem status change.

### PM review & resolution path (Stage 4)

- **FR-16** `[Ph-1]` — On test-report upload, the system runs the per-customer test report parser (generated by the Test Report Document Profiler per `[D-011]`) to extract per-item `(item_id, status ∈ {passed, failed, non-applicable, waived, not-started}, [waiver_ref])` tuples, the canonical classifier emits `final | interim` per FR-46, and the PM is presented with the classification + per-item status grid for review and resolution-path determination on unresolved failures.
- **FR-53** `[Ph-1]` — On receipt of a test report, tech report, or waiver document linked to a DeliveryItem (via FR-52 or manual upload), the runtime LLM performs an initial quality review against the per-customer checklist generated by the Test Report Profiler `[D-011]`, writes findings to the DeliveryItem record in SharePoint, and surfaces them on the PM dashboard for TPM review; Ph-1 scope is initial review, findings persistence to SharePoint, and dashboard display only — PM response tracking, owner revision communication, and multi-version re-review are deferred (DEF-2 remainder, FR-17).
- **FR-17** `[Ph-2]` — When a revised document arrives for a DeliveryItem that already has a document of the same type recorded in the HILDA document index, the system stores the revision at `<doc_type_slug>/<doc_id_slug>/revN/` per FR-13 (N = next revision number), updates the DeliveryItem record with the new attachment link, re-runs the test report parser and classifier (FR-46) against the new version, performs a subsequent LLM quality review against the same per-customer checklist as FR-53, and writes updated findings to the DeliveryItem record in SharePoint. Revisions are detected and handled per source: `[Ph-2]` **(a) Email (10.1 / 10.1.1)** — a revised document arriving via email is first routed to the DeliveryItem via FR-52's two-tier process; after (item, document_type) are resolved, the system checks whether a prior document of that type already exists in the HILDA document index — if so, the file is written to `<doc_type_slug>/<doc_id_slug>/revN/` regardless of whether the filename matches the original; PLM auto-upload applies to `revN/` writes under the same modality conditional as FR-13. `[Ph-2]` **(b) Shared network drive (10.2)** — owners may write revised documents directly to the item's `<doc_type_slug>/<doc_id_slug>/revN/` folder following the agreed path convention; HILDA monitors these folders by configurable-interval polling per FR-55; detection triggers re-parse, classifier re-run, subsequent LLM review, and SharePoint update; PLM auto-upload applies under the same modality conditional as FR-13; activation of this sub-path is gated on ops confirming owners are trained on the folder convention. `[Ph-2]` **(c) PLM (10.3 / 10.3.1)** — HILDA tracks each PLM-issue-attached document as a (file_name, timestamp) record per PLM issue at every poll interval per FR-26; a new timestamp for an existing file_name entry is treated as a revision — HILDA copies the revised file to `<doc_type_slug>/<doc_id_slug>/revN/` on the shared drive, updates the DeliveryItem record, re-runs the classifier, and triggers subsequent LLM review with SharePoint update; a new file_name triggers initial document routing via FR-52 instead.
- **FR-46** `[Ph-1]` — A test report is classified `final` iff every item is in `{passed, non-applicable, waived}` AND every `failed` item carries a `waiver_ref` (which reclassifies it as `waived`); otherwise the report is `interim` (anchors `[D-011]`).
- **FR-47** `[Ph-2]` — For every `failed` item without a `waiver_ref` in a test report, the system surfaces the item on the PM dashboard for resolution-path determination (fix-pre-launch / tech report / waiver), feeding FR-16's auto-create logic.
- **FR-48** `[Ph-2]` — When the PM-determined resolution path is `waiver`, the system auto-creates a Waiver DeliveryItem with its own lifecycle; the test report classifier consumes only the existence of `waiver_ref` (boolean), not the waiver's outcome — the TPM (Technical Project Manager) is not the final authority on the waiver path, which is owned by the Waiver DeliveryItem's separate workflow.

### SharePoint UI

- **FR-56** `[Ph-1]` — The SharePoint UI presents a per-milestone view (classic web part) listing all DeliveryItems for that milestone, grouped by `tg_name`; each row displays `item_no`, item name, owner, `delivery_state`, and `expected_completion_date`; items where `item_type = Confirmation` show no document section; all other item types show a document section populated via `actual_item_info` (FR-57); the view includes a **Start Collection** action (available to PM and TPM; enabled when the milestone has not yet been kicked off) that triggers FR-8 — PLM issue creation per (owner × milestone) and initial owner outreach per FR-9; this view is the primary TPM review surface.

- **FR-57** `[Ph-1]` — `actual_item_info` on `DeliveryItem` stores the HILDA document enumeration URL `https://hilda.corp/docs/<item_id>`; this URL is populated on first document write to the item's shared drive path and remains stable thereafter; the endpoint queries the HILDA document index (populated at ingest time per FR-52, FR-55, and FR-26) and returns `[{doc_type, doc_id_slug, filename, download_url}]` for the latest available version of each document; `download_url` is a HILDA-mediated link per FR-61 that resolves to the PLM attachment for owner deliverables; `[Ph-2]` when multiple revisions exist, only the latest revision of each document is returned (determined by `upload_timestamp` in the document index).

- **FR-58** `[Ph-1]` — `item_type` is the discriminator for document-carrying behavior: `Confirmation` items (owner reply closes the item; no artifact deliverable) have no document section, no document routing, and no review pipeline; all other item types expose document receipt, routing (FR-52 / FR-55), initial review (FR-53), and document display (FR-59 / FR-60 / FR-61).

- **FR-59** `[Ph-1]` — The SharePoint UI document section for each applicable DeliveryItem calls `actual_item_info` and renders one row per document grouped by `doc_type`; each row shows `doc_type`, `doc_id_slug` (human-readable filename-derived label), `filename`, and a download link; in Ph-1 each document has exactly one version (`rev1/` for email / PLM poll; single file in `inbound/` for NSD); `[Ph-2]` when multiple revisions exist, only the latest revision of each document is shown in the list view.

- **FR-60** `[Ph-1]` — The SharePoint UI displays review results alongside each document row in the document section: (a) rule-based parser output from FR-16 — per-item status grid and `final | interim` classification per FR-46 — for test reports; (b) LLM quality review findings from FR-53 for test reports, tech reports, and waivers; both result sets are written to the DeliveryItem record in SharePoint by their respective processing steps and rendered per document row.

- **FR-61** `[Ph-1]` — Each document row in FR-59 provides a HILDA-mediated download link (`https://hilda.corp/dl/<scoped_token>`) authenticated via on-prem AD and authorized against the DeliveryItem ACL per NFR-16; in Ph-1 the link resolves to the single available version (`rev1/` or `inbound/` file); `[Ph-2]` download resolves to the latest revision of that document (determined by `upload_timestamp` in the document index); download is available only for items where `item_type ≠ Confirmation`.

- **FR-62** `[Ph-2]` — The SharePoint UI provides a document upload surface for each applicable DeliveryItem (`item_type ≠ Confirmation`); item and `doc_type` are specified explicitly by the PM or TPM in the UI (no FR-52 routing needed); HILDA uploads the document to the owner's PLM issue, writes the document file to `<doc_type_slug>/<doc_id_slug>/rev1/` per FR-13, updates the HILDA document index with `(delivery_item_id, plm_id, doc_type, doc_id_slug, plm_attachment_id, upload_timestamp)`, updates the DeliveryItem record and `actual_item_info` in SharePoint, and triggers initial review per FR-53.

### Submission (Stage 5)

- **FR-18** `[Ph-2]` — System assembles the submission package from the relevant DeliveryItems' artifacts on the shared network drive (FR-13) per the customer's `customer_delivery_modality` once all DeliveryItems for a milestone reach the Ready-for-Submission state.
- **FR-19** `[Ph-2]` — Customer adapters implement the surface `{submitItem, getStatus, postComment, uploadAttachment}` and authenticate as the PM using the PM's stored credentials (never a service account).
- **FR-20** `[Ph-2]` — Submission is blocked and queued (with PM dashboard alert) when the PM's credential for the target customer system is missing or expired.

### Customer follow-up & closure (Stage 6)

- **FR-21** `[Ph-2]` — System captures customer feedback from the customer's tracking system and email and surfaces it on the PM dashboard with source + timestamp.
- **FR-22** `[Ph-2]` — DeliveryItem transitions to Closed only on customer approval AND explicit PM confirmation; Milestone transitions to Complete when all child DeliveryItems are Closed.

### Communication adapters — Email Service

- **FR-23** `[Ph-1]` — Email Service owns a dedicated mailbox, polls inbound 24/7 (or accepts push notifications from the mail server), and emits outbound on behalf of PMs with the PM's name in the signature and a stable From address.
- **FR-24** `[Ph-1]` — Outbound email subject lines embed the structured reference tag (device, PM, milestone, deliverable, item); the Email Service parses the same tag from inbound replies for routing and captures the sender email address alongside the tag for attribution and anomaly detection per FR-12.

### Communication adapters — IssueTracker (internal)

- **FR-25** `[Ph-1]` — The `IssueTracker` Protocol per `[D-008]` serves two distinct roles in HILDA: (a) **corp PLM adapter** — document storage and source of truth for owner deliverables, one issue per (owner × milestone), accessed via the proprietary corp PLM adapter generated by the API Spec Ingestor `[D-003]`; HILDA polls this issue when `tracking_modality includes CorporatePLM` (FR-13, FR-26); (b) **customer JIRA adapter** — HILDA polls the customer's JIRA instance for waiver and issue closure status when `tracking_modality includes CustomerJIRA`, wired via `core/src/issue_tracker/jira_adapter.py` (public Jira REST API); **there is no internal-Jira tracking in v1** — JIRA is used exclusively for customer-facing closure/status polling; both roles use the identical Protocol surface, distinguished only by adapter and configuration.
- **FR-26** `[Ph-1]` — Corp PLM issues are created once per (owner × milestone) at tracker creation (FR-2), not per DeliveryItem; the PLM issue is **document-only** — corp PLM does **not** sync status back to HILDA; when `tracking_modality includes CorporatePLM`, HILDA polls the PLM issue at a configurable interval for new owner-uploaded documents, routes each to the corresponding DeliveryItem via FR-52, downloads from PLM, writes the document file to `<doc_type_slug>/<doc_id_slug>/rev1/` under the item's shared drive path per FR-13, and updates the HILDA document index; `[Ph-2]` HILDA maintains a per-PLM-issue document index of (file_name, timestamp) pairs persisted across poll cycles; a new timestamp observed for an existing file_name triggers the revision path per FR-17(c) rather than initial FR-52 routing; a new file_name triggers initial FR-52 routing as normal; when `tracking_modality does not include CorporatePLM`, HILDA receives documents via email (FR-12 / FR-52) or NSD `inbound/` (FR-55) and uploads each to the owner's PLM issue per FR-13 — PLM is the source of truth in all cases (anchors `[D-035]`); in all cases corp PLM is the document source of truth (anchors `[D-035]`); there is no internal-Jira tracking — the Jira adapter is used only for customer-facing `CustomerJIRA` modality per FR-25. `[Ph-2]` The PLM auto-upload conditional extends to documents written to `<doc_type_slug>/<doc_id_slug>/revN/` under the same modality rules — when `tracking_modality does not include CorporatePLM`, HILDA auto-pushes revised documents to the PLM issue alongside initial documents (per FR-17).

### Communication adapters — Messenger

- **FR-50** `[Ph-1]` — Messenger adapter implements the `Messenger` Protocol per `[D-009]`; v1 targets are **Slack** (adapter at `core/src/messenger/slack_adapter.py`, Slack Web API via `slack_sdk`) and the **proprietary internal messenger** (adapter at `customizations/messenger/<proprietary>_adapter.py`, generated by the API Spec Ingestor per `[D-003]` as its first end-to-end exercise in v1); both adapters must pass the same `Messenger` Protocol contract test suite (anchors `[D-016]`); corp messenger carries **status only** — attachment receipt via messenger is not supported.
- **FR-54** `[Ph-2]` — Inbound corp messenger messages from owners are captured on receipt, attributed by sender handle to the owner, recorded as comments on all open DeliveryItems in the owner's active batch, and surfaced as a `Messenger reply — Manual triage` flag on the PM dashboard; if the owner has multiple open items across milestones, flags are grouped by milestone to minimise PM triage actions; the runtime LLM classifies the message content to extract status (closed / open / delayed / blocked) and reason — above-threshold classifications are auto-applied; below-threshold go to PM triage.

### Communication adapters — Customer systems (pluggable)

- **FR-27** `[Ph-2]` — Customer adapters are registered via configuration (AutomationRules + per-customer config) including endpoint URL, field mappings, and outbound templates; adding a new customer requires no code change in `core/`.

### Rule engine

- **FR-28** `[Ph-1]` — Rule engine executes IF/THEN AutomationRules with triggers on item creation, state change, deadline proximity, and attachment upload.
- **FR-29** `[Ph-1]` — Rule actions include `SendReminder`, `Escalate`, `UpdateState`, `TriggerAIReview`, and `QueueSubmission`.
- **FR-30** `[Ph-1]` — Rules are scopeable to Global, Customer, or Device level and are customer-agnostic in shape (referencing modality fields, not hard-coded channels).
- **FR-31** `[Ph-1]` — PM can pause, customize, or manually trigger any rule-driven action on any tracker.

### Credentials

- **FR-51** `[Ph-1]` — v1 credential_service reads PM credentials from sops-encrypted `.env` files provisioned by ops at deploy time per `[D-038]` (one file per service, one env var per PM per system type); exposes `get_credential(pm_id, system_type) -> Credential` to all callers; credentials are decrypted at startup into process memory and never logged, written to disk, or written to any SharePoint List; no PM registration UI, no Vault integration, no OAuth2 refresh in v1 (anchors `[D-019]`; v1 deployment mechanism superseded by `[D-026]` and `[D-038]`).
- ~~**FR-32**~~ — *(deferred 2026-05-04 → DEF-14: PM self-service credential registration UI — v2)*
- ~~**FR-33**~~ — *(deferred 2026-05-04 → DEF-14: Vault/AES-256 secrets store — v2)*
- ~~**FR-34**~~ — *(deferred 2026-05-04 → DEF-14: per-request in-memory decryption boundary — v2)*
- ~~**FR-35**~~ — *(deferred 2026-05-04 → DEF-14: secrets store pod isolation + mTLS — v2)*
- ~~**FR-36**~~ — *(deferred 2026-05-04 → DEF-14: OAuth2 health monitor + token refresh — v2)*
- ~~**FR-37**~~ — *(deferred 2026-05-04 → DEF-14: PM credential revocation UI — v2)*
- ~~**FR-38**~~ — *(deferred 2026-05-04 → DEF-14: auto-association + re-association on PM reassignment — v2)*

### Templates & three-tier configuration

- **FR-39** `[Ph-1]` — PM team leads author customer templates via one of two separately maintained paths — (a) SharePoint UI: live editing via classic web-part forms `[Ph-1]`; (b) Microsoft Excel upload: file conforming to the per-customer schema generated by the Template Schema Ingestor `[D-010]` `[Ph-2]` (deferred — Template Schema Ingestor is Ph-2, consistent with DEF-15 and FR-1 path b); TPMs choose between the paths per workflow preference, and both produce identical internal data model representations (anchors `[D-014]`).
- **FR-40** `[Ph-1]` — Customer templates define standard milestones and DeliveryItems (grouped by `tg_name`) with all static fields pre-populated and are versioned (`template_version`); no Deliverable level (anchors `[D-028]`).
- **FR-41** `[Ph-1]` — Configuration overrides apply at three runtime tiers — Global / Customer / Device — without code change or redeploy; onboarding a new customer or new device is a configuration change, not a deployment.

### Audit & runtime diagnostics

- **FR-42** `[Ph-1]` — Every external action (email send, message post, issue create/update, customer-system call, credential retrieval / refresh / use) is recorded in `CommunicationLog` with attribution to the originating PM, target system, action type, and DeliveryItem reference; credential material is never logged.
- **FR-49** `[Ph-1]` — Every functional module exposes a `--diagnostic` mode runnable in production (`python -m core.src.<module>.<module>_cli --diagnostic`) that emits a compact RPT report of the module's runtime state without restarting the service — usable by ops to inspect a live deployment and shareable in chat for joint diagnosis (anchors `[D-002]` `[D-005]`).
- ~~**FR-43** — Every functional module emits compact RPT / MET / FIX / QC reports per `[D-002]` containing only counts, status flags, and bounded enum tokens — no proprietary content (test report fragments, tech report prose, waiver text, customer feedback, R&D reply prose, customer-system payloads, or PM credentials).~~ (moved 2026-05-01: reclassified as NFR-17 — chat-mediated collaboration invariant.)
- ~~**FR-44** — Every service / module failure raises a registered error code from the central `error_codes.py` registry in the format `{MODULE}-{E|W}{NNN}` per `[D-002]`.~~ (moved 2026-05-01: reclassified as NFR-18 — chat-mediated collaboration invariant.)
- ~~**FR-45** — Every functional module ships `<module>_cli.py` with `--diagnostic` (emits compact reports) and, for side-effect-bearing modules, `--mock` / `--dry-run`; every UI / web-facing module ships a mock web harness exercising it without production SharePoint access per `[D-005]`.~~ (split 2026-05-01: runtime `--diagnostic` for ops + RPT emission → FR-49; dev/test `--mock` / `--dry-run` + mock web harness → NFR-19.)

## Non-functional

### Data sensitivity & boundary

- **NFR-1** — All HILDA services run on-premises; no public-cloud LLM and no SaaS LLM calls (anchors `[D-007]`).
- **NFR-2** — Compact reports, error messages, and logs that leave the on-prem environment contain no proprietary content (anchors `[D-002]`); negative tests verify the invariant for every artifact type.

### Credential & security

- **NFR-3** — Per-PM credential isolation — each PM's credentials are stored under their own path in the secrets store; no cross-PM credential access by the application or by ops.
- **NFR-4** — Credential material is encrypted at rest via `sops` (AES-256-GCM, `age`-based key per `[D-038]`) — `.env` files on the host are sops-encrypted; the credential service decrypts at startup into process memory; the age private key at `/etc/hilda/age.key` (`chmod 400`, HILDA service user only) is the only plaintext secret on the filesystem; in transit, all external communications (SharePoint, PLM, customer JIRA, email server) use TLS; service-to-service mTLS is a v2 K8s target per `[D-021]` and `[D-026]`.

### PM approval & accountability

- **NFR-5** — No customer-facing outbound action — submission, post-to-customer-comment, customer email, customer adapter call — is executed without an explicit PM-approval signal that is recorded in `CommunicationLog`.
- **NFR-6** — Every external action is attributable to a specific PM (no service-account actions); `CommunicationLog` is append-only and complete.

### SharePoint constraint

- **NFR-7** — SharePoint deployment-specific values (site URLs, list internal names, lookup field IDs, library paths) live exclusively in `customizations/sharepoint_config/` and are loaded at startup; `core/` contains no hard-coded SharePoint instance values (anchors `[D-004]`).
- **NFR-8** — SharePoint integration uses the SharePoint REST API + on-prem AD auth (NTLM / Kerberos) against SharePoint 2017; integration scope is List CRUD + classic web parts only — binary owner deliverables are stored in corp PLM per FR-13; HILDA-generated internal artifacts are stored on the shared network drive `outbound/` per FR-13; neither is stored in SharePoint Document Libraries (anchors `[D-006]`).

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

- **NFR-15** — hilda-worker (Celery worker), Email Service, and Credential Service are configured with `restart: unless-stopped` in Docker Compose v1 (per `[D-026]`); HA replicas are a v2 K8s deployment target per `[D-021]` and `[D-026]`; Temporal Workers are deferred to v2 per `[D-022]`; v1 accepts single-instance restart-recovery semantics — in-flight tasks may fail and retry on container restart; concurrent-replica HA is not provided.

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

- ~~**DEF-1**~~ — *(promoted 2026-05-13 → FR-12 path (c): rule-based parsing first, runtime LLM fallback when rule-based fails; when path (c) fires with attachments, the LLM call is fused with FR-52 attachment routing in one pass — anchors `[D-033]`, `[D-034]`)*
- **DEF-2** — LLM quality review — PM feedback workflow: tracking PM response to LLM findings, owner revision communication, and multi-version re-review (§8.2a; initial one-pass review for test reports, tech reports, and waivers promoted to FR-53 2026-05-12 — revisit: Ph-2 when revision tracking workflow is in scope).
- **DEF-3** — LLM-drafted customer responses (RAG-grounded, PM-approval-gated) (§8.2c) (deferred: same — revisit: same).
- **DEF-4** — LLM natural-language status summarization (§8.2d) (deferred: same — revisit: same).
- **DEF-5** — Messenger adapter full feature set (§7.2) — complete thread management, file upload, webhook secret rotation (deferred: v1 ships Slack + proprietary adapters per `[D-016]` with core `send / receive / list_thread` surface only; full feature set is v2 — revisit: post-v1 adapter acceptance testing).
- ~~**DEF-6**~~ — *(promoted 2026-05-14 → Ph-1: corp PLM IssueTracker adapter is generated by the API Spec Ingestor per `[D-003]` alongside the proprietary messenger adapter `[D-016]`; v1 IssueTracker target is corp PLM, not Jira; CustomerJIRA closure-polling adapter remains separately deferred → DEF-7)*
- **DEF-7** — Customer Jira adapter (Jira-as-customer-system; distinct from internal Jira-as-IssueTracker per FR-25) (`HILDA_Design.md` §7.4) (deferred: v2 first customer adapter — revisit: Phase 2 scoping).
- **DEF-8** — Multi-customer scale-out (2nd / 3rd customer adapters, parallel deployments) (§13 Phase 3) (deferred: v3 — revisit: post-v1 retro).
- **DEF-9** — Advanced dashboard views (Kanban boards, cross-device matrix, charts) (§6.2, §13 Phase 3) (deferred: v3 — revisit: same).
- **DEF-10** — Browser-automation customer adapters (Playwright / Selenium) for customers without APIs (§7.4, §13 Phase 4) (deferred: v4 — revisit: when a customer without API support is in scope).
- **DEF-11** — Self-service customer-template wizard (§13 Phase 4) (deferred: v4 — revisit: post-v3).
- **DEF-12** — LLM feedback loop learning from PM corrections to AI drafts (§13 Phase 4) (deferred: v4 — revisit: linked to STATUS.md Flag "Eval-data channel").
- **DEF-13** — Advanced analytics (cycle time per item type, customer SLAs, R&D performance) (§13 Phase 4) (deferred: v4 — revisit: same).
- **DEF-15** — Excel-imported data validated against per-customer schema before SharePoint write (FR-4 content; deferred 2026-05-12 — revisit: when Excel import is in scope for its implementation phase).
- **DEF-14** — Full PM self-service credential management: registration UI (OAuth2 redirect + API token + basic auth), Vault-backed AES-256 secrets store, per-request in-memory decryption boundary, Credential Service pod isolation + mTLS, OAuth2 health monitor + proactive token refresh, PM credential revocation UI, auto-association + re-association at tracker creation / PM reassignment (FR-32–FR-38 content; deferred: v2 per `[D-019]` — v1 uses ops-provisioned K8s Secrets via FR-51; revisit: when PMs need to self-register credentials without ops involvement).
