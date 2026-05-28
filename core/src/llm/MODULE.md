# Module: llm

**Purpose**: Single Protocol-mediated surface (`LLMProvider`) for every runtime LLM call HILDA makes — document classification (Tier-2 in the `[D-039]` 4-step classifier), attachment routing (Tier-2 in the `[D-033]` two-tier FR-52 router), document quality review (FR-53), and message classification fallback (FR-12 path c per `[D-034]`). Owns prompt-template loading, per-TaskKind backend selection (local Ollama vs corp on-prem LLM per `[D-052]`), rate-limiting, retry policy, structured-output parsing, and the LLM API key handoff from `credential_service`. Anchors `[D-007]` (on-prem LLM hosting), `[D-029]` (Ph-1 LLM scope), `[D-033]`, `[D-034]`, `[D-039]`, `[D-052]` (dual-backend, empirical routing); serves FR-12, FR-52, FR-53, NFR-1, NFR-2.

**On-prem only per `[D-007]`**: No public-cloud LLM, no SaaS LLM, no corp-proxy-to-public-cloud call. "On-prem" is the corporate network boundary — both **local Ollama** on the HILDA PC and **corp on-prem LLM** (hosted on corporate infrastructure, reachable from the HILDA lab subnet) satisfy the invariant per `[D-052]`. Ph-1/Ph-2 model serving: local Ollama on the HILDA PC GPU + corp LLM at the corp-internal endpoint. Ph-3+ same Protocol surface deployed on MicroK8s per `[D-043]`.

**Workload assignment**: The `hilda-llm-gateway` container per `[D-021]` is the sole HILDA workload that issues outbound LLM calls — `hilda-api` and `hilda-worker` import this module's *client-side* Protocol surface and proxy calls to `hilda-llm-gateway` over HTTP. Concentrating egress in one workload simplifies model-endpoint network policy (one container needs egress to both Ollama and corp LLM endpoints) and contains LLM-side failures from cascading into API request handling.

**Ph-1 scope per `[D-029]` impl note 2026-05-13** — three runtime functions only (FR-52 attachment routing decomposes into multiple TaskKinds per `[D-052]` impl note 2026-05-28):
1. FR-52 attachment routing — three TaskKinds: `CLASSIFY_DOC_TYPE` (filename-opaque → doc_type), `ROUTE_ATTACHMENT` (Tier-2 item match), `CLASSIFY_DOC` (D-039 Step 2 new-vs-revision).
2. FR-53 document quality review (document content + checklist → findings list) — `REVIEW_DOCUMENT`.
3. FR-12 path (c) message classification fallback (message body → intent label) — `CLASSIFY_MESSAGE`.

**Ph-2 surface (deferred)**: DEF-3 (LLM-drafted customer responses), DEF-4 (status summarization). Not on this module's Ph-1 Protocol surface.

---

## Public surface

### `protocol.py`

```python
class TaskKind(str, Enum):
    """Bounded set of runtime LLM tasks. Each value maps 1:1 to a prompt template
    in templates/ and a structured output schema in schemas.py."""
    CLASSIFY_DOC_TYPE     = "classify_doc_type"     # FR-52 / FR-55 — filename opaque → doc_type ∈ {test_report, tech_report, waiver}
    ROUTE_ATTACHMENT      = "route_attachment"      # [D-033] Tier-2 — attachment → DeliveryItem match
    CLASSIFY_DOC          = "classify_doc"          # [D-039] Step 2 — new document vs revision-of-existing
    REVIEW_DOCUMENT       = "review_document"       # FR-53 — quality review against checklist
    CLASSIFY_MESSAGE      = "classify_message"      # FR-12 path (c) — message-intent fallback
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
    HilfdaLLMGatewayServer (the in-process implementation that hilda-llm-gateway
    runs on the egress side), MockLLM (tests)."""

    async def invoke(self, request: LLMRequest) -> LLMResponse: ...

    async def health(self) -> dict[str, Any]:
        """Returns {model, ready: bool, queue_depth: int}. Used by --diagnostic."""
```

### Task input/output schemas (`schemas.py`)

