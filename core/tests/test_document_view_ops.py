"""storage document_view_ops — view-tree save + versioning + browse per D-150."""
from __future__ import annotations

import pytest

from core.src.storage import (
    NSDPath,
    configure_engine,
    get_current_version,
    get_version_by_num,
    init_db,
    list_files_in_tg,
    list_tg_names_for_scope,
    list_versions_for_file,
    read_current_version_bytes,
    read_version_bytes,
    save_view_document,
)
from core.src.storage.config import GlobalStorageConfig, set_storage_config


@pytest.fixture(autouse=True)
async def storage_env(tmp_path):
    set_storage_config(GlobalStorageConfig(nsd_mount_root=tmp_path / "nsd"))
    engine = configure_engine("sqlite+aiosqlite:///:memory:")
    await init_db()
    yield
    await engine.dispose()
    set_storage_config(None)


# ---------------------------------------------------------------------------
# NSDPath.view_tree factory
# ---------------------------------------------------------------------------


class TestViewTreeNSDPath:
    def test_view_tree_shape(self):
        p = NSDPath.view_tree("MMK", "SM-S671U1", "DRR", "hw_reports", "final.xlsx")
        assert p.to_relative() == "view/MMK/SM-S671U1/DRR/hw_reports/final.xlsx"

    def test_view_tree_zip_folder_preservation(self):
        p = NSDPath.view_tree(
            "MMK", "SM-S671U1", "DRR", "hw_reports",
            "vendor_pack", "sig_reports", "report.pdf",
        )
        assert p.to_relative() == (
            "view/MMK/SM-S671U1/DRR/hw_reports/vendor_pack/sig_reports/report.pdf"
        )

    def test_view_tree_no_relative_parts_is_tg_folder(self):
        # Empty relative_parts points at the tg_name directory itself
        p = NSDPath.view_tree("MMK", "SM-S671U1", "DRR", "hw_reports")
        assert p.to_relative() == "view/MMK/SM-S671U1/DRR/hw_reports"

    def test_view_version_sibling_shape(self):
        current = NSDPath.view_tree(
            "MMK", "SM-S671U1", "DRR", "hw_reports", "final.xlsx",
        )
        sibling = NSDPath.view_version_sibling(current, 3)
        assert sibling.to_relative() == (
            "view/MMK/SM-S671U1/DRR/hw_reports/final.xlsx.v3"
        )


# ---------------------------------------------------------------------------
# save_view_document — first-save + version-bump semantics
# ---------------------------------------------------------------------------


class TestSaveViewDocument:
    async def test_first_save_creates_v1_row(self):
        row = await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("final.xlsx",),
            content=b"hello world", saved_by="pm.smith",
        )
        assert row.version_num == 1
        assert row.is_current is True
        assert row.size_bytes == 11
        assert row.filename == "final.xlsx"
        assert row.view_relative_path == "view/MMK/SM-S671U1/DRR/hw_reports/final.xlsx"

    async def test_second_save_bumps_version_and_flips_prior(self):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("final.xlsx",),
            content=b"v1 content", saved_by="pm.smith",
        )
        row2 = await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("final.xlsx",),
            content=b"v2 content NEW", saved_by="pm.smith",
        )
        assert row2.version_num == 2
        assert row2.is_current is True

        versions = await list_versions_for_file(
            "view/MMK/SM-S671U1/DRR/hw_reports/final.xlsx",
        )
        assert len(versions) == 2
        assert versions[0].version_num == 2  # newest first
        assert versions[0].is_current is True
        assert versions[1].version_num == 1
        assert versions[1].is_current is False

    async def test_current_file_reads_latest_bytes_after_save(self):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("final.xlsx",),
            content=b"first", saved_by="pm.smith",
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("final.xlsx",),
            content=b"second", saved_by="pm.smith",
        )
        content = await read_current_version_bytes(
            "view/MMK/SM-S671U1/DRR/hw_reports/final.xlsx",
        )
        assert content == b"second"

    async def test_older_version_bytes_readable_via_sibling(self):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("final.xlsx",),
            content=b"v1 bytes", saved_by="pm.smith",
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("final.xlsx",),
            content=b"v2 bytes", saved_by="pm.smith",
        )
        v1 = await read_version_bytes(
            "view/MMK/SM-S671U1/DRR/hw_reports/final.xlsx",
            version_num=1,
        )
        assert v1 == b"v1 bytes"
        v2 = await read_version_bytes(
            "view/MMK/SM-S671U1/DRR/hw_reports/final.xlsx",
            version_num=2,
        )
        assert v2 == b"v2 bytes"

    async def test_get_version_by_num_returns_specific_row(self):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("final.xlsx",),
            content=b"a", saved_by="pm.smith",
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("final.xlsx",),
            content=b"bb", saved_by="pm.jones",
        )
        v1 = await get_version_by_num(
            "view/MMK/SM-S671U1/DRR/hw_reports/final.xlsx",
            version_num=1,
        )
        assert v1 is not None
        assert v1.version_num == 1
        assert v1.saved_by == "pm.smith"
        assert v1.size_bytes == 1

    async def test_save_preserves_zip_folder_tree(self):
        row = await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports",
            relative_parts=("vendor_pack", "sig", "spec.pdf"),
            content=b"PDF content", saved_by="auto",
            source="zip_extract",
        )
        assert row.view_relative_path == (
            "view/MMK/SM-S671U1/DRR/hw_reports/vendor_pack/sig/spec.pdf"
        )
        assert row.source == "zip_extract"
        assert row.filename == "spec.pdf"


