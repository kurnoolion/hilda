"""DRRP1-STATE-1 phase 2 (2026-09-10) -- reconcile + doc-received hook helpers.

Pure-function tests for the two helper modules:
  * tracker.drr_mapping_reconcile.reconcile_target_items_on_source_rfs
  * tracker.doc_received_bounce.bounce_to_under_pm_review_if_rfs

Uses stub deps + monkeypatched update_delivery_state so the tests never
touch storage / celery / SP writer. Integration coverage of the real
call sites (pm_approval, inbound_attachment, dashboard route) is deferred
to the on-corp deploy verification.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# reconcile_target_items_on_source_rfs
# ---------------------------------------------------------------------------


class _StubStorage:
    """Minimal storage stub -- returns items keyed by (customer, device,
    milestone) tuple. Each item is a SimpleNamespace with the fields the
    reconcile helper reads."""

    def __init__(self, items_by_scope: dict):
        self._items_by_scope = items_by_scope

    def list_items_for_milestone(self, *, customer_id, device_id, milestone_id):
        return self._items_by_scope.get(
            (customer_id, device_id, milestone_id), [],
        )


def _make_p1_item(item_no: int, state: str) -> SimpleNamespace:
    return SimpleNamespace(
        item_id=f"MMK-SM-S671U1-P1-{item_no}",
        delivery_item_id=f"MMK-SM-S671U1-P1-{item_no}",
        item_no=item_no,
        customer_id="MMK",
        device_id="SM-S671U1",
        milestone_id="P1",
        delivery_state=state,
    )


class TestReconcileTargetItems:
    def _patched_uds(self, monkeypatch, dispatched: list):
        """Monkeypatch update_delivery_state to record every dispatch instead
        of touching storage / audit. Returns the list where calls accumulate."""
        def _fake_uds(**kwargs):
            dispatched.append(kwargs)
        monkeypatch.setattr(
            "core.src.tracker.transitions.update_delivery_state",
            _fake_uds,
        )
        return dispatched

    def _patched_mapping(self, monkeypatch, pairs):
        """Stub get_mapping_blocks with a single DRR->P1 block using
        `pairs` (dict[int, int] source_item_no -> target_item_no)."""
        from core.src.template_schema.milestone_item_mapping import MappingBlock
        block = MappingBlock(
            source_milestone="DRR",
            target_milestone="P1",
            pairs=pairs,
        )
        monkeypatch.setattr(
            "core.src.template_schema.milestone_item_mapping.get_mapping_blocks",
            lambda customer_id: [block],
        )

    def test_promote_open_p1_item_from_drr_source(self, monkeypatch):
        # DRR item 50 -> P1 item 10; P1 is Open; expect one dispatch.
        dispatched = self._patched_uds(monkeypatch, [])
        self._patched_mapping(monkeypatch, {50: 10})
        storage = _StubStorage({
            ("MMK", "SM-S671U1", "P1"): [_make_p1_item(10, "Open")],
        })
        deps = SimpleNamespace(storage=storage, sp_writer=None, audit=None)

        from core.src.tracker.drr_mapping_reconcile import (
            reconcile_target_items_on_source_rfs,
        )
        summary = reconcile_target_items_on_source_rfs(
            deps=deps,
            source_customer_id="MMK",
            source_device_id="SM-S671U1",
            source_milestone_id="DRR",
            source_item_no=50,
            correlation_id="corr-1",
        )
        assert summary["outcome"] == "reconciled"
        assert summary["promoted"] == ["MMK-SM-S671U1-P1-10"]
        assert len(dispatched) == 1
        call = dispatched[0]
        assert call["target_state"].value == "ReadyForSubmission"
        assert call["event_context"]["trigger_source"] == "drr_mapping_promote"
        assert call["event_context"]["drr_source_milestone_id"] == "DRR"
        assert call["event_context"]["drr_source_item_no"] == 50

    def test_skips_p1_item_in_under_pm_review(self, monkeypatch):
        # Per user 2026-09-09 #3.
        dispatched = self._patched_uds(monkeypatch, [])
        self._patched_mapping(monkeypatch, {50: 10})
        storage = _StubStorage({
            ("MMK", "SM-S671U1", "P1"): [_make_p1_item(10, "UnderPMReview")],
        })
        deps = SimpleNamespace(storage=storage, sp_writer=None, audit=None)

        from core.src.tracker.drr_mapping_reconcile import (
            reconcile_target_items_on_source_rfs,
        )
        summary = reconcile_target_items_on_source_rfs(
            deps=deps,
            source_customer_id="MMK", source_device_id="SM-S671U1",
            source_milestone_id="DRR", source_item_no=50,
        )
        assert summary["promoted"] == []
        assert summary["skipped_under_pm_review"] == ["MMK-SM-S671U1-P1-10"]
        assert dispatched == []  # no transition attempted

    def test_skips_already_final_p1_item(self, monkeypatch):
        dispatched = self._patched_uds(monkeypatch, [])
        self._patched_mapping(monkeypatch, {50: 10})
        storage = _StubStorage({
            ("MMK", "SM-S671U1", "P1"): [
                _make_p1_item(10, "ReadyForSubmission"),
            ],
        })
        deps = SimpleNamespace(storage=storage, sp_writer=None, audit=None)

        from core.src.tracker.drr_mapping_reconcile import (
            reconcile_target_items_on_source_rfs,
        )
        summary = reconcile_target_items_on_source_rfs(
            deps=deps,
            source_customer_id="MMK", source_device_id="SM-S671U1",
            source_milestone_id="DRR", source_item_no=50,
        )
        assert summary["promoted"] == []
        assert summary["skipped_already_final"] == ["MMK-SM-S671U1-P1-10"]
        assert dispatched == []

    def test_multi_source_second_fire_is_idempotent(self, monkeypatch):
        # Real MMK case: DRR 35 AND DRR 60 both -> P1 23.
        # First DRR RFS promotes P1 23; second DRR RFS finds P1 23 already
        # at RFS and no-ops -- correct per user 2026-09-09.
        dispatched = self._patched_uds(monkeypatch, [])
        self._patched_mapping(monkeypatch, {35: 23, 60: 23})
        p1_state = ["Open"]
        p1_23 = _make_p1_item(23, "Open")

        # First run
        storage = _StubStorage({("MMK", "SM-S671U1", "P1"): [p1_23]})
        deps = SimpleNamespace(storage=storage, sp_writer=None, audit=None)

        from core.src.tracker.drr_mapping_reconcile import (
            reconcile_target_items_on_source_rfs,
        )
        summary_a = reconcile_target_items_on_source_rfs(
            deps=deps, source_customer_id="MMK",
            source_device_id="SM-S671U1", source_milestone_id="DRR",
            source_item_no=35,
        )
        assert summary_a["promoted"] == ["MMK-SM-S671U1-P1-23"]

        # Simulate P1 state now RFS (real system: postgres write finalized).
        p1_23_after = _make_p1_item(23, "ReadyForSubmission")
        storage2 = _StubStorage({
            ("MMK", "SM-S671U1", "P1"): [p1_23_after],
        })
        deps2 = SimpleNamespace(storage=storage2, sp_writer=None, audit=None)
        summary_b = reconcile_target_items_on_source_rfs(
            deps=deps2, source_customer_id="MMK",
            source_device_id="SM-S671U1", source_milestone_id="DRR",
            source_item_no=60,
        )
        assert summary_b["promoted"] == []
        assert summary_b["skipped_already_final"] == ["MMK-SM-S671U1-P1-23"]

    def test_no_mapping_for_source_item_no_op(self, monkeypatch):
        dispatched = self._patched_uds(monkeypatch, [])
        self._patched_mapping(monkeypatch, {50: 10})   # only 50 mapped
        deps = SimpleNamespace(
            storage=_StubStorage({}), sp_writer=None, audit=None,
        )
        from core.src.tracker.drr_mapping_reconcile import (
            reconcile_target_items_on_source_rfs,
        )
        summary = reconcile_target_items_on_source_rfs(
            deps=deps, source_customer_id="MMK",
            source_device_id="SM-S671U1", source_milestone_id="DRR",
            source_item_no=99,   # not in the mapping
        )
        assert summary["promoted"] == []
        assert dispatched == []

    def test_no_mapping_yaml_for_customer(self, monkeypatch):
        # A customer with no mapping.yaml gets a clean no-op.
        monkeypatch.setattr(
            "core.src.template_schema.milestone_item_mapping.get_mapping_blocks",
            lambda customer_id: [],
        )
        deps = SimpleNamespace(
            storage=_StubStorage({}), sp_writer=None, audit=None,
        )
        from core.src.tracker.drr_mapping_reconcile import (
            reconcile_target_items_on_source_rfs,
        )
        summary = reconcile_target_items_on_source_rfs(
            deps=deps, source_customer_id="OTHER",
            source_device_id="SM-X", source_milestone_id="DRR",
            source_item_no=50,
        )
        assert summary["outcome"] == "no_mapping"

    def test_target_item_row_missing_recorded(self, monkeypatch):
        # Mapping says P1 20 should exist, but list_items_for_milestone
        # returns []; helper records the miss without raising.
        self._patched_mapping(monkeypatch, {67: 20})
        deps = SimpleNamespace(
            storage=_StubStorage({}),   # no rows for the P1 scope
            sp_writer=None, audit=None,
        )
        from core.src.tracker.drr_mapping_reconcile import (
            reconcile_target_items_on_source_rfs,
        )
        summary = reconcile_target_items_on_source_rfs(
            deps=deps, source_customer_id="MMK",
            source_device_id="SM-S671U1", source_milestone_id="DRR",
            source_item_no=67,
        )
        assert summary["promoted"] == []
        assert summary["skipped_no_target_item"] == [("P1", 20)]


# ---------------------------------------------------------------------------
# bounce_to_under_pm_review_if_rfs
# ---------------------------------------------------------------------------


class _StubStorageWithItem:
    def __init__(self, item):
        self._item = item

    def get_delivery_item(self, delivery_item_id):
        if self._item is None:
            return None
        return self._item if delivery_item_id == getattr(
            self._item, "delivery_item_id", None,
        ) else None


class TestDocReceivedBounce:
    def test_bounces_from_rfs(self, monkeypatch):
        dispatched: list = []
        monkeypatch.setattr(
            "core.src.tracker.transitions.update_delivery_state",
            lambda **kw: dispatched.append(kw),
        )
        item = _make_p1_item(10, "ReadyForSubmission")
        deps = SimpleNamespace(
            storage=_StubStorageWithItem(item),
            sp_writer=None, audit=None,
        )

        from core.src.tracker.doc_received_bounce import (
            bounce_to_under_pm_review_if_rfs,
        )
        summary = bounce_to_under_pm_review_if_rfs(
            deps=deps,
            delivery_item_id="MMK-SM-S671U1-P1-10",
            correlation_id="corr-2",
            source_marker="inbound_attachment_persist",
        )
        assert summary["outcome"] == "bounced"
        assert summary["from_state"] == "ReadyForSubmission"
        assert len(dispatched) == 1
        assert dispatched[0]["target_state"].value == "UnderPMReview"
        assert dispatched[0]["event_context"]["trigger_source"] == "doc_received_after_rfs"
        assert dispatched[0]["event_context"]["bounce_source"] == "inbound_attachment_persist"

    def test_no_op_when_not_rfs(self, monkeypatch):
        dispatched: list = []
        monkeypatch.setattr(
            "core.src.tracker.transitions.update_delivery_state",
            lambda **kw: dispatched.append(kw),
        )
        item = _make_p1_item(10, "Open")
        deps = SimpleNamespace(
            storage=_StubStorageWithItem(item),
            sp_writer=None, audit=None,
        )
        from core.src.tracker.doc_received_bounce import (
            bounce_to_under_pm_review_if_rfs,
        )
        summary = bounce_to_under_pm_review_if_rfs(
            deps=deps,
            delivery_item_id="MMK-SM-S671U1-P1-10",
        )
        assert summary["outcome"] == "no_op_not_rfs"
        assert summary["from_state"] == "Open"
        assert dispatched == []

    def test_item_not_found(self, monkeypatch):
        deps = SimpleNamespace(
            storage=_StubStorageWithItem(None),
            sp_writer=None, audit=None,
        )
        from core.src.tracker.doc_received_bounce import (
            bounce_to_under_pm_review_if_rfs,
        )
        summary = bounce_to_under_pm_review_if_rfs(
            deps=deps,
            delivery_item_id="does-not-exist",
        )
        assert summary["outcome"] == "item_not_found"

    def test_bounce_never_raises_on_uds_failure(self, monkeypatch):
        # A transition exception must not escape the helper.
        def _boom(**kw):
            raise RuntimeError("simulated transition failure")
        monkeypatch.setattr(
            "core.src.tracker.transitions.update_delivery_state", _boom,
        )
        item = _make_p1_item(10, "ReadyForSubmission")
        deps = SimpleNamespace(
            storage=_StubStorageWithItem(item),
            sp_writer=None, audit=None,
        )
        from core.src.tracker.doc_received_bounce import (
            bounce_to_under_pm_review_if_rfs,
        )
        summary = bounce_to_under_pm_review_if_rfs(
            deps=deps,
            delivery_item_id="MMK-SM-S671U1-P1-10",
        )
        assert summary["outcome"] == "error"
