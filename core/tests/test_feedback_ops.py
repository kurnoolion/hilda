"""test_feedback_ops.py -- unit tests for storage.feedback_ops.

Uses the same per-test sqlite+aiosqlite pattern as test_storage_delivery_item.py.
Covers create (with + without attachment), list ordering, get by pk, per-scope
seq_in_scope monotonicity, and cross-scope isolation.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.filterwarnings(
    "ignore::pytest.PytestUnraisableExceptionWarning",
)

from core.src.storage.db import configure_engine, init_db
from core.src.storage.feedback_ops import (
    FeedbackStorage,
    create_feedback_ticket,
    get_ticket_by_pk,
    list_tickets_for_scope,
)


@pytest.fixture(autouse=True)
async def _fresh_db(tmp_path):
    db_file = tmp_path / "test_feedback.db"
    engine = configure_engine(f"sqlite+aiosqlite:///{db_file}")
    await init_db()
    yield
    await engine.dispose()


class TestCreateFeedbackTicket:
    @pytest.mark.asyncio
    async def test_creates_first_ticket_seq_1(self):
        row = await create_feedback_ticket(
            customer_id="MMK",
            device_id="SM-A012U",
            milestone_id="DRR",
            category="bug",
            bug_type="SETUP-setup button broken",
            description="Clicked and nothing happened",
        )
        assert row.ticket_pk is not None
        assert row.seq_in_scope == 1
        assert row.ticket_id == "MMK-SM-A012U-DRR-1"
        assert row.status == "open"
        assert row.description == "Clicked and nothing happened"
        assert row.attachment_bytes is None

    @pytest.mark.asyncio
    async def test_second_ticket_same_scope_seq_2(self):
        await create_feedback_ticket(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
            category="bug", bug_type="SETUP-x", description="first",
        )
        row2 = await create_feedback_ticket(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
            category="bug", bug_type="APPROVE-y", description="second",
        )
        assert row2.seq_in_scope == 2
        assert row2.ticket_id == "MMK-SM-A012U-DRR-2"

    @pytest.mark.asyncio
    async def test_different_scope_starts_at_seq_1(self):
        await create_feedback_ticket(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
            category="bug", bug_type="SETUP-x", description="scope A",
        )
        # Different device -> independent seq.
        row_b = await create_feedback_ticket(
            customer_id="MMK", device_id="SM-M456U", milestone_id="DRR",
            category="bug", bug_type="SETUP-x", description="scope B",
        )
        assert row_b.seq_in_scope == 1
        assert row_b.ticket_id == "MMK-SM-M456U-DRR-1"

    @pytest.mark.asyncio
    async def test_stores_attachment_bytes_and_metadata(self):
        content = b"fake png bytes " * 100  # ~1.5 KB
        row = await create_feedback_ticket(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
            category="bug", bug_type="OTHER-OTHER", description="see attached",
            attachment_filename="screenshot.png",
            attachment_content_type="image/png",
            attachment_bytes=content,
        )
        assert row.attachment_filename == "screenshot.png"
        assert row.attachment_content_type == "image/png"
        assert row.attachment_bytes == content
        assert row.attachment_size == len(content)

    @pytest.mark.asyncio
    async def test_improvement_category_accepted(self):
        row = await create_feedback_ticket(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
            category="improvement",
            bug_type="OTHER-OTHER",
            description="Would be nice if the button turned green after click",
        )
        assert row.category == "improvement"
        assert row.bug_type == "OTHER-OTHER"

    @pytest.mark.asyncio
    async def test_null_description_persisted_as_none(self):
        row = await create_feedback_ticket(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
            category="bug", bug_type="SETUP-x", description=None,
        )
        assert row.description is None


class TestListTicketsForScope:
    @pytest.mark.asyncio
    async def test_empty_scope_returns_empty(self):
        rows = await list_tickets_for_scope(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
        )
        assert rows == []

    @pytest.mark.asyncio
    async def test_returns_only_matching_scope(self):
        await create_feedback_ticket(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
            category="bug", bug_type="SETUP-x", description="A",
        )
        await create_feedback_ticket(
            customer_id="MMK", device_id="SM-M456U", milestone_id="DRR",
            category="bug", bug_type="SETUP-x", description="B",
        )
        rows = await list_tickets_for_scope(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
        )
        assert len(rows) == 1
        assert rows[0].description == "A"

    @pytest.mark.asyncio
    async def test_ordered_newest_first(self):
        await create_feedback_ticket(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
            category="bug", bug_type="SETUP-x", description="oldest",
        )
        await create_feedback_ticket(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
            category="bug", bug_type="APPROVE-y", description="middle",
        )
        await create_feedback_ticket(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
            category="bug", bug_type="CLOSE-ITEM-z", description="newest",
        )
        rows = await list_tickets_for_scope(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
        )
        assert [r.seq_in_scope for r in rows] == [3, 2, 1]


class TestGetTicketByPk:
    @pytest.mark.asyncio
    async def test_returns_row_with_bytes(self):
        content = b"PDF-1.4 hello"
        created = await create_feedback_ticket(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
            category="bug", bug_type="OTHER-OTHER", description=None,
            attachment_filename="report.pdf",
            attachment_content_type="application/pdf",
            attachment_bytes=content,
        )
        fetched = await get_ticket_by_pk(created.ticket_pk)
        assert fetched is not None
        assert fetched.ticket_id == created.ticket_id
        assert fetched.attachment_bytes == content

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self):
        assert await get_ticket_by_pk(99999) is None


class TestFeedbackStorageSyncWrapper:
    """Mirror the delivery_item_ops.PostgresStorage sync-facing pattern."""

    def test_sync_create_and_list(self):
        storage = FeedbackStorage()
        row = storage.create_ticket(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
            category="bug",
            bug_type="SETUP-setup button broken",
            description="via sync wrapper",
        )
        assert row.ticket_id == "MMK-SM-A012U-DRR-1"
        listed = storage.list_tickets(
            customer_id="MMK", device_id="SM-A012U", milestone_id="DRR",
        )
        assert len(listed) == 1
        assert listed[0].ticket_pk == row.ticket_pk

    def test_sync_get_missing_returns_none(self):
        storage = FeedbackStorage()
        assert storage.get_ticket(12345) is None
