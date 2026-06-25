# Module: credential_service

**Purpose**: Single read-only interface (`get_credential(pm_id, system_type) -> Credential`) that returns the credential material every outbound adapter needs to authenticate against external systems (corp PLM via gateway, customer JIRA, corp messenger via gateway, customer portals, email mailbox, SharePoint service account). Owns the sops-encrypted `.env` file layout, age-key decryption at process start, and the in-memory credential cache for the process lifetime. Anchors `[D-019]`, `[D-038]`, `[D-052]` (tri-backend LLM credential split per impl note 2026-06-08); serves FR-51, FR-42, NFR-3, NFR-4.

**Ph-1/Ph-2 model per `[D-019]` impl note 2026-05-24**: HILDA operates with a **shared ops-team credential set per customer system**, not per-PM credentials. The `get_credential(pm_id, ...)` interface accepts a `pm_id` argument and is stable across phases, but Ph-1/Ph-2 returns the shared ops-team credential under the hood regardless of `pm_id`. Per-PM provisioning + isolation lands at Ph-3+ alongside Vault per DEF-14. NFR-3 (per-PM isolation) is therefore an interface-level invariant in Ph-1/Ph-2, not an enforced runtime property — attribution lives in `CommunicationLog` per FR-42.

**Workload assignment**: In-process import in `hilda-api`, `hilda-worker`, `hilda-beat`, and `hilda-llm-gateway` per `[D-021]`. No dedicated container; decryption happens once at each container's startup. The age key is mounted read-only from the host into every workload container per `[D-038]`.

---

## Public surface

### `protocol.py`

```python
@dataclass(frozen=True)
class Credential:
    pm_id:       str                # PM attribution token (Ph-1/Ph-2: ops-team identifier)
    system_type: str                # bounded enum — see SystemType below
    auth_type:   Literal["api_token", "basic", "basic_totp", "ntlm", "kerberos", "oauth2_bearer"]
    # Value carriers below are set per credential, by auth_type. For basic_totp (per `[D-116]`
    # D15 cascade 2026-06-25 -- HILDA shared ops-team Google identity for customer_adapter):
    # username + password + totp_seed are all required; HILDA generates the ephemeral 6-digit
    # TOTP code at upload time via pyotp.TOTP(totp_seed).now().
    api_token:   str | None = None  # api_token
    username:    str | None = None  # basic, basic_totp, ntlm
    password:    str | None = None  # basic, basic_totp, ntlm — never logged, never __repr__'d
    totp_seed:   str | None = None  # basic_totp (added per `[D-116]` D15 2026-06-25) -- long-lived
                                    # ~20-char base32 string from Google MFA setup; sops-encrypted
                                    # at rest per `[D-038]`; NEVER logged, never __repr__'d
    keytab_path: Path | None = None # kerberos
    bearer:      str | None = None  # oauth2_bearer
    expires_at:  datetime | None = None  # Ph-3+ Vault populates; Ph-1/Ph-2 always None

    def __repr__(self) -> str:
        """Returns 'Credential(pm_id=..., system_type=..., auth_type=...)' only.
        No secret material in the repr. Used by diagnostics dumps."""

    def value_carriers_consistent(self) -> bool:
        """True when the populated value carriers match auth_type's requirement.
        Backs the --validate QC surface. (Added at implementation 2026-06-11.)"""
```

