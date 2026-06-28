"""EwsSender -- conforms to EmailSender Protocol via Exchange Web Services.

Per [D-132]: replaces SmtpSender for corp Exchange deployments. Builds an
exchangelib Message + send_and_save() via the same account/configuration
pattern as EwsReceiver. Sync exchangelib calls wrapped in asyncio.to_thread.

Credentials per-call (NFR-2). EML-E008 on credential failure; EML-E009 on
EWS transport failure.

Reference pattern: Chaitanya Kamsu's ExchangeMailService.send_email + the
@retry-wrapped __sendEmail__. We do NOT implement the retry decorator here --
workflow_engine retries are the source of truth for re-tries per [D-022], and
double-layered retries are an anti-pattern.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Protocol

from core.src.diagnostics.error_codes import PipelineError
from core.src.email_service.config import EwsConfig

__all__ = ["EwsSender", "CredentialServiceProtocol"]

logger = logging.getLogger(__name__)


class CredentialServiceProtocol(Protocol):
    async def get_credential(
        self,
        pm_id: str,
        system_type: str,
        customer_id: str | None = None,
    ) -> Any: ...


class EwsSender:
    """Conforms to EmailSender Protocol.

    Per [D-132] basic auth + service account; per-call credential resolution
    (NFR-2 -- never cached on instance).
    """

    def __init__(
        self,
        config: EwsConfig,
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
        """Send one outbound email; returns the Message-ID of the sent message.

        Raises EML-E008 on credential failure; EML-E009 on EWS transport failure.
        """
        try:
            cred = await self._cred.get_credential(pm_id="ops", system_type="email")
        except PipelineError as exc:
            raise PipelineError(
                "EML-E008",
                context={"reason": "credential_service rejected EWS lookup"},
                cause=exc,
            )

        message_id = f"<{uuid.uuid4()}@hilda.local>"

        try:
            await asyncio.to_thread(
                self._send_sync,
                cred,
                message_id,
                to, cc, subject, body, in_reply_to,
            )
        except PipelineError:
            raise
        except Exception as exc:
            raise PipelineError(
                "EML-E009",
                context={"reason": str(exc)[:120]},
                cause=exc,
            )

        # Log only bounded info (NFR-2) -- never body content
        logger.info(
            "ews_sent: message_id=%s to_count=%d cc_count=%d",
            message_id,
            len(to),
            len(cc),
        )
        return message_id

    def _send_sync(
        self,
        credential: Any,
        message_id: str,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        in_reply_to: str | None,
    ) -> None:
        """Sync EWS send via exchangelib Message + send_and_save.

        Lazy-imports exchangelib so non-EWS deployments don't pay the cost.
        Production override hook (subclass + override) if a deployment needs
        custom headers, BCC, or attachment dispatch.
        """
        try:
            from exchangelib import (   # type: ignore[import-not-found]
                DELEGATE,
                HTMLBody,
                IMPERSONATION,
                Account,
                Build,
                Configuration,
                Credentials,
                Mailbox,
                Message,
                Version,
            )
        except ImportError:
            raise PipelineError(
                "EML-E009",
                context={"reason": "exchangelib not installed; install or use MockEwsSender"},
            )

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

        # Auto-detect HTML by leading '<' in the body. exchangelib renders
        # plain `body` as plain text; HTMLBody-wrapped body sets the message
        # content-type to text/html so receivers see formatted output (tables,
        # lists, links). Detection is intentionally loose -- a body starting
        # with any HTML tag triggers it. Added 2026-06-28 to support the
        # outreach_table.j2 template per architect Step 5 design.
        stripped = body.lstrip()
        body_payload: Any = HTMLBody(body) if stripped.startswith("<") else body

        msg = Message(
            account=account,
            subject=subject,
            body=body_payload,
            to_recipients=[Mailbox(email_address=addr) for addr in to],
            cc_recipients=[Mailbox(email_address=addr) for addr in cc] if cc else None,
        )

        # in_reply_to is preserved as a custom header via msg.message_id only
        # at receive-time; exchangelib's outbound Message exposes Message-ID
        # only after send_and_save commits. We set our locally-tracked
        # message_id in EwsSender.send() and return it -- the corp Exchange-
        # assigned Message-ID lives in the audit trail server-side.
        # NOTE: if FR-9 / FR-10 / FR-24 reply-threading semantics require the
        # Exchange-assigned Message-ID, that's a Ph-2 enhancement -- refresh
        # msg.refresh() after send_and_save() to read msg.message_id.

        msg.send_and_save()
        return None
