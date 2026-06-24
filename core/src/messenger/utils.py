"""messenger utility helpers -- byte-length validation + truncation + redaction.

Per NFR-2: owner_corp_id is redacted in compact reports + log lines (not in audit
storage). Use `redact_owner_corp_id` for any RPT / log emission; the raw value
flows to CommunicationLog rows + adapter call frames only.
"""
from __future__ import annotations

__all__ = [
    "TRUNCATION_MARKER",
    "validate_message_length",
    "truncate_message",
    "redact_owner_corp_id",
]

TRUNCATION_MARKER = " ... [truncated]"


def validate_message_length(message: str, max_bytes: int) -> tuple[bool, int]:
    """Return (within_limit, byte_count) -- UTF-8 byte size, not char count."""
    n = len(message.encode("utf-8"))
    return (n <= max_bytes, n)


def truncate_message(message: str, max_bytes: int) -> tuple[str, int]:
    """Truncate UTF-8 message to fit within `max_bytes`, appending TRUNCATION_MARKER.

    Returns (truncated_message, original_byte_count). Idempotent when already
    within the limit (returns the message unchanged + its byte count).
    """
    encoded = message.encode("utf-8")
    original_n = len(encoded)
    if original_n <= max_bytes:
        return message, original_n
    marker = TRUNCATION_MARKER
    marker_bytes = marker.encode("utf-8")
    budget = max_bytes - len(marker_bytes)
    if budget <= 0:
        # Pathologically tiny max_bytes -- emit pure marker truncated.
        return marker_bytes[:max_bytes].decode("utf-8", errors="ignore"), original_n
    # Walk back from `budget` until we land on a UTF-8 boundary.
    head = encoded[:budget]
    # decode with errors="ignore" then re-encode to drop dangling multibyte head.
    head_str = head.decode("utf-8", errors="ignore")
    return head_str + marker, original_n


def redact_owner_corp_id(owner_corp_id: str) -> str:
    """Per NFR-2 -- mask owner_corp_id for compact reports + log lines.

    Pattern: first char + asterisks. Empty string yields '<empty>'.
    """
    if not owner_corp_id:
        return "<empty>"
    head = owner_corp_id[0]
    return f"{head}***"
