"""llm CLI: --diagnostic / --mock / --invoke / --contract per [D-005].

Emits LLG-RPT / LLG-MET / LLG-QC compact reports — token counts, latency, model, confidence
bucket only; never prompt body or model response text (NFR-2).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone

import httpx

from core.src.credential_service import MockCredentialService
from core.src.diagnostics import ReportRecord, ReportType, ReportWriter
from core.src.diagnostics.error_codes import PipelineError
from core.src.llm.client import OnPremLLMClient
from core.src.llm.gateway_server import BackendConfig, LLMGatewayServer
from core.src.llm.protocol import LLMRequest, TaskKind
from core.src.llm.schemas import OUTPUT_SCHEMAS

# Canned schema-valid outputs + synthetic inputs per TaskKind for --contract / --mock.
_CANNED: dict[TaskKind, tuple[dict, dict]] = {
    TaskKind.CLASSIFY_DOC_TYPE: (
        {"first_page_excerpt": "test case results", "candidate_doc_types": ["test_report", "tech_report", "waiver"]},
        {"doc_type": "test_report", "confidence": 0.9}),
    TaskKind.CLASSIFY_DOC: (
        {"new_doc_first_page_excerpt": "x", "existing_candidates": []},
        {"verdict": "NEW_DOCUMENT", "revision_of": None, "confidence": 0.9}),
    TaskKind.ROUTE_ATTACHMENT: (
        {"excerpt": "x", "candidate_items": [{"item_id": "i1", "item_name": "n", "item_description": "d"}]},
        {"matches": [{"item_id": "i1", "confidence": 0.9}]}),
    TaskKind.REVIEW_DOCUMENT: (
        {"doc_excerpt": "x", "doc_type": "test_report", "checklist": [{"id": "c1", "description": "d", "severity": "high"}]},
        {"findings": [], "overall_verdict": "pass"}),
    TaskKind.CLASSIFY_MESSAGE: (
        {"body": "all done", "candidate_intents": ["owner_done", "delay"]},
        {"intent": "owner_done", "confidence": 0.9}),
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ollama_transport(canned_output: dict) -> httpx.AsyncClient:
    """Fake Ollama transport returning a fixed schema-valid response."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"response": json.dumps(canned_output),
                                         "prompt_eval_count": 10, "eval_count": 4})
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _single_task_gateway(task: TaskKind, canned_output: dict) -> LLMGatewayServer:
    gw = LLMGatewayServer(
        backends={"ollama_a4000": BackendConfig(name="ollama_a4000", endpoint_url="http://a4000-box:11434")},
        task_backend_map={task: "ollama_a4000"},
        task_model_map={task: "qwen3:8b-q4_k_m"},
        credential_service=MockCredentialService(),
    )
    gw.set_http_client(_ollama_transport(canned_output))
    return gw


async def _cmd_diagnostic(run_id: str, gateway_url: str) -> int:
    """Probe the configured gateway's /health. Honest about unreachable on a dev box."""
    writer = ReportWriter("LLG", run_id)
    client = OnPremLLMClient(gateway_url)
    reachable = False
    info: dict = {}
    try:
        info = await client.health()
        reachable = True
    except Exception:
        pass
    writer.emit(ReportRecord(ReportType.RPT, "LLG", run_id, _now(), {
        "gateway_reachable": reachable,
        "backends_total": len(info.get("backends", [])),
        "credentials_loaded": int(info.get("credentials_loaded", 0)),
        "ready": bool(info.get("ready", False)),
    }))
    writer.flush()
    return 0 if reachable else 1


async def _cmd_mock(run_id: str) -> int:
    """Run every TaskKind through the gateway-over-fake-transport; LLG-RPT ops summary."""
    writer = ReportWriter("LLG", run_id)
    ok = 0
    for task, (inputs, output) in _CANNED.items():
        gw = _single_task_gateway(task, output)
        try:
            resp = await gw.invoke(LLMRequest(task=task, inputs=inputs))
            if resp.output == output:
                ok += 1
        except PipelineError:
            pass
    writer.emit(ReportRecord(ReportType.RPT, "LLG", run_id, _now(), {
        "mode": "mock", "tasks": len(_CANNED), "ok": ok, "fail": len(_CANNED) - ok,
    }))
    writer.flush()
    return 0 if ok == len(_CANNED) else 1


