# Module: rule_engine

> **Status:** Initial draft 2026-06-10 (Ph-1-first scope per discipline locked 2026-06-09). Pure evaluator surface per `[D-022]` — no Celery, no SP write, no HTTP, no DB writes. Sections curated; code implementation begins after `/switch-phase development rule_engine`.
>
> **Rollback log:**
> - **2026-06-10 (initial draft)** — first MODULE.md for `rule_engine`. Scope locked to **Ph-1 only**: 15 Ph-1 triggers + 18 Ph-1 actions (Ph-2 triggers/actions → `## Deferred`). Per-trigger ordered action lists (no priority / no first-match / no score per architect decision 2026-06-10). Cross-rule firing independent (each `RuleMatch` schedules as its own Celery task chain via `workflow_engine`). `AutomationRuleOverride` orphan-audit owned by this module per architect decision 2026-06-10 (`[D-062]` soft-FK). Anchors FR-28, FR-29, FR-30, FR-31, FR-23, FR-55, `[D-022]` (pure evaluator), `[D-030]` (config-as-code rules), `[D-031]` (config-as-code Postgres overrides via FR-31), `[D-062]` (override soft-FK + orphan audit), `[D-025]` (YAML bind-mount), `[D-005]` (test interface), `[D-002]` (compact reports + RUL-* error codes). New error code prefix `RUL` already in `diagnostics.PREFIX_REGISTRY`.

**Purpose**: Pure evaluator for HILDA's IF/THEN AutomationRules per `[D-022]` — given a `TriggerEvent` (one of the 15 Ph-1 triggers per FR-28) plus an `EntityRef` (which Customer/Device/Milestone/DeliveryItem the event is about), returns the ordered set of `RuleMatch` tuples that should fire — each carrying an intra-rule-ordered list of `RuleAction`s (Ph-1 subset of FR-29). Resolves scope precedence per FR-30 (Device → Customer → Global, most-specific wins per `rule_id`) with FR-31 Postgres overrides taking precedence over all YAML tiers for the specific item. **No side effects** — this module never writes to SP, NSD, Postgres, Redis, or any external system; it never enqueues Celery tasks; it never makes network calls. `workflow_engine` consumes the `evaluate()` output per `[D-022]` and schedules each `RuleMatch`'s action chain as an independent Celery task. Serves FR-28, FR-29 (Ph-1 subset), FR-30 (scope ladder + YAML layout), FR-31 (sub-1 pause/resume read-side; sub-2 Postgres override consumption; sub-3 manual triggers bypass this module entirely), FR-23 + FR-55 (`polling_schedule` deadline-tiered evaluator). Anchors `[D-022]` (rule_engine = pure evaluator boundary), `[D-030]` + `[D-031]` (config-as-code rules + Postgres FR-31 overrides), `[D-062]` (override soft-FK to YAML + orphan-audit ownership), `[D-025]` (YAML bind-mount: hot-reload at startup + SIGHUP), `[D-005]` (`--diagnostic` + `--validate` CLI), `[D-002]` (RUL-* error codes + RPT/MET/QC compact reports + no-proprietary-content invariant).

**Workload assignment**: In-process library consumed by `workflow_engine` (`hilda-worker` Celery pool) on every trigger event. **No standalone Deployment** — rule_engine has no `hilda-rule-engine` pod; it lives as a Python package imported by workflow_engine's task functions and by the `--diagnostic` CLI. Per-evaluation cost is small (in-memory rule lookup + Postgres SELECT on `AutomationRuleOverride` for the item — cached per item-id with TTL).

**Per-evaluation latency target**: <50 ms for typical rule sets (Global ~30 rules + Customer ~10 rules + Device ~5 rules + per-item override lookup with Postgres connection pool); evaluation runs synchronously on the Celery worker; no background tasks of its own.

---

## Sub-modules

```
core/src/rule_engine/
  __init__.py
  models.py                         ← Pydantic + dataclass models: Rule, RuleAction, TriggerEvent, RuleMatch, RuleScope, EntityRef, TriggerKind enum, ActionKind enum, PollingScheduleTier
  loader.py                         ← YAML loader: walks customizations/rules/{global,<customer>/...}/ tree; validates Pydantic shape; emits RUL-E* on parse errors; loads Postgres AutomationRuleOverride at startup
  resolver.py                       ← Scope-precedence resolver: per (entity_ref, rule_id), returns the effective rule per FR-30 ladder (Device → Customer → Global) with FR-31 Postgres override layer
  evaluator.py                      ← Pure trigger → list[RuleMatch] evaluator; intra-rule action order preserved (YAML declaration order)
  polling_schedule.py               ← Deadline-tiered breakpoint evaluator per FR-23 / FR-55 (days_to_deadline → interval_minutes); baseline tier required (RUL-W004 if missing)
  orphan_audit.py                   ← Startup audit per [D-062]: emits RUL-W002 for Postgres AutomationRuleOverride.rule_id entries not present in any YAML tier
  collision_audit.py                ← Startup audit: emits RUL-W001 when distinct rule_ids both write UpdateState on the same trigger (config smell)
  pause_state.py                    ← FR-31 sub-1 pause/resume read-side: lookup pause flag on a delivery_item_id (storage-mediated; this module doesn't write)
  diagnostics_cli.py                ← --diagnostic / --validate / --explain modes
  rule_engine_cli.py                ← user-facing wrapper for ops debugging
  tests/
  MODULE.md                         ← this file

customizations/rules/                ← rule YAML files (bind-mounted at runtime per [D-025])
  global/
    defaults.yaml                   ← Global rules (Ph-1)
  <customer_slug>/
    customer_rules.yaml             ← Customer-scoped overrides (Ph-1)
    <device_slug>/
      device_rules.yaml             ← Device-scoped overrides (optional, Ph-1)
```

---

## Public surface

### `models.py`

