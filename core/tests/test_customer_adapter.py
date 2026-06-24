"""customer_adapter test suite -- protocol + CarrierUploadResult + TOTP +
GoogleDriveBaseAdapter + MockCustomerAdapter + diagnostics_cli + config.

Per [D-116] Ratified 2026-06-25. Tests use FakeCredentialService + a mocked
AuditWriter; the binding is mocked via a test subclass that overrides
`_invoke_binding`. No real network / pyotp / NTP probe.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.src.credential_service.protocol import Credential, SystemType
from core.src.customer_adapter import (
    AuditWriter,
    CarrierUploadResult,
    CustomerAdapter,
    CustomerAdapterConfig,
    GoogleDriveBaseAdapter,
    MockCustomerAdapter,
    current_totp,
    ntp_skew_seconds,
)
from core.src.customer_adapter.diagnostics_cli import (
    _cmd_diagnostic,
    _count_customer_subclasses,
    _run_mock,
)
from core.src.diagnostics.error_codes import (
    ERROR_CODES,
    PREFIX_REGISTRY,
    PipelineError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


# Valid base32 seed for testing pyotp (standard "JBSWY3DPEHPK3PXP" = "Hello!")
_TEST_SEED = "JBSWY3DPEHPK3PXP"


class FakeCredentialService:
    """Async credential_service stub matching CredentialService Protocol."""

    def __init__(self, cred: Credential | None = None, raise_on_lookup: bool = False) -> None:
        self._cred = cred
        self._raise = raise_on_lookup
        self.calls: list[tuple[str, str, str | None]] = []

    async def get_credential(
        self,
        pm_id: str,
        system_type: str,
        customer_id: str | None = None,
    ) -> Credential:
        self.calls.append((pm_id, system_type, customer_id))
        if self._raise:
            raise PipelineError(
                "CRD-E001",
                context={"pm_id": pm_id, "system": system_type, "customer_id": customer_id or ""},
            )
        assert self._cred is not None
        return self._cred


class FakeAuditWriter:
    """Captures CommunicationLog rows for assertion."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.raise_on_write = False

    def write_communication_log(
        self,
        action_type: str,
        delivery_item_id: str | None,
        attribution: dict[str, str],
        details: dict[str, Any],
    ) -> None:
        if self.raise_on_write:
            raise RuntimeError("audit writer down")
        self.rows.append({
            "action_type":      action_type,
            "delivery_item_id": delivery_item_id,
            "attribution":      dict(attribution),
            "details":          dict(details),
        })


def _make_cred(seed: str = _TEST_SEED) -> Credential:
    return Credential(
        pm_id="test_pm",
        system_type=SystemType.CUSTOMER.value,
        auth_type="basic_totp",
        username="test_user",
        password="test_pass",
        totp_seed=seed,
    )


