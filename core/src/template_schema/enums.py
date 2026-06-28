"""Canonical enums for HILDA's entity hierarchy.

Anchors FR-7, FR-28, FR-29, NFR-14, [D-011], [D-052], [D-053], [D-054], [D-066].
"""
from __future__ import annotations

from enum import Enum


class DeliveryState(str, Enum):
    """Canonical 11-value enum per FR-7. The active happy-path 8-state traversal is
    Open → OutreachSent → DocumentReceived → OwnerClosed → UnderPMReview →
    ReadyForSubmission → SubmittedToCustomer → Closed. Not Started precedes Open
    (TPM template-loaded but tracking not active). Delayed and Blocked are off-path
    holding states reachable from OutreachSent onwards (not from Open — owner cannot
    report status before outreach). Extensible via DeliveryStateRegistry.
    """

    # Per architect direction 2026-06-28 [D-138] supersedes [D-124] (α): enum value
    # strings match SP UI Choice column display. SP UI uses PascalCase-no-space for
    # multi-word values (OutreachSent, DocumentReceived, OwnerClosed, UnderPMReview,
    # ReadyForSubmission, SubmittedToCustomer) and single-word/two-word-natural for
    # the rest (Not Started, Open, Closed, Delayed, Blocked). D-124's space-bearing
    # convention was based on incorrect reading of SP display; corrected by direct
    # screenshot of SP UI Choice list 2026-06-28. Python attribute names stay
    # SCREAMING_SNAKE_CASE for readability of code.
    NOT_STARTED           = "Not Started"
    OPEN                  = "Open"
    OUTREACH_SENT         = "OutreachSent"
    DOCUMENT_RECEIVED     = "DocumentReceived"
    OWNER_CLOSED          = "OwnerClosed"
    UNDER_PM_REVIEW       = "UnderPMReview"
    READY_FOR_SUBMISSION  = "ReadyForSubmission"
    SUBMITTED_TO_CUSTOMER = "SubmittedToCustomer"
    CLOSED                = "Closed"
    DELAYED               = "Delayed"
    BLOCKED               = "Blocked"


class ItemType(str, Enum):
    """Four core item_types per [D-053] impl note 2026-06-08 (supersedes the prior
    7-value enum + the withdrawn "1:1 derivation of doc_type" framing per [D-053]
    impl note 2026-05-28b). item_type defines the workflow category of a work-item;
    doc_type is classified per content via FR-85 and aligned per FR-86 alignment
    invariant. Extensible via ItemTypeRegistry — legacy values (TestReport /
    TechReport / Waiver / CompletionPct / SoftwareBinary) removed from the core
    enum and may be re-added per-customer via ItemTypeRegistry.extend_registry()
    if backward compatibility is needed.
    """

    # Mixed-case per SP UI engineer lock 2026-06-23: short-label categories
    # (Confirmation, Default) use PascalCase; long-named categories
    # (test_tech_waiver_report, compliance_certification_release_notes) use
    # lowercase_snake_case. Reverts 2026-06-20 all-lowercase rename for the two
    # short labels per SP UI engineer locked SP Choice column values.
    CONFIRMATION                           = "Confirmation"
    TEST_TECH_WAIVER_REPORT                = "test_tech_waiver_report"
    COMPLIANCE_CERTIFICATION_RELEASE_NOTES = "compliance_certification_release_notes"
    DEFAULT                                = "Default"


class TrackingModality(str, Enum):
    """Multi-value per DeliveryItem (stored as a list) per [D-037] (2026-05-13).
    Six Ph-1 values per architect direction 2026-06-23 (HILDA forward-looking;
    SP UI engineer has 5-value SP Choice column with SPUI omitted — to be added
    Ph-2). Valid combinations require at least one status-capable + one
    document-capable modality per FR-7.
    """

    EMAIL                = "Email"                # status + documents via email reply
    CORPORATE_MESSENGER  = "CorporateMessenger"   # status only; Ph-2 inbound per FR-54
    CORPORATE_PLM        = "CorporatePLM"         # documents only; HILDA polls per FR-26
    NETWORK_SHARED_DRIVE = "NetworkSharedDrive"   # documents only; FR-13/FR-55
    CUSTOMER_JIRA        = "CustomerJIRA"         # status only; FR-25
    SP_UI                = "SPUI"                 # status via SP UI button per [D-074]; HILDA forward-looking Ph-1 (SP UI engineer adds Ph-2)


