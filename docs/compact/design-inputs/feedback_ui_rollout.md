# /feedback/* early-access UI — rollout runbook (FB-1..FB-6)

Ph-1 architect ask 2026-07-30. 5 TPMs, one URL per (customer, device,
milestone) tuple, submit + view + attachment, no auth, BOT self-email
notification on submit.

## What ships in the repo (FB-1..FB-5 already committed)

| Chunk | Commit  | Files |
| ---   | ---     | --- |
| FB-1  | e5bdf80 | config/feedback_bug_types.json, core/src/dashboard/feedback_config.py, core/tests/test_feedback_config.py |
| FB-2  | 58c5549 | core/src/storage/db.py (FeedbackTicketTable), core/src/storage/feedback_ops.py, core/tests/test_feedback_ops.py |
| FB-3  | 72370aa | core/src/dashboard/feedback_routes.py, core/src/dashboard/app.py (route registration + guard), core/src/dashboard/templates/feedback/page.html (minimal), core/tests/test_feedback_routes.py |
| FB-4  | f6de0fd | core/src/dashboard/templates/feedback/page.html (polished; cascading dropdown + inline image thumbs) |
| FB-5  | d0c73a7 | core/src/dashboard/app.py (email_sender + credential_service wiring), core/src/dashboard/feedback_routes.py (_notify_bot_of_new_ticket helper) |
| FB-6  | (this)  | scripts/feedback_smoke_test.sh, docs/compact/design-inputs/feedback_ui_rollout.md |

## What you need to do on the staging PC

### 1. Pull + restart hilda-api

```bash
cd ~/hilda
git pull
podman restart hilda-api
```

`hilda-worker` and `hilda-beat` don't need restart — dashboard is the only
container touched by FB-1..FB-6.

### 2. Add the nginx block for /feedback/

Edit `~/hilda/deploy/nginx/nginx.conf` (or wherever your nginx-hilda mounts
its config). Add a location block if it doesn't already exist. Two things
matter:

- `client_max_body_size 6m;` — attachments are capped at 5MB in FastAPI;
  nginx default is 1MB, which would 413 before hilda-api ever sees the
  request. 6M leaves a bit of multipart overhead headroom.
- proxy_pass to the hilda-api upstream that other dashboard routes use.

Sample block (adjust upstream name / path to match your existing config):

```nginx
location /feedback/ {
    client_max_body_size 6m;

    # Reuse the same upstream as /docs/*, /browse/*, /dl/* etc.
    proxy_pass http://hilda-api:8443;
    proxy_http_version 1.1;

    # Standard proxy headers so hilda-api sees the real client info
    proxy_set_header Host              $host;
    proxy_set_header X-Real-IP         $remote_addr;
    proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;

    # Attachments are small (<= 5MB) but streaming friendly
    proxy_request_buffering off;
    proxy_read_timeout      60s;
}
```

Reload nginx (does NOT drop existing connections):

```bash
podman exec nginx-hilda nginx -t && podman exec nginx-hilda nginx -s reload
```

If your nginx already has a catch-all `location / { proxy_pass ... }`
block that routes everything to hilda-api, the ONLY change strictly
required is bumping `client_max_body_size` (either at the http/server
scope or on a `/feedback/` block that overrides). Everything else is
already routed.

### 3. Verify email BOT wiring

Feedback submit tries to email a notification to the BOT self address on
success (best-effort — failure is logged and swallowed; ticket create still
succeeds). Requires `email.enc.env` + sops age key + reverse-proxy origin
configured. Check hilda-api boot log for one of these lines:

```bash
podman logs --tail 50 hilda-api | grep -iE "feedback email_sender|feedback notify"
```

- `feedback email_sender NOT wired` → email config missing; submits will
  succeed without notify.
- No line at all → wired successfully (only failure paths log).

### 4. Run the smoke test

```bash
HILDA_BASE=https://<your-hilda-host> bash scripts/feedback_smoke_test.sh
```

