#!/usr/bin/env python3
"""simulate_uploader_callbacks.py -- CARRIER-BATCH test harness.

Stands in for the corp-side Jenkins uploader. POSTs one per-file result
to HILDA's callback endpoint per triplet in a batch, as if each file
had just been uploaded. Use it to verify the callback + per-item
transition + reconcile flow end-to-end BEFORE Jenkins is wired.

Deps: requests. Optional: psycopg2-binary (only when using --postgres-url
to auto-load triplets from the carrier_upload_triplet table).

Two ways to feed triplets in:

  A) Auto-discover from Postgres (use this when HILDA's submit_to_carrier
     has already persisted the batch and you just want to fire callbacks):

        python simulate_uploader_callbacks.py \\
            --batch-id BATCH-abc1234567890abc \\
            --wopi-secret "$(cat /path/to/wopi_jwt_secret)" \\
            --reverse-proxy-origin http://localhost:8080 \\
            --postgres-url postgresql://hilda@localhost:5432/hilda \\
            --delay-per-file 0.3

  B) Explicit triplet list (use for greenfield testing when Postgres
     rows may not exist -- you'll need to seed those yourself first):

        python simulate_uploader_callbacks.py \\
            --batch-id BATCH-manual \\
            --callback-url 'http://localhost:8080/api/v1/carrier_upload/callback/BATCH-manual?token=<...>' \\
            --triplets-json triplets.json

     triplets.json shape:
        [
          {"triplet_id": "TRIP-1", "filename": "a.pdf", "target_dir": "Doc/A"},
          {"triplet_id": "TRIP-2", "filename": "b.pdf", "target_dir": "Doc/A"}
        ]

Failure simulation:
  --success-rate 0.9         30% chance any given triplet returns success=false
  --fail-triplet-ids A,B     these triplets explicitly fail
  --stop-after-n 5           stop simulating after N POSTs (leaves the rest
                             unreported -- exercises the reconcile timeout path)

The HMAC token is minted here using the same primitive HILDA uses
(HMAC-SHA256, 32-char hex, body = "<batch_id>|<expires_at_unix>"). You
provide the wopi_jwt_secret + reverse_proxy_origin; the script builds
the full callback URL.

Exit code 0 iff every POST returned 2xx.
"""
from __future__ import annotations

import argparse
import base64  # noqa: F401 -- kept for parity with HILDA's helpers
import hashlib
import hmac
import json
import random
import sys
import time
from datetime import datetime, timezone
from typing import Any


# ---------------------------------------------------------------------------
# HMAC (matches core/src/dashboard/carrier_upload_routes.py mint_callback_token)
# ---------------------------------------------------------------------------


def _hmac_hex(secret: str, body: str) -> str:
    return hmac.new(
        (secret or "unset-secret").encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]


def mint_callback_url(
    *, secret: str, reverse_proxy_origin: str, batch_id: str, ttl_seconds: int,
    url_prefix: str = "/hilda",
) -> str:
    """URLPFX-1 (2026-09-07): corp nginx serves HILDA under `/hilda/*` and
    strips the prefix before proxying. Emitted URLs must carry the prefix
    so nginx routes them.

    Default url_prefix='/hilda' matches the corp deployment. Pass
    url_prefix='' when HILDA serves at the root (dev / test rigs).
    Do NOT include the prefix in reverse_proxy_origin -- that would
    produce `/hilda/hilda/...`.
    """
    expires_at = int(time.time()) + int(ttl_seconds)
    body = f"{batch_id}|{expires_at}"
    sig = _hmac_hex(secret, body)
    token = f"{expires_at}.{sig}"
    origin = reverse_proxy_origin.rstrip("/")
    # Simple prefix join -- if url_prefix is empty, produces "/api/..."; otherwise
    # produces "/hilda/api/..." (or whatever prefix). Idempotent if the path
    # already starts with the prefix.
    pfx = url_prefix.strip("/")
    if pfx:
        prefix = f"/{pfx}"
    else:
        prefix = ""
    return f"{origin}{prefix}/api/v1/carrier_upload/callback/{batch_id}?token={token}"


# ---------------------------------------------------------------------------
# Triplet loaders
# ---------------------------------------------------------------------------


