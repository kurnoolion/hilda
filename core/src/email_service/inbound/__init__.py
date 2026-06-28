"""email_service.inbound -- IMAP receiver + classifier + parsers + FR-52 router."""
from core.src.email_service.inbound.attachment_router import Fr52AttachmentRouter
from core.src.email_service.inbound.body_parser_freetext import (
    parse_freetext_with_attachments,
)
from core.src.email_service.inbound.body_parser_structured import parse_structured_block
from core.src.email_service.inbound.body_parser_table import parse_table_block
from core.src.email_service.inbound.classifier import classify
from core.src.email_service.inbound.ews_receiver import EwsReceiver
from core.src.email_service.inbound.receiver import ImapReceiver
from core.src.email_service.inbound.subject_parser import parse_subject
from core.src.email_service.inbound.tg_resolver import resolve_tg_from_email

__all__ = [
    "EwsReceiver",
    "Fr52AttachmentRouter",
    "ImapReceiver",
    "classify",
    "parse_freetext_with_attachments",
    "parse_structured_block",
    "parse_subject",
    "parse_table_block",
    "resolve_tg_from_email",
]
