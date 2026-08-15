"""PLM-3 (2026-08-14) -- beat-fired poll that creates PLM tickets per
owner, downloads their files, and ingests them through the existing
Fr52AttachmentRouter pipeline (same shape as NSD2-3/4).

Design in one sentence: for each (customer, device, milestone='P1') scope,
group MQL-FIT + MNO-SOLUTION items by owner_corp_email; per owner group,
create ONE PLM ticket if no plm_id exists yet (SP-first-with-Postgres-
fallback lookup), write plm_id + actual_item_info back to SP, download
all files attached to the ticket, dedup by hash, and route each new file
through the router with the owner's items as candidates.

Task never raises; each layer WARNs + moves on, per NSD2 poll convention.

Not handled here (deferred to PLM-6):
  * `close_plm_defect(case_id)` on milestone closure -- lives in the
    milestone-close cascade, not per-tick. Ticket stays open across
    ticks so redownloads catch new files.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Any

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = [
    "plm_poll_task",
    "poll_plm_once",
    "close_plm_defects_for_milestone",   # PLM-6 milestone-close hook
]

_log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Policy constants (module-level so tests can monkey-patch)
# ---------------------------------------------------------------------------


_PLM_MILESTONE_ID = "P1"

# tg_name values whose items participate in PLM ticket creation.
# Match is exact (case-sensitive after strip).
_PLM_ELIGIBLE_TG_NAMES = frozenset({"MQL-FIT", "MNO-SOLUTION"})

# delivery_state values that DISQUALIFY an item from PLM eligibility.
_PLM_TERMINAL_STATES = frozenset({"Closed", "Cancelled", "CloseInProgress"})

# PLM-7 (2026-08-14): TrackingModality enum value that opts an item into
# PLM polling. Mirrors TrackingModality.CORPORATE_PLM.value at
# core/src/template_schema/enums.py. Kept as module-level constant to
# avoid template_schema import at PLM module load time.
_PLM_MODALITY_VALUE = "CorporatePLM"


# ---------------------------------------------------------------------------
# Celery entry
# ---------------------------------------------------------------------------


@hilda_celery_app.task(
    bind=True,
    name="core.src.workflow_engine.tasks.plm_poll.tick",
    autoretry_for=(),  # never retry -- next beat tick reprocesses
    ignore_result=False,
)
def plm_poll_task(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Beat-fired entry. Delegates to poll_plm_once() which is unit-testable
    without a Celery runtime."""
    deps = get_task_deps()
    return poll_plm_once(deps)


# ---------------------------------------------------------------------------
# Pure-python core (testable without Celery)
# ---------------------------------------------------------------------------


def poll_plm_once(deps: Any) -> dict[str, Any]:
    """One poll tick. Iterates active (customer, device, P1) scopes; groups
    eligible items by owner_corp_email; per owner group, creates PLM ticket
    if needed, writes plm_id + actual_item_info back to SP, downloads files,
    ingests through router. Returns per-tick counters + correlation_id."""
    correlation_id = f"plmpoll-{uuid.uuid4().hex[:12]}"
    stats: dict[str, Any] = {
        "correlation_id":          correlation_id,
        "scopes_scanned":          0,
        "eligible_items":          0,
        "owner_groups":            0,
        "tickets_created":         0,
        "tickets_reused":          0,     # plm_id already present -> skip create
        "tickets_create_failed":   0,
        "sp_writes_ok":            0,
        "sp_writes_failed":        0,
        "downloads_ok":            0,
        "downloads_failed":        0,
        "files_yielded":           0,
        "files_dedup_skipped":     0,
        "files_ingested":          0,
        "files_ingest_failed":     0,
    }

    if deps is None or getattr(deps, "storage", None) is None:
        _log.warning("PLM_POLL: no deps/storage; skipping tick")
        stats["outcome"] = "no_deps"
        return stats

    _log.warning("PLM_POLL: tick start correlation_id=%s", correlation_id)

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
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "PLM_POLL: scope error customer=%s device=%s milestone=%s: %s: %s",
                customer_id, device_id, milestone_id,
                type(exc).__name__, str(exc)[:200],
            )

    stats["outcome"] = "fired"
    _log.warning("PLM_POLL: tick done %s", stats)
    return stats


