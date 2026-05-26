# SharePoint UI Requirements — HILDA

**Audience**: SP UI engineer building HILDA's SharePoint 2017 web parts.

**Scope**: This document is everything you need to develop the SharePoint side of HILDA. It does **not** cover the Python services that consume SP data (that side is independently developed; you don't need to know its internals).

**Stack constraint**: SharePoint 2017 on-prem. **Vanilla SharePoint Lists + classic web parts only.** No SPFx modern, no Power Apps, no Office 365 features.

**Authentication**: PM/TPM browsers are already authenticated to corp SP via on-prem AD (NTLM/Kerberos). No SSO bridge needed for SP itself.

---

## 1. Big picture — what the SP UI does

PMs (Project Managers) and TPMs (Technical Project Managers) use SharePoint as their **dashboard** for managing connected-device certification deliverables.

Through SP, they:
- See the status of every delivery item in every device's milestone, in a live view
- Click buttons (Start Collection, Approve, Submit to Carrier, Close All Items, Send Reminder, etc.) to drive the workflow
- Edit fields directly (overrides) when needed
- Download documents (test reports, tech reports, waivers) via HILDA-mediated links
- Review LLM quality assessments + rule-based parser results on uploaded documents
- Approve customer submissions

**Behind the scenes** (you don't need to know the details, but for context): a separate HILDA service running on a Linux PC reads SP list data, automates everything else (email outreach to R&D owners, document classification, PLM uploads, carrier submission, status tracking), and writes status updates back into SP lists. Your job is the SP-side UI; HILDA's job is everything else.

---

## 2. The SP Lists you'll build views on

HILDA's data lives in **6 SharePoint Lists** within a dedicated DeliverableHub SP site. Column names and types below — please use these **internal names exactly** so HILDA's REST API client can read/write them without per-deployment translation.

> **Note**: `CustomerTemplates` and `AutomationRules` are **NOT** SharePoint Lists in HILDA — they live in YAML files on the HILDA Linux server and are managed by ops, not by SP UI. You don't need to render them.

### 2.1 List: `Customers`

| Internal name | Type | Notes |
|---|---|---|
| `customer_id` | String (auto) | PK |
| `customer_name` | String, required | "Carrier Alpha" |
| `customer_code` | String, required, unique | "CALPHA" (used in tags) |
| `primary_contact_name` | String | |
| `primary_contact_email` | String | |
| `notes` | Multi-line text | |
| `created_date` | DateTime, default Now | |
| `is_active` | Boolean, default Yes | Soft-delete flag |

### 2.2 List: `Devices`

| Internal name | Type | Notes |
|---|---|---|
| `device_id` | String (auto) | PK |
| `device_name` | String, required | e.g. "ModelZ-5G" |
| `customer_id` | Lookup → Customers | required |
| `assigned_pm_id` | Lookup → Users | required |
| `status` | Choice | Active / Completed / Archived |
| `template_id` | String | nullable — references a YAML template (not a SP list lookup) |
| `created_date` | DateTime, default Now | |
| `target_launch_date` | Date | |

### 2.3 List: `Milestones`

| Internal name | Type | Notes |
|---|---|---|
| `milestone_id` | String (auto) | PK |
| `device_id` | Lookup → Devices | required |
| `milestone_name` | String, required | e.g. "LE-6", "Lab Entry" |
| `sort_order` | Integer, required | display order within device |
| `target_date` | Date | |
| `status` | Choice | Not Started / In Progress / Completed / Delayed |
| `email_cc_list` | Multi-line text | JSON array `[{name, email, role}, ...]` |
| `milestone_collection_started_at` | DateTime | nullable — set by clicking **Start Collection** |
| `milestone_submission_triggered_at` | DateTime | nullable — set by clicking **Submit to Carrier** |
| `refresh_requested_at` | DateTime | nullable — hidden field set by **Refresh** action |

Unique constraint: `(device_id, milestone_name)`

### 2.4 List: `DeliveryItems` — the main tracking table

| Internal name | Type | Notes |
|---|---|---|
| `item_id` | String (auto) | PK |
| `item_no` | Integer, required | sequential within milestone — **immutable for the life of the item** (do NOT re-number when items are added/removed mid-milestone — HILDA uses this as a routing key) |
| `tg_name` | String | "HW" / "SW" / "QA" / etc. — registry-controlled |
| `milestone_id` | Lookup → Milestones | required |
| `item_name` | String, required | |
| `item_description` | Multi-line text | |
| `delivery_state` | Choice | Not Started / Open / OutreachSent / DocumentReceived / UnderPMReview / OwnerClosed / Delayed / Blocked / ReadyForSubmission / SubmittedToCustomer / Closed |
| `expected_completion_date` | Date | |
| `actual_completion_date` | Date | nullable — auto-set when state→OwnerClosed |
| `item_type` | Choice | Confirmation / CompletionPct / TestReport / SoftwareBinary / TechReport / Waiver |
| `owner_name` | String | |
| `owner_email` | String | |
| `tracking_modality` | Choice (multi-select) | Email / CorporateMessenger / CorporatePLM / NetworkSharedDrive / CustomerJIRA — this is **multi-value** (an item can be tracked via multiple modalities) |
| `actual_item_info` | URL | PLM issue URL (set by HILDA on first document arrival) |
| `plm_id` | String | PLM issue ID (e.g. "PROJ-1234") |
| `handset`, `tablet`, `wearable`, `mr`, `hmr_smr` | Boolean each | form factor flags |
| `customer_delivery_modality` | Choice | None / Email / CustomerTrackingSystem / OurFileStorage |
| `customer_delivery_info` | String | email address / credential ID / empty |
| `owner_status_note` | Multi-line text | latest interim status |
| `comment` | Multi-line text | |
| `doc_count` | Integer, default 1 | number of test reports expected before DocumentReceived |
| `review_required` | Boolean, default No | gates LLM quality review |
| `review_status` | Choice | pending / complete / not_required |
| `item_completion_pct` | Integer (computed/read-only) | document-review completion % |
| `email_cc_list` | Multi-line text | JSON array — per-item CC override |
| `milestone_gating` | Boolean | does this item gate milestone closure? *(field name TBD — confirm with HILDA team if your prototype uses a different name)* |
| `last_updated` | DateTime, auto-updated | |
| `last_owner_contacted` | DateTime | nullable |
| `last_reminder_triggered_at` | DateTime | nullable — set by **Send Reminder** action |
| `sort_order` | Integer, required | display order within milestone |

Unique constraints: `(milestone_id, item_name)`, `(milestone_id, item_no)`

### 2.5 List: `Users`

| Internal name | Type | Notes |
|---|---|---|
| `user_id` | String (auto) | PK |
| `display_name` | String, required | |
| `email` | String, required, unique | corp email; used for SSO matching |
| `role` | Choice | PM / TeamLead / Admin |
| `is_active` | Boolean, default Yes | |

### 2.6 List: `PMCredentials` (metadata only — actual secrets stored elsewhere by HILDA)

| Internal name | Type | Notes |
|---|---|---|
| `credential_id` | String (auto) | PK |
| `user_id` | Lookup → Users | required |
| `system_type` | Choice | InternalIssueTracker / CustomerJira / CustomerPortal / etc. |
| `system_name` | String, required | display name |
| `auth_method` | Choice | OAuth2 / APIToken / BasicAuth / SessionCookie |
| `token_expiry` | DateTime | nullable |
| `last_validated` | DateTime | |
| `status` | Choice | Active / Expired / Revoked |
| `created_date` | DateTime, default Now | |
| `updated_date` | DateTime, default Now | |

> **Important**: no plaintext credential / API token / password ever lives in this list. HILDA holds the encrypted blob outside SP. This list only carries metadata for the credential-management UI (which you'll build later, post-Phase-1).

### 2.7 List: `CommunicationLog`

| Internal name | Type | Notes |
|---|---|---|
| `log_id` | String (auto) | PK |
| `item_id` | Lookup → DeliveryItems | nullable |
| `device_id` | Lookup → Devices | nullable |
| `channel` | Choice | Email / Messenger / CorporatePLM / NetworkSharedDrive / CustomerJIRA / SharePoint |
| `direction` | Choice | Inbound / Outbound |
| `sender` | String | |
| `recipients` | String | |
| `subject` | String | |
| `summary` | Multi-line text | brief content summary (LLM-generated for inbound) |
| `action_type` | String | e.g. "submission" / "resubmission" / "bulk_close" |
| `attachments` | Multi-line text | JSON `[{filename, download_url}, ...]` |
| `timestamp` | DateTime, default Now | |

### 2.8 List: `TGGroups` — per-TG-group metadata (TG = Technical Group)

A DeliveryItem belongs to a TG (e.g., "HW", "SW", "QA") via its `tg_name` field — that's already in §2.4. The TG itself has metadata that applies to **every item in the group within a given milestone**: who coordinates the TG, the TG's corp email distribution alias, the TG's corp messenger IDs, the default CC list for emails. Those are NOT stored on each DeliveryItem row (would duplicate across many items) — they live in this list, one row per `(milestone_id, tg_name)`.

| Internal name | Type | Notes |
|---|---|---|
| `tg_group_id` | String (auto) | PK |
| `milestone_id` | Lookup → Milestones, required | parent milestone |
| `tg_name` | String, required | e.g. "HW" / "SW" / "QA" — must match the `tg_name` values used on DeliveryItem rows in this milestone |
| `tg_owner_name` | String | TG coordinator's display name — knows the current engineer assignments for this TG (distinct from per-DeliveryItem `owner` who is the actual delivery engineer) |
| `tg_owner_email` | String | TG coordinator's email |
| `email_group_alias` | String | nullable — the TG's corporate email distribution alias (e.g., `ims.corp@corp.com`). **When set, HILDA sends outreach to this alias instead of individual `owner_email` addresses for items in this TG.** When null, HILDA sends to individual owners per item. |
| `corp_id_list` | Multi-line text | nullable — JSON array of corp IDs for all TG members (e.g., `["mkadado", "tarasu", ...]`). **When set, HILDA uses this list for corp messenger escalation (FR-10) instead of the individual owner's corp ID.** When null, HILDA escalates to individual owner only. |
| `default_cc_list` | Multi-line text | nullable — JSON array `[{name, email, role}, ...]`. Pre-populates per-item `email_cc_list` on DeliveryItems at tracker-creation time (per-item override is allowed via FR-14). |

**Unique constraint:** `(milestone_id, tg_name)` — one row per TG per milestone; SP UI must prevent duplicates.

**How rows get populated:**
- HILDA creates these rows automatically at tracker-creation time, reading values from the customer template YAML (`customizations/template_schemas/<customer>/tg_groups.yaml`).
- TPM can edit these rows in SP UI **before** the milestone's collection kickoff (FR-71 ODF — Phase 2; for Phase 1 the YAML values are accepted as-is).
- After collection kickoff, edits trigger SP alerts to HILDA so the live system picks up the change (e.g., a corrected `tg_owner_email` reaches HILDA via the SP-alert email channel).

**Why this is a separate list (not columns on DeliveryItems):**
- One TG can contain 20+ items in a milestone. Putting `tg_owner_email`, `email_group_alias`, etc. on every item would mean 20× duplication and 20× consistency risk when the value changes.
- The DeliveryItems list keeps just `tg_name` (the foreign-key-like label); SP UI does a lookup to TGGroups for display.
- Aligns with the schema/content boundary per HILDA's `[D-045]` — TG metadata is per-group config, not per-item runtime state.

---

## 3. Views to build

### 3.1 Milestone View (the primary view)

The view PMs/TPMs spend most of their time in. One web part page per milestone. Built on the `DeliveryItems` list, filtered by `milestone_id`.

**Layout:**
- Header row: device name, milestone name, milestone status, target date
- **Grouped by `tg_name`** (HW / SW / QA / …) — for each group, render a **TG header** at the top of the group with metadata from the matching `TGGroups` row (lookup on `(milestone_id, tg_name)`):
  - `tg_owner_name` + `tg_owner_email` (the TG coordinator)
  - `email_group_alias` if set, shown as `Alias: <value>` (else "—")
  - `default_cc_list` size (e.g., "CC: 3 recipients" — click to expand)
  - *(Phase 2)* an inline **"Edit TG metadata"** link that opens the TGGroups list-item edit form per FR-71
- Then the per-item rows under the TG header
- Each row shows: `item_no`, `item_name`, `owner_name`, `delivery_state`, `expected_completion_date`
- Items where `item_type ≠ Confirmation` show a **document section** (see §5)
- Items where `delivery_state = UnderPMReview` show a **PM Review section** (see §6)

**Milestone-level actions at the top:**
- **Start Collection** button — see §4.1
- **Submit to Carrier** button — see §4.3
- **Close All Items** button — see §4.4
- **Refresh** button — see §4.5

**Item-level actions per row:**
- **Send Reminder** button — see §4.6
- **Approve** button — see §4.2 (only when `delivery_state = UnderPMReview`)
- *(Phase 2)* **Upload Document** button — see §4.7
- *(Phase 2)* **Close Item** button — owner self-close

**Last updated indicator** at the top of the view: "Last updated: X min ago" (computed from `milestone.refresh_requested_at` or the most recent `item.last_updated` across the milestone).

### 3.2 Device Tracker View

A roll-up showing all milestones for one device. Built on `Milestones` filtered by `device_id`. Each row shows milestone name, status, target date, target items / completed items count, and a link to the Milestone View (3.1).

### 3.3 PM Dashboard View

Home view per PM. Shows their assigned devices (`Devices` filtered by `assigned_pm_id = current_user`). Each row links to the Device Tracker View (3.2).

### 3.4 *(Phase 2+)* Cross-device matrix / Kanban views

Out of scope for Phase 1. Build the three views above first.

---

## 4. Buttons / Actions — exactly what each does

**The key pattern**: every button click works by **writing a value to a SharePoint List field**. The field-write is what HILDA sees via SP alerts (see §7). The button never directly calls HILDA's HTTP endpoint for command actions — only for **download** (see §5.3) and *(optional)* **status polling** (see §8.2).

For each action: enabled when, what field gets modified, user prompt (if any).

### 4.1 Start Collection (milestone-level)

| Aspect | Detail |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | `milestone.milestone_collection_started_at` is empty AND `milestone.status` ∈ {Not Started, In Progress} |
| **User prompt** | "Start collection for milestone <name>? This will send initial outreach to all R&D owners." (Yes / Cancel) |
| **What happens on click** | Set `Milestones.milestone_collection_started_at = <now>` |
| **What HILDA does next** | (Background — out of your scope) HILDA creates PLM issues per (owner × milestone), fires email outreach to all owners, sets each item's `delivery_state = OutreachSent`. Item rows update via §8 live polling. |

### 4.2 Approve (item-level)

| Aspect | Detail |
|---|---|
| **Where** | Item row in Milestone View; in the PM Review section |
| **Enabled when** | `item.delivery_state = UnderPMReview` |
| **User prompt** | NFR-5 confirmation: "Approve <item_name> for submission?" (Yes / Cancel) |
| **What happens on click** | Set `DeliveryItems.delivery_state = ReadyForSubmission` |
| **Permission** | PM or TPM |

### 4.3 Submit to Carrier (milestone-level)

| Aspect | Detail |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | All items in milestone are in {ReadyForSubmission, SubmittedToCustomer, Closed} AND at least one item is in ReadyForSubmission |
| **User prompt** | NFR-5 confirmation. **Delta-aware** — show: "Re-submitting {N} items with updated documents. {M} previously-submitted items are unchanged and excluded from this package." (Confirm / Cancel) — or for initial submission: "Submit {N} items in milestone <name> to <carrier>?" |
| **What happens on click** | Set `Milestones.milestone_submission_triggered_at = <now>` |
| **What HILDA does next** | (Background) assembles submission package from items currently in ReadyForSubmission only; dispatches to carrier; flips dispatched items to `delivery_state = SubmittedToCustomer`. |

### 4.4 Close All Items (milestone-level)

| Aspect | Detail |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | All items in milestone are in {SubmittedToCustomer, Closed} AND at least one item is in SubmittedToCustomer |
| **User prompt** | "Close all submitted items for milestone <name>?" (Confirm / Cancel) |
| **What happens on click** | Set `Milestones.status` field to indicate close — actual mechanism TBD. Possible: write to a hidden `close_all_triggered_at` field; HILDA flips each item's `delivery_state` to `Closed`. *(Confirm field name with HILDA team.)* |

### 4.5 Refresh (milestone-level — soft refresh)

| Aspect | Detail |
|---|---|
| **Where** | Milestone View top bar |
| **What happens on click** | Set `Milestones.refresh_requested_at = <now>` |
| **What HILDA does next** | (Background) does a soft-poll of email / PLM / NSD to catch any pending inbound traffic; writes any state changes back to the list. View picks up the changes via §8 live polling. |
| **Rate-limit on SP side** | None — rate-limit is HILDA-side. User can click as often as they like; HILDA debounces. |

### 4.6 Send Reminder (item-level)

| Aspect | Detail |
|---|---|
| **Where** | Each item row in Milestone View |
| **Enabled when** | `item.delivery_state` ∉ {OwnerClosed, ReadyForSubmission, SubmittedToCustomer, Closed} |
| **What happens on click** | Set `DeliveryItems.last_reminder_triggered_at = <now>` |
| **What HILDA does next** | (Background) sends an immediate reminder to the owner via all status-capable tracking modalities. |
| **No confirmation prompt needed** | Lightweight; no destructive effect. |

### 4.7 *(Phase 2)* Upload Document (item-level)

| Aspect | Detail |
|---|---|
| **Where** | Each item row in Milestone View (where `item_type ≠ Confirmation`) |
| **Enabled when** | `item.delivery_state` ∈ {DocumentReceived, UnderPMReview, SubmittedToCustomer} |
| **UI flow** | (1) File picker; (2) PM/TPM declares **New document** or **Revision of existing**; if Revision, show dropdown of existing `doc_id_slug`s; (3) Confirm. |
| **What happens on click Confirm** | Upload file to HILDA's upload endpoint at `https://hilda.corp/upload/<delivery_item_id>` *(exact endpoint shape TBD with HILDA team)*. **This is one of the few places where the SP UI calls HILDA directly via HTTPS** (vs writing a field). |
| **State effect** | If `delivery_state` was `SubmittedToCustomer`, it reverts to `UnderPMReview` after upload. Picked up by §8 live polling. |

### 4.8 *(Phase 2)* Close Item (item-level — owner self-close)

| Aspect | Detail |
|---|---|
| **Where** | Each item row when current user is the assigned owner |
| **Enabled when** | `item.delivery_state` ∉ {OwnerClosed, ReadyForSubmission, SubmittedToCustomer, Closed} |
| **What happens on click** | Set `DeliveryItems.delivery_state = OwnerClosed` |

---

## 5. Document section (for items where `item_type ≠ Confirmation`)

### 5.1 Data source

For each applicable item, the document section is populated by calling HILDA's **document enumeration API**:

```
GET https://hilda.corp/docs/<delivery_item_id>
  (Phase 1 default)              → returns one entry per (doc_type, doc_id_slug), always rev1
  (Phase 2 default)              → returns latest revision per (doc_type, doc_id_slug)
  (Phase 2 with ?all_revisions=true) → returns all revision rows ordered by rev_number ascending
```

**Response shape** (JSON):
```json
[
  {
    "doc_type": "test_report",
    "doc_id_slug": "band_1_rf_conformance",
    "rev_number": 1,
    "original_filename": "band1_rf_conformance.pdf",
    "download_url": "https://hilda.corp/dl/<scoped_token>",
    "parser_result": { ... },           // for test_reports only; null otherwise
    "llm_review_findings": { ... }       // null when review_required=false
  },
  ...
]
```

### 5.2 Rendering (per FR-58 / FR-59 / FR-60)

- Group by `doc_type` (test_report / tech_report / waiver)
- One row per document
- Each row shows: `doc_type`, `doc_id_slug` (human-readable), `rev_number`, `original_filename`, download link, parser_result summary (for test reports), llm_review_findings summary
- **View in PLM** link per item — points to `item.actual_item_info` URL
- *(Phase 2)* When multiple revisions exist, show only the latest in the main list; provide an "expand history" toggle that calls the API with `?all_revisions=true`

### 5.3 Download link (per FR-61)

Each document row shows a "Download" link. The href is the `download_url` returned by the enumeration API. The user's browser:
1. Hits `https://hilda.corp/dl/<scoped_token>` directly (HILDA-mediated URL)
2. HILDA streams the file bytes back; browser saves to Downloads
3. **The SP UI does not handle the bytes** — it just renders the link. The user's local browser fetches the file directly from `hilda.corp`.

**Authentication on the download URL**: corp AD; the scoped token authorizes the specific PM for the specific document. No additional SP-side wiring needed.

**`hilda.corp` resolution**: corp IT admin has DNS + reverse-proxy configured so `hilda.corp` reaches HILDA over the corp network. You don't need to worry about how — just use the URLs as returned by the enumeration API.

---

## 6. PM Review section (for items with `delivery_state = UnderPMReview`)

Per FR-56, items in `UnderPMReview` need an extra section showing:
- All received documents (same data as §5 document section, but presented as a review surface)
- Per-document parser_result + llm_review_findings (rendered in a more prominent way than the list view — full text of findings, not just a summary)
- **Approve** button (§4.2)
- *(Phase 2)* Per-document **Override Final** action — TPM picks a different revision as `is_final`. UI: per `doc_id_slug`, a dropdown of revisions; selecting one POSTs to `https://hilda.corp/docs/<delivery_item_id>/set_final` *(endpoint shape TBD)*.

---

## 7. SP Alerts — the channel HILDA uses to learn about your field writes

**Critical**: every button click and every field edit you make to a SP list **MUST** trigger an SP alert email to HILDA's dedicated mailbox. This is the **only** way HILDA learns about SP-side actions.

### 7.1 SP Alert configuration (required deployment step)

For each of the **7 SharePoint Lists** in §2 (Customers, Devices, Milestones, DeliveryItems, Users, PMCredentials, CommunicationLog, **TGGroups**), configure an alert subscription:

*(Note: CommunicationLog is technically append-only and shouldn't need editing, so its alert could be omitted in practice — but configuring it for completeness costs nothing and protects against future field additions. The 7 lists above all need alerts; the new TGGroups list is the critical one for TPM-driven TG metadata overrides per FR-71.)*

- **Subscriber address**: HILDA's dedicated mailbox (provided by HILDA team at deployment time — e.g., `deliverablehub@<corp-domain>`)
- **Send Alerts for These Changes**: **`Anything changes`** — NOT "specific columns." If you restrict to specific columns, HILDA misses field edits.
- **When to send alerts**: Immediately (not daily/weekly digest)
- **Alert format**: standard SP alert email format. HILDA's parser understands the default format — don't try to customize subject/body templates.

### 7.2 What the alert email looks like (FYI, you don't generate these — SP does)

Standard SP alert format. Subject: `Alert_<ListName>_<Suffix> - <ItemTitle>`. Sub-header verb: `has been added` / `has been modified` / `has been deleted`. Body: key:value pairs of fields. Example (real screenshot from prototype):

```
Subject: Alert_Tasks_MMK - CQ&RE (was "DI&RT") test plan
Body:
  Title: CQ&RE (was "DI&RT") test plan
  MinorMilestone: LE-6
  ItemNumber: 5
  ProjectID: 48
  Model: SM-G123U
  TrackingModality: PLM Only
  DeliveryState: Not Started
  DeliveryType: Confirmation (Yes/No)
  TeamName: HW
  ...
```

### 7.3 Why this matters

HILDA's SP-alert parser reads these emails to discover what changed in SP. If alerts are not fired (or restricted to subset columns), HILDA does not learn about the change — even though the field is updated in SP. Result: the workflow stalls silently.

---

## 8. Live updates — how the view stays fresh

### 8.1 The pattern (recommended)

Web part JavaScript polls the **SP REST API** (NOT HILDA) on an interval, fetching the current state of the milestone's items:

```
GET https://<sp-site>/_api/web/lists/getbytitle('DeliveryItems')/items
   ?$filter=milestone_id eq <id>
   &$select=item_id,item_no,item_name,delivery_state,...
```

When the response differs from the cached client-side state, re-render the affected rows in place — no full page reload.

**Recommended poll interval**: 5–10 seconds while the page is in the foreground; longer (30–60s) when backgrounded.

**Why poll SP and not HILDA**: SP REST is corp-to-corp (no firewall issues; sub-second latency). HILDA is on a separate network; the SP UI doesn't talk directly to HILDA for state.

### 8.2 *(Optional)* Status endpoint for in-flight tasks

For long-running operations (e.g., submission package assembly which is minutes-scale), the response to clicking **Submit to Carrier** might want to show progress. If so, the SP UI can poll:

```
GET https://hilda.corp/status/milestone/<id>/submission
```

Returns JSON `{ "phase": "downloading" | "uploading" | "done", "files_done": N, "total_files": M }`.

This is via the same `hilda.corp` reverse-proxy as document downloads — works from SP UI JavaScript in the user's browser. *(Endpoint shape TBD with HILDA team.)*

**Most operations don't need this**; rely on §8.1 SP REST polling for routine status changes.

---

## 9. Permissions / who can do what

| Role | Can edit list items? | Can click action buttons? |
|---|---|---|
| PM (assigned to device) | Yes — all DeliveryItem fields on their devices | All buttons in their assigned milestone |
| TPM | Same as PM | Same as PM |
| Team Lead | Yes — across all devices | All buttons |
| Admin | Yes — all lists | All buttons |
| Viewer / R&D owner / other | Read-only (specific lists / views) | No buttons |

Configure via standard SP list-level permissions + view-level filtering. No custom permission framework.

---

## 10. Phase scoping — what to build now vs later

### Phase 1 (build first — first-customer end-to-end)
- §2.1–§2.8 — provision all **8 SharePoint lists** (Customers, Devices, Milestones, DeliveryItems, Users, PMCredentials, CommunicationLog, **TGGroups**)
- §3.1 Milestone View (with grouping by `tg_name` + TG-group header rows reading from TGGroups via lookup)
- §3.2 Device Tracker View
- §3.3 PM Dashboard View
- §4.1 Start Collection
- §4.2 Approve
- §4.3 Submit to Carrier (with delta confirmation prompt)
- §4.4 Close All Items
- §4.5 Refresh
- §4.6 Send Reminder
- §5 Document section + download links (Phase 1: single revision per document only — don't worry about revision history UI)
- §6 PM Review section (Phase 1: Approve only; no Override Final yet)
- §7 SP Alert configuration on all 8 lists (including TGGroups)
- §8.1 Live SP REST polling
- §9 Permissions

### Phase 2 (after Phase 1 lands with first customer)
- §4.7 Upload Document
- §4.8 Close Item (owner self-close)
- §5.2 Expandable revision history (`?all_revisions=true`)
- §6 Override Final revision UI
- §8.2 In-flight status polling for submission assembly progress
- **TGGroups inline edit link in §3.1** ("Edit TG metadata" — opens the TGGroups list-item form for TPM to override `tg_owner_email`, `email_group_alias`, `corp_id_list`, `default_cc_list` before ODF fires per FR-71)

### Phase 3+ (deferred)
- Cross-device matrix / Kanban views
- Self-service template creation wizard
- Credential management UI (`PMCredentials` list editing)
- Carrier feedback inbox UI

---

## 11. Out of scope — explicitly NOT to build

- **SharePoint Document Libraries** — HILDA stores all files on a separate corp network share (NSD), not in SP. Don't add Document Library uploads to any list.
- **Workflows beyond SP alerts** — no SharePoint Designer workflows, no Power Automate. The mechanism is SP alerts only.
- **Power Apps / SPFx modern web parts** — vanilla SP 2017 classic only.
- **Email composition UI** — HILDA handles all outbound email; users don't compose emails in SP.
- **Custom auth / SSO bridges** — corp AD is the only auth.
- **Direct PLM / messenger / customer-system UI** — HILDA handles all external system integration; SP UI never talks to those directly.
- **CustomerTemplates list / AutomationRules list** — these are NOT in SharePoint. They are YAML files on the HILDA server, managed by ops.

---

## 12. Contract with HILDA — your hand-off

HILDA team owns:
- The dedicated mailbox that receives SP alerts
- The `hilda.corp` reverse-proxy routing (for `/dl/*`, `/docs/*`, `/status/*`, `/upload/*` endpoints)
- All Python services that consume SP data and write back

SP UI team (you) owns:
- The **8 SharePoint Lists** (column definitions per §2; create the lists with these internal names exactly — including the new TGGroups list per §2.8)
- All views per §3 (including TG-group header rendering above each `tg_name` grouping in §3.1, populated by lookup to TGGroups)
- All buttons per §4 (each writes to a SP field — no direct HILDA HTTP calls for state actions)
- The SP-side alert configuration per §7 (this is a SP deployment step, not code) — covering all 8 lists
- The document section and PM Review section per §5 and §6 (which call HILDA's enumeration API for data + render HILDA-mediated download links)

### Open items to confirm with HILDA team

1. **Field name** for the `milestone_gating` boolean — your prototype uses `MilestoneGating`; HILDA's canonical schema (post-2026-05-24 review) is to be confirmed. If different, agree on the SP-side name before you build the list.
2. **Field name mapping** — your prototype may use different column internal names than HILDA's canonical schema (e.g., `ItemNumber` vs `item_no`, `TeamName` vs `tg_name`, `DeliveryType` vs `item_type`). Either align prototype to canonical names OR HILDA's parser adds a name-mapping translation layer. Agree on one approach before list creation.
3. **`Close All Items` field name** (§4.4) — TBD which field gets written on the click.
4. **Override Final endpoint** (§6 Phase 2) — `POST https://hilda.corp/docs/<delivery_item_id>/set_final` shape TBD.
5. **Status endpoint** (§8.2) — `GET https://hilda.corp/status/milestone/<id>/submission` shape TBD.
6. **Upload endpoint** (§4.7 Phase 2) — `POST https://hilda.corp/upload/<delivery_item_id>` shape TBD.
7. **TGGroups list (§2.8)** — added 2026-05-26. Confirm with HILDA team: (a) `corp_id_list` and `default_cc_list` JSON shape; (b) whether `(milestone_id, tg_name)` unique constraint is enforced SP-side or HILDA-side; (c) the auto-population mechanism at tracker creation — does HILDA push these rows via REST after reading the customer template YAML, or does the SP UI provide a pre-creation hook? Most likely answer: HILDA pushes via REST after tracker creation, same as for Devices / Milestones / DeliveryItems.

---

## Quick reference summary

| What you build | Where it lives | What it writes / calls |
|---|---|---|
| 8 SharePoint Lists (incl. TGGroups per §2.8) | Corp SP site | — (data store) |
| 3 views (milestone / device / dashboard) | SP web parts | Reads SP lists |
| Action buttons | SP web parts | Writes SP list fields (which trigger SP alerts → HILDA) |
| Document section | SP web parts | Calls `GET https://hilda.corp/docs/<item_id>` |
| Download links | SP web parts (just link rendering) | User's browser → `https://hilda.corp/dl/<token>` |
| Live status polling | SP web part JavaScript | Polls SP REST API (NOT HILDA) |
| SP Alert subscriptions | SP deployment configuration | (no code; setup step) |

If a feature isn't in this document, it's HILDA's side — not yours.

**Questions / clarifications**: contact the HILDA team.
