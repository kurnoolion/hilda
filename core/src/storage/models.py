"""Canonical storage models per [D-046] — document index, associations, audit, overrides.

DocType / IngestSource / RuleScope are imported from template_schema (canonical
entity-enum home per the Depends-on contract); this module defines only the
storage-side enums (Channel, Direction, NSDPathType, RoutingResolution) and rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import PureWindowsPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from core.src.template_schema import DocType, IngestSource, RuleScope

__all__ = [
    "AutomationRuleOverride",
    "BatchIdempotencyKey",
    "Channel",
    "CommunicationLogRow",
    "Direction",
    "DocumentIndexRow",
    "DocumentItemAssociation",
    "NSDPathType",
    "PLMFanOutTarget",
    "RevisionResolution",
    "RoutingResolution",
    "TGFolderRoutingRow",
    "TagCatalogRow",
]


class Channel(str, Enum):
    """Communication channel for CommunicationLogRow per NFR-6."""

    EMAIL = "Email"
    MESSENGER = "Messenger"
    CORPORATE_PLM = "CorporatePLM"
    NETWORK_SHARED_DRIVE = "NetworkSharedDrive"
    CUSTOMER_JIRA = "CustomerJIRA"
    SHAREPOINT = "SharePoint"


class Direction(str, Enum):
    INBOUND = "Inbound"
    OUTBOUND = "Outbound"


class NSDPathType(str, Enum):
    """Per FR-86 storage matrix — explicit state tracker for which of the 4 NSD path
    types the file currently lives at on a given DocumentItemAssociation row.
    Indexed in Postgres for STR-W007 stale-staged queries + FR-86 state transitions
    triggered by FR-87 SP UI resolution. Per-association state (independent across
    rows for the same file_hash)."""

    CLASSIFIED = "classified"                        # aligned + [D-039] revision-pass
    STAGED_NOT_CLASSIFIED = "staged_not_classified"  # (item_type, doc_type) misaligned; awaits FR-87 step (B)
    STAGED_NOT_REVISION = "staged_not_revision"      # aligned but [D-039] ambiguous; awaits FR-87 step (C)
    UNROUTED = "unrouted"                            # landed on Default work-item; [D-039] skipped at ingest


@dataclass(frozen=True)
class RevisionResolution:
    """FR-87 step (C) revision discriminator — TPM's NEW vs REVISION_OF choice.
    Tagged union (clearer than `Literal["new"] | tuple[Literal["revision_of"], str]`);
    factory methods give grep-able names and self-validating construction."""

    kind: Literal["new", "revision_of"]
    revised_doc_id_slug: str | None = None  # required when kind="revision_of"

    @classmethod
    def new(cls) -> "RevisionResolution":
        return cls(kind="new")

    @classmethod
    def revision_of(cls, doc_id_slug: str) -> "RevisionResolution":
        return cls(kind="revision_of", revised_doc_id_slug=doc_id_slug)

    def __post_init__(self) -> None:
        if self.kind == "revision_of" and not self.revised_doc_id_slug:
            raise ValueError("revision_of resolution requires revised_doc_id_slug")
        if self.kind == "new" and self.revised_doc_id_slug:
            raise ValueError("new resolution must not provide revised_doc_id_slug")


class RoutingResolution(str, Enum):
    """Per FR-52 5-step routing pipeline — which step resolved the document's routing."""

    SUBSTRING_MATCH = "SubstringMatch"          # step 1 — strict substring on filename vs item tags
    FUZZY_MATCH = "FuzzyMatch"                  # step 2 — rapidfuzz above threshold
    FOLDER_ROUTING = "FolderRouting"            # step 3 — FR-77 Type-2 ingress_folder mapping
    LLM_ROUTE_ATTACHMENT = "LLMRouteAttachment" # step 4 — LLM TaskKind.ROUTE_ATTACHMENT
    STAGED_DEFAULT = "StagedDefault"            # step 5 / FR-78 — milestone default work-item
    TPM_REASSIGNED = "TPMReassigned"            # FR-83 — TPM-manual reassignment


class DocumentIndexRow(BaseModel):
    """One row per physical document (file-centric per FR-79 revised). PK = file_hash.

    Holds file-content + per-ingest properties only; per-(file, item) association data
    lives on DocumentItemAssociation. Secondary unique constraint
    (milestone_id, doc_id_slug, rev_number) preserves the FR-57 lookup contract.
    doc_id_slug/rev_number are nullable while the file sits on a staged path
    (FR-86 staged-not-revision-determined) awaiting FR-87 resolution.
    """

    model_config = ConfigDict(frozen=False)

    file_hash: str                          # PK — SHA-256 per [D-039] Step 0
    milestone_id: str                       # FR-79 single-milestone invariant keeps this stable
    doc_type: DocType                       # 5-value enum; UNRESOLVED is a sentinel value, never None
    doc_id_slug: str | None = None          # stable across revisions per FR-57; NULL while staged
    rev_number: int | None = None           # 1 new, ≥2 revisions per FR-17; NULL while staged
    ingest_source: IngestSource             # channel of FIRST ingest (first-write-wins)
    original_filename: str
    first_page_excerpt: str = ""            # for [D-039] Tier-2 LLM comparison
    is_final: bool = False                  # FR-66; auto-true in Ph-1 single-revision flow
    parser_result: dict[str, Any] | None = None       # FR-16; test_report only
    llm_review_findings: dict[str, Any] | None = None # FR-53; null when review skipped
    from_zip: bool = False                  # FR-72
    source_zip_filename: str | None = None
    inferred_tg_name: str | None = None     # FR-78/FR-83 channel-resolved TG; per-ingest
    routing_resolution: RoutingResolution   # per-ingest; pipeline runs once per ingest event
    ingested_at: datetime


