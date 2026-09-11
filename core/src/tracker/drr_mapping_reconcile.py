"""DRRP1-STATE-1 phase 2 (2026-09-10) -- cross-milestone RFS reconcile.

When a source-milestone item (currently DRR) transitions to
ReadyForSubmission, promote every mapped target-milestone item (currently P1)
to ReadyForSubmission via the drr_mapping_promote trigger.

The mapping shape lives in
`customizations/template_schemas/<customer>/milestone_item_mapping.yaml`
loaded by `template_schema.milestone_item_mapping`. This module is the
consumer side -- it calls that loader, filters to blocks whose
`source_milestone` matches the item just approved, resolves the target
items via storage, and dispatches the transition.

Semantics (per user 2026-09-09 + 2026-09-10):
  * ANY pre-RFS state on the target item is a legal promote (Open,
    OutreachSent, DocumentReceived, OwnerClosed, Delayed, Blocked).
  * UnderPMReview on the target item BLOCKS the promote -- Guard 10
    enforces that TPM must approve explicitly.
  * Already-RFS / Submitted / Closed on the target: no-op idempotent.
  * Multi-source (many DRR items -> single P1 item): each source's RFS
    fires the reconcile independently; the first one that lands promotes
    the target, subsequent sources are audited no-ops.

Pure orchestration -- transition + storage lookup + audit dispatch are
delegated to the tracker + storage layers. Every outcome is captured in
the audit log so a TPM can reconstruct why a P1 item did / did not move.
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

__all__ = [
    "reconcile_target_items_on_source_rfs",
]


def reconcile_target_items_on_source_rfs(
    *,
    deps: Any,
    source_customer_id: str,
    source_device_id: str,
    source_milestone_id: str,
    source_item_no: int,
    correlation_id: str = "?",
    pm_id: str = "system:drr_mapping_reconcile",
) -> dict[str, Any]:
    """Walk every mapping block whose `source_milestone` equals
    `source_milestone_id`; for each block, look up the target item at
    `(source_customer_id, source_device_id, target_milestone,
    pairs[source_item_no])`; dispatch a drr_mapping_promote transition to
    RFS on the target.

    Returns a summary dict for telemetry / audit:
      {
        "outcome": "reconciled" | "no_mapping" | "no_mappings_for_source",
        "promoted": [target_item_id, ...],
        "skipped_under_pm_review": [target_item_id, ...],
        "skipped_already_final": [target_item_id, ...],  # RFS / Submitted / Closed
        "skipped_no_target_item": [(target_milestone, target_item_no), ...],
        "failed": [(target_item_id, exc_type_name), ...],
      }

    Never raises -- each per-target failure is caught + logged + rolled into
    the summary. The caller (apply_pm_approval_task) MUST NOT let this hook
    block or fail the DRR-side approval flow.
    """
    from core.src.template_schema.milestone_item_mapping import (
        get_mapping_blocks,
    )
    from core.src.tracker import DeliveryState as _DS
    from core.src.tracker.transitions import update_delivery_state as _uds

    summary: dict[str, Any] = {
        "outcome": "reconciled",
        "promoted": [],
        "skipped_under_pm_review": [],
        "skipped_already_final": [],
        "skipped_no_target_item": [],
        "failed": [],
    }

    try:
        blocks = get_mapping_blocks(source_customer_id)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "DRRP1_RECONCILE: get_mapping_blocks failed customer=%s: %s: %s",
            source_customer_id, type(exc).__name__, str(exc)[:120],
        )
        summary["outcome"] = "no_mapping"
        return summary

    if not blocks:
        summary["outcome"] = "no_mapping"
        return summary

    # Filter to blocks where the source milestone matches the item that
    # just approved. Multiple blocks are supported (a customer could map
    # DRR->P1 AND some other source->target on the same yaml).
    matching_blocks = [
        b for b in blocks
        if (b.source_milestone or "").strip() == (source_milestone_id or "").strip()
    ]
    if not matching_blocks:
        summary["outcome"] = "no_mappings_for_source"
        return summary

    _FINAL_STATES = {
        _DS.READY_FOR_SUBMISSION.value,
        _DS.SUBMITTED_TO_CUSTOMER.value,
        _DS.CLOSED.value,
        _DS.CLOSE_IN_PROGRESS.value,
    }

    for block in matching_blocks:
        target_item_no = block.pairs.get(int(source_item_no))
        if target_item_no is None:
            # This block covers other pairs; the source_item_no just isn't
            # mapped here. Not an error -- keep scanning other blocks.
            continue

        target_item = _resolve_target_item(
            deps,
            customer_id=source_customer_id,
            device_id=source_device_id,
            milestone_id=block.target_milestone,
            item_no=int(target_item_no),
        )
        if target_item is None:
            summary["skipped_no_target_item"].append(
                (block.target_milestone, int(target_item_no))
            )
            _log.warning(
                "DRRP1_RECONCILE: target item not found scope=%s/%s/%s item_no=%s "
                "(source_item_no=%s)",
                source_customer_id, source_device_id, block.target_milestone,
                target_item_no, source_item_no,
            )
            continue

        target_item_id = getattr(target_item, "item_id", None) or getattr(
            target_item, "delivery_item_id", None,
        )
        if not target_item_id:
            summary["failed"].append(("<unknown_id>", "MissingItemId"))
            continue

        current_state = (
            getattr(target_item, "delivery_state", None) or ""
        )
        if current_state in _FINAL_STATES:
            # Already RFS / Submitted / Closed -- audit only, no transition.
            summary["skipped_already_final"].append(target_item_id)
            _log.info(
                "DRRP1_RECONCILE: target already final scope=%s state=%s -- no-op",
                target_item_id, current_state,
            )
            continue
        if current_state == _DS.UNDER_PM_REVIEW.value:
            # Per user 2026-09-09 #3: TPM must approve explicitly.
            summary["skipped_under_pm_review"].append(target_item_id)
            _log.warning(
                "DRRP1_RECONCILE: target in UnderPMReview scope=%s -- TPM must "
                "approve explicitly; reconcile skipped",
                target_item_id,
            )
            continue

        # Dispatch the promote via the drr_mapping_promote trigger. Guard 10
        # runs the from_state != UnderPMReview + target == RFS enforcement.
        try:
            _uds(
                delivery_item_id=target_item_id,
                target_state=_DS.READY_FOR_SUBMISSION,
                params={},
                event_context={
                    "correlation_id":   correlation_id,
                    "delivery_item_id": target_item_id,
                    "trigger_source":   "drr_mapping_promote",
                    "rule_id":          "drrp1_state:drr_mapping_promote",
                    "pm_id":            pm_id,
                    # Traceability audit fields -- surface the source that
                    # authorized this promotion.
                    "drr_source_customer_id":  source_customer_id,
                    "drr_source_device_id":    source_device_id,
                    "drr_source_milestone_id": source_milestone_id,
                    "drr_source_item_no":      int(source_item_no),
                },
                storage=deps.storage,
                sp_writer=deps.sp_writer,
                audit=deps.audit,
            )
            summary["promoted"].append(target_item_id)
            _log.warning(
                "DRRP1_RECONCILE: promoted target=%s from=%s (source=%s item_no=%s)",
                target_item_id, current_state,
                source_milestone_id, source_item_no,
            )
        except Exception as exc:  # noqa: BLE001
            summary["failed"].append((target_item_id, type(exc).__name__))
            _log.warning(
                "DRRP1_RECONCILE: promote failed target=%s: %s: %s",
                target_item_id, type(exc).__name__, str(exc)[:160],
            )
    return summary


def _resolve_target_item(
    deps: Any, *,
    customer_id: str, device_id: str, milestone_id: str, item_no: int,
) -> Any:
    """Look up a DeliveryItem by (customer_id, device_id, milestone_id,
    item_no). Uses storage.list_items_for_milestone -- its signature is
    (milestone_id, states) with no customer/device filter, so we scope
    the returned rows in Python (item counts per milestone are bounded
    ~20-100, cheap to walk). Returns None on any failure; logs the
    failure so a bug like the initial kwargs-mismatch surfaces instead
    of silently reporting `target item not found` for every source.
    """
    try:
        items = deps.storage.list_items_for_milestone(milestone_id, None)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "DRRP1_RECONCILE: list_items_for_milestone failed "
            "milestone=%s customer=%s device=%s: %s: %s",
            milestone_id, customer_id, device_id,
            type(exc).__name__, str(exc)[:160],
        )
        return None
    for it in items or []:
        # Scope filter -- storage helper returns items across all
        # customers + devices for the given milestone.
        if (getattr(it, "customer_id", None) or "") != customer_id:
            continue
        if (getattr(it, "device_id", None) or "") != device_id:
            continue
        try:
            if int(getattr(it, "item_no", -1)) == int(item_no):
                return it
        except (TypeError, ValueError):
            continue
    return None
