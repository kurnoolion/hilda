"""Test/CLI mock implementations for email_service.

Used by:
- core/tests/test_email_service.py
- email_service_cli.py --mock mode

Each conforms to the matching Protocol surface in protocol.py + receiver/sender
Protocols. None of these touch network or filesystem.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from core.src.credential_service.protocol import Credential
from core.src.email_service.protocol import (
    InboundMessage,
)

__all__ = [
    "MockImapReceiver",
    "MockSmtpSender",
    "FakeCredentialService",
    "InMemoryStorage",
]


@dataclass
class MockImapReceiver:
    """In-memory EmailReceiver -- returns pre-loaded fixtures."""

    messages: list[InboundMessage] = field(default_factory=list)
    processed: list[str] = field(default_factory=list)

    async def fetch_new(self) -> AsyncIterator[InboundMessage]:
        for m in self.messages:
            yield m

    async def fetch_once(self) -> list[InboundMessage]:
        return list(self.messages)

    async def mark_processed(self, message_id: str) -> None:
        self.processed.append(message_id)


@dataclass
class MockSmtpSender:
    """In-memory EmailSender -- records outbound messages.

    Each call appends a dict to `sent`; returns a synthesized Message-ID.
    Raises RuntimeError when `raise_on_send` is True (for EML-E004 path tests).
    """

    sent: list[dict[str, Any]] = field(default_factory=list)
    raise_on_send: bool = False

    async def send(
        self,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> str:
        if self.raise_on_send:
            raise RuntimeError("MockSmtpSender configured to raise")
        message_id = f"<{uuid.uuid4()}@mock.local>"
        self.sent.append({
            "message_id":  message_id,
            "to":          list(to),
            "cc":          list(cc),
            "subject":     subject,
            "body":        body,
            "in_reply_to": in_reply_to,
        })
        return message_id


@dataclass
class FakeCredentialService:
    """Async credential_service stub for tests. Same shape as
    customer_adapter test fixture."""

    cred: Credential | None = None
    raise_on_lookup: bool = False
    calls: list[tuple[str, str, str | None]] = field(default_factory=list)

    async def get_credential(
        self,
        pm_id: str,
        system_type: str,
        customer_id: str | None = None,
    ) -> Credential:
        self.calls.append((pm_id, system_type, customer_id))
        if self.raise_on_lookup:
            from core.src.diagnostics.error_codes import PipelineError
            raise PipelineError(
                "CRD-E001",
                context={"pm_id": pm_id, "system": system_type,
                         "customer_id": customer_id or ""},
            )
        if self.cred is None:
            return Credential(
                pm_id=pm_id,
                system_type=system_type,
                auth_type="basic",
                username="hilda-ops",
                password="<test-redacted>",
            )
        return self.cred


@dataclass
class InMemoryStorage:
    """Minimal StorageBackend Protocol impl for Fr52AttachmentRouter tests.

    Records calls + holds an in-memory document_index by file_hash.
    """

    index: dict[str, Any] = field(default_factory=dict)
    associations: list[Any] = field(default_factory=list)
    written_files: list[Any] = field(default_factory=list)
    communications: list[Any] = field(default_factory=list)
    slugs_for_item: dict[tuple[str, str], list[str]] = field(default_factory=dict)

    async def get_document_index_row_by_hash(self, file_hash: str) -> Any:
        return self.index.get(file_hash)

    async def add_document_index_row(self, row: Any) -> None:
        fh = getattr(row, "file_hash", None) or row.get("file_hash")  # type: ignore[union-attr]
        self.index[fh] = row

    async def add_document_item_association(self, assoc: Any) -> None:
        self.associations.append(assoc)

    async def find_doc_id_slugs_for_item(
        self, delivery_item_id: str, doc_type: Any
    ) -> list[str]:
        key = (delivery_item_id, getattr(doc_type, "value", str(doc_type)))
        return list(self.slugs_for_item.get(key, []))

    async def write_file(self, path: Any, content: Any) -> None:
        self.written_files.append((path, content))

    async def log_communication(self, row: Any) -> None:
        self.communications.append(row)
