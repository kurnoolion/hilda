# Module: sharepoint_integration

> **Rollback log:**
> - **2026-06-10 (drift fixes + FR-84 / FR-87 / column-map cascade alignment)** — A-tier drift fixes against current Structure block: 8-list canonical entity set per `[D-051]` (was stale 7-list); `SpCrud.delete_item` added to declared Public surface; `SharePointListProvider.from_sp_fields` added to Protocol; `mock_server/` sub-module documented (FastAPI SP stub + `--serve --port` CLI mode); sample line counts bumped 7 → 8. B-tier additions reflecting recent session work: FR-84 outbound writeback invariant (SP→HILDA HTTP firewall-blocked; HILDA→SP REST is the sole HILDA-initiated writeback channel); FR-87 TPM resolution writeback path invariant (SP-UI-button → HILDA-REST → HILDA-DB → SpCrud.update_item, strict A→B→C ordering); column-map append-only invariant for the 2026-06-08 cascade fields (target_folder / no_customer_upload / FR-87 TPM resolution fields on DeliveryItems; ingress_nsd / folder_routing_enabled / tracking_enabled on TGGroups); SP Choice-field value sync added to Non-goals (SP UI engineer owns Choice value updates when HILDA enums change — e.g., 4-value ItemType per `[D-053]`); SP-alert email channel added to Non-goals (owned by `email_service` per FR-84). C-tier polish: Depended-on-by extended (`issue_tracker`, `customizations/issue_tracker`, indirect-via-workflow_engine `customer_adapter`); two-halves-of-the-same-conversation positioning note for SP UI engineer collaboration. Anchors `[D-051]` (8-list framing), `[D-053]` (4-value ItemType), FR-84 (SP-HILDA channel discipline), FR-87 (TPM resolution).

**Purpose**: All SharePoint 2017 REST API interaction for HILDA — entity CRUD on SP Lists, NTLM/Kerberos authentication, and the mapping from HILDA's canonical entity fields to customer-deployment-specific SP list names and column names. Serves D-004, D-006, NFR-8, and anchors the SharePointListProvider Protocol pattern `[D-020]`.

*SharePoint scope is frozen at 2017 Lists + classic web parts only — no SPFx, no Power Apps, no Document Libraries per `[D-006]` `[D-013]` NFR-8.*

*This module is list-agnostic by design per `[D-020]` — it CRUDs any list named by `FileBasedListProvider` via `customizations/sharepoint_config/<deployment>.yaml`. The 8 SP lists in scope per `sharepoint/REQUIREMENTS.md §2` (2026-05-26: Customers, Devices, Milestones, DeliveryItems, Users, PMCredentials, CommunicationLog, TGGroups per `[D-051]`) are all served by the same SpClient + SpCrud + SharePointListProvider stack without per-list code. Adding a new SP list in a future deployment is a config-only change in customizations/sharepoint_config/.*

---

## Architecture

Two orthogonal concerns, composed by `list_crud.py`:

1. **SpClient** — *how to talk to SharePoint*: raw async HTTP over the SP REST API. Handles auth, wire protocol, retry logic. Has no knowledge of HILDA entities or customer config.
2. **SharePointListProvider** — *what to talk about*: pure lookup service — given a HILDA entity type and a scope, returns the SP list name and column map. Makes no HTTP calls.

`list_crud.py` is the only compositor and the only public call site for all other modules.

**Sub-module: `mock_server/`** — local FastAPI-backed SP stub for SP-less dev + integration tests. `mock_server/store.py:InMemoryStore` (thread-safe, list-addressed by display name, monotonic per-list item IDs, audit log) + `mock_server/app.py:build_app(store)` expose the SP 2017 REST surface that `SpClient` consumes + an HTML browser UI for manual data inspection. Started via `sharepoint_integration_cli --serve --port <N>`. Not used in production; not under the `[D-006]` SP-2017-only invariant — the mock surface is a test-time convenience that the real SP 2017 box also supports.

**Two halves of the same conversation**: SP UI engineer (corp-side server work — SP List definitions, classic web-parts, JS forms, SP-alert email config, FR-87 TPM resolution buttons) + this module (HILDA-side client over SP REST + customer-deployment-specific list/column maps in `customizations/`). Neither half stands alone; column-map YAML changes here must be matched by SP UI engineer's SP-side list-schema work and vice versa.

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

    def from_sp_fields(
        self, entity: str, scope: ListScope, sp_dict: dict[str, Any]
    ) -> dict[str, Any]:
        """Inverse translation: SP internal column names → canonical field names.
        Used by SpCrud.get_items to return canonical-shaped results to callers."""
