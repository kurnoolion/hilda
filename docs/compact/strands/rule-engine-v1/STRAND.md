# rule-engine-v1

**Status:** in-flight
**Opened:** 2026-06-12
**Landed:**
**Assignees:** dileepkratnala
**Target modules:** rule_engine
**Active phase:** development

## Summary

Ph-1 implementation of rule_engine per [D-022] pure evaluator boundary (no Celery / no SP write / no DB writes). 8 sub-modules: models / loader / resolver / evaluator / polling_schedule / orphan_audit / collision_audit / pause_state. 15 Ph-1 TriggerKinds, 18 Ph-1 ActionKinds. Rule.kind discriminator (TRIGGER_ACTION vs POLLING_SCHEDULE) per [D-066]; per-trigger ordered action lists (no priority / no first-match / no score). YAML loader reads customizations/rules/<customer_slug>/*.yaml; Postgres overrides via storage.list_active_overrides per FR-31.

## Notes

**Branch:** main (or feature branch)

**First week plan:**
1. Read rule_engine/MODULE.md end-to-end (~315 lines)
2. Run the 3 worked examples in MODULE.md mentally — they pin the API contracts
3. Implement models.py (Rule, RuleSet, RuleMatch, TriggerEvent, RuleAction, PollingScheduleTier, EntityRef)
4. Implement loader.py (YAML → RuleSet) with strict validation against template_schema.RuleActionRegistry/RuleTriggerRegistry
5. Implement resolver.py (3-tier Scope precedence: Device > Customer > Global per FR-30)
