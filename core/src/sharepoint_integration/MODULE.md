# Module: sharepoint_integration

**Purpose**: All SharePoint 2017 REST API interaction for HILDA — entity CRUD on SP Lists, NTLM/Kerberos authentication, and the mapping from HILDA's canonical entity fields to customer-deployment-specific SP list names and column names. Serves D-004, D-006, NFR-8, and anchors the SharePointListProvider Protocol pattern `[D-020]`.

*SharePoint scope is frozen at 2017 Lists + classic web parts only — no SPFx, no Power Apps, no Document Libraries per `[D-006]` `[D-013]` NFR-8.*

---

## Architecture

Two orthogonal concerns, composed by `list_crud.py`:

1. **SpClient** — *how to talk to SharePoint*: raw async HTTP over the SP REST API. Handles auth, wire protocol, retry logic. Has no knowledge of HILDA entities or customer config.
2. **SharePointListProvider** — *what to talk about*: pure lookup service — given a HILDA entity type and a scope, returns the SP list name and column map. Makes no HTTP calls.

`list_crud.py` is the only compositor and the only public call site for all other modules.

---

## Public surface

### SpClient

```python
class SpClient:
    """Async SP REST HTTP client. No HILDA model knowledge — takes SP-native names only."""

    def __init__(self, config: GlobalSharePointConfig) -> None: ...

    async def get_list_items(
        self,
        list_name: str,
        select: list[str] | None = None,
        filter_expr: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def create_list_item(
        self, list_name: str, fields: dict[str, Any]
    ) -> str: ...  # returns SP item ID

    async def update_list_item(
        self, list_name: str, item_id: str, fields: dict[str, Any]
    ) -> None: ...

    async def batch_create(
        self, list_name: str, items: list[dict[str, Any]]
    ) -> list[str]: ...  # returns list of SP item IDs

    async def batch_update(
        self, list_name: str, updates: list[tuple[str, dict[str, Any]]]
    ) -> None: ...
```

Auth: NTLM via `requests-ntlm` (sync wrapped in `asyncio.to_thread`) or Kerberos (`requests-kerberos`) — both adapters behind an `_AuthHandler` internal protocol. Selected by `config.auth_type`. Retry: exponential backoff on 429/503, configurable via `config.max_retries` + `config.retry_backoff_seconds`.

### SharePointListProvider Protocol

```python
@dataclass
class ListScope:
    customer_slug: str
    device_slug: str | None = None  # non-None triggers device-level override lookup

class SharePointListProvider(Protocol):
    """Pure lookup service — no HTTP, no side effects."""

    def get_list_name(self, entity: str, scope: ListScope) -> str:
        """Returns SP list name for the given HILDA entity + customer/device scope.
        Raises SHP-E002 if no mapping found for scope."""

    def get_column_map(self, entity: str, scope: ListScope) -> dict[str, str]:
        """Returns {canonical_field: sp_internal_column_name} for the scope."""

    def to_sp_fields(
        self, entity: str, scope: ListScope, canonical: dict[str, Any]
    ) -> dict[str, Any]:
        """Translates a dict of canonical field names → SP internal column names.
        Raises SHP-E003 for unmapped required canonical fields."""
```

Entities: `"devices"`, `"milestones"`, `"deliverables"`, `"delivery_items"`, `"templates"`, `"automation_rules"`, `"communication_log"`.

### FileBasedListProvider

Boilerplate implementation shipped in `core/`. Reads from `customizations/sharepoint_config/` at startup. Implements the 3-tier scope lookup: device override → customer config → `SHP-E002`.

```python
class FileBasedListProvider:
    """Implements SharePointListProvider by reading YAML files from customizations/."""

    def __init__(self, config_base: Path = Path("customizations/sharepoint_config")) -> None: ...
    # loads customers/<slug>.yaml and devices/special_devices.yaml at init
```

