# SharePoint UI Requirements — HILDA

> **Status:** Initial draft 2026-05-26 + 2026-06-09 cascade Group 5 + **2026-06-12 SP UI engineer review absorption (D-073 / D-074 / `[D-051]` impl note)**. 2026-06-12 changes:
> - **List count 8 → 7**: `TGGroups` REMOVED as a separate SP list (per `[D-051]` impl note 2026-06-12 / D-DRAFT-Y); 9 TG metadata fields are DENORMALIZED onto each `DeliveryItems` row as SP-side READ-ONLY display mirrors (write-once at DI creation by HILDA from customer YAML via `[D-064]` writeback). §2.8 is now an "OBSOLETE" reference; §2.4 carries the denormalized TG columns; §3.1 TG-header rendering reads from the DI rows directly (no separate lookup).
> - **SP UI engineer manually provisions SP lists** per **D-073** (corp SP-2017 SP-alert + custom-SP-task constraint that REST cannot express). HILDA does NOT call REST to create lists; `sharepoint_integration` REST writeback per `[D-064]` is unchanged (HILDA writes existing rows in already-provisioned lists). Customer onboarding gains an explicit SP UI engineer ceremony step. See §0 (added below).
> - **SP↔HILDA integration via Variant A** per **D-074** — HILDA's `dashboard` module server-side-renders document pages; SP web parts render link-out (`<a href="https://hilda-proxy.corp/docs/<delivery_item_id>" target="_blank">`); browser navigates top-level (no SP-side cross-origin XHR — that path is blocked by corp policy in our environment). Two clicks per download (open HILDA tab + click download). §5.1 / §5.3 reworded accordingly.
> - **Schema deltas absorbed**: `milestone_gating` → `is_milestone_gating` (§2.4); ADD `pm_approval_at` + `pm_approval_pm_id` per `[D-068]` (§2.4); ADD `owner_corp_id` on DeliveryItems (denormalized) + Users (canonical) (§2.4 / §2.5); DROP `last_updated` column (use SP built-in `Modified`) (§2.4); ADD `close_all_items_triggered_at` on Milestones (§2.3 / §4.4); `default_work_item_config` is NOT a SP column (lives in YAML; §2.3 note); DROP `source_system` from CommunicationLog (channel column covers it; §2.7); ADD `milestone_id` / `tg_name` / `credential_id` on CommunicationLog (Ph-1; were already on storage's `CommunicationLogRow`); ADD `owner_corp_id` + `TPM` role on Users (§2.5); `ingress_nsd` Choice updated to `none / nsd1 / nsd2` (§2.4 denormalized + §2.8 OBSOLETE); SP system columns (`Created` / `Modified` / `Author` / `Editor`) NOT duplicated as custom columns (§2.4 / §2.5 notes); `item_no` declared IMMUTABLE per item lifetime (referential integrity for FR-77 + storage.TGFolderRoutingRow; §2.4 note).
>
> Original status preceding 2026-06-12 amendments: 11-group update pass applied 2026-06-09 (list count 6→8 at that time; `item_type` Choice 6→4 per `[D-053]`; `customer_delivery_modality` Choice updated for `[D-054]` GoogleDrive; 5 new DeliveryItems fields incl. `target_folder` / `no_customer_upload` / 3 TPM-resolution fields per FR-87; 3 new TGGroups fields then; 3 new §4.9–§4.11 TPM-resolution buttons FR-87 strict A→B→C; §5 document section updated for 5-value DocType + `nsd_path_type` + `inferred_tg_name`; new §7.4 SP-alert action-verb mapping; §10 Phase-1 scope updated; §12 open items resolved/added).

**Audience**: SP UI engineer building HILDA's SharePoint 2017 web parts.

**Scope**: This document is everything you need to develop the SharePoint side of HILDA. It does **not** cover the Python services that consume SP data (that side is independently developed; you don't need to know its internals).

**Stack constraint**: SharePoint 2017 on-prem. **Vanilla SharePoint Lists + classic web parts only.** No SPFx modern, no Power Apps, no Office 365 features.

**Authentication**: PM/TPM browsers are already authenticated to corp SP via on-prem AD (NTLM/Kerberos). No SSO bridge needed for SP itself.

---

## 0. How you and HILDA work together (added 2026-06-12 per D-073 / D-074)

**Two new architectural decisions land here as a section because they shape EVERYTHING below — your role, your inputs, your outputs, your integration touchpoints with HILDA.**

### 0.1 You manually create the SP lists (D-073)

For each customer deployment:

1. **You read two artifacts** at deployment time:
   - `docs/sp_ui_engineer/HILDA_SP_Schema.xlsx` (the authoritative comm channel — column-level detail per list, type, Choice values, Phase, Required, Source FR/Decision, Notes)
   - `customizations/sharepoint_config/customers/<customer_slug>.yaml` (the canonical → SP-internal column-name mapping HILDA's REST writeback needs at runtime)
2. **You create the SP lists + columns in SP UI directly** — by hand, in the SharePoint web UI. Including: alert subscriptions per §7.1, any custom SP workflows / custom field types, the column-level permissions noted in §9.
3. **You assign SP internal column names** as you create each column. SP auto-generates them from your display names (typically `Display_x0020_Name`). Record the SP internal names back in the customer YAML so HILDA can address them via REST.
4. **HILDA never calls REST to create lists**. `sharepoint_integration` REST writeback per `[D-064]` is unchanged from §7 — HILDA writes existing rows in already-provisioned lists, but does NOT create lists or columns programmatically.

Why this ceremony exists: corp SP 2017's SP-alert email triggers + custom SP tasks (workflows, custom field types, alert-trigger configuration) cannot be expressed via the REST API surface HILDA uses. SP-side provisioning is structurally necessary. HILDA's `tracker` module assumes SP lists pre-exist; if you haven't provisioned them at deployment time, HILDA won't auto-recover. Any YAML change to the canonical-field set (new fields, renames, removals) requires you to manually update SP lists before HILDA can write to them — coordinate through the HILDA_SP_Schema.xlsx comm channel per `[D-065]`.

### 0.2 SP-side UI talks to HILDA via top-level browser navigation, NOT cross-origin XHR (D-074)

Your SP web parts (classic content editor / script editor, or SPFx) render **link-out anchors** to HILDA's `dashboard` module:

```html
<a href="https://hilda-proxy.corp/docs/<delivery_item_id>" target="_blank">View Documents</a>
```

The TPM clicks → browser opens a new tab → corp reverse proxy forwards → HILDA server-side-renders the document section as HTML (Jinja2 templates; doc list + per-doc download links embedded with short-lived scoped tokens). TPM clicks a download link inside HILDA's tab → browser does its own direct GET to `/dl/<scoped_token>` → HILDA streams the file with the right `Content-Disposition` per §5.3.

**Two clicks** total per download (open HILDA tab + click download). Auth on both endpoints is Windows Integrated (Kerberos / SPNEGO) — auto-attached by browser; requires `hilda-proxy.corp` to be in TPMs' Local Intranet zone via group policy. **Single-click download is not architecturally achievable** in this corp environment.

**Why this pattern**: SP web-part JS `fetch()` to HILDA — cross-origin XHR with `credentials: 'include'` — was tested and fails in our corp environment. Likely cause is corp SharePoint farm's Content Security Policy (`connect-src` restricted to SP origins only) or corp network ACL blocking SP-origin browser tabs from reaching `hilda.corp` directly. Either way the JS-fetch path is structurally closed for this deployment. Iframe embedding (variant β) is reserved as a possible Ph-2 polish if "new tab" UX proves disruptive. Top-level navigation works through any corp policy because the browser is doing the GET directly, not via an SP-page-JS XHR.

Implication for your build: **you do NOT need any JS that fetches HILDA**. No CORS configuration on your side. No retry/fallback logic. You render bare `<a href>` anchors. HILDA does all the rendering on its side.

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

HILDA's data lives in **7 SharePoint Lists** within a dedicated DeliverableHub SP site (revised 2026-06-12 from 8 per `[D-051]` impl note 2026-06-12 / D-DRAFT-Y — `TGGroups` removed as separate list; 9 TG fields denormalized onto DeliveryItems as SP-side read-only display mirrors). Column names and types below — please use these **internal names exactly** so HILDA's REST API client can read/write them without per-deployment translation.

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
| `close_all_items_triggered_at` | DateTime | nullable — set by **Close All Items** action per §4.4. **SP-side ONLY field** (no HILDA Postgres mirror — SP is system-of-record for this audit timestamp). Added 2026-06-12 per SP UI engineer 2026-06-10 review. |

> **NOTE (added 2026-06-12)**: `default_work_item_config` is NOT a SP column on Milestones. It's customer-deployment-fixed config that lives in `customizations/template_schemas/<customer_slug>/milestones.yaml` per FR-78 / `[D-053]`. Not TPM-editable.

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
| `item_type` | Choice | **Confirmation / TEST_TECH_WAIVER_REPORT / COMPLIANCE_CERTIFICATION_RELEASE_NOTES / Default** (revised 2026-06-09 per `[D-053]` impl note 2026-06-08 — collapsed 6-value → 4-value; prior values `TestReport`/`TechReport`/`Waiver` bundled into `TEST_TECH_WAIVER_REPORT`; `CompletionPct`/`SoftwareBinary` removed as vestigial; `Default` is the auto-instantiated default work-item per FR-78) |
| `owner_name` | String | |
| `owner_email` | String | |
| `tracking_modality` | Choice (multi-select) | Email / CorporateMessenger / CorporatePLM / NetworkSharedDrive / CustomerJIRA — this is **multi-value** (an item can be tracked via multiple modalities) |
| `actual_item_info` | URL | PLM issue URL (set by HILDA on first document arrival) |
| `plm_id` | String | PLM issue ID (e.g. "PROJ-1234") |
| `handset`, `tablet`, `wearable`, `mr`, `hmr_smr` | Boolean each | form factor flags |
| `customer_delivery_modality` | Choice | **None / Email / CustomerTrackingSystem / GoogleDrive** (revised 2026-06-09 per `[D-054]` Ph-1/Ph-2 — `OurFileStorage` removed as too-generic; `GoogleDrive` added — Ph-3+ adds `WebPortal` + `CustomerJiraPortal`) |
| `customer_delivery_info` | String | email address / credential ID / empty |
| `owner_status_note` | Multi-line text | latest interim status |
| `comment` | Multi-line text | |
| `doc_count` | Integer, default 1 | number of test reports expected before DocumentReceived |
| `review_required` | Boolean, default No | gates LLM quality review |
| `review_status` | Choice | pending / complete / not_required |
| `item_completion_pct` | Integer (computed/read-only) | document-review completion % |
| `email_cc_list` | Multi-line text | JSON array — per-item CC override |
| `is_milestone_gating` | Boolean | does this item gate milestone closure (FR-64 Close All Items enablement)? Renamed 2026-06-12 from `milestone_gating` per SP UI engineer 2026-06-10 review. |
| `last_owner_contacted` | DateTime | nullable |
| `last_reminder_triggered_at` | DateTime | nullable — set by **Send Reminder** action |
| `target_folder` | String | nullable — **[Ph-1]** FR-77 OUTBOUND customer-portal upload destination per `[D-054]` (e.g., Google Drive folder path on the carrier side). Distinct from INBOUND ingress paths. Added 2026-06-09. |
| `no_customer_upload` | Boolean, default No | **[Ph-1]** FR-80 — when Yes, item is excluded from carrier upload (TPM-handled instead per FR-7 TPM-Mark-Closed manual path). Added 2026-06-09. |
| `tpm_reassignment_target_item_id` | String | nullable — **[Ph-1]** FR-87 step A field — TPM sets via §4.9 Reassign Work-Item button on unrouted docs; HILDA reads via SP alert per `tpm_reassign_to_workitem` action verb. Added 2026-06-09. |
| `tpm_resolved_doc_type` | Choice | nullable — **[Ph-1]** FR-87 step B field — TPM sets via §4.10 Resolve doc_type button; values `{test_report, tech_report, waiver, compliance_certification_release_notes}`; HILDA reads via SP alert per `tpm_resolve_doc_type` action verb. Added 2026-06-09. |
| `tpm_revision_resolution` | String | nullable — **[Ph-1]** FR-87 step C field — TPM sets via §4.11 Resolve revision button; values `NEW` or existing `<doc_id_slug>`; HILDA reads via SP alert per `tpm_resolve_revision` action verb. Added 2026-06-09. |
| `sort_order` | Integer, required | display order within milestone |
| `pm_approval_at` | DateTime | nullable — **[Ph-1]** `[D-068]` PM-approval recording. Set by HILDA's workflow_engine PMApproval task body BEFORE transitioning item to `ReadyForSubmission`; cleared on `[D-067]` rewind path. **Configure SP alert subscription on this field change** (HILDA fires the PMApproval trigger on the alert). Added 2026-06-12. |
| `pm_approval_pm_id` | User (Person/Group field) | nullable — **[Ph-1]** `[D-068]` — PM/TPM attribution for the PMApproval action. Cleared together with `pm_approval_at`. Added 2026-06-12. |
| `owner_corp_id` | String | nullable — **[Ph-1]** denormalized from `Users.owner_corp_id` at DI creation per D-073 (HILDA mirrors at write time). Reused by corp messenger Ph-2 (replaces dropped `user_corp_messenger_handle`). Added 2026-06-12. |

**Denormalized TG fields** (added 2026-06-12 per `[D-051]` impl note 2026-06-12 / D-DRAFT-Y) — `TGGroups` is no longer a separate SP list; these 9 fields are SP-side READ-ONLY display mirrors of TG metadata sourced from `customizations/template_schemas/<customer_slug>/tg_groups.yaml`. HILDA writes them once at DI creation via `[D-064]` writeback; **TPM SP UI MUST NOT allow editing these columns on DI rows** (would diverge from siblings in the same TG and from YAML source-of-truth). For per-TG TG-level editing in Ph-2, see deferred items in §10 (TG-level editing is gone in Ph-1 because TGGroups list is gone).

| Internal name | Type | Notes |
|---|---|---|
| `ingress_nsd` | Choice (`none` / `nsd1` / `nsd2`) | **[Ph-1]** Choice values updated 2026-06-12 per SP UI engineer 2026-06-10 (was `NSD1` / `NSD2`; `none` added). Denormalized from TGGroupBase. |
| `tracking_modality_tg` | Choice (multi-select) | **[Ph-1]** TG-level default tracking modality (Email / CorporateMessenger / CorporatePLM / NetworkSharedDrive / CustomerJIRA). DISTINCT column name from per-item `tracking_modality` above. Denormalized from TGGroupBase. |
| `email_group_alias` | String | **[Ph-1]** Denormalized from TGGroupBase. Nullable. |
| `tg_owner_name` | String | **[Ph-1]** Denormalized from TGGroupBase. TG coordinator name. |
| `tg_owner_email` | String | **[Ph-1]** Denormalized from TGGroupBase. |
| `default_cc_list` | Multi-line text (JSON list) | **[Ph-1]** Denormalized from TGGroupBase. Per-TG default CC. |
| `folder_routing_enabled` | Boolean, default No | **[Ph-1]** FR-77 Type-2 enablement. Denormalized from TGGroupBase. |
| `tracking_enabled` | Boolean, default Yes | **[Ph-1]** FR-81 enablement. Denormalized from TGGroupBase. |
| `corp_id_list` | Multi-line text (JSON list) | **[Ph-2]** Denormalized from TGGroupBase. Deferred Ph-2 per SP UI engineer 2026-06-10. |

> **NOTE (added 2026-06-12)**: `last_updated` was removed as a custom column 2026-06-12 — use SP's built-in `Modified` field instead. SP system columns (`Created` / `Modified` / `Author` / `Editor`) MUST NOT be duplicated as custom columns — discipline added per SP UI engineer 2026-06-10 review.
>
> **`item_no` IMMUTABILITY (added 2026-06-12)**: `item_no` is **immutable per item lifetime** per template_schema/MODULE.md Invariant 2026-06-12. SP UI reorder operations MUST NOT mutate `item_no` (user-visible reordering is `sort_order`'s job). Referential integrity for FR-77 folder routing + storage.TGFolderRoutingRow.item_no FK depends on this.

Unique constraints: `(milestone_id, item_name)`, `(milestone_id, item_no)`

### 2.5 List: `Users`

| Internal name | Type | Notes |
|---|---|---|
| `user_id` | String (auto) | PK |
| `display_name` | String, required | |
| `email` | String, required, unique | corp email; used for SSO matching |
| `owner_corp_id` | String | nullable — **[Ph-1]** canonical per-person corp ID. Denormalized onto DeliveryItems at DI creation per D-073. Reused by corp messenger Ph-2 (replaces dropped `user_corp_messenger_handle` per user 2026-06-12). Added 2026-06-12. |
| `role` | Choice | PM / TPM / TeamLead / Admin |
| `is_active` | Boolean, default Yes | |

> **NOTE (added 2026-06-12)**: SP system columns (`Created` / `Modified` / `Author` / `Editor`) MUST NOT be duplicated as custom columns — discipline added per SP UI engineer 2026-06-10 review.

### 2.6 List: `PMCredentials` (metadata only — actual secrets stored elsewhere by HILDA)

| Internal name | Type | Notes |
|---|---|---|
| `credential_id` | String (auto) | PK |
| `user_id` | Lookup → Users | required |
| `system_type` | Choice | InternalIssueTracker / CustomerJira / CustomerPortal / etc. **Note 2026-06-09: PMCredentials list is Phase 3+ (deferred); when built, align values exactly with HILDA's `credential_service.SystemType` enum (per `[D-019]`) — current values are `issue_tracker / messenger / customer / email / sharepoint / llm_ollama_a4000 / llm_vllm_dgx / llm_corp_llm` per `[D-052]` impl note 2026-06-08 tri-backend split. Confirm enum values with HILDA team at Phase 3 build time.** |
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
| `milestone_id` | Lookup → Milestones | nullable — **[Ph-1]** Added 2026-06-12; already on storage's `CommunicationLogRow`. For milestone-level audit queries. |
| `tg_name` | String | nullable — **[Ph-1]** Added 2026-06-12; already on storage's `CommunicationLogRow`. For TG-scoped audit queries. |
| `credential_id` | String | nullable — **[Ph-1]** Added 2026-06-12; already on storage's `CommunicationLogRow`. Opaque reference to credential_service per `[D-019]`; **never the credential material itself**. For per-PM accountability on credential-using operations. |

> **NOTE (added 2026-06-12)**: `source_system` was requested in earlier review but DROPPED 2026-06-12 — the `channel` column already covers the use case (Email / Messenger / CorporatePLM / NetworkSharedDrive / CustomerJIRA / SharePoint enumerates the originating system).

### 2.8 List: `TGGroups` — REMOVED 2026-06-12 (denormalized onto DeliveryItems per `[D-051]` impl note)

> **OBSOLETE — do NOT create this SP list.** Original [D-051] specified `TGGroups` as a separate SP list with one row per `(milestone_id, tg_name)`. On 2026-06-12 the architect accepted SP UI engineer's 2026-06-10 review proposal to DENORMALIZE the 9 TG metadata fields onto each `DeliveryItems` row instead — one SP list to view/edit, no SP-side joins needed, accepted row-level duplication tradeoff. **The 9 TG fields now live on `DeliveryItems` (see §2.4 — `ingress_nsd`, `tracking_modality_tg`, `email_group_alias`, `tg_owner_name`, `tg_owner_email`, `default_cc_list`, `folder_routing_enabled`, `tracking_enabled`, plus `corp_id_list` Ph-2).** Source-of-truth lives in customer YAML at `customizations/template_schemas/<customer_slug>/tg_groups.yaml` per template_schema's `TGGroupBase` Pydantic model. HILDA writes TG fields onto DI rows at DI creation via `[D-064]` writeback; SP UI MUST NOT allow TPMs to edit TG columns on DI rows (would diverge from siblings + YAML).

The original §2.8 schema is preserved below as **historical reference only**; ignore it when provisioning the 7 lists. SP UI engineer should:
- NOT create a `TGGroups` SP list.
- Render TG-level metadata in §3.1 milestone-view TG headers by READING the denormalized columns on the first DeliveryItem row in each `tg_name` group (all rows in the same TG carry identical values for those 9 fields).
- NOT provide an "Edit TG metadata" link (was Ph-2 §4 deferred item; gone now — TG-level editing in SP is no longer modeled; TG schema changes go through YAML + HILDA tracker rewriting all DI rows for the affected TG).

<details>
<summary>Historical §2.8 schema (do NOT use)</summary>

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
| `ingress_nsd` | Choice (`NSD1` / `NSD2`) | **[Ph-1]** `[D-013]` dual-NSD topology — identifies which NSD this TG's documents arrive on (relevant when `tracking_modality` includes `NetworkSharedDrive`). Default `NSD1`. Added 2026-06-09. |
| `folder_routing_enabled` | Boolean, default No | **[Ph-1]** FR-77 Type-2 routing — when Yes, HILDA uses the per-TG `folder_routing.yaml` (lives in HILDA YAML, not SP) for NSD-direct files to resolve work-item. Added 2026-06-09. |
| `tracking_enabled` | Boolean, default Yes | **[Ph-2]** — when No, HILDA does not track items in this TG. Per-item `force_tracking_enabled` override exists on DeliveryItems. Added 2026-06-09. |

**Unique constraint:** `(milestone_id, tg_name)` — one row per TG per milestone; SP UI must prevent duplicates.

**How rows get populated:**
- HILDA creates these rows automatically at tracker-creation time, reading values from the customer template YAML (`customizations/template_schemas/<customer>/tg_groups.yaml`).
- TPM can edit these rows in SP UI **before** the milestone's collection kickoff (FR-71 ODF — Phase 2; for Phase 1 the YAML values are accepted as-is).
- After collection kickoff, edits trigger SP alerts to HILDA so the live system picks up the change (e.g., a corrected `tg_owner_email` reaches HILDA via the SP-alert email channel).

**Why this is a separate list (not columns on DeliveryItems):**
- One TG can contain 20+ items in a milestone. Putting `tg_owner_email`, `email_group_alias`, etc. on every item would mean 20× duplication and 20× consistency risk when the value changes.
- The DeliveryItems list keeps just `tg_name` (the foreign-key-like label); SP UI does a lookup to TGGroups for display.
- Aligns with the schema/content boundary per HILDA's `[D-045]` — TG metadata is per-group config, not per-item runtime state.

</details>

---

## 3. Views to build

### 3.1 Milestone View (the primary view)

The view PMs/TPMs spend most of their time in. One web part page per milestone. Built on the `DeliveryItems` list, filtered by `milestone_id`.

**Layout:**
- Header row: device name, milestone name, milestone status, target date
- **Grouped by `tg_name`** (HW / SW / QA / …) — for each group, render a **TG header** at the top of the group with metadata READ FROM THE DENORMALIZED TG COLUMNS on the first DeliveryItem row in the group (per `[D-051]` impl note 2026-06-12 — all DI rows in the same TG carry identical values for these 9 columns; reading from the first row in the group is equivalent to a lookup):
  - `tg_owner_name` + `tg_owner_email` (the TG coordinator)
  - `email_group_alias` if set, shown as `Alias: <value>` (else "—")
  - `default_cc_list` size (e.g., "CC: 3 recipients" — click to expand)
  - ~~*(Phase 2)* an inline **"Edit TG metadata"** link~~ — REMOVED 2026-06-12 (TGGroups list no longer exists; TG schema changes go through YAML + HILDA tracker rewriting all DI rows for the affected TG; no SP-side editing of TG fields).
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
| **What happens on click** | Set `Milestones.close_all_items_triggered_at = <now>` (field added 2026-06-12 per §2.3 — SP-side ONLY field, no HILDA Postgres mirror). HILDA flips each item's `delivery_state` to `Closed` after reading the SP-alert per `[D-047]`. |

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

### 4.9 *(Phase 1)* Reassign Work-Item — TPM-manual unrouted-document resolution (FR-87 step A — added 2026-06-09 per `[D-053]` impl note 2026-06-08)

| Aspect | Detail |
|---|---|
| **Where** | Per-document row on the milestone's **default work-item** (the auto-instantiated catch-all per FR-78; receives docs that HILDA's FR-52 5-step routing pipeline could not resolve) |
| **Enabled when** | The default work-item has one or more unrouted documents pending TPM resolution |
| **User prompt** | Dropdown of candidate work-items within the milestone, filtered by `inferred_tg_name` (the channel-resolved TG attached to the document per `[D-060]`). TPM selects target item. Confirmation: "Reassign document `<filename>` to work-item `<item_name>`?" (Yes / Cancel) |
| **What happens on click** | Set `DeliveryItems.tpm_reassignment_target_item_id = <selected item_id>` on the new target item row |
| **What HILDA does next** | (Background) `email_service.sp_alert_parser` receives the alert with `action_type = tpm_reassign_to_workitem`; HILDA moves the file from `_unrouted/` NSD path to the target item's classified path per FR-86 storage matrix; updates `DocumentItemAssociation` per `[D-055]`; runs `[D-039]` revision determination (deferred at ingest per FR-86 skip rule). Item rows update via §8 live polling. |
| **Permission** | TPM only |

### 4.10 *(Phase 1)* Resolve doc_type — TPM-manual classification resolution (FR-87 step B — added 2026-06-09 per `[D-053]` impl note 2026-06-08)

| Aspect | Detail |
|---|---|
| **Where** | Per-document row where `doc_type = unresolved` (HILDA's FR-85 2-step classification ladder didn't resolve) OR `(item_type, doc_type)` misaligned per FR-86 storage matrix (e.g., a `TEST_TECH_WAIVER_REPORT` item received a `compliance_certification_release_notes`-classified document) |
| **Enabled when** | The item has one or more documents in `staged-not-classified` NSD path |
| **User prompt** | Dropdown of `{test_report, tech_report, waiver, compliance_certification_release_notes}`. TPM selects target doc_type. SP UI validates alignment with `item_type` (e.g., if item is `TEST_TECH_WAIVER_REPORT`, only `{test_report, tech_report, waiver}` selectable; if item is `COMPLIANCE_CERTIFICATION_RELEASE_NOTES`, only `compliance_certification_release_notes` selectable). Confirmation: "Resolve doc_type for `<filename>` → `<selected>`?" (Yes / Cancel) |
| **What happens on click** | Set `DeliveryItems.tpm_resolved_doc_type = <selected>` |
| **What HILDA does next** | (Background) `sp_alert_parser` receives the alert with `action_type = tpm_resolve_doc_type`; HILDA validates FR-86 alignment; runs `[D-039]` revision determination (was skipped at ingest per FR-86 skip rule); moves file from `_staged_classification/` to canonical `classified` NSD path (or to `_staged_revision/` if `[D-039]` returned ambiguous). Item rows update via §8 live polling. |
| **Permission** | TPM only |
| **FR-87 strict order**: must complete BEFORE §4.11 (Resolve revision is gated on doc_type being set). |

### 4.11 *(Phase 1)* Resolve revision — TPM-manual revision-determination resolution (FR-87 step C — added 2026-06-09 per `[D-053]` impl note 2026-06-08)

| Aspect | Detail |
|---|---|
| **Where** | Per-document row where the document is in `_staged_revision/` NSD path (`[D-039]` LLM Step 3 returned ambiguous on NEW-vs-REVISION classification) |
| **Enabled when** | The item has one or more documents in `staged-not-revision-determined` NSD path AND `doc_type` is set (Steps A + B complete per FR-87 strict order) |
| **User prompt** | Two-option radio: "NEW" or "Revision of `<doc_id_slug>`" (dropdown of existing `doc_id_slug` values for this `(item, doc_type)` family). TPM selects. Confirmation: "Resolve `<filename>` as `<selected>`?" (Yes / Cancel) |
| **What happens on click** | Set `DeliveryItems.tpm_revision_resolution = NEW` or `<doc_id_slug>` |
| **What HILDA does next** | (Background) `sp_alert_parser` receives the alert with `action_type = tpm_resolve_revision`; HILDA assigns `doc_id_slug` + `rev_number`; moves file from `_staged_revision/` to canonical `classified` NSD path; fires FR-77 carrier-upload trigger if `delivery_state` advances. Item rows update via §8 live polling. |
| **Permission** | TPM only |

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

**Response shape** (JSON; revised 2026-06-09 per `[D-053]` / `[D-055]` / `[D-060]` impl notes 2026-06-08/09):
```json
[
  {
    "doc_type": "test_report",          // 5-value enum: test_report | tech_report | waiver | compliance_certification_release_notes | unresolved
    "doc_id_slug": "band_1_rf_conformance",  // null when nsd_path_type in {staged_not_revision, staged_not_classified, unrouted}
    "rev_number": 1,                     // null when doc_id_slug null
    "original_filename": "band1_rf_conformance.pdf",
    "download_url": "https://hilda.corp/dl/<scoped_token>",
    "parser_result": { ... },           // for doc_type=test_report only; null otherwise
    "llm_review_findings": { ... },      // null when review_required=false OR doc_type ∈ {compliance_certification_release_notes, unresolved}
    "inferred_tg_name": "HW",            // per [D-060] — channel-resolved TG; shown when the doc is on the default work-item (unrouted) so TPM sees which TG it came from
    "nsd_path_type": "classified"        // [D-055] impl note 2026-06-09 — one of: classified | staged_not_classified | staged_not_revision | unrouted; drives the TPM-resolution button visibility in §4.9/§4.10/§4.11
  },
  ...
]
```

### 5.2 Rendering (per FR-58 / FR-59 / FR-60; revised 2026-06-09 for 5-value DocType + FR-86 staged-path indicators)

- Group by `doc_type` (5-value: test_report / tech_report / waiver / compliance_certification_release_notes / unresolved)
- One row per document
- Each row shows: `doc_type`, `doc_id_slug` (human-readable; "—" when null), `rev_number` (or "—"), `original_filename`, download link, parser_result summary (for test_report only), llm_review_findings summary (for test/tech/waiver only), **`nsd_path_type` indicator** (badge / icon — classified docs vs staged-not-classified vs staged-not-revision-determined vs unrouted), and for unrouted docs **show `inferred_tg_name`** (helps TPM group default-work-item docs by their channel-resolved TG before reassigning via §4.9)
- Staged-path docs (`nsd_path_type ∈ {staged_not_classified, staged_not_revision, unrouted}`) surface their corresponding TPM-resolution button (§4.9 / §4.10 / §4.11) inline on the row
- **View in PLM** link per item — points to `item.actual_item_info` URL (null for documents in staged paths since PLM upload happens only after FR-86 classified state is reached)
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

For each of the **7 SharePoint Lists** in §2 (Customers, Devices, Milestones, DeliveryItems, Users, PMCredentials, CommunicationLog — TGGroups removed 2026-06-12 per `[D-051]` impl note; 9 TG fields denormalized onto DeliveryItems), configure an alert subscription:

*(Note: CommunicationLog is technically append-only and shouldn't need editing, so its alert could be omitted in practice — but configuring it for completeness costs nothing and protects against future field additions. The 7 lists above all need alerts. **Note 2026-06-12**: the original 2026-06-09 note flagged TGGroups as "the critical one for TPM-driven TG metadata overrides per FR-71" — that list no longer exists; TG-field overrides are no longer surfaced in SP UI per `[D-051]` impl note 2026-06-12. The critical alerts are now on DeliveryItems (TPM-driven field edits including FR-87 resolutions and `pm_approval_at` / `pm_approval_pm_id`) and Milestones (button-click timestamps).)*

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

### 7.4 Action verb conventions (added 2026-06-09 — FR-87 strict A→B→C TPM resolution)

HILDA's `sp_alert_parser` infers what action TPM took by parsing **which field** was modified in the alert body's key:value pairs. SP UI engineer doesn't generate these verbs — they're inferred by HILDA from the field-name → verb mapping. **For the FR-87 TPM-resolution buttons added in §4.9–§4.11**, the mapping is:

| Field modified (key in alert body) | Inferred action_type | Triggered by button |
|---|---|---|
| `tpm_reassignment_target_item_id` | `tpm_reassign_to_workitem` | §4.9 Reassign Work-Item |
| `tpm_resolved_doc_type` | `tpm_resolve_doc_type` | §4.10 Resolve doc_type |
| `tpm_revision_resolution` | `tpm_resolve_revision` | §4.11 Resolve revision |
| `milestone_collection_started_at` | `start_collection` | §4.1 Start Collection |
| `milestone_submission_triggered_at` | `submit_to_carrier` | §4.3 Submit to Carrier |
| `refresh_requested_at` | `refresh` | §4.5 Refresh |
| `last_reminder_triggered_at` | `send_reminder` | §4.6 Send Reminder |
| `delivery_state` change to `ReadyForSubmission` | `pm_approval` | §4.2 Approve |

**SP UI engineer responsibility**: ensure the new fields (§2.4 additions for FR-87) trigger SP alerts via the "Anything changes" setting per §7.1. **Field write is what HILDA sees** — the SP UI does not generate the verb itself; HILDA's parser does the field → verb inference.

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

**Refresh model: focus-aware** (per SP UI engineer 2026-06-10; supersedes the prior poll-interval framing). On tab/window focus-gain → re-fetch list deltas + re-render via the SP REST GET above (Page Visibility API + focus event triggers). In-focus refresh strategy (continuous interval / interaction-triggered / hybrid) is the SP UI engineer's implementation choice. Backgrounded tab does no SP work — saves SP server load. This refresh model applies to all SP UI views surfacing HILDA-written state (milestone view, delivery items, CommunicationLog, etc.). Anchors `[D-064]` (HILDA→SP REST as sole HILDA-initiated state writeback channel).

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
- §2.1–§2.7 — provision **7 SharePoint lists** (Customers, Devices, Milestones, DeliveryItems, Users, PMCredentials, CommunicationLog — TGGroups removed 2026-06-12) with the field corrections + additions per 2026-06-09 cascade (4-value `item_type`, 4-value `customer_delivery_modality`, `target_folder` / `no_customer_upload` / 3 FR-87 TPM-resolution fields on DeliveryItems) PLUS 2026-06-12 additions (`is_milestone_gating` rename; `pm_approval_at` + `pm_approval_pm_id` per `[D-068]`; `owner_corp_id` on DeliveryItems + Users; `close_all_items_triggered_at` on Milestones; 9 denormalized TG columns on DeliveryItems per `[D-051]` impl note; `TPM` role on Users; `milestone_id` / `tg_name` / `credential_id` on CommunicationLog; SP system columns NOT duplicated as custom columns; `item_no` immutability)
- §3.1 Milestone View (with grouping by `tg_name` + TG-group header rows reading from TGGroups via lookup)
- §3.2 Device Tracker View
- §3.3 PM Dashboard View
- §4.1 Start Collection
- §4.2 Approve
- §4.3 Submit to Carrier (with delta confirmation prompt)
- §4.4 Close All Items
- §4.5 Refresh
- §4.6 Send Reminder
- **§4.9 Reassign Work-Item** (FR-87 step A — TPM unrouted-document resolution; added 2026-06-09)
- **§4.10 Resolve doc_type** (FR-87 step B — TPM classification resolution; added 2026-06-09)
- **§4.11 Resolve revision** (FR-87 step C — TPM revision-determination resolution; added 2026-06-09)
- §5 Document section + download links (Phase 1: single revision per document only — don't worry about revision history UI; **DO render 5-value doc_type + `nsd_path_type` indicator + `inferred_tg_name` on unrouted docs per 2026-06-09 cascade**)
- §6 PM Review section (Phase 1: Approve only; no Override Final yet)
- §7 SP Alert configuration on all 7 lists (revised 2026-06-12 from 8; TGGroups removed) + **§7.4 action verb conventions** (added 2026-06-09)
- §8.1 Live SP REST polling
- §9 Permissions

### Phase 2 (after Phase 1 lands with first customer)
- §4.7 Upload Document
- §4.8 Close Item (owner self-close)
- §5.2 Expandable revision history (`?all_revisions=true`)
- §6 Override Final revision UI
- §8.2 In-flight status polling for submission assembly progress
- ~~**TGGroups inline edit link in §3.1**~~ — REMOVED 2026-06-12. TGGroups list no longer exists; TG-field editing in SP UI is not modeled anymore. TG schema changes go through YAML + HILDA tracker rewriting all DI rows for the affected TG.

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
- The **7 SharePoint Lists** (column definitions per §2; create the lists with these internal names exactly — TGGroups removed 2026-06-12 per `[D-051]` impl note; 9 TG fields denormalized onto DeliveryItems per §2.4)
- All views per §3 (including TG-group header rendering above each `tg_name` grouping in §3.1, populated by lookup to TGGroups)
- All buttons per §4 (each writes to a SP field — no direct HILDA HTTP calls for state actions)
- The SP-side alert configuration per §7 (this is a SP deployment step, not code) — covering all 7 lists
- The document section and PM Review section per §5 and §6 (which call HILDA's enumeration API for data + render HILDA-mediated download links)

### Open items to confirm with HILDA team

1. ~~**Field name** for the `milestone_gating` boolean~~ — **RESOLVED 2026-06-12: `is_milestone_gating`** per template_schema/MODULE.md Invariant 2026-06-12 (renamed from `milestone_gating`).
2. **Field name mapping** — your prototype may use different column internal names than HILDA's canonical schema (e.g., `ItemNumber` vs `item_no`, `TeamName` vs `tg_name`, `DeliveryType` vs `item_type`). Either align prototype to canonical names OR HILDA's parser adds a name-mapping translation layer. Agree on one approach before list creation. **2026-06-12 note**: `item_no` is now declared IMMUTABLE per item lifetime (referential integrity for FR-77 + storage.TGFolderRoutingRow); SP UI reorder MUST NOT mutate it (use `sort_order` for visible reorder).
3. ~~**`Close All Items` field name** (§4.4)~~ — **RESOLVED 2026-06-12: `close_all_items_triggered_at`** on Milestones (SP-side only; no HILDA Postgres mirror) per §2.3.
4. **Override Final endpoint** (§6 Phase 2) — `POST https://hilda.corp/docs/<delivery_item_id>/set_final` shape TBD.
5. **Status endpoint** (§8.2) — `GET https://hilda.corp/status/milestone/<id>/submission` shape TBD.
6. **Upload endpoint** (§4.7 Phase 2) — `POST https://hilda.corp/upload/<delivery_item_id>` shape TBD.
7. ~~**TGGroups list (§2.8)** — added 2026-05-26~~ *— RESOLVED 2026-06-09: TGGroups list field shape (`corp_id_list` and `default_cc_list` JSON arrays); `(milestone_id, tg_name)` unique constraint enforced SP-side per `[D-051]` ; auto-population mechanism — HILDA pushes via REST after reading the customer template YAML, same as for Devices / Milestones / DeliveryItems.*
8. **NEW 2026-06-09: TPM-resolution field names** (§4.9–§4.11) — confirm SP-side internal names match the prototype exactly: `tpm_reassignment_target_item_id`, `tpm_resolved_doc_type`, `tpm_revision_resolution`. HILDA's `sp_alert_parser` field → action_type inference (§7.4) depends on these names.
9. **NEW 2026-06-09: TPM-resolution button visibility logic** (§4.9–§4.11) — confirm with SP UI engineer: the buttons are inline on each document row (per §5.2 staged-path-docs surface the button), NOT in a separate "Resolution Queue" view. Confirm UX preference + verify the document-enumeration API response (§5.1) carries `nsd_path_type` so SP UI can drive button visibility client-side without a separate API call.
10. **NEW 2026-06-09: `inferred_tg_name` display on unrouted docs** (§5.2) — confirm: does TPM want a separate filter ("Show all unrouted docs from TG X") at the milestone level, or is per-document `inferred_tg_name` rendering sufficient?

---

## Quick reference summary

| What you build | Where it lives | What it writes / calls |
|---|---|---|
| 7 SharePoint Lists (TGGroups removed 2026-06-12 per `[D-051]` impl note; TG fields denormalized onto DeliveryItems per §2.4) | Corp SP site | — (data store) |
| 3 views (milestone / device / dashboard) | SP web parts | Reads SP lists |
| Action buttons | SP web parts | Writes SP list fields (which trigger SP alerts → HILDA) |
| Document section | SP web parts | Calls `GET https://hilda.corp/docs/<item_id>` |
| Download links | SP web parts (just link rendering) | User's browser → `https://hilda.corp/dl/<token>` |
| Live status polling | SP web part JavaScript | Polls SP REST API (NOT HILDA) |
| SP Alert subscriptions | SP deployment configuration | (no code; setup step) |

If a feature isn't in this document, it's HILDA's side — not yours.

**Questions / clarifications**: contact the HILDA team.
