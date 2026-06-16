# SP UI Button Action Specifications

**Audience**: SP UI engineer implementing the SharePoint web parts.
**Purpose**: Per-button spec for every clickable action in the SP UI, with the exact field-write contract HILDA expects on each click.
**Authority**: this file + `SP_lists_authoritative.xlsx` are the two SP UI engineer deliverables of authority.

---

## Common conventions

**Field-write pattern.** Every button click is a **SP-side field write** on a SP list row. The act of writing the field is what fires the SP-alert that wakes HILDA — no direct HTTP call to HILDA from the SP web part (corp firewall blocks it per FR-84). HILDA learns of every click via the resulting alert email.

**Milestone-scoped writes touch every work-item row in the milestone.** Milestone-level actions (Start Collection / Submit to Carrier / Close All Items / Refresh / Download Package) write the milestone-level column (e.g., `milestone_collection_started_at`) to **every work-item row** in the milestone within `Tasks_<customer_id>` — N rows × 1 column write in a single SP transaction. Each per-row write fires an SP-alert; HILDA processes the first alert in the burst and deduplicates the rest by detecting the same `(customer_id, MinorMilestone, column_name)` tuple across the burst (per FR-11 / `[D-082]` cascade-dedup pattern). Per-DeliveryItem actions write to the individual DI row only.

**Atomic 3-field write for Approve.** The Approve button writes 3 fields (`delivery_state` + `pm_approval_at` + `pm_approval_pm_id`) in a **single SP transaction**. SP web part code must enforce atomicity; partial writes are a defect.

**No direct HTTP to HILDA.** All HILDA-side processing happens via the SP-alert email channel (`[D-047]` / FR-84). The SP web part never calls `hilda-api` or `hilda-proxy.corp` directly except via top-level browser navigation for link-out anchors (View Documents / View in PLM / Upload Document).

**Live polling.** SP UI polls the SP list for row state changes (focus-aware refresh per `[D-064]` 2026-06-10 SP UI engineer discipline). State updates pushed by HILDA via `[D-064]` REST writeback become visible to TPM after the next focus refresh.

**Role-based control.** Field-level role restrictions are SP web part responsibility. Where this spec says "TPM editable" or "ops only", the web part enforces. HILDA does not enforce role permissions — it trusts the SP write.

**Atomicity for SP-side cascades.** Where this spec says SP-side cascade (e.g., Start Collection writes `milestone_collection_started_at` AND no other field; Approve writes 3 fields atomically), the web part executes all writes in one SP transaction OR fires all alerts only after all writes succeed.

---

## Milestone View — top-bar buttons

### Start Collection

| Field | Value |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | `milestone.milestone_collection_started_at` is empty AND `milestone.status` ∈ {Not Started, In Progress} |
| **User prompt** | "Start collection for milestone `<name>`? This will send initial outreach to all R&D owners." (Yes / Cancel) |
| **What happens on click** | Set `milestone_collection_started_at = <now>` on **every work-item row** in the milestone (within `Tasks_<customer_id>`, filter `model + minorMilestone`). Single SP transaction batching N row updates. |
| **What HILDA does next** | *(Background — out of your scope)* HILDA receives N SP-alerts, deduplicates to one via `(customer_id, minorMilestone, milestone_collection_started_at)` burst detection, transitions each DI from `Not Started` → `Open`, creates PLM issues per (owner × milestone), fires email outreach to all owners, transitions each DI to `OutreachSent`, activates runtime polling channels (Email/NSD/PLM/CustomerJIRA per per-item `tracking_modality`), and sets `Milestone.status = "In Progress"` (writes to every row in the milestone). Item rows update via live polling. |
| **Idempotency** | Re-click on an already-started milestone is safe — HILDA detects existing `plm_id` and skips duplicate creation; only DIs still in `Open` get re-fired outreach. |
| **FR refs** | FR-8, FR-56 (e), FR-84 (N-row write pattern), `[D-082]` (cascade dedup) |

### Submit to Carrier