```python
class SystemType(str, Enum):
    """Bounded set of external system kinds credential_service serves credentials for.
    Tri-backend LLM split per `[D-052]` impl note 2026-06-08 — was single LLM_GATEWAY pre-2026-06-09.

    **Per-system credential scope** (architect lock 2026-06-21):
    - Per-(account, customer): ISSUE_TRACKER (customer JIRA only in Ph-1; pm_id=account_id)
    - Per-customer: CUSTOMER (Google Drive per FR-19/77; customer_id required)
    - Single shared: EMAIL, SHAREPOINT, LLM_OLLAMA_A4000, LLM_VLLM_DGX, LLM_CORP_LLM
    - **No HILDA-credential** (forward-compat enum-only; Ph-1/Ph-2 always raises CRD-E001):
      MESSENGER — corp messenger gateway uses IP-allowlist + gateway-side auth;
                  HILDA passes corp identity as API parameter, not credential.
      (ISSUE_TRACKER lookups for corp PLM also raise CRD-E001 — see FR-25 (a)
      pattern (d); only customer JIRA has a credential under ISSUE_TRACKER.)"""
    ISSUE_TRACKER     = "issue_tracker"      # customer JIRA in Ph-1; corp PLM is no-credential per FR-25 (a)
    MESSENGER         = "messenger"           # NO HILDA-credential in Ph-1/Ph-2 — IP-allowlist + gateway-side auth
    CUSTOMER          = "customer"            # customer portal / submission system (Ph-1: Google Drive per `[D-054]` + thin-wrapper per `[D-116]`); per-customer; auth_type=basic_totp 3-tuple (username + password + totp_seed) per D15 cascade 2026-06-25
    EMAIL             = "email"               # IMAP/SMTP mailbox per `[D-016]`; single shared
    SHAREPOINT        = "sharepoint"          # SP service account (NTLM/Kerberos); single shared
    # LLM tri-backend per `[D-052]` impl note 2026-06-08 (split 2026-06-09 from single LLM_GATEWAY); single shared per backend:
    LLM_OLLAMA_A4000  = "llm_ollama_a4000"   # Ollama on RTX A4000 box (lab subnet); typically no auth or basic per per-deployment policy
    LLM_VLLM_DGX      = "llm_vllm_dgx"        # vLLM on DGX Spark box (lab subnet); typically no auth or basic per per-deployment policy
    LLM_CORP_LLM      = "llm_corp_llm"        # corp on-prem LLM (off-lab); API token / OAuth2 per `[D-007]` / corp policy
```

### `service.py`

```python
class CredentialService(Protocol):
    """All adapters depend on this Protocol, not on the concrete implementation.
    Enables MockCredentialService in tests and a future Vault-backed swap at Ph-3+
    with no caller-side change per [D-019].

    Signature extended 2026-06-21 with `customer_id` per FR-25 (b) cascade lock
    2026-06-19 — customer JIRA carrier governance requires per-(account, customer)
    credentials; customer adapter (Google Drive) requires per-customer credentials
    per FR-19/77. Forward-compat default `customer_id=None` preserves all prior
    call sites for single-credential systems (email, sharepoint, llm_*)."""

    async def get_credential(
        self,
        pm_id: str,
        system_type: str,
        customer_id: str | None = None,
    ) -> Credential: ...

class SopsCredentialService:
    """Ph-1/Ph-2 implementation. Reads sops-encrypted `.env` files at startup,
    decrypts via `sops --decrypt` using the age key at /etc/hilda/age.key,
    caches Credential objects in-process. Never writes to disk."""

    def __init__(
        self,
        env_dir:      Path = Path("/etc/hilda/credentials"),
        age_key_path: Path = Path("/etc/hilda/age.key"),
    ) -> None: ...

    async def get_credential(
        self,
        pm_id: str,
        system_type: str,
        customer_id: str | None = None,
    ) -> Credential:
        """Resolution order (Ph-1/Ph-2):
        1. Per-(account, customer) systems (customer_jira; pm_id = account_id):
           lookup `customer_jira/<pm_id>/<customer_id>.enc.env`. Raise CRD-E005
           if `customer_id` is None.
        2. Per-customer systems (customer; pm_id ignored in Ph-1/Ph-2):
           lookup `customer/<customer_id>.enc.env`. Raise CRD-E002 if `customer_id`
           is None.
        3. Single-credential systems (email, sharepoint, llm_*):
           lookup `<system_type>.enc.env`. `customer_id` parameter is ignored;
           `pm_id` is ignored in Ph-1/Ph-2 (per [D-019] impl note 2026-05-24
           — shared ops-team credential).
        4. corp PLM gateway / corp messenger gateway: raise CRD-E001
           (no HILDA-side credential per FR-25 (a) + FR-51 pattern (d);
           callers must use the IP-allowlist + identity-assertion pattern instead).
        5. If still absent: raise CRD-E001 with (pm_id, system_type, customer_id)."""

    async def reload(self) -> None:
        """Re-runs `sops --decrypt` on every file in env_dir and rebuilds the cache.
        Called by ops via SIGHUP after rotating an .env file (SIGHUP-only per
        strand draft decision 2026-06-11; HTTP admin endpoint deliberately
        deferred Ph-1/Ph-2). No hot-reload in normal operation."""

    async def load(self) -> int:
        """Decrypt-once-at-startup entry point; idempotent (reload() is the
        post-rotation rebuild). Returns cached credential count. Workload
        entrypoints call this once at container start. (Added 2026-06-11.)"""

    def install_sighup_handler(self) -> bool:
        """Wires SIGHUP → reload() on the running event loop. Returns False
        (no-op, never raises) where SIGHUP or add_signal_handler is unavailable
        (Windows dev boxes); production runtime is the Linux HILDA PC per
        [D-026]. (Added 2026-06-11.)"""
```

