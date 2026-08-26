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


class TestNeedsMergeFlag:
    """MERGE-1 (2026-07-28) — red-asterisk signal for owner-on-top-of-TPM-edit.

    Rule: needs_merge = (version_count > 1) AND (current.saved_by == "auto")
                       AND (any prior.saved_by != "auto").
    Owner sentinel: saved_by == "auto" (router-driven ingest from owner email).
    TPM/human:      saved_by != "auto" (dashboard Edit save-back, real corp_id).
    """
    _scope = dict(customer_id="MMK", device_id="SM-S671U1",
                   milestone_id="DRR", tg_name="hw_reports")

    async def _save(self, name: str, content: bytes, saved_by: str, source: str = "editor"):
        await save_view_document(
            **self._scope, relative_parts=(name,),
            content=content, saved_by=saved_by, source=source,
        )

    async def _get(self, name: str):
        files = await list_files_in_tg(**self._scope)
        by_name = {f.filename: f for f in files}
        return by_name.get(name)

    async def test_single_version_owner_no_asterisk(self):
        await self._save("a.xlsx", b"a1", saved_by="auto", source="zip_extract")
        f = await self._get("a.xlsx")
        assert f is not None
        assert f.version_count == 1
        assert f.needs_merge is False

    async def test_single_version_tpm_no_asterisk(self):
        await self._save("a.xlsx", b"a1", saved_by="unknown")
        f = await self._get("a.xlsx")
        assert f.needs_merge is False

    async def test_all_versions_owner_no_asterisk(self):
        # Owner-only cascade: nothing was ever edited by TPM, nothing to merge.
        await self._save("a.xlsx", b"a1", saved_by="auto")
        await self._save("a.xlsx", b"a2", saved_by="auto")
        await self._save("a.xlsx", b"a3", saved_by="auto")
        f = await self._get("a.xlsx")
        assert f.version_count == 3
        assert f.needs_merge is False

    async def test_all_versions_tpm_no_asterisk(self):
        # TPM edited across all versions, no owner overwrite -- no merge signal.
        await self._save("a.xlsx", b"a1", saved_by="unknown")
        await self._save("a.xlsx", b"a2", saved_by="pm.smith")
        f = await self._get("a.xlsx")
        assert f.version_count == 2
        assert f.needs_merge is False

    async def test_tpm_last_after_owner_no_asterisk(self):
        # Owner v1, TPM v2 (latest). TPM has already merged / re-edited.
        await self._save("a.xlsx", b"a1", saved_by="auto")
        await self._save("a.xlsx", b"a2", saved_by="unknown")
        f = await self._get("a.xlsx")
        assert f.version_count == 2
        assert f.last_saved_by == "unknown"
        assert f.needs_merge is False

    async def test_owner_after_tpm_asterisk(self):
        # TPM v1 edit, then owner v2 landed on top -- MERGE REQUIRED.
        await self._save("a.xlsx", b"a1", saved_by="unknown")
        await self._save("a.xlsx", b"a2", saved_by="auto")
        f = await self._get("a.xlsx")
        assert f.version_count == 2
        assert f.last_saved_by == "auto"
        assert f.needs_merge is True

    async def test_owner_tpm_owner_asterisk(self):
        # Owner v1 -> TPM v2 (edit) -> Owner v3 (overwrite). Merge required.
        await self._save("a.xlsx", b"a1", saved_by="auto")
        await self._save("a.xlsx", b"a2", saved_by="pm.smith")
        await self._save("a.xlsx", b"a3", saved_by="auto")
        f = await self._get("a.xlsx")
        assert f.version_count == 3
        assert f.needs_merge is True

    async def test_owner_tpm_owner_tpm_no_asterisk(self):
        # Owner -> TPM -> Owner -> TPM. TPM re-merged; latest is TPM. No asterisk.
        await self._save("a.xlsx", b"a1", saved_by="auto")
        await self._save("a.xlsx", b"a2", saved_by="pm.smith")
        await self._save("a.xlsx", b"a3", saved_by="auto")
        await self._save("a.xlsx", b"a4", saved_by="pm.smith")
        f = await self._get("a.xlsx")
        assert f.version_count == 4
        assert f.last_saved_by == "pm.smith"
        assert f.needs_merge is False


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


# ---------------------------------------------------------------------------
# RECLASS-1 (2026-08-24): list_files_in_tg surfaces doc_type + is_staged
# via join to document_index + document_item_association so the TG-view
# template can render a Reclassify button on Unresolved rows.
# ---------------------------------------------------------------------------


