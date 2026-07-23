"""apply_tpm_sp_close_task — mirror SP-authored delivery_state='Closed'.

Ph-1 simplification per architect 2026-07-23: whenever SP CHANGED alert
carries delivery_state='Closed' (case-insensitive) in the field_deltas,
mirror unconditionally to local Postgres. No state machine, no guards.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.src.workflow_engine.tasks.tpm_sp_close import apply_tpm_sp_close_task


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
