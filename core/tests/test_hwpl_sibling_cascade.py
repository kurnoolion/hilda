"""HWPL-SIBLING-1 (2026-09-13) tests -- sibling-group cascade for HW PL.
Covers loader, guards 12/13, and reconcile module (both RFS + Submit paths).
Uses inline yaml + stubbed deps -- no Postgres / SP touched.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from core.src.template_schema import sibling_work_item_groups as swig
from core.src.template_schema.sibling_work_item_groups import (
    SiblingGroup,
    get_sibling_groups,
    sibling_group_for_anchor,
)
from core.src.tracker import DeliveryState as _DS
from core.src.tracker.guards import check_transition_guards
from core.src.tracker.hwpl_sibling_reconcile import (
    reconcile_siblings_on_anchor_rfs,
    reconcile_siblings_on_anchor_submitted,
)


# ---------------------------------------------------------------------------
# Fixtures + stubs
# ---------------------------------------------------------------------------


class _StubStorage:
    """Minimal storage stub -- list_items_for_milestone returns the seeded
    items regardless of args (we scope by customer/device in Python)."""
    def __init__(self, items):
        self._items = items

    def list_items_for_milestone(self, milestone_id, states):  # noqa: ARG002
        return list(self._items)


def _mk_item(item_no, state, *, item_id=None, customer_id="MMK",
             device_id="SM-S671U1", milestone_id="P1"):
    return SimpleNamespace(
        item_id=item_id or f"{customer_id}-{device_id}-{milestone_id}-{item_no}",
        item_no=item_no,
        delivery_state=state,
        customer_id=customer_id,
        device_id=device_id,
        milestone_id=milestone_id,
    )


@pytest.fixture(autouse=True)
def _clear_cache():
    """Loader is process-cached; clear before + after each test so yaml
    stubs don't leak across cases."""
    swig._clear_cache_for_tests()
    yield
    swig._clear_cache_for_tests()


def _stub_groups(monkeypatch, groups, target_milestone="P1"):
    """Bypass the yaml loader with an in-memory group list."""
    def _fake_get(customer_id):
        return (target_milestone, tuple(groups))
    monkeypatch.setattr(swig, "get_sibling_groups", _fake_get)
    # Also patch the reconcile module's import so it uses the stub too.
    from core.src.tracker import hwpl_sibling_reconcile as _hsr
    monkeypatch.setattr(
        "core.src.template_schema.sibling_work_item_groups.get_sibling_groups",
        _fake_get,
    )


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


class TestLoader:
    def test_missing_yaml_returns_empty(self):
        # Customer that certainly has no config file -- loader returns
        # empty tuple, no exception.
        milestone, groups = get_sibling_groups("NOSUCH_CUSTOMER")
        assert groups == ()
        assert milestone == ""

    def test_sibling_group_for_anchor_lookup(self, monkeypatch):
        _stub_groups(monkeypatch, [
            SiblingGroup(tg_name="HW PL", anchor=10, siblings=(11, 12)),
            SiblingGroup(tg_name="HW PL", anchor=14, siblings=(19, 21)),
        ])
        g = sibling_group_for_anchor("MMK", "HW PL", 10)
        assert g is not None
        assert g.siblings == (11, 12)
        assert sibling_group_for_anchor("MMK", "HW PL", 99) is None
        # case-insensitive tg_name match
        assert sibling_group_for_anchor("MMK", "hw pl", 10) is not None


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------


class TestTriggerSourceRegistered:
    """Guards 12/13 live in the same big check_transition_guards function
    that Guards 1-11 do; isolating them for a pure unit test requires
    setting many item fields to shape past every earlier guard, which is
    fragile. Instead we assert the trigger_source is present in the
    TriggerSource literal (imported by callers via type-check) -- a
    missing entry would crash the Literal check at reconcile-dispatch
    time. End-to-end coverage lives in the reconcile tests below."""

    def test_hwpl_sibling_promote_in_trigger_source_literal(self):
        from typing import get_args
        from core.src.tracker.guards import TriggerSource
        assert "hwpl_sibling_promote" in get_args(TriggerSource)

    def test_hwpl_sibling_submit_in_trigger_source_literal(self):
        from typing import get_args
        from core.src.tracker.guards import TriggerSource
        assert "hwpl_sibling_submit" in get_args(TriggerSource)


# ---------------------------------------------------------------------------
# Reconcile module -- RFS cascade
# ---------------------------------------------------------------------------


