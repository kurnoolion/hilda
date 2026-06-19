# SP UI Specifications — Buttons + Column Reads

**Audience**: SP UI engineer implementing the SharePoint web parts.
**Purpose**: SP UI engineer's two-direction contract with HILDA — Part 1 specifies every clickable WRITE action (buttons + field-write contracts that fire SP-alerts to HILDA); Part 2 specifies every READ path (which columns SP UI engineer's web part reads from `Deliverables_<customer_id>` to drive display, button visibility/enablement, and PM dashboard surfaces).
**Authority**: this file + `SP_lists_authoritative.xlsx` are the two SP UI engineer deliverables of authority.

**File organization**:
- **Part 1 — Write Actions** (buttons + per-row + rule control panel): every button click is a field write that fires an SP-alert.
- **Part 2 — Read Paths** (display + UI gating + dashboard surfaces): every column the web part reads from SP and what it drives in the UI.
- **Out-of-scope clarifications** (FR-87 buttons, FR-62 form, HILDA dashboard rendering — owned by HILDA).
- **SP-alert configuration requirements** (Alert trigger config that makes Part 1 work).

---

## Common conventions

**Field-write pattern.** Every button click is a **SP-side field write** on a SP list row. The act of writing the field is what fires the SP-alert that wakes HILDA — no direct HTTP call to HILDA from the SP web part (corp firewall blocks it per FR-84). HILDA learns of every click via the resulting alert email.

**Milestone-scoped writes touch every work-item row in the milestone.** Milestone-level actions (Start Collection / Submit to Carrier / Close All Items / Refresh / Download Package) write the milestone-level column (e.g., `milestone_collection_started_at`) to **every work-item row** in the milestone within `Deliverables_<customer_id>` — N rows × 1 column write in a single SP transaction. Each per-row write fires an SP-alert; HILDA processes the first alert in the burst and deduplicates the rest by detecting the same `(customer_id, MinorMilestone, column_name)` tuple across the burst (per FR-11 / `[D-082]` cascade-dedup pattern). Per-DeliveryItem actions write to the individual DI row only.

**Atomic 3-field write for Approve.** The Approve button writes 3 fields (`delivery_state` + `pm_approval_at` + `pm_approval_pm_id`) in a **single SP transaction**. SP web part code must enforce atomicity; partial writes are a defect.

**No direct HTTP to HILDA.** All HILDA-side processing happens via the SP-alert email channel (`[D-047]` / FR-84). The SP web part never calls `hilda-api` or `hilda-proxy.corp` directly except via top-level browser navigation for link-out anchors (View Documents / View in PLM / Upload Document).

**Live polling.** SP UI polls the SP list for row state changes (focus-aware refresh per `[D-064]` 2026-06-10 SP UI engineer discipline). State updates pushed by HILDA via `[D-064]` REST writeback become visible to TPM after the next focus refresh.

**Role-based control.** Field-level role restrictions are SP web part responsibility. Where this spec says "TPM editable" or "ops only", the web part enforces. HILDA does not enforce role permissions — it trusts the SP write.

**Atomicity for SP-side cascades.** Where this spec says SP-side cascade (e.g., Start Collection writes `milestone_collection_started_at` AND no other field; Approve writes 3 fields atomically), the web part executes all writes in one SP transaction OR fires all alerts only after all writes succeed.

---

# PART 1 — WRITE ACTIONS (Buttons + field writes)

All write actions in this Part trigger SP-alerts to HILDA per `[D-047]` / FR-84. SP UI engineer's web part executes the field writes; HILDA reads the resulting SP-alert and applies downstream effects.

---

## Milestone View — top-bar buttons

### Start Collection (Need NOW)

| Field | Value |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | `milestone.milestone_collection_started_at` is empty AND `milestone.status` ∈ {Not Started, In Progress} |
| **User prompt** | "Start collection for milestone `<name>`? This will send initial outreach to all R&D owners." (Yes / Cancel) |
| **What happens on click** | Set `milestone_collection_started_at = <now>` on **every work-item row** in the milestone (within `Deliverables_<customer_id>`, filter `model + minorMilestone`). Single SP transaction batching N row updates. |
| **What HILDA does next** | *(Background — out of your scope)* HILDA receives N SP-alerts, deduplicates to one via `(customer_id, minorMilestone, milestone_collection_started_at)` burst detection, transitions each DI from `Not Started` → `Open`, creates PLM issues per (owner × milestone), fires email outreach to all owners, transitions each DI to `OutreachSent`, activates runtime polling channels (Email/NSD/PLM/CustomerJIRA per per-item `tracking_modality`), and sets `Milestone.status = "In Progress"` (writes to every row in the milestone). Item rows update via live polling. |
| **Idempotency** | Re-click on an already-started milestone is safe — HILDA detects existing `plm_id` and skips duplicate creation; only DIs still in `Open` get re-fired outreach. |
| **FR refs** | FR-8, FR-56 (e), FR-84 (N-row write pattern), `[D-082]` (cascade dedup) |

### Submit to Carrier  (Need NOW)

