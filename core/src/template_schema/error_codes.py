"""TSC-prefixed error codes for template_schema."""
from __future__ import annotations

from core.src.diagnostics import ErrorCode, register_code

_TSC_CODES = [
    ErrorCode("TSC-E001", "Schema validation failed for customer '{customer}': {reason}", False),
    ErrorCode("TSC-E002", "Required canonical field '{field}' missing in customer schema '{customer}'", False),
    ErrorCode("TSC-E003", "Unknown entity type '{entity}' in customer schema '{customer}'", False),
    ErrorCode("TSC-E004", "Invalid path_id '{value}': must match [a-zA-Z0-9_-]+", False),
    ErrorCode("TSC-W001", "Customer schema version mismatch for '{customer}': schema v{schema_ver} vs module v{module_ver}", True),
    ErrorCode("TSC-W002", "Optional canonical field '{field}' unmapped in customer schema '{customer}'", True),
    # Phase 3 additions 2026-06-10:
    ErrorCode("TSC-W003", "Unknown tag '{tag}' in item_description for item '{item_id}' — not registered in customer tag catalog (FR-82)", True),
    ErrorCode("TSC-W004", "Confirmation item '{item_id}' has no_customer_upload=False — Confirmation items MUST have no_customer_upload=True per [D-053] + tracker MODULE.md invariant", True),
    # Phase 4 additions 2026-06-21 (template_schema arch revisit cascade):
    ErrorCode("TSC-W005", "TG-field divergence — items in milestone '{milestone}' sharing tg_name '{tg_name}' have inconsistent TG-denormalized fields", True),
    ErrorCode("TSC-W006", "CustomerJIRA-only role-collapse violation — item '{item_id}' has owner_corp_email != PM.Work_email (informational; non-blocking per FR-25 (b) lock 2026-06-20)", True),
    ErrorCode("TSC-W007", "Routing tag-set subset-overlap — item '{item_id}' tag-set is subset of item '{other_id}' tag-set in same (milestone, tg_name); routing is ambiguous per FR-82 architect lock 2026-06-20", True),
    ErrorCode("TSC-W008", "doc_count consistency violation — item '{item_id}' has doc_count={doc_count} but len(item_description)={tag_set_count} per FR-82 architect lock 2026-06-20", True),
]

for _code in _TSC_CODES:
    register_code(_code)
