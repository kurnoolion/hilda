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
- **FR-2** `[Ph-1]` — Tracker creation auto-populates the full Milestone → DeliveryItem hierarchy from the template, with all static fields pre-populated per `HILDA_Design.md` §3.3; DeliveryItems are grouped within a milestone by `tg_name` (anchors `[D-028]`); `plm_id` is not set at tracker creation — it is assigned per (owner × milestone) at collection kickoff (FR-8); each DeliveryItem's `expected_completion_date` is set at tracker creation to the parent `Milestone.target_date` and can be overridden per-item by the PM post-creation (FR-14); `doc_count` is pre-populated from the template per item and determines how many test reports must be received before `DocumentReceived` state is reached (FR-7).
- **FR-3** `[Ph-1]` — PM can add, remove, or reassign DeliveryItems after instantiation without re-creating the tracker.
- ~~**FR-4**~~ — *(deferred 2026-05-12 → DEF-15: Excel import schema validation — implementation phase TBD)*
- **FR-5** `[Ph-1]` — Hierarchical data is enforced as Devices/Milestones/DeliveryItems with uniqueness on (device_id, milestone_name), (milestone_id, item_name), and (milestone_id, item_no); no Deliverable intermediate level (anchors `[D-028]`); all DeliveryItems with the same (milestone_id, owner) share the same `plm_id` — the PLM issue is one per (owner × milestone), not per item (anchors `[D-035]`).
- **FR-6** `[Ph-1]` — Milestone status and `completion_pct` are computed from the states of their child DeliveryItems.
- **FR-7** `[Ph-1]` — Item types, tracking modalities, customer delivery modalities, `delivery_state` values, and `doc_count` are extensible via configuration without code change; `doc_count` is a per-DeliveryItem integer from the template (default 1) specifying how many `test_report` documents must be received before the item advances to `DocumentReceived` state — applies only to artifact item types; `Confirmation` items have `doc_count = 0`; `tracking_modality` is a **multi-value field** (list) per DeliveryItem supporting the following v1 values — `Email` `[Ph-1]` (status + documents via email reply), `CorporateMessenger` `[Ph-2]` (status only; no attachments; not document-capable), `CorporatePLM` `[Ph-1]` (documents only; not status-capable; owner uploads to PLM directly; HILDA polls PLM, downloads, and writes the document file to `<doc_type_slug>/<doc_id_slug>/rev1/`; document arrival advances item to `DocumentReceived` intermediate state — does **not** set `OwnerClosed`), `NetworkSharedDrive` `[Ph-1]` (documents only; not status-capable; owner drops in shared drive `inbound/` folder; HILDA polls; document arrival advances item to `DocumentReceived` intermediate state — does **not** set `OwnerClosed`), `CustomerJIRA` `[Ph-1]` (status only; not document-capable; HILDA polls customer JIRA API for waiver/issue closure status; no outbound; no attachments); valid combinations require at least one status-capable modality (`[Ph-1]` `Email` or `CustomerJIRA`; `[Ph-2]` `CorporateMessenger` added) and at least one document-capable modality (`Email`, `CorporatePLM`, or `NetworkSharedDrive`) for items with artifact deliverables; `CorporateMessenger` escalation (FR-10) is automatic regardless of modality and is not a modality value itself; `OwnerClosed` state is set exclusively via explicit owner "done" confirmation received on a status-capable modality (`Email` `[Ph-1]` or `CorporateMessenger` `[Ph-2]`) — document receipt alone via `CorporatePLM` or `NetworkSharedDrive` advances to `DocumentReceived` only; `[Ph-2]` owner may self-close directly in the SharePoint UI (FR-56); v1 `delivery_state` enumeration: `Open` (initial, set at tracker creation), `OutreachSent` (initial outreach dispatched per FR-9), `DocumentReceived` (document arrived via any ingest channel per FR-28), `UnderReview` (HILDA LLM review complete, findings surfaced to PM per FR-53), `OwnerClosed` (owner confirmed done via status-capable modality), `Delayed` (owner-reported delay — set via status modality, any pre-approval state), `Blocked` (owner-reported blocker — set via status modality, any pre-approval state), `ReadyForSubmission` (PM approved per FR-28 `PMApproval` trigger), `SubmittedToCustomer` `[Ph-2]` (submission package dispatched to customer per FR-18), `Closed` `[Ph-2]` (customer approval + PM confirmation per FR-22); `Delayed` and `Blocked` are transient — PM or owner can move the item back to the prior active state; `Confirmation` items skip `DocumentReceived` and `UnderReview`; `CustomerJIRA` items reach `OwnerClosed` via HILDA detecting JIRA closure (no owner message required); v1 `doc_type` enumeration (inferred by HILDA from filename and first-page text during routing per FR-52/FR-55; used as folder organiser `<doc_type_slug>/` on shared drive and as document index key; extensible via configuration): `test_report` (lab test report — triggers FR-16 parser + FR-53 LLM review), `tech_report` (technical report — triggers FR-53 LLM review only), `waiver` (waiver document — triggers FR-53 LLM review only); `doc_type` is distinct from `item_type` — a single DeliveryItem may receive documents of more than one `doc_type` across its lifecycle.

### Collection kickoff & ongoing tracking