| Field | Value |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | All `is_milestone_gating = true` DeliveryItems in milestone are in `{ReadyForSubmission, SubmittedToCustomer}` AND at least one `is_milestone_gating = true` DI is in `ReadyForSubmission` (per FR-63 gating-aware lock 2026-06-17). **Items excluded from the enablement check** regardless of `is_milestone_gating`: (a) default work-item per FR-78 (`item_type = Default`); (b) items with `no_customer_upload = true` per FR-80; (c) `item_type = Confirmation` per FR-7 — all three have no carrier-submission path. **Also disabled when** any `is_milestone_gating = true` item is in `UnderPMReview` (FR-62 upload-induced revert blocker — PM must re-approve per FR-56 (e) before next Submit). Disabled-state hover tooltip: *"Submit blocked: `<N>` gating items in `UnderPMReview` — PM must re-approve per FR-56 (e). Items: `<list>`"* |
| **User prompt** | **Initial submission** (no items in `SubmittedToCustomer` at click time): "Submit `<N>` items to `<carrier_name>`. Package contents: `<N>` `ReadyForSubmission` items dispatched as individual files per FR-18 (zip never uploaded per `[D-054]`); LLM review attestations stay in HILDA (audit trail per FR-53 / FR-60); no milestone-level cover sheet is generated. This action is irreversible from HILDA's side — any subsequent upload requires re-approval and re-submission per FR-62 revert pattern." (Yes / Cancel) — `<N>` = count of `ReadyForSubmission` gating + non-gating items being dispatched. **Re-submission** (one or more items already in `SubmittedToCustomer`): "Re-submitting `<N>` items with updated documents. `<M>` previously-submitted items are unchanged and excluded from this package. Proceed?" (Yes / Cancel). **Default-work-item soft warn (FR-63 Approach C lock 2026-06-17)**: if `DocumentItemAssociation` count > 0 for the default work-item's `delivery_item_id`, additionally surface non-blocking advisory: *"⚠ `<K>` documents are currently in the unrouted bucket (default work-item per FR-78) — confirm you've triaged any docs intended for this milestone via FR-87; remaining docs may be junk/noise and can be triaged later or left for FR-78 Mark Closed cleanup. They will NOT be included in this submission package."* — does NOT block confirm; PM/TPM may still proceed. |
| **What happens on click** | Set `milestone_submission_triggered_at = <now>` on **every work-item row** in the milestone (N-row write; HILDA deduplicates per `[D-082]`). |
| **What HILDA does next** | *(Background — out of your scope)* HILDA assembles the submission package from `ReadyForSubmission` items only (`SubmittedToCustomer` items are skipped on re-submission), dispatches via the customer adapter (Google Drive browser automation Ph-1/Ph-2 per FR-19), transitions dispatched items to `SubmittedToCustomer`, logs to `CommunicationLog` with `action_type=submission` (initial) or `resubmission` (delta). Item rows update via live polling. |
| **Idempotency** | If a previous Submit attempt failed mid-dispatch, re-clicking is safe — HILDA's idempotency check on `(milestone_id, dispatch_run_id)` prevents duplicate carrier upload. |
| **FR refs** | FR-18, FR-19, FR-63, FR-56 (e) |

### Close All Items  (Need NOW)

| Field | Value |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | All `is_milestone_gating=true` DIs are in `{SubmittedToCustomer, Closed}` AND at least one `is_milestone_gating=true` DI is in `SubmittedToCustomer`. **Non-gating items** (`is_milestone_gating=false`) may remain in any non-blocking state at click time. |
| **User prompt** | "Close All Items will set the milestone state machine to terminal: (a) `<N_part1>` `SubmittedToCustomer` items will be set to `Closed` (gating cascade); (b) `<N_part2>` non-gating items in earlier states will be auto-advanced to `Closed` (state-machine consistency cascade); (c) the default work-item must still be Marked Closed separately per FR-78 to trigger `MilestoneAllClosed`. Closing all items will permanently delete NSD storage for this milestone (per FR-76 cleanup) — download any needed documents before proceeding. Document download links (FR-61) will return `DSH-E003` after cleanup. Only close items after the carrier has confirmed technical acceptance of the milestone submission. This action is irreversible." (Yes / Cancel) |
| **What happens on click** | Set `closed_all_items_triggered_at = <now>` on **every work-item row** in the milestone (N-row write; HILDA deduplicates per `[D-082]`). |
| **What HILDA does next** | *(Background — out of your scope)* HILDA performs a two-part cascade per FR-64: (1) sets all `SubmittedToCustomer` items to `Closed`; (2) auto-advances any `is_milestone_gating=false` items in non-Closed states to `Closed` (action_type=`bulk_close_non_gating`). When all gating items + default work-item reach `Closed`, FR-76 `MilestoneStorageCleanup` fires — NSD `internal/<milestone>/` subtree is deleted permanently. Item rows update via live polling. |
| **Idempotency** | Re-click is no-op once all items are `Closed`. |
| **FR refs** | FR-28 (`MilestoneAllClosed`), FR-64, FR-76, FR-56 (e) |

### Refresh (DONT NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | Always |
| **User prompt** | None (immediate action). |
| **What happens on click** | Set `refresh_requested_at = <now>` on **every work-item row** in the milestone (N-row write; HILDA deduplicates per `[D-082]`). |
| **What HILDA does next** | *(Background — out of your scope)* HILDA performs a soft-poll across Email/PLM/NSD ingest channels for the milestone, checking for new documents since the last poll. Rate-limited HILDA-side (default 5 min per milestone). If a soft-poll is already in progress, the alert is acknowledged with no new task. After poll, HILDA writes back SP updates per `[D-064]`. Item rows update via live polling. |
| **Idempotency** | Rate limit prevents over-firing. |
| **FR refs** | FR-56 (f) |

### Download Package (DONT NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | Same as Submit to Carrier: all DIs in `{ReadyForSubmission, SubmittedToCustomer}` AND ≥1 in `ReadyForSubmission` |
| **User prompt** | "Download submission package for milestone `<name>`? HILDA will prepare the zip and notify when ready (~30 s typical)." (Yes / Cancel) |
| **What happens on click** | (a) Set `download_package_request_timestamp = <now>` on **every work-item row** in the milestone (N-row write; HILDA deduplicates per `[D-082]`); (b) immediately update SP UI to show "Package preparing..." indicator (replaces the button); (c) clear `download_package_url`, `download_package_status`, `download_package_generated_at` on every work-item row in the milestone (fresh assembly per click). |
| **What HILDA does next** | *(Background — out of your scope)* HILDA assembles the zip in background per FR-73 (individual files only per FR-77 Type-2 routing — no audit zips). On completion, HILDA writes back to **every work-item row in the milestone**: `download_package_url = https://hilda-proxy.corp/dl/<scoped_token>`, `download_package_status = "ready"`, `download_package_generated_at = <now>`. On failure: `download_package_status = "failed"` (on every row) + `HildaOpsAlert` logged. |
| **Live polling on completion** | SP web part's focus-aware refresh re-reads any work-item row in the milestone (all carry identical milestone-level values); when `download_package_status = "ready"` is observed, the "Preparing..." indicator is replaced with a clickable **Download Now** link (the `download_package_url`). |
| **Idempotency** | Per-click regeneration — each click triggers fresh assembly; previous cached zip is discarded. |
| **FR refs** | FR-73, FR-56 (e) |

---

## Per-row buttons — DeliveryItem level (DI row writes)

