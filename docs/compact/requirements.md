# Requirements

Last updated: 2026-04-30. Behavioral specs only — project identity and scope live in `PROJECT.md`.

<!--
How to use this file:

- Each requirement has a stable ID. IDs are never reused and never renumbered.
  - New functional requirement → next `FR-N`.
  - New non-functional requirement → next `NFR-N`.
- One sentence per requirement. Active voice. Testable where possible.
- Removed requirements are struck through in place:
    ~~**FR-3** — <original text>~~ (removed YYYY-MM-DD: <reason>)
- Items agreed to postpone go under `## Deferred` — they are not drift.
- `drift-check` reads this file. Keep it current; it is the authority for what the
  system is supposed to do, which design and implementation are checked against.

This file is a skeleton. The requirements phase will populate it from
`docs/compact/design-inputs/HILDA_Design.md`. Strong starting material:
- §2 Current vs. To-Be workflow table → user-facing FR candidates
- §5 Generalized Workflow stages → end-to-end FR candidates
- §8 Orchestration & Automation Engine rule table → rule-engine FR candidates
- §9 Human-in-the-Loop matrix → PM-approval-gate FR / NFR candidates
- §10.7 Security Controls → credential / encryption NFR candidates
- §15 Success Metrics → some translate to NFRs with measurable thresholds

NFR candidates anchored by DECISIONS already captured (to materialize during requirements phase as `NFR-N` entries):
- Chat-mediated collaboration: every cross-boundary artifact has a compact-format counterpart (RPT / MET / FIX / QC) with no proprietary content; every service/module failure emits a stable prefixed error code from the central registry. (Anchors: `[D-002]`.)
- Adapter pattern: dev LLM never reads proprietary API specs; proprietary adapters are produced by the on-prem API Spec Ingestor in `customizations/`. (Anchors: `[D-003]`.)
- SharePoint deployment-specific values live in `customizations/sharepoint_config/`, never hard-coded in `core/`. (Anchors: `[D-004]`.)
- Independent testability: every functional module ships `<module>_cli.py` with `--diagnostic` and `--mock` / `--dry-run` for side-effect operations; every UI / web-facing module ships a mock web harness exercising it without production access. (Anchors: `[D-005]`.)
-->

## Functional

- **FR-1** — TODO: populate during requirements phase.

## Non-functional

- **NFR-1** — TODO: populate during requirements phase. Likely v1 candidates: data sensitivity / on-prem-only boundary; per-PM credential isolation (AES-256 at rest, never in SharePoint); PM-approval gate on all customer-facing outbound actions; full audit trail via `CommunicationLog` for every external action; SharePoint-2017 frozen-version compatibility; near-real-time tracking SLO (target latency between owner reply and dashboard update).

## Deferred

<!--
Requirements explicitly postponed. Not drift. Drift-check surfaces these as notes.

Entry format:
- **FR-N** — <requirement> (deferred: <why> — revisit: <trigger or date>)
-->

<!-- (none yet) -->
