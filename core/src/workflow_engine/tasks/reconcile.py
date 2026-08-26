"""reconcile.py -- meta-reconciler + 5 sync sub-tasks for missed SP alert emails.

Anchors [D-142] 5-sync reconciliation architecture + [D-143] SP-alerts-are-best-effort.

Design (strand `reconcile-sync-cascade`):
  - SINGLE Celery beat entry `reconcile_all_task` fires every N minutes (default 5).
  - Task iterates (customer x device x milestone) tuples SERIALLY (per user Q1).
  - For each tuple, dispatches 5 sync sub-tasks IN ORDER:
      sync-1 delivery_item_count      -- backfill missing Deliverable rows
      sync-2 milestone-start-collection -- retry kickoff when all still NS
      sync-3 deliverable-approved      -- per-item PM-approval mirror
      sync-4 milestone-submit-to-carrier -- retry submit when all still RFS
      sync-5 milestone-close-all-items -- retry close when all still SubmittedToCustomer
  - No retry limits; task naturally no-ops per tick when predicates broken.
  - trigger_source="sync_backfill_*" so guards (D-140 pattern) trust the reconciler.
  - Terminate on convergence == task returns cleanly with all predicates unmet.

Sync-1 special: fires while SP `milestone_submission_triggered_at IS NULL` (i.e.,
until TPM clicks Submit-to-Carrier). Catches both initial-burst ADDED alerts AND
delayed ADDED alerts trickling in mid-collection.

Sync-2/4/5: fire ONLY when ALL items still in the pre-transition state (per user
Q2/Q4/Q5). Rationale: if even 1 item transitioned, the email was received; existing
flow guarantees eventual processing of remaining items via its own path. Reconciler
is a missed-EMAIL safety net, not a per-item catch-up.

Sync-3 (per-item): PM approval is per-item; reconciler covers each item
individually because approvals are staggered by definition.

Open risks acknowledged for Ph-1 (see STRAND.md OQ-1, OQ-2):
  - Late-arriving ADDED alert post-kickoff -> item stays in Not Started forever
    (no sync-2b catch-up variant Ph-1).
  - Late-arriving CHANGED alert mid-batch on pm_approval / submit / close -> may
    leave one item asymmetric until TPM re-triggers.

SP Milestones is GLOBAL per architect Q5 lock 2026-07-02; Deliverables + Projects
are per-customer (Deliverables_<customer_id> / Projects_<customer_id>).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from core.src.workflow_engine.celery_app import hilda_celery_app
from core.src.workflow_engine.reconcile_config import ReconcileConfig
from core.src.workflow_engine.task_deps import get_task_deps

__all__ = ["reconcile_all_task"]

_log = logging.getLogger(__name__)


# -- State constants (mirror the existing task file constants) ------------------
_STATE_NOT_STARTED         = "Not Started"
_STATE_OPEN                 = "Open"
_STATE_READY_FOR_SUBMISSION = "ReadyForSubmission"
_STATE_UNDER_PM_REVIEW      = "UnderPMReview"
_STATE_SUBMITTED_TO_CUSTOMER = "SubmittedToCustomer"
_STATE_CLOSED               = "Closed"
_STATE_CLOSE_IN_PROGRESS    = "CloseInProgress"


@hilda_celery_app.task(
    name="core.src.workflow_engine.tasks.reconcile.reconcile_all",
    ignore_result=True,   # beat-fired; no result caching needed
)
def reconcile_all_task(
    params: dict[str, Any] | None = None,
    event_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Meta-reconciler entry point -- one beat entry per architect Q1 lock.

    Iterates (customer x device x milestone) tuples serially; for each, dispatches
    the 5 sync sub-tasks in order. Each sub-task decides fire/no-op based on its
    own predicate; the meta task's job is orchestration + accumulation.
    """
    cfg = ReconcileConfig.from_sources()
    if not cfg.enabled:
        return {"outcome": "disabled"}

    deps = get_task_deps()
    if deps.sp_writer is None or deps.storage is None:
        _log.warning("reconcile_all_skip_no_deps: sp_writer or storage None")
        return {"outcome": "skipped_no_deps"}

    stats: dict[str, int] = {
        "milestones_scanned":         0,
        "sync_1_backfilled":          0,
        "sync_1_skipped":             0,
        "sync_2_dispatched":          0,
        "sync_2_skipped":             0,
        "sync_3_dispatched":          0,
        "sync_3_skipped":             0,
        "sync_4_dispatched":          0,
        "sync_4_skipped":             0,
        "sync_5_dispatched":          0,
        "sync_5_skipped":             0,
        "sync_6_advanced":            0,   # CIP-4 stuck-CloseInProgress sweeper
        "sync_6_skipped":             0,
        "sync_7_routed":              0,   # SYNC7-1 retry-unrouted promoted
        "sync_7_multi_match":         0,   # SYNC7-1 ambiguous (skipped)
        "sync_7_no_match":            0,   # SYNC7-1 no candidate matched (skipped)
        "sync_7_skipped":             0,
    }

    correlation_id = f"reconcile-{uuid.uuid4().hex[:12]}"

    for customer_id, device_id, milestone_id, milestone_name in _iter_tuples(deps):
        stats["milestones_scanned"] += 1
        # Read the SP Milestone row once per tuple -- shared by syncs 2/4/5.
        sp_milestone = _sp_read_milestone(deps, customer_id, device_id, milestone_name)
        # sync-1 needs milestone_submission_triggered_at from sp_milestone AND SP Deliverables list.
        try:
            _sync_1_delivery_item_count(
                deps, cfg, stats, correlation_id,
                customer_id, device_id, milestone_id, sp_milestone,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("sync_1_error: milestone=%s: %s", milestone_id, type(exc).__name__)
            stats["sync_1_skipped"] += 1
        try:
            _sync_2_start_collection(
                deps, cfg, stats, correlation_id,
                customer_id, device_id, milestone_id, sp_milestone,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("sync_2_error: milestone=%s: %s", milestone_id, type(exc).__name__)
            stats["sync_2_skipped"] += 1
        try:
            _sync_3_pm_approval(
                deps, cfg, stats, correlation_id,
                customer_id, device_id, milestone_id,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("sync_3_error: milestone=%s: %s", milestone_id, type(exc).__name__)
            stats["sync_3_skipped"] += 1
        try:
            _sync_4_submit_to_carrier(
                deps, cfg, stats, correlation_id,
                customer_id, device_id, milestone_id, sp_milestone,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("sync_4_error: milestone=%s: %s", milestone_id, type(exc).__name__)
            stats["sync_4_skipped"] += 1
        try:
            _sync_5_close_all_items(
                deps, cfg, stats, correlation_id,
                customer_id, device_id, milestone_id, sp_milestone,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("sync_5_error: milestone=%s: %s", milestone_id, type(exc).__name__)
            stats["sync_5_skipped"] += 1
        try:
            _sync_6_close_in_progress(
                deps, cfg, stats, correlation_id,
                customer_id, device_id, milestone_id,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("sync_6_error: milestone=%s: %s", milestone_id, type(exc).__name__)
            stats["sync_6_skipped"] += 1
        try:
            _sync_7_retry_unrouted(
                deps, cfg, stats, correlation_id,
                customer_id, device_id, milestone_id,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning("sync_7_error: milestone=%s: %s", milestone_id, type(exc).__name__)
            stats["sync_7_skipped"] += 1

    _log.info("reconcile_all: %s", stats)
    return {"outcome": "fired", "correlation_id": correlation_id, **stats}


# ---------------------------------------------------------------------------
# Tuple iteration -- (customer x device x milestone) from template.yaml
# ---------------------------------------------------------------------------


def _iter_tuples(deps: Any):
    """Yield (customer_id, device_id, milestone_id, milestone_name) tuples per
    architect Q1 lock: serial per customer, per device, per milestone.

    Source of truth: template_lookup cache (loaded at bootstrap). No SP round
    trip to enumerate customers.
    """
    from core.src.template_schema import template_lookup
    for customer_id, template in template_lookup._CACHE.items():  # noqa: SLF001
        devices = template.get("devices") or {}
        milestones = template.get("milestones") or {}
        if not isinstance(milestones, dict) or not isinstance(devices, dict):
            continue
        for milestone_id, milestone in milestones.items():
            if not isinstance(milestone, dict):
                continue
            # Milestone-level `devices:` list is optional. Per FR-40 the same
            # work-items apply across every device in the customer template, so
            # a milestone that omits `devices:` (MMK convention 2026-07-30)
            # falls back to the full top-level `devices` dict. Explicit
            # per-milestone scope still honored for customers that use it.
            scope = milestone.get("devices")
            if isinstance(scope, list):
                device_ids = [d for d in scope if d in devices]
            else:
                device_ids = list(devices.keys())
            for device_id in device_ids:
                yield customer_id, device_id, milestone_id, milestone_id


# ---------------------------------------------------------------------------
# SP-read helpers
# ---------------------------------------------------------------------------


def _sp_read_milestone(
    deps: Any, customer_id: str, device_id: str, milestone_name: str,
) -> dict[str, Any] | None:
    """SP Milestones is GLOBAL per architect Q5 lock 2026-07-02. Filter by the
    (carrier, project_model, Title=milestone_name) triple to disambiguate.
    """
    from core.src.sharepoint_integration.config import ListScope
    try:
        rows = deps.sp_writer.get_items(
            entity="milestones",
            scope=ListScope(customer_id=customer_id),
            canonical_filters={
                "carrier":       customer_id,
                "project_model": device_id,
                "milestone_id":  milestone_name,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "reconcile_sp_read_milestone_failed: customer=%s device=%s milestone=%s: %s",
            customer_id, device_id, milestone_name, type(exc).__name__,
        )
        return None
    if not rows:
        return None
    return rows[0]


def _sp_read_delivery_items(
    deps: Any, customer_id: str, device_id: str, milestone_id: str,
) -> list[dict[str, Any]]:
    """Deliverables is per-customer (Deliverables_<customer_id>). Filter by
    (project_model=device_id, milestone_id) to scope to the tuple.
    """
    from core.src.sharepoint_integration.config import ListScope
    try:
        return deps.sp_writer.get_items(
            entity="delivery_items",
            scope=ListScope(customer_id=customer_id),
            canonical_filters={
                "project_model": device_id,
                "milestone_id":  milestone_id,
            },
        ) or []
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "reconcile_sp_read_deliverables_failed: customer=%s device=%s milestone=%s: %s",
            customer_id, device_id, milestone_id, type(exc).__name__,
        )
        return []


def _sp_read_delivery_items_count(
    deps: Any, customer_id: str, device_id: str, milestone_id: str,
) -> int | None:
    """RECON-1 (2026-07-30): count-first probe for sync-1. Returns the number
    of rows in SP Deliverables_<customer> filtered by (project_model,
    milestone_id) WITHOUT paying the full-row payload cost.

    Uses the same get_items() call as the full read but the underlying SP
    HTTP surface should support $top=1 + inline-count. If get_items doesn't
    expose count-only mode, this falls back to the full-row read (no
    optimization gain but correctness preserved). Returns None on read
    failure (caller skips this tick).
    """
    from core.src.sharepoint_integration.config import ListScope
    # Try count-only shape first. If sp_writer.get_items supports a
    # count_only=True kwarg it'll return an int; otherwise it'll return
    # a list and we take len(). Both shapes are handled.
    try:
        result = deps.sp_writer.get_items(
            entity="delivery_items",
            scope=ListScope(customer_id=customer_id),
            canonical_filters={
                "project_model": device_id,
                "milestone_id":  milestone_id,
            },
            count_only=True,
        )
    except TypeError:
        # get_items doesn't accept count_only -- fall back to full-row read.
        result = _sp_read_delivery_items(deps, customer_id, device_id, milestone_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "reconcile_sp_count_failed: customer=%s device=%s milestone=%s: %s",
            customer_id, device_id, milestone_id, type(exc).__name__,
        )
        return None
    if isinstance(result, int):
        return result
    if isinstance(result, list):
        return len(result)
    return None


def _elapsed_seconds(sp_timestamp_iso: Any) -> float | None:
    """Parse SP timestamp string; return seconds elapsed from that time to now
    (UTC per architect Q2 lock 2026-07-02). Returns None on parse failure.
    """
    if not sp_timestamp_iso or not isinstance(sp_timestamp_iso, str):
        return None
    try:
        # SP typically returns ISO 8601; also accept trailing Z.
        s = sp_timestamp_iso.rstrip("Z")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        return (now - dt).total_seconds()
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# sync-1: delivery_item_count backfill
# ---------------------------------------------------------------------------


def _sync_1_delivery_item_count(
    deps: Any, cfg: ReconcileConfig, stats: dict[str, int], correlation_id: str,
    customer_id: str, device_id: str, milestone_id: str,
    sp_milestone: dict[str, Any] | None,
) -> None:
    """Compare SP Deliverables natural-key set vs Postgres set for this
    (customer, device, milestone) tuple. Backfill missing rows.

    Guard: SP Milestone row's `milestone_submission_triggered_at` IS NULL (i.e.,
    TPM has NOT yet clicked Submit-to-Carrier). Once submit is clicked, item
    count is frozen; no more backfill needed.

    Ignores TPM add/delete per user Q1 lock -- reverse drift (Postgres row with
    no SP row) NOT handled Ph-1.
    """
    sub_cfg = cfg.sync_1_delivery_item_count
    if not sub_cfg.enabled:
        return
    if sp_milestone is None:
        stats["sync_1_skipped"] += 1
        return
    if sp_milestone.get("milestone_submission_triggered_at"):
        return  # submit already clicked; count is frozen

    # RECON-1 (2026-07-30): count-first optimization. Fetch Postgres count +
    # SP count first; only do the full-row SP fetch on mismatch. Saves the
    # 87-row payload transfer on every tick when scope is already in sync
    # (the steady-state common case).
    pg_items_probe = deps.storage.list_items_for_milestone(milestone_id, None) or []
    pg_count = sum(
        1 for it in pg_items_probe
        if getattr(it, "device_id", None) == device_id
    )
    sp_count = _sp_read_delivery_items_count(
        deps, customer_id, device_id, milestone_id,
    )
    if sp_count is None:
        stats["sync_1_skipped"] += 1
        return
    if sp_count == 0:
        return  # no items in SP either; nothing to backfill
    if pg_count >= sp_count:
        # In sync (or Postgres has more -- reverse drift is not handled Ph-1
        # per user Q1 lock). Skip the full-row fetch.
        return

    # Mismatch -- fetch full rows to know which specific item_no's to backfill.
    sp_items = _sp_read_delivery_items(deps, customer_id, device_id, milestone_id)
    if not sp_items:
        return  # racy: count said N but read returned 0; skip this tick

    sp_by_item_no: dict[int, dict[str, Any]] = {}
    for r in sp_items:
        v = r.get("item_no")
        if v is None:
            continue
        try:
            sp_by_item_no[int(v)] = r
        except (TypeError, ValueError):
            continue

    pg_items = deps.storage.list_items_for_milestone(milestone_id, None) or []
    pg_item_nos: set[int] = set()
    for it in pg_items:
        # filter by device_id -- list_items_for_milestone is milestone-scoped only
        it_device = getattr(it, "device_id", None)
        if it_device != device_id:
            continue
        it_no = getattr(it, "item_no", None)
        if it_no is None:
            continue
        try:
            pg_item_nos.add(int(it_no))
        except (TypeError, ValueError):
            continue

    missing = set(sp_by_item_no.keys()) - pg_item_nos
    if not missing:
        return  # in sync

    from core.src.workflow_engine.tasks.sp_alert_imports import (
        import_deliverable_tracker_task,
    )
    for item_no in sorted(missing):
        sp_row = sp_by_item_no[item_no]
        # Synthesize an ADDED-alert event_context. body_kvs comes from the SP row
        # canonical-field-out dict; routing_key + item_title derived similarly.
        body_kvs = {k: str(v) for k, v in sp_row.items() if v is not None and k != "_sp_id"}
        # Ensure identity fields are present (SP row may not carry them explicitly).
        body_kvs.setdefault("item_no",       str(item_no))
        body_kvs.setdefault("project_model", device_id)
        body_kvs.setdefault("milestone_id",  milestone_id)
        event_ctx = {
            "sub_trigger":    "added",
            "customer_id":    customer_id,
            "milestone_id":   milestone_id,
            "correlation_id": correlation_id,
            "trigger_source": "sync_backfill_ingest",
            "derived_fields": {
                "body_kvs":    body_kvs,
                "routing_key": {
                    "list_name":     "Deliverables",
                    "list_suffix":   customer_id,
                    "milestone_name": milestone_id,
                    "item_number":   item_no,
                },
                "item_title":  sp_row.get("Title", body_kvs.get("item_name", f"Item {item_no}")),
            },
        }
        try:
            result = import_deliverable_tracker_task.apply(
                args=({}, event_ctx), throw=False,
            )
            outcome = (result.result or {}).get("outcome") if result and result.result else None
            if outcome == "imported":
                stats["sync_1_backfilled"] += 1
                _audit(deps, "sync_1_backfilled", None, {
                    "customer_id":    customer_id,
                    "device_id":      device_id,
                    "milestone_id":   milestone_id,
                    "item_no":        item_no,
                    "correlation_id": correlation_id,
                })
            else:
                stats["sync_1_skipped"] += 1
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "sync_1_backfill_failed: customer=%s milestone=%s item_no=%s: %s",
                customer_id, milestone_id, item_no, type(exc).__name__,
            )
            stats["sync_1_skipped"] += 1


# ---------------------------------------------------------------------------
# sync-2: milestone-start-collection retry
# ---------------------------------------------------------------------------


def _sync_2_start_collection(
    deps: Any, cfg: ReconcileConfig, stats: dict[str, int], correlation_id: str,
    customer_id: str, device_id: str, milestone_id: str,
    sp_milestone: dict[str, Any] | None,
) -> None:
    """Fire kickoff_collection_task ONLY when:
      - SP `milestone_collection_started_at` set >5min ago, AND
      - ALL items in this (device, milestone) still in Not Started.

    Per user Q2 lock: if any single item advanced past Not Started, the kickoff
    email was received; existing flow guarantees eventual kick-off of remaining.
    """
    sub_cfg = cfg.sync_2_start_collection
    if not sub_cfg.enabled or sp_milestone is None:
        return
    ts = sp_milestone.get("milestone_collection_started_at")
    if not ts:
        return
    elapsed = _elapsed_seconds(ts)
    if elapsed is None or elapsed < sub_cfg.elapsed_threshold_sec:
        return

    pg_items = deps.storage.list_items_for_milestone(milestone_id, None) or []
    pg_items = [it for it in pg_items if getattr(it, "device_id", None) == device_id]
    if not pg_items:
        return
    # RECON-4 (2026-07-30): predicate was "all items still in OPEN" (RECON-1
    # 2026-07-30 morning) but user reported case where TPM manually closed
    # 2 items BEFORE Start-Collection was clicked -- 2 in Closed + 84 in Open,
    # so all-open predicate never matches and sync-2 doesn't fire. Manual
    # closure is not evidence of kickoff processing (kickoff writes
    # OutreachSent, not Closed). Correct predicate: no item is in a state
    # reachable ONLY via kickoff (OutreachSent or later along the collection
    # path, plus the owner-holding states Delayed/Blocked which are reachable
    # only from OutreachSent onwards per DeliveryState.CLOSE_IN_PROGRESS
    # docstring). Closed items are TPM-manual overrides and ignored. Requires
    # at least one Open item so we don't fire on a milestone where every item
    # is TPM-closed.
    _KICKOFF_EVIDENCE_STATES = frozenset({
        "OutreachSent", "DocumentReceived", "OwnerClosed", "UnderPMReview",
        "ReadyForSubmission", "SubmittedToCustomer", "CloseInProgress",
        "Delayed", "Blocked",
    })
    states = [(getattr(it, "delivery_state", None) or "") for it in pg_items]
    if any(s in _KICKOFF_EVIDENCE_STATES for s in states):
        return  # kickoff email was received -- existing flow handles the rest
    if not any(s == _STATE_OPEN for s in states):
        return  # nothing waiting on kickoff (all Closed / NotStarted)

    from core.src.workflow_engine.tasks.sp_alert_imports import (
        kickoff_collection_task,
    )
    event_ctx = {
        "customer_id":    customer_id,
        "device_id":      device_id,
        "milestone_id":   milestone_id,
        "correlation_id": correlation_id,
        "trigger_source": "sync_backfill_kickoff",
    }
    try:
        kickoff_collection_task.apply(args=({}, event_ctx), throw=False)
        stats["sync_2_dispatched"] += 1
        _audit(deps, "sync_2_dispatched", None, {
            "customer_id":    customer_id,
            "device_id":      device_id,
            "milestone_id":   milestone_id,
            "elapsed_sec":    int(elapsed),
            "correlation_id": correlation_id,
        })
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "sync_2_dispatch_failed: customer=%s milestone=%s: %s",
            customer_id, milestone_id, type(exc).__name__,
        )
        stats["sync_2_skipped"] += 1


# ---------------------------------------------------------------------------
# sync-3: deliverable-approved per-item mirror
# ---------------------------------------------------------------------------


def _sync_3_pm_approval(
    deps: Any, cfg: ReconcileConfig, stats: dict[str, int], correlation_id: str,
    customer_id: str, device_id: str, milestone_id: str,
) -> None:
    """Per-item: for each Postgres item in UnderPMReview, SP-READ the SP row and
    check if SP shows delivery_state=ReadyForSubmission + pm_approval_at set +
    >5min elapsed. If so, mirror the 3-tuple to Postgres via apply_pm_approval.

    Companion to D-139 Pattern A email path -- reconciler covers missed CHANGED
    alerts for PM approve.
    """
    sub_cfg = cfg.sync_3_pm_approval
    if not sub_cfg.enabled:
        return
    pg_items = deps.storage.list_items_for_milestone(milestone_id, None) or []
    pg_items = [
        it for it in pg_items
        if getattr(it, "device_id", None) == device_id
        and (getattr(it, "delivery_state", None) or "") == _STATE_UNDER_PM_REVIEW
    ]
    if not pg_items:
        return

    sp_items = _sp_read_delivery_items(deps, customer_id, device_id, milestone_id)
    sp_by_item_no: dict[int, dict[str, Any]] = {}
    for r in sp_items:
        v = r.get("item_no")
        if v is None:
            continue
        try:
            sp_by_item_no[int(v)] = r
        except (TypeError, ValueError):
            continue

    from core.src.workflow_engine.tasks.pm_approval import apply_pm_approval_task
    for it in pg_items:
        it_no = getattr(it, "item_no", None)
        it_id = getattr(it, "item_id", None) or getattr(it, "delivery_item_id", None)
        if it_no is None or it_id is None:
            continue
        try:
            it_no_int = int(it_no)
        except (TypeError, ValueError):
            continue
        sp_row = sp_by_item_no.get(it_no_int)
        if sp_row is None:
            continue
        sp_approval_at = sp_row.get("pm_approval_at")
        # RECON-1 (2026-07-30): SP UI Approve button no longer writes
        # delivery_state=RFS (SP UI engineer confirmed 2026-07-30 -- waits
        # on HILDA to drive the state advance). Prior predicate required
        # both sp_state==RFS AND sp_approval_at to fire; the sp_state
        # check would never match -> sync-3 never fired. Correct predicate:
        # pm_approval_at is set + elapsed >threshold. Postgres-side check
        # (item still in UnderPMReview) is unchanged and remains the
        # "did the normal Pattern A email path already handle it?" gate.
        if not sp_approval_at:
            continue
        elapsed = _elapsed_seconds(sp_approval_at)
        if elapsed is None or elapsed < sub_cfg.elapsed_threshold_sec:
            continue

        event_ctx = {
            "customer_id":      customer_id,
            "delivery_item_id": it_id,
            "milestone_id":     milestone_id,
            "correlation_id":   correlation_id,
            "trigger_source":   "sync_backfill_pm_approval",
            "derived_fields": {
                "body_kvs": {
                    # SP UI no longer writes delivery_state on approve; only
                    # pm_approval_at + pm_approval_pm_id. HILDA drives RFS.
                    "pm_approval_at":     str(sp_approval_at),
                    "pm_approval_pm_id":  str(sp_row.get("pm_approval_pm_id") or ""),
                },
            },
        }
        try:
            apply_pm_approval_task.apply(args=({}, event_ctx), throw=False)
            stats["sync_3_dispatched"] += 1
            _audit(deps, "sync_3_dispatched", it_id, {
                "customer_id":    customer_id,
                "milestone_id":   milestone_id,
                "item_no":        it_no_int,
                "elapsed_sec":    int(elapsed),
                "correlation_id": correlation_id,
            })
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "sync_3_dispatch_failed: customer=%s item=%s: %s",
                customer_id, it_id, type(exc).__name__,
            )
            stats["sync_3_skipped"] += 1


# ---------------------------------------------------------------------------
# sync-4: milestone-submit-to-carrier retry
# ---------------------------------------------------------------------------


def _sync_4_submit_to_carrier(
    deps: Any, cfg: ReconcileConfig, stats: dict[str, int], correlation_id: str,
    customer_id: str, device_id: str, milestone_id: str,
    sp_milestone: dict[str, Any] | None,
) -> None:
    """Fire submit_to_carrier_task ONLY when:
      - SP `milestone_submission_triggered_at` set >5min ago, AND
      - ALL items in this (device, milestone) still in ReadyForSubmission
        (i.e., NO items reached SubmittedToCustomer).

    Per user Q4 lock: if any single item transitioned to SubmittedToCustomer,
    the submit email was received; existing submit_to_carrier_task guarantees
    eventual processing of remaining items via its own state-filter guard.
    """
    sub_cfg = cfg.sync_4_submit_to_carrier
    if not sub_cfg.enabled or sp_milestone is None:
        return
    ts = sp_milestone.get("milestone_submission_triggered_at")
    if not ts:
        return
    elapsed = _elapsed_seconds(ts)
    if elapsed is None or elapsed < sub_cfg.elapsed_threshold_sec:
        return

    pg_items = deps.storage.list_items_for_milestone(milestone_id, None) or []
    pg_items = [it for it in pg_items if getattr(it, "device_id", None) == device_id]
    if not pg_items:
        return

    # Predicate: ALL items still in RFS. If any is SubmittedToCustomer, exit.
    for it in pg_items:
        state = getattr(it, "delivery_state", None) or ""
        if state == _STATE_SUBMITTED_TO_CUSTOMER:
            return  # submit email was received; existing flow handles rest
    # None reached SubmittedToCustomer -- fire submit for the whole milestone.

    from core.src.workflow_engine.tasks.submit_to_carrier import submit_to_carrier_task
    event_ctx = {
        "customer_id":    customer_id,
        "milestone_id":   milestone_id,
        "correlation_id": correlation_id,
        "trigger_source": "sync_backfill_submit_to_carrier",
    }
    try:
        submit_to_carrier_task.apply(args=({}, event_ctx), throw=False)
        stats["sync_4_dispatched"] += 1
        _audit(deps, "sync_4_dispatched", None, {
            "customer_id":    customer_id,
            "device_id":      device_id,
            "milestone_id":   milestone_id,
            "elapsed_sec":    int(elapsed),
            "correlation_id": correlation_id,
        })
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "sync_4_dispatch_failed: customer=%s milestone=%s: %s",
            customer_id, milestone_id, type(exc).__name__,
        )
        stats["sync_4_skipped"] += 1


# ---------------------------------------------------------------------------
# sync-5: milestone-close-all-items retry
# ---------------------------------------------------------------------------


def _sync_5_close_all_items(
    deps: Any, cfg: ReconcileConfig, stats: dict[str, int], correlation_id: str,
    customer_id: str, device_id: str, milestone_id: str,
    sp_milestone: dict[str, Any] | None,
) -> None:
    """Fire close_all_items_task ONLY when:
      - SP `closed_all_items_triggered_at` set >5min ago, AND
      - ALL items in this (device, milestone) still in SubmittedToCustomer
        (i.e., NO items reached Closed).

    Per user Q5 lock: if any single item transitioned to Closed, the close-all
    email was received; existing close_all_items_task guarantees eventual
    processing of remaining items via its own state-filter guard.
    """
    sub_cfg = cfg.sync_5_close_all_items
    if not sub_cfg.enabled or sp_milestone is None:
        return
    ts = sp_milestone.get("closed_all_items_triggered_at")
    if not ts:
        return
    elapsed = _elapsed_seconds(ts)
    if elapsed is None or elapsed < sub_cfg.elapsed_threshold_sec:
        return

    pg_items = deps.storage.list_items_for_milestone(milestone_id, None) or []
    pg_items = [it for it in pg_items if getattr(it, "device_id", None) == device_id]
    if not pg_items:
        return

    # Predicate: ALL items still in SubmittedToCustomer (or RFS + no_customer_upload
    # which close_all_items_task also handles per DEF-20 carve-out). If any is
    # Closed, exit.
    for it in pg_items:
        state = getattr(it, "delivery_state", None) or ""
        if state == _STATE_CLOSED:
            return  # close email was received

    from core.src.workflow_engine.tasks.milestone import close_all_items_task
    event_ctx = {
        "customer_id":    customer_id,
        "milestone_id":   milestone_id,
        "correlation_id": correlation_id,
        "trigger_source": "sync_backfill_close_all",
        "pm_id":          "sync_reconciler",
    }
    params = {"milestone_id": milestone_id, "pm_id": "sync_reconciler"}
    try:
        close_all_items_task.apply(args=(params, event_ctx), throw=False)
        stats["sync_5_dispatched"] += 1
        _audit(deps, "sync_5_dispatched", None, {
            "customer_id":    customer_id,
            "device_id":      device_id,
            "milestone_id":   milestone_id,
            "elapsed_sec":    int(elapsed),
            "correlation_id": correlation_id,
        })
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "sync_5_dispatch_failed: customer=%s milestone=%s: %s",
            customer_id, milestone_id, type(exc).__name__,
        )
        stats["sync_5_skipped"] += 1


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


def _audit(
    deps: Any,
    action_type: str,
    delivery_item_id: str | None,
    details: dict[str, Any],
) -> None:
    """Best-effort audit writer -- failures never break the reconciler."""
    if deps.audit is None:
        return
    try:
        deps.audit.write_communication_log(
            action_type=action_type,
            delivery_item_id=delivery_item_id,
            attribution={
                "trigger_source": details.get("trigger_source", "sync_reconciler"),
                "correlation_id": details.get("correlation_id", ""),
                "modified_by":    "system",
            },
            details=details,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning("reconcile_audit_failed: %s", type(exc).__name__)


# ---------------------------------------------------------------------------
# sync-6: stuck-CloseInProgress sweeper (CIP-4 2026-07-28)
# ---------------------------------------------------------------------------


def _sync_6_close_in_progress(
    deps: Any, cfg: ReconcileConfig, stats: dict[str, int], correlation_id: str,
    customer_id: str, device_id: str, milestone_id: str,
) -> None:
    """RECON-5 (2026-07-30): SP is the authoritative TPM close-intent source.

    Scan SP for items in CloseInProgress and force-advance to CLOSED in both
    SP and Postgres regardless of Postgres state (Open, OutreachSent, CIP,
    etc.). Handles the missed-SP-alert case: TPM clicks Close on SP UI, SP
    writes CIP, the CHANGED alert email to HILDA is lost; without this sync
    Postgres stays behind indefinitely and SP shows CIP forever.

    Prior implementation (CIP-4 2026-07-28) scanned Postgres for CIP -- only
    caught the hop-2-crash case (<1s window) and missed the far more common
    lost-alert case entirely.

    Force-advance uses update_delivery_state with bypass_guards=True +
    trigger_source='manual_tpm_override' (reuse of the CLOSE-1 escape
    hatch). SP writeback happens inside update_delivery_state.

    No-op when:
      * sync_6 disabled in config
      * SP read returns no items or no CIP items in this scope
      * Postgres item is already CLOSED (RECON-1 already-closed pattern)
      * SP row's Modified timestamp within the elapsed threshold (avoids
        racing the primary apply_tpm_sp_close_in_progress_task alert path)
    """
    sync_cfg = cfg.sync_6_close_in_progress
    if not sync_cfg.enabled:
        stats["sync_6_skipped"] += 1
        return

    sp_items = _sp_read_delivery_items(deps, customer_id, device_id, milestone_id)
    if not sp_items:
        return
    sp_cip_rows = [
        r for r in sp_items
        if (r.get("delivery_state") or "").strip() == _STATE_CLOSE_IN_PROGRESS
    ]
    if not sp_cip_rows:
        return   # normal case -- no CIP intent on SP for this scope

    pg_items = deps.storage.list_items_for_milestone(milestone_id, None) or []
    pg_by_item_no: dict[int, Any] = {}
    for it in pg_items:
        if (getattr(it, "device_id", None) or "") != device_id:
            continue
        it_no = getattr(it, "item_no", None)
        if it_no is None:
            continue
        try:
            pg_by_item_no[int(it_no)] = it
        except (TypeError, ValueError):
            continue

    from core.src.template_schema.enums import DeliveryState
    from core.src.tracker.transitions import update_delivery_state

    threshold_sec = sync_cfg.elapsed_threshold_sec

    for sp_row in sp_cip_rows:
        sp_item_no = sp_row.get("item_no")
        if sp_item_no is None:
            continue
        try:
            item_no_int = int(sp_item_no)
        except (TypeError, ValueError):
            continue
        pg_item = pg_by_item_no.get(item_no_int)
        if pg_item is None:
            continue   # SP has an item HILDA didn't import; skip
        pg_state = (getattr(pg_item, "delivery_state", None) or "")
        if pg_state == _STATE_CLOSED:
            continue   # already done

        # SP Modified is the closest signal to "when did TPM click Close".
        # Fall back to Postgres last_updated when SP row lacks Modified.
        sp_modified = (
            sp_row.get("Modified")
            or sp_row.get("modified")
            or sp_row.get("last_updated")
        )
        elapsed = _elapsed_seconds(sp_modified) if sp_modified else None
        if elapsed is not None and elapsed < threshold_sec:
            continue   # too fresh, could still be racing the alert path

        item_id = getattr(pg_item, "item_id", None) or getattr(pg_item, "delivery_item_id", None)
        if item_id is None:
            continue

        try:
            result = update_delivery_state(
                delivery_item_id=item_id,
                target_state=DeliveryState.CLOSED,
                params={"closed_via": "reconcile_sync_6_sp_close_in_progress"},
                event_context={
                    "correlation_id":   correlation_id,
                    "customer_id":      customer_id,
                    "milestone_id":     milestone_id,
                    "delivery_item_id": item_id,
                    "trigger_source":   "manual_tpm_override",
                },
                storage=deps.storage,
                sp_writer=deps.sp_writer,
                audit=deps.audit,
                bypass_guards=True,
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "sync_6_advance_failed: item=%s: %s: %s",
                item_id, type(exc).__name__, str(exc)[:120],
            )
            continue

        if result.outcome in ("transitioned", "no_op_idempotent"):
            stats["sync_6_advanced"] += 1
            _audit(
                deps,
                "reconcile_sync_6_sp_close_in_progress_advanced",
                item_id,
                {
                    "milestone_id":    milestone_id,
                    "customer_id":     customer_id,
                    "device_id":       device_id,
                    "correlation_id":  correlation_id,
                    "elapsed_sec":     elapsed,
                    "pg_state_prior":  pg_state,
                    "trigger_source":  "sync_backfill_close_in_progress",
                },
            )


# ---------------------------------------------------------------------------
# SYNC7-1 (2026-08-26): retry-unrouted sweeper
# ---------------------------------------------------------------------------

def _matches_any_tag_group(text: str, groups: list[list[str]]) -> bool:
    """Mirror of `Fr52AttachmentRouter._any_group_matches` substring semantics
    (AND-of-OR). Local copy to avoid pulling the full router class for what
    is a 4-line predicate. IMEI reserved-literal handling intentionally
    omitted -- sync-7 operates on folder-name match_input where the IMEI
    Excel-only branch doesn't apply."""
    for group in groups:
        if all(tag.lower() in text for tag in group):
            return True
    return False


def _sync_7_retry_unrouted(
    deps: Any, cfg: ReconcileConfig, stats: dict[str, int], correlation_id: str,
    customer_id: str, device_id: str, milestone_id: str,
) -> None:
    """SYNC7-1 (2026-08-26): retry routing for files stuck in _unrouted when
    new eligible items now exist in the candidate pool.

    Timing race the sync closes: TPM adds delivery_item rows via SP alert
    import AFTER an NSD/PLM tick ingested a file whose folder-tag would
    match them -> file lands in _unrouted (the ingest-time candidate pool
    didn't include the newly-added items) -> dedup by file_hash prevents
    the next tick from re-attempting -> file stays stuck until TPM triages
    via the UR-5 /_unknownTG/ UI.

    Sync-7 runs each meta-reconciler tick and:
      1. `list_unrouted_for_scope` -- fetches every DocumentIndex row in
         scope with no association.
      2. For each unrouted doc:
         a. Derive match_hint = parent folder name of original_filename
            (NSDMATCH-3 semantics, D-173). No parent -> skip (email-ingested;
            no folder context to rescue).
         b. `list_route_candidates_for_scope` -- the current eligible-target
            item pool (Confirmation + Default excluded, per manual-triage
            semantics).
         c. Substring-match parent-folder against each candidate's
            item_description via the router's AND-of-OR tag-group logic.
         d. EXACTLY ONE candidate matches -> promote via
            `route_unrouted_to_item` (creates association, moves bytes to
            staging path, updates document_index, writes audit).
         e. Zero matches -> skipped (no candidate; leave for TPM triage).
         f. Multi-match -> skipped (ambiguous; leave for TPM triage;
            respects D-153 cross-TG constraint semantics).

    Idempotent: once a file is routed the next tick's `list_unrouted` no
    longer surfaces it (has association). Naturally converges. Configured
    with elapsed_threshold_sec=0 -- fires immediately per tick since the
    predicate (unrouted AND matching item exists) doesn't race any
    concurrent path.

    Cross-TG constraint: multi-match across items in DIFFERENT tg_names
    falls into the multi-match skip branch above, deferring the routing
    decision to the TPM. Sync-7 is deliberately conservative -- only the
    unambiguous single-TG single-item case is auto-promoted.
    """
    sync_cfg = cfg.sync_7_retry_unrouted
    if not sync_cfg.enabled:
        stats["sync_7_skipped"] += 1
        return

    from pathlib import PurePosixPath
    from core.src.email_service.inbound.attachment_router import (
        Fr52AttachmentRouter,
    )
    from core.src.storage.unrouted_ops import UnroutedStorage

    us = UnroutedStorage()
    try:
        unrouted = us.list_unrouted(
            customer_id=customer_id, device_id=device_id, milestone_id=milestone_id,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "sync_7_list_unrouted_failed: milestone=%s: %s: %s",
            milestone_id, type(exc).__name__, str(exc)[:120],
        )
        return
    if not unrouted:
        return

    try:
        candidates = us.list_route_candidates(
            customer_id=customer_id, device_id=device_id, milestone_id=milestone_id,
        )
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "sync_7_list_candidates_failed: milestone=%s: %s: %s",
            milestone_id, type(exc).__name__, str(exc)[:120],
        )
        return
    if not candidates:
        return

    for doc in unrouted:
        parent = PurePosixPath(doc.original_filename or "").parent.name.lower()
        if not parent:
            continue  # email-ingested or root-level file; no folder context
        matches: list = []
        for cand in candidates:
            desc = getattr(cand, "item_description", None)
            groups = Fr52AttachmentRouter._extract_tag_groups(desc)
            if not groups:
                continue
            if _matches_any_tag_group(parent, groups):
                matches.append(cand)
        if len(matches) == 0:
            stats["sync_7_no_match"] += 1
            continue
        if len(matches) > 1:
            stats["sync_7_multi_match"] += 1
            _log.info(
                "sync_7_multi_match: file_hash=%s parent=%r matched %d items -- skipping",
                doc.file_hash[:12], parent, len(matches),
            )
            continue

        target = matches[0]
        target_iid = getattr(target, "item_id", None)
        if not target_iid:
            continue
        try:
            result = us.route(
                file_hash=doc.file_hash,
                target_delivery_item_id=target_iid,
                tpm_id="reconcile-sync-7",
            )
        except Exception as exc:  # noqa: BLE001
            _log.warning(
                "sync_7_route_failed: file_hash=%s target=%s: %s: %s",
                doc.file_hash[:12], target_iid,
                type(exc).__name__, str(exc)[:120],
            )
            continue

        if getattr(result, "outcome", None) == "routed":
            stats["sync_7_routed"] += 1
            _audit(
                deps,
                "reconcile_sync_7_retry_unrouted_routed",
                target_iid,
                {
                    "milestone_id":      milestone_id,
                    "customer_id":       customer_id,
                    "device_id":         device_id,
                    "correlation_id":    correlation_id,
                    "file_hash":         doc.file_hash,
                    "original_filename": doc.original_filename,
                    "parent_folder":     parent,
                    "trigger_source":    "sync_backfill_retry_unrouted",
                },
            )
