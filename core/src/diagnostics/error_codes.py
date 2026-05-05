"""Central error-code registry for HILDA. Anchors [D-002] NFR-18."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ErrorSeverity(str, Enum):
    ERROR = "E"
    WARNING = "W"


@dataclass(frozen=True)
class ErrorCode:
    code: str
    message: str
    recoverable: bool


PREFIX_REGISTRY: dict[str, str] = {
    "DGN": "diagnostics",
    "TSC": "template_schema",
    "SHP": "sharepoint_integration",
    "STO": "storage",
    "CRD": "credential_service",
    "EML": "email_service",
    "ITR": "issue_tracker",
    "MSG": "messenger",
    "CAD": "customer_adapter",
    "TRK": "tracker",
    "TRC": "test_report",
    "RUL": "rule_engine",
    "LLG": "llm",
    "WFL": "workflow_engine",
    "ASI": "api_spec_ingestor",
    "TSI": "template_schema_ingestor",
    "TRP": "test_report_profiler",
    "DSH": "dashboard",
}


ERROR_CODES: dict[str, ErrorCode] = {
    "DGN-E001": ErrorCode(
        "DGN-E001", "Duplicate prefix '{prefix}' registered by '{module}'", False
    ),
    "DGN-E002": ErrorCode("DGN-E002", "Unknown error code '{code}' referenced", False),
    "DGN-W001": ErrorCode(
        "DGN-W001", "Module '{module}' has no QC template registered", True
    ),
}


def register_code(code: ErrorCode) -> None:
    """Register a new ErrorCode. Validates prefix is in PREFIX_REGISTRY.

    Idempotent for identical re-registration; raises on conflict.
    """
    prefix = code.code.split("-", 1)[0]
    if prefix not in PREFIX_REGISTRY:
        raise PipelineError(
            "DGN-E001",
            context={"prefix": prefix, "module": f"<unknown for code {code.code}>"},
        )
    existing = ERROR_CODES.get(code.code)
    if existing is not None and existing != code:
        raise ValueError(
            f"Code {code.code} already registered with different definition"
        )
    ERROR_CODES[code.code] = code


def get_code(code: str) -> ErrorCode:
    if code not in ERROR_CODES:
        raise PipelineError("DGN-E002", context={"code": code})
    return ERROR_CODES[code]


def format_code(code: str, **kwargs: str) -> str:
    return get_code(code).message.format(**kwargs)


class PipelineError(Exception):
    """Structured error carrying a registered error code + context dict."""

    def __init__(
        self,
        code: str,
        context: dict[str, object] | None = None,
        cause: Exception | None = None,
    ) -> None:
        self.code_id = code
        self.context = dict(context or {})
        self.cause = cause
        if code in ERROR_CODES:
            try:
                msg = ERROR_CODES[code].message.format(**self.context)
            except KeyError:
                msg = ERROR_CODES[code].message
        else:
            msg = f"Unknown error code: {code}"
        super().__init__(f"{code}: {msg}")