async def _cmd_contract(run_id: str) -> int:
    """Structured-output contract suite — synthetic inputs → invoke → output validates
    against each TaskKind's schema. Emits per-task LLG-QC + summary LLG-RPT."""
    writer = ReportWriter("LLG", run_id)
    passed = 0
    for task, (inputs, output) in _CANNED.items():
        gw = _single_task_gateway(task, output)
        schema_valid = False
        bucket = "n/a"
        latency = 0
        try:
            resp = await gw.invoke(LLMRequest(task=task, inputs=inputs))
            OUTPUT_SCHEMAS[task].model_validate(resp.output)   # re-validate the returned output
            schema_valid = True
            latency = resp.latency_ms
            bucket = LLMGatewayServer.confidence_bucket(resp.output.get("confidence"))
            passed += 1
        except (PipelineError, Exception):
            pass
        writer.emit(ReportRecord(ReportType.QC, "LLG", run_id, _now(), {
            "task": task.value, "schema_valid": schema_valid, "latency_ms": latency,
            "confidence_bucket": bucket, "result": "OK" if schema_valid else "FAIL",
        }))
    writer.emit(ReportRecord(ReportType.RPT, "LLG", run_id, _now(), {
        "mode": "contract", "tasks": len(_CANNED), "passed": passed, "failed": len(_CANNED) - passed,
    }))
    writer.flush()
    return 0 if passed == len(_CANNED) else 1


async def _cmd_invoke(run_id: str, gateway_url: str, task_name: str, input_file: str) -> int:
    """One real LLM call against the gateway from a fixture file. Emits LLG-MET (no input text)."""
    writer = ReportWriter("LLG", run_id)
    task = TaskKind(task_name)
    with open(input_file, encoding="utf-8") as f:
        inputs = json.load(f)
    client = OnPremLLMClient(gateway_url)
    try:
        resp = await client.invoke(LLMRequest(task=task, inputs=inputs))
    except PipelineError as exc:
        writer.emit(ReportRecord(ReportType.RPT, "LLG", run_id, _now(),
                                 {"task": task.value, "ok": False, "error_code": exc.code_id}))
        writer.flush()
        return 1
    writer.emit(ReportRecord(ReportType.MET, "LLG", run_id, _now(), {
        "task": task.value, "model": resp.model, "latency_ms": resp.latency_ms,
        "tokens_in": resp.tokens_in, "tokens_out": resp.tokens_out,
        "confidence_bucket": LLMGatewayServer.confidence_bucket(resp.output.get("confidence")),
    }))
    writer.flush()
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="llm CLI — diagnostic / mock / invoke / contract")
    parser.add_argument("--diagnostic", action="store_true", help="Probe gateway /health; LLG-RPT")
    parser.add_argument("--mock", action="store_true", help="Run every TaskKind via fake transport")
    parser.add_argument("--contract", action="store_true", help="Structured-output contract suite")
    parser.add_argument("--invoke", action="store_true", help="One real call from --input-file")
    parser.add_argument("--task", default=None, help="TaskKind value for --invoke")
    parser.add_argument("--input-file", default=None, help="JSON fixture for --invoke")
    parser.add_argument("--gateway-url", default=os.environ.get("HILDA_LLM_GATEWAY_URL", "http://hilda-llm-gateway:9100"))
    parser.add_argument("--run-id", default=None)
    args = parser.parse_args()
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:8]}"

    if args.diagnostic:
        code = asyncio.run(_cmd_diagnostic(run_id, args.gateway_url))
    elif args.mock:
        code = asyncio.run(_cmd_mock(run_id))
    elif args.contract:
        code = asyncio.run(_cmd_contract(run_id))
    elif args.invoke:
        if not (args.task and args.input_file):
            parser.error("--invoke requires --task and --input-file")
        code = asyncio.run(_cmd_invoke(run_id, args.gateway_url, args.task, args.input_file))
    else:
        parser.print_help()
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
