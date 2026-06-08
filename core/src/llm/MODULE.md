# Module: llm

> **Status:** Draft + 2026-06-07 Phase B rollback applied (`[D-053]` routing model — CLASSIFY_DOC_TYPE removed; FR-52 5-step pipeline; REVIEW_DOCUMENT DEFAULT-skip note). Sections curated; code implementation begins after `/switch-phase development`.
>
> **Rollback log:**
> - **2026-06-07 (host-topology clarification, same day)** — fixed stale "Ollama on the HILDA PC GPU" framing across 4 sites. Clarified that **HILDA PC is a distinct Linux box from the model-serving hosts**: HILDA PC runs the HILDA app containers (hilda-api / hilda-worker / hilda-llm-gateway per `[D-021]`/`[D-026]`) and has NO GPU; `ollama_a4000` runs on a separate **A4000 box** (Linux + RTX A4000) on the lab subnet; `vllm_dgx` runs on a separate **DGX Spark box** (GB10 + 128 GB unified) on the lab subnet; `corp_llm` is corp infrastructure. hilda-llm-gateway proxies LLM calls OUTWARD from the HILDA PC to these three hosts. Updated `[D-007]` On-prem paragraph (now enumerates 3 distinct hosts + HILDA PC); Hardware-topology table (Host column clarifies "separate machine" per backend + footer note "Distinct from HILDA PC"); BackendConfig.endpoint_url example hostnames changed from `hilda-pc:11434` to `a4000-box:11434` for ollama_a4000 + clarifying comment; tentative-assignments footer clarifies env vars live on HILDA PC but resolve to lab-subnet DNS for the model boxes. **`[D-052]` DECISIONS.md body** still contains the older "HILDA PC GPU" wording at lines 638-639 — covered by the existing STATUS.md flag ("`[D-052]` ADR body needs impl note appended") since `[D-002]` ADR-body immutability prevents direct edit; the pending impl note 2026-06-07 will document the 3-host topology.
> - **2026-06-07 (docstring clarifications, same day)** — three additions captured from review-session Q&A: (a) `ReviewDocumentInput.checklist` docstring expanded — clarified as build-time-generated YAML (NOT hand-authored, NOT runtime-built) at `customizations/checklists/<slug>/<doc_type>.yaml`, per-customer because carriers have different acceptance criteria; (b) Non-goals "Not a test report parser" expanded into a two-phase pipeline breakdown — build-time Test Report Profiler emits BOTH the rule-based parser AND the REVIEW_DOCUMENT checklist (one profiler run, two outputs), runtime split: `test_report` module runs parser, `llm` module consumes checklist; (c) new "Relationship to `parser_result`" Non-goal section — explicit comparison table contrasting `parser_result` (content extraction, FR-16, rule-based, test_report-only) vs `llm_review_findings` (quality assessment, FR-53, LLM-driven, test_report/tech_report/waiver, gated on `review_required` + skipped for DEFAULT) + 4-quadrant coexistence matrix per document. No API surface change; pure docstring + cross-reference improvements.
> - **2026-06-07 (tri-backend amendment, same day)** — `[D-052]` framing expanded from dual-backend to tri-backend per user clarification on hardware topology: `BackendConfig.name` Literal expanded from 2 values to 3 (`ollama_a4000`, `vllm_dgx`, `corp_llm`); added Hardware-topology section to Purpose documenting per-backend memory/bandwidth/profile with capacity-vs-bandwidth note (DGX Spark's 128 GB ≠ throughput advantage for small models — A4000's GDDR6 ~448 GB/s beats DGX Spark LPDDR5X ~273 GB/s for autoregressive inference of fits-on-A4000 models; DGX Spark wins on capacity, concurrency, vLLM batching); BackendConfig gains `cold_load_expected` + `supports_batching` informational fields; LLMGatewayServer init docstring updated for tri-backend A/B; Invariants "On-prem only" + "No code-level backend precedence" updated to enumerate all 3 backends + add no-automatic-spillover restatement; Key choices `[D-052]` bullet rewritten as tri-backend with per-backend profile + precedence rule per task (A/B winner); tentative-assignments table gains separate `vllm_dgx` A/B candidate column + model catalog note (same Ollama models served on both ollama_a4000 + vllm_dgx for direct comparability; larger models added to vllm_dgx only if small-model A/B unsatisfactory); test-interface RPT line updated to backends_total=3 with per-backend model lists. **STATUS.md follow-up flagged**: A/B-test gate flag (2026-05-28; `[D-052]` Consequences) must be updated to reflect tri-backend testing matrix (was dual-backend); `[D-052]` ADR body in DECISIONS.md needs an impl note appended (2026-06-07 dual→tri expansion) per `[D-002]` append-only convention.
> - **2026-06-07** — Phase B Module rollback (Group 3 of N, after `template_schema/MODULE.md` + `storage/MODULE.md`): TaskKind enum reduced 5 → 4 (`CLASSIFY_DOC_TYPE` removed per `[D-053]` + `[D-052]` impl note 2026-05-28b — doc_type now derived 1:1 from `item.item_type` at routing time, not LLM-classified); removed `ClassifyDocTypeInput`/`ClassifyDocTypeOutput` schemas; updated Purpose anchors to include `[D-053]`; FR-52 framing updated from `[D-033]` 2-tier to `[D-053]` 5-step pipeline (ROUTE_ATTACHMENT now contextualized as step 4 of 5; failure falls through to step 5 default work-item per FR-78); RouteAttachmentInput docstring clarifies candidate_items come from FR-52 caller as narrowed set surviving steps 1-3 (substring / fuzzy / folder-template per FR-77 Type-2); Key choices added two bullets ([D-053] doc_type derivation + REVIEW_DOCUMENT DEFAULT-skip per FR-7 amendment); Non-goals rewrote "Not a filename-rule doc_type classifier" → "Not a doc_type classifier" with structural-derivation rationale; tentative-assignments table dropped CLASSIFY_DOC_TYPE row + added A/B-priority note (REVIEW_DOCUMENT highest); two stale "Tier-2" references in Invariants + Depended-on-by updated to step-4 framing. No new TaskKinds added for FR-77 / FR-78 / FR-79 / FR-82 / FR-83 / FR-84 — those are non-LLM concerns.

