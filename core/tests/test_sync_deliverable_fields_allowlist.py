"""Guards on `sync_deliverable_fields._STR_FIELDS` / `_BOOL_FIELDS` allowlists.

Post-import TPM edits to SP columns only propagate to Postgres when the field
name is present in these tuples. Silent omissions caused the COMMENT-SRC-1
(comment) and NSD2-7 (ingress_folder) production bugs. This test locks in
the current allowlist so any future removal is a deliberate, reviewed change.
"""
from __future__ import annotations

from core.src.workflow_engine.tasks import sync_deliverable_fields as sdf


def test_str_fields_allowlist_contains_all_expected_fields():
    """Named string fields TPMs can edit via SP UI that HILDA needs to sync
    to Postgres via the Deliverables-CHANGED alert path."""
    expected = {
        "target_folder",         # legacy Ph-1
        "tg_path_id",            # legacy Ph-1
        "item_path_id",          # legacy Ph-1
        "item_type",             # legacy Ph-1
        "path_id",               # legacy Ph-1
        "ingress_folder",        # NSD2-7 (2026-08-13)
        "plm_id",                # PLM-2 (2026-08-14)
        "actual_item_info",      # PLM-2 (2026-08-14)
    }
    assert set(sdf._STR_FIELDS) >= expected, (
        f"Missing from _STR_FIELDS: {expected - set(sdf._STR_FIELDS)}"
    )


def test_bool_fields_allowlist_contains_form_factor_flags():
    """Form-factor flags (per [D-084]) TPMs can toggle on SP items."""
    expected = {
        "handset", "tablet", "wearable",
    }
    assert set(sdf._BOOL_FIELDS) >= expected, (
        f"Missing from _BOOL_FIELDS: {expected - set(sdf._BOOL_FIELDS)}"
    )
