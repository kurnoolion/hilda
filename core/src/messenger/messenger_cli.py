"""messenger CLI.

Modes:
  --diagnostic   -- config + template loading + adapter availability RPT
  --mock         -- spin up MockMessengerAdapter + run a synthetic send_escalation
                    end-to-end (composer + daily-limit + audit) MET
  --send         -- real send harness (per-customer subclass required)
                    --owner <id> --message <text> -- emits MSG-MET

Per NFR-2: no proprietary content in compact reports. owner_corp_id is redacted;
message body is never echoed.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.src.diagnostics import ReportRecord, ReportType, ReportWriter
from core.src.diagnostics.error_codes import PipelineError
from core.src.messenger.composer import REQUIRED_TEMPLATE_VARS, get_template_env
from core.src.messenger.config import MessengerConfig
from core.src.messenger.corp_messenger import CorpMessengerAdapter
from core.src.messenger.daily_limit import DailyLimitChecker
from core.src.messenger.mocks import InMemoryMessengerStorage, MockMessengerAdapter
from core.src.messenger.protocol import EscalationReason
from core.src.messenger.service import MessengerService
from core.src.messenger.utils import redact_owner_corp_id


def _make_run_id() -> str:
    return f"msg-{uuid.uuid4().hex[:8]}"


async def _cmd_diagnostic(writer: ReportWriter, config: MessengerConfig) -> int:
    """Validate config + template availability + base adapter."""
    template_ok = False
    template_error = ""
    try:
        env = get_template_env(config.templates_dir)
        env.get_template("escalation.j2")
        template_ok = True
    except Exception as exc:  # noqa: BLE001
        template_error = type(exc).__name__

    base_adapter_available = True
    try:
        # Constructor sanity check only -- never actually sends.
        CorpMessengerAdapter(config=config)
    except Exception:  # noqa: BLE001
        base_adapter_available = False

    writer.emit(
        ReportRecord(
            report_type=ReportType.RPT,
            module_prefix="MSG",
            run_id=writer.run_id,
            timestamp=datetime.now(timezone.utc),
            fields={
                "mode": "diagnostic",
                "daily_limit_per_owner": config.daily_limit_per_owner,
                "max_message_bytes": config.max_message_bytes,
                "retry_attempts": config.retry_attempts,
                "templates_dir_exists": Path(config.templates_dir).exists(),
                "template_loaded": template_ok,
                "template_error": template_error or "none",
                "required_vars_count": len(REQUIRED_TEMPLATE_VARS),
                "base_adapter_available": base_adapter_available,
            },
        )
    )
    return 0 if template_ok and base_adapter_available else 1


async def _cmd_mock(writer: ReportWriter, config: MessengerConfig) -> int:
    """Run a synthetic end-to-end escalation through the mock adapter."""
    storage = InMemoryMessengerStorage()
    adapter = MockMessengerAdapter()
    checker = DailyLimitChecker(storage=storage, config=config)
    # Force backoff=0 in CLI mock mode -- never sleep when retrying canned failures.
    cfg = config.model_copy(update={"retry_backoff_seconds": 0.0})
    service = MessengerService(adapter=adapter, daily_checker=checker, config=cfg)

    item = {
        "item_id": "ITEM-MOCK",
        "owner_corp_id": "test_owner_001",
        "device_id": "MODEL-A",
    }
    milestone_ctx = {
        "owner_name": "Test Owner",
        "deliverable_count": 2,
        "milestone_name": "P1",
        "device_id": "MODEL-A",
        "deadline_date": "2026-07-01",
        "corp_plm_link": "https://corp_plm.example/PLM-1",
        "reminder_count": 2,
    }
    result = await service.send_escalation(
        item=item,
        batch_id="MOCK-BATCH",
        reason=EscalationReason.POST_REMINDER,
        milestone_ctx=milestone_ctx,
    )
    writer.emit(
        ReportRecord(
            report_type=ReportType.MET,
            module_prefix="MSG",
            run_id=writer.run_id,
            timestamp=datetime.now(timezone.utc),
            fields={
                "mode": "mock",
                "owner_redacted": redact_owner_corp_id(result.owner_corp_id),
                "success": result.success,
                "message_bytes": result.message_bytes,
                "error_code": result.error_code or "none",
                "audit_rows_written": len(storage.communications),
                "adapter_calls": len(adapter.calls),
            },
        )
    )
    return 0 if result.success else 1


async def _cmd_send(
    writer: ReportWriter,
    config: MessengerConfig,
    owner_corp_id: str,
    message: str,
) -> int:
    """Real send harness -- requires a per-customer subclass at customizations/.

    The base CorpMessengerAdapter raises MSG-E003 on send -- so this command
    expects the user to have invoked it via a subclass entry-point. Ph-1 CLI
    simply attempts via the base class + surfaces MSG-E003 cleanly."""
    adapter = CorpMessengerAdapter(config=config)
    try:
        ok = await adapter.send(owner_corp_id, message)
    except PipelineError as exc:
        writer.emit(
            ReportRecord(
                report_type=ReportType.MET,
                module_prefix="MSG",
                run_id=writer.run_id,
                timestamp=datetime.now(timezone.utc),
                fields={
                    "mode": "send",
                    "owner_redacted": redact_owner_corp_id(owner_corp_id),
                    "success": False,
                    "error_code": exc.code_id,
                },
            )
        )
        return 2
    writer.emit(
        ReportRecord(
            report_type=ReportType.MET,
            module_prefix="MSG",
            run_id=writer.run_id,
            timestamp=datetime.now(timezone.utc),
            fields={
                "mode": "send",
                "owner_redacted": redact_owner_corp_id(owner_corp_id),
                "success": ok,
                "message_bytes": len(message.encode("utf-8")),
                "error_code": "none" if ok else "MSG-E001",
            },
        )
    )
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="messenger_cli")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--diagnostic", action="store_true")
    mode.add_argument("--mock", action="store_true")
    mode.add_argument("--send", action="store_true")
    parser.add_argument("--owner", default=None)
    parser.add_argument("--message", default=None)
    args = parser.parse_args(argv)

    config = MessengerConfig.from_sources()
    writer = ReportWriter(module_prefix="MSG", run_id=_make_run_id())

    if args.diagnostic:
        rc = asyncio.run(_cmd_diagnostic(writer, config))
    elif args.mock:
        rc = asyncio.run(_cmd_mock(writer, config))
    elif args.send:
        if not args.owner or args.message is None:
            parser.error("--send requires --owner <id> --message <text>")
        rc = asyncio.run(_cmd_send(writer, config, args.owner, args.message))
    else:  # pragma: no cover -- argparse mutually-exclusive guards this
        rc = 2
    writer.flush()
    return rc


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