**Purpose**: Single Protocol-mediated surface (`LLMProvider`) for every runtime LLM call HILDA makes — new-vs-revision classification (Tier-2 in the `[D-039]` 4-step classifier), attachment routing (**step 4 of the FR-52 5-step routing pipeline per `[D-053]`**; `[D-033]` Tier-2 framing superseded), document quality review (FR-53), and message classification fallback (FR-12 path c per `[D-034]`). Owns prompt-template loading, per-TaskKind backend selection across **three on-prem backends** (Ollama on RTX A4000 + vLLM on DGX Spark + corp on-prem LLM per `[D-052]`), rate-limiting, retry policy, structured-output parsing, and the LLM API key handoff from `credential_service`. Anchors `[D-007]` (on-prem LLM hosting), `[D-029]` (Ph-1 LLM scope), `[D-033]` (Tier-2 framing superseded by `[D-053]`), `[D-034]`, `[D-039]`, `[D-052]` (multi-backend, empirical routing), `[D-053]` (routing model + doc_type derivation removes `CLASSIFY_DOC_TYPE`); serves FR-12, FR-52, FR-53, NFR-1, NFR-2.

> **NB**: `CLASSIFY_DOC_TYPE` TaskKind was removed 2026-05-28b per `[D-052]` impl note + `[D-053]` routing model — doc_type is now derived 1:1 from `item.item_type` at routing time, not classified from document content. No runtime LLM call participates in doc_type assignment. See Non-goals.

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

