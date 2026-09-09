"""NSD2-6 (2026-08-08) -- unit tests for the NSD2 poller cascade.

Covers:
  * nsd2_resolver.strip_sm_prefix + is_excluded_folder_name + walk_nsd2_directory
  * nsd2_resolver.resolve_nsd2_device_folder for Phone (all 4 model_types)
    + Tablet + Watch + all miss cases
  * nsd2_poll.poll_nsd2_once end-to-end: scope enumeration, HW PL filtering,
    NSD2-root prefix filtering, dedup, ingest hook. Uses monkeypatched
    `_ingest_new_nsd2_file` so we don't drag the real router into these
    tests (router is exercised in test_workflow_engine_tasks + friends).
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest


# ===========================================================================
# nsd2_resolver.strip_sm_prefix + is_excluded_folder_name
# ===========================================================================


class TestStripSmPrefix:
    def test_uppercase_sm_prefix_stripped(self):
        from core.src.storage.nsd2_resolver import strip_sm_prefix
        assert strip_sm_prefix("SM-A015V") == "A015V"

    def test_lowercase_sm_prefix_also_stripped(self):
        from core.src.storage.nsd2_resolver import strip_sm_prefix
        assert strip_sm_prefix("sm-t307u") == "t307u"

    def test_no_prefix_passes_through(self):
        from core.src.storage.nsd2_resolver import strip_sm_prefix
        assert strip_sm_prefix("F721U") == "F721U"

    def test_empty_string(self):
        from core.src.storage.nsd2_resolver import strip_sm_prefix
        assert strip_sm_prefix("") == ""

    def test_whitespace_stripped(self):
        from core.src.storage.nsd2_resolver import strip_sm_prefix
        assert strip_sm_prefix("  SM-A015V  ") == "A015V"


class TestIsExcludedFolderName:
    """Carrier-scoped substring match. Only customer_id='MMK' gets filtered."""

    def test_mmk_excludes_all_configured_substrings(self):
        from core.src.storage.nsd2_resolver import (
            is_excluded_folder_name, MMK_EXCLUDED_FOLDER_SUBSTRINGS,
        )
        # Every substring should independently exclude
        for sub in MMK_EXCLUDED_FOLDER_SUBSTRINGS:
            folder_name = f"Deliverables - {sub} Special"
            assert is_excluded_folder_name(folder_name, "MMK") is True, sub

    def test_mmk_normal_folder_not_excluded(self):
        from core.src.storage.nsd2_resolver import is_excluded_folder_name
        assert is_excluded_folder_name("A015V (A01)",   "MMK") is False
        assert is_excluded_folder_name("Standard Folder", "MMK") is False

    def test_case_insensitive_match(self):
        from core.src.storage.nsd2_resolver import is_excluded_folder_name
        assert is_excluded_folder_name("comcast overrides", "MMK") is True
        assert is_excluded_folder_name("STRATEGIC",         "MMK") is True

    def test_non_mmk_carrier_never_excluded(self):
        from core.src.storage.nsd2_resolver import is_excluded_folder_name
        assert is_excluded_folder_name("Comcast Overrides", "OTHER") is False
        assert is_excluded_folder_name("DISH Config",       "SPRINT") is False
        assert is_excluded_folder_name("Strategic",         "") is False


# ===========================================================================
# nsd2_resolver.resolve_nsd2_device_folder
# ===========================================================================


def _mk_item(**overrides):
    """Build a SimpleNamespace stand-in for a DeliveryItem with the fields
    the resolver reads."""
    defaults = {
        "delivery_item_id": "MMK-X-P1-1",
        "handset":  False,
        "tablet":   False,
        "wearable": False,
        "device_id": "",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def nsd2_tree(tmp_path):
    """Synthetic NSD2 tree covering Phone/Tablet/Watch paths with
    representative model folders for each of the 4 phone prefixes."""
    (tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)").mkdir(parents=True)
    (tmp_path / "Deliverables - Phone" / "S" / "S967U (Galaxy S25 Ultra)").mkdir(parents=True)
    (tmp_path / "Deliverables - Phone" / "Flip,Fold" / "F721U (Flip3)").mkdir(parents=True)
    (tmp_path / "Deliverables - Phone" / "X Cover" / "G789U (Xcover 7)").mkdir(parents=True)
    (tmp_path / "Deliverables - Tablet" / "vendor" / "T307U (Tab A 8.4)").mkdir(parents=True)
    (tmp_path / "Deliverables - Watch" / "series" / "T638U (Active4 Pro)").mkdir(parents=True)
    return tmp_path


class TestResolveNsd2DeviceFolder:

    def test_phone_A_model(self, nsd2_tree):
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(handset=True, device_id="SM-A015V")
        result = resolve_nsd2_device_folder(item, nsd2_tree)
        assert result is not None and result.name == "A015V (A01)"

    def test_phone_S_model(self, nsd2_tree):
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(handset=True, device_id="SM-S967U")
        result = resolve_nsd2_device_folder(item, nsd2_tree)
        assert result is not None and "S967U" in result.name

    def test_phone_F_model_maps_to_flip_fold(self, nsd2_tree):
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(handset=True, device_id="SM-F721U")
        result = resolve_nsd2_device_folder(item, nsd2_tree)
        assert result is not None and "F721U" in result.name
        assert "Flip,Fold" in str(result)

    def test_phone_G_model_maps_to_x_cover(self, nsd2_tree):
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(handset=True, device_id="SM-G789U")
        result = resolve_nsd2_device_folder(item, nsd2_tree)
        assert result is not None and "G789U" in result.name
        assert "X Cover" in str(result)

    def test_tablet_recursive_search(self, nsd2_tree):
        """Tablet has no mid model_type; leaf is found by RECURSIVE search."""
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(tablet=True, device_id="SM-T307U")
        result = resolve_nsd2_device_folder(item, nsd2_tree)
        assert result is not None and "T307U" in result.name

    def test_watch_recursive_search(self, nsd2_tree):
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(wearable=True, device_id="SM-T638U")
        result = resolve_nsd2_device_folder(item, nsd2_tree)
        assert result is not None and "T638U" in result.name

    def test_ambiguous_device_type_returns_none(self, nsd2_tree):
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(handset=True, tablet=True, device_id="SM-A015V")
        assert resolve_nsd2_device_folder(item, nsd2_tree) is None

    def test_no_device_type_returns_none(self, nsd2_tree):
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(device_id="SM-A015V")   # all 3 flags False
        assert resolve_nsd2_device_folder(item, nsd2_tree) is None

    def test_empty_device_id_returns_none(self, nsd2_tree):
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(handset=True, device_id="")
        assert resolve_nsd2_device_folder(item, nsd2_tree) is None

    def test_unknown_phone_prefix_returns_none(self, nsd2_tree):
        """Z is not in PHONE_MODEL_TYPE_FOLDER_MAP; resolver should skip."""
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(handset=True, device_id="SM-Z999X")
        assert resolve_nsd2_device_folder(item, nsd2_tree) is None

    def test_no_matching_leaf_folder_returns_none(self, nsd2_tree):
        """Prefix maps to 'A' folder but no direct sub-folder contains 'A999X'."""
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(handset=True, device_id="SM-A999X")
        assert resolve_nsd2_device_folder(item, nsd2_tree) is None

    def test_missing_device_type_folder_returns_none(self, tmp_path):
        """nsd2_root exists but Deliverables - Phone folder isn't there."""
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(handset=True, device_id="SM-A015V")
        assert resolve_nsd2_device_folder(item, tmp_path) is None

    def test_missing_root_returns_none(self):
        from core.src.storage.nsd2_resolver import resolve_nsd2_device_folder
        item = _mk_item(handset=True, device_id="SM-A015V")
        assert resolve_nsd2_device_folder(item, Path("/does-not-exist-nsd2")) is None


