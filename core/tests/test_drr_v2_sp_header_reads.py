"""DRR-V2-4 (2026-08-04) — SP read helpers for the Verizon-template DRR
Excel header block.

Two helpers under test:

  * _read_milestone_headers(deps, customer_id, device_id, milestone_id)
      -> {fld_lockdown_date, req_version, target_date}
  * _read_project_headers(deps, customer_id, device_id)
      -> {LE, FFW}

Both are best-effort: SP transport failure / row miss / blank field
returns None + WARN log (never raises). DRR-V2-5 excel builder decides
whether to render "N/A" or leave the cell blank.
"""
from __future__ import annotations

import logging
from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

from core.src.workflow_engine.tasks.tpm_notification import (
    _MILESTONE_HEADER_FIELDS,
    _PROJECT_HEADER_FIELDS,
    _read_milestone_headers,
    _read_project_headers,
)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


def _mk_deps_with_milestones(rows: list[dict]) -> SimpleNamespace:
    """Wire a mock sp_writer that returns `rows` on get_items(entity='milestones', ...)."""
    sp_writer = MagicMock()
    def _get_items(*, entity, scope=None, **kwargs):
        if entity == "milestones":
            return list(rows)
        return []
    sp_writer.get_items.side_effect = _get_items
    return SimpleNamespace(sp_writer=sp_writer)


def _mk_deps_with_projects(rows: list[dict]) -> SimpleNamespace:
    sp_writer = MagicMock()
    def _get_items(*, entity, scope=None, canonical_filters=None, **kwargs):
        if entity == "projects":
            return list(rows)
        return []
    sp_writer.get_items.side_effect = _get_items
    return SimpleNamespace(sp_writer=sp_writer)


# ---------------------------------------------------------------------------
# _read_milestone_headers
# ---------------------------------------------------------------------------


class TestReadMilestoneHeaders:

    def test_returns_all_three_fields_when_row_present(self):
        deps = _mk_deps_with_milestones([
            {
                "carrier": "MMK",
                "project_model": "SM-S671U1",
                "milestone_id": "DRR",
                "fld_lockdown_date": date(2026, 8, 1),
                "req_version": "5.7",
                "target_date": date(2026, 8, 15),
            },
        ])
        got = _read_milestone_headers(deps, "MMK", "SM-S671U1", "DRR")
        assert set(got.keys()) == set(_MILESTONE_HEADER_FIELDS)
        assert got["fld_lockdown_date"] == date(2026, 8, 1)
        assert got["req_version"] == "5.7"
        assert got["target_date"] == date(2026, 8, 15)

    def test_none_row_returns_all_none_and_warns(self, caplog):
        deps = _mk_deps_with_milestones([
            {
                "carrier": "MMK", "project_model": "SM-OTHER",
                "milestone_id": "DRR",
                "fld_lockdown_date": date(2026, 8, 1),
                "req_version": "5.7",
                "target_date": date(2026, 8, 15),
            },
        ])
        with caplog.at_level(
            logging.WARNING,
            logger="core.src.workflow_engine.tasks.tpm_notification",
        ):
            got = _read_milestone_headers(deps, "MMK", "SM-S671U1", "DRR")
        assert got == {k: None for k in _MILESTONE_HEADER_FIELDS}
        assert any("no milestone row matched" in r.getMessage() for r in caplog.records)

    def test_matches_via_title_when_milestone_id_absent(self):
        """SP rows commonly key milestone as 'Title' rather than 'milestone_id';
        the matcher must accept either canonical name."""
        deps = _mk_deps_with_milestones([
            {
                "carrier": "MMK",
                "project_model": "SM-S671U1",
                "Title": "DRR",
                "fld_lockdown_date": date(2026, 8, 1),
                "req_version": "5.7",
                "target_date": date(2026, 8, 15),
            },
        ])
        got = _read_milestone_headers(deps, "MMK", "SM-S671U1", "DRR")
        assert got["req_version"] == "5.7"

    def test_matches_via_device_id_when_project_model_absent(self):
        deps = _mk_deps_with_milestones([
            {
                "carrier": "MMK",
                "device_id": "SM-S671U1",
                "milestone_id": "DRR",
                "fld_lockdown_date": "2026-08-01",
                "req_version": "5.7",
                "target_date": "2026-08-15",
            },
        ])
        got = _read_milestone_headers(deps, "MMK", "SM-S671U1", "DRR")
        assert got["fld_lockdown_date"] == "2026-08-01"

    def test_blank_field_yields_none_and_warns(self, caplog):
        deps = _mk_deps_with_milestones([
            {
                "carrier": "MMK",
                "project_model": "SM-S671U1",
                "milestone_id": "DRR",
                "fld_lockdown_date": None,
                "req_version": "   ",
                "target_date": date(2026, 8, 15),
            },
        ])
        with caplog.at_level(
            logging.WARNING,
            logger="core.src.workflow_engine.tasks.tpm_notification",
        ):
            got = _read_milestone_headers(deps, "MMK", "SM-S671U1", "DRR")
        assert got["fld_lockdown_date"] is None
        assert got["req_version"] is None
        assert got["target_date"] == date(2026, 8, 15)
        # Both blank fields should have logged.
        blank_warnings = [
            r for r in caplog.records if "blank on milestone" in r.getMessage()
        ]
        assert len(blank_warnings) == 2

    def test_sp_transport_failure_returns_all_none_never_raises(self, caplog):
        deps = SimpleNamespace(sp_writer=MagicMock())
        deps.sp_writer.get_items.side_effect = RuntimeError("SP transport boom")
        with caplog.at_level(
            logging.WARNING,
            logger="core.src.workflow_engine.tasks.tpm_notification",
        ):
            got = _read_milestone_headers(deps, "MMK", "SM-S671U1", "DRR")
        assert got == {k: None for k in _MILESTONE_HEADER_FIELDS}
        assert any("milestones read failed" in r.getMessage() for r in caplog.records)

    def test_row_matching_tolerates_whitespace(self):
        deps = _mk_deps_with_milestones([
            {
                "carrier": "MMK",
                "project_model": "  SM-S671U1  ",
                "milestone_id": "DRR ",
                "fld_lockdown_date": date(2026, 8, 1),
                "req_version": "5.7",
                "target_date": date(2026, 8, 15),
            },
        ])
        got = _read_milestone_headers(deps, "MMK", " SM-S671U1", "DRR  ")
        assert got["req_version"] == "5.7"


