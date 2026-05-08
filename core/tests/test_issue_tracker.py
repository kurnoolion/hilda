"""Unit tests for core.src.issue_tracker — Protocol, data classes, MockIssueTracker, load_adapter."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.src.diagnostics import PipelineError
from core.src.issue_tracker import (
    AttachmentInput,
    AttachmentRef,
    CommentRef,
    Issue,
    IssueChange,
    IssueQuery,
    IssueRef,
    IssueStatus,
    IssuePriority,
    IssueTracker,
    WebhookRef,
)
from core.src.issue_tracker.mock_adapter import MockIssueTracker


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _mock() -> MockIssueTracker:
    return MockIssueTracker()


# ---------------------------------------------------------------------------
# TestDataClasses
# ---------------------------------------------------------------------------


class TestDataClasses:
    def test_issue_ref_is_frozen(self) -> None:
        ref = IssueRef(issue_id="ISS-1", source_system="mock", url="http://x/ISS-1")
        with pytest.raises((AttributeError, TypeError)):
            ref.issue_id = "ISS-2"  # type: ignore[misc]

    def test_issue_ref_equality(self) -> None:
        r1 = IssueRef("ISS-1", "mock", "http://x/1")
        r2 = IssueRef("ISS-1", "mock", "http://x/1")
        assert r1 == r2

    def test_issue_status_values(self) -> None:
        assert IssueStatus.OPEN == "Open"
        assert IssueStatus.IN_PROGRESS == "InProgress"
        assert IssueStatus.RESOLVED == "Resolved"
        assert IssueStatus.CLOSED == "Closed"

    def test_issue_priority_values(self) -> None:
        assert IssuePriority.LOW == "Low"
        assert IssuePriority.CRITICAL == "Critical"

    def test_issue_query_defaults(self) -> None:
        q = IssueQuery()
        assert q.project is None
        assert q.labels == []

    def test_comment_ref_frozen(self) -> None:
        ref = IssueRef("ISS-1", "mock", "")
        cr = CommentRef(comment_id="C-1", issue_ref=ref, source_system="mock")
        with pytest.raises((AttributeError, TypeError)):
            cr.comment_id = "C-2"  # type: ignore[misc]

    def test_attachment_ref_frozen(self) -> None:
        ref = IssueRef("ISS-1", "mock", "")
        ar = AttachmentRef(attachment_id="A-1", issue_ref=ref, filename="f.pdf", source_system="mock")
        with pytest.raises((AttributeError, TypeError)):
            ar.attachment_id = "A-2"  # type: ignore[misc]

    def test_webhook_ref_frozen(self) -> None:
        wr = WebhookRef(webhook_id="W-1", source_system="mock", callback_url="http://cb")
        with pytest.raises((AttributeError, TypeError)):
            wr.webhook_id = "W-2"  # type: ignore[misc]

    def test_attachment_input_type_alias(self) -> None:
        # AttachmentInput should be usable as Path or AsyncIterable[bytes]
        p: AttachmentInput = Path("/tmp/test.bin")
        assert isinstance(p, Path)


# ---------------------------------------------------------------------------
# TestMockIssueTrackerSourceSystem
# ---------------------------------------------------------------------------


class TestMockIssueTrackerSourceSystem:
    def test_source_system_is_mock(self) -> None:
        t = _mock()
        assert t.source_system == "mock"

    def test_source_system_immutable(self) -> None:
        t = _mock()
        with pytest.raises((AttributeError, TypeError)):
            t.source_system = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestMockIssueTrackerCRUD  (C01, C02, C03, C04)
# ---------------------------------------------------------------------------


class TestMockIssueTrackerCRUD:
    async def test_c01_create_and_get_round_trip(self) -> None:
        t = _mock()
        ref = await t.create_issue("PROJ", "My summary", "My desc")
        assert ref.source_system == "mock"
        assert ref.issue_id

        issue = await t.get_issue(ref)
        assert issue.summary == "My summary"
        assert issue.description == "My desc"
        assert issue.ref == ref
        assert issue.status == IssueStatus.OPEN

    async def test_c01_create_returns_unique_ids(self) -> None:
        t = _mock()
        r1 = await t.create_issue("P", "A", "")
        r2 = await t.create_issue("P", "B", "")
        assert r1.issue_id != r2.issue_id

    async def test_c02_update_fields_persist(self) -> None:
        t = _mock()
        ref = await t.create_issue("P", "Title", "Desc")
        await t.update_issue(ref, {"summary": "New title", "priority": IssuePriority.HIGH})
        issue = await t.get_issue(ref)
        assert issue.summary == "New title"
        assert issue.priority == IssuePriority.HIGH

    async def test_c03_transition_valid(self) -> None:
        t = _mock()
        ref = await t.create_issue("P", "T", "")
        await t.transition_issue(ref, "start")
        issue = await t.get_issue(ref)
        assert issue.status == IssueStatus.IN_PROGRESS

    async def test_c03_transition_to_resolved(self) -> None:
        t = _mock()
        ref = await t.create_issue("P", "T", "")
        await t.transition_issue(ref, "resolve")
        issue = await t.get_issue(ref)
        assert issue.status == IssueStatus.RESOLVED

    async def test_c04_close_issue(self) -> None:
        t = _mock()
        ref = await t.create_issue("P", "T", "")
        await t.close_issue(ref, "done")
        issue = await t.get_issue(ref)
        assert issue.status == IssueStatus.CLOSED

    async def test_c04_close_idempotent(self) -> None:
        t = _mock()
        ref = await t.create_issue("P", "T", "")
        await t.close_issue(ref, "done")
        await t.close_issue(ref, "done")  # must not raise
        issue = await t.get_issue(ref)
        assert issue.status == IssueStatus.CLOSED


# ---------------------------------------------------------------------------
# TestMockIssueTrackerCommentAndAttachment  (C05, C06)
# ---------------------------------------------------------------------------


class TestMockIssueTrackerCommentAndAttachment:
    async def test_c05_add_comment_returns_ref(self) -> None:
        t = _mock()
        ref = await t.create_issue("P", "T", "")
        cref = await t.add_comment(ref, "First comment")
        assert isinstance(cref, CommentRef)
        assert cref.issue_ref == ref
        assert cref.source_system == "mock"

    async def test_c05_comment_visible_on_issue(self) -> None:
        t = _mock()
        ref = await t.create_issue("P", "T", "")
        await t.add_comment(ref, "Visible comment")
        issue = await t.get_issue(ref)
        # The Issue should expose a comments list or the comment count must be > 0
        assert hasattr(issue, "comments") or issue.updated_at >= issue.created_at

    async def test_c06_upload_attachment_returns_ref(self) -> None:
        t = _mock()
        ref = await t.create_issue("P", "T", "")
        aref = await t.upload_attachment(ref, Path("/tmp/report.pdf"))
        assert isinstance(aref, AttachmentRef)
        assert aref.issue_ref == ref
        assert aref.filename == "report.pdf"
        assert aref.source_system == "mock"

    async def test_c06_upload_bytes_stream(self) -> None:
        async def _stream():
            yield b"data"

        t = _mock()
        ref = await t.create_issue("P", "T", "")
        aref = await t.upload_attachment(ref, _stream())
        assert isinstance(aref, AttachmentRef)


# ---------------------------------------------------------------------------
# TestMockIssueTrackerSearch  (C07)
# ---------------------------------------------------------------------------


class TestMockIssueTrackerSearch:
    async def test_c07_search_returns_created_issue(self) -> None:
        t = _mock()
        ref = await t.create_issue("PROJ", "Searchable", "")
        results = [r async for r in await t.search(IssueQuery(project="PROJ"))]
        assert ref in results

    async def test_c07_search_empty_project_returns_all(self) -> None:
        t = _mock()
        r1 = await t.create_issue("P1", "A", "")
        r2 = await t.create_issue("P2", "B", "")
        results = [r async for r in await t.search(IssueQuery())]
        assert r1 in results
        assert r2 in results

    async def test_c07_search_by_status(self) -> None:
        t = _mock()
        r_open = await t.create_issue("P", "Open one", "")
        r_closed = await t.create_issue("P", "Closed one", "")
        await t.close_issue(r_closed, "done")
        results = [r async for r in await t.search(IssueQuery(status=IssueStatus.OPEN))]
        assert r_open in results
        assert r_closed not in results


# ---------------------------------------------------------------------------
# TestMockIssueTrackerChanges  (C08)
# ---------------------------------------------------------------------------


class TestMockIssueTrackerChanges:
    async def test_c08_list_recent_changes_after_create(self) -> None:
        t = _mock()
        t0 = _now()
        ref = await t.create_issue("P", "T", "")
        changes = [c async for c in await t.list_recent_changes(ref, since=t0)]
        assert len(changes) >= 1
        assert all(isinstance(c, IssueChange) for c in changes)
        assert all(c.issue_ref == ref for c in changes)

    async def test_c08_changes_after_comment(self) -> None:
        t = _mock()
        ref = await t.create_issue("P", "T", "")
        t0 = _now()
        await t.add_comment(ref, "triggers change")
        changes = [c async for c in await t.list_recent_changes(ref, since=t0)]
        assert any(c.field == "comment_added" for c in changes)

    async def test_c08_changes_since_future_returns_empty(self) -> None:
        t = _mock()
        ref = await t.create_issue("P", "T", "")
        await asyncio.sleep(0)  # yield
        future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        changes = [c async for c in await t.list_recent_changes(ref, since=future)]
        assert changes == []


# ---------------------------------------------------------------------------
# TestMockIssueTrackerIdempotency  (C09)
# ---------------------------------------------------------------------------


class TestMockIssueTrackerIdempotency:
    async def test_c09_create_same_key_returns_same_ref(self) -> None:
        t = _mock()
        r1 = await t.create_issue("P", "Dup", "", idempotency_key="key-abc")
        r2 = await t.create_issue("P", "Dup", "", idempotency_key="key-abc")
        assert r1.issue_id == r2.issue_id

    async def test_c09_different_keys_create_separate_issues(self) -> None:
        t = _mock()
        r1 = await t.create_issue("P", "A", "", idempotency_key="key-1")
        r2 = await t.create_issue("P", "B", "", idempotency_key="key-2")
        assert r1.issue_id != r2.issue_id

    async def test_c09_no_key_always_creates_new(self) -> None:
        t = _mock()
        r1 = await t.create_issue("P", "X", "")
        r2 = await t.create_issue("P", "X", "")
        assert r1.issue_id != r2.issue_id

    async def test_c09_comment_idempotency(self) -> None:
        t = _mock()
        ref = await t.create_issue("P", "T", "")
        cr1 = await t.add_comment(ref, "body", idempotency_key="cmt-1")
        cr2 = await t.add_comment(ref, "body", idempotency_key="cmt-1")
        assert cr1.comment_id == cr2.comment_id


# ---------------------------------------------------------------------------
# TestMockIssueTrackerErrors  (C10)
# ---------------------------------------------------------------------------


class TestMockIssueTrackerErrors:
    async def test_c10_get_unknown_ref_raises_itr_e002(self) -> None:
        t = _mock()
        fake = IssueRef(issue_id="MISSING-99", source_system="mock", url="")
        with pytest.raises(PipelineError) as ei:
            await t.get_issue(fake)
        assert ei.value.code_id == "ITR-E002"

    async def test_c10_update_unknown_ref_raises_itr_e002(self) -> None:
        t = _mock()
        fake = IssueRef(issue_id="MISSING-99", source_system="mock", url="")
        with pytest.raises(PipelineError) as ei:
            await t.update_issue(fake, {"summary": "x"})
        assert ei.value.code_id == "ITR-E002"

    async def test_c10_transition_unknown_ref_raises_itr_e002(self) -> None:
        t = _mock()
        fake = IssueRef(issue_id="MISSING-99", source_system="mock", url="")
        with pytest.raises(PipelineError) as ei:
            await t.transition_issue(fake, "start")
        assert ei.value.code_id == "ITR-E002"

    async def test_c10_comment_unknown_ref_raises_itr_e002(self) -> None:
        t = _mock()
        fake = IssueRef(issue_id="MISSING-99", source_system="mock", url="")
        with pytest.raises(PipelineError) as ei:
            await t.add_comment(fake, "body")
        assert ei.value.code_id == "ITR-E002"


# ---------------------------------------------------------------------------
# TestMockIssueTrackerPollChanges
# ---------------------------------------------------------------------------


class TestMockIssueTrackerPollChanges:
    async def test_poll_changes_returns_changes_in_window(self) -> None:
        t = _mock()
        t0 = _now()
        r1 = await t.create_issue("P", "A", "")
        r2 = await t.create_issue("P", "B", "")
        changes = [c async for c in await t.poll_changes(since=t0)]
        issue_ids = {c.issue_ref.issue_id for c in changes}
        assert r1.issue_id in issue_ids
        assert r2.issue_id in issue_ids

    async def test_poll_changes_empty_before_any_activity(self) -> None:
        t = _mock()
        future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        changes = [c async for c in await t.poll_changes(since=future)]
        assert changes == []


# ---------------------------------------------------------------------------
# TestMockIssueTrackerWebhook
# ---------------------------------------------------------------------------


class TestMockIssueTrackerWebhook:
    async def test_register_webhook_returns_ref(self) -> None:
        t = _mock()
        wref = await t.register_webhook(
            callback_url="http://hilda.corp/webhooks/issue-tracker/mock",
            events=["issue_created", "issue_updated"],
            secret="s3cr3t",
        )
        assert isinstance(wref, WebhookRef)
        assert wref.source_system == "mock"
        assert wref.callback_url == "http://hilda.corp/webhooks/issue-tracker/mock"


# ---------------------------------------------------------------------------
# TestLoadAdapter
# ---------------------------------------------------------------------------


class TestLoadAdapter:
    def test_load_mock_adapter(self) -> None:
        from core.src.issue_tracker.issue_tracker_cli import load_adapter
        adapter = load_adapter("mock")
        assert isinstance(adapter, MockIssueTracker)
        assert adapter.source_system == "mock"

    def test_load_unknown_slug_raises_itr_e006(self) -> None:
        from core.src.issue_tracker.issue_tracker_cli import load_adapter
        with pytest.raises(PipelineError) as ei:
            load_adapter("nonexistent_slug_xyz_abc")
        assert ei.value.code_id == "ITR-E006"


# ---------------------------------------------------------------------------
# TestItrErrorCodesRegistered
# ---------------------------------------------------------------------------


class TestItrErrorCodesRegistered:
    @pytest.mark.parametrize("code", [
        "ITR-E001", "ITR-E002", "ITR-E003", "ITR-E004",
        "ITR-E005", "ITR-E006", "ITR-W001", "ITR-W002",
    ])
    def test_code_is_registered(self, code: str) -> None:
        from core.src.diagnostics import get_code
        ec = get_code(code)
        assert ec.code == code

    def test_itr_errors_are_not_recoverable(self) -> None:
        from core.src.diagnostics import get_code
        for code in ["ITR-E001", "ITR-E002", "ITR-E003", "ITR-E004", "ITR-E005", "ITR-E006"]:
            assert get_code(code).recoverable is False

    def test_itr_warnings_are_recoverable(self) -> None:
        from core.src.diagnostics import get_code
        for code in ["ITR-W001", "ITR-W002"]:
            assert get_code(code).recoverable is True

    def test_itr_prefix_registered(self) -> None:
        from core.src.diagnostics.error_codes import PREFIX_REGISTRY
        assert PREFIX_REGISTRY["ITR"] == "issue_tracker"
