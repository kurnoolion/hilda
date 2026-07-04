# Module: template_schema

> **Status:** Draft + 2026-06-21 architect cascade + 2026-06-23 SP UI engineer enum lock mini-cascade applied. **2026-06-23 SP UI engineer enum lock**: ItemType mixed-case (short-label categories `Confirmation` + `Default` PascalCase per SP UI engineer locked SP Choice column values; long-named categories `test_tech_waiver_report` + `compliance_certification_release_notes` stay snake_case) — reverts the 2026-06-20 all-lowercase rename for the two short labels. TrackingModality extended 5 → **6 values** with `SPUI = "SPUI"` added (HILDA forward-looking Ph-1; SP UI engineer's SP Choice column has 5-value spec with SPUI omitted — to be added Ph-2). `ingress_nsd` Choice values PascalCase: `None` / `NSD1` / `NSD2` (was lowercase `none` / `nsd1` / `nsd2`). `review_status` keeps 4-value `pending` / `complete` / `not_required` / `failed` (SP UI engineer 'Not Failed' is a typo per architect direction — HILDA forward-looking value is `failed`). `tg_name` keeps 17-value enum (SP UI engineer has 16 with MNO-CP omitted — to be added Ph-2). `customer_delivery_modality` single-value `GoogleDrive` confirmed. Prior 2026-06-21 architect cascade (FR-77/FR-78/FR-81/FR-82/FR-88 + Ph-1 setup-window owner-editability + per-customer Projects + delivery_path_template + nested tag-set + 4-field owner identity + TGGroupBase dropped per [D-051] denormalization). Prior 2026-06-08 cascade (`[D-053]` ItemType 7→4 + DocType 4→5 + alignment invariant) preserved. Code implementation continues in development phase.
>
> **Rollback log:**
> - **2026-07-02 (D-141 template.yaml is authoritative for structural DeliveryItem fields at import)** — NEW sub-module `template_lookup.py` per D-141 (also anchors D-142 reconciliation cascade + D-143 SP-alerts-are-best-effort stance). Process-local eager cache of all `customizations/template_schemas/*/template.yaml` files loaded at worker bootstrap (`workflow_engine.bootstrap._bootstrap_template_lookup`). Public API: `load_all_customer_templates(base_dir)`, `load_customer_template(customer_id, path)`, `get_workitem(customer_id, device_id, milestone_id, item_no) -> dict | None`, `get_customer_delivery_info(customer_id) -> str | None` (customer-level GDrive base URL denormalized per DeliveryItem at import per D-141), `get_delivery_path_template(customer_id) -> str | None`, `clear_cache()` (test hook). Consumers: `workflow_engine.tasks.sp_alert_imports._build_delivery_item` (import merge), `scripts/backfill_static_fields_from_template.py` (one-shot backfill of existing rows). Fallback semantics: template lookup misses degrade to body_kvs-only path (matches pre-D-141 behavior; no hard failure). Field bucketing per D-141: **template-authoritative** — `doc_count`, `tracking_modality`, `milestone_gating`, `item_type`, `item_description`, `tg_path_id`, `item_path_id`, form-factor flags (`handset`/`tablet`/`wearable`/`ir`/`osmr`/`rmr`/`hmr_smr`), `customer_delivery_info`, `delivery_path_template`. **Template-seeded + SP-editable**: `no_customer_upload`, `force_tracking_enabled`, `review_required`, `target_folder`. **SP-only** (body_kvs authoritative): `delivery_state`, `owner_*`, `tg_*` identity, `owner_status_note`, `pm_approval_*`, milestone `*_triggered_at`, `last_updated`, `item_name`, `item_completion_pct`. Public surface + Sub-modules sections should reference `template_lookup` on next full-file curation pass; this rollback-log entry is the authoritative pointer until then. Commits: `8a60abb` (main cascade) + `5b09af4` (customer_delivery_info denormalization follow-up).
> - **2026-06-23 (SP UI engineer enum lock mini-cascade)** — SP UI engineer locked SP Choice column values for 13 SP-side fields; HILDA spec aligned to match SP-side reality where direct (ItemType, ingress_nsd, customer_delivery_modality, BOOL fields) and HILDA-forward-looking where SP UI engineer's column is incomplete (TrackingModality + SPUI, tg_name + MNO-CP, review_status + failed — all to be added/fixed SP-side in Ph-2). **Code changes**: `enums.py` ItemType `CONFIRMATION = "Confirmation"` + `DEFAULT = "Default"` (PascalCase per SP UI engineer lock); TrackingModality adds `SP_UI = "SPUI"` (6-value enum). `models.py` `_v_ingress_nsd` valid set `{"None", "NSD1", "NSD2"}` + `ingress_nsd` field default `"None"`; `DefaultWorkItemConfig.item_type: Literal["Default"] = "Default"`. **Test changes**: `test_template_schema.py` ItemType 4-value assertion updated to mixed-case set; TrackingModality test renamed `test_tracking_modality_6_values_per_d037` with 6-value assertion; ingress_nsd value assertion PascalCase. **Cascade out**: `customizations/template_schemas/MMK/template.yaml` updated (item_type `Confirmation`, ingress_nsd `None`); `storage/MODULE.md` 2 sites (`item_type = "Default"`); `llm/MODULE.md` 1 site. Tracker MODULE.md cascade follows next per strict-order #6 sweep. **Architect rationale**: HILDA spec stays forward-looking — SP UI engineer's omissions are reconciled in their Ph-2 SP-side rollout; HILDA's canonical enum values capture the long-term correct state, not the in-flight SP-side state.
> - **2026-06-12 (architecture re-entry — SP UI engineer 2026-06-10 review absorption)** — three sub-edits absorbing SP UI engineer review items (STATUS line 263 / 280 / 288 / 316 Flags): (a) **field rename** `DeliveryItemBase.milestone_gating: bool` → `DeliveryItemBase.is_milestone_gating: bool` per SP UI engineer naming preference (resolves STATUS line 316 "MilestoneGating schema" item); semantics unchanged — boolean flag indicating whether the item gates milestone closure (FR-64 enablement). (b) **New Invariant** — `DeliveryItemBase.item_no` is immutable per item lifetime: once assigned at template instantiation, never changes for the life of the work-item; reorder operations on SP UI MUST NOT mutate the value. Referential integrity for FR-77 `FolderRoutingEntry.item_no` (line 349) + storage `TGFolderRoutingRow.item_no` + dashboard rendering depends on this; reassignment is via FR-83 (which deletes/creates DocumentItemAssociation rows, not item_no). Resolves STATUS line 316 "ItemNumber-stability schema" item. (c) **Cross-reference** to D-DRAFT-Y (2026-06-12 SP UI engineer denormalization of TGGroups onto DeliveryItems SP list — TG fields are SP-side read-only mirrors; YAML remains source-of-truth for TG values). TGGroupBase Pydantic model UNCHANGED here (still defines the TG schema); SP-list-side denormalization is a `customizations/sharepoint_config/` concern. No code change required in this module — pure docstring + Invariant + field rename. Returning to development phase next.
> - **2026-06-10 (architecture re-entry per `[D-068]` + Confirmation invariant)** — brief return to architecture phase during template_schema development-phase drift reconciliation: (a) `DeliveryItemBase` Public surface adds 2 fields — `pm_approval_at: datetime | None` + `pm_approval_pm_id: str | None` — per `[D-068]` PM-approval recording decision (close-session 2026-06-10 session 3); workflow_engine's PMApproval-trigger UPDATE_STATE task body sets these BEFORE invoking tracker.update_delivery_state(target=ReadyForSubmission); tracker.guards reads them; clearing discipline lives in tracker. (b) New Invariant — "Confirmation items MUST have `no_customer_upload=True`" — per tracker/MODULE.md invariant + `[D-053]` Confirmation semantic; enforced via Pydantic `model_validator` on `DeliveryItemBase` at template load. (c) New error code `TSC-W004` for Confirmation+no_customer_upload violation (distinct from TSC-W003 tag-catalog warning). Returning to development phase next.
> - **2026-06-08 (post-cascade incremental, same day)** — user review pass corrections: (a) `CustomerDeliveryModality` enum — added `GOOGLE_DRIVE = "GoogleDrive"` explicitly per `[D-054]` Ph-1/Ph-2 scope; removed legacy `FILE_STORAGE = "FileStorage"` as too-generic (HILDA runtime needs the specific modality to route to the right `customer_adapter` family); added phase-scoped docstring listing Ph-3+ deferred values (`WebPortal`, `CustomerJiraPortal`); Customer-extensible via `extend_registry()` if back-compat needed. (b) `MilestoneBase.default_work_item_config` — type changed `dict | None` → `DefaultWorkItemConfig | None` for type safety (Pydantic round-trip + explicit semantics); docstring rewritten to clarify FULL-replacement semantic (not partial merge) + explicit separation between STRUCTURAL config (this field) vs query-time candidate filtering at FR-83 (separate concern; per-document `inferred_tg_name` query per `[D-060]` impl note 2026-06-08).
> - **2026-06-08 (Phase B Module cascade — Group 1 of 3 against the corrected `[D-053]` model)** — applied the requirements-phase redesign (locked 2026-06-08 in `requirements.md` FR-7 + FR-85 + FR-86 + FR-87 + amendments + `DECISIONS.md` `[D-053]` impl note 2026-06-08): **ItemType enum collapse 7 → 4** (`CONFIRMATION`, `TEST_TECH_WAIVER_REPORT`, `COMPLIANCE_CERTIFICATION_RELEASE_NOTES`, `DEFAULT` — legacy values `TEST_REPORT` / `TECH_REPORT` / `WAIVER` collapsed into `TEST_TECH_WAIVER_REPORT` per bundled-item-type model; `COMPLETION_PCT` / `SOFTWARE_BINARY` removed as vestigial — customer registry-extendable); **DocType enum expand 4 → 5** (rename `DEFAULT = "default"` → `COMPLIANCE_CERTIFICATION_RELEASE_NOTES = "compliance_certification_release_notes"`; add `UNRESOLVED = "unresolved"`); **strike "1:1 derivation"** docstrings on both ItemType + DocType (replaced with FR-85 classification pipeline + FR-86 alignment invariant references); **add alignment invariant** to Invariants section (`TEST_TECH_WAIVER_REPORT` ↔ `{test_report, tech_report, waiver}`; `COMPLIANCE_CERTIFICATION_RELEASE_NOTES` ↔ `compliance_certification_release_notes`; `DEFAULT` ↔ any of 5; `CONFIRMATION` ↔ none); **expand Purpose anchors** to include FR-85, FR-86, FR-87 + note `[D-053]` 2026-05-28b withdrawn / 2026-06-08 active; **add Key choices bullet** capturing the corrected model + restoration of `CLASSIFY_DOC_TYPE` TaskKind in `llm/MODULE.md`. Remaining cascade Groups 2 + 3 are `storage/MODULE.md` + `llm/MODULE.md` per STATUS.md In-progress 2026-06-08.

**Purpose**: Canonical data model for HILDA's entity hierarchy — Device / Milestone / DeliveryItem (grouped by tg_name) + TG-group metadata (per `(milestone_id, tg_name)`) — and the contract types shared across all runtime modules. Defines Pydantic base models, canonical enums, extensibility registries, slug conventions, and the `CustomerSchema` output contract that `template_schema_ingestor` produces and all runtime modules consume. Serves FR-1–7, FR-39–41, FR-66, FR-70, FR-71, FR-77 (folder routing config types), FR-78 (default work-item config), FR-79 (multi-item association keys), FR-80 (`no_customer_upload`), FR-81 (`tracking_enabled` / `force_tracking_enabled`), FR-82 (routing tag catalog + validation), FR-84 (TG-level data for `sp_alert_parser`), FR-85 (doc_type classification ladder consumes DocType enum + DocTypeRegistry), FR-86 (storage matrix consumes alignment invariant + ItemType/DocType pair semantics), FR-87 (SP UI strict-order resolution validates alignment), NFR-14, and anchors `[D-014]` `[D-018]` `[D-028]` `[D-037]` `[D-046]` `[D-049]` `[D-051]` `[D-053]` (impl notes 2026-05-28b withdrawn + 2026-06-08 active) `[D-054]`.

*This module is pure data model — no IO, no SharePoint, no network. Runtime modules extend these base types with persistence-layer fields (SP List IDs, DB columns) in their own models.*

---

## Public surface

### Enums

```python
class DeliveryState(str, Enum):
    """Extensible via DeliveryStateRegistry — new values added through config, not code.
    Full 11-state enum per FR-7 (NOT_STARTED added 2026-06-21 — initial state at setup_milestone
    per FR-7 + FR-2 R&R lock; was missing in 2026-05-15 rewrite)."""
    NOT_STARTED          = "Not Started"          # initial at setup_milestone per FR-7 + FR-2 R&R; hardcoded by SP UI engineer's web part
    OPEN                 = "Open"                 # transitioned by HILDA at Start Collection per FR-8 step 1
    OUTREACH_SENT        = "OutreachSent"        # initial outreach dispatched per FR-9 (D-124 α: PascalCase + space)
    DOCUMENT_RECEIVED    = "DocumentReceived"    # document arrived via any ingest channel
    UNDER_PM_REVIEW      = "UnderPMReview"      # active TPM review gate per FR-56
    OWNER_CLOSED         = "OwnerClosed"         # owner confirmed done; transient — forks per FR-7 (D-048 multi-rev selection)
    DELAYED              = "Delayed"              # owner-reported delay; transient
    BLOCKED              = "Blocked"              # owner-reported blocker; transient
    READY_FOR_SUBMISSION = "ReadyForSubmission" # PM approved per FR-28 PMApproval trigger
    SUBMITTED_TO_CUSTOMER = "SubmittedToCustomer" # submission package dispatched per FR-18
    CLOSED               = "Closed"               # manually set per FR-14 / FR-64; automated transition deferred per DEF-20

class ItemType(str, Enum):
    """Four core item_types per `[D-053]` impl note 2026-06-08 (supersedes the prior 7-value enum
    + the withdrawn "1:1 derivation of doc_type" framing per `[D-053]` impl note 2026-05-28b).
    item_type defines the workflow category of a work-item; doc_type is classified per content
    via FR-85 and aligned per FR-86 alignment invariant. Extensible via ItemTypeRegistry —
    legacy values (TestReport / TechReport / Waiver / CompletionPct / SoftwareBinary) removed
    from the core enum and may be re-added per-customer via `ItemTypeRegistry.extend_registry()`
    if backward compatibility is needed."""
    CONFIRMATION                           = "Confirmation"                            # type 1 — owner reply closes item; no documents per item type definition (FR-9/FR-10 reply paths) — PascalCase per SP UI engineer lock 2026-06-23
    TEST_TECH_WAIVER_REPORT                = "test_tech_waiver_report"                 # type 2 — receives any of {test_report, tech_report, waiver}; review_required = true (FR-53 fires); doc_count counts test_reports only (FR-7)
    COMPLIANCE_CERTIFICATION_RELEASE_NOTES = "compliance_certification_release_notes"  # type 3 — receives compliance_certification_release_notes documents; review_required = false; no parser, no review
    DEFAULT                                = "Default"                                  # type 4 — auto-instantiated default work-item per milestone per FR-78; accepts any document whose target work-item is not resolved; tg_name = "_unrouted" sentinel; sort_order = max+1; not editable; not deletable; immutable doc_count = 0, review_required = false; `[D-039]` revision determination SKIPPED at ingest per FR-86 — PascalCase per SP UI engineer lock 2026-06-23

class TrackingModality(str, Enum):
    """Multi-value per DeliveryItem (stored as a list) per `[D-037]` (2026-05-13).
    Five Ph-1 values; valid combinations require at least one status-capable + one
    document-capable modality per FR-7."""
    EMAIL               = "Email"                 # status + documents via email reply
    CORPORATE_MESSENGER = "CorporateMessenger"    # status only; no attachments; Ph-2 inbound per FR-54
    CORPORATE_PLM       = "CorporatePLM"          # documents only; owner uploads to corp PLM; HILDA polls per FR-26
    NETWORK_SHARED_DRIVE = "NetworkSharedDrive"   # documents only; owner drops in NSD ingress folder per FR-13/FR-55 (NSD1 or NSD2 per TGGroupBase.ingress_nsd)
    CUSTOMER_JIRA       = "CustomerJIRA"          # status only; HILDA polls customer JIRA per FR-25
    # InternalIssueTracker removed per [D-037] — no internal-JIRA tracking in Ph-1/Ph-2; corp PLM serves that role.

class IngestSource(str, Enum):
    """Per FR-13 + `[D-039]` — recorded in document index for every classified document."""
    EMAIL                = "Email"
    CORPORATE_PLM        = "CorporatePLM"
    NETWORK_SHARED_DRIVE = "NetworkSharedDrive"
    SHAREPOINT_UI        = "SharePointUI"   # PM-uploaded via SP UI per FR-62 (Ph-2)

class DocType(str, Enum):
    """Five doc_type values per `[D-053]` impl note 2026-06-08 (supersedes the prior 4-value enum
    + the withdrawn "1:1 derivation from item.item_type" framing). `doc_type` is classified per
    inbound document via the FR-85 2-step ladder (filename regex Step 1 + LLM CLASSIFY_DOC_TYPE
    Step 2 restricted to {test_report, tech_report, waiver}) — NOT derived from item.item_type.
    `compliance_certification_release_notes` is detected by filename regex only (Step 1) — LLM
    never returns this value. `unresolved` is the residual on Step 2 low-confidence + Default-
    routed-undetermined state. Alignment with item_type enforced per FR-86 storage matrix —
    misaligned pairs land on `staged-not-classified` for TPM resolution per FR-87. Used as
    folder organizer in NSD path `<doc_type_slug>/<doc_id_slug>/revN/` per `[D-013]` (when set).
    `CLASSIFY_DOC_TYPE` TaskKind restored in `llm/MODULE.md` per FR-85 Step 2 (un-revert of the
    2026-05-28b removal)."""
    TEST_REPORT                            = "test_report"                             # triggers FR-16 parser + FR-46 final/interim + FR-53 LLM review when review_required=true (type 2 items only)
    TECH_REPORT                            = "tech_report"                             # triggers FR-53 LLM review when review_required=true (type 2 items only)
    WAIVER                                 = "waiver"                                  # triggers FR-53 LLM review when review_required=true (type 2 items only)
    COMPLIANCE_CERTIFICATION_RELEASE_NOTES = "compliance_certification_release_notes"  # renamed from prior `default` 2026-06-08 — bundle for compliance docs / certification docs / release notes; FR-16/FR-46/FR-53 do NOT fire for this doc_type; auto-assigned for type 3 items; detected by filename regex only (no LLM)
    UNRESOLVED                             = "unresolved"                              # FR-85 Step 2 low-confidence outcome OR Default-routed-undetermined state; no downstream actions fire; awaits TPM resolution via FR-87 step (B) on `staged-not-classified` NSD path

class CustomerDeliveryModality(str, Enum):
    """Per-item delivery modality to the customer/carrier. Phase-scoped values per `[D-054]`
    customer_adapter scope: Ph-1/Ph-2 = Google Drive only; Ph-3+ adds web portal + JIRA
    customer portal (deferred per DEF-N). Customer-extensible via
    `CustomerDeliveryModalityRegistry` per FR-7 / NFR-14 — registry pattern allows
    customer-specific values without code change. Legacy value `FileStorage` removed
    2026-06-08 as too-generic (HILDA runtime needs the specific modality to route to
    the right `customer_adapter` family); customers needing back-compat can re-add via
    `extend_registry()`."""
    NONE                   = "None"                    # no automated delivery; TPM uploads manually if needed
    EMAIL                  = "Email"                   # delivery via email attachment
    CUSTOMER_TRACKING_SYS  = "CustomerTrackingSystem"  # customer's own tracking system (e.g., JIRA — Ph-1 read-only per FR-25; Ph-3+ write via JIRA-as-customer-portal)
    GOOGLE_DRIVE           = "GoogleDrive"             # Ph-1/Ph-2 — shared Google Drive folder per `[D-054]`; browser-automation per `[D-054]` impl note 2026-06-05 (selenium/playwright on headless Chromium); per-carrier adapter at `customizations/customer_adapter/<carrier_slug>_adapter.py`
    # Ph-3+ values (deferred per [D-054]):
    # WEB_PORTAL          = "WebPortal"               # customer's web portal (TBD per-carrier)
    # CUSTOMER_JIRA_PORTAL = "CustomerJiraPortal"     # JIRA-as-customer-portal write surface

class MilestoneStatus(str, Enum):
    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    COMPLETED   = "Completed"
    DELAYED     = "Delayed"

class RuleScope(str, Enum):
    GLOBAL   = "Global"
    CUSTOMER = "Customer"
    DEVICE   = "Device"

class RuleActionType(str, Enum):
    """Per FR-28 / FR-29 (rewritten 2026-06-05). Extensible via RuleActionRegistry — new actions
    added via config without code change per FR-29 invariant. The closed enum seeds the registry."""
    # Ph-1 actions
    SEND_REMINDER                    = "SendReminder"
    ESCALATE                         = "Escalate"
    UPDATE_STATE                     = "UpdateState"
    START_ITEM_COLLECTION            = "StartItemCollection"        # FR-8 / FR-29
    SEND_INITIAL_OUTREACH            = "SendInitialOutreach"        # FR-9 per-modality outreach
    NOTIFY_NEW_OWNER                 = "NotifyNewOwner"             # FR-28 OwnerReassigned sub-trigger
    TRIGGER_PARSER                   = "TriggerParser"              # rule-based test-report parser per FR-16
    TRIGGER_AI_REVIEW                = "TriggerAIReview"            # FR-53 LLM quality review (renamed from TRIGGER_LLM_REVIEW 2026-06-05 to match FR-29 canonical name)
    QUEUE_SUBMISSION                 = "QueueSubmission"
    NOTIFY_PM                        = "NotifyPM"                   # dashboard alert; no owner-facing outbound
    NOTIFY_HILDA_OPS                 = "NotifyHildaOps"             # FR-75 — routes background faults to HildaOpsAlert (distinct from NotifyPM)
    INSTANTIATE_DEFAULT_WORK_ITEM    = "InstantiateDefaultWorkItem" # FR-78 — auto-instantiate at tracker creation
    MILESTONE_STORAGE_CLEANUP        = "MilestoneStorageCleanup"    # FR-76 — delete NSD1 internal subtree on MilestoneAllClosed
    HALT_MILESTONE_POLLING           = "HaltMilestonePolling"       # FR-74 — stop FR-25/FR-26/FR-55 polling for milestone
    FINAL_SWEEP                      = "FinalSweep"                 # FR-74 — one final poll across NSD/PLM/Email before halt
    REASSIGN_DOCUMENT_TO_WORKITEM    = "ReassignDocumentToWorkItem" # FR-83 — TPM-manual default-work-item resolution
    PROPAGATE_TAGS_TO_ACTIVE_TRACKERS = "PropagateTagsToActiveTrackers"  # FR-82 — fires on ItemModified.TagsModified
    PM_APPROVAL                      = "PMApproval"                 # FR-28 PM approval trigger

    # Ph-2 actions
    CANCEL_OUTSTANDING               = "CancelOutstanding"          # Ph-2 FR-29 — cancel pending reminders + notify owner
    NOTIFY_OWNER_DOC_COUNT_PENDING   = "NotifyOwnerDocCountPending" # Ph-2 — FR-7 OwnerClosed guard violation notify
    TRIGGER_VERSION_SELECTION        = "TriggerVersionSelection"    # Ph-2 [D-048] / FR-66
    TRIGGER_PLM_CLEANUP              = "TriggerPLMCleanup"          # Ph-2 FR-67 PLM stale-attachment deletion
    TRIGGER_ODF                      = "TriggerODF"                 # Ph-2 [D-049] / FR-71 Owner Discovery Function
    SEND_OWNER_ROUTING_QUERY         = "SendOwnerRoutingQuery"      # Ph-2 FR-83 — owner outreach for unrouted documents

class RuleTriggerType(str, Enum):
    """Per FR-28 trigger taxonomy (revised 2026-06-05). Extensible via RuleTriggerRegistry —
    new triggers added via config. The closed enum seeds the registry."""
    # Ph-1 triggers
    ITEM_CREATED                    = "ItemCreated"
    ITEM_MODIFIED                   = "ItemModified"                  # sub-triggers: OwnerReassigned, DeadlineMoved, TagsModified
    STATE_CHANGE                    = "StateChange"
    OWNER_STATUS_CONFIRMED          = "OwnerStatusConfirmed"
    LAST_CONTACT_THRESHOLD          = "LastContactThreshold"          # FR-10
    DEADLINE_PROXIMITY              = "DeadlineProximity"             # FR-11
    ATTACHMENT_RECEIVED             = "AttachmentReceived"
    AI_REVIEW_RESULT                = "AIReviewResult"                # FR-53
    PM_APPROVAL                     = "PMApproval"
    TRACKER_CREATED                 = "TrackerCreated"                # FR-78 default work-item; FR-71 ODF (Ph-2)
    MILESTONE_ALL_CLOSED            = "MilestoneAllClosed"            # Ph-1 cleanup-only per FR-76 (state-advance variant deferred per DEF-20)
    COLLECTION_PHASE_CLOSURE_REACHED = "CollectionPhaseClosureReached" # FR-74 — fires HaltMilestonePolling + FinalSweep
    CREDENTIAL_EXPIRED              = "CredentialExpired"             # FR-20

    # Ph-2 triggers
    ITEM_DELETED                    = "ItemDeleted"                   # Ph-2 — fires CancelOutstanding
    UNROUTED_DOCUMENT_ACCUMULATED   = "UnroutedDocumentAccumulated"   # Ph-2 — fires SendOwnerRoutingQuery per FR-83

class RuleSubTriggerType(str, Enum):
    """Sub-triggers under ItemModified per FR-28."""
    OWNER_REASSIGNED = "OwnerReassigned"   # owner field changed
    DEADLINE_MOVED   = "DeadlineMoved"     # expected_completion_date changed
    TAGS_MODIFIED    = "TagsModified"      # item_description (tag list) changed; fires PropagateTagsToActiveTrackers per FR-82

class TestReportItemStatus(str, Enum):
    """Canonical per-item status vocabulary for test reports (anchors [D-011] FR-16)."""
    PASSED         = "passed"
    FAILED         = "failed"
    NON_APPLICABLE = "non-applicable"
    WAIVED         = "waived"
    NOT_STARTED    = "not-started"

class TestReportClassification(str, Enum):
    """final | interim classifier output (anchors FR-46)."""
    FINAL   = "final"
    INTERIM = "interim"
```

### Extensibility registries (FR-7, NFR-14)

```python
# Mutable registries loaded from config/<customer>.json at startup.
# Core code validates against the registry, not the closed enum.
# Allows new delivery states, item types, and modalities without code change.

DeliveryStateRegistry: set[str]   # initialized from DeliveryState enum values
ItemTypeRegistry: set[str]        # initialized from ItemType enum values
TrackingModalityRegistry: set[str]
CustomerDeliveryModalityRegistry: set[str]
TGNameRegistry: set[str]          # technical group names (e.g. "Hardware", "Software"); customer-extensible
RuleActionRegistry: set[str]      # initialized from RuleActionType enum values; per FR-29 — new actions added via config without code change
RuleTriggerRegistry: set[str]     # initialized from RuleTriggerType enum values; per FR-28 — new triggers added via config without code change
DocTypeRegistry: set[str]         # initialized from DocType enum values; per FR-7 amendment — customer-extensible

def extend_registry(registry: set[str], values: list[str]) -> None:
    """Called at startup by config loader to add customer-specific extension values."""
```

### Slug convention (D-013)

```python
SLUG_PATTERN: re.Pattern  # re.compile(r'^[a-zA-Z0-9_-]+$')

def validate_slug(value: str) -> str:
    """Validator for all path_slug fields. Raises TSC-E004 if pattern fails."""

def make_slug(human_name: str) -> str:
    """Deterministic: lower-case, replace spaces/special chars with '-', truncate to 64 chars.
    Minted at entity-creation; never recomputed on rename (stored value is authoritative)."""
```

### Entity base models

```python
class DeviceBase(BaseModel):
    """Per-device canonical fields. NOTE: `assigned_pm_id` here is the template-time
    expected PM identity (informational); at runtime HILDA resolves PM 3-tuple via
    per-customer Projects_<customer_id> lookup per [D-088] + NFR-21 2026-06-21 amendment.
    The template-time value may be authoritative for setup_milestone provisioning logic;
    runtime always defers to the SP Projects list."""
    device_id:          str
    device_name:        str
    customer_id:        str
    assigned_pm_id:     str | None = None    # template-time PM identity; runtime resolves via Projects_<customer_id> per [D-088]
    status:             str   # validated against DeliveryStateRegistry equivalent
    template_id:        str | None = None
    path_id:            str   # [a-zA-Z0-9_-]+, immutable after creation (renamed from path_slug 2026-06-21 per session slug→id rename)
    target_launch_date: date | None = None

class MilestoneBase(BaseModel):
    """Per-milestone canonical fields. Milestones SP list is GLOBAL per architect lock
    2026-06-21 (intentional asymmetry — milestone names like LE-2 reused across carriers;
    Projects + Deliverables are per-customer). Composite uniqueness enforced by SP UI
    engineer's setup_milestone via `(carrier, project_id, project_model, Title)`."""
    milestone_id:   str        # SP intrinsic auto-Counter Id (per architect direction 2026-06-21)
    carrier:        str        # = customer_id; denormalized per Milestones row for composite-key
    project_id:     int        # SP intrinsic Id of Projects_<customer_id> row (per [D-088] + NFR-21 2026-06-21 amendment)
    project_model:  str        # = device_id; denormalized per Milestones row for composite-key
    milestone_name: str        # = Title (SP intrinsic) per [D-091] YAML key naming
    sort_order:     int
    target_date:    date | None  # sole authoritative deadline per [D-085]; TPM types before Start Collection per FR-88
    status:         MilestoneStatus
    email_cc_list:  list[dict] | None   # [{name, email, role}]; applied to all emails in this milestone
    path_id:        str        # canonical path slug per [D-013] (renamed from path_slug 2026-06-21)
    # Per-milestone fields added 2026-06-05 (FR-78 / [D-053]):
    default_work_item_config: DefaultWorkItemConfig | None = None  # per FR-78 — per-milestone OVERRIDE of the tracker-wide default. Typed as `DefaultWorkItemConfig | None` for type safety. None → inherit tracker-wide default from CustomerTemplateBase. Non-None → REPLACES the tracker-wide config (full replacement, NOT partial merge). Configuration is STRUCTURAL (how the default work-item entity is instantiated); routing/candidate filtering at FR-83 reassignment is a separate query-time concern per `[D-060]` impl note 2026-06-08. Exactly one default work-item per milestone (NOT per TG).
    # HILDA-managed runtime button-trigger timestamps (NOT authored in template.yaml;
    # written by SP UI engineer's web part on button click; read by HILDA via SP-alert):
    milestone_collection_started_at:    datetime | None = None  # SP UI engineer writes on Start Collection click; HILDA reads alert and performs FR-8 downstream actions
    milestone_submission_triggered_at:  datetime | None = None  # SP UI engineer writes on Submit Milestone click; HILDA reads alert + performs submission actions (FR-69/FR-77)
    closed_all_items_triggered_at:      datetime | None = None  # SP UI engineer writes trigger on Close All Items click per FR-64 Option (b); HILDA dispatches close_all_items Celery task + cascades per-item delivery_state = Closed via [D-064]
    refresh_requested_at:               datetime | None = None  # Ph-2 per [D-089]
    # HILDA-managed milestone-level state:
    milestone_completion_pct:           int = 0     # PERCENTAGE; HILDA-written per FR-70 milestone-level aggregation
    # Download-package fields (Ph-2 per [D-089]):
    download_package_request_timestamp: datetime | None = None  # SP-internal 32-char truncation: download_package_request_timesta per FR-40 [D-065]
    download_package_status:            str | None = None       # Ph-2 STR Choice (pending/in_progress/ready/failed)
    download_package_url:               str | None = None       # Ph-2 STR (URL)
    download_package_generated_at:      datetime | None = None  # Ph-2 DateTime

class DeliveryItemBase(BaseModel):
    """Per-work-item canonical fields. As of 2026-06-21:
    - Owner identity is 4-field free-form text per FR-88 + [D-080] + [D-086]
      (owner_corp_usa_email, owner_corp_email, owner_corp_id, owner_name); single
      `owner_email` removed.
    - TG fields denormalized onto this model per [D-051] (TGGroups SP list dropped;
      TGGroupBase Pydantic model removed; TG fields now live on each row).
    - `item_description` is nested JSON list-of-lists per FR-82 (per-document tag-sets)
      — supersedes the 2026-06-05 comma-separated string framing; TSC-W007 subset detection
      + TSC-W008 doc_count consistency enforced.
    - `force_tracking_enabled` is sole per-item tracking gate (SP BOOL column-default=true)
      per FR-81 option (a) lock 2026-06-20; per-TG `tracking_enabled` no longer exists.
    - `target_folder` is template-author-supplied (per-item sub-path under milestone HOME);
      runtime composes upload destination = customer_delivery_info + delivery_path_template
      (expanded) + target_folder per FR-77 + NFR-21 §6 amendment 2026-06-21.
    """
    item_id:                         str
    item_no:                         int        # sequential within milestone; unique on (milestone_id, item_no)
    milestone_id:                    str        # parent milestone — no Deliverable level ([D-028])
    item_name:                       str
    item_description:                list[list[str]] | None  # per FR-82 nested tag-set lock 2026-06-20 — per-document tag-sets as JSON list-of-lists. Outer list = one entry per expected document (length must equal doc_count per TSC-W008). Inner list = tag-set for that document (e.g. [["Sustainability"], ["SDoc"], ["Qualification", "Product"]] for 3 expected docs). Empty/null only for confirmation items + default WI. Validator raises TSC-W007 on subset-overlap across rows in the same milestone+TG; TSC-W008 on doc_count ≠ len(item_description); TSC-W003 on unknown tags. Tags propagate via ItemModified.TagsModified → PropagateTagsToActiveTrackers per FR-82.
    delivery_state:                  str   # validated against DeliveryStateRegistry
    prior_delivery_state:            str | None = None  # HILDA-managed per FR-7 Delayed/Blocked exit paths; null in normal flow
    expected_completion_date:        date | None
    actual_completion_date:          date | None  # auto-set when delivery_state → OwnerClosed (per FR-15 update)
    item_type:                       str   # validated against ItemTypeRegistry
    # 4-field owner identity per FR-88 + [D-080] + [D-086] (free-form text, no AD validation;
    # all 4 null in template per Ph-1 setup-window lock 2026-06-20 — TPM types in SP UI
    # between setup_milestone and Start Collection):
    owner_corp_usa_email:            str | None = None   # preferred outreach recipient per [D-080]
    owner_corp_email:                str | None = None   # fallback outreach recipient (corp non-USA domains)
    owner_corp_id:                   str | None = None   # corp directory identifier; PLM grouping key per FR-5 + [D-035]; engineer-stable
    owner_name:                      str | None = None
    tracking_modality:               list[str]  # MULTI-VALUE per [D-037] — list of TrackingModality values; validated against TrackingModalityRegistry
    actual_item_info:                str | None  # PLM issue URL for (device, milestone, owner_corp_id) tuple per FR-57; set on first document arrival
    plm_id:                          str | None  # PLM system ID; one issue per (device, milestone, owner_corp_id) tuple per [D-035] / FR-8
    handset:                         bool = False  # form factor applicability flags (static, from template) per [D-084]
    tablet:                          bool = False
    wearable:                        bool = False
    ir:                              bool = False
    osmr:                            bool = False
    rmr:                             bool = False
    hmr_smr:                         bool = False
    # customer_delivery_modality REMOVED from DeliveryItemBase per D-126 Q2 lock 2026-06-26
    # (moved to CustomerTemplateBase as per-customer top-level; subclass-implicit at runtime).
    # no_customer_upload is sole upload gate per FR-80.
    customer_delivery_info:          str | None  # PER-ROW value from Deliverables SP list per D-126 Q1 (e.g. "drive.google.com"); passed as 9th arg to customer_adapter binding
    # customer_delivery_credential_id REMOVED per D-126 + [D-019] shared HILDA ops-team identity
    owner_status_note:               str | None  # latest interim owner update; auto-populated from inbound
    comment:                         str | None
    last_updated:                    datetime
    last_owner_contacted:            datetime | None
    last_reminder_triggered_at:      datetime | None  # HILDA + SP UI dual-writer per NFR-21 §5 amendment 2026-06-21
    last_owner_response_at:          datetime | None  # set by FR-12 inbound parsing; feeds FR-10 no-response detection
    reminder_count:                  int = 0  # incremented per outreach event per FR-9 / FR-10 / FR-65
    sort_order:                      int
    path_id:                         str        # canonical path slug per [D-013] (renamed from path_slug 2026-06-21 per session slug→id rename)
    # FR-7 / FR-53 / FR-70 fields:
    doc_count:                       int = 1     # per FR-7; number of expected documents = len(item_description) per TSC-W008; 0 for Confirmation items + default WI
    review_required:                 bool = False # per FR-2 / FR-53; gates LLM quality review (FR-53); Ph-1 early-drop lock 2026-06-19 = false for all items
    review_status:                   str   # per FR-53 / FR-60; 4-value enum: pending | complete | not_required | failed (added 2026-06-19)
    item_completion_pct:             int = 0     # per FR-70; document-review completion percentage; computed field
    email_cc_list:                   list[dict] | None = None  # per FR-2 per-item override; pre-populated from TG-denormalized default_cc_list at tracker creation; array of {name, email, role}
    milestone_gating:                bool = True  # renamed back from is_milestone_gating 2026-06-21 — SP UI engineer xlsx + spec converged on `milestone_gating`; does this item gate milestone closure (FR-64 Close All Items enablement)? Always True for default WI per FR-78 hardcoded invariant.
    no_customer_upload:              bool = False  # per [D-054]; True for confirmation items (TSC-W004 invariant); False otherwise
    force_tracking_enabled:          bool = True  # SP BOOL column-default=true per FR-81 option (a) lock 2026-06-20; sole per-item tracking gate (per-TG `tracking_enabled` removed). False for default WI per FR-78 hardcoded invariant.
    manual_triage_required:          bool = False  # HILDA-set on FR-12 path c.2 below-threshold; TPM-clearable
    plm_id:                          str | None = None  # HILDA-written at collection kickoff per (device, milestone, owner_corp_id) tuple per FR-8 step 2
    # FR-77 routing fields:
    ingress_folder:                  str | None = None   # per FR-77 Type-2 routing — INBOUND folder path under NSD ingress (HILDA-PC side); consumed by sp_alert_parser / email_service routing pipeline (FR-52 step 3). Distinct from `target_folder`.
    target_folder:                   str | None = None   # OUTBOUND — template-author-supplied sub-path under milestone HOME directory; HILDA composes final upload destination at FR-77 dispatch = customer_delivery_info + delivery_path_template (expanded with project_model + milestone_name) + target_folder per NFR-21 §6 amendment 2026-06-21. Null for confirmation items + default WI.
    # FR-78 default-WI hardcoded fields (also live on regular WIs):
    item_path_id:                    str | None = None   # NSD path component (e.g. "mno_cpm_item"); null for confirmation items + default WI per FR-78 lock 2026-06-21
    tg_path_id:                      str | None = None   # NSD TG path component (e.g. "mno_cpm"); "_unrouted" for default WI per FR-78
    # TG-denormalized fields per [D-051] (TGGroupBase dropped 2026-06-21; TG-grouping
    # semantics preserved via shared tg_name + denormalized values across items in same TG):
    tg_name:                         str | None    # validated against TGNameRegistry; "_unrouted" for default WI per FR-78
    ingress_nsd:                     str = "None"  # Choice per FR-13 + SP UI engineer lock 2026-06-23: None / NSD1 / NSD2; "None" for Ph-1 early drop per architect lock 2026-06-21
    folder_routing_enabled:          bool = False  # per FR-77 Type-2 routing opt-in
    tg_email_group_alias:            str | None = None  # TG corporate email distribution alias; null in Ph-1 template per setup-window lock; TPM types before Start Collection
    tg_owner_name:                   str | None = None
    tg_owner_corp_usa_email:         str | None = None
    tg_owner_corp_email:             str | None = None
    tg_owner_corp_id:                str | None = None
    corp_id_list:                    list[str] | None = None  # complete corp-ID list of TG members; semi-colon-separated when serialized to SP STR column per architect direction 2026-06-19; messenger escalation uses this when set
    # JIRA polling state (Ph-2 SP write-back per FR-25 (b)):
    jira_open_ticket_count:          int = 0     # Ph-2 only — HILDA-written from JIRA polling
    jira_ticket_summary_json:        str | None = None  # Ph-2 only — JSON list of top-N tickets
    # PM-approval recording per [D-068]:
    pm_approval_at:                  datetime | None = None  # set by workflow_engine PMApproval-trigger UPDATE_STATE task body BEFORE invoking tracker.update_delivery_state(target=ReadyForSubmission); read by tracker.guards.check_transition_guards guard #3. Cleared on entry to UNDER_PM_REVIEW + on rewind from SUBMITTED_TO_CUSTOMER per [D-067] + on DELAYED/BLOCKED return to UNDER_PM_REVIEW.
    pm_approval_pm_id:               str | None = None       # PM/TPM attribution for the PMApproval action; cleared together with pm_approval_at.
    # TPM-resolution fields per FR-83 / FR-87:
    tpm_reassignment_target_item_id: int | None = None  # SP integer Required=No per architect direction 2026-06-21
    tpm_resolved_doc_type:           str | None = None   # LIST/Choice column per architect direction 2026-06-21: doc_type Choice value or null
    tpm_revision_resolution:         str | None = None   # Ph-2 — TPM-resolved revision per FR-66
    # FR-87 audit fields:
    list_documents_url_prefix:       str | None = None   # SP web part property at deployment per FR-56 (c); not authored in template (SP-side config)
    per_item_rule_overrides:         str | None = None   # Ph-2 — JSON; FR-31 sub-3 manual trigger overrides (architect-flagged 2026-06-21 — actual storage in HILDA-local Postgres per FR-31 + [D-062]; SP column is Ph-2 future)

# ~~TGGroupBase~~ — DROPPED 2026-06-21. Rationale: per [D-051] (denormalization decision) +
# architect lock 2026-06-21, all TG-level fields are denormalized per-work-item onto
# DeliveryItemBase. No separate TGGroups SP list exists; no separate Pydantic model is
# needed. TG-grouping semantics are preserved via shared tg_name across items in the same
# milestone; aggregation (e.g. one outreach per TG-batch) is computed at runtime by
# email_service from the denormalized values. Cf. [D-051] impl note 2026-06-12
# (D-DRAFT-Y promotion); cf. template.yaml work_items denormalization pattern.

class CustomerTemplateBase(BaseModel):
    """Top-level customer template per FR-39/FR-40 / [D-091] YAML structure.
    Authored manually by PM team lead in Ph-1; generated by template_schema_ingestor
    per [D-010] + [D-018] in Ph-2+.
    Reads from `customizations/template_schemas/<customer_id>/template.yaml`."""
    template_id:      str
    customer_id:      str        # carrier code; HILDA-canonical (YAML top-level key per [D-091])
    template_name:    str
    template_version: int
    # HILDA-config-only fields (NOT in SP per [D-083]):
    customer_jira_url:        str | None = None    # read from template.yaml at startup per FR-25 (b)
    # Customer-level carrier-portal delivery config (added 2026-06-21 per FR-77 + NFR-21 §6 amendment):
    customer_delivery_info:   str        # base URL for carrier upload (e.g. "drive.google.com"); denormalized per-item at setup_milestone
    delivery_path_template:   str        # template producing milestone HOME directory path; supports literal segments + {placeholders}. Example: "OEM_Folder1/OEM_Folder2/{project_model}/{milestone_name}" → "OEM_Folder1/OEM_Folder2/MODEL-A/P1". Expanded at FR-77 dispatch; combined with customer_delivery_info + per-item target_folder to yield final upload destination.
    # Devices + milestones:
    devices:    dict[str, DeviceBase]    # YAML key = device_id per [D-091]; one entry per device launched under this customer
    milestones: list[MilestoneBase]      # same milestone set instantiated for every device at setup_milestone per FR-40
    # DeliveryItems nested within milestones directly — no Deliverable level ([D-028])
    is_active: bool = True

class DefaultWorkItemConfig(BaseModel):
    """Per FR-78 + [D-053] — configures the auto-instantiated default work-item per MILESTONE.
    Exactly one default work-item per milestone (NOT per TG). The default work-item is a
    milestone-level catch-all: no real tg_name, no owner, no TG-scoped behavior. Excluded
    from FR-74 collection-phase-closure threshold (would never fire otherwise).

    Routing model: when the FR-52 pipeline cannot resolve a specific work-item, the document
    lands here. The document's TG IS knowable from the inbound channel and is recorded on
    the document record as `DocumentIndexRow.inferred_tg_name` (storage module), NOT on the
    default work-item. FR-83 TPM-manual reassignment uses inferred_tg_name to shortlist
    candidate work-items within that TG.

    Hardcoded inventory per FR-78 architect lock 2026-06-21 (expanded):
    """
    # Identity:
    tg_name:               Literal["_unrouted"] = "_unrouted"   # system-reserved TG
    item_name:             str = "Unrouted Documents"
    item_type:             Literal["Default"] = "Default"       # PascalCase per SP UI engineer lock 2026-06-23 (reverts 2026-06-20/21 lowercase rename for the two short-label categories Confirmation/Default; the long-named categories test_tech_waiver_report + compliance_certification_release_notes stay snake_case)
    # Path components per FR-78 + FR-86 (added 2026-06-21):
    tg_path_id:            Literal["_unrouted"] = "_unrouted"   # first segment of FR-86 `_unrouted/<inferred_tg_name>/` NSD path
    item_path_id:          None = None  # null — default WI has no per-WI item subfolder; documents fan out under per-document <inferred_tg_name>
    # Tracking / outreach gates per FR-78 + FR-81 architect lock 2026-06-21:
    force_tracking_enabled: Literal[False] = False  # the one explicit exception to FR-81 column-default True; no tracking_modality, no owner_*, no outreach surface
    tracking_modality:     None = None  # null — no modality; polling/outreach paths short-circuit on this
    # Owner identity per FR-78 + FR-88 — all 4 fields null (TPM cannot edit per FR-78 invariant):
    owner_corp_usa_email:  None = None
    owner_corp_email:      None = None
    owner_corp_id:         None = None
    owner_name:            None = None
    # Workflow gates per FR-78 hardcoded invariants:
    milestone_gating:      Literal[True] = True   # always True; YAML template's milestone_gating ignored for default WI per FR-78 + MilestoneAllClosed gating-aware semantic
    no_customer_upload:    Literal[True] = True   # cannot upload to carrier portal per FR-77
    review_required:       Literal[False] = False # never review
    review_status:         Literal["not_required"] = "not_required"
    doc_count:             Literal[0] = 0         # never set; documents fan in via reassignment per FR-83
    # Structural:
    sort_order_strategy:   Literal["max_plus_1", "fixed"] = "max_plus_1"
    sort_order_fixed:      int | None = None   # used when strategy == "fixed"
    not_editable:          bool = True
    not_deletable:         bool = True

class FolderRoutingEntry(BaseModel):
    """Per FR-77 Type-2 routing — single (ingress_folder → item_no) mapping.

    Naming: `ingress_folder` = inbound NSD-side folder where the document arrives
    (HILDA-PC local path under TGGroupBase.ingress_nsd). Distinct from `target_folder`
    on DeliveryItemBase which refers to OUTBOUND customer-portal upload destination
    (carrier-facing upload path per FR-73 / FR-19). Inbound and outbound folder
    namespaces must never be conflated."""
    ingress_folder: str       # folder path under NSD ingress (e.g. "deliverables/q3/test_reports") — INBOUND, HILDA-PC side
    item_no:        int       # FK → DeliveryItemBase.item_no within the milestone
    routing_notes:  str | None = None   # optional per-mapping note for TPM

class TGFolderRouting(BaseModel):
    """Per FR-77 — TG-scoped folder routing table. One row per (milestone_id, tg_name).
    Loaded into routing pipeline cache (FR-52 step 3) at tracker creation; refreshed
    on TGGroupBase update. Empty list → folder routing disabled for this TG."""
    milestone_id:   str
    tg_name:        str
    entries:        list[FolderRoutingEntry] = []

class TagCatalogEntry(BaseModel):
    """Per FR-82 (revised 2026-06-05) — single tag in the customer's tag catalog.
    Validated against DeliveryItemBase.item_description on ingest; unknown tags
    raise TSC-W003 (warning, not error)."""
    tag:          str       # canonical tag string (e.g. "MUST_HAVE", "RegA", "Confidential")
    description:  str | None = None
    color:        str | None = None   # optional UI hint (hex code) for dashboard chips

class AutomationRuleBase(BaseModel):
    rule_id:           str
    rule_name:         str
    scope:             RuleScope
    scope_id:          str | None   # customer_id or device_id; None if Global
    trigger_event:     str   # validated against RuleTriggerRegistry per FR-28 (revised 2026-06-05)
    trigger_sub_event: str | None = None  # validated against RuleSubTriggerType when trigger_event == "ItemModified"; None otherwise
    trigger_condition: dict[str, Any]   # structured; rule_engine owns interpretation
    action_type:       str   # validated against RuleActionRegistry per FR-29 (revised 2026-06-05); seeded by RuleActionType enum
    action_parameters: dict[str, Any]   # action-specific; rule_engine owns interpretation
    priority:          int = 100
    is_active:         bool = True
```

### CustomerSchema (ingestor → runtime contract, anchors D-018)

```python
class ColumnMapping(BaseModel):
    source:    str           # Excel column header (customer-specific)
    canonical: str           # canonical field name on the entity base model
    col_type:  Literal["str", "int", "float", "bool", "date", "email", "enum"]
    required:  bool = False
    format:    str | None = None       # e.g. "MM/DD/YYYY" for dates
    enum_values: list[str] | None = None  # for col_type == "enum"

class EntitySchemaConfig(BaseModel):
    entity:     Literal["device", "milestone", "delivery_item"]   # no deliverable level ([D-028])
    header_row: int = 1                # which Excel row holds column headers
    columns:    list[ColumnMapping]

class CustomerSchema(BaseModel):
    """Output contract of template_schema_ingestor; input contract for tracker, dashboard,
    sharepoint_integration, and rule_engine at startup. Stored as
    customizations/template_schemas/<customer_slug>/schema.yaml (YAML-serialized).
    """
    customer_slug:    str             # [a-zA-Z0-9_-]+
    schema_version:   int             # bumped on each ingestor re-run
    entity_hierarchy: list[EntitySchemaConfig]
    sp_list_mappings: dict[str, str]  # canonical_field → SharePoint internal column name
    # (populated by template_schema_ingestor; consumed by sharepoint_integration)

    @classmethod
    def load(cls, customer_slug: str, base_path: Path) -> "CustomerSchema":
        """Loads and validates schema.yaml from customizations/template_schemas/<slug>/."""

    def to_yaml(self) -> str:
        """Round-trips cleanly; used by ingestor --dry-run and --mode infer output."""
```

---

## Invariants

- **No IO.** No file reads, no network calls, no SharePoint access. Pure data model — validators and serialization only. IO belongs to the importing module.
- **Registry, not closed enum, for extensible fields.** `DeliveryState`, `ItemType`, `TrackingModality`, `CustomerDeliveryModality`, `DocType`, `RuleActionType`, `RuleTriggerType`, and the tag catalog are validated against mutable registries at runtime, not against the closed enum. The enum values seed the registry at import time. Per FR-28 / FR-29 (revised 2026-06-05), new triggers and actions are added via customer config without code change.
- **Alignment invariant** (per FR-86 + `[D-053]` impl note 2026-06-08): `TEST_TECH_WAIVER_REPORT` items hold doc_type ∈ `{test_report, tech_report, waiver}`; `COMPLIANCE_CERTIFICATION_RELEASE_NOTES` items hold doc_type = `compliance_certification_release_notes`; `DEFAULT` items hold any of the 5 doc_types; `CONFIRMATION` items hold no documents. Misaligned (item_type, doc_type) pairs at ingest land on the `staged-not-classified` NSD path per FR-86 for TPM SP UI resolution per FR-87. **doc_type is NOT derived from item.item_type** — supersedes the withdrawn `[D-053]` impl note 2026-05-28b "1:1 derivation" framing.
- **`item_description` is nested JSON list-of-lists per FR-82 architect lock 2026-06-20** (supersedes the 2026-06-05 comma-separated string framing). Outer list = one entry per expected document (length must equal `doc_count`; mismatch raises `TSC-W008`). Inner list = tag-set for that document — validated against customer's tag catalog (`TagCatalogEntry` registry); unknown tags raise `TSC-W003` (warning). **Subset-overlap detection** across rows in the same `(milestone_id, tg_name)`: if any item's tag-set is a subset of another's tag-set in the same TG, raises `TSC-W007` (routing-ambiguity warning). Tag mutations fire `ItemModified.TagsModified` → `PropagateTagsToActiveTrackers` per FR-82 with narrow propagation scope `(customer_id, tg_name, item_no)`.
- **`path_id` is immutable after creation** (renamed from `path_slug` 2026-06-21 per session slug→id rename). `make_slug()` is called once at entity-creation; subsequent renames do not recompute it. The stored value is authoritative. Anchors `[D-013]`.
- **`force_tracking_enabled` is sole per-item tracking gate per FR-81 option (a) lock 2026-06-20**. SP BOOL column-default = `true`. No per-TG `tracking_enabled` exists. Default WI is the one explicit exception (force_tracking_enabled = `false` hardcoded per FR-78).
- **TG fields are denormalized per-work-item per [D-051] + architect lock 2026-06-21**. `TGGroupBase` model dropped; all TG-level fields (`tg_email_group_alias`, `tg_owner_*`, `corp_id_list`, `ingress_nsd`, `folder_routing_enabled`, etc.) live on `DeliveryItemBase` directly. TG-grouping semantics preserved via shared `tg_name` across items in the same milestone.
- **Owner identity is 4-field free-form text per FR-88 + [D-080] + [D-086]**. Fields: `owner_corp_usa_email` (preferred outreach), `owner_corp_email` (fallback), `owner_corp_id` (corp directory ID; PLM grouping key per FR-5 + [D-035]), `owner_name`. No AD validation; TPM populates in SP UI between setup_milestone and Start Collection per Ph-1 setup-window lock 2026-06-20 (template.yaml all 4 null).
- **`delivery_path_template` lives at customer level per architect lock 2026-06-21**. Combined with `customer_delivery_info` (base URL, denormalized per-item) + per-item `target_folder` (sub-path under milestone HOME) at FR-77 dispatch time. Composition: `customer_delivery_info + "/" + delivery_path_template (expanded with {project_model} + {milestone_name}) + "/" + target_folder`.
- **`DeliveryItemBase.item_no` is immutable per item lifetime** (added 2026-06-12 per SP UI engineer review resolution). Once assigned at template instantiation, the value never changes for the life of the work-item — reorder operations in SP UI MUST NOT mutate it; user-visible reordering is a `sort_order` concern, not `item_no`. This invariant is referential-integrity-critical for FR-77 `FolderRoutingEntry.item_no` (line 349; ingress-folder → item routing), `storage.TGFolderRoutingRow.item_no`, dashboard rendering, and SP-side denormalized DI rows per D-DRAFT-Y (2026-06-12). FR-83 work-item reassignment changes `DocumentItemAssociation` rows, not `item_no` on either source or target item. Enforcement: Pydantic model is frozen=True or item_no field is set at construction-only (Ph-1 discipline; runtime enforcement deferred to dev phase). Resolves STATUS line 316 "ItemNumber-stability schema".
- **`CustomerSchema` is the only cross-module data contract for customer-specific configuration.** No module reads `customizations/` YAML directly except via `CustomerSchema.load()`. This makes the YAML format a versioned API.
- **No proprietary content.** Base models hold structural metadata only (field names, types, states, dates). No customer test report content, tech report prose, or waiver text ever appears in this module. Anchors NFR-2.
- **Confirmation items MUST have `no_customer_upload=True`** (added 2026-06-10 per tracker/MODULE.md invariant + `[D-053]` Confirmation semantic). Confirmation (`ItemType.CONFIRMATION`) is owner-Yes/No-confirmation by nature — there is no document to upload to customer/carrier. Enforced via Pydantic `model_validator` on `DeliveryItemBase` at template load: if `item_type == "confirmation"` AND `no_customer_upload == False`, raises `TSC-W004` (warning, not error — customer may extend `ItemTypeRegistry` with non-Confirmation values that legitimately collide with the string `"confirmation"`; the invariant binds only the canonical enum value). Customer-extensible registry doesn't bypass this — the invariant is enforced at the model layer regardless of `ItemTypeRegistry` extensions.

---

## Key choices

- **`[D-014]`** — two separate authoring paths (SP-UI + Excel); both produce `CustomerSchema`-conformant data; this module defines the target contract both paths converge on.
- **`[D-018]`** — three ingestor input modes (schema-file / row-offset / infer); `CustomerSchema` is the common output; `to_yaml()` enables the infer-once → commit → schema-file production workflow.
- **`[D-013]`** — slug convention (`path_slug` field on every entity, `make_slug()` + `validate_slug()` owned here as the cross-cutting convention).
- **Extensibility via registry (FR-7 NFR-14)** — closed Python enums would require code changes for new item types or delivery states. Registry pattern allows config-file extension. The closed enum values serve as seeds and documentation; the registry is the runtime authority.
- **`sp_list_mappings` in `CustomerSchema`** — SharePoint internal column names are customer-deployment-specific. Embedding them in `CustomerSchema` (rather than in `customizations/sharepoint_config/`) co-locates the per-customer SP mapping with the rest of the customer schema, keeping all customer-specific config in one place.
- **`[D-053]` impl note 2026-06-08 — `doc_type` is classified per content, NOT derived from `item.item_type`** (supersedes the prior `[D-053]` impl note 2026-05-28b "1:1 derivation" framing). ItemType enum collapses 7→4 (`Confirmation`, `TEST_TECH_WAIVER_REPORT`, `COMPLIANCE_CERTIFICATION_RELEASE_NOTES`, `Default`); DocType enum expands 4→5 (rename `default` → `compliance_certification_release_notes`; add `unresolved`); alignment invariant per FR-86 enforced via storage matrix. `CLASSIFY_DOC_TYPE` TaskKind is restored in `llm/MODULE.md` per FR-85 Step 2 ladder (un-revert of the 2026-05-28b removal). Legacy ItemType values (`TestReport` / `TechReport` / `Waiver` / `CompletionPct` / `SoftwareBinary`) removed from the core enum — `TestReport`/`TechReport`/`Waiver` collapsed into `TEST_TECH_WAIVER_REPORT` per the bundled-item-type model; `CompletionPct`/`SoftwareBinary` deemed vestigial (re-addable per customer via `ItemTypeRegistry.extend_registry()` if needed).

---

## Non-goals

- Not a persistence layer — no DB queries, no SharePoint reads/writes. `sharepoint_integration` owns that.
- Not a template rendering engine — the `tracker` module instantiates `CustomerTemplateBase` into concrete `DeliveryItemBase` rows.
- Not an Excel parser — `template_schema_ingestor` and `tracker` (for Excel import) own parsing. This module only defines what a valid parse result looks like.
- Not a rule evaluator — `AutomationRuleBase.trigger_condition` and `action_parameters` are opaque `dict[str, Any]` here; `rule_engine` owns their interpretation.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate` (TSC error codes registered in `error_codes.py`).

---

## Depended on by

All runtime modules that touch entity data: `sharepoint_integration`, `storage`, `credential_service`, `tracker`, `email_service`, `issue_tracker`, `messenger`, `customer_adapter`, `test_report`, `rule_engine`, `llm`, `workflow_engine`, `dashboard`. Build-time: `template_schema_ingestor`.

---

## Test interface

```
python -m core.src.template_schema.template_schema_cli --diagnostic
```
Loads all `CustomerSchema` files from `customizations/template_schemas/*/schema.yaml`, validates each against the `CustomerSchema` model, and emits a `TSC-RPT` record:
```
RPT|TSC|run-00001|2026-05-04T10:00:00Z|customers_found=1|schemas_valid=1|schemas_invalid=0|extension_values=0
```

```
python -m core.src.template_schema.template_schema_cli --validate --customer <slug>
```
Validates one customer schema and emits a `TSC-QC` record:
```
QC|TSC|run-00001|2026-05-04T10:00:00Z|customer=carrier_alpha|entity_count=1|columns_mapped=8|required_fields_covered=true|sp_mappings_present=true|result=OK
```

No `--mock` or `--dry-run` — no side effects. Reads `customizations/` YAML only.

**Error codes (TSC prefix — registered in `diagnostics/error_codes.py`):**
```
TSC-E001  Schema validation failed for customer '{customer}': {reason}
TSC-E002  Required canonical field '{field}' missing in customer schema '{customer}'
TSC-E003  Unknown entity type '{entity}' in customer schema '{customer}'
TSC-E004  Invalid path_slug '{value}': must match [a-zA-Z0-9_-]+
TSC-W001  Customer schema version mismatch for '{customer}': schema v{schema_ver} vs module v{module_ver}
TSC-W002  Optional canonical field '{field}' unmapped in customer schema '{customer}'
TSC-W003  Unknown tag '{tag}' in item_description for item '{item_id}' — not registered in customer tag catalog (FR-82)
TSC-W004  Confirmation item '{item_id}' has `no_customer_upload=False` — Confirmation items MUST have no_customer_upload=True per [D-053] + tracker MODULE.md invariant (added 2026-06-10)
TSC-W005  TG-field divergence — items in milestone '{milestone}' sharing tg_name '{tg_name}' have inconsistent TG-denormalized fields (added 2026-06-21 cascade)
TSC-W006  CustomerJIRA-only role-collapse violation — item '{item_id}' has owner_corp_email != PM.Work_email (informational; non-blocking per FR-25 (b) lock 2026-06-20)
TSC-W007  Routing tag-set subset-overlap — item '{item_id}' tag-set is subset of item '{other_id}' tag-set in same (milestone, tg_name); routing is ambiguous per FR-82 architect lock 2026-06-20
TSC-W008  doc_count consistency violation — item '{item_id}' has doc_count={doc_count} but len(item_description)={tag_set_count} per FR-82 architect lock 2026-06-20
```

**QC template** (`TSC:customer_schema` — registered in `diagnostics/qc.py`):
```
Fields: entity_count (int), columns_mapped (int), required_fields_covered (bool),
        sp_mappings_present (bool), result (enum: OK / WARN / FAIL)
```

---

<!-- BEGIN:STRUCTURE -->

- `AutomationRuleBase` — class — pub
- `ColumnMapping` — class — pub
- `CustomerDeliveryModality` — class — pub — Per-item delivery modality to the customer/carrier. Phase-scoped values per
- `CustomerSchema` — class — pub — Output contract of template_schema_ingestor; input contract for runtime modules.
- `CustomerTemplateBase` — class — pub — Top-level customer template per FR-39/FR-40 + [D-091] YAML structure.
- `DefaultWorkItemConfig` — class — pub — Per FR-78 + [D-053] — configures the auto-instantiated default work-item
- `DeliveryItemBase` — class — pub — Per-work-item canonical fields. 2026-06-21 architect cascade applied:
- `DeliveryState` — class — pub — Canonical 11-value enum per FR-7. The active happy-path 8-state traversal is
- `DeviceBase` — class — pub — Per-device canonical fields. NOTE: `assigned_pm_id` here is the template-time
- `DocType` — class — pub — Five doc_type values per [D-053] impl note 2026-06-08 (supersedes the prior
- `EntitySchemaConfig` — class — pub
- `FolderRoutingEntry` — class — pub — Per FR-77 Type-2 routing — single (ingress_folder → item_no) mapping.
- `IngestSource` — class — pub — Per FR-13 + [D-039] — recorded in document index for every classified document.
- `ItemType` — class — pub — Four core item_types per [D-053] impl note 2026-06-08 (supersedes the prior
- `MilestoneBase` — class — pub — Per-milestone canonical fields. Milestones SP list is GLOBAL per architect lock
- `MilestoneStatus` — class — pub — Per FR-2 / FR-7 milestone lifecycle.
- `RuleActionType` — class — pub — Per FR-28 / FR-29 (rewritten 2026-06-05). Extensible via RuleActionRegistry —
- `RuleScope` — class — pub — Per FR-30 — AutomationRule scope ladder (Device → Customer → Global).
- `RuleSubTriggerType` — class — pub — Sub-triggers under ItemModified per FR-28.
- `RuleTriggerType` — class — pub — Per FR-28 trigger taxonomy (revised 2026-06-05). Extensible via
- `TGFolderRouting` — class — pub — Per FR-77 — TG-scoped folder routing table. One row per (milestone_id, tg_name).
- `TagCatalogEntry` — class — pub — Per FR-82 — single tag in the customer's tag catalog.
- `TestReportClassification` — class — pub — final | interim classifier output. Anchors FR-46.
- `TestReportItemStatus` — class — pub — Canonical per-item status vocabulary for test reports. Anchors [D-011] FR-16.
- `TrackingModality` — class — pub — Multi-value per DeliveryItem (stored as a list) per [D-037] (2026-05-13).
- `extend_registry` — func — pub — Idempotent — duplicates silently ignored.
- `make_slug` — func — pub — Deterministic: lower-case, replace non-alphanumeric with '-', truncate.
- `validate_in_registry` — func — pub
- `validate_slug` — func — pub — Validator for path_slug fields. Raises TSC-E004 if pattern fails.

<!-- END:STRUCTURE -->
