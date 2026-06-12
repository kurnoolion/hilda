# Draft decisions — rule-engine-v1

*Promoted to canonical DECISIONS.md at `/land-strand`. Per architect ruling 2026-06-12,
the cross-module entries (D-DRAFT-1, D-DRAFT-2, and the cross-module half of D-DRAFT-3)
will be promoted by the architect directly — they touch two landed modules (`storage`,
`template_schema`) and reframe `[D-062]`, and DECISIONS.md numbering is shared across the
dashboard-v1 / llm-v1 / rule-engine-v1 strands. They are recorded here as the strand-side
record of the conflict discovery + the seam rule_engine builds against.*

---

## D-DRAFT-1 — FR-31 overrides are item-level; storage's scope-level AutomationRuleOverride is drift; rule_engine builds against its own OverrideStore Protocol

**Date:** 2026-06-12
**Status:** draft — architect-ruled 2026-06-12 (direction confirmed; cross-module reconciliation owned by architect)

**Context:** `rule_engine/MODULE.md` (resolver step 4, Worked Example 3) models FR-31
Postgres overrides as per-`(rule_id × delivery_item_id)` records with a payload that can
carry polling tiers. The landed `storage.AutomationRuleOverride` (storage-v1, models.py:210)
is scope-level instead: `(scope: Global|Customer|Device, scope_id, rule_id, parameter_name,
parameter_value: str, set_by_pm_id, set_at, expires_at)` — no `delivery_item_id`, no payload.
The two contracts cannot both be right.

**Decision (architect):** rule_engine + FR-31 are right; storage is the drifted side.
FR-31 sub-2 (requirements.md:219) and FR-30 (requirements.md:216) both specify *item-level*
override records that beat the whole resolved YAML ladder; there is no Ph-1/Ph-2 requirement
for scope-level runtime overrides. storage conflated the YAML scope ladder (FR-30 mechanism)
with the Postgres override layer (FR-31 mechanism). `[D-062]`'s "Device override > Customer
override > Global override > YAML base" precedence carries the same conflation and will be
reframed. Architect will switch to architecture phase, correct `storage.AutomationRuleOverride`
to the item-level shape, reframe `[D-062]`, log the canonical D-XXX, and re-open storage
(storage-v1 follow-up or storage-v2 strand).

**rule_engine build consequence (this strand):** do NOT import `storage.AutomationRuleOverride`.
rule_engine defines and owns an injected `OverrideStore` Protocol returning rule_engine's own
item-level override type (Worked-Example-3 shape: `delivery_item_id`, `rule_id`,
`override_payload`/tiers, `created_by_pm_id`, `source_tier`). Tests mock the Protocol; the
reconciled storage implements it later. `loader.py`'s override load + `resolver.py` step 4 sit
behind this seam; everything else in the module is conflict-free and built directly.

