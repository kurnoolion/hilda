"""OUTREACH-STATUS-HINT-1: status-hint header + 'Close' status variant.

Two TPM asks (2026-09-02):
  1. show the allowed values in the Current Status header cell, so owners do
     not have to scroll to the legend under the table;
  2. accept 'Close' as well as 'Closed' -- owners type both.

The header change is the risky half. Reply parsing locates the outreach
table by matching header text, and a missing `status` alias makes
_find_hilda_table_rows reject the table outright -- so the WHOLE reply stops
parsing, not just one column. The round-trip test below renders the shipped
outreach_table.j2 and parses it back, which is the only test that can catch
template and parser drifting apart.
"""
from __future__ import annotations

import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.src.email_service.inbound.body_parser_table import (
    _STATUS_CELL_TO_SYMBOL,
    parse_table_block,
    resolve_header_alias,
)

TEMPLATE_DIR = Path("core/src/email_service/templates")


class TestResolveHeaderAlias:
    def test_plain_headers_still_resolve(self) -> None:
        # Legacy exact-match behaviour must be untouched.
        assert resolve_header_alias("Current Status") == "status"
        assert resolve_header_alias("status") == "status"
        assert resolve_header_alias("item_no") == "item_no"
        assert resolve_header_alias("Item No") == "item_no"
        assert resolve_header_alias("Item Title") == "item_title"
        assert resolve_header_alias("Owner Comment") == "owner_status_note"
        assert resolve_header_alias("owner_status_note") == "owner_status_note"

    def test_status_hint_header_resolves_br_form(self) -> None:
        # What bs4 actually produces for `Current Status<br>(Open/...)`:
        # no space before the paren.
        assert resolve_header_alias(
            "Current Status(Open/Closed/Blocked/Delayed)") == "status"

    def test_status_hint_header_resolves_spaced_form(self) -> None:
        assert resolve_header_alias(
            "Current Status (Open/Closed/Blocked/Delayed)") == "status"

    def test_resolution_is_case_insensitive(self) -> None:
        assert resolve_header_alias(
            "CURRENT STATUS (OPEN/CLOSED/BLOCKED/DELAYED)") == "status"
        assert resolve_header_alias("cUrReNt StAtUs") == "status"

    def test_existing_completion_date_hints_still_resolve(self) -> None:
        for h in ("Completion Date (MM/DD/YYYY)",
                  "Completion Date (YYYY-MM-DD)",
                  "completion_date", "completion date", "completion"):
            assert resolve_header_alias(h) == "actual_completion_date", h

    def test_unknown_parenthetical_hint_resolves_generically(self) -> None:
        # The point of normalising: a future hint needs no new alias.
        assert resolve_header_alias(
            "Completion Date (any new hint)") == "actual_completion_date"
        assert resolve_header_alias(
            "Owner Comment (free text)") == "owner_status_note"
        assert resolve_header_alias("Item No (integer)") == "item_no"

    def test_non_headers_return_none(self) -> None:
        for h in ("Random Column", "", "   ", "Notes", "(Open/Closed)"):
            assert resolve_header_alias(h) is None, h

    def test_none_input_is_safe(self) -> None:
        assert resolve_header_alias(None) is None  # type: ignore[arg-type]


class TestCloseStatusVariant:
    def test_close_maps_to_owner_closed(self) -> None:
        assert _STATUS_CELL_TO_SYMBOL["close"] == "OWNER_CLOSED"

    def test_closed_still_maps_to_owner_closed(self) -> None:
        assert _STATUS_CELL_TO_SYMBOL["closed"] == "OWNER_CLOSED"

    def test_other_values_unchanged(self) -> None:
        assert _STATUS_CELL_TO_SYMBOL["open"] == "OPEN"
        assert _STATUS_CELL_TO_SYMBOL["blocked"] == "BLOCKED"
        assert _STATUS_CELL_TO_SYMBOL["delayed"] == "DELAYED"

    def test_no_unintended_new_vocabulary(self) -> None:
        # 'done' / 'complete' were NOT requested -- an owner typing those
        # should still fall through to the unknown-status branch so it is
        # visible rather than silently interpreted.
        assert set(_STATUS_CELL_TO_SYMBOL) == {
            "open", "closed", "close", "blocked", "delayed"}


