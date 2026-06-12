# dashboard-v1 — decisions-draft

Drafts of decision-worthy items surfaced during this strand. Promoted to canonical `DECISIONS.md` with the next sequential `D-XXX` at `/land-strand` time.

---

## D-DRAFT-FR87: FR-87 TPM-resolution UX moves from SP-side field write to HILDA-tab same-origin form POST

**Date drafted**: 2026-06-12

**Context**: FR-87 (TPM document resolution — strict order A → B → C) was originally specified with SP-side field write semantics: TPM clicks button in SP UI → SP writes `tpm_reassignment_target_item_id` / `tpm_resolved_doc_type` / `tpm_revision_resolution` field on the DeliveryItem SP row → SP-alert fires per `[D-047]` → `email_service.sp_alert_parser` routes to a HILDA-side resolution handler → HILDA processes + writes back to SP. This matched the FR-84 SP→HILDA channel pattern used by every other SP button (Start Collection, Submit, Close All Items, Send Reminder, Approve, Refresh).

**Decision**: For FR-87 specifically, TPM-resolution buttons move from SP UI to HILDA's rendered document section (per FR-59 / `[D-074]` Variant A). TPM views document in HILDA tab; FR-87 button surfaces inline on document rows where `nsd_path_type ∈ {staged_not_classified, staged_not_revision, unrouted}`; clicking does a **same-origin form POST** from HILDA tab to HILDA's dashboard endpoint (`/docs/<delivery_item_id>/reassign`, `/resolve-doc-type`, `/resolve-revision`); HILDA processes directly + writes back to SP via `[D-064]` REST writeback as **read-only audit columns** (TPM-editable input semantics removed from the 3 SP fields). No SP-alert round-trip for FR-87.

**Why**:
- **(a)** TPM needs to SEE the document content before resolving (especially step B doc_type and step C revision picks). Document is in HILDA's rendered tab per FR-59 / `[D-074]` Variant A; rendering the same document in BOTH the SP item dialog AND HILDA tab would be duplicate effort and stale-state risk.
- **(b)** SP-side field write was UX-awkward for per-document actions on a per-item SP dialog — one DeliveryItem can have multiple documents in different staged paths; SP per-item dialog can't cleanly surface per-document buttons.
- **(c)** HILDA-tab same-origin POST is unblocked by corp policy per `[D-074]` (the cross-origin XHR ban only applies SP→HILDA; HILDA→HILDA same-origin is unrestricted). No firewall fight, no SP-alert latency.
- **(d)** Invalid (item_type, doc_type) combinations can be rejected at the dashboard endpoint with a form-redisplay error — better UX than the SP-alert round-trip model where invalid saves silently landed on staged-not-classified.
- **(e)** Eliminates one SP-alert action verb mapping per `sharepoint/REQUIREMENTS.md §7.4` (3 verbs gone: `tpm_reassign_to_workitem` / `tpm_resolve_doc_type` / `tpm_resolve_revision`).

**Rejected alternatives**:
- **(α) Keep SP-side field writes (original FR-87 model)**: rejected — per-document buttons in per-item SP dialog UX problem (b); SP-alert latency adds 5-15s to TPM round-trip vs near-instant HILDA-tab POST; invalid-pair UX is worse.
- **(β) Render FR-87 dropdowns BOTH in SP UI and HILDA tab**: rejected — duplicate rendering effort, dual source-of-truth risk on what TPM picked.
- **(γ) Dedicated FR-87 SP web part separate from milestone view**: rejected — same UX problems as (α); doesn't help.

**Consequences**:
- Dashboard module gains 3 new POST endpoints: `/docs/<delivery_item_id>/reassign`, `/resolve-doc-type`, `/resolve-revision` (added to dashboard MODULE.md as part of dashboard-v1 strand work; soft-flag because additive Public surface).
- The 3 SP fields `tpm_reassignment_target_item_id` / `tpm_resolved_doc_type` / `tpm_revision_resolution` become **read-only audit display columns** in SP DeliveryItems list — TPM-editable input semantics removed. SP UI engineer applies column-level read-only permission. Schema discipline added to `customizations/sharepoint_config/MODULE.md` 2026-06-12 (part of D-DRAFT-FR87).
- `sharepoint/REQUIREMENTS.md §4.9 / §4.10 / §4.11` need rework parallel to this FR-87 rewrite (buttons live in HILDA tab, not SP web part).
- `sharepoint/REQUIREMENTS.md §7.4` (SP-alert action-verb conventions) — 3 verbs for FR-87 are obsoleted; remove from §7.4 (sp_alert_parser no longer needs to recognize them).
- `email_service.sp_alert_parser` Ph-1 implementation does NOT need FR-87 action-verb handlers (reduces email_service module's Ph-1 surface area slightly).
- Dashboard MODULE.md adds 3 new error codes: DSH-E005 (FR-87 step A invalid target item), DSH-E006 (FR-87 step B invalid doc_type for item_type), DSH-E007 (FR-87 step C revision picker mismatch) — to be locked during dashboard architecture review.
- Storage's `tpm_resolve_doc_type` + `tpm_resolve_revision` + `reassign_document_to_workitem` storage APIs (already landed per `D-071` / `D-072`) are unchanged — the same APIs serve both the old SP-alert model (if revived) and the new HILDA-tab POST model. The strand work for FR-87 is purely in dashboard (new endpoints) + sharepoint_config (column permission discipline).

**Anchors**: `[D-074]` (Variant A SP↔HILDA integration); `[D-053]` impl note 2026-06-08 (FR-87 strict A → B → C); `[D-047]` (SP-alert channel — FR-87 no longer uses it); `[D-064]` (HILDA→SP REST writeback — used for audit-column updates after FR-87 click); `[D-006]` (Kerberos auth — covers HILDA-tab same-origin POST).