# ---------------------------------------------------------------------------
# Scope iteration + per-scope grouping
# ---------------------------------------------------------------------------


def _iter_active_scopes(deps: Any):
    """Yield (customer_id, device_id, milestone_id='P1') tuples per NSD2-11
    P1-only convention -- same pattern as nsd2_poll._iter_active_scopes."""
    from core.src.template_schema import template_lookup
    for customer_id, template in template_lookup._CACHE.items():  # noqa: SLF001
        devices = template.get("devices") or {}
        milestones = template.get("milestones") or {}
        if _PLM_MILESTONE_ID not in milestones:
            continue
        for device_id in devices:
            yield (customer_id, device_id, _PLM_MILESTONE_ID)


def _poll_one_scope(
    *,
    deps: Any,
    stats: dict[str, Any],
    correlation_id: str,
    customer_id: str,
    device_id: str,
    milestone_id: str,
) -> None:
    """Load all items for (customer, device, milestone); keep MQL-FIT +
    MNO-SOLUTION items in non-terminal state; group by owner_corp_email;
    dispatch each owner group to _process_owner_group."""
    try:
        all_items = deps.storage.list_items_for_milestone(milestone_id, None) or []
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "PLM_POLL: list_items_for_milestone failed customer=%s device=%s "
            "milestone=%s: %s: %s -- skipping scope",
            customer_id, device_id, milestone_id,
            type(exc).__name__, str(exc)[:120],
        )
        return

    eligible = _filter_eligible_items(all_items, device_id)
    if not eligible:
        return

    stats["eligible_items"] += len(eligible)
    groups = _group_by_owner(eligible)
    stats["owner_groups"] += len(groups)

    for owner_email, owner_items in groups.items():
        _process_owner_group(
            deps=deps,
            stats=stats,
            correlation_id=correlation_id,
            customer_id=customer_id,
            device_id=device_id,
            milestone_id=milestone_id,
            owner_email=owner_email,
            items=owner_items,
        )


def _filter_eligible_items(all_items: list[Any], device_id: str) -> list[Any]:
    """PLM-7 (2026-08-14): gate on tracking_modality in addition to tg_name.

    Eligible when ALL apply:
      tg_name in ('MQL-FIT', 'MNO-SOLUTION')
      device_id matches scope
      'CorporatePLM' in tracking_modality  (list membership)
      delivery_state NOT in terminal states (Closed, Cancelled, CloseInProgress)

    Strict gate -- no fallback to tg-name-only. Items with missing
    tracking_modality are opted out (TPM must explicitly enable via the
    SP Choice column). P1 milestone is not TPM-live yet so no backward-
    compat concern (per architect 2026-08-14)."""
    out: list[Any] = []
    for it in all_items:
        tg = (getattr(it, "tg_name", None) or "").strip()
        if tg not in _PLM_ELIGIBLE_TG_NAMES:
            continue
        if (getattr(it, "device_id", None) or "").strip() != device_id.strip():
            continue
        state = (getattr(it, "delivery_state", None) or "").strip()
        if state in _PLM_TERMINAL_STATES:
            continue
        modality = getattr(it, "tracking_modality", None) or []
        if _PLM_MODALITY_VALUE not in modality:
            continue
        out.append(it)
    return out


def _group_by_owner(items: list[Any]) -> dict[str, list[Any]]:
    """Group items by owner_corp_email (case-insensitive after strip).
    Items with empty owner_corp_email are silently dropped -- there's no
    one to attribute the PLM to; a WARN is logged so ops can chase."""
    groups: dict[str, list[Any]] = {}
    for it in items:
        raw = getattr(it, "owner_corp_email", None) or ""
        key = raw.strip().lower()
        if not key:
            _log.warning(
                "PLM_POLL: item has empty owner_corp_email -- skipping "
                "delivery_item_id=%s tg=%s",
                _item_id(it), getattr(it, "tg_name", "?"),
            )
            continue
        groups.setdefault(key, []).append(it)
    return groups


