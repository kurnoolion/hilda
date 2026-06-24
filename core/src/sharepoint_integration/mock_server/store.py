"""In-memory SP list store — pluggable backend for the mock server."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any, Iterator


class ListNotFoundError(KeyError):
    """Raised when a list referenced by name does not exist."""


@dataclass
class _AuditEntry:
    timestamp: datetime
    method: str
    list_name: str
    item_id: str | None
    body_summary: str  # bounded — counts of fields, not values


@dataclass
class InMemoryStore:
    """Thread-safe-ish in-memory backend.

    Single process; no persistence. Lists are addressed by display name
    (matching SP REST `getbytitle` semantics). Item IDs are monotonic
    per-list integers stringified.
    """

    _lists: dict[str, dict[str, dict[str, Any]]] = field(default_factory=dict)
    _next_id: dict[str, int] = field(default_factory=dict)
    _audit: list[_AuditEntry] = field(default_factory=list)
    _lock: RLock = field(default_factory=RLock)
    # Test-only knob: number of upcoming writes that should be forced to 403
    # to exercise the SpSession digest-refresh path per architect Q2
    # 2026-06-25. Decrements on each consumed write.
    digest_403_count: int = 0
    _digest_counter: int = 0

    def next_digest(self, base: str) -> str:
        """Return a fresh digest token. Each call returns a uniquely
        suffixed value so tests can assert that a refresh actually issued
        a new digest."""
        with self._lock:
            self._digest_counter += 1
            return f"{base}-{self._digest_counter}"

    def ensure_list(self, list_name: str) -> None:
        with self._lock:
            if list_name not in self._lists:
                self._lists[list_name] = {}
                self._next_id[list_name] = 1

    def list_names(self) -> list[str]:
        with self._lock:
            return sorted(self._lists.keys())

    def get_list(self, list_name: str) -> dict[str, dict[str, Any]]:
        with self._lock:
            if list_name not in self._lists:
                raise ListNotFoundError(list_name)
            return dict(self._lists[list_name])

    def iter_items(self, list_name: str) -> Iterator[dict[str, Any]]:
        with self._lock:
            if list_name not in self._lists:
                raise ListNotFoundError(list_name)
            for item_id, fields in self._lists[list_name].items():
                yield {"Id": int(item_id), **fields}

    def create_item(self, list_name: str, fields: dict[str, Any]) -> str:
        with self._lock:
            self.ensure_list(list_name)
            item_id = self._next_id[list_name]
            self._next_id[list_name] += 1
            self._lists[list_name][str(item_id)] = dict(fields)
            self._record("POST", list_name, str(item_id), fields)
            return str(item_id)

    def update_item(
        self, list_name: str, item_id: str, fields: dict[str, Any]
    ) -> None:
        with self._lock:
            if list_name not in self._lists or item_id not in self._lists[list_name]:
                raise ListNotFoundError(f"{list_name}/{item_id}")
            self._lists[list_name][item_id].update(fields)
            self._record("PATCH", list_name, item_id, fields)

    def delete_item(self, list_name: str, item_id: str) -> None:
        with self._lock:
            if list_name not in self._lists or item_id not in self._lists[list_name]:
                raise ListNotFoundError(f"{list_name}/{item_id}")
            del self._lists[list_name][item_id]
            self._record("DELETE", list_name, item_id, {})

    def audit_log(self) -> list[_AuditEntry]:
        with self._lock:
            return list(self._audit)

    def reset(self) -> None:
        with self._lock:
            self._lists.clear()
            self._next_id.clear()
            self._audit.clear()
            self.digest_403_count = 0
            self._digest_counter = 0

    def _record(
        self,
        method: str,
        list_name: str,
        item_id: str | None,
        fields: dict[str, Any],
    ) -> None:
        # Store only the field count, not values, to avoid leaking content
        # if this store is reused in production-adjacent contexts.
        summary = f"fields={len(fields)}"
        self._audit.append(
            _AuditEntry(
                timestamp=datetime.now(timezone.utc),
                method=method,
                list_name=list_name,
                item_id=item_id,
                body_summary=summary,
            )
        )