**Ph-1 scope per `[D-029]` impl note 2026-05-13 + `[D-052]` impl note 2026-05-28b + `[D-053]`** — three runtime functions / **four TaskKinds total**:
1. FR-52 attachment routing — **two TaskKinds**: `ROUTE_ATTACHMENT` (step 4 of FR-52 5-step pipeline — invoked only when steps 1-3 substring/fuzzy/folder-template fail; output `None` falls through to step 5 default work-item per FR-78) + `CLASSIFY_DOC` (`[D-039]` Step 2 new-vs-revision).
2. FR-53 document quality review (document content + checklist → findings list) — `REVIEW_DOCUMENT`.
3. FR-12 path (c) message classification fallback (message body → intent label) — `CLASSIFY_MESSAGE`.

`CLASSIFY_DOC_TYPE` (5th TaskKind in the pre-2026-05-28b design) was removed per `[D-053]` — doc_type is structural (derived 1:1 from `item.item_type`), not content-classified.

**Ph-2 surface (deferred)**: DEF-3 (LLM-drafted customer responses — `DRAFT_CUSTOMER_REPLY`), DEF-4 (status summarization — `SUMMARIZE_STATUS`). Not on this module's Ph-1 Protocol surface. Adding a Ph-2 task requires four changes: a new `TaskKind` enum value, a prompt template under `templates/`, input/output Pydantic schemas in `schemas.py`, and a `task_backend_map` + `task_model_map` entry in env-config — no Protocol-surface change.

---

## Public surface

### `protocol.py`

```python
class TaskKind(str, Enum):
    """Bounded set of runtime LLM tasks. Each value maps 1:1 to a prompt template
    in templates/ and a structured output schema in schemas.py.
    Four Ph-1 TaskKinds (2026-05-28b: CLASSIFY_DOC_TYPE removed per `[D-053]`)."""
    ROUTE_ATTACHMENT      = "route_attachment"      # FR-52 step 4 of 5 per [D-053] (was [D-033] Tier-2, framing superseded) — attachment → DeliveryItem match
    CLASSIFY_DOC          = "classify_doc"          # [D-039] Step 2 — new document vs revision-of-existing
    REVIEW_DOCUMENT       = "review_document"       # FR-53 — quality review against checklist; skipped by caller for doc_type == DEFAULT per FR-7 amendment + [D-053]
    CLASSIFY_MESSAGE      = "classify_message"      # FR-12 path (c) — message-intent fallback
    # Removed 2026-05-28b per [D-052] impl note + [D-053] (doc_type now derived from item.item_type):
    # CLASSIFY_DOC_TYPE  — REMOVED
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
# CLASSIFY_DOC_TYPE schemas — REMOVED 2026-05-28b per [D-052] impl note + [D-053]
# (doc_type is derived 1:1 from item.item_type at routing time; no runtime LLM classification)

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
class RouteAttachmentOutput(BaseModel):
    item_id:           str | None  # None when confidence below threshold → caller falls through
                                    # to FR-52 step 5 (default work-item per FR-78); routing_resolution
                                    # recorded on DocumentIndexRow as StagedDefault
    confidence:        float

# REVIEW_DOCUMENT — FR-53
class ReviewDocumentInput(BaseModel):
    doc_excerpt: str
    doc_type:    str               # test_report | tech_report | waiver — NOT called for "default" per FR-7 amendment + [D-053]
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
        credential_service: CredentialService,         # system_type = SystemType.LLM_GATEWAY
        max_retries:        int   = 3,
        retry_backoff_s:    float = 1.0,
    ) -> None: ...

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
    credential_key: str | None        # which credential_service key carries auth, if any
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
    and credential handoff (retrieved once at startup from credential_service,
    system_type=LLM_GATEWAY, never logged). Mounts a single FastAPI route POST /invoke;
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
        (surfaces as `LLG-W006` for ops visibility per [D-052])."""

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        """Pipeline:
        1. Resolve backend + model from request.task via task_backend_map / task_model_map.
        2. Acquire rate-limit token (token-bucket per backend); on miss → LLG-W005 / -W006.
        3. Render prompt from templates/<task>.j2 + request.inputs.
        4. Call backend (Ollama /api/generate or corp LLM endpoint) with format=json.
        5. Parse output as structured JSON; validate against task's output schema; retry on fail.
        6. Emit MET record (backend, model, latency, token counts, confidence bucket).
        7. Return LLMResponse."""
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
- **LLM API key never stored on instance post-startup.** `LLMGatewayServer` retrieves it once at startup via `credential_service.get_credential(pm_id="ops", system_type=SystemType.LLM_GATEWAY)`, holds it in process memory, never writes it to log/disk/report.

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
```

