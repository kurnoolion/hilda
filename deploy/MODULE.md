# Module: deploy

> **Status:** Ph-1 scaffolding 2026-06-25 — sanitized public github artifacts ready for first deployment test. Real corp values + sops keys + `.env` LOCAL on architect's Linux deployment box per `[D-125]` Point 3. Validated runtime: Podman 4.9.3 + podman-compose 1.0.6 on Ubuntu 24.04.4 LTS (corp Linux box) per `[D-129]` + smoke-test results. Container base: Debian (`python:3.11-slim-bookworm`) per `[D-130]`. Chromium binary at `/usr/lib/chromium/chromium` per `[D-130]`.

## Purpose

Deployment scaffolding for HILDA Ph-1 + Ph-2 production stack per `[D-026]` 6-container architecture. Hosts:

- 4 HILDA Dockerfiles (api / worker / beat / llm-gateway)
- `docker-compose.yml` (6-service compose v3.9 topology, runtime-interchangeable Podman / Docker)
- `.env.example` sanitized template (real `.env` LOCAL only per `[D-125]`)
- `sops_decrypt.sh` pre-boot hook for `customizations/credentials/*.sops.yaml`
- This `MODULE.md` ops runbook

**Ph-1 + Ph-2 deployment target**: single bare-metal Linux PC (`omadm-HP-Z640-Workstation` or equivalent), Ubuntu 24.04 / Debian 12.

**Ph-3+ migration target**: MicroK8s + Helm chart per `[D-021]`. Helm migration retains the Dockerfiles unchanged; only orchestrator binding (compose YAML → Helm chart) changes.

## Public surface

Operations runbook only — no Python public surface.

## Invariants

- **Sanitized samples only in github** per `[D-125]` Point 3. Production `.env` + sops-encrypted credentials + per-customer `customer.yaml` / `template.yaml` / `recipients.yaml` LOCAL only.
- **Compose v3.9 syntax** — runtime-interchangeable across Podman (`podman-compose`) and Docker (`docker compose`). No runtime-specific extensions in the compose file.
- **Debian bookworm base** for all 4 HILDA containers per `[D-130]`. No Ubuntu base (snap-stub Chromium fails).
- **Chromium binary location explicit**: `/usr/lib/chromium/chromium` per `[D-130]` validation. Selenium `binary_location` config must match.
- **Non-root user (uid=10001 `hilda`) inside each HILDA container** per `[D-129]` rootless model.
- **`tini` as PID 1** in every HILDA container — proper signal handling for graceful uvicorn / celery shutdown.
- **Bind-mounts for `customizations/` + `config/`** per `[D-025]` 3-tier config + hot-reload. `Z` flag on each bind-mount handles SELinux relabel (harmless if SELinux off).
- **No `:latest` tags in production** — pin Postgres + Redis to specific minor versions. HILDA images built locally; tag with git commit SHA for traceability.
- **Beat schedule persistence** to bind-mounted volume so `celerybeat-schedule.db` survives container restarts.
- **NSD bind-mount production override** — compose file references named volume `nsd_internal` for dev; production deployments uncomment the host SMB/CIFS bind-mount (`/mnt/nsd:/mnt/nsd:rw,Z,bind-propagation=rslave`).
- **Sops decryption to tmpfs ramdisk** per `[D-038]` — decrypted credentials live in `/var/run/hilda-creds` (size-limited tmpfs); wiped on host reboot.

## Key choices

- **Single bare-metal Linux box Ph-1/Ph-2** over **multi-host even for dev** per `[D-026]`. K8s in Ph-3+ per `[D-021]`. Justification: HILDA's 6-container scale doesn't justify K8s orchestration burden until concurrent customer scaling demands it.

- **Podman 4.x runtime over Docker** per `[D-129]` — corp firewall block on `auth.docker.io` validated 2026-06-25 + rootless security model + systemd-native integration. Docker remains a fallback if corp policy changes.

- **Debian base over Ubuntu 24.04** per `[D-130]` — Ubuntu 24.04 `chromium-browser` package is snap-only stub that fails in containers; Debian still ships real `.deb` Chromium.

