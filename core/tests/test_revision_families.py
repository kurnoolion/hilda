"""REV-1 (2026-08-30) -- multi-revision slug families.

Covers the three pieces that make an owner resend land as revision N+1 of an
existing document instead of colliding with revision 1:

  1. `Fr52AttachmentRouter._slug_from_filename` version-token stripping, so
     `report.xlsx` / `report_v2.xlsx` / `report (1).xlsx` share one family key.
  2. Router Step C family resolution -- match against the item's existing
     slugs, continue at max(rev)+1, or open a new family at rev 1.
  3. `storage.get_max_rev_for_slug` -- the family-tip lookup Step C drives.

Background: before REV-1 the Step C lookup sat behind
`ph1_first_pass_substring_only`, so `rev_number` was ALWAYS 1. A same-filename
resend therefore produced a second index row colliding on the
`uq_doc_slug_rev` unique index AND re-derived the same internal NSD path,
overwriting revision 1's bytes. See STATUS/DECISIONS REV-1.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.src.email_service import InboundAttachment
from core.src.email_service.inbound.attachment_router import Fr52AttachmentRouter
from core.src.email_service.mocks import InMemoryStorage
from core.src.storage.models import DocumentIndexRow, RoutingResolution
from core.src.template_schema import DocType, IngestSource, ItemType

NOW = datetime(2026, 8, 30, 12, 0, 0, tzinfo=timezone.utc)
MILESTONE = "P1"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _attachment(filename: str, content: bytes = b"payload") -> InboundAttachment:
    return InboundAttachment(
        filename=filename,
        content=content,
        content_type="application/octet-stream",
        file_hash=hashlib.sha256(content + filename.encode()).hexdigest(),
    )


def _item(
    *,
    item_id: str = "MMK-SM-S671U1-P1-10",
    item_type: str = ItemType.TEST_TECH_WAIVER_REPORT.value,
    milestone_id: str = MILESTONE,
) -> dict:
    """Candidate the filenames below all substring-match on, so Stage 1 routes
    to it and Step C's gate_passes evaluates True.

    Filenames must ALSO satisfy the bundled `test_report` filename regex
    (`.*test[_\\- ]?report.*\\.(pdf|docx|xlsx)$`) -- an UNRESOLVED doc_type
    closes the Step C gate and the file stages instead of getting a slug.
    """
    return {
        "item_id":                item_id,
        "item_no":                10,
        "item_name":              "test_report",
        "item_description":       [["test_report"]],
        "item_type":              item_type,
        "milestone_id":           milestone_id,
        "tg_name":                "HW PL",
        "tg_email_group_alias":   None,
        "owner_corp_usa_email":   ["owner@corp.example"],
        "owner_corp_email":       ["owner@corp.example"],
        "owner_corp_id":          ["owner-001"],
        "folder_routing_enabled": False,
    }


def _router(storage: InMemoryStorage) -> Fr52AttachmentRouter:
    """Router with production's ph1 flag ON, to prove REV-1 works WITHOUT
    flipping `ph1_first_pass_substring_only` (Steps B2/B3/B4 and the
    TG_SINGLE_ITEM shortcut stay disabled -- see the 2026-07-25 STATUS flag)."""
    return Fr52AttachmentRouter(
        storage=storage,
        llm=None,
        tg_resolver=None,
        doc_type_filename_rules_path=Path(
            "core/src/email_service/default_doc_type_rules.yaml"
        ),
        plm_upload_enabled=False,
        review_required_enabled=False,
        ph1_first_pass_substring_only=True,
    )


# ---------------------------------------------------------------------------
# 1. Slug normalization
# ---------------------------------------------------------------------------


class TestSlugFromFilename:
    @pytest.mark.parametrize(
        "filename,expected",
        [
            # Baseline -- no version token.
            ("report.xlsx",            "report"),
            # The four token shapes locked by the user 2026-08-30.
            ("report_v2.xlsx",         "report"),
            ("report v3.xlsx",         "report"),
            ("report_rev2.docx",       "report"),
            ("report (1).xlsx",        "report"),
            ("report(2).xlsx",         "report"),
            # Stacked suffixes collapse through the loop.
            ("report_v2_rev3.xlsx",    "report"),
            # Hyphen separator is equivalent to underscore post-normalization.
            ("report-v4.pdf",          "report"),
            # Case-insensitive.
            ("Report_V2.XLSX",         "report"),
        ],
    )
    def test_version_tokens_collapse_to_one_family(self, filename, expected):
        assert Fr52AttachmentRouter._slug_from_filename(filename) == expected

    @pytest.mark.parametrize(
        "filename,expected",
        [
            # A genuinely different document must NOT join `report`.
            ("test_report.xlsx",       "test_report"),
            ("report_summary.xlsx",    "report_summary"),
            # Bare trailing digits are NOT version tokens -- real filenames
            # carry date-ish tails where the digits are part of the identity.
            # Stripping them would merge unrelated documents (see docstring).
            ("PA3_Release Notes_SWMK_V4_1104.docx",
             "pa3_release_notes_swmk_v4_1104"),
            ("weekly_20260830.xlsx",   "weekly_20260830"),
        ],
    )
    def test_distinct_documents_stay_distinct(self, filename, expected):
        assert Fr52AttachmentRouter._slug_from_filename(filename) == expected

    def test_all_token_filename_degrades_to_sentinel(self):
        """`v2.xlsx` strips to empty -- must not produce an empty slug, which
        would violate the DocumentIndexRow contract."""
        assert Fr52AttachmentRouter._slug_from_filename("v2.xlsx") == "doc"

    def test_idempotent_on_an_already_normalized_slug(self):
        """Step C re-normalizes STORED slugs to compare against the incoming
        candidate, so the function must be a no-op on its own output."""
        once = Fr52AttachmentRouter._slug_from_filename("report_v2.xlsx")
        assert Fr52AttachmentRouter._slug_from_filename(once) == once


# ---------------------------------------------------------------------------
# 2. Router Step C family resolution
# ---------------------------------------------------------------------------


class TestStepCFamilyResolution:
    # Base document: slugs to `signal_test_report`, classifies as test_report,
    # and substring-matches the candidate item's `test_report` tag.
    V1 = "signal_test_report.xlsx"
    V2_SAME_NAME = "signal_test_report.xlsx"
    V2_DIFF_NAME = "signal_test_report_v2.xlsx"
    FAMILY = "signal_test_report"

    async def test_new_document_opens_family_at_rev1(self):
        result = await self._route(InMemoryStorage(), self.V1)
        assert result.doc_id_slug == self.FAMILY
        assert result.rev_number == 1

    async def test_same_filename_resend_becomes_rev2(self):
        """The regression that motivated REV-1: previously rev stayed 1, which
        collided on uq_doc_slug_rev and overwrote rev1's bytes on disk."""
        storage = self._with_family(rev=1)
        result = await self._route(storage, self.V2_SAME_NAME)
        assert result.doc_id_slug == self.FAMILY
        assert result.rev_number == 2

    async def test_different_filename_resend_joins_the_same_family(self):
        """`..._v2.xlsx` must land as rev2 of the same family, not fork a new
        one -- this is what lets upload selection pick a single winner."""
        storage = self._with_family(rev=1)
        result = await self._route(storage, self.V2_DIFF_NAME)
        assert result.doc_id_slug == self.FAMILY
        assert result.rev_number == 2

    async def test_rev_continues_from_family_tip_not_from_one(self):
        storage = self._with_family(rev=4)
        result = await self._route(storage, "signal_test_report_v5.xlsx")
        assert result.rev_number == 5

    async def test_legacy_unstripped_slug_still_matches_and_keeps_its_spelling(self):
        """Rows written before REV-1 carry un-stripped slugs. A new arrival
        must join them, and the STORED spelling must be preserved -- rewriting
        it would orphan the existing rows and their unique-index entries."""
        storage = self._with_family(slug="signal_test_report_v2", rev=1)
        result = await self._route(storage, self.V1)
        assert result.doc_id_slug == "signal_test_report_v2"   # stored spelling
        assert result.rev_number == 2

    async def test_unrelated_document_opens_its_own_family(self):
        storage = self._with_family(rev=3)
        result = await self._route(storage, "antenna_test_report.xlsx")
        assert result.doc_id_slug == "antenna_test_report"
        assert result.rev_number == 1

    async def test_storage_failure_degrades_to_new_document_not_staged(self):
        """A storage hiccup must not strand the file in _staged_revision --
        forking a family is recoverable by the TPM, stranding is not."""
        class _Boom(InMemoryStorage):
            async def find_doc_id_slugs_for_item(self, delivery_item_id, doc_type):
                raise RuntimeError("db down")

        result = await self._route(_Boom(), self.V1)
        assert result.doc_id_slug == self.FAMILY
        assert result.rev_number == 1

    async def test_max_rev_failure_falls_back_to_rev1(self):
        class _Boom(InMemoryStorage):
            async def get_max_rev_for_slug(self, milestone_id, doc_id_slug):
                raise RuntimeError("db down")

        storage = _Boom()
        storage.slugs_for_item[
            ("MMK-SM-S671U1-P1-10", DocType.TEST_REPORT.value)
        ] = [self.FAMILY]

        result = await self._route(storage, self.V1)
        assert result.doc_id_slug == self.FAMILY
        assert result.rev_number == 1

    # -- helpers -----------------------------------------------------------

    def _with_family(self, *, slug: str | None = None, rev: int) -> InMemoryStorage:
        """Storage pre-seeded with one existing revision family for the item."""
        slug = slug or self.FAMILY
        storage = InMemoryStorage()
        storage.slugs_for_item[
            ("MMK-SM-S671U1-P1-10", DocType.TEST_REPORT.value)
        ] = [slug]
        storage.max_rev_for_slug[(MILESTONE, slug)] = rev
        return storage

    # -- helper ------------------------------------------------------------

    async def _route(self, storage: InMemoryStorage, filename: str):
        router = _router(storage)
        return await router.route(_attachment(filename), "BATCH-REV1", [_item()])


