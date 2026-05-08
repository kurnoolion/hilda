"""conftest.py — pytest fixtures and CLI options for IssueTracker contract tests."""
from __future__ import annotations

import pytest

from core.src.issue_tracker.protocol import IssueTracker


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--adapter",
        default="mock",
        help="Adapter slug to test (default: mock).",
    )
    parser.addoption(
        "--project",
        default="TEST",
        help="Project key to use for issue creation (default: TEST).",
    )


@pytest.fixture(scope="module")
def adapter_slug(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--adapter")


@pytest.fixture(scope="module")
def project_key(request: pytest.FixtureRequest) -> str:
    return request.config.getoption("--project")


@pytest.fixture(scope="module")
def tracker(adapter_slug: str) -> IssueTracker:
    from core.src.issue_tracker.issue_tracker_cli import load_adapter
    return load_adapter(adapter_slug)