# ---------------------------------------------------------------------------
# _read_project_headers
# ---------------------------------------------------------------------------


class TestReadProjectHeaders:

    def test_returns_le_and_ffw_when_row_present(self):
        deps = _mk_deps_with_projects([
            {
                "project_model": "SM-S671U1",
                "LE": "2026-09-01",
                "FFW": "2026-08-20",
            },
        ])
        got = _read_project_headers(deps, "MMK", "SM-S671U1")
        assert set(got.keys()) == set(_PROJECT_HEADER_FIELDS)
        assert got["LE"] == "2026-09-01"
        assert got["FFW"] == "2026-08-20"

    def test_empty_projects_yields_all_none_and_warns(self, caplog):
        deps = _mk_deps_with_projects([])
        with caplog.at_level(
            logging.WARNING,
            logger="core.src.workflow_engine.tasks.tpm_notification",
        ):
            got = _read_project_headers(deps, "MMK", "SM-S671U1")
        assert got == {k: None for k in _PROJECT_HEADER_FIELDS}
        assert any("no Projects row matched" in r.getMessage() for r in caplog.records)

    def test_blank_field_yields_none_and_warns(self, caplog):
        deps = _mk_deps_with_projects([
            {
                "project_model": "SM-S671U1",
                "LE": "",
                "FFW": "2026-08-20",
            },
        ])
        with caplog.at_level(
            logging.WARNING,
            logger="core.src.workflow_engine.tasks.tpm_notification",
        ):
            got = _read_project_headers(deps, "MMK", "SM-S671U1")
        assert got["LE"] is None
        assert got["FFW"] == "2026-08-20"
        blank_warnings = [
            r for r in caplog.records if "blank on project" in r.getMessage()
        ]
        assert len(blank_warnings) == 1

    def test_sp_transport_failure_returns_all_none_never_raises(self, caplog):
        deps = SimpleNamespace(sp_writer=MagicMock())
        deps.sp_writer.get_items.side_effect = RuntimeError("SP transport boom")
        with caplog.at_level(
            logging.WARNING,
            logger="core.src.workflow_engine.tasks.tpm_notification",
        ):
            got = _read_project_headers(deps, "MMK", "SM-S671U1")
        assert got == {k: None for k in _PROJECT_HEADER_FIELDS}
        assert any("projects read failed" in r.getMessage() for r in caplog.records)

    def test_takes_first_row_when_multiple_match(self):
        """canonical_filters={'project_model': device_id} narrows on the SP
        side, but if multiple rows come back the helper picks the first."""
        deps = _mk_deps_with_projects([
            {"project_model": "SM-S671U1", "LE": "first-LE", "FFW": "first-FFW"},
            {"project_model": "SM-S671U1", "LE": "second-LE", "FFW": "second-FFW"},
        ])
        got = _read_project_headers(deps, "MMK", "SM-S671U1")
        assert got["LE"] == "first-LE"
        assert got["FFW"] == "first-FFW"
