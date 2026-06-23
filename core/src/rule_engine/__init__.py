"""rule_engine — pure evaluator for HILDA's IF/THEN AutomationRules per [D-022].

See core/src/rule_engine/MODULE.md.
"""
# Register error codes + QC template on import.
from core.src.rule_engine import error_codes, qc_templates  # noqa: F401
from core.src.rule_engine.config import RuleEngineConfig
from core.src.rule_engine.evaluator import RuleEngine
from core.src.rule_engine.loader import RuleSet
from core.src.rule_engine.models import (
    ITEM_MODIFIED_SUB_TRIGGERS_PH1,
    ActionKind,
    EntityRef,
    PollingScheduleTier,
    Rule,
    RuleAction,
    RuleKind,
    RuleMatch,
    RuleScope,
    TriggerEvent,
    TriggerKind,
)
from core.src.rule_engine.collision_audit import CollisionFinding, collision_audit_update_state
from core.src.rule_engine.polling_schedule import evaluate_polling_schedule
from core.src.rule_engine.resolver import (
    resolve_polling_schedule_for_item,
    resolve_rules_for_entity,
)

# Ph-2 forward-looking surface (per D4 + D5 cascade 2026-06-23) -- still importable for
# tests + Ph-2 development, but NOT in Ph-1 public __all__:
#   from core.src.rule_engine.orphan_audit import OrphanFinding, orphan_audit_postgres_overrides
#   from core.src.rule_engine.override_store import InMemoryOverrideStore, ItemOverride, OverrideStore
# Note: PauseStateLookup Protocol DROPPED per [D-112] -- pause state is read directly from
# item_snapshot.rules_paused via the SP column per [D-108].

__all__ = [
    "ITEM_MODIFIED_SUB_TRIGGERS_PH1",
    "ActionKind",
    "CollisionFinding",
    "EntityRef",
    "PollingScheduleTier",
    "Rule",
    "RuleAction",
    "RuleEngine",
    "RuleEngineConfig",
    "RuleKind",
    "RuleMatch",
    "RuleScope",
    "RuleSet",
    "TriggerEvent",
    "TriggerKind",
    "collision_audit_update_state",
    "evaluate_polling_schedule",
    "resolve_polling_schedule_for_item",
    "resolve_rules_for_entity",
]
