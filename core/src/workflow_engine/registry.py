"""ActionKind -> Celery task registry + chain builder.

Each downstream task module (`tasks/state.py`, `tasks/outreach.py`, etc.) registers
its (ActionKind -> TaskBinding) mappings at import time via `register_task_binding`.
The `ACTION_KIND_TO_TASK` dict is the single source of truth consumed by:
- `TriggerDispatcher.build_chain_from_rule_match` -- rule_engine-driven dispatch
- `manual_trigger.handle_manual_trigger` -- FR-31 sub-3 TPM SP UI manual dispatch

Per Guardrail #3 forward-compat: the registry includes ALL 18 Ph-1 ActionKinds at
startup; tasks register lazily as their modules import. CLI `--diagnostic` reports
registry_complete=True only when every ActionKind has a registered TaskBinding.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from celery import chain as celery_chain
from celery.canvas import Signature

from core.src.diagnostics import PipelineError
from core.src.rule_engine import ActionKind, RuleMatch

__all__ = [
    "TaskBinding",
    "RetryPolicy",
    "ACTION_KIND_TO_TASK",
    "register_task_binding",
    "build_chain_from_rule_match",
    "lookup_for_manual_trigger",
    "registry_complete",
    "expected_action_kinds_ph1",
]


@dataclass(frozen=True)
class RetryPolicy:
    """Per-task retry policy. workflow_engine sets sensible defaults; tasks can
    override via @task decorator kwargs."""

    max_retries:    int    = 5
    backoff_factor: float  = 2.0
    retry_on:       tuple[type[BaseException], ...] = ()


@dataclass(frozen=True)
class TaskBinding:
    """Maps one ActionKind to its Celery task callable + queue + retry policy."""

    action_kind:    ActionKind
    celery_task:    Callable[..., Any]    # @hilda_celery_app.task-decorated function
    queue:          str
    retry_policy:   RetryPolicy           = field(default_factory=RetryPolicy)


# Populated at module-import time by each tasks/*.py file. Ph-1 = 18 ActionKinds
# (3 of which -- TRIGGER_AI_REVIEW, TRIGGER_PARSER stubs -- delegate to modules not
# yet implemented; their task bodies log-and-skip until downstream is ready).
ACTION_KIND_TO_TASK: dict[ActionKind, TaskBinding] = {}


def expected_action_kinds_ph1() -> set[ActionKind]:
    """The full Ph-1 ActionKind set per rule_engine.ActionKind enum. Used by
    --diagnostic CLI to verify registry completeness."""
    return set(ActionKind)


def register_task_binding(binding: TaskBinding) -> None:
    """Register a TaskBinding. Idempotent for identical re-registration; raises
    on conflict (different binding for same ActionKind).

    Called by tasks/*.py modules at import time.
    """
    existing = ACTION_KIND_TO_TASK.get(binding.action_kind)
    if existing is not None:
        # Identity check: same callable + queue counts as no-op (defensive against
        # double-import in tests).
        if (existing.celery_task is binding.celery_task
                and existing.queue == binding.queue):
            return
        raise ValueError(
            f"ActionKind {binding.action_kind} already registered with different binding"
        )
    ACTION_KIND_TO_TASK[binding.action_kind] = binding


def registry_complete() -> bool:
    """True iff every ActionKind in rule_engine.ActionKind has a TaskBinding."""
    return expected_action_kinds_ph1().issubset(ACTION_KIND_TO_TASK.keys())


def build_chain_from_rule_match(
    match: RuleMatch,
    event_context: dict[str, Any],
) -> Signature:
    """Per [D-066]: build one Celery chain from a RuleMatch's ordered actions list.

    Each chain task receives `(params, event_context, previous_result=None)` per
    the common task shape. Returns a Celery `Signature` ready for `.apply_async()`.

    Raises WFL-E001 if any action_kind in the match is not in the registry.
    """
    if not match.actions:
        raise ValueError(f"RuleMatch '{match.rule_id}' has empty actions list")

    signatures: list[Signature] = []
    for action in match.actions:
        binding = ACTION_KIND_TO_TASK.get(action.kind)
        if binding is None:
            raise PipelineError("WFL-E001", {"action_kind": action.kind.value})
        # Pass (params, event_context) -- previous_result is added by Celery canvas
        # when chained tasks return a value.
        sig = binding.celery_task.s(action.params, event_context)
        signatures.append(sig)

    if len(signatures) == 1:
        return signatures[0]
    return celery_chain(*signatures)


def lookup_for_manual_trigger(action_kind: ActionKind) -> TaskBinding:
    """Per FR-31 sub-3 TPM SP UI manual trigger dispatch -- bypasses rule_engine.
    Caller (manual_trigger.handle_manual_trigger) calls
    binding.celery_task.apply_async(...) directly.

    Raises WFL-E001 if action_kind not in registry.
    """
    binding = ACTION_KIND_TO_TASK.get(action_kind)
    if binding is None:
        raise PipelineError("WFL-E001", {"action_kind": action_kind.value})
    return binding
