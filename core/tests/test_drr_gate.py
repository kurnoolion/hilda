"""DRR-GATE-1 (2026-08-28) -- tpm_notification is DRR-only.

Locks in the invariant that only milestone_ids beginning with "DRR"
(case-insensitive, trailing whitespace tolerated) fire the DRR closure
final-status email. Guards against a target_date accidentally set on a
non-DRR row (P1 / LTE-OTA / etc.) sending a DRR-branded email.

Pure-function tests on the module-level `_is_drr_milestone` helper.
"""
from core.src.workflow_engine.tasks.tpm_notification import _is_drr_milestone


class TestIsDrrMilestone:

    def test_exact_drr_matches(self):
        assert _is_drr_milestone("DRR") is True

    def test_drr_with_trailing_space_matches(self):
        # Real customer config had "DRR " / "DRR  " variants.
        assert _is_drr_milestone("DRR ") is True
        assert _is_drr_milestone("DRR  ") is True

    def test_drr_closure_matches(self):
        assert _is_drr_milestone("DRR Closure") is True

    def test_drr_version_matches(self):
        assert _is_drr_milestone("DRR Version 5.7") is True

    def test_lowercase_drr_matches(self):
        assert _is_drr_milestone("drr") is True
        assert _is_drr_milestone("drr closure") is True

    def test_mixed_case_drr_matches(self):
        assert _is_drr_milestone("Drr") is True
        assert _is_drr_milestone("dRr Version") is True

    def test_p1_does_not_match(self):
        assert _is_drr_milestone("P1") is False

    def test_lte_ota_does_not_match(self):
        assert _is_drr_milestone("LTE-OTA") is False

    def test_empty_does_not_match(self):
        assert _is_drr_milestone("") is False

    def test_none_does_not_match(self):
        assert _is_drr_milestone(None) is False

    def test_whitespace_only_does_not_match(self):
        assert _is_drr_milestone("   ") is False

    def test_word_containing_drr_but_not_prefix_does_not_match(self):
        # "MyDRR" is not a DRR milestone -- prefix match only.
        assert _is_drr_milestone("MyDRR") is False
        assert _is_drr_milestone("PreDRR Closure") is False
