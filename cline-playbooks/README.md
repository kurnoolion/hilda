# Cline playbooks

These are the structured tasks the user invokes against the on-prem SP instance, customer
templates, and proprietary files. Each playbook is self-contained: a brief, the steps,
the report template.

The always-on rules in `.clinerules/` (loaded automatically) govern Cline's role,
content-safety / redaction protocol, and output discipline. The playbooks here are invoked
**manually** per task.

## How to invoke

In your Cline conversation, paste the playbook path and the input:

> *"Follow `cline-playbooks/sp-connect.md` for customer `<CUST0>`."*

Cline reads the playbook and executes the steps.

## Playbooks

| File | Purpose | Output |
|---|---|---|
| [`orient.md`](orient.md) | Load HILDA project context (run on first conversation per session) | `ORIENT` report (~5–8 lines) |
| [`mapping.md`](mapping.md) | Maintain the redaction table at `<env_dir>/state/hilda-mapping.json` | `MAP` report (~2–4 lines) |
| [`sp-connect.md`](sp-connect.md) | Test SP connectivity; inspect list inventory and schemas | `SP-RPT` report (~10–15 lines) |
| [`ingest-template.md`](ingest-template.md) | Run Template Schema Ingestor on a customer template file | `TSI-RPT` report (~12–18 lines) |
| [`profile-test-report.md`](profile-test-report.md) | Profile historical test report files via Test Report Profiler | `TRP-RPT` report (~12–15 lines) *(pending impl)* |
| [`ingest-api-spec.md`](ingest-api-spec.md) | Ingest a proprietary API spec via API Spec Ingestor | `ASI-RPT` report (~10–15 lines) *(pending impl)* |
| [`develop-issue-tracker-adapter.md`](develop-issue-tracker-adapter.md) | Verify IssueTracker adapter scaffold: import check → unit tests → CLI contract (C01–C10) | `ITR-RPT` report (~8–12 lines) |
| [`debug-pipeline.md`](debug-pipeline.md) | Run one or more HILDA processing stages; capture stats | `PIPE-RPT` report (~15–25 lines) |
| [`share-back.md`](share-back.md) | Bundle multiple reports for one typing trip into Teacher LLM | `BUNDLE` report (≤40 lines) |
| [`placeholder-convention.md`](placeholder-convention.md) | Reference document — placeholder convention for proprietary identifiers in `customizations/` scaffolds (`<SYS0>`, `<CUST0>`, `<URL0>`, etc.). Not a playbook to invoke; read on demand when reviewing scaffolds or producing compact reports. | — (reference doc) |

## Workflow loop

```
   ┌──── on-prem (Cline + real data) ──┐               ┌──── cloud (Teacher LLM) ────┐
   │                                   │   manual      │                             │
   │  1. user invokes a playbook       │   typing      │  3. read report             │
   │  2. Cline produces compact        │ ───────────▶  │  4. design + code           │
   │     redacted report               │               │  5. commit to git           │
   │  6. user runs `git pull`          │ ◀──── git ──── │                             │
   │  7. Cline runs new code           │               │                             │
   │  8. Cline reports follow-up       │ ───────────▶  │  9. respond                 │
   └───────────────────────────────────┘               └─────────────────────────────┘
```

Steps 3 + 9 ("read") are manual — the user types the redacted report from Cline's screen
into Teacher LLM. Code never moves through chat — only through git.

## Per-session bootstrap

Run `orient.md` first each session. Then proceed to the task.

## Playbook availability

Playbooks marked *(pending impl)* reference modules not yet implemented. Run them once the
corresponding MODULE.md has been drafted and code committed. Check `docs/compact/STATUS.md`
for implementation status.

## Adding new playbooks

When a recurring on-prem task emerges that doesn't fit any existing playbook:

1. Capture the steps once with the user manually.
2. If the same shape repeats 3+ times, ask Teacher LLM to draft a playbook file.
3. Commit to `cline-playbooks/`.
4. Add the row to the table above.
