"""Email-kind discriminator. Pure function -- no IO.

Order of checks per MODULE.md (first match wins):
  1. Subject matches D-047 SP-alert pattern `Alert_<List>_<Suffix> - <ItemTitle>`
     -> SP_ALERT
  2. Subject contains `BATCH-<id>` per FR-24 -> OWNER_REPLY
  3. Otherwise -> OTHER (logged in CommunicationLog with kind='other';
     surfaced on PM dashboard for triage per Invariant "exhaustive classification")
"""
from __future__ import annotations

import re

from core.src.email_service.protocol import EmailKind, InboundMessage

__all__ = ["classify", "SP_ALERT_SUBJECT_RE", "BATCH_ID_RE"]


# D-047 SP-alert subject -- `Alert_<List>_<Suffix> - <ItemTitle>`
SP_ALERT_SUBJECT_RE = re.compile(r"^Alert_([A-Za-z0-9_]+)_([A-Za-z0-9]+)\s*-\s*.+")

# FR-24 BATCH-id token -- per outbound composer convention `BATCH-<alphanumeric>`
BATCH_ID_RE = re.compile(r"BATCH-[a-zA-Z0-9]+")


def classify(msg: InboundMessage) -> EmailKind:
    """Return the EmailKind discriminator for one InboundMessage.

    Exhaustive per MODULE.md Invariant: every message receives a value;
    OTHER messages are not silently discarded -- caller must log to
    CommunicationLog (kind='other') and surface on PM dashboard.
    """
    subject = msg.subject or ""
    if SP_ALERT_SUBJECT_RE.match(subject):
        return EmailKind.SP_ALERT
    if BATCH_ID_RE.search(subject):
        return EmailKind.OWNER_REPLY
    return EmailKind.OTHER
