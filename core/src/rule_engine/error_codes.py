"""RUL-prefixed error codes for rule_engine."""
from __future__ import annotations

from core.src.diagnostics import ErrorCode, register_code

_RUL_CODES = [
    ErrorCode("RUL-E001", "Duplicate rule_id '{rule_id}' in scope '{scope}' (scope_keys '{keys}') — fatal at load", False),
    ErrorCode("RUL-E002", "Required field '{field}' missing in rule '{rule_id}' (file '{path}') — fatal at load", False),
    ErrorCode("RUL-E003", "Unknown trigger '{trigger}' in rule '{rule_id}' (file '{path}') — fatal at load; allowed triggers in TriggerKind enum", False),
    ErrorCode("RUL-E004", "Unknown action '{action}' in rule '{rule_id}' (file '{path}') — fatal at load; allowed actions in ActionKind enum (Ph-1)", False),
    ErrorCode("RUL-E005", "Postgres connection failure on AutomationRuleOverride load: {reason}", False),
    ErrorCode("RUL-W001", "UpdateState collision: rules '{r1}' and '{r2}' both write delivery_state on trigger '{trigger}' (scope_keys '{keys}') — ops triage", True),
    ErrorCode("RUL-W002", "Orphan Postgres override: rule_id '{rule_id}' (item '{delivery_item_id}') not present in any YAML tier — likely stale per [D-062]", True),
    ErrorCode("RUL-W003", "Rule '{rule_id}' paused for item '{delivery_item_id}' per FR-31 sub-1 — match returned with pause_state=paused; caller decides whether to skip", True),
    ErrorCode("RUL-W004", "polling_schedule baseline tier missing in rule '{rule_id}' — falling back to default {minutes}min per RuleEngineConfig.default_baseline_minutes", True),
    ErrorCode("RUL-W005", "Condition expression operator '{op}' unsupported in rule '{rule_id}' — rule skipped (evaluator returns no match); ops triage", True),
]

for _code in _RUL_CODES:
    register_code(_code)