---

## Key choices

- **`[D-007]`** — on-prem LLM hosting for both runtime and code-generation. This module is the runtime side. Code-generation LLM (used by `api_spec_ingestor`, `template_schema_ingestor`, `test_report_profiler`) runs through the same `hilda-llm-gateway` container per `[D-007]` impl note 2026-05-26, but build-time tools' use of LLM is governed by their own MODULE.md — this module specifies the runtime contract.
- **`[D-053]` doc_type derivation removes CLASSIFY_DOC_TYPE** — Per `[D-053]` + FR-7 amendment, `doc_type` is derived 1:1 from `item.item_type` at routing time (not from document content). When routing succeeds (FR-52 5-step pipeline), doc_type is known structurally from the resolved item. When routing fails → lands on milestone's default work-item per FR-78 with `doc_type = DEFAULT`. The pre-2026-05-28b `CLASSIFY_DOC_TYPE` TaskKind that classified opaque-filename documents into `{test_report, tech_report, waiver}` was removed. Net TaskKind count dropped 5 → 4.
- **`REVIEW_DOCUMENT` is skipped by callers for `doc_type == DEFAULT`** per FR-7 amendment + `[D-053]`. The `llm` module itself is agnostic — it will run the review against any doc_type if asked — but `workflow_engine`'s `TRIGGER_AI_REVIEW` action (renamed from `TRIGGER_LLM_REVIEW` per FR-29 revised) gates on `item.review_required`, which is hardcoded `False` for default work-items. No `REVIEW_DOCUMENT` calls reach here for DEFAULT documents under normal flow.
- **`[D-052]` Tri-backend with empirical routing** (per impl note 2026-06-07 — expanded from dual to tri) — Ph-1 has THREE on-prem LLM providers available:
  - **`ollama_a4000`** — Ollama on RTX A4000 (16 GB GDDR6, ~448 GB/s); single-stream tokens/sec leader for ≤14 GB models; VRAM-bound concurrency (1 model hot, swaps); free per call.
  - **`vllm_dgx`** — vLLM on DGX Spark (128 GB unified LPDDR5X, ~273 GB/s); per-token throughput LOWER than A4000 for the same model (lower bandwidth) but supports models the A4000 cannot fit (30B+, FP16 14B, long-context KV) + vLLM continuous batching wins at concurrency.
  - **`corp_llm`** — corporate LLM service; rate-limited per minute/hour/day; chat + agentic APIs.

  **No backend gets default precedence.** `task_backend_map` is env-config; each TaskKind's `(backend, model)` pairing is locked only after measured-quality A/B testing across all three backends on representative real fixtures. **Precedence rule per task**: the A/B winner is assigned. Capacity-vs-bandwidth note: DGX Spark's 128 GB capacity ≠ throughput advantage for small models — A4000 GDDR6 bandwidth (~448 GB/s) beats DGX Spark LPDDR5X (~273 GB/s) for autoregressive inference of models that fit on A4000. DGX Spark's advantage is large-model capacity, concurrency (many models hot), and vLLM batching. The A/B test result per TaskKind captures these tradeoffs empirically. **No automatic spillover** between backends on rate-limit / outage: silent fallback would risk degraded-quality results without a quality gate. Spillover surfaces as `LLG-W006` for visibility — caller / ops decides whether to manually re-route.
