"""DRRP1-STATE-1 phase 3 (2026-09-10) -- sync-8 belt-and-suspenders sweep.

Pure-function tests for `_sync_8_drr_mapping_promote`. Stubs storage +
monkeypatches the reconcile helper so we assert on the sync's iteration
+ eligibility logic without touching real transitions.
"""
from __future__ import annotations

from types import SimpleNamespace


def _make_item(item_no: int, state: str, device_id: str = "SM-S671U1"):
    return SimpleNamespace(
        item_id=f"MMK-{device_id}-DRR-{item_no}",
        delivery_item_id=f"MMK-{device_id}-DRR-{item_no}",
        item_no=item_no,
        customer_id="MMK",
        device_id=device_id,
        milestone_id="DRR",
        delivery_state=state,
    )


class _StubStorage:
    def __init__(self, items):
        self._items = items

    def list_items_for_milestone(self, milestone_id, _tenant=None):
        return self._items


def _patched_mapping(monkeypatch, pairs, source="DRR", target="P1"):
    from core.src.template_schema.milestone_item_mapping import MappingBlock
    block = MappingBlock(source_milestone=source, target_milestone=target, pairs=pairs)
    monkeypatch.setattr(
        "core.src.template_schema.milestone_item_mapping.get_mapping_blocks",
        lambda customer_id: [block],
    )


def _default_cfg():
    from core.src.workflow_engine.reconcile_config import ReconcileConfig
    return ReconcileConfig()


