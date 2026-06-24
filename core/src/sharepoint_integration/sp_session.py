"""SpSession: SP 2017 NTLM + digest-dance sync transport.

Encapsulates the architect-validated production pattern locked 2026-06-25
(Q1-Q4 thread). Three concerns wrapped in one object so callers don't have
to re-implement the dance per write:

1.  NTLM auth via `requests-ntlm` `HttpNtlmAuth`. Username is the FULL
    `corp\\<user>` literal per architect Q1 — no separate domain field.
2.  Digest dance — step 1 GET the site URL to capture the `WSSAUTH` cookie,
    step 2 POST `/_api/contextinfo` to obtain the `FormDigestValue` (CSRF
    token). Lazy: not run until the first write. Refreshed on 403 per
    architect Q2.
3.  SP 2017 MERGE protocol for partial updates — POST + `X-Http-Method:
    MERGE` + `__metadata` wrapper carrying `SP.Data.<list>_x005f_<carrier>
    ListItem`. The encoding helper `list_item_type` produces the type
    discriminator.

One `SpSession` per Celery task per architect Q4 (no session pool). The
sync `requests.Session` is wrapped at `asyncio.to_thread` in `SpClient`.

Privacy: `password`, the WSSAUTH `cookie`, and the `FormDigestValue` MUST
NEVER appear in logs, `__repr__`, or compact reports per NFR-2.

Anchors `[D-006]` SP REST + AD auth; pending TODO(D-117 candidate) to
ratify the digest-dance lifecycle as an ADR.
"""
from __future__ import annotations

import json
from typing import Any

import requests
from requests_ntlm import HttpNtlmAuth

from core.src.diagnostics import PipelineError


def list_item_type(list_display_name: str) -> str:
    """Encode the SP 2017 `__metadata.type` discriminator for a list.

    SP 2017 uses `SP.Data.<encoded>ListItem` where the list display name is
    encoded with underscores → `_x005f_` and spaces → `_x0020_`. The argument
    is the FULL display name including any `_<customer_id>` suffix.

    Examples:
        list_item_type("Deliverables_<customer_id>")
          → "SP.Data.Deliverables_x005f_<customer_id>ListItem"
        list_item_type("My List")
          → "SP.Data.My_x0020_ListListItem"
    """
    encoded = list_display_name.replace("_", "_x005f_").replace(" ", "_x0020_")
    return f"SP.Data.{encoded}ListItem"


def _quote_list_name(name: str) -> str:
    """SP `getbytitle('X')` requires single-quote escaping."""
    return name.replace("'", "''")


