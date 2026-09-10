"""DOCTYPE-PRECEDENCE-1 + DOCTYPE-EXT-1: Step 1 classification hardening.

Both fixes came out of one live DRR ingest on 2026-09-02, where three files
named 'SM-DEVICE-001 Waiver Request_*.ppt' ended up stored as
compliance_certification_release_notes -- aligned and CLASSIFIED at rev1,
not staged, with no TPM signal. Two independent defects combined:

  DOCTYPE-EXT-1  The MMK rules file carried three different trailing
                 extension groups across 123 patterns, none including 'ppt',
                 and 32 compliance rules had 'pptxi' -- a typo that accepted
                 a non-existent extension while rejecting real .pptx. The
                 .ppt waivers therefore matched NO rule and went UNRESOLVED.

  DOCTYPE-PRECEDENCE-1  _classify_doc_type returned UNRESOLVED on ANY
                 multi-match, which handed the doc to singleton-alignment
                 auto-promotion -- classifying it from the routed item's
                 item_type rather than its own name.

Waiver precedence is the load-bearing property here: waivers are never
uploaded on the P1 submit path, so a waiver classified as anything else is
SENT to the carrier.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.src.email_service.inbound.attachment_router import (
    _CANONICAL_DOC_EXTENSIONS,
    _DOC_TYPE_PRECEDENCE,
    _canonicalize_extension_group,
    load_doc_type_rules,
)
from core.src.template_schema.enums import DocType, ItemType

MMK_RULES = Path("customizations/template_schemas/MMK/doc_type_filename_rules.yaml")
DEFAULT_RULES = Path("core/src/email_service/default_doc_type_rules.yaml")

# The three filenames from the live 2026-09-02 DRR ingest.
LIVE_PPT_WAIVERS = (
    "SM-DEVICE-001 Waiver Request_WPC Certi.ppt",
    "SM-DEVICE-001 Waiver Request_PTCRB_V2.ppt",
    "SM-DEVICE-001 Waiver Request_FCC Grant_V2.ppt",
)


class TestCanonicalizeExtensionGroup:
    def test_rewrites_a_trailing_group(self) -> None:
        out, did = _canonicalize_extension_group(
            r".*waiver.*\.(pdf|doc|docx|xlsx|pptx)$")
        assert did is True
        assert "ppt|" in out or "|ppt)" in out
        assert re.compile(out, re.IGNORECASE).search("x_waiver.ppt")

    def test_fixes_the_pptxi_typo(self) -> None:
        out, did = _canonicalize_extension_group(
            r".*HWCert.*\.(pdf|doc|docx|xlsx|pptxi|html|htm)$")
        assert did is True
        assert "pptxi" not in out
        rx = re.compile(out, re.IGNORECASE)
        assert rx.search("HWCert.pptx")      # real extension now accepted
        assert not rx.search("HWCert.pptxi")  # bogus one no longer is

    def test_leaves_a_pattern_without_the_shape_untouched(self) -> None:
        original = r".*something.*"
        out, did = _canonicalize_extension_group(original)
        assert (out, did) == (original, False)

    def test_is_idempotent(self) -> None:
        once, _ = _canonicalize_extension_group(r".*x.*\.(pdf)$")
        twice, _ = _canonicalize_extension_group(once)
        assert once == twice

    @pytest.mark.parametrize("ext", _CANONICAL_DOC_EXTENSIONS)
    def test_every_canonical_extension_is_accepted(self, ext: str) -> None:
        out, _ = _canonicalize_extension_group(r".*waiver.*\.(pdf)$")
        assert re.compile(out, re.IGNORECASE).search(f"a_waiver.{ext}"), ext

    def test_ppt_and_pptx_are_both_present(self) -> None:
        # The original bug was that neither file could be classified: 'ppt'
        # was absent everywhere and 'pptx' was absent from 32 rules.
        assert "ppt" in _CANONICAL_DOC_EXTENSIONS
        assert "pptx" in _CANONICAL_DOC_EXTENSIONS
        assert "pptxi" not in _CANONICAL_DOC_EXTENSIONS


class TestLoadedRulesAreNormalised:
    def test_no_loaded_mmk_pattern_retains_a_skewed_group(self) -> None:
        rules = load_doc_type_rules(MMK_RULES)
        assert rules, "MMK rules failed to load"
        for doc_type, pats in rules.items():
            for p in pats:
                assert "pptxi" not in p.pattern, f"{doc_type}: {p.pattern}"
                # Every pattern ends in the canonical group.
                assert p.pattern.endswith(
                    r"\.(" + "|".join(_CANONICAL_DOC_EXTENSIONS) + r")$"
                ), f"{doc_type}: {p.pattern}"

    def test_default_rules_normalise_too(self) -> None:
        rules = load_doc_type_rules(DEFAULT_RULES)
        assert rules
        for pats in rules.values():
            for p in pats:
                assert "pptxi" not in p.pattern

    @pytest.mark.parametrize("name", LIVE_PPT_WAIVERS)
    def test_live_ppt_waivers_now_match_the_waiver_rule(self, name: str) -> None:
        rules = load_doc_type_rules(MMK_RULES)
        hit = [dt for dt, pats in rules.items()
               if any(p.search(name) for p in pats)]
        assert DocType.WAIVER.value in hit, f"{name} -> {hit}"

    def test_waiver_matches_regardless_of_document_extension(self) -> None:
        rules = load_doc_type_rules(MMK_RULES)
        waiver_pats = rules[DocType.WAIVER.value]
        for ext in _CANONICAL_DOC_EXTENSIONS:
            name = f"SM-DEVICE-001 Waiver Request.{ext}"
            assert any(p.search(name) for p in waiver_pats), name


class TestPrecedenceList:
    def test_waiver_is_first(self) -> None:
        # The only doc_type with an outward-facing consequence.
        assert _DOC_TYPE_PRECEDENCE[0] == DocType.WAIVER.value

    def test_order_is_waiver_compliance_tech_test(self) -> None:
        assert _DOC_TYPE_PRECEDENCE == (
            DocType.WAIVER.value,
            DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
            DocType.TECH_REPORT.value,
            DocType.TEST_REPORT.value,
        )

    def test_entries_are_unique(self) -> None:
        assert len(_DOC_TYPE_PRECEDENCE) == len(set(_DOC_TYPE_PRECEDENCE))

    def test_every_doc_type_in_the_mmk_rules_has_a_precedence_entry(self) -> None:
        # A doc_type present in the YAML but absent here would fall through
        # the precedence loop and stage instead of classifying.
        rules = load_doc_type_rules(MMK_RULES)
        missing = set(rules) - set(_DOC_TYPE_PRECEDENCE)
        assert not missing, f"doc_types with no precedence entry: {missing}"

    def test_every_doc_type_in_the_default_rules_has_an_entry(self) -> None:
        rules = load_doc_type_rules(DEFAULT_RULES)
        missing = set(rules) - set(_DOC_TYPE_PRECEDENCE)
        assert not missing, f"doc_types with no precedence entry: {missing}"

    def test_precedence_entries_are_real_doc_types(self) -> None:
        valid = {d.value for d in DocType}
        assert set(_DOC_TYPE_PRECEDENCE) <= valid


def _classifier(rules_path: Path):
    """Bare Fr52AttachmentRouter with only the rules wired -- enough to
    exercise _classify_doc_type without storage/LLM."""
    from core.src.email_service.inbound.attachment_router import (
        Fr52AttachmentRouter,
    )
    r = Fr52AttachmentRouter.__new__(Fr52AttachmentRouter)
    loaded = load_doc_type_rules(rules_path)
    r._rules = lambda: loaded            # type: ignore[method-assign]
    return r


class TestClassifyDocTypePrecedence:
    @pytest.mark.parametrize("name", LIVE_PPT_WAIVERS)
    def test_live_ppt_waivers_classify_as_waiver(self, name: str) -> None:
        # The regression. Two of these three match BOTH the waiver rule and
        # a compliance rule, so they exercise precedence, not just the
        # extension fix.
        from core.src.email_service.protocol import ClassificationResolution
        doc_type, res = _classifier(MMK_RULES)._classify_doc_type(name)
        assert doc_type == DocType.WAIVER.value
        assert res == ClassificationResolution.FILENAME_REGEX

    def test_multi_match_resolves_to_waiver_over_compliance(self) -> None:
        doc_type, _ = _classifier(MMK_RULES)._classify_doc_type(
            "SM-DEVICE-001 Waiver Request_WPC Certi.ppt")
        assert doc_type == DocType.WAIVER.value

    def test_single_match_is_unaffected(self) -> None:
        c = _classifier(MMK_RULES)
        assert c._classify_doc_type(
            "M3 Power Management Test Result_HQ.xlsx"
        )[0] == DocType.TEST_REPORT.value
        assert c._classify_doc_type(
            "HWCertReport_1152921505698422460.html"
        )[0] == DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value
        assert c._classify_doc_type(
            "SM-DEVICE-004_WFA137464_(20250908).pdf"
        )[0] == DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value

    def test_volte_odr_resolves_to_compliance_after_star_fix(self) -> None:
        """DOCTYPE-STAR-1 (2026-09-04): this file previously classified
        test_report and misaligned against its MNO-UX compliance work-item,
        staging silently out of the submission.

        Two independent fixes had to land for it to resolve correctly:
        `volte*ODR` had to become `volte.*ODR` (a bare `*` means
        "zero-or-more of the PRECEDING CHARACTER", so it only ever matched
        'volteODR'), and DOCTYPE-PRECEDENCE-1 had to resolve multi-match by
        precedence instead of collapsing to UNRESOLVED -- the name matches
        BOTH `.*volte.*` (test_report) and `.*volte.*ODR.*` (compliance).
        """
        doc_type, _ = _classifier(MMK_RULES)._classify_doc_type(
            "[Miracle] VoLTE ODR.pdf")
        assert doc_type == DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value

    def test_no_match_still_unresolved(self) -> None:
        from core.src.email_service.protocol import ClassificationResolution
        doc_type, res = _classifier(MMK_RULES)._classify_doc_type(
            "totally_unknown_thing.pdf")
        assert doc_type == DocType.UNRESOLVED.value
        assert res == ClassificationResolution.UNRESOLVED_LOW_CONFIDENCE

    def test_non_document_extension_still_unresolved(self) -> None:
        # .msg / .zip are not classifiable documents; the canonical set is
        # deliberately limited to real document types.
        c = _classifier(MMK_RULES)
        for name in ("SM-DEVICE-001 Waiver Request.msg", "bundle.zip"):
            assert c._classify_doc_type(name)[0] == DocType.UNRESOLVED.value

    def test_precedence_is_logged(self, caplog) -> None:  # noqa: ANN001
        import logging
        c = _classifier(MMK_RULES)
        with caplog.at_level(logging.WARNING):
            c._classify_doc_type("SM-DEVICE-001 Waiver Request_WPC Certi.ppt")
        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert "DOCTYPE_PRECEDENCE" in blob
        assert "resolving to waiver by precedence" in blob


# ---------------------------------------------------------------------------
# CLASSIFY-BASENAME-1 (2026-09-09): classification uses ONLY the basename;
# folder segments never leak into doc_type. NSD ingest passes the full
# share-relative path here as the filename, and pre-fix the doc_type regex
# saw "Waiver" in "VZW/14. PTCRB (Waiver)/Certi/..." and auto-classified as
# waiver -- even when the actual filename said nothing about waivers. Now
# folder = ROUTING (via match_hint / item_description tags), filename =
# CLASSIFICATION.
# ---------------------------------------------------------------------------


def _classifier_with_inline_rules() -> object:
    """A classifier with a small in-memory ruleset -- independent of the
    checked-in MMK yaml, which is a sanitized placeholder in public github
    (D-125) and loads 0 patterns on machines without the corp copy. Every
    CLASSIFY-BASENAME-1 test drives this so it passes anywhere."""
    import re
    from core.src.email_service.inbound.attachment_router import (
        Fr52AttachmentRouter,
    )
    inline_rules: dict[str, list[re.Pattern[str]]] = {
        DocType.WAIVER.value: [re.compile(r"waiver", re.IGNORECASE)],
        DocType.TEST_REPORT.value: [re.compile(r"test.*report", re.IGNORECASE)],
    }
    r = Fr52AttachmentRouter.__new__(Fr52AttachmentRouter)
    r._rules = lambda: inline_rules            # type: ignore[method-assign]
    return r


class TestClassifyDocTypeUsesBasenameOnly:

    def test_folder_segment_containing_waiver_does_not_leak(self) -> None:
        # The reported failure: filename has no doc-type token; a folder in
        # the path does. Pre-fix: classified as waiver. Post-fix: UNRESOLVED
        # -> stages, TPM reclassifies via WAIVER-UNIVERSAL-1 one-click.
        doc_type, _ = _classifier_with_inline_rules()._classify_doc_type(
            "VZW/14. PTCRB (Waiver)/Certi/"
            "SM-S948U1_REV1.0_0_S948U1.001(S948U1UEU8YKG)_SVN01.pdf"
        )
        assert doc_type == DocType.UNRESOLVED.value

    def test_folder_named_test_report_does_not_leak(self) -> None:
        # Same rule for other doc types: a folder named after a doc-type
        # pattern must not carry through to a nothing-matching filename.
        doc_type, _ = _classifier_with_inline_rules()._classify_doc_type(
            "VZW/Test Report Folder/SM-anonymous.pdf"
        )
        assert doc_type == DocType.UNRESOLVED.value

    def test_basename_match_still_wins_over_folder(self) -> None:
        # A real waiver .ppt in ANY folder still classifies as waiver -- the
        # pattern lives in the basename now.
        doc_type, _ = _classifier_with_inline_rules()._classify_doc_type(
            "some/deep/random/path/SM-DEVICE-001 Waiver Request.ppt"
        )
        assert doc_type == DocType.WAIVER.value

    def test_bare_basename_still_classifies(self) -> None:
        # Regression: email ingest passes a bare basename (no path); strip
        # must be a no-op there.
        doc_type, _ = _classifier_with_inline_rules()._classify_doc_type(
            "SM-DEVICE-001 Waiver Request.ppt"
        )
        assert doc_type == DocType.WAIVER.value

    def test_windows_style_path_separators_are_stripped(self) -> None:
        # Defensive: PurePosixPath treats backslashes as filename characters,
        # not separators. The router accepts forward-slash paths (NSD walk
        # yields those; NEST-1 zip inner paths are forward-slash; PLM
        # normalises to forward-slash). If a backslash ever arrives, the
        # basename is the whole string -- and if that whole string contains
        # a doc-type token in a folder segment, it still leaks. Document
        # that expectation here so a future backslash path is investigated
        # rather than assumed to work.
        doc_type, _ = _classifier_with_inline_rules()._classify_doc_type(
            r"VZW\14. PTCRB (Waiver)\SM-anonymous.pdf"
        )
        # Backslash IS a filename character on POSIX -> whole string is the
        # basename -> "(Waiver)" leaks. Callers must normalise separators
        # BEFORE reaching the classifier (NSD walk uses .as_posix()).
        assert doc_type == DocType.WAIVER.value

    def test_no_extension_no_folder_still_unresolved(self) -> None:
        doc_type, _ = _classifier_with_inline_rules()._classify_doc_type(
            "totally_unknown_thing"
        )
        assert doc_type == DocType.UNRESOLVED.value

    def test_empty_string_unresolved(self) -> None:
        doc_type, _ = _classifier_with_inline_rules()._classify_doc_type("")
        assert doc_type == DocType.UNRESOLVED.value

    def test_folder_named_test_report_does_not_promote_test_report(self) -> None:
        # A folder called 'Test Report Folder' would have matched the
        # test.*report pattern pre-fix. Post-fix the filename decides.
        doc_type, _ = _classifier_with_inline_rules()._classify_doc_type(
            "VZW/Test Report Folder/SM-anonymous-file.pdf"
        )
        assert doc_type == DocType.UNRESOLVED.value

    def test_filename_says_waiver_uses_basename(self) -> None:
        # The reported failure: an NSD file whose FILENAME says nothing about
        # waivers -- 'A3LSMS948U WPT RF Exposure Test Report revD.pdf' --
        # sits inside a folder called '7. FCC (Waiver)/Test reports/'. The
        # DOCTYPE-WAIVER-VETO promotion guard called filename_says_waiver on
        # the FULL PATH and force-classified the doc as WAIVER, exactly the
        # reverse of CLASSIFY-BASENAME-1's intent (folder = routing,
        # filename = classification). filename_says_waiver must now strip
        # the path first.
        from core.src.email_service.inbound.attachment_router import (
            filename_says_waiver,
        )
        # Folder says waiver, filename does not -> no veto.
        assert filename_says_waiver(
            "VZW/7. FCC (Waiver)/Test reports/"
            "A3LSMS948U WPT RF Exposure Test Report revD.pdf"
        ) is False
        # Filename itself says waiver -> veto fires.
        assert filename_says_waiver(
            "VZW/anything/SM-DEVICE-001 Waiver Request.ppt"
        ) is True
        # Bare basename still works (email ingest path).
        assert filename_says_waiver("SM-DEVICE-001 Waiver Request.ppt") is True
        assert filename_says_waiver("A3LSMS948U WPT RF Test.pdf") is False
        # Edge cases: empty / None-ish.
        assert filename_says_waiver("") is False
        assert filename_says_waiver(None) is False  # type: ignore[arg-type]

    def test_keyword_fallback_uses_basename(self) -> None:
        # keyword_fallback_doc_type also folded onto the basename so a
        # 'Technical Reports/foo.pdf' folder cannot promote a foo.pdf to
        # TECH_REPORT from the folder name alone.
        from core.src.email_service.inbound.attachment_router import (
            keyword_fallback_doc_type,
        )
        ttwr = ItemType.TEST_TECH_WAIVER_REPORT.value
        # Folder says "Technical Reports"; basename is neutral.
        # Rung 3 defaults to TEST_REPORT for any non-empty name; the check
        # here is that TECH_REPORT is NOT emitted from a folder-only signal.
        assert keyword_fallback_doc_type(
            "VZW/Technical Reports Folder/SM-anon-file.pdf", ttwr,
        ) == DocType.TEST_REPORT
        # Folder says "waiver"; basename does not -> no waiver from folder.
        assert keyword_fallback_doc_type(
            "VZW/(Waiver)/SM-anon-file.pdf", ttwr,
        ) == DocType.TEST_REPORT
        # Basename says waiver -> WAIVER, path notwithstanding.
        assert keyword_fallback_doc_type(
            "some/deep/path/SM-DEVICE-001 Waiver Request.ppt", ttwr,
        ) == DocType.WAIVER

    def test_precedence_log_shows_basename_not_full_path(self, caplog) -> None:  # noqa: ANN001
        # Precedence-log diagnostics stay useful when the input is a path:
        # log the basename that actually drove the classification, not the
        # folder chain that could mislead a reader. Uses inline rules that
        # produce a multi-match on the BASENAME so the precedence log fires.
        import logging
        import re
        from core.src.email_service.inbound.attachment_router import (
            Fr52AttachmentRouter,
        )
        multi_match_rules: dict[str, list[re.Pattern[str]]] = {
            DocType.WAIVER.value: [re.compile(r"waiver", re.IGNORECASE)],
            DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value: [
                re.compile(r"certi", re.IGNORECASE),
            ],
        }
        c = Fr52AttachmentRouter.__new__(Fr52AttachmentRouter)
        c._rules = lambda: multi_match_rules   # type: ignore[method-assign]
        with caplog.at_level(logging.WARNING):
            c._classify_doc_type(
                "VZW/Any Folder/SM-DEVICE-001 Waiver Request_WPC Certi.ppt"
            )
        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert "DOCTYPE_PRECEDENCE" in blob
        assert "SM-DEVICE-001 Waiver Request_WPC Certi.ppt" in blob
        # The folder prefix must not appear in the log line.
        assert "VZW/Any Folder" not in blob
