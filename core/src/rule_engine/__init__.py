"""rule_engine — pure evaluator for HILDA's IF/THEN AutomationRules per [D-022].

See core/src/rule_engine/MODULE.md.
"""
# Register error codes on import.
from core.src.rule_engine import error_codes  # noqa: F401
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
from core.src.rule_engine.polling_schedule import evaluate_polling_schedule

__all__ = [
    "ITEM_MODIFIED_SUB_TRIGGERS_PH1",
    "ActionKind",
    "EntityRef",
    "PollingScheduleTier",
    "Rule",
    "RuleAction",
    "RuleKind",
    "RuleMatch",
    "RuleScope",
    "TriggerEvent",
    "TriggerKind",
    "evaluate_polling_schedule",
]