class _SuccessAdapter(GoogleDriveBaseAdapter):
    """Test subclass: binding returns True (success)."""

    source_system: str = "test_customer"
    customer_id: str = "test_customer"
    pm_id: str = "test_pm"

    def __init__(self, *a: Any, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self.invoke_calls: list[dict[str, Any]] = []

    async def _invoke_binding(  # type: ignore[override]
        self, *, device_id: str, milestone_name: str, source_dir: Path,
        target_dir: str, filename: str, pm_id: str, pm_password: str,
        totp_code: str, customer_delivery_info: str,
    ) -> bool:
        self.invoke_calls.append({
            "device_id":      device_id,
            "milestone_name": milestone_name,
            "source_dir":     str(source_dir),
            "target_dir":     target_dir,
            "filename":       filename,
            "pm_id":          pm_id,
            "customer_delivery_info": customer_delivery_info,
            # Intentionally NOT capturing pm_password / totp_code in fields we
            # might dump; but the test still verifies their shape via separate
            # local assertions in tests.
            "pm_password_len": len(pm_password),
            "totp_code_len":   len(totp_code),
        })
        return True


class _PostVerifyFailedAdapter(_SuccessAdapter):
    async def _invoke_binding(self, **kw: Any) -> bool:  # type: ignore[override]
        return False


class _RaisesAdapter(_SuccessAdapter):
    def __init__(self, *a: Any, exc: BaseException, **kw: Any) -> None:
        super().__init__(*a, **kw)
        self._exc = exc

    async def _invoke_binding(self, **kw: Any) -> bool:  # type: ignore[override]
        raise self._exc


@pytest.fixture
def cfg() -> CustomerAdapterConfig:
    # diagnostic_ntp_check=False to keep tests fast + offline.
    return CustomerAdapterConfig(diagnostic_ntp_check=False)


@pytest.fixture
def fake_creds() -> FakeCredentialService:
    return FakeCredentialService(cred=_make_cred())


@pytest.fixture
def audit() -> FakeAuditWriter:
    return FakeAuditWriter()


# ---------------------------------------------------------------------------
# TestCarrierUploadResult
# ---------------------------------------------------------------------------


class TestCarrierUploadResult:
    def test_frozen_dataclass(self) -> None:
        now = datetime.now(timezone.utc)
        result = CarrierUploadResult(
            success=True,
            uploaded_filename="abc.report",
            device_id="MODEL-A",
            milestone_name="P1",
            target_dir="TestReports/Power",
            upload_started_at=now,
            upload_completed_at=now,
        )
        with pytest.raises(Exception):
            result.success = False  # type: ignore[misc]
        # Ph-2 fields default to None per [D-116] D16
        assert result.carrier_file_id is None
        assert result.carrier_file_url is None

    def test_error_path_fields(self) -> None:
        now = datetime.now(timezone.utc)
        result = CarrierUploadResult(
            success=False,
            uploaded_filename="x.pdf",
            device_id="MODEL-A",
            milestone_name="P1",
            target_dir="X/Y",
            upload_started_at=now,
            upload_completed_at=now,
            error_code="CAD-E004",
            error_detail="binding_failure",
        )
        assert result.error_code == "CAD-E004"
        assert result.error_detail == "binding_failure"


# ---------------------------------------------------------------------------
# TestTotpGeneration
# ---------------------------------------------------------------------------


class TestTotpGeneration:
    def test_current_totp_returns_6_digits(self) -> None:
        code = current_totp(_TEST_SEED)
        assert code.isdigit()
        assert len(code) == 6

    def test_current_totp_deterministic_within_window(self) -> None:
        # Same seed called twice within ~30s window returns same code.
        c1 = current_totp(_TEST_SEED)
        c2 = current_totp(_TEST_SEED)
        assert c1 == c2

    def test_ntp_skew_seconds_returns_none_on_unresolvable_host(self) -> None:
        # Resolves DNS lookup failure to None silently per spec.
        skew = ntp_skew_seconds(ntp_host="does-not-exist.invalid", timeout=0.5)
        assert skew is None


# ---------------------------------------------------------------------------
# TestGoogleDriveBaseAdapter
# ---------------------------------------------------------------------------


class TestGoogleDriveBaseAdapter:
    @pytest.mark.asyncio
    async def test_happy_path_returns_success_result(
        self, cfg: CustomerAdapterConfig, fake_creds: FakeCredentialService,
        audit: FakeAuditWriter,
    ) -> None:
        adapter = _SuccessAdapter(cfg, fake_creds, audit)
        result = await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="TestReports/Power",
            filename="abc.report",
            customer_delivery_info="drive.google.com",
        )
        assert result.success is True
        assert result.error_code is None
        assert result.uploaded_filename == "abc.report"
        assert result.device_id == "MODEL-A"
        assert result.milestone_name == "P1"
        assert result.target_dir == "TestReports/Power"
        # Credential resolve happened with correct routing tuple
        assert fake_creds.calls == [("test_pm", SystemType.CUSTOMER.value, "test_customer")]
        # Audit row emitted with bounded fields only
        assert len(audit.rows) == 1
        row = audit.rows[0]
        assert row["action_type"] == "carrier_upload"
        assert row["details"]["success"] is True
        # NFR-2: credential material must NOT appear in audit details
        flat = str(row).lower()
        assert "test_pass" not in flat
        assert _TEST_SEED.lower() not in flat
        # invoke_binding got the right 8-arg shape
        assert len(adapter.invoke_calls) == 1
        call = adapter.invoke_calls[0]
        assert call["pm_id"] == "test_user"           # cred.username
        assert call["pm_password_len"] == len("test_pass")
        assert call["totp_code_len"] == 6

    @pytest.mark.asyncio
    async def test_post_verify_failed_returns_cad_e005(
        self, cfg: CustomerAdapterConfig, fake_creds: FakeCredentialService,
        audit: FakeAuditWriter,
    ) -> None:
        adapter = _PostVerifyFailedAdapter(cfg, fake_creds, audit)
        result = await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="X", filename="x.pdf",
            customer_delivery_info="drive.google.com",
        )
        assert result.success is False
        assert result.error_code == "CAD-E005"
        assert result.error_detail == "post_verify_failed"

    @pytest.mark.asyncio
    async def test_credential_not_retrievable_returns_cad_e008(
        self, cfg: CustomerAdapterConfig, audit: FakeAuditWriter,
    ) -> None:
        creds = FakeCredentialService(raise_on_lookup=True)
        adapter = _SuccessAdapter(cfg, creds, audit)
        result = await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="X", filename="x.pdf",
            customer_delivery_info="drive.google.com",
        )
        assert result.success is False
        assert result.error_code == "CAD-E008"
        # _invoke_binding was NOT called (we never got past credential lookup).
        assert adapter.invoke_calls == []

    @pytest.mark.asyncio
    async def test_binding_raises_generic_exception_returns_cad_e004(
        self, cfg: CustomerAdapterConfig, fake_creds: FakeCredentialService,
        audit: FakeAuditWriter,
    ) -> None:
        adapter = _RaisesAdapter(cfg, fake_creds, audit, exc=RuntimeError("selenium-internal-detail"))
        result = await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="X", filename="x.pdf",
            customer_delivery_info="drive.google.com",
        )
        assert result.success is False
        assert result.error_code == "CAD-E004"
        # NFR-2: binding's internal error message must NOT leak via error_detail.
        assert result.error_detail == "binding_failure"
        assert "selenium-internal-detail" not in (result.error_detail or "")

    @pytest.mark.asyncio
    async def test_binding_timeout_returns_cad_e005(
        self, cfg: CustomerAdapterConfig, fake_creds: FakeCredentialService,
        audit: FakeAuditWriter,
    ) -> None:
        adapter = _RaisesAdapter(cfg, fake_creds, audit, exc=TimeoutError())
        result = await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="X", filename="x.pdf",
            customer_delivery_info="drive.google.com",
        )
        assert result.success is False
        assert result.error_code == "CAD-E005"
        assert result.error_detail == "binding_timeout"

    @pytest.mark.asyncio
    async def test_not_implemented_subclass_returns_cad_e009(
        self, cfg: CustomerAdapterConfig, fake_creds: FakeCredentialService,
        audit: FakeAuditWriter,
    ) -> None:
        # Use the abstract base class directly (no _invoke_binding override).
        class _BaseClassDirect(GoogleDriveBaseAdapter):
            source_system = "test_customer"
            customer_id = "test_customer"
            pm_id = "test_pm"

        adapter = _BaseClassDirect(cfg, fake_creds, audit)
        result = await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="X", filename="x.pdf",
            customer_delivery_info="drive.google.com",
        )
        assert result.success is False
        assert result.error_code == "CAD-E009"

    @pytest.mark.asyncio
    async def test_audit_writer_optional_none_skips_log(
        self, cfg: CustomerAdapterConfig, fake_creds: FakeCredentialService,
    ) -> None:
        # No audit_writer injected -- adapter still works.
        adapter = _SuccessAdapter(cfg, fake_creds, audit_writer=None)
        result = await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="X", filename="x.pdf",
            customer_delivery_info="drive.google.com",
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_audit_writer_raise_does_not_break_upload(
        self, cfg: CustomerAdapterConfig, fake_creds: FakeCredentialService,
    ) -> None:
        audit = FakeAuditWriter()
        audit.raise_on_write = True
        adapter = _SuccessAdapter(cfg, fake_creds, audit)
        # Should not raise — audit emission is best-effort
        result = await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="X", filename="x.pdf",
            customer_delivery_info="drive.google.com",
        )
        assert result.success is True

    @pytest.mark.asyncio
    async def test_credential_wrong_auth_type_returns_cad_e008(
        self, cfg: CustomerAdapterConfig, audit: FakeAuditWriter,
    ) -> None:
        # auth_type="basic" instead of "basic_totp" -- missing totp_seed
        bad_cred = Credential(
            pm_id="test_pm",
            system_type=SystemType.CUSTOMER.value,
            auth_type="basic",
            username="u", password="p",
        )
        creds = FakeCredentialService(cred=bad_cred)
        adapter = _SuccessAdapter(cfg, creds, audit)
        result = await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="X", filename="x.pdf",
            customer_delivery_info="drive.google.com",
        )
        assert result.success is False
        assert result.error_code == "CAD-E008"
        assert result.error_detail == "auth_type_mismatch"

    @pytest.mark.asyncio
    async def test_health_returns_expected_shape(
        self, cfg: CustomerAdapterConfig, fake_creds: FakeCredentialService,
        audit: FakeAuditWriter,
    ) -> None:
        adapter = _SuccessAdapter(cfg, fake_creds, audit)
        health = await adapter.health()
        assert health["ready"] is True
        assert health["customer_id"] == "test_customer"
        # diagnostic_ntp_check=False so skew is None.
        assert health["ntp_skew_s"] is None


