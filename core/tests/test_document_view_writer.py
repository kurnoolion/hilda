"""view-tree writer -- zip extraction + version bumps per D-150 Chunk 3."""
from __future__ import annotations

import io
import zipfile

import pytest

from core.src.storage import (
    configure_engine,
    init_db,
    list_files_in_tg,
    list_versions_for_file,
)
from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage.document_view_writer import (
    MAX_ZIP_SIZE_BYTES,
    write_attachment_to_view_tree,
)


@pytest.fixture(autouse=True)
async def env(tmp_path):
    set_storage_config(GlobalStorageConfig(nsd_mount_root=tmp_path / "nsd"))
    engine = configure_engine("sqlite+aiosqlite:///:memory:")
    await init_db()
    yield
    await engine.dispose()
    set_storage_config(None)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    """Build an in-memory zip archive with the given (path, bytes) entries."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, data in entries.items():
            zf.writestr(path, data)
    return buf.getvalue()


# ---------------------------------------------------------------------------


class TestNonZipAttachment:
    async def test_saves_single_file(self):
        written = await write_attachment_to_view_tree(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", item_type="test_tech_waiver_report",
            filename="report.pdf", content=b"pdf content",
        )
        assert written == ["view/MMK/SM-S671U1/DRR/hw_reports/report.pdf"]

    async def test_second_arrival_bumps_version(self):
        await write_attachment_to_view_tree(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", item_type="test_tech_waiver_report",
            filename="report.pdf", content=b"v1",
        )
        await write_attachment_to_view_tree(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", item_type="test_tech_waiver_report",
            filename="report.pdf", content=b"v2",
        )
        versions = await list_versions_for_file(
            "view/MMK/SM-S671U1/DRR/hw_reports/report.pdf",
        )
        assert len(versions) == 2
        assert versions[0].version_num == 2
        assert versions[0].source == "router"


class TestDefaultWiExclusion:
    async def test_default_item_type_skipped(self):
        written = await write_attachment_to_view_tree(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="_unrouted", item_type="default",
            filename="report.pdf", content=b"content",
        )
        assert written == []

    async def test_empty_tg_name_skipped(self):
        written = await write_attachment_to_view_tree(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="", item_type="test_tech_waiver_report",
            filename="report.pdf", content=b"content",
        )
        assert written == []


class TestZipExtraction:
    async def test_extracts_flat_zip_preserves_original(self):
        zip_bytes = _zip_bytes({
            "spec.pdf": b"spec bytes",
            "notes.txt": b"notes bytes",
        })
        written = await write_attachment_to_view_tree(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", item_type="test_tech_waiver_report",
            filename="pack.zip", content=zip_bytes,
        )
        assert "view/MMK/SM-S671U1/DRR/hw_reports/pack.zip" in written
        assert "view/MMK/SM-S671U1/DRR/hw_reports/spec.pdf" in written
        assert "view/MMK/SM-S671U1/DRR/hw_reports/notes.txt" in written

    async def test_extracts_recursive_zip_preserving_tree(self):
        zip_bytes = _zip_bytes({
            "vendor/sig/report.pdf": b"sig",
            "vendor/sw/build.log": b"build",
            "vendor/README.txt": b"readme",
        })
        written = await write_attachment_to_view_tree(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", item_type="test_tech_waiver_report",
            filename="vendor_pack.zip", content=zip_bytes,
        )
        assert "view/MMK/SM-S671U1/DRR/hw_reports/vendor_pack.zip" in written
        assert "view/MMK/SM-S671U1/DRR/hw_reports/vendor/sig/report.pdf" in written
        assert "view/MMK/SM-S671U1/DRR/hw_reports/vendor/sw/build.log" in written
        assert "view/MMK/SM-S671U1/DRR/hw_reports/vendor/README.txt" in written

        files = await list_files_in_tg(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports",
        )
        paths = {f.view_relative_path for f in files}
        assert "view/MMK/SM-S671U1/DRR/hw_reports/vendor/sig/report.pdf" in paths

    async def test_zip_slip_entries_skipped(self):
        # Simulate a zip-slip attempt: entries with .. and absolute path
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("../../../etc/passwd", b"pwned")
            zf.writestr("/etc/hosts", b"pwned2")
            zf.writestr("safe.txt", b"safe content")
        zip_bytes = buf.getvalue()

        written = await write_attachment_to_view_tree(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", item_type="test_tech_waiver_report",
            filename="attack.zip", content=zip_bytes,
        )
        # Safe entry present + original zip present, malicious entries not
        assert "view/MMK/SM-S671U1/DRR/hw_reports/safe.txt" in written
        assert "view/MMK/SM-S671U1/DRR/hw_reports/attack.zip" in written
        assert not any("passwd" in p or "hosts" in p for p in written)

    async def test_oversized_zip_keeps_original_skips_extraction(self, monkeypatch):
        # Fake a "large" zip by patching the constant to a tiny value
        import core.src.storage.document_view_writer as mod
        monkeypatch.setattr(mod, "MAX_ZIP_SIZE_BYTES", 100)
        zip_bytes = _zip_bytes({"a.txt": b"x" * 500})
        assert len(zip_bytes) > 100

        written = await write_attachment_to_view_tree(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", item_type="test_tech_waiver_report",
            filename="big.zip", content=zip_bytes,
        )
        # Only the original zip should have landed; no extraction
        assert written == ["view/MMK/SM-S671U1/DRR/hw_reports/big.zip"]

    async def test_malformed_zip_after_magic_keeps_original(self):
        # Bytes start with the zip magic but rest is garbage
        malformed = b"PK\x03\x04" + b"garbage garbage garbage" * 20
        written = await write_attachment_to_view_tree(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", item_type="test_tech_waiver_report",
            filename="broken.zip", content=malformed,
        )
        assert written == ["view/MMK/SM-S671U1/DRR/hw_reports/broken.zip"]


class TestConstant:
    def test_max_zip_size_is_300MB(self):
        assert MAX_ZIP_SIZE_BYTES == 300 * 1024 * 1024
