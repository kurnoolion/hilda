# Module: diagnostics

**Purpose**: Central registry and schema library for HILDA's chat-mediated collaboration surface. Owns the error-code prefix registry, the four compact report types (RPT / MET / FIX / QC), and the fixed-field QC template base class. Every module in the system imports from here; this module imports nothing from HILDA. Serves NFR-17, NFR-18, and the no-proprietary-content invariant across all modules (`[D-002]`).

---

## Public surface

### `error_codes.py`

```python
class ErrorSeverity(str, Enum):
    ERROR = "E"
    WARNING = "W"

@dataclass(frozen=True)
class ErrorCode:
    code: str           # e.g. "EML-E001" — format: {PREFIX}-{E|W}{NNN}
    message: str        # human-readable template; may contain {placeholders}
    recoverable: bool   # True = warning/retry candidate; False = hard failure

# Pre-registered prefix → module name mapping.
# Every module registers its prefix here before its first error code.
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

# Central registry: all error codes across all modules live here.
# Format: "{PREFIX}-{E|W}{NNN}" → ErrorCode(...)
ERROR_CODES: dict[str, ErrorCode] = {
    # --- diagnostics (DGN) ---
    "DGN-E001": ErrorCode("DGN-E001", "Duplicate prefix '{prefix}' registered by '{module}'", False),
    "DGN-E002": ErrorCode("DGN-E002", "Unknown error code '{code}' referenced", False),
    "DGN-W001": ErrorCode("DGN-W001", "Module '{module}' has no QC template registered", True),
    # --- all other module codes added as each MODULE.md is drafted ---
}

def get_code(code: str) -> ErrorCode:
    """Raises DGN-E002 if code is not registered."""

def format_code(code: str, **kwargs: str) -> str:
    """Returns formatted message string for the given code."""
```

### `report.py`

```python
class ReportType(str, Enum):
    RPT = "RPT"   # run / activity report — what happened, pass/fail, item counts
    MET = "MET"   # metrics — timing, rates, queue depths, retry counts
    FIX = "FIX"   # PM corrections — what a human changed and why
    QC  = "QC"    # quality check — fixed-field, numbers + Y/N + bounded enum tokens

@dataclass
class ReportRecord:
    report_type: ReportType
    module_prefix: str          # 3-letter prefix from PREFIX_REGISTRY
    run_id: str                 # stable run/session identifier (UUIDv4 or CLI-supplied)
    timestamp: datetime         # UTC
    fields: dict[str, int | float | bool | str]
    # str values MUST be bounded enum tokens or error codes — never free prose.
    # Enforced at runtime by ReportWriter when QCTemplate is registered.

    def to_line(self) -> str:
        """Serializes to a single pasteable line:
        RPT|EML|run-abc123|2026-05-04T10:00:00Z|batch_count=12|parse_ok=10|parse_fail=2
        """

    @classmethod
    def from_line(cls, line: str) -> "ReportRecord":
        """Inverse of to_line; raises DGN-E002 on parse failure."""

class ReportWriter:
    """Collects ReportRecords and flushes to stdout or file.
    Use as a context manager; emits on __exit__ or explicit flush().
    """
    def __init__(self, module_prefix: str, run_id: str) -> None: ...
    def emit(self, record: ReportRecord) -> None: ...
    def flush(self, dest: TextIO = sys.stdout) -> None: ...
```

### `qc.py`

