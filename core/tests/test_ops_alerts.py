"""ops_alerts test suite -- Protocol + composer + rate_limiter +
recipients_loader + OpsAlertsService + MockOpsAlerts.

Per [D-127] Ratified 2026-06-26. Covers Q1 (single-set routing + subject
tag + HTML badge), Q2 (rate_limit_per_minute int|None), Q3 (LOCAL
recipients.yaml shape), Q4 (10 module call-site readiness — Protocol
exposed correctly).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from core.src.diagnostics.error_codes import ERROR_CODES, PREFIX_REGISTRY, PipelineError
from core.src.ops_alerts import (
    MockOpsAlerts,
    OpsAlerts,
    OpsAlertResult,
    OpsAlertsConfig,
    OpsAlertsService,
    RateLimiter,
    Recipients,
    Severity,
    build_ops_alerts,
    compose_alert,
    load_recipients,
)

FIXTURE_PATH = (
    Path(__file__).parent / "fixtures" / "ops_alerts" / "test_recipients.yaml"
)


# ---------------------------------------------------------------------------
# Severity + OpsAlertResult shape
# ---------------------------------------------------------------------------


class TestSeverity:
    def test_4_values(self) -> None:
        assert {s.value for s in Severity} == {"info", "warning", "error", "critical"}

    def test_str_enum(self) -> None:
        # str-based for serialization friendliness
        assert Severity.CRITICAL == "critical"


class TestOpsAlertResult:
    def test_frozen_dataclass(self) -> None:
        r = OpsAlertResult(email_sent=True, messenger_dm_count=2, suppressed_by_rate_limit=False)
        with pytest.raises(Exception):
            r.email_sent = False  # type: ignore[misc]
        assert r.error_codes == []

    def test_carries_error_codes(self) -> None:
        r = OpsAlertResult(
            email_sent=False,
            messenger_dm_count=1,
            suppressed_by_rate_limit=False,
            error_codes=["OPS-E002", "OPS-E003"],
        )
        assert "OPS-E002" in r.error_codes


# ---------------------------------------------------------------------------
# Composer -- pure function
# ---------------------------------------------------------------------------


class TestComposeAlert:
    def test_subject_prefix_tag_per_q1(self) -> None:
        composed = compose_alert(
            source="issue_tracker",
            error_code="ITR-W004",
            context={},
            severity=Severity.CRITICAL,
        )
        assert composed.subject.startswith("[CRITICAL] HILDA: issue_tracker ITR-W004")

    def test_subject_per_severity(self) -> None:
        for sev in Severity:
            composed = compose_alert(
                source="x", error_code="y", context={}, severity=sev,
            )
            assert f"[{sev.value.upper()}]" in composed.subject

    def test_html_body_has_severity_badge(self) -> None:
        for sev in Severity:
            composed = compose_alert(
                source="x", error_code="y", context={}, severity=sev,
            )
            assert sev.value.upper() in composed.html_body
            assert "<span" in composed.html_body  # badge

    def test_html_body_color_per_q1(self) -> None:
        # critical + error: red; warning: orange; info: gray.
        critical = compose_alert(source="x", error_code="y", context={}, severity=Severity.CRITICAL)
        warning  = compose_alert(source="x", error_code="y", context={}, severity=Severity.WARNING)
        info     = compose_alert(source="x", error_code="y", context={}, severity=Severity.INFO)
        assert "#c0392b" in critical.html_body
        assert "#e67e22" in warning.html_body
        assert "#7f8c8d" in info.html_body

    def test_plain_body_has_no_html(self) -> None:
        composed = compose_alert(
            source="x", error_code="y",
            context={"foo": "bar"},
            severity=Severity.ERROR,
        )
        assert "<" not in composed.plain_body  # no html tags

    def test_plain_body_includes_context(self) -> None:
        composed = compose_alert(
            source="src", error_code="E001",
            context={"a": "1", "b": "2"},
            severity=Severity.INFO,
        )
        assert "a = 1" in composed.plain_body
        assert "b = 2" in composed.plain_body

    def test_credential_redaction(self) -> None:
        composed = compose_alert(
            source="x", error_code="y",
            context={"password": "supersecret", "totp_code": "123456", "ok_field": "visible"},
            severity=Severity.ERROR,
        )
        assert "supersecret" not in composed.plain_body
        assert "123456" not in composed.plain_body
        assert "<REDACTED>" in composed.plain_body
        assert "visible" in composed.plain_body

    def test_value_truncation_per_nfr2(self) -> None:
        long_value = "x" * 500
        composed = compose_alert(
            source="x", error_code="y",
            context={"long": long_value},
            severity=Severity.INFO,
            context_value_max_chars=100,
        )
        assert "[truncated]" in composed.plain_body
        assert long_value not in composed.plain_body

    def test_credential_key_variants_redacted(self) -> None:
        # Substring match for password/totp/secret/_key suffix
        composed = compose_alert(
            source="x", error_code="y",
            context={
                "user_password": "p1",
                "totp_seed": "t1",
                "api_key": "k1",
                "session_secret": "s1",
                "plain_field": "visible",
            },
            severity=Severity.INFO,
        )
        for cred_val in ("p1", "t1", "k1", "s1"):
            assert cred_val not in composed.plain_body
        assert "visible" in composed.plain_body


# ---------------------------------------------------------------------------
# Recipients loader
# ---------------------------------------------------------------------------


class TestRecipientsLoader:
    def test_loads_valid_yaml(self) -> None:
        r = load_recipients(FIXTURE_PATH)
        assert r.ops_bot_email == "test-ops@example.com"
        assert r.broadcast_corp_ids == ("alice_test", "bob_test")
        assert r.rate_limit_per_minute is None

    def test_missing_file_raises_ops_e001(self, tmp_path: Path) -> None:
        with pytest.raises(PipelineError) as ei:
            load_recipients(tmp_path / "nope.yaml")
        assert ei.value.code_id == "OPS-E001"

    def test_invalid_email_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text("ops_bot_email: no_at_sign\nbroadcast_corp_ids: []\n")
        with pytest.raises(PipelineError) as ei:
            load_recipients(p)
        assert ei.value.code_id == "OPS-E001"

    def test_invalid_rate_limit_raises(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.yaml"
        p.write_text(
            "ops_bot_email: a@b.com\nbroadcast_corp_ids: []\n"
            "rate_limit_per_minute: -1\n"
        )
        with pytest.raises(PipelineError) as ei:
            load_recipients(p)
        assert ei.value.code_id == "OPS-E001"

    def test_int_rate_limit_accepted(self, tmp_path: Path) -> None:
        p = tmp_path / "ok.yaml"
        p.write_text(
            "ops_bot_email: a@b.com\nbroadcast_corp_ids: []\n"
            "rate_limit_per_minute: 5\n"
        )
        r = load_recipients(p)
        assert r.rate_limit_per_minute == 5

    def test_bool_rate_limit_rejected(self, tmp_path: Path) -> None:
        # YAML true/false should NOT be accepted as rate limit
        p = tmp_path / "bad.yaml"
        p.write_text(
            "ops_bot_email: a@b.com\nbroadcast_corp_ids: []\n"
            "rate_limit_per_minute: true\n"
        )
        with pytest.raises(PipelineError) as ei:
            load_recipients(p)
        assert ei.value.code_id == "OPS-E001"

    def test_empty_corp_ids_list_ok(self, tmp_path: Path) -> None:
        p = tmp_path / "ok.yaml"
        p.write_text("ops_bot_email: a@b.com\nbroadcast_corp_ids: []\n")
        r = load_recipients(p)
        assert r.broadcast_corp_ids == ()


# ---------------------------------------------------------------------------
# Rate limiter -- per (source, error_code) rolling window per Q2
# ---------------------------------------------------------------------------


class TestRateLimiter:
    def test_none_pass_through(self) -> None:
        limiter = RateLimiter(rate_limit_per_minute=None)
        for _ in range(100):
            assert limiter.allow("src", "ERR-1") is True

    def test_n_per_window(self) -> None:
        clock_state = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        limiter = RateLimiter(rate_limit_per_minute=3, window_seconds=60,
                              clock=lambda: clock_state["t"])
        assert limiter.allow("src", "E1") is True
        assert limiter.allow("src", "E1") is True
        assert limiter.allow("src", "E1") is True
        # 4th call within window → suppressed
        assert limiter.allow("src", "E1") is False

    def test_window_eviction(self) -> None:
        clock_state = {"t": datetime(2026, 1, 1, tzinfo=timezone.utc)}
        limiter = RateLimiter(rate_limit_per_minute=2, window_seconds=60,
                              clock=lambda: clock_state["t"])
        assert limiter.allow("s", "E") is True
        assert limiter.allow("s", "E") is True
        assert limiter.allow("s", "E") is False
        # Advance past window
        clock_state["t"] += timedelta(seconds=61)
        assert limiter.allow("s", "E") is True

    def test_per_source_error_code_independence(self) -> None:
        limiter = RateLimiter(rate_limit_per_minute=1, window_seconds=60)
        assert limiter.allow("src_a", "ERR-1") is True
        assert limiter.allow("src_a", "ERR-1") is False
        # Different source OR different error_code = separate bucket
        assert limiter.allow("src_b", "ERR-1") is True
        assert limiter.allow("src_a", "ERR-2") is True


# ---------------------------------------------------------------------------
# OpsAlertsService -- fan-out + fire-and-forget per [D-127] Invariant 4
# ---------------------------------------------------------------------------


class _FakeEmailSender:
    def __init__(self, *, raise_on_send: bool = False) -> None:
        self._raise = raise_on_send
        self.sent: list[dict] = []

    async def send(
        self,
        to: list[str],
        cc: list[str],
        subject: str,
        body: str,
        in_reply_to: str | None = None,
    ) -> str:
        if self._raise:
            raise RuntimeError("smtp down")
        record = {"to": to, "cc": cc, "subject": subject, "body": body}
        self.sent.append(record)
        return "msg-id-001"


class _FakeMessenger:
    def __init__(self, *, return_value: bool = True, raise_on_corp_id: str | None = None) -> None:
        self._rv = return_value
        self._raise_on = raise_on_corp_id
        self.sent: list[tuple[str, str]] = []

    async def send(self, owner_corp_id: str, message: str) -> bool:
        if self._raise_on and owner_corp_id == self._raise_on:
            raise ConnectionError("messenger down")
        self.sent.append((owner_corp_id, message))
        return self._rv


def _make_config(tmp_path: Path) -> OpsAlertsConfig:
    return OpsAlertsConfig(
        recipients_yaml_path=FIXTURE_PATH,
        ops_log_fallback_path=tmp_path / "ops.log",
    )


def _make_recipients(rate: int | None = None, broadcast: tuple[str, ...] = ("alice", "bob")) -> Recipients:
    return Recipients(
        ops_bot_email="ops@example.com",
        broadcast_corp_ids=broadcast,
        rate_limit_per_minute=rate,
    )


@pytest.mark.asyncio
class TestOpsAlertsServiceFanOut:
    async def test_happy_path_both_channels(self, tmp_path: Path) -> None:
        email = _FakeEmailSender()
        msg = _FakeMessenger()
        svc = OpsAlertsService(_make_config(tmp_path), _make_recipients(),
                               email_sender=email, messenger=msg)
        result = await svc.emit_alert(
            source="issue_tracker.ITR-W004",
            error_code="ITR-W004",
            context={"item_id": "i1", "plm_id": "p1", "attempts": 5},
            severity=Severity.ERROR,
        )
        assert result.email_sent is True
        assert result.messenger_dm_count == 2
        assert result.suppressed_by_rate_limit is False
        assert result.error_codes == []
        assert email.sent[0]["to"] == ["ops@example.com"]
        assert "[ERROR] HILDA: issue_tracker.ITR-W004 ITR-W004" in email.sent[0]["subject"]
        assert {x[0] for x in msg.sent} == {"alice", "bob"}

    async def test_email_failure_does_not_break_fanout(self, tmp_path: Path) -> None:
        email = _FakeEmailSender(raise_on_send=True)
        msg = _FakeMessenger()
        svc = OpsAlertsService(_make_config(tmp_path), _make_recipients(),
                               email_sender=email, messenger=msg)
        result = await svc.emit_alert("s", "E1", {}, Severity.ERROR)
        assert result.email_sent is False
        assert result.messenger_dm_count == 2
        assert "OPS-E002" in result.error_codes

    async def test_messenger_partial_failure(self, tmp_path: Path) -> None:
        email = _FakeEmailSender()
        msg = _FakeMessenger(raise_on_corp_id="bob")
        svc = OpsAlertsService(_make_config(tmp_path), _make_recipients(),
                               email_sender=email, messenger=msg)
        result = await svc.emit_alert("s", "E1", {}, Severity.WARNING)
        assert result.email_sent is True
        assert result.messenger_dm_count == 1
        assert "OPS-E003" in result.error_codes

    async def test_messenger_total_failure(self, tmp_path: Path) -> None:
        email = _FakeEmailSender()
        msg = _FakeMessenger(return_value=False)
        svc = OpsAlertsService(_make_config(tmp_path), _make_recipients(),
                               email_sender=email, messenger=msg)
        result = await svc.emit_alert("s", "E1", {}, Severity.WARNING)
        # all-DMs-False (gateway-rejected) → OPS-E004
        assert result.messenger_dm_count == 0
        assert "OPS-E004" in result.error_codes

    async def test_local_log_fallback_when_both_channels_fail(self, tmp_path: Path) -> None:
        email = _FakeEmailSender(raise_on_send=True)
        msg = _FakeMessenger(return_value=False)
        cfg = _make_config(tmp_path)
        svc = OpsAlertsService(cfg, _make_recipients(),
                               email_sender=email, messenger=msg)
        await svc.emit_alert("s", "E1", {"k": "v"}, Severity.CRITICAL)
        assert cfg.ops_log_fallback_path is not None
        log_text = cfg.ops_log_fallback_path.read_text(encoding="utf-8")
        assert "[CRITICAL] HILDA: s E1" in log_text
        assert "k = v" in log_text

    async def test_rate_limit_suppresses(self, tmp_path: Path) -> None:
        email = _FakeEmailSender()
        msg = _FakeMessenger()
        svc = OpsAlertsService(
            _make_config(tmp_path),
            _make_recipients(rate=1),
            email_sender=email, messenger=msg,
        )
        r1 = await svc.emit_alert("s", "E1", {}, Severity.ERROR)
        r2 = await svc.emit_alert("s", "E1", {}, Severity.ERROR)
        assert r1.suppressed_by_rate_limit is False
        assert r2.suppressed_by_rate_limit is True
        assert r2.messenger_dm_count == 0
        assert len(email.sent) == 1

    async def test_emit_alert_never_raises_on_any_failure(self, tmp_path: Path) -> None:
        # Per [D-127] Invariant 4 -- even with both channels None, no raise.
        svc = OpsAlertsService(
            _make_config(tmp_path),
            _make_recipients(),
            email_sender=None,
            messenger=None,
        )
        result = await svc.emit_alert("s", "E1", {}, Severity.CRITICAL)
        assert result.email_sent is False
        assert result.messenger_dm_count == 0

    async def test_health_returns_summary(self, tmp_path: Path) -> None:
        svc = OpsAlertsService(
            _make_config(tmp_path),
            _make_recipients(rate=10, broadcast=("a", "b", "c")),
            email_sender=_FakeEmailSender(), messenger=_FakeMessenger(),
        )
        h = await svc.health()
        assert h["ready"] is True
        assert h["ops_bot_email"] == "ops@example.com"
        assert h["broadcast_corp_id_count"] == 3
        assert h["rate_limit_per_minute"] == 10

    async def test_no_messenger_zero_dms(self, tmp_path: Path) -> None:
        svc = OpsAlertsService(
            _make_config(tmp_path),
            _make_recipients(),
            email_sender=_FakeEmailSender(),
            messenger=None,
        )
        result = await svc.emit_alert("s", "E1", {}, Severity.INFO)
        assert result.email_sent is True
        assert result.messenger_dm_count == 0
        # No error code since messenger is intentionally absent
        assert result.error_codes == []


@pytest.mark.asyncio
class TestBuildOpsAlerts:
    async def test_build_helper_wires(self, tmp_path: Path) -> None:
        svc = build_ops_alerts(
            _make_config(tmp_path),
            _make_recipients(),
            email_sender=_FakeEmailSender(),
            messenger=_FakeMessenger(),
        )
        assert isinstance(svc, OpsAlertsService)
        assert isinstance(svc, OpsAlerts)


# ---------------------------------------------------------------------------
# MockOpsAlerts -- test double
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMockOpsAlerts:
    async def test_captures_calls(self) -> None:
        m = MockOpsAlerts()
        result = await m.emit_alert(
            source="src", error_code="E001",
            context={"k": "v"}, severity=Severity.WARNING,
        )
        assert isinstance(result, OpsAlertResult)
        assert len(m.calls) == 1
        assert m.calls[0].source == "src"
        assert m.calls[0].error_code == "E001"
        assert m.calls[0].context == {"k": "v"}
        assert m.calls[0].severity == Severity.WARNING

    async def test_set_next_result_consumed_once(self) -> None:
        m = MockOpsAlerts()
        m.set_next_result(OpsAlertResult(
            email_sent=False, messenger_dm_count=0,
            suppressed_by_rate_limit=True,
        ))
        first = await m.emit_alert("s", "E1", {}, Severity.INFO)
        second = await m.emit_alert("s", "E1", {}, Severity.INFO)
        assert first.suppressed_by_rate_limit is True
        assert second.suppressed_by_rate_limit is False  # back to default

    async def test_implements_protocol(self) -> None:
        m: OpsAlerts = MockOpsAlerts()  # type-check: assignment is the proof
        h = await m.health()
        assert h["ready"] is True


# ---------------------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------------------


class TestErrorCodes:
    def test_ops_prefix_registered(self) -> None:
        assert PREFIX_REGISTRY["OPS"] == "ops_alerts"

    def test_all_ops_codes_present(self) -> None:
        expected = {
            "OPS-E001", "OPS-E002", "OPS-E003", "OPS-E004", "OPS-E005",
            "OPS-W001", "OPS-W002",
        }
        for code in expected:
            assert code in ERROR_CODES, f"Missing OPS code: {code}"


# ---------------------------------------------------------------------------
# Recursion guard (per [D-127] Invariant 9) -- design-time check
# ---------------------------------------------------------------------------


class TestRecursionGuard:
    def test_messenger_does_not_import_ops_alerts(self) -> None:
        # Per [D-127] Invariant 9: messenger MUST NOT depend on ops_alerts
        # (else: messenger.send failures → ops_alerts.emit_alert → which
        # calls messenger.send → infinite recursion).
        import importlib
        importlib.import_module("core.src.messenger")
        # Now scan messenger's __init__ + service modules for ops_alerts imports
        from pathlib import Path as _P
        messenger_root = _P(__file__).parent.parent / "src" / "messenger"
        for py in messenger_root.glob("*.py"):
            text = py.read_text(encoding="utf-8")
            # Look for actual imports, not just the string (PREFIX_REGISTRY
            # entries name modules as strings without importing them).
            assert "from core.src.ops_alerts" not in text and "import ops_alerts" not in text, (
                f"messenger/{py.name} imports ops_alerts -- recursion guard breach "
                f"per [D-127] Invariant 9"
            )

    def test_email_service_does_not_import_ops_alerts(self) -> None:
        # Same guard for email_service.
        from pathlib import Path as _P
        email_root = _P(__file__).parent.parent / "src" / "email_service"
        for py in email_root.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            # Look for actual imports, not just the string (PREFIX_REGISTRY
            # entries name modules as strings without importing them).
            assert "from core.src.ops_alerts" not in text and "import ops_alerts" not in text, (
                f"email_service/{py.relative_to(email_root)} imports ops_alerts -- "
                f"recursion guard breach per [D-127] Invariant 9"
            )

    def test_diagnostics_does_not_import_ops_alerts(self) -> None:
        from pathlib import Path as _P
        diag_root = _P(__file__).parent.parent / "src" / "diagnostics"
        for py in diag_root.rglob("*.py"):
            text = py.read_text(encoding="utf-8")
            # Look for actual imports, not just the string (PREFIX_REGISTRY
            # entries name modules as strings without importing them).
            assert "from core.src.ops_alerts" not in text and "import ops_alerts" not in text, (
                f"diagnostics/{py.relative_to(diag_root)} imports ops_alerts -- "
                f"recursion guard breach per [D-127] Invariant 9"
            )
