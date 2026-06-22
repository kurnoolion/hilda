"""CredentialService Protocol + Ph-1/Ph-2 sops-backed implementation.

Anchors [D-019] (stable interface across phases) + [D-038] (sops + age at rest).
See core/src/credential_service/MODULE.md.
"""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Protocol, runtime_checkable

from core.src.diagnostics.error_codes import PipelineError, format_code
from core.src.credential_service.protocol import (
    SYSTEM_CRED_SCOPE,
    SYSTEM_ENV_PREFIX,
    SYSTEM_SUBTREE,
    AuthType,
    Credential,
    CredentialScope,
    SystemType,
)

logger = logging.getLogger(__name__)

__all__ = ["CredentialService", "SopsCredentialService", "OPS_TEAM_PM_ID"]

# Ph-1/Ph-2 shared ops-team attribution token per [D-019] impl note 2026-05-24.
# Overridable per system via HILDA_<PREFIX>_PM_ID in the credential file.
OPS_TEAM_PM_ID = "ops-team"

_AUTH_TYPES: tuple[AuthType, ...] = (
    "api_token",
    "basic",
    "ntlm",
    "kerberos",
    "oauth2_bearer",
)

# Which env-var field names carry the secret value(s) per auth_type.
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    "api_token": ("API_TOKEN",),
    "basic": ("USERNAME", "PASSWORD"),
    "ntlm": ("USERNAME", "PASSWORD"),
    "kerberos": ("KEYTAB_PATH",),
    "oauth2_bearer": ("BEARER",),
}


@runtime_checkable
class CredentialService(Protocol):
    """All adapters depend on this Protocol, not on the concrete implementation.

    Enables MockCredentialService in tests and a future Vault-backed swap at
    Ph-3+ with no caller-side change per [D-019].
    """

    async def get_credential(
        self,
        pm_id: str,
        system_type: str,
        customer_id: str | None = None,
    ) -> Credential: ...


def _parse_env_lines(text: str) -> dict[str, str]:
    """Parse decrypted KEY=VALUE lines; ignores blanks and # comments."""
    entries: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        entries[key.strip()] = value.strip().strip('"').strip("'")
    return entries


def _build_credential(system: SystemType, entries: dict[str, str], filename: str) -> Credential | None:
    """Build a Credential from decrypted env entries for one system.

    Returns None when the file declares no credential at all (legal for the
    no-auth lab LLM backends per MODULE.md file layout). Raises CRD-E004 when
    a credential is declared but incomplete for its auth_type.
    """
    prefix = f"HILDA_{SYSTEM_ENV_PREFIX[system]}_"
    fields = {
        key.removeprefix(prefix): value
        for key, value in entries.items()
        if key.startswith(prefix)
    }
    if not fields:
        return None

    auth_type = fields.get("AUTH_TYPE")
    if auth_type is None or auth_type not in _AUTH_TYPES:
        raise PipelineError(
            "CRD-E004", context={"file": filename, "field": f"{prefix}AUTH_TYPE"}
        )

    for required in _REQUIRED_FIELDS[auth_type]:
        if not fields.get(required):
            raise PipelineError(
                "CRD-E004", context={"file": filename, "field": f"{prefix}{required}"}
            )

    expires_at: datetime | None = None
    if fields.get("EXPIRES_AT"):
        expires_at = datetime.fromisoformat(fields["EXPIRES_AT"])

    keytab = fields.get("KEYTAB_PATH")
    return Credential(
        pm_id=fields.get("PM_ID", OPS_TEAM_PM_ID),
        system_type=system.value,
        auth_type=auth_type,  # type: ignore[arg-type]  # validated against _AUTH_TYPES above
        api_token=fields.get("API_TOKEN"),
        username=fields.get("USERNAME"),
        password=fields.get("PASSWORD"),
        keytab_path=Path(keytab) if keytab else None,
        bearer=fields.get("BEARER"),
        expires_at=expires_at,
    )


