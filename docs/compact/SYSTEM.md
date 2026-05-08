# System Architecture

*Companion to `PROJECT.md` (what / why), `MAP.md` (modules + dependency graph), `structure-conventions.md` (code layout). This doc owns: **process topology, inter-component communication, deployment, observability, secrets flow, CI/CD, egress**. Decisions land as `D-XXX` entries in `DECISIONS.md`; risks land as Flags in `STATUS.md`.*

*Anchors `HILDA_Design.md` §6 (Solution Architecture), §8 (Orchestration), §11 (Deployment), §12 (Configurability). Where this doc deviates from the design input, the conflict is logged below and resolved via `D-XXX`.*

---

## Conflicts with `HILDA_Design.md` (the input is older than v1 simplifications)

| # | Design-input claim | Current state | Resolution |
|---|---|---|---|
| C1 | §6.2 / §7.1 use **Microsoft Graph API** | SP 2017 frozen → REST API + NTLM/Kerberos | Resolved by `[D-006]` |
| C2 | §11 specifies **HashiCorp Vault** for credentials | v1 simplified to host env-file secrets (bare-metal per `[D-026]`) or K8s Secrets (v2); Vault deferred to v2+ | Resolved by `[D-019]` + `[D-026]` |
| C3 | §11 lists **12 separate deployments** (microservices) | v1 = modular monolith, 3 process groups (api / worker / llm-gateway); design-doc 12-pod inventory preserved as v2+ target | Resolved by `[D-021]` |
| C4 | §11 specifies **Temporal** as workflow engine | v1 = Celery + Redis broker + Postgres backend; Temporal deferred to v2 if multi-step durable orchestration emerges | Resolved by `[D-022]` |
| C5 | §11 specifies **Kubernetes** with 12-pod microservices + Helm chart | v1 = Docker Compose on single bare-metal Linux PC; K8s + Helm chart preserved as v2+ target; process boundaries and container image unchanged | Resolved by `[D-026]` |

---

## §1 — Two-pillar topology (stable, anchored in `HILDA_Design.md` §6)

```
SharePoint 2017 layer            ← PM / TPM-facing UI + entity store + document library
        ▲
        │   SP REST API + NTLM/Kerberos  (per [D-006])
        ▼
Automation layer (bare-metal PC v1 / K8s v2)  ← all HILDA backend services
```

