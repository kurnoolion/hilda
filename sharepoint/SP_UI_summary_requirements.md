# SP UI Engineer — Condensed Requirements Summary

> **Status**: 2026-06-12 compliant. Source: Google Docs condensed summary (link
> below) made compliant with `sharepoint/REQUIREMENTS.md` as of commits
> `4da9900` + `48f884c` + `972a1bb`. **For full details see
> [`sharepoint/REQUIREMENTS.md`](./REQUIREMENTS.md)** — section anchors below
> reference it.
>
> **Source doc**: https://docs.google.com/document/d/1D4GEQ-O-DnILhRGWW5ISL0EfNmgVEVfGjli0T7tf4X4

---

## 0. Architectural framing (must read first — 2026-06-12 changes)

**Two architectural decisions from 2026-06-12 shape what you build:**

### 0.1 You manually create the SP lists (D-073)

For each customer deployment, **YOU read** two artifacts and **hand-create** the SP lists + columns in SP UI directly:

- `docs/sp_ui_engineer/HILDA_SP_Schema.xlsx` — column-level detail per list (type, Choice values, Phase, Required, Source FR/Decision, Notes)
- `customizations/sharepoint_config/customers/<customer_slug>.yaml` — the canonical → SP-internal column-name mapping HILDA's REST writeback needs at runtime

You also configure: SP-alert subscriptions per §7, any custom SP workflows / custom field types, column-level permissions per §9. HILDA does NOT call REST to create lists — corp SP 2017's SP-alert + custom-task requirement structurally requires UI-side provisioning. See `REQUIREMENTS.md §0.1` for full rationale.

### 0.2 SP renders link-out; HILDA renders the document UI (D-074)

You do **NOT** need any JS that fetches HILDA. No CORS, no cross-origin XHR, no retry/fallback logic. SP web parts render bare anchor tags:

```html
<a href="https://hilda-proxy.corp/docs/<delivery_item_id>" target="_blank">View Documents</a>
```

TPM clicks → browser opens new tab → corp reverse proxy forwards → HILDA's `dashboard` module server-side-renders the document section as HTML with embedded short-lived download tokens. TPM clicks a download link inside HILDA's tab → browser GETs `/dl/<scoped_token>` → HILDA streams the file. **Two clicks per download** (open HILDA tab + click download). Single-click is not architecturally achievable in this corp environment (SP-page-JS cross-origin fetch is blocked by corp policy — tested + confirmed). Auth on both endpoints is Windows Integrated (Kerberos/SPNEGO) — auto-attached by browser; requires `hilda-proxy.corp` in TPMs' Local Intranet zone.

### 0.3 7 SP Lists (was 8; TGGroups removed 2026-06-12 per `[D-051]` impl note)

You provision **7** lists: `Customers`, `Devices`, `Milestones`, `DeliveryItems`, `Users`, `PMCredentials`, `CommunicationLog`. The 8th list `TGGroups` was REMOVED — its 9 TG metadata fields (`ingress_nsd`, `tracking_modality_tg`, `email_group_alias`, `tg_owner_name`, `tg_owner_email`, `default_cc_list`, `folder_routing_enabled`, `tracking_enabled`, plus the existing `tg_name`) are DENORMALIZED onto each `DeliveryItems` row as SP-side **read-only** display mirrors. YAML is source-of-truth; **TPM SP UI must NOT allow editing TG columns on DI rows** (would diverge from siblings + YAML).

---

## 1. HILDA provides YAML/Excel files to SP UI engineer with schema for all 7 lists

Per §0.1 above — the artifacts are `HILDA_SP_Schema.xlsx` (comm-channel partner) and `customers/<customer_slug>.yaml` (mapping HILDA uses at runtime).

## 2. Tasks get loaded on setup-milestone — TPM can edit values before collection / tracking kickoff

Pre-kickoff TPM edits hit the SP list directly. HILDA picks them up via SP-alert email on the affected field changes per §7 of `REQUIREMENTS.md`.

## 3. Any correction to task — SP-Email alert triggered to HILDA

Per `[D-047]` SP-alert email is the ONLY SP→HILDA notification channel (HTTP inbound is firewall-blocked). Configure SP alert subscription on each of the 7 lists with "Send Alerts for These Changes: Anything changes" per `REQUIREMENTS.md §7.1`.

## 4. Milestone-level actions at the top

### Start Collection button

| | |
|---|---|
| **Where** | Milestone View top bar |
| **Enabled when** | `milestone.milestone_collection_started_at` is empty AND `milestone.status` ∈ {Not Started, In Progress} |
| **User prompt** | "Start collection for milestone `<name>`? This will send initial outreach to all R&D owners." (Yes / Cancel) |
| **What happens on click** | Set `Milestones.milestone_collection_started_at = <now>` |
| **What HILDA does next** | (Background — out of your scope) HILDA creates PLM issues per (owner × milestone), fires email outreach to all owners, sets each item's `delivery_state = OutreachSent`. Item rows update via §8 live polling. |

### Submit to Carrier button