```

Entities (8-list canonical set per `[D-051]` — updated 2026-06-10 from prior 7-list stale set): `"customers"`, `"devices"`, `"milestones"`, `"delivery_items"`, `"users"`, `"pm_credentials"`, `"communication_log"`, `"tg_groups"`.

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

    async def delete_item(
        self, entity: str, scope: ListScope, item_id: str
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
- **HILDA→SP REST is the sole HILDA-initiated state writeback channel per FR-84** (added 2026-06-10). SP→HILDA HTTP is **unconditionally firewall-blocked** on the corp network — SP→HILDA flows reach HILDA only via SP-alert emails (owned by `email_service`, not this module). Every HILDA-side state mutation that PMs/TPMs need to see in SP UI (most importantly `DeliveryItem.delivery_state` transitions and `CommunicationLog` rows) must flow through `SpCrud.update_item` / `SpCrud.create_item` here. SP UI's per-§8.1 5–10s REST polling picks up the writes.
- **FR-87 TPM resolution writeback path** (added 2026-06-10; corrected 2026-06-10 per drift-check `[DRIFT-4]`) — SP UI TPM-resolution buttons (§4.9 Reassign Work-Item, §4.10 Resolve doc_type, §4.11 Resolve revision) MUST be invoked in **strict order A → B → C** per FR-87 — across the three resolution steps (not per-step ordering): **(A)** Reassign work-item must complete before **(B)** Resolve doc_type before **(C)** Resolve revision. **Per-step path** (per FR-84 + `[D-047]` + `[D-064]`): SP-UI-button → SP-field-write → SP-alert email → `email_service.sp_alert_parser` → HILDA Celery dispatch → HILDA DB state mutation → `SpCrud.update_item` writes resolution fields (`tpm_reassignment_target_item_id`, `tpm_resolved_doc_type`, `tpm_revision_resolution`) back to SP → SP UI focus-aware refresh surfaces the result. **SP UI never calls hilda-api directly** (firewall-blocked per FR-84) — every TPM-resolution action round-trips through the SP-alert email channel `[D-047]` and returns via `[D-064]` writeback.
- **Column maps are append-only** (added 2026-06-10) — when HILDA adds canonical fields (e.g., the 2026-06-08 cascade added `target_folder`, `no_customer_upload`, FR-87 TPM-resolution fields on `DeliveryItems`; `ingress_nsd`, `folder_routing_enabled`, `tracking_enabled` on `TGGroups`), the SP UI engineer adds the corresponding SP columns + the customer YAML extends the `columns:` block. No code change in this module — list-agnosticism per `[D-020]` makes the addition mechanical.

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
- **Not a SP Choice-field value synchronizer** (added 2026-06-10). When HILDA enums change (e.g., 4-value `ItemType` per `[D-053]` 2026-06-08 — Confirmation / TestReport / TechReport / Waiver + Default; 5-value `DocType`; `delivery_state` 8-state machine), this module does NOT introspect or push allowed values to SP Choice columns. The column map (`columns: {item_type: "Item_x0020_Type"}`) tells the client how to address the column; the SP UI engineer updates the SP Choice field's allowed values + UI rendering on the SP side. Mismatches surface as SP API errors → `SHP-E001`, not as silent dropped data.
- **Not an SP-alert email receiver** — SP→HILDA via SP-alert email is owned by `email_service` per FR-84. This module owns only the HILDA→SP REST direction.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate` (SHP error codes registered in `error_codes.py`).
- `template_schema` — `CustomerSchema` (used at startup to validate SP column map coverage against canonical fields); `ListScope` co-located here but typed against `customer_slug` / `device_slug` from `template_schema`.

---

## Depended on by

`tracker`, `dashboard`, `email_service` (CommunicationLog writes for inbound/outbound email channel), `rule_engine`, `workflow_engine` (FR-84 delivery_state writeback + FR-87 TPM-resolution writeback + CommunicationLog rows for `customer_adapter` carrier uploads), `issue_tracker` (CommunicationLog writes for PLM upload + ITR-W003 PLM-Carrier hash-mismatch surfacing), `customizations/issue_tracker` (`defecttrack_adapter` CommunicationLog writes). Indirect via `workflow_engine` (no direct dependency): `customer_adapter`. Build-time: none.

