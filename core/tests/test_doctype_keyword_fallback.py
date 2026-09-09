"""DOCTYPE-FALLBACK-1: item_type-scoped keyword fallback for doc_type.

Third rung of the FR-85 ladder, reached only when no rule in
doc_type_filename_rules.yaml matched AND `_singleton_alignment_doc_type`
found no unambiguous answer. Scoped to item_type=test_tech_waiver_report,
whose FR-86-aligned set is {test_report, tech_report, waiver}.

The two properties that make it safe are pinned here:
  * 'waiver' wins over everything, because a waiver misfiled as a test
    report gets UPLOADED to the carrier on the P1 submit path.
  * bare 'report' is NOT a tech_report signal, and 'TR' only matches as a
    delimited token -- otherwise Control/Spectrum/Strategic would all
    classify as tech reports.
"""
from __future__ import annotations

import pytest

from core.src.email_service.inbound.attachment_router import (
    keyword_fallback_doc_type,
)
from core.src.email_service.protocol import ClassificationResolution
from core.src.template_schema.enums import DocType, ItemType

TT = ItemType.TEST_TECH_WAIVER_REPORT.value


class TestWaiverRung:
    """Highest-precedence rung: waivers are never uploaded, so a miss here
    is the one that ships a document to the carrier by mistake."""

    @pytest.mark.parametrize("name", [
        "SM-DEVICE-003_BT_Waiver_Request.pdf",
        "waiver.docx",
        "WAIVER_FORM.xlsx",
        "37535_bt_iot_waiver_signed.pdf",
        "Waiver.PDF",
    ])
    def test_waiver_in_name_classifies_waiver(self, name: str) -> None:
        assert keyword_fallback_doc_type(name, TT) is DocType.WAIVER

    def test_waiver_beats_tech_report_tokens(self) -> None:
        # Both signals present -- waiver must win, per the upload asymmetry.
        assert keyword_fallback_doc_type(
            "Technical Report Waiver.docx", TT) is DocType.WAIVER
        assert keyword_fallback_doc_type(
            "IMEI_WAIVER_TR_v2.xlsx", TT) is DocType.WAIVER

    def test_waiver_match_is_case_insensitive(self) -> None:
        for name in ("WaIvEr.pdf", "ABC_WAIVER_x.pdf", "abc_waiver_x.pdf"):
            assert keyword_fallback_doc_type(name, TT) is DocType.WAIVER


class TestTechReportRung:
    @pytest.mark.parametrize("name", [
        "SM-DEVICE-003_Technical_Report.pdf",
        "Technical Report.docx",
        "Tech Report - GPS.docx",
        "tech_report_v2.xlsx",
        "TechReport.pdf",
        "technicalreport.pdf",
    ])
    def test_phrase_forms(self, name: str) -> None:
        assert keyword_fallback_doc_type(name, TT) is DocType.TECH_REPORT

    @pytest.mark.parametrize("name", [
        "GPS_TR_Bluetooth.pdf",     # underscore-delimited
        "GPS-TR-v2.xlsx",           # hyphen-delimited
        "GPS TR summary.pdf",       # space-delimited
        "(TR) summary.pdf",         # paren-delimited
        "TR.pdf",                   # whole stem -- the rare case
        "GPS_TR.pdf",               # trailing before extension
    ])
    def test_tr_as_delimited_token(self, name: str) -> None:
        assert keyword_fallback_doc_type(name, TT) is DocType.TECH_REPORT

    @pytest.mark.parametrize("name", [
        "Control_Plane_Results.pdf",
        "Spectrum_Analysis.xlsx",
        "Country_List.pdf",
        "Extract_Transmit_Central.docx",
        "Strategic_Review.pdf",
        "Trace_Log.pdf",            # leading 'tr' but not a token
        "Attribute_Table.xlsx",
    ])
    def test_tr_substring_inside_words_is_not_a_signal(self, name: str) -> None:
        # Same failure mode as the NSD2 denylist's 'CHA' matching 'Charging'.
        assert keyword_fallback_doc_type(name, TT) is DocType.TEST_REPORT


class TestTestReportDefault:
    @pytest.mark.parametrize("name", [
        "IMEI_1_Test_Report.xlsx",
        "Power Management Report.pdf",
        "BT_IOT_37535_results.pdf",
        "random_file.pdf",
        "37893.xlsx",
        "no_extension_at_all",
    ])
    def test_everything_else_defaults_to_test_report(self, name: str) -> None:
        assert keyword_fallback_doc_type(name, TT) is DocType.TEST_REPORT

    def test_bare_report_is_not_a_tech_report_signal(self) -> None:
        # The bug this design avoids: nearly every TEST report filename
        # contains 'report', so keying on it would invert the two buckets.
        for name in ("Test_Report.pdf", "report.pdf", "Final Report.docx",
                     "HW_Report_v3.xlsx"):
            assert keyword_fallback_doc_type(name, TT) is DocType.TEST_REPORT, name


class TestItemTypeScoping:
    """Returns None -- leave UNRESOLVED, stage for TPM -- for every other
    item_type. Without this a release-notes slot could be handed
    test_report and land misaligned under FR-86."""

    @pytest.mark.parametrize("item_type", [
        ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
        ItemType.DEFAULT.value,
        "Confirmation",
        "",
        "some_future_item_type",
    ])
    def test_other_item_types_return_none(self, item_type: str) -> None:
        assert keyword_fallback_doc_type("anything.pdf", item_type) is None

    def test_waiver_name_still_returns_none_off_scope(self) -> None:
        # Scoping wins over the keyword -- the caller decides, not the name.
        assert keyword_fallback_doc_type(
            "BT_Waiver.pdf",
            ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
        ) is None


class TestDegenerateInput:
    @pytest.mark.parametrize("name", ["", "   ", None])
    def test_empty_filename_returns_none(self, name) -> None:  # noqa: ANN001
        assert keyword_fallback_doc_type(name, TT) is None


class TestResolutionMarker:
    def test_fallback_resolution_value_is_distinct(self) -> None:
        # Must not reuse FILENAME_REGEX: these docs are guesses and have to
        # stay countable so the fallback's hit rate can be measured.
        assert (ClassificationResolution.FILENAME_FALLBACK_KEYWORD.value
                == "FilenameFallbackKeyword")
        assert (ClassificationResolution.FILENAME_FALLBACK_KEYWORD
                is not ClassificationResolution.FILENAME_REGEX)

    def test_all_resolutions_remain_distinct(self) -> None:
        values = [r.value for r in ClassificationResolution]
        assert len(values) == len(set(values))


class TestReturnedDocTypesAreAligned:
    def test_every_outcome_is_in_the_fr86_aligned_set(self) -> None:
        # The three doc_types valid for test_tech_waiver_report. Returning
        # anything outside this set would stage instead of classify.
        allowed = {DocType.TEST_REPORT, DocType.TECH_REPORT, DocType.WAIVER}
        for name in ("a_waiver.pdf", "a_TR_file.pdf", "anything.pdf",
                     "Technical Report.docx", "Control.pdf"):
            assert keyword_fallback_doc_type(name, TT) in allowed, name