# ---------------------------------------------------------------------------
# Per-owner group processing
# ---------------------------------------------------------------------------


def _process_owner_group(
    *,
    deps: Any,
    stats: dict[str, Any],
    correlation_id: str,
    customer_id: str,
    device_id: str,
    milestone_id: str,
    owner_email: str,
    items: list[Any],
) -> None:
    """For one owner group: resolve or create plm_id, write to SP, download
    files, ingest through router. All failures WARN + move on to next owner."""
    from core.src.issue_tracker.corp_plm import on_prem_client

    # Step 1: does any item already have plm_id populated? (Postgres check)
    existing_plm_id = _check_plm_id_state(deps, items, customer_id, device_id, milestone_id)

    if existing_plm_id:
        # Reuse existing ticket -- skip create, jump straight to download.
        stats["tickets_reused"] += 1
        plm_id = existing_plm_id
        _log.info(
            "PLM_POLL: reuse plm_id=%s owner=%s device=%s (%d items)",
            plm_id, owner_email, device_id, len(items),
        )
    else:
        # Create new ticket via on-prem script.
        payload = [(int(getattr(it, "item_no", 0) or 0),
                    str(getattr(it, "item_name", "") or "")) for it in items]
        case_id, url = on_prem_client.create_plm_ticket(device_id, payload)
        if not case_id:
            _log.warning(
                "PLM_POLL: create_plm_ticket returned empty for owner=%s "
                "device=%s (%d items) -- skipping group this tick",
                owner_email, device_id, len(items),
            )
            stats["tickets_create_failed"] += 1
            return
        stats["tickets_created"] += 1
        plm_id = case_id
        _log.warning(
            "PLM_POLL: created plm_id=%s url=%s owner=%s device=%s",
            case_id, url, owner_email, device_id,
        )

        # Step 2: write plm_id + actual_item_info back to SP for all items
        # in this group (D-164 Pattern A: SP-authoritative state-mirror).
        # SP CHANGED alert propagates to Postgres via _STR_FIELDS sync
        # (PLM-2). Wrapped per-item so one failure doesn't lose the rest.
        _write_plm_fields_to_sp(
            deps=deps, stats=stats,
            customer_id=customer_id, device_id=device_id, milestone_id=milestone_id,
            items=items, plm_id=case_id, url=url,
        )

    # Step 3: download files into a per-owner scratch dir, walk it, ingest.
    _download_and_ingest(
        deps=deps, stats=stats, correlation_id=correlation_id,
        customer_id=customer_id, milestone_id=milestone_id,
        plm_id=plm_id, items=items,
    )


