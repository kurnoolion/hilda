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
    APIRouter, Body, Depends, FastAPI, Form, Header, HTTPException, Request,
    status,
)
from fastapi.responses import (
    HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse,
)

__all__ = ["register_document_view_routes"]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Token: HMAC-signed scope + path bundle for /browse/edit + /browse/download
# ---------------------------------------------------------------------------


_TOKEN_TTL_SECONDS = 30 * 60   # 30 min


def _make_scoped_token(*, secret: str, view_relative_path: str, mode: str,
                       user_id: str, ttl_seconds: int = _TOKEN_TTL_SECONDS,
                       version_num: int | None = None) -> str:
    """URL-safe token containing view_relative_path + mode + user_id + expires_at.
    HMAC-SHA256 signed with dashboard.wopi_jwt_secret so tampering is detected.

    Modes: "view" | "edit" | "download" | "versions" | "history".

    Optional `version_num`: when set on a "download" token, /browse/download
    streams the historical `.v<N>` sibling instead of the current bytes. Used
    by the /browse/versions view to link Download on prior-version rows.
    Absent version_num = current bytes (backward compatible).
    """
    payload: dict[str, Any] = {
        "p":  view_relative_path,
        "m":  mode,
        "u":  user_id,
        "x":  int(time.time()) + ttl_seconds,
    }
    if version_num is not None:
        payload["v"] = int(version_num)
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


def _make_wopi_jwt(*, secret: str, view_relative_path: str, exp_seconds: int = 3600,
                   version_num: int | None = None) -> str:
    """WOPI back-channel token (OnlyOffice server → HILDA WOPI endpoints).
    HILDA verifies HMAC on inbound WOPI calls. Payload includes exp for
    freshness.

    Optional `version_num`: when set, wopi_get_file_contents streams the
    archived `.v<N>` sibling instead of the current bytes. Used by the
    read-only preview flow on prior versions in /browse/versions.
    """
    header = {"alg": "HS256", "typ": "JWT"}
    payload: dict[str, Any] = {
        "path": view_relative_path,
        "iat": int(time.time()),
        "exp": int(time.time()) + exp_seconds,
    }
    if version_num is not None:
        payload["v"] = int(version_num)
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


# File-type dispatch (per architect 2026-07-24 matrix refresh):
#   editor  = OnlyOffice DocEditor round-trip (Edit + Download)
#   native  = browser renders inline via /browse/view (View + Download)
#   download = file streams as attachment only (no Edit / View link)
#
# .txt moved from native -> editor: OnlyOffice supports plain-text editing
# via documentType="word", which gives us save-back + versions + history +
# DRM gating consistent with the .docx flow at zero extra plumbing.
#
# Legacy binary Office formats (.doc, .xls, .ppt) live in _DOWNLOAD_ONLY_EXTENSIONS
# per architect 2026-07-24. Empirical: corp Exchange DLP always NASCA-wraps
# legacy binary attachments in transit; the wrapped bytes can't be decrypted
# server-side, so Edit will always fail. Modern OOXML .docx/.xlsx/.pptx come
# through clean and stay editable. The D-152 magic-byte sniff still runs on
# every save — this is a policy layer on top: even if a clean legacy binary
# ever arrives through a non-email path, we still route it to download-only
# because OnlyOffice CE 8 doesn't reliably convert .doc/.xls anyway.
#
# .msg (Outlook message) and .db (SQLite) are download-only for the same
# "no browser renderer / binary payload" reason.
_EDITOR_EXTENSIONS = {
    # Modern OOXML — zip-backed, not wrapped by corp email path
    ".docx", ".xlsx", ".xlsm", ".pptx",
    ".txt",   # 2026-07-24: plaintext via OnlyOffice Word editor
}
_NATIVE_VIEW_EXTENSIONS = {
    ".pdf", ".html", ".htm", ".csv", ".md",
    # Browser-native image previews (2026-07-24 matrix add)
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg",
}
_DOWNLOAD_ONLY_EXTENSIONS = {
    # Legacy binary Office — always NASCA-wrapped by corp email; also poor
    # OnlyOffice CE support even when clean. Users download + open locally
    # in a NASCA-aware Office client.
    ".doc", ".xls", ".ppt",
    ".msg",  # Outlook message — no browser renderer
    ".db",   # SQLite database file — binary
}


def _open_mode_for(filename: str) -> str:
    """Return 'editor' | 'native' | 'download' based on extension.

    Order matters: editor wins over native (a .txt with editor + native
    membership would open in the editor). _DOWNLOAD_ONLY_EXTENSIONS is
    checked before the default so it stays authoritative-by-intent even
    if the sets accidentally overlap in the future.
    """
    ext = _ext(filename)
    if ext in _DOWNLOAD_ONLY_EXTENSIONS:
        return "download"
    if ext in _EDITOR_EXTENSIONS:
        return "editor"
    if ext in _NATIVE_VIEW_EXTENSIONS:
        return "native"
    return "download"


