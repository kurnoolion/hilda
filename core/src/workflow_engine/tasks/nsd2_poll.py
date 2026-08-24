"""NSD2-3 (2026-08-08) -- periodic poller that ingests documents landed
in the NSD2 SMB share for tg_name='HW PL' delivery items.

Beat-scheduled Celery task. Per tick:
  1. Enumerate active (customer, device, milestone) tuples from
     template_lookup._CACHE (same source as reconcile.py's _iter_tuples).
  2. For each tuple, load HW PL delivery_items from Postgres whose
     `ingress_folder` points at an NSD2 root (config-listed).
  3. For each such item:
       a. Resolve absolute device folder via nsd2_resolver.
       b. Walk it (recursively) via walk_nsd2_directory, honoring the
          MMK exclusion filter + file-size cap.
       c. For each yielded (relative_path, bytes, sha256):
            - Skip when document_index already has this file_hash
              (idempotent per NSD2-3 dedup contract).
            - Otherwise, hand off to `_ingest_new_nsd2_file(...)` --
              the router-integration seam NSD2-4 wires; a stub
              placeholder here just logs + counts.
  4. Emit a tick-end summary log with per-scope counters.

The task never raises: every layer of failure logs a WARN and moves
on so a bad SMB read / permission error / router hiccup for one item
doesn't abort the tick.

Design notes:
- Filesystem scan is CPU-cheap + I/O-bound; runs single-threaded on
  the worker's beat-fired dispatch. If tree grows > minutes to scan,
  swap to mtime-watermark strategy (config knob reserved for NSD2-5).
- NSD2 root list lives in nsd2_poll config (NSD2-5); Ph-1 default
  is empty, so the task is a no-op until an operator configures the
  root. This keeps corp-deploy hygiene: the SMB mount can land
  independent of code.
- Dedup by file_hash is GLOBAL (any prior ingest, from any channel,
  wins). Same NSD2 file appearing under two delivery_items yields
  two associations against the same document_index row -- matches
  existing D-155 / NEST-1 semantics.
"""
from __future__ import annotations

import logging
import uuid
from pathlib import Path
from typing import Any

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["nsd2_poll_task", "poll_nsd2_once"]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Celery entry
# ---------------------------------------------------------------------------


