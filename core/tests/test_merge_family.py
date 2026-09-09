"""MERGE-2 (2026-08-30) -- family-scoped merge detection + superseded revisions.

MERGE-1 computed `needs_merge` per view path: the current version is
owner-authored (`saved_by == "auto"`) AND some earlier version at THAT path was
human-authored. That covers an owner resending under the SAME filename, where
every version stacks on one path.

It missed the resend-under-a-different-name case. `report_v2.xlsx` lands on its
own view path, so the TPM's edit on `report.xlsx` sat in a separate version
history the rule never consulted: no flag fired, `manual_triage_required` stayed
False, Submit stayed enabled, and family-based upload selection would ship the
owner's file while silently dropping the TPM's work.

MERGE-2 keeps the predicate and widens its scope to the revision FAMILY
(delivery_item_id + doc_id_slug, established by REV-1). It also marks
non-winning revisions `is_superseded` so the TPM can only edit the revision that
will actually be uploaded.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.src.storage import (
    add_document_index_row,
    add_document_item_association,
    configure_engine,
    init_db,
    is_superseded_revision,
    list_files_in_tg,
    save_view_document,
)
from core.src.storage.config import GlobalStorageConfig, set_storage_config
from core.src.storage.models import (
    DocumentIndexRow, DocumentItemAssociation, NSDPathType, RoutingResolution,
)
from core.src.template_schema import DocType, IngestSource

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)

CUSTOMER, DEVICE, MILESTONE, TG = "MMK", "SM-S671U1", "P1", "HW PL"
ITEM = "MMK-SM-S671U1-P1-10"
FAMILY = "signal_test_report"
OWNER = "auto"          # ingest / router-authored version
TPM = "t.arasu"         # human-authored version


@pytest.fixture(autouse=True)
async def storage_env(tmp_path):
    set_storage_config(GlobalStorageConfig(nsd_mount_root=tmp_path / "nsd"))
    engine = configure_engine("sqlite+aiosqlite:///:memory:")
    await init_db()
    yield
    await engine.dispose()
    set_storage_config(None)


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


async def _owner_sends(filename: str, *, rev: int, body: bytes,
                       slug: str = FAMILY, item: str = ITEM,
                       doc_type: DocType = DocType.TEST_REPORT) -> str:
    """Simulate an ingest: view-tree save (saved_by='auto') PLUS the
    document_index + association rows the router writes. Returns the path."""
    row = await save_view_document(
        customer_id=CUSTOMER, device_id=DEVICE, milestone_id=MILESTONE,
        tg_name=TG, relative_parts=(filename,), content=body,
        saved_by=OWNER, source="router",
    )
    await add_document_index_row(DocumentIndexRow(
        file_hash=row.sha256,
        milestone_id=MILESTONE,
        doc_type=doc_type,
        doc_id_slug=slug,
        rev_number=rev,
        ingest_source=IngestSource.EMAIL,
        original_filename=filename,
        routing_resolution=RoutingResolution.SUBSTRING_MATCH,
        ingested_at=NOW,
    ))
    await add_document_item_association(DocumentItemAssociation(
        file_hash=row.sha256,
        delivery_item_id=item,
        milestone_id=MILESTONE,
        local_nsd_path=f"internal/{slug}/rev{rev}/{filename}",
        nsd_path_type=NSDPathType.CLASSIFIED,
        owner_corp_id="owner-001",
        associated_at=NOW,
    ))
    return row.view_relative_path


async def _tpm_edits(filename: str, *, body: bytes) -> str:
    """Simulate a WOPI save-back: a new document_version row at the SAME path,
    human-authored, with NO document_index or association row (v2+ never
    create associations -- the assoc stays keyed on the ingest-time hash)."""
    row = await save_view_document(
        customer_id=CUSTOMER, device_id=DEVICE, milestone_id=MILESTONE,
        tg_name=TG, relative_parts=(filename,), content=body,
        saved_by=TPM, source="editor",
    )
    return row.view_relative_path


async def _by_path() -> dict:
    files = await list_files_in_tg(
        customer_id=CUSTOMER, device_id=DEVICE,
        milestone_id=MILESTONE, tg_name=TG,
    )
    return {f.view_relative_path: f for f in files}


# ---------------------------------------------------------------------------
# Same-filename resend -- MERGE-1 behaviour, must be preserved
# ---------------------------------------------------------------------------


class TestSameFilenameResend:
    async def test_owner_resend_over_tpm_edit_flags_merge(self):
        await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        await _tpm_edits("signal_test_report.xlsx", body=b"v2-tpm")
        path = await _owner_sends("signal_test_report.xlsx", rev=2, body=b"v3-owner")

        f = (await _by_path())[path]
        assert f.needs_merge is True
        # One path holds the whole family, so nothing is superseded.
        assert f.is_superseded is False

    async def test_tpm_reedit_clears_the_flag(self):
        await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        await _tpm_edits("signal_test_report.xlsx", body=b"v2-tpm")
        await _owner_sends("signal_test_report.xlsx", rev=2, body=b"v3-owner")
        path = await _tpm_edits("signal_test_report.xlsx", body=b"v4-tpm-merged")

        assert (await _by_path())[path].needs_merge is False

    async def test_owner_only_history_never_flags(self):
        await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        path = await _owner_sends("signal_test_report.xlsx", rev=2, body=b"v2")

        assert (await _by_path())[path].needs_merge is False


# ---------------------------------------------------------------------------
# Different-filename resend -- the gap MERGE-2 closes
# ---------------------------------------------------------------------------


class TestDifferentFilenameResend:
    async def test_owner_resend_under_new_name_flags_the_tpm_edited_path(self):
        """The bug: two paths, each individually innocent, so MERGE-1 saw
        nothing. The marker belongs on the path the TPM edited -- that is the
        content they must carry forward."""
        path_a = await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        await _tpm_edits("signal_test_report.xlsx", body=b"v2-tpm")
        path_b = await _owner_sends(
            "signal_test_report_v2.xlsx", rev=2, body=b"owner-rev2",
        )

        files = await _by_path()
        assert files[path_a].needs_merge is True     # TPM's edit -- merge this
        assert files[path_b].needs_merge is False    # the winner, owner-authored

    async def test_older_revision_is_superseded_and_winner_is_not(self):
        path_a = await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        path_b = await _owner_sends(
            "signal_test_report_v2.xlsx", rev=2, body=b"owner-rev2",
        )

        files = await _by_path()
        assert files[path_a].is_superseded is True
        assert files[path_b].is_superseded is False

    async def test_tpm_edit_on_winner_clears_flag_across_the_family(self):
        path_a = await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        await _tpm_edits("signal_test_report.xlsx", body=b"v2-tpm")
        await _owner_sends("signal_test_report_v2.xlsx", rev=2, body=b"owner-rev2")
        # TPM merges into the winning revision -- its current version is now human.
        await _tpm_edits("signal_test_report_v2.xlsx", body=b"merged")

        files = await _by_path()
        assert all(f.needs_merge is False for f in files.values())
        # Superseded state is about revision order, not merge state -- unchanged.
        assert files[path_a].is_superseded is True

    async def test_tpm_edit_on_stale_revision_does_not_clear_flag(self):
        """S4: the TPM edited the wrong row. The winner is still owner-authored,
        so the family stays flagged and Submit stays blocked."""
        await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        await _owner_sends("signal_test_report_v2.xlsx", rev=2, body=b"owner-rev2")
        path_a = await _tpm_edits("signal_test_report.xlsx", body=b"late-tpm-edit")

        files = await _by_path()
        assert files[path_a].needs_merge is True

    async def test_unrelated_families_do_not_cross_contaminate(self):
        await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        await _tpm_edits("signal_test_report.xlsx", body=b"tpm")
        other = await _owner_sends(
            "antenna_test_report.xlsx", rev=1, body=b"other",
            slug="antenna_test_report",
        )

        files = await _by_path()
        assert files[other].needs_merge is False
        assert files[other].is_superseded is False


# ---------------------------------------------------------------------------
# Fallback + guard
# ---------------------------------------------------------------------------


class TestFamilylessFallback:
    async def test_path_without_index_rows_uses_path_scoped_rule(self):
        """Vintage docs (pre-REV-1, or staged with no slug) have no family.
        They must keep the original MERGE-1 behaviour rather than losing the
        flag entirely."""
        await save_view_document(
            customer_id=CUSTOMER, device_id=DEVICE, milestone_id=MILESTONE,
            tg_name=TG, relative_parts=("legacy.xlsx",), content=b"v1",
            saved_by=OWNER, source="router",
        )
        await save_view_document(
            customer_id=CUSTOMER, device_id=DEVICE, milestone_id=MILESTONE,
            tg_name=TG, relative_parts=("legacy.xlsx",), content=b"v2",
            saved_by=TPM, source="editor",
        )
        row = await save_view_document(
            customer_id=CUSTOMER, device_id=DEVICE, milestone_id=MILESTONE,
            tg_name=TG, relative_parts=("legacy.xlsx",), content=b"v3",
            saved_by=OWNER, source="router",
        )

        f = (await _by_path())[row.view_relative_path]
        assert f.needs_merge is True
        assert f.is_superseded is False


class TestDocTypeSurvivesTpmEdit:
    """RECLASS-DISPLAY-1 (2026-08-31).

    doc_type / is_staged / item_type were resolved from the CURRENT version's
    sha256. A TPM browser edit writes a document_version row with a fresh
    sha256 and no document_index row, so after any edit the TG view rendered
    an empty Doc Type -- and `is_staged` fell to the "__missing__" sentinel,
    withholding the Reclassify control too. The row showed no classification
    and offered no way to set one.
    """

    async def test_doc_type_survives_a_tpm_edit(self):
        path = await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        await _tpm_edits("signal_test_report.xlsx", body=b"tpm-edit")

        f = (await _by_path())[path]
        assert f.doc_type == DocType.TEST_REPORT.value
        assert f.is_staged is False

    async def test_file_hash_is_the_indexed_hash_not_the_edited_one(self):
        """The reclassify POST uses file_hash as the key into
        tpm_resolve_doc_type, so it has to be a hash that document_index
        actually knows about."""
        path = await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        await _tpm_edits("signal_test_report.xlsx", body=b"tpm-edit")

        files = await _by_path()
        f = files[path]
        current_sha = None
        from core.src.storage import get_current_version
        current_sha = (await get_current_version(path)).sha256
        assert f.file_hash != current_sha        # not the TPM edit's hash
        assert f.doc_type == DocType.TEST_REPORT.value

    async def test_unresolved_doc_still_offers_reclassify_after_edit(self):
        """The sentinel must keep distinguishing 'no index row' from 'indexed
        but unresolved' -- the latter still needs the Reclassify dropdown."""
        path = await _owner_sends(
            "mystery_test_report.xlsx", rev=1, body=b"v1",
            slug="mystery", doc_type=DocType.UNRESOLVED,
        )
        await _tpm_edits("mystery_test_report.xlsx", body=b"tpm-edit")

        f = (await _by_path())[path]
        assert f.doc_type == DocType.UNRESOLVED.value
        assert f.is_staged is True

    async def test_vintage_file_with_no_index_row_is_not_reclassifiable(self):
        row = await save_view_document(
            customer_id=CUSTOMER, device_id=DEVICE, milestone_id=MILESTONE,
            tg_name=TG, relative_parts=("vintage.xlsx",), content=b"old",
            saved_by=OWNER, source="router",
        )
        f = (await _by_path())[row.view_relative_path]
        assert f.doc_type == ""
        assert f.is_staged is False        # "__missing__" -> no Reclassify
        assert f.file_hash == row.sha256   # falls back to current sha

    async def test_highest_revision_wins_when_several_are_indexed(self):
        """Same-filename resend puts two indexed revisions on one path; the
        displayed classification should follow the newest."""
        path = await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        await _owner_sends(
            "signal_test_report.xlsx", rev=2, body=b"v2",
            doc_type=DocType.TECH_REPORT,
        )
        await _tpm_edits("signal_test_report.xlsx", body=b"tpm-edit")

        f = (await _by_path())[path]
        assert f.doc_type == DocType.TECH_REPORT.value


class TestIsSupersededRevisionGuard:
    async def test_true_for_older_revision(self):
        path_a = await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        await _owner_sends("signal_test_report_v2.xlsx", rev=2, body=b"v2")
        assert await is_superseded_revision(path_a) is True

    async def test_false_for_winner(self):
        await _owner_sends("signal_test_report.xlsx", rev=1, body=b"v1")
        path_b = await _owner_sends("signal_test_report_v2.xlsx", rev=2, body=b"v2")
        assert await is_superseded_revision(path_b) is False

    @pytest.mark.parametrize(
        "path",
        [
            "",                                   # empty
            "not/a/view/path",                    # wrong root
            "view/MMK/SM-S671U1/P1/HW PL",        # scope only, no filename
            "view/MMK/SM-S671U1/P1/NoSuchTG/x.xlsx",  # unknown scope
        ],
    )
    async def test_fails_open_on_unresolvable_paths(self, path):
        """The guard is belt-and-braces behind the UI -- a lookup glitch must
        never block a legitimate edit."""
        assert await is_superseded_revision(path) is False
