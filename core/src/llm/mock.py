"""MockLLM — in-memory deterministic LLMProvider for tests. See core/src/llm/MODULE.md.

Register fixed responses per (task, inputs-match); invoke() returns the matching
registration's output. Unregistered (task, inputs) → LLG-E001. No network, no gateway.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.src.diagnostics.error_codes import PipelineError
from core.src.llm.protocol import LLMRequest, LLMResponse, TaskKind

__all__ = ["MockLLM"]


@dataclass
class _Registration:
    task: TaskKind
    inputs_match: dict[str, Any]   # subset matched against request.inputs ({} = catch-all for task)
    output: dict[str, Any]
    model: str


def _matches(inputs_match: dict[str, Any], inputs: dict[str, Any]) -> bool:
    """A registration matches when every key/value in inputs_match equals the
    corresponding entry in request.inputs (subset semantics; {} matches anything)."""
    return all(inputs.get(k) == v for k, v in inputs_match.items())


def _est_tokens(value: Any) -> int:
    """Deterministic rough token estimate (~4 chars/token) for the audit fields."""
    return max(1, len(str(value)) // 4)


class MockLLM:
    """Full LLMProvider surface, deterministic. Used in unit + integration tests
    instead of standing up the gateway."""

    source_system: str = "mock_llm"

    def __init__(self) -> None:
        self._registrations: list[_Registration] = []

    def register(
        self, task: TaskKind, inputs_match: dict[str, Any], output: dict[str, Any],
        *, model: str = "mock",
    ) -> None:
        """Register a canned output for (task, inputs_match). Later registrations take
        precedence on overlap (most-recently-registered wins — convenient for test setup)."""
        self._registrations.insert(0, _Registration(task, dict(inputs_match), dict(output), model))

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        for reg in self._registrations:
            if reg.task == request.task and _matches(reg.inputs_match, request.inputs):
                return LLMResponse(
                    task=request.task,
                    output=dict(reg.output),
                    model=reg.model,
                    latency_ms=0,
                    tokens_in=_est_tokens(request.inputs),
                    tokens_out=_est_tokens(reg.output),
                )
        raise PipelineError(
            "LLG-E001",
            context={"task": request.task.value, "backend": "mock",
                     "reason": "no registered MockLLM response for these inputs"},
        )

    async def health(self) -> dict[str, Any]:
        return {"model": "mock", "ready": True, "queue_depth": 0}
