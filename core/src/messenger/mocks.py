"""Test/CLI mock implementations for messenger.

Used by:
- core/tests/test_messenger.py
- messenger_cli.py --mock mode

`MockMessengerAdapter` conforms to MessengerAdapter Protocol (`source_system`
attr + async `send`). None of these touch network or filesystem.

`InMemoryMessengerStorage` is a minimal MessengerStorageProtocol impl --
records log_communication calls + supports query_communications with the
subset of filters DailyLimitChecker actually uses (channel, action_type,
since, until).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = ["MockMessengerAdapter", "InMemoryMessengerStorage"]


@dataclass
class MockMessengerAdapter:
    """In-memory MessengerAdapter -- returns canned bool responses.

    Per architect Q-M1 semantics: bool return value == owner received.

    - `register_response(owner_corp_id, value)` sets a per-owner canned response.
    - `set_default(value)` sets the fallback when no per-owner response registered.
    - `calls` records every (owner_corp_id, message) pair for assertions.
    - `set_force_failure(owner_corp_id)` is sugar for register_response(owner, False).

    Set `raise_on_send` (per-owner via `_raise_for`) to simulate gateway exceptions.
    """

    source_system: str = "mock_corp_messenger"
    default_response: bool = True
    responses: dict[str, bool] = field(default_factory=dict)
    calls: list[tuple[str, str]] = field(default_factory=list)
    _raise_for: set[str] = field(default_factory=set)

    def register_response(self, owner_corp_id: str, value: bool) -> None:
        self.responses[owner_corp_id] = value

    def set_default(self, value: bool) -> None:
        self.default_response = value

    def set_force_failure(self, owner_corp_id: str) -> None:
        self.responses[owner_corp_id] = False

    def set_force_exception(self, owner_corp_id: str) -> None:
        self._raise_for.add(owner_corp_id)

    async def send(self, owner_corp_id: str, message: str) -> bool:
        self.calls.append((owner_corp_id, message))
        if owner_corp_id in self._raise_for:
            raise RuntimeError("MockMessengerAdapter forced exception")
        return self.responses.get(owner_corp_id, self.default_response)


@dataclass
class InMemoryMessengerStorage:
    """Minimal MessengerStorageProtocol impl for tests + --mock CLI.

    Records log_communication calls in a list; query_communications scans that
    list and returns rows matching channel + action_type + (since <= ts < until).
    """

    communications: list[Any] = field(default_factory=list)

    async def log_communication(self, row: Any) -> None:
        self.communications.append(row)

    async def query_communications(
        self,
        *,
        channel: Any = None,
        action_type: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        **kwargs: Any,
    ) -> list[Any]:
        out: list[Any] = []
        for row in self.communications:
            ch_val = getattr(getattr(row, "channel", None), "value", None) or getattr(row, "channel", None)
            wanted_ch = getattr(channel, "value", None) or channel
            if wanted_ch is not None and ch_val != wanted_ch:
                continue
            if action_type is not None and getattr(row, "action_type", None) != action_type:
                continue
            ts = getattr(row, "timestamp", None)
            if since is not None and ts is not None and ts < since:
                continue
            if until is not None and ts is not None and ts >= until:
                continue
            out.append(row)
        return out