# ===========================================================================
# nsd2_resolver.walk_nsd2_directory
# ===========================================================================


class TestWalkNsd2Directory:

    # NSD2-VZW-1: these run against a FLAT device folder (no carrier
    # partition folder), so the depth-0 allowlist gate stays disengaged and
    # the legacy denylist walk applies -- see TestFlatDeviceFolder.

    def test_happy_path_yields_expected_files(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "a.pdf").write_bytes(b"aaa")
        (tmp_path / "sub").mkdir()
        (tmp_path / "sub" / "b.docx").write_bytes(b"bbb")
        results = list(walk_nsd2_directory(tmp_path, "MMK"))
        rels = sorted(r[0] for r in results)
        assert rels == ["a.pdf", "sub/b.docx"]

    def test_hash_is_sha256_hex(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "x.txt").write_bytes(b"payload-data")
        results = list(walk_nsd2_directory(tmp_path, "MMK"))
        assert len(results) == 1
        _rel, data, sha = results[0]
        assert sha == hashlib.sha256(data).hexdigest()

    def test_deep_excluded_subfolder_pruned(self, tmp_path):
        # Flat layout -> denylist still guards against a foreign carrier
        # folder nested anywhere below.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "Deep" / "DISH Config").mkdir(parents=True)
        (tmp_path / "Deep" / "DISH Config" / "leaf.pdf").write_bytes(b"x")
        (tmp_path / "Deep" / "keep.pdf").write_bytes(b"y")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "MMK"))
        assert rels == ["Deep/keep.pdf"]

    def test_non_mmk_carrier_surfaces_excluded_folders(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "Comcast Overrides").mkdir()
        (tmp_path / "Comcast Overrides" / "x.pdf").write_bytes(b"x")
        rels = [r[0] for r in walk_nsd2_directory(tmp_path, "OTHER")]
        assert "Comcast Overrides/x.pdf" in rels

    def test_oversized_files_skipped(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "small.pdf").write_bytes(b"x" * 100)
        (tmp_path / "big.bin").write_bytes(b"x" * 5000)
        rels = [
            r[0] for r in walk_nsd2_directory(tmp_path, "MMK", max_file_bytes=1000)
        ]
        assert rels == ["small.pdf"]

    def test_oversized_cap_still_applies_inside_allowed_folder(self, tmp_path):
        # The depth-0 allowlist must not disable the size cap below it.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "VZW").mkdir()
        (tmp_path / "VZW" / "small.pdf").write_bytes(b"x" * 100)
        (tmp_path / "VZW" / "big.bin").write_bytes(b"x" * 5000)
        rels = [
            r[0] for r in walk_nsd2_directory(tmp_path, "MMK", max_file_bytes=1000)
        ]
        assert rels == ["VZW/small.pdf"]


# ===========================================================================
# NSD2-VZW-1: carrier root allowlist
# ===========================================================================


