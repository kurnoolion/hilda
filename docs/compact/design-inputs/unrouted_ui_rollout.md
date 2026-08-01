# /_unknownTG manual-routing UI — rollout runbook (UR-1..UR-9)

Ph-2 architect ask 2026-08-01. Adds a per-scope triage UI for files that
landed in the router's `_unrouted` bucket, plus a weekly ops digest that
aggregates unrouted counts across every scope.

## What ships in the repo

| Chunk | Commit  | Files |
| ---   | ---     | --- |
| UR-1  | 5bce5ac | core/src/storage/db.py (customer_id + device_id columns on document_index + composite index), core/src/storage/models.py, core/src/storage/document_ops.py |
| UR-2  | ec6c9f9 | core/src/workflow_engine/tasks/inbound_attachment.py (populate the new columns at ingest) |
| UR-3  | ad095da | core/src/storage/unrouted_ops.py (list_unrouted_for_scope, list_route_candidates_for_scope, route_unrouted_to_item, UnroutedStorage), core/tests/test_unrouted_ops.py |
| UR-4  | e824d60 | core/src/dashboard/config.py (manual_routing_excluded_item_names + _milestone_names), core/tests/test_dashboard_config.py |
| UR-5  | 5256dab | core/src/dashboard/document_view_routes.py (GET /_unknownTG/), core/src/dashboard/templates/view_tree_unrouted.html |
| UR-6  | 916dac0 | core/src/dashboard/document_view_routes.py (POST /_unknownTG/route + PRG flash) |
| UR-7  | 9c5632c | core/src/dashboard/templates/view_tree_landing.html (bucket + badge), core/src/storage/unrouted_ops.py (count_unrouted_for_scope, list_all_unrouted_scopes) |
| UR-8  | 0b6d2fb | core/src/workflow_engine/tasks/ops_digest.py, core/src/workflow_engine/tpm_notification_config.py (4 new fields), celery_app.py (beat entry) |
| UR-9  | (this)  | core/tests/test_unrouted_ui_e2e.py, scripts/unrouted_smoke_test.sh, this runbook |

## What you need to do on the staging PC

### 1. Postgres schema migration — REQUIRED before pulling

The UR-1 change adds two nullable columns + one composite index to
`document_index`. Existing rows will have NULL customer_id + device_id
(the ingest path only populates them for NEW attachments starting at
UR-2). Legacy NULL rows are invisible to the /_unknownTG UI — accepted
gap; see UR-9 test for the behavior contract.

Run the migration BEFORE `git pull` + restart so the new code doesn't
hit "column does not exist" errors on the first request:

```bash
podman exec hilda-postgres psql -U hilda hilda -c "
ALTER TABLE document_index
    ADD COLUMN IF NOT EXISTS customer_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS device_id   VARCHAR(64);
CREATE INDEX IF NOT EXISTS ix_di_unrouted_scope
    ON document_index (routing_resolution, customer_id, device_id, milestone_id);
"
```

Idempotent; safe to re-run. If your Postgres user isn't `hilda` swap it
in the `-U` flag.

### 2. Pull + restart

```bash
cd ~/hilda
git pull
podman restart hilda-api hilda-worker hilda-beat
```

All three containers restart because:
- **hilda-api** loads the new UR-5/6/7 routes + templates.
- **hilda-worker** picks up the new `ops_digest` task binding via
  tasks/__init__.py.
- **hilda-beat** re-reads `celery_app.conf.beat_schedule` to register
  the new `ops_unrouted_digest_weekly` entry.

nginx-hilda doesn't need restart — no new location blocks in this
cascade (everything routes through the existing dashboard upstream).

### 3. Configure DashboardConfig exclusions (UR-4)

The /_unknownTG target-item dropdown excludes:
- Confirmation items (always)
- Default work items (always)
- Any item_name in `manual_routing_excluded_item_names`, scoped to the
  milestones in `manual_routing_excluded_milestone_names` (or globally
  when the milestone list is empty).

MMK's item 85 goes in the DRR-only bucket per architect ask:

```bash
# Add to ~/hilda/config/hilda-api.env (or wherever you set container env)
HILDA_DASHBOARD_MANUAL_ROUTING_EXCLUDED_ITEM_NAMES="Item 85"
HILDA_DASHBOARD_MANUAL_ROUTING_EXCLUDED_MILESTONE_NAMES="DRR"
```

Both env vars accept a comma-separated list. Leave unset (default empty)
until you have a real exclusion to add. Restart hilda-api after editing.

### 4. Configure ops weekly digest recipient (UR-8)

The ops digest emails aggregated unrouted counts to a single mailbox
every 7 days. Empty recipient short-circuits the tick — safe to leave
unset until ops mailbox is ready.

