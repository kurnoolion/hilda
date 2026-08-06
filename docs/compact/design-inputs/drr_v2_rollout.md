# DRR-V2 Verizon-template excel — rollout runbook (DRR-V2-1..7)

Ph-1 architect ask 2026-08-03. Rewrites the DRR closure final-status
Excel to match the Verizon-issued DRR checklist template — carrier
logo + header block (Model / lockdown / req version / dates), section
grouping rows, 6-column body, Phase-1 gating yellow highlights, and
Open/Closed/Completion summary tables.

## What ships in the repo

| Chunk    | Commit  | Files |
| ---      | ---     | --- |
| DRR-V2-1 | cef3859 | core/src/template_schema/template_lookup.py (get_drr_version + get_drr_section_grouping helpers), core/tests/test_template_lookup_drr.py |
| DRR-V2-2 | 9f848c0 | core/src/template_schema/models.py + core/src/storage/db.py (added owner_completion_date; superseded by 6c) |
| DRR-V2-3 | a17a60e | core/src/email_service/inbound/body_parser_table.py + protocol.py + outreach_table.j2 + owner_reply.py (new "Completion Date" outreach column parses + persists), 6 new TestBodyParserTable cases |
| DRR-V2-4 | 50b59b3 | core/src/workflow_engine/tasks/tpm_notification.py (_read_milestone_headers + _read_project_headers), core/tests/test_drr_v2_sp_header_reads.py (12 cases) |
| DRR-V2-5 | 223fd85 | core/src/email_service/outbound/drr_report_excel.py (full rewrite to Verizon-template shape), core/tests/test_drr_report_excel_v2.py |
| DRR-V2-5a| e628397 | drr_report_excel.py fixes from smoke: P1_yellow_marker fill + native-date cells + col-A merges + saturated yellow banner |
| DRR-V2-6 | ea31496 | tpm_notification._build_drr_v2_context + _resolve_logo_path, template_lookup.get_drr_logo_filename, wired _send_notification + send_drr_excel_oneshot.py, core/tests/test_drr_v2_wiring.py (12 cases) |
| DRR-V2-6a| 3e19c65 | scripts/__init__.py (unblock `python -m scripts.<name>` invocation) |
| DRR-V2-6b| f7d04ea | Doc `-w /home/omadm/hilda` flag in one-shot docstring |
| DRR-V2-6c| ae5c64b | Consolidate owner_completion_date -> actual_completion_date across models/db/protocol/parser/task/tests |
| DRR-V2-6d| 542100f | bootstrap_task_deps() in one-shot fresh-process fallback (was: guessed `build_task_deps`) |
| DRR-V2-7 | (this)  | this runbook |

## What you need to do on the staging PC

### 1. Postgres schema migration — REQUIRED before pulling

DRR-V2 reads `delivery_item.actual_completion_date` (pre-existing ORM
column that was never applied to the live table). Apply the ALTER
before restart:

```bash
podman exec -it hilda-postgres psql -U hilda -d hilda -c "ALTER TABLE delivery_item ADD COLUMN IF NOT EXISTS actual_completion_date DATE;"
```

`IF NOT EXISTS` makes it safe to re-run. No data loss — all existing rows get NULL.

### 2. Customer YAML edits (corp-only, not in git)

The Verizon-template header block reads five new SP fields via
canonical names in your `customizations/sharepoint_config/customers/customer.yaml`:

**`milestones.columns`** — add three canonical mappings:

```yaml
    columns:
      # ... existing ...
      fld_lockdown_date: "<your SP internal name>"
      req_version:       "<your SP internal name>"
      # target_date already exists per Module #11 cascade
```

**`projects.columns`** — add two:

```yaml
    columns:
      # ... existing ...
      LE:  "<your SP internal name>"
      FFW: "<your SP internal name>"
```

Missing / blank values render blank cells + WARN log (per Q6 spec 2026-08-03).

### 3. Template.yaml edits — customer + brand

`customizations/template_schemas/<customer_id>/template.yaml` (public
repo — MMK version is tracked in git). Add three root-level keys:

```yaml
customer_id: MMK
MMK_template_version: 5.7               # -> "DRR Version 5.7" in header
drr_branding_logo: verizon.png          # -> resolved against customizations/branding/

# Per-work-item under milestones.DRR.work_items:
milestones:
  DRR:
    work_items:
      - item_no: 1
        item_name: "Product Summary Sheet FINALIZED"
        parent: "Product Documentation Review"    # -> section header row
        P1_yellow_marker: true                    # -> bright FFFF00 fill on Description cell
      # ... etc
```

