"""credential_service — stable read-only credential interface per [D-019] [D-038].

See core/src/credential_service/MODULE.md.
"""
from core.src.credential_service import qc_templates  # noqa: F401  (registers CRD QC template)
from core.src.credential_service.mock_service import MockCredentialService
from core.src.credential_service.protocol import (
    SYSTEM_ENV_PREFIX,
    AuthType,
    Credential,
    SystemType,
)
from core.src.credential_service.service import (
    OPS_TEAM_PM_ID,
    CredentialService,
    SopsCredentialService,
)

__all__ = [
    "AuthType",
    "Credential",
    "CredentialService",
    "MockCredentialService",
    "OPS_TEAM_PM_ID",
    "SYSTEM_ENV_PREFIX",
    "SopsCredentialService",
    "SystemType",
]
