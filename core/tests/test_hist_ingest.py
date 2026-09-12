"""HIST-INGEST-1 (2026-09-12) tests -- the "document_received" audit row
must be written on ingest (email/PLM/NSD) so the /browse/history/{token}
timeline shows the first "how did this file get here?" event, not just
subsequent view/edit/save/download events.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest


class TestActionTypeFilter:
    def test_document_received_is_in_query_filter(self):
        """list_document_events must include the new action_type."""
        from core.src.storage.document_view_ops import (
            _DOCUMENT_VIEW_ACTION_TYPES,
        )
        assert "document_received" in _DOCUMENT_VIEW_ACTION_TYPES

    def test_all_dashboard_action_types_covered(self):
        """The filter tuple must include every action_type the write path
        emits -- otherwise the /browse/history render omits rows silently."""
        from core.src.storage.document_view_ops import (
            _DOCUMENT_VIEW_ACTION_TYPES,
        )
        # Written from dashboard.document_view_routes._audit calls
        for action in [
            "document_viewed",
            "document_edit_opened",
            "document_saved",
            "document_downloaded",
            "document_edit_blocked_drm",
        ]:
            assert action in _DOCUMENT_VIEW_ACTION_TYPES, (
                f"{action!r} missing from filter -- history will drop it"
            )
        # Written from ingest paths (this fix)
        assert "document_received" in _DOCUMENT_VIEW_ACTION_TYPES


@pytest.mark.asyncio
async def test_write_matches_to_view_tree_audits_received(monkeypatch):
    """The email/PLM ingest path must write ONE document_received audit
    row per view_relative_path returned by write_attachment_to_view_tree,
    keyed on that path so /browse/history finds it."""
    from core.src.workflow_engine.tasks import inbound_attachment as ia

    # Fake write_attachment_to_view_tree to return two paths (simulates
    # an archive with two extracted entries).
    async def _fake_write(**kwargs):
        return [
            "MMK/SM-S671U1/P1/MNO-ETM/inner1.pdf",
            "MMK/SM-S671U1/P1/MNO-ETM/inner2.pdf",
        ]
    monkeypatch.setattr(
        "core.src.storage.write_attachment_to_view_tree", _fake_write,
    )

    audit_calls: list[dict] = []
    audit = MagicMock()
    def _capture(**kwargs):
        audit_calls.append(kwargs)
    audit.write_communication_log.side_effect = _capture
    deps = SimpleNamespace(audit=audit)

    attachment = SimpleNamespace(
        content=b"pdf-bytes", filename="bundle.zip",
    )
    matched = {"item-A"}
    candidates = [{
        "item_id":      "item-A",
        "customer_id":  "MMK",
        "device_id":    "SM-S671U1",
        "milestone_id": "P1",
        "tg_name":      "MNO-ETM",
        "item_type":    "test_tech_waiver_report",
    }]

    await ia._write_matches_to_view_tree(
        deps=deps, attachment=attachment,
        matched_item_ids=matched, candidate_items=candidates,
        correlation_id="corr-xyz", ingest_source="email",
    )

    # One audit row per returned view_relative_path.
    assert len(audit_calls) == 2
    for call, expected_path in zip(audit_calls, [
        "MMK/SM-S671U1/P1/MNO-ETM/inner1.pdf",
        "MMK/SM-S671U1/P1/MNO-ETM/inner2.pdf",
    ]):
        assert call["action_type"] == "document_received"
        # attribution.correlation_id is what becomes external_message_id
        # in the DB row -- must equal the view_relative_path so the
        # history query WHERE clause matches.
        assert call["attribution"]["correlation_id"] == expected_path
        assert call["details"]["view_relative_path"] == expected_path
        assert call["details"]["ingest_source"] == "email"


@pytest.mark.asyncio
async def test_write_matches_to_view_tree_no_paths_no_audit(monkeypatch):
    """When write_attachment_to_view_tree returns [] (Default WI, empty
    tg_name, etc.), no audit rows should be written -- the file isn't in
    the view tree, so a history event would be a phantom."""
    from core.src.workflow_engine.tasks import inbound_attachment as ia

    async def _fake_write(**kwargs):
        return []
    monkeypatch.setattr(
        "core.src.storage.write_attachment_to_view_tree", _fake_write,
    )

    audit = MagicMock()
    deps = SimpleNamespace(audit=audit)
    attachment = SimpleNamespace(content=b"x", filename="x.pdf")
    candidates = [{
        "item_id": "item-B", "customer_id": "MMK",
        "device_id": "D", "milestone_id": "P1",
        "tg_name": "", "item_type": "test_tech_waiver_report",
    }]

    await ia._write_matches_to_view_tree(
        deps=deps, attachment=attachment,
        matched_item_ids={"item-B"}, candidate_items=candidates,
        correlation_id="c", ingest_source="email",
    )
    audit.write_communication_log.assert_not_called()
