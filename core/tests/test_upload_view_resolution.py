"""UPLOAD-VIEW-1 (2026-08-30) -- what actually gets uploaded to the carrier.

submit_to_carrier used to iterate classified associations and upload
`local_nsd_path` for each. Two defects fell out of that:

  * Every REVISION uploaded. An owner resend creates a second index row +
    association, so a resent document was pushed twice under the same filename.
  * The as-received bytes uploaded. A TPM browser edit writes a new
    document_version at the view path but no new association, so the carrier
    received the pre-edit file and the TPM's work never shipped.

`list_upload_files_for_item` collapses both axes: one winner per revision
family (document_index), resolved to its view-tree path whose on-disk file is
always the current version (document_version).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.src.storage import (
    add_document_index_row,
    add_document_item_association,
    configure_engine,
    init_db,
    list_upload_files_for_item,
    read_current_version_bytes,
    save_view_document,
    set_is_final,
)
from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage.models import (
    DocumentIndexRow, DocumentItemAssociation, NSDPathType, RoutingResolution,
)
from core.src.template_schema import DocType, IngestSource
from core.src.workflow_engine.tasks.submit_to_carrier import _view_subdir_prefix

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
CUSTOMER, DEVICE, MILESTONE, TG = "MMK", "SM-S671U1", "P1", "HW PL"
ITEM = "MMK-SM-S671U1-P1-10"
FAMILY = "signal_test_report"


@pytest.fixture(autouse=True)
async def storage_env(tmp_path):
    set_storage_config(GlobalStorageConfig(nsd_mount_root=tmp_path / "nsd"))
    engine = configure_engine("sqlite+aiosqlite:///:memory:")
    await init_db()
    yield
    await engine.dispose()
    set_storage_config(None)


async def _ingest(filename: str, *, rev: int, body: bytes,
                  slug: str = FAMILY, doc_type: DocType = DocType.TEST_REPORT,
                  item: str = ITEM, to_view: bool = True,
                  from_zip: bool = False,
                  parts: tuple[str, ...] | None = None) -> str:
    """Router-shaped write: view-tree save + index row + association."""
    sha: str
    if to_view:
        row = await save_view_document(
            customer_id=CUSTOMER, device_id=DEVICE, milestone_id=MILESTONE,
            tg_name=TG, relative_parts=parts or (filename,), content=body,
            saved_by="auto", source="router",
        )
        sha = row.sha256
    else:
        import hashlib
        sha = hashlib.sha256(body).hexdigest()
    await add_document_index_row(DocumentIndexRow(
        file_hash=sha, milestone_id=MILESTONE, doc_type=doc_type,
        doc_id_slug=slug, rev_number=rev, ingest_source=IngestSource.EMAIL,
        original_filename=filename, from_zip=from_zip,
        routing_resolution=RoutingResolution.SUBSTRING_MATCH, ingested_at=NOW,
    ))
    await add_document_item_association(DocumentItemAssociation(
        file_hash=sha, delivery_item_id=item, milestone_id=MILESTONE,
        local_nsd_path=f"internal/{slug}/rev{rev}/{filename}",
        nsd_path_type=NSDPathType.CLASSIFIED, owner_corp_id="owner-001",
        associated_at=NOW,
    ))
    return sha


async def _tpm_edit(filename: str, *, body: bytes) -> None:
    await save_view_document(
        customer_id=CUSTOMER, device_id=DEVICE, milestone_id=MILESTONE,
        tg_name=TG, relative_parts=(filename,), content=body,
        saved_by="t.arasu", source="editor",
    )


# ---------------------------------------------------------------------------
# One file per revision family
# ---------------------------------------------------------------------------


class TestRevisionCollapse:
    async def test_single_document_yields_one_view_file(self):
        await _ingest("signal_test_report.xlsx", rev=1, body=b"v1")
        files = await list_upload_files_for_item(ITEM)
        assert len(files) == 1
        assert files[0].is_view is True
        assert files[0].filename == "signal_test_report.xlsx"
        assert files[0].relative_path.startswith("view/MMK/SM-S671U1/P1/HW PL/")

    async def test_same_filename_resend_uploads_once_not_twice(self):
        """Two associations, one file. Previously both uploaded, pushing the
        same filename to the carrier twice."""
        await _ingest("signal_test_report.xlsx", rev=1, body=b"v1")
        await _ingest("signal_test_report.xlsx", rev=2, body=b"v2")

        files = await list_upload_files_for_item(ITEM)
        assert len(files) == 1
        # Both revisions share one view path; its file is the latest bytes.
        assert await read_current_version_bytes(files[0].relative_path) == b"v2"

    async def test_different_filename_resend_uploads_only_the_winner(self):
        await _ingest("signal_test_report.xlsx", rev=1, body=b"v1")
        await _ingest("signal_test_report_v2.xlsx", rev=2, body=b"v2")

        files = await list_upload_files_for_item(ITEM)
        assert len(files) == 1
        assert files[0].filename == "signal_test_report_v2.xlsx"

    async def test_is_final_pin_beats_highest_rev(self):
        """FR-66: once a TPM pins a revision, that one ships even if a later
        revision arrived afterwards."""
        sha1 = await _ingest("signal_test_report.xlsx", rev=1, body=b"v1")
        await _ingest("signal_test_report_v2.xlsx", rev=2, body=b"v2")
        await set_is_final(sha1, True)

        files = await list_upload_files_for_item(ITEM)
        assert len(files) == 1
        assert files[0].filename == "signal_test_report.xlsx"

    async def test_distinct_families_each_contribute_one_file(self):
        await _ingest("signal_test_report.xlsx", rev=1, body=b"a")
        await _ingest("antenna_test_report.xlsx", rev=1, body=b"b",
                      slug="antenna_test_report")

        files = await list_upload_files_for_item(ITEM)
        assert {f.filename for f in files} == {
            "signal_test_report.xlsx", "antenna_test_report.xlsx",
        }

    async def test_other_items_documents_are_not_included(self):
        await _ingest("signal_test_report.xlsx", rev=1, body=b"a")
        await _ingest("other_test_report.xlsx", rev=1, body=b"b",
                      slug="other_test_report", item="MMK-SM-S671U1-P1-11")

        files = await list_upload_files_for_item(ITEM)
        assert [f.filename for f in files] == ["signal_test_report.xlsx"]


# ---------------------------------------------------------------------------
# Current version, not as-received bytes
# ---------------------------------------------------------------------------


class TestCurrentVersionWins:
    async def test_tpm_edit_is_what_uploads(self):
        """The defect that motivated commit 3: the TPM's edit lives only in
        document_version, so an association-driven upload shipped `v1`."""
        await _ingest("signal_test_report.xlsx", rev=1, body=b"as-received")
        await _tpm_edit("signal_test_report.xlsx", body=b"tpm-edited")

        files = await list_upload_files_for_item(ITEM)
        assert len(files) == 1
        assert await read_current_version_bytes(files[0].relative_path) == b"tpm-edited"


# ---------------------------------------------------------------------------
# Exclusions
# ---------------------------------------------------------------------------


class TestExclusions:
    async def test_waivers_are_never_uploaded(self):
        await _ingest("signal_test_report.xlsx", rev=1, body=b"a")
        await _ingest("legal_waiver.pdf", rev=1, body=b"b",
                      slug="legal_waiver", doc_type=DocType.WAIVER)

        files = await list_upload_files_for_item(ITEM)
        assert [f.filename for f in files] == ["signal_test_report.xlsx"]

    async def test_archive_container_audit_row_is_skipped(self):
        await _ingest("bundle.zip", rev=1, body=b"zip-bytes", slug="bundle",
                      doc_type=DocType.ARCHIVE_CONTAINER)
        assert await list_upload_files_for_item(ITEM) == []

    async def test_archive_basename_is_skipped_even_if_classified(self):
        """Belt-and-braces: the view tree keeps the original archive next to
        its extracted contents, and the carrier must receive the contents."""
        await _ingest("bundle.7z", rev=1, body=b"z", slug="bundle")
        assert await list_upload_files_for_item(ITEM) == []

    async def test_item_with_no_classified_documents_yields_nothing(self):
        assert await list_upload_files_for_item(ITEM) == []


# ---------------------------------------------------------------------------
# Internal-tree fallback
# ---------------------------------------------------------------------------


class TestInternalFallback:
    async def test_no_view_row_falls_back_to_association_path(self):
        """Items with no tg_name never reach the view tree. They must keep
        uploading from the internal path exactly as before."""
        await _ingest("signal_test_report.xlsx", rev=1, body=b"v1", to_view=False)

        files = await list_upload_files_for_item(ITEM)
        assert len(files) == 1
        assert files[0].is_view is False
        assert files[0].relative_path.startswith("internal/")
        assert files[0].filename == "signal_test_report.xlsx"


# ---------------------------------------------------------------------------
# Carrier sub-folder derivation
# ---------------------------------------------------------------------------


class TestFromZipGate:
    """UPLOAD-FLAT-1 (2026-08-31).

    NSD ingest passes the share-relative path as the filename, so an ordinary
    file sitting in an NSD folder carries path segments just like an archive
    entry does. Recreating NSD folders on the carrier is wrong -- a standalone
    file uploads flat. The path can't tell them apart (NEST-1 only prefixes the
    archive name at depth >= 1), so the decision rides document_index.from_zip.
    """

    async def test_standalone_file_in_an_nsd_folder_is_not_marked_from_zip(self):
        await _ingest(
            "2. DMDform (Done)/report_test_report.xlsx", rev=1, body=b"v1",
            parts=("2. DMDform (Done)", "report_test_report.xlsx"),
        )
        files = await list_upload_files_for_item(ITEM)
        assert len(files) == 1
        assert files[0].from_zip is False

    async def test_archive_extracted_file_is_marked_from_zip(self):
        await _ingest(
            "bundle.zip/inner/report_test_report.xlsx", rev=1, body=b"v1",
            from_zip=True, parts=("inner", "report_test_report.xlsx"),
        )
        files = await list_upload_files_for_item(ITEM)
        assert len(files) == 1
        assert files[0].from_zip is True


class TestViewSubdirPrefix:
    P = "view/MMK/SM-S671U1/P1/HW PL/"

    @pytest.mark.parametrize(
        "tail,expected",
        [
            # Loose file at the TG root -> flat upload.
            ("a.pdf",                             ""),
            # Folder from inside a zip -> recreated under target_folder.
            ("i am c/d.pdf",                      "i am c"),
            # The archive's own name is a container, not delivered structure.
            ("b.zip/i am c/d.pdf",                "i am c"),
            ("report.7z/folder/nested/x.pdf",     "folder/nested"),
            # NEST-1 nested archives: every container segment drops out.
            ("outer.zip/inner.zip/x/y.pdf",       "x"),
            # Deep recursion inside one archive folder survives intact.
            ("pack/a/b/c/deep.pdf",               "pack/a/b/c"),
        ],
    )
    def test_subdir_from_view_path(self, tail, expected):
        assert _view_subdir_prefix(self.P + tail) == expected

    @pytest.mark.parametrize(
        "path",
        ["", "internal/x/rev1/a.pdf", "view/MMK/SM-S671U1/P1/HW PL"],
    )
    def test_non_view_or_scope_only_paths_have_no_subdir(self, path):
        assert _view_subdir_prefix(path) == ""

    def test_spaces_are_preserved_in_folder_names(self):
        """Real archives carry spaces; the carrier folder must match."""
        assert _view_subdir_prefix(self.P + "i am c/d.pdf") == "i am c"
