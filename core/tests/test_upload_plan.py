"""UPLOAD-PLAN-1: shared carrier-destination resolution.

These helpers were private to submit_to_carrier until 2026-09-06. They moved
because three consumers now need the identical answer -- the uploader, the TG
document view, and the download-all preview -- and a preview that computes a
different path from the uploader is worse than no preview at all.

That failure mode already happened once in another guise: DOCTYPE-MISALIGN-UI-1
found the TG view rendering a document as classified while
list_upload_files_for_item silently dropped it from the submission, because the
two read different predicates. These tests exist to keep the three callers on
one code path.
"""
from __future__ import annotations

import pytest

from core.src.storage.upload_plan import (
    ARCHIVE_EXTS,
    carrier_subdir,
    effective_target_dir,
    nsd_subdir_prefix_from_relative_path,
    plm_subdir_prefix_from_local_path,
    sanitize_subdir_segment,
    view_subdir_prefix,
)

VIEW = "view/MMK/SM-S671U1/P1/MNO-Solution/"
INTERNAL = "internal/MMK/SM-S671U1/P1/HW PL/item_10/rev1/"


class TestViewSubdirPrefix:
    @pytest.mark.parametrize("rel,expected", [
        (VIEW + "a.pdf",                          ""),
        (VIEW + "i am c/d.pdf",                   "i am c"),
        (VIEW + "b.zip/i am c/d.pdf",             "i am c"),
        (VIEW + "report.7z/folder/nested/x.pdf",  "folder/nested"),
        (VIEW + "outer.zip/inner.7z/VoNR/r.xlsx", "VoNR"),
        (VIEW + "5G/LC/Ericsson/latency.pdf",     "5G/LC/Ericsson"),
    ])
    def test_examples_from_the_docstring(self, rel: str, expected: str) -> None:
        assert view_subdir_prefix(rel) == expected

    def test_spaces_survive_verbatim(self) -> None:
        # The 'i am c' case is a real archive folder; sanitising must not
        # collapse or strip spaces.
        assert view_subdir_prefix(VIEW + "i am c/d.pdf") == "i am c"

    @pytest.mark.parametrize("rel", [
        "", "view", "view/a/b/c/d",              # shorter than scope + basename
        "internal/MMK/D/P1/TG/x/y.pdf",          # not the view tree
    ])
    def test_non_view_or_too_short_yields_empty(self, rel: str) -> None:
        assert view_subdir_prefix(rel) == ""

    def test_tree_is_tg_scoped_with_no_item_segment(self) -> None:
        """The property that makes a within-TG work-item change a no-op for
        the view tree: the path contains the TG but never the item."""
        assert view_subdir_prefix(VIEW + "sub/x.pdf") == "sub"
        # Same TG, different item -- identical result, because no item is
        # encoded in the path at all.
        assert view_subdir_prefix(VIEW + "sub/x.pdf") == view_subdir_prefix(
            "view/MMK/SM-S671U1/P1/MNO-Solution/sub/x.pdf")


class TestPlmSubdirPrefix:
    @pytest.mark.parametrize("rel,expected", [
        (INTERNAL + "a.pdf",                              ""),
        (INTERNAL + "b.zip/i am c/d.pdf",                 "i am c"),
        (INTERNAL + "outer.zip/inner.zip/x/y.pdf",        "x"),
        (INTERNAL + "report.7z/folder/nested/file.pdf",   "folder/nested"),
        ("internal/MMK/D/P1/TG/item_1/_staged_classification/report.zip/folder/x.pdf",
         "folder"),
    ])
    def test_examples_from_the_docstring(self, rel: str, expected: str) -> None:
        assert plm_subdir_prefix_from_local_path(rel) == expected

    def test_no_anchor_segment_yields_empty(self) -> None:
        assert plm_subdir_prefix_from_local_path("internal/a/b/c/x.pdf") == ""

    def test_empty_path_is_safe(self) -> None:
        assert plm_subdir_prefix_from_local_path("") == ""