- **`tini` PID 1 over native entrypoint** — Celery worker + uvicorn don't reap zombies properly when PID 1. `tini` is ~16KB binary; trivial cost.

- **Per-service Dockerfile over single monolithic image** — even though all 4 HILDA containers share most layers, separating Dockerfiles makes future divergence (e.g., GPU build for hilda-llm-gateway in Ph-1 next pass) clean.

- **Bind-mount `customizations/` read-only over volume copy** per `[D-025]` hot-reload pattern — ops can SIGHUP `rule_engine.RuleSet.reload()` after editing `automation_rules.yaml` without container rebuild.

- **Healthcheck-gated `depends_on`** over `wait-for-it.sh` patterns — compose v3.9 native; podman-compose validated 2026-06-25.

- **Sops decryption pre-boot hook over runtime credential service in containers** per `[D-038]` — decryption requires GPG/age key access (host-side concern, not containerized). `deploy/sops_decrypt.sh` runs once per host startup.

- **No exposed external ports except `hilda-api`** — postgres + redis + llm-gateway only on internal compose network. `hilda-api` exposed on `127.0.0.1:8443` (reverse proxy on different host fronts it; no direct external access).

## Non-goals

- **Not a Kubernetes manifest** Ph-1 — that's Ph-3+ scope per `[D-021]`.
- **Not a CI/CD pipeline** — basic `git pull` + `podman-compose up` per PROJECT.md Ph-1/Ph-2 ops pattern; full CD lands Ph-3+ per `[D-024]`.
- **Not a backup/restore solution** — postgres + redis volumes assumed handled by host-level backup (out of HILDA scope).
- **Not a monitoring/observability stack** — Prometheus + Grafana lands Ph-3+ per `[D-024]`. Ph-1 logging is container stdout/stderr → host journald.
- **Not a TLS termination layer** — corp reverse-proxy PC handles external TLS per SYSTEM.md §3. `hilda-api` serves plain HTTP on the internal network.

## Depends on

- `core/src/dashboard/app.py` `build_app(...)` — FastAPI factory for hilda-api
- `core/src/workflow_engine/celery_app.py` `hilda_celery_app` — Celery app instance for hilda-worker + hilda-beat
- `core/src/llm/gateway_server.py` (Ph-1 stub; Ph-1 next pass real impl) — hilda-llm-gateway entrypoint
- `requirements.txt` — Python deps consumed by every Dockerfile
- `customizations/credentials/*.sops.yaml` (LOCAL) — decrypted by `sops_decrypt.sh` before bring-up
- `customizations/sharepoint_config/customers/customer.yaml` (LOCAL) — bind-mounted into hilda-worker + hilda-api
- `customizations/template_schemas/<customer_id>/template.yaml` (LOCAL) — same
- `customizations/ops_alerts/recipients.yaml` (LOCAL) — same
- `config/*.json` (some sanitized in github; production LOCAL) — same

## Depended on by

