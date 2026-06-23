"""workflow_engine task bodies grouped by domain.

Each module registers its ActionKind -> TaskBinding mappings at import time via
`register_task_binding`. Importing any sub-module triggers registration as a
side effect; the registry is the authoritative ACTION_KIND_TO_TASK source.

Task bodies are thin wrappers per workflow_engine MODULE.md Invariant: business
logic lives in the owning module (tracker, email_service, customer_adapter,
etc.); workflow_engine just coordinates the Celery scheduling.
"""
# Import-time registration: pulling in each tasks/<name>.py triggers the
# register_task_binding calls at module load. Order doesn't matter -- each
# binding is independent.
from core.src.workflow_engine.tasks import escalation, milestone, routing_resolution, state  # noqa: F401

__all__ = ["escalation", "milestone", "routing_resolution", "state"]
