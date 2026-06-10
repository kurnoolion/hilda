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
    auth_type:   Literal["api_token", "basic", "ntlm", "kerberos", "oauth2_bearer"]
    # Exactly one of the value carriers below is set per credential, by auth_type:
    api_token:   str | None = None  # api_token
    username:    str | None = None  # basic, ntlm
    password:    str | None = None  # basic, ntlm — never logged, never __repr__'d
    keytab_path: Path | None = None # kerberos
    bearer:      str | None = None  # oauth2_bearer
    expires_at:  datetime | None = None  # Ph-3+ Vault populates; Ph-1/Ph-2 always None

    def __repr__(self) -> str:
        """Returns 'Credential(pm_id=..., system_type=..., auth_type=...)' only.
        No secret material in the repr. Used by diagnostics dumps."""
```

```python
class SystemType(str, Enum):
    """Bounded set of external system kinds credential_service serves credentials for.
    Tri-backend LLM split per `[D-052]` impl note 2026-06-08 — was single LLM_GATEWAY pre-2026-06-09."""
    ISSUE_TRACKER     = "issue_tracker"      # corp PLM (via corp_plm_gateway) + customer JIRA
    MESSENGER         = "messenger"           # corp messenger (via corp_messenger_gateway)
    CUSTOMER          = "customer"            # customer portal / submission system (Ph-1/Ph-2 Google Drive per `[D-054]`)
    EMAIL             = "email"               # IMAP/SMTP mailbox per `[D-016]`
    SHAREPOINT        = "sharepoint"          # SP service account (NTLM/Kerberos)
    # LLM tri-backend per `[D-052]` impl note 2026-06-08 (split 2026-06-09 from single LLM_GATEWAY):
    LLM_OLLAMA_A4000  = "llm_ollama_a4000"   # Ollama on RTX A4000 box (lab subnet); typically no auth or basic per per-deployment policy
    LLM_VLLM_DGX      = "llm_vllm_dgx"        # vLLM on DGX Spark box (lab subnet); typically no auth or basic per per-deployment policy
    LLM_CORP_LLM      = "llm_corp_llm"        # corp on-prem LLM (off-lab); API token / OAuth2 per `[D-007]` / corp policy
```

### `service.py`

```python
class CredentialService(Protocol):
    """All adapters depend on this Protocol, not on the concrete implementation.
    Enables MockCredentialService in tests and a future Vault-backed swap at Ph-3+
    with no caller-side change per [D-019]."""

    async def get_credential(
        self, pm_id: str, system_type: str
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
        self, pm_id: str, system_type: str
    ) -> Credential:
        """Resolution order (Ph-1/Ph-2):
        1. Lookup (pm_id, system_type) in process cache.
        2. If absent, fall back to the shared ops-team credential for system_type.
        3. If still absent, raise CRD-E001."""

    async def reload(self) -> None:
        """Re-runs `sops --decrypt` on every file in env_dir and rebuilds the cache.
        Called by ops via SIGHUP / admin endpoint after rotating an .env file.
        No hot-reload in normal operation."""
```

### `MockCredentialService`

```python
class MockCredentialService:
    """In-memory credential store for tests. Pre-populated by test fixtures via
    register(); raises CRD-E001 for unknown (pm_id, system_type)."""

    def register(self, cred: Credential) -> None: ...
    async def get_credential(self, pm_id: str, system_type: str) -> Credential: ...
```

---

## File layout (Ph-1/Ph-2)

```
/etc/hilda/
  age.key                                      ← mode 0400, owned by hilda-svc-local
  credentials/
    issue_tracker.enc.env                      ← sops-encrypted; HILDA_ITR_*
    messenger.enc.env
    customer.enc.env
    email.enc.env
    sharepoint.enc.env
    # LLM tri-backend per `[D-052]` impl note 2026-06-08 (split 2026-06-09 from single llm_gateway.enc.env):
    llm_ollama_a4000.enc.env                   ← may be empty / no-auth in default lab deployment
    llm_vllm_dgx.enc.env                       ← may be empty / no-auth in default lab deployment
    llm_corp_llm.enc.env                       ← API token / OAuth2 per `[D-007]`
```

Each `.enc.env` declares env-var-style entries for the shared ops-team credential of that system. Ph-3+ Vault path layout is `secret/hilda/<pm_id>/<system_type>` per `[D-019]` (interface-stable migration target; loader swaps, callers don't).

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
CRD-E001  No credential for pm_id='{pm_id}' system_type='{system}'
CRD-E002  sops decrypt failed for '{file}': {reason}  (age key missing, corrupt, or not authorized)
CRD-E003  Unknown system_type '{system}' — not in SystemType enum
CRD-E004  Credential file '{file}' malformed: missing required field '{field}'
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

**QC template** (`CRD:credential_completeness` — registered in `diagnostics/qc.py`):
```
Fields: present (bool), auth_type (enum: api_token|basic|ntlm|kerberos|oauth2_bearer),
        value_carriers_consistent (bool), result (enum: OK / WARN / FAIL)
```

---

<!-- BEGIN:STRUCTURE -->
[DRAFT] No code present yet — architecture-phase doc-first design intent. Structure regeneration skipped per regen-map spec; will populate from code on first /switch-phase development pass.
<!-- END:STRUCTURE -->
