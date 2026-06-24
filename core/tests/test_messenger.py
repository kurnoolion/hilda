"""messenger test suite -- protocol + config + composer + daily-limit + adapter +
service + mocks + error codes + Protocol conformance + CLI smoke.

Per messenger/MODULE.md test-scenario subset (FR-10 cross-channel escalation
Ph-1 happy path) + architect Q-M1..Q-M6 lock 2026-06-25.

Uses MockMessengerAdapter + InMemoryMessengerStorage from core.src.messenger.mocks.
Privacy convention: all owner_corp_id values are synthetic test_* IDs per NFR-2.
"""
from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from core.src.diagnostics.error_codes import ERROR_CODES, PipelineError
from core.src.messenger import (
    CorpMessengerAdapter,
    DailyLimitChecker,
    EscalationReason,
    InMemoryMessengerStorage,
    MessengerAdapter,
    MessengerConfig,
    MessengerService,
    MockMessengerAdapter,
    REQUIRED_TEMPLATE_VARS,
    SendResult,
    compose_escalation,
    redact_owner_corp_id,
    truncate_message,
    validate_message_length,
)
from core.src.messenger.messenger_cli import main as cli_main


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _now() -> datetime:
    return datetime(2026, 6, 25, 10, 0, 0, tzinfo=timezone.utc)


def _config(**overrides: Any) -> MessengerConfig:
    base = {
        "templates_dir": Path(__file__).resolve().parents[1] / "src" / "messenger" / "templates",
        "retry_attempts": 1,
        "retry_backoff_seconds": 0.0,
    }
    base.update(overrides)
    return MessengerConfig(**base)


def _milestone_ctx(**overrides: Any) -> dict[str, Any]:
    base = {
        "owner_name": "Test Owner",
        "deliverable_count": 2,
        "milestone_name": "P1",
        "device_id": "MODEL-A",
        "deadline_date": "2026-07-01",
        "corp_plm_link": "https://corp_plm.example/PLM-1",
        "reminder_count": 2,
    }
    base.update(overrides)
    return base


def _item(owner_corp_id: str = "test_owner_001", item_id: str = "ITEM-1") -> dict:
    return {
        "item_id": item_id,
        "owner_corp_id": owner_corp_id,
        "device_id": "MODEL-A",
    }


# ===========================================================================
# TestEscalationReason
# ===========================================================================


class TestEscalationReason:
    def test_enum_values(self):
        assert EscalationReason.POST_REMINDER.value == "post_reminder"
        assert EscalationReason.DEADLINE_IMMINENT.value == "deadline_imminent"
        assert EscalationReason.MANUAL_TPM.value == "manual_tpm"


# ===========================================================================
# TestSendResult
# ===========================================================================


class TestSendResult:
    def test_frozen_dataclass(self):
        r = SendResult(
            success=True,
            owner_corp_id="test_owner_001",
            message_bytes=128,
            error_code=None,
            sent_at=_now(),
        )
        assert r.success is True
        with pytest.raises(Exception):
            r.success = False  # type: ignore[misc]


# ===========================================================================
# TestMessengerConfig
# ===========================================================================


class TestMessengerConfig:
    def test_defaults_match_architect_locks(self):
        cfg = MessengerConfig()
        # Q-M2 hard caps:
        assert cfg.daily_limit_per_owner == 3
        assert cfg.max_message_bytes == 4000
        # Q-M3 anonymous attribution -- bot signature, never TPM identity:
        assert "BOT" in cfg.bot_signature

    def test_overrides_apply(self):
        cfg = MessengerConfig(daily_limit_per_owner=5, max_message_bytes=1000)
        assert cfg.daily_limit_per_owner == 5
        assert cfg.max_message_bytes == 1000


# ===========================================================================
# TestComposer
# ===========================================================================


