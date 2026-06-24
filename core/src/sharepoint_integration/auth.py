"""SP auth handlers: NoAuth (mock), NTLM, Kerberos.

NTLM/Kerberos handlers wrap the sync `requests-ntlm` / `requests-kerberos`
libraries; httpx hooks adapt them via _AuthHandler.apply(). For v1 dev against
the mock server, NoAuthHandler is the active path; NTLM/Kerberos are exercised
in integration testing against real SP 2017 (deferred until corp AD lab access).
"""
from __future__ import annotations

from typing import Protocol

import httpx

from core.src.diagnostics import PipelineError
from core.src.sharepoint_integration.config import GlobalSharePointConfig


class _AuthHandler(Protocol):
    """Internal protocol — not part of public surface."""

    def apply(self, request: httpx.Request) -> None:
        """Mutate the request to add auth (headers, cookies, etc.).

        Some auth schemes (NTLM) require a challenge-response handshake handled
        at the transport layer. For those, apply() is a no-op and the auth lives
        in the httpx.Auth object returned by as_httpx_auth().
        """

    def as_httpx_auth(self) -> httpx.Auth | None:
        """Return an httpx.Auth implementation, or None if apply() suffices."""


class NoAuthHandler:
    """For local mock server testing. No auth applied."""

    auth_type = "none"

    def apply(self, request: httpx.Request) -> None:
        return None

    def as_httpx_auth(self) -> httpx.Auth | None:
        return None


class NtlmAuthHandler:
    """NTLM via requests-ntlm / httpx-ntlm. Sync library wrapped at httpx auth layer.

    TODO(architect 2026-06-25): NTLM code snippets pending architect delivery.
    The current implementation is Ph-1 placeholder shape; revise to match the
    architect's reference once received (per MODULE.md Architecture D6 cascade
    note + Q4-adjacent thread).  Do NOT redesign without those snippets — wait.
    """

    auth_type = "ntlm"

    def __init__(self, username: str, password: str) -> None:
        self.username = username
        self.password = password

    def apply(self, request: httpx.Request) -> None:
        return None

    def as_httpx_auth(self) -> httpx.Auth | None:
        try:
            from httpx_ntlm import HttpNtlmAuth  # type: ignore[import-not-found]
        except ImportError:
            try:
                from requests_ntlm import HttpNtlmAuth as _RNAuth  # type: ignore[import-not-found]
            except ImportError as e:
                raise PipelineError(
                    "SHP-E004", context={"auth_type": "ntlm"}, cause=e
                ) from e
            # requests-ntlm targets the requests library, not httpx; in this
            # case we'd need a transport-layer adapter. v1 prefers httpx-ntlm
            # when available; fall back is documented as a known gap.
            raise PipelineError(
                "SHP-E004",
                context={
                    "auth_type": "ntlm-no-httpx-adapter",
                },
            )
        return HttpNtlmAuth(self.username, self.password)


class KerberosAuthHandler:
    """Kerberos / SPNEGO. Requires libkrb5-dev + valid ticket cache."""

    auth_type = "kerberos"

    def __init__(self, keytab_path: object | None = None) -> None:
        self.keytab_path = keytab_path

    def apply(self, request: httpx.Request) -> None:
        return None

    def as_httpx_auth(self) -> httpx.Auth | None:
        try:
            from requests_kerberos import HTTPKerberosAuth  # type: ignore[import-not-found]
        except ImportError as e:
            raise PipelineError(
                "SHP-E004", context={"auth_type": "kerberos"}, cause=e
            ) from e
        # Same caveat: requests-kerberos targets requests, not httpx. v1 stops
        # here and surfaces SHP-E004 with detail until an httpx-native adapter
        # is wired in (or until ops provides corp AD lab + a tested handler).
        raise PipelineError(
            "SHP-E004",
            context={"auth_type": "kerberos-no-httpx-adapter"},
        )


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
