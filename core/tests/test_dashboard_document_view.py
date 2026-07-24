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

    def test_txt_is_editor_2026_07_24(self):
        """.txt moved to editor: OnlyOffice Word editor supports plaintext."""
        assert _open_mode_for("notes.txt") == "editor"
        assert _open_mode_for("README.TXT") == "editor"

    def test_native_view_extensions(self):
        assert _open_mode_for("report.pdf") == "native"
        assert _open_mode_for("readme.html") == "native"
        assert _open_mode_for("data.csv") == "native"
        assert _open_mode_for("notes.md") == "native"

    def test_images_are_native_2026_07_24(self):
        for name in ("photo.png", "shot.JPG", "pic.jpeg", "anim.gif",
                     "old.bmp", "modern.webp", "logo.svg"):
            assert _open_mode_for(name) == "native", f"failed for {name}"

    def test_msg_and_db_are_download_only_2026_07_24(self):
        """Outlook .msg and SQLite .db files are download-only per architect."""
        assert _open_mode_for("message.msg") == "download"
        assert _open_mode_for("Cache.MSG") == "download"
        assert _open_mode_for("state.db") == "download"

    def test_legacy_binary_office_is_download_only_2026_07_24(self):
        """Legacy binary .doc/.xls/.ppt formats never open in the editor per
        architect 2026-07-24: they're always NASCA-wrapped by corp email path
        and OnlyOffice CE has poor legacy-binary conversion support anyway."""
        for name in ("report.doc", "spec.DOC", "data.xls", "budget.XLS",
                     "slides.ppt", "Deck.PPT"):
            assert _open_mode_for(name) == "download", f"{name} should be download-only"

    def test_download_fallback(self):
        assert _open_mode_for("archive.zip") == "download"
        assert _open_mode_for("no_extension") == "download"


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


class TestDrmWrappedFiles:
    """D-152: NASCA-wrapped files must be sniffed at save time, flagged in
    listings, and rejected by /browse/edit even when the caller has a valid
    edit token (UI gate is not the only gate)."""

    _NASCA_BYTES = b"<## NASCA-WRAPPED-DOC\x00\x01\x02fake-encrypted-payload"

    async def test_save_sniffs_nasca_magic(self, cfg):
        from core.src.storage import get_current_version
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("wrapped.docx",),
            content=self._NASCA_BYTES, saved_by="router", source="router",
        )
        row = await get_current_version("view/MMK/SM-S671U1/DRR/hw_reports/wrapped.docx")
        assert row is not None
        assert row.is_drm_wrapped is True

    async def test_save_clean_bytes_flag_stays_false(self, cfg):
        from core.src.storage import get_current_version
        # OLE compound-doc magic — real legacy .doc
        clean_doc = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 100
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("clean.doc",),
            content=clean_doc, saved_by="router", source="router",
        )
        row = await get_current_version("view/MMK/SM-S671U1/DRR/hw_reports/clean.doc")
        assert row is not None
        assert row.is_drm_wrapped is False

    async def test_browse_listing_shows_drm_badge_and_gates_edit(self, cfg):
        # One wrapped .doc + one clean .xlsx side-by-side
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("wrapped.docx",),
            content=self._NASCA_BYTES, saved_by="router", source="router",
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("clean.xlsx",),
            content=b"PK\x03\x04clean-ooxml-payload", saved_by="router", source="router",
        )
        client = TestClient(build_app(cfg))
        r = client.get("/browse/MMK/SM-S671U1/DRR/tg/hw_reports/")
        assert r.status_code == 200
        # Wrapped file: DRM badge visible
        assert "🔒 DRM" in r.text
        # Clean xlsx still gets an Edit link
        assert "Edit</a>" in r.text
        # Both get Download
        assert r.text.count("Download</a>") >= 2

    async def test_browse_edit_returns_415_for_drm_wrapped(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("wrapped.docx",),
            content=self._NASCA_BYTES, saved_by="router", source="router",
        )
        edit_tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/wrapped.docx",
            mode="edit", user_id="tpm",
        )
        client = TestClient(build_app(cfg))
        r = client.get(f"/browse/edit/{edit_tok}")
        assert r.status_code == 415
        assert "DRM-protected" in r.text
        # 415 page should carry a working Download link
        assert "/browse/download/" in r.text