| Field | Value |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | All DIs in milestone are in `{ReadyForSubmission, SubmittedToCustomer}` AND at least one DI is in `ReadyForSubmission` |
| **User prompt** | **Initial submission**: "Submit `<N>` items to carrier? This dispatches the submission package via the customer adapter." (Yes / Cancel) — `<N>` = count of ReadyForSubmission items. **Re-submission** (any item already SubmittedToCustomer): "Re-submitting `<N>` items with updated documents. `<M>` previously-submitted items are unchanged and excluded from this package. Proceed?" (Yes / Cancel) |
| **What happens on click** | Set `milestone_submission_triggered_at = <now>` on **every work-item row** in the milestone (N-row write; HILDA deduplicates per `[D-082]`). |
| **What HILDA does next** | *(Background — out of your scope)* HILDA assembles the submission package from `ReadyForSubmission` items only (`SubmittedToCustomer` items are skipped on re-submission), dispatches via the customer adapter (Google Drive browser automation Ph-1/Ph-2 per FR-19), transitions dispatched items to `SubmittedToCustomer`, logs to `CommunicationLog` with `action_type=submission` (initial) or `resubmission` (delta). Item rows update via live polling. |
| **Idempotency** | If a previous Submit attempt failed mid-dispatch, re-clicking is safe — HILDA's idempotency check on `(milestone_id, dispatch_run_id)` prevents duplicate carrier upload. |
| **FR refs** | FR-18, FR-19, FR-63, FR-56 (e) |

### Close All Items

| Field | Value |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | All `is_milestone_gating=true` DIs are in `{SubmittedToCustomer, Closed}` AND at least one `is_milestone_gating=true` DI is in `SubmittedToCustomer`. **Non-gating items** (`is_milestone_gating=false`) may remain in any non-blocking state at click time. |
| **User prompt** | "Close all items in milestone `<name>`? This finalizes the milestone — non-Closed items will be auto-advanced to Closed, and document storage will be permanently cleaned up. **Document download links will return 404 after cleanup — download any needed files first.**" (Yes / Cancel) |
| **What happens on click** | Set `close_all_items_triggered_at = <now>` on **every work-item row** in the milestone (N-row write; HILDA deduplicates per `[D-082]`). |
| **What HILDA does next** | *(Background — out of your scope)* HILDA performs a two-part cascade per FR-64: (1) sets all `SubmittedToCustomer` items to `Closed`; (2) auto-advances any `is_milestone_gating=false` items in non-Closed states to `Closed` (action_type=`bulk_close_non_gating`). When all gating items + default work-item reach `Closed`, FR-76 `MilestoneStorageCleanup` fires — NSD `internal/<milestone>/` subtree is deleted permanently. Item rows update via live polling. |
| **Idempotency** | Re-click is no-op once all items are `Closed`. |
| **FR refs** | FR-28 (`MilestoneAllClosed`), FR-64, FR-76, FR-56 (e) |

### Refresh

| Field | Value |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | Always |
| **User prompt** | None (immediate action). |
| **What happens on click** | Set `refresh_requested_at = <now>` on **every work-item row** in the milestone (N-row write; HILDA deduplicates per `[D-082]`). |
| **What HILDA does next** | *(Background — out of your scope)* HILDA performs a soft-poll across Email/PLM/NSD ingest channels for the milestone, checking for new documents since the last poll. Rate-limited HILDA-side (default 5 min per milestone). If a soft-poll is already in progress, the alert is acknowledged with no new task. After poll, HILDA writes back SP updates per `[D-064]`. Item rows update via live polling. |
| **Idempotency** | Rate limit prevents over-firing. |
| **FR refs** | FR-56 (f) |

### Download Package

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

### Approve

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View |
| **Enabled when** | `delivery_state = UnderPMReview` |
| **User prompt** | "Approve item `<item_name>` for submission to carrier?" (Yes / Cancel) |
| **What happens on click** | **Atomic 3-field write to the DI row in ONE SP transaction**: `delivery_state = "ReadyForSubmission"` + `pm_approval_at = <now>` + `pm_approval_pm_id = <current PM/TPM AD account>`. Partial writes are a defect — all 3 or none. |
| **What HILDA does next** | *(Background — out of your scope)* HILDA receives the SP-alert, reads the 3 fields, logs to `CommunicationLog` with `action_type=pm_approval`, fires FR-28 `PMApproval` trigger downstream (queues for FR-18 submission per rule_engine). HILDA does NOT re-write any of the 3 fields — they are SP-canonical per `[D-068]` impl note 2026-06-12. |
| **Revert path** | If TPM uploads a new document via FR-62 [Ph-2] on a `ReadyForSubmission` item, the web part must atomically revert `delivery_state = "UnderPMReview"` + clear `pm_approval_at` + clear `pm_approval_pm_id` in one SP transaction (per FR-62 revert pattern). |
| **FR refs** | FR-28 (`PMApproval`), FR-56 (c), FR-7 state machine, `[D-068]` |

