"""customer_jira sub-module — Standard JIRA REST adapter + Ph-1 informational poller.

Per Module #13 cascade D2 + `[D-092]` Ph-1 lock 2026-06-25. See
core/src/issue_tracker/MODULE.md.
"""
from core.src.issue_tracker.customer_jira.adapter import CustomerJiraAdapterBase, CustomerJiraConfig
from core.src.issue_tracker.customer_jira.poller import CustomerJiraPoller

__all__ = [
    "CustomerJiraAdapterBase",
    "CustomerJiraConfig",
    "CustomerJiraPoller",
]
