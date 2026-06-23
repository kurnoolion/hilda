"""workflow_engine CLI: --diagnostic / --replay-event.

Per [D-005] independent test interface. Emits WFL-RPT / WFL-MET compact reports.

Ph-1 modes:
- `--diagnostic`: validates Celery app config + ACTION_KIND_TO_TASK registry
  completeness against rule_engine.ActionKind + queue routing health.
- `--replay-event`: loads a serialised TriggerEvent JSON; dispatches in dry-run
  mode (no actual apply_async); emits resolution trace.

Deferred per Ph-1 cascade:
- `--queue-stats`: needs running broker; Ph-2.
- `--beat-schedule`: Ph-2 forward-looking per D3 cascade.
"""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.src.diagnostics import ReportRecord, ReportType, ReportWriter
from core.src.rule_engine import ActionKind, TriggerKind

from .celery_app import hilda_celery_app
from .registry import (
    ACTION_KIND_TO_TASK,
    expected_action_kinds_ph1,
    registry_complete,
)

# Importing tasks/ triggers task registration via side effect.
from . import tasks  # noqa: F401


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _emit(writer: ReportWriter, rtype: ReportType, fields: dict) -> None:
    writer.emit(ReportRecord(rtype, "WFL", writer.run_id, _now(), fields))


def _cmd_diagnostic(run_id: str) -> int:
    """Validates Celery + registry. Emits WFL-RPT."""
    writer = ReportWriter("WFL", run_id)

    queues = sorted({route["queue"] for route in hilda_celery_app.conf.task_routes.values()})
    expected = expected_action_kinds_ph1()
    registered = set(ACTION_KIND_TO_TASK.keys())
    missing = expected - registered

    _emit(writer, ReportType.RPT, {
        "broker_url":               hilda_celery_app.conf.broker_url or "<unset>",
        "queues":                   ",".join(queues),
        "action_kinds_registered":  len(registered),
        "action_kinds_expected":    len(expected),
        "registry_complete":        registry_complete(),
        "missing_action_kinds":     ",".join(sorted(k.value for k in missing)) if missing else "none",
        "celery_app_main":          hilda_celery_app.main,
    })
    writer.flush()
    return 0 if not missing else 1


def _cmd_replay_event(run_id: str, event_json_path: Path) -> int:
    """Dry-run dispatch -- load TriggerEvent JSON, surface match resolution
    without enqueuing. Emits WFL-MET."""
    writer = ReportWriter("WFL", run_id)
    try:
        data = json.loads(event_json_path.read_text())
    except (json.JSONDecodeError, FileNotFoundError) as exc:
        print(f"Failed to load event JSON: {exc}", file=sys.stderr)
        return 2

    trigger = data.get("trigger")
    sub_trigger = data.get("sub_trigger")
    try:
        TriggerKind(trigger)
    except ValueError:
        print(f"Unknown trigger '{trigger}'; allowed: {[t.value for t in TriggerKind]}",
              file=sys.stderr)
        return 1

    _emit(writer, ReportType.MET, {
        "mode":         "replay_dry_run",
        "trigger":      trigger,
        "sub_trigger":  sub_trigger or "<none>",
        "loaded":       True,
    })
    writer.flush()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(
        description="workflow_engine CLI -- diagnostics + event replay"
    )
    parser.add_argument("--diagnostic", action="store_true",
                        help="Validate Celery + registry; WFL-RPT")
    parser.add_argument("--replay-event", action="store_true",
                        help="Dry-run dispatch from event JSON; WFL-MET")
    parser.add_argument("--event-json", default=None, help="Path to TriggerEvent JSON for --replay-event")
    parser.add_argument("--run-id", default=None, help="Stable run identifier (default: auto)")

    args = parser.parse_args()
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:8]}"

    if args.diagnostic:
        code = _cmd_diagnostic(run_id)
    elif args.replay_event:
        if not args.event_json:
            parser.error("--replay-event requires --event-json <path>")
        code = _cmd_replay_event(run_id, Path(args.event_json))
    else:
        parser.print_help()
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
