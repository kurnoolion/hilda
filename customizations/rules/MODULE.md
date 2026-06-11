# Module: customizations/rules

> **Status:** Initial draft 2026-06-10 (Ph-1-first scope per discipline locked 2026-06-09). **Data drop-zone — no Python code under this directory.** YAML files consumed by `core/src/rule_engine.RuleSet.load` per `[D-030]` / `[D-031]`. Aligned with `rule_engine` MODULE.md Public surface (`Rule` model + `RuleKind` discriminator covering trigger-action rules + polling-schedule rules).
>
> **Rollback log:**
> - **2026-06-10 (initial draft)** — first MODULE.md for `customizations/rules/`. Same drop-zone discipline as `customizations/sharepoint_config/` (2026-06-10). Anchors `[D-001]` (three-tier layout — customizations belong here, not in core), `[D-022]` (rule_engine pure-evaluator boundary — this YAML is its data input), `[D-025]` (Docker Compose bind-mount Ph-1/Ph-2; K8s ConfigMap Ph-3+), `[D-030]` (config-as-code rules), `[D-031]` (config-as-code Postgres overrides via FR-31 — the RUNTIME counterpart; not in this YAML but related), `[D-062]` (`AutomationRuleOverride` soft-FK to YAML rule_id; orphan-audit owned by `rule_engine` per `[D-062]`), `[D-066]` (per-trigger ordered action lists; no priority/score/first-match — this YAML structure reflects that). FR-30 (Global / Customer / Device scope ladder + YAML layout), FR-23 + FR-55 (`polling_schedule` deadline-tiered tier shape).

**Purpose**: **Per-deployment drop-zone** for HILDA AutomationRules + polling-schedule rules that `core/src/rule_engine.RuleSet.load` consumes at startup (and on SIGHUP `reload()`) per FR-30. One YAML file per scope tier: `global/defaults.yaml` (Global tier — applies to all customers/devices unless overridden); `<customer_slug>/customer_rules.yaml` (Customer tier); `<customer_slug>/<device_slug>/device_rules.yaml` (Device tier — optional, created only when needed). Each YAML file may carry two top-level keys: `rules:` (TRIGGER_ACTION shape — trigger + ordered actions list per FR-28 / FR-29) and `polling_schedules:` (POLLING_SCHEDULE shape — tiered breakpoints per FR-23 / FR-55). **Data-only drop-zone — no Python code under this directory** (the `__init__.py` is present for Python package discoverability per structure-conventions but contains no logic; the directory is functionally a YAML data store). Anchors `[D-001]`, `[D-022]`, `[D-025]`, `[D-030]`, `[D-031]`, `[D-062]`, `[D-066]`, and serves FR-30 (scope ladder + YAML layout), FR-23 / FR-55 (polling_schedule tier shape), FR-28 (13 trigger kinds populate `trigger:` field), FR-29 (18 Ph-1 action kinds populate `actions[].kind` field).

**Workload assignment**: No workload. Files are bind-mounted into `hilda-worker` + `hilda-beat` containers per `[D-025]` Docker-Compose bind-mount pattern (Ph-1/Ph-2) — same mechanism as `customizations/sharepoint_config/` and `customizations/template_schemas/`. Reloaded by `rule_engine.RuleSet.reload()` on SIGHUP. No HILDA pod runs here.

---

## Sub-modules

```
customizations/rules/
  __init__.py                          ← empty; present for Python package discoverability only (no logic)
  global/
    defaults.yaml                      ← Global tier rules (Ph-1; applies to all customers/devices unless overridden)
  <customer_slug>/
    customer_rules.yaml                ← Customer tier overrides + additive customer-specific rules (Ph-1)
    <device_slug>/
      device_rules.yaml                ← Device tier overrides (OPTIONAL — created only when needed; Ph-1)
  MODULE.md                            ← this file
```

---

## Public surface

*(No Python surface. The "Public surface" of this module is the YAML file schema validated by `rule_engine.RuleSet.load` against the `Rule` Pydantic model in `core/src/rule_engine/models.py`.)*