```python
_ALLOWED_FIELD_TYPES = {"int", "float", "bool", "enum"}

@dataclass
class QCField:
    name: str
    field_type: Literal["int", "float", "bool", "enum"]
    enum_values: tuple[str, ...] | None = None  # required when field_type == "enum"

    def __post_init__(self) -> None:
        # Raises TypeError if field_type not in _ALLOWED_FIELD_TYPES.
        # Raises TypeError if field_type == "enum" and enum_values is None.
        # This is the compile-time no-free-text enforcement.

class QCTemplate:
    """Fixed-field QC template for one artifact type within a module.
    Registered globally so diagnostics_cli --validate can surface missing templates.
    """
    module_prefix: str
    artifact_type: str    # e.g. "email_batch", "test_report_parse", "adapter_generate"
    fields: tuple[QCField, ...]

    def validate_record(self, record: ReportRecord) -> list[str]:
        """Returns list of validation errors; empty = valid."""

    def sample_line(self) -> str:
        """Emits a sample QC line with placeholder values — for MODULE.md documentation."""

    @classmethod
    def register(cls, template: "QCTemplate") -> None:
        """Registers template in the global QC_REGISTRY. Called at module import time."""

QC_REGISTRY: dict[str, QCTemplate] = {}  # key: "{prefix}:{artifact_type}"
```

---

## Invariants

- **No HILDA imports.** `diagnostics` imports only stdlib + third-party (dataclasses, enum, datetime, typing, sys). Any HILDA import creates a cycle and is a hard error.
- **No free-text in report fields.** `str` fields in `ReportRecord` must contain only bounded enum tokens or registered error codes. `QCTemplate` enforces this at class definition time via `QCField.__post_init__`. `ReportWriter` enforces it at emit time when a `QCTemplate` is registered for the module.
- **No proprietary content.** Applies to all report output by construction — field types are int / float / bool / enum; no mechanism exists to embed arbitrary strings. Anchors `[D-002]` NFR-17 NFR-2.
- **All 21 prefixes pre-registered** (revised 2026-06-21 from 20 — added `STATUS` meta-prefix for cross-cutting `HildaOpsAlert` codes owned by diagnostics per FR-75 + [D-002]; 2026-06-09 revision from 18 added `CMG` / `CPG` for corp-side gateway modules per SYSTEM.md §2.1 2026-05-24 expansion). `PREFIX_REGISTRY` and `ERROR_CODES` are populated as each MODULE.md is drafted. The `--validate` flag fails if a prefix appears in `ERROR_CODES` without a `PREFIX_REGISTRY` entry.
- **Error codes are stable across deployments.** Once a code is registered with a number, neither the number nor the message template may change. Deprecations add a `deprecated: True` field; codes are never renumbered or deleted.

---

## Key choices

- **`[D-002]`** — compact report schema + no-proprietary-content invariant; this module is the concrete implementation of that decision.
- **`[D-017]`** — standalone leaf-node module (Option A); justification for not embedding this inline per module.

---

## Non-goals

- Not a logging framework — modules use stdlib `logging`; `diagnostics` provides the compact pasteable schema for the AI-collaboration surface, not runtime log management.
- Not a metrics store — Prometheus / OpenTelemetry handle time-series metrics; `diagnostics` defines what to measure (MET records), not where to store it.
- Not a tracing system — distributed tracing (e.g. Jaeger via OpenTelemetry) is separate infrastructure; `run_id` in `ReportRecord` serves as a correlation handle but is not a span.

---

## Depends on

*(none — leaf node)*

---

## Depended on by

All 19 other modules (revised 2026-06-09 from 17 — added `corp_messenger_gateway` + `corp_plm_gateway` corp-side gateway modules per SYSTEM.md §2.1 2026-05-24 expansion): `template_schema`, `sharepoint_integration`, `storage`, `credential_service`, `email_service`, `issue_tracker`, `messenger`, `customer_adapter`, `tracker`, `test_report`, `rule_engine`, `llm`, `workflow_engine`, `api_spec_ingestor`, `template_schema_ingestor`, `test_report_profiler`, `dashboard`, `corp_messenger_gateway`, `corp_plm_gateway`.

---

## Test interface

```
python -m core.src.diagnostics.diagnostics_cli --diagnostic
```
Emits a `DGN-RPT` record listing all registered prefixes, module names, and per-module error-code counts. Safe to run in any environment; reads only in-process registry state.