- **FR-8** `[Ph-1]` — PM or TPM clicks **Start Collection** from the SharePoint milestone view (FR-56), sending one request to HILDA for the entire milestone; HILDA creates one PLM issue per unique (owner × milestone) pair via the IssueTracker adapter and writes the resulting `plm_id` to all DeliveryItems for that (owner, milestone) — this is a batch operation across the whole milestone, not per item; after PLM issue creation, HILDA fires FR-9 initial outreach for all open DeliveryItems in the milestone, advancing them to `OutreachSent`; PLM issue creation is idempotent — re-triggering Start Collection does not create duplicate issues for pairs that already have a `plm_id`; for items added to the milestone after Start Collection has already fired, per-item `StartItemCollection` logic applies (FR-28 `ItemCreated` trigger, FR-29).
- **FR-9** `[Ph-1]` — Initial owner outreach is sent via the DeliveryItem's `tracking_modality` (see FR-7 for all values); for `Email` modality, multiple DeliveryItems owned by the same recipient are consolidated into one outbound message per round identified by a stable `BATCH-<id>`, with a per-item structured reply block grouped by `tg_name` `[Ph-1]` and per-item `mailto:` quick-update tap-links `[Ph-2]` in the body; for `CorporateMessenger` modality, no initial outreach message is sent — CorporateMessenger is used exclusively as an escalation channel per FR-10 when email reminders yield no owner response; for `CorporatePLM` and `NetworkSharedDrive` modalities, outreach informs the owner of the expected delivery path (PLM issue reference or shared drive path) — outbound format TBD architecture phase; for `CustomerJIRA` modality, no HILDA-initiated outreach (HILDA polls the customer JIRA passively); on dispatch, all outreached DeliveryItems advance to `OutreachSent` delivery state.
- **FR-10** `[Ph-1]` — Rule engine sends scheduled reminders to owners when `delivery_state = "Open"` and `days_since_last_contact > N` (N is per-rule configurable); if email reminders yield no owner response for a further configurable period (no `last_updated` change after reminder_count ≥ M), the rule engine additionally sends a cross-channel escalation via corp messenger `[Ph-1]` (outbound only — inbound messenger responses handled by FR-54); if the owner has multiple open items in the same milestone, the messenger escalation aggregates all open items into a single message to minimise transactions.
- **FR-11** `[Ph-1]` — Rule engine escalates to owner + PM when `expected_completion_date - today ≤ N` and item is not Closed; `expected_completion_date` is set at tracker creation from the parent `Milestone.target_date` (FR-2) and may be overridden per-item by the PM (FR-14).
- **FR-12** `[Ph-1]` — Inbound email replies route to the correct DeliveryItems via three convergent paths, all keyed on the `BATCH-<id>`: (a) `[Ph-1]` a structured reply block edited in place by the owner, regex-parsed from the body; (b) `[Ph-2]` per-item `mailto:` tap-links that pre-compose tiny emails parsed from the subject (`[HILDA] BATCH-<id> ITEM-<n> <STATUS>`); (c) `[Ph-1]` free-text replies that match neither path are processed in two stages — (1) rule-based parsing attempts to extract status and comment against item-name tokens and `BATCH-<id>` context; above threshold, auto-apply and notify PM; (2) if rule-based parsing fails, the runtime LLM infers item classification and status — above LLM confidence threshold, auto-apply; below threshold, record as comments on all batch items and surface as a Manual triage flag on the PM dashboard (DEF-1 promoted 2026-05-13); when path (c) fires on an email that also contains attachments, a single fused LLM call processes email body, first-page attachment excerpts, and the batch DeliveryItem list together, covering both message classification and attachment routing in one pass (see FR-52, anchors `[D-034]`). Status applies are idempotent on `(BATCH-id, item-index, status)`; outbound is sent multipart/alternative and the structured block is ASCII-only; inbound email attachments are routed to DeliveryItems per FR-52. The sender email address is captured in `CommunicationLog` for attribution on every inbound path; if the sender does not match the registered owner email for the BATCH-id, the update is applied and a `Sender mismatch` note is surfaced on the PM dashboard for PM review (not a hard rejection); stricter sender-enforcement rules are configurable via `AutomationRules`.
- **FR-13** `[Ph-1]` — Inbound owner deliverables and HILDA-generated internal artifacts are managed via the on-prem shared network drive following the fixed root path `\\share\hilda\<carrier_slug>\<device_slug>\<milestone_slug>\<item_slug>\` (slug-encoded immutable identifiers; "carrier" = customer / certification body in HILDA's domain); **corp PLM is the source of truth for all owner deliverables** — the shared drive is an ingest channel and local audit trail, not the authoritative store (anchors `[D-035]`); PLM issues are created one per (owner × milestone) at collection kickoff per FR-8. Each item root path contains three areas: **(a) `inbound/`** — raw drop zone for the `NetworkSharedDrive` ingest channel exclusively; owners with filesystem write access drop documents here; HILDA monitors and processes per FR-55; **(b) `outbound/`** — HILDA-generated internal artifacts only (QC reports, diagnostics, submission-review outputs); these are never owner deliverables and are never written to PLM; **(c) `<doc_type_slug>/<doc_id_slug>/rev1/`** — classified audit storage written by HILDA after routing and PLM upload, for all three ingest sources (email, PLM poll, NSD `inbound/`); `doc_id_slug` is derived by slugifying the first-received filename for that (delivery_item_id, doc_type) pair. Ingest write flows by source: for **email**, HILDA classifies the document via FR-52, uploads to the owner's PLM issue via the IssueTracker adapter, records `(delivery_item_id, plm_id, doc_type, doc_id_slug, rev_number, plm_attachment_id, upload_timestamp, ingest_source, local_classified_path, original_filename, file_hash, parser_result, llm_review_findings)` in the HILDA document index, and writes the document file to `<doc_type_slug>/<doc_id_slug>/rev1/`; for **PLM poll**, HILDA classifies the document via FR-52 — the document is already in PLM as the source of truth and is **not re-uploaded**; `plm_attachment_id` is already known from the poll and written directly to the document index alongside the local classified path; for **NSD `inbound/`**, HILDA detects the drop per FR-55, classifies `doc_type`, uploads to the owner's PLM issue, writes the document file to `<doc_type_slug>/<doc_id_slug>/rev1/`, and records in the document index. All download links are HILDA-mediated (`https://hilda.corp/dl/<scoped_token>`) resolving to the PLM attachment — never as direct UNC paths (anchors NFR-16); the DeliveryItem's `actual_item_info` field holds the PLM issue URL for the (owner × milestone) pair per FR-57. `[Ph-2]` Subsequent revisions are written to `<doc_type_slug>/<doc_id_slug>/revN/` (N ≥ 2) per FR-17.
- **FR-55** `[Ph-1]` — For DeliveryItems whose `tracking_modality includes NetworkSharedDrive`, HILDA monitors the item's `inbound/` drop folder on the shared network drive by configurable-interval polling; when a new file is detected (written directly by an owner with filesystem write access), the item is identified from the folder path (no FR-52 item-routing needed), the file is recorded in `CommunicationLog` with the owner's filesystem identity, HILDA classifies the `doc_type` from filename and first-page text, uploads the document to the owner's PLM issue (PLM = source of truth per FR-13), writes the document file to `<doc_type_slug>/<doc_id_slug>/rev1/`, updates the HILDA document index with `(delivery_item_id, plm_id, doc_type, doc_id_slug, rev_number, plm_attachment_id, upload_timestamp, ingest_source, local_classified_path, original_filename, file_hash, parser_result, llm_review_findings)`, updates the DeliveryItem record in SharePoint and sets `actual_item_info` to the PLM issue URL (constructed from `plm_id` per FR-57) on first document arrival for this item, advances item to `DocumentReceived` delivery state, and triggers LLM quality review per FR-53 which advances the item to `UnderReview` on completion (does **not** set `OwnerClosed` — explicit owner confirmation via Email `[Ph-1]` or CorporateMessenger `[Ph-2]` is required to reach `OwnerClosed`); detection latency is bounded by the configurable poll interval (default ≤ 5 minutes); HILDA does **not** poll drop folders for items where `tracking_modality` does not include `NetworkSharedDrive`. `[Ph-2]` HILDA additionally monitors the item's `<doc_type_slug>/<doc_id_slug>/revN/` folders by configurable-interval polling; detection of a new file written directly by an owner triggers the revision path per FR-17(b) — re-parse, classifier re-run, subsequent LLM review, and SharePoint update.
- **FR-52** `[Ph-1]` — When one or more documents arrive from any inbound source — email attachment (via FR-12) or corp PLM poll (via FR-26) — the system routes each document to the corresponding DeliveryItem via a two-tier process (anchors `[D-033]`): (1) extract filename and first-page text from each document and fuzzy-match against item name, description, and `item_type` within the (owner × milestone) batch — filenames from any source (email or PLM) may be opaque lab IDs in which case first-page text is the primary signal; high-confidence fuzzy matches are applied directly; (2) for documents where fuzzy matching cannot resolve the mapping, the runtime LLM inspects the filename and first-page content together with the batch DeliveryItem list to determine the match; **fused vs. standalone path:** when the triggering email also fires FR-12 path (c), email attachment routing is subsumed into a single fused LLM call for that path — path (c) delivers both a free-text message body and attachments simultaneously, so one LLM call resolves both message classification and document routing together for efficiency and shared context (anchors `[D-034]`); the standalone two-tier process does not run separately for those attachments; PLM-polled documents always use the standalone two-tier path because they arrive with no concurrent message body and the fused call does not apply; `[Ph-2]` after item and `doc_type` are resolved, the system classifies the incoming document as new or a revision before routing (mechanism per `[D-039]`; applies to email source; PLM-polled documents use the `(file_name, timestamp)` detection in FR-26): exact duplicate documents are detected and skipped — logged in `CommunicationLog` and PM notified; non-duplicate documents are classified as new (route to `rev1/`) or revision (route to `revN/`, N = max existing `rev_number` + 1 per FR-17); ambiguous cases where the document cannot be unambiguously assigned as new or revision are held in staging and a `Document classification ambiguous` flag surfaced on the PM dashboard for manual assignment; confirmed matches trigger: (1) upload to the owner's PLM issue via the IssueTracker adapter — **skipped for CorporatePLM-polled documents**, which are already present in PLM as the source of truth and must not be re-uploaded; for PLM-sourced documents the `plm_attachment_id` is already known from the poll and written directly to the document index; (2) the document file written to `<doc_type_slug>/<doc_id_slug>/rev1/` under the item's shared drive path per FR-13; (3) the HILDA document index updated with `(delivery_item_id, plm_id, doc_type, doc_id_slug, rev_number, plm_attachment_id, upload_timestamp, ingest_source, local_classified_path, original_filename, file_hash, parser_result, llm_review_findings)`; (4) `delivery_state` advanced to `DocumentReceived` and LLM quality review triggered per FR-53, which advances the item to `UnderReview` on completion; (5) the DeliveryItem record updated in SharePoint and `actual_item_info` set to the PLM issue URL for the (owner × milestone) pair (constructed from `plm_id` by the IssueTracker adapter) on first document arrival — stable thereafter per FR-57; unresolved documents are surfaced on the PM dashboard for manual assignment.
- **FR-14** `[Ph-1]` — PM can manually override DeliveryItem dates, owners, comments, and `delivery_state` from the dashboard, and can trigger ad-hoc reminders independent of the scheduled rule cadence.
- **FR-15** `[Ph-1]` — `last_owner_contacted` and `last_updated` timestamps update on every DeliveryItem status change.

