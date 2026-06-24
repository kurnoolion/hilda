"""email_service -- inbound email parsing + outbound composition + FR-52 router
+ FR-85 classifier + FR-86 storage matrix + sp_alert_parser.

See core/src/email_service/MODULE.md.
"""
from core.src.email_service.config import (
    EmailServiceConfig,
    Fr52Config,
    ImapConfig,
    SmtpConfig,
)
from core.src.email_service.inbound import (
    Fr52AttachmentRouter,
    ImapReceiver,
    classify,
    parse_freetext_with_attachments,
    parse_structured_block,
    parse_subject,
    resolve_tg_from_email,
)
from core.src.email_service.outbound import (
    SmtpSender,
    compose_escalation_for_messenger,
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
    "EmailReceiver",
    "EmailSender",
    "EmailServiceConfig",
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
    "classify",
    "compose_escalation_for_messenger",
    "compose_outreach",
    "compose_reminder",
    "extract_routing_key",
    "get_template_env",
    "parse_freetext_with_attachments",
    "parse_structured_block",
    "parse_subject",
    "resolve_tg_from_email",
]