### Per-file YAML schema — two top-level keys

```yaml
# Optional — one or the other or both may be present per file
rules:                                    # TRIGGER_ACTION rules (event-fired)
  - rule_id: <unique-id-within-scope>
    trigger: <TriggerKind value>          # one of FR-28's 15 Ph-1 triggers
    sub_trigger: <str | null>             # required when trigger == ItemModified; else null
    condition: <declarative-expr | null>  # closed operator DSL; null = unconditional
    actions:                              # ordered list — intra-rule order = execution order
      - kind: <ActionKind value>          # one of FR-29's 18 Ph-1 actions
        params: <dict>                    # action-instance-specific params (channel, recipient, template, target_state, etc.)
      # additional actions in declared order...

polling_schedules:                        # POLLING_SCHEDULE rules (on-demand; consumed by workflow_engine polling scheduler)
  - rule_id: <unique-id-within-scope>
    tiers:                                # deadline-tiered breakpoints per FR-23 / FR-55
      - days_before_deadline: null        # baseline tier (REQUIRED — always-applies fallback)
        interval_minutes: <int>
      - days_before_deadline: <int>       # additional tiers (evaluated in ascending order)
        interval_minutes: <int>
```

### Trigger-action rule schema (full)

```yaml
rules:
  - rule_id: send_reminder_on_no_contact            # globally unique within {scope, scope_keys, RuleKind}
    trigger: LastContactThreshold                   # one of: ItemCreated, ItemModified, StateChange, OwnerStatusConfirmed,
                                                    #         LastContactThreshold, DeadlineProximity, AttachmentReceived,
                                                    #         AIReviewResult, PMApproval, TrackerCreated, MilestoneAllClosed,
                                                    #         CollectionPhaseClosureReached, CredentialExpired
    sub_trigger: null                               # ItemModified sub-triggers: OwnerReassigned / DeadlineMoved / TagsModified
    condition:                                      # declarative DSL — closed operator set
      field: reminder_count_unanswered              # field name (from DeliveryItem snapshot or derived context)
      op: lt                                        # one of: eq, neq, in, gt, gte, lt, lte, and, or
      value: 3                                      # literal value
    actions:                                        # ordered list (execution = declared order; YAML position 0, 1, 2, ...)
      - kind: SendReminder                          # one of FR-29's 18 Ph-1 actions
        params:
          template: standard_owner_reminder
          channel: email
      # additional actions in declared order — Celery chain executes sequentially
```

### Condition DSL (closed operator set)

```yaml
# Leaf condition (single predicate)
condition:
  field: <field_name>
  op: <eq | neq | in | gt | gte | lt | lte>
  value: <literal>

# Composite condition (boolean logic)
condition:
  and:                                              # OR `or:`
    - {field: doc_count_reached, op: eq, value: true}
    - {field: review_required,   op: eq, value: true}

# Null condition = unconditional firing (any trigger event matches)
condition: null
```

**No Jinja, no embedded Python, no `eval()`, no jq filters** — per `rule_engine` MODULE.md Key choices (anchored to NFR-2 / `[D-002]` no-proprietary-content invariant). Adding a new operator is a `core/src/rule_engine/` code change.

### Polling-schedule rule schema (full)

```yaml
polling_schedules:
  - rule_id: default_polling_schedule               # unique within scope+kind bucket
    tiers:                                          # ordered ascending by days_before_deadline
      - days_before_deadline: null                  # baseline tier (REQUIRED — always-applies fallback)
        interval_minutes: 60
      - days_before_deadline: 3                     # ≤ 3 days from deadline → tighter interval
        interval_minutes: 15
      - days_before_deadline: 1                     # ≤ 1 day from deadline → tightest
        interval_minutes: 5
```

### Scope ladder per FR-30

