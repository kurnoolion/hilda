"""email_service.outbound -- SMTP sender + composers + templates.

Per architect Q-M6 lock 2026-06-25 + 2026-06-26 cascade: composer_escalation.py
deleted; messenger module owns escalation composition (see messenger.composer).
"""
from core.src.email_service.outbound.composer_outreach import compose_outreach
from core.src.email_service.outbound.composer_reminder import compose_reminder
from core.src.email_service.outbound.sender import SmtpSender, get_template_env

__all__ = [
    "SmtpSender",
    "compose_outreach",
    "compose_reminder",
    "get_template_env",
]
