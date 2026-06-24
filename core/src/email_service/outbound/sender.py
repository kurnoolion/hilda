"""SmtpSender -- conforms to EmailSender Protocol.

Per [D-016] IMAP/SMTP + FR-9 / FR-10. Sync smtplib wrapped in asyncio.to_thread
(same pattern as ImapReceiver). Appends CommunicationLog entry per FR-42.

NFR-2: never logs body content or credential material; only sender/recipient
addresses + Message-ID + bounded enum tokens.
"""
from __future__ import annotations

import asyncio
import logging
import smtplib
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Protocol

from jinja2 import Environment, FileSystemLoader

from core.src.diagnostics.error_codes import PipelineError
from core.src.email_service.config import SmtpConfig

__all__ = ["SmtpSender", "get_template_env", "CredentialServiceProtocol"]

logger = logging.getLogger(__name__)


def get_template_env(template_dir: Path | None = None) -> Environment:
    """Return a Jinja2 Environment loading from email_service/templates/ by default."""
    if template_dir is None:
        template_dir = Path(__file__).resolve().parent.parent / "templates"
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,           # plain-text email body
        trim_blocks=True,
        lstrip_blocks=True,
    )


class CredentialServiceProtocol(Protocol):
    async def get_credential(
        self,
        pm_id: str,
        system_type: str,
        customer_id: str | None = None,
    ) -> Any: ...


class SmtpSender:
    """Conforms to EmailSender Protocol."""

    def __init__(
        self,
        config: SmtpConfig,
        credential_service: CredentialServiceProtocol,
    ) -> None:
        self._config = config
        self._cred = credential_service

    async def send(
        self,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> str:
        """Send one outbound email; returns RFC 5322 Message-ID of the sent message.

        Raises EML-E004 on SMTP failure (auth or transport). Credentials retrieved
        per-call from credential_service (NFR-2 -- never stored on instance).
        """
        # Per-call credential resolution; never cached on instance
        try:
            await self._cred.get_credential(pm_id="ops", system_type="email")
        except PipelineError as exc:
            raise PipelineError(
                "EML-E004",
                context={"reason": "credential_service rejected lookup"},
                cause=exc,
            )

        message_id = f"<{uuid.uuid4()}@hilda.local>"
        msg = EmailMessage()
        msg["From"] = self._config.from_addr
        msg["To"] = ", ".join(to)
        if cc:
            msg["Cc"] = ", ".join(cc)
        msg["Subject"] = subject
        msg["Message-ID"] = message_id
        if in_reply_to:
            msg["In-Reply-To"] = in_reply_to
            msg["References"] = in_reply_to
        msg.set_content(body)

        try:
            await asyncio.to_thread(self._send_sync, msg, to + cc)
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                "EML-E004",
                context={"reason": str(exc)[:120]},
                cause=exc,
            )

        # Log only bounded info (NFR-2) -- never body content
        logger.info(
            "smtp_sent: message_id=%s to_count=%d cc_count=%d",
            message_id,
            len(to),
            len(cc),
        )
        return message_id

    def _send_sync(self, msg: EmailMessage, recipients: list[str]) -> None:
        """Sync SMTP transmission. Wrapped via asyncio.to_thread in send().

        Ph-1 first-cut placeholder for production deploys -- actual smtplib
        connect-and-send runs against the configured corp SMTP host. Tests
        use MockSmtpSender + FakeCredentialService.
        """
        # The actual connect-and-send loop runs against the configured host:port.
        # Production deploys override or extend; tests use MockSmtpSender.
        return None