```bash
# ~/hilda/config/hilda-beat.env (or wherever hilda-beat reads env)
HILDA_TPM_NOTIFICATION_OPS_UNROUTED_DIGEST_RECIPIENT="ops-hilda@sea.samsung.com"

# Optional tunables (defaults shown):
HILDA_TPM_NOTIFICATION_OPS_UNROUTED_DIGEST_ENABLED=true
HILDA_TPM_NOTIFICATION_OPS_UNROUTED_DIGEST_BEAT_INTERVAL_SECONDS=604800  # 7d
HILDA_TPM_NOTIFICATION_OPS_UNROUTED_DIGEST_MIN_COUNT=1                   # noise floor
```

Restart hilda-beat AND hilda-worker after editing:

```bash
podman restart hilda-beat hilda-worker
```

To fire the digest immediately for verification (bypasses the 7d wait):

```bash
podman exec hilda-worker python -c "
from core.src.workflow_engine.tasks.ops_digest import ops_unrouted_digest_tick_task
print(ops_unrouted_digest_tick_task(None, None))
"
```

Expect one of:
- `{"outcome": "sent", "scopes_scanned": N, ...}` — digest went out
- `{"outcome": "no_recipient", ...}` — env var not set yet
- `{"outcome": "no_scopes_over_threshold", ...}` — nothing unrouted
- `{"outcome": "disabled", ...}` — flag off

### 5. Run the smoke test

```bash
HILDA_BASE=https://<your-hilda-host> bash scripts/unrouted_smoke_test.sh
```

Exits 0 on green. Read-only by design — it verifies the routes are
reachable + templates render, but doesn't POST a real route (would
need a seeded unrouted doc). The script's footer prints a manual-
verification checklist for the POST path.

## What ops does day-to-day

### Manually route a file from the UI

1. TPM (or ops) opens `https://<hilda>/browse/<c>/<d>/<m>/_unknownTG/`.
2. Picks a target work item from the dropdown (Confirmation + Default +
   configured-excluded items are pre-filtered).
3. Clicks **Route**. Backend moves the file from `_unrouted/` to the
   target item's `_staged_classification/` on NSD, creates the
   `document_item_association` row, updates `document_index.routing_
   resolution` to `TPM_REASSIGNED`, and writes an audit row
   (`manual_route_from_unrouted`).
4. Browser redirects back to `/_unknownTG/` with a green "Routed to
   <item>" banner + the row disappears from the list.

**No state-machine re-evaluation** is triggered here (per architect
ask 2026-08-01 -- keeps route_unrouted_to_item storage-pure). If the
target item now has enough docs to advance state, that will be picked
up by the next reconciler tick (~5min). Ph-2 follow-up.

### Audit trail

Every route action writes to `communication_log`:

```sql
SELECT
    timestamp, delivery_item_id, credential_id AS tpm,
    summary,
    attachments->0->>'file_hash' AS file_hash,
    attachments->0->>'target_nsd_path' AS nsd_target
FROM communication_log
WHERE action_type = 'manual_route_from_unrouted'
ORDER BY timestamp DESC
LIMIT 20;
```

Weekly digest sends also audit:

```sql
SELECT timestamp, details
FROM communication_log
WHERE action_type = 'ops_unrouted_digest_sent'
ORDER BY timestamp DESC;
```

### Add / remove item_name from the exclusion list

Edit `HILDA_DASHBOARD_MANUAL_ROUTING_EXCLUDED_ITEM_NAMES` +
`_MILESTONE_NAMES` env vars (Step 3 above), restart hilda-api. No
code deploy needed.

## Failure modes + fixes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `500` on GET /browse/.../ with `column "customer_id" does not exist` | UR-1 migration didn't run | Step 1 above; ALTER TABLE is idempotent |
| /_unknownTG/ empty even though files landed in `_unrouted/` | Files ingested BEFORE UR-2 shipped -- legacy rows lack customer_id/device_id | Accepted gap. New ingest populates the columns; old files remain invisible to the UI. If needed, backfill via SQL from the doc_index NSD path (parsers left for ops discretion). |
| POST /route returns `500` with "python-multipart" in log | Container missing python-multipart | `pip install python-multipart` in the container, then restart |
| Dropdown shows Confirmation items | UR-3 filter regressed | Check `list_route_candidates_for_scope` still filters ItemType.CONFIRMATION.value + DEFAULT.value; run `pytest core/tests/test_unrouted_ops.py -k excludes_confirmation -v` |
| /_unknownTG target dropdown missing item that TPM expects | Exclusion env var too broad | Verify `HILDA_DASHBOARD_MANUAL_ROUTING_EXCLUDED_ITEM_NAMES` -- items match by exact item_name string |
| Digest email never arrives | No recipient set OR total < min_count OR email_sender not wired | Fire manually (Step 4) and read the return outcome |

## Ph-2 candidates (not blocking Ph-2 UR rollout)

- Trigger state-machine re-evaluation after successful manual route
  (currently deferred to next reconcile tick).
- Move manually-routed file into the view tree (currently lands in
  the internal staged_classification path only, so landing tg-tables
  don't show it until reconciler runs).
- Backfill script for pre-UR-1 legacy document_index rows.
- Ops UI (or CLI) to view the digest without waiting for the weekly
  email.
- Per-scope digest opt-in (some scopes may not need the alert).
