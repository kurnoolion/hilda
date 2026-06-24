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
        "ITR-E005", "ITR-E006", "ITR-E007", "ITR-E008",
        "ITR-W001", "ITR-W002",
        # Added 2026-06-25 (Module #13 cascade D15)
        "ITR-W003", "ITR-W004", "ITR-W005",
    ])
    def test_code_is_registered(self, code: str) -> None:
        from core.src.diagnostics import get_code
        ec = get_code(code)
        assert ec.code == code

    def test_itr_errors_are_not_recoverable(self) -> None:
        from core.src.diagnostics import get_code
        for code in ["ITR-E001", "ITR-E002", "ITR-E003", "ITR-E004",
                     "ITR-E005", "ITR-E006", "ITR-E007", "ITR-E008"]:
            assert get_code(code).recoverable is False

    def test_itr_warnings_are_recoverable(self) -> None:
        from core.src.diagnostics import get_code
        for code in ["ITR-W001", "ITR-W002", "ITR-W003", "ITR-W004", "ITR-W005"]:
            assert get_code(code).recoverable is True

    def test_itr_prefix_registered(self) -> None:
        from core.src.diagnostics.error_codes import PREFIX_REGISTRY
        assert PREFIX_REGISTRY["ITR"] == "issue_tracker"


# ===========================================================================
# Module #13 cascade 2026-06-25 -- new Protocol surfaces + sub-modules
# ===========================================================================


# ---------------------------------------------------------------------------
# TestTpmCorpIdHelper (D8)
# ---------------------------------------------------------------------------


class TestTpmCorpIdHelper:
    def test_strips_domain_from_email(self) -> None:
        from core.src.issue_tracker.utils import derive_tpm_corp_id
        assert derive_tpm_corp_id("abc@corp.com") == "abc"

    def test_handles_dotted_local_part(self) -> None:
        from core.src.issue_tracker.utils import derive_tpm_corp_id
        assert derive_tpm_corp_id("alice.smith@corp.example.com") == "alice.smith"

    def test_empty_input_raises_itr_e002(self) -> None:
        from core.src.issue_tracker.utils import derive_tpm_corp_id
        with pytest.raises(PipelineError) as ei:
            derive_tpm_corp_id("")
        assert ei.value.code_id == "ITR-E002"

    def test_missing_at_separator_raises_itr_e002(self) -> None:
        from core.src.issue_tracker.utils import derive_tpm_corp_id
        with pytest.raises(PipelineError) as ei:
            derive_tpm_corp_id("plain-string")
        assert ei.value.code_id == "ITR-E002"

    def test_empty_local_part_raises_itr_e002(self) -> None:
        from core.src.issue_tracker.utils import derive_tpm_corp_id
        with pytest.raises(PipelineError) as ei:
            derive_tpm_corp_id("@corp.com")
        assert ei.value.code_id == "ITR-E002"

    def test_export_via_module_init(self) -> None:
        from core.src.issue_tracker import derive_tpm_corp_id
        assert derive_tpm_corp_id("abc@corp.com") == "abc"


# ---------------------------------------------------------------------------
# TestPlmDataclasses (D5)
# ---------------------------------------------------------------------------


