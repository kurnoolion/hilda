# Module: sharepoint_integration

> **Rollback log:**
> - **2026-06-25 (Module #11 arch revisit per architect Q1-Q5 + D-104 cascade + recent ADR sweep)** — major scope contraction + cascade alignment. **D1 — HILDA SP scope contracted to 3 lists** per architect Q1 lock: HILDA reads/writes ONLY `Milestones_<customer_id>` + `Projects_<customer_id>` + `Deliverables_<customer_id>` (per-customer naming per `[D-104]`); IGNORES SP-side `TasksTemplate_<id>` + `Tasks_<id>` + `Trials_<id>` + `Activities_<id>` + `Email_<id>` + `CommunicationLog_<id>` (SP UI engineer's display surface only — NOT HILDA's concern). **Canonical entity set shrinks from 8 → 3**: `delivery_items` + `milestones` + `projects` (was: `customers, devices, milestones, delivery_items, users, pm_credentials, communication_log, tg_groups`). The other 5 SP lists (Customer, Device, User, PMCredentials, CommunicationLog, TGGroups) are NOT in HILDA's SP read/write scope — customer + device deployment-stable data lives in `customizations/template_schemas/<customer_id>/customer.yaml` per existing pattern; HILDA's CommunicationLog is Postgres-internal per FR-42; TGGroups DROPPED per `[D-106]`. **D2 — Per-customer SP list naming** per architect-confirmed pattern `<base>_<customer_id>` (e.g., `Projects_<customer_id>`, `Deliverables_<customer_id>`, `Milestones_<customer_id>`) per `[D-104]` + Q1. **D3 — slug → id rename** per `[D-091]`: `customer_slug` → `customer_id`; `device_slug` → `device_id` throughout. **D4 — Field-name authority** per architect Q5: `docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` (3 worksheets: Milestones / Deliverables / Projects) is the AUTHORITATIVE source for SP internal column names; supersedes stale `HILDA_SP_Schema.xlsx` + `DeliveryItem_visibility_review.xlsx` (both renamed `_DEPRECATED_2026-06-15.xlsx` in same directory). **D5 — SP REST URL pattern confirmed** per architect VS Code sample 2026-06-25: site URL = `<corp-sp-root>/sp/tg/<TG_SITE_NAME>` (one TG site per team-group); list URL = `<site>/_api/web/lists/getbytitle('<list_name>')/items?$top=N&$filter=...&$select=...`; header = `Accept: application/json;odata=verbose`. Matches existing SpClient design. **D6 — Browser vs server auth context**: sample uses XMLHttpRequest with implicit cookie auth (SP same-origin web part); HILDA server-side uses NTLM/Kerberos via service account per `[D-006]`. NTLM code snippets pending architect delivery; defer locking auth implementation details to next pass. **D7 — Pagination open question** per architect Q4: SP standard pattern is `$top + __next` continuation; architect mentioned "every element accessed individually" — surface as an OPEN architectural question pending NTLM code snippet review. Document Ph-1 as `$top + __next` auto-follow but flag for confirmation. **D8 — D-DRAFT-Y → [D-106] promotion**: TGGroups removal is now Ratified per [D-106]; replace `D-DRAFT-Y` references with `[D-106]`. **D9 — D-DRAFT-Z SUPERSEDED** by architect Q1 + `[D-104]`: HILDA scope is now 3 lists (Milestones + Projects + DeliveryItems per-customer), NOT 2 lists per D-DRAFT-Z. **D10 — D-DRAFT-X retained** (SP UI engineer manual provisioning still pending architect ratification — surface as P0 candidate for next ADR triage). **D11 — Anchors refresh**: drop `[D-051]` 8-list framing as authority (superseded by Q1 3-list); add `[D-091]` slug→id, `[D-104]` Projects per-customer, `[D-105]` 4-field owner identity, `[D-106]` TGGroupBase DROPPED, `[D-108]` rules_paused SP column. **D12 — Non-goals expand**: explicit "NOT a reader/writer of SP-side TasksTemplate/Tasks/Trials/Activities/Email/CommunicationLog lists" (SP UI engineer owns those) + "HILDA's CommunicationLog is Postgres-internal per FR-42 — distinct from SP-side CommunicationLog_<customer_id> list per architect Q3 lock 2026-06-25".
> - **2026-06-10 (drift fixes + FR-84 / FR-87 / column-map cascade alignment)** — A-tier drift fixes against current Structure block: 8-list canonical entity set per `[D-051]` (was stale 7-list); `SpCrud.delete_item` added to declared Public surface; `SharePointListProvider.from_sp_fields` added to Protocol; `mock_server/` sub-module documented (FastAPI SP stub + `--serve --port` CLI mode); sample line counts bumped 7 → 8. B-tier additions reflecting recent session work: FR-84 outbound writeback invariant (SP→HILDA HTTP firewall-blocked; HILDA→SP REST is the sole HILDA-initiated writeback channel); FR-87 TPM resolution writeback path invariant (SP-UI-button → HILDA-REST → HILDA-DB → SpCrud.update_item, strict A→B→C ordering); column-map append-only invariant for the 2026-06-08 cascade fields (target_folder / no_customer_upload / FR-87 TPM resolution fields on DeliveryItems; ingress_nsd / folder_routing_enabled / tracking_enabled on TGGroups); SP Choice-field value sync added to Non-goals (SP UI engineer owns Choice value updates when HILDA enums change — e.g., 4-value ItemType per `[D-053]`); SP-alert email channel added to Non-goals (owned by `email_service` per FR-84). C-tier polish: Depended-on-by extended (`issue_tracker`, `customizations/issue_tracker`, indirect-via-workflow_engine `customer_adapter`); two-halves-of-the-same-conversation positioning note for SP UI engineer collaboration. Anchors `[D-051]` (8-list framing), `[D-053]` (4-value ItemType), FR-84 (SP-HILDA channel discipline), FR-87 (TPM resolution).

**Purpose**: All SharePoint 2017 REST API interaction for HILDA — entity CRUD on SP Lists, NTLM/Kerberos authentication, and the mapping from HILDA's canonical entity fields to customer-deployment-specific SP list names and column names. Serves D-004, D-006, NFR-8, and anchors the SharePointListProvider Protocol pattern `[D-020]`. **HILDA's SP read/write scope is 3 per-customer lists** per architect Q1 lock 2026-06-25 + `[D-104]`: `Milestones_<customer_id>` + `Projects_<customer_id>` + `Deliverables_<customer_id>` (per-customer naming pattern `<base>_<customer_id>`). SP UI engineer maintains additional SP lists (`TasksTemplate`, `Tasks`, `Trials`, `Activities`, `Email`, `CommunicationLog`) in the same TG site as display surfaces — HILDA does not touch those.

*SharePoint scope is frozen at 2017 Lists + classic web parts only — no SPFx, no Power Apps, no Document Libraries per `[D-006]` `[D-013]` NFR-8.*

*This module is list-agnostic by design per `[D-020]` — it CRUDs any list named by `FileBasedListProvider` via `customizations/sharepoint_config/<deployment>.yaml`. The 3 SP lists in HILDA's runtime scope per architect Q1 2026-06-25 + `[D-104]` (Milestones / Projects / DeliveryItems, per-customer-named) are all served by the same SpClient + SpCrud + SharePointListProvider stack without per-list code. SP internal column names are sourced from `docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` (3 worksheets: Milestones / Deliverables / Projects) per architect Q5 — the AUTHORITATIVE field-name source (supersedes stale `HILDA_SP_Schema.xlsx` + `DeliveryItem_visibility_review.xlsx`, both renamed `_DEPRECATED_2026-06-15.xlsx`). Adding a per-customer scope in a future deployment is a config-only change in customizations/sharepoint_config/.*

---

## Architecture

Two orthogonal concerns, composed by `list_crud.py`:

1. **SpClient** — *how to talk to SharePoint*: raw async HTTP over the SP REST API. Handles auth, wire protocol, retry logic. Has no knowledge of HILDA entities or customer config.
2. **SharePointListProvider** — *what to talk about*: pure lookup service — given a HILDA entity type and a scope, returns the SP list name and column map. Makes no HTTP calls.

`list_crud.py` is the only compositor and the only public call site for all other modules.

**SP REST URL pattern** (architect-confirmed via VS Code sample 2026-06-25): site URL = `<corp-sp-root>/sp/tg/<TG_SITE_NAME>` (one TG site per team-group); list URL = `<site>/_api/web/lists/getbytitle('<list_name>')/items?$top=N&$filter=...&$select=...`; request header = `Accept: application/json;odata=verbose`. The architect's sample is a browser-context XMLHttpRequest with implicit cookie auth (SP same-origin web part); HILDA's server-side `SpClient` uses NTLM/Kerberos via service account per `[D-006]`. **Pagination — OPEN**: architect Q4 2026-06-25 ambiguous ("every element accessed individually"). Ph-1 documents standard SP `$top + __next` auto-follow continuation; flag for confirmation when NTLM code snippets land. **TODO**: confirm pagination pattern with architect on NTLM snippet delivery.

**NTLM auth + digest-dance lifecycle** (architect Q1-Q4 lock 2026-06-25; TODO(D-117 candidate) to ratify as ADR): the production transport is `requests` + `requests-ntlm` wrapped in `asyncio.to_thread` per the existing "NTLM sync-wrapped vs. native async" key choice. Encapsulated in `sp_session.py:SpSession`. Per architect:
  * **Q1** — `GlobalSharePointConfig.username` stores the FULL `corp\<user>` literal (NT4-style domain prefix included); there is no separate domain field.
  * **Q2** — Lazy digest acquisition. On the first write, run the 3-step dance: (1) NTLM-authed `GET <site_url>` to capture the `WSSAUTH` cookie from `Set-Cookie`; (2) `POST <site_url>/_api/contextinfo` to retrieve `d.GetContextWebInformation.FormDigestValue`. Cache cookie + digest in-session. On a 403 from any subsequent write, re-run steps 1-2 and retry the write exactly once. No time-based expiry tracking.
  * **Q3** — HILDA writes ONLY `Milestones_<customer_id>` / `Projects_<customer_id>` / `Deliverables_<customer_id>` (3-list per-customer scope per `[D-104]`). `SpSession` itself is list-agnostic per `[D-020]`; scope enforcement is upstream in `FileBasedListProvider`.
  * **Q4** — One `SpSession` per Celery task instance; no session pool. Sessions are short-lived per task.

**SP 2017 MERGE protocol for partial updates**: writes use POST with the pseudo-verb header `X-Http-Method: MERGE` plus `IF-MATCH: *` plus `X-RequestDigest: <digest>` plus body wrapped in `{"__metadata": {"type": "SP.Data.<list>_x005f_<customer_id>ListItem"}, ...fields}`. The type discriminator is built by `sp_session.list_item_type(list_display_name)` — encoding underscores as `_x005f_` and spaces as `_x0020_`. `customer_id` flows from `SpCrud.update_item(scope=...)` through `SpClient.update_list_item(customer_id=...)` to `SpSession.merge` so the `__metadata.type` can be composed correctly.

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
        self, list_name: str, fields: dict[str, Any], *, customer_id: str
    ) -> str: ...  # returns SP item ID

    async def update_list_item(
        self, list_name: str, item_id: str, fields: dict[str, Any], *, customer_id: str
    ) -> None: ...

    async def batch_create(
        self, list_name: str, items: list[dict[str, Any]], *, customer_id: str
    ) -> list[str]: ...  # returns list of SP item IDs

    async def batch_update(
        self, list_name: str, updates: list[tuple[str, dict[str, Any]]], *, customer_id: str
    ) -> None: ...
