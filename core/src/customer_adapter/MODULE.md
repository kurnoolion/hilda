# Module: customer_adapter

> **Status:** Initial draft 2026-06-09 + **2026-06-24 architect cascade revisit applied (11 drift items D1-D11 against locks since 2026-06-09)**. Per-carrier subclass pattern + Google-Drive browser-automation baseline per `[D-054]` + `[D-054]` impl note 2026-06-05. **Implementation strategy 2026-06-24 per user direction**: thin-wrapper pattern — HILDA's `GoogleDriveBaseAdapter` (and per-customer subclass) wraps the user's existing Google Drive API/library (reused from their pre-existing tooling); HILDA owns the Protocol contract + CommunicationLog discipline + selector/session management; the user provides the actual Google Drive API binding. Sections curated; code implementation begins after `/switch-phase development`.
>
> **Rollback log:**
> - **2026-06-24 (architect cascade revisit — 11 drift items applied)** — strict-order module-by-module sweep Module #10 of 13. **D1 — slug → id rename per `[D-091]`**: `customer_id` → `customer_id` throughout (per-customer subclass file paths at `customizations/customer_adapter/<customer_id>_adapter.py` + `<customer_id>_adapter_config.yaml`; config key `customer_id` in TaskBinding constructor params; CarrierCapabilityFlags lookup key). **D2 — `CustomerAdapterConfig` typo → `CustomerAdapterConfig`** (3 sites at lines referencing `CustomerAdapterConfig.browser_engine` / `CustomerAdapterConfig.session_max_age_s`). **D3 — FR-68 PLM-Carrier hash sync DROPPED per architect direction 2026-06-20**: requirements.md FR-68 (B) hash-match logic dropped; verification now reduced to upload-success markers — (i) PLM `uploadAttachment` return result (in issue_tracker module's scope, not here); (ii) carrier-portal UI file-existence check (this module's scope — per-folder list-files check that the dispatched filename exists at the FR-77 Type-2 target path). `CarrierCapabilityFlags.supports_hash_verification` retained as Ph-2 forward-looking but no longer required for FR-68 Ph-1 verification. **D4 — `[D-054]` PLM-Carrier hash-sync anchor narrowed**: anchor now scoped to "individual files only, never zips" semantic; byte-level hash comparison portion is out of scope per architect direction 2026-06-20. **D5 — FR-77 path composition per NFR-21 amendment §6 (2026-06-21)**: `upload_attachment(target_folder=...)` receives the FULLY-RESOLVED path = `customer_delivery_info + delivery_path_template_expanded + target_folder` (composed by workflow_engine's submission task body via FR-77 dispatch logic). customer_adapter does NOT compose paths; it consumes the resolved string. `customer_delivery_info` example: `drive.google.com`; `delivery_path_template_expanded` example: `OEM_Folder1/OEM_Folder2/MODEL-A/P1`; `target_folder` example: `TestReports/Power`. Final resolved path: `drive.google.com/OEM_Folder1/OEM_Folder2/MODEL-A/P1/TestReports/Power`. **D6 — workflow_engine integration acknowledgment**: `workflow_engine.tasks.submission.QUEUE_SUBMISSION` task body (commit `96a498f` -- 10/18 ActionKinds registered; QUEUE_SUBMISSION is among the 8 pending downstream module landing) is stub-pending until this module's code lands. Once customer_adapter Ph-1 dev complete, register the task binding + activate QUEUE_SUBMISSION in workflow_engine's `tasks/submission.py`. **D7 — credential_service Ph-1 cascade**: `get_credential` signature updated 2026-06-21 to `get_credential(pm_id, system_type, customer_id=None)` per credential_service mini-cascade; for SystemType.CUSTOMER the customer_id is required (per-customer scope per `SYSTEM_CRED_SCOPE = {CUSTOMER: PER_CUSTOMER}`). `customer_adapter` callers pass the resolved customer_id from `EntityRef`. **D8 — dashboard integration**: `dashboard` (Module #9, dev complete 2026-06-24 commit `dc31949`) surfaces upload status from `CommunicationLog` rows written here; `CarrierUploadResult.carrier_file_url` renders as a clickable TPM verification link in the dashboard's document section per FR-57. **D9 — Status header refresh (this entry)**. **D10 — Anchors update**: adds `[D-091]` (slug → id rename throughout). **D11 — User direction 2026-06-24 — thin-wrapper implementation strategy**: HILDA's `GoogleDriveBaseAdapter` is a Protocol-conformant thin wrapper around the user's existing Google Drive API binding. HILDA owns (a) the `CustomerAdapter` Protocol contract; (b) `CarrierUploadResult` shape; (c) CommunicationLog discipline per FR-42; (d) `CarrierCapabilityFlags` per-customer surface; (e) selector pack versioning + session pool management. User-provided binding owns the actual `upload(file_bytes, target_folder)` -> `(file_id, file_url)` call. Per `[D-027]` Teacher/Student split: HILDA-side Protocol scaffold + thin wrapper authored by Claude on Personal PC; concrete API call body filled in by Cline on Work PC using the user's existing implementation (no proprietary API details on public github per NFR-2).
> - **2026-06-09 (initial draft)** — final remaining Phase B Module rollback per STATUS.md In-progress 2026-06-08. Draft scoped to **Ph-1 only**: Google Drive baseline + per-carrier subclass + browser automation per `[D-054]` impl note 2026-06-05 (selenium/playwright on headless Chromium; selector versioning per-deployment; capability flags per-carrier). Ph-2 (`get_status` / `post_comment` / `fetch_feedback`) noted in `## Deferred` only. Ph-3+ (web portal flavor + JIRA-as-customer-portal flavor per `[D-054]`) noted in `## Deferred`. Anchors FR-19, FR-68 (PLM-Carrier hash sync invariant; narrowed 2026-06-20 per D4 cascade — byte-level hash comparison dropped), FR-73 (carrier-package zip flow), FR-77 (target_folder outbound carrier path), FR-80 (`no_customer_upload` gate), NFR-2 (no proprietary content in compact reports). New error code prefix `CAD` per `diagnostics.PREFIX_REGISTRY`.

