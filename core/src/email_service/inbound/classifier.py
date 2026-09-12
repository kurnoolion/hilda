"""Email-kind discriminator. Pure function -- no IO.

Order of checks per MODULE.md (first match wins):
  1. Bounce / DSN (Delivery Status Notification) heuristics -> OTHER
     (BOUNCE-LOOP-STOP-1, 2026-09-12). Must fire BEFORE the BATCH-id
     check because Outlook prepends "Undeliverable: " to the failed
     outreach's original subject and the BATCH-id token survives; the
     downstream OWNER_REPLY path then fires an unparseable-auto-reply
     addressed to `postmaster@corp` (or MAILER-DAEMON) which itself
     bounces, and 12 hours later HILDA is in an infinite loop with the
     mail server. Detected purely from subject + sender pattern; body
     content untouched (no false positives on a legit reply that
     happens to contain the word "delivery" in prose).
  2. Subject matches D-047 SP-alert pattern -- per architect screenshots
     2026-06-27 the real SP format is:
       * Milestones (GLOBAL list):  `Milestones - <Title>`
       * Deliverables (per-cust):   `Deliverables_<customer_id> - <Title>`
       * Projects   (per-customer): `Projects_<customer_id> - <Title>` (Ph-2)
     -> SP_ALERT
  3. Subject contains `BATCH-<id>` per FR-24 -> OWNER_REPLY
  4. Otherwise -> OTHER (logged in CommunicationLog with kind='other';
     surfaced on PM dashboard for triage per Invariant "exhaustive classification")
"""
from __future__ import annotations

import re

from core.src.email_service.protocol import EmailKind, InboundMessage

__all__ = [
    "classify",
    "is_bounce_message",
    "SP_ALERT_SUBJECT_RE",
    "BATCH_ID_RE",
    "BOUNCE_SUBJECT_RE",
    "BOUNCE_SENDER_LOCAL_PARTS",
]


# D-047 SP-alert subject -- real format per architect screenshots 2026-06-27.
# Milestones (global) has no customer suffix; Deliverables/Projects (per-customer)
# carry _<customer_id> suffix. NO "Alert_" prefix (was a wrong assumption pre-2026-06-27).
SP_ALERT_SUBJECT_RE = re.compile(
    r"^(?:Milestones|Projects|Deliverables)(?:_[A-Za-z0-9]+)?\s*-\s*.+"
)

# FR-24 BATCH-id token -- per outbound composer convention `BATCH-<alphanumeric>`
BATCH_ID_RE = re.compile(r"BATCH-[a-zA-Z0-9]+")

# BOUNCE-LOOP-STOP-1 (2026-09-12): common NDR / DSN subject prefixes across
# corp Exchange, Office 365, Postfix, Sendmail. Case-insensitive. Matches
# on prefix so the surviving original-subject tail (with our BATCH-id)
# doesn't rescue the classification into OWNER_REPLY.
BOUNCE_SUBJECT_RE = re.compile(
    r"^\s*(?:"
    r"undeliverable"                       # Outlook / Exchange
    r"|undelivered\s+mail\s+returned\s+to\s+sender"   # Postfix
    r"|delivery\s+status\s+notification"   # RFC 3464 DSN standard
    r"|mail\s+delivery\s+(?:failure|failed|status)"
    r"|failure\s+notice"                   # Sendmail
    r"|returned\s+mail"
    r"|automatic\s+reply"                  # cover the strictest OOO case
    r"|out\s+of\s+office"
    r")\b",
    re.IGNORECASE,
)

# System addresses that generate bounces / DSNs; never reply to these.
# Local-part comparison is case-insensitive.
BOUNCE_SENDER_LOCAL_PARTS: frozenset[str] = frozenset({
    "postmaster",
    "mailer-daemon",
    "mail-daemon",
    "no-reply",
    "noreply",
})


def _sender_local_part(sender: str) -> str:
    """Return the lowercased local part of an RFC 5322 sender field.

    Tolerates "Name <a@b>" (Outlook display shape), bare "a@b", and
    missing/empty inputs. Returns "" when no @ is present."""
    if not sender:
        return ""
    s = sender.strip()
    # strip "Name <addr>" wrapper if present
    if "<" in s and ">" in s:
        s = s[s.rfind("<") + 1 : s.rfind(">")]
    at = s.find("@")
    if at < 0:
        return ""
    return s[:at].strip().lower()


def is_bounce_message(msg: InboundMessage) -> bool:
    """BOUNCE-LOOP-STOP-1 (2026-09-12): return True when `msg` looks like
    a bounce / DSN / auto-reply. Two independent signals; ANY match wins.

    Signal 1: sender local part in BOUNCE_SENDER_LOCAL_PARTS
             (postmaster, mailer-daemon, no-reply, ...).
    Signal 2: subject starts with a known bounce prefix
             (Undeliverable, Delivery Status Notification, Failure Notice, ...).

    Called by `classify` before the BATCH-id check (bounces echo the
    original subject and would otherwise route to OWNER_REPLY -> parser
    fails -> unparseable auto-reply -> bounces again -> infinite loop).
    Also exposed for callers wanting a defensive last-second check
    before sending a per-message auto-reply.
    """
    if _sender_local_part(msg.sender or "") in BOUNCE_SENDER_LOCAL_PARTS:
        return True
    if BOUNCE_SUBJECT_RE.match(msg.subject or ""):
        return True
    return False


def classify(msg: InboundMessage) -> EmailKind:
    """Return the EmailKind discriminator for one InboundMessage.

    Exhaustive per MODULE.md Invariant: every message receives a value;
    OTHER messages are not silently discarded -- caller must log to
    CommunicationLog (kind='other') and surface on PM dashboard.
    """
    # Order matters: bounces echo original subject (with BATCH-id) and
    # would otherwise route to OWNER_REPLY, closing the loop.
    if is_bounce_message(msg):
        return EmailKind.OTHER
    subject = msg.subject or ""
    if SP_ALERT_SUBJECT_RE.match(subject):
        return EmailKind.SP_ALERT
    if BATCH_ID_RE.search(subject):
        return EmailKind.OWNER_REPLY
    return EmailKind.OTHER
