"""issue_tracker — IssueTracker Protocol + adapters. Anchors [D-003] [D-008].

See core/src/issue_tracker/MODULE.md.
"""
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
    IssueTracker,
    WebhookRef,
)

__all__ = [
    "AttachmentInput",
    "AttachmentRef",
    "CommentRef",
    "Issue",
    "IssueChange",
    "IssueQuery",
    "IssueRef",
    "IssuePriority",
    "IssueStatus",
    "IssueTracker",
    "WebhookRef",
]
