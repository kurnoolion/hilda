"""llm tests — increment 1: pure-types foundation (protocol, schemas, codes, QC)."""
from __future__ import annotations

import asyncio

import pytest

from core.src.diagnostics import QC_REGISTRY, ReportRecord, ReportType
from core.src.diagnostics.error_codes import ERROR_CODES, PipelineError
from core.src.credential_service import Credential, MockCredentialService, OPS_TEAM_PM_ID
from core.src.llm import (
    INPUT_SCHEMAS,
    OUTPUT_SCHEMAS,
    BackendConfig,
    ClassifyDocTypeOutput,
    LLMGatewayServer,
    LLMRequest,
    LLMResponse,
    MockLLM,
    RouteAttachmentMatch,
    RouteAttachmentOutput,
    TaskKind,
)
from core.src.llm.qc_templates import TASK_CONTRACT


def _lab_backend(name="ollama_a4000", url="http://a4000-box:11434", cred=None):
    return BackendConfig(name=name, endpoint_url=url, credential_key=cred)


def _gateway(backends, task_backend_map, task_model_map=None, cred_service=None):
    tmm = task_model_map or {t: "qwen3:8b-q4_k_m" for t in task_backend_map}
    return LLMGatewayServer(
        backends=backends, task_backend_map=task_backend_map, task_model_map=tmm,
        credential_service=cred_service or MockCredentialService(),
    )


class TestProtocol:
    def test_five_ph1_taskkinds(self):
        assert len(TaskKind) == 5
        assert {t.value for t in TaskKind} == {
            "route_attachment", "classify_doc", "classify_doc_type",
            "review_document", "classify_message",
        }

    def test_request_response_frozen(self):
        req = LLMRequest(task=TaskKind.CLASSIFY_MESSAGE, inputs={"body": "x"})
        import pytest
        with pytest.raises(Exception):
            req.task = TaskKind.REVIEW_DOCUMENT  # type: ignore[misc]
        resp = LLMResponse(task=req.task, output={"intent": "done"}, model="m",
                           latency_ms=10, tokens_in=5, tokens_out=2)
        assert resp.tokens_in == 5


class TestSchemas:
    def test_every_taskkind_has_input_and_output_schema(self):
        for t in TaskKind:
            assert t in INPUT_SCHEMAS, t
            assert t in OUTPUT_SCHEMAS, t

    def test_route_attachment_output_roundtrip(self):
        out = RouteAttachmentOutput(matches=[RouteAttachmentMatch(item_id="i1", confidence=0.9)])
        assert RouteAttachmentOutput.model_validate(out.model_dump()).matches[0].item_id == "i1"

    def test_route_attachment_empty_is_valid(self):
        # empty list → caller falls through to FR-52 step 5
        assert RouteAttachmentOutput(matches=[]).matches == []

    def test_classify_doc_type_restricted_to_three(self):
        ClassifyDocTypeOutput(doc_type="test_report", confidence=0.9)  # ok
        import pytest
        from pydantic import ValidationError
        with pytest.raises(ValidationError):
            ClassifyDocTypeOutput(doc_type="compliance_certification_release_notes", confidence=0.9)
        with pytest.raises(ValidationError):
            ClassifyDocTypeOutput(doc_type="unresolved", confidence=0.5)


class TestMockLLM:
    def test_register_and_invoke_subset_match(self):
        mock = MockLLM()
        mock.register(TaskKind.CLASSIFY_DOC_TYPE, {"first_page_excerpt": "battery"},
                      {"doc_type": "test_report", "confidence": 0.92}, model="gemma3:12b")
        req = LLMRequest(task=TaskKind.CLASSIFY_DOC_TYPE,
                         inputs={"first_page_excerpt": "battery", "candidate_doc_types": ["test_report"]})
        resp = asyncio.run(mock.invoke(req))
        assert resp.output["doc_type"] == "test_report"
        assert resp.model == "gemma3:12b"
        assert resp.task == TaskKind.CLASSIFY_DOC_TYPE
        assert resp.tokens_in > 0 and resp.tokens_out > 0

    def test_unregistered_raises_llg_e001(self):
        mock = MockLLM()
        with pytest.raises(PipelineError) as exc:
            asyncio.run(mock.invoke(LLMRequest(task=TaskKind.CLASSIFY_MESSAGE, inputs={"body": "x"})))
        assert exc.value.code_id == "LLG-E001"

    def test_task_must_match_too(self):
        mock = MockLLM()
        mock.register(TaskKind.CLASSIFY_MESSAGE, {}, {"intent": "done", "confidence": 0.9})
        # catch-all for CLASSIFY_MESSAGE, but a different task must still miss
        with pytest.raises(PipelineError) as exc:
            asyncio.run(mock.invoke(LLMRequest(task=TaskKind.REVIEW_DOCUMENT, inputs={"body": "x"})))
        assert exc.value.code_id == "LLG-E001"

    def test_latest_registration_wins(self):
        mock = MockLLM()
        mock.register(TaskKind.CLASSIFY_MESSAGE, {}, {"intent": "old", "confidence": 0.5})
        mock.register(TaskKind.CLASSIFY_MESSAGE, {}, {"intent": "new", "confidence": 0.9})
        resp = asyncio.run(mock.invoke(LLMRequest(task=TaskKind.CLASSIFY_MESSAGE, inputs={"body": "x"})))
        assert resp.output["intent"] == "new"

    def test_implements_protocol_and_health(self):
        from core.src.llm import LLMProvider
        mock = MockLLM()
        assert isinstance(mock, LLMProvider)  # runtime_checkable Protocol
        h = asyncio.run(mock.health())
        assert h["ready"] is True and h["queue_depth"] == 0