# ---------------------------------------------------------------------------
# TestMockCustomerAdapter
# ---------------------------------------------------------------------------


class TestMockCustomerAdapter:
    @pytest.mark.asyncio
    async def test_registered_tuple_returns_canned_result(self) -> None:
        adapter = MockCustomerAdapter()
        now = datetime.now(timezone.utc)
        canned = CarrierUploadResult(
            success=True, uploaded_filename="abc.report",
            device_id="MODEL-A", milestone_name="P1", target_dir="X",
            upload_started_at=now, upload_completed_at=now,
        )
        adapter.register_upload_result("MODEL-A", "P1", "X", "abc.report", canned)
        result = await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="X", filename="abc.report",
            customer_delivery_info="drive.google.com",
        )
        assert result is canned

    @pytest.mark.asyncio
    async def test_unregistered_tuple_returns_default_failure(self) -> None:
        adapter = MockCustomerAdapter()
        result = await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="X", filename="unknown.pdf",
            customer_delivery_info="drive.google.com",
        )
        assert result.success is False
        assert result.error_code == "CAD-E004"

    @pytest.mark.asyncio
    async def test_unregistered_with_default_success_true(self) -> None:
        adapter = MockCustomerAdapter()
        adapter.set_default_success(True)
        result = await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="X", filename="unknown.pdf",
            customer_delivery_info="drive.google.com",
        )
        assert result.success is True
        assert result.error_code is None

    @pytest.mark.asyncio
    async def test_calls_log_captures_tuples(self) -> None:
        adapter = MockCustomerAdapter()
        await adapter.upload_attachment(
            device_id="MODEL-A", milestone_name="P1",
            source_dir=Path("/tmp"), target_dir="X", filename="a.pdf",
            customer_delivery_info="drive.google.com",
        )
        await adapter.upload_attachment(
            device_id="MODEL-B", milestone_name="P2",
            source_dir=Path("/tmp"), target_dir="Y", filename="b.pdf",
            customer_delivery_info="drive.google.com",
        )
        assert adapter.calls == [
            ("MODEL-A", "P1", "X", "a.pdf"),
            ("MODEL-B", "P2", "Y", "b.pdf"),
        ]

    @pytest.mark.asyncio
    async def test_health_ready(self) -> None:
        adapter = MockCustomerAdapter()
        health = await adapter.health()
        assert health["ready"] is True
        assert health["customer_id"] == "mock_customer"