### `MockCredentialService`

```python
class MockCredentialService:
    """In-memory credential store for tests. Pre-populated by test fixtures via
    register(); raises CRD-E001 for unknown (pm_id, system_type, customer_id).
    Signature aligned with CredentialService Protocol 2026-06-21."""

    def register(self, cred: Credential, customer_id: str | None = None) -> None: ...
    async def get_credential(
        self,
        pm_id: str,
        system_type: str,
        customer_id: str | None = None,
    ) -> Credential: ...
```

---

## File layout (Ph-1/Ph-2)

**UPDATED 2026-06-21**: per FR-25 (b) cascade lock 2026-06-19 (per-account/per-customer customer JIRA credentials for carrier governance); per FR-25 (a) + FR-51 pattern (d) corp PLM is **no-HILDA-credential** (IP-allowlist + gateway-side auth); per architect confirmation 2026-06-21 corp messenger is also **no-HILDA-credential** (IP-allowlist + gateway-side auth); per Q2 architect confirmation 2026-06-21 customer adapter (Google Drive per FR-19/77) needs per-customer credentials.

**Two views of the same files** (sops-encrypted; never decrypted to disk):

```
# REPO SOURCE-OF-TRUTH (sops-encrypted; checked into git per [D-038]):
customizations/credentials/
  age.key.pub                              ← age public key (encryption-side, in git)
  # Single shared-credential systems:
  email.env.sops                           ← HILDA mailbox IMAP/SMTP
  sharepoint.env.sops                      ← SP service account (NTLM/Kerberos)
  llm_ollama_a4000.env.sops                ← may be empty / no-auth lab deployment
  llm_vllm_dgx.env.sops                    ← may be empty / no-auth lab deployment
  llm_corp_llm.env.sops                    ← corp LLM API token / OAuth2 per [D-007]
  # Per-(account, customer) — FR-25 (b) customer JIRA carrier governance:
  customer_jira/
    <account_id>/                          ← assigned_pm_id (=TPM.User_name per [D-088])
                                              OR HILDA OPS member id (corp dir slug)
      <customer_id>.env.sops               ← e.g. customer_jira/y.vasilyev/MMK.env.sops
  # Per-customer — FR-19/77 carrier portal (Ph-1: Google Drive):
  customer/
    <customer_id>.env.sops                 ← e.g. customer/MMK.env.sops

# CONTAINER VIEW (bind-mounted from customizations/credentials/ at container start
# per [D-026] Docker Compose; same encrypted files, runtime path):
/etc/hilda/
  age.key                                  ← age PRIVATE key (mode 0400, hilda-svc owned;
                                              ops-provisioned, NOT in git)
  credentials/                             ← bind-mount target of customizations/credentials/
    email.enc.env
    sharepoint.enc.env
    llm_ollama_a4000.enc.env
    llm_vllm_dgx.enc.env
    llm_corp_llm.enc.env
    customer_jira/<account_id>/<customer_id>.enc.env
    customer/<customer_id>.enc.env
```

**NO HILDA-side credential files for** (architect lock 2026-06-21):
- **corp PLM gateway** — HILDA calls gateway APIs passing the corp human id (from `customizations/issue_tracker/<corp_plm_slug>_adapter_config.yaml`) as an API parameter; gateway authenticates to corp PLM using gateway-side fixed-key infrastructure; HILDA→gateway is lab-subnet IP-allowlist. Per FR-25 (a) + FR-51 pattern (d).
- **corp messenger gateway** — same pattern as corp PLM gateway; HILDA passes corp identity as API parameter; gateway handles corp messenger auth; HILDA→gateway is lab-subnet IP-allowlist.

These two systems remain in the `SystemType` enum for forward-compatibility (Ph-3+ pattern may shift) but `credential_service` has no `.env.sops` files for them in Ph-1/Ph-2 — lookups via `get_credential` for these system_types raise `CRD-E001` by design (callers should never call for these systems per FR-51).

