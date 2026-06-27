"""Tests for core/src/storage/audit_writer_impl.py."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning",
)

from core.src.storage.audit_ops import query_communications
from core.src.storage.audit_writer_impl import PostgresAuditWriter
from core.src.storage.db import configure_engine, init_db


@pytest.fixture(autouse=True)
async def _fresh_db(tmp_path):
    db_file = tmp_path / "test_audit.db"
    engine = configure_engine(f"sqlite+aiosqlite:///{db_file}")
    await init_db()
    yield
    await engine.dispose()


def test_postgres_audit_writer_protocol_surface():
    """Conforms to tracker.AuditWriter Protocol."""
    w = PostgresAuditWriter()
    assert hasattr(w, "write_communication_log")


async def test_audit_writer_persists_row():
    """write_communication_log writes a CommunicationLog row queryable via
    audit_ops.query_communications."""
    w = PostgresAuditWriter()
    w.write_communication_log(
        action_type="send_initial_outreach",
        delivery_item_id="I-1234",
        attribution={
            "correlation_id": "corr-001",
            "modified_by":    "system",
        },
        details={
            "template":   "initial_outreach_per_owner",
            "channel":    "email",
            "recipient":  "owner@corp.example",
            "milestone_id": "M-1",
        },
    )
    rows = await query_communications(delivery_item_id="I-1234")
    assert len(rows) == 1
    assert rows[0].action_type == "send_initial_outreach"
    assert rows[0].delivery_item_id == "I-1234"
    assert rows[0].external_message_id == "corr-001"   # correlation_id mapping
    # Summary carries JSON of attribution + details (compact)
    assert "initial_outreach_per_owner" in rows[0].summary
    assert "owner@corp.example" in rows[0].summary


async def test_audit_writer_handles_none_delivery_item_id():
    """Some events (collection_kickoff_dispatched) have no item id."""
    w = PostgresAuditWriter()
    w.write_communication_log(
        action_type="collection_kickoff_dispatched",
        delivery_item_id=None,
        attribution={"correlation_id": "kick-001"},
        details={"customer_id": "MMK", "events_fired": "3"},
    )
    # No item filter -> query by action_type
    rows = await query_communications(action_type="collection_kickoff_dispatched")
    assert len(rows) == 1
    assert rows[0].delivery_item_id is None
