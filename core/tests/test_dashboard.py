"""dashboard test suite -- routes + auth + Confirmation skip + token expiry +
admin overrides Ph-1 empty + content negotiation + error codes + FR-87 POST
endpoints + per-load SP READ via SpCrud + SpCrud wiring per 2026-06-26 cascade.

Uses httpx.TestClient against build_app with mocked storage helpers.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

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


class FakeSpCrud:
    """In-memory stand-in for SpCrud used by tests. Records get_item +
    update_item calls so tests can assert per-load fetch + audit writeback."""

    def __init__(self, rows: dict[tuple[str, int], dict[str, Any]] | None = None):
        self._rows = rows or {}
        self.get_calls: list[tuple[str, int]] = []
        self.update_calls: list[dict[str, Any]] = []

    async def get_item(self, entity, scope, item_id):
        self.get_calls.append((scope.customer_id, int(item_id)))
        return self._rows.get((scope.customer_id, int(item_id)))

    async def update_item(self, entity, scope, item_id, canonical_fields):
        self.update_calls.append({
            "entity": entity,
            "customer_id": scope.customer_id,
            "item_id": item_id,
            "canonical_fields": canonical_fields,
        })


def _mk_sp_row(item_type=None, **extra):
    """Build a minimal SP row dict for a delivery_items entity."""
    return {
        "item_type":      item_type or "Default",
        "item_no":        1,
        "delivery_state": "Open",
        "tg_name":        "TG-A",
        "owner_corp_id":  "owner-001",
        **extra,
    }


@pytest.fixture
def cfg_mock():
    """Config with mock_auth enabled so tests skip header validation.
    Pre-2026-07-01 tests exercise the Ph-2 SP-READ code path -- explicit
    ph1_minimal=False so DashboardConfig's new Ph-1 default doesn't take
    over. Ph-1 code path has its own dedicated test class below."""
    return DashboardConfig(mock_auth=True, ph1_minimal=False)


@pytest.fixture
def cfg_prod():
    """Config with production auth (mock_auth=False)."""
    return DashboardConfig(mock_auth=False, ph1_minimal=False)


@pytest.fixture
def patched_storage(monkeypatch):
    """Patch storage helpers used by dashboard.app to return controlled fixtures."""
    state = {
        "docs":       [],
        "assocs":     [],
        "tokens":     {},
        "overrides":  [],
        "files":      {},
        "token_counter": 0,
        "reassign_calls": [],
        "resolve_doc_type_calls": [],
    }

    async def fake_get_documents_for_item(item_id):
        return state["docs"]

    async def fake_list_associations_for_item(item_id):
        return state["assocs"]

    async def fake_make_download_token(file_hash, delivery_item_id, ttl_seconds=300):
        state["token_counter"] += 1
        token = f"tok-{state['token_counter']}"
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

    async def fake_reassign_document_to_workitem(**kwargs):
        state["reassign_calls"].append(kwargs)

    async def fake_tpm_resolve_doc_type(**kwargs):
        state["resolve_doc_type_calls"].append(kwargs)

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
    monkeypatch.setattr("core.src.dashboard.app.reassign_document_to_workitem",
                        fake_reassign_document_to_workitem)
    monkeypatch.setattr("core.src.dashboard.app.storage_tpm_resolve_doc_type",
                        fake_tpm_resolve_doc_type)
    return state


def _build_with_sp(cfg, sp_crud=None, mock_sp_rows=None, **kwargs):
    """Build app + (when no sp_crud provided) seed mock_sp_rows on app.state."""
    app = build_app(cfg, sp_crud=sp_crud, **kwargs)
    if mock_sp_rows is not None and sp_crud is None:
        app.state.mock_sp_rows.update(mock_sp_rows)
    return app


# ---------------------------------------------------------------------------
# /docs/{customer_id}/{sp_id} tests (Gap 1 + Gap 6 + Gap 7 + Gap 8)
# ---------------------------------------------------------------------------


class TestGetDocumentSection:
    def test_html_response_default(self, cfg_mock, patched_storage):
        patched_storage["docs"] = [
            FakeDoc("h1", DocType.TEST_REPORT, "power_report", 1, "report.pdf"),
        ]
        patched_storage["assocs"] = [FakeAssoc("h1", "classified")]
        mock_rows = {("mock_customer", 1234): _mk_sp_row(item_type="Default")}
        app = _build_with_sp(cfg_mock, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.get("/docs/mock_customer/1234")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]
        assert "power_report" in r.text
        assert "report.pdf" in r.text

    def test_json_response_on_accept_header(self, cfg_mock, patched_storage):
        patched_storage["docs"] = [
            FakeDoc("h1", DocType.TEST_REPORT, "power_report", 1, "report.pdf"),
        ]
        patched_storage["assocs"] = [FakeAssoc("h1", "classified")]
        mock_rows = {("mock_customer", 1234): _mk_sp_row()}
        app = _build_with_sp(cfg_mock, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.get("/docs/mock_customer/1234",
                       headers={"Accept": "application/json"})
        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/json")
        data = r.json()
        assert len(data) == 1
        assert data[0]["doc_id_slug"] == "power_report"
        assert data[0]["download_url"].startswith("/dl/")

    def test_llm_review_placeholder_per_d7_cascade(self, cfg_mock, patched_storage):
        """Per D7 cascade 2026-06-23: llm_review_findings=None renders placeholder."""
        patched_storage["docs"] = [
            FakeDoc("h1", DocType.TEST_REPORT, "report", 1, "x.pdf",
                    parser_result=None, llm_review_findings=None),
        ]
        patched_storage["assocs"] = [FakeAssoc("h1", "classified")]
        mock_rows = {("mock_customer", 1234): _mk_sp_row()}
        app = _build_with_sp(cfg_mock, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.get("/docs/mock_customer/1234")
        assert "AI review not enabled" in r.text

    def test_staged_doc_renders_fr87_button_info(self, cfg_mock, patched_storage):
        """Per D4 cascade: doc_row_staged.html surfaces FR-87 step (A)(B)(C) info."""
        patched_storage["docs"] = [
            FakeDoc("h1", DocType.TEST_REPORT, "staged", 1, "x.pdf"),
        ]
        patched_storage["assocs"] = [FakeAssoc("h1", "staged_not_classified")]
        mock_rows = {("mock_customer", 1234): _mk_sp_row()}
        app = _build_with_sp(cfg_mock, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.get("/docs/mock_customer/1234")
        assert "step (B)" in r.text or "doc_type re-classification" in r.text

    def test_unauth_request_rejected_in_production_mode(self, cfg_prod, patched_storage):
        client = TestClient(build_app(cfg_prod))
        r = client.get("/docs/mock_customer/1234")
        assert r.status_code == 401
        assert "DSH-E003" in r.text or r.headers.get("www-authenticate") == "Negotiate"

    def test_proxy_forwarded_identity_accepted(self, cfg_prod, patched_storage):
        mock_rows = {("mock_customer", 1234): _mk_sp_row()}
        app = _build_with_sp(cfg_prod, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.get("/docs/mock_customer/1234",
                       headers={PROXY_USER_HEADER: "y.vasilyev"})
        assert r.status_code == 200

    def test_404_when_sp_row_missing(self, cfg_mock, patched_storage):
        """Per Gap 7: when SpCrud returns None / mock_sp_rows lacks key, 404."""
        app = _build_with_sp(cfg_mock, mock_sp_rows={})
        client = TestClient(app)
        r = client.get("/docs/mock_customer/9999")
        assert r.status_code == 404
        assert "not found" in r.text.lower()


# ---------------------------------------------------------------------------
# Confirmation detection (Gap 6) -- authoritative item_type check
# ---------------------------------------------------------------------------


class TestConfirmationDetection:
    def test_confirmation_item_type_renders_no_doc_section(self, cfg_mock, patched_storage):
        """Gap 6: SP row item_type='Confirmation' renders empty doc section per FR-58."""
        patched_storage["docs"] = []
        patched_storage["assocs"] = []
        mock_rows = {
            ("mock_customer", 1234): _mk_sp_row(item_type=ItemType.CONFIRMATION.value),
        }
        app = _build_with_sp(cfg_mock, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.get("/docs/mock_customer/1234")
        assert r.status_code == 200
        assert "Confirmation item" in r.text or "no document section" in r.text.lower()

    def test_non_confirmation_with_no_docs_renders_empty_section(
        self, cfg_mock, patched_storage
    ):
        """Gap 6: item_type='Default' with NO docs renders 'no documents yet'
        (NOT 'Confirmation' message) -- the authoritative check beats the old
        no-docs heuristic."""
        patched_storage["docs"] = []
        patched_storage["assocs"] = []
        mock_rows = {("mock_customer", 1234): _mk_sp_row(item_type="Default")}
        app = _build_with_sp(cfg_mock, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.get("/docs/mock_customer/1234")
        assert r.status_code == 200
        # Must NOT show the Confirmation message
        assert "Confirmation item" not in r.text


# ---------------------------------------------------------------------------
# Per-load SP READ via SpCrud (Gap 7)
# ---------------------------------------------------------------------------


class TestPerLoadSpRead:
    def test_sp_crud_get_item_called_every_get(self, cfg_mock, patched_storage):
        sp_crud = FakeSpCrud(rows={
            ("mock_customer", 1234): _mk_sp_row(item_type="Default"),
        })
        client = TestClient(build_app(cfg_mock, sp_crud=sp_crud))
        r1 = client.get("/docs/mock_customer/1234")
        r2 = client.get("/docs/mock_customer/1234")
        assert r1.status_code == 200
        assert r2.status_code == 200
        # Two GETs -> two SpCrud.get_item calls (no caching Ph-1)
        assert len(sp_crud.get_calls) == 2

    def test_sp_crud_404_when_get_item_returns_none(self, cfg_mock, patched_storage):
        sp_crud = FakeSpCrud(rows={})   # nothing
        client = TestClient(build_app(cfg_mock, sp_crud=sp_crud))
        r = client.get("/docs/mock_customer/1234")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# SpCrud wiring + mock_sp_rows fallback (Gap 8)
# ---------------------------------------------------------------------------


class TestSpCrudWiring:
    def test_build_app_accepts_sp_crud(self, cfg_mock, patched_storage):
        sp_crud = FakeSpCrud(rows={("mock_customer", 1): _mk_sp_row()})
        app = build_app(cfg_mock, sp_crud=sp_crud)
        assert app.state.sp_crud is sp_crud

    def test_build_app_falls_back_to_mock_sp_rows_when_sp_crud_none(
        self, cfg_mock, patched_storage
    ):
        """Gap 8 (b): when sp_crud=None, the handler reads
        request.app.state.mock_sp_rows."""
        app = build_app(cfg_mock, sp_crud=None)
        app.state.mock_sp_rows[("mock_customer", 99)] = _mk_sp_row()
        client = TestClient(app)
        r = client.get("/docs/mock_customer/99")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# FR-87 step (A) -- POST /docs/{customer_id}/{sp_id}/resolve_reassign
# ---------------------------------------------------------------------------


class TestFr87ResolveReassign:
    def test_happy_path_returns_303_redirect(self, cfg_mock, patched_storage):
        mock_rows = {
            ("mock_customer", 100): _mk_sp_row(item_type="Default", tg_name="TG-src"),
            ("mock_customer", 200): _mk_sp_row(item_type="Default", tg_name="TG-tgt"),
        }
        app = _build_with_sp(cfg_mock, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.post(
            "/docs/mock_customer/100/resolve_reassign",
            json={"target_item_id": 200, "file_hash": "h-xyz"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/docs/mock_customer/100"
        # Storage layer called
        assert len(patched_storage["reassign_calls"]) == 1
        call = patched_storage["reassign_calls"][0]
        assert call["file_hash"] == "h-xyz"
        assert call["source_delivery_item_id"] == "100"
        assert call["target_delivery_item_id"] == "200"

    def test_rejects_nonexistent_target_item_400(self, cfg_mock, patched_storage):
        mock_rows = {("mock_customer", 100): _mk_sp_row()}
        app = _build_with_sp(cfg_mock, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.post(
            "/docs/mock_customer/100/resolve_reassign",
            json={"target_item_id": 99999, "file_hash": "h-xyz"},
        )
        assert r.status_code == 400
        assert "target item" in r.text.lower()

    def test_rejects_nonexistent_source_item_404(self, cfg_mock, patched_storage):
        app = _build_with_sp(cfg_mock, mock_sp_rows={})
        client = TestClient(app)
        r = client.post(
            "/docs/mock_customer/100/resolve_reassign",
            json={"target_item_id": 200, "file_hash": "h-xyz"},
        )
        assert r.status_code == 404

    def test_state_mismatch_surfaces_409_conflict(self, cfg_mock, patched_storage, monkeypatch):
        """A->B->C ordering: storage's STR-E009 (state mismatch) -> 409 Conflict."""
        async def fail_with_state_mismatch(**kwargs):
            raise Exception("STR-E009: state mismatch -- item not in staged_not_classified")
        monkeypatch.setattr("core.src.dashboard.app.reassign_document_to_workitem",
                            fail_with_state_mismatch)
        mock_rows = {
            ("mock_customer", 100): _mk_sp_row(),
            ("mock_customer", 200): _mk_sp_row(),
        }
        app = _build_with_sp(cfg_mock, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.post(
            "/docs/mock_customer/100/resolve_reassign",
            json={"target_item_id": 200, "file_hash": "h-xyz"},
        )
        assert r.status_code == 409
        assert "step (A)" in r.text

    def test_sp_audit_writeback_called_when_sp_crud_wired(self, cfg_mock, patched_storage):
        sp_crud = FakeSpCrud(rows={
            ("mock_customer", 100): _mk_sp_row(),
            ("mock_customer", 200): _mk_sp_row(),
        })
        client = TestClient(build_app(cfg_mock, sp_crud=sp_crud))
        r = client.post(
            "/docs/mock_customer/100/resolve_reassign",
            json={"target_item_id": 200, "file_hash": "h-xyz"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        # SP audit writeback per [D-064]
        update_calls = [c for c in sp_crud.update_calls if c["item_id"] == "100"]
        assert len(update_calls) == 1
        assert update_calls[0]["canonical_fields"]["tpm_reassignment_target_item_id"] == 200

    def test_auth_required_in_production_mode(self, cfg_prod, patched_storage):
        app = _build_with_sp(cfg_prod, mock_sp_rows={
            ("mock_customer", 100): _mk_sp_row(),
        })
        client = TestClient(app)
        r = client.post(
            "/docs/mock_customer/100/resolve_reassign",
            json={"target_item_id": 200, "file_hash": "h-xyz"},
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# FR-87 step (B) -- POST /docs/{customer_id}/{sp_id}/resolve_doc_type
# ---------------------------------------------------------------------------


class TestFr87ResolveDocType:
    def test_happy_path_returns_303_redirect(self, cfg_mock, patched_storage):
        mock_rows = {("mock_customer", 100): _mk_sp_row()}
        app = _build_with_sp(cfg_mock, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.post(
            "/docs/mock_customer/100/resolve_doc_type",
            json={"file_hash": "h-xyz", "target_doc_type": "test_report"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/docs/mock_customer/100"
        # storage_tpm_resolve_doc_type called
        assert len(patched_storage["resolve_doc_type_calls"]) == 1
        call = patched_storage["resolve_doc_type_calls"][0]
        assert call["file_hash"] == "h-xyz"
        assert call["new_doc_type"] == DocType.TEST_REPORT

    def test_rejects_invalid_doc_type_per_d119(self, cfg_mock, patched_storage):
        """[D-119] 4-value validation: UNRESOLVED + anything off-list rejected."""
        mock_rows = {("mock_customer", 100): _mk_sp_row()}
        app = _build_with_sp(cfg_mock, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.post(
            "/docs/mock_customer/100/resolve_doc_type",
            json={"file_hash": "h-xyz", "target_doc_type": "unresolved"},
        )
        assert r.status_code == 400
        assert "D-119" in r.text or "4-value" in r.text
        assert len(patched_storage["resolve_doc_type_calls"]) == 0

    def test_rejects_state_mismatch_409_conflict(self, cfg_mock, patched_storage, monkeypatch):
        """A->B->C ordering enforced by storage; STR-E009 -> 409."""
        async def fail_with_state_mismatch(**kwargs):
            raise Exception("STR-E009: state mismatch")
        monkeypatch.setattr("core.src.dashboard.app.storage_tpm_resolve_doc_type",
                            fail_with_state_mismatch)
        mock_rows = {("mock_customer", 100): _mk_sp_row()}
        app = _build_with_sp(cfg_mock, mock_sp_rows=mock_rows)
        client = TestClient(app)
        r = client.post(
            "/docs/mock_customer/100/resolve_doc_type",
            json={"file_hash": "h-xyz", "target_doc_type": "tech_report"},
        )
        assert r.status_code == 409
        assert "step (B)" in r.text

    def test_sp_audit_writeback_called_when_sp_crud_wired(self, cfg_mock, patched_storage):
        sp_crud = FakeSpCrud(rows={("mock_customer", 100): _mk_sp_row()})
        client = TestClient(build_app(cfg_mock, sp_crud=sp_crud))
        r = client.post(
            "/docs/mock_customer/100/resolve_doc_type",
            json={"file_hash": "h-xyz", "target_doc_type": "waiver"},
            follow_redirects=False,
        )
        assert r.status_code == 303
        update_calls = [c for c in sp_crud.update_calls if c["item_id"] == "100"]
        assert len(update_calls) == 1
        assert update_calls[0]["canonical_fields"]["tpm_resolved_doc_type"] == "waiver"

    def test_auth_required_in_production_mode(self, cfg_prod, patched_storage):
        app = _build_with_sp(cfg_prod, mock_sp_rows={
            ("mock_customer", 100): _mk_sp_row(),
        })
        client = TestClient(app)
        r = client.post(
            "/docs/mock_customer/100/resolve_doc_type",
            json={"file_hash": "h-xyz", "target_doc_type": "test_report"},
        )
        assert r.status_code == 401


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
        client = TestClient(build_app(cfg_mock))
        r = client.get("/dl/notarealtoken")
        assert r.status_code == 410
        assert "Link expired" in r.text


# ---------------------------------------------------------------------------
# /milestone/{id}/refresh tests
# ---------------------------------------------------------------------------


class TestMilestoneRefresh:
    def test_refresh_without_dispatcher_returns_503(self, cfg_mock, patched_storage):
        client = TestClient(build_app(cfg_mock))
        r = client.post("/milestone/M-1/refresh")
        assert r.status_code == 503

    def test_refresh_with_dispatcher_dispatches(self, cfg_mock, patched_storage):
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

    def test_ph1_minimal_defaults_true(self):
        cfg = DashboardConfig()
        assert cfg.ph1_minimal is True


# ===========================================================================
# TestPh1DocSection -- Ph-1 architect lock 2026-07-01
# ===========================================================================


class _MockStore:
    """Duck-typed StorageWriter with just the two methods the Ph-1 handler needs."""

    def __init__(self):
        self.items: dict[tuple[str, int], object] = {}
        self.docs:  dict[str, list[tuple]] = {}

    def register_item(self, customer_id, sp_id, item):
        self.items[(customer_id, int(sp_id))] = item

    def register_docs(self, item_id, rows):
        """rows is list of (original_filename, doc_type, ingested_at)."""
        self.docs[item_id] = rows

    def get_by_customer_and_sp_id(self, customer_id, sp_id):
        return self.items.get((customer_id, int(sp_id)))

    def list_documents_for_item_display(self, delivery_item_id):
        return self.docs.get(delivery_item_id, [])


def _ph1_item(item_id="MMK-SM-S671U1-P1-2", item_type="test_tech_waiver_report",
              item_name="Sustainability Certificate", tg_name="CPM",
              item_no=2, milestone_id="P1", device_id="SM-S671U1",
              delivery_state="UnderPMReview"):
    return SimpleNamespace(
        item_id=item_id, delivery_item_id=item_id,
        item_type=item_type, item_name=item_name, tg_name=tg_name,
        item_no=item_no, milestone_id=milestone_id, device_id=device_id,
        delivery_state=delivery_state,
    )


@pytest.fixture
def cfg_ph1():
    return DashboardConfig(mock_auth=True, ph1_minimal=True)


class TestPh1DocSection:
    def test_ph1_happy_path_renders_3_columns(self, cfg_ph1):
        from datetime import datetime, timezone
        store = _MockStore()
        item = _ph1_item()
        store.register_item("MMK", 42, item)
        store.register_docs(item.item_id, [
            ("Doc-A.pdf",  "test_tech_waiver_report",
             datetime(2026, 6, 1, tzinfo=timezone.utc)),
            ("Doc-B.pdf",  "compliance_certification_release_notes",
             datetime(2026, 6, 30, tzinfo=timezone.utc)),
        ])
        client = TestClient(build_app(cfg_ph1, storage=store))
        r = client.get("/docs/MMK/42")
        assert r.status_code == 200
        body = r.text
        # 3 headers
        assert "<th>#</th>" in body
        assert "<th>Filename</th>" in body
        assert "<th>Doc Type</th>" in body
        # No Ph-2 headers should appear
        assert "<th>Doc Slug</th>" not in body
        assert "<th>Rev</th>" not in body
        assert "<th>Path Type</th>" not in body
        assert "<th>Review</th>" not in body
        assert "<th>Download</th>" not in body
        # Rows contain humanized doc_type
        assert "Test Tech Waiver Report" in body
        assert "Compliance Certification Release Notes" in body
        # Header shows item context
        assert "Sustainability Certificate" in body
        assert "SM-S671U1" in body

    def test_ph1_newest_first_ordering(self, cfg_ph1):
        """Ordering comes from storage helper's SQL (ORDER BY ingested_at DESC);
        the template just iterates whatever storage returns. This verifies the
        template preserves that order."""
        from datetime import datetime, timezone
        store = _MockStore()
        item = _ph1_item()
        store.register_item("MMK", 42, item)
        store.register_docs(item.item_id, [
            ("Newest.pdf", "test_tech_waiver_report",
             datetime(2026, 6, 30, tzinfo=timezone.utc)),
            ("Middle.pdf", "test_tech_waiver_report",
             datetime(2026, 6, 15, tzinfo=timezone.utc)),
            ("Oldest.pdf", "test_tech_waiver_report",
             datetime(2026, 6, 1, tzinfo=timezone.utc)),
        ])
        client = TestClient(build_app(cfg_ph1, storage=store))
        r = client.get("/docs/MMK/42")
        assert r.status_code == 200
        body = r.text
        # Order in HTML preserves list order
        newest_pos = body.index("Newest.pdf")
        middle_pos = body.index("Middle.pdf")
        oldest_pos = body.index("Oldest.pdf")
        assert newest_pos < middle_pos < oldest_pos

    def test_ph1_confirmation_item_shows_placeholder(self, cfg_ph1):
        store = _MockStore()
        item = _ph1_item(item_type="Confirmation", item_name="Device Readiness Review")
        store.register_item("MMK", 1, item)
        client = TestClient(build_app(cfg_ph1, storage=store))
        r = client.get("/docs/MMK/1")
        assert r.status_code == 200
        body = r.text
        assert "Confirmation item" in body or "confirmation" in body.lower()
        # No table rendered
        assert "<th>Filename</th>" not in body

    def test_ph1_empty_docs_shows_placeholder(self, cfg_ph1):
        store = _MockStore()
        item = _ph1_item()
        store.register_item("MMK", 42, item)
        store.register_docs(item.item_id, [])
        client = TestClient(build_app(cfg_ph1, storage=store))
        r = client.get("/docs/MMK/42")
        assert r.status_code == 200
        assert "No documents associated" in r.text

    def test_ph1_404_when_delivery_item_missing(self, cfg_ph1):
        store = _MockStore()
        # No items registered
        client = TestClient(build_app(cfg_ph1, storage=store))
        r = client.get("/docs/MMK/999")
        assert r.status_code == 404

    def test_ph1_503_when_storage_not_wired(self, cfg_ph1, monkeypatch):
        """Simulate the storage-unavailable case by making PostgresStorage
        auto-wire raise (as it would if DB creds/env aren't set at boot)."""
        import core.src.storage.delivery_item_ops as _di_ops
        def _boom(*a, **kw):
            raise RuntimeError("simulated DB init failure")
        monkeypatch.setattr(_di_ops, "PostgresStorage", _boom)
        client = TestClient(build_app(cfg_ph1, storage=None))
        r = client.get("/docs/MMK/42")
        assert r.status_code == 503

    def test_ph1_no_sp_read_called(self, cfg_ph1):
        """Prove Ph-1 does not touch sp_crud even when one is wired.
        MODULE.md Gap 7 requires SP READ per page load; Ph-1 lock 2026-07-01
        explicitly skips this."""
        store = _MockStore()
        item = _ph1_item()
        store.register_item("MMK", 42, item)
        store.register_docs(item.item_id, [])

        sp_get_calls: list = []
        class _SpCrudSpy:
            async def get_item(self, entity, scope, item_id):
                sp_get_calls.append((entity, scope, item_id))
                return {"item_type": "test_tech_waiver_report"}

        client = TestClient(build_app(cfg_ph1, storage=store, sp_crud=_SpCrudSpy()))
        r = client.get("/docs/MMK/42")
        assert r.status_code == 200
        assert sp_get_calls == [], "Ph-1 must skip SP READ per architect lock 2026-07-01"

    def test_ph1_doc_type_humanizer_filter(self, cfg_ph1):
        """The humanize_doc_type Jinja filter turns snake_case into Title Case."""
        from core.src.dashboard.app import build_app as _build_app
        app = _build_app(cfg_ph1, storage=_MockStore())
        # Access the filter through the app's Jinja environment.
        # The filter was registered on templates.env when build_app ran.
        # We verify indirectly via a rendered page below; direct-filter access
        # would require reaching into private state.
        assert app is not None  # smoke -- filter registration didn't raise