class SpSession:
    """Sync SP 2017 NTLM session with lazy digest acquisition + 403 refresh.

    Per architect lock 2026-06-25:
        Q1 — `ntlm_user` is the full `corp\\<user>` literal.
        Q2 — Lazy digest; on 403 from a write, refresh once + retry once.
        Q3 — Caller is responsible for restricting writes to the
              `Milestones_/Projects_/Deliverables_<customer_id>` 3-list scope
              (enforced upstream by `FileBasedListProvider`).
        Q4 — One `SpSession` per Celery task; no session pool.

    Privacy: NEVER repr the cookie or digest; NEVER log the password.
    """

    def __init__(self, site_url: str, ntlm_user: str, ntlm_pass: str) -> None:
        self._site_url = site_url.rstrip("/")
        self._session = requests.Session()
        self._session.auth = HttpNtlmAuth(ntlm_user, ntlm_pass)
        self._session.headers.update({"Accept": "application/json;odata=verbose"})
        # Lazy state — populated on first write by _ensure_digest().
        self._cookie: str | None = None
        self._digest: str | None = None

    def __repr__(self) -> str:  # pragma: no cover — privacy guard
        return f"SpSession(site_url={self._site_url!r})"

    def __str__(self) -> str:  # pragma: no cover — privacy guard
        return self.__repr__()

    def close(self) -> None:
        self._session.close()

    # ---- digest dance ----

    def _ensure_digest(self) -> None:
        """Step 1 (GET to capture WSSAUTH cookie) + Step 2 (POST
        /_api/contextinfo to obtain FormDigestValue). Idempotent — no-op
        if already cached."""
        if self._digest is not None:
            return
        self._refresh_digest()

    def _refresh_digest(self) -> None:
        """Force regeneration of the cookie + digest. Called by
        `_ensure_digest` on first use and by `merge`/`create`/`delete` on a
        403 response per architect Q2."""
        r = self._session.get(self._site_url)
        if r.status_code >= 400:
            self._cookie = None
            self._digest = None
            raise PipelineError(
                "SHP-E004",
                context={"auth_type": "ntlm", "status": r.status_code},
            )
        set_cookie = r.headers.get("Set-Cookie")
        self._cookie = set_cookie.split(";")[0] if set_cookie else None

        ctx = self._session.post(f"{self._site_url}/_api/contextinfo")
        if ctx.status_code >= 400:
            self._cookie = None
            self._digest = None
            raise PipelineError(
                "SHP-E004",
                context={"auth_type": "ntlm-contextinfo", "status": ctx.status_code},
            )
        try:
            self._digest = ctx.json()["d"]["GetContextWebInformation"]["FormDigestValue"]
        except (KeyError, ValueError) as e:
            self._cookie = None
            self._digest = None
            raise PipelineError(
                "SHP-E004",
                context={"auth_type": "ntlm-digest-parse"},
                cause=e,
            ) from e

    # ---- read ----

    def get(
        self,
        url: str,
        params: dict[str, str] | None = None,
    ) -> requests.Response:
        """NTLM-auth GET. No digest needed for reads."""
        if not url.startswith("http"):
            url = f"{self._site_url}{url}"
        return self._session.get(url, params=params)

    # ---- write helpers ----

    def _write_headers(self, http_method: str) -> dict[str, str]:
        assert self._digest is not None, "digest must be ensured before write"
        h = {
            "Accept": "application/json;odata=verbose",
            "Content-Type": "application/json;odata=verbose;",
            "X-RequestDigest": self._digest,
            "IF-MATCH": "*",
        }
        if http_method != "POST":
            h["X-Http-Method"] = http_method
        if self._cookie:
            h["Cookie"] = self._cookie
        return h

    def merge(
        self,
        list_name: str,
        customer_id: str,
        item_id: int | str,
        fields: dict[str, Any],
    ) -> int:
        """SP 2017 MERGE flow for partial updates.

        Per architect pattern: POST + `X-Http-Method: MERGE` + `__metadata`
        wrapper carrying `SP.Data.<list>_x005f_<customer_id>ListItem`.
        Lazy digest on first call; on 403 we refresh the digest and retry
        the write exactly once per architect Q2.

        Returns the SP HTTP status code (204 = success).
        """
        # `customer_id` is accepted for symmetry with create/delete; the
        # `list_name` already embeds it for `__metadata` type encoding.
        _ = customer_id
        self._ensure_digest()
        url = self._item_url(list_name, item_id)
        body = self._wrap_metadata(list_name, fields)
        return self._post_with_retry(url, http_method="MERGE", body=body)

    def create(
        self,
        list_name: str,
        customer_id: str,
        fields: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        """Standard SP 2017 create: POST + `__metadata` wrapper. Returns
        (status, body)."""
        _ = customer_id
        self._ensure_digest()
        url = self._items_url(list_name)
        body = self._wrap_metadata(list_name, fields)
        status, payload = self._post_with_retry(
            url, http_method="POST", body=body, return_body=True
        )
        return status, payload

    def delete(self, list_name: str, item_id: int | str) -> int:
        """POST + `X-Http-Method: DELETE` + `IF-MATCH: *`."""
        self._ensure_digest()
        url = self._item_url(list_name, item_id)
        return self._post_with_retry(url, http_method="DELETE", body=None)

    # ---- internals ----

    def _items_url(self, list_name: str) -> str:
        quoted = _quote_list_name(list_name)
        return f"{self._site_url}/_api/web/lists/getbytitle('{quoted}')/items"

    def _item_url(self, list_name: str, item_id: int | str) -> str:
        return f"{self._items_url(list_name)}({item_id})"

    def _wrap_metadata(
        self, list_name: str, fields: dict[str, Any]
    ) -> dict[str, Any]:
        return {
            "__metadata": {"type": list_item_type(list_name)},
            **fields,
        }

    def _post_with_retry(
        self,
        url: str,
        *,
        http_method: str,
        body: dict[str, Any] | None,
        return_body: bool = False,
    ) -> Any:
        """POST `body` to `url` with the digest-dance headers. On 403,
        refresh the digest and retry exactly once per architect Q2."""
        data = json.dumps(body) if body is not None else None
        headers = self._write_headers(http_method)
        resp = self._session.post(url, headers=headers, data=data)
        if resp.status_code == 403:
            self._refresh_digest()
            headers = self._write_headers(http_method)
            resp = self._session.post(url, headers=headers, data=data)
        if return_body:
            try:
                payload = resp.json() if resp.content else {}
            except ValueError:
                payload = {}
            return resp.status_code, payload
        return resp.status_code