@hilda_celery_app.task(
    bind=True,
    name="core.src.workflow_engine.tasks.nsd2_poll.tick",
    autoretry_for=(),  # never retry -- next beat tick reprocesses
    ignore_result=False,
)
def nsd2_poll_task(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Beat-fired entry. Delegates to poll_nsd2_once() which is unit-testable
    without a Celery runtime."""
    deps = get_task_deps()
    return poll_nsd2_once(deps)


# ---------------------------------------------------------------------------
# Pure-python core (testable without Celery)
# ---------------------------------------------------------------------------


def poll_nsd2_once(deps: Any) -> dict[str, Any]:
    """One poll tick. Enumerates active scopes, iterates HW PL items,
    walks NSD2 device folders, dedups by file_hash, hands new files to
    the router seam. Returns per-tick counters + correlation_id."""
    from core.src.storage.nsd2_resolver import (
        resolve_nsd2_device_folder,
        walk_nsd2_directory,
    )

    correlation_id = f"nsd2poll-{uuid.uuid4().hex[:12]}"
    stats: dict[str, Any] = {
        "correlation_id":         correlation_id,
        "scopes_scanned":         0,
        "hw_pl_items_scanned":    0,     # total eligible items across all scopes (unchanged semantic)
        "devices_folder_missing": 0,     # NSD2-11: resolver returned None per (customer, device)
        "devices_walked":         0,     # NSD2-11: one walk per (customer, device), not per item
        "files_yielded":          0,
        "files_dedup_skipped":    0,
        "files_ingested":         0,
        "files_ingest_failed":    0,
    }

    if deps is None or getattr(deps, "storage", None) is None:
        _log.warning("NSD2_POLL: no deps/storage; skipping tick")
        stats["outcome"] = "no_deps"
        return stats

    # Config: which NSD2 roots exist. Empty list = feature off.
    nsd2_roots = _configured_nsd2_roots(deps)
    if not nsd2_roots:
        _log.info("NSD2_POLL: no nsd2_roots configured; tick is no-op")
        stats["outcome"] = "no_roots_configured"
        return stats

    _log.warning(
        "NSD2_POLL: tick start correlation_id=%s nsd2_roots=%s",
        correlation_id, [str(r) for r in nsd2_roots],
    )

    for customer_id, device_id, milestone_id in _iter_active_scopes(deps):
        stats["scopes_scanned"] += 1
        try:
            _poll_one_scope(
                deps=deps,
                stats=stats,
                correlation_id=correlation_id,
                customer_id=customer_id,
                device_id=device_id,
                milestone_id=milestone_id,
                nsd2_roots=nsd2_roots,
                resolve_fn=resolve_nsd2_device_folder,
                walk_fn=walk_nsd2_directory,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "NSD2_POLL: scope error customer=%s device=%s milestone=%s: %s: %s",
                customer_id, device_id, milestone_id,
                type(exc).__name__, str(exc)[:200],
            )

    stats["outcome"] = "fired"
    _log.warning("NSD2_POLL: tick done %s", stats)
    return stats


# ---------------------------------------------------------------------------
# Per-scope processing
# ---------------------------------------------------------------------------


def _poll_one_scope(
    *,
    deps: Any,
    stats: dict[str, Any],
    correlation_id: str,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    nsd2_roots: list[Path],
    resolve_fn: Any,
    walk_fn: Any,
) -> None:
    """NSD2-11 (2026-08-14) -- per-device single walk, not per-item.

    Load HW PL items for this (customer, device, milestone), keep those
    whose ingress_folder sits under a configured NSD2 root. Pick the FIRST
    such item's ingress_folder as the walk base (all HW PL items for the
    same device+P1 milestone are expected to share the same ingress_folder
    value; first-wins is a safe default if not). Walk ONCE; feed each
    yielded file through the router with ALL eligible items as candidates
    (router picks best match by filename substring). Replaces the earlier
    per-item loop that redundantly resolved + walked the same folder N
    times."""
    try:
        all_items = deps.storage.list_items_for_milestone(milestone_id, None) or []
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "NSD2_POLL: list_items_for_milestone failed customer=%s device=%s "
            "milestone=%s: %s: %s -- skipping scope",
            customer_id, device_id, milestone_id,
            type(exc).__name__, str(exc)[:120],
        )
        return

    hw_pl_items = _filter_hw_pl_nsd2_items(all_items, device_id, nsd2_roots)
    if not hw_pl_items:
        return

    stats["hw_pl_items_scanned"] += len(hw_pl_items)
    _poll_one_device(
        deps=deps,
        stats=stats,
        correlation_id=correlation_id,
        items=hw_pl_items,
        customer_id=customer_id,
        device_id=device_id,
        milestone_id=milestone_id,
        nsd2_roots=nsd2_roots,
        resolve_fn=resolve_fn,
        walk_fn=walk_fn,
    )


def _filter_hw_pl_nsd2_items(
    all_items: list[Any],
    device_id: str,
    nsd2_roots: list[Path],   # kept for signature stability; not read since NSD2-12
) -> list[Any]:
    """NSD2-12 (2026-08-14) -- gate on tracking_modality, not ingress_folder.

    Return items with:
      tg_name == 'HW PL'
      device_id matches scope
      'NetworkSharedDrive' in tracking_modality  (list membership; case-sensitive
                                                   per TrackingModality enum value)

    Previous NSD2-11 gate was ingress_folder starts-with /mnt/nsd2. That
    forced TPMs to type a container-mount path (HILDA-internal detail) into
    a SP field -- bad UX seam. TPMs now use the SP Choice column
    "tracking_modality" which is user-facing + validated at SP UI level.

    ingress_folder is deprecated for HW PL items (still exists in schema
    for other TG types per FR-77). The NSD2 walk root now comes exclusively
    from HILDA_NSD2_ROOTS env var (deployment-level config, not per-item)."""
    out: list[Any] = []
    for it in all_items:
        if (getattr(it, "tg_name", None) or "").strip() != "HW PL":
            continue
        if (getattr(it, "device_id", None) or "").strip() != device_id.strip():
            continue
        modality = getattr(it, "tracking_modality", None) or []
        if _NSD2_MODALITY_VALUE not in modality:
            continue
        out.append(it)
    return out


# TrackingModality enum value that opts a HW PL item into NSD2 polling.
# Mirrors TrackingModality.NETWORK_SHARED_DRIVE.value at
# core/src/template_schema/enums.py -- kept as a module-level constant
# (rather than importing the enum) to avoid a template_schema dependency
# at NSD2 module load time.
_NSD2_MODALITY_VALUE = "NetworkSharedDrive"


def _poll_one_device(
    *,
    deps: Any,
    stats: dict[str, Any],
    correlation_id: str,
    items: list[Any],
    customer_id: str,
    device_id: str,
    milestone_id: str,
    nsd2_roots: list[Path],
    resolve_fn: Any,
    walk_fn: Any,
) -> None:
    """NSD2-11/12 (2026-08-14) -- resolve device folder ONCE per (customer,
    device, milestone) scope, walk ONCE, feed each yielded file through the
    router with ALL eligible items as candidates. `items` is guaranteed
    non-empty by caller (_poll_one_scope short-circuits when the filter
    empties).

    Walk base comes from nsd2_roots[0] (HILDA_NSD2_ROOTS env var), not from
    any per-item field, per NSD2-12 (tracking_modality gate replaced
    ingress_folder path)."""
    first_item = items[0]
    base_path = nsd2_roots[0]  # env-var driven; no per-item path lookup

    # Resolve device folder from env-var-configured NSD2 root. Resolver still
    # takes the item to derive the model-type sub-folder (e.g. Deliverables -
    # Phone/A/A015V) from device_id + form-factor flags.
    try:
        device_folder = resolve_fn(first_item, base_path)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "NSD2_POLL: resolve raised (should not happen) device=%s: %s: %s",
            device_id, type(exc).__name__, str(exc)[:120],
        )
        stats["devices_folder_missing"] += 1
        return
    if device_folder is None:
        stats["devices_folder_missing"] += 1
        return

    stats["devices_walked"] += 1
    _log.warning(
        "NSD2_POLL: device=%s milestone=%s walking %s (candidates=%d items)",
        device_id, milestone_id, device_folder, len(items),
    )

    for rel_path, file_bytes, file_hash in walk_fn(device_folder, customer_id):
        stats["files_yielded"] += 1

        # Dedup: skip if this file_hash already lives in document_index
        # (from any channel -- email, prior NSD2 tick, etc.).
        try:
            existing = deps.storage.get_document_index_row_by_hash(file_hash)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "NSD2_POLL: dedup lookup failed file_hash=%s device=%s: %s: %s "
                "-- treating as new (safe: router de-dups again downstream)",
                file_hash[:16], device_id,
                type(exc).__name__, str(exc)[:120],
            )
            existing = None
        if existing is not None:
            stats["files_dedup_skipped"] += 1
            continue

        # Hand off new file to router with ALL scope's HW PL items as
        # candidates. Router picks best match by filename substring.
        try:
            _ingest_new_nsd2_file(
                deps=deps,
                items=items,
                customer_id=customer_id,
                milestone_id=milestone_id,
                filename=rel_path,
                content=file_bytes,
                file_hash=file_hash,
                correlation_id=correlation_id,
            )
            stats["files_ingested"] += 1
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "NSD2_POLL: ingest failed device=%s filename=%s file_hash=%s: %s: %s",
                device_id, rel_path, file_hash[:16],
                type(exc).__name__, str(exc)[:200],
            )
            stats["files_ingest_failed"] += 1


# ---------------------------------------------------------------------------
# Seams: config resolution + scope iteration + ingest stub
# ---------------------------------------------------------------------------


def _configured_nsd2_roots(deps: Any) -> list[Path]:
    """Return the list of NSD2 root paths from config. NSD2-5 wires
    the real config knob; Ph-1 seam reads `deps.nsd2_roots` if the
    bootstrap set it, else looks at `NSD2_ROOTS` env-var (comma-
    separated), else returns empty list (feature off)."""
    import os

    # Prefer explicit deps attr (test-injectable + bootstrap-injectable)
    from_deps = getattr(deps, "nsd2_roots", None)
    if from_deps:
        return [Path(str(p)) for p in from_deps]

    env = (os.environ.get("HILDA_NSD2_ROOTS") or "").strip()
    if env:
        return [Path(s.strip()) for s in env.split(",") if s.strip()]

    return []


# NSD2-11 (2026-08-14): only P1 milestone participates in NSD2 polling.
# Owners deposit HW PL documents on the network share during Phase 1 only;
# later milestones don't have an ingress_folder convention on NSD2. Filter
# at scope-iteration time so we emit one (customer, device, 'P1') tuple per
# device, skipping devices whose template has no P1 milestone defined.
_NSD2_MILESTONE_ID = "P1"


def _iter_active_scopes(deps: Any):
    """Yield (customer_id, device_id, milestone_id) tuples restricted to
    the P1 milestone per NSD2-11. Effective shape: one tuple per (customer,
    device) when the customer's template.yaml defines a P1 milestone entry;
    zero tuples otherwise. Mirrors reconcile.py's _iter_tuples pattern --
    source is template_lookup._CACHE."""
    from core.src.template_schema import template_lookup
    for customer_id, template in template_lookup._CACHE.items():  # noqa: SLF001
        devices = template.get("devices") or {}
        milestones = template.get("milestones") or {}
        if _NSD2_MILESTONE_ID not in milestones:
            continue
        for device_id in devices:
            yield (customer_id, device_id, _NSD2_MILESTONE_ID)


def _ingest_new_nsd2_file(
    *,
    deps: Any,
    items: list[Any],
    customer_id: str,
    milestone_id: str,
    filename: str,
    content: bytes,
    file_hash: str,
    correlation_id: str,
) -> None:
    """NSD2-4 (2026-08-08) + NSD2-11 (2026-08-14): feed a newly-discovered
    NSD2 file through the existing Fr52AttachmentRouter pipeline with MULTIPLE
    HW PL items as router candidates (per-device walk, not per-item). Router
    picks the best filename-substring match among them.

    Steps:
      1. Build a synthetic InboundAttachment (filename preserves subfolder
         structure per ZIP-1 convention).
      2. Widen ALL HW PL delivery_items in the (customer, device, P1) scope
         into the router's expected candidate_items shape.
      3. Construct the Ph-1 router (substring-first-pass mode).
      4. Call _process_regular_attachment with
         ingest_source=IngestSource.NETWORK_SHARED_DRIVE.

    batch_id: synthesized as `NSD2-<file_hash[:12]>` -- audit/log correlation
    only, never for lookup (NSD2 has no batch concept).
    """
    import asyncio

    from core.src.email_service.protocol import InboundAttachment
    from core.src.template_schema.enums import IngestSource
    from core.src.workflow_engine.tasks.inbound_attachment import (
        _build_ph1_router,
        _process_regular_attachment,
        _widen_candidates_for_router,
    )

    # DeliveryItemBase Pydantic model exposes the ID as `item_id`; the underlying
    # SQLAlchemy column is `delivery_item_id`. Try both so we're resilient
    # whether the caller hands us Pydantic rows or raw SA models.
    def _item_id(it: Any) -> str:
        return (
            getattr(it, "item_id", None)
            or getattr(it, "delivery_item_id", None)
            or ""
        )
    delivery_item_ids = [i for i in (_item_id(it) for it in items) if i]
    batch_id = f"NSD2-{file_hash[:12]}"

    # NSDMATCH-3 (2026-08-24): pass immediate parent folder name as
    # match_hint so the router uses folder-name for tag substring match
    # (item_description) while keeping filename for doc-type regex
    # classification. Empty parent (file at device-folder root) -> None
    # -> router falls back to filename for both, preserving pre-NSDMATCH
    # behavior for that edge case.
    from pathlib import PurePosixPath as _PP
    _parent = _PP(filename).parent.name or None
    attachment = InboundAttachment(
        filename=filename,
        content=content,
        content_type="application/octet-stream",
        file_hash=file_hash,
        match_hint=_parent,
    )

    async def _run() -> dict[str, Any]:
        # Widen ALL items in the scope so the router can pick the best
        # filename-substring match.
        candidate_items = _widen_candidates_for_router(
            deps,
            [{"delivery_item_id": iid} for iid in delivery_item_ids],
        )
        if not candidate_items:
            _log.warning(
                "NSD2_POLL_INGEST: candidate widen returned empty for items=%s "
                "-- widen helper couldn't fetch DeliveryItems; skipping file",
                delivery_item_ids[:3],
            )
            return {"processed": 0}

        router = _build_ph1_router(deps, customer_id=customer_id)
        if router is None:
            _log.warning(
                "NSD2_POLL_INGEST: router unavailable customer=%s items=%s "
                "-- skipping file",
                customer_id, delivery_item_ids[:3],
            )
            return {"processed": 0}

        return await _process_regular_attachment(
            deps=deps,
            router=router,
            attachment=attachment,
            candidate_items=candidate_items,
            batch_id=batch_id,
            correlation_id=correlation_id,
            ingest_source=IngestSource.NETWORK_SHARED_DRIVE.value,
        )

    # Sync bridge to the async router pipeline. Same asyncio-loop-lifecycle
    # pattern as tpm_notification._send_via_email_sender (with RUNTIMEERR-1
    # narrow-catch so coro-raised RuntimeErrors bubble up).
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            new_loop = asyncio.new_event_loop()
            try:
                result = new_loop.run_until_complete(_run())
            finally:
                new_loop.close()
        else:
            result = loop.run_until_complete(_run())
    except RuntimeError as exc:
        msg = str(exc).lower()
        if "no current event loop" not in msg and "event loop is closed" not in msg:
            raise
        new_loop = asyncio.new_event_loop()
        try:
            result = new_loop.run_until_complete(_run())
        finally:
            new_loop.close()

    _log.warning(
        "NSD2_POLL_INGEST: item=%s filename=%r file_hash=%s result=%s",
        delivery_item_id, filename, file_hash[:16],
        {k: (list(v) if isinstance(v, set) else v)
         for k, v in (result or {}).items()},
    )