class TestComposer:
    def test_happy_path_render(self):
        cfg = _config()
        result = compose_escalation(
            config=cfg,
            owner_name="Test Owner",
            deliverable_count=3,
            milestone_name="P1",
            device_id="MODEL-A",
            deadline_date="2026-07-01",
            corp_plm_link="https://corp_plm.example/PLM-1",
            reminder_count=2,
            batch_id="BATCH-abc",
            reason="post_reminder",
        )
        assert not result.truncated
        assert result.rendered_bytes == len(result.message.encode("utf-8"))
        assert "Test Owner" in result.message
        assert "P1" in result.message
        assert "MODEL-A" in result.message
        assert "BATCH-abc" in result.message
        assert "HILDA BOT" in result.message  # Q-M3 anonymous signature

    def test_truncation_emits_marker_and_flag(self):
        # Force a tiny max-bytes cap to drive truncation
        cfg = _config(max_message_bytes=100)
        result = compose_escalation(
            config=cfg,
            owner_name="Test Owner",
            deliverable_count=3,
            milestone_name="P1",
            device_id="MODEL-A",
            deadline_date="2026-07-01",
            corp_plm_link="https://corp_plm.example/PLM-1",
            reminder_count=2,
            batch_id="BATCH-abc",
            reason="post_reminder",
        )
        assert result.truncated is True
        assert len(result.message.encode("utf-8")) <= 100
        assert "[truncated]" in result.message
        assert result.original_bytes > result.rendered_bytes

    def test_required_vars_constant_lists_template_inputs(self):
        # Defensive: keep the template variable list in lockstep with composer.
        for name in (
            "owner_name", "deliverable_count", "milestone_name", "device_id",
            "deadline_date", "corp_plm_link", "reminder_count", "batch_id", "reason",
        ):
            assert name in REQUIRED_TEMPLATE_VARS

    def test_template_missing_raises_msg_e002(self, tmp_path):
        cfg = MessengerConfig(templates_dir=tmp_path)  # empty dir -- no template
        with pytest.raises(PipelineError) as exc_info:
            compose_escalation(
                config=cfg,
                owner_name="Owner", deliverable_count=1, milestone_name="P1",
                device_id="MODEL-A", deadline_date="2026-07-01",
                corp_plm_link="x", reminder_count=1, batch_id="BATCH-x",
                reason="post_reminder",
            )
        assert exc_info.value.code_id == "MSG-E002"

    def test_no_tpm_identity_in_message_per_q_m3(self):
        """Q-M3 lock: sender appears as 'anonymous HILDA BOT' -- no TPM preamble."""
        cfg = _config()
        result = compose_escalation(
            config=cfg, owner_name="Owner", deliverable_count=1,
            milestone_name="P1", device_id="MODEL-A", deadline_date="2026-07-01",
            corp_plm_link="x", reminder_count=1, batch_id="BATCH-x",
            reason="post_reminder",
        )
        # No "on behalf of TPM" / "from TPM X" preamble:
        assert "behalf of TPM" not in result.message
        assert "from TPM" not in result.message


# ===========================================================================
# TestDailyLimitChecker
# ===========================================================================


class TestDailyLimitChecker:
    async def test_first_send_ok(self):
        storage = InMemoryMessengerStorage()
        checker = DailyLimitChecker(storage=storage, config=_config())
        assert await checker.can_send("test_owner_001", _now()) is True

    async def test_third_send_ok(self):
        storage = InMemoryMessengerStorage()
        cfg = _config()
        checker = DailyLimitChecker(storage=storage, config=cfg)
        ts = _now()
        # Record 2 prior sends today -- the 3rd should still be permitted.
        await checker.record_send("test_owner_001", 200, True, ts)
        await checker.record_send("test_owner_001", 200, True, ts)
        assert await checker.can_send("test_owner_001", ts) is True
        assert await checker.current_count("test_owner_001", ts) == 2

    async def test_fourth_send_blocked(self):
        storage = InMemoryMessengerStorage()
        cfg = _config()
        checker = DailyLimitChecker(storage=storage, config=cfg)
        ts = _now()
        for _ in range(3):
            await checker.record_send("test_owner_001", 200, True, ts)
        assert await checker.current_count("test_owner_001", ts) == 3
        assert await checker.can_send("test_owner_001", ts) is False

    async def test_cross_day_boundary_resets(self):
        storage = InMemoryMessengerStorage()
        cfg = _config()
        checker = DailyLimitChecker(storage=storage, config=cfg)
        yesterday = _now() - timedelta(days=1)
        today = _now()
        # 3 sends "yesterday"
        for _ in range(3):
            await checker.record_send("test_owner_001", 200, True, yesterday)
        # Today's count should be 0 -- fresh window
        assert await checker.current_count("test_owner_001", today) == 0
        assert await checker.can_send("test_owner_001", today) is True


