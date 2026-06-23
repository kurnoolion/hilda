"""dashboard -- FastAPI app + Jinja2 SSR for HILDA's HTTP surface.

See core/src/dashboard/MODULE.md.
"""
from core.src.dashboard.app import MilestoneRefreshState, build_app
from core.src.dashboard.auth import (
    AuthPrincipal,
    PROXY_USER_HEADER,
    require_authenticated_principal,
)
from core.src.dashboard.config import DashboardConfig

__all__ = [
    "AuthPrincipal",
    "DashboardConfig",
    "MilestoneRefreshState",
    "PROXY_USER_HEADER",
    "build_app",
    "require_authenticated_principal",
]
