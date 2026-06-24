"""messenger Protocol surface -- MessengerAdapter + EscalationReason + SendResult.

Per architect Q-M1..Q-M6 lock 2026-06-25 (FR-10 cross-channel escalation).

HILDA owns the Protocol contract + the result shape; the corp messenger gateway
binding owns the actual sendMessage call body. Per [D-027] Teacher/Student split:
HILDA-side Protocol + base adapter authored by Claude on Personal PC; concrete
per-customer binding-import filled in by Cline on Work PC at
`customizations/messenger/<customer_id>_corp_messenger_adapter.py`.

The single API wrapped (per architect Q-M1):

    bool sendMessage(owner_corp_id, message) -> True when owner RECEIVES;
                                                 False on failure

See core/src/messenger/MODULE.md.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

__all__ = ["EscalationReason", "SendResult", "MessengerAdapter"]


class EscalationReason(str, Enum):
    """Why an escalation message is being sent. Bounded enum tokens per NFR-2."""

    POST_REMINDER = "post_reminder"            # FR-10 after N email reminders
    DEADLINE_IMMINENT = "deadline_imminent"    # <3 days to deadline + still open
    MANUAL_TPM = "manual_tpm"                  # TPM-triggered ad hoc (Ph-2)


@dataclass(frozen=True)
class SendResult:
    """Result shape from MessengerService.send_escalation.

    Per architect Q-M1 lock 2026-06-25: `success` True == owner RECEIVED the
    message (stronger than gateway-accepted). `error_code` is one of the
    MSG-EXXX / MSG-W001 family (or None on success).
    """

    success: bool                       # True = owner received per Q-M1
    owner_corp_id: str                  # may appear in audit; redacted in RPT
    message_bytes: int                  # size of the message that was sent
    error_code: str | None              # MSG-E001..MSG-E003 / MSG-W001 / None
    sent_at: datetime


@runtime_checkable
class MessengerAdapter(Protocol):
    """All callers depend on this Protocol, not on a concrete subclass.

    Implementations (Ph-1):
    - `CorpMessengerAdapter` (HILDA-side base class; subclass per customer)
    - per-customer subclass under `customizations/messenger/`
    - `MockMessengerAdapter` (tests)

    Per architect Q-M1 / Q-M3 lock 2026-06-25:
    - Returns True only when owner RECEIVES (not just gateway-accepted).
    - Sender attribution is "anonymous HILDA BOT" -- no TPM identity preamble
      needed in the composed message body.

    Composition + daily-limit + audit + retry live in `MessengerService` (not
    the adapter). The adapter is the thin transport-only surface.
    """

    source_system: str       # bounded enum token; identifies the customer / deployment

    async def send(self, owner_corp_id: str, message: str) -> bool:
        """Send one message to one owner. See class docstring."""
        ...