```python
class TriggerKind(str, Enum):
    """All Ph-1 triggers per FR-28."""
    ITEM_CREATED                          = "ItemCreated"
    ITEM_MODIFIED                         = "ItemModified"   # sub-trigger discriminator carried in TriggerEvent.sub_trigger
    STATE_CHANGE                          = "StateChange"
    OWNER_STATUS_CONFIRMED                = "OwnerStatusConfirmed"
    LAST_CONTACT_THRESHOLD                = "LastContactThreshold"
    DEADLINE_PROXIMITY                    = "DeadlineProximity"
    ATTACHMENT_RECEIVED                   = "AttachmentReceived"
    AI_REVIEW_RESULT                      = "AIReviewResult"
    PM_APPROVAL                           = "PMApproval"
    TRACKER_CREATED                       = "TrackerCreated"
    MILESTONE_ALL_CLOSED                  = "MilestoneAllClosed"
    COLLECTION_PHASE_CLOSURE_REACHED      = "CollectionPhaseClosureReached"
    CREDENTIAL_EXPIRED                    = "CredentialExpired"

# ItemModified sub-triggers (Ph-1) — discriminated by TriggerEvent.sub_trigger string
ITEM_MODIFIED_SUB_TRIGGERS_PH1 = {"OwnerReassigned", "DeadlineMoved", "TagsModified"}

class ActionKind(str, Enum):
    """Ph-1 actions per FR-29. Ph-2 actions are in ## Deferred and NOT in this enum."""
    SEND_REMINDER                         = "SendReminder"
    ESCALATE                              = "Escalate"
    UPDATE_STATE                          = "UpdateState"
    START_ITEM_COLLECTION                 = "StartItemCollection"
    SEND_INITIAL_OUTREACH                 = "SendInitialOutreach"
    NOTIFY_NEW_OWNER                      = "NotifyNewOwner"
    TRIGGER_PARSER                        = "TriggerParser"
    TRIGGER_AI_REVIEW                     = "TriggerAIReview"
    QUEUE_SUBMISSION                      = "QueueSubmission"
    NOTIFY_PM                             = "NotifyPM"
    NOTIFY_HILDA_OPS                      = "NotifyHildaOps"
    INSTANTIATE_DEFAULT_WORK_ITEM         = "InstantiateDefaultWorkItem"
    MILESTONE_STORAGE_CLEANUP             = "MilestoneStorageCleanup"
    HALT_MILESTONE_POLLING                = "HaltMilestonePolling"
    FINAL_SWEEP                           = "FinalSweep"
    REASSIGN_DOCUMENT_TO_WORK_ITEM        = "ReassignDocumentToWorkItem"
    PROPAGATE_TAGS_TO_ACTIVE_TRACKERS     = "PropagateTagsToActiveTrackers"
    REARM_DEADLINE_PROXIMITY              = "RearmDeadlineProximity"  # internal re-arm on DeadlineMoved sub-trigger

class RuleScope(str, Enum):
    GLOBAL   = "global"
    CUSTOMER = "customer"
    DEVICE   = "device"

class RuleKind(str, Enum):
    """Discriminates Rule shape — trigger-action rules fire from TriggerEvents; polling-schedule
    rules are consumed on-demand by workflow_engine's polling scheduler (not evaluator-fired)."""
    TRIGGER_ACTION   = "trigger_action"
    POLLING_SCHEDULE = "polling_schedule"

@dataclass(frozen=True)
class PollingScheduleTier:
    """One breakpoint in a deadline-tiered polling_schedule per FR-23 / FR-55."""
    days_before_deadline: int | None    # None = baseline tier (always-applies fallback)
    interval_minutes:     int

@dataclass(frozen=True)
class EntityRef:
    """Identifies the entity a TriggerEvent or rule applies to. Not all fields required for all triggers."""
    customer_slug:     str
    device_slug:       str | None = None
    milestone_id:      str | None = None
    delivery_item_id:  str | None = None

@dataclass(frozen=True)
class RuleAction:
    """One action within a rule's ordered action list. Action parameters are
    action-instance-specific dicts; the rule_engine does NOT execute the action —
    workflow_engine consumes (kind, params) and dispatches to the right module."""
    kind:    ActionKind
    params:  dict[str, Any]                 # e.g. {"target_state": "ReadyForSubmission"} for UPDATE_STATE
    sequence: int                            # 0-indexed position within the rule's actions list (informational)

@dataclass(frozen=True)
class Rule:
    """A single rule loaded from YAML (or its Postgres-override variant). Two shapes via `kind`
    discriminator: TRIGGER_ACTION rules fire from TriggerEvents (carry trigger/sub_trigger/condition/actions);
    POLLING_SCHEDULE rules are consumed on-demand by workflow_engine's polling scheduler (carry tiers).
    Pydantic validator enforces shape: if kind == TRIGGER_ACTION then trigger non-None + actions non-empty;
    if kind == POLLING_SCHEDULE then tiers non-empty + trigger/sub_trigger/condition/actions are None/empty."""
    rule_id:         str                     # globally unique within {scope, scope_keys, kind} bucket
    kind:            RuleKind                # discriminator — TRIGGER_ACTION or POLLING_SCHEDULE
    scope:           RuleScope
    scope_keys:      dict[str, str]          # {} for global; {"customer_slug": ...} for customer; {"customer_slug": ..., "device_slug": ...} for device
    source:          Literal["yaml", "postgres_override"]
    source_file:     str | None              # YAML file path for "yaml"; None for "postgres_override"
    source_tier:     RuleScope | Literal["postgres_override"]  # for FR-31 sub-2 "overridden from X" UI surfacing
    # Trigger-action fields (populated when kind == TRIGGER_ACTION; None/empty when kind == POLLING_SCHEDULE):
    trigger:         TriggerKind | None      = None
    sub_trigger:     str | None              = None   # required when trigger == ITEM_MODIFIED; else None
    condition:       dict[str, Any] | None   = None   # optional declarative condition (NOT arbitrary code) — see Invariants
    actions:         list[RuleAction]        = field(default_factory=list)  # ordered list; intra-rule order = YAML declaration order
    # Polling-schedule fields (populated when kind == POLLING_SCHEDULE; empty when kind == TRIGGER_ACTION):
    tiers:           list[PollingScheduleTier] = field(default_factory=list)  # deadline-tiered breakpoints per FR-23 / FR-55

@dataclass(frozen=True)
class TriggerEvent:
    """Fired by callers (workflow_engine task bodies, ingest pipelines, etc.) and passed to evaluate()."""
    trigger:        TriggerKind
    sub_trigger:    str | None
    entity_ref:     EntityRef
    field_deltas:   dict[str, tuple[Any, Any]] | None     # for ItemModified: {field_name: (old, new)}
    timestamp:      datetime                              # event timestamp; used for time-window evaluations
    correlation_id: str                                   # threads through to RuleMatch + downstream Celery tasks for tracing

@dataclass(frozen=True)
class RuleMatch:
    """One matched rule + its action list. Multiple RuleMatch instances per TriggerEvent are common
    (different rule_ids matching the same trigger); workflow_engine schedules each as an independent Celery task chain."""
    rule_id:         str
    matched_scope:   RuleScope                           # the winning scope after FR-30 ladder + FR-31 override
    actions:         list[RuleAction]                    # ordered; from rule.actions verbatim
    pause_state:     Literal["active", "paused"]         # FR-31 sub-1; paused matches are returned but flagged — caller decides whether to skip
    override_source: Literal["yaml", "postgres_override"]
    correlation_id:  str                                 # passed through from TriggerEvent
```

### `loader.py`