def load_triplets_from_postgres(
    *, postgres_url: str, batch_id: str,
) -> list[dict[str, Any]]:
    """Query carrier_upload_triplet for the given batch. Returns list of dicts
    ready to feed the POST loop.
    """
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        print(
            "ERROR: psycopg2 not installed. Install with 'pip install "
            "psycopg2-binary' or use --triplets-json instead.",
            file=sys.stderr,
        )
        sys.exit(2)

    conn = psycopg2.connect(postgres_url)
    conn.autocommit = True
    triplets: list[dict[str, Any]] = []
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT triplet_id, filename, target_dir, source_dir, item_id,
                   file_hash, status
              FROM carrier_upload_triplet
             WHERE batch_id = %s
             ORDER BY triplet_id
            """,
            (batch_id,),
        )
        for row in cur.fetchall():
            triplets.append(dict(row))
    conn.close()
    return triplets


def load_triplets_from_json(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, list):
        print(f"ERROR: {path} must contain a JSON array.", file=sys.stderr)
        sys.exit(2)
    return data


# ---------------------------------------------------------------------------
# POST loop
# ---------------------------------------------------------------------------


def simulate(
    *,
    callback_url: str,
    batch_id: str,
    triplets: list[dict[str, Any]],
    success_rate: float,
    fail_ids: set[str],
    delay_per_file: float,
    stop_after_n: int | None,
    verbose: bool,
) -> tuple[int, int]:
    """POST one callback per triplet. Returns (ok_count, fail_count).

    A "fail" here is either the simulated business failure (success=false in
    the payload) OR an HTTP error from the callback endpoint (non-2xx). Both
    count against the ok_count.
    """
    try:
        import requests
    except ImportError:
        print(
            "ERROR: requests not installed. Install with 'pip install requests'.",
            file=sys.stderr,
        )
        sys.exit(2)

    session = requests.Session()
    ok = 0
    fail = 0
    for i, t in enumerate(triplets):
        if stop_after_n is not None and i >= stop_after_n:
            print(
                f"[SIM] stop-after-n={stop_after_n} reached; "
                f"{len(triplets) - i} triplets left UNREPORTED "
                f"(reconcile beat will pick them up after batch_timeout).",
            )
            break

        triplet_id = t["triplet_id"]
        # Decide business success/failure
        explicit_fail = triplet_id in fail_ids
        rng_fail = random.random() > success_rate
        is_success = not (explicit_fail or rng_fail)

        payload = {
            "batch_id":    batch_id,
            "triplet_id":  triplet_id,
            "filename":    t.get("filename", ""),
            "target_dir":  t.get("target_dir", ""),
            "success":     is_success,
            "error_code":  None if is_success else "SIM-E001",
            "error_detail": None if is_success else (
                "explicit_fail" if explicit_fail else "random_fail"
            ),
            "elapsed_ms":  int(delay_per_file * 1000),
        }

        started = time.time()
        try:
            resp = session.post(callback_url, json=payload, timeout=30)
            http_ok = 200 <= resp.status_code < 300
            body_preview = resp.text[:200] if resp.text else ""
        except Exception as exc:
            http_ok = False
            resp = None
            body_preview = f"exception: {type(exc).__name__}: {str(exc)[:120]}"

        elapsed_ms = int((time.time() - started) * 1000)
        status_str = (
            f"HTTP {resp.status_code}" if resp is not None else "NO_RESPONSE"
        )
        marker = "OK " if http_ok else "ERR"
        if verbose or not http_ok:
            print(
                f"[SIM] {marker} triplet={triplet_id} filename={payload['filename']!r} "
                f"target={payload['target_dir']!r} biz_success={is_success} "
                f"http={status_str} elapsed_ms={elapsed_ms} body={body_preview}",
            )

        if http_ok:
            ok += 1
        else:
            fail += 1

        if delay_per_file > 0 and i < len(triplets) - 1:
            time.sleep(delay_per_file)

    return ok, fail


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Simulate Jenkins uploader callbacks for a CARRIER-BATCH batch.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--batch-id", required=True, help="Batch id (e.g. BATCH-abc123)")

    # URL: either build via HMAC or pass pre-built.
    p.add_argument("--callback-url", help="Full callback URL (skip HMAC minting)")
    p.add_argument("--wopi-secret", help="HILDA wopi_jwt_secret (needed to mint URL)")
    p.add_argument(
        "--reverse-proxy-origin",
        help="HILDA public origin, e.g. http://localhost:8080 (needed to mint URL)",
    )
    p.add_argument(
        "--ttl-seconds", type=int, default=6600,   # 110 min (matches HILDA default)
        help="HMAC token TTL when minting URL",
    )
    p.add_argument(
        "--url-prefix", default="/hilda",
        help="Dashboard URL prefix (URLPFX-1). Corp nginx serves HILDA under "
             "/hilda/* by default; pass '' if HILDA serves at root.",
    )

    # Triplet source: postgres OR json OR neither (empty).
    p.add_argument("--postgres-url", help="DB URL to auto-load triplets from")
    p.add_argument("--triplets-json", help="Path to JSON file with triplet list")

    # Simulation knobs
    p.add_argument(
        "--success-rate", type=float, default=1.0,
        help="Fraction of triplets marked success=true (default 1.0)",
    )
    p.add_argument(
        "--fail-triplet-ids", default="",
        help="Comma-separated triplet_ids to force success=false",
    )
    p.add_argument(
        "--delay-per-file", type=float, default=0.3,
        help="Sleep between POSTs to simulate upload latency (default 0.3s)",
    )
    p.add_argument(
        "--stop-after-n", type=int, default=None,
        help="Stop after N POSTs (leaves the rest unreported -- tests "
             "the reconcile timeout+retry path)",
    )
    p.add_argument("--verbose", action="store_true", help="Log every POST")
    p.add_argument(
        "--dry-run", action="store_true",
        help="Print the URL + payload plan and exit without POSTing",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()

    # Load triplets
    if args.postgres_url:
        triplets = load_triplets_from_postgres(
            postgres_url=args.postgres_url, batch_id=args.batch_id,
        )
    elif args.triplets_json:
        triplets = load_triplets_from_json(args.triplets_json)
    else:
        print(
            "ERROR: must pass either --postgres-url or --triplets-json",
            file=sys.stderr,
        )
        return 2

    if not triplets:
        print(
            f"ERROR: no triplets found for batch_id={args.batch_id}",
            file=sys.stderr,
        )
        return 2

    # Build callback URL
    if args.callback_url:
        callback_url = args.callback_url
    else:
        if not (args.wopi_secret and args.reverse_proxy_origin):
            print(
                "ERROR: --callback-url not given, so --wopi-secret and "
                "--reverse-proxy-origin are required to mint one.",
                file=sys.stderr,
            )
            return 2
        callback_url = mint_callback_url(
            secret=args.wopi_secret,
            reverse_proxy_origin=args.reverse_proxy_origin,
            batch_id=args.batch_id,
            ttl_seconds=args.ttl_seconds,
            url_prefix=args.url_prefix,
        )

    fail_ids = set(x.strip() for x in args.fail_triplet_ids.split(",") if x.strip())

    print(f"[SIM] batch_id={args.batch_id}")
    print(f"[SIM] callback_url={callback_url}")
    print(f"[SIM] triplets_to_post={len(triplets)}")
    if fail_ids:
        print(f"[SIM] explicit_fail_ids={sorted(fail_ids)}")
    print(f"[SIM] success_rate={args.success_rate}")
    print(f"[SIM] delay_per_file={args.delay_per_file}s")
    if args.stop_after_n is not None:
        print(f"[SIM] stop_after_n={args.stop_after_n} (leaves rest unreported)")

    if args.dry_run:
        print("[SIM] --dry-run set; not POSTing.")
        for t in triplets[: args.stop_after_n or len(triplets)]:
            print(
                f"[SIM] would POST triplet_id={t['triplet_id']} "
                f"filename={t.get('filename')!r} target={t.get('target_dir')!r}",
            )
        return 0

    started_at = datetime.now(timezone.utc)
    ok, fail = simulate(
        callback_url=callback_url,
        batch_id=args.batch_id,
        triplets=triplets,
        success_rate=args.success_rate,
        fail_ids=fail_ids,
        delay_per_file=args.delay_per_file,
        stop_after_n=args.stop_after_n,
        verbose=args.verbose,
    )
    ended_at = datetime.now(timezone.utc)
    total_s = (ended_at - started_at).total_seconds()
    print(
        f"[SIM] DONE ok={ok} http_fail={fail} total_time={total_s:.2f}s",
    )
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
