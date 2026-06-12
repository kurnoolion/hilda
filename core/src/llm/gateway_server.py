"""LLMGatewayServer — egress-side LLMProvider (runs inside hilda-llm-gateway only).

Increment 3 scope: BackendConfig + init/validation + conditional per-backend credential
retrieval (start()). The invoke() pipeline (template render → backend call → rate limit →
schema validation) lands in a later increment.

Credential model per [D-052] impl-note addendum 2026-06-12: up to one credential per
backend, retrieved CONDITIONALLY (only when credential_key is not None) — lab Ollama/vLLM
are auth-less; only corp_llm needs one. OnPremLLMClient (caller side) holds none.
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from jinja2 import Environment, FileSystemLoader
from pydantic import ValidationError

from core.src.credential_service import OPS_TEAM_PM_ID, Credential, CredentialService
from core.src.diagnostics import ReportRecord, ReportType
from core.src.diagnostics.error_codes import PipelineError, format_code
from core.src.llm.backends import call_backend
from core.src.llm.protocol import LLMRequest, LLMResponse, TaskKind
from core.src.llm.rate_limit import BackendRateLimiter
from core.src.llm.schemas import OUTPUT_SCHEMAS

__all__ = ["BackendConfig", "LLMGatewayServer"]

logger = logging.getLogger(__name__)

_DEFAULT_MAX_RETRIES = 2  # parse/validation retries before LLG-E003

_PACKAGED_TEMPLATES = Path(__file__).parent / "templates"
_INTERNAL_SUFFIXES = (".corp", ".lab", ".local", ".internal")


def _is_onprem_host(host: str, extra_suffixes: tuple[str, ...] = ()) -> bool:
    """On-prem ([D-007] corporate-network-boundary) host check. On-prem when: loopback,
    RFC-1918 private IP, a bare hostname (no dots → internal DNS, e.g. `a4000-box`), or an
    internal suffix (`.corp`/`.lab`/`.local`/`.internal` + any HILDA_LLM_ONPREM_SUFFIXES).
    Public-looking hosts (e.g. `api.openai.com`) are rejected — implements 'no public-cloud,
    no SaaS LLM'."""
    if not host:
        return False
    host = host.lower()
    if host == "localhost" or host.startswith(("127.", "10.", "192.168.")):
        return True
    if host.startswith("172."):
        parts = host.split(".")
        if len(parts) >= 2 and parts[1].isdigit() and 16 <= int(parts[1]) <= 31:
            return True
    if "." not in host:
        return True  # bare internal hostname
    return host.endswith(_INTERNAL_SUFFIXES + extra_suffixes)


@dataclass(frozen=True)
class BackendConfig:
    """One backend = one LLM serving endpoint. Three Ph-1 backends per [D-052]."""

    name: Literal["ollama_a4000", "vllm_dgx", "corp_llm"]
    endpoint_url: str
    credential_key: str | None = None        # SystemType value (e.g. "llm_corp_llm") or None (auth-less)
    rate_limit_per_minute: int | None = None
    rate_limit_per_hour: int | None = None
    rate_limit_per_day: int | None = None
    cold_load_expected: bool = False
    supports_batching: bool = False


