"""MockCorpPlmAdapter + MockCustomerJiraAdapter -- in-memory test doubles for
the new Module #13 Protocols (2026-06-25).

These do NOT depend on httpx, the corp_plm_gateway PC, or any real JIRA
instance. Test usage:

    plm = MockCorpPlmAdapter(customer_id="test_carrier_a")
    plm_id = await plm.create_plm("MODEL-X", "tpm0", "Title", "owner0", "desc")

    jira = MockCustomerJiraAdapter(customer_id="test_carrier_a")
    jira.seed_issue(JiraIssue(...))
    issues = await jira.search_issues(jql="anything")
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from core.src.diagnostics.error_codes import PipelineError
from core.src.issue_tracker.protocol import (
    JiraComment,
    JiraIssue,
    PlmDocumentNode,
)


# ===========================================================================
# MockCorpPlmAdapter
# ===========================================================================


@dataclass
class _MockPlmIssue:
    plm_id: str
    model_number: str
    tpm_corp_id: str
    title: str
    owner_corp_id: str
    description: str
    closed: bool = False
    documents: list[PlmDocumentNode] = field(default_factory=list)
    uploaded: list[tuple[str, str]] = field(default_factory=list)  # (file_name, str(file_path))


class MockCorpPlmAdapter:
    """In-memory CorpPlmAdapter for tests. Implements the new 5-method Protocol.

    Counters per method invocation:
        adapter.calls["create_plm"], adapter.calls["close_plm"], etc.

    State helpers (test-only):
        adapter.seed_documents(plm_id, nodes)
        adapter.downloaded_files -> dict[(plm_id, file_id), Path]
    """

    def __init__(self, customer_id: str = "mock") -> None:
        self._customer_id = customer_id
        self._lock = asyncio.Lock()
        self._issues: dict[str, _MockPlmIssue] = {}
        self._counter = 0
        self.calls: dict[str, int] = {
            "create_plm": 0,
            "close_plm": 0,
            "get_documents_list": 0,
            "download_file": 0,
            "upload_file": 0,
        }
        self.downloaded_files: dict[tuple[str, str], Path] = {}

    @property
    def source_system(self) -> str:
        return self._customer_id

    # ------------------------------------------------------------------
    # Test helpers (NOT Protocol surface)
    # ------------------------------------------------------------------

    def seed_documents(self, plm_id: str, nodes: list[PlmDocumentNode]) -> None:
        if plm_id not in self._issues:
            raise ValueError(f"seed_documents: unknown plm_id {plm_id!r}")
        self._issues[plm_id].documents = list(nodes)

    # ------------------------------------------------------------------
    # Protocol surface
    # ------------------------------------------------------------------

    async def create_plm(
        self,
        model_number: str,
        tpm_corp_id: str,
        title: str,
        owner_corp_id: str,
        description: str,
    ) -> str | None:
        async with self._lock:
            self.calls["create_plm"] += 1
            self._counter += 1
            plm_id = f"MOCKPLM-{self._counter}"
            self._issues[plm_id] = _MockPlmIssue(
                plm_id=plm_id,
                model_number=model_number,
                tpm_corp_id=tpm_corp_id,
                title=title,
                owner_corp_id=owner_corp_id,
                description=description,
            )
            return plm_id

    async def close_plm(self, plm_id: str, tpm_corp_id: str) -> bool:
        async with self._lock:
            self.calls["close_plm"] += 1
            issue = self._issues.get(plm_id)
            if issue is None:
                return False
            issue.closed = True
            return True

    async def get_documents_list(
        self, plm_id: str, tpm_corp_id: str
    ) -> list[PlmDocumentNode]:
        async with self._lock:
            self.calls["get_documents_list"] += 1
            issue = self._issues.get(plm_id)
            if issue is None:
                return []
            return list(issue.documents)

    async def download_file(
        self,
        tpm_corp_id: str,
        document_id: str,
        document_name: str,
        file_id: str,
        download_path: Path,
    ) -> bool:
        async with self._lock:
            self.calls["download_file"] += 1
            # Locate the plm_id matching this file_id (best-effort for mock).
            owning_plm: str | None = None
            for plm_id, issue in self._issues.items():
                if any(n.file_id == file_id for n in issue.documents):
                    owning_plm = plm_id
                    break
            # Mock: don't actually write; just record the path that would be written.
            key = (owning_plm or "?", file_id)
            self.downloaded_files[key] = download_path
            return True

    async def upload_file(
        self, plm_id: str, file_name: str, file_path: Path
    ) -> bool:
        async with self._lock:
            self.calls["upload_file"] += 1
            issue = self._issues.get(plm_id)
            if issue is None:
                return False
            issue.uploaded.append((file_name, str(file_path)))
            return True


# ===========================================================================
# MockCustomerJiraAdapter
# ===========================================================================


class MockCustomerJiraAdapter:
    """In-memory CustomerJiraAdapter for tests. Implements Ph-1 informational
    read-only surface.

    Test helpers:
        adapter.seed_issue(JiraIssue(...))
        adapter.seed_comment(issue_key, JiraComment(...))
    """

    def __init__(self, customer_id: str = "mock") -> None:
        self._customer_id = customer_id
        self._lock = asyncio.Lock()
        self._issues: dict[str, JiraIssue] = {}
        self._comments: dict[str, list[JiraComment]] = {}
        self.calls: dict[str, int] = {
            "search_issues": 0,
            "get_issue": 0,
            "list_comments": 0,
        }

    @property
    def source_system(self) -> str:
        return self._customer_id

    def seed_issue(self, issue: JiraIssue) -> None:
        self._issues[issue.issue_key] = issue
        self._comments.setdefault(issue.issue_key, [])

    def seed_comment(self, issue_key: str, comment: JiraComment) -> None:
        self._comments.setdefault(issue_key, []).append(comment)

    async def search_issues(self, jql: str) -> list[JiraIssue]:
        async with self._lock:
            self.calls["search_issues"] += 1
            # Mock returns all seeded issues regardless of JQL (tests can filter).
            return list(self._issues.values())

    async def get_issue(self, issue_key: str) -> JiraIssue:
        async with self._lock:
            self.calls["get_issue"] += 1
            issue = self._issues.get(issue_key)
            if issue is None:
                raise PipelineError(
                    "ITR-E002",
                    context={"issue_id": issue_key, "system": f"customer_jira/{self.source_system}"},
                )
            return issue

    async def list_comments(self, issue_key: str) -> list[JiraComment]:
        async with self._lock:
            self.calls["list_comments"] += 1
            if issue_key not in self._issues:
                raise PipelineError(
                    "ITR-E002",
                    context={"issue_id": issue_key, "system": f"customer_jira/{self.source_system}"},
                )
            return list(self._comments.get(issue_key, []))
