"""EwsReceiver -- conforms to EmailReceiver Protocol via Exchange Web Services.

Per [D-132]: corp Exchange deployments (no IMAP/SMTP enabled) reach mail via
EWS. exchangelib provides the SOAP wire path; we wrap the sync API in
asyncio.to_thread per the [D-008] pattern (same shape as ImapReceiver +
SpClient).

Authentication is basic-over-TLS with a service account; credentials flow
per-call through credential_service and are never cached on the instance
(NFR-2). The exchangelib import is lazy so deployments running in IMAP/SMTP
mode don't pay the dependency cost.

Per architect Q1 (mode discriminator) + Q2 (exchangelib) + Q3 (polling) + Q4
(basic auth + service account) lock 2026-06-25. Streaming-notification primary
deferred Ph-1 next pass per Q3 polling lock.

Reference pattern: Chaitanya Kamsu's ExchangeMailService (216 lines) validated
against current corp Exchange. We mirror connect + filter + attachment-extract;
attachment write-to-disk is NOT performed here -- attachments stay in-memory as
bytes on InboundAttachment.content per the existing email_service contract
(disk landing is FR-86 storage matrix territory, not inbound transport).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol

from core.src.diagnostics.error_codes import PipelineError
from core.src.email_service.config import EwsConfig
from core.src.email_service.protocol import (
    InboundAttachment,
    InboundMessage,
)

__all__ = ["EwsReceiver", "CredentialServiceProtocol"]

logger = logging.getLogger(__name__)


class CredentialServiceProtocol(Protocol):
    """Minimal credential_service surface required by EwsReceiver."""

    async def get_credential(
        self,
        pm_id: str,
        system_type: str,
        customer_id: str | None = None,
    ) -> Any: ...


class EwsReceiver:
    """Conforms to EmailReceiver Protocol. EWS polling per FR-23 deadline-tiered.

    Per [D-107] SystemType.EMAIL stays SHARED -- pm_id='ops' credential.
    Per [D-132] basic auth + service account; OAuth deferred Ph-2.

    Streaming notifications via exchangelib.StreamingNotifications would give
    lower latency but adds complexity (long-lived TCP + reconnection retry).
    Ph-1 sticks to polling per architect Q3 lock.
    """

    def __init__(
        self,
        config: EwsConfig,
        credential_service: CredentialServiceProtocol,
    ) -> None:
        self._config = config
        self._cred = credential_service
        self._last_fetched_at: datetime | None = None

    async def _get_ews_credential(self) -> Any:
        """Per-call credential retrieval; never cached on instance.

        Re-raises CRD-E001 as EML-E008 (EWS auth) preserving the original
        cause for log chain analysis.
        """
        try:
            return await self._cred.get_credential(
                pm_id="ops",
                system_type="email",   # SystemType.EMAIL.value per [D-107]
            )
        except PipelineError as exc:
            raise PipelineError(
                "EML-E008",
                context={"reason": "credential_service rejected EWS lookup"},
                cause=exc,
            )

    async def fetch_new(self) -> AsyncIterator[InboundMessage]:
        """Long-running poll loop. Yields one InboundMessage per fetched item.

        Per Ph-1 polling shape: cadence = config.poll_interval_s. EWS streaming
        notifications deferred Ph-1 next pass.
        """
        while True:
            try:
                msgs = await self.fetch_once()
            except PipelineError:
                raise
            for m in msgs:
                yield m
            await asyncio.sleep(self._config.poll_interval_s)

    async def fetch_once(self) -> list[InboundMessage]:
        """Fetch all unprocessed messages in one batch; for --once test mode + as
        the poll-fallback inner step.

        Sync exchangelib work wrapped via asyncio.to_thread (same pattern as
        ImapReceiver + SpClient). EML-E008 on credential failure; EML-E009 on
        EWS transport failure.
        """
        cred = await self._get_ews_credential()
        try:
            raw_msgs = await asyncio.to_thread(self._fetch_sync, cred)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                "EML-E009",
                context={"reason": str(exc)[:120]},
                cause=exc,
            )
        return [self._to_inbound(m) for m in raw_msgs]

    async def mark_processed(self, message_id: str) -> None:
        """MOVE the message into config.folder_processed.

        EWS supports server-side MOVE natively (no COPY+DELETE fallback needed
        as with legacy IMAP servers). EML-E009 on transport failure.
        """
        cred = await self._get_ews_credential()
        try:
            await asyncio.to_thread(self._mark_sync, cred, message_id)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                "EML-E009",
                context={"reason": f"mark_processed({message_id}): {str(exc)[:120]}"},
                cause=exc,
            )

    # ---- sync hooks (separated for testability) -------------------------------

    def _fetch_sync(self, credential: Any) -> list[dict[str, Any]]:
        """Sync exchangelib fetch. Lazy-imports exchangelib so unit tests can
        stub the wrapper without exchangelib installed in CI.

        Returns: list of dicts in the same shape ImapReceiver._fetch_sync
        produces, so _to_inbound is wire-format-agnostic. Keys: message_id,
        sender, to_addrs, cc_addrs, subject, body_text, body_html, received_at,
        attachments (list of {filename, content, content_type}).

        Per Chaitanya's sample, the connect-and-filter loop is:
            credentials = Credentials(username, password)
            config = Configuration(credentials=..., service_endpoint=...,
                                   auth_type='basic', version=Version(Build(15,2)))
            config.timeout = 120
            account = Account(primary_smtp_address, config=config,
                              credentials=credentials, autodiscover=False,
                              access_type=DELEGATE)
            messages = account.inbox.filter(Q(datetime_received__gt=since))[:N]

        Production deployments override _fetch_sync directly OR extend it; the
        default implementation here builds the connection from config + creds
        and emits the raw dict list. Tests use MockEwsReceiver to bypass
        exchangelib entirely.
        """
        try:
            from exchangelib import (   # type: ignore[import-not-found]
                DELEGATE,
                IMPERSONATION,
                Account,
                Build,
                Configuration,
                Credentials,
                EWSDateTime,
                EWSTimeZone,
                Q,
                Version,
                FileAttachment,
            )
        except ImportError:
            raise PipelineError(
                "EML-E009",
                context={"reason": "exchangelib not installed; install or use MockEwsReceiver"},
            )

        # Build connection per Chaitanya pattern
        creds = Credentials(
            username=getattr(credential, "username", None) or "",
            password=getattr(credential, "password", None) or "",
        )
        version = None
        if (self._config.exchange_build_major is not None
                and self._config.exchange_build_minor is not None):
            version = Version(build=Build(
                self._config.exchange_build_major,
                self._config.exchange_build_minor,
            ))
        configuration = Configuration(
            credentials=creds,
            service_endpoint=self._config.service_endpoint,
            auth_type=self._config.auth_type,
            version=version,
        )
        configuration.timeout = self._config.timeout_s
        access_type = DELEGATE if self._config.access_type == "DELEGATE" else IMPERSONATION
        account = Account(
            self._config.primary_smtp_address,
            config=configuration,
            credentials=creds,
            autodiscover=False,
            access_type=access_type,
        )

        # Pick folder
        folder = account.inbox
        if self._config.inbox_subfolder:
            folder = folder / self._config.inbox_subfolder

        # Filter to messages received after the last successful fetch (or all
        # if first run). Mark advancement only on successful return.
        since = self._last_fetched_at
        if since is None:
            # First run -- start from "now minus poll window" to avoid massive
            # initial pull. Ph-1 conservative: just fetch the inbox top-N.
            tz = EWSTimeZone.localzone()
            now = datetime.now(timezone.utc)
            since = now
        else:
            tz = EWSTimeZone.localzone()

        if since:
            ews_since = EWSDateTime(
                since.year, since.month, since.day,
                since.hour, since.minute, since.second,
                tzinfo=tz,
            )
            qs = folder.filter(Q(datetime_received__gt=ews_since))
        else:
            qs = folder.all()
        qs = qs.order_by("-datetime_received")[:self._config.fetch_limit]

        out: list[dict[str, Any]] = []
        for msg in qs:
            attachments: list[dict[str, Any]] = []
            for att in (msg.attachments or []):
                if isinstance(att, FileAttachment):
                    attachments.append({
                        "filename": att.name or "",
                        "content": att.content or b"",
                        "content_type": att.content_type or "application/octet-stream",
                    })
            out.append({
                "message_id": str(msg.message_id or msg.item_id or ""),
                "sender": str(getattr(msg.sender, "email_address", "") or ""),
                "to_addrs": tuple(
                    str(r.email_address) for r in (msg.to_recipients or [])
                    if getattr(r, "email_address", None)
                ),
                "cc_addrs": tuple(
                    str(r.email_address) for r in (msg.cc_recipients or [])
                    if getattr(r, "email_address", None)
                ),
                "subject": msg.subject or "",
                "body_text": msg.text_body or "",
                "body_html": (str(msg.body) if msg.body else None) if msg.body_type == "HTML" else None,
                "received_at": msg.datetime_received,
                "attachments": attachments,
            })

        # Advance high-water mark only on success
        self._last_fetched_at = datetime.now(timezone.utc)
        return out

    def _mark_sync(self, credential: Any, message_id: str) -> None:
        """Sync EWS MOVE to folder_processed.

        Production override hook -- the default implementation re-acquires the
        account + finds the message by message_id + invokes msg.move(target).
        Tests use MockEwsReceiver which records the call without touching
        exchangelib.
        """
        try:
            from exchangelib import (   # type: ignore[import-not-found]
                DELEGATE,
                IMPERSONATION,
                Account,
                Configuration,
                Credentials,
                Q,
            )
        except ImportError:
            raise PipelineError(
                "EML-E009",
                context={"reason": "exchangelib not installed; install or use MockEwsReceiver"},
            )

        creds = Credentials(
            username=getattr(credential, "username", None) or "",
            password=getattr(credential, "password", None) or "",
        )
        configuration = Configuration(
            credentials=creds,
            service_endpoint=self._config.service_endpoint,
            auth_type=self._config.auth_type,
        )
        configuration.timeout = self._config.timeout_s
        access_type = DELEGATE if self._config.access_type == "DELEGATE" else IMPERSONATION
        account = Account(
            self._config.primary_smtp_address,
            config=configuration,
            credentials=creds,
            autodiscover=False,
            access_type=access_type,
        )

        target = account.inbox / self._config.folder_processed
        matches = account.inbox.filter(Q(message_id=message_id))[:1]
        for msg in matches:
            msg.move(target)
            break
        return None

    def _to_inbound(self, raw: dict[str, Any]) -> InboundMessage:
        """Convert exchangelib raw dict to InboundMessage (same shape as
        ImapReceiver._to_inbound -- the dict shape is wire-format-agnostic
        on purpose so the downstream pipeline is identical)."""
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
        received = raw.get("received_at")
        if isinstance(received, datetime):
            received_at = received
        else:
            received_at = datetime.now(timezone.utc)
        return InboundMessage(
            message_id=raw.get("message_id", ""),
            received_at=received_at,
            sender=raw.get("sender", ""),
            to_addrs=tuple(raw.get("to_addrs", ()) or ()),
            cc_addrs=tuple(raw.get("cc_addrs", ()) or ()),
            subject=raw.get("subject", ""),
            body_text=raw.get("body_text", ""),
            body_html=raw.get("body_html"),
            attachments=tuple(attachments),
        )