```

Auth: NTLM via `requests-ntlm` `HttpNtlmAuth` (architect lock 2026-06-25) — username is the full `corp\<user>` literal per architect Q1. The sync `requests.Session` lives inside `SpSession`; `SpClient` wraps each call in `asyncio.to_thread` per the existing key choice. Kerberos remains a placeholder (deferred until corp AD lab access). Selected by `config.auth_type`. Retry: exponential backoff on 429/503 for reads (configurable via `config.max_retries` + `config.retry_backoff_seconds`); writes use the dedicated 403→refresh-digest→retry-once path per architect Q2.

### SharePointListProvider Protocol

```python
@dataclass
class ListScope:
    customer_id: str
    device_id: str | None = None  # non-None triggers device-level override lookup (Ph-2/Ph-3+ Deferred per Q1 2026-06-25)

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

Entities (3-list canonical set per architect Q1 2026-06-25 lock + `[D-104]` — contracted from prior 8-list `[D-051]` framing): `"delivery_items"`, `"milestones"`, `"projects"`. The other SP lists in the architect's TG site (`TasksTemplate`, `Tasks`, `Trials`, `Activities`, `Email`, `CommunicationLog`) are SP UI engineer's display surface only and NOT in HILDA's read/write scope. HILDA's `CommunicationLog` is Postgres-internal per FR-42 — distinct concept from the SP-side `CommunicationLog_<customer_id>` list per architect Q3 lock 2026-06-25.

