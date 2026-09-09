"""UPLOAD-BUNDLE-1: the submission as a downloadable zip.

The zip's entry paths ARE the manifest's carrier destinations, so opening the
archive shows the exact folder tree a Submit would create on Google Drive.
Nothing recomputes a path here -- duplicating that logic is how a preview
starts lying, which is the failure UPLOAD-PLAN-1 was extracted to prevent.
"""
from __future__ import annotations

import zipfile
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage.upload_bundle import (
    DEFAULT_MAX_BUNDLE_BYTES,
    build_manifest_zip,
    estimate_bundle_bytes,
)


@dataclass
class _Row:
    filename: str
    source_path: str
    doc_type: str = "test_report"
    carrier_destination: str = ""
    excluded_reason: str = ""
    migrated_from: str = ""


@dataclass
class _Item:
    item_no: int
    rows: list = field(default_factory=list)


@dataclass
class _Manifest:
    customer_id: str = "MMK"
    device_id: str = "SM-S671U1"
    milestone_id: str = "P1"
    items: list = field(default_factory=list)


@pytest.fixture(autouse=True)
def nsd_root(tmp_path):
    set_storage_config(GlobalStorageConfig(nsd_mount_root=tmp_path / "nsd"))
    yield tmp_path / "nsd"
    set_storage_config(None)


def _write_source(nsd_root: Path, rel: str, content: bytes = b"payload") -> None:
    p = nsd_root.joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(content)


def _names(zip_path: Path) -> list[str]:
    with zipfile.ZipFile(zip_path) as zf:
        return sorted(zf.namelist())


class TestZipStructure:
    def test_entry_paths_are_the_carrier_destinations(self, nsd_root, tmp_path):
        _write_source(nsd_root, "view/MMK/SM-S671U1/P1/HW PL/battery.pdf")
        m = _Manifest(items=[_Item(10, [
            _Row("battery.pdf", "view/MMK/SM-S671U1/P1/HW PL/battery.pdf",
                 carrier_destination="Feature Test Results/Battery/battery.pdf"),
        ])])
        out = tmp_path / "b.zip"
        r = build_manifest_zip(m, out)
        assert r.files_written == 1
        assert _names(out) == ["Feature Test Results/Battery/battery.pdf"]

    def test_zip_subfolders_are_reproduced(self, nsd_root, tmp_path):
        _write_source(nsd_root, "view/MMK/SM-S671U1/P1/HW PL/DOU/b.pdf")
        m = _Manifest(items=[_Item(10, [
            _Row("b.pdf", "view/MMK/SM-S671U1/P1/HW PL/DOU/b.pdf",
                 carrier_destination="Feature Test Results/Battery/DOU/b.pdf"),
        ])])
        out = tmp_path / "b.zip"
        build_manifest_zip(m, out)
        assert _names(out) == ["Feature Test Results/Battery/DOU/b.pdf"]

    def test_content_is_the_source_bytes(self, nsd_root, tmp_path):
        _write_source(nsd_root, "view/x/a.pdf", b"exact-bytes")
        m = _Manifest(items=[_Item(10, [
            _Row("a.pdf", "view/x/a.pdf", carrier_destination="F/a.pdf"),
        ])])
        out = tmp_path / "b.zip"
        build_manifest_zip(m, out)
        with zipfile.ZipFile(out) as zf:
            assert zf.read("F/a.pdf") == b"exact-bytes"


class TestExclusions:
    def test_excluded_files_are_not_written_but_are_counted(self, nsd_root, tmp_path):
        _write_source(nsd_root, "view/x/w.pdf")
        m = _Manifest(items=[_Item(10, [
            _Row("w.pdf", "view/x/w.pdf", doc_type="waiver",
                 excluded_reason="waiver — never uploaded, any milestone"),
        ])])
        out = tmp_path / "b.zip"
        r = build_manifest_zip(m, out)
        assert r.files_written == 0 and r.files_excluded == 1
        # A README explains the empty archive rather than handing back a
        # mystery zero-entry file.
        assert _names(out) == ["README.txt"]

    def test_mixed_included_and_excluded(self, nsd_root, tmp_path):
        _write_source(nsd_root, "view/x/ok.pdf")
        _write_source(nsd_root, "view/x/w.pdf")
        m = _Manifest(items=[_Item(10, [
            _Row("ok.pdf", "view/x/ok.pdf", carrier_destination="F/ok.pdf"),
            _Row("w.pdf", "view/x/w.pdf", excluded_reason="waiver"),
        ])])
        out = tmp_path / "b.zip"
        r = build_manifest_zip(m, out)
        assert r.files_written == 1 and r.files_excluded == 1
        assert _names(out) == ["F/ok.pdf"]