---

## Test interface

```
python -m core.src.sharepoint_integration.sharepoint_integration_cli --diagnostic
```
Connects to SP (using config), reads one item from each registered list name in `customizations/sharepoint_config/customers/*.yaml`, emits `SHP-RPT`:
```
RPT|SHP|run-00001|2026-06-10T10:00:00Z|customers_configured=1|lists_reachable=8|lists_unreachable=0|auth_type=ntlm
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
MET|SHP|run-00001|2026-06-10T10:00:00Z|customer=carrier-alpha|lists_validated=8|columns_mapped=58|missing_columns=0
```

```
python -m core.src.sharepoint_integration.sharepoint_integration_cli --serve --port <N>
```
Starts the FastAPI-backed `mock_server/` SP stub on the given port — exposes the SP 2017 REST surface that `SpClient` consumes plus an HTML browser UI for manual data inspection. Use for SP-less local dev and integration tests; not a production surface. **Exercised end-to-end** via `email_service` Ph-1 happy-path test scenario — that scenario drives the full state machine (Open → OutreachSent → DocumentReceived → OwnerClosed → UnderPMReview → ReadyForSubmission → SubmittedToCustomer → Closed) and each transition writes through `SpCrud.update_item` to verify the writeback path under realistic load.

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
### `auth.py`
- `KerberosAuthHandler` — class — pub — Kerberos/SPNEGO handler; raises SHP-E004 until corp AD lab + httpx-native adapter wired.
- `NoAuthHandler` — class — pub — Pass-through handler for mock-server dev.
- `NtlmAuthHandler` — class — pub — NTLM handler via httpx-ntlm; falls back to SHP-E004 when no httpx adapter available.
- `make_handler(config) -> _AuthHandler` — function — pub — Factory: picks NoAuth/Ntlm/Kerberos from `config.auth_type`; raises SHP-E004 on misconfig.

### `config.py`
- `GlobalSharePointConfig` — Pydantic BaseModel — pub (via `__all__`) — Operational SP config (site_url, auth_type, creds, timeouts, page_size); secret-redacted `__repr__`; `from_sources(config_path, cli_overrides, env_prefix)` 3-tier loader.
- `ListScope` — frozendataclass — pub (via `__all__`) — Lookup scope (customer_slug, optional device_slug for override path).

### `error_codes.py`
- (no public top-level names — registers SHP-E001..E004 + SHP-W001 on import via `register_code` side-effect.)

### `list_crud.py`
- `SpCrud` — class — pub (via `__all__`) — Sole public CRUD compositor over SpClient + SharePointListProvider; canonical-in / canonical-out; `get_items/create_item/update_item/delete_item/batch_create/batch_update`.

### `list_provider.py`
- `FileBasedListProvider` — class — pub (via `__all__`) — YAML-backed SharePointListProvider; reads customer + device-override files from `customizations/sharepoint_config/`; raises SHP-E002 on scope miss.
- `SharePointListProvider` — Protocol — pub (via `__all__`) — Pure lookup: `get_list_name`, `get_column_map`, `to_sp_fields`, `from_sp_fields`.

### `mock_server/app.py`
- `build_app(store=None) -> FastAPI` — function — pub — Builds the mock SP FastAPI app exposing SP 2017 REST + HTML browser UI over a shared `InMemoryStore`.

### `mock_server/store.py`
- `InMemoryStore` — dataclass — pub — Thread-safe in-memory list store backing the mock server; lists addressed by display name; monotonic per-list item IDs; audit log.
- `ListNotFoundError` — class (KeyError) — pub — Raised when a list referenced by name does not exist.

### `sharepoint_integration_cli.py`
- `DEFAULT_BASE` — module constant — pub — Default `customizations/sharepoint_config` path.
- `DEFAULT_CONFIG` — module constant — pub — Default `config/sharepoint_integration.json` path.
- `main(argv=None) -> int` — function — pub — CLI entrypoint: `--diagnostic` / `--mock` / `--dry-run --customer` / `--serve --port` modes.

### `sp_client.py`
- `SpClient` — class — pub (via `__all__`) — Async SP 2017 REST HTTP client (httpx); list-item GET/POST/PATCH/DELETE + batch + pagination; retry on 429/503; OData $select/$filter/$top; SP error → SHP-E001/E004 mapping.
<!-- END:STRUCTURE -->