### FileBasedListProvider

Boilerplate implementation shipped in `core/`. Reads from `customizations/sharepoint_config/` at startup. Implements the 3-tier scope lookup: device override → customer config → `SHP-E002`.

```python
class FileBasedListProvider:
    """Implements SharePointListProvider by reading YAML files from customizations/."""

    def __init__(self, config_base: Path = Path("customizations/sharepoint_config")) -> None: ...
    # loads customers/<slug>.yaml and devices/special_devices.yaml at init
```

Customer YAML shape (`customizations/sharepoint_config/customers/<customer_id>.yaml`) — 3 lists per Q1 2026-06-25 lock + `[D-104]`; field names per `docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` (3 worksheets: Milestones / Deliverables / Projects) per architect Q5:
```yaml
customer_id: <customer_id>                         # slug→id rename per [D-091]
lists:
  # Field names per docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx
  # (3 worksheets: Milestones/Deliverables/Projects) — AUTHORITATIVE per architect Q5 2026-06-25
  milestones:
    name: "Milestones_<customer_id>"               # per-customer naming per [D-104] + architect Q1
    columns:
      milestone_name: "Title"
      # ... per Milestones worksheet
  projects:
    name: "Projects_<customer_id>"
    columns:
      # ... per Projects worksheet; TPM 3-tuple per [D-088]
  delivery_items:
    name: "Deliverables_<customer_id>"
    columns:
      item_name: "Title"
      delivery_state: "Delivery_x0020_State"
      rules_paused: "Rules_x0020_Paused"           # Boolean per [D-108] FR-31 sub-1
      # 4-field owner identity per [D-105]:
      owner_corp_usa_email: "Owner_x0020_Corp_x0020_USA_x0020_Email"
      owner_corp_email: "Owner_x0020_Corp_x0020_Email"
      owner_corp_id: "Owner_x0020_Corp_x0020_Id"
      owner_name: "Owner_x0020_Name"
      # ... per Deliverables worksheet
```

