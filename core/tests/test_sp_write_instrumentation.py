"""SPWLOG-1: instrumentation on the HILDA -> SP write path.

Before this, a successful SP mutation left no trace: only failures surfaced
as `PipelineError("SHP-E001")`. These tests pin the two log layers --

  * `SP_WRITE` (semantic, `SpClient`): op / list / item_id / status / origin
    / redacted fields.
  * `SP_HTTP`  (transport, `SpSession._post_with_retry`): the waist every
    create, merge and delete passes through, including the 403 digest retry.

-- plus the NFR-2 guarantee that no digest, cookie or password reaches a log.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from core.src.diagnostics import PipelineError
from core.src.sharepoint_integration.sp_client import SpClient
from core.src.sharepoint_integration.sp_session import SpSession
from core.src.sharepoint_integration.write_audit import (
    SP_WRITE_ORIGIN,
    caller_ref,
    redact_fields,
    sp_write_origin,
)


# --- redact_fields --------------------------------------------------------


class TestRedactFields:
    def test_empty_and_none_render_as_dash(self) -> None:
        assert redact_fields(None) == "-"
        assert redact_fields({}) == "-"

    def test_pairs_are_sorted_for_stable_grepping(self) -> None:
        out = redact_fields({"zeta": "1", "alpha": "2"})
        assert out == "alpha=2 zeta=1"

    def test_metadata_discriminator_is_dropped(self) -> None:
        out = redact_fields({"__metadata": {"type": "SP.Data.X"}, "a": "1"})
        assert out == "a=1"

    def test_only_metadata_renders_as_dash(self) -> None:
        assert redact_fields({"__metadata": {"type": "SP.Data.X"}}) == "-"

    @pytest.mark.parametrize(
        "key",
        ["password", "Password", "ntlm_pass", "FormDigestValue",
         "X-RequestDigest", "Cookie", "access_token", "client_secret"],
    )
    def test_secret_keys_are_masked(self, key: str) -> None:
        assert redact_fields({key: "hunter2"}) == f"{key}=***"
        assert "hunter2" not in redact_fields({key: "hunter2"})

    def test_long_values_truncated_with_original_length(self) -> None:
        out = redact_fields({"desc": "x" * 200})
        assert out.startswith("desc=" + "x" * 80)
        assert out.endswith("...<200c>")

    def test_delivery_state_survives_verbatim(self) -> None:
        # The whole point of SPWLOG-1 -- this value must be readable.
        assert redact_fields({"delivery_state": "OutreachSent"}) == (
            "delivery_state=OutreachSent"
        )


# --- caller_ref / sp_write_origin ----------------------------------------


class TestCallerAttribution:
    def test_caller_ref_names_the_calling_test(self) -> None:
        ref = caller_ref()
        assert "test_sp_write_instrumentation" in ref
        assert "test_caller_ref_names_the_calling_test" in ref

    def test_caller_ref_skips_sp_plumbing_frames(self) -> None:
        # Called through a real SP module frame: attribution must walk past
        # it and land on this test, not on sp_client.
        from core.src.sharepoint_integration import sp_client as mod

        ref = mod._origin()
        assert "sharepoint_integration" not in ref
        assert "test_caller_ref_skips_sp_plumbing_frames" in ref

    def test_caller_ref_never_raises(self) -> None:
        assert isinstance(caller_ref(max_depth=0), str)

    def test_origin_contextvar_defaults_empty(self) -> None:
        assert SP_WRITE_ORIGIN.get() == ""

    def test_explicit_label_wins_over_stack(self) -> None:
        from core.src.sharepoint_integration import sp_client as mod

        with sp_write_origin("plm_poll.backfill"):
            assert mod._origin() == "plm_poll.backfill"
        # reset after the block
        assert mod._origin() != "plm_poll.backfill"

    def test_origin_nests_and_restores(self) -> None:
        with sp_write_origin("outer"):
            with sp_write_origin("inner"):
                assert SP_WRITE_ORIGIN.get() == "inner"
            assert SP_WRITE_ORIGIN.get() == "outer"
        assert SP_WRITE_ORIGIN.get() == ""

    def test_origin_restores_on_exception(self) -> None:
        with pytest.raises(RuntimeError):
            with sp_write_origin("doomed"):
                raise RuntimeError("boom")
        assert SP_WRITE_ORIGIN.get() == ""


# --- SpClient SP_WRITE lines ---------------------------------------------


class _StubSession:
    """Minimal SpSession stand-in exposing merge/create/delete."""

    def __init__(self, status: int = 204, body: Any = None) -> None:
        self.status = status
        self.body = body if body is not None else {"Id": 42}
        self.calls: list[tuple[str, tuple[Any, ...]]] = []

    def merge(self, list_name, customer_id, item_id, fields):  # noqa: ANN001
        self.calls.append(("merge", (list_name, customer_id, item_id, fields)))
        return self.status

    def create(self, list_name, customer_id, fields):  # noqa: ANN001
        self.calls.append(("create", (list_name, customer_id, fields)))
        return self.status, self.body

    def delete(self, list_name, item_id):  # noqa: ANN001
        self.calls.append(("delete", (list_name, item_id)))
        return self.status


def _client(session: _StubSession) -> SpClient:
    client = SpClient.__new__(SpClient)
    client._session = session  # type: ignore[attr-defined]
    return client


def _lines(caplog: pytest.LogCaptureFixture, tag: str) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith(tag)]


class TestSpClientWriteLogging:
    @pytest.mark.asyncio
    async def test_update_emits_sp_write_with_full_context(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _client(_StubSession(status=204))
        with caplog.at_level(logging.WARNING):
            await client.update_list_item(
                "Deliverables_MMK", "12459",
                {"delivery_state": "OutreachSent"}, customer_id="MMK",
            )
        (line,) = _lines(caplog, "SP_WRITE")
        assert "op=update" in line
        assert "list=Deliverables_MMK" in line
        assert "item_id=12459" in line
        assert "status=204" in line
        assert "delivery_state=OutreachSent" in line
        # attribution points at the caller, not the SP plumbing
        assert "origin=" in line
        assert "sharepoint_integration" not in line.split("origin=")[1]

    @pytest.mark.asyncio
    async def test_update_honours_explicit_origin_label(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _client(_StubSession())
        with caplog.at_level(logging.WARNING):
            with sp_write_origin("transitions.state_change"):
                await client.update_list_item(
                    "Deliverables_MMK", "1", {"a": "b"}, customer_id="MMK",
                )
        assert "origin=transitions.state_change" in _lines(caplog, "SP_WRITE")[0]

    @pytest.mark.asyncio
    async def test_create_emits_sp_write(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _client(_StubSession(status=201))
        with caplog.at_level(logging.WARNING):
            await client.create_list_item(
                "Deliverables_MMK", {"item_no": 5}, customer_id="MMK",
            )
        (line,) = _lines(caplog, "SP_WRITE")
        assert "op=create" in line and "status=201" in line and "item_no=5" in line

    @pytest.mark.asyncio
    async def test_delete_emits_sp_write(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _client(_StubSession(status=204))
        with caplog.at_level(logging.WARNING):
            await client.delete_list_item("Deliverables_MMK", "77")
        (line,) = _lines(caplog, "SP_WRITE")
        assert "op=delete" in line and "item_id=77" in line

    @pytest.mark.asyncio
    async def test_failed_write_is_logged_before_raising(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # A 500 must leave a record too -- the log is the audit trail, so it
        # cannot be conditional on success.
        client = _client(_StubSession(status=500))
        with caplog.at_level(logging.WARNING):
            with pytest.raises(PipelineError):
                await client.update_list_item(
                    "Deliverables_MMK", "9", {"x": "y"}, customer_id="MMK",
                )
        assert "status=500" in _lines(caplog, "SP_WRITE")[0]

    @pytest.mark.asyncio
    async def test_write_log_masks_secret_valued_fields(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _client(_StubSession())
        with caplog.at_level(logging.WARNING):
            await client.update_list_item(
                "L", "1", {"password": "hunter2"}, customer_id="MMK",
            )
        assert "hunter2" not in _lines(caplog, "SP_WRITE")[0]


# --- SpSession SP_HTTP lines ---------------------------------------------


class _FakeResponse:
    def __init__(self, status_code: int, json_body: Any = None) -> None:
        self.status_code = status_code
        self._json_body = json_body
        self.headers: dict[str, str] = {}
        self.content = json.dumps(json_body).encode() if json_body else b""

    def json(self) -> Any:
        if self._json_body is None:
            raise ValueError("no body")
        return self._json_body


class _SeqSession:
    """requests.Session stand-in returning queued responses in order."""

    def __init__(self, responses: list[_FakeResponse]) -> None:
        self._responses = responses
        self.posts: list[tuple[str, dict[str, str] | None]] = []

    def post(self, url, headers=None, data=None, **kw):  # noqa: ANN001
        self.posts.append((url, headers))
        return self._responses.pop(0)

    def get(self, url, params=None, **kw):  # noqa: ANN001
        return _FakeResponse(200)


def _session_with(responses: list[_FakeResponse]) -> tuple[SpSession, _SeqSession]:
    rec = _SeqSession(responses)
    sess = SpSession.__new__(SpSession)
    sess._site_url = "https://sp.example"
    sess._session = rec  # type: ignore[assignment]
    sess._cookie = None
    sess._digest = "DIGEST-TOKEN-abc123"
    return sess, rec


class TestSpSessionHttpLogging:
    def test_merge_emits_sp_http_at_the_waist(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        sess, _ = _session_with([_FakeResponse(204)])
        with caplog.at_level(logging.WARNING):
            status = sess.merge("Deliverables_MMK", "MMK", 12459,
                                {"delivery_state": "Open"})
        assert status == 204
        (line,) = _lines(caplog, "SP_HTTP")
        assert "method=MERGE" in line and "status=204" in line
        assert "items(12459)" in line

    def test_403_logs_both_attempts(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        sess, _ = _session_with([_FakeResponse(403), _FakeResponse(204)])
        sess._refresh_digest = lambda: None  # type: ignore[method-assign]
        with caplog.at_level(logging.WARNING):
            sess.merge("L_MMK", "MMK", 1, {"a": "b"})
        lines = _lines(caplog, "SP_HTTP")
        assert len(lines) == 2
        assert "status=403" in lines[0]
        assert "status=204" in lines[1] and "after digest refresh" in lines[1]

    def test_delete_and_create_also_reach_the_waist(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        sess, _ = _session_with([_FakeResponse(204)])
        with caplog.at_level(logging.WARNING):
            sess.delete("L_MMK", 3)
        assert "method=DELETE" in _lines(caplog, "SP_HTTP")[0]

        caplog.clear()
        sess2, _ = _session_with([_FakeResponse(201, {"Id": 9})])
        with caplog.at_level(logging.WARNING):
            sess2.create("L_MMK", "MMK", {"a": "b"})
        assert "method=POST" in _lines(caplog, "SP_HTTP")[0]

    def test_nfr2_no_digest_or_cookie_in_any_log_line(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        sess, _ = _session_with([_FakeResponse(204)])
        sess._cookie = "WSSAUTH=super-secret-cookie"
        with caplog.at_level(logging.WARNING):
            sess.merge("L_MMK", "MMK", 1, {"a": "b"})
        blob = "\n".join(r.getMessage() for r in caplog.records)
        assert "DIGEST-TOKEN-abc123" not in blob
        assert "super-secret-cookie" not in blob
