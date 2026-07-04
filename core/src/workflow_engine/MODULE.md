# Module: workflow_engine

> **Status:** Initial draft 2026-06-10 + **2026-06-23 architect cascade revisit applied (D1-D12 against locks since 2026-06-10)**. Celery app + task dispatcher per `[D-022]` — owns the action-kind → Celery task mapping that `rule_engine.RuleMatch` outputs are scheduled through. **Ph-1 scope NARROWED 2026-06-23 per architect direction** (mirroring rule_engine D4/D6 cascade): (a) SIGHUP Celery-beat schedule reload **deferred to Ph-2** (Ph-1 = build_beat_schedule at startup only; service restart required for schedule changes); (b) FR-31 sub-2 Postgres-override consumption in beat schedule **deferred to Ph-2** (Ph-1 polling intervals come from Global YAML rules only — no per-item override layer); (c) FR-31 sub-1 per-item pause via `item.rules_paused` SP column (NEW Ph-1 mechanism per rule_engine D5 cascade) — TriggerDispatcher reads item snapshot from storage BEFORE calling rule_engine.evaluate; PauseStateLookup Protocol dropped from Public surface. Sections re-curated; code implementation continues in development phase.
>
> **Rollback log:**
> - **2026-07-02 (D-139/D-140/D-141/D-142/D-143 cascade)** — 4 new task bindings landed in-window: (1) `tasks/pm_approval.apply_pm_approval_task` per D-139 (SP-authoritative multi-field atomic mirror; dispatcher `_PM_APPROVAL_DELTA_FIELDS` refinement discriminates `PmApproved` sub-trigger from generic Deliverables CHANGED); (2) `tasks/submit_to_carrier.submit_to_carrier_task` per D-140 (milestone-scoped upload orchestrator; `browser_automation` queue; `autoretry_for=(Exception,) + max_retries=2 + retry_backoff=180 + retry_backoff_max=300 + retry_jitter=True`); (3) `tasks/milestone.close_all_items_task` binding wired per D-141 (task body was pre-window per FR-64 Option (b) but never dispatched — `CLOSE_ALL_ITEMS` ActionKind + rule + binding land this window); (4) `tasks/sync_deliverable_fields.sync_deliverable_fields_task` per D-141 (Deliverables CHANGED null-guarded merge — int=0 / str=None|`""` / list=[] / bool=key-missing → skip; skip preserves current). `tasks/__init__.py` updated. `bootstrap.py` gains `_bootstrap_template_lookup()` calling `template_schema.template_lookup.load_all_customer_templates()` at worker startup + `_build_customer_adapter()` auto-discovers per-customer subclass via `HILDA_CUSTOMER_ID` env var + `customizations.customer_adapter.<customer_id_lower>_adapter.ADAPTER_FACTORY(audit_writer=audit)` convention + optional `bootstrap_directories(template)` at boot (default ON; opt out via `HILDA_SKIP_GDRIVE_DIRS_BOOTSTRAP=1`). Guard 4 in `tracker.guards` now trusts `trigger_source='submit_to_carrier_task'` (and by extension `'sync_backfill_submit_to_carrier'` per D-142) as authoritative evidence of upload completion per D-140; `_transition_to_submitted` returns bool; caller only bumps `uploaded_items` on true success (else `partial_items`). Kickoff eligibility filter excludes `item_type=Default` (defense-in-depth against SP UI engineer's setup script leaving `force_tracking_enabled=True` on the SP row default per FR-81). Reconciliation cascade per D-142/D-143 is DESIGNED but implementation deferred to next session — `tasks/reconcile.py` + `config/reconcile.json` + Celery beat wiring for 5 sync tasks (delivery_item_count / milestone-start-collection / deliverable-approved / milestone-submit-to-carrier / milestone-close-all-items). Public surface + Sub-modules sections should reference the 4 new task files on next full-file curation pass. Commits: `2ca7a2a`, `3874041`, `032ad19`, `3ffcddb`, `820764a`, `c818963`, `62a3222`, `2457af4`, `c093692`, `8a60abb`, `5b09af4`, `5cbc383`, `53e11ae`, `e5e955b`, `9072194`, `87ee967`.
> - **2026-06-23 (architect cascade revisit — 12 drift items applied against recent locks since 2026-06-10)** — strict-order module-by-module sweep Module #8 of 13 (per architect direction 2026-06-21). **D1 — slug → id rename per `[D-091]`**: `event_context` dict keys `customer_slug` → `customer_id`, `device_slug` → `device_id` (caller-side construction; Celery task bodies read updated keys); sharepoint_config path `customers/<slug>.yaml` → `customers/<customer_id>.yaml`. **D2 — `PauseStateLookup` Protocol DROPPED** per rule_engine D5 cascade 2026-06-23: `TriggerDispatcher.__init__` signature drops `pause_lookup` param; Ph-1 pause check reads `item.rules_paused: bool` directly. **D3 — SIGHUP Celery-beat schedule reload DEFERRED to Ph-2** per rule_engine D6 cascade: Ph-1 `build_beat_schedule` runs at hilda-beat startup ONLY; YAML or rule changes require hilda-beat restart. `reload_beat_schedule()` kept as Ph-2 forward-looking. **D4 — FR-31 sub-2 Postgres polling-override consumption DEFERRED to Ph-2** per rule_engine D4 cascade: Ph-1 `build_beat_schedule` calls `rule_engine.resolve_polling_schedule_for_item` which returns Global-tier YAML polling_schedule only (no Postgres override layer); per-item polling-cadence overrides Ph-2. **D5 — `expected_completion_date` removed per `[D-085]`**: `deadline_evaluator.py` computes `days_to_deadline = Milestone.target_date - today` from the Milestones SP list row (was per-item `expected_completion_date`); all items in the milestone share the same `target_date` per `[D-085]`. **D6 — `owner_email` → 4-field owner identity per `[D-080]` + `[D-086]`**: sp_alert_source constructs `OwnerReassigned` sub-trigger when ANY of 4 owner fields (`owner_corp_usa_email` / `owner_corp_email` / `owner_corp_id` / `owner_name`) changes on the Deliverables row (was single `owner_email` field); sp_alert_parser's field-name → sub-trigger map updated. **D7 — FR-64 Option (b) HILDA-owned per-item cascade for Close All Items**: NEW workflow_engine task `tasks/milestone.close_all_items` (per tracker MODULE.md FR-64 lock 2026-06-20 + tracker Worked Example) — milestone-level "Close All Items" SP UI button writes `closed_all_items_triggered_at` on Milestones row; sp_alert_source detects + dispatches; this task iterates over CLOSE-eligible items and calls `tracker.update_delivery_state(target=CLOSED, trigger_source="tpm_button")` per item; MilestoneAllClosed trigger fires downstream after all items committed. **D8 — llm Ph-1 phasing acknowledgment**: TRIGGER_AI_REVIEW action remains in registry per Guardrail #3 forward-compat (REVIEW_DOCUMENT TaskKind), but per architect direction 2026-06-22 (llm Ph-1 phasing) Ph-1 first-pass active = ROUTE_ATTACHMENT + CLASSIFY_DOC_TYPE only; REVIEW_DOCUMENT is Ph-1 next pass + runtime-dormant in early drop (review_required=false on all items per architect lock 2026-06-19). TRIGGER_AI_REVIEW task body Ph-1 = stub-with-skip; activates fully at next pass. **D9 — FR-52 5-step pipeline driver lives in email_service Module #12** (not workflow_engine): rule_engine doesn't have a `ROUTE_ATTACHMENT` ActionKind; routing is internal to FR-52 step 4 within email_service's attachment-handling pipeline. workflow_engine's role for FR-52 is firing `AttachmentReceived` TriggerEvents (from email_ingest_source / nsd_watch_source / plm_watch_source) that may match rules with downstream actions. **D10 — Status header refresh (this entry)**. **D11 — Anchors update**: Purpose adds `[D-080]`, `[D-083]`, `[D-085]`, `[D-086]`, `[D-088]`, `[D-091]`; D-DRAFT candidates `D-094` (item_type mixed-case), `D-100` (FR-64 Option (b)). **D12 — TriggerDispatcher item-snapshot flow** (NEW per D2): when `event.entity_ref.delivery_item_id` is non-None, TriggerDispatcher reads the item snapshot from storage BEFORE calling `rule_engine.evaluate(event, item_snapshot=item)`; the snapshot is also passed into the Celery `event_context` so downstream task bodies don't need to re-read.
> - **2026-06-10 (initial draft)** — first MODULE.md for `workflow_engine`. Scope locked to **Ph-1 only**: 18 Ph-1 ActionKind tasks (mapped from `rule_engine.ActionKind`), trigger dispatcher consuming `TriggerEvent` from 6 trigger sources (`sp_alert_parser`, `email_service` ingress, NSD watcher, PLM poller, Celery-beat scheduler tick, `tracker` state-change), Celery-beat singleton schedule for periodic tickers (FR-23 owner-reply polling, FR-26 PLM polling, FR-55 NSD polling, deadline-tiered re-arm per FR-31 sub-2 polling_schedule overrides; sub-2 deferred to Ph-2 per D4 cascade 2026-06-23), Postgres result backend + Redis broker per `[D-022]` / `[D-043]`, 4 task queues by latency profile. Ph-2 ActionKind tasks → `## Deferred`. Anchors `[D-021]` (modular monolith, 4-workload split: `hilda-api` / `hilda-worker` / `hilda-llm-gateway` / `hilda-beat`), `[D-022]` (workflow_engine = Celery app + dispatcher; rule_engine = pure evaluator boundary), `[D-026]` (Docker Compose Ph-1/Ph-2; K8s Ph-3+), `[D-043]` (Redis broker Ph-1/Ph-2; RabbitMQ Ph-3+), `[D-066]` (per-`RuleMatch` independent Celery chain; intra-rule ordering preserved, no cross-rule order), `[D-064]` (HILDA→SP REST sole writeback channel; this module's `UpdateState` task path), `[D-047]` (SP→HILDA via SP-alert email; this module's trigger ingestion path from `sp_alert_parser`). New error code prefix `WFL` already in `diagnostics.PREFIX_REGISTRY`.

**Purpose**: HILDA's Celery app + central task dispatcher per `[D-022]`. Owns: (a) the Celery application instance (`hilda_celery_app`) configured with Redis broker + Postgres result backend per `[D-043]`; (b) the **`ACTION_KIND_TO_CELERY_TASK` registry** that maps `rule_engine.ActionKind` values to concrete Celery task callables (some workflow_engine-owned, some delegated to downstream modules' task definitions); (c) the **`TriggerDispatcher`** which receives `TriggerEvent`s from 6 trigger-source sites, calls `rule_engine.evaluate(event)`, and schedules each returned `RuleMatch`'s action chain as an **independent Celery chain** per `[D-066]`; (d) the **Celery-beat schedule** loader for periodic tickers (FR-23 owner-reply polling, FR-26 PLM polling, FR-55 NSD polling, deadline-tier re-arm); (e) **FR-31 sub-3 manual-trigger handler** that bypasses `rule_engine` and dispatches a single Celery task directly per TPM SP UI button click. **No business logic of its own** — workflow_engine is a Celery scaffolding + dispatch layer; the actual work (sending emails, uploading files, updating states, etc.) lives in `email_service` / `customer_adapter` / `issue_tracker` / `messenger` / `tracker` / `storage` / `sharepoint_integration`. workflow_engine just orchestrates. **Serves**: FR-8 (Start Collection at tracker creation), FR-9 (per-modality initial outreach), FR-10 (reminders + escalation), FR-13 (file processing pipeline tail), FR-18 (carrier submission), FR-23 (owner-reply deadline-tiered polling), FR-26 (PLM polling), FR-31 (sub-1 pause respect + sub-3 manual trigger), FR-52 (routing dispatch from email_service ingest), FR-55 (NSD polling), FR-63 (PM Submit-to-Carrier batch), FR-64 (Close-All-Items milestone batch), FR-73 (Ph-1 carrier-package zip two-click flow + Ph-2 multi-file submit), FR-74 (collection phase closure + halt polling), FR-76 (milestone storage cleanup), FR-83 (TPM-manual document reassignment), FR-87 (TPM resolution A→B→C dispatched via sp_alert_parser). Anchors `[D-021]` (4-workload split — workflow_engine runs in `hilda-worker` + `hilda-beat` Deployments), `[D-022]` (Celery + Redis + workflow_engine ownership of dispatch), `[D-026]` (Docker Compose Ph-1/Ph-2), `[D-043]` (Redis broker + result-backend selection), `[D-066]` (per-RuleMatch independent Celery chain), `[D-064]` + `[D-047]` (SP-HILDA channel discipline), `[D-080]` (4-field owner identity preference rule for outreach), `[D-083]` (Projects-per-customer architecture; PM identity 3-tuple resolution downstream), `[D-085]` (Milestone.target_date sole authoritative deadline; per-item `expected_completion_date` removed), `[D-086]` (free-form text owner identity discipline), `[D-088]` (3-tuple PM resolution from Projects-list TPM column), `[D-091]` (slug → id rename throughout: `customer_slug`→`customer_id`, `device_slug`→`device_id`), `[D-005]` (`--diagnostic` + `--validate` CLI), `[D-002]` (WFL-* error codes + RPT/MET/QC compact reports + no-proprietary-content invariant). D-DRAFT candidates `D-094` (item_type mixed-case per SP UI engineer lock 2026-06-23), `D-100` (FR-64 Option (b) HILDA-owned per-item cascade for Close All Items).

