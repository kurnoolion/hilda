# Module: customer_adapter

> **Status:** Initial draft 2026-06-09 + 2026-06-24 architect cascade revisit (D1-D11) + **2026-06-25 binding-API finalization second pass (D12-D17 per `[D-116]` Ratified)**. Per-customer subclass pattern + Google-Drive thin-wrapper baseline. **Final implementation strategy per `[D-116]`**: HILDA's `GoogleDriveBaseAdapter` wraps the architect's pre-existing **selenium-backed self-contained** Google Drive binding via the locked 8-arg API `uploadAttachment(Model_No, milestone_name, source_dir, target_dir, filename, pm_id, pm_password, totp_code) -> bool`. HILDA owns Protocol contract + `CommunicationLog` discipline + per-call credential composition (3-tuple + pyotp TOTP generation). Binding owns selenium / session login / MFA / UI selectors / target-folder auto-creation / post-upload verification. NO HILDA-side session pool or selector pack in Ph-1. Sections curated; code implementation begins after `/switch-phase development`.
>
> **Rollback log:**
> - **2026-06-26 (D-126 cascade CLOSED -- [D-116] D13 follow-up resolved per architect Q1+Q2+Q3 locks)** — **Q1 (binding 9th arg)**: `CustomerAdapter.upload_attachment` + `GoogleDriveBaseAdapter.upload_attachment` + `_invoke_binding` + `MockCustomerAdapter.upload_attachment` + `diagnostics_cli` + `customizations/customer_adapter/example_adapter.py` scaffold all extended to take `customer_delivery_info` as 9th positional arg per (B-α) -- binding composes `<customer_delivery_info>/<device_id>/<milestone_name>/<target_dir>/<filename>` internally. Replaces the previous binding-baked customer-root framing. **Q2 (modality per-customer)**: `customer_delivery_modality` MOVED from `DeliveryItemBase` (per-item) to `CustomerTemplateBase` (per-customer top-level). One modality per customer; subclass-implicit at runtime. Removed from `DefaultWorkItemConfig` dict in `tracker/default_workitem.py` + from `manual_override.py` overridable list + from template.yaml per-item entries (14 dropped in mock_customer.yaml; equivalent in MMK pending architect's local paste per Point 3 [D-125]). `no_customer_upload` is sole upload gate per FR-80. **Q3 (CAD-E010)**: validation in `GoogleDriveBaseAdapter.upload_attachment` step 0 -- raises `CAD-E010 "customer_delivery_info required for upload"` when missing/empty (data-config error -- SP UI engineer must provision the field). **Also**: `customer_delivery_credential_id` REMOVED per `[D-019]` shared HILDA ops-team identity. **Tests**: 755/755 passing; 30/30 customer_adapter + CAD-E010 count assertion updated 14→15. **Anchors**: D-126 ADR; closes the 2026-06-26-morning D13 follow-up flag.
>
> - **2026-06-25 (binding-API finalization second pass per `[D-116]`)** — Q&A loop with architect locked the user's pre-existing Google Drive binding API (selenium-backed, self-contained). **D12 — Final binding signature** locked: `uploadAttachment(Model_No, milestone_name, source_dir, target_dir, filename, pm_id, pm_password, totp_code) -> bool`. Bool return (True=success, False=upload-completed-but-post-verify-failed); raises on infrastructure failure (network/selenium/auth/MFA/file-not-found). Binding auto-creates `target_dir` under `<customer-baked-root>/<Model_No>/<milestone_name>/` if missing. **D13 — Path composition boundary (B-α)**: HILDA passes identifier **components** (Model_No, milestone_name, target_dir) — NOT a fully-resolved Drive target path; binding composes Drive path internally. **REVERSES D5 cascade target-side framing**: D5's "FULLY-RESOLVED path" applies to LOCAL source path only (passed as `source_dir`); target side is component-pass. Customer-specific Drive folder naming conventions stay in Cline's domain per `[D-027]` Teacher/Student split. Customer_id is implicit via per-customer subclass at `customizations/customer_adapter/<customer_id>_adapter.py` (Drive root baked in); NO `customer_id` arg on binding. **D14 — Sub-modules shrink (Ph-2 forward-looking only)**: binding is self-contained, so `session_manager.py` + `selector_loader.py` + `capability_flags.py` DROPPED from Ph-1 Sub-modules + Public surface — moved to Deferred (Ph-2 forward-looking IF a non-binding-backed customer modality lands per `[D-054]` Ph-2+ `WebPortalBaseAdapter`). `MockCustomerAdapter` + `diagnostics_cli.py` retained. Net Ph-1 module ~400 lines vs original ~600-800 estimate. **D15 — Credential model**: `credential_service.get_credential(pm_id, SystemType.CUSTOMER, customer_id=...)` returns `CustomerCredential(user_id, password, totp_seed)` 3-tuple. HILDA generates the current 6-digit TOTP code per upload via `pyotp.TOTP(totp_seed).now()`; passes (user_id, password, totp_code) to binding per call. No session-cookie blob; no HILDA-side session pool. Long-lived seed sops-encrypted Ph-1 per `[D-038]`; short-lived TOTP code ephemeral in memory per upload (NEVER logged). **D16 — `CarrierUploadResult` Ph-1 reduced**: `carrier_file_id` and `carrier_file_url` are `None` Ph-1 per architect direction 2026-06-25 (extracting Google Drive file URLs via selenium is fragmented across 5+ patterns — `drive.google.com/file/d/<id>/view` for PDF/images/Office vs `docs.google.com/{document,spreadsheets,presentation}/d/<id>/edit` for native Docs/Sheets/Slides — selector-fragile; not worth Ph-1 cost). Ph-2 revisit per `dashboard` FR-57 fallback policy. **D17 — New CAD-W005 + pyotp dependency**: `CAD-W005` clock-skew warning if HILDA host clock drifts beyond TOTP tolerance (~±25s); diagnostic-mode check + upload-time check. New 3rd-party dep `pyotp` (pure-Python MIT). **Operational dependency change**: Chromium binary requirement from `[D-054]` impl note 2026-06-05 is now binding-side (Cline's Work PC concern) — HILDA host no longer requires Chromium directly per Ph-1 thin-wrapper pattern.
> - **2026-06-24 (architect cascade revisit — 11 drift items applied)** — strict-order module-by-module sweep Module #10 of 13. **D1 — slug → id rename per `[D-091]`**: `customer_id` → `customer_id` throughout (per-customer subclass file paths at `customizations/customer_adapter/<customer_id>_adapter.py` + `<customer_id>_adapter_config.yaml`; config key `customer_id` in TaskBinding constructor params; CarrierCapabilityFlags lookup key). **D2 — `CustomerAdapterConfig` typo → `CustomerAdapterConfig`** (3 sites at lines referencing `CustomerAdapterConfig.browser_engine` / `CustomerAdapterConfig.session_max_age_s`). **D3 — FR-68 PLM-Carrier hash sync DROPPED per architect direction 2026-06-20**: requirements.md FR-68 (B) hash-match logic dropped; verification now reduced to upload-success markers — (i) PLM `uploadAttachment` return result (in issue_tracker module's scope, not here); (ii) carrier-portal UI file-existence check (this module's scope — per-folder list-files check that the dispatched filename exists at the FR-77 Type-2 target path). `CarrierCapabilityFlags.supports_hash_verification` retained as Ph-2 forward-looking but no longer required for FR-68 Ph-1 verification. **D4 — `[D-054]` PLM-Carrier hash-sync anchor narrowed**: anchor now scoped to "individual files only, never zips" semantic; byte-level hash comparison portion is out of scope per architect direction 2026-06-20. **D5 — FR-77 path composition per NFR-21 amendment §6 (2026-06-21)**: `upload_attachment(target_folder=...)` receives the FULLY-RESOLVED path = `customer_delivery_info + delivery_path_template_expanded + target_folder` (composed by workflow_engine's submission task body via FR-77 dispatch logic). customer_adapter does NOT compose paths; it consumes the resolved string. `customer_delivery_info` example: `drive.google.com`; `delivery_path_template_expanded` example: `OEM_Folder1/OEM_Folder2/MODEL-A/P1`; `target_folder` example: `TestReports/Power`. Final resolved path: `drive.google.com/OEM_Folder1/OEM_Folder2/MODEL-A/P1/TestReports/Power`. **D6 — workflow_engine integration acknowledgment**: `workflow_engine.tasks.submission.QUEUE_SUBMISSION` task body (commit `96a498f` -- 10/18 ActionKinds registered; QUEUE_SUBMISSION is among the 8 pending downstream module landing) is stub-pending until this module's code lands. Once customer_adapter Ph-1 dev complete, register the task binding + activate QUEUE_SUBMISSION in workflow_engine's `tasks/submission.py`. **D7 — credential_service Ph-1 cascade**: `get_credential` signature updated 2026-06-21 to `get_credential(pm_id, system_type, customer_id=None)` per credential_service mini-cascade; for SystemType.CUSTOMER the customer_id is required (per-customer scope per `SYSTEM_CRED_SCOPE = {CUSTOMER: PER_CUSTOMER}`). `customer_adapter` callers pass the resolved customer_id from `EntityRef`. **D8 — dashboard integration**: `dashboard` (Module #9, dev complete 2026-06-24 commit `dc31949`) surfaces upload status from `CommunicationLog` rows written here; `CarrierUploadResult.carrier_file_url` renders as a clickable TPM verification link in the dashboard's document section per FR-57. **D9 — Status header refresh (this entry)**. **D10 — Anchors update**: adds `[D-091]` (slug → id rename throughout). **D11 — User direction 2026-06-24 — thin-wrapper implementation strategy**: HILDA's `GoogleDriveBaseAdapter` is a Protocol-conformant thin wrapper around the user's existing Google Drive API binding. HILDA owns (a) the `CustomerAdapter` Protocol contract; (b) `CarrierUploadResult` shape; (c) CommunicationLog discipline per FR-42; (d) `CarrierCapabilityFlags` per-customer surface; (e) selector pack versioning + session pool management. User-provided binding owns the actual `upload(file_bytes, target_folder)` -> `(file_id, file_url)` call. Per `[D-027]` Teacher/Student split: HILDA-side Protocol scaffold + thin wrapper authored by Claude on Personal PC; concrete API call body filled in by Cline on Work PC using the user's existing implementation (no proprietary API details on public github per NFR-2).
> - **2026-06-09 (initial draft)** — final remaining Phase B Module rollback per STATUS.md In-progress 2026-06-08. Draft scoped to **Ph-1 only**: Google Drive baseline + per-carrier subclass + browser automation per `[D-054]` impl note 2026-06-05 (selenium/playwright on headless Chromium; selector versioning per-deployment; capability flags per-carrier). Ph-2 (`get_status` / `post_comment` / `fetch_feedback`) noted in `## Deferred` only. Ph-3+ (web portal flavor + JIRA-as-customer-portal flavor per `[D-054]`) noted in `## Deferred`. Anchors FR-19, FR-68 (PLM-Carrier hash sync invariant; narrowed 2026-06-20 per D4 cascade — byte-level hash comparison dropped), FR-73 (carrier-package zip flow), FR-77 (target_folder outbound carrier path), FR-80 (`no_customer_upload` gate), NFR-2 (no proprietary content in compact reports). New error code prefix `CAD` per `diagnostics.PREFIX_REGISTRY`.

