"""Deadline-tiered polling_schedule breakpoint evaluator per FR-23 / FR-55.

Pure tier math — no IO. The PollingScheduleTier model lives in models.py alongside Rule
so the kind discriminator can reference it.
"""
from __future__ import annotations

import logging
from collections.abc import Sequence

from core.src.diagnostics import format_code

from .models import PollingScheduleTier

__all__ = ["evaluate_polling_schedule"]

logger = logging.getLogger(__name__)

_DEFAULT_BASELINE_MINUTES = 60  # mirrors RuleEngineConfig.default_baseline_minutes


def evaluate_polling_schedule(
    tiers: Sequence[PollingScheduleTier],
    days_to_deadline: int,
    *,
    default_baseline_minutes: int = _DEFAULT_BASELINE_MINUTES,
    rule_id: str = "<unknown>",
) -> int:
    """Returns the active interval_minutes for the given days_to_deadline.

    Tiers are evaluated in ascending order of days_before_deadline; the most specific tier
    whose breakpoint covers days_to_deadline (days_to_deadline <= days_before_deadline) wins.
    The boundary is INCLUSIVE: at exactly N days to deadline, the N-day tier applies
    (architect-confirmed 2026-06-12). Negative days_to_deadline (past deadline) falls into
    the tightest tier — past deadline polls fastest.
    The baseline tier (days_before_deadline=None) must be present and applies when no
    breakpoint covers; if missing, RUL-W004 is logged and `default_baseline_minutes` is the
    fallback. Example with tiers [{60min baseline}, {3 days, 15min}, {1 day, 5min}]:
    days_to_deadline=4 -> 60; days_to_deadline=2 -> 15; days_to_deadline=0 -> 5.

    When expected_completion_date has been overridden per-item by TPM (FR-14), that override
    date drives tier evaluation — the caller passes the override-resolved days_to_deadline.
    """
    baseline = next((t for t in tiers if t.days_before_deadline is None), None)
    if baseline is None:
        logger.warning(
            "RUL-W004: " + format_code("RUL-W004", rule_id=rule_id, minutes=str(default_baseline_minutes))
        )

    covering = [
        t for t in tiers
        if t.days_before_deadline is not None and days_to_deadline <= t.days_before_deadline
    ]
    if covering:
        return min(covering, key=lambda t: t.days_before_deadline).interval_minutes
    if baseline is not None:
        return baseline.interval_minutes
    return default_baseline_minutes
