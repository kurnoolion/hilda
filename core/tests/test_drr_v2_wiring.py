"""DRR-V2-6 (2026-08-05) — wiring of DRR-V2 helpers into
tpm_notification.

Covers:
  * template_lookup.get_drr_logo_filename — reads `drr_branding_logo`
    from template.yaml root (per-customer brand override).
  * tpm_notification._resolve_logo_path — resolves that filename
    against _BRANDING_DIRS probe paths.
  * tpm_notification._build_drr_v2_context — composes every kwarg
    build_drr_report_excel() needs in V2 mode (drr_version +
    section_grouping + milestone_headers + project_headers + logo_path).
"""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.src.template_schema import template_lookup
from core.src.workflow_engine.tasks import tpm_notification


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_template_cache():
    template_lookup.clear_cache()
    yield
    template_lookup.clear_cache()


def _seed_template(customer_id: str, template: dict) -> None:
    template_lookup._CACHE[customer_id] = template  # noqa: SLF001


def _deps_with_sp(milestone_rows: list[dict] | None = None,
                   project_rows: list[dict] | None = None) -> SimpleNamespace:
    sp_writer = MagicMock()
    def _get_items(*, entity, scope=None, **kwargs):
        if entity == "milestones":
            return list(milestone_rows or [])
        if entity == "projects":
            return list(project_rows or [])
        return []
    sp_writer.get_items.side_effect = _get_items
    return SimpleNamespace(sp_writer=sp_writer)


# ---------------------------------------------------------------------------
# get_drr_logo_filename
# ---------------------------------------------------------------------------


class TestGetDrrLogoFilename:
    def test_returns_declared_filename(self):
        _seed_template("MMK", {"drr_branding_logo": "verizon.png"})
        assert template_lookup.get_drr_logo_filename("MMK") == "verizon.png"

    def test_missing_key_returns_none(self):
        _seed_template("MMK", {"MMK_template_version": "5.7"})
        assert template_lookup.get_drr_logo_filename("MMK") is None

    def test_empty_value_returns_none(self):
        _seed_template("MMK", {"drr_branding_logo": "   "})
        assert template_lookup.get_drr_logo_filename("MMK") is None

    def test_uncached_customer_returns_none(self):
        assert template_lookup.get_drr_logo_filename("UNKNOWN") is None

    def test_strips_whitespace(self):
        _seed_template("MMK", {"drr_branding_logo": "  verizon.png  "})
        assert template_lookup.get_drr_logo_filename("MMK") == "verizon.png"


# ---------------------------------------------------------------------------
# _resolve_logo_path
# ---------------------------------------------------------------------------


class TestResolveLogoPath:
    def test_returns_none_when_template_has_no_logo(self):
        _seed_template("MMK", {})
        assert tpm_notification._resolve_logo_path("MMK") is None

    def test_returns_none_when_file_not_on_disk(self, caplog):
        _seed_template("MMK", {"drr_branding_logo": "verizon.png"})
        with patch.object(
            tpm_notification, "_BRANDING_DIRS",
            (Path("/no/such/dir/a"), Path("/no/such/dir/b")),
        ):
            with caplog.at_level(
                logging.WARNING,
                logger="core.src.workflow_engine.tasks.tpm_notification",
            ):
                got = tpm_notification._resolve_logo_path("MMK")
        assert got is None
        assert any(
            "declared in template but not found" in r.getMessage()
            for r in caplog.records
        )

    def test_returns_first_matching_path(self, tmp_path):
        # Create the logo in the second probe dir
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_b / "verizon.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        _seed_template("MMK", {"drr_branding_logo": "verizon.png"})
        with patch.object(
            tpm_notification, "_BRANDING_DIRS", (dir_a, dir_b),
        ):
            got = tpm_notification._resolve_logo_path("MMK")
        assert got == dir_b / "verizon.png"

    def test_prefers_first_probe_dir_when_both_have_file(self, tmp_path):
        dir_a = tmp_path / "a"
        dir_b = tmp_path / "b"
        dir_a.mkdir()
        dir_b.mkdir()
        (dir_a / "verizon.png").write_bytes(b"a")
        (dir_b / "verizon.png").write_bytes(b"b")

        _seed_template("MMK", {"drr_branding_logo": "verizon.png"})
        with patch.object(
            tpm_notification, "_BRANDING_DIRS", (dir_a, dir_b),
        ):
            got = tpm_notification._resolve_logo_path("MMK")
        assert got == dir_a / "verizon.png"


