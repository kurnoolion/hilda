"""FR-12 path (a) sibling -- parses the HTML table emitted by outreach_table.j2.

Added 2026-06-28 per architect Phase B design (Step 5 Phase A established the
table-format outreach; this is the inbound counterpart).

Format expected (per templates/outreach_table.j2):

    <p>HILDA-BATCH-ID: BATCH-<id></p>
    <table>
      <thead><tr>
        <th>item_no</th><th>item_title</th>
        <th>status</th><th>owner_status_note</th>
      </tr></thead>
      <tbody>
        <tr><td>1</td><td>...</td><td>Open</td><td></td></tr>
        ...
      </tbody>
    </table>

Owner edits the `status` and `owner_status_note` cells inline. Reply parsing is
structural (header-row text -> column index), so attribute stripping by email
clients is non-fatal.

Returns the SAME `StructuredReplyBlock` type as `parse_structured_block` so the
consuming task is parser-agnostic. Status-cell -> delivery_state mapping uses the
existing SCREAMING_SNAKE convention (Open->OPEN, Closed->OWNER_CLOSED,
Blocked->BLOCKED, Delayed->DELAYED) -- the task maps to the canonical
DeliveryState enum.

Returns None when the table is not detected OR the BATCH-ID anchor doesn't match
the subject's batch_id -- caller falls back to `parse_structured_block` (text
format) and then to FR-12 path (c) Ph-2 stub.
"""
from __future__ import annotations

import logging
import re

from core.src.email_service.inbound.body_parser_structured import resolve_sender_match
from core.src.email_service.protocol import (
    InboundMessage,
    PerItemReplyUpdate,
    StructuredReplyBlock,
)

__all__ = ["parse_table_block"]

_log = logging.getLogger(__name__)


# Matches the body anchor emitted by outreach_table.j2:
#   <p>HILDA-BATCH-ID: BATCH-<id></p>
# Tolerant to surrounding tags / whitespace -- only the literal token matters.
_BATCH_ANCHOR_RE = re.compile(r"HILDA-BATCH-ID:\s*(BATCH-[A-Za-z0-9]+)", re.IGNORECASE)

# Header cell text -> canonical column name. Owners shouldn't edit the header
# row, but be tolerant to case + whitespace + surrounding HTML entities.
_HEADER_ALIASES = {
    "item_no":           "item_no",
    "item_title":        "item_title",
    "status":            "status",
    "owner_status_note": "owner_status_note",
}

# Owner-facing status cell -> PerItemReplyUpdate.delivery_state symbol.
# SCREAMING_SNAKE per existing parser convention (see body_parser_structured.py).
# The task layer maps these to DeliveryState enum values.
_STATUS_CELL_TO_SYMBOL = {
    "open":     "OPEN",
    "closed":   "OWNER_CLOSED",
    "blocked":  "BLOCKED",
    "delayed":  "DELAYED",
}


def parse_table_block(
    msg: InboundMessage,
    batch_id: str,
    expected_items: list[dict],
) -> StructuredReplyBlock | None:
    """Extract the HILDA outreach-table reply from msg.body_html.

    Returns None when:
      - body_html is empty / missing the BATCH-ID anchor
      - anchor's batch_id doesn't match the caller-supplied batch_id
      - no parseable HILDA table found (header row missing required columns)
      - no data rows yield a valid item_no
    """
    body = msg.body_html or ""
    if not body:
        return None

    try:
        from bs4 import BeautifulSoup
    except ImportError:
        # bs4 is in requirements.txt per Phase B 2026-06-28. If we hit this
        # branch the container hasn't been rebuilt with the new requirements.
        # Log loudly because the symptom otherwise looks like "unparseable
        # body" and burns multiple rounds of debug (architect live test
        # 2026-06-28). Use WARNING so it shows up at default log level.
        _log.warning(
            "parse_table_block: beautifulsoup4 not installed -- container "
            "image needs rebuild with current requirements.txt. Parser "
            "returning None; owner replies will all be classified as "
            "'unparseable' until bs4 is installed."
        )
        return None

    soup = BeautifulSoup(body, "html.parser")

    # Defensive anchor check: parser only proceeds when the body references
    # our specific batch_id somewhere. Prevents picking up a stray <table>
    # from a wholly unrelated reply.
    #
    # We do a plain substring check (not a regex tying "HILDA-BATCH-ID:" to
    # the token) because Outlook's reply rendering puts arbitrary inline
    # markup AND sometimes intervening content from the quoted history
    # between the label and the value -- two earlier attempts (raw-HTML
    # regex 2026-06-28a, text-extract regex 2026-06-28b) both broke on
    # real corp Outlook replies.
    #
    # The classifier already confirmed the SUBJECT carries BATCH-<id>;
    # we only need to confirm the body references the SAME batch_id. If
    # the body matches but doesn't carry our outreach table,
    # _find_hilda_table_rows returns None and the parser still bails out.
    if batch_id not in body:
        return None
    table_rows = _find_hilda_table_rows(soup)
    if table_rows is None:
        return None
    header_map, data_rows = table_rows

    per_item: list[PerItemReplyUpdate] = []
    for tr in data_rows:
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        # Defensive: row shorter than max needed column index -> skip
        def cell_text(col: str) -> str:
            idx = header_map.get(col)
            if idx is None or idx >= len(cells):
                return ""
            return cells[idx].get_text(strip=True).replace("\xa0", " ").strip()

        raw_item_no = cell_text("item_no")
        try:
            item_no = int(raw_item_no)
        except (TypeError, ValueError):
            continue

        raw_status = cell_text("status").lower()
        symbol = _STATUS_CELL_TO_SYMBOL.get(raw_status)
        if symbol is None:
            # Unknown status word -- preserve raw value uppercased so the task
            # layer can audit + skip without dispatching a state transition.
            symbol = raw_status.upper() or "UNKNOWN"

        note = cell_text("owner_status_note") or None

        per_item.append(
            PerItemReplyUpdate(
                item_no=item_no,
                delivery_state=symbol,
                owner_status_note=note,
                confidence=1.0,
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


def _find_hilda_table_rows(soup):
    """Locate the HILDA outreach table among possibly several <table> elements
    in the body (owner signature tables, quoted-reply nesting, etc.).

    A table qualifies when its first row's cell texts (lowercased, stripped)
    include all required columns from _HEADER_ALIASES. Returns
    (header_index_map, data_rows) or None.
    """
    for table in soup.find_all("table"):
        all_rows = table.find_all("tr")
        if len(all_rows) < 2:
            continue
        header_cells = all_rows[0].find_all(["th", "td"])
        header_texts = [c.get_text(strip=True).lower() for c in header_cells]
        header_map: dict[str, int] = {}
        for idx, text in enumerate(header_texts):
            canonical = _HEADER_ALIASES.get(text)
            if canonical is not None and canonical not in header_map:
                header_map[canonical] = idx
        # Required columns for a successful parse: item_no + status.
        # owner_status_note is optional (legacy variants may omit), item_title
        # is purely informational. If item_no or status missing, this isn't
        # our table -- try the next <table> element.
        if "item_no" in header_map and "status" in header_map:
            return header_map, all_rows[1:]
    return None
