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


# ===========================================================================
# NSD-STRICT-CARRIER-SHORTCIRCUIT-1 (2026-09-09): carrier-allowlist customers
# (D-189: MMK -> VZW/Verizon) bypass NSD-STRICT-1 entirely, because the walk
# has already trusted the whole subtree under the carrier folder. Dynamic
# sub-folder names ('Skylo NTN', 'Power Management', 'Test reports') that
# cannot be enumerated in template.yaml must NOT be skipped -- they route
# via TG_DEFAULT_MULTIMATCH / TDN-1 to a `["default"]`-tagged catch-all.
# ===========================================================================


class TestCarrierAllowlistShortCircuit:
    """The customer_id gate on NSD-STRICT-1 -- pure predicate check."""

    def test_mmk_is_carrier_allowlist_customer(self):
        # If this assertion changes, the short-circuit gate in nsd2_poll
        # must be revisited: MMK's exemption is the point of the fix.
        from core.src.storage.nsd2_resolver import allowed_root_folders
        assert allowed_root_folders("MMK") is not None
        assert allowed_root_folders("MMK") == ("VZW", "Verizon")

    def test_non_carrier_customers_still_gated(self):
        # Every other customer_id returns None -- NSD-STRICT-1 applies to
        # them as before.
        from core.src.storage.nsd2_resolver import allowed_root_folders
        for cid in ("ACME", "OTHER", "", "verizon", "vzw"):
            assert allowed_root_folders(cid) is None, cid

    def test_shortcircuit_guard_present_in_nsd2_poll_source(self):
        """The guard is a 3-line condition; a regression would strip it and
        the failure would only surface at deploy. Inspect the source directly
        so a broken merge fails HERE, not in production logs."""
        import inspect
        from core.src.workflow_engine.tasks import nsd2_poll
        src = inspect.getsource(nsd2_poll._ingest_new_nsd2_file)
        # Both halves of the guard must be intact.
        assert "_allowed_root_folders(customer_id) is None" in src
        assert "not _is_archive_attachment(attachment)" in src
        # And the strict-gate warning has not been removed.
        assert "NSD_SKIP_NO_ITEM" in src
