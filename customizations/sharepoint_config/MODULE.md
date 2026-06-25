# Module: customizations/sharepoint_config

> **Status:** Draft + 2026-06-25 Module #11 arch revisit cascade (architect Q1-Q5 lock → **3-list framing**, was 7 per D-DRAFT-Y; per-customer naming `<base>_<customer_id>` per `[D-104]`; field-name authority = `milestones_workitems_fields_values.xlsx`). Prior absorptions: 2026-06-12 SP UI engineer review (D-DRAFT-X SP list creation responsibility = SP UI engineer manual; D-DRAFT-Y TGGroups removed → ratified as `[D-106]`); initial draft 2026-06-10 (Ph-1-first scope per discipline locked 2026-06-09). **Data drop-zone — no Python code under this directory.** YAML files consumed by `core/src/sharepoint_integration/FileBasedListProvider` per `[D-020]`. Aligned with architect Q1 2026-06-25 (3-list scope), `[D-091]` slug→id, `[D-104]` Projects per-customer, `[D-105]` 4-field owner identity, `[D-106]` TGGroupBase DROPPED, `[D-108]` rules_paused SP column, `[D-053]` 4-value ItemType / 5-value DocType, `[D-064]` HILDA→SP REST writeback channel, `[D-065]` SP UI engineer ownership of SP Choice values.
>
> **Rollback log:**
> - **2026-06-25 (Module #11 arch revisit cascade — sharepoint_config side)** — corresponding cascade to sharepoint_integration's 2026-06-25 entry. **D1 — Lists block shrinks 7 → 3**: `customers`, `devices`, `users`, `pm_credentials`, `communication_log` REMOVED from customer YAML's `lists:` block per architect Q1 2026-06-25 lock + `[D-104]` Projects per-customer cascade. New `projects:` entry ADDED. Final lists: `delivery_items` + `milestones` + `projects`. (`tg_groups` was already removed per `[D-106]` formerly D-DRAFT-Y.) **D2 — Per-customer list naming**: list `name:` entries follow `<base>_<customer_id>` pattern per architect Q1 sample (e.g., `Deliverables_<customer_id>`, `Milestones_<customer_id>`, `Projects_<customer_id>`). **D3 — slug → id rename** per `[D-091]`: `customer_slug:` → `customer_id:`; `device_slug:` → `device_id:`. **D4 — Field-name authority refresh** per architect Q5: stale `docs/sp_ui_engineer/HILDA_SP_Schema.xlsx` references replaced with `docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` (3 worksheets: Milestones / Deliverables / Projects). Both stale workbooks renamed `_DEPRECATED_2026-06-15.xlsx` in the same directory. **D5 — D-DRAFT-Y → [D-106] promotion**: replace `D-DRAFT-Y` references with `[D-106]` (TGGroupBase DROPPED — already locked). **D6 — D-DRAFT-Z SUPERSEDED**: HILDA SP scope is 3 lists (not 2 per D-DRAFT-Z). **D7 — D-DRAFT-X retained as pending**. **D8 — Customer + Device deployment-stable data**: moved entirely out of SP-list scope; customer + device metadata lives in `customizations/template_schemas/<customer_id>/customer.yaml` per existing pattern (FR-78 / `[D-053]` / `[D-077]` impl note 2026-05-24). HILDA's runtime SP coupling = Milestones + Projects + DeliveryItems lists only per-customer. **D9 — Anchors refresh**: drop `[D-051]` 8-list framing as authority (now historical); add `[D-091]` + `[D-104]` + `[D-105]` + `[D-106]` + `[D-108]` (rules_paused SP column on Deliverables).
> - **2026-06-12 (SP UI engineer 2026-06-10 review absorption — D-DRAFT-X + D-DRAFT-Y)** — six sub-edits absorbing SP UI engineer review items closing STATUS line 263 / 280 / 288 / 316 Flags: **(a) D-DRAFT-X (decision pending close-session ratification)** — SP UI engineer manually creates SP lists from customer YAML (NOT HILDA's tracker module via REST). Driver: corp SP-2017 SP-alert email triggers + custom SP tasks (workflows, custom field types) that REST API cannot express; SP UI engineer needs the SP-UI provisioning path. Consequence: this YAML maps canonical → SP internal column name only (no provisioning role); SP UI engineer reads YAML during deployment to know what columns to create + assigns internal column names. `sharepoint_integration` REST writeback per `[D-064]` is unchanged (HILDA writes existing rows; doesn't create lists). **(b) D-DRAFT-Y (decision pending close-session ratification)** — `[D-051]` 8-list framing amended: **TGGroups removed as a separate SP list**; TG metadata fields (`tg_name`, `ingress_nsd`, `tracking_modality`, `email_group_alias`, `tg_owner_name`, `tg_owner_email`, `default_cc_list`, `folder_routing_enabled`, `tracking_enabled`) are DENORMALIZED onto each DeliveryItems row. Source-of-truth remains customer YAML (FileBasedListProvider); SP-side TG columns are write-once-at-DI-creation read-only display mirrors per `[D-064]` writeback. Reason: SP UI engineer's design — one SP list to view/edit, no SP-side joins needed; accepted row-level duplication tradeoff. Result: 7-list SP layout. **(c) `delivery_items:` column block expanded** — added 9 denormalized TG columns; added `is_milestone_gating` (renamed from `milestone_gating` per template_schema/MODULE.md); added `pm_approval_at` + `pm_approval_pm_id` per `[D-068]`; added `owner_corp_id` (denormalized from Users.owner_corp_id at DI creation for display); dropped tpm_resolved_doc_type stale Choice "default" → corrected to "compliance_certification_release_notes" per `[D-053]` 5-value DocType. **(d) `milestones:` column block** — `default_work_item_config` NOT in SP (moved to YAML — onboarding-time-fixed config, not TPM-editable); `close_all_items_triggered_at: datetime | None` added as SP-side-only field (no HILDA Postgres mirror; SP is system-of-record for this audit timestamp). **(e) `users:` column block** — added `owner_corp_id: str` (canonical per-person; reused by corp messenger module Ph-2 per user comment dropping `user_corp_messenger_handle`); added discipline note that SP system columns (`Created` / `Modified` / `Author` / `Editor`) MUST NOT be duplicated as custom columns. **(f) `tg_groups:` list block dropped** per D-DRAFT-Y; note added that TG YAML schema lives in `customizations/template_schemas/<customer_slug>/tg_groups.yaml` per template_schema/TGGroupBase (unchanged); `communication_log:` `source_system` request from STATUS line 263 dropped (channel column already covers the use case; SP UI engineer to confirm or surface specific need).
> - **2026-06-10 (initial draft)** — first MODULE.md for `customizations/sharepoint_config/`. Flagged NEW by regen-map 2026-06-10; resolved as "(a) stub documenting drop-zone convention" per architect decision 2026-06-10 (versus "(b) carve-out config-only dirs in structure-conventions.md"). Anchors `[D-001]` (three-tier layout — customizations belong here, not in core), `[D-004]` (SP integration split: standard mechanics in core; deployment-specific config here), `[D-020]` (SharePointListProvider Protocol — this dir's YAML is FileBasedListProvider's input), `[D-051]` (8-list SP layout), `[D-053]` (4-value ItemType + 5-value DocType — Choice value enumeration), `[D-064]` (HILDA→SP REST writeback channel), `[D-065]` (SP UI engineer owns SP Choice values; this YAML maps canonical→SP-internal name only, NOT Choice values). New error-code source: SHP-* codes (registered in `core/src/sharepoint_integration/error_codes.py` per `[D-002]`).

**Purpose**: **Per-customer-deployment drop-zone** for SP list/column mappings that `core/src/sharepoint_integration/FileBasedListProvider` consumes at startup to translate HILDA's canonical field names to deployment-specific SP internal column names. One YAML file per customer deployment under `customers/<customer_id>.yaml` per `[D-091]` slug→id rename; device-level overrides Deferred to Ph-2/Ph-3+ per architect Q1 2026-06-25 (no Ph-1 device-override surface). **Data-only drop-zone — no Python code under this directory** (the `__init__.py` is present to make the directory importable as a Python package per structure-conventions but contains no logic; the directory is functionally a YAML data store). The 3-tier name model per `[D-065]`: **HILDA canonical name** (Python code field name — owned by HILDA architect via `core/src/template_schema/`) → **SP internal column name** (REST wire-protocol name — recorded here in YAML) → **SP display name** (TPM-visible browser label — owned by SP UI engineer in SP itself, NOT recorded here). This module owns the middle layer (canonical → SP internal mapping); upstream and downstream layers live in `template_schema/` and SP respectively. **`docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` is the authoritative comm channel** between HILDA architect and SP UI engineer for canonical-field set + Choice values + new list additions per architect Q5 2026-06-25 (3 worksheets: Milestones / Deliverables / Projects); this directory's YAML lags the workbook (SP UI engineer creates SP columns first → SP auto-generates internal names → HILDA architect records the mapping here). Stale workbooks `HILDA_SP_Schema.xlsx` + `DeliveryItem_visibility_review.xlsx` were renamed `_DEPRECATED_2026-06-15.xlsx` in the same directory. Anchors architect Q1 2026-06-25 (3-list scope), `[D-001]`, `[D-004]`, `[D-020]`, `[D-091]` (slug→id), `[D-104]` (Projects per-customer), `[D-105]` (4-field owner identity), `[D-106]` (TGGroupBase DROPPED), `[D-108]` (rules_paused SP column), `[D-053]`, `[D-064]`, `[D-065]`, and serves FR-30 (rule_engine YAML lives in a sister directory `customizations/rules/` — same drop-zone discipline), FR-84 (HILDA→SP REST writeback uses these column maps), FR-87 (TPM resolution buttons' SP column names live here).

**Workload assignment**: No workload. Files are bind-mounted into `hilda-api` / `hilda-worker` containers per `[D-025]` Docker-Compose bind-mount pattern (Ph-1/Ph-2) — same mechanism as `customizations/rules/` and `customizations/template_schemas/`. Reloaded by `FileBasedListProvider.reload()` on SIGHUP. No HILDA pod runs here.

---

## Sub-modules

```
customizations/sharepoint_config/
  __init__.py                          ← empty; present for Python package discoverability only (no logic)
  customers/
    example.yaml                       ← template shape for ops to copy
    <customer_id>.yaml                 ← one file per customer deployment (Ph-1); slug→id per [D-091]
  devices/                             ← Deferred to Ph-2/Ph-3+ per architect Q1 2026-06-25 (no Ph-1 device-override surface)
    special_devices.yaml               ← Deferred — per-(customer × device) overrides
  MODULE.md                            ← this file
```

---

## Public surface

*(No Python surface. The "Public surface" of this module is the YAML file schema validated by `core/src/sharepoint_integration/FileBasedListProvider`.)*

### Customer YAML schema (`customers/<customer_id>.yaml`)

```yaml
customer_id: <customer_id>               # MUST match the directory + match template_schema's customer_id; slug→id rename per [D-091]
lists:                                   # 3 lists per architect Q1 2026-06-25 lock + [D-104] (was 7 per D-DRAFT-Y; was 8 per [D-051]).
                                         # Customers / Devices / Users / PMCredentials / CommunicationLog lists REMOVED from HILDA SP scope per architect Q1:
                                         #   - Customer + Device deployment-stable data → customizations/template_schemas/<customer_id>/customer.yaml
                                         #   - HILDA's CommunicationLog is Postgres-internal per FR-42 (distinct from SP-side CommunicationLog_<customer_id> list per Q3)
                                         #   - User + PMCredential data → credential_service per [D-019]; not in SP-list scope
                                         #   - TGGroups removed per [D-106] (formerly D-DRAFT-Y); TG fields denormalized onto delivery_items
                                         # Field names per docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx (3 worksheets) per architect Q5 — AUTHORITATIVE.
  milestones:
    name: "Milestones_<customer_id>"     # per-customer naming `<base>_<customer_id>` per [D-104] + architect Q1 sample 2026-06-25
    columns:
      milestone_name: "Title"            # STR canonical per [D-090] (NOT milestone_id)
      expected_collection_start: "Expected_x0020_Collection_x0020_Start"
      milestone_status: "Status"
      close_all_items_triggered_at: "Close_x0020_All_x0020_Items_x0020_Triggered_x0020_At"  # SP-side-only audit timestamp; FR-64 Close All Items action. NO HILDA Postgres mirror — SP is system-of-record.
      # NOTE: `default_work_item_config` is NOT a SP column — customer-deployment-fixed config; lives in `customizations/template_schemas/<customer_id>/milestones.yaml` per FR-78 / `[D-053]`. Not TPM-editable.
      # ... per Milestones worksheet in milestones_workitems_fields_values.xlsx
  projects:
    name: "Projects_<customer_id>"       # per-customer per [D-104] + architect Q1 (supersedes prior global-Projects framing)
    columns:
      # TPM 3-tuple per [D-088] (per-customer per [D-104])
      # ... per Projects worksheet in milestones_workitems_fields_values.xlsx
  delivery_items:
    name: "Deliverables_<customer_id>"   # per-customer naming per [D-104] + architect Q1
    columns:
      # Item-specific fields
      item_name: "Title"
      item_no: "Item_x0020_No"                                              # IMMUTABLE per item lifetime (template_schema/MODULE.md Invariant 2026-06-12); referential integrity for FR-77 routing
      item_type: "Item_x0020_Type"                                          # Choice column; allowed values managed by SP UI engineer per [D-065]. **TODO**: verify Choice values per FR-58 lowercase rename (supersedes [D-094] mixed-case framing).
      delivery_state: "Delivery_x0020_State"                                # Choice column
      rules_paused: "Rules_x0020_Paused"                                    # Boolean per [D-108] for FR-31 sub-1 (added 2026-06-25)
      # 4-field owner identity per [D-105] (replaces single owner_email):
      owner_corp_usa_email: "Owner_x0020_Corp_x0020_USA_x0020_Email"
      owner_corp_email: "Owner_x0020_Corp_x0020_Email"
      owner_corp_id: "Owner_x0020_Corp_x0020_Id"
      owner_name: "Owner_x0020_Name"
      target_folder: "Target_x0020_Folder"                                  # FR-77 outbound
      no_customer_upload: "No_x0020_Customer_x0020_Upload"                  # FR-80 boolean
      is_milestone_gating: "Is_x0020_Milestone_x0020_Gating"                # Boolean; FR-64 enablement
      pm_approval_at: "PM_x0020_Approval_x0020_At"                          # DateTime per [D-068]; FR-28 PMApproval trigger; cleared per [D-067] rewind path
      pm_approval_pm_id: "PM_x0020_Approval_x0020_PM_x0020_Id"              # User type per [D-068]; PM/TPM attribution; cleared together with pm_approval_at
      tpm_reassignment_target_item_id: "TPM_x0020_Reassign_x0020_Target"    # FR-87 step (A)
      tpm_resolved_doc_type: "TPM_x0020_Resolved_x0020_DocType"             # FR-87 step (B) Choice (test_report / tech_report / waiver / compliance_certification_release_notes per [D-053] 5-value DocType). **TODO**: confirm relationship between tpm_resolved_doc_type and item_type FR-58 lowercase rename — these are distinct enums; verify with architect on next pass.
      tpm_revision_resolution: "TPM_x0020_Revision_x0020_Resolution"        # FR-87 step (C)
      # Denormalized TG fields per [D-106] (formerly D-DRAFT-Y) — TGGroupBase Pydantic model DROPPED; TG metadata is denormalized onto delivery_items per [D-106]. Write-once-at-DI-creation from customer YAML via [D-064] writeback; SP-side read-only display mirrors (TPM SP UI MUST NOT allow editing); YAML remains source-of-truth.
      tg_name: "TG_x0020_Name"
      ingress_nsd: "Ingress_x0020_NSD"                                      # Choice: none / nsd1 / nsd2
      tracking_modality: "Tracking_x0020_Modality"                          # multi-value Choice per [D-037]
      email_group_alias: "Email_x0020_Group_x0020_Alias"
      tg_owner_name: "TG_x0020_Owner_x0020_Name"
      tg_owner_email: "TG_x0020_Owner_x0020_Email"
      default_cc_list: "Default_x0020_CC_x0020_List"
      folder_routing_enabled: "Folder_x0020_Routing_x0020_Enabled"          # Boolean
      tracking_enabled: "Tracking_x0020_Enabled"                            # Boolean
      # NOTE: `last_updated` is NOT a custom column — use SP's built-in `Modified` field (SP system column reuse discipline)
      # ... per Deliverables worksheet in milestones_workitems_fields_values.xlsx
```

### Device override YAML schema — Deferred to Ph-2 / Ph-3+

Device-level overrides are not in Ph-1 scope per architect Q1 2026-06-25 (Ph-1 surface is per-customer only). See Deferred section below. The previously documented `devices/special_devices.yaml` schema with `device_overrides:` (one of: `delivery_items` / `milestones` / `projects`) lands when a multi-device-per-customer scenario surfaces in Ph-2/Ph-3+.

---

## Invariants

- **Data-only drop-zone — no Python code under this directory** beyond an empty `__init__.py` for package discoverability. Any logic that would belong "near the data" lives in `core/src/sharepoint_integration/` (per `[D-001]` core-vs-customizations split + `[D-020]` SharePointListProvider Protocol). YAML edits never require a code release.
- **3-tier name model per `[D-065]`** — this YAML records the middle layer (HILDA canonical → SP internal column name) ONLY. The HILDA canonical names are owned by `core/src/template_schema/` (HILDA architect authority); the SP display names are owned by SP UI engineer in SP itself (NOT in this YAML); the SP internal column names recorded here are auto-generated by SP from display names at SP-list-column-creation time (typically `Display_x0020_Name` style with `_x0020_` for spaces). Editing the SP internal name in this YAML without a corresponding SP-side change WILL break the integration (writes will return `SHP-E001` HTTP 400).
- **3 lists per architect Q1 2026-06-25 lock + `[D-104]`** — every customer YAML's `lists:` block must include exactly 3 entries (`milestones`, `projects`, `delivery_items`), per-customer-named `<base>_<customer_id>` (e.g., `Milestones_<customer_id>`, `Projects_<customer_id>`, `Deliverables_<customer_id>`). `FileBasedListProvider` raises `SHP-E002` at startup if any of the 3 is missing for a customer's scope. **Was 7 pre-Q1 (D-DRAFT-Y); was 8 pre-D-DRAFT-Y per `[D-051]`.** Removed lists (`customers`, `devices`, `users`, `pm_credentials`, `communication_log`) are NOT in HILDA's SP read/write scope: customer + device deployment-stable data lives in `customizations/template_schemas/<customer_id>/customer.yaml`; user + PM credential data lives in `credential_service` per `[D-019]`; HILDA's CommunicationLog is Postgres-internal per FR-42 (distinct from SP-side `CommunicationLog_<customer_id>` list per architect Q3 lock). `tg_groups` REMOVED per `[D-106]` (formerly D-DRAFT-Y); TG fields denormalized onto `delivery_items:` rows; TG YAML at `customizations/template_schemas/<customer_id>/tg_groups.yaml` remains source-of-truth.
- **SP list provisioning is SP UI engineer manual, NOT HILDA-automated** (D-DRAFT-X 2026-06-12 — STILL PENDING ratification per architect 2026-06-25; surfaced as P0 candidate for next ADR triage). SP UI engineer reads customer YAML (this directory) at deployment time and hand-creates SP lists + columns in SP UI directly, including configuring SP-alert email triggers + custom SP tasks (workflows, custom field types) that REST API cannot express. `sharepoint_integration` REST writeback per `[D-064]` is unchanged (HILDA writes existing rows in already-provisioned lists; HILDA does NOT call REST to create lists or columns). `tracker` module does NOT provision; assumes SP lists pre-exist when running. Consequence for customer onboarding: any YAML change to canonical-field set (new fields, renames, removed fields) requires SP UI engineer to manually update SP lists before HILDA can write to them — coordination via `docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` comm channel (per architect Q5).
- **Denormalized TG fields on `delivery_items:` rows are SP-side read-only mirrors** per `[D-106]` (formerly D-DRAFT-Y; TGGroupBase Pydantic model DROPPED). HILDA writes TG fields onto each DI row at DI creation from customer YAML (via `[D-064]` writeback); SP UI MUST NOT allow TPMs to edit TG columns on DI rows (would diverge from YAML and from siblings in the same TG). YAML is source-of-truth for TG values; SP-side denormalization is display-only. HILDA always reads TG metadata from YAML at runtime (FileBasedListProvider), NEVER from SP DI rows. YAML-to-SP TG-field sync on YAML change: Ph-1 = HILDA re-writes all DI rows for the affected TG via tracker → sharepoint_integration writeback (write amplification accepted as TG fields are onboarding-time-immutable in practice).
- **Column maps are append-only** (per `sharepoint_integration/MODULE.md` invariant). When HILDA's `template_schema` adds canonical fields (e.g., the 2026-06-08 cascade added `target_folder`, `no_customer_upload`, FR-87 TPM-resolution fields on `delivery_items`), the SP UI engineer adds the corresponding SP columns + the customer YAML here extends its `columns:` block. Existing entries are not renamed (would break running queries); deprecated columns stay until ops confirms no callers.
- **NOT a Choice-value source per `[D-065]`** — the YAML records column names only, never the list of allowed values for SP Choice columns. SP UI engineer maintains Choice values in SP UI; HILDA architect communicates enum changes via `docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` per architect Q5 (NOT via this YAML). Mismatch surfaces at runtime as `SHP-E001`.
- **No credential material, no proprietary content** per NFR-2 / `[D-002]`. The YAML carries column names + list display names + scope keys; never credential blobs, never customer-data tokens, never PII. (Credential storage is `credential_service/` per `[D-019]` / `[D-038]`; PII never enters HILDA configuration files by construction.)
- **`customer_id` in the YAML MUST match the filename and MUST match `template_schema`'s customer_id** for that deployment per `[D-091]` slug→id rename. `FileBasedListProvider` raises `SHP-E002` on mismatch at startup.
- **Bind-mounted at runtime per `[D-025]`** — the directory is bind-mounted into `hilda-api` and `hilda-worker` containers; ops edits a YAML file, sends SIGHUP, `FileBasedListProvider.reload()` picks up the change without a redeploy. No restart required for column-map additions.
- **Per-customer SP list naming `<base>_<customer_id>`** per architect Q1 lock 2026-06-25 + `[D-104]` — list `name:` entries embed the customer identifier (e.g., `Deliverables_<customer_id>`); SP UI engineer creates the list with the embedded id. Ph-1 has no device-override surface (Deferred to Ph-2/Ph-3+).
- **`example.yaml` is a template shape, not a deployed customer** — `FileBasedListProvider` ignores files where `customer_id == "example"` (per `[D-091]` slug→id rename) so the template can ship in-repo without polluting any real deployment's scope.

---

## Key choices

- **`[D-001]`** — three-tier code organization: `core/` (HILDA Python) + `customizations/` (per-deployment config) + `config/` (operational tuning). This module is squarely in tier 2 (per-deployment config); no Python logic belongs here.
- **`[D-004]`** — SP integration split: standard API mechanics in `core/src/sharepoint_integration/`; deployment-specific list/column maps here. The boundary is load-bearing — moving any of this YAML's content into `core/` would break the multi-customer-deployment model.
- **`[D-020]`** — `FileBasedListProvider` boilerplate ships in `core/`; this YAML is its input. The `SharePointListProvider` Protocol stays in core; alternative implementations (e.g., a future SP-list-based provider that reads its own config from SP) would consume different data sources but expose the same Protocol surface.
- **`[D-025]`** — Docker-Compose bind-mount for this directory (Ph-1/Ph-2); Ph-3+ migrates to K8s ConfigMap with the same shape. The YAML schema is stable across phases.
- **Architect Q1 2026-06-25 (HILDA SP scope = 3 lists per-customer)** — supersedes `[D-051]` 8-list framing and D-DRAFT-Z 2-list scope. HILDA's runtime SP coupling = `Milestones_<customer_id>` + `Projects_<customer_id>` + `Deliverables_<customer_id>` only. SP UI engineer's TG site contains additional SP lists (`TasksTemplate`, `Tasks`, `Trials`, `Activities`, `Email`, `CommunicationLog`) — display surfaces only; HILDA does not touch them. Adding a 4th list requires an ADR.
- **`[D-104]`** — Projects per-customer SP list (supersedes prior global-Projects framing in `[D-083]`). List naming pattern `<base>_<customer_id>` applies to all 3 lists.
- **`[D-091]`** — slug → id rename throughout: `customer_slug:` → `customer_id:`; `device_slug:` → `device_id:` (both YAML field names and embedded list-name suffix).
- **`[D-105]`** — 4-field owner identity on Deliverables (`owner_corp_usa_email`, `owner_corp_email`, `owner_corp_id`, `owner_name`); replaces single `owner_email` column.
- **`[D-106]`** — TGGroupBase Pydantic model DROPPED (formerly D-DRAFT-Y 2026-06-12, now ratified per `[D-051]` denormalization architect lock 2026-06-21). TG fields denormalized onto Deliverables; YAML at `customizations/template_schemas/<customer_id>/tg_groups.yaml` remains source-of-truth.
- **`[D-108]`** — `rules_paused` SP column on Deliverables for FR-31 sub-1 (Boolean; column-map append).
- **`[D-051]` historical / superseded** — was 8-list SP layout; superseded by architect Q1 2026-06-25 (3-list per-customer). Retained as historical anchor.
- **D-DRAFT-X 2026-06-12 (still pending architect ratification 2026-06-25)** — SP UI engineer manually provisions SP lists from this YAML; HILDA does not call REST to create lists. Driver: corp SP-2017 SP-alert email triggers + custom SP tasks require SP-UI provisioning path (REST API cannot express). Consequences: tracker module assumes lists pre-exist; customer onboarding has explicit SP UI engineer ceremony step. **Surface as P0 candidate for next ADR triage** per architect 2026-06-25 not having explicitly addressed D-DRAFT-X.
- **D-DRAFT-Y SUPERSEDED → `[D-106]`** ratified (TGGroupBase DROPPED).
- **D-DRAFT-Z SUPERSEDED → architect Q1 2026-06-25 + `[D-104]`** (HILDA scope is 3 lists per-customer, NOT 2 per D-DRAFT-Z).
- **`[D-053]`** — Choice columns (e.g., `item_type`, `delivery_state`, `tpm_resolved_doc_type`) carry the canonical-side enum semantics in `core/src/template_schema/`; this YAML records only the column name addressing those Choice columns. SP UI engineer maintains the actual Choice value list per `[D-065]`.
- **`[D-064]`** — HILDA→SP REST is the sole HILDA-initiated state writeback channel. The column maps here are the addresses HILDA uses for those writes; SP UI surfaces them via focus-aware refresh.
- **`[D-065]`** — SP UI engineer owns SP Choice column allowed values. This YAML does NOT record Choice value enumerations — they live in SP itself + are documented in `docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` (per architect Q5 2026-06-25) for HILDA-side validation.
- **Drop-zone-with-MODULE.md over carve-out in `structure-conventions.md`** (architect decision 2026-06-10) — when regen-map flagged this dir as `NEW` without MODULE.md, two options: (a) draft a stub MODULE.md documenting the data-only convention; (b) edit `structure-conventions.md` to carve out config-only dirs as exempt from the "every top-level customizations dir is a module" rule. **Chose (a)** because this is a real contribution surface (SP UI engineer + ops both touch it); having a MODULE.md gives them an entry point with traceability anchors. (b) would have made the dir invisible to regen-map but lost the documentation value.

---

## Non-goals

- **Not a Python module** — no logic, no imports, no Public API beyond the YAML schema. The `__init__.py` is empty by design; do not add code here.
- **Not a Choice-value source** per `[D-065]`. SP UI engineer maintains SP Choice column allowed values in SP itself. Mismatches surface as `SHP-E001` at runtime; this YAML is not the safety net.
- **Not an SP display name registry** — display names live in SP; this YAML records the auto-generated internal name only. If you need the display name (e.g., for documentation), consult `docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` per architect Q5.
- **Not the canonical schema source** — HILDA canonical field names are owned by `core/src/template_schema/`. This YAML records the mapping; it cannot add new canonical fields (those require a `template_schema` change first).
- **Not a SP list provisioner** (D-DRAFT-X 2026-06-12 — still pending architect ratification 2026-06-25; P0 ADR triage candidate). This YAML is a READ input for SP UI engineer's manual SP list creation ceremony; HILDA does not consume it to call REST and create lists. List provisioning automation is deferred indefinitely (corp SP-2017 SP-alert + custom SP task requirement makes UI-side provisioning structurally necessary).
- **Not the TG schema source** per `[D-106]` (formerly D-DRAFT-Y; TGGroupBase Pydantic model DROPPED). TG schema/values live in `customizations/template_schemas/<customer_id>/tg_groups.yaml`; this YAML records only the canonical → SP-internal-column mapping for the denormalized TG fields on `delivery_items:`. TG schema changes go through `template_schema_ingestor`, not here.
- **Not a reader/writer mapping for SP-side `TasksTemplate` / `Tasks` / `Trials` / `Activities` / `Email` / `CommunicationLog` lists** per architect Q1 2026-06-25 — those 6 SP lists exist in the architect's TG site but are SP UI engineer's display surface only; HILDA does not touch them. This YAML records mappings ONLY for the 3 HILDA-touched lists (Milestones / Projects / Deliverables, per-customer).
- **Not a credential store** — credentials live in `credential_service/` per `[D-019]`. Never put session cookies, API tokens, NTLM passwords, or any auth material here.
- **Not the runtime AutomationRules file** — that's `customizations/rules/` (sister directory consumed by `rule_engine`). Two distinct drop-zones with distinct purposes.
- **Not a multi-tenant directory** — one deployment = one HILDA installation = one set of YAML files under `customers/`. The "multiple `customers/*.yaml`" is "HILDA serves multiple downstream customers within one deployment", not "multiple HILDA tenants share a config dir".

---

## Depends on

- *(none — this is a data drop-zone with no code dependencies)*

## Depended on by

- `core/src/sharepoint_integration/FileBasedListProvider` — reads `customers/*.yaml` + `devices/special_devices.yaml` at startup; serves the resolved column maps to `SpCrud` for every read/write per FR-30 scope lookup.
- Indirect via `sharepoint_integration`: every HILDA module that writes to SP (`tracker`, `dashboard`, `email_service` for CommunicationLog, `rule_engine` for AutomationRules read, `workflow_engine` for FR-84 writeback, `issue_tracker` + `customizations/issue_tracker` for CommunicationLog).
- **Cross-team consumer**: SP UI engineer references this YAML to know which SP internal column names HILDA expects. `docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` is the bidirectional comm artifact per architect Q5 2026-06-25 (3 worksheets: Milestones / Deliverables / Projects); supersedes stale `HILDA_SP_Schema.xlsx` + `DeliveryItem_visibility_review.xlsx` (both renamed `_DEPRECATED_2026-06-15.xlsx`).

---

## Deferred (Ph-2 / Ph-3+)

- **Ph-2 / Ph-3+ — Device-level overrides** (`devices/special_devices.yaml` with `device_overrides:` entries keyed on `customer_id` + `device_id` per `[D-091]`; `entity:` one of `delivery_items` / `milestones` / `projects`; per-(customer × device) list-name + column-map overrides). Not in Ph-1 scope per architect Q1 2026-06-25 (Ph-1 surface is per-customer only). Lands when a multi-device-per-customer scenario surfaces.
- **Ph-2 — additional fields per the 2026-06-08 corrected model rollout** (e.g., FR-86 NSDPathType / `unrouted_source_path` references) as their producing modules land Ph-2 surfaces. (`communication_log` SP list NOT in HILDA scope per architect Q3.)
- **Ph-2 — SP-side Choice value-set validation** (Ph-2 enhancement): per `[D-065]`, a Ph-3+ CI check might diff `template_schema/enums.py` against the Excel workbook's Choice-values columns and flag drift in PR review. This module wouldn't need to change; the CI check would compare two other artifacts.
- **Ph-3+ — K8s ConfigMap migration per `[D-025]`** — the YAML schema is unchanged; only the mounting mechanism shifts from Docker-Compose bind-mount to ConfigMap. No file-content change.
- **Ph-3+ — SP-list-based config provider** — an alternative `SharePointListProvider` implementation that reads its own config from SP (bootstrap problem solved via a tiny YAML pointer to "where in SP the config lives"). Currently rejected for added complexity; revisit when ops scale demands it.
- **Ph-3+ — per-PM credential isolation requires schema extension** per DEF-14: the `pm_credentials` list will likely grow Vault-pointer columns; deferred until `[D-019]` v2 lands.

---

## Test interface

This directory has no executable test surface of its own — the YAML is exercised through `core/src/sharepoint_integration/sharepoint_integration_cli.py`. Two relevant modes:

```
python -m core.src.sharepoint_integration.sharepoint_integration_cli --diagnostic
```
Loads all customer YAMLs + device-overrides; connects to SP (using current auth config); reads one item from each registered list; emits per-customer reachability + column-coverage report. Validates that this directory's YAML is structurally correct AND that the recorded SP internal column names actually exist in SP. Emits `SHP-RPT`.

```
python -m core.src.sharepoint_integration.sharepoint_integration_cli --dry-run --customer <slug>
```
Loads the specified customer's YAML without SP connection; reports missing required columns (per `template_schema` canonical fields) + any unused mappings. Pure local validation. Emits `SHP-MET`.

Both modes are documented under `core/src/sharepoint_integration/MODULE.md`'s Test interface section. **No CLI ships under this directory itself.**

---

<!-- BEGIN:STRUCTURE -->

_No public surface detected (module is empty or all-internal)._

<!-- END:STRUCTURE -->
