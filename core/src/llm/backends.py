"""Backend client adapters — Ollama (/api/generate) + OpenAI-compatible (/v1/chat/completions).

The HTTP client is passed in (not created here) so the invoke pipeline is testable against
an httpx.MockTransport with no real model server. Each adapter returns the raw model text
(expected to be a JSON string) + token counts; JSON parsing + schema validation happen in
LLMGatewayServer.invoke.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from core.src.diagnostics.error_codes import PipelineError

__all__ = ["BackendResult", "call_backend"]


@dataclass(frozen=True)
class BackendResult:
    text: str          # raw model output (expected JSON string)
    tokens_in: int
    tokens_out: int


def _auth_headers(api_token: str | None) -> dict[str, str]:
    return {"Authorization": f"Bearer {api_token}"} if api_token else {}


async def _call_ollama(
    client: httpx.AsyncClient, endpoint_url: str, model: str, prompt: str,
    temperature: float | None, max_tokens: int | None, timeout_s: float | None, api_token: str | None,
) -> BackendResult:
    options: dict[str, Any] = {}
    if temperature is not None:
        options["temperature"] = temperature
    if max_tokens is not None:
        options["num_predict"] = max_tokens
    resp = await client.post(
        f"{endpoint_url.rstrip('/')}/api/generate",
        json={"model": model, "prompt": prompt, "stream": False, "format": "json", "options": options},
        headers=_auth_headers(api_token),
        timeout=timeout_s,
    )
    resp.raise_for_status()
    data = resp.json()
    return BackendResult(
        text=data.get("response", ""),
        tokens_in=int(data.get("prompt_eval_count", 0)),
        tokens_out=int(data.get("eval_count", 0)),
    )


async def _call_openai_compatible(
    client: httpx.AsyncClient, endpoint_url: str, model: str, prompt: str,
    temperature: float | None, max_tokens: int | None, timeout_s: float | None, api_token: str | None,
) -> BackendResult:
    body: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
    }
    if temperature is not None:
        body["temperature"] = temperature
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    # endpoint_url already includes the /v1 base for vllm_dgx (e.g. http://dgx-spark:8000/v1)
    resp = await client.post(
        f"{endpoint_url.rstrip('/')}/chat/completions",
        json=body,
        headers=_auth_headers(api_token),
        timeout=timeout_s,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    usage = data.get("usage", {})
    return BackendResult(
        text=text,
        tokens_in=int(usage.get("prompt_tokens", 0)),
        tokens_out=int(usage.get("completion_tokens", 0)),
    )


async def call_backend(
    client: httpx.AsyncClient, backend_name: str, endpoint_url: str, model: str, prompt: str,
    *, temperature: float | None = None, max_tokens: int | None = None,
    timeout_s: float | None = None, api_token: str | None = None,
) -> BackendResult:
    """Dispatch to the serving-stack adapter by backend name: `ollama_a4000` → Ollama API;
    `vllm_dgx` / `corp_llm` → OpenAI-compatible. Wraps transport errors as LLG-E001."""
    try:
        if backend_name == "ollama_a4000":
            return await _call_ollama(client, endpoint_url, model, prompt,
                                      temperature, max_tokens, timeout_s, api_token)
        return await _call_openai_compatible(client, endpoint_url, model, prompt,
                                             temperature, max_tokens, timeout_s, api_token)
    except httpx.HTTPError as exc:
        raise PipelineError(
            "LLG-E001",
            context={"task": "?", "backend": backend_name, "reason": str(exc)[:120]},
            cause=exc,
        )