Each `.env.sops` declares env-var-style entries for the credential. Ph-3+ Vault path layout is `secret/hilda/<pm_id>/<system_type>/[<scope_key>]` per `[D-019]` (interface-stable migration target; loader swaps, callers don't).

**Env-var layout inside each decrypted file** (implementation 2026-06-11; ops-facing contract — see strand draft decision): `HILDA_<PREFIX>_<FIELD>`, where `<PREFIX>` comes from `SYSTEM_ENV_PREFIX` in `protocol.py` (module-prefix abbreviation where one exists — `ITR` / `MSG` / `CAD` / `EML` / `SHP`; LLM backends use the uppercased system_type). Fields: `AUTH_TYPE` (required when any credential is declared) plus the carriers that auth_type requires (`API_TOKEN`; `USERNAME` + `PASSWORD`; `KEYTAB_PATH`; `BEARER`), optional `PM_ID` (default `ops-team`) and `EXPIRES_AT` (ISO-8601). An empty or carrier-free file declares no credential — legal for the no-auth lab LLM backends (lookups raise CRD-E001); a declared-but-incomplete credential raises CRD-E004 naming the missing field. `--validate --system <type>` is the ops-side conformance check.

---

## Invariants

- **Stable interface across phases.** `get_credential(pm_id, system_type) -> Credential` signature is fixed per `[D-019]`. Callers never branch on phase; the implementation behind the Protocol changes (sops-env → Vault) without caller-side edits.
- **No credential material in logs, reports, or compact RPT records.** Anchors NFR-2 / `[D-002]`. `Credential.__repr__` returns identifiers only. RPT/MET/FIX/QC fields are counts, statuses, and bounded enum tokens — never the secret value.
- **No credential material on disk after startup.** sops decryption happens once into process memory; the decrypted plaintext is never persisted, written to a tmp file, or echoed to stdout. Only the `.enc.env` (encrypted) and the age private key (gating) live on disk.
- **The age key is the single plaintext secret on the host.** Rotation is an ops runbook item per `[D-038]` impl note 2026-05-26. credential_service does not manage the age key — it only consumes it.
- **`pm_id` is an attribution token, not an authorization key in Ph-1/Ph-2.** The same shared ops-team credential is returned regardless of `pm_id` value (per `[D-019]` impl note 2026-05-24). The argument exists so call sites and CommunicationLog entries are phase-stable.
- **Adapters never cache the returned `Credential`.** Per `[D-008]` and `JiraAdapter`'s "credentials retrieved from credential_service at each call — never stored on the instance after construction" — credential_service owns the process-lifetime cache; adapters request per call.
- **CommunicationLog gets every credential retrieval per FR-42.** Each `get_credential` call appends an entry with `(pm_id, system_type, timestamp)` — no value. Audit trail lives in `storage`'s CommunicationLog table.

---

## Error codes (CRD prefix — registered in `diagnostics/error_codes.py`)

```
CRD-E001  No credential for pm_id='{pm_id}' system_type='{system}' customer_id='{customer_id}'
CRD-E002  sops decrypt failed for '{file}': {reason}  (age key missing, corrupt, or not authorized)
CRD-E003  Unknown system_type '{system}' — not in SystemType enum
CRD-E004  Credential file '{file}' malformed: missing required field '{field}'
CRD-E005  Required customer_id missing for per-customer/per-(account,customer) system_type='{system}'  (added 2026-06-21 per FR-25 (b) cascade)
CRD-W001  Credential cache miss for pm_id='{pm_id}' — falling back to ops-team credential  (Ph-1/Ph-2 expected; absent at Ph-3+)
CRD-W002  Credential reload triggered by SIGHUP — cache rebuilt
```

---

## Key choices

- **`[D-019]`** — credential_service Ph-1 simplified to ops-provisioned shared credential per system; full Vault-backed per-PM provisioning deferred to Ph-3+ (DEF-14). The Protocol interface is the durable contract; the storage backend swaps without touching callers.
- **`[D-038]`** — sops + age over Docker Compose native secrets / ansible-vault / plaintext. Diff-friendly encrypted files in git; single plaintext secret (age key) on the host; standalone tool, no ansible coupling.
- **Process-lifetime cache, not per-call decrypt.** sops decryption is a subprocess call (~100ms); per-call decrypt would multiply that across every adapter operation. The decrypted value lives in process memory only; cache invalidated on `reload()` / process restart.
- **`pm_id` argument preserved in Ph-1/Ph-2** — could have shipped a `get_credential(system_type)` signature and added `pm_id` at Ph-3+, but that would force a caller migration at the phase boundary. Carrying the unused argument now buys phase-stable call sites at zero cost.
- **`SystemType` as a closed enum, not extensible registry** — unlike `DeliveryState` / `ItemType` / `TrackingModality` in `template_schema`, credential system types correspond to HILDA modules (issue_tracker, messenger, email, sharepoint, customer, llm_gateway). Adding a new system type is a code change anyway; closed enum matches the actual cadence.

---

## Non-goals

- **Not a credential provisioning surface.** Ops creates and rotates `.enc.env` files outside HILDA (sops + age-keygen). credential_service only reads.
- **Not a PM self-service registration UI** — deferred to Ph-3+ per DEF-14 (FR-32 deferred).
- **Not an OAuth2 refresh loop / health monitor** — deferred to Ph-3+ per DEF-14 (FR-36 deferred).
- **Not a Vault client** — Vault integration lands at Ph-3+ as a new `VaultCredentialService` class implementing the same Protocol; the sops implementation does not call Vault.
- **Not the audit log.** `CommunicationLog` writes for FR-42 are performed by the adapter that called `get_credential`, not by credential_service itself — keeps credential_service free of `storage` module dependency.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate` (CRD codes registered in `error_codes.py`).
- `sops` binary — installed on the host as an ops dependency, not a Python package. credential_service invokes it via `asyncio.create_subprocess_exec`.
- The age private key at `/etc/hilda/age.key` — provisioned by ops; credential_service reads but does not manage it.

---

## Depended on by

- `issue_tracker` (per call, never stored on adapter instance per `[D-008]`).
- `messenger` (per call).
- `customer_adapter` (per submission attempt — FR-19, FR-20).
- `email_service` (mailbox poll auth).
- `sharepoint_integration` (NTLM/Kerberos auth for SpClient — at startup, since the SP service account is process-wide not per-PM).
- `workflow_engine` (passes through to adapters; does not call directly).
- `llm` / `hilda-llm-gateway` (LLM credentials on startup — **three SystemType values per `[D-052]` tri-backend** per impl note 2026-06-08: `LLM_OLLAMA_A4000`, `LLM_VLLM_DGX`, `LLM_CORP_LLM`; `LLMGatewayServer` retrieves one credential per configured `BackendConfig` at startup).

---

## Test interface

```
python -m core.src.credential_service.credential_service_cli --diagnostic
```
Loads all `.enc.env` files, decrypts each, validates that every required env var per SystemType is present. Emits no credential values:
```
RPT|CRD|run-00001|2026-05-27T10:00:00Z|files_found=8|files_decrypted=8|files_failed=0|systems_covered=8
```

```
python -m core.src.credential_service.credential_service_cli --mock
```
Spins up `MockCredentialService` pre-loaded with synthetic credentials for every SystemType; useful for integration tests that need a credential surface without sops.

```
python -m core.src.credential_service.credential_service_cli --validate --system issue_tracker
```
Validates that the credential for the named system_type is structurally complete (auth_type set, value carriers consistent with auth_type) without printing the value. Emits `CRD-QC`:
```
QC|CRD|run-00001|2026-05-27T10:00:00Z|system=issue_tracker|present=true|auth_type=api_token|value_carriers_consistent=true|result=OK
```

No `--dry-run` — credential_service has no write surface, so dry-run is a no-op.

All CLI modes accept `--env-dir` / `--age-key` overrides (defaults `/etc/hilda/credentials` / `/etc/hilda/age.key`) so lab and test environments can point at non-production paths. (Added 2026-06-11.)

**QC template** (`CRD:credential_completeness` — registered via `core/src/credential_service/qc_templates.py` into the central `diagnostics` QC registry):
```
Fields: present (bool), auth_type (enum: api_token|basic|ntlm|kerberos|oauth2_bearer|none),
        value_carriers_consistent (bool), result (enum: OK / WARN / FAIL)
```
(`none` covers the file-absent / no-credential case so the QC record stays fixed-field — added 2026-06-11.)

---

<!-- BEGIN:STRUCTURE -->

- `Credential` — class — pub — One credential as served to adapters.
- `CredentialService` — class — pub — All adapters depend on this Protocol, not on the concrete implementation.
- `MockCredentialService` — class — pub — In-memory credential store for tests. Pre-populated by test fixtures via
- `SopsCredentialService` — class — pub — Ph-1/Ph-2 implementation. Reads sops-encrypted `.env` files at startup,
- `SystemType` — class — pub — Bounded set of external system kinds credential_service serves credentials for.

<!-- END:STRUCTURE -->
