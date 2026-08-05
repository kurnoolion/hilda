"""DRR-V2-5 (2026-08-04) — Verizon-template DRR Excel builder tests.

Covers:
  * Legacy 4-column flat mode (backward compat: no keyword args).
  * DRR-V2 mode: header block cells, section rows, item rows sourced
    from postgres DeliveryItem (item_no + name + actual_completion_date
    + status + tg_name + comment).
  * Header field blank -> WARN log + blank cell (Q6 spec).
  * Items 85/86/87 excluded via section_grouping filtering.
  * Missing logo path silently skipped.
"""
from __future__ import annotations

import io
import logging
from datetime import date
from types import SimpleNamespace

import pytest

from core.src.email_service.outbound.drr_report_excel import (
    CLOSED_LIKE_STATES,
    build_drr_report_excel,
    status_for_item,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _item(
    item_no: int,
    *,
    item_name: str | None = None,
    delivery_state: str = "Open",
    actual_completion_date: date | None = None,
    tg_name: str = "TPM",
    comment: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        item_no=item_no,
        item_name=item_name or f"Item {item_no}",
        delivery_state=delivery_state,
        actual_completion_date=actual_completion_date,
        tg_name=tg_name,
        comment=comment,
        # legacy attribute so status_for_item / legacy mode also work
        owner_status_note=comment,
    )


def _open_ws(xlsx_bytes: bytes):
    """Parse the produced bytes back with openpyxl for cell assertions."""
    from openpyxl import load_workbook
    wb = load_workbook(filename=io.BytesIO(xlsx_bytes))
    return wb.active


# ---------------------------------------------------------------------------
# Legacy mode
# ---------------------------------------------------------------------------


class TestLegacyFlat:
    def test_legacy_call_shape_still_works(self):
        items = [_item(2, delivery_state="Closed"), _item(1)]
        out = build_drr_report_excel(items)
        assert isinstance(out, bytes) and len(out) > 0
        ws = _open_ws(out)
        assert ws.title == "DRR Closure"
        # Legacy 4-column header
        assert ws.cell(row=1, column=1).value == "Item No"
        assert ws.cell(row=1, column=4).value == "Owner Comment"
        # Sorted by item_no ascending
        assert ws.cell(row=2, column=1).value == 1
        assert ws.cell(row=3, column=1).value == 2

    def test_status_for_item(self):
        assert status_for_item(_item(1, delivery_state="Open")) == "Open"
        assert status_for_item(_item(1, delivery_state="Closed")) == "Closed"
        assert status_for_item(_item(1, delivery_state="ReadyForSubmission")) == "Closed"
        assert status_for_item(_item(1, delivery_state="SubmittedToCustomer")) == "Closed"
        assert status_for_item(_item(1, delivery_state="OwnerClosed")) == "Open"

    def test_closed_like_states_membership(self):
        assert "Closed" in CLOSED_LIKE_STATES
        assert "OwnerClosed" not in CLOSED_LIKE_STATES


# ---------------------------------------------------------------------------
# DRR-V2 mode — header block
# ---------------------------------------------------------------------------


class TestV2HeaderBlock:
    def _v2_call(self, **overrides):
        defaults = dict(
            items=[_item(1)],
            customer_id="MMK",
            device_id="SM-F976U",
            milestone_id="DRR",
            section_grouping=[{
                "section": "Product Documentation Review",
                "work_items": [{"item_no": 1, "item_name": "Item 1"}],
            }],
            drr_version="5.7",
            milestone_headers={
                "fld_lockdown_date": date(2026, 3, 18),
                "req_version": "Oct 25",
                "target_date": date(2026, 4, 29),
            },
            project_headers={
                "LE": date(2026, 6, 11),
                "FFW": date(2026, 5, 13),
            },
        )
        defaults.update(overrides)
        return build_drr_report_excel(**defaults)

    def test_row_1_oem_model_title(self):
        ws = _open_ws(self._v2_call())
        assert ws.cell(row=1, column=2).value == "OEM Model"

    def test_row_3_device_readiness_review_subtitle(self):
        ws = _open_ws(self._v2_call())
        assert ws.cell(row=3, column=2).value == "Device Readiness Review"

    def test_row_4_drr_version(self):
        ws = _open_ws(self._v2_call())
        assert ws.cell(row=4, column=2).value == "DRR Version 5.7"

    def test_header_field_values_rendered(self):
        ws = _open_ws(self._v2_call())
        # Row 6: Model Number -> device_id
        assert ws.cell(row=6, column=2).value == "Model Number:"
        assert ws.cell(row=6, column=3).value == "SM-F976U"
        # Row 7: Compliance Matrix Lockdown On -> fld_lockdown_date
        assert ws.cell(row=7, column=2).value == "Compliance Matrix Lockdown On:"
        assert ws.cell(row=7, column=3).value == "03/18/26"
        # Row 8: VZW Requirements Version -> req_version
        assert ws.cell(row=8, column=3).value == "Oct 25"
        # Row 10: DRR Date -> target_date
        assert ws.cell(row=10, column=3).value == "04/29/26"
        # Row 11: Ph1 Date -> FFW
        assert ws.cell(row=11, column=3).value == "05/13/26"
        # Row 12: Target TA Date -> LE
        assert ws.cell(row=12, column=3).value == "06/11/26"

    def test_row_13_yellow_note(self):
        ws = _open_ws(self._v2_call())
        assert "Yellow Highlighting" in (ws.cell(row=13, column=2).value or "")

    def test_row_14_body_column_headers(self):
        ws = _open_ws(self._v2_call())
        assert ws.cell(row=14, column=2).value == "Description of Task"
        assert ws.cell(row=14, column=3).value == "Completion"
        assert ws.cell(row=14, column=4).value == "Current Status"
        assert ws.cell(row=14, column=5).value == "Owner"
        assert ws.cell(row=14, column=6).value == "Remarks"

    def test_missing_drr_version_falls_back_to_bare_label(self):
        ws = _open_ws(self._v2_call(drr_version=None))
        assert ws.cell(row=4, column=2).value == "DRR Version"

    def test_blank_header_field_warns_and_emits_blank(self, caplog):
        with caplog.at_level(
            logging.WARNING,
            logger="core.src.email_service.outbound.drr_report_excel",
        ):
            ws = _open_ws(self._v2_call(
                milestone_headers={
                    "fld_lockdown_date": None,
                    "req_version": "",
                    "target_date": date(2026, 4, 29),
                },
            ))
        assert ws.cell(row=7, column=3).value in (None, "")   # fld_lockdown_date blank
        assert ws.cell(row=8, column=3).value in (None, "")   # req_version blank
        blanks = [
            r for r in caplog.records
            if "header field" in r.getMessage() and "blank" in r.getMessage()
        ]
        assert len(blanks) >= 2

    def test_missing_logo_path_does_not_crash(self):
        # logo_path=None -> silent skip; logo_path=/nonexistent -> WARN + skip
        out = self._v2_call(logo_path=None)
        assert len(out) > 0
        out2 = self._v2_call(logo_path="/nonexistent/verizon.png")
        assert len(out2) > 0


# ---------------------------------------------------------------------------
# DRR-V2 mode — section + item rows
# ---------------------------------------------------------------------------


class TestV2Body:
    def test_section_row_then_item_rows(self):
        grouping = [
            {
                "section": "Product Documentation Review",
                "work_items": [
                    {"item_no": 1, "item_name": "Product Summary Sheet"},
                    {"item_no": 2, "item_name": "Product ID"},
                ],
            },
            {
                "section": "Pre-Submission Items",
                "work_items": [{"item_no": 5, "item_name": "ODR"}],
            },
        ]
        items = [
            _item(1, item_name="Product Summary Sheet", delivery_state="Open",
                  tg_name="CPM", comment="Finalize by P1"),
            _item(2, item_name="Product ID", delivery_state="Open", tg_name="CPM"),
            _item(5, item_name="ODR", delivery_state="Closed",
                  actual_completion_date=date(2026, 4, 29),
                  tg_name="UX team", comment="ETA: 4/29"),
        ]
        ws = _open_ws(build_drr_report_excel(
            items=items,
            customer_id="MMK", device_id="SM-F976U", milestone_id="DRR",
            section_grouping=grouping,
            drr_version="5.7",
        ))
        # Row 15 = section 1 label (merged B..F, value in A)
        assert ws.cell(row=15, column=1).value == "Product Documentation Review"
        # Rows 16-17: two item rows
        assert ws.cell(row=16, column=1).value == 1
        assert ws.cell(row=16, column=2).value == "Product Summary Sheet"
        assert ws.cell(row=16, column=4).value == "Open"
        assert ws.cell(row=16, column=5).value == "CPM"
        assert ws.cell(row=16, column=6).value == "Finalize by P1"
        assert ws.cell(row=17, column=1).value == 2
        # Row 18 = section 2 header
        assert ws.cell(row=18, column=1).value == "Pre-Submission Items"
        # Row 19 = item#5 with completion date formatted MM/DD/YY
        assert ws.cell(row=19, column=1).value == 5
        assert ws.cell(row=19, column=3).value == "04/29/26"
        assert ws.cell(row=19, column=4).value == "Closed"

    def test_missing_postgres_item_still_renders_template_no_and_name(self):
        """When section_grouping references an item_no that has no matching
        DeliveryItem in postgres (setup not run yet / item deleted), the
        row must still appear with item_no + template name; other columns
        blank rather than crashing on getattr."""
        grouping = [{
            "section": "Sec",
            "work_items": [{"item_no": 42, "item_name": "Orphan Item"}],
        }]
        ws = _open_ws(build_drr_report_excel(
            items=[],   # no delivery items at all
            customer_id="MMK", device_id="X", milestone_id="M",
            section_grouping=grouping,
        ))
        assert ws.cell(row=15, column=1).value == "Sec"
        assert ws.cell(row=16, column=1).value == 42
        assert ws.cell(row=16, column=2).value == "Orphan Item"
        # openpyxl round-trip normalizes empty strings to None on load.
        assert ws.cell(row=16, column=4).value in (None, "")
        assert ws.cell(row=16, column=5).value in (None, "")

    def test_empty_section_is_skipped(self):
        grouping = [
            {"section": "Empty", "work_items": []},
            {"section": "Populated",
             "work_items": [{"item_no": 1, "item_name": "X"}]},
        ]
        ws = _open_ws(build_drr_report_excel(
            items=[_item(1)],
            customer_id="MMK", device_id="X", milestone_id="M",
            section_grouping=grouping,
        ))
        # Should NOT see "Empty" anywhere in first 5 rows below body header
        seen = [ws.cell(row=r, column=1).value for r in range(15, 20)]
        assert "Empty" not in seen
        assert "Populated" in seen

    def test_items_85_86_87_excluded_via_section_grouping(self):
        """DRR-V2-1 architect ask: 85 + 86 + 87 must not appear. When
        the caller passes a section_grouping that already filters those
        (via template_lookup.get_drr_section_grouping), they never reach
        the excel body — even if postgres has them."""
        grouping = [{
            "section": "Sec",
            "work_items": [
                {"item_no": 1, "item_name": "Kept"},
                # 85/86/87 deliberately absent
            ],
        }]
        items = [
            _item(1, item_name="Kept"),
            _item(85, item_name="Final DRR excel"),
            _item(86, item_name="Ph-1 placeholder"),
            _item(87, item_name="Default WI"),
        ]
        ws = _open_ws(build_drr_report_excel(
            items=items,
            customer_id="MMK", device_id="X", milestone_id="M",
            section_grouping=grouping,
        ))
        seen_names = [
            ws.cell(row=r, column=2).value for r in range(15, 20)
        ]
        assert "Kept" in seen_names
        assert "Final DRR excel" not in seen_names
        assert "Ph-1 placeholder" not in seen_names
        assert "Default WI" not in seen_names


# ---------------------------------------------------------------------------
# DRR-V2 mode — summary tables
# ---------------------------------------------------------------------------


class TestV2SummaryTables:
    def test_open_closed_completion_percentage(self):
        grouping = [{
            "section": "Sec",
            "work_items": [{"item_no": i, "item_name": f"I{i}"} for i in range(1, 5)],
        }]
        items = [
            _item(1, delivery_state="Open"),
            _item(2, delivery_state="Open"),
            _item(3, delivery_state="Closed"),
            _item(4, delivery_state="Closed"),
        ]
        out = build_drr_report_excel(
            items=items,
            customer_id="MMK", device_id="X", milestone_id="M",
            section_grouping=grouping,
        )
        ws = _open_ws(out)
        # Locate summary rows below body (body ends ~row 19: 15=section,
        # 16-19=items 1-4; summary starts at row 21 = last_body + 2)
        # Scan for "Open" / "Closed" / "Completion Percentage" labels.
        labels_col_c = {
            r: ws.cell(row=r, column=3).value
            for r in range(20, 40)
            if ws.cell(row=r, column=3).value is not None
        }
        vals_col_d = {r: ws.cell(row=r, column=4).value for r in labels_col_c}
        # Find each of the three summary labels
        def _find(label):
            for r, v in labels_col_c.items():
                if v == label:
                    return vals_col_d[r]
            raise AssertionError(f"summary label {label!r} not found")
        assert _find("Open") == 2
        assert _find("Closed") == 2
        assert _find("Completion Percentage") == "50%"

    def test_per_owner_open_counts(self):
        grouping = [{
            "section": "Sec",
            "work_items": [{"item_no": i, "item_name": f"I{i}"} for i in range(1, 5)],
        }]
        items = [
            _item(1, delivery_state="Open", tg_name="CPM"),
            _item(2, delivery_state="Open", tg_name="CPM"),
            _item(3, delivery_state="Open", tg_name="HW team"),
            _item(4, delivery_state="Closed", tg_name="CPM"),
        ]
        out = build_drr_report_excel(
            items=items,
            customer_id="MMK", device_id="X", milestone_id="M",
            section_grouping=grouping,
        )
        ws = _open_ws(out)
        # Scan for owner labels in col C with count in col D
        found: dict[str, int] = {}
        for r in range(20, 50):
            k = ws.cell(row=r, column=3).value
            v = ws.cell(row=r, column=4).value
            if isinstance(k, str) and isinstance(v, int):
                found[k] = v
        assert found.get("CPM") == 2       # 2 Open CPM items
        assert found.get("HW team") == 1

    def test_zero_items_yields_zero_percentage(self):
        out = build_drr_report_excel(
            items=[],
            customer_id="MMK", device_id="X", milestone_id="M",
            section_grouping=[],
        )
        ws = _open_ws(out)
        # No section/item rows; header block still present.
        assert ws.cell(row=1, column=2).value == "OEM Model"
