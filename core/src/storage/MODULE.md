# Module: storage

> **Status:** Initial draft (2026-05-24). Sections curated; pending section-by-section user review before contract is finalized. Code implementation begins after `/switch-phase development`.

## Purpose

Owns HILDA's internal persistence — Postgres (SQLAlchemy 2.x async + Alembic) for the document index, `CommunicationLog`, BATCH-id idempotency cache, FR-31 runtime overrides, and Celery result backend; Redis client (Celery broker Ph-1/Ph-2 per `[D-022]`; cache-only Ph-3+ per `[D-043]`); NSD SMB client for the two-tree document store per `[D-013]` / `[D-041]`. **Authoritative for FR-31** (PM/TPM runtime override persistence — the override table IS this module). **Contributes to** FR-13 (NSD path construction + writes; authoritative module is `email_service` for the ingest flow logic), FR-15 (timestamp persistence; `tracker` is authoritative for the state transitions that update them), FR-52 / FR-55 (document index queries supporting `[D-039]` classification + NSD polling; `email_service` is authoritative for the classification and polling logic), FR-57 (document enumeration data layer; `dashboard` is authoritative for the HTTP API surface), FR-67 / FR-68 (document index drives PLM cleanup + sync diff; `issue_tracker` and `customer_adapter` are authoritative for the PLM/carrier action paths). NFR-15 is a deployment-level NFR owned by SYSTEM.md + `deploy/`, not by any single module. Canonical Pydantic models per `[D-046]` live here as the source-of-truth schema from which SP List provisioning script, Alembic migrations, and YAML template-schema spec are generated.

## Public surface

### Pydantic models (canonical schema source per `[D-046]`)

```python
class DocumentIndexRow:
    """Natural key: (delivery_item_id, doc_type, doc_id_slug, rev_number) per FR-57."""
    delivery_item_id: str          # FK → SharePoint DeliveryItems
    plm_id: str | None             # PLM issue ID per FR-26; null while Ph-2 deferred upload pending
    doc_type: DocType              # enum: test_report | tech_report | waiver
    doc_id_slug: str               # slugified first-received filename; stable across revisions per FR-57
    rev_number: int                # 1 for new, ≥2 for revisions per FR-17
    plm_attachment_id: str | None
    upload_timestamp: datetime
    ingest_source: IngestSource    # enum: Email | CorporatePLM | NetworkSharedDrive | SharePointUI
    local_classified_path: Path    # NSD path per FR-13
    original_filename: str         # as-received; preserved without transformation per FR-57
    file_hash: str                 # SHA-256 per [D-039] Step 0
    first_page_excerpt: str        # for [D-039] Tier-2 LLM new-vs-revision classification
    is_final: bool                 # FR-66 version-selection result; auto-true in Ph-1 (single revision)
    download_url: str              # HILDA-mediated per NFR-16; resolves to NSD classified path
    parser_result: dict | None     # FR-16 test-report parser output (null for tech_report / waiver)
    llm_review_findings: dict | None  # FR-53 LLM quality review; null when review_required=false
    from_zip: bool                 # FR-72 ZIP ingest source flag
    source_zip_filename: str | None  # original ZIP filename when from_zip=true

class CommunicationLogRow:
    """Append-only audit trail per NFR-6. No UPDATE/DELETE on this table."""
    log_id: str                    # PK
    delivery_item_id: str | None
    device_id: str | None
    channel: Channel               # enum: Email | Messenger | CorporatePLM | NetworkSharedDrive | CustomerJIRA | SharePoint
    direction: Direction           # enum: Inbound | Outbound
    sender: str | None
    recipients: str | None
    subject: str | None
    summary: str | None            # LLM-generated for inbound where applicable; never raw proprietary content
    external_message_id: str | None
    credential_id: str | None      # opaque reference to credential_service blob — never plaintext
    action_type: str | None        # e.g., submission | resubmission | bulk_close | get_credential | ...
    attachments: list[dict]        # [{filename, download_url}, ...] — never file content
    timestamp: datetime

class BatchIdempotencyKey:
    """Per [D-012]. Stored in Redis (short-TTL), not Postgres."""
    batch_id: str
    item_index: int
    status: str
    ttl_seconds: int = 86400       # 24h max per [D-012] short-TTL invariant

class AutomationRuleOverride:
    """FR-31 runtime override (precedence over YAML rule files per FR-30)."""
    scope: Scope                   # enum: Device | Customer | Global
    scope_id: str | None           # device_id | customer_id | NULL (Global)
    rule_id: str
    parameter_name: str
    parameter_value: str           # serialized; type-validated by rule_engine at read time
    set_by_pm_id: str
    set_at: datetime
    expires_at: datetime | None

# Helper enums and value types (DocType, IngestSource, Channel, Direction, Scope) live alongside
# the Pydantic models in this module's models.py. Canonical source per [D-046]; generators emit
# SP List provisioning script + Alembic migrations + YAML schema spec from these definitions.
```

