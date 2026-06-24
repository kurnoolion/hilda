"""FR-9 initial outreach composer.

Honors email_group_alias on DI: when set, one outreach email to alias with
per-owner BATCH blocks (TG-alias mode); else individual per-owner outreach.

Structured reply block included in body for FR-12 path (a) parsing. The
block format matches body_parser_structured.py's regex.
"""
from __future__ import annotations

import uuid
from typing import Any

from jinja2 import Environment

__all__ = ["compose_outreach", "generate_batch_id"]


def generate_batch_id() -> str:
    """Generate a BATCH-<id> token per FR-24 (used in subject + structured block)."""
    return f"BATCH-{uuid.uuid4().hex[:10]}"


def compose_outreach(
    item: dict[str, Any],
    owner_identity: dict[str, Any],
    batch_id: str,
    items_in_batch: list[dict[str, Any]],
    template_env: Environment,
) -> tuple[str, str]:
    """Compose subject + body for an initial outreach email per FR-9.

    Arguments:
        item            -- the primary DeliveryItem dict
        owner_identity  -- 4-field identity per [D-105]:
                           {owner_corp_usa_email, owner_corp_email,
                            owner_corp_id, owner_name}
        batch_id        -- BATCH-<id> token from generate_batch_id()
        items_in_batch  -- all items this outreach covers (1 for individual mode,
                           N for tg_alias mode)
        template_env    -- Jinja2 Environment loading from templates/

    Returns: (subject, body)
    """
    # tg_alias mode when alias is set AND items_in_batch has >1 item
    tg_alias = item.get("tg_email_group_alias") or item.get("email_group_alias")
    use_alias_template = bool(tg_alias) and len(items_in_batch) > 1

    template_name = (
        "outreach_tg_alias.j2" if use_alias_template else "outreach_individual.j2"
    )
    template = template_env.get_template(template_name)

    subject = f"[HILDA] Status request -- {batch_id}"

    body = template.render(
        batch_id=batch_id,
        item=item,
        owner=owner_identity,
        items=items_in_batch,
        tg_alias=tg_alias,
    )

    return subject, body