**Purpose**: Single Protocol-mediated surface (`CustomerAdapter`) for HILDA's outbound carrier submission — upload individual document files (per `[D-054]` — individual files only, never zips; byte-level hash comparison portion narrowed out of scope per D4 cascade 2026-06-20) to each carrier's submission destination + emit `CommunicationLog` entries per FR-42 + verify upload success per FR-68 (B) per-folder list-files check (per D3 cascade). **This module does NOT do FR-77 routing itself** — the caller (`workflow_engine` submission task body, `tasks/submission.QUEUE_SUBMISSION` — stub-pending until this module's code lands per D6 cascade) resolves the **fully-composed carrier path** via NFR-21 amendment §6 (2026-06-21) `customer_delivery_info + delivery_path_template_expanded + target_folder` and passes the resolved string IN to `upload_attachment(target_folder=...)`. This module just honours it. Ph-1/Ph-2 scope: **Google Drive only** per `[D-054]`. **Implementation strategy per user direction 2026-06-24 (D11 cascade)**: `GoogleDriveBaseAdapter` is a thin wrapper around the user's existing Google Drive API binding — HILDA owns the Protocol contract + CommunicationLog discipline + selector versioning + session pool management; user-provided binding handles the actual `upload(file_bytes, target_folder) -> (file_id, file_url)` call. Per-customer subclass at `customizations/customer_adapter/<customer_id>_adapter.py` extends the `GoogleDriveBaseAdapter` reference class with customer-specific selectors + session-login flow + capability flags (loaded from `customizations/customer_adapter/<customer_id>_adapter_config.yaml`). Anchors `[D-007]` (on-prem; browser runs on HILDA PC inside corporate network boundary), `[D-019]` (credential_service for PM session cookies — per-customer scoped per credential_service mini-cascade 2026-06-21 per D7 cascade), `[D-054]` (Google-Drive-only + thin-wrapper impl per D11 cascade; hash-sync anchor narrowed per D4 cascade), `[D-091]` (slug → id rename per D1 cascade); serves FR-19, FR-68 (upload-success verification per D3 cascade — byte-level hash comparison out of scope), FR-73 (Ph-1 carrier-package zip two-click flow), FR-77 (consumes fully-resolved path per NFR-21 amendment §6 per D5 cascade), FR-80 (`no_customer_upload` gate), NFR-1, NFR-2.

**Workload assignment**: `hilda-worker` Celery task pool per `[D-021]` — submission tasks are background, minutes-scale per-upload due to browser-automation latency (per `[D-054]` impl note: 10–100× slower than REST). `hilda-api` reads submission status from `CommunicationLog` for SP UI surfaces but does not host the browser. **Chromium binary is a hard ops dependency** on the HILDA PC per `[D-054]` impl note — installed via `docker-compose` image build OR pre-installed on the host; selector-config files are deploy-time updates without code release.

**Per-upload latency (Ph-1 reality)**: ~10–30s per file (browser session reuse) up to ~60–90s on cold session establishment. Per-carrier worker concurrency configured against available browser sessions (default 1 per carrier; configurable via `CustomerAdapterConfig.session_pool_size`). For FR-73 carrier-package multi-file submissions, concurrency stays serial per carrier (Google Drive throttles parallel uploads from same session); inter-carrier submissions parallelize freely.

---

## Sub-modules

```
core/src/customer_adapter/
  __init__.py
  protocol.py                       ← interfaces: CustomerAdapter, CarrierUploadResult, CarrierCapabilityFlags
  google_drive_base.py              ← GoogleDriveBaseAdapter reference class — selenium/playwright browser automation; per-carrier subclasses extend
  session_manager.py                ← browser session lifecycle: launch headless Chromium, restore cookies, refresh on expiry; one session pool per carrier
  selector_loader.py                ← loads versioned UI selectors from customizations/customer_adapter/<customer_id>_adapter_config.yaml
  capability_flags.py               ← per-carrier capability flag resolution (which CustomerAdapter methods are supported)
  diagnostics_cli.py                ← --diagnostic / --mock / --invoke modes
  customer_adapter_cli.py           ← user-facing wrapper for ops debugging
  tests/
  MODULE.md                         ← this file

customizations/customer_adapter/
  <customer_id>_adapter.py         ← per-carrier subclass: overrides login flow + selector pack + capability flags
  <customer_id>_adapter_config.yaml ← per-carrier configuration: Drive folder root path, selector versions, capability flags, session-cookie storage key
  README.md                         ← onboarding doc for adding a new carrier
```

---

## Public surface

### `protocol.py`

```python
@dataclass(frozen=True)
class CarrierUploadResult:
    """Per FR-19 + FR-68 — return from CustomerAdapter.upload_attachment()."""
    success:               bool
    carrier_file_id:       str | None         # Google Drive file ID when success=True; None on failure
    carrier_file_url:      str | None         # Google Drive viewer URL when success=True
    uploaded_filename:     str                # original filename as uploaded (preserved per FR-57)
    target_folder_path:    str                # the FR-77 target_folder used (carrier-side path)
    file_hash_local:       str                # SHA-256 of local file at upload time; consumed by FR-68 hash sync invariant
    file_hash_carrier:     str | None         # SHA-256 fetched from carrier post-upload (Ph-2 enhancement; Ph-1 may be None — fallback per FR-68 hash sync)
    upload_started_at:     datetime
    upload_completed_at:   datetime
    error_code:            str | None         # CAD-E001..CAD-E0NN when success=False
    error_detail:          str | None         # bounded enum token + carrier-side message excerpt (NFR-2 — no proprietary content)

@dataclass(frozen=True)
class CarrierCapabilityFlags:
    """Per `[D-054]` impl note 2026-06-05 — per-carrier capability declaration loaded from
    customizations/customer_adapter/<customer_id>_adapter_config.yaml. Some Google Drive
    deployments render certain operations only in HTML without underlying API endpoints —
    those operations raise NotImplementedError; callers handle gracefully."""
    supports_upload:                       bool = True   # FR-19 — assumed True for Ph-1 (no carrier without upload support)
    supports_session_reuse:                bool = True   # most carriers allow multi-upload per session; some force re-login
    supports_upload_success_verification:  bool = True   # FR-68 (B) per D3 cascade 2026-06-20 — per-folder list-files check after upload; Google Drive Ph-1 = True
    supports_hash_verification:            bool = False  # FR-68 byte-level hash retained as Ph-2 forward-looking; not required for Ph-1 verification per D3 cascade

class CustomerAdapter(Protocol):
    """All callers depend on this Protocol, not on a concrete subclass. Implementations:
    GoogleDriveBaseAdapter (Ph-1/Ph-2 reference); per-carrier subclasses in customizations/;
    MockCustomerAdapter (tests)."""

    source_system: str                         # immutable; identifies the carrier slug

    async def upload_attachment(
        self,
        file_path:           Path,             # local NSD classified path per FR-13
        target_folder:       str,              # FR-77 FULLY-RESOLVED carrier path per NFR-21 amendment §6 (2026-06-21):
                                               # customer_delivery_info + delivery_path_template_expanded + per-item target_folder
                                               # composed by workflow_engine's submission task body; this module just honours.
                                               # Example: "drive.google.com/OEM_Folder1/OEM_Folder2/MODEL-A/P1/TestReports/Power"
        customer_credential_id: str,           # opaque reference to credential_service-stored session cookie blob
        original_filename:   str,              # preserved per FR-57
    ) -> CarrierUploadResult: ...

    async def health(self) -> dict[str, Any]:
        """Returns {ready: bool, session_age_s: int, queue_depth: int, capability_flags: dict}.
        Used by --diagnostic."""

    @property
    def capability_flags(self) -> CarrierCapabilityFlags: ...
```

### `google_drive_base.py`

```python
class GoogleDriveBaseAdapter(CustomerAdapter):
    """Reference baseline for Google Drive carrier adapters per `[D-054]` + impl note 2026-06-05.
    Per-carrier subclasses (e.g., `customizations/customer_adapter/<customer_id>_adapter.py`)
    extend by:
      - overriding `login(session)` for carrier-specific SAML / SSO / one-time setup
      - declaring `capability_flags` per carrier policy
      - declaring `selectors_version` to lock against a specific selector pack version

    Browser automation: selenium (default) or playwright per `CustomerAdapterConfig.browser_engine`;
    headless Chromium instance per session; PM session cookies retrieved from credential_service
    (system_type = CUSTOMER) and restored at session establishment. Selectors versioned in
    customizations/<carrier>_adapter_config.yaml — deploy-time refresh when Google Drive UI
    changes upstream."""

    source_system:        str                  # subclass overrides — carrier slug
    selectors_version:    str                  # subclass-declared compatibility pointer — minimum
                                               # selector-pack version this subclass expects (e.g.
                                               # "gdrive-2026.06"). The AUTHORITATIVE selectors_version
                                               # is the one inside the YAML config; selector_loader
                                               # compares the two at init and emits CAD-W004 on mismatch
                                               # (caller chooses to proceed or abort). Bumping the YAML
                                               # version (deploy-time UI refresh) does NOT require a
                                               # code/subclass change — see Key choices.

    def __init__(
        self,
        config:             "CustomerAdapterConfig",
        credential_service: CredentialService,
        session_manager:    "SessionManager",
        selector_pack:      "SelectorPack",     # loaded by selector_loader from <carrier>_adapter_config.yaml
        capability_flags:   CarrierCapabilityFlags,
    ) -> None: ...

    async def upload_attachment(
        self, file_path: Path, target_folder: str, customer_credential_id: str,
        original_filename: str
    ) -> CarrierUploadResult:
        """Browser-automation upload flow:
        1. Acquire/lease a session from session_manager (reuse if available; cold-start otherwise).
        2. Navigate to target_folder via Google Drive web URL pattern.
        3. Click upload button per selector_pack.upload_button.
        4. Send local file_path via Chromium's file-input handler.
        5. Wait for upload-progress completion per selector_pack.upload_done_indicator (timeout per CustomerAdapterConfig).
        6. Read uploaded file's URL/ID from DOM per selector_pack.file_id_extractor.
        7. (Ph-2) If supports_hash_verification: navigate to file properties; extract carrier-side hash.
        8. Emit MET record (selector_version, latency_ms, file_size_kb, success).
        9. Return CarrierUploadResult."""

    async def login(self, session: "BrowserSession") -> None:
        """Subclass-overridable carrier-specific authentication. Default Google Drive baseline:
        restore session cookie blob from credential_service.get_credential(...).session_cookie_jar.
        Subclasses extend for SAML / SSO / one-time-password flows."""

    async def health(self) -> dict[str, Any]: ...

    @property
    def capability_flags(self) -> CarrierCapabilityFlags: ...
```

### `session_manager.py`

```python
class SessionManager:
    """Browser session pool per carrier. One Chromium process per session; sessions reused
    across uploads to amortize cold-start cost (per `[D-054]` impl note: cold-start is the
    dominant per-upload latency). Session pool size configurable per carrier; default 1.
    Sessions expire per `CustomerAdapterConfig.session_max_age_s` (default 3600s); expired sessions
    are torn down + re-established on next use."""

    def __init__(self, max_sessions_per_carrier: int = 1, max_age_s: int = 3600) -> None: ...
    async def acquire(self, customer_id: str) -> "BrowserSession": ...
    async def release(self, session: "BrowserSession") -> None: ...
    async def teardown_all(self) -> None: ...
```

### `selector_loader.py`

```python
@dataclass(frozen=True)
class SelectorPack:
    """Versioned UI selector bundle per `[D-054]` impl note 2026-06-05. Locked per carrier in
    customizations/customer_adapter/<customer_id>_adapter_config.yaml. Selector-version bumps
    are deploy-time updates (no code release) to absorb upstream Google Drive UI changes."""
    selectors_version:      str                # e.g. "gdrive-2026.06"
    upload_button:          str                # CSS selector for the upload button
    file_input:             str                # CSS selector for the hidden <input type=file>
    upload_done_indicator:  str                # CSS selector that confirms upload completed
    file_id_extractor:      str                # CSS selector for the uploaded file's URL/ID in DOM
    folder_navigation:      str                # CSS selector for folder navigation pattern
    # Optional Ph-2 selectors:
    file_hash_view:         str | None = None  # Ph-2 — Google Drive file properties view for hash extraction

def load_selector_pack(customer_id: str, config_dir: Path) -> SelectorPack:
    """Loads from customizations/customer_adapter/<customer_id>_adapter_config.yaml.
    Raises CAD-E001 if config file missing; CAD-E002 if required selector fields missing."""
```

### `MockCustomerAdapter`

```python
class MockCustomerAdapter:
    """In-process mock for tests. No Chromium, no selenium, no playwright. Returns canned
    CarrierUploadResult per registered (customer_id, target_folder) pair."""

    source_system: str = "mock_customer"

    def register_upload_result(
        self, customer_id: str, target_folder: str, result: CarrierUploadResult
    ) -> None: ...
    async def upload_attachment(self, ...) -> CarrierUploadResult: ...
```

### Configuration

```python
class CustomerAdapterConfig(BaseModel):
    """Per `[D-025]` + `[D-038]` 3-tier (CLI > env > config/customer_adapter.json)."""
    browser_engine:           Literal["selenium", "playwright"] = "selenium"
    chromium_binary_path:     Path = Path("/usr/bin/chromium-browser")
    session_max_age_s:        int = 3600              # 1h default
    session_pool_size:        int = 1                 # per carrier
    upload_timeout_s:         int = 120               # per-upload wait
    cold_start_timeout_s:     int = 180               # session establishment
    customizations_dir:       Path = Path("/etc/hilda/customizations/customer_adapter")
```

Credentials per D7 cascade 2026-06-24: `credential_service.get_credential(pm_id, SystemType.CUSTOMER, customer_id=<resolved>)` returns the per-customer session-cookie blob. Per credential_service mini-cascade 2026-06-21, SystemType.CUSTOMER is per-customer scoped (`SYSTEM_CRED_SCOPE = {CUSTOMER: PER_CUSTOMER}`); `customer_id` is required (resolved from `EntityRef.customer_id`). Per `[D-019]` Ph-1/Ph-2 model, this is the ops-team-provisioned shared session (per-PM Vault isolation Ph-3+ per DEF-14 + `[D-019]` v2).

---

## Invariants

- **Ph-1/Ph-2: Google Drive only** per `[D-054]`. Web portal flavor + JIRA-as-customer-portal flavor deferred to Ph-3+. No per-carrier code in `core/`; only the `GoogleDriveBaseAdapter` reference class.
- **Individual files only** per `[D-054]` — `upload_attachment` accepts one file at a time. Never uploads zip archives. FR-73 carrier-package "two-click" flow assembles the zip in HILDA storage (FR-73) for TPM download; carrier submission still uploads individual files unpacked.
- **Upload-success verification per FR-68 (narrowed 2026-06-20 per D3 cascade)** — FR-68 (B) byte-level hash-match logic DROPPED per architect direction 2026-06-20; `[D-054]` PLM-Carrier hash-sync anchor narrowed to "individual files only, never zips" semantic. Ph-1 verification = upload-success markers: (i) **PLM side** (in `issue_tracker` module's scope, not here) — `IssueTracker.uploadAttachment` return result + `plm_attachment_id` recorded in `DocumentIndexRow` as persistent success marker. (ii) **Carrier side** (this module's scope) — per-carrier `CustomerAdapter` confirms file existence via the platform's UI surface (Google Drive Ph-1/Ph-2: per-folder list-files check that the dispatched filename exists at the FR-77 Type-2 target path; gated by `CarrierCapabilityFlags.supports_upload_success_verification` -- Ph-1 = True for Google Drive). No hash recomputation, no PLM-Carrier byte-level comparison. On verification failure: `ITR-W003 — Upload verification failed for file '{filename}' item '{item_id}' channel '{plm | carrier}'` (issue_tracker emits; per-row flag affordance per FR-56 (c) routes TPM to HILDA-rendered document section per `[D-074]` for manual reconciliation). `file_hash_carrier` field on `CarrierUploadResult` retained as Ph-2 forward-looking but unused in Ph-1 FR-68 verification; `CarrierCapabilityFlags.supports_hash_verification` retained as Ph-2 flag.
- **`no_customer_upload` gate per FR-80** — when DeliveryItemBase.no_customer_upload = True, this adapter is NOT invoked for that item; caller (workflow_engine submission task) skips. Symmetric with PLM upload skip per `[D-054]`.
- **Chromium binary is a hard ops dependency** per `[D-054]` impl note 2026-06-05 — installed via docker-compose image build OR pre-installed on host. `--diagnostic` mode validates Chromium availability at startup.
- **Selectors are versioned in customizations/** — selector-pack version bumps are deploy-time updates (no code release). The `selectors_version` field is logged in every MET record for traceability against Google Drive UI changes.
- **Per-carrier capability flags** — `CarrierCapabilityFlags` declares what operations a carrier supports. Methods backed by unavailable carrier surface raise `NotImplementedError`; callers must handle gracefully (e.g., dashboard surfaces "status not available for this carrier" rather than treating as error).
- **No credential material in logs / reports** per NFR-2 / `[D-002]`. Session cookies retrieved per-session via credential_service; never logged, written to disk, or echoed to compact reports. `customer_credential_id` is an opaque reference only.
- **No proprietary content in compact reports** per NFR-2 / `[D-002]`. CAD-RPT / -MET / -FIX / -QC records emit selector_version, latency_ms, file_size_kb, capability_flags, success bool, error_code, latency buckets — never the file content, carrier UI text, or carrier-side identifiers.
- **CommunicationLog write per FR-42** — every `upload_attachment` call appends a `CommunicationLog` row with channel=`customer`, direction=`outbound`, action_type=`carrier_upload`, attachments=[{filename, carrier_file_url}], credential_id=opaque, timestamp. Non-blocking; survives upload failure.
- **SP UI progress/completion via SP REST writeback per FR-84** — this module does NOT push to SP UI directly. Caller (`workflow_engine` submission task) is responsible: on `CarrierUploadResult.success=True`, the caller writes the `CommunicationLog` row + updates `DeliveryItemBase.delivery_state` (e.g., → `SubmittedToCustomer`) via SP REST API per FR-84 outbound writeback (SP→HILDA HTTP is firewall-blocked; HILDA→SP HTTP is fine). SP UI sees the state change via its §8.1 SP REST polling (5–10s delta render). For long FR-73 carrier-package submissions, SP UI may additionally poll the optional `hilda.corp/status/milestone/<id>/submission` endpoint per SP REQUIREMENTS §8.2 to surface in-progress per-file progress.
- **Async-native** per `[D-008]` pattern — `upload_attachment` is `async def`; browser automation (sync libraries) wrapped in `asyncio.to_thread` per `structure-conventions.md` Sync-API wrapping convention.

---

## Error codes (CAD prefix — registered in `diagnostics/error_codes.py`)

```
CAD-E001  Selector config file '{path}' not found for carrier '{customer_id}' (selector_loader)
CAD-E002  Selector config '{path}' missing required field '{field}' for selectors_version '{version}'
CAD-E003  Chromium binary not found at '{path}' (ops dependency missing per [D-054] impl note 2026-06-05)
CAD-E004  Browser session establishment failed for carrier '{customer_id}': {reason} (cold-start timeout, login failure, etc.)
CAD-E005  Upload timed out after {timeout_s}s on file_hash='{file_hash}' carrier='{customer_id}' target_folder='{folder}' selectors_version='{version}'
CAD-E006  Upload selector '{selector_name}' not found in current Google Drive DOM — selectors_version '{version}' likely stale (deploy-time refresh needed)
CAD-E007  Carrier file_id extraction failed post-upload — uploaded file may exist on carrier side; manual verification needed (file_hash='{file_hash}' carrier='{customer_id}')
CAD-E008  Carrier credential '{credential_id}' not retrievable from credential_service
CAD-W001  Capability flag '{flag}' is False for carrier '{customer_id}' — operation '{op}' not supported; caller fell back gracefully
CAD-W002  Browser session expired during upload; re-established and retried (recoverable; latency penalty)
CAD-W003  Selector '{selector}' returned multiple matches — used first; selectors_version '{version}' may need refinement
CAD-W004  Selector-pack version mismatch: subclass expects '{subclass_version}', YAML provides '{yaml_version}' (carrier '{customer_id}') — proceeding per caller policy
```

---

## Key choices

- **`[D-054]`** — Ph-1/Ph-2 customer_adapter scope is **Google Drive only** (carriers accepting submissions via shared Google Drive folders). Web portal + JIRA-as-customer-portal flavors deferred to Ph-3+.
- **`[D-054]` impl note 2026-06-05** — Google Drive REST API is **unavailable** per corp/carrier policy. Implementation switches to **browser-automation libraries** (selenium default, playwright alternative) driving headless Chromium. Per-upload latency is 10–100× slower than REST equivalent; concurrency bounded by browser-session pool.
- **Per-carrier subclass pattern at `customizations/`** — each carrier extends `GoogleDriveBaseAdapter` for SAML/SSO/login-flow specifics + declares its `selectors_version` + `CarrierCapabilityFlags`. Aligns with `[D-003]` adapter pattern (proprietary specifics live in `customizations/`, generic logic in `core/`).
- **Versioned selectors in carrier config** — Google Drive UI changes break selectors. Locking selector packs by version (e.g. `gdrive-2026.06`) with deploy-time updates (no code release) is the operational pattern per `[D-054]` impl note 2026-06-05. *Worked example*: Google Drive upstream renames upload button class from `.upload-btn-old` to `.upload-btn-2026.07`. Without this pattern: developer reproduces locally, edits Python, rebuilds image, redeploys (hours-to-days lead time). With this pattern: (1) upload fails → emits `CAD-E006` (stale selector); (2) ops edits `customizations/customer_adapter/<carrier>_adapter_config.yaml` — bumps `selectors_version: gdrive-2026.07` + updates `upload_button: ".upload-btn-2026.07"`; (3) bind-mount picks it up at next adapter init (per `[D-025]` customer YAML mount); (4) no code change, no rebuild, no redeploy (minutes lead time). Same discipline as `[D-031]` config-as-code for rules + `customizations/template_schemas/<slug>/schema.yaml` for customer schemas — deployment-fragile data lives in versioned YAML, not in code.
- **Session pool per carrier** — Chromium cold-start dominates per-upload latency. One reused session per carrier (default; configurable) amortizes the cost. Sessions auto-expire per `CustomerAdapterConfig.session_max_age_s`; re-established on next use.
- **Capability flags per carrier** — some Google Drive operations (e.g., file hash visibility) may be unavailable in certain deployments (per-tenant settings, etc.). `CarrierCapabilityFlags` declares what's supported; calls to unsupported methods raise `NotImplementedError`. Callers (workflow_engine submission task; dashboard surfaces) handle gracefully.
- **`credential_service.SystemType.CUSTOMER`** — single SystemType value for all carriers (vs per-carrier SystemTypes). Per-carrier credential differentiation handled via `customer_credential_id` lookup key within the CUSTOMER credential blob. Matches credential_service pattern for "one SystemType per system kind, not per system instance".
- **`asyncio.to_thread` wrapping for sync libraries** — selenium/playwright sync APIs are mature; native async wrappers exist (playwright-async) but selenium-py is sync. Same wrapping pattern as `JiraAdapter` (`[D-008]`) and `SpClient` (NTLM) for consistency.

---

## Non-goals

- **Not a model hosting / web crawler / scraping framework.** Browser automation is a means to upload files; this module does not extract content, render pages for archival, or crawl carrier portals beyond the upload UI flow.
- **Not a PLM uploader.** Per `[D-054]` — `issue_tracker.upload_attachment` handles PLM uploads; this module handles carrier portal uploads. The two are coordinated by `workflow_engine` at submission time but live in separate modules.
- **Not the FR-73 carrier-package zip assembler.** Per FR-73, HILDA assembles the zip for TPM download (storage module owns the zip-store path); this module uploads individual unpacked files to the carrier (per `[D-054]` — individual files only, never zips).
- **Not a credential store.** Session cookies retrieved per call from `credential_service` per `[D-019]`; never cached on adapter instance beyond the session lifetime.
- **Not a session monitoring dashboard.** Session pool depth + age surfaced via `--diagnostic` MET records; ops handle. No live dashboard owned here.
- **Not a Ph-3+ multi-carrier-flavor router.** Web portal + JIRA-as-customer-portal flavors are deferred Ph-3+ surfaces; when they land, they'll be peer subclasses (or peer modules) of `GoogleDriveBaseAdapter`, not nested inside it.
- **Not the FR-68 PLM hash-sync verifier.** That verifier is in `issue_tracker` per `[D-054]` (post-dispatch hash sync against PLM). This module provides `file_hash_local` in `CarrierUploadResult` for the verifier to consume; verification itself lives elsewhere.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate` (CAD codes registered in `error_codes.py`).
- `credential_service` — `get_credential(pm_id, SystemType.CUSTOMER)` per upload; returns per-carrier session cookie blob.
- `storage` — `read_file(NSDPath)` for local file bytes (the file lives at `DocumentItemAssociation.local_nsd_path` for the source item per `[D-055]`); `log_communication(...)` for FR-42 audit trail.
- `template_schema` — `DeliveryItemBase.target_folder` + `no_customer_upload` consumed by callers; `CustomerDeliveryModality.GoogleDrive` (per template_schema cascade 2026-06-08) consumed.
- `selenium` (3rd party) — primary browser automation library; wrapped in `asyncio.to_thread`.
- `playwright` (3rd party, alternative) — secondary; selectable via `CustomerAdapterConfig.browser_engine`.
- Chromium binary (host dependency) — installed via docker-compose image build OR pre-installed on host; ops responsibility.

---

## Depended on by

- `workflow_engine` (Module #8 -- foundation + 10/18 ActionKinds in commit `96a498f`) — fires `QueueSubmission` action which invokes `upload_attachment` per item via Celery task body at `tasks/submission.QUEUE_SUBMISSION` (stub-pending until this module lands per D6 cascade); gates per `DeliveryItemBase.no_customer_upload`; composes the fully-resolved carrier path per FR-77 + NFR-21 amendment §6 per D5 cascade before passing to `upload_attachment(target_folder=...)`; advances `delivery_state` to `SubmittedToCustomer` on success per FR-7 (via `tracker.update_delivery_state` per Module #6 + tasks/state.UPDATE_STATE chain).
- `dashboard` (Module #9 -- complete in commit `dc31949` per D8 cascade) — surfaces upload status from `CommunicationLog` rows written by this module; renders `CarrierUploadResult.carrier_file_url` as clickable TPM verification link in the document section per FR-57 (HILDA-mediated download path is not used for carrier URLs -- those go directly to the carrier domain).
- `issue_tracker` — FR-68 upload-success verification per D3 cascade 2026-06-20 (byte-level hash comparison dropped; verification now = upload-success markers); consumes `CarrierUploadResult.success` + `CarrierUploadResult.carrier_file_id`; emits ITR-W003 on verification failure (PLM side or carrier side).

---

## Deferred (Ph-2 / Ph-3+)

- **Ph-2: `get_status(carrier_file_id) -> CarrierStatus`** — query carrier-side status of an uploaded file (e.g., "Pending review", "Approved", "Rejected"); per-carrier capability-flagged. Revisit trigger: customer-feedback workflow surfaces.
- **Ph-2: `post_comment(carrier_file_id, comment_text)`** — post a comment / response to carrier on a uploaded file (per-carrier capability-flagged). Revisit trigger: customer-feedback inbox UI build.
- **Ph-2: `fetch_feedback(carrier_file_id) -> list[CarrierFeedback]`** — fetch customer feedback / approval status updates; per-carrier capability-flagged.
- **Ph-2: Carrier-side hash verification** — per `CarrierCapabilityFlags.supports_hash_verification`; some Google Drive deployments expose file properties view for SHA-256 extraction. Currently `file_hash_carrier` may be None on Ph-1.
- **Ph-3+: Web portal flavor** — `WebPortalBaseAdapter` peer to `GoogleDriveBaseAdapter` for carriers using their own web portals; per-carrier subclass extends similarly.
- **Ph-3+: JIRA-as-customer-portal flavor** — `JiraPortalBaseAdapter` peer for carriers using JIRA as their customer submission system; reuses `issue_tracker` JIRA adapter mechanics.
- **Ph-3+: Per-PM credential isolation** per DEF-14 + `[D-019]` v2 — currently Ph-1/Ph-2 returns shared ops-team session per `[D-019]` impl note 2026-05-24; per-PM Vault-backed lands at Ph-3+.
- **Ph-3+: Automated selector refresh detection** — currently selectors version bumps are manual ops runbook items; Ph-3+ could add a passive monitor that flags broken selectors via CAD-E006 + emits a refresh advisory.

---

## Test interface

```
python -m core.src.customer_adapter.customer_adapter_cli --diagnostic
```
Validates Chromium availability, loads all per-carrier selector configs from `customizations/customer_adapter/`, validates each selector pack against the schema, probes credential_service for each registered carrier credential. Emits no proprietary content:
```
RPT|CAD|run-00001|2026-06-09T10:00:00Z|chromium_available=true|chromium_version=119.0|carriers_total=2|carriers_config_valid=2|credentials_resolvable=2|browser_engine=selenium
```

```
python -m core.src.customer_adapter.customer_adapter_cli --mock
```
Spins up `MockCustomerAdapter` pre-registered with canned `CarrierUploadResult` responses; integration tests run end-to-end without Chromium.

```
python -m core.src.customer_adapter.customer_adapter_cli --invoke --carrier <slug> --file <fixture.pdf> --target-folder <path>
```
Performs one real upload against the carrier's Google Drive folder using current selectors + session cookie. Emits a `CAD-MET`:
```
MET|CAD|run-00001|2026-06-09T10:00:00Z|carrier=carrier_alpha|selectors_version=gdrive-2026.06|file_size_kb=1024|upload_started_at=...|cold_start=false|latency_ms=14200|success=true|file_id_extracted=true|capability_flags=supports_upload:true,supports_session_reuse:true,supports_hash_verification:false
```
The `--invoke` mode never logs the file content or carrier UI text — only selector_version, latency, file size, success/error code, capability flags per NFR-2.

```
python -m core.src.customer_adapter.customer_adapter_cli --contract --carrier <slug>
```
Runs a structural contract suite against the carrier subclass: validates `CarrierCapabilityFlags` declared; validates Protocol method surface intact; validates selector pack loads without errors. No real upload. Per-carrier OK / FAIL.

**QC template** (`CAD:upload_quality` — registered in `diagnostics/qc.py`):
```
Fields: customer_id (enum: registered carrier slugs), selectors_version (str — bounded
        per-deployment list), file_size_bucket (enum: small | medium | large | xlarge),
        cold_start (bool), latency_bucket (enum: fast | normal | slow | timeout),
        success (bool), capability_supports_upload (bool),
        capability_supports_hash_verification (bool),
        result (enum: OK / WARN / FAIL — FAIL when success=false; WARN when
        latency_bucket=slow OR capability_supports_hash_verification=false)
```

---

<!-- BEGIN:STRUCTURE -->
[DRAFT] No code present yet (only empty `__init__.py`) — architecture-phase doc-first design intent. Structure regeneration skipped per regen-map spec; will populate from code on first /switch-phase development pass.
<!-- END:STRUCTURE -->