- **`[D-029]` Ph-1 narrow surface** — 4 TaskKinds: `ROUTE_ATTACHMENT`, `CLASSIFY_DOC`, `REVIEW_DOCUMENT`, `CLASSIFY_MESSAGE` (DEF-1 promoted per impl note 2026-05-13; `CLASSIFY_DOC_TYPE` removed per `[D-053]` impl note 2026-05-28b). DRAFT_CUSTOMER_REPLY, SUMMARIZE_STATUS deferred to Ph-2 (DEF-3 / DEF-4). Adding a Ph-2 task = add TaskKind value + template + schema + backend mapping; no Protocol-surface change.
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
| `CLASSIFY_MESSAGE` (FR-12 path c) | `ollama_a4000` | `qwen3:8b-q4_k_m` | `vllm_dgx` vs `corp_llm` |

`CLASSIFY_DOC_TYPE` row removed 2026-05-28b — TaskKind eliminated per `[D-053]` (doc_type now structural, not LLM-classified).

These placeholders live in env config, not code — production env vars on the HILDA PC (where `hilda-llm-gateway` reads them at startup) override after A/B testing. Endpoint URLs for `ollama_a4000` (the A4000 box) and `vllm_dgx` (the DGX Spark box) are separate env vars resolved against the lab-subnet DNS.

---

## Non-goals

- **Not a model hosting / serving stack.** Ollama / corp LLM serving infrastructure and GPU provisioning are out of scope for this module — handled at the deployment / infra / corp-platform layer. This module's only model-side surface is HTTP endpoint URLs in `BackendConfig`.
- **Not a test report parser.** FR-16 (test report → per-test-case `(test_case_id, status, [waiver_ref])` tuples) and FR-46 (`final | interim` classification) are currently scoped as **rule-based** per `[D-011]`. **Two-phase pipeline:**
  - **Build-time** — the Test Report Profiler (a build-time tool, not a runtime module) analyzes the customer's historical test-report corpus on-prem and emits **two outputs**: (a) the per-customer rule-based parser at `customizations/test_report_parsers/<customer_slug>/parser.py` + `layout_config.yaml`, and (b) the `REVIEW_DOCUMENT` checklist at `customizations/checklists/<customer_slug>/<doc_type>.yaml`. One profiler run feeds both consumers. Cline (student LLM) does the heavy reading; engineers review/refine; outputs committed to `customizations/`.
  - **Runtime** — the `test_report` module executes the parser (no LLM in `[D-011]` current scope) producing `parser_result`. THIS module (`llm`) consumes the checklist for `REVIEW_DOCUMENT` producing `llm_review_findings`. The two outputs are written to the same `DocumentIndexRow` but address different concerns — see "Relationship to `parser_result`" below.

  **Open architecture-phase question (flagged 2026-05-28 in STATUS.md)**: rule-based-only parsing per `[D-011]` may be insufficient for the long tail of test reports that don't fit a template (~10% of corpus per user observation: arbitrary feature reports, battery tests, multi-tab xlsx without summary tab). HILDA's parser strategy should be generic (works for any document shape) rather than per-customer custom logic; an LLM-augmented FR-16/FR-46 path may eventually land as a new TaskKind (`EXTRACT_TEST_CASES`) in this module — deferred until the architecture-phase decision on `[D-011]` is revisited.
