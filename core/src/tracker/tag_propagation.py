"""FR-82 cross-tracker tag propagation — TagsModified handler.

Called by workflow_engine PROPAGATE_TAGS_TO_ACTIVE_TRACKERS task body when
`ItemModified.TagsModified` trigger fires (TPM edited `item_description` on
a Deliverables row).

Propagates the new nested tag-set (list of synonym groups per architect
lock 2026-06-20) to ALL items matching `(customer_id, tg_name, item_no)`
across all active milestones + devices for that customer. Idempotent:
re-running with the same nested tag-set is a no-op (JSON equality check
on item_description per item).

Ph-1/Ph-2 boundaries:
- Nested tag-set semantics are Ph-1 (architect lock 2026-06-20).
- Subset-overlap detection per TSC-W007 fires at template_schema validation
  time, NOT at propagation time -- propagation is best-effort.
- Ph-2 may add `tag_history` audit table tracking which tags applied to
  which items at submission time (per MODULE.md Deferred section); Ph-1
  doesn't persist a history.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.src.tracker.protocols import AuditWriter, SpWriter, StorageWriter
from core.src.tracker.state_machine import DeliveryState

__all__ = [
    "TagPropagationResult",
    "propagate_tags_to_active_trackers",
]


# Terminal states per FR-82: items in CLOSED / SUBMITTED_TO_CUSTOMER no
# longer accept tag updates (tags don't affect routing once dispatched).
_TERMINAL_STATES: frozenset[DeliveryState] = frozenset({
    DeliveryState.CLOSED,
    DeliveryState.SUBMITTED_TO_CUSTOMER,
})


@dataclass(frozen=True)
class TagPropagationResult:
    customer_id: str
    tg_name: str
    item_no: int
    new_tags: list[list[str]] | None    # nested tag-set per FR-82
    propagated_count: int
    skipped_count: int
    skipped_item_ids: list[str] = field(default_factory=list)
    correlation_id: str = ""


def _dedup_synonym_group(group: list[str]) -> tuple[str, ...]:
    """Canonicalize a synonym group: dedup case-preserving by lowercase
    comparison, sort by lowercase, return as immutable tuple for outer dedup.
    """
    seen_lower: set[str] = set()
    out: list[tuple[str, str]] = []
    for tag in group:
        if not isinstance(tag, str):
            continue
        normalized = tag.strip()
        if not normalized:
            continue
        key = normalized.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        out.append((key, normalized))
    out.sort(key=lambda x: x[0])
    return tuple(tag for _, tag in out)


def _dedup_nested_tags(
    new_tags: list[list[str]] | None,
) -> list[list[str]] | None:
    """Canonicalize nested tag-set:
    - Dedup within each group (synonym deduplication).
    - Dedup across groups (no duplicate synonym groups).
    Returns None if input is None; preserves [] if input is empty.
    """
    if new_tags is None:
        return None
    seen_groups: set[tuple[str, ...]] = set()
    deduped: list[list[str]] = []
    for group in new_tags:
        if not isinstance(group, list):
            continue
        canonical = _dedup_synonym_group(group)
        if not canonical or canonical in seen_groups:
            continue
        seen_groups.add(canonical)
        deduped.append(list(canonical))
    return deduped


def _tags_equal(a: list[list[str]] | None, b: list[list[str]] | None) -> bool:
    """Idempotency check: are two nested tag-sets equivalent?

    Equivalence is set-of-canonicalized-groups: order doesn't matter at the
    outer list level or within a group; comparison is case-insensitive
    (stored form preserves first-seen case for display, but equality check
    normalizes to lowercase keys).
    """
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False

    def to_lower_keyset(nested: list[list[str]]) -> set[tuple[str, ...]]:
        out: set[tuple[str, ...]] = set()
        for group in nested:
            if not isinstance(group, list):
                continue
            keys = sorted({
                t.strip().lower()
                for t in group
                if isinstance(t, str) and t.strip()
            })
            if keys:
                out.add(tuple(keys))
        return out

    return to_lower_keyset(a) == to_lower_keyset(b)


def propagate_tags_to_active_trackers(
    customer_id: str,
    tg_name: str,
    item_no: int,
    new_tags: list[list[str]] | None,
    pm_id: str,
    storage: StorageWriter,
    sp_writer: SpWriter,
    audit: AuditWriter,
    event_context: dict[str, Any],
) -> TagPropagationResult:
    """FR-82 propagation. See module docstring."""
    correlation_id = event_context.get("correlation_id", "")
    deduped_tags = _dedup_nested_tags(new_tags)

    matched_items = storage.find_items_by_natural_key(
        customer_id=customer_id,
        tg_name=tg_name,
        item_no=item_no,
        only_active=True,
    )

    propagated = 0
    skipped: list[str] = []

    for item in matched_items:
        item_id = getattr(item, "delivery_item_id", None) or getattr(item, "item_id", None)
        if item_id is None:
            # Defensive: storage Protocol should always yield an id.
            continue
        if item.delivery_state in _TERMINAL_STATES:
            audit.write_communication_log(
                action_type="tag_propagation_skipped_terminal",
                delivery_item_id=item_id,
                attribution={
                    "trigger_source": event_context.get("trigger_source", "automated"),
                    "correlation_id": correlation_id,
                    "modified_by": pm_id,
                },
                details={
                    "state": item.delivery_state.value if hasattr(item.delivery_state, "value") else str(item.delivery_state),
                    "code": "TRK-W002",
                },
            )
            skipped.append(item_id)
            continue

        # Idempotency check: skip if item_description already matches deduped_tags
        existing_tags = getattr(item, "item_description", None)
        if _tags_equal(existing_tags, deduped_tags):
            # No-op write; do not bump propagated_count for a true no-op.
            continue

        # Write the update (storage + SP atomic at workflow_engine task-body layer).
        storage.update_delivery_item(item_id, {"item_description": deduped_tags})
        sp_writer.update_item(
            entity="delivery_items",
            scope=event_context.get("sp_scope"),
            item_id=item_id,
            canonical_fields={"item_description": deduped_tags},
        )
        audit.write_communication_log(
            action_type="tag_catalog_propagation",
            delivery_item_id=item_id,
            attribution={
                "trigger_source": event_context.get("trigger_source", "automated"),
                "correlation_id": correlation_id,
                "modified_by": pm_id,
            },
            details={
                "group_count": len(deduped_tags) if deduped_tags else 0,
                "total_synonym_count": sum(len(g) for g in deduped_tags) if deduped_tags else 0,
                "modified_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        propagated += 1

    return TagPropagationResult(
        customer_id=customer_id,
        tg_name=tg_name,
        item_no=item_no,
        new_tags=deduped_tags,
        propagated_count=propagated,
        skipped_count=len(skipped),
        skipped_item_ids=skipped,
        correlation_id=correlation_id,
    )