class IngestSource(str, Enum):
    """Per FR-13 + [D-039] — recorded in document index for every classified document."""

    EMAIL                = "Email"
    CORPORATE_PLM        = "CorporatePLM"
    NETWORK_SHARED_DRIVE = "NetworkSharedDrive"
    SHAREPOINT_UI        = "SharePointUI"         # PM-uploaded via SP UI per FR-62 (Ph-2)


class DocType(str, Enum):
    """Five doc_type values per [D-053] impl note 2026-06-08 (supersedes the prior
    4-value enum + the withdrawn "1:1 derivation from item.item_type" framing).
    doc_type is classified per inbound document via the FR-85 2-step ladder
    (filename regex Step 1 + LLM CLASSIFY_DOC_TYPE Step 2 restricted to
    {test_report, tech_report, waiver}). compliance_certification_release_notes
    is detected by filename regex only (Step 1) — LLM never returns this value.
    unresolved is the residual on Step 2 low-confidence + Default-routed-undetermined
    state. Alignment with item_type enforced per FR-86 storage matrix — misaligned
    pairs land on 'staged-not-classified' for TPM resolution per FR-87. Extensible
    via DocTypeRegistry.
    """

    TEST_REPORT                            = "test_report"
    TECH_REPORT                            = "tech_report"
    WAIVER                                 = "waiver"
    COMPLIANCE_CERTIFICATION_RELEASE_NOTES = "compliance_certification_release_notes"
    UNRESOLVED                             = "unresolved"


class CustomerDeliveryModality(str, Enum):
    """Per-item delivery modality to the customer/carrier. Phase-scoped values per
    [D-054]: Ph-1/Ph-2 = Google Drive only; Ph-3+ adds web portal + JIRA customer
    portal (deferred per [D-054]). Customer-extensible via
    CustomerDeliveryModalityRegistry per FR-7 / NFR-14. Legacy value FileStorage
    removed 2026-06-08 as too-generic; customers needing back-compat re-add via
    extend_registry().
    """

    NONE                  = "None"
    EMAIL                 = "Email"
    CUSTOMER_TRACKING_SYS = "CustomerTrackingSystem"
    GOOGLE_DRIVE          = "GoogleDrive"         # Ph-1/Ph-2 per [D-054]
    # Ph-3+ values (deferred per [D-054]):
    # WEB_PORTAL          = "WebPortal"
    # CUSTOMER_JIRA_PORTAL = "CustomerJiraPortal"


class MilestoneStatus(str, Enum):
    """Per FR-2 / FR-7 milestone lifecycle."""

    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    COMPLETED   = "Completed"
    DELAYED     = "Delayed"


class RuleScope(str, Enum):
    """Per FR-30 — AutomationRule scope ladder (Device → Customer → Global)."""

    GLOBAL   = "Global"
    CUSTOMER = "Customer"
    DEVICE   = "Device"


