"""D-151 TG-scoped attachment routing helpers per architect 2026-07-22.

Tests the router's internal `_tg_scoped_route` + `_route_within_tg` methods
directly (no full-route harness), plus the DeliveryItemBase model validator
that rejects `"default"` mixed with other tags in a tag-set.
"""
from __future__ import annotations

import pytest

from core.src.email_service.inbound.attachment_router import Fr52AttachmentRouter
from core.src.storage.models import RoutingResolution
from core.src.template_schema import ItemType


# ---------------------------------------------------------------------------
# Minimal router instance for helper-level testing
# ---------------------------------------------------------------------------


class _Storage:
    async def get_document_index_row_by_hash(self, file_hash):
        return None


def _mk_router(ph1: bool = True) -> Fr52AttachmentRouter:
    """Bare Fr52AttachmentRouter — we call _tg_scoped_route directly, so
    rules_path + tg_resolver never fire.

    `ph1=True` (default) matches current production flag:
    ph1_first_pass_substring_only=True. Under this flag, TG_SINGLE_ITEM
    Stage 0 fallback is DISABLED (per architect 2026-07-25 Doc 3 review) —
    unmatched-in-any-TG docs fall through to milestone Default WI instead
    of accidentally landing on whichever TG happens to have a solo item.

    Pass `ph1=False` to exercise the Ph-2 shape where fuzzy/folder/LLM +
    TG_SINGLE_ITEM Stage 0 fallback all activate.
    """
    from pathlib import Path
    return Fr52AttachmentRouter(
        storage=_Storage(),
        llm=None,
        tg_resolver=None,
        doc_type_filename_rules_path=Path("/nonexistent"),
        ph1_first_pass_substring_only=ph1,
    )


def _item(item_id: str, tg_name: str = "TG-A",
          item_description=None, item_type: str | None = None) -> dict:
    return {
        "item_id":          item_id,
        "item_name":        f"item_{item_id}",
        "item_description": item_description,
        "item_type":        item_type or ItemType.TEST_TECH_WAIVER_REPORT.value,
        "tg_name":          tg_name,
    }


def _default_wi() -> dict:
    return _item("DEFAULT-WI", tg_name=None, item_type=ItemType.DEFAULT.value)


# ---------------------------------------------------------------------------
# _tg_scoped_route: end-to-end 4-stage pipeline
# ---------------------------------------------------------------------------


