"""DRM-1 (2026-08-28) -- subprocess wrapper around the on-prem
force-decryption helper script.

Sibling of core/src/issue_tracker/corp_plm/on_prem_client.py -- same
subprocess isolation pattern. hilda-worker calls the on-prem script at
`HILDA_DRM_SCRIPTS_DIR/<script>` before NSD polling reads a folder, so
DRM-wrapped (NASCA) files are unwrapped in-place first.

Kept lean by design (one operation, one script), but factored so we
can add sibling ops later (health check, batch summary, etc.) without
reshaping this file.

Config seams (env vars, module-level constants -- monkey-patchable in
tests):

  HILDA_DRM_SCRIPTS_DIR    base dir (default /opt/drm_decrypt_scripts)
  HILDA_DRM_PYTHON         interpreter (default python3)
  HILDA_DRM_TIMEOUT        subprocess timeout seconds (default 120)
  HILDA_DRM_ENABLED        "false"/"0"/"no" -> skip decrypt call entirely
                           (feature flag; useful in dev / non-corp
                           deploys where the endpoint is unreachable)

Failure policy per architect 2026-08-28: any non-zero return -> WARN
log + return False. Caller (nsd2_poll) treats False as "skip this
folder's ingest this tick, retry next tick". Decrypt idempotency is
guaranteed by the corp API (2026-08-28 confirm), so a subsequent
retry when the endpoint is healthy is safe.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

__all__ = [
    "decrypt_folder",
    "SCRIPT_DECRYPT",
]

_log = logging.getLogger(__name__)


_DEFAULT_SCRIPTS_DIR = "/opt/drm_decrypt_scripts"
_DEFAULT_PYTHON = "python3"
_DEFAULT_TIMEOUT_SEC = 120

# Script file name -- module-level so tests can monkey-patch.
SCRIPT_DECRYPT = "drm_decrypt.py"


def _scripts_dir() -> Path:
    return Path(os.environ.get("HILDA_DRM_SCRIPTS_DIR") or _DEFAULT_SCRIPTS_DIR)


def _python() -> str:
    return os.environ.get("HILDA_DRM_PYTHON") or _DEFAULT_PYTHON


def _timeout() -> int:
    raw = os.environ.get("HILDA_DRM_TIMEOUT")
    if raw is None or not raw.strip():
        return _DEFAULT_TIMEOUT_SEC
    try:
        return max(1, int(raw))
    except ValueError:
        _log.warning(
            "DRM_CLIENT: HILDA_DRM_TIMEOUT=%r not int; using default %d",
            raw, _DEFAULT_TIMEOUT_SEC,
        )
        return _DEFAULT_TIMEOUT_SEC


def _enabled() -> bool:
    raw = (os.environ.get("HILDA_DRM_ENABLED") or "").strip().lower()
    if raw in ("false", "0", "no", "off"):
        return False
    return True


def decrypt_folder(folder_path: str) -> bool:
    """Force-decrypt every DRM-wrapped file inside folder_path.

    Fires the on-prem script (which POSTs to the corp DRM endpoint).
    Returns True on success, False on any failure (missing script,
    subprocess timeout, non-zero exit, endpoint unreachable). Caller
    should treat False as "skip this ingest cycle, retry next tick"
    per DRM-1 architect ask 2026-08-28.

    File-level enumeration is NOT needed here -- the on-prem API
    interprets missing file_name as "decrypt every file in folder"
    which is cheaper than N per-file calls (1 subprocess + 1 HTTP
    round-trip per folder). Idempotent per corp API contract, so
    already-plaintext files are no-ops.

    When HILDA_DRM_ENABLED is "false"/"0"/"no", returns True without
    invoking the script -- lets non-corp deploys (dev machines, CI)
    proceed as if decryption succeeded. Never gates ingest in envs
    that don't need it.
    """
    if not folder_path:
        _log.warning("DRM_CLIENT: decrypt_folder called with empty path")
        return False

    if not _enabled():
        _log.info(
            "DRM_CLIENT: HILDA_DRM_ENABLED=false -- skipping decrypt for %s",
            folder_path[:200],
        )
        return True

    script_path = _scripts_dir() / SCRIPT_DECRYPT
    if not script_path.exists():
        _log.warning(
            "DRM_CLIENT: script missing at %s -- treating decrypt as FAILED "
            "(folder=%s)",
            script_path, folder_path[:200],
        )
        return False

    cmd = [
        _python(), str(script_path),
        "--folder-path", folder_path,
        # file-name intentionally omitted -> batch mode (decrypt all)
    ]
    timeout_sec = _timeout()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            check=False,
        )
    except subprocess.TimeoutExpired:
        _log.warning(
            "DRM_CLIENT: decrypt timed out after %ds folder=%s",
            timeout_sec, folder_path[:200],
        )
        return False
    except (OSError, ValueError) as exc:
        _log.warning(
            "DRM_CLIENT: decrypt spawn failed folder=%s: %s: %s",
            folder_path[:200], type(exc).__name__, str(exc)[:200],
        )
        return False

    if result.returncode != 0:
        _log.warning(
            "DRM_CLIENT: decrypt returncode=%d folder=%s stderr=%s",
            result.returncode, folder_path[:200],
            (result.stderr or "").strip()[:400],
        )
        return False

    _log.info(
        "DRM_CLIENT: decrypt ok folder=%s stdout=%s",
        folder_path[:200], (result.stdout or "").strip()[:200],
    )
    return True
