"""Alert payload composer -- subject tag + HTML body badge + plaintext.

Per [D-127] Q1 lock 2026-06-26:
- Subject prefix tag `[<SEVERITY>] HILDA: <source> <error_code>` (RFC 5322
  plain text; works on every mail client).
- HTML body opens with severity badge color-coded:
    red (CRITICAL, ERROR), orange (WARNING), gray (INFO).
- Messenger DM body = email plaintext WITHOUT color/badge (per architect
  "no differentiation in corp messenger communication").

Per [D-127] Invariants 7 + 8:
- Context values >config.context_value_max_chars truncated.
- Credential field keys redacted to `<REDACTED>` regardless of severity.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape as _html_escape
from typing import Any

from core.src.ops_alerts.protocol import Severity

# Credential field keys -- per [D-127] Invariant 8.
_REDACTED_KEYS = frozenset({
    "password",
    "totp_seed",
    "totp_code",
    "auth_header",
    "bearer_token",
    "cookie_value",
    "cred",
    "credential",
    "secret",
    "api_key",
})

_REDACTED_MARKER = "<REDACTED>"
_TRUNCATION_SUFFIX = "...[truncated]"

# Severity → HTML body badge color.
_SEVERITY_BADGE_COLOR = {
    Severity.CRITICAL: "#c0392b",   # red
    Severity.ERROR:    "#c0392b",   # red
    Severity.WARNING:  "#e67e22",   # orange
    Severity.INFO:     "#7f8c8d",   # gray
}


@dataclass(frozen=True)
class ComposedAlert:
    """Output of compose_alert -- ready for email + messenger dispatch."""

    subject:   str        # email subject line
    html_body: str        # email body (text/html)
    plain_body: str       # email body (text/plain) + messenger DM body


def compose_alert(
    *,
    source: str,
    error_code: str,
    context: dict[str, Any],
    severity: Severity,
    subject_prefix_template: str = "[{severity}] HILDA: {source} {error_code}",
    context_value_max_chars: int = 200,
    timestamp: datetime | None = None,
) -> ComposedAlert:
    """Compose an alert payload. Pure function -- no I/O."""
    redacted = _redact_and_truncate(context, context_value_max_chars)
    ts = (timestamp or datetime.now(timezone.utc)).isoformat(timespec="seconds")

    subject = subject_prefix_template.format(
        severity=severity.value.upper(),
        source=source,
        error_code=error_code,
    )
    plain_body = _format_plain_body(source, error_code, severity, ts, redacted)
    html_body = _format_html_body(source, error_code, severity, ts, redacted)
    return ComposedAlert(subject=subject, html_body=html_body, plain_body=plain_body)


def _redact_and_truncate(
    context: dict[str, Any], max_chars: int
) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, raw_value in context.items():
        k = str(key)
        if k.lower() in _REDACTED_KEYS or any(
            r in k.lower() for r in ("password", "totp", "secret", "_key")
        ):
            out[k] = _REDACTED_MARKER
            continue
        value_str = str(raw_value)
        if len(value_str) > max_chars:
            value_str = value_str[: max_chars - len(_TRUNCATION_SUFFIX)] + _TRUNCATION_SUFFIX
        out[k] = value_str
    return out


def _format_plain_body(
    source: str,
    error_code: str,
    severity: Severity,
    ts: str,
    context: dict[str, str],
) -> str:
    lines = [
        f"HILDA alert -- {severity.value.upper()}",
        f"Source:     {source}",
        f"Error code: {error_code}",
        f"Timestamp:  {ts}",
        "",
        "Context:",
    ]
    if not context:
        lines.append("  (none)")
    else:
        for k, v in context.items():
            lines.append(f"  {k} = {v}")
    return "\n".join(lines)


def _format_html_body(
    source: str,
    error_code: str,
    severity: Severity,
    ts: str,
    context: dict[str, str],
) -> str:
    color = _SEVERITY_BADGE_COLOR[severity]
    badge = (
        f'<span style="background:{color};color:#fff;padding:4px 10px;'
        f'border-radius:4px;font-family:sans-serif;font-weight:bold;'
        f'font-size:14px;">{_html_escape(severity.value.upper())}</span>'
    )
    rows = []
    if not context:
        rows.append("<tr><td colspan=\"2\"><em>(none)</em></td></tr>")
    else:
        for k, v in context.items():
            rows.append(
                f"<tr><td style=\"padding:4px 8px;font-family:monospace;"
                f"vertical-align:top;\"><b>{_html_escape(k)}</b></td>"
                f"<td style=\"padding:4px 8px;font-family:monospace;\">"
                f"{_html_escape(v)}</td></tr>"
            )
    return (
        "<!doctype html><html><body style=\"font-family:sans-serif;font-size:14px;\">"
        f"<p>{badge}&nbsp;<b>HILDA alert</b></p>"
        "<table style=\"border-collapse:collapse;border:1px solid #ddd;\">"
        f"<tr><td style=\"padding:4px 8px;\"><b>Source</b></td>"
        f"<td style=\"padding:4px 8px;\">{_html_escape(source)}</td></tr>"
        f"<tr><td style=\"padding:4px 8px;\"><b>Error code</b></td>"
        f"<td style=\"padding:4px 8px;\">{_html_escape(error_code)}</td></tr>"
        f"<tr><td style=\"padding:4px 8px;\"><b>Timestamp</b></td>"
        f"<td style=\"padding:4px 8px;\">{_html_escape(ts)}</td></tr>"
        "</table>"
        "<p><b>Context:</b></p>"
        "<table style=\"border-collapse:collapse;border:1px solid #ddd;\">"
        f"{''.join(rows)}"
        "</table></body></html>"
    )