**Workload assignment**: Of the **4 workloads** in `[D-021]`'s modular-monolith split (`hilda-api` / `hilda-worker` / `hilda-llm-gateway` / `hilda-beat`), workflow_engine code is the entry point for **2 of them**: **`hilda-worker`** (Celery worker pool — runs the task bodies; scaled per queue; typical 4–8 workers per host) + **`hilda-beat`** (Celery beat singleton — fires periodic tasks; exactly one instance per deployment per `[D-022]`). Same code image; each workload launches a different Celery subcommand (`celery worker` vs `celery beat`). The other 2 workloads relate to workflow_engine asymmetrically: `hilda-api` **imports** the Celery app (calls `apply_async` to enqueue tasks for corp_messenger_gateway / corp_plm_gateway POSTs + ops admin endpoints) but does NOT execute task bodies; `hilda-llm-gateway` does NOT touch Celery — workflow_engine's `TRIGGER_AI_REVIEW` task calls `hilda-llm-gateway` via HTTP per `[D-052]`/`[D-059]` tri-backend.

**Queue topology** (Ph-1): **4 queues** by latency profile + per-task priority — `default` (fast outreach / SP writes / notifications; 4–8 workers per host); `llm_calls` (FR-53 LLM document review, FR-12 path c message classification, FR-52 step 4 ROUTE_ATTACHMENT, FR-85 CLASSIFY_DOC_TYPE — minutes-scale per `[D-052]` empirical latency; 1–2 workers per host, dedicated to avoid blocking the default queue); `browser_automation` (`customer_adapter.upload_attachment` — 10–100× slower than REST per `[D-054]` impl note; 1 worker per host per carrier session pool); `periodic` (beat-triggered polling tickers — short-lived, fires `TriggerEvent`s onto `default` queue then exits). Queue routing is configured per-task at decorator time; `apply_async(queue=...)` overrides supported for ops debugging.

**Per-task latency targets**: `default` queue tasks <500 ms (network + SP write + log); `llm_calls` queue 5–120 s per task (per-backend A/B-test gate; tracked in MET); `browser_automation` queue 10–90 s per `upload_attachment` (per `customer_adapter` MODULE.md latency budgets); `periodic` tickers <50 ms each (they just emit `TriggerEvent`).

---

## Sub-modules

