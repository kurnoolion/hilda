"""messenger module -- FR-10 corp messenger channel for cross-channel escalation.

Per architect Q-M1..Q-M6 lock 2026-06-25. See core/src/messenger/MODULE.md.

Public surface:
- `MessengerAdapter` Protocol + `EscalationReason` enum + `SendResult` dataclass
- `MessengerConfig` (3-tier config per [D-025])
- `MessengerService` (top-level orchestrator -- workflow_engine ESCALATE entry)
- `CorpMessengerAdapter` (HILDA-side base class per [D-027] Teacher/Student)
- `compose_escalation` (Jinja2 escalation message composer per Q-M6)
- `DailyLimitChecker` (Q-M2 daily-limit enforcement + audit-log write)
- `MockMessengerAdapter` + `InMemoryMessengerStorage` (tests + --mock CLI)
"""
from core.src.messenger.composer import (
    ComposeResult,
    REQUIRED_TEMPLATE_VARS,
    compose_escalation,
    get_template_env,
)
from core.src.messenger.config import MessengerConfig
from core.src.messenger.corp_messenger import CorpMessengerAdapter
from core.src.messenger.daily_limit import DailyLimitChecker, MessengerStorageProtocol
from core.src.messenger.mocks import InMemoryMessengerStorage, MockMessengerAdapter
from core.src.messenger.protocol import EscalationReason, MessengerAdapter, SendResult
from core.src.messenger.service import MessengerService
from core.src.messenger.utils import (
    TRUNCATION_MARKER,
    redact_owner_corp_id,
    truncate_message,
    validate_message_length,
)

__all__ = [
    # Protocol surface
    "MessengerAdapter",
    "EscalationReason",
    "SendResult",
    # Config
    "MessengerConfig",
    # Composer
    "compose_escalation",
    "ComposeResult",
    "get_template_env",
    "REQUIRED_TEMPLATE_VARS",
    # Daily-limit + storage protocol
    "DailyLimitChecker",
    "MessengerStorageProtocol",
    # Adapters
    "CorpMessengerAdapter",
    "MockMessengerAdapter",
    # Storage shim
    "InMemoryMessengerStorage",
    # Service
    "MessengerService",
    # Utilities
    "TRUNCATION_MARKER",
    "validate_message_length",
    "truncate_message",
    "redact_owner_corp_id",
]
