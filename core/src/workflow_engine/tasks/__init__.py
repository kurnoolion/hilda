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
# Use relative imports per Python sub-package idiom: avoids
# "partially initialized module" ImportError that can surface when the
# package's own __init__.py uses absolute-path multi-name imports against
# itself (race between module attribute assignment and submodule load
# triggered by sibling decorator side effects).
from . import (  # noqa: F401
    email_polling,   # added 2026-06-27: periodic ews_receiver poll + dispatch
    escalation,
    milestone,
    inbound_attachment,  # added 2026-06-29: Step 5.5 process_inbound_attachments task (FR-52/FR-85/FR-86)
    outreach,        # added 2026-06-27: SEND_INITIAL_OUTREACH + SEND_REMINDER + NOTIFY_NEW_OWNER
    owner_reply,     # added 2026-06-28: Phase B apply_owner_reply task (HTML table parser)
    pm_approval,     # added 2026-06-28: Pattern A apply_pm_approval task (SP-authoritative mirror)
    routing_resolution,
    sp_alert_imports,  # added 2026-06-26: IMPORT_DELIVERABLE_TRACKER + KICKOFF_COLLECTION per [D-118] cascade
    state,
    submission,      # added 2026-06-27: ESCALATE + START_ITEM_COLLECTION + QUEUE_SUBMISSION
)

__all__ = [
    "email_polling",
    "escalation",
    "milestone",
    "inbound_attachment",
    "outreach",
    "owner_reply",
    "pm_approval",
    "routing_resolution",
    "sp_alert_imports",
    "state",
    "submission",
]