```
core/src/workflow_engine/
  __init__.py
  celery_app.py                     ← Celery application instance; broker/result-backend config; queue routing; signal handlers (task_prerun, task_postrun, task_failure for WFL-* emission)
  registry.py                       ← ACTION_KIND_TO_CELERY_TASK mapping; build_chain_from_rule_match() helper; manual-trigger registry for FR-31 sub-3
  dispatcher.py                     ← TriggerDispatcher class: receives TriggerEvent, calls rule_engine.evaluate, schedules RuleMatches as independent Celery chains per [D-066], honors FR-31 sub-1 pause
  beat_schedule.py                  ← Celery-beat schedule loader; Ph-1 (per D3 cascade 2026-06-23): builds periodic-task schedule from AutomationRules + global-tier polling_schedule rules at startup ONLY (no SIGHUP reload Ph-1 — service restart required for schedule changes; Ph-2 adds reload_beat_schedule)
  tasks/                            ← per-ActionKind Celery task definitions (one .py per group)
    state.py                        ← UPDATE_STATE (delegates to tracker.update_delivery_state); INSTANTIATE_DEFAULT_WORK_ITEM (delegates to tracker)
    outreach.py                     ← SEND_REMINDER, SEND_INITIAL_OUTREACH, NOTIFY_NEW_OWNER (delegate to email_service; outreach recipient resolution uses 4-field owner per [D-080] + [D-086])
    escalation.py                   ← ESCALATE (delegates to messenger); NOTIFY_PM, NOTIFY_HILDA_OPS (workflow_engine-owned — writes CommunicationLog via sharepoint_integration + dashboard surface)
    collection.py                   ← START_ITEM_COLLECTION (workflow_engine-owned; creates PLM issue via issue_tracker per FR-5 + [D-035] owner_corp_id grouping + chains SEND_INITIAL_OUTREACH)
    review.py                       ← TRIGGER_PARSER (delegates to test_report); TRIGGER_AI_REVIEW (delegates to llm; per architect direction 2026-06-22 llm Ph-1 phasing — REVIEW_DOCUMENT is Ph-1 next pass + runtime-dormant in early drop since review_required=false on all items per architect lock 2026-06-19; Ph-1 first-pass task body = stub-with-skip)
    submission.py                   ← QUEUE_SUBMISSION (workflow_engine-owned; resolves target_folder via FR-77 NFR-21 amendment composition customer_delivery_info + delivery_path_template + target_folder; chains customer_adapter.upload_attachment per file)
    milestone.py                    ← MILESTONE_STORAGE_CLEANUP (delegates to storage); HALT_MILESTONE_POLLING (workflow_engine-owned — modifies beat schedule); FINAL_SWEEP (workflow_engine-owned — one-shot poll burst); **close_all_items** (NEW per D7 cascade 2026-06-23 + FR-64 Option (b) HILDA-owned per-item cascade lock 2026-06-20 — iterates CLOSE-eligible items in a milestone and calls tracker.update_delivery_state(target=CLOSED, trigger_source="tpm_button") per item; MilestoneAllClosed trigger fires downstream)
    routing_resolution.py           ← REASSIGN_DOCUMENT_TO_WORK_ITEM (delegates to tracker.reassign_document_to_workitem per FR-83 + FR-87 step (A)); PROPAGATE_TAGS_TO_ACTIVE_TRACKERS (delegates to tracker.propagate_tags_to_active_trackers per FR-82 nested tag-set lock 2026-06-20 — list[list[str]]); REARM_DEADLINE_PROXIMITY (workflow_engine-owned; sourced from Milestone.target_date edit per [D-085])
  polling/                          ← periodic-ticker task implementations
    plm_polling.py                  ← per-item PLM polling task (FR-26); fires AttachmentReceived TriggerEvent
    nsd_polling.py                  ← per-TG NSD polling task (FR-55); fires AttachmentReceived TriggerEvent
    owner_reply_polling.py          ← inbound-mailbox polling task (FR-23 third-tier fallback); fires OwnerStatusConfirmed / SP-alert TriggerEvents
    deadline_evaluator.py           ← scheduler tick that evaluates LastContactThreshold + DeadlineProximity per active item; fires TriggerEvents on threshold cross
  trigger_sources/                  ← façade wrappers around the 6 trigger-source sites; each constructs canonical TriggerEvents and calls TriggerDispatcher
    sp_alert_source.py              ← invoked by email_service.sp_alert_parser when SP-alert email arrives (FR-84); constructs TriggerEvent per action verb
    email_ingest_source.py          ← invoked by email_service when owner-reply email parsed (FR-12 paths a/b/c)
    nsd_watch_source.py             ← invoked by polling/nsd_polling.py
    plm_watch_source.py             ← invoked by polling/plm_polling.py
    scheduler_tick_source.py        ← invoked by polling/deadline_evaluator.py
    state_change_source.py          ← invoked by tracker.update_delivery_state after a transition commits (StateChange trigger)
  manual_trigger.py                 ← FR-31 sub-3 handler: SP UI button → SP-alert email → sp_alert_parser routes here (trigger_source=manual); dispatches a single Celery task bypassing rule_engine
  diagnostics_cli.py                ← --diagnostic / --queue-stats / --replay-event / --beat-schedule modes
  workflow_engine_cli.py            ← user-facing wrapper for ops debugging
  tests/
  MODULE.md                         ← this file
```

---

## Public surface

### `celery_app.py`

```python
hilda_celery_app: Celery = Celery(
    "hilda",
    broker      = "redis://...",                  # from WorkflowEngineConfig.broker_url
    backend     = "db+postgresql://...",          # Postgres result backend via storage module
    include     = ["core.src.workflow_engine.tasks", "core.src.workflow_engine.polling"],
)

# Queue routing (declared at decorator time per task; configurable in WorkflowEngineConfig)
hilda_celery_app.conf.task_routes = {
    "core.src.workflow_engine.tasks.review.trigger_ai_review":            {"queue": "llm_calls"},
    "core.src.workflow_engine.tasks.submission.queue_submission":         {"queue": "browser_automation"},
    "core.src.workflow_engine.polling.*":                                  {"queue": "periodic"},
    "*":                                                                   {"queue": "default"},
}
```

Signal handlers (`task_prerun`, `task_postrun`, `task_failure`, `task_retry`) emit WFL-* compact-report lines per `[D-002]`. No proprietary content; only task name, queue, latency bucket, error code if applicable.

### `registry.py`

```python
@dataclass(frozen=True)
class TaskBinding:
    """Maps one ActionKind to its Celery task callable + queue + retry policy."""
    action_kind:      ActionKind                # from rule_engine.models
    celery_task:      Callable[..., Any]        # the @hilda_celery_app.task-decorated function
    queue:            str
    retry_policy:     RetryPolicy               # max_retries, backoff_factor, retry_on (list of exception types)

# Ph-1 mapping — populated at module import time by each tasks/*.py file
ACTION_KIND_TO_TASK: dict[ActionKind, TaskBinding] = {
    ActionKind.SEND_REMINDER:                  TaskBinding(...),
    ActionKind.ESCALATE:                       TaskBinding(...),
    ActionKind.UPDATE_STATE:                   TaskBinding(...),
    ActionKind.START_ITEM_COLLECTION:          TaskBinding(...),
    ActionKind.SEND_INITIAL_OUTREACH:          TaskBinding(...),
    ActionKind.NOTIFY_NEW_OWNER:               TaskBinding(...),
    ActionKind.TRIGGER_PARSER:                 TaskBinding(...),
    ActionKind.TRIGGER_AI_REVIEW:              TaskBinding(...),
    ActionKind.QUEUE_SUBMISSION:               TaskBinding(...),
    ActionKind.NOTIFY_PM:                      TaskBinding(...),
    ActionKind.NOTIFY_HILDA_OPS:               TaskBinding(...),
    ActionKind.INSTANTIATE_DEFAULT_WORK_ITEM:  TaskBinding(...),
    ActionKind.MILESTONE_STORAGE_CLEANUP:      TaskBinding(...),
    ActionKind.HALT_MILESTONE_POLLING:         TaskBinding(...),
    ActionKind.FINAL_SWEEP:                    TaskBinding(...),
    ActionKind.REASSIGN_DOCUMENT_TO_WORK_ITEM: TaskBinding(...),
    ActionKind.PROPAGATE_TAGS_TO_ACTIVE_TRACKERS: TaskBinding(...),
    ActionKind.REARM_DEADLINE_PROXIMITY:       TaskBinding(...),
}

def build_chain_from_rule_match(
    match:           "RuleMatch",
    event_context:   dict[str, Any],          # serialisable derived context: customer_id, milestone_id, delivery_item_id, correlation_id (per [D-091] slug->id rename 2026-06-23)
) -> "Signature":                              # Celery canvas: chain(task0.s(...), task1.s(...))
    """Per [D-066]: build one Celery chain from a RuleMatch's ordered actions list.
    Each chain task receives (action.params, event_context) and the previous task's
    return value (Celery canvas default). Raises WFL-E001 if action_kind not in registry."""

def lookup_for_manual_trigger(action_kind: ActionKind) -> TaskBinding:
    """Per FR-31 sub-3: TPM SP UI manual trigger dispatch bypasses rule_engine.
    Looks up the action_kind's TaskBinding; caller (manual_trigger.py) calls
    binding.celery_task.apply_async(...) directly. Raises WFL-E001 if not in registry."""
```