| | |
|---|---|
| **Enabled when** | All items in milestone are in {ReadyForSubmission, SubmittedToCustomer, Closed} AND at least one item is in ReadyForSubmission |
| **User prompt** | Delta-aware: "Re-submitting `{N}` items with updated documents. `{M}` previously-submitted items are unchanged and excluded from this package." (Confirm / Cancel) — or for initial submission: "Submit `{N}` items in milestone `<name>` to `<carrier>`?" |
| **What happens on click** | Set `Milestones.milestone_submission_triggered_at = <now>` |
| **What HILDA does next** | (Background) assembles submission package from items currently in ReadyForSubmission only; dispatches to carrier; flips dispatched items to `delivery_state = SubmittedToCustomer`. |

### Close All Items button

| | |
|---|---|
| **Enabled when** | All items in milestone are in {SubmittedToCustomer, Closed} AND at least one item is in SubmittedToCustomer |
| **User prompt** | "Close all submitted items for milestone `<name>`?" (Confirm / Cancel) |
| **What happens on click** | **Set `Milestones.close_all_items_triggered_at = <now>`** *(field name resolved 2026-06-12 per `REQUIREMENTS.md §2.3` — SP-side ONLY field, no HILDA Postgres mirror)*. HILDA reads the SP-alert per `[D-047]` and flips each `SubmittedToCustomer` item's `delivery_state` to `Closed`. |

### Refresh button

| | |
|---|---|
| **What happens on click** | Set `Milestones.refresh_requested_at = <now>` |
| **What HILDA does next** | (Background) does a soft-poll of email / PLM / NSD to catch any pending inbound traffic; writes any state changes back to the list. View picks up the changes via §8 live polling. |
| **Rate-limit on SP side** | None — rate-limit is HILDA-side. User can click as often as they like; HILDA debounces. |

## 5. Item-level actions per task

### Send Reminder button

| | |
|---|---|
| **Enabled when** | `item.delivery_state` ∉ {OwnerClosed, ReadyForSubmission, SubmittedToCustomer, Closed} |
| **What happens on click** | Set `DeliveryItems.last_reminder_triggered_at = <now>` |
| **What HILDA does next** | (Background) sends an immediate reminder to the owner via all status-capable tracking modalities. |
| **No confirmation prompt needed** | Lightweight; no destructive effect. |

### Approve button — (only when `delivery_state = UnderPMReview`)

