"""DRM-1 (2026-08-28) -- drm_client subprocess wrapper tests.

Verifies:
* HILDA_DRM_ENABLED=false short-circuits to True (dev / non-corp envs).
* Empty folder_path returns False.
* Missing script file returns False.
* Non-zero subprocess return -> False.
* Timeout -> False.
* Successful return -> True.

Uses monkeypatch on the module-level SCRIPT_DECRYPT + subprocess.run to
avoid actually shelling out to /opt/drm_decrypt_scripts (which won't
exist in the test env).
"""
import subprocess
from pathlib import Path

import pytest

from core.src.storage import drm_client


class TestDecryptFolder:

    def test_empty_folder_returns_false(self, monkeypatch):
        monkeypatch.delenv("HILDA_DRM_ENABLED", raising=False)
        assert drm_client.decrypt_folder("") is False

    def test_disabled_env_short_circuits_true(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HILDA_DRM_ENABLED", "false")
        # Even with missing script, disabled -> True (dev/CI escape hatch).
        monkeypatch.setenv("HILDA_DRM_SCRIPTS_DIR", str(tmp_path / "nowhere"))
        assert drm_client.decrypt_folder("/some/folder") is True

    def test_disabled_variants(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HILDA_DRM_SCRIPTS_DIR", str(tmp_path / "nowhere"))
        for val in ("false", "0", "no", "off", "FALSE", "No"):
            monkeypatch.setenv("HILDA_DRM_ENABLED", val)
            assert drm_client.decrypt_folder("/x") is True, f"failed for {val!r}"

    def test_missing_script_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.delenv("HILDA_DRM_ENABLED", raising=False)
        monkeypatch.setenv("HILDA_DRM_SCRIPTS_DIR", str(tmp_path))  # empty dir
        assert drm_client.decrypt_folder("/some/folder") is False

    def test_script_success_returns_true(self, monkeypatch, tmp_path):
        monkeypatch.delenv("HILDA_DRM_ENABLED", raising=False)
        # Materialize a dummy script file so the exists() check passes.
        scripts_dir = tmp_path
        (scripts_dir / drm_client.SCRIPT_DECRYPT).write_text("#!fake\n")
        monkeypatch.setenv("HILDA_DRM_SCRIPTS_DIR", str(scripts_dir))

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0], returncode=0,
                stdout='{"success": true, "message": "ok"}', stderr="",
            )
        monkeypatch.setattr(subprocess, "run", _fake_run)
        assert drm_client.decrypt_folder("/nsd/foo") is True

    def test_script_nonzero_return_false(self, monkeypatch, tmp_path):
        monkeypatch.delenv("HILDA_DRM_ENABLED", raising=False)
        (tmp_path / drm_client.SCRIPT_DECRYPT).write_text("#!fake\n")
        monkeypatch.setenv("HILDA_DRM_SCRIPTS_DIR", str(tmp_path))

        def _fake_run(*args, **kwargs):
            return subprocess.CompletedProcess(
                args=args[0], returncode=3,
                stdout="", stderr="transport error",
            )
        monkeypatch.setattr(subprocess, "run", _fake_run)
        assert drm_client.decrypt_folder("/nsd/foo") is False

    def test_script_timeout_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.delenv("HILDA_DRM_ENABLED", raising=False)
        (tmp_path / drm_client.SCRIPT_DECRYPT).write_text("#!fake\n")
        monkeypatch.setenv("HILDA_DRM_SCRIPTS_DIR", str(tmp_path))

        def _fake_run(*args, **kwargs):
            raise subprocess.TimeoutExpired(cmd=args[0], timeout=1)
        monkeypatch.setattr(subprocess, "run", _fake_run)
        assert drm_client.decrypt_folder("/nsd/foo") is False

    def test_script_spawn_oserror_returns_false(self, monkeypatch, tmp_path):
        monkeypatch.delenv("HILDA_DRM_ENABLED", raising=False)
        (tmp_path / drm_client.SCRIPT_DECRYPT).write_text("#!fake\n")
        monkeypatch.setenv("HILDA_DRM_SCRIPTS_DIR", str(tmp_path))

        def _fake_run(*args, **kwargs):
            raise OSError(2, "no such file")
        monkeypatch.setattr(subprocess, "run", _fake_run)
        assert drm_client.decrypt_folder("/nsd/foo") is False

    def test_folder_only_no_file_name_arg(self, monkeypatch, tmp_path):
        """Regression: batch mode -- caller must NOT pass --file-name."""
        monkeypatch.delenv("HILDA_DRM_ENABLED", raising=False)
        (tmp_path / drm_client.SCRIPT_DECRYPT).write_text("#!fake\n")
        monkeypatch.setenv("HILDA_DRM_SCRIPTS_DIR", str(tmp_path))

        captured_cmd: list = []

        def _fake_run(cmd, *args, **kwargs):
            captured_cmd.extend(cmd)
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout='{"success": true}', stderr="",
            )
        monkeypatch.setattr(subprocess, "run", _fake_run)
        drm_client.decrypt_folder("/nsd/foo")

        assert "--folder-path" in captured_cmd
        assert "/nsd/foo" in captured_cmd
        assert "--file-name" not in captured_cmd

    def test_bad_timeout_env_falls_back(self, monkeypatch):
        monkeypatch.setenv("HILDA_DRM_TIMEOUT", "not-a-number")
        assert drm_client._timeout() == drm_client._DEFAULT_TIMEOUT_SEC

    def test_custom_timeout_env(self, monkeypatch):
        monkeypatch.setenv("HILDA_DRM_TIMEOUT", "42")
        assert drm_client._timeout() == 42
