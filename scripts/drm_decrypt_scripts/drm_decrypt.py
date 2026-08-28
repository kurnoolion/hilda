#!/usr/bin/env python3
"""DRM-1 (2026-08-28) -- on-prem decrypt helper.

Sibling of /opt/plm_scripts/*.py: standalone Python script deployed
outside the hilda-worker container (default /opt/drm_decrypt_scripts/)
that hilda-worker shells out to via subprocess. Same contract as the
PLM scripts (see on_prem_client.py docstring):

  1. argparse CLI, no interactive prompts, no GUI.
  2. Success -> print JSON to stdout, exit 0.
  3. Failure -> non-zero exit, error text on stderr.
  4. No stdout writes mid-run (parser reads stdout as a single JSON blob).

What it does
------------
POSTs one folder+file pair to the corp DRM force-decryption API and
reports success/failure. Called by hilda-worker's NSD polling task
BEFORE each file is read off NSD, so DRM-wrapped files are unwrapped
in-place first.

Usage
-----
  drm_decrypt.py \\
    --folder-path "C:\\path\\to\\folder" \\
    [--file-name   "target_file.ext"] \\
    [--endpoint   "http://105.52.91.178:5050/force-decryption"] \\
    [--timeout    60]

--file-name is OPTIONAL. When omitted, the decrypt service decrypts
EVERY file in the folder (per corp DRM API contract 2026-08-28). NSD
polling calls the folder-only form once per folder before walking it,
which is cheaper than per-file (1 subprocess + 1 HTTP round-trip per
folder instead of N).

Success stdout (single JSON line):
  {"success": true, "message": "Enforce Decryption completed successfully."}

Failure: non-zero exit + stderr diagnostic. Wrapper caller (hilda-worker
subprocess run) treats any non-zero returncode as a decrypt miss and
should log-and-move-on rather than crashing the polling tick.

Deploy
------
  1. Copy this file to /opt/drm_decrypt_scripts/drm_decrypt.py on the
     corp box (mirror /opt/plm_scripts layout).
  2. chmod +x drm_decrypt.py (if invoking as script) OR call via
     `python3 drm_decrypt.py ...` (matches HILDA_PLM_PYTHON pattern).
  3. Bind-mount /opt/drm_decrypt_scripts read-only into hilda-worker
     the same way plm_scripts is mounted (see deploy hygiene notes).
  4. Optional env var `HILDA_DRM_SCRIPTS_DIR` on the container side
     will point hilda-worker's wrapper at this directory.

Dependencies: stdlib only (urllib). No requests / no HILDA imports --
runs in whatever host python the corp deploy team maintains.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request

DEFAULT_ENDPOINT = "http://105.52.91.178:5050/force-decryption"
DEFAULT_TIMEOUT_SEC = 60


def _post_decrypt(
    *,
    endpoint: str,
    folder_path: str,
    file_name: str | None,
    timeout_sec: int,
) -> dict:
    """POST {folder_path, file_name?} to the DRM endpoint. Returns the
    parsed JSON response dict. Raises on transport / non-2xx / non-JSON.

    file_name=None sends payload WITHOUT file_name (folder-only), which
    the corp DRM API interprets as "decrypt every file in folder_path"
    (2026-08-28 confirm).
    """
    body: dict = {"folder_path": folder_path}
    if file_name:
        body["file_name"] = file_name
    payload = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url=endpoint,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
        if resp.status < 200 or resp.status >= 300:
            raise RuntimeError(
                f"DRM API returned HTTP {resp.status}: {raw[:200]}"
            )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"DRM API returned non-JSON body: {raw[:200]!r}"
        ) from exc


def main() -> int:
    p = argparse.ArgumentParser(description="Call corp DRM force-decryption for one file.")
    p.add_argument("--folder-path", required=True,
                   help="Full NSD folder path (containing the file). Windows or POSIX.")
    p.add_argument("--file-name", required=False, default=None,
                   help="File basename inside folder-path. When omitted, the "
                        "DRM API decrypts EVERY file in the folder (batch mode).")
    p.add_argument("--endpoint", default=DEFAULT_ENDPOINT,
                   help=f"DRM API URL (default: {DEFAULT_ENDPOINT})")
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SEC,
                   help=f"HTTP timeout seconds (default: {DEFAULT_TIMEOUT_SEC})")
    args = p.parse_args()

    try:
        body = _post_decrypt(
            endpoint=args.endpoint,
            folder_path=args.folder_path,
            file_name=args.file_name,
            timeout_sec=args.timeout,
        )
    except urllib.error.HTTPError as exc:
        sys.stderr.write(
            f"DRM decrypt HTTP error: status={exc.code} "
            f"folder={args.folder_path!r} file={args.file_name!r}: "
            f"{str(exc)[:200]}\n"
        )
        return 2
    except urllib.error.URLError as exc:
        sys.stderr.write(
            f"DRM decrypt transport error: endpoint={args.endpoint} "
            f"file={args.file_name!r}: {str(exc)[:200]}\n"
        )
        return 3
    except (RuntimeError, TimeoutError) as exc:
        sys.stderr.write(
            f"DRM decrypt failed: file={args.file_name!r}: "
            f"{str(exc)[:200]}\n"
        )
        return 4
    except Exception as exc:  # noqa: BLE001 -- surface anything unexpected on stderr
        sys.stderr.write(
            f"DRM decrypt unexpected error: {type(exc).__name__}: "
            f"{str(exc)[:200]}\n"
        )
        return 5

    # API contract per screenshot: {"success": bool, "message": str}
    if not isinstance(body, dict) or not body.get("success", False):
        sys.stderr.write(
            f"DRM decrypt reported failure: file={args.file_name!r} "
            f"body={json.dumps(body)[:300]}\n"
        )
        return 1

    # Success -- single JSON line on stdout for wrapper to parse.
    print(json.dumps({
        "success": True,
        "message": body.get("message", ""),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
