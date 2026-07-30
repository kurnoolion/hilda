"""apply_tpm_sp_close_task — mirror SP-authored delivery_state='Closed'.

Ph-1 simplification per architect 2026-07-23: whenever SP CHANGED alert
carries delivery_state='Closed' (case-insensitive) in the field_deltas,
mirror unconditionally to local Postgres. No state machine, no guards.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.src.workflow_engine.tasks.tpm_sp_close import (
    apply_tpm_sp_close_task,
    apply_tpm_sp_close_in_progress_task,
)


def _make_deps():
    deps = MagicMock()
    deps.storage.update_delivery_item = MagicMock()
    deps.audit.write_communication_log = MagicMock()
    return deps


@pytest.fixture
def patched_deps(monkeypatch):
    deps = _make_deps()
    monkeypatch.setattr(
        "core.src.workflow_engine.tasks.tpm_sp_close.get_task_deps",
        lambda: deps,
    )
    return deps


class TestApplyTpmSpClose:
    def test_new_value_closed_mirrors_to_postgres(self, patched_deps):
        result = apply_tpm_sp_close_task({}, {
            "delivery_item_id": "MMK-SM-S671U1-DRR-10",
            "field_deltas": {"delivery_state": ["Open", "Closed"]},
            "correlation_id": "corr-1",
        })
        assert result["outcome"] == "synced"
        assert result["delivery_item_id"] == "MMK-SM-S671U1-DRR-10"
        assert result["prior_value"] == "Open"

        # Storage write happened with delivery_state='Closed'
        patched_deps.storage.update_delivery_item.assert_called_once()
        call = patched_deps.storage.update_delivery_item.call_args
        item_id_arg, fields_arg = call.args
        assert item_id_arg == "MMK-SM-S671U1-DRR-10"
        assert fields_arg["delivery_state"] == "Closed"
        assert "actual_completion_date" in fields_arg
        assert "last_updated" in fields_arg

        # Audit was written with correct attribution
        patched_deps.audit.write_communication_log.assert_called_once()
        audit_kwargs = patched_deps.audit.write_communication_log.call_args.kwargs
        assert audit_kwargs["action_type"] == "tpm_sp_close_synced"
        assert audit_kwargs["delivery_item_id"] == "MMK-SM-S671U1-DRR-10"
        assert audit_kwargs["attribution"]["trigger_source"] == "sp_ui_delivery_state_write"
        assert audit_kwargs["details"]["prior_value"] == "Open"
        assert audit_kwargs["details"]["new_value"] == "Closed"

    def test_from_not_started_still_works(self, patched_deps):
        """TPM early-close from NOT_STARTED per D-149."""
        result = apply_tpm_sp_close_task({}, {
            "delivery_item_id": "MMK-SM-S671U1-DRR-11",
            "field_deltas": {"delivery_state": ["Not Started", "Closed"]},
        })
        assert result["outcome"] == "synced"
        assert result["prior_value"] == "Not Started"

    def test_new_value_open_skipped(self, patched_deps):
        """Guard against stray dispatch — if new value isn't Closed, don't write."""
        result = apply_tpm_sp_close_task({}, {
            "delivery_item_id": "MMK-SM-S671U1-DRR-10",
            "field_deltas": {"delivery_state": ["Not Started", "Open"]},
        })
        assert result["outcome"] == "skipped_not_closed"
        assert result["new_value"] == "Open"
        patched_deps.storage.update_delivery_item.assert_not_called()
        patched_deps.audit.write_communication_log.assert_not_called()

    def test_case_insensitive_closed(self, patched_deps):
        """SP UI may write 'Closed' or 'closed' etc.; both should fire."""
        for val in ("Closed", "closed", "CLOSED", " Closed "):
            patched_deps.storage.update_delivery_item.reset_mock()
            result = apply_tpm_sp_close_task({}, {
                "delivery_item_id": "MMK-SM-S671U1-DRR-10",
                "field_deltas": {"delivery_state": ["Open", val]},
            })
            assert result["outcome"] == "synced", f"failed for value={val!r}"

    def test_missing_item_id_skipped(self, patched_deps):
        result = apply_tpm_sp_close_task({}, {
            "field_deltas": {"delivery_state": ["Open", "Closed"]},
        })
        assert result["outcome"] == "skipped_missing_item_id"
        patched_deps.storage.update_delivery_item.assert_not_called()

    def test_delta_as_plain_value_treated_as_new(self, patched_deps):
        """If field_deltas serialized delta as plain string (not tuple/list),
        treat it as the new value."""
        result = apply_tpm_sp_close_task({}, {
            "delivery_item_id": "MMK-SM-S671U1-DRR-10",
            "field_deltas": {"delivery_state": "Closed"},
        })
        assert result["outcome"] == "synced"
        assert result["prior_value"] is None

    def test_storage_write_failure_reported(self, patched_deps):
        patched_deps.storage.update_delivery_item.side_effect = RuntimeError("db down")
        result = apply_tpm_sp_close_task({}, {
            "delivery_item_id": "MMK-SM-S671U1-DRR-10",
            "field_deltas": {"delivery_state": ["Open", "Closed"]},
        })
        assert result["outcome"] == "storage_write_failed"
        assert "RuntimeError" in result["error"]
        # Audit NOT written when storage failed
        patched_deps.audit.write_communication_log.assert_not_called()

    def test_audit_failure_does_not_rollback_mirror(self, patched_deps):
        """Audit failure must NOT roll back the mirror write — the state change
        is already in Postgres; audit is best-effort."""
        patched_deps.audit.write_communication_log.side_effect = RuntimeError("audit down")
        result = apply_tpm_sp_close_task({}, {
            "delivery_item_id": "MMK-SM-S671U1-DRR-10",
            "field_deltas": {"delivery_state": ["Open", "Closed"]},
        })
        assert result["outcome"] == "synced"
        patched_deps.storage.update_delivery_item.assert_called_once()


class TestApplyTpmSpCloseInProgress:
    """CIP-1 (2026-07-28) — 2-hop task: SP CHANGED alert with
    delivery_state='CloseInProgress' -> mirror to Postgres -> advance to
    Closed via update_delivery_state (bypass_guards=True, trigger_source=
    manual_tpm_override). Design serializes TPM's per-item close click so
    SP UI's Start Collection button can be disabled immediately.
    """

    def test_close_in_progress_2_hop_happy_path(self, patched_deps, monkeypatch):
        # Mock update_delivery_state to succeed (returns transitioned outcome).
        from unittest.mock import MagicMock
        stub_result = MagicMock(outcome="transitioned")
        stub_update = MagicMock(return_value=stub_result)
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.tpm_sp_close.update_delivery_state",
            stub_update,
        )

        result = apply_tpm_sp_close_in_progress_task({}, {
            "delivery_item_id": "MMK-SM-S671U1-DRR-10",
            "field_deltas": {"delivery_state": ["Open", "CloseInProgress"]},
            "correlation_id": "corr-cip-1",
        })

        assert result["outcome"] == "closed"
        assert result["delivery_item_id"] == "MMK-SM-S671U1-DRR-10"
        assert result["prior_value"] == "Open"

        # Hop 1: direct storage write with CloseInProgress
        patched_deps.storage.update_delivery_item.assert_called_once()
        item_id_arg, fields_arg = patched_deps.storage.update_delivery_item.call_args.args
        assert item_id_arg == "MMK-SM-S671U1-DRR-10"
        assert fields_arg["delivery_state"] == "CloseInProgress"
        assert "last_updated" in fields_arg

        # Hop 2: update_delivery_state called with bypass + manual_tpm_override
        stub_update.assert_called_once()
        hop2_kwargs = stub_update.call_args.kwargs
        assert hop2_kwargs["delivery_item_id"] == "MMK-SM-S671U1-DRR-10"
        assert hop2_kwargs["bypass_guards"] is True
        from core.src.template_schema.enums import DeliveryState
        assert hop2_kwargs["target_state"] == DeliveryState.CLOSED
        assert hop2_kwargs["event_context"]["trigger_source"] == "manual_tpm_override"

    def test_case_insensitive_close_in_progress(self, patched_deps, monkeypatch):
        """SP UI may write any casing; task normalizes."""
        from unittest.mock import MagicMock
        stub_result = MagicMock(outcome="transitioned")
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.tpm_sp_close.update_delivery_state",
            MagicMock(return_value=stub_result),
        )

        for val in ("CloseInProgress", "closeinprogress", "CLOSEINPROGRESS", " CloseInProgress "):
            patched_deps.storage.update_delivery_item.reset_mock()
            r = apply_tpm_sp_close_in_progress_task({}, {
                "delivery_item_id": "MMK-SM-S671U1-DRR-10",
                "field_deltas": {"delivery_state": ["Open", val]},
            })
            assert r["outcome"] == "closed", f"failed for value={val!r}"

    def test_skipped_when_not_close_in_progress(self, patched_deps):
        """Guard against stray dispatch — if new value isn't CloseInProgress, don't write."""
        result = apply_tpm_sp_close_in_progress_task({}, {
            "delivery_item_id": "MMK-SM-S671U1-DRR-10",
            "field_deltas": {"delivery_state": ["Open", "Closed"]},
        })
        assert result["outcome"] == "skipped_not_close_in_progress"
        assert result["new_value"] == "Closed"
        patched_deps.storage.update_delivery_item.assert_not_called()

    def test_missing_item_id_skipped(self, patched_deps):
        result = apply_tpm_sp_close_in_progress_task({}, {
            "field_deltas": {"delivery_state": ["Open", "CloseInProgress"]},
        })
        assert result["outcome"] == "skipped_missing_item_id"
        patched_deps.storage.update_delivery_item.assert_not_called()

    def test_hop1_storage_failure_no_hop2(self, patched_deps, monkeypatch):
        """If hop 1 (mirror) fails, don't attempt hop 2 (advance)."""
        from unittest.mock import MagicMock
        patched_deps.storage.update_delivery_item.side_effect = RuntimeError("db down")
        stub_update = MagicMock()
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.tpm_sp_close.update_delivery_state",
            stub_update,
        )

        result = apply_tpm_sp_close_in_progress_task({}, {
            "delivery_item_id": "MMK-SM-S671U1-DRR-10",
            "field_deltas": {"delivery_state": ["Open", "CloseInProgress"]},
        })
        assert result["outcome"] == "hop1_storage_write_failed"
        assert "RuntimeError" in result["error"]
        stub_update.assert_not_called()   # hop 2 never fires
        patched_deps.audit.write_communication_log.assert_not_called()

    def test_hop2_advance_raise_returns_hop2_outcome(self, patched_deps, monkeypatch):
        """If hop 2 raises (e.g., SP write blows up), report + leave item at
        CloseInProgress in Postgres (reconciler sync-6 catches later)."""
        from unittest.mock import MagicMock
        stub_update = MagicMock(side_effect=RuntimeError("sp write blew up"))
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.tpm_sp_close.update_delivery_state",
            stub_update,
        )

        result = apply_tpm_sp_close_in_progress_task({}, {
            "delivery_item_id": "MMK-SM-S671U1-DRR-10",
            "field_deltas": {"delivery_state": ["Open", "CloseInProgress"]},
        })
        assert result["outcome"] == "hop2_advance_raised"
        # Hop 1 mirror DID land -- item is at CloseInProgress in Postgres
        patched_deps.storage.update_delivery_item.assert_called_once()

    def test_skip_when_postgres_already_closed(self, patched_deps, monkeypatch):
        """RECON-1 (2026-07-30): sync-6 reconciler may have force-advanced
        this item to CLOSED before the SP CHANGED alert arrives. In that
        case, apply_tpm_sp_close_in_progress must skip BOTH hops -- otherwise
        it re-mirrors CloseInProgress on top of CLOSED then re-advances,
        producing a CLOSED->CloseInProgress->CLOSED audit trail (correct
        final state, noisy intermediate).
        """
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        # Fresh get returns an item already CLOSED
        patched_deps.storage.get_delivery_item = MagicMock(
            return_value=SimpleNamespace(delivery_state="Closed"),
        )
        stub_update = MagicMock()
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.tpm_sp_close.update_delivery_state",
            stub_update,
        )

        result = apply_tpm_sp_close_in_progress_task({}, {
            "delivery_item_id": "MMK-SM-S671U1-DRR-10",
            "field_deltas": {"delivery_state": ["Open", "CloseInProgress"]},
            "correlation_id": "corr-late-alert",
        })

        assert result["outcome"] == "skipped_already_closed"
        # Neither hop ran
        patched_deps.storage.update_delivery_item.assert_not_called()
        stub_update.assert_not_called()

    def test_hop2_guard_denial_reports_outcome(self, patched_deps, monkeypatch):
        """If hop 2 returns a non-success outcome (e.g., guard_denied even
        with bypass_guards=True somehow), surface it in the return."""
        from unittest.mock import MagicMock
        stub_result = MagicMock(outcome="guard_denied")
        monkeypatch.setattr(
            "core.src.workflow_engine.tasks.tpm_sp_close.update_delivery_state",
            MagicMock(return_value=stub_result),
        )

        result = apply_tpm_sp_close_in_progress_task({}, {
            "delivery_item_id": "MMK-SM-S671U1-DRR-10",
            "field_deltas": {"delivery_state": ["Open", "CloseInProgress"]},
        })
        assert result["outcome"] == "hop2_guard_denied"
