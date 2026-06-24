"""FR-12 path (c) -- free-text body + fused LLM call per [D-034].

**Ph-2 STUB**: Ph-1 basic flow exercises only FR-12 path (a). Raising
NotImplementedError on call so callers can detect Ph-2 deferral cleanly.

When implemented (Ph-2):
- A single fused LLM call processes body + first-page attachment excerpts +
  expected_items in one pass (per [D-034]) so message classification and
  attachment routing share context.
- When no attachments present, falls back to CLASSIFY_MESSAGE TaskKind.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from core.src.email_service.protocol import (
    InboundMessage,
    RoutedAttachment,
    StructuredReplyBlock,
)

if TYPE_CHECKING:
    from core.src.llm.protocol import LLMProvider

__all__ = ["parse_freetext_with_attachments"]


async def parse_freetext_with_attachments(
    msg: InboundMessage,
    batch_id: str,
    expected_items: list[dict],
    llm: "LLMProvider",
) -> tuple[StructuredReplyBlock, list[RoutedAttachment]]:
    """FR-12 path (c) -- Ph-2 deferred.

    Raises NotImplementedError per email_service/MODULE.md Ph-1-first discipline.
    Ph-1 basic flow exercises only path (a) via body_parser_structured.
    """
    raise NotImplementedError(
        "FR-12 path (c) (free-text + fused LLM call per [D-034]) is Ph-2 "
        "deferred per email_service/MODULE.md -- Ph-1 basic flow exercises "
        "only FR-12 path (a)."
    )
