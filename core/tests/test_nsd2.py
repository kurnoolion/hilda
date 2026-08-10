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

    def test_mmk_exclusion_prunes_subtree(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "keep.pdf").write_bytes(b"keep")
        (tmp_path / "Comcast Overrides").mkdir()
        (tmp_path / "Comcast Overrides" / "drop1.pdf").write_bytes(b"drop1")
        (tmp_path / "Comcast Overrides" / "sub" / "drop2.pdf").parent.mkdir(parents=True)
        (tmp_path / "Comcast Overrides" / "sub" / "drop2.pdf").write_bytes(b"drop2")
        rels = sorted(r[0] for r in walk_nsd2_directory(tmp_path, "MMK"))
        assert rels == ["keep.pdf"]

    def test_deep_excluded_subfolder_pruned(self, tmp_path):
        from core.src.storage.nsd2_resolver import walk_nsd2_directory
        (tmp_path / "Deep" / "DISH Config" / "leaf.pdf").parent.mkdir(parents=True)
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
        rels = [r[0] for r in walk_nsd2_directory(tmp_path, "MMK", max_file_bytes=1000)]
        assert rels == ["small.pdf"]

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


def _build_hw_pl_item(ingress_folder: str, delivery_item_id="MMK-SM-A015V-P1-1"):
    return SimpleNamespace(
        delivery_item_id=delivery_item_id,
        tg_name="HW PL",
        device_id="SM-A015V",
        ingress_folder=ingress_folder,
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
        assert stats["items_walked"] == 1
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

    def test_ingress_folder_not_under_nsd2_root_filtered_out(self, tmp_path, monkeypatch):
        """Item's ingress_folder must start with a configured NSD2 root."""
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        (tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)").mkdir(parents=True)

        # Item points at a DIFFERENT root path (NSD1) -- should be filtered
        wrong_root_item = _build_hw_pl_item("/some/other/nsd1/root")
        _seed_template_cache()
        calls = _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(items=[wrong_root_item]),
            nsd2_roots=[tmp_path],
        )
        stats = poll_nsd2_once(deps)
        assert stats["hw_pl_items_scanned"] == 0
        assert stats["files_yielded"] == 0

    def test_windows_backslash_ingress_folder_matches(self, tmp_path, monkeypatch):
        """Ingress folder value from SP may carry backslash separators;
        prefix match must be tolerant of separator + case round-trips."""
        from core.src.workflow_engine.tasks.nsd2_poll import poll_nsd2_once

        (tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)").mkdir(parents=True)
        (tmp_path / "Deliverables - Phone" / "A" / "A015V (A01)" / "x.pdf").write_bytes(b"x")

        # Item ingress_folder uses backslashes (Windows SP-native shape).
        # We fake a mixed-case backslash-heavy variant of the real root.
        windows_shaped = str(tmp_path).upper().replace("/", "\\")
        item = _build_hw_pl_item(windows_shaped)
        _seed_template_cache()
        calls = _make_ingest_recorder(monkeypatch)
        deps = SimpleNamespace(
            storage=_StubStorage(items=[item]),
            nsd2_roots=[tmp_path],
        )
        stats = poll_nsd2_once(deps)
        assert stats["hw_pl_items_scanned"] == 1
        assert stats["files_ingested"] == 1

    def test_resolver_miss_skips_item_gracefully(self, tmp_path, monkeypatch):
        """When resolver returns None (folder missing on disk), item is
        counted as items_folder_missing + no walk / no ingest."""
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
        assert stats["items_folder_missing"] == 1
        assert stats["items_walked"] == 0
        assert stats["files_ingested"] == 0