class TestCarrierRootAllowlist:
    """Only the contents of an allowlisted top-level folder are ingested.

    Mirrors the real SM-DEVICE-002 (M3) device folder: STG/ and VZW/ side by
    side, plus a loose workbook and a 338 MB 'VZW M3 HW Deliverables.zip'
    that the archive extractor would otherwise fan out.
    """

    @staticmethod
    def _build(tmp_path):
        (tmp_path / "STG").mkdir()
        (tmp_path / "STG" / "stg_internal.xlsx").write_bytes(b"stg")
        (tmp_path / "VZW" / "sub").mkdir(parents=True)
        (tmp_path / "VZW" / "vzw_report.pdf").write_bytes(b"vzw")
        (tmp_path / "VZW" / "sub" / "deep.pdf").write_bytes(b"deep")
        (tmp_path / "Miracle Workbook Final_ver 1.8_1229.xlsx").write_bytes(b"m")
        (tmp_path / "VZW M3 HW Deliverables.zip").write_bytes(b"z")
        return tmp_path

    def test_only_vzw_subtree_is_yielded(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        root = self._build(tmp_path)
        rels = sorted(r[0] for r in walk_nsd2_directory(root, "MMK"))
        assert rels == ["VZW/sub/deep.pdf", "VZW/vzw_report.pdf"]

    def test_stg_sibling_is_skipped(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        root = self._build(tmp_path)
        rels = [r[0] for r in walk_nsd2_directory(root, "MMK")]
        assert not any(r.startswith("STG/") for r in rels)

    def test_loose_root_files_are_skipped(self, tmp_path):
        # Including the zip -- otherwise the extractor fans it out into
        # many leaf documents that were never under VZW/.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        root = self._build(tmp_path)
        rels = [r[0] for r in walk_nsd2_directory(root, "MMK")]
        assert "VZW M3 HW Deliverables.zip" not in rels
        assert "Miracle Workbook Final_ver 1.8_1229.xlsx" not in rels

    def test_unlisted_carrier_folders_are_skipped(self, tmp_path):
        # A denylist could not do this -- ATT/TMO/Sprint were never enumerated.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        for name in ("ATT", "TMO", "Sprint", "USC"):
            (tmp_path / name).mkdir()
            (tmp_path / name / "x.pdf").write_bytes(b"x")
        (tmp_path / "VZW").mkdir()
        (tmp_path / "VZW" / "keep.pdf").write_bytes(b"k")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "MMK"))
        assert rels == ["VZW/keep.pdf"]

    def test_verizon_spelling_also_allowed(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "Verizon").mkdir()
        (tmp_path / "Verizon" / "a.pdf").write_bytes(b"a")
        rels = [r[0] for r in walk_nsd2_directory(tmp_path, "MMK")]
        assert rels == ["Verizon/a.pdf"]

    def test_match_is_case_insensitive(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "vzw").mkdir()
        (tmp_path / "vzw" / "a.pdf").write_bytes(b"a")
        rels = [r[0] for r in walk_nsd2_directory(tmp_path, "MMK")]
        assert rels == ["vzw/a.pdf"]

    def test_match_is_exact_not_substring_so_vzw_se_is_excluded(self, tmp_path):
        # 'VZW SE' is a different carrier scope and was on the old denylist;
        # exact matching keeps it out without needing that denylist.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "VZW SE").mkdir()
        (tmp_path / "VZW SE" / "x.pdf").write_bytes(b"x")
        assert list(walk_nsd2_directory(tmp_path, "MMK")) == []

    def test_no_filtering_below_the_allowed_folder(self, tmp_path):
        # Architect 2026-09-01: "all files under vzw/ folder are uploaded".
        # These four names all match the old 'CHA'/'CCT'/'STG' substrings and
        # would previously have been pruned inside VZW/.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        for name in ("Charging", "Mechanical", "Exchange", "Chart"):
            (tmp_path / "VZW" / name).mkdir(parents=True)
            (tmp_path / "VZW" / name / "f.pdf").write_bytes(b"f")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "MMK"))
        assert rels == [
            "VZW/Charging/f.pdf", "VZW/Chart/f.pdf",
            "VZW/Exchange/f.pdf", "VZW/Mechanical/f.pdf",
        ]

    def test_nested_depth_is_unlimited_inside_allowed_folder(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        deep = tmp_path / "VZW" / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "leaf.pdf").write_bytes(b"x")
        rels = [r[0] for r in walk_nsd2_directory(tmp_path, "MMK")]
        assert rels == ["VZW/a/b/c/d/leaf.pdf"]

    def test_carrier_without_allowlist_is_unchanged(self, tmp_path):
        # NSD-DRM-DECRYPT-1 (2026-09-09): 'VZW M3 HW Deliverables.zip' has no
        # 'decrypt' marker in its stem, so it's filtered as a DRM-wrapped
        # archive on ALL customers (DRM is not customer-scoped). Everything
        # else here is a non-archive file and reaches the yield untouched.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        root = self._build(tmp_path)
        rels = sorted(r[0] for r in walk_nsd2_directory(root, "OTHER"))
        assert rels == [
            "Miracle Workbook Final_ver 1.8_1229.xlsx",
            "STG/stg_internal.xlsx",
            "VZW/sub/deep.pdf",
            "VZW/vzw_report.pdf",
        ]

    def test_anchor_choice_is_logged_at_warning(self, tmp_path, caplog):
        # The old prune logged at INFO, which the containers (WARNING) drop,
        # so files vanished with no production trace. The anchor decision is
        # the auditable record of what was ingested and what was unreachable.
        import logging
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        root = self._build(tmp_path)
        with caplog.at_level(logging.WARNING):
            list(walk_nsd2_directory(root, "MMK"))
        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert "carrier anchor(s)" in blob
        assert "mode=anchored" in blob


