"""test_feedback_routes.py -- FB-3 route smoke + validation tests.

Uses fastapi.testclient with a real SQLite-backed FeedbackStorage injected
via build_app's app.state.feedback_storage override. Covers:
  - GET /feedback/{c}/{d} redirects to /DRR
  - GET /feedback/{c}/{d}/{m} renders empty + populated views
  - POST /submit bug (no attachment) -> 303 + ticket created
  - POST /submit improvement forces bug_type=OTHER-OTHER server-side
  - POST /submit improvement without description -> 400
  - POST /submit bad category / bad bug_type / bad target_milestone -> 400
  - POST /submit with attachment stored + downloadable
  - POST /submit oversized attachment -> 413
  - GET attachment: hit, scope-mismatch 404, missing-ticket 404, no-attachment 404
"""
from __future__ import annotations

import io

import pytest

# python-multipart is required by FastAPI to parse Form(...) and File(...)
# inputs. It's in requirements.txt (installed in hilda-api container) but
# corp local dev environments occasionally miss it -- skip cleanly rather
# than crash the suite.
pytest.importorskip(
    "multipart",
    reason="python-multipart not installed; skipping route tests (runs in container)",
)

pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning",
)

from fastapi.testclient import TestClient

from core.src.dashboard import DashboardConfig, build_app
from core.src.dashboard.feedback_routes import _MAX_ATTACHMENT_BYTES
from core.src.storage.db import configure_engine, init_db


@pytest.fixture(autouse=True)
async def _fresh_db(tmp_path):
    db_file = tmp_path / "test_fb_routes.db"
    engine = configure_engine(f"sqlite+aiosqlite:///{db_file}")
    await init_db()
    yield
    await engine.dispose()


@pytest.fixture
def client():
    """FastAPI TestClient with feedback routes wired to the sqlite engine."""
    cfg = DashboardConfig(mock_auth=True, ph1_minimal=False)
    app = build_app(cfg)
    return TestClient(app, follow_redirects=False)


class TestRedirect:
    def test_bare_scope_redirects_to_drr(self, client):
        r = client.get("/feedback/MMK/SM-A012U")
        assert r.status_code == 302
        assert r.headers["location"] == "/feedback/MMK/SM-A012U/DRR"


class TestViewPage:
    def test_empty_scope_renders(self, client):
        r = client.get("/feedback/MMK/SM-A012U/DRR")
        assert r.status_code == 200
        assert "Feedback" in r.text
        assert "MMK" in r.text
        assert "SM-A012U" in r.text
        assert "DRR" in r.text
        assert "No tickets yet" in r.text

    def test_view_lists_tickets(self, client):
        # Submit two tickets, then GET and expect both in the page.
        client.post(
            "/feedback/MMK/SM-A012U/DRR/submit",
            data={
                "category": "bug",
                "bug_type": "SETUP-setup button not available / does not work",
                "description": "first ticket",
                "target_milestone": "DRR",
            },
        )
        client.post(
            "/feedback/MMK/SM-A012U/DRR/submit",
            data={
                "category": "improvement",
                "bug_type": "OTHER-OTHER",
                "description": "second ticket -- improvement",
                "target_milestone": "DRR",
            },
        )
        r = client.get("/feedback/MMK/SM-A012U/DRR")
        assert r.status_code == 200
        assert "MMK-SM-A012U-DRR-1" in r.text
        assert "MMK-SM-A012U-DRR-2" in r.text
        assert "first ticket" in r.text
        assert "improvement" in r.text


