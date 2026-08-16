"""FR-12 path (a) -- regex-parses the structured reply block embedded in
outbound emails per templates/outreach_*.j2.

The structured block format (must match the outbound template):

    HILDA STATUS BLOCK
    BATCH-<id>
    ITEM-<n> <STATUS>: <status_note>
    ITEM-<n> <STATUS>: <status_note>

Returns None when the structured block is not detected -- caller (workflow)
should fall back to FR-12 path (c) free-text + fused LLM call (Ph-2; currently
a stub).

sender_match enum (owner | tg_alias | cc | mismatch) resolved against the 4-field
owner identity per [D-105] (preferred order: owner_corp_usa_email -> owner_corp_email
-> email_group_alias -> cc list). Fields are read off DeliveryItemBase per [D-106]
denormalization (TGGroupBase Pydantic model dropped).
"""
from __future__ import annotations

import re
from typing import Any, Literal

from core.src.email_service.protocol import (
    InboundMessage,
    PerItemReplyUpdate,
    StructuredReplyBlock,
)

__all__ = ["parse_structured_block", "resolve_sender_match"]


_HEADER_RE = re.compile(r"HILDA\s+STATUS\s+BLOCK", re.IGNORECASE)
_BATCH_LINE_RE = re.compile(r"(BATCH-[a-zA-Z0-9]+)")
# ITEM line: "ITEM-<n> <STATUS>: <note>"
_ITEM_LINE_RE = re.compile(
    r"^ITEM-(?P<n>\d+)\s+(?P<status>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<note>.*)$",
    re.MULTILINE,
)


def parse_structured_block(
    msg: InboundMessage,
    batch_id: str,
    expected_items: list[dict],
) -> StructuredReplyBlock | None:
    """Regex-parse the structured reply block in msg.body_text.

    Returns None when the block header is missing OR no ITEM-<n> lines parse.
    """
    body = msg.body_text or ""
    if not _HEADER_RE.search(body):
        return None
    # BATCH-id line must match the subject's batch_id
    batch_match = _BATCH_LINE_RE.search(body)
    if not batch_match or batch_match.group(1) != batch_id:
        return None

    per_item: list[PerItemReplyUpdate] = []
    for m in _ITEM_LINE_RE.finditer(body):
        item_no = int(m.group("n"))
        status = m.group("status").upper()
        note = m.group("note").strip() or None
        per_item.append(
            PerItemReplyUpdate(
                item_no=item_no,
                delivery_state=status,
                owner_status_note=note,
                confidence=1.0,             # FR-12 path (a) is regex-exact
            )
        )

    if not per_item:
        return None

    sender_email = (msg.sender or "").strip().lower()
    sender_match = resolve_sender_match(
        sender_email, msg.cc_addrs, expected_items
    )

    return StructuredReplyBlock(
        batch_id=batch_id,
        sender_email=sender_email,
        sender_match=sender_match,
        per_item_updates=tuple(per_item),
    )


def resolve_sender_match(
    sender_email: str,
    cc_addrs: tuple[str, ...],
    expected_items: list[dict],
) -> Literal["owner", "tg_alias", "cc", "mismatch"]:
    """Resolve sender against the 4-field owner identity per [D-105].

    Lookup order (OWNER-7 2026-08-16, B-final-B: singular fallback removed
    -- owner_corp_*_email are now unsuffixed lists on DeliveryItemBase):
      (1) owner_corp_usa_email  -- ANY entry match (multi-owner list)
      (2) owner_corp_email      -- ANY entry match (multi-owner list)
      (3) tg_email_group_alias  -- TG-shared alias
      (4) cc list               -- cc'd participant
      otherwise -> mismatch (caller does NOT reject the reply -- reply is
      still parsed + applied; sender_match=="mismatch" surfaces in the
      audit row per architect Q3 2026-08-14 "not strict; just add to audit").

    OWNER-4 (2026-08-14): multi-owner semantics. A single item may have
    N owners (all sharing accountability). Any owner's reply matches;
    audit trail captures the matching entry via sender_email regardless.

    expected_items is a list of dicts representing DeliveryItemBase rows;
    per [D-106] TG fields are denormalized onto each item; per OWNER-7 the
    owner_corp_*_email keys carry list values (unsuffixed after B-final-B
    rename). Dict values that are strings (pre-OWNER-7 test fixtures) fall
    into the tolerant path: any non-list value is treated as an empty list
    for the check, which the caller-side data-shape upgrade is expected
    to cover -- but string-typed values should not appear in freshly-
    fetched rows after OWNER-7.
    """
    s = (sender_email or "").strip().lower()
    if not s:
        return "mismatch"
    cc_lower = {c.strip().lower() for c in cc_addrs if c}
    for item in expected_items:
        usa_list = _lc_list(item.get("owner_corp_usa_email"))
        corp_list = _lc_list(item.get("owner_corp_email"))
        if s in usa_list or s in corp_list:
            return "owner"
    for item in expected_items:
        alias = (item.get("tg_email_group_alias") or item.get("email_group_alias") or "").strip().lower()
        if s and s == alias:
            return "tg_alias"
    if s in cc_lower:
        return "cc"
    return "mismatch"


def _lc_list(value: Any) -> set[str]:
    """Coerce a list-of-strings owner field to a lowercased-stripped set
    for membership testing. Non-list values (missing / None) return an
    empty set. Empty strings inside the list are dropped."""
    if not isinstance(value, list):
        return set()
    return {str(e).strip().lower() for e in value if e and str(e).strip()}
