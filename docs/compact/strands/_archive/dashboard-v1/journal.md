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


## 2026-06-13/14 — Session: SP UI engineer xlsx review absorption + D-DRAFT-Z v2 rewrite + final R&R lock + multi-FR cascade

**Strand-bound session covering 5 streams of work:**

### Stream 1 — SP UI engineer 2026-06-14 xlsx review (DeliveryItem_visibility_review)
Full 80-row xlsx parsed and reviewed line-by-line. 24 issues + Q1/Q2/Q3 `is_milestone_gating` gating cascade resolved per user decisions:
- Cascade: FR-28 `MilestoneAllClosed` semantic gating-aware; FR-64 Close All Items extended with non-gating cascade; FR-76 storage cleanup trigger condition; FR-78 default work-item implicitly `is_milestone_gating=true`
- doc_type 1:1 derivation deprecation cleanup (FR-26 / FR-52 step 4 / FR-55 / Appendix B.1) → FR-85 classification pipeline
- FR-73 step (iv) — JS polling → focus-aware refresh per [D-074]
- FR-62 + FR-56 — pm_approval_at clearing moved SP-side per [D-068] discipline
- Various stale wording sweeps (slug → id, last_updated → Modified, ingest_source SharePointUI → HILDA_DASHBOARD, etc.)

### Stream 2 — D-DRAFT-Z multi-revision (v1 → v2 → AMEND-14b → AMEND-14c)
SP UI engineer feedback arrived in tranches; D-DRAFT-Z iterated three times same day:
- **v2 rewrite (early 2026-06-14)**: HILDA runtime SP coupling expands from 2 lists to 4 lists (Customers + Devices + Milestones + DeliveryItems). customer.yaml dropped entirely.
- **AMEND-2026-06-14b**: Joint `(Model, ProjectID)` Device lookup key + slug→id rename + single template.yaml folding tg_groups.yaml.
- **AMEND-2026-06-14c (final R&R lock)**: SP UI engineer's `setup_milestone` TPM task is **SOLE** SP row-creation path; HILDA NEVER creates SP rows; template.yaml hierarchy = customer → milestones (with work-items, shared) + devices (sibling). Supersedes 14b on Devices lookup (single PK = ProjectID, not joint key).

### Stream 3 — ~30 FRs touched in requirements.md
- FR-2 (R&R lock + customer-onboarding flow + 5-field SP-alert routing key + slug→id rename + tg_groups.yaml drop)
- FR-5 (SP-side uniqueness with ProjectID as Devices PK)
- FR-6 (4-value Milestone.status enum)
- FR-7 (SPUI added to tracking_modality)
- FR-11 + FR-14 (Milestone.target_date sole TPM-editable + cascade dedup)
- FR-13 (NSD path slug source from SP Customers/Devices; NTLM SMB)
- FR-15 (last_reminder_triggered_at SP-managed)
- FR-25 (PM ≡ TPM role-collapse clarifier)
- FR-40 (single template.yaml; milestones at customer level; project_id NOT in template)
- FR-56 (URL prefix discipline; Approve button SP-side atomic write)
- FR-58 / FR-59 (View Documents anchor `<prefix>/<item_id>`)
- FR-62 (Upload Document anchor; HILDA_DASHBOARD ingest_source)
- FR-71 (tg_groups.yaml → template.yaml)
- FR-81 (tracking_enabled location)
- FR-82 (HILDA ops only path b clarification)
- FR-84 (5-field routing key; subject/body extraction; resolution chain)
- NFR-8 + NFR-10 (NTLM-only)
- Multiple Appendix C summary refreshes (FR-13, FR-15, FR-56, FR-62, FR-72, FR-73, FR-78, FR-83)

