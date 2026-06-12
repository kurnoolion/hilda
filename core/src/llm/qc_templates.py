"""LLG QC template — registered in the central diagnostics QC registry at import."""
from __future__ import annotations

from core.src.diagnostics import QCField, QCTemplate, register_template
from core.src.llm.protocol import TaskKind

__all__ = ["TASK_CONTRACT"]

TASK_CONTRACT = QCTemplate(
    module_prefix="LLG",
    artifact_type="task_contract",
    fields=(
        QCField("task", "enum", tuple(t.value for t in TaskKind)),
        QCField("schema_valid", "bool"),
        QCField("latency_ms", "int"),
        QCField("confidence_bucket", "enum", ("high", "medium", "low", "n/a")),
        QCField("result", "enum", ("OK", "WARN", "FAIL")),
    ),
)

register_template(TASK_CONTRACT)
