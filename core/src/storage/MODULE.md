# Module: storage

> **Status:** Draft + 2026-06-09 cascade Group 2 of 3 applied (`[D-053]` impl note 2026-06-08 corrected model: 5-value DocType + alignment invariant + FR-86 4-path NSD storage matrix; `[D-060]` impl note 2026-06-08 inferred_tg_name in unrouted path; `NSDPath` helpers renamed/added; new `NSDPathType` enum + `local_nsd_path` rename per user-review correction 2026-06-09). Earlier rollbacks: 2026-06-07 (FR-77 / FR-78 / FR-79 revised / FR-82 / FR-83 / FR-84 / `[D-053]` original framing). Initial draft 2026-05-24. Sections curated; pending section-by-section user review before contract is finalized. Code implementation begins after `/switch-phase development`.
>
> **Rollback log:**
> - **2026-06-11 (storage Ph-1 dev — FR-87 TPM resolution APIs added)** — closes the FR-87 (Ph-1) storage gap surfaced at the 2026-06-11 retro ripple-check (email_service handlers expected `update_doc_type`/`set_revision_resolution` that didn't exist). Per architect ruling: **two additive thin-primitive methods** — `tpm_resolve_doc_type` (FR-87 step B) + `tpm_resolve_revision` (FR-87 step C) — caller (workflow_engine task body, post `email_service.sp_alert_parser`) owns ALL upstream resolution per the Option A / caller-resolves discipline (re-runs `[D-039]` via `llm`, passes resolved values); storage does only the field update + NSD file move (move-precedes-DB-write for crash-safety) + audit. Named `tpm_resolve_*` not `update_*` to signal one-way state transition (NULL slug → populated never reverts per the staged-fill Invariant). New value type `RevisionResolution` (tagged union; `.new()` / `.revision_of(slug)` factories). New codes `STR-E009` (state mismatch), `STR-E010` (asymmetric slug/rev), `STR-W008` (idempotent re-call). New action_types `tpm_resolve_doc_type` / `tpm_resolve_revision`. **Soft-flag — purely additive** (2 methods + 1 value type + 3 codes + 2 action_types; no existing signature changed, no Invariant relaxed, no Depends-on edge changed); documented inline in dev phase per the pragmatic-equivalence precedent; surface at close-session. Cross-module follow-up (NOT done here): email_service's FR-87 handler docstrings still describe the fat-method shape and must be reconciled to call these thin primitives + own the `[D-039]` re-run — flag for the email_service owner.
> - **2026-06-11 (storage Ph-1 dev — NSD IO model alignment with [D-013] hilda-svc invariant)** — NSD IO targets the pre-mounted filesystem at `HILDA_NSD_MOUNT_ROOT` (host-level Kerberos-authenticated cifs/smb3 mount as `hilda-svc`, bind-mounted into containers per [D-013]) rather than direct `smbprotocol` SMB calls. This was already specified by [D-013]'s invariant ("containers see a pre-mounted local filesystem and write to it normally" — line 700) but the Key choices line declared `smbprotocol` as the IO mechanism, carried over from an earlier framing where containers did SMB directly. Resolution: NSD IO is plain stdlib filesystem (`pathlib.Path` + `aiofiles` for async write/read; sync stdlib for stat/readdir wrapped in `asyncio.to_thread` per structure-conventions Sync-API wrapping rule). `smbprotocol` library stays in the dependency manifest as an available fallback for diagnostic CLI (verify share visibility independent of kernel mount) + dev troubleshooting (host-mount unavailable scenarios), but is NOT exercised in the routine NSD IO path. NSDPath helpers now compose paths under the configured mount root rather than emitting UNC-style `\\share\hilda\...` strings; UNC syntax in MODULE.md docstrings is illustrative of the corp NSD share, not the literal filesystem path the code sees. No D-XXX — clarification of [D-013]'s already-locked model; Key choices line was the bug, not the IO mechanism. Soft-flag: tooling-role clarification + Invariants text refinement; no Public surface change, no Depends-on edge change.
> - **2026-06-11 (storage Ph-1 dev — `clear_override` signature alignment)** — `clear_override` gained required `pm_id` kwarg. The function's docstring already required `credential_id=<calling pm_id>` on the emitted CommunicationLog audit row, but the signature `(scope, scope_id, rule_id, parameter_name)` exposed no pm_id source — caller had no way to satisfy the audit contract without violating it. Compare with `set_override(override: AutomationRuleOverride)` which sources pm_id from `override.set_by_pm_id` on the Pydantic model. `clear_override` has no override object, so pm_id must be an explicit param. Signature widened (additive new required kwarg); docstring unchanged in audit-row semantics. No D-XXX — bug-fix, signature alignment with documented audit contract. Soft-flag classification with one caveat: this is a Public-surface signature WIDENING (new required param), which would normally hard-flag for architecture round-trip — but the docstring already required pm_id, so this is "make signature match docstring," not "add new capability." Documented inline in dev phase per pragmatic equivalence rule (same precedent as the 2026-06-11 `doc_id_slug` NULL fix earlier today).
> - **2026-06-11 (storage Ph-1 dev — async Redis client naming correction)** — `aioredis` references corrected to `redis.asyncio` throughout. The standalone `aioredis` package was absorbed into `redis-py` 4.2 (Dec 2021); the standalone repo is archived. Same async rationale; identical API surface; storage's Public surface (`cache_set`, `cache_get`, `cache_delete`, `check_batch_idempotency`, `record_batch_idempotency`, `get_celery_broker_url`) is unchanged. Storage-only naming fix — does NOT propagate to requirements.md, template_schema/MODULE.md, or any other module's contract. Dependency manifest (`pyproject.toml` / `requirements.txt`) drops `aioredis>=2.0` and relies on `redis>=4.2` (already required for sync `redis-py` calls if any; otherwise adds `redis>=5.0` as the canonical pin). No flag classification — clarification of an outdated library name, not a constraint change.
> - **2026-06-11 (storage Ph-1 dev — internal contradiction resolved)** — `doc_id_slug` and `rev_number` on `DocumentIndexRow` reclassified from non-null to **nullable** to match FR-86 staged-fill timing already declared in the same file's `DocumentIndexRow` class docstring (lines 41-47: "doc_id_slug + rev_number NULL until TPM resolves per FR-87 step (C)"). Internal contradiction flagged during storage Ph-1 dev: docstring required NULL-while-staged, field declaration required non-null. Resolution: nullability matches FR-86 — the docstring was correct, the field declaration was the bug (carried over from pre-`[D-053]` 2026-05-24 initial draft when staged-fill timing wasn't yet specified). Secondary uniqueness changed from `UNIQUE (milestone_id, doc_id_slug, rev_number)` to a **partial unique index** `... WHERE doc_id_slug IS NOT NULL AND rev_number IS NOT NULL` (SQL NULL doesn't deduplicate; full UNIQUE would either reject legitimate staged-NULL siblings or break under NULL-equality semantics depending on backend). Added staged-fill lifecycle Invariant + NULL-handling contract on `get_document_index_row_by_slug` and `list_revisions`. Storage-only change — does NOT propagate to requirements.md (FR-86 already specified the staged-fill timing) or template_schema/MODULE.md (no template_schema field is affected). Soft-flag classification: relaxes a constraint without breaking callers — non-null callers still receive non-null values post-resolution.
> - **2026-06-11 (implementation session 1 — architect review correction)** — **no DeliveryItem mirror in storage; caller-resolves discipline.** Implementation had introduced a minimal `DeliveryItemMirrorTable` + `upsert_delivery_item_mirror` to back `get_default_work_item_for_milestone` and `set_folder_routing_for_tg`'s item_no validation. Architect rejected: storage holds no DeliveryItem schema, no bidirectional tracker↔storage dep, no single-writer discipline burden. Resolution: (a) mirror table + upsert REMOVED; (b) `get_default_work_item_for_milestone` REMOVED from storage — a pure entity lookup; callers (workflow_engine task bodies) resolve via `sharepoint_integration` get_items; (c) `reassign_document_to_workitem` gains explicit keyword params `target_tg_name` / `target_owner_email` / `target_plm_id` (caller resolves from SP before invoking); (d) `set_folder_routing_for_tg` gains required `valid_item_nos: set[int]` (caller-supplied; STR-E006 validation stays in storage). STR-W003 (default work-item missing) remains registered as the CALLER's signal. Draft decision in strand `storage-v1` decisions-draft.
> - **2026-06-09 (post-cascade user-review correction, same day)** — applied 4 corrections from user review of Group 2 cascade: (a) **status header refreshed** to reflect 2026-06-09 cascade Group 2 of 3 + 2026-06-09 user-review correction. (b) **DocumentItemAssociation docstring** gained explicit "same file may occupy 2+ NSD paths simultaneously" rationale per `[D-055]` (rejected-alternative symbolic-link model documented; TPM expectation noted). (c) **`local_classified_path` renamed → `local_nsd_path`** across the file (10 references — 7 active + 3 historical-context preserved in rollback log) — the prior name implied "classified" specifically but the field holds whichever of the 4 FR-86 path types (classified / staged_classification / staged_revision / unrouted) the file is currently at. (d) **New `nsd_path_type: NSDPathType` column** added to DocumentItemAssociation per user observation that path TYPE was only derivable via string-parsing the path string (brittle + non-indexable); new `NSDPathType` enum (4 values mirroring FR-86 path types) added next to `RoutingResolution` enum (peer storage-internal enum); STR-W007 stale-staged-document query updated to use the new indexed column instead of LIKE-pattern on path strings. (e) Storage-only change — does NOT propagate to requirements.md or template_schema/MODULE.md (NSDPathType is a storage-implementation concern; FR-86's 4 path types are the requirements-side spec; template_schema owns cross-tier data contracts not storage-internal enums).
> - **2026-06-09 (Phase B Module cascade — Group 2 of 3 against the corrected `[D-053]` model — after `template_schema/MODULE.md` cascade 2026-06-08)** — applied the requirements-phase redesign locked 2026-06-08 (FR-7 + FR-85 + FR-86 + FR-87 + `[D-053]` impl note 2026-06-08 + `[D-060]` impl note 2026-06-08): **DocumentIndexRow.doc_type** docstring updated to 5-value enum (test_report / tech_report / waiver / compliance_certification_release_notes / unresolved) — UNRESOLVED is explicit enum sentinel value (NOT Optional[DocType] = None); FR-85 classification + FR-86 alignment + downstream trigger rules documented per the corrected model. **parser_result + llm_review_findings** docstrings updated for 5-value DocType (null on compliance_certification_release_notes / unresolved per FR-86). **DocumentIndexRow class docstring** gained FR-86 storage matrix paragraph documenting the 4 NSD paths the row's file lives in + state transitions via FR-84 SP-alert channel. **`NSDPath.internal_default_workitem()`** signature added `inferred_tg_name_slug` parameter per `[D-060]` impl note 2026-06-08 — path now `internal/<carrier>/<device>/<milestone>/<inferred_tg_name>/_unrouted/<filename>` (was: no TG segment). **`NSDPath.internal_staged` renamed → `NSDPath.internal_staged_revision`** with explicit name + `_staged_revision/` path segment + full 7-arg signature; added explicit ambiguity-class distinction docstring. **New `NSDPath.internal_staged_classification`** helper — path `internal/<carrier>/<device>/<milestone>/<tg>/<item>/_staged_classification/<filename>` (no `<doc_type>` segment); for FR-86 misaligned-pair documents; awaits FR-87 step (B) TPM resolution. **New error code `STR-W007`** — stale staged document warning (age > threshold per `customizations/<customer>/storage_alerts.yaml`; default 7 days) surfaced on `--diagnostic`. Cascade Group 3 of 3 (`llm/MODULE.md`) is next per STATUS.md In-progress 2026-06-08.
> - **2026-06-07** — Phase B Module rollback (Group 2 of N, after `template_schema/MODULE.md`): added `DocumentItemAssociation` M:M for FR-79 multi-item + PLM fan-out across distinct (owner × milestone) pairs; new `DocumentIndexRow` fields (`milestone_id`, `inferred_tg_name` per FR-78/FR-83, `routing_resolution`); new `RoutingResolution` enum (6 values mirroring FR-52 5-step pipeline + FR-83 reassignment); new `TGFolderRoutingRow` (FR-77 Type-2 with `ingress_folder` naming per inbound/outbound discipline); new `TagCatalogRow` (FR-82 revised); new NSDPath helpers (`internal_default_workitem` for FR-78 `_unrouted` sentinel; `ingress_folder` for FR-77 NSD1/NSD2 inbound paths); association/fan-out API methods + FR-83 `reassign_document_to_workitem`; expanded `CommunicationLogRow.action_type` example registry per FR-29 revised; new error codes (`STR-E005` / `STR-E006` / `STR-W002` / `STR-W003`); invariants added for ingress/target naming discipline, single-milestone association scope, per-association PLM attachment, channel→TG resolution, registry-based action/trigger validation, soft-deactivate tag catalog; Deferred items added for cross-milestone associations, tag-catalog audit history, default-work-item path namespace evolution.
> - **2026-06-07 (API surface review, same day)** — 10-item review pass on API surface lines 171-462: removed stale `NSDPath.to_download_token()` (replaced by storage-layer `make_download_token(file_hash, delivery_item_id)`); expanded docstrings on `internal_staged` vs `internal_default_workitem` clarifying distinct ambiguity classes (Tier-2 NEW-vs-REVISION vs FR-78 which-work-item); renamed `internal_un_resolved_zip` → `internal_unrouted_zip` for naming consistency with `_unrouted` sentinel; expanded `query_communications` with file_hash / action_type / pm_id / until / offset filters + pagination cap (max=1000); added `MAX_CACHE_TTL_SECONDS=86_400` constant + `cache_set` TTL guard (raises STR-E008); added 9 convenience helpers (`cache_delete`, `get_documents_for_item`, `list_documents_for_milestone`, `delete_document_item_association`, `list_tag_catalog_entries`, `reactivate_tag`, `list_active_overrides` bulk-load); replaced `fan_out_plm_associations` tuple return with typed `PLMFanOutTarget` model (adds `item_count` diagnostic field for FR-79 case-(a)-vs-(b) signal); expanded `add_document_item_association` docstring covering 3 code paths (first-association / multi-item fan-out / FR-83 target); expanded `reassign_document_to_workitem` to sync `DocumentIndexRow.inferred_tg_name` to target item's tg_name for audit clarity (with old→new note in CommunicationLog); refreshed STR-MET diagnostic line with new table counts (`association_rows`, `folder_routing_rows`, `tag_catalog_rows`, orphan diagnostics); added override audit logging (set_override/clear_override write CommunicationLog with credential_id=pm_id); new error codes `STR-E008` (cache TTL guard), `STR-W005` (orphan DocumentIndexRow), `STR-W006` (PLM fan-out target without plm_id).
> - **2026-06-07 (post-refactor sweep, same day)** — fixed stale invariant + API signatures missed in the file-centric refactor: replaced "natural key uniqueness `(delivery_item_id, doc_type, doc_id_slug, rev_number)`" with new identity model (PK = `file_hash`; secondary unique constraint `(milestone_id, doc_id_slug, rev_number)`); split `get_document_index_row` → `get_document_index_row_by_hash` (primary) + `get_document_index_row_by_slug` (FR-57 secondary lookup); rekeyed `update_review_findings` + `set_is_final` to `file_hash`; rekeyed `list_revisions` to `(milestone_id, doc_id_slug)` family; added per-method docstrings clarifying JOIN-through-M:M pattern for per-item queries. Cross-channel idempotency invariant added (same file via Email + PLM produces ONE row; first arrival wins on per-ingest fields).
> - **2026-06-07 (clarification, same day)** — clarified `AutomationRuleOverride.rule_id` as a SOFT FK to YAML-defined rules in `customizations/rules/<customer>.yaml` per FR-30/FR-31 (base rules YAML + overrides Postgres split — no DB-level FK because YAML rules don't live in Postgres); added `list_all_override_rule_ids()` helper for `rule_engine`'s startup orphan-audit; new error code `STR-W004` (orphan override).
> - **2026-06-07 (refactor, same day)** — refactored DocumentIndexRow/DocumentItemAssociation per user review: dropped muddled primary/secondary model (no `is_primary` on M:M, no `is_primary_association` on DocumentIndexRow); moved `local_classified_path`, `plm_id`, `plm_attachment_id`, `owner_email`, `upload_timestamp` from DocumentIndexRow → DocumentItemAssociation (genuinely per-(file, item)); moved `inferred_tg_name`, `routing_resolution` to DocumentIndexRow (per-ingest, not per-association); dropped `landing_item_id` (redundant — M:M row's delivery_item_id IS the landing); dropped synthetic `association_id` (composite PK `(file_hash, delivery_item_id)`); DocumentIndexRow PK changed from `(delivery_item_id, doc_type, doc_id_slug, rev_number)` to `file_hash` (file-centric model — FR-57 natural key preserved via secondary unique constraint `(milestone_id, doc_id_slug, rev_number)` + JOIN); dropped persisted `download_url` (replaced with `make_download_token(file_hash, delivery_item_id, ttl_seconds)` computed at page-render time per FR-61); new error code `STR-E007` (invalid/expired download token); two new invariants (symmetric M:M no-primary, never-persist-download-url). Net: clean single source of truth per field; no dual-write hazards; per-(file, item) physical-storage semantics preserved (same file can occupy 2+ NSD paths, one per item).

## Purpose

Owns HILDA's internal persistence — Postgres (SQLAlchemy 2.x async + Alembic) for the document index, `CommunicationLog`, BATCH-id idempotency cache, FR-31 runtime overrides, and Celery result backend; Redis client (Celery broker Ph-1/Ph-2 per `[D-022]`; cache-only Ph-3+ per `[D-043]`); NSD SMB client for the two-tree document store per `[D-013]` / `[D-041]`. **Authoritative for FR-31** (PM/TPM runtime override persistence — the override table IS this module). **Contributes to** FR-13 (NSD path construction + writes; authoritative module is `email_service` for the ingest flow logic), FR-15 (timestamp persistence; `tracker` is authoritative for the state transitions that update them), FR-52 / FR-55 (document index queries supporting `[D-039]` classification + NSD polling; `email_service` is authoritative for the classification and polling logic), FR-57 (document enumeration data layer; `dashboard` is authoritative for the HTTP API surface), FR-67 / FR-68 (document index drives PLM cleanup + sync diff; `issue_tracker` and `customer_adapter` are authoritative for the PLM/carrier action paths). NFR-15 is a deployment-level NFR owned by SYSTEM.md + `deploy/`, not by any single module. Canonical Pydantic models per `[D-046]` live here as the source-of-truth schema from which SP List provisioning script, Alembic migrations, and YAML template-schema spec are generated.

## Public surface

### Pydantic models (canonical schema source per `[D-046]`)

```python
class DocumentIndexRow:
    """One row per physical document (per FR-79 revised 2026-06-07 — file-centric model).
    Holds file-content + per-ingest properties only; per-(file, item) association data
    lives on DocumentItemAssociation. Same file_hash arriving via multiple channels is
    idempotent — first arrival wins on per-ingest fields.

    Natural key: file_hash (PK). Secondary unique constraint: (milestone_id, doc_id_slug, rev_number)
    preserves the FR-57 lookup contract — given (item, doc_id_slug, rev_number), JOIN to
    DocumentItemAssociation to resolve the file. Single-milestone invariant per FR-79 (Ph-1/Ph-2)
    keeps milestone_id stable per file_hash.

    **NSD path per FR-86 storage matrix** (`[D-053]` impl note 2026-06-08): the row's file
    physically lives in exactly one of 4 NSD paths at any time, derivable from the row's state:
    - **classified** (`NSDPath.internal_classified`) — (item_type, doc_type) aligned per
      FR-86 invariant AND `[D-039]` revision determination passed. doc_id_slug + rev_number set.
    - **unrouted** (`NSDPath.internal_default_workitem`) — work-item lands on `item_type =
      Default` (FR-52 step 5 fallback); `[D-039]` SKIPPED at ingest per FR-86 (deferred to
      FR-83 reassignment); inferred_tg_name surfaced in path per `[D-060]` impl note 2026-06-08.
    - **staged-not-revision-determined** (`NSDPath.internal_staged_revision`) — alignment
      passed BUT `[D-039]` ambiguous (LLM Step 3 low confidence). doc_id_slug + rev_number
      NULL until TPM resolves per FR-87 step (C).
    - **staged-not-classified** (`NSDPath.internal_staged_classification`) — (item_type,
      doc_type) MISALIGNED per FR-86 invariant OR doc_type = unresolved on non-Default item.
      Awaits TPM resolution per FR-87 step (B).

    State transitions: TPM saves via SP UI → FR-84 SP-alert email → HILDA re-runs FR-86
    matrix → file moves between paths + DocumentIndexRow updates (doc_id_slug, rev_number,
    doc_type, inferred_tg_name as applicable) + DocumentItemAssociation.local_nsd_path +
    DocumentItemAssociation.nsd_path_type follow the new path/state. STR-W007 surfaces stale
    staged docs on `--diagnostic` via nsd_path_type indexed lookup."""
    file_hash: str                 # PK — SHA-256 per [D-039] Step 0; cross-association identity key per FR-79
    milestone_id: str              # FK → MilestoneBase; denormalized — FR-79 single-milestone invariant guarantees stability across associations
    doc_type: DocType              # 5-value enum per `[D-053]` impl note 2026-06-08: test_report | tech_report | waiver | compliance_certification_release_notes | unresolved. Classified per FR-85 ladder (filename regex Step 1 + LLM CLASSIFY_DOC_TYPE Step 2 restricted to {test/tech/waiver}); UNRESOLVED is the sentinel value (NOT Python None — always set in the row) for low-confidence classifications + Default-routed-undetermined state. Alignment with item_type enforced per FR-86 storage matrix — misaligned pairs land on staged-not-classified NSD path for TPM resolution per FR-87. **Downstream triggering**: FR-16 fires on `test_report` regardless of landing item; FR-53 fires on `{test_report, tech_report, waiver}` when review_required=true (TEST_TECH_WAIVER_REPORT items only); neither fires on `{compliance_certification_release_notes, unresolved}`. Supersedes the prior 4-value framing per `[D-053]` impl note 2026-05-28b (withdrawn).
    doc_id_slug: str | None        # slugified first-received filename; stable across revisions per FR-57. **NULL while file is at `staged_not_classification` or `staged_not_revision` or `unrouted` NSD path** per FR-86 staged-fill — populated when classification + `[D-039]` revision determination complete; never reverted to NULL once set.
    rev_number: int | None         # 1 for new, ≥2 for revisions per FR-17. NULL with same semantics as doc_id_slug — both fields share the staged-fill lifecycle (always both NULL or both populated per FR-86 invariant).
    ingest_source: IngestSource    # enum: Email | CorporatePLM | NetworkSharedDrive | SharePointUI — channel of FIRST ingest (idempotent on retry)
    original_filename: str         # as-received; preserved without transformation per FR-57
    first_page_excerpt: str        # for [D-039] Tier-2 LLM new-vs-revision classification
    is_final: bool                 # FR-66 version-selection result; auto-true in Ph-1 (single revision)
    parser_result: dict | None     # FR-16 test-report parser output; null for tech_report / waiver / compliance_certification_release_notes / unresolved — per-file property (cascade 2026-06-09)
    llm_review_findings: dict | None  # FR-53 LLM quality review; null when review_required=false OR doc_type ∈ {compliance_certification_release_notes, unresolved} — per-file property (cascade 2026-06-09)
    from_zip: bool                 # FR-72 ZIP ingest source flag
    source_zip_filename: str | None  # original ZIP filename when from_zip=true
    # Per-ingest fields (computed once when the file is first ingested):
    inferred_tg_name: str | None   # per FR-78/FR-83 — TG resolved from inbound channel (NSD ingress_nsd / email_group_alias lookup / PLM-id reverse-lookup); null only for SP-UI direct uploads where TG is explicit on the targeted DeliveryItem. Consumed by FR-83 TPM reassignment to shortlist candidate work-items within the TG.
    routing_resolution: RoutingResolution  # per FR-52 — which 5-step pipeline step resolved this routing (per-ingest, not per-association — the pipeline runs once per ingest event regardless of how many items it resolves to)
    ingested_at: datetime          # timestamp of FIRST ingest
    # Fields explicitly dropped 2026-06-07: delivery_item_id, plm_id, plm_attachment_id,
    # local_classified_path, upload_timestamp (moved to DocumentItemAssociation per-association);
    # download_url (computed on demand via make_download_token); landing_item_id (redundant with
    # M:M delivery_item_id); is_primary_association (no primary/secondary semantic in symmetric M:M).

class NSDPathType(str, Enum):
    """Per FR-86 storage matrix — explicit state tracker for which of the 4 NSD path types
    the file currently lives at on a given DocumentItemAssociation row. Added 2026-06-09
    per user-review correction — avoids string-parsing `local_nsd_path` to determine state;
    indexed in Postgres for STR-W007 stale-staged-document queries + FR-86 state transitions
    triggered by FR-87 SP UI resolution. Per-association state (independent across rows for
    the same file_hash)."""
    CLASSIFIED                = "classified"                  # FR-86 classified path; (item_type, doc_type) aligned + [D-039] revision-pass; file at NSDPath.internal_classified(...)
    STAGED_NOT_CLASSIFIED     = "staged_not_classified"       # FR-86 staged-not-classified path; (item_type, doc_type) MISALIGNED OR doc_type=unresolved on non-Default item; file at NSDPath.internal_staged_classification(...); awaits FR-87 step (B)
    STAGED_NOT_REVISION       = "staged_not_revision"         # FR-86 staged-not-revision-determined path; aligned BUT [D-039] failed (LLM Step 3 low confidence); file at NSDPath.internal_staged_revision(...); awaits FR-87 step (C)
    UNROUTED                  = "unrouted"                    # FR-86 unrouted path; work-item landed on Default; file at NSDPath.internal_default_workitem(...); [D-039] SKIPPED at ingest per FR-86; awaits FR-83 reassignment + FR-87 A/B/C resolution

class RoutingResolution(str, Enum):
    """Per FR-52 5-step routing pipeline — records which step resolved the document's routing.
    Used for monitoring + dashboard surface (FR-83 unrouted-document tracking)."""
    SUBSTRING_MATCH      = "SubstringMatch"        # FR-52 step 1 — exact substring match on filename/subject vs item_name
    FUZZY_MATCH          = "FuzzyMatch"            # FR-52 step 2 — fuzzy match (rapidfuzz) above threshold
    FOLDER_ROUTING       = "FolderRouting"         # FR-77 Type-2 — ingress_folder → item_no via TGFolderRoutingRow
    LLM_ROUTE_ATTACHMENT = "LLMRouteAttachment"    # FR-52 step 4 — LLM TaskKind.ROUTE_ATTACHMENT
    STAGED_DEFAULT       = "StagedDefault"         # FR-52 step 5 / FR-78 — fell through to milestone's default work-item
    TPM_REASSIGNED       = "TPMReassigned"         # FR-83 — TPM-manual reassignment from default work-item to specific item

class DocumentItemAssociation:
    """Per FR-79 (revised 2026-06-07) — symmetric M:M between document file_hash and DeliveryItem
    within one milestone. Holds per-(file, item) properties: where the file lives on disk for
    this item, where it was uploaded in PLM for this item's owner, when. No primary/secondary
    distinction — all associations are equal.

    Composite PK: (file_hash, delivery_item_id). No synthetic association_id; CommunicationLog
    audit references the file_hash + delivery_item_id pair directly.

    Invariants:
    - All associations for a given file_hash share the same milestone_id (FR-79 same-milestone
      invariant Ph-1/Ph-2; cross-milestone deferred to Ph-3+). Enforced via STR-E005.
    - File physically exists at each association's local_nsd_path (renamed from local_classified_path
      2026-06-09 — the field stores whichever of the 4 FR-86 path types the file is at, not just
      classified). **The same file (one file_hash) may occupy 2+ NSD paths simultaneously** —
      one physical copy per associated item. This was a deliberate design decision per `[D-055]`:
      the alternative (one canonical NSD home with symbolic links from other item paths) was
      rejected because SMB symlink semantics are inconsistent across Linux/Windows kernels, file
      deletion + reassignment flows get complex, and TPM expectation is that each item folder
      contains the actual file (not a symlink). Per-item paths are independent — each association
      row tracks its own `local_nsd_path` + `nsd_path_type`, and the rows may be in different
      `nsd_path_type` states simultaneously (e.g., file at row A is `classified` while file at
      row B is `staged_not_classified` after item B was reassigned to a misaligned item_type).
    - PLM fan-out: documents upload to PLM for EVERY distinct (owner_email, plm_id) pair across
      the associations. issue_tracker iterates `fan_out_plm_associations(file_hash, milestone_id)`
      which returns DISTINCT (owner_email, plm_id) sets; each upload writes back its
      plm_attachment_id + upload_timestamp on the contributing association rows.
    - Two cases per FR-79:
        (a) one owner, N items in same TG, same file → N rows, all share (owner_email, plm_id,
            plm_attachment_id, upload_timestamp) — PLM upload happens once
        (b) two owners in same TG, N items, same file → N rows, distinct (owner_email, plm_id)
            pairs → 2 PLM uploads → distinct plm_attachment_id + upload_timestamp per row
    """
    file_hash:             str        # PK part 1 — FK → DocumentIndexRow.file_hash
    delivery_item_id:      str        # PK part 2 — FK → DeliveryItemBase
    milestone_id:          str        # denormalized from delivery_item_id → MilestoneBase; FR-79 same-milestone invariant
    local_nsd_path:        Path       # NSD path FOR THIS ITEM's copy of the file — whichever of the 4 FR-86 path types the file is currently at (classified | staged_revision | staged_classification | unrouted). Renamed from `local_classified_path` 2026-06-09 — the prior name implied "classified" specifically but the field also holds staged + unrouted paths. Per `[D-055]` + `[D-040]` + FR-13: the file physically exists at this exact path; for multi-item associations the same file_hash may be at 2+ NSD paths simultaneously (one per item, one copy each).
    nsd_path_type:         NSDPathType  # explicit state tracker per FR-86 storage matrix (added 2026-06-09 per user review) — derives the path TYPE without string-parsing local_nsd_path; indexed for STR-W007 stale-staged-document queries + FR-86 state transitions. Each association row's state is independent (a file may be in different nsd_path_type states across different association rows simultaneously). Enum: see NSDPathType definition.
    owner_email:           str        # denormalized from DeliveryItemBase.owner_email — fast PLM fan-out grouping
    plm_id:                str | None # PLM issue ID for THIS (owner × milestone) per FR-26 / FR-57 — denormalized from DeliveryItemBase.plm_id for query convenience
    plm_attachment_id:     str | None # per-association PLM attachment ID per FR-79 fan-out; same value across rows that share (owner_email, plm_id) within milestone (case (a)); distinct per (owner_email, plm_id) pair (case (b))
    upload_timestamp:      datetime | None  # when uploaded to THIS owner's PLM; null until issue_tracker confirms upload. Mirrors plm_attachment_id grouping per case (a)/(b)
    associated_at:         datetime  # when this association row was created
    associated_by:         str       # "auto" — FR-52 pipeline; "<pm_id>" — TPM-manual / FR-83 reassignment

class CommunicationLogRow:
    """Append-only audit trail per NFR-6. No UPDATE/DELETE on this table."""
    log_id: str                    # PK
    delivery_item_id: str | None
    device_id: str | None
    channel: Channel               # enum: Email | Messenger | CorporatePLM | NetworkSharedDrive | CustomerJIRA | SharePoint
    direction: Direction           # enum: Inbound | Outbound
    sender: str | None
    recipients: str | None
    subject: str | None
    summary: str | None            # LLM-generated for inbound where applicable; never raw proprietary content
    external_message_id: str | None
    credential_id: str | None      # opaque reference to credential_service blob — never plaintext
    action_type: str | None        # free-string registry per FR-29 (revised 2026-06-05). Examples: submission | resubmission | bulk_close | get_credential | propagate_tags (FR-82) | notify_hilda_ops (FR-75) | milestone_storage_cleanup (FR-76) | halt_milestone_polling (FR-74) | final_sweep (FR-74) | instantiate_default_work_item (FR-78) | reassign_to_workitem (FR-83) | tpm_resolve_doc_type (FR-87 B) | tpm_resolve_revision (FR-87 C) | send_owner_routing_query (FR-83 Ph-2) | fan_out_plm_upload (FR-79 revised)
    attachments: list[dict]        # [{filename, download_url}, ...] — never file content
    timestamp: datetime

class BatchIdempotencyKey:
    """Per [D-012]. Stored in Redis (short-TTL), not Postgres."""
    batch_id: str
    item_index: int
    status: str
    ttl_seconds: int = 86400       # 24h max per [D-012] short-TTL invariant

class TGFolderRoutingRow:
    """Per FR-77 Type-2 routing — persisted form of TGFolderRouting/FolderRoutingEntry from
    template_schema. Loaded into routing pipeline cache at tracker creation; refreshed on
    TGGroupBase update. Replace-all write semantics — a TG's full table is overwritten on update."""
    milestone_id:    str       # PK part 1 — FK → MilestoneBase
    tg_name:         str       # PK part 2 — FK → TGGroupBase (within milestone)
    ingress_folder:  str       # PK part 3 — INBOUND folder path under TG's ingress_nsd (NOT customer-facing target_folder)
    item_no:         int       # → DeliveryItemBase.item_no within the milestone
    routing_notes:   str | None

class TagCatalogRow:
    """Per FR-82 (revised 2026-06-05) — customer-extensible tag catalog. Validated against
    DeliveryItemBase.item_description (comma-separated tag list) on ingest. Unknown tags raise
    TSC-W003 at template_schema validator + STR-W002 at storage layer (warnings, not errors —
    the catalog is customer-extensible)."""
    customer_id:  str          # PK part 1
    tag:          str          # PK part 2 — canonical tag string
    description:  str | None
    color:        str | None   # optional UI hint (hex code) for dashboard chips
    active:       bool = True  # soft-deactivate without delete; preserves historical references

class AutomationRuleOverride:
    """FR-31 runtime override (precedence over YAML rule files per FR-30).

    Relationship to base rules: `rule_id` is a SOFT FK to a rule defined in
    `customizations/rules/<customer_slug>.yaml` (loaded + indexed by `rule_engine` at startup
    or on YAML hot-reload). The base YAML rule defines (trigger_event, trigger_sub_event,
    trigger_condition, action_type, base action_parameters); this override modifies a single
    `parameter_name` for a scoped subset (Device | Customer | Global). No DB-level FK because
    YAML rules don't live in Postgres — base rules are version-controlled config,
    overrides are runtime PM/TPM mutations. Orphan overrides (no matching rule_id in
    YAML at load time) are logged as `STR-W004` by `rule_engine`'s startup audit and ignored
    at evaluation time. Effective-value precedence per FR-30:
        Device override > Customer override > Global override > YAML base."""
    scope: Scope                   # enum: Device | Customer | Global
    scope_id: str | None           # device_id | customer_id | NULL (Global)
    rule_id: str                   # SOFT FK to YAML-defined rule_id in customizations/rules/<customer>.yaml
    parameter_name: str
    parameter_value: str           # serialized; type-validated by rule_engine at read time against AutomationRuleBase.action_parameters schema
    set_by_pm_id: str
    set_at: datetime
    expires_at: datetime | None

# Helper enums and value types (DocType, IngestSource, Channel, Direction, Scope) live alongside
# the Pydantic models in this module's models.py. Canonical source per [D-046]; generators emit
# SP List provisioning script + Alembic migrations + YAML schema spec from these definitions.
```

### Postgres ORM API (async SQLAlchemy 2.x)

```python
# Session management
async def get_session() -> AsyncIterator[AsyncSession]: ...
async def init_db() -> None: ...                                  # creates schema if missing — dev/test only

# DocumentIndex operations (FR-13, FR-17, FR-52, FR-55, FR-57)
# Identity model (post-2026-06-07 refactor): PK = file_hash; secondary unique constraint
# (milestone_id, doc_id_slug, rev_number) preserves FR-57 lookup. Per-item queries JOIN
# through DocumentItemAssociation.

async def add_document_index_row(row: DocumentIndexRow) -> None
    """Idempotent on `file_hash` — re-ingest of the same physical file returns existing row
    unchanged; per-ingest fields (ingest_source, inferred_tg_name, routing_resolution,
    ingested_at) are first-write-wins."""

async def get_document_index_row_by_hash(file_hash: str) -> DocumentIndexRow | None
    """Primary lookup — by file content identity."""

async def get_document_index_row_by_slug(
    milestone_id: str, doc_id_slug: str, rev_number: int
) -> DocumentIndexRow | None
    """FR-57 lookup — by the partial unique index. Used by `[D-039]` slug-match
    flow that doesn't yet have the file_hash in hand (e.g., revision detection from
    inbound filename match). Both `doc_id_slug` and `rev_number` are required non-null
    params — staged-fill rows (doc_id_slug/rev_number NULL on DocumentIndexRow) are
    NOT discoverable through this path by design; callers chasing such rows must
    use `get_document_index_row_by_hash(file_hash)` or query via
    DocumentItemAssociation.nsd_path_type ∈ {STAGED_*, UNROUTED}."""

async def find_doc_id_slugs_for_item(
    delivery_item_id: str, doc_type: DocType
) -> list[str]
    """Supports [D-039] Step 1 (slug match) and Step 2 (NEW_DOCUMENT short-circuit when empty).
    Implementation: JOINs DocumentItemAssociation → DocumentIndexRow filtered by doc_type."""

async def list_revisions(
    milestone_id: str, doc_id_slug: str
) -> list[DocumentIndexRow]
    """Supports FR-60 expandable history view (Ph-2). All revisions of a (milestone, doc_id_slug)
    family — note: doc_id_slug is stable across revisions per FR-57; rev_number distinguishes them.
    Returns only RESOLVED rows — staged-fill rows with NULL doc_id_slug are filtered out
    (they are not yet members of any revision family per the staged-fill lifecycle Invariant)."""

async def update_review_findings(
    file_hash: str,
    parser_result: dict | None, llm_review_findings: dict | None
) -> None
    """Per-file properties (parser_result, llm_review_findings) live on DocumentIndexRow;
    keyed by file_hash directly."""

async def set_is_final(file_hash: str, is_final: bool) -> None
    """FR-66 version-selection support. Setting is_final=true on a file_hash auto-sets
    false on all OTHER revisions in the same (milestone_id, doc_id_slug) family. Implementation
    derives the family from the target row's (milestone_id, doc_id_slug) and updates siblings
    in the same transaction."""

async def get_documents_for_item(delivery_item_id: str) -> list[DocumentIndexRow]
    """Convenience helper — returns all DocumentIndexRow rows associated with the work-item.
    Implementation: JOINs DocumentItemAssociation (filtered by delivery_item_id) →
    DocumentIndexRow. Used by dashboard per-item document listing (Flow 1 per FR-61) and
    by submission assembly per-item enumeration (FR-41 path that scopes by item rather than
    milestone)."""

async def list_documents_for_milestone(
    milestone_id: str, doc_type: DocType | None = None, is_final_only: bool = False
) -> list[DocumentIndexRow]
    """Returns all DocumentIndexRow rows in a milestone, optionally filtered. Used by
    submission-assembly worker (FR-73 carrier-package) and by FR-76 MilestoneStorageCleanup
    (no filters → returns everything for the milestone)."""

# DocumentItemAssociation operations (FR-79 revised 2026-06-07 — symmetric M:M + PLM fan-out)
async def add_document_item_association(assoc: DocumentItemAssociation) -> None
    """Idempotent on composite PK (file_hash, delivery_item_id). Enforces same-milestone
    invariant — raises STR-E005 if file_hash already associates with a different milestone.

    Used by THREE code paths:
      (a) First-association (FR-52 pipeline) — file just classified, item just resolved;
          associated_by='auto'. Caller also creates the DocumentIndexRow first.
      (b) Multi-item fan-out (FR-79 explicit) — TPM or rule-driven action links the same
          file to an additional work-item in the same milestone; associated_by='<pm_id>'
          or 'auto' depending on origin. DocumentIndexRow already exists.
      (c) FR-83 reassignment target — called by reassign_document_to_workitem; the prior
          association is removed via delete_document_item_association in the same transaction.

    Caller is responsible for the NSD file copy/move at local_nsd_path + sets nsd_path_type
    per FR-86 storage matrix; this function only writes the index row."""

async def delete_document_item_association(
    file_hash: str, delivery_item_id: str, *, delete_file: bool = True
) -> None
    """Removes the M:M row for (file_hash, delivery_item_id). When delete_file=True (default),
    also deletes the per-item NSD copy at the association's local_nsd_path — preserves
    the same file's other associations on the other items.

    Used by:
      - FR-67 PLM cleanup (item closed → associations removed → NSD copies removed)
      - FR-83 reassignment (source association removed after target is created)
      - Item-deletion paths (TPM deletes a work-item → cascade associations)
      - FR-76 MilestoneStorageCleanup (delete_file=True on all associations in the milestone)

    If this was the LAST association for the file_hash within the milestone, the underlying
    DocumentIndexRow is NOT auto-deleted — orphan rows are cleaned by the periodic orphan-sweep
    task (Ph-2) or surfaced as STR-W005 (orphan DocumentIndexRow with no associations) on
    --diagnostic. Rationale: preserve audit trail in case of accidental delete + reassignment."""

async def list_associations_for_file(file_hash: str) -> list[DocumentItemAssociation]
    """Returns all (file, item) associations for a file. milestone_id is invariant across them
    per FR-79 same-milestone rule."""

async def list_associations_for_item(delivery_item_id: str) -> list[DocumentItemAssociation]
    """Used by FR-67 PLM cleanup — when an item is closed, find all documents associated."""

async def fan_out_plm_associations(file_hash: str) -> list[PLMFanOutTarget]
    """Per FR-79 revised — returns DISTINCT (owner_email, plm_id) pairs across all associations
    of file_hash, wrapped as PLMFanOutTarget. issue_tracker iterates this list and performs one
    PLM upload per target (regardless of how many items share that target). Each successful
    upload then calls `update_association_plm_attachment(...)` which fans the result back across
    all M:M rows that share the (owner_email, plm_id) pair within the milestone."""

# Returned by fan_out_plm_associations — typed for clarity + future extension:
class PLMFanOutTarget(BaseModel):
    """One PLM upload target for a file_hash per FR-79 revised. Each distinct (owner_email,
    plm_id) pair across associations produces exactly one target."""
    owner_email: str
    plm_id:      str | None       # None = owner has no PLM issue yet for this milestone (Ph-2 edge case; logged STR-W006)
    item_count:  int              # how many DeliveryItems share this (owner, plm) pair within the milestone — diagnostic signal; useful for surfacing one-owner-N-items vs distinct-owner cases (FR-79 case (a) vs (b))

async def update_association_plm_attachment(
    file_hash: str, delivery_item_id: str,
    plm_attachment_id: str, upload_timestamp: datetime
) -> None
    """Called after issue_tracker successfully uploads to an owner's PLM. Updates the target
    association row + any other association rows sharing the same (owner_email, plm_id) within
    the milestone (case (a) one-owner-N-items fan-out result is replicated across all rows for
    that owner in the same transaction)."""

async def reassign_document_to_workitem(
    file_hash: str, source_delivery_item_id: str,
    target_delivery_item_id: str, pm_id: str,
    *, target_tg_name: str | None, target_owner_email: str,
    target_plm_id: str | None = None,
) -> None
    """FR-83 — TPM-manual reassignment from default work-item (or any item) to a specific
    work-item. Transactional — all steps succeed or all roll back:
    1. Adds new DocumentItemAssociation row for (file_hash, target_delivery_item_id) using
       target item's owner_email + plm_id (NSD path constructed via NSDPath.internal_classified
       for the target item).
    2. Removes the source association row via delete_document_item_association(...,
       delete_file=True) — file is moved on NSD from source path to target path (Step 1's
       NSD write happens before Step 2's delete to avoid data loss on failure).
    3. Updates DocumentIndexRow:
         - routing_resolution = TPMReassigned
         - inferred_tg_name   = <target item's tg_name>  (per FR-83 — original channel-resolved
           TG may have been wrong if TPM is reassigning across TGs; this preserves audit
           clarity that the document is now considered to belong to the target's TG)
    4. Logs CommunicationLog action_type='reassign_to_workitem' with credential_id=pm_id and
       attachments=[{file_hash, source→target items}].
    Note: file_hash + delivery_item_id is the natural identity per the composite PK; no synthetic
    document_index_id / association_id is required.

    Audit trail: Step 3's inferred_tg_name overwrite is intentional and recorded in the
    CommunicationLog summary field as 'inferred_tg_name: <old> → <new>' so the original
    channel-resolved TG remains discoverable via comm-log query."""

# FR-87 TPM staged-document resolution (steps B + C) — added 2026-06-11 (architect ruling).
# Thin persistence primitives: the CALLER (workflow_engine task body, after
# email_service.sp_alert_parser decodes the SP-alert email) owns ALL upstream resolution per
# the Option A / caller-resolves discipline — re-runs [D-039] via the llm module and passes
# resolved values; storage does only the field update + NSD file move + audit. Does NOT run
# [D-039], does NOT fire FR-77 (caller orchestrates per Non-goals). NSD move precedes DB write
# (crash-safety: a failed DB write leaves the file at target while the row still reads STAGED_*,
# which the next reconcile/retry detects — readers never see a CLASSIFIED row with a missing file).

@dataclass(frozen=True)
class RevisionResolution:
    """FR-87 step (C) revision discriminator — TPM's NEW vs REVISION_OF choice (tagged union)."""
    kind: Literal["new", "revision_of"]
    revised_doc_id_slug: str | None = None        # required when kind="revision_of"
    @classmethod
    def new(cls) -> "RevisionResolution": ...
    @classmethod
    def revision_of(cls, doc_id_slug: str) -> "RevisionResolution": ...

async def tpm_resolve_doc_type(
    file_hash: str, delivery_item_id: str, new_doc_type: DocType,
    *, doc_id_slug: str | None = None, rev_number: int | None = None, pm_id: str,
) -> None
    """FR-87 step (B) — TPM picks doc_type for a file at `staged_not_classification`. Caller
    re-runs [D-039] and passes resolved values:
      - [D-039] PASSED   → doc_id_slug + rev_number (both non-NULL) → file moves to `classified`;
        nsd_path_type → CLASSIFIED.
      - [D-039] AMBIGUOUS→ omit both (both NULL) → file moves to `staged_not_revision`;
        nsd_path_type → STAGED_NOT_REVISION; awaits step (C).
    Both doc_id_slug + rev_number together or neither (asymmetric None → STR-E010). State
    mismatch (file not at staged_not_classification, and not already at target) → STR-E009.
    Idempotent on (file_hash, delivery_item_id, target_state): re-call at target → STR-W008 no-op.
    Emits CommunicationLog action_type='tpm_resolve_doc_type' with credential_id=pm_id."""

async def tpm_resolve_revision(
    file_hash: str, delivery_item_id: str, resolution: RevisionResolution, *, pm_id: str,
) -> None
    """FR-87 step (C) — TPM picks revision resolution for a file at `staged_not_revision`.
    RevisionResolution.new() → storage assigns doc_id_slug = make_slug(original_filename) +
    rev_number = 1; RevisionResolution.revision_of(slug) → doc_id_slug = slug, rev_number =
    MAX(family) + 1. File moves to `classified`; nsd_path_type → CLASSIFIED. State mismatch →
    STR-E009; idempotent re-call (already CLASSIFIED) → STR-W008. Emits CommunicationLog
    action_type='tpm_resolve_revision' with credential_id=pm_id; summary records the resolution."""

# Default work-item lookup (FR-78) — REMOVED from storage 2026-06-11 (architect review):
# a pure entity lookup; the FR-52 caller resolves the milestone's default work-item via
# sharepoint_integration get_items (item_type = Default) and fires
# INSTANTIATE_DEFAULT_WORK_ITEM (STR-W003 signal) when absent. Storage holds no
# DeliveryItem mirror — see rollback log 2026-06-11.

# Download token operations (FR-61 — HILDA-mediated download per NFR-16)
async def make_download_token(
    file_hash: str, delivery_item_id: str, ttl_seconds: int = 300
) -> str
    """Per FR-61 — generate a short-lived HILDA-mediated download token bound to a specific
    (file, item) association. Token encodes (file_hash, delivery_item_id) + signature + expiry.
    Token resolves server-side to DocumentItemAssociation.local_nsd_path FOR THAT ITEM
    (each item's view of a shared file resolves to that item's own NSD copy).
    Used by dashboard / SP UI integration at page-render time; never persisted. Distinct from
    worker-internal SMB reads (submission assembly per FR-41 / FR-73) which use
    storage.read_file(NSDPath) directly without a URL layer."""

async def resolve_download_token(token: str) -> tuple[str, str, NSDPath]:
    """Verify token signature + TTL; return (file_hash, delivery_item_id, NSDPath) for the
    hilda-api download endpoint to stream from. Raises STR-E007 on invalid or expired token."""

# TG folder routing operations (FR-77 Type-2)
async def get_folder_routing_for_tg(milestone_id: str, tg_name: str) -> list[TGFolderRoutingRow]
    """Used by FR-52 step 3 routing pipeline. Cached in-process; refreshed on TGGroupBase update."""

async def set_folder_routing_for_tg(
    milestone_id: str, tg_name: str, entries: list[TGFolderRoutingRow],
    *, valid_item_nos: set[int],
) -> None
    """Replace-all semantics — TG's full routing table is overwritten atomically.
    Validates every entry's item_no against caller-supplied `valid_item_nos`; raises
    STR-E006 if any item_no is unknown. The CALLER resolves the milestone's item_no
    set from SharePoint (no DeliveryItem mirror in storage — 2026-06-11)."""

# Tag catalog operations (FR-82 revised)
async def get_tag_catalog(customer_id: str) -> set[str]
    """Fast validation surface — returns set of ACTIVE tag strings only. Cached in-process
    + Redis with TTL ≤ 24h per [D-012]; cache evicted via cache_delete on upsert/deactivate."""

async def list_tag_catalog_entries(
    customer_id: str, include_inactive: bool = False
) -> list[TagCatalogRow]
    """Full-row variant for dashboard display (description, color, active flag). Use
    `get_tag_catalog` for validation hot-path."""

async def upsert_tag(row: TagCatalogRow) -> None
    """Insert or update. Evicts the cached tag set for the customer (cache_delete)."""

async def deactivate_tag(customer_id: str, tag: str) -> None
    """Soft-deactivate (sets active=False); does not delete row (preserves historical
    references in item_description). Evicts cache."""

async def reactivate_tag(customer_id: str, tag: str) -> None
    """Undo for deactivate_tag — sets active=True. No-op if the row doesn't exist (caller
    should upsert in that case). Evicts cache."""

# CommunicationLog operations (append-only per NFR-6)
async def log_communication(row: CommunicationLogRow) -> None
    """Append-only; never updates or deletes existing rows."""

async def query_communications(
    *, delivery_item_id: str | None = None, device_id: str | None = None,
    channel: Channel | None = None, file_hash: str | None = None,
    action_type: str | None = None, pm_id: str | None = None,
    since: datetime | None = None, until: datetime | None = None,
    limit: int = 100, offset: int = 0
) -> list[CommunicationLogRow]
    """Audit query surface per NFR-6 + FR-29 action accountability.
    Filters (all optional, AND-composed):
      - delivery_item_id — comms tied to a specific work-item
      - device_id — comms tied to a device (any item)
      - channel — Email | Messenger | CorporatePLM | NetworkSharedDrive | CustomerJIRA | SharePoint
      - file_hash — all comms about a specific file (matched against attachments[].file_hash
        for outbound, or sender-supplied hash for inbound when known)
      - action_type — e.g., 'fan_out_plm_upload', 'reassign_to_workitem', 'propagate_tags' per
        FR-29 revised action registry
      - pm_id — comms where credential_id matched this PM (per-PM accountability for FR-31
        override edits, FR-83 reassignments, etc.)
      - since / until — time window
    Pagination: limit (max 1000; default 100) + offset for chronological scroll.
    Order: timestamp DESC."""

# FR-31 runtime override operations
async def get_active_overrides(
    scope: Scope, scope_id: str | None, rule_id: str
) -> list[AutomationRuleOverride]
    """Returns overrides active at `now` for a specific (scope, scope_id, rule_id) triple.
    Caller (rule_engine) applies Device → Customer → Global precedence per FR-30
    (Postgres override > YAML rules). Hot-path per-rule lookup; use `list_active_overrides`
    for bulk startup load."""

async def list_active_overrides(
    *, scope: Scope | None = None, scope_id: str | None = None
) -> list[AutomationRuleOverride]
    """Bulk load — returns ALL overrides active at `now`, optionally filtered by scope or
    scope_id. Used by rule_engine startup cache population (single query) and by dashboard
    "all overrides" admin view. Logs CommunicationLog action_type='list_overrides_bulk'."""

async def set_override(override: AutomationRuleOverride) -> None
    """Insert or replace. Logs CommunicationLog action_type='set_override' with
    credential_id=set_by_pm_id for FR-31 audit accountability. Evicts rule_engine cache via
    cache_delete on the affected scope key."""

async def clear_override(
    scope: Scope, scope_id: str | None, rule_id: str, parameter_name: str, *, pm_id: str
) -> None
    """Removes the override identified by (scope, scope_id, rule_id, parameter_name).
    `pm_id` is REQUIRED keyword-only — the emitted CommunicationLog audit row
    sets `credential_id=pm_id` per FR-31 audit accountability (mirrors `set_override`
    which sources pm_id from `override.set_by_pm_id` on its AutomationRuleOverride
    arg; clear_override has no override object so pm_id is an explicit param).
    Evicts rule_engine cache via cache_delete. Idempotent: clearing a non-existent
    override still emits the audit row (records the intent) but does no DB delete."""

async def list_all_override_rule_ids() -> set[str]
    """Per rule_engine startup audit — returns DISTINCT rule_ids referenced by any active
    AutomationRuleOverride row. rule_engine cross-references against YAML-loaded rule_ids;
    rule_ids present here but absent from YAML are logged as STR-W004 (orphan override) and
    ignored at evaluation. Storage does NOT auto-purge orphans — PMs may temporarily disable
    a rule in YAML during ops without losing their override values."""
```

### Redis client

```python
# Broker role (Ph-1/Ph-2 only — read by workflow_engine for Celery init)
def get_celery_broker_url() -> str
    """Reads from HILDA_REDIS_URL env. Ph-3+: workflow_engine calls get_rabbitmq_broker_url
    instead per [D-043]."""

# Cache role (both phases)
MAX_CACHE_TTL_SECONDS: int = 86_400   # 24h — enforces invariant "Redis cache TTL ≤ 24h" per [D-012]

async def cache_set(key: str, value: bytes, ttl_seconds: int) -> None
    """Raises STR-E008 if `ttl_seconds > MAX_CACHE_TTL_SECONDS` (24h) — enforces the
    short-TTL invariant at the API boundary so callers can't silently violate. No durable
    state in Redis per `[D-012]`."""

async def cache_get(key: str) -> bytes | None

async def cache_delete(key: str) -> None
    """Invalidation helper. Used by `set_folder_routing_for_tg` to evict the cached
    routing table for the affected (milestone_id, tg_name); by `upsert_tag` /
    `deactivate_tag` to evict the cached customer tag catalog; by `set_override` /
    `clear_override` to evict any rule-engine cached effective-parameter snapshot. Idempotent —
    deleting a missing key is a no-op."""

# BATCH-id idempotency per [D-012]
async def check_batch_idempotency(batch_id: str, item_index: int) -> str | None
    """Returns existing status if (batch_id, item_index) was already recorded; None otherwise."""

async def record_batch_idempotency(key: BatchIdempotencyKey) -> None
    """Idempotent — re-recording the same (batch_id, item_index, status) is a no-op."""
```

### NSD client per `[D-013]` / `[D-041]`

```python
class NSDPath:
    """Encapsulates the two-tree NSD path structure per FR-13."""

    @classmethod
    def inbound_drop(cls, carrier_slug: str, device_slug: str, milestone_slug: str, item_slug: str) -> NSDPath:
        """Owner inbound tree: \\share\hilda\inbound\<carrier>\<device>\<milestone>\<item>\"""

    @classmethod
    def internal_classified(
        cls, carrier_slug, device_slug, milestone_slug, tg_name_slug, item_slug,
        doc_type_slug, doc_id_slug, rev_number
    ) -> NSDPath:
        """HILDA internal classified path: ...\internal\<carrier>\<device>\<milestone>\<tg>\<item>\<doc_type>\<doc_id>\revN\"""

    @classmethod
    def internal_staged_revision(cls, carrier_slug, device_slug, milestone_slug, tg_name_slug, item_slug, doc_type_slug, original_filename) -> NSDPath:
        """Renamed 2026-06-09 from `internal_staged` for explicit naming aligned with FR-86
        path nomenclature (`staged-not-revision-determined`). Path:
        ...\internal\<carrier>\<device>\<milestone>\<tg>\<item>\<doc_type>\_staged_revision\<original_filename>

        Per FR-86 (staged-not-revision-determined path) — work-item resolved AND (item_type,
        doc_type) aligned per the alignment invariant BUT `[D-039]` revision determination
        failed (LLM Step 3 low confidence on NEW-vs-REVISION classification). Awaits TPM
        resolution per FR-87 step (C) — TPM clicks "NEW" or "Revision of <doc_id_slug>" →
        HILDA assigns doc_id_slug + rev_number → file moves to canonical `internal_classified`
        path. doc_id_slug + rev_number are NULL on DocumentIndexRow while file is at this path.

        Distinct ambiguity class from `internal_default_workitem` (FR-78 _unrouted) AND from
        `internal_staged_classification` (FR-86 misalignment): the ambiguity here is "what
        version of an existing document family?", NOT "which work-item?" (that's _unrouted)
        and NOT "which doc_type?" (that's _staged_classification). Routing succeeded; doc_type
        is known and aligned; only the revision-family membership is unresolved."""

    @classmethod
    def internal_staged_classification(cls, carrier_slug, device_slug, milestone_slug, tg_name_slug, item_slug, original_filename) -> NSDPath:
        """New 2026-06-09 per FR-86 (staged-not-classified path) + `[D-053]` impl note 2026-06-08.
        Path:
        ...\internal\<carrier>\<device>\<milestone>\<tg>\<item>\_staged_classification\<original_filename>

        Note: NO `<doc_type>` segment in path — doc_type is the unresolved dimension. Triggered
        when the (item_type, doc_type) pair is MISALIGNED per the FR-86 alignment invariant
        (e.g., TEST_TECH_WAIVER_REPORT item with doc_type = compliance_certification_release_notes,
        OR any non-Default item with doc_type = unresolved). Awaits TPM resolution per FR-87
        step (B) — TPM picks doc_type from {test_report, tech_report, waiver,
        compliance_certification_release_notes}; on save HILDA runs `[D-039]` revision
        determination (deferred at ingest per FR-86 skip rule) and re-runs the FR-86 storage
        matrix → file moves to canonical `internal_classified` path (if [D-039] passes) or
        to `internal_staged_revision` path (if [D-039] ambiguous).

        Distinct ambiguity class from `internal_staged_revision` (revision-family unknown) and
        `internal_default_workitem` (work-item unknown). Here: work-item is known AND
        classification ran, but the result doesn't align with the work-item's item_type
        constraints — TPM disambiguates which way the misalignment resolves."""

    @classmethod
    def internal_zip_store(cls, ..., item_slug, original_zip_filename) -> NSDPath:
        """FR-72 per-item NSD-sourced ZIP storage."""

    @classmethod
    def internal_unrouted_zip(cls, ..., tg_name_slug, original_zip_filename) -> NSDPath:
        """FR-72 TG-scoped Email/PLM-sourced ZIP storage — when a ZIP arrives via Email/PLM
        scoped only to a TG (not a specific item) per FR-72. Renamed from `internal_un_resolved_zip`
        2026-06-07 for naming consistency with the `_unrouted` sentinel used in FR-78 default
        work-item paths."""

    @classmethod
    def internal_outbound(cls, ..., item_slug) -> NSDPath:
        """HILDA-generated artifacts (QC reports, diagnostics, submission outputs); never owner deliverables.
        Note: FR-73 carrier-package zips are regenerated per-click and deleted on TPM download —
        callers should treat this path as transient for those artifacts."""

    @classmethod
    def internal_default_workitem(
        cls, carrier_slug, device_slug, milestone_slug, inferred_tg_name_slug, original_filename
    ) -> NSDPath:
        """Per FR-78 + FR-86 + `[D-060]` impl note 2026-06-08 — landing path for documents
        routed to the milestone's default work-item (item_type = ItemType.DEFAULT). Path:
        ...\internal\<carrier>\<device>\<milestone>\<inferred_tg_name>\_unrouted\<original_filename>

        **Signature change 2026-06-09**: added `inferred_tg_name_slug` parameter per `[D-060]`
        impl note 2026-06-08 (was 4 args; now 5). The inferred_tg_name surfaces in the path
        for TPM browsing by TG (organizes unrouted docs grouped by their channel-resolved TG
        instead of one flat folder per milestone). The slug is derived from
        DocumentIndexRow.inferred_tg_name via make_slug(); when inferred_tg_name is NULL
        (rare — SP-UI-direct-upload edge case), caller may pass `"_unknown_tg"` sentinel.

        Distinct ambiguity class from `internal_staged_revision` (`[D-039]` Tier-2 NEW-vs-
        REVISION ambiguity): the ambiguity here is "WHICH work-item does this belong to?" —
        the FR-52 5-step routing pipeline could not match a specific work-item. Document-
        identity (NEW-vs-REVISION) is irrelevant on the default work-item since `[D-039]`
        revision determination is SKIPPED at ingest per FR-86 (run at FR-83 reassignment
        instead — saves LLM cost on unrouted docs).

        The `_unrouted` sentinel mirrors the default work-item's sentinel tg_name on the
        SP-list-side entity per DefaultWorkItemConfig (per-milestone, NOT per-TG). The actual
        TG of the document IS surfaced on BOTH (a) the path's `<inferred_tg_name>` segment
        AND (b) DocumentIndexRow.inferred_tg_name (the row is the authoritative source; the
        path mirrors it for filesystem-level grouping). FR-83 TPM-manual reassignment moves
        the file from this path to the target item's `internal_classified` path (after
        FR-87 step (B) doc_type resolution + step (C) revision resolution if needed)."""

    @classmethod
    def ingress_folder(
        cls, carrier_slug, ingress_nsd: Literal["NSD1", "NSD2"], folder_path: str
    ) -> NSDPath:
        """Per FR-77 Type-2 routing — INBOUND folder under the TG's ingress_nsd.
        NSD1: \\share\hilda\inbound\nsd1\<carrier>\<folder_path>
        NSD2: \\share\hilda\inbound\nsd2\<carrier>\<folder_path>
        Distinct from outbound customer-portal upload destinations (FR-73 / FR-19 — those are
        not NSD paths at all; they live on the customer portal). The folder_path corresponds
        to TGFolderRoutingRow.ingress_folder."""

    def to_relative(self) -> str           # share-relative POSIX path — the PERSISTED form of
                                           # DocumentItemAssociation.local_nsd_path (mount-root-independent)
    @classmethod
    def from_relative(cls, relative: str) -> NSDPath   # inverse; STR-E004 on absolute/empty
    def to_local(self) -> Path             # absolute path under GlobalStorageConfig.nsd_mount_root —
                                           # what file IO actually targets per [D-013] host mount
    def to_unc(self) -> str                # \\share\hilda\... — DIAGNOSTIC / display only (illustrative
                                           # corp-share rendering); NOT the path the IO code sees
    @classmethod
    def from_unc(cls, unc: str) -> NSDPath              # inverse of to_unc; diagnostic use
    # Persisted-path convention (2026-06-11 [D-013] alignment): local_nsd_path stores the
    # to_relative() form; callers compose absolute paths via to_local() at IO time. to_unc/from_unc
    # are retained for diagnostics + human-facing docs only.
    # NOTE: download-token generation lives at the storage-API layer (make_download_token /
    # resolve_download_token), NOT on NSDPath — tokens are bound to (file_hash, delivery_item_id)
    # association, which an NSDPath alone cannot disambiguate post-FR-79 (same file may exist
    # at multiple NSD paths, one per item).

async def read_file(path: NSDPath) -> AsyncIterator[bytes]
    """Streams file from NSD; used by hilda-api download endpoint (FR-61)."""

async def write_file(path: NSDPath, content: AsyncIterable[bytes]) -> None
    """Writes via hilda-svc identity per [D-013]. Idempotent on (path, content) — re-write of
    identical bytes is a no-op."""

async def compute_file_hash(path: NSDPath) -> str
    """SHA-256 per [D-039] Step 0 (exact-duplicate detection)."""

async def list_inbound_drops(
    carrier_slug, device_slug, milestone_slug, item_slug
) -> list[NSDPath]
    """FR-55 polling support — returns files dropped by owners since last poll."""

async def extract_first_page(path: NSDPath) -> str
    """First-page text extraction for [D-039] Tier-2 LLM comparison. Ph-1 scope:
    txt/csv/md/log + XLSX (openpyxl). PDF / DOCX / DOC raise STR-E004 with a pointer
    to the open [D-011] extraction-library decision (pypdf vs pdfplumber vs pymupdf;
    python-docx vs docx2txt — footprint/license/quality tradeoffs; STATUS.md Next) —
    deliberately NOT silently chosen here. Graceful degradation until [D-011] lands:
    FR-52 step 4 cannot run for those formats → falls through to step 5 default
    work-item → TPM resolves via FR-87. The first strand needing extract_first_page(pdf)
    forces [D-011] resolution."""
```

### Operational config (`config.py`)

```python
class GlobalStorageConfig(BaseModel):
    """Operational values only — runtime/domain data lives in customizations/.
    3-tier precedence per structure-conventions Config format:
    CLI overrides > env vars > config/storage.json > defaults."""
    nsd_mount_root: Path = Path("/nsd")        # [D-013] host cifs/smb3 mount; env HILDA_NSD_MOUNT_ROOT
    db_url:         str  = "postgresql+asyncpg://hilda@localhost:5432/hilda"  # env HILDA_STORAGE_DB_URL
    redis_url:      str  = "redis://localhost:6379/0"                          # env HILDA_REDIS_URL

    @classmethod
    def from_sources(cls, config_path=None, cli_overrides=None) -> "GlobalStorageConfig": ...

def get_storage_config() -> GlobalStorageConfig   # process-wide, lazily resolved
def set_storage_config(config: GlobalStorageConfig | None) -> None  # inject / reset (tests, CLI)
```

### Alembic migration interface

- Migrations live under `core/src/storage/migrations/`
- Ph-1/Ph-2 deploy: `docker compose run --rm hilda-api alembic upgrade head` per `[D-026]` impl note 2026-05-24
- Ph-3+ MicroK8s: init container on `hilda-api` per SYSTEM.md §4
- Migrations are idempotent and backward-compatible with the running prior version

### Independent test interface per `[D-005]`

```bash
# Live diagnostic (against real Postgres + Redis + NSD mount; corp AD context)
python -m core.src.storage.storage_cli --diagnostic
# Emits STR-RPT:
#   STR OK 3s postgres_ping=Y alembic_head=<rev_id> redis_ping=Y nsd_mount=Y
#   STR MET doc_index_rows=12450 association_rows=14802 comm_log_rows=348201 overrides_active=7 \
#          folder_routing_rows=23 tag_catalog_rows=47 orphan_doc_index_rows=0 orphan_overrides=0

# Mock mode — no external IO
python -m core.src.storage.storage_cli --mock
# Uses SQLite in-memory + fakeredis + in-memory filesystem; deterministic timestamps.

# Mock-postgres mode (CI / local dev with real Postgres semantics but no real corp data)
python -m core.src.storage.storage_cli --mock-postgres
# Spins up test Postgres container; runs Alembic head; emits STR-RPT against it.

# Schema validation
python -m core.src.storage.storage_cli --validate --customer <slug>
# Validates that current Pydantic models can round-trip a synthetic DeliveryItem
# matching the customer's template_schemas/<slug>/template.yaml. STR-QC report.

# Alembic round-trip test (CI gate per [D-046])
python -m core.src.storage.storage_cli --alembic-roundtrip
# upgrade head → downgrade base → upgrade head — verifies migrations are reversible.
```

## Invariants

- **Document index identity**: `DocumentIndexRow.file_hash` is the primary key (SHA-256 per `[D-039]`). A **partial unique index** `(milestone_id, doc_id_slug, rev_number) WHERE doc_id_slug IS NOT NULL AND rev_number IS NOT NULL` preserves the FR-57 lookup contract — given a populated (milestone, doc_id_slug, rev_number) triple, exactly one file is identifiable. Cannot be a full UNIQUE constraint because both columns are nullable per FR-86 staged-fill (SQL NULL does not deduplicate), and any number of staged rows may legitimately co-exist with NULLs before TPM resolution. Per-item resolution uses a JOIN through `DocumentItemAssociation`. The pre-refactor key `(delivery_item_id, doc_type, doc_id_slug, rev_number)` no longer applies since `delivery_item_id` is per-association, not per-file.
- **`doc_id_slug` + `rev_number` staged-fill lifecycle** per FR-86: both fields are NULL between document ingest and resolution of (classification + `[D-039]` revision determination); both are populated together — atomic transition NULL → non-NULL on the row when the file moves from a staged/unrouted NSD path to `classified`. Never reverted to NULL once set. Callers that read these fields must handle NULL as the "pre-resolution" state, not as "missing/error". Specifically: `get_document_index_row_by_slug(...)` is undefined when the slug-row is still NULL — caller must lookup by `file_hash` for pre-resolution rows; `list_revisions(milestone_id, doc_id_slug)` returns only resolved rows (NULL-slug rows are filtered out — they're not yet in any revision family).
- **`add_document_index_row` is idempotent on `file_hash`**: a re-ingest of the same physical file (same SHA-256) returns the existing row unchanged. Idempotent across channels — same file arriving via Email and PLM produces ONE row; subsequent arrivals do not overwrite per-ingest fields (`ingest_source`, `inferred_tg_name`, `routing_resolution`, `ingested_at`) — first arrival wins.
- **`add_document_item_association` is idempotent on `(file_hash, delivery_item_id)`**: re-association of the same file to the same item is a no-op; does not duplicate rows or reset per-association timestamps.
- **`CommunicationLog` is append-only**: no UPDATE or DELETE on existing rows per NFR-6 audit semantics.
- **All NSD writes go through `hilda-svc` AD identity** per `[D-013]`. **`CORP\hilda-svc` is a dedicated Active Directory service account — not a HILDA process or container.** It owns Modify permission on the NSD share (`\\share\hilda\`); the HILDA PC's host-level SMB mount authenticates to the corp file server as `hilda-svc` via Kerberos keytab. From the corp file server's perspective every write appears as `hilda-svc`, regardless of which HILDA container (hilda-api, hilda-worker) actually performed the write — the containers see a pre-mounted local filesystem and write to it normally. Per-PM attribution lives in HILDA's `CommunicationLog` (application layer), not in the filesystem ACL.
- **NSD-classified path is the source of truth for in-progress deliverables** per `[D-040]`; submission assembly reads from there per `[D-041]`.
- **Redis cache TTL ≤ 24 hours** per `[D-012]` short-TTL invariant; no durable state in Redis.
- **Async-native**: all DB and Redis IO via async SQLAlchemy + `redis.asyncio` (the modern async client shipped inside `redis-py` 4.2+; the legacy standalone `aioredis` package was absorbed upstream and is archived). **NSD IO** uses the pre-mounted host filesystem at `HILDA_NSD_MOUNT_ROOT` per `[D-013]`'s `hilda-svc` invariant (kernel-level cifs/smb3 mount on the HILDA host; bind-mounted into `hilda-api` / `hilda-worker` containers per `[D-025]`) — async writes via `aiofiles`; sync stat/readdir operations wrapped in `asyncio.to_thread` per `structure-conventions.md` Sync-API wrapping convention. **`smbprotocol` is NOT exercised in the routine IO path** — retained as a declared dependency only for diagnostic CLI (independent share-visibility verification) and as a fallback for dev scenarios where host-mount is unavailable.
- **Schema is canonical** per `[D-046]`: Pydantic models in `core/src/storage/models.py` are the single source from which the SP List provisioning script, Alembic migrations, and YAML template-schema spec are generated. CI gates enforce sync — schema-shape drift between Pydantic models and any of the three generated artifacts is a hard build failure.
- **No credential material stored, logged, or transmitted**: this module never writes decrypted credentials to Postgres, Redis, NSD, logs, compact reports, or error messages. `credential_id` in `CommunicationLogRow` is an opaque reference; resolution to plaintext is `credential_service`'s exclusive responsibility per `[D-019]`.
- **Error-code contract**: all module errors raised as `PipelineError` with `STR-E001..STR-W003` codes registered in `core/src/diagnostics/error_codes.py` per `[D-002]` + `[D-017]`. Compact reports (RPT/MET/QC) emitted per `[D-002]` use only counts, status flags, and bounded enum tokens — never file content or proprietary identifiers. Codes added 2026-06-07: `STR-E005` (cross-milestone association violation per FR-79), `STR-E006` (FolderRoutingTable references unknown item_no per FR-77), `STR-E007` (invalid or expired download token per FR-61), `STR-W002` (unknown tag per FR-82, mirrors TSC-W003), `STR-W003` (default work-item missing for milestone — instantiation race per FR-78), `STR-W004` (orphan override — rule_id not found in any loaded YAML rule per FR-30/FR-31), `STR-E008` (cache_set TTL exceeds 24h cap per `[D-012]`), `STR-W005` (orphan DocumentIndexRow — no DocumentItemAssociation references; surfaced on `--diagnostic`), `STR-W006` (PLM fan-out target has plm_id=None — owner has no PLM issue for this milestone; Ph-2 edge case). Code added 2026-06-09: `STR-W007` (document in staged NSD path age > threshold — TPM resolution pending per FR-87; surfaced on `--diagnostic`; queried via `DocumentItemAssociation.nsd_path_type ∈ {STAGED_NOT_CLASSIFIED, STAGED_NOT_REVISION, UNROUTED}` indexed lookup — added 2026-06-09; default age threshold 7 days, configurable in `customizations/<customer_slug>/storage_alerts.yaml`). Codes added 2026-06-11 (FR-87 TPM resolution): `STR-E009` (TPM resolution on a file not at the expected source state — state mismatch, FR-87 step B/C), `STR-E010` (`tpm_resolve_doc_type` called with asymmetric doc_id_slug/rev_number — one NULL, one populated; violates the FR-86 staged-fill Invariant at the API boundary), `STR-W008` (TPM resolution idempotent re-call — already at target state; informational).
- **NSD path-construction is deterministic from entity attributes**: given a fixed set of slugs, `NSDPath.internal_classified(...)` returns the same path on every host (lab, dev, test). No path mutation after entity creation.
- **Persisted `local_nsd_path` is share-relative POSIX form** (added 2026-06-11 per [D-013] NSD-IO alignment): `DocumentItemAssociation.local_nsd_path` stores `NSDPath.to_relative()` (e.g. `inbound/<carrier>/<device>/<milestone>/<item>/file.pdf`) — **never** a UNC string (`\\share\hilda\...`) or a mount-root-prefixed absolute path. This keeps Postgres rows mount-root-independent: the same row resolves correctly under any `HILDA_NSD_MOUNT_ROOT` (dev `/tmp/test-nsd` vs prod `/nsd`) without a migration when the mount root changes. Runtime prefixing is done by `to_local()` at IO time; `to_unc()`/`from_unc()` exist for diagnostic display only. Persisting a UNC or mount-prefixed string would re-introduce mount-root coupling and is a contract violation.
- **`ingress_folder` vs `target_folder` — naming discipline** (2026-06-07): `ingress_folder` always refers to INBOUND NSD-side paths (HILDA-PC under `TGGroupBase.ingress_nsd`); `target_folder` is reserved for OUTBOUND customer-portal upload destinations (FR-73 / FR-19). The two namespaces are never conflated in storage APIs, models, or path helpers. `NSDPath.ingress_folder(...)` is inbound-only; outbound customer-portal paths are not NSD paths.
- **Symmetric M:M, no primary/secondary** (2026-06-07): `DocumentItemAssociation` is a pure M:M; no `is_primary` flag. The same file may exist at multiple NSD paths simultaneously (one per item's `local_nsd_path` per `[D-040]` / FR-13). Per-(file, item) properties (`local_nsd_path`, `nsd_path_type`, `plm_id`, `plm_attachment_id`, `owner_email`, `upload_timestamp`) live exclusively on the M:M row; per-file properties (`parser_result`, `llm_review_findings`, `inferred_tg_name`, `routing_resolution`, etc.) live exclusively on `DocumentIndexRow`. No dual-write hazard across the two tables.
- **Single-milestone association scope** per FR-79 (Ph-1/Ph-2): all `DocumentItemAssociation` rows for a given `file_hash` share the same `milestone_id`. Cross-milestone associations deferred to Ph-3+; enforcement raises `STR-E005`.
- **Composite PK on M:M** (2026-06-07): `(file_hash, delivery_item_id)` is the natural PK; no synthetic `association_id`. CommunicationLog audit entries reference the pair directly.
- **PLM fan-out is per-(owner × PLM) pair within a milestone** per FR-79 (revised): `fan_out_plm_associations(file_hash)` returns DISTINCT (owner_email, plm_id) pairs. One PLM upload per pair. Result `plm_attachment_id` + `upload_timestamp` are replicated across all M:M rows sharing that pair (case (a) one-owner-N-items) or differ across rows (case (b) two-owners-N-items-two-PLMs).
- **Document TG-of-origin lives on `DocumentIndexRow.inferred_tg_name`** (2026-06-07): the TG IS knowable from the inbound channel (NSD ingress_nsd / email_group_alias / PLM-id reverse-lookup); recorded per-file (per-ingest), not per-association. Consumed by FR-83 TPM reassignment to shortlist candidate work-items within the TG.
- **`download_url` is never persisted** (2026-06-07): per FR-61 download tokens are short-lived (TTL ≤ 300s default) and computed at page-render time via `make_download_token(file_hash, delivery_item_id)`. Token resolves server-side to the per-item `local_nsd_path` on the M:M row. Stored URLs would be stale on path migration, ambiguous in multi-item case, and defeat FR-61's short-lived intent. Worker-internal submission assembly (FR-41 / FR-73) reads NSD via SMB directly through `storage.read_file(NSDPath)` — no URL layer involved.
- **Rule action / trigger references validated against template_schema registries** (2026-06-05): `AutomationRuleOverride.rule_id` resolution and `CommunicationLogRow.action_type` values reference action / trigger registries owned by `template_schema` (`RuleActionRegistry`, `RuleTriggerRegistry`). Storage is opaque to action/trigger semantics but consumers validate at read time. Unknown action/trigger strings are not blocked at write time — registries are customer-extensible per FR-28/FR-29.
- **Tag catalog is customer-scoped, soft-deactivate-only** per FR-82 (revised): `TagCatalogRow.active = False` deactivates a tag without deleting; historical `item_description` references remain valid. Unknown tags raise `STR-W002` (warning), mirroring `TSC-W003` at the template_schema layer.

## Key choices

- `[D-022]` — Celery via Redis broker (Ph-1/Ph-2) + Postgres result backend
- `[D-043]` — Ph-3+ broker migration to RabbitMQ Quorum Queues; Redis retained as cache-only
- `[D-013]` — NSD ACL model: `hilda-svc` writes only; HILDA-mediated reads
- `[D-039]` — Document classification pipeline (hash → slug → Tier-2 LLM → staged/) is consumed here; this module exposes the persistence primitives the classification logic in `email_service` / `llm` uses
- `[D-040]` — NSD classified path = in-progress source of truth; PLM = submitted deliverables only
- `[D-041]` — Submission assembly source = NSD classified path in both phases
- `[D-045]` — Schema/content boundary invariant (data model gated by code release)
- `[D-046]` — Canonical schema source = Pydantic models in this module
- `[D-012]` — BATCH-id idempotency stored in Redis with short TTL (not Postgres)
- **SQLAlchemy 2.x async + Alembic** chosen as the ORM stack (vs Django ORM / peewee / raw asyncpg) — broad ecosystem, async-native, mature Alembic; reference implementation also uses this stack
- **`redis.asyncio`** chosen (vs blocking sync `redis-py` calls) for async-native client matching the rest of HILDA. Note: `redis.asyncio` is the canonical async client shipped inside `redis-py` 4.2+ — the standalone `aioredis` package (referenced in earlier drafts of this MODULE.md) was absorbed upstream and is now archived; the import path is `from redis import asyncio as aioredis` (alias convention) or `import redis.asyncio` directly.
- **Host-mounted NSD filesystem** chosen as the routine NSD IO path per `[D-013]`'s `hilda-svc` invariant: the corp NSD share is mounted at `HILDA_NSD_MOUNT_ROOT` on the HILDA host via Kerberos keytab as `hilda-svc` (kernel-level cifs/smb3); HILDA containers bind-mount this path per `[D-025]` and read/write via plain stdlib filesystem (`pathlib.Path` + `aiofiles`). Rejected alternative: direct `smbprotocol`-based SMB calls from the Python layer — this would require credential material (or delegation) inside the container, contradicting `[D-019]` credential discipline and `[D-013]`'s "containers see a pre-mounted local filesystem" model. `smbprotocol` is retained as a declared dependency for diagnostic CLI and dev fallback only (see Invariants).
- **NSD path encapsulated as `NSDPath` value object** (vs raw strings) — type safety, prevents hand-rolled path bugs, single point of update when path conventions evolve (cf. the 2026-05-14 + 2026-05-21 path-convention rewrites)

## Non-goals

- Does **NOT** cache decrypted credentials. Per `[D-019]` credential discipline, `credential_service` is the only path to decrypted material; this module sees only opaque `credential_id` references.
- Does **NOT** own SP REST client. That's `sharepoint_integration`. This module mirrors SP data into Postgres for fast query; the canonical SP write path goes through `sharepoint_integration`.
- Does **NOT** own the **SP List provisioning script** as code. It's generated from this module's Pydantic models per `[D-046]`, but the generator lives in `deploy/` (or a separate `core/src/schema_generators/` module — to be decided in architecture phase).
- Does **NOT** do document classification. That's `email_service` + `llm` per FR-52 + `[D-039]`. This module only stores classification results.
- Does **NOT** own NSD ACL configuration. Corp infra concern per `[D-013]`.
- Does **NOT** do Celery task dispatch. That's `workflow_engine`. This module only provides the Redis broker URL.
- Does **NOT** own LLM call dedup or prompt-template storage. `llm` module's concern.
- Does **NOT** own SP-alert email parsing. That's `email_service.sp_alert_parser` per `[D-047]`. This module persists the parsed events into `CommunicationLog`.
- Does **NOT** do file format conversion or OCR. PDF/DOCX/XLSX/DOC text extraction is local to `extract_first_page` for `[D-039]` Tier-2 LLM only; richer extraction belongs to `test_report` and `test_report_profiler`.

## Depends on

- `core/src/diagnostics/` — `STR-` error codes registered + RPT/MET/QC compact-report schemas
- `core/src/template_schema/` — canonical entity enums (DocType incl. DEFAULT per `[D-053]`, IngestSource, Channel, Direction, Scope, DeliveryState, ItemType incl. DEFAULT, RuleActionType, RuleTriggerType, RuleSubTriggerType) per `[D-028]`; Pydantic base classes for entity hierarchy; `RuleActionRegistry` + `RuleTriggerRegistry` consumed for read-time validation of `AutomationRuleOverride` and `CommunicationLogRow.action_type`; `TagCatalogEntry` model mirrored by `TagCatalogRow` here; `FolderRoutingEntry` / `TGFolderRouting` mirrored by `TGFolderRoutingRow`; `DefaultWorkItemConfig` lives on `MilestoneBase.default_work_item_config`

## Depended on by

- `tracker` — document-index + association reads/writes through the injected `StorageWriter` Protocol; **no entity CRUD here** — DeliveryItem entities live in SP only (no Postgres mirror per 2026-06-11 architect decision; tracker resolves entity attributes via `sharepoint_integration` and passes them as explicit params)
- `workflow_engine` — Celery broker URL + result backend connection
- `rule_engine` — reads `AutomationRuleOverride` for FR-31 precedence ordering
- `email_service` — `CommunicationLog` writes + BATCH-id idempotency checks + NSD reads for inbound classification
- `messenger` — `CommunicationLog` writes
- `issue_tracker` — `CommunicationLog` writes + document index updates from PLM polls
- `customer_adapter` — `CommunicationLog` `action_type` writes for submission / resubmission events
- `test_report` — `parser_result` writes to document index per FR-16; NSD reads for parsing
- `dashboard` — document enumeration API reads (FR-57); `CommunicationLog` reads for audit views
- `credential_service` — `CommunicationLog` writes for every `get_credential` call per NFR-6

## Deferred

- **Postgres HA / read replicas** — Ph-3+ per `[D-043]`; Ph-1/Ph-2 = single Postgres container with `restart: unless-stopped`
- **Per-owner NSD `inbound/` ACL** — DEF-16 (Ph-3+); Ph-1/Ph-2 uses a shared write group for all NSD owners
- **Filesystem identity attribution in `CommunicationLog`** — DEF-16 (Ph-3+); Ph-1/Ph-2 records `ingest_source = NetworkSharedDrive` without per-owner attribution
- **Cross-region replication** — out of Ph-1 / Ph-2 / Ph-3+ scope (no current trigger)
- **Postgres connection pooling tuning** — implementation-phase concern; defaults in `config/storage.json`
- **Sharded document index** — out of Ph-1 / Ph-2 scope; one Postgres instance handles one customer comfortably. Revisit at Ph-3+ if multi-customer scale-out (DEF-8) introduces volume that warrants sharding.
- **NSD content-addressable storage** — files are stored by classified path, not by content hash; deduplication is a Ph-3+ idea if storage volume warrants
- **Cross-milestone `DocumentItemAssociation`** — Ph-1/Ph-2 enforces same-milestone scope per FR-79 (storage raises `STR-E005`). Ph-3+ revisit: document submitted in milestone N reused in milestone N+1 (e.g. carry-forward waivers, sustaining test reports). Revisit trigger: TPM reports double-uploading documents across milestones.
- **Tag catalog versioning / audit history** — Ph-1/Ph-2 uses a simple `active` flag for soft-deactivation (FR-82 revised). Full audit history of tag renames / merges / catalog snapshots deferred to Ph-3+ if catalog churn warrants. Revisit trigger: customer requests "as-of" reporting on tag membership.
- **Default work-item path namespace evolution** — Ph-1 uses the `_unrouted` sentinel under `internal\<milestone>\` per FR-78. If FR-83 TPM-reassignment volume grows or per-TG default work-items become desirable (FR-78 revisit), the path convention may need a TG-scoped variant; would require migration script + `NSDPath` update.

<!-- BEGIN:STRUCTURE -->
### `audit_ops.py`
- `clear_override(scope, scope_id, rule_id, parameter_name, *, pm_id) -> None` — function — pub (via `__all__`) — FR-31 override delete + audit row (credential_id=pm_id) + cache evict; idempotent.
- `deactivate_tag(customer_id, tag) -> None` — function — pub (via `__all__`) — FR-82 soft-deactivate; preserves historical references.
- `get_active_overrides(scope, scope_id, rule_id) -> list` — function — pub (via `__all__`) — FR-31 hot-path override lookup (expiry-filtered).
- `get_folder_routing_for_tg(milestone_id, tg_name) -> list` — function — pub (via `__all__`) — FR-77 Type-2 routing rows for a TG.
- `get_tag_catalog(customer_id) -> set[str]` — function — pub (via `__all__`) — active tag strings (validation hot path).
- `list_active_overrides(*, scope, scope_id) -> list` — function — pub (via `__all__`) — bulk override load for rule_engine startup.
- `list_all_override_rule_ids() -> set[str]` — function — pub (via `__all__`) — DISTINCT override rule_ids for STR-W004 orphan audit.
- `list_tag_catalog_entries(customer_id, include_inactive=False) -> list` — function — pub (via `__all__`) — full tag rows for dashboard.
- `log_communication(row) -> None` — function — pub (via `__all__`) — append-only CommunicationLog write per NFR-6.
- `query_communications(*, ...filters, limit=100, offset=0) -> list` — function — pub (via `__all__`) — AND-composed audit query, timestamp DESC, cap 1000.
- `reactivate_tag(customer_id, tag) -> None` — function — pub (via `__all__`) — undo deactivate_tag.
- `set_folder_routing_for_tg(milestone_id, tg_name, entries, *, valid_item_nos) -> None` — function — pub (via `__all__`) — FR-77 replace-all; STR-E006 on unknown caller-supplied item_no.
- `set_override(override) -> None` — function — pub (via `__all__`) — FR-31 insert/replace + audit + cache evict.
- `upsert_tag(row) -> None` — function — pub (via `__all__`) — FR-82 tag insert/update; cache evict.

### `config.py`
- `GlobalStorageConfig` — Pydantic BaseModel — pub (via `__all__`) — operational config (nsd_mount_root / db_url / redis_url); 3-tier `from_sources()`.
- `get_storage_config() -> GlobalStorageConfig` — function — pub (via `__all__`) — process-wide config, lazily resolved.
- `set_storage_config(config) -> None` — function — pub (via `__all__`) — TEST/CLI-MOCK ONLY config injection/reset.

### `db.py`
- `AutomationRuleOverrideTable` / `CommunicationLogTable` / `DocumentIndexTable` / `DocumentItemAssociationTable` / `TGFolderRoutingTable` / `TagCatalogTable` — ORM table — pub (via `__all__`) — SQLAlchemy mappings of the storage rows (DocumentIndexTable carries the partial unique index).
- `Base` — DeclarativeBase — pub (via `__all__`) — ORM metadata root for Alembic.
- `configure_engine(url=None, *, echo=False) -> AsyncEngine` — function — pub (via `__all__`) — (re)configure module engine; StaticPool for in-memory sqlite.
- `get_engine() -> AsyncEngine` — function — pub (via `__all__`) — lazily-resolved module engine.
- `get_session() -> AsyncIterator[AsyncSession]` — function — pub (via `__all__`) — plain session iterator.
- `init_db() -> None` — function — pub (via `__all__`) — create schema (dev/test only).
- `session_scope() -> AsyncIterator[AsyncSession]` — function — pub (via `__all__`) — session context with STR-E001 error contract; ops modules' session entry.

### `document_ops.py`
- `add_document_index_row(row) -> None` — function — pub (via `__all__`) — idempotent-on-file_hash index insert (cross-channel first-write-wins).
- `add_document_item_association(assoc) -> None` — function — pub (via `__all__`) — idempotent M:M insert; STR-E005 cross-milestone guard.
- `delete_document_item_association(file_hash, delivery_item_id, *, delete_file=True) -> None` — function — pub (via `__all__`) — remove one M:M row + optional per-item NSD copy.
- `fan_out_plm_associations(file_hash) -> list` — function — pub (via `__all__`) — DISTINCT (owner, plm) PLM upload targets per FR-79.
- `find_doc_id_slugs_for_item(delivery_item_id, doc_type) -> list[str]` — function — pub (via `__all__`) — [D-039] Step 1 slug match / Step 2 short-circuit.
- `get_document_index_row_by_hash(file_hash) -> DocumentIndexRow | None` — function — pub (via `__all__`) — primary content-identity lookup.
- `get_document_index_row_by_slug(milestone_id, doc_id_slug, rev_number) -> DocumentIndexRow | None` — function — pub (via `__all__`) — FR-57 partial-index lookup (resolved rows only).
- `get_documents_for_item(delivery_item_id) -> list` — function — pub (via `__all__`) — JOIN-through-M:M per-item doc list.
- `list_associations_for_file(file_hash) -> list` — function — pub (via `__all__`) — all (file, item) associations.
- `list_associations_for_item(delivery_item_id) -> list` — function — pub (via `__all__`) — FR-67 cleanup support.
- `list_documents_for_milestone(milestone_id, doc_type=None, is_final_only=False) -> list` — function — pub (via `__all__`) — milestone doc enumeration.
- `list_revisions(milestone_id, doc_id_slug) -> list` — function — pub (via `__all__`) — resolved revision family (NULL-slug excluded).
- `make_download_token(file_hash, delivery_item_id, ttl_seconds=300) -> str` — function — pub (via `__all__`) — FR-61 short-lived HMAC token (never persisted).
- `reassign_document_to_workitem(file_hash, source, target, pm_id, *, target_tg_name, target_owner_email, target_plm_id=None) -> None` — function — pub (via `__all__`) — FR-83 transactional reassign (caller-resolved target attrs).
- `resolve_download_token(token) -> tuple[str, str, NSDPath]` — function — pub (via `__all__`) — verify token; STR-E007 on invalid/expired.
- `set_is_final(file_hash, is_final) -> None` — function — pub (via `__all__`) — FR-66; auto-clears sibling revisions.
- `tpm_resolve_doc_type(file_hash, delivery_item_id, new_doc_type, *, doc_id_slug=None, rev_number=None, pm_id) -> None` — function — pub (via `__all__`) — FR-87 step B thin primitive; moves staged_not_classification → classified | staged_not_revision; STR-E009/E010/W008.
- `tpm_resolve_revision(file_hash, delivery_item_id, resolution, *, pm_id) -> None` — function — pub (via `__all__`) — FR-87 step C thin primitive; assigns doc_id_slug/rev_number, moves staged_not_revision → classified; STR-E009/W008.
- `update_association_plm_attachment(file_hash, delivery_item_id, plm_attachment_id, upload_timestamp) -> None` — function — pub (via `__all__`) — FR-79 fan-out result replication.
- `update_review_findings(file_hash, parser_result, llm_review_findings) -> None` — function — pub (via `__all__`) — per-file FR-16/FR-53 result write.

### `models.py`
- `AutomationRuleOverride` / `BatchIdempotencyKey` / `CommunicationLogRow` / `DocumentIndexRow` / `DocumentItemAssociation` / `PLMFanOutTarget` / `TGFolderRoutingRow` / `TagCatalogRow` — Pydantic BaseModel — pub (via `__all__`) — canonical storage row schemas per `[D-046]`.
- `Channel` / `Direction` / `NSDPathType` / `RoutingResolution` — Enum — pub (via `__all__`) — storage-side enums (Channel/Direction for CommunicationLog; NSDPathType FR-86 state; RoutingResolution FR-52 step).
- `RevisionResolution` — frozendataclass — pub (via `__all__`) — FR-87 step C tagged-union (new / revision_of) for `tpm_resolve_revision`.

### `nsd.py`
- `NSDPath` — frozendataclass — pub (via `__all__`) — two-tree NSD path value object; 9 path constructors, `to_relative`/`from_relative` (persisted form), `to_local` (mount IO), `to_unc`/`from_unc` (diagnostic).
- `compute_file_hash(path) -> str` — function — pub (via `__all__`) — SHA-256 per [D-039] Step 0.
- `extract_first_page(path) -> str` — function — pub (via `__all__`) — txt/xlsx first-page text; PDF/DOCX → STR-E004 pending [D-011].
- `list_inbound_drops(carrier, device, milestone, item) -> list` — function — pub (via `__all__`) — FR-55 inbound-folder poll.
- `read_file(path) -> AsyncIterator[bytes]` — function — pub (via `__all__`) — aiofiles stream from host mount (FR-61).
- `write_file(path, content) -> None` — function — pub (via `__all__`) — atomic idempotent write via host mount.

### `qc_templates.py`
- `SCHEMA_ROUNDTRIP` — QCTemplate — pub (via `__all__`) — `STR:schema_roundtrip` QC template; registered at import.

### `redis_client.py`
- `MAX_CACHE_TTL_SECONDS` — module constant — pub (via `__all__`) — 24h cap per [D-012].
- `cache_delete(key) -> None` / `cache_get(key) -> bytes | None` / `cache_set(key, value, ttl_seconds) -> None` — function — pub (via `__all__`) — cache ops; cache_set raises STR-E008 over the cap.
- `check_batch_idempotency(batch_id, item_index) -> str | None` — function — pub (via `__all__`) — [D-012] BATCH idempotency check.
- `get_celery_broker_url() -> str` — function — pub (via `__all__`) — Ph-1/Ph-2 Redis broker URL.
- `record_batch_idempotency(key) -> None` — function — pub (via `__all__`) — idempotent BATCH record.
- `set_redis_client(client) -> None` — function — pub (via `__all__`) — inject client (tests pass fakeredis).

### `storage_cli.py`
- `main() -> None` — function — pub — CLI: `--diagnostic` / `--mock` / `--mock-postgres` / `--validate --customer` / `--alembic-roundtrip`.
<!-- END:STRUCTURE -->
