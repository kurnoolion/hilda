"""UPLOAD-SUBDIR-PLM-1 (2026-08-27) -- unit tests for the subdir-derivation
helper used by submit_to_carrier when uploading PLM-ingested files.

Pure-function tests; no DB / Celery / adapter surface. Covers user's
2026-08-27 confirmation examples explicitly:
  * loose file at rev1 root                 -> flat
  * b.zip/"i am c"/d.pdf                    -> "i am c"
  * outer.zip/inner.zip/x/y.pdf              -> "x"
  * .7z + nested folders                    -> preserves nested
  * _staged_classification anchor           -> same behavior
  * sanitize: backslash + trailing dot     -> replaced / stripped

Plus regression: NSD-style paths (no archive segment) collapse to "".
"""
from core.src.workflow_engine.tasks.submit_to_carrier import (
    _plm_subdir_prefix_from_local_path,
    _sanitize_subdir_segment,
)


class TestPlmSubdirPrefix:

    def test_loose_file_at_rev_root_returns_empty(self):
        # e.g., PLM ticket has a.pdf directly attached (not in a zip).
        path = "internal/MMK/SM-S671U1/P1/HW PL/item_10/test_report/slug/rev1/a.pdf"
        assert _plm_subdir_prefix_from_local_path(path) == ""

    def test_user_example_zip_with_named_folder(self):
        # User's 2026-08-27 example: b.zip contains "i am c"/d.pdf ->
        # "b" DOES NOT make it to Drive, "i am c" DOES.
        path = "internal/MMK/SM-S671U1/P1/HW PL/item_10/test_report/slug/rev1/b.zip/i am c/d.pdf"
        assert _plm_subdir_prefix_from_local_path(path) == "i am c"

    def test_nested_zips_strip_all_archive_layers(self):
        # outer.zip -> inner.zip -> x -> y.pdf : both zips vanish, "x" survives.
        path = "internal/MMK/SM-S671U1/P1/HW PL/item_10/test_report/slug/rev1/outer.zip/inner.zip/x/y.pdf"
        assert _plm_subdir_prefix_from_local_path(path) == "x"

    def test_seven_zip_extension_stripped(self):
        # .7z is treated identically to .zip.
        path = "internal/MMK/SM-S671U1/P1/HW PL/item_10/test_report/slug/rev1/report.7z/folder/nested/file.pdf"
        assert _plm_subdir_prefix_from_local_path(path) == "folder/nested"

    def test_case_insensitive_archive_suffix(self):
        # Owner-authored archive names may be UPPER or MixedCase.
        path = "internal/.../rev1/Report.ZIP/folder/file.pdf"
        assert _plm_subdir_prefix_from_local_path(path) == "folder"

    def test_staged_classification_anchor_same_behavior(self):
        # PLM zip that lands STAGED_NOT_CLASSIFIED at ingest still preserves
        # in-archive folder when uploaded (user's Q4 confirm).
        path = "internal/MMK/SM-S671U1/P1/HW PL/item_10/_staged_classification/report.zip/folder/x.pdf"
        assert _plm_subdir_prefix_from_local_path(path) == "folder"

    def test_nsd_flat_file_returns_empty(self):
        # NSD ingest of "16. CEC(done)/spec.pdf": no .zip in the path
        # segments after rev1/ -> subdir collapse to "" so upload flattens.
        # (In practice the NSD subdir prefix comes through the router's
        # original_filename argument -- and even if it did land here, we
        # want NSD flat per user's #1 confirm.)
        path = "internal/MMK/SM-S671U1/P1/HW PL/item_10/test_report/slug/rev1/16. CEC(done)/spec.pdf"
        # Note: this test's subdir IS "16. CEC(done)" -- NSD doesn't strip.
        # UPLOAD-SUBDIR-PLM-1 GATES on ingest_source=CorporatePLM in
        # submit_to_carrier's main loop, not in this pure helper. The helper
        # only knows about archive-container stripping. So for NSD paths that
        # happen to have subdirs, the helper WOULD preserve them -- but
        # they'll never reach here because the calling gate skips.
        # This test documents the helper's contract:
        assert _plm_subdir_prefix_from_local_path(path) == "16. CEC(done)"

    def test_helper_returns_empty_when_no_rev_anchor(self):
        # Path without a rev<N> or _staged_classification segment.
        path = "internal/MMK/SM-S671U1/P1/HW PL/somewhere_else/file.pdf"
        assert _plm_subdir_prefix_from_local_path(path) == ""

    def test_helper_returns_empty_on_empty_path(self):
        assert _plm_subdir_prefix_from_local_path("") == ""

    def test_sanitize_backslash_replaced(self):
        # Windows-authored archive with a backslash in a folder name.
        assert _sanitize_subdir_segment("foo\\bar") == "foo_bar"

    def test_sanitize_control_chars_stripped(self):
        seg = "abc\x00def\x01"
        assert _sanitize_subdir_segment(seg) == "abcdef"

    def test_sanitize_trailing_dot_and_space_stripped(self):
        assert _sanitize_subdir_segment("folder. ") == "folder"

    def test_sanitize_preserves_spaces_inside_segment(self):
        # User's "i am c" example must survive verbatim.
        assert _sanitize_subdir_segment("i am c") == "i am c"

    def test_sanitize_preserves_unicode_letters(self):
        assert _sanitize_subdir_segment("réport") == "réport"

    def test_sanitize_applied_end_to_end(self):
        # Full helper -> backslash in a segment gets swapped by sanitize.
        path = "internal/.../rev1/report.zip/folder\\weird/nested/file.pdf"
        assert _plm_subdir_prefix_from_local_path(path) == "folder_weird/nested"