### PM review & resolution path (Stage 4)

- **FR-16** `[Ph-1]` — On receipt of a test-report document via any ingest channel — email attachment (FR-52), NSD drop (FR-55), PLM poll (FR-26), or SharePoint UI upload (FR-62) — triggered by the `TriggerParser` rule action (FR-28/FR-29) after document routing and `doc_type` classification confirm `doc_type = test_report`; the system runs the per-customer test report parser (generated by the Test Report Document Profiler per `[D-011]`) to extract per-test-case `(test_case_id, status ∈ {passed, failed, non-applicable, waived, not-started}, [waiver_ref])` tuples, the canonical classifier emits `final | interim` per FR-46, and the PM is presented with the classification + per-test-case status grid for review and resolution-path determination on unresolved failures.
- **FR-53** `[Ph-1]` — On receipt of a test report, tech report, or waiver document linked to a DeliveryItem via any ingest channel (email FR-52, NSD FR-55, PLM poll FR-26, or SharePoint UI upload FR-62), the runtime LLM performs an initial quality review against the per-customer checklist generated by the Test Report Profiler `[D-011]`, writes findings to the `llm_review_findings` field of the document index row for the current `(doc_type, doc_id_slug, rev_number)`, and updates a summary `review_status` flag on the DeliveryItem record in SharePoint; surfaces findings on the PM dashboard for TPM review; Ph-1 scope is initial review, findings persistence to document index, and dashboard display only — PM response tracking, owner revision communication, and multi-version re-review are deferred (DEF-2 remainder, FR-17).
- **FR-17** `[Ph-2]` — When a revised document arrives for a DeliveryItem — classified as a revision (not a new document or duplicate) by FR-52's detection mechanism per `[D-039]` — and the HILDA document index already has a document of the same type for that item, the system stores the revision at `<doc_type_slug>/<doc_id_slug>/revN/` per FR-13 (N = next revision number), updates the DeliveryItem record with the new attachment link, re-runs the test report parser and classifier (FR-46) against the new version, performs a subsequent LLM quality review against the same per-customer checklist as FR-53, and writes updated `parser_result` and `llm_review_findings` to the document index row for the new revision — prior revision review results are preserved in their own rows and never overwritten. Revisions are detected and handled per source: `[Ph-2]` **(a) Email (10.1 / 10.1.1)** — a revised document arriving via email is first routed to the DeliveryItem via FR-52's two-tier process; after (item, document_type) are resolved, the system checks whether a prior document of that type already exists in the HILDA document index — if so, the file is written to `<doc_type_slug>/<doc_id_slug>/revN/` regardless of whether the filename matches the original; PLM auto-upload applies to `revN/` writes under the same modality conditional as FR-13. `[Ph-2]` **(b) Shared network drive (10.2)** — the owner drops the revised document into the item's `inbound/` folder on the shared drive (same drop zone as the initial submission, per FR-55); HILDA detects the new file via FR-55 configurable-interval polling; FR-52 applies the D-039 hash-based classification — hash match = duplicate (skip); slug match = revision (route to `revN/`); new slug = new document (route to `rev1/`); on confirmed revision, HILDA writes the file to `<doc_type_slug>/<doc_id_slug>/revN/`, uploads to PLM under the same modality conditional as FR-13, updates the document index, re-runs the parser and classifier, triggers subsequent LLM review, and updates the DeliveryItem record in SharePoint. `[Ph-2]` **(c) PLM (10.3 / 10.3.1)** — during each PLM poll cycle per FR-26, HILDA downloads every document attached to the PLM issue and applies the D-039 hash-based classification to each: hash matches an existing document index row → duplicate, skip; `doc_id_slug` derived from filename matches an existing row → revision, write to `<doc_type_slug>/<doc_id_slug>/revN/` on the shared drive, update the document index, re-run the classifier, and trigger subsequent LLM review with SharePoint update; no slug match → new document, route via FR-52 as normal.
- **FR-46** `[Ph-1]` — A test report is classified `final` iff every test case is in `{passed, non-applicable, waived}` AND every `failed` test case carries a `waiver_ref` (which reclassifies it as `waived`); otherwise the report is `interim` (anchors `[D-011]`).
- **FR-47** `[Ph-2]` — For every `failed` test case without a `waiver_ref` in a test report, the system surfaces the test case on the PM dashboard for resolution-path determination (fix-pre-launch / tech report / waiver), feeding FR-16's auto-create logic.
- **FR-48** `[Ph-2]` — When the PM-determined resolution path is `waiver`, the system auto-creates a Waiver DeliveryItem with its own lifecycle; the test report classifier consumes only the existence of `waiver_ref` (boolean), not the waiver's outcome — the TPM (Technical Project Manager) is not the final authority on the waiver path, which is owned by the Waiver DeliveryItem's separate workflow.

### SharePoint UI

- **FR-56** `[Ph-1]` — The SharePoint UI presents a per-milestone view (classic web part) listing all DeliveryItems for that milestone, grouped by `tg_name`; each row displays `item_no`, item name, owner, `delivery_state`, and `expected_completion_date`; items where `item_type = Confirmation` show no document section; all other item types show a document section populated via `actual_item_info` (FR-57); the view includes a **Start Collection** action (available to PM and TPM; enabled when the milestone has not yet been kicked off) that triggers FR-8 — PLM issue creation per (owner × milestone) and initial owner outreach per FR-9; this view is the primary TPM review surface; `[Ph-2]` item rows include a **Close Item** action enabling the item's assigned owner to self-close directly from the SharePoint UI.

- **FR-57** `[Ph-1]` — `actual_item_info` on `DeliveryItem` stores the PLM issue URL for the (owner × milestone) pair, constructed from `plm_id` by the IssueTracker adapter; set on first document arrival per FR-52 or FR-55 and stable thereafter; this URL is the direct link to the PLM issue where all owner deliverables for this (owner × milestone) are attached; the SharePoint document section (FR-59) is rendered via the separate HILDA document enumeration API `https://hilda.corp/docs/<item_id>`, which queries the HILDA document index (populated at ingest time per FR-52, FR-55, and FR-26) and returns `[{doc_type, doc_id_slug, rev_number, original_filename, download_url, parser_result, llm_review_findings}]`; natural key of the document index: `(delivery_item_id, doc_type, doc_id_slug, rev_number)`; `download_url` is a HILDA-mediated link per FR-61 that resolves to the PLM attachment; `parser_result` and `llm_review_findings` are null for revisions not yet reviewed; `[Ph-1]` returns one entry per (doc_type, doc_id_slug) pair for the latest revision (determined by `rev_number`); `[Ph-2]` supports an `all_revisions=true` query parameter returning all revision rows per (doc_type, doc_id_slug) ordered by `rev_number` ascending — used by FR-60 expandable history view.

- **FR-58** `[Ph-1]` — `item_type` is the discriminator for document-carrying behavior: `Confirmation` items (owner reply closes the item; no artifact deliverable) have no document section, no document routing, and no review pipeline; all other item types expose document receipt, routing (FR-52 / FR-55), initial review (FR-53), and document display (FR-59 / FR-60 / FR-61).

- **FR-59** `[Ph-1]` — The SharePoint UI document section for each applicable DeliveryItem calls the HILDA document enumeration API (FR-57) and renders one row per document grouped by `doc_type`; each row shows `doc_type`, `doc_id_slug` (human-readable label derived from rev1 filename), `rev_number`, `original_filename` (filename as received for that revision), and a HILDA-mediated download link (FR-61); a **View in PLM** link is rendered per item using `actual_item_info` (the PLM issue URL per FR-57) for PMs who prefer to view documents directly in PLM; in Ph-1 each document has exactly one version (`rev1/` for email / PLM poll; single file in `inbound/` for NSD); `[Ph-2]` when multiple revisions exist, only the latest revision of each document is shown in the list view.

