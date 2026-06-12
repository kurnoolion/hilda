"""Per-TaskKind input/output Pydantic schemas. Pure types. See core/src/llm/MODULE.md.

Each TaskKind maps 1:1 to an (Input, Output) schema pair here and a Jinja2 template
in templates/. LLMGatewayServer validates every model response against the Output schema.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from core.src.llm.protocol import TaskKind

__all__ = [
    "ClassifyDocTypeInput",
    "ClassifyDocTypeOutput",
    "ClassifyDocInput",
    "ClassifyDocOutput",
    "ExistingDocCandidate",
    "RouteAttachmentInput",
    "RouteAttachmentMatch",
    "RouteAttachmentOutput",
    "ReviewDocumentInput",
    "ReviewDocumentOutput",
    "ClassifyMessageInput",
    "ClassifyMessageOutput",
    "INPUT_SCHEMAS",
    "OUTPUT_SCHEMAS",
]


# --- CLASSIFY_DOC_TYPE — FR-85 Step 2 ------------------------------------------------

class ClassifyDocTypeInput(BaseModel):
    """FR-85 Step 2 — fires only when Step 1 filename regex fails or multi-matches.
    Candidate set RESTRICTED to {test_report, tech_report, waiver}; the LLM never returns
    `compliance_certification_release_notes` (regex-only per Step 1) nor `unresolved`
    (caller maps below-threshold confidence to DocType.UNRESOLVED)."""

    first_page_excerpt: str
    candidate_doc_types: list[Literal["test_report", "tech_report", "waiver"]]


class ClassifyDocTypeOutput(BaseModel):
    """Below-threshold confidence (default 0.85) → caller sets DocType.UNRESOLVED sentinel."""

    doc_type: Literal["test_report", "tech_report", "waiver"]
    confidence: float


# --- CLASSIFY_DOC — [D-039] Step 2 (new vs revision) --------------------------------

@dataclass(frozen=True)
class ExistingDocCandidate:
    doc_id_slug: str
    first_page_excerpt: str


class ClassifyDocInput(BaseModel):
    new_doc_first_page_excerpt: str
    existing_candidates: list[ExistingDocCandidate]


class ClassifyDocOutput(BaseModel):
    verdict: Literal["REVISION", "NEW_DOCUMENT"]
    revision_of: str | None                    # doc_id_slug when verdict == REVISION; else None
    confidence: float


# --- ROUTE_ATTACHMENT — FR-52 step 4 of 5 -------------------------------------------

class RouteAttachmentInput(BaseModel):
    excerpt: str
    candidate_items: list[dict]                # [{item_id, item_name, item_description}] — narrowed by FR-52 caller


class RouteAttachmentMatch(BaseModel):
    """One above-threshold (item_id, confidence) match. Per FR-79 a document may land on
    multiple work-items; each match is committed as a separate DocumentItemAssociation."""

    item_id: str
    confidence: float


class RouteAttachmentOutput(BaseModel):
    """LIST of above-threshold matches. EMPTY → caller falls through to FR-52 step 5
    (default work-item). NON-EMPTY → one DocumentItemAssociation per match per [D-055].
    The caller does NOT re-filter — committing all returned matches is the contract."""

    matches: list[RouteAttachmentMatch]


# --- REVIEW_DOCUMENT — FR-53 ---------------------------------------------------------

class ReviewDocumentInput(BaseModel):
    doc_excerpt: str
    doc_type: str                              # test_report | tech_report | waiver
    checklist: list[dict]                      # build-time per-customer YAML criteria per [D-011]


class ReviewDocumentOutput(BaseModel):
    findings: list[dict]                       # [{checklist_item_id, status, evidence_span}]
    overall_verdict: Literal["pass", "fail", "needs_review"]


# --- CLASSIFY_MESSAGE — FR-12 path (c) per [D-034] ----------------------------------

class ClassifyMessageInput(BaseModel):
    body: str
    candidate_intents: list[str]


class ClassifyMessageOutput(BaseModel):
    intent: str                                # one of candidate_intents
    confidence: float


# --- Registries: TaskKind → schema (used by LLMGatewayServer validation + --contract) ---

INPUT_SCHEMAS: dict[TaskKind, type[BaseModel]] = {
    TaskKind.CLASSIFY_DOC_TYPE: ClassifyDocTypeInput,
    TaskKind.CLASSIFY_DOC: ClassifyDocInput,
    TaskKind.ROUTE_ATTACHMENT: RouteAttachmentInput,
    TaskKind.REVIEW_DOCUMENT: ReviewDocumentInput,
    TaskKind.CLASSIFY_MESSAGE: ClassifyMessageInput,
}

OUTPUT_SCHEMAS: dict[TaskKind, type[BaseModel]] = {
    TaskKind.CLASSIFY_DOC_TYPE: ClassifyDocTypeOutput,
    TaskKind.CLASSIFY_DOC: ClassifyDocOutput,
    TaskKind.ROUTE_ATTACHMENT: RouteAttachmentOutput,
    TaskKind.REVIEW_DOCUMENT: ReviewDocumentOutput,
    TaskKind.CLASSIFY_MESSAGE: ClassifyMessageOutput,
}
