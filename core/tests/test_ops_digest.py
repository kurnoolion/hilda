"""UR-8 (Ph-2 2026-08-01) — ops_unrouted_digest tick task.

Focused unit tests for the aggregate + kill-switch + email-shape logic.
Task called directly with monkeypatched get_task_deps + config + async
storage helpers, mirroring test_setup_complete_notification.py.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.src.workflow_engine.tasks.ops_digest import (
    _build_body,
    _build_subject,
    ops_unrouted_digest_tick_task,
)


def _mk_cfg(*, enabled=True, recipient="ops@hilda.local", min_count=1):
    return SimpleNamespace(
        ops_unrouted_digest_enabled=enabled,
        ops_unrouted_digest_recipient=recipient,
        ops_unrouted_digest_min_count=min_count,
        ops_unrouted_digest_beat_interval_seconds=604800,
    )


def _make_deps(*, email_sender_none=False):
    deps = MagicMock()
    sends = []
    async def _send(*, to, cc, subject, body, attachments):
        sends.append({"to": list(to), "subject": subject, "body": body,
                      "attachments": list(attachments)})
        return "<msg-id@hilda.local>"
    if email_sender_none:
        deps.email_sender = None
    else:
        deps.email_sender.send = _send
    deps._sends = sends
    return deps


@pytest.fixture
def wire(monkeypatch):
    """Factory: install cfg, deps, and async storage stubs."""
    def _build(*, cfg, scopes, per_scope_counts, email_sender_none=False):
        deps = _make_deps(email_sender_none=email_sender_none)
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.ops_digest.get_task_deps",
            lambda: deps,
        )
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.ops_digest."
            "TpmNotificationConfig.from_sources",
            lambda: cfg,
        )

        async def _list_scopes():
            return list(scopes)

        async def _count(c, d, m):
            return per_scope_counts.get((c, d, m), 0)

        monkeypatch.setattr(
            "core.src.storage.unrouted_ops.list_all_unrouted_scopes",
            _list_scopes,
        )
        monkeypatch.setattr(
            "core.src.storage.unrouted_ops.count_unrouted_for_scope",
            _count,
        )
        return deps
    return _build


class TestKillSwitches:
    def test_disabled_returns_disabled(self, monkeypatch):
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.ops_digest."
            "TpmNotificationConfig.from_sources",
            lambda: _mk_cfg(enabled=False),
        )
        r = ops_unrouted_digest_tick_task(None, None)
        assert r["outcome"] == "disabled"

    def test_no_recipient_short_circuits(self, monkeypatch):
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.ops_digest."
            "TpmNotificationConfig.from_sources",
            lambda: _mk_cfg(recipient=""),
        )
        r = ops_unrouted_digest_tick_task(None, None)
        assert r["outcome"] == "no_recipient"

    def test_no_email_sender_short_circuits(self, wire):
        wire(cfg=_mk_cfg(), scopes=[], per_scope_counts={},
             email_sender_none=True)
        r = ops_unrouted_digest_tick_task(None, None)
        assert r["outcome"] == "no_email_sender"


class TestScopeAggregation:
    def test_zero_scopes_no_send(self, wire):
        deps = wire(cfg=_mk_cfg(), scopes=[], per_scope_counts={})
        r = ops_unrouted_digest_tick_task(None, None)
        assert r["outcome"] == "no_scopes_over_threshold"
        assert r["total_unrouted"] == 0
        assert deps._sends == []

    def test_min_count_filters_scopes(self, wire):
        """Scope with count < min_count is filtered out entirely."""
        deps = wire(
            cfg=_mk_cfg(min_count=3),
            scopes=[
                ("MMK", "SM-A012U", "DRR"),
                ("MMK", "SM-A012U", "GCF"),
            ],
            per_scope_counts={
                ("MMK", "SM-A012U", "DRR"): 5,
                ("MMK", "SM-A012U", "GCF"): 2,
            },
        )
        r = ops_unrouted_digest_tick_task(None, None)
        assert r["outcome"] == "sent"
        assert r["scopes_scanned"] == 1
        assert r["total_unrouted"] == 5
        assert len(deps._sends) == 1
        assert "GCF" not in deps._sends[0]["body"]
        assert "DRR" in deps._sends[0]["body"]

    def test_sends_email_with_totals(self, wire):
        deps = wire(
            cfg=_mk_cfg(),
            scopes=[
                ("MMK", "SM-A012U", "DRR"),
                ("XYZ", "SM-M456U", "GCF"),
            ],
            per_scope_counts={
                ("MMK", "SM-A012U", "DRR"): 3,
                ("XYZ", "SM-M456U", "GCF"): 4,
            },
        )
        r = ops_unrouted_digest_tick_task(None, None)
        assert r["outcome"] == "sent"
        assert r["scopes_scanned"] == 2
        assert r["total_unrouted"] == 7
        assert r["message_id"] == "<msg-id@hilda.local>"
        assert deps._sends[0]["to"] == ["ops@hilda.local"]
        assert "7 across 2 scope(s)" in deps._sends[0]["subject"]
        # Audit row written (best-effort)
        deps.audit.write_communication_log.assert_called_once()
        call = deps.audit.write_communication_log.call_args
        assert call.kwargs["action_type"] == "ops_unrouted_digest_sent"
        assert call.kwargs["details"]["scopes_reported"] == 2
        assert call.kwargs["details"]["total_unrouted"] == 7


class TestBodyRendering:
    def test_body_orders_by_count_desc(self):
        rows = [
            {"customer_id": "A", "device_id": "d1", "milestone_id": "m1", "unrouted": 1},
            {"customer_id": "B", "device_id": "d2", "milestone_id": "m2", "unrouted": 9},
            {"customer_id": "C", "device_id": "d3", "milestone_id": "m3", "unrouted": 4},
        ]
        html = _build_body(rows, total=14)
        # Row for B (unrouted=9) should appear before row for C (unrouted=4)
        # which should appear before row for A (unrouted=1).
        pos_b = html.find(">B</td>")
        pos_c = html.find(">C</td>")
        pos_a = html.find(">A</td>")
        assert 0 < pos_b < pos_c < pos_a

    def test_body_carries_triage_links(self):
        rows = [{"customer_id": "MMK", "device_id": "SM-A012U",
                 "milestone_id": "DRR", "unrouted": 5}]
        html = _build_body(rows, total=5)
        assert "/browse/MMK/SM-A012U/DRR/_unknownTG/" in html
        assert ">triage</a>" in html


class TestSubjectShape:
    def test_subject_includes_total_and_scope_count(self):
        s = _build_subject(42, 7)
        assert "42 across 7 scope(s)" in s
        assert s.startswith("[HILDA]")
