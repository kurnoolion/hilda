# Draft decisions — credential-service-v1-implementation

*Promoted to canonical DECISIONS.md with sequential D-XXX IDs at `/land-strand`.
Unnumbered until then.*

---

## DRAFT-1: Credential reload trigger is SIGHUP-only; HTTP admin endpoint deliberately deferred (Ph-1/Ph-2)

**Date:** 2026-06-11
**Status:** draft — pending architect review

**Context:** `SopsCredentialService` caches decrypted credentials per-process across
the 4 workload containers (`hilda-api`, `hilda-worker`, `hilda-beat`,
`hilda-llm-gateway` per `[D-021]`). After ops rotates an `.enc.env` per `[D-038]`,
every process must rebuild its cache. The 2026-05-27 MODULE.md review settled that
`reload()` is ops-triggered only ("SIGHUP / admin endpoint") but left the admin
endpoint underspecified: which workloads expose it, route path, and fan-out
(per-workload vs broadcast).

**Decision:** No HTTP admin endpoint in Ph-1/Ph-2. Reload is triggered exclusively
by SIGHUP per container; ops runs a convenience script
(`deploy/scripts/reload-credentials.sh`, to be added when `deploy/` lands) that
sends `docker kill -s HUP` to all 4 containers and reports per-container status.
`install_sighup_handler()` (implemented 2026-06-11) wires the handler; it no-ops
returning False on platforms without SIGHUP (Windows dev boxes) — production
runtime is the Linux HILDA PC per `[D-026]`.

**Why:** (a) Rotation is a rare, deliberate ops event on a single host where ops
already has shell access (manual deploy model per `[D-024]` impl note). (b) SIGHUP →
`reload()` → atomic swap is the established project idiom — identical to the
`[D-025]` rules reload in `customizations/rules` / `rule_engine` and
`workflow_engine.reload_beat_schedule()` (all drafted 2026-06-10); credentials extend
the same pattern from 2 containers to 4. (c) Adding HTTP listeners to
`hilda-worker`/`hilda-beat` (Celery processes with no server today) creates new
attack surface for near-zero traffic. (d) The alternative of an `hilda-api` endpoint
enqueueing a Celery task is structurally wrong for cache invalidation: a task runs on
ONE worker process while the cache is per-process across all four — it would refresh
one process and silently leave three stale. (e) Fully reversible: an endpoint can be
added later if a remote-trigger need materializes; Ph-3+ Vault (DEF-14 proactive
refresh via `Credential.expires_at`) retires the reload mechanism anyway.

**Consequences:** Ops rotation runbook is shell-only (no dashboard button) through
Ph-2. `deploy/scripts/reload-credentials.sh` must be added to the deploy tree and
the `[D-038]` rotation runbook. The thundering-herd concern from the 2026-05-27
review does not apply (4 deliberate ~100ms sops decrypts; reload is never wired to
auth-error paths). Workload entrypoints must call
`service.install_sighup_handler()` at startup alongside `await service.load()`.

---

## DRAFT-2: `.enc.env` internal env-var layout — `HILDA_<PREFIX>_*` convention

**Date:** 2026-06-11
**Status:** draft — pending architect review

**Context:** MODULE.md's File layout names the per-system files
(`/etc/hilda/credentials/<system_type>.enc.env`) and gave a single example of their
contents (`HILDA_ITR_*`) but did not define the full variable convention. Ops must
author these files per the `[D-038]` runbook, and `SopsCredentialService` must parse
them — the layout is a contract between the two.

**Decision:** Inside each decrypted file, variables are
`HILDA_<PREFIX>_<FIELD>` where `<PREFIX>` comes from `SYSTEM_ENV_PREFIX` in
`protocol.py`: module-prefix abbreviation where one exists (`ITR`, `MSG`, `CAD`,
`EML`, `SHP`) and the uppercased system_type for the LLM backends
(`LLM_OLLAMA_A4000`, `LLM_VLLM_DGX`, `LLM_CORP_LLM`). Fields: `AUTH_TYPE` (required
when any credential is declared; one of `api_token|basic|ntlm|kerberos|oauth2_bearer`)
plus the carriers that auth_type requires (`API_TOKEN`; `USERNAME`+`PASSWORD`;
`KEYTAB_PATH`; `BEARER`), optional `PM_ID` (default `ops-team`) and `EXPIRES_AT`
(ISO-8601). An empty or carrier-free file declares no credential — legal for the
no-auth lab LLM backends; lookups for that system then raise CRD-E001. A declared
but incomplete credential raises CRD-E004 naming the missing field.

**Why:** Per-system prefixes (vs a single generic `HILDA_CRED_*`) keep variables
self-describing in ops tooling and match the MODULE.md's existing `HILDA_ITR_*`
example; reusing module prefixes where they exist matches the error-code prefix
discipline (`[D-017]`). Empty-file-as-no-credential (vs error) honors the MODULE.md
note that lab LLM backend files "may be empty / no-auth in default lab deployment".

**Consequences:** The `[D-038]` ops runbook documents this layout verbatim; changing
it later is a coordinated ops + code change. `--validate --system <type>` is the
ops-side conformance check for authored files.