class TestMissingSources:
    def test_missing_file_is_counted_not_fatal(self, nsd_root, tmp_path):
        _write_source(nsd_root, "view/x/present.pdf")
        m = _Manifest(items=[_Item(10, [
            _Row("gone.pdf", "view/x/gone.pdf", carrier_destination="F/gone.pdf"),
            _Row("present.pdf", "view/x/present.pdf",
                 carrier_destination="F/present.pdf"),
        ])])
        out = tmp_path / "b.zip"
        r = build_manifest_zip(m, out)
        assert r.files_missing == 1 and r.files_written == 1
        assert _names(out) == ["F/present.pdf"]

    def test_malformed_source_path_is_survivable(self, nsd_root, tmp_path):
        m = _Manifest(items=[_Item(10, [
            _Row("x.pdf", "/absolute/not/share/relative",
                 carrier_destination="F/x.pdf"),
        ])])
        out = tmp_path / "b.zip"
        r = build_manifest_zip(m, out)
        assert r.files_missing == 1 and r.files_written == 0


class TestCollisions:
    def test_two_files_to_one_destination_are_both_kept(self, nsd_root, tmp_path):
        """P1 #14 and #20 share a target_folder, so identically-named
        documents genuinely collide. The zip disambiguates; the carrier
        upload would NOT, which is why the collision is reported."""
        _write_source(nsd_root, "view/x/a.pdf", b"one")
        _write_source(nsd_root, "view/x/b.pdf", b"two")
        dest = "RF Parametric Data/Documentation/FCC Package/report.pdf"
        m = _Manifest(items=[
            _Item(14, [_Row("report.pdf", "view/x/a.pdf", carrier_destination=dest)]),
            _Item(20, [_Row("report.pdf", "view/x/b.pdf", carrier_destination=dest)]),
        ])
        out = tmp_path / "b.zip"
        r = build_manifest_zip(m, out)
        assert r.files_written == 2
        assert r.collisions == [dest]
        names = _names(out)
        assert dest in names
        assert any(name != dest and name.endswith(".pdf") for name in names)

    def test_distinct_destinations_are_not_collisions(self, nsd_root, tmp_path):
        _write_source(nsd_root, "view/x/a.pdf")
        _write_source(nsd_root, "view/x/b.pdf")
        m = _Manifest(items=[_Item(10, [
            _Row("a.pdf", "view/x/a.pdf", carrier_destination="F/a.pdf"),
            _Row("b.pdf", "view/x/b.pdf", carrier_destination="F/b.pdf"),
        ])])
        out = tmp_path / "b.zip"
        r = build_manifest_zip(m, out)
        assert r.collisions == [] and r.files_written == 2


class TestSizeCap:
    def test_oversized_bundle_is_refused_before_writing(self, nsd_root, tmp_path):
        _write_source(nsd_root, "view/x/big.pdf", b"x" * 5000)
        m = _Manifest(items=[_Item(10, [
            _Row("big.pdf", "view/x/big.pdf", carrier_destination="F/big.pdf"),
        ])])
        out = tmp_path / "b.zip"
        r = build_manifest_zip(m, out, max_bytes=1000)
        assert r.oversized is True
        assert r.files_written == 0
        assert not out.exists(), "nothing should be written when refused"

    def test_within_cap_proceeds(self, nsd_root, tmp_path):
        _write_source(nsd_root, "view/x/small.pdf", b"x" * 100)
        m = _Manifest(items=[_Item(10, [
            _Row("small.pdf", "view/x/small.pdf", carrier_destination="F/s.pdf"),
        ])])
        out = tmp_path / "b.zip"
        r = build_manifest_zip(m, out, max_bytes=1000)
        assert r.oversized is False and r.files_written == 1

    def test_estimate_counts_only_included_files(self, nsd_root, tmp_path):
        _write_source(nsd_root, "view/x/a.pdf", b"x" * 100)
        _write_source(nsd_root, "view/x/w.pdf", b"x" * 900)
        m = _Manifest(items=[_Item(10, [
            _Row("a.pdf", "view/x/a.pdf", carrier_destination="F/a.pdf"),
            _Row("w.pdf", "view/x/w.pdf", excluded_reason="waiver"),
        ])])
        assert estimate_bundle_bytes(m) == 100

    def test_default_cap_is_sane(self):
        assert DEFAULT_MAX_BUNDLE_BYTES == 2 * 1024 * 1024 * 1024


class TestEmptyManifest:
    def test_empty_manifest_yields_an_explaining_archive(self, tmp_path):
        out = tmp_path / "b.zip"
        r = build_manifest_zip(_Manifest(), out)
        assert r.files_written == 0
        assert _names(out) == ["README.txt"]
        with zipfile.ZipFile(out) as zf:
            assert b"No documents were resolved" in zf.read("README.txt")
