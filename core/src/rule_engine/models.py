"""Models for rule_engine per MODULE.md Public surface.

Frozen dataclasses + enums. Shape conformance for the Rule.kind discriminator is
enforced at construction (__post_init__); loader.py wraps violations into RUL-E002
PipelineErrors with the source-file path it alone knows.

RuleScope is imported from template_schema (canonical "Global" / "Customer" / "Device")
per the 2026-06-12 architect ruling — no local lowercase duplicate.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from core.src.template_schema import RuleScope

__all__ = [
    "ITEM_MODIFIED_SUB_TRIGGERS_PH1",
    "ActionKind",
    "EntityRef",
    "PollingScheduleTier",
    "Rule",
    "RuleAction",
    "RuleKind",
    "RuleMatch",
    "RuleScope",
    "TriggerEvent",
    "TriggerKind",
]


class TriggerKind(str, Enum):
    """All Ph-1 triggers per FR-28 (15 counting the 3 ItemModified sub-triggers)."""

    ITEM_CREATED = "ItemCreated"
    ITEM_MODIFIED = "ItemModified"  # sub-trigger discriminator carried in TriggerEvent.sub_trigger
    STATE_CHANGE = "StateChange"
    OWNER_STATUS_CONFIRMED = "OwnerStatusConfirmed"
    LAST_CONTACT_THRESHOLD = "LastContactThreshold"
    DEADLINE_PROXIMITY = "DeadlineProximity"
    ATTACHMENT_RECEIVED = "AttachmentReceived"
    AI_REVIEW_RESULT = "AIReviewResult"
    PM_APPROVAL = "PMApproval"
    TRACKER_CREATED = "TrackerCreated"
    MILESTONE_ALL_CLOSED = "MilestoneAllClosed"
    COLLECTION_PHASE_CLOSURE_REACHED = "CollectionPhaseClosureReached"
    CREDENTIAL_EXPIRED = "CredentialExpired"


# ItemModified sub-triggers (Ph-1) — discriminated by TriggerEvent.sub_trigger / Rule.sub_trigger.
# Membership is validated at load time (loader.py), not at model construction — Ph-2 may extend
# sub-triggers via registry per the D-DRAFT-3 ownership decision.
ITEM_MODIFIED_SUB_TRIGGERS_PH1 = {"OwnerReassigned", "DeadlineMoved", "TagsModified"}


class ActionKind(str, Enum):
    """Ph-1 actions per FR-29. Ph-2 actions are in MODULE.md ## Deferred and NOT in this enum."""

    SEND_REMINDER = "SendReminder"
    ESCALATE = "Escalate"
    UPDATE_STATE = "UpdateState"
    START_ITEM_COLLECTION = "StartItemCollection"
    SEND_INITIAL_OUTREACH = "SendInitialOutreach"
    NOTIFY_NEW_OWNER = "NotifyNewOwner"
    TRIGGER_PARSER = "TriggerParser"
    TRIGGER_AI_REVIEW = "TriggerAIReview"
    QUEUE_SUBMISSION = "QueueSubmission"
    NOTIFY_PM = "NotifyPM"
    NOTIFY_HILDA_OPS = "NotifyHildaOps"
    INSTANTIATE_DEFAULT_WORK_ITEM = "InstantiateDefaultWorkItem"
    MILESTONE_STORAGE_CLEANUP = "MilestoneStorageCleanup"
    HALT_MILESTONE_POLLING = "HaltMilestonePolling"
    FINAL_SWEEP = "FinalSweep"
    REASSIGN_DOCUMENT_TO_WORK_ITEM = "ReassignDocumentToWorkItem"
    PROPAGATE_TAGS_TO_ACTIVE_TRACKERS = "PropagateTagsToActiveTrackers"
    REARM_DEADLINE_PROXIMITY = "RearmDeadlineProximity"  # internal re-arm on DeadlineMoved sub-trigger


class RuleKind(str, Enum):
    """Discriminates Rule shape — trigger-action rules fire from TriggerEvents; polling-schedule
    rules are consumed on-demand by workflow_engine's polling scheduler (not evaluator-fired)."""

    TRIGGER_ACTION = "trigger_action"
    POLLING_SCHEDULE = "polling_schedule"


@dataclass(frozen=True)
class PollingScheduleTier:
    """One breakpoint in a deadline-tiered polling_schedule per FR-23 / FR-55."""

    days_before_deadline: int | None  # None = baseline tier (always-applies fallback)
    interval_minutes: int


@dataclass(frozen=True)
class EntityRef:
    """Identifies the entity a TriggerEvent or rule applies to. Not all fields required for all triggers."""

    customer_slug: str
    device_slug: str | None = None
    milestone_id: str | None = None
    delivery_item_id: str | None = None


