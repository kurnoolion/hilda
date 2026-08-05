"""DRR-V2-1 (Ph-1 2026-08-03) — template_lookup DRR-Excel-generation helpers.

Covers `get_drr_version` + `get_drr_section_grouping` — the two additions
that the DRR-V2 final-Excel builder (DRR-V2-5) consumes for header
"DRR Version <N>" rendering and section-grouped row emission.
"""
from __future__ import annotations

import pytest

from core.src.template_schema import template_lookup


@pytest.fixture(autouse=True)
def _clear_cache():
    """Prevent cross-test bleed via the module-level _CACHE dict."""
    template_lookup.clear_cache()
    yield
    template_lookup.clear_cache()


def _seed(customer_id: str, template: dict) -> None:
    """Inject a template into the cache without a yaml round-trip."""
    template_lookup._CACHE[customer_id] = template  # noqa: SLF001


# ---------------------------------------------------------------------------
# get_drr_version
# ---------------------------------------------------------------------------


class TestGetDrrVersion:
    def test_returns_customer_prefixed_version(self):
        _seed("MMK", {"MMK_template_version": "5.7"})
        assert template_lookup.get_drr_version("MMK") == "5.7"

    def test_generic_per_customer_prefix(self):
        """ATT_template_version, VZW_template_version, etc. — the key is
        always `{customer_id}_template_version`."""
        _seed("ATT", {"ATT_template_version": "3.4"})
        _seed("VZW", {"VZW_template_version": "1.0"})
        assert template_lookup.get_drr_version("ATT") == "3.4"
        assert template_lookup.get_drr_version("VZW") == "1.0"

    def test_int_version_stringified(self):
        _seed("MMK", {"MMK_template_version": 6})
        assert template_lookup.get_drr_version("MMK") == "6"

    def test_missing_key_returns_none(self):
        _seed("MMK", {"customer_delivery_info": "http://gdrive"})
        assert template_lookup.get_drr_version("MMK") is None

    def test_empty_value_returns_none(self):
        _seed("MMK", {"MMK_template_version": "   "})
        assert template_lookup.get_drr_version("MMK") is None

    def test_uncached_customer_returns_none(self):
        assert template_lookup.get_drr_version("UNKNOWN") is None

    def test_wrong_customer_prefix_isolated(self):
        """MMK's key must not leak when queried for ATT and vice-versa."""
        _seed("MMK", {"MMK_template_version": "5.7"})
        _seed("ATT", {"MMK_template_version": "5.7"})   # wrong key for ATT
        assert template_lookup.get_drr_version("MMK") == "5.7"
        assert template_lookup.get_drr_version("ATT") is None


# ---------------------------------------------------------------------------
# get_drr_section_grouping
# ---------------------------------------------------------------------------


def _wi(item_no: int, parent: str = "Product Documentation Review",
        item_name: str | None = None, **extras) -> dict:
    d = {"item_no": item_no, "item_name": item_name or f"Item {item_no}",
         "parent": parent}
    d.update(extras)
    return d


def _tmpl_with_drr_items(items: list[dict], devices: list[str] | None = None) -> dict:
    milestone = {"work_items": items}
    if devices is not None:
        milestone["devices"] = devices
    return {"milestones": {"DRR": milestone}}


