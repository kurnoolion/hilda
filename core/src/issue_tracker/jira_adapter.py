"""JiraAdapter — IssueTracker Protocol implementation against Jira REST API v2."""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, AsyncIterable, AsyncIterator, Literal

import httpx

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


def _parse_jira_datetime(s: str) -> datetime:
    """Parse Jira's ISO 8601 datetime strings, e.g. '2026-05-08T10:00:00.000+0000'."""
    if not s:
        return datetime.now(timezone.utc)
    # Normalize +HHmm → +HH:mm and strip sub-second precision before offset
    s = re.sub(r'\.\d+([+-])', r'\1', s)
    s = re.sub(r'([+-]\d{2})(\d{2})$', r'\1:\2', s)
    s = s.replace('Z', '+00:00')
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        return datetime.now(timezone.utc)


@dataclass
class JiraAdapterConfig:
    base_url: str
    auth_type: Literal["api_token", "basic"]
    project_key: str
    # IssueStatus.value → Jira status name, e.g. {"Open": "To Do", "InProgress": "In Progress"}
    status_map: dict[str, str] = field(default_factory=dict)
    # HILDA canonical verb → Jira transition name, e.g. {"start": "In Progress", "resolve": "Done"}
    transition_map: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_env(cls, prefix: str = "HILDA_JIRA_") -> "JiraAdapterConfig":
        """Read config from environment. Raises ITR-E006 if required vars are missing."""
        import json

        def _require(name: str) -> str:
            val = os.environ.get(f"{prefix}{name}")
            if not val:
                raise PipelineError(
                    "ITR-E006",
                    context={"slug": f"jira (missing env var {prefix}{name})"},
                )
            return val

        status_map_raw = os.environ.get(f"{prefix}STATUS_MAP", "{}")
        transition_map_raw = os.environ.get(f"{prefix}TRANSITION_MAP", "{}")

        return cls(
            base_url=_require("BASE_URL").rstrip("/"),
            auth_type=_require("AUTH_TYPE"),  # type: ignore[arg-type]
            project_key=_require("PROJECT_KEY"),
            status_map=json.loads(status_map_raw),
            transition_map=json.loads(transition_map_raw),
        )