- **Not a doc_type classifier.** Per `[D-053]` `doc_type` is derived 1:1 from `item.item_type` at routing time — NOT classified from document content. When the FR-52 5-step routing pipeline resolves a specific work-item, doc_type is known structurally from that item. Routing failures land on the milestone's default work-item per FR-78 with `doc_type = DEFAULT` (FR-7 amendment). No runtime LLM call participates in doc_type assignment. The pre-2026-05-28b `CLASSIFY_DOC_TYPE` TaskKind (which classified opaque-filename documents into `{test_report, tech_report, waiver}` from first-page excerpt) was removed when this design landed.
- **Not a build-time LLM module.** Code-generation LLM use by `api_spec_ingestor` / `template_schema_ingestor` / `test_report_profiler` shares the same `hilda-llm-gateway` egress per `[D-007]` impl note, but those tools' Protocol surfaces live in their own modules.
- **Relationship to `parser_result`** — `storage.DocumentIndexRow` carries TWO independently-produced fields about a document, and they are complementary (not redundant):

  | Field | What it answers | Produced by | Anchor | Applies to (`doc_type`) | Gate |
  |---|---|---|---|---|---|
  | `parser_result` | "What's IN the document" — per-test-case rows `(tc_id, status, comment, waiver_ref)`, summary stats, engineer/date metadata | `test_report` module rule-based parser (NOT this module) | FR-16 / FR-46 / `[D-011]` | `test_report` only | Fires whenever a test_report is received |
  | `llm_review_findings` | "Is the document ACCEPTABLE per customer standards" — per-checklist-criterion findings + overall verdict | `llm` module via `REVIEW_DOCUMENT` TaskKind (this module) | FR-53 / `[D-052]` | `test_report` ∪ `tech_report` ∪ `waiver` | Gates on `item.review_required = true`; skipped for `doc_type == DEFAULT` |

  **Coexistence matrix per document:**
  - `parser_result` ✓ + `llm_review_findings` ✓ → test_report with review enabled (full coverage)
  - `parser_result` ✓ + `llm_review_findings` ✗ → test_report with review opt-out (`review_required=false`)
  - `parser_result` ✗ + `llm_review_findings` ✓ → tech_report or waiver (parser is test_report-specific; review still applies)
  - `parser_result` ✗ + `llm_review_findings` ✗ → DEFAULT doc_type (default work-item; neither fires)

  Both are written to `DocumentIndexRow` via `storage.update_review_findings(file_hash, parser_result, llm_review_findings)`. The `test_report` module writes the first; `workflow_engine` (driven by `TRIGGER_AI_REVIEW` action per FR-29 revised) drives this module to write the second. Downstream consumers — FR-47 failed-no-waiver surface, FR-48 auto-waiver-item creation, PLM upload manifest — read `parser_result`; the FR-53 dashboard quality surface reads `llm_review_findings`.

- **Not a chat / streaming interface.** No streaming responses, no multi-turn conversation state. Every task is a single request → single structured response.
- **Not an agentic-loop framework.** No `function_calling`, no multi-step agent traversal in Ph-1. If corp LLM's agentic API becomes empirically superior on a TaskKind (per `[D-052]` A/B test), the agentic surface would be wrapped inside `LLMGatewayServer.invoke()` for that task — caller-side Protocol remains one request → one structured response.
- **Not a credentials store.** LLM API key flows through `credential_service`; never persisted in this module.
- **Not a backend spillover / failover engine.** Per `[D-052]`, no automatic backend swap on rate-limit / outage. Quality gate (the empirical A/B that locked the pairing) does not generalize across backends; silent degradation is rejected.
- **Not a Drafted-reply / summarization surface in Ph-1.** DEF-3 / DEF-4 deferred to Ph-2.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate` (LLG codes registered in `error_codes.py`).
- `credential_service` — `get_credential(pm_id, SystemType.LLM_GATEWAY)` called once at `LLMGatewayServer` startup for each configured backend (one credential per backend in `BackendConfig.credential_key`).
- `template_schema` — `DocType`, `IngestSource` enums consumed by CLASSIFY_DOC inputs.

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
RPT|LLG|run-00001|2026-05-28T10:00:00Z|gateway_reachable=true|backends_total=3|backends_reachable=3|ollama_a4000_models=gemma3:12b,qwen3:8b-q4_k_m|vllm_dgx_models=gemma3:12b,qwen3:8b-q4_k_m|corp_llm_ready=true|templates_loaded=4
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
<!-- END:STRUCTURE -->
