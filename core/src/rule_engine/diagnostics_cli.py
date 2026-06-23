"""rule_engine diagnostic CLI — --diagnostic / --validate / --explain modes per [D-005].

Safe in any environment: no Postgres writes, no network, no side effects. Loads YAML rules
only; the FR-31 OverrideStore seam is not wired here (postgres_overrides=0) until the
storage reconciliation lands (D-DRAFT-1).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.src.diagnostics import PipelineError, ReportType, ReportWriter

from .config import RuleEngineConfig
from .evaluator import RuleEngine
from .loader import RuleSet
from .models import (
    ITEM_MODIFIED_SUB_TRIGGERS_PH1,
    ActionKind,
    EntityRef,
    RuleKind,
    TriggerEvent,
    TriggerKind,
)
# PauseStateLookup Protocol dropped per D5 cascade 2026-06-23 — CLI runs without item
# snapshot context (no pause check; --explain shows pause_state=None for matched rules).

__all__ = ["main"]

# FR-28 counts ItemModified's sub-triggers individually: 12 non-ItemModified + 3 sub-triggers.
_PH1_TRIGGER_COUNT = (len(TriggerKind) - 1) + len(ITEM_MODIFIED_SUB_TRIGGERS_PH1)


def _load(rules_dir: Path) -> RuleSet:
    return RuleSet.load(rules_dir)


def _cmd_diagnostic(rules_dir: Path, run_id: str) -> int:
    writer = ReportWriter("RUL", run_id)
    rule_set = _load(rules_dir)
    rules = rule_set.all_rules()
    scopes = {r.scope for r in rules}
    writer.make(
        ReportType.RPT,
        {
            "tiers_loaded": len(scopes),
            "rules_total": len(rules),
            "rules_global": sum(r.scope.value == "Global" for r in rules),
            "rules_customer": sum(r.scope.value == "Customer" for r in rules),
            "rules_device": sum(r.scope.value == "Device" for r in rules),
            "postgres_overrides": rule_set.override_count,
            "orphan_warnings": len(rule_set.orphan_findings),
            "collision_warnings": len(rule_set.collision_findings),
            "trigger_kinds": _PH1_TRIGGER_COUNT,
            "action_kinds": len(ActionKind),
        },
    )
    writer.flush()
    return 0


def _cmd_validate(rules_dir: Path, run_id: str) -> int:
    writer = ReportWriter("RUL", run_id)
    try:
        rule_set = _load(rules_dir)
    except PipelineError as exc:
        print(str(exc), file=sys.stderr)
        writer.make(ReportType.QC, {"rules_total": 0, "load_error": exc.code_id, "result": "FAIL"})
        writer.flush()
        return 1
    writer.make(
        ReportType.QC,
        {
            "rules_total": len(rule_set.all_rules()),
            "orphan_warnings": len(rule_set.orphan_findings),
            "collision_warnings": len(rule_set.collision_findings),
            "result": "WARN" if (rule_set.orphan_findings or rule_set.collision_findings) else "OK",
        },
    )
    writer.flush()
    return 0


def _cmd_explain(
    rules_dir: Path,
    run_id: str,
    trigger: str,
    entity_json: str,
    sub_trigger: str | None,
    field_deltas_json: str | None,
    derived_fields_json: str | None,
) -> int:
    try:
        trigger_kind = TriggerKind(trigger)
    except ValueError:
        print(f"RUL-E003: unknown trigger '{trigger}' — allowed: {[t.value for t in TriggerKind]}", file=sys.stderr)
        return 1
    entity = EntityRef(**json.loads(entity_json))
    field_deltas = (
        {k: tuple(v) for k, v in json.loads(field_deltas_json).items()} if field_deltas_json else None
    )
    derived_fields = json.loads(derived_fields_json) if derived_fields_json else None

    rule_set = _load(rules_dir)
    engine = RuleEngine(rule_set)        # Ph-1: no pause_lookup; item_snapshot passed per-evaluate per D5 cascade
    event = TriggerEvent(
        trigger=trigger_kind,
        sub_trigger=sub_trigger,
        entity_ref=entity,
        field_deltas=field_deltas,
        timestamp=datetime.now(timezone.utc),
        correlation_id=run_id,
        derived_fields=derived_fields,
    )
    started = time.perf_counter()
    trace = engine.explain(event)
    latency_ms = int((time.perf_counter() - started) * 1000)

    matched = [t for t in trace if t["matched"]]
    writer = ReportWriter("RUL", run_id)
    writer.make(
        ReportType.MET,
        {
            "trigger": trigger_kind.value,
            "matched_rules": len(matched),
            "matches": ",".join(f"{t['rule_id']}:{t['winning_scope'].lower()}" for t in matched) or "none",
            "paused_matches": sum(t["pause_state"] == "paused" for t in matched),
            "eval_latency_ms": latency_ms,
        },
    )
    writer.flush()
    for t in trace:
        print(json.dumps(t, default=str))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rule_engine_cli")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--diagnostic", action="store_true")
    group.add_argument("--validate", action="store_true")
    group.add_argument("--explain", action="store_true")
    group.add_argument("--simulate", metavar="YAML_PATH", help="Ph-3+ extension hook (not implemented)")
    parser.add_argument("--rules-dir", type=Path, default=None)
    parser.add_argument("--run-id", default=f"run-{uuid.uuid4().hex[:8]}")
    parser.add_argument("--trigger", help="TriggerKind value for --explain")
    parser.add_argument("--entity", help='JSON EntityRef for --explain, e.g. \'{"customer_slug":"c1"}\'')
    parser.add_argument("--sub-trigger", default=None)
    parser.add_argument("--field-deltas", default=None, help='JSON {field: [old, new]} for --explain')
    parser.add_argument("--derived-fields", default=None, help='JSON {field: value} for --explain')
    args = parser.parse_args(argv)

    config = RuleEngineConfig.from_sources(cli_overrides={"rules_dir": args.rules_dir})
    rules_dir = config.rules_dir

    if args.simulate:
        print("--simulate is a Ph-3+ extension hook (rule_engine/MODULE.md ## Deferred); not implemented.",
              file=sys.stderr)
        return 2
    try:
        if args.diagnostic:
            return _cmd_diagnostic(rules_dir, args.run_id)
        if args.validate:
            return _cmd_validate(rules_dir, args.run_id)
        if not args.trigger or not args.entity:
            parser.error("--explain requires --trigger and --entity")
        return _cmd_explain(
            rules_dir, args.run_id, args.trigger, args.entity,
            args.sub_trigger, args.field_deltas, args.derived_fields,
        )
    except PipelineError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
