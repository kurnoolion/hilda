# Module: tracker

> **Status:** Initial draft 2026-06-10 + 2026-06-23 architect cascade revisit applied (13 drift items D1-D18 against recent locks since 2026-06-10: slug→id rename per `[D-091]`, 4-field owner identity per `[D-080]`+`[D-086]`, FR-78 default WI hardcoded inventory expansion, item_type case cleanup per SP UI engineer lock 2026-06-23, FR-82 nested tag-set per architect lock 2026-06-20, Projects-per-customer per NFR-21 amendment + `[D-083]`, FR-64 Option (b) HILDA-owned cascade, Confirmation FR-25 (b) role-collapse skip-pattern, `expected_completion_date` removal per `[D-085]`, FR-87 step (A)(B)(C) clarification). DeliveryItem lifecycle orchestrator — owns the 11-state `delivery_state` machine + per-transition guards per FR-7 / FR-28. Pure library; no Celery, no SP REST writes (delegates to `sharepoint_integration`), no rule evaluation (delegates to `workflow_engine`'s downstream `StateChange` dispatch). Sections curated; code implementation begins after `/switch-phase development tracker`.
>
> **Rollback log:**
> - **2026-06-23 (architect cascade revisit — 13 drift items applied against recent locks since 2026-06-10)** — strict-order module-by-module sweep Module #6 of 13 (per architect direction 2026-06-21). **D1 — slug → id rename per `[D-091]`**: `customer_slug` → `customer_id`, `device_slug` → `device_id`, `inferred_tg_name_slug` → `inferred_tg_path_id` (7 sites: `instantiate_default_workitem` params + `TagPropagationResult` + `propagate_tags_to_active_trackers` params + `StorageWriter.find_items_by_natural_key` Protocol signature + docstrings). **D2 — `owner_email` → 4-field owner identity per `[D-080]` + `[D-086]`**: `sp_alert_parser` routing description updated — `owner_corp_usa_email` / `owner_corp_email` / `owner_corp_id` / `owner_name` field changes route to `ItemModified.OwnerReassigned` (was single `owner_email`). PLM grouping key is `owner_corp_id` per FR-5 + `[D-035]`. **D3 — FR-78 default WI hardcoded inventory expansion**: `instantiate_default_workitem` docstring expanded with full 11-field inventory per FR-78 lock (`tg_path_id="_unrouted"`, `item_path_id=None`, `force_tracking_enabled=False`, `owner_corp_usa_email=None`, `owner_corp_email=None`, `owner_corp_id=None`, `owner_name=None`, `tracking_modality=None`, `milestone_gating=True`, `no_customer_upload=True`, `review_required=False`, `doc_count=0`, `item_type="Default"`, `delivery_state=Open`). **D4 — item_type literal cleanup**: SCREAMING enum names (`TEST_TECH_WAIVER_REPORT`, `COMPLIANCE_CERTIFICATION_RELEASE_NOTES`) replaced with snake_case literal values per FR-7 / `[D-053]` (the enum NAMES remain SCREAMING per PEP 8; the VALUES are snake_case). `Confirmation` + `Default` stay PascalCase per SP UI engineer lock 2026-06-23. **D5 — FR-82 nested tag-set per architect lock 2026-06-20**: `propagate_tags_to_active_trackers.new_tags` type `list[str]` → `list[list[str]]` (nested synonym groups); `TagPropagationResult.new_tags` same type change; docstring + dedup-at-write-time semantics updated for nested structure. **D6 — Projects-per-customer per NFR-21 amendment + `[D-083]`**: anchor added to Purpose + Status header. **D7 — FR-64 Option (b) HILDA-owned per-item cascade**: anchor add to Purpose + Status header (cascade implementation at lines 556-589 already aligns — Close All Items batch iterates per-item via `tracker.update_delivery_state`; matches Option (b) semantic). **D10 — Status header refresh (this entry)**. **D11 — Anchors update**: Purpose adds `[D-080]`, `[D-083]`, `[D-086]`, `[D-091]`; D-DRAFT candidates `D-094` (item_type rename + 2026-06-23 mixed-case lock) and `D-100` (FR-64 Option (b)) noted. **D12 — Confirmation FR-25 (b) skip-pattern**: state machine narrative updated — Confirmation items (`item_type = "Confirmation"`) skip `DocumentReceived` + `OwnerClosed` per FR-7 confirmation carve-out + FR-25 (b) role-collapse lock 2026-06-19; advance `OutreachSent → UnderPMReview` directly on close-intent reply. **D13 — `previous_active_state` → `prior_delivery_state` field name** (verified consistency with `template_schema.DeliveryItemBase.prior_delivery_state`). **D14 — `expected_completion_date` removed per `[D-085]`**: `ItemModified.DeadlineMoved` sub-trigger now sources from `Milestone.target_date` edit on the Milestones SP list row (was per-item `expected_completion_date`); sp_alert_parser routing description updated. **D15 — `actual_item_info` field confirmed exists** (PLM URL per architect direction 2026-06-23) — kept in `apply_manual_tpm_override` field list. **D18 — FR-87 step (A)(B)(C) clarification**: `reassign_document_to_workitem` docstring clarified to scope step (A) (TPM reassignment from default WI to non-default item); FR-86 alignment matrix enforcement covers step (B) (doc_type-misclassification resolution); `[D-039]` Steps 0-1 fire during reassignment but Step 2 LLM + Step 3 staged remain Ph-2 per architect direction 2026-06-21 (CLASSIFY_DOC Ph-2 demotion).
> - **2026-06-10 (initial draft)** — first MODULE.md for `tracker`. Scope locked to **Ph-1 only**: 11-state `delivery_state` machine (Not Started + 8 active happy-path + Delayed + Blocked off-path); per-transition guards per FR-7 / FR-28 (OwnerClosed 2-condition guard; Confirmation-item carve-out; PMApproval gate per NFR-5); FR-78 default work-item instantiation; FR-82 cross-tracker tag propagation; FR-83 TPM-manual document reassignment; FR-14 manual TPM field overrides with `bypass_guards` discipline; idempotent transitions for Celery at-least-once retry safety. Ph-2 (corp-messenger version-selection FR-66 reflected in transition map + `## Deferred`). Anchors `[D-022]` (workflow_engine consumes tracker; rule_engine does not depend on tracker), `[D-064]` (state writebacks via `sharepoint_integration.SpCrud` per FR-84), `[D-066]` (`StateChange` is the canonical downstream trigger; tracker returns the dispatch signal but does NOT call workflow_engine — caller fires), `[D-053]` (4-value `ItemType` + Confirmation carve-out path), `[D-060]` (default work-item per-MILESTONE; FR-78 instantiation site), `[D-005]` (`--diagnostic` + `--validate` CLI), `[D-002]` (TRK-* error codes + RPT/MET/FIX/QC compact reports + no-proprietary-content invariant). New error code prefix `TRK` already in `diagnostics.PREFIX_REGISTRY`.

**Purpose**: `tracker` is HILDA's **DeliveryItem lifecycle orchestrator**. It owns: (a) the **11-state `delivery_state` machine** (`Not Started`, `Open`, `OutreachSent`, `DocumentReceived`, `OwnerClosed`, `UnderPMReview`, `ReadyForSubmission`, `SubmittedToCustomer`, `Closed`, `Delayed`, `Blocked`) — the canonical transition matrix + per-transition guard predicates per FR-7 + FR-28; (b) the four **cross-cutting orchestration actions** that `workflow_engine` consumes: `update_delivery_state` (`UPDATE_STATE` action target), `instantiate_default_workitem` (FR-78 / `INSTANTIATE_DEFAULT_WORK_ITEM`), `reassign_document_to_workitem` (FR-83 / `REASSIGN_DOCUMENT_TO_WORK_ITEM`), `propagate_tags_to_active_trackers` (FR-82 / `PROPAGATE_TAGS_TO_ACTIVE_TRACKERS`); (c) the **`StateChangeDispatchSignal`** that callers (workflow_engine task bodies) consume to fire the canonical `StateChange` TriggerEvent per `[D-066]` downstream-trigger pattern. **No Celery, no rule evaluation, no SP writes of its own** — tracker is a pure library. Side effects (DB writes, SP REST writebacks, audit-log writes, follow-on trigger dispatches) are mediated through injected Protocols (`StorageWriter`, `SpWriter`, `AuditWriter`) so unit tests are mock-friendly and dependency cycles are avoided. Serves FR-2 (item lifecycle init), FR-7 (state machine + transition guards), FR-14 (manual TPM field override path), FR-18 (state advancement to `SubmittedToCustomer` on carrier upload success), FR-28 (OwnerStatusConfirmed 2-condition guard, PMApproval gate, MilestoneAllClosed eligibility), FR-31 sub-1 (pause check on automated transitions; sub-3 manual bypasses guards via explicit `bypass_guards` flag), FR-42 (CommunicationLog row per state transition), FR-52 / FR-83 (document-to-work-item reassignment via TPM SP UI; FR-87 step (A) TPM resolution path), FR-64 (per-item Close All Items cascade per Option (b) HILDA-owned lock 2026-06-20), FR-78 (default work-item per milestone with hardcoded inventory per architect lock 2026-06-21), FR-82 (cross-tracker tag propagation with nested tag-set per architect lock 2026-06-20). Anchors `[D-022]` (rule_engine boundary — tracker depends on neither rule_engine nor workflow_engine; consumers fire the StateChange trigger), `[D-064]` (state writebacks via `SpCrud`), `[D-066]` (StateChange as downstream trigger primitive), `[D-053]` (4-value ItemType + Confirmation carve-out + mixed-case per SP UI engineer lock 2026-06-23), `[D-060]` (default work-item per-MILESTONE), `[D-080]` (4-field owner identity preference rule for outreach), `[D-083]` (Projects-per-customer architecture; tracker reads `assigned_pm_id` via per-customer `Projects_<customer_id>` SP list lookup per NFR-21 amendment), `[D-085]` (Milestone.target_date sole authoritative deadline; per-item `expected_completion_date` removed), `[D-086]` (free-form text owner identity discipline), `[D-088]` (3-tuple `(assigned_pm_id, pm_display_name, pm_email)` PM resolution from Projects-list TPM Person/Group column), `[D-091]` (slug → id rename throughout: `customer_slug`→`customer_id`, `device_slug`→`device_id`, `inferred_tg_name_slug`→`inferred_tg_path_id`), `[D-005]`, `[D-002]`. D-DRAFT candidates `D-094` (item_type rename + 2026-06-23 mixed-case lock), `D-100` (FR-64 Option (b)) — pending ADR ratification.

**Workload assignment**: Library code imported by `hilda-worker` (task bodies for all 4 cross-cutting actions) + `hilda-api` (read-only state queries from REST endpoints; never invokes mutating functions from the API side). No standalone Deployment, no Celery worker, no scheduled tasks of its own.

**Per-transition latency target**: <20 ms (DB read + guard evaluation + DB write + audit log write); 95th-percentile <100 ms under contention. Transitions are synchronous; the calling Celery task body waits for the result.

---

## Sub-modules

```
core/src/tracker/
  __init__.py
  state_machine.py                  ← DeliveryState transition matrix (immutable global); transition_legal(from, to) predicate
  guards.py                         ← per-transition guard predicates: pure functions of (item snapshot, target_state) → GuardResult
  transitions.py                    ← public transition functions: update_delivery_state, advance_on_doc_count_reached, mark_owner_closed_via_status_confirmed, etc.
  default_workitem.py               ← FR-78 instantiation + per-milestone idempotency
  reassignment.py                   ← FR-83 document-to-work-item reassignment + re-derive doc_type per FR-85 + re-run FR-77 routing
  tag_propagation.py                ← FR-82 cross-tracker tag propagation (idempotent; audit-logged per propagation hop)
  manual_override.py                ← FR-14 path: TPM manual field write via SP-alert email → bypass_guards transition with explicit attribution
  protocols.py                      ← StorageWriter / SpWriter / AuditWriter dependency-injection Protocols (decouples tracker from sharepoint_integration / storage / workflow_engine concrete classes)
  diagnostics_cli.py                ← --diagnostic / --validate / --explain-transition modes
  tracker_cli.py                    ← user-facing wrapper for ops debugging
  tests/
  MODULE.md                         ← this file
```

---

## Public surface

### `state_machine.py`

```python
class DeliveryState(str, Enum):
    """Canonical 11-value enum per FR-7. Imported from template_schema; re-exported here for
    convenience. The active happy-path 8-state traversal is Open → OutreachSent →
    DocumentReceived → OwnerClosed → UnderPMReview → ReadyForSubmission → SubmittedToCustomer →
    Closed. **Not Started** = TPM has loaded the customer template into SP and the item row
    exists, but HILDA tracking has not yet been kicked off (no outreach, no polling, no rule
    evaluations); HILDA stays passive until TPM clicks "Start Collection" per FR-8 / FR-2 which
    transitions Not Started → **Open**. **Open** = HILDA has started active tracking for the
    item (rule evaluations + scheduled polling + outreach actions are now armed); outreach has
    not yet been sent (sending advances to OutreachSent). Delayed and Blocked are off-path
    holding states reachable from active states **OutreachSent onwards** via FR-28
    OwnerStatusConfirmed — they cannot be entered from Open because the owner has not yet
    received outreach and therefore cannot report status. Resume from Delayed/Blocked returns
    to the previous active state (one of OutreachSent / DocumentReceived / UnderPMReview /
    ReadyForSubmission), never to Open."""
    NOT_STARTED            = "Not Started"
    OPEN                   = "Open"
    OUTREACH_SENT          = "Outreach Sent"       # D-124 α: PascalCase + space
    DOCUMENT_RECEIVED      = "Document Received"
    OWNER_CLOSED           = "Owner Closed"
    UNDER_PM_REVIEW        = "Under PM Review"
    READY_FOR_SUBMISSION   = "Ready For Submission"
    SUBMITTED_TO_CUSTOMER  = "Submitted To Customer"
    CLOSED                 = "Closed"
    DELAYED                = "Delayed"
    BLOCKED                = "Blocked"

# The legal transition matrix — immutable global. Adding a transition is a code change + ADR.
# Format: from_state → set of legal target states (excluding self).
LEGAL_TRANSITIONS: dict[DeliveryState, frozenset[DeliveryState]] = {
    DeliveryState.NOT_STARTED:           frozenset({DeliveryState.OPEN}),
    DeliveryState.OPEN:                  frozenset({DeliveryState.OUTREACH_SENT}),       # no Delayed/Blocked from Open: owner cannot report status before outreach reaches them
    DeliveryState.OUTREACH_SENT:         frozenset({DeliveryState.DOCUMENT_RECEIVED, DeliveryState.OWNER_CLOSED, DeliveryState.DELAYED, DeliveryState.BLOCKED}),
    DeliveryState.DOCUMENT_RECEIVED:     frozenset({DeliveryState.OWNER_CLOSED, DeliveryState.DELAYED, DeliveryState.BLOCKED}),
    DeliveryState.OWNER_CLOSED:          frozenset({DeliveryState.UNDER_PM_REVIEW}),    # auto-advance; transient
    DeliveryState.UNDER_PM_REVIEW:       frozenset({DeliveryState.READY_FOR_SUBMISSION, DeliveryState.DELAYED, DeliveryState.BLOCKED}),
    DeliveryState.READY_FOR_SUBMISSION:  frozenset({DeliveryState.SUBMITTED_TO_CUSTOMER, DeliveryState.CLOSED, DeliveryState.DELAYED, DeliveryState.BLOCKED}),    # ReadyForSubmission → Closed allowed ONLY for no_customer_upload=True items per FR-7 + DEF-20
    DeliveryState.SUBMITTED_TO_CUSTOMER: frozenset({
        DeliveryState.CLOSED,
        DeliveryState.DOCUMENT_RECEIVED,    # rewind: customer RFI for additional/replacement doc; requires TPM attribution
        DeliveryState.OUTREACH_SENT,        # rewind: customer needs different deliverable; restart owner outreach; requires TPM attribution
    }),
    DeliveryState.CLOSED:                frozenset(),                                    # terminal
    DeliveryState.DELAYED:               frozenset({DeliveryState.OUTREACH_SENT, DeliveryState.DOCUMENT_RECEIVED, DeliveryState.UNDER_PM_REVIEW, DeliveryState.READY_FOR_SUBMISSION}),  # resume to previous active state (OPEN excluded — can't enter DELAYED from OPEN)
    DeliveryState.BLOCKED:               frozenset({DeliveryState.OUTREACH_SENT, DeliveryState.DOCUMENT_RECEIVED, DeliveryState.UNDER_PM_REVIEW, DeliveryState.READY_FOR_SUBMISSION}),
}

def transition_legal(from_state: DeliveryState, to_state: DeliveryState) -> bool:
    """Returns True iff to_state ∈ LEGAL_TRANSITIONS[from_state] OR from_state == to_state
    (idempotent no-op transitions are always legal). Pure function; no side effects."""
```

### `guards.py`

```python
@dataclass(frozen=True)
class GuardResult:
    """Outcome of a pre-transition guard check."""
    allowed:    bool
    reason:     str | None       # bounded enum token + brief context; never customer-data content (NFR-2)
    blocking_conditions: list[str]    # list of unmet guard predicates (e.g., ["doc_count_not_reached", "reviews_pending_for_doc_X"])

def check_transition_guards(
    item:           "DeliveryItem",                  # current item snapshot
    target_state:   DeliveryState,
    trigger_source: Literal["automated", "manual_tpm_override", "tpm_button"] = "automated",
    bypass_guards:  bool                              = False,   # FR-14 manual override discipline
) -> GuardResult:
    """Pure function: returns GuardResult indicating whether the (item, target_state) transition
    is allowed under current item snapshot. **Does NOT mutate.** Specific guards (Ph-1):

    1. **transition_legal(item.delivery_state, target_state)** — basic state-machine legality.
    2. **OwnerClosed 2-condition guard** per FR-28 OwnerStatusConfirmed: when target is OWNER_CLOSED,
       (a) if item_type == "Confirmation" → no doc/review requirement; allowed. **Note**: per
           FR-25 (b) role-collapse lock 2026-06-19 + FR-7 confirmation carve-out, Confirmation
           items skip OWNER_CLOSED + DOCUMENT_RECEIVED entirely (close-intent reply advances
           OUTREACH_SENT → UNDER_PM_REVIEW directly via the FR-12 path (a)/(c) close-intent
           processor); this guard branch covers any legacy/edge case where OWNER_CLOSED is
           requested for a Confirmation item.
       (b) else: (i) doc_count_received >= doc_count AND (ii) **only when `review_required = true`**:
                  all received docs+revisions reviewed (for test_report: parser_result + non-null
                  llm_review_findings; for tech_report/waiver: non-null llm_review_findings).
                  **When `review_required = false`, condition (ii) is vacuously satisfied** —
                  guard advances on (i) alone. Per FR-53: review_required is set true only when
                  item_type == "test_tech_waiver_report" (compliance/certification/release-notes
                  items + Default items have review_required = false; in Ph-1 early drop ALL
                  items have review_required = false per architect lock 2026-06-19). If (i) unmet:
                  blocking_conditions=['doc_count_not_reached']; if (ii) applies and is unmet:
                  blocking_conditions=['reviews_pending'].
    3. **READY_FOR_SUBMISSION requires UNDER_PM_REVIEW → ReadyForSubmission AND `item.pm_approval_at`
        non-None** per FR-28 PMApproval + NFR-5 gate. The PM approval is recorded as a field on the
        DeliveryItem (architect decision 2026-06-10 — Option B from PMApproval storage triage):
        `pm_approval_at: datetime | None` + `pm_approval_pm_id: str | None`. The PMApproval-trigger
        flow's UPDATE_STATE task body sets these fields BEFORE invoking tracker.update_delivery_state;
        tracker's guard reads from the item snapshot. CommunicationLog row (action_type=pm_approval +
        pm_id + timestamp) is written in parallel via AuditWriter for audit chain. blocking_conditions=
        ['pm_approval_required'] if pm_approval_at is None at guard time.
    4. **SubmittedToCustomer requires ReadyForSubmission AND all in-scope files successfully
        uploaded per FR-18** (no_customer_upload items skip this; can reach CLOSED via TPM-Mark-Closed
        per FR-7 amendment + DEF-20 carve-out).
    5. **CLOSED transitions in Ph-1 are ALWAYS TPM-manual** per FR-7 amendment + DEF-20 carve-out
        — automatic CLOSED transitions are deferred per DEF-20. Two manual paths:
        (a) **Per-item TPM-Mark-Closed**: TPM clicks "Mark Closed" button on individual item row in
            SP UI (FR-7 amendment). Allowed from SUBMITTED_TO_CUSTOMER (after submission completes
            normally) AND from READY_FOR_SUBMISSION for `no_customer_upload=True` items (skip carrier
            upload; close directly). Guard requires `trigger_source ∈ {"manual_tpm_override",
            "tpm_button"}`; automated rules MUST NOT close.
        (b) **Per-milestone "Close All Items"** (FR-64): TPM clicks one milestone-level button;
            tracker iterates over all CLOSE-eligible items in the milestone (those in
            SUBMITTED_TO_CUSTOMER OR ReadyForSubmission with `no_customer_upload=True`) and applies
            the per-item close transition to each. Same guard logic as (a) per-item; batch wraps
            multiple invocations with one TPM attribution.
        Items in DELAYED / BLOCKED / OPEN / OUTREACH_SENT / DOCUMENT_RECEIVED / UNDER_PM_REVIEW cannot
        reach CLOSED — must resume to a close-eligible state first.
    6. **DELAYED / BLOCKED** unconditional from any active state (FR-28 OwnerStatusConfirmed delayed/blocked
        + manual TPM override).
    7. **OWNER_CLOSED → UNDER_PM_REVIEW** auto-advance (transient state; tracker enforces immediately).
    8. **Rewind from SUBMITTED_TO_CUSTOMER** to DOCUMENT_RECEIVED or OUTREACH_SENT — supports customer
        RFI / re-submission scenarios per Ph-1 production reality. Guard REQUIRES TPM attribution:
        `trigger_source in {"manual_tpm_override", "tpm_button"}`. Automated rules MUST NOT rewind a
        SUBMITTED item — TRK-E006 if attempted (e.g., a rule's `UpdateState` action accidentally
        targets DOCUMENT_RECEIVED on a SUBMITTED item without TPM provenance). Rewind audit row carries
        `action_type=state_rewind_for_rfi` + `prior_carrier_submission_ref` (the FR-18 dispatch record's
        correlation_id) so the audit chain is preservable across re-submissions.

    When bypass_guards=True (FR-14 manual TPM override path; trigger_source must be
    'manual_tpm_override'): all guards skipped except transition_legal — returns allowed=True
    if state-machine-legal, else allowed=False with reason='illegal_transition_bypass_rejected'.
    Manual triggers per FR-31 sub-3 (TPM Trigger Action dropdown) do NOT use bypass_guards —
    they go through normal guards (state-filter logic on the SP UI side ensures TPM only sees
    applicable actions per FR-31 sub-3 spec)."""

def list_blocking_conditions_for_state(
    item:           "DeliveryItem",
    target_state:   DeliveryState,
) -> list[str]:
    """Convenience wrapper around check_transition_guards for SP UI / dashboard surfacing.
    Returns the human-readable list of conditions blocking the transition. Pure."""
```

### `transitions.py` — public mutating surface (consumed by `workflow_engine` task bodies)

```python
@dataclass(frozen=True)
class TransitionResult:
    """Result of an attempted transition. Caller (workflow_engine task body) consumes this:
    (a) on success, fires StateChange TriggerEvent via workflow_engine.trigger_sources.state_change_source;
    (b) on guard denial, writes WFL-W001-style log + returns to Celery framework (no retry on
        guard-denied — terminal; PM dashboard surfaces); (c) on idempotent no-op, returns task
        success without firing StateChange (no state actually changed)."""
    item_id:            str
    from_state:         DeliveryState
    to_state:           DeliveryState
    outcome:            Literal["transitioned", "no_op_idempotent", "guard_denied", "illegal_transition"]
    guard_result:       GuardResult | None                # populated for guard_denied / illegal_transition
    dispatch_signal:    "StateChangeDispatchSignal | None"   # populated for transitioned; consumed by caller to fire StateChange
    correlation_id:     str

@dataclass(frozen=True)
class StateChangeDispatchSignal:
    """Returned by tracker on successful state change; caller (workflow_engine task body) uses
    this to fire the canonical StateChange TriggerEvent per [D-066] downstream-trigger pattern.
    tracker does NOT directly call workflow_engine — keeps dependency direction one-way
    (workflow_engine → tracker) and avoids module-load cycle."""
    delivery_item_id:   str
    old_state:          DeliveryState
    new_state:          DeliveryState
    correlation_id:     str
    trigger_source:     Literal["automated", "manual_tpm_override", "tpm_button"]

def update_delivery_state(
    delivery_item_id:   str,
    target_state:       DeliveryState,
    params:             dict[str, Any],                  # action-instance params (e.g., reason text for transitions)
    event_context:      dict[str, Any],                  # serialised TriggerEvent metadata: correlation_id, trigger_source, pm_id, timestamp
    storage:            "StorageWriter",                 # injected
    sp_writer:          "SpWriter",                      # injected (delegates to sharepoint_integration.SpCrud per [D-064])
    audit:              "AuditWriter",                   # injected (writes CommunicationLog row per FR-42)
    bypass_guards:      bool                              = False,
) -> TransitionResult:
    """Canonical state-transition function consumed by workflow_engine UPDATE_STATE task body.
    Pipeline (idempotent against at-least-once delivery):
    1. Read current item snapshot via storage.get_delivery_item(delivery_item_id).
    2. If item.delivery_state == target_state → return TransitionResult(outcome=no_op_idempotent)
       (Celery retry safety — same outcome on duplicate execution).
    3. Run check_transition_guards(item, target_state, trigger_source, bypass_guards).
    4. If GuardResult.allowed=False: emit TRK-W001 + write CommunicationLog row with
       action_type=transition_guard_denied + blocking_conditions; return outcome=guard_denied.
    5. If illegal transition: emit TRK-E001 + return outcome=illegal_transition.
    6. Else: atomic transaction:
       (a) storage.write_delivery_state(item_id, target_state, modified_at, modified_by)
       (b) sp_writer.update_item("delivery_items", scope, item_id, {"delivery_state": target_state.value})
           — writes back to SP per [D-064] HILDA→SP REST
       (c) audit.write_communication_log(...) per FR-42 with action_type=state_transition
    7. Return TransitionResult(outcome=transitioned, dispatch_signal=...) — caller fires StateChange.

    Idempotent: same call twice produces the same outcome. Atomicity: steps 6a/6b/6c are wrapped
    in a single Postgres transaction with 6b deferred (SP write may fail; on failure, transaction
    rolls back DB write + caller retries; SP eventual consistency surfaces SHP-E001 if SP UI
    engineer's column setup is out of sync per [D-065])."""

def advance_on_doc_count_reached(
    delivery_item_id:   str,
    storage:            "StorageWriter",
    sp_writer:          "SpWriter",
    audit:              "AuditWriter",
    event_context:      dict[str, Any],
) -> TransitionResult:
    """Convenience wrapper called from AttachmentReceived task body when doc_count is met for
    test_report items (FR-28). Equivalent to update_delivery_state(target_state=DOCUMENT_RECEIVED)
    but with the count-check guard inlined for clarity."""

def auto_advance_owner_closed_to_under_pm_review(
    delivery_item_id:   str,
    storage:            "StorageWriter",
    sp_writer:          "SpWriter",
    audit:              "AuditWriter",
    event_context:      dict[str, Any],
) -> TransitionResult:
    """Transient-state auto-advance per FR-7. Called by tracker itself immediately after a
    successful OWNER_CLOSED transition completes. OWNER_CLOSED is never observably stable —
    it advances to UNDER_PM_REVIEW within the same transaction (Ph-1 single-revision flow) or
    forks to TriggerVersionSelection (FR-66, Ph-2 multi-revision case)."""
```

### `default_workitem.py`

```python
def instantiate_default_workitem(
    milestone_id:           str,
    customer_id:            str,                              # was customer_slug per [D-091] slug→id rename 2026-06-21
    device_id:              str,                              # was device_slug per [D-091]
    inferred_tg_path_id:    str                              = "_unrouted",   # was inferred_tg_name_slug per [D-091]
    storage:                "StorageWriter",
    sp_writer:              "SpWriter",
    audit:                  "AuditWriter",
) -> "DeliveryItem":
    """FR-78 default work-item instantiation. Called by workflow_engine
    INSTANTIATE_DEFAULT_WORK_ITEM task body when TrackerCreated trigger fires. Idempotent:
    if milestone already has a Default work-item, returns the existing row (no-op). Otherwise
    creates row with the FR-78 hardcoded inventory (architect lock 2026-06-21 — full default
    work-item field set, none template-author-editable):

      item_type             = "Default"               # PascalCase per SP UI engineer lock 2026-06-23
      tg_name               = "_unrouted"             # sentinel TG name (underscore-prefix per FR-86)
      tg_path_id            = "_unrouted"             # NSD path segment matches tg_name
      item_path_id          = None                    # default WI has no per-item NSD sub-path
      item_no               = 0 (reserved)            # zero reserved for default WI per FR-5 architect lock
      sort_order            = max(existing) + 1
      delivery_state        = OPEN                    # default WI skips OutreachSent — docs land directly
      tracking_modality     = None                    # default WI is HILDA-internal; no owner outreach
      doc_count             = 0                       # default WI has no expected doc count
      review_required       = False                   # default WI never AI-reviewed
      milestone_gating      = True                    # default WI MUST be Marked Closed before milestone Completed
      no_customer_upload    = True                    # default WI docs never uploaded to carrier portal
      force_tracking_enabled = False                  # default WI ineligible for force-tracking (no owner)
      owner_corp_usa_email  = None                    # 4-field owner identity null per [D-080] + [D-086]
      owner_corp_email      = None
      owner_corp_id         = None                    # PLM grouping key per FR-5 + [D-035] — null on default WI
      owner_name            = None
      ingress_nsd           = "None"                  # PascalCase per SP UI engineer lock 2026-06-23
      folder_routing_enabled = False                  # default WI never participates in FR-77 routing
      pm_approval_at        = None                    # cleared on entry to UNDER_PM_REVIEW; never set on default WI Ph-1
      pm_approval_pm_id     = None
      target_folder         = None                    # NULL per FR-77 — default WI has no carrier-portal upload destination
      customer_delivery_modality = None               # NULL — no carrier upload

    Writes CommunicationLog row with action_type=default_workitem_instantiated."""
```

### `reassignment.py`

```python
@dataclass(frozen=True)
class ReassignmentResult:
    file_hash:                  str
    source_item_id:             str                              # was on default work-item
    target_item_id:             str                              # TPM's chosen work-item
    rederived_doc_type:         "DocType"                        # per FR-85 re-classification on new item context
    # D-039 new-vs-revision outcome (Ph-1 Steps 0+1 fire on reassignment; Ph-2 adds Step 2 LLM + Step 3 staged)
    revision_classification:    Literal["new_document", "revision_of", "duplicate_skipped"]
    revision_of_doc_id_slug:    str | None                        # populated when revision_classification == "revision_of"
    rev_number:                 int                               # 1 for new; N+1 for revision of existing slug; existing rev_number preserved for duplicate_skipped (no increment)
    fr77_re_routed:             bool                              # True if FR-77 Type-2 carrier upload target_folder recomputed
    upload_dispatched:          bool                              # True if customer_adapter.upload_attachment was enqueued (skipped if duplicate_skipped OR no_customer_upload=True OR target_folder unresolved)
    correlation_id:             str

def reassign_document_to_workitem(
    file_hash:          str,
    source_item_id:     str,                              # the Default work-item the doc currently belongs to
    target_item_id:     str,                              # TPM's selected work-item
    pm_id:              str,                              # TPM attribution for audit
    storage:            "StorageWriter",
    sp_writer:          "SpWriter",
    audit:              "AuditWriter",
    event_context:      dict[str, Any],
) -> ReassignmentResult:
    """FR-83 TPM-manual document reassignment via SP UI. Called by workflow_engine
    REASSIGN_DOCUMENT_TO_WORK_ITEM task body when sp_alert_parser dispatches the
    tpm_reassign_to_workitem action verb from FR-87 step (A) button click on the HILDA-rendered
    document section. **FR-87 step scope**: step (A) = TPM reassignment from default WI to a
    template-defined work-item (this function's scope); step (B) = doc_type re-classification
    when FR-86 alignment matrix flags mismatch (re-derivation happens inline at step 6 below;
    if the corrected doc_type still mismatches the target item's item_type, TRK-E003 fires);
    step (C) = `[D-039]` revision resolution (Ph-1 fires Steps 0+1 deterministic; Ph-2 adds
    Step 2 LLM CLASSIFY_DOC + Step 3 staged per architect direction 2026-06-21 CLASSIFY_DOC
    Ph-2 demotion). Pipeline:
    1. Verify source_item_id refers to a Default work-item (TRK-E002 if not).
    2. Verify target_item_id is in same milestone + has compatible item_type for the doc's
       doc_type per FR-86 alignment matrix (TRK-E003 if not — TPM picked an incompatible
       target; SP UI engineer's button picker SHOULD filter, but tracker enforces).
    3. Update DocumentItemAssociation: replace source_item_id with target_item_id; clear
       unrouted_source_path on the row.
    4. **D-039 Step 0 (hash dedup, Ph-1)** — check if file_hash exists in any DocumentIndexRow
       for (target_item_id, doc_type). If match → revision_classification="duplicate_skipped";
       set rev_number to the existing duplicate's rev_number (preserved); skip storage doc-row
       write + skip upload dispatch; jump to step 9 (audit + SP writeback).
    5. **D-039 Step 1 (slug match, Ph-1)** — slugify(original_filename) → candidate_slug.
       Query existing doc_id_slugs for (target_item_id, doc_type):
       - If candidate_slug matches exactly → revision_classification="revision_of",
         revision_of_doc_id_slug=<matched>, rev_number=max_existing_rev_for_slug+1.
       - If no slug entries exist for (target_item_id, doc_type) → "new_document", rev_number=1.
       - If multiple non-exact matches exist (Ph-2 LLM ambiguity case) → defer to Ph-2 D-039
         Step 2; for Ph-1, fall through to "new_document" with rev_number=1 and emit TRK-W007
         (Ph-2 LLM disambiguation deferred; Ph-2 may revise this classification when it lands).
    6. Re-derive doc_type via FR-85 ladder if needed (may differ from default-workitem
       assignment); if changed, storage.update_document_index_row(doc_type=...) AND re-run
       D-039 Steps 0+1 against the new doc_type scope (compatibility re-check).
    7. Re-run FR-77 Type-2 routing for the document → resolves new target_folder.
    8. If revision_classification != "duplicate_skipped" AND no_customer_upload=False AND
       target_folder resolved: enqueue customer_adapter.upload_attachment Celery task
       (idempotency: per-file hash check per FR-68; per-revision behavior: rev1 = new upload,
       revN = revision per FR-69 portal_structure.yaml overwrite-vs-rename rule).
    9. Audit: CommunicationLog row with action_type=tpm_reassign_to_workitem + pm_id +
       revision_classification.
    10. SP writeback: DeliveryItem row's tpm_reassignment_target_item_id field cleared
        post-resolution; default work-item row's document_count decremented per FR-86
        combinatorics."""
```

### `tag_propagation.py`

```python
@dataclass(frozen=True)
class TagPropagationResult:
    customer_id:        str                              # was customer_slug per [D-091]
    tg_name:            str
    item_no:            int
    new_tags:           list[list[str]] | None           # nested tag-set per FR-82 lock 2026-06-20 (list of synonym groups; None = clear tags)
    propagated_count:   int                              # active items updated across milestones / devices
    skipped_count:      int                              # items skipped due to pause / mismatched state / etc.
    correlation_id:     str

def propagate_tags_to_active_trackers(
    customer_id:        str,                              # was customer_slug per [D-091]
    tg_name:            str,
    item_no:            int,
    new_tags:           list[list[str]] | None,           # nested tag-set per FR-82 lock 2026-06-20
    pm_id:              str,                              # TPM/ops attribution
    storage:            "StorageWriter",
    sp_writer:          "SpWriter",
    audit:              "AuditWriter",
    event_context:      dict[str, Any],
) -> TagPropagationResult:
    """FR-82 + ItemModified.TagsModified handler. Called by workflow_engine
    PROPAGATE_TAGS_TO_ACTIVE_TRACKERS task body. Propagates the new item_description
    **nested tag-set** (list of synonym groups per FR-82 lock 2026-06-20; each inner list is
    a synonym group like `[["VoLTE", "Voice over LTE"], ["AGPS"]]`) to ALL items matching
    (customer_id, tg_name, item_no) across all active milestones + devices for that customer.
    Pipeline:
    1. storage.find_items_by_natural_key(customer_id, tg_name, item_no, only_active=True)
    2. Dedup at synonym-group level within new_tags (idempotent per FR-82): dedupe groups by
       set-equality (order-insensitive within a group) + dedupe at the outer list level by
       canonicalized-group-tuple. Subset-overlap warnings emitted via TSC-W007 at template_schema
       validation time (not at propagation time — propagation is best-effort).
    3. For each matched item: skip if delivery_state in {CLOSED, SUBMITTED_TO_CUSTOMER} — tags
       no longer affect routing once dispatched; per FR-82.
    4. For each non-skipped item: update item_description via storage.update_delivery_item +
       sp_writer.update_item (writes JSON serialization of nested list per template_schema
       FR-82 field model); audit with action_type=tag_catalog_propagation + pm_id +
       group_count + total_synonym_count (no raw tag text — NFR-2).
    5. Returns TagPropagationResult counts. Idempotent: re-running with same new_tags is a no-op
       (dedup at write time + idempotency on item_description JSON equality check)."""
```

### `manual_override.py`

```python
def apply_manual_tpm_override(
    delivery_item_id:   str,
    field_deltas:       dict[str, tuple[Any, Any]],     # e.g., {"delivery_state": (DOCUMENT_RECEIVED, OWNER_CLOSED), "doc_count": (3, 5), "no_customer_upload": (False, True)}
    pm_id:              str,                              # TPM attribution
    storage:            "StorageWriter",
    sp_writer:          "SpWriter",
    audit:              "AuditWriter",
    event_context:      dict[str, Any],
) -> list[TransitionResult]:
    """FR-14 manual TPM field override — **generic handler for TPM SP UI field edits that do
    NOT have a dedicated ItemModified sub-trigger per FR-28**. Called by workflow_engine task
    body when `sp_alert_parser` detects a TPM-manual SP field edit on a field WITHOUT a
    sub-trigger. **Sub-trigger fields route via rule_engine instead** — any of the 4 owner
    identity fields (`owner_corp_usa_email` / `owner_corp_email` / `owner_corp_id` / `owner_name`
    per [D-080] + [D-086]) → ItemModified.OwnerReassigned (sp_alert_parser detects any of the
    4-field set changing); Milestone-level `target_date` edit (was per-item `expected_completion_date`
    pre-[D-085]) → ItemModified.DeadlineMoved (sourced from the Milestones SP list row per
    [D-083] + [D-085]; cascades to ALL items in the milestone via re-arm semantics per FR-11);
    `item_description` → ItemModified.TagsModified (nested tag-set per FR-82 lock 2026-06-20).
    For those fields, sp_alert_parser
    constructs a TriggerEvent and dispatches through workflow_engine.TriggerDispatcher — NOT
    through this function.

    Fields THIS function handles (no sub-trigger per FR-28): `delivery_state` (direct state
    write with bypass_guards), `doc_count`, `review_required`, `no_customer_upload`, `plm_id`,
    `actual_item_info`, `target_folder` (FR-77 routing override), other ad-hoc TPM corrections.

    Pipeline:
    1. For each field in field_deltas:
       (a) if field == 'delivery_state': call update_delivery_state with bypass_guards=True
            + trigger_source='manual_tpm_override' (TPM override is explicit; guards apply
            only the legality check, not the OwnerClosed 2-condition gate / pm_approval guard /
            rewind guard).
       (b) else: storage.update_delivery_item(field, new_value) + sp_writer.update_item.
    2. Audit: per-field CommunicationLog row with action_type=manual_field_override + pm_id +
       field_name + old_value + new_value (subject to NFR-2 — bounded values only; raw text
       fields like item_description are tag-list-shaped so safe; free-text overrides are
       rejected at sp_alert_parser).
    3. Returns list of TransitionResult (one per state-related delta; empty for non-state
       fields). Caller fires StateChange triggers from the dispatch_signals returned."""
```

### `protocols.py` — dependency injection contract

```python
class StorageWriter(Protocol):
    """Subset of storage module's interface that tracker depends on. Injected so tracker is
    decoupled from concrete storage class + unit-testable with mocks."""
    def get_delivery_item(self, delivery_item_id: str) -> "DeliveryItem": ...
    def write_delivery_state(self, delivery_item_id: str, new_state: DeliveryState,
                              modified_at: datetime, modified_by: str) -> None: ...
    def update_delivery_item(self, delivery_item_id: str, fields: dict[str, Any]) -> None: ...
    def update_document_item_association(self, file_hash: str,
                                          fields: dict[str, Any]) -> None: ...
    def update_document_index_row(self, file_hash: str, fields: dict[str, Any]) -> None: ...
    def find_items_by_natural_key(self, customer_id: str, tg_name: str, item_no: int,
                                    only_active: bool = True) -> list["DeliveryItem"]: ...    # customer_id per [D-091] slug→id rename
    def list_default_workitem_for_milestone(self, milestone_id: str) -> "DeliveryItem | None": ...

class SpWriter(Protocol):
    """Subset of sharepoint_integration.SpCrud per [D-064]. Same decoupling rationale."""
    def update_item(self, entity: str, scope: "ListScope", item_id: str,
                    canonical_fields: dict[str, Any]) -> None: ...
    def create_item(self, entity: str, scope: "ListScope",
                    canonical_fields: dict[str, Any]) -> str: ...

class AuditWriter(Protocol):
    """Writes CommunicationLog rows per FR-42. Decoupled so tests can mock."""
    def write_communication_log(self, action_type: str, delivery_item_id: str | None,
                                  attribution: dict[str, str], details: dict[str, Any]) -> None: ...
```

### Configuration

```python
class TrackerConfig(BaseModel):
    """Light config — most behavior is fixed by FR-7 / FR-28 / FR-82 / FR-83 specs."""
    auto_advance_owner_closed:           bool = True       # Ph-1 always True; Ph-2 may add per-item override
    propagate_tags_skip_terminal_states: bool = True       # FR-82 — items in CLOSED / SUBMITTED don't accept tag updates
    audit_log_every_transition:          bool = True       # FR-42; should always be True; flag exists for dev environments
    guard_check_strict_mode:             bool = True       # production = True; ops debug = False to allow tracing without state mutations
```

---

## Invariants

- **11-state machine is the single source of truth for `delivery_state`** — no other module mutates `delivery_state` directly; all changes go through `transitions.update_delivery_state` (which enforces guards) or `transitions.auto_advance_owner_closed_to_under_pm_review` (internal). `storage.write_delivery_state` is only called from tracker.
- **Transitions are idempotent** per Celery at-least-once delivery — re-running `update_delivery_state(item, target_state)` produces the same `TransitionResult` (outcome=`no_op_idempotent` on second invocation if state already at target).
- **Guards are pure functions** — `check_transition_guards` never mutates DB, SP, or any external system. Pure functional means easier unit tests + safe to call from `dashboard` / SP UI engineer's prototype for "would this transition be allowed?" queries.
- **`bypass_guards=True` is reserved for FR-14 manual TPM override** — automated callers (rule_engine-driven UPDATE_STATE) MUST NOT set `bypass_guards=True`. TRK-E004 if attempted.
- **Rewind from SUBMITTED_TO_CUSTOMER requires TPM attribution** — customer RFI / re-submission scenarios per Ph-1 production reality: TPM can rewind a submitted item to DOCUMENT_RECEIVED (additional/replacement document) or to OUTREACH_SENT (different deliverable; restart owner outreach). Rewind requires `trigger_source ∈ {"manual_tpm_override", "tpm_button"}`; automated rules MUST NOT rewind a SUBMITTED item — TRK-E006 if attempted. Audit row carries `action_type=state_rewind_for_rfi` + `prior_carrier_submission_ref` (FR-18 dispatch record's correlation_id) so audit chain preserves across re-submissions.
- **sp_alert_parser routing discipline for TPM SP UI field edits** — `email_service.sp_alert_parser` (per `[D-047]`) routes TPM-edited field changes into one of two paths based on which field changed: (a) **rule_engine path** for fields with a dedicated ItemModified sub-trigger per FR-28 — **any of the 4-field owner identity set** (`owner_corp_usa_email` / `owner_corp_email` / `owner_corp_id` / `owner_name` per `[D-080]` + `[D-086]`) → `ItemModified.OwnerReassigned` (sp_alert_parser detects any of the 4-field set changing on Deliverables row); **Milestone-level `target_date`** edit on Milestones SP list row per `[D-083]` + `[D-085]` (was per-item `expected_completion_date` pre-`[D-085]`) → `ItemModified.DeadlineMoved` (cascades to ALL items in the milestone via FR-11 re-arm semantics; sp_alert_parser scopes the trigger to the milestone, not the row); **`item_description`** → `ItemModified.TagsModified` (nested tag-set per FR-82 lock 2026-06-20). sp_alert_parser constructs a `TriggerEvent` and calls `workflow_engine.TriggerDispatcher.dispatch`; matched rules fire their action lists (NotifyNewOwner + StartItemCollection / REARM_DEADLINE_PROXIMITY / PROPAGATE_TAGS_TO_ACTIVE_TRACKERS respectively). (b) **FR-14 manual override path** for ALL other fields — `delivery_state`, `doc_count`, `review_required`, `no_customer_upload`, `plm_id`, `actual_item_info` (PLM URL per architect direction 2026-06-23), `target_folder`, etc. sp_alert_parser calls `workflow_engine.tasks.apply_manual_tpm_override` which invokes `tracker.apply_manual_tpm_override`. The routing decision is sp_alert_parser's responsibility, not tracker's — tracker accepts both `update_delivery_state(trigger_source="manual_tpm_override")` (from the FR-14 path) and `update_delivery_state(trigger_source="automated")` (from rule_engine path) symmetrically.
- **All Ph-1 CLOSED transitions are TPM-manual** per FR-7 amendment + DEF-20 — automatic CLOSED is deferred. Two paths only: (a) per-item TPM-Mark-Closed button (FR-7), (b) per-milestone FR-64 "Close All Items" batch (which internally invokes the per-item close for each eligible item). Automated rules MUST NOT produce `UpdateState(target=CLOSED)` — TRK-E006-style rejection (the same "requires TPM attribution" guard that rejects rewinds from SUBMITTED also rejects automated close).
- **Confirmation items MUST have `no_customer_upload=True`** — `item_type = "Confirmation"` (`[D-053]` + SP UI engineer lock 2026-06-23 PascalCase) is an owner Yes/No confirmation; by nature there is no document to upload, hence no carrier upload. Enforced at template_schema validation time (TSC-W003-style warning at customer-template load if a Confirmation item declares `no_customer_upload=False`); enforced at `tracker.instantiate_default_workitem` if a default work-item is created with conflicting flags. **Confirmation state-traversal carve-out per FR-7 + FR-25 (b) role-collapse lock 2026-06-19**: Confirmation items skip `DOCUMENT_RECEIVED` + `OWNER_CLOSED` entirely (no doc to receive, no doc-driven close gate); close-intent reply advances `OUTREACH_SENT → UNDER_PM_REVIEW` directly via FR-12 path (a)/(c) close-intent processor. The OwnerClosed 2-condition guard's `item_type == "Confirmation"` branch (line 114) is a backstop for legacy/edge cases — in normal Ph-1 operation Confirmation items never reach OWNER_CLOSED. PM-Review step for Confirmation items is a nominal approval (no docs/results to evaluate); TPM-Mark-Closed from `ReadyForSubmission` is the close path (per the `no_customer_upload=True` carve-out).
- **`pm_approval_at` clearing discipline** — the field is set by the PMApproval-trigger flow before tracker advances UNDER_PM_REVIEW → READY_FOR_SUBMISSION. tracker MUST clear `pm_approval_at = None` + `pm_approval_pm_id = None` on (a) entry to UNDER_PM_REVIEW (auto-advance from OWNER_CLOSED — fresh review cycle starts), (b) all rewind transitions from SUBMITTED_TO_CUSTOMER (re-traversal of UNDER_PM_REVIEW requires fresh PM approval), (c) DELAYED / BLOCKED return to UNDER_PM_REVIEW (state was lost; re-approval required). Failure to clear means a stale prior approval would silently let a re-traversal jump to READY_FOR_SUBMISSION without genuine PM input — defensive against this case is the whole point of Option B. TRK-W006 emitted if guard finds `pm_approval_at` non-None on an item just-entered UNDER_PM_REVIEW (detection of missed clear).
- **`StateChange` trigger is fired by the CALLER, not by tracker** — per `[D-022]` boundary discipline + module-load-cycle avoidance. tracker returns `StateChangeDispatchSignal`; workflow_engine task body fires the trigger via `trigger_sources.state_change_source.fire_state_change` AFTER successful transaction commit. tracker never imports workflow_engine.
- **State transition + SP writeback + CommunicationLog write are one transaction** — DB transaction wraps all three; on SP write failure (transient), entire transaction rolls back; Celery retry replays the call (idempotent per above). On SP write permanent failure (SHP-E001 from value mismatch per `[D-065]`), tracker emits TRK-E005 + Celery does NOT retry — ops triage required.
- **OWNER_CLOSED is transient** — never observably stable; auto-advances to UNDER_PM_REVIEW within the same Celery task body via `auto_advance_owner_closed_to_under_pm_review`. SP UI never shows OWNER_CLOSED for any meaningful duration.
- **DELAYED and BLOCKED are off-path holding states** — reachable from active states **OutreachSent onwards** via FR-28 OwnerStatusConfirmed; NOT reachable from Open (owner has not yet received outreach and therefore cannot report a delay or block). Resume from DELAYED/BLOCKED returns to the previous active state (stored in item's `prior_delivery_state` field per `template_schema.DeliveryItemBase` + FR-7 Delayed/Blocked exit paths) — one of OutreachSent / DocumentReceived / UnderPMReview / ReadyForSubmission. Cannot transition directly from DELAYED/BLOCKED to CLOSED (must resume first).
- **`Default work-item` is per-MILESTONE per `[D-060]`** — not per-TG. `instantiate_default_workitem` is idempotent per milestone; second call returns the existing row.
- **FR-82 tag propagation is dedup'd at write time** — duplicate tags in `new_tags` list are silently de-duplicated; matching items in CLOSED/SUBMITTED states are silently skipped per FR-82.
- **FR-83 reassignment enforces FR-86 alignment matrix** — TPM-selected target item must have a compatible `item_type` for the document's doc_type; TRK-E003 if not (SP UI engineer SHOULD filter at button-picker time, but tracker enforces as backstop).
- **FR-31 sub-1 pause check is at workflow_engine dispatch time, NOT at tracker transition time** — tracker is invoked by workflow_engine task bodies; if the item is paused, workflow_engine skips the task at dispatch time. Tracker assumes the caller has already cleared pause check (FR-31 sub-1 semantic). Manual TPM triggers per FR-31 sub-3 bypass pause check entirely.
- **No proprietary content in TRK-* error codes or compact reports** per NFR-2 / `[D-002]` — emit state names, transition kind, blocking-condition tokens (bounded enum), pm_id (opaque), correlation_id; never customer-data values, never owner-reply prose, never document content.

---

## Error codes (TRK prefix — registered in `diagnostics/error_codes.py`)

```
TRK-E001  Illegal transition: delivery_item '{item_id}' state '{from}' → '{to}' not in LEGAL_TRANSITIONS
TRK-E002  Reassignment source '{source_item_id}' is not a Default work-item — FR-83 path rejects
TRK-E003  Reassignment target '{target_item_id}' item_type '{target_type}' incompatible with doc_type '{doc_type}' per FR-86 alignment matrix
TRK-E004  bypass_guards=True attempted from non-manual_tpm_override trigger_source '{source}' — automated callers MUST NOT bypass
TRK-E005  SP REST writeback permanent failure on state transition (delivery_item '{item_id}' → '{state}'): {sp_error} — ops triage
TRK-E006  Rewind from SubmittedToCustomer to '{target_state}' attempted without TPM attribution (trigger_source='{source}') — automated rules cannot rewind a submitted item; manual TPM override required per FR-14
TRK-W001  Transition guard denied: delivery_item '{item_id}' state '{from}' → '{to}' (blocking: {conditions}) — PM/TPM dashboard surface
TRK-W002  FR-82 tag propagation: item '{item_id}' skipped (terminal state '{state}')
TRK-W003  Transition idempotent no-op: delivery_item '{item_id}' already at '{state}' — Celery retry collapsed
TRK-W004  Default work-item already instantiated for milestone '{milestone_id}'; reusing existing row '{item_id}'
TRK-W005  FR-83 reassignment: target item's tpm_reassignment_target_item_id field was not cleared post-resolution — surfacing for ops triage; may indicate SP UI engineer's button UX out of sync
TRK-W006  pm_approval_at not cleared on entry to UNDER_PM_REVIEW for item '{item_id}' — defensive clear discipline missed; ops triage (stale approval may silently auto-advance on next UpdateState)
TRK-W007  D-039 Step 2 LLM disambiguation deferred (Ph-2) on reassignment of file '{file_hash}' to item '{target_item_id}': multiple slug candidates {candidates}; Ph-1 fallback set revision_classification=new_document (rev1) — Ph-2 LLM will resolve later
```

---

## Key choices

- **`[D-022]`** — tracker depends on neither rule_engine nor workflow_engine. The 4 cross-cutting actions tracker exports are consumed by workflow_engine task bodies; tracker's `StateChangeDispatchSignal` return value lets the caller fire the StateChange trigger without tracker importing workflow_engine. Keeps dependency direction one-way + avoids Python module-load cycle.
- **`[D-064]`** — state writebacks via injected `SpWriter` Protocol (concrete: `sharepoint_integration.SpCrud`). Per `[D-064]` HILDA→SP REST sole writeback channel. tracker never writes to SP directly; never bypasses SpCrud.
- **`[D-066]`** — StateChange is the canonical downstream-trigger primitive enabling rule-chaining via "after rule A's effect lands, rule B subscribes to StateChange". tracker fires StateChange dispatch signal AFTER commit; downstream rules see committed state.
- **Pure functional guards** (architect decision 2026-06-10 — captured here as Key choice, no separate ADR) — `check_transition_guards` is side-effect-free. Rejected alternatives: (α) guards embedded inside update_delivery_state (composability lost — SP UI engineer's "preflight check" would need a separate dry-run flag); (β) guards write to a "guard-check audit" table (over-engineered — Postgres write per check is wasteful; audit happens at guard-denied moment via TRK-W001 + CommunicationLog). Pure functions enable: dashboard surfacing "what blocks this transition?", SP UI preflight before button enable, unit-testability.
- **Dependency injection via Protocols** (architect decision 2026-06-10 — Key choice) — `StorageWriter`, `SpWriter`, `AuditWriter` injected at function-call time. Rejected alternatives: (α) module-level singletons (test isolation broken; can't swap concrete implementations); (β) factory functions inside tracker (couples tracker to concrete classes). The Protocol pattern matches the `[D-008]` IssueTracker + `[D-009]` Messenger boundary conventions HILDA uses elsewhere.
- **`pm_approval_at` field on DeliveryItem for PMApproval recording** (Option B; architect decision 2026-06-10) — rejected alternatives: (α) implicit via state-machine constraints alone — defensively weak; rule misfire could silently advance items past UNDER_PM_REVIEW without genuine PM approval; (β) CommunicationLog query in guard — requires StorageWriter extension + per-guard SQL roundtrip + complex "most-recent UNDER_PM_REVIEW entry" filter; (γ) chosen Option B — 2 fields on DeliveryItem (`pm_approval_at` + `pm_approval_pm_id`); zero extra DB cost in guard (already in item snapshot); clear discipline on UNDER_PM_REVIEW entry + rewind paths; CommunicationLog row still written in parallel for audit chain.
- **Atomic state-write + SP writeback + CommunicationLog** in one transaction (architect decision 2026-06-10) — rejected alternatives: (α) write state + emit "should-also-write-SP" event for async pickup — race window where DB says one thing, SP says another; rejected because PM/TPM watching SP UI would see stale state until the async event lands. (β) Write state synchronously, write SP best-effort with retry queue — SP writeback failure is more common than DB failure (SP-side Choice value mismatch per `[D-065]`); async would silently accumulate failures. Synchronous one-transaction = simpler reasoning + Celery retry handles transient SP failures naturally.
- **OWNER_CLOSED transient (auto-advances)** per FR-7 — Ph-1 single-revision happy path makes OWNER_CLOSED a state with no observable duration. Ph-2 multi-revision (FR-66) makes it briefly observable (waiting for owner version-selection); MODULE.md flags this in `## Deferred`. Currently OWNER_CLOSED → UNDER_PM_REVIEW happens within the same task body via inline `auto_advance_owner_closed_to_under_pm_review` call.

---

## Worked example — `OwnerStatusConfirmed "done"` for a test_report item, doc_count met, reviews complete

Showing how Owner reports "done" status, tracker advances `delivery_state` through OWNER_CLOSED → UNDER_PM_REVIEW, and downstream rules see the committed state via StateChange trigger.

1. Owner emails "I'm done" reply for item `I-1234` (FR-12 path c)
2. `email_service.email_ingest_source.fire_from_owner_reply` constructs `TriggerEvent(trigger=OWNER_STATUS_CONFIRMED, field_deltas={status: "done"})`
3. `workflow_engine.TriggerDispatcher.dispatch(event)`:
   - rule_engine.evaluate returns `[RuleMatch(rule_id="handle_owner_done_confirmed", actions=[UpdateState(target=OWNER_CLOSED)])]`
4. workflow_engine UPDATE_STATE task body (in `hilda-worker`) runs:
   ```python
   result = tracker.update_delivery_state(
       delivery_item_id="I-1234",
       target_state=DeliveryState.OWNER_CLOSED,
       params={},
       event_context={...},
       storage=storage_writer, sp_writer=sp_crud, audit=audit_writer,
   )
   ```
5. Inside `tracker.update_delivery_state`:
   - reads current item: `state=DOCUMENT_RECEIVED`, `doc_count=3`, `doc_count_received=3`, all 3 reviewed
   - `check_transition_guards(item, target=OWNER_CLOSED)`:
     - transition_legal(DOCUMENT_RECEIVED, OWNER_CLOSED) → True
     - OwnerClosed 2-condition guard: (i) 3≥3 ✓; (ii) all reviewed ✓ → allowed=True
   - atomic transaction:
     - storage.write_delivery_state("I-1234", OWNER_CLOSED, ...)
     - sp_writer.update_item("delivery_items", scope, "I-1234", {"delivery_state": "OwnerClosed"})
     - audit.write_communication_log(action_type="state_transition", ...)
   - **Inline auto-advance** (OWNER_CLOSED is transient): immediately calls `auto_advance_owner_closed_to_under_pm_review("I-1234", ...)` → second transition DOCUMENT_RECEIVED→OWNER_CLOSED then OWNER_CLOSED→UNDER_PM_REVIEW; both within same task body
   - returns `TransitionResult(outcome="transitioned", from=DOCUMENT_RECEIVED, to=UNDER_PM_REVIEW, dispatch_signal=...)`
6. workflow_engine task body consumes dispatch_signal → calls `state_change_source.fire_state_change(item_id="I-1234", old_state=DOCUMENT_RECEIVED, new_state=UNDER_PM_REVIEW, correlation_id=...)`
7. **StateChange TriggerEvent fires** → rule_engine.evaluate may match downstream rules (e.g., `notify_pm_on_under_pm_review_entry`) → independent Celery chains scheduled per `[D-066]`
8. SP UI focus-aware refresh picks up `delivery_state=UnderPMReview` on next focus-gain; TPM sees the advance

Two state writes happened (DOCUMENT_RECEIVED→OWNER_CLOSED, OWNER_CLOSED→UNDER_PM_REVIEW), but only ONE observable state in SP UI (UnderPMReview) — OWNER_CLOSED transient never gets serialized to SP because the auto-advance fires within the same transaction (actually, technically two transactions, but back-to-back; observability window is sub-second).

## Worked example — FR-64 "Close All Items" batch close (milestone-level TPM action)

Showing the per-milestone batch close path:

1. TPM clicks **"Close All Items"** milestone-level button in SP UI (FR-64 sentinel pattern per FR-84 — milestone-scoped actions write to `ItemNumber="_milestone"` sentinel row)
2. SP-alert email → `sp_alert_parser` detects milestone-level action verb `close_all_items`
3. `workflow_engine.tasks.milestone.close_all_items` Celery task fires:
   ```python
   def close_all_items(milestone_id, pm_id, event_context):
       eligible = storage.list_items_for_milestone(
           milestone_id,
           states={DeliveryState.SUBMITTED_TO_CUSTOMER,
                   DeliveryState.READY_FOR_SUBMISSION},   # ReadyForSubmission only counts if no_customer_upload=True
       )
       eligible = [i for i in eligible
                   if i.delivery_state == SUBMITTED_TO_CUSTOMER
                   or (i.delivery_state == READY_FOR_SUBMISSION and i.no_customer_upload)]
       results = []
       for item in eligible:
           result = tracker.update_delivery_state(
               delivery_item_id=item.id,
               target_state=DeliveryState.CLOSED,
               params={"closed_via": "fr64_batch"},
               event_context={"trigger_source": "tpm_button", "pm_id": pm_id, "correlation_id": ...},
               storage=storage_writer, sp_writer=sp_crud, audit=audit_writer,
           )
           results.append(result)
       return {"eligible_count": len(eligible), "closed_count": sum(1 for r in results if r.outcome == "transitioned")}
   ```
4. For each item, `tracker.update_delivery_state` runs guards normally — but since `trigger_source = "tpm_button"`, the CLOSED transitions pass (would be denied for `"automated"`). Each transition writes its own DB row + SP writeback + CommunicationLog row with `action_type=closed_via_batch` + pm_id.
5. Each successful transition returns a `StateChangeDispatchSignal`; workflow_engine fires individual StateChange triggers per item (downstream rules see committed Closed state).
6. After all items processed: if every item in the milestone is now `Closed`, the `MilestoneAllClosed` trigger fires → rule_engine may fire `MILESTONE_STORAGE_CLEANUP` (FR-76) action.

**Idempotency**: Re-running `close_all_items` re-iterates over eligible items; items already in CLOSED state → `tracker.update_delivery_state` returns `outcome=no_op_idempotent`; batch returns lower `closed_count` on second run (since most items already closed).

---

## Non-goals

- **Not a Celery worker / not a task scheduler** — `workflow_engine` owns Celery; tracker is library code consumed by task bodies. No `@hilda_celery_app.task` decorators in tracker.
- **Not a rule evaluator** — `rule_engine` owns evaluation per `[D-022]`. tracker emits state transitions; downstream rule firing is workflow_engine's domain.
- **Not the SP REST writer** — `sharepoint_integration.SpCrud` per `[D-064]`. tracker injects `SpWriter` Protocol; the concrete impl is SpCrud.
- **Not a document storage manager** — `storage` owns NSD + Postgres. tracker reads via `StorageWriter` injection; never opens SMB sessions or Postgres connections directly.
- **Not a credential reader** — `credential_service` per `[D-019]`. tracker accepts attribution metadata (pm_id) passed in by callers.
- **Not the LLM consumer** — `llm` per `[D-052]`. tracker's transitions are deterministic; AI review fires via the rule_engine → workflow_engine → llm path, not from tracker.
- **Not the carrier upload dispatcher** — `customer_adapter` per `[D-054]`. `reassign_document_to_workitem` enqueues a `customer_adapter.upload_attachment` task via workflow_engine, but the upload mechanics live in customer_adapter.
- **Not the SP-alert email parser** — `email_service.sp_alert_parser` per `[D-047]`. tracker is invoked by workflow_engine task bodies that themselves were dispatched from sp_alert_parser-fired triggers — two hops upstream.
- **Not a workflow DSL** — no DAGs, no multi-step durable orchestration. tracker is single-transaction state mutations; multi-step orchestration lives in workflow_engine + rule_engine chaining via StateChange.
- **Not a manual-trigger UI** — SP UI engineer owns FR-31 sub-3 / FR-87 / FR-65 / FR-83 buttons; tracker is invoked downstream by workflow_engine task bodies.
- **Not the FR-31 sub-1 pause enforcer** — that lives in workflow_engine's TriggerDispatcher; tracker assumes upstream check.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate`, `register_code` (TRK-* codes registered).
- `template_schema` — `DeliveryItemBase`, `DeliveryState` enum (canonical 11-value source), `ItemType` enum, `DocType` enum, FR-86 alignment matrix predicate.
- `StorageWriter` Protocol (injected; concrete: `storage`) — DB reads + writes.
- `SpWriter` Protocol (injected; concrete: `sharepoint_integration.SpCrud`) — SP writebacks.
- `AuditWriter` Protocol (injected; concrete: `storage.write_communication_log` per FR-42) — audit log writes.

*(Conspicuous absences: no `rule_engine` — tracker doesn't evaluate rules; no `workflow_engine` — tracker doesn't fire triggers (returns dispatch signal for caller to fire); no `llm` / `email_service` / `messenger` / `customer_adapter` / `issue_tracker` — tracker is pure state-machine + DB; no upstream business logic.)*

---

## Depended on by

- `workflow_engine` — calls `tracker.update_delivery_state` from `UPDATE_STATE` task body; `tracker.instantiate_default_workitem` from `INSTANTIATE_DEFAULT_WORK_ITEM` task body; `tracker.reassign_document_to_workitem` from `REASSIGN_DOCUMENT_TO_WORK_ITEM` task body; `tracker.propagate_tags_to_active_trackers` from `PROPAGATE_TAGS_TO_ACTIVE_TRACKERS` task body; `tracker.apply_manual_tpm_override` from FR-14 manual override task body. Authoritative caller.
- `dashboard` — calls `tracker.guards.check_transition_guards` (read-only) to surface "what blocks this transition?" on PM/TPM views; never invokes mutating functions.
- `hilda-api` (workload, not module) — read-only state queries from REST endpoints (e.g., `/items/{id}/state`); never invokes mutating tracker functions from the API surface.

---

## Deferred (Ph-2 / Ph-3+)

- **Ph-2 — D-039 Step 2 LLM `CLASSIFY_DOC` + Step 3 staged on ambiguous slug match during FR-83 reassignment** — currently Ph-1 fires D-039 Steps 0+1 (deterministic hash dedup + exact-slug match) during reassignment; on ambiguous slug match (Step 1 returns multiple non-exact candidates), Ph-1 falls through to `new_document` with TRK-W007 emitted. Ph-2 will fire LLM `CLASSIFY_DOC` first-page-excerpt comparison against existing doc_id_slugs and either pick REVISION:<slug> (high confidence) or defer to STAGED for PM dashboard triage. ReassignmentResult.revision_classification may be revised by the Ph-2 LLM after-the-fact resolution.
- **Ph-2 — Multi-revision FR-66 fork at OWNER_CLOSED** — currently Ph-1 OWNER_CLOSED auto-advances to UNDER_PM_REVIEW since single-revision is assumed. Ph-2 introduces a fork: if multiple revisions exist for any `doc_id_slug`, tracker fires `TriggerVersionSelection` instead of auto-advancing; owner selects final revision via corp messenger; on selection `is_final=true` → tracker resumes auto-advance. Will require a new transient state (`AwaitingVersionSelection`) inserted between OWNER_CLOSED and UNDER_PM_REVIEW.
- **Ph-2 — `apply_manual_tpm_override` extension** for per-field-type validation (currently Ph-1 accepts any field write with `bypass_guards`; Ph-2 may add per-field validation policies).
- **Ph-2 — Cross-tracker tag propagation may extend to TERMINAL items in audit-only mode** — currently FR-82 skips CLOSED/SUBMITTED; Ph-2 may add "tag_history" audit table tracking which tags applied to which items at submission time.
- **Ph-2 — DELAYED / BLOCKED state extensions** — Ph-2 may add structured reason codes (e.g., `delay_reason=awaiting_supplier`, `block_reason=ip_clearance_pending`) + auto-notifications on long-pending DELAYED/BLOCKED items.
- **Ph-3+ — Distributed state-machine event sourcing** — current Ph-1/Ph-2 stores delivery_state as a single column on DeliveryItem; Ph-3+ may add event-source layer for replay + audit. Defer until ops scale demands.
- **Ph-3+ — Optimistic concurrency control on state transitions** — currently Ph-1 uses Postgres SELECT FOR UPDATE for the read-then-write pattern; under high concurrency this may bottleneck. Ph-3+ may add version-column-based OCC. Defer until ops scale demands.
- **Ph-3+ — State-machine policy plugins** — currently the LEGAL_TRANSITIONS matrix is hardcoded in state_machine.py; Ph-3+ could allow per-customer override (e.g., "Customer Z requires UNDER_PM_REVIEW → DELAYED instead of direct → CLOSED on rejection"). Rejected for Ph-1/Ph-2 as YAGNI; revisit if real customer needs emerge.

---

## Test interface

```
python -m core.src.tracker.tracker_cli --diagnostic
```
Loads the LEGAL_TRANSITIONS matrix; validates against FR-7 spec; verifies all 11 enum values present; tests guard predicates against synthetic snapshots. Emits TRK-RPT:
```
RPT|TRK|run-00001|2026-06-10T10:00:00Z|states_total=11|transitions_total=23|guards_registered=8|pm_approval_field_synced=true|legal_matrix_complete=true|enum_synced_with_template_schema=true
```

```
python -m core.src.tracker.tracker_cli --validate
```
Pydantic-validates the LEGAL_TRANSITIONS matrix against FR-7 prose (compares against a fixture extracted from requirements.md FR-7); flags any drift. Safe in CI.

```
python -m core.src.tracker.tracker_cli --explain-transition --from <state> --to <state> --item-json <path>
```
Loads a serialised item snapshot from JSON; runs `check_transition_guards` against the requested transition; emits TRK-RPT with the GuardResult — for ops debugging "why is item I-1234 stuck at DocumentReceived?":
```
MET|TRK|run-00001|2026-06-10T10:00:00Z|item_id=I-1234|from=DocumentReceived|to=OwnerClosed|allowed=false|blocking=reviews_pending|reason=owner_closed_guard_2c
```

```
python -m core.src.tracker.tracker_cli --simulate --item-id <id>
```
Reads the item from Postgres (read-only); evaluates which transitions are currently allowed; emits a list with reasons. For SP UI engineer's preflight queries: "given current item state, which buttons should be enabled?".

**QC template** (`TRK:transition_quality` — registered in `diagnostics/qc.py`):
```
Fields: from_state (enum: DeliveryState values),
        to_state (enum: DeliveryState values),
        outcome (enum: transitioned / no_op_idempotent / guard_denied / illegal_transition),
        guard_denied_reason (enum: bounded reason codes; null if not guard_denied),
        trigger_source (enum: automated / manual_tpm_override / tpm_button),
        sp_writeback_ok (bool),
        transition_latency_bucket (enum: fast / normal / slow / timeout),
        result (enum: OK / WARN / FAIL — FAIL on illegal_transition or sp_writeback_ok=false; WARN on guard_denied)
```

---

<!-- BEGIN:STRUCTURE -->

- `AuditWriter` — class — pub — Writes CommunicationLog rows per FR-42. Concrete impl: `storage.write_communication_log`.
- `GuardResult` — class — pub — Outcome of a pre-transition guard check.
- `ReassignmentResult` — class — pub
- `SpWriter` — class — pub — Subset of `sharepoint_integration.SpCrud` per [D-064] HILDA->SP REST
- `StateChangeDispatchSignal` — class — pub — Returned on successful state change. Caller fires the canonical
- `StorageWriter` — class — pub — Subset of `storage` module's interface that tracker depends on.
- `TagPropagationResult` — class — pub
- `TransitionResult` — class — pub — Result of an attempted transition.
- `advance_on_doc_count_reached` — func — pub — Convenience wrapper for AttachmentReceived task body when doc_count
- `apply_manual_tpm_override` — func — pub — FR-14 manual TPM field override.
- `auto_advance_owner_closed_to_under_pm_review` — func — pub — Transient-state auto-advance per FR-7. Called inline by
- `check_transition_guards` — func — pub — Returns GuardResult indicating whether (item, target_state) transition
- `instantiate_default_workitem` — func — pub — FR-78 default work-item instantiation.
- `list_blocking_conditions_for_state` — func — pub — Convenience wrapper for SP UI / dashboard surfacing — returns the
- `propagate_tags_to_active_trackers` — func — pub — FR-82 propagation. See module docstring.
- `reassign_document_to_workitem` — func — pub — FR-83 TPM-manual document reassignment via SP UI. See module docstring.
- `transition_legal` — func — pub — Returns True iff `to_state` is a legal transition target from `from_state`.
- `update_delivery_state` — func — pub — Canonical state-transition function consumed by workflow_engine

<!-- END:STRUCTURE -->
