"""dashboard test suite -- 5 routes + auth + Confirmation skip + token expiry +
admin overrides Ph-1 empty + content negotiation + error codes.

Uses httpx.TestClient against build_app with mocked storage helpers.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from core.src.dashboard import DashboardConfig, MilestoneRefreshState, build_app
from core.src.dashboard.auth import PROXY_USER_HEADER
from core.src.template_schema import DocType, ItemType


# ---------------------------------------------------------------------------
# Storage helper mocks (monkeypatch on the storage module functions used by app)
# ---------------------------------------------------------------------------


class FakeDoc:
    """Stand-in for storage.DocumentIndexRow used by tests."""

    def __init__(self, file_hash, doc_type, doc_id_slug, rev_number,
                 original_filename, parser_result=None, llm_review_findings=None):
        self.file_hash = file_hash
        self.doc_type = doc_type
        self.doc_id_slug = doc_id_slug
        self.rev_number = rev_number
        self.original_filename = original_filename
        self.parser_result = parser_result
        self.llm_review_findings = llm_review_findings


class FakeAssoc:
    def __init__(self, file_hash, nsd_path_type="classified", inferred_tg_name=None):
        self.file_hash = file_hash
        self.nsd_path_type = SimpleNamespace(value=nsd_path_type)
        self.inferred_tg_name = inferred_tg_name


@pytest.fixture
def cfg_mock():
    """Config with mock_auth enabled so tests skip header validation."""
    return DashboardConfig(mock_auth=True)


@pytest.fixture
def cfg_prod():
    """Config with production auth (mock_auth=False)."""
    return DashboardConfig(mock_auth=False)


@pytest.fixture
def patched_storage(monkeypatch):
    """Patch storage helpers used by dashboard.app to return controlled fixtures."""
    state = {
        "docs":       [],
        "assocs":     [],
        "tokens":     {},  # token -> (file_hash, item_id, NSDPath)
        "overrides":  [],
        "files":      {},  # NSDPath -> bytes
        "token_counter": 0,
    }

    async def fake_get_documents_for_item(item_id):
        return state["docs"]

    async def fake_list_associations_for_item(item_id):
        return state["assocs"]

    async def fake_make_download_token(file_hash, delivery_item_id, ttl_seconds=300):
        state["token_counter"] += 1
        token = f"tok-{state['token_counter']}"
        # Find the doc for filename look-up via resolve_download_token
        state["tokens"][token] = (file_hash, delivery_item_id, SimpleNamespace())
        return token

    async def fake_resolve_download_token(token):
        if token not in state["tokens"]:
            raise Exception("STR-E007: token invalid or expired")
        return state["tokens"][token]

    async def fake_list_active_overrides(*, scope=None, scope_id=None):
        return state["overrides"]

    def fake_read_file(nsd_path):
        async def _gen():
            yield b"mock file content"
        return _gen()

    monkeypatch.setattr("core.src.dashboard.app.get_documents_for_item",
                        fake_get_documents_for_item)
    monkeypatch.setattr("core.src.dashboard.app.list_associations_for_item",
                        fake_list_associations_for_item)
    monkeypatch.setattr("core.src.dashboard.app.make_download_token",
                        fake_make_download_token)
    monkeypatch.setattr("core.src.dashboard.app.resolve_download_token",
                        fake_resolve_download_token)
    monkeypatch.setattr("core.src.dashboard.app.list_active_overrides",
                        fake_list_active_overrides)
    monkeypatch.setattr("core.src.dashboard.app.read_file", fake_read_file)
    return state


# ---------------------------------------------------------------------------
# /docs/{delivery_item_id} tests
# ---------------------------------------------------------------------------


class TestGetDocumentSection:
    def test_html_response_default(self, cfg_mock, patched_storage):
        patched_storage["docs"] = [
            FakeDoc("h1", DocType.TEST_REPORT, "power_report", 1, "report.pdf"),
        ]
        patched_storage["assocs"] = [FakeAssoc("h1", "classified")]
        client = TestClient(build_app(cfg_mock))
        r = client.get("/docs/I-1234")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "power_report" in r.text
        assert "report.pdf" in r.text

    def test_json_response_on_accept_header(self, cfg_mock, patched_storage):
        patched_storage["docs"] = [
            FakeDoc("h1", DocType.TEST_REPORT, "power_report", 1, "report.pdf"),
        ]
        patched_storage["assocs"] = [FakeAssoc("h1", "classified")]
        client = TestClient(build_app(cfg_mock))
        r = client.get("/docs/I-1234", headers={"Accept": "application/json"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        data = r.json()
        assert len(data) == 1
        assert data[0]["doc_id_slug"] == "power_report"
        assert data[0]["download_url"].startswith("/dl/")

    def test_confirmation_skip_per_fr58(self, cfg_mock, patched_storage):
        """Per D2 cascade: item_type='Confirmation' renders empty doc section."""
        patched_storage["docs"] = []                 # No docs -> rendered as Confirmation per Ph-1 heuristic
        patched_storage["assocs"] = []
        client = TestClient(build_app(cfg_mock))
        r = client.get("/docs/I-confirmation")
        assert r.status_code == 200
        # FR-58: confirmation message is present
        assert "Confirmation item" in r.text or "no document section" in r.text.lower()

    def test_llm_review_placeholder_per_d7_cascade(self, cfg_mock, patched_storage):
        """Per D7 cascade 2026-06-23: llm_review_findings=None renders placeholder
        'AI review not enabled for this item (Ph-1 early drop)'."""
        patched_storage["docs"] = [
            FakeDoc("h1", DocType.TEST_REPORT, "report", 1, "x.pdf",
                    parser_result=None, llm_review_findings=None),
        ]
        patched_storage["assocs"] = [FakeAssoc("h1", "classified")]
        client = TestClient(build_app(cfg_mock))
        r = client.get("/docs/I-1234")
        assert "AI review not enabled" in r.text

    def test_staged_doc_renders_fr87_button_info(self, cfg_mock, patched_storage):
        """Per D4 cascade: doc_row_staged.html surfaces FR-87 step (A)(B)(C) info."""
        patched_storage["docs"] = [
            FakeDoc("h1", DocType.TEST_REPORT, "staged", 1, "x.pdf"),
        ]
        patched_storage["assocs"] = [FakeAssoc("h1", "staged_not_classified")]
        client = TestClient(build_app(cfg_mock))
        r = client.get("/docs/I-1234")
        assert "step (B)" in r.text or "doc_type re-classification" in r.text

    def test_unauth_request_rejected_in_production_mode(self, cfg_prod, patched_storage):
        """Per Invariant: production reverse-proxy mode rejects requests without
        Negotiate / X-Authenticated-User header."""
        client = TestClient(build_app(cfg_prod))
        r = client.get("/docs/I-1234")
        assert r.status_code == 401
        assert "DSH-E003" in r.text or r.headers.get("www-authenticate") == "Negotiate"

    def test_proxy_forwarded_identity_accepted(self, cfg_prod, patched_storage):
        """Per Invariant: trusted proxy-forwarded X-Authenticated-User header is accepted."""
        patched_storage["docs"] = []
        patched_storage["assocs"] = []
        client = TestClient(build_app(cfg_prod))
        r = client.get("/docs/I-1234", headers={PROXY_USER_HEADER: "y.vasilyev"})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# /dl/{scoped_token} tests
# ---------------------------------------------------------------------------


class TestDownloadFile:
    def test_valid_token_streams_file(self, cfg_mock, patched_storage):
        patched_storage["docs"] = [
            FakeDoc("h1", DocType.TEST_REPORT, "report", 1, "report.pdf"),
        ]
        patched_storage["tokens"]["validtoken"] = ("h1", "I-1234", SimpleNamespace())
        client = TestClient(build_app(cfg_mock))
        r = client.get("/dl/validtoken")
        assert r.status_code == 200
        assert "mock file content" in r.text
        # PDF -> inline disposition per FR-61
        assert "inline" in r.headers["content-disposition"]
        assert 'filename="report.pdf"' in r.headers["content-disposition"]

    def test_xlsx_attachment_disposition_per_fr61(self, cfg_mock, patched_storage):
        patched_storage["docs"] = [
            FakeDoc("h1", DocType.TEST_REPORT, "report", 1, "data.xlsx"),
        ]
        patched_storage["tokens"]["t-xlsx"] = ("h1", "I-1234", SimpleNamespace())
        client = TestClient(build_app(cfg_mock))
        r = client.get("/dl/t-xlsx")
        assert r.status_code == 200
        assert "attachment" in r.headers["content-disposition"]

    def test_expired_token_renders_dsh_e002_page(self, cfg_mock, patched_storage):
        """Per D2 cascade: expired/invalid token renders DSH-E002 friendly HTML
        with HTTP 410 (Gone), not 500."""
        client = TestClient(build_app(cfg_mock))
        r = client.get("/dl/notarealtoken")
        assert r.status_code == 410
        assert "Link expired" in r.text


# ---------------------------------------------------------------------------
# /milestone/{id}/refresh tests
# ---------------------------------------------------------------------------


class TestMilestoneRefresh:
    def test_refresh_without_dispatcher_returns_503(self, cfg_mock, patched_storage):
        """Per Ph-1 wiring: without workflow_engine.TriggerDispatcher injected,
        the endpoint returns 503."""
        client = TestClient(build_app(cfg_mock))
        r = client.post("/milestone/M-1/refresh")
        assert r.status_code == 503

    def test_refresh_with_dispatcher_dispatches(self, cfg_mock, patched_storage):
        """Per D3 cascade: dispatches via TriggerDispatcher; returns 202 with task_ids."""
        from unittest.mock import MagicMock
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch.return_value = SimpleNamespace(
            scheduled_tasks=["task-1", "task-2"], matched_count=2,
            correlation_id="c-1", skipped_matches=[],
        )
        state = MilestoneRefreshState()
        client = TestClient(build_app(cfg_mock, refresh_state=state, dispatcher=mock_dispatcher))
        r = client.post("/milestone/M-1/refresh")
        assert r.status_code == 202
        data = r.json()
        assert data["task_ids"] == ["task-1", "task-2"]
        assert data["matched_count"] == 2
        assert data["deduped"] is False

    def test_refresh_rate_limited_returns_dedup(self, cfg_mock, patched_storage):
        from unittest.mock import MagicMock
        mock_dispatcher = MagicMock()
        mock_dispatcher.dispatch.return_value = SimpleNamespace(
            scheduled_tasks=["task-1"], matched_count=1, correlation_id="c-1",
            skipped_matches=[],
        )
        state = MilestoneRefreshState()
        client = TestClient(build_app(cfg_mock, refresh_state=state, dispatcher=mock_dispatcher))
        r1 = client.post("/milestone/M-1/refresh")
        assert r1.status_code == 202
        r2 = client.post("/milestone/M-1/refresh")
        assert r2.status_code == 202
        assert r2.json()["deduped"] is True
        assert r2.json()["task_ids"] == ["task-1"]

    def test_refresh_status_endpoint(self, cfg_mock, patched_storage):
        state = MilestoneRefreshState()
        state.record("M-1", ["task-1"])
        client = TestClient(build_app(cfg_mock, refresh_state=state))
        r = client.get("/milestone/M-1/refresh/status")
        assert r.status_code == 200
        assert r.json()["status"] == "dispatched"
        assert r.json()["task_ids"] == ["task-1"]

    def test_refresh_status_unknown_milestone(self, cfg_mock, patched_storage):
        client = TestClient(build_app(cfg_mock))
        r = client.get("/milestone/M-unknown/refresh/status")
        assert r.status_code == 200
        assert r.json()["status"] == "no_refresh_requested"


# ---------------------------------------------------------------------------
# /admin/overrides tests
# ---------------------------------------------------------------------------


class TestAdminOverrides:
    def test_overrides_empty_in_ph1_per_d1_cascade(self, cfg_mock, patched_storage):
        """Per D1 cascade 2026-06-23: AutomationRuleOverride Postgres consumption
        Ph-2 deferred; Ph-1 endpoint renders empty table with Ph-1 note."""
        patched_storage["overrides"] = []
        client = TestClient(build_app(cfg_mock))
        r = client.get("/admin/overrides")
        assert r.status_code == 200
        assert "No active overrides" in r.text or "Ph-1" in r.text

    def test_overrides_renders_when_present(self, cfg_mock, patched_storage):
        from core.src.template_schema import RuleScope
        patched_storage["overrides"] = [
            SimpleNamespace(
                scope=RuleScope.GLOBAL, scope_id=None,
                rule_id="reminder_cadence", parameter_name="interval_minutes",
                parameter_value="30", set_by_pm_id="pm-001",
                set_at="2026-06-23T10:00:00Z",
            ),
        ]
        client = TestClient(build_app(cfg_mock))
        r = client.get("/admin/overrides")
        assert r.status_code == 200
        assert "reminder_cadence" in r.text


# ---------------------------------------------------------------------------
# Error code registration tests
# ---------------------------------------------------------------------------


class TestErrorCodes:
    def test_all_6_dsh_codes_registered(self):
        from core.src.diagnostics.error_codes import ERROR_CODES
        expected = {"DSH-E001", "DSH-E002", "DSH-E003", "DSH-E004",
                    "DSH-W001", "DSH-W002"}
        present = {c for c in ERROR_CODES if c.startswith("DSH-")}
        assert expected == present


# ---------------------------------------------------------------------------
# Config tests
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self):
        cfg = DashboardConfig()
        assert cfg.bind_port == 8443
        assert cfg.refresh_rate_limit_seconds == 300
        assert cfg.token_ttl_seconds == 300
        assert cfg.mock_auth is False
        assert cfg.cors_origins == ()

    def test_from_sources_env_override(self, monkeypatch):
        monkeypatch.setenv("HILDA_DASHBOARD_BIND_PORT", "9000")
        cfg = DashboardConfig.from_sources()
        assert cfg.bind_port == 9000
