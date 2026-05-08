# Module: issue_tracker

**Purpose**: Implements the `IssueTracker` Protocol per `[D-008]` — the internal issue-tracking integration for DeliveryItems whose `tracking_modality = "InternalIssueTracker"`. Ships the Jira adapter (public Jira REST API, `core/src/issue_tracker/jira_adapter.py`) and a `MockIssueTracker` for tests. Proprietary adapters live in `customizations/issue_tracker/` and are loaded via `load_adapter()`. Exports `AttachmentInput` as the cross-module attachment primitive used by `messenger`. Serves FR-25, FR-26, FR-42, NFR-17, NFR-18, anchors `[D-003]` `[D-008]`.

**Workload assignment**: `hilda-worker` executes all async IssueTracker operations (create, update, transition, close, comment, upload, search, poll). `hilda-api` hosts the webhook ingestion endpoint `POST /webhooks/issue-tracker/<adapter>` that enqueues inbound events to `hilda-worker` via Redis.

---

## Public surface

### `protocol.py`

```python
AttachmentInput = Path | AsyncIterable[bytes]
# Cross-module primitive — re-exported from core.src.issue_tracker; messenger imports from here.

@dataclass(frozen=True)
class IssueRef:
    issue_id:      str
    source_system: str   # immutable adapter slug, e.g. "jira", "proprietary"
    url:           str   # direct link to the issue in the external system

class IssueStatus(str, Enum):
    OPEN        = "Open"
    IN_PROGRESS = "InProgress"
    RESOLVED    = "Resolved"
    CLOSED      = "Closed"

class IssuePriority(str, Enum):
    LOW      = "Low"
    MEDIUM   = "Medium"
    HIGH     = "High"
    CRITICAL = "Critical"

@dataclass
class Issue:
    ref:         IssueRef
    summary:     str
    description: str | None
    status:      IssueStatus
    priority:    IssuePriority | None
    assignee:    str | None
    labels:      list[str]
    created_at:  datetime
    updated_at:  datetime

@dataclass(frozen=True)
class IssueChange:
    issue_ref:  IssueRef
    field:      str          # e.g. "status", "assignee", "comment_added"
    old_value:  str | None
    new_value:  str | None
    changed_at: datetime
    changed_by: str | None

@dataclass
class IssueQuery:
    project:       str | None = None
    status:        IssueStatus | None = None
    updated_after: datetime | None = None
    assignee:      str | None = None
    labels:        list[str] = field(default_factory=list)

@dataclass(frozen=True)
class CommentRef:
    comment_id:    str
    issue_ref:     IssueRef
    source_system: str

@dataclass(frozen=True)
class AttachmentRef:
    attachment_id: str
    issue_ref:     IssueRef
    filename:      str
    source_system: str

@dataclass(frozen=True)
class WebhookRef:
    webhook_id:    str
    source_system: str
    callback_url:  str

class IssueTracker(Protocol):
    source_system: str   # immutable slug; set at adapter instantiation, never reassigned

    async def create_issue(
        self, project: str, summary: str, description: str,
        fields: dict | None = None,
        attachments: list[AttachmentInput] | None = None,
        idempotency_key: str | None = None,
        timeout_s: float | None = None,
    ) -> IssueRef: ...

    async def get_issue(self, ref: IssueRef, timeout_s: float | None = None) -> Issue: ...

    async def update_issue(
        self, ref: IssueRef, updates: dict, timeout_s: float | None = None
    ) -> None: ...

    async def transition_issue(
        self, ref: IssueRef, transition: str, timeout_s: float | None = None
    ) -> None: ...

    async def close_issue(
        self, ref: IssueRef, resolution: str, timeout_s: float | None = None
    ) -> None: ...

    async def add_comment(
        self, ref: IssueRef, body: str,
        attachments: list[AttachmentInput] | None = None,
        idempotency_key: str | None = None,
        timeout_s: float | None = None,
    ) -> CommentRef: ...

    async def upload_attachment(
        self, ref: IssueRef, file: AttachmentInput, timeout_s: float | None = None
    ) -> AttachmentRef: ...

    async def search(
        self, query: IssueQuery, timeout_s: float | None = None
    ) -> AsyncIterator[IssueRef]: ...

    async def list_recent_changes(
        self, ref: IssueRef, since: datetime, timeout_s: float | None = None
    ) -> AsyncIterator[IssueChange]: ...

    async def register_webhook(
        self, callback_url: str, events: list[str], secret: str,
        timeout_s: float | None = None,
    ) -> WebhookRef: ...

    async def poll_changes(
        self, since: datetime, timeout_s: float | None = None
    ) -> AsyncIterator[IssueChange]: ...
```

### `jira_adapter.py`