def _check_plm_id_state(
    deps: Any, items: list[Any],
    customer_id: str, device_id: str, milestone_id: str,
) -> str:
    """Return existing plm_id for this owner group, or "" if none exists.

    Postgres-first: if any item.plm_id is populated, use it (all items in
    the group SHOULD share the same plm_id since they were created together).

    SP fallback: when Postgres is empty across all items, do a fresh SP
    read via deps.sp_writer.get_items(). Handles the CHANGED-alert-in-flight
    window where SP has the plm_id but Postgres hasn't caught up yet --
    prevents duplicate ticket creation.
    """
    # Postgres path
    for it in items:
        raw = getattr(it, "plm_id", None) or ""
        if raw.strip():
            return raw.strip()

    # SP fallback -- only if sp_writer wired
    if getattr(deps, "sp_writer", None) is None:
        return ""

    try:
        from core.src.sharepoint_integration.config import ListScope
        scope = ListScope(customer_id=customer_id)
        # Filter by ANY item's item_no -- they all share the same plm_id.
        first_item_no = int(getattr(items[0], "item_no", 0) or 0)
        rows = deps.sp_writer.get_items(
            entity="delivery_items",
            scope=scope,
            canonical_filters={
                "item_no":      first_item_no,
                "milestone_id": milestone_id,
                "project_model": device_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "PLM_POLL: SP fallback read failed customer=%s device=%s: %s: %s",
            customer_id, device_id, type(exc).__name__, str(exc)[:120],
        )
        return ""

    if not rows:
        return ""
    sp_plm_id = str(rows[0].get("plm_id") or "").strip()
    if sp_plm_id:
        _log.warning(
            "PLM_POLL: SP-fallback found plm_id=%s (Postgres empty) "
            "customer=%s device=%s",
            sp_plm_id, customer_id, device_id,
        )
    return sp_plm_id


def _write_plm_fields_to_sp(
    *,
    deps: Any,
    stats: dict[str, Any],
    customer_id: str,
    device_id: str,
    milestone_id: str,
    items: list[Any],
    plm_id: str,
    url: str,
) -> None:
    """Per-item SP write of plm_id + actual_item_info. Best-effort:
    failures are WARN-logged but don't stop the download step -- worst
    case, next tick's SP-fallback read still finds the plm_id in SP if
    at least one write succeeded, OR create_plm_ticket runs again and
    the on-prem script's own idempotency handles the duplicate."""
    if getattr(deps, "sp_writer", None) is None:
        _log.warning("PLM_POLL: sp_writer unwired -- skipping SP write of plm_id=%s", plm_id)
        return
    try:
        from core.src.sharepoint_integration.config import ListScope
    except Exception as exc:  # noqa: BLE001
        _log.warning("PLM_POLL: ListScope import failed: %s", exc)
        return

    scope = ListScope(customer_id=customer_id)
    for it in items:
        item_no = int(getattr(it, "item_no", 0) or 0)
        try:
            rows = deps.sp_writer.get_items(
                entity="delivery_items",
                scope=scope,
                canonical_filters={
                    "item_no":       item_no,
                    "milestone_id":  milestone_id,
                    "project_model": device_id,
                },
            )
            if not rows:
                _log.warning(
                    "PLM_POLL: SP-write no matching SP row item_no=%s device=%s",
                    item_no, device_id,
                )
                stats["sp_writes_failed"] += 1
                continue
            sp_id = str(rows[0].get("_sp_id") or "")
            if not sp_id:
                _log.warning("PLM_POLL: SP row missing _sp_id item_no=%s", item_no)
                stats["sp_writes_failed"] += 1
                continue
            deps.sp_writer.update_item(
                entity="delivery_items",
                scope=scope,
                item_id=sp_id,
                canonical_fields={"plm_id": plm_id, "actual_item_info": url},
            )
            stats["sp_writes_ok"] += 1
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "PLM_POLL: SP-write failed item_no=%s device=%s: %s: %s",
                item_no, device_id, type(exc).__name__, str(exc)[:200],
            )
            stats["sp_writes_failed"] += 1


