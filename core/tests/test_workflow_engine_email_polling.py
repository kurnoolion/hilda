"""Tests for periodic ews_receiver polling task.

Tests invoke the inner async function directly (bypassing Celery dispatch)
so we don't need psycopg2 in the test env for the postgres result backend.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.src.workflow_engine import TaskDeps, override_task_deps


@pytest.fixture
def base_deps():
    return TaskDeps(
        storage=SimpleNamespace(get_delivery_item=lambda _id: None),
        sp_writer=SimpleNamespace(),
        audit=SimpleNamespace(),
    )


def _patches_for_poll(receiver_mock, classify_return=None, parse_return=None):
    """Common patch set: receiver + config + credentials + classifier + parser."""
    from core.src.email_service.protocol import EmailKind

    patches = [
        patch("core.src.email_service.build_receiver", return_value=receiver_mock),
        patch(
            "core.src.email_service.config.EmailServiceConfig.from_sources",
            return_value=MagicMock(),
        ),
    ]
    cred_patch = patch("core.src.credential_service.service.SopsCredentialService")
    patches.append(cred_patch)

    if classify_return is not None:
        patches.append(
            patch(
                "core.src.email_service.inbound.classifier.classify",
                return_value=classify_return,
            )
        )
    if parse_return is not None:
        parser_patch = patch("core.src.email_service.sp_alert_parser.SpAlertParser")
        patches.append(parser_patch)

    return patches


async def _run_poll():
    """Invoke the inner async function (skips Celery .apply machinery)."""
    from core.src.workflow_engine.tasks.email_polling import _async_poll_and_dispatch
    return await _async_poll_and_dispatch()


async def test_poll_returns_skipped_when_dispatcher_not_wired(base_deps):
    """deps.dispatcher is None -> skipped_no_dispatcher=True; no dispatch."""
    fake_receiver = MagicMock()
    fake_receiver.fetch_once = AsyncMock(return_value=[])

    with override_task_deps(base_deps), \
         patch("core.src.email_service.build_receiver", return_value=fake_receiver), \
         patch(
             "core.src.email_service.config.EmailServiceConfig.from_sources",
             return_value=MagicMock(),
         ), \
         patch("core.src.credential_service.service.SopsCredentialService") as mock_cred_cls:
        mock_cred = MagicMock()
        mock_cred.load = AsyncMock()
        mock_cred_cls.return_value = mock_cred
        result = await _run_poll()

    assert result["skipped_no_dispatcher"] is True
    assert result["messages_fetched"] == 0
    assert result["dispatched"] == 0


async def test_poll_dispatches_each_sp_alert(base_deps):
    """Each SP_ALERT message gets dispatched."""
    fake_dispatcher = MagicMock()
    deps_with_dispatcher = TaskDeps(
        storage=base_deps.storage,
        sp_writer=base_deps.sp_writer,
        audit=base_deps.audit,
        dispatcher=fake_dispatcher,
    )

    msgs = [
        MagicMock(message_id=f"msg-{i}", subject=f"SP alert {i}",
                  body_text=f"body {i}", sender="sp@corp", received_at=None)
        for i in range(3)
    ]
    fake_receiver = MagicMock()
    fake_receiver.fetch_once = AsyncMock(return_value=msgs)

    fake_parsed = SimpleNamespace(
        action_type="added",
        item_title="Item",
        body_kvs={"k": "v"},
        field_deltas=None,
        routing_key=SimpleNamespace(
            list_name="Deliverables",
            list_suffix="MMK",
            milestone_name="P1",
            project_id="2350",
            item_number=5,
        ),
    )

    from core.src.email_service.protocol import EmailKind
    with override_task_deps(deps_with_dispatcher), \
         patch("core.src.email_service.build_receiver", return_value=fake_receiver), \
         patch(
             "core.src.email_service.config.EmailServiceConfig.from_sources",
             return_value=MagicMock(),
         ), \
         patch("core.src.credential_service.service.SopsCredentialService") as mock_cred_cls, \
         patch(
             "core.src.email_service.inbound.classifier.classify",
             return_value=EmailKind.SP_ALERT,
         ), \
         patch("core.src.email_service.sp_alert_parser.SpAlertParser") as mock_parser_cls:
        mock_cred = MagicMock()
        mock_cred.load = AsyncMock()
        mock_cred_cls.return_value = mock_cred
        mock_parser_cls.return_value = MagicMock(parse=MagicMock(return_value=fake_parsed))
        result = await _run_poll()

    assert result["messages_fetched"] == 3
    assert result["sp_alerts"] == 3
    assert result["dispatched"] == 3
    assert result["parse_failures"] == 0
    assert fake_dispatcher.dispatch.call_count == 3


async def test_poll_enqueues_owner_replies(base_deps):
    """OWNER_REPLY messages are not dispatched as SP alerts -- they enqueue
    apply_owner_reply_task via .delay() per Phase B 2026-06-28 wiring.
    Replaces the old test_poll_skips_non_sp_alerts which asserted silent skip
    (the pre-Phase-B behavior).
    """
    fake_dispatcher = MagicMock()
    deps_with_dispatcher = TaskDeps(
        storage=base_deps.storage,
        sp_writer=base_deps.sp_writer,
        audit=base_deps.audit,
        dispatcher=fake_dispatcher,
    )

    # Use real-ish InboundMessage-shaped mocks so the payload-build in
    # _enqueue_owner_reply produces JSON-serializable strings (MagicMock
    # attributes would break celery's task.delay JSON encoding).
    from datetime import datetime, timezone
    def _msg(i):
        m = MagicMock()
        m.message_id = f"msg-{i}"
        m.subject = f"Re: [HILDA] Status request -- BATCH-test{i}"
        m.body_text = ""
        m.body_html = "<p>HILDA-BATCH-ID: BATCH-testX</p>"
        m.sender = "owner@corp.example"
        m.to_addrs = ()
        m.cc_addrs = ()
        m.received_at = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
        m.attachments = ()
        return m
    msgs = [_msg(i) for i in range(3)]
    fake_receiver = MagicMock()
    fake_receiver.fetch_once = AsyncMock(return_value=msgs)

    from core.src.email_service.protocol import EmailKind
    with override_task_deps(deps_with_dispatcher), \
         patch("core.src.email_service.build_receiver", return_value=fake_receiver), \
         patch(
             "core.src.email_service.config.EmailServiceConfig.from_sources",
             return_value=MagicMock(),
         ), \
         patch("core.src.credential_service.service.SopsCredentialService") as mock_cred_cls, \
         patch(
             "core.src.email_service.inbound.classifier.classify",
             return_value=EmailKind.OWNER_REPLY,
         ), \
         patch(
             "core.src.workflow_engine.tasks.owner_reply.apply_owner_reply_task.delay"
         ) as mock_delay:
        mock_cred = MagicMock()
        mock_cred.load = AsyncMock()
        mock_cred_cls.return_value = mock_cred
        result = await _run_poll()

    assert result["messages_fetched"] == 3
    assert result["sp_alerts"] == 0
    assert result["dispatched"] == 0
    assert result["owner_replies"] == 3
    assert result["owner_reply_enqueued"] == 3
    assert mock_delay.call_count == 3
    # Each call gets a JSON-serializable payload dict.
    for call in mock_delay.call_args_list:
        payload = call.args[0]
        assert isinstance(payload, dict)
        assert payload["message_id"].startswith("msg-")
        assert payload["sender"] == "owner@corp.example"
    assert fake_dispatcher.dispatch.call_count == 0


async def test_poll_skips_silent_noop_parses(base_deps):
    """parser.parse returning None -> silent skip; no dispatch."""
    fake_dispatcher = MagicMock()
    deps_with_dispatcher = TaskDeps(
        storage=base_deps.storage,
        sp_writer=base_deps.sp_writer,
        audit=base_deps.audit,
        dispatcher=fake_dispatcher,
    )

    msgs = [MagicMock() for _ in range(2)]
    fake_receiver = MagicMock()
    fake_receiver.fetch_once = AsyncMock(return_value=msgs)

    from core.src.email_service.protocol import EmailKind
    with override_task_deps(deps_with_dispatcher), \
         patch("core.src.email_service.build_receiver", return_value=fake_receiver), \
         patch(
             "core.src.email_service.config.EmailServiceConfig.from_sources",
             return_value=MagicMock(),
         ), \
         patch("core.src.credential_service.service.SopsCredentialService") as mock_cred_cls, \
         patch(
             "core.src.email_service.inbound.classifier.classify",
             return_value=EmailKind.SP_ALERT,
         ), \
         patch("core.src.email_service.sp_alert_parser.SpAlertParser") as mock_parser_cls:
        mock_cred = MagicMock()
        mock_cred.load = AsyncMock()
        mock_cred_cls.return_value = mock_cred
        mock_parser_cls.return_value = MagicMock(parse=MagicMock(return_value=None))
        result = await _run_poll()

    assert result["sp_alerts"] == 2
    assert result["dispatched"] == 0
    assert result["parse_failures"] == 0
    assert fake_dispatcher.dispatch.call_count == 0


def test_beat_schedule_includes_poll_ews_inbox():
    """celery_app.beat_schedule has the periodic poll entry."""
    from core.src.workflow_engine import hilda_celery_app

    schedule = hilda_celery_app.conf.beat_schedule
    assert "poll_ews_inbox_60s" in schedule
    entry = schedule["poll_ews_inbox_60s"]
    assert entry["task"] == "core.src.workflow_engine.tasks.email_polling.poll_ews_inbox"
    assert entry["schedule"] == 60.0


def test_poll_ews_inbox_task_registered():
    """Task is importable + Celery-registered."""
    from core.src.workflow_engine.celery_app import hilda_celery_app
    from core.src.workflow_engine.tasks.email_polling import poll_ews_inbox_task   # noqa: F401

    name = "core.src.workflow_engine.tasks.email_polling.poll_ews_inbox"
    assert name in hilda_celery_app.tasks
