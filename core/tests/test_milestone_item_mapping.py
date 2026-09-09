"""DRRP1-1 (2026-09-01) -- cross-milestone work-item mapping loader.

Documents collected against DRR work-items are submitted as part of P1. DRR
never uploads on its own path, so a dropped or misread pair means documents that
are collected and then never delivered -- hence the emphasis here on validation
rejecting bad input LOUDLY while keeping the rest of the file usable.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core.src.template_schema import milestone_item_mapping as mim

CUST = "MMK"


@pytest.fixture(autouse=True)
def clean_cache():
    mim.clear_cache()
    yield
    mim.clear_cache()


def _write(tmp_path: Path, data, customer: str = CUST) -> Path:
    d = tmp_path / customer
    d.mkdir(parents=True, exist_ok=True)
    p = d / mim.MAPPING_FILENAME
    if isinstance(data, str):
        p.write_text(data, encoding="utf-8")
    else:
        p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


def _simple(pairs: dict, src="DRR", dst="P1") -> dict:
    return {"mappings": [
        {"source_milestone": src, "target_milestone": dst, "pairs": pairs},
    ]}


# ---------------------------------------------------------------------------
# The real shipped file
# ---------------------------------------------------------------------------


_MMK_MAPPING_PRESENT = (
    mim._default_base_dir() / CUST / mim.MAPPING_FILENAME
).is_file()


@pytest.mark.skipif(
    not _MMK_MAPPING_PRESENT,
    reason=(
        "MMK milestone_item_mapping.yaml absent -- carrier config is not "
        "published to the public mirror. SKIP rather than mock: a mock "
        "mapping would make this class stop guarding the real shipped file, "
        "which is its only purpose."
    ),
)
class TestShippedMmkMapping:
    """Guards the actual customizations file, so a hand-edit that breaks a pair
    fails here rather than at a carrier submission."""

    @pytest.fixture(autouse=True)
    def loaded(self):
        assert mim.load_customer_mapping(CUST) is True

    def test_loads_one_drr_to_p1_block(self):
        blocks = mim.get_mapping_blocks(CUST)
        assert len(blocks) == 1
        assert blocks[0].source_milestone == "DRR"
        assert blocks[0].target_milestone == "P1"

    def test_has_all_26_pairs(self):
        # 24 at DRRP1-1 (2026-09-01); +1 for DRR-79-P1 (2026-09-04), the
        # upload-only target added because SP does not expose
        # Submit-to-Carrier for DRR; +1 for 60 -> 23 (2026-09-06), the first
        # fan-in pair.
        assert len(mim.get_mapping_blocks(CUST)[0].pairs) == 26

    def test_mno_iot_fan_in_survives_the_load(self):
        """DRR #35 and #60 both feed P1 #23. Regression guard: the loader used
        to keep only the first, so DRR #60's documents were collected and then
        never uploaded, with nothing but a startup WARN to say so."""
        assert mim.get_source_item_nos(
            customer_id=CUST, target_milestone="P1", target_item_no=23,
        ) == [("DRR", 35), ("DRR", 60)]

    @pytest.mark.parametrize(
        "source_no,target_no",
        [
            (77, 2), (50, 10), (52, 7), (56, 15), (61, 9), (64, 18),
            (67, 20), (68, 113), (69, 14), (74, 17), (75, 16), (13, 5),
            (53, 4), (35, 23), (60, 23), (34, 26), (5, 30), (7, 29), (36, 78),
            (58, 80), (54, 86), (14, 105), (55, 106), (62, 103), (63, 104),
            (79, 114),
        ],
    )
    def test_each_pair_resolves_both_directions(self, source_no, target_no):
        assert mim.get_target_item_no(
            customer_id=CUST, source_milestone="DRR", source_item_no=source_no,
        ) == ("P1", target_no)
        # Membership, not equality: P1 #23 is fed by two sources, so the
        # reverse direction is a list.
        assert ("DRR", source_no) in mim.get_source_item_nos(
            customer_id=CUST, target_milestone="P1", target_item_no=target_no,
        )

    def test_unmapped_drr_items_stay_unmapped(self):
        """#70 (intrinsically safe) and #76 (UN38.3) are closed by TPM/HW PL
        directly and must not resolve to a P1 target."""
        for no in (70, 76):
            assert mim.get_target_item_no(
                customer_id=CUST, source_milestone="DRR", source_item_no=no,
            ) is None

    def test_every_pair_exists_in_the_template(self):
        """The mapping is only meaningful if both work-items are real. Cross-
        checks against template.yaml so a renumbered template surfaces here."""
        from core.src.template_schema import template_lookup
        template_lookup.clear_cache()
        assert template_lookup.load_customer_template(CUST) is True
        tpl = template_lookup._CACHE[CUST]

        def item_nos(milestone: str) -> set[int]:
            wis = (tpl["milestones"].get(milestone) or {}).get("work_items") or []
            return {
                int(w["item_no"]) for w in wis
                if isinstance(w, dict) and w.get("item_no") is not None
            }

        drr_nos, p1_nos = item_nos("DRR"), item_nos("P1")
        block = mim.get_mapping_blocks(CUST)[0]
        missing_src = sorted(s for s in block.pairs if s not in drr_nos)
        missing_dst = sorted(t for t in block.pairs.values() if t not in p1_nos)
        assert missing_src == [], f"DRR item_no not in template: {missing_src}"
        assert missing_dst == [], f"P1 item_no not in template: {missing_dst}"


# ---------------------------------------------------------------------------
# Lookups
# ---------------------------------------------------------------------------


class TestLookups:
    def test_unknown_customer_yields_no_blocks(self):
        assert mim.get_mapping_blocks("NOPE") == []
        assert mim.get_source_item_no(
            customer_id="NOPE", target_milestone="P1", target_item_no=2,
        ) is None

    def test_lookup_is_scoped_to_the_named_milestone(self, tmp_path):
        _write(tmp_path, _simple({1: 2}))
        mim.load_customer_mapping(CUST, tmp_path / CUST / mim.MAPPING_FILENAME)
        # Right route resolves; a different target milestone does not.
        assert mim.get_source_item_no(
            customer_id=CUST, target_milestone="P1", target_item_no=2,
        ) == ("DRR", 1)
        assert mim.get_source_item_no(
            customer_id=CUST, target_milestone="PA1", target_item_no=2,
        ) is None

    def test_multiple_blocks_are_independent(self, tmp_path):
        _write(tmp_path, {"mappings": [
            {"source_milestone": "DRR", "target_milestone": "P1",
             "pairs": {1: 10}},
            {"source_milestone": "DRR", "target_milestone": "PA1",
             "pairs": {1: 20}},
        ]})
        mim.load_customer_mapping(CUST, tmp_path / CUST / mim.MAPPING_FILENAME)
        assert len(mim.get_mapping_blocks(CUST)) == 2
        assert mim.get_source_item_no(
            customer_id=CUST, target_milestone="P1", target_item_no=10,
        ) == ("DRR", 1)
        assert mim.get_source_item_no(
            customer_id=CUST, target_milestone="PA1", target_item_no=20,
        ) == ("DRR", 1)

    def test_string_item_numbers_are_coerced(self, tmp_path):
        _write(tmp_path, "mappings:\n  - source_milestone: DRR\n"
                         "    target_milestone: P1\n    pairs:\n      '77': '2'\n")
        mim.load_customer_mapping(CUST, tmp_path / CUST / mim.MAPPING_FILENAME)
        assert mim.get_source_item_no(
            customer_id=CUST, target_milestone="P1", target_item_no=2,
        ) == ("DRR", 77)


# ---------------------------------------------------------------------------
# Validation -- bad input must not take the whole file down
# ---------------------------------------------------------------------------


class TestValidation:
    def test_absent_file_is_not_an_error(self, tmp_path):
        ok = mim.load_customer_mapping(
            CUST, tmp_path / CUST / mim.MAPPING_FILENAME,
        )
        assert ok is True                       # most carriers have no mapping
        assert mim.get_mapping_blocks(CUST) == []

    def test_empty_file_yields_no_blocks(self, tmp_path):
        _write(tmp_path, "")
        assert mim.load_customer_mapping(
            CUST, tmp_path / CUST / mim.MAPPING_FILENAME) is True
        assert mim.get_mapping_blocks(CUST) == []

    def test_malformed_yaml_fails_closed(self, tmp_path):
        _write(tmp_path, "mappings: [unclosed\n")
        assert mim.load_customer_mapping(
            CUST, tmp_path / CUST / mim.MAPPING_FILENAME) is False
        assert mim.get_mapping_blocks(CUST) == []

    def test_missing_mappings_key_fails_closed(self, tmp_path):
        _write(tmp_path, {"something_else": []})
        assert mim.load_customer_mapping(
            CUST, tmp_path / CUST / mim.MAPPING_FILENAME) is False
        assert mim.get_mapping_blocks(CUST) == []

    def test_self_mapping_is_rejected(self, tmp_path):
        _write(tmp_path, _simple({1: 2}, src="P1", dst="P1"))
        mim.load_customer_mapping(CUST, tmp_path / CUST / mim.MAPPING_FILENAME)
        assert mim.get_mapping_blocks(CUST) == []

    def test_missing_milestone_names_rejected(self, tmp_path):
        _write(tmp_path, {"mappings": [{"pairs": {1: 2}}]})
        mim.load_customer_mapping(CUST, tmp_path / CUST / mim.MAPPING_FILENAME)
        assert mim.get_mapping_blocks(CUST) == []

    def test_duplicate_route_keeps_the_first(self, tmp_path):
        _write(tmp_path, {"mappings": [
            {"source_milestone": "DRR", "target_milestone": "P1",
             "pairs": {1: 10}},
            {"source_milestone": "DRR", "target_milestone": "P1",
             "pairs": {2: 20}},
        ]})
        mim.load_customer_mapping(CUST, tmp_path / CUST / mim.MAPPING_FILENAME)
        blocks = mim.get_mapping_blocks(CUST)
        assert len(blocks) == 1
        assert blocks[0].pairs == {1: 10}

    def test_two_sources_may_feed_one_target(self, tmp_path):
        """Fan-in is legitimate: a TG can track two source work-items
        separately and deliver both into one carrier folder. Both pairs must
        survive -- dropping one silently strips those documents from the
        submission."""
        _write(tmp_path, "mappings:\n  - source_milestone: DRR\n"
                         "    target_milestone: P1\n    pairs:\n"
                         "      50: 10\n      51: 10\n")
        mim.load_customer_mapping(CUST, tmp_path / CUST / mim.MAPPING_FILENAME)
        block = mim.get_mapping_blocks(CUST)[0]
        assert block.pairs == {50: 10, 51: 10}
        assert block.reverse == {10: [50, 51]}

    def test_fan_in_is_reported_in_mapping_file_order(self, tmp_path):
        _write(tmp_path, "mappings:\n  - source_milestone: DRR\n"
                         "    target_milestone: P1\n    pairs:\n"
                         "      51: 10\n      50: 10\n")
        mim.load_customer_mapping(CUST, tmp_path / CUST / mim.MAPPING_FILENAME)
        assert mim.get_source_item_nos(
            customer_id=CUST, target_milestone="P1", target_item_no=10,
        ) == [("DRR", 51), ("DRR", 50)]

    def test_forward_direction_stays_single_valued(self, tmp_path):
        """One source cannot deliver to two targets -- the second is dropped."""
        _write(tmp_path, "mappings:\n  - source_milestone: DRR\n"
                         "    target_milestone: P1\n    pairs:\n"
                         "      50: 10\n      '50': 11\n")
        mim.load_customer_mapping(CUST, tmp_path / CUST / mim.MAPPING_FILENAME)
        assert mim.get_mapping_blocks(CUST)[0].pairs == {50: 10}

    def test_non_integer_pair_is_dropped_but_siblings_survive(self, tmp_path):
        """A typo in one line must not disable delivery for the rest."""
        _write(tmp_path, "mappings:\n  - source_milestone: DRR\n"
                         "    target_milestone: P1\n    pairs:\n"
                         "      50: 10\n      notanumber: 11\n      52: 7\n")
        mim.load_customer_mapping(CUST, tmp_path / CUST / mim.MAPPING_FILENAME)
        assert mim.get_mapping_blocks(CUST)[0].pairs == {50: 10, 52: 7}

    def test_block_with_no_usable_pairs_is_skipped(self, tmp_path):
        _write(tmp_path, "mappings:\n  - source_milestone: DRR\n"
                         "    target_milestone: P1\n    pairs:\n"
                         "      bad: alsobad\n")
        mim.load_customer_mapping(CUST, tmp_path / CUST / mim.MAPPING_FILENAME)
        assert mim.get_mapping_blocks(CUST) == []

    def test_pairs_not_a_mapping_is_skipped(self, tmp_path):
        _write(tmp_path, {"mappings": [
            {"source_milestone": "DRR", "target_milestone": "P1",
             "pairs": [50, 10]},
        ]})
        mim.load_customer_mapping(CUST, tmp_path / CUST / mim.MAPPING_FILENAME)
        assert mim.get_mapping_blocks(CUST) == []


class TestLoadAll:
    def test_walks_customer_directories(self, tmp_path):
        _write(tmp_path, _simple({1: 2}), customer="AAA")
        _write(tmp_path, _simple({3: 4}), customer="BBB")
        (tmp_path / "CCC").mkdir()          # no mapping file -> skipped entirely
        results = mim.load_all_mappings(tmp_path)
        assert results == {"AAA": True, "BBB": True}
        assert mim.get_mapping_blocks("AAA")[0].pairs == {1: 2}
        assert mim.get_mapping_blocks("CCC") == []

    def test_missing_base_dir_is_not_fatal(self, tmp_path):
        assert mim.load_all_mappings(tmp_path / "nope") == {}
