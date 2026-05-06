# System Architecture

*Companion to `PROJECT.md` (what / why), `MAP.md` (modules + dependency graph), `structure-conventions.md` (code layout). This doc owns: **process topology, inter-component communication, deployment, observability, secrets flow, CI/CD, egress**. Decisions land as `D-XXX` entries in `DECISIONS.md`; risks land as Flags in `STATUS.md`.*

*Anchors `HILDA_Design.md` §6 (Solution Architecture), §8 (Orchestration), §11 (Deployment), §12 (Configurability). Where this doc deviates from the design input, the conflict is logged below and resolved via `D-XXX`.*

---

## Conflicts with `HILDA_Design.md` (the input is older than v1 simplifications)

| # | Design-input claim | Current state | Resolution |
|---|---|---|---|
| C1 | §6.2 / §7.1 use **Microsoft Graph API** | SP 2017 frozen → REST API + NTLM/Kerberos | Resolved by `[D-006]` |
| C2 | §11 specifies **HashiCorp Vault** for credentials | v1 simplified to K8s Secrets, Vault deferred to v2 | Resolved by `[D-019]` |
| C3 | §11 lists **12 separate deployments** (microservices) | v1 = modular monolith, 3 deployable workloads (api / worker / llm-gateway); design-doc 12-pod inventory preserved as v2+ target | Resolved by `[D-021]` |
| C4 | §11 specifies **Temporal** as workflow engine | v1 = Celery + Redis broker + Postgres backend; Temporal deferred to v2 if multi-step durable orchestration emerges | Resolved by `[D-022]` |

---

## §1 — Two-pillar topology (stable, anchored in `HILDA_Design.md` §6)

```
SharePoint 2017 layer            ← PM / TPM-facing UI + entity store + document library
        ▲
        │   SP REST API + NTLM/Kerberos  (per [D-006])
        ▼
Kubernetes automation layer      ← all HILDA backend services
```