# ===========================================================================
# TestCorpMessengerAdapter
# ===========================================================================


class TestCorpMessengerAdapter:
    async def test_base_class_raises_msg_e003_on_send(self):
        adapter = CorpMessengerAdapter(config=_config())
        with pytest.raises(PipelineError) as exc_info:
            await adapter.send("test_owner_001", "hello")
        assert exc_info.value.code_id == "MSG-E003"

    async def test_subclass_success_path(self):
        class _OkAdapter(CorpMessengerAdapter):
            async def _invoke_send_message(self, owner_corp_id, message):
                return True

        adapter = _OkAdapter(config=_config(), customer_id="example")
        assert await adapter.send("test_owner_001", "hello") is True
        assert adapter.source_system == "corp_messenger/example"

    async def test_subclass_failure_returns_false_per_q_m1(self):
        """Per Q-M1: binding raised -> adapter returns False (NOT re-raises)."""
        class _RaisesAdapter(CorpMessengerAdapter):
            async def _invoke_send_message(self, owner_corp_id, message):
                raise RuntimeError("gateway transport error")

        adapter = _RaisesAdapter(config=_config())
        assert await adapter.send("test_owner_001", "hello") is False


# ===========================================================================
# TestMessengerService
# ===========================================================================


class TestMessengerService:
    async def test_full_flow_happy(self):
        storage = InMemoryMessengerStorage()
        adapter = MockMessengerAdapter()
        cfg = _config()
        service = MessengerService(
            adapter=adapter,
            daily_checker=DailyLimitChecker(storage=storage, config=cfg),
            config=cfg,
        )
        result = await service.send_escalation(
            item=_item(),
            batch_id="BATCH-abc",
            reason=EscalationReason.POST_REMINDER,
            milestone_ctx=_milestone_ctx(),
        )
        assert result.success is True
        assert result.error_code is None
        assert result.message_bytes > 0
        # CommunicationLog row written per Q-M5:
        assert len(storage.communications) == 1
        row = storage.communications[0]
        assert row.action_type == "messenger_escalation"
        # Sender attribution is anonymous per Q-M3 -- no TPM in sender field:
        assert row.sender is None
        # Q-M5: no external_message_id Ph-1 (bool-only outcome):
        assert row.external_message_id is None
        # Adapter was invoked exactly once on happy path:
        assert len(adapter.calls) == 1

    async def test_daily_limit_blocked_emits_msg_w001(self):
        storage = InMemoryMessengerStorage()
        adapter = MockMessengerAdapter()
        cfg = _config()
        checker = DailyLimitChecker(storage=storage, config=cfg)
        # Pre-load 3 prior sends for the owner today -- must match the
        # service's internal _utcnow() day window, so use a real `now` here.
        ts = datetime.now(timezone.utc)
        for _ in range(3):
            await checker.record_send("test_owner_001", 200, True, ts)
        service = MessengerService(adapter=adapter, daily_checker=checker, config=cfg)
        result = await service.send_escalation(
            item=_item(),
            batch_id="BATCH-abc",
            reason=EscalationReason.POST_REMINDER,
            milestone_ctx=_milestone_ctx(),
        )
        assert result.success is False
        assert result.error_code == "MSG-W001"
        # Adapter NOT invoked -- short-circuited:
        assert len(adapter.calls) == 0
        # 3 prior + 1 blocked-attempt audit row = 4 rows total
        assert len(storage.communications) == 4

    async def test_composer_truncation_still_sends(self):
        """Truncation is a warning, not a hard block -- the truncated message sends."""
        storage = InMemoryMessengerStorage()
        adapter = MockMessengerAdapter()
        cfg = _config(max_message_bytes=100)  # force truncation
        service = MessengerService(
            adapter=adapter,
            daily_checker=DailyLimitChecker(storage=storage, config=cfg),
            config=cfg,
        )
        result = await service.send_escalation(
            item=_item(),
            batch_id="BATCH-abc",
            reason=EscalationReason.POST_REMINDER,
            milestone_ctx=_milestone_ctx(),
        )
        assert result.success is True
        assert result.message_bytes <= 100
        sent_msg = adapter.calls[0][1]
        assert "[truncated]" in sent_msg

    async def test_adapter_failure_with_retry(self):
        storage = InMemoryMessengerStorage()
        adapter = MockMessengerAdapter()
        adapter.set_default(False)  # all attempts fail
        cfg = _config(retry_attempts=3, retry_backoff_seconds=0.0)
        service = MessengerService(
            adapter=adapter,
            daily_checker=DailyLimitChecker(storage=storage, config=cfg),
            config=cfg,
        )
        result = await service.send_escalation(
            item=_item(),
            batch_id="BATCH-abc",
            reason=EscalationReason.POST_REMINDER,
            milestone_ctx=_milestone_ctx(),
        )
        assert result.success is False
        assert result.error_code == "MSG-E001"
        # 3 attempts made:
        assert len(adapter.calls) == 3
        # Audit row for the failure:
        assert len(storage.communications) == 1

    async def test_communication_log_row_shape(self):
        """CommunicationLog row contains bounded enum tokens only per NFR-2 -- not the message body."""
        storage = InMemoryMessengerStorage()
        adapter = MockMessengerAdapter()
        cfg = _config()
        service = MessengerService(
            adapter=adapter,
            daily_checker=DailyLimitChecker(storage=storage, config=cfg),
            config=cfg,
        )
        await service.send_escalation(
            item=_item(),
            batch_id="BATCH-abc",
            reason=EscalationReason.POST_REMINDER,
            milestone_ctx=_milestone_ctx(),
        )
        row = storage.communications[0]
        # summary contains bounded enum tokens (channel / reason / size bucket / success bool)
        # NOT the rendered message body:
        assert "Test Owner" not in (row.summary or "")
        assert "MODEL-A" not in (row.summary or "")
        assert "reason=post_reminder" in row.summary
        assert "channel=corp_messenger" in row.summary