class LLMGatewayServer:
    """Egress-side implementation. __init__ does synchronous config validation; `start()`
    does the async conditional credential retrieval (get_credential is async, mirroring the
    credential_service load() precedent). Call `await server.start()` after construction."""

    source_system: str = "llm_gateway"

    def __init__(
        self,
        backends: dict[str, BackendConfig],
        task_backend_map: dict[TaskKind, str],
        task_model_map: dict[TaskKind, str],
        credential_service: CredentialService,
        template_dir: Path | None = None,
    ) -> None:
        self._backends = dict(backends)
        self._task_backend_map = dict(task_backend_map)
        self._task_model_map = dict(task_model_map)
        self._credential_service = credential_service
        self._template_dir = Path(template_dir) if template_dir else _PACKAGED_TEMPLATES
        self._credentials: dict[str, Credential] = {}
        self._started = False
        self._http: httpx.AsyncClient | None = None
        self._idem_cache: dict[tuple[TaskKind, str], LLMResponse] = {}
        self._max_retries = _DEFAULT_MAX_RETRIES

        extra = tuple(s.strip() for s in os.environ.get("HILDA_LLM_ONPREM_SUFFIXES", "").split(",") if s.strip())

        # 1. On-prem endpoint validation per [D-007] / [D-052].
        for backend in self._backends.values():
            host = urlsplit(backend.endpoint_url).hostname or ""
            if not _is_onprem_host(host, extra):
                raise PipelineError("LLG-E004", context={"url": backend.endpoint_url})

        # 2. task_backend_map referential integrity + prompt-template existence.
        for task, backend_name in self._task_backend_map.items():
            if backend_name not in self._backends:
                raise PipelineError(
                    "LLG-E006",
                    context={"task": task.value, "backend": f"{backend_name} (not in backends)"},
                )
            if not (self._template_dir / f"{task.value}.j2").exists():
                raise PipelineError("LLG-E005", context={"task": task.value})

        self._env = Environment(loader=FileSystemLoader(str(self._template_dir)), autoescape=False)

        # 3. Per-backend rate limiters (no-op for backends with no rate_limit_* configured).
        self._limiters: dict[str, BackendRateLimiter] = {
            name: BackendRateLimiter(
                name, b.rate_limit_per_minute, b.rate_limit_per_hour, b.rate_limit_per_day)
            for name, b in self._backends.items()
        }

    async def start(self) -> int:
        """Conditional per-backend credential retrieval. Returns count retrieved. Idempotent.
        Only backends with `credential_key is not None` get a credential — lab Ollama/vLLM
        are auth-less, so the common lab config retrieves zero and still starts."""
        if self._started:
            return len(self._credentials)
        for name, backend in self._backends.items():
            if backend.credential_key is not None:
                self._credentials[name] = await self._credential_service.get_credential(
                    OPS_TEAM_PM_ID, backend.credential_key
                )
        self._started = True
        return len(self._credentials)

    def backend_for(self, task: TaskKind) -> BackendConfig:
        """Resolve the A/B-winner backend for a task; LLG-E006 when unmapped."""
        name = self._task_backend_map.get(task)
        if name is None:
            raise PipelineError("LLG-E006", context={"task": task.value, "backend": "<none>"})
        return self._backends[name]

    def model_for(self, task: TaskKind) -> str:
        """Resolve the model id for a task; LLG-E006 when unmapped."""
        model = self._task_model_map.get(task)
        if model is None:
            raise PipelineError("LLG-E006", context={"task": task.value, "backend": "<no model>"})
        return model

    def set_http_client(self, client: httpx.AsyncClient) -> None:
        """Inject the HTTP client (tests pass an httpx.AsyncClient over MockTransport)."""
        self._http = client

    def _get_http(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient()
        return self._http

    def _render(self, task: TaskKind, inputs: dict[str, Any]) -> str:
        return self._env.get_template(f"{task.value}.j2").render(**inputs)

    async def _acquire_rate_token(self, backend: BackendConfig, task: TaskKind) -> None:
        """Per-backend rate-limit acquire per [D-052] — no-op for unlimited lab backends;
        raises LLG-W006 (no automatic spillover) when a `corp_llm` window is exhausted, logs
        LLG-W005 when approaching. The check is in-memory (no IO), so kept sync under this seam."""
        self._limiters[backend.name].acquire(task.value)

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        """Pipeline: idempotency cache → resolve backend+model → rate-limit token → render
        prompt → call backend → parse + schema-validate (retry on fail, then LLG-E003) →
        emit MET → return LLMResponse."""
        if request.idempotency_key is not None:
            cached = self._idem_cache.get((request.task, request.idempotency_key))
            if cached is not None:
                return cached

        backend = self.backend_for(request.task)   # LLG-E006 if unmapped
        model = self.model_for(request.task)
        await self._acquire_rate_token(backend, request.task)   # may raise LLG-W006

        prompt = self._render(request.task, request.inputs)
        out_schema = OUTPUT_SCHEMAS[request.task]
        cred = self._credentials.get(backend.name)
        api_token = cred.api_token if cred is not None else None

        t0 = time.perf_counter()
        last_reason = "unknown"
        result = None
        validated = None
        for attempt in range(1, self._max_retries + 2):  # initial try + _max_retries retries
            result = await call_backend(
                self._get_http(), backend.name, backend.endpoint_url, model, prompt,
                temperature=request.temperature, max_tokens=request.max_tokens,
                timeout_s=request.timeout_s, api_token=api_token,
            )
            try:
                validated = out_schema.model_validate(json.loads(result.text))
                break
            except (json.JSONDecodeError, ValidationError) as exc:
                last_reason = str(exc)[:80]
                if attempt <= self._max_retries:
                    logger.warning("LLG-W003: " + format_code(
                        "LLG-W003", n=str(attempt), max=str(self._max_retries),
                        task=request.task.value, backend=backend.name))
        if validated is None:
            raise PipelineError(
                "LLG-E003",
                context={"n": str(self._max_retries), "task": request.task.value, "backend": backend.name},
            )

        latency_ms = int((time.perf_counter() - t0) * 1000)
        self._emit_met(request.task, backend.name, model, latency_ms, result, validated)

        response = LLMResponse(
            task=request.task, output=validated.model_dump(), model=model,
            latency_ms=latency_ms, tokens_in=result.tokens_in, tokens_out=result.tokens_out,
        )
        if request.idempotency_key is not None:
            self._idem_cache[(request.task, request.idempotency_key)] = response
        return response

    @staticmethod
    def confidence_bucket(confidence: float | None) -> str:
        if confidence is None:
            return "n/a"
        return "high" if confidence >= 0.85 else "medium" if confidence >= 0.6 else "low"

    def _emit_met(self, task, backend_name, model, latency_ms, result, validated) -> None:
        """Build the per-invoke MET line (counts/latency/model/confidence bucket only — never
        prompt or output text per NFR-2) and log it as structured observability (not stdout
        spam). Returns nothing; the bounded record is the ops-collectable artifact."""
        record = ReportRecord(
            ReportType.MET, "LLG", "run-invoke", datetime.now(timezone.utc),
            {"task": task.value, "backend": backend_name, "model": model,
             "latency_ms": latency_ms, "tokens_in": result.tokens_in,
             "tokens_out": result.tokens_out,
             "confidence_bucket": self.confidence_bucket(getattr(validated, "confidence", None))},
        )
        logger.info(record.to_line())

    async def health(self) -> dict[str, Any]:
        return {
            "model": "gateway",
            "ready": self._started,
            "queue_depth": 0,
            "backends": sorted(self._backends),
            "credentials_loaded": len(self._credentials),
        }
