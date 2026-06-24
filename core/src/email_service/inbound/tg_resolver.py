"""Email-channel-only TG resolver per [D-060] + [D-105] + [D-106].

Resolves `inferred_tg_name` for inbound EMAIL-channel attachments only.
Other channels' tg_resolvers live in their owning modules:
- NSD ingress     -> NSD polling module (reads DeliveryItemBase.ingress_nsd)
- corp PLM        -> issue_tracker (reverse-looks up DeliveryItemBase.plm_id)
- SP UI direct    -> no resolution needed (TG explicit on the targeted DI)

Lookup order per [D-105] 4-field owner identity (against denormalized
DeliveryItemBase rows per [D-106] -- TGGroupBase Pydantic model dropped):
  (1) email_group_alias on To/CC list preferred when set
  (2) sender reverse-lookup against owner_corp_usa_email (preferred-for-outreach)
  (3) then owner_corp_email (fallback + PLM grouping match)
  (4) then CC list
"""
from __future__ import annotations

from core.src.email_service.protocol import InboundMessage

__all__ = ["resolve_tg_from_email"]


def resolve_tg_from_email(
    msg: InboundMessage,
    candidate_di_rows: list[dict],
) -> str | None:
    """Resolve TG name from an inbound EMAIL-channel message.

    Returns None when no match found. Caller surfaces None as
    DocumentIndexRow.inferred_tg_name = None and uses "_unknown_tg"
    placeholder in NSDPath.internal_default_workitem segment.
    """
    to_cc_lower = {a.strip().lower() for a in (msg.to_addrs + msg.cc_addrs) if a}
    sender_lower = (msg.sender or "").strip().lower()

    # (1) email_group_alias on To/CC -- TG-level alias
    for row in candidate_di_rows:
        alias = (row.get("tg_email_group_alias") or row.get("email_group_alias") or "").strip().lower()
        if alias and alias in to_cc_lower:
            tg = row.get("tg_name")
            if tg:
                return tg

    # (2) sender == owner_corp_usa_email
    for row in candidate_di_rows:
        usa = (row.get("owner_corp_usa_email") or "").strip().lower()
        if usa and usa == sender_lower:
            tg = row.get("tg_name")
            if tg:
                return tg

    # (3) sender == owner_corp_email
    for row in candidate_di_rows:
        corp = (row.get("owner_corp_email") or "").strip().lower()
        if corp and corp == sender_lower:
            tg = row.get("tg_name")
            if tg:
                return tg

    # (4) sender in CC list of any row (rare; conservative last fallback)
    for row in candidate_di_rows:
        cc_list = row.get("email_cc_list") or []
        # email_cc_list per template_schema is list[dict] of {email, name}; fall back to plain string
        cc_emails: set[str] = set()
        for entry in cc_list:
            if isinstance(entry, dict):
                e = entry.get("email")
                if e:
                    cc_emails.add(str(e).strip().lower())
            elif isinstance(entry, str):
                cc_emails.add(entry.strip().lower())
        if sender_lower in cc_emails:
            tg = row.get("tg_name")
            if tg:
                return tg

    return None