```python
class RuleSet:
    """Container for the loaded set of rules across all 3 YAML tiers + Postgres overrides."""

    @classmethod
    def load(
        cls,
        rules_dir: Path = Path("/etc/hilda/customizations/rules"),
        storage:   "Storage",                            # storage module instance for AutomationRuleOverride read
    ) -> "RuleSet":
        """Load all YAML files under rules_dir + Postgres AutomationRuleOverride rows.
        Each YAML file may carry two top-level keys: `rules:` (TRIGGER_ACTION shape; trigger +
        actions) and `polling_schedules:` (POLLING_SCHEDULE shape; tiers). Both shapes
        Pydantic-validated against the Rule discriminated model (Rule.kind set per top-level
        key). Runs orphan_audit + collision_audit (collision-audit is TRIGGER_ACTION-only —
        polling_schedule rules can't UpdateState); emits RUL-W001/W002 as warnings (does NOT
        abort). Raises RUL-E001 on duplicate rule_id within same scope+kind bucket; RUL-E002
        on shape mismatch or missing required field; RUL-E003 on unknown trigger; RUL-E004
        on unknown action; RUL-E005 on Postgres connection failure."""

    def reload(self) -> None:
        """Re-runs load(); used on SIGHUP + on FR-31 sub-2 Postgres override change events.
        Idempotent."""

    def rules_for_scope(
        self, scope: RuleScope, scope_keys: dict[str, str]
    ) -> list[Rule]:
        """Returns the full set of rules at the given (scope, scope_keys) — pre-resolution."""

    def all_rule_ids(self) -> set[str]:
        """Returns the union of all rule_ids across all tiers + Postgres overrides."""
```

### `resolver.py`

```python
def resolve_rules_for_entity(
    rule_set:    RuleSet,
    entity_ref:  EntityRef,
    trigger:     TriggerKind,
    sub_trigger: str | None = None,
) -> list[Rule]:
    """Returns the effective TRIGGER_ACTION rule set for the entity at the trigger per FR-30:
    1. Collect Global rules (kind=TRIGGER_ACTION, matching trigger + sub_trigger).
    2. Overlay Customer rules (per rule_id, customer-tier replaces global).
    3. Overlay Device rules (per rule_id, device-tier replaces customer/global).
    4. Overlay FR-31 Postgres overrides (per rule_id × delivery_item_id, postgres replaces everything).
    Returns the resolved rules. Each rule.source / source_tier is set per the winning tier.
    POLLING_SCHEDULE rules are NOT returned by this function — use resolve_polling_schedule_for_item."""

def resolve_polling_schedule_for_item(
    rule_set:    RuleSet,
    entity_ref:  EntityRef,
    rule_id:     str = "default_polling_schedule",
) -> Rule:
    """Returns the resolved POLLING_SCHEDULE rule for the item per the same FR-30 ladder +
    FR-31 Postgres override. Called by workflow_engine when rescheduling per-item polling tasks
    (FR-26 PLM polling, FR-55 NSD polling). Distinct from resolve_rules_for_entity (which only
    handles TRIGGER_ACTION rules) because polling_schedule rules are consumed on-demand, not
    event-fired. Raises RUL-E002 if no rule with the given rule_id exists at any tier."""
```

### `evaluator.py`

```python
class RuleEngine:
    """The Public Protocol surface — workflow_engine and ingest pipelines consume this."""

    def __init__(self, rule_set: RuleSet, pause_lookup: "PauseStateLookup") -> None: ...

    def evaluate(self, event: TriggerEvent) -> list[RuleMatch]:
        """Pure function. Returns ALL matching rules for the trigger, with intra-rule action order
        preserved. Across-RuleMatch ordering is NOT guaranteed — workflow_engine treats each
        RuleMatch as an independent Celery task chain. If a matched rule's delivery_item_id is
        paused per FR-31 sub-1, the RuleMatch is returned with pause_state="paused" — caller
        decides whether to skip the chain (default: skip)."""

    def explain(self, event: TriggerEvent) -> list[dict[str, Any]]:
        """Returns the same matches as evaluate() PLUS the resolution trace for each match:
        which scope tier won, which rules were overridden by which tier, any FR-31 override
        applied. Used by --explain CLI mode for ops debugging. Pure."""
```

### `polling_schedule.py`

*(`PollingScheduleTier` model lives in `models.py` alongside `Rule` so the discriminator can reference it.)*

```python
def evaluate_polling_schedule(
    tiers:             list[PollingScheduleTier],
    days_to_deadline:  int,
) -> int:
    """Returns the active interval_minutes for the given days_to_deadline.
    Tiers are evaluated in ascending order of days_before_deadline; baseline tier
    (days_before_deadline=None) must be present (RUL-W004 if missing — falls back to
    PollingScheduleConfig.default_baseline_minutes). For days_to_deadline ≤ first non-baseline
    tier's days_before_deadline, that tier's interval applies; example with tiers
    [{60min baseline}, {3 days, 15min}, {1 day, 5min}]: days_to_deadline=4 → 60min;
    days_to_deadline=2 → 15min; days_to_deadline=0 → 5min. When expected_completion_date has
    been overridden per-item by TPM (FR-14), that override date drives tier evaluation —
    caller passes the override-resolved days_to_deadline."""
```

### `orphan_audit.py` + `collision_audit.py`

```python
def orphan_audit_postgres_overrides(
    yaml_rule_ids:        set[str],
    postgres_override_ids: set[str],
) -> list[OrphanFinding]:
    """Per [D-062]: AutomationRuleOverride.rule_id is a soft-FK to YAML rule_id (no DB FK constraint
    because YAML is the source of truth). Emit RUL-W002 for each Postgres override whose rule_id
    is not present in any YAML tier (likely stale — rule was removed from YAML but Postgres
    override survives). Caller logs the warnings; rule_engine does NOT delete orphans (ops decision)."""

def collision_audit_update_state(
    rules_by_trigger: dict[TriggerKind, list[Rule]],
) -> list[CollisionFinding]:
    """At startup, for each trigger, find pairs of distinct rule_ids both producing an UPDATE_STATE
    action targeting delivery_state. Likely a configuration smell (multiple rules competing to
    set state). Emit RUL-W001 per collision pair; ops resolves YAML."""
```

### `pause_state.py`

```python
class PauseStateLookup(Protocol):
    """Injected dependency; backed by storage. rule_engine NEVER writes pause state — that's FR-31's
    SP UI write path through sharepoint_integration."""

    def is_item_paused(self, delivery_item_id: str) -> bool: ...
    def is_milestone_paused(self, milestone_id: str) -> bool: ...   # FR-31 sub-1 Pause All
```

### Configuration

