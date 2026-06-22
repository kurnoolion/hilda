"""MockCredentialService — in-memory credential store for tests.

See core/src/credential_service/MODULE.md.
"""
from __future__ import annotations

from core.src.diagnostics.error_codes import PipelineError
from core.src.credential_service.protocol import Credential, SystemType
from core.src.credential_service.service import OPS_TEAM_PM_ID

__all__ = ["MockCredentialService"]


class MockCredentialService:
    """In-memory credential store for tests. Pre-populated by test fixtures via
    register(); raises CRD-E001 for unknown (pm_id, system_type).

    No Ph-1/Ph-2 ops-team fallback here — the mock is exact-match by design so
    tests fail loudly on unexpected lookups. Use register() with the pm_id the
    code under test will pass.
    """

    def __init__(self) -> None:
        # Key tuple extended 2026-06-21: (pm_id, system_type, customer_id) supports
        # per-(account, customer) JIRA + per-customer Google Drive routing.
        # customer_id=None for single-shared-credential systems (current Ph-1/Ph-2 pattern).
        self._store: dict[tuple[str, str, str | None], Credential] = {}

    def register(self, cred: Credential, customer_id: str | None = None) -> None:
        self._store[(cred.pm_id, cred.system_type, customer_id)] = cred

    async def get_credential(
        self,
        pm_id: str,
        system_type: str,
        customer_id: str | None = None,
    ) -> Credential:
        try:
            system = SystemType(system_type)
        except ValueError:
            raise PipelineError("CRD-E003", context={"system": system_type})
        credential = self._store.get((pm_id, system.value, customer_id))
        if credential is None:
            raise PipelineError(
                "CRD-E001",
                context={"pm_id": pm_id, "system": system.value, "customer_id": customer_id or ""},
            )
        return credential

    @classmethod
    def with_all_system_types(cls, pm_id: str = OPS_TEAM_PM_ID) -> "MockCredentialService":
        """Synthetic credential per SystemType — for integration tests / --mock CLI."""
        mock = cls()
        for system in SystemType:
            mock.register(
                Credential(
                    pm_id=pm_id,
                    system_type=system.value,
                    auth_type="api_token",
                    api_token=f"mock-token-{system.value}",
                )
            )
        return mock