# ---------------------------------------------------------------------------
# TestErrorCodes
# ---------------------------------------------------------------------------


class TestErrorCodes:
    def test_cad_prefix_registered(self) -> None:
        assert PREFIX_REGISTRY["CAD"] == "customer_adapter"

    def test_all_cad_codes_present(self) -> None:
        expected = {
            "CAD-E001", "CAD-E002", "CAD-E003", "CAD-E004", "CAD-E005",
            "CAD-E006", "CAD-E007", "CAD-E008", "CAD-E009", "CAD-E010",
            "CAD-W001", "CAD-W002", "CAD-W003", "CAD-W004", "CAD-W005",
        }
        for code in expected:
            assert code in ERROR_CODES, f"Missing CAD code: {code}"


# ---------------------------------------------------------------------------
# TestConfig
# ---------------------------------------------------------------------------


class TestConfig:
    def test_defaults(self) -> None:
        cfg = CustomerAdapterConfig()
        assert cfg.ntp_skew_warn_s == 25.0
        assert cfg.diagnostic_ntp_check is True
        assert cfg.customizations_dir == Path("/etc/hilda/customizations/customer_adapter")

    def test_overrides(self) -> None:
        cfg = CustomerAdapterConfig(
            ntp_skew_warn_s=10.0,
            diagnostic_ntp_check=False,
            customizations_dir=Path("/x/y"),
        )
        assert cfg.ntp_skew_warn_s == 10.0
        assert cfg.diagnostic_ntp_check is False


