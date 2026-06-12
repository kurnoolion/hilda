# llm-v1

**Status:** in-flight
**Opened:** 2026-06-12
**Landed:**
**Assignees:** trepository
**Target modules:** llm
**Active phase:** development

## Summary

Ph-1 implementation of LLMGatewayServer per `[D-021]` + `[D-052]` tri-backend (ollama_a4000 / vllm_dgx / corp_llm). Empirical per-TaskKind routing via env-config (`task_backend_map` + `task_model_map`). 5 TaskKinds: CLASSIFY_DOC_TYPE / ROUTE_ATTACHMENT / CLASSIFY_DOC / REVIEW_DOCUMENT / CLASSIFY_MESSAGE. Token-bucket rate limiter per backend. No automatic spillover per `[D-052]` — quota exhaustion surfaces as LLG-W006.

## Notes

**🔒 LAND GATE (architect, 2026-06-12):** Do **not** run `/land-strand llm-v1` yet.
Landing is sequenced **after** `dashboard-v1` lands — dashboard-v1 is active and will
complete first; land llm-v1 only once that is done. This supersedes the session-1
close note's "land whenever real callers wire in" trigger. Landing promotes the
`[D-052]` impl-note addendum in `decisions-draft.md` to canonical DECISIONS.md, so the
order also keeps DECISIONS.md numbering/append sequencing clean across the two strands.

**First-week plan (from architect):**
1. Read `core/src/llm/MODULE.md` end-to-end.
2. Decide test-harness strategy — mock LLM server first, or real Ollama from day 1.
3. Implement `BackendConfig` + `LLMGatewayServer` init.
4. Implement Ollama client adapter (Ph-1 dev backend).
5. Token-bucket rate limiter + LLG-W005 / LLG-W006 emission.

**Deferred (need lab access):** `vllm_dgx` + `corp_llm` backend adapters.
