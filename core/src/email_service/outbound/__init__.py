"""email_service.outbound -- SMTP sender + composers + templates."""
from core.src.email_service.outbound.composer_escalation import (
    compose_escalation_for_messenger,
)
from core.src.email_service.outbound.composer_outreach import compose_outreach
from core.src.email_service.outbound.composer_reminder import compose_reminder
from core.src.email_service.outbound.sender import SmtpSender, get_template_env

__all__ = [
    "SmtpSender",
    "compose_escalation_for_messenger",
    "compose_outreach",
    "compose_reminder",
    "get_template_env",
]