```
python -m core.src.diagnostics.diagnostics_cli --validate
```
Emits a `DGN-QC` record with fields:
- `prefix_count: int` — total prefixes in `PREFIX_REGISTRY`
- `code_count: int` — total entries in `ERROR_CODES`
- `duplicate_prefixes: bool` — True = collision found (DGN-E001 raised)
- `orphan_codes: bool` — True = a code's prefix not in `PREFIX_REGISTRY` (DGN-E002 raised)
- `missing_qc_templates: bool` — True = a module has no `QCTemplate` registered (DGN-W001 raised)
- `result: enum[OK, WARN, FAIL]`

No `--mock` or `--dry-run` needed — no side effects, no network, no disk writes.

**Sample `--diagnostic` output** (pasteable into chat):
```
RPT|DGN|run-00001|2026-05-04T10:00:00Z|prefix_count=21|code_count=3|modules=DGN,TSC,SHP,STO,CRD,EML,ITR,MSG,CSA,TRK,TRC,RUL,LLG,WFL,ASI,TSI,TRP,DSH,CMG,CPG,STATUS
```

**Sample `--validate` output**:
```
QC|DGN|run-00001|2026-05-04T10:00:00Z|prefix_count=21|code_count=3|duplicate_prefixes=false|orphan_codes=false|missing_qc_templates=true|result=WARN
```

---

<!-- BEGIN:STRUCTURE -->
### `diagnostics_cli.py`
- `main(argv=None) -> int` — function — pub — CLI entrypoint: `--diagnostic` emits DGN-RPT (prefix_count, code_count, modules); `--validate` runs QC over registries.

### `error_codes.py`
- `ERROR_CODES` — module constant — pub (via `__all__`) — Registered error-code dict; seeded with DGN-/ITR-/CRD- codes.
- `ErrorCode` — frozendataclass — pub (via `__all__`) — Immutable error definition (code, message, recoverable).
- `ErrorSeverity` — Enum — pub (via `__all__`) — Severity discriminator (E/W).
- `PREFIX_REGISTRY` — module constant — pub (via `__all__`) — 21-entry prefix→module map (DGN, TSC, SHP, STO, CRD, EML, ITR, MSG, CSA, TRK, TRC, RUL, LLG, WFL, ASI, TSI, TRP, DSH, CMG, CPG, STATUS).
- `PipelineError` — class (Exception) — pub (via `__all__`) — Structured error carrying registered code + context dict + optional cause.
- `format_code(code, **kwargs) -> str` — function — pub (via `__all__`) — Format a registered code's message with placeholder values.
- `get_code(code) -> ErrorCode` — function — pub (via `__all__`) — Lookup; raises DGN-E002 if unknown.
- `register_code(code) -> None` — function — pub (via `__all__`) — Idempotent register; raises DGN-E001 on unknown prefix, ValueError on definition conflict.

### `qc.py`
- `QC_REGISTRY` — module constant — pub (via `__all__`) — Registered QCTemplate dict keyed by `<prefix>:<artifact_type>`.
- `QCField` — frozendataclass — pub (via `__all__`) — Single QC field spec (name, field_type ∈ {int,float,bool,enum}, enum_values).
- `QCTemplate` — frozendataclass — pub (via `__all__`) — Fixed-field QC schema; `validate_record(rec) -> list[str]` enforces no-free-text invariant; `sample_line()` renders skeleton.
- `register_template(template) -> None` — function — pub (via `__all__`) — Idempotent template register; raises ValueError on definition conflict.

### `report.py`
- `ReportRecord` — dataclass — pub (via `__all__`) — Compact-report row (type, prefix, run_id, timestamp, fields); `to_line()` / `from_line()` round-trip.
- `ReportType` — Enum — pub (via `__all__`) — RPT / MET / FIX / QC discriminator.
- `ReportWriter` — class — pub (via `__all__`) — Buffered compact-report emitter; `emit`/`make`/`flush`; context-manager support.
<!-- END:STRUCTURE -->