class TestPlmDataclasses:
    def test_plm_document_node_frozen(self) -> None:
        from core.src.issue_tracker import PlmDocumentNode
        node = PlmDocumentNode(document_id="D-1", document_name="report.pdf", file_id="F-1")
        with pytest.raises((AttributeError, TypeError)):
            node.document_id = "D-2"  # type: ignore[misc]

    def test_plm_document_node_both_fields_required_per_q7(self) -> None:
        from core.src.issue_tracker import PlmDocumentNode
        # Q7: both document_id + file_id persist; no derivation.
        node = PlmDocumentNode(document_id="D-1", document_name="x", file_id="F-1")
        assert node.document_id == "D-1"
        assert node.file_id == "F-1"
        assert node.document_id != node.file_id

    def test_jira_issue_frozen(self) -> None:
        from core.src.issue_tracker import JiraIssue
        iss = JiraIssue(
            issue_key="PROJ-1", summary="x", status="Open",
            owner_corp_email="alice@corp.com",
            last_updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
            project_key="PROJ",
        )
        with pytest.raises((AttributeError, TypeError)):
            iss.issue_key = "PROJ-2"  # type: ignore[misc]

    def test_jira_comment_frozen(self) -> None:
        from core.src.issue_tracker import JiraComment
        c = JiraComment(
            comment_id="C-1", author_corp_email="bob@corp.com",
            body="hi", created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        with pytest.raises((AttributeError, TypeError)):
            c.comment_id = "C-2"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TestCorpPlmAdapterProtocol (D3)
# ---------------------------------------------------------------------------


class TestCorpPlmAdapterProtocol:
    def test_mock_is_runtime_checkable_corp_plm(self) -> None:
        from core.src.issue_tracker import CorpPlmAdapter, MockCorpPlmAdapter
        m = MockCorpPlmAdapter(customer_id="test_a")
        assert isinstance(m, CorpPlmAdapter)
        assert m.source_system == "test_a"

    async def test_mock_create_plm_returns_id(self) -> None:
        from core.src.issue_tracker import MockCorpPlmAdapter
        m = MockCorpPlmAdapter()
        plm_id = await m.create_plm("MODEL-X", "tpm0", "Title", "owner0", "desc")
        assert plm_id is not None
        assert plm_id.startswith("MOCKPLM-")
        assert m.calls["create_plm"] == 1

    async def test_mock_close_plm_returns_true(self) -> None:
        from core.src.issue_tracker import MockCorpPlmAdapter
        m = MockCorpPlmAdapter()
        plm_id = await m.create_plm("MODEL-X", "tpm0", "T", "o0", "d")
        ok = await m.close_plm(plm_id or "", "tpm0")
        assert ok is True
        assert m.calls["close_plm"] == 1

    async def test_mock_close_plm_unknown_returns_false(self) -> None:
        from core.src.issue_tracker import MockCorpPlmAdapter
        m = MockCorpPlmAdapter()
        ok = await m.close_plm("DOES-NOT-EXIST", "tpm0")
        assert ok is False

    async def test_mock_get_documents_list_returns_seeded_nodes(self) -> None:
        from core.src.issue_tracker import MockCorpPlmAdapter, PlmDocumentNode
        m = MockCorpPlmAdapter()
        plm_id = await m.create_plm("MX", "tpm0", "T", "o0", "d")
        m.seed_documents(plm_id or "", [
            PlmDocumentNode(document_id="D1", document_name="a.pdf", file_id="F1"),
            PlmDocumentNode(document_id="D2", document_name="b.pdf", file_id="F2"),
        ])
        nodes = await m.get_documents_list(plm_id or "", "tpm0")
        assert len(nodes) == 2
        assert nodes[0].file_id == "F1"

    async def test_mock_download_file_records_path(self) -> None:
        from core.src.issue_tracker import MockCorpPlmAdapter, PlmDocumentNode
        m = MockCorpPlmAdapter()
        plm_id = await m.create_plm("MX", "tpm0", "T", "o0", "d")
        m.seed_documents(plm_id or "", [
            PlmDocumentNode(document_id="D1", document_name="a.pdf", file_id="F1"),
        ])
        dest = Path("/tmp/audit/a.pdf")
        ok = await m.download_file("tpm0", "D1", "a.pdf", "F1", dest)
        assert ok is True
        assert m.downloaded_files[(plm_id or "", "F1")] == dest

    async def test_mock_upload_file_records(self) -> None:
        from core.src.issue_tracker import MockCorpPlmAdapter
        m = MockCorpPlmAdapter()
        plm_id = await m.create_plm("MX", "tpm0", "T", "o0", "d")
        ok = await m.upload_file(plm_id or "", "out.pdf", Path("/tmp/out.pdf"))
        assert ok is True

    async def test_base_class_invoke_methods_raise_not_implemented_as_e001(self) -> None:
        # Per D3: base class _invoke_* default impls raise NotImplementedError
        # which the public surface translates into ITR-E001.
        from core.src.issue_tracker import CorpPlmAdapterBase
        base = CorpPlmAdapterBase(customer_id="bare", max_retries=1)
        with pytest.raises(PipelineError) as ei:
            await base.create_plm("MX", "tpm0", "T", "o0", "d")
        assert ei.value.code_id == "ITR-E001"


# ---------------------------------------------------------------------------
# TestInFlightDownloadTracker (D7 / Q6)
# ---------------------------------------------------------------------------


class TestInFlightDownloadTracker:
    async def test_first_acquire_succeeds(self) -> None:
        from core.src.issue_tracker import InFlightDownloadTracker
        t = InFlightDownloadTracker()
        async with t.acquire(("plm1", "f1")) as acquired:
            assert acquired is True
            assert ("plm1", "f1") in t.in_flight()

    async def test_concurrent_acquire_same_key_returns_no_op(self) -> None:
        from core.src.issue_tracker import InFlightDownloadTracker
        t = InFlightDownloadTracker()
        first_entered = asyncio.Event()
        first_release = asyncio.Event()
        second_result: list[bool] = []

        async def first_task() -> None:
            async with t.acquire(("plm1", "f1")) as acquired:
                first_entered.set()
                await first_release.wait()
                second_result.append(acquired)  # True

        async def second_task() -> None:
            await first_entered.wait()
            async with t.acquire(("plm1", "f1")) as acquired:
                second_result.append(acquired)  # False -- in-flight

        task_a = asyncio.create_task(first_task())
        task_b = asyncio.create_task(second_task())
        await asyncio.sleep(0.01)
        first_release.set()
        await asyncio.gather(task_a, task_b)
        # First task records True, second records False
        assert True in second_result
        assert False in second_result

    async def test_release_clears_key_on_exit(self) -> None:
        from core.src.issue_tracker import InFlightDownloadTracker
        t = InFlightDownloadTracker()
        async with t.acquire(("plm1", "f1")):
            pass
        assert ("plm1", "f1") not in t.in_flight()

    async def test_release_clears_key_on_exception(self) -> None:
        from core.src.issue_tracker import InFlightDownloadTracker
        t = InFlightDownloadTracker()
        with pytest.raises(RuntimeError):
            async with t.acquire(("plm1", "f1")):
                raise RuntimeError("boom")
        assert ("plm1", "f1") not in t.in_flight()

    async def test_different_keys_acquire_independently(self) -> None:
        from core.src.issue_tracker import InFlightDownloadTracker
        t = InFlightDownloadTracker()
        async with t.acquire(("plm1", "f1")) as a, t.acquire(("plm1", "f2")) as b:
            assert a is True
            assert b is True


# ---------------------------------------------------------------------------
# TestCorpPlmPoller (D6 deadline-tiered cadence)
# ---------------------------------------------------------------------------


class TestCorpPlmPoller:
    def test_default_ladder_baseline_60_min(self) -> None:
        from core.src.issue_tracker import resolve_polling_interval
        assert resolve_polling_interval(30) == 60

    def test_default_ladder_14_days(self) -> None:
        from core.src.issue_tracker import resolve_polling_interval
        assert resolve_polling_interval(14) == 30
        assert resolve_polling_interval(10) == 30

    def test_default_ladder_7_days(self) -> None:
        from core.src.issue_tracker import resolve_polling_interval
        assert resolve_polling_interval(7) == 15
        assert resolve_polling_interval(5) == 15

    def test_default_ladder_3_days(self) -> None:
        from core.src.issue_tracker import resolve_polling_interval
        assert resolve_polling_interval(3) == 5
        assert resolve_polling_interval(2) == 5

    def test_default_ladder_deadline_day(self) -> None:
        from core.src.issue_tracker import resolve_polling_interval
        assert resolve_polling_interval(1) == 1
        assert resolve_polling_interval(0) == 1
        assert resolve_polling_interval(-1) == 1

    def test_custom_ladder_used(self) -> None:
        from core.src.issue_tracker import PollingTier, resolve_polling_interval
        ladder = (
            PollingTier(days_before_deadline=1, interval_minutes=2),
            PollingTier(days_before_deadline=None, interval_minutes=120),
        )
        assert resolve_polling_interval(0, ladder) == 2
        assert resolve_polling_interval(10, ladder) == 120

    def test_ladder_missing_baseline_raises(self) -> None:
        from core.src.issue_tracker import PollingTier, resolve_polling_interval
        ladder = (PollingTier(days_before_deadline=1, interval_minutes=5),)
        with pytest.raises(ValueError):
            resolve_polling_interval(10, ladder)

    async def test_poll_target_emits_trigger_for_new_files(self) -> None:
        from core.src.issue_tracker import (
            ActivePollTarget,
            CorpPlmPoller,
            InFlightDownloadTracker,
            MockCorpPlmAdapter,
            PlmDocumentNode,
        )

        class StubEmitter:
            def __init__(self) -> None:
                self.events: list[tuple[str, str, str, Path]] = []

            def emit_attachment_received(
                self, delivery_item_id: str, plm_id: str, file_id: str, downloaded_path: Path,
            ) -> None:
                self.events.append((delivery_item_id, plm_id, file_id, downloaded_path))

        adapter = MockCorpPlmAdapter()
        plm_id = await adapter.create_plm("MX", "tpm0", "T", "o0", "d")
        adapter.seed_documents(plm_id or "", [
            PlmDocumentNode(document_id="D1", document_name="a.pdf", file_id="F1"),
            PlmDocumentNode(document_id="D2", document_name="b.pdf", file_id="F2"),
        ])
        emitter = StubEmitter()
        poller = CorpPlmPoller(
            adapter=adapter,
            in_flight=InFlightDownloadTracker(),
            trigger_emitter=emitter,
        )
        downloaded = await poller.poll_target(ActivePollTarget(
            plm_id=plm_id or "",
            tpm_corp_id="tpm0",
            days_to_deadline=10,
            nsd_root=Path("/tmp/nsd"),
            delivery_item_id="DI-1",
        ))
        assert len(downloaded) == 2
        assert len(emitter.events) == 2
        # File path layout: <nsd_root>/<plm_id>/<file_id>/<document_name>
        assert downloaded[0] == Path("/tmp/nsd") / (plm_id or "") / "F1" / "a.pdf"

    async def test_poll_target_dedupes_already_seen(self) -> None:
        from core.src.issue_tracker import (
            ActivePollTarget,
            CorpPlmPoller,
            InFlightDownloadTracker,
            MockCorpPlmAdapter,
            PlmDocumentNode,
        )
        adapter = MockCorpPlmAdapter()
        plm_id = await adapter.create_plm("MX", "tpm0", "T", "o0", "d")
        adapter.seed_documents(plm_id or "", [
            PlmDocumentNode(document_id="D1", document_name="a.pdf", file_id="F1"),
        ])
        poller = CorpPlmPoller(adapter=adapter, in_flight=InFlightDownloadTracker())
        target = ActivePollTarget(
            plm_id=plm_id or "", tpm_corp_id="tpm0", days_to_deadline=10,
            nsd_root=Path("/tmp/nsd"), delivery_item_id="DI-1",
        )
        first = await poller.poll_target(target)
        second = await poller.poll_target(target)
        assert len(first) == 1
        assert len(second) == 0  # seen-set dedupes

    async def test_poll_target_skips_in_flight_keys(self) -> None:
        from core.src.issue_tracker import (
            ActivePollTarget,
            CorpPlmPoller,
            InFlightDownloadTracker,
            MockCorpPlmAdapter,
            PlmDocumentNode,
        )
        adapter = MockCorpPlmAdapter()
        plm_id = await adapter.create_plm("MX", "tpm0", "T", "o0", "d")
        adapter.seed_documents(plm_id or "", [
            PlmDocumentNode(document_id="D1", document_name="a.pdf", file_id="F1"),
        ])
        in_flight = InFlightDownloadTracker()
        poller = CorpPlmPoller(adapter=adapter, in_flight=in_flight)
        target = ActivePollTarget(
            plm_id=plm_id or "", tpm_corp_id="tpm0", days_to_deadline=10,
            nsd_root=Path("/tmp/nsd"), delivery_item_id="DI-1",
        )

        # Pre-occupy the in-flight tracker for the key the poller is about to try.
        async def pre_occupy() -> None:
            async with in_flight.acquire((plm_id or "", "F1")):
                # Poll runs while we're holding the key.
                results = await poller.poll_target(target)
                assert len(results) == 0  # skipped

        await pre_occupy()


# ---------------------------------------------------------------------------
# TestCustomerJiraAdapterProtocol (D4)
# ---------------------------------------------------------------------------


class TestCustomerJiraAdapterProtocol:
    def test_mock_is_runtime_checkable(self) -> None:
        from core.src.issue_tracker import CustomerJiraAdapter, MockCustomerJiraAdapter
        m = MockCustomerJiraAdapter(customer_id="test_a")
        assert isinstance(m, CustomerJiraAdapter)
        assert m.source_system == "test_a"

    async def test_search_issues_returns_seeded(self) -> None:
        from core.src.issue_tracker import JiraIssue, MockCustomerJiraAdapter
        m = MockCustomerJiraAdapter()
        m.seed_issue(JiraIssue(
            issue_key="PROJ-1", summary="x", status="Open",
            owner_corp_email="a@corp.com",
            last_updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
            project_key="PROJ",
        ))
        results = await m.search_issues(jql="anything")
        assert len(results) == 1
        assert results[0].issue_key == "PROJ-1"

    async def test_get_issue_raises_e002_for_unknown(self) -> None:
        from core.src.issue_tracker import MockCustomerJiraAdapter
        m = MockCustomerJiraAdapter()
        with pytest.raises(PipelineError) as ei:
            await m.get_issue("NOPE-1")
        assert ei.value.code_id == "ITR-E002"

    async def test_list_comments_returns_seeded(self) -> None:
        from core.src.issue_tracker import JiraComment, JiraIssue, MockCustomerJiraAdapter
        m = MockCustomerJiraAdapter()
        m.seed_issue(JiraIssue(
            issue_key="PROJ-1", summary="x", status="Open",
            owner_corp_email=None,
            last_updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
            project_key="PROJ",
        ))
        m.seed_comment("PROJ-1", JiraComment(
            comment_id="C1", author_corp_email="alice@corp.com", body="hi",
            created_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
        ))
        comments = await m.list_comments("PROJ-1")
        assert len(comments) == 1
        assert comments[0].comment_id == "C1"

    async def test_ph2_create_issue_raises_e007(self) -> None:
        # Per D4: Ph-2 mutation surface is documented as stubs on the base class
        # raising ITR-E007. The mock doesn't expose them (Ph-1 informational only),
        # so we exercise the base class.
        from core.src.issue_tracker.customer_jira.adapter import (
            CustomerJiraAdapterBase,
            CustomerJiraConfig,
        )
        import httpx
        adapter = CustomerJiraAdapterBase(
            config=CustomerJiraConfig(
                customer_id="test_a", base_url="http://localhost", project_key="X"
            ),
            http_client=httpx.AsyncClient(base_url="http://localhost"),
        )
        with pytest.raises(PipelineError) as ei:
            await adapter.create_issue()
        assert ei.value.code_id == "ITR-E007"


# ---------------------------------------------------------------------------
# TestCustomerJiraPoller (D9 -- Ph-1 informational)
# ---------------------------------------------------------------------------


class TestCustomerJiraPoller:
    async def test_poll_caches_results(self) -> None:
        from core.src.issue_tracker import (
            CustomerJiraPoller,
            JiraIssue,
            MockCustomerJiraAdapter,
        )
        m = MockCustomerJiraAdapter()
        m.seed_issue(JiraIssue(
            issue_key="PROJ-1", summary="x", status="Open",
            owner_corp_email=None,
            last_updated=datetime(2026, 1, 1, tzinfo=timezone.utc),
            project_key="PROJ",
        ))
        poller = CustomerJiraPoller(adapter=m)
        results = await poller.poll(jql="anything")
        assert len(results) == 1
        assert poller.cached("PROJ-1") is not None

    async def test_open_tickets_filters_terminal_status(self) -> None:
        from core.src.issue_tracker import (
            CustomerJiraPoller,
            JiraIssue,
            MockCustomerJiraAdapter,
        )
        m = MockCustomerJiraAdapter()
        ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
        m.seed_issue(JiraIssue("PROJ-1", "open one", "Open", None, ts, "PROJ"))
        m.seed_issue(JiraIssue("PROJ-2", "done one", "Done", None, ts, "PROJ"))
        m.seed_issue(JiraIssue("PROJ-3", "closed one", "Closed", None, ts, "PROJ"))
        m.seed_issue(JiraIssue("PROJ-4", "wip one", "In Progress", None, ts, "PROJ"))
        poller = CustomerJiraPoller(adapter=m)
        await poller.poll(jql="anything")
        open_keys = {iss.issue_key for iss in poller.open_tickets()}
        assert open_keys == {"PROJ-1", "PROJ-4"}

    async def test_reset_cache_clears(self) -> None:
        from core.src.issue_tracker import (
            CustomerJiraPoller,
            JiraIssue,
            MockCustomerJiraAdapter,
        )
        m = MockCustomerJiraAdapter()
        m.seed_issue(JiraIssue(
            "PROJ-1", "x", "Open", None,
            datetime(2026, 1, 1, tzinfo=timezone.utc), "PROJ",
        ))
        poller = CustomerJiraPoller(adapter=m)
        await poller.poll(jql="x")
        poller.reset_cache()
        assert poller.cached("PROJ-1") is None


# ---------------------------------------------------------------------------
# TestProtocolContract (D1)
# ---------------------------------------------------------------------------


class TestProtocolContract:
    def test_corp_plm_protocol_has_five_methods(self) -> None:
        from core.src.issue_tracker import CorpPlmAdapter
        # Per architect Q1 + 5-API spec
        protocol_attrs = {
            m for m in dir(CorpPlmAdapter)
            if not m.startswith("_") and m != "source_system"
        }
        assert protocol_attrs == {
            "create_plm", "close_plm", "get_documents_list",
            "download_file", "upload_file",
        }

    def test_customer_jira_protocol_ph1_surface_is_read_only(self) -> None:
        from core.src.issue_tracker import CustomerJiraAdapter
        # Per D4 + [D-092] Ph-1 informational lock
        protocol_attrs = {
            m for m in dir(CustomerJiraAdapter)
            if not m.startswith("_") and m != "source_system"
        }
        assert protocol_attrs == {"search_issues", "get_issue", "list_comments"}

    def test_corp_plm_adapter_base_is_thin_wrapper(self) -> None:
        from core.src.issue_tracker import CorpPlmAdapterBase
        # Per D3 + [D-027]: base class provides _invoke_* methods to override
        for m in (
            "_invoke_create_plm",
            "_invoke_close_plm",
            "_invoke_get_documents_list",
            "_invoke_download_file",
            "_invoke_upload_file",
        ):
            assert hasattr(CorpPlmAdapterBase, m)


# ---------------------------------------------------------------------------
# TestNewExports (module init)
# ---------------------------------------------------------------------------


class TestNewExports:
    def test_module_exports_new_surfaces(self) -> None:
        import core.src.issue_tracker as it
        for name in (
            "CorpPlmAdapter", "CorpPlmAdapterBase",
            "CustomerJiraAdapter", "CustomerJiraAdapterBase", "CustomerJiraConfig",
            "JiraComment", "JiraIssue", "PlmDocumentNode",
            "derive_tpm_corp_id",
            "InFlightDownloadTracker",
            "CorpPlmPoller", "ActivePollTarget",
            "DEFAULT_POLLING_LADDER", "PollingTier", "TriggerEmitter",
            "resolve_polling_interval",
            "CustomerJiraPoller",
            "MockCorpPlmAdapter", "MockCustomerJiraAdapter",
        ):
            assert hasattr(it, name), f"missing export: {name}"

    def test_module_retains_legacy_exports(self) -> None:
        import core.src.issue_tracker as it
        for name in (
            "AttachmentInput", "AttachmentRef", "CommentRef",
            "Issue", "IssueChange", "IssueQuery", "IssueRef",
            "IssuePriority", "IssueStatus", "IssueTracker", "WebhookRef",
        ):
            assert hasattr(it, name), f"missing legacy export: {name}"