# ===========================================================================
# TestMockMessengerAdapter
# ===========================================================================


class TestMockMessengerAdapter:
    async def test_registered_response_overrides_default(self):
        adapter = MockMessengerAdapter()
        adapter.set_default(True)
        adapter.register_response("test_owner_blocked", False)
        assert await adapter.send("test_owner_ok", "hi") is True
        assert await adapter.send("test_owner_blocked", "hi") is False

    async def test_force_exception_raises(self):
        adapter = MockMessengerAdapter()
        adapter.set_force_exception("test_owner_001")
        with pytest.raises(RuntimeError):
            await adapter.send("test_owner_001", "hi")


# ===========================================================================
# TestErrorCodes
# ===========================================================================


class TestErrorCodes:
    def test_all_msg_codes_registered(self):
        expected = {
            "MSG-E001", "MSG-E002", "MSG-E003",
            "MSG-W001", "MSG-W002", "MSG-W003",
        }
        for code in expected:
            assert code in ERROR_CODES, f"{code} not registered"


# ===========================================================================
# TestProtocolContract
# ===========================================================================


class TestProtocolContract:
    def test_adapters_honor_messenger_adapter_protocol(self):
        """Both CorpMessengerAdapter + MockMessengerAdapter satisfy MessengerAdapter."""
        mock = MockMessengerAdapter()
        base = CorpMessengerAdapter(config=_config())
        assert isinstance(mock, MessengerAdapter)
        assert isinstance(base, MessengerAdapter)


# ===========================================================================
# TestUtils
# ===========================================================================


class TestUtils:
    def test_validate_message_length_within(self):
        ok, n = validate_message_length("hello", 100)
        assert ok is True
        assert n == 5

    def test_truncate_message_appends_marker(self):
        msg, original_n = truncate_message("x" * 200, 50)
        assert len(msg.encode("utf-8")) <= 50
        assert "[truncated]" in msg
        assert original_n == 200

    def test_redact_owner_corp_id_masks_value(self):
        assert redact_owner_corp_id("owner_abc123") == "o***"
        assert redact_owner_corp_id("") == "<empty>"


# ===========================================================================
# TestDiagnosticsCli
# ===========================================================================


class TestDiagnosticsCli:
    def test_diagnostic_mode_emits_rpt(self, capsys):
        rc = cli_main(["--diagnostic"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "RPT|MSG|" in captured.out
        assert "template_loaded=true" in captured.out

    def test_mock_mode_emits_met(self, capsys):
        rc = cli_main(["--mock"])
        assert rc == 0
        captured = capsys.readouterr()
        assert "MET|MSG|" in captured.out
        assert "success=true" in captured.out