class TestListFilesInTgReclassColumns:
    """Populate document_index + document_item_association rows alongside
    document_version rows and confirm the join surfaces (doc_type, file_hash,
    is_staged) correctly."""

    _scope = dict(
        customer_id="MMK",
        device_id="SM-S671U1",
        milestone_id="P1",
        tg_name="HW PL",
    )

    async def test_no_index_row_defaults_to_empty_doc_type(self):
        # Vintage path -- doc_version exists but document_index doesn't.
        # Fields default to "" / False so template treats them as classified.
        await save_view_document(
            **self._scope, relative_parts=("legacy.pdf",),
            content=b"legacy", saved_by="auto",
        )
        files = await list_files_in_tg(**self._scope)
        assert len(files) == 1
        f = files[0]
        assert f.doc_type == ""
        assert f.is_staged is False
        assert f.file_hash != ""     # sha256 is always populated by save_view_document

    async def test_unresolved_doc_with_staged_assoc_flags_is_staged(self):
        """NSD/PLM/Email ingest path -- doc landed with doc_type=Unresolved
        and nsd_path_type=STAGED_NOT_CLASSIFIED. Reclassify button should
        appear."""
        from core.src.storage.db import (
            DocumentIndexTable, DocumentItemAssociationTable, session_scope,
        )
        from core.src.storage.models import NSDPathType

        await save_view_document(
            **self._scope, relative_parts=("hac_report.pdf",),
            content=b"hac-bytes", saved_by="auto",
        )
        # Look up the file_hash written by save_view_document
        files_before = await list_files_in_tg(**self._scope)
        file_hash = files_before[0].file_hash
        assert file_hash

        async with session_scope() as session:
            from datetime import datetime as _dt, timezone as _tz
            _now = _dt.now(_tz.utc)
            session.add(DocumentIndexTable(
                file_hash=file_hash,
                milestone_id="P1",
                doc_type="unresolved",
                doc_id_slug=None,
                rev_number=None,
                ingest_source="NetworkSharedDrive",
                original_filename="hac_report.pdf",
                inferred_tg_name="HW PL",
                routing_resolution="SubstringMatch",
                ingested_at=_now,
            ))
            session.add(DocumentItemAssociationTable(
                file_hash=file_hash,
                delivery_item_id="MMK-SM-S671U1-P1-42",
                milestone_id="P1",
                local_nsd_path="internal/MMK/SM-S671U1/P1/HW PL/42/Unresolved/hac-report/rev1/hac_report.pdf",
                nsd_path_type=NSDPathType.STAGED_NOT_CLASSIFIED.value,
                owner_corp_id="",
                associated_at=_now,
            ))
            await session.commit()
        files = await list_files_in_tg(**self._scope)
        assert len(files) == 1
        f = files[0]
        assert f.doc_type == "unresolved"
        assert f.is_staged is True

    async def test_classified_doc_flags_not_staged(self):
        """Classified doc (nsd_path_type=CLASSIFIED) -- Reclassify button
        should NOT appear."""
        from core.src.storage.db import (
            DocumentIndexTable, DocumentItemAssociationTable, session_scope,
        )
        from core.src.storage.models import NSDPathType

        await save_view_document(
            **self._scope, relative_parts=("classified.pdf",),
            content=b"classy", saved_by="auto",
        )
        files_before = await list_files_in_tg(**self._scope)
        file_hash = files_before[0].file_hash

        async with session_scope() as session:
            from datetime import datetime as _dt, timezone as _tz
            _now = _dt.now(_tz.utc)
            session.add(DocumentIndexTable(
                file_hash=file_hash,
                milestone_id="P1",
                doc_type="test_report",
                doc_id_slug="classified",
                rev_number=1,
                ingest_source="NetworkSharedDrive",
                original_filename="classified.pdf",
                inferred_tg_name="HW PL",
                routing_resolution="SubstringMatch",
                ingested_at=_now,
            ))
            session.add(DocumentItemAssociationTable(
                file_hash=file_hash,
                delivery_item_id="MMK-SM-S671U1-P1-42",
                milestone_id="P1",
                local_nsd_path="internal/MMK/SM-S671U1/P1/HW PL/42/TestReport/classified/rev1/classified.pdf",
                nsd_path_type=NSDPathType.CLASSIFIED.value,
                owner_corp_id="",
                associated_at=_now,
            ))
            await session.commit()
        files = await list_files_in_tg(**self._scope)
        f = files[0]
        assert f.doc_type == "test_report"
        assert f.is_staged is False

    async def test_classified_doc_type_wins_over_stale_staged_assoc(self):
        """RECLASS-BUGFIX-2 (2026-08-26): doc_type on document_index is the
        source of truth for is_staged. A concrete doc_type
        (test_report / compliance_certification_release_notes / ...) MUST
        yield is_staged=False even when a stale document_item_association
        row still carries nsd_path_type=STAGED_NOT_CLASSIFIED. Regression
        guard for the row-1 shape seen on the corp box: doc_type classified
        at ingest but assoc left at STAGED_NOT_CLASSIFIED, which caused the
        template to render 'compliance_certification_release_notes -- not
        classified' + a spurious Reclassify dropdown on already-classified
        docs.
        """
        from core.src.storage.db import (
            DocumentIndexTable, DocumentItemAssociationTable, session_scope,
        )
        from core.src.storage.models import NSDPathType

        await save_view_document(
            **self._scope, relative_parts=("release_notes.docx",),
            content=b"release-notes-bytes", saved_by="auto",
        )
        files_before = await list_files_in_tg(**self._scope)
        file_hash = files_before[0].file_hash

        async with session_scope() as session:
            from datetime import datetime as _dt, timezone as _tz
            _now = _dt.now(_tz.utc)
            session.add(DocumentIndexTable(
                file_hash=file_hash,
                milestone_id="P1",
                doc_type="compliance_certification_release_notes",
                doc_id_slug="release-notes",
                rev_number=1,
                ingest_source="NetworkSharedDrive",
                original_filename="release_notes.docx",
                inferred_tg_name="HW PL",
                routing_resolution="SubstringMatch",
                ingested_at=_now,
            ))
            # Stale assoc: STAGED_NOT_CLASSIFIED even though doc_type is real.
            session.add(DocumentItemAssociationTable(
                file_hash=file_hash,
                delivery_item_id="MMK-SM-S671U1-P1-42",
                milestone_id="P1",
                local_nsd_path="internal/staged/release_notes.docx",
                nsd_path_type=NSDPathType.STAGED_NOT_CLASSIFIED.value,
                owner_corp_id="",
                associated_at=_now,
            ))
            await session.commit()
        files = await list_files_in_tg(**self._scope)
        f = files[0]
        assert f.doc_type == "compliance_certification_release_notes"
        assert f.is_staged is False, \
            "Concrete doc_type MUST win over stale STAGED_NOT_CLASSIFIED assoc"