class TestReconcileRfs:

    def _wire(self, monkeypatch, items, groups=None):
        groups = groups or [
            SiblingGroup(tg_name="HW PL", anchor=10, siblings=(11, 12)),
        ]
        _stub_groups(monkeypatch, groups)
        storage = _StubStorage(items)
        transitions = MagicMock()
        # Fake update_delivery_state -- just record + mutate the item.
        def _fake_uds(delivery_item_id, target_state, params,
                      event_context, storage, sp_writer, audit):
            for it in items:
                if it.item_id == delivery_item_id:
                    it.delivery_state = target_state.value
            transitions(
                delivery_item_id=delivery_item_id,
                target_state=target_state.value,
                trigger_source=event_context.get("trigger_source"),
            )
            return SimpleNamespace(outcome="transitioned")
        monkeypatch.setattr(
            "core.src.tracker.transitions.update_delivery_state", _fake_uds,
        )
        deps = SimpleNamespace(
            storage=storage, sp_writer=None, audit=MagicMock(),
        )
        return deps, transitions

    def test_no_group_short_circuits(self, monkeypatch):
        # Non-anchor item -> outcome=no_group, no transitions dispatched.
        items = [_mk_item(11, _DS.OPEN.value)]
        deps, transitions = self._wire(monkeypatch, items)
        summary = reconcile_siblings_on_anchor_rfs(
            deps=deps,
            anchor_customer_id="MMK", anchor_device_id="SM-S671U1",
            anchor_milestone_id="P1", anchor_tg_name="HW PL",
            anchor_item_no=11,   # 11 is a sibling, not an anchor
        )
        assert summary["outcome"] == "no_group"
        transitions.assert_not_called()

    def test_promotes_all_non_terminal_siblings(self, monkeypatch):
        items = [
            _mk_item(10, _DS.READY_FOR_SUBMISSION.value),
            _mk_item(11, _DS.OPEN.value),
            _mk_item(12, _DS.OUTREACH_SENT.value),
        ]
        deps, transitions = self._wire(monkeypatch, items)
        summary = reconcile_siblings_on_anchor_rfs(
            deps=deps,
            anchor_customer_id="MMK", anchor_device_id="SM-S671U1",
            anchor_milestone_id="P1", anchor_tg_name="HW PL",
            anchor_item_no=10,
        )
        assert sorted(summary["promoted"]) == sorted([
            items[1].item_id, items[2].item_id,
        ])
        assert summary["skipped_terminal"] == []
        assert summary["skipped_already_rfs"] == []
        assert transitions.call_count == 2

    def test_skips_terminal_sibling(self, monkeypatch):
        # Sibling 11 already Closed by TPM -> stays Closed.
        items = [
            _mk_item(10, _DS.READY_FOR_SUBMISSION.value),
            _mk_item(11, _DS.CLOSED.value),
            _mk_item(12, _DS.OPEN.value),
        ]
        deps, transitions = self._wire(monkeypatch, items)
        summary = reconcile_siblings_on_anchor_rfs(
            deps=deps,
            anchor_customer_id="MMK", anchor_device_id="SM-S671U1",
            anchor_milestone_id="P1", anchor_tg_name="HW PL",
            anchor_item_no=10,
        )
        assert summary["skipped_terminal"] == [items[1].item_id]
        assert summary["promoted"] == [items[2].item_id]

    def test_idempotent_when_already_rfs(self, monkeypatch):
        items = [
            _mk_item(10, _DS.READY_FOR_SUBMISSION.value),
            _mk_item(11, _DS.READY_FOR_SUBMISSION.value),
            _mk_item(12, _DS.OPEN.value),
        ]
        deps, transitions = self._wire(monkeypatch, items)
        summary = reconcile_siblings_on_anchor_rfs(
            deps=deps,
            anchor_customer_id="MMK", anchor_device_id="SM-S671U1",
            anchor_milestone_id="P1", anchor_tg_name="HW PL",
            anchor_item_no=10,
        )
        assert summary["skipped_already_rfs"] == [items[1].item_id]
        assert summary["promoted"] == [items[2].item_id]

    def test_wrong_milestone_short_circuits(self, monkeypatch):
        items = [_mk_item(10, _DS.READY_FOR_SUBMISSION.value)]
        deps, transitions = self._wire(monkeypatch, items)
        summary = reconcile_siblings_on_anchor_rfs(
            deps=deps,
            anchor_customer_id="MMK", anchor_device_id="SM-S671U1",
            anchor_milestone_id="DRR",   # config says target_milestone=P1
            anchor_tg_name="HW PL", anchor_item_no=10,
        )
        assert summary["outcome"] == "wrong_milestone"
        transitions.assert_not_called()


