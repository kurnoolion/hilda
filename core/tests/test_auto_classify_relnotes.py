"""AUTO-CLASSIFY-RELNOTES-1 (2026-08-27) -- pure-function tests for the
singleton-alignment helper used by both attachment_router.route() and
unrouted_ops.route_unrouted_to_item to auto-promote UNRESOLVED doc_types
on release-notes items without a TPM click.

Per user 2026-08-27: routing then classification is strictly sequential
within router.route(), so at the promotion seam both doc_type_value AND
routed item's item_type are in hand. Only item_type with a singleton
FR-86 alignment (currently: compliance_certification_release_notes)
triggers promotion; others (test_tech_waiver_report, Confirmation,
default) are ambiguous or accept-any and skip.

Pure-function tests; no DB / Celery / router surface.
"""
from core.src.email_service.inbound.attachment_router import (
    _singleton_alignment_doc_type,
)
from core.src.template_schema.enums import DocType, ItemType


class TestSingletonAlignmentDocType:

    def test_release_notes_returns_relnotes_doctype(self):
        result = _singleton_alignment_doc_type(
            ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value
        )
        assert result == DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES

    def test_test_tech_waiver_report_returns_none(self):
        # TTWR has 3 valid doc_types (test_report, tech_report, waiver);
        # cannot auto-pick; TPM must choose.
        result = _singleton_alignment_doc_type(
            ItemType.TEST_TECH_WAIVER_REPORT.value
        )
        assert result is None

    def test_confirmation_returns_none(self):
        # Confirmation accepts any doc_type per FR-86; no promotion signal.
        result = _singleton_alignment_doc_type(ItemType.CONFIRMATION.value)
        assert result is None

    def test_default_returns_none(self):
        # Default WI is catch-all; no promotion.
        result = _singleton_alignment_doc_type(ItemType.DEFAULT.value)
        assert result is None

    def test_empty_string_returns_none(self):
        assert _singleton_alignment_doc_type("") is None

    def test_unknown_item_type_returns_none(self):
        assert _singleton_alignment_doc_type("not_a_real_item_type") is None

    def test_result_aligns_via_fr86(self):
        # Belt-and-suspenders: the returned doc_type MUST pass FR-86
        # alignment against the input item_type. This test locks in the
        # invariant so future changes to _fr86_aligned that break the
        # promotion path are caught here.
        from core.src.email_service.inbound.attachment_router import (
            Fr52AttachmentRouter,
        )
        item_type = ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value
        result = _singleton_alignment_doc_type(item_type)
        assert result is not None
        assert Fr52AttachmentRouter._fr86_aligned(item_type, result.value)
