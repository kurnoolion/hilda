"""test_dashboard_config.py -- UR-4 (Ph-2 2026-08-01).

Focused coverage of the manual-routing exclusion fields on DashboardConfig
+ the shared comma-separated env-var parser they share with
tpm_notification_config._parse_milestone_names.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from core.src.dashboard.config import DashboardConfig


_ENV_KEYS = [
    "HILDA_DASHBOARD_MANUAL_ROUTING_EXCLUDED_ITEM_NAMES",
    "HILDA_DASHBOARD_MANUAL_ROUTING_EXCLUDED_MILESTONE_NAMES",
]


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Guarantee no ambient env leakage into the parse/precedence tests."""
    for k in _ENV_KEYS:
        monkeypatch.delenv(k, raising=False)
    yield


class TestDefaults:
    def test_empty_by_default(self):
        cfg = DashboardConfig()
        assert cfg.manual_routing_excluded_item_names == []
        assert cfg.manual_routing_excluded_milestone_names == []


class TestCommaSeparatedParsing:
    def test_string_splits_on_comma(self):
        cfg = DashboardConfig(
            manual_routing_excluded_item_names="A,B,C",
            manual_routing_excluded_milestone_names="DRR",
        )
        assert cfg.manual_routing_excluded_item_names == ["A", "B", "C"]
        assert cfg.manual_routing_excluded_milestone_names == ["DRR"]

    def test_string_strips_whitespace_and_empties(self):
        cfg = DashboardConfig(
            manual_routing_excluded_item_names=" A ,  , B ,",
        )
        assert cfg.manual_routing_excluded_item_names == ["A", "B"]

    def test_empty_string_yields_empty_list(self):
        cfg = DashboardConfig(
            manual_routing_excluded_item_names="",
            manual_routing_excluded_milestone_names="",
        )
        assert cfg.manual_routing_excluded_item_names == []
        assert cfg.manual_routing_excluded_milestone_names == []

    def test_list_passes_through(self):
        cfg = DashboardConfig(
            manual_routing_excluded_item_names=["X", "Y"],
            manual_routing_excluded_milestone_names=["DRR", "GCF"],
        )
        assert cfg.manual_routing_excluded_item_names == ["X", "Y"]
        assert cfg.manual_routing_excluded_milestone_names == ["DRR", "GCF"]


class TestFromSourcesPrecedence:
    def test_env_var_overrides_default(self, monkeypatch):
        monkeypatch.setenv(
            "HILDA_DASHBOARD_MANUAL_ROUTING_EXCLUDED_ITEM_NAMES",
            "Final DRR status excel deliverable for carrier,Item 85",
        )
        monkeypatch.setenv(
            "HILDA_DASHBOARD_MANUAL_ROUTING_EXCLUDED_MILESTONE_NAMES",
            "DRR",
        )
        cfg = DashboardConfig.from_sources(config_path=Path("/does/not/exist"))
        assert cfg.manual_routing_excluded_item_names == [
            "Final DRR status excel deliverable for carrier",
            "Item 85",
        ]
        assert cfg.manual_routing_excluded_milestone_names == ["DRR"]

    def test_cli_overrides_env(self, monkeypatch):
        monkeypatch.setenv(
            "HILDA_DASHBOARD_MANUAL_ROUTING_EXCLUDED_ITEM_NAMES", "env-item",
        )
        cfg = DashboardConfig.from_sources(
            config_path=Path("/does/not/exist"),
            cli_overrides={"manual_routing_excluded_item_names": ["cli-item"]},
        )
        assert cfg.manual_routing_excluded_item_names == ["cli-item"]

    def test_config_file_seed(self, tmp_path):
        """JSON config-file precedence: below env, above defaults."""
        import json
        cfg_path = tmp_path / "dashboard.json"
        cfg_path.write_text(json.dumps({
            "manual_routing_excluded_item_names": ["from-json"],
            "manual_routing_excluded_milestone_names": ["DRR"],
        }))
        cfg = DashboardConfig.from_sources(config_path=cfg_path)
        assert cfg.manual_routing_excluded_item_names == ["from-json"]
        assert cfg.manual_routing_excluded_milestone_names == ["DRR"]