Exits 0 on green; loud failure with diagnostic on the first bad step.
Leaves 2-3 real tickets in postgres (visible in the view page) — treat
them as manual QA evidence. Delete via SQL later if you want:

```sql
DELETE FROM feedback_ticket WHERE description LIKE 'smoke-test%';
```

## What ops does day-to-day

### Change a ticket's status

Ph-1: status transitions are ops-managed via SQL. TPMs only submit + view.

```sql
-- Moving to in-process (with an optional short note explaining what's happening):
UPDATE feedback_ticket
   SET status = 'in-process',
       updated_at = now(),
       resolution_note = 'Working on it -- suspected sync-3 race. ETA end of week.'
 WHERE ticket_id = 'MMK-SM-A012U-DRR-3';

-- Closing a ticket -- ALWAYS include resolution_note so the TPM knows why.
-- The view page renders resolution_note verbatim in a "Resolution" column;
-- if you close without a note, the TPM sees a "— (no note provided)"
-- placeholder which is fine for trivial dupes but confusing for real fixes.
UPDATE feedback_ticket
   SET status = 'closed',
       updated_at = now(),
       resolution_note = 'Fixed in commit abc1234 -- deploy tomorrow after DRR window.'
 WHERE ticket_id = 'MMK-SM-A012U-DRR-3';
```

Valid status values: `open` (default), `in-process`, `closed`.

The `resolution_note` field is Text (unlimited length), rendered as
pre-wrapped text so line breaks are preserved. Keep it short and
TPM-readable -- ops-jargon-heavy notes belong in the audit log, not here.

### Add / remove a bug type

Edit `config/feedback_bug_types.json` and restart hilda-api. No code
deploy needed. Loader validates structure on load; malformed JSON produces
a clear ValueError on next request.

### Add a new milestone to the dropdown

Ph-1 hardcodes `["DRR"]` in `feedback_routes.py:_MILESTONE_DROPDOWN_OPTIONS`.
Add new milestones there and restart hilda-api. Ph-2 will read this from
template_lookup._CACHE if / when we wire template loading into hilda-api.

### Give TPMs their URLs

Each TPM gets ONE URL — their assigned (customer, device, milestone) tuple.
Example:

```
https://<hilda-host>/feedback/MMK/SM-A012U/DRR
https://<hilda-host>/feedback/MMK/SM-M456U/DRR
https://<hilda-host>/feedback/MMK/SM-A012U1/DRR
```

Anyone with the URL can view + submit -- no auth (5-TPM early-access
scope). Not designed for wider audience.

## Failure modes + fixes

| Symptom | Cause | Fix |
| --- | --- | --- |
| `413 Request Entity Too Large` on submit with attachment | nginx `client_max_body_size` < 5MB | Step 2 above; set to 6m; reload nginx |
| `500` on submit, hilda-api log shows `Form data requires "python-multipart"` | container missing python-multipart | in container: `pip install python-multipart` then restart |
| No email delivered on submit | email_sender not wired OR EWS transient failure | check hilda-api boot log for `feedback email_sender NOT wired`; check credential_service can decrypt email.enc.env |
| Bug-type dropdown empty | `config/feedback_bug_types.json` missing or malformed | verify file exists + `python -c "from core.src.dashboard.feedback_config import flat_bug_types; print(len(flat_bug_types()))"` -- expect 24 |
| Ticket seq skipping numbers | UniqueConstraint race retry consumed a seq | benign; seq is display-only, no gap risk to logic. Rare at 5-TPM scale. |
| Attachment download 404 across scope | scope-check preventing cross-scope leak (by design) | expected; only the ticket's own scope URL serves its attachment |

## Ph-2 candidates (not blocking Ph-1)

- Read milestone dropdown from template_lookup rather than hardcode
- Ops UI (or CLI) to change status without SQL
- Multi-attachment per ticket (currently 1)
- Attachment on-disk store instead of Postgres bytea (if volume grows)
- Auth (SSO) if the URL surface widens beyond 5 TPMs