def _ext(filename: str) -> str:
    if "." not in filename:
        return ""
    return "." + filename.rsplit(".", 1)[-1].lower()


# --- UI presentation helpers (exposed to templates) ------------------------

def _pretty_by(saved_by: str | None) -> str:
    """Map internal audit identities to TPM-facing role labels for the
    documents view. Per architect 2026-07-24:
      * 'auto'    → 'owner'   (router-driven ingest from owner-reply email)
      * 'unknown' → 'TPM'     (dashboard-mock-auth Edit save-back)
      * anything else → passthrough (real corp_id when available)
    """
    if saved_by is None:
        return ""
    s = str(saved_by).strip()
    if s == "auto":
        return "owner"
    if s == "unknown":
        return "TPM"
    return s


# All view-tree timestamps are stored tz-aware UTC in Postgres. TPMs live in
# America/New_York → render as ET (auto EDT/EST via zoneinfo tzdata) rather
# than UTC. Per architect 2026-07-24. If future customers span multiple zones,
# make this per-user; Ph-1 single-tenant is fine as a module constant.
try:
    from zoneinfo import ZoneInfo
    _DISPLAY_TZ = ZoneInfo("America/New_York")
except Exception:  # noqa: BLE001 — tzdata missing on some minimal images
    _DISPLAY_TZ = None


def _fmt_dt(dt: datetime | None, fmt: str = "%Y-%m-%d %H:%M %Z") -> str:
    """Format a UTC-stored tz-aware datetime in America/New_York for TPM UI.
    Returns '' for None; falls back to the raw UTC string if zoneinfo is absent
    (dev environments without tzdata)."""
    if dt is None:
        return ""
    if _DISPLAY_TZ is None:
        return dt.strftime(fmt)
    return dt.astimezone(_DISPLAY_TZ).strftime(fmt)


