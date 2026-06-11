"""CRD QC templates — registered in the central diagnostics QC registry at import.

`auth_type` includes "none" beyond the five AuthType values so the template can
express the file-absent / no-credential case (present=false) without free text.
"""
from __future__ import annotations

from core.src.diagnostics import QCField, QCTemplate, register_template

__all__ = ["CREDENTIAL_COMPLETENESS"]

CREDENTIAL_COMPLETENESS = QCTemplate(
    module_prefix="CRD",
    artifact_type="credential_completeness",
    fields=(
        QCField("present", "bool"),
        QCField(
            "auth_type",
            "enum",
            ("api_token", "basic", "ntlm", "kerberos", "oauth2_bearer", "none"),
        ),
        QCField("value_carriers_consistent", "bool"),
        QCField("result", "enum", ("OK", "WARN", "FAIL")),
    ),
)

register_template(CREDENTIAL_COMPLETENESS)
