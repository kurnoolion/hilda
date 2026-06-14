## 2026-06-12 — Session: SP UI engineer 2026-06-10 review absorption + dashboard MODULE.md skeleton + multi-FR requirements pass

**Strand-bound session covering 4 streams of work:**

### Stream 1 — dashboard MODULE.md skeleton seeded
NEW `core/src/dashboard/MODULE.md` (architecture-phase doc-first design intent) with:
- Purpose: HTTP entry point for HILDA per [D-074]; 4 endpoints (FR-56 refresh, FR-57 docs, FR-61 download, FR-31 admin)
- Public surface: route signatures with docstrings; DashboardConfig; 6 error codes (DSH-E001..E004 + DSH-W001..W002)
- 11 Invariants covering auth, content negotiation, token freshness, reverse-proxy trust, no-writeback discipline
- 6 OPEN architectural decisions to lock during architecture review
- Test interface per [D-005] (CLI + mock harness + pytest)

### Stream 2 — Three decision drafts captured in decisions-draft.md
- **D-DRAFT-FR87**: TPM-resolution buttons move from SP-side field write to HILDA-tab same-origin form POST per [D-074]. 3 SP fields become read-only audit columns. Full Context/Decision/Why/3 rejected alternatives/7 consequences body.
- **D-DRAFT-FR87-ASYNC**: Sync-validate-and-enqueue boundary + 3-channel async error UX (inline badge / top-of-page banner / TPM email). Refines D-DRAFT-FR87 with sync/async semantics for dashboard's POST handler latency target (<500ms).
- **D-DRAFT-Z**: HILDA's runtime SP coupling restricted to Milestones + DeliveryItems lists only. Customer + Device + User + PMCredential data moves to YAML (`customizations/template_schemas/<customer_slug>/customer.yaml`). Milestone SP rows gain denormalized `customer_slug` + `device_slug` columns. Net: SP coupling drops 6 lists → 2 lists. Full Context/Decision/Why/3 rejected alternatives/9 consequences body.

### Stream 3 — ~21 FRs touched in requirements.md
- Foundation: FR-84 (no SP-page-JS polling), FR-57 (storage 2026-06-07 file-centric cleanup; HILDA dashboard renders HTML)
- Dashboard rendering: FR-58 (Confirmation no link-out), FR-59 (HILDA renders document section), FR-60 (HILDA displays review results), FR-61 (path-agnostic resolution + token stability)
- Buttons: FR-56 (column model + 3-bucket structure + Refresh button + cleanup), FR-62 (Ph-2 HILDA-rendered upload + ReadyForSubmission state), FR-64 (is_milestone_gating semantic), FR-87 (HILDA-tab POST + sync/async + dropdown filter + progress UX)
- Owner identity model: FR-2 (split corp_usa_email + corp_email + corp_id), FR-9 (preference rule), FR-71 (specific field names per FR-2)
- Data flow precision: FR-2 + FR-3 + FR-15 (tracker creation flow per [D-073]+[D-071]+[D-064]), FR-7 (10-vs-11 delivery_state distinction; Not Started SP-only)
- D-DRAFT-Z absorption: FR-2 + FR-13 + FR-77 + FR-31 (customer_slug + device_slug from YAML)
- Storage cleanup: FR-57 + FR-79 (primary/secondary association removed; file-centric model alignment)
- FR-82 (item_description ops-only)
- FR-56 + FR-7 (10-vs-11 delivery_state explicit cross-ref)

### Stream 4 — SP UI engineer artifacts updated
- `customizations/sharepoint_config/MODULE.md` — D-DRAFT-Y absorption (TGGroups denormalized, 7-list framing); D-DRAFT-Z scope restriction Invariant; denormalized customer_slug + device_slug on milestones
- `customizations/sharepoint_config/customers/example.yaml` — expanded with D-DRAFT-Z denormalized fields
- `docs/sp_ui_engineer/HILDA_SP_Schema.xlsx` — Milestones tab + tab annotations for D-DRAFT-Z
- NEW `docs/sp_ui_engineer/DeliveryItem_visibility_review.xlsx` — 54-field display/writable matrix per FR-56 column model
- NEW `docs/sp_ui_engineer/SP_UI_summary_requirements.md` — condensed summary made compliant
- `sharepoint/REQUIREMENTS.md` — substantial cleanup (TGGroups OBSOLETE section, denormalized columns, schema delta absorption)

### Concerns & open items
- **D-DRAFT-Z YAML file creation pending**: `customer.yaml` schema needs to be created and FileBasedListProvider extended to read it
- **SP UI engineer schema propagation to template_schema/models.py pending**: owner_corp_usa_email + owner_corp_email + drr + ir_ffw_p1 form factor flags + path_slug→item_path_slug + email_group_alias→tg_email_group_alias renames need to land in code
- **Reverse-proxy registration binding contract** still unresolved (STATUS Flag from today; 4 open architectural questions for dashboard architecture review)

### Teammate coordination updates (via rebases)
- Member 2 (llm-v1) shipped Ph-1 LLMGatewayServer (mock-first; tri-backend); LAND GATE note added to llm-v1/STRAND.md (don't land llm-v1 before dashboard-v1)
- Member 3 (rule-engine-v1) shipped massive Ph-1 implementation (~21 files, 780-line test suite, 8 sub-modules + 2 CLIs)

### Next session candidates
- Continue requirements review at user's pace
- Switch to architecture for Order (A) batch: MODULE.md + xlsx + example.yaml + template_schema/models.py updates for SP UI engineer schema absorption (owner email split + form factor flags)
- Resume dashboard MODULE.md architecture review (6 open decisions + 2 STATUS Flags from today)
- /land-strand dashboard-v1 when work converges
