# Draft decisions — llm-v1

*Promoted to canonical DECISIONS.md at `/land-strand`. Note: the entry below is an
**impl-note addendum to the existing `[D-052]`**, not a new D-XXX — per architect ruling
2026-06-12 it shares the lineage of the SystemType tri-backend split already anchored at
`[D-052]` impl note 2026-06-08. At land-strand, append it to `[D-052]` rather than minting
a new decision number.*

---

## `[D-052]` impl-note addendum (2026-06-12) — client-side LLM credential removed; gateway holds per-authenticated-backend creds

**Date:** 2026-06-12
**Status:** draft — architect-approved 2026-06-12 (full approval + one refinement); land as `[D-052]` impl-note addendum

**Context:** `llm/MODULE.md` still referenced `SystemType.LLM_GATEWAY` — a single LLM credential
that no longer exists in code (the `[D-052]` tri-backend split 2026-06-09 replaced it with
`LLM_OLLAMA_A4000` / `LLM_VLLM_DGX` / `LLM_CORP_LLM`). The doc conflated two distinct network
hops under that one stale credential.

**Decision (architect):** Two-hop separation. **Hop A** (`OnPremLLMClient` → `hilda-llm-gateway`)
is an intra-Docker-Compose, on-HILDA-PC call inside the Ph-1 trust domain; the gateway makes
no caller-identity authorization decision (routes purely on TaskKind) — so **no credential**
(analogous to the NSD host-mount and `corp_*_gateway` intake hops). The `credential_service`
param is **removed** from `OnPremLLMClient.__init__`. **Hop B** (`LLMGatewayServer` → model
backends) is where per-backend credentials belong, retrieved **conditionally — up to one per
backend**, only when `BackendConfig.credential_key is not None`:
`get_credential(OPS_TEAM_PM_ID, backend.credential_key)`. Lab Ollama (`ollama_a4000`) and vLLM
(`vllm_dgx`) are typically auth-less (`credential_key=None`); only `corp_llm` reliably needs a
credential. `pm_id="ops"` corrected to the `OPS_TEAM_PM_ID` constant ("ops-team").

**Why:** Matches the built code (the `LLM_GATEWAY` SystemType is already gone). Forcing a
credential for all three backends would make the gateway un-startable in the common lab config
where local model servers have no auth — hence `str | None` + conditional retrieval. The
`[D-007]` "no caller short-circuits to a model endpoint" invariant is unaffected: it's enforced
by not handing callers backend URLs, not by a credential. Stable across phases — Ph-3+ MicroK8s
caller↔gateway auth, if ever wanted, is mTLS at the service-mesh layer, still not a constructor
param on `OnPremLLMClient`.

**Consequences:** `llm/MODULE.md` reconciled 2026-06-12 (rollback-log entry): `OnPremLLMClient`
signature narrowed; `LLMGatewayServer.__init__` docstring + Invariant + Depends-on rewritten for
conditional per-backend retrieval; `BackendConfig.credential_key` documented as the SystemType
value. Soft-flag (signature narrowing + doc-to-code reconciliation). No code beyond this module
is affected — `credential_service` already shipped the tri-backend SystemTypes.
