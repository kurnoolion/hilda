"""SHP-prefixed error codes for sharepoint_integration."""
from __future__ import annotations

from core.src.diagnostics import ErrorCode, register_code

_SHP_CODES = [
    ErrorCode("SHP-E001", "SP REST call failed: list '{list}' — HTTP {status}: {sp_error_code}", False),
    ErrorCode("SHP-E002", "No list mapping found for entity '{entity}' in scope customer='{customer}' device='{device}'", False),
    ErrorCode("SHP-E003", "Canonical field '{field}' has no SP column mapping for entity '{entity}' scope '{customer}'", False),
    ErrorCode("SHP-E004", "SP auth failed ({auth_type}): check credentials / keytab", False),
    ErrorCode("SHP-E005", "SP page size exceeded without next-link: entity '{entity}' scope '{customer}' — truncation risk", False),
    ErrorCode("SHP-W001", "SP list '{list}' returned 0 items — scope customer='{customer}' device='{device}' (expected non-empty)", True),
    ErrorCode("SHP-W002", "Column map coverage for '{entity}' scope '{customer}': {n} canonical fields unmapped (optional)", True),
]

for _code in _SHP_CODES:
    register_code(_code)
