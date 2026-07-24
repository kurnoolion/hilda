"""D-150 HILDA-side documents view — FastAPI routes.

Chunk 4 (browse UI):
  * GET  /browse/{customer_id}/{device_id}/{milestone_id}/
        Landing page — list tg_names as directories.
  * GET  /browse/{customer_id}/{device_id}/{milestone_id}/tg/{tg_name}/
        Flat file list under a tg_name (per architect Q4 lock).

Chunk 5 (WOPI Host + editor embed):
  * GET  /browse/edit/{token}
        Loads an HTML page that embeds the OnlyOffice editor iframe with a
        signed WOPI URL. `token` is a short-lived scoped token identifying
        the file to open. Auth: X-Authenticated-User (same as dashboard).
  * GET  /wopi/files/{file_id}
        CheckFileInfo per WOPI protocol. Called BY OnlyOffice server (not
        browser); JWT-signed. Returns metadata JSON.
  * GET  /wopi/files/{file_id}/contents
        Returns raw file bytes. Called by OnlyOffice server; JWT-signed.
  * POST /wopi/files/{file_id}/contents
        Save handler. Body = new file bytes. Creates a new version_num row
        via save_view_document. Called by OnlyOffice server on save; JWT-signed.

Chunk 6 (view-only PDF/HTML + download):
  * GET  /browse/download/{token}
        Direct download link — streams file bytes with Content-Disposition
        attachment. Same tokenization as edit.
  * GET  /browse/view/{token}
        Native browser view (inline Content-Disposition). Used for PDF/HTML
        rendering (no OnlyOffice needed).

Chunk 7 (audit): every open/edit/save/download event logs to
CommunicationLog with the D-150 action_type set:
  * document_viewed          (file opened via /browse/view or /browse/edit view mode)
  * document_edit_opened     (file opened in edit mode via /browse/edit)
  * document_saved           (OnlyOffice PUT to /wopi/files/*/contents)
  * document_downloaded      (file streamed via /browse/download)
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from pathlib import PurePosixPath
from typing import Any

from fastapi import (
    APIRouter, Body, Depends, FastAPI, Header, HTTPException, Request, status,
)
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse

__all__ = ["register_document_view_routes"]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token: HMAC-signed scope + path bundle for /browse/edit + /browse/download
# ---------------------------------------------------------------------------


_TOKEN_TTL_SECONDS = 30 * 60   # 30 min


def _make_scoped_token(*, secret: str, view_relative_path: str, mode: str,
                       user_id: str, ttl_seconds: int = _TOKEN_TTL_SECONDS) -> str:
    """URL-safe token containing view_relative_path + mode + user_id + expires_at.
    HMAC-SHA256 signed with dashboard.wopi_jwt_secret so tampering is detected."""
    payload = {
        "p":  view_relative_path,
        "m":  mode,             # "view" | "edit" | "download"
        "u":  user_id,
        "x":  int(time.time()) + ttl_seconds,
    }
    body = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    sig = _hmac_hex(secret, body)
    return f"{body}.{sig}"


def _resolve_scoped_token(*, secret: str, token: str) -> dict[str, Any]:
    """Verify HMAC + expiry. Raises HTTPException(401) on any failure."""
    try:
        body, sig = token.rsplit(".", 1)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="malformed token") from exc
    if not hmac.compare_digest(sig, _hmac_hex(secret, body)):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        pad = "=" * (-len(body) % 4)
        payload = json.loads(base64.urlsafe_b64decode(body + pad).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="bad payload") from exc
    if payload.get("x", 0) < int(time.time()):
        raise HTTPException(status_code=401, detail="token expired")
    return payload


def _hmac_hex(secret: str, body: str) -> str:
    return hmac.new(
        (secret or "unset-secret").encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]


# ---------------------------------------------------------------------------
# WOPI JWT — for HILDA <-> OnlyOffice back-channel authentication
# ---------------------------------------------------------------------------


def _sign_jwt(*, secret: str, payload: dict[str, Any]) -> str:
    """Generic HS256 JWT signer over an arbitrary payload dict."""
    header = {"alg": "HS256", "typ": "JWT"}
    h = base64.urlsafe_b64encode(
        json.dumps(header, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    p = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    sig_b = hmac.new(
        (secret or "unset-secret").encode("utf-8"),
        f"{h}.{p}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    sig = base64.urlsafe_b64encode(sig_b).rstrip(b"=").decode("ascii")
    return f"{h}.{p}.{sig}"


def _make_wopi_jwt(*, secret: str, view_relative_path: str, exp_seconds: int = 3600) -> str:
    """WOPI back-channel token (OnlyOffice server → HILDA WOPI endpoints).
    HILDA verifies HMAC on inbound WOPI calls. Payload includes exp for
    freshness."""
    header = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "path": view_relative_path,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_seconds,
    }
    h = base64.urlsafe_b64encode(
        json.dumps(header, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    p = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).rstrip(b"=").decode("ascii")
    sig_b = hmac.new(
        (secret or "unset-secret").encode("utf-8"),
        f"{h}.{p}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    sig = base64.urlsafe_b64encode(sig_b).rstrip(b"=").decode("ascii")
    return f"{h}.{p}.{sig}"


def _verify_wopi_jwt(*, secret: str, token: str) -> dict[str, Any]:
    """Verify inbound WOPI JWT from OnlyOffice. Raises HTTPException(401)."""
    try:
        h, p, sig = token.split(".")
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="malformed WOPI JWT") from exc
    expected = hmac.new(
        (secret or "unset-secret").encode("utf-8"),
        f"{h}.{p}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    provided_sig_bytes = _urlsafe_b64_decode(sig)
    if not hmac.compare_digest(expected, provided_sig_bytes):
        raise HTTPException(status_code=401, detail="bad WOPI JWT signature")
    try:
        payload = json.loads(_urlsafe_b64_decode(p).decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=401, detail="bad WOPI JWT payload") from exc
    if payload.get("exp", 0) < int(time.time()):
        raise HTTPException(status_code=401, detail="WOPI JWT expired")
    return payload


def _urlsafe_b64_decode(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------------------
# File-type dispatch
# ---------------------------------------------------------------------------


_EDITOR_EXTENSIONS = {".docx", ".xlsx", ".xlsm", ".doc", ".xls", ".pptx", ".ppt"}
_NATIVE_VIEW_EXTENSIONS = {".pdf", ".html", ".htm", ".txt", ".csv", ".md"}
_DOWNLOAD_ONLY_EXTENSIONS: set[str] = set()  # everything else -> download


def _open_mode_for(filename: str) -> str:
    """Return 'editor' | 'native' | 'download' based on extension."""
    ext = _ext(filename)
    if ext in _EDITOR_EXTENSIONS:
        return "editor"
    if ext in _NATIVE_VIEW_EXTENSIONS:
        return "native"
    return "download"


def _ext(filename: str) -> str:
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


def _wopi_src_to_key(wopi_src: str) -> str:
    """OnlyOffice `document.key` — unique per ~1-min edit window, ≤128 chars.

    OnlyOffice DocumentServer spec: `document.key` must be ≤128 characters,
    charset [0-9a-zA-Z._-]. Corp deploy 2026-07-23 was silently failing
    (`"Other error"` / "file cannot be accessed") because the naïve key
    `<wopi_src>_<bucket>` came out at ~170 chars (base64 file_id alone is
    ~130 chars). OnlyOffice rejected without a clear log line.

    Bucketing rationale: same key across attempts causes OnlyOffice to reuse
    cached document state — including cached FAILED state from prior broken
    configs. A 1-min time bucket gives each edit session a fresh cache slot
    while still letting concurrent editors within the same minute share.

    Format: `d{16-hex-of-sha256(wopi_src)}_{minute_bucket}` — ~30 chars,
    stable across processes, unique per file per minute.
    """
    minute_bucket = int(time.time()) // 60
    digest = hashlib.sha256(wopi_src.encode("utf-8")).hexdigest()[:16]
    return f"d{digest}_{minute_bucket}"


def _mime_for(filename: str) -> str:
    ext = _ext(filename)
    return {
        ".pdf":  "application/pdf",
        ".html": "text/html",
        ".htm":  "text/html",
        ".txt":  "text/plain",
        ".csv":  "text/csv",
        ".md":   "text/markdown",
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif":  "image/gif",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    }.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


def _audit(request: Request, action_type: str, view_relative_path: str, user_id: str,
           details: dict[str, Any] | None = None) -> None:
    """Log to CommunicationLog. Best-effort; failure is logged locally."""
    deps = getattr(request.app.state, "task_deps", None)
    if deps is None or getattr(deps, "audit", None) is None:
        return
    d = {"view_relative_path": view_relative_path, "user_id": user_id}
    if details:
        d.update(details)
    try:
        deps.audit.write_communication_log(
            action_type=action_type,
            delivery_item_id=None,
            attribution={
                "trigger_source": "dashboard:document_view",
                "correlation_id": view_relative_path,
                "modified_by":    user_id,
            },
            details=d,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("document_view audit failed action=%s: %s", action_type, str(exc)[:120])


# ---------------------------------------------------------------------------
# Route registration
# ---------------------------------------------------------------------------


def register_document_view_routes(app: FastAPI, cfg, templates) -> None:
    """Wire the /browse/* + /wopi/* routes onto an existing FastAPI app.

    `cfg` is the dashboard config (has wopi_jwt_secret + onlyoffice_public_url +
    onlyoffice_internal_url). `templates` is the Jinja2Templates instance
    already configured by the caller.
    """
    from .auth import require_authenticated_principal

    def _auth(request: Request):
        return require_authenticated_principal(request, cfg)

    # ----- Chunk 4: browse landing + folder listing ------------------------

    @app.get(
        "/browse/{customer_id}/{device_id}/{milestone_id}/",
        response_class=HTMLResponse,
    )
    async def browse_landing(
        customer_id: str, device_id: str, milestone_id: str,
        request: Request,
        principal=Depends(_auth),
    ):
        from core.src.storage import list_tg_names_for_scope
        entries = await list_tg_names_for_scope(
            customer_id=customer_id, device_id=device_id, milestone_id=milestone_id,
        )
        return templates.TemplateResponse(
            request,
            "view_tree_landing.html",
            {
                "customer_id":  customer_id,
                "device_id":    device_id,
                "milestone_id": milestone_id,
                "tg_entries":   entries,
            },
        )

    @app.get(
        "/browse/{customer_id}/{device_id}/{milestone_id}/tg/{tg_name}/",
        response_class=HTMLResponse,
    )
    async def browse_tg_files(
        customer_id: str, device_id: str, milestone_id: str, tg_name: str,
        request: Request,
        principal=Depends(_auth),
    ):
        from core.src.storage import list_files_in_tg
        files = await list_files_in_tg(
            customer_id=customer_id, device_id=device_id,
            milestone_id=milestone_id, tg_name=tg_name,
        )
        # For each file, compute open-mode + a scoped token for that mode
        secret = cfg.wopi_jwt_secret
        user_id = getattr(principal, "corp_id", None) or getattr(principal, "user_id", "unknown")
        rendered = []
        for f in files:
            mode = _open_mode_for(f.filename)
            # D-152: NASCA-wrapped files cannot be edited in-browser (OnlyOffice
            # has no NASCA agent). Downgrade Edit → Download for wrapped files
            # so the token itself grants only what the UI will show. Also emit
            # a separate download_token so the template can render a Download
            # link alongside a live Edit / native View when applicable.
            effective_mode = "download" if f.is_drm_wrapped else mode
            tok_mode = ("edit" if effective_mode == "editor"
                        else "view" if effective_mode == "native"
                        else "download")
            tok = _make_scoped_token(
                secret=secret, view_relative_path=f.view_relative_path,
                mode=tok_mode, user_id=user_id,
            )
            download_tok = _make_scoped_token(
                secret=secret, view_relative_path=f.view_relative_path,
                mode="download", user_id=user_id,
            )
            rendered.append({
                "filename":            f.filename,
                "view_relative_path":  f.view_relative_path,
                "size_bytes":          f.size_bytes,
                "version_count":       f.version_count,
                "last_saved_at":       f.last_saved_at,
                "last_saved_by":       f.last_saved_by,
                "open_mode":           effective_mode,
                "open_token":          tok,
                "download_token":      download_tok,
                "is_drm_wrapped":      f.is_drm_wrapped,
            })
        return templates.TemplateResponse(
            request,
            "view_tree_tg.html",
            {
                "customer_id":  customer_id,
                "device_id":    device_id,
                "milestone_id": milestone_id,
                "tg_name":      tg_name,
                "files":        rendered,
            },
        )

    # ----- Chunk 6: view + download (native browser) -----------------------

    @app.get("/browse/view/{token}")
    async def browse_view(token: str, request: Request):
        payload = _resolve_scoped_token(secret=cfg.wopi_jwt_secret, token=token)
        if payload["m"] not in ("view", "edit"):
            raise HTTPException(status_code=403, detail="token not a view/edit token")
        view_relative_path = payload["p"]
        user_id = payload["u"]
        _audit(request, "document_viewed", view_relative_path, user_id)
        from core.src.storage import read_current_version_bytes
        content = await read_current_version_bytes(view_relative_path)
        filename = PurePosixPath(view_relative_path).name
        return StreamingResponse(
            iter((content,)),
            media_type=_mime_for(filename),
            headers={"Content-Disposition": f'inline; filename="{filename}"'},
        )

    @app.get("/browse/download/{token}")
    async def browse_download(token: str, request: Request):
        payload = _resolve_scoped_token(secret=cfg.wopi_jwt_secret, token=token)
        if payload["m"] not in ("download", "view", "edit"):
            raise HTTPException(status_code=403, detail="token not a download token")
        view_relative_path = payload["p"]
        user_id = payload["u"]
        _audit(request, "document_downloaded", view_relative_path, user_id)
        from core.src.storage import read_current_version_bytes
        content = await read_current_version_bytes(view_relative_path)
        filename = PurePosixPath(view_relative_path).name
        return StreamingResponse(
            iter((content,)),
            media_type=_mime_for(filename),
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    # ----- Chunk 5: OnlyOffice edit embed -----

    @app.get("/browse/edit/{token}", response_class=HTMLResponse)
    async def browse_edit(token: str, request: Request):
        payload = _resolve_scoped_token(secret=cfg.wopi_jwt_secret, token=token)
        if payload["m"] != "edit":
            raise HTTPException(status_code=403, detail="token not an edit token")
        view_relative_path = payload["p"]
        user_id = payload["u"]

        filename = PurePosixPath(view_relative_path).name
        if _open_mode_for(filename) != "editor":
            raise HTTPException(status_code=415, detail="file type not editable")

        # Config check runs before disk I/O so misconfigured deploys short-circuit
        # cleanly without needing the file to exist (matches historical behavior).
        if not cfg.onlyoffice_public_url or not cfg.wopi_jwt_secret:
            return HTMLResponse(
                "<html><body><h1>OnlyOffice not configured</h1>"
                "<p>Set dashboard.onlyoffice_public_url + wopi_jwt_secret in "
                "config/dashboard.json to enable in-browser editing.</p></body></html>",
                status_code=503,
            )

        # D-152 belt-and-suspenders: even if the UI hid the Edit link, a caller
        # can still hit this URL directly (bookmarked token, curl, etc). Sniff
        # the first 4 bytes of the current version — NASCA-wrapped files start
        # with `<## ` (0x3c 0x23 0x23 0x20) and OnlyOffice cannot decrypt them.
        # Fail fast with 415 + a Download link rather than letting OnlyOffice
        # spin on a corrupt-looking payload and surface "Unknown error".
        from core.src.storage import read_current_version_bytes
        head = (await read_current_version_bytes(view_relative_path))[:4]
        if head == b"<## ":
            _audit(request, "document_edit_blocked_drm", view_relative_path, user_id)
            dl_tok = _make_scoped_token(
                secret=cfg.wopi_jwt_secret, view_relative_path=view_relative_path,
                mode="download", user_id=user_id,
            )
            return HTMLResponse(
                "<html><body>"
                "<h1>🔒 DRM-protected document</h1>"
                "<p>This file was wrapped by corp Information Rights Management "
                "(NASCA) in transit. In-browser editing is not available for "
                "wrapped files.</p>"
                f"<p><a href=\"/browse/download/{dl_tok}\">Download</a> and open "
                "in a NASCA-aware Office client on your workstation to edit.</p>"
                "</body></html>",
                status_code=415,
            )

        _audit(request, "document_edit_opened", view_relative_path, user_id)

        # WOPI src URL — used by OnlyOffice DOCUMENT SERVER (not browser) to
        # fetch file bytes + POST saves back. OnlyOffice runs inside its own
        # podman container on the same shared network as hilda-api. Container
        # DNS lets OnlyOffice reach `hilda-api:8080` directly, bypassing
        # nginx and avoiding rootless-podman hairpin NAT (container -> host
        # external IP: 105.52.91.33:8443 typically unreachable from inside).
        #
        # 2026-07-23 architect corp deploy: "Download failed" surfaced when
        # OnlyOffice tried to reach reverse_proxy_origin (105.52.91.33:8443)
        # from inside its container -> rootless podman doesn't loop back to
        # the host's external IP. Switch to internal container-network URL.
        hilda_internal = "http://hilda-api:8080"
        wopi_src = f"{hilda_internal}/wopi/files/{_encode_file_id(view_relative_path)}"
        # WOPI back-channel access token (OnlyOffice server → HILDA WOPI GETs/POST)
        wopi_access_token = _make_wopi_jwt(
            secret=cfg.wopi_jwt_secret, view_relative_path=view_relative_path,
        )

        # Build the full DocEditor config server-side per OnlyOffice contract:
        # OnlyOffice validates `token` as a JWT signing the ENTIRE config
        # object (document + editorConfig + documentType + ...). If token
        # payload differs from the actual config, OnlyOffice rejects with
        # "The document security token is not correctly formed".
        ext = _ext(filename).lstrip(".")
        if ext in ("docx", "doc", "odt"):
            document_type = "word"
        elif ext in ("xlsx", "xls", "xlsm", "ods"):
            document_type = "cell"
        elif ext in ("pptx", "ppt", "odp"):
            document_type = "slide"
        else:
            document_type = "word"

        docs_config: dict[str, Any] = {
            "documentType": document_type,
            "document": {
                "fileType": ext or "docx",
                "key":      _wopi_src_to_key(wopi_src),
                "title":    filename,
                "url":      f"{wopi_src}/contents?access_token={wopi_access_token}",
            },
            "editorConfig": {
                "mode": "edit",
                "user": {"id": user_id, "name": user_id},
                "callbackUrl": f"{wopi_src}/contents?access_token={wopi_access_token}&user={user_id}",
            },
        }
        # Sign the config as a JWT; the resulting token is what OnlyOffice
        # verifies against the DocEditor config object at client-side init.
        docs_config_token = _sign_jwt(secret=cfg.wopi_jwt_secret, payload=docs_config)

        return templates.TemplateResponse(
            request,
            "view_tree_editor.html",
            {
                "onlyoffice_public_url": cfg.onlyoffice_public_url.rstrip("/"),
                "filename":              filename,
                "docs_config_json":      json.dumps(docs_config),
                "docs_config_token":     docs_config_token,
            },
        )


    # ----- Chunk 5: WOPI Host endpoints -----

    @app.get("/wopi/files/{file_id}")
    async def wopi_check_file_info(file_id: str, request: Request):
        """CheckFileInfo — WOPI protocol metadata GET."""
        view_relative_path = _decode_file_id(file_id)
        _verify_wopi_from_headers(request, cfg.wopi_jwt_secret)
        from core.src.storage import get_current_version
        row = await get_current_version(view_relative_path)
        if row is None:
            raise HTTPException(status_code=404, detail="no such file")
        return JSONResponse({
            "BaseFileName":     row.filename,
            "Size":             row.size_bytes,
            "OwnerId":          row.saved_by,
            "UserId":           row.saved_by,
            "UserFriendlyName": row.saved_by,
            "Version":          str(row.version_num),
            "SupportsUpdate":   True,
            "UserCanWrite":     True,
            "UserCanRename":    False,
            "ReadOnly":         False,
            "SHA256":           row.sha256,
        })

    @app.get("/wopi/files/{file_id}/contents")
    async def wopi_get_file_contents(file_id: str, request: Request):
        view_relative_path = _decode_file_id(file_id)
        _verify_wopi_from_headers(request, cfg.wopi_jwt_secret)
        from core.src.storage import read_current_version_bytes
        content = await read_current_version_bytes(view_relative_path)
        return StreamingResponse(iter((content,)),
                                 media_type="application/octet-stream")

    @app.post("/wopi/files/{file_id}/contents")
    async def wopi_put_file_contents(file_id: str, request: Request):
        """OnlyOffice save callback.

        Contract note (corp deploy 2026-07-23): OnlyOffice's DocEditor
        `editorConfig.callbackUrl` speaks OnlyOffice's OWN callback protocol,
        not raw-WOPI PutFile. The body is JSON:

            {"key": "...", "status": <int>, "url": "<download-url>",
             "users": [...], "actions": [...], "token": "<jwt>"}

        Status codes (OnlyOffice DocumentServer 8.x):
          1 = editing started        -> respond {"error":0}, no save
          2 = ready to save          -> download from "url", save bytes, {"error":0}
          3 = save error             -> log, {"error":0}
          4 = no changes to save     -> {"error":0}
          6 = force-save requested   -> same as 2
          7 = force-save error       -> log, {"error":0}

        Response MUST be exactly `{"error": 0}` on success; anything else
        (including WOPI-style {LastModifiedTime,Version}) is treated by
        OnlyOffice as save failure -> user sees "document could not be saved".
        """
        view_relative_path = _decode_file_id(file_id)
        _verify_wopi_from_headers(request, cfg.wopi_jwt_secret)

        raw_body = await request.body()
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            _log.warning(
                "wopi_put_file_contents: body is not JSON (len=%d) for %s; "
                "falling back to raw-bytes WOPI PutFile path",
                len(raw_body), view_relative_path,
            )
            payload = None

        status_code = None
        download_url = None
        if isinstance(payload, dict):
            status_code = payload.get("status")
            download_url = payload.get("url")
        _log.info(
            "wopi callback for %s: status=%s has_url=%s",
            view_relative_path, status_code, bool(download_url),
        )

        # Parse scope for save_view_document
        parts = view_relative_path.split("/")
        if len(parts) < 6 or parts[0] != "view":
            raise HTTPException(status_code=400, detail="malformed view path")
        _, cust, dev, mile, tg, *rel = parts
        user_id = request.query_params.get("user") or "wopi-save"

        # OnlyOffice callback protocol: only fetch+save on status 2 or 6.
        if status_code in (2, 6) and download_url:
            import httpx
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    resp = await client.get(download_url)
                    resp.raise_for_status()
                    new_bytes = resp.content
            except Exception as exc:  # noqa: BLE001
                _log.error(
                    "wopi callback: failed to fetch modified doc from %s: %s: %s",
                    download_url, type(exc).__name__, str(exc)[:200],
                )
                # Still return {"error":0}; failing hard here causes OnlyOffice
                # to endlessly retry the callback. Ops alert path handles this.
                return JSONResponse({"error": 0})

            from core.src.storage import save_view_document
            row = await save_view_document(
                customer_id=cust, device_id=dev, milestone_id=mile, tg_name=tg,
                relative_parts=tuple(rel),
                content=new_bytes, saved_by=user_id, source="editor",
            )
            _audit(request, "document_saved", view_relative_path, user_id,
                   details={"version_num": row.version_num,
                            "size_bytes": row.size_bytes,
                            "onlyoffice_status": status_code})
            _log.info(
                "wopi callback: saved v%d (%d bytes) for %s",
                row.version_num, row.size_bytes, view_relative_path,
            )
            return JSONResponse({"error": 0})

        # Legacy path: raw-bytes WOPI PutFile (no JSON body, no status).
        # Kept for future WOPI clients that don't use OnlyOffice callback proto.
        if payload is None and raw_body:
            from core.src.storage import save_view_document
            row = await save_view_document(
                customer_id=cust, device_id=dev, milestone_id=mile, tg_name=tg,
                relative_parts=tuple(rel),
                content=raw_body, saved_by=user_id, source="editor",
            )
            _audit(request, "document_saved", view_relative_path, user_id,
                   details={"version_num": row.version_num,
                            "size_bytes": row.size_bytes,
                            "protocol": "wopi_putfile_raw"})
            return JSONResponse({"error": 0})

        # Status 1/3/4/7 or missing url: acknowledge without saving.
        return JSONResponse({"error": 0})


def _verify_wopi_from_headers(request: Request, secret: str) -> None:
    """Look for WOPI JWT on Authorization: Bearer <token> or ?access_token=..."""
    auth = request.headers.get("Authorization", "")
    _log.info(
        "WOPI request received: url=%s method=%s client=%s auth_present=%s "
        "access_token_present=%s user_agent=%s",
        request.url.path, request.method, request.client.host if request.client else "?",
        bool(auth), "access_token" in request.query_params,
        request.headers.get("user-agent", "?")[:80],
    )
    if auth.lower().startswith("bearer "):
        try:
            payload = _verify_wopi_jwt(secret=secret, token=auth[7:])
            _log.info("WOPI Bearer JWT verified: payload_keys=%s", list(payload.keys()))
        except HTTPException as exc:
            _log.warning("WOPI Bearer JWT REJECTED: %s (token[:40]=%s)",
                         exc.detail, auth[7:47])
            raise
        return
    tok = request.query_params.get("access_token")
    if tok:
        try:
            payload = _verify_wopi_jwt(secret=secret, token=tok)
            _log.info("WOPI access_token verified: payload_keys=%s", list(payload.keys()))
        except HTTPException as exc:
            _log.warning("WOPI access_token REJECTED: %s (token[:40]=%s)",
                         exc.detail, tok[:40])
            raise
        return
    _log.warning("WOPI request REJECTED: neither Authorization nor access_token provided")
    raise HTTPException(status_code=401, detail="WOPI JWT required")


def _encode_file_id(view_relative_path: str) -> str:
    return base64.urlsafe_b64encode(view_relative_path.encode("utf-8")).rstrip(b"=").decode("ascii")


def _decode_file_id(file_id: str) -> str:
    return _urlsafe_b64_decode(file_id).decode("utf-8")
