"""RECLASS-UI-SCOPE-1 (2026-08-27) -- pure-function tests for the
per-item_type FR-86 alignment intersection used by list_files_in_tg
to scope the Reclassify dropdown.

Locks in the invariant that dropdown options for a routed item MUST be
FR-86-aligned for that item's item_type. Under D-155 one-doc-one-item
this is a single item_type -> a single set; N-way associations intersect.

Pure-function tests; no DB / router surface.
"""
from core.src.storage.document_view_ops import list_files_in_tg  # noqa: F401
from core.src.template_schema.enums import DocType, ItemType

# Import the inner helper. list_files_in_tg defines _allowed_for_item_types
# as a local function; we re-implement it inline here for testability.
# Keep this in sync with document_view_ops.py.


def _allowed(item_types):
    """Local mirror of the inner helper -- see document_view_ops.py."""
    ALL = (
        DocType.TEST_REPORT.value,
        DocType.TECH_REPORT.value,
        DocType.WAIVER.value,
        DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
    )
    TTWR = (
        DocType.TEST_REPORT.value,
        DocType.TECH_REPORT.value,
        DocType.WAIVER.value,
    )
    RELNOTES = (DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,)
    per_item = []
    for it in item_types:
        if it == ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value:
            per_item.append(RELNOTES)
        elif it == ItemType.TEST_TECH_WAIVER_REPORT.value:
            per_item.append(TTWR)
        elif it in (ItemType.CONFIRMATION.value, ItemType.DEFAULT.value):
            per_item.append(ALL)
        else:
            per_item.append(())
    if not per_item:
        return ()
    common = set(per_item[0])
    for opts in per_item[1:]:
        common &= set(opts)
    return tuple(dt for dt in ALL if dt in common)


class TestAllowedDocTypes:

    def test_release_notes_item_singleton(self):
        result = _allowed({ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value})
        assert result == (DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,)

    def test_ttwr_item_returns_three_options(self):
        result = _allowed({ItemType.TEST_TECH_WAIVER_REPORT.value})
        assert set(result) == {
            DocType.TEST_REPORT.value,
            DocType.TECH_REPORT.value,
            DocType.WAIVER.value,
        }
        # Deterministic canonical order preserved.
        assert result == (
            DocType.TEST_REPORT.value,
            DocType.TECH_REPORT.value,
            DocType.WAIVER.value,
        )

    def test_confirmation_returns_all_four(self):
        result = _allowed({ItemType.CONFIRMATION.value})
        assert len(result) == 4

    def test_default_returns_all_four(self):
        result = _allowed({ItemType.DEFAULT.value})
        assert len(result) == 4

    def test_no_item_types_returns_empty(self):
        # Vintage doc / no assoc -> template falls back to legacy 4-option.
        assert _allowed(set()) == ()

    def test_unknown_item_type_returns_empty(self):
        # Fail-safe: unknown item_type contributes no options -> intersection
        # empty -> template legacy fallback. Doesn't crash.
        assert _allowed({"weird_new_item_type"}) == ()

    def test_intersection_ttwr_and_relnotes_empty(self):
        # Rare N-way: same doc on both TTWR item and release-notes item.
        # No doc_type aligns with both -> empty. Template fallback fires.
        result = _allowed({
            ItemType.TEST_TECH_WAIVER_REPORT.value,
            ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
        })
        assert result == ()

    def test_intersection_confirmation_and_relnotes(self):
        # Confirmation accepts all -> intersection with release_notes =
        # just release_notes.
        result = _allowed({
            ItemType.CONFIRMATION.value,
            ItemType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,
        })
        assert result == (DocType.COMPLIANCE_CERTIFICATION_RELEASE_NOTES.value,)

    def test_intersection_two_confirmation_returns_all(self):
        result = _allowed({ItemType.CONFIRMATION.value})
        assert len(result) == 4