```python
@dataclass
class JiraAdapterConfig:
    base_url:       str                        # "https://jira.corp.example.com"
    auth_type:      Literal["api_token", "basic"]
    project_key:    str                        # Jira project key, e.g. "HILDA"
    status_map:     dict[str, str]             # IssueStatus.value → Jira status name
    transition_map: dict[str, str]             # HILDA canonical verb → Jira transition name/ID

    @classmethod
    def from_env(cls, prefix: str = "HILDA_JIRA_") -> "JiraAdapterConfig":
        """Reads HILDA_JIRA_BASE_URL, HILDA_JIRA_AUTH_TYPE, HILDA_JIRA_PROJECT_KEY,
        HILDA_JIRA_STATUS_MAP (JSON), HILDA_JIRA_TRANSITION_MAP (JSON).
        Raises ITR-E006 if any required var is absent."""

class JiraAdapter:
    """Implements IssueTracker Protocol against Jira REST API v2 / v3.
    Credentials (api_token or username:password) retrieved from credential_service
    at each call — never stored on the instance after construction.
    Sync Jira REST calls wrapped in asyncio.get_event_loop().run_in_executor().
    """
    source_system: str = "jira"

    def __init__(self, config: JiraAdapterConfig, credential_service: Any) -> None: ...
```

### `mock_adapter.py`

```python
class MockIssueTracker:
    """In-memory IssueTracker for unit and integration tests.
    Implements full Protocol surface. Raises ITR-E002 for unknown refs.
    Uses asyncio.Lock for thread safety.
    All mutating methods honour idempotency_key (dedup within process lifetime).
    """
    source_system: str = "mock"
    issues:  dict[str, Issue]       # keyed by issue_id
    changes: list[IssueChange]      # append-only log
```

### `__init__.py` (re-exports)

```python
from core.src.issue_tracker.protocol import (
    AttachmentInput, IssueRef, Issue, IssueStatus, IssuePriority,
    IssueChange, IssueQuery, CommentRef, AttachmentRef, WebhookRef,
    IssueTracker,
)
```

### Adapter factory (`load_adapter`)

```python
def load_adapter(slug: str, config: dict | None = None) -> IssueTracker:
    """Resolves adapter in priority order:
    1. core/src/issue_tracker/<slug>_adapter.py
    2. customizations/issue_tracker/<slug>_adapter.py
    Each file must export make_adapter(config: dict) -> IssueTracker.
    Raises ITR-E006 if neither path exists.
    """
```

---

## Invariants

- **`source_system` is immutable** — set at adapter instantiation, never reassigned; appears verbatim in every `IssueRef` and `CommentRef` produced by the adapter.
- **Async-native** — all Protocol methods are `async def`; adapters wrapping sync HTTP clients use `run_in_executor`. Callers always get coroutines.
- **Idempotency keys on all mutating methods** — when `idempotency_key` is supplied and was seen before, return the existing ref without creating a duplicate. Keys are deduped in-memory per process lifetime; full persistence deferred to v2.
- **`close_issue` is idempotent** — calling on an already-closed issue returns successfully without error.
- **Transition vocabulary is adapter-defined** — `transition_issue(ref, "resolve")` maps through `transition_map`; core code uses only HILDA canonical verbs; adapters own the translation.
- **No credential material in reports or logs** — credentials retrieved from `credential_service` per call; never stored on adapter instance post-init, never written to `ReportRecord` fields.
- **No proprietary issue content in compact reports** — ITR-RPT fields are counts, status flags, and bounded enum tokens only. Anchors NFR-2.

---

## Error codes (ITR prefix — registered in `diagnostics/error_codes.py`)

```
ITR-E001  Unauthorized: credentials rejected by issue tracker '{system}'
ITR-E002  Issue not found: '{issue_id}' in '{system}'
ITR-E003  Conflict: idempotency key '{key}' already resolved to '{existing_id}'
ITR-E004  Transition '{transition}' not available from current state on '{issue_id}'
ITR-E005  Attachment upload failed for '{issue_id}': {reason}
ITR-E006  Adapter '{slug}' not found in core/ or customizations/
ITR-W001  Rate limited by '{system}'; retry after {retry_after_s}s  (recoverable)
ITR-W002  Webhook registration failed for '{system}'; falling back to poll_changes  (recoverable)
```

---

## Key choices