```
customizations/rules/
  global/
    defaults.yaml                ← Global tier — every customer / device gets these unless overridden
  carrier-alpha/                 ← Customer tier — one dir per customer_slug
    customer_rules.yaml          ← Customer-tier overrides per rule_id + additive customer-specific rules
    smartphone-X/                ← Device tier (OPTIONAL) — one dir per device_slug
      device_rules.yaml          ← Device-tier overrides per rule_id + additive device-specific rules
  carrier-beta/
    customer_rules.yaml
```

**Resolution per `[D-066]`**: per `rule_id`, most-specific tier wins (Device → Customer → Global). FR-31 Postgres overrides take precedence over all three YAML tiers for the specific item. Rules with distinct `rule_id`s coexist additively — multiple rules can match the same trigger and fire concurrently as independent Celery chains.

### Example — full multi-tier composition (`carrier-alpha` customer + LastContactThreshold trigger)

#### Global tier

```yaml
# customizations/rules/global/defaults.yaml
rules:
  - rule_id: send_reminder_on_no_contact
    trigger: LastContactThreshold
    condition: {field: reminder_count_unanswered, op: lt, value: 3}
    actions:
      - kind: SendReminder
        params: {template: standard_owner_reminder, channel: email}

  - rule_id: escalate_after_3_misses
    trigger: LastContactThreshold
    condition: {field: reminder_count_unanswered, op: gte, value: 3}
    actions:
      - kind: Escalate
        params: {channel: corp_messenger, escalation_template: tg_lead_escalation}
      - kind: NotifyPM
        params: {urgency: medium}

polling_schedules:
  - rule_id: default_polling_schedule
    tiers:
      - {days_before_deadline: null, interval_minutes: 60}
      - {days_before_deadline: 3,    interval_minutes: 15}
      - {days_before_deadline: 1,    interval_minutes: 5}
```

#### Customer tier — overrides Global send_reminder + adds new customer-specific rule

```yaml
# customizations/rules/carrier-alpha/customer_rules.yaml
rules:
  - rule_id: send_reminder_on_no_contact            # same rule_id as Global → OVERRIDES
    trigger: LastContactThreshold
    condition: {field: reminder_count_unanswered, op: lt, value: 3}
    actions:
      - kind: SendReminder
        params: {template: alpha_branded_reminder, channel: email}

  - rule_id: alpha_cc_tg_lead_on_no_contact         # NEW rule_id → ADDITIVE
    trigger: LastContactThreshold
    condition: null                                  # unconditional
    actions:
      - kind: NotifyPM
        params: {recipient: tg_lead, urgency: low}
```

#### Resolution outcome for entity (carrier-alpha, smartphone-X)

For `LastContactThreshold` firing with `reminder_count_unanswered=2`:
- `send_reminder_on_no_contact`: Customer-tier wins → action `SendReminder(alpha_branded_reminder)`
- `escalate_after_3_misses`: Global-tier wins (no Customer override); condition `≥ 3` fails → not matched
- `alpha_cc_tg_lead_on_no_contact`: Customer-tier only; condition null → matched
- Result: **2 independent Celery chains** scheduled per `[D-066]` — concurrent firing, no cross-rule order

### Bind-mount + hot-reload mechanism (`[D-025]`)

```
On host: /etc/hilda/customizations/rules/
       (deploy-time mount target)
                ▼ Docker Compose bind-mount
In container: /etc/hilda/customizations/rules/
                ▼ rule_engine.RuleSet.load(rules_dir=Path("/etc/hilda/customizations/rules"))
              parses all YAML files + Postgres AutomationRuleOverride snapshot
                ▼ orphan_audit + collision_audit fire (RUL-W001/W002 warnings if any)
              In-memory RuleSet ready for evaluate() calls
```

On ops edit:
```
Ops edits YAML file → SIGHUP to hilda-worker + hilda-beat → rule_engine.RuleSet.reload() → swap in new rule set atomically
```

Pre-`reload()` evaluations use the prior rule set (no torn state); post-`reload()` evaluations use the new set.

---

## Invariants

