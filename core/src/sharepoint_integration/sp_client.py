"""SpClient: async SP 2017 REST HTTP client.

No HILDA model knowledge — takes SP-native list names and SP internal column
names. List name → SP REST URL conversion happens here. Pagination, retry,
auth all handled at this layer.

Anchors [D-006] (SP REST + AD auth), NFR-8 (Lists + classic web parts only).

Auth: NTLM (`requests-ntlm` / `httpx-ntlm`) and Kerberos (`requests-kerberos`)
both wrap sync libs at the httpx auth layer.  NTLM **code snippets pending
architect delivery** (per architect Q4-adjacent thread 2026-06-25); the
`NtlmAuthHandler` shape here is the Ph-1 placeholder and may be revised to
match the architect's reference once received.  Do NOT redesign without
those snippets in hand — see `auth.py` for the matching TODO(architect)
markers.
"""
from __future__ import annotations

import asyncio
from typing import Any, Iterable

import httpx

from core.src.diagnostics import PipelineError
from core.src.sharepoint_integration.auth import make_handler
from core.src.sharepoint_integration.config import GlobalSharePointConfig


def _quote_list_name(name: str) -> str:
    """SP `getbytitle('X')` requires single-quote escaping."""
    return name.replace("'", "''")


class SpClient:
    """Async SP REST client. Construct once per process; reuse the underlying httpx client."""

    def __init__(
        self,
        config: GlobalSharePointConfig,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self._auth_handler = make_handler(config)
        kwargs: dict[str, Any] = dict(
            base_url=config.site_url.rstrip("/"),
            timeout=config.timeout_seconds,
            headers={
                "Accept": "application/json;odata=nometadata",
                "Content-Type": "application/json;odata=nometadata",
            },
        )
        if transport is not None:
            kwargs["transport"] = transport
        auth_obj = self._auth_handler.as_httpx_auth()
        if auth_obj is not None:
            kwargs["auth"] = auth_obj
        self._client = httpx.AsyncClient(**kwargs)

    async def aclose(self) -> None:
        await self._client.aclose()

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
        # Pagination: standard SP `$top + odata.nextLink` continuation pattern.
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
                resp = await self._request("GET", next_url, params=params)
                first = False
            else:
                resp = await self._request("GET", next_url)
            payload = resp.json()
            items.extend(payload.get("value", []))
            # SP 2017 returns the continuation under `odata.nextLink` (nometadata)
            # or `@odata.nextLink` (verbose). Both surfaces handled defensively.
            next_url = payload.get("odata.nextLink") or payload.get("@odata.nextLink")
        return items

    async def create_list_item(
        self, list_name: str, fields: dict[str, Any]
    ) -> str:
        url = self._items_url(list_name)
        resp = await self._request("POST", url, json=fields)
        body = resp.json()
        # SP returns the created item; ID is "Id" or "ID" depending on endpoint config
        item_id = body.get("Id") or body.get("ID") or body.get("id")
        if item_id is None:
            raise PipelineError(
                "SHP-E001",
                context={
                    "list": list_name,
                    "status": resp.status_code,
                    "sp_error_code": "missing-id-in-response",
                },
            )
        return str(item_id)

    async def update_list_item(
        self, list_name: str, item_id: str, fields: dict[str, Any]
    ) -> None:
        url = self._item_url(list_name, item_id)
        await self._request(
            "PATCH",
            url,
            json=fields,
            headers={"IF-MATCH": "*"},
        )

    async def delete_list_item(self, list_name: str, item_id: str) -> None:
        url = self._item_url(list_name, item_id)
        await self._request("DELETE", url, headers={"IF-MATCH": "*"})

    async def batch_create(
        self, list_name: str, items: Iterable[dict[str, Any]]
    ) -> list[str]:
        # v1: simple sequential — switch to SP $batch if perf demands it.
        out: list[str] = []
        for fields in items:
            out.append(await self.create_list_item(list_name, fields))
        return out

    async def batch_update(
        self, list_name: str, updates: Iterable[tuple[str, dict[str, Any]]]
    ) -> None:
        for item_id, fields in updates:
            await self.update_list_item(list_name, item_id, fields)

    # ---- internals ----

    def _items_url(self, list_name: str) -> str:
        quoted = _quote_list_name(list_name)
        return f"/_api/web/lists/getbytitle('{quoted}')/items"

    def _item_url(self, list_name: str, item_id: str) -> str:
        return f"{self._items_url(list_name)}({item_id})"

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, str] | None = None,
        json: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                resp = await self._client.request(
                    method,
                    url,
                    params=params,
                    json=json,
                    headers=headers,
                )
                if resp.status_code in (429, 503) and attempt < self.config.max_retries:
                    await asyncio.sleep(
                        self.config.retry_backoff_seconds * (2**attempt)
                    )
                    continue
                if resp.status_code == 401:
                    raise PipelineError(
                        "SHP-E004",
                        context={"auth_type": self._auth_handler.auth_type},
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
            except httpx.HTTPError as e:
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
        # Should not reach here; the loop exits via return or raise.
        raise PipelineError(
            "SHP-E001",
            context={
                "list": _list_from_url(url),
                "status": 0,
                "sp_error_code": "retry-exhausted",
            },
            cause=last_exc,
        )


def _extract_sp_code(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        if isinstance(body, dict):
            err = body.get("error") or body.get("odata.error")
            if isinstance(err, dict):
                code = err.get("code") or err.get("@code")
                if isinstance(code, str):
                    return code.split(",", 1)[0]
    except (ValueError, KeyError):
        pass
    return f"http-{resp.status_code}"


def _list_from_url(url: str) -> str:
    """Extract list name from `/_api/web/lists/getbytitle('X')/...`. Best-effort."""
    marker = "getbytitle('"
    if marker in url:
        rest = url.split(marker, 1)[1]
        return rest.split("')", 1)[0].replace("''", "'")
    return "<unknown>"
