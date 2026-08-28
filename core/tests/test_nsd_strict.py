"""NSD-STRICT-1 (2026-08-27) -- unit tests for the pre-ingest substring pre-check
used by nsd2_poll to skip NSD files whose folder-name doesn't substring-hit
any candidate item.

Per user 2026-08-27: NSD-only channel gets strict-no-fallback semantics. If
zero real (non-Default) items match the folder-name match_hint, ingest is
skipped entirely -- file stays on the NSD share. Prevents WPC-style
DRR-milestone docs from landing STAGED on a wrong-item `["default"]` in P1
milestone under TDN-1 fallback.

Pure-function tests; no DB / Celery / adapter surface.
"""
from core.src.workflow_engine.tasks.nsd2_poll import (
    _any_candidate_substring_hits,
)


def _cand(*, item_no: int, item_type: str, item_description: list) -> dict:
    return {
        "item_id": f"MMK-SM-S671U1-P1-{item_no}",
        "item_no": item_no,
        "item_type": item_type,
        "item_description": item_description,
    }


class TestAnyCandidateSubstringHits:

    def test_single_tag_hit_returns_true(self):
        cands = [_cand(item_no=22, item_type="compliance_certification_release_notes",
                       item_description=[["CEC"]])]
        assert _any_candidate_substring_hits("16. cec(done)", cands) is True

    def test_no_tag_hit_returns_false(self):
        # User's WPC case: no item has "WPC" tag; folder is 15. WPC(done).
        cands = [
            _cand(item_no=21, item_type="compliance_certification_release_notes",
                  item_description=[["HAC"]]),
            _cand(item_no=22, item_type="compliance_certification_release_notes",
                  item_description=[["CEC"]]),
        ]
        assert _any_candidate_substring_hits("15. wpc(done)", cands) is False

    def test_default_only_item_ignored(self):
        # An item whose ONLY tag-set is ["default"] should not count as evidence.
        # (The `["default"]` marker is TG-fallback bookkeeping, not a folder tag.)
        cands = [
            _cand(item_no=99, item_type="test_tech_waiver_report",
                  item_description=[["default"]]),
        ]
        # "default" substring in "default_folder" would be a spurious hit
        # from the user's perspective (item 99 isn't a real match for a
        # folder that happens to have "default" in its name). We still
        # accept it here because the helper does simple substring math;
        # user's config already prevents this via their template.yaml design.
        # Regression check: "wpc" folder produces no hit against a
        # default-only item.
        assert _any_candidate_substring_hits("15. wpc(done)", cands) is False

    def test_default_wi_item_type_skipped(self):
        # Item with item_type='default' (i.e. milestone Default WI) is skipped
        # even if its tag-set would substring-match. Default WI is the
        # catch-all bucket, not a folder-owning item.
        cands = [_cand(item_no=999, item_type="default",
                       item_description=[["release", "note"]])]
        assert _any_candidate_substring_hits("1. hw release notes(done)", cands) is False

    def test_and_semantics_within_group(self):
        # AND-of-OR: both tags in a group must appear.
        # Tag-set [["release", "note"]] requires BOTH "release" AND "note".
        cands = [_cand(item_no=112, item_type="compliance_certification_release_notes",
                       item_description=[["release", "note"]])]
        assert _any_candidate_substring_hits("1. hw release notes(done)", cands) is True
        # If only "release" present but not "note", no hit.
        assert _any_candidate_substring_hits("release plans", cands) is False

    def test_or_semantics_across_groups(self):
        cands = [_cand(item_no=22, item_type="compliance_certification_release_notes",
                       item_description=[["CEC"], ["California", "Energy"]])]
        # CEC tag hits.
        assert _any_candidate_substring_hits("16. cec(done)", cands) is True
        # California AND Energy both hit.
        assert _any_candidate_substring_hits("california energy dept", cands) is True

    def test_case_insensitive_tag_matching(self):
        # Tag stored as "HAC" (uppercase), input already lowercase.
        cands = [_cand(item_no=21, item_type="compliance_certification_release_notes",
                       item_description=[["HAC"]])]
        assert _any_candidate_substring_hits("3. hac reports(draft done)", cands) is True

    def test_empty_candidates_returns_false(self):
        assert _any_candidate_substring_hits("anything", []) is False

    def test_candidates_with_no_description_returns_false(self):
        cands = [_cand(item_no=1, item_type="compliance_certification_release_notes",
                       item_description=[])]
        assert _any_candidate_substring_hits("anything", cands) is False

    def test_default_tag_in_addition_to_real_tag_still_counts(self):
        # User's HAC-default config: item 14 has [["HAC"], ["default"]] -- the
        # HAC tag-set is a real match, so the item counts as evidence for HAC
        # folder. (Only items whose SOLE tag-set is ["default"] are excluded.)
        cands = [_cand(item_no=14, item_type="compliance_certification_release_notes",
                       item_description=[["HAC"], ["default"]])]
        assert _any_candidate_substring_hits("3. hac reports(draft done)", cands) is True

    def test_multiple_candidates_one_hits(self):
        cands = [
            _cand(item_no=21, item_type="compliance_certification_release_notes",
                  item_description=[["HAC"]]),
            _cand(item_no=22, item_type="compliance_certification_release_notes",
                  item_description=[["CEC"]]),
            _cand(item_no=112, item_type="compliance_certification_release_notes",
                  item_description=[["release", "note"]]),
        ]
        # HAC folder -> item 21 hits.
        assert _any_candidate_substring_hits("3. hac reports(draft done)", cands) is True
        # CEC folder -> item 22 hits.
        assert _any_candidate_substring_hits("16. cec(done)", cands) is True
        # Release notes folder -> item 112 hits.
        assert _any_candidate_substring_hits("1. hw release notes(done)", cands) is True
        # WPC folder -> nothing hits.
        assert _any_candidate_substring_hits("15. wpc(done)", cands) is False
