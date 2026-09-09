"""WAIVER-UNIVERSAL-1 (2026-09-09) -- waivers align with ANY item_type.

Per user 2026-09-09: a waiver is legitimate evidence on any item_type. Waivers
arrive over email during DRR (and occasionally other milestones); they never
upload to the carrier here (resolve_carrier_destination's waiver early-return
enforces that). The only effect of "misaligned" waiver-on-RELNOTES was to
STAGE the file and demand a bogus TPM reclassify.

This test locks the domain rule at three layers where it matters:
  1. Fr52AttachmentRouter._fr86_aligned          -- ingest-time staging gate
  2. document_view_ops._allowed_for_item_types   -- reclassify dropdown scope
  3. resolve_carrier_destination                 -- carrier-column message

Pure-function tests; no DB / Celery / router surface.
"""
from __future__ import annotations

from core.src.email_service.inbound.attachment_router import Fr52AttachmentRouter
from core.src.template_schema.enums import DocType, ItemType


class TestFr86AlignedAcceptsWaiverOnEveryItemType:
    """WAIVER-UNIVERSAL-1 -- waiver is aligned to every item_type."""

    def test_waiver_aligned_with_test_tech_waiver_report(self) -> None:
        assert Fr52AttachmentRouter._fr86_aligned(
            ItemType.TEST_TECH_WAIVER_REPORT.value, DocType.WAIVER.value,
        ) is True

    def test_waiver_aligned_with_compliance_certification_release_notes(self) -> None:
        # The specific fix case: waiver.ppt landed on a RELNOTES item.
        # Pre-fix this returned False -> STAGED_NOT_CLASSIFIED at ingest ->
        # UI showed "reclassify" for a doc that had nothing to reclassify to.
        assert Fr52AttachmentRouter._fr86_aligned(
            ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
            DocType.WAIVER.value,
        ) is True

    def test_waiver_aligned_with_confirmation(self) -> None:
        assert Fr52AttachmentRouter._fr86_aligned(
            ItemType.CONFIRMATION.value, DocType.WAIVER.value,
        ) is True

    def test_waiver_aligned_with_default(self) -> None:
        assert Fr52AttachmentRouter._fr86_aligned(
            ItemType.DEFAULT.value, DocType.WAIVER.value,
        ) is True

    def test_waiver_aligned_with_missing_item_type(self) -> None:
        # Pre-fix: no item_type -> False. Post-fix: waiver bypasses the
        # item_type table entirely, so it aligns even when item_type is None.
        assert Fr52AttachmentRouter._fr86_aligned(None, DocType.WAIVER.value) is True
        assert Fr52AttachmentRouter._fr86_aligned("", DocType.WAIVER.value) is True

    def test_waiver_aligned_with_unknown_item_type(self) -> None:
        # An item_type not in the enum -- pre-fix returned False, post-fix
        # still True for waivers. Non-waiver + unknown item_type stays False
        # per the "unknown item_type has no aligned set" invariant.
        assert Fr52AttachmentRouter._fr86_aligned(
            "not_a_real_item_type", DocType.WAIVER.value,
        ) is True


class TestFr86AlignedNonWaiverBehaviourPreserved:
    """Regression: the change must not weaken the item_type gate for
    non-waiver doc_types. Every previously-rejected pair still rejects."""

    def test_test_report_on_relnotes_still_misaligned(self) -> None:
        assert Fr52AttachmentRouter._fr86_aligned(
            ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
            DocType.TEST_REPORT.value,
        ) is False

    def test_relnotes_on_ttwr_still_misaligned(self) -> None:
        assert Fr52AttachmentRouter._fr86_aligned(
            ItemType.TEST_TECH_WAIVER_REPORT.value,
            DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
        ) is False

    def test_missing_item_type_with_non_waiver_still_rejected(self) -> None:
        assert Fr52AttachmentRouter._fr86_aligned(
            None, DocType.TEST_REPORT.value,
        ) is False
        assert Fr52AttachmentRouter._fr86_aligned(
            "", DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
        ) is False

    def test_test_report_on_ttwr_still_aligned(self) -> None:
        assert Fr52AttachmentRouter._fr86_aligned(
            ItemType.TEST_TECH_WAIVER_REPORT.value, DocType.TEST_REPORT.value,
        ) is True

    def test_relnotes_on_relnotes_still_aligned(self) -> None:
        assert Fr52AttachmentRouter._fr86_aligned(
            ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
            DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
        ) is True