- **Data-only drop-zone — no Python code under this directory** beyond an empty `__init__.py` for package discoverability. Any logic that would belong "near the data" lives in `core/src/rule_engine/` (per `[D-001]` core-vs-customizations split + `[D-022]` rule_engine pure-evaluator boundary). YAML edits never require a code release.
- **Two YAML top-level keys: `rules:` (TRIGGER_ACTION shape) and `polling_schedules:` (POLLING_SCHEDULE shape)** — per `rule_engine.Rule.kind` discriminator. A single file may carry both keys, only one, or neither. Other top-level keys are rejected at `RuleSet.load` (RUL-E002 shape mismatch).
- **Three YAML tiers per FR-30 scope ladder**: `global/defaults.yaml`, `<customer_slug>/customer_rules.yaml`, `<customer_slug>/<device_slug>/device_rules.yaml`. Device tier is OPTIONAL and only created when needed; Customer and Global are required for an active deployment (an empty Global file is acceptable if all rules are Customer-tier).
- **Resolution per FR-30: per `rule_id`, most-specific tier wins** (Device → Customer → Global). FR-31 Postgres overrides take precedence over all 3 tiers for the specific item.
- **Multi-rule per trigger is additive composition per `[D-066]`** — rules with distinct `rule_id`s attached to the same trigger coexist. Each fires as an independent Celery chain when its condition matches.
- **Intra-rule action order = YAML declaration order** per `[D-066]` — `actions:` list position 0 runs first, position 1 second, etc. Celery `chain()` enforces sequential execution.
- **Cross-rule order is NOT guaranteed** per `[D-066]` — workflow_engine schedules each matching RuleMatch's chain independently. No priority field, no first-match, no score, no `depends_on` DAG.
- **Condition DSL is declarative and closed** — only the operator set `{eq, neq, in, gt, gte, lt, lte, and, or}` is allowed. No Jinja, no Python, no jq filters. Anchors NFR-2 (no proprietary content leak via condition expressions).
- **Polling-schedule baseline tier is REQUIRED** — every `polling_schedules` rule must have a tier with `days_before_deadline: null`; RUL-W004 emitted if missing (falls back to `RuleEngineConfig.default_baseline_minutes`).
- **`__all_rules__` sentinel reserved for FR-31 sub-1 pause** — this rule_id is NEVER used in YAML files. It's reserved for the Postgres `AutomationRuleOverride.rule_id` value when a TPM pauses all rules for a delivery item via the SP UI Pause toggle. YAML loader rejects any file declaring `rule_id: __all_rules__` (RUL-E001 collision).
- **Bind-mounted at runtime per `[D-025]`** — directory is bind-mounted into `hilda-worker` + `hilda-beat` containers; ops edits a YAML file, sends SIGHUP, `rule_engine.RuleSet.reload()` picks up the change without a redeploy. No restart required.
- **Orphan-audit lives in `rule_engine` per `[D-062]`** — Postgres `AutomationRuleOverride.rule_id` is a soft-FK to YAML rule_id (no DB constraint since YAML is the source of truth). `rule_engine` runs orphan-audit at `load()` / `reload()`; emits RUL-W002 for Postgres rule_ids not in any YAML tier. This module's YAML defines the canonical set against which Postgres overrides are checked.
- **No credential material, no proprietary content** per NFR-2 / `[D-002]`. The YAML carries rule_ids + condition expressions (bounded operators) + action kinds + action params (channel / recipient slugs / template names — no raw text); never credential blobs, never customer-data values, never owner-reply prose, never document content.

---

## Key choices

