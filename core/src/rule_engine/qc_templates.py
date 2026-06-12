"""RUL QC template — registered in the central diagnostics QC registry at import."""
from __future__ import annotations

from core.src.diagnostics import QCField, QCTemplate, register_template

from .models import TriggerKind

__all__ = ["RULE_EVALUATION"]

RULE_EVALUATION = QCTemplate(
    module_prefix="RUL",
    artifact_type="rule_evaluation",
    fields=(
        QCField("trigger_kind", "enum", tuple(t.value for t in TriggerKind)),
        QCField("matched_count", "int"),
        QCField("winning_scope_distribution", "enum", ("global", "customer", "device", "postgres_override")),
        QCField("paused_match_count", "int"),
        QCField("eval_latency_bucket", "enum", ("fast", "normal", "slow", "timeout")),
        QCField("collision_warnings", "int"),
        QCField("orphan_warnings", "int"),
        QCField("result", "enum", ("OK", "WARN", "FAIL")),
    ),
)

register_template(RULE_EVALUATION)
