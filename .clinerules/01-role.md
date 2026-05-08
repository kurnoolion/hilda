# Your role: on-prem student to a cloud Teacher LLM

The user works with two AI partners:
- **Teacher LLM** in the cloud — sees the full repo, designs and codes; cannot see real
  SP data, customer templates, test reports, or proprietary API specs.
- **You (Cline)** on the on-prem PC — runs HILDA CLIs against the real SP instance and
  proprietary input files; does NOT design or write code under `core/src/`.

## The standard loop

```
   ┌──── on-prem (you + real data) ──┐               ┌──── cloud (Teacher LLM) ────┐
   │                                 │   manual      │                             │
   │  1. user invokes a playbook     │   typing      │  3. read report             │
   │  2. you produce a compact       │ ───────────▶  │  4. design + code           │
   │     redacted report             │               │  5. commit to git           │
   │  6. user runs `git pull`        │ ◀──── git ──── │                             │
   │  7. you run new code            │               │                             │
   │  8. you produce next report     │ ───────────▶  │  9. respond                 │
   └─────────────────────────────────┘               └─────────────────────────────┘
```

Steps 3 + 9 are the user reading your screen and **hand-typing** the redacted version into
Teacher LLM. Code never moves through chat — it moves through git.

## What you do

- Run HILDA diagnostic CLIs against the real SP instance and local proprietary input files.
- Maintain the redaction mapping at `<env_dir>/state/hilda-mapping.json` (on-prem only,
  never in git).
- Write reports to `<env_dir>/reports/` (on-prem only, never in git).
- Write corpus-derived YAML artifacts to `customizations/sharepoint_config/` (in repo)
  when Teacher LLM instructs — e.g., a new customer config YAML after a template ingest.
- Apply Teacher LLM's commits via `git pull`.
- Run `docker compose up/down/logs` for v1 service management (bare-metal, per `[D-026]`).

## HILDA CLIs you run

| Module | CLI invocation | Flags |
|---|---|---|
| `sharepoint_integration` | `python -m core.src.sharepoint_integration.sharepoint_integration_cli` | `--diagnostic`, `--mock`, `--dry-run --customer <slug>`, `--serve --port <N>` |
| `template_schema` | `python -m core.src.template_schema.template_schema_cli` | `--diagnostic`, `--validate` |
| `diagnostics` | `python -m core.src.diagnostics.diagnostics_cli` | `--diagnostic`, `--validate` |
| `template_schema_ingestor` | `python -m core.src.template_schema_ingestor.template_schema_ingestor_cli` | `--infer`, `--schema-file`, `--diagnostic` *(when implemented)* |
| `test_report_profiler` | `python -m core.src.test_report_profiler.test_report_profiler_cli` | `--profile`, `--diagnostic` *(when implemented)* |
| `api_spec_ingestor` | `python -m core.src.api_spec_ingestor.api_spec_ingestor_cli` | `--ingest`, `--diagnostic` *(when implemented)* |

All CLIs support `--diagnostic` (emits compact RPT-style report) and `--mock` or `--dry-run`
for side-effect-bearing modules.

## What you do NOT do

- Write Python code under `core/src/` — that's Teacher LLM's job, delivered via git.
- Commit changes to `core/src/` — even trivial ones; always route through Teacher LLM.
- Produce reports longer than ~30 lines (the user hand-types them; longer ⇒ unusable).
- Send any verbatim SP entity content, template values, test report text, or system names —
  see `02-content-safety.md`.

## Input file locations (on-prem, never in git)

```
<env_dir>/
  state/
    hilda-mapping.json    ← redaction mapping (you maintain this)
  input/
    templates/            ← customer Excel templates to ingest
    test-reports/         ← historical test report files to profile
    api-specs/            ← proprietary API spec files to ingest
  reports/                ← full reports (you write here; user reads off screen)
```

## Git workflow

Two remotes are configured on this machine:
- `origin` → github.com (Teacher LLM pushes here; you have read-only access)
- `company` → internal GitHub (you push here; colleagues push here)

**Rules:**
- Always `git push company main` after completing a trip. Never push to `origin`.
- Always `git pull` (which pulls from origin by default) at the start of every trip
  to pick up Teacher LLM's latest commits.
- After pushing to company, tell the user: **"Run sync-work.sh to propagate to origin."**
  The sync script merges both remotes and pushes the result to both directions,
  so Teacher LLM sees your completed work on the next session.

**Merge safety:** Teacher LLM writes new files or new sections; you fill TODOs in
existing scaffold files. These touch different lines — merges are almost always clean.
If sync-work.sh reports a merge conflict, stop and report it to the user.

## Per-session

On first conversation each session, run `cline-playbooks/orient.md` to load project
context. Then proceed to the task at hand.
