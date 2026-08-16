"""PLM-1 (2026-08-14) -- subprocess wrappers around on-prem Python scripts
that talk to the corp PLM system. Distinct from the sibling CorpPlmAdapter
Protocol (adapter.py + poller.py) which was a Ph-2 speculative shape for
deadline-tiered polling; this client is the Ph-1 concrete integration used
by the beat-fired PLM poll task (PLM-3).

Three callable operations, each shelling out to a Python script under
`HILDA_PLM_SCRIPTS_DIR` (default `/opt/plm_scripts`):

  * create_plm_ticket(device_id, items, owner_corp_id) -> (case_id, url) tuple
      ("", "") on failure; on success, case_id like "P20260810-03454"
      and url like "https://splm.sec.corp/.../K3483945xxxx".
      `owner_corp_id` is the corp ID of the person the PLM ticket is
      assigned to (per architect OWNER-5 2026-08-16: "PLM can be
      assigned to only one person; if owner_corp_id is a list, the
      first entry is used"). Passed to the on-prem script as
      `--owner-corp-id`.
  * list_and_download_all(case_id, dir) -> int 0 on success, -1 on failure
  * close_plm_defect(case_id)           -> int 0 on success, -1 on failure

(No separate get_actual_item_info wrapper: PLM-1b 2026-08-14 consolidation
per PLM-engineer confirmation -- createPlmTicket now returns both the case
code AND the web URL from a single API call, so HILDA never needs a
second round-trip.)

Contract each on-prem script must honor (so the wrapper can parse stdout +
returncode reliably):

  1. `main()` block with `argparse`.
  2. On success: `print(<result>)` to stdout, then `sys.exit(0)`.
     - create_plm_ticket: print a SINGLE JSON object with EXACTLY the keys
                          `case_id` (str) and `url` (str), e.g.
                          `{"case_id": "P20260810-03454", "url": "https://..."}`.
                          Any extra keys are ignored by the wrapper.
                          Recognized args:
                            --device-id       (str, required)
                            --items-json      (JSON list of [item_no, title] pairs, required)
                            --owner-corp-id   (str, required -- PLM assignee, OWNER-5)
     - list_and_download_all: print nothing (or file count for info)
     - close_plm_defect: print nothing
  3. On failure: exit with non-zero code (stderr optional but recommended).
  4. Never prompt for interactive input; never open a GUI.

If a script violates these (writes to stdout mid-run, hangs, etc.), the
wrapper's timeout + strict stdout parsing will catch it and treat the
call as a failure -- HILDA's poll task WARN-logs and moves on.

Subprocess isolation is deliberate: the on-prem scripts have their own
Python dependencies (SP client, cred files, corp cert bundle) that
hilda-worker's environment doesn't ship. Subprocess lets those scripts
run in whatever Python venv the corp deploy team maintains for them,
without coupling hilda-worker's requirements.txt to that toolchain.

Config seams (env vars, module-level constants -- monkey-patchable in
tests):

  HILDA_PLM_SCRIPTS_DIR      base dir containing the 4 scripts (default /opt/plm_scripts)
  HILDA_PLM_PYTHON           interpreter to invoke (default python3)
  HILDA_PLM_TIMEOUT_CREATE   subprocess timeout in seconds for create (default 60)
  HILDA_PLM_TIMEOUT_DOWNLOAD subprocess timeout for download (default 300)
  HILDA_PLM_TIMEOUT_CLOSE    subprocess timeout for close (default 30)

Script file names are module-level constants below; tests can monkey-patch
them or point HILDA_PLM_SCRIPTS_DIR at a fixture directory.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path
from typing import Iterable

__all__ = [
    "create_plm_ticket",
    "list_and_download_all",
    "close_plm_defect",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config resolution (env-driven, test-overridable via monkey-patch)
# ---------------------------------------------------------------------------


_DEFAULT_SCRIPTS_DIR = "/opt/plm_scripts"
_DEFAULT_PYTHON = "python3"

# Script file names -- module-level so tests can monkey-patch.
SCRIPT_CREATE   = "create_plm_ticket.py"
SCRIPT_DOWNLOAD = "plm_file_download.py"
SCRIPT_CLOSE    = "close_plm_defect.py"


def _scripts_dir() -> Path:
    return Path(os.environ.get("HILDA_PLM_SCRIPTS_DIR") or _DEFAULT_SCRIPTS_DIR)


def _python() -> str:
    return os.environ.get("HILDA_PLM_PYTHON") or _DEFAULT_PYTHON


def _timeout(env_var: str, default: int) -> int:
    raw = os.environ.get(env_var)
    if raw is None or not raw.strip():
        return default
    try:
        return max(1, int(raw))
    except ValueError:
        _log.warning("PLM_CLIENT: %s=%r not int; using default %d", env_var, raw, default)
        return default


# ---------------------------------------------------------------------------
# Low-level subprocess helper
# ---------------------------------------------------------------------------


def _run_script(
    script_name: str,
    args: list[str],
    *,
    cwd: Path | None = None,
    timeout_sec: int,
    op_label: str,
) -> tuple[int, str, str]:
    """Invoke `<python> <scripts_dir>/<script_name> <args...>` with the
    given cwd + timeout. Returns (returncode, stdout_stripped, stderr).
    All exceptions (missing script, timeout, spawn failure) are caught
    and converted to (returncode=-999, stdout='', stderr='<reason>') --
    callers only need to check returncode == 0."""
    script_path = _scripts_dir() / script_name
    cmd = [_python(), str(script_path), *args]

    if not script_path.exists():
        _log.warning(
            "PLM_CLIENT: %s script missing at %s -- returning failure",
            op_label, script_path,
        )
        return (-999, "", f"script not found: {script_path}")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=str(cwd) if cwd is not None else None,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _log.warning(
            "PLM_CLIENT: %s timed out after %ds: %s",
            op_label, timeout_sec, " ".join(cmd),
        )
        return (-999, "", f"timeout after {timeout_sec}s")
    except (OSError, ValueError) as exc:
        _log.warning(
            "PLM_CLIENT: %s spawn failed: %s: %s",
            op_label, type(exc).__name__, str(exc)[:200],
        )
        return (-999, "", f"spawn failed: {exc}")

    return (result.returncode, (result.stdout or "").strip(), (result.stderr or ""))


# ---------------------------------------------------------------------------
# Public wrappers -- one per on-prem script
# ---------------------------------------------------------------------------


def create_plm_ticket(
    device_id: str,
    items: Iterable[tuple[int, str]],
    *,
    owner_corp_id: str,
) -> tuple[str, str]:
    """Create a PLM ticket for `device_id` with a list of (item_no, item_title)
    tuples to include in the ticket body, assigned to `owner_corp_id`.
    Returns a `(case_id, url)` tuple:

      case_id -- e.g. "P20260810-03454" (the plm_id we persist)
      url     -- e.g. "https://splm.sec.corp/.../K3483945xxxx"
                 (the actual_item_info clickable URL for TPMs)

    Returns `("", "")` on any failure. Both are always populated together on
    success -- if the on-prem script returns JSON with one field missing or
    empty, the wrapper treats it as a partial failure and returns `("", "")`
    (we don't want to persist a plm_id without a clickable URL, or vice versa).

    OWNER-5 (2026-08-16): `owner_corp_id` is a REQUIRED kwarg. Callers must
    resolve the assignee before invoking (typically the first non-empty
    entry of `owner_corp_id_list` across the group per architect "PLM can
    be assigned to only one person"). Passing "" is rejected up front (we
    don't want to shell out and let the on-prem script surface the error
    later; fail loud + local).

    On-prem script contract:
      python3 create_plm_ticket.py --device-id <device_id> \
                                   --items-json <json>     \
                                   --owner-corp-id <corp_id>
      -> stdout: JSON object with keys `case_id` and `url` (both str)
      -> stderr: optional diagnostic
      -> exit  : 0 on success, non-zero on failure
    """
    if not owner_corp_id or not owner_corp_id.strip():
        _log.warning(
            "PLM_CLIENT: create_plm_ticket device=%s called with empty "
            "owner_corp_id -- refusing to shell out (PLM requires assignee)",
            device_id,
        )
        return ("", "")
    items_json = json.dumps([[int(no), str(title)] for (no, title) in items])
    rc, out, err = _run_script(
        SCRIPT_CREATE,
        [
            "--device-id", device_id,
            "--items-json", items_json,
            "--owner-corp-id", owner_corp_id.strip(),
        ],
        timeout_sec=_timeout("HILDA_PLM_TIMEOUT_CREATE", 60),
        op_label="create_plm_ticket",
    )
    if rc != 0:
        _log.warning(
            "PLM_CLIENT: create_plm_ticket device=%s owner_corp_id=%s exit=%s stderr=%s",
            device_id, owner_corp_id, rc, err[:200],
        )
        return ("", "")
    if not out:
        _log.warning(
            "PLM_CLIENT: create_plm_ticket device=%s owner_corp_id=%s exit=0 but stdout empty",
            device_id, owner_corp_id,
        )
        return ("", "")
    try:
        payload = json.loads(out)
    except json.JSONDecodeError as exc:
        _log.warning(
            "PLM_CLIENT: create_plm_ticket device=%s stdout not JSON: %s -- raw=%r",
            device_id, exc, out[:200],
        )
        return ("", "")
    case_id = str(payload.get("case_id") or "").strip()
    url     = str(payload.get("url") or "").strip()
    if not case_id or not url:
        _log.warning(
            "PLM_CLIENT: create_plm_ticket device=%s owner_corp_id=%s incomplete payload "
            "case_id=%r url=%r -- treating as failure",
            device_id, owner_corp_id, case_id, url,
        )
        return ("", "")
    _log.info(
        "PLM_CLIENT: create_plm_ticket device=%s owner_corp_id=%s case_id=%s url=%s",
        device_id, owner_corp_id, case_id, url,
    )
    return (case_id, url)


def list_and_download_all(case_id: str, download_dir: Path) -> int:
    """Download all files attached to PLM ticket `case_id` into
    `download_dir` (subprocess runs with cwd=download_dir; script writes
    to `<cwd>/downloads/` per its own convention). Caller is responsible
    for creating download_dir beforehand and walking its `downloads/`
    subtree after. Returns 0 on success, -1 on any failure.

    On-prem script contract:
      python3 plm_file_download.py --case-id <case_id>
      (writes to cwd/downloads/<file>...)
      -> exit: 0 on success, non-zero on failure
    """
    if not download_dir.exists():
        _log.warning(
            "PLM_CLIENT: list_and_download_all download_dir missing: %s",
            download_dir,
        )
        return -1
    rc, out, err = _run_script(
        SCRIPT_DOWNLOAD,
        ["--case-id", case_id],
        cwd=download_dir,
        timeout_sec=_timeout("HILDA_PLM_TIMEOUT_DOWNLOAD", 300),
        op_label="list_and_download_all",
    )
    if rc != 0:
        _log.warning(
            "PLM_CLIENT: list_and_download_all case_id=%s exit=%s stderr=%s",
            case_id, rc, err[:200],
        )
        return -1
    _log.info(
        "PLM_CLIENT: list_and_download_all case_id=%s ok cwd=%s stdout=%s",
        case_id, download_dir, out[:120],
    )
    return 0


def close_plm_defect(case_id: str) -> int:
    """Close PLM ticket `case_id`. Returns 0 on success, -1 on any failure.

    On-prem script contract:
      python3 close_plm_defect.py --case-id <case_id>
      -> exit: 0 on success, non-zero on failure
    """
    rc, out, err = _run_script(
        SCRIPT_CLOSE,
        ["--case-id", case_id],
        timeout_sec=_timeout("HILDA_PLM_TIMEOUT_CLOSE", 30),
        op_label="close_plm_defect",
    )
    if rc != 0:
        _log.warning(
            "PLM_CLIENT: close_plm_defect case_id=%s exit=%s stderr=%s",
            case_id, rc, err[:200],
        )
        return -1
    _log.info("PLM_CLIENT: close_plm_defect case_id=%s ok", case_id)
    return 0


# Note: get_actual_item_info() was intentionally removed 2026-08-14 per
# PLM-1b: create_plm_ticket() now returns both case_id AND url in one
# call, so no second script round-trip is needed. If the PLM system ever
# needs an out-of-band URL lookup (e.g. for backfilling historic rows
# whose url wasn't captured at create time), add a new wrapper -- do
# not repurpose create_plm_ticket for read-only lookups.
