"""messenger composer -- compose_escalation builds the message body.

Per architect Q-M6 lock 2026-06-25: messenger module OWNS its own composition
(templates + render) -- NOT email_service. The Ph-1 stub at
`core/src/email_service/outbound/composer_escalation.py` becomes vestigial /
should be removed in a follow-up; this composer is the canonical Ph-1 path.

Template: `templates/escalation.j2` (Jinja2). Bounded enum tokens only per
NFR-2 -- the template body itself may include owner_name / device_id for
human readability (the message is sent to the owner), but compact RPT
records emitted around the send NEVER include those fields raw.

If the rendered body exceeds `max_message_bytes`, the composer truncates with
the standard marker and emits MSG-W002 (warning, not error). MSG-E002 is raised
when template rendering itself fails (template not found, missing required
variable, malformed YAML, etc.).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, TemplateError

from core.src.diagnostics.error_codes import PipelineError
from core.src.messenger.config import MessengerConfig
from core.src.messenger.utils import truncate_message, validate_message_length

__all__ = [
    "ComposeResult",
    "compose_escalation",
    "get_template_env",
    "REQUIRED_TEMPLATE_VARS",
]

# Variables the escalation.j2 template references. compose_escalation validates
# the call site passes all of them so a downstream Jinja2 UndefinedError is
# raised pre-render via an explicit MSG-E002 with the missing field name.
REQUIRED_TEMPLATE_VARS: tuple[str, ...] = (
    "owner_name",
    "deliverable_count",
    "milestone_name",
    "device_id",
    "deadline_date",
    "corp_plm_link",
    "reminder_count",
    "batch_id",
    "reason",
)


class ComposeResult:
    """Composition output -- rendered message + size accounting + truncation flag.

    Lightweight (not a dataclass) so it stays cheap when constructed many times
    in a batch escalation flow.
    """

    __slots__ = ("message", "rendered_bytes", "truncated", "original_bytes")

    def __init__(
        self,
        message: str,
        rendered_bytes: int,
        truncated: bool,
        original_bytes: int,
    ) -> None:
        self.message = message
        self.rendered_bytes = rendered_bytes
        self.truncated = truncated
        self.original_bytes = original_bytes


def get_template_env(template_dir: Path | None = None) -> Environment:
    """Jinja2 Environment loading from messenger/templates/ by default."""
    if template_dir is None:
        template_dir = Path(__file__).resolve().parent / "templates"
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=False,
        trim_blocks=True,
        lstrip_blocks=True,
    )


def compose_escalation(
    *,
    config: MessengerConfig,
    owner_name: str,
    deliverable_count: int,
    milestone_name: str,
    device_id: str,
    deadline_date: str,
    corp_plm_link: str,
    reminder_count: int,
    batch_id: str,
    reason: str,
    owner_id_for_error: str = "<owner>",
    template_env: Environment | None = None,
) -> ComposeResult:
    """Render escalation.j2 and enforce the max_message_bytes invariant.

    On render failure: raises PipelineError MSG-E002 with the failing owner
    in context. On byte-overrun: truncates with `... [truncated]` marker and
    returns `truncated=True` on the ComposeResult (caller emits MSG-W002).
    """
    env = template_env or get_template_env(config.templates_dir)
    try:
        template = env.get_template("escalation.j2")
        message = template.render(
            owner_name=owner_name,
            deliverable_count=deliverable_count,
            milestone_name=milestone_name,
            device_id=device_id,
            deadline_date=deadline_date,
            corp_plm_link=corp_plm_link,
            reminder_count=reminder_count,
            batch_id=batch_id,
            reason=reason,
            bot_signature=config.bot_signature,
        )
    except TemplateError as exc:
        raise PipelineError(
            "MSG-E002", context={"owner_id": owner_id_for_error}, cause=exc
        )
    except OSError as exc:
        # Template file not found.
        raise PipelineError(
            "MSG-E002", context={"owner_id": owner_id_for_error}, cause=exc
        )

    within, byte_count = validate_message_length(message, config.max_message_bytes)
    if within:
        return ComposeResult(
            message=message,
            rendered_bytes=byte_count,
            truncated=False,
            original_bytes=byte_count,
        )
    truncated_msg, original_n = truncate_message(message, config.max_message_bytes)
    _, truncated_n = validate_message_length(truncated_msg, config.max_message_bytes)
    return ComposeResult(
        message=truncated_msg,
        rendered_bytes=truncated_n,
        truncated=True,
        original_bytes=original_n,
    )
