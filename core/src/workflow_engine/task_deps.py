"""Task-body dependency injection.

Celery task bodies cannot receive concrete Storage / SpWriter / AuditWriter
objects through args (serialization constraint). Pattern:

- Production: worker startup signal handler initializes the deps registry via
  `set_task_deps(TaskDeps(storage=..., sp_writer=..., audit=...))`.
- Tests: override the deps via `set_task_deps` (the autouse fixture in
  test_workflow_engine_tasks.py snapshots + restores).

Task bodies call `get_task_deps()` to resolve their dependencies at run time.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Iterator

from core.src.diagnostics import PipelineError

# Forward refs to Protocols defined in tracker (re-exported here via TYPE_CHECKING
# would be cleanest, but tracker.protocols already uses structural typing -- a plain
# `Any` annotation keeps the import surface clean).
from core.src.tracker import AuditWriter, SpWriter, StorageWriter

__all__ = ["TaskDeps", "get_task_deps", "set_task_deps", "override_task_deps"]


@dataclass(frozen=True)
class TaskDeps:
    """Bundle of runtime dependencies task bodies need. Constructed once at worker
    startup (production) or per-test (fixtures).

    Optional fields added 2026-06-27 per 6-ActionKind wire-up cascade: downstream
    module adapters (email_sender, messenger, customer_adapter) are None Ph-1 dev
    setups where the downstream module isn't yet wired into the worker boot. Each
    task body gracefully degrades to "audit-only" when its required dep is None.
    """

    storage:   StorageWriter
    sp_writer: SpWriter
    audit:     AuditWriter

    # Downstream module adapters (Optional Ph-1 -- task bodies degrade to audit-only
    # when None). Wired at worker startup in production; injected by tests via
    # override_task_deps.
    email_sender:    Any = None        # EmailSender protocol (email_service)
    messenger:       Any = None        # MessengerAdapter or MessengerService (messenger)
    customer_adapter: Any = None       # CustomerAdapter protocol (customer_adapter)


_deps: TaskDeps | None = None


def get_task_deps() -> TaskDeps:
    """Return the current TaskDeps. Raises if not initialised -- production worker
    startup signal handler must call `set_task_deps` before any task body runs;
    tests must set via fixture."""
    if _deps is None:
        raise PipelineError(
            "WFL-E004",
            {"reason": "TaskDeps not initialized; call set_task_deps() before dispatching tasks"},
        )
    return _deps


def set_task_deps(deps: TaskDeps) -> None:
    """Install the runtime TaskDeps bundle. Idempotent re-set is allowed."""
    global _deps
    _deps = deps


@contextmanager
def override_task_deps(deps: TaskDeps) -> Iterator[None]:
    """Context manager for tests -- swap deps, restore on exit."""
    global _deps
    snapshot = _deps
    _deps = deps
    try:
        yield
    finally:
        _deps = snapshot
