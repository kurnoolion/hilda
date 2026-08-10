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
        "correlation_id":      correlation_id,
        "scopes_scanned":      0,
        "hw_pl_items_scanned": 0,
        "items_folder_missing":  0,     # resolver returned None
        "items_walked":        0,
        "files_yielded":       0,
        "files_dedup_skipped": 0,
        "files_ingested":      0,
        "files_ingest_failed": 0,
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
    """Load HW PL items for this (customer, device, milestone), skipping
    items whose ingress_folder doesn't sit under any configured NSD2 root.
    For each remaining item: resolve device folder -> walk -> dedup +
    hand off new files."""
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
    for item in hw_pl_items:
        _poll_one_item(
            deps=deps,
            stats=stats,
            correlation_id=correlation_id,
            item=item,
            customer_id=customer_id,
            milestone_id=milestone_id,
            resolve_fn=resolve_fn,
            walk_fn=walk_fn,
        )


def _filter_hw_pl_nsd2_items(
    all_items: list[Any],
    device_id: str,
    nsd2_roots: list[Path],
) -> list[Any]:
    """Return items with tg_name='HW PL' AND matching device_id AND
    ingress_folder starting with (or equal to) one of the configured
    NSD2 roots. Ingress_folder match is done by case-insensitive
    normalized-string prefix -- SMB paths on Windows have mixed
    separator + case behavior; keep the check loose enough to survive
    round-trips through SP UI."""
    out: list[Any] = []
    normalized_roots = [_normalize_path_for_prefix(str(r)) for r in nsd2_roots]
    for it in all_items:
        if (getattr(it, "tg_name", None) or "").strip() != "HW PL":
            continue
        if (getattr(it, "device_id", None) or "").strip() != device_id.strip():
            continue
        raw_ingress = getattr(it, "ingress_folder", None) or ""
        if not raw_ingress.strip():
            continue
        norm = _normalize_path_for_prefix(raw_ingress)
        if any(norm.startswith(root) for root in normalized_roots):
            out.append(it)
    return out


def _normalize_path_for_prefix(p: str) -> str:
    """Lower-case + backslash-to-forward-slash for tolerant SMB path
    prefix compare. Strips trailing slashes so 'X/' and 'X' match."""
    return (p or "").replace("\\", "/").rstrip("/").lower()


def _poll_one_item(
    *,
    deps: Any,
    stats: dict[str, Any],
    correlation_id: str,
    item: Any,
    customer_id: str,
    milestone_id: str,
    resolve_fn: Any,
    walk_fn: Any,
) -> None:
    """Resolve device folder for this item, walk it, dedup by hash,
    ingest new files. All failures WARN-log + continue."""
    delivery_item_id = getattr(item, "delivery_item_id", "?")
    device_id = getattr(item, "device_id", "") or ""
    ingress_folder = getattr(item, "ingress_folder", "") or ""

    # Resolve device folder. The item's ingress_folder value IS the
    # NSD2 root the resolver should use as the base.
    try:
        device_folder = resolve_fn(item, Path(ingress_folder))
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "NSD2_POLL: resolve raised (should not happen) item=%s: %s: %s",
            delivery_item_id, type(exc).__name__, str(exc)[:120],
        )
        stats["items_folder_missing"] += 1
        return
    if device_folder is None:
        stats["items_folder_missing"] += 1
        return

    stats["items_walked"] += 1
    _log.warning(
        "NSD2_POLL: item=%s device=%s milestone=%s walking %s",
        delivery_item_id, device_id, milestone_id, device_folder,
    )

    for rel_path, file_bytes, file_hash in walk_fn(device_folder, customer_id):
        stats["files_yielded"] += 1

        # Dedup: skip if this file_hash already lives in document_index
        # (from any channel -- email, prior NSD2 tick, etc.).
        try:
            existing = deps.storage.get_document_index_row_by_hash(file_hash)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "NSD2_POLL: dedup lookup failed file_hash=%s item=%s: %s: %s "
                "-- treating as new (safe: router de-dups again downstream)",
                file_hash[:16], delivery_item_id,
                type(exc).__name__, str(exc)[:120],
            )
            existing = None
        if existing is not None:
            stats["files_dedup_skipped"] += 1
            continue

        # Hand off new file to router integration seam. NSD2-4 wires
        # the real router call; NSD2-3 leaves a stub that just logs +
        # counts so the poller shape is testable in isolation.
        try:
            _ingest_new_nsd2_file(
                deps=deps,
                item=item,
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
                "NSD2_POLL: ingest failed item=%s filename=%s file_hash=%s: %s: %s",
                delivery_item_id, rel_path, file_hash[:16],
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


def _iter_active_scopes(deps: Any):
    """Yield (customer_id, device_id, milestone_id) tuples over active
    (customer, device, milestone) combinations. Mirrors reconcile.py's
    _iter_tuples pattern -- source is template_lookup._CACHE."""
    from core.src.template_schema import template_lookup
    for customer_id, template in template_lookup._CACHE.items():  # noqa: SLF001
        devices = template.get("devices") or {}
        milestones = template.get("milestones") or {}
        for device_id in devices:
            for milestone_id in milestones:
                yield (customer_id, device_id, milestone_id)


def _ingest_new_nsd2_file(
    *,
    deps: Any,
    item: Any,
    customer_id: str,
    milestone_id: str,
    filename: str,
    content: bytes,
    file_hash: str,
    correlation_id: str,
) -> None:
    """NSD2-3 stub for the router-integration seam. NSD2-4 replaces this
    body with a call into the existing Fr52AttachmentRouter pipeline
    (or process_inbound_attachments) that:
      * Builds a synthetic attachment shape (filename preserves subfolder
        path per ZIP-1 pattern; ingest_source=NETWORK_SHARED_DRIVE).
      * Feeds it to the router scoped to tg_name='HW PL' candidate items
        for (customer_id, device_id, milestone_id).
      * Router handles doc_type classification, item association,
        document_index write, view-tree copy.

    For now: just log so the poller shape is proven independently."""
    delivery_item_id = getattr(item, "delivery_item_id", "?")
    _log.warning(
        "NSD2_POLL_INGEST_STUB: item=%s customer=%s milestone=%s filename=%r "
        "size=%d file_hash=%s correlation=%s -- NSD2-4 wires the real router",
        delivery_item_id, customer_id, milestone_id, filename,
        len(content), file_hash[:16], correlation_id,
    )