- **SharePoint owns**: `Devices`, `Milestones`, `Deliverables`, `DeliveryItems`, `Customers`, `CustomerTemplates`, `AutomationRules`, `Users`, `PMCredentials` (entity rows); document library (current state in `[D-013]`: shifted to `\\share\hilda\` shared drive — design doc's "SharePoint Document Libraries" superseded by `D-013` for artifacts but SP still holds entity rows).
- **Automation layer owns**: every module in `core/src/` and `customizations/`, plus infra services (Postgres, Redis).
- **Boundary**: automation-layer services read+write SP via `core/src/sharepoint_integration/`. No service holds canonical entity state — SP is source-of-truth for entities; HILDA's own state (BATCH-ids, CommunicationLog, idempotency, eval-data, AutomationRule run history) lives in Postgres.

---

## §2 — Process granularity *(Decided — `[D-021]`, platform updated by `[D-026]`)*

**Decision**: modular monolith — **one container image**, **three process groups** deployed as Docker Compose services in v1 (bare-metal Linux PC) and as K8s Deployments in v2, per `[D-026]`:

| Workload | Role | v1 (Compose) | v2 (K8s replicas) | Hosts which modules |
|---|---|---|---|---|
| `hilda-api` | FastAPI/uvicorn; dashboard backend, SP-mediated download endpoint per `[D-013]`, inbound webhooks (messenger / issue-tracker callbacks) | 1 service | 2 | `dashboard`, plus in-process imports of `sharepoint_integration`, `tracker`, `rule_engine`, `template_schema`, `credential_service`, `storage`, `diagnostics` |
| `hilda-worker` + `hilda-beat` | Async-job runner (Celery); scheduled rule firings, mailbox polling, ingestor jobs, customer-adapter polling, blocking IO | 2 services | 2 + 1 beat | `email_service`, `messenger`, `issue_tracker`, `customer_adapter`, `workflow_engine`, all three Ingestor / Profiler modules; same in-process imports as api where shared |
| `hilda-llm-gateway` | Sole egress path to runtime LLM `[D-007]` and on-prem code-gen LLM; rate-limiting, retries, prompt templates; owns LLM API-key credential | 1 service | 2 | `llm` module |

**Key consequences** (full text in `[D-021]`, deployment platform in `[D-026]`):
- All 18 modules importable from any service via `core.src.<module>` — process boundary is at start-command level, not Python-package level.
- Module Protocol boundaries already in place (`[D-008]`, `[D-009]`, `[D-019]`, `[D-020]`) preserve a mechanical v2 split path: extract module + add thin REST surface; call sites don't change.
- Per-customer adapter services deferred until customer 2 (`DEF-8`).
- `credential_service` stays in-process v1 per `[D-019]`; gets its own service/pod when Vault swaps in v2.
- Each `MODULE.md` adds a curated subsection naming which workload(s) host it.
- `HILDA_Design.md` §11's 12-pod inventory preserved as v2+ target shape, not v1.

---

## §3 — Inter-component communication *(follows from §2)*

Given the three-workload split:

| From → To | Mechanism | Notes |
|---|---|---|
| `hilda-api` ↔ `hilda-worker` | Celery via Redis broker; results / state in Postgres per `[D-022]` | Async fan-out for reminders, ingest jobs; scheduled triggers via `hilda-beat` singleton |
| `hilda-api`, `hilda-worker` → `hilda-llm-gateway` | Internal HTTP (Docker DNS v1 / cluster DNS v2) | LLM gateway is the only egress path for runtime LLM |
| Any service → SharePoint | `sharepoint_integration` lib (in-process import) → external HTTPS | Corp NTLM/Kerberos |
| Any service → Postgres / Redis | Standard drivers, Docker DNS (`postgres` / `redis` hostnames) | Same hostnames in K8s ClusterIP |
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
| **Redis** | (a) async-job broker (Celery); (b) short-TTL dedup cache for inbound email idempotency keys per `[D-012]`; (c) optional rate-limit token buckets | No durable state — Postgres is authoritative |
| **Shared file system (`\\share\hilda\`)** | All artifacts: test reports, tech reports, waivers, submission packages per `[D-013]` | `hilda-svc` AD service writes; HILDA-mediated reads via `https://hilda.corp/dl/<token>` per NFR-16 |

**Workflow engine** *(Decided — `[D-022]`)*: **Celery + Redis broker + Postgres result backend.** `hilda-beat` (singleton) loads cron-style triggers from the SP `AutomationRules` list at startup and `SIGHUP`-triggered refresh. Event-triggered rules enqueue Celery tasks directly from the originating handler in `hilda-api`. `core/src/workflow_engine/` owns the Celery app, task decorators wired to `[D-002]` error codes (WFL prefix), beat schedule loader, and event dispatcher; `core/src/rule_engine/` stays a pure-Python rule-condition evaluator.

**DB migration strategy**: Alembic migrations run as part of deploy via `docker compose run --rm hilda-api alembic upgrade head` (v1 deploy script) before services start. In v2 K8s this becomes an init container on `hilda-api`. Migrations are idempotent and backward-compatible with the running prior version.

---

## §5 — Deployment topology *(v1: Docker Compose on bare-metal — `[D-026]`; v2: K8s per `[D-021]`)*

**v1 — Docker Compose on single bare-metal Linux PC:**

| Service | Image | Notes |
|---|---|---|
| `hilda-api` | `hilda:<sha>` | FastAPI/uvicorn; Nginx fronts it on :443; health: `GET /health` |
| `hilda-worker` | `hilda:<sha>` | Celery worker pool; health: `celery inspect ping` |
| `hilda-beat` | `hilda:<sha>` | Celery beat singleton per `[D-022]`; loads schedule from SP `AutomationRules` |
| `hilda-llm-gateway` | `hilda:<sha>` | Sole egress to on-prem LLM per `[D-007]`; health: `GET /health` |
| `postgres` | `postgres:16` | Volume: `postgres_data`; health: `pg_isready` |
| `redis` | `redis:7-alpine` | No persistence needed; health: `redis-cli ping` |
| `nginx` | `nginx:alpine` | TLS termination (corp cert) + reverse proxy → `hilda-api:8000`; port 443 exposed on host |
| `mock-sharepoint` *(dev/test only)* | `mock-sharepoint:<sha>` | Compose dev profile; `HILDA_SP_SITE_URL=http://mock-sharepoint:8765` |

All services on `hilda_net` Docker bridge network. Service names mirror intended v2 K8s ClusterIP Service names for zero-rename migration.

**v2 K8s equivalents (from `[D-021]`):**

| Workload | K8s kind | Replicas |
|---|---|---|
| `hilda-api` | Deployment | 2 |
| `hilda-worker` | Deployment | 2 |
| `hilda-beat` | Deployment | 1 |
| `hilda-llm-gateway` | Deployment | 2 |
| `postgres` | StatefulSet | 1 + 1 replica |
| `redis` | Deployment | 1 (v1) → HA later |
| `vault` *(v2 only)* | StatefulSet | 3 |
| `nginx` → Ingress controller | — | — |
| per-customer adapter *(deferred, DEF-8)* | Deployment | 1 each |

---

## §6 — Observability *(Decided — `[D-023]`)*

**Decision**: light stack, zero new HILDA-owned services, dashboards/alerts as code under `deploy/`.

**Three signal channels:**
- **Logs** — structured JSON to stdout from every container; host log forwarder ships to corp log store. Required fields: `ts`, `level`, `service`, `module`, `error_code`, `run_id`, `pm_id` (never credentials).
- **Metrics** — `/metrics` endpoint per container via `prometheus_client`. Required families: `hilda_request_total`, `hilda_celery_tasks_total`, `hilda_pipeline_errors_total{code}` (the `[D-002]` integration), `hilda_llm_calls_total`, `hilda_sp_request_total`, `hilda_credential_expiry_seconds`, `hilda_queue_depth`, `hilda_adapter_retry_total`, plus latency histograms.
- **Compact reports** — RPT / MET / FIX / QC per `[D-002]`, persisted in `CommunicationLog` (Postgres). Domain audit trail; orthogonal to logs/metrics.

**Dashboards-as-code** in `deploy/grafana/dashboards/`: `system_overview.json`, `error_codes.json`, `workers_and_queues.json`, `llm_gateway.json`, `sharepoint_integration.json`. Imported into corp Grafana, not run by HILDA.

**Alerts-as-code** in `deploy/prometheus/alerts/hilda.yaml`: rules keyed on `[D-002]` error-code rate (e.g. `SHP-E*` > N/min for 5m → page).

**Not in v1**: HILDA-owned Grafana / Loki / Tempo / OTel collector / distributed tracing. Tracing follow-up `D-XXX` if cross-service debugging pain emerges.

**Implementation seam**: `core/src/observability/` (or extension of `diagnostics/`) provides Prom client setup + log formatter + standard metric registry; `[D-002]`'s `PipelineError` raise path auto-increments `hilda_pipeline_errors_total{code}`.

---

## §7 — Secrets + config flow *(v1 updated by `[D-026]`)*

```
                    /etc/hilda/<service>.env  (v1 — host file, provisioned by ops, gitignored)
                    K8s Secret                (v2 — same env var names, different mount mechanism)
                              │
                              ▼  env_file: in Compose (v1) / envFrom: secretRef (v2)
              GlobalSharePointConfig.from_sources()
                              │  3-tier: CLI overrides > env > config file > defaults
                              ▼
                       SpClient / etc.
```

- **v1 secrets**: per-service `.env` files at `/etc/hilda/<service>.env` on the bare-metal host; provisioned by ops at deploy time; gitignored; env var names (`HILDA_SP_PASSWORD`, `HILDA_LLM_API_KEY`, etc.) are identical to what v2 K8s Secrets will set — zero code change for migration.
- **v2 K8s Secrets**: SP password, LLM API key, customer-system credentials, hilda-svc keytab. One Secret per logical credential, mounted only into pods that need it. Env var names unchanged from v1.
- **ConfigMaps** (`config/<module>.json` files): per-module operational settings, baked into image; mountable as ConfigMap in v2 without code change.
- **Customer YAML** (`customizations/sharepoint_config/`): list/column maps per `[D-004]` + `[D-020]`. v1 = Docker bind-mount from host at `./customizations/sharepoint_config/` → `/app/customizations/sharepoint_config/` (allows adding a customer without image rebuild); v2 = ConfigMap at same container path. Per `[D-025]`.

**Pattern is stable**; the `sharepoint_integration` module already implements it correctly via `from_sources()`. Each new module follows the same shape.

---

## §8 — CI/CD + environments *(Shape Decided — `[D-024]`; v1 deploy updated by `[D-026]`; tool-bound choices Flagged)*

**Pipeline shape (tool-agnostic, unchanged):**
- **PR**: lint + unit tests + integration tests against in-process mock SP + image build + vulnerability scan
- **Merge to main**: tag image `hilda:<git-sha>`, push to corp registry, deploy to test env, smoke tests
- **Promote to prod**: manual; re-tag with semver; deploy to prod; smoke tests; gate on success
- **Image versioning**: SHA tag for dev/test (immutable); semver tag for releases. `latest` never used in any deploy manifest

**v1 deploy artifacts (Docker Compose on bare-metal):**
- `deploy/compose/docker-compose.yaml` — base Compose file (all services)
- `deploy/compose/docker-compose.dev.yaml` — dev overrides (mock SP service, debug ports)
- `deploy/compose/.env.example` — env var template (actual `.env` files gitignored, provisioned by ops)
- `deploy/scripts/deploy.sh` — deploy script: `git pull` → `docker compose pull` → `docker compose up -d` → run Alembic migrations
- Mock SP runs as the `mock-sharepoint` service (dev Compose profile); test env points `HILDA_SP_SITE_URL=http://mock-sharepoint:8765`

**v2 deploy artifacts (K8s Helm — placeholder only in v1):**
- `deploy/charts/hilda/` — umbrella Helm chart per `[D-024]`, preserved as v2 migration target; v1 contains only a `README.md` placeholder
- Per-environment values files (`values-dev.yaml` / `values-test.yaml` / `values-prod.yaml`) to be added when migrating to K8s

**Tool-bound choices — TBD pending corp-ops consultation** (tracked as `STATUS.md` Flag):
- CI runner — GitHub Actions / GitLab CI / Jenkins / corp-specific
- Image registry — Harbor / Artifactory / Nexus / corp-specific
- Deploy trigger — manual SSH + `deploy.sh` vs CI-push webhook to bare-metal host

These are parameters in the pipeline shape; resolved via follow-up `D-XXX` once corp ops is consulted.

---

## §9 — Networking + egress *(v1: Docker Compose bridge network — `[D-026]`; v2: K8s NetworkPolicy — TBD)*

**v1 — Docker Compose on bare-metal:**
- **Inbound**: `nginx` container on `hilda_net` handles TLS termination (corp cert) and proxies `hilda.corp/...` → `hilda-api:8000` on the Docker bridge network. Port 443 exposed on host; all other services are internal-only.
- **Internal**: single Docker bridge network `hilda_net`; all services reachable by service name (Docker DNS). Service names chosen to match intended v2 K8s ClusterIP Service names: `hilda-api`, `hilda-worker`, `hilda-beat`, `hilda-llm-gateway`, `postgres`, `redis`.
- **Outbound**:
  - SP, email server, internal messenger/issue-tracker → from containers via host network stack (corp network reachable from bare-metal host).
  - Runtime LLM (`[D-007]`) → `hilda-llm-gateway` container only; other containers cannot reach the LLM endpoint without going through `hilda-llm-gateway`'s HTTP surface.
  - Customer systems (Jira/portal): per-customer adapter service, egress via corp proxy configured in container env.
- **No app code makes direct external calls** — always through a designated service (`hilda-llm-gateway` for LLM; `sharepoint_integration` lib for SP).

**v2 K8s (migration target):**
- `nginx` container → Ingress controller (corp default); TLS at ingress
- `hilda_net` Docker bridge → K8s overlay network + ClusterIP Services (service names preserved)
- LLM egress restriction → K8s NetworkPolicy (only `hilda-llm-gateway` pod egresses to LLM host)
- Full NetworkPolicy set to be decided as `D-XXX` when migrating to K8s

---

## Open system-architecture questions

*(Running list — items resolve via `D-XXX` entries; backlogged items become `STATUS.md` Flags.)*

1. ~~**Process granularity** (§2)~~ — *resolved by `[D-021]`: modular monolith with three workloads (api / worker / llm-gateway).*
2. ~~**Workflow engine** (§4)~~ — *resolved by `[D-022]`: Celery + Redis broker + Postgres backend; Temporal deferred to v2.*
3. ~~**Observability stack** (§6)~~ — *resolved by `[D-023]`: light stack, zero HILDA-owned services, dashboards/alerts-as-code under `deploy/`.*
4. **CI runner + image registry + deploy trigger** (§8) — *partially resolved by `[D-024]` + `[D-026]`: pipeline shape and Compose deploy script decided; specific tool choices (CI runner, registry, deploy trigger mechanism) remain a `STATUS.md` Flag pending corp-ops consultation.*
5. ~~**Helm chart granularity** (§8)~~ — *resolved by `[D-024]` + `[D-026]`: Helm chart preserved as v2 placeholder at `deploy/charts/hilda/`; Docker Compose is v1 deploy artifact.*
6. ~~**Customer YAML mount** (§7)~~ — *resolved by `[D-025]`: v1 Docker bind-mount from host; v2 ConfigMap at same container path.*
7. **Email service split** (§2) — own service or in `hilda-worker`. Decide when `email_service/MODULE.md` is drafted.
8. **Per-customer adapter services** (§5) — deferred until customer 2.
9. ~~**Deployment platform v1**~~ — *resolved by `[D-026]`: Docker Compose on single bare-metal Linux PC; K8s is v2+ target; process boundaries, container image, and task architecture unchanged.*

---

## How this doc evolves

- Decisions made: append a row to the "Conflicts" table if the resolution overrides `HILDA_Design.md`; cross-link to `D-XXX`.
- Section moves from "TBD" to "Decided" when its `D-XXX` lands. Update prose accordingly; do not delete the rationale — replace with a one-line summary linking the `D-XXX`.
- Topology diagram: add a process-level Mermaid to a new `## Topology diagram` section when priorities allow — §2 + §5 are now decided and ready for diagramming.
- This file is hand-curated (peer of `structure-conventions.md`), not regenerated.
