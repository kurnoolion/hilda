"""test_feedback_config.py -- unit tests for dashboard.feedback_config.

Covers loading the JSON, flat + grouped views, membership check, malformed
input, and the shipped config file's structure (smoke test).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.src.dashboard.feedback_config import (
    CATEGORIES,
    CATEGORY_BUG,
    CATEGORY_IMPROVEMENT,
    IMPROVEMENT_BUG_TYPE,
    clear_cache,
    flat_bug_types,
    grouped_bug_types,
    is_valid_bug_type,
    load_bug_types,
)


def _write_config(tmp_path: Path, payload: dict) -> Path:
    p = tmp_path / "feedback_bug_types.json"
    p.write_text(json.dumps(payload), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


class TestConstants:
    def test_categories_are_bug_and_improvement(self):
        assert CATEGORIES == (CATEGORY_BUG, CATEGORY_IMPROVEMENT)
        assert CATEGORY_BUG == "bug"
        assert CATEGORY_IMPROVEMENT == "improvement"

    def test_improvement_bug_type_is_other_other(self):
        # Composed form 'PHASE-description' -- OTHER category's single OTHER entry.
        assert IMPROVEMENT_BUG_TYPE == "OTHER-OTHER"


class TestLoadBugTypes:
    def test_loads_valid_json(self, tmp_path):
        p = _write_config(tmp_path, {
            "bug_types_by_category": {
                "SETUP": ["a", "b"],
                "OTHER": ["OTHER"],
            }
        })
        groups = load_bug_types(str(p))
        assert groups == {"SETUP": ["a", "b"], "OTHER": ["OTHER"]}

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_bug_types(str(tmp_path / "does_not_exist.json"))

    def test_missing_top_key_raises(self, tmp_path):
        p = _write_config(tmp_path, {"wrong_key": {}})
        with pytest.raises(ValueError, match="bug_types_by_category"):
            load_bug_types(str(p))

    def test_empty_top_key_raises(self, tmp_path):
        p = _write_config(tmp_path, {"bug_types_by_category": {}})
        with pytest.raises(ValueError, match="missing or empty"):
            load_bug_types(str(p))

    def test_empty_phase_list_raises(self, tmp_path):
        p = _write_config(tmp_path, {
            "bug_types_by_category": {"SETUP": []}
        })
        with pytest.raises(ValueError, match="non-empty list"):
            load_bug_types(str(p))

    def test_non_string_entry_raises(self, tmp_path):
        p = _write_config(tmp_path, {
            "bug_types_by_category": {"SETUP": ["ok", 42]}
        })
        with pytest.raises(ValueError, match="non-string entry"):
            load_bug_types(str(p))

    def test_empty_phase_key_raises(self, tmp_path):
        p = _write_config(tmp_path, {
            "bug_types_by_category": {"": ["ok"]}
        })
        with pytest.raises(ValueError, match="invalid phase key"):
            load_bug_types(str(p))


class TestGroupedBugTypes:
    def test_returns_dict_copy(self, tmp_path):
        p = _write_config(tmp_path, {
            "bug_types_by_category": {"SETUP": ["a"], "OTHER": ["OTHER"]}
        })
        g = grouped_bug_types(p)
        assert g == {"SETUP": ["a"], "OTHER": ["OTHER"]}


class TestFlatBugTypes:
    def test_composes_phase_hyphen_description(self, tmp_path):
        p = _write_config(tmp_path, {
            "bug_types_by_category": {
                "SETUP": ["setup button broken", "no email"],
                "OTHER": ["OTHER"],
            }
        })
        assert flat_bug_types(p) == [
            "SETUP-setup button broken",
            "SETUP-no email",
            "OTHER-OTHER",
        ]

    def test_preserves_phase_insertion_order(self, tmp_path):
        # JSON preserves order in Python 3.7+; verify order flows through.
        p = _write_config(tmp_path, {
            "bug_types_by_category": {
                "ZZZ-LAST": ["z"],
                "AAA-FIRST": ["a"],
            }
        })
        # Insertion order is ZZZ then AAA -- flat output must reflect that,
        # not sort alphabetically.
        assert flat_bug_types(p) == ["ZZZ-LAST-z", "AAA-FIRST-a"]


class TestIsValidBugType:
    def test_accepts_real_entry(self, tmp_path):
        p = _write_config(tmp_path, {
            "bug_types_by_category": {"SETUP": ["setup button broken"]}
        })
        assert is_valid_bug_type("SETUP-setup button broken", p) is True

    def test_rejects_garbage(self, tmp_path):
        p = _write_config(tmp_path, {
            "bug_types_by_category": {"SETUP": ["setup button broken"]}
        })
        assert is_valid_bug_type("random garbage", p) is False

    def test_case_sensitive(self, tmp_path):
        p = _write_config(tmp_path, {
            "bug_types_by_category": {"SETUP": ["setup button broken"]}
        })
        assert is_valid_bug_type("setup-setup button broken", p) is False


class TestShippedConfig:
    """Smoke test against the actual shipped config/feedback_bug_types.json.

    Anchors the architect-specified 9 categories + 24 bug_types (2026-07-30).
    If ops adds a category, this test's counts need bumping -- intentional
    friction to keep the shipped surface reviewed.
    """
    def test_shipped_config_loads(self):
        groups = grouped_bug_types()
        assert len(groups) == 9
        expected_categories = {
            "SETUP", "START-COLLECTION", "OWNER-RESPONSE", "APPROVE",
            "CLOSE-ITEM", "CLOSE-MILESTONE", "DELETE-MILESTONE",
            "FINAL-DRR", "OTHER",
        }
        assert set(groups.keys()) == expected_categories

    def test_shipped_config_flat_count(self):
        assert len(flat_bug_types()) == 24

    def test_improvement_default_bug_type_is_shipped(self):
        # IMPROVEMENT category always resolves to this bug_type; it MUST
        # exist in the shipped registry or submits will 400.
        assert is_valid_bug_type(IMPROVEMENT_BUG_TYPE) is True