@dataclass(frozen=True)
class RuleAction:
    """One action within a rule's ordered action list. Action parameters are
    action-instance-specific dicts; the rule_engine does NOT execute the action —
    workflow_engine consumes (kind, params) and dispatches to the right module."""

    kind: ActionKind
    params: dict[str, Any]
    sequence: int  # 0-indexed position within the rule's actions list (informational)


@dataclass(frozen=True)
class Rule:
    """A single rule loaded from YAML (or its Postgres-override variant). Two shapes via `kind`
    discriminator: TRIGGER_ACTION rules fire from TriggerEvents (carry trigger/sub_trigger/
    condition/actions); POLLING_SCHEDULE rules are consumed on-demand by workflow_engine's
    polling scheduler (carry tiers). Shape conformance enforced in __post_init__; loader
    surfaces violations as RUL-E002."""

    rule_id: str  # globally unique within {scope, scope_keys, kind} bucket
    kind: RuleKind
    scope: RuleScope
    scope_keys: dict[str, str]  # {} global; {"customer_slug": ...} customer; +{"device_slug": ...} device
    source: Literal["yaml", "postgres_override"]
    source_file: str | None  # YAML file path for "yaml"; None for "postgres_override"
    source_tier: RuleScope | Literal["postgres_override"]  # for FR-31 sub-2 "overridden from X" UI surfacing
    # Trigger-action fields (populated when kind == TRIGGER_ACTION; None/empty when POLLING_SCHEDULE):
    trigger: TriggerKind | None = None
    sub_trigger: str | None = None  # required when trigger == ITEM_MODIFIED; else None
    condition: dict[str, Any] | None = None  # declarative dict shape, NOT arbitrary code — see MODULE.md Invariants
    actions: tuple[RuleAction, ...] = ()  # ordered; intra-rule order = YAML declaration order
    # Polling-schedule fields (populated when kind == POLLING_SCHEDULE; empty when TRIGGER_ACTION):
    tiers: tuple[PollingScheduleTier, ...] = ()  # deadline-tiered breakpoints per FR-23 / FR-55

    def __post_init__(self) -> None:
        if self.kind is RuleKind.TRIGGER_ACTION:
            if self.trigger is None:
                raise ValueError(f"rule '{self.rule_id}': kind=trigger_action requires a trigger")
            if not self.actions:
                raise ValueError(f"rule '{self.rule_id}': kind=trigger_action requires a non-empty actions list")
            if self.tiers:
                raise ValueError(f"rule '{self.rule_id}': kind=trigger_action must not carry polling tiers")
            if self.trigger is TriggerKind.ITEM_MODIFIED and self.sub_trigger is None:
                raise ValueError(f"rule '{self.rule_id}': trigger=ItemModified requires sub_trigger")
            if self.trigger is not TriggerKind.ITEM_MODIFIED and self.sub_trigger is not None:
                raise ValueError(f"rule '{self.rule_id}': sub_trigger is only valid with trigger=ItemModified")
        elif self.kind is RuleKind.POLLING_SCHEDULE:
            if not self.tiers:
                raise ValueError(f"rule '{self.rule_id}': kind=polling_schedule requires a non-empty tiers list")
            if self.trigger is not None or self.sub_trigger is not None or self.condition is not None or self.actions:
                raise ValueError(
                    f"rule '{self.rule_id}': kind=polling_schedule must not carry trigger/sub_trigger/condition/actions"
                )


@dataclass(frozen=True)
class TriggerEvent:
    """Fired by callers (workflow_engine task bodies, ingest pipelines, etc.) and passed to evaluate()."""

    trigger: TriggerKind
    sub_trigger: str | None
    entity_ref: EntityRef
    field_deltas: dict[str, tuple[Any, Any]] | None  # for ItemModified: {field_name: (old, new)}
    timestamp: datetime  # event timestamp; used for time-window evaluations
    correlation_id: str  # threads through to RuleMatch + downstream Celery tasks for tracing
    # Caller-supplied derived facts conditions reference (e.g. doc_count_reached,
    # review_required — MODULE.md Worked Example 3 "caller also supplies derived fields").
    # Additive 2026-06-12 (soft-flag): condition lookup checks here first, then the
    # new-value side of field_deltas.
    derived_fields: dict[str, Any] | None = None


@dataclass(frozen=True)
class RuleMatch:
    """One matched rule + its action list. Multiple RuleMatch instances per TriggerEvent are common
    (different rule_ids matching the same trigger); workflow_engine schedules each as an
    independent Celery task chain."""

    rule_id: str
    matched_scope: RuleScope  # the winning scope after FR-30 ladder + FR-31 override
    actions: tuple[RuleAction, ...]  # ordered; from rule.actions verbatim
    pause_state: Literal["active", "paused"]  # FR-31 sub-1; paused matches returned but flagged
    override_source: Literal["yaml", "postgres_override"]
    correlation_id: str  # passed through from TriggerEvent
