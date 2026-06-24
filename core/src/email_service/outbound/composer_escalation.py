"""FR-10 cross-channel escalation enqueue.

Ph-1 STUB: when reminders yield no response after reminder_count >= M, enqueue
a messenger task. Ph-1 returns the enqueue dict (workflow_engine routes it);
the messenger transport is owned by the messenger module per Non-goals.
"""
from __future__ import annotations

from typing import Any

__all__ = ["compose_escalation_for_messenger"]


def compose_escalation_for_messenger(
    item: dict[str, Any], reminder_count: int
) -> dict[str, Any]:
    """Build the enqueue payload for the messenger module.

    Returns a dict that workflow_engine routes to messenger (Ph-1 stub shape).
    """
    owner_identity = {
        "owner_corp_usa_email": item.get("owner_corp_usa_email"),
        "owner_corp_email":     item.get("owner_corp_email"),
        "owner_corp_id":        item.get("owner_corp_id"),
        "owner_name":           item.get("owner_name"),
    }
    return {
        "messenger_action":   "owner_escalation",
        "delivery_item_id":   item.get("item_id"),
        "owner_identity":     owner_identity,
        "reminder_count":     reminder_count,
        "context_summary":    f"Owner non-response after {reminder_count} reminders",
    }