- **FR-60** `[Ph-1]` — The SharePoint UI displays review results alongside each document row in the document section: (a) rule-based parser output from FR-16 — per-item status grid and `final | interim` classification per FR-46 — for test reports; (b) LLM quality review findings from FR-53 for test reports, tech reports, and waivers; both result sets are stored in the document index per revision (`parser_result` and `llm_review_findings` fields, keyed by `delivery_item_id, doc_type, doc_id_slug, rev_number`) and surfaced via the HILDA document enumeration API (FR-57); `[Ph-1]` the latest revision's review results are rendered inline on each document row; `[Ph-2]` each document row is expandable to show all revisions with their individual `parser_result` and `llm_review_findings`, enabling the PM to track the improvement trajectory across revisions (e.g., rev1: interim — 3 failures; rev2: final — all passed).

- **FR-61** `[Ph-1]` — Each document row in FR-59 provides a HILDA-mediated download link (`https://hilda.corp/dl/<scoped_token>`) authenticated via on-prem AD and authorized against the DeliveryItem ACL per NFR-16; in Ph-1 the link resolves to the single available version (`rev1/` or `inbound/` file); `[Ph-2]` download resolves to the latest revision of that document (determined by `upload_timestamp` in the document index); download is available only for items where `item_type ≠ Confirmation`.

- **FR-62** `[Ph-2]` — The SharePoint UI provides a document upload surface for each applicable DeliveryItem (`item_type ≠ Confirmation`); item and `doc_type` are specified explicitly by the PM or TPM in the UI (no FR-52 routing needed); HILDA uploads the document to the owner's PLM issue, writes the document file to `<doc_type_slug>/<doc_id_slug>/rev1/` per FR-13, updates the HILDA document index with `(delivery_item_id, plm_id, doc_type, doc_id_slug, rev_number, plm_attachment_id, upload_timestamp, ingest_source, local_classified_path, original_filename, file_hash, parser_result, llm_review_findings)`, updates the DeliveryItem record in SharePoint (`actual_item_info` is already set to the PLM issue URL per FR-57 from the item's first document arrival and does not change), and triggers initial review per FR-53.

- **FR-63** `[Ph-2]` — The SharePoint milestone view (FR-56) presents a **Submit to Carrier** action available to PM and TPM; enabled only when all DeliveryItems in the milestone are in `ReadyForSubmission` state; on activation, HILDA assembles and dispatches the submission package per FR-18 via the customer adapter; on successful dispatch, all items in the milestone advance to `SubmittedToCustomer` delivery state; the action is milestone-scoped — there is no per-item submission from the SharePoint UI.

- **FR-64** `[Ph-2]` — The SharePoint milestone view (FR-56) presents a **Close All Items** action available to PM and TPM; enabled only when all DeliveryItems in the milestone are in `SubmittedToCustomer` state; this action represents PM confirmation that the customer has accepted all submitted deliverables; on activation, HILDA advances all items in the milestone to `Closed` delivery state per FR-22 and fires `MilestoneAllClosed` per FR-28; the action requires explicit PM confirmation (NFR-5 gate) before executing; the action is milestone-scoped — there is no per-item close from the SharePoint UI at this stage.

- **FR-65** `[Ph-1]` — Each DeliveryItem row in the SharePoint milestone view (FR-56) presents a **Send Reminder** action available to PM and TPM; the action is available at any time regardless of the scheduled rule cadence (FR-10) and regardless of the item's current `delivery_state`, except when `delivery_state ∈ {OwnerClosed, ReadyForSubmission, SubmittedToCustomer, Closed}`; on activation, HILDA immediately dispatches a reminder to the item's owner via all status-capable modalities in the item's `tracking_modality` list (per FR-9 outreach format); the send is recorded in `CommunicationLog` per FR-42 with PM attribution; `last_owner_contacted` is updated per FR-15; this action is the SP UI surface for the ad-hoc reminder capability stated in FR-14 and the manual trigger capability in FR-31.

### Submission (Stage 5)

- **FR-18** `[Ph-2]` — System assembles the submission package by resolving the latest document for each DeliveryItem via the HILDA document index (FR-57), downloading each document from the owner's PLM issue via the IssueTracker adapter using the recorded `plm_attachment_id` (PLM is the source of truth per FR-13; the shared network drive `<doc_type_slug>/<doc_id_slug>/revN/` paths are the local audit trail, not the assembly source); assembly is triggered by the **Submit to Carrier** action in the SharePoint UI (FR-63) once all DeliveryItems for a milestone reach `ReadyForSubmission` state (set by `PMApproval` trigger per FR-28); dispatched per the customer's `customer_delivery_modality`; each successfully dispatched item advances to `SubmittedToCustomer` delivery state.
- **FR-19** `[Ph-2]` — Customer adapters implement the surface `{submitItem, getStatus, postComment, uploadAttachment}` and authenticate as the PM using the PM's stored credentials (never a service account).
- **FR-20** `[Ph-2]` — Submission is blocked and queued (with PM dashboard alert) when the PM's credential for the target customer system is missing or expired.

### Customer follow-up & closure (Stage 6)

- **FR-21** `[Ph-2]` — System captures customer feedback from the customer's tracking system and email and surfaces it on the PM dashboard with source + timestamp.
- **FR-22** `[Ph-2]` — DeliveryItem transitions to `Closed` only on customer approval AND explicit PM confirmation; PM confirmation is made via the **Close All Items** action in the SharePoint UI (FR-64); Milestone transitions to `Complete` when all child DeliveryItems are `Closed`.

### Communication adapters — Email Service

- **FR-23** `[Ph-1]` — Email Service owns a dedicated mailbox, polls inbound 24/7 (or accepts push notifications from the mail server), and emits outbound on behalf of PMs with the PM's name in the signature and a stable From address.
- **FR-24** `[Ph-1]` — Outbound email subject lines embed the structured reference tag (device, PM, milestone, deliverable, item); the Email Service parses the same tag from inbound replies for routing and captures the sender email address alongside the tag for attribution and anomaly detection per FR-12.

### Communication adapters — IssueTracker (internal)

- **FR-25** `[Ph-1]` — The `IssueTracker` Protocol per `[D-008]` serves two distinct roles in HILDA: (a) **corp PLM adapter** — document storage and source of truth for owner deliverables, one issue per (owner × milestone), accessed via the proprietary corp PLM adapter generated by the API Spec Ingestor `[D-003]`; HILDA polls this issue when `tracking_modality includes CorporatePLM` (FR-13, FR-26); (b) **customer JIRA adapter** — HILDA polls the customer's JIRA instance for waiver and issue closure status when `tracking_modality includes CustomerJIRA`, wired via `core/src/issue_tracker/jira_adapter.py` (public Jira REST API); **there is no internal-Jira tracking in v1** — JIRA is used exclusively for customer-facing closure/status polling; both roles use the identical Protocol surface, distinguished only by adapter and configuration.
- **FR-26** `[Ph-1]` — Corp PLM issues are created once per (owner × milestone) at collection kickoff (FR-8), not per DeliveryItem; the PLM issue is **document-only** — corp PLM does **not** sync status back to HILDA; when `tracking_modality includes CorporatePLM`, HILDA polls the PLM issue at a configurable interval for new owner-uploaded documents, routes each to the corresponding DeliveryItem via FR-52, downloads from PLM, writes the document file to `<doc_type_slug>/<doc_id_slug>/rev1/` under the item's shared drive path per FR-13, and updates the HILDA document index; `[Ph-2]` during each poll cycle HILDA downloads every document attached to the PLM issue and applies the D-039 hash-based classification: hash match → duplicate, skip; `doc_id_slug` derived from filename matches an existing document index row → revision, handled per FR-17(c); no slug match → new document, routed via FR-52 as normal; when `tracking_modality does not include CorporatePLM`, HILDA receives documents via email (FR-12 / FR-52) or NSD `inbound/` (FR-55) and uploads each to the owner's PLM issue per FR-13 — PLM is the source of truth in all cases (anchors `[D-035]`); in all cases corp PLM is the document source of truth (anchors `[D-035]`); there is no internal-Jira tracking — the Jira adapter is used only for customer-facing `CustomerJIRA` modality per FR-25. `[Ph-2]` The PLM auto-upload conditional extends to documents written to `<doc_type_slug>/<doc_id_slug>/revN/` under the same modality rules — when `tracking_modality does not include CorporatePLM`, HILDA auto-pushes revised documents to the PLM issue alongside initial documents (per FR-17).

### Communication adapters — Messenger

