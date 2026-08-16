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


# ---------------------------------------------------------------------------
# OWNER-2 (2026-08-14): 4-field owner identity is now list-typed
# ---------------------------------------------------------------------------


def test_split_owner_list_semicolon_and_comma_delimiters():
    """Both ';' (SP-convention) and ',' (TPM tolerance) accepted."""
    from core.src.workflow_engine.tasks.sp_alert_imports import _split_owner_list
    assert _split_owner_list("alice@corp; bob@corp") == ["alice@corp", "bob@corp"]
    assert _split_owner_list("alice@corp, bob@corp") == ["alice@corp", "bob@corp"]
    assert _split_owner_list("alice@corp;bob@corp,carol@corp") == [
        "alice@corp", "bob@corp", "carol@corp",
    ]


def test_split_owner_list_dedup_case_insensitive_preserves_first_seen_casing():
    from core.src.workflow_engine.tasks.sp_alert_imports import _split_owner_list
    # Duplicate "ALICE@corp" preserves first-seen "alice@corp.com"
    assert _split_owner_list("alice@corp.com; ALICE@corp.com; bob@corp") == [
        "alice@corp.com", "bob@corp",
    ]


def test_split_owner_list_handles_trailing_semicolon_and_whitespace():
    """SP frequently emits trailing ';' + inconsistent whitespace."""
    from core.src.workflow_engine.tasks.sp_alert_imports import _split_owner_list
    assert _split_owner_list("alice@corp; bob@corp;  ") == ["alice@corp", "bob@corp"]
    assert _split_owner_list("   ") == []
    assert _split_owner_list("") == []
    assert _split_owner_list(None) == []


def test_merge_owner_field_dual_writes_singular_and_list():
    """_merge_owner_field writes BOTH singular (first entry) AND _list (full)
    when body_kvs has a non-empty parsed value."""
    updates: dict = {}
    body_kvs = {"owner_corp_email": "alice@corp; bob@corp;"}
    sdf._merge_owner_field(updates, body_kvs, "owner_corp_email", "owner_corp_email_list")
    assert updates == {
        "owner_corp_email":      "alice@corp",
        "owner_corp_email_list": ["alice@corp", "bob@corp"],
    }


def test_merge_owner_field_null_guards_preserve_existing():
    """Missing key OR empty parsed list -> skip BOTH writes (null-guard)."""
    updates: dict = {}
    # Missing key
    sdf._merge_owner_field(updates, {}, "owner_name", "owner_name_list")
    assert updates == {}
    # Empty string value
    sdf._merge_owner_field(updates, {"owner_name": ""}, "owner_name", "owner_name_list")
    assert updates == {}
    # Whitespace-only + trailing delimiters that yield empty parsed list
    sdf._merge_owner_field(updates, {"owner_name": " ;; "}, "owner_name", "owner_name_list")
    assert updates == {}


def test_merge_owner_field_single_owner_still_populates_both():
    """Backward-compat: TPM entering just 'alice@corp' (no delimiter) still
    dual-writes singular + list-of-1."""
    updates: dict = {}
    sdf._merge_owner_field(
        updates, {"owner_corp_id": "alice"}, "owner_corp_id", "owner_corp_id_list",
    )
    assert updates == {"owner_corp_id": "alice", "owner_corp_id_list": ["alice"]}