- **`[D-001]`** — three-tier layout: `core/` (HILDA Python) + `customizations/` (per-deployment data) + `config/` (operational tuning). This directory is squarely tier 2; no Python logic belongs here.
- **`[D-022]`** — `rule_engine` is a pure evaluator; this directory's YAML is its **declarative input** + the only place new rules can be added without a code release. The boundary is load-bearing.
- **`[D-025]`** — Docker Compose bind-mount Ph-1/Ph-2; K8s ConfigMap Ph-3+ — same YAML schema across phases.
- **`[D-030]`** — config-as-code rules: AutomationRules live in YAML (this directory), not in Postgres or in code. Auditable via git; reproducible deployments via standard Compose/K8s mount semantics.
- **`[D-031]`** — config-as-code FR-31 Postgres overrides (sister concept; the RUNTIME counterpart): TPM-set per-item rule parameter overrides stored in Postgres `AutomationRuleOverride` table, NOT in this YAML. The two layers compose at evaluation time per `rule_engine.resolver`.
- **`[D-062]`** — orphan-audit ownership: `rule_engine` (not `storage`) owns the YAML vs Postgres orphan-audit; this directory provides the canonical YAML set the audit compares against.
- **`[D-066]`** — per-trigger ordered action lists (no priority/score/first-match) + cross-rule independence. This YAML structure (one rule_id per ordered `actions:` list) reflects that.
- **Three explicit YAML tiers (no global YAML-merge layer)** (architect decision — captured as Key choice 2026-06-10) — alternative considered: a single tier with explicit `scope: device|customer|global` field per rule; rejected because (a) directory structure visually maps to scope precedence (ops reading the tree understands the ladder immediately); (b) atomic file edits per tier (ops touches one file when overriding a single rule for a single customer); (c) bind-mount semantics simpler at the directory level. Aligns with `customizations/sharepoint_config/customers/<slug>.yaml` + `customizations/template_schemas/<slug>/schema.yaml` (per-customer-slug directory pattern HILDA uses elsewhere).
- **Declarative condition DSL with closed operator set** (architect decision — anchored to rule_engine MODULE.md Key choice) — rejected alternatives include Jinja templates, embedded Python via `eval()`, and jq-style filters. Trade-off: some complex conditions require multiple chained rules; gain: auditability + NFR-2 protection + ops-team accessibility without learning a separate query language.

---

## Non-goals

- **Not a Python module** — no logic, no imports, no Public API beyond the YAML schema. `__init__.py` is empty by design.
- **Not the FR-31 Postgres override layer** — TPM-set per-item overrides live in Postgres `AutomationRuleOverride` table per `[D-031]` / `[D-062]`. This YAML is the canonical "what rules exist" surface; Postgres carries "what runtime overrides apply per item".
- **Not the rule editor / not the FR-31 control panel backend** — TPM pause/customize/manual-trigger UX lives in SP UI + `sharepoint_integration`. This YAML is ops-edited only; PMs/TPMs don't touch it.
- **Not a YAML semantic validator beyond Pydantic shape** — does this rule make business sense? does this trigger/action pair compose with the FR-7 state machine? — those are the ops team's responsibility + `rule_engine.collision_audit` + `/drift-check design` at architecture phase. Load-time shape validation only.
- **Not the SP-list-mapping YAML** — `customizations/sharepoint_config/` per-customer SP list/column maps live there (sister directory). Separate concerns.
- **Not the customer-template YAML** — `customizations/template_schemas/<customer_slug>/schema.yaml` carries the canonical entity hierarchy + custom fields per `[D-018]`. Separate concerns.
- **Not the per-customer checklist YAML** — `customizations/checklists/<customer_slug>/<doc_type>.yaml` carries the FR-53 LLM review checklist generated by `test_report_profiler`. Separate concerns.
- **Not a YAML-include / template-engine surface** — no `!include` directives, no Jinja-templated YAML, no anchors-and-aliases beyond pure-YAML. Keep files self-contained for predictable diffs + auditable git history.

---

## Depends on

- *(none — this is a data drop-zone with no code dependencies)*

## Depended on by