**Purpose**: Single Protocol-mediated surface (`CustomerAdapter`) for HILDA's outbound carrier submission — upload individual document files (per `[D-054]` — individual files only, never zips; byte-level hash comparison out of scope per D4 cascade 2026-06-20) to each carrier's submission destination + emit `CommunicationLog` entries per FR-42 + return upload-success bool (Ph-1) / `CarrierUploadResult` with rich metadata (Ph-2 forward-looking). **Path composition boundary per D13 cascade 2026-06-25 (B-α lock)**: caller (`workflow_engine.tasks.submission.QUEUE_SUBMISSION`) resolves the LOCAL source path via storage layout + passes IDENTIFIER COMPONENTS for Drive target side: `Model_No` (device_id), `milestone_name`, `target_dir` (per-item `target_folder`). Customer_adapter passes these to the binding, which composes the actual Drive path internally per customer-baked root. `customer_id` is implicit via per-customer subclass instance — NOT a binding arg. Ph-1/Ph-2 scope: **Google Drive only** per `[D-054]`. **Implementation strategy per `[D-116]` 2026-06-25 Ratified**: `GoogleDriveBaseAdapter` is a thin Protocol-conformant wrapper around the user's pre-existing **selenium-backed self-contained** Google Drive binding. HILDA owns Protocol contract + `CommunicationLog` discipline per FR-42 + per-call credential composition (`CustomerCredential` 3-tuple + pyotp TOTP generation) + clock-skew diagnostic (CAD-W005). Binding owns selenium / session login / MFA / UI selectors / target-folder auto-creation / post-upload verification. Per-customer subclass at `customizations/customer_adapter/<customer_id>_adapter.py` carries the customer-baked Drive root + any customer-specific binding configuration. Anchors `[D-007]` (on-prem; binding runs on HILDA PC inside corporate network boundary), `[D-019]` (credential_service for HILDA shared ops-team PM identity Ph-1/Ph-2), `[D-027]` (Teacher/Student boundary for proprietary binding internals), `[D-038]` (sops-encrypted credential vault), `[D-054]` (Google-Drive-only; selenium-in-HILDA framing superseded by `[D-116]` for Ph-1), `[D-091]` (slug → id rename), `[D-107]` (credential_service scope-aware routing), `[D-116]` (thin-wrapper + binding API lock); serves FR-19, FR-42, FR-57 (upload status surfaced via CommunicationLog), FR-68 (Ph-1 verification = binding's bool return per D3 cascade), FR-77 (consumes identifier components for target side per D13 cascade; consumes resolved source path), FR-80 (`no_customer_upload` gate), NFR-1, NFR-2.