Customer YAML shape (`customizations/sharepoint_config/customers/<customer_slug>.yaml`):
```yaml
customer_slug: carrier-alpha
lists:
  devices:
    name: "CA - Device Tracker"
    columns:
      device_name: "Title"
      assigned_pm_id: "PM_Owner"
      target_launch_date: "Target_x0020_Launch_x0020_Date"
  delivery_items:
    name: "CA - Delivery Items"
    columns:
      item_name: "Title"
      delivery_state: "Status"
      expected_completion_date: "Expected_x0020_Completion_x0020_Date"
      owner_email: "Owner_x0020_Email"
```

Device override YAML shape (`customizations/sharepoint_config/devices/special_devices.yaml`):
```yaml
device_overrides:
  - customer_slug: carrier-alpha
    device_slug: special-device-x
    entity: delivery_items
    list_name: "CA - SpecialDev-X Items"
    # column map inherits from customer config unless overridden here
    columns: {}
```

### list_crud.py — compositor (canonical call site)

All HILDA modules call this; none call `SpClient` or `SharePointListProvider` directly.

```python
class SpCrud:
    def __init__(self, client: SpClient, provider: SharePointListProvider) -> None: ...

    async def get_items(
        self,
        entity: str,
        scope: ListScope,
        canonical_filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Returns items as canonical-field dicts (SP column names translated back)."""

    async def create_item(
        self, entity: str, scope: ListScope, canonical_fields: dict[str, Any]
    ) -> str: ...  # returns SP item ID

    async def update_item(
        self, entity: str, scope: ListScope, item_id: str, canonical_fields: dict[str, Any]
    ) -> None: ...

    async def batch_create(
        self, entity: str, scope: ListScope, items: list[dict[str, Any]]
    ) -> list[str]: ...

    async def batch_update(
        self, entity: str, scope: ListScope, updates: list[tuple[str, dict[str, Any]]]
    ) -> None: ...
```

### Configuration

**Operational (nora 3-tier: CLI arg → env var → `config/sharepoint_integration.json`):**

```python
class GlobalSharePointConfig(BaseModel):
    site_url:               str                          # e.g. "https://sp2017.corp/sites/hilda"
    auth_type:              Literal["ntlm", "kerberos"]
    username:               str | None = None            # NTLM only; from env HILDA_SP_USER
    password:               str | None = None            # NTLM only; from env HILDA_SP_PASS (never logged)
    keytab_path:            Path | None = None           # Kerberos only
    timeout_seconds:        int   = 30
    max_retries:            int   = 3
    retry_backoff_seconds:  float = 1.0
    page_size:              int   = 100                  # $top for list queries
```

**Business config (not nora 3-tier — lives in `customizations/`, loaded by `FileBasedListProvider`):**
Customer-specific SP list names and SP internal column names live in `customizations/sharepoint_config/` YAML files (see FileBasedListProvider above). These are customer-deployment facts, not environment-switching concerns — they do not belong in `config/sharepoint_integration.json`.

---

## Invariants

- **SpClient has no HILDA model knowledge.** It takes SP-native list names and SP internal column names only. HILDA entity routing is `SharePointListProvider`'s responsibility.
- **SharePointListProvider makes no HTTP calls.** Pure config lookup — testable without a SP instance.
- **All callers use `SpCrud` (list_crud.py), never SpClient or SharePointListProvider directly.** One compositor, one public surface.
- **No proprietary content in compact reports or error messages.** Error codes emit entity names and scope slugs only — no SP list names, no SP column names, no customer data values. Anchors NFR-2, `[D-002]`.
- **`config/sharepoint_integration.json` holds operational values only** (site URL, auth type, timeouts). Customer SP list names and column maps belong in `customizations/sharepoint_config/`, not in `config/`.
- **SP password never logged.** `GlobalSharePointConfig.password` is excluded from all `__repr__`, `__str__`, and compact report emissions.
- **SP 2017 Lists + classic web parts only.** No Document Libraries, no SPFx endpoints, no `/_api/v2.0/` Graph-compatibility surface. Anchors NFR-8 `[D-006]`.

---

## Key choices