- **`[D-003]`** — Protocol in `core/`, Jira adapter in `core/`, proprietary adapters in `customizations/issue_tracker/` generated by API Spec Ingestor. Adding an adapter is a `customizations/` drop-in; no `core/` change required.
- **`[D-008]`** — async-native Protocol design. Proprietary trackers expose sync REST or SDK clients; adapters own the `run_in_executor` wrapping, keeping callers (`workflow_engine`, `tracker`) uniform.
- **`AttachmentInput` placement** — defined in `issue_tracker.protocol`, re-exported from `core.src.issue_tracker`; `messenger` imports from here. Avoids a premature shared module for a two-consumer primitive. If a third unrelated module needs it, extract to `core.src.types`.
- **Jira auth** — `api_token` (Jira Cloud or Server with token auth) or `basic` (Jira Server legacy). Config-driven; runtime credentials come from `credential_service.get_credential(pm_id, "issue_tracker")` at call time, not stored on the adapter instance.
- **`--contract` CLI mode** — 10-check contract suite against any registered adapter; emits `ITR-RPT` with pass/fail per check. Primary integration hook between Teacher LLM (authors the adapter scaffold) and Cline (runs `--contract --adapter <slug>` against the real system, pastes ITR-RPT back into Teacher chat for iteration).
- **Adapter discovery order** — `core/` first, then `customizations/`. Slug collision between the two raises ITR-E006 at load time, not at call time.

---

## Non-goals

- Not a full Jira client — only the Protocol surface is wired; Jira features outside the Protocol (epics, sprints, dashboards) are not exposed.
- Not a credentials store — credentials flow through `credential_service` exclusively.
- Not a webhook router — `hilda-api` receives the POST; `register_webhook` / `poll_changes` are the outbound setup primitives only.
- No retry logic beyond `ITR-W001` emission — retry policy belongs to `workflow_engine`.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate` (ITR codes registered in `diagnostics/error_codes.py`).
- `template_schema` — `TrackingModality.INTERNAL_ISSUE_TRACKER` (guard before adapter calls).
- `credential_service` — `get_credential(pm_id, system_type="issue_tracker")` called per request; result never cached on adapter instance.

---

## Depended on by

- `workflow_engine` — calls `create_issue`, `add_comment`, `close_issue`, `transition_issue` in rule-engine-driven flows.
- `tracker` — syncs DeliveryItem status via `list_recent_changes` + `poll_changes`.
- `messenger` — imports `AttachmentInput` from `core.src.issue_tracker.protocol`.

---

## Test interface

```
python -m core.src.issue_tracker.issue_tracker_cli --diagnostic
```
Emits `ITR-RPT` showing loaded adapters and config validation status:
```
RPT|ITR|run-00001|2026-05-08T10:00:00Z|adapters_found=2|adapters_valid=2|adapters_invalid=0
```

```
python -m core.src.issue_tracker.issue_tracker_cli --mock --dry-run --customer <slug>
```
Runs a full create → update → comment → close cycle against `MockIssueTracker`; no external calls. Emits `ITR-RPT`:
```
RPT|ITR|run-00001|2026-05-08T10:00:00Z|adapter=mock|ops_attempted=4|ops_ok=4|ops_fail=0
```

```
python -m core.src.issue_tracker.issue_tracker_cli --contract --adapter <slug>
```
Runs the 10-check contract suite against the named adapter with the real (or test) system.

Contract checks:

| ID  | Check                                          | Methods exercised                   |
|-----|------------------------------------------------|-------------------------------------|
| C01 | Round-trip: create → get → verify fields       | `create_issue`, `get_issue`         |
| C02 | Update: set fields → get → verify changed      | `update_issue`, `get_issue`         |
| C03 | Transition: valid state change accepted        | `transition_issue`, `get_issue`     |
| C04 | Close idempotent: close twice → no error       | `close_issue` × 2                   |
| C05 | Comment: add → retrievable via get_issue       | `add_comment`, `get_issue`          |
| C06 | Attachment: upload → ref returned              | `upload_attachment`                 |
| C07 | Search: query returns the created issue        | `search`                            |
| C08 | Changes: list_recent_changes since T₀          | `list_recent_changes`               |
| C09 | Idempotency: create twice same key → one issue | `create_issue` × 2                  |
| C10 | Error surface: unknown ref → ITR-E002          | `get_issue` with unknown ref        |

Emits `ITR-RPT`:
```
RPT|ITR|run-00001|2026-05-08T10:00:00Z|adapter=jira|tests=10|passed=10|failed=0|fail_methods=
```
On failure:
```
RPT|ITR|run-00001|2026-05-08T10:00:00Z|adapter=proprietary|tests=10|passed=7|failed=3|fail_methods=C03,C08,C09
```

**QC template** (`ITR:adapter_contract` — registered in `diagnostics/qc.py`):
```
Fields: tests (int), passed (int), failed (int),
        fail_methods (enum: C01|C02|C03|C04|C05|C06|C07|C08|C09|C10 pipe-separated, or empty)
```

---

<!-- BEGIN:STRUCTURE -->
<!-- END:STRUCTURE -->