### Send Reminder

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View |
| **Enabled when** | `delivery_state ∉ {OwnerClosed, ReadyForSubmission, SubmittedToCustomer, Closed}` |
| **User prompt** | None (immediate action). Optional: "Send reminder to `<owner_name>`?" (Yes / Cancel) — implementation choice. |
| **What happens on click** | Set `DeliveryItems.last_reminder_triggered_at = <now>` on the DI row. |
| **What HILDA does next** | *(Background — out of your scope)* HILDA dispatches a reminder to the owner via the item's status-capable modality (Email primary; `tg_email_group_alias` if set; per FR-9 preference rule). On dispatch success, HILDA writes back `last_owner_contacted = <now>` via `[D-064]` REST. On failure, logs to `HildaOpsAlert` per FR-75; `last_owner_contacted` stays unchanged. |
| **Idempotency** | Re-click re-sends the reminder (intentional — TPM ad-hoc trigger per FR-14 / FR-65). |
| **FR refs** | FR-9, FR-10, FR-15, FR-65 |

### Mark Closed (manual close path)

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View. Visible on: (a) the default work-item per FR-78; (b) any item with `no_customer_upload = true` per FR-80. NOT visible on other items (they reach `Closed` only via Submit to Carrier + Close All Items). |
| **Enabled when** | **For default work-item (FR-78)**: no remaining documents associated via `DocumentItemAssociation` (i.e., all unrouted docs have been reassigned to real items via FR-83). **For `no_customer_upload=true` items (FR-80)**: standard `OwnerClosed` guards met — `delivery_state = OwnerClosed` AND any subsequent state, but not yet `Closed`. |
| **User prompt** | "Mark `<item_name>` as Closed? This action is irrevocable." (Yes / Cancel) |
| **What happens on click** | Set `DeliveryItems.delivery_state = "Closed"` on the DI row. (HILDA enforces the guard server-side via the SP-alert; web part SHOULD do client-side gating but HILDA is the ultimate enforcer.) |
| **What HILDA does next** | *(Background — out of your scope)* HILDA receives SP-alert, verifies guards. If gate passes: logs to `CommunicationLog` with `action_type=tpm_mark_closed`. If the closed item is the default work-item AND all gating items are also `Closed`, fires `MilestoneAllClosed` (FR-28) → `MilestoneStorageCleanup` (FR-76). If guard fails: HILDA reverts `delivery_state` to the prior value via `[D-064]` and logs a `DSH-E0XX` error to surface in next focus refresh. |
| **Idempotency** | Already-`Closed` items have the button hidden. |
| **FR refs** | FR-7, FR-78, FR-80, FR-14 |

