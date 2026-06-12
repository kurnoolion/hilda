"""llm — single Protocol surface for HILDA runtime LLM calls. See core/src/llm/MODULE.md.

Anchors [D-007] (on-prem hosting), [D-029] (Ph-1 scope), [D-052] (tri-backend, empirical
routing, no spillover), [D-053] (FR-85 CLASSIFY_DOC_TYPE).
"""
from core.src.llm import qc_templates  # noqa: F401  (registers LLG QC template)
from core.src.llm.client import OnPremLLMClient
from core.src.llm.gateway_server import BackendConfig, LLMGatewayServer
from core.src.llm.mock import MockLLM
from core.src.llm.protocol import LLMProvider, LLMRequest, LLMResponse, TaskKind
from core.src.llm.schemas import (
    INPUT_SCHEMAS,
    OUTPUT_SCHEMAS,
    ClassifyDocInput,
    ClassifyDocOutput,
    ClassifyDocTypeInput,
    ClassifyDocTypeOutput,
    ClassifyMessageInput,
    ClassifyMessageOutput,
    ExistingDocCandidate,
    ReviewDocumentInput,
    ReviewDocumentOutput,
    RouteAttachmentInput,
    RouteAttachmentMatch,
    RouteAttachmentOutput,
)

__all__ = [
    "BackendConfig",
    "INPUT_SCHEMAS",
    "LLMGatewayServer",
    "OUTPUT_SCHEMAS",
    "ClassifyDocInput",
    "ClassifyDocOutput",
    "ClassifyDocTypeInput",
    "ClassifyDocTypeOutput",
    "ClassifyMessageInput",
    "ClassifyMessageOutput",
    "ExistingDocCandidate",
    "LLMProvider",
    "LLMRequest",
    "LLMResponse",
    "MockLLM",
    "OnPremLLMClient",
    "ReviewDocumentInput",
    "ReviewDocumentOutput",
    "RouteAttachmentInput",
    "RouteAttachmentMatch",
    "RouteAttachmentOutput",
    "TaskKind",
]
