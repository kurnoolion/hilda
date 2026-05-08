# HILDA / DeliverableHub project context

This repo is **HILDA — DeliverableHub**. Python codebase that automates the end-to-end
deliverable lifecycle for PMs managing multi-customer connected-device certification
programs — template-driven, rule-based automation with human-in-the-loop approval.

Where to read more (in this order, via the `orient` playbook):
- `docs/compact/PROJECT.md` — 1-page identity + Contributors table
- `docs/compact/MAP.md` — module table + Mermaid dependency graph
- `docs/compact/STATUS.md` — active phase, in-progress, flags
- `docs/compact/SYSTEM.md` — process topology, K8s workloads, observability, CI/CD
- `core/src/<module>/MODULE.md` — per-module curated contracts (load on demand)

The project is partnered between a cloud **Teacher LLM** (full design + code) and you
(on-prem Cline, the student with access to real SP data and proprietary artifacts). Your
role and content-safety rules are in `01-role.md` / `02-content-safety.md`.

The existing `docs/compact/` scaffold (COMPACT methodology) is maintained by Teacher LLM
across cloud sessions. You read those artifacts; you do not invoke COMPACT skills.
