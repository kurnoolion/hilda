"""CorpMessengerAdapter -- HILDA-side thin wrapper around the corp messenger gateway.

Per architect Q-M1..Q-M6 lock 2026-06-25 + `[D-027]` Teacher/Student split.

HILDA-side base class lives in core/; per-customer concrete subclass at
`customizations/messenger/<customer_id>_corp_messenger_adapter.py` overrides the
abstract `_invoke_send_message` method with the actual corp messenger binding
call body. NEVER lands on public github per NFR-2.

Default `_invoke_send_message` raises NotImplementedError MSG-E003 so tests
against the base class fail loudly when not subclassed.

HILDA owns:
- Q-M1 success semantics (returns True only when binding bool was True)
- Exception-swallowing per Q-M1 (binding raise -> False return + MSG-E001 log)
- MessengerAdapter Protocol conformance

Binding owns:
- The actual sendMessage(owner_corp_id, message) -> bool call
- Auth + transport + protocol details (proprietary; per [D-027])
"""
from __future__ import annotations

import logging

from core.src.diagnostics.error_codes import PipelineError
from core.src.messenger.config import MessengerConfig
from core.src.messenger.utils import redact_owner_corp_id

__all__ = ["CorpMessengerAdapter"]

logger = logging.getLogger(__name__)


class CorpMessengerAdapter:
    """Thin wrapper base class. Subclasses override `_invoke_send_message`.

    Conforms to the MessengerAdapter Protocol (`source_system` attr + async
    `send`). Default `source_system` is "corp_messenger"; per-customer
    subclasses may override.

    Subclasses MUST override:
    - `_invoke_send_message(owner_corp_id, message) -> bool`
    """

    source_system: str = "corp_messenger"

    def __init__(self, config: MessengerConfig, customer_id: str | None = None) -> None:
        self._config = config
        # Optional customer scoping -- a multi-tenant corp messenger gateway
        # may share one binding across customers; single-tenant deployments
        # bake the customer_id into the subclass + leave this None.
        self._customer_id = customer_id
        if customer_id:
            self.source_system = f"corp_messenger/{customer_id}"

    async def send(self, owner_corp_id: str, message: str) -> bool:
        """Per architect Q-M1 lock: True when owner RECEIVES the message;
        False on any failure (binding returned False, binding raised, or
        subclass not configured -> MSG-E003).

        Caller (MessengerService) is responsible for retry + daily-limit
        + audit logging. This adapter is the thin transport-only surface.
        """
        redacted = redact_owner_corp_id(owner_corp_id)
        try:
            result = await self._invoke_send_message(owner_corp_id, message)
        except NotImplementedError:
            # Surface base-class default loudly -- subclass missing per [D-027].
            logger.warning(
                "messenger_send_failed: code=MSG-E003 owner=%s system=%s",
                redacted, self.source_system,
            )
            raise PipelineError("MSG-E003")
        except Exception as exc:  # noqa: BLE001 -- opaque per Q-M1
            # NFR-2 -- never log the message body or owner_corp_id raw.
            reason_token = type(exc).__name__
            logger.warning(
                "messenger_send_failed: code=MSG-E001 owner=%s reason=%s system=%s",
                redacted, reason_token, self.source_system,
            )
            return False
        return bool(result)

    # ------------------------------------------------------------------
    # Abstract _invoke_* method (override in per-customer subclass)
    # ------------------------------------------------------------------

    async def _invoke_send_message(self, owner_corp_id: str, message: str) -> bool:
        """Per [D-027] -- override in per-customer subclass at
        `customizations/messenger/<customer_id>_corp_messenger_adapter.py`."""
        raise NotImplementedError(
            "MSG-E003: Override in customizations/messenger/<customer_id>_corp_messenger_adapter.py per [D-027]"
        )