def _download_and_ingest(
    *,
    deps: Any,
    stats: dict[str, Any],
    correlation_id: str,
    customer_id: str,
    milestone_id: str,
    plm_id: str,
    items: list[Any],
) -> None:
    """Download all files for `plm_id` into a fresh tmpdir; walk
    downloads/ subtree; dedup by hash; ingest through router with `items`
    as candidates. Always cleans up tmpdir."""
    from core.src.issue_tracker.corp_plm import on_prem_client

    work_dir = Path(tempfile.mkdtemp(prefix=f"plm-{plm_id}-"))
    try:
        rc = on_prem_client.list_and_download_all(plm_id, work_dir)
        if rc != 0:
            _log.warning(
                "PLM_POLL: download failed plm_id=%s (rc=%d) -- skipping ingest",
                plm_id, rc,
            )
            stats["downloads_failed"] += 1
            return
        stats["downloads_ok"] += 1

        downloads_dir = work_dir / "downloads"
        if not downloads_dir.exists():
            _log.warning(
                "PLM_POLL: no downloads/ subdir after successful download "
                "plm_id=%s cwd=%s -- script contract violation?",
                plm_id, work_dir,
            )
            return

        for file_path in sorted(downloads_dir.rglob("*")):
            if not file_path.is_file():
                continue
            stats["files_yielded"] += 1
            try:
                content = file_path.read_bytes()
            except OSError as exc:
                _log.warning(
                    "PLM_POLL: read failed plm_id=%s file=%s: %s",
                    plm_id, file_path, exc,
                )
                continue

            import hashlib
            file_hash = hashlib.sha256(content).hexdigest()

            try:
                existing = deps.storage.get_document_index_row_by_hash(file_hash)
            except Exception:  # noqa: BLE001
                existing = None
            if existing is not None:
                stats["files_dedup_skipped"] += 1
                continue

            rel_path = str(file_path.relative_to(downloads_dir)).replace("\\", "/")
            try:
                _ingest_new_plm_file(
                    deps=deps, items=items,
                    customer_id=customer_id, milestone_id=milestone_id,
                    filename=rel_path, content=content, file_hash=file_hash,
                    correlation_id=correlation_id, plm_id=plm_id,
                )
                stats["files_ingested"] += 1
            except Exception as exc:  # noqa: BLE001
                _log.warning(
                    "PLM_POLL: ingest failed plm_id=%s file=%s: %s: %s",
                    plm_id, rel_path, type(exc).__name__, str(exc)[:200],
                )
                stats["files_ingest_failed"] += 1
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Router integration (mirror of nsd2_poll._ingest_new_nsd2_file)
# ---------------------------------------------------------------------------


def _ingest_new_plm_file(
    *,
    deps: Any,
    items: list[Any],
    customer_id: str,
    milestone_id: str,
    filename: str,
    content: bytes,
    file_hash: str,
    correlation_id: str,
    plm_id: str,
) -> None:
    """Feed one downloaded PLM file through the existing Fr52 router with
    the owner's items as candidates. batch_id encodes plm_id for
    audit/log correlation."""
    import asyncio

    from core.src.email_service.protocol import InboundAttachment
    from core.src.template_schema.enums import IngestSource
    from core.src.workflow_engine.tasks.inbound_attachment import (
        _build_ph1_router,
        _process_regular_attachment,
        _widen_candidates_for_router,
    )

    delivery_item_ids = [i for i in (_item_id(it) for it in items) if i]
    batch_id = f"PLM-{plm_id}"

    attachment = InboundAttachment(
        filename=filename,
        content=content,
        content_type="application/octet-stream",
        file_hash=file_hash,
    )

    async def _run() -> dict[str, Any]:
        candidate_items = _widen_candidates_for_router(
            deps,
            [{"delivery_item_id": iid} for iid in delivery_item_ids],
        )
        if not candidate_items:
            _log.warning(
                "PLM_POLL_INGEST: widen returned empty items=%s -- skipping file",
                delivery_item_ids[:3],
            )
            return {"processed": 0}
        router = _build_ph1_router(deps, customer_id=customer_id)
        if router is None:
            _log.warning(
                "PLM_POLL_INGEST: router unavailable customer=%s items=%s -- skipping",
                customer_id, delivery_item_ids[:3],
            )
            return {"processed": 0}
        return await _process_regular_attachment(
            deps=deps, router=router, attachment=attachment,
            candidate_items=candidate_items,
            batch_id=batch_id, correlation_id=correlation_id,
            ingest_source=IngestSource.CORPORATE_PLM.value,
        )

    # Sync bridge to the async router pipeline. Same asyncio-loop-lifecycle
    # pattern as tpm_notification / nsd2_poll (with RUNTIMEERR-1 narrow catch).
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
        "PLM_POLL_INGEST: plm_id=%s filename=%r file_hash=%s result=%s",
        plm_id, filename, file_hash[:16],
        {k: (list(v) if isinstance(v, set) else v)
         for k, v in (result or {}).items()},
    )


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _item_id(it: Any) -> str:
    """DeliveryItemBase Pydantic exposes the ID as `item_id`; the SA table
    column is `delivery_item_id`. Try both -- matches NSD2 fix."""
    return (
        getattr(it, "item_id", None)
        or getattr(it, "delivery_item_id", None)
        or ""
    )