- `core/src/rule_engine` (primary consumer) — `RuleSet.load` reads `customizations/rules/{global,*}/*.yaml` at startup + on SIGHUP `reload()`; orphan-audit compares Postgres `AutomationRuleOverride.rule_id` values against the YAML rule_id set.
- Indirect via rule_engine: `workflow_engine.TriggerDispatcher.dispatch` consumes `RuleEngine.evaluate(event)` output (resolved from this YAML); every Celery task body that calls `evaluate` indirectly depends on this directory's content.
- **Cross-team consumer**: HILDA ops team (deploy-time + runtime ops). YAML edits are the primary mechanism for per-customer rule customization without code release.

---

## Deferred (Ph-2 / Ph-3+)

- **Ph-2 — Ph-2 ActionKind values in YAML** (not in Ph-1 `ActionKind` enum): `CancelOutstanding`, `NotifyOwnerDocCountPending`, `TriggerVersionSelection`, `TriggerPLMCleanup`, `TriggerODF`, `SendOwnerRoutingQuery`. Rules using these actions are rejected at load time with `RUL-E004` until Ph-2 lands.
- **Ph-2 — Ph-2 trigger kinds in YAML** (not in Ph-1 `TriggerKind` enum): `ItemDeleted`, `UnroutedDocumentAccumulated`. Rules using these triggers are rejected at load time with `RUL-E003` until Ph-2 lands.
- **Ph-2 — Per-rule pause-state extension per FR-31 sub-1** — currently Ph-1 pause is all-rules-or-none per item (sentinel `rule_id: __all_rules__` in Postgres). Ph-2 may add per-rule_id pause; YAML structure unchanged (the pause-state shape lives in Postgres, not in this YAML).
- **Ph-2 — Full rule parameter override UI per FR-31 sub-2** — currently Ph-1 supports only `polling_schedule` breakpoint overrides via SP UI; Ph-2 may add arbitrary `LastContactThreshold` / `DeadlineProximity` parameter overrides. YAML structure unchanged.
- **Ph-3+ — Tooling to generate Customer-tier YAML from Excel customer schema** via `template_schema_ingestor` (`[D-018]` Ph-2 module; rule generation is a Ph-3+ extension once `template_schema_ingestor` is mature).
- **Ph-3+ — K8s ConfigMap migration per `[D-025]`** — YAML schema unchanged; only mounting mechanism shifts from Docker Compose bind-mount to ConfigMap. No file-content change.
- **Ph-3+ — Per-customer rule editor UI** — currently ops-edited only; Ph-3+ could expose a web-based YAML editor for HILDA ops team. Defer until ops scale demands.
- **Ph-3+ — `depends_on: <rule_id>` DAG cross-rule ordering** — currently per `[D-066]` no cross-rule order; Ph-3+ may add an explicit dependency edge if a real HILDA need emerges. YAML schema would extend `rule:` with optional `depends_on: [rule_id]` list.

---

## Test interface

This directory has no executable test surface of its own — the YAML is exercised through `core/src/rule_engine/rule_engine_cli.py`. Three relevant modes:

```
python -m core.src.rule_engine.rule_engine_cli --diagnostic
```
Loads all YAML files under `customizations/rules/` + Postgres `AutomationRuleOverride` snapshot; reports per-tier rule counts, trigger / action distribution, orphan + collision audit results. Validates that this directory's YAML is structurally correct. Emits `RUL-RPT`.

```
python -m core.src.rule_engine.rule_engine_cli --validate
```
Pydantic-validates all YAML files in `customizations/rules/{global,*}/*.yaml`; emits `RUL-E*` codes for shape errors. Safe in CI; returns non-zero exit on `RUL-E*`. Use as pre-deploy check.

```
python -m core.src.rule_engine.rule_engine_cli --explain --trigger <kind> --entity '{"customer_slug":"c1","device_slug":"d1"}'
```
Synthesizes a `TriggerEvent` + runs `evaluate()` against the loaded RuleSet; emits resolution trace (which scope tier won; which rules matched; what actions in what order). For ops debugging "why did rule X fire?". Pure (no side effects).

**No CLI ships under this directory itself.** All validation flows through `rule_engine_cli`.

---

<!-- BEGIN:STRUCTURE -->
<!-- END:STRUCTURE -->
