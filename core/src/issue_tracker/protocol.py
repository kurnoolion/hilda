"""IssueTracker Protocol and all shared data classes. No IO, no network. Anchors [D-008]."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import AsyncIterable, AsyncIterator, Protocol

# Cross-module primitive — re-exported from core.src.issue_tracker;
# messenger imports AttachmentInput from here to avoid a premature shared module.
AttachmentInput = Path | AsyncIterable[bytes]


@dataclass(frozen=True)
class IssueRef:
    issue_id: str
    source_system: str  # immutable adapter slug, e.g. "jira", "mock"
    url: str            # direct link to the issue in the external system


class IssueStatus(str, Enum):
    OPEN = "Open"
    IN_PROGRESS = "InProgress"
    RESOLVED = "Resolved"
    CLOSED = "Closed"


class IssuePriority(str, Enum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"
    CRITICAL = "Critical"


@dataclass
class Issue:
    ref: IssueRef
    summary: str
    description: str | None
    status: IssueStatus
    priority: IssuePriority | None
    assignee: str | None
    labels: list[str]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class IssueChange:
    issue_ref: IssueRef
    field: str          # e.g. "status", "assignee", "comment_added", "created"
    old_value: str | None
    new_value: str | None
    changed_at: datetime
    changed_by: str | None


@dataclass
class IssueQuery:
    project: str | None = None
    status: IssueStatus | None = None
    updated_after: datetime | None = None
    assignee: str | None = None
    labels: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class CommentRef:
    comment_id: str
    issue_ref: IssueRef
    source_system: str


@dataclass(frozen=True)
class AttachmentRef:
    attachment_id: str
    issue_ref: IssueRef
    filename: str
    source_system: str


@dataclass(frozen=True)
class WebhookRef:
    webhook_id: str
    source_system: str
    callback_url: str


class IssueTracker(Protocol):
    """Async-native Protocol for all issue-tracker adapters. Anchors [D-008]."""

    source_system: str  # immutable slug set at adapter construction

    async def create_issue(
        self,
        project: str,
        summary: str,
        description: str,
        fields: dict | None = None,
        attachments: list[AttachmentInput] | None = None,
        idempotency_key: str | None = None,
        timeout_s: float | None = None,
    ) -> IssueRef: ...

    async def get_issue(
        self, ref: IssueRef, timeout_s: float | None = None
    ) -> Issue: ...

    async def update_issue(
        self, ref: IssueRef, updates: dict, timeout_s: float | None = None
    ) -> None: ...

    async def transition_issue(
        self, ref: IssueRef, transition: str, timeout_s: float | None = None
    ) -> None: ...

    async def close_issue(
        self, ref: IssueRef, resolution: str, timeout_s: float | None = None
    ) -> None: ...

    async def add_comment(
        self,
        ref: IssueRef,
        body: str,
        attachments: list[AttachmentInput] | None = None,
        idempotency_key: str | None = None,
        timeout_s: float | None = None,
    ) -> CommentRef: ...

    async def upload_attachment(
        self, ref: IssueRef, file: AttachmentInput, timeout_s: float | None = None
    ) -> AttachmentRef: ...

    async def search(
        self, query: IssueQuery, timeout_s: float | None = None
    ) -> AsyncIterator[IssueRef]: ...

    async def list_recent_changes(
        self, ref: IssueRef, since: datetime, timeout_s: float | None = None
    ) -> AsyncIterator[IssueChange]: ...

    async def register_webhook(
        self,
        callback_url: str,
        events: list[str],
        secret: str,
        timeout_s: float | None = None,
    ) -> WebhookRef: ...

    async def poll_changes(
        self, since: datetime, timeout_s: float | None = None
    ) -> AsyncIterator[IssueChange]: ...