```python
class RuleEngineConfig(BaseModel):
    """3-tier config per [D-025] + [D-038] (CLI > env > config/rule_engine.json)."""
    rules_dir:                       Path = Path("/etc/hilda/customizations/rules")
    postgres_override_cache_ttl_s:   int  = 60               # per-item cache TTL
    default_baseline_minutes:        int  = 60               # used when polling_schedule.baseline tier missing (RUL-W004)
    collision_audit_on_load:         bool = True             # emit RUL-W001 at load; ops may disable in test environments
    orphan_audit_on_load:            bool = True             # emit RUL-W002 at load
```

---

## Invariants

- **Rule shape discriminator** (`Rule.kind`) — every Rule is either `TRIGGER_ACTION` (event-fired; carries trigger + actions; consumed by `evaluate()`) or `POLLING_SCHEDULE` (on-demand; carries `tiers`; consumed by `workflow_engine`'s polling scheduler via `evaluate_polling_schedule`). Pydantic-validated shape conformance at load (RUL-E002 on shape mismatch). The two shapes share `rule_id`, `scope`, `scope_keys`, `source*` fields so FR-30 scope ladder + FR-31 Postgres override layer + orphan-audit apply uniformly to both. `evaluate()` returns NO `POLLING_SCHEDULE` rules (they're never event-fired); the polling scheduler calls a separate `resolve_polling_schedule_for_item(item_id)` method.
- **Pure evaluator per `[D-022]`** — `rule_engine` makes no network calls, writes no DB rows, enqueues no Celery tasks, sends no emails or messages. Side-effect-free given a fixed rule set + Postgres override snapshot. The only IO is rule_set load (YAML read + Postgres `AutomationRuleOverride` SELECT) at `load()` / `reload()` time, which is bounded to startup + SIGHUP + on-demand reload (not per-evaluate).
- **Intra-rule action order = YAML declaration order** — actions in a rule's `actions:` list execute sequentially in the order written. Workflow_engine consumes `RuleMatch.actions` verbatim. This is the **only ordering guarantee**.
- **Cross-rule firing is independent** — when multiple rules match the same trigger, each `RuleMatch` is an independent Celery task chain. No cross-rule ordering is guaranteed (no priority, no first-match-wins, no score). If two rules at the same trigger need a specific cross-rule order, the design smell is "they should be one rule" — ops merges them.
- **Scope precedence per FR-30** — Device overrides Customer overrides Global, **per `rule_id`** (most-specific scope's version of that rule wins). FR-31 Postgres overrides take precedence over all three YAML tiers for the specific item.
- **`UpdateState` collisions surface as `RUL-W001`** at load time — when two distinct rule_ids both write `UPDATE_STATE` on the same trigger, that's a configuration smell (likely an ops error). rule_engine does NOT block load; emits warning for ops triage.
- **Postgres override orphan-audit owned here per `[D-062]`** — at `load()`, this module compares Postgres `AutomationRuleOverride.rule_id` values against YAML rule_ids; emits `RUL-W002` for any Postgres rule_id absent in YAML. ops decides whether to delete the orphan or restore the YAML rule.
- **Condition expressions are declarative, NOT arbitrary code** — `Rule.condition` is a Pydantic-validated dict shape (e.g., `{"field": "doc_type", "op": "eq", "value": "test_report"}`) restricted to a small set of operators (`eq`, `neq`, `in`, `gt`, `gte`, `lt`, `lte`, `and`, `or`). No `eval()`, no Python lambdas, no Jinja, no jq-style filters. Adding a new operator is a `core/` code change. Anchors NFR-2 (no proprietary content leak via condition expressions).
- **FR-31 manual triggers bypass this module entirely** — sub-3 "Trigger Action" dropdown enqueues a Celery task directly via `workflow_engine`; rule_engine is NOT consulted. The decision-source for the action's params is the SP UI; the rule_engine evaluation pipeline plays no role.
- **No proprietary content in compact reports** per NFR-2 / `[D-002]`. `RUL-RPT` / `-MET` / `-QC` emit rule_ids, scope tiers, trigger kinds, action kinds, latency buckets, match counts, override source — never customer-data values, never the contents of `Rule.condition.value` fields when those might carry customer-specific tokens.
- **YAML hot-reload via `[D-025]` bind-mount + SIGHUP** — `customizations/rules/` is bind-mounted into the container; ops edits a YAML file and sends SIGHUP to the worker pool; `reload()` re-runs `load()` without redeploy. Pre-`reload()` evaluations use the prior rule set (no torn-state risk).
- **`PauseStateLookup` injected, never imported as a global** — testable in isolation; mock pause state at unit-test time.

---

## Error codes (RUL prefix — registered in `diagnostics/error_codes.py`)

```
RUL-E001  Duplicate rule_id '{rule_id}' in scope '{scope}' (scope_keys '{keys}') — fatal at load
RUL-E002  Required field '{field}' missing in rule '{rule_id}' (file '{path}') — fatal at load
RUL-E003  Unknown trigger '{trigger}' in rule '{rule_id}' (file '{path}') — fatal at load; allowed triggers in TriggerKind enum
RUL-E004  Unknown action '{action}' in rule '{rule_id}' (file '{path}') — fatal at load; allowed actions in ActionKind enum (Ph-1)
RUL-E005  Postgres connection failure on AutomationRuleOverride load: {reason}
RUL-W001  UpdateState collision: rules '{r1}' and '{r2}' both write delivery_state on trigger '{trigger}' (scope_keys '{keys}') — ops triage
RUL-W002  Orphan Postgres override: rule_id '{rule_id}' (item '{delivery_item_id}') not present in any YAML tier — likely stale per [D-062]
RUL-W003  Rule '{rule_id}' paused for item '{delivery_item_id}' per FR-31 sub-1 — match returned with pause_state=paused; caller decides whether to skip
RUL-W004  polling_schedule baseline tier missing in rule '{rule_id}' — falling back to default {minutes}min per RuleEngineConfig.default_baseline_minutes
RUL-W005  Condition expression operator '{op}' unsupported in rule '{rule_id}' — rule skipped (evaluator returns no match); ops triage
```

---

## Key choices

- **`[D-022]`** — `rule_engine` is a **pure evaluator**; Celery dispatch lives in `workflow_engine`. This boundary is the load-bearing decision; reversing it would re-fold dispatch into rule_engine and make this module stateful (Celery client, retry policy, task queue selection). Keeping the boundary lets workflow_engine pick task queues, retry strategies, and time-budgets per action kind without rule_engine knowing.
- **Per-trigger ordered action lists, no priority / no first-match / no score** (architect decision 2026-06-10 — captured here, no separate ADR per Q4 decision triage 2026-06-10). Intra-rule order = YAML declaration order; cross-rule independence (each `RuleMatch` is its own Celery chain). Rationale: priority / score systems make rule files harder to read and harder to reason about; explicit declaration order in YAML is the single source of truth. Cross-rule independence avoids "rule A blocks rule B" surprises.
- **`[D-030]` + `[D-031]`** — rules are config-as-code (YAML); FR-31 Postgres overrides are config-as-code at runtime (item-level). Both layers are bind-mounted / loaded without code release per `[D-025]`. Adding a new customer / device / rule = YAML edit + SIGHUP; no Python change.
- **`[D-062]` orphan-audit ownership** — `AutomationRuleOverride.rule_id` is a soft-FK to YAML (no DB constraint since YAML is the source of truth). Per architect decision 2026-06-10, `rule_engine.orphan_audit` runs at startup + on `reload()`; emits `RUL-W002`; does NOT delete orphans (ops decision). Alternative considered: orphan-audit in `storage` module — rejected because storage doesn't know YAML state; rule_engine is the only module that sees both sides.
- **Declarative condition DSL (small fixed operator set), NOT Turing-complete** — condition expressions are a Pydantic-validated dict shape with a closed operator set (`eq`, `neq`, `in`, `gt`, `gte`, `lt`, `lte`, `and`, `or`). Rejected alternatives: (α) Jinja templates (`{{ item.doc_type == "test_report" }}`) — opens code-injection surface, leaks customer-data through error messages; (β) embedded Python (`condition: "item.doc_type == 'test_report'"` evaluated via `eval()`) — same risk + harder ops review; (γ) jq-style filters — ops-team learning curve for an external tool. The closed-DSL choice trades flexibility (some complex conditions require multiple chained rules) for auditability + NFR-2 + ops-team accessibility.
- **`workflow_engine` schedules; rule_engine returns** — `RuleMatch.actions` is consumed verbatim by workflow_engine, which has its own logic for action-kind → Celery task mapping (e.g., `SEND_REMINDER` → `email_service.send_owner_reminder` Celery task; `UPDATE_STATE` → `tracker.update_delivery_state` Celery task). The action-kind → task mapping is **workflow_engine's responsibility**, not rule_engine's; rule_engine just produces the (kind, params) tuples.
- **`PauseStateLookup` Protocol-injected** — FR-31 sub-1 pause state lives in Postgres (per FR-31 — `CommunicationLog` records pause/resume; pause flag is item-level). rule_engine reads via injected Protocol so unit tests can mock pause state without standing up Postgres. Same pattern as `credential_service` injection in `customer_adapter`.
- **`polling_schedule` is one rule among many** — not a special-cased evaluator stream. polling_schedule rules are normal AutomationRules whose `trigger` is the scheduler-tick + whose evaluation calls `evaluate_polling_schedule(tiers, days_to_deadline)` to pick the active interval. workflow_engine consumes the interval to reschedule the per-item polling task.

---

## Worked examples (Ph-1 use cases)

Three concrete walk-throughs grounding the abstract Public surface. Each shows YAML rule shape + what `resolve_rules_for_entity()` returns + what `evaluate()` returns + what the TPM can do via FR-31 SP UI (sub-1 pause/resume, sub-2 polling_schedule override, sub-3 manual trigger).

### Example 1 — `OwnerReassigned`: single rule, two ordered actions

**Use case**: Owner of `delivery_item_id=I-1234` (customer `carrier-alpha`, device `smartphone-X`, milestone `M-1001`) is reassigned from `old.owner@example.com` to `new.owner@example.com`. HILDA must (a) notify the new owner, then (b) start collection for the new (owner × milestone) pair if collection is active.

**YAML (Global only — no customer or device override)**:
```yaml
# customizations/rules/global/defaults.yaml
rules:
  - rule_id: handle_owner_reassignment
    trigger: ItemModified
    sub_trigger: OwnerReassigned
    condition: null                              # null = unconditional once sub_trigger matches
    actions:
      - kind: NotifyNewOwner                     # action 0 — runs first
        params:
          template: owner_reassignment_notice
          channel: email
      - kind: StartItemCollection                # action 1 — runs second
        params: {}
```

**TriggerEvent** (constructed by the SP-alert-driven `workflow_engine` task body when the SP `Owner` field changes):
```python
TriggerEvent(
    trigger        = TriggerKind.ITEM_MODIFIED,
    sub_trigger    = "OwnerReassigned",
    entity_ref     = EntityRef(customer_slug="carrier-alpha", device_slug="smartphone-X",
                               milestone_id="M-1001", delivery_item_id="I-1234"),
    field_deltas   = {"owner_email": ("old.owner@example.com", "new.owner@example.com")},
    timestamp      = datetime(2026, 6, 10, 14, 30),
    correlation_id = "evt-abc123",
)
```

**`resolve_rules_for_entity()`** walks the FR-30 ladder for trigger=ItemModified + sub_trigger=OwnerReassigned:
1. Global tier matches: `[handle_owner_reassignment]`
2. Customer tier (carrier-alpha): no override → no change
3. Device tier (smartphone-X): no override → no change
4. Postgres tier `(item=I-1234, rule_id=handle_owner_reassignment)`: no row → no override

Returns a 1-element list: `[Rule(rule_id="handle_owner_reassignment", scope=GLOBAL, source_tier=GLOBAL, actions=[NotifyNewOwner@seq=0, StartItemCollection@seq=1])]`.

**`evaluate()`** then applies condition + pause check:
- `condition is None` → trivially matches
- `PauseStateLookup.is_item_paused("I-1234")` → False (not paused)
- Wraps as `RuleMatch(rule_id="handle_owner_reassignment", actions=[...preserved order...], pause_state="active")`

Returns `[RuleMatch]` (1 element). `workflow_engine` consumes and builds **one Celery chain** running the two tasks sequentially.

**TPM SP UI options** (per FR-31):
- **sub-1 Pause/resume**: TPM pauses item `I-1234` (e.g., reassignment is a temporary swap; don't want collection auto-restarted) → next `OwnerReassigned` event still produces the `RuleMatch` but with `pause_state="paused"` → workflow_engine skips the chain → resume restores normal behavior.
- **sub-2 polling_schedule override**: not directly applicable to this trigger (polling cadence affects FR-26/FR-55 detection, separate concern from owner notification).
- **sub-3 Manual trigger**: TPM can pick `NotifyNewOwner` from the Trigger Action dropdown standalone (e.g., owner was informed verbally; TPM wants the audit-trail email sent without re-firing StartItemCollection). Or pick `StartItemCollection` standalone (rare — collection failed to auto-start due to a transient error; manual retry).

---

### Example 2 — `LastContactThreshold`: multi-rule additive composition with scope-tier overrides

**Use case**: Owner hasn't responded to outreach. Global ships a 2-tier rule set (reminder under 3 unanswered; escalate at 3+). Customer `carrier-alpha` (a) wants a branded reminder template (override) and (b) wants the TG lead CC'd on every reminder (additive new rule).

**YAML — Global tier**:
```yaml
# customizations/rules/global/defaults.yaml
rules:
  - rule_id: send_reminder_on_no_contact
    trigger: LastContactThreshold
    condition:
      field: reminder_count_unanswered
      op: lt
      value: 3
    actions:
      - kind: SendReminder
        params: { template: standard_owner_reminder, channel: email }

  - rule_id: escalate_after_3_misses
    trigger: LastContactThreshold
    condition:
      field: reminder_count_unanswered
      op: gte
      value: 3
    actions:
      - kind: Escalate
        params: { channel: corp_messenger, escalation_template: tg_lead_escalation }
      - kind: NotifyPM
        params: { urgency: medium }
```

**YAML — Customer (carrier-alpha) tier**:
```yaml
# customizations/rules/carrier-alpha/customer_rules.yaml
rules:
  - rule_id: send_reminder_on_no_contact           # same rule_id as Global → OVERRIDES
    trigger: LastContactThreshold
    condition:
      field: reminder_count_unanswered
      op: lt
      value: 3
    actions:
      - kind: SendReminder
        params: { template: alpha_branded_reminder, channel: email }   # customer-specific template

  - rule_id: alpha_cc_tg_lead                       # NEW rule_id → ADDITIVE (no Global counterpart)
    trigger: LastContactThreshold
    condition: null                                  # unconditional on this trigger
    actions:
      - kind: NotifyPM
        params: { recipient: tg_lead, urgency: low }
```

**TriggerEvent** (fired when `LastContactThreshold` scheduler tick detects elapsed time):
```python
TriggerEvent(
    trigger      = TriggerKind.LAST_CONTACT_THRESHOLD,
    entity_ref   = EntityRef(customer_slug="carrier-alpha", ..., delivery_item_id="I-1234"),
    field_deltas = {"reminder_count_unanswered": (1, 2)},   # second reminder elapsed
    correlation_id = "evt-def456",
    ...
)
```

**`resolve_rules_for_entity()`** walks the ladder per-rule_id:
1. **`send_reminder_on_no_contact`**: Global → Customer overrides → Customer version wins (`source_tier=CUSTOMER`, alpha-branded template)
2. **`escalate_after_3_misses`**: Global → no Customer override → Global wins (`source_tier=GLOBAL`)
3. **`alpha_cc_tg_lead`**: exists only at Customer tier → Customer wins (`source_tier=CUSTOMER`)

Returns a 3-element list: `[Rule(send_reminder_on_no_contact, scope=CUSTOMER, condition=lt 3), Rule(escalate_after_3_misses, scope=GLOBAL, condition=gte 3), Rule(alpha_cc_tg_lead, scope=CUSTOMER, condition=null)]`.

**`evaluate()`** applies each rule's condition against `reminder_count_unanswered=2`:
- `send_reminder_on_no_contact`: `2 < 3` → TRUE → MATCH
- `escalate_after_3_misses`: `2 >= 3` → FALSE → no match
- `alpha_cc_tg_lead`: `condition=null` → TRUE → MATCH

Returns `[RuleMatch(send_reminder_on_no_contact, actions=[SendReminder w/ alpha template]), RuleMatch(alpha_cc_tg_lead, actions=[NotifyPM tg_lead])]` — **2 RuleMatch instances**.

`workflow_engine` schedules **2 independent Celery chains**:
- Chain 1: `email_service.send_owner_reminder(alpha_branded_reminder, ...)`
- Chain 2: `notification.notify_pm(tg_lead, low)`

**Cross-chain order**: NOT guaranteed. The branded reminder email and the TG lead CC notification may run in either order (or concurrently). This is fine because they're independent side effects — neither depends on the other.

**TPM SP UI options** (per FR-31):
- **sub-1 Pause/resume**: TPM pauses `I-1234` → both RuleMatches return `pause_state="paused"` → workflow_engine skips both → no reminder + no TG-lead notification. Use case: owner on confirmed vacation; TPM doesn't want spam.
- **sub-2 polling_schedule override**: NOT applicable to this trigger directly (LastContactThreshold fires from time-elapsed scheduler, not from polling). Note: Ph-2 sub-2 expansion lets TPM edit the `N`-day threshold value itself; Ph-1 supports only `polling_schedule` breakpoint overrides.
- **sub-3 Manual trigger**: TPM can pick `SendReminder` from dropdown → fires immediately bypassing the 3-day threshold (e.g., critical deadline; TPM force-reminds now). Or pick `Escalate` → fires escalation without waiting for the 3-misses threshold (TPM knows owner is unreachable, escalates to TG lead immediately).

---

### Example 3 — `AttachmentReceived` + polling_schedule (FR-31 sub-2 case)

**Use case**: Owner uploads a 5th document to PLM for item `I-1234`; `doc_count=5` is now reached. HILDA must advance state to `DocumentReceived` AND trigger AI quality review. **Separately**, TPM has overridden the per-item polling cadence to detect uploads faster (deadline is tight).

**YAML — Global tier (trigger-action rules + polling_schedule sister shape)**:
```yaml
# customizations/rules/global/defaults.yaml
rules:
  - rule_id: advance_state_on_doc_count_reached
    trigger: AttachmentReceived
    condition:
      field: doc_count_reached
      op: eq
      value: true
    actions:
      - kind: UpdateState                          # action 0 — state first
        params: { target_state: DocumentReceived }
      - kind: TriggerAIReview                      # action 1 — review second (consumes the updated state)
        params: {}

  - rule_id: review_on_supplementary_attachment
    trigger: AttachmentReceived
    condition:
      and:                                          # boolean composition: both must hold
        - { field: doc_count_reached, op: eq, value: false }
        - { field: review_required,   op: eq, value: true  }
    actions:
      - kind: TriggerAIReview
        params: {}

polling_schedules:                                  # SEPARATE top-level shape, NOT a trigger-action rule
  - rule_id: default_polling_schedule
    tiers:
      - { days_before_deadline: null, interval_minutes: 60 }   # baseline (required)
      - { days_before_deadline: 3,    interval_minutes: 15 }
      - { days_before_deadline: 1,    interval_minutes: 5  }
```

**Postgres `AutomationRuleOverride` (TPM's FR-31 sub-2 override for I-1234)**:
```python
AutomationRuleOverride(
    delivery_item_id = "I-1234",
    rule_id          = "default_polling_schedule",
    override_payload = {
        "tiers": [
            {"days_before_deadline": None, "interval_minutes": 30},   # faster baseline
            {"days_before_deadline": 3,    "interval_minutes": 10},
            {"days_before_deadline": 1,    "interval_minutes": 2 },   # very tight near deadline
        ]
    },
    created_by_pm_id = "tpm-003",
    source_tier      = "global",                                       # tier the override is overriding
    created_at       = datetime(2026, 6, 10, 13, 0),
)
```

**TriggerEvent** (fired when FR-26 PLM polling task discovers the 5th document):
```python
TriggerEvent(
    trigger        = TriggerKind.ATTACHMENT_RECEIVED,
    entity_ref     = EntityRef(customer_slug="carrier-alpha", device_slug="smartphone-X",
                               milestone_id="M-1001", delivery_item_id="I-1234"),
    field_deltas   = {"doc_count_received": (4, 5)},
    timestamp      = datetime(2026, 6, 10, 14, 30),
    correlation_id = "evt-ghi789",
    # caller also supplies derived fields the conditions reference:
    # doc_count_reached=True (doc_count target=5, just reached), review_required=True, doc_type="test_report"
)
```

**`resolve_rules_for_entity()`** for trigger=AttachmentReceived:
1. Global tier: `[advance_state_on_doc_count_reached, review_on_supplementary_attachment]`
2. Customer / Device / Postgres tiers: no overrides for these rule_ids → Global wins for both

Returns the 2 Global rules.

**`evaluate()`** applies conditions against the event's derived field values (`doc_count_reached=true`):
- `advance_state_on_doc_count_reached`: `doc_count_reached == true` → TRUE → MATCH
- `review_on_supplementary_attachment`: `and(doc_count_reached == false, review_required == true)` → `and(false, true)` → FALSE → no match

Returns `[RuleMatch(advance_state_on_doc_count_reached, actions=[UpdateState@seq=0, TriggerAIReview@seq=1], pause_state="active")]` — **1 RuleMatch with 2 ordered actions**.

`workflow_engine` builds **one Celery chain**: `update_state.s(target_state=DocumentReceived) | trigger_ai_review.s()`. State change happens before review, guaranteeing review sees the correct state.

**Separate polling_schedule resolution (not part of this trigger flow but exercised by FR-31 sub-2)**:

`workflow_engine` reschedules `I-1234`'s PLM polling task; calls `resolve_polling_schedule_for_item("I-1234")`:
1. Global polling_schedule: tiers `(60, 15, 5)`
2. Customer / Device: no override
3. **Postgres override matches** `(item=I-1234, rule_id=default_polling_schedule)` → Postgres wins → tiers `(30, 10, 2)`

`evaluate_polling_schedule(tiers=(30,10,2), days_to_deadline=2)` → interval `10 min`. `workflow_engine` reschedules the next polling tick in 10 minutes. SP UI shows the item's polling-schedule override as **"overridden from global default (faster cadence per TPM tpm-003 on 2026-06-10)"** per the `source_tier` field.

**TPM SP UI options** (per FR-31):
- **sub-1 Pause/resume**: TPM pauses `I-1234` → on next AttachmentReceived event, the RuleMatch returns `pause_state="paused"` → workflow_engine skips → state does not advance, no AI review fires. Use case: TPM wants to manually inspect the uploaded document before HILDA processes it (e.g., audit a sensitive submission).
- **sub-2 polling_schedule override** (the active case here): TPM clicks the inline polling-schedule edit control on I-1234's row → enters new tier values → SP UI writes to Postgres via the FR-87 SP-alert email flow → rule_engine reloads its Postgres override snapshot on next eval → workflow_engine's next polling-task reschedule uses the new tiers. TPM can revert by deleting the override row.
- **sub-3 Manual trigger**: TPM can pick `TriggerAIReview` from dropdown → fires AI review on already-received docs without waiting for a new attachment event (e.g., after a parser patch landed). Or pick `TriggerParser` standalone → re-runs the rule-based parser on the doc. Or pick `UpdateState` with target_state=`DocumentReceived` → force-advance state (rare; usually only when an automated guard misfired).

---

## Non-goals

- **Not a Celery client / not a task scheduler** — `workflow_engine` owns Celery; rule_engine is library code consumed by Celery task bodies. Per `[D-022]`.
- **Not a state-transition validator** — `delivery_state` legality (e.g., can `Open` transition directly to `Closed`?) is `tracker`'s responsibility (the 11-state machine owner). rule_engine emits `UPDATE_STATE` actions with target_state; tracker enforces the transition guards (FR-7 + FR-28 OwnerStatusConfirmed 2-condition guard, etc.) before applying the state.
- **Not an action executor** — actions are dispatched by workflow_engine to the owning module (email_service, customer_adapter, issue_tracker, sharepoint_integration, storage, etc.). rule_engine never sends an email, never uploads a file, never writes to SP.
- **Not the manual-trigger surface (FR-31 sub-3)** — manual triggers from the SP UI Trigger Action dropdown bypass this module entirely and enqueue a Celery task directly via workflow_engine. rule_engine evaluation is for automated rule-driven flow only.
- **Not a credential reader** — `CredentialExpired` trigger fires when `credential_service.get_credential` raises or returns missing; the firing event is constructed by the caller (`workflow_engine` task body that consumed the missing credential) and passed to `evaluate()`. rule_engine does NOT call credential_service.
- **Not a YAML schema validator beyond Pydantic shape** — semantic validation (does this rule make business sense? does this trigger/action pair compose with the FR-7 state machine?) is the ops team's responsibility + the collision-audit + `/drift-check design` mode at architecture phase. rule_engine's load-time validation is shape-only.
- **Not a rule editor / not the FR-31 control panel backend** — the SP UI pause/resume + parameter-customize + manual-trigger surfaces live in SP UI + `sharepoint_integration`. rule_engine consumes the resulting Postgres override / pause-state records; doesn't surface a UI of its own.
- **Not a runtime LLM consumer** — no `llm` module dependency; all rule evaluations are deterministic. LLM calls (`TRIGGER_AI_REVIEW`, etc.) are workflow_engine's domain after rule_engine fires the action.
- **Not a `polling_schedule` rule storage** — polling_schedule rules live in `customizations/rules/<scope>/<file>.yaml` like any other rule. `polling_schedule.py` is just the evaluator helper for tier-resolution.
- **Not a config reload monitor** — file-watching for YAML edits is ops responsibility (SIGHUP signal). rule_engine exposes `reload()` to be called; doesn't poll the filesystem.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate`, `register_code` (RUL-* codes registered).
- `template_schema` — `DeliveryItemBase`, `AutomationRuleBase`, `AutomationRule` Pydantic model (loader validates rule YAML against this), `DeliveryState` enum (for `UPDATE_STATE` target_state validation), `TGGroupBase` (rules may scope to TG via condition expressions).
- `storage` — Postgres `AutomationRuleOverride` table read (one query per `delivery_item_id` per evaluation, cached per-item with TTL per `RuleEngineConfig.postgres_override_cache_ttl_s`); pause-state read for `PauseStateLookup` Protocol.
- `PyYAML` (3rd party) — YAML file parsing.

*(Conspicuous absences: no `sharepoint_integration` — rule YAML lives in `customizations/`, not in SP. No `email_service` / `messenger` / `issue_tracker` / `customer_adapter` — actions are emitted as data, not executed. No `llm` — evaluation is deterministic. No `workflow_engine` — workflow_engine depends on rule_engine, not the other way around.)*

---

## Depended on by

- `workflow_engine` — every Celery task body calls `RuleEngine.evaluate(event)` to determine what actions to fire next; workflow_engine schedules each `RuleMatch.actions` chain as an independent Celery task; consumes pause_state and skips paused matches per the `RuleEngineConfig` policy. Authoritative caller.
- `tracker` — at `delivery_state` transition guard time, may consult `RuleEngine.evaluate` for `STATE_CHANGE`-triggered rules; receives `UPDATE_STATE` actions back from rule_engine via workflow_engine. (Indirect via workflow_engine — tracker does not directly import rule_engine; both go through Celery tasks.)
- `email_service` — receives `SEND_REMINDER` / `ESCALATE` / `SEND_INITIAL_OUTREACH` / `NOTIFY_NEW_OWNER` / `NOTIFY_PM` action dispatches from workflow_engine (workflow_engine resolves action kind → email_service task).
- `customer_adapter` — receives `QUEUE_SUBMISSION` dispatches (workflow_engine resolves to `customer_adapter.upload_attachment` for the item's files).
- `issue_tracker` (core + customizations) — receives `START_ITEM_COLLECTION` dispatches (PLM issue create) + Ph-2 `TRIGGER_PLM_CLEANUP`.
- `storage` — receives `MILESTONE_STORAGE_CLEANUP` dispatches (FR-76 NSD subtree + local cache delete).
- `sharepoint_integration` — receives `UPDATE_STATE` writeback dispatches (via workflow_engine → tracker → sharepoint_integration).

---

## Deferred (Ph-2 / Ph-3+)

- **Ph-2 triggers** (not in Ph-1 `TriggerKind` enum; added when Ph-2 lands):
  - `ItemDeleted` — fires when PM removes item via FR-3 → `CancelOutstanding`
  - `UnroutedDocumentAccumulated` — per FR-78 default-work-item Ph-2 escalation path
  - `ItemModified` sub-trigger expansion for Ph-2 (corp messenger reply, etc.)
- **Ph-2 actions** (not in Ph-1 `ActionKind` enum):
  - `CancelOutstanding` — cancel pending reminders/escalations for a removed item
  - `NotifyOwnerDocCountPending` — corp messenger owner notification
  - `TriggerVersionSelection` — corp messenger version-selection workflow per FR-66
  - `TriggerPLMCleanup` — PLM stale attachment cleanup per FR-67
  - `TriggerODF` — Owner Discovery Function per FR-71
  - `SendOwnerRoutingQuery` — corp-messenger upstream-of-TPM query per FR-83
- **Ph-2 FR-31 sub-1 rule-scoped pause** — currently Ph-1 pause is all-rules-or-none per item; Ph-2 adds per-rule-type pause (e.g., pause `DeadlineProximity` only). Requires `PauseStateLookup.is_rule_paused(delivery_item_id, rule_id)` extension.
- **Ph-2 FR-31 sub-2 full rule-parameter override UI** — currently Ph-1 supports only `polling_schedule` breakpoint override; Ph-2 adds arbitrary parameter overrides (LastContactThreshold N, DeadlineProximity N, reminder template per item) — Postgres `AutomationRuleOverride` schema extends accordingly.
- **Ph-3+ rule-execution telemetry stream** — currently RUL-MET captures per-evaluation latency + match counts; Ph-3+ may add an aggregated rule-effectiveness telemetry stream (which rules fire most often; which actions get paused; which scope tiers dominate) for ops tuning.
- **Ph-3+ rule-conflict simulator** — `--simulate <trigger> --entity <ref>` mode that runs evaluation against a candidate YAML edit before deploy; surfaces collision warnings before they hit production. Defers to Ph-3+ when ops scale demands it.
- **Ph-3+ rule-DSL extension via plugins** — currently the condition operator set is closed in `core/`; Ph-3+ could allow `customizations/rules/operators/<op>.py` plugins for custom predicate logic with an explicit allow-list. Avoid until proven need.

---

## Test interface

```
python -m core.src.rule_engine.rule_engine_cli --diagnostic
```
Validates YAML loading across all tiers; emits orphan-audit + collision-audit findings; reports per-tier rule counts + trigger / action distribution. Safe to run in any environment; no Postgres writes. Sample:
```
RPT|RUL|run-00001|2026-06-10T10:00:00Z|tiers_loaded=3|rules_total=42|rules_global=30|rules_customer=10|rules_device=2|postgres_overrides=5|orphan_warnings=0|collision_warnings=1|trigger_kinds=15|action_kinds=18
```

```
python -m core.src.rule_engine.rule_engine_cli --validate
```
Pydantic-validates all YAML files; emits RUL-E* codes for shape errors; safe in CI. Returns non-zero exit on RUL-E*.

```
python -m core.src.rule_engine.rule_engine_cli --explain --trigger <kind> --entity '{"customer_slug":"c1","device_slug":"d1"}' [--sub-trigger <s>] [--field-deltas '{...}']
```
Synthesizes a TriggerEvent + runs `evaluate()` + `explain()`; emits resolution trace (which scope tier won; which rules matched; what actions in what order). For ops debugging "why did rule X fire?" — no side effects. Sample:
```
MET|RUL|run-00001|2026-06-10T10:00:00Z|trigger=AttachmentReceived|matched_rules=3|matches=advance-state:global,trigger-review:customer,ack-owner:device|paused_matches=0|eval_latency_ms=12
```

```
python -m core.src.rule_engine.rule_engine_cli --simulate <yaml_path>
```
Loads a candidate YAML in addition to current rules; runs collision-audit + orphan-audit against the merged set; emits warnings for the candidate before deploy. Ph-3+ extension hook.

**QC template** (`RUL:rule_evaluation` — registered in `diagnostics/qc.py`):
```
Fields: trigger_kind (enum: TriggerKind values),
        matched_count (int),
        winning_scope_distribution (enum: global / customer / device / postgres_override),
        paused_match_count (int),
        eval_latency_bucket (enum: fast | normal | slow | timeout),
        collision_warnings (int),
        orphan_warnings (int),
        result (enum: OK / WARN / FAIL)
```

---

<!-- BEGIN:STRUCTURE -->
[DRAFT] No code present yet (only empty `__init__.py`) — architecture-phase doc-first design intent. Structure regeneration skipped per regen-map spec; will populate from code on first /switch-phase development pass.
<!-- END:STRUCTURE -->
