"""LLMProvider Protocol + request/response types. Anchors [D-007] [D-029] [D-052].

Pure types — no IO. See core/src/llm/MODULE.md.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

__all__ = ["TaskKind", "LLMRequest", "LLMResponse", "LLMProvider"]


class TaskKind(str, Enum):
    """Bounded set of runtime LLM tasks. Each value maps 1:1 to a prompt template in
    templates/ and a structured output schema in schemas.py. Five Ph-1 TaskKinds
    (CLASSIFY_DOC_TYPE restored 2026-06-09 per [D-053] impl note 2026-06-08)."""

    ROUTE_ATTACHMENT = "route_attachment"      # FR-52 step 4 of 5 — attachment → DeliveryItem match
    CLASSIFY_DOC = "classify_doc"              # [D-039] Step 2 — new document vs revision-of-existing
    CLASSIFY_DOC_TYPE = "classify_doc_type"    # FR-85 Step 2 — restricted set {test_report, tech_report, waiver}
    REVIEW_DOCUMENT = "review_document"        # FR-53 — quality review against checklist
    CLASSIFY_MESSAGE = "classify_message"      # FR-12 path (c) — message-intent fallback
    # Ph-2 (deferred per [D-029] / DEF-3 / DEF-4):
    # DRAFT_CUSTOMER_REPLY = "draft_customer_reply"
    # SUMMARIZE_STATUS     = "summarize_status"


@dataclass(frozen=True)
class LLMRequest:
    task: TaskKind
    inputs: dict[str, Any]                     # task-specific payload — see schemas.py
    timeout_s: float | None = None
    max_tokens: int | None = None
    temperature: float | None = None           # None → use template default
    idempotency_key: str | None = None          # dedup within process lifetime


@dataclass(frozen=True)
class LLMResponse:
    task: TaskKind
    output: dict[str, Any]                      # structured per task — validated against output schema
    model: str                                  # which on-prem model served the call (audit field)
    latency_ms: int
    tokens_in: int
    tokens_out: int


@runtime_checkable
class LLMProvider(Protocol):
    """All callers depend on this Protocol, not on a concrete implementation.
    Implementations: OnPremLLMClient (Ph-1/Ph-2; calls hilda-llm-gateway over HTTP),
    LLMGatewayServer (egress-side, runs inside hilda-llm-gateway), MockLLM (tests)."""

    async def invoke(self, request: LLMRequest) -> LLMResponse: ...

    async def health(self) -> dict[str, Any]:
        """Returns {model, ready: bool, queue_depth: int}. Used by --diagnostic."""
        ...