Guidance:
* `parent` groups items into section rows (Verizon's "Product Documentation Review", "Pre-Submission Items", "Bluetooth Testing", …). Items with no `parent` still render but under a `None` section.
* `P1_yellow_marker: true` on gating items — matches the Verizon template's yellow highlighting for Phase 1 Submission Gating rows.
* Items **85, 86, 87 are hard-filtered** by `get_drr_section_grouping` (85 = Final DRR excel deliverable, 86 = Ph-1 non-DRR-docs placeholder, 87 = Default WI). No need to omit them from template.yaml; they'll be excluded regardless.

### 4. Brand logo — drop the PNG

```bash
mkdir -p customizations/branding
cp <your verizon.png> customizations/branding/verizon.png
```

Common gotcha: if the download was mangled (0 bytes, HTML error page renamed .png, WEBP with .png extension), openpyxl's PIL raises `UnidentifiedImageError`. The excel builder catches it and continues without the logo (WARN logged). Verify:

```bash
file customizations/branding/verizon.png    # expect: PNG image data, WxH, 8-bit/color RGBA, non-interlaced
head -c 8 customizations/branding/verizon.png | od -An -tx1    # expect: 89 50 4e 47 0d 0a 1a 0a
```

### 5. Volume-mount `scripts/` into the worker container (optional but recommended)

The one-shot smoke script under `scripts/send_drr_excel_oneshot.py` isn't in the default worker container volume mount (only `core/`, `config/`, `customizations/`). Two options:

**A. Add to docker-compose.yaml** — permanent, matches `git pull` behavior:

```yaml
    volumes:
      - ./core:/app/core
      - ./config:/app/config
      - ./customizations:/app/customizations
      - ./scripts:/app/scripts        # NEW — enables `-m scripts.*` from container
```

Then `podman-compose up -d --force-recreate hilda-worker`.

**B. `podman cp` each time you want to smoke** — ad-hoc:

```bash
podman exec hilda-worker mkdir -p /app/scripts
podman cp scripts/__init__.py hilda-worker:/app/scripts/__init__.py
podman cp scripts/send_drr_excel_oneshot.py hilda-worker:/app/scripts/send_drr_excel_oneshot.py
```

### 6. Restart the worker to pick up new code

```bash
git pull origin main
podman-compose up -d --force-recreate hilda-worker hilda-beat
```

Beat picks up the tick schedule on restart; worker picks up the DRR-V2 code paths.

## Verification

### Dry-run the one-shot (no email sent, artifacts land in /tmp)

```bash
podman exec -w /app -it hilda-worker python -m scripts.send_drr_excel_oneshot \
    --customer MMK --device SM-S671U1 --milestone DRR \
    --to <tpm-email> --tpm-name "TPM" --dry-run
```

Expected output ends with `DRY RUN — artifacts written to /tmp/drr_oneshot_MMK_SM-S671U1_DRR/`.

Copy the xlsx out to eyeball:

```bash
podman cp hilda-worker:/tmp/drr_oneshot_MMK_SM-S671U1_DRR/DRR_MMK_SM-S671U1_DRR_final.xlsx ./
```

### What to eyeball in the xlsx

* Row 1 `OEM Model` (centered, bold-16, merged A..F).
* Row 2: Verizon logo top-left (blank if PNG was skipped — check WARN log).
* Row 3 `Device Readiness Review` (centered, bold-18, merged A..F).
* Row 4 `DRR Version 5.7` (left, merged A..F).
* Rows 6-8 + 10-12: right-aligned labels in B, values in C. Dates in mm/dd/yy format with NO green triangle in cell corner.
* Row 13: full-band bright yellow banner "Yellow Highlighting indicates Phase 1 Submission Gating items".
* Row 14: red-fill body header (Description of Task / Completion / Current Status / Owner / Remarks).
* Row 15+ section rows (gray fill) alternating with item rows. P1 gating items have their Description cell filled bright yellow.
* Bottom summary tables: Open / Closed / Completion Percentage + per-owner Open counts.

### Live send (real email)

Same command **without** `--dry-run`. Sends via the same EmailSender the beat tick uses. Verify `SENT — message_id=<…>` in stdout + inspect the TPM's mailbox.

### Beat cadence

Once wired, `tpm_notification_tick_task` fires on the beat schedule (default every 5 min) and per-milestone sends only within the day-of / day-before window relative to `milestones.target_date`. Idempotency guaranteed via `CommunicationLog` audit rows keyed on (customer, device, milestone, phase).

## Rollback

If DRR-V2 needs to be reverted without a full git revert:

* **Body-parser Completion Date column**: harmless if template.yaml drops the outreach change — old outreach batches (pre-DRR-V2-3) still parse (backward-compat covered by `test_backward_compat_table_without_completion_date_column`).
* **Excel builder**: legacy 4-column mode is preserved. Callers that pass `section_grouping=None` (the default when `template_lookup.get_drr_section_grouping` returns None for an un-migrated customer) get the pre-DRR-V2 flat sheet.
* **Postgres**: leaving the `actual_completion_date` column in place is safe — no existing code reads it destructively.

## Known-good invocation checklist

```bash
# 1. Schema migration
podman exec -it hilda-postgres psql -U hilda -d hilda -c \
    "ALTER TABLE delivery_item ADD COLUMN IF NOT EXISTS actual_completion_date DATE;"

# 2. Pull + restart
git pull origin main
podman-compose up -d --force-recreate hilda-worker hilda-beat

# 3. Optionally add scripts/ to compose volumes OR ad-hoc copy:
podman exec hilda-worker mkdir -p /app/scripts
podman cp scripts/__init__.py hilda-worker:/app/scripts/__init__.py
podman cp scripts/send_drr_excel_oneshot.py hilda-worker:/app/scripts/send_drr_excel_oneshot.py

# 4. Dry-run smoke
podman exec -w /app -it hilda-worker python -m scripts.send_drr_excel_oneshot \
    --customer MMK --device SM-S671U1 --milestone DRR \
    --to <tpm-email> --tpm-name "TPM" --dry-run

# 5. Verify xlsx
podman cp hilda-worker:/tmp/drr_oneshot_MMK_SM-S671U1_DRR/DRR_MMK_SM-S671U1_DRR_final.xlsx ./
```

## References

* Architect ask: 2026-08-03 spec (Q1..Q6 + item#86 addition 2026-08-04)
* Verizon template: `Q8_VZW_DRR_Checklist_V5.8Feb2024_042926.xlsx` (screenshots from architect 2026-08-04)
* Related DECISIONS: D-141 (template-first field merge), D-158 (`no_customer_upload` semantic + DRR-milestone posture)
* Tests: 109 cases across `test_template_lookup_drr.py`, `test_drr_v2_sp_header_reads.py`, `test_drr_report_excel_v2.py`, `test_drr_v2_wiring.py`, plus 6 DRR-V2-3 additions in `test_email_service.py`
