"""CustomerJiraPoller -- Ph-1 informational polling per `[D-092]`.

Per Module #13 cascade D9 2026-06-25.

Ph-1 lock: HILDA polls customer JIRA for waiver/issue tickets per Projects.TPM
filter + delivery_item references; collects results into in-memory state for FR-9
outreach attachments (PM has JIRA URL in outreach per `[D-092]`). NO SP write-back.

Per FR-25 (b) close-intent flows via email per FR-12.

Ph-2 will add SP write-back to Deliverables_<customer_id> columns:
  - jira_open_ticket_count (int)
  - jira_ticket_summary_json (JSON-as-string)
"""
from __future__ import annotations

from dataclasses import dataclass, field

from core.src.issue_tracker.protocol import CustomerJiraAdapter, JiraIssue


@dataclass
class CustomerJiraPoller:
    """Ph-1 informational polling loop. Stateful in-memory cache keyed on
    `issue_key`; refreshed each poll cycle. NO SP write-back per `[D-092]`.

    Usage:
        poller = CustomerJiraPoller(adapter=customer_jira_adapter)
        await poller.poll(jql="project=HILDA AND assignee = currentUser()")
        for issue in poller.open_tickets():
            ...
    """

    adapter: CustomerJiraAdapter
    _cache: dict[str, JiraIssue] = field(default_factory=dict)

    async def poll(self, jql: str) -> list[JiraIssue]:
        """Runs one poll cycle. Returns the list of issues found; refreshes the
        in-memory cache."""
        issues = await self.adapter.search_issues(jql)
        # Replace cache with the polled snapshot (Ph-1 -- stale entries dropped).
        self._cache = {iss.issue_key: iss for iss in issues}
        return issues

    def cached(self, issue_key: str) -> JiraIssue | None:
        """Returns the cached JiraIssue or None. For FR-9 outreach attachment URL
        surfacing."""
        return self._cache.get(issue_key)

    def open_tickets(self) -> list[JiraIssue]:
        """Returns cached issues whose status is NOT Closed/Done/Resolved-Closed.

        Ph-1 heuristic: status string NOT matching any of {closed, done, resolved, cancelled}
        (case-insensitive). Customer JIRA status vocabularies vary; the customer-
        specific subclass MAY override this method with a tighter filter.
        """
        terminal = {"closed", "done", "resolved", "cancelled", "canceled"}
        return [iss for iss in self._cache.values() if iss.status.strip().lower() not in terminal]

    def reset_cache(self) -> None:
        """Test hook -- clears the cache."""
        self._cache.clear()
