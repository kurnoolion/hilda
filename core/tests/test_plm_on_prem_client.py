"""PLM-1 tests -- subprocess wrappers around on-prem PLM scripts.

Strategy: instead of mocking subprocess.run directly (fragile + tightly
couples to library internals), we drop tiny fake scripts into a tmp_path
directory and point HILDA_PLM_SCRIPTS_DIR at it. Each fake script exercises
a specific contract (exit 0 + stdout, exit non-zero, timeout, missing arg,
etc.). This mirrors what real on-prem scripts look like and catches
integration issues subprocess-mocking would miss.
"""
from __future__ import annotations

import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

from core.src.issue_tracker.corp_plm import on_prem_client


# ---------------------------------------------------------------------------
# Fixtures: write fake scripts into tmp_path/scripts/, wire env vars
# ---------------------------------------------------------------------------


def _write_script(scripts_dir: Path, name: str, body: str) -> Path:
    p = scripts_dir / name
    p.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    return p


@pytest.fixture
def scripts_env(tmp_path: Path, monkeypatch):
    """Point HILDA_PLM_SCRIPTS_DIR at tmp_path/scripts and HILDA_PLM_PYTHON
    at the current test interpreter (so fake scripts using stdlib work
    cross-platform without a global python3 install)."""
    scripts_dir = tmp_path / "scripts"
    scripts_dir.mkdir()
    monkeypatch.setenv("HILDA_PLM_SCRIPTS_DIR", str(scripts_dir))
    monkeypatch.setenv("HILDA_PLM_PYTHON", sys.executable)
    return scripts_dir


# ---------------------------------------------------------------------------
# create_plm_ticket
# ---------------------------------------------------------------------------


class TestCreatePlmTicket:

    def test_success_returns_case_id(self, scripts_env):
        # Fake script: reads --device-id + --items-json, prints canned case_id
        _write_script(scripts_env, "create_plm_ticket.py", """
            import argparse, sys, json
            p = argparse.ArgumentParser()
            p.add_argument("--device-id", required=True)
            p.add_argument("--items-json", required=True)
            args = p.parse_args()
            items = json.loads(args.items_json)
            # Sanity: assert we got what we expected
            assert args.device_id == "SM-S671U1"
            assert items == [[10, "HW PL Test A"], [11, "HW PL Test B"]]
            print("P334434-73839")
            sys.exit(0)
        """)
        result = on_prem_client.create_plm_ticket(
            "SM-S671U1", [(10, "HW PL Test A"), (11, "HW PL Test B")],
        )
        assert result == "P334434-73839"

    def test_script_exit_nonzero_returns_empty(self, scripts_env):
        _write_script(scripts_env, "create_plm_ticket.py", """
            import sys
            print("some diagnostic", file=sys.stderr)
            sys.exit(1)
        """)
        result = on_prem_client.create_plm_ticket("SM-S671U1", [(1, "x")])
        assert result == ""

    def test_script_success_but_empty_stdout_returns_empty(self, scripts_env):
        _write_script(scripts_env, "create_plm_ticket.py", """
            import sys
            sys.exit(0)
        """)
        result = on_prem_client.create_plm_ticket("SM-S671U1", [(1, "x")])
        assert result == ""

    def test_script_missing_returns_empty(self, scripts_env):
        # No script written
        result = on_prem_client.create_plm_ticket("SM-S671U1", [(1, "x")])
        assert result == ""

    def test_timeout_returns_empty(self, scripts_env, monkeypatch):
        _write_script(scripts_env, "create_plm_ticket.py", """
            import time, sys
            time.sleep(30)
            print("P123-456")
            sys.exit(0)
        """)
        monkeypatch.setenv("HILDA_PLM_TIMEOUT_CREATE", "1")
        result = on_prem_client.create_plm_ticket("SM-S671U1", [(1, "x")])
        assert result == ""

    def test_items_serialized_as_json_pairs(self, scripts_env):
        """Verify the wrapper serializes items as JSON list-of-2-tuples,
        preserves item_no as int + title as str."""
        _write_script(scripts_env, "create_plm_ticket.py", """
            import argparse, sys, json
            p = argparse.ArgumentParser()
            p.add_argument("--device-id", required=True)
            p.add_argument("--items-json", required=True)
            args = p.parse_args()
            items = json.loads(args.items_json)
            # Echo whether type is preserved
            all_ok = all(isinstance(x[0], int) and isinstance(x[1], str) for x in items)
            print("PASS" if all_ok and items == [[7, "A"], [42, "B"]] else "FAIL")
            sys.exit(0)
        """)
        # Pass strings/ints mixed to confirm coercion at wrapper boundary
        result = on_prem_client.create_plm_ticket("dev", [("7", "A"), (42, "B")])
        assert result == "PASS"


# ---------------------------------------------------------------------------
# list_and_download_all
# ---------------------------------------------------------------------------