### Postgres ORM API (async SQLAlchemy 2.x)

```python
# Session management
async def get_session() -> AsyncIterator[AsyncSession]: ...
async def init_db() -> None: ...                                  # creates schema if missing — dev/test only

# DocumentIndex operations (FR-13, FR-17, FR-52, FR-55, FR-57)
async def add_document_index_row(row: DocumentIndexRow) -> None
    """Idempotent on natural key per FR-57 — no duplicate rows on retry."""

async def get_document_index_row(
    delivery_item_id: str, doc_type: DocType, doc_id_slug: str, rev_number: int
) -> DocumentIndexRow | None

async def find_doc_id_slugs_for_item(
    delivery_item_id: str, doc_type: DocType
) -> list[str]
    """Supports [D-039] Step 1 (slug match) and Step 2 (NEW_DOCUMENT short-circuit when empty)."""

async def list_revisions(
    delivery_item_id: str, doc_type: DocType, doc_id_slug: str
) -> list[DocumentIndexRow]
    """Supports FR-60 expandable history view (Ph-2)."""

async def update_review_findings(
    delivery_item_id: str, doc_type: DocType, doc_id_slug: str, rev_number: int,
    parser_result: dict | None, llm_review_findings: dict | None
) -> None

async def set_is_final(
    delivery_item_id: str, doc_type: DocType, doc_id_slug: str, rev_number: int,
    is_final: bool
) -> None
    """FR-66 version-selection support. Setting is_final=true on revN auto-sets false on all others
    for the same (delivery_item_id, doc_type, doc_id_slug)."""

# CommunicationLog operations (append-only per NFR-6)
async def log_communication(row: CommunicationLogRow) -> None
    """Append-only; never updates or deletes existing rows."""

async def query_communications(
    *, delivery_item_id: str | None = None, device_id: str | None = None,
    channel: Channel | None = None, since: datetime | None = None, limit: int = 100
) -> list[CommunicationLogRow]

# FR-31 runtime override operations
async def get_active_overrides(
    scope: Scope, scope_id: str | None, rule_id: str
) -> list[AutomationRuleOverride]
    """Returns overrides active at `now`. Caller (rule_engine) applies Device → Customer → Global
    precedence per FR-30 (Postgres override > YAML rules)."""

async def set_override(override: AutomationRuleOverride) -> None
async def clear_override(scope: Scope, scope_id: str | None, rule_id: str, parameter_name: str) -> None
```

### Redis client

```python
# Broker role (Ph-1/Ph-2 only — read by workflow_engine for Celery init)
def get_celery_broker_url() -> str
    """Reads from HILDA_REDIS_URL env. Ph-3+: workflow_engine calls get_rabbitmq_broker_url
    instead per [D-043]."""

# Cache role (both phases)
async def cache_set(key: str, value: bytes, ttl_seconds: int) -> None
async def cache_get(key: str) -> bytes | None

# BATCH-id idempotency per [D-012]
async def check_batch_idempotency(batch_id: str, item_index: int) -> str | None
    """Returns existing status if (batch_id, item_index) was already recorded; None otherwise."""

async def record_batch_idempotency(key: BatchIdempotencyKey) -> None
    """Idempotent — re-recording the same (batch_id, item_index, status) is a no-op."""
```

