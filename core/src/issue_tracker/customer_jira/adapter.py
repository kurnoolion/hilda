"""CustomerJiraAdapterBase -- standard JIRA REST httpx-backed adapter.

Per Module #13 cascade D4 + `[D-092]` Ph-1 lock 2026-06-25.

Ph-1 INFORMATIONAL READ-ONLY:
  - search_issues(jql) -> list[JiraIssue]
  - get_issue(issue_key) -> JiraIssue
  - list_comments(issue_key) -> list[JiraComment]

Ph-2 mutation surface (NOT exposed Ph-1; documented stubs raise ITR-E007):
  - create_issue, update_issue, add_comment, transition_issue,
    upload_attachment, download_attachment

Per-customer subclass at `customizations/issue_tracker/<customer_id>_customer_jira_adapter.py`
carries the customer JIRA base_url + per-customer auth config.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx

from core.src.diagnostics.error_codes import PipelineError
from core.src.issue_tracker.protocol import JiraComment, JiraIssue


def _parse_jira_datetime(s: str) -> datetime:
    """Parse JIRA's ISO 8601 datetime strings (`2026-05-08T10:00:00.000+0000`)."""
    if not s:
        return datetime.now(timezone.utc)
    s = re.sub(r"\.\d+([+-])", r"\1", s)
    s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
    s = s.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.now(timezone.utc)


@dataclass
class CustomerJiraConfig:
    """Per-customer JIRA connection config.

    Loaded by per-customer subclass at `customizations/issue_tracker/<customer_id>_customer_jira_adapter.py`.
    Credentials resolved via `credential_service.get_credential(pm_id, system_type=JIRA, customer_id=...)`
    per `[D-107]` -- NEVER stored on this config.
    """

    customer_id: str
    base_url: str
    project_key: str
    default_max_results: int = 50


class CustomerJiraAdapterBase:
    """Standard JIRA REST wrapper, Ph-1 informational read-only per `[D-092]`.

    Implements the `CustomerJiraAdapter` Protocol. Per-customer subclass at
    `customizations/issue_tracker/<customer_id>_customer_jira_adapter.py` supplies
    the config + per-call credentials.

    Constructor takes `httpx.BasicAuth` or `None` for unauthenticated test usage;
    in production, the subclass should resolve credentials via `credential_service`
    per `[D-107]` and pass an `httpx.BasicAuth` instance.
    """

    def __init__(
        self,
        config: CustomerJiraConfig,
        auth: httpx.BasicAuth | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._config = config
        self._http = http_client or httpx.AsyncClient(
            base_url=config.base_url,
            auth=auth,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=httpx.Timeout(30.0),
        )

    @property
    def source_system(self) -> str:
        return self._config.customer_id

    @property
    def project_key(self) -> str:
        return self._config.project_key

    # ------------------------------------------------------------------
    # Public Protocol surface (Ph-1 informational read-only)
    # ------------------------------------------------------------------

    async def search_issues(self, jql: str) -> list[JiraIssue]:
        resp = await self._http.post(
            "/rest/api/2/search",
            json={
                "jql": jql,
                "fields": ["summary", "status", "assignee", "updated", "project"],
                "maxResults": self._config.default_max_results,
            },
        )
        self._raise_for_jira_error(resp)
        return [self._parse_issue(item) for item in resp.json().get("issues", [])]

    async def get_issue(self, issue_key: str) -> JiraIssue:
        resp = await self._http.get(f"/rest/api/2/issue/{issue_key}")
        self._raise_for_jira_error(resp, issue_key)
        return self._parse_issue(resp.json())

    async def list_comments(self, issue_key: str) -> list[JiraComment]:
        resp = await self._http.get(f"/rest/api/2/issue/{issue_key}/comment")
        self._raise_for_jira_error(resp, issue_key)
        return [self._parse_comment(c) for c in resp.json().get("comments", [])]

    # ------------------------------------------------------------------
    # Ph-2 mutation stubs (raise ITR-E007 per [D-092] Ph-1 lock)
    # ------------------------------------------------------------------

    async def create_issue(self, *_args: Any, **_kw: Any) -> None:
        raise PipelineError(
            "ITR-E007",
            context={"operation": "create_issue", "adapter": f"customer_jira/{self.source_system}"},
        )

    async def update_issue(self, *_args: Any, **_kw: Any) -> None:
        raise PipelineError(
            "ITR-E007",
            context={"operation": "update_issue", "adapter": f"customer_jira/{self.source_system}"},
        )

    async def add_comment(self, *_args: Any, **_kw: Any) -> None:
        raise PipelineError(
            "ITR-E007",
            context={"operation": "add_comment", "adapter": f"customer_jira/{self.source_system}"},
        )

    async def transition_issue(self, *_args: Any, **_kw: Any) -> None:
        raise PipelineError(
            "ITR-E007",
            context={"operation": "transition_issue", "adapter": f"customer_jira/{self.source_system}"},
        )

    async def upload_attachment(self, *_args: Any, **_kw: Any) -> None:
        raise PipelineError(
            "ITR-E007",
            context={"operation": "upload_attachment", "adapter": f"customer_jira/{self.source_system}"},
        )

    async def download_attachment(self, *_args: Any, **_kw: Any) -> None:
        raise PipelineError(
            "ITR-E007",
            context={"operation": "download_attachment", "adapter": f"customer_jira/{self.source_system}"},
        )

    # ------------------------------------------------------------------
    # Internal parsers
    # ------------------------------------------------------------------

    def _parse_issue(self, data: dict) -> JiraIssue:
        fields = data.get("fields", {})
        status = (fields.get("status") or {}).get("name", "") or ""
        assignee_obj = fields.get("assignee") or {}
        owner_corp_email = assignee_obj.get("emailAddress")
        project_key = (fields.get("project") or {}).get("key") or self._config.project_key
        return JiraIssue(
            issue_key=data.get("key", ""),
            summary=fields.get("summary", ""),
            status=status,
            owner_corp_email=owner_corp_email,
            last_updated=_parse_jira_datetime(fields.get("updated", "")),
            project_key=project_key,
            raw=data,
        )

    def _parse_comment(self, data: dict) -> JiraComment:
        author = (data.get("author") or {})
        return JiraComment(
            comment_id=str(data.get("id", "")),
            author_corp_email=author.get("emailAddress"),
            body=data.get("body", ""),
            created_at=_parse_jira_datetime(data.get("created", "")),
        )

    def _raise_for_jira_error(self, resp: httpx.Response, issue_id: str = "") -> None:
        if resp.status_code == 401:
            raise PipelineError(
                "ITR-E003",
                context={"account_id": "<redacted>", "customer_id": self.source_system},
            )
        if resp.status_code == 404:
            raise PipelineError(
                "ITR-E002", context={"issue_id": issue_id, "system": f"customer_jira/{self.source_system}"}
            )
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "60")
            raise PipelineError(
                "ITR-W001", context={"system": f"customer_jira/{self.source_system}", "retry_after_s": retry_after}
            )
        resp.raise_for_status()
