"""D-150 HILDA-side documents view routes -- browse + WOPI + edit + download."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from core.src.dashboard import DashboardConfig, build_app
from core.src.dashboard.document_view_routes import (
    _make_scoped_token,
    _resolve_scoped_token,
    _make_wopi_jwt,
    _verify_wopi_jwt,
    _open_mode_for,
    _encode_file_id,
    _decode_file_id,
)
from core.src.storage import (
    configure_engine,
    init_db,
    save_view_document,
)
from core.src.storage.config import GlobalStorageConfig, set_storage_config


@pytest.fixture(autouse=True)
async def env(tmp_path):
    set_storage_config(GlobalStorageConfig(nsd_mount_root=tmp_path / "nsd"))
    engine = configure_engine("sqlite+aiosqlite:///:memory:")
    await init_db()
    yield
    await engine.dispose()
    set_storage_config(None)


@pytest.fixture
def cfg():
    return DashboardConfig(
        mock_auth=True,
        ph1_minimal=False,
        wopi_jwt_secret="test-secret-abcdef1234567890",
        onlyoffice_public_url="http://oo.test/office",
        onlyoffice_internal_url="http://onlyoffice-ds",
        reverse_proxy_origin="http://hilda.test:8443",
    )


# ---------------------------------------------------------------------------
# Token round-trip + tamper detection
# ---------------------------------------------------------------------------


class TestScopedToken:
    def test_roundtrip(self):
        tok = _make_scoped_token(
            secret="s", view_relative_path="view/c/d/m/tg/x.xlsx",
            mode="edit", user_id="pm.smith",
        )
        payload = _resolve_scoped_token(secret="s", token=tok)
        assert payload["p"] == "view/c/d/m/tg/x.xlsx"
        assert payload["m"] == "edit"
        assert payload["u"] == "pm.smith"

    def test_tampered_signature_rejected(self):
        tok = _make_scoped_token(secret="s", view_relative_path="p", mode="view", user_id="u")
        body, sig = tok.rsplit(".", 1)
        bad = body + "." + ("0" * len(sig))
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _resolve_scoped_token(secret="s", token=bad)
        assert exc.value.status_code == 401

    def test_expired_rejected(self):
        tok = _make_scoped_token(
            secret="s", view_relative_path="p", mode="view", user_id="u",
            ttl_seconds=-1,   # already expired
        )
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _resolve_scoped_token(secret="s", token=tok)
        assert exc.value.status_code == 401


class TestWopiJwt:
    def test_roundtrip(self):
        jwt = _make_wopi_jwt(secret="s", view_relative_path="p")
        payload = _verify_wopi_jwt(secret="s", token=jwt)
        assert payload["path"] == "p"

    def test_wrong_secret_rejected(self):
        jwt = _make_wopi_jwt(secret="s1", view_relative_path="p")
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as exc:
            _verify_wopi_jwt(secret="s2", token=jwt)
        assert exc.value.status_code == 401


class TestOpenMode:
    def test_editor_extensions(self):
        assert _open_mode_for("report.xlsx") == "editor"
        assert _open_mode_for("Report.DOCX") == "editor"

    def test_native_view_extensions(self):
        assert _open_mode_for("report.pdf") == "native"
        assert _open_mode_for("readme.html") == "native"
        assert _open_mode_for("notes.txt") == "native"

    def test_download_fallback(self):
        assert _open_mode_for("archive.zip") == "download"
        assert _open_mode_for("image.png") == "download"


class TestFileIdEncoding:
    def test_encode_decode_roundtrip(self):
        p = "view/MMK/SM-S671U1/DRR/hw_reports/sub/report.pdf"
        enc = _encode_file_id(p)
        assert _decode_file_id(enc) == p
        # Encoded form must not contain slashes (URL-safe)
        assert "/" not in enc


# ---------------------------------------------------------------------------
# Route smoke tests through TestClient
# ---------------------------------------------------------------------------


class TestBrowseRoutes:
    async def test_landing_empty_scope(self, cfg):
        client = TestClient(build_app(cfg))
        r = client.get("/browse/MMK/SM-S671U1/DRR/")
        assert r.status_code == 200
        assert "No documents received yet" in r.text

    async def test_landing_lists_saved_tg(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"x", saved_by="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get("/browse/MMK/SM-S671U1/DRR/")
        assert r.status_code == 200
        assert "hw_reports" in r.text

    async def test_tg_files_page_lists_files_and_actions(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"x", saved_by="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get("/browse/MMK/SM-S671U1/DRR/tg/hw_reports/")
        assert r.status_code == 200
        # xlsx is editor mode -> Edit link should render
        assert "Edit</a>" in r.text
        # Download always available
        assert "Download</a>" in r.text


class TestViewDownloadRoutes:
    async def test_view_streams_current_bytes(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("readme.txt",),
            content=b"hello", saved_by="pm",
        )
        tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/readme.txt",
            mode="view", user_id="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get(f"/browse/view/{tok}")
        assert r.status_code == 200
        assert r.content == b"hello"
        assert r.headers["content-disposition"].startswith("inline;")

    async def test_download_sends_attachment(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("readme.txt",),
            content=b"hello", saved_by="pm",
        )
        tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/readme.txt",
            mode="download", user_id="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get(f"/browse/download/{tok}")
        assert r.status_code == 200
        assert r.content == b"hello"
        assert r.headers["content-disposition"].startswith("attachment;")

    async def test_view_bad_token_rejected(self, cfg):
        client = TestClient(build_app(cfg))
        r = client.get("/browse/view/deadbeef.bad")
        assert r.status_code == 401


class TestWopiEndpoints:
    async def test_check_file_info_returns_metadata(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"XXX", saved_by="pm",
        )
        view_path = "view/MMK/SM-S671U1/DRR/hw_reports/r.xlsx"
        file_id = _encode_file_id(view_path)
        jwt = _make_wopi_jwt(secret=cfg.wopi_jwt_secret, view_relative_path=view_path)
        client = TestClient(build_app(cfg))
        r = client.get(f"/wopi/files/{file_id}?access_token={jwt}")
        assert r.status_code == 200
        j = r.json()
        assert j["BaseFileName"] == "r.xlsx"
        assert j["Size"] == 3
        assert j["Version"] == "1"

    async def test_get_contents_returns_bytes(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"BYTES", saved_by="pm",
        )
        view_path = "view/MMK/SM-S671U1/DRR/hw_reports/r.xlsx"
        file_id = _encode_file_id(view_path)
        jwt = _make_wopi_jwt(secret=cfg.wopi_jwt_secret, view_relative_path=view_path)
        client = TestClient(build_app(cfg))
        r = client.get(f"/wopi/files/{file_id}/contents?access_token={jwt}")
        assert r.status_code == 200
        assert r.content == b"BYTES"

    async def test_put_contents_creates_new_version(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"v1", saved_by="pm",
        )
        view_path = "view/MMK/SM-S671U1/DRR/hw_reports/r.xlsx"
        file_id = _encode_file_id(view_path)
        jwt = _make_wopi_jwt(secret=cfg.wopi_jwt_secret, view_relative_path=view_path)
        client = TestClient(build_app(cfg))
        r = client.post(
            f"/wopi/files/{file_id}/contents?access_token={jwt}&user=pm.smith",
            content=b"v2 saved via WOPI",
        )
        assert r.status_code == 200
        # OnlyOffice callback sentinel: {"error": 0} on success, regardless
        # of raw-bytes vs JSON-callback protocol path.
        assert r.json() == {"error": 0}
        # Verify subsequent GET returns new bytes
        r2 = client.get(f"/wopi/files/{file_id}/contents?access_token={jwt}")
        assert r2.content == b"v2 saved via WOPI"

    async def test_wopi_without_jwt_rejected(self, cfg):
        client = TestClient(build_app(cfg))
        file_id = _encode_file_id("view/c/d/m/tg/x.xlsx")
        r = client.get(f"/wopi/files/{file_id}")
        assert r.status_code == 401

    async def test_callback_status_1_editing_no_save(self, cfg, monkeypatch):
        """Status 1 = editing started; must not create a new version."""
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"v1", saved_by="pm",
        )
        view_path = "view/MMK/SM-S671U1/DRR/hw_reports/r.xlsx"
        file_id = _encode_file_id(view_path)
        jwt = _make_wopi_jwt(secret=cfg.wopi_jwt_secret, view_relative_path=view_path)
        client = TestClient(build_app(cfg))
        r = client.post(
            f"/wopi/files/{file_id}/contents?access_token={jwt}&user=pm.smith",
            json={"status": 1, "users": ["pm.smith"]},
        )
        assert r.status_code == 200
        assert r.json() == {"error": 0}
        # v1 still current — no v2 written
        r2 = client.get(f"/wopi/files/{file_id}/contents?access_token={jwt}")
        assert r2.content == b"v1"

    async def test_callback_status_2_fetches_and_saves(self, cfg, monkeypatch):
        """Status 2 = ready to save; fetch from `url`, persist as new version."""
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"v1", saved_by="pm",
        )
        view_path = "view/MMK/SM-S671U1/DRR/hw_reports/r.xlsx"
        file_id = _encode_file_id(view_path)
        jwt = _make_wopi_jwt(secret=cfg.wopi_jwt_secret, view_relative_path=view_path)

        # Stub httpx.AsyncClient.get so we don't hit the real network.
        import httpx
        edited_bytes = b"edited-in-onlyoffice"
        class _StubResponse:
            content = edited_bytes
            def raise_for_status(self): pass
        class _StubClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url): return _StubResponse()
        monkeypatch.setattr(httpx, "AsyncClient", _StubClient)

        client = TestClient(build_app(cfg))
        r = client.post(
            f"/wopi/files/{file_id}/contents?access_token={jwt}&user=pm.smith",
            json={
                "status": 2,
                "url": "http://onlyoffice-ds/cache/files/abc/output.xlsx",
                "users": ["pm.smith"],
                "actions": [{"type": 0, "userid": "pm.smith"}],
            },
        )
        assert r.status_code == 200
        assert r.json() == {"error": 0}
        r2 = client.get(f"/wopi/files/{file_id}/contents?access_token={jwt}")
        assert r2.content == edited_bytes

    async def test_callback_status_2_fetch_failure_still_acks(self, cfg, monkeypatch):
        """Download failure must still return {"error":0} — else OnlyOffice
        retries forever. Failure is logged + audited via ops path."""
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"v1", saved_by="pm",
        )
        view_path = "view/MMK/SM-S671U1/DRR/hw_reports/r.xlsx"
        file_id = _encode_file_id(view_path)
        jwt = _make_wopi_jwt(secret=cfg.wopi_jwt_secret, view_relative_path=view_path)

        import httpx
        class _BoomClient:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def get(self, url):
                raise httpx.ConnectError("cannot reach onlyoffice-ds")
        monkeypatch.setattr(httpx, "AsyncClient", _BoomClient)

        client = TestClient(build_app(cfg))
        r = client.post(
            f"/wopi/files/{file_id}/contents?access_token={jwt}&user=pm.smith",
            json={"status": 2, "url": "http://unreachable/x", "users": ["pm.smith"]},
        )
        assert r.status_code == 200
        assert r.json() == {"error": 0}
        # v1 still current — save didn't happen
        r2 = client.get(f"/wopi/files/{file_id}/contents?access_token={jwt}")
        assert r2.content == b"v1"


class TestEditorEmbed:
    async def test_edit_page_renders_iframe_wiring(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"x", saved_by="pm",
        )
        tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/r.xlsx",
            mode="edit", user_id="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get(f"/browse/edit/{tok}")
        assert r.status_code == 200
        # Editor page must load the OnlyOffice API
        assert "api/documents/api.js" in r.text
        # Must include HILDA WOPI URL
        assert "/wopi/files/" in r.text

    async def test_edit_page_rejects_non_editable_extension(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("readme.txt",),
            content=b"x", saved_by="pm",
        )
        tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/readme.txt",
            mode="edit", user_id="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get(f"/browse/edit/{tok}")
        assert r.status_code == 415

    async def test_edit_page_503_when_not_configured(self):
        cfg_empty = DashboardConfig(
            mock_auth=True, ph1_minimal=False,
            wopi_jwt_secret="", onlyoffice_public_url="",
        )
        # Even with empty config, token minted with empty secret still verifies
        tok = _make_scoped_token(
            secret="", view_relative_path="view/c/d/m/tg/r.xlsx",
            mode="edit", user_id="pm",
        )
        client = TestClient(build_app(cfg_empty))
        r = client.get(f"/browse/edit/{tok}")
        assert r.status_code == 503