class TestFlatDeviceFolder:
    """Not every device folder is carrier-partitioned.

    Real layout for S731U (S25 FE): the numbered deliverable folders sit
    straight under the device model with no VZW/ or STG/ wrapper. Gating
    that on 'VZW' would ingest nothing, so the depth-0 gate only engages
    when a carrier partition is actually present.
    """

    NUMBERED = (
        "1. HW Release notes(done)",
        "2. DMDform(done)",
        "3. HAC Report(done)",
        "4. Water mark indicator(done)",
        "5. Water and Toxic Enforcement-ROH(done)",
        "6. California warranty law(done)",
    )

    def test_flat_layout_ingests_every_numbered_folder(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        for name in self.NUMBERED:
            (tmp_path / name).mkdir()
            (tmp_path / name / "f.pdf").write_bytes(b"x")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "MMK"))
        assert rels == sorted(f"{n}/f.pdf" for n in self.NUMBERED)

    def test_flat_layout_ingests_loose_root_files(self, tmp_path):
        # No partition -> nothing to be "outside", so root files count.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "notes.pdf").write_bytes(b"x")
        (tmp_path / "1. HW Release notes(done)").mkdir()
        rels = [r[0] for r in walk_nsd2_directory(tmp_path, "MMK")]
        assert rels == ["notes.pdf"]

    def test_partition_detected_via_allowlisted_folder(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "VZW").mkdir()
        (tmp_path / "VZW" / "keep.pdf").write_bytes(b"k")
        (tmp_path / "1. HW Release notes(done)").mkdir()
        (tmp_path / "1. HW Release notes(done)" / "drop.pdf").write_bytes(b"d")
        (tmp_path / "loose.xlsx").write_bytes(b"l")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "MMK"))
        assert rels == ["VZW/keep.pdf"]

    def test_denylisted_folder_but_no_anchor_falls_back_and_still_prunes(
        self, tmp_path
    ):
        # STG present, no VZW anywhere -> nonconforming, so fall back rather
        # than ingest nothing. STG's CONTENTS are still kept out by the
        # denylist, which is the property that actually matters.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "STG").mkdir()
        (tmp_path / "STG" / "x.pdf").write_bytes(b"x")
        (tmp_path / "loose.xlsx").write_bytes(b"l")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "MMK"))
        assert rels == ["loose.xlsx"]

    def test_flat_layout_logs_the_fallback(self, tmp_path, caplog):
        import logging
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "1. HW Release notes(done)").mkdir()
        (tmp_path / "1. HW Release notes(done)" / "f.pdf").write_bytes(b"x")
        with caplog.at_level(logging.WARNING):
            list(walk_nsd2_directory(tmp_path, "MMK"))
        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert "NONCONFORMING LAYOUT" in blob


class TestNestedCarrierAnchor:
    """The carrier folder is not always a direct child of the device folder.

    Real layout for F776U (Filp8): device -> Deliverable/ -> VZW/ -> numbered
    folders. A depth-0-only gate treated 'Deliverable' as an ordinary folder,
    disengaged, and leaked 'Deliverable/ATT/' and loose files.
    """

    @staticmethod
    def _build_f776u(tmp_path):
        for n in ("1. HW RN (Done)", "2. DMDform (Done)"):
            (tmp_path / "Deliverable" / "VZW" / n).mkdir(parents=True)
            (tmp_path / "Deliverable" / "VZW" / n / "f.pdf").write_bytes(b"x")
        (tmp_path / "Deliverable" / "STG").mkdir(parents=True)
        (tmp_path / "Deliverable" / "STG" / "i.xlsx").write_bytes(b"s")
        (tmp_path / "Deliverable" / "ATT").mkdir(parents=True)
        (tmp_path / "Deliverable" / "ATT" / "a.pdf").write_bytes(b"a")
        (tmp_path / "Deliverable" / "notes.txt").write_bytes(b"n")
        (tmp_path / "rootloose.txt").write_bytes(b"r")
        return tmp_path

    def test_vzw_one_level_down_is_found(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        root = self._build_f776u(tmp_path)
        rels = sorted(r[0] for r in walk_nsd2_directory(root, "MMK"))
        assert rels == [
            "Deliverable/VZW/1. HW RN (Done)/f.pdf",
            "Deliverable/VZW/2. DMDform (Done)/f.pdf",
        ]

    def test_sibling_carrier_beside_the_anchor_is_unreachable(self, tmp_path):
        # ATT is not on the denylist -- only the anchor keeps it out.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        root = self._build_f776u(tmp_path)
        rels = [r[0] for r in walk_nsd2_directory(root, "MMK")]
        assert not any("ATT" in r for r in rels)
        assert not any("STG" in r for r in rels)
        assert "Deliverable/notes.txt" not in rels
        assert "rootloose.txt" not in rels

    def test_shallowest_anchor_wins(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "VZW").mkdir()
        (tmp_path / "VZW" / "top.pdf").write_bytes(b"t")
        (tmp_path / "Deliverable" / "VZW").mkdir(parents=True)
        (tmp_path / "Deliverable" / "VZW" / "deep.pdf").write_bytes(b"d")
        rels = [r[0] for r in walk_nsd2_directory(tmp_path, "MMK")]
        assert rels == ["VZW/top.pdf"]

    def test_vzw_nested_inside_denylisted_folder_is_never_an_anchor(self, tmp_path):
        # Otherwise 'STG/VZW/' would re-admit the subtree the denylist exists
        # to keep out.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "STG" / "VZW").mkdir(parents=True)
        (tmp_path / "STG" / "VZW" / "leak.pdf").write_bytes(b"l")
        assert list(walk_nsd2_directory(tmp_path, "MMK")) == []

    def test_multiple_anchors_at_same_level_all_ingested(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "VZW").mkdir()
        (tmp_path / "VZW" / "a.pdf").write_bytes(b"a")
        (tmp_path / "Verizon").mkdir()
        (tmp_path / "Verizon" / "b.pdf").write_bytes(b"b")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "MMK"))
        assert rels == ["VZW/a.pdf", "Verizon/b.pdf"]

    def test_anchor_deeper_than_the_cap_is_not_found(self, tmp_path):
        # Guards the flat-fallback boundary: beyond the cap we must not scan
        # forever, and the tree is treated as nonconforming.
        from core.src.storage.nsd2_resolver import (
            walk_nsd2_directory, NSD2_ANCHOR_SEARCH_MAX_DEPTH,
        )
        deep = tmp_path
        for i in range(NSD2_ANCHOR_SEARCH_MAX_DEPTH + 2):
            deep = deep / f"w{i}"
        (deep / "VZW").mkdir(parents=True)
        (deep / "VZW" / "x.pdf").write_bytes(b"x")
        rels = [r[0] for r in walk_nsd2_directory(tmp_path, "MMK")]
        # Falls back to whole-device-folder mode rather than finding it.
        assert rels and rels[0].endswith("x.pdf")

    def test_nonconforming_layout_is_logged_loudly(self, tmp_path, caplog):
        # The fallback is a guess, so it must be visible: the HW PL TG needs
        # a worklist of device folders to normalise.
        import logging
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "1. HW RN (Done)").mkdir()
        (tmp_path / "1. HW RN (Done)" / "f.pdf").write_bytes(b"x")
        with caplog.at_level(logging.WARNING):
            list(walk_nsd2_directory(tmp_path, "MMK"))
        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert "NONCONFORMING LAYOUT" in blob
        assert "mode=whole-device-folder" in blob


