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
    # CAD prefix used by customer_adapter MODULE.md + [D-116] ADR error codes
    # (parallel to CSA env-var prefix in credential_service; semantically distinct):
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
    # Added 2026-06-09 — corp-side gateway modules per SYSTEM.md §2.1 (2026-05-24 expansion):
    "CMG": "corp_messenger_gateway",
    "CPG": "corp_plm_gateway",
    # Added 2026-06-21 — meta-prefix for cross-cutting HildaOpsAlert codes per FR-75 + [D-002];
    # owned by diagnostics as the chat-mediated collaboration infra anchor (not a runtime module):
    "STATUS": "diagnostics",
    # Added 2026-06-26 per [D-127] -- HILDA OPS alert mechanism (single-ingress alert fan-out
    # to email + messenger). Distinct from STATUS meta-prefix above (which is for chat reports):
    "OPS": "ops_alerts",
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
    # Added 2026-06-25 (Module #13 cascade D15) per architect Q5 / Q6 / [D-098] locks:
    "ITR-W003": ErrorCode(
        "ITR-W003", "PLM download in-flight for ('{plm_id}', '{file_id}'); skipping duplicate", True
    ),
    "ITR-W004": ErrorCode(
        "ITR-W004", "PLM API N-retries-exhausted for '{plm_id}' op '{operation}' -- HILDA OPS alert mechanism TBD", True
    ),
    "ITR-W005": ErrorCode(
        "ITR-W005", "PLM upload verification failure for '{plm_id}' / '{file_name}'", True
    ),
    # --- credential_service (CRD) ---
    "CRD-E001": ErrorCode(
        "CRD-E001", "No credential for pm_id='{pm_id}' system_type='{system}' customer_id='{customer_id}'", False
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
    "CRD-E005": ErrorCode(
        "CRD-E005", "Required customer_id missing for per-customer/per-(account,customer) system_type='{system}'", False
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
    # --- tracker (TRK) — registered 2026-06-23 per tracker MODULE.md development phase ---
    "TRK-E001": ErrorCode(
        "TRK-E001", "Illegal transition: delivery_item '{item_id}' state '{from_state}' -> '{to_state}' not in LEGAL_TRANSITIONS", False
    ),
    "TRK-E002": ErrorCode(
        "TRK-E002", "Reassignment source '{source_item_id}' is not a Default work-item -- FR-83 path rejects", False
    ),
    "TRK-E003": ErrorCode(
        "TRK-E003", "Reassignment target '{target_item_id}' item_type '{target_type}' incompatible with doc_type '{doc_type}' per FR-86 alignment matrix", False
    ),
    "TRK-E004": ErrorCode(
        "TRK-E004", "bypass_guards=True attempted from non-manual_tpm_override trigger_source '{source}' -- automated callers MUST NOT bypass", False
    ),
    "TRK-E005": ErrorCode(
        "TRK-E005", "SP REST writeback permanent failure on state transition (delivery_item '{item_id}' -> '{state}'): {sp_error} -- ops triage", False
    ),
    "TRK-E006": ErrorCode(
        "TRK-E006", "Rewind from SubmittedToCustomer to '{target_state}' attempted without TPM attribution (trigger_source='{source}') -- automated rules cannot rewind a submitted item; manual TPM override required per FR-14", False
    ),
    "TRK-W001": ErrorCode(
        "TRK-W001", "Transition guard denied: delivery_item '{item_id}' state '{from_state}' -> '{to_state}' (blocking: {conditions}) -- PM/TPM dashboard surface", True
    ),
    "TRK-W002": ErrorCode(
        "TRK-W002", "FR-82 tag propagation: item '{item_id}' skipped (terminal state '{state}')", True
    ),
    "TRK-W003": ErrorCode(
        "TRK-W003", "Transition idempotent no-op: delivery_item '{item_id}' already at '{state}' -- Celery retry collapsed", True
    ),
    "TRK-W004": ErrorCode(
        "TRK-W004", "Default work-item already instantiated for milestone '{milestone_id}'; reusing existing row '{item_id}'", True
    ),
    "TRK-W005": ErrorCode(
        "TRK-W005", "FR-83 reassignment: target item's tpm_reassignment_target_item_id field was not cleared post-resolution -- surfacing for ops triage; may indicate SP UI engineer's button UX out of sync", True
    ),
    "TRK-W006": ErrorCode(
        "TRK-W006", "pm_approval_at not cleared on entry to UNDER_PM_REVIEW for item '{item_id}' -- defensive clear discipline missed; ops triage (stale approval may silently auto-advance on next UpdateState)", True
    ),
    "TRK-W007": ErrorCode(
        "TRK-W007", "[D-039] Step 2 LLM disambiguation deferred (Ph-2) on reassignment of file '{file_hash}' to item '{target_item_id}': multiple slug candidates {candidates}; Ph-1 fallback set revision_classification=new_document (rev1) -- Ph-2 LLM will resolve later", True
    ),
    "TRK-W008": ErrorCode(
        "TRK-W008", "Automated DELAYED/BLOCKED resume on delivery_item '{item_id}' targets '{to_state}' but prior_delivery_state was '{prior_state}' -- resume target mismatch; TPM override path required to resume to a different active state per FR-14", True
    ),
    # --- workflow_engine (WFL) -- registered 2026-06-23 per workflow_engine MODULE.md dev phase ---
    "WFL-E001": ErrorCode(
        "WFL-E001", "ActionKind '{action_kind}' has no TaskBinding in ACTION_KIND_TO_TASK registry -- registry incomplete; ops triage", False
    ),
    "WFL-E002": ErrorCode(
        "WFL-E002", "Manual trigger '{action_kind}' not applicable to item '{item_id}' state '{state}' -- FR-31 sub-3 state-filter rejection", False
    ),
    "WFL-E003": ErrorCode(
        "WFL-E003", "Celery-beat schedule build failed: storage error '{reason}' -- beat singleton cannot start", False
    ),
    "WFL-E004": ErrorCode(
        "WFL-E004", "Celery broker connection failure: {reason} -- task enqueue fails", False
    ),
    "WFL-E005": ErrorCode(
        "WFL-E005", "Task '{task_name}' terminal failure after {retries} retries on item '{item_id}': {exc_class} -- manual triage", False
    ),
    "WFL-E006": ErrorCode(
        "WFL-E006", "Trigger source '{source}' constructed malformed TriggerEvent (missing required field '{field}') -- caller bug; dispatcher rejects", False
    ),
    "WFL-W001": ErrorCode(
        "WFL-W001", "RuleMatch for rule '{rule_id}' on item '{item_id}' skipped (FR-31 sub-1 pause active per item.rules_paused)", True
    ),
    "WFL-W002": ErrorCode(
        "WFL-W002", "Polling-schedule for item '{item_id}' invalid (RUL-W004 from rule_engine); using baseline default {minutes}min", True
    ),
    "WFL-W003": ErrorCode(
        "WFL-W003", "Task '{task_name}' retried (attempt {n}/{max}) on item '{item_id}' -- transient {exc_class}", True
    ),
    "WFL-W004": ErrorCode(
        "WFL-W004", "Celery worker concurrency saturated on queue '{queue}'; task '{task_name}' queue depth {n} -- capacity flag", True
    ),
    "WFL-W005": ErrorCode(
        "WFL-W005", "Beat singleton skew detected: two beat instances running (multi-fire risk) -- ops triage", True
    ),
    # --- dashboard (DSH) -- registered 2026-06-24 per dashboard MODULE.md dev phase ---
    "DSH-E001": ErrorCode(
        "DSH-E001", "Delivery item '{item_id}' not found", False
    ),
    "DSH-E002": ErrorCode(
        "DSH-E002", "Download token invalid or expired -- renders friendly page (HTTP 410)", True
    ),
    "DSH-E003": ErrorCode(
        "DSH-E003", "Authentication failed: no Negotiate header / Kerberos validation failed (HTTP 401)", False
    ),
    "DSH-E004": ErrorCode(
        "DSH-E004", "Refresh rate limit hit for milestone '{milestone_id}' (HTTP 429; informational, not an error)", True
    ),
    "DSH-W001": ErrorCode(
        "DSH-W001", "Reverse proxy did not forward Negotiate header -- degraded mode warning; surface in /admin diagnostic", True
    ),
    "DSH-W002": ErrorCode(
        "DSH-W002", "Static-asset cache miss (Ph-2 cold-cache warning)", True
    ),
    # --- customer_adapter (CAD) -- registered 2026-06-25 per [D-116] Ratified ---
    "CAD-E001": ErrorCode(
        "CAD-E001", "Selector config file '{path}' not found for carrier '{customer_id}' (Ph-2 forward-looking; selector_loader)", False
    ),
    "CAD-E002": ErrorCode(
        "CAD-E002", "Selector config '{path}' missing required field '{field}' for selectors_version '{version}' (Ph-2 forward-looking)", False
    ),
    "CAD-E003": ErrorCode(
        "CAD-E003", "Chromium binary not found at '{path}' (Ph-2 forward-looking; binding owns Chromium per [D-116] D17)", False
    ),
    "CAD-E004": ErrorCode(
        "CAD-E004", "Binding session establishment failed for carrier '{customer_id}': {reason}", False
    ),
    "CAD-E005": ErrorCode(
        "CAD-E005", "Upload timed out after {timeout_s}s on filename='{filename}' carrier='{customer_id}' target_dir='{target_dir}'", False
    ),
    "CAD-E006": ErrorCode(
        "CAD-E006", "Upload selector '{selector_name}' not found in current Google Drive DOM -- selectors_version stale (Ph-2 forward-looking)", False
    ),
    "CAD-E007": ErrorCode(
        "CAD-E007", "Carrier file_id extraction failed post-upload (Ph-2 forward-looking; Ph-1 binding returns bool only per [D-116] D16)", False
    ),
    "CAD-E008": ErrorCode(
        "CAD-E008", "Customer credential '{credential_id}' not retrievable from credential_service", False
    ),
    "CAD-E009": ErrorCode(
        "CAD-E009", "Binding _invoke_binding method not implemented for customer '{customer_id}' -- per-customer subclass missing or stub (per [D-027] Teacher/Student split)", False
    ),
    "CAD-E010": ErrorCode(
        "CAD-E010", "customer_delivery_info required for upload (no_customer_upload=False) but missing/empty on Deliverables row for customer '{customer_id}' -- SP UI engineer must provision the per-row value per D-126 cascade 2026-06-26", False
    ),
    "CAD-W001": ErrorCode(
        "CAD-W001", "Capability flag '{flag}' is False for carrier '{customer_id}' -- operation '{op}' not supported (Ph-2 forward-looking)", True
    ),
    "CAD-W002": ErrorCode(
        "CAD-W002", "Browser session expired during upload; re-established and retried (Ph-2 forward-looking)", True
    ),
    "CAD-W003": ErrorCode(
        "CAD-W003", "Selector '{selector}' returned multiple matches -- used first; selectors_version may need refinement (Ph-2 forward-looking)", True
    ),
    "CAD-W004": ErrorCode(
        "CAD-W004", "Selector-pack version mismatch: subclass expects '{subclass_version}', YAML provides '{yaml_version}' (Ph-2 forward-looking)", True
    ),
    "CAD-W005": ErrorCode(
        "CAD-W005", "Clock skew exceeds TOTP tolerance: HILDA host {skew_s}s off NTP server; TOTP window is ~30s; uploads may fail until clock resyncs", True
    ),
    # --- email_service (EML) -- registered 2026-06-25 per email_service MODULE.md dev phase ---
    "EML-E001": ErrorCode(
        "EML-E001", "Subject does not contain BATCH-<id> per FR-24 -- message_id='{mid}' sender='{sender_redacted}'", False
    ),
    "EML-E002": ErrorCode(
        "EML-E002", "IMAP fetch failed: {reason}", False
    ),
    "EML-E003": ErrorCode(
        "EML-E003", "IMAP authentication rejected (credential_service may need refresh)", False
    ),
    "EML-E004": ErrorCode(
        "EML-E004", "SMTP send failed for outbound message: {reason}", False
    ),
    "EML-E005": ErrorCode(
        "EML-E005", "FR-12 path (a) structured block expected but not found; falling back to path (c)", False
    ),
    "EML-E006": ErrorCode(
        "EML-E006", "Attachment classification failed for '{filename_slug}' on message_id='{mid}': {reason}", False
    ),
    "EML-E007": ErrorCode(
        "EML-E007", "SP alert subject matched pattern but routing key not resolvable (D-047)", False
    ),
    "EML-W001": ErrorCode(
        "EML-W001", "Sender mismatch -- sender '{sender_redacted}' is not owner / tg_alias / cc for BATCH-<id> (FR-12; recoverable, surfaced as PM flag)", True
    ),
    "EML-W002": ErrorCode(
        "EML-W002", "Duplicate attachment skipped -- file_hash already in document_index for item_id='{item}' (D-039 Step 0)", True
    ),
    "EML-W003": ErrorCode(
        "EML-W003", "Attachment revision determination ambiguous -- written to '_staged_revision/' path per FR-86 (NSDPathType=STAGED_NOT_REVISION); awaits FR-87 step (C) TPM resolution", True
    ),
    "EML-W004": ErrorCode(
        "EML-W004", "Email classified 'OTHER' -- neither SP alert nor BATCH-id; surfaced for PM triage", True
    ),
    # EML-W005 RETIRED 2026-06-09 per [D-053] impl note 2026-06-08 -- replaced by FR-86 storage matrix
    # landing rule (misaligned (item_type, doc_type) pairs land on staged-not-classified per EML-W006).
    # Code reserved (do not reuse); kept for registry-completeness audits.
    "EML-W005": ErrorCode(
        "EML-W005", "RETIRED -- prior routing/type-mismatch warning superseded by FR-86 storage matrix (use EML-W006); reserved, do not reuse", True
    ),
    "EML-W006": ErrorCode(
        "EML-W006", "Attachment misaligned per FR-86 alignment invariant -- (item_type='{item_type}', doc_type='{doc_type}') pair landed on 'staged-not-classified' path (NSDPathType=STAGED_NOT_CLASSIFIED); awaits FR-87 step (B) TPM doc_type resolution OR step (A) work-item reassignment", True
    ),
    "EML-W007": ErrorCode(
        "EML-W007", "Attachment over-routed per FR-79 -- N={n} matches on file_hash='{file_hash}' exceeds route_attachment_max_matches_threshold={threshold}; all matches committed per [D-055] symmetric M:M contract but flagged for ops review", True
    ),
    # --- messenger (MSG) -- registered 2026-06-25 per messenger MODULE.md Ph-1 dev phase ---
    # Per architect Q-M1..Q-M6 lock 2026-06-25 (FR-10 cross-channel escalation).
    "MSG-E001": ErrorCode(
        "MSG-E001", "sendMessage failed: {reason_token}", False
    ),
    "MSG-E002": ErrorCode(
        "MSG-E002", "Composer template rendering failed for owner '{owner_id}'", False
    ),
    "MSG-E003": ErrorCode(
        "MSG-E003", "CorpMessengerAdapter binding not configured for deployment (subclass missing or stub)", False
    ),
    "MSG-W001": ErrorCode(
        "MSG-W001", "Daily limit reached ({daily_limit_per_owner}/day) for owner_corp_id='{redacted}'", True
    ),
    "MSG-W002": ErrorCode(
        "MSG-W002", "Message truncated: rendered '{n}' bytes exceeds max_message_bytes='{max}'", True
    ),
    "MSG-W003": ErrorCode(
        "MSG-W003", "Retry attempt {attempt}/{max_attempts} for owner_corp_id='{redacted}'", True
    ),
    # OPS prefix added 2026-06-26 per [D-127] -- ops_alerts module Ph-1 dev:
    "OPS-E001": ErrorCode(
        "OPS-E001", "recipients.yaml load failure at '{path}' -- reason='{reason}' (LOCAL config per [D-125] Point 3)", False
    ),
    "OPS-E002": ErrorCode(
        "OPS-E002", "ops_alert email send failed (best-effort; signal site not disrupted per [D-127] Invariant 4)", False
    ),
    "OPS-E003": ErrorCode(
        "OPS-E003", "ops_alert messenger DM fan-out partial -- some corp_ids landed, others failed (best-effort)", False
    ),
    "OPS-E004": ErrorCode(
        "OPS-E004", "ops_alert messenger DM fan-out total failure -- no corp_ids landed (best-effort; local-log fallback may have written)", False
    ),
    "OPS-E005": ErrorCode(
        "OPS-E005", "ops_alert context payload exceeded NFR-2 bound -- value at key='{key}' truncated to {max_chars} chars", False
    ),
    "OPS-W001": ErrorCode(
        "OPS-W001", "ops_alert suppressed by rate limit: source='{source}' error_code='{error_code}' (rate_limit_per_minute={rate})", True
    ),
    "OPS-W002": ErrorCode(
        "OPS-W002", "ops_alert context payload contained credential key='{key}' -- redacted to <REDACTED> per NFR-2", True
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
