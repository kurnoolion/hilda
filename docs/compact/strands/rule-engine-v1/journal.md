# Journal — rule-engine-v1

## 2026-06-12 — session 1: Ph-1 spine implemented end-to-end behind two seams

**Strand opened, bound, development phase.** First-week plan items (1)–(5) done in one
session; module is Ph-1 functionally complete behind the OverrideStore + PauseStateLookup
seams. 84 module tests; full suite 403; CLI reproduces MODULE.md worked examples live.

**Contract conflicts found at dependency-load, architect-ruled same day:**
- (A) FR-31 overrides are ITEM-level; landed storage.AutomationRuleOverride (scope-level)
  is the drifted side → rule_engine owns the OverrideStore seam (D-DRAFT-1); storage-v2
  reshape architect-owned. (B) PauseStateLookup "backed by storage" was wrong — home TBD
  (SP column vs event-carried), Protocol stays injected (D-DRAFT-2). (C) D-DRAFT-3
  RESOLVED option (ii): rule_engine owns the rule grammar; template_schema.RuleActionType
  retires; ActionKind (18) authoritative; PMApproval is a trigger, not an action.

**Implementation:** models (frozen dataclasses; canonical template_schema.RuleScope;
tuples for actions/tiers; TriggerEvent.derived_fields added) → polling_schedule (inclusive
tier boundary) → loader (3-tier walk; RUL-E001..E005; reload(); W008 dual-kind smell) →
resolver (per-rule_id ladder; Ph-1 tiers-only override; source_tier preserved for
"overridden from X") → evaluator (closed-DSL conditions; fail-closed-but-visible W006;
W007 typo'd payload keys; pause W003) → audits (W001 scope-compatible pairs only, W002)
→ config/QC/CLIs. Seed rules in customizations/rules/global/defaults.yaml.

**Architect Q&A round (all 4 answered, assumptions confirmed):** Q1 snapshot model +
writer-triggered reload (TTL field deletion rides with storage-v2); Q2 async list_active
awaited only in load()/reload(); Q3 enum divergence resolves under (ii); Q4 paused-skip
is caller policy (text fixed). Soft fixes applied same session.

**Flags / next session:**
- Wire real OverrideStore + delete TTL field + workload-line rewrite → at storage-v2
  landing (architect pass).
- Production PauseStateLookup → after pause-home decision (D-DRAFT-2).
- Non-blocking check: RearmDeadlineProximity — YAML-authored vs workflow_engine-internal
  (D-DRAFT-3 addendum).
- MODULE.md "Depends on: storage" entry aligned to the seam reality at close-session
  audit (this session).
- workflow_engine integration (evaluate() consumption, SIGHUP wiring, skip policy) =
  caller-side work, not this strand.
- Land-gate: dashboard-v1 → llm-v1 → rule-engine-v1 drafts + storage-v2 (architect
  sequencing 2026-06-12).
- Doctor 2026-06-12 (close-session): 9/12 pass; DEFERRED to architect — skill-inventory
  counts stale in scaffold docs (.claude/skills/README.md says "8 skills", compact/SKILL.md
  says "seven sub-skills"; disk has 13 — the 5 strand skills are missing from both tables).
  Pre-existing; shared scaffold infrastructure, not strand scope.
- regen-map drift (signal): rule_engine code exposes additive public members not yet
  itemized as MODULE.md Public-surface bullets (RuleSet.all_rules/override_for/
  override_count, NoPauseState, InMemoryOverrideStore, RuleEngineConfig, RULE_EVALUATION,
  CLI mains, evaluate_polling_schedule kwargs) — covered in aggregate by the 2026-06-12
  rollback-log entry; itemize on next MODULE.md pass.
