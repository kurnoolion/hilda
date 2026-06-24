"""Integration tests for sharepoint_integration_cli."""
from __future__ import annotations

import argparse
import io
import json
import uuid
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from core.src.sharepoint_integration.sharepoint_integration_cli import (
    DEFAULT_BASE,
    DEFAULT_CONFIG,
    _diagnostic,
    _dry_run,
    _mock,
    main,
)


def _mk_args(**overrides: object) -> argparse.Namespace:
    base = dict(
        diagnostic=False,
        mock=False,
        dry_run=False,
        serve=False,
        customer=None,
        config=DEFAULT_CONFIG,
        base_path=DEFAULT_BASE,
        host="127.0.0.1",
        port=8765,
        run_id=f"run-{uuid.uuid4().hex[:8]}",
    )
    base.update(overrides)
    return argparse.Namespace(**base)


def _seed_customer(base: Path, customer_id: str = "test_customer") -> None:
    customers = base / "customers"
    customers.mkdir(parents=True, exist_ok=True)
    payload = {
        "customer_id": customer_id,
        "lists": {
            "delivery_items": {
                "name": f"Deliverables_{customer_id}",
                "columns": {"item_name": "Title", "delivery_state": "Status"},
            },
        },
    }
    (customers / f"{customer_id}.yaml").write_text(json.dumps(payload))


class TestCli:
    @pytest.mark.asyncio
    async def test_mock_mode_emits_rpt(self, tmp_path: Path) -> None:
        _seed_customer(tmp_path)
        args = _mk_args(mock=True, base_path=tmp_path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = await _mock(args)
        assert rc == 0
        line = buf.getvalue().strip()
        assert line.startswith("RPT|SHP|")
        assert "mock=true" in line
        assert "customers_configured=1" in line
        assert "lists_reachable=1" in line
        assert "lists_unreachable=0" in line
        assert "auth_type=none" in line

    @pytest.mark.asyncio
    async def test_mock_with_no_customers(self, tmp_path: Path) -> None:
        args = _mk_args(mock=True, base_path=tmp_path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = await _mock(args)
        assert rc == 0
        line = buf.getvalue().strip()
        assert "customers_configured=0" in line

    def test_dry_run_emits_met(self, tmp_path: Path) -> None:
        _seed_customer(tmp_path, customer_id="test_customer")
        args = _mk_args(dry_run=True, customer="test_customer", base_path=tmp_path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = _dry_run(args)
        assert rc == 0
        line = buf.getvalue().strip()
        assert line.startswith("MET|SHP|")
        assert "customer=test_customer" in line
        assert "lists_validated=1" in line
        assert "columns_mapped=2" in line

    def test_dry_run_unknown_customer_returns_1(self, tmp_path: Path) -> None:
        args = _mk_args(dry_run=True, customer="unknown", base_path=tmp_path)
        rc = _dry_run(args)
        assert rc == 1

    def test_dry_run_requires_customer(self, tmp_path: Path) -> None:
        with pytest.raises(SystemExit):
            main(["--dry-run", "--base-path", str(tmp_path)])

    def test_required_mode_arg(self) -> None:
        with pytest.raises(SystemExit):
            main([])

    @pytest.mark.asyncio
    async def test_diagnostic_with_no_customers_emits_zero_rpt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        cfg_file = tmp_path / "missing.json"
        monkeypatch.setenv("HILDA_SP_SITE_URL", "http://nowhere.invalid")
        monkeypatch.setenv("HILDA_SP_AUTH_TYPE", "none")
        args = _mk_args(diagnostic=True, config=cfg_file, base_path=tmp_path)
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = await _diagnostic(args)
        assert rc == 0
        line = buf.getvalue().strip()
        assert line.startswith("RPT|SHP|")
        assert "customers_configured=0" in line
