"""Example per-customer corp messenger adapter scaffold.

Per architect Q-M1..Q-M6 lock 2026-06-25 + `[D-027]` Teacher/Student split.

COPY this file to `<customer_id>_corp_messenger_adapter.py` (e.g.,
`test_carrier_a_corp_messenger_adapter.py` -- or per-deployment if the corp
messenger gateway is shared across customers), then fill in the `# TODO(cline)`
markers with the actual corp messenger gateway binding call body. The single
binding API is already available in the corp environment per architect spec
2026-06-25:

    bool sendMessage(owner_corp_id, message)
        -> True when owner RECEIVED the message (per Q-M1)
        -> False on failure (gateway-rejected, owner-not-reachable, etc.)

NEVER lands on public github per NFR-2 -- per-customer subclass is the
proprietary boundary.
"""
from __future__ import annotations

from core.src.messenger.config import MessengerConfig
from core.src.messenger.corp_messenger.adapter import CorpMessengerAdapter


# TODO(cline): import the actual corp messenger gateway binding module from
# the Work PC env, e.g.,
# from corp_messenger_gateway_client import sendMessage


class ExampleCorpMessengerAdapter(CorpMessengerAdapter):
    """Per-customer corp messenger adapter scaffold.

    Subclass instances may be constructed per customer_id, or shared across
    customers if the corp messenger gateway is multi-tenant. The binding may
    share state across customers or be per-customer.
    """

    source_system: str = "corp_messenger/example"

    def __init__(
        self,
        config: MessengerConfig,
        customer_id: str = "example",
    ) -> None:
        super().__init__(config=config, customer_id=customer_id)

    async def _invoke_send_message(self, owner_corp_id: str, message: str) -> bool:
        # TODO(cline): call the corp messenger gateway binding
        # return sendMessage(owner_corp_id, message)
        raise NotImplementedError(
            "TODO(cline): wire corp messenger sendMessage binding here per [D-027]"
        )


def make_corp_messenger_adapter(
    config: MessengerConfig, customer_id: str = "example"
) -> ExampleCorpMessengerAdapter:
    """Factory for messenger_cli --send / --diagnostic resolution."""
    return ExampleCorpMessengerAdapter(config=config, customer_id=customer_id)
