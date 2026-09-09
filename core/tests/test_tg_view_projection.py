"""MISALIGN-PROJ-1: the TG view's dict projection must carry every field its
template reads.

`browse_tg_files` does not hand `TgFileEntry` objects to Jinja -- it builds a
dict per file. In Jinja, `f.foo` on a dict falls back to `f["foo"]`, and a
MISSING key yields Undefined, which is silently falsy. So a template guarded
by `{% if f.some_flag %}` renders nothing at all when the projection forgot
`some_flag`, with no error, no log line, and no failing test.

That is not hypothetical. DOCTYPE-MISALIGN-UI-1 (2026-09-03) added
`is_staged_not_classified` to TgFileEntry and to view_tree_tg.html but not to
the projection, so the misaligned-document warning badge and the Reclassify
control never rendered for any misaligned document. It was found live on
2026-09-08 on MMK DRR / MNO-ETM, where the carrier-destination cell reported a
file as staged (that key WAS projected) while the Doc Type cell showed plain
text -- two cells reading a present key and a missing one, appearing to
contradict each other.

Unit tests on `list_files_in_tg` pass, because the dataclass is correct.
Template tests pass, because the markup is correct. Only the seam between
them is wrong, which is what this file checks.
"""
from __future__ import annotations

import ast
import pathlib
import re

import pytest

ROUTES = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "dashboard" / "document_view_routes.py"
)
TEMPLATE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "dashboard" / "templates" / "view_tree_tg.html"
)

# Jinja builtins and loop helpers reachable as `f.<name>` that are not
# projection keys.
_NOT_PROJECTION_KEYS = {"items", "keys", "values", "get", "update"}


def _projection_keys() -> set[str]:
    """String keys of the largest dict literal appended inside
    `browse_tg_files`. Located by walking the AST rather than by regex so a
    reformat cannot silently empty this set -- an empty set would make every
    assertion below vacuously true.
    """
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
    best: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {
            k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
        # The per-file projection is the one carrying the filename plus the
        # view path -- distinctive enough not to match the flash-params or
        # template-context dicts in the same function.
        if {"filename", "view_relative_path"} <= keys and len(keys) > len(best):
            best = keys
    return best


def _template_field_refs() -> set[str]:
    """Every `f.<name>` the TG template reads, excluding Jinja comments.

    Comments are stripped first: the `{# ... #}` blocks in this template
    discuss `f.is_staged` and `f.is_staged_not_classified` in prose, and
    counting those would mask a genuinely missing key.
    """
    text = TEMPLATE.read_text(encoding="utf-8")
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)
    return {
        m.group(1) for m in re.finditer(r"\bf\.([A-Za-z_][A-Za-z0-9_]*)", text)
    } - _NOT_PROJECTION_KEYS


def test_anchors_resolve():
    """A wrong path or a failed AST match would make the real test vacuous."""
    assert ROUTES.is_file() and TEMPLATE.is_file()
    keys = _projection_keys()
    assert "filename" in keys and len(keys) >= 15, keys
    refs = _template_field_refs()
    assert "filename" in refs and len(refs) >= 10, refs


def test_every_template_field_is_projected():
    """The regression guard. A field the template reads but the route does not
    project renders as Undefined -- falsy, silent, and invisible to every
    other test in the suite."""
    missing = sorted(_template_field_refs() - _projection_keys())
    assert not missing, (
        "view_tree_tg.html reads these fields but browse_tg_files does not "
        f"project them: {missing}. Jinja resolves a missing dict key to "
        "Undefined (falsy), so any {% if %} guarded on one renders nothing "
        "with no error."
    )


@pytest.mark.parametrize(
    "field",
    ["is_staged", "is_staged_not_classified", "allowed_doc_types",
     "item_type", "file_hash", "upload_excluded_reason",
     "carrier_destination", "target_folder_override", "migrated_to"],
)
def test_reclassify_and_destination_fields_specifically(field):
    """Named explicitly, not just covered by the sweep above: these drive the
    Reclassify control, the misalignment badge and the carrier-destination
    cell. `is_staged_not_classified` is the one that was actually missing."""
    assert field in _projection_keys()


UNROUTED_TEMPLATE = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "dashboard" / "templates" / "view_tree_unrouted.html"
)


def _candidate_projection_keys() -> set[str]:
    """Keys of the dict `_shape` builds for each manual-route dropdown option.

    Same seam, second instance: `_shape` in browse_unrouted projects
    DeliveryItemTable rows into dicts, and view_tree_unrouted.html reads them
    as `c.<field>`. A field added to the template but not to _shape renders
    as empty text in the option label rather than raising -- so the TPM sees
    a truncated label and picks from incomplete information.
    """
    tree = ast.parse(ROUTES.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        keys = {
            k.value for k in node.keys
            if isinstance(k, ast.Constant) and isinstance(k.value, str)
        }
        if {"delivery_item_id", "item_no", "item_name"} <= keys:
            return keys
    return set()


def _unrouted_candidate_refs() -> set[str]:
    text = UNROUTED_TEMPLATE.read_text(encoding="utf-8")
    text = re.sub(r"\{#.*?#\}", "", text, flags=re.DOTALL)
    return {
        m.group(1) for m in re.finditer(r"c\.([A-Za-z_][A-Za-z0-9_]*)", text)
    } - _NOT_PROJECTION_KEYS


def test_unrouted_candidate_anchors_resolve():
    assert UNROUTED_TEMPLATE.is_file()
    keys = _candidate_projection_keys()
    assert {"delivery_item_id", "item_no"} <= keys, keys
    assert _unrouted_candidate_refs(), "no c.<field> refs found"


def test_every_candidate_field_is_projected():
    """UNROUTED-ITEMTYPE-1 added item_type to the option label; this pins
    that the label and the projection stay in step."""
    missing = sorted(_unrouted_candidate_refs() - _candidate_projection_keys())
    assert not missing, (
        "view_tree_unrouted.html reads these candidate fields but _shape "
        f"does not project them: {missing}"
    )


def test_item_type_is_in_the_candidate_label():
    """Routing and doc_type classification are independent -- the router
    never consults doc_type -- so item_type is what tells a TPM whether a
    pick will align or land STAGED. The list is deliberately unfiltered per
    [D-192], which makes the label the only signal."""
    assert "item_type" in _candidate_projection_keys()
    assert "item_type" in _unrouted_candidate_refs()


def test_projection_matches_the_dataclass():
    """Every projected key that comes from a TgFileEntry attribute must be a
    real field on it -- catches a rename landing in the dataclass but not the
    projection, the same seam in the other direction."""
    from core.src.storage.document_view_ops import TgFileEntry

    fields = set(TgFileEntry.__dataclass_fields__)
    # Keys the route derives itself (tokens, pretty-printed values, open
    # mode) legitimately have no dataclass counterpart.
    derived = {
        "last_saved_at_pretty", "last_saved_by_pretty", "open_mode",
        "open_token", "download_token", "versions_token", "history_token",
    }
    unknown = sorted(_projection_keys() - fields - derived)
    assert not unknown, (
        f"projected keys with no TgFileEntry field and not known-derived: "
        f"{unknown}"
    )