**Workload assignment**: `hilda-worker` Celery task pool per `[D-021]` — submission tasks are background, minutes-scale per-upload due to binding's selenium-backed UI automation (10–100× slower than REST). `hilda-api` reads submission status from `CommunicationLog` for SP UI surfaces but does not invoke the binding directly. **Chromium binary is a hard ops dependency on the binding's runtime host per `[D-116]` D17 cascade** — typically same host as HILDA PC; managed by the binding (Cline's Work PC concern), not by HILDA's customer_adapter directly. NTP-synced clock is also a hard ops dependency on HILDA PC per D17 cascade (CAD-W005 surfaces drift).

**Per-upload latency (Ph-1 reality)**: ~10–60s per file via binding's selenium-backed flow (cold-start MFA login + per-file upload + post-upload verify). Per-customer worker concurrency: serial per binding instance (selenium UI throttles parallel uploads through same browser session); inter-customer submissions parallelize freely via separate per-customer `<customer_id>_adapter.py` binding instances. HILDA does NOT maintain a session pool per `[D-116]` D14 — each upload triggers per-call auth via the 8-arg binding API; the binding internally caches its own session if it chooses.

---

## Sub-modules (Ph-1 per `[D-116]` D14)

```
core/src/customer_adapter/
  __init__.py
  protocol.py                       ← interfaces: CustomerAdapter, CarrierUploadResult
  google_drive_base.py              ← GoogleDriveBaseAdapter thin-wrapper reference class -- composes
                                      CustomerCredential 3-tuple from credential_service; generates TOTP
                                      via pyotp; invokes binding's uploadAttachment(...) per [D-116]
  totp.py                           ← thin pyotp wrapper + CAD-W005 clock-skew detection
  diagnostics_cli.py                ← --diagnostic / --mock / --invoke modes
  customer_adapter_cli.py           ← user-facing wrapper for ops debugging
  tests/
  MODULE.md                         ← this file

customizations/customer_adapter/
  <customer_id>_adapter.py         ← per-customer subclass: carries customer-baked Drive root + binding
                                      configuration; concrete uploadAttachment(...) call body filled in by
                                      Cline on Work PC per [D-027] Teacher/Student split
  <customer_id>_adapter_config.yaml ← per-customer configuration (binding-related kwargs if any)
  README.md                         ← onboarding doc for adding a new customer
```

**Ph-2 forward-looking sub-modules** (NOT in Ph-1 scope per D14 cascade 2026-06-25; deferred until a non-binding-backed customer modality lands — see `## Deferred`):
- `session_manager.py` — only needed if HILDA owns the selenium browser pool (e.g., for a future `WebPortalBaseAdapter` peer per `[D-054]` Ph-3+)
- `selector_loader.py` — only needed if HILDA owns the UI selectors directly (binding owns them today)
- `capability_flags.py` — `CarrierCapabilityFlags` declaration becomes meaningful when there are differing per-customer surfaces (Ph-1 has one self-contained binding pattern)

---

## Public surface

### `protocol.py`

```python
@dataclass(frozen=True)
class CarrierUploadResult:
    """Per FR-19 + FR-42 + FR-57 -- return from CustomerAdapter.upload_attachment().
    Ph-1 shape per [D-116] D16 cascade 2026-06-25: success bool + minimal audit metadata;
    carrier_file_id + carrier_file_url are None (binding returns bool only; Drive URL
    extraction deferred Ph-2 per Google Drive's 5-pattern URL fragmentation for native
    Docs/Sheets/Slides vs PDF/images/Office files)."""
    success:               bool                 # True = uploaded + post-verified by binding
                                                # False = binding completed but post-verify failed
    uploaded_filename:     str                  # original filename as uploaded (preserved per FR-57)
    device_id:             str                  # Model_No passed to the binding
    milestone_name:        str                  # milestone YAML key passed to the binding
    target_dir:            str                  # Drive subdirectory passed to the binding (per-item target_folder)
    upload_started_at:     datetime
    upload_completed_at:   datetime
    error_code:            str | None           # CAD-E001..CAD-E0NN when success=False or raised
    error_detail:          str | None           # bounded enum token (NFR-2 -- no proprietary content)
    # Ph-2 forward-looking fields (NOT populated in Ph-1; binding returns only bool):
    carrier_file_id:       str | None = None    # Google Drive file ID; Ph-2 per D16 cascade
    carrier_file_url:      str | None = None    # Drive viewer URL; Ph-2 per D16 cascade

class CustomerAdapter(Protocol):
    """All callers depend on this Protocol, not on a concrete subclass. Implementations:
    GoogleDriveBaseAdapter (Ph-1 reference); per-customer subclasses in customizations/;
    MockCustomerAdapter (tests)."""

    source_system: str                         # immutable; identifies the customer_id

    async def upload_attachment(
        self,
        device_id:           str,              # Model_No; e.g., "MODEL-A"
        milestone_name:      str,              # milestone YAML key; e.g., "P1"
        source_dir:          Path,             # LOCAL NSD directory holding the file
                                               # (storage layout resolved by caller per storage Protocol)
        target_dir:          str,              # Drive subdirectory under <customer-baked-root>/<Model_No>/<milestone_name>/
                                               # (per-item DeliveryItemBase.target_folder); binding composes full Drive path
        filename:            str,              # basename only; e.g., "abc.report"
    ) -> CarrierUploadResult:
        """Per [D-116] D13 (B-α) lock 2026-06-25 -- HILDA passes IDENTIFIER COMPONENTS for the
        Drive target side; binding composes the full Drive path internally per customer-baked
        root + (Model_No, milestone_name, target_dir, filename). customer_id is implicit via the
        per-customer subclass instance. Credentials (pm_id, pm_password, totp_code) are resolved
        + injected by GoogleDriveBaseAdapter from credential_service per call; never passed to
        Protocol-level callers."""

    async def health(self) -> dict[str, Any]:
        """Returns {ready: bool, customer_id: str, ntp_skew_s: float | None}. Used by --diagnostic."""
```

### `google_drive_base.py`

```python
class GoogleDriveBaseAdapter(CustomerAdapter):
    """Thin-wrapper reference class per [D-116] Ratified 2026-06-25. Per-customer subclass
    at customizations/customer_adapter/<customer_id>_adapter.py carries the customer-baked
    Drive root + concrete uploadAttachment(...) call body (filled in by Cline on Work PC per
    [D-027] Teacher/Student split; never lands on public github per NFR-2).

    GoogleDriveBaseAdapter wraps the binding by:
    1. Resolving credential_service.get_credential(pm_id, SystemType.CUSTOMER, customer_id=...)
       -> CustomerCredential(user_id, password, totp_seed)
    2. Generating the current 6-digit TOTP code via pyotp.TOTP(totp_seed).now()
    3. Invoking the binding's uploadAttachment(...) with the 8-arg signature locked in [D-116] D12
    4. Wrapping the bool return into CarrierUploadResult + emitting CommunicationLog per FR-42
    5. Detecting CAD-W005 clock skew per --diagnostic + per upload (best-effort NTP check)
    """

    source_system:        str                  # subclass overrides -- equals customer_id
    customer_id:          str                  # baked into subclass; same value as source_system
    pm_id:                str                  # shared HILDA ops-team Google account user_id; baked
                                               # into subclass via per-customer .env / config

    def __init__(
        self,
        config:             "CustomerAdapterConfig",
        credential_service: CredentialService,
    ) -> None:
        """Constructs the adapter. NO session pool, NO selector pack, NO capability flags --
        all binding-internal per [D-116] D14 cascade."""

    async def upload_attachment(
        self,
        device_id: str, milestone_name: str, source_dir: Path,
        target_dir: str, filename: str,
    ) -> CarrierUploadResult:
        """Per [D-116] D12 + D13 (B-α). Flow:
        1. Resolve CustomerCredential 3-tuple via credential_service.
        2. Generate totp_code via pyotp.TOTP(cred.totp_seed).now().
        3. (Optional, --diagnostic-mode) Check NTP skew; emit CAD-W005 if >25s.
        4. Invoke binding's uploadAttachment(device_id, milestone_name, str(source_dir),
           target_dir, filename, cred.user_id, cred.password, totp_code) -> bool.
        5. Wrap into CarrierUploadResult. Emit CommunicationLog row per FR-42.
        6. Return CarrierUploadResult."""

    async def health(self) -> dict[str, Any]:
        """Returns {ready: bool, customer_id: str, ntp_skew_s: float | None}."""
```

The concrete `uploadAttachment(...)` call body is filled in by per-customer subclass in `customizations/customer_adapter/<customer_id>_adapter.py` — that subclass holds the binding-import + invocation. Per `[D-027]` Teacher/Student: HILDA scaffold here defines the abstract Protocol; Cline on Work PC fills in the binding-specific subclass.

### `totp.py`

```python
def current_totp(seed: str) -> str:
    """Returns the current 6-digit TOTP code derived from the base32 seed via pyotp.
    Pure-function wrapper around pyotp.TOTP(seed).now()."""

def ntp_skew_seconds() -> float | None:
    """Returns abs(local time - NTP server time) in seconds. Best-effort: returns None if
    NTP is unreachable. Emits CAD-W005 if skew > 25s. Called by --diagnostic + best-effort
    pre-upload check."""
```

### `MockCustomerAdapter`

```python
class MockCustomerAdapter:
    """In-process mock for tests. No selenium, no binding. Returns canned CarrierUploadResult
    per registered (device_id, milestone_name, target_dir, filename) tuple."""

    source_system: str = "mock_customer"
    customer_id:   str = "mock_customer"

    def register_upload_result(
        self,
        device_id: str, milestone_name: str, target_dir: str, filename: str,
        result: CarrierUploadResult,
    ) -> None: ...

    async def upload_attachment(
        self, device_id, milestone_name, source_dir, target_dir, filename,
    ) -> CarrierUploadResult: ...
```

### Configuration

```python
class CustomerAdapterConfig(BaseModel):
    """Per `[D-025]` + `[D-038]` 3-tier (CLI > env > config/customer_adapter.json).
    Slimmed per [D-116] D14 cascade -- binding owns selenium / Chromium / session pool /
    upload timeout / cold-start timeout internally."""
    customizations_dir:   Path  = Path("/etc/hilda/customizations/customer_adapter")
    ntp_skew_warn_s:      float = 25.0      # CAD-W005 threshold; TOTP window ~30s
    diagnostic_ntp_check: bool  = True      # disable in air-gapped test rigs
```

Credentials per D15 cascade 2026-06-25 + `[D-107]`: `credential_service.get_credential(pm_id, SystemType.CUSTOMER, customer_id=<resolved>)` returns `CustomerCredential(user_id, password, totp_seed)` per-customer 3-tuple. Per `[D-019]` Ph-1/Ph-2 model, this is the ops-team-provisioned shared HILDA PM identity (per-PM Vault isolation Ph-3+ per DEF-14 + `[D-019]` v2). The `totp_seed` is the long-lived ~20-char base32 string captured during MFA setup; HILDA generates the ephemeral 6-digit code per upload via `totp.current_totp(seed)`. Seed NEVER leaves credential_service vault except into the GoogleDriveBaseAdapter call frame (in-memory only; never logged).

---

## Invariants

- **Ph-1/Ph-2: Google Drive only** per `[D-054]` (selenium-in-HILDA framing superseded by `[D-116]` for Ph-1 — binding owns selenium). Web portal flavor + JIRA-as-customer-portal flavor deferred to Ph-3+. No per-customer code in `core/`; only the `GoogleDriveBaseAdapter` thin-wrapper reference class.
- **Individual files only** per `[D-054]` — `upload_attachment` accepts one file at a time. Never uploads zip archives. FR-73 carrier-package "two-click" flow assembles the zip in HILDA storage (FR-73) for TPM download; carrier submission still uploads individual files unpacked.
- **Path composition (B-α) per `[D-116]` D13 2026-06-25** — HILDA passes identifier components for the Drive target side (`device_id`, `milestone_name`, `target_dir`, `filename`); binding composes the full Drive path internally per `<customer-baked-root>/<Model_No>/<milestone_name>/<target_dir>/<filename>`. LOCAL source path is fully-resolved by HILDA (via storage Protocol) + passed as `source_dir`. NO `customer_id` arg on the binding -- implicit via per-customer subclass instance.
- **Upload-success verification per FR-68 (narrowed 2026-06-20 per D3 cascade)** — FR-68 (B) byte-level hash-match logic DROPPED; verification = binding's bool return (binding internally does post-upload list-files / file-existence check at the composed Drive target path). On verification failure: `ITR-W003 — Upload verification failed for file '{filename}' item '{item_id}' channel '{plm | carrier}'` (issue_tracker emits; per-row flag affordance per FR-56 (c) routes TPM to HILDA-rendered document section per `[D-074]` for manual reconciliation). `carrier_file_id` + `carrier_file_url` retained as Ph-2 forward-looking on `CarrierUploadResult` but `None` Ph-1 per D16 cascade.
- **`no_customer_upload` gate per FR-80** — when `DeliveryItemBase.no_customer_upload = True`, this adapter is NOT invoked for that item; caller (workflow_engine submission task) skips. Symmetric with PLM upload skip per `[D-054]`.
- **Per-call credentials, no HILDA-side session pool per `[D-116]` D14/D15 2026-06-25** — each upload triggers `credential_service.get_credential(...)` -> `CustomerCredential(user_id, password, totp_seed)` -> pyotp TOTP code generation -> binding invocation with (pm_id, pm_password, totp_code). HILDA does NOT cache sessions, cookies, or TOTP seeds beyond the call frame. Binding internally caches its own selenium session if it chooses.
- **Chromium binary is NOT a HILDA dependency in Ph-1** per `[D-116]` D17 — binding owns selenium / Chromium internally; HILDA host requires only Python + pyotp + the binding-import line in `customizations/<customer_id>_adapter.py`. NTP-synced clock IS a HILDA dependency (CAD-W005 surfaces drift).
- **Customer-baked Drive root per `[D-116]`** — per-customer subclass at `customizations/customer_adapter/<customer_id>_adapter.py` carries the customer's Drive root path baked in (e.g., one customer uses `drive.google.com/OEM-Folder1/<MODEL>/<MILESTONE>/`; another uses `drive.google.com/CarrierX/Submissions/<MODEL>/<MILESTONE>/`); naming conventions stay in Cline's domain per `[D-027]`.
- **No credential material in logs / reports** per NFR-2 / `[D-002]`. `pm_password` + `totp_seed` + `totp_code` NEVER logged, written to disk, or echoed to compact reports. Logged fields are bounded enum tokens + size buckets + success bools only.
- **No proprietary content in compact reports** per NFR-2 / `[D-002]`. CAD-RPT / -MET / -FIX / -QC records emit `customer_id`, `device_id`, `milestone_name`, `target_dir`, `filename` (already non-credential), latency_ms, file_size_kb, success bool, error_code, NTP skew — never carrier UI text, file content, or carrier-side identifiers (file_id/url are None in Ph-1 anyway).
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
CAD-W004  Selector-pack version mismatch: subclass expects '{subclass_version}', YAML provides '{yaml_version}' (carrier '{customer_id}') -- Ph-2 forward-looking (no Ph-1 use per [D-116] D14)
CAD-W005  Clock skew exceeds TOTP tolerance: HILDA host {skew_s}s off NTP server; TOTP window is ~30s; uploads will fail until clock resyncs (recommend `systemctl status systemd-timesyncd` or equivalent)
```

---

## Key choices

- **`[D-054]`** — Ph-1/Ph-2 customer_adapter scope is **Google Drive only** (carriers accepting submissions via shared Google Drive folders). Web portal + JIRA-as-customer-portal flavors deferred to Ph-3+.
- **`[D-116]` thin-wrapper strategy 2026-06-25 (supersedes `[D-054]` impl note 2026-06-05 for Ph-1)** — HILDA's `GoogleDriveBaseAdapter` wraps the architect's pre-existing self-contained selenium-backed binding via the 8-arg `uploadAttachment` API. HILDA owns Protocol contract + CommunicationLog + per-call credential composition. Binding owns selenium / Chromium / session / MFA / UI selectors / target-folder auto-creation. Net Ph-1 module ~400 lines.
- **Path composition (B-α)** — HILDA passes identifier components (Model_No, milestone_name, target_dir) for the Drive target side; binding composes the full Drive path internally per customer-baked root. Avoids HILDA needing to know per-customer Drive folder naming conventions. Local source path IS fully-resolved by HILDA via storage Protocol.
- **Per-customer subclass pattern at `customizations/`** — each customer's subclass carries the customer-baked Drive root + the concrete binding-invocation body. Aligns with `[D-003]` adapter pattern + `[D-027]` Teacher/Student (proprietary specifics live in `customizations/`, filled in by Cline on Work PC).
- **TOTP code generated per upload by HILDA, not stored in binding** — long-lived `totp_seed` lives in credential_service sops vault per `[D-038]`; HILDA generates ephemeral 6-digit code via `pyotp.TOTP(seed).now()`; passes to binding. Binding remains stateless re: credentials.
- **`credential_service.SystemType.CUSTOMER` 3-tuple per `[D-107]` + D15 cascade 2026-06-25** — returns `CustomerCredential(user_id, password, totp_seed)` per customer_id; the 3-tuple flows into `GoogleDriveBaseAdapter` call frame only (never to higher-level Protocol callers).
- **Asyncio + Celery wrapping for sync binding** — binding's selenium internals are sync; HILDA's `GoogleDriveBaseAdapter.upload_attachment` wraps the binding call in `asyncio.to_thread` per `structure-conventions.md` Sync-API wrapping convention + `[D-008]` pattern (same as `JiraAdapter`).

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
- `credential_service` — `get_credential(pm_id, SystemType.CUSTOMER, customer_id=...)` per upload; returns `CustomerCredential(user_id, password, totp_seed)` per D15 cascade + `[D-107]`.
- `storage` — local NSD path resolved by caller (workflow_engine submission task) per storage Protocol; HILDA passes `source_dir` to the binding which reads `<source_dir>/<filename>` to obtain file bytes. `log_communication(...)` for FR-42 audit trail.
- `template_schema` — `DeliveryItemBase.target_folder` (= `target_dir` arg) + `no_customer_upload` (skip gate) + `device_id` + `milestone_name` (identifier args) consumed by callers; `CustomerDeliveryModality.GoogleDrive` (per template_schema cascade 2026-06-08) consumed.
- `pyotp` (3rd party, NEW per D17 cascade 2026-06-25) — pure-Python MIT-licensed; `pyotp.TOTP(seed).now()` generates current 6-digit TOTP code.
- **Architect's Google Drive binding** (`customizations/customer_adapter/<customer_id>_adapter.py` import target) — pre-existing selenium-backed self-contained Python module providing `uploadAttachment(...)` per `[D-116]` D12 8-arg signature. Filled in by Cline on Work PC; never imported in HILDA `core/`.
- **selenium / Chromium / playwright** are NOT direct HILDA dependencies in Ph-1 per D17 cascade — they are the binding's internal dependencies (managed by Cline on Work PC). HILDA host requirement reduces to Python + pyotp + NTP-synced clock.

---

## Depended on by

- `workflow_engine` (Module #8 -- foundation + 10/18 ActionKinds in commit `96a498f`) — fires `QueueSubmission` action which invokes `upload_attachment` per item via Celery task body at `tasks/submission.QUEUE_SUBMISSION` (stub-pending until this module lands per D6 cascade); gates per `DeliveryItemBase.no_customer_upload`; passes IDENTIFIER COMPONENTS per D13 (B-α) — `device_id`, `milestone_name`, `source_dir` (LOCAL NSD path resolved via storage Protocol), `target_dir` (= `DeliveryItemBase.target_folder`), `filename`; advances `delivery_state` to `SubmittedToCustomer` on success per FR-7 (via `tracker.update_delivery_state` per Module #6 + tasks/state.UPDATE_STATE chain).
- `dashboard` (Module #9 -- complete in commit `dc31949` per D8 cascade) — surfaces upload status from `CommunicationLog` rows written by this module; renders `CarrierUploadResult.carrier_file_url` as clickable TPM verification link in the document section per FR-57 (HILDA-mediated download path is not used for carrier URLs -- those go directly to the carrier domain).
- `issue_tracker` — FR-68 upload-success verification per D3 cascade 2026-06-20 (byte-level hash comparison dropped; verification now = upload-success markers); consumes `CarrierUploadResult.success` + `CarrierUploadResult.carrier_file_id`; emits ITR-W003 on verification failure (PLM side or carrier side).

---

## Deferred (Ph-2 / Ph-3+)

- **Ph-2: `carrier_file_id` + `carrier_file_url` populated on `CarrierUploadResult`** per `[D-116]` D16 — extract Drive file URL post-upload. Requires selecting an extraction strategy across the 5-pattern Drive URL fragmentation matrix (drive.google.com/file/d/ for binaries vs docs.google.com/{document,spreadsheets,presentation}/d/ for native Docs/Sheets/Slides). Either (a) binding-side post-upload navigation + DOM extraction; (b) HILDA-side separate `get_file_metadata(...)` call. Revisit when dashboard FR-57 clickable verification link is requested by TPMs.
- **Ph-2: `session_manager.py` sub-module** per `[D-116]` D14 — only needed if HILDA owns the selenium browser pool (e.g., for a future `WebPortalBaseAdapter` peer per `[D-054]` Ph-3+).
- **Ph-2: `selector_loader.py` sub-module + `SelectorPack`** per `[D-116]` D14 — only needed if HILDA owns the UI selectors directly (binding owns them today). `CAD-W004` selector-pack mismatch warning retained but dormant.
- **Ph-2: `capability_flags.py` sub-module + `CarrierCapabilityFlags`** per `[D-116]` D14 — meaningful when there are differing per-customer surfaces (Ph-1 has one self-contained binding pattern).
- **Ph-2: `get_status(...)`** — query carrier-side status of an uploaded file (e.g., "Pending review", "Approved", "Rejected"). Revisit trigger: customer-feedback workflow surfaces.
- **Ph-2: `post_comment(...)`** — post a comment / response to carrier on an uploaded file. Revisit trigger: customer-feedback inbox UI build.
- **Ph-2: `fetch_feedback(...)`** — fetch customer feedback / approval status updates.
- **Ph-3+: Web portal flavor** — `WebPortalBaseAdapter` peer to `GoogleDriveBaseAdapter` for carriers using their own web portals; per-customer subclass extends similarly. Requires HILDA to own the selenium stack (un-defers Ph-2 sub-modules above).
- **Ph-3+: JIRA-as-customer-portal flavor** — `JiraPortalBaseAdapter` peer for carriers using JIRA as their customer submission system; reuses `issue_tracker` JIRA adapter mechanics.
- **Ph-3+: Per-PM credential isolation** per DEF-14 + `[D-019]` v2 — currently Ph-1/Ph-2 returns shared ops-team identity per `[D-019]` impl note 2026-05-24; per-PM Vault-backed lands Ph-3+.
- **Ph-3+: Automated TOTP-seed rotation detection** — currently if the shared HILDA PM account's MFA is re-set, ops manually update the seed in sops; Ph-3+ could detect auth-fail-due-to-seed-rotation + emit a refresh advisory.

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

- `AuditWriter` — class — pub — Subset of `storage` audit-log interface this module depends on.
- `CarrierUploadResult` — class — pub — Per FR-19 + FR-42 + FR-57 -- return shape from CustomerAdapter.upload_attachment.
- `CustomerAdapter` — class — pub — All callers depend on this Protocol, not on a concrete subclass.
- `CustomerAdapterConfig` — class — pub — Operational config -- environment-switching values only.
- `GoogleDriveBaseAdapter` — class — pub — Thin-wrapper reference class for Google Drive customer submissions.
- `MockCustomerAdapter` — class — pub — In-process mock honoring the CustomerAdapter Protocol.
- `current_totp` — func — pub — Returns the current 6-digit TOTP code derived from a base32 seed via pyotp.
- `ntp_skew_seconds` — func — pub — Best-effort SNTP probe; returns abs(local_time - ntp_time) in seconds.

<!-- END:STRUCTURE -->