class TestGetDrrSectionGrouping:
    def test_groups_by_parent_in_item_no_order(self):
        _seed("MMK", _tmpl_with_drr_items([
            _wi(1, parent="Product Documentation Review"),
            _wi(2, parent="Product Documentation Review"),
            _wi(3, parent="Product Documentation Review"),
            _wi(4, parent="Product Documentation Review"),
            _wi(5, parent="Pre-Submission items"),
            _wi(6, parent="Pre-Submission items"),
            _wi(30, parent="Bluetooth Testing"),
        ]))
        groups = template_lookup.get_drr_section_grouping("MMK", "SM-S671U1", "DRR")
        assert groups is not None
        assert [g["section"] for g in groups] == [
            "Product Documentation Review",
            "Pre-Submission items",
            "Bluetooth Testing",
        ]
        assert [wi["item_no"] for wi in groups[0]["work_items"]] == [1, 2, 3, 4]
        assert [wi["item_no"] for wi in groups[1]["work_items"]] == [5, 6]
        assert [wi["item_no"] for wi in groups[2]["work_items"]] == [30]

    def test_out_of_order_input_sorted_by_item_no(self):
        _seed("MMK", _tmpl_with_drr_items([
            _wi(6, parent="Pre-Submission items"),
            _wi(1, parent="Product Documentation Review"),
            _wi(5, parent="Pre-Submission items"),
            _wi(2, parent="Product Documentation Review"),
        ]))
        groups = template_lookup.get_drr_section_grouping("MMK", "SM-S671U1", "DRR")
        assert [g["section"] for g in groups] == [
            "Product Documentation Review",
            "Pre-Submission items",
        ]
        assert [wi["item_no"] for wi in groups[0]["work_items"]] == [1, 2]
        assert [wi["item_no"] for wi in groups[1]["work_items"]] == [5, 6]

    def test_excludes_items_85_86_87_per_architect_ask(self):
        """Item#85 (Final DRR excel) + item#86 (Ph-1-only non-DRR-docs
        placeholder "Stadium, Private Network, Skylo, DR") + item#87
        (Default WI) are NOT part of the Verizon-facing checklist.
        Confirmed spec 2026-08-03 Q1 + 2026-08-04 #86 addition."""
        _seed("MMK", _tmpl_with_drr_items([
            _wi(1, parent="Product Documentation Review"),
            _wi(85, parent="Deliverables to Carrier"),   # excluded
            _wi(86, parent="Ph-1 Placeholder"),          # excluded
            _wi(87, parent="Default"),                   # excluded
        ]))
        groups = template_lookup.get_drr_section_grouping("MMK", "SM-S671U1", "DRR")
        surfaced = {wi["item_no"] for g in groups for wi in g["work_items"]}
        assert surfaced == {1}
        assert 85 not in surfaced
        assert 86 not in surfaced
        assert 87 not in surfaced

    def test_missing_parent_field_still_grouped_as_none_section(self):
        """Missing `parent` is NOT enforced here — DRR-V2-5 excel builder
        fail-louds with the missing-item_nos so the message is context-rich
        (architect ask Q3). Helper returns whatever the template has."""
        _seed("MMK", _tmpl_with_drr_items([
            _wi(1, parent="Product Documentation Review"),
            {"item_no": 2, "item_name": "Missing-parent"},   # no parent
        ]))
        groups = template_lookup.get_drr_section_grouping("MMK", "SM-S671U1", "DRR")
        # Two groups: one under the real section, one under `section=None`
        # (excel builder will refuse the None group with a fail-loud error).
        section_names = [g["section"] for g in groups]
        assert "Product Documentation Review" in section_names
        assert None in section_names

    def test_device_scope_check_returns_none_for_out_of_scope_device(self):
        _seed("MMK", _tmpl_with_drr_items(
            [_wi(1)],
            devices=["SM-S671U1"],
        ))
        assert template_lookup.get_drr_section_grouping(
            "MMK", "SM-OTHER", "DRR",
        ) is None

    def test_missing_customer_returns_none(self):
        assert template_lookup.get_drr_section_grouping(
            "UNKNOWN", "SM-S671U1", "DRR",
        ) is None

    def test_missing_milestone_returns_none(self):
        _seed("MMK", {"milestones": {"OTHER": {"work_items": [_wi(1)]}}})
        assert template_lookup.get_drr_section_grouping(
            "MMK", "SM-S671U1", "DRR",
        ) is None

    def test_empty_work_items_returns_empty_list(self):
        _seed("MMK", _tmpl_with_drr_items([]))
        groups = template_lookup.get_drr_section_grouping("MMK", "SM-S671U1", "DRR")
        assert groups == []

    def test_non_dict_entries_skipped(self):
        """Defensive: a bad yaml with mixed types shouldn't crash."""
        _seed("MMK", _tmpl_with_drr_items([
            _wi(1),
            "bad-string-entry",           # skipped
            42,                           # skipped
            _wi(2),
        ]))
        groups = template_lookup.get_drr_section_grouping("MMK", "SM-S671U1", "DRR")
        surfaced = {wi["item_no"] for g in groups for wi in g["work_items"]}
        assert surfaced == {1, 2}

    def test_non_numeric_item_no_sorts_last(self):
        _seed("MMK", _tmpl_with_drr_items([
            _wi(1),
            {"item_no": "not-a-number", "item_name": "X", "parent": "Extra"},
            _wi(3),
        ]))
        groups = template_lookup.get_drr_section_grouping("MMK", "SM-S671U1", "DRR")
        # numeric items grouped first (item_no order), then non-numeric last
        section_names = [g["section"] for g in groups]
        assert section_names[0] == "Product Documentation Review"
        assert section_names[-1] == "Extra"