- **`[D-004]`** — SharePoint integration split: standard API mechanics in `core/`; deployment-specific SP list names + column maps in `customizations/`. This module is the authority for that split.
- **`[D-006]`** — SP REST API + on-prem AD auth (NTLM/Kerberos). No Microsoft Graph.
- **`[D-020]`** — SharePointListProvider Protocol pattern: SpClient owns the "how"; SharePointListProvider owns the "what". FileBasedListProvider boilerplate ships in `core/`; `customizations/` provides the YAML data. `list_crud.py` is the sole compositor.
- **NTLM sync-wrapped vs. native async** — `requests-ntlm` + `asyncio.to_thread` chosen over a native async NTLM implementation. The SP 2017 auth handshake is synchronous at the protocol level; wrapping is simpler and test-equivalent. Revisit if throughput becomes a bottleneck.
- **Page_size capped at 100** — SP 2017 REST API default page size is 100; HILDA queries use explicit `$top` with server-side paging for any query that may return >100 results. SpClient handles pagination internally.

---

## Non-goals

- Not a Document Library client — file storage is `storage` module (NW drive via SMB) per `[D-013]`.
- Not a SharePoint provisioning tool — List schema creation, web part deployment, and site provisioning are ops-time activities outside `core/`.
- Not a search client — no `/_api/search` surface; HILDA queries by known List + OData filter only.
- Not a GraphQL / Graph API client — SP 2017 only.
- Not responsible for PM credential management — credentials come from `credential_service` per `[D-019]`; `GlobalSharePointConfig` holds the service-account (NTLM/Kerberos) for List access, not per-PM credentials.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate` (SHP error codes registered in `error_codes.py`).
- `template_schema` — `CustomerSchema` (used at startup to validate SP column map coverage against canonical fields); `ListScope` co-located here but typed against `customer_slug` / `device_slug` from `template_schema`.

---

## Depended on by

`tracker`, `dashboard`, `email_service` (CommunicationLog writes), `rule_engine`, `workflow_engine`. Build-time: none.

---

## Test interface

```
python -m core.src.sharepoint_integration.sharepoint_integration_cli --diagnostic
```
Connects to SP (using config), reads one item from each registered list name in `customizations/sharepoint_config/customers/*.yaml`, emits `SHP-RPT`:
```
RPT|SHP|run-00001|2026-05-04T10:00:00Z|customers_configured=1|lists_reachable=7|lists_unreachable=0|auth_type=ntlm
```

```
python -m core.src.sharepoint_integration.sharepoint_integration_cli --mock
```
Starts a local `httpx.MockTransport`-backed SP stub. All `SpCrud` call sites can be tested without a real SP instance. Emits `SHP-RPT` with `mock=true`.

```
python -m core.src.sharepoint_integration.sharepoint_integration_cli --dry-run --customer <slug>
```
Logs all SP operations that would be performed for the given customer scope but performs no writes. Emits `SHP-MET`:
```
MET|SHP|run-00001|2026-05-04T10:00:00Z|customer=carrier-alpha|lists_validated=7|columns_mapped=42|missing_columns=0
```

**Error codes (SHP prefix — registered in `diagnostics/error_codes.py`):**
```
SHP-E001  SP REST call failed: list '{list}' — HTTP {status}: {sp_error_code}
SHP-E002  No list mapping found for entity '{entity}' in scope customer='{c}' device='{d}'
SHP-E003  Canonical field '{field}' has no SP column mapping for entity '{entity}' scope '{c}'
SHP-E004  SP auth failed ({auth_type}): check credentials / keytab
SHP-E005  SP page size exceeded without next-link: entity '{entity}' scope '{c}' — truncation risk
SHP-W001  SP list '{list}' returned 0 items — scope customer='{c}' device='{d}' (expected non-empty)
SHP-W002  Column map coverage for '{entity}' scope '{c}': {n} canonical fields unmapped (optional)
```

**QC template** (`SHP:list_coverage` — registered in `diagnostics/qc.py`):
```
Fields: lists_reachable (int), lists_unreachable (int), columns_mapped (int),
        missing_required_columns (int), result (enum: OK / WARN / FAIL)
```

---

<!-- BEGIN:STRUCTURE -->
<!-- END:STRUCTURE -->
