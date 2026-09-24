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


def _mk_expected_items(customer_id="MMK", device_id="SM-S671U1"):
    """UNP-TPM-1: the shape _lookup_batch_items returns, trimmed to the keys
    the auto-reply reads."""
    return [{
        "item_no": 5,
        "delivery_item_id": f"{customer_id}-{device_id}-DRR-5",
        "customer_id": customer_id,
        "device_id": device_id,
    }]


def _mk_deps(*, has_email_sender=True, prior_notified=False, tpm_email=None):
    deps = MagicMock()
    # UNP-TPM-1: sp_writer stands in for the Projects_<customer_id> read.
    # Returning [] (rather than leaving the MagicMock auto-attribute) makes
    # "no TPM configured" the explicit default, so a test that wants a TPM
    # has to say so.
    def _get_items(*a, **kw):
        if tpm_email is None:
            return []
        return [{"tpm_email": {"EMail": tpm_email, "Title": "The TPM"}}]
    deps.sp_writer.get_items = MagicMock(side_effect=_get_items)
    if not has_email_sender:
        deps.email_sender = None
    else:
        sends = []
        async def _send(*, to, cc, subject, body, attachments):
            sends.append({
                "to": list(to), "cc": list(cc), "subject": subject,
                "body": body, "attachments": attachments,
            })
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

    # -- UNP-ATTACH-1 (2026-09-24) --------------------------------------

    @pytest.mark.asyncio
    async def test_body_tells_owner_not_to_resend_attachments(self):
        """Owners were re-sending their whole document set on every
        re-reply, creating duplicate revisions HILDA then had to collapse.
        The copy must say, unambiguously, that attachments already landed
        and only the table needs sending again."""
        deps = _mk_deps()
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=_mk_msg(), batch_id="BATCH-abc",
            correlation_id="corr-1",
        )
        # The body is hand-wrapped HTML, so collapse whitespace before
        # matching -- otherwise a purely cosmetic re-wrap breaks the test.
        body = " ".join(deps._sends[0]["body"].lower().split())
        assert "do not send them again" in body
        assert "only the status table needs re-sending" in body
        assert "no attachments needed" in body

    # -- UNP-TPM-1 (2026-09-24) -----------------------------------------

    @pytest.mark.asyncio
    async def test_tpm_is_added_to_the_to_list(self):
        deps = _mk_deps(tpm_email="tpm@corp.example")
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=_mk_msg(), batch_id="BATCH-abc",
            correlation_id="corr-1", expected_items=_mk_expected_items(),
        )
        sent = deps._sends[0]
        assert sent["to"] == ["owner@corp.example", "tpm@corp.example"]
        assert sent["cc"] == []

    @pytest.mark.asyncio
    async def test_projects_is_queried_with_the_batch_scope(self):
        deps = _mk_deps(tpm_email="tpm@corp.example")
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=_mk_msg(), batch_id="BATCH-abc",
            correlation_id="corr-1",
            expected_items=_mk_expected_items("VZW", "SM-A186U"),
        )
        kw = deps.sp_writer.get_items.call_args.kwargs
        assert kw["entity"] == "projects"
        assert kw["canonical_filters"] == {"project_model": "SM-A186U"}

    @pytest.mark.asyncio
    async def test_owner_only_when_scope_is_unavailable(self):
        """No expected_items (or a row missing customer/device) -> no TPM
        lookup at all, and the owner still gets their notification."""
        deps = _mk_deps(tpm_email="tpm@corp.example")
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=_mk_msg(), batch_id="BATCH-abc",
            correlation_id="corr-1", expected_items=None,
        )
        assert deps._sends[0]["to"] == ["owner@corp.example"]
        assert not deps.sp_writer.get_items.called

    @pytest.mark.asyncio
    async def test_tpm_lookup_failure_still_notifies_the_owner(self):
        deps = _mk_deps(tpm_email="tpm@corp.example")
        deps.sp_writer.get_items = MagicMock(side_effect=RuntimeError("SP down"))
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=_mk_msg(), batch_id="BATCH-abc",
            correlation_id="corr-1", expected_items=_mk_expected_items(),
        )
        assert deps._sends[0]["to"] == ["owner@corp.example"]

    @pytest.mark.asyncio
    async def test_tpm_who_is_the_sender_is_not_duplicated(self):
        """A TPM replying on an owner's behalf shouldn't be addressed twice."""
        deps = _mk_deps(tpm_email="Owner@Corp.Example")
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=_mk_msg(sender="owner@corp.example"),
            batch_id="BATCH-abc", correlation_id="corr-1",
            expected_items=_mk_expected_items(),
        )
        assert deps._sends[0]["to"] == ["owner@corp.example"]

    @pytest.mark.asyncio
    async def test_pm_sentence_appears_only_when_the_pm_is_actually_on_it(self):
        """Regression guard on a factual claim. The pre-2026-09-24 copy said
        'your PM has been copied' unconditionally while sending cc=[] to the
        owner alone -- an owner could read that as 'someone else will handle
        it' and stop. The claim must track reality."""
        with_tpm = _mk_deps(tpm_email="tpm@corp.example")
        await _maybe_send_unparseable_auto_reply(
            deps=with_tpm, msg=_mk_msg(), batch_id="BATCH-abc",
            correlation_id="corr-1", expected_items=_mk_expected_items(),
        )
        body = " ".join(with_tpm._sends[0]["body"].lower().split())
        assert "your pm is on this email" in body

        without_tpm = _mk_deps(tpm_email=None)
        await _maybe_send_unparseable_auto_reply(
            deps=without_tpm, msg=_mk_msg(), batch_id="BATCH-abc",
            correlation_id="corr-1", expected_items=_mk_expected_items(),
        )
        sent = without_tpm._sends[0]
        body = " ".join(sent["body"].lower().split())
        assert sent["to"] == ["owner@corp.example"]
        assert "your pm is on this email" not in body
        assert "has been copied" not in body

    @pytest.mark.asyncio
    async def test_audit_records_whether_the_tpm_was_notified(self):
        deps = _mk_deps(tpm_email="tpm@corp.example")
        await _maybe_send_unparseable_auto_reply(
            deps=deps, msg=_mk_msg(), batch_id="BATCH-abc",
            correlation_id="corr-1", expected_items=_mk_expected_items(),
        )
        details = deps.audit.write_communication_log.call_args.kwargs["details"]
        assert details["tpm_recipient"] == "tpm@corp.example"