- **FR-50** `[Ph-1]` — Messenger adapter implements the `Messenger` Protocol per `[D-009]`; v1 targets are **Slack** (adapter at `core/src/messenger/slack_adapter.py`, Slack Web API via `slack_sdk`) and the **proprietary internal messenger** (adapter at `customizations/messenger/<proprietary>_adapter.py`, generated by the API Spec Ingestor per `[D-003]` as its first end-to-end exercise in v1); both adapters must pass the same `Messenger` Protocol contract test suite (anchors `[D-016]`); corp messenger carries **status only** — attachment receipt via messenger is not supported.
- **FR-54** `[Ph-2]` — Inbound corp messenger messages from owners are captured on receipt, attributed by sender handle to the owner, recorded as comments on all open DeliveryItems in the owner's active batch, and surfaced as a `Messenger reply — Manual triage` flag on the PM dashboard; if the owner has multiple open items across milestones, flags are grouped by milestone to minimise PM triage actions; the runtime LLM classifies the message content to extract status (closed / open / delayed / blocked) and reason — above-threshold classifications are auto-applied; below-threshold go to PM triage.

### Communication adapters — Customer systems (pluggable)

- **FR-27** `[Ph-2]` — Customer adapters are registered via configuration (AutomationRules + per-customer config) including endpoint URL, field mappings, and outbound templates; adding a new customer requires no code change in `core/`.

### Rule engine

- **FR-28** `[Ph-1]` — Rule engine executes IF/THEN AutomationRules. Trigger taxonomy (rules reference data model fields directly and are customer-agnostic in shape):
  - `ItemCreated` — at tracker creation (FR-2): no action, collection not yet started; mid-collection (FR-3, collection active): fires `StartItemCollection`
  - `ItemModified` `[Ph-1]` — sub-triggers:
    - `OwnerReassigned`: owner field changed → `NotifyNewOwner` + `StartItemCollection` for new (owner × milestone) if collection active
    - `DeadlineMoved`: `expected_completion_date` changed → re-arm `DeadlineProximity` evaluation immediately
  - `ItemDeleted` `[Ph-2]` — PM removes item via FR-3 → `CancelOutstanding`
  - `StateChange` — fires on any `delivery_state` change
  - `OwnerStatusConfirmed` — explicit owner status message received via status-capable modality (`Email` `[Ph-1]`, `CorporateMessenger` `[Ph-2]`); maps owner-reported status to `delivery_state`: "done" → `OwnerClosed`, "delayed" → `Delayed`, "blocked" → `Blocked`; state transition guard for "done": `item_type = Confirmation` may reach `OwnerClosed` directly from `OutreachSent` (no document required); all other item types require `delivery_state` to be `DocumentReceived` or `UnderReview` before `OwnerClosed` is accepted — if a "done" message arrives while the item is still in `OutreachSent`, the state is not advanced and a `Premature closure attempt` flag is surfaced on the PM dashboard; document receipt alone via PLM or NSD does **not** fire this trigger — see `AttachmentReceived`
  - `LastContactThreshold` — fires when `days_since_last_contact > N` (FR-10)
  - `DeadlineProximity` — fires when `expected_completion_date - today ≤ N` (FR-11)
  - `AttachmentReceived` — document arrives via any ingest channel (email attachment, PLM poll, NSD drop); behaviour depends on `doc_type`: if `doc_type = test_report`, system checks count of `test_report` rows in the document index for this item — if count reaches `doc_count` (from template, FR-7), → `UpdateState` to `DocumentReceived`; if count < `doc_count`, document is recorded in the index and FR-53 LLM review is triggered but `delivery_state` remains unchanged; supplementary documents (`waiver`, `tech_report`) are always recorded in the index and trigger FR-53 LLM review but do **not** advance `delivery_state`; does **not** set `OwnerClosed`
  - `AIReviewResult` — HILDA LLM review complete per FR-53 → `UpdateState` to `UnderReview` + `NotifyPM` surfacing findings to PM dashboard
  - `PMApproval` — per-item; PM explicitly approves item action → `QueueSubmission` + `UpdateState` to `ReadyForSubmission`; NFR-5 gate before any customer-facing action
  - `MilestoneAllClosed` `[Ph-2]` — milestone-level trigger, not a per-item event; fires when all DeliveryItems in a milestone reach `OwnerClosed` → `NotifyPM` per FR-22 + `[Ph-2]` queue submission assembly per FR-18

  Rule bodies cited by FR ID: FR-8 (PLM issue creation + initial outreach at Start Collection), FR-9 (per-modality outreach format), FR-10 (reminder + messenger escalation), FR-11 (deadline escalation to owner + PM), FR-16 (test-report parser trigger on attachment), FR-22 (milestone-closure state + PM notification), FR-46 (final/interim classification on test-report receipt), FR-47 (failed-item surface on PM dashboard), FR-48 (waiver DeliveryItem auto-create), FR-53 (AI quality review trigger), NFR-5 (PM-approval gate before any customer-facing action), FR-18 (queue for customer submission on PM approval).
- **FR-29** `[Ph-1]` — Rule actions include `SendReminder`, `Escalate`, `UpdateState`, `StartItemCollection` (idempotent PLM issue creation for an (owner × milestone) pair followed by `SendInitialOutreach` — used by `ItemCreated` mid-collection and `OwnerReassigned`), `SendInitialOutreach` (per-modality initial owner outreach per FR-9), `NotifyNewOwner` (outreach to reassigned owner including current item state and delivery path), `TriggerParser`, `TriggerAIReview`, `QueueSubmission`, `NotifyPM`, and `CancelOutstanding` `[Ph-2]` (cancel all pending reminders and escalations for a removed item and notify owner of removal); `TriggerParser` invokes the rule-based test-report parser (FR-16) distinct from the LLM review; `NotifyPM` surfaces a dashboard alert and/or out-of-band notification without an owner-facing outbound; action parameters (channel, recipients, template, target state) are rule-instance-specific; new action types are added via configuration without code change.
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

---

## Appendix A — Rule Engine: Triggers, Conditions, and Actions

All rules fire via `AutomationRules` evaluated by the rule engine (FR-28 – FR-31). Column definitions: **Trigger** = event type from the FR-28 taxonomy; **Condition** = guard that must hold for the rule to fire; **Actions** = FR-29 action verbs invoked; **State Transition** = resulting `delivery_state` change on the target DeliveryItem; **Ph** = earliest phase in which the trigger/action is active; **FR refs** = authoritative requirements.

> **Note — Start Collection (FR-8):** PM/TPM clicking **Start Collection** in the SharePoint UI (FR-56) is a direct HILDA request, not a rule engine trigger. It creates PLM issues per (owner × milestone), calls `SendInitialOutreach` for all open items, and advances all to `OutreachSent`. For items added after Start Collection has already fired, `ItemCreated` mid-collection fires `StartItemCollection` instead (row 2).

> **Note — StateChange (row 6):** `StateChange` fires on any `delivery_state` transition and is an observation point for downstream rules. It does not independently initiate state transitions and has no default action — rules scoped to it are customer-configurable.

### A.1 Rule engine trigger table

