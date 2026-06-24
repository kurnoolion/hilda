"""issue_tracker utility helpers.

Per Module #13 cascade D8 2026-06-25 -- derive_tpm_corp_id helper for corp PLM
action attribution per Q4 architect lock.
"""
from __future__ import annotations

from core.src.diagnostics.error_codes import PipelineError


def derive_tpm_corp_id(projects_tpm_email: str) -> str:
    """Per Q4 architect lock 2026-06-25 -- strip the domain from Projects.TPM
    email; return the local part.

    Per `Projects_<customer_id>` SP row 3-tuple lookup per `[D-088]`. Result is
    used as ACTION ATTRIBUTION parameter when HILDA calls corp PLM APIs (NOT a
    credential per `[D-019]`; corp_plm_gateway PC handles actual auth).

    Examples:
        derive_tpm_corp_id("abc@corp.com")  -> "abc"
        derive_tpm_corp_id("alice.smith@corp.example.com")  -> "alice.smith"

    Raises:
        PipelineError ITR-E002 -- input is empty, missing the '@' separator, or
        has an empty local part.
    """
    if not projects_tpm_email or "@" not in projects_tpm_email:
        raise PipelineError(
            "ITR-E002",
            context={"issue_id": projects_tpm_email or "<empty>", "system": "tpm_corp_id"},
        )
    local = projects_tpm_email.split("@", 1)[0].strip()
    if not local:
        raise PipelineError(
            "ITR-E002",
            context={"issue_id": projects_tpm_email, "system": "tpm_corp_id"},
        )
    return local