```python
# CLASSIFY_DOC_TYPE — FR-52 / FR-55 filename-opaque doc_type classification
class ClassifyDocTypeInput(BaseModel):
    first_page_excerpt:  str                              # bounded length per template
    candidate_doc_types: list[str]                        # closed enum subset of DocType per [D-045]
class ClassifyDocTypeOutput(BaseModel):
    doc_type:   Literal["test_report", "tech_report", "waiver"]
    confidence: float                                     # [0.0, 1.0]

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

# ROUTE_ATTACHMENT — [D-033] Tier-2
class RouteAttachmentInput(BaseModel):
    excerpt:           str
    candidate_items:   list[dict]  # [{item_id, item_name, item_description}]
class RouteAttachmentOutput(BaseModel):
    item_id:           str | None  # None when confidence below threshold
    confidence:        float

# REVIEW_DOCUMENT — FR-53
class ReviewDocumentInput(BaseModel):
    doc_excerpt: str
    doc_type:    str               # test_report | tech_report | waiver
    checklist:   list[dict]        # per-customer; generated by test_report_profiler
class ReviewDocumentOutput(BaseModel):
    findings:        list[dict]    # [{checklist_item_id, status, evidence_span}]
    overall_verdict: Literal["pass", "fail", "needs_review"]

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
    """One backend = one LLM serving endpoint. Two backends in Ph-1 per [D-052]:
    local Ollama on the HILDA PC GPU + corp on-prem LLM."""
    name:           Literal["ollama", "corp_llm"]
    endpoint_url:   str               # e.g. "http://localhost:11434" or corp LLM URL
    credential_key: str | None        # which credential_service key carries auth, if any
    # Rate-limit shape varies by backend:
    rate_limit_per_minute: int | None = None    # Ollama: None (VRAM-bound); corp LLM: N
    rate_limit_per_hour:   int | None = None
    rate_limit_per_day:    int | None = None

class LLMGatewayServer:
    """The egress-side implementation. Owns prompt-template loading from templates/,
    per-TaskKind backend + model selection per [D-052], output-schema validation,
    per-backend rate-limiting (token-bucket), retry policy, structured-output parsing,
    and credential handoff (retrieved once at startup from credential_service,
    system_type=LLM_GATEWAY, never logged). Mounts a single FastAPI route POST /invoke;
    rejects unknown TaskKind values."""

    def __init__(
        self,
        backends:           dict[str, BackendConfig],          # {"ollama": ..., "corp_llm": ...}
        task_backend_map:   dict[TaskKind, str],               # TaskKind → backend name
        task_model_map:     dict[TaskKind, str],               # TaskKind → model id within backend
        credential_service: CredentialService,
        template_dir:       Path = Path("/etc/hilda/llm-templates"),
    ) -> None:
        """Both task_backend_map and task_model_map are env-config; no code-level default
        precedence between backends. Each (TaskKind, backend, model) pairing is locked
        per [D-052] only after measured-quality A/B testing on representative fixtures."""

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
- **On-prem only — corporate network boundary.** Both backends (`ollama` local on HILDA PC, `corp_llm` on corp infrastructure) live inside the corporate network. No public DNS, no corp proxy-to-public-cloud, no SaaS endpoint. Validated at startup by `LLMGatewayServer` — backend endpoint URLs must resolve to allowed on-prem hosts per `[D-007]` + `[D-052]`.
- **No code-level backend precedence.** Per `[D-052]`, `task_backend_map` is env-config; no Python defaults assume "corp LLM is better for hard tasks" or vice versa. Each TaskKind's backend is set after empirical A/B testing on real fixtures; changing the pairing is an env-config change, not a code release.
- **No proprietary content in compact reports.** RPT/MET/FIX/QC records emit token counts, latency, task kind, model name, confidence buckets — never the prompt body, never the document excerpt, never the model response text. Anchors NFR-2 / `[D-002]`. The proprietary content flows through the LLM but does not surface in the chat-mediated diagnostics channel.
- **Structured output only.** Every task has a Pydantic output schema; non-conforming model responses are retried up to `max_retries` and then raise LLG-E003. The Protocol never returns free-form text to callers.
- **Async, non-blocking.** Per requirements.md NFR (Async LLM tasks): FR-52 Tier-2, FR-53, FR-12 path(c) execute as Celery background tasks; the state-change event (`DocumentReceived`, etc.) propagates within the polling-interval SLA regardless of LLM latency. `LLMProvider.invoke` is `async def`; long-running tasks live in `hilda-worker`, not `hilda-api`.
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
- **`[D-052]` Dual-backend with empirical routing** — Ph-1 has two on-prem LLM providers available: local Ollama (HILDA PC GPU; free per call; VRAM-bound concurrency; rate limit None) and corp on-prem LLM (corporate infrastructure; rate-limited per minute/hour/day; no per-call cost). **Neither backend gets default precedence.** `task_backend_map` and `task_model_map` are env-config; each TaskKind's `(backend, model)` pairing is locked only after measured-quality A/B testing on representative real fixtures. **No automatic spillover** between backends: if a TaskKind's assigned backend is rate-limited or down, the task queues — falling back silently to the other backend would risk delivering degraded-quality results without a quality gate. Spillover surfaces as `LLG-W006` for visibility.
- **`[D-029]` Ph-1 narrow surface** — 4 task kinds (CLASSIFY_DOC, ROUTE_ATTACHMENT, REVIEW_DOCUMENT, CLASSIFY_MESSAGE — DEF-1 promoted per impl note 2026-05-13). DRAFT_CUSTOMER_REPLY, SUMMARIZE_STATUS deferred. Adding a Ph-2 task = add TaskKind value + template + schema + backend mapping; no Protocol-surface change.
- **One Protocol, two implementations** — `OnPremLLMClient` (caller-side HTTP) + `LLMGatewayServer` (egress-side prompt+model orchestration + dual-backend routing) implement the same `LLMProvider` surface. Tests use either `MockLLM` (in-process) or stand up `LLMGatewayServer` with fake backend endpoints. No "client-vs-server" branching in caller code.
- **Structured output as a hard constraint** — alternative is free-form text + caller-side parsing, which scatters parsing logic and makes the contract LLM-dependent. JSON-mode + Pydantic validation localizes the brittleness and gives callers a typed surface.
- **Process-lifetime idempotency cache** — keys cleared on process restart; full Redis-backed persistence deferred to Ph-3+ (DEF-14 family). Acceptable for Ph-1/Ph-2 because Celery retries within the same worker process the majority of the time.
- **Prompt templates in code, customer content in inputs** — templates are versioned with HILDA releases (per `[D-045]` schema-vs-content boundary); customer-specific checklists flow through `LLMRequest.inputs`. Adding a new customer doesn't touch templates.

### Tentative model + backend assignments (pending A/B test)

Per `[D-052]`, every TaskKind's `(backend, model)` pairing must be A/B-tested on real fixtures before production lock-in. Current placeholder assignments — **all on local Ollama** until FR-53 A/B run completes:

| TaskKind | Tentative backend | Tentative model | A/B candidates to test |
|---|---|---|---|
| `REVIEW_DOCUMENT` (FR-53) | `ollama` | `gemma3:12b` | vs `corp_llm` / vs `qwen3:8b` — checklist-against-content quality |
| `CLASSIFY_DOC_TYPE` (FR-52 / FR-55) | `ollama` | `qwen3:8b-q4_k_m` | vs `corp_llm` — closed-enum 3-way classification |
| `ROUTE_ATTACHMENT` (FR-52 Tier 2) | `ollama` | `qwen3:8b-q4_k_m` | vs `corp_llm` |
| `CLASSIFY_DOC` (D-039 Step 2) | `ollama` | `qwen3:8b-q4_k_m` | vs `corp_llm` if rate budget allows |
| `CLASSIFY_MESSAGE` (FR-12 path c) | `ollama` | `qwen3:8b-q4_k_m` | vs `corp_llm` |

These placeholders live in env config, not code — production HILDA PC env vars override after A/B testing.

---

## Non-goals

- **Not a model hosting / serving stack.** Ollama / corp LLM serving infrastructure and GPU provisioning are out of scope for this module — handled at the deployment / infra / corp-platform layer. This module's only model-side surface is HTTP endpoint URLs in `BackendConfig`.
- **Not a test report parser.** FR-16 (test report → per-test-case `(test_case_id, status, [waiver_ref])` tuples) and FR-46 (`final | interim` classification) are **rule-based** per `[D-011]`, implemented by the per-customer parser generated by the Test Report Profiler. This module's `REVIEW_DOCUMENT` task is FR-53 — quality-review of a document against a per-customer checklist; it does **not** enumerate test cases, does not produce parser_result rows, and is not in the FR-16 / FR-46 critical path.
- **Not a filename-rule doc_type classifier.** When inbound document filenames match deterministic regex rules (e.g. `*test_report*.pdf` → `test_report`), `email_service` / `storage` resolve doc_type without calling this module. `CLASSIFY_DOC_TYPE` is invoked only on the opaque-filename fallback path per FR-52 / FR-55.
- **Not a build-time LLM module.** Code-generation LLM use by `api_spec_ingestor` / `template_schema_ingestor` / `test_report_profiler` shares the same `hilda-llm-gateway` egress per `[D-007]` impl note, but those tools' Protocol surfaces live in their own modules.
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

- `email_service` — FR-52 Tier-2 attachment routing (CLASSIFY_DOC + ROUTE_ATTACHMENT), FR-12 path (c) message classification.
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
RPT|LLG|run-00001|2026-05-28T10:00:00Z|gateway_reachable=true|backends_total=2|backends_reachable=2|ollama_models=gemma3:12b,qwen3:8b-q4_k_m|corp_llm_ready=true|templates_loaded=4
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
