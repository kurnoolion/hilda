"""Kerberos/SPNEGO auth middleware stub for dashboard.

Per dashboard MODULE.md Invariant + Key choice `[D-006]`: corp reverse proxy
performs the Kerberos handshake; dashboard validates the proxy-forwarded
identity. **Ph-1 implementation note**: the actual Kerberos validation
requires corp infrastructure (KDC + service principal + keytab) that this
codebase cannot reach. Ph-1 ships a SEAM-based implementation that:

- In production: relies on the corp reverse proxy stripping any client-supplied
  `X-Authenticated-User` / `X-User-Email` headers and ONLY setting them after
  successful Kerberos validation. dashboard reads the trusted headers.
- In mock mode (DashboardConfig.mock_auth=True): accepts a hard-coded
  identity for `--serve --mock` + tests; bypasses header trust check entirely.

Per dashboard MODULE.md "Architectural decisions to lock" items 2 + 3 (reverse-
proxy identity-forwarding mechanism + cross-cutting auth module split): the
Ph-1 implementation uses header-trust (decision 2 option (b)) and lives in
dashboard module (decision 3 -- defer auth-module split until FR-62 upload
endpoint Ph-2 forces the question).
"""
from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException, Request, status

from core.src.diagnostics import format_code

from .config import DashboardConfig

__all__ = ["AuthPrincipal", "require_authenticated_principal", "PROXY_USER_HEADER"]


PROXY_USER_HEADER = "X-Authenticated-User"
PROXY_EMAIL_HEADER = "X-User-Email"


@dataclass(frozen=True)
class AuthPrincipal:
    """The authenticated corp AD principal forwarded by the corp reverse proxy."""

    user_name:    str             # AD sAMAccountName (e.g. "y.vasilyev")
    email:        str | None      # AD email if proxy forwards it
    mock:         bool             # True only when DashboardConfig.mock_auth=True


def require_authenticated_principal(
    request: Request,
    config: DashboardConfig,
) -> AuthPrincipal:
    """Extract + validate the proxy-forwarded identity.

    Per dashboard MODULE.md Invariant: reverse proxy is trusted; client identity
    headers are NOT. Production: dashboard only runs behind the corp reverse proxy
    (source-IP allowlist enforced at proxy + at network layer); the proxy strips
    any client-supplied identity headers BEFORE forwarding. Mock mode skips the
    check for the --serve --mock CLI mode + tests.

    Raises DSH-E003 (HTTP 401) when no Negotiate / no identity header.
    """
    if config.mock_auth:
        return AuthPrincipal(user_name="mock.user", email="mock@example.com", mock=True)

    user_name = request.headers.get(PROXY_USER_HEADER)
    if not user_name:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=format_code("DSH-E003"),
            headers={"WWW-Authenticate": "Negotiate"},
        )
    email = request.headers.get(PROXY_EMAIL_HEADER)
    return AuthPrincipal(user_name=user_name, email=email, mock=False)
