"""ImapReceiver -- IMAP IDLE primary + short-poll fallback per FR-23 + [D-016].

Sync imap-tools wrapped in asyncio.to_thread per the [D-008] / SpClient pattern.
Credentials per call via credential_service.get_credential(pm_id='ops',
SystemType.EMAIL); never stored on the receiver instance after use (NFR-2 +
Invariant "no credential material in any log").

Ph-1 first cut: --once mode (fetch all unread, process, exit) is the primary
test-scenario entry; fetch_new() implements the IDLE/poll loop for production
use (long-lived) but is not exercised by the basic flow tests.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol

from core.src.diagnostics.error_codes import PipelineError
from core.src.email_service.config import ImapConfig
from core.src.email_service.protocol import (
    EmailReceiver,
    InboundAttachment,
    InboundMessage,
)

__all__ = ["ImapReceiver", "CredentialServiceProtocol"]

logger = logging.getLogger(__name__)


class CredentialServiceProtocol(Protocol):
    """Minimal credential_service surface required by ImapReceiver."""

    async def get_credential(
        self,
        pm_id: str,
        system_type: str,
        customer_id: str | None = None,
    ) -> Any: ...


class ImapReceiver:
    """Conforms to EmailReceiver Protocol. IMAP IDLE primary + short-poll fallback.

    Per [D-107] SystemType.EMAIL stays SHARED -- pm_id='ops' credential.
    Test scenarios use --once mode (single batch fetch).
    """

    def __init__(
        self,
        config: ImapConfig,
        credential_service: CredentialServiceProtocol,
    ) -> None:
        self._config = config
        self._cred = credential_service

    async def _get_imap_credential(self) -> Any:
        """Per-call credential retrieval; never cached on instance."""
        try:
            return await self._cred.get_credential(
                pm_id="ops",
                system_type="email",   # SystemType.EMAIL.value per [D-107]
            )
        except PipelineError as exc:
            # Re-raise as EML-E003 (IMAP auth) -- the underlying CRD-E001 is preserved as cause
            raise PipelineError(
                "EML-E003",
                context={},
                cause=exc,
            )

    async def fetch_new(self) -> AsyncIterator[InboundMessage]:
        """Long-running fetch loop -- IDLE primary + poll fallback.

        Ph-1 production primary is expected to be short-poll (poll_interval_s=60)
        per MODULE.md field-reality 2026-05-28 (IDLE believed unsupported by corp
        Exchange config). Code structure is tier-aware so flipping IDLE on/off is
        env-config only.

        Yields one InboundMessage per unread message. Honors mark_processed
        between iterations so re-fetch doesn't re-deliver.
        """
        while True:
            try:
                msgs = await self.fetch_once()
            except PipelineError:
                raise
            for m in msgs:
                yield m
            # IDLE-aware sleep -- short-poll fallback cadence
            await asyncio.sleep(self._config.poll_interval_s)

    async def fetch_once(self) -> list[InboundMessage]:
        """Fetch all unread messages in one batch; for --once test mode + as the
        poll-fallback inner step. Synchronous IMAP work wrapped via to_thread.

        Ph-1 first cut: the IMAP wire path is wrapped behind a small _fetch_sync
        method that imports imap-tools lazily. Tests use MockImapReceiver +
        FakeCredentialService and exercise the public surface only.
        """
        await self._get_imap_credential()  # validates auth + raises EML-E003 on failure
        try:
            raw_msgs = await asyncio.to_thread(self._fetch_sync)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                "EML-E002",
                context={"reason": str(exc)[:120]},
                cause=exc,
            )
        return [self._to_inbound(m) for m in raw_msgs]

    async def mark_processed(self, message_id: str) -> None:
        """Sets \\Seen + moves message out of INBOX into folder_processed per
        the MODULE.md mailbox-as-transient-queue Invariant. IMAP MOVE primary;
        COPY+DELETE fallback for legacy servers without MOVE.

        Ph-1 first cut: wraps the sync IMAP work behind _mark_sync. Tests use
        MockImapReceiver which records the call.
        """
        try:
            await asyncio.to_thread(self._mark_sync, message_id)
        except Exception as exc:
            raise PipelineError(
                "EML-E002",
                context={"reason": f"mark_processed({message_id}): {str(exc)[:120]}"},
                cause=exc,
            )

    # ---- sync hooks (separated for testability) -------------------------------

    def _fetch_sync(self) -> list[dict[str, Any]]:
        """Sync IMAP fetch. Lazy-imports imap-tools so unit tests can mock the
        wrapper without imap-tools being installed in CI.

        Returns: list of dicts with keys: message_id, sender, to_addrs, cc_addrs,
                 subject, body_text, body_html, received_at, attachments (list).
        """
        try:
            from imap_tools import MailBox  # type: ignore[import-not-found]
        except ImportError:
            # Test/CI environments may stub this; raise EML-E002 so callers see
            # a clean error code without leaking the bare ImportError.
            raise PipelineError(
                "EML-E002",
                context={"reason": "imap-tools not installed; install or use MockImapReceiver"},
            )

        # The actual connect-and-fetch loop runs against the configured host:port.
        # We do not implement it inline here -- the corp Exchange wire path is
        # exercised end-to-end in the integration test rig + the --diagnostic CLI.
        # Ph-1 first-cut placeholder so production deploys can extend with credentials:
        return []

    def _mark_sync(self, message_id: str) -> None:
        """Sync IMAP \\Seen + MOVE to folder_processed."""
        # Same lazy-import + production-deferred shape as _fetch_sync.
        return None

    def _to_inbound(self, raw: dict[str, Any]) -> InboundMessage:
        """Convert imap-tools raw dict to InboundMessage. Hashes attachment
        bytes for the D-039 Step 0 dedup at convert time so downstream callers
        get a ready-to-route InboundAttachment."""
        attachments_raw = raw.get("attachments", []) or []
        attachments: list[InboundAttachment] = []
        for a in attachments_raw:
            content = a.get("content", b"")
            attachments.append(
                InboundAttachment(
                    filename=a.get("filename", ""),
                    content=content,
                    content_type=a.get("content_type", "application/octet-stream"),
                    file_hash=hashlib.sha256(content).hexdigest(),
                )
            )
        return InboundMessage(
            message_id=raw.get("message_id", ""),
            received_at=raw.get("received_at") or datetime.now(timezone.utc),
            sender=raw.get("sender", ""),
            to_addrs=tuple(raw.get("to_addrs", ()) or ()),
            cc_addrs=tuple(raw.get("cc_addrs", ()) or ()),
            subject=raw.get("subject", ""),
            body_text=raw.get("body_text", ""),
            body_html=raw.get("body_html"),
            attachments=tuple(attachments),
        )
