"""URLPFX-1 (2026-09-07) -- the public path prefix all TPM-facing URLs sit under.

nginx serves HILDA at `<ip>:8443/hilda/*` and STRIPS the prefix before
proxying (`proxy_pass http://hilda_api/` with the trailing slash), so the app
still receives and matches unprefixed paths: a browser request for
`/hilda/browse/MMK/SM-S671U1/P1/` reaches FastAPI as `/browse/...`.

The asymmetry is deliberate. Route declarations, and the 154 test call sites
that exercise them, stay exactly as they were -- tests drive the app directly
and see the same paths nginx forwards in production. Only what the app EMITS
has to carry the prefix: hrefs, form actions, redirect targets, and the
`download_url` values embedded in page payloads. Emit an unprefixed path and
the browser leaves `/hilda/`, where nginx no longer proxies anything.

Everything that builds an outbound URL goes through `join()` or the
`url_prefix` Jinja global, so the prefix has one definition rather than forty
string literals. Set it to "" to serve at the root again -- old paths keep
working through the nginx 301s either way.
"""
from __future__ import annotations

__all__ = ["DEFAULT_URL_PREFIX", "ENV_VAR", "join", "normalize"]

DEFAULT_URL_PREFIX = "/hilda"
ENV_VAR = "HILDA_DASHBOARD_URL_PREFIX"


def normalize(raw: str | None) -> str:
    """Canonical form: "" or "/seg" with no trailing slash.

    Accepts the shapes a human writes in JSON or an env var -- "hilda",
    "/hilda", "/hilda/", "  /hilda  " -- because a stray slash here would
    produce `//browse/...` in every link on every page, which browsers treat
    as a protocol-relative URL and resolve against the wrong host.
    """
    text = (raw or "").strip().strip("/")
    return f"/{text}" if text else ""


def join(prefix: str, path: str) -> str:
    """Prefix an app-internal absolute path for emission to a browser.

    `path` is the route as declared (leading slash, unprefixed). Idempotent:
    a path already carrying the prefix is returned unchanged, so a double
    application during refactoring cannot produce `/hilda/hilda/browse/...`.
    """
    if not path.startswith("/"):
        path = f"/{path}"
    pfx = normalize(prefix)
    if not pfx:
        return path
    if path == pfx or path.startswith(f"{pfx}/"):
        return path
    return f"{pfx}{path}"
