# Module: template_schema

**Purpose**: Canonical data model for HILDA's entity hierarchy — Device / Milestone / DeliveryItem (grouped by tg_name) + TG-group metadata (per `(milestone_id, tg_name)`) — and the contract types shared across all runtime modules. Defines Pydantic base models, canonical enums, extensibility registries, slug conventions, and the `CustomerSchema` output contract that `template_schema_ingestor` produces and all runtime modules consume. Serves FR-1–7, FR-39–41, FR-66, FR-70, FR-71, FR-77 (folder routing config types), FR-78 (default work-item config), FR-79 (multi-item association keys), FR-80 (`no_customer_upload`), FR-81 (`tracking_enabled` / `force_tracking_enabled`), FR-82 (routing tag catalog + validation), FR-84 (TG-level data for `sp_alert_parser`), NFR-14, and anchors `[D-014]` `[D-018]` `[D-028]` `[D-037]` `[D-046]` `[D-049]` `[D-051]` `[D-053]` `[D-054]`.

*This module is pure data model — no IO, no SharePoint, no network. Runtime modules extend these base types with persistence-layer fields (SP List IDs, DB columns) in their own models.*

---

## Public surface

### Enums

```python
class DeliveryState(str, Enum):
    """Extensible via DeliveryStateRegistry — new values added through config, not code.
    Full 10-state enum per FR-7 (rewritten 2026-05-15)."""
    OPEN                 = "Open"                 # initial; set at tracker creation
    OUTREACH_SENT        = "OutreachSent"         # initial outreach dispatched per FR-9
    DOCUMENT_RECEIVED    = "DocumentReceived"     # document arrived via any ingest channel
    UNDER_PM_REVIEW      = "UnderPMReview"        # active TPM review gate per FR-56
    OWNER_CLOSED         = "OwnerClosed"          # owner confirmed done; transient — forks per FR-7 (D-048 multi-rev selection)
    DELAYED              = "Delayed"              # owner-reported delay; transient
    BLOCKED              = "Blocked"              # owner-reported blocker; transient
    READY_FOR_SUBMISSION = "ReadyForSubmission"   # PM approved per FR-28 PMApproval trigger
    SUBMITTED_TO_CUSTOMER = "SubmittedToCustomer" # submission package dispatched per FR-18
    CLOSED               = "Closed"               # manually set per FR-14 / FR-64; automated transition deferred per DEF-20

class ItemType(str, Enum):
    """item_type drives the 1:1 derivation of doc_type per [D-053] item homogeneity invariant
    (TestReport → test_report, TechReport → tech_report, Waiver → waiver, DEFAULT → default,
    others → no document pipeline). Extensible via ItemTypeRegistry."""
    CONFIRMATION    = "Confirmation (Yes/No)"   # owner reply closes item; no artifact required
    COMPLETION_PCT  = "CompletionPct"
    TEST_REPORT     = "TestReport"
    SOFTWARE_BINARY = "SoftwareBinary"
    TECH_REPORT     = "TechReport"
    WAIVER          = "Waiver"
    DEFAULT         = "Default"                  # per FR-78 + [D-053] — used for the auto-instantiated
                                                  # default work-item per milestone (tg_name = "_unrouted";
                                                  # sort_order = max+1; not editable; not deletable;
                                                  # immutable doc_count = 0, review_required = false)

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
    """Per FR-7. Used as folder organizer in NSD path `<doc_type_slug>/<doc_id_slug>/revN/` per `[D-013]`.
    Derived 1:1 from `item.item_type` per `[D-053]` (no runtime doc_type LLM classification —
    CLASSIFY_DOC_TYPE TaskKind removed per `[D-052]` impl note 2026-05-28b)."""
    TEST_REPORT = "test_report"    # triggers FR-16 parser + FR-53 LLM review when review_required=true
    TECH_REPORT = "tech_report"    # triggers FR-53 LLM review when review_required=true
    WAIVER      = "waiver"         # triggers FR-53 LLM review when review_required=true
    DEFAULT     = "default"        # per FR-7 amendment + [D-053] — catch-all for compliance docs,
                                    # certification docs, release notes, and any other non-{test_report,
                                    # tech_report, waiver} documents; FR-16/FR-46/FR-53 do NOT fire
                                    # for this doc_type

class CustomerDeliveryModality(str, Enum):
    NONE                   = "None"
    EMAIL                  = "Email"
    CUSTOMER_TRACKING_SYS  = "CustomerTrackingSystem"
    FILE_STORAGE           = "FileStorage"

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
    device_id:          str
    device_name:        str
    customer_id:        str
    assigned_pm_id:     str
    status:             str   # validated against DeliveryStateRegistry equivalent
    template_id:        str | None
    path_slug:          str   # [a-zA-Z0-9_-]+, immutable after creation
    target_launch_date: date | None

class MilestoneBase(BaseModel):
    milestone_id:   str
    device_id:      str
    milestone_name: str
    sort_order:     int
    target_date:    date | None
    status:         MilestoneStatus
    email_cc_list:  list[dict] | None   # [{name, email, role}]; applied to all emails in this milestone
    path_slug:      str
    # Per-milestone fields added 2026-06-05 (FR-78 / [D-053]):
    default_work_item_config: dict | None = None  # per FR-78 — DefaultWorkItemConfig override for this milestone (serialized DefaultWorkItemConfig); None → use tracker-wide default from CustomerTemplateBase. Exactly one default work-item per milestone (NOT per TG); TG-of-document is recorded on DocumentIndexRow.inferred_tg_name.

class DeliveryItemBase(BaseModel):
    item_id:                         str
    item_no:                         int        # sequential within milestone; unique on (milestone_id, item_no)
    milestone_id:                    str        # parent milestone — no Deliverable level ([D-028])
    tg_name:                         str | None # validated against TGNameRegistry; foreign-key-like label to TGGroupBase per [D-049] / [D-051]
    item_name:                       str
    item_description:                str | None   # per FR-82 (revised 2026-06-05) — comma-separated tag list (catalog-validated); replaces free-form description. Empty/null allowed. Validator raises TSC-W003 on unknown tags (warning, not error — tag catalog is customer-extensible). Tags propagate to active trackers via ItemModified.TagsModified → PropagateTagsToActiveTrackers per FR-82.
    delivery_state:                  str   # validated against DeliveryStateRegistry
    expected_completion_date:        date | None
    actual_completion_date:          date | None  # auto-set when delivery_state → OwnerClosed (per FR-15 update)
    item_type:                       str   # validated against ItemTypeRegistry
    owner_name:                      str | None
    owner_email:                     str | None
    tracking_modality:               list[str]  # MULTI-VALUE per [D-037] — list of TrackingModality values; validated against TrackingModalityRegistry
    actual_item_info:                str | None  # PLM issue URL for (owner × milestone) pair per FR-57, set on first document arrival
    plm_id:                          str | None  # PLM system ID (e.g. Jira-style); one issue per (owner × milestone) per [D-035] / FR-8
    handset:                         bool = False  # form factor applicability flags (static, from template)
    tablet:                          bool = False
    wearable:                        bool = False
    mr:                              bool = False
    hmr_smr:                         bool = False
    customer_delivery_modality:      str   # validated against CustomerDeliveryModalityRegistry
    customer_delivery_info:          str | None
    customer_delivery_credential_id: str | None
    owner_status_note:               str | None  # latest interim owner update; auto-populated from inbound
    comment:                         str | None
    last_updated:                    datetime
    last_owner_contacted:            datetime | None
    sort_order:                      int
    path_slug:                       str
    # Per-DeliveryItem fields added 2026-05-15+ (FR-2 / FR-7 / FR-53 / FR-70):
    doc_count:                       int = 1     # per FR-7; number of test_report docs required before DocumentReceived; 0 for Confirmation items
    review_required:                 bool = False # per FR-2 / FR-53; gates LLM quality review (FR-53); always False for Confirmation items
    review_status:                   str   # per FR-53 / FR-60; enum: pending | complete | not_required
    item_completion_pct:             int = 0     # per FR-70; document-review completion percentage; computed field
    email_cc_list:                   list[dict] | None = None  # per FR-2 (per-item override); pre-populated from per-TG default_cc_list at tracker creation; array of {name, email, role}
    milestone_gating:                bool = False  # per SP alert sample 2026-05-26 (sharepoint/REQUIREMENTS.md §2.4); does this item gate milestone closure?
    # Per-DeliveryItem fields added 2026-06-05 ([D-053] / [D-054] / FR-77 / FR-78):
    no_customer_upload:              bool = False  # per [D-054] — when True, this item is excluded from customer-portal upload (e.g. internal-only deliverable, owner-confidential)
    force_tracking_enabled:          bool | None = None  # per Ph-2 — overrides per-TG tracking_enabled when set; None → inherit TGGroupBase.tracking_enabled
    ingress_folder:                  str | None = None   # per FR-77 Type-2 routing — INBOUND folder path under NSD ingress (HILDA-PC side, scoped by TGGroupBase.ingress_nsd) that maps to this item; consumed by sp_alert_parser / email_service routing pipeline (FR-52 step 3). Distinct from `target_folder` (outbound customer-portal upload destination, FR-73 / FR-19).
    target_folder:                   str | None = None   # OUTBOUND — customer-portal upload destination path (carrier-facing) per FR-73 / FR-19; consumed by customer_adapter on submission. Distinct from `ingress_folder` (inbound NSD-side path).

class TGGroupBase(BaseModel):
    """Per-TG-group metadata per `[D-049]` (ODF) + `[D-051]` (TGGroups SP list normalization).
    One row per `(milestone_id, tg_name)` — applies to all DeliveryItems sharing that tg_name in the milestone.
    Source data: `customizations/template_schemas/<customer_slug>/tg_groups.yaml` per FR-2 / FR-71.
    Runtime storage: TGGroups SP list per `sharepoint/REQUIREMENTS.md §2.8`."""
    tg_group_id:        str
    milestone_id:       str   # FK → MilestoneBase
    tg_name:            str   # validated against TGNameRegistry; matches DeliveryItemBase.tg_name
    tg_owner_name:      str | None  # TG coordinator (delivery-engineer assignment authority); distinct from per-item DeliveryItemBase.owner_name
    tg_owner_email:     str | None
    email_group_alias:  str | None  # TG corporate email distribution alias (e.g. "ims.corp@corp.com"); when set, replaces individual owner_email for TG outreach per FR-2 / FR-9
    corp_id_list:       list[str] | None  # complete corp-ID list of TG members; when set, replaces individual owner corp-ID for messenger escalation per FR-10
    default_cc_list:    list[dict] | None  # per-TG default CC list; pre-populates per-item DeliveryItemBase.email_cc_list at tracker creation
    # Per-TG fields added 2026-06-05 (FR-77 / FR-78 / [D-053] / [D-054]):
    ingress_nsd:               Literal["NSD1", "NSD2"] = "NSD1"  # per [D-013] dual-NSD topology; which ingress NSD this TG's documents arrive on
    folder_routing_enabled:    bool = False  # per FR-77 Type-2 routing — when True, route by ingress_folder mapping (TGFolderRouting); when False, work-item routing only
    tracking_enabled:          bool = True   # per Ph-2 — when False, HILDA does not track items in this TG (force_tracking_enabled per-item override)
    # Unique constraint: (milestone_id, tg_name) — enforced SP-side per [D-051]
    # Note: default work-item is milestone-scoped (not TG-scoped) — config lives on MilestoneBase.default_work_item_config per FR-78

class CustomerTemplateBase(BaseModel):
    template_id:      str
    customer_id:      str
    template_name:    str
    template_version: int
    # template_data is the full instantiated hierarchy — typed as nested lists of base models
    milestones: list[MilestoneBase]
    # DeliveryItems nested within milestones directly — no Deliverable level ([D-028])
    is_active: bool = True

class DefaultWorkItemConfig(BaseModel):
    """Per FR-78 + [D-053] — configures the auto-instantiated default work-item per MILESTONE.
    Exactly one default work-item per milestone (NOT per TG). The default work-item is a
    milestone-level catch-all: no real tg_name, no owner, no TG-scoped behavior. Excluded
    from FR-74 collection-phase-closure threshold (would never fire otherwise).

    Routing model: when the FR-52 pipeline cannot resolve a specific work-item, the document
    lands here. The document's TG IS knowable from the inbound channel (NSD ingress folder
    per TGGroupBase.ingress_nsd; email sender via email_group_alias / owner_email lookup;
    PLM-id via DeliveryItemBase.plm_id reverse-lookup) and is recorded on the document
    record as `DocumentIndexRow.inferred_tg_name` (storage module), NOT on the default
    work-item. FR-83 TPM-manual reassignment uses inferred_tg_name to shortlist candidate
    work-items within that TG."""
    tg_name:               Literal["_unrouted"] = "_unrouted"   # sentinel — default work-item has no real TG; TG-of-document lives on DocumentIndexRow.inferred_tg_name
    item_name:             str = "Unrouted Documents"
    item_type:             Literal["Default"] = "Default"   # ItemType.DEFAULT per [D-053]
    sort_order_strategy:   Literal["max_plus_1", "fixed"] = "max_plus_1"
    sort_order_fixed:      int | None = None   # used when strategy == "fixed"
    not_editable:          bool = True   # immutable doc_count=0, review_required=False, item_type=Default
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
- **`item_description` is a comma-separated tag list per FR-82 (revised 2026-06-05).** Validator splits on comma, strips whitespace, and checks each tag against the customer's tag catalog (TagCatalogEntry registry). Unknown tags raise `TSC-W003` (warning, not error — catalog is customer-extensible). Tag mutations fire `ItemModified.TagsModified` → `PropagateTagsToActiveTrackers` per FR-82 with narrow propagation scope `(customer_id, tg_name, item_no)`.
- **`path_slug` is immutable after creation.** `make_slug()` is called once at entity-creation; subsequent renames do not recompute it. The stored value is authoritative. Anchors `[D-013]`.
- **`CustomerSchema` is the only cross-module data contract for customer-specific configuration.** No module reads `customizations/` YAML directly except via `CustomerSchema.load()`. This makes the YAML format a versioned API.
- **No proprietary content.** Base models hold structural metadata only (field names, types, states, dates). No customer test report content, tech report prose, or waiver text ever appears in this module. Anchors NFR-2.

---

## Key choices

- **`[D-014]`** — two separate authoring paths (SP-UI + Excel); both produce `CustomerSchema`-conformant data; this module defines the target contract both paths converge on.
- **`[D-018]`** — three ingestor input modes (schema-file / row-offset / infer); `CustomerSchema` is the common output; `to_yaml()` enables the infer-once → commit → schema-file production workflow.
- **`[D-013]`** — slug convention (`path_slug` field on every entity, `make_slug()` + `validate_slug()` owned here as the cross-cutting convention).
- **Extensibility via registry (FR-7 NFR-14)** — closed Python enums would require code changes for new item types or delivery states. Registry pattern allows config-file extension. The closed enum values serve as seeds and documentation; the registry is the runtime authority.
- **`sp_list_mappings` in `CustomerSchema`** — SharePoint internal column names are customer-deployment-specific. Embedding them in `CustomerSchema` (rather than in `customizations/sharepoint_config/`) co-locates the per-customer SP mapping with the rest of the customer schema, keeping all customer-specific config in one place.

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
```

**QC template** (`TSC:customer_schema` — registered in `diagnostics/qc.py`):
```
Fields: entity_count (int), columns_mapped (int), required_fields_covered (bool),
        sp_mappings_present (bool), result (enum: OK / WARN / FAIL)
```

---

<!-- BEGIN:STRUCTURE -->
<!-- END:STRUCTURE -->