### `dispatcher.py`

```python
class TriggerDispatcher:
    """Receives TriggerEvent from any of the 6 trigger-source sites; calls rule_engine.evaluate;
    schedules each RuleMatch's chain as independent task per [D-066].

    Per D2 + D12 cascade 2026-06-23: PauseStateLookup Protocol DROPPED. Pause check now reads
    `item.rules_paused: bool` directly from a storage-fetched item snapshot, threaded into
    rule_engine.evaluate via the `item_snapshot` kwarg.
    """

    def __init__(
        self,
        rule_engine:        "RuleEngine",
        storage:            "Storage",                # NEW per D12 cascade — for item snapshot fetch
        celery_app:         "Celery"           = hilda_celery_app,
    ) -> None: ...

    def dispatch(self, event: "TriggerEvent") -> "DispatchResult":
        """Pipeline (Ph-1 per D2 + D12 cascade 2026-06-23):
        1. If event.entity_ref.delivery_item_id is non-None: fetch item snapshot from storage.
           (Item-less events -- MilestoneAllClosed, CredentialExpired, etc. -- pass item_snapshot=None.)
        2. rule_engine.evaluate(event, item_snapshot=item) -> list[RuleMatch].
           rule_engine reads item.rules_paused for per-item pause check; paused matches come back
           with pause_state='paused' (workflow_engine respects this -- does not schedule).
        3. For each RuleMatch: if pause_state='paused', log WFL-W001 + skip (do not enqueue).
        4. For each non-paused RuleMatch: build_chain_from_rule_match(match, event_context) and
           .apply_async(). Each RuleMatch is its own chain -- no cross-RuleMatch ordering per [D-066].
        5. Emit WFL-MET record (matched_count, paused_count, scheduled_count, dispatch_latency_ms,
           correlation_id from event).
        Returns DispatchResult with task IDs + skipped reason list. Pure orchestration -- no business
        logic; the actions themselves run in worker pool."""

@dataclass(frozen=True)
class DispatchResult:
    correlation_id:   str
    scheduled_tasks:  list[str]                # Celery task IDs
    skipped_matches:  list[tuple[str, Literal["paused"]]]   # (rule_id, reason)
    matched_count:    int                       # rule_engine matches before pause filter
```

### `beat_schedule.py`

```python
@dataclass(frozen=True)
class BeatScheduleEntry:
    """One Celery-beat periodic task entry."""
    task_name:     str                          # fully-qualified Celery task path
    schedule:      timedelta | crontab          # Celery schedule spec
    args:          tuple                        # static args (e.g., the item_id for per-item polling)
    kwargs:        dict
    queue:         str

def build_beat_schedule(
    storage:       "Storage",                  # for active-item list
    rule_engine:   "RuleEngine",               # for resolve_polling_schedule_for_item
) -> dict[str, BeatScheduleEntry]:
    """Builds the full periodic schedule at startup (Ph-1 per D3 + D4 cascade 2026-06-23):
    - Per active DeliveryItem with `tracking_modality in {CorporatePLM, NSD, Email}`: schedule
      polling-ticker task at the interval returned by rule_engine.resolve_polling_schedule_for_item.
      **Ph-1 per D4 cascade**: rule_engine returns Global-tier YAML polling_schedule only -- no
      per-item Postgres override layer (FR-31 sub-2 deferred to Ph-2).
    - Global: deadline-evaluator ticker (every 5 min in Ph-1; configurable).
    - Global: FR-23 third-tier fallback owner-reply mailbox poll (configurable; default 30 s).
    Called by hilda-beat singleton at startup. **Ph-1 per D3 cascade: startup-only; no SIGHUP
    reload.** Returns the schedule dict Celery-beat consumes. Raises WFL-E003 on storage failure;
    WFL-W002 if any item has invalid polling_schedule (caller continues with baseline tier)."""

def reload_beat_schedule() -> None:
    """[Ph-2 forward-looking — deferred per D3 cascade 2026-06-23] SIGHUP handler -- re-runs
    build_beat_schedule and atomically swaps Celery-beat's schedule. Ph-1: NOT CALLED (service
    restart required for schedule changes -- acceptable per early drop scale)."""
```

### `tasks/*.py` — common task shape

```python
@hilda_celery_app.task(
    bind                  = True,
    queue                 = "default",                # overridable in task_routes
    autoretry_for         = (TransientError,),
    retry_backoff         = True,
    retry_backoff_max     = 300,                       # 5 min cap
    retry_jitter          = True,
    max_retries           = 5,
)
def some_action_task(self, params: dict, event_context: dict) -> dict:
    """Common task shape:
    - First arg `params` = RuleAction.params from the YAML rule definition
    - Second arg `event_context` = serialised TriggerEvent metadata (customer_id, milestone_id,
      delivery_item_id, correlation_id, timestamp) per [D-091] slug->id rename 2026-06-23
    - Returns a dict (passed to next task in chain via Celery canvas default behavior)
    - Raises TransientError for autoretry; PermanentError for terminal failure
    - On terminal failure, signal handler emits WFL-E0NN + writes CommunicationLog row with
      action_type=task_failed
    - All tasks must be idempotent — Celery retry semantics + at-least-once delivery may cause
      double-execution
    """
```

### `manual_trigger.py`

```python
def handle_manual_trigger(
    delivery_item_id:   str,
    action_kind:        ActionKind,
    params:             dict[str, Any],
    invoked_by_pm_id:   str,
    correlation_id:     str,
) -> str:
    """Per FR-31 sub-3: TPM SP UI manual trigger dispatch bypasses rule_engine.
    Looks up action_kind in ACTION_KIND_TO_TASK; calls task.apply_async with manual-trigger
    flag in event_context (downstream task body logs trigger_source=manual in CommunicationLog).
    Returns Celery task ID. Raises WFL-E001 if action_kind not in registry; WFL-E002 if
    action_kind not applicable to the item's current state (state-filter logic mirrors SP UI
    dropdown filter per FR-31)."""
```

### `polling/*.py` — periodic Celery tasks (fired by `hilda-beat`)

Each is a Celery task with `@hilda_celery_app.task(queue="periodic")` decorator. Beat schedule (built by `beat_schedule.build_beat_schedule`) enqueues these at deadline-tier-driven intervals per FR-23 / FR-26 / FR-55.