| # | Trigger | Condition | Actions | State Transition | Ph | FR refs |
|---|---------|-----------|---------|------------------|----|---------|
| 1 | `ItemCreated` | At tracker creation; collection **not yet started** | *(none — item enters `Open`)* | → `Open` (initial) | Ph-1 | FR-2, FR-8 |
| 2 | `ItemCreated` | Mid-collection; collection **already active** for this milestone | `StartItemCollection` (idempotent PLM issue creation for new owner × milestone) + `SendInitialOutreach` | `Open` → `OutreachSent` | Ph-1 | FR-3, FR-8, FR-28, FR-29 |
| 3 | `ItemModified` · `OwnerReassigned` | Owner field changed; collection active | `NotifyNewOwner`; `StartItemCollection` for new (owner × milestone) + `SendInitialOutreach` | `Open` → `OutreachSent` (new owner) | Ph-1 | FR-3, FR-9, FR-28, FR-29 |
| 4 | `ItemModified` · `DeadlineMoved` | `expected_completion_date` changed by PM (FR-14) | Re-arm `DeadlineProximity` evaluation immediately | *(no state change)* | Ph-1 | FR-14, FR-28 |
| 5 | `ItemDeleted` | PM removes DeliveryItem via FR-3 | `CancelOutstanding` — cancel all pending reminders and escalations; notify owner of removal | Item removed from tracker | Ph-2 | FR-3, FR-28, FR-29 |
| 6 | `StateChange` | Any `delivery_state` transition occurs | *(observation point — no default action; downstream rules may be scoped here)* | Varies | Ph-1 | FR-28 |
| 7 | `OwnerStatusConfirmed` | Owner reports **"done"** via `Email` [Ph-1] or `CorporateMessenger` [Ph-2]; AND (`item_type = Confirmation` OR `delivery_state ∈ {DocumentReceived, UnderReview}`) | `UpdateState` | → `OwnerClosed` | Ph-1 | FR-7, FR-28 |
| 8 | `OwnerStatusConfirmed` | Owner reports **"done"**; `item_type ≠ Confirmation` AND `delivery_state = OutreachSent` (premature — no document received yet) | `NotifyPM` (`Premature closure attempt` flag on PM dashboard) | *(no state change)* | Ph-1 | FR-7, FR-28 |
| 9 | `OwnerStatusConfirmed` | Owner reports **"delayed"** via status-capable modality; any pre-approval state | `UpdateState` | → `Delayed` (transient; PM or owner can reverse) | Ph-1 | FR-7, FR-28 |
| 10 | `OwnerStatusConfirmed` | Owner reports **"blocked"** via status-capable modality; any pre-approval state | `UpdateState` | → `Blocked` (transient; PM or owner can reverse) | Ph-1 | FR-7, FR-28 |
| 11 | `OwnerStatusConfirmed` | `tracking_modality includes CustomerJIRA`; HILDA poll detects JIRA closure (no owner message required) | `UpdateState` | → `OwnerClosed` | Ph-1 | FR-7, FR-25, FR-28 |
| 12 | `LastContactThreshold` | `days_since_last_contact > N` (N configurable per rule); item not `Closed` | `SendReminder` (via item's status-capable modality) | *(no state change)* | Ph-1 | FR-10, FR-28 |
| 13 | `LastContactThreshold` | `days_since_last_contact > N`; `reminder_count ≥ M` (M configurable) and no owner response (no `last_updated` change after M reminders) | `Escalate` via corp messenger cross-channel; if owner has multiple open items in same milestone, messenger message aggregates all into one | *(no state change)* | Ph-1 | FR-10, FR-28, FR-29 |
| 14 | `DeadlineProximity` | `expected_completion_date - today ≤ N` (N configurable per rule); item not `Closed` | `Escalate` (owner + PM via configured channel) | *(no state change)* | Ph-1 | FR-11, FR-28 |
| 15 | `AttachmentReceived` | `doc_type = test_report`; count of `test_report` rows in document index for this item **< `doc_count`** (more test reports still expected) | `TriggerParser` (FR-16 rule-based parser); `TriggerAIReview` (FR-53 LLM review); document recorded in index | *(no `delivery_state` change)* | Ph-1 | FR-7, FR-16, FR-28, FR-52, FR-53, FR-55 |
| 16 | `AttachmentReceived` | `doc_type = test_report`; count of `test_report` rows in document index **reaches `doc_count`** | `TriggerParser` (FR-16); `TriggerAIReview` (FR-53); `UpdateState` | → `DocumentReceived` | Ph-1 | FR-7, FR-16, FR-28, FR-52, FR-53, FR-55 |
| 17 | `AttachmentReceived` | `doc_type ∈ {waiver, tech_report}` (supplementary documents) | `TriggerAIReview` (FR-53 LLM review); document recorded in index | *(no `delivery_state` change)* | Ph-1 | FR-7, FR-28, FR-53 |
| 18 | `AIReviewResult` | HILDA LLM quality review complete per FR-53 | `UpdateState`; `NotifyPM` (findings surfaced to PM dashboard; `review_status` flag updated on DeliveryItem) | → `UnderReview` | Ph-1 | FR-28, FR-53 |
| 19 | `PMApproval` | PM explicitly approves per-item action; NFR-5 PM-approval gate | `QueueSubmission`; `UpdateState` | → `ReadyForSubmission` | Ph-1 | FR-28, FR-29, NFR-5 |
| 20 | `MilestoneAllClosed` | All DeliveryItems in milestone reach `OwnerClosed` (milestone-level event, not per-item) | `NotifyPM` (FR-22); queue submission assembly (FR-18) | *(per-item state already `OwnerClosed`; no additional per-item change from this trigger)* | Ph-2 | FR-18, FR-22, FR-28 |

### A.2 Direct SharePoint UI actions (not rule-engine triggers)

These are PM/TPM-initiated milestone-level actions that call HILDA directly, bypassing the rule engine. They can fire rule engine events as a side effect (e.g., Start Collection fires `SendInitialOutreach` which fires `ItemCreated` mid-collection for new items, etc.).

| Action | Enabled when | HILDA operations | State transition | Ph | FR refs |
|--------|-------------|------------------|-----------------|----|---------|
| **Start Collection** | Milestone has not yet been kicked off | Create PLM issues per (owner × milestone) (idempotent); `SendInitialOutreach` for all open items per FR-9 | All open items: `Open` → `OutreachSent` | Ph-1 | FR-8, FR-9, FR-56 |
| **Submit to Carrier** | All items in milestone = `ReadyForSubmission` | Assemble submission package from PLM (FR-18); dispatch per customer delivery modality; NFR-5 gate | All items: `ReadyForSubmission` → `SubmittedToCustomer` | Ph-2 | FR-18, FR-63, NFR-5 |
| **Close All Items** | All items in milestone = `SubmittedToCustomer` | Advance all items to `Closed` per FR-22 (NFR-5 confirmation gate); fire `MilestoneAllClosed` | All items: `SubmittedToCustomer` → `Closed`; Milestone → `Complete` | Ph-2 | FR-22, FR-64, NFR-5 |

### A.3 Delivery state machine

Valid `delivery_state` transitions per DeliveryItem:

```
Open
 └─[Start Collection / StartItemCollection]──────────────────────► OutreachSent
                                                                         │
                              ┌──────────────────────────────────────────┤
                              │ OwnerStatusConfirmed "done"               │ AttachmentReceived;
                              │ (Confirmation item only)                  │ doc_count reached
                              ▼                                           ▼
                          OwnerClosed                            DocumentReceived
                              ▲                                           │
                              │ OwnerStatusConfirmed "done"               │ AIReviewResult
                              │ (after DocumentReceived or UnderReview)   ▼
                              └───────────────────────────────────── UnderReview

Delayed  ◄──[OwnerStatusConfirmed "delayed"; any pre-approval state]
Blocked  ◄──[OwnerStatusConfirmed "blocked"; any pre-approval state]
Delayed / Blocked ──[PM or owner reversal]──► (prior active state)

OwnerClosed ──[PMApproval]───────────────────────────────────────► ReadyForSubmission   [Ph-1]
ReadyForSubmission ──[Submit to Carrier (FR-63)]─────────────────► SubmittedToCustomer  [Ph-2]
SubmittedToCustomer ──[Close All Items (FR-64)]──────────────────► Closed               [Ph-2]
```

**Guards:**
- `Confirmation` items (`item_type = Confirmation`): skip `DocumentReceived` and `UnderReview`; `OwnerStatusConfirmed "done"` from `OutreachSent` is valid.
- All other item types: `OwnerStatusConfirmed "done"` from `OutreachSent` is **not** valid — triggers `NotifyPM` (`Premature closure attempt`) instead.
- `CustomerJIRA` items: reach `OwnerClosed` via HILDA detecting JIRA closure; no owner message required.
- `Delayed` and `Blocked` are transient overlay states — the item re-enters its prior active state when the PM or owner reverses.

---

## Appendix B — FRs Requiring LLM Calls

> **Constraint (applies to every row):** All LLM calls — runtime and build/onboarding-time — must use on-premises open-source models. No public-cloud or SaaS LLM calls in any phase (NFR-1, `[D-007]`).

Two categories of LLM use exist in HILDA: **runtime LLM** (called during live pipeline execution) and **build/onboarding-time LLM** (called once per customer or adapter onboarding to generate code/config artifacts; not called during normal operation).

### B.1 Runtime LLM — Phase 1

| FR | LLM role | Inputs | Output / effect | Notes |
|----|----------|--------|-----------------|-------|
| **FR-12** path (c) | Free-text email reply classification | Email body text + `BATCH-<id>` DeliveryItem batch context | Item assignment, status extraction (`delivery_state` update) | LLM is fallback — rule-based parsing (path a) runs first; LLM fires only when rule-based confidence is below threshold; when the email also contains attachments, FR-52 document routing is **fused** into this same LLM call (one pass, shared context) — anchors `[D-034]` |
| **FR-52** tier 2 | Document-to-item routing | Filename + first-page text excerpt + DeliveryItem batch list | `(delivery_item_id, doc_type)` mapping | LLM is fallback — fuzzy match (tier 1) runs first; LLM fires only when fuzzy match is inconclusive; not invoked when fused into FR-12 path (c) call |
| **FR-53** | Document quality review | Document content (test report / tech report / waiver) + per-customer quality checklist generated by the Test Report Profiler `[D-011]` | `llm_review_findings` written to document index row; `review_status` flag updated on DeliveryItem | Fires for all three `doc_type` values on every ingest channel (email FR-52, NSD FR-55, PLM poll FR-26, SP UI upload FR-62); findings surfaced on PM dashboard |

### B.2 Runtime LLM — Phase 2 additions

| FR | LLM role | Inputs | Output / effect | Notes |
|----|----------|--------|-----------------|-------|
| **FR-17** | Revision document quality re-review | Revised document (revN) + same per-customer quality checklist | Updated `llm_review_findings` written to document index row for `rev_number = N` | Prior revision findings are preserved in their own rows and never overwritten; invoked for all three revision sub-paths (email, NSD, PLM) |
| **FR-54** | Inbound messenger message classification | Inbound messenger text + owner's open DeliveryItems across milestones | Status (`OwnerClosed / Delayed / Blocked`) + reason; auto-applied above confidence threshold | Below-threshold cases go to PM dashboard as `Messenger reply — Manual triage`; flags grouped by milestone when owner has items across multiple milestones |

### B.3 Build / onboarding-time LLM

These LLM calls run **once per onboarding event** (new API spec, new customer template, new test-report corpus). They produce code/config artifacts committed to `customizations/`. Normal pipeline operation does not call these LLMs.

| Module | LLM role | Inputs | Output artifacts | Phase introduced | FR / decision refs |
|--------|----------|--------|-----------------|------------------|--------------------|
| **API Spec Ingestor** | Adapter code generation | Proprietary REST API spec (format TBD per `[D-015]`) | IssueTracker / Messenger Protocol adapter module under `customizations/<system>/<vendor>_adapter.py` | Ph-1 (corp PLM adapter + proprietary messenger adapter are v1 first exercises) | `[D-003]`, `[D-008]`, `[D-009]`, FR-25, FR-50 |
| **Template Schema Ingestor** | Schema artifact generation | Customer Excel template schema | Per-customer Pydantic validators, Excel parsers, SharePoint List column mappings, AutomationRules under `customizations/template_schemas/<customer>/` | Ph-2 (deferred alongside FR-1 path b, DEF-15) | `[D-010]`, FR-1, FR-39 |
| **Test Report Document Profiler** | Parser and checklist generation | Historical test report corpus (per-customer) | Per-customer test-report parser (consumed by FR-16), per-customer quality checklist (consumed by FR-53) | Ph-1 (parser + checklist are pre-requisites for FR-16 and FR-53) | `[D-011]`, FR-16, FR-46, FR-53 |

---

## Appendix C — Phase Scope Index

FRs that span both phases appear in both sections with the column **"In-phase scope"** describing what is active in that phase only. NFRs are architectural invariants that apply from Phase 1 onward and are listed once at the end.

### C.1 Phase 1 items

| FR | Title | In-phase scope |
|----|-------|----------------|
| **FR-1** | Tracker creation | Path (a) only: create tracker from customer template |
| **FR-2** | Tracker auto-population | Full: Milestone → DeliveryItem hierarchy, all static fields, `expected_completion_date`, `doc_count`; `plm_id` deferred to FR-8 |
| **FR-3** | Post-creation item management | Full: PM adds, removes, or reassigns DeliveryItems after tracker creation |
| **FR-5** | Hierarchy enforcement | Full: uniqueness constraints, PLM issue per (owner × milestone) |
| **FR-6** | Milestone status computation | Full: status and `completion_pct` computed from child item states |
| **FR-7** | Item types, modalities, states, `doc_count`, `doc_type` | Full enumeration and business rules defined; `CorporateMessenger` modality is Ph-1 for escalation only (FR-10) — not for initial outreach (Ph-2) |
| **FR-8** | Start Collection kickoff | Full: one PLM issue per (owner × milestone); initial outreach for all open items; idempotent |
| **FR-9** | Initial owner outreach | Email, CorporatePLM, NSD, CustomerJIRA modality outreach; `OutreachSent` state advance |
| **FR-10** | Reminders and escalation | Full: scheduled reminders; cross-channel escalation via corp messenger when email reminders yield no response |
| **FR-11** | Deadline escalation | Full: escalation to owner + PM when deadline proximity threshold crossed |
| **FR-12** | Inbound email routing | Paths (a) structured block and (c) free-text + LLM fallback + fused attachment call; sender attribution; `Sender mismatch` flag |
| **FR-13** | Shared drive + document ingest write flows | Full Ph-1: `inbound/`, `outbound/`, `<doc_type_slug>/<doc_id_slug>/rev1/` areas; email, PLM poll, NSD ingest write flows; PLM as source of truth; HILDA-mediated download links |
| **FR-14** | PM manual overrides | Full: PM overrides dates, owners, comments, `delivery_state`; ad-hoc reminder trigger |
| **FR-15** | Contact + update timestamps | Full: `last_owner_contacted` and `last_updated` update on every state change |
| **FR-16** | Test-report parser trigger | Full: invoked by `TriggerParser` after `doc_type = test_report`; per-item status grid; `final \| interim` classification |
| **FR-23** | Email Service mailbox | Full: dedicated mailbox, 24/7 inbound poll/push, outbound on behalf of PM |
| **FR-24** | Email structured reference tag | Full: tag in outbound subject; tag parsing + sender capture on inbound |
| **FR-25** | IssueTracker Protocol | Full: corp PLM adapter (document storage); CustomerJIRA adapter (closure polling); both via same Protocol surface |
| **FR-26** | PLM polling | Base: PLM issue creation; HILDA polls for new documents; routes via FR-52; writes to NSD + document index |
| **FR-28** | Rule engine triggers | `ItemCreated`, `ItemModified` (both sub-triggers), `StateChange`, `OwnerStatusConfirmed` (all outcomes), `LastContactThreshold`, `DeadlineProximity`, `AttachmentReceived`, `AIReviewResult`, `PMApproval` |
| **FR-29** | Rule actions | `SendReminder`, `Escalate`, `UpdateState`, `StartItemCollection`, `SendInitialOutreach`, `NotifyNewOwner`, `TriggerParser`, `TriggerAIReview`, `QueueSubmission`, `NotifyPM` |
| **FR-30** | Rule scoping | Full: Global / Customer / Device scope |
| **FR-31** | PM rule control | Full: pause, customize, or manually trigger any rule action |
| **FR-39** | Template authoring | Path (a) only: SharePoint UI live editing via classic web-part forms |
| **FR-40** | Template structure and versioning | Full: milestones + DeliveryItems grouped by `tg_name`; `template_version` |
| **FR-41** | Three-tier configuration | Full: Global / Customer / Device overrides without code change |
| **FR-42** | CommunicationLog audit | Full: every external action logged with PM attribution, target system, action type, DeliveryItem reference |
| **FR-46** | Final / interim classification | Full: deterministic rule — `final` iff all items passed/waived; otherwise `interim` |
| **FR-49** | Diagnostic CLI mode | Full: `--diagnostic` mode per module emitting compact RPT report |
| **FR-50** | Messenger adapter | Base: Slack adapter + proprietary internal messenger adapter; `send / receive / list_thread` surface; status-only (no attachments) |
| **FR-51** | Credential service | Full v1: sops-encrypted `.env` files; `get_credential(pm_id, system_type)`; decrypted at startup |
| **FR-52** | Document routing | Tier-1 fuzzy match + tier-2 LLM routing; fused email path; PLM no-re-upload; `DocumentReceived` state advance + FR-53 trigger |
| **FR-53** | LLM document quality review | Full: initial quality review for all doc_types; `llm_review_findings` written to document index; PM dashboard findings display |
| **FR-55** | NSD `inbound/` monitoring | Base: poll `inbound/` folder; classify `doc_type`; upload to PLM; write to NSD classified path; document index update; `DocumentReceived` advance; FR-53 trigger |
| **FR-56** | SharePoint milestone view | Milestone view web part: item list grouped by `tg_name`; per-row fields; **Start Collection** action |
| **FR-57** | `actual_item_info` + document enumeration API | `actual_item_info` = PLM issue URL; enumeration API returning latest revision per (doc_type, doc_id_slug) |
| **FR-58** | `item_type` discriminator | Full: `Confirmation` items have no document section; all others have full document pipeline |
| **FR-59** | SharePoint document section | Per-item document rows (doc_type, doc_id_slug, rev_number, original_filename, download link, View in PLM link); single revision per document in Ph-1 |
| **FR-60** | Review results display | Inline latest-revision `parser_result` + `llm_review_findings` per document row |
| **FR-61** | HILDA-mediated document download | Download link authenticated via on-prem AD; resolves to single available version |

### C.2 Phase 2 items

Items marked **(extends Ph-1)** add to an existing FR; items with no such mark are entirely new in Ph-2.

| FR | Title | In-phase scope |
|----|-------|----------------|
| **FR-1** | Tracker creation *(extends Ph-1)* | Paths (b) Excel import (requires Template Schema Ingestor DEF-15) and (c) manual entry |
| **FR-9** | Initial owner outreach *(extends Ph-1)* | `CorporateMessenger` modality outreach added; per-item `mailto:` tap-links added to Email outreach body |
| **FR-12** | Inbound email routing *(extends Ph-1)* | Path (b): `mailto:` tap-link emails parsed from subject (`[HILDA] BATCH-<id> ITEM-<n> <STATUS>`) |
| **FR-13** | Shared drive + document ingest *(extends Ph-1)* | `revN/` (N ≥ 2) subsequent revision storage |
| **FR-17** | Revision document handling | All three revision sub-paths: (a) email revision via FR-52 two-tier; (b) owner writes directly to `revN/` on NSD; (c) PLM (file_name, timestamp) change detection; re-parse, re-review, SharePoint update |
| **FR-18** | Submission package assembly and dispatch | Resolve latest document per item from PLM via document index; assemble; dispatch per customer delivery modality; triggered by Submit to Carrier (FR-63) |
| **FR-19** | Customer adapter surface | `{submitItem, getStatus, postComment, uploadAttachment}`; PM credential authentication |
| **FR-20** | Credential-expired submission queue | Block and queue submission; PM dashboard alert + out-of-band notification on credential missing/expired |
| **FR-21** | Customer feedback capture | Capture feedback from customer tracking system + email; surface on PM dashboard with source + timestamp |
| **FR-22** | `Closed` state transition | Customer approval + PM confirmation (via Close All Items FR-64); Milestone → `Complete` when all items `Closed` |
| **FR-26** | PLM polling *(extends Ph-1)* | Per-PLM-issue (file_name, timestamp) index persisted across poll cycles; new timestamp for existing file_name → revision path (FR-17c); PLM auto-upload extended to `revN/` writes |
| **FR-27** | Customer adapter registration | New customer adapter added via configuration (AutomationRules + per-customer config); no code change in `core/` |
| **FR-28** | Rule engine triggers *(extends Ph-1)* | `ItemDeleted` trigger; `CorporateMessenger` as additional sub-trigger for `OwnerStatusConfirmed`; `MilestoneAllClosed` milestone-level trigger |
| **FR-29** | Rule actions *(extends Ph-1)* | `CancelOutstanding` action (cancel pending reminders/escalations for removed item; notify owner) |
| **FR-39** | Template authoring *(extends Ph-1)* | Path (b): Excel upload conforming to schema generated by Template Schema Ingestor `[D-010]` |
| **FR-47** | Failed-item surface on PM dashboard | Surface every `failed` item without `waiver_ref` from a test report for PM resolution-path determination |
| **FR-48** | Waiver DeliveryItem auto-create | PM selects `waiver` resolution path → system auto-creates a Waiver DeliveryItem with its own lifecycle |
| **FR-52** | Document routing *(extends Ph-1)* | Revision classification: duplicate detection (file hash per `[D-039]`), new-vs-revision logic, ambiguous staging, PM dashboard flag |
| **FR-54** | Inbound messenger message classification | Capture inbound messenger messages; LLM classifies status + reason; auto-apply above threshold; PM triage below |
| **FR-55** | NSD monitoring *(extends Ph-1)* | Monitor `<doc_type_slug>/<doc_id_slug>/revN/` folders in addition to `inbound/`; new file detected → revision path per FR-17(b) |
| **FR-56** | SharePoint milestone view *(extends Ph-1)* | Per-row **Close Item** action enabling the item's assigned owner to self-close from the SP UI |
| **FR-57** | Document enumeration API *(extends Ph-1)* | `all_revisions=true` query parameter returning all revision rows per (doc_type, doc_id_slug) ordered by `rev_number` ascending |
| **FR-59** | SharePoint document section *(extends Ph-1)* | When multiple revisions exist, only latest revision shown in list view |
| **FR-60** | Review results display *(extends Ph-1)* | Expandable document row showing all revisions with individual `parser_result` and `llm_review_findings` per revision |
| **FR-61** | Document download *(extends Ph-1)* | Download resolves to latest revision (by `upload_timestamp` in document index) |
| **FR-62** | SharePoint document upload surface | PM/TPM uploads document directly in SP UI; item + `doc_type` specified explicitly; HILDA uploads to PLM + document index update + FR-53 trigger |
| **FR-63** | Submit to Carrier SP action | Milestone-scoped; enabled when all items = `ReadyForSubmission`; triggers FR-18; NFR-5 gate; → `SubmittedToCustomer` |
| **FR-64** | Close All Items SP action | Milestone-scoped; enabled when all items = `SubmittedToCustomer`; NFR-5 confirmation gate; → `Closed`; fires `MilestoneAllClosed` |

### C.3 Non-functional requirements (all phases)

NFRs are architectural invariants active from Phase 1 onward. None are phase-gated.

| NFR | Area | Summary |
|-----|------|---------|
| **NFR-1** | Data boundary | All HILDA services on-prem; no cloud or SaaS LLM calls |
| **NFR-2** | Data boundary | Compact reports / logs leaving on-prem contain no proprietary content |
| **NFR-3** | Credential security | Per-PM credential isolation; no cross-PM access |
| **NFR-4** | Credential security | sops AES-256-GCM encryption at rest; TLS in transit; mTLS deferred to v2 |
| **NFR-5** | PM approval | No customer-facing outbound without explicit PM-approval signal recorded in `CommunicationLog` |
| **NFR-6** | PM accountability | Every external action attributable to a specific PM; `CommunicationLog` append-only |
| **NFR-7** | SharePoint | Deployment-specific SP values in `customizations/sharepoint_config/` only; none in `core/` |
| **NFR-8** | SharePoint | SharePoint REST API + on-prem AD auth (NTLM / Kerberos) against SP 2017; List CRUD + classic web parts only |
| **NFR-9** | Latency | State changes propagate to PM dashboard in < 60 s end-to-end |
| **NFR-10** | Reliability | Email Service polls 24/7; transient failures use exponential backoff; no silent failures |
| **NFR-11** | Reliability | Expired / missing PM credential → step queued (not lost) + PM alerted |
| **NFR-12** | Adapter / build boundary | Dev LLM has no access to proprietary API specs, template schemas, or historical test reports |
| **NFR-13** | Adapter / build boundary | Generated proprietary artifacts under `customizations/` only; `core/` contains Protocols, Ingestors, public-vendor adapters |
| **NFR-14** | Extensibility | New item types, modalities, states extensible via configuration; no schema migrations |
| **NFR-15** | High availability | `restart: unless-stopped` in Docker Compose v1; HA replicas deferred to v2 K8s |
| **NFR-16** | NSD boundary | NSD reads via HILDA-mediated download endpoint only; direct UNC paths never exposed to PMs |
| **NFR-17** | Chat-mediated collaboration | All module RPT / MET / FIX / QC reports contain only counts, flags, bounded enum tokens — no proprietary content |
| **NFR-18** | Chat-mediated collaboration | All failures raise registered error codes from central `error_codes.py` in format `{MODULE}-{E\|W}{NNN}` |
| **NFR-19** | Test interface | Every side-effect module ships `--mock` / `--dry-run`; every UI module ships mock web harness without production SP access |