class TestSanitizeSubdirSegment:
    def test_backslash_becomes_underscore(self) -> None:
        assert sanitize_subdir_segment(r"a\b") == "a_b"

    def test_control_characters_stripped(self) -> None:
        assert sanitize_subdir_segment("a\x01b\x1f") == "ab"

    def test_trailing_dots_and_spaces_stripped(self) -> None:
        assert sanitize_subdir_segment("folder. ") == "folder"

    def test_spaces_and_unicode_preserved(self) -> None:
        assert sanitize_subdir_segment("i am c") == "i am c"
        assert sanitize_subdir_segment("측정 결과") == "측정 결과"


class TestCarrierSubdir:
    """UPLOAD-FLAT-1: the from_zip gate is the whole point. An NSD file sitting
    in a share folder carries path segments that look identical to an archive's
    internal folders, and recreating those on the carrier leaked NSD folder
    names to Verizon once already."""

    def test_view_file_from_zip_keeps_structure(self) -> None:
        assert carrier_subdir(
            relative_path=VIEW + "UCTP_Ericsson/r.pdf",
            is_view=True, from_zip=True) == "UCTP_Ericsson"

    def test_view_file_not_from_zip_uploads_flat(self) -> None:
        # The UPLOAD-FLAT-1 regression: '2. DMDform (Done)/' must NOT reach
        # the carrier.
        assert carrier_subdir(
            relative_path=VIEW + "2. DMDform (Done)/r.pdf",
            is_view=True, from_zip=False) == ""

    def test_internal_file_plm_uses_plm_rule(self) -> None:
        assert carrier_subdir(
            relative_path=INTERNAL + "b.zip/i am c/d.pdf",
            is_view=False, from_zip=True,
            ingest_source="CorporatePLM") == "i am c"

    def test_internal_file_non_plm_uploads_flat(self) -> None:
        # UPLOAD-SUBDIR-PLM-1 is deliberately PLM-only on the internal path.
        for src in ("Email", "NetworkSharedDrive", ""):
            assert carrier_subdir(
                relative_path=INTERNAL + "b.zip/i am c/d.pdf",
                is_view=False, from_zip=True, ingest_source=src) == "", src

    def test_from_zip_is_ignored_on_the_internal_path(self) -> None:
        # The internal branch keys on ingest_source, not from_zip.
        assert carrier_subdir(
            relative_path=INTERNAL + "b.zip/i am c/d.pdf",
            is_view=False, from_zip=False,
            ingest_source="CorporatePLM") == "i am c"


class TestEffectiveTargetDir:
    def test_composes_with_subdir(self) -> None:
        assert effective_target_dir("A/B", "x/y") == "A/B/x/y"

    def test_empty_subdir_leaves_target_alone(self) -> None:
        assert effective_target_dir("A/B", "") == "A/B"

    def test_trailing_slash_on_target_is_normalised(self) -> None:
        assert effective_target_dir("A/B/", "x") == "A/B/x"
        assert effective_target_dir("A/B/", "") == "A/B"

    def test_empty_target_is_safe(self) -> None:
        assert effective_target_dir("", "x") == "/x" or \
               effective_target_dir("", "x") == "x"


