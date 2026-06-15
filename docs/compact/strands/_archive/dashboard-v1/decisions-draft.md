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

**Anchors**: `[D-074]` (Variant A SP↔HILDA integration); `[D-053]` impl note 2026-06-08 (FR-87 strict A → B → C); `[D-047]` (SP-alert channel — FR-87 no longer uses it); `[D-064]` (HILDA→SP REST writeback — used for audit-column updates after FR-87 click); `[D-006]` (on-prem AD auth — NTLM per 2026-06-14 impl note; covers HILDA-tab same-origin POST authentication via the same Negotiate flow).

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

---

## D-DRAFT-Z: HILDA runtime SP coupling = 4 lists (Customers + Devices + Milestones + DeliveryItems); SP-list-row IDs resolve customer/device slugs at SP-alert receive — no YAML for customer/device data

**Date drafted**: 2026-06-12; **REWRITTEN 2026-06-14** (originally proposed 2-list coupling + customer.yaml YAML; reversed after slug-resolution gap analysis showed customer.yaml didn't earn its own file and SP-list reads are the cleaner bridge).

**Context**: SP UI engineer 2026-06-12 review surfaced that HILDA's Linux service layer currently has runtime read/write dependencies on 6 SP lists. Earlier 2026-06-12 ratifications already eliminated User + PMCredential SP-list dependencies. User initially proposed (2026-06-12 D-DRAFT-Z v1) moving Customer + Device data to `customer.yaml` and denormalizing `customer_id` + `device_id` onto Milestone SP rows. **2026-06-13 review** of the SP-alert routing key `(ProjectID, MinorMilestone, ItemNumber)` showed `MinorMilestone` is uniquely scoped to a (customer, device) per FR-5 but neither slug is in the tuple — `sp_alert_parser` cannot map alert → slugs without an extra step. **2026-06-14 review** of pruned customer.yaml showed it carried only 2 leaf values (`customer_jira_url` + `assigned_pm_id`) under a folder name already encoding `customer_id` — the file didn't earn its own existence. User proposed promoting Customers + Devices SP lists from "SP-display-only" to "HILDA-readable" — slug values + the 2 leaf fields all live on existing SP rows.

**Decision**: HILDA's Linux service layer runtime SP coupling is **4 SP lists**: Customers + Devices + Milestones + DeliveryItems (read scope); writeback (per `[D-064]`) is Milestones + DeliveryItems only — HILDA does NOT write Customers or Devices SP rows (ops edits those directly via SP UI). Users + PMCredentials remain SP-display-only (HILDA reads neither at runtime). **No `customer.yaml` file is created**; customer + device data lives entirely on SP. SP-alert resolution flow: (1) `sp_alert_parser` extracts SP row IDs from the alert; (2) does SP REST GET on Customers row by `customer_id` → reads `customer_id` + `customer_jira_url`; (3) does SP REST GET on Devices row by `device_id` → reads `device_id` + `assigned_pm_id`; (4) caches reads for the alert dispatch duration to amortize batched per-item alerts in the same milestone.

**SP-list schema additions** (SP UI engineer adds, ops-editable only):
- **Customers SP list** (2 new columns): `customer_id` (HILDA-readable identifier; ops sets at customer-onboarding), `customer_jira_url` (FR-25 base URL).
- **Devices SP list** (2 new columns): `device_id` (HILDA-readable identifier), `assigned_pm_id` (PM identity for FR-19/FR-25/FR-51 credentialed external calls; per FR-25, PM ≡ TPM in this deployment).
- TPM cannot edit any of these 4 fields (misconfig on `customer_jira_url` would break FR-25 polling; misconfig on slugs would break HILDA's NSD path construction).

**Why**:
- **(a) Single source of truth**: all customer + device data lives on SP. Ops edits SP rows directly via TPM SP UI's ops-role view — no git commit + bind-mount + SIGHUP cycle for changing `customer_jira_url` or `assigned_pm_id`. TPM, ops, and HILDA all see the same row.
- **(b) Eliminates customer.yaml**: the file would have carried only 2 leaf values per pruned 2026-06-14 schema; not worth a separate file + loader + reload path.
- **(c) Solves the slug-resolution gap**: SP-alert tuple doesn't encode slugs; HILDA reads them from SP rows directly. Cost: 2 SP REST GETs per alert (~100ms); amortized via per-dispatch cache.
- **(d) Cleaner SP UI engineer ownership** for Customer/Device UX (validation, permissions, presentation); HILDA contract becomes "we read 4 lists, write to 2, never touch Users/PMCredentials."
- **(e) Closes the open question** about who maintains Customer/Device rows: ops via SP UI (with role-based control restricting HILDA-readable fields to ops, not TPM).

**Rejected alternatives**:
- **(α) D-DRAFT-Z v1 (customer.yaml + denormalized slugs on Milestone)**: rejected — customer.yaml carries 2 leaves under a folder name that already encodes customer_id (doesn't earn its file); denormalization is ops-coordination overhead with no offsetting HILDA benefit (HILDA can resolve via Customers+Devices SP-list reads under same caching budget).
- **(β) Encode customer_id + device_id in the SP-alert routing tuple itself (expand 3-key → 5-key)**: viable but requires SP UI engineer to inject 2 extra fields into every SP-alert email template; brittle to SP-alert config drift; loses SP as single point of truth (slugs would live in 2 places: SP row + alert payload).
- **(γ) HILDA-written Title encoding (write `Title = "<customer_id>__<device_id>__<milestone_name>"` on Milestone rows; parse from alert subject)**: viable; zero new SP columns; but Title becomes HILDA-format-coupled, fragile across SP UI engineer's display changes.
- **(δ) Keep HILDA reading all 6 SP lists**: rejected — Users + PMCredentials are SP-display-only by `[D-019]` discipline; expanding scope back is unjustified.

**Consequences**:
- **No** `customer.yaml` file; **no** `customizations/template_schemas/<customer_id>/customer.yaml` directory pattern for customer data. (The 2-YAML-per-customer model becomes `template.yaml` + `tg_groups.yaml` only per FR-40.)
- HILDA's runtime SP coupling grows from D-DRAFT-Z v1's 2-list to **4-list** (Customers + Devices added). SP REST GET budget: 2 extra reads per SP-alert dispatch; cached for batch duration.
- `sharepoint_integration.SpCrud.get_items` for Customers + Devices becomes called at runtime; for Users + PMCredentials remains not called.
- `Milestone` SP list does NOT gain `customer_id` + `device_id` columns (was the D-DRAFT-Z v1 plan; reversed 2026-06-14). Milestone has `device_id` lookup → Devices row carries slug.
- `HILDA_SP_Schema.xlsx` Milestones tab: any prior `customer_id` / `device_id` rows are dropped. Customers tab gains `customer_id` row (xlsx row 76); Devices tab gains `device_id` + `assigned_pm_id` rows (xlsx rows 79-80). Confirmed in 2026-06-14 xlsx review.
- `customizations/sharepoint_config/customers/example.yaml` (SP-side schema mapping): Customers + Devices sections include the new columns; Milestones section drops `customer_id` + `device_id` denormalization. Architecture-phase cascade.
- SP UI engineer's role-based control (already confirmed per xlsx row 13 owner_corp-usa_email pattern) restricts edit access to the 4 new HILDA-readable fields to ops role only (TPM role sees them read-only).
- TPM-runtime edits to Customer/Device SP rows DO fire HILDA-bound SP-alerts on these lists (alerts trigger HILDA cache invalidation for the affected row).
- Ops workflow for Customer/Device changes: ops edits SP row via SP UI → SP-alert fires → HILDA invalidates cache for that customer_id/device_id → next alert refetches. No git/YAML/SIGHUP.
- Multiple FR rewrites already in place: FR-2 (no customer.yaml; tracker creation resolves via SP reads), FR-13/FR-31/FR-77 (slug source is SP via alert-driven cache, not YAML), FR-40 (2-YAML schema: template + tg_groups), FR-84 (no Milestone denormalization).

**Anchors**: `[D-001]`, `[D-004]`, `[D-006]`, `[D-019]` (credential discipline — PMCredential SP list stays HILDA-unread), `[D-020]` (SharePointListProvider — extends to Customers + Devices SP), `[D-047]` (SP-alert channel; resolution-via-SP-read pattern documented in FR-84), `[D-051]` impl note (TG denormalization — unchanged; separate concern from customer/device), `[D-064]` (writeback Milestones + DeliveryItems only — unchanged), `[D-068]` (SP-side audit field write pattern — generalized), `[D-071]` (storage doesn't mirror DI; caller-resolves applies to Customer+Device too), `[D-073]` (SP UI engineer provisions — extended via impl note 2026-06-14 for 4 new columns).

**Supersedes**: D-DRAFT-Z v1 (2026-06-12; 2-list scope + customer.yaml + Milestone denormalization).

---

## D-DRAFT-FR64-GATING: `is_milestone_gating` semantic activated in FR-64 (was vestigial)

**Date drafted**: 2026-06-12

**Context**: SP UI engineer 2026-06-10 review surfaced that `is_milestone_gating` field on `template_schema.DeliveryItemBase` (renamed from `milestone_gating` per template_schema/MODULE.md Invariant 2026-06-12) carried no functional effect — all items gated milestone closure regardless of flag value per the original FR-64 enablement check ("all items in milestone are in `{SubmittedToCustomer, Closed}` AND at least one in `SubmittedToCustomer`"). User asked: "let us make this field is_milestone_gating for milestone closure."

**Decision**: FR-64 Close All Items enablement check changes to use `is_milestone_gating`: **enabled when all `is_milestone_gating=true` DeliveryItems in the milestone are in `{SubmittedToCustomer, Closed}` AND at least one `is_milestone_gating=true` item is in `SubmittedToCustomer`**. Items with `is_milestone_gating=false` do NOT block enablement and may remain in any non-blocking state when the action fires. On activation, closure scope still includes all `SubmittedToCustomer` items in the milestone regardless of gating flag (flag affects enablement only, not closure action scope). Field is **YAML-only / NOT TPM-editable** — ops/PM team identifies critical-for-closure items at template creation time.

**Why**:
- All-items-gate model is unnecessarily strict for milestones with optional waivers, advisory items, sustaining test reports, etc. that ops/PM team identifies as non-critical for milestone closure
- Field already existed (carried over from prior schema; renamed 2026-06-12); needed a functional purpose
- Matches user intent ("make this field for milestone closure")
- Closure scope kept unchanged (all SubmittedToCustomer items close regardless) — gating affects enablement, not action — keeps existing TPM Close All Items behavior aligned with NFR-5 PM-approval-gate semantic
- Rejected alternatives: **(α) keep vestigial** — wasted schema field; **(β) extend closure scope to only-gating items** — leaves non-gating items in SubmittedToCustomer stuck open after Close All; TPM would have to manually close each via FR-14; operational regression.

**Consequences**:
- FR-64 enablement check changes per Decision above; original wording preserved struck-through per requirements.md ID-stability convention
- Ops/PM template-authors must consciously identify critical-for-closure items at template creation (`is_milestone_gating: true` for items that MUST be closed before milestone close)
- `is_milestone_gating` stays YAML-only (NOT TPM-editable per user ratification — locked in FR-56 column model bucket (a))
- `customizations/sharepoint_config/MODULE.md` example schema + `HILDA_SP_Schema.xlsx` DeliveryItems tab already reflect `is_milestone_gating` rename (committed 2026-06-12)
- `template_schema/MODULE.md` Invariant (rename) already documents the new functional semantic via cross-reference to FR-64
- New canonical state for the field: was orphan; now load-bearing for FR-64 Close All Items button enablement

**Anchors**: FR-64 (rewritten 2026-06-12), template_schema/MODULE.md Invariant 2026-06-12 (`is_milestone_gating` rename + immutability), `[D-068]` (PM approval recording — unaffected; gating is closure-time concern, not approval-time).

---

## D-DRAFT-FR62-RFS: ReadyForSubmission added to FR-62 upload allowed states with revert-to-UnderPMReview semantic

**Date drafted**: 2026-06-12

**Context**: FR-62 (Ph-2 dashboard-rendered upload form per `[D-074]`) originally allowed uploads only when `delivery_state ∈ {DocumentReceived, UnderPMReview, SubmittedToCustomer}` plus the `Open` state when `tracking_enabled=False` per FR-81 no-tracking-TG fallback. User 2026-06-12 surfaced real TPM use case: late doc upload needed after PM approval but before FR-63 Submit fires (supplementary test report; customer-induced revision discovered late; doc_count overflow due to ambiguous original spec; miscellaneous artifact TPM realizes is missing).

**Decision**: `ReadyForSubmission` added to FR-62 allowed states. State transition on upload from `ReadyForSubmission` **reverts to `UnderPMReview`** (matches SubmittedToCustomer revert pattern). HILDA clears `pm_approval_at` + `pm_approval_pm_id` per `[D-068]` impl note 2026-06-12 clearing discipline. TPM must re-approve before next FR-63 Submit fires.

**Why**:
- Real TPM use case: late additional docs needed before submission cycle (waiting until SubmittedToCustomer to upload then revert is operationally annoying — TPM should pre-empt)
- Revert pattern matches SubmittedToCustomer (already established): stale PM approval becomes invalid when item content changes; PM must re-approve fresh state
- Clearing pm_approval_at + pm_approval_pm_id maintains audit consistency per `[D-068]` impl note clearing discipline
- Maintains NFR-5 PM-approval gate (no submission without fresh approval)
- Rejected alternatives: **(α) don't allow upload in ReadyForSubmission** — forces TPM to wait until SubmittedToCustomer (then upload triggers revert); operationally clunky; **(β) allow upload without revert** — silently invalidates PM approval; defeats NFR-5 PM-approval-gate-before-customer-facing semantic.

**Consequences**:
- FR-62 enabled state set grows from 3 to 4 states (`DocumentReceived`, `UnderPMReview`, `ReadyForSubmission`, `SubmittedToCustomer`); plus Open state for no-tracking TG per FR-81
- ReadyForSubmission → UnderPMReview transition added to delivery_state machine
- HILDA must clear `pm_approval_at` + `pm_approval_pm_id` on the transition (matches `[D-068]` impl note clearing discipline for rewind paths per `[D-067]` + entry to UnderPMReview discipline)
- TPM must re-approve before next FR-63 Submit fires
- Tracker MODULE.md state-machine + transitions code must be updated to include this transition (Ph-2 dev — tracker module is currently in design only)
- FR-87 step (A) reassignment doesn't change semantic (already covered by FR-83); FR-62 upload is distinct from FR-87 reassignment

**Anchors**: FR-62 (Ph-2; rewritten 2026-06-12), `[D-068]` impl note 2026-06-12 (pm_approval clearing discipline on rewind paths), `[D-067]` (customer RFI rewind from SubmittedToCustomer — sets the precedent pattern for revert-on-content-change), NFR-5 (PM-approval gate before customer-facing action — preserved).

---

## D-DRAFT-OWNER-EMAIL-SPLIT: owner_email split into corp_usa_email + corp_email (per-item AND TG-level)

**Date drafted**: 2026-06-12

**Context**: SP UI engineer 2026-06-10 review proposed splitting the single `owner_email` field on `template_schema.DeliveryItemBase` into two distinct email fields to handle corp's USA-vs-non-USA owner population: `owner_corp_usa_email` (SP Person/Group field, AD-resolved against corp-USA AD directory) + `owner_corp_email` (free text, for non-USA owners or AD-unresolved cases). Same split applies to `template_schema.TGGroupBase`: `tg_owner_email` → `tg_owner_corp_usa_email` + `tg_owner_corp_email`.

**Decision**: `template_schema.DeliveryItemBase.owner_email` is removed; replaced by `owner_corp_usa_email: str | None = None` + `owner_corp_email: str | None = None`. Same split on `TGGroupBase` (`tg_owner_email` removed; `tg_owner_corp_usa_email` + `tg_owner_corp_email` added). **HILDA preference rule** (per FR-2 + FR-9 2026-06-12): for outreach/identity, use `corp_usa_email` if set; else fall back to `corp_email`. **Owner-change event semantics**: a write to EITHER `owner_corp_usa_email` OR `owner_corp_email` constitutes an owner change event (fires `OwnerReassigned` rule per rule_engine MODULE.md; HILDA re-resolves canonical identity per preference rule + updates `owner_name` display via SP Person/Group AD lookup + writes CommunicationLog with `action_type=owner_reassigned`). A write to `owner_corp_id` alone is NOT an owner change event (corp_id is identifier metadata; not principal identity for outreach). Same semantics at TG-scope for tg_owner_* triple.

**Why**:
- Corp AD Person/Group field type auto-resolves only USA-corp emails (xxx@corp-usa.com); non-USA corp employees use different email format (xxx@corp.com) and don't resolve via the same SP AD picker
- Single owner_email field would force either AD-only constraint (excludes non-USA owners — operational dead-end) or free-text (loses AD validation for USA owners — typo risk, no auto-resolution of owner_name)
- Splitting provides both: validated AD-resolved identity for USA owners (via Person/Group SP field type) + flexibility for non-USA owners (free-text)
- Preference rule (corp_usa_email > corp_email) gives unambiguous runtime owner identity for HILDA outreach + PLM assignment
- Owner-change event semantics define when downstream rules (OwnerReassigned, FR-9 outreach re-fire, FR-71 ODF re-fire) trigger — change to either email = re-evaluate; change to corp_id alone = identifier metadata only
- Rejected alternatives: **(α) single owner_email Person/Group + free-text fallback marker** — confusing schema (which field is authoritative?); **(β) keep owner_email + add is_non_usa boolean flag** — duplicate fields with shared semantic; redundant; **(γ) separate SP list for non-USA owners** — partitioning overhead + complicates HILDA lookups + breaks model consistency.

**Consequences**:
- `template_schema/models.py` updates (Order (A) architecture-phase batch queued in STATUS Flag 2026-06-12):
  - `DeliveryItemBase`: remove `owner_email`; add `owner_corp_usa_email: str | None = None` + `owner_corp_email: str | None = None`
  - `TGGroupBase`: remove `tg_owner_email`; add `tg_owner_corp_usa_email: str | None = None` + `tg_owner_corp_email: str | None = None`
- `customizations/sharepoint_config/MODULE.md` example schema updates (delivery_items + denormalized TG columns)
- `customizations/sharepoint_config/customers/example.yaml` updates
- `HILDA_SP_Schema.xlsx` DeliveryItems + TGGroups tab updates (already partially reflected in SP UI engineer's revision shared via Google Sheets)
- `core/tests/test_template_schema.py` updates for new owner field set
- HILDA outreach code uses preference rule (corp_usa_email if set; else corp_email)
- `OwnerReassigned` rule fires on either email field change (not on corp_id alone)
- `owner_name` display auto-resolved by SP from corp_usa_email Person/Group field at SP-side; HILDA reads from DI row at runtime (denormalized read-only mirror)
- FR-2 (rewritten 2026-06-12) documents the owner identity model; FR-9 (rewritten 2026-06-12) documents the preference rule; FR-71 (rewritten 2026-06-12) uses the specific field names
- Anchors `[D-051]` impl note 2026-06-12 (TG denormalization pattern; tg_owner_* fields are denormalized read-only mirrors on DI rows)

**Anchors**: FR-2 (owner identity model 2026-06-12), FR-9 (outreach preference rule 2026-06-12), FR-71 (ODF specific field names 2026-06-12), `[D-051]` impl note 2026-06-12 (TG denormalization), `[D-065]` (SP UI engineer owns Person/Group field type + AD lookup mechanism for corp_usa_email).

---

## D-DRAFT-FORM-FACTOR-EXPAND: Form factor flag set expands from 5 to 7 canonical bools (drr + ir_ffw_p1 added)

**Date drafted**: 2026-06-12

**Context**: SP UI engineer 2026-06-10 review surfaced 2 additional form factor flag fields beyond the canonical 5 (`handset`, `tablet`, `wearable`, `mr`, `hmr_smr`): `drr` + `ir_ffw_p1` (Python-friendly rename from SP display name "ir/ffw/p1"). These are customer-specific device classifications increasingly common across customer deployments.

**Decision**: Add `drr: bool = False` + `ir_ffw_p1: bool = False` to `template_schema.DeliveryItemBase` as canonical fields. Rename `ir/ffw/p1` SP display → `ir_ffw_p1` Python identifier (underscores + lowercase required for Python attribute syntax + Pydantic compatibility). Total canonical form factor flags: **7** (was 5). Per FR-56 column model 2026-06-12, all 7 are `may_show` / not TPM-editable (YAML-loaded template-fixed flags).

**Why**:
- Customer-specific device classifications (`drr`, `ir_ffw_p1`) are now common enough across customer deployments to warrant canonical schema inclusion
- 7 flags is still small and manageable; no concrete burden to growing the set when new customer classifications emerge
- Customer-extensible registry alternative was considered but rejected — these are bool flags, not enum values; bool flags don't extend cleanly via a registry pattern (which maps strings → display labels)
- HILDA's Pydantic model needs awareness of these fields for type-safe runtime access by routing rules (rule_engine conditions reference form factor flags per FR-7)
- Rejected alternatives: **(α) keep flags in customer-extensible YAML config only** — HILDA's Pydantic DeliveryItemBase wouldn't know about them; loses type safety + IDE support + Pydantic validation; **(β) registry-based extensible flags** — over-engineered for bool flags; complicates rule_engine evaluator + storage layer for marginal gain; **(γ) consolidate flags into a single multi-value enum/list** — breaks back-compat with existing flag-by-name conventions in rule conditions.

**Consequences**:
- `template_schema/models.py` DeliveryItemBase gains 2 new bool fields with `False` default (Order (A) architecture-phase batch)
- Customer onboardings populate these from template YAML at tracker creation per FR-2
- Routing rules in YAML (`customizations/rules/<customer>/`) can reference `drr` / `ir_ffw_p1` in conditions per FR-7 + rule_engine MODULE.md
- `HILDA_SP_Schema.xlsx` DeliveryItems tab + `example.yaml` + `sharepoint_config/MODULE.md` example schema updated (mostly already documented in STATUS Flag 2026-06-12)
- `core/tests/test_template_schema.py` adds field-existence + default-value coverage for new fields
- Future form factor additions follow same canonical-addition pattern (not registry-extensible)
- FR-7 mentions form factor flags as a set; the 5→7 expansion is captured in FR-56 column model bucket (b) 2026-06-12 ("7 flags total")

**Anchors**: FR-7 (form factor scope; extensible-via-configuration mention), FR-56 column model 2026-06-12 (bucket b lists 7 flags), template_schema/MODULE.md (canonical schema location), `[D-046]` (canonical schema source — Pydantic models in template_schema).


---

## D-DRAFT-AA: `Milestone.target_date` is sole TPM-editable date; SP-side cascade with sp_alert_parser dedup discipline

**Date drafted**: 2026-06-14

**Context**: 2026-06-14 SP UI engineer xlsx review (row 68) surfaced that TPM-editing per-DeliveryItem `expected_completion_date` independently is operationally regressive — for a milestone with N items (often 40+), TPM would need N edits to shift all items to a new target date. User locked: `Milestone.target_date` is the SOLE TPM-editable date; all items in a milestone share the same target_date. This requires a cascade mechanism + a dedup discipline because SP-alerts fire per-row.

**Decision**: `Milestone.target_date` is the sole TPM-editable date for any milestone. **Cascade flow**: (1) TPM edits `Milestone.target_date` in SP UI → (2) SP UI engineer's web part atomically writes the new value to the Milestone row AND propagates to each child DeliveryItem's `expected_completion_date` field in a multi-row SP-side write → (3) the milestone-level write fires one SP-alert; each per-DI write also fires its own SP-alert (SP-alert engine is per-row); → (4) HILDA's `email_service.sp_alert_parser` processes ONLY the Milestone `target_date` change alert and cascades the new value to its in-memory state for all DIs in the milestone; **per-DI `expected_completion_date` change alerts triggered by the same edit burst are deduplicated and ignored**. Per-item `expected_completion_date` editing is NOT exposed in TPM SP UI; FR-14 amended 2026-06-14 to drop per-item date override.

**Why**:
- **(a) Operational simplicity for TPM**: one edit shifts all items in a milestone. Matches the natural model "milestone target slips → all items slip together."
- **(b) Eliminates per-item date divergence**: prevents TPM from accidentally creating items with mismatched target_dates that escape FR-11 escalation in confusing ways.
- **(c) SP-side cascade keeps SP single-source-of-truth**: SP web part owns the per-DI write fan-out; HILDA learns the new state via the milestone alert without participating in the fan-out.
- **(d) Dedup discipline avoids HILDA over-processing**: a 40-item milestone's target_date edit fires 1 milestone alert + 40 DI alerts; without dedup, HILDA would process 41 logical changes for 1 user action.

**Rejected alternatives**:
- **(α) Per-item date editing retained (current FR-14 wording)**: rejected per user 2026-06-14 — operationally regressive; no use case.
- **(β) HILDA owns the cascade (SP UI engineer writes milestone only; HILDA reads alert + fans out per-DI writes via `[D-064]`)**: rejected — SP UI engineer's web part is already at the point of edit (atomic SP-side write is cheaper than HILDA round-trip); HILDA cascade introduces a window where Milestone.target_date and DI.expected_completion_date are temporarily out of sync.
- **(γ) No DI mirror of target_date; HILDA computes deadline math from Milestone.target_date at runtime**: viable but loses backward-compat with existing FR-11/FR-26/FR-55/FR-23 `polling_schedule` evaluation which reads `expected_completion_date` per item (deadline-tiered intervals); rewrite scope too large for marginal benefit.

**Consequences**:
- FR-11 amended 2026-06-14 to document cascade + dedup discipline.
- FR-14 amended 2026-06-14 to drop per-item `expected_completion_date` override (replaced by milestone-target_date-only edit path).
- `sp_alert_parser` gains dedup logic: when a Milestone target_date change alert is in the same alert batch as per-DI `expected_completion_date` change alerts for items in that milestone, the per-DI alerts are ignored. Alert-batch grouping mechanism: SP-alerts arrive via IMAP IDLE / short-poll; alerts with the same `Modified` timestamp ±N seconds and shared milestone scope are grouped per architecture-phase detail.
- SP UI engineer's web part implements the atomic SP-side multi-row write on Milestone.target_date edit; failure to propagate to all DIs leaves the milestone in a partially-cascaded state requiring TPM re-edit (acceptable Ph-1 failure mode).
- Per-DI `expected_completion_date` field on DeliveryItems SP list becomes HILDA-managed (read by sp_alert_parser cache; written by SP UI engineer's cascade). TPM SP UI MUST NOT expose this field as editable.
- DEF-N (Ph-3+): if cross-item date divergence is ever needed (e.g., one item slips while others stay), a per-item date override mechanism can be re-introduced via FR-14 — gated on actual operational need.

**Anchors**: FR-2 (target_date set at tracker creation), FR-11 (deadline escalation per `expected_completion_date`), FR-14 (TPM overrides — date dropped from list), `[D-047]` (SP-alert channel — dedup is sp_alert_parser responsibility), `[D-064]` (SP-side cascade write is SP UI engineer's, not HILDA's), `[D-068]` impl note 2026-06-12 (SP-side button-write discipline — same pattern generalized to multi-row cascade).


---

## D-DRAFT-D073-IMPL-2026-06-14: D-073 impl note — Customers + Devices SP list gain 4 HILDA-readable columns per D-DRAFT-Z 2026-06-14 rewrite

**Date drafted**: 2026-06-14

**Target**: appended to canonical `[D-073]` as an implementation note at `/land-strand` time (parallel to existing impl notes on `[D-051]`, `[D-068]`, etc.). Not a new D-XXX.

**Context**: D-073 (canonical 2026-06-12) locked SP UI engineer's manual provisioning ceremony for SP lists. D-DRAFT-Z 2026-06-14 rewrite expanded HILDA's runtime SP read coupling from 2 lists to 4 (Customers + Devices added). SP UI engineer's ceremony per `[D-073]` must add the 4 new HILDA-readable columns so HILDA can resolve customer/device data from SP rows at SP-alert receive.

**Decision (impl note text, verbatim for promotion at land-strand)**:

> **Implementation note (2026-06-14 — 4 new HILDA-readable columns added to Customers + Devices SP lists per D-DRAFT-Z 2026-06-14 rewrite)**: SP UI engineer's manual provisioning ceremony per this Decision expands to include 4 new columns supporting HILDA's expanded 4-list runtime SP coupling per D-DRAFT-Z rewrite. **Customers SP list adds**: `customer_id` (HILDA-readable identifier resolved from `customer_id` SP row PK at alert receive; ops sets at customer-onboarding) + the existing `customer_jira_url` (FR-25 base URL). **Devices SP list adds**: `device_id` (HILDA-readable identifier resolved from `device_id`) + `assigned_pm_id` (PM identity per device for FR-19 / FR-25 / FR-51 PM-credentialed external calls; per FR-25 PM ≡ TPM in this deployment). **Edit discipline (load-bearing)**: all 4 fields are **ops-editable only** — TPM SP UI role MUST NOT allow edits. Misconfig on `customer_jira_url` would break FR-25 CustomerJIRA polling; misconfig on `customer_id` / `device_id` would break HILDA's NSD path construction (FR-13) and folder routing (FR-77). Field-level role restriction is the SP web part's responsibility per the SP UI engineer's existing role-based control pattern (confirmed 2026-06-14 — same pattern used for `pm_approval_at` / `last_reminder_triggered_at` SP-managed audit fields). SP-alert subscription on Customers + Devices SP lists is required (alert fires HILDA cache invalidation for the affected row id). Captured in `HILDA_SP_Schema.xlsx` Customers tab row 76 + Devices tab rows 79-80 (2026-06-14 SP UI engineer review). Anchors expanded: `[D-019]`, `[D-020]`, `[D-047]`, `[D-064]` (writeback still M+DI only), `[D-068]` (SP-side audit field write pattern — generalized to ops-only fields), D-DRAFT-Z 2026-06-14 rewrite.

**Why drafted as impl note (not new D-XXX)**: amends existing canonical decision with operational scope extension; the core D-073 framing (SP UI engineer provisions; HILDA does not REST-create) is unchanged.

**Anchors**: `[D-073]` (parent), D-DRAFT-Z 2026-06-14 rewrite (this strand), `[D-019]`, `[D-020]`, `[D-047]`, `[D-064]`, `[D-068]`.


---

## D-DRAFT-D006-IMPL-2026-06-14: D-006 impl note — NTLM confirmed as the actual on-prem AD auth protocol (Kerberos option removed)

**Date drafted**: 2026-06-14

**Target**: appended to canonical `[D-006]` as an implementation note at `/land-strand` time (parallel to existing impl-note pattern on `[D-051]`, `[D-068]`, `[D-073]`). Not a new D-XXX.

**Context**: D-006 (canonical 2026-04-30) chose "SharePoint REST API + on-prem AD auth (NTLM / Kerberos) against SharePoint 2017" — the "NTLM / Kerberos" framing was originally an "either-acceptable" placeholder, deferring the exact protocol choice to the deployment environment. 2026-06-14 corp environment confirmation: corp standard is **NTLM across both HILDA→SP REST and HILDA→NSD SMB**. SP UI engineer + ops infra inspection of HILDA Linux PC: NSD mounts use `cifs-utils` with `sec=ntlmssp` + `~/.smbcredentials` (username + password); no Kerberos keytab present; no krb5 ticket cache. SP REST writeback uses the same NTLM credentials via `requests_ntlm` (or equivalent) per `sharepoint_integration.SpCrud` Ph-1 implementation.

**Decision (impl note text, verbatim for promotion at land-strand)**:

> **Implementation note (2026-06-14 — NTLM confirmed; Kerberos option dropped)**: Corp environment standardizes on **NTLM** for both HILDA→SP REST and HILDA→NSD SMB authentication. The "NTLM / Kerberos" placeholder in this Decision's original text is replaced with NTLM-only. HILDA→SP REST uses `requests_ntlm.HttpNtlmAuth` (or equivalent NTLM credential injector) with `hilda-svc` AD service account credentials per `[D-019]` / `[D-038]` sops-encrypted env file discipline. HILDA→NSD SMB uses `cifs-utils` mount option `sec=ntlmssp` with `~/.smbcredentials` (username + password file mode 600, ops-provisioned). No Kerberos keytab + no krb5 ticket cache on HILDA PC — Kerberos infrastructure is not deployed in this environment. Browser→HILDA-proxy auth (per `[D-074]` Windows Integrated Auth) is separate and unaffected — that channel uses corp browser's native Negotiate (which may fall back to NTLM or use Kerberos depending on browser + GPO config; HILDA-side accepts both via the reverse proxy's auth handler). FR-2, FR-13, FR-73, FR-84, NFR-8, NFR-10 updated 2026-06-14 to reflect NTLM-only on HILDA→SP and HILDA→NSD paths. Anchors `[D-019]` (credential service discipline — NTLM credentials are HILDA-local secrets per `[D-038]` sops), `[D-038]` (sops-encrypted env file holds the NTLM username/password), `[D-074]` (browser→HILDA-proxy is separate auth scope; impl note doesn't cover it).

**Why drafted as impl note (not new D-XXX)**: amends existing canonical decision with environment-specific protocol confirmation; the core D-006 framing (SP REST + on-prem AD auth + SP 2017 against `[D-006]` Graph-vs-REST trade-off) is unchanged.

**Anchors**: `[D-006]` (parent), `[D-019]`, `[D-038]`, `[D-074]` (separate auth scope clarification).


---

## D-DRAFT-Z-V2-AMEND-2026-06-14b: D-DRAFT-Z v2 amendment — joint `(Model, ProjectID)` Device lookup key + slug→id rename + single template.yaml (folds tg_groups.yaml)

**Date drafted**: 2026-06-14 (late-session amendment to D-DRAFT-Z v2 — accumulates with v2 at land-strand promotion)

**Context**: D-DRAFT-Z v2 was drafted earlier 2026-06-14 with single-key Device lookup (`device_id` only) + 2-file YAML model (template.yaml + tg_groups.yaml) + `*_slug` naming. SP UI engineer's template.yaml proposal + xlsx review later same day surfaced refinements: (a) Devices SP list uses **joint key `(Model, ProjectID)`** because same Model can be tracked under multiple ProjectIDs; (b) SP-alert payload carries 5 fields, not 3 (`MMK`, `Model`, `ProjectID`, `MinorMilestone`, `ItemNumber`); (c) `*_slug` business codes rename to `*_id` to match corp + alert payload naming; (d) tg_groups.yaml folds into template.yaml with TG fields denormalized per-work-item — single YAML per customer.

**Decision (amendment text, verbatim for promotion-as-amendment at land-strand)**:

> **Amendment 2026-06-14 — joint Device key + 5-field routing + slug→id + single template.yaml**:
> 1. **Devices SP list PK = `ProjectID`** (corp-assigned external value, e.g., `2479`; not auto-Counter). `model` is a field column (= `device_id` in template.yaml, e.g., `SM-S901U`). Device lookup at SP-alert receive is direct PK lookup by ProjectID; `Model` cross-validated against the row's `model` column for integrity (log `EML-W008` on mismatch but don't fail). Supersedes joint-key wording — same Model may appear across multiple ProjectID rows but lookup is single-key by PK.
> 2. **SP-alert payload routing key** expands from 3-tuple `(ProjectID, MinorMilestone, ItemNumber)` to **5-field set**: `customer_id` (value from subject suffix in `Alert_Tasks_<customer_id>` — example value `MMK` in subject `Alert_Tasks_MMK - NVIOT - AGPS Test Results`; note that `MMK` is a customer_id VALUE, not a field name), plus `Model`, `ProjectID`, `MinorMilestone`, `ItemNumber` (all body fields).
> 3. **Naming convention**: `customer_slug` → `customer_id`, `device_slug` → `device_id`, `milestone_slug` → `milestone_id`, `item_path_slug` → `item_path_id`, `tg_path_slug` → `tg_path_id` throughout FRs + storage + SP-config. SP-list Counter PKs (auto-generated Integer) referred to as `customer_pk` / `device_pk` / `milestone_pk` / `item_pk` to avoid collision with business-identifier `_id` namespace.
> 4. **Single template.yaml per customer** at `customizations/template_schemas/<customer_id>/template.yaml` (was 2 files: template.yaml + tg_groups.yaml). Hierarchical structure: `<customer_id> → <device_id> → <milestone_id> → work_items[]`. TG metadata fields denormalized per-work-item with HILDA-validated equality discipline (same `tg_name` group within a milestone must have identical TG-field values across all items; `TSC-W005` warning on divergence). tg_groups.yaml is obsolete.
> 5. **Customer-onboarding seed flow**: template.yaml is the deployment-time SEED for all SP rows (Customers + Devices + Milestones + DeliveryItems); HILDA reads + writes initial rows via `[D-064]` on ops bootstrap command/SIGHUP. Existing SP rows never overwritten (SP is canonical for in-flight state); template-reload diff writes NEW rows only (for new device launch / new milestone).

**Why drafted as amendment to v2 (not new D-XXX)**: refines v2's specifics (joint key, naming, file count) without changing the v2 core decision (HILDA reads 4 SP lists incl. Customers + Devices; canonical store is SP). Promotes at land-strand as continuation of v2's canonical impl note.

**Cascade SP-list schema (supplements D-DRAFT-D073-IMPL)**: **Customers SP list** carries `customer_id` (= MMK alert tag) + `customer_jira_url`; **Devices SP list** carries `model` + `project_id` (joint key) + `device_id` (HILDA NSD path identifier; may equal `model` or be a derived value — exact rule architecture-phase) + `assigned_pm_id`; **Milestones SP list** carries `milestone_id` (= MinorMilestone alert tag); **DeliveryItems SP list** carries `item_no` (= ItemNumber alert tag) + `item_path_id` (NSD path identifier).

**Anchors**: D-DRAFT-Z v2 (parent), FR-2 customer-onboarding seed flow, FR-40 single-template rewrite, FR-84 5-field routing, D-DRAFT-D073-IMPL (column additions on Customers/Devices SP).


---

## D-DRAFT-Z-V2-AMEND-2026-06-14c: D-DRAFT-Z v2 final R&R lock — SP UI engineer setup_milestone is SOLE SP row-creation path; HILDA never creates SP rows; single-template hierarchy with milestones at customer level

**Date drafted**: 2026-06-14 (late-session amendment; supersedes earlier 2026-06-14b on Devices joint-key + template.yaml hierarchy)

**Context**: Earlier in 2026-06-14 session D-DRAFT-Z v2 + AMEND-2026-06-14b proposed HILDA seeds SP rows at first bootstrap and writes new tuples on template.yaml diffs (case B1). SP UI engineer end-of-day clarification rejected this — SP UI engineer setup_milestone task is the SOLE path for SP row creation across all 4 lists. HILDA NEVER creates SP rows. Additionally template.yaml hierarchy revised: milestones at customer level (NOT under device) since work-items per carrier do not change per device. Devices SP PK is project_id (corp-assigned, not auto-Counter), not a joint key.

**Decision (amendment text, verbatim for promotion-as-amendment at land-strand; supersedes 2026-06-14b)**:

> **Final R&R lock 2026-06-14 (supersedes earlier same-day drafts)**:
> 1. **SP UI engineer = SOLE SP row-creation authority**. setup_milestone TPM task per (customer, device) creates Customer + Device + Milestone + DeliveryItem rows using template.yaml as field/default reference. SP UI engineer assigns project_id (Devices PK) at row create.
> 2. **HILDA NEVER creates SP rows** — at any time, under any condition. HILDA = state transitions + audit-field writes on existing rows via [D-064]; never SpCrud.create_item.
> 3. **Template.yaml change semantics**: (a) change has no effect on active milestones; (b) SIGHUP HILDA after template.yaml change has no row-creation effect; (c) no on-demand work-item addition to active milestones — template.yaml edits apply only to FUTURE setup_milestone runs.
> 4. **Hierarchy**: customer to milestones (with work-items, shared across all devices) + devices (separate, per-device metadata only). NOT customer to device to milestone. Work-items per (customer, milestone) — single source — applied to every (device, milestone) combination at setup_milestone time.
> 5. **Devices PK = project_id** (not joint key, not auto-Counter). model is a field column carrying the device_id value (e.g., SM-S901U). Same model may appear across multiple ProjectID rows. Device lookup at SP-alert receive: direct PK GET by ProjectID; Model cross-validated against model column (log EML-W008 on mismatch).
> 6. **Template.yaml does NOT contain project_id** — SP UI engineer assigns at Devices SP row creation.
>
> **Why cleaner**: (a) sharp R&R boundary — SP UI engineer owns row creation; HILDA owns state transitions; zero collision risk; (b) HILDA is structurally insulated from template.yaml edits — no diff logic, no bootstrap/diff mode detection, no SIGHUP-driven write path; (c) milestones-at-customer-level eliminates the M*D*W work-item duplication problem when devices share milestones; (d) ProjectID-as-PK matches corp convention.

**Why drafted as additional amendment (not replacement of v2)**: v2 core decision (HILDA reads 4 SP lists; canonical store is SP) is unchanged. Refines + supersedes the row-creation/diff aspects of v2 and AMEND-2026-06-14b. At land-strand, v2 + 2026-06-14b + 2026-06-14c all promote as one canonical D-XXX with three impl notes documenting the iterative refinement.

**Anchors**: D-DRAFT-Z v2 (parent), D-DRAFT-Z-V2-AMEND-2026-06-14b (superseded R&R/hierarchy + joint-key aspects), FR-2 R&R lock 2026-06-14 late session, FR-5 SP-side uniqueness, FR-40 single-template structure (milestones at customer level), FR-84 Devices PK lookup, [D-073] (SP UI engineer manual provisioning + setup_milestone task ownership).
