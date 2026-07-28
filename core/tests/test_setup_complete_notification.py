"""SETUP-1 (2026-07-28) — setup_complete_notification tick.

Focused unit tests for the completion-check + idempotency logic. Task is
called directly (not via Celery broker) with a monkeypatched get_task_deps,
mirroring the test_tpm_sp_close.py pattern.

Sub-modules already covered by their own tests:
  * TPM email extraction — test_tpm_notification_user_field.py (TPM-1..4)
  * EmailSender wiring   — email_service tests
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.src.workflow_engine.tasks.setup_complete_notification import (
    setup_complete_notification_tick_task,
    _build_subject,
    _build_body,
)


def _mk_item(customer_id, device_id, milestone_id, item_no, state, tg="hw_pl"):
    return SimpleNamespace(
        item_id=f"{customer_id}-{device_id}-{milestone_id}-{item_no}",
        customer_id=customer_id,
        device_id=device_id,
        milestone_id=milestone_id,
        item_no=item_no,
        delivery_state=state,
        tg_name=tg,
    )


def _make_deps(*, items_by_milestone=None, already_notified=None,
               storage_none=False, sp_writer_none=False,
               email_sender_none=False):
    deps = MagicMock()
    if storage_none:
        deps.storage = None
    else:
        items_by_milestone = items_by_milestone or {}
        deps.storage.list_items_for_milestone = MagicMock(
            side_effect=lambda mid, states=None: list(items_by_milestone.get(mid, []))
        )
    if sp_writer_none:
        deps.sp_writer = None
    if email_sender_none:
        deps.email_sender = None

    already = set(already_notified or [])
    def _query(action_type=None, details_contains=None):
        if action_type != "setup_complete_notified":
            return []
        key = (details_contains.get("customer_id"),
               details_contains.get("device_id"),
               details_contains.get("milestone_id"))
        return [{"marker": key}] if key in already else []
    deps.audit.query_communications = MagicMock(side_effect=_query)

    sends = []
    async def _send(*, to, cc, subject, body, attachments):
        sends.append({"to": list(to), "subject": subject, "body": body,
                       "attachments": list(attachments)})
        return "<msg-id@hilda.local>"
    deps.email_sender.send = _send
    deps._sends = sends  # test hook
    return deps


@pytest.fixture
def deps_and_patches(monkeypatch):
    """Return a factory that installs a fresh deps + patches for one test."""
    def _build(**kwargs):
        deps = _make_deps(**kwargs)
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.setup_complete_notification.get_task_deps",
            lambda: deps,
        )
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.setup_complete_notification."
            "TpmNotificationConfig.from_sources",
            lambda: SimpleNamespace(setup_complete_enabled=True),
        )
        return deps
    return _build


class TestDisabledAndPreflight:
    def test_disabled_config_returns_disabled(self, monkeypatch):
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.setup_complete_notification."
            "TpmNotificationConfig.from_sources",
            lambda: SimpleNamespace(setup_complete_enabled=False),
        )
        r = setup_complete_notification_tick_task(None, None)
        assert r["outcome"] == "disabled"
        assert r["scopes_scanned"] == 0

    def test_no_sp_writer_returns_no_sp_writer(self, deps_and_patches):
        deps_and_patches(sp_writer_none=True)
        r = setup_complete_notification_tick_task(None, None)
        assert r["outcome"] == "no_sp_writer"

    def test_no_storage_returns_no_storage(self, deps_and_patches):
        deps_and_patches(storage_none=True)
        r = setup_complete_notification_tick_task(None, None)
        assert r["outcome"] == "no_storage"


class TestCompletionCheck:

    def test_skips_when_any_item_is_not_started(self, deps_and_patches, monkeypatch):
        items = [
            _mk_item("MMK", "SM-S671U1", "DRR", 1, "Open"),
            _mk_item("MMK", "SM-S671U1", "DRR", 2, "Not Started"),
            _mk_item("MMK", "SM-S671U1", "DRR", 3, "Open"),
        ]
        deps = deps_and_patches(items_by_milestone={"DRR": items})
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.setup_complete_notification._list_scopes",
            lambda deps_: [("MMK", "SM-S671U1", "DRR")],
        )
        r = setup_complete_notification_tick_task(None, None)
        assert r["sends_attempted"] == 0
        assert r["sends_succeeded"] == 0
        assert deps._sends == []

    def test_sends_when_all_past_not_started(self, deps_and_patches, monkeypatch):
        items = [_mk_item("MMK", "SM-S671U1", "DRR", i, "Open") for i in range(1, 6)]
        deps = deps_and_patches(items_by_milestone={"DRR": items})
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.setup_complete_notification._list_scopes",
            lambda deps_: [("MMK", "SM-S671U1", "DRR")],
        )
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.tpm_notification._read_tpm_email",
            lambda deps_, c, d: ("t.arasu@samsung.com", "Thendral Arasu"),
        )
        r = setup_complete_notification_tick_task(None, None)
        assert r["sends_attempted"] == 1
        assert r["sends_succeeded"] == 1
        assert len(deps._sends) == 1
        sent = deps._sends[0]
        assert sent["to"] == ["t.arasu@samsung.com"]
        assert "5 items" in sent["subject"]
        assert "[MMK/SM-S671U1/DRR]" in sent["subject"]
        assert sent["attachments"] == []

        # Audit row written
        assert deps.audit.write_communication_log.called
        audit_kwargs = deps.audit.write_communication_log.call_args.kwargs
        assert audit_kwargs["action_type"] == "setup_complete_notified"
        assert audit_kwargs["details"]["item_count"] == 5
        assert audit_kwargs["details"]["customer_id"] == "MMK"

    def test_skips_already_notified_scope(self, deps_and_patches, monkeypatch):
        items = [_mk_item("MMK", "SM-S671U1", "DRR", 1, "Open")]
        deps = deps_and_patches(
            items_by_milestone={"DRR": items},
            already_notified=[("MMK", "SM-S671U1", "DRR")],
        )
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.setup_complete_notification._list_scopes",
            lambda deps_: [("MMK", "SM-S671U1", "DRR")],
        )
        r = setup_complete_notification_tick_task(None, None)
        assert r["sends_attempted"] == 0
        assert deps._sends == []

    def test_empty_scope_skipped_cleanly(self, deps_and_patches, monkeypatch):
        deps = deps_and_patches(items_by_milestone={"DRR": []})
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.setup_complete_notification._list_scopes",
            lambda deps_: [("MMK", "SM-S671U1", "DRR")],
        )
        r = setup_complete_notification_tick_task(None, None)
        assert r["sends_attempted"] == 0
        assert r["scopes_scanned"] == 1

    def test_filters_by_device_id_per_scope(self, deps_and_patches, monkeypatch):
        # SM-S671U1 all Open -> ready. SM-M777U has Not Started -> skip.
        items = [
            _mk_item("MMK", "SM-S671U1", "DRR", 1, "Open"),
            _mk_item("MMK", "SM-S671U1", "DRR", 2, "Open"),
            _mk_item("MMK", "SM-M777U",  "DRR", 1, "Not Started"),
        ]
        deps = deps_and_patches(items_by_milestone={"DRR": items})
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.setup_complete_notification._list_scopes",
            lambda deps_: [
                ("MMK", "SM-S671U1", "DRR"),
                ("MMK", "SM-M777U",  "DRR"),
            ],
        )
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.tpm_notification._read_tpm_email",
            lambda deps_, c, d: ("t.arasu@samsung.com", "T"),
        )
        r = setup_complete_notification_tick_task(None, None)
        assert r["sends_attempted"] == 1
        assert r["sends_succeeded"] == 1

    def test_missing_tpm_email_skips_send(self, deps_and_patches, monkeypatch):
        items = [_mk_item("MMK", "SM-S671U1", "DRR", 1, "Open")]
        deps = deps_and_patches(items_by_milestone={"DRR": items})
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.setup_complete_notification._list_scopes",
            lambda deps_: [("MMK", "SM-S671U1", "DRR")],
        )
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.tpm_notification._read_tpm_email",
            lambda deps_, c, d: (None, None),
        )
        r = setup_complete_notification_tick_task(None, None)
        assert r["sends_attempted"] == 1  # attempt started
        assert r["sends_succeeded"] == 0  # but skipped at TPM lookup


class TestSubjectAndBody:
    def test_subject_shape(self):
        items = [_mk_item("MMK", "SM-S671U1", "DRR", i, "Open") for i in range(1, 88)]
        s = _build_subject("MMK", "SM-S671U1", "DRR", items)
        assert s == "[MMK/SM-S671U1/DRR] Setup deliverables complete — 87 items ready"

    def test_body_contains_scope_count_and_tg_breakdown(self):
        items = [
            _mk_item("MMK", "SM-S671U1", "DRR", 1, "Open", tg="hw_pl"),
            _mk_item("MMK", "SM-S671U1", "DRR", 2, "Open", tg="hw_pl"),
            _mk_item("MMK", "SM-S671U1", "DRR", 3, "Open", tg="dfit"),
        ]
        body = _build_body(
            tpm_name="Thendral", customer_id="MMK",
            device_id="SM-S671U1", milestone_id="DRR", items=items,
        )
        assert "Dear Thendral" in body
        assert "MMK / SM-S671U1 / DRR" in body
        assert "<b>3</b> items" in body
        assert "hw_pl" in body and "dfit" in body

    def test_body_greeting_fallback_when_no_name(self):
        items = [_mk_item("MMK", "SM-S671U1", "DRR", 1, "Open")]
        body = _build_body(
            tpm_name=None, customer_id="MMK",
            device_id="SM-S671U1", milestone_id="DRR", items=items,
        )
        assert "Hi," in body
        assert "Dear " not in body

    def test_body_handles_missing_tg(self):
        items = [
            SimpleNamespace(delivery_state="Open", tg_name=None),
            SimpleNamespace(delivery_state="Open", tg_name=""),
        ]
        body = _build_body(
            tpm_name="TPM", customer_id="C",
            device_id="D", milestone_id="M", items=items,
        )
        assert "(no TG)" in body