# ---------------------------------------------------------------------------
# PLM-6 (2026-08-14) -- milestone-close hook
# ---------------------------------------------------------------------------
#
# When the P1 milestone is deleted / closed by the TPM, HILDA must close the
# PLM tickets it created during that milestone's lifetime so PLM doesn't
# accumulate orphan open tickets. Called from apply_milestone_delete_task
# BEFORE the Postgres cascade wipes delivery_items (we need to read their
# plm_id values first).
#
# Semantic: "milestone closure" here == "SP Milestones-list row deleted".
# There's no separate `milestone_state=Closed` transition in this codebase
# today; the TPM signals milestone-end via delete. If a stronger "milestone
# finalized but preserved" state ever exists (Ph-2), add a second call site.
#
# Design: read all plm_ids for the scope, dedup, call close_plm_defect for
# each unique one. Errors are WARN-logged + counted; deletion cascade
# continues regardless (PLM ticket cleanup is best-effort -- if PLM is
# unreachable, the milestone-delete still needs to complete).


def close_plm_defects_for_milestone(
    deps: Any,
    customer_id: str,
    device_id: str,
    milestone_id: str,
) -> dict[str, Any]:
    """Close all PLM tickets referenced by delivery_items in the given
    (customer, device, milestone) scope. Idempotent per-plm_id (repeated
    calls to close_plm_defect on an already-closed ticket return -1 which
    we count but don't raise on).

    Returns per-scope counters:
      plm_ids_found         -- distinct non-empty plm_ids in scope
      plm_ids_closed        -- close_plm_defect returned 0
      plm_ids_close_failed  -- close_plm_defect returned non-zero (WARN logged)

    MUST be called BEFORE storage.delete_milestone_cascade() -- once items
    are deleted, plm_ids are gone and we can't reach the tickets.

    Safe to invoke unconditionally: returns zeros when no items or no
    plm_ids exist for the scope.
    """
    from core.src.issue_tracker.corp_plm import on_prem_client

    result = {
        "plm_ids_found":        0,
        "plm_ids_closed":       0,
        "plm_ids_close_failed": 0,
    }

    if deps is None or getattr(deps, "storage", None) is None:
        _log.warning("PLM_CLOSE: no deps/storage; skipping scope=%s/%s/%s",
                     customer_id, device_id, milestone_id)
        return result

    try:
        items = deps.storage.list_items_for_milestone(milestone_id, None) or []
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "PLM_CLOSE: list_items_for_milestone failed %s/%s/%s: %s: %s "
            "-- skipping close",
            customer_id, device_id, milestone_id,
            type(exc).__name__, str(exc)[:120],
        )
        return result

    # Collect UNIQUE plm_ids scoped to this device (list_items_for_milestone
    # returns all devices' items in the milestone; filter by device_id here
    # to avoid closing tickets owned by other devices in the same milestone).
    plm_ids: set[str] = set()
    for it in items:
        it_device = (getattr(it, "device_id", None) or "").strip()
        if it_device != device_id.strip():
            continue
        raw = getattr(it, "plm_id", None) or ""
        pid = raw.strip()
        if pid:
            plm_ids.add(pid)

    result["plm_ids_found"] = len(plm_ids)
    if not plm_ids:
        return result

    _log.warning(
        "PLM_CLOSE: milestone-close hook %s/%s/%s -- %d unique plm_id(s) to close",
        customer_id, device_id, milestone_id, len(plm_ids),
    )
    for pid in sorted(plm_ids):
        try:
            rc = on_prem_client.close_plm_defect(pid)
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "PLM_CLOSE: close_plm_defect raised plm_id=%s: %s: %s",
                pid, type(exc).__name__, str(exc)[:200],
            )
            result["plm_ids_close_failed"] += 1
            continue
        if rc == 0:
            result["plm_ids_closed"] += 1
        else:
            _log.warning("PLM_CLOSE: close_plm_defect returned %d for plm_id=%s", rc, pid)
            result["plm_ids_close_failed"] += 1
    return result