class TestFindCarrierAnchors:
    def test_returns_empty_for_carrier_without_allowlist(self, tmp_path):
        from core.src.storage.nsd2_resolver import find_carrier_anchors
        (tmp_path / "VZW").mkdir()
        assert find_carrier_anchors(tmp_path, "OTHER") == ([], False)

    def test_marker_flag_true_when_only_denylisted_folder_present(self, tmp_path):
        from core.src.storage.nsd2_resolver import find_carrier_anchors
        (tmp_path / "STG").mkdir()
        anchors, saw_marker = find_carrier_anchors(tmp_path, "MMK")
        assert anchors == [] and saw_marker is True

    def test_marker_flag_false_for_flat_layout(self, tmp_path):
        from core.src.storage.nsd2_resolver import find_carrier_anchors
        (tmp_path / "1. HW RN (Done)").mkdir()
        anchors, saw_marker = find_carrier_anchors(tmp_path, "MMK")
        assert anchors == [] and saw_marker is False

    def test_missing_root_is_handled(self, tmp_path):
        from core.src.storage.nsd2_resolver import find_carrier_anchors
        assert find_carrier_anchors(tmp_path / "nope", "MMK") == ([], False)


class TestDenylistWholeTokenMatching:
    """NSD2-VZW-1: single-word needles match whole tokens, not substrings.

    The substring form silently pruned plausible HW-deliverable folder
    names, subtree and all, and logged it at INFO so production never saw it.
    """

    def test_cha_no_longer_matches_ordinary_words(self):
        from core.src.storage.nsd2_resolver import is_excluded_folder_name
        for name in ("Charging", "Mechanical", "Exchange", "Chart",
                     "Discharge", "Charging Test Results"):
            assert is_excluded_folder_name(name, "MMK") is False, name

    def test_real_carrier_tokens_still_match(self):
        from core.src.storage.nsd2_resolver import is_excluded_folder_name
        for name in ("CHA", "CCT", "DISH Config", "STG", "TFN Notes",
                     "Comcast Overrides", "STRATEGIC", "Tracfone", "Charter"):
            assert is_excluded_folder_name(name, "MMK") is True, name

    def test_multiword_needle_still_substring_matches(self):
        from core.src.storage.nsd2_resolver import is_excluded_folder_name
        assert is_excluded_folder_name("VZW SE", "MMK") is True
        assert is_excluded_folder_name("Deliverables - VZW SE Special",
                                       "MMK") is True

    def test_california_warranty_law_is_not_excluded(self):
        # From the real S731U device folder.
        from core.src.storage.nsd2_resolver import is_excluded_folder_name
        assert is_excluded_folder_name("6. California warranty law(done)",
                                       "MMK") is False


class TestIsAllowedRootFolder:
    def test_allowlisted_names(self):
        from core.src.storage.nsd2_resolver import is_allowed_root_folder
        for name in ("VZW", "vzw", " VZW ", "Verizon", "verizon"):
            assert is_allowed_root_folder(name, "MMK") is True

    def test_rejected_names(self):
        from core.src.storage.nsd2_resolver import is_allowed_root_folder
        for name in ("STG", "VZW SE", "ATT", "TMO", "VZW2", "", "Verizon SE"):
            assert is_allowed_root_folder(name, "MMK") is False

    def test_carrier_without_allowlist_accepts_everything(self):
        from core.src.storage.nsd2_resolver import is_allowed_root_folder
        for name in ("STG", "anything", ""):
            assert is_allowed_root_folder(name, "OTHER") is True

    def test_allowed_root_folders_returns_none_for_unlisted_carrier(self):
        from core.src.storage.nsd2_resolver import allowed_root_folders
        assert allowed_root_folders("OTHER") is None
        assert allowed_root_folders("MMK") == ("VZW", "Verizon")

    def test_missing_root_yields_empty(self):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        results = list(walk_nsd2_directory(Path("/does-not-exist-abc123"), "MMK"))
        assert results == []


# ===========================================================================
# nsd2_poll.poll_nsd2_once end-to-end
# ===========================================================================


class _StubStorage:
    """Duck-typed storage stub for poller tests."""
    def __init__(self, items=None, existing_hashes=None):
        self._items = items or []
        self._existing = set(existing_hashes or ())

    def list_items_for_milestone(self, milestone_id, states):
        return list(self._items)

    def get_document_index_row_by_hash(self, file_hash):
        if file_hash in self._existing:
            return SimpleNamespace(file_hash=file_hash)
        return None


