"""NASCA-PLM-1 (2026-09-11) -- HTTP client for the corp NASCA PLM decrypt
API. Kicks off a per-plm_id decrypt job on the corp endpoint and polls
until terminal, so PLM downloads see plaintext files.

Sibling of core/src/storage/drm_client.py (NSD-side subprocess wrapper).
Different transport: PLM's decrypt is a direct HTTP async job (POST kicks
off, /status polled) rather than a per-folder subprocess script. Same
retry semantics: any non-terminal outcome -> return False -> caller
(plm_poll._download_and_ingest) skips download this tick and retries
next tick. Corp API guarantees idempotency, so redo-on-retry is safe.

API contract (per architect 2026-09-11 spec sheet):
  POST   /process-plm-documents           body: {"plm_id": "<id>"}
                                          -> {"success": true, ...}
  GET/POST /process-plm-documents/status  -> {"running": bool,
                                              "result": {...},
                                              "message": "..."}
Terminal states from /status:
  Finished  -- running=false, result.success=true  -> True
  Failed    -- running=false, result.success=false -> False
  Never run -- running=false, result=None          -> False (transient
                                                     right after POST;
                                                     next poll usually
                                                     flips to Running)
  Running   -- running=true                        -> keep polling

Config seams (env vars, monkey-patchable module-level constants):
  HILDA_NASCA_PLM_BASE_URL       default http://105.52.91.178:5050
  HILDA_NASCA_PLM_POLL_SEC       poll interval seconds (default 30)
  HILDA_NASCA_PLM_TIMEOUT_SEC    max wait for terminal (default 900 = 15m)
  HILDA_NASCA_PLM_HTTP_TIMEOUT   per-request HTTP timeout (default 30)
  HILDA_NASCA_PLM_ENABLED        "false"/"0"/"no" -> skip decrypt entirely
                                 (feature flag; dev / non-corp deploys)

Never raises externally: HTTP errors, JSON decode errors, timeouts, and
non-2xx responses are all WARN-logged and return False.
"""
from __future__ import annotations

import json
import logging
import os
import time
import urllib.error
import urllib.request
from typing import Any

__all__ = [
    "decrypt_plm_id",
]

_log = logging.getLogger(__name__)


_DEFAULT_BASE_URL = "http://105.52.91.178:5050"
_DEFAULT_POLL_SEC = 30
_DEFAULT_TIMEOUT_SEC = 15 * 60   # 15 minutes
_DEFAULT_HTTP_TIMEOUT = 30


def _base_url() -> str:
    raw = (os.environ.get("HILDA_NASCA_PLM_BASE_URL") or "").strip()
    return (raw or _DEFAULT_BASE_URL).rstrip("/")


def _poll_sec() -> int:
    return _int_env("HILDA_NASCA_PLM_POLL_SEC", _DEFAULT_POLL_SEC, min_val=1)


def _timeout_sec() -> int:
    return _int_env("HILDA_NASCA_PLM_TIMEOUT_SEC", _DEFAULT_TIMEOUT_SEC, min_val=1)


def _http_timeout() -> int:
    return _int_env("HILDA_NASCA_PLM_HTTP_TIMEOUT", _DEFAULT_HTTP_TIMEOUT, min_val=1)


def _int_env(name: str, default: int, min_val: int = 1) -> int:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return max(min_val, int(raw))
    except ValueError:
        _log.warning(
            "NASCA_PLM: %s=%r not int; using default %d",
            name, raw, default,
        )
        return default


def _enabled() -> bool:
    raw = (os.environ.get("HILDA_NASCA_PLM_ENABLED") or "").strip().lower()
    if raw in ("false", "0", "no", "off"):
        return False
    return True