Device override YAML (`customizations/sharepoint_config/devices/special_devices.yaml`) — Deferred to Ph-2/Ph-3+ per architect Q1 2026-06-25 (no Ph-1 surface for device-level SP-list overrides; Ph-1 scope is per-customer only). See Deferred section.

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
- **Column maps are append-only** (added 2026-06-10) — when HILDA adds canonical fields (e.g., the 2026-06-08 cascade added `target_folder`, `no_customer_upload`, FR-87 TPM-resolution fields on `DeliveryItems`; `rules_paused` per `[D-108]` for FR-31 sub-1), the SP UI engineer adds the corresponding SP columns + the customer YAML extends the `columns:` block. No code change in this module — list-agnosticism per `[D-020]` makes the addition mechanical. TGGroups-side fields DROPPED per `[D-106]` (TGGroupBase Pydantic model removed; TG denormalization onto delivery_items lives in customer YAML, not SP-side TG list).
- **3-list per-customer scope per architect Q1 lock 2026-06-25 + `[D-104]`** — HILDA reads/writes ONLY `Milestones_<customer_id>` + `Projects_<customer_id>` + `Deliverables_<customer_id>`. Adding a 4th list to HILDA's runtime SP scope requires an ADR. SP UI engineer's TG site contains additional SP lists (`TasksTemplate`, `Tasks`, `Trials`, `Activities`, `Email`, `CommunicationLog`) — HILDA does not read or write those; they are SP UI engineer's display surface only.
- **Per-customer SP list naming `<base>_<customer_id>`** per architect Q1 confirmed pattern 2026-06-25 + `[D-104]`. List names embed the customer identifier (e.g., `Projects_<customer_id>`); HILDA resolves the name via `SharePointListProvider.get_list_name(entity, scope)` — never hardcoded.
- **NTLM digest-dance lifecycle per architect Q1-Q4 lock 2026-06-25** (TODO(D-117 candidate) to ratify as ADR) — production writes go through `SpSession` which (1) NTLM-auths with the FULL `corp\<user>` literal (Q1, no separate domain field), (2) lazily acquires the WSSAUTH cookie + FormDigestValue on first write via the GET-site + POST-contextinfo dance (Q2), (3) on 403 refreshes the digest and retries the write exactly once (Q2), and (4) is constructed per Celery task (Q4, no session pool). The MERGE protocol for partial updates posts to `items({id})` with `X-Http-Method: MERGE`, `IF-MATCH: *`, `X-RequestDigest`, and a body wrapped in `{"__metadata": {"type": "SP.Data.<list>_x005f_<customer_id>ListItem"}, ...}`. The type discriminator is built by `sp_session.list_item_type()` with `_` → `_x005f_` and ` ` → `_x0020_` encoding. `customer_id` flows through every write path (`SpCrud.create_item/update_item` → `SpClient.create_list_item/update_list_item(customer_id=...)` → `SpSession.create/merge`) so the `__metadata.type` can be composed per-customer.
- **Field-name authority** per architect Q5 2026-06-25: `docs/sp_ui_engineer/milestones_workitems_fields_values.xlsx` (3 worksheets: Milestones / Deliverables / Projects) is the AUTHORITATIVE source for SP internal column names. Stale `HILDA_SP_Schema.xlsx` and `DeliveryItem_visibility_review.xlsx` were renamed `_DEPRECATED_2026-06-15.xlsx` in the same directory; do not reference them.

