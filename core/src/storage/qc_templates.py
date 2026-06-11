"""STR QC templates — registered in the central diagnostics QC registry at import."""
from __future__ import annotations

from core.src.diagnostics import QCField, QCTemplate, register_template

__all__ = ["SCHEMA_ROUNDTRIP"]

SCHEMA_ROUNDTRIP = QCTemplate(
    module_prefix="STR",
    artifact_type="schema_roundtrip",
    fields=(
        QCField("entities_ok", "bool"),
        QCField("columns_mapped", "int"),
        QCField("roundtrip_ok", "bool"),
        QCField("result", "enum", ("OK", "WARN", "FAIL")),
    ),
)

register_template(SCHEMA_ROUNDTRIP)