# ---------------------------------------------------------------------------
# _build_drr_v2_context
# ---------------------------------------------------------------------------


class TestBuildDrrV2Context:
    def test_composes_full_dict_with_all_pieces_resolved(self, tmp_path):
        _seed_template("MMK", {
            "MMK_template_version": "5.7",
            "drr_branding_logo": "verizon.png",
            "milestones": {
                "DRR": {
                    "work_items": [
                        {"item_no": 1, "item_name": "X",
                         "parent": "Product Documentation Review"},
                    ],
                },
            },
        })
        # Real logo file on disk
        (tmp_path / "verizon.png").write_bytes(b"\x89PNG\r\n\x1a\n")

        deps = _deps_with_sp(
            milestone_rows=[{
                "carrier": "MMK",
                "project_model": "SM-S671U1",
                "milestone_id": "DRR",
                "fld_lockdown_date": date(2026, 3, 18),
                "req_version": "Oct 25",
                "target_date": date(2026, 4, 29),
            }],
            project_rows=[{
                "project_model": "SM-S671U1",
                "LE": date(2026, 6, 11),
                "FFW": date(2026, 5, 13),
            }],
        )

        with patch.object(tpm_notification, "_BRANDING_DIRS", (tmp_path,)):
            ctx = tpm_notification._build_drr_v2_context(
                deps, "MMK", "SM-S671U1", "DRR",
            )

        assert ctx["customer_id"] == "MMK"
        assert ctx["device_id"] == "SM-S671U1"
        assert ctx["milestone_id"] == "DRR"
        assert ctx["drr_version"] == "5.7"
        assert ctx["section_grouping"] is not None
        assert len(ctx["section_grouping"]) == 1
        assert ctx["milestone_headers"]["req_version"] == "Oct 25"
        assert ctx["project_headers"]["LE"] == date(2026, 6, 11)
        assert ctx["logo_path"] == str(tmp_path / "verizon.png")

    def test_missing_template_yields_none_section_grouping(self):
        """Customer whose template isn't cached at all -> V2 mode
        gracefully degrades to legacy 4-column via section_grouping=None."""
        deps = _deps_with_sp()
        ctx = tpm_notification._build_drr_v2_context(
            deps, "UNMIGRATED", "DEV-1", "M1",
        )
        assert ctx["section_grouping"] is None
        assert ctx["drr_version"] is None
        assert ctx["logo_path"] is None
        # Header dicts still returned (all Nones); builder tolerates
        assert set(ctx["milestone_headers"].keys()) == {
            "fld_lockdown_date", "req_version", "target_date",
        }
        assert set(ctx["project_headers"].keys()) == {"LE", "FFW"}

    def test_result_dict_keys_match_builder_signature(self):
        """The returned dict is meant to be spread as **kwargs into
        build_drr_report_excel; every key must be a valid kwarg on the
        builder. Guards against silent drift if the builder signature
        changes."""
        import inspect
        from core.src.email_service.outbound import drr_report_excel

        deps = _deps_with_sp()
        ctx = tpm_notification._build_drr_v2_context(
            deps, "X", "Y", "Z",
        )
        sig = inspect.signature(drr_report_excel.build_drr_report_excel)
        valid_kwargs = set(sig.parameters.keys())
        # Every key in ctx must be a valid kwarg
        for key in ctx:
            assert key in valid_kwargs, (
                f"_build_drr_v2_context returned key {key!r} that is not "
                f"a valid kwarg on build_drr_report_excel"
            )