class RuleActionType(str, Enum):
    """Per FR-28 / FR-29 (rewritten 2026-06-05). Extensible via RuleActionRegistry —
    new actions added via config without code change per FR-29 invariant. 18 Ph-1 +
    6 Ph-2 = 24 total values.
    """

    # Ph-1 actions (18)
    SEND_REMINDER                     = "SendReminder"
    ESCALATE                          = "Escalate"
    UPDATE_STATE                      = "UpdateState"
    START_ITEM_COLLECTION             = "StartItemCollection"           # FR-8
    SEND_INITIAL_OUTREACH             = "SendInitialOutreach"           # FR-9
    NOTIFY_NEW_OWNER                  = "NotifyNewOwner"                # FR-28 OwnerReassigned
    TRIGGER_PARSER                    = "TriggerParser"                 # FR-16
    TRIGGER_AI_REVIEW                 = "TriggerAIReview"               # FR-53
    QUEUE_SUBMISSION                  = "QueueSubmission"
    NOTIFY_PM                         = "NotifyPM"
    NOTIFY_HILDA_OPS                  = "NotifyHildaOps"                # FR-75
    INSTANTIATE_DEFAULT_WORK_ITEM     = "InstantiateDefaultWorkItem"    # FR-78
    MILESTONE_STORAGE_CLEANUP         = "MilestoneStorageCleanup"       # FR-76
    HALT_MILESTONE_POLLING            = "HaltMilestonePolling"          # FR-74
    FINAL_SWEEP                       = "FinalSweep"                    # FR-74
    REASSIGN_DOCUMENT_TO_WORKITEM     = "ReassignDocumentToWorkItem"    # FR-83
    PROPAGATE_TAGS_TO_ACTIVE_TRACKERS = "PropagateTagsToActiveTrackers" # FR-82
    PM_APPROVAL                       = "PMApproval"                    # FR-28

    # Ph-2 actions (6)
    CANCEL_OUTSTANDING             = "CancelOutstanding"           # Ph-2
    NOTIFY_OWNER_DOC_COUNT_PENDING = "NotifyOwnerDocCountPending"  # Ph-2
    TRIGGER_VERSION_SELECTION      = "TriggerVersionSelection"     # Ph-2 [D-048] / FR-66
    TRIGGER_PLM_CLEANUP            = "TriggerPLMCleanup"           # Ph-2 FR-67
    TRIGGER_ODF                    = "TriggerODF"                  # Ph-2 [D-049] / FR-71
    SEND_OWNER_ROUTING_QUERY       = "SendOwnerRoutingQuery"       # Ph-2 FR-83


class RuleTriggerType(str, Enum):
    """Per FR-28 trigger taxonomy (revised 2026-06-05). Extensible via
    RuleTriggerRegistry — new triggers added via config. 13 Ph-1 + 2 Ph-2 = 15 total.
    """

    # Ph-1 triggers (13)
    ITEM_CREATED                     = "ItemCreated"
    ITEM_MODIFIED                    = "ItemModified"                  # sub-triggers below
    STATE_CHANGE                     = "StateChange"
    OWNER_STATUS_CONFIRMED           = "OwnerStatusConfirmed"
    LAST_CONTACT_THRESHOLD           = "LastContactThreshold"          # FR-10
    DEADLINE_PROXIMITY               = "DeadlineProximity"             # FR-11
    ATTACHMENT_RECEIVED              = "AttachmentReceived"
    AI_REVIEW_RESULT                 = "AIReviewResult"                # FR-53
    PM_APPROVAL                      = "PMApproval"
    TRACKER_CREATED                  = "TrackerCreated"                # FR-78
    MILESTONE_ALL_CLOSED             = "MilestoneAllClosed"            # FR-76
    COLLECTION_PHASE_CLOSURE_REACHED = "CollectionPhaseClosureReached" # FR-74
    CREDENTIAL_EXPIRED               = "CredentialExpired"             # FR-20

    # Ph-2 triggers (2)
    ITEM_DELETED                  = "ItemDeleted"                    # Ph-2
    UNROUTED_DOCUMENT_ACCUMULATED = "UnroutedDocumentAccumulated"    # Ph-2 — FR-83


class RuleSubTriggerType(str, Enum):
    """Sub-triggers under ItemModified per FR-28."""

    OWNER_REASSIGNED = "OwnerReassigned"   # owner_email field changed
    DEADLINE_MOVED   = "DeadlineMoved"     # expected_completion_date changed
    TAGS_MODIFIED    = "TagsModified"      # item_description tag list changed — fires PropagateTagsToActiveTrackers per FR-82


class TestReportItemStatus(str, Enum):
    """Canonical per-item status vocabulary for test reports. Anchors [D-011] FR-16."""

    PASSED         = "passed"
    FAILED         = "failed"
    NON_APPLICABLE = "non-applicable"
    WAIVED         = "waived"
    NOT_STARTED    = "not-started"


class TestReportClassification(str, Enum):
    """final | interim classifier output. Anchors FR-46."""

    FINAL   = "final"
    INTERIM = "interim"
