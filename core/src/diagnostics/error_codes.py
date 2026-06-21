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
    "CSA": "customer_adapter",
    "TRK": "tracker",
    "TRC": "test_report",
    "RUL": "rule_engine",
    "LLG": "llm",
    "WFL": "workflow_engine",
    "ASI": "api_spec_ingestor",
    "TSI": "template_schema_ingestor",
    "TRP": "test_report_profiler",
    "DSH": "dashboard",
    # Added 2026-06-09 — corp-side gateway modules per SYSTEM.md §2.1 (2026-05-24 expansion):
    "CMG": "corp_messenger_gateway",
    "CPG": "corp_plm_gateway",
    # Added 2026-06-21 — meta-prefix for cross-cutting HildaOpsAlert codes per FR-75 + [D-002];
    # owned by diagnostics as the chat-mediated collaboration infra anchor (not a runtime module):
    "STATUS": "diagnostics",
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
    # ITR-E003 renumbered to ITR-E008 on 2026-06-21 per architect decision (FR-25 (b) cascade lock
    # 2026-06-19 assigned ITR-E003 = "customer JIRA auth expired"); old idempotency semantic preserved
    # at ITR-E008 to honor diagnostics Invariant "codes never deleted, semantic stable".
    "ITR-E003": ErrorCode(
        "ITR-E003", "Customer JIRA auth expired for account '{account_id}' on '{customer_id}'", False
    ),
    "ITR-E008": ErrorCode(
        "ITR-E008", "Conflict: idempotency key '{key}' already resolved to '{existing_id}'", False
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
    # --- storage (STR) ---
    "STR-E001": ErrorCode(
        "STR-E001", "Postgres unavailable or session failure: {reason}", False
    ),
    "STR-E002": ErrorCode(
        "STR-E002", "Record not found: {entity} '{key}'", False
    ),
    "STR-E003": ErrorCode(
        "STR-E003", "Constraint violation on {entity}: {reason}", False
    ),
    "STR-E004": ErrorCode(
        "STR-E004", "NSD IO failure at '{path}': {reason}", False
    ),
    "STR-E005": ErrorCode(
        "STR-E005", "Cross-milestone association rejected: file '{file_hash}' already bound to milestone '{existing_milestone}'", False
    ),
    "STR-E006": ErrorCode(
        "STR-E006", "Folder routing references unknown item_no {item_no} in milestone '{milestone_id}'", False
    ),
    "STR-E007": ErrorCode(
        "STR-E007", "Invalid or expired download token", False
    ),
    "STR-E008": ErrorCode(
        "STR-E008", "Cache TTL {ttl_seconds}s exceeds 24h cap per [D-012]", False
    ),
    "STR-W001": ErrorCode(
        "STR-W001", "NSD mount unavailable; file operations degraded", True
    ),
    "STR-W002": ErrorCode(
        "STR-W002", "Unknown tag '{tag}' for customer '{customer_id}' — not in tag catalog", True
    ),
    "STR-W003": ErrorCode(
        "STR-W003", "Default work-item missing for milestone '{milestone_id}'", True
    ),
    "STR-W004": ErrorCode(
        "STR-W004", "Orphan override: rule_id '{rule_id}' not found in any loaded YAML rule", True
    ),
    "STR-W005": ErrorCode(
        "STR-W005", "Orphan DocumentIndexRow '{file_hash}' — no associations reference it", True
    ),
    "STR-W006": ErrorCode(
        "STR-W006", "PLM fan-out target for '{file_hash}' has no plm_id (owner '{owner_email}' lacks PLM issue)", True
    ),
    "STR-W007": ErrorCode(
        "STR-W007", "Document in staged NSD path older than {age_days}d — TPM resolution pending", True
    ),
    "STR-E009": ErrorCode(
        "STR-E009", "TPM resolution on '{file_hash}'/'{delivery_item_id}': file not at expected source state '{expected}' (found '{actual}')", False
    ),
    "STR-E010": ErrorCode(
        "STR-E010", "tpm_resolve_doc_type called with asymmetric doc_id_slug/rev_number — pass both or neither per FR-86 staged-fill", False
    ),
    "STR-W008": ErrorCode(
        "STR-W008", "TPM resolution idempotent re-call for '{file_hash}'/'{delivery_item_id}' — already at target state", True
    ),
    # --- llm (LLG) ---
    "LLG-E001": ErrorCode(
        "LLG-E001", "LLM call failed for task '{task}' backend '{backend}': {reason}", False
    ),
    "LLG-E002": ErrorCode(
        "LLG-E002", "Unknown TaskKind '{task}' — not in TaskKind enum", False
    ),
    "LLG-E003": ErrorCode(
        "LLG-E003", "Structured output validation failed after {n} retries for task '{task}' backend '{backend}'", False
    ),
    "LLG-E004": ErrorCode(
        "LLG-E004", "Backend endpoint '{url}' is not on-prem — rejected by [D-007] / [D-052] startup check", False
    ),
    "LLG-E005": ErrorCode(
        "LLG-E005", "Prompt template '{task}.j2' not found in template_dir", False
    ),
    "LLG-E006": ErrorCode(
        "LLG-E006", "TaskKind '{task}' has no backend mapping — missing entry in task_backend_map", False
    ),
    "LLG-W001": ErrorCode(
        "LLG-W001", "Rate limit hit for task '{task}' backend '{backend}'; queued for {wait_s}s", True
    ),
    "LLG-W002": ErrorCode(
        "LLG-W002", "LLM confidence {score} below threshold {threshold} for task '{task}'", True
    ),
    "LLG-W003": ErrorCode(
        "LLG-W003", "Retry {n}/{max} after parse failure on task '{task}' backend '{backend}'", True
    ),
    "LLG-W004": ErrorCode(
        "LLG-W004", "Model cold-load triggered for '{backend}/{model}'; expected latency +{n}s", True
    ),
    "LLG-W005": ErrorCode(
        "LLG-W005", "Corp LLM rate limit approaching ({used}/{limit_per_min} per minute); queue depth {n}", True
    ),
    "LLG-W006": ErrorCode(
        "LLG-W006", "Corp LLM rate limit exceeded; task '{task}' deferred {n}s — no automatic spillover per [D-052]", True
    ),
    "LLG-W007": ErrorCode(
        "LLG-W007", "Reserved", True
    ),
    "LLG-W008": ErrorCode(
        "LLG-W008", "ROUTE_ATTACHMENT returned {n} matches with summed confidence {score} exceeding over-routing threshold {threshold} on '{file_hash}'; all committed per FR-79 but flagged", True
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