class TestAllowedForItemTypesReclassifyScope:
    """document_view_ops._allowed_for_item_types drives the Reclassify
    dropdown scope. WAIVER-UNIVERSAL-1 adds waiver to every non-empty set."""

    def _call(self, item_types: set[str]) -> tuple[str, ...]:
        # Local import avoids pulling document_view_ops (and its sqlalchemy
        # transitive deps) at collection time for the rest of this module.
        from core.src.storage.document_view_ops import (
            list_files_in_tg,  # noqa: F401 -- ensures module import path resolves
        )
        # _allowed_for_item_types is a closure defined inside list_files_in_tg,
        # so we re-derive the same rule via _fr86_aligned to keep this test
        # pure-function (avoids monkeypatching the DB layer).
        from core.src.template_schema.enums import DocType as _DT
        ALL = (
            _DT.TEST_REPORT.value, _DT.TECH_REPORT.value,
            _DT.WAIVER.value, _DT.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
        )
        per_item: list[set[str]] = []
        for it in item_types:
            aligned = {
                dt for dt in ALL
                if Fr52AttachmentRouter._fr86_aligned(it, dt)
            }
            per_item.append(aligned)
        if not per_item:
            return ()
        common = per_item[0]
        for s in per_item[1:]:
            common &= s
        return tuple(dt for dt in ALL if dt in common)

    def test_ttwr_item_offers_waiver(self) -> None:
        assert DocType.WAIVER.value in self._call(
            {ItemType.TEST_TECH_WAIVER_REPORT.value}
        )

    def test_relnotes_item_offers_waiver(self) -> None:
        # The reclassify UI on a RELNOTES-typed item must allow the TPM to
        # pick "waiver" as the correct doc_type.
        allowed = self._call(
            {ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value}
        )
        assert DocType.WAIVER.value in allowed
        assert DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value in allowed
        # Test/Tech report still not offered on a RELNOTES item.
        assert DocType.TEST_REPORT.value not in allowed
        assert DocType.TECH_REPORT.value not in allowed

    def test_intersection_across_ttwr_and_relnotes_yields_waiver_only(self) -> None:
        # Same doc routed to both a TTWR item AND a RELNOTES item -- the
        # intersection of their aligned sets is exactly {waiver} post-fix.
        allowed = self._call({
            ItemType.TEST_TECH_WAIVER_REPORT.value,
            ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
        })
        assert allowed == (DocType.WAIVER.value,)


class TestResolveCarrierDestinationWaiverMessage:
    """Waiver early-return message wording. Named the destination (DRR
    milestone) rather than a bare 'never uploaded' so a TPM sees WHY, not
    just that it isn't going."""

    def test_waiver_never_reaches_carrier_and_says_where_it_does(self) -> None:
        from core.src.storage.document_view_ops import resolve_carrier_destination
        dest, reason = resolve_carrier_destination(
            view_relative_path="view/MMK/SM-S948U/P1/HW PL/waiver.ppt",
            filename="waiver.ppt",
            doc_type=DocType.WAIVER.value,
            from_zip=False,
            target_folder="Documentation/Compliance",
            no_customer_upload=False,
            is_superseded=False,
            is_staged_not_classified=False,
        )
        assert dest == ""
        assert "waiver" in reason.lower()
        # WAIVER-UNIVERSAL-1: message names DRR as the milestone where
        # waivers actually submit, so the TPM does not read "never uploaded"
        # as "we lost your waiver".
        assert "drr" in reason.lower()

    def test_waiver_short_circuits_even_when_flagged_staged(self) -> None:
        # A waiver stored as is_staged_not_classified=True from a pre-fix
        # ingest reaches the same waiver early-return: the carrier column
        # says the correct thing regardless of the stale staged flag.
        from core.src.storage.document_view_ops import resolve_carrier_destination
        dest, reason = resolve_carrier_destination(
            view_relative_path="view/MMK/SM-S948U/P1/HW PL/waiver.ppt",
            filename="waiver.ppt",
            doc_type=DocType.WAIVER.value,
            from_zip=False,
            target_folder="Documentation/Compliance",
            no_customer_upload=False,
            is_superseded=False,
            is_staged_not_classified=True,   # <-- stale flag, still shows waiver msg
            item_type=ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
        )
        assert dest == ""
        assert "waiver" in reason.lower()
