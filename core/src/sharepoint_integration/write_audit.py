"""Instrumentation helpers for the HILDA -> SP write path (SPWLOG-1).

Every SP mutation funnels through `SpClient.{create,update,delete}_list_item`
and, one layer down, `SpSession._post_with_retry`. Before 2026-09-01 a
*successful* write left no trace anywhere: only failures surfaced, as
`PipelineError("SHP-E001")`. That made "did HILDA write this field?"
unanswerable from HILDA's own logs -- it had to be argued from the absence
of audit rows, which is far weaker evidence because SP writeback is
best-effort while the audit write is unconditional.

This module supplies the three things the write-path log lines need:

1.  `sp_write_origin(...)` -- an optional ContextVar label so a caller can
    name itself ("plm_poll.backfill", "transitions.state_change"). The
    codebase has no correlation-id ContextVar, so without this the log can
    only report a stack-derived caller.
2.  `caller_ref()` -- stack-derived `module.function:line` of the first
    frame outside the SP plumbing. Zero call-site changes required, which
    is why it exists alongside (1): the nine existing call sites get
    attribution today, and can adopt the explicit label later.
3.  `redact_fields()` -- key/value summary for the log line.

Privacy per NFR-2: `password`, the WSSAUTH cookie and the `FormDigestValue`
must never reach a log. `redact_fields` masks them by key, and the write-path
log lines deliberately record the URL, HTTP method and status only -- never
request headers.
"""
from __future__ import annotations

import contextlib
import inspect
from contextvars import ContextVar
from typing import Any, Iterator

__all__ = [
    "SP_WRITE_ORIGIN",
    "caller_ref",
    "redact_fields",
    "sp_write_origin",
]

# Explicit caller label. Empty string means "not set" -- log falls back to
# `caller_ref()`.
SP_WRITE_ORIGIN: ContextVar[str] = ContextVar("sp_write_origin", default="")

# Frames belonging to the SP plumbing itself, plus the sync/async bridge and
# stdlib scaffolding it runs on. `caller_ref` walks past all of these to find
# the business-logic frame that actually wanted the write.
_SKIP_PREFIXES = (
    "core.src.sharepoint_integration.",
    "core.src.storage._sync_bridge",
    "asyncio.",
    "concurrent.futures.",
    "contextlib",
    "threading",
)

# Field names whose values must never be logged (NFR-2). Matched as a
# case-insensitive substring so `FormDigestValue`, `X-RequestDigest` and
# `ntlm_pass` are all covered.
_SECRET_HINTS = ("password", "passwd", "pass", "digest", "cookie", "token", "secret")

_MAX_VALUE_CHARS = 80


@contextlib.contextmanager
def sp_write_origin(label: str) -> Iterator[None]:
    """Label every SP write made inside this block.

    Optional -- `caller_ref()` already gives stack-derived attribution. Use
    this when the stack frame is uninformative (a lambda, a shared helper)
    or when you want a stable string to grep for across releases.
    """
    token = SP_WRITE_ORIGIN.set(label)
    try:
        yield
    finally:
        SP_WRITE_ORIGIN.reset(token)


def caller_ref(max_depth: int = 25) -> str:
    """Return `module.function:line` for the nearest non-plumbing frame.

    Falls back to `"?"` when the whole visible stack is plumbing, which
    happens if a write is issued from a bare thread with no business frame
    below it. Never raises -- this runs on the write path and must not be
    able to break a write.
    """
    try:
        frame = inspect.currentframe()
        if frame is not None:
            frame = frame.f_back          # skip caller_ref itself
        depth = 0
        while frame is not None and depth < max_depth:
            module = frame.f_globals.get("__name__", "")
            if not module.startswith(_SKIP_PREFIXES):
                return f"{module}.{frame.f_code.co_name}:{frame.f_lineno}"
            frame = frame.f_back
            depth += 1
    except Exception:  # pragma: no cover - defensive; never break a write
        pass
    return "?"


def _is_secret(key: str) -> bool:
    lowered = key.lower()
    return any(hint in lowered for hint in _SECRET_HINTS)


def redact_fields(fields: dict[str, Any] | None) -> str:
    """Render `fields` as `k=v` pairs for a log line, secrets masked.

    Values are truncated to keep one write to one readable line; `__metadata`
    is dropped because it is a constant type discriminator, not data.
    """
    if not fields:
        return "-"
    parts: list[str] = []
    for key in sorted(fields):
        if key == "__metadata":
            continue
        if _is_secret(key):
            parts.append(f"{key}=***")
            continue
        text = str(fields[key])
        if len(text) > _MAX_VALUE_CHARS:
            text = text[:_MAX_VALUE_CHARS] + f"...<{len(text)}c>"
        parts.append(f"{key}={text}")
    return " ".join(parts) or "-"