class TestGatewayInit:
    def test_lab_only_config_starts_with_zero_credentials(self):
        # ollama_a4000 + vllm_dgx, both auth-less → start() retrieves nothing
        backends = {
            "ollama_a4000": _lab_backend("ollama_a4000", "http://a4000-box:11434"),
            "vllm_dgx": _lab_backend("vllm_dgx", "http://dgx-spark:8000/v1"),
        }
        gw = _gateway(backends, {TaskKind.CLASSIFY_DOC_TYPE: "ollama_a4000"})
        assert asyncio.run(gw.start()) == 0
        assert gw.backend_for(TaskKind.CLASSIFY_DOC_TYPE).name == "ollama_a4000"

    def test_corp_backend_retrieves_exactly_one_credential(self):
        mock_cs = MockCredentialService()
        mock_cs.register(Credential(pm_id=OPS_TEAM_PM_ID, system_type="llm_corp_llm",
                                    auth_type="api_token", api_token="x"))
        backends = {
            "ollama_a4000": _lab_backend("ollama_a4000", "http://a4000-box:11434"),
            "corp_llm": _lab_backend("corp_llm", "http://corp-llm.corp:8080", cred="llm_corp_llm"),
        }
        gw = _gateway(backends, {TaskKind.REVIEW_DOCUMENT: "corp_llm"}, cred_service=mock_cs)
        # only corp_llm has credential_key → exactly one retrieved
        assert asyncio.run(gw.start()) == 1

    def test_start_is_idempotent(self):
        gw = _gateway({"ollama_a4000": _lab_backend()}, {TaskKind.CLASSIFY_MESSAGE: "ollama_a4000"})

        async def run():
            return await gw.start(), await gw.start()

        assert asyncio.run(run()) == (0, 0)

    def test_public_endpoint_rejected_llg_e004(self):
        with pytest.raises(PipelineError) as exc:
            _gateway({"corp_llm": _lab_backend("corp_llm", "https://api.openai.com/v1", cred="llm_corp_llm")},
                     {TaskKind.REVIEW_DOCUMENT: "corp_llm"})
        assert exc.value.code_id == "LLG-E004"

    def test_bare_hostname_and_private_ip_accepted(self):
        for url in ("http://a4000-box:11434", "http://10.1.2.3:11434", "http://dgx.lab:8000"):
            gw = _gateway({"ollama_a4000": _lab_backend("ollama_a4000", url)},
                          {TaskKind.CLASSIFY_DOC: "ollama_a4000"})
            assert gw is not None

    def test_unknown_backend_in_map_rejected_llg_e006(self):
        with pytest.raises(PipelineError) as exc:
            _gateway({"ollama_a4000": _lab_backend()}, {TaskKind.CLASSIFY_DOC: "nonexistent"})
        assert exc.value.code_id == "LLG-E006"

    def test_missing_template_rejected_llg_e005(self, tmp_path):
        # point template_dir at an empty dir → mapped task's .j2 missing
        with pytest.raises(PipelineError) as exc:
            LLMGatewayServer(
                backends={"ollama_a4000": _lab_backend()},
                task_backend_map={TaskKind.CLASSIFY_DOC: "ollama_a4000"},
                task_model_map={TaskKind.CLASSIFY_DOC: "m"},
                credential_service=MockCredentialService(),
                template_dir=tmp_path,
            )
        assert exc.value.code_id == "LLG-E005"

    def test_backend_for_unmapped_task_llg_e006(self):
        gw = _gateway({"ollama_a4000": _lab_backend()}, {TaskKind.CLASSIFY_DOC: "ollama_a4000"})
        with pytest.raises(PipelineError) as exc:
            gw.backend_for(TaskKind.REVIEW_DOCUMENT)
        assert exc.value.code_id == "LLG-E006"

    def test_onprem_suffix_env_override(self, monkeypatch):
        monkeypatch.setenv("HILDA_LLM_ONPREM_SUFFIXES", ".acme-internal.net")
        gw = _gateway({"corp_llm": _lab_backend("corp_llm", "http://llm.acme-internal.net:8080")},
                      {TaskKind.REVIEW_DOCUMENT: "corp_llm"})
        assert gw is not None