### Approve (NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View |
| **Enabled when** | `delivery_state = UnderPMReview` |
| **User prompt** | "Approve item `<item_name>` for submission to carrier?" (Yes / Cancel) |
| **What happens on click** | **Atomic 3-field write to the DI row in ONE SP transaction**: `delivery_state = "ReadyForSubmission"` + `pm_approval_at = <now>` + `pm_approval_pm_id = <current PM/TPM AD account>`. Partial writes are a defect — all 3 or none. |
| **What HILDA does next** | *(Background — out of your scope)* HILDA receives the SP-alert, reads the 3 fields, logs to `CommunicationLog` with `action_type=pm_approval`, fires FR-28 `PMApproval` trigger downstream (queues for FR-18 submission per rule_engine). HILDA does NOT re-write any of the 3 fields — they are SP-canonical per `[D-068]` impl note 2026-06-12. |
| **Revert path** | If TPM uploads a new document via FR-62 [Ph-2] on a `ReadyForSubmission` **OR `SubmittedToCustomer`** item, the web part must atomically revert `delivery_state = "UnderPMReview"` + clear `pm_approval_at` + clear `pm_approval_pm_id` in one SP transaction (per FR-62 revert pattern). Both upload-from-states map to the same `UnderPMReview` revert target — prior PM approval is stale (new doc content changes what was approved) for `ReadyForSubmission`; prior submission is stale (TPM must re-review and re-approve before re-submission) for `SubmittedToCustomer`. |
| **FR refs** | FR-28 (`PMApproval`), FR-56 (c), FR-7 state machine, `[D-068]` |

### Send Reminder (NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View |
| **Enabled when** | `delivery_state ∉ {OwnerClosed, ReadyForSubmission, SubmittedToCustomer, Closed}` AND `item_type ≠ Default` (suppress button on the default work-item row per FR-78 — no owner to remind; system-reserved TG `_unrouted`) |
| **User prompt** | None (immediate action). Optional: "Send reminder to `<owner_name>`?" (Yes / Cancel) — implementation choice. |
| **What happens on click** | Set `DeliveryItems.last_reminder_triggered_at = <now>` on the DI row. Editor column captures PM-or-TPM identity (SP-side attribution). **You do NOT write side-effect columns** — `last_owner_contacted`, `reminder_count`, `delivery_state` Open→OutreachSent transition are all HILDA-managed via `[D-064]` REST writeback (parallel to FR-12 / FR-14 / FR-15-extended write-ownership pattern). |
| **What HILDA does next** | *(Background — out of your scope)* HILDA receives SP-alert per `[D-047]` + FR-84, dispatches reminder to owner **via email only** (PLM is not a messaging platform per FR-65 modality lock 2026-06-17 — HILDA uses PLM for tracking/issue management only, not owner messaging; NSD/SPUI are ingest-only; CorpMessenger reserved for FR-11 escalations). Email recipients per FR-9 preference rule using `owner_corp_usa_email` / `owner_corp_email` (per `[D-080]`) + `tg_email_group_alias` if set. On dispatch success, HILDA writes back via `[D-064]` REST: (a) `last_owner_contacted = <now>`; (b) `reminder_count += 1`; (c) if item was in `Open`, advances `delivery_state` to `OutreachSent` atomically. `last_owner_response_at` + `manual_triage_required` + `prior_delivery_state` unchanged. Cadence-reset: HILDA reschedules next FR-10 automated reminder relative to this manual send timestamp. On dispatch failure, HILDA logs to `HildaOpsAlert` per FR-75; side-effect columns stay unchanged; HILDA may retry per NFR-10 backoff. **Delayed/Blocked**: dispatch fires but item does NOT auto-exit Delayed/Blocked (use Resume from Delayed/Blocked button); `last_owner_contacted` updates but FR-10 cadence stays paused per FR-12. |
| **Idempotency** | Re-click re-sends the reminder (intentional — TPM ad-hoc trigger per FR-14 / FR-65); each click increments `reminder_count`. |
| **FR refs** | FR-9, FR-10, FR-12, FR-14, FR-15, FR-15-extended, FR-65, FR-78, `[D-064]`, `[D-080]` |

### Mark Closed (manual close path) (NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View. Visible on: (a) the default work-item per FR-78; (b) any item with `no_customer_upload = true` per FR-80. NOT visible on other items (they reach `Closed` only via Submit to Carrier + Close All Items). |
| **Enabled when** | **For default work-item (FR-78)**: no remaining documents associated via `DocumentItemAssociation` (i.e., all unrouted docs have been reassigned to real items via FR-83). **For `no_customer_upload=true` items (FR-80)**: standard `OwnerClosed` guards met — `delivery_state = OwnerClosed` AND any subsequent state, but not yet `Closed`. |
| **User prompt** | "Mark `<item_name>` as Closed? This action is irrevocable." (Yes / Cancel) |
| **What happens on click** | Set `DeliveryItems.delivery_state = "Closed"` on the DI row. (HILDA enforces the guard server-side via the SP-alert; web part SHOULD do client-side gating but HILDA is the ultimate enforcer.) |
| **What HILDA does next** | *(Background — out of your scope)* HILDA receives SP-alert, verifies guards. If gate passes: logs to `CommunicationLog` with `action_type=tpm_mark_closed`. If the closed item is the default work-item AND all gating items are also `Closed`, fires `MilestoneAllClosed` (FR-28) → `MilestoneStorageCleanup` (FR-76). If guard fails: HILDA reverts `delivery_state` to the prior value via `[D-064]` and logs a guard-failure dashboard error to surface in next focus refresh (concrete error code `DSH-EXXX` pending assignment at architecture phase + dashboard MODULE.md error catalogue alignment — TODO 2026-06-17). |
| **Idempotency** | Already-`Closed` items have the button hidden. |
| **FR refs** | FR-7, FR-78, FR-80, FR-14 |