class TestSync8DrrMappingPromote:
    def test_iterates_rfs_items_and_calls_reconcile_helper(self, monkeypatch):
        _patched_mapping(monkeypatch, {50: 10, 52: 7})
        deps = SimpleNamespace(
            storage=_StubStorage([
                _make_item(50, "ReadyForSubmission"),
                _make_item(52, "ReadyForSubmission"),
                _make_item(55, "Open"),  # not RFS -> skipped
            ]),
            sp_writer=None, audit=None,
        )
        calls: list = []

        def _fake_reconcile(**kw):
            calls.append(kw)
            # Simulate one target promoted per call
            return {
                "outcome": "reconciled",
                "promoted": [f"MMK-SM-S671U1-P1-{kw['source_item_no'] * 10}"],
                "skipped_under_pm_review": [],
                "skipped_already_final": [],
                "skipped_no_target_item": [],
                "failed": [],
            }

        monkeypatch.setattr(
            "core.src.tracker.drr_mapping_reconcile.reconcile_target_items_on_source_rfs",
            _fake_reconcile,
        )
        stats = {
            "sync_8_promoted": 0,
            "sync_8_skipped_ineligible": 0,
            "sync_8_skipped": 0,
        }
        from core.src.workflow_engine.tasks.reconcile import _sync_8_drr_mapping_promote
        _sync_8_drr_mapping_promote(
            deps, _default_cfg(), stats, "corr-1",
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
        )
        assert len(calls) == 2   # two RFS items
        assert stats["sync_8_promoted"] == 2

    def test_non_source_milestone_is_no_op(self, monkeypatch):
        # sync-8 iterates milestones; P1 is not a source in the mapping,
        # so the sweep must return without touching storage.
        _patched_mapping(monkeypatch, {50: 10})
        deps = SimpleNamespace(storage=_StubStorage([]), sp_writer=None, audit=None)
        calls: list = []
        monkeypatch.setattr(
            "core.src.tracker.drr_mapping_reconcile.reconcile_target_items_on_source_rfs",
            lambda **kw: calls.append(kw) or {"promoted": []},
        )
        stats = {
            "sync_8_promoted": 0,
            "sync_8_skipped_ineligible": 0,
            "sync_8_skipped": 0,
        }
        from core.src.workflow_engine.tasks.reconcile import _sync_8_drr_mapping_promote
        _sync_8_drr_mapping_promote(
            deps, _default_cfg(), stats, "corr-1",
            customer_id="MMK", device_id="SM-S671U1", milestone_id="P1",
        )
        assert calls == []
        assert stats["sync_8_promoted"] == 0

    def test_customer_without_mapping_is_no_op(self, monkeypatch):
        monkeypatch.setattr(
            "core.src.template_schema.milestone_item_mapping.get_mapping_blocks",
            lambda customer_id: [],
        )
        deps = SimpleNamespace(storage=_StubStorage([]), sp_writer=None, audit=None)
        stats = {
            "sync_8_promoted": 0,
            "sync_8_skipped_ineligible": 0,
            "sync_8_skipped": 0,
        }
        from core.src.workflow_engine.tasks.reconcile import _sync_8_drr_mapping_promote
        _sync_8_drr_mapping_promote(
            deps, _default_cfg(), stats, "corr-1",
            customer_id="OTHER", device_id="SM-X", milestone_id="DRR",
        )
        # No mapping blocks -> the "source_milestones" set is empty so DRR
        # doesn't match and the sweep exits. No promotions, no calls.
        assert stats["sync_8_promoted"] == 0

    def test_device_scope_filter_applied(self, monkeypatch):
        # list_items_for_milestone doesn't take device_id -- sync-8 filters
        # after the fetch. Item for a different device is skipped.
        _patched_mapping(monkeypatch, {50: 10})
        deps = SimpleNamespace(
            storage=_StubStorage([
                _make_item(50, "ReadyForSubmission", device_id="SM-OTHER"),
            ]),
            sp_writer=None, audit=None,
        )
        calls: list = []
        monkeypatch.setattr(
            "core.src.tracker.drr_mapping_reconcile.reconcile_target_items_on_source_rfs",
            lambda **kw: calls.append(kw) or {"promoted": []},
        )
        stats = {
            "sync_8_promoted": 0,
            "sync_8_skipped_ineligible": 0,
            "sync_8_skipped": 0,
        }
        from core.src.workflow_engine.tasks.reconcile import _sync_8_drr_mapping_promote
        _sync_8_drr_mapping_promote(
            deps, _default_cfg(), stats, "corr-1",
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
        )
        assert calls == []   # device mismatch filtered

    def test_disabled_sync_8_is_no_op(self, monkeypatch):
        _patched_mapping(monkeypatch, {50: 10})
        deps = SimpleNamespace(
            storage=_StubStorage([_make_item(50, "ReadyForSubmission")]),
            sp_writer=None, audit=None,
        )
        calls: list = []
        monkeypatch.setattr(
            "core.src.tracker.drr_mapping_reconcile.reconcile_target_items_on_source_rfs",
            lambda **kw: calls.append(kw),
        )
        cfg = _default_cfg()
        cfg.sync_8_drr_mapping_promote.enabled = False
        stats = {
            "sync_8_promoted": 0,
            "sync_8_skipped_ineligible": 0,
            "sync_8_skipped": 0,
        }
        from core.src.workflow_engine.tasks.reconcile import _sync_8_drr_mapping_promote
        _sync_8_drr_mapping_promote(
            deps, cfg, stats, "corr-1",
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
        )
        assert calls == []
        assert stats["sync_8_skipped"] == 1

    def test_skipped_stats_aggregate_from_helper_summary(self, monkeypatch):
        _patched_mapping(monkeypatch, {50: 10})
        deps = SimpleNamespace(
            storage=_StubStorage([_make_item(50, "ReadyForSubmission")]),
            sp_writer=None, audit=None,
        )
        monkeypatch.setattr(
            "core.src.tracker.drr_mapping_reconcile.reconcile_target_items_on_source_rfs",
            lambda **kw: {
                "outcome": "reconciled",
                "promoted": [],
                "skipped_under_pm_review": ["MMK-SM-S671U1-P1-10"],
                "skipped_already_final": [],
                "skipped_no_target_item": [],
                "failed": [],
            },
        )
        stats = {
            "sync_8_promoted": 0,
            "sync_8_skipped_ineligible": 0,
            "sync_8_skipped": 0,
        }
        from core.src.workflow_engine.tasks.reconcile import _sync_8_drr_mapping_promote
        _sync_8_drr_mapping_promote(
            deps, _default_cfg(), stats, "corr-1",
            customer_id="MMK", device_id="SM-S671U1", milestone_id="DRR",
        )
        assert stats["sync_8_promoted"] == 0
        assert stats["sync_8_skipped_ineligible"] == 1