class TestGatewayInvoke:
    @staticmethod
    def _ollama_client(response_obj, *, status=200, tin=12, tout=4, counter=None):
        import json as _json

        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            if counter is not None:
                counter.append(1)
            if status != 200:
                return httpx.Response(status, json={"error": "boom"})
            body = response_obj if isinstance(response_obj, str) else _json.dumps(response_obj)
            return httpx.Response(200, json={"response": body, "prompt_eval_count": tin, "eval_count": tout})

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    @staticmethod
    def _openai_client(content_obj):
        import json as _json

        import httpx

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "choices": [{"message": {"content": _json.dumps(content_obj)}}],
                "usage": {"prompt_tokens": 20, "completion_tokens": 6},
            })

        return httpx.AsyncClient(transport=httpx.MockTransport(handler))

    def _ollama_gateway(self):
        return _gateway({"ollama_a4000": _lab_backend("ollama_a4000", "http://a4000-box:11434")},
                        {TaskKind.CLASSIFY_DOC_TYPE: "ollama_a4000"},
                        {TaskKind.CLASSIFY_DOC_TYPE: "qwen3:8b-q4_k_m"})

    def test_invoke_happy_path_ollama(self):
        gw = self._ollama_gateway()
        gw.set_http_client(self._ollama_client({"doc_type": "test_report", "confidence": 0.92}))
        req = LLMRequest(task=TaskKind.CLASSIFY_DOC_TYPE,
                         inputs={"first_page_excerpt": "battery cell capacity test",
                                 "candidate_doc_types": ["test_report", "tech_report", "waiver"]})
        resp = asyncio.run(gw.invoke(req))
        assert resp.output == {"doc_type": "test_report", "confidence": 0.92}
        assert resp.model == "qwen3:8b-q4_k_m"
        assert (resp.tokens_in, resp.tokens_out) == (12, 4)
        assert resp.latency_ms >= 0

    def test_invoke_openai_compatible_vllm(self):
        gw = _gateway({"vllm_dgx": _lab_backend("vllm_dgx", "http://dgx-spark:8000/v1")},
                      {TaskKind.CLASSIFY_MESSAGE: "vllm_dgx"},
                      {TaskKind.CLASSIFY_MESSAGE: "gemma3:12b"})
        gw.set_http_client(self._openai_client({"intent": "owner_done", "confidence": 0.8}))
        resp = asyncio.run(gw.invoke(LLMRequest(task=TaskKind.CLASSIFY_MESSAGE,
                                                inputs={"body": "all closed", "candidate_intents": ["owner_done"]})))
        assert resp.output["intent"] == "owner_done"
        assert resp.tokens_in == 20

    def test_invalid_output_retries_then_llg_e003(self):
        gw = self._ollama_gateway()
        calls: list[int] = []
        gw.set_http_client(self._ollama_client("not json at all", counter=calls))
        req = LLMRequest(task=TaskKind.CLASSIFY_DOC_TYPE,
                         inputs={"first_page_excerpt": "x", "candidate_doc_types": ["test_report"]})
        with pytest.raises(PipelineError) as exc:
            asyncio.run(gw.invoke(req))
        assert exc.value.code_id == "LLG-E003"
        assert len(calls) == 3  # initial + 2 retries (_DEFAULT_MAX_RETRIES)

    def test_schema_violation_retries(self):
        gw = self._ollama_gateway()
        # valid JSON but doc_type outside the restricted set → ValidationError → retry → E003
        gw.set_http_client(self._ollama_client({"doc_type": "unresolved", "confidence": 0.4}))
        req = LLMRequest(task=TaskKind.CLASSIFY_DOC_TYPE,
                         inputs={"first_page_excerpt": "x", "candidate_doc_types": ["test_report"]})
        with pytest.raises(PipelineError) as exc:
            asyncio.run(gw.invoke(req))
        assert exc.value.code_id == "LLG-E003"

    def test_backend_http_error_llg_e001(self):
        gw = self._ollama_gateway()
        gw.set_http_client(self._ollama_client({}, status=500))
        req = LLMRequest(task=TaskKind.CLASSIFY_DOC_TYPE,
                         inputs={"first_page_excerpt": "x", "candidate_doc_types": ["test_report"]})
        with pytest.raises(PipelineError) as exc:
            asyncio.run(gw.invoke(req))
        assert exc.value.code_id == "LLG-E001"

    def test_idempotency_key_caches(self):
        gw = self._ollama_gateway()
        calls: list[int] = []
        gw.set_http_client(self._ollama_client({"doc_type": "waiver", "confidence": 0.9}, counter=calls))
        req = LLMRequest(task=TaskKind.CLASSIFY_DOC_TYPE,
                         inputs={"first_page_excerpt": "x", "candidate_doc_types": ["waiver"]},
                         idempotency_key="k-1")

        async def run():
            r1 = await gw.invoke(req)
            r2 = await gw.invoke(req)
            return r1, r2

        r1, r2 = asyncio.run(run())
        assert r1.output == r2.output
        assert len(calls) == 1  # second call served from cache, no backend hit

    def test_confidence_bucket(self):
        assert LLMGatewayServer.confidence_bucket(0.9) == "high"
        assert LLMGatewayServer.confidence_bucket(0.7) == "medium"
        assert LLMGatewayServer.confidence_bucket(0.3) == "low"
        assert LLMGatewayServer.confidence_bucket(None) == "n/a"


