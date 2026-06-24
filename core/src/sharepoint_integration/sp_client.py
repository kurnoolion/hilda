"""SpClient: async SP 2017 REST client over a sync `SpSession` transport.

The async public surface is preserved per the existing key choice; sync
`requests-ntlm` + digest-dance calls live in `SpSession` and are wrapped
via `asyncio.to_thread` here.

Architect lock 2026-06-25 (Q1-Q4):
    - NTLM via `requests-ntlm` with full `corp\\<user>` literal (Q1).
    - Lazy digest acquisition; refresh + retry on 403 (Q2).
    - HILDA's 3-list scope (Milestones/Projects/Deliverables per-customer)
      is enforced upstream by `FileBasedListProvider` (Q3); SpClient is
      list-agnostic per `[D-020]`.
    - One SpSession per Celery task; no session pool (Q4).

Anchors `[D-006]` (SP REST + AD auth), NFR-8 (Lists + classic web parts).
Pagination kept as `$top + __next` auto-follow continuation pending architect
Q4 follow-up (marked OPEN in MODULE.md).
"""
from __future__ import annotations

import asyncio
from typing import Any, Iterable

import requests

from core.src.diagnostics import PipelineError
from core.src.sharepoint_integration.config import GlobalSharePointConfig
from core.src.sharepoint_integration.sp_session import SpSession


