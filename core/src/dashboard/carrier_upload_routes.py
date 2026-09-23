"""CARRIER-BATCH-7 (2026-09-20): async carrier-upload callback endpoint.

Corp-side Jenkins uploader POSTs one row per file as uploads complete. HILDA
validates the HMAC-signed per-batch token, updates `carrier_upload_triplet`,
writes a `communication_log` audit row per file, and — when a per-item's
final file just landed — transitions the item RFS -> SubmittedToCustomer.

Callback URL shape:
  POST <reverse_proxy_origin>/api/v1/carrier_upload/callback/<batch_id>?token=<hmac>

HMAC construction:
  body    = "<batch_id>|<expires_at_unix>"
  sig     = hmac_sha256(wopi_jwt_secret, body).hexdigest()[:32]
  token   = "<expires_at_unix>.<sig>"

Validation:
  * token parses cleanly into (expires_at, sig)
  * now() < expires_at
  * hmac_compare(expected_sig, sig)

POST body (CARRIER-RETRY-1, D-218 — `error_code` replaces the old `success`
bool, because a bool can't say "don't bother retrying"):

  {"triplet_id": "...", "error_code": 0, "error_detail": null, "elapsed_ms": 1234}

    error_code = 0  file uploaded            -> triplet 'succeeded'
                 1  transient failure        -> triplet 'needs_retry';
                                                rides the batch's next
                                                re-dispatch
                 2  permanent fault          -> triplet 'permanent_failure';
                                                never retried

  A BATCH-level fault (Drive login died, job can't proceed at all) is POSTed
  as error_code=2 with triplet_id / filename / target_dir empty. Every
  still-pending triplet in the batch goes to permanent_failure and one
  aggregated ops alert follows from the reconcile beat.

A fresh callback URL is minted for every (re-)dispatch, so a token only has
to outlive one attempt.

See D-217, D-218.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request

__all__ = [
    "callback_ttl_seconds",
    "mint_batch_callback_url",
    "mint_callback_token",
    "mint_callback_url",
    "register_carrier_upload_routes",
    "verify_callback_token",
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# HMAC helpers (SHA-256; same primitive HILDA uses for browse-tree scoped
# tokens at document_view_routes._hmac_hex — kept independent here so the
# carrier-upload path can be reasoned about without cross-import).
# ---------------------------------------------------------------------------


def _hmac_hex(secret: str, body: str) -> str:
    return hmac.new(
        (secret or "unset-secret").encode("utf-8"),
        body.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()[:32]


def mint_callback_token(
    *, secret: str, batch_id: str, expires_at: int,
) -> str:
    """Produce `<expires_at_unix>.<sig>` for the given batch_id + expiry.

    Callers construct the full callback URL with mint_callback_url.
    """
    body = f"{batch_id}|{expires_at}"
    sig = _hmac_hex(secret, body)
    return f"{expires_at}.{sig}"


def mint_callback_url(
    *,
    secret: str,
    reverse_proxy_origin: str,
    batch_id: str,
    ttl_seconds: int,
    url_prefix: str = "",
) -> str:
    """Build the full URL the corp-side uploader POSTs to.

    URLPFX-1 (2026-09-07): corp nginx serves HILDA under `/hilda/*` and
    strips the prefix before proxying to FastAPI, so route declarations
    stay unprefixed but every emitted URL a browser or external service
    hits must carry the prefix.

    Args:
      secret: HILDA's wopi_jwt_secret; used for HMAC signing.
      reverse_proxy_origin: e.g. "https://hilda.corp.example:8443" from
        DashboardConfig.reverse_proxy_origin.
      batch_id: HILDA-minted batch id.
      ttl_seconds: how long the HMAC token stays valid.
      url_prefix: DashboardConfig.url_prefix (e.g. "/hilda"); empty for
        deployments serving at root. Uses url_prefix.join() semantics so
        double-prefixing is idempotent.
    """
    from .url_prefix import join as _url_join

    expires_at = int(time.time()) + int(ttl_seconds)
    token = mint_callback_token(secret=secret, batch_id=batch_id, expires_at=expires_at)
    origin = reverse_proxy_origin.rstrip("/")
    path = _url_join(url_prefix, f"/api/v1/carrier_upload/callback/{batch_id}")
    return f"{origin}{path}?token={token}"


def callback_ttl_seconds(ca_cfg) -> int:
    """TTL for ONE attempt's callback token.

    CARRIER-RETRY-7 (D-218): a fresh URL is minted on every re-dispatch, so
    the token only has to outlive a single attempt -- through its kill
    deadline, plus grace for a late POST (network re-transmit, clock skew).
    Before D-218 this spanned the whole retry chain, which meant one leaked
    token stayed valid for hours.
    """
    return int(ca_cfg.batch_kill_after_seconds + ca_cfg.batch_callback_grace_seconds)


def mint_batch_callback_url(*, deps: Any, batch_id: str, ca_cfg) -> str:
    """Compose the callback URL for a (re-)dispatch of `batch_id`.

    Shared by submit_to_carrier_task (first dispatch) and
    carrier_upload_reconcile_task (re-dispatch) so both agree on origin,
    prefix, secret and TTL -- a mismatch would mint URLs the callback
    endpoint rejects, and the failure mode (all callbacks 401) is invisible
    until the batch times out.

    Reads DashboardConfig off task_deps when wired; otherwise falls back to
    the worker's environment (older deploys don't attach dashboard_config).
    """
    import os

    dash_cfg = getattr(deps, "dashboard_config", None)
    secret = getattr(dash_cfg, "wopi_jwt_secret", "") if dash_cfg else ""
    origin = getattr(dash_cfg, "reverse_proxy_origin", "") if dash_cfg else ""
    prefix = getattr(dash_cfg, "url_prefix", "") if dash_cfg else ""
    if not secret:
        secret = os.environ.get("HILDA_WOPI_JWT_SECRET", "unset-secret")
    if not origin:
        origin = os.environ.get("HILDA_REVERSE_PROXY_ORIGIN", "http://localhost:8080")
    if not prefix:
        # URLPFX-1 default: corp nginx serves HILDA under /hilda/*.
        prefix = os.environ.get("HILDA_DASHBOARD_URL_PREFIX", "/hilda")
    return mint_callback_url(
        secret=secret, reverse_proxy_origin=origin, batch_id=batch_id,
        ttl_seconds=callback_ttl_seconds(ca_cfg), url_prefix=prefix,
    )


def verify_callback_token(
    *, secret: str, batch_id: str, token: str,
) -> None:
    """Raise HTTPException(401) on any tampering or expiry. Silent success."""
    try:
        expires_str, sig = token.split(".", 1)
        expires_at = int(expires_str)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(status_code=401, detail="malformed token") from exc
    if int(time.time()) >= expires_at:
        raise HTTPException(status_code=401, detail="token expired")
    body = f"{batch_id}|{expires_at}"
    expected = _hmac_hex(secret, body)
    if not hmac.compare_digest(expected, sig):
        raise HTTPException(status_code=401, detail="bad signature")


# ---------------------------------------------------------------------------
# Callback handler + wiring
# ---------------------------------------------------------------------------


async def _transition_item_if_all_succeeded(
    *,
    deps: Any,
    batch_id: str,
    item_id: str,
    correlation_id: str,
) -> None:
    """If every triplet for this (batch, item) is 'succeeded', transition the
    delivery item ReadyForSubmission -> SubmittedToCustomer.

    Idempotent under concurrent callback arrivals: the guarded state machine
    treats an already-transitioned item as no-op ('no_op_idempotent'), and
    only one caller wins the actual state change.
    """
    from core.src.storage import carrier_upload_ops as _cu

    triplets = await _cu.get_triplets_for_batch_item(batch_id, item_id)
    if not triplets:
        return
    if not all(t.status == "succeeded" for t in triplets):
        return

    # Import lazily to keep dashboard-side module clean of tracker deps at
    # import time (mirrors kickoff_collection_task).
    from core.src.tracker import DeliveryState
    from core.src.tracker.transitions import update_delivery_state
    try:
        update_delivery_state(
            delivery_item_id=item_id,
            target_state=DeliveryState.SUBMITTED_TO_CUSTOMER,
            params={"transition_via": "carrier_upload_callback"},
            event_context={
                "correlation_id":   correlation_id,
                "delivery_item_id": item_id,
                "trigger_source":   "submit_to_carrier_task",
                "rule_id":          "carrier_upload_all_files_succeeded",
            },
            storage=deps.storage,
            sp_writer=deps.sp_writer,
            audit=deps.audit,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "carrier_upload_callback: item transition failed item=%s: %s: %s",
            item_id, type(exc).__name__, str(exc)[:120],
        )


def _audit_carrier_upload_file(
    *,
    deps: Any,
    batch_id: str,
    triplet_id: str,
    item_id: str,
    filename: str,
    target_dir: str,
    success: bool,
    error_code: str | None,
    error_detail: str | None,
    elapsed_ms: int | None,
) -> None:
    """Write one communication_log row per file callback. Matches today's
    per-file audit shape (action_type=carrier_upload, same details keys),
    with batch_id/triplet_id added so callback POSTs correlate back to a
    dispatch."""
    audit = getattr(deps, "audit", None)
    if audit is None:
        return
    details: dict[str, Any] = {
        "batch_id":    batch_id,
        "triplet_id":  triplet_id,
        "item_id":     item_id,
        "filename":    filename,
        "target_dir":  target_dir,
        "success":     success,
    }
    if error_code:
        details["error_code"] = error_code
        details["error_detail"] = error_detail or ""
    if elapsed_ms is not None:
        details["elapsed_ms"] = elapsed_ms
    try:
        audit.write_communication_log(
            action_type="carrier_upload",
            delivery_item_id=item_id,
            attribution={
                "trigger_source": "carrier_upload_callback",
                "correlation_id": batch_id,
                "modified_by":    "system:carrier_uploader",
            },
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("carrier_upload audit failed: %s", str(exc)[:120])


def register_carrier_upload_routes(app: FastAPI, cfg) -> None:
    """Wire the carrier upload callback route onto an existing FastAPI app.

    `cfg` is a DashboardConfig instance -- carries wopi_jwt_secret used to
    verify the callback token.
    """

    @app.post("/api/v1/carrier_upload/callback/{batch_id}")
    async def carrier_upload_callback(batch_id: str, request: Request):
        token = request.query_params.get("token") or ""
        verify_callback_token(
            secret=cfg.wopi_jwt_secret, batch_id=batch_id, token=token,
        )
        try:
            body = await request.json()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=400, detail="body is not JSON") from exc
        if not isinstance(body, dict):
            raise HTTPException(status_code=400, detail="body must be a JSON object")

        # -- error_code is the authoritative outcome (CARRIER-RETRY-1, D-218).
        # 0 = success, 1 = retryable, 2 = permanent fault. The pre-D-218
        # `success` bool is gone: it couldn't express "don't bother retrying".
        error_code_raw = body.get("error_code")
        if isinstance(error_code_raw, bool) or not isinstance(error_code_raw, (int, str)):
            raise HTTPException(status_code=400, detail="error_code must be an integer")
        try:
            error_code = int(error_code_raw)
        except (TypeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="error_code must be an integer") from exc

        error_detail = body.get("error_detail")
        elapsed_ms_raw = body.get("elapsed_ms")
        elapsed_ms = int(elapsed_ms_raw) if isinstance(elapsed_ms_raw, (int, float)) else None
        # Batch_id in body is optional; URL path is authoritative. But if
        # present, reject on mismatch to catch a mis-routed POST.
        body_batch_id = body.get("batch_id")
        if body_batch_id and body_batch_id != batch_id:
            raise HTTPException(status_code=400, detail="batch_id path/body mismatch")

        triplet_id = body.get("triplet_id")
        if not isinstance(triplet_id, str):
            triplet_id = ""

        from core.src.storage import carrier_upload_ops as _cu
        now = datetime.now(timezone.utc)

        # -- BATCH-LEVEL permanent fault. Per the architect 2026-09-23: with
        # error_code=2 the uploader may POST batch_id alone, leaving
        # triplet_id / filename / target_dir empty -- the fault (e.g. Drive
        # login failure) belongs to the job, not to any one file. Drag every
        # still-pending triplet to permanent_failure and stop; the alert
        # sweep in the reconcile beat sends one aggregated notice.
        if error_code == 2 and not triplet_id:
            moved = await _cu.mark_batch_permanent_failure(
                batch_id,
                detail=(error_detail if isinstance(error_detail, str) else None),
                now=now,
            )
            _log.warning(
                "carrier_upload_callback: batch-level permanent fault batch=%s "
                "triplets_failed=%d detail=%s",
                batch_id, moved, str(error_detail)[:120],
            )
            return {"ok": True, "batch_id": batch_id,
                    "status": "permanent_failure", "triplets_failed": moved}

        if not triplet_id:
            raise HTTPException(status_code=400, detail="missing triplet_id")

        updated = await _cu.mark_triplet_result(
            triplet_id=triplet_id,
            error_code=error_code,
            error=(error_detail or f"error_code={error_code}") if error_code else None,
            completed_at=now, now=now,
        )
        if updated is None:
            # Unknown triplet_id -- callback references a triplet HILDA never
            # dispatched (stale batch, misrouted POST). Log + 404.
            _log.warning(
                "carrier_upload_callback: unknown triplet_id=%s batch=%s",
                triplet_id, batch_id,
            )
            raise HTTPException(status_code=404, detail="unknown triplet_id")

        succeeded = error_code == 0

        # Audit + per-item transition. Access task_deps via app.state
        # (same pattern as HIST-INGEST-1 for document_view routes).
        deps = getattr(request.app.state, "task_deps", None)
        if deps is not None:
            _audit_carrier_upload_file(
                deps=deps, batch_id=batch_id, triplet_id=triplet_id,
                item_id=updated.item_id, filename=updated.filename,
                target_dir=updated.target_dir, success=succeeded,
                error_code=str(error_code), error_detail=error_detail,
                elapsed_ms=elapsed_ms,
            )
            if succeeded:
                await _transition_item_if_all_succeeded(
                    deps=deps, batch_id=batch_id, item_id=updated.item_id,
                    correlation_id=batch_id,
                )

        # Close the batch out once nothing is pending. Note this is NOT
        # received >= expected: a code-1 file counts as reported but is still
        # owed a retry, so the batch must stay open for the reconcile beat.
        settled = await _cu.settle_batch_if_all_reported(batch_id)

        return {
            "ok":       True,
            "batch":    {"batch_id": batch_id, "settled_status": settled},
            "triplet":  {
                "triplet_id":  updated.triplet_id,
                "status":      updated.status,
                "retry_count": updated.retry_count,
            },
        }
