# Journal — llm-v1

## 2026-06-12 — session 1: Ph-1 llm module implemented mock-first (6 increments)

**Strand opened, bound, development phase.** Module is the runtime LLM surface
(`LLMProvider`) — 5 TaskKinds, tri-backend, per-task empirical routing per `[D-052]`.

**Architecture-phase contract reconciliation** (proper architecture↔development toggle):
stale `SystemType.LLM_GATEWAY` references in `llm/MODULE.md` corrected to the built
tri-backend reality per architect ruling 2026-06-12 — `OnPremLLMClient` credential param
removed (client→gateway is an internal Ph-1 trust-domain hop, no caller-identity auth);
`LLMGatewayServer` retrieves **up to one credential per backend, CONDITIONALLY**
(`credential_key is not None` — lab Ollama/vLLM auth-less, only corp_llm needs one).
`pm_id="ops"` → `OPS_TEAM_PM_ID`. Captured as a `[D-052]` impl-note addendum in
decisions-draft.md (lands at land-strand; not a new D-XXX).

**Implementation — `core/src/llm/` (mock-first, no lab access):**
- `protocol.py` — TaskKind (5), LLMRequest/LLMResponse, LLMProvider Protocol.
- `schemas.py` — 5 task input/output Pydantic pairs + INPUT/OUTPUT_SCHEMAS registries
  (CLASSIFY_DOC_TYPE output Literal-restricted to {test_report, tech_report, waiver}).
- `mock.py` — MockLLM (subset-match registration; unregistered → LLG-E001).
- `gateway_server.py` — BackendConfig + LLMGatewayServer: sync __init__ (on-prem URL
  validation LLG-E004, map integrity LLG-E006, template existence LLG-E005) + async
  start() (conditional per-backend creds) + invoke pipeline (idempotency → resolve →
  rate-limit → render → call → parse+validate+retry LLG-W003/E003 → MET).
- `backends.py` — Ollama (/api/generate) + OpenAI-compatible (/v1/chat/completions)
  adapters; injectable httpx client; transport error → LLG-E001.
- `rate_limit.py` — per-backend fixed-window limiter; LLG-W006 (exhausted, NO spillover
  per [D-052]) + LLG-W005 (approaching); injected clock for deterministic tests.
- `client.py` — OnPremLLMClient (NO credential param per architect ruling); retry on
  transport error; structured LLG errors propagated.
- `app.py` — thin FastAPI POST /invoke + GET /health; LLG→HTTP status mapping.
- `llm_cli.py` — --diagnostic / --mock / --contract / --invoke per [D-005].
- `templates/*.j2` (5); `qc_templates.py` (LLG:task_contract); 14 LLG codes in diagnostics.

**Verification:** 43 llm tests (full suite 360). Client↔gateway round-trip tested
in-process (ASGITransport → FastAPI → gateway → MockTransport → fake Ollama). CLI
--mock/--contract 5/5 green; --diagnostic honestly unreachable on dev box. Key proofs:
schema violation rejected + retried → E003; idempotency caches (1 backend hit);
lab-only config starts with 0 credentials; corp exhaustion → W006 no spillover;
OnPremLLMClient signature has no credential_service param.

**Problems fixed:** W005 threshold ordering (check after consume); log lines gained
code prefix for grep-ability.

**Soft-flag additive helpers** (accepted as idiomatic at close-session): __init__/start()
split (get_credential is async), set_http_client() test seam, _max_retries attr,
confidence_bucket static helper.

**Deferred (need lab access):** real Ollama/vLLM/corp-LLM integration tests (skipif-guarded);
per-task A/B backend tuning (real fixtures). vllm_dgx + corp_llm wired but fake-tested only.

**Cross-module note (not ours):** `[D-052]` ADR body still has stale "HILDA PC GPU" wording
(pre-existing STATUS flag) — separate doc-debt.
