"""email_service Protocol surface -- InboundMessage / Attachment / EmailKind /
StructuredReplyBlock / RoutedAttachment / Protocols.

Per email_service/MODULE.md Public surface section + 2026-06-25 cascade locks:
- D-094 SUPERSEDED item_type mixed-case enum (Confirmation / test_tech_waiver_report /
  compliance_certification_release_notes / Default)
- D-105 4-field owner identity (owner_corp_usa_email / owner_corp_email /
  owner_corp_id / owner_name)
- D-106 TGGroupBase Pydantic model dropped (TG fields denormalized onto DeliveryItemBase)
- D-053 impl note 2026-06-08 -- 5-value DocType + FR-85 2-step classification ladder
  + FR-86 4-path storage matrix + FR-79 multi-item association
- D-060 inferred_tg_name per-channel resolution

Pure types -- no IO. Implementations live in inbound/ + outbound/ + sp_alert_parser/.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import AsyncIterator, Literal, Protocol, runtime_checkable

from core.src.storage.models import NSDPathType, RoutingResolution

__all__ = [
    "AttachmentItemMatch",
    "AttachmentRouter",
    "ClassificationResolution",
    "EmailKind",
    "EmailReceiver",
    "EmailSender",
    "InboundAttachment",
    "InboundMessage",
    "ParsedSubject",
    "PerItemReplyUpdate",
    "RoutedAttachment",
    "StructuredReplyBlock",
]


class EmailKind(str, Enum):
    """Discriminator output of inbound/classifier.py."""

    OWNER_REPLY = "owner_reply"     # subject contains BATCH-<id> per FR-24
    SP_ALERT = "sp_alert"           # subject matches D-047 alert pattern
    OTHER = "other"                 # neither -- surfaced as 'unrecognized inbound' for PM review


class ClassificationResolution(str, Enum):
    """Per FR-85 2-step doc_type classification ladder -- records which step
    resolved the classification (or UnresolvedLowConfidence on Step 2 below
    threshold). Added 2026-06-09 per [D-053] impl note 2026-06-08."""

    FILENAME_REGEX = "FilenameRegex"
    LLM_CLASSIFIED = "LLMClassified"
    UNRESOLVED_LOW_CONFIDENCE = "UnresolvedLowConfidence"


@dataclass(frozen=True)
class InboundAttachment:
    filename: str
    content: bytes
    content_type: str           # MIME type
    file_hash: str              # sha256 of content; used by D-039 Step 0 dedup


@dataclass(frozen=True)
class InboundMessage:
    """One inbound email after IMAP fetch, before parsing."""

    message_id: str             # RFC 5322 Message-ID header
    received_at: datetime
    sender: str                 # email address (raw header)
    to_addrs: tuple[str, ...]
    cc_addrs: tuple[str, ...]
    subject: str
    body_text: str
    body_html: str | None
    attachments: tuple[InboundAttachment, ...]


@dataclass(frozen=True)
class ParsedSubject:
    """FR-24 output. ITEM-n / status optional (Ph-2 mailto tap-link subjects)."""

    batch_id: str
    item_no: int | None         # set when path (b) mailto subject; Ph-2
    status: str | None          # set when path (b)
    raw_subject: str


@dataclass(frozen=True)
class PerItemReplyUpdate:
    item_no: int
    delivery_state: str | None      # OPEN / OWNER_CLOSED / DELAYED / BLOCKED / etc.
    owner_status_note: str | None
    confidence: float               # 1.0 for structured-block parse; LLM-derived for path (c)


@dataclass(frozen=True)
class StructuredReplyBlock:
    """FR-12 path (a) parse output."""

    batch_id: str
    sender_email: str
    sender_match: Literal["owner", "tg_alias", "cc", "mismatch"]
    per_item_updates: tuple[PerItemReplyUpdate, ...]


@dataclass(frozen=True)
class AttachmentItemMatch:
    """One (item_id, confidence) match per FR-79 multi-item association.
    Mirrors llm.RouteAttachmentMatch but adds the routing step that produced
    it (for diagnostic/audit). One DocumentItemAssociation row written per
    match per [D-055]."""

    item_id: str
    confidence: float           # [0.0, 1.0]
    source: RoutingResolution   # which FR-52 step produced this match


@dataclass(frozen=True)
class RoutedAttachment:
    """FR-52 5-step routing + FR-85 doc_type classification + FR-86 storage matrix
    output per inbound attachment (per [D-053] impl note 2026-06-08)."""

    file_hash: str
    matches: tuple[AttachmentItemMatch, ...]
    doc_type: str               # DocType value; 5-value per [D-053]
    doc_id_slug: str | None
    rev_number: int | None
    classification_resolution: ClassificationResolution
    routing_resolution: RoutingResolution
    inferred_tg_name: str | None
    nsd_path_type: NSDPathType
    is_duplicate: bool = False


# -----------------------------------------------------------------------------
# Protocols
# -----------------------------------------------------------------------------


@runtime_checkable
class EmailReceiver(Protocol):
    """All callers depend on this Protocol, not on a concrete implementation."""

    async def fetch_new(self) -> AsyncIterator[InboundMessage]: ...

    async def fetch_once(self) -> list[InboundMessage]: ...

    async def mark_processed(self, message_id: str) -> None: ...


@runtime_checkable
class EmailSender(Protocol):
    async def send(
        self,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        in_reply_to: str | None = None,
        attachments: list[tuple[str, bytes, str]] | None = None,
    ) -> str:
        """Returns Message-ID of sent email.

        attachments (added 2026-07-15 for TPM DRR closure email per architect ask):
            optional list of (filename, content_bytes, mime_type) tuples.
            Each tuple gets attached to the outbound message. Callers that
            don't need attachments pass None (default) -- backward-compatible
            with all existing call sites.
        """
        ...


@runtime_checkable
class AttachmentRouter(Protocol):
    async def route(
        self,
        attachment: InboundAttachment,
        batch_id: str,
        candidate_items: list[dict],
    ) -> RoutedAttachment:
        """Run the FR-52 + FR-85 + FR-86 pipeline for one attachment.

        candidate_items shape: [{item_id, item_name, item_description, item_type,
        tg_name, ...}]; item_type values per the 4-value ItemType enum
        {Confirmation, test_tech_waiver_report, compliance_certification_release_notes,
        Default}. item_description is a list-of-lists (FR-82 nested tag-sets) OR
        a comma-separated string (Ph-1 simplification).
        """
        ...