# ---------------------------------------------------------------------------
# TestProtocolContract
# ---------------------------------------------------------------------------


class TestProtocolContract:
    def test_mock_adapter_honors_protocol(self) -> None:
        adapter = MockCustomerAdapter()
        # runtime_checkable Protocol -- structural check via isinstance.
        assert isinstance(adapter, CustomerAdapter)

    def test_google_drive_base_honors_protocol(
        self, cfg: CustomerAdapterConfig,
    ) -> None:
        adapter = _SuccessAdapter(cfg, FakeCredentialService(cred=_make_cred()))
        assert isinstance(adapter, CustomerAdapter)


# ---------------------------------------------------------------------------
# TestDiagnosticsCli
# ---------------------------------------------------------------------------


class TestDiagnosticsCli:
    def test_diagnostic_emits_rpt_and_returns_zero(
        self, cfg: CustomerAdapterConfig, capsys: pytest.CaptureFixture,
    ) -> None:
        code = _cmd_diagnostic("run-test01", cfg)
        captured = capsys.readouterr()
        assert code == 0
        # CAD-RPT line emitted
        assert "RPT|CAD|run-test01|" in captured.out
        assert "pyotp_available=true" in captured.out
        assert "binding_strategy=thin_wrapper" in captured.out

    def test_count_customer_subclasses_missing_dir(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        assert _count_customer_subclasses(missing) == 0

    def test_count_customer_subclasses_counts_adapter_files(
        self, tmp_path: Path,
    ) -> None:
        (tmp_path / "carrier_alpha_adapter.py").write_text("# stub")
        (tmp_path / "carrier_beta_adapter.py").write_text("# stub")
        (tmp_path / "__init__.py").write_text("")
        (tmp_path / "_private_adapter.py").write_text("# hidden")
        (tmp_path / "unrelated.py").write_text("# not an adapter")
        count = _count_customer_subclasses(tmp_path)
        assert count == 2

    def test_run_mock_emits_rpt_success(
        self, capsys: pytest.CaptureFixture,
    ) -> None:
        rc = asyncio.run(_run_mock("run-test02"))
        out = capsys.readouterr().out
        assert rc == 0
        assert "RPT|CAD|run-test02|" in out
        assert "mode=mock" in out
        assert "success=true" in out
