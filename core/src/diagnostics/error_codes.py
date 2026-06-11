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
    # --- issue_tracker (ITR) ---
    "ITR-E001": ErrorCode(
        "ITR-E001", "Unauthorized: credentials rejected by issue tracker '{system}'", False
    ),
    "ITR-E002": ErrorCode(
        "ITR-E002", "Issue not found: '{issue_id}' in '{system}'", False
    ),
    "ITR-E003": ErrorCode(
        "ITR-E003", "Conflict: idempotency key '{key}' already resolved to '{existing_id}'", False
    ),
    "ITR-E004": ErrorCode(
        "ITR-E004", "Transition '{transition}' not available from current state on '{issue_id}'", False
    ),
    "ITR-E005": ErrorCode(
        "ITR-E005", "Attachment upload failed for '{issue_id}': {reason}", False
    ),
    "ITR-E006": ErrorCode(
        "ITR-E006", "Adapter '{slug}' not found in core/ or customizations/", False
    ),
    "ITR-W001": ErrorCode(
        "ITR-W001", "Rate limited by '{system}'; retry after {retry_after_s}s", True
    ),
    "ITR-W002": ErrorCode(
        "ITR-W002", "Webhook registration failed for '{system}'; falling back to poll_changes", True
    ),
    "ITR-E007": ErrorCode(
        "ITR-E007", "Operation '{operation}' not supported by adapter '{adapter}'", False
    ),
    # --- credential_service (CRD) ---
    "CRD-E001": ErrorCode(
        "CRD-E001", "No credential for pm_id='{pm_id}' system_type='{system}'", False
    ),
    "CRD-E002": ErrorCode(
        "CRD-E002", "sops decrypt failed for '{file}': {reason}", False
    ),
    "CRD-E003": ErrorCode(
        "CRD-E003", "Unknown system_type '{system}' — not in SystemType enum", False
    ),
    "CRD-E004": ErrorCode(
        "CRD-E004", "Credential file '{file}' malformed: missing required field '{field}'", False
    ),
    "CRD-W001": ErrorCode(
        "CRD-W001", "Credential cache miss for pm_id='{pm_id}' — falling back to ops-team credential", True
    ),
    "CRD-W002": ErrorCode(
        "CRD-W002", "Credential reload triggered by SIGHUP — cache rebuilt", True
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
