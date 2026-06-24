"""issue_tracker — IssueTracker Protocols + adapters.

Per Module #13 cascade 2026-06-25: two surface-distinct Protocols (CorpPlmAdapter
+ CustomerJiraAdapter) per Q1-Q7 architect lock. Legacy generic IssueTracker
Protocol + dataclasses retained Ph-1 for back-compat (jira_adapter, mock_adapter,
defecttrack_adapter); slated for Ph-2 removal.

Anchors `[D-027]`, `[D-091]`, `[D-092]`, `[D-094]` SUPERSEDED, `[D-098]`,
`[D-105]`, `[D-106]` DROPPED, `[D-117]`. See core/src/issue_tracker/MODULE.md.
"""
# New Protocols + dataclasses (Module #13 cascade 2026-06-25)
from core.src.issue_tracker.protocol import (
    CorpPlmAdapter,
    CustomerJiraAdapter,
    JiraComment,
    JiraIssue,
    PlmDocumentNode,
)
from core.src.issue_tracker.utils import derive_tpm_corp_id
from core.src.issue_tracker.corp_plm.adapter import CorpPlmAdapterBase
from core.src.issue_tracker.corp_plm.in_flight_tracker import InFlightDownloadTracker
from core.src.issue_tracker.corp_plm.poller import (
    ActivePollTarget,
    CorpPlmPoller,
    DEFAULT_POLLING_LADDER,
    PollingTier,
    TriggerEmitter,
    resolve_polling_interval,
)
from core.src.issue_tracker.customer_jira.adapter import (
    CustomerJiraAdapterBase,
    CustomerJiraConfig,
)
from core.src.issue_tracker.customer_jira.poller import CustomerJiraPoller
from core.src.issue_tracker.mocks import (
    MockCorpPlmAdapter,
    MockCustomerJiraAdapter,
)

# Legacy Ph-1 back-compat surfaces (slated for Ph-2 removal)
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
    # New (Module #13 cascade 2026-06-25)
    "CorpPlmAdapter",
    "CorpPlmAdapterBase",
    "CustomerJiraAdapter",
    "CustomerJiraAdapterBase",
    "CustomerJiraConfig",
    "JiraComment",
    "JiraIssue",
    "PlmDocumentNode",
    "derive_tpm_corp_id",
    "InFlightDownloadTracker",
    "CorpPlmPoller",
    "ActivePollTarget",
    "DEFAULT_POLLING_LADDER",
    "PollingTier",
    "TriggerEmitter",
    "resolve_polling_interval",
    "CustomerJiraPoller",
    "MockCorpPlmAdapter",
    "MockCustomerJiraAdapter",
    # Legacy (Ph-1 back-compat)
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
