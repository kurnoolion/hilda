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
from core.src.rule_engine.orphan_audit import OrphanFinding, orphan_audit_postgres_overrides
from core.src.rule_engine.collision_audit import CollisionFinding, collision_audit_update_state
from core.src.rule_engine.override_store import InMemoryOverrideStore, ItemOverride, OverrideStore
from core.src.rule_engine.pause_state import NoPauseState, PauseStateLookup
from core.src.rule_engine.polling_schedule import evaluate_polling_schedule
from core.src.rule_engine.resolver import (
    resolve_polling_schedule_for_item,
    resolve_rules_for_entity,
)

__all__ = [
    "ITEM_MODIFIED_SUB_TRIGGERS_PH1",
    "ActionKind",
    "CollisionFinding",
    "EntityRef",
    "InMemoryOverrideStore",
    "ItemOverride",
    "NoPauseState",
    "OrphanFinding",
    "OverrideStore",
    "PauseStateLookup",
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
    "orphan_audit_postgres_overrides",
    "resolve_polling_schedule_for_item",
    "resolve_rules_for_entity",
]
