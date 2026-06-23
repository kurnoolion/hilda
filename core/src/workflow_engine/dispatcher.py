"""TriggerDispatcher -- the central rule_engine -> Celery dispatch surface.

Receives `TriggerEvent`s from the 6 trigger-source sites, calls `rule_engine.evaluate`
with an `item_snapshot` (per D2 + D12 cascade 2026-06-23), and schedules each returned
`RuleMatch`'s action chain as an independent Celery chain per [D-066].

Pause check (FR-31 sub-1) reads `item.rules_paused: bool` from the snapshot per D5
cascade 2026-06-23 -- no PauseStateLookup Protocol (dropped per [D-112]; SP column
is the canonical home per [D-108]).

Pure orchestration -- workflow_engine does not execute action logic; downstream
modules own the actual work.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from celery import Celery

from core.src.diagnostics import format_code
from core.src.rule_engine import RuleEngine, RuleMatch, TriggerEvent

from .celery_app import hilda_celery_app
from .registry import build_chain_from_rule_match

__all__ = ["TriggerDispatcher", "DispatchResult", "StorageLike"]


logger = logging.getLogger(__name__)


class StorageLike(Protocol):
    """Minimal storage Protocol for TriggerDispatcher -- fetches item snapshot for
    pause check. Concrete `storage` module implements this via structural typing.

    Per D12 cascade 2026-06-23: dispatcher reads item BEFORE calling
    rule_engine.evaluate so pause check happens at evaluation time.
    """

    def get_delivery_item(self, delivery_item_id: str) -> Any: ...


@dataclass(frozen=True)
class DispatchResult:
    """Outcome of one TriggerDispatcher.dispatch() call."""

    correlation_id:  str
    matched_count:   int                          # rule_engine matches before pause filter
    scheduled_tasks: list[str] = field(default_factory=list)        # Celery task IDs
    skipped_matches: list[tuple[str, Literal["paused"]]] = field(default_factory=list)


class TriggerDispatcher:
    """Receives TriggerEvent from any of the 6 trigger sources; resolves matches
    via rule_engine; schedules independent Celery chains per [D-066]."""

    def __init__(
        self,
        rule_engine: RuleEngine,
        storage: StorageLike | None = None,    # None ok for item-less event tests
        celery_app: Celery = hilda_celery_app,
    ) -> None:
        self._rule_engine = rule_engine
        self._storage = storage
        self._celery_app = celery_app

    def _fetch_item_snapshot(self, event: TriggerEvent) -> Any:
        """Fetch item snapshot per D12 cascade 2026-06-23. Returns None for
        item-less events (MilestoneAllClosed, CredentialExpired, etc.) or when
        no storage Protocol injected (e.g., tests)."""
        delivery_item_id = getattr(event.entity_ref, "delivery_item_id", None)
        if delivery_item_id is None or self._storage is None:
            return None
        try:
            return self._storage.get_delivery_item(delivery_item_id)
        except Exception:
            # Snapshot fetch failure should not block dispatch -- caller already
            # has the event; we just lose the pause check.
            logger.warning("Failed to fetch item snapshot for %s; dispatching without pause check",
                           delivery_item_id)
            return None

    def _build_event_context(self, event: TriggerEvent) -> dict[str, Any]:
        """Construct JSON-serialisable event_context per Key choice 2026-06-10.

        Per D1 cascade 2026-06-23: uses customer_id / device_id (was customer_slug /
        device_slug)."""
        ref = event.entity_ref
        return {
            "correlation_id":   event.correlation_id,
            "customer_id":      ref.customer_id,
            "device_id":        getattr(ref, "device_id", None),
            "milestone_id":     getattr(ref, "milestone_id", None),
            "delivery_item_id": getattr(ref, "delivery_item_id", None),
            "trigger":          event.trigger.value,
            "sub_trigger":      event.sub_trigger,
            "timestamp":        event.timestamp.isoformat() if event.timestamp else None,
        }

    def dispatch(self, event: TriggerEvent) -> DispatchResult:
        """Pipeline (Ph-1 per D2 + D12 cascade 2026-06-23):
        1. Fetch item snapshot (None for item-less events).
        2. rule_engine.evaluate(event, item_snapshot=item) -> list[RuleMatch].
        3. For each RuleMatch:
           (a) If pause_state='paused', log WFL-W001 + skip (do not enqueue).
           (b) Else: build_chain_from_rule_match + apply_async; record task ID.
        4. Return DispatchResult.

        Item-less triggers (MilestoneAllClosed, CredentialExpired, etc.) pass
        item_snapshot=None to rule_engine; pause check is skipped automatically.
        """
        item_snapshot = self._fetch_item_snapshot(event)
        matches: list[RuleMatch] = self._rule_engine.evaluate(event, item_snapshot=item_snapshot)

        event_context = self._build_event_context(event)
        scheduled: list[str] = []
        skipped: list[tuple[str, Literal["paused"]]] = []

        for match in matches:
            if match.pause_state == "paused":
                logger.warning("WFL-W001: " + format_code(
                    "WFL-W001", rule_id=match.rule_id,
                    item_id=event_context.get("delivery_item_id") or "<none>",
                ))
                skipped.append((match.rule_id, "paused"))
                continue
            try:
                signature = build_chain_from_rule_match(match, event_context)
                async_result = signature.apply_async()
                scheduled.append(async_result.id)
            except Exception as exc:
                # Per workflow_engine MODULE.md Invariant: dispatch failure is
                # surfaced via WFL-* but does not abort other RuleMatches.
                logger.error("Dispatch failure for RuleMatch %s: %s", match.rule_id, exc)
                # Continue to next match.

        return DispatchResult(
            correlation_id=event.correlation_id,
            matched_count=len(matches),
            scheduled_tasks=scheduled,
            skipped_matches=skipped,
        )