class SopsCredentialService:
    """Ph-1/Ph-2 implementation. Reads sops-encrypted `.env` files at startup,
    decrypts via `sops --decrypt` using the age key, caches Credential objects
    in-process. Never writes plaintext to disk.

    Resolution order for get_credential (Ph-1/Ph-2):
      1. Lookup (pm_id, system_type) in process cache.
      2. If absent, fall back to the shared ops-team credential for system_type
         (CRD-W001, expected in Ph-1/Ph-2).
      3. If still absent, raise CRD-E001.
    """

    def __init__(
        self,
        env_dir: Path = Path("/etc/hilda/credentials"),
        age_key_path: Path = Path("/etc/hilda/age.key"),
    ) -> None:
        self._env_dir = env_dir
        self._age_key_path = age_key_path
        # 3-tuple cache key: (pm_id, system_type, customer_id). customer_id is None
        # for SHARED scope (single shared credential); populated for PER_CUSTOMER and
        # PER_ACCOUNT_PER_CUSTOMER scopes. Extended 2026-06-21 per FR-25 (b) cascade.
        self._cache: dict[tuple[str, str, str | None], Credential] = {}
        self._loaded = False
        self._lock = asyncio.Lock()

    async def _decrypt_file(self, path: Path) -> str:
        """Run `sops --decrypt` and return plaintext from stdout (memory only)."""
        env = dict(os.environ)
        env["SOPS_AGE_KEY_FILE"] = str(self._age_key_path)
        try:
            proc = await asyncio.create_subprocess_exec(
                "sops",
                "--decrypt",
                str(path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except FileNotFoundError as exc:
            raise PipelineError(
                "CRD-E002",
                context={"file": path.name, "reason": "sops binary not found"},
                cause=exc,
            )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            # stderr from sops names the file / key problem, never secret material.
            reason = (stderr.decode(errors="replace").strip() or "non-zero exit")[:200]
            raise PipelineError(
                "CRD-E002", context={"file": path.name, "reason": reason}
            )
        return stdout.decode()

    async def _build_cache(self) -> dict[tuple[str, str, str | None], Credential]:
        """Scope-aware scan of env_dir per SYSTEM_CRED_SCOPE.

        SHARED: env_dir/<system>.enc.env -> key (pm_id, system, None)
        PER_CUSTOMER: env_dir/<subtree>/<customer_id>.enc.env
                      -> key (pm_id, system, customer_id)
        PER_ACCOUNT_PER_CUSTOMER: env_dir/<subtree>/<account_id>/<customer_id>.enc.env
                                  -> key (account_id, system, customer_id)
                                  (account_id overrides the file's PM_ID per FR-25 (b)
                                  carrier governance — account_id is the lookup key,
                                  PM_ID inside the file is informational only)
        NO_CREDENTIAL: skipped (lookups raise CRD-E001 by design)
        """
        cache: dict[tuple[str, str, str | None], Credential] = {}
        for system in SystemType:
            scope = SYSTEM_CRED_SCOPE.get(system, CredentialScope.SHARED)
            if scope == CredentialScope.NO_CREDENTIAL:
                continue
            if scope == CredentialScope.SHARED:
                path = self._env_dir / f"{system.value}.enc.env"
                if not path.exists():
                    continue
                plaintext = await self._decrypt_file(path)
                credential = _build_credential(
                    system, _parse_env_lines(plaintext), path.name
                )
                if credential is not None:
                    cache[(credential.pm_id, system.value, None)] = credential
                continue
            # PER_CUSTOMER or PER_ACCOUNT_PER_CUSTOMER scope:
            subtree_name = SYSTEM_SUBTREE.get(system)
            if subtree_name is None:
                continue
            subtree_root = self._env_dir / subtree_name
            if not subtree_root.exists():
                continue
            if scope == CredentialScope.PER_CUSTOMER:
                for cred_file in sorted(subtree_root.glob("*.enc.env")):
                    # Strip ".enc.env" (Path.stem only strips one extension; we have two):
                    customer_id = cred_file.name.removesuffix(".enc.env")
                    plaintext = await self._decrypt_file(cred_file)
                    credential = _build_credential(
                        system, _parse_env_lines(plaintext), cred_file.name
                    )
                    if credential is not None:
                        cache[(credential.pm_id, system.value, customer_id)] = credential
                continue
            if scope == CredentialScope.PER_ACCOUNT_PER_CUSTOMER:
                for account_dir in sorted(subtree_root.iterdir()):
                    if not account_dir.is_dir():
                        continue
                    account_id = account_dir.name
                    for cred_file in sorted(account_dir.glob("*.enc.env")):
                        customer_id = cred_file.name.removesuffix(".enc.env")
                        plaintext = await self._decrypt_file(cred_file)
                        credential = _build_credential(
                            system, _parse_env_lines(plaintext), cred_file.name
                        )
                        if credential is None:
                            continue
                        # account_id is the authoritative pm_id per FR-25 (b);
                        # override any PM_ID set inside the credential file.
                        credential = replace(credential, pm_id=account_id)
                        cache[(account_id, system.value, customer_id)] = credential
        return cache

    async def load(self) -> int:
        """Decrypt-once-at-startup entry point. Returns count of cached credentials.

        Idempotent: subsequent calls are no-ops (use reload() after rotation).
        """
        async with self._lock:
            if not self._loaded:
                self._cache = await self._build_cache()
                self._loaded = True
        return len(self._cache)

    async def reload(self) -> None:
        """Re-runs `sops --decrypt` on every file in env_dir and rebuilds the cache.

        Called by ops via SIGHUP / admin endpoint after rotating an .env file.
        No hot-reload in normal operation. Not wired to auth-error scenarios —
        a 401/403 from an external system means stale external state, not a
        stale local cache (MODULE.md review 2026-05-27).
        """
        async with self._lock:
            self._cache = await self._build_cache()
            self._loaded = True
        logger.warning(format_code("CRD-W002"))

    async def get_credential(
        self,
        pm_id: str,
        system_type: str,
        customer_id: str | None = None,
    ) -> Credential:
        """Resolve a credential per SYSTEM_CRED_SCOPE.

        NO_CREDENTIAL (MESSENGER + ISSUE_TRACKER for corp PLM intent): raises CRD-E001
            immediately. Callers must use IP-allowlist + identity-assertion pattern
            per FR-25 (a) + FR-51 pattern (d) instead.
        PER_ACCOUNT_PER_CUSTOMER (customer JIRA): exact lookup (pm_id=account_id,
            system, customer_id). Requires customer_id; raises CRD-E005 if None.
        PER_CUSTOMER (Google Drive): lookup by (system, customer_id) ignoring pm_id
            (Ph-1/Ph-2 shared ops-team pattern). Requires customer_id; raises CRD-E005
            if None.
        SHARED (email, sharepoint, llm_*): exact lookup (pm_id, system, None), then
            fallback to any cached entry for that system with customer_id=None
            (CRD-W001, expected in Ph-1/Ph-2 per [D-019] impl note 2026-05-24).
        """
        try:
            system = SystemType(system_type)
        except ValueError:
            raise PipelineError("CRD-E003", context={"system": system_type})

        if not self._loaded:
            await self.load()

        scope = SYSTEM_CRED_SCOPE.get(system, CredentialScope.SHARED)

        if scope == CredentialScope.NO_CREDENTIAL:
            # By design — corp PLM gateway / corp messenger gateway have no HILDA
            # credential. Caller is using the wrong pattern; surface CRD-E001 with
            # a hint that no credential exists for this system in Ph-1/Ph-2.
            raise PipelineError(
                "CRD-E001",
                context={
                    "pm_id": pm_id,
                    "system": system.value,
                    "customer_id": customer_id or "",
                },
            )

        if scope == CredentialScope.PER_ACCOUNT_PER_CUSTOMER:
            if customer_id is None:
                raise PipelineError(
                    "CRD-E005", context={"system": system.value}
                )
            credential = self._cache.get((pm_id, system.value, customer_id))
            if credential is not None:
                return credential
            raise PipelineError(
                "CRD-E001",
                context={
                    "pm_id": pm_id,
                    "system": system.value,
                    "customer_id": customer_id,
                },
            )

        if scope == CredentialScope.PER_CUSTOMER:
            if customer_id is None:
                raise PipelineError(
                    "CRD-E005", context={"system": system.value}
                )
            # pm_id is ignored in Ph-1/Ph-2 for per-customer scope — match on
            # (system, customer_id) and return the single cached credential.
            for (cached_pm_id, cached_system, cached_customer_id), credential in self._cache.items():
                if cached_system == system.value and cached_customer_id == customer_id:
                    if pm_id != cached_pm_id:
                        logger.debug(format_code("CRD-W001", pm_id=pm_id))
                    return credential
            raise PipelineError(
                "CRD-E001",
                context={
                    "pm_id": pm_id,
                    "system": system.value,
                    "customer_id": customer_id,
                },
            )

        # SHARED scope: exact lookup with customer_id=None, then ops-team fallback.
        exact = self._cache.get((pm_id, system.value, None))
        if exact is not None:
            return exact
        for (cached_pm_id, cached_system, cached_customer_id), credential in self._cache.items():
            if cached_system == system.value and cached_customer_id is None:
                if pm_id != cached_pm_id:
                    logger.debug(format_code("CRD-W001", pm_id=pm_id))
                return credential
        raise PipelineError(
            "CRD-E001",
            context={
                "pm_id": pm_id,
                "system": system.value,
                "customer_id": customer_id or "",
            },
        )

    def install_sighup_handler(self) -> bool:
        """Wire SIGHUP → reload() on the running event loop (ops rotation hook).

        Returns True when installed. No-op (False) where SIGHUP is unavailable —
        Windows lacks the signal and its Proactor loop lacks add_signal_handler.
        Production runtime is the Linux HILDA PC per [D-026]; on a Windows dev
        box reload() remains reachable directly / via the admin endpoint.
        """
        import signal

        if not hasattr(signal, "SIGHUP"):
            logger.info("SIGHUP unavailable on this platform; reload() handler not installed")
            return False
        loop = asyncio.get_running_loop()
        try:
            loop.add_signal_handler(
                signal.SIGHUP, lambda: asyncio.ensure_future(self.reload())
            )
        except NotImplementedError:
            logger.info("Event loop lacks add_signal_handler; reload() handler not installed")
            return False
        return True
