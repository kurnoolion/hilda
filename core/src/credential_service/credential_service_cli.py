"""credential_service CLI: --diagnostic, --mock, --validate --system <type>.

No --dry-run — credential_service has no write surface, so dry-run is a no-op.
Emits no credential values anywhere (NFR-2 / [D-002]).
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from core.src.diagnostics import ReportRecord, ReportType, ReportWriter
from core.src.diagnostics.error_codes import PipelineError
from core.src.credential_service.mock_service import MockCredentialService
from core.src.credential_service.protocol import (
    SYSTEM_CRED_SCOPE,
    SYSTEM_SUBTREE,
    CredentialScope,
    SystemType,
)
from core.src.credential_service.service import (
    OPS_TEAM_PM_ID,
    SopsCredentialService,
    _build_credential,
    _parse_env_lines,
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def _cmd_diagnostic(run_id: str, env_dir: Path, age_key: Path) -> int:
    """Decrypt every .enc.env, validate required vars per SystemType. No values emitted."""
    writer = ReportWriter("CRD", run_id)
    service = SopsCredentialService(env_dir=env_dir, age_key_path=age_key)

    files_found = 0
    files_decrypted = 0
    files_failed = 0
    systems_covered = 0

    for system in SystemType:
        path = env_dir / f"{system.value}.enc.env"
        if not path.exists():
            continue
        files_found += 1
        try:
            plaintext = await service._decrypt_file(path)
            files_decrypted += 1
            credential = _build_credential(system, _parse_env_lines(plaintext), path.name)
            if credential is not None:
                systems_covered += 1
        except PipelineError:
            files_failed += 1

    writer.emit(
        ReportRecord(
            ReportType.RPT,
            "CRD",
            run_id,
            _now(),
            {
                "files_found": files_found,
                "files_decrypted": files_decrypted,
                "files_failed": files_failed,
                "systems_covered": systems_covered,
            },
        )
    )
    writer.flush()
    return 0 if files_failed == 0 else 1


async def _cmd_mock(run_id: str) -> int:
    """Spin up MockCredentialService with synthetic credentials for every SystemType."""
    writer = ReportWriter("CRD", run_id)
    mock = MockCredentialService.with_all_system_types()

    lookups_ok = 0
    for system in SystemType:
        try:
            await mock.get_credential(OPS_TEAM_PM_ID, system.value)
            lookups_ok += 1
        except PipelineError:
            pass

    writer.emit(
        ReportRecord(
            ReportType.RPT,
            "CRD",
            run_id,
            _now(),
            {
                "adapter": "mock",
                "systems_registered": len(SystemType),
                "lookups_ok": lookups_ok,
                "lookups_fail": len(SystemType) - lookups_ok,
            },
        )
    )
    writer.flush()
    return 0 if lookups_ok == len(SystemType) else 1


def _resolve_validate_path(
    system: SystemType,
    env_dir: Path,
    account_id: str | None,
    customer_id: str | None,
) -> Path | None:
    """Resolve the credential file path for the given system + scope.

    Returns None for NO_CREDENTIAL scope (validation is a no-op there).
    Raises ValueError when required scope args are missing.
    """
    scope = SYSTEM_CRED_SCOPE.get(system, CredentialScope.SHARED)
    if scope == CredentialScope.NO_CREDENTIAL:
        return None
    if scope == CredentialScope.SHARED:
        return env_dir / f"{system.value}.enc.env"
    subtree = SYSTEM_SUBTREE.get(system)
    if subtree is None:
        return None
    if scope == CredentialScope.PER_CUSTOMER:
        if customer_id is None:
            raise ValueError("--customer-id required for per-customer system")
        return env_dir / subtree / f"{customer_id}.enc.env"
    if scope == CredentialScope.PER_ACCOUNT_PER_CUSTOMER:
        if account_id is None or customer_id is None:
            raise ValueError(
                "--account-id and --customer-id required for per-(account,customer) system"
            )
        return env_dir / subtree / account_id / f"{customer_id}.enc.env"
    return None


async def _cmd_validate(
    run_id: str,
    system_arg: str,
    env_dir: Path,
    age_key: Path,
    account_id: str | None = None,
    customer_id: str | None = None,
) -> int:
    """Structural completeness check for one system's credential. Value never printed.

    Scope-aware per FR-25 (b) + FR-19/77 + arch lock 2026-06-21:
    - SHARED: resolves env_dir/<system>.enc.env
    - PER_CUSTOMER: requires --customer-id; resolves env_dir/<subtree>/<customer_id>.enc.env
    - PER_ACCOUNT_PER_CUSTOMER: requires --account-id + --customer-id;
      resolves env_dir/<subtree>/<account_id>/<customer_id>.enc.env
    - NO_CREDENTIAL: validates that NO file exists (presence is a config error)
    """
    writer = ReportWriter("CRD", run_id)
    try:
        system = SystemType(system_arg)
    except ValueError:
        raise PipelineError("CRD-E003", context={"system": system_arg})

    service = SopsCredentialService(env_dir=env_dir, age_key_path=age_key)
    scope = SYSTEM_CRED_SCOPE.get(system, CredentialScope.SHARED)

    present = False
    auth_type = "none"
    consistent = False
    result = "FAIL"

    if scope == CredentialScope.NO_CREDENTIAL:
        # NO_CREDENTIAL: validation succeeds iff no credential file exists for this system.
        # Any file present is a config error (would violate FR-25 (a) pattern (d)).
        subtree = SYSTEM_SUBTREE.get(system)
        present_files = []
        if subtree is not None:
            subtree_root = env_dir / subtree
            if subtree_root.exists():
                present_files = list(subtree_root.rglob("*.enc.env"))
        result = "OK" if not present_files else "FAIL"
    else:
        try:
            path = _resolve_validate_path(system, env_dir, account_id, customer_id)
        except ValueError:
            result = "FAIL"
            path = None

        if path is not None and path.exists():
            try:
                plaintext = await service._decrypt_file(path)
                credential = _build_credential(system, _parse_env_lines(plaintext), path.name)
                if credential is not None:
                    present = True
                    auth_type = credential.auth_type
                    consistent = credential.value_carriers_consistent()
                    result = "OK" if consistent else "WARN"
            except PipelineError:
                result = "FAIL"

    writer.emit(
        ReportRecord(
            ReportType.QC,
            "CRD",
            run_id,
            _now(),
            {
                "system": system.value,
                "scope": scope.value,
                "present": present,
                "auth_type": auth_type,
                "value_carriers_consistent": consistent,
                "result": result,
            },
        )
    )
    writer.flush()
    return 0 if result == "OK" else 1


def main() -> None:
    parser = argparse.ArgumentParser(
        description="credential_service CLI — diagnostic, mock surface, per-system validation"
    )
    parser.add_argument("--diagnostic", action="store_true", help="Decrypt + validate all .enc.env files; emit CRD-RPT")
    parser.add_argument("--mock", action="store_true", help="MockCredentialService round-trip for every SystemType")
    parser.add_argument("--validate", action="store_true", help="Structural completeness QC for one system")
    parser.add_argument("--system", default=None, help="SystemType value for --validate")
    parser.add_argument(
        "--account-id", default=None,
        help="Account id for per-(account,customer) systems (e.g. customer JIRA); required when --system is per-(account,customer) scope"
    )
    parser.add_argument(
        "--customer-id", default=None,
        help="Customer id for per-customer systems (e.g. customer Google Drive); required when --system is per-customer or per-(account,customer) scope"
    )
    parser.add_argument("--env-dir", default="/etc/hilda/credentials", help="Directory of .enc.env files")
    parser.add_argument("--age-key", default="/etc/hilda/age.key", help="Path to age private key")
    parser.add_argument("--run-id", default=None, help="Stable run identifier (default: auto)")

    args = parser.parse_args()
    run_id = args.run_id or f"run-{uuid.uuid4().hex[:8]}"
    env_dir = Path(args.env_dir)
    age_key = Path(args.age_key)

    if args.diagnostic:
        code = asyncio.run(_cmd_diagnostic(run_id, env_dir, age_key))
    elif args.mock:
        code = asyncio.run(_cmd_mock(run_id))
    elif args.validate:
        if not args.system:
            parser.error("--validate requires --system <type>")
        code = asyncio.run(
            _cmd_validate(
                run_id,
                args.system,
                env_dir,
                age_key,
                account_id=args.account_id,
                customer_id=args.customer_id,
            )
        )
    else:
        parser.print_help()
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