- HILDA operations / deployment team (architect's office Linux box Ph-1/Ph-2)
- `[D-021]` Ph-3+ Helm chart authors (will read this scaffolding as the canonical
  container shape spec)

## Sub-modules

```
deploy/
├── Dockerfile.hilda-api             # FastAPI + uvicorn; port 8443 internal
├── Dockerfile.hilda-worker          # Celery worker + Chromium for customer_adapter
├── Dockerfile.hilda-beat            # Celery beat singleton; reads polling schedules
├── Dockerfile.hilda-llm-gateway     # On-prem LLM proxy; Ph-1 stub, Ph-1-next real
├── docker-compose.yml               # 6-service stack: 4 HILDA + postgres + redis
├── .env.example                     # SANITIZED env template
├── sops_decrypt.sh                  # Pre-boot credential decrypt
└── MODULE.md                        # This runbook
```

## Deployment runbook (Phase D2 — architect-led on Linux box)

### Prerequisites on Linux box

```bash
# Already validated on omadm-HP-Z640-Workstation 2026-06-25:
sudo apt install -y podman podman-compose       # ✓ done
podman --version                                  # 4.9.3
podman-compose --version                          # 1.0.6
podman run --rm hello-world                       # ✓ passes
sops --version                                    # install if missing: per https://github.com/getsops/sops
```

### One-time setup

```bash
git clone git@github.com:kurnoolion/hilda.git ~/hilda
cd ~/hilda

# Place LOCAL files per [D-125] Point 3 (architect maintains these on office PC):
# - customizations/sharepoint_config/customers/customer.yaml
# - customizations/template_schemas/MMK/template.yaml
# - customizations/template_schemas/MMK/doc_type_filename_rules.yaml
# - customizations/ops_alerts/recipients.yaml
# - customizations/credentials/*.sops.yaml
# - config/*.json (real overrides per [D-025] Tier 2)

# Configure env:
cp deploy/.env.example deploy/.env
vi deploy/.env                                    # fill real corp values
```

### Bring-up sequence

```bash
cd ~/hilda

# 1. Decrypt sops bundles (per [D-038])
bash deploy/sops_decrypt.sh

# 2. Build images (first time only; subsequent runs use cache)
podman-compose -f deploy/docker-compose.yml --env-file deploy/.env build

# 3. Bring up the stack
podman-compose -f deploy/docker-compose.yml --env-file deploy/.env up -d

# 4. Verify
podman-compose -f deploy/docker-compose.yml ps
podman-compose -f deploy/docker-compose.yml logs -f hilda-worker
```

### Hot-reload customizations (no rebuild)

```bash
# Edit customizations/rules/global/automation_rules.yaml
vi customizations/rules/global/automation_rules.yaml

# SIGHUP rule_engine to reload (per [D-025] hot-reload pattern)
podman-compose exec hilda-worker pkill -HUP -f 'celery worker'
podman-compose exec hilda-api    pkill -HUP -f uvicorn
```

### Bring-down

```bash
podman-compose -f deploy/docker-compose.yml down

# Wipe decrypted creds tmpfs
sudo umount /var/run/hilda-creds 2>/dev/null || true
```

## Deferred (Ph-2+ / Ph-3+ forward-looking)

- **GPU-enabled `hilda-llm-gateway`** — Ph-1 next pass adds CUDA driver layer for vllm/llama.cpp backends.
- **Multi-instance worker scaling** — production may run separate `hilda-worker-default` / `-llm` / `-browser` instances with queue-filter args (`--queues=<queue>`). Compose file scaffolds the pattern in comments.
- **External port exposure model** — Ph-3+ may add NodePort/Ingress via Helm; Ph-1/Ph-2 sticks to reverse-proxy-fronted localhost binding.
- **Health endpoint implementation** — `hilda-api` `/healthz` + `hilda-llm-gateway` `/healthz` referenced in healthcheck commands; placeholder endpoints to be added to respective modules in Ph-1 next pass.
- **Per-container resource limits** (`mem_limit`, `cpus`) — Ph-1 unlimited (single-box; little risk); Ph-3+ K8s adds per-pod resource quotas.
- **Backup/restore script** — Ph-3+; postgres `pg_dump` + NSD rsync + sops bundle backup are deployment-ops concerns.
- **Monitoring/alerting stack** — Prometheus + Grafana per `[D-024]` Ph-3+; ops_alerts module (Ph-1 today) handles Ph-1 anomaly signals.

## Anchors

- `[D-021]` (MicroK8s Ph-3+ target)
- `[D-022]` (Celery broker + worker shape)
- `[D-024]` (CI/CD shape Ph-3+ Helm chart)
- `[D-025]` (3-tier config + hot-reload pattern)
- `[D-026]` (Docker Compose 6-container Ph-1/Ph-2 deployment — runtime now Podman per `[D-129]`)
- `[D-027]` (Teacher/Student split — proprietary bindings LOCAL on Work PC)
- `[D-038]` (sops-encrypted credentials policy)
- `[D-129]` (Podman selected over Docker for Ph-1 runtime)
- `[D-130]` (Debian base + Chromium binary location)
- `customizations/.gitignore` (Point 3 enforcement)
- Top-level `.gitignore` (should add `deploy/.env`, `customizations/credentials/*` patterns when Phase D2 setup begins)

## Structure

<!-- BEGIN:STRUCTURE -->

_No public Python surface — operations-runbook module only._

<!-- END:STRUCTURE -->
