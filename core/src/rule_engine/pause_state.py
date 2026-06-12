"""FR-31 sub-1 pause/resume read-side.

PauseStateLookup is an injected dependency — physical home of the live pause flag is TBD
(SP DeliveryItem column vs event-carried; architect decision pending per rule-engine-v1
D-DRAFT-2, 2026-06-12). rule_engine NEVER writes pause state — that's FR-31's SP UI write
path through sharepoint_integration.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

__all__ = ["NoPauseState", "PauseStateLookup"]


@runtime_checkable
class PauseStateLookup(Protocol):
    """Injected pause-state provider; mocked in unit tests."""

    def is_item_paused(self, delivery_item_id: str) -> bool: ...

    def is_milestone_paused(self, milestone_id: str) -> bool: ...  # FR-31 sub-1 Pause All


class NoPauseState:
    """Null provider — nothing paused. Default for the diagnostic CLI and --explain mode,
    which run without production pause data."""

    def is_item_paused(self, delivery_item_id: str) -> bool:
        return False

    def is_milestone_paused(self, milestone_id: str) -> bool:
        return False
