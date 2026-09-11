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
from pathlib import Path, PurePosixPath
from typing import Any

from fastapi import (
    APIRouter, Body, Depends, FastAPI, Form, Header, HTTPException, Request,
    status,
)
from starlette.concurrency import run_in_threadpool
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


def _effective_open_mode(
    mode: str,
    *,
    is_drm_wrapped: bool = False,
    is_superseded: bool = False,
    pending_classification: bool = False,
) -> str:
    """Downgrade an extension-derived open mode to what the UI may offer.

    `mode` is `_open_mode_for`'s answer; the flags are reasons to withhold
    editing. Returned mode also decides which scoped token is minted, so the
    token grants exactly what the UI shows.

    - D-152: NASCA-wrapped files cannot be edited in-browser (OnlyOffice has
      no NASCA agent).
    - MERGE-2 (2026-08-30): a superseded revision is read-only -- editing a
      stale revision produces work upload will never select, since selection
      takes the family's winning revision.
    - EDIT-GATE-1 (2026-09-08): an unclassified or misaligned file is not yet
      a deliverable. Editing it writes a document_version with a fresh sha256
      into the VIEW tree while the authoritative file still sits in
      internal/.../_staged_classification/, which Reclassify then moves to
      rev1/ -- two trees diverging on one document whose type is still in
      question. Only EDIT is withheld: native View survives, because you
      often have to open a file to decide its doc_type, and Download is
      emitted separately by the caller regardless.
    """
    if is_drm_wrapped or is_superseded:
        return "download"
    if pending_classification and mode == "editor":
        return "download"
    return mode


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
    from .url_prefix import join as _url_join

    def _auth(request: Request):
        return require_authenticated_principal(request, cfg)

    def _u(path: str) -> str:
        """URLPFX-1: prefix an emitted url. Routes below stay unprefixed --
        nginx strips /hilda before proxying -- so this applies only to
        redirect targets and hrefs handed back to the browser."""
        return _url_join(getattr(cfg, "url_prefix", ""), path)

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

    # ----- UPLOAD-MANIFEST-1 (2026-09-06): submission preview ------------
    #
    # "Where will these files land on Google Drive?" answered BEFORE the TPM
    # clicks Submit in the SP UI, for a whole milestone. Rows come from the
    # same resolvers submit_to_carrier calls, so the preview cannot disagree
    # with the delivery.

    @app.get(
        "/browse/{customer_id}/{device_id}/{milestone_id}/manifest",
        response_class=HTMLResponse,
    )
    async def upload_manifest(
        customer_id: str, device_id: str, milestone_id: str,
        request: Request,
        principal=Depends(_auth),
    ):
        from core.src.storage import build_milestone_manifest
        manifest = await build_milestone_manifest(
            customer_id, device_id, milestone_id,
        )
        return templates.TemplateResponse(
            request,
            "upload_manifest.html",
            {
                "customer_id":  customer_id,
                "device_id":    device_id,
                "milestone_id": milestone_id,
                "manifest":     manifest,
            },
        )

    @app.get("/browse/{customer_id}/{device_id}/{milestone_id}/manifest.csv")
    async def upload_manifest_csv(
        customer_id: str, device_id: str, milestone_id: str,
        principal=Depends(_auth),
    ):
        """Same data as the page, for TPMs who want to diff it against the
        drive or attach it to a submission record."""
        import csv, io
        from fastapi.responses import StreamingResponse
        from core.src.storage import build_milestone_manifest

        manifest = await build_milestone_manifest(
            customer_id, device_id, milestone_id,
        )
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator=chr(10))
        w.writerow([
            "item_no", "tg_name", "delivery_state", "state_ready",
            "filename", "doc_type", "migrated_from",
            "carrier_destination", "excluded_reason", "source_path",
        ])
        for it in manifest.items:
            for r in it.rows:
                w.writerow([
                    it.item_no, it.tg_name, it.delivery_state,
                    "yes" if it.state_ready else "no",
                    r.filename, r.doc_type, r.migrated_from,
                    r.carrier_destination, r.excluded_reason, r.source_path,
                ])
        buf.seek(0)
        fname = f"manifest_{customer_id}_{device_id}_{milestone_id}.csv"
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{fname}"'},
        )

    # ----- UPLOAD-BUNDLE-1 (2026-09-06): download the submission as a zip -
    #
    # Same tree a Submit-to-Carrier would create on Drive, on the TPM's own
    # machine, so placement can be checked by opening folders. Entry paths ARE
    # the manifest's carrier destinations -- nothing is recomputed here.

    @app.get("/browse/{customer_id}/{device_id}/{milestone_id}/download-all.zip")
    async def download_all_zip(
        customer_id: str, device_id: str, milestone_id: str,
        principal=Depends(_auth),
    ):
        import shutil
        import tempfile
        from fastapi.responses import FileResponse
        from starlette.background import BackgroundTask

        from core.src.storage import build_milestone_manifest
        from core.src.storage.upload_bundle import build_manifest_zip

        manifest = await build_milestone_manifest(
            customer_id, device_id, milestone_id,
        )
        # Assembled on disk, not in memory: a milestone can hold gigabytes and
        # buffering that would take the API container down.
        tmpdir = tempfile.mkdtemp(prefix="hilda-bundle-")
        zip_path = PurePosixPath(tmpdir) / (
            f"submission_{customer_id}_{device_id}_{milestone_id}.zip"
        )
        result = await run_in_threadpool(
            build_manifest_zip, manifest, Path(str(zip_path)),
        )
        if result.oversized:
            shutil.rmtree(tmpdir, ignore_errors=True)
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=(
                    f"Submission bundle is {result.total_estimated_bytes:,} bytes, "
                    "over the download cap. Use the Submission preview page, or "
                    "download per technology group."
                ),
            )
        _log.warning(
            "DOWNLOAD_ALL_ZIP: scope=%s/%s/%s written=%d excluded=%d "
            "missing=%d collisions=%d by=%s",
            customer_id, device_id, milestone_id, result.files_written,
            result.files_excluded, result.files_missing,
            len(result.collisions), getattr(principal, "user_id", None),
        )
        return FileResponse(
            str(zip_path),
            media_type="application/zip",
            filename=Path(str(zip_path)).name,
            # Temp dir removed once the response has been streamed.
            background=BackgroundTask(shutil.rmtree, tmpdir, ignore_errors=True),
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
        #     DRR-V2-8g (2026-08-07): fetch logic extracted into
        #     drr_v2_context.read_apps_tg_xlsx_bytes so beat-tick path
        #     can share it without divergence.
        from core.src.email_service.outbound.drr_v2_context import (
            read_apps_tg_xlsx_bytes,
        )
        apps_bytes = await read_apps_tg_xlsx_bytes(
            customer_id, device_id, milestone_id,
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
            # MERGE-2 (2026-08-30): superseded revisions are read-only. Editing
            # a stale revision produces work that upload will never select
            # (selection takes the family's winning revision), so downgrade the
            # same way DRM does -- the token then grants only what the UI shows,
            # and /browse/edit enforces the rule server-side regardless.
            effective_mode = _effective_open_mode(
                mode,
                is_drm_wrapped=f.is_drm_wrapped,
                is_superseded=f.is_superseded,
                pending_classification=bool(
                    f.is_staged or f.is_staged_not_classified
                ),
            )
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
                # MERGE-2 (2026-08-30): older revision of a family -- template
                # badges it and shows Download only (no Edit link).
                "is_superseded":       f.is_superseded,
                # RECLASS-2 (2026-08-24): pass reclassify inputs to template
                # so it can render Reclassify button + doc_type dropdown on
                # is_staged rows. Template posts (file_hash, new_doc_type)
                # to /browse/{c}/{d}/{m}/reclassify -- POST handler derives
                # delivery_item_id + slug internally.
                "doc_type":            f.doc_type,
                "file_hash":           f.file_hash,
                "is_staged":           f.is_staged,
                # MISALIGN-PROJ-1 (2026-09-08): this key was missing from the
                # projection while TgFileEntry and the template both carried
                # it, so every template read resolved to Undefined -- falsy.
                # Effect: DOCTYPE-MISALIGN-UI-1's warning badge and Reclassify
                # control have NEVER rendered for a misaligned document in
                # this deployment, while the carrier-destination cell (which
                # reads the already-projected upload_excluded_reason) reported
                # the file as staged. The two cells appeared to contradict
                # each other; they were reading a present key and a missing
                # one. Found live on MMK DRR MNO-ETM.
                "is_staged_not_classified": f.is_staged_not_classified,
                # RECLASS-UI-SCOPE-1 (2026-08-27): per-row Reclassify options
                # scoped to the routed item's item_type (FR-86 alignment).
                # Template renders one <option> per entry; when singleton +
                # is_staged, the sole option is preselected so TPM clicks
                # once. When empty (no routed item known), template falls
                # back to legacy 4-option dropdown.
                "item_type":           f.item_type,
                "allowed_doc_types":   list(f.allowed_doc_types),
                # UPLOAD-DEST-1 / UPLOAD-FOLDER-OVERRIDE-1 (2026-09-06):
                # where this file lands on the carrier, or why it will not be
                # delivered, plus any TPM-chosen folder currently in force.
                "migrated_to":            f.migrated_to,
                "carrier_destination":    f.carrier_destination,
                "upload_excluded_reason": f.upload_excluded_reason,
                "target_folder_override": f.target_folder_override,
            })
        from core.src.storage.upload_folder_override import (
            list_folder_options_for_tg,
        )
        # DRRP1-DEST-1: on a source milestone (DRR) every item is
        # no_customer_upload with target_folder NULL, so its own folder list
        # is empty -- and DRR documents are not listed under P1 at all (the
        # view tree is milestone-scoped; migration is a read-through at
        # upload time). This page is therefore the ONLY place a TPM can set
        # the override, so the options must be the TARGET milestone's
        # folders. The override itself is stored on the source association
        # and travels via list_migrated_upload_files_for_item.
        from core.src.template_schema import milestone_item_mapping as _mim
        _fwd = [
            b for b in _mim.get_mapping_blocks(customer_id)
            if b.source_milestone == milestone_id
        ]
        _folder_milestone = _fwd[0].target_milestone if _fwd else milestone_id
        folder_options = await list_folder_options_for_tg(
            customer_id, device_id, _folder_milestone, tg_name,
        )
        return templates.TemplateResponse(
            request,
            "view_tree_tg.html",
            {
                "customer_id":  customer_id,
                "device_id":    device_id,
                "milestone_id": milestone_id,
                "tg_name":      tg_name,
                "files":        rendered,
                # UPLOAD-FOLDER-OVERRIDE-1: folders declared by work items in
                # THIS TG only. The TPM picks a destination that already
                # exists, so the override cannot invent one.
                "folder_options": folder_options,
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
        def _shape(items):
            # DeliveryItemTable primary key column is `item_id`; the UR-6
            # POST expects target_delivery_item_id — pass it as that.
            return [
                {
                    "delivery_item_id": c.item_id,
                    "item_no":          c.item_no,
                    "item_name":        c.item_name,
                    "tg_name":          c.tg_name,
                    "delivery_state":   c.delivery_state,
                    # UNROUTED-ITEMTYPE-1 (2026-09-09): item_type in the
                    # option label. The dropdown is deliberately NOT filtered
                    # to FR-86-aligned items -- per [D-192] an empty dropdown
                    # strands the TPM, and a resolved doc_type can itself be
                    # the half that is wrong. But an unlabelled list gives no
                    # way to see that a pick will misalign and land STAGED,
                    # so show the item_type and let the TPM choose knowingly.
                    "item_type":        c.item_type,
                }
                for c in items
            ]

        # UNROUTED-TG-SCOPE-1 (2026-09-03): the dropdown is now scoped PER
        # ROW to the TG the document arrived for, not milestone-wide. A doc
        # lives under exactly one TG folder per [D-153], so offering another
        # TG's items invites a mis-route the TPM can only undo by hand.
        #
        # Cached per TG rather than queried per row -- an unrouted list is
        # usually one or two TGs deep, so this is 1-2 queries regardless of
        # row count. The unscoped list is fetched once and reused for rows
        # with no resolvable TG (multi-TG email batch, or rows ingested
        # before the resolver was wired), because an empty dropdown would
        # leave the TPM unable to route at all.
        candidates_all = _shape(await list_route_candidates_for_scope(
            customer_id=customer_id, device_id=device_id,
            milestone_id=milestone_id, excluded_item_names=excluded_arg,
        ))
        by_tg_cache: dict[str, list[dict]] = {}

        async def _candidates_for(tg: str) -> tuple[list[dict], bool]:
            """Returns (rows, is_scoped). Falls back to the milestone-wide
            list when the TG is unknown or has no eligible items."""
            tg = (tg or "").strip()
            if not tg:
                return candidates_all, False
            if tg not in by_tg_cache:
                by_tg_cache[tg] = _shape(await list_route_candidates_for_scope(
                    customer_id=customer_id, device_id=device_id,
                    milestone_id=milestone_id,
                    excluded_item_names=excluded_arg, tg_name=tg,
                ))
            scoped = by_tg_cache[tg]
            if not scoped:
                _log.warning(
                    "unrouted UI: TG %r has no eligible route targets in "
                    "%s/%s/%s -- falling back to milestone-wide list",
                    tg, customer_id, device_id, milestone_id,
                )
                return candidates_all, False
            return scoped, True

        # Kept for templates/tests that still read the page-level list.
        candidate_rows = candidates_all
        rows = []
        for u in unrouted:
            cands, is_scoped = await _candidates_for(u.inferred_tg_name)
            rows.append({
                "file_hash":             u.file_hash,
                "original_filename":     u.original_filename,
                "doc_type":              u.doc_type or "—",
                "ingested_at_pretty":    _fmt_dt(u.ingested_at),
                "is_dup_hash_elsewhere": u.is_dup_hash_elsewhere,
                "inferred_tg_name":      u.inferred_tg_name or "",
                "candidates":            cands,
                "candidates_scoped":     is_scoped,
            })
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

        # DRRP1-STATE-1 phase 2 (2026-09-10): a manual TPM route from
        # _unrouted lands a new association on the target item. Universal
        # per user Q1=(b): if the target item is currently in RFS, pull
        # it back to UnderPMReview so PM re-approves before the doc ships.
        # Only fires when the route actually landed (outcome == 'ok');
        # idempotent no-op otherwise. See tracker.doc_received_bounce.
        if result.outcome == "ok":
            try:
                _deps = getattr(request.app.state, "task_deps", None)
                if _deps is not None:
                    from core.src.tracker.doc_received_bounce import (
                        bounce_to_under_pm_review_if_rfs,
                    )
                    bounce_to_under_pm_review_if_rfs(
                        deps=_deps,
                        delivery_item_id=target_delivery_item_id,
                        correlation_id=f"manual_route:{file_hash[:12]}",
                        source_marker="manual_route_from_unrouted",
                    )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "DRRP1_BOUNCE: manual-route hook unexpected exception "
                    "item=%s: %s: %s",
                    target_delivery_item_id, type(exc).__name__, str(exc)[:160],
                )

        from urllib.parse import urlencode
        params: dict[str, str] = {"outcome": result.outcome}
        if result.target_delivery_item_id:
            params["target"] = result.target_delivery_item_id
        if result.error:
            # Keep the flash short — full detail is in audit + logs.
            params["error"] = result.error[:200]
        redirect_url = _u(
            f"/browse/{customer_id}/{device_id}/{milestone_id}/_unknownTG/"
            f"?{urlencode(params)}"
        )
        return RedirectResponse(
            url=redirect_url, status_code=status.HTTP_303_SEE_OTHER,
        )

    # ----- RECLASS-2 (2026-08-24): TPM doc_type reclassification -----------
    # FR-87 step B UI. When TPM sees an Unresolved doc in the per-TG view,
    # picks the right doc_type from a dropdown -> POST here. Handler resolves
    # the (delivery_item_id, slug, tpm_email) trio, calls the existing
    # tpm_resolve_doc_type storage helper (which moves the file to the new
    # doc_type path + upgrades nsd_path_type to CLASSIFIED). Ingest-source
    # agnostic -- works for NSD / PLM / Email docs equally.

    # ----- UPLOAD-FOLDER-OVERRIDE-1 (2026-09-06): set/clear the carrier
    # folder for one document. The TPM picks a DESTINATION, not a work item --
    # a folder is often owned by several items, so the choice says nothing
    # about which item was meant.

    @app.post(
        "/browse/{customer_id}/{device_id}/{milestone_id}/tg/{tg_name}/set-folder",
        response_class=HTMLResponse, response_model=None,
    )
    async def browse_set_upload_folder(
        customer_id: str, device_id: str, milestone_id: str, tg_name: str,
        request: Request,
        file_hash: str = Form(...),
        target_folder: str = Form(""),
        principal=Depends(_auth),
    ):
        """Redirect a document, and its whole revision family, to a folder
        already declared by a work item in this TG. An empty target_folder
        resets it to the item's own folder.

        The submitted folder is re-validated against list_folder_options_for_tg
        server-side. The form is not trusted: accepting an arbitrary string
        would let a typo or a crafted POST deliver documents to a path no work
        item declares, which is precisely the "cannot invent a destination"
        property the override design rests on.
        """
        from core.src.storage.db import (
            DocumentItemAssociationTable, session_scope,
        )
        from core.src.storage.upload_folder_override import (
            clear_upload_folder_override,
            list_folder_options_for_tg,
            set_upload_folder_override,
        )
        from sqlalchemy import select as _select

        tpm_id = getattr(principal, "user_id", None) or "tpm@unknown"
        back = _u(
            f"/browse/{customer_id}/{device_id}/{milestone_id}"
            f"/tg/{tg_name}/?outcome="
        )

        # Resolve the association(s) this document has in scope. The template
        # only knows the file_hash; the handler derives the item, exactly as
        # the reclassify POST does.
        async with session_scope() as session:
            item_ids = [
                r for (r,) in (await session.execute(
                    _select(DocumentItemAssociationTable.delivery_item_id)
                    .where(
                        DocumentItemAssociationTable.file_hash == file_hash,
                        DocumentItemAssociationTable.milestone_id == milestone_id,
                    )
                )).all()
            ]
        if not item_ids:
            _log.warning(
                "SET_UPLOAD_FOLDER: no association for file_hash=%s in %s",
                file_hash[:12], milestone_id,
            )
            return RedirectResponse(back + "no_assoc", status_code=303)

        folder = (target_folder or "").strip()
        if not folder:
            for item_id in item_ids:
                await clear_upload_folder_override(
                    file_hash=file_hash, delivery_item_id=item_id,
                    tpm_id=tpm_id,
                )
            return RedirectResponse(back + "folder_reset", status_code=303)

        allowed = {
            o.target_folder
            for o in await list_folder_options_for_tg(
                customer_id, device_id, milestone_id, tg_name,
            )
        }
        if folder not in allowed:
            _log.warning(
                "SET_UPLOAD_FOLDER: rejected folder=%r not declared by any "
                "work item in tg=%s (%d option(s)) file_hash=%s by=%s",
                folder, tg_name, len(allowed), file_hash[:12], tpm_id,
            )
            return RedirectResponse(back + "folder_invalid", status_code=303)

        for item_id in item_ids:
            await set_upload_folder_override(
                file_hash=file_hash, delivery_item_id=item_id,
                target_folder=folder, tpm_id=tpm_id,
            )
        return RedirectResponse(back + "folder_set", status_code=303)

    @app.post(
        "/browse/{customer_id}/{device_id}/{milestone_id}/tg/{tg_name}/reclassify",
        response_class=HTMLResponse, response_model=None,
    )
    async def browse_reclassify(
        customer_id: str, device_id: str, milestone_id: str, tg_name: str,
        request: Request,
        file_hash: str = Form(...),
        new_doc_type: str = Form(...),
        principal=Depends(_auth),
    ):
        """TPM reclassifies an Unresolved doc's doc_type. Resolves inputs
        automatically:
          - delivery_item_id(s): every STAGED_NOT_CLASSIFIED association
            with this file_hash gets promoted (typically ONE per Option B
            routing; N-way OK if the same doc landed on N items).
          - doc_id_slug + rev_number: derived from filename via
            Fr52AttachmentRouter._slug_from_filename + rev=1 (Ph-1
            NEW_DOCUMENT convention; matches ingest-time slug generation
            path that would have fired if the classifier hadn't missed).
          - pm_id: TPM email from Projects_<customer> SP list via
            tpm_notification._read_tpm_email. Falls back to a sentinel on
            lookup miss (audit still captures the reclassify event).

        Post-Redirect-Get back to /browse/{c}/{d}/{m}/tg/{tg_name}/ with
        ?outcome=<code>[&error=<detail>] so browser refresh doesn't replay.
        """
        from urllib.parse import urlencode
        from core.src.email_service.inbound.attachment_router import (
            Fr52AttachmentRouter as _Fr52,
        )
        from core.src.storage.db import (
            DocumentIndexTable, DocumentItemAssociationTable, session_scope,
        )
        from core.src.storage.models import NSDPathType
        from core.src.storage.document_ops import tpm_resolve_doc_type
        from core.src.template_schema import DocType
        from sqlalchemy import select

        # Validate new_doc_type is a real DocType enum value (defense against
        # form-tampering; the template dropdown only exposes valid options).
        try:
            new_doc_type_enum = DocType(new_doc_type)
        except ValueError:
            params = {"outcome": "invalid_doc_type", "error": new_doc_type[:60]}
            return RedirectResponse(
                url=_u(f"/browse/{customer_id}/{device_id}/{milestone_id}/tg/{tg_name}/?{urlencode(params)}"),
                status_code=status.HTTP_303_SEE_OTHER,
            )

        # Look up the file's document_index row (for original filename ->
        # slug derivation) + all STAGED_NOT_CLASSIFIED associations.
        async with session_scope() as session:
            di_row = (await session.execute(
                select(DocumentIndexTable).where(DocumentIndexTable.file_hash == file_hash)
            )).scalar_one_or_none()
            if di_row is None:
                params = {"outcome": "no_doc_row", "error": file_hash[:12]}
                return RedirectResponse(
                    url=_u(f"/browse/{customer_id}/{device_id}/{milestone_id}/tg/{tg_name}/?{urlencode(params)}"),
                    status_code=status.HTTP_303_SEE_OTHER,
                )
            assocs = (await session.execute(
                select(DocumentItemAssociationTable).where(
                    DocumentItemAssociationTable.file_hash == file_hash,
                    DocumentItemAssociationTable.nsd_path_type == NSDPathType.STAGED_NOT_CLASSIFIED.value,
                )
            )).scalars().all()
            filename = di_row.original_filename or ""
            item_ids = [a.delivery_item_id for a in assocs]

        if not item_ids:
            params = {"outcome": "not_staged", "error": file_hash[:12]}
            return RedirectResponse(
                url=_u(f"/browse/{customer_id}/{device_id}/{milestone_id}/tg/{tg_name}/?{urlencode(params)}"),
                status_code=status.HTTP_303_SEE_OTHER,
            )

        # RECLASS-UI-SCOPE-1 (2026-08-27): FR-86 alignment defense. UI
        # dropdown offers only aligned options, but a form-tampered POST
        # could still submit a misaligned new_doc_type -- reject it here
        # so the file doesn't silently re-stage on the next classification
        # sweep. Look up each association's item_type and require alignment
        # for ALL of them (under D-155 one-doc-one-item this is normally a
        # single check).
        from core.src.email_service.inbound.attachment_router import (
            Fr52AttachmentRouter as _Fr52,
        )
        from core.src.storage.db import DeliveryItemTable as _DelItm
        # DeliveryItemTable's primary-key column is `item_id` (not
        # `delivery_item_id`) -- session.get() uses PK directly.
        async with session_scope() as _s:
            _item_types: list[str] = []
            for iid in item_ids:
                _di = await _s.get(_DelItm, iid)
                if _di is not None and _di.item_type:
                    _item_types.append(_di.item_type)
        _misaligned = [
            it for it in _item_types
            if not _Fr52._fr86_aligned(it, new_doc_type_enum.value)
        ]
        if _misaligned:
            params = {
                "outcome": "misaligned",
                "error": f"{new_doc_type_enum.value} not valid for item_type={_misaligned[0]}",
            }
            _log.warning(
                "RECLASSIFY: rejected misaligned pick file_hash=%s "
                "new_doc_type=%s item_types=%s",
                file_hash[:12], new_doc_type_enum.value, _item_types,
            )
            return RedirectResponse(
                url=_u(f"/browse/{customer_id}/{device_id}/{milestone_id}/tg/{tg_name}/?{urlencode(params)}"),
                status_code=status.HTTP_303_SEE_OTHER,
            )

        # Derive slug + rev=1 (Ph-1 NEW_DOCUMENT). Slug generation matches
        # ingest-time convention -- if the file had classified at ingest, it
        # would have gotten this same slug.
        doc_id_slug = _Fr52._slug_from_filename(filename)
        rev_number = 1

        # Fetch TPM email from Projects_<customer> for pm_id / audit attribution.
        # Fallback: dashboard auth principal (X-User-Email) is a distant runner-up;
        # ultimate fallback: "tpm@unknown" sentinel matches SETUP-1 tolerance.
        tpm_email: str | None = None
        deps_state = getattr(request.app.state, "task_deps", None)
        if deps_state is not None:
            try:
                from core.src.workflow_engine.tasks.tpm_notification import _read_tpm_email
                tpm_email, _ = _read_tpm_email(deps_state, customer_id, device_id)
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "RECLASSIFY: TPM email lookup failed customer=%s device=%s: %s",
                    customer_id, device_id, type(exc).__name__,
                )
        if not tpm_email:
            tpm_email = (
                getattr(principal, "email", None)
                or getattr(principal, "user_id", None)
                or "tpm@unknown"
            )

        _log.warning(
            "RECLASSIFY: POST scope=%s/%s/%s/%s file_hash=%s new_doc_type=%s "
            "assocs=%d pm_id=%s",
            customer_id, device_id, milestone_id, tg_name,
            file_hash[:12], new_doc_type_enum.value, len(item_ids), tpm_email,
        )

        # Reclassify each staged association. Any one failure -> abort remainder
        # (partial state is confusing; caller can retry after fixing root cause).
        errors: list[str] = []
        for item_id in item_ids:
            try:
                await tpm_resolve_doc_type(
                    file_hash=file_hash,
                    delivery_item_id=item_id,
                    new_doc_type=new_doc_type_enum,
                    doc_id_slug=doc_id_slug,
                    rev_number=rev_number,
                    pm_id=tpm_email,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{item_id}:{type(exc).__name__}")
                _log.warning(
                    "RECLASSIFY: tpm_resolve_doc_type failed item=%s file_hash=%s: %s: %s",
                    item_id, file_hash[:12], type(exc).__name__, str(exc)[:200],
                )
                break

        outcome = "reclassified" if not errors else "partial_failure"
        params: dict[str, str] = {"outcome": outcome, "doc_type": new_doc_type_enum.value}
        if errors:
            params["error"] = "; ".join(errors)[:200]
        return RedirectResponse(
            url=_u(f"/browse/{customer_id}/{device_id}/{milestone_id}/tg/{tg_name}/?{urlencode(params)}"),
            status_code=status.HTTP_303_SEE_OTHER,
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
            "document_edit_blocked_superseded":
                                          "Edit blocked (superseded revision)",
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
                f"<p><a href=\"{_u(f'/browse/download/{dl_tok}')}\">Download</a> and open "
                "in a NASCA-aware Office client on your workstation to edit.</p>"
                "</body></html>",
                status_code=415,
            )

        # MERGE-2 (2026-08-30) belt-and-suspenders, mirroring the DRM guard
        # above: the TG view withholds the Edit link on superseded revisions,
        # but a bookmarked token or a direct curl can still reach here. Editing
        # a stale revision produces work carrier upload will never select, so
        # refuse and point at the revision that WILL upload.
        from core.src.storage import is_superseded_revision
        if await is_superseded_revision(view_relative_path):
            _audit(
                request, "document_edit_blocked_superseded",
                view_relative_path, user_id,
            )
            dl_tok = _make_scoped_token(
                secret=cfg.wopi_jwt_secret, view_relative_path=view_relative_path,
                mode="download", user_id=user_id,
            )
            return HTMLResponse(
                "<html><body>"
                "<h1>Superseded revision</h1>"
                "<p>A newer revision of this document has since arrived, so this "
                "one is read-only. Edits made here would not be submitted to the "
                "carrier &mdash; only the latest revision is uploaded.</p>"
                "<p>Open the latest revision from the TG view to make your edits, "
                f"or <a href=\"{_u(f'/browse/download/{dl_tok}')}\">download</a> this one "
                "for reference while merging.</p>"
                "</body></html>",
                status_code=409,
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
                f"<a href=\"{_u(f'/browse/download/{dl_tok}')}\">Download</a> to open locally.</p>"
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
            # MTR-1 (2026-08-27): post-save trigger -- recompute needs_merge
            # across the TG's docs, update delivery_item.manual_triage_required
            # and push to SP so the SP UI Submit-to-Carrier button gates
            # correctly. Best-effort; failures logged but don't fail the save.
            try:
                deps_state = getattr(request.app.state, "task_deps", None)
                if deps_state is not None:
                    from core.src.workflow_engine.tasks.manual_triage import (
                        refresh_manual_triage_after_view_save,
                    )
                    await refresh_manual_triage_after_view_save(
                        deps_state,
                        customer_id=cust, device_id=dev,
                        milestone_id=mile, tg_name=tg,
                        correlation_id=f"wopi-{row.version_id[:12]}",
                    )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "MTR-1 post-save refresh failed for %s: %s: %s",
                    view_relative_path, type(exc).__name__, str(exc)[:200],
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
            # MTR-1 (2026-08-27): mirror the JSON-callback branch's post-save
            # refresh so both WOPI paths keep manual_triage_required in sync.
            try:
                deps_state = getattr(request.app.state, "task_deps", None)
                if deps_state is not None:
                    from core.src.workflow_engine.tasks.manual_triage import (
                        refresh_manual_triage_after_view_save,
                    )
                    await refresh_manual_triage_after_view_save(
                        deps_state,
                        customer_id=cust, device_id=dev,
                        milestone_id=mile, tg_name=tg,
                        correlation_id=f"wopi-raw-{row.version_id[:12]}",
                    )
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "MTR-1 post-save refresh failed for %s: %s: %s",
                    view_relative_path, type(exc).__name__, str(exc)[:200],
                )
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
