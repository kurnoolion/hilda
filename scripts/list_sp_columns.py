"""list_sp_columns.py -- list all field/column definitions of a SharePoint list.

Standalone helper (not wired into any HILDA runtime). Bypasses HILDA's
3-list entity abstraction (Deliverables_/Milestones_/Projects_<customer_id>)
by talking to `SpSession` directly with a list name of your choice. Useful
for onboarding new lists (e.g., Deliverables_Template) before you decide
which columns to map into HILDA proper.

Read-only:  This script never writes anything; it hits the SP REST endpoint
    GET /_api/web/lists/getbytitle('<list>')/fields?$top=500 once and
    prints the result. No SP rows are created, updated, or deleted.

USAGE (run inside the hilda-worker container so config + env vars are already set):

    # Step 1 -- copy the script into the container's writable overlay (/tmp)
    podman cp scripts/list_sp_columns.py hilda-worker:/tmp/

    # Step 2 -- run it against the target list
    podman exec hilda-worker python /tmp/list_sp_columns.py Deliverables_Template

    # Try other lists too if useful
    podman exec hilda-worker python /tmp/list_sp_columns.py Milestones
    podman exec hilda-worker python /tmp/list_sp_columns.py Projects_MMK

Optional environment overrides (fall through to config/sharepoint_integration.json
+ any HILDA_SP_* env vars the container already has):

    HILDA_SP_SITE_URL       -- e.g. https://sp2017.corp/sites/hilda
    HILDA_SP_USERNAME       -- corp\\svc_account
    HILDA_SP_PASSWORD       -- <password>
    HILDA_SP_AUTH_TYPE      -- ntlm (default)

Output:

    Two tables side-by-side:
      USER-FACING COLUMNS       -- editable + visible columns you'd populate
                                    when creating new rows
      SYSTEM / HIDDEN COLUMNS   -- SP-managed metadata (usually skip)

    Each row shows:
      InternalName | Title | Type | Required | ReadOnly | Hidden
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Ensure the container's /app root is on sys.path even when python is invoked
# from /tmp (as happens with `podman exec ... python /tmp/list_sp_columns.py`).
# celery is launched with WORKDIR=/app which puts /app on sys.path implicitly;
# ad-hoc `podman exec python <script>` runs from the exec CWD (usually /) and
# would otherwise fail with `ModuleNotFoundError: No module named 'core'`.
# Silently no-op outside the container (dev machine, non-/app deployment).
for _candidate in ("/app", str(Path(__file__).resolve().parents[1])):
    if Path(_candidate).is_dir() and _candidate not in sys.path:
        sys.path.insert(0, _candidate)


def _resolve_config_path() -> Path | None:
    """Find sharepoint_integration.json in the standard + container paths."""
    for candidate in (
        Path("config/sharepoint_integration.json"),
        Path("/app/config/sharepoint_integration.json"),
    ):
        if candidate.exists():
            return candidate
    return None


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        print("ERROR: list name required, e.g. `python list_sp_columns.py Deliverables_Template`")
        return 2

    list_name = sys.argv[1]

    try:
        from core.src.sharepoint_integration.config import GlobalSharePointConfig
        from core.src.sharepoint_integration.sp_session import (
            SpSession, _quote_list_name,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: import failed: {type(exc).__name__}: {exc}")
        print("Are you running inside the hilda-worker container (PYTHONPATH set)?")
        return 3

    cfg = GlobalSharePointConfig.from_sources(config_path=_resolve_config_path())
    if not cfg.username or not cfg.password:
        print("ERROR: NTLM credentials missing. Set HILDA_SP_USERNAME + HILDA_SP_PASSWORD "
              "env vars OR provide them in config/sharepoint_integration.json.")
        return 4
    if cfg.auth_type != "ntlm":
        print(f"ERROR: this script only supports auth_type=ntlm (got: {cfg.auth_type!r})")
        return 5

    print(f"[info] site_url : {cfg.site_url}")
    print(f"[info] username : {cfg.username}")
    print(f"[info] list_name: {list_name}")
    print()

    session = SpSession(
        site_url=cfg.site_url,
        ntlm_user=cfg.username,
        ntlm_pass=cfg.password,
    )
    # /fields endpoint directly (SpSession's public API covers /items only;
    # /fields uses the same base list URL pattern + a different sub-path).
    fields_url = (
        f"{cfg.site_url.rstrip('/')}/_api/web/lists/getbytitle("
        f"'{_quote_list_name(list_name)}')/fields?$top=500"
    )
    try:
        resp = session._session.get(fields_url, timeout=cfg.timeout_seconds)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: network / auth failure: {type(exc).__name__}: {str(exc)[:200]}")
        return 6

    if resp.status_code == 404:
        print(f"ERROR: list '{list_name}' not found. Check the exact name (case-sensitive) "
              f"and that your SP account has read access.")
        return 7
    if resp.status_code >= 400:
        print(f"ERROR: HTTP {resp.status_code}: {resp.text[:400]}")
        return 8

    try:
        payload = resp.json()
    except ValueError:
        print("ERROR: response was not JSON. Raw text (first 400 chars):")
        print(resp.text[:400])
        return 9

    results = (payload.get("d") or {}).get("results") or []
    if not results:
        print("[warn] list exists but returned zero fields; unexpected. Raw payload:")
        print(json.dumps(payload, indent=2)[:2000])
        return 0

    # Split user-facing vs system/hidden for readability.
    user_facing = []
    system = []
    for f in results:
        internal = f.get("InternalName", "")
        title    = f.get("Title", "")
        type_str = f.get("TypeAsString", "")
        required = bool(f.get("Required", False))
        readonly = bool(f.get("ReadOnlyField", False))
        hidden   = bool(f.get("Hidden", False))
        from_base_type = bool(f.get("FromBaseType", False))
        row = (internal, title, type_str, required, readonly, hidden, from_base_type)
        if hidden or from_base_type:
            system.append(row)
        else:
            user_facing.append(row)

    def _print_table(rows, header):
        if not rows:
            return
        print(header)
        print("=" * len(header))
        w_internal = max(20, min(48, max(len(r[0]) for r in rows) + 1))
        w_title    = max(20, min(48, max(len(r[1]) for r in rows) + 1))
        w_type     = max(10, max(len(r[2]) for r in rows) + 1)
        fmt = (
            f"{{:<{w_internal}}} {{:<{w_title}}} {{:<{w_type}}} "
            f"{{:<9}} {{:<9}} {{:<7}}"
        )
        print(fmt.format("InternalName", "Title", "Type", "Required", "ReadOnly", "Hidden"))
        print(fmt.format(
            "-" * (w_internal - 1), "-" * (w_title - 1),
            "-" * (w_type - 1), "-" * 8, "-" * 8, "-" * 6,
        ))
        for r in sorted(rows, key=lambda x: x[0].lower()):
            print(fmt.format(
                r[0][:w_internal - 1],
                r[1][:w_title - 1],
                r[2][:w_type - 1],
                "yes" if r[3] else "no",
                "yes" if r[4] else "no",
                "yes" if r[5] else "no",
            ))
        print()

    _print_table(user_facing, f"USER-FACING COLUMNS ({len(user_facing)})")
    _print_table(system,      f"SYSTEM / HIDDEN COLUMNS ({len(system)}) -- usually skip")

    print(f"[done] {len(user_facing)} user-facing + {len(system)} system fields on list '{list_name}'")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
