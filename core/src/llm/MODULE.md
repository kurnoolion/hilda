# Module: llm

> **Status:** Draft + 2026-06-09 cascade Group 3 of 3 + 2026-06-21 Ph-1 phasing lock + 2026-06-22 CLASSIFY_DOC_TYPE demotion. **Ph-1 phasing per architect direction 2026-06-21 + further-narrowed 2026-06-22 early-drop demotion**: TaskKinds are now phase-scoped into 3 tiers — (a) **Ph-1 first pass** active = `ROUTE_ATTACHMENT` only (full implementation; runtime-dormant in early drop when FR-52 steps 1-3 substring/fuzzy/folder rules are exhaustive — typical for fixed early-drop work-item set + stable per-WI filename keyword conventions per architect direction 2026-06-22); (b) **Ph-1 next pass** = `CLASSIFY_DOC_TYPE` + `CLASSIFY_MESSAGE` + `REVIEW_DOCUMENT` (Protocol declared, dormant implementation, prompt templates + schemas land at Ph-1 next pass). **CLASSIFY_DOC_TYPE demoted from first pass to next pass 2026-06-22** per architect direction: for the Ph-1 early drop with fixed work items + stable per-WI file-naming convention (filename keywords like "Test Plan" / "SDoc" / "Sustainability Certificate" / "HWCertReport" are persistent across device models), **FR-85 Step 1 filename regex against per-customer rules at `customizations/template_schemas/<customer_id>/doc_type_filename_rules.yaml` is sufficient for 100% of early-drop classifications** — FR-85 Step 2 LLM does not fire at runtime. CLASSIFY_DOC_TYPE TaskKind remains Protocol-declared (forward-compat per Guardrail #3) but implementation lands at Ph-1 next pass alongside CLASSIFY_MESSAGE + REVIEW_DOCUMENT; (c) **Ph-2 deferred** = `CLASSIFY_DOC` (per architect direction "document revision resolution targeted Ph-2" + `[D-039]` Tier-2 implementation Ph-2) + `DRAFT_CUSTOMER_REPLY` (DEF-3) + `SUMMARIZE_STATUS` (DEF-4). Protocol declares all 5 active TaskKinds per Guardrail #3 (forward-compat contract tests cover full surface). Prior 2026-06-09 cascade: `[D-053]` impl note 2026-06-08 corrected model: 5-value DocType + alignment invariant + FR-85 2-step classification ladder + FR-86 storage matrix + FR-87 SP UI A→B→C; **CLASSIFY_DOC_TYPE TaskKind RESTORED** (un-revert of the 2026-05-28b removal); TaskKind count 4 → 5. Earlier rollbacks: 2026-06-07 (tri-backend `[D-052]` + host-topology fix + docstring clarifications + Phase B rollback original `[D-053]` framing). Sections curated; first-pass implementation (CLASSIFY_DOC_TYPE + ROUTE_ATTACHMENT) begins after `/switch-phase development`.
>
> **Rollback log:**
> - **2026-06-12 (llm-v1 dev — client-side credential removed; gateway holds per-authenticated-backend creds)** — reconciles stale `SystemType.LLM_GATEWAY` references (a SystemType that no longer exists in code — `credential_service.protocol` has only `LLM_OLLAMA_A4000` / `LLM_VLLM_DGX` / `LLM_CORP_LLM` per the `[D-052]` tri-backend split 2026-06-09) to the built reality. Per architect ruling 2026-06-12: **two-hop separation** — Hop A (`OnPremLLMClient` → `hilda-llm-gateway`) is an intra-Compose, on-HILDA-PC call inside the Ph-1 trust domain; the gateway authorizes nothing on caller identity (routes on TaskKind), so **no credential** (analogous to the NSD host-mount / `corp_*_gateway` intake). Hop B (`LLMGatewayServer` → model backends) is where per-backend creds belong. **`credential_service` param removed from `OnPremLLMClient.__init__`.** `LLMGatewayServer` retrieves **up to one credential per backend, CONDITIONALLY** — only when `BackendConfig.credential_key is not None` (lab Ollama/vLLM are auth-less; only `corp_llm` reliably needs one — forcing all three would make the gateway un-startable in the common lab config). `pm_id="ops"` → `OPS_TEAM_PM_ID` constant. The `[D-007]` no-short-circuit invariant is unaffected (enforced by not handing callers backend URLs, not by a credential). **Not a new ADR** — same lineage as the SystemType split anchored at `[D-052]` impl note 2026-06-08; captured as a `[D-052]` impl-note addendum in the strand `decisions-draft.md` for land-strand. Soft-flag: signature narrowing (param removed) + Invariant/Depends-on text reconciliation to already-built code.
> - **2026-06-09 (post-cascade user-review correction, same day)** — applied 2 corrections from user review of Group 3 cascade: (a) **`RouteAttachmentOutput` schema corrected** to return `list[RouteAttachmentMatch]` instead of a single `(item_id, confidence)` pair — user observation that FR-79 multi-item association requires multi-match support; new typed `RouteAttachmentMatch` model added; output schema docstring clarifies LLM-side filtering above threshold (caller does NOT re-filter; empty list → FR-52 step 5 fall-through; non-empty list → one DocumentItemAssociation row per match per `[D-055]` symmetric M:M); new `LLG-W008` warning for potential over-routing (summed-confidence threshold). (b) **`[D-029]` Ph-1 narrow surface Key choices bullet** updated from "4 TaskKinds" to **"5 TaskKinds"** — CLASSIFY_DOC_TYPE was added to the listed TaskKinds + restoration note added (the prior bullet text claiming CLASSIFY_DOC_TYPE was removed per 2026-05-28b was obsolete after the 2026-06-09 cascade restore). (c) Reserved `LLG-W007` as placeholder; LLG-W008 occupied by the over-routing warning.
> - **2026-06-09 (Phase B Module cascade — Group 3 of 3 against the corrected `[D-053]` model — after `template_schema/MODULE.md` cascade 2026-06-08 + `storage/MODULE.md` cascade 2026-06-09)** — applied the requirements-phase redesign locked 2026-06-08 (`requirements.md` FR-7 + FR-85 + FR-86 + FR-87 + `DECISIONS.md` `[D-053]` impl note 2026-06-08): **CLASSIFY_DOC_TYPE TaskKind RESTORED** (un-revert of the 2026-05-28b removal); TaskKind enum count 4 → 5; **restricted candidate set** `{test_report, tech_report, waiver}` per FR-85 Step 2 — LLM never returns `compliance_certification_release_notes` (regex-only per FR-85 Step 1 because bundled sub-categories visually indistinguishable from test/tech/waiver content) nor `unresolved` (caller-side sentinel on low confidence). **ClassifyDocTypeInput / ClassifyDocTypeOutput schemas restored** with restricted-candidate-set design + below-threshold UNRESOLVED sentinel mapping documented. **NB note + Status header refreshed** to reflect restored TaskKind + withdrawn "1:1 derivation" framing. **Purpose anchors + Ph-1 scope** updated to 5 TaskKinds with FR-85 Step 2 context. **Key choices**: replaced `[D-053]` doc_type derivation bullet entirely with FR-85 2-step ladder + FR-86 alignment invariant + CLASSIFY_DOC_TYPE restoration explanation; REVIEW_DOCUMENT skip bullet updated for 5-value DocType (skip when `doc_type ∈ {compliance_certification_release_notes, unresolved}` OR `review_required = false`). **Non-goal "Not a doc_type classifier" REVERTED** → new Non-goal "IS a doc_type classifier (Step 2 of FR-85 2-step ladder)" with explicit scope (LLM only fires when regex Step 1 fails; restricted-candidate-set rationale). **Coexistence matrix per document** updated for 5-value DocType + new special-case row for test_report on Default work-item (parser_result ✓ + llm_review_findings ✗). **Tentative-assignments table** 4 → 5 rows with CLASSIFY_DOC_TYPE restored (priority lower than REVIEW_DOCUMENT). **Test interface RPT line** templates_loaded=4 → 5. **Depends-on** updated to reference 5-value DocType from template_schema. Cascade chain complete (Group 1 template_schema 2026-06-08 + Group 2 storage 2026-06-09 + Group 3 llm 2026-06-09).
> - **2026-06-07 (host-topology clarification, same day)** — fixed stale "Ollama on the HILDA PC GPU" framing across 4 sites. Clarified that **HILDA PC is a distinct Linux box from the model-serving hosts**: HILDA PC runs the HILDA app containers (hilda-api / hilda-worker / hilda-llm-gateway per `[D-021]`/`[D-026]`) and has NO GPU; `ollama_a4000` runs on a separate **A4000 box** (Linux + RTX A4000) on the lab subnet; `vllm_dgx` runs on a separate **DGX Spark box** (GB10 + 128 GB unified) on the lab subnet; `corp_llm` is corp infrastructure. hilda-llm-gateway proxies LLM calls OUTWARD from the HILDA PC to these three hosts. Updated `[D-007]` On-prem paragraph (now enumerates 3 distinct hosts + HILDA PC); Hardware-topology table (Host column clarifies "separate machine" per backend + footer note "Distinct from HILDA PC"); BackendConfig.endpoint_url example hostnames changed from `hilda-pc:11434` to `a4000-box:11434` for ollama_a4000 + clarifying comment; tentative-assignments footer clarifies env vars live on HILDA PC but resolve to lab-subnet DNS for the model boxes. **`[D-052]` DECISIONS.md body** still contains the older "HILDA PC GPU" wording at lines 638-639 — covered by the existing STATUS.md flag ("`[D-052]` ADR body needs impl note appended") since `[D-002]` ADR-body immutability prevents direct edit; the pending impl note 2026-06-07 will document the 3-host topology.
> - **2026-06-07 (docstring clarifications, same day)** — three additions captured from review-session Q&A: (a) `ReviewDocumentInput.checklist` docstring expanded — clarified as build-time-generated YAML (NOT hand-authored, NOT runtime-built) at `customizations/checklists/<slug>/<doc_type>.yaml`, per-customer because carriers have different acceptance criteria; (b) Non-goals "Not a test report parser" expanded into a two-phase pipeline breakdown — build-time Test Report Profiler emits BOTH the rule-based parser AND the REVIEW_DOCUMENT checklist (one profiler run, two outputs), runtime split: `test_report` module runs parser, `llm` module consumes checklist; (c) new "Relationship to `parser_result`" Non-goal section — explicit comparison table contrasting `parser_result` (content extraction, FR-16, rule-based, test_report-only) vs `llm_review_findings` (quality assessment, FR-53, LLM-driven, test_report/tech_report/waiver, gated on `review_required` + skipped for DEFAULT) + 4-quadrant coexistence matrix per document. No API surface change; pure docstring + cross-reference improvements.
> - **2026-06-07 (tri-backend amendment, same day)** — `[D-052]` framing expanded from dual-backend to tri-backend per user clarification on hardware topology: `BackendConfig.name` Literal expanded from 2 values to 3 (`ollama_a4000`, `vllm_dgx`, `corp_llm`); added Hardware-topology section to Purpose documenting per-backend memory/bandwidth/profile with capacity-vs-bandwidth note (DGX Spark's 128 GB ≠ throughput advantage for small models — A4000's GDDR6 ~448 GB/s beats DGX Spark LPDDR5X ~273 GB/s for autoregressive inference of fits-on-A4000 models; DGX Spark wins on capacity, concurrency, vLLM batching); BackendConfig gains `cold_load_expected` + `supports_batching` informational fields; LLMGatewayServer init docstring updated for tri-backend A/B; Invariants "On-prem only" + "No code-level backend precedence" updated to enumerate all 3 backends + add no-automatic-spillover restatement; Key choices `[D-052]` bullet rewritten as tri-backend with per-backend profile + precedence rule per task (A/B winner); tentative-assignments table gains separate `vllm_dgx` A/B candidate column + model catalog note (same Ollama models served on both ollama_a4000 + vllm_dgx for direct comparability; larger models added to vllm_dgx only if small-model A/B unsatisfactory); test-interface RPT line updated to backends_total=3 with per-backend model lists. **STATUS.md follow-up flagged**: A/B-test gate flag (2026-05-28; `[D-052]` Consequences) must be updated to reflect tri-backend testing matrix (was dual-backend); `[D-052]` ADR body in DECISIONS.md needs an impl note appended (2026-06-07 dual→tri expansion) per `[D-002]` append-only convention.
> - **2026-06-07** — Phase B Module rollback (Group 3 of N, after `template_schema/MODULE.md` + `storage/MODULE.md`): TaskKind enum reduced 5 → 4 (`CLASSIFY_DOC_TYPE` removed per `[D-053]` + `[D-052]` impl note 2026-05-28b — doc_type now derived 1:1 from `item.item_type` at routing time, not LLM-classified); removed `ClassifyDocTypeInput`/`ClassifyDocTypeOutput` schemas; updated Purpose anchors to include `[D-053]`; FR-52 framing updated from `[D-033]` 2-tier to `[D-053]` 5-step pipeline (ROUTE_ATTACHMENT now contextualized as step 4 of 5; failure falls through to step 5 default work-item per FR-78); RouteAttachmentInput docstring clarifies candidate_items come from FR-52 caller as narrowed set surviving steps 1-3 (substring / fuzzy / folder-template per FR-77 Type-2); Key choices added two bullets ([D-053] doc_type derivation + REVIEW_DOCUMENT DEFAULT-skip per FR-7 amendment); Non-goals rewrote "Not a filename-rule doc_type classifier" → "Not a doc_type classifier" with structural-derivation rationale; tentative-assignments table dropped CLASSIFY_DOC_TYPE row + added A/B-priority note (REVIEW_DOCUMENT highest); two stale "Tier-2" references in Invariants + Depended-on-by updated to step-4 framing. No new TaskKinds added for FR-77 / FR-78 / FR-79 / FR-82 / FR-83 / FR-84 — those are non-LLM concerns.

**Purpose**: Single Protocol-mediated surface (`LLMProvider`) for every runtime LLM call HILDA makes — **doc_type classification (FR-85 Step 2 — restricted-candidate-set LLM)**, new-vs-revision classification (Tier-2 in the `[D-039]` 4-step classifier), attachment routing (**step 4 of the FR-52 5-step routing pipeline per `[D-053]`**; `[D-033]` Tier-2 framing superseded), document quality review (FR-53), and message classification fallback (FR-12 path c per `[D-034]`). Owns prompt-template loading, per-TaskKind backend selection across **three on-prem backends** (Ollama on RTX A4000 + vLLM on DGX Spark + corp on-prem LLM per `[D-052]`), rate-limiting, retry policy, structured-output parsing, and the LLM API key handoff from `credential_service`. Anchors `[D-007]` (on-prem LLM hosting), `[D-029]` (Ph-1 LLM scope), `[D-033]` (Tier-2 framing superseded by `[D-053]`), `[D-034]`, `[D-039]`, `[D-052]` (multi-backend, empirical routing), `[D-053]` (impl note 2026-06-08 corrected model — CLASSIFY_DOC_TYPE restored with restricted candidate set per FR-85; impl note 2026-05-28b "1:1 derivation" framing withdrawn); serves FR-12, FR-52, FR-53, FR-85, NFR-1, NFR-2.

> **NB**: `CLASSIFY_DOC_TYPE` TaskKind is **RESTORED** per `[D-053]` impl note 2026-06-08 (un-revert of the 2026-05-28b removal). The "doc_type derived 1:1 from `item.item_type`" framing is **withdrawn** — doc_type is classified per inbound document via the FR-85 2-step ladder (filename regex Step 1 + LLM CLASSIFY_DOC_TYPE Step 2 with restricted candidate set `{test_report, tech_report, waiver}`). Alignment with `item_type` enforced per FR-86 storage matrix (misaligned pairs land on `staged-not-classified` NSD path for FR-87 step (B) TPM resolution). See Key choices + Non-goals.

### Hardware topology (Ph-1/Ph-2 on-prem)

Three runtime backends, each on its own host, with distinct capability/throughput profile. **None of them are co-located with the HILDA application** — the HILDA PC runs `hilda-llm-gateway` which proxies OUTWARD to these hosts over the lab subnet. **No code-level precedence** — `task_backend_map` (env-config) assigns one backend per TaskKind based on measured A/B-test result per `[D-052]`:

| Backend name | Host (separate machine) | Serving stack | Memory | Bandwidth | Profile |
|---|---|---|---|---|---|
| `ollama_a4000` | **A4000 box** — Linux box on lab subnet with RTX A4000 GPU | Ollama | 16 GB GDDR6 | ~448 GB/s | Highest single-stream tokens/sec for models ≤14 GB; VRAM-bound concurrency (1 model hot, swaps on demand); free per call. Best fit for latency-sensitive small-model tasks. |
| `vllm_dgx` | **DGX Spark box** — GB10 Grace Blackwell machine on lab subnet | vLLM | 128 GB unified LPDDR5X | ~273 GB/s | Holds many models hot simultaneously; vLLM continuous batching wins at batch>1; lower single-stream tok/s than A4000 for the SAME model (bandwidth-bound), but supports models the A4000 cannot fit (30B+, FP16 14B, long-context KV cache). Best fit for high-throughput batched workloads + large-model quality lift. |
| `corp_llm` | Corp infrastructure (off-lab; reachable from lab subnet) | (corp-provided) | corp-managed | corp-managed | Rate-limited (per minute/hour/day); no per-call cost. Chat + agentic APIs. Best fit when a TaskKind's A/B winner is on the corp side. |

**Distinct from**: the HILDA PC (Linux) — the HILDA app host running `hilda-api` / `hilda-worker` / `hilda-llm-gateway` containers per `[D-021]` / `[D-026]`. The HILDA PC has no GPU and serves no model; it only originates outbound LLM calls via `hilda-llm-gateway`.

**Capacity vs bandwidth note**: 128 GB unified RAM on DGX Spark gives capacity for large models the A4000 cannot fit, but the LPDDR5X bandwidth (~273 GB/s) is LOWER than A4000's GDDR6 (~448 GB/s). For autoregressive inference of models that fit on the A4000, A4000 typically wins per-token throughput; DGX Spark wins on capacity, concurrency, and batched serving (vLLM continuous batching). A/B-test results may differ across backends for the same TaskKind for these reasons.

**Model catalog (Ph-1 placeholder)**: Same Ollama models served on both `ollama_a4000` (via Ollama) and `vllm_dgx` (via vLLM with HF-compatible weights) for direct A/B comparability — `qwen3:8b-q4_k_m`, `gemma3:12b`. If A/B results are unsatisfactory, larger models (Gemma-27B FP16, Llama-3.1-70B Q4) will be added to the `vllm_dgx` catalog where they can fit (per STATUS.md A/B-test-gate Flag).

**On-prem only per `[D-007]`**: No public-cloud LLM, no SaaS LLM, no corp-proxy-to-public-cloud call. "On-prem" is the corporate network boundary. **Three distinct on-prem hosts** serve LLM workloads (clarified 2026-06-07 — not co-located on the HILDA PC):
- **HILDA PC** (Linux) — runs the HILDA application containers (`hilda-api`, `hilda-worker`, `hilda-llm-gateway`) per `[D-021]` / `[D-026]`. **Does NOT host a model directly.** `hilda-llm-gateway` proxies LLM calls OUTWARD to the model-serving hosts below.
- **A4000 box** (separate Linux box on the lab subnet) — hosts **Ollama** serving on the RTX A4000 GPU.
- **DGX Spark box** (separate machine on the lab subnet) — hosts **vLLM** serving on the GB10 / 128 GB unified memory.
- **Corp on-prem LLM** — corporate infrastructure, reachable from the HILDA lab subnet over corp network.

All four endpoints satisfy `[D-007]` (corporate network boundary). Ph-3+ same Protocol surface deployed on MicroK8s per `[D-043]`; host topology may consolidate or expand at that point.

**Workload assignment**: The `hilda-llm-gateway` container per `[D-021]` is the sole HILDA workload that issues outbound LLM calls — `hilda-api` and `hilda-worker` import this module's *client-side* Protocol surface and proxy calls to `hilda-llm-gateway` over HTTP. Concentrating egress in one workload simplifies model-endpoint network policy (one container needs egress to both Ollama and corp LLM endpoints) and contains LLM-side failures from cascading into API request handling.

**Ph-1 scope per `[D-029]` impl note 2026-05-13 + `[D-053]` impl note 2026-06-08 + architect direction 2026-06-21 Ph-1 phasing lock + architect direction 2026-06-22 CLASSIFY_DOC_TYPE demotion** — five Protocol-declared TaskKinds split into 3 implementation phases:

**Ph-1 FIRST PASS (active implementation 2026-06-22+)** — 1 TaskKind:
1. **`ROUTE_ATTACHMENT`** (FR-52 step 4 of 5) — invoked only when steps 1-3 substring/fuzzy/folder-template fail; output `None` falls through to step 5 default work-item per FR-78. Implementation pairs with the FR-52 5-step pipeline driver in `email_service` (Module #12); LLM owns only step 4. **Early-drop runtime dormancy**: for Ph-1 early-drop customers (fixed work-item set + stable per-WI filename keyword conventions), FR-52 steps 1-3 (substring/fuzzy/folder) are typically exhaustive against `customizations/template_schemas/<customer_id>/folder_routing.yaml` rules per architect direction 2026-06-22; ROUTE_ATTACHMENT activates at runtime only for new customers / new doc types not yet covered by the routing YAML. Implementation lands at first pass for forward-compat.

**Ph-1 NEXT PASS (Protocol declared; dormant implementation)** — 3 TaskKinds:
2. **`CLASSIFY_DOC_TYPE`** (FR-85 Step 2) — **demoted from Ph-1 first pass to next pass per architect direction 2026-06-22**. For Ph-1 early-drop customers, **FR-85 Step 1 filename regex against per-customer rules at `customizations/template_schemas/<customer_id>/doc_type_filename_rules.yaml` is sufficient for 100% of classifications** — filename keywords like "Test Plan" / "Test Result" / "SDoc" / "Sustainability Certificate" / "HWCertReport" are persistent across device models per architect-validated real-sample patterns 2026-06-22. FR-85 Step 2 LLM does not fire at runtime in early drop. Next-pass implementation lands when (a) new customers without exhaustive filename rules are onboarded, OR (b) ops finds a filename pattern outside YAML coverage. When invoked: restricted candidate set `{test_report, tech_report, waiver}` — LLM never returns `compliance_certification_release_notes` (regex-only per Step 1) nor `unresolved` (caller-side sentinel on low-confidence).
3. **`REVIEW_DOCUMENT`** (FR-53 — document content + checklist → findings list). Skipped by caller when `doc_type ∈ {compliance_certification_release_notes, unresolved}` OR `review_required = false` per FR-86 alignment + FR-7 (`review_required = true` only on `TEST_TECH_WAIVER_REPORT` items). In Ph-1 early drop, `review_required = false` on all items per architect lock 2026-06-19 — TaskKind is dormant at runtime even after next-pass implementation lands.
4. **`CLASSIFY_MESSAGE`** (FR-12 path (c) — message-intent fallback for owner-reply classification when rule-based path c.1 doesn't match).

**Ph-2 DEFERRED (Protocol declared but not implemented)** — 3 TaskKinds:
5. **`CLASSIFY_DOC`** (`[D-039]` Step 2 — new document vs revision-of-existing) — Ph-2 per architect direction 2026-06-21 ("document revision resolution targeted Ph-2"). `[D-039]` Tier-2 LLM call deferred; Ph-1 falls back to `[D-039]` Step 0/1 (hash-dedup + slug-match) + staged-not-revision NSD path per FR-86 for TPM resolution via FR-87 step (C).
6. **`DRAFT_CUSTOMER_REPLY`** (DEF-3) — Ph-2.
7. **`SUMMARIZE_STATUS`** (DEF-4) — Ph-2.

Adding a Ph-2 task requires four changes: a new `TaskKind` enum value (already declared for #5), a prompt template under `templates/` (already present for #5 per Guardrail #3 forward-compat), input/output Pydantic schemas in `schemas.py` (already present for #5), and a `task_backend_map` + `task_model_map` entry in env-config — no Protocol-surface change. The Protocol declares all 5 active TaskKinds across the 3 phases; contract tests cover the full surface per Guardrail #3.

---

## Public surface

### `protocol.py`

```python
class TaskKind(str, Enum):
    """Bounded set of runtime LLM tasks. Each value maps 1:1 to a prompt template
    in templates/ and a structured output schema in schemas.py.
    Five active TaskKinds (CLASSIFY_DOC_TYPE restored 2026-06-09 per `[D-053]` impl note 2026-06-08
    — un-revert of 2026-05-28b removal; the "1:1 derivation" framing was withdrawn).
    Phase-scoped per architect direction 2026-06-21 + 2026-06-22 — see Ph-1 scope narrative above:
    Ph-1 first pass active = ROUTE_ATTACHMENT only (CLASSIFY_DOC_TYPE demoted 2026-06-22 — FR-85 Step 1 filename regex sufficient for early drop);
    Ph-1 next pass = CLASSIFY_DOC_TYPE + CLASSIFY_MESSAGE + REVIEW_DOCUMENT (Protocol declared, dormant impl);
    Ph-2 = CLASSIFY_DOC (revision resolution deferred)."""
    ROUTE_ATTACHMENT      = "route_attachment"      # [Ph-1 FIRST PASS] FR-52 step 4 of 5 per [D-053] — attachment → DeliveryItem match; runtime-dormant in early drop if FR-52 steps 1-3 substring/fuzzy/folder rules in folder_routing.yaml are exhaustive
    CLASSIFY_DOC          = "classify_doc"          # [Ph-2 DEFERRED] [D-039] Step 2 — new document vs revision-of-existing; architect direction 2026-06-21
    CLASSIFY_DOC_TYPE     = "classify_doc_type"     # [Ph-1 NEXT PASS] FR-85 Step 2 — demoted from first pass 2026-06-22 per architect direction (FR-85 Step 1 filename regex against per-customer doc_type_filename_rules.yaml is sufficient for 100% of early-drop classifications); restricted candidate set {test_report, tech_report, waiver} when invoked
    REVIEW_DOCUMENT       = "review_document"       # [Ph-1 NEXT PASS] FR-53 — quality review against checklist; runtime-dormant in early drop (review_required=false on all items per architect lock 2026-06-19)
    CLASSIFY_MESSAGE      = "classify_message"      # [Ph-1 NEXT PASS] FR-12 path (c) — message-intent fallback
    # Ph-2 (deferred per [D-029] / DEF-3 / DEF-4):
    # DRAFT_CUSTOMER_REPLY = "draft_customer_reply"
    # SUMMARIZE_STATUS     = "summarize_status"

@dataclass(frozen=True)
class LLMRequest:
    task:           TaskKind
    inputs:         dict[str, Any]   # task-specific payload — see schemas.py
    timeout_s:      float | None = None
    max_tokens:     int | None = None
    temperature:    float | None = None   # None → use template default
    idempotency_key: str | None = None    # dedup within process lifetime

@dataclass(frozen=True)
class LLMResponse:
    task:        TaskKind
    output:      dict[str, Any]   # structured per task — validated against output schema
    model:       str              # which on-prem model served the call (audit field)
    latency_ms:  int
    tokens_in:   int
    tokens_out:  int

class LLMProvider(Protocol):
    """All callers depend on this Protocol, not on a concrete implementation.
    Implementations: OnPremLLMClient (Ph-1/Ph-2; calls hilda-llm-gateway over HTTP),
    LLMGatewayServer (the in-process implementation that hilda-llm-gateway
    runs on the egress side), MockLLM (tests)."""

    async def invoke(self, request: LLMRequest) -> LLMResponse: ...

    async def health(self) -> dict[str, Any]:
        """Returns {model, ready: bool, queue_depth: int}. Used by --diagnostic."""
```

### Task input/output schemas (`schemas.py`)

```python
# CLASSIFY_DOC_TYPE — FR-85 Step 2 (restored 2026-06-09 per [D-053] impl note 2026-06-08)
class ClassifyDocTypeInput(BaseModel):
    """Per FR-85 Step 2 — LLM-based doc_type classification. Fires only when FR-85 Step 1
    (filename regex against per-customer rules at `customizations/template_schemas/<customer_id>/doc_type_filename_rules.yaml`)
    fails or multi-matches. Candidate set is RESTRICTED to 3 values {test_report, tech_report,
    waiver} — LLM never returns `compliance_certification_release_notes` (that doc_type is
    detected EXCLUSIVELY by filename regex per Step 1 because its sub-categories bundle
    compliance/cert/release_notes which the LLM can't reliably differentiate from
    test_report/tech_report content). `unresolved` is also never returned by the LLM directly;
    the caller maps below-threshold confidence to `DocType.UNRESOLVED` as a sentinel."""
    first_page_excerpt:  str
    candidate_doc_types: list[Literal["test_report", "tech_report", "waiver"]]   # always all 3 in Ph-1; field exists for future per-customer flex via FR-86 alignment narrowing

class ClassifyDocTypeOutput(BaseModel):
    """Per FR-85 Step 2 output. confidence below threshold (default 0.85; configurable per customer
    at `customizations/<slug>/doc_type_classifier_config.yaml`) → caller sets
    DocumentIndexRow.doc_type = DocType.UNRESOLVED (sentinel) → file moves to FR-86
    staged-not-classified path → awaits FR-87 step (B) TPM resolution."""
    doc_type:   Literal["test_report", "tech_report", "waiver"]   # 3-value restricted output; never compliance_certification_release_notes (regex-only) or unresolved (caller-side sentinel)
    confidence: float                                              # [0.0, 1.0]; LLG-W002 emitted by caller when below threshold

# CLASSIFY_DOC — [D-039] Step 2 — new document vs revision-of-existing
@dataclass(frozen=True)
class ExistingDocCandidate:
    doc_id_slug:        str                               # existing slug in document index
    first_page_excerpt: str                               # excerpt of rev1 of that doc_id_slug
class ClassifyDocInput(BaseModel):
    new_doc_first_page_excerpt: str
    existing_candidates:        list[ExistingDocCandidate]  # all priors for (delivery_item_id, doc_type)
class ClassifyDocOutput(BaseModel):
    verdict:     Literal["REVISION", "NEW_DOCUMENT"]
    revision_of: str | None                               # doc_id_slug when verdict == REVISION; None otherwise
    confidence:  float                                    # below threshold → caller routes to D-039 Step 3 staged/

# ROUTE_ATTACHMENT — FR-52 step 4 of 5 per [D-053] ([D-033] Tier-2 framing superseded)
class RouteAttachmentInput(BaseModel):
    excerpt:           str
    candidate_items:   list[dict]  # [{item_id, item_name, item_description}] — populated by FR-52
                                    # caller (email_service.attachment_router) as the NARROWED
                                    # candidate set surviving steps 1-3 (substring / fuzzy / folder
                                    # template per FR-77 Type-2). LLM ranks within this set.

class RouteAttachmentMatch(BaseModel):
    """One (item_id, confidence) match — per FR-79 multi-item association, a single document
    can land on multiple work-items when its content overlaps multiple items (e.g., a regression
    test report covering VoLTE + handover). Each match in the output list is committed by the
    caller per FR-52 step 4 ("above-threshold matches are committed") as a separate
    DocumentItemAssociation row per [D-055]."""
    item_id:    str
    confidence: float                # [0.0, 1.0]

class RouteAttachmentOutput(BaseModel):
    """Per FR-52 step 4 — output is a LIST of above-threshold matches (revised 2026-06-09 from
    single-pair to list per user observation that FR-79 multi-item association requires it).
    EMPTY list → caller falls through to FR-52 step 5 (default work-item per FR-78);
    `RoutingResolution.STAGED_DEFAULT` recorded on DocumentIndexRow. NON-EMPTY list → caller
    creates one DocumentItemAssociation row per match per [D-055] symmetric M:M model.
    Matches MUST already be above the LLM's per-task confidence threshold (the LLM is instructed
    to omit low-confidence guesses); the caller does NOT re-filter — committing all returned
    matches is the contract. If the LLM returns multiple matches whose summed-confidence
    exceeds a customer-specific over-routing threshold, caller may emit LLG-W008 (potential
    over-routing) for ops visibility but still commits all matches."""
    matches:           list[RouteAttachmentMatch]   # 0..N matches; empty list → step 5 fall-through

# REVIEW_DOCUMENT — FR-53
class ReviewDocumentInput(BaseModel):
    doc_excerpt: str
    doc_type:    str               # test_report | tech_report | waiver — NOT called for "Default" item_type per FR-7 amendment + [D-053]
    checklist:   list[dict]        # per-customer YAML list of criteria, each {id, description, severity, evidence_hint?}.
                                    # Generated BUILD-TIME by the Test Report Profiler per [D-011] — same profiler run
                                    # that generates the rule-based test_report parser. Loaded at runtime from
                                    # customizations/checklists/<customer_slug>/<doc_type>.yaml. NOT hand-authored,
                                    # NOT runtime-built. Per-customer because different carriers have different
                                    # acceptance criteria (e.g., signature requirement, waiver-reference convention,
                                    # summary-table presence).
class ReviewDocumentOutput(BaseModel):
    findings:        list[dict]    # [{checklist_item_id, status: pass|fail|needs_review, evidence_span}] — one entry per checklist criterion
    overall_verdict: Literal["pass", "fail", "needs_review"]   # fail if any required-severity criterion failed

# CLASSIFY_MESSAGE — FR-12 path (c) per [D-034]
class ClassifyMessageInput(BaseModel):
    body:                 str
    candidate_intents:    list[str]
class ClassifyMessageOutput(BaseModel):
    intent:     str                # one of candidate_intents
    confidence: float
```

### `client.py` (caller-side — imported by `hilda-api`, `hilda-worker`)

```python
class OnPremLLMClient:
    """Thin HTTP client that proxies LLMRequest to hilda-llm-gateway. Used by every
    in-process LLM caller. Honours idempotency_key, retry policy, and the
    `[D-007]` invariant (no caller can short-circuit to a direct model endpoint)."""

    source_system: str = "on_prem_llm"  # immutable

    def __init__(
        self,
        gateway_url:        str,                       # e.g. "http://hilda-llm-gateway:9100"
        max_retries:        int   = 3,
        retry_backoff_s:    float = 1.0,
    ) -> None:
        """No credential param — `OnPremLLMClient` → `hilda-llm-gateway` is an intra-Compose,
        on-HILDA-PC hop inside the Ph-1 trust domain (like the NSD host-mount / corp_*_gateway
        intake). The gateway authorizes nothing on caller identity — it routes on TaskKind.
        Per-backend creds live server-side only (see `LLMGatewayServer`). The `[D-007]`
        no-short-circuit invariant is enforced by not handing callers backend URLs, not by a
        credential. (Ph-3+ caller↔gateway auth, if ever, is mTLS at the mesh layer — still not a
        constructor param here.) Per `[D-052]` impl-note addendum 2026-06-12."""

    async def invoke(self, request: LLMRequest) -> LLMResponse: ...
    async def health(self) -> dict[str, Any]: ...
```

### `gateway_server.py` (in-process — runs inside `hilda-llm-gateway` only)

```python
@dataclass(frozen=True)
class BackendConfig:
    """One backend = one LLM serving endpoint. Three backends in Ph-1 per `[D-052]`
    (impl note 2026-06-07 — expanded from dual-backend to tri-backend):
    `ollama_a4000` (Ollama on RTX A4000) + `vllm_dgx` (vLLM on DGX Spark) + `corp_llm` (corp on-prem)."""
    name:           Literal["ollama_a4000", "vllm_dgx", "corp_llm"]
    endpoint_url:   str               # e.g. "http://a4000-box:11434" (ollama_a4000 — separate Linux box on lab subnet),
                                       # "http://dgx-spark:8000/v1" (vllm_dgx — separate DGX Spark box, OpenAI-compatible),
                                       # corp LLM URL (corp_llm — corp infrastructure)
                                       # All three resolve to hosts DISTINCT from the HILDA PC; hilda-llm-gateway on
                                       # the HILDA PC proxies outbound over the lab subnet.
    credential_key: str | None        # the credential_service SystemType value carrying this backend's auth
                                       # (e.g. "llm_corp_llm"), or None for auth-less lab backends
                                       # (ollama_a4000 / vllm_dgx). Drives the conditional per-backend
                                       # credential retrieval in LLMGatewayServer.__init__ ([D-052] addendum 2026-06-12).
    # Rate-limit shape varies by backend:
    # - ollama_a4000: VRAM-bound concurrency (1 model hot, swap on demand); rate_limit_* = None;
    #     concurrency bounded externally by the request semaphore in LLMGatewayServer
    # - vllm_dgx: continuous-batching throughput; rate_limit_* = None typically; concurrency
    #     bounded by vLLM's max-num-seqs config (not by this module)
    # - corp_llm: hard rate-limited per minute/hour/day; values populated from env config
    rate_limit_per_minute: int | None = None
    rate_limit_per_hour:   int | None = None
    rate_limit_per_day:    int | None = None
    # Per-backend operational metadata (informational; consumed by --diagnostic):
    cold_load_expected:    bool = False   # True for ollama_a4000 (model swap on rare-task path);
                                           # False for vllm_dgx (all models hot) + corp_llm
    supports_batching:     bool = False   # True for vllm_dgx (continuous batching);
                                           # False for ollama_a4000 (serializes by default)

class LLMGatewayServer:
    """The egress-side implementation. Owns prompt-template loading from templates/,
    per-TaskKind backend + model selection per [D-052], output-schema validation,
    per-backend rate-limiting (token-bucket), retry policy, structured-output parsing,
    and credential handoff (retrieved once at startup from credential_service, conditionally
    per authenticated backend — see __init__; never logged). Mounts a single FastAPI route POST /invoke;
    rejects unknown TaskKind values."""

    def __init__(
        self,
        backends:           dict[str, BackendConfig],          # {"ollama_a4000": ..., "vllm_dgx": ..., "corp_llm": ...}
        task_backend_map:   dict[TaskKind, str],               # TaskKind → backend name (A/B winner per task)
        task_model_map:     dict[TaskKind, str],               # TaskKind → model id within backend
        credential_service: CredentialService,
        template_dir:       Path = Path("/etc/hilda/llm-templates"),
    ) -> None:
        """All three maps are env-config; no code-level default precedence between backends.
        Each (TaskKind, backend, model) pairing is locked per `[D-052]` only after measured-quality
        A/B testing on representative fixtures across all three backends. Per-task precedence rule:
        the A/B winner becomes the assigned backend for that TaskKind. No automatic spillover to a
        runner-up backend on rate-limit / outage — silent quality degradation is rejected
        (surfaces as `LLG-W006` for ops visibility per [D-052]).

        Credential handoff is per-backend and CONDITIONAL — at most one credential per backend,
        retrieved only when that backend declares auth (`[D-052]` impl-note addendum 2026-06-12):
            for name, backend in backends.items():
                if backend.credential_key is not None:
                    cred = credential_service.get_credential(OPS_TEAM_PM_ID, backend.credential_key)
        Lab-subnet Ollama (`ollama_a4000`) and vLLM (`vllm_dgx`) are typically auth-less
        (`credential_key=None`) — forcing a credential for all three would make the gateway
        un-startable in the common lab config; only `corp_llm` reliably needs one. Credentials
        held in process memory, never written to log/disk/report. `OPS_TEAM_PM_ID` is the
        credential_service constant ("ops-team")."""

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        """Pipeline:
        1. Resolve backend + model from request.task via task_backend_map / task_model_map.
        2. Acquire rate-limit token (token-bucket per backend); on miss → LLG-W005 / -W006.
        3. Render prompt from templates/<task>.j2 + request.inputs.
        4. Call backend (Ollama /api/generate or corp LLM endpoint) with format=json.
        5. Parse output as structured JSON; validate against task's output schema; retry on fail.
        6. Emit MET record (backend, model, latency, token counts, confidence bucket).
        7. Return LLMResponse."""

    async def start(self) -> int:
        """Conditional per-backend credential retrieval — get_credential is async, so the
        async work splits out of __init__ (mirrors credential_service.load()). Returns count
        retrieved; idempotent. Call after construction. (Added at implementation 2026-06-12.)"""

    def set_http_client(self, client: httpx.AsyncClient) -> None:
        """Test/seam — inject the backend HTTP client (tests pass an httpx.MockTransport
        client). (Added 2026-06-12.)"""

    @staticmethod
    def confidence_bucket(confidence: float | None) -> str:
        """high (≥0.85) / medium (≥0.6) / low / n/a — for MET + QC. (Added 2026-06-12.)"""
```

### `MockLLM`

```python
class MockLLM:
    """In-memory deterministic LLM for tests. Register fixed responses per
    (task, inputs-hash); raises LLG-E001 for unregistered combinations.
    Used in unit and integration tests instead of standing up the gateway."""

    source_system: str = "mock_llm"

    def register(self, task: TaskKind, inputs_match: dict, output: dict) -> None: ...
    async def invoke(self, request: LLMRequest) -> LLMResponse: ...
```

---

## Prompt templates

Stored under `core/src/llm/templates/<task_kind>.j2` (Jinja2). Loaded once by `LLMGatewayServer` at startup; reloaded on `reload()` admin signal. One template per `TaskKind` value. Templates carry:

- System prompt (task-specific instructions + structured-output schema reminder).
- User-message body with `{{ variable }}` slots filled from `LLMRequest.inputs`.
- Output-format constraint (JSON-mode where supported by the serving stack).

Templates are part of the code release (versioned with HILDA), not customer-deployment config. Customer-specific content (checklists for REVIEW_DOCUMENT) flows in as `LLMRequest.inputs`, not as template variants.

---

## Invariants

- **All LLM calls go through `LLMProvider`.** No module imports `httpx` or `openai` SDK to talk to an LLM directly. Anchors `[D-007]`.
- **All LLM egress goes through `hilda-llm-gateway`.** `hilda-api` / `hilda-worker` use `OnPremLLMClient`; they do not call any model endpoint directly. Concentrates network policy + retry/rate-limit logic + dual-backend routing.
- **On-prem only — corporate network boundary.** All three backends (`ollama_a4000` on RTX A4000 in lab, `vllm_dgx` on DGX Spark in lab, `corp_llm` on corp infrastructure) live inside the corporate network. No public DNS, no corp proxy-to-public-cloud, no SaaS endpoint. Validated at startup by `LLMGatewayServer` — backend endpoint URLs must resolve to allowed on-prem hosts per `[D-007]` + `[D-052]`.
- **No code-level backend precedence.** Per `[D-052]`, `task_backend_map` is env-config; no Python defaults assume "corp LLM is better for hard tasks" or "DGX Spark is always better than A4000". Each TaskKind's backend is chosen after empirical A/B testing across all three backends on real fixtures; the per-task A/B winner is the precedence rule. Changing the pairing is an env-config change, not a code release. **No automatic spillover** between backends on rate-limit / outage — quality A/B does not generalize across backends; silent degradation is rejected.
- **No proprietary content in compact reports.** RPT/MET/FIX/QC records emit token counts, latency, task kind, model name, confidence buckets — never the prompt body, never the document excerpt, never the model response text. Anchors NFR-2 / `[D-002]`. The proprietary content flows through the LLM but does not surface in the chat-mediated diagnostics channel.
- **Structured output only.** Every task has a Pydantic output schema; non-conforming model responses are retried up to `max_retries` and then raise LLG-E003. The Protocol never returns free-form text to callers.
- **Async, non-blocking.** Per requirements.md NFR (Async LLM tasks): FR-52 step 4 (ROUTE_ATTACHMENT) + `[D-039]` Step 2 (CLASSIFY_DOC), FR-53 (REVIEW_DOCUMENT), FR-12 path(c) (CLASSIFY_MESSAGE) execute as Celery background tasks; the state-change event (`DocumentReceived`, etc.) propagates within the polling-interval SLA regardless of LLM latency. `LLMProvider.invoke` is `async def`; long-running tasks live in `hilda-worker`, not `hilda-api`.
- **Idempotency keys honoured.** Same `(task, idempotency_key)` returns the cached `LLMResponse` from the process-lifetime cache without re-issuing the model call. Useful for Celery retry semantics.
- **Backend credentials never stored on instance post-startup.** `LLMGatewayServer` retrieves **up to one credential per authenticated backend** at startup via `credential_service.get_credential(OPS_TEAM_PM_ID, backend.credential_key)` — only for backends whose `credential_key is not None` (lab Ollama / vLLM are typically auth-less; `corp_llm` needs one) — holds them in process memory, never writes to log/disk/report. `OnPremLLMClient` holds **no** credential (client→gateway is an internal Ph-1 trust-domain hop). Per `[D-052]` impl-note addendum 2026-06-12.

---

## Error codes (LLG prefix — registered in `diagnostics/error_codes.py`)

```
LLG-E001  LLM call failed for task '{task}' backend '{backend}': {reason}  (model unreachable, 5xx, parse failure)
LLG-E002  Unknown TaskKind '{task}' — not in TaskKind enum
LLG-E003  Structured output validation failed after {n} retries for task '{task}' backend '{backend}'
LLG-E004  Backend endpoint '{url}' is not on-prem — rejected by [D-007] / [D-052] startup check
LLG-E005  Prompt template '{task}.j2' not found in template_dir
LLG-E006  TaskKind '{task}' has no backend mapping — missing entry in task_backend_map
LLG-W001  Rate limit hit for task '{task}' backend '{backend}'; queued for {wait_s}s  (recoverable)
LLG-W002  LLM confidence {score} below threshold {threshold} for task '{task}'  (caller decides; not itself a failure)
LLG-W003  Retry {n}/{max} after parse failure on task '{task}' backend '{backend}'
LLG-W004  Model cold-load triggered for '{backend}/{model}'; expected latency +{n}s
LLG-W005  Corp LLM rate limit approaching ({used}/{limit_per_min} per minute); queue depth {n}
LLG-W006  Corp LLM rate limit exceeded; task '{task}' deferred {n}s — no automatic spillover to other backend per [D-052]
LLG-W007  Reserved (placeholder for next storage-adjacent warning code if needed)
LLG-W008  ROUTE_ATTACHMENT returned {n} matches with summed confidence {score} exceeding customer over-routing threshold {threshold} on document '{file_hash}' — potential over-routing; all matches committed per FR-79 contract but flagged for ops review (added 2026-06-09)
```

---

## Key choices

- **`[D-007]`** — on-prem LLM hosting for both runtime and code-generation. This module is the runtime side. Code-generation LLM (used by `api_spec_ingestor`, `template_schema_ingestor`, `test_report_profiler`) runs through the same `hilda-llm-gateway` container per `[D-007]` impl note 2026-05-26, but build-time tools' use of LLM is governed by their own MODULE.md — this module specifies the runtime contract.
- **`[D-053]` impl note 2026-06-08 — `doc_type` classified per FR-85 2-step ladder; `CLASSIFY_DOC_TYPE` RESTORED** (supersedes the withdrawn 2026-05-28b "1:1 derivation" framing). Per FR-85: (Step 1) filename regex against per-customer rules covers all 4 actionable doc_types `{test_report, tech_report, waiver, compliance_certification_release_notes}` — note `compliance_certification_release_notes` is detected by regex ONLY (LLM cannot reliably differentiate the bundled sub-categories from test/tech/waiver content). (Step 2) LLM `CLASSIFY_DOC_TYPE` fires only when Step 1 fails or multi-matches; **restricted candidate set** `{test_report, tech_report, waiver}` (3 values); below-confidence-threshold → caller sets `DocumentIndexRow.doc_type = DocType.UNRESOLVED` sentinel → file moves to FR-86 `staged-not-classified` path → FR-87 step (B) TPM resolution. **Alignment with `item_type`** enforced per FR-86 storage matrix (`TEST_TECH_WAIVER_REPORT` items ↔ `{test/tech/waiver}` doc_types; `COMPLIANCE_CERTIFICATION_RELEASE_NOTES` items ↔ `compliance_certification_release_notes`; `Default` items ↔ any of 5; `Confirmation` items ↔ none); misaligned pairs land on `staged-not-classified` for TPM resolution. TaskKind count: 4 → 5 (restored).
- **`REVIEW_DOCUMENT` is skipped by callers when `doc_type ∈ {compliance_certification_release_notes, unresolved}` OR `review_required = false`** per FR-86 + FR-7 (`review_required = true` only on `TEST_TECH_WAIVER_REPORT` items; false for all other ItemTypes). The `llm` module itself is agnostic — it will run the review against any doc_type if asked — but `workflow_engine`'s `TRIGGER_AI_REVIEW` action (renamed from `TRIGGER_LLM_REVIEW` per FR-29 revised) gates on `item.review_required` AND `doc_type ∈ {test_report, tech_report, waiver}`. No `REVIEW_DOCUMENT` calls reach here for `compliance_certification_release_notes` / `unresolved` / `Default` work-item documents under normal flow.
- **`[D-052]` Tri-backend with empirical routing** (per impl note 2026-06-07 — expanded from dual to tri) — Ph-1 has THREE on-prem LLM providers available:
  - **`ollama_a4000`** — Ollama on RTX A4000 (16 GB GDDR6, ~448 GB/s); single-stream tokens/sec leader for ≤14 GB models; VRAM-bound concurrency (1 model hot, swaps); free per call.
  - **`vllm_dgx`** — vLLM on DGX Spark (128 GB unified LPDDR5X, ~273 GB/s); per-token throughput LOWER than A4000 for the same model (lower bandwidth) but supports models the A4000 cannot fit (30B+, FP16 14B, long-context KV) + vLLM continuous batching wins at concurrency.
  - **`corp_llm`** — corporate LLM service; rate-limited per minute/hour/day; chat + agentic APIs.

  **No backend gets default precedence.** `task_backend_map` is env-config; each TaskKind's `(backend, model)` pairing is locked only after measured-quality A/B testing across all three backends on representative real fixtures. **Precedence rule per task**: the A/B winner is assigned. Capacity-vs-bandwidth note: DGX Spark's 128 GB capacity ≠ throughput advantage for small models — A4000 GDDR6 bandwidth (~448 GB/s) beats DGX Spark LPDDR5X (~273 GB/s) for autoregressive inference of models that fit on A4000. DGX Spark's advantage is large-model capacity, concurrency (many models hot), and vLLM batching. The A/B test result per TaskKind captures these tradeoffs empirically. **No automatic spillover** between backends on rate-limit / outage: silent fallback would risk degraded-quality results without a quality gate. Spillover surfaces as `LLG-W006` for visibility — caller / ops decides whether to manually re-route.
- **`[D-029]` Ph-1 narrow surface** — **5 TaskKinds** (revised 2026-06-09): `ROUTE_ATTACHMENT`, `CLASSIFY_DOC`, `CLASSIFY_DOC_TYPE`, `REVIEW_DOCUMENT`, `CLASSIFY_MESSAGE` (DEF-1 promoted per impl note 2026-05-13; **`CLASSIFY_DOC_TYPE` RESTORED per `[D-053]` impl note 2026-06-08** — un-revert of the 2026-05-28b removal; the prior "1:1 derivation" framing was withdrawn after requirements-phase redesign locked the FR-85 2-step ladder + restricted-candidate-set model). DRAFT_CUSTOMER_REPLY, SUMMARIZE_STATUS deferred to Ph-2 (DEF-3 / DEF-4). Adding a Ph-2 task = add TaskKind value + template + schema + backend mapping; no Protocol-surface change.
- **One Protocol, two implementations** — `OnPremLLMClient` (caller-side HTTP) + `LLMGatewayServer` (egress-side prompt+model orchestration + dual-backend routing) implement the same `LLMProvider` surface. Tests use either `MockLLM` (in-process) or stand up `LLMGatewayServer` with fake backend endpoints. No "client-vs-server" branching in caller code.
- **Structured output as a hard constraint** — alternative is free-form text + caller-side parsing, which scatters parsing logic and makes the contract LLM-dependent. JSON-mode + Pydantic validation localizes the brittleness and gives callers a typed surface.
- **Process-lifetime idempotency cache** — keys cleared on process restart; full Redis-backed persistence deferred to Ph-3+ (DEF-14 family). Acceptable for Ph-1/Ph-2 because Celery retries within the same worker process the majority of the time.
- **Prompt templates in code, customer content in inputs** — templates are versioned with HILDA releases (per `[D-045]` schema-vs-content boundary); customer-specific checklists flow through `LLMRequest.inputs`. Adding a new customer doesn't touch templates.

### Tentative model + backend assignments (pending A/B test)

Per `[D-052]`, every TaskKind's `(backend, model)` pairing must be A/B-tested on real fixtures across all THREE backends (`ollama_a4000`, `vllm_dgx`, `corp_llm`) before production lock-in. Current placeholder assignments — **all on `ollama_a4000`** (cheapest + lowest per-token latency for small models) until A/B runs complete.

**A/B priority** per STATUS.md Flag 2026-05-28: `REVIEW_DOCUMENT` is highest-value (user observation — corp LLM empirically weak at test-case enumeration; FR-53 task shape is checklist-against-content, untested at HILDA scale). Other 3 TaskKinds priority secondary.

**Model catalog**: Same Ollama models served on both `ollama_a4000` (via Ollama) and `vllm_dgx` (via vLLM with HF-compatible weights) for direct A/B comparability. Larger models (e.g., Gemma-27B FP16, Llama-3.1-70B Q4) added to `vllm_dgx` catalog only if A/B-tested small-model results are unsatisfactory.

| TaskKind | Tentative backend | Tentative model | A/B candidates to test |
|---|---|---|---|
| `REVIEW_DOCUMENT` (FR-53) — **highest A/B priority** | `ollama_a4000` | `gemma3:12b` | `vllm_dgx` (same model + larger if needed: `gemma3:27b`, `qwen2.5:32b`) vs `corp_llm` (chat + agentic) — checklist-against-content quality |
| `ROUTE_ATTACHMENT` (FR-52 step 4 of 5 per `[D-053]`) | `ollama_a4000` | `qwen3:8b-q4_k_m` | `vllm_dgx` (same model — bandwidth-bound, A4000 likely wins) vs `corp_llm` |
| `CLASSIFY_DOC` (`[D-039]` Step 2 new-vs-revision) | `ollama_a4000` | `qwen3:8b-q4_k_m` | `vllm_dgx` vs `corp_llm` if rate budget allows |
| `CLASSIFY_DOC_TYPE` (FR-85 Step 2 — restored 2026-06-09) | `ollama_a4000` | `qwen3:8b-q4_k_m` | `vllm_dgx` (same model — small-task, A4000 likely wins) vs `corp_llm`. **A/B priority: lower than REVIEW_DOCUMENT** — task is 3-way restricted classification on first-page excerpt; less LLM-quality-sensitive than checklist-against-content review |
| `CLASSIFY_MESSAGE` (FR-12 path c) | `ollama_a4000` | `qwen3:8b-q4_k_m` | `vllm_dgx` vs `corp_llm` |

`CLASSIFY_DOC_TYPE` row restored 2026-06-09 — TaskKind un-reverted per `[D-053]` impl note 2026-06-08 (the prior 2026-05-28b removal + "1:1 derivation" framing was withdrawn after requirements-phase redesign).

These placeholders live in env config, not code — production env vars on the HILDA PC (where `hilda-llm-gateway` reads them at startup) override after A/B testing. Endpoint URLs for `ollama_a4000` (the A4000 box) and `vllm_dgx` (the DGX Spark box) are separate env vars resolved against the lab-subnet DNS.

---

## Non-goals

- **Not a model hosting / serving stack.** Ollama / corp LLM serving infrastructure and GPU provisioning are out of scope for this module — handled at the deployment / infra / corp-platform layer. This module's only model-side surface is HTTP endpoint URLs in `BackendConfig`.
- **Not a test report parser.** FR-16 (test report → per-test-case `(test_case_id, status, [waiver_ref])` tuples) and FR-46 (`final | interim` classification) are currently scoped as **rule-based** per `[D-011]`. **Two-phase pipeline:**
  - **Build-time** — the Test Report Profiler (a build-time tool, not a runtime module) analyzes the customer's historical test-report corpus on-prem and emits **two outputs**: (a) the per-customer rule-based parser at `customizations/test_report_parsers/<customer_slug>/parser.py` + `layout_config.yaml`, and (b) the `REVIEW_DOCUMENT` checklist at `customizations/checklists/<customer_slug>/<doc_type>.yaml`. One profiler run feeds both consumers. Cline (student LLM) does the heavy reading; engineers review/refine; outputs committed to `customizations/`.
  - **Runtime** — the `test_report` module executes the parser (no LLM in `[D-011]` current scope) producing `parser_result`. THIS module (`llm`) consumes the checklist for `REVIEW_DOCUMENT` producing `llm_review_findings`. The two outputs are written to the same `DocumentIndexRow` but address different concerns — see "Relationship to `parser_result`" below.

  **Open architecture-phase question (flagged 2026-05-28 in STATUS.md)**: rule-based-only parsing per `[D-011]` may be insufficient for the long tail of test reports that don't fit a template (~10% of corpus per user observation: arbitrary feature reports, battery tests, multi-tab xlsx without summary tab). HILDA's parser strategy should be generic (works for any document shape) rather than per-customer custom logic; an LLM-augmented FR-16/FR-46 path may eventually land as a new TaskKind (`EXTRACT_TEST_CASES`) in this module — deferred until the architecture-phase decision on `[D-011]` is revisited.
- **IS a doc_type classifier (Step 2 of the FR-85 2-step ladder)** — but with explicit scope. This module's `CLASSIFY_DOC_TYPE` TaskKind performs ONLY Step 2 of FR-85's 2-step classification ladder: filename regex Step 1 (cheaper, deterministic) runs first in `email_service.attachment_router` against `customizations/<customer_slug>/doc_type_filename_rules.yaml`; THIS module's LLM Step 2 fires only when regex Step 1 fails or multi-matches. The LLM candidate set is **restricted to 3 values** `{test_report, tech_report, waiver}` — never returns `compliance_certification_release_notes` (regex-only because bundled sub-categories are visually indistinguishable from test/tech/waiver content via first-page excerpt) and never returns `unresolved` directly (caller-side sentinel mapped from below-threshold confidence). Reverted 2026-06-09 per `[D-053]` impl note 2026-06-08 — the prior "Not a doc_type classifier" Non-goal claim was withdrawn along with the "1:1 derivation" framing. Restricted-candidate-set rationale: keeps the LLM contract narrow (3-way classification on structurally similar content) — broader 5-way classification would invite hallucination on the bundle categories without precision gain.
- **Not a build-time LLM module.** Code-generation LLM use by `api_spec_ingestor` / `template_schema_ingestor` / `test_report_profiler` shares the same `hilda-llm-gateway` egress per `[D-007]` impl note, but those tools' Protocol surfaces live in their own modules.
- **Relationship to `parser_result`** — `storage.DocumentIndexRow` carries TWO independently-produced fields about a document, and they are complementary (not redundant):

  | Field | What it answers | Produced by | Anchor | Applies to (`doc_type`) | Gate |
  |---|---|---|---|---|---|
  | `parser_result` | "What's IN the document" — per-test-case rows `(tc_id, status, comment, waiver_ref)`, summary stats, engineer/date metadata | `test_report` module rule-based parser (NOT this module) | FR-16 / FR-46 / `[D-011]` | `test_report` only | Fires whenever a test_report is received |
  | `llm_review_findings` | "Is the document ACCEPTABLE per customer standards" — per-checklist-criterion findings + overall verdict | `llm` module via `REVIEW_DOCUMENT` TaskKind (this module) | FR-53 / `[D-052]` | `test_report` ∪ `tech_report` ∪ `waiver` only | Gates on `item.review_required = true` (true on `TEST_TECH_WAIVER_REPORT` items only per FR-7); skipped when `doc_type ∈ {compliance_certification_release_notes, unresolved}` |

  **Coexistence matrix per document** (under the 5-value DocType per `[D-053]` impl note 2026-06-08):
  - `parser_result` ✓ + `llm_review_findings` ✓ → test_report on a `TEST_TECH_WAIVER_REPORT` item with `review_required=true` (full coverage)
  - `parser_result` ✓ + `llm_review_findings` ✗ → test_report on a `TEST_TECH_WAIVER_REPORT` item with `review_required=false` (review opt-out)
  - `parser_result` ✗ + `llm_review_findings` ✓ → tech_report or waiver on a `TEST_TECH_WAIVER_REPORT` item with `review_required=true` (parser is test_report-specific; review still applies)
  - `parser_result` ✗ + `llm_review_findings` ✗ → doc_type ∈ `{compliance_certification_release_notes, unresolved}` (FR-16 + FR-53 both gate off) OR landed on `Default` work-item (`review_required` hardcoded false; FR-16 only fires if doc_type=test_report so works through Default for test_reports — see special case below)
  - **Special case**: test_report classified on `Default` work-item → `parser_result` ✓ (FR-16 fires on doc_type=test_report regardless of item) + `llm_review_findings` ✗ (FR-53 gated off because `review_required=false` on Default). Useful for TPM at FR-83 reassignment time.

  Both are written to `DocumentIndexRow` via `storage.update_review_findings(file_hash, parser_result, llm_review_findings)`. The `test_report` module writes the first; `workflow_engine` (driven by `TRIGGER_AI_REVIEW` action per FR-29 revised) drives this module to write the second. Downstream consumers — FR-47 failed-no-waiver surface, FR-48 auto-waiver-item creation, PLM upload manifest — read `parser_result`; the FR-53 dashboard quality surface reads `llm_review_findings`.

- **Not a chat / streaming interface.** No streaming responses, no multi-turn conversation state. Every task is a single request → single structured response.
- **Not an agentic-loop framework.** No `function_calling`, no multi-step agent traversal in Ph-1. If corp LLM's agentic API becomes empirically superior on a TaskKind (per `[D-052]` A/B test), the agentic surface would be wrapped inside `LLMGatewayServer.invoke()` for that task — caller-side Protocol remains one request → one structured response.
- **Not a credentials store.** LLM API key flows through `credential_service`; never persisted in this module.
- **Not a backend spillover / failover engine.** Per `[D-052]`, no automatic backend swap on rate-limit / outage. Quality gate (the empirical A/B that locked the pairing) does not generalize across backends; silent degradation is rejected.
- **Not a Drafted-reply / summarization surface in Ph-1.** DEF-3 / DEF-4 deferred to Ph-2.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate` (LLG codes registered in `error_codes.py`).
- `credential_service` — `get_credential(OPS_TEAM_PM_ID, backend.credential_key)` called once at `LLMGatewayServer` startup, **conditionally per backend** (only when `BackendConfig.credential_key is not None`); `credential_key` holds the per-backend `SystemType` value (`LLM_OLLAMA_A4000` / `LLM_VLLM_DGX` / `LLM_CORP_LLM`). Lab backends are typically auth-less. `OnPremLLMClient` does **not** depend on `credential_service` (client→gateway hop is unauthenticated within the Ph-1 trust domain). Per `[D-052]` impl-note addendum 2026-06-12.
- `template_schema` — 5-value `DocType` enum (test_report / tech_report / waiver / compliance_certification_release_notes / unresolved per `[D-053]` impl note 2026-06-08) consumed by `CLASSIFY_DOC_TYPE` (FR-85) candidate set + `CLASSIFY_DOC` (`[D-039]` Step 2) family scope + `REVIEW_DOCUMENT` skip rule; `IngestSource` enum consumed by `CLASSIFY_DOC` inputs.

---

## Depended on by

- `email_service` — FR-52 5-step routing pipeline per `[D-053]`: ROUTE_ATTACHMENT at step 4 (after steps 1-3 substring/fuzzy/folder-template fail; step 5 fall-through to default work-item per FR-78 is non-LLM); CLASSIFY_DOC for `[D-039]` Step 2 new-vs-revision classification; CLASSIFY_MESSAGE for FR-12 path (c) message-intent fallback.
- `storage` — `[D-039]` Step 2 classification of inbound NSD documents.
- `workflow_engine` — FR-53 REVIEW_DOCUMENT trigger after AttachmentReceived events (rules 15, 16, 17 per requirements.md).
- `issue_tracker` — `[D-039]` classification of corp-PLM-polled documents (FR-26).
- `dashboard` — surfaces `llm_review_findings` from `REVIEW_DOCUMENT` outputs to PM.

---

## Test interface

```
python -m core.src.llm.llm_cli --diagnostic
```
Calls `LLMProvider.health()` against the configured `hilda-llm-gateway` endpoint; probes every backend in `task_backend_map`; emits no proprietary content:
```
RPT|LLG|run-00001|2026-05-28T10:00:00Z|gateway_reachable=true|backends_total=3|backends_reachable=3|ollama_a4000_models=gemma3:12b,qwen3:8b-q4_k_m|vllm_dgx_models=gemma3:12b,qwen3:8b-q4_k_m|corp_llm_ready=true|templates_loaded=5
```

```
python -m core.src.llm.llm_cli --mock
```
Spins up `MockLLM` pre-registered with canned responses for each `TaskKind`; integration tests run end-to-end without standing up the gateway.

```
python -m core.src.llm.llm_cli --invoke --task review_document --input-file <fixture.json>
```
Submits one real LLM call against the gateway using the named fixture; emits a `LLG-MET`:
```
MET|LLG|run-00001|2026-05-27T10:00:00Z|task=review_document|model=llama3-on-prem|latency_ms=2840|tokens_in=1842|tokens_out=312|confidence_bucket=high
```
The `--invoke` mode never logs the input text or model response — only token counts, latency, and bounded confidence bucket per NFR-2.

```
python -m core.src.llm.llm_cli --contract
```
Runs a structured-output contract suite against every registered `TaskKind`: synthetic inputs → invoke → output validates against schema. Per-task pass/fail, no proprietary content in report.

**QC template** (`LLG:task_contract` — registered in `diagnostics/qc.py`):
```
Fields: task (enum: TaskKind values), schema_valid (bool), latency_ms (int),
        confidence_bucket (enum: high|medium|low|n/a), result (enum: OK / WARN / FAIL)
```

---

<!-- BEGIN:STRUCTURE -->

- `BackendConfig` — class — pub — One backend = one LLM serving endpoint. Three Ph-1 backends per [D-052].
- `ClassifyDocInput` — class — pub
- `ClassifyDocOutput` — class — pub
- `ClassifyDocTypeInput` — class — pub — FR-85 Step 2 — fires only when Step 1 filename regex fails or multi-matches.
- `ClassifyDocTypeOutput` — class — pub — Below-threshold confidence (default 0.85) → caller sets DocType.UNRESOLVED sentinel.
- `ClassifyMessageInput` — class — pub
- `ClassifyMessageOutput` — class — pub
- `ExistingDocCandidate` — class — pub
- `LLMGatewayServer` — class — pub — Egress-side implementation. __init__ does synchronous config validation; `start()`
- `LLMProvider` — class — pub — All callers depend on this Protocol, not on a concrete implementation.
- `LLMRequest` — class — pub
- `LLMResponse` — class — pub
- `MockLLM` — class — pub — Full LLMProvider surface, deterministic. Used in unit + integration tests
- `OnPremLLMClient` — class — pub
- `ReviewDocumentInput` — class — pub
- `ReviewDocumentOutput` — class — pub
- `RouteAttachmentInput` — class — pub
- `RouteAttachmentMatch` — class — pub — One above-threshold (item_id, confidence) match. Per FR-79 a document may land on
- `RouteAttachmentOutput` — class — pub — LIST of above-threshold matches. EMPTY → caller falls through to FR-52 step 5
- `TaskKind` — class — pub — Bounded set of runtime LLM tasks. Each value maps 1:1 to a prompt template in

<!-- END:STRUCTURE -->
