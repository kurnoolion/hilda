"""Thin FastAPI surface for hilda-llm-gateway — a single POST /invoke + GET /health.

Deserializes the wire request → LLMGatewayServer.invoke() → serializes LLMResponse.
PipelineError (LLG-*) is mapped to a JSON error body the OnPremLLMClient reconstructs.
This is the only HTTP server in the module; callers reach it via OnPremLLMClient.
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from core.src.diagnostics.error_codes import PipelineError
from core.src.llm.gateway_server import LLMGatewayServer
from core.src.llm.protocol import LLMRequest, TaskKind

__all__ = ["make_app"]

# LLG code → HTTP status for the client↔gateway hop.
_STATUS = {
    "LLG-W006": 429,   # rate-limited; caller defers
    "LLG-E002": 400,   # unknown TaskKind
    "LLG-E006": 400,   # no backend mapping
}
_DEFAULT_ERROR_STATUS = 502   # upstream model / validation failure (E001 / E003)


def _serialize(resp) -> dict[str, Any]:
    return {
        "task": resp.task.value,
        "output": resp.output,
        "model": resp.model,
        "latency_ms": resp.latency_ms,
        "tokens_in": resp.tokens_in,
        "tokens_out": resp.tokens_out,
    }


def make_app(gateway: LLMGatewayServer) -> FastAPI:
    app = FastAPI(title="hilda-llm-gateway")

    @app.post("/invoke")
    async def invoke(payload: dict) -> Any:
        try:
            task = TaskKind(payload["task"])
        except (ValueError, KeyError):
            return JSONResponse(
                status_code=400,
                content={"error_code": "LLG-E002", "context": {"task": str(payload.get("task"))}},
            )
        request = LLMRequest(
            task=task,
            inputs=payload.get("inputs", {}),
            timeout_s=payload.get("timeout_s"),
            max_tokens=payload.get("max_tokens"),
            temperature=payload.get("temperature"),
            idempotency_key=payload.get("idempotency_key"),
        )
        try:
            resp = await gateway.invoke(request)
        except PipelineError as exc:
            return JSONResponse(
                status_code=_STATUS.get(exc.code_id, _DEFAULT_ERROR_STATUS),
                content={"error_code": exc.code_id, "context": exc.context},
            )
        return _serialize(resp)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return await gateway.health()

    return app
