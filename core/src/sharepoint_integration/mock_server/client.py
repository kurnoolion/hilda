"""MockSpSession: SpSession backed by a FastAPI TestClient.

Used by the `--mock` CLI mode + the test suite to drive `SpClient` against
the in-process `mock_server` FastAPI app without real network or NTLM.

The architect's digest dance is honoured: this session runs the real
digest-acquisition flow against the mock endpoints — that way the mock
exercises the full lifecycle SpClient relies on in production.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.src.sharepoint_integration.sp_session import SpSession, list_item_type

__all__ = ["MockSpSession", "list_item_type"]


class _RequestsAdapter:
    """Adapter that makes a FastAPI `TestClient` quack like a
    `requests.Session` for the few methods `SpSession` uses (`get`, `post`,
    `close`)."""

    def __init__(self, app: FastAPI, base_url: str) -> None:
        self._tc = TestClient(app, base_url=base_url)
        self.headers: dict[str, str] = {}
        # Hook so `SpSession.__init__` assignment `self._session.auth = ...`
        # doesn't fail; we ignore the value because TestClient has no NTLM.
        self.auth: Any = None

    def get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self._tc.get(url, params=params, headers=self.headers)

    def post(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        data: Any = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        merged = dict(self.headers)
        if headers:
            merged.update(headers)
        # httpx/TestClient expects raw str/bytes via `content=`, not `data=`.
        content = data if isinstance(data, (bytes, str)) else None
        form = data if content is None else None
        return self._tc.post(
            url, headers=merged, content=content, data=form, params=params
        )

    def close(self) -> None:
        self._tc.close()


class MockSpSession(SpSession):
    """SpSession variant that drives a FastAPI app via TestClient instead of
    NTLM-authed `requests`."""

    def __init__(self, app: FastAPI, site_url: str = "http://mock-sp") -> None:
        # Bypass parent __init__ so we can swap in our adapter without
        # touching `requests_ntlm`.
        self._site_url = site_url.rstrip("/")
        self._session = _RequestsAdapter(app, base_url=self._site_url)
        # NoAuth — TestClient hits the in-process app directly.
        self._session.headers = {"Accept": "application/json;odata=verbose"}
        self._cookie: str | None = None
        self._digest: str | None = None
