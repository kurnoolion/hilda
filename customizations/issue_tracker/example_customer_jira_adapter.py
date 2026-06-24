"""Example per-customer JIRA adapter scaffold.

Per Module #13 cascade D2 + `[D-092]` Ph-1 informational polling lock 2026-06-25.

COPY this file to `<customer_id>_customer_jira_adapter.py` (e.g.,
`test_carrier_a_customer_jira_adapter.py`), then fill in the `# TODO(cline)`
markers with the customer JIRA base_url + project_key + credential resolution.

Ph-1 surface is informational read-only:
  search_issues, get_issue, list_comments

Ph-2 mutation methods (create/update/transition/add_comment/upload/download
attachment) raise ITR-E007 -- override per-customer if/when Ph-2 lands.
"""
from __future__ import annotations

import os

import httpx

from core.src.issue_tracker.customer_jira.adapter import (
    CustomerJiraAdapterBase,
    CustomerJiraConfig,
)


class ExampleCustomerJiraAdapter(CustomerJiraAdapterBase):
    """Per-customer JIRA adapter scaffold."""

    pass  # Ph-1 base class is sufficient; per-customer override only if needed.


def make_customer_jira_adapter(customer_id: str = "example") -> ExampleCustomerJiraAdapter:
    """Factory for load_adapter() / CLI --jira-diagnostic mode.

    Reads env vars:
      HILDA_JIRA_<CUSTOMER_ID>_BASE_URL
      HILDA_JIRA_<CUSTOMER_ID>_PROJECT_KEY
      HILDA_JIRA_<CUSTOMER_ID>_USERNAME
      HILDA_JIRA_<CUSTOMER_ID>_API_TOKEN

    In production, credentials should resolve via credential_service.get_credential(
    pm_id, system_type=JIRA, customer_id=customer_id) per [D-107]; this scaffold
    uses env vars for ops debugging only.
    """
    upper = customer_id.upper()
    prefix = f"HILDA_JIRA_{upper}_"
    base_url = os.environ.get(f"{prefix}BASE_URL", "http://localhost:8080")
    project_key = os.environ.get(f"{prefix}PROJECT_KEY", "HILDA")
    username = os.environ.get(f"{prefix}USERNAME", "")
    token = os.environ.get(f"{prefix}API_TOKEN", "")

    config = CustomerJiraConfig(
        customer_id=customer_id,
        base_url=base_url.rstrip("/"),
        project_key=project_key,
    )
    auth = httpx.BasicAuth(username, token) if (username and token) else None
    return ExampleCustomerJiraAdapter(config=config, auth=auth)