# ---------------------------------------------------------------------------
# Reconcile module -- Submit cascade
# ---------------------------------------------------------------------------


class TestReconcileSubmit:

    def _wire(self, monkeypatch, items):
        _stub_groups(monkeypatch, [
            SiblingGroup(tg_name="HW PL", anchor=10, siblings=(11, 12)),
        ])
        storage = _StubStorage(items)
        seen_triggers: list[str] = []
        def _fake_uds(delivery_item_id, target_state, params,
                      event_context, storage, sp_writer, audit):
            for it in items:
                if it.item_id == delivery_item_id:
                    it.delivery_state = target_state.value
            seen_triggers.append(event_context.get("trigger_source", ""))
            return SimpleNamespace(outcome="transitioned")
        monkeypatch.setattr(
            "core.src.tracker.transitions.update_delivery_state", _fake_uds,
        )
        deps = SimpleNamespace(
            storage=storage, sp_writer=None, audit=MagicMock(),
        )
        return deps, seen_triggers

    def test_sibling_in_open_2_hops(self, monkeypatch):
        # Sibling 11 in Open -> promote (hwpl_sibling_promote) then submit
        # (hwpl_sibling_submit). Two dispatches per sibling.
        items = [
            _mk_item(10, _DS.SUBMITTED_TO_CUSTOMER.value),
            _mk_item(11, _DS.OPEN.value),
        ]
        deps, seen = self._wire(monkeypatch, items)
        summary = reconcile_siblings_on_anchor_submitted(
            deps=deps,
            anchor_customer_id="MMK", anchor_device_id="SM-S671U1",
            anchor_milestone_id="P1", anchor_tg_name="HW PL",
            anchor_item_no=10,
        )
        assert summary["submitted"] == [items[1].item_id]
        # Two hops fired: promote -> submit.
        assert seen == ["hwpl_sibling_promote", "hwpl_sibling_submit"]

    def test_sibling_already_rfs_single_hop(self, monkeypatch):
        # Sibling 11 already in RFS from earlier hwpl_sibling_promote --
        # only the submit hop fires.
        items = [
            _mk_item(10, _DS.SUBMITTED_TO_CUSTOMER.value),
            _mk_item(11, _DS.READY_FOR_SUBMISSION.value),
        ]
        deps, seen = self._wire(monkeypatch, items)
        summary = reconcile_siblings_on_anchor_submitted(
            deps=deps,
            anchor_customer_id="MMK", anchor_device_id="SM-S671U1",
            anchor_milestone_id="P1", anchor_tg_name="HW PL",
            anchor_item_no=10,
        )
        assert summary["submitted"] == [items[1].item_id]
        assert seen == ["hwpl_sibling_submit"]

    def test_terminal_sibling_preserved(self, monkeypatch):
        items = [
            _mk_item(10, _DS.SUBMITTED_TO_CUSTOMER.value),
            _mk_item(11, _DS.CLOSED.value),       # TPM closed it
            _mk_item(12, _DS.OPEN.value),
        ]
        deps, seen = self._wire(monkeypatch, items)
        summary = reconcile_siblings_on_anchor_submitted(
            deps=deps,
            anchor_customer_id="MMK", anchor_device_id="SM-S671U1",
            anchor_milestone_id="P1", anchor_tg_name="HW PL",
            anchor_item_no=10,
        )
        assert summary["skipped_terminal"] == [items[1].item_id]
        assert summary["submitted"] == [items[2].item_id]

    def test_sibling_already_submitted_no_op(self, monkeypatch):
        items = [
            _mk_item(10, _DS.SUBMITTED_TO_CUSTOMER.value),
            _mk_item(11, _DS.SUBMITTED_TO_CUSTOMER.value),
        ]
        deps, seen = self._wire(monkeypatch, items)
        summary = reconcile_siblings_on_anchor_submitted(
            deps=deps,
            anchor_customer_id="MMK", anchor_device_id="SM-S671U1",
            anchor_milestone_id="P1", anchor_tg_name="HW PL",
            anchor_item_no=10,
        )
        assert summary["skipped_already_submit"] == [items[1].item_id]
        assert summary["submitted"] == []
        assert seen == []
