"""Contract test suite for IssueTracker adapters (C01-C10).

Run against any adapter slug:
    pytest customizations/issue_tracker/tests/test_contract.py \
        --adapter <slug> --project <PROJECT_KEY> -v

The --adapter and --project options are supplied on the command line.
Default adapter is "mock" so the suite can run in CI without credentials.

To run against the real proprietary adapter:
    export HILDA_<SLUG>_... (adapter-specific env vars)
    pytest customizations/issue_tracker/tests/test_contract.py \
        --adapter proprietary --project HILDA -v
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

import pytest

from core.src.diagnostics import PipelineError
from core.src.issue_tracker import (
    AttachmentRef,
    CommentRef,
    IssueChange,
    IssueQuery,
    IssueRef,
    IssueStatus,
    IssueTracker,
    WebhookRef,
)
from core.src.issue_tracker.mock_adapter import MockIssueTracker
# Fixtures and CLI options (--adapter, --project) are defined in conftest.py


# ---------------------------------------------------------------------------
# Helper: collect async iterator into list
# ---------------------------------------------------------------------------


async def _collect(ait: AsyncIterator) -> list:
    return [item async for item in ait]


# ---------------------------------------------------------------------------
# C01 — Round-trip: create → get → verify fields
# ---------------------------------------------------------------------------


class TestC01CreateGet:
    async def test_create_returns_ref(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C01 summary", "C01 description")
        assert isinstance(ref, IssueRef)
        assert ref.issue_id
        assert ref.source_system == tracker.source_system

    async def test_get_returns_correct_fields(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C01 get-check", "C01 desc")
        issue = await tracker.get_issue(ref)
        assert issue.summary == "C01 get-check"
        assert issue.ref.issue_id == ref.issue_id
        assert issue.status in IssueStatus.__members__.values()

    async def test_create_sets_source_system(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C01 source", "")
        assert ref.source_system == tracker.source_system


# ---------------------------------------------------------------------------
# C02 — Update: set fields → get → verify changed
# ---------------------------------------------------------------------------


class TestC02Update:
    async def test_update_summary(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C02 original", "")
        await tracker.update_issue(ref, {"summary": "C02 updated"})
        issue = await tracker.get_issue(ref)
        assert issue.summary == "C02 updated"

    async def test_update_unknown_ref_raises_itr_e002(self, tracker: IssueTracker) -> None:
        fake = IssueRef(issue_id="NO-SUCH-ISSUE-99999", source_system=tracker.source_system, url="")
        with pytest.raises(PipelineError) as ei:
            await tracker.update_issue(fake, {"summary": "x"})
        assert ei.value.code_id == "ITR-E002"


# ---------------------------------------------------------------------------
# C03 — Transition: valid state change accepted
# ---------------------------------------------------------------------------


class TestC03Transition:
    async def test_transition_start(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C03 start", "")
        await tracker.transition_issue(ref, "start")
        issue = await tracker.get_issue(ref)
        assert issue.status == IssueStatus.IN_PROGRESS

    async def test_transition_resolve(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C03 resolve", "")
        await tracker.transition_issue(ref, "resolve")
        issue = await tracker.get_issue(ref)
        assert issue.status == IssueStatus.RESOLVED

    async def test_transition_unknown_ref_raises_itr_e002(self, tracker: IssueTracker) -> None:
        fake = IssueRef(issue_id="NO-SUCH-ISSUE-99999", source_system=tracker.source_system, url="")
        with pytest.raises(PipelineError) as ei:
            await tracker.transition_issue(fake, "start")
        assert ei.value.code_id == "ITR-E002"


# ---------------------------------------------------------------------------
# C04 — Close idempotent: close twice → no error
# ---------------------------------------------------------------------------


class TestC04CloseIdempotent:
    async def test_close_sets_closed_status(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C04 close", "")
        await tracker.close_issue(ref, "done")
        issue = await tracker.get_issue(ref)
        assert issue.status == IssueStatus.CLOSED

    async def test_close_twice_no_error(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C04 idempotent", "")
        await tracker.close_issue(ref, "done")
        await tracker.close_issue(ref, "done")  # must not raise
        issue = await tracker.get_issue(ref)
        assert issue.status == IssueStatus.CLOSED


# ---------------------------------------------------------------------------
# C05 — Comment: add → retrievable
# ---------------------------------------------------------------------------


class TestC05Comment:
    async def test_add_comment_returns_comment_ref(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C05 comment host", "")
        cref = await tracker.add_comment(ref, "C05 comment body")
        assert isinstance(cref, CommentRef)
        assert cref.issue_ref.issue_id == ref.issue_id
        assert cref.source_system == tracker.source_system

    async def test_add_comment_unknown_ref_raises_itr_e002(self, tracker: IssueTracker) -> None:
        fake = IssueRef(issue_id="NO-SUCH-ISSUE-99999", source_system=tracker.source_system, url="")
        with pytest.raises(PipelineError) as ei:
            await tracker.add_comment(fake, "body")
        assert ei.value.code_id == "ITR-E002"


# ---------------------------------------------------------------------------
# C06 — Attachment: upload → ref returned
# ---------------------------------------------------------------------------


class TestC06Attachment:
    async def test_upload_path_returns_attachment_ref(
        self, tracker: IssueTracker, project_key: str, tmp_path: Path
    ) -> None:
        ref = await tracker.create_issue(project_key, "C06 attachment host", "")
        attachment_file = tmp_path / "evidence.pdf"
        attachment_file.write_bytes(b"%PDF-1.4 fake content")
        aref = await tracker.upload_attachment(ref, attachment_file)
        assert isinstance(aref, AttachmentRef)
        assert aref.issue_ref.issue_id == ref.issue_id
        assert "evidence.pdf" in aref.filename
        assert aref.source_system == tracker.source_system

    async def test_upload_stream_returns_attachment_ref(
        self, tracker: IssueTracker, project_key: str
    ) -> None:
        async def _stream():
            yield b"binary data chunk 1"
            yield b"binary data chunk 2"

        ref = await tracker.create_issue(project_key, "C06 stream host", "")
        aref = await tracker.upload_attachment(ref, _stream())
        assert isinstance(aref, AttachmentRef)


# ---------------------------------------------------------------------------
# C07 — Search: query returns created issue
# ---------------------------------------------------------------------------


class TestC07Search:
    async def test_search_finds_created_issue(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C07 searchable", "")
        results = await _collect(await tracker.search(IssueQuery(project=project_key)))
        issue_ids = [r.issue_id for r in results]
        assert ref.issue_id in issue_ids

    async def test_search_by_status_open(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C07 open", "")
        results = await _collect(
            await tracker.search(IssueQuery(project=project_key, status=IssueStatus.OPEN))
        )
        issue_ids = [r.issue_id for r in results]
        assert ref.issue_id in issue_ids

    async def test_search_returns_issue_refs(self, tracker: IssueTracker, project_key: str) -> None:
        await tracker.create_issue(project_key, "C07 ref-type", "")
        results = await _collect(await tracker.search(IssueQuery(project=project_key)))
        assert all(isinstance(r, IssueRef) for r in results)


# ---------------------------------------------------------------------------
# C08 — Changes: list_recent_changes since T₀
# ---------------------------------------------------------------------------


class TestC08Changes:
    async def test_changes_after_create(self, tracker: IssueTracker, project_key: str) -> None:
        t0 = datetime.now(timezone.utc)
        ref = await tracker.create_issue(project_key, "C08 changes", "")
        changes = await _collect(await tracker.list_recent_changes(ref, since=t0))
        assert len(changes) >= 1
        assert all(isinstance(c, IssueChange) for c in changes)
        assert all(c.issue_ref.issue_id == ref.issue_id for c in changes)
        assert all(c.changed_at >= t0 for c in changes)

    async def test_changes_after_comment(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C08 comment change", "")
        t0 = datetime.now(timezone.utc)
        await tracker.add_comment(ref, "C08 change trigger")
        changes = await _collect(await tracker.list_recent_changes(ref, since=t0))
        assert len(changes) >= 1

    async def test_changes_since_future_empty(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C08 future", "")
        far_future = datetime(2099, 1, 1, tzinfo=timezone.utc)
        changes = await _collect(await tracker.list_recent_changes(ref, since=far_future))
        assert changes == []


# ---------------------------------------------------------------------------
# C09 — Idempotency: create twice same key → one issue
# ---------------------------------------------------------------------------


class TestC09Idempotency:
    async def test_create_same_key_returns_same_ref(
        self, tracker: IssueTracker, project_key: str
    ) -> None:
        key = f"c09-idem-{id(tracker)}"
        r1 = await tracker.create_issue(project_key, "C09 dup", "", idempotency_key=key)
        r2 = await tracker.create_issue(project_key, "C09 dup", "", idempotency_key=key)
        assert r1.issue_id == r2.issue_id

    async def test_different_keys_create_different_issues(
        self, tracker: IssueTracker, project_key: str
    ) -> None:
        base = f"c09-diff-{id(tracker)}"
        r1 = await tracker.create_issue(project_key, "C09 A", "", idempotency_key=f"{base}-1")
        r2 = await tracker.create_issue(project_key, "C09 B", "", idempotency_key=f"{base}-2")
        assert r1.issue_id != r2.issue_id

    async def test_comment_idempotency(self, tracker: IssueTracker, project_key: str) -> None:
        ref = await tracker.create_issue(project_key, "C09 comment host", "")
        key = f"c09-cmt-{id(tracker)}"
        cr1 = await tracker.add_comment(ref, "body", idempotency_key=key)
        cr2 = await tracker.add_comment(ref, "body", idempotency_key=key)
        assert cr1.comment_id == cr2.comment_id


# ---------------------------------------------------------------------------
# C10 — Error surface: unknown ref → ITR-E002
# ---------------------------------------------------------------------------


class TestC10ErrorSurface:
    async def test_get_unknown_ref_raises_itr_e002(self, tracker: IssueTracker) -> None:
        fake = IssueRef(issue_id="NO-SUCH-ISSUE-99999", source_system=tracker.source_system, url="")
        with pytest.raises(PipelineError) as ei:
            await tracker.get_issue(fake)
        assert ei.value.code_id == "ITR-E002"

    async def test_error_carries_issue_id_in_context(self, tracker: IssueTracker) -> None:
        fake = IssueRef(issue_id="NO-SUCH-XYZ", source_system=tracker.source_system, url="")
        with pytest.raises(PipelineError) as ei:
            await tracker.get_issue(fake)
        assert "NO-SUCH-XYZ" in str(ei.value) or ei.value.context.get("issue_id") == "NO-SUCH-XYZ"

    async def test_error_carries_system_in_context(self, tracker: IssueTracker) -> None:
        fake = IssueRef(issue_id="NO-SUCH-ZZZ", source_system=tracker.source_system, url="")
        with pytest.raises(PipelineError) as ei:
            await tracker.get_issue(fake)
        assert (
            tracker.source_system in str(ei.value)
            or ei.value.context.get("system") == tracker.source_system
        )
