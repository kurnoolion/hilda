"""diagnostics — central registry + compact report schemas + QC template base.

See core/src/diagnostics/MODULE.md.
"""
from core.src.diagnostics.error_codes import (
    ERROR_CODES,
    ErrorCode,
    ErrorSeverity,
    PREFIX_REGISTRY,
    PipelineError,
    format_code,
    get_code,
    register_code,
)
from core.src.diagnostics.qc import (
    QC_REGISTRY,
    QCField,
    QCTemplate,
    register_template,
)
from core.src.diagnostics.report import ReportRecord, ReportType, ReportWriter

__all__ = [
    "ERROR_CODES",
    "ErrorCode",
    "ErrorSeverity",
    "PREFIX_REGISTRY",
    "PipelineError",
    "QC_REGISTRY",
    "QCField",
    "QCTemplate",
    "ReportRecord",
    "ReportType",
    "ReportWriter",
    "format_code",
    "get_code",
    "register_code",
    "register_template",
]