class TestRateLimiter:
    def test_unlimited_backend_is_noop(self):
        from core.src.llm.rate_limit import BackendRateLimiter
        lim = BackendRateLimiter("ollama_a4000")  # no limits
        for _ in range(1000):
            lim.acquire("classify_doc")  # never raises

    def test_exhaustion_raises_w006_no_spillover(self):
        from core.src.llm.rate_limit import BackendRateLimiter
        lim = BackendRateLimiter("corp_llm", per_minute=2)
        lim.acquire("review_document")
        lim.acquire("review_document")
        with pytest.raises(PipelineError) as exc:
            lim.acquire("review_document")
        assert exc.value.code_id == "LLG-W006"

    def test_window_resets_with_clock(self):
        from core.src.llm.rate_limit import BackendRateLimiter
        now = [1000.0]
        lim = BackendRateLimiter("corp_llm", per_minute=1, clock=lambda: now[0])
        lim.acquire("review_document")
        with pytest.raises(PipelineError):
            lim.acquire("review_document")        # window full
        now[0] += 61.0                            # advance past the minute window
        lim.acquire("review_document")            # resets → allowed again

    def test_approaching_logs_w005(self, caplog):
        import logging

        from core.src.llm.rate_limit import BackendRateLimiter
        lim = BackendRateLimiter("corp_llm", per_minute=5)  # threshold = ceil(5*0.2)=1
        with caplog.at_level(logging.WARNING):
            for _ in range(4):                    # 4th leaves remaining=1 ≤ threshold → W005
                lim.acquire("classify_doc")
        assert any("LLG-W005" in r.message for r in caplog.records)


class TestGatewayRateLimit:
    def test_gateway_defers_on_corp_exhaustion(self):
        # corp backend, per_minute=1: first invoke ok, second → LLG-W006 (no spillover)
        mock_cs = MockCredentialService()
        mock_cs.register(Credential(pm_id=OPS_TEAM_PM_ID, system_type="llm_corp_llm",
                                    auth_type="api_token", api_token="x"))
        backends = {"corp_llm": BackendConfig(
            name="corp_llm", endpoint_url="http://corp-llm.corp:8080",
            credential_key="llm_corp_llm", rate_limit_per_minute=1)}
        gw = _gateway(backends, {TaskKind.CLASSIFY_MESSAGE: "corp_llm"},
                      {TaskKind.CLASSIFY_MESSAGE: "gemma3:12b"}, cred_service=mock_cs)
        gw.set_http_client(TestGatewayInvoke._openai_client({"intent": "x", "confidence": 0.9}))
        asyncio.run(gw.start())
        req = LLMRequest(task=TaskKind.CLASSIFY_MESSAGE,
                         inputs={"body": "hi", "candidate_intents": ["x"]})
        asyncio.run(gw.invoke(req))               # 1st ok
        with pytest.raises(PipelineError) as exc:
            asyncio.run(gw.invoke(req))           # 2nd exhausted
        assert exc.value.code_id == "LLG-W006"


