"""FR-24 subject parser -- extracts BATCH-<id> + optional ITEM-<n> + STATUS.

Path (b) FR-12 mailto tap-link subjects are Ph-2 (`[HILDA] BATCH-<id> ITEM-<n>
<STATUS>`). Ph-1 path (a) subjects need only the BATCH-id token. EML-E001
raised when BATCH-id not findable.
"""
from __future__ import annotations

import re

from core.src.diagnostics.error_codes import PipelineError

from core.src.email_service.inbound.classifier import BATCH_ID_RE
from core.src.email_service.protocol import ParsedSubject

__all__ = ["parse_subject"]


# Optional Ph-2 ITEM-<n> + status token after BATCH-id
_ITEM_RE = re.compile(r"ITEM-(\d+)")
_STATUS_TOKENS = {"OPEN", "OWNER_CLOSED", "DELAYED", "BLOCKED", "DONE", "CONFIRMED"}


def parse_subject(subject: str) -> ParsedSubject:
    """Extract BATCH-<id> from subject. Raises EML-E001 when not found."""
    if not subject:
        raise PipelineError(
            "EML-E001",
            context={"mid": "<unknown>", "sender_redacted": "<unknown>"},
        )
    match = BATCH_ID_RE.search(subject)
    if not match:
        raise PipelineError(
            "EML-E001",
            context={"mid": "<unknown>", "sender_redacted": "<unknown>"},
        )
    batch_id = match.group(0)

    item_no: int | None = None
    status: str | None = None
    item_match = _ITEM_RE.search(subject)
    if item_match:
        item_no = int(item_match.group(1))
        # Status token comes after ITEM-<n>; look for one of the known tokens
        tail = subject[item_match.end():].strip().upper()
        for tok in _STATUS_TOKENS:
            if tok in tail:
                status = tok
                break

    return ParsedSubject(
        batch_id=batch_id,
        item_no=item_no,
        status=status,
        raw_subject=subject,
    )
