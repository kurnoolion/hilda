"""MockIssueTracker — in-memory IssueTracker for unit and integration tests."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterable, AsyncIterator

from core.src.diagnostics.error_codes import PipelineError
from core.src.issue_tracker.protocol import (
    AttachmentInput,
    AttachmentRef,
    CommentRef,
    Issue,
    IssueChange,
    IssueQuery,
    IssueRef,
    IssuePriority,
    IssueStatus,
    WebhookRef,
)

_TRANSITION_MAP: dict[str, IssueStatus] = {
    "start": IssueStatus.IN_PROGRESS,
    "begin": IssueStatus.IN_PROGRESS,
    "resolve": IssueStatus.RESOLVED,
    "done": IssueStatus.CLOSED,
    "close": IssueStatus.CLOSED,
    "reopen": IssueStatus.OPEN,
    "reopen_issue": IssueStatus.OPEN,
}


@dataclass
class _IssueRecord:
    issue: Issue
    project: str
    comments: list[tuple[str, str]]    # (comment_id, body)
    attachments: list[AttachmentRef]
    idempotency_key: str | None


class MockIssueTracker:
    """In-memory IssueTracker for unit and integration tests.

    Implements the full IssueTracker Protocol surface. All mutating methods
    honour idempotency_key (dedup within process lifetime). close_issue is
    idempotent. Uses asyncio.Lock for async-safety.
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._records: dict[str, _IssueRecord] = {}
        self._idempotency: dict[str, str] = {}          # key → issue_id
        self._comment_idempotency: dict[str, str] = {}  # key → comment_id
        self._changes: list[IssueChange] = []
        self._counter = 0

    @property
    def source_system(self) -> str:
        return "mock"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _next_id(self, prefix: str = "ISS") -> str:
        self._counter += 1
        return f"{prefix}-{self._counter}"

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc)

    def _get_record(self, ref: IssueRef) -> _IssueRecord:
        record = self._records.get(ref.issue_id)
        if record is None:
            raise PipelineError(
                "ITR-E002",
                context={"issue_id": ref.issue_id, "system": "mock"},
            )
        return record

    def _record_change(
        self,
        ref: IssueRef,
        field: str,
        old_value: str | None,
        new_value: str | None,
    ) -> None:
        self._changes.append(
            IssueChange(
                issue_ref=ref,
                field=field,
                old_value=old_value,
                new_value=new_value,
                changed_at=self._now(),
                changed_by=None,
            )
        )

    # ------------------------------------------------------------------
    # Protocol implementation
    # ------------------------------------------------------------------

    async def create_issue(
        self,
        project: str,
        summary: str,
        description: str,
        fields: dict | None = None,
        attachments: list[AttachmentInput] | None = None,
        idempotency_key: str | None = None,
        timeout_s: float | None = None,
    ) -> IssueRef:
        async with self._lock:
            if idempotency_key and idempotency_key in self._idempotency:
                existing_id = self._idempotency[idempotency_key]
                return self._records[existing_id].issue.ref

            issue_id = self._next_id()
            now = self._now()
            ref = IssueRef(
                issue_id=issue_id,
                source_system=self.source_system,
                url=f"mock://issues/{issue_id}",
            )
            priority = None
            if fields and "priority" in fields:
                priority = fields["priority"]

            issue = Issue(
                ref=ref,
                summary=summary,
                description=description or None,
                status=IssueStatus.OPEN,
                priority=priority,
                assignee=None,
                labels=[],
                created_at=now,
                updated_at=now,
            )
            self._records[issue_id] = _IssueRecord(
                issue=issue,
                project=project,
                comments=[],
                attachments=[],
                idempotency_key=idempotency_key,
            )
            if idempotency_key:
                self._idempotency[idempotency_key] = issue_id
            self._record_change(ref, "created", None, issue_id)
            return ref

    async def get_issue(
        self, ref: IssueRef, timeout_s: float | None = None
    ) -> Issue:
        async with self._lock:
            record = self._get_record(ref)
            return deepcopy(record.issue)

    async def update_issue(
        self, ref: IssueRef, updates: dict, timeout_s: float | None = None
    ) -> None:
        async with self._lock:
            record = self._get_record(ref)
            issue = record.issue
            for key, value in updates.items():
                old = getattr(issue, key, None)
                old_str = str(old) if old is not None else None
                setattr(issue, key, value)
                self._record_change(ref, key, old_str, str(value))
            issue.updated_at = self._now()

    async def transition_issue(
        self, ref: IssueRef, transition: str, timeout_s: float | None = None
    ) -> None:
        async with self._lock:
            record = self._get_record(ref)
            new_status = _TRANSITION_MAP.get(transition.lower())
            if new_status is None:
                raise PipelineError(
                    "ITR-E004",
                    context={"transition": transition, "issue_id": ref.issue_id},
                )
            old_status = record.issue.status
            record.issue.status = new_status
            record.issue.updated_at = self._now()
            self._record_change(ref, "status", str(old_status.value), str(new_status.value))

    async def close_issue(
        self, ref: IssueRef, resolution: str, timeout_s: float | None = None
    ) -> None:
        async with self._lock:
            record = self._get_record(ref)
            if record.issue.status == IssueStatus.CLOSED:
                return  # idempotent
            old_status = record.issue.status
            record.issue.status = IssueStatus.CLOSED
            record.issue.updated_at = self._now()
            self._record_change(
                ref, "status", str(old_status.value), str(IssueStatus.CLOSED.value)
            )

    async def add_comment(
        self,
        ref: IssueRef,
        body: str,
        attachments: list[AttachmentInput] | None = None,
        idempotency_key: str | None = None,
        timeout_s: float | None = None,
    ) -> CommentRef:
        async with self._lock:
            if idempotency_key and idempotency_key in self._comment_idempotency:
                existing_id = self._comment_idempotency[idempotency_key]
                return CommentRef(
                    comment_id=existing_id,
                    issue_ref=ref,
                    source_system=self.source_system,
                )
            record = self._get_record(ref)
            comment_id = self._next_id("CMT")
            record.comments.append((comment_id, body))
            record.issue.updated_at = self._now()
            if idempotency_key:
                self._comment_idempotency[idempotency_key] = comment_id
            self._record_change(ref, "comment_added", None, comment_id)
            return CommentRef(
                comment_id=comment_id,
                issue_ref=ref,
                source_system=self.source_system,
            )

    async def upload_attachment(
        self, ref: IssueRef, file: AttachmentInput, timeout_s: float | None = None
    ) -> AttachmentRef:
        async with self._lock:
            record = self._get_record(ref)
            attachment_id = self._next_id("ATT")

            if isinstance(file, Path):
                filename = file.name
            else:
                async for _ in file:  # type: ignore[union-attr]
                    pass
                filename = "attachment"

            aref = AttachmentRef(
                attachment_id=attachment_id,
                issue_ref=ref,
                filename=filename,
                source_system=self.source_system,
            )
            record.attachments.append(aref)
            record.issue.updated_at = self._now()
            return aref

    async def search(
        self, query: IssueQuery, timeout_s: float | None = None
    ) -> AsyncIterator[IssueRef]:
        async with self._lock:
            matched: list[IssueRef] = []
            for record in self._records.values():
                issue = record.issue
                if query.project and record.project != query.project:
                    continue
                if query.status and issue.status != query.status:
                    continue
                if query.assignee and issue.assignee != query.assignee:
                    continue
                if query.updated_after and issue.updated_at < query.updated_after:
                    continue
                if query.labels and not all(lbl in issue.labels for lbl in query.labels):
                    continue
                matched.append(issue.ref)

        async def _gen() -> AsyncIterator[IssueRef]:
            for item in matched:
                yield item

        return _gen()

    async def list_recent_changes(
        self, ref: IssueRef, since: datetime, timeout_s: float | None = None
    ) -> AsyncIterator[IssueChange]:
        async with self._lock:
            self._get_record(ref)  # validate ref exists
            matched = [
                c
                for c in self._changes
                if c.issue_ref.issue_id == ref.issue_id and c.changed_at >= since
            ]

        async def _gen() -> AsyncIterator[IssueChange]:
            for change in matched:
                yield change

        return _gen()

    async def register_webhook(
        self,
        callback_url: str,
        events: list[str],
        secret: str,
        timeout_s: float | None = None,
    ) -> WebhookRef:
        async with self._lock:
            webhook_id = self._next_id("WH")
            return WebhookRef(
                webhook_id=webhook_id,
                source_system=self.source_system,
                callback_url=callback_url,
            )

    async def poll_changes(
        self, since: datetime, timeout_s: float | None = None
    ) -> AsyncIterator[IssueChange]:
        async with self._lock:
            matched = [c for c in self._changes if c.changed_at >= since]

        async def _gen() -> AsyncIterator[IssueChange]:
            for change in matched:
                yield change

        return _gen()
