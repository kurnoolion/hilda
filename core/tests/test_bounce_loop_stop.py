"""BOUNCE-LOOP-STOP-1 (2026-09-12) tests -- classifier must drop bounces
so the unparseable auto-reply never fires a reply chain against a mail
server. Live incident 2026-09-12: 12h of HILDA <-> corp-postmaster loop
after outreach hit an invalid recipient.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.src.email_service.inbound.classifier import (
    BOUNCE_SUBJECT_RE,
    classify,
    is_bounce_message,
)
from core.src.email_service.protocol import EmailKind, InboundMessage


def _msg(
    *,
    sender: str = "owner@corp.example",
    subject: str = "Re: [HILDA] MMK/SM-S671U1/P1 -- BATCH-abc123",
    body: str = "",
) -> InboundMessage:
    return InboundMessage(
        message_id="<id>",
        received_at=datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc),
        sender=sender,
        to_addrs=("hilda@corp.example",),
        cc_addrs=(),
        subject=subject,
        body_text=body,
        body_html=None,
        attachments=(),
    )


class TestBounceSubjectRegex:
    @pytest.mark.parametrize("subject", [
        "Undeliverable: [HILDA] BATCH-abc",
        "UNDELIVERABLE:  [HILDA] BATCH-abc",
        "Delivery Status Notification (Failure)",
        "Mail delivery failed: returning message to sender",
        "Mail Delivery Failure",
        "Failure Notice",
        "Returned mail: see transcript for details",
        "Undelivered Mail Returned to Sender",
        "Automatic reply: out of office",
        "Out of Office AutoReply -- back Monday",
    ])
    def test_bounce_prefix_matches(self, subject):
        assert BOUNCE_SUBJECT_RE.match(subject) is not None

    @pytest.mark.parametrize("subject", [
        "Re: [HILDA] BATCH-abc",
        "Milestones - Change in Milestone",
        "Please deliver the report by Friday",   # word 'deliver' in prose
    ])
    def test_normal_subjects_not_matched(self, subject):
        assert BOUNCE_SUBJECT_RE.match(subject) is None


class TestIsBounceMessage:
    def test_postmaster_sender_is_bounce(self):
        msg = _msg(sender="Postmaster <postmaster@corp.example>",
                   subject="Re: BATCH-abc123")  # subject looks legit
        assert is_bounce_message(msg) is True

    def test_mailer_daemon_sender_is_bounce(self):
        msg = _msg(sender="MAILER-DAEMON@corp.example",
                   subject="Re: BATCH-abc123")
        assert is_bounce_message(msg) is True

    def test_undeliverable_subject_is_bounce_even_if_sender_normal(self):
        msg = _msg(sender="owner@corp.example",
                   subject="Undeliverable: [HILDA] BATCH-abc123")
        assert is_bounce_message(msg) is True

    def test_normal_reply_is_not_bounce(self):
        msg = _msg(sender="owner@corp.example",
                   subject="Re: [HILDA] BATCH-abc123")
        assert is_bounce_message(msg) is False

    def test_empty_sender_and_subject_safe(self):
        # Should not raise on degenerate input.
        msg = _msg(sender="", subject="")
        assert is_bounce_message(msg) is False


class TestClassify:
    def test_bounce_routes_to_other_not_owner_reply(self):
        """The loop-closing bug: bounces echo the original subject with
        the BATCH-id token, so pre-BOUNCE-LOOP-STOP-1 they classified
        as OWNER_REPLY and fired unparseable-auto-reply back at the
        mail server. MUST land on OTHER now."""
        msg = _msg(
            sender="postmaster@corp.example",
            subject="Undeliverable: [HILDA] MMK/SM-S671U1/P1 -- BATCH-abc123",
        )
        assert classify(msg) == EmailKind.OTHER

    def test_legit_reply_still_routes_to_owner_reply(self):
        msg = _msg(
            sender="owner@corp.example",
            subject="Re: [HILDA] MMK/SM-S671U1/P1 -- BATCH-abc123",
        )
        assert classify(msg) == EmailKind.OWNER_REPLY

    def test_sp_alert_still_routes_to_sp_alert(self):
        msg = _msg(
            sender="sharepoint@corp.example",
            subject="Milestones - Change in item on Deliverable Tracker",
        )
        assert classify(msg) == EmailKind.SP_ALERT

    def test_generic_email_routes_to_other(self):
        msg = _msg(sender="someone@corp.example", subject="Hello")
        assert classify(msg) == EmailKind.OTHER

    def test_out_of_office_reply_is_other_even_with_batch_id_in_body(self):
        """OOO auto-replies quote the original subject (with BATCH-id)
        in the subject line too. Must not fire the unparseable path."""
        msg = _msg(
            sender="owner@corp.example",
            subject="Automatic reply: Re: [HILDA] BATCH-abc123",
        )
        assert classify(msg) == EmailKind.OTHER