### Stream 4 — D-DRAFT inventory in decisions-draft.md (12 entries; pending land-strand)
1. D-DRAFT-FR87 (TPM-resolution UX → HILDA-tab POST) — 2026-06-12
2. D-DRAFT-FR87-ASYNC (sync-validate-and-enqueue) — 2026-06-12
3. D-DRAFT-Z v2 (4-list SP coupling) — 2026-06-12 rewritten 2026-06-14
4. D-DRAFT-FR64-GATING (is_milestone_gating activated) — 2026-06-12
5. D-DRAFT-FR62-RFS (ReadyForSubmission revert) — 2026-06-12
6. D-DRAFT-OWNER-EMAIL-SPLIT — 2026-06-12
7. D-DRAFT-FORM-FACTOR-EXPAND — 2026-06-12
8. D-DRAFT-AA (Milestone.target_date cascade dedup) — 2026-06-13
9. D-DRAFT-D073-IMPL-2026-06-14 (Customers/Devices SP column additions)
10. D-DRAFT-D006-IMPL-2026-06-14 (NTLM-only confirmation)
11. D-DRAFT-Z-V2-AMEND-2026-06-14b (joint key + slug→id + single template.yaml)
12. D-DRAFT-Z-V2-AMEND-2026-06-14c (final R&R lock; SP UI engineer sole row creation)

### Stream 5 — Discipline observance
- **Violation caught mid-session**: directly edited canonical DECISIONS.md with D-073 impl note while strand-bound; reverted and re-drafted in decisions-draft.md as `D-DRAFT-D073-IMPL-2026-06-14` (promotes as append to D-073 at land-strand).
- **STATUS.md handling**: only "Active phase" + "Last updated" headers updated in-place; Done + Flags deferred to land-strand promotion (per strict strand-binding discipline locked this session).
- **PROJECT.md edit**: PM≡TPM note added to Users line — single sentence; arguably product-spec rather than strand-state. Left as canonical edit.

### Concerns & open items (promote to canonical STATUS.md Flags at land-strand)
- **dashboard-v1 strand: 12 D-DRAFTs pending promotion** — post-land sweep needed on requirements.md to replace ~50 `D-DRAFT-*` refs with assigned `D-XXX` IDs.
- **setup_milestone task handoff sequencing** — SP UI engineer sync needed for: per-(customer, device) granularity confirmation, sequence with FR-8 Start Collection (distinct actions), template.yaml line 28 minor ambiguity (milestone block inside second device block — confirm not device-scoped).
- **example.yaml + xlsx cascades after D-DRAFT-Z v2**: `customizations/sharepoint_config/customers/example.yaml` SP-list schema mapping needs Milestones-tab denormalization removal + Customers/Devices column additions per D-DRAFT-D073-IMPL + owner-email split + form-factor expansion. Defer to architecture phase.
- **template_schema MODULE.md follow-ups**: `TSC-W005` TG-equality validator on `DeliveryItemBase`; 7-flag form-factor expansion (drr, ir_ffw_p1); owner_corp_usa_email/owner_corp_email split.
- **email_service MODULE.md follow-up**: `EML-W008` error code registration (Model cross-validation mismatch in sp_alert_parser).
- **HILDA_SP_Schema.xlsx Milestones tab cleanup**: drop denormalized customer_id/device_id rows.
- **Stale `Last drift-check` marker** (2026-06-10, 4+ days old). Run `/drift-check design` after land-strand + post-land sweep (so drift-check sees canonical D-XXX, not D-DRAFT-*).
- **D-DRAFT-FR87 + FR87-ASYNC entries** in decisions-draft.md still anchor `[D-006] (Kerberos auth)`; one fixed this session, other remains — verify at land-strand.

### Teammate coordination
- No teammate strand activity sync'd this session.

### Next session candidates
- `/land-strand dashboard-v1` (promote 12 D-DRAFTs to D-XXX; consolidate Done + Flags into canonical STATUS.md; archive strand folder)
- Post-land sweep on requirements.md to replace `D-DRAFT-*` refs with assigned `D-XXX`
- Open new strand for continued FR review (suggested name: `fr-review-continuation-v1` or `arch-handoff-v1`)
- `/drift-check design` after sweep + new strand bind