def _wopi_src_to_key(wopi_src: str, version_num: int | None = None) -> str:
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

    Version isolation (PREV cascade 2026-07-24): when `version_num` is set,
    include it in the hash input so the read-only preview of a prior version
    gets a distinct key from the current-version editor session. Without this,
    OnlyOffice's cache would serve the current bytes when a TPM opens v1 in
    preview immediately after someone had the current version open.

    Format: `d{16-hex-of-sha256(wopi_src|v?)}_{minute_bucket}` — ~30 chars.
    """
    minute_bucket = int(time.time()) // 60
    hash_input = wopi_src if version_num is None else f"{wopi_src}|v{version_num}"
    digest = hashlib.sha256(hash_input.encode("utf-8")).hexdigest()[:16]
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
        # Images (native inline preview per architect 2026-07-24)
        ".png":  "image/png",
        ".jpg":  "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif":  "image/gif",
        ".bmp":  "image/bmp",
        ".webp": "image/webp",
        ".svg":  "image/svg+xml",
        # Office (editor via OnlyOffice; also correct on downloads)
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        # Download-only bins per architect 2026-07-24 matrix
        ".msg":  "application/vnd.ms-outlook",
        ".db":   "application/vnd.sqlite3",
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
        from core.src.storage.unrouted_ops import count_unrouted_for_scope
        entries = await list_tg_names_for_scope(
            customer_id=customer_id, device_id=device_id, milestone_id=milestone_id,
        )
        # UR-7 (2026-08-01): _unknownTG bucket count -- surface an entry
        # for the manual-routing page even when zero, so TPMs learn where
        # unrouted files go when the first one lands.
        unrouted_count = await count_unrouted_for_scope(
            customer_id=customer_id, device_id=device_id,
            milestone_id=milestone_id,
        )
        return templates.TemplateResponse(
            request,
            "view_tree_landing.html",
            {
                "customer_id":    customer_id,
                "device_id":      device_id,
                "milestone_id":   milestone_id,
                "tg_entries":     entries,
                "unrouted_count": unrouted_count,
            },
        )

    # ----- DRR-DL-1 (2026-08-06): on-demand Download DRR status ---------
    #
    # TPMs can grab the same DRR-V2 excel the beat tick produces at any
    # time — no need to wait for target_date−1 / target_date windows.
    # Reads current items from Postgres + fresh SP header fields, builds
    # the workbook, streams the bytes as an .xlsx attachment. Auth is the
    # same `/browse` gate as the rest of the module (no extra role check).
    @app.get(
        "/browse/{customer_id}/{device_id}/{milestone_id}/drr-status.xlsx",
    )
    async def download_drr_status(
        customer_id: str, device_id: str, milestone_id: str,
        request: Request,
        principal=Depends(_auth),
    ):
        _log.warning(
            "DRR_DL: on-demand download requested customer=%s device=%s "
            "milestone=%s principal=%s",
            customer_id, device_id, milestone_id,
            getattr(principal, "user_id", None)
            or getattr(principal, "corp_id", "?"),
        )

        # 1. Load items for the milestone (sync helper, same as one-shot).
        from core.src.storage.delivery_item_ops import list_items_for_milestone
        all_items = await list_items_for_milestone(milestone_id) or []
        items = [
            it for it in all_items
            if getattr(it, "customer_id", None) == customer_id
            and getattr(it, "device_id", None) == device_id
        ]
        if not items:
            _log.warning(
                "DRR_DL: no items in scope customer=%s device=%s milestone=%s "
                "(query returned %d items for milestone before device+customer "
                "filter) -- returning 404",
                customer_id, device_id, milestone_id, len(all_items),
            )
            raise HTTPException(
                status_code=404,
                detail=(
                    f"no delivery items in scope for customer={customer_id} "
                    f"device={device_id} milestone={milestone_id}"
                ),
            )

        # 2. Build DRR-V2 context (drr_version, section_grouping, header dicts,
        #    logo path) via the celery-free helper. Dashboard container
        #    doesn't ship celery, so this must NOT reach into
        #    workflow_engine.tasks.tpm_notification (that module's top
        #    imports hilda_celery_app). DRR-DL-1a (2026-08-06) extracted
        #    the helpers into a shared drr_v2_context module for exactly
        #    this reason.
        #
        # 2a. Seed template_lookup._CACHE lazily on first call. In the
        #     worker container bootstrap_task_deps() loads all customer
        #     templates at startup; the dashboard container skips that
        #     (celery-free path). Without templates cached,
        #     get_drr_section_grouping returns None and the builder
        #     falls back to the legacy 4-column flat sheet -- exactly
        #     the symptom seen on the corp-box smoke 2026-08-06.
        from core.src.template_schema import template_lookup
        if not template_lookup._CACHE:      # noqa: SLF001
            _log.warning(
                "DRR_DL: template_lookup cache empty at first call -- "
                "seeding from customizations/template_schemas/",
            )
            template_lookup.load_all_customer_templates()
        # 2b. The context helper needs an object with .sp_writer to reach
        #     SP. Dashboard has its own sp_writer wired via app.state
        #     (SP-write channel for browse-page refresh). Pull it from
        #     there; fall back to a stub with .sp_writer=None if not
        #     wired -- SP reads then return dict-of-Nones (blank header
        #     cells) rather than crashing.
        from types import SimpleNamespace
        sp_writer = getattr(request.app.state, "sp_writer", None)
        deps_stub = SimpleNamespace(sp_writer=sp_writer)

        from core.src.email_service.outbound.drr_v2_context import (
            build_drr_v2_context,
            read_deliverables_comments,
        )
        drr_ctx = build_drr_v2_context(
            deps_stub, customer_id, device_id, milestone_id,
        )

        # 2c. COMMENT-SRC-1 (2026-08-07): fetch fresh Remarks (SP `comment`
        #     column) directly from SP at click time. Bypasses the
        #     Deliverables-CHANGED alert sync path, so a TPM edit lands in
        #     the excel on the very next Download click even if the alert
        #     hasn't propagated yet. Overlay onto the Postgres items --
        #     SP wins where present; Postgres value stays for items SP
        #     doesn't return.
        sp_comments = read_deliverables_comments(
            deps_stub, customer_id, device_id, milestone_id,
        )
        overlaid = 0
        for _it in items:
            _no = getattr(_it, "item_no", None)
            if isinstance(_no, int) and _no in sp_comments:
                try:
                    _it.comment = sp_comments[_no]
                    overlaid += 1
                except Exception:  # noqa: BLE001 -- frozen models fall through
                    pass
        _log.warning(
            "DRR_DL: SP comment overlay: %d/%d items updated from live SP "
            "customer=%s device=%s milestone=%s",
            overlaid, len(items), customer_id, device_id, milestone_id,
        )

        # 2d. DRR-V2-8b (2026-08-07): fetch the most recent xlsx routed
        #     under APPS TG for this scope. That file is the owner's
        #     reply to the outreach; it carries an "Applications"
        #     worksheet we merge into our DRR excel's Applications tab.
        #     Silent-skip on any error (no APPS file yet, read failure,
        #     etc.) -- excel builder falls back to the placeholder note.
        apps_bytes: bytes | None = None
        try:
            from core.src.storage import list_files_in_tg
            from core.src.storage.nsd import NSDPath
            apps_files = await list_files_in_tg(
                customer_id=customer_id, device_id=device_id,
                milestone_id=milestone_id, tg_name="APPS",
            )
            # Pick the most recent .xlsx by last_saved_at
            xlsx_files = [
                f for f in (apps_files or [])
                if (f.filename or "").lower().endswith(".xlsx")
                and not f.is_drm_wrapped
            ]
            if xlsx_files:
                xlsx_files.sort(key=lambda f: f.last_saved_at, reverse=True)
                latest = xlsx_files[0]
                # Reconstruct absolute path from view_relative_path
                # (e.g. "view/MMK/SM-A012U/DRR/APPS/reply.xlsx")
                from pathlib import Path as _Path
                from core.src.storage.config import get_storage_config
                _cfg = get_storage_config()
                _abs = _cfg.nsd_mount_root / latest.view_relative_path
                if _abs.is_file():
                    apps_bytes = _abs.read_bytes()
                    _log.warning(
                        "DRR_DL: APPS attachment loaded filename=%r "
                        "size=%d saved_at=%s from %d candidate xlsx",
                        latest.filename, len(apps_bytes),
                        latest.last_saved_at, len(xlsx_files),
                    )
                else:
                    _log.warning(
                        "DRR_DL: APPS view path missing on disk: %s",
                        _abs,
                    )
            else:
                _log.warning(
                    "DRR_DL: no APPS xlsx found for scope customer=%s "
                    "device=%s milestone=%s -- Applications tab will "
                    "show placeholder",
                    customer_id, device_id, milestone_id,
                )
        except Exception as _exc:  # noqa: BLE001
            _log.warning(
                "DRR_DL: APPS attachment fetch failed: %s: %s -- "
                "Applications tab will show placeholder",
                type(_exc).__name__, str(_exc)[:120],
            )

        # 3. Build the workbook bytes.
        from core.src.email_service.outbound.drr_report_excel import (
            build_drr_report_excel,
        )
        try:
            xlsx_bytes = build_drr_report_excel(
                items=items, applications_sheet_bytes=apps_bytes, **drr_ctx,
            )
        except Exception as exc:
            _log.warning(
                "DRR_DL: build failed customer=%s device=%s milestone=%s: "
                "%s: %s",
                customer_id, device_id, milestone_id,
                type(exc).__name__, str(exc)[:200],
            )
            raise HTTPException(status_code=500, detail=str(exc)[:200])

        _log.warning(
            "DRR_DL: built %d bytes customer=%s device=%s milestone=%s "
            "items=%d section_grouping=%s",
            len(xlsx_bytes), customer_id, device_id, milestone_id,
            len(items),
            "present" if drr_ctx.get("section_grouping") else "legacy-flat",
        )

        # 4. Stream as attachment. Content-Disposition triggers "save as"
        #    in the browser — filename matches the beat-tick pattern.
        from io import BytesIO
        filename = f"DRR_{customer_id}_{device_id}_{milestone_id}_status.xlsx"
        return StreamingResponse(
            BytesIO(xlsx_bytes),
            media_type=(
                "application/vnd.openxmlformats-officedocument."
                "spreadsheetml.sheet"
            ),
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"',
                "Content-Length":       str(len(xlsx_bytes)),
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
            # Versions link only appears when there's history to show;
            # emit the token unconditionally so template branch is simple.
            versions_tok = _make_scoped_token(
                secret=secret, view_relative_path=f.view_relative_path,
                mode="versions", user_id=user_id,
            )
            history_tok = _make_scoped_token(
                secret=secret, view_relative_path=f.view_relative_path,
                mode="history", user_id=user_id,
            )
            rendered.append({
                "filename":            f.filename,
                "view_relative_path":  f.view_relative_path,
                "size_bytes":          f.size_bytes,
                "version_count":       f.version_count,
                "last_saved_at":       f.last_saved_at,
                "last_saved_at_pretty":_fmt_dt(f.last_saved_at),
                "last_saved_by":       f.last_saved_by,
                "last_saved_by_pretty":_pretty_by(f.last_saved_by),
                "open_mode":           effective_mode,
                "open_token":          tok,
                "download_token":      download_tok,
                "versions_token":      versions_tok,
                "history_token":       history_tok,
                "is_drm_wrapped":      f.is_drm_wrapped,
                # MERGE-1 (2026-07-28): flag surfaced as red asterisk in
                # view_tree_tg.html when an owner-authored version landed on
                # top of a prior TPM edit -- manual merge required.
                "needs_merge":         f.needs_merge,
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

    # ----- UR-5 (Ph-2 2026-08-01): manual routing UI for _unknownTG bucket --

    @app.get(
        "/browse/{customer_id}/{device_id}/{milestone_id}/_unknownTG/",
        response_class=HTMLResponse,
    )
    async def browse_unrouted(
        customer_id: str, device_id: str, milestone_id: str,
        request: Request,
        principal=Depends(_auth),
    ):
        """/_unknownTG landing per architect ask 2026-08-01: lists documents
        that landed in the _unrouted bucket for this (customer, device,
        milestone), lets the TPM pick a work item as the manual route target
        via dropdown, POST goes to UR-6.

        Excluded from the target dropdown per DashboardConfig
        (manual_routing_excluded_item_names + _milestone_names): Confirmation
        items, Default WIs, and any configured item_name -- scoped to the
        configured milestones when set (see UR-4 comment on cfg). MMK's
        item 85 config lives at HILDA_DASHBOARD_MANUAL_ROUTING_EXCLUDED_*.
        """
        from core.src.storage.unrouted_ops import (
            list_route_candidates_for_scope, list_unrouted_for_scope,
        )
        # Load exclusion policy from cfg (UR-4). The milestone gate keeps
        # MMK's "item 85 in DRR only" ask literal: exclusion applies here
        # iff this milestone is on the whitelist (or the whitelist is empty
        # -> apply everywhere).
        excluded_names = list(cfg.manual_routing_excluded_item_names or [])
        excluded_milestones = list(cfg.manual_routing_excluded_milestone_names or [])
        apply_exclusion = (
            (not excluded_milestones) or (milestone_id in excluded_milestones)
        )
        excluded_arg = excluded_names if apply_exclusion else None

        unrouted = await list_unrouted_for_scope(
            customer_id=customer_id, device_id=device_id,
            milestone_id=milestone_id,
        )
        candidates = await list_route_candidates_for_scope(
            customer_id=customer_id, device_id=device_id,
            milestone_id=milestone_id, excluded_item_names=excluded_arg,
        )
        # Shape candidates for the template: DeliveryItemTable rows carry
        # attributes the Jinja template shouldn't reach into directly.
        candidate_rows = [
            {
                # DeliveryItemTable primary key column is `item_id`; the UR-6
                # POST expects target_delivery_item_id — pass it as that.
                "delivery_item_id": c.item_id,
                "item_no":          c.item_no,
                "item_name":        c.item_name,
                "tg_name":          c.tg_name,
                "delivery_state":   c.delivery_state,
            }
            for c in candidates
        ]
        rows = [
            {
                "file_hash":             u.file_hash,
                "original_filename":     u.original_filename,
                "doc_type":              u.doc_type or "—",
                "ingested_at_pretty":    _fmt_dt(u.ingested_at),
                "is_dup_hash_elsewhere": u.is_dup_hash_elsewhere,
            }
            for u in unrouted
        ]
        # UR-6 (Ph-2 2026-08-01): outcome flash from redirect (POST -> 303 GET)
        flash_outcome = request.query_params.get("outcome") or None
        flash_target  = request.query_params.get("target")  or None
        flash_error   = request.query_params.get("error")   or None
        return templates.TemplateResponse(
            request,
            "view_tree_unrouted.html",
            {
                "customer_id":  customer_id,
                "device_id":    device_id,
                "milestone_id": milestone_id,
                "unrouted":     rows,
                "candidates":   candidate_rows,
                "flash_outcome":flash_outcome,
                "flash_target": flash_target,
                "flash_error":  flash_error,
            },
        )

    # UR-6 (Ph-2 2026-08-01): manual route commit. POST-Redirect-Get:
    # returns 303 back to /_unknownTG/ with outcome + target as query
    # params so the GET renders a flash message. Never renders inline --
    # keeps refresh-safe.
    @app.post(
        "/browse/{customer_id}/{device_id}/{milestone_id}/_unknownTG/route",
        response_class=HTMLResponse, response_model=None,
    )
    async def browse_unrouted_route(
        customer_id: str, device_id: str, milestone_id: str,
        request: Request,
        file_hash: str = Form(...),
        target_delivery_item_id: str = Form(...),
        principal=Depends(_auth),
    ):
        """Commit a manual routing decision from the /_unknownTG/ UI.

        Delegates to storage.unrouted_ops.route_unrouted_to_item, then 303s
        back to the /_unknownTG/ page with ?outcome=<code>&target=<item>
        (or &error=<detail> on failed) so the browser refresh doesn't
        replay the POST.
        """
        from core.src.storage.unrouted_ops import route_unrouted_to_item

        tpm_id = (
            getattr(principal, "corp_id", None)
            or getattr(principal, "user_id", None)
            or "unknown"
        )
        # UR-10a (2026-08-06) breadcrumb: POST entry. Grep the worker log
        # for `MANUAL_ROUTE` to trace every TPM routing action end-to-end.
        _log.warning(
            "MANUAL_ROUTE: POST /_unknownTG/route entered scope=%s/%s/%s "
            "file_hash=%s target=%s tpm=%s",
            customer_id, device_id, milestone_id,
            file_hash[:12], target_delivery_item_id, tpm_id,
        )
        result = await route_unrouted_to_item(
            file_hash=file_hash,
            target_delivery_item_id=target_delivery_item_id,
            tpm_id=tpm_id,
        )
        _log.warning(
            "MANUAL_ROUTE: POST result outcome=%s file_hash=%s target=%s "
            "target_nsd_path=%s error=%s",
            result.outcome, file_hash[:12], target_delivery_item_id,
            (result.target_nsd_path or "-")[:120],
            (result.error or "-")[:120],
        )

        from urllib.parse import urlencode
        params: dict[str, str] = {"outcome": result.outcome}
        if result.target_delivery_item_id:
            params["target"] = result.target_delivery_item_id
        if result.error:
            # Keep the flash short — full detail is in audit + logs.
            params["error"] = result.error[:200]
        redirect_url = (
            f"/browse/{customer_id}/{device_id}/{milestone_id}/_unknownTG/"
            f"?{urlencode(params)}"
        )
        return RedirectResponse(
            url=redirect_url, status_code=status.HTTP_303_SEE_OTHER,
        )

    # ----- Chunk 7: per-file versions list ---------------------------------

    @app.get("/browse/versions/{token}", response_class=HTMLResponse)
    async def browse_versions(token: str, request: Request):
        """Per-file version history — one row per DocumentVersion. Actions:
        Edit (current + non-DRM only), Download (every version, threaded via
        the scoped-token `v` claim), History (per-file, all events)."""
        payload = _resolve_scoped_token(secret=cfg.wopi_jwt_secret, token=token)
        if payload["m"] != "versions":
            raise HTTPException(status_code=403, detail="token not a versions token")
        view_relative_path = payload["p"]
        user_id = payload["u"]

        from core.src.storage import get_current_version, list_versions_for_file
        current = await get_current_version(view_relative_path)
        if current is None:
            raise HTTPException(status_code=404, detail="no such file")
        versions = await list_versions_for_file(view_relative_path)   # DESC

        filename = PurePosixPath(view_relative_path).name
        secret = cfg.wopi_jwt_secret

        # Edit link only for CURRENT + not DRM-wrapped + editor-mode extension.
        # Current: Edit + Download. Prior: read-only Preview + Download
        # (PREV cascade 2026-07-24 — architect: TPMs need to peek at what
        # an older version contained without risking a save).
        rows = []
        is_editor_file = (_open_mode_for(filename) == "editor")
        for v in versions:
            is_cur = (v.version_num == current.version_num)
            can_edit = is_cur and not v.is_drm_wrapped and is_editor_file
            # Prior versions: Preview link only when the extension is editor-
            # eligible AND the version isn't DRM-wrapped. DRM-wrapped prior
            # versions get Download-only (same as current-DRM policy).
            can_preview = (
                (not is_cur)
                and not v.is_drm_wrapped
                and is_editor_file
            )
            edit_tok = _make_scoped_token(
                secret=secret, view_relative_path=view_relative_path,
                mode="edit", user_id=user_id,
            ) if can_edit else None
            preview_tok = _make_scoped_token(
                secret=secret, view_relative_path=view_relative_path,
                mode="preview", user_id=user_id, version_num=v.version_num,
            ) if can_preview else None
            # Prior-version download carries `v` claim → browse_download routes
            # to read_version_bytes(path, N). Current stays without `v` to
            # exercise the read_current_version_bytes fast path.
            dl_tok = _make_scoped_token(
                secret=secret, view_relative_path=view_relative_path,
                mode="download", user_id=user_id,
                version_num=None if is_cur else v.version_num,
            )
            rows.append({
                "version_num":         v.version_num,
                "is_current":          is_cur,
                "is_drm_wrapped":      v.is_drm_wrapped,
                "size_bytes":          v.size_bytes,
                "saved_at_pretty":     _fmt_dt(v.saved_at),
                "saved_by_pretty":     _pretty_by(v.saved_by),
                "source":              v.source,
                "edit_token":          edit_tok,
                "preview_token":       preview_tok,
                "download_token":      dl_tok,
            })

        history_tok = _make_scoped_token(
            secret=secret, view_relative_path=view_relative_path,
            mode="history", user_id=user_id,
        )
        return templates.TemplateResponse(
            request,
            "view_tree_versions.html",
            {
                "filename":            filename,
                "view_relative_path":  view_relative_path,
                "rows":                rows,
                "history_token":       history_tok,
            },
        )

    @app.get("/browse/history/{token}", response_class=HTMLResponse)
    async def browse_history(token: str, request: Request):
        """Per-file audit-event timeline — opens/edits/saves/downloads across
        all versions (per architect 2026-07-24: per-file scope, not per-version).
        Rows are sourced from CommunicationLog filtered by external_message_id.
        """
        payload = _resolve_scoped_token(secret=cfg.wopi_jwt_secret, token=token)
        if payload["m"] != "history":
            raise HTTPException(status_code=403, detail="token not a history token")
        view_relative_path = payload["p"]

        from core.src.storage import list_document_events
        events = await list_document_events(view_relative_path)
        filename = PurePosixPath(view_relative_path).name

        # Humanize action_type for the timeline column
        _humanize = {
            "document_viewed":            "Opened (view)",
            "document_edit_opened":       "Opened (edit)",
            "document_saved":             "Saved",
            "document_downloaded":        "Downloaded",
            "document_edit_blocked_drm":  "Edit blocked (DRM)",
        }
        rows = []
        for e in events:
            # `details` may carry version_num on saves + downloads; surface it
            # in the row so the template can render a compact note column.
            note_bits = []
            v = e.details.get("version_num") if e.details else None
            if v:
                note_bits.append(f"v{v}")
            if e.details and e.details.get("onlyoffice_status"):
                note_bits.append(f"oo_status={e.details['onlyoffice_status']}")
            rows.append({
                "timestamp_pretty": _fmt_dt(e.timestamp),
                "event":            _humanize.get(e.action_type, e.action_type),
                "user_pretty":      _pretty_by(e.user_id),
                "note":             " · ".join(note_bits),
            })

        return templates.TemplateResponse(
            request,
            "view_tree_history.html",
            {
                "filename":           filename,
                "view_relative_path": view_relative_path,
                "rows":               rows,
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
        # Optional `v` claim (added by /browse/versions links for prior versions)
        # routes to the archived .v<N> sibling instead of current bytes.
        version_num = payload.get("v")
        _audit(request, "document_downloaded", view_relative_path, user_id,
               details={"version_num": version_num} if version_num else None)
        if version_num is not None:
            from core.src.storage import read_version_bytes
            content = await read_version_bytes(view_relative_path, int(version_num))
        else:
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
        # Only OOXML + .txt reach this branch per 2026-07-24 policy — legacy
        # binary formats (.doc/.xls/.ppt) are gated download-only upstream in
        # _open_mode_for. ODF variants (.odt/.ods/.odp) are not deployed in
        # this env but left in the mapping for future-proofing.
        if ext in ("docx", "odt", "txt"):
            document_type = "word"
        elif ext in ("xlsx", "xlsm", "ods"):
            document_type = "cell"
        elif ext in ("pptx", "odp"):
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

    # ----- PREV cascade 2026-07-24: read-only preview of a prior version ---

    @app.get("/browse/preview/{token}", response_class=HTMLResponse)
    async def browse_preview(token: str, request: Request):
        """Read-only OnlyOffice preview of a SPECIFIC prior version.

        Per architect 2026-07-24: on /browse/versions/{token}, prior versions
        get a "View" link that opens the archived .v<N> bytes in OnlyOffice
        with `editorConfig.mode="view"` + permissions.edit=false. No callback
        URL is set so OnlyOffice cannot even attempt a save-back — read-only
        end-to-end. Only the current version keeps the full Edit flow.
        """
        payload = _resolve_scoped_token(secret=cfg.wopi_jwt_secret, token=token)
        if payload["m"] != "preview":
            raise HTTPException(status_code=403, detail="token not a preview token")
        view_relative_path = payload["p"]
        user_id = payload["u"]
        version_num = payload.get("v")
        if version_num is None:
            raise HTTPException(status_code=400, detail="preview token missing v claim")
        version_num = int(version_num)

        filename = PurePosixPath(view_relative_path).name
        if _open_mode_for(filename) != "editor":
            raise HTTPException(status_code=415, detail="file type not previewable")

        if not cfg.onlyoffice_public_url or not cfg.wopi_jwt_secret:
            return HTMLResponse(
                "<html><body><h1>OnlyOffice not configured</h1></body></html>",
                status_code=503,
            )

        # DRM sniff: even for read-only preview, wrapped bytes can't be
        # decrypted by OnlyOffice — fail fast rather than let it spin.
        from core.src.storage import read_version_bytes
        head = (await read_version_bytes(view_relative_path, version_num))[:4]
        if head == b"<## ":
            _audit(request, "document_edit_blocked_drm", view_relative_path, user_id,
                   details={"version_num": version_num, "mode": "preview"})
            dl_tok = _make_scoped_token(
                secret=cfg.wopi_jwt_secret, view_relative_path=view_relative_path,
                mode="download", user_id=user_id, version_num=version_num,
            )
            return HTMLResponse(
                "<html><body><h1>🔒 DRM-protected version</h1>"
                f"<p>v{version_num} is IRM-wrapped and cannot preview in-browser. "
                f"<a href=\"/browse/download/{dl_tok}\">Download</a> to open locally.</p>"
                "</body></html>",
                status_code=415,
            )

        _audit(request, "document_viewed", view_relative_path, user_id,
               details={"version_num": version_num, "mode": "preview"})

        # WOPI back-channel URL carries `v` so wopi_get_file_contents streams
        # the archived .v<N> sibling instead of current bytes.
        hilda_internal = "http://hilda-api:8080"
        wopi_src = f"{hilda_internal}/wopi/files/{_encode_file_id(view_relative_path)}"
        wopi_access_token = _make_wopi_jwt(
            secret=cfg.wopi_jwt_secret, view_relative_path=view_relative_path,
            version_num=version_num,
        )

        ext = _ext(filename).lstrip(".")
        if ext in ("docx", "odt", "txt"):
            document_type = "word"
        elif ext in ("xlsx", "xlsm", "ods"):
            document_type = "cell"
        elif ext in ("pptx", "odp"):
            document_type = "slide"
        else:
            document_type = "word"

        docs_config: dict[str, Any] = {
            "documentType": document_type,
            "document": {
                "fileType":    ext or "docx",
                # Key includes version_num so this preview session is a
                # distinct cache entry from any concurrent edit of current.
                "key":         _wopi_src_to_key(wopi_src, version_num=version_num),
                "title":       f"{filename} (v{version_num} — read only)",
                "url":         f"{wopi_src}/contents?access_token={wopi_access_token}",
                "permissions": {
                    "edit":    False,
                    "download": True,
                    "review":  False,
                    "comment": False,
                    "print":   True,
                },
            },
            "editorConfig": {
                "mode": "view",   # OnlyOffice read-only mode
                "user": {"id": user_id, "name": user_id},
                # NO callbackUrl — read-only end-to-end; OnlyOffice must not
                # even attempt a save POST.
            },
        }
        docs_config_token = _sign_jwt(secret=cfg.wopi_jwt_secret, payload=docs_config)

        return templates.TemplateResponse(
            request,
            "view_tree_editor.html",
            {
                "onlyoffice_public_url": cfg.onlyoffice_public_url.rstrip("/"),
                "filename":              f"{filename} (v{version_num})",
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
        wopi_payload = _verify_wopi_from_headers(request, cfg.wopi_jwt_secret)
        # Optional `v` claim (PREV cascade 2026-07-24): when set by the read-only
        # preview flow on prior versions, stream the archived .v<N> sibling
        # instead of the current bytes. Absent = current (backward compatible).
        version_num = wopi_payload.get("v")
        if version_num is not None:
            from core.src.storage import read_version_bytes
            content = await read_version_bytes(view_relative_path, int(version_num))
        else:
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


def _verify_wopi_from_headers(request: Request, secret: str) -> dict[str, Any]:
    """Look for WOPI JWT on Authorization: Bearer <token> or ?access_token=...
    Returns the verified JWT payload (used by callers to read claims like `v`
    for version-scoped read-only preview). Raises HTTPException(401) on failure.
    """
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
        return payload
    tok = request.query_params.get("access_token")
    if tok:
        try:
            payload = _verify_wopi_jwt(secret=secret, token=tok)
            _log.info("WOPI access_token verified: payload_keys=%s", list(payload.keys()))
        except HTTPException as exc:
            _log.warning("WOPI access_token REJECTED: %s (token[:40]=%s)",
                         exc.detail, tok[:40])
            raise
        return payload
    _log.warning("WOPI request REJECTED: neither Authorization nor access_token provided")
    raise HTTPException(status_code=401, detail="WOPI JWT required")


def _encode_file_id(view_relative_path: str) -> str:
    return base64.urlsafe_b64encode(view_relative_path.encode("utf-8")).rstrip(b"=").decode("ascii")


def _decode_file_id(file_id: str) -> str:
    return _urlsafe_b64_decode(file_id).decode("utf-8")
