"""UNP-1 (2026-07-29) — owner_reply unparseable auto-reply helper.

Ph-1 test surface: owner copy-pasted a table from another email into
their reply; Outlook flattened it to <div>+inline-style, so <table>
parser returned unparseable. Owner had no idea their reply was dropped.
Fix: HILDA auto-replies to the sender with format instructions.

Tests exercise _maybe_send_unparseable_auto_reply directly (async
helper) with a fake InboundMessage + MagicMock deps -- simpler than
driving the full apply_owner_reply_task through the unparseable path
just to reach the same helper.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.src.workflow_engine.tasks.owner_reply import (
    _maybe_send_unparseable_auto_reply,
)


def _mk_msg(sender="owner@corp.example", message_id="mid-abc-123",
             subject="RE: [HILDA] MMK / SM-S671U1 / DRR -- Status request -- BATCH-abc"):
    return SimpleNamespace(sender=sender, message_id=message_id, subject=subject)


def _mk_deps(*, has_email_sender=True, prior_notified=False):
    deps = MagicMock()
    if not has_email_sender:
        deps.email_sender = None
    else:
        sends = []
        async def _send(*, to, cc, subject, body, attachments):
            sends.append({"to": list(to), "subject": subject, "body": body})
            return "<msg-id@hilda.local>"
        deps.email_sender.send = _send
        deps._sends = sends
    def _query(action_type=None, details_contains=None):
        if prior_notified and action_type == "owner_reply_unparseable_notified":
            return [{"marker": details_contains}]
        return []
    deps.audit.query_communications = MagicMock(side_effect=_query)
    return deps


class TestUnparseableAutoReply:

    @pytest.mark.asyncio
    async def test_sends_reply_when_sender_and_email_sender_present(self):
        deps = _mk_deps()
        msg = _mk_msg()
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=msg, batch_id="BATCH-abc",
            correlation_id="corr-1",
        )
        assert len(deps._sends) == 1
        sent = deps._sends[0]
        assert sent["to"] == ["owner@corp.example"]
        assert "BATCH-abc" in sent["subject"]
        assert "please re-reply from original" in sent["subject"].lower()
        assert "BATCH-abc" in sent["body"]
        # Audit written for idempotency on future retries
        assert deps.audit.write_communication_log.called
        audit_kwargs = deps.audit.write_communication_log.call_args.kwargs
        assert audit_kwargs["action_type"] == "owner_reply_unparseable_notified"
        assert audit_kwargs["details"]["message_id"] == "mid-abc-123"
        assert audit_kwargs["details"]["sender"] == "owner@corp.example"

    @pytest.mark.asyncio
    async def test_skip_when_no_sender(self):
        deps = _mk_deps()
        msg = _mk_msg(sender="")
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=msg, batch_id="BATCH-abc",
            correlation_id="corr-1",
        )
        assert deps._sends == []
        assert not deps.audit.write_communication_log.called

    @pytest.mark.asyncio
    async def test_skip_when_no_email_sender(self):
        deps = _mk_deps(has_email_sender=False)
        msg = _mk_msg()
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=msg, batch_id="BATCH-abc",
            correlation_id="corr-1",
        )
        # deps._sends attribute not set (email_sender None) -- just check no audit
        assert not deps.audit.write_communication_log.called

    @pytest.mark.asyncio
    async def test_skip_when_already_notified_for_message_id(self):
        # Simulate Celery retry: same message_id previously auto-replied.
        deps = _mk_deps(prior_notified=True)
        msg = _mk_msg()
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=msg, batch_id="BATCH-abc",
            correlation_id="corr-1",
        )
        assert deps._sends == []
        # No new audit -- idempotency probe found prior notification
        assert not deps.audit.write_communication_log.called

    @pytest.mark.asyncio
    async def test_send_failure_swallowed_no_crash(self):
        deps = _mk_deps()
        async def _boom(*a, **k):
            raise RuntimeError("SMTP unreachable")
        deps.email_sender.send = _boom
        msg = _mk_msg()
        # Must not raise
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=msg, batch_id="BATCH-abc",
            correlation_id="corr-1",
        )
        # No audit written when send failed (would falsely mark idempotent)
        assert not deps.audit.write_communication_log.called

    @pytest.mark.asyncio
    async def test_subject_special_chars_escaped(self):
        deps = _mk_deps()
        msg = _mk_msg(subject="RE: <b>bad</b> subject with html")
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=msg, batch_id="BATCH-abc",
            correlation_id="corr-1",
        )
        body = deps._sends[0]["body"]
        assert "&lt;b&gt;bad&lt;/b&gt;" in body
        assert "<b>bad</b>" not in body
