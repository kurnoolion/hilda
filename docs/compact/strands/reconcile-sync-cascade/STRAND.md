# reconcile-sync-cascade

**Status:** in-flight
**Opened:** 2026-07-02
**Landed:**
**Assignees:** ai-math-01
**Target modules:** workflow_engine
**Active phase:** development

## Summary

Reconciliation-sweep backstop for missed SP alert emails (D-142/D-143 implementation). Single meta-reconciler Celery beat task running serial per (customer × device × milestone) tuple, dispatching 5 sync sub-tasks per tick:

- **sync-1 delivery_item_count** — compares SP Deliverables list vs Postgres natural-key set for the milestone; imports any missing item. Fires while `milestone_submission_triggered_at IS NULL` on the SP Milestone row (i.e., until TPM clicks Submit-to-Carrier). Covers both the "initial burst" case (250-concurrent ADDED alerts) AND the "delayed alert trickles in mid-collection" case. Ignores TPM add/delete for Ph-1. Resolves SP milestone_id via (carrier, project_model, milestone_name) triple from template.yaml → Milestones SP list lookup. Includes Default WI (item_type=Default). Compares by natural-key set (not just count).
- **sync-2 milestone-start-collection** — fires kickoff only when `milestone_collection_started_at` set >5min AND **ALL** items still in Not Started. Missed-email backstop only; the existing flow (kickoff_collection_task) guarantees eventual kick-off when the email IS received. Fires at most once per (customer × milestone).
- **sync-3 deliverable-approved** — per-item; when SP shows `delivery_state=ReadyForSubmission + pm_approval_at + pm_approval_pm_id` set >5min but Postgres still `UnderPMReview`, invoke `apply_pm_approval_task` with `trigger_source="sync_backfill_pm_approval"`. Rewind case (SP cleared pm_approval_at) does not fire because guard requires the timestamp non-null.
- **sync-4 milestone-submit-to-carrier** — fires `submit_to_carrier_task` only when `milestone_submission_triggered_at` set >5min AND **ALL** items still in `ReadyForSubmission` (i.e., zero items reached `SubmittedToCustomer`). If any single item transitioned to `SubmittedToCustomer`, the submit email was received — reconciler dies.
- **sync-5 milestone-close-all-items** — fires `close_all_items_task` only when `closed_all_items_triggered_at` set >5min AND **ALL** items still in `SubmittedToCustomer` (or `RFS + no_customer_upload`). If any single item transitioned to `Closed`, the close email was received — reconciler dies.

**No retry limits** on any sync — the reconciler is a missed-email safety net; if the email lands, the task naturally terminates on next tick when its predicate becomes false.

HILDA operates in UTC; elapsed comparisons against SP timestamp values use UTC. Reconciliation task naturally no-ops per tick when drift conditions don't apply — no persistent "stopped" flag.

## Cross-cutting design decisions (locked)

- **Single meta-reconciler beat entry** running serial per (customer × device × milestone) tuple, dispatching 5 sync sub-tasks in order. NOT 5 separate beat entries.
- **Serial per customer** within one task tick.
- **Terminate on convergence** = task naturally no-ops when predicates aren't met; no persistent flag.
- **Trigger-source attribution**: each sync fires downstream tasks with distinct `trigger_source="sync_backfill_*"` for audit differentiability. Guards (D-140 pattern) accept these as authoritative via trust-list extension.
- **First-run-after-boot**: no delay; trust idempotency at each downstream task's state-filter guard.
- **Observability**: each sync emits `sync_backfill_dispatched` audit row when it takes action.
- **Config file**: `config/reconcile.json` — enabled + interval + elapsed threshold per sync (3-tier precedence per storage.json pattern).

## Testing plan

Architect tests tomorrow (2026-07-03) whether missed-email is a real production concern:
- If real → merge this strand into main; deploy reconciliation cascade
- If not observed → leave the strand as-is; revisit if the concern surfaces later

Ph-2 direction (post-Ph-1-stable) is HILDA-native TPM UI, which eliminates SP for deliverables/milestones entirely and makes this whole strand's motivation moot. Separate later cascade.

## Open questions

- **[OQ-1] Late-arriving item never kicked off** — sync-1 imports a delayed ADDED alert AFTER kickoff fired (majority already OutreachSent). Sync-2 predicate ("ALL items in Not Started") won't fire because most items already advanced. Late item sits in Not Started forever. Options: (a) add a sync-2b "kickoff any single item stuck in Not Started when milestone_collection_started_at set >5min ago" partial-catch-up variant; (b) accept as edge case for Ph-1 (TPM re-triggers Start Collection). To decide during implementation.
- **[OQ-2] Same class of "late-arriving CHANGED alert mid-batch"** — same issue could apply to pm_approval / submit-to-carrier / close-all if a late CHANGED alert arrives after the reconciler's ALL-in-pre-transition-state predicate has been broken by an earlier alert. Whether this needs partial-catch-up variants of sync-3/4/5 too, or accept as edge case.
- ~~**[OQ-3] SP Milestones list scope**~~ **RESOLVED 2026-07-02**: SP Milestones is a **global** list (single list across all customers); Deliverables + Projects are per-customer (`Deliverables_<customer_id>` / `Projects_<customer_id>`). Reconciler's milestone-row lookup queries the single global list; filter by (carrier=customer_id, project_model=device_id, Title=milestone_name) triple to disambiguate.
- **[OQ-4] SP-READ throttling budget under reconciliation load** — meta-reconciler + 5 sub-syncs × 5 customers × 24 ticks/hour = 600 SP GET calls/hour baseline. Bounded by SP throttle budget (~60k req/hr per tenant per doc). Monitor at scale.

## Notes

(empty — appended over strand's lifetime by close-session)