class TestClientGatewayRoundTrip:
    """OnPremLLMClient → (ASGITransport) → FastAPI /invoke → gateway → (MockTransport) →
    fake Ollama. Full client↔gateway round-trip in-process, no running server."""

    @staticmethod
    def _wired_client(gateway):
        import httpx

        from core.src.llm import OnPremLLMClient
        from core.src.llm.app import make_app
        app = make_app(gateway)
        http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw")
        return OnPremLLMClient("http://gw", http_client=http)

    def test_invoke_round_trip(self):
        gw = TestGatewayInvoke()._ollama_gateway()
        gw.set_http_client(TestGatewayInvoke._ollama_client({"doc_type": "test_report", "confidence": 0.9}))
        client = self._wired_client(gw)
        resp = asyncio.run(client.invoke(LLMRequest(
            task=TaskKind.CLASSIFY_DOC_TYPE,
            inputs={"first_page_excerpt": "x", "candidate_doc_types": ["test_report"]})))
        assert resp.output["doc_type"] == "test_report"
        assert resp.model == "qwen3:8b-q4_k_m"

    def test_unknown_task_returns_llg_e002(self):
        # craft a raw payload with a bogus task via the gateway app directly
        import httpx

        from core.src.llm.app import make_app
        gw = TestGatewayInvoke()._ollama_gateway()
        app = make_app(gw)

        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://gw") as c:
                return await c.post("/invoke", json={"task": "not_a_task", "inputs": {}})

        resp = asyncio.run(run())
        assert resp.status_code == 400
        assert resp.json()["error_code"] == "LLG-E002"

    def test_gateway_error_propagates_to_client(self):
        gw = TestGatewayInvoke()._ollama_gateway()
        gw.set_http_client(TestGatewayInvoke._ollama_client({}, status=500))
        client = self._wired_client(gw)
        with pytest.raises(PipelineError) as exc:
            asyncio.run(client.invoke(LLMRequest(
                task=TaskKind.CLASSIFY_DOC_TYPE,
                inputs={"first_page_excerpt": "x", "candidate_doc_types": ["test_report"]})))
        assert exc.value.code_id == "LLG-E001"

    def test_health_round_trip(self):
        gw = TestGatewayInvoke()._ollama_gateway()
        client = self._wired_client(gw)
        h = asyncio.run(client.health())
        assert "backends" in h and h["ready"] is False

    def test_client_has_no_credential_param(self):
        # architect ruling: OnPremLLMClient takes no credential_service
        import inspect

        from core.src.llm import OnPremLLMClient
        params = set(inspect.signature(OnPremLLMClient.__init__).parameters)
        assert "credential_service" not in params


class TestCli:
    def test_mock_all_tasks_green(self, capsys):
        from core.src.llm import llm_cli
        code = asyncio.run(llm_cli._cmd_mock("run-t"))
        out = capsys.readouterr().out
        assert "RPT|LLG|run-t" in out and "ok=5" in out and "fail=0" in out
        assert code == 0

    def test_contract_all_tasks_pass(self, capsys):
        from core.src.llm import llm_cli
        code = asyncio.run(llm_cli._cmd_contract("run-t"))
        out = capsys.readouterr().out
        assert "passed=5" in out and "failed=0" in out
        # one QC line per task
        assert out.count("QC|LLG|run-t") == 5
        assert code == 0

    def test_diagnostic_unreachable_is_honest(self, capsys):
        from core.src.llm import llm_cli
        code = asyncio.run(llm_cli._cmd_diagnostic("run-t", "http://127.0.0.1:1/nope"))
        out = capsys.readouterr().out
        assert "gateway_reachable=false" in out.lower()
        assert code == 1


class TestRegistrations:
    def test_all_14_llg_codes_registered(self):
        codes = [f"LLG-E00{i}" for i in range(1, 7)] + [f"LLG-W00{i}" for i in range(1, 9)]
        for code in codes:
            assert code in ERROR_CODES, code
        # E001-E006 hard, W001-W008 recoverable
        assert ERROR_CODES["LLG-E001"].recoverable is False
        assert ERROR_CODES["LLG-W006"].recoverable is True

    def test_qc_template_registered(self):
        assert "LLG:task_contract" in QC_REGISTRY

    def test_qc_template_validates_good_record(self):
        rec = ReportRecord(
            ReportType.QC, "LLG", "run-t", __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc),
            {"task": "review_document", "schema_valid": True, "latency_ms": 100,
             "confidence_bucket": "high", "result": "OK"},
        )
        assert TASK_CONTRACT.validate_record(rec) == []