def decrypt_plm_id(plm_id: str) -> bool:
    """Kick off + poll a NASCA PLM decrypt job for `plm_id`.

    Returns True when the job reaches Finished/success, False on any
    other outcome (kickoff HTTP failure, Failed, timeout, disabled=False
    returns True since there's nothing to decrypt). Never raises.

    Caller (plm_poll._download_and_ingest) uses the return value to
    gate the subsequent PLM download call; on False, skip download this
    tick and retry next tick. Corp API is idempotent -- repeating the
    kickoff on an already-decrypted plm_id is a no-op on their side.
    """
    if not plm_id or not str(plm_id).strip():
        _log.warning("NASCA_PLM: decrypt_plm_id called with empty plm_id")
        return False

    plm_id = str(plm_id).strip()

    if not _enabled():
        _log.info(
            "NASCA_PLM: HILDA_NASCA_PLM_ENABLED=false -- skipping decrypt "
            "for plm_id=%s", plm_id,
        )
        return True

    base = _base_url()
    kickoff_url = f"{base}/process-plm-documents"
    status_url = f"{base}/process-plm-documents/status"

    # Kickoff -- POST plm_id. On non-2xx or network error -> WARN + False.
    kickoff = _post_json(kickoff_url, {"plm_id": plm_id})
    if kickoff is None:
        return False
    if not kickoff.get("success"):
        _log.warning(
            "NASCA_PLM: kickoff returned success=false plm_id=%s body=%r",
            plm_id, kickoff,
        )
        return False
    _log.warning(
        "NASCA_PLM: kickoff ok plm_id=%s msg=%s",
        plm_id, str(kickoff.get("message") or "")[:200],
    )

    # Poll /status until terminal or timeout.
    poll_sec = _poll_sec()
    timeout_sec = _timeout_sec()
    deadline = time.monotonic() + timeout_sec

    while True:
        status = _get_json(status_url)
        if status is None:
            # Transient network hiccup: retry until deadline. Same as any
            # other non-terminal state -- polling loop handles it.
            if time.monotonic() >= deadline:
                _log.warning(
                    "NASCA_PLM: status HTTP failure at deadline plm_id=%s "
                    "(timeout=%ds)", plm_id, timeout_sec,
                )
                return False
            time.sleep(poll_sec)
            continue

        running = bool(status.get("running"))
        result = status.get("result")
        if running:
            if time.monotonic() >= deadline:
                _log.warning(
                    "NASCA_PLM: still running at deadline plm_id=%s "
                    "(timeout=%ds) msg=%s",
                    plm_id, timeout_sec,
                    str(status.get("message") or "")[:200],
                )
                return False
            time.sleep(poll_sec)
            continue

        # Terminal: running=false. Distinguish Finished / Failed / Never run.
        if isinstance(result, dict) and result.get("success"):
            renamed = result.get("renamed_files") or []
            _log.warning(
                "NASCA_PLM: decrypt finished plm_id=%s renamed_count=%d msg=%s",
                plm_id, len(renamed) if isinstance(renamed, list) else 0,
                str(result.get("message") or "")[:200],
            )
            return True

        # Failed OR Never run OR malformed. Never-run is transient right
        # after our POST (the server hasn't started the job yet); keep
        # polling until deadline. Anything else -> WARN + False now.
        if result is None:
            # Never-run: transient. Retry until deadline.
            if time.monotonic() >= deadline:
                _log.warning(
                    "NASCA_PLM: status=never-run at deadline plm_id=%s "
                    "-- kickoff may not have registered; retry next tick",
                    plm_id,
                )
                return False
            _log.info(
                "NASCA_PLM: status=never-run plm_id=%s -- polling",
                plm_id,
            )
            time.sleep(poll_sec)
            continue

        # Failed (or unexpected shape): WARN with the server-side message
        # so ops can trace which file/step blew up.
        msg = ""
        if isinstance(result, dict):
            msg = str(result.get("message") or "")
        _log.warning(
            "NASCA_PLM: decrypt failed plm_id=%s server_msg=%s",
            plm_id, msg[:400],
        )
        return False


# ---------------------------------------------------------------------------
# HTTP helpers -- stdlib only so no new deps. Both return the parsed JSON
# object on success, or None on any error (WARN-logged). Keeping them
# module-local so tests can monkey-patch cleanly.
# ---------------------------------------------------------------------------


def _post_json(url: str, body: dict[str, Any]) -> dict[str, Any] | None:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json",
                 "Accept":       "application/json"},
    )
    return _do_request(req, url, "POST")


def _get_json(url: str) -> dict[str, Any] | None:
    req = urllib.request.Request(
        url, method="GET",
        headers={"Accept": "application/json"},
    )
    return _do_request(req, url, "GET")


def _do_request(
    req: urllib.request.Request, url: str, method: str,
) -> dict[str, Any] | None:
    try:
        with urllib.request.urlopen(req, timeout=_http_timeout()) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        _log.warning(
            "NASCA_PLM: %s %s HTTPError code=%s reason=%s",
            method, url, exc.code, str(exc.reason)[:120],
        )
        return None
    except urllib.error.URLError as exc:
        _log.warning(
            "NASCA_PLM: %s %s URLError: %s",
            method, url, str(exc.reason)[:200],
        )
        return None
    except (OSError, TimeoutError) as exc:
        _log.warning(
            "NASCA_PLM: %s %s transport error: %s: %s",
            method, url, type(exc).__name__, str(exc)[:200],
        )
        return None

    try:
        parsed = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError) as exc:
        _log.warning(
            "NASCA_PLM: %s %s JSON decode failed: %s body=%s",
            method, url, exc, raw[:200],
        )
        return None

    if not isinstance(parsed, dict):
        _log.warning(
            "NASCA_PLM: %s %s expected JSON object; got %s",
            method, url, type(parsed).__name__,
        )
        return None
    return parsed