- **SharePoint owns**: `Devices`, `Milestones`, `Deliverables`, `DeliveryItems`, `Customers`, `CustomerTemplates`, `AutomationRules`, `Users`, `PMCredentials` (entity rows); document library (current state in `[D-013]`: shifted to `\\share\hilda\` shared drive — design doc's "SharePoint Document Libraries" superseded by `D-013` for artifacts but SP still holds entity rows).
- **K8s owns**: every module in `core/src/` and `customizations/`, plus infra services (Postgres, Redis, optional Temporal).
- **Boundary**: K8s services read+write SP via `core/src/sharepoint_integration/`. No K8s service holds canonical entity state — SP is source-of-truth for entities; HILDA's own state (BATCH-ids, CommunicationLog, idempotency, eval-data, AutomationRule run history) lives in Postgres.

---

## §2 — Process granularity *(Decided — `[D-021]`)*

**Decision**: modular monolith — **one container image**, **three K8s Deployments**:

| Workload | Role | Replicas | Hosts which modules |
|---|---|---|---|
| `hilda-api` | FastAPI/uvicorn; dashboard backend, SP-mediated download endpoint per `[D-013]`, inbound webhooks (messenger / issue-tracker callbacks) | 2 | `dashboard`, plus in-process imports of `sharepoint_integration`, `tracker`, `rule_engine`, `template_schema`, `credential_service`, `storage`, `diagnostics` |
| `hilda-worker` | Async-job runner (Celery / RQ — engine TBD in §4); scheduled rule firings, mailbox polling, ingestor jobs, customer-adapter polling, blocking IO | 2 + 1 beat | `email_service`, `messenger`, `issue_tracker`, `customer_adapter`, `workflow_engine`, all three Ingestor / Profiler modules; same in-process imports as api where shared |
| `hilda-llm-gateway` | Sole egress path to runtime LLM `[D-007]` and on-prem code-gen LLM; rate-limiting, retries, prompt templates; owns LLM API-key Secret | 2 | `llm` module |

**Key consequences** (full text in `[D-021]`):
- All 18 modules importable from any pod via `core.src.<module>` — process boundary is at start-command level, not Python-package level.
- Module Protocol boundaries already in place (`[D-008]`, `[D-009]`, `[D-019]`, `[D-020]`) preserve a mechanical v2 split path: extract module + add thin REST surface; call sites don't change.
- Per-customer adapter pods deferred until customer 2 (`DEF-8`).
- `credential_service` stays in-process v1 per `[D-019]`; gets its own Deployment when Vault swaps in v2.
- Each `MODULE.md` adds a curated subsection naming which workload(s) host it.
- `HILDA_Design.md` §11's 12-pod inventory preserved as v2+ target shape, not v1.

---

## §3 — Inter-component communication *(follows from §2; specific async-engine TBD in §4)*

Given the three-workload split:

| From → To | Mechanism | Notes |
|---|---|---|
| `hilda-api` ↔ `hilda-worker` | Celery via Redis broker; results / state in Postgres per `[D-022]` | Async fan-out for reminders, ingest jobs; scheduled triggers via `hilda-beat` singleton |
| `hilda-api`, `hilda-worker` → `hilda-llm-gateway` | Internal HTTP (cluster DNS) | LLM gateway is the only egress path for runtime LLM |
| Any pod → SharePoint | `sharepoint_integration` lib (in-process import) → external HTTPS | Corp NTLM/Kerberos |
| Any pod → Postgres / Redis | Standard drivers, in-cluster service DNS | |
| External email server → `hilda-worker` | Polling (no inbound webhook in v1) | `email_service` worker task |
| External messenger / issue tracker → `hilda-api` | HTTPS webhook to `/webhooks/<adapter>` | Adapter dispatches to in-process handler |
| Customer systems → HILDA | Customer adapter (per-customer); v1 = email-only customer per `DEF-7` | |

**Open**: do we need an outbound queue for SP writes (rate-limit / retry surface), or is in-process retry in `SpClient` (already implemented) enough? Probably enough for v1; revisit if SP throttling shows up.

---

## §4 — Persistence substrate *(partly TBD — `D-XXX` for workflow engine)*

| Store | Role | Schema owner |
|---|---|---|
| **SharePoint Lists** | Canonical entity store (Devices, DeliveryItems, etc.) per `[D-006]` + `HILDA_Design.md` §3 | Customer YAML in `customizations/sharepoint_config/<customer>.yaml` defines list+column names |
| **PostgreSQL** | HILDA-internal state: BATCH-id idempotency, `CommunicationLog`, eval-data corrections, AutomationRule run history, queue persistence (if not Redis-only) | Owned by `core/src/storage/` (Layer 1, not yet drafted) — Alembic migrations |
| **Redis** | (a) async-job broker (Celery/RQ); (b) short-TTL dedup cache for inbound email idempotency keys per `[D-012]`; (c) optional rate-limit token buckets | No durable state — Postgres is authoritative |
| **Shared file system (`\\share\hilda\`)** | All artifacts: test reports, tech reports, waivers, submission packages per `[D-013]` | `hilda-svc` AD service writes; HILDA-mediated reads via `https://hilda.corp/dl/<token>` per NFR-16 |

**Workflow engine** *(Decided — `[D-022]`)*: **Celery + Redis broker + Postgres result backend.** `hilda-beat` (singleton Deployment) loads cron-style triggers from the SP `AutomationRules` list at startup and `SIGHUP`-triggered refresh. Event-triggered rules (webhooks, attachment-received, PM-approval-clicked) enqueue Celery tasks directly from the originating handler in `hilda-api`. `core/src/workflow_engine/` owns the Celery app, task decorators wired to `[D-002]` error codes (WFL prefix), beat schedule loader, and event dispatcher; `core/src/rule_engine/` stays a pure-Python rule-condition evaluator. `HILDA_Design.md` §11's Temporal StatefulSet is removed from v1 topology; Temporal remains a v2+ candidate if rule complexity grows past ~30 rules with cross-step durable state.

---

## §5 — Deployment topology *(follows from §2; final inventory pending §4)*

Given §2's three-workload split and assuming §4 = Celery+Postgres:

| Workload | K8s kind | Replicas | Notes |
|---|---|---|---|
| `hilda-api` | Deployment | 2 | FastAPI; ingress-exposed |
| `hilda-worker` | Deployment | 2 | Celery worker pool |
| `hilda-beat` | Deployment | 1 | Celery beat singleton per `[D-022]`; loads schedule from SP `AutomationRules` |
| `hilda-llm-gateway` | Deployment | 2 | Egress to on-prem LLM via corp proxy per `[D-007]` |
| `postgresql` | StatefulSet | 1 + 1 replica (or operator-managed) | |
| `redis` | Deployment | 1 (v1) → HA later | |
| (deferred to v2) `vault` | StatefulSet | 3 | Per `[D-019]`; v1 uses K8s Secrets |
| (deferred to N=2 customers) per-customer adapter | Deployment | 1 each | |

**Constants regardless of §2 outcome**: Postgres, Redis, ingress controller, cert-manager (or corp PKI integration).

---

## §6 — Observability *(Decided — `[D-023]`)*

**Decision**: light stack, zero new HILDA-owned pods, dashboards/alerts as code under `deploy/`.

**Three signal channels:**
- **Logs** — structured JSON to stdout from every pod; cluster's existing log forwarder ships to whatever corp log store exists. Required fields: `ts`, `level`, `pod`, `module`, `error_code`, `run_id`, `pm_id` (never credentials).
- **Metrics** — `/metrics` endpoint per pod via `prometheus_client`. Required families: `hilda_request_total`, `hilda_celery_tasks_total`, `hilda_pipeline_errors_total{code}` (the `[D-002]` integration), `hilda_llm_calls_total`, `hilda_sp_request_total`, `hilda_credential_expiry_seconds`, `hilda_queue_depth`, `hilda_adapter_retry_total`, plus latency histograms.
- **Compact reports** — RPT / MET / FIX / QC per `[D-002]`, persisted in `CommunicationLog` (Postgres). Domain audit trail; orthogonal to logs/metrics.

**Dashboards-as-code** in `deploy/grafana/dashboards/`: `system_overview.json`, `error_codes.json`, `workers_and_queues.json`, `llm_gateway.json`, `sharepoint_integration.json`. Imported into corp Grafana, not run by HILDA.

**Alerts-as-code** in `deploy/prometheus/alerts/hilda.yaml`: rules keyed on `[D-002]` error-code rate (e.g. `SHP-E*` > N/min for 5m → page).

**Not in v1**: HILDA-owned Grafana / Loki / Tempo / OTel collector / distributed tracing. Tracing follow-up `D-XXX` if cross-pod debugging pain emerges.

**Implementation seam**: `core/src/observability/` (or extension of `diagnostics/`) provides Prom client setup + log formatter + standard metric registry; `[D-002]`'s `PipelineError` raise path auto-increments `hilda_pipeline_errors_total{code}` (one-shot wiring at `error_codes.py` level).

---

## §7 — Secrets + config flow *(stable)*

```
                    K8s Secret (provisioned by ops at deploy time, [D-019])
                              │
                              ▼  mounted as env var (e.g. HILDA_SP_PASSWORD)
              GlobalSharePointConfig.from_sources()
                              │  3-tier: CLI overrides > env > config file > defaults
                              ▼
                       SpClient / etc.
```

- **K8s Secrets**: SP password, LLM API key, customer-system credentials, hilda-svc keytab. One Secret per logical credential, mounted only by pods that need it.
- **ConfigMaps**: `config/<module>.json` files (per-module operational settings, install-time only — runtime data stays out, per `structure-conventions.md`).
- **Customer YAML in `customizations/`**: list/column maps per `[D-004]` + `[D-020]`. Mounted as ConfigMap or baked into the image (TBD: image-baked simplifies dev; ConfigMap supports per-cluster customization without rebuild).

**Pattern is stable**; the `sharepoint_integration` module already implements it correctly. Each new module follows the same `from_sources()` shape.

---

## §8 — CI/CD + environments *(Shape Decided — `[D-024]`; tool-bound choices Flagged)*

**Pipeline shape (tool-agnostic):**
- **PR**: lint + unit tests + integration tests against in-process mock SP + image build + vulnerability scan
- **Merge to main**: tag image `hilda:<git-sha>`, push, deploy to test env, smoke tests
- **Promote to prod**: manual; re-tag with semver; deploy to prod; smoke tests; gate on success
- **Image versioning**: SHA tag for dev/test (immutable); semver tag for releases. `latest` never used in cluster manifests

**Helm chart structure**: one umbrella chart at `deploy/charts/hilda/` containing all three v1 Deployments per `[D-021]`. Per-environment values files (`values-dev.yaml` / `values-test.yaml` / `values-prod.yaml`) checked in; secret values referenced by K8s Secret name only per `[D-019]`. Mock SP runs as a separate Deployment in test env (`mock-sharepoint:<sha>`) so test pods point at `HILDA_SP_SITE_URL=http://mock-sharepoint:8765`.

**Tool-bound choices — TBD pending corp-ops consultation** (tracked as `STATUS.md` Flag):
- CI runner — GitHub Actions / GitLab CI / Jenkins / corp-specific
- Image registry — Harbor / Artifactory / Nexus / corp-specific
- GitOps tool — ArgoCD / Flux / none (CI-driven `helm upgrade`)
- Environment topology — separate clusters vs separate namespaces in one cluster

These are parameters in the pipeline shape, not redesigns; resolved via follow-up `D-XXX` once corp ops is consulted.

---

## §9 — Networking + egress *(TBD — `D-XXX`)*

- **Inbound**: ingress controller (corp default) routes `hilda.corp/...` → `hilda-api` Service. TLS termination at ingress.
- **Internal**: ClusterIP services for `hilda-worker`, `hilda-llm-gateway`, Postgres, Redis. Network policies restrict who can talk to whom (e.g., only `hilda-llm-gateway` can egress to LLM).
- **Outbound**:
  - SP, email server, internal messenger/issue-tracker → direct (in-cluster path).
  - Runtime LLM (`[D-007]`: on-prem) → `hilda-llm-gateway` only.
  - Customer systems (Jira/portal): per-customer adapter pod, egress via corp proxy.
- **No app code makes direct external calls** — always through a designated egress sidecar or proxy.

---

## Open system-architecture questions

*(Running list — items resolve via `D-XXX` entries; backlogged items become `STATUS.md` Flags.)*

1. ~~**Process granularity** (§2)~~ — *resolved by `[D-021]`: modular monolith with three workloads (api / worker / llm-gateway).*
2. ~~**Workflow engine** (§4)~~ — *resolved by `[D-022]`: Celery + Redis broker + Postgres backend; Temporal deferred to v2.*
3. ~~**Observability stack** (§6)~~ — *resolved by `[D-023]`: light stack, zero HILDA-owned pods, dashboards/alerts-as-code under `deploy/`.*
4. **CI runner + image registry + GitOps + env topology** (§8) — *partially resolved by `[D-024]`: pipeline shape decided; specific tool choices remain a `STATUS.md` Flag pending corp-ops consultation.*
5. ~~**Helm chart granularity** (§8)~~ — *resolved by `[D-024]`: one umbrella chart at `deploy/charts/hilda/` with per-environment values files.*
6. **Customer YAML mount** (§7) — image-baked vs ConfigMap. TBD.
7. **Email service split** (§2) — own pod or in `hilda-worker`. Decide when `email_service/MODULE.md` is drafted.
8. **Per-customer adapter pods** (§5) — deferred until customer 2.

---

## How this doc evolves

- Decisions made: append a row to the "Conflicts" table if the resolution overrides `HILDA_Design.md`; cross-link to `D-XXX`.
- Section moves from "TBD" to "Decided" when its `D-XXX` lands. Update prose accordingly; do not delete the rationale — replace with a one-line summary linking the `D-XXX`.
- Topology Mermaid: add to a new `## Topology diagram` section once §2 + §5 are decided. Keep MAP.md's module-graph Mermaid separate; SYSTEM.md's diagram is process-level.
- This file is hand-curated (peer of `structure-conventions.md`), not regenerated.
