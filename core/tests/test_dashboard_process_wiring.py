"""DRRP1-API-1: caches the dashboard process must load for itself.

Several caches in this codebase are PROCESS-LOCAL and eager, populated by
`workflow_engine.bootstrap.bootstrap_task_deps`. That function runs in
hilda-worker and hilda-beat via celery_app's init hook. hilda-api does NOT
run it -- it starts as `uvicorn core.src.dashboard.app:build_app` -- so any
cache the dashboard reads has to be loaded by `build_app` itself.

`milestone_item_mapping` was not, and the failure was entirely silent:
`get_target_item_no` returns None on a cache miss exactly as it does for a
carrier with no mapping file, so every caller in the API took the
"unmapped" branch. Two features degraded without an error anywhere:

  * DRRP1-DEST-1's migrated carrier destination never resolved, so a DRR
    page showed "work item is marked no_customer_upload" on every row.
  * build_milestone_manifest omitted every migrated document, so the
    submission preview and download-all zip under-reported what
    submit_to_carrier actually uploads -- that runs in the worker, where the
    cache IS loaded. A preview that disagrees with the submission is worse
    than no preview at all.

Found live on 2026-09-08 after DRRP1-DEST-1 shipped and changed nothing in
the UI.
"""
from __future__ import annotations

import pytest

from core.src.dashboard.app import build_app
from core.src.dashboard.config import DashboardConfig
from core.src.template_schema import milestone_item_mapping as mim


def _app():
    return build_app(DashboardConfig(url_prefix="", mock_auth=True))


def test_build_app_loads_the_milestone_mapping(monkeypatch):
    """Spied rather than asserted on the real cache, so this test cannot
    pollute the module-level cache other tests share."""
    calls: list[object] = []

    def _spy(*a, **k):
        calls.append((a, k))
        return {"MMK": True}

    monkeypatch.setattr(mim, "load_all_mappings", _spy)
    _app()
    assert calls, (
        "build_app must load milestone_item_mapping -- hilda-api never runs "
        "bootstrap_task_deps, so nothing else will, and every mapping lookup "
        "silently returns None"
    )


def test_a_mapping_failure_does_not_stop_the_app(monkeypatch):
    """Best-effort by design: a carrier with no mapping file is the normal
    case, and a broken one must degrade the destination column rather than
    take the dashboard down."""
    def _boom(*a, **k):
        raise RuntimeError("simulated mapping load failure")

    monkeypatch.setattr(mim, "load_all_mappings", _boom)
    assert _app() is not None


@pytest.mark.skipif(
    not (mim._default_base_dir() / "MMK" / mim.MAPPING_FILENAME).is_file(),
    reason=(
        "MMK milestone_item_mapping.yaml absent -- carrier config is not "
        "published to the public mirror. The two tests above cover the wiring "
        "itself with a spy and need no carrier data; only this behavioural "
        "check does."
    ),
)
def test_mapping_lookups_resolve_in_a_dashboard_process():
    """The behavioural half: after build_app, the pairs the DRR view depends
    on actually resolve. MMK DRR #35 and #60 both feed P1 #23 (MNO-IOT), and
    DRR #5 feeds P1 #30 (MNO-UX) -- the two scopes where the empty cache was
    observed."""
    _app()
    assert mim.get_target_item_no(
        customer_id="MMK", source_milestone="DRR", source_item_no=35,
    ) == ("P1", 23)
    assert mim.get_target_item_no(
        customer_id="MMK", source_milestone="DRR", source_item_no=5,
    ) == ("P1", 30)