### Upload Document `[Ph-2]`

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View |
| **Enabled when** | `item_type ≠ Confirmation` AND `delivery_state ∈ {DocumentReceived, UnderPMReview, ReadyForSubmission, SubmittedToCustomer}` OR (in `Open` state when effective `tracking_enabled = false` per FR-81 — no-tracking TG fallback) |
| **User prompt** | None (button is a link-out anchor; navigation, not click-action) |
| **What happens on click** | Browser opens a new tab navigating to `https://hilda-proxy.corp/upload/<customer_id>/<item_id>` where `<customer_id>` is the row's `customer_id` column value (selects the SP list) and `<item_id>` is the SP system `ID` column (auto-Counter PK; per-list unique). Built as: `<uploadUrlPrefix>/<customer_id>/<item_id>` where `uploadUrlPrefix` is set as a SP web part property at deployment (e.g., `https://hilda-proxy.corp/upload`). HILDA's dashboard resolves the target row via `Tasks_<customer_id>` `GetItemById(<item_id>)` per FR-5. No SP field write. |
| **What HILDA does next** | *(Background — handled by HILDA's dashboard, not SP)* HILDA's dashboard renders an upload form; TPM submits via same-origin form POST; HILDA writes the file to NSD + document index per FR-62. After successful upload, if `delivery_state ∈ {ReadyForSubmission, SubmittedToCustomer}`, HILDA reverts to `UnderPMReview` and clears `pm_approval_at`/`pm_approval_pm_id` per the revert pattern (see Approve button → Revert path). State updates push back to SP via `[D-064]`; visible on next focus refresh. |
| **FR refs** | FR-62, `[D-074]` |

### View Documents

| Field | Value |
|---|---|
| **Where** | Per-row link on each DeliveryItem in Milestone View |
| **Enabled when** | `item_type ≠ Confirmation` |
| **User prompt** | None (navigation link) |
| **What happens on click** | Browser opens a new tab navigating to `<documentsUrlPrefix>/<customer_id>/<item_id>` where `<customer_id>` is the row's `customer_id` column value (selects the SP list) and `<item_id>` is the SP system `ID` column (auto-Counter PK; per-list unique — NOT globally unique across `Tasks_<customer_id>` lists per FR-5). Example: `https://hilda-proxy.corp/docs/MMK/12345`. Prefix is a SP web part property set at deployment. HILDA's dashboard resolves the target row via `Tasks_<customer_id>` `GetItemById(<item_id>)` per FR-5. No SP field write. |
| **What HILDA does next** | *(Background — HILDA dashboard renders)* HILDA's dashboard renders the document section as server-side HTML per FR-57/FR-59/FR-60. TPM stays in the HILDA tab for FR-87 TPM-resolution buttons (handled in HILDA tab, NOT SP UI). |
| **FR refs** | FR-57, FR-59, FR-60, FR-61, `[D-074]` |

### View in PLM

| Field | Value |
|---|---|
| **Where** | Per-row link on each DeliveryItem in Milestone View |
| **Enabled when** | `actual_item_info` is non-null (set once the first document for the owner × milestone has arrived per FR-57) |
| **User prompt** | None (navigation link) |
| **What happens on click** | Browser opens a new tab navigating to the URL stored in `actual_item_info` (the corp PLM issue URL for the owner × milestone pair). No SP field write. |
| **What HILDA does next** | *(None — direct PLM navigation)* TPM views the PLM issue in PLM's own UI. HILDA is uninvolved. |
| **FR refs** | FR-57 |

---

## Rule control panel — per-item actions (FR-31)

### Pause Item Rules `[Ph-1]`

| Field | Value |
|---|---|
| **Where** | Per-row action on each DeliveryItem in Milestone View. Visible as a toggle or dropdown action. |
| **Enabled when** | Item is not currently paused (no active `AutomationRuleOverride` row for this item) |
| **User prompt** | "Pause all rules for `<item_name>`? Reminders and escalations will not fire until resumed." (Yes / Cancel) |
| **What happens on click** | Set `DeliveryItems.rules_paused_at = <now>` on the DI row. Display "⏸ Rules paused" badge on the row. |
| **What HILDA does next** | *(Background — out of your scope)* HILDA receives SP-alert, logs override to Postgres `AutomationRuleOverride` (scope=item, sentinel `rule_id="__all_rules__"`), logs to `CommunicationLog` with PM attribution. `rule_engine` suppresses all rule evaluations for this item until resumed. |
| **FR refs** | FR-31 sub-1 |

### Resume Item Rules `[Ph-1]`

| Field | Value |
|---|---|
| **Where** | Per-row action; visible only when item is paused |
| **Enabled when** | Item is currently paused |
| **User prompt** | "Resume rules for `<item_name>`?" (Yes / Cancel) |
| **What happens on click** | Clear `DeliveryItems.rules_paused_at` on the DI row (set to null or empty). Remove "⏸ Rules paused" badge. |
| **What HILDA does next** | *(Background)* HILDA receives SP-alert, removes the override from Postgres `AutomationRuleOverride`, logs to `CommunicationLog`. `rule_engine` resumes evaluations on next tick. |
| **FR refs** | FR-31 sub-1 |

### Pause All / Resume All (milestone-level) `[Ph-1]`

| Field | Value |
|---|---|
| **Where** | Milestone View top bar (separate from the 5 buttons above) |
| **Enabled when** | Always (toggle state shows current milestone-wide pause status) |
| **User prompt** | "Pause/Resume all rules for milestone `<name>` (`<N>` items)?" (Yes / Cancel) |
| **What happens on click** | Iterate over all DI rows in the milestone; set/clear `rules_paused_at` on each. Web part should batch these writes to minimize SP-alert noise. |
| **What HILDA does next** | *(Background)* HILDA processes the batch alerts; each fires an individual `AutomationRuleOverride` insert/delete. |
| **FR refs** | FR-31 sub-1 |

### Trigger Action `[Ph-1]`

| Field | Value |
|---|---|
| **Where** | Per-row dropdown on each DeliveryItem in Milestone View |
| **Enabled when** | Dropdown is always visible; available action options vary by current `delivery_state` (e.g., `TriggerAIReview` shown only when documents exist + `review_required=true`; `QueueSubmission` shown only when `delivery_state = UnderPMReview`) |
| **User prompt** | Action-specific. Example: "Trigger LLM quality review for `<item_name>`?" (Yes / Cancel) |
| **What happens on click** | Set `DeliveryItems.manual_action_triggered_at = <now>` AND `DeliveryItems.manual_action_kind = "<action_kind>"` on the DI row (one of FR-29 action verbs). Atomic 2-field write. |
| **What HILDA does next** | *(Background)* HILDA receives SP-alert, dispatches the specified action via `workflow_engine` (bypasses normal rule evaluation), logs to `CommunicationLog` with `trigger_source=manual` + PM attribution. |
| **FR refs** | FR-31 sub-3 |

---

## Field summary — milestone-level fields (duplicated across every work-item row in the milestone)

These are the SP audit fields written by milestone-level button clicks. Per `SP_lists_authoritative.xlsx`, all live as **columns on every work-item row** within `Tasks_<customer_id>` — milestone-level button clicks write the column on every row in the milestone (N rows × 1 column). HILDA deduplicates the alert burst via `[D-082]`.

| Field | Type | Written by | Cleared/reset by |
|---|---|---|---|
| `milestone_collection_started_at` | DateTime | Start Collection button | (never; one-shot) |
| `milestone_submission_triggered_at` | DateTime | Submit to Carrier button | (never; one-shot per dispatch) |
| `close_all_items_triggered_at` | DateTime | Close All Items button | (never; one-shot) |
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

---

## Out-of-scope clarifications

These buttons are **NOT** in the SP UI engineer's scope:

- **FR-87 TPM document resolution buttons** (Reassign work-item / Resolve doc_type / Resolve revision) — these live in **HILDA's rendered document section** (HILDA tab), not SP UI per `[D-074]` Variant A. They use same-origin form POST to HILDA's dashboard endpoints, bypassing SP-alert entirely.
- **FR-62 Upload Document FORM** — the *button* is in SP UI (link-out anchor only); the upload *form itself* is rendered by HILDA's dashboard.
- **Any HILDA dashboard rendering** (`/docs/<customer_id>/<item_id>`, `/upload/<customer_id>/<item_id>`, `/dl/<scoped_token>`) — owned by HILDA, not SP UI engineer.

---

## SP-alert configuration requirements

For SP-alert email channel to function:

1. **"Send Alerts for These Changes" = "Anything changes"** on every per-customer `Tasks_<customer_id>` list HILDA reads from. Without this, TPM direct field edits per FR-14 won't fire alerts.
2. **Subject line format**: `Alert_Tasks_<customer_id> - <ItemTitle>` per FR-84 (e.g., `Alert_Tasks_MMK - NVIOT - AGPS Test Results`). SP UI engineer's alert template MUST emit the 5 routing-key fields in the alert body (`customer_id`, `Model`, `ProjectID`, `MinorMilestone`, `ItemNumber`). The `customer_id` value is encoded in the subject suffix; `Model`, `ProjectID`, `MinorMilestone`, `ItemNumber` in the body select the row within the list.
3. **Alert destination**: the HILDA team's dedicated mailbox (configured per deployment).
4. **Buttons that write fields must NOT bypass alerts** — every field write in this spec MUST fire an alert. If SP UI engineer uses any "silent update" API, HILDA misses the event.

---

## Versioning

Last updated: 2026-06-15. Tracks Ph-1 + locked Ph-2 buttons. When new buttons are added or behavior changes, update this file + `SP_lists_authoritative.xlsx` together.