class TestSubmitToCarrierStillUsesTheseHelpers:
    """The extraction must not have left submit_to_carrier with a private copy
    -- that is exactly how the uploader and the preview would drift."""

    def test_task_module_reexports_the_shared_functions(self) -> None:
        from core.src.workflow_engine.tasks import submit_to_carrier as sc
        assert sc._view_subdir_prefix is view_subdir_prefix
        assert sc._plm_subdir_prefix_from_local_path is \
            plm_subdir_prefix_from_local_path
        assert sc._sanitize_subdir_segment is sanitize_subdir_segment
        assert sc._carrier_subdir is carrier_subdir
        assert sc._effective_target_dir is effective_target_dir
        assert sc._ARCHIVE_EXTS is ARCHIVE_EXTS

    def test_shared_module_does_not_pull_in_celery(self) -> None:
        """The dashboard imports this; it must not drag the WORKER's tree in.

        Checked in a fresh interpreter against sys.modules -- grepping the
        source would trip over this very docstring, and checking in-process
        would see whatever pytest already imported.

        sqlalchemy is deliberately NOT asserted against: importing anything
        under core.src.storage runs that package's __init__, and the dashboard
        already imports document_view_ops, so sqlalchemy is present either way.
        Celery is the dependency that would actually be new.
        """
        import subprocess, sys, json
        out = subprocess.run(
            [sys.executable, "-c",
             "import json, sys; import core.src.storage.upload_plan; "
             "print(json.dumps(sorted(m for m in sys.modules "
             "if m.split('.')[0] == 'celery')))"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        assert json.loads(out) == [], f"upload_plan pulled in celery: {out}"

    def test_module_itself_imports_only_stdlib(self) -> None:
        """Separate from the sys.modules check: the MODULE's own import
        statements, read from its AST, must stay stdlib-only so it can be
        relocated out of core.src.storage later without a dependency shuffle."""
        import ast
        from pathlib import Path as _Path
        from core.src.storage import upload_plan
        tree = ast.parse(
            _Path(upload_plan.__file__).read_text(encoding="utf-8"))
        roots = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                roots.add(node.module.split(".")[0])
        assert roots <= {"__future__", "pathlib"}, f"non-stdlib imports: {roots}"


# ---------------------------------------------------------------------------
# UPLOAD-NSD-SUBDIR-1 (2026-09-09) -- NSD carrier-relative subdir helper.
# ---------------------------------------------------------------------------
CARRIER = ("VZW", "Verizon")


class TestNsdSubdirPrefixMatchedFolder:
    """Immediate-post-carrier folder that substring-hits a REAL (non-default)
    tag group -- the router routed via that folder, so target_folder already
    represents it. Strip it, keep deeper structure."""

    def test_top_level_file_directly_under_carrier(self) -> None:
        # No sub-folder to preserve OR strip; deeper tail is empty.
        assert nsd_subdir_prefix_from_relative_path(
            "VZW/foo.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["Audio"]],
        ) == ""

    def test_matched_first_folder_is_stripped(self) -> None:
        assert nsd_subdir_prefix_from_relative_path(
            "VZW/Audio(Done)/foo.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["Audio"]],
        ) == ""

    def test_matched_first_folder_deep_structure_preserved(self) -> None:
        assert nsd_subdir_prefix_from_relative_path(
            "VZW/Audio(Done)/sub/nested/foo.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["Audio"]],
        ) == "sub/nested"

    def test_matched_first_folder_with_inner_zip(self) -> None:
        # Zip-container segment is dropped from the deeper tail.
        assert nsd_subdir_prefix_from_relative_path(
            "VZW/Audio(Done)/report.zip/inner/x.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["Audio"]],
        ) == "inner"

    def test_multi_tag_and_group_all_must_match(self) -> None:
        # [["A","B"]] is one AND-group; folder must contain both.
        both = "SDoc Qualification (final)"
        one_only = "SDoc only"
        assert nsd_subdir_prefix_from_relative_path(
            f"VZW/{both}/x.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["SDoc", "Qualification"]],
        ) == ""
        assert nsd_subdir_prefix_from_relative_path(
            f"VZW/{one_only}/x.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["SDoc", "Qualification"]],
        ) == "SDoc only"


class TestNsdSubdirPrefixUnmatchedFolder:
    """Immediate-post-carrier folder that does NOT substring-hit any real tag
    group -- the router fell through to a `["default"]`-tagged catch-all via
    TDN-1. The folder is dynamic content that must survive on the carrier."""

    def test_unmatched_folder_kept_default_only_desc(self) -> None:
        assert nsd_subdir_prefix_from_relative_path(
            "VZW/Skylo NTN (For Solution team)/foo.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["default"]],
        ) == "Skylo NTN (For Solution team)"

    def test_unmatched_folder_deep_structure_preserved(self) -> None:
        assert nsd_subdir_prefix_from_relative_path(
            "VZW/Power Management/rev3/x.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["default"]],
        ) == "Power Management/rev3"

    def test_unmatched_folder_with_real_tags_that_dont_hit(self) -> None:
        # Item has real tags, but this folder just doesn't match them.
        assert nsd_subdir_prefix_from_relative_path(
            "VZW/Test reports/foo.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["Audio"], ["Camera"]],
        ) == "Test reports"

    def test_item_description_none_treats_as_unmatched(self) -> None:
        # A caller that lacks item_description context degrades to "keep".
        assert nsd_subdir_prefix_from_relative_path(
            "VZW/Skylo NTN/foo.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=None,
        ) == "Skylo NTN"


class TestNsdSubdirPrefixCarrierAnchoring:
    def test_nested_carrier_layout(self) -> None:
        # S671U1/some_folder/VZW/Power Management/foo.pdf reaches walk as
        # 'some_folder/VZW/Power Management/foo.pdf' (path is relative to
        # device folder root). The 'some_folder/' prefix is stripped.
        assert nsd_subdir_prefix_from_relative_path(
            "some_folder/VZW/Power Management/foo.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["default"]],
        ) == "Power Management"

    def test_carrier_name_matches_case_insensitively(self) -> None:
        assert nsd_subdir_prefix_from_relative_path(
            "vzw/Skylo/foo.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["default"]],
        ) == "Skylo"
        assert nsd_subdir_prefix_from_relative_path(
            "VERIZON/Skylo/foo.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["default"]],
        ) == "Skylo"

    def test_shallowest_carrier_wins_when_nested(self) -> None:
        # A path with two allowed folder names is anchored at the SHALLOWEST
        # -- the deeper occurrence is content, not a re-anchor.
        assert nsd_subdir_prefix_from_relative_path(
            "VZW/VZW/inner/foo.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["default"]],
        ) == "VZW/inner"

    def test_no_carrier_in_path_returns_empty(self) -> None:
        assert nsd_subdir_prefix_from_relative_path(
            "STG/deeper/foo.pdf",
            allowed_carrier_folders=CARRIER,
            item_description=[["default"]],
        ) == ""

    def test_empty_allowed_carrier_folders_returns_empty(self) -> None:
        assert nsd_subdir_prefix_from_relative_path(
            "VZW/Skylo/foo.pdf",
            allowed_carrier_folders=(),
            item_description=[["default"]],
        ) == ""


class TestCarrierSubdirNsdBranch:
    """`carrier_subdir` dispatches to the NSD helper when
    ingest_source == 'NetworkSharedDrive' AND allowed_carrier_folders is
    non-empty. Every other combination falls back to the legacy branches."""

    def test_dispatches_to_nsd_branch(self) -> None:
        assert carrier_subdir(
            relative_path="VZW/Skylo/foo.pdf",
            is_view=True, from_zip=False,
            ingest_source="NetworkSharedDrive",
            allowed_carrier_folders=CARRIER,
            item_description=[["default"]],
        ) == "Skylo"

    def test_nsd_ingest_without_allowlist_falls_back_to_view_branch(self) -> None:
        # A non-carrier-allowlist NSD customer takes the legacy view branch:
        # from_zip=False -> flat upload, matching pre-Fix-2 behavior.
        assert carrier_subdir(
            relative_path="VZW/Skylo/foo.pdf",
            is_view=True, from_zip=False,
            ingest_source="NetworkSharedDrive",
            allowed_carrier_folders=(),
            item_description=None,
        ) == ""

    def test_non_view_ignores_new_kwargs(self) -> None:
        # Internal-tree PLM path never enters the NSD branch even if
        # allowed_carrier_folders happens to be passed.
        assert carrier_subdir(
            relative_path=INTERNAL + "b.zip/i am c/d.pdf",
            is_view=False, from_zip=True,
            ingest_source="CorporatePLM",
            allowed_carrier_folders=CARRIER,
            item_description=[["Audio"]],
        ) == "i am c"

    def test_legacy_view_from_zip_true_unaffected_for_non_nsd(self) -> None:
        # Email-ingested archive: NSD kwargs default to empty, view path
        # applies with from_zip=True.
        assert carrier_subdir(
            relative_path=VIEW + "b.zip/i am c/d.pdf",
            is_view=True, from_zip=True,
            ingest_source="",
        ) == "i am c"

    def test_defaults_preserve_backwards_compat(self) -> None:
        # Old-style call site (no new kwargs) still returns the same answer.
        assert carrier_subdir(
            relative_path=VIEW + "a.pdf",
            is_view=True, from_zip=False,
            ingest_source="",
        ) == ""