class TestListAndDownloadAll:

    def test_success_returns_0(self, scripts_env, tmp_path):
        _write_script(scripts_env, "plm_file_download.py", """
            import argparse, os, sys
            p = argparse.ArgumentParser()
            p.add_argument("--case-id", required=True)
            args = p.parse_args()
            # Emulate what the real script does: create downloads/ in CWD
            os.makedirs("downloads", exist_ok=True)
            with open(os.path.join("downloads", "file1.pdf"), "wb") as f:
                f.write(b"pdf-bytes-A")
            with open(os.path.join("downloads", "file2.pdf"), "wb") as f:
                f.write(b"pdf-bytes-B")
            print("downloaded=2")
            sys.exit(0)
        """)
        work_dir = tmp_path / "work-P123"
        work_dir.mkdir()
        rc = on_prem_client.list_and_download_all("P123-456", work_dir)
        assert rc == 0
        downloaded = list((work_dir / "downloads").iterdir())
        assert len(downloaded) == 2
        assert {p.name for p in downloaded} == {"file1.pdf", "file2.pdf"}

    def test_missing_download_dir_returns_minus_1(self, scripts_env, tmp_path):
        _write_script(scripts_env, "plm_file_download.py", """
            import sys; sys.exit(0)
        """)
        rc = on_prem_client.list_and_download_all(
            "P123", tmp_path / "does_not_exist",
        )
        assert rc == -1

    def test_script_failure_returns_minus_1(self, scripts_env, tmp_path):
        _write_script(scripts_env, "plm_file_download.py", """
            import sys
            print("network unreachable", file=sys.stderr)
            sys.exit(1)
        """)
        work_dir = tmp_path / "w"
        work_dir.mkdir()
        rc = on_prem_client.list_and_download_all("P123", work_dir)
        assert rc == -1


# ---------------------------------------------------------------------------
# close_plm_defect
# ---------------------------------------------------------------------------


class TestClosePlmDefect:

    def test_success_returns_0(self, scripts_env):
        _write_script(scripts_env, "close_plm_defect.py", """
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--case-id", required=True)
            p.parse_args()
            sys.exit(0)
        """)
        assert on_prem_client.close_plm_defect("P123-456") == 0

    def test_failure_returns_minus_1(self, scripts_env):
        _write_script(scripts_env, "close_plm_defect.py", """
            import sys
            print("already closed", file=sys.stderr)
            sys.exit(2)
        """)
        assert on_prem_client.close_plm_defect("P123") == -1

    def test_missing_script_returns_minus_1(self, scripts_env):
        assert on_prem_client.close_plm_defect("P123") == -1


# ---------------------------------------------------------------------------
# get_actual_item_info
# ---------------------------------------------------------------------------


class TestGetActualItemInfo:

    def test_success_returns_url(self, scripts_env):
        _write_script(scripts_env, "get_actual_item_info.py", """
            import argparse, sys
            p = argparse.ArgumentParser()
            p.add_argument("--case-id", required=True)
            args = p.parse_args()
            assert args.case_id == "P260811-04409"
            print("https://plm.corp.example/tickets/GMKJ6NOt")
            sys.exit(0)
        """)
        url = on_prem_client.get_actual_item_info("P260811-04409")
        assert url == "https://plm.corp.example/tickets/GMKJ6NOt"

    def test_failure_returns_empty(self, scripts_env):
        _write_script(scripts_env, "get_actual_item_info.py", """
            import sys; sys.exit(1)
        """)
        assert on_prem_client.get_actual_item_info("P999") == ""

    def test_empty_stdout_returns_empty(self, scripts_env):
        _write_script(scripts_env, "get_actual_item_info.py", """
            import sys; sys.exit(0)
        """)
        assert on_prem_client.get_actual_item_info("P123") == ""


# ---------------------------------------------------------------------------
# Config surface: env vars honored
# ---------------------------------------------------------------------------


class TestConfigResolution:

    def test_scripts_dir_env_var_honored(self, scripts_env, monkeypatch):
        # Move scripts to a different dir; point env var there
        alt = scripts_env.parent / "alt_scripts"
        alt.mkdir()
        _write_script(alt, "close_plm_defect.py", """
            import sys; sys.exit(0)
        """)
        # scripts_env has no close_plm_defect.py — would fail if used
        monkeypatch.setenv("HILDA_PLM_SCRIPTS_DIR", str(alt))
        assert on_prem_client.close_plm_defect("P1") == 0

    def test_scripts_dir_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("HILDA_PLM_SCRIPTS_DIR", raising=False)
        # Path.as_posix() to compare portably across Windows dev + Linux prod.
        assert on_prem_client._scripts_dir().as_posix() == "/opt/plm_scripts"

    def test_python_interpreter_default_when_unset(self, monkeypatch):
        monkeypatch.delenv("HILDA_PLM_PYTHON", raising=False)
        assert on_prem_client._python() == "python3"

    def test_timeout_env_var_parsed_as_int(self, monkeypatch):
        monkeypatch.setenv("HILDA_PLM_TIMEOUT_CREATE", "5")
        assert on_prem_client._timeout("HILDA_PLM_TIMEOUT_CREATE", 60) == 5

    def test_timeout_env_var_invalid_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("HILDA_PLM_TIMEOUT_CREATE", "not-a-number")
        assert on_prem_client._timeout("HILDA_PLM_TIMEOUT_CREATE", 60) == 60

    def test_timeout_env_var_empty_falls_back_to_default(self, monkeypatch):
        monkeypatch.setenv("HILDA_PLM_TIMEOUT_CREATE", "")
        assert on_prem_client._timeout("HILDA_PLM_TIMEOUT_CREATE", 60) == 60