### NSD client per `[D-013]` / `[D-041]`

```python
class NSDPath:
    """Encapsulates the two-tree NSD path structure per FR-13."""

    @classmethod
    def inbound_drop(cls, carrier_slug: str, device_slug: str, milestone_slug: str, item_slug: str) -> NSDPath:
        """Owner inbound tree: \\share\hilda\inbound\<carrier>\<device>\<milestone>\<item>\"""

    @classmethod
    def internal_classified(
        cls, carrier_slug, device_slug, milestone_slug, tg_name_slug, item_slug,
        doc_type_slug, doc_id_slug, rev_number
    ) -> NSDPath:
        """HILDA internal classified path: ...\internal\<carrier>\<device>\<milestone>\<tg>\<item>\<doc_type>\<doc_id>\revN\"""

    @classmethod
    def internal_staged(cls, ..., doc_type_slug, original_filename) -> NSDPath:
        """Staged holding for [D-039] Tier-2-ambiguous documents."""

    @classmethod
    def internal_zip_store(cls, ..., item_slug, original_zip_filename) -> NSDPath:
        """FR-72 per-item NSD-sourced ZIP storage."""

    @classmethod
    def internal_un_resolved_zip(cls, ..., tg_name_slug, original_zip_filename) -> NSDPath:
        """FR-72 TG-scoped Email/PLM-sourced ZIP storage."""

    @classmethod
    def internal_outbound(cls, ..., item_slug) -> NSDPath:
        """HILDA-generated artifacts (QC reports, diagnostics, submission outputs); never owner deliverables."""

    def to_unc(self) -> str                # \\share\hilda\... — used by hilda-api internally only
    def to_download_token(self) -> str     # scoped token for HILDA-mediated URL per FR-61

async def read_file(path: NSDPath) -> AsyncIterator[bytes]
    """Streams file from NSD; used by hilda-api download endpoint (FR-61)."""

async def write_file(path: NSDPath, content: AsyncIterable[bytes]) -> None
    """Writes via hilda-svc identity per [D-013]. Idempotent on (path, content) — re-write of
    identical bytes is a no-op."""

async def compute_file_hash(path: NSDPath) -> str
    """SHA-256 per [D-039] Step 0 (exact-duplicate detection)."""

async def list_inbound_drops(
    carrier_slug, device_slug, milestone_slug, item_slug
) -> list[NSDPath]
    """FR-55 polling support — returns files dropped by owners since last poll."""

async def extract_first_page(path: NSDPath) -> str
    """First-page text extraction for [D-039] Tier-2 LLM comparison; supports PDF, DOCX, XLSX, DOC."""
```

### Alembic migration interface

- Migrations live under `core/src/storage/migrations/`
- Ph-1/Ph-2 deploy: `docker compose run --rm hilda-api alembic upgrade head` per `[D-026]` impl note 2026-05-24
- Ph-3+ MicroK8s: init container on `hilda-api` per SYSTEM.md §4
- Migrations are idempotent and backward-compatible with the running prior version

### Independent test interface per `[D-005]`

```bash
# Live diagnostic (against real Postgres + Redis + NSD mount; corp AD context)
python -m core.src.storage.storage_cli --diagnostic
# Emits STR-RPT:
#   STR OK 3s postgres_ping=Y alembic_head=<rev_id> redis_ping=Y nsd_mount=Y
#   STR MET doc_index_rows=12450 comm_log_rows=348201 overrides_active=7

# Mock mode — no external IO
python -m core.src.storage.storage_cli --mock
# Uses SQLite in-memory + fakeredis + in-memory filesystem; deterministic timestamps.

# Mock-postgres mode (CI / local dev with real Postgres semantics but no real corp data)
python -m core.src.storage.storage_cli --mock-postgres
# Spins up test Postgres container; runs Alembic head; emits STR-RPT against it.

# Schema validation
python -m core.src.storage.storage_cli --validate --customer <slug>
# Validates that current Pydantic models can round-trip a synthetic DeliveryItem
# matching the customer's template_schemas/<slug>/template.yaml. STR-QC report.

# Alembic round-trip test (CI gate per [D-046])
python -m core.src.storage.storage_cli --alembic-roundtrip
# upgrade head → downgrade base → upgrade head — verifies migrations are reversible.
```

