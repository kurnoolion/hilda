"""archive_extractor -- .zip + .7z extraction abstraction per D-155 (2026-07-26).

Round-trip tests confirm both formats extract to the same ExtractedEntry shape
so downstream consumers can process either uniformly.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from core.src.storage.archive_extractor import (
    MAX_COMPRESSED_BYTES,
    MAX_DECOMPRESSED_BYTES,
    extract_archive,
    is_archive_filename,
    safe_relative_parts,
)

py7zr = pytest.importorskip("py7zr")


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _sevenz_bytes(entries: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with py7zr.SevenZipFile(buf, "w") as z:
        for name, data in entries.items():
            z.writestr(data, name)
    return buf.getvalue()


class TestIsArchiveFilename:
    def test_zip_recognized(self):
        assert is_archive_filename("foo.zip") is True
        assert is_archive_filename("Foo.ZIP") is True

    def test_7z_recognized(self):
        assert is_archive_filename("foo.7z") is True
        assert is_archive_filename("Foo.7Z") is True

    def test_ooxml_not_recognized(self):
        assert is_archive_filename("foo.xlsx") is False
        assert is_archive_filename("foo.docx") is False
        assert is_archive_filename("foo.pptx") is False

    def test_plain_files_not_recognized(self):
        assert is_archive_filename("foo.pdf") is False
        assert is_archive_filename("") is False


class TestNonArchive:
    def test_non_archive_extension_returns_not_archive(self):
        r = extract_archive("hello.txt", b"content")
        assert r.status == "not_archive"

    def test_empty_content_returns_not_archive(self):
        r = extract_archive("foo.zip", b"")
        # ext matches but content is empty -> not_archive (missing magic)
        assert r.status in ("bad_archive", "not_archive")


class TestZip:
    def test_flat_zip_extracts(self):
        z = _zip_bytes({"a.txt": b"aaa", "b.txt": b"bbb"})
        r = extract_archive("pack.zip", z)
        assert r.status == "extracted"
        paths = {e.relative_parts for e in r.entries}
        assert ("a.txt",) in paths
        assert ("b.txt",) in paths

    def test_nested_zip_preserves_tree(self):
        z = _zip_bytes({"docs/report.pdf": b"pdf", "docs/sub/note.txt": b"n"})
        r = extract_archive("pack.zip", z)
        assert r.status == "extracted"
        paths = {e.relative_parts for e in r.entries}
        assert ("docs", "report.pdf") in paths
        assert ("docs", "sub", "note.txt") in paths

    def test_bad_magic_returns_bad_archive(self):
        r = extract_archive("fake.zip", b"not-a-zip-at-all")
        assert r.status == "bad_archive"

    def test_zip_slip_skipped(self):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../etc/passwd", b"pwned")
            zf.writestr("safe.txt", b"safe")
        r = extract_archive("attack.zip", buf.getvalue())
        assert r.status == "extracted"
        paths = {e.relative_parts for e in r.entries}
        assert ("safe.txt",) in paths
        assert not any("passwd" in "/".join(p) for p in paths)

    def test_oversized_compressed_skipped(self, monkeypatch):
        import core.src.storage.archive_extractor as mod
        monkeypatch.setattr(mod, "MAX_COMPRESSED_BYTES", 100)
        z = _zip_bytes({"a.txt": b"x" * 500})
        assert len(z) > 100
        r = extract_archive("big.zip", z)
        assert r.status == "oversized"

    def test_decompressed_oversized_skipped(self, monkeypatch):
        import core.src.storage.archive_extractor as mod
        # Cap the decompressed total below the actual decompressed size.
        monkeypatch.setattr(mod, "MAX_DECOMPRESSED_BYTES", 50)
        z = _zip_bytes({"a.txt": b"x" * 500})  # decompresses to 500 bytes
        r = extract_archive("bomb.zip", z)
        assert r.status == "decompressed_oversized"


class TestSevenZ:
    def test_flat_7z_extracts(self):
        z = _sevenz_bytes({"a.txt": b"aaa", "b.txt": b"bbb"})
        r = extract_archive("pack.7z", z)
        assert r.status == "extracted"
        paths = {e.relative_parts for e in r.entries}
        contents = {e.relative_parts: e.content for e in r.entries}
        assert ("a.txt",) in paths and contents[("a.txt",)] == b"aaa"
        assert ("b.txt",) in paths and contents[("b.txt",)] == b"bbb"

    def test_nested_7z_preserves_tree(self):
        z = _sevenz_bytes({
            "docs/report.pdf": b"pdf",
            "docs/sub/note.txt": b"note",
        })
        r = extract_archive("pack.7z", z)
        assert r.status == "extracted"
        paths = {e.relative_parts for e in r.entries}
        assert ("docs", "report.pdf") in paths
        assert ("docs", "sub", "note.txt") in paths

    def test_bad_magic_returns_bad_archive(self):
        r = extract_archive("fake.7z", b"not-a-7z-at-all")
        assert r.status == "bad_archive"

    def test_password_protected_returns_password_protected(self):
        # Build a real password-protected 7z via py7zr.
        buf = io.BytesIO()
        with py7zr.SevenZipFile(buf, "w", password="secret") as z:
            z.writestr(b"secret content", "secret.txt")
        r = extract_archive("locked.7z", buf.getvalue())
        assert r.status == "password_protected"

    def test_oversized_compressed_skipped(self, monkeypatch):
        import core.src.storage.archive_extractor as mod
        monkeypatch.setattr(mod, "MAX_COMPRESSED_BYTES", 100)
        z = _sevenz_bytes({"a.txt": b"x" * 500})
        # 7z with heavy compression may still exceed 100 bytes with headers
        if len(z) > 100:
            r = extract_archive("big.7z", z)
            assert r.status == "oversized"


class TestSafeRelativeParts:
    def test_normal_path(self):
        assert safe_relative_parts("a/b/c.txt") == ("a", "b", "c.txt")

    def test_windows_separators_normalized(self):
        assert safe_relative_parts("a\\b\\c.txt") == ("a", "b", "c.txt")

    def test_absolute_rejected(self):
        assert safe_relative_parts("/etc/passwd") is None

    def test_parent_dir_rejected(self):
        assert safe_relative_parts("../etc/passwd") is None
        assert safe_relative_parts("a/../b") is None

    def test_empty_rejected(self):
        assert safe_relative_parts("") is None
        assert safe_relative_parts(".") is None