class TestTgScopedRoute:
    def test_stage0_tg_single_item_ph2(self):
        """TG=1 shortcut is a Ph-2 feature (requires owner-scoped candidate
        filtering to be safe). Under Ph-1 flag it's disabled; under Ph-2
        flag it fires as originally spec'd."""
        r = _mk_router(ph1=False)
        matches, res = r._tg_scoped_route(
            "anything.pdf",
            [_item("ITEM-1", tg_name="TG-A", item_description=None), _default_wi()],
        )
        assert res == RoutingResolution.TG_SINGLE_ITEM
        assert matches[0].item_id == "ITEM-1"

    def test_stage0_tg_single_item_suppressed_under_ph1_2026_07_25(self):
        """Ph-1 gate: TG_SINGLE_ITEM must NOT fire under the current
        substring-only mode. Verified by Doc 3 corp regression 2026-07-25 —
        was routing unmatched-anywhere docs to a solo-item Voice/DRR TG
        (item_no=84). Falling through to empty lets caller default-WI to
        the milestone Default WI (or absorb via ops if none configured)."""
        r = _mk_router(ph1=True)
        matches, res = r._tg_scoped_route(
            "anything.pdf",
            [_item("ITEM-1", tg_name="TG-A", item_description=None), _default_wi()],
        )
        assert matches == []
        assert res == RoutingResolution.SUBSTRING_MATCH  # fall-through sentinel

    def test_stage0_ignores_default_wi_in_count_ph2(self):
        # 1 real item in TG-A + Default WI (item_type='default') = still TG=1
        r = _mk_router(ph1=False)
        matches, res = r._tg_scoped_route(
            "nomatch.pdf",
            [_item("ITEM-1", tg_name="TG-A",
                   item_description=[["never_matches"]]), _default_wi()],
        )
        assert res == RoutingResolution.TG_SINGLE_ITEM

    def test_stage1_clean_single_match_wins_over_tg_default(self):
        """Q4: single unambiguous match wins over ["default"] fallback."""
        r = _mk_router()
        matches, res = r._tg_scoped_route(
            "waiver_document.pdf",
            [
                _item("A", tg_name="TG-A", item_description=[["waiver"]]),
                _item("B", tg_name="TG-A", item_description=[["default"]]),
            ],
        )
        assert res == RoutingResolution.SUBSTRING_MATCH
        assert matches[0].item_id == "A"

    def test_stage1_multimatch_default_wins(self):
        """Q3: multi-match tiebreaker → ["default"]-tagged wins."""
        r = _mk_router()
        matches, res = r._tg_scoped_route(
            "waiver_report.pdf",
            [
                _item("A", tg_name="TG-A", item_description=[["waiver"]]),
                _item("B", tg_name="TG-A", item_description=[["report"]]),
                _item("C", tg_name="TG-A",
                      item_description=[["waiver"], ["default"]]),
            ],
        )
        assert res == RoutingResolution.TG_DEFAULT_MULTIMATCH
        assert matches[0].item_id == "C"

    def test_stage1_multimatch_no_default_falls_through(self):
        r = _mk_router()
        matches, res = r._tg_scoped_route(
            "waiver.pdf",
            [
                _item("A", tg_name="TG-A", item_description=[["waiver"]]),
                _item("B", tg_name="TG-A", item_description=[["waiver"]]),
            ],
        )
        assert matches == []           # falls through -> caller uses B5 default
        assert res == RoutingResolution.SUBSTRING_MATCH

    @pytest.mark.skip(reason=(
        "Ph-2 deferred per architect 2026-07-22 Q3: TG_DEFAULT_NOMATCH "
        "(a TG's [\"default\"]-tagged item catching Stage-1-missed docs) "
        "is excluded from Ph-1 runtime. Restore this test when the "
        "commented block in _route_within_tg is re-enabled in Ph-2."
    ))
    def test_stage2_no_match_uses_tg_default_PH2(self):
        r = _mk_router()
        matches, res = r._tg_scoped_route(
            "zzz_random.pdf",
            [
                _item("A", tg_name="TG-A", item_description=[["waiver"]]),
                _item("B", tg_name="TG-A", item_description=[["default"]]),
            ],
        )
        assert res == RoutingResolution.TG_DEFAULT_NOMATCH
        assert matches[0].item_id == "B"

    def test_no_match_no_tg_default_falls_through(self):
        r = _mk_router()
        matches, res = r._tg_scoped_route(
            "zzz_random.pdf",
            [
                _item("A", tg_name="TG-A", item_description=[["waiver"]]),
                _item("B", tg_name="TG-A", item_description=[["report"]]),
            ],
        )
        assert matches == []
        assert res == RoutingResolution.SUBSTRING_MATCH

    def test_two_tg1_tgs_substring_disambiguates(self):
        """Q1/Q2 architect 2026-07-22: same-owner-multi-TG early-access case.
        Two TGs each with 1 item + item_description tags. Doc filename matches
        only one TG's tag. Substring runs FIRST (Ph-1 refinement) so:
          - matched TG returns SUBSTRING_MATCH
          - unmatched TG returns TG_SINGLE_ITEM
        Precedence SUBSTRING_MATCH > TG_SINGLE_ITEM picks the correct TG only.
        """
        r = _mk_router()
        matches, res = r._tg_scoped_route(
            "5g_report.pdf",
            [
                _item("A", tg_name="TG-A", item_description=[["5g"]]),
                _item("B", tg_name="TG-B", item_description=[["wifi"]]),
            ],
        )
        assert res == RoutingResolution.SUBSTRING_MATCH
        assert len(matches) == 1
        assert matches[0].item_id == "A"

    def test_two_tg1_tgs_no_substring_both_hit_default_wi_ph2(self):
        """D-153 architect 2026-07-25: cross-TG constraint — a doc can never
        route to items in multiple TGs. Two 1-item TGs both firing
        TG_SINGLE_ITEM used to fan out to both (pre-D-153); now returns empty
        so the caller uses milestone Default WI. Test flag=False to exercise
        the Ph-2 path where TG_SINGLE_ITEM is enabled; the aggregation
        constraint applies at both Ph-1 and Ph-2."""
        r = _mk_router(ph1=False)
        matches, res = r._tg_scoped_route(
            "random_zzz.pdf",
            [
                _item("A", tg_name="TG-A", item_description=[["5g"]]),
                _item("B", tg_name="TG-B", item_description=[["wifi"]]),
            ],
        )
        assert matches == []  # cross-TG TG_SINGLE_ITEM ambiguity → Default WI
        assert res == RoutingResolution.SUBSTRING_MATCH

    def test_two_tg1_tgs_only_one_matches_substring_ph2(self):
        """Ph-2: one TG has substring evidence, the other doesn't → route to
        the TG with evidence (SUBSTRING_MATCH wins over the sibling's
        TG_SINGLE_ITEM fallback which is suppressed by the evidence-first
        rule)."""
        r = _mk_router(ph1=False)
        matches, res = r._tg_scoped_route(
            "5g_report.pdf",
            [
                _item("A", tg_name="TG-A", item_description=[["5g"]]),
                _item("B", tg_name="TG-B", item_description=[["wifi"]]),
            ],
        )
        assert res == RoutingResolution.SUBSTRING_MATCH
        assert len(matches) == 1 and matches[0].item_id == "A"

    def test_two_tg1_tgs_no_substring_falls_through_under_ph1(self):
        """Ph-1 gate 2026-07-25: same scenario as _ph2 above but under Ph-1
        flag → TG_SINGLE_ITEM suppressed → no matches → caller falls to
        milestone Default WI. This is the fix for Doc 3."""
        r = _mk_router(ph1=True)
        matches, res = r._tg_scoped_route(
            "random_zzz.pdf",
            [
                _item("A", tg_name="TG-A", item_description=[["5g"]]),
                _item("B", tg_name="TG-B", item_description=[["wifi"]]),
            ],
        )
        assert matches == []
        assert res == RoutingResolution.SUBSTRING_MATCH  # sentinel for no-match

    def test_tg1_with_tags_but_no_hit_still_captures_ph2(self):
        """Ph-2: TG=1 item with tags but no substring hit → TG_SINGLE_ITEM
        fallback catches. Ph-1 counterpart asserts fall-through instead."""
        r = _mk_router(ph1=False)
        matches, res = r._tg_scoped_route(
            "no_matching_tags.pdf",
            [_item("A", tg_name="TG-A", item_description=[["compliance"]])],
        )
        assert res == RoutingResolution.TG_SINGLE_ITEM
        assert matches[0].item_id == "A"

    def test_tg1_with_tags_but_no_hit_falls_through_under_ph1(self):
        """Ph-1 counterpart: TG=1 item, no substring hit, flag=True →
        empty matches → caller uses milestone Default WI."""
        r = _mk_router(ph1=True)
        matches, res = r._tg_scoped_route(
            "no_matching_tags.pdf",
            [_item("A", tg_name="TG-A", item_description=[["compliance"]])],
        )
        assert matches == []

    # -----------------------------------------------------------------------
    # D-153 cross-TG constraint suite (architect 2026-07-25)
    # -----------------------------------------------------------------------

    def test_cross_tg_two_single_matches_go_to_default_wi(self):
        """D-153 rule 2 (refined): TG-A single-match + TG-B single-match →
        cannot route to both (view-tree TG scoping); Default WI instead."""
        r = _mk_router()  # ph1 default; behavior identical under both flags
        matches, res = r._tg_scoped_route(
            "sustainability_waiver_report.pdf",
            [
                _item("A", tg_name="TG-A", item_description=[["sustainability"]]),
                _item("B", tg_name="TG-B", item_description=[["waiver"]]),
            ],
        )
        assert matches == []
        assert res == RoutingResolution.SUBSTRING_MATCH

    def test_cross_tg_default_multimatch_plus_other_tg_evidence_defaults(self):
        """D-153: TG-A has multi-match resolved by ["default"] tiebreaker AND
        TG-B has single-match. Both TGs contributed evidence → cross-TG →
        Default WI, not TG_DEFAULT_MULTIMATCH-wins-over-SUBSTRING-cross-TG
        (which was the pre-D-153 D-151 precedence)."""
        r = _mk_router()
        matches, res = r._tg_scoped_route(
            "waiver_compliance.pdf",
            [
                # TG-A: 2 items both match "waiver", one has ["default"] tiebreak
                _item("A1", tg_name="TG-A", item_description=[["waiver"]]),
                _item("A2", tg_name="TG-A",
                      item_description=[["waiver"], ["default"]]),
                # TG-B: 1 item that also matches
                _item("B1", tg_name="TG-B", item_description=[["compliance"]]),
            ],
        )
        assert matches == []
        assert res == RoutingResolution.SUBSTRING_MATCH

    def test_cross_tg_intra_tg_multi_no_default_plus_other_tg_defaults(self):
        """D-153 rule 1 + cross-TG: TG-A multi-match no ["default"] AND TG-B
        single-match. Even though TG-A returns None (intra-TG ambiguity per
        rule 1) AND TG-B has a legit single match, both TGs contributed
        substring evidence → cross-TG → Default WI."""
        r = _mk_router()
        matches, res = r._tg_scoped_route(
            "waiver_compliance.pdf",
            [
                _item("A1", tg_name="TG-A", item_description=[["waiver"]]),
                _item("A2", tg_name="TG-A", item_description=[["waiver"]]),
                _item("B1", tg_name="TG-B", item_description=[["compliance"]]),
            ],
        )
        assert matches == []
        assert res == RoutingResolution.SUBSTRING_MATCH

    def test_intra_tg_multi_no_default_alone_defaults_ph1_and_ph2(self):
        """D-153 rule 1: intra-TG multi-match with no ["default"] tiebreaker
        → Default WI. When it's the only signal (no evidence in other TGs),
        this is naturally covered by the len(tgs_with_evidence)==1 branch
        returning None-match → empty."""
        r = _mk_router()
        matches, res = r._tg_scoped_route(
            "waiver.pdf",
            [
                _item("A1", tg_name="TG-A", item_description=[["waiver"]]),
                _item("A2", tg_name="TG-A", item_description=[["waiver"]]),
            ],
        )
        assert matches == []
        assert res == RoutingResolution.SUBSTRING_MATCH

    def test_single_tg_evidence_still_routes_normally(self):
        """D-153 doesn't change the happy path: evidence in exactly ONE TG
        (single-match here) still routes to that item."""
        r = _mk_router()
        matches, res = r._tg_scoped_route(
            "sustainability.pdf",
            [
                _item("A", tg_name="TG-A", item_description=[["sustainability"]]),
                _item("B", tg_name="TG-B", item_description=[["waiver"]]),
            ],
        )
        assert res == RoutingResolution.SUBSTRING_MATCH
        assert len(matches) == 1 and matches[0].item_id == "A"

    def test_multi_tg_matched_tg_default_wins(self):
        r = _mk_router()
        matches, res = r._tg_scoped_route(
            "waiver.pdf",
            [
                _item("A1", tg_name="TG-A", item_description=[["waiver"]]),
                _item("A2", tg_name="TG-A",
                      item_description=[["waiver"], ["default"]]),
                _item("B1", tg_name="TG-B", item_description=[["compliance"]]),
            ],
        )
        assert res == RoutingResolution.TG_DEFAULT_MULTIMATCH
        assert matches[0].item_id == "A2"