class DocumentItemAssociation(BaseModel):
    """Symmetric M:M between file_hash and DeliveryItem within one milestone per FR-79.

    Composite PK (file_hash, delivery_item_id); no synthetic association_id.
    The same file may occupy 2+ NSD paths simultaneously — one physical copy per
    associated item per [D-055] (symlink alternative rejected: SMB symlink semantics
    are inconsistent across kernels and TPMs expect real files per item folder).
    """

    file_hash: str                          # PK part 1 — FK → DocumentIndexRow
    delivery_item_id: str                   # PK part 2 — FK → DeliveryItemBase
    milestone_id: str                       # denormalized; FR-79 same-milestone invariant
    local_nsd_path: str                     # UNC path of THIS ITEM's copy (any of the 4 FR-86 types)
    nsd_path_type: NSDPathType              # indexed state tracker per FR-86
    owner_email: str                        # denormalized for PLM fan-out grouping
    plm_id: str | None = None               # PLM issue for (owner × milestone) per FR-26
    plm_attachment_id: str | None = None    # per FR-79 fan-out; shared across same (owner, plm) rows
    upload_timestamp: datetime | None = None
    associated_at: datetime
    associated_by: str = "auto"             # "auto" (FR-52 pipeline) or "<pm_id>" (TPM / FR-83)


class PLMFanOutTarget(BaseModel):
    """One PLM upload target for a file_hash per FR-79 revised — one per DISTINCT
    (owner_email, plm_id) pair across the file's associations."""

    owner_email: str
    plm_id: str | None                      # None → owner has no PLM issue yet (STR-W006)
    item_count: int                         # items sharing this pair — case (a) vs (b) diagnostic


class CommunicationLogRow(BaseModel):
    """Append-only audit trail per NFR-6. No UPDATE/DELETE on this table."""

    log_id: str
    channel: Channel
    direction: Direction
    timestamp: datetime
    delivery_item_id: str | None = None
    device_id: str | None = None
    sender: str | None = None
    recipients: str | None = None
    subject: str | None = None
    summary: str | None = None              # never raw proprietary content
    external_message_id: str | None = None
    credential_id: str | None = None        # opaque credential_service reference — never plaintext
    action_type: str | None = None          # free-string registry per FR-29 (validated by consumers)
    attachments: list[dict[str, Any]] = []  # [{filename, file_hash, ...}] — never file content


class BatchIdempotencyKey(BaseModel):
    """Per [D-012]. Stored in Redis (short-TTL), not Postgres."""

    batch_id: str
    item_index: int
    status: str
    ttl_seconds: int = 86_400               # 24h max per [D-012]


class TGFolderRoutingRow(BaseModel):
    """Per FR-77 Type-2 routing — persisted (ingress_folder → item_no) mapping.
    Composite PK (milestone_id, tg_name, ingress_folder). Replace-all write semantics."""

    milestone_id: str
    tg_name: str
    ingress_folder: str                     # INBOUND NSD-side path; never the outbound target_folder
    item_no: int
    routing_notes: str | None = None


class TagCatalogRow(BaseModel):
    """Per FR-82 revised — customer-extensible tag catalog; soft-deactivate only."""

    customer_id: str                        # PK part 1
    tag: str                                # PK part 2
    description: str | None = None
    color: str | None = None
    active: bool = True


class AutomationRuleOverride(BaseModel):
    """FR-31 runtime override; precedence over YAML rules per FR-30.
    rule_id is a SOFT FK to customizations/rules YAML — no DB-level FK; orphans
    surface as STR-W004 via rule_engine's startup audit."""

    scope: RuleScope
    scope_id: str | None                    # device_id | customer_id | None (Global)
    rule_id: str
    parameter_name: str
    parameter_value: str                    # serialized; rule_engine validates at read time
    set_by_pm_id: str
    set_at: datetime
    expires_at: datetime | None = None


# UNC path sanity helper used by NSD layer — kept here so models stay IO-free.
def is_unc_path(value: str) -> bool:
    """True when value looks like a \\\\server\\share UNC path."""
    return PureWindowsPath(value).drive.startswith("\\\\")