## Invariants

- **Document index natural key uniqueness**: `(delivery_item_id, doc_type, doc_id_slug, rev_number)` is enforced via Postgres unique constraint per FR-57.
- **`add_document_index_row` is idempotent on natural key**: retry-safe; no duplicate rows.
- **`CommunicationLog` is append-only**: no UPDATE or DELETE on existing rows per NFR-6 audit semantics.
- **All NSD writes go through `hilda-svc` AD identity** per `[D-013]`. **`CORP\hilda-svc` is a dedicated Active Directory service account — not a HILDA process or container.** It owns Modify permission on the NSD share (`\\share\hilda\`); the HILDA PC's host-level SMB mount authenticates to the corp file server as `hilda-svc` via Kerberos keytab. From the corp file server's perspective every write appears as `hilda-svc`, regardless of which HILDA container (hilda-api, hilda-worker) actually performed the write — the containers see a pre-mounted local filesystem and write to it normally. Per-PM attribution lives in HILDA's `CommunicationLog` (application layer), not in the filesystem ACL.
- **NSD-classified path is the source of truth for in-progress deliverables** per `[D-040]`; submission assembly reads from there per `[D-041]`.
- **Redis cache TTL ≤ 24 hours** per `[D-012]` short-TTL invariant; no durable state in Redis.
- **Async-native**: all DB and Redis IO via async SQLAlchemy + aioredis. SMB IO is sync (via `smbprotocol`) — wrapped in `asyncio.to_thread` per `structure-conventions.md` Sync-API wrapping convention.
- **Schema is canonical** per `[D-046]`: Pydantic models in `core/src/storage/models.py` are the single source from which the SP List provisioning script, Alembic migrations, and YAML template-schema spec are generated. CI gates enforce sync — schema-shape drift between Pydantic models and any of the three generated artifacts is a hard build failure.
- **No credential material stored, logged, or transmitted**: this module never writes decrypted credentials to Postgres, Redis, NSD, logs, compact reports, or error messages. `credential_id` in `CommunicationLogRow` is an opaque reference; resolution to plaintext is `credential_service`'s exclusive responsibility per `[D-019]`.
- **Error-code contract**: all module errors raised as `PipelineError` with `STR-E001..STR-W001` codes registered in `core/src/diagnostics/error_codes.py` per `[D-002]` + `[D-017]`. Compact reports (RPT/MET/QC) emitted per `[D-002]` use only counts, status flags, and bounded enum tokens — never file content or proprietary identifiers.
- **NSD path-construction is deterministic from entity attributes**: given a fixed set of slugs, `NSDPath.internal_classified(...)` returns the same path on every host (lab, dev, test). No path mutation after entity creation.

## Key choices

- `[D-022]` — Celery via Redis broker (Ph-1/Ph-2) + Postgres result backend
- `[D-043]` — Ph-3+ broker migration to RabbitMQ Quorum Queues; Redis retained as cache-only
- `[D-013]` — NSD ACL model: `hilda-svc` writes only; HILDA-mediated reads
- `[D-039]` — Document classification pipeline (hash → slug → Tier-2 LLM → staged/) is consumed here; this module exposes the persistence primitives the classification logic in `email_service` / `llm` uses
- `[D-040]` — NSD classified path = in-progress source of truth; PLM = submitted deliverables only
- `[D-041]` — Submission assembly source = NSD classified path in both phases
- `[D-045]` — Schema/content boundary invariant (data model gated by code release)
- `[D-046]` — Canonical schema source = Pydantic models in this module
- `[D-012]` — BATCH-id idempotency stored in Redis with short TTL (not Postgres)
- **SQLAlchemy 2.x async + Alembic** chosen as the ORM stack (vs Django ORM / peewee / raw asyncpg) — broad ecosystem, async-native, mature Alembic; reference implementation also uses this stack
- **`aioredis`** chosen (vs blocking `redis-py`) for async-native client matching the rest of HILDA
- **`smbprotocol` library** chosen for Linux SMB access (vs `pysmb`) — actively maintained, supports SMB 2/3, async-friendly via `asyncio.to_thread`
- **NSD path encapsulated as `NSDPath` value object** (vs raw strings) — type safety, prevents hand-rolled path bugs, single point of update when path conventions evolve (cf. the 2026-05-14 + 2026-05-21 path-convention rewrites)

## Non-goals

- Does **NOT** cache decrypted credentials. Per `[D-019]` credential discipline, `credential_service` is the only path to decrypted material; this module sees only opaque `credential_id` references.
- Does **NOT** own SP REST client. That's `sharepoint_integration`. This module mirrors SP data into Postgres for fast query; the canonical SP write path goes through `sharepoint_integration`.
- Does **NOT** own the **SP List provisioning script** as code. It's generated from this module's Pydantic models per `[D-046]`, but the generator lives in `deploy/` (or a separate `core/src/schema_generators/` module — to be decided in architecture phase).
- Does **NOT** do document classification. That's `email_service` + `llm` per FR-52 + `[D-039]`. This module only stores classification results.
- Does **NOT** own NSD ACL configuration. Corp infra concern per `[D-013]`.
- Does **NOT** do Celery task dispatch. That's `workflow_engine`. This module only provides the Redis broker URL.
- Does **NOT** own LLM call dedup or prompt-template storage. `llm` module's concern.
- Does **NOT** own SP-alert email parsing. That's `email_service.sp_alert_parser` per `[D-047]`. This module persists the parsed events into `CommunicationLog`.
- Does **NOT** do file format conversion or OCR. PDF/DOCX/XLSX/DOC text extraction is local to `extract_first_page` for `[D-039]` Tier-2 LLM only; richer extraction belongs to `test_report` and `test_report_profiler`.

## Depends on

- `core/src/diagnostics/` — `STR-` error codes registered + RPT/MET/QC compact-report schemas
- `core/src/template_schema/` — canonical entity enums (DocType, IngestSource, Channel, Direction, Scope, DeliveryState, etc.) per `[D-028]`; Pydantic base classes for entity hierarchy

## Depended on by

- `tracker` — entity CRUD via Postgres mirror + SP sync
- `workflow_engine` — Celery broker URL + result backend connection
- `rule_engine` — reads `AutomationRuleOverride` for FR-31 precedence ordering
- `email_service` — `CommunicationLog` writes + BATCH-id idempotency checks + NSD reads for inbound classification
- `messenger` — `CommunicationLog` writes
- `issue_tracker` — `CommunicationLog` writes + document index updates from PLM polls
- `customer_adapter` — `CommunicationLog` `action_type` writes for submission / resubmission events
- `test_report` — `parser_result` writes to document index per FR-16; NSD reads for parsing
- `dashboard` — document enumeration API reads (FR-57); `CommunicationLog` reads for audit views
- `credential_service` — `CommunicationLog` writes for every `get_credential` call per NFR-6

## Deferred

- **Postgres HA / read replicas** — Ph-3+ per `[D-043]`; Ph-1/Ph-2 = single Postgres container with `restart: unless-stopped`
- **Per-owner NSD `inbound/` ACL** — DEF-16 (Ph-3+); Ph-1/Ph-2 uses a shared write group for all NSD owners
- **Filesystem identity attribution in `CommunicationLog`** — DEF-16 (Ph-3+); Ph-1/Ph-2 records `ingest_source = NetworkSharedDrive` without per-owner attribution
- **Cross-region replication** — out of Ph-1 / Ph-2 / Ph-3+ scope (no current trigger)
- **Postgres connection pooling tuning** — implementation-phase concern; defaults in `config/storage.json`
- **Sharded document index** — out of Ph-1 / Ph-2 scope; one Postgres instance handles one customer comfortably. Revisit at Ph-3+ if multi-customer scale-out (DEF-8) introduces volume that warrants sharding.
- **NSD content-addressable storage** — files are stored by classified path, not by content hash; deduplication is a Ph-3+ idea if storage volume warrants

<!-- BEGIN:STRUCTURE -->
<!-- END:STRUCTURE -->