**Why:** Building against the landed-but-wrong shape would bake the drift into a second module;
the requirement text is unambiguous ("per-item override … persisted as item-level override
records … take precedence over all three YAML tiers for that item").

**Consequences:** storage Public-surface change (hard-flag, architect-owned); `[D-062]` reframe;
orphan-audit key changes from rule_id-only to the item-level shape (coupled, handled in the same
architect pass); rule_engine MODULE.md loader/resolver text referencing "storage" for override
load to be updated to the OverrideStore seam when the integration session lands.

---

## D-DRAFT-2 — Pause-state physical home is TBD (SP DeliveryItem column vs event-carried); not storage; PauseStateLookup Protocol stays injected

**Date:** 2026-06-12
**Status:** draft — architect-ruled 2026-06-12 (Protocol confirmed buildable now; home-of-pause decision deferred, captured by architect alongside D-DRAFT-1)

**Context:** `rule_engine/MODULE.md` documents `PauseStateLookup` as "backed by storage", but
the landed storage module has zero pause surface (no API, no table). FR-31 sub-1
(requirements.md:218) places the pause toggle "on each DeliveryItem row" with pause/resume
events "recorded in CommunicationLog".

**Decision (architect):** The live pause flag is a DeliveryItem attribute whose authoritative
home is SP (toggled in SP UI, arriving over the `[D-047]` SP-alert channel) — read either via
`sharepoint_integration` or carried on the `TriggerEvent`/`EntityRef` by the workflow_engine
task body that already holds the item. CommunicationLog (storage) holds only the audit events,
not the live flag. Given rule_engine's pure-evaluator + <50 ms + no-IO-per-eval invariants,
**event-carried is the leaning default**, but the home decision is deliberately deferred — to
be settled (architect) rather than assumed. Growing storage a pause table is ruled out as
contradicting the requirement.

**rule_engine build consequence (this strand):** `pause_state.py` implements the
`PauseStateLookup` Protocol as specced (inject + mock); no storage wiring. MODULE.md's
"backed by storage" wording softened to "backed by an injected provider — physical home TBD"
(soft-flag edit 2026-06-12).

**Consequences:** when the home decision lands, the production `PauseStateLookup` implementation
lives in the owning module (sharepoint_integration or workflow_engine event enrichment);
rule_engine is unaffected beyond the injected impl.

---

## D-DRAFT-3 — Rule-YAML schema ownership: template_schema.AutomationRuleBase is pre-[D-066] drift; loader validates against rule_engine's own Rule model

**Date:** 2026-06-12
**Status:** draft — architect input requested both ways; rule-engine-v1 input: **option (ii)**

**Context:** `template_schema.AutomationRuleBase` still carries `priority: int = 100` and a
single `action_type` (models.py:295,297) — the pre-`[D-066]` shape. `[D-066]` (2026-06-10)
locked per-trigger **ordered action lists** with **no priority / no first-match / no score**.
rule_engine's declared loader contract ("validate rule YAML against
template_schema.AutomationRuleBase") is therefore unsatisfiable — that model cannot validate
ordered-actions YAML, and its `priority` field contradicts a locked decision.

**Options:** (i) update `AutomationRuleBase` to match `[D-066]` (drop priority, action_type →
ordered actions), keeping template_schema as canonical rule-schema source per `[D-046]`;
(ii) move rule-YAML schema ownership entirely into rule_engine (the rich `Rule`/`RuleAction`
model with `kind` discriminator + polling tiers IS the schema); drop the rule_engine →
template_schema dependency for rule validation, keeping it only for entity enums
(DeliveryState etc.).

**rule-engine-v1 input:** **(ii)**, agreeing with the architect's lean. The rule grammar is
rule_engine-specific (kind discriminator, polling tiers, ordered action lists, declarative
condition DSL) and has no second consumer; template_schema should own cross-tier *entity*
contracts, not one module's YAML grammar. Keeping a parallel AutomationRuleBase invites exactly
this class of drift again. One interplay to settle in the same pass: FR-28/FR-29
registry-based extensibility (`RuleTriggerRegistry`/`RuleActionRegistry`, customer-extensible
without code change) vs rule_engine's closed Ph-1 `TriggerKind`/`ActionKind` enums (RUL-E003/
E004 validate against the enums per MODULE.md). If registries remain the extensibility
mechanism, rule_engine's load-time validation should consult registry-seeded sets rather than
the closed enums — decide ownership of that seam together with (i)/(ii).

**Interim (this strand, today):** loader validates against rule_engine's own `Rule` model;
no `AutomationRuleBase` import. Anchor hygiene applied as soft-flag: MODULE.md's `[D-031]`
citations for "config-as-code Postgres overrides" re-anchored to `[D-062]` (`[D-031]` is
actually the Template Schema Ingestor Ph-2 deferral, DECISIONS.md:434).

**Consequences:** if (ii) ratified — template_schema deprecates/removes `AutomationRuleBase`
(its own soft/hard-flag pass, architect-owned since template_schema is landed-and-shared);
`[D-046]` canonical-schema-source scope note (rule grammar exempted); rule_engine MODULE.md
Depends-on entry for template_schema narrows to enums/registries.