```python
# polling/plm_polling.py — per-item PLM polling (FR-26)
@hilda_celery_app.task(queue="periodic", autoretry_for=(TransientError,), max_retries=3)
def poll_plm_for_item(delivery_item_id: str) -> dict:
    """For each new owner-uploaded attachment found on PLM since last poll:
       (a) extract attachment metadata + download bytes
       (b) construct TriggerEvent(trigger=AttachmentReceived, sub_trigger="PLMSource", ...)
       (c) call workflow_engine.trigger_sources.plm_watch_source.fire(event)
       Returns dict {polled_at: datetime, new_count: int, item_id: str}.
       Beat schedule re-arms this task at the interval resolved by
       rule_engine.resolve_polling_schedule_for_item(item_id) per FR-31 sub-2."""

# polling/nsd_polling.py — per-TG NSD polling (FR-55)
@hilda_celery_app.task(queue="periodic", autoretry_for=(TransientError,), max_retries=3)
def poll_nsd_for_tg(milestone_id: str, tg_name: str) -> dict:
    """For each new file detected under \\\\NSD<N>\\<carrier>\\<device>\\<milestone>\\<Folder>\\
       (both NSD1 + NSD2 per `[D-013]` impl note 2026-05-28):
       (a) construct TriggerEvent(trigger=AttachmentReceived, sub_trigger="NSDSource", ...)
           with field_deltas carrying file_path + file_hash
       (b) call workflow_engine.trigger_sources.nsd_watch_source.fire(event)
       Returns dict {polled_at: datetime, new_count: int, tg_name: str}.
       Beat schedule re-arms per the TG's polling_schedule rule."""

# polling/owner_reply_polling.py — FR-23 third-tier fallback (IMAP IDLE primary; this is fallback)
@hilda_celery_app.task(queue="periodic", autoretry_for=(TransientError,), max_retries=2)
def poll_owner_replies() -> dict:
    """Short-interval poll of the HILDA dedicated mailbox when IMAP IDLE is unavailable
       (per `[D-047]` Consequences (3) — third-tier fallback used in environments where
       Exchange admin has disabled IDLE). Each new email is handed to email_service.parse
       which routes to the right trigger source (sp_alert_source for SP alerts;
       email_ingest_source for owner replies). When IMAP IDLE is active, this task is
       still scheduled but no-ops since email_service handles ingest via the IDLE
       connection. Returns dict {polled_at: datetime, new_count: int}."""

# polling/deadline_evaluator.py — global tick for time-based triggers
@hilda_celery_app.task(queue="periodic")
def evaluate_deadlines_tick() -> dict:
    """Global ticker that runs every WorkflowEngineConfig.beat_default_interval_s (5 min default).
       For each active DeliveryItem:
       (a) compute days_to_deadline = Milestone.target_date - today (per [D-085] — read from
           the Milestones SP list row; all items in a milestone share the same target_date;
           was per-item expected_completion_date pre-[D-085])
       (b) if rule(LastContactThreshold).condition(days_since_last_contact) crosses N → fire
           TriggerEvent(trigger=LastContactThreshold, ...) via scheduler_tick_source
       (c) if rule(DeadlineProximity).condition(days_to_deadline) crosses N → fire
           TriggerEvent(trigger=DeadlineProximity, ...) via scheduler_tick_source
       Returns dict {evaluated_count: int, triggers_fired: int}."""
```

### `trigger_sources/*.py` — TriggerEvent construction façades

Each source is a small module (typically 1–2 public functions) that knows how to construct a canonical `TriggerEvent` from its source-specific input and call `TriggerDispatcher.dispatch`. All sources share the same shape: validate inputs → build `TriggerEvent` → call `dispatcher.dispatch(event)` → emit per-source MET line.

```python
# trigger_sources/sp_alert_source.py — entry point for SP-alert email path per [D-047] / FR-84
def fire_from_sp_alert(parsed_alert: "SPAlertEvent") -> DispatchResult:
    """Called by email_service.sp_alert_parser after deterministic regex extraction of
       SP-alert email subject + body + action verb. Constructs TriggerEvent — trigger
       kind derives from the action verb (`modified` / `added` / `deleted`) + the
       changed field name (e.g., `Owner` field change → sub_trigger=OwnerReassigned;
       `TPM_Resolved_DocType` field set → sub_trigger=TPMResolvedDocType). Calls
       TriggerDispatcher.dispatch. Emits MET line with source=sp_alert + routing key.
       Raises WFL-E006 if action verb or field name not in the known FR-87 / FR-31
       / FR-14 set."""

# trigger_sources/email_ingest_source.py — entry point for owner-reply email path (FR-12)
def fire_from_owner_reply(parsed_reply: "OwnerReplyEvent") -> DispatchResult:
    """Called by email_service after FR-12 path (a) BATCH-id parser / path (b) mailto
       tap-link parser / path (c) rule-based-or-LLM message classifier produces an
       OwnerReplyEvent. Constructs TriggerEvent(trigger=OwnerStatusConfirmed, ...)
       with field_deltas carrying the parsed status mapping. Calls
       TriggerDispatcher.dispatch. Emits MET line with source=email_ingest + path tag."""

# trigger_sources/nsd_watch_source.py — entry point for NSD polling per FR-55
def fire_from_nsd_detection(milestone_id: str, tg_name: str, file_path: str,
                             file_hash: str, source_nsd: Literal["NSD1", "NSD2"]) -> DispatchResult:
    """Called by polling/nsd_polling.poll_nsd_for_tg per detected file. Constructs
       TriggerEvent(trigger=AttachmentReceived, sub_trigger=f"NSDSource_{source_nsd}",
       field_deltas={'file_path': (None, ...), 'file_hash': (None, ...)}).
       Calls TriggerDispatcher.dispatch. Emits MET line with source=nsd_watch + NSD tag."""

# trigger_sources/plm_watch_source.py — entry point for PLM polling per FR-26
def fire_from_plm_attachment(delivery_item_id: str, plm_attachment_id: str,
                              attachment_metadata: dict) -> DispatchResult:
    """Called by polling/plm_polling.poll_plm_for_item per new attachment. Constructs
       TriggerEvent(trigger=AttachmentReceived, sub_trigger='PLMSource', ...). Calls
       TriggerDispatcher.dispatch. Emits MET line with source=plm_watch."""

# trigger_sources/scheduler_tick_source.py — entry point for time-based triggers
def fire_scheduler_tick(trigger: TriggerKind, entity_ref: EntityRef,
                         field_deltas: dict[str, tuple[Any, Any]]) -> DispatchResult:
    """Called by polling/deadline_evaluator per threshold-cross detection. Generic
       constructor for any time-based TriggerEvent (LastContactThreshold,
       DeadlineProximity, others as added in Ph-2). Calls TriggerDispatcher.dispatch.
       Emits MET line with source=scheduler_tick + trigger kind."""

# trigger_sources/state_change_source.py — entry point for tracker state transitions
def fire_state_change(delivery_item_id: str, old_state: DeliveryState,
                       new_state: DeliveryState, trigger_correlation_id: str) -> DispatchResult:
    """Called by tracker.update_delivery_state after a transition successfully commits.
       Constructs TriggerEvent(trigger=StateChange, field_deltas={'delivery_state':
       (old_state, new_state)}, correlation_id=<chained from upstream>). Calls
       TriggerDispatcher.dispatch. Allows rules to subscribe to state transitions as
       a downstream trigger per [D-066] escape-hatch (b). Emits MET line with
       source=state_change + transition tag."""
```

### Configuration

```python
class WorkflowEngineConfig(BaseModel):
    """3-tier config per [D-025] + [D-038] (CLI > env > config/workflow_engine.json)."""
    broker_url:                  str                = "redis://hilda-redis:6379/0"
    result_backend_url:          str                = "db+postgresql://..."          # storage module's Postgres
    worker_concurrency_default:  int                = 4
    worker_concurrency_llm:      int                = 2
    worker_concurrency_browser:  int                = 1                              # per-host; per-carrier session pool dominates
    beat_default_interval_s:     int                = 300                            # deadline-evaluator tick
    owner_reply_poll_interval_s: int                = 30                             # FR-23 third-tier fallback
    task_default_max_retries:    int                = 5
    task_default_retry_backoff_max_s: int           = 300
```

---

## Worked example — FR-87 TPM "Resolve doc_type" round-trip

Showing the full SP UI → workflow_engine → SP-back path for FR-87 + `[D-064]` / `[D-047]`:

1. TPM clicks **Resolve doc_type → test_report** button on item `I-1234` in SP UI
2. SP UI writes `tpm_resolved_doc_type = "test_report"` to SP list (canonical column name addressed by `customizations/sharepoint_config/customers/<customer_id>.yaml` per [D-091] slug->id rename)
3. SP fires alert email (Anything-changes config per `[D-047]`); email lands in HILDA mailbox
4. `email_service.sp_alert_parser` parses subject + body + action verb (`modified`); constructs canonical `TriggerEvent`:
   ```python
   TriggerEvent(
       trigger="ItemModified",
       sub_trigger="TPMResolvedDocType",            # synthetic sub-trigger for FR-87 path
       entity_ref=EntityRef(..., delivery_item_id="I-1234"),
       field_deltas={"tpm_resolved_doc_type": (None, "test_report")},
       correlation_id="evt-xyz789",
   )
   ```
5. `sp_alert_parser` calls `workflow_engine.trigger_sources.sp_alert_source.fire(event)`
6. `TriggerDispatcher.dispatch(event)`:
   - calls `rule_engine.evaluate(event)`
   - assume a rule `handle_tpm_doc_type_resolution` matches with actions `[ReassignDocumentToWorkItem, TriggerAIReview]`
   - builds chain: `tracker.reassign_document.s({...}, ctx) | llm.review_document.s({...}, ctx)`
   - `apply_async(queue="default")` → returns task IDs
7. `hilda-worker` picks up `tracker.reassign_document`; updates DB; writes `CommunicationLog` row (via `sharepoint_integration.SpCrud.create_item` per FR-42); writes `delivery_state` writeback via `SpCrud.update_item` per `[D-064]`
8. On success → next chain task fires: `llm.review_document` runs on `llm_calls` queue
9. SP UI's focus-aware refresh (per `[D-064]` Consequences) picks up the writes on next poll-on-focus → TPM sees Resolved-doc_type + reassignment landed + AI review in progress

All 6 trigger sources follow the same shape: source-specific event construction → `TriggerDispatcher.dispatch()` → independent Celery chains per `RuleMatch` per `[D-066]`.

---

## Invariants

- **Celery + Redis broker + Postgres result backend per `[D-022]`/`[D-043]`** — Ph-1/Ph-2. Ph-3+ broker swap to RabbitMQ is a config change only (broker_url) — no task code change. Result backend stays Postgres across phases.
- **`hilda-beat` is a singleton** per `[D-022]` — exactly one beat instance per deployment. Two beats would double-fire periodic tasks. Enforced by the orchestrator (Docker Compose service replicas=1 Ph-1/Ph-2; K8s Deployment replicas=1 with leader-election Ph-3+).
- **Per-`RuleMatch` independent Celery chains per `[D-066]`** — `TriggerDispatcher` builds ONE chain per `RuleMatch`. Multiple `RuleMatch`es from one `TriggerEvent` produce multiple independent chains scheduled with no guaranteed cross-chain order. Intra-chain ordering = YAML declaration order via Celery `chain()` canvas.
- **FR-31 sub-1 pause is respected at dispatch time via `item.rules_paused`** (revised 2026-06-23 per D2 + D5 cascade) — `TriggerDispatcher` fetches the item snapshot from storage before calling `rule_engine.evaluate(event, item_snapshot=item)`; rule_engine reads `item.rules_paused: bool` and marks all matches `pause_state="paused"` when True; workflow_engine then LOGS (WFL-W001) but does NOT schedule paused matches. Pause is consulted at dispatch time, not at task-run time, so already-enqueued tasks for an item paused mid-flight will still run (acceptable per FR-31 semantics — pause prevents new task scheduling, not in-flight execution). PauseStateLookup Protocol from earlier draft DROPPED.
- **FR-31 sub-3 manual triggers bypass `rule_engine` entirely** — `manual_trigger.handle_manual_trigger` looks up the `ActionKind` directly in `ACTION_KIND_TO_TASK` and calls `apply_async`. The task's event_context carries `trigger_source="manual"` so downstream `CommunicationLog` writes are correctly attributed per FR-31.
- **All tasks must be idempotent** — Celery's at-least-once delivery + worker crash + retry semantics may cause double-execution. Tasks that mutate state must check current state before applying (typically: read DB row, check if mutation already applied, no-op if so). Task bodies are responsible for their own idempotency; workflow_engine does not enforce it.
- **No business logic in workflow_engine itself** — task bodies delegate to the owning module (email_service, customer_adapter, etc.). Workflow_engine owns Celery scaffolding, dispatch, and a handful of cross-module orchestration tasks (`START_ITEM_COLLECTION`, `QUEUE_SUBMISSION`, `NOTIFY_PM`, `NOTIFY_HILDA_OPS`, `HALT_MILESTONE_POLLING`, `FINAL_SWEEP`, `REARM_DEADLINE_PROXIMITY`); everything else is a thin wrapper that delegates.
- **Task return values are part of the Celery canvas chain interface** — each task's return value is passed as the first positional arg to the next task in the chain (Celery default). Task signatures must be designed for chaining: accept `(params, event_context, previous_result=None)`.
- **No proprietary content in compact reports or error codes** per NFR-2 / `[D-002]` — WFL-* codes emit task name, queue, latency bucket, retry count, error class — never the email body content, the test report contents, customer-data values, or proprietary identifiers.
- **No direct SP REST writes from workflow_engine** — all SP writes go through `sharepoint_integration.SpCrud` per `[D-064]`. workflow_engine task bodies that need to write to SP call into the right downstream module (typically `tracker.update_delivery_state` for state writebacks, `email_service.log_communication` for CommunicationLog rows).
- **No direct credential reads from workflow_engine** — all credential lookups go through `credential_service.get_credential` per `[D-019]`; task bodies pass `pm_id` to downstream modules which fetch their own credentials. workflow_engine never sees credential material.
- **6 trigger sources only** per Ph-1 — `sp_alert_source`, `email_ingest_source`, `nsd_watch_source`, `plm_watch_source`, `scheduler_tick_source`, `state_change_source`. Adding a new trigger source is an architecture-phase event (new sub-module + Public surface review).
- **Celery-beat schedule is rebuildable on SIGHUP** *(Ph-2 — deferred per D3 cascade 2026-06-23; Ph-1 = startup-only build)* — Ph-2: operational changes to per-item polling cadence (FR-31 sub-2 Postgres override) take effect without restart via `reload_beat_schedule()`. Ph-1: build at hilda-beat startup only; schedule changes require service restart (acceptable per early drop scale; FR-31 sub-2 deferred to Ph-2 per D4 cascade).

---

## Error codes (WFL prefix — registered in `diagnostics/error_codes.py`)

```
WFL-E001  ActionKind '{action_kind}' has no TaskBinding in ACTION_KIND_TO_TASK registry — registry incomplete; ops triage
WFL-E002  Manual trigger '{action_kind}' not applicable to item '{item_id}' state '{state}' — FR-31 sub-3 state-filter rejection
WFL-E003  Celery-beat schedule build failed: storage error '{reason}' — beat singleton cannot start
WFL-E004  Celery broker connection failure: {reason} — task enqueue fails
WFL-E005  Task '{task_name}' terminal failure after {retries} retries on item '{item_id}': {exc_class} — manual triage
WFL-E006  Trigger source '{source}' constructed malformed TriggerEvent (missing required field '{field}') — caller bug; dispatcher rejects
WFL-W001  RuleMatch for rule '{rule_id}' on item '{item_id}' skipped (FR-31 sub-1 pause active)
WFL-W002  Polling-schedule for item '{item_id}' invalid (RUL-W004 from rule_engine); using baseline default {minutes}min
WFL-W003  Task '{task_name}' retried (attempt {n}/{max}) on item '{item_id}' — transient {exc_class}
WFL-W004  Celery worker concurrency saturated on queue '{queue}'; task '{task_name}' queue depth {n} — capacity flag
WFL-W005  Beat singleton skew detected: two beat instances running (multi-fire risk) — ops triage
```

---