class TestSubmitValidation:
    def test_submit_bug_success(self, client):
        r = client.post(
            "/feedback/MMK/SM-A012U/DRR/submit",
            data={
                "category": "bug",
                "bug_type": "SETUP-setup button not available / does not work",
                "description": "",
                "target_milestone": "DRR",
            },
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/feedback/MMK/SM-A012U/DRR"

    def test_submit_improvement_forces_other_other(self, client):
        # Client sends a garbage bug_type with category=improvement; server
        # should overwrite to OTHER-OTHER (defensive) and accept.
        r = client.post(
            "/feedback/MMK/SM-A012U/DRR/submit",
            data={
                "category": "improvement",
                "bug_type": "GARBAGE-not real",   # ignored server-side
                "description": "please add a status filter dropdown",
                "target_milestone": "DRR",
            },
        )
        assert r.status_code == 303
        # Verify the persisted ticket has bug_type OTHER-OTHER.
        r2 = client.get("/feedback/MMK/SM-A012U/DRR")
        assert "OTHER-OTHER" in r2.text

    def test_submit_improvement_without_description_rejected(self, client):
        r = client.post(
            "/feedback/MMK/SM-A012U/DRR/submit",
            data={
                "category": "improvement",
                "bug_type": "OTHER-OTHER",
                "description": "   ",   # whitespace-only
                "target_milestone": "DRR",
            },
        )
        assert r.status_code == 400
        assert "description is required" in r.text

    def test_submit_bad_category_rejected(self, client):
        r = client.post(
            "/feedback/MMK/SM-A012U/DRR/submit",
            data={
                "category": "not-a-category",
                "bug_type": "SETUP-setup button not available / does not work",
                "description": "",
                "target_milestone": "DRR",
            },
        )
        assert r.status_code == 400
        assert "invalid category" in r.text

    def test_submit_bad_bug_type_rejected(self, client):
        r = client.post(
            "/feedback/MMK/SM-A012U/DRR/submit",
            data={
                "category": "bug",
                "bug_type": "SETUP-something not on the list",
                "description": "",
                "target_milestone": "DRR",
            },
        )
        assert r.status_code == 400
        assert "invalid bug_type" in r.text

    def test_submit_bad_target_milestone_rejected(self, client):
        r = client.post(
            "/feedback/MMK/SM-A012U/DRR/submit",
            data={
                "category": "bug",
                "bug_type": "SETUP-setup button not available / does not work",
                "description": "",
                "target_milestone": "NOT-A-MILESTONE",
            },
        )
        assert r.status_code == 400
        assert "invalid target_milestone" in r.text


class TestSubmitAttachment:
    def test_submit_with_attachment_stored_and_downloadable(self, client):
        payload = b"fake png bytes for the test attachment"
        r = client.post(
            "/feedback/MMK/SM-A012U/DRR/submit",
            data={
                "category": "bug",
                "bug_type": "OTHER-OTHER",
                "description": "see attached",
                "target_milestone": "DRR",
            },
            files={
                "attachment": ("screenshot.png", io.BytesIO(payload), "image/png"),
            },
        )
        assert r.status_code == 303

        # Confirm ticket lists show the attachment link
        view = client.get("/feedback/MMK/SM-A012U/DRR")
        assert "screenshot.png" in view.text
        assert "/attachment/" in view.text

        # Download the attachment (ticket_pk=1 since it's the first row)
        dl = client.get("/feedback/MMK/SM-A012U/DRR/attachment/1")
        assert dl.status_code == 200
        assert dl.content == payload
        assert dl.headers["content-type"].startswith("image/png")
        assert "screenshot.png" in dl.headers["content-disposition"]

    def test_submit_oversized_attachment_rejected(self, client):
        oversized = b"x" * (_MAX_ATTACHMENT_BYTES + 1)
        r = client.post(
            "/feedback/MMK/SM-A012U/DRR/submit",
            data={
                "category": "bug",
                "bug_type": "OTHER-OTHER",
                "description": "",
                "target_milestone": "DRR",
            },
            files={
                "attachment": ("big.bin", io.BytesIO(oversized), "application/octet-stream"),
            },
        )
        assert r.status_code == 413
        assert "exceeds" in r.text


class TestAttachmentDownload:
    def _create_ticket_with_attachment(self, client, customer="MMK", device="SM-A012U",
                                       milestone="DRR", payload=b"data"):
        client.post(
            f"/feedback/{customer}/{device}/{milestone}/submit",
            data={
                "category": "bug",
                "bug_type": "OTHER-OTHER",
                "description": "",
                "target_milestone": milestone,
            },
            files={"attachment": ("f.bin", io.BytesIO(payload), "application/octet-stream")},
        )

    def test_missing_ticket_404(self, client):
        r = client.get("/feedback/MMK/SM-A012U/DRR/attachment/99999")
        assert r.status_code == 404
        assert "not found" in r.text

    def test_scope_mismatch_404(self, client):
        # Create ticket in one scope, request from another URL.
        self._create_ticket_with_attachment(client, device="SM-A012U")
        r = client.get("/feedback/MMK/SM-M456U/DRR/attachment/1")
        assert r.status_code == 404
        assert "not found in this scope" in r.text

    def test_ticket_without_attachment_404(self, client):
        # Create ticket WITHOUT attachment
        client.post(
            "/feedback/MMK/SM-A012U/DRR/submit",
            data={
                "category": "bug",
                "bug_type": "OTHER-OTHER",
                "description": "no file",
                "target_milestone": "DRR",
            },
        )
        r = client.get("/feedback/MMK/SM-A012U/DRR/attachment/1")
        assert r.status_code == 404
        assert "no attachment" in r.text


class TestCrossScopeIndependence:
    def test_ticket_seq_independent_per_scope(self, client):
        # Submit one per scope; each starts at seq 1.
        client.post(
            "/feedback/MMK/SM-A012U/DRR/submit",
            data={"category": "bug",
                  "bug_type": "SETUP-setup button not available / does not work",
                  "description": "",
                  "target_milestone": "DRR"},
        )
        client.post(
            "/feedback/MMK/SM-M456U/DRR/submit",
            data={"category": "bug",
                  "bug_type": "SETUP-setup button not available / does not work",
                  "description": "",
                  "target_milestone": "DRR"},
        )
        a = client.get("/feedback/MMK/SM-A012U/DRR")
        b = client.get("/feedback/MMK/SM-M456U/DRR")
        assert "MMK-SM-A012U-DRR-1" in a.text
        assert "MMK-SM-M456U-DRR-1" in b.text
        # No cross-contamination.
        assert "SM-M456U" not in a.text.replace("SM-M456U-DRR", "XX")  # allow only the scope header if any
        assert "SM-A012U" not in b.text.replace("SM-A012U-DRR", "XX")
