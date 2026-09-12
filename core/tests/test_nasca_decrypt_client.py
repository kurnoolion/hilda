"""NASCA-PLM-1 (2026-09-11) tests -- pure-python coverage of the PLM
decrypt HTTP client. Mocks urlopen so no network is touched; module-level
constants and time.sleep are monkey-patched so tests run in ms.
"""
from __future__ import annotations

import io
import json
from typing import Any

import pytest

from core.src.issue_tracker.corp_plm import nasca_decrypt_client as ndc


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeResp:
    def __init__(self, body: bytes):
        self._body = body

    def read(self) -> bytes:
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _fake_urlopen_factory(script):
    """script: list of (url_substring, response-dict-or-Exception).
    Each call pops the head; raises AssertionError if list runs out."""

    def _fake(req, timeout=None):
        assert script, "urlopen called more times than scripted"
        substr, payload = script.pop(0)
        assert substr in req.full_url, (
            f"expected url containing {substr!r}, got {req.full_url!r}"
        )
        if isinstance(payload, Exception):
            raise payload
        return _FakeResp(json.dumps(payload).encode("utf-8"))

    return _fake


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr(ndc.time, "sleep", lambda _s: None)


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch):
    # Zero interval so retries don't wall-clock the tests.
    monkeypatch.setenv("HILDA_NASCA_PLM_POLL_SEC", "1")
    monkeypatch.setenv("HILDA_NASCA_PLM_TIMEOUT_SEC", "300")
    monkeypatch.setenv("HILDA_NASCA_PLM_ENABLED", "true")


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------


class TestKickoffFailure:
    def test_kickoff_http_error_returns_false(self, monkeypatch):
        import urllib.error
        script = [("process-plm-documents", urllib.error.HTTPError(
            "u", 500, "boom", {}, io.BytesIO(b"")))]
        monkeypatch.setattr(ndc.urllib.request, "urlopen",
                            _fake_urlopen_factory(script))
        assert ndc.decrypt_plm_id("DEF-1") is False

    def test_kickoff_success_false_returns_false(self, monkeypatch):
        script = [("process-plm-documents",
                   {"success": False, "message": "bad"})]
        monkeypatch.setattr(ndc.urllib.request, "urlopen",
                            _fake_urlopen_factory(script))
        assert ndc.decrypt_plm_id("DEF-2") is False

    def test_empty_plm_id_returns_false(self):
        assert ndc.decrypt_plm_id("") is False
        assert ndc.decrypt_plm_id("   ") is False


class TestPollTerminalStates:
    def test_running_then_finished_returns_true(self, monkeypatch):
        script = [
            ("process-plm-documents",
             {"success": True, "message": "started"}),
            ("/status",
             {"running": True, "message": "in progress"}),
            ("/status",
             {"running": False,
              "result": {"success": True, "message": "done",
                         "renamed_files": ["a_decrypt.zip", "b_decrypt.zip"]}}),
        ]
        monkeypatch.setattr(ndc.urllib.request, "urlopen",
                            _fake_urlopen_factory(script))
        assert ndc.decrypt_plm_id("DEF-3") is True

    def test_finished_immediately_returns_true(self, monkeypatch):
        script = [
            ("process-plm-documents",
             {"success": True, "message": "kicked"}),
            ("/status",
             {"running": False,
              "result": {"success": True, "message": "done"}}),
        ]
        monkeypatch.setattr(ndc.urllib.request, "urlopen",
                            _fake_urlopen_factory(script))
        assert ndc.decrypt_plm_id("DEF-4") is True

    def test_failed_returns_false(self, monkeypatch):
        script = [
            ("process-plm-documents",
             {"success": True}),
            ("/status",
             {"running": False,
              "result": {"success": False,
                         "message": "Exception during PLM"}}),
        ]
        monkeypatch.setattr(ndc.urllib.request, "urlopen",
                            _fake_urlopen_factory(script))
        assert ndc.decrypt_plm_id("DEF-5") is False


class TestPollTimeout:
    def test_still_running_at_deadline_returns_false(self, monkeypatch):
        # Pin monotonic so the 2nd call (post-status) exceeds the 300s
        # deadline computed from the 1st call.
        clock = iter([0.0, 500.0])

        def _mono():
            return next(clock)
        monkeypatch.setattr(ndc.time, "monotonic", _mono)

        script = [
            ("process-plm-documents",
             {"success": True}),
            ("/status", {"running": True}),
        ]
        monkeypatch.setattr(ndc.urllib.request, "urlopen",
                            _fake_urlopen_factory(script))
        assert ndc.decrypt_plm_id("DEF-6") is False

    def test_never_run_at_deadline_returns_false(self, monkeypatch):
        clock = iter([0.0, 500.0])

        def _mono():
            return next(clock)
        monkeypatch.setattr(ndc.time, "monotonic", _mono)

        script = [
            ("process-plm-documents",
             {"success": True}),
            ("/status",
             {"running": False, "result": None,
              "message": "No PLM job has been run yet."}),
        ]
        monkeypatch.setattr(ndc.urllib.request, "urlopen",
                            _fake_urlopen_factory(script))
        assert ndc.decrypt_plm_id("DEF-7") is False


class TestDisabledFlag:
    def test_disabled_returns_true_without_http(self, monkeypatch):
        monkeypatch.setenv("HILDA_NASCA_PLM_ENABLED", "false")

        def _boom(*_a, **_kw):
            raise AssertionError("urlopen should not be called when disabled")
        monkeypatch.setattr(ndc.urllib.request, "urlopen", _boom)
        assert ndc.decrypt_plm_id("DEF-8") is True


class TestTransportErrors:
    def test_status_urlerror_retries_until_deadline(self, monkeypatch):
        import urllib.error
        clock = iter([0.0, 500.0])

        def _mono():
            return next(clock)
        monkeypatch.setattr(ndc.time, "monotonic", _mono)

        script = [
            ("process-plm-documents",
             {"success": True}),
            ("/status",
             urllib.error.URLError("connection reset")),
        ]
        monkeypatch.setattr(ndc.urllib.request, "urlopen",
                            _fake_urlopen_factory(script))
        assert ndc.decrypt_plm_id("DEF-9") is False
