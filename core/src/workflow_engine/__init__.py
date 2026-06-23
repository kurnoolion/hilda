"""workflow_engine -- Celery app + central task dispatcher per [D-022].

See core/src/workflow_engine/MODULE.md.
"""
from core.src.workflow_engine.celery_app import build_celery_app, hilda_celery_app
from core.src.workflow_engine.config import WorkflowEngineConfig
from core.src.workflow_engine.dispatcher import (
    DispatchResult,
    StorageLike,
    TriggerDispatcher,
)
from core.src.workflow_engine.manual_trigger import handle_manual_trigger
from core.src.workflow_engine.registry import (
    ACTION_KIND_TO_TASK,
    RetryPolicy,
    TaskBinding,
    build_chain_from_rule_match,
    expected_action_kinds_ph1,
    lookup_for_manual_trigger,
    register_task_binding,
    registry_complete,
)
from core.src.workflow_engine.task_deps import (
    TaskDeps,
    get_task_deps,
    override_task_deps,
    set_task_deps,
)
# Importing tasks/ at package init triggers register_task_binding for each
# task module via side effect.
from core.src.workflow_engine import tasks  # noqa: F401

__all__ = [
    "build_celery_app",
    "hilda_celery_app",
    "WorkflowEngineConfig",
    "DispatchResult",
    "StorageLike",
    "TriggerDispatcher",
    "ACTION_KIND_TO_TASK",
    "RetryPolicy",
    "TaskBinding",
    "build_chain_from_rule_match",
    "expected_action_kinds_ph1",
    "lookup_for_manual_trigger",
    "register_task_binding",
    "registry_complete",
    "handle_manual_trigger",
    "TaskDeps",
    "get_task_deps",
    "override_task_deps",
    "set_task_deps",
]
