"""credential_service tests — protocol, sops service (decrypt patched), mock, CLI, leak checks."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.src.credential_service import (
    Credential,
    MockCredentialService,
    OPS_TEAM_PM_ID,
    SopsCredentialService,
    SystemType,
)
from core.src.credential_service.qc_templates import CREDENTIAL_COMPLETENESS
from core.src.credential_service.service import _build_credential, _parse_env_lines
from core.src.diagnostics import QC_REGISTRY, ReportRecord, ReportType
from core.src.diagnostics.error_codes import ERROR_CODES, PipelineError

SECRET = "s3cr3t-T0KEN-xyz"


def make_cred(system: SystemType = SystemType.ISSUE_TRACKER, **kwargs) -> Credential:
    defaults = dict(
        pm_id=OPS_TEAM_PM_ID,
        system_type=system.value,
        auth_type="api_token",
        api_token=SECRET,
    )
    defaults.update(kwargs)
    return Credential(**defaults)


# ---------------------------------------------------------------------------
# protocol.py
# ---------------------------------------------------------------------------


class TestCredential:
    def test_repr_contains_no_secret_material(self):
        cred = make_cred()
        assert SECRET not in repr(cred)
        assert SECRET not in str(cred)
        assert "pm_id" in repr(cred) and "auth_type" in repr(cred)

    def test_repr_no_secret_for_basic_auth(self):
        cred = make_cred(
            auth_type="basic", api_token=None, username="svc", password=SECRET
        )
        assert SECRET not in repr(cred)

    def test_frozen(self):
        with pytest.raises(Exception):
            make_cred().pm_id = "other"  # type: ignore[misc]

    def test_system_type_has_8_values(self):
        assert len(SystemType) == 8

    @pytest.mark.parametrize(
        "auth_type,carriers,expected",
        [
            ("api_token", {"api_token": "t"}, True),
            ("api_token", {}, False),
            ("basic", {"username": "u", "password": "p"}, True),
            ("basic", {"username": "u"}, False),
            ("ntlm", {"username": "u", "password": "p"}, True),
            ("kerberos", {"keytab_path": Path("/etc/krb.keytab")}, True),
            ("oauth2_bearer", {"bearer": "b"}, True),
            # carrier from a different auth_type set → inconsistent
            ("api_token", {"api_token": "t", "password": "p"}, False),
        ],
    )
    def test_value_carriers_consistent(self, auth_type, carriers, expected):
        cred = Credential(
            pm_id="x", system_type="email", auth_type=auth_type, **carriers
        )
        assert cred.value_carriers_consistent() is expected


# ---------------------------------------------------------------------------
# env parsing + credential building
# ---------------------------------------------------------------------------


class TestEnvParsing:
    def test_parse_env_lines_ignores_comments_and_blanks(self):
        text = "# comment\n\nHILDA_ITR_AUTH_TYPE=api_token\nHILDA_ITR_API_TOKEN='tok'\n"
        entries = _parse_env_lines(text)
        assert entries == {
            "HILDA_ITR_AUTH_TYPE": "api_token",
            "HILDA_ITR_API_TOKEN": "tok",
        }

    def test_build_credential_api_token(self):
        entries = {
            "HILDA_ITR_AUTH_TYPE": "api_token",
            "HILDA_ITR_API_TOKEN": SECRET,
        }
        cred = _build_credential(SystemType.ISSUE_TRACKER, entries, "issue_tracker.enc.env")
        assert cred is not None
        assert cred.api_token == SECRET
        assert cred.pm_id == OPS_TEAM_PM_ID
        assert cred.expires_at is None

    def test_build_credential_empty_file_returns_none(self):
        # Legal for no-auth lab LLM backends per MODULE.md file layout.
        assert _build_credential(SystemType.LLM_OLLAMA_A4000, {}, "llm_ollama_a4000.enc.env") is None

    def test_build_credential_missing_auth_type_raises_e004(self):
        entries = {"HILDA_ITR_API_TOKEN": SECRET}
        with pytest.raises(PipelineError) as exc:
            _build_credential(SystemType.ISSUE_TRACKER, entries, "issue_tracker.enc.env")
        assert exc.value.code_id == "CRD-E004"

    def test_build_credential_missing_carrier_raises_e004(self):
        entries = {
            "HILDA_EML_AUTH_TYPE": "basic",
            "HILDA_EML_USERNAME": "svc",
            # password absent
        }
        with pytest.raises(PipelineError) as exc:
            _build_credential(SystemType.EMAIL, entries, "email.enc.env")
        assert exc.value.code_id == "CRD-E004"

    def test_build_credential_pm_id_and_expires_override(self):
        entries = {
            "HILDA_SHP_AUTH_TYPE": "ntlm",
            "HILDA_SHP_USERNAME": "hilda-svc",
            "HILDA_SHP_PASSWORD": SECRET,
            "HILDA_SHP_PM_ID": "ops-team-emea",
            "HILDA_SHP_EXPIRES_AT": "2027-01-01T00:00:00+00:00",
        }
        cred = _build_credential(SystemType.SHAREPOINT, entries, "sharepoint.enc.env")
        assert cred is not None
        assert cred.pm_id == "ops-team-emea"
        assert cred.expires_at == datetime(2027, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# SopsCredentialService (decrypt step patched — no sops binary needed)
# ---------------------------------------------------------------------------


def patched_service(tmp_path: Path, contents: dict[str, str]) -> SopsCredentialService:
    """Service over tmp .enc.env files whose 'decryption' returns the given text."""
    env_dir = tmp_path / "credentials"
    env_dir.mkdir(exist_ok=True)
    for filename, _ in contents.items():
        (env_dir / filename).write_text("ENC[AES256_GCM,...]")  # ciphertext placeholder
    service = SopsCredentialService(env_dir=env_dir, age_key_path=tmp_path / "age.key")

    async def fake_decrypt(path: Path) -> str:
        return contents[path.name]

    service._decrypt_file = fake_decrypt  # type: ignore[method-assign]
    return service


ITR_ENV = f"HILDA_ITR_AUTH_TYPE=api_token\nHILDA_ITR_API_TOKEN={SECRET}\n"


class TestSopsCredentialService:
    def test_get_credential_exact_match(self, tmp_path):
        service = patched_service(tmp_path, {"issue_tracker.enc.env": ITR_ENV})
        cred = asyncio.run(service.get_credential(OPS_TEAM_PM_ID, "issue_tracker"))
        assert cred.api_token == SECRET

    def test_pm_id_fallback_returns_ops_team_credential(self, tmp_path):
        # Ph-1/Ph-2: same shared credential regardless of pm_id per [D-019].
        service = patched_service(tmp_path, {"issue_tracker.enc.env": ITR_ENV})
        cred = asyncio.run(service.get_credential("pm-alice", "issue_tracker"))
        assert cred.api_token == SECRET
        assert cred.pm_id == OPS_TEAM_PM_ID  # attribution stays ops-team

    def test_unknown_system_type_raises_e003(self, tmp_path):
        service = patched_service(tmp_path, {})
        with pytest.raises(PipelineError) as exc:
            asyncio.run(service.get_credential("x", "not_a_system"))
        assert exc.value.code_id == "CRD-E003"

    def test_no_credential_raises_e001(self, tmp_path):
        service = patched_service(tmp_path, {})
        with pytest.raises(PipelineError) as exc:
            asyncio.run(service.get_credential("x", "email"))
        assert exc.value.code_id == "CRD-E001"

    def test_load_is_idempotent_and_counts(self, tmp_path):
        service = patched_service(tmp_path, {"issue_tracker.enc.env": ITR_ENV})

        async def run() -> tuple[int, int]:
            return await service.load(), await service.load()

        first, second = asyncio.run(run())
        assert first == second == 1

    def test_reload_picks_up_rotated_credential(self, tmp_path):
        contents = {"issue_tracker.enc.env": ITR_ENV}
        service = patched_service(tmp_path, contents)

        async def run() -> tuple[str | None, str | None]:
            before = (await service.get_credential("x", "issue_tracker")).api_token
            contents["issue_tracker.enc.env"] = (
                "HILDA_ITR_AUTH_TYPE=api_token\nHILDA_ITR_API_TOKEN=rotated\n"
            )
            # No hot-reload: cache still serves the old value until reload().
            mid = (await service.get_credential("x", "issue_tracker")).api_token
            await service.reload()
            after = (await service.get_credential("x", "issue_tracker")).api_token
            return before == SECRET and mid == SECRET, after

        unchanged_until_reload, after = asyncio.run(run())
        assert unchanged_until_reload
        assert after == "rotated"

    def test_decrypt_failure_raises_e002(self, tmp_path):
        env_dir = tmp_path / "credentials"
        env_dir.mkdir()
        (env_dir / "email.enc.env").write_text("ENC[...]")
        service = SopsCredentialService(env_dir=env_dir, age_key_path=tmp_path / "age.key")

        async def failing_decrypt(path: Path) -> str:
            raise PipelineError("CRD-E002", context={"file": path.name, "reason": "age key missing"})

        service._decrypt_file = failing_decrypt  # type: ignore[method-assign]
        with pytest.raises(PipelineError) as exc:
            asyncio.run(service.get_credential("x", "email"))
        assert exc.value.code_id == "CRD-E002"

    def test_install_sighup_handler_portable(self, tmp_path):
        # Installs on POSIX; returns False (never raises) where SIGHUP or
        # add_signal_handler is unavailable (Windows / Proactor loop).
        service = patched_service(tmp_path, {})

        async def run() -> bool:
            return service.install_sighup_handler()

        import signal

        installed = asyncio.run(run())
        assert installed is hasattr(signal, "SIGHUP")

    def test_no_plaintext_written_to_disk(self, tmp_path):
        service = patched_service(tmp_path, {"issue_tracker.enc.env": ITR_ENV})
        asyncio.run(service.get_credential("x", "issue_tracker"))
        files = list((tmp_path / "credentials").iterdir())
        assert [f.name for f in files] == ["issue_tracker.enc.env"]
        assert SECRET not in (tmp_path / "credentials" / "issue_tracker.enc.env").read_text()


# ---------------------------------------------------------------------------
# MockCredentialService
# ---------------------------------------------------------------------------


class TestMockCredentialService:
    def test_register_and_get(self):
        mock = MockCredentialService()
        mock.register(make_cred())
        cred = asyncio.run(mock.get_credential(OPS_TEAM_PM_ID, "issue_tracker"))
        assert cred.api_token == SECRET

    def test_unknown_raises_e001(self):
        mock = MockCredentialService()
        with pytest.raises(PipelineError) as exc:
            asyncio.run(mock.get_credential("nobody", "email"))
        assert exc.value.code_id == "CRD-E001"

    def test_unknown_system_raises_e003(self):
        mock = MockCredentialService()
        with pytest.raises(PipelineError) as exc:
            asyncio.run(mock.get_credential("x", "vault"))
        assert exc.value.code_id == "CRD-E003"

    def test_with_all_system_types_covers_enum(self):
        mock = MockCredentialService.with_all_system_types()
        for system in SystemType:
            cred = asyncio.run(mock.get_credential(OPS_TEAM_PM_ID, system.value))
            assert cred.system_type == system.value


# ---------------------------------------------------------------------------
# Error codes + QC template registration
# ---------------------------------------------------------------------------


class TestRegistrations:
    def test_all_crd_codes_registered(self):
        for code, recoverable in [
            ("CRD-E001", False),
            ("CRD-E002", False),
            ("CRD-E003", False),
            ("CRD-E004", False),
            ("CRD-W001", True),
            ("CRD-W002", True),
        ]:
            assert code in ERROR_CODES, code
            assert ERROR_CODES[code].recoverable is recoverable

    def test_qc_template_registered(self):
        assert "CRD:credential_completeness" in QC_REGISTRY

    def test_qc_template_validates_good_record(self):
        record = ReportRecord(
            ReportType.QC,
            "CRD",
            "run-test",
            datetime.now(timezone.utc),
            {
                "present": True,
                "auth_type": "api_token",
                "value_carriers_consistent": True,
                "result": "OK",
            },
        )
        assert CREDENTIAL_COMPLETENESS.validate_record(record) == []

    def test_qc_template_rejects_free_text(self):
        record = ReportRecord(
            ReportType.QC,
            "CRD",
            "run-test",
            datetime.now(timezone.utc),
            {
                "present": True,
                "auth_type": "some made-up prose",
                "value_carriers_consistent": True,
                "result": "OK",
            },
        )
        assert CREDENTIAL_COMPLETENESS.validate_record(record)


# ---------------------------------------------------------------------------
# CLI (in-process, decrypt patched) — leak negative tests
# ---------------------------------------------------------------------------


class TestCli:
    def test_diagnostic_counts_and_no_secret_leak(self, tmp_path, capsys, monkeypatch):
        from core.src.credential_service import credential_service_cli as cli

        env_dir = tmp_path / "credentials"
        env_dir.mkdir()
        (env_dir / "issue_tracker.enc.env").write_text("ENC[...]")
        (env_dir / "email.enc.env").write_text("ENC[...]")

        contents = {
            "issue_tracker.enc.env": ITR_ENV,
            "email.enc.env": "HILDA_EML_AUTH_TYPE=basic\nHILDA_EML_USERNAME=svc\n",  # malformed
        }

        async def fake_decrypt(self, path: Path) -> str:
            return contents[path.name]

        monkeypatch.setattr(SopsCredentialService, "_decrypt_file", fake_decrypt)
        code = asyncio.run(cli._cmd_diagnostic("run-test", env_dir, tmp_path / "age.key"))
        out = capsys.readouterr().out
        assert "RPT|CRD|run-test" in out
        assert "files_found=2" in out
        assert "files_decrypted=2" in out
        assert "files_failed=1" in out
        assert "systems_covered=1" in out
        assert SECRET not in out
        assert code == 1  # malformed file present

    def test_mock_round_trip(self, capsys):
        from core.src.credential_service import credential_service_cli as cli

        code = asyncio.run(cli._cmd_mock("run-test"))
        out = capsys.readouterr().out
        assert "systems_registered=8" in out
        assert "lookups_ok=8" in out
        assert code == 0

    def test_validate_ok_and_qc_record_passes_template(self, tmp_path, capsys, monkeypatch):
        from core.src.credential_service import credential_service_cli as cli

        env_dir = tmp_path / "credentials"
        env_dir.mkdir()
        (env_dir / "issue_tracker.enc.env").write_text("ENC[...]")

        async def fake_decrypt(self, path: Path) -> str:
            return ITR_ENV

        monkeypatch.setattr(SopsCredentialService, "_decrypt_file", fake_decrypt)
        code = asyncio.run(cli._cmd_validate("run-test", "issue_tracker", env_dir, tmp_path / "age.key"))
        out = capsys.readouterr().out
        assert "QC|CRD|run-test" in out
        assert "present=true" in out.lower()
        assert "auth_type=api_token" in out
        assert "result=OK" in out
        assert SECRET not in out
        assert code == 0
        # round-trip the emitted line through the QC template
        line = next(l for l in out.splitlines() if l.startswith("QC|CRD"))
        record = ReportRecord.from_line(line)
        assert CREDENTIAL_COMPLETENESS.validate_record(record) == []

    def test_validate_missing_file_fails_without_free_text(self, tmp_path, capsys):
        from core.src.credential_service import credential_service_cli as cli

        env_dir = tmp_path / "credentials"
        env_dir.mkdir()
        code = asyncio.run(cli._cmd_validate("run-test", "email", env_dir, tmp_path / "age.key"))
        out = capsys.readouterr().out
        assert "present=false" in out.lower()
        assert "auth_type=none" in out
        assert "result=FAIL" in out
        assert code == 1

    def test_validate_unknown_system_raises_e003(self, tmp_path):
        from core.src.credential_service import credential_service_cli as cli

        with pytest.raises(PipelineError) as exc:
            asyncio.run(cli._cmd_validate("run-test", "vault", tmp_path, tmp_path / "age.key"))
        assert exc.value.code_id == "CRD-E003"