# ---------------------------------------------------------------------------
# 3. storage.get_max_rev_for_slug
# ---------------------------------------------------------------------------


class TestGetMaxRevForSlug:
    """Exercises the real Postgres/SQLite path, not the in-memory mock."""

    @pytest.fixture(autouse=True)
    async def storage_env(self, tmp_path):
        from core.src.storage import configure_engine, init_db
        from core.src.storage.config import GlobalStorageConfig, set_storage_config
        set_storage_config(GlobalStorageConfig(nsd_mount_root=tmp_path / "nsd"))
        engine = configure_engine("sqlite+aiosqlite:///:memory:")
        await init_db()
        yield
        await engine.dispose()
        set_storage_config(None)

    async def test_returns_zero_for_unknown_family(self):
        from core.src.storage import get_max_rev_for_slug
        assert await get_max_rev_for_slug("P1", "nothing-here") == 0

    async def test_returns_highest_rev_in_family(self):
        from core.src.storage import add_document_index_row, get_max_rev_for_slug
        for rev, h in ((1, "a"), (3, "b"), (2, "c")):
            await add_document_index_row(self._doc(h * 64, rev))
        assert await get_max_rev_for_slug("P1", "report") == 3

    async def test_scoped_by_milestone(self):
        """DRR and P1 reuse slugs; the tip must not leak across milestones."""
        from core.src.storage import add_document_index_row, get_max_rev_for_slug
        await add_document_index_row(self._doc("a" * 64, 1))
        await add_document_index_row(self._doc("b" * 64, 7, milestone="DRR"))
        assert await get_max_rev_for_slug("P1", "report") == 1
        assert await get_max_rev_for_slug("DRR", "report") == 7

    async def test_ignores_staged_rows_with_null_rev(self):
        """Staged rows aren't family members until their slug/rev resolve --
        matches list_revisions' contract."""
        from core.src.storage import add_document_index_row, get_max_rev_for_slug
        await add_document_index_row(self._doc("a" * 64, 2))
        await add_document_index_row(
            self._doc("b" * 64, None, slug=None)
        )
        assert await get_max_rev_for_slug("P1", "report") == 2

    @staticmethod
    def _doc(file_hash: str, rev: int | None, *, milestone: str = "P1",
             slug: str | None = "report") -> DocumentIndexRow:
        return DocumentIndexRow(
            file_hash=file_hash,
            milestone_id=milestone,
            doc_type=DocType.TEST_REPORT,
            doc_id_slug=slug,
            rev_number=rev,
            ingest_source=IngestSource.EMAIL,
            original_filename="report.xlsx",
            routing_resolution=RoutingResolution.SUBSTRING_MATCH,
            ingested_at=NOW,
        )