## Key choices

- **`[D-022]`** — workflow_engine = Celery app + dispatcher; rule_engine = pure evaluator. This boundary keeps Celery semantics (retries, queues, signal handlers) out of rule_engine. Reversing the boundary would re-fold dispatch into rule_engine and make rule_engine stateful — rejected during rule_engine drafting (2026-06-10 architect decision).
- **`[D-066]`** — per-`RuleMatch` independent Celery chains; no cross-rule ordering. Captured in rule_engine's ADR; consumed here as the dispatcher contract.
- **`[D-043]`** — Redis broker Ph-1/Ph-2; RabbitMQ Ph-3+. Redis chosen for Ph-1 because: (a) simpler ops surface (no separate broker config); (b) same Redis instance can serve as cache for unrelated needs; (c) result backend is Postgres so broker reliability is bounded — task results persist regardless of broker uptime. RabbitMQ deferred to Ph-3+ when production throughput demands per-task ack semantics + dead-letter queues.
- **4 queues by latency profile** (architect decision 2026-06-10 — captured here, no separate ADR): `default` / `llm_calls` / `browser_automation` / `periodic`. Alternative considered: one queue with priority levels — rejected because Celery's priority semantics on Redis broker are fragile (Redis lacks true priority queue; Celery emulates via multiple Redis lists, which loses message order). Multiple queues = simpler ops mental model + clean per-queue concurrency tuning. Adding a new queue is a config change + per-task decorator update.
- **`hilda-beat` singleton enforced by orchestrator** (architect decision 2026-06-10) — Celery-beat does NOT support active-active multi-instance natively. Singleton enforcement is the orchestrator's job (Docker Compose `replicas=1` Ph-1/Ph-2; K8s Deployment + leader-election Ph-3+). Rejected alternatives: (α) `celerybeat-redis-lock`-style distributed locking — adds complexity; risk of split-brain if lock TTL misconfigured; (β) accept multi-fire and dedupe at task level — pushes complexity to every task body. Singleton is the cleanest model for Ph-1/Ph-2 scale.
- **Task bodies are thin wrappers; business logic lives in owning modules** — workflow_engine owns Celery scaffolding + 7 cross-module orchestration tasks (START_ITEM_COLLECTION, QUEUE_SUBMISSION, NOTIFY_PM, NOTIFY_HILDA_OPS, HALT_MILESTONE_POLLING, FINAL_SWEEP, REARM_DEADLINE_PROXIMITY); everything else delegates. Rejected alternative: workflow_engine implements all task bodies in-line — would balloon the module + force every downstream module's logic to leak here. The delegation pattern keeps module boundaries intact.
- **6 trigger sources, not 1 generic event bus** — each trigger source is a small façade module that knows how to construct the canonical `TriggerEvent` from its source-specific input (SP-alert email format, owner-reply email parse, NSD file metadata, scheduler tick context, etc.). Rejected alternative: a single `submit_event(raw_payload, source_type)` generic endpoint — would push event-construction logic into every caller and lose per-source observability. Each source emits its own MET line.
- **`event_context` is JSON-serialisable; passed through every task in the chain** — correlation_id, customer_id, milestone_id, delivery_item_id, timestamp (per [D-091] slug->id rename 2026-06-23). Celery serialises task args; complex objects would force a custom serializer. The flat dict keeps the contract simple and observable in Celery logs.

---

## Non-goals

- **Not a rule evaluator** — `rule_engine` owns the evaluation logic per `[D-022]`. workflow_engine just consumes `RuleMatch`es and schedules chains.
- **Not a state-machine enforcer** — `tracker` owns the 11-state `delivery_state` machine + transition guards. workflow_engine's `UpdateState` task delegates to `tracker.update_delivery_state` which enforces FR-7 / FR-28 guards.
- **Not a credential store** — `credential_service` per `[D-019]` / `[D-038]` / `[D-052]`.
- **Not an action executor** — workflow_engine schedules; the action bodies live in downstream modules. The 7 cross-module orchestration tasks workflow_engine owns are coordination wrappers, not domain logic.
- **Not a Celery-beat replacement** — uses upstream Celery-beat as the periodic scheduler; doesn't reinvent the cron logic. Custom logic limited to `build_beat_schedule()` + `reload_beat_schedule()`.
- **Not a dashboard / UI surface** — `dashboard` (Layer 4) owns the PM/TPM/ops UI; workflow_engine emits MET/RPT lines that dashboard consumes but doesn't render them itself.
- **Not a manual-trigger SP UI** — SP UI engineer owns the Trigger Action dropdown (FR-31 sub-3) and the FR-87 TPM resolution buttons; workflow_engine receives the resulting SP-alert email and dispatches. The UI is corp-side, not HILDA-side.
- **Not the SP REST writer** — `sharepoint_integration.SpCrud` per `[D-064]`. workflow_engine tasks call into the right downstream module which calls SpCrud.
- **Not a workflow-orchestration DSL** — no Airflow-style DAGs, no Temporal workflows, no step functions. Per `[D-022]` Ph-1 = Celery chains via canvas. Future Ph-3+ may revisit if multi-step durable orchestration emerges as a need.
- **Not the LLM gateway** — `llm` module owns the tri-backend gateway per `[D-052]`/`[D-059]`. workflow_engine's `TRIGGER_AI_REVIEW` task calls `llm` as a client.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate`, `register_code` (WFL-* codes registered).
- `template_schema` — `DeliveryItemBase`, `AutomationRuleBase`, `DeliveryState` enum (for state-filter logic in `manual_trigger.handle_manual_trigger`).
- `rule_engine` — `RuleEngine.evaluate(event, item_snapshot=...)` per D2 + D12 cascade 2026-06-23 (PauseStateLookup Protocol DROPPED — pause check now reads `item.rules_paused` directly via item_snapshot), `TriggerEvent`, `RuleMatch`, `ActionKind`, `TriggerKind`, `evaluate_polling_schedule`, `resolve_polling_schedule_for_item` (Ph-1 returns Global-tier YAML polling_schedule only — FR-31 sub-2 Postgres override layer Ph-2 per D4 cascade). Consumed by `TriggerDispatcher` + `build_beat_schedule`.
- `storage` — Postgres for Celery result backend + Redis client for broker; storage's `read_file` + `log_communication` consumed by tasks; `list_active_items` for beat schedule construction.
- `credential_service` — passed through to downstream module task bodies; workflow_engine never reads credentials directly.
- `sharepoint_integration` — task bodies that need to write to SP go through `SpCrud` (typically via `tracker` or `email_service`).
- `email_service` — `sp_alert_parser` calls workflow_engine's `sp_alert_source` to construct TriggerEvents; `email_service.send_*` Celery tasks are imported and registered in `ACTION_KIND_TO_TASK`.
- `customer_adapter` — `upload_attachment` invoked from `QueueSubmission` task body.
- `issue_tracker` — `create_issue` invoked from `START_ITEM_COLLECTION` task body.
- `messenger` — `escalate_message` invoked from `ESCALATE` task body.
- `llm` — `review_document` invoked from `TRIGGER_AI_REVIEW` task body.
- `test_report` — `run_parser` invoked from `TRIGGER_PARSER` task body.
- `tracker` — `update_delivery_state` / `instantiate_default_workitem` / `reassign_document` / `propagate_tags` invoked from corresponding task bodies; consumed bidirectionally (workflow_engine schedules tracker; tracker fires `StateChange` TriggerEvents back via `state_change_source`).
- `celery` (3rd party) — Celery 5.x app + canvas + beat + signals.
- `redis` (Ph-1/Ph-2 — 3rd party) — broker.

---

## Depended on by