### Upload Document `[Ph-2]` (DO NOT NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View |
| **Enabled when** | `item_type ≠ Confirmation` AND `item_type ≠ Default` (default work-item per FR-78 is not a TPM upload target — it absorbs failed-routing docs from natural ingest channels only) AND (`delivery_state ∈ {DocumentReceived, UnderPMReview, ReadyForSubmission, SubmittedToCustomer}` OR `delivery_state = Open` AND (effective `tracking_enabled = false` per FR-81 no-tracking TG fallback OR `tracking_modality` includes `SPUI` per FR-9 silent-outreach rule — SPUI items have no HILDA-initiated outreach; FR-62 is the TPM-side upload path)) |
| **User prompt** | None (button is a link-out anchor; navigation, not click-action) |
| **What happens on click** | Browser opens a new tab navigating to `https://hilda-proxy.corp/upload/<customer_id>/<item_id>` where `<customer_id>` is the row's `customer_id` column value (selects the SP list) and `<item_id>` is the SP system `ID` column (auto-Counter PK; per-list unique). Built as: `<uploadUrlPrefix>/<customer_id>/<item_id>` where `uploadUrlPrefix` is set as a SP web part property at deployment (e.g., `https://hilda-proxy.corp/upload`). HILDA's dashboard resolves the target row via `Deliverables_<customer_id>` `GetItemById(<item_id>)` per FR-5. No SP field write. |
| **What HILDA does next** | *(Background — handled by HILDA's dashboard, not SP)* HILDA's dashboard renders an upload form; TPM submits via same-origin form POST; HILDA writes the file to NSD + document index per FR-62. After successful upload, if `delivery_state ∈ {ReadyForSubmission, SubmittedToCustomer}`, HILDA reverts to `UnderPMReview` and clears `pm_approval_at`/`pm_approval_pm_id` per the revert pattern (see Approve button → Revert path). State updates push back to SP via `[D-064]`; visible on next focus refresh. |
| **FR refs** | FR-62, `[D-074]` |

### View Documents (NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Per-row link on each DeliveryItem in Milestone View |
| **Enabled when** | `item_type ≠ Confirmation` |
| **User prompt** | None (navigation link) |
| **What happens on click** | Browser opens a new tab navigating to `<documentsUrlPrefix>/<customer_id>/<item_id>` where `<customer_id>` is the row's `customer_id` column value (selects the SP list) and `<item_id>` is the SP system `ID` column (auto-Counter PK; per-list unique — NOT globally unique across `Deliverables_<customer_id>` lists per FR-5). Example: `https://hilda-proxy.corp/docs/MMK/12345`. Prefix is a SP web part property set at deployment. HILDA's dashboard resolves the target row via `Deliverables_<customer_id>` `GetItemById(<item_id>)` per FR-5. No SP field write. |
| **What HILDA does next** | *(Background — HILDA dashboard renders)* HILDA's dashboard renders the document section as server-side HTML per FR-57/FR-59/FR-60. TPM stays in the HILDA tab for FR-87 TPM-resolution buttons (handled in HILDA tab, NOT SP UI). |
| **FR refs** | FR-57, FR-59, FR-60, FR-61, `[D-074]` |

### View in PLM (NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Per-row link on each DeliveryItem in Milestone View |
| **Enabled when** | `actual_item_info` is non-null (set once the first document for the (device, milestone, owner) tuple has arrived per FR-57) |
| **User prompt** | None (navigation link) |
| **What happens on click** | Browser opens a new tab navigating to the URL stored in `actual_item_info` (the corp PLM issue URL for the (device, milestone, owner) tuple). No SP field write. |
| **What HILDA does next** | *(None — direct PLM navigation)* TPM views the PLM issue in PLM's own UI. HILDA is uninvolved. |
| **FR refs** | FR-57 |

### Clear Triage Flag (DO NOT NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View; visible only when `manual_triage_required = true` |
| **Enabled when** | `manual_triage_required = true` (HILDA-set by FR-12 path (c.2) below-threshold owner-reply LLM classification) |
| **User prompt** | "Clear triage flag for `<item_name>`? (Confirm you've read the original email + applied the correct `delivery_state` via cell edit or buttons.)" (Yes / Cancel) |
| **What happens on click** | Set `manual_triage_required = false` on the DI row. Single field write. |
| **What HILDA does next** | *(Background — out of your scope)* HILDA receives SP-alert, logs to `CommunicationLog` with `action_type = manual_triage_cleared` + TPM AD account; no further state-machine processing (triage resolution is the TPM's manual `delivery_state` write per FR-14 — this button only clears the surfaced flag). |
| **Idempotency** | Already-cleared items have the button hidden (visibility on `manual_triage_required = true`). |
| **FR refs** | FR-12 I4, FR-14 |

### Confirm Carrier Acceptance (Mark Carrier-Closed) (DO NOT NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View; visible only when `delivery_state = SubmittedToCustomer` |
| **Enabled when** | `delivery_state = SubmittedToCustomer` AND customer-side acceptance has been confirmed out-of-band (email / customer portal status / internal communication — TPM judgment) |
| **User prompt** | "Mark `<item_name>` as Closed? Carrier has confirmed acceptance of this deliverable. **This action is irrevocable.**" (Yes / Cancel) |
| **What happens on click** | Set `delivery_state = "Closed"` on the DI row. Single field write. |
| **What HILDA does next** | *(Background — out of your scope)* HILDA receives SP-alert, applies the state transition per FR-14 contract (FR-7 path (i) Closed); logs to `CommunicationLog` with `action_type = manual_carrier_acceptance` + TPM AD account. If this is the last remaining non-Closed `is_milestone_gating=true` item in the milestone (AND the default work-item is also Closed), fires `MilestoneAllClosed` (FR-28) → `MilestoneStorageCleanup` (FR-76). |
| **Idempotency** | Already-`Closed` items have the button hidden. |
| **FR refs** | FR-7 path (i), FR-14, FR-28 (`MilestoneAllClosed`), FR-76 |

### Resume from Delayed / Resume from Blocked  (DO NOT NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View; visible only when `delivery_state ∈ {Delayed, Blocked}` |
| **Enabled when** | `delivery_state ∈ {Delayed, Blocked}` AND `prior_delivery_state` is non-null (set by HILDA on the original Delayed/Blocked entry per FR-7 I2 prior_delivery_state discipline) |
| **User prompt** | "Resume `<item_name>` from `<current delivery_state>` back to `<prior_delivery_state value>`?" (Yes / Cancel) |
| **What happens on click** | Set `delivery_state = <prior_delivery_state value>` on the DI row. Single field write. SP UI reads `prior_delivery_state` to determine the target state and renders the button label accordingly (e.g., "Resume to OutreachSent"). |
| **What HILDA does next** | *(Background — out of your scope)* HILDA receives SP-alert, applies state transition per FR-14 contract (FR-7 I2 exit path (b)). HILDA atomically clears `prior_delivery_state` (NULL) in the same `[D-064]` REST batch as the `delivery_state` write per FR-12 / FR-14 `prior_delivery_state` discipline. Logs to `CommunicationLog` with `action_type = manual_resume_from_delayed` (or `manual_resume_from_blocked`) + TPM AD account. Re-arms FR-11 `DeadlineProximity` evaluation for the item. |
| **Idempotency** | Items not in `Delayed`/`Blocked` have the button hidden. |
| **FR refs** | FR-7 I2 (Delayed/Blocked exit paths), FR-14, FR-11 (DeadlineProximity re-arm), FR-12 (prior_delivery_state discipline parallel) |

---

## Rule control panel — per-item actions (FR-31) `[Ph-2]`  (DO NOT NEED THIS NOW)

**FR-31 entirely deferred to Ph-2 2026-06-17 per architect lock** — all rule control panel sub-capabilities (Pause/Resume Item Rules, Pause All/Resume All, Trigger Action dropdown, rule param inline edit) are Ph-2; in Ph-1, TPM rule changes go via HILDA ops ticket who edits `customizations/rules/<customer_id>/per_item_overrides.yaml` (YAML drop-zone + SIGHUP reload per FR-30 I1 lock). All sections below are Ph-2 stubs preserved for SP UI engineer Ph-2 implementation reference; **SP UI engineer's Ph-1 deliverable does NOT include any of these sections**.

### Pause Item Rules `[Ph-2]`  (DO NOT NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View. Visible as a toggle or dropdown action. |
| **Enabled when** | Item is not currently paused (no active `AutomationRuleOverride` row for this item) |
| **User prompt** | "Pause all rules for `<item_name>`? Reminders and escalations will not fire until resumed." (Yes / Cancel) |
| **What happens on click** | Set `DeliveryItems.rules_paused_at = <now>` on the DI row. Display "⏸ Rules paused" badge on the row. |
| **What HILDA does next** | *(Background — out of your scope)* HILDA receives SP-alert, logs override to Postgres `AutomationRuleOverride` (scope=item, sentinel `rule_id="__all_rules__"`), logs to `CommunicationLog` with PM attribution. `rule_engine` suppresses all rule evaluations for this item until resumed. |
| **FR refs** | FR-31 sub-1 |

### Resume Item Rules `[Ph-2]`  (DO NOT NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Per-row action; visible only when item is paused |
| **Enabled when** | Item is currently paused |
| **User prompt** | "Resume rules for `<item_name>`?" (Yes / Cancel) |
| **What happens on click** | Clear `DeliveryItems.rules_paused_at` on the DI row (set to null or empty). Remove "⏸ Rules paused" badge. |
| **What HILDA does next** | *(Background)* HILDA receives SP-alert, removes the override from Postgres `AutomationRuleOverride`, logs to `CommunicationLog`. `rule_engine` resumes evaluations on next tick. |
| **FR refs** | FR-31 sub-1 |

### Pause All / Resume All (milestone-level) `[Ph-2]`  (DO NOT NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Milestone View top bar (separate from the 5 buttons above) |
| **Enabled when** | Always (toggle state shows current milestone-wide pause status) |
| **User prompt** | "Pause/Resume all rules for milestone `<name>` (`<N>` items)?" (Yes / Cancel) |
| **What happens on click** | Iterate over all DI rows in the milestone; set/clear `rules_paused_at` on each. Web part should batch these writes to minimize SP-alert noise. |
| **What HILDA does next** | *(Background)* HILDA processes the batch alerts; each fires an individual `AutomationRuleOverride` insert/delete. |
| **FR refs** | FR-31 sub-1 |

### Trigger Action `[Ph-2]`  (DO NOT NEED THIS NOW)

| Field | Value |
|---|---|
| **Where** | Per-row dropdown on each DeliveryItem in Milestone View |
| **Enabled when** | Dropdown is always visible (Ph-2); available action options per FR-31 Option (b) Ph-2 lock 2026-06-17: **`TriggerAIReview`** shown only when documents exist + `review_required=true` + (typically) `review_status=failed` per FR-53 I8 (re-trigger LLM review for failed cases); **`TriggerParser`** shown when an unparsed or `parser_failed` `test_report` document exists per FR-16. **`QueueSubmission` is NOT exposed** per FR-29 I6 lock — no-op marker action (delivery_state IS the queue per FR-63 + FR-18 lock; UnderPMReview→ReadyForSubmission advances via FR-56 (c) Approve button, not via HILDA-side trigger). **`ReassignDocumentToWorkItem` is NOT in SP UI** — lives in HILDA-rendered tab per FR-87 + `[D-074]` Variant A. |
| **User prompt** | Action-specific. Example: "Trigger LLM quality review for `<item_name>`?" (Yes / Cancel) |
| **What happens on click** | Set `DeliveryItems.manual_action_triggered_at = <now>` AND `DeliveryItems.manual_action_kind = "<action_kind>"` on the DI row (one of FR-29 action verbs). Atomic 2-field write. |
| **What HILDA does next** | *(Background)* HILDA receives SP-alert, dispatches the specified action via `workflow_engine` (bypasses normal rule evaluation), logs to `CommunicationLog` with `trigger_source=manual` + PM attribution. |
| **FR refs** | FR-31 sub-3 |

---

## Direct cell edits (FR-14)

In addition to button clicks above, TPM can directly edit TPM-editable columns on individual rows via SP UI cell-edit (the SharePoint list view's inline edit). Each cell edit fires an SP-alert that wakes HILDA per the same SP-alert channel (`[D-047]` / FR-84). HILDA processes the cell edit per FR-14 contract.

### TPM-editable columns

(Editable in SP UI per xlsx column 4 "Writable in SP UI YES/TPM" + FR-56 column model.)

| Column | Effect on edit | FR refs |
|---|---|---|
| `comment` | Advisory; no FR-28 rule-engine trigger fires | FR-14 |
| `delivery_state` | Fires FR-28 rule-engine triggers per FR-14 Downstream effects. E.g., setting `Closed` on the last gating item fires `MilestoneAllClosed` → `MilestoneStorageCleanup`. **For direct `Closed` transition from `SubmittedToCustomer`, prefer the Confirm Carrier Acceptance button above (consistent action_type logging). For direct `Delayed`/`Blocked` entry or recovery, see prior_delivery_state discipline below.** | FR-14, FR-7 |
| `review_required` | `false → true` triggers retroactive FR-53 enqueue for existing received docs on the item | FR-14, FR-53 |
| `manual_triage_required` | TPM clears (`true → false`) after triage resolution. Prefer the Clear Triage Flag button above for consistent UX. | FR-12 I4, FR-14 |
| `email_cc_list` | Per-item CC list override per FR-9 path (b) | FR-9, FR-14 |
| `tracking_modality` | Modality enum override (multi-value) per FR-7 | FR-7, FR-14 |
| **owners `[Ph-2]`** (`owner_corp_usa_email`, `owner_corp_email`, `owner_corp_id`) | Fires `OwnerReassigned` rule + new-owner outreach cascade per FR-88. **Deferred to Ph-2 per FR-3 / DEF-22** — in Ph-1 owner fields are fixed at `setup_milestone` time. | FR-3, FR-88, DEF-22 |

### Exclusions (web part MUST enforce non-editable in SP UI)

- **Ops-editable-only fields per FR-2** (TPM MUST NOT edit): `customer_id`, `customer_jira_url`, `model` (= `device_id`), `project_id`, `assigned_pm_id`
- **YAML-only fields** (NOT TPM-editable): `is_milestone_gating` per `[D-078]`
- **HILDA-managed fields** (HILDA writes; TPM read-only or hidden — see Part 2 Excluded list)

### SP-alert routing + TPM attribution

- Each cell edit fires an SP-alert per the standard "Send Alerts for These Changes" = "Anything changes" config (per SP-alert configuration requirements below).
- HILDA's `sp_alert_parser` routes via the 5-field SP-alert routing key per FR-84 (`customer_id, Model, ProjectID, MinorMilestone, ItemNumber`).
- TPM identity captured via SP's built-in `Editor` system column on the SP-alert payload — logged to HILDA's `CommunicationLog` with `action_type = manual_field_override` + `field_name` + `old_value` + `new_value` + TPM AD account (per FR-14 NFR-5 / NFR-6 attribution).

### Idempotency

- HILDA reads current SP column value before applying the override; if the value already matches the incoming alert payload, no-op (skip the writeback + skip rule-engine triggers). Read-then-write-if-changed parallels FR-6 no-op suppression. `CommunicationLog` entry written once per actual value change (not per duplicate alert).

### `prior_delivery_state` discipline (for Delayed/Blocked cell edits)

- When TPM cell-edits `delivery_state` to `Delayed` or `Blocked` from another state, HILDA's `sp_alert_parser` sets `prior_delivery_state = <current value>` BEFORE writing the new `delivery_state` — both writes in one `[D-064]` REST batch (idempotent on retry).
- When TPM cell-edits `delivery_state` recovery from `Delayed`/`Blocked` to a pre-approval state, HILDA clears `prior_delivery_state` (NULL) atomically with the new `delivery_state` write.
- The Resume from Delayed / Resume from Blocked button above provides one-click UX for the recovery case (uses `prior_delivery_state` as the target).
- SP UI engineer's web part does NOT write `prior_delivery_state` directly — HILDA manages it via `sp_alert_parser`.

---

## Field summary — milestone-level fields (duplicated across every work-item row in the milestone)

These are the SP audit fields written by milestone-level button clicks. Per `SP_lists_authoritative.xlsx`, all live as **columns on every work-item row** within `Deliverables_<customer_id>` — milestone-level button clicks write the column on every row in the milestone (N rows × 1 column). HILDA deduplicates the alert burst via `[D-082]`.

| Field | Type | Written by | Cleared/reset by |
|---|---|---|---|
| `milestone_collection_started_at` | DateTime | Start Collection button | (never; one-shot) |
| `milestone_submission_triggered_at` | DateTime | Submit to Carrier button | (never; one-shot per dispatch) |
| `closed_all_items_triggered_at` | DateTime | Close All Items button | (never; one-shot) |
| `refresh_requested_at` | DateTime | Refresh button | (overwritten on next click) |
| `download_package_request_timestamp` | DateTime | Download Package button | (overwritten on next click) |
| `download_package_url` | URL | HILDA writeback after assembly | Cleared by next Download Package click |
| `download_package_status` | Choice {preparing, ready, failed} | SP UI sets `preparing` on click; HILDA writes `ready`/`failed` | Cleared by next Download Package click |
| `download_package_generated_at` | DateTime | HILDA writeback | Cleared by next Download Package click |

## Field summary — per DeliveryItem row

These are the SP audit fields written by per-row button clicks.

| Field | Type | Written by | Cleared/reset by |
|---|---|---|---|
| `delivery_state` | Choice (11 values) | Approve (→ ReadyForSubmission); Mark Closed (→ Closed); FR-62 Upload (→ UnderPMReview on revert) | HILDA state-machine transitions |
| `pm_approval_at` | DateTime | Approve button (atomic with delivery_state) | Cleared on revert (FR-62 upload while ReadyForSubmission/SubmittedToCustomer) |
| `pm_approval_pm_id` | Person/Group | Approve button (atomic) | Cleared on revert (with `pm_approval_at`) |
| `last_reminder_triggered_at` | DateTime | Send Reminder button | (never; new value overwrites) |
| `rules_paused_at` | DateTime | Pause Item Rules button | Cleared by Resume Item Rules button |
| `manual_action_triggered_at` | DateTime | Trigger Action dropdown | (overwritten on next trigger) |
| `manual_action_kind` | Choice (FR-29 verbs) | Trigger Action dropdown (atomic with timestamp) | (overwritten with timestamp) |
| `manual_triage_required` | Boolean | HILDA sets `true` on FR-12 path (c.2) below-threshold; TPM clears via Clear Triage Flag button OR direct cell edit per FR-14 | (cleared by TPM after triage resolution) |

---

# PART 2 — READ PATHS (Column display + UI gating + dashboard surfaces)

This Part documents the columns SP UI engineer's web part **reads** from `Deliverables_<customer_id>` to drive UI display, button visibility / enablement, and PM dashboard surfaces. Reads happen continuously via live polling + focus-aware refresh. Each row cites the read context, the column(s), and the authoritative FR.

## Milestone View — Read Paths

### Banner + milestone-level indicators

| What | Column(s) read | Drives | FR refs |
|---|---|---|---|
| Milestone banner styling (Not Started / In Progress / Completed / Delayed) | `Milestone.status` (read from any row in milestone — column duplicated across all rows per Ph-1 flat-table per `[D-077]`) | Banner color, label, status badge | FR-6 |
| Start Collection button visibility | `milestone_collection_started_at` (any row) | Button rendered only when `milestone_collection_started_at IS NULL` | FR-6, FR-8 |
| Submit to Carrier button enablement | `delivery_state` + `is_milestone_gating` + `item_type` + `no_customer_upload` (all rows in milestone) | Button enabled when all `is_milestone_gating = true` DIs in `{ReadyForSubmission, SubmittedToCustomer}` AND ≥1 `is_milestone_gating = true` DI in `ReadyForSubmission` AND no gating DI in `UnderPMReview` (FR-62 revert blocker). Items excluded from check (regardless of gating): (a) default work-item (`item_type = Default`); (b) `no_customer_upload = true`; (c) `item_type = Confirmation` | FR-63 |
| Close All Items button enablement | `delivery_state` + `is_milestone_gating` (all rows) | Enabled when all `is_milestone_gating=true` DIs in `{SubmittedToCustomer, Closed}` AND ≥1 `SubmittedToCustomer` | FR-64 |
| Milestone deadline display | `target_date` (any row) | Banner subtitle / deadline display | FR-11, FR-6 |
| Download Package status indicator | `download_package_status` + `download_package_url` (any row) | Indicator state (preparing / ready / failed); Download Now link rendered when status = ready | FR-73 |

## Per-Item Row — Display Reads

| Section | Column(s) read | Display purpose | FR refs |
|---|---|---|---|
| Identity | `item_no`, `item_name`, `item_path_id` | Row label + ordering | FR-2, FR-5 |
| State | `delivery_state` | State badge per FR-7 11-value enum | FR-7 |
| Item type | `item_type` | Type label (4-value enum); affects layout (Confirmation has no document section) | FR-7, FR-58 |
| Owner identity | `owner_corp_usa_email`, `owner_corp_email`, `owner_corp_id`, `owner_name` | Owner display per FR-88 3-field model + auto-resolved display name | FR-88 |
| Tracking modality | `tracking_modality` | Modality badges (multi-value enum) | FR-7 |
| Last contact | `last_owner_contacted` | Display + tooltip showing days since contact | FR-15 |
| Last reminder | `last_reminder_triggered_at` | Display (when set by FR-65 TPM trigger) | FR-15, FR-65 |
| Doc count | `doc_count`, `doc_count_received` | "n of N docs received" display | FR-7 |
| Review required | `review_required` | Toggle display (TPM-editable per FR-14) | FR-7, FR-53 |
| Approval audit | `pm_approval_at`, `pm_approval_pm_id` | Approval timestamp + PM identity display | FR-56(c), `[D-068]` |
| Completion | `actual_completion_date` | Display when set | FR-15 |
| TG identity | `tg_name`, `tg_path_id`, `tg_email_group_alias` | TG header / group display | FR-2, FR-9 |
| TG-coordinator | `tg_owner_corp_usa_email`, `tg_owner_corp_email`, `tg_owner_corp_id` | TG-coordinator display (NOT for outreach per FR-9 — display only) | FR-71, FR-88 |
| Form factor | `handset`, `tablet`, `wearable`, `ir`, `osmr`, `rmr`, `hmr_smr` | Form factor flags display | `[D-084]` |
| Comment | `comment` | Comment display (TPM-editable per FR-14) | FR-14 |
| Triage flag | `manual_triage_required` | "Needs triage" badge surfacing for PM dashboard | FR-12 |
| Expected completion | `expected_completion_date` | Per-item deadline display (HILDA-written from `target_date`; not TPM-editable) | FR-11 |

## Per-Item Button Visibility / Enablement — Read Paths

| Button | Column(s) read | Enablement condition | FR refs |
|---|---|---|---|
| Approve | `delivery_state` | `delivery_state = UnderPMReview` | FR-56(c) |
| Send Reminder | `delivery_state` + `item_type` | `delivery_state ∉ {OwnerClosed, ReadyForSubmission, SubmittedToCustomer, Closed}` AND `item_type ≠ Default` (suppress on default work-item per FR-78 — no owner to remind; system-reserved TG `_unrouted`) | FR-65, FR-78 |
| Mark Closed | `delivery_state` + `no_customer_upload` + (default-work-item check) | (a) FR-78 default work-item: no remaining DocumentItemAssociation; (b) `no_customer_upload = true` per FR-80: standard `OwnerClosed` guards met | FR-78, FR-80 |
| View Documents | `item_type` | Visible only when `item_type ≠ Confirmation` | FR-58 |
| View in PLM | `actual_item_info` | Visible only when `actual_item_info` is non-null | FR-57 |
| Upload Document `[Ph-2]` | `item_type` + `delivery_state` + (effective `tracking_enabled`) + `tracking_modality` | `item_type ≠ Confirmation` AND `item_type ≠ Default` (default work-item is not a TPM upload target per FR-78) AND (`delivery_state ∈ {DocumentReceived, UnderPMReview, ReadyForSubmission, SubmittedToCustomer}` OR `delivery_state = Open` AND (effective `tracking_enabled = false` per FR-81 OR `tracking_modality` includes `SPUI` per FR-9 silent-outreach rule)) | FR-9, FR-62, FR-78, FR-81 |
| Pause/Resume Item Rules | `rules_paused_at` | Pause visible when `rules_paused_at IS NULL`; Resume visible when `rules_paused_at` non-null | FR-31 |
| Trigger Action dropdown | `delivery_state` + (per-action conditions) | Available action options vary by current state (e.g., `TriggerAIReview` shown only when documents exist + `review_required=true`; `QueueSubmission` shown only when `delivery_state = UnderPMReview`) | FR-31 |

## PM Dashboard Surface — Read Paths

PM dashboard is HILDA-rendered per `[D-074]` for document/review surface, but the SP-side milestone/item lists are SP-rendered. SP-side surfaces:

| Dashboard surface | Column(s) read | Surface action | FR refs |
|---|---|---|---|
| Manual triage queue | `manual_triage_required = true` (filter) | List items needing triage; one-click clear after resolution | FR-12 |
| Pending review queue | `review_required = true` (per item; `review_status` is HILDA-internal — see Excluded below) | List items with reviews pending | FR-53 |
| Issues queue | `delivery_state ∈ {Delayed, Blocked}` | List items reported as Delayed/Blocked by owner | FR-7 |
| Overdue queue | `expected_completion_date < today` AND `delivery_state ∉ {ReadyForSubmission, SubmittedToCustomer, Closed}` | Items past deadline | FR-11 |

## Columns SP UI Engineer Does NOT Read

These are HILDA-internal columns — SP UI engineer's web part neither reads nor writes them:

| Column | Why HILDA-internal only | FR refs |
|---|---|---|
| `review_status` | HILDA-internal review aggregation enum (pending/complete/not_required/failed); not surfaced in SP UI per xlsx Notes | FR-53 |
| `last_owner_response_at` | HILDA-internal "no response" signal for FR-10 escalation | FR-10 |
| `reminder_count` | HILDA-internal counter for FR-10 escalation threshold | FR-10 |
| `prior_delivery_state` | HILDA-managed audit of pre-Delayed/Blocked state | FR-7 |
| `plm_id` | HILDA-managed PLM issue reference; PM accesses PLM via View in PLM button which uses `actual_item_info` URL (not `plm_id` directly) | FR-8, FR-26 |
| `project_id` (Devices PK) | Ops-set at row creation; HILDA reads only for FR-84 routing-key cross-validation; SP UI displays it but does not drive UI behavior | FR-2, FR-84 |
| `customer_jira_url` | Ops-set; HILDA reads for FR-25 CustomerJIRA polling base URL | FR-25 |
| `assigned_pm_id` | Ops-set at setup_milestone; HILDA reads for FR-9 outreach sender attribution + FR-51 PM credentialed calls; SP UI may display read-only but does not drive UI behavior | FR-2, FR-9 |
| `tracking_enabled` (effective) | Computed by HILDA from TG-level flag per FR-81 | FR-81 |

## Read cadence + freshness

- **Live polling**: SP UI polls `Deliverables_<customer_id>` per `[D-064]` 2026-06-10 SP UI engineer discipline. Recommended interval 5–10 s for active milestone views; configurable per deployment.
- **Focus-aware refresh**: SP UI re-reads on tab/window focus-gain (per `[D-074]`). PM/TPM returning to SP tab after viewing HILDA dashboard always sees fresh state on next focus event.
- **HILDA writeback latency**: HILDA's `[D-064]` REST writes are typically <1 s end-to-end. UI lag is bounded by SP UI's poll interval + REST commit latency.

---

## Out-of-scope clarifications

These buttons are **NOT** in the SP UI engineer's scope:

- **FR-87 TPM document resolution buttons** (Reassign work-item / Resolve doc_type / Resolve revision) — these live in **HILDA's rendered document section** (HILDA tab), not SP UI per `[D-074]` Variant A. They use same-origin form POST to HILDA's dashboard endpoints, bypassing SP-alert entirely.
- **FR-47 resolution-path picker** `[Ph-2]` (PM selects `resolution_path ∈ {fix_pre_launch, tech_report, waiver}` for each failed test case without `waiver_ref` in `interim` reports per FR-46) — lives in **HILDA's rendered document section** at `/docs/<customer_id>/<item_id>` per `[D-074]` + FR-60 + FR-47. Same family as FR-87 — HILDA-rendered, not SP UI; bypasses SP-alert.
- **FR-62 Upload Document FORM** — the *button* is in SP UI (link-out anchor only); the upload *form itself* is rendered by HILDA's dashboard.
- **Any HILDA dashboard rendering** (`/docs/<customer_id>/<item_id>`, `/upload/<customer_id>/<item_id>`, `/dl/<scoped_token>`) — owned by HILDA, not SP UI engineer. Includes: document section + per-doc review findings display + FR-87 TPM resolution buttons + FR-47 resolution-path picker + FR-62 upload form.

---

## SP-alert configuration requirements

For SP-alert email channel to function:

1. **"Send Alerts for These Changes" = "Anything changes"** on every per-customer `Deliverables_<customer_id>` list HILDA reads from. Without this, TPM direct field edits per FR-14 won't fire alerts.
2. **Subject line format**: `Alert_Deliverables_<customer_id> - <ItemTitle>` per FR-84 (e.g., `Alert_Tasks_MMK - NVIOT - AGPS Test Results`). SP UI engineer's alert template MUST emit the 5 routing-key fields in the alert body (`customer_id`, `Model`, `ProjectID`, `MinorMilestone`, `ItemNumber`). The `customer_id` value is encoded in the subject suffix; `Model`, `ProjectID`, `MinorMilestone`, `ItemNumber` in the body select the row within the list.
3. **Alert destination**: the HILDA team's dedicated mailbox (configured per deployment).
4. **Buttons that write fields must NOT bypass alerts** — every field write in this spec MUST fire an alert. If SP UI engineer uses any "silent update" API, HILDA misses the event.

---

## Versioning

Last updated: 2026-06-17 — **FR-56 → FR-65 sync pass**: Submit to Carrier enablement updated to FR-63 gating-aware lock + 3 category exclusions + FR-62 revert blocker tooltip + first-time-submission irreversibility text + default-work-item soft warn; Close All Items confirmation text updated to FR-64 NFR-5 gate text (cascade-count summary + `DSH-E003` post-cleanup + default-work-item separate Mark Closed); Upload Document enable-when added FR-9 SPUI silent-outreach condition + FR-78 default-work-item exclusion; Send Reminder Part 2 enable-when added FR-78 default-work-item exclusion (Part 1/Part 2 now in sync); Approve revert path extended to cover both `ReadyForSubmission` and `SubmittedToCustomer` upload-from states per FR-62 revert pattern; Mark Closed guard-failure error code marked as TODO pending dashboard MODULE.md error catalogue alignment. Last prior: 2026-06-16 — Doc restructured into Part 1 (WRITE) + Part 2 (READ). When new buttons are added, new read paths surface, or behavior changes, update this file + `SP_lists_authoritative.xlsx` together.