class TestHasDefaultTagSet:
    def test_default_alone_positive(self):
        assert Fr52AttachmentRouter._has_default_tag_set(
            {"item_description": [["default"]]}
        ) is True

    def test_default_in_own_set_positive(self):
        assert Fr52AttachmentRouter._has_default_tag_set(
            {"item_description": [["waiver"], ["default"]]}
        ) is True

    def test_default_mixed_negative(self):
        # Router's helper says "not tagged as default" because the ["default"]
        # tag-set isn't a singleton. Template validator rejects this shape
        # at load time separately.
        assert Fr52AttachmentRouter._has_default_tag_set(
            {"item_description": [["waiver", "default"]]}
        ) is False

    def test_no_default_negative(self):
        assert Fr52AttachmentRouter._has_default_tag_set(
            {"item_description": [["waiver"], ["compliance"]]}
        ) is False

    def test_no_item_description_negative(self):
        assert Fr52AttachmentRouter._has_default_tag_set(
            {"item_description": None}
        ) is False


# ---------------------------------------------------------------------------
# DeliveryItemBase._v_default_tag_isolation model validator
# ---------------------------------------------------------------------------


class TestDefaultTagValidator:
    def _mk(self, item_description):
        from datetime import datetime, timezone
        from core.src.template_schema.models import DeliveryItemBase
        from core.src.template_schema.enums import DeliveryState
        return DeliveryItemBase(
            item_id="ITEM-1",
            item_no=1,
            item_name="test",
            item_description=item_description,
            item_type=ItemType.TEST_TECH_WAIVER_REPORT.value,
            delivery_state=DeliveryState.NOT_STARTED.value,
            tracking_modality=["Email"],
            doc_count=len(item_description or []) or 1,
            sort_order=1,
            path_id="test",
            last_updated=datetime.now(timezone.utc),
            milestone_id="ms-1",
        )

    def test_default_alone_ok(self):
        self._mk([["default"]])

    def test_default_own_set_ok(self):
        self._mk([["waiver"], ["default"]])

    def test_default_mixed_rejected(self):
        with pytest.raises(Exception) as exc:
            self._mk([["waiver", "default"]])
        assert "default" in str(exc.value).lower()

    def test_default_first_position_mixed_rejected(self):
        with pytest.raises(Exception) as exc:
            self._mk([["default", "sig_report"]])
        assert "default" in str(exc.value).lower()

    def test_case_insensitive_reject(self):
        with pytest.raises(Exception) as exc:
            self._mk([["Default", "waiver"]])
        assert "default" in str(exc.value).lower()