- `tracker` (Layer 3) — calls workflow_engine to enqueue tasks (e.g., post-state-change re-evaluation); imports the Celery app for `apply_async`.
- `dashboard` (Layer 4) — surfaces WFL-RPT / MET lines for PM/ops dashboards.
- `email_service` — imports workflow_engine's `sp_alert_source.fire(event)` from `sp_alert_parser` after parsing an SP-alert email; email_ingest_source for owner-reply paths.
- `hilda-api` (workload) — calls `apply_async` to enqueue tasks from API endpoint handlers (e.g., the `/dispatch/manual` endpoint backing FR-31 sub-3 SP UI button clicks — note: in practice FR-31 sub-3 round-trips through SP-alert email per `[D-064]`, not direct hilda-api; this entry covers other API-triggered tasks like FR-73 carrier-package assembly trigger).

---

## Deferred (Ph-2 / Ph-3+)

- **Ph-2 ActionKind tasks** (not in Ph-1 `ACTION_KIND_TO_TASK` registry; added when Ph-2 lands): `CancelOutstanding`, `NotifyOwnerDocCountPending`, `TriggerVersionSelection`, `TriggerPLMCleanup`, `TriggerODF`, `SendOwnerRoutingQuery`. Each adds a TaskBinding entry + a task body delegating to its owning module.
- **Ph-2 trigger sources**: `corp_messenger_ingest_source` (owner replies via corp messenger per FR-54 Ph-2 LLM classification path); `customer_jira_poll_source` (CustomerJIRA closure polling per FR-25).
- **Ph-2 per-rule pause-state lookup** — currently FR-31 sub-1 pause is all-rules-or-none per item (read via `item.rules_paused` SP column per D5 cascade 2026-06-23); Ph-2 adds per-rule pause (separate `item.rule_pauses: dict[str, bool]` field OR `is_rule_paused(delivery_item_id, rule_id)` lookup) consumed by `TriggerDispatcher`.

- **Ph-2 SIGHUP Celery-beat schedule reload** per D3 cascade 2026-06-23: `reload_beat_schedule()` becomes callable; ops can edit YAML / restart hilda-beat workers without service-wide restart. Ph-1 = startup-only build; YAML or rule changes require hilda-beat restart.

- **Ph-2 FR-31 sub-2 Postgres polling-override consumption in beat_schedule** per D4 cascade 2026-06-23: `build_beat_schedule` becomes aware of `AutomationRuleOverride` Postgres rows for per-item polling cadence overrides (rule_engine's `resolve_polling_schedule_for_item` Ph-2 returns Postgres-overridden tiers). Ph-1 = Global YAML tiers only.
- **Ph-2 carrier-package async assembly with progress feedback** per FR-73 — currently Ph-1 is two-click (Download then Submit), Ph-2 may add a long-running carrier-package assembly task with progress endpoint via hilda-api per SP REQUIREMENTS §8.2.
- **Ph-3+ RabbitMQ broker** per `[D-043]` — broker_url config change only; task code unchanged. Triggers per-task ack semantics + dead-letter queues.
- **Ph-3+ Temporal-style durable workflow orchestration** per `[D-022]` Consequences — if multi-step long-running workflows with checkpoint resume become a need (e.g., multi-day FR-66 multi-revision selection workflow), revisit Celery chains vs Temporal vs similar. Deferred until proven need.
- **Ph-3+ distributed tracing** per `[D-023]` — Ph-1 light observability stack (stdout JSON logs + Prometheus `/metrics` + compact reports). Ph-3+ may add OpenTelemetry tracing across workflow_engine → rule_engine → downstream modules for cross-module latency analysis.
- **Ph-3+ priority queues** — currently 4 queues by latency profile; Ph-3+ may add per-customer priority weights if multi-customer SLA differentiation emerges. Defer until proven need.
- **Ph-3+ leader-election for `hilda-beat`** — currently singleton enforced by orchestrator (Docker Compose `replicas=1` Ph-1/Ph-2). Ph-3+ K8s Deployment may use leader-election sidecar (e.g., `k8s.io/client-go/tools/leaderelection`) for HA beat.
- **Ph-3+ rule-execution telemetry stream** — aggregated per-rule metrics consumed by ops dashboard for tuning (which rules fire most; which tasks retry most). Defer until ops scale demands.

---

## Test interface

```
python -m core.src.workflow_engine.workflow_engine_cli --diagnostic
```
Validates Celery app config + broker connectivity + result backend connectivity + per-queue concurrency settings + ACTION_KIND_TO_TASK registry completeness against `rule_engine.ActionKind`. Emits WFL-RPT:
```
RPT|WFL|run-00001|2026-06-10T10:00:00Z|broker_url=redis://hilda-redis:6379/0|result_backend=postgres|queues=default,llm_calls,browser_automation,periodic|action_kinds_registered=18|action_kinds_expected=18|registry_complete=true|beat_singleton_check=pass|trigger_sources=6
```

```
python -m core.src.workflow_engine.workflow_engine_cli --queue-stats
```
Reports per-queue depth, active task count, recent latency buckets, retry rate. Emits WFL-MET. Safe to run anytime; read-only against the broker.

```
python -m core.src.workflow_engine.workflow_engine_cli --replay-event --event-json <path>
```
Loads a serialised TriggerEvent from JSON; runs `TriggerDispatcher.dispatch` in dry-run mode (no `apply_async`); emits WFL-RPT showing which RuleMatches would fire + which Celery chains would build. For ops debugging "why did rule X fire (or not) on event Y?".

```
python -m core.src.workflow_engine.workflow_engine_cli --beat-schedule
```
Prints the current Celery-beat schedule (per-item polling tickers + global deadline-evaluator tick + FR-23 third-tier fallback) with intervals + queues. For ops verification of polling-schedule overrides post FR-31 sub-2.

**QC template** (`WFL:dispatch_quality` — registered in `diagnostics/qc.py`):
```
Fields: trigger_source (enum: sp_alert / email_ingest / nsd_watch / plm_watch / scheduler_tick / state_change),
        matched_count (int),
        scheduled_count (int),
        paused_skipped_count (int),
        dispatch_latency_bucket (enum: fast / normal / slow / timeout),
        task_failure_rate (float),
        beat_singleton_ok (bool),
        result (enum: OK / WARN / FAIL — FAIL when dispatch errors > threshold; WARN when paused-skipped count > 50%)
```

---

<!-- BEGIN:STRUCTURE -->

- `DispatchResult` — class — pub — Outcome of one TriggerDispatcher.dispatch() call.
- `RetryPolicy` — class — pub — Per-task retry policy. workflow_engine sets sensible defaults; tasks can
- `StorageLike` — class — pub — Minimal storage Protocol for TriggerDispatcher -- fetches item snapshot for
- `TaskBinding` — class — pub — Maps one ActionKind to its Celery task callable + queue + retry policy.
- `TaskDeps` — class — pub — Bundle of runtime dependencies task bodies need. Constructed once at worker
- `TriggerDispatcher` — class — pub — Receives TriggerEvent from any of the 6 trigger sources; resolves matches
- `WorkflowEngineConfig` — class — pub — Operational values per workflow_engine/MODULE.md. Per Ph-1 D3 + D4 cascade
- `build_celery_app` — func — pub — Build a Celery app from a WorkflowEngineConfig. Default singleton constructed
- `build_chain_from_rule_match` — func — pub — Per [D-066]: build one Celery chain from a RuleMatch's ordered actions list.
- `expected_action_kinds_ph1` — func — pub — The full Ph-1 ActionKind set per rule_engine.ActionKind enum. Used by
- `get_task_deps` — func — pub — Return the current TaskDeps. Raises if not initialised -- production worker
- `handle_manual_trigger` — func — pub — Per FR-31 sub-3: TPM SP UI manual trigger dispatch bypasses rule_engine.
- `lookup_for_manual_trigger` — func — pub — Per FR-31 sub-3 TPM SP UI manual trigger dispatch -- bypasses rule_engine.
- `override_task_deps` — func — pub — Context manager for tests -- swap deps, restore on exit.
- `register_task_binding` — func — pub — Register a TaskBinding. Idempotent for identical re-registration; raises
- `registry_complete` — func — pub — True iff every ActionKind in rule_engine.ActionKind has a TaskBinding.
- `set_task_deps` — func — pub — Install the runtime TaskDeps bundle. Idempotent re-set is allowed.

<!-- END:STRUCTURE -->
