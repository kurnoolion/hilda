"""HILDA DeliveryItem lifecycle orchestrator (tracker).

Pure library: state machine + per-transition guards + the four cross-cutting
orchestration actions consumed by workflow_engine — update_delivery_state,
instantiate_default_workitem, reassign_document_to_workitem,
propagate_tags_to_active_trackers, plus apply_manual_tpm_override (FR-14
generic override path).

See `MODULE.md` for the curated design + Public surface contract.
"""
from core.src.tracker.default_workitem import instantiate_default_workitem
from core.src.tracker.guards import (
    GuardResult,
    TriggerSource,
    check_transition_guards,
    list_blocking_conditions_for_state,
)
from core.src.tracker.manual_override import apply_manual_tpm_override
from core.src.tracker.protocols import AuditWriter, SpWriter, StorageWriter
from core.src.tracker.reassignment import (
    ReassignmentResult,
    RevisionClassification,
    reassign_document_to_workitem,
)
from core.src.tracker.state_machine import (
    LEGAL_TRANSITIONS,
    DeliveryState,
    transition_legal,
)
from core.src.tracker.tag_propagation import (
    TagPropagationResult,
    propagate_tags_to_active_trackers,
)
from core.src.tracker.transitions import (
    StateChangeDispatchSignal,
    TransitionOutcome,
    TransitionResult,
    advance_on_doc_count_reached,
    auto_advance_owner_closed_to_under_pm_review,
    update_delivery_state,
)

__all__ = [
    # State machine
    "DeliveryState",
    "LEGAL_TRANSITIONS",
    "transition_legal",
    # Guards
    "GuardResult",
    "TriggerSource",
    "check_transition_guards",
    "list_blocking_conditions_for_state",
    # Transitions
    "TransitionOutcome",
    "TransitionResult",
    "StateChangeDispatchSignal",
    "update_delivery_state",
    "advance_on_doc_count_reached",
    "auto_advance_owner_closed_to_under_pm_review",
    # Default work-item
    "instantiate_default_workitem",
    # Reassignment
    "ReassignmentResult",
    "RevisionClassification",
    "reassign_document_to_workitem",
    # Tag propagation
    "TagPropagationResult",
    "propagate_tags_to_active_trackers",
    # Manual override
    "apply_manual_tpm_override",
    # Protocols
    "StorageWriter",
    "SpWriter",
    "AuditWriter",
]
