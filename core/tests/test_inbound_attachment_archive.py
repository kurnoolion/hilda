"""D-155 (2026-07-26) — archive-container dispatch helpers in
workflow_engine.tasks.inbound_attachment.

Full pipeline (router+storage+view-tree) is covered end-to-end by the
existing test_workflow_engine_tasks + test_archive_extractor +
test_document_view_writer suites. This file locks the dispatch glue that
lives above the router: extension detection, hash helper, and the
ARCHIVE_CONTAINER audit row for the outer archive.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from core.src.storage.models import RoutingResolution
from core.src.workflow_engine.tasks.inbound_attachment import (
    _is_archive_attachment,
    _sha256_hex,
)


class _StubAttachment:
    def __init__(self, filename, content=b"", file_hash=""):
        self.filename = filename
        self.content = content
        self.content_type = "application/octet-stream"
        self.file_hash = file_hash


class TestIsArchiveAttachment:
    def test_zip_detected(self):
        assert _is_archive_attachment(_StubAttachment("foo.zip")) is True

    def test_7z_detected(self):
        assert _is_archive_attachment(_StubAttachment("foo.7z")) is True

    def test_ooxml_not_archive(self):
        assert _is_archive_attachment(_StubAttachment("foo.xlsx")) is False
        assert _is_archive_attachment(_StubAttachment("foo.docx")) is False

    def test_regular_file_not_archive(self):
        assert _is_archive_attachment(_StubAttachment("report.pdf")) is False

    def test_empty_filename(self):
        assert _is_archive_attachment(_StubAttachment("")) is False


class TestSha256Hex:
    def test_matches_known_hash(self):
        # sha256("") = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
        assert _sha256_hex(b"") == (
            "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_stable_across_calls(self):
        data = b"hilda-archive-dispatch"
        assert _sha256_hex(data) == _sha256_hex(data)

    def test_differs_for_different_content(self):
        assert _sha256_hex(b"a") != _sha256_hex(b"b")


class TestArchiveContainerEnumValue:
    """The outer archive audit row writes routing_resolution=ArchiveContainer.
    Guard the value so a rename in the enum doesn't silently break the
    persisted rows or downstream ops queries filtering on this string."""

    def test_archive_container_string_value_stable(self):
        assert RoutingResolution.ARCHIVE_CONTAINER.value == "ArchiveContainer"


class TestZipRoundTripThroughExtractor:
    """Confirms the extractor path used by _process_archive_attachment
    hands back inner entries in the shape the pipeline expects. Guards
    against future extractor-side breakage that would silently zero-out
    the inner routing loop."""

    def test_flat_zip_yields_expected_entries(self):
        from core.src.storage.archive_extractor import extract_archive
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("a.txt", b"aa")
            zf.writestr("sub/b.txt", b"bb")
        r = extract_archive("pack.zip", buf.getvalue())
        assert r.status == "extracted"
        parts = {e.relative_parts for e in r.entries}
        assert ("a.txt",) in parts
        assert ("sub", "b.txt") in parts
        # Inner-file synthesized filename would be "sub/b.txt" — must
        # split correctly in the view-tree writer.
        inner_filenames = ["/".join(e.relative_parts) for e in r.entries]
        assert "sub/b.txt" in inner_filenames

    def test_7z_yields_expected_entries(self):
        py7zr = pytest.importorskip("py7zr")
        from core.src.storage.archive_extractor import extract_archive
        buf = io.BytesIO()
        with py7zr.SevenZipFile(buf, "w") as z:
            z.writestr(b"aa", "a.txt")
            z.writestr(b"bb", "sub/b.txt")
        r = extract_archive("pack.7z", buf.getvalue())
        assert r.status == "extracted"
        parts = {e.relative_parts for e in r.entries}
        assert ("a.txt",) in parts
        assert ("sub", "b.txt") in parts