class JiraAdapter:
    """Implements IssueTracker Protocol against Jira REST API v2.

    Credentials are supplied at construction via httpx.BasicAuth — never stored
    beyond the adapter's lifetime. For production use, call from_credential_service()
    to wire in credential_service. Idempotency keys are deduped in-memory per
    process lifetime (sufficient for worker restart tolerance; full persistence v2).
    """

    def __init__(self, config: JiraAdapterConfig, auth: httpx.BasicAuth) -> None:
        self._config = config
        self._http = httpx.AsyncClient(
            base_url=config.base_url,
            auth=auth,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-Atlassian-Token": "no-check",  # required for attachment uploads
            },
            timeout=httpx.Timeout(30.0),
        )
        self._idempotency: dict[str, IssueRef] = {}
        self._comment_idempotency: dict[str, CommentRef] = {}
        self._reverse_status_map: dict[str, IssueStatus] = {
            v: IssueStatus(k) for k, v in config.status_map.items() if k in IssueStatus._value2member_map_
        }

    @property
    def source_system(self) -> str:
        return "jira"

    @classmethod
    def from_credential_service(
        cls, config: JiraAdapterConfig, credential_service: Any, pm_id: str
    ) -> "JiraAdapter":
        cred = credential_service.get_credential(pm_id, "issue_tracker")
        auth = httpx.BasicAuth(cred.username, cred.secret)
        return cls(config, auth)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _jira_status(self, hilda_status: IssueStatus) -> str:
        return self._config.status_map.get(hilda_status.value, hilda_status.value)

    def _hilda_status(self, jira_status_name: str) -> IssueStatus:
        return self._reverse_status_map.get(jira_status_name, IssueStatus.OPEN)

    def _parse_issue(self, data: dict) -> Issue:
        fields = data.get("fields", {})
        jira_status = (fields.get("status") or {}).get("name", "")
        jira_priority = (fields.get("priority") or {}).get("name")
        assignee_obj = fields.get("assignee") or {}

        priority: IssuePriority | None = None
        if jira_priority:
            try:
                priority = IssuePriority(jira_priority)
            except ValueError:
                pass

        ref = IssueRef(
            issue_id=data["key"],
            source_system=self.source_system,
            url=data.get("self", ""),
        )
        return Issue(
            ref=ref,
            summary=fields.get("summary", ""),
            description=fields.get("description"),
            status=self._hilda_status(jira_status),
            priority=priority,
            assignee=assignee_obj.get("displayName"),
            labels=fields.get("labels", []),
            created_at=_parse_jira_datetime(fields.get("created", "")),
            updated_at=_parse_jira_datetime(fields.get("updated", "")),
        )

    def _raise_for_jira_error(self, resp: httpx.Response, issue_id: str = "") -> None:
        if resp.status_code == 401:
            raise PipelineError("ITR-E001", context={"system": "jira"})
        if resp.status_code == 404:
            raise PipelineError(
                "ITR-E002", context={"issue_id": issue_id, "system": "jira"}
            )
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After", "60")
            raise PipelineError(
                "ITR-W001", context={"system": "jira", "retry_after_s": retry_after}
            )
        resp.raise_for_status()

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
        if idempotency_key and idempotency_key in self._idempotency:
            return self._idempotency[idempotency_key]

        body: dict = {
            "fields": {
                "project": {"key": project},
                "summary": summary,
                "description": description,
                "issuetype": {"name": "Task"},
            }
        }
        if fields:
            body["fields"].update(fields)

        resp = await self._http.post("/rest/api/2/issue", json=body)
        self._raise_for_jira_error(resp)
        data = resp.json()
        ref = IssueRef(
            issue_id=data["key"],
            source_system=self.source_system,
            url=data.get("self", ""),
        )
        if idempotency_key:
            self._idempotency[idempotency_key] = ref
        return ref

    async def get_issue(
        self, ref: IssueRef, timeout_s: float | None = None
    ) -> Issue:
        resp = await self._http.get(f"/rest/api/2/issue/{ref.issue_id}")
        self._raise_for_jira_error(resp, ref.issue_id)
        return self._parse_issue(resp.json())

    async def update_issue(
        self, ref: IssueRef, updates: dict, timeout_s: float | None = None
    ) -> None:
        jira_fields: dict = {}
        for key, value in updates.items():
            if key == "summary":
                jira_fields["summary"] = value
            elif key == "description":
                jira_fields["description"] = value
            elif key == "priority":
                jira_fields["priority"] = {"name": value.value if isinstance(value, IssuePriority) else value}
            elif key == "assignee":
                jira_fields["assignee"] = {"name": value} if value else None
            elif key == "labels":
                jira_fields["labels"] = value

        resp = await self._http.put(
            f"/rest/api/2/issue/{ref.issue_id}",
            json={"fields": jira_fields},
        )
        self._raise_for_jira_error(resp, ref.issue_id)

    async def transition_issue(
        self, ref: IssueRef, transition: str, timeout_s: float | None = None
    ) -> None:
        jira_transition_name = self._config.transition_map.get(transition, transition)

        # Get available transitions
        resp = await self._http.get(f"/rest/api/2/issue/{ref.issue_id}/transitions")
        self._raise_for_jira_error(resp, ref.issue_id)
        transitions = resp.json().get("transitions", [])

        # Match by name or ID
        transition_id = None
        for t in transitions:
            if t.get("name") == jira_transition_name or t.get("id") == jira_transition_name:
                transition_id = t["id"]
                break

        if transition_id is None:
            raise PipelineError(
                "ITR-E004",
                context={"transition": transition, "issue_id": ref.issue_id},
            )

        resp = await self._http.post(
            f"/rest/api/2/issue/{ref.issue_id}/transitions",
            json={"transition": {"id": transition_id}},
        )
        self._raise_for_jira_error(resp, ref.issue_id)

    async def close_issue(
        self, ref: IssueRef, resolution: str, timeout_s: float | None = None
    ) -> None:
        # Check current state first — idempotent if already closed
        try:
            issue = await self.get_issue(ref)
            if issue.status == IssueStatus.CLOSED:
                return
        except PipelineError:
            pass

        jira_close_name = self._config.transition_map.get("close", "Done")
        resp = await self._http.get(f"/rest/api/2/issue/{ref.issue_id}/transitions")
        self._raise_for_jira_error(resp, ref.issue_id)
        transitions = resp.json().get("transitions", [])

        transition_id = None
        for t in transitions:
            if t.get("name") == jira_close_name or t.get("id") == jira_close_name:
                transition_id = t["id"]
                break

        if transition_id is None:
            # Already closed or no applicable transition — treat as idempotent
            return

        body: dict = {"transition": {"id": transition_id}}
        if resolution:
            body["fields"] = {"resolution": {"name": resolution}}

        resp = await self._http.post(
            f"/rest/api/2/issue/{ref.issue_id}/transitions", json=body
        )
        self._raise_for_jira_error(resp, ref.issue_id)

    async def add_comment(
        self,
        ref: IssueRef,
        body: str,
        attachments: list[AttachmentInput] | None = None,
        idempotency_key: str | None = None,
        timeout_s: float | None = None,
    ) -> CommentRef:
        if idempotency_key and idempotency_key in self._comment_idempotency:
            return self._comment_idempotency[idempotency_key]

        resp = await self._http.post(
            f"/rest/api/2/issue/{ref.issue_id}/comment",
            json={"body": body},
        )
        self._raise_for_jira_error(resp, ref.issue_id)
        data = resp.json()
        cref = CommentRef(
            comment_id=str(data["id"]),
            issue_ref=ref,
            source_system=self.source_system,
        )
        if idempotency_key:
            self._comment_idempotency[idempotency_key] = cref
        return cref

    async def upload_attachment(
        self, ref: IssueRef, file: AttachmentInput, timeout_s: float | None = None
    ) -> AttachmentRef:
        if isinstance(file, Path):
            filename = file.name
            content = file.read_bytes()
        else:
            chunks = []
            async for chunk in file:  # type: ignore[union-attr]
                chunks.append(chunk)
            content = b"".join(chunks)
            filename = "attachment"

        # Jira requires multipart/form-data with no CSRF header
        files = {"file": (filename, content, "application/octet-stream")}
        resp = await self._http.post(
            f"/rest/api/2/issue/{ref.issue_id}/attachments",
            files=files,
            headers={"X-Atlassian-Token": "no-check"},
        )
        self._raise_for_jira_error(resp, ref.issue_id)
        data = resp.json()
        first = data[0] if isinstance(data, list) else data
        return AttachmentRef(
            attachment_id=str(first.get("id", uuid.uuid4())),
            issue_ref=ref,
            filename=first.get("filename", filename),
            source_system=self.source_system,
        )

    async def search(
        self, query: IssueQuery, timeout_s: float | None = None
    ) -> AsyncIterator[IssueRef]:
        jql_parts: list[str] = []
        if query.project:
            jql_parts.append(f'project = "{query.project}"')
        if query.status:
            jira_status = self._jira_status(query.status)
            jql_parts.append(f'status = "{jira_status}"')
        if query.assignee:
            jql_parts.append(f'assignee = "{query.assignee}"')
        if query.updated_after:
            ts = query.updated_after.strftime("%Y-%m-%d %H:%M")
            jql_parts.append(f'updated >= "{ts}"')
        for label in query.labels:
            jql_parts.append(f'labels = "{label}"')

        jql = " AND ".join(jql_parts) if jql_parts else "ORDER BY created DESC"
        resp = await self._http.post(
            "/rest/api/2/search",
            json={"jql": jql, "fields": ["summary", "status"], "maxResults": 50},
        )
        self._raise_for_jira_error(resp)
        issues_data = resp.json().get("issues", [])
        refs = [
            IssueRef(
                issue_id=item["key"],
                source_system=self.source_system,
                url=item.get("self", ""),
            )
            for item in issues_data
        ]

        async def _gen() -> AsyncIterator[IssueRef]:
            for ref in refs:
                yield ref

        return _gen()

    async def list_recent_changes(
        self, ref: IssueRef, since: datetime, timeout_s: float | None = None
    ) -> AsyncIterator[IssueChange]:
        resp = await self._http.get(
            f"/rest/api/2/issue/{ref.issue_id}/changelog"
        )
        self._raise_for_jira_error(resp, ref.issue_id)
        histories = resp.json().get("histories", [])
        changes: list[IssueChange] = []

        for history in histories:
            changed_at = _parse_jira_datetime(history.get("created", ""))
            if changed_at < since:
                continue
            author = (history.get("author") or {}).get("displayName")
            for item in history.get("items", []):
                changes.append(
                    IssueChange(
                        issue_ref=ref,
                        field=item.get("field", "unknown"),
                        old_value=item.get("fromString"),
                        new_value=item.get("toString"),
                        changed_at=changed_at,
                        changed_by=author,
                    )
                )

        async def _gen() -> AsyncIterator[IssueChange]:
            for change in changes:
                yield change

        return _gen()

    async def register_webhook(
        self,
        callback_url: str,
        events: list[str],
        secret: str,
        timeout_s: float | None = None,
    ) -> WebhookRef:
        resp = await self._http.post(
            "/rest/api/1.0/webhook",
            json={
                "name": f"hilda-{uuid.uuid4().hex[:8]}",
                "url": callback_url,
                "events": events,
                "filters": {},
                "excludeIssueDetails": False,
            },
        )
        if resp.status_code in (400, 404, 501):
            # Webhook endpoint not available (Jira Server without webhook plugin)
            raise PipelineError("ITR-W002", context={"system": "jira"})
        self._raise_for_jira_error(resp)
        data = resp.json()
        return WebhookRef(
            webhook_id=str(data.get("webhookId", uuid.uuid4())),
            source_system=self.source_system,
            callback_url=callback_url,
        )

    async def poll_changes(
        self, since: datetime, timeout_s: float | None = None
    ) -> AsyncIterator[IssueChange]:
        ts = since.strftime("%Y-%m-%d %H:%M")
        jql = f'project = "{self._config.project_key}" AND updated >= "{ts}" ORDER BY updated ASC'
        resp = await self._http.post(
            "/rest/api/2/search",
            json={"jql": jql, "fields": ["summary", "status", "updated"], "maxResults": 100},
        )
        self._raise_for_jira_error(resp)
        issues_data = resp.json().get("issues", [])
        changes: list[IssueChange] = []

        for item in issues_data:
            ref = IssueRef(
                issue_id=item["key"],
                source_system=self.source_system,
                url=item.get("self", ""),
            )
            fields = item.get("fields", {})
            changes.append(
                IssueChange(
                    issue_ref=ref,
                    field="updated",
                    old_value=None,
                    new_value=(fields.get("status") or {}).get("name"),
                    changed_at=_parse_jira_datetime(fields.get("updated", "")),
                    changed_by=None,
                )
            )

        async def _gen() -> AsyncIterator[IssueChange]:
            for change in changes:
                yield change

        return _gen()


def make_adapter(config: dict | None = None) -> "JiraAdapter":
    """Factory for load_adapter(). Reads config + credentials from environment."""
    cfg = JiraAdapterConfig.from_env()
    username = os.environ.get("HILDA_JIRA_USERNAME", "")
    token = os.environ.get("HILDA_JIRA_API_TOKEN") or os.environ.get("HILDA_JIRA_PASSWORD", "")
    return JiraAdapter(cfg, httpx.BasicAuth(username, token))
