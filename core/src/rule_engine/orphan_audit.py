"""Startup orphan audit per [D-062].

ItemOverride.rule_id is a soft-FK to YAML rule_id (no DB FK constraint because YAML is the
source of truth). Emit RUL-W002 for each override whose rule_id is not present in any YAML
tier (likely stale — rule was removed from YAML but the override survives). Caller logs the
warnings; rule_engine does NOT delete orphans (ops decision).

Signature note (2026-06-12): takes the overrides themselves rather than a bare id set —
the item-level override shape per D-DRAFT-1 means each finding carries its
delivery_item_id (RUL-W002 message requires it). The architect's [D-062] reconciliation
pass owns the canonical key shape.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Sequence

from core.src.diagnostics import format_code

from .override_store import ItemOverride

__all__ = ["OrphanFinding", "orphan_audit_postgres_overrides"]

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OrphanFinding:
    rule_id: str
    delivery_item_id: str


def orphan_audit_postgres_overrides(
    yaml_rule_ids: set[str],
    overrides: Sequence[ItemOverride],
) -> list[OrphanFinding]:
    """Returns one OrphanFinding per override whose rule_id is absent from every YAML tier.
    Logs RUL-W002 per finding. Pure set comparison — no IO."""
    findings = [
        OrphanFinding(rule_id=o.rule_id, delivery_item_id=o.delivery_item_id)
        for o in overrides
        if o.rule_id not in yaml_rule_ids
    ]
    for f in findings:
        logger.warning(
            "RUL-W002: " + format_code("RUL-W002", rule_id=f.rule_id, delivery_item_id=f.delivery_item_id)
        )
    return findings
