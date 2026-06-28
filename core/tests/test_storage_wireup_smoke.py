"""End-to-end smoke test for the storage-wireup strand (Chunks 1-5).

Validates the full chain works in-process with sqlite:
  bootstrap -> PostgresStorage + PostgresAuditWriter + dispatcher
  -> import_deliverable_tracker_task creates real DB row
  -> kickoff_collection_task reads the row + dispatches ItemCreated

This is an integration test (in-process) -- the real-Postgres-container
test belongs in deployment integration suite (testcontainers or live
container fixtures), not this Python unit test suite.
"""
from __future__ import annotations

import pytest

from core.src.workflow_engine.bootstrap import bootstrap_task_deps
from core.src.workflow_engine.task_deps import get_task_deps

pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning",
)


def _restore_task_deps_to_none() -> None:
    import core.src.workflow_engine.task_deps as _td
    _td._deps = None


def test_full_chain_import_then_kickoff(tmp_path, monkeypatch):
    """Validates the full storage-wireup chain end-to-end:
      1. Bootstrap wires storage + audit + dispatcher
      2. import_deliverable_tracker_task creates a DB row
      3. kickoff_collection_task reads the milestone, dispatches ItemCreated
    """
    _restore_task_deps_to_none()
    db_path = tmp_path / "smoke.db"
    monkeypatch.setenv("HILDA_STORAGE_DB_URL", f"sqlite+aiosqlite:///{db_path}")

    # Use a minimal valid rules YAML so dispatcher gets wired alongside storage
    (tmp_path / "minimal.yaml").write_text("rules: []\n")

    result = bootstrap_task_deps(rules_dir=tmp_path)
    assert result.storage_wired is True, f"storage not wired: {result.warnings}"
    assert result.audit_wired is True
    # Dispatcher requires rule_engine; rule_engine may fail to load if loader
    # is strict about empty/minimal YAML -- record but don't fail the smoke
    # test on that. Storage chain is the primary subject.

    deps = get_task_deps()
    assert deps.storage is not None

    # ---- Phase 1: import_deliverable_tracker_task creates real DB row ----
    from core.src.workflow_engine.tasks.sp_alert_imports import (
        import_deliverable_tracker_task,
    )
    body_kvs = {
        "Title":             "Device Readiness Review",
        "project_model":     "SM-S671U1",
        "milestone_name":    "P1",
        "item_no":           "5",
        "item_type":         "test_tech_waiver_report",
        "delivery_state":    "Not Started",
        "owner_name":        "Test Owner",
        "owner_corp_email":  "owner@corp.example",
        "owner_corp_usa_email": "owner.usa@corp.example",
        "owner_corp_id":     "owner-id-1",
        "tg_name":           "MNO-ETM",
        "tracking_modality": "Email",
        "force_tracking_enabled": "Yes",
        "no_customer_upload":     "No",
        "review_required":        "No",
        "milestone_gating":       "Yes",
        "doc_count":              "1",
        "sort_order":             "5",
    }
    event_context = {
        "correlation_id": "smoke-corr-001",
        "customer_id":    "MMK",
        "milestone_id":   "P1",
        "sub_trigger":    "added",
        "derived_fields": {
            "action_type": "added",
            "list_name":   "Deliverables",
            "item_title":  "Device Readiness Review",
            "body_kvs":    body_kvs,
            "routing_key": {
                "project_id":     "2350",
                "milestone_name": "P1",
                "item_number":    5,
                "list_suffix":    "MMK",
            },
        },
    }
    import_result = import_deliverable_tracker_task({}, event_context)
    assert import_result["outcome"] == "imported"

    # Verify row landed in storage
    imported_id = import_result["delivery_item_id"]
    stored_item = deps.storage.get_delivery_item(imported_id)
    assert stored_item is not None
    assert stored_item.item_no == 5
    assert stored_item.force_tracking_enabled is True
    assert stored_item.item_type == "test_tech_waiver_report"
    assert stored_item.tg_name == "MNO-ETM"

    # ---- Phase 2: kickoff_collection_task reads + sends batch outreach ----
    # Architect Step 5 Phase A 2026-06-28 restructure: kickoff no longer
    # dispatches ItemCreated events -- it groups eligible items by owner,
    # sends one batch email per owner, and transitions each item Not Started ->
    # Open -> Outreach Sent inline. Stub the two SP/email helpers so this
    # in-process smoke test doesn't need a live SP or EWS endpoint.
    import core.src.workflow_engine.tasks.sp_alert_imports as kc_mod

    owner_map = {
        imported_id: {
            "owner_corp_usa_email": "owner.usa@corp.example",
            "owner_corp_email":     "owner@corp.example",
            "owner_name":           "Test Owner",
        }
    }
    monkeypatch.setattr(
        kc_mod, "_resolve_owners_for_eligible",
        lambda deps, customer_id, milestone_id, eligible: owner_map,
    )
    sent_batches: list[dict] = []
    def _fake_send(*, deps, owner_identity, items, batch_id, recipient):
        sent_batches.append({
            "recipient": recipient, "batch_id": batch_id, "n_items": len(items),
        })
        return "SMOKE-MID-1"
    monkeypatch.setattr(
        "core.src.workflow_engine.tasks.outreach._send_batch_outreach_email",
        _fake_send,
    )

    from core.src.workflow_engine.task_deps import TaskDeps, override_task_deps

    deps_for_kickoff = TaskDeps(
        storage=deps.storage,
        sp_writer=deps.sp_writer,
        audit=deps.audit,
        email_sender=object(),  # opaque non-None sentinel; _send is stubbed
    )
    from core.src.workflow_engine.tasks.sp_alert_imports import kickoff_collection_task
    with override_task_deps(deps_for_kickoff):
        kickoff_result = kickoff_collection_task(
            {}, {
                "correlation_id": "smoke-kick-001",
                "customer_id":    "MMK",
                "milestone_id":   "P1",
            },
        )

    assert kickoff_result["outcome"] == "fired"
    assert kickoff_result["items_eligible"] == 1
    assert kickoff_result["owner_groups"] == 1
    assert kickoff_result["emails_sent"] == 1
    assert kickoff_result["items_transitioned"] == 1
    # One batch email recorded, addressed to the imported tracker's owner.
    assert len(sent_batches) == 1
    assert sent_batches[0]["recipient"] == "owner.usa@corp.example"
    assert sent_batches[0]["n_items"] == 1

    _restore_task_deps_to_none()