| | |
|---|---|
| **Enabled when** | `item.delivery_state = UnderPMReview` |
| **User prompt** | NFR-5 confirmation: "Approve `<item_name>` for submission?" (Yes / Cancel) |
| **What happens on click** | Set `DeliveryItems.delivery_state = ReadyForSubmission` *(plus HILDA-side will populate `pm_approval_at` / `pm_approval_pm_id` per `[D-068]` — SP UI does not write these directly; HILDA's workflow_engine writes them BEFORE the state transition fires)* |
| **Permission** | PM or TPM |

### *(Phase 1, FR-87 strict A→B→C — see `REQUIREMENTS.md §4.9 / §4.10 / §4.11`)* TPM-resolution buttons

For documents in `nsd_path_type ∈ {unrouted, staged_not_classified, staged_not_revision}`, surface the corresponding button inline on the document row (see §6.2 below):

- **§4.9 Reassign Work-Item** — sets `DeliveryItems.tpm_reassignment_target_item_id` (FR-87 step A)
- **§4.10 Resolve doc_type** — sets `DeliveryItems.tpm_resolved_doc_type` (FR-87 step B; Choice values: `test_report` / `tech_report` / `waiver` / `compliance_certification_release_notes`)
- **§4.11 Resolve revision** — sets `DeliveryItems.tpm_revision_resolution` (FR-87 step C)

HILDA's `sp_alert_parser` per `REQUIREMENTS.md §7.4` infers action_type from the changed field name and routes to the right tpm_resolve_* storage API.

## 6. Item-view — document section

> **2026-06-12 IMPORTANT (D-074)**: The document section is NOT rendered by SP UI. SP renders a per-item **link-out** anchor:
>
> ```html
> <a href="https://hilda-proxy.corp/docs/<delivery_item_id>" target="_blank">View Documents</a>
> ```
>
> Clicking opens a new tab to HILDA's `dashboard` module, which server-side-renders the document list as HTML (using the schema below internally). **Two clicks per download** (open HILDA tab + click download link). No SP-side JS fetch; no CORS.
>
> The JSON shape below is documented for context (it's the data the dashboard renderer works against; you do NOT consume it directly).

### 6.1 GET /docs/<delivery_item_id> response — INTERNAL contract for HILDA's dashboard renderer

```json
[
  {
    "doc_type": "test_report",          // 5-value: test_report | tech_report | waiver | compliance_certification_release_notes | unresolved
    "doc_id_slug": "band_1_rf_conformance",  // null when nsd_path_type ∈ {staged_not_revision, staged_not_classified, unrouted}
    "rev_number": 1,                     // null when doc_id_slug null
    "original_filename": "band1_rf_conformance.pdf",
    "download_url": "https://hilda.corp/dl/<scoped_token>",  // 300s TTL; generated at HTML-render time; never stored
    "parser_result": { ... },           // for doc_type=test_report only; null otherwise
    "llm_review_findings": { ... },      // null when review_required=false OR doc_type ∈ {compliance_certification_release_notes, unresolved}
    "inferred_tg_name": "HW",            // per [D-060] — channel-resolved TG; shown when doc is on the default work-item (unrouted)
    "nsd_path_type": "classified"        // one of: classified | staged_not_classified | staged_not_revision | unrouted; drives FR-87 button visibility
  },
  ...
]
```

### 6.2 Per-document rendering — handled by HILDA's dashboard renderer (NOT SP)

When TPM lands on `https://hilda-proxy.corp/docs/<delivery_item_id>`, HILDA renders:

- Group by `doc_type` (5-value)
- One row per document: `doc_type`, `doc_id_slug` (or "—"), `rev_number` (or "—"), `original_filename`, `parser_result` summary (test_report only), `llm_review_findings` summary (test/tech/waiver only), `inferred_tg_name` (unrouted only), `nsd_path_type` badge, download link
- For `nsd_path_type ∈ {staged_not_classified, staged_not_revision, unrouted}` — surface the corresponding FR-87 button (§4.9 / §4.10 / §4.11) inline

**Why this is HILDA-side**: per D-074, SP-page-JS cannot reach HILDA (corp policy blocks cross-origin XHR); the rendering moved to HILDA so SP just navigates to it.

### 6.3 View in PLM link — per item

Renders SP-side from `DeliveryItems.actual_item_info` (the PLM issue URL). Null for documents in staged paths since PLM upload happens only after FR-86 classified state is reached.

## 7. Last updated indicator at the top of the view

"Last updated: X min ago" — computed from `Milestones.refresh_requested_at` OR the most recent **SP built-in `Modified`** timestamp across DeliveryItems rows in the milestone.

> **2026-06-12 change**: the custom `last_updated` field was DROPPED — use SP's built-in `Modified` field. SP system columns (`Created` / `Modified` / `Author` / `Editor`) MUST NOT be duplicated as custom columns.

## 8. Views

### 8.1 Milestone View (primary)

One web part page per milestone. Built on the `DeliveryItems` list filtered by `milestone_id`.

**Layout**:
- Header: device name, milestone name, milestone status, target date
- **Grouped by `tg_name`** — for each TG group, render a **TG header** with metadata **READ FROM THE DENORMALIZED TG COLUMNS on the first DeliveryItem row in the group** (per `[D-051]` impl note 2026-06-12 — all DI rows in the same TG carry identical values for the 9 TG columns; reading from the first row in the group is equivalent to a lookup):
  - `tg_owner_name` + `tg_owner_email`
  - `email_group_alias` if set (else "—")
  - `default_cc_list` size (e.g., "CC: 3 recipients" — click to expand)
- Then per-item rows under the TG header
- Each row shows: `item_no`, `item_name`, `owner_name`, `delivery_state`, `expected_completion_date`
- Items where `item_type ≠ Confirmation` show a **"View Documents"** link per §6 (link-out to HILDA-rendered page)
- Items where `delivery_state = UnderPMReview` show an **Approve** button per §5

### 8.2 Device Tracker View

A roll-up showing all milestones for one device. Built on `Milestones` filtered by `device_id`. Each row shows milestone name, status, target date, target items / completed items count, and a link to the Milestone View.

### 8.3 PM Dashboard View

Home view per PM. Shows their assigned devices (`Devices` filtered by `assigned_pm_id = current_user`). Each row links to the Device Tracker View.

---

## What's NOT in this summary (see full `REQUIREMENTS.md` for)

- Full SP list schemas per §2 (column-by-column tables)
- §6 PM Review section UX
- §7 SP Alert email format + §7.4 action verb conventions
- §8 Live updates polling pattern + status endpoint
- §9 Permissions matrix
- §10 Phase 1 / Phase 2 / Phase 3+ scope split
- §11 Out of scope
- §12 Contract with HILDA — your hand-off

## Reference

- **Full requirements**: [`sharepoint/REQUIREMENTS.md`](./REQUIREMENTS.md)
- **Excel comm channel**: `docs/sp_ui_engineer/HILDA_SP_Schema.xlsx`
- **Canonical YAML mapping**: `customizations/sharepoint_config/customers/example.yaml`
- **Architectural decisions cited**: `[D-006]` (Kerberos auth), `[D-047]` (SP-alert email channel), `[D-051]` (amended 2026-06-12 → 7-list), `[D-053]` (4-value ItemType / 5-value DocType), `[D-060]` (inferred_tg_name), `[D-064]` (HILDA→SP REST writeback), `[D-065]` (SP UI engineer owns Choice values), `[D-068]` (PM approval field), `[D-073]` (SP UI engineer provisions lists), `[D-074]` (SP renders link-out; HILDA renders pages) — see `docs/compact/DECISIONS.md`
