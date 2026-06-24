"""IssueTracker Protocols and data classes.

Two surface-distinct Protocols per Module #13 strict-order cascade 2026-06-25:

- `CorpPlmAdapter` -- thin wrapper over the 5 already-available corp PLM APIs per
  `[D-027]` Teacher/Student split (HILDA-side abstract `_invoke_*` methods + per-
  customer concrete bodies at `customizations/issue_tracker/<customer_id>_corp_plm_adapter.py`).
- `CustomerJiraAdapter` -- standard JIRA REST. Ph-1 INFORMATIONAL POLLING ONLY
  per `[D-092]`; Ph-2 will add SP write-back.

The legacy generic `IssueTracker` Protocol + the original dataclasses
(`IssueRef`, `Issue`, `IssueChange`, `IssueQuery`, `CommentRef`, `AttachmentRef`,
`WebhookRef`, `IssueStatus`, `IssuePriority`) are RETAINED Ph-1 for back-compat
with `jira_adapter.py`, `mock_adapter.py`, and `customizations/issue_tracker/
defecttrack_adapter.py`. They are **slated for removal Ph-2** once those adapters
are rewired onto the two new Protocols.

Anchors `[D-027]`, `[D-092]`, `[D-094]` SUPERSEDED, `[D-098]`, `[D-105]`, `[D-117]`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, AsyncIterable, AsyncIterator, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Cross-module primitive (preserved -- messenger imports from here)
# ---------------------------------------------------------------------------
AttachmentInput = Path | AsyncIterable[bytes]


# ===========================================================================
# NEW Protocols (Module #13 cascade 2026-06-25)
# ===========================================================================


@dataclass(frozen=True)
class PlmDocumentNode:
    """One entry returned by `getdocumentslist` per Q7 architect spec 2026-06-25.

    Both `document_id` and `file_id` persist on HILDA's `DocumentIndexRow` per file;
    both are required for `downloadFile`. No derivation between them.
    """

    document_id: str
    document_name: str
    file_id: str


@dataclass(frozen=True)
class JiraIssue:
    """Ph-1 informational fields only per `[D-092]`. `raw` carries any customer-
    specific JIRA fields for forward-looking Ph-2 use (no schema migration needed)."""

    issue_key: str
    summary: str
    status: str                       # JIRA status string verbatim; HILDA does not normalize Ph-1
    owner_corp_email: str | None      # JIRA assignee → corp email lookup per [D-105]
    last_updated: datetime
    project_key: str
    raw: dict = field(default_factory=dict)


@dataclass(frozen=True)
class JiraComment:
    """Ph-1 informational. author_corp_email keyed on JIRA author → corp email
    mapping per `[D-105]` 4-field identity."""

    comment_id: str
    author_corp_email: str | None
    body: str
    created_at: datetime


@runtime_checkable
class CorpPlmAdapter(Protocol):
    """Thin wrapper around the 5 already-available corp PLM APIs per `[D-027]`.

    The 5 underlying APIs (architect spec 2026-06-25):
        str   createPLM(Model_Number, tpm_corp_id, title, owner_corp_id, description)
                                                              -> plm_id, None on failure
        bool  closePLM(plm_id, tpm_corp_id)                   -> True on success
        list  getdocumentslist(plm_id, tpm_corp_id)           -> list of nodes
        bool  downloadFile(tpm_corp_id, document_id, document_name, file_id, downloadPath)
                                                              -> True on success
        bool  uploadFile(plm_id, file_name, file_path)        -> True on success

    HILDA passes `tpm_corp_id` as ACTION ATTRIBUTION (NOT credential per `[D-019]`);
    actual auth flows via `corp_plm_gateway` PC. Per-customer concrete body filled
    in by Cline on Work PC at `customizations/issue_tracker/<customer_id>_corp_plm_adapter.py`;
    never lands on public github per NFR-2.
    """

    source_system: str   # immutable; equals customer_id per [D-091]

    async def create_plm(
        self,
        model_number: str,
        tpm_corp_id: str,
        title: str,
        owner_corp_id: str,
        description: str,
    ) -> str | None:
        """Returns plm_id; None on failure (opaque ITR-E001 per Q5).

        Caller writes plm_id back to Deliverables_<customer_id> SP row via SpClient
        digest-dance per `[D-117]`. NOT called directly here; happens in workflow_engine
        ActionKind.
        """
        ...

    async def close_plm(self, plm_id: str, tpm_corp_id: str) -> bool:
        """Returns True on success; False on failure. Idempotent -- closing an
        already-closed PLM returns True without error. Called from
        workflow_engine.tasks/milestone.py FINAL_SWEEP ActionKind per Q2."""
        ...

    async def get_documents_list(
        self, plm_id: str, tpm_corp_id: str,
    ) -> list[PlmDocumentNode]:
        """Returns the list of {document_id, document_name, file_id} nodes for
        the PLM issue. Called by CorpPlmPoller per FR-26 polling cycle."""
        ...

    async def download_file(
        self,
        tpm_corp_id: str,
        document_id: str,
        document_name: str,
        file_id: str,
        download_path: Path,
    ) -> bool:
        """Downloads one file to download_path. Returns True on success.

        Caller (poller) wraps this in `InFlightDownloadTracker.acquire((plm_id, file_id))`
        per Q6 to prevent duplicate concurrent downloads.
        """
        ...

    async def upload_file(
        self, plm_id: str, file_name: str, file_path: Path,
    ) -> bool:
        """Uploads file_path to PLM issue plm_id. Returns True on success;
        ITR-W005 on post-upload verification failure per `[D-098]` narrowing
        (FR-68 hash-match dropped; UI file-exists check sufficient)."""
        ...


@runtime_checkable
class CustomerJiraAdapter(Protocol):
    """Standard JIRA REST API. Ph-1 INFORMATIONAL POLLING ONLY per `[D-092]`.

    HILDA polls customer JIRA for waiver/issue tickets and surfaces ticket URLs in
    FR-9 outreach attachments via in-memory state. Close-intent flows via email
    reply path per FR-25 (b) + FR-12. NO SP write-back Ph-1.

    Ph-2 will add SP write-back to Deliverables_<customer_id> columns:
      - jira_open_ticket_count (int)
      - jira_ticket_summary_json (JSON-as-string)
    and the Ph-2 mutation surface (`create_issue`, `update_issue`, `add_comment`,
    `transition_issue`, `upload_attachment`, `download_attachment`).
    """

    source_system: str   # immutable; equals customer_id per [D-091]

    async def search_issues(self, jql: str) -> list[JiraIssue]: ...
    async def get_issue(self, issue_key: str) -> JiraIssue: ...
    async def list_comments(self, issue_key: str) -> list[JiraComment]: ...


# ===========================================================================
# LEGACY surfaces (Ph-1 back-compat; slated for Ph-2 removal)
# ===========================================================================


@dataclass(frozen=True)
class IssueRef:
    """LEGACY -- used by `IssueTracker` Protocol + `jira_adapter` + `mock_adapter`
    + `defecttrack_adapter`. Slated for Ph-2 removal."""

    issue_id: str
    source_system: str
    url: str


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
    field: str
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
    """LEGACY generic Protocol -- retained Ph-1 for back-compat (`jira_adapter`,
    `mock_adapter`, `defecttrack_adapter`). Slated for Ph-2 removal once those
    adapters rewire onto `CorpPlmAdapter` / `CustomerJiraAdapter`."""

    source_system: str

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