def _seed_template_cache(customers=None):
    """Overwrite template_lookup._CACHE to control _iter_active_scopes."""
    from core.src.template_schema import template_lookup
    template_lookup._CACHE.clear()
    if customers is None:
        customers = {"MMK": {"devices": {"SM-A015V": {}}, "milestones": {"P1": {}}}}
    for cid, template in customers.items():
        template_lookup._CACHE[cid] = template


def _make_ingest_recorder(monkeypatch):
    """Monkeypatch _ingest_new_nsd2_file to a call-recorder + stop it from
    exercising the real router (which has heavy deps)."""
    calls: list[dict] = []

    def _fake_ingest(**kwargs):
        calls.append(dict(kwargs))

    monkeypatch.setattr(
        "core.src.workflow_engine.tasks.nsd2_poll._ingest_new_nsd2_file",
        _fake_ingest,
    )
    return calls


def _build_hw_pl_item(
    ingress_folder: str = "",   # ignored since NSD2-12; kept for existing test call-sites
    delivery_item_id="MMK-SM-A015V-P1-1",
    tracking_modality=None,     # NSD2-12: opt-in signal replaces ingress_folder
):
    return SimpleNamespace(
        delivery_item_id=delivery_item_id,
        tg_name="HW PL",
        device_id="SM-A015V",
        ingress_folder=ingress_folder,
        tracking_modality=(tracking_modality if tracking_modality is not None
                           else ["NetworkSharedDrive"]),
        handset=True, tablet=False, wearable=False,
        item_no=1, milestone_id="P1",
    )


