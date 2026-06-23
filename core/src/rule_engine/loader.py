"""YAML rule loader.

Ph-1 (per D7 cascade 2026-06-23): walks `<rules_dir>/global/*.yaml` ONLY. The
per-customer + per-device YAML directories are Ph-2 forward-looking; the walking
code below treats any non-global subdirectory as Ph-2 territory (no rules placed
there in Ph-1 by ops convention; if any happen to be present, they load — but the
Global-only Ph-1 ops contract makes this irrelevant). Ph-2 will actively populate
the Customer + Device tiers.

The OverrideStore param is Ph-2 forward-looking per D4 cascade 2026-06-23: Ph-1
defaults to override_store=None (no Postgres-override consumption); Ph-2 activates
the seam.

Directory tier mapping per FR-30 / MODULE.md:
    <rules_dir>/global/*.yaml                       -> RuleScope.GLOBAL,   scope_keys {}              (Ph-1 + Ph-2)
    <rules_dir>/<customer_id>/*.yaml                -> RuleScope.CUSTOMER, scope_keys {customer_id}   (Ph-2)
    <rules_dir>/<customer_id>/<device_id>/*.yaml    -> RuleScope.DEVICE,   scope_keys {customer_id, device_id} (Ph-2)

Renamed 2026-06-23 per [D-091]: customer_slug -> customer_id; device_slug -> device_id.

Each YAML file may carry two top-level keys: `rules:` (TRIGGER_ACTION shape) and
`polling_schedules:` (POLLING_SCHEDULE shape).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterator

import yaml

from core.src.diagnostics import PipelineError, format_code
from core.src.template_schema import RuleScope

from .collision_audit import CollisionFinding, collision_audit_update_state
from .models import (
    ActionKind,
    PollingScheduleTier,
    Rule,
    RuleAction,
    RuleKind,
    TriggerKind,
)
from .orphan_audit import OrphanFinding, orphan_audit_postgres_overrides
from .override_store import ItemOverride, OverrideStore

__all__ = ["RuleSet"]

logger = logging.getLogger(__name__)

_YAML_GLOBS = ("*.yaml", "*.yml")
_DEFAULT_RULES_DIR = Path("/etc/hilda/customizations/rules")


def _yaml_files(directory: Path) -> Iterator[Path]:
    for pattern in _YAML_GLOBS:
        yield from sorted(directory.glob(pattern))


def _require(entry: dict[str, Any], field_name: str, rule_id: str, path: Path) -> Any:
    value = entry.get(field_name)
    if value is None:
        raise PipelineError("RUL-E002", {"field": field_name, "rule_id": rule_id, "path": str(path)})
    return value


def _parse_trigger_rule(entry: dict[str, Any], scope: RuleScope, scope_keys: dict[str, str], path: Path) -> Rule:
    rule_id = str(_require(entry, "rule_id", "<missing>", path))
    trigger_raw = str(_require(entry, "trigger", rule_id, path))
    try:
        trigger = TriggerKind(trigger_raw)
    except ValueError:
        raise PipelineError("RUL-E003", {"trigger": trigger_raw, "rule_id": rule_id, "path": str(path)}) from None

    actions_raw = _require(entry, "actions", rule_id, path)
    actions: list[RuleAction] = []
    for seq, action_entry in enumerate(actions_raw):
        kind_raw = str(_require(action_entry, "kind", rule_id, path))
        try:
            kind = ActionKind(kind_raw)
        except ValueError:
            raise PipelineError("RUL-E004", {"action": kind_raw, "rule_id": rule_id, "path": str(path)}) from None
        actions.append(RuleAction(kind=kind, params=dict(action_entry.get("params") or {}), sequence=seq))

    try:
        return Rule(
            rule_id=rule_id,
            kind=RuleKind.TRIGGER_ACTION,
            scope=scope,
            scope_keys=scope_keys,
            source="yaml",
            source_file=str(path),
            source_tier=scope,
            trigger=trigger,
            sub_trigger=entry.get("sub_trigger"),
            condition=entry.get("condition"),
            actions=tuple(actions),
        )
    except ValueError as exc:
        raise PipelineError("RUL-E002", {"field": "shape", "rule_id": rule_id, "path": str(path)}, cause=exc) from exc


def _parse_polling_rule(entry: dict[str, Any], scope: RuleScope, scope_keys: dict[str, str], path: Path) -> Rule:
    rule_id = str(_require(entry, "rule_id", "<missing>", path))
    tiers_raw = _require(entry, "tiers", rule_id, path)
    tiers: list[PollingScheduleTier] = []
    for tier_entry in tiers_raw:
        if "interval_minutes" not in tier_entry:
            raise PipelineError("RUL-E002", {"field": "interval_minutes", "rule_id": rule_id, "path": str(path)})
        tiers.append(
            PollingScheduleTier(
                days_before_deadline=tier_entry.get("days_before_deadline"),
                interval_minutes=int(tier_entry["interval_minutes"]),
            )
        )
    try:
        return Rule(
            rule_id=rule_id,
            kind=RuleKind.POLLING_SCHEDULE,
            scope=scope,
            scope_keys=scope_keys,
            source="yaml",
            source_file=str(path),
            source_tier=scope,
            tiers=tuple(tiers),
        )
    except ValueError as exc:
        raise PipelineError("RUL-E002", {"field": "shape", "rule_id": rule_id, "path": str(path)}, cause=exc) from exc


def _parse_file(path: Path, scope: RuleScope, scope_keys: dict[str, str]) -> list[Rule]:
    with path.open("r", encoding="utf-8") as f:
        try:
            doc = yaml.safe_load(f) or {}
        except yaml.YAMLError as exc:
            raise PipelineError("RUL-E002", {"field": "yaml", "rule_id": "<parse>", "path": str(path)}, cause=exc) from exc
    rules: list[Rule] = []
    for entry in doc.get("rules") or []:
        rules.append(_parse_trigger_rule(entry, scope, scope_keys, path))
    for entry in doc.get("polling_schedules") or []:
        rules.append(_parse_polling_rule(entry, scope, scope_keys, path))
    return rules


class RuleSet:
    """Container for the loaded set of rules across all 3 YAML tiers + item-level overrides.

    Construct via RuleSet.load(). IO (YAML read + OverrideStore.list_active()) happens only
    here and in reload() — never per-evaluate, per the pure-evaluator invariant ([D-022])."""

    def __init__(
        self,
        rules: list[Rule],
        overrides: dict[tuple[str, str], ItemOverride],
        rules_dir: Path,
        override_store: OverrideStore | None,
        orphan_findings: list[OrphanFinding],
        collision_findings: list[CollisionFinding],
    ) -> None:
        self._rules = rules
        self._overrides = overrides  # keyed (delivery_item_id, rule_id)
        self._rules_dir = rules_dir
        self._override_store = override_store
        self.orphan_findings = orphan_findings
        self.collision_findings = collision_findings

    @classmethod
    def load(
        cls,
        rules_dir: Path = _DEFAULT_RULES_DIR,
        override_store: OverrideStore | None = None,
        *,
        collision_audit_on_load: bool = True,
        orphan_audit_on_load: bool = True,
    ) -> "RuleSet":
        """Load all YAML files under rules_dir + active item-level overrides via the injected
        OverrideStore (D-DRAFT-1 seam). Runs orphan_audit + collision_audit (warnings, does NOT
        abort). Raises RUL-E001 on duplicate rule_id within the same scope+kind bucket; RUL-E002
        on shape mismatch or missing required field; RUL-E003 on unknown trigger; RUL-E004 on
        unknown action; RUL-E005 on OverrideStore failure."""
        rules: list[Rule] = []

        global_dir = rules_dir / "global"
        if global_dir.is_dir():
            for path in _yaml_files(global_dir):
                rules.extend(_parse_file(path, RuleScope.GLOBAL, {}, ))

        if rules_dir.is_dir():
            for customer_dir in sorted(p for p in rules_dir.iterdir() if p.is_dir() and p.name != "global"):
                if customer_dir.name.startswith((".", "_")):
                    continue
                customer_keys = {"customer_id": customer_dir.name}     # [D-091] slug->id rename
                for path in _yaml_files(customer_dir):
                    rules.extend(_parse_file(path, RuleScope.CUSTOMER, customer_keys))
                for device_dir in sorted(p for p in customer_dir.iterdir() if p.is_dir()):
                    if device_dir.name.startswith((".", "_")):
                        continue
                    device_keys = {**customer_keys, "device_id": device_dir.name}    # [D-091] slug->id rename
                    for path in _yaml_files(device_dir):
                        rules.extend(_parse_file(path, RuleScope.DEVICE, device_keys))

        seen: set[tuple[RuleScope, tuple[tuple[str, str], ...], RuleKind, str]] = set()
        kinds_by_id: dict[tuple[RuleScope, tuple[tuple[str, str], ...], str], set[RuleKind]] = defaultdict(set)
        for rule in rules:
            keys_t = tuple(sorted(rule.scope_keys.items()))
            bucket = (rule.scope, keys_t, rule.kind, rule.rule_id)
            if bucket in seen:
                raise PipelineError(
                    "RUL-E001",
                    {"rule_id": rule.rule_id, "scope": rule.scope.value, "keys": str(rule.scope_keys)},
                )
            seen.add(bucket)
            kinds_by_id[(rule.scope, keys_t, rule.rule_id)].add(rule.kind)
        # RUL-W008 config-smell per architect ruling 2026-06-12: same rule_id under both kinds
        # in the same scope is legal (the uniqueness key includes kind) but confusing for
        # orphan-audit and --explain readability.
        for (scope, keys_t, rule_id), kinds in kinds_by_id.items():
            if len(kinds) > 1:
                logger.warning(
                    "RUL-W008: " + format_code(
                        "RUL-W008", rule_id=rule_id, scope=scope.value, keys=str(dict(keys_t)),
                    )
                )

        overrides: dict[tuple[str, str], ItemOverride] = {}
        if override_store is not None:
            try:
                active = override_store.list_active()
            except Exception as exc:
                raise PipelineError("RUL-E005", {"reason": str(exc)}, cause=exc) from exc
            for o in active:
                overrides[(o.delivery_item_id, o.rule_id)] = o

        yaml_rule_ids = {r.rule_id for r in rules}
        orphan_findings: list[OrphanFinding] = []
        if orphan_audit_on_load and overrides:
            orphan_findings = orphan_audit_postgres_overrides(yaml_rule_ids, list(overrides.values()))

        collision_findings: list[CollisionFinding] = []
        if collision_audit_on_load:
            by_trigger: dict[TriggerKind, list[Rule]] = defaultdict(list)
            for rule in rules:
                if rule.kind is RuleKind.TRIGGER_ACTION and rule.trigger is not None:
                    by_trigger[rule.trigger].append(rule)
            collision_findings = collision_audit_update_state(dict(by_trigger))

        return cls(rules, overrides, rules_dir, override_store, orphan_findings, collision_findings)

    def reload(self) -> None:
        """Re-runs load() (SIGHUP + FR-31 override change events). Idempotent. Pre-reload()
        evaluations keep the prior snapshot — no torn state."""
        fresh = RuleSet.load(self._rules_dir, self._override_store)
        self._rules = fresh._rules
        self._overrides = fresh._overrides
        self.orphan_findings = fresh.orphan_findings
        self.collision_findings = fresh.collision_findings

    # -- query surface ------------------------------------------------------

    def rules_for_scope(self, scope: RuleScope, scope_keys: dict[str, str]) -> list[Rule]:
        """Full set of rules at the given (scope, scope_keys) — pre-resolution."""
        return [r for r in self._rules if r.scope is scope and r.scope_keys == scope_keys]

    def all_rule_ids(self) -> set[str]:
        """Union of all rule_ids across all tiers + overrides."""
        return {r.rule_id for r in self._rules} | {rule_id for (_item, rule_id) in self._overrides}

    def all_rules(self) -> list[Rule]:
        return list(self._rules)

    def override_for(self, delivery_item_id: str | None, rule_id: str) -> ItemOverride | None:
        """The active FR-31 override for (item, rule_id), if any."""
        if delivery_item_id is None:
            return None
        return self._overrides.get((delivery_item_id, rule_id))

    @property
    def override_count(self) -> int:
        return len(self._overrides)
