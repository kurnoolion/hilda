"""DRRP1-STATE-1 phase 2 (2026-09-10) -- reverse RFS -> UnderPMReview bounce.

When a new own document arrives on a DeliveryItem that has already reached
ReadyForSubmission, pull the item back to UnderPMReview so PM re-approves
before the doc ships.

Universal per user 2026-09-09 Q1=(b): applies to ANY item currently in
RFS regardless of how it got there (standard PMApproval ladder or the
DRR mapping promote path).

Caller-responsibility filters -- these are NOT new own docs and MUST NOT
dispatch the bounce:
  * doc_type reclassify (existing doc's classification changes; no new
    evidence)
  * revision-family merge / TPM edit of an existing rev
  * DRR migration passthrough (a P1 item is receiving a mapped DRR
    document's link -- the "new" association is a routing update, not
    new PM-worthy evidence)

Sites that DO call this on new own doc arrival:
  * email_service inbound routing (Fr52AttachmentRouter persist)
  * plm_poll new-revision persist
  * nsd2_poll new-file persist
  * unrouted_ops manual TPM route from _unrouted
"""
from __future__ import annotations

import logging
from typing import Any

_log = logging.getLogger(__name__)

__all__ = [
    "bounce_to_under_pm_review_if_rfs",
]


def bounce_to_under_pm_review_if_rfs(
    *,
    deps: Any,
    delivery_item_id: str,
    correlation_id: str = "?",
    source_marker: str = "doc_received",
) -> dict[str, Any]:
    """Idempotent bounce: if `delivery_item_id`'s current state is RFS,
    dispatch a doc_received_after_rfs transition to UnderPMReview.

    Returns a summary:
      {
        "outcome": "bounced" | "no_op_not_rfs" | "item_not_found" | "error",
        "delivery_item_id": ...,
        "from_state": <string or None>,
      }

    Never raises. Callers should invoke AFTER the persist that created the
    new own doc, so the item is only pulled back when a new file actually
    lands. `source_marker` lets audit distinguish which persist site fired
    the bounce (email vs. plm vs. nsd vs. unrouted).
    """
    from core.src.tracker import DeliveryState as _DS
    from core.src.tracker.transitions import update_delivery_state as _uds

    summary: dict[str, Any] = {
        "outcome": "no_op_not_rfs",
        "delivery_item_id": delivery_item_id,
        "from_state": None,
    }
    try:
        item = deps.storage.get_delivery_item(delivery_item_id)
    except Exception as exc:  # noqa: BLE001
        summary["outcome"] = "error"
        _log.warning(
            "DRRP1_BOUNCE: get_delivery_item failed item=%s: %s: %s",
            delivery_item_id, type(exc).__name__, str(exc)[:120],
        )
        return summary
    if item is None:
        summary["outcome"] = "item_not_found"
        return summary

    current_state = getattr(item, "delivery_state", None) or ""
    summary["from_state"] = current_state
    if current_state != _DS.READY_FOR_SUBMISSION.value:
        return summary

    try:
        _uds(
            delivery_item_id=delivery_item_id,
            target_state=_DS.UNDER_PM_REVIEW,
            params={},
            event_context={
                "correlation_id":   correlation_id,
                "delivery_item_id": delivery_item_id,
                "trigger_source":   "doc_received_after_rfs",
                "rule_id":          f"drrp1_state:doc_received_after_rfs:{source_marker}",
                "pm_id":            "system:doc_received_after_rfs",
                "bounce_source":    source_marker,
            },
            storage=deps.storage,
            sp_writer=deps.sp_writer,
            audit=deps.audit,
        )
        summary["outcome"] = "bounced"
        _log.warning(
            "DRRP1_BOUNCE: RFS -> UnderPMReview item=%s source=%s (new own doc)",
            delivery_item_id, source_marker,
        )
    except Exception as exc:  # noqa: BLE001
        summary["outcome"] = "error"
        _log.warning(
            "DRRP1_BOUNCE: transition failed item=%s: %s: %s",
            delivery_item_id, type(exc).__name__, str(exc)[:160],
        )
    return summary