class TestPollNsd2OnceEndToEnd:

    def test_happy_path_ingests_new_files(self, tmp_path, monkeypatch):
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        # Build the tree
        device_folder = tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)"
        device_folder.mkdir(parents=True)
        (device_folder / "report_a.pdf").write_bytes(b"content-A")
        (device_folder / "sub").mkdir()
        (device_folder / "sub" / "report_b.pdf").write_bytes(b"content-B")

        _seed_template_cache()
        calls = _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_build_hw_pl_item(str(tmp_path))]),
            nsd2_roots=[tmp_path],
        )
        stats = poll_nsd2_once(deps)
        assert stats["outcome"] == "fired"
        assert stats["scopes_scanned"] == 1
        assert stats["hw_pl_items_scanned"] == 1
        assert stats["devices_walked"] == 1
        assert stats["files_yielded"] == 2
        assert stats["files_dedup_skipped"] == 0
        assert stats["files_ingested"] == 2
        # Both files handed to ingest
        assert len(calls) == 2
        filenames = sorted(c["filename"] for c in calls)
        assert filenames == ["report_a.pdf", "sub/report_b.pdf"]

    def test_dedup_skips_files_already_in_document_index(self, tmp_path, monkeypatch):
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        device_folder = tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)"
        device_folder.mkdir(parents=True)
        (device_folder / "already_seen.pdf").write_bytes(b"content-A")
        (device_folder / "new_one.pdf").write_bytes(b"content-B")

        existing_hash = hashlib.sha256(b"content-A").hexdigest()
        _seed_template_cache()
        calls = _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(
                items=[_build_hw_pl_item(str(tmp_path))],
                existing_hashes={existing_hash},
            ),
            nsd2_roots=[tmp_path],
        )
        stats = poll_nsd2_once(deps)
        assert stats["files_yielded"] == 2
        assert stats["files_dedup_skipped"] == 1
        assert stats["files_ingested"] == 1
        assert len(calls) == 1
        assert calls[0]["filename"] == "new_one.pdf"

    def test_no_deps_short_circuits(self):
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once
        stats = poll_nsd2_once(SimpleNamespace(storage=None))
        assert stats["outcome"] == "no_deps"

    def test_no_roots_configured_short_circuits(self, monkeypatch):
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once
        # Neither deps.nsd2_roots nor $HILDA_NSD2_ROOTS -> no-op
        monkeypatch.delenv("HILDA_NSD2_ROOTS", raising=False)
        _seed_template_cache()
        deps = SimpleNamespace(storage=_StubStorage(), nsd2_roots=[])
        stats = poll_nsd2_once(deps)
        assert stats["outcome"] == "no_roots_configured"

    def test_non_hw_pl_items_filtered_out(self, tmp_path, monkeypatch):
        """Only tg_name='HW PL' items should be walked."""
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        device_folder = tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)"
        device_folder.mkdir(parents=True)
        (device_folder / "x.pdf").write_bytes(b"x")

        # 3 items: 1 HW PL (would match), 2 other TGs (should be filtered)
        hw_pl = _build_hw_pl_item(str(tmp_path))
        other1 = _build_hw_pl_item(str(tmp_path), delivery_item_id="MMK-SM-A015V-P1-2")
        other1.tg_name = "MNO-Solution"
        other2 = _build_hw_pl_item(str(tmp_path), delivery_item_id="MMK-SM-A015V-P1-3")
        other2.tg_name = "APPS"

        _seed_template_cache()
        calls = _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(items=[hw_pl, other1, other2]),
            nsd2_roots=[tmp_path],
        )
        stats = poll_nsd2_once(deps)
        assert stats["hw_pl_items_scanned"] == 1   # only 1 of 3 kept
        assert stats["files_ingested"] == 1

    def test_tracking_modality_missing_networksharedrive_filtered_out(self, tmp_path, monkeypatch):
        """NSD2-12: TPM opts a HW PL item into NSD2 polling by adding
        'NetworkSharedDrive' to tracking_modality. Items without it are
        filtered out even when tg_name='HW PL' and device matches."""
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        (tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)").mkdir(parents=True)

        # Item with tg_name=HW PL + matching device but only 'Email' modality
        item = _build_hw_pl_item(tracking_modality=["Email"])
        _seed_template_cache()
        _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(items=[item]),
            nsd2_roots=[tmp_path],
        )
        stats = poll_nsd2_once(deps)
        assert stats["hw_pl_items_scanned"] == 0
        assert stats["files_yielded"] == 0

    def test_tracking_modality_empty_filtered_out(self, tmp_path, monkeypatch):
        """NSD2-12 strict gate: empty tracking_modality is NOT opted in
        (per architect 2026-08-14; P1 not TPM-live yet, no backward compat)."""
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        (tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)").mkdir(parents=True)

        item = _build_hw_pl_item(tracking_modality=[])
        _seed_template_cache()
        _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(items=[item]),
            nsd2_roots=[tmp_path],
        )
        stats = poll_nsd2_once(deps)
        assert stats["hw_pl_items_scanned"] == 0

    def test_tracking_modality_multi_value_includes_nsd(self, tmp_path, monkeypatch):
        """NSD2-12: multi-value tracking_modality (per D-037) works so long
        as 'NetworkSharedDrive' is one of the values."""
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        device_folder = tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)"
        device_folder.mkdir(parents=True)
        (device_folder / "x.pdf").write_bytes(b"x")

        item = _build_hw_pl_item(
            tracking_modality=["Email", "NetworkSharedDrive", "CorporatePLM"],
        )
        _seed_template_cache()
        _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(items=[item]),
            nsd2_roots=[tmp_path],
        )
        stats = poll_nsd2_once(deps)
        assert stats["hw_pl_items_scanned"] == 1
        assert stats["files_ingested"] == 1

    def test_ingress_folder_ignored_since_nsd2_12(self, tmp_path, monkeypatch):
        """Regression: item with a bogus ingress_folder is NOT rejected by
        the filter (NSD2-12 dropped ingress_folder from the HW PL gate).
        Walk base comes from env var / nsd2_roots, not the item."""
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        device_folder = tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)"
        device_folder.mkdir(parents=True)
        (device_folder / "x.pdf").write_bytes(b"x")

        # Bogus / empty ingress_folder should NOT matter -- gate is on modality now
        item = _build_hw_pl_item(
            ingress_folder="/wrong/nonsense/path",
            tracking_modality=["NetworkSharedDrive"],
        )
        _seed_template_cache()
        _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(items=[item]),
            nsd2_roots=[tmp_path],   # <-- walk base is HERE, not from item
        )
        stats = poll_nsd2_once(deps)
        assert stats["hw_pl_items_scanned"] == 1
        assert stats["files_ingested"] == 1

    def test_resolver_miss_skips_item_gracefully(self, tmp_path, monkeypatch):
        """When resolver returns None (folder missing on disk), the device is
        counted as devices_folder_missing + no walk / no ingest.
        NSD2-11: per-device counter renamed from items_folder_missing."""
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        # Do NOT create the device folder tree -> resolver returns None
        _seed_template_cache()
        calls = _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_build_hw_pl_item(str(tmp_path))]),
            nsd2_roots=[tmp_path],
        )
        stats = poll_nsd2_once(deps)
        assert stats["hw_pl_items_scanned"] == 1
        assert stats["devices_folder_missing"] == 1
        assert stats["devices_walked"] == 0
        assert stats["files_ingested"] == 0

    def test_only_p1_milestone_scanned(self, tmp_path, monkeypatch):
        """NSD2-11: `_iter_active_scopes` restricts to milestone_id='P1'.
        Templates with DRR + P1 milestones defined should scan ONLY P1,
        never DRR (or any other milestone name)."""
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        device_folder = tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)"
        device_folder.mkdir(parents=True)
        (device_folder / "p1_report.pdf").write_bytes(b"content-P1")

        # Template exposes BOTH milestones; poller must pick P1 only.
        _seed_template_cache({
            "MMK": {
                "devices":    {"SM-A015V": {}},
                "milestones": {"DRR": {}, "P1": {}},
            }
        })
        calls = _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_build_hw_pl_item(str(tmp_path))]),
            nsd2_roots=[tmp_path],
        )
        stats = poll_nsd2_once(deps)
        # scopes_scanned counts (customer, device) tuples emitted by
        # _iter_active_scopes. With P1-only filter, 1 device × 1 (P1) = 1 scope
        # (not 2 — DRR must be filtered out).
        assert stats["scopes_scanned"] == 1
        assert stats["hw_pl_items_scanned"] == 1
        assert stats["devices_walked"] == 1
        assert stats["files_ingested"] == 1

    def test_no_p1_in_template_yields_zero_scopes(self, tmp_path, monkeypatch):
        """NSD2-11 edge case: a customer template with NO 'P1' milestone
        entry yields zero scopes for that customer (never scanned). Prior
        behavior would have iterated whatever milestones existed."""
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        _seed_template_cache({
            "MMK": {
                "devices":    {"SM-A015V": {}},
                "milestones": {"DRR": {}, "PA1": {}},   # no P1
            }
        })
        calls = _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(items=[_build_hw_pl_item(str(tmp_path))]),
            nsd2_roots=[tmp_path],
        )
        stats = poll_nsd2_once(deps)
        assert stats["scopes_scanned"] == 0
        assert stats["hw_pl_items_scanned"] == 0
        assert stats["devices_walked"] == 0
        assert stats["files_ingested"] == 0

    def test_per_device_single_walk_multi_items(self, tmp_path, monkeypatch):
        """NSD2-11: two HW PL items for the same (customer, device, P1) scope
        must trigger ONE walk (not one per item). Each yielded file is passed
        to _ingest_new_nsd2_file exactly once with BOTH items as candidates."""
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        device_folder = tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)"
        device_folder.mkdir(parents=True)
        (device_folder / "shared.pdf").write_bytes(b"content-shared")

        item1 = _build_hw_pl_item(str(tmp_path), delivery_item_id="MMK-SM-A015V-P1-1")
        item2 = _build_hw_pl_item(str(tmp_path), delivery_item_id="MMK-SM-A015V-P1-2")
        _seed_template_cache()
        calls = _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(items=[item1, item2]),
            nsd2_roots=[tmp_path],
        )
        stats = poll_nsd2_once(deps)
        assert stats["hw_pl_items_scanned"] == 2       # both items count as eligible
        assert stats["devices_walked"] == 1            # one walk (per-device, not per-item)
        assert stats["files_yielded"] == 1             # one file on disk
        assert stats["files_ingested"] == 1            # ingest called once, not twice
        assert len(calls) == 1
        # Router receives BOTH items as candidates -- lets router match by filename.
        candidate_items = calls[0]["items"]
        assert len(candidate_items) == 2
        candidate_ids = {getattr(it, "delivery_item_id", None) for it in candidate_items}
        assert candidate_ids == {"MMK-SM-A015V-P1-1", "MMK-SM-A015V-P1-2"}


