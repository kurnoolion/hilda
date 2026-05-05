"""FastAPI app: SP 2017 REST stub + HTML browser UI.

The same FastAPI app object can be extended with additional routes (e.g. by
the dashboard mock) — pass the desired InMemoryStore in to share state.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Path as FPath, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response

from core.src.sharepoint_integration.mock_server.store import (
    InMemoryStore,
    ListNotFoundError,
)

_GETBYTITLE_RE = re.compile(r"getbytitle\('(?P<name>(?:[^']|'')*)'\)", re.IGNORECASE)


def _decode_list_name(quoted: str) -> str:
    return quoted.replace("''", "'")


def build_app(store: InMemoryStore | None = None) -> FastAPI:
    """Build the FastAPI mock SP server.

    Args:
        store: Pre-existing store for shared state across multiple servers /
            tests. If None, a fresh InMemoryStore is created.
    """
    app = FastAPI(title="HILDA Mock SharePoint", openapi_url=None, docs_url=None)
    app.state.store = store if store is not None else InMemoryStore()

    @app.get("/_api/web/lists/getbytitle('{list_name}')/items")
    async def get_items(
        request: Request,
        list_name: str = FPath(...),
        select: str | None = Query(default=None, alias="$select"),
        filter_: str | None = Query(default=None, alias="$filter"),
        top: int | None = Query(default=None, alias="$top"),
    ) -> JSONResponse:
        store_: InMemoryStore = request.app.state.store
        decoded = _decode_list_name(list_name)
        try:
            items = list(store_.iter_items(decoded))
        except ListNotFoundError:
            return JSONResponse(
                status_code=404,
                content={"error": {"code": "-1, ListNotFound", "message": f"list '{decoded}'"}},
            )
        if filter_:
            items = [i for i in items if _match_filter(i, filter_)]
        if select:
            keep = {s.strip() for s in select.split(",")}
            keep.add("Id")
            items = [{k: v for k, v in i.items() if k in keep} for i in items]
        if top is not None:
            items = items[:top]
        return JSONResponse({"value": items})

    @app.post("/_api/web/lists/getbytitle('{list_name}')/items")
    async def create_item(
        request: Request,
        list_name: str = FPath(...),
        body: dict[str, Any] = Body(...),
    ) -> JSONResponse:
        store_: InMemoryStore = request.app.state.store
        decoded = _decode_list_name(list_name)
        item_id = store_.create_item(decoded, body)
        return JSONResponse({"Id": int(item_id), **body}, status_code=201)

    @app.patch("/_api/web/lists/getbytitle('{list_name}')/items({item_id})")
    async def update_item(
        request: Request,
        list_name: str = FPath(...),
        item_id: str = FPath(...),
        body: dict[str, Any] = Body(...),
    ) -> Response:
        store_: InMemoryStore = request.app.state.store
        decoded = _decode_list_name(list_name)
        try:
            store_.update_item(decoded, item_id, body)
        except ListNotFoundError as e:
            raise HTTPException(404, detail=str(e))
        return Response(status_code=204)

    @app.delete("/_api/web/lists/getbytitle('{list_name}')/items({item_id})")
    async def delete_item(
        request: Request,
        list_name: str = FPath(...),
        item_id: str = FPath(...),
    ) -> Response:
        store_: InMemoryStore = request.app.state.store
        decoded = _decode_list_name(list_name)
        try:
            store_.delete_item(decoded, item_id)
        except ListNotFoundError as e:
            raise HTTPException(404, detail=str(e))
        return Response(status_code=204)

    # ---- Web UI ----

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> str:
        store_: InMemoryStore = request.app.state.store
        names = store_.list_names()
        rows = (
            "".join(
                f'<tr><td><a href="/lists/{html.escape(n)}">{html.escape(n)}</a></td>'
                f"<td>{len(store_.get_list(n))}</td></tr>"
                for n in names
            )
            or '<tr><td colspan="2"><i>no lists yet — POST to '
            "<code>/_api/web/lists/getbytitle(&#x27;X&#x27;)/items</code> "
            "to create one</i></td></tr>"
        )
        return _render(
            "Mock SharePoint",
            f"""
            <h2>Lists</h2>
            <table><thead><tr><th>Name</th><th>Items</th></tr></thead>
            <tbody>{rows}</tbody></table>
            <p><a href="/audit">Audit log</a> · <a href="/health">Health</a></p>
            """,
        )

    @app.get("/lists/{list_name}", response_class=HTMLResponse)
    async def list_detail(request: Request, list_name: str) -> str:
        store_: InMemoryStore = request.app.state.store
        try:
            items = list(store_.iter_items(list_name))
        except ListNotFoundError:
            raise HTTPException(404, detail=f"list '{list_name}'")
        all_keys: list[str] = ["Id"]
        seen = {"Id"}
        for it in items:
            for k in it:
                if k not in seen:
                    seen.add(k)
                    all_keys.append(k)
        head = "".join(f"<th>{html.escape(k)}</th>" for k in all_keys)
        body_rows = (
            "".join(
                "<tr>"
                + "".join(f"<td>{html.escape(str(it.get(k, '')))}</td>" for k in all_keys)
                + "</tr>"
                for it in items
            )
            or f'<tr><td colspan="{len(all_keys)}"><i>empty</i></td></tr>'
        )
        return _render(
            f"List: {list_name}",
            f"""
            <h2>{html.escape(list_name)}</h2>
            <p><a href="/">← back to lists</a></p>
            <table><thead><tr>{head}</tr></thead>
            <tbody>{body_rows}</tbody></table>
            """,
        )

    @app.get("/audit", response_class=HTMLResponse)
    async def audit(request: Request) -> str:
        store_: InMemoryStore = request.app.state.store
        rows = "".join(
            f"<tr><td>{e.timestamp.strftime('%Y-%m-%d %H:%M:%S')}</td>"
            f"<td>{html.escape(e.method)}</td>"
            f"<td>{html.escape(e.list_name)}</td>"
            f"<td>{html.escape(e.item_id or '')}</td>"
            f"<td>{html.escape(e.body_summary)}</td></tr>"
            for e in store_.audit_log()
        ) or '<tr><td colspan="5"><i>no activity yet</i></td></tr>'
        return _render(
            "Audit",
            f"""
            <h2>Audit log</h2>
            <p><a href="/">← back to lists</a></p>
            <table>
              <thead><tr><th>Timestamp</th><th>Method</th><th>List</th>
                         <th>Item</th><th>Summary</th></tr></thead>
              <tbody>{rows}</tbody>
            </table>
            """,
        )

    @app.get("/health")
    async def health() -> dict[str, Any]:
        store_: InMemoryStore = app.state.store
        return {
            "ok": True,
            "lists": len(store_.list_names()),
            "audit_entries": len(store_.audit_log()),
        }

    @app.post("/__reset__")
    async def reset(request: Request) -> dict[str, bool]:
        """Test-only: clear in-memory state."""
        request.app.state.store.reset()
        return {"ok": True}

    return app


def _match_filter(item: dict[str, Any], odata_filter: str) -> bool:
    """Tiny OData $filter evaluator. Supports `<col> eq <literal>` joined by ` and `."""
    parts = [p.strip() for p in odata_filter.split(" and ")]
    for clause in parts:
        m = re.match(r"^(\S+)\s+eq\s+(.+)$", clause)
        if not m:
            return False
        col, raw = m.group(1), m.group(2)
        expected: Any
        if raw.startswith("'") and raw.endswith("'"):
            expected = raw[1:-1].replace("''", "'")
        elif raw == "true":
            expected = True
        elif raw == "false":
            expected = False
        elif raw == "null":
            expected = None
        else:
            try:
                expected = int(raw)
            except ValueError:
                try:
                    expected = float(raw)
                except ValueError:
                    expected = raw
        actual = item.get(col)
        if actual != expected:
            return False
    return True


_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>{title}</title>
<style>
  body {{ font-family: -apple-system, system-ui, sans-serif; padding: 1.5em; }}
  table {{ border-collapse: collapse; min-width: 50%; }}
  th, td {{ border: 1px solid #ddd; padding: 6px 12px; text-align: left; }}
  th {{ background: #f4f4f4; }}
  a {{ color: #0366d6; }}
  code {{ background: #f4f4f4; padding: 2px 4px; border-radius: 3px; }}
</style>
</head>
<body>
<h1>HILDA Mock SharePoint</h1>
{body}
</body>
</html>"""


def _render(title: str, body: str) -> str:
    return _TEMPLATE.format(title=html.escape(title), body=body)
