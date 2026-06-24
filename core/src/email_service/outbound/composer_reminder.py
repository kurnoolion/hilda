"""FR-10 scheduled reminder composer.

Reuses BATCH-id from original outreach for thread continuity (per MODULE.md +
FR-24). Sets in_reply_to on the SMTP message for proper threading.
"""
from __future__ import annotations

from typing import Any

from jinja2 import Environment

__all__ = ["compose_reminder"]


def compose_reminder(
    item: dict[str, Any],
    batch_id: str,
    original_outreach_msg_id: str,
    reminder_count: int,
    template_env: Environment,
) -> tuple[str, str]:
    """Compose subject + body for a reminder email per FR-10.

    Arguments:
        item                       -- DeliveryItem dict
        batch_id                   -- BATCH-<id> from original outreach (thread continuity)
        original_outreach_msg_id   -- Message-ID of original outreach (for In-Reply-To)
        reminder_count             -- 1, 2, 3, ... (informational; logged)
        template_env               -- Jinja2 Environment

    Returns: (subject, body). Caller passes original_outreach_msg_id as
    in_reply_to to SmtpSender.send() for thread continuity.
    """
    template = template_env.get_template("reminder.j2")

    subject = f"[HILDA] Reminder ({reminder_count}) -- {batch_id}"

    body = template.render(
        batch_id=batch_id,
        item=item,
        reminder_count=reminder_count,
        original_msg_id=original_outreach_msg_id,
    )

    return subject, body