# ===========================================================================
# NSD-DRM-DECRYPT-1 (2026-09-09): walk skips DRM-wrapped archives whose stem
# lacks the 'decrypt' marker. Applies to ALL customers; DRM is not
# customer-scoped.
# ===========================================================================


class TestIsDrmWrappedArchive:
    """The stem-marker predicate. Substring match, case-insensitive."""

    @pytest.mark.parametrize("name", [
        "foo.zip", "report.7z", "bundle.rar",
        "FOO.ZIP", "MIXED_case.Zip",
        "release notes final.zip",
    ])
    def test_archive_without_decrypt_stem_is_skipped(self, name: str) -> None:
        from core.src.storage.nsd2_resolver import _is_drm_wrapped_archive
        assert _is_drm_wrapped_archive(name) is True

    @pytest.mark.parametrize("name", [
        "foo_decrypt.zip", "foo.decrypt.zip", "decrypt-final.7z",
        "DECRYPT_bundle.RAR", "release_notes_decrypt.zip",
        "prefix-DeCrYpT-suffix.zip",
    ])
    def test_archive_with_decrypt_stem_passes(self, name: str) -> None:
        from core.src.storage.nsd2_resolver import _is_drm_wrapped_archive
        assert _is_drm_wrapped_archive(name) is False

    @pytest.mark.parametrize("name", [
        "foo.pdf", "spec.xlsx", "notes.txt", "readme",
        "no_extension_at_all",
    ])
    def test_non_archive_extensions_always_pass(self, name: str) -> None:
        from core.src.storage.nsd2_resolver import _is_drm_wrapped_archive
        # Non-archives never trigger the filter -- they're not DRM candidates.
        assert _is_drm_wrapped_archive(name) is False

    def test_extension_only_no_stem_is_skipped(self) -> None:
        from core.src.storage.nsd2_resolver import _is_drm_wrapped_archive
        # '.zip' with no stem -- pathological but not decrypt-marked.
        assert _is_drm_wrapped_archive(".zip") is True

    def test_empty_string_passes(self) -> None:
        from core.src.storage.nsd2_resolver import _is_drm_wrapped_archive
        assert _is_drm_wrapped_archive("") is False


class TestWalkSkipsDrmWrappedArchives:
    """End-to-end walk-level integration. Files landing inside the allowed
    carrier subtree still get filtered."""

    def test_plain_zip_inside_vzw_is_skipped(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "VZW").mkdir()
        (tmp_path / "VZW" / "release_notes.zip").write_bytes(b"encrypted")
        (tmp_path / "VZW" / "release_notes_decrypt.zip").write_bytes(b"decrypted")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "MMK"))
        assert rels == ["VZW/release_notes_decrypt.zip"]

    def test_all_customers_get_filter_not_just_mmk(self, tmp_path):
        # Non-carrier-allowlist customer walks the whole tree; DRM filter
        # still fires there.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "foo.zip").write_bytes(b"encrypted")
        (tmp_path / "foo_decrypt.zip").write_bytes(b"decrypted")
        (tmp_path / "keep.pdf").write_bytes(b"pdf")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "OTHER"))
        assert rels == ["foo_decrypt.zip", "keep.pdf"]

    def test_pdf_and_xlsx_untouched(self, tmp_path):
        # Non-archive extensions must not be caught by the DRM filter.
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "VZW").mkdir()
        (tmp_path / "VZW" / "spec.pdf").write_bytes(b"a")
        (tmp_path / "VZW" / "matrix.xlsx").write_bytes(b"b")
        (tmp_path / "VZW" / "notes.txt").write_bytes(b"c")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "MMK"))
        assert rels == ["VZW/matrix.xlsx", "VZW/notes.txt", "VZW/spec.pdf"]

    def test_seven_zip_and_rar_also_filtered(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "VZW").mkdir()
        (tmp_path / "VZW" / "bundle.7z").write_bytes(b"e")
        (tmp_path / "VZW" / "bundle_decrypt.7z").write_bytes(b"d")
        (tmp_path / "VZW" / "archive.rar").write_bytes(b"e")
        (tmp_path / "VZW" / "decrypt-archive.rar").write_bytes(b"d")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "MMK"))
        assert rels == [
            "VZW/bundle_decrypt.7z",
            "VZW/decrypt-archive.rar",
        ]

    def test_decrypt_zip_at_any_depth_kept(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "VZW" / "Audio").mkdir(parents=True)
        (tmp_path / "VZW" / "Audio" / "report.zip").write_bytes(b"e")
        (tmp_path / "VZW" / "Audio" / "report_decrypt.zip").write_bytes(b"d")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "MMK"))
        assert rels == ["VZW/Audio/report_decrypt.zip"]