---

## Key choices

- **`[D-004]`** — SharePoint integration split: standard API mechanics in `core/`; deployment-specific SP list names + column maps in `customizations/`. This module is the authority for that split.
- **`[D-006]`** — SP REST API + on-prem AD auth (NTLM/Kerberos). No Microsoft Graph.
- **`[D-020]`** — SharePointListProvider Protocol pattern: SpClient owns the "how"; SharePointListProvider owns the "what". FileBasedListProvider boilerplate ships in `core/`; `customizations/` provides the YAML data. `list_crud.py` is the sole compositor.
- **`[D-104]`** — Projects per-customer SP list (supersedes earlier framing where Projects was global). Combined with `[D-051]` superseded-by-Q1, HILDA's SP scope is now 3 per-customer lists: `Milestones_<customer_id>` + `Projects_<customer_id>` + `Deliverables_<customer_id>`. Architect Q1 lock 2026-06-25.
- **`[D-091]`** — slug → id rename throughout: `customer_slug` → `customer_id`; `device_slug` → `device_id` (both Protocol surface `ListScope` and YAML field names).
- **`[D-105]`** — 4-field owner identity model on Deliverables (`owner_corp_usa_email`, `owner_corp_email`, `owner_corp_id`, `owner_name`); replaces single `owner_email` field.
- **`[D-106]`** — TGGroupBase Pydantic model DROPPED (formerly D-DRAFT-Y; ratified per `[D-051]` denormalization architect lock 2026-06-21). No SP-side TG list; TG metadata lives only as customer YAML / denormalized columns on Deliverables.
- **`[D-108]`** — `rules_paused` SP column on Deliverables for FR-31 sub-1 (Boolean; column-map append).
- **Per-customer SP list naming `<base>_<customer_id>`** per architect Q1 lock 2026-06-25 + `[D-104]`.
- **`[D-051]` historical / superseded** — was 8-list canonical entity set; superseded by architect Q1 2026-06-25 3-list scope. Retained as historical anchor.
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
- **NOT a reader/writer of SP-side `TasksTemplate` / `Tasks` / `Trials` / `Activities` / `Email` / `CommunicationLog` lists** per architect Q1 lock 2026-06-25 — those 6 SP lists exist in the architect's TG site but are SP UI engineer's display surface only; HILDA does not touch them. HILDA's runtime SP scope is exactly `Milestones_<customer_id>` + `Projects_<customer_id>` + `Deliverables_<customer_id>`.
- **NOT a sync target for the SP-side `CommunicationLog_<customer_id>` list** per architect Q3 lock 2026-06-25 — HILDA's `CommunicationLog` is Postgres-internal per FR-42; the SP-side list (if it exists per SP UI engineer's design) is independent.
- **NOT the TasksTemplate authority** per architect Q2 lock 2026-06-25 — template.yaml lives in `customizations/template_schemas/` per existing pattern; no SP-side mirror needed.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate` (SHP error codes registered in `error_codes.py`).
- `template_schema` — `CustomerSchema` (used at startup to validate SP column map coverage against canonical fields); `ListScope` co-located here but typed against `customer_id` / `device_id` from `template_schema` per `[D-091]` slug→id rename.

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
RPT|SHP|run-00001|2026-06-25T10:00:00Z|customers_configured=1|lists_reachable=3|lists_unreachable=0|auth_type=ntlm
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
MET|SHP|run-00001|2026-06-25T10:00:00Z|customer=<customer_id>|lists_validated=3|columns_mapped=58|missing_columns=0
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
- `KerberosAuthHandler` — class — pub — Kerberos/SPNEGO placeholder; raises SHP-E004 until corp AD lab access.
- `NoAuthHandler` — class — pub — Pass-through handler for mock-server dev.
- `NtlmAuthHandler` — class — pub — NTLM handler via `requests-ntlm` `HttpNtlmAuth`; username is full `corp\<user>` literal per architect Q1 2026-06-25.
- `make_handler(config) -> _AuthHandler` — function — pub — Factory: picks NoAuth/Ntlm/Kerberos from `config.auth_type`; raises SHP-E004 on misconfig.

### `config.py`
- `GlobalSharePointConfig` — Pydantic BaseModel — pub (via `__all__`) — Operational SP config (site_url, auth_type, creds, timeouts, page_size); secret-redacted `__repr__`; `from_sources(config_path, cli_overrides, env_prefix)` 3-tier loader.
- `ListScope` — frozendataclass — pub (via `__all__`) — Lookup scope (customer_id, optional device_id for override path per `[D-091]` slug→id rename).

### `error_codes.py`
- (no public top-level names — registers SHP-E001..E004 + SHP-W001 on import via `register_code` side-effect.)

### `list_crud.py`
- `SpCrud` — class — pub (via `__all__`) — Sole public CRUD compositor over SpClient + SharePointListProvider; canonical-in / canonical-out; `get_items/create_item/update_item/delete_item/batch_create/batch_update`.

### `list_provider.py`
- `FileBasedListProvider` — class — pub (via `__all__`) — YAML-backed SharePointListProvider; reads customer + device-override files from `customizations/sharepoint_config/`; raises SHP-E002 on scope miss.
- `SharePointListProvider` — Protocol — pub (via `__all__`) — Pure lookup: `get_list_name`, `get_column_map`, `to_sp_fields`, `from_sp_fields`.

### `mock_server/app.py`
- `build_app(store=None) -> FastAPI` — function — pub — Builds the mock SP FastAPI app exposing SP 2017 REST + the architect digest-dance endpoints (`GET /` Set-Cookie + `POST /_api/contextinfo` FormDigestValue) + MERGE/DELETE pseudo-verb dispatch on POST + HTML browser UI over a shared `InMemoryStore`.

### `mock_server/client.py`
- `MockSpSession` — class — pub (via `mock_server.__init__`) — `SpSession` subclass that drives an in-process FastAPI `mock_server` app via `TestClient`; runs the full digest dance against the mock to exercise `SpClient` end-to-end without NTLM or real network.

### `mock_server/store.py`
- `InMemoryStore` — dataclass — pub — Thread-safe in-memory list store backing the mock server; lists addressed by display name; monotonic per-list item IDs; audit log; `digest_403_count` test knob to force 403s; `next_digest(base)` issues unique tokens per call.
- `ListNotFoundError` — class (KeyError) — pub — Raised when a list referenced by name does not exist.

### `sharepoint_integration_cli.py`
- `DEFAULT_BASE` — module constant — pub — Default `customizations/sharepoint_config` path.
- `DEFAULT_CONFIG` — module constant — pub — Default `config/sharepoint_integration.json` path.
- `main(argv=None) -> int` — function — pub — CLI entrypoint: `--diagnostic` / `--mock` / `--dry-run --customer` / `--serve --port` modes.

### `sp_client.py`
- `SpClient` — class — pub (via `__all__`) — Async SP 2017 REST client over an injected sync `SpSession` transport (architect lock 2026-06-25); list-item GET/POST(MERGE)/POST(DELETE)/PATCH + batch + pagination; retry on 429/503 for reads; per-call `customer_id` flows to `SpSession` for `__metadata.type` composition; SP error → SHP-E001/E004 mapping.

### `sp_session.py`
- `SpSession` — class — pub (via `__all__`) — Sync SP 2017 NTLM session encapsulating `requests-ntlm` auth + digest dance (lazy WSSAUTH-cookie + FormDigestValue acquisition; 403→refresh→retry-once per architect Q2 2026-06-25) + SP 2017 MERGE protocol for partial updates (`__metadata` wrapper + `X-Http-Method: MERGE` + `IF-MATCH: *`).
- `list_item_type(list_display_name) -> str` — function — pub (via `__all__`) — Encode the SP 2017 `__metadata.type` discriminator: `_` → `_x005f_`, ` ` → `_x0020_`; returns `SP.Data.<encoded>ListItem`.
<!-- END:STRUCTURE -->
