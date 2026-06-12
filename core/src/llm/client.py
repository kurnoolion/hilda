"""OnPremLLMClient — caller-side thin HTTP client for hilda-api / hilda-worker.

Proxies LLMRequest to hilda-llm-gateway's POST /invoke. NO credential param: the
client→gateway hop is an intra-Compose, on-HILDA-PC call inside the Ph-1 trust domain;
the gateway authorizes nothing on caller identity (routes on TaskKind). Backend creds live
server-side only. Per [D-052] impl-note addendum 2026-06-12. The [D-007] no-short-circuit
invariant is enforced by callers never holding backend URLs — only the gateway URL.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx

from core.src.diagnostics.error_codes import PipelineError
from core.src.llm.protocol import LLMRequest, LLMResponse, TaskKind

__all__ = ["OnPremLLMClient"]


class OnPremLLMClient:
    source_system: str = "on_prem_llm"   # immutable

    def __init__(
        self,
        gateway_url: str,                  # e.g. "http://hilda-llm-gateway:9100"
        max_retries: int = 3,
        retry_backoff_s: float = 1.0,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self._gateway_url = gateway_url.rstrip("/")
        self._max_retries = max_retries
        self._retry_backoff_s = retry_backoff_s
        self._http = http_client

    def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(base_url=self._gateway_url)
        return self._http

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        body = {
            "task": request.task.value,
            "inputs": request.inputs,
            "timeout_s": request.timeout_s,
            "max_tokens": request.max_tokens,
            "temperature": request.temperature,
            "idempotency_key": request.idempotency_key,
        }
        last_exc: Exception | None = None
        for attempt in range(1, self._max_retries + 1):
            try:
                resp = await self._client().post(f"{self._gateway_url}/invoke", json=body)
            except httpx.TransportError as exc:
                # Transport-level failure (gateway unreachable) — retry with backoff.
                last_exc = exc
                if attempt < self._max_retries:
                    await asyncio.sleep(self._retry_backoff_s * attempt)
                    continue
                raise PipelineError(
                    "LLG-E001",
                    context={"task": request.task.value, "backend": "gateway",
                             "reason": str(exc)[:120]},
                    cause=exc,
                )
            if resp.status_code == 200:
                data = resp.json()
                return LLMResponse(
                    task=TaskKind(data["task"]),
                    output=data["output"],
                    model=data["model"],
                    latency_ms=data["latency_ms"],
                    tokens_in=data["tokens_in"],
                    tokens_out=data["tokens_out"],
                )
            # Structured LLG error from the gateway — deterministic, no retry.
            err = resp.json()
            raise PipelineError(err["error_code"], context=err.get("context") or {})
        # Unreachable (loop either returns or raises), but keep the type-checker happy.
        raise PipelineError("LLG-E001", context={"task": request.task.value, "backend": "gateway",
                                                 "reason": str(last_exc)})

    async def health(self) -> dict[str, Any]:
        resp = await self._client().get(f"{self._gateway_url}/health")
        resp.raise_for_status()
        return resp.json()
