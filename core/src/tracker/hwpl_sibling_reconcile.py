"""HWPL-SIBLING-1 (2026-09-13) -- sibling-group cascade for HW PL / MQL-FIT.

When a doc lands at NSD for a work-item group like {10 anchor, 11, 12},
the router routes it to a single item -- usually the anchor. The anchor
walks Open -> RFS -> SubmittedToCustomer; siblings that never got a doc
stay stuck. This module cascades the anchor's terminal transitions:

  * anchor -> RFS         => siblings -> RFS  (Guard 12)
  * anchor -> Submitted   => siblings -> SubmittedToCustomer (Guard 13)
                              (2-hop through RFS internally when needed)

Config lives in
`customizations/template_schemas/<CUSTOMER>/sibling_work_item_groups.yaml`
loaded by `template_schema.sibling_work_item_groups`.

Design (per user 2026-09-13):
  * Skip siblings already terminal (Closed / CloseInProgress /
    SubmittedToCustomer / Cancelled) -- TPM's decision is authoritative.
  * Skip siblings already at target -- idempotent no-op.
  * No reverse cascade: if the anchor is later kicked back to
    UnderPMReview, siblings stay put (per architect: keep it simple).
  * Multi-source (one sibling in two groups) is not a production shape
    and the loader rejects the second block.

Pattern mirrors tracker.drr_mapping_reconcile -- same shape of walk +
resolve + dispatch, same summary dict for telemetry. Every outcome is
audited so a TPM can reconstruct why a sibling did / did not move.
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

__all__ = [
    "reconcile_siblings_on_anchor_rfs",
    "reconcile_siblings_on_anchor_submitted",
]


def reconcile_siblings_on_anchor_rfs(
    *,
    deps: Any,
    anchor_customer_id: str,
    anchor_device_id: str,
    anchor_milestone_id: str,
    anchor_tg_name: str,
    anchor_item_no: int,
    correlation_id: str = "?",
    pm_id: str = "system:hwpl_sibling_reconcile",
) -> dict[str, Any]:
    """Anchor just reached RFS -- promote each sibling to RFS.

    Skips siblings already terminal (Closed / CIP / Submitted / Cancelled)
    -- TPM decision is authoritative. Skips siblings already in RFS
    (idempotent). Never raises; each per-sibling failure is caught +
    logged + rolled into the summary.

    Returns:
      {
        "outcome": "reconciled" | "no_group" | "wrong_milestone",
        "promoted":            [item_id, ...],
        "skipped_terminal":    [item_id, ...],
        "skipped_already_rfs": [item_id, ...],
        "skipped_no_item":     [item_no, ...],
        "failed":              [(item_id, exc_type_name), ...],
      }
    """
    from core.src.template_schema.sibling_work_item_groups import (
        get_sibling_groups,
    )
    from core.src.tracker import DeliveryState as _DS
    from core.src.tracker.transitions import update_delivery_state as _uds

    summary: dict[str, Any] = {
        "outcome":               "reconciled",
        "promoted":              [],
        "skipped_terminal":      [],
        "skipped_already_rfs":   [],
        "skipped_no_item":       [],
        "failed":                [],
    }

    target_milestone, groups = get_sibling_groups(anchor_customer_id)
    if not groups:
        summary["outcome"] = "no_group"
        return summary
    if target_milestone and target_milestone != anchor_milestone_id:
        summary["outcome"] = "wrong_milestone"
        return summary

    tg_key = (anchor_tg_name or "").strip().lower()
    matching = [
        g for g in groups
        if g.anchor == anchor_item_no and g.tg_name.strip().lower() == tg_key
    ]
    if not matching:
        summary["outcome"] = "no_group"
        return summary

    _TERMINAL = frozenset({
        _DS.CLOSED.value,
        _DS.CLOSE_IN_PROGRESS.value,
        _DS.SUBMITTED_TO_CUSTOMER.value,
        _DS.CANCELLED.value if hasattr(_DS, "CANCELLED") else "Cancelled",
    })

    for group in matching:
        for sibling_item_no in group.siblings:
            sibling = _resolve_sibling_item(
                deps,
                customer_id=anchor_customer_id,
                device_id=anchor_device_id,
                milestone_id=anchor_milestone_id,
                item_no=int(sibling_item_no),
            )
            if sibling is None:
                summary["skipped_no_item"].append(int(sibling_item_no))
                _log.warning(
                    "HWPL_SIBLING_RFS: sibling item not found scope=%s/%s/%s "
                    "item_no=%s (anchor=%s)",
                    anchor_customer_id, anchor_device_id, anchor_milestone_id,
                    sibling_item_no, anchor_item_no,
                )
                continue

            sibling_item_id = (
                getattr(sibling, "item_id", None)
                or getattr(sibling, "delivery_item_id", None)
            )
            if not sibling_item_id:
                summary["failed"].append(("<unknown_id>", "MissingItemId"))
                continue

            current_state = getattr(sibling, "delivery_state", None) or ""
            if current_state in _TERMINAL:
                summary["skipped_terminal"].append(sibling_item_id)
                _log.info(
                    "HWPL_SIBLING_RFS: sibling terminal scope=%s state=%s "
                    "-- TPM decision preserved",
                    sibling_item_id, current_state,
                )
                continue
            if current_state == _DS.READY_FOR_SUBMISSION.value:
                summary["skipped_already_rfs"].append(sibling_item_id)
                continue

            try:
                _uds(
                    delivery_item_id=sibling_item_id,
                    target_state=_DS.READY_FOR_SUBMISSION,
                    params={},
                    event_context={
                        "correlation_id":   correlation_id,
                        "delivery_item_id": sibling_item_id,
                        "trigger_source":   "hwpl_sibling_promote",
                        "rule_id":          "hwpl_sibling:promote_to_rfs",
                        "pm_id":            pm_id,
                        "sibling_anchor_item_no": int(anchor_item_no),
                        "sibling_anchor_tg_name": anchor_tg_name,
                    },
                    storage=deps.storage,
                    sp_writer=deps.sp_writer,
                    audit=deps.audit,
                )
                summary["promoted"].append(sibling_item_id)
                _log.warning(
                    "HWPL_SIBLING_RFS: promoted sibling=%s from=%s "
                    "(anchor=%s tg=%s)",
                    sibling_item_id, current_state,
                    anchor_item_no, anchor_tg_name,
                )
            except Exception as exc:  # noqa: BLE001
                summary["failed"].append(
                    (sibling_item_id, type(exc).__name__)
                )
                _log.warning(
                    "HWPL_SIBLING_RFS: promote failed sibling=%s: %s: %s",
                    sibling_item_id, type(exc).__name__, str(exc)[:160],
                )

    return summary


def reconcile_siblings_on_anchor_submitted(
    *,
    deps: Any,
    anchor_customer_id: str,
    anchor_device_id: str,
    anchor_milestone_id: str,
    anchor_tg_name: str,
    anchor_item_no: int,
    correlation_id: str = "?",
    pm_id: str = "system:hwpl_sibling_reconcile",
) -> dict[str, Any]:
    """Anchor just reached SubmittedToCustomer -- move siblings to Submitted.

    2-hop where needed: if a sibling is not yet in RFS (e.g., the RFS
    cascade earlier was skipped because the sibling was in a terminal
    state that later cleared, or the RFS hook missed for whatever reason),
    promote to RFS first via hwpl_sibling_promote, then submit via
    hwpl_sibling_submit.

    Skips siblings already Submitted / Closed / CIP / Cancelled --
    idempotent + TPM decision preserved. Never raises.

    Returns summary dict (same shape as reconcile_siblings_on_anchor_rfs
    but with `submitted` instead of `promoted`, plus `failed_promote_hop`
    for siblings that got to RFS but blew up on the second hop).
    """
    from core.src.template_schema.sibling_work_item_groups import (
        get_sibling_groups,
    )
    from core.src.tracker import DeliveryState as _DS
    from core.src.tracker.transitions import update_delivery_state as _uds

    summary: dict[str, Any] = {
        "outcome":                 "reconciled",
        "submitted":               [],
        "skipped_terminal":        [],
        "skipped_already_submit":  [],
        "skipped_no_item":         [],
        "failed":                  [],
    }

    target_milestone, groups = get_sibling_groups(anchor_customer_id)
    if not groups:
        summary["outcome"] = "no_group"
        return summary
    if target_milestone and target_milestone != anchor_milestone_id:
        summary["outcome"] = "wrong_milestone"
        return summary

    tg_key = (anchor_tg_name or "").strip().lower()
    matching = [
        g for g in groups
        if g.anchor == anchor_item_no and g.tg_name.strip().lower() == tg_key
    ]
    if not matching:
        summary["outcome"] = "no_group"
        return summary

    _CLOSED_OR_CIP = frozenset({
        _DS.CLOSED.value,
        _DS.CLOSE_IN_PROGRESS.value,
        _DS.CANCELLED.value if hasattr(_DS, "CANCELLED") else "Cancelled",
    })

    for group in matching:
        for sibling_item_no in group.siblings:
            sibling = _resolve_sibling_item(
                deps,
                customer_id=anchor_customer_id,
                device_id=anchor_device_id,
                milestone_id=anchor_milestone_id,
                item_no=int(sibling_item_no),
            )
            if sibling is None:
                summary["skipped_no_item"].append(int(sibling_item_no))
                _log.warning(
                    "HWPL_SIBLING_SUB: sibling item not found scope=%s/%s/%s "
                    "item_no=%s (anchor=%s)",
                    anchor_customer_id, anchor_device_id, anchor_milestone_id,
                    sibling_item_no, anchor_item_no,
                )
                continue

            sibling_item_id = (
                getattr(sibling, "item_id", None)
                or getattr(sibling, "delivery_item_id", None)
            )
            if not sibling_item_id:
                summary["failed"].append(("<unknown_id>", "MissingItemId"))
                continue

            current_state = getattr(sibling, "delivery_state", None) or ""
            if current_state == _DS.SUBMITTED_TO_CUSTOMER.value:
                summary["skipped_already_submit"].append(sibling_item_id)
                continue
            if current_state in _CLOSED_OR_CIP:
                summary["skipped_terminal"].append(sibling_item_id)
                _log.info(
                    "HWPL_SIBLING_SUB: sibling terminal scope=%s state=%s "
                    "-- TPM decision preserved",
                    sibling_item_id, current_state,
                )
                continue

            # 2-hop: if not yet in RFS, promote first. Reuses the same
            # trigger + guard as the RFS cascade so telemetry stays
            # consistent. On promote failure, the item stays put and we
            # move on -- do NOT attempt submit from a non-RFS state.
            if current_state != _DS.READY_FOR_SUBMISSION.value:
                try:
                    _uds(
                        delivery_item_id=sibling_item_id,
                        target_state=_DS.READY_FOR_SUBMISSION,
                        params={},
                        event_context={
                            "correlation_id":   correlation_id,
                            "delivery_item_id": sibling_item_id,
                            "trigger_source":   "hwpl_sibling_promote",
                            "rule_id":          "hwpl_sibling:promote_to_rfs_hop1",
                            "pm_id":            pm_id,
                            "sibling_anchor_item_no": int(anchor_item_no),
                            "sibling_anchor_tg_name": anchor_tg_name,
                        },
                        storage=deps.storage,
                        sp_writer=deps.sp_writer,
                        audit=deps.audit,
                    )
                except Exception as exc:  # noqa: BLE001
                    summary["failed"].append(
                        (sibling_item_id, f"promote:{type(exc).__name__}")
                    )
                    _log.warning(
                        "HWPL_SIBLING_SUB: promote-hop failed sibling=%s "
                        "from=%s: %s: %s -- skipping submit",
                        sibling_item_id, current_state,
                        type(exc).__name__, str(exc)[:160],
                    )
                    continue

            # Second hop (or first for siblings already in RFS): submit.
            try:
                _uds(
                    delivery_item_id=sibling_item_id,
                    target_state=_DS.SUBMITTED_TO_CUSTOMER,
                    params={},
                    event_context={
                        "correlation_id":   correlation_id,
                        "delivery_item_id": sibling_item_id,
                        "trigger_source":   "hwpl_sibling_submit",
                        "rule_id":          "hwpl_sibling:submit_hop2",
                        "pm_id":            pm_id,
                        "sibling_anchor_item_no": int(anchor_item_no),
                        "sibling_anchor_tg_name": anchor_tg_name,
                    },
                    storage=deps.storage,
                    sp_writer=deps.sp_writer,
                    audit=deps.audit,
                )
                summary["submitted"].append(sibling_item_id)
                _log.warning(
                    "HWPL_SIBLING_SUB: submitted sibling=%s "
                    "(anchor=%s tg=%s)",
                    sibling_item_id, anchor_item_no, anchor_tg_name,
                )
            except Exception as exc:  # noqa: BLE001
                summary["failed"].append(
                    (sibling_item_id, f"submit:{type(exc).__name__}")
                )
                _log.warning(
                    "HWPL_SIBLING_SUB: submit-hop failed sibling=%s: %s: %s",
                    sibling_item_id, type(exc).__name__, str(exc)[:160],
                )
    return summary


def _resolve_sibling_item(
    deps: Any, *,
    customer_id: str, device_id: str, milestone_id: str, item_no: int,
) -> Any:
    """Look up a DeliveryItem by (customer_id, device_id, milestone_id,
    item_no). Same pattern as drr_mapping_reconcile._resolve_target_item
    (storage helper signature is (milestone_id, states); we scope in
    Python since counts per milestone are small)."""
    try:
        items = deps.storage.list_items_for_milestone(milestone_id, None)
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "HWPL_SIBLING: list_items_for_milestone failed milestone=%s "
            "customer=%s device=%s: %s: %s",
            milestone_id, customer_id, device_id,
            type(exc).__name__, str(exc)[:160],
        )
        return None
    for it in items or []:
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