# ---------------------------------------------------------------------------
# Browse UI helpers
# ---------------------------------------------------------------------------


class TestBrowseHelpers:
    async def test_landing_page_returns_distinct_tg_names(self):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("a.xlsx",),
            content=b"a", saved_by="pm",
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("b.xlsx",),
            content=b"b", saved_by="pm",
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="sw_reports", relative_parts=("c.xlsx",),
            content=b"c", saved_by="pm",
        )
        entries = await list_tg_names_for_scope(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
        )
        names = [e.tg_name for e in entries]
        assert names == ["hw_reports", "sw_reports"]  # alphabetical
        hw = next(e for e in entries if e.tg_name == "hw_reports")
        assert hw.file_count == 2

    async def test_landing_page_excludes_other_scopes(self):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("a.xlsx",),
            content=b"a", saved_by="pm",
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-F918U", milestone_id="DRR",   # different device
            tg_name="hw_reports", relative_parts=("b.xlsx",),
            content=b"b", saved_by="pm",
        )
        entries = await list_tg_names_for_scope(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
        )
        assert len(entries) == 1
        assert entries[0].file_count == 1

    async def test_browse_returns_flat_file_list_with_versions(self):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("a.xlsx",),
            content=b"a1", saved_by="pm",
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("a.xlsx",),
            content=b"a2", saved_by="pm",     # second version of a.xlsx
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("sub", "b.pdf"),
            content=b"b", saved_by="auto", source="zip_extract",
        )
        files = await list_files_in_tg(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports",
        )
        by_path = {f.view_relative_path: f for f in files}
        assert set(by_path.keys()) == {
            "view/MMK/SM-S671U1/DRR/hw_reports/a.xlsx",
            "view/MMK/SM-S671U1/DRR/hw_reports/sub/b.pdf",
        }
        a = by_path["view/MMK/SM-S671U1/DRR/hw_reports/a.xlsx"]
        assert a.version_count == 2
        assert a.filename == "a.xlsx"
        b = by_path["view/MMK/SM-S671U1/DRR/hw_reports/sub/b.pdf"]
        assert b.version_count == 1


# ---------------------------------------------------------------------------
# get_current_version
# ---------------------------------------------------------------------------


class TestGetCurrentVersion:
    async def test_missing_file_returns_none(self):
        row = await get_current_version("view/MMK/SM-X/DRR/hw/absent.xlsx")
        assert row is None

    async def test_after_save_returns_latest_row(self):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("final.xlsx",),
            content=b"a", saved_by="pm.smith",
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("final.xlsx",),
            content=b"bb", saved_by="pm.jones",
        )
        row = await get_current_version("view/MMK/SM-S671U1/DRR/hw_reports/final.xlsx")
        assert row is not None
        assert row.version_num == 2
        assert row.is_current is True
        assert row.saved_by == "pm.jones"
        assert row.size_bytes == 2