def _render_outreach_table(items: list[dict], batch_id: str) -> str:
    jinja2 = pytest.importorskip("jinja2")
    env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(TEMPLATE_DIR)))
    return env.get_template("outreach_table.j2").render(
        owner=SimpleNamespace(owner_names=["Owner A"], owner_name="Owner A"),
        batch_id=batch_id,
        items=items,
    )


class TestRenderedTemplate:
    def test_header_cell_carries_the_hint(self) -> None:
        html = _render_outreach_table([], "BATCH-x")
        assert "Current Status<br>(Open/Closed/Blocked/Delayed)" in html

    def test_hint_is_in_the_same_cell_not_a_new_column(self) -> None:
        # A separate column would shift every cell index after it and break
        # index-based reply parsing.
        html = _render_outreach_table([], "BATCH-x")
        thead = re.search(r"<thead.*?</thead>", html, re.S).group(0)
        # `<th\s` so the `<thead` tag itself is not counted.
        assert len(re.findall(r"<th\s", thead)) == 6

    def test_jinja_comment_is_not_shipped_to_owners(self) -> None:
        html = _render_outreach_table([], "BATCH-x")
        assert "OUTREACH-STATUS-HINT-1" not in html
        assert "resolve_header_alias" not in html


class TestRoundTrip:
    """Render the shipped template, fill it in as an owner would, parse back."""

    @staticmethod
    def _reply(statuses: list[str], batch_id: str = "BATCH-rt1") -> str:
        items = [
            {"item_no": i + 1, "item_name": f"item_{i + 1}",
             "modality_display": "Email"}
            for i in range(len(statuses))
        ]
        html = _render_outreach_table(items, batch_id)
        # An owner edits the pre-filled 'Open' cell in each row.
        for status in statuses:
            html = html.replace("<td>Open</td>", f"<td>{status}</td>", 1)
        return html

    @staticmethod
    def _msg(html: str, batch_id: str = "BATCH-rt1"):
        from datetime import datetime, timezone
        from core.src.email_service.protocol import InboundMessage
        return InboundMessage(
            message_id="m1",
            received_at=datetime(2026, 9, 2, tzinfo=timezone.utc),
            sender="owner@corp.example",
            to_addrs=("hilda@corp.example",),
            cc_addrs=(),
            subject=f"Re: [HILDA] Status request -- {batch_id}",
            body_text="",
            body_html=html,
            attachments=(),
        )

    def test_shipped_template_still_parses(self) -> None:
        # The regression guard: template header changed, parser must follow.
        block = parse_table_block(
            self._msg(self._reply(["Closed", "Open"])), "BATCH-rt1", [])
        assert block is not None, "reply from the shipped template did not parse"
        assert len(block.per_item_updates) == 2

    def test_close_variant_round_trips_to_owner_closed(self) -> None:
        block = parse_table_block(
            self._msg(self._reply(["Close"])), "BATCH-rt1", [])
        assert block is not None
        assert block.per_item_updates[0].delivery_state == "OWNER_CLOSED"

    def test_closed_and_close_are_equivalent(self) -> None:
        a = parse_table_block(
            self._msg(self._reply(["Closed"])), "BATCH-rt1", [])
        b = parse_table_block(
            self._msg(self._reply(["Close"])), "BATCH-rt1", [])
        assert a is not None and b is not None
        assert (a.per_item_updates[0].delivery_state
                == b.per_item_updates[0].delivery_state == "OWNER_CLOSED")

    @pytest.mark.parametrize("status,expected", [
        ("Open", "OPEN"), ("Closed", "OWNER_CLOSED"), ("Close", "OWNER_CLOSED"),
        ("close", "OWNER_CLOSED"), ("CLOSE", "OWNER_CLOSED"),
        ("Blocked", "BLOCKED"), ("Delayed", "DELAYED"),
    ])
    def test_every_status_round_trips(self, status: str, expected: str) -> None:
        block = parse_table_block(
            self._msg(self._reply([status])), "BATCH-rt1", [])
        assert block is not None
        assert block.per_item_updates[0].delivery_state == expected

    def test_owner_typing_the_hint_text_is_not_a_valid_status(self) -> None:
        # If an owner pastes the header hint into a data cell, it must NOT
        # resolve to a state -- it should surface as unknown.
        block = parse_table_block(
            self._msg(self._reply(["Open/Closed/Blocked/Delayed"])),
            "BATCH-rt1", [])
        assert block is not None
        assert block.per_item_updates[0].delivery_state not in (
            "OPEN", "OWNER_CLOSED", "BLOCKED", "DELAYED")
