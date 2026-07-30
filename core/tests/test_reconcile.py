"""test_reconcile.py -- unit tests for meta-reconciler + 5 sync sub-tasks.

Strand: reconcile-sync-cascade
Design: [D-142] 5-sync architecture + [D-143] SP-alerts-are-best-effort.

Focus: predicate logic per sync-type (fire vs no-op). End-to-end integration
lives in the corp-box smoke test tomorrow.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from core.src.workflow_engine.reconcile_config import (
    ReconcileConfig,
    SyncTypeConfig,
)
from core.src.workflow_engine.tasks.reconcile import (
    _elapsed_seconds,
    _iter_tuples,
    _sync_1_delivery_item_count,
    _sync_2_start_collection,
    _sync_3_pm_approval,
    _sync_4_submit_to_carrier,
    _sync_5_close_all_items,
)


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------


def _iso_ago(seconds: int) -> str:
    """Return ISO-8601 UTC timestamp `seconds` in the past (no `Z` suffix)."""
    return (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()


def _mk_item(item_no: int, device_id: str, delivery_state: str = "Not Started") -> SimpleNamespace:
    return SimpleNamespace(
        item_id=f"MMK-{device_id}-P1-{item_no}",
        item_no=item_no,
        device_id=device_id,
        milestone_id="P1",
        delivery_state=delivery_state,
    )


def _mk_deps(pg_items=None, sp_items=None, sp_milestone=None):
    """Build minimal deps mock. pg_items = list returned by storage; sp_items =
    list returned by sp_writer.get_items on delivery_items; sp_milestone = row
    returned on milestones."""
    storage = MagicMock()
    storage.list_items_for_milestone.return_value = pg_items or []
    sp_writer = MagicMock()

    def _get_items(entity, scope, canonical_filters=None):
        if entity == "delivery_items":
            return sp_items or []
        if entity == "milestones":
            return [sp_milestone] if sp_milestone else []
        return []

    sp_writer.get_items.side_effect = _get_items
    audit = MagicMock()
    return SimpleNamespace(storage=storage, sp_writer=sp_writer, audit=audit)


# ---------------------------------------------------------------------------
# _elapsed_seconds
# ---------------------------------------------------------------------------


class TestElapsedSeconds:
    def test_none_input(self):
        assert _elapsed_seconds(None) is None
        assert _elapsed_seconds("") is None

    def test_valid_iso(self):
        elapsed = _elapsed_seconds(_iso_ago(600))
        assert elapsed is not None
        assert 590 < elapsed < 610

    def test_z_suffix_stripped(self):
        elapsed = _elapsed_seconds(_iso_ago(300) + "Z")
        assert elapsed is not None
        assert 290 < elapsed < 310

    def test_malformed(self):
        assert _elapsed_seconds("not-a-timestamp") is None
        assert _elapsed_seconds(42) is None


# ---------------------------------------------------------------------------
# ReconcileConfig
# ---------------------------------------------------------------------------


class TestReconcileConfig:
    def test_defaults(self):
        cfg = ReconcileConfig()
        assert cfg.enabled is True
        assert cfg.interval_sec == 300
        assert cfg.sync_1_delivery_item_count.elapsed_threshold_sec == 0
        assert cfg.sync_3_pm_approval.elapsed_threshold_sec == 300

    def test_env_override(self, monkeypatch):
        monkeypatch.setenv("HILDA_RECONCILE_ENABLED", "false")
        monkeypatch.setenv("HILDA_RECONCILE_INTERVAL_SEC", "120")
        cfg = ReconcileConfig.from_sources()
        assert cfg.enabled is False
        assert cfg.interval_sec == 120

    def test_per_sync_disable(self):
        cfg = ReconcileConfig(
            sync_1_delivery_item_count=SyncTypeConfig(enabled=False),
        )
        assert cfg.sync_1_delivery_item_count.enabled is False
        assert cfg.sync_2_start_collection.enabled is True


# ---------------------------------------------------------------------------
# sync-1 delivery_item_count
# ---------------------------------------------------------------------------


class TestSync1DeliveryItemCount:
    def test_disabled_noop(self):
        cfg = ReconcileConfig(sync_1_delivery_item_count=SyncTypeConfig(enabled=False))
        deps = _mk_deps()
        stats = {"sync_1_backfilled": 0, "sync_1_skipped": 0}
        _sync_1_delivery_item_count(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", None)

        assert stats == {"sync_1_backfilled": 0, "sync_1_skipped": 0}

    def test_submit_triggered_stops_backfill(self):
        cfg = ReconcileConfig()
        deps = _mk_deps()
        stats = {"sync_1_backfilled": 0, "sync_1_skipped": 0}
        sp_milestone = {"milestone_submission_triggered_at": _iso_ago(100)}
        _sync_1_delivery_item_count(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        # No SP-read for deliverables since submit already clicked
        assert stats["sync_1_backfilled"] == 0

    def test_in_sync_no_backfill(self):
        cfg = ReconcileConfig()
        pg_items = [_mk_item(1, "SM-1"), _mk_item(2, "SM-1")]
        sp_items = [{"item_no": 1}, {"item_no": 2}]
        deps = _mk_deps(pg_items=pg_items, sp_items=sp_items)
        stats = {"sync_1_backfilled": 0, "sync_1_skipped": 0}
        sp_milestone = {"milestone_submission_triggered_at": None}
        _sync_1_delivery_item_count(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_1_backfilled"] == 0

    def test_missing_items_are_detected(self):
        """sync-1 identifies SP items not in Postgres and calls import task
        with trigger_source='sync_backfill_ingest'.
        """
        cfg = ReconcileConfig()
        pg_items = [_mk_item(1, "SM-1")]
        sp_items = [{"item_no": 1}, {"item_no": 2}, {"item_no": 3}]
        deps = _mk_deps(pg_items=pg_items, sp_items=sp_items)
        stats = {"sync_1_backfilled": 0, "sync_1_skipped": 0}
        sp_milestone = {"milestone_submission_triggered_at": None}
        # Patch import task to record what got called
        with patch(
            "core.src.workflow_engine.tasks.sp_alert_imports.import_deliverable_tracker_task"
        ) as mock_import:
            mock_import.apply.return_value = SimpleNamespace(
                result={"outcome": "imported", "delivery_item_id": "new-id"},
            )
            _sync_1_delivery_item_count(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_1_backfilled"] == 2  # item_no 2 and 3 missing


# ---------------------------------------------------------------------------
# sync-2 milestone-start-collection
# ---------------------------------------------------------------------------


class TestSync2StartCollection:
    def test_disabled_noop(self):
        cfg = ReconcileConfig(sync_2_start_collection=SyncTypeConfig(enabled=False))
        deps = _mk_deps()
        stats = {"sync_2_dispatched": 0, "sync_2_skipped": 0}
        _sync_2_start_collection(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", None)
        assert stats == {"sync_2_dispatched": 0, "sync_2_skipped": 0}

    def test_missing_timestamp_noop(self):
        cfg = ReconcileConfig()
        deps = _mk_deps()
        stats = {"sync_2_dispatched": 0, "sync_2_skipped": 0}
        sp_milestone = {"milestone_collection_started_at": None}
        _sync_2_start_collection(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_2_dispatched"] == 0

    def test_below_threshold_noop(self):
        # RECON-1 (2026-07-30): threshold raised 300 -> 900s. 60s still below.
        cfg = ReconcileConfig()
        deps = _mk_deps()
        stats = {"sync_2_dispatched": 0, "sync_2_skipped": 0}
        sp_milestone = {"milestone_collection_started_at": _iso_ago(60)}  # 60s < 900s
        _sync_2_start_collection(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_2_dispatched"] == 0

    def test_at_600s_still_below_new_threshold(self):
        # RECON-1: prior threshold was 300s so 600s fired; new threshold 900s
        # holds off at 600s. Validates the config bump landed.
        cfg = ReconcileConfig()
        pg_items = [_mk_item(1, "SM-1", "Open"), _mk_item(2, "SM-1", "Open")]
        deps = _mk_deps(pg_items=pg_items)
        stats = {"sync_2_dispatched": 0, "sync_2_skipped": 0}
        sp_milestone = {"milestone_collection_started_at": _iso_ago(600)}
        _sync_2_start_collection(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_2_dispatched"] == 0

    def test_partial_completion_no_dispatch(self):
        """If ANY item advanced past Open (i.e., OutreachSent+), sync-2 does
        NOT fire -- kickoff email was received, existing flow handles the rest.
        RECON-1: predicate is now 'all still in OPEN' (was NS, but D-144 auto-
        transitions NS->Open at import time so items post-setup are Open)."""
        cfg = ReconcileConfig()
        pg_items = [_mk_item(1, "SM-1", "Open"), _mk_item(2, "SM-1", "OutreachSent")]
        deps = _mk_deps(pg_items=pg_items)
        stats = {"sync_2_dispatched": 0, "sync_2_skipped": 0}
        sp_milestone = {"milestone_collection_started_at": _iso_ago(1000)}  # >900s
        _sync_2_start_collection(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_2_dispatched"] == 0

    def test_all_still_open_fires(self):
        """All items still in Open AND >15min elapsed = fire kickoff. RECON-1:
        renamed from test_all_still_ns_fires; predicate now uses OPEN state."""
        cfg = ReconcileConfig()
        pg_items = [_mk_item(1, "SM-1", "Open"), _mk_item(2, "SM-1", "Open")]
        deps = _mk_deps(pg_items=pg_items)
        stats = {"sync_2_dispatched": 0, "sync_2_skipped": 0}
        sp_milestone = {"milestone_collection_started_at": _iso_ago(1000)}  # >900s
        with patch(
            "core.src.workflow_engine.tasks.sp_alert_imports.kickoff_collection_task"
        ) as mock_kickoff:
            mock_kickoff.apply.return_value = SimpleNamespace(result={"outcome": "fired"})
            _sync_2_start_collection(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_2_dispatched"] == 1

    def test_all_still_ns_does_not_fire_post_recon1(self):
        """RECON-1: if items are somehow still in NS (import auto-transition
        didn't run yet), sync-2 does NOT fire -- 'all Open' predicate fails.
        Guards against firing on incomplete import state."""
        cfg = ReconcileConfig()
        pg_items = [_mk_item(1, "SM-1", "Not Started"), _mk_item(2, "SM-1", "Not Started")]
        deps = _mk_deps(pg_items=pg_items)
        stats = {"sync_2_dispatched": 0, "sync_2_skipped": 0}
        sp_milestone = {"milestone_collection_started_at": _iso_ago(1000)}
        _sync_2_start_collection(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_2_dispatched"] == 0


# ---------------------------------------------------------------------------
# sync-4 milestone-submit-to-carrier (mirror sync-2 pattern)
# ---------------------------------------------------------------------------


class TestSync4SubmitToCarrier:
    def test_below_threshold_noop(self):
        cfg = ReconcileConfig()
        deps = _mk_deps()
        stats = {"sync_4_dispatched": 0, "sync_4_skipped": 0}
        sp_milestone = {"milestone_submission_triggered_at": _iso_ago(60)}
        _sync_4_submit_to_carrier(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_4_dispatched"] == 0

    def test_any_submitted_stops(self):
        """If ANY item reached SubmittedToCustomer, sync-4 exits (existing flow
        handles remaining stragglers)."""
        cfg = ReconcileConfig()
        pg_items = [
            _mk_item(1, "SM-1", "ReadyForSubmission"),
            _mk_item(2, "SM-1", "SubmittedToCustomer"),  # one already through
        ]
        deps = _mk_deps(pg_items=pg_items)
        stats = {"sync_4_dispatched": 0, "sync_4_skipped": 0}
        sp_milestone = {"milestone_submission_triggered_at": _iso_ago(600)}
        _sync_4_submit_to_carrier(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_4_dispatched"] == 0

    def test_all_still_rfs_fires(self):
        cfg = ReconcileConfig()
        pg_items = [
            _mk_item(1, "SM-1", "ReadyForSubmission"),
            _mk_item(2, "SM-1", "ReadyForSubmission"),
        ]
        deps = _mk_deps(pg_items=pg_items)
        stats = {"sync_4_dispatched": 0, "sync_4_skipped": 0}
        sp_milestone = {"milestone_submission_triggered_at": _iso_ago(600)}
        with patch(
            "core.src.workflow_engine.tasks.submit_to_carrier.submit_to_carrier_task"
        ) as mock_submit:
            mock_submit.apply.return_value = SimpleNamespace(result={"outcome": "fired"})
            _sync_4_submit_to_carrier(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_4_dispatched"] == 1


# ---------------------------------------------------------------------------
# sync-5 milestone-close-all-items
# ---------------------------------------------------------------------------


class TestSync5CloseAllItems:
    def test_any_closed_stops(self):
        cfg = ReconcileConfig()
        pg_items = [
            _mk_item(1, "SM-1", "SubmittedToCustomer"),
            _mk_item(2, "SM-1", "Closed"),  # one already closed
        ]
        deps = _mk_deps(pg_items=pg_items)
        stats = {"sync_5_dispatched": 0, "sync_5_skipped": 0}
        sp_milestone = {"closed_all_items_triggered_at": _iso_ago(600)}
        _sync_5_close_all_items(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_5_dispatched"] == 0

    def test_all_still_submitted_fires(self):
        cfg = ReconcileConfig()
        pg_items = [
            _mk_item(1, "SM-1", "SubmittedToCustomer"),
            _mk_item(2, "SM-1", "SubmittedToCustomer"),
        ]
        deps = _mk_deps(pg_items=pg_items)
        stats = {"sync_5_dispatched": 0, "sync_5_skipped": 0}
        sp_milestone = {"closed_all_items_triggered_at": _iso_ago(600)}
        with patch(
            "core.src.workflow_engine.tasks.milestone.close_all_items_task"
        ) as mock_close:
            mock_close.apply.return_value = SimpleNamespace(result={"outcome": "completed"})
            _sync_5_close_all_items(deps, cfg, stats, "cid", "MMK", "SM-1", "P1", sp_milestone)
        assert stats["sync_5_dispatched"] == 1


# ---------------------------------------------------------------------------
# sync-3 deliverable-approved (per-item mirror)
# ---------------------------------------------------------------------------


class TestSync3PmApproval:
    def test_no_pg_upr_items_noop(self):
        cfg = ReconcileConfig()
        pg_items = [_mk_item(1, "SM-1", "OutreachSent")]
        deps = _mk_deps(pg_items=pg_items)
        stats = {"sync_3_dispatched": 0, "sync_3_skipped": 0}
        _sync_3_pm_approval(deps, cfg, stats, "cid", "MMK", "SM-1", "P1")
        assert stats["sync_3_dispatched"] == 0

    def test_pg_upr_and_sp_rfs_pm_approval_fires(self):
        """Per-item mirror: Postgres shows UnderPMReview, SP shows RFS +
        pm_approval_at set >5min ago -> fire apply_pm_approval with
        trigger_source='sync_backfill_pm_approval'."""
        cfg = ReconcileConfig()
        pg_items = [_mk_item(1, "SM-1", "UnderPMReview")]
        sp_items = [{
            "item_no":            1,
            "delivery_state":     "ReadyForSubmission",
            "pm_approval_at":     _iso_ago(600),
            "pm_approval_pm_id":  "pm@corp.com",
        }]
        deps = _mk_deps(pg_items=pg_items, sp_items=sp_items)
        stats = {"sync_3_dispatched": 0, "sync_3_skipped": 0}
        with patch(
            "core.src.workflow_engine.tasks.pm_approval.apply_pm_approval_task"
        ) as mock_pm:
            mock_pm.apply.return_value = SimpleNamespace(result={"outcome": "mirrored"})
            _sync_3_pm_approval(deps, cfg, stats, "cid", "MMK", "SM-1", "P1")
        assert stats["sync_3_dispatched"] == 1

    def test_sp_still_upr_no_dispatch(self):
        """SP hasn't recorded the approval yet -> reconciler exits (nothing to
        mirror). Post RECON-1: 'no pm_approval_at' = nothing to mirror."""
        cfg = ReconcileConfig()
        pg_items = [_mk_item(1, "SM-1", "UnderPMReview")]
        sp_items = [{"item_no": 1, "delivery_state": "UnderPMReview"}]  # no pm_approval_at
        deps = _mk_deps(pg_items=pg_items, sp_items=sp_items)
        stats = {"sync_3_dispatched": 0, "sync_3_skipped": 0}
        _sync_3_pm_approval(deps, cfg, stats, "cid", "MMK", "SM-1", "P1")
        assert stats["sync_3_dispatched"] == 0

    def test_pm_approval_at_only_fires_even_without_sp_state_rfs(self):
        """RECON-1 (2026-07-30): SP UI Approve button no longer writes
        delivery_state=RFS. sync-3 must fire on pm_approval_at alone;
        sp_state may still be UnderPMReview or be missing entirely."""
        cfg = ReconcileConfig()
        pg_items = [_mk_item(1, "SM-1", "UnderPMReview")]
        # NOTE: no delivery_state=RFS in SP row -- only pm_approval_at + pm_id.
        sp_items = [{
            "item_no":            1,
            "pm_approval_at":     _iso_ago(600),
            "pm_approval_pm_id":  "pm@corp.com",
            # delivery_state NOT set (or set to UnderPMReview per SP UI actual)
        }]
        deps = _mk_deps(pg_items=pg_items, sp_items=sp_items)
        stats = {"sync_3_dispatched": 0, "sync_3_skipped": 0}
        with patch(
            "core.src.workflow_engine.tasks.pm_approval.apply_pm_approval_task"
        ) as mock_pm:
            mock_pm.apply.return_value = SimpleNamespace(result={"outcome": "mirrored"})
            _sync_3_pm_approval(deps, cfg, stats, "cid", "MMK", "SM-1", "P1")
        assert stats["sync_3_dispatched"] == 1
        # Verify event_ctx does NOT carry delivery_state (SP UI didn't write it)
        # apply.call_args.kwargs = {'args': (params, event_ctx), 'throw': False}
        args_tuple = mock_pm.apply.call_args.kwargs["args"]
        _, event_ctx = args_tuple
        body_kvs = (event_ctx.get("derived_fields") or {}).get("body_kvs", {})
        assert "delivery_state" not in body_kvs
        assert body_kvs.get("pm_approval_at") is not None
        assert body_kvs.get("pm_approval_pm_id") == "pm@corp.com"


# ---------------------------------------------------------------------------
# _iter_tuples
# ---------------------------------------------------------------------------


class TestIterTuples:
    def test_yields_from_template_cache(self):
        from core.src.template_schema import template_lookup
        # Save + swap cache
        saved = dict(template_lookup._CACHE)
        template_lookup._CACHE.clear()
        template_lookup._CACHE["MMK"] = {
            "devices":    {"SM-A": {}, "SM-B": {}},
            "milestones": {
                "P1": {"devices": ["SM-A", "SM-B"]},
                "P2": {"devices": ["SM-A"]},
            },
        }
        try:
            deps = MagicMock()
            tuples = list(_iter_tuples(deps))
            assert ("MMK", "SM-A", "P1", "P1") in tuples
            assert ("MMK", "SM-B", "P1", "P1") in tuples
            assert ("MMK", "SM-A", "P2", "P2") in tuples
            assert ("MMK", "SM-B", "P2", "P2") not in tuples  # not in P2 scope
            assert len(tuples) == 3
        finally:
            template_lookup._CACHE.clear()
            template_lookup._CACHE.update(saved)

    def test_milestone_without_devices_defaults_to_all(self):
        """MMK convention 2026-07-30: milestones omit devices: list; the
        reconciler must fall back to the top-level devices dict per FR-40."""
        from core.src.template_schema import template_lookup
        saved = dict(template_lookup._CACHE)
        template_lookup._CACHE.clear()
        template_lookup._CACHE["MMK"] = {
            "devices":    {"SM-A012U": {}, "SM-A012U1": {}, "SM-M456U": {}},
            "milestones": {
                "DRR": {"work_items": []},  # no `devices:` key
            },
        }
        try:
            deps = MagicMock()
            tuples = list(_iter_tuples(deps))
            assert ("MMK", "SM-A012U",  "DRR", "DRR") in tuples
            assert ("MMK", "SM-A012U1", "DRR", "DRR") in tuples
            assert ("MMK", "SM-M456U",  "DRR", "DRR") in tuples
            assert len(tuples) == 3
        finally:
            template_lookup._CACHE.clear()
            template_lookup._CACHE.update(saved)