class SpClient:
    """Async SP REST client. Construct one per Celery task per architect Q4."""

    def __init__(
        self,
        config: GlobalSharePointConfig,
        *,
        session: SpSession | None = None,
    ) -> None:
        """Build a client; an explicit `session` overrides the default
        NTLM-credential-driven build path (used by tests + the mock-server
        in-process driver)."""
        self.config = config
        if session is not None:
            self._session = session
        else:
            self._session = self._make_session_from_config(config)

    @staticmethod
    def _make_session_from_config(config: GlobalSharePointConfig) -> SpSession:
        if config.auth_type == "ntlm":
            if not config.username or not config.password:
                raise PipelineError(
                    "SHP-E004",
                    context={"auth_type": "ntlm-missing-creds"},
                )
            return SpSession(
                site_url=config.site_url,
                ntlm_user=config.username,
                ntlm_pass=config.password,
            )
        # `none` / `kerberos` paths require an externally injected session
        # — see test fixtures + mock-server driver (Kerberos handler stays
        # placeholder until corp AD lab access).
        raise PipelineError(
            "SHP-E004",
            context={"auth_type": f"requires-explicit-session:{config.auth_type}"},
        )

    async def aclose(self) -> None:
        await asyncio.to_thread(self._session.close)

    async def __aenter__(self) -> "SpClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    # ---- list-item operations ----

    async def get_list_items(
        self,
        list_name: str,
        select: list[str] | None = None,
        filter_expr: str | None = None,
    ) -> list[dict[str, Any]]:
        # Pagination: standard SP `$top + __next` continuation pattern.
        # TODO(architect Q4 2026-06-25): architect comment "every element accessed
        # individually as far as I know" leaves this OPEN — confirm pattern when
        # NTLM code snippets land. Ph-1 ships with auto-follow continuation as
        # documented in MODULE.md Architecture / `[D-006]`.
        params: dict[str, str] = {"$top": str(self.config.page_size)}
        if select:
            params["$select"] = ",".join(select)
        if filter_expr:
            params["$filter"] = filter_expr
        url = self._items_url(list_name)
        items: list[dict[str, Any]] = []
        next_url: str | None = url
        first = True
        while next_url is not None:
            if first:
                resp = await self._sp_get(next_url, params=params)
                first = False
            else:
                resp = await self._sp_get(next_url)
            payload = self._json_or_raise(resp, list_name)
            # SP 2017 odata=verbose wraps results in {"d": {"results": [...]}};
            # nometadata returns {"value": [...]} flat. Handle both.
            d = payload.get("d")
            if isinstance(d, dict) and "results" in d:
                items.extend(d["results"])
                next_url = d.get("__next") or payload.get("@odata.nextLink")
            else:
                items.extend(payload.get("value", []))
                next_url = payload.get("odata.nextLink") or payload.get(
                    "@odata.nextLink"
                )
        return items

    async def create_list_item(
        self,
        list_name: str,
        fields: dict[str, Any],
        *,
        customer_id: str,
    ) -> str:
        status, body = await asyncio.to_thread(
            self._session.create, list_name, customer_id, fields
        )
        if status >= 400:
            sp_code = _extract_sp_code_from_payload(body, status)
            if status == 401:
                raise PipelineError(
                    "SHP-E004",
                    context={"auth_type": "ntlm", "list": list_name},
                )
            raise PipelineError(
                "SHP-E001",
                context={
                    "list": list_name,
                    "status": status,
                    "sp_error_code": sp_code,
                },
            )
        # SP 2017 odata=verbose: {"d": {"Id": N, ...}}; nometadata: {"Id": N}.
        d = body.get("d") if isinstance(body, dict) else None
        source = d if isinstance(d, dict) else body
        item_id = (
            source.get("Id") or source.get("ID") or source.get("id")
            if isinstance(source, dict)
            else None
        )
        if item_id is None:
            raise PipelineError(
                "SHP-E001",
                context={
                    "list": list_name,
                    "status": status,
                    "sp_error_code": "missing-id-in-response",
                },
            )
        return str(item_id)

    async def update_list_item(
        self,
        list_name: str,
        item_id: str,
        fields: dict[str, Any],
        *,
        customer_id: str,
    ) -> None:
        status = await asyncio.to_thread(
            self._session.merge, list_name, customer_id, item_id, fields
        )
        if status >= 400:
            if status == 401:
                raise PipelineError(
                    "SHP-E004",
                    context={"auth_type": "ntlm", "list": list_name},
                )
            raise PipelineError(
                "SHP-E001",
                context={
                    "list": list_name,
                    "status": status,
                    "sp_error_code": f"http-{status}",
                },
            )

    async def delete_list_item(self, list_name: str, item_id: str) -> None:
        status = await asyncio.to_thread(self._session.delete, list_name, item_id)
        if status >= 400:
            if status == 401:
                raise PipelineError(
                    "SHP-E004",
                    context={"auth_type": "ntlm", "list": list_name},
                )
            raise PipelineError(
                "SHP-E001",
                context={
                    "list": list_name,
                    "status": status,
                    "sp_error_code": f"http-{status}",
                },
            )

    async def batch_create(
        self,
        list_name: str,
        items: Iterable[dict[str, Any]],
        *,
        customer_id: str,
    ) -> list[str]:
        # v1: simple sequential — switch to SP $batch if perf demands it.
        out: list[str] = []
        for fields in items:
            out.append(
                await self.create_list_item(list_name, fields, customer_id=customer_id)
            )
        return out

    async def batch_update(
        self,
        list_name: str,
        updates: Iterable[tuple[str, dict[str, Any]]],
        *,
        customer_id: str,
    ) -> None:
        for item_id, fields in updates:
            await self.update_list_item(
                list_name, item_id, fields, customer_id=customer_id
            )

    # ---- internals ----

    def _items_url(self, list_name: str) -> str:
        return self._session._items_url(list_name)  # noqa: SLF001 — internal helper

    async def _sp_get(
        self,
        url: str,
        params: dict[str, str] | None = None,
    ) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                resp = await asyncio.to_thread(self._session.get, url, params)
                if resp.status_code in (429, 503) and attempt < self.config.max_retries:
                    await asyncio.sleep(
                        self.config.retry_backoff_seconds * (2**attempt)
                    )
                    continue
                if resp.status_code == 401:
                    raise PipelineError(
                        "SHP-E004",
                        context={"auth_type": self.config.auth_type},
                    )
                if resp.status_code >= 400:
                    sp_code = _extract_sp_code(resp)
                    raise PipelineError(
                        "SHP-E001",
                        context={
                            "list": _list_from_url(url),
                            "status": resp.status_code,
                            "sp_error_code": sp_code,
                        },
                    )
                return resp
            except requests.RequestException as e:
                last_exc = e
                if attempt < self.config.max_retries:
                    await asyncio.sleep(
                        self.config.retry_backoff_seconds * (2**attempt)
                    )
                    continue
                raise PipelineError(
                    "SHP-E001",
                    context={
                        "list": _list_from_url(url),
                        "status": 0,
                        "sp_error_code": type(e).__name__,
                    },
                    cause=e,
                ) from e
        raise PipelineError(
            "SHP-E001",
            context={
                "list": _list_from_url(url),
                "status": 0,
                "sp_error_code": "retry-exhausted",
            },
            cause=last_exc,
        )

    def _json_or_raise(
        self, resp: requests.Response, list_name: str
    ) -> dict[str, Any]:
        try:
            return resp.json()
        except ValueError as e:
            raise PipelineError(
                "SHP-E001",
                context={
                    "list": list_name,
                    "status": resp.status_code,
                    "sp_error_code": "invalid-json",
                },
                cause=e,
            ) from e


def _extract_sp_code(resp: requests.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return f"http-{resp.status_code}"
    return _extract_sp_code_from_payload(body, resp.status_code)


def _extract_sp_code_from_payload(body: Any, status: int) -> str:
    if isinstance(body, dict):
        err = body.get("error") or body.get("odata.error")
        if isinstance(err, dict):
            code = err.get("code") or err.get("@code")
            if isinstance(code, str):
                return code.split(",", 1)[0]
            message = err.get("message")
            if isinstance(message, dict):
                value = message.get("value")
                if isinstance(value, str):
                    return value.split(",", 1)[0]
    return f"http-{status}"


def _list_from_url(url: str) -> str:
    """Extract list name from `/_api/web/lists/getbytitle('X')/...`. Best-effort."""
    marker = "getbytitle('"
    if marker in url:
        rest = url.split(marker, 1)[1]
        return rest.split("')", 1)[0].replace("''", "'")
    return "<unknown>"
