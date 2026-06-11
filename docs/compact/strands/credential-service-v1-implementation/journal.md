# Journal — credential-service-v1-implementation

## 2026-06-11 — session 1: Ph-1 module implemented end-to-end

**Strand opened, bound, and pushed** (commit `27e952f` after rebase onto teammate's
`template-schema-v2-rewrite` strand). Phase set to development scoped to
`credential_service` (+ one-hop dep `diagnostics`).

**Implementation — `core/src/credential_service/` complete against MODULE.md:**
- `protocol.py` — frozen `Credential` (secret-free `__repr__`/`__str__`;
  `value_carriers_consistent()` helper), 8-value closed `SystemType`
  (5 systems + 3 LLM backends per `[D-052]` tri-backend), `SYSTEM_ENV_PREFIX` map.
- `service.py` — `CredentialService` Protocol; `SopsCredentialService`: decrypt-once
  via `sops --decrypt` subprocess (`SOPS_AGE_KEY_FILE`, plaintext in memory only),
  process-lifetime cache under asyncio lock, 3-step resolution (exact → ops-team
  fallback CRD-W001 → CRD-E001), idempotent `load()`, ops-triggered-only `reload()`
  (CRD-W002), `install_sighup_handler() -> bool` (Windows-safe no-op; returns False
  where SIGHUP/add_signal_handler unavailable — cross-platform constraint from user).
- `mock_service.py` — exact-match `MockCredentialService` + `with_all_system_types()`.
- `qc_templates.py` — `CRD:credential_completeness` registered at import
  (auth_type enum extended with `none` for the file-absent fixed-field case).
- `credential_service_cli.py` — `--diagnostic` / `--mock` / `--validate --system`
  (+ `--env-dir` / `--age-key` overrides for lab/test); no `--dry-run` (no write surface).
- 6 CRD codes appended to central `diagnostics/error_codes.py`.

**Verification:** 40 module tests (incl. negatives: no secret in repr/RPT/QC output,
no plaintext on disk after startup, free-text rejected by QC template, malformed file
→ CRD-E004, rotation invisible until `reload()`). Full suite 219 green. CLI smoke runs
emit clean `RPT|CRD` / `QC|CRD` lines. Real-sops round-trip NOT yet exercised
(tests patch the decrypt step) — paused at user request; plan is a
`skipif(no sops)`-guarded integration test, pending user go-ahead.

**Design settled this session** (drafts in `decisions-draft.md`):
1. `reload()` trigger = SIGHUP-only; HTTP admin endpoint deliberately deferred
   Ph-1/Ph-2. Verified consistent with the `[D-025]` reload idiom in
   `customizations/rules` / `rule_engine` / `workflow_engine` drafts (2026-06-10).
   Needs `deploy/scripts/reload-credentials.sh` (4 containers) when deploy/ lands.
2. `.enc.env` internal env-var layout convention (ops-facing contract for the
   `[D-038]` runbook).

**Environment notes:** created `hilda-env/` venv (Python 3.12 via uv) — none existed
on this machine. requirements.txt under-pins starlette (1.3.0 breaks
test_mock_server collection under `filterwarnings=error`); pinned `starlette<1`
locally; flagged repo fix as background-task chip (out of strand scope).

**Open items for next session(s):**
- Real-sops `skipif` integration test (paused on user questions).
- Architect review of the two draft decisions (esp. SIGHUP-only reload).
- Caller integration arrives with other modules: `jira_adapter` wiring, FR-42
  CommunicationLog entries (needs `storage`), workload-entrypoint `load()` calls +
  age-key read-only mounts in `deploy/compose/`.
- Pre-existing portability note (outside strand): `issue_tracker_cli.py` C06 check
  hardcodes `/tmp` — fails on Windows; one-line `tempfile.gettempdir()` fix when
  issue_tracker is next touched.
