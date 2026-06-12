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

---

## D-DRAFT-FR87-ASYNC: FR-87 button POST handler is sync-validate-and-enqueue; async tail runs in workflow_engine; async-tail errors surface via 3 channels

**Date drafted**: 2026-06-12

**Context**: D-DRAFT-FR87 locked that FR-87 buttons move to HILDA-tab same-origin POST. The dashboard endpoint POST handler must decide: (a) block on full FR-87 processing (sync to TPM, including the `[D-039]` Step 2 LLM re-run for Step B which is 10-30s) and return either form-redisplay-error or 303-redirect-with-final-state; OR (b) do only sync work (validation + immediate storage write + workflow_engine task enqueue) and return 303 in <500ms, with the async tail completing in workflow_engine and errors surfacing via separate UX channels after the redirect. The original D-DRAFT-FR87 wording conflated these — "invalid choices rejected at the endpoint" was vague about whether all errors block or only validation errors block.

**Decision**: **Option (b) — sync-validate-and-enqueue.** Dashboard's FR-87 POST handlers do **only** synchronous work:
- Validate auth, Choice-value membership, FR-86 alignment, target item (Step A), `doc_id_slug` existence (Step C), FR-87 button token freshness
- Call storage's `reassign_document_to_workitem` / `tpm_resolve_doc_type` / `tpm_resolve_revision` for the immediate DB row update + NSD file move (sub-second)
- Enqueue workflow_engine task for the async tail
- Return 303 redirect to `GET /docs/<delivery_item_id>`

Net latency target: **sync POST handler returns 303 in <500ms** (validation + sub-second storage + sub-second enqueue). Async tail in workflow_engine: `[D-039]` Step 2 LLM re-run (Step B only; 10-30s typical); FR-86 storage matrix re-run; final NSD path move if `[D-039]` resolves; FR-77 carrier-upload if doc reaches classified (subject to `no_customer_upload`); `[D-064]` SP writeback for audit columns; review pipeline trigger if `review_required=true`.

**Sync errors surface as form-redisplay**: DSH-E005 (target item invalid Step A), DSH-E006 (FR-86 alignment violation Step B), DSH-E007 (revision picker mismatch Step C), STR-E005 (cross-milestone association), STR-E007 (expired button token).

**Async-tail errors surface to TPM via three channels** (in priority order):
- **(α) Primary — inline document-row badge on next `/docs/<delivery_item_id>` visit**: dashboard renders the row with a status badge read from CommunicationLog query on `(file_hash, delivery_item_id)` — e.g., "🔴 `[D-039]` re-run failed: LLM gateway timeout. Retry?" with retry button.
- **(β) Secondary — top-of-page banner on `/docs/<delivery_item_id>`**: for the most recent FR-87 action within N minutes (configurable; default 5 min), show banner: "Last action `tpm_resolve_doc_type` had a downstream error: <message>. [Retry] [Dismiss]". Per-TPM session.
- **(γ) Escalation — TPM email**: when async failure is unrecoverable AND > N minutes elapsed since submit (configurable; default 10 min), send TPM email with error details + retry link. Reserved for genuinely-stuck cases — too noisy as primary channel.

**Why option (b)**:
- **(a) sync model**: rejected — 10-30s blocked HTTP response is poor UX (corp reverse proxy may timeout; blocks dashboard worker thread; user perceives "frozen page"; not consistent with HILDA's async-by-default workflow_engine pattern). The cleaner either-or outcome is appealing but the latency cost is too high.
- **(b) async-with-status (chosen)**: matches HILDA's overall async pattern; sub-500ms HTTP response keeps reverse proxy happy; TPM experience: click → immediate redirect → "processing" badge → eventual completion (via focus refresh OR new tab opens). The three-channel async-error UX (badge / banner / email) gracefully handles real-world async failures including LLM timeouts and SP writeback latency.

**Rejected alternatives**:
- **(γ) WebSocket/SSE push notifications** for async completion: rejected — adds JS state engine to dashboard's "no SPA, no client-side framework" Invariant; same-origin would work in HILDA tab, but the badge-on-next-render approach is simpler and matches server-render-only discipline.
- **(δ) Synchronous AJAX polling from HILDA tab to dashboard for async status**: rejected — same reason as γ; introduces client-side JS state.
- **(ε) Long-polling**: rejected — same.

**Consequences**:
- Dashboard's FR-87 POST handlers MUST be sub-500ms-budget; LLM calls + FR-77 + SP writeback MUST be in workflow_engine task bodies, not in dashboard's sync path.
- Dashboard adds inline-badge + top-of-page-banner rendering to `GET /docs/<delivery_item_id>` — reads CommunicationLog for status of recent FR-87 actions on docs in this DeliveryItem; renders the appropriate badge/banner.
- workflow_engine task body for FR-87 async tail emits CommunicationLog rows on success AND failure (with detailed error code + message); these rows drive the dashboard's badge/banner rendering.
- New error codes for the async-tail outcomes: `DSH-W003` (FR-87 async tail in progress; informational), `DSH-W004` (FR-87 async tail failed; surfaced as badge); existing `LLG-W006` (LLM rate-limit; surfaced as badge for Step B specifically); existing `STR-W007` (stale-staged-document) might fire if NSD path move fails post-commit.
- TPM email integration for escalation channel (γ): uses existing `email_service` outbound capability; new email template for FR-87 stuck-resolution notification; rate-limited (one email per (TPM, document) pair per 24h).
- FR-87 button token freshness check: tokens generated at HTML-render time (per FR-61 download token pattern); 300s TTL; STR-E007 if expired (form-redisplay with "session expired" + auto-redirect to fresh `/docs/<id>`).
- Async-tail errors that occur DURING the workflow_engine task body retry per workflow_engine's standard Celery retry policy; only after retries exhaust does the error become user-surfaced via channels (α)/(β)/(γ).

**Anchors**: `[D-022]` (Celery + Redis broker — workflow_engine async pattern); `[D-074]` (Variant A SP↔HILDA integration); `[D-039]` (LLM revision determination — Step 2 is the long-haul work in FR-87 Step B); `[D-064]` (HILDA→SP REST writeback for audit columns); D-DRAFT-FR87 (parent decision; this one refines the sync/async boundary).
