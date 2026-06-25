"""email_service -- inbound email parsing + outbound composition + FR-52 router
+ FR-85 classifier + FR-86 storage matrix + sp_alert_parser.

See core/src/email_service/MODULE.md.
"""
from core.src.email_service.config import (
    EmailMode,
    EmailServiceConfig,
    EwsConfig,
    Fr52Config,
    ImapConfig,
    SmtpConfig,
)
from core.src.email_service.inbound import (
    EwsReceiver,
    Fr52AttachmentRouter,
    ImapReceiver,
    classify,
    parse_freetext_with_attachments,
    parse_structured_block,
    parse_subject,
    resolve_tg_from_email,
)
from core.src.email_service.outbound import (
    EwsSender,
    SmtpSender,
    compose_outreach,
    compose_reminder,
    get_template_env,
)
from core.src.email_service.protocol import (
    AttachmentItemMatch,
    AttachmentRouter,
    ClassificationResolution,
    EmailKind,
    EmailReceiver,
    EmailSender,
    InboundAttachment,
    InboundMessage,
    ParsedSubject,
    PerItemReplyUpdate,
    RoutedAttachment,
    StructuredReplyBlock,
)
from core.src.email_service.sp_alert_parser import (
    ALERT_SCOPE_LISTS,
    AlertRoutingKey,
    ParsedSpAlert,
    SpAlertParser,
    extract_routing_key,
)

__all__ = [
    "ALERT_SCOPE_LISTS",
    "AlertRoutingKey",
    "AttachmentItemMatch",
    "AttachmentRouter",
    "ClassificationResolution",
    "EmailKind",
    "EmailMode",
    "EmailReceiver",
    "EmailSender",
    "EmailServiceConfig",
    "EwsConfig",
    "EwsReceiver",
    "EwsSender",
    "Fr52AttachmentRouter",
    "Fr52Config",
    "ImapConfig",
    "ImapReceiver",
    "InboundAttachment",
    "InboundMessage",
    "ParsedSpAlert",
    "ParsedSubject",
    "PerItemReplyUpdate",
    "RoutedAttachment",
    "SmtpConfig",
    "SmtpSender",
    "SpAlertParser",
    "StructuredReplyBlock",
    "build_receiver",
    "build_sender",
    "classify",
    "compose_outreach",
    "compose_reminder",
    "extract_routing_key",
    "get_template_env",
    "parse_freetext_with_attachments",
    "parse_structured_block",
    "parse_subject",
    "resolve_tg_from_email",
]


# ---------------------------------------------------------------------------
# Adapter factory per [D-132] mode discriminator
# ---------------------------------------------------------------------------

def build_receiver(
    cfg: EmailServiceConfig,
    credential_service: object,
) -> EmailReceiver:
    """Pick the inbound adapter based on cfg.mode per [D-132].

    "imap_smtp" -> ImapReceiver (default; non-corp + dev)
    "ews"       -> EwsReceiver  (corp Exchange deployments)
    "mock"      -> caller is responsible for wiring MockImapReceiver directly;
                   this factory raises ValueError to surface mis-wiring early
                   (mock harness should never reach build_receiver).
    """
    if cfg.mode == "ews":
        return EwsReceiver(cfg.ews, credential_service)        # type: ignore[arg-type]
    if cfg.mode == "imap_smtp":
        return ImapReceiver(cfg.imap, credential_service)      # type: ignore[arg-type]
    raise ValueError(
        f"build_receiver: cfg.mode={cfg.mode!r} not implemented in factory; "
        "wire MockImapReceiver directly for test harness"
    )


def build_sender(
    cfg: EmailServiceConfig,
    credential_service: object,
) -> EmailSender:
    """Pick the outbound adapter based on cfg.mode per [D-132].

    Same dispatch as build_receiver; see its docstring.
    """
    if cfg.mode == "ews":
        return EwsSender(cfg.ews, credential_service)          # type: ignore[arg-type]
    if cfg.mode == "imap_smtp":
        return SmtpSender(cfg.smtp, credential_service)        # type: ignore[arg-type]
    raise ValueError(
        f"build_sender: cfg.mode={cfg.mode!r} not implemented in factory; "
        "wire MockSmtpSender directly for test harness"
    )