class TestVersionsAndHistoryViews:
    """D-150 Chunk 7 refinements 2026-07-24: Filename column rename, clickable
    Versions cell, /browse/versions/{token} + /browse/history/{token} routes,
    prior-version download via `v` claim, By-column pretty mapping, and ET
    timezone rendering."""

    async def test_versions_cell_is_link_when_count_gt_1(self, cfg):
        # Two saves -> version_count=2 on the current row
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"v1-bytes", saved_by="pm",
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"v2-bytes", saved_by="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get("/browse/MMK/SM-S671U1/DRR/tg/hw_reports/")
        assert r.status_code == 200
        assert "/browse/versions/" in r.text
        assert "History" in r.text  # column present

    async def test_versions_cell_is_plaintext_when_count_eq_1(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("only.xlsx",),
            content=b"only-v1", saved_by="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get("/browse/MMK/SM-S671U1/DRR/tg/hw_reports/")
        assert r.status_code == 200
        # Not linked because count==1
        assert "/browse/versions/" not in r.text

    async def test_by_mapping_auto_to_owner_and_unknown_to_TPM(self, cfg):
        # router-driven save (saved_by="auto")
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("router.xlsx",),
            content=b"auto-payload", saved_by="auto", source="router",
        )
        # editor save-back (saved_by="unknown")
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("editor.xlsx",),
            content=b"editor-payload", saved_by="unknown", source="editor",
        )
        client = TestClient(build_app(cfg))
        r = client.get("/browse/MMK/SM-S671U1/DRR/tg/hw_reports/")
        assert r.status_code == 200
        assert ">owner<" in r.text or "owner\n" in r.text or " owner " in r.text
        assert ">TPM<" in r.text or "TPM\n" in r.text or " TPM " in r.text
        # And the raw labels should NOT leak
        assert ">auto<" not in r.text
        assert ">unknown<" not in r.text

    async def test_versions_view_renders_with_edit_on_current_download_on_prior(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"v1-bytes", saved_by="pm",
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"v2-bytes", saved_by="pm",
        )
        tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/r.xlsx",
            mode="versions", user_id="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get(f"/browse/versions/{tok}")
        assert r.status_code == 200
        # Two rows rendered
        assert "v1" in r.text and "v2" in r.text
        assert "current" in r.text
        # At least one Edit link (on the current row)
        assert "/browse/edit/" in r.text
        # Download link on every row (2 downloads total)
        assert r.text.count("/browse/download/") >= 2

    async def test_versions_view_rejects_wrong_mode_token(self, cfg):
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=b"v1-bytes", saved_by="pm",
        )
        # An "edit" mode token cannot open /browse/versions
        wrong_tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/r.xlsx",
            mode="edit", user_id="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get(f"/browse/versions/{wrong_tok}")
        assert r.status_code == 403

    async def test_prior_version_download_streams_vN_sibling_bytes(self, cfg):
        v1 = b"v1-original-content"
        v2 = b"v2-newer-content"
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=v1, saved_by="pm",
        )
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("r.xlsx",),
            content=v2, saved_by="pm",
        )
        # Token with v=1 must stream v1 bytes (from .v1 sibling), not current
        tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/r.xlsx",
            mode="download", user_id="pm",
            version_num=1,
        )
        client = TestClient(build_app(cfg))
        r = client.get(f"/browse/download/{tok}")
        assert r.status_code == 200
        assert r.content == v1
        # And a token WITHOUT v still streams current (v2)
        cur_tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/r.xlsx",
            mode="download", user_id="pm",
        )
        r2 = client.get(f"/browse/download/{cur_tok}")
        assert r2.status_code == 200
        assert r2.content == v2

    async def test_history_view_lists_events(self, cfg):
        # The dashboard's _audit() short-circuits when task_deps isn't wired
        # on the test app, so writing routes through TestClient doesn't
        # generate audit rows. Insert them directly via the same writer used
        # in production so the query path is exercised end-to-end.
        import json as _json
        import uuid as _uuid
        from datetime import datetime as _dt, timezone as _tz
        from core.src.storage.db import CommunicationLogTable, session_scope
        from core.src.storage.models import Channel, Direction

        view_path = "view/MMK/SM-S671U1/DRR/hw_reports/readme.txt"
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("readme.txt",),
            content=b"hello", saved_by="pm",
        )

        async with session_scope() as s:
            for action_type in ("document_viewed", "document_downloaded"):
                s.add(CommunicationLogTable(
                    log_id=str(_uuid.uuid4()),
                    channel=Channel.SHAREPOINT,
                    direction=Direction.OUTBOUND,
                    timestamp=_dt.now(_tz.utc),
                    delivery_item_id=None,
                    device_id=None,
                    sender="pm",
                    recipients=None,
                    subject=None,
                    summary=_json.dumps({
                        "attribution": {"trigger_source": "dashboard:document_view",
                                        "correlation_id": view_path,
                                        "modified_by": "pm"},
                        "details":     {"view_relative_path": view_path,
                                        "user_id": "pm"},
                    }),
                    external_message_id=view_path,
                    credential_id=None,
                    action_type=action_type,
                    attachments=[],
                ))
            await s.commit()

        hist_tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret, view_relative_path=view_path,
            mode="history", user_id="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get(f"/browse/history/{hist_tok}")
        assert r.status_code == 200
        assert "Opened (view)" in r.text
        assert "Downloaded" in r.text

    async def test_route_audit_wired_end_to_end(self, cfg):
        """HIST-1 regression: verify build_app() wires task_deps.audit so that
        real /browse/view + /browse/download hits actually create CommunicationLog
        rows that the /browse/history view can then render.

        Prior to HIST-1, _audit() short-circuited (app.state.task_deps was None)
        and History always rendered "No events recorded" no matter how many
        times the user opened/downloaded a file.
        """
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("audit-me.txt",),
            content=b"hello", saved_by="pm",
        )
        view_path = "view/MMK/SM-S671U1/DRR/hw_reports/audit-me.txt"
        client = TestClient(build_app(cfg))

        # Trigger view + download through the actual routes -- if audit isn't
        # wired, no CommunicationLog rows are produced and history is empty.
        view_tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret, view_relative_path=view_path,
            mode="view", user_id="pm",
        )
        dl_tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret, view_relative_path=view_path,
            mode="download", user_id="pm",
        )
        assert client.get(f"/browse/view/{view_tok}").status_code == 200
        assert client.get(f"/browse/download/{dl_tok}").status_code == 200

        # Now the history page should reflect both events -- proof that
        # task_deps.audit landed rows and list_document_events reads them back.
        hist_tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret, view_relative_path=view_path,
            mode="history", user_id="pm",
        )
        r = client.get(f"/browse/history/{hist_tok}")
        assert r.status_code == 200
        assert "Opened (view)" in r.text, "view audit row did not land"
        assert "Downloaded" in r.text, "download audit row did not land"
        # And "no events" message MUST be absent
        assert "No events recorded" not in r.text

    async def test_history_view_rejects_wrong_mode_token(self, cfg):
        wrong_tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/r.xlsx",
            mode="download", user_id="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get(f"/browse/history/{wrong_tok}")
        assert r.status_code == 403


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

    async def test_view_image_streams_inline_with_image_content_type(self, cfg):
        """.png (added to native-view set 2026-07-24) must stream inline with
        image/png content-type so the browser can preview it."""
        # Fake 1-pixel PNG magic bytes; contents don't need to be a real image
        # for the streaming path — we're only checking headers + bytes echo.
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("shot.png",),
            content=png_bytes, saved_by="pm",
        )
        tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/shot.png",
            mode="view", user_id="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get(f"/browse/view/{tok}")
        assert r.status_code == 200
        assert r.content == png_bytes
        assert r.headers["content-type"].startswith("image/png")
        assert r.headers["content-disposition"].startswith("inline;")

    async def test_txt_edit_page_reaches_editor_render(self, cfg):
        """.txt moved to editor 2026-07-24: /browse/edit must accept it (not
        415), pass through DRM sniff, and render the OnlyOffice iframe HTML."""
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("notes.txt",),
            content=b"plain text content",  # clean, not DRM-wrapped
            saved_by="pm",
        )
        tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/notes.txt",
            mode="edit", user_id="pm",
        )
        client = TestClient(build_app(cfg))
        r = client.get(f"/browse/edit/{tok}")
        assert r.status_code == 200
        # Editor iframe should be present; documentType is "word" for .txt
        assert "DocsAPI.DocEditor" in r.text
        assert '"documentType": "word"' in r.text or "'documentType': 'word'" in r.text

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
        # 2026-07-24: .txt moved to editor mode, so this uses .pdf (native) as
        # the canonical "not editable" example. .msg / .db / .zip would work
        # equally well — /browse/edit rejects anything not in _EDITOR_EXTENSIONS.
        await save_view_document(
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
            tg_name="hw_reports", relative_parts=("report.pdf",),
            content=b"%PDF-1.4 fake", saved_by="pm",
        )
        tok = _make_scoped_token(
            secret=cfg.wopi_jwt_secret,
            view_relative_path="view/MMK/SM-S671U1/DRR/hw_reports/report.pdf",
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
