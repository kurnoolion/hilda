"""SP auth handlers: NoAuth (mock), NTLM, Kerberos.

NTLM is the production path per architect lock 2026-06-25 (Q1-Q4 thread):
`requests-ntlm`'s `HttpNtlmAuth` is the validated transport. Username is
the FULL `corp\\<user>` literal per architect Q1 — no separate domain
field on `GlobalSharePointConfig`.

Kerberos remains a placeholder (deferred until corp AD lab access).
NoAuth is the active path for mock-server dev + unit tests.
"""
from __future__ import annotations

from typing import Any, Protocol

from core.src.diagnostics import PipelineError
from core.src.sharepoint_integration.config import GlobalSharePointConfig


class _AuthHandler(Protocol):
    """Internal protocol — not part of public surface."""

    auth_type: str

    def as_requests_auth(self) -> Any | None:
        """Return a `requests`-compatible auth object, or None for no-auth."""


class NoAuthHandler:
    """For local mock server testing. No auth applied."""

    auth_type = "none"

    def as_requests_auth(self) -> Any | None:
        return None


class NtlmAuthHandler:
    """NTLM via `requests-ntlm` per architect lock 2026-06-25 (Q1-Q4).

    `username` is the FULL `corp\\<user>` literal — architect Q1: HILDA's
    `GlobalSharePointConfig.username` stores the entire domain-prefixed
    string; there is no separate domain field. Password is never logged
    (NFR-2)."""

    auth_type = "ntlm"

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self._password = password

    def as_requests_auth(self) -> Any | None:
        try:
            from requests_ntlm import HttpNtlmAuth
        except ImportError as e:  # pragma: no cover — dependency guard
            raise PipelineError(
                "SHP-E004", context={"auth_type": "ntlm"}, cause=e
            ) from e
        return HttpNtlmAuth(self.username, self._password)


class KerberosAuthHandler:
    """Kerberos / SPNEGO. Requires libkrb5-dev + valid ticket cache.

    Placeholder — corp AD lab access deferred. Raises SHP-E004 with detail
    until ratified."""

    auth_type = "kerberos"

    def __init__(self, keytab_path: object | None = None) -> None:
        self.keytab_path = keytab_path

    def as_requests_auth(self) -> Any | None:
        try:
            from requests_kerberos import HTTPKerberosAuth  # type: ignore[import-not-found]
        except ImportError as e:
            raise PipelineError(
                "SHP-E004", context={"auth_type": "kerberos"}, cause=e
            ) from e
        return HTTPKerberosAuth()


def make_handler(config: GlobalSharePointConfig) -> _AuthHandler:
    """Factory: pick handler from config.auth_type."""
    if config.auth_type == "none":
        return NoAuthHandler()
    if config.auth_type == "ntlm":
        if not config.username or not config.password:
            raise PipelineError(
                "SHP-E004",
                context={"auth_type": "ntlm-missing-creds"},
            )
        return NtlmAuthHandler(config.username, config.password)
    if config.auth_type == "kerberos":
        return KerberosAuthHandler(config.keytab_path)
    raise PipelineError(
        "SHP-E004", context={"auth_type": f"unknown:{config.auth_type}"}
    )
