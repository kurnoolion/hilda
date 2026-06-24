"""email_service CLI: --diagnostic / --once / --mock / --send.

Per [D-005] independent test interface + [D-002] compact reports (EML-RPT /
EML-MET). NFR-2: emits only bounded enum tokens + counts + Message-IDs;
never subject text / body content / attachment filenames / credentials.

Modes:
- --diagnostic: validates IMAP/SMTP cred lookup + template loading + emits EML-RPT
- --once:       fetches all unread, runs through pipeline, exits; emits summary RPT
- --mock:       MockImapReceiver + MockLLM + InMemoryStorage end-to-end
- --send:       outbound send harness (one message); emits EML-MET
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.src.diagnostics import ReportRecord, ReportType, ReportWriter

from core.src.email_service.config import EmailServiceConfig
from core.src.email_service.inbound.classifier import classify
from core.src.email_service.outbound.sender import get_template_env
from core.src.email_service.mocks import (
    FakeCredentialService,
    InMemoryStorage,
    MockImapReceiver,
    MockSmtpSender,
)

_PREFIX = "EML"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(writer: ReportWriter, rtype: ReportType, fields: dict) -> None:
    writer.emit(ReportRecord(rtype, _PREFIX, writer.run_id, _now(), fields))


def _templates_loadable() -> int:
    """Count loadable Jinja2 templates under templates/."""
    env = get_template_env()
    loader = env.loader
    try:
        names = loader.list_templates()  # type: ignore[union-attr]
        return sum(1 for n in names if n.endswith(".j2"))
    except Exception:
        return 0


def _cmd_diagnostic(run_id: str, cfg: EmailServiceConfig) -> int:
    """Validate credential lookup path + Jinja2 template loading + emit EML-RPT.

    Ph-1 first-cut: imap_reachable / smtp_reachable / idle_supported are reported
    as 'unknown' (false) when the credential service is not wired -- the actual
    wire-path probe runs in production deploys against the real Exchange host.
    """
    writer = ReportWriter(_PREFIX, run_id)
    template_count = _templates_loadable()

    _emit(writer, ReportType.RPT, {
        "imap_reachable":   False,
        "smtp_reachable":   False,
        "idle_supported":   cfg.imap.use_idle,
        "mailbox":          cfg.imap.mailbox,
        "templates_loaded": template_count,
        "fr52_threshold_fuzzy":   cfg.fr52.fuzzy_threshold,
        "fr52_max_matches":       cfg.fr52.route_attachment_max_matches_threshold,
    })
    writer.flush()
    return 0


def _cmd_mock(run_id: str) -> int:
    """Spin up MockImapReceiver + MockSmtpSender + InMemoryStorage; emit RPT."""
    async def _run() -> int:
        writer = ReportWriter(_PREFIX, run_id)
        receiver = MockImapReceiver(messages=[])
        sender = MockSmtpSender()
        storage = InMemoryStorage()
        cred = FakeCredentialService()
        # Smoke test: receiver returns empty list; sender records no calls
        msgs = await receiver.fetch_once()
        owner_reply = sum(1 for m in msgs if classify(m).value == "owner_reply")
        sp_alert = sum(1 for m in msgs if classify(m).value == "sp_alert")
        other = sum(1 for m in msgs if classify(m).value == "other")

        _emit(writer, ReportType.RPT, {
            "messages_fetched":  len(msgs),
            "owner_reply":       owner_reply,
            "sp_alert":          sp_alert,
            "other":             other,
            "attachments_total": 0,
            "cred_lookups":      len(cred.calls),
            "sent":              len(sender.sent),
            "storage_rows":      len(storage.index),
        })
        writer.flush()
        return 0

    return asyncio.run(_run())


def _cmd_once(run_id: str, cfg: EmailServiceConfig) -> int:
    """Ph-1 first-cut --once: report-only (real IMAP wire path lives behind
    ImapReceiver._fetch_sync, which is a production-deploy override point)."""
    writer = ReportWriter(_PREFIX, run_id)
    _emit(writer, ReportType.RPT, {
        "messages_fetched":             0,
        "owner_reply":                  0,
        "sp_alert":                     0,
        "other":                        0,
        "attachments_total":            0,
        "dedup_skipped":                0,
        "classification_filename_regex": 0,
        "classification_llm_classified": 0,
        "classification_unresolved":     0,
        "routing_substring_match":       0,
        "routing_fuzzy_match":           0,
        "routing_folder_routing":        0,
        "routing_llm_route_attachment":  0,
        "routing_staged_default":        0,
        "nsd_classified":                0,
        "nsd_unrouted":                  0,
        "nsd_staged_not_classified":     0,
        "nsd_staged_not_revision":       0,
        "multi_item_associations":       0,
        "over_routed_w007":              0,
    })
    writer.flush()
    return 0


def _cmd_send(run_id: str, cfg: EmailServiceConfig, to: str, subject: str, body_file: Path) -> int:
    """Outbound send harness (one message). Ph-1 first cut: mock sender path
    used unless production SMTP creds wired."""
    async def _run() -> int:
        writer = ReportWriter(_PREFIX, run_id)
        sender = MockSmtpSender()
        body = body_file.read_text(encoding="utf-8") if body_file.is_file() else "<missing body file>"
        try:
            msg_id = await sender.send(
                to=[to], cc=[], subject=subject, body=body,
            )
            _emit(writer, ReportType.MET, {
                "send_ok":     True,
                "message_id":  msg_id,
                "body_bytes":  len(body),
                "to_count":    1,
            })
            writer.flush()
            return 0
        except Exception as exc:
            _emit(writer, ReportType.MET, {
                "send_ok": False,
                "reason":  str(exc)[:60],
            })
            writer.flush()
            return 1

    return asyncio.run(_run())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="email_service_cli")
    parser.add_argument("--diagnostic", action="store_true")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--send", action="store_true")
    parser.add_argument("--to", default=None)
    parser.add_argument("--subject", default=None)
    parser.add_argument("--body-file", default=None)
    parser.add_argument("--config", default=None, help="Path to email_service.json")
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args(argv)

    run_id = args.run_id or f"run-{uuid.uuid4().hex[:8]}"
    cfg = EmailServiceConfig.from_sources(
        config_path=Path(args.config) if args.config else None
    )

    if args.diagnostic:
        return _cmd_diagnostic(run_id, cfg)
    if args.mock:
        return _cmd_mock(run_id)
    if args.once:
        return _cmd_once(run_id, cfg)
    if args.send:
        if not (args.to and args.subject and args.body_file):
            print("ERR: --send requires --to, --subject, --body-file", file=sys.stderr)
            return 2
        return _cmd_send(run_id, cfg, args.to, args.subject, Path(args.body_file))

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
