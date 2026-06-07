# Module: storage

> **Status:** Draft + 2026-06-07 rollback applied (FR-77 / FR-78 / FR-79 revised / FR-82 / FR-83 / FR-84 / `[D-053]`). Initial draft 2026-05-24. Sections curated; pending section-by-section user review before contract is finalized. Code implementation begins after `/switch-phase development`.
>
> **Rollback log:**
> - **2026-06-07** — Phase B Module rollback (Group 2 of N, after `template_schema/MODULE.md`): added `DocumentItemAssociation` M:M for FR-79 multi-item + PLM fan-out across distinct (owner × milestone) pairs; new `DocumentIndexRow` fields (`milestone_id`, `inferred_tg_name` per FR-78/FR-83, `routing_resolution`); new `RoutingResolution` enum (6 values mirroring FR-52 5-step pipeline + FR-83 reassignment); new `TGFolderRoutingRow` (FR-77 Type-2 with `ingress_folder` naming per inbound/outbound discipline); new `TagCatalogRow` (FR-82 revised); new NSDPath helpers (`internal_default_workitem` for FR-78 `_unrouted` sentinel; `ingress_folder` for FR-77 NSD1/NSD2 inbound paths); association/fan-out API methods + FR-83 `reassign_document_to_workitem`; expanded `CommunicationLogRow.action_type` example registry per FR-29 revised; new error codes (`STR-E005` / `STR-E006` / `STR-W002` / `STR-W003`); invariants added for ingress/target naming discipline, single-milestone association scope, per-association PLM attachment, channel→TG resolution, registry-based action/trigger validation, soft-deactivate tag catalog; Deferred items added for cross-milestone associations, tag-catalog audit history, default-work-item path namespace evolution.
> - **2026-06-07 (refactor, same day)** — refactored DocumentIndexRow/DocumentItemAssociation per user review: dropped muddled primary/secondary model (no `is_primary` on M:M, no `is_primary_association` on DocumentIndexRow); moved `local_classified_path`, `plm_id`, `plm_attachment_id`, `owner_email`, `upload_timestamp` from DocumentIndexRow → DocumentItemAssociation (genuinely per-(file, item)); moved `inferred_tg_name`, `routing_resolution` to DocumentIndexRow (per-ingest, not per-association); dropped `landing_item_id` (redundant — M:M row's delivery_item_id IS the landing); dropped synthetic `association_id` (composite PK `(file_hash, delivery_item_id)`); DocumentIndexRow PK changed from `(delivery_item_id, doc_type, doc_id_slug, rev_number)` to `file_hash` (file-centric model — FR-57 natural key preserved via secondary unique constraint `(milestone_id, doc_id_slug, rev_number)` + JOIN); dropped persisted `download_url` (replaced with `make_download_token(file_hash, delivery_item_id, ttl_seconds)` computed at page-render time per FR-61); new error code `STR-E007` (invalid/expired download token); two new invariants (symmetric M:M no-primary, never-persist-download-url). Net: clean single source of truth per field; no dual-write hazards; per-(file, item) physical-storage semantics preserved (same file can occupy 2+ NSD paths, one per item).

## Purpose

Owns HILDA's internal persistence — Postgres (SQLAlchemy 2.x async + Alembic) for the document index, `CommunicationLog`, BATCH-id idempotency cache, FR-31 runtime overrides, and Celery result backend; Redis client (Celery broker Ph-1/Ph-2 per `[D-022]`; cache-only Ph-3+ per `[D-043]`); NSD SMB client for the two-tree document store per `[D-013]` / `[D-041]`. **Authoritative for FR-31** (PM/TPM runtime override persistence — the override table IS this module). **Contributes to** FR-13 (NSD path construction + writes; authoritative module is `email_service` for the ingest flow logic), FR-15 (timestamp persistence; `tracker` is authoritative for the state transitions that update them), FR-52 / FR-55 (document index queries supporting `[D-039]` classification + NSD polling; `email_service` is authoritative for the classification and polling logic), FR-57 (document enumeration data layer; `dashboard` is authoritative for the HTTP API surface), FR-67 / FR-68 (document index drives PLM cleanup + sync diff; `issue_tracker` and `customer_adapter` are authoritative for the PLM/carrier action paths). NFR-15 is a deployment-level NFR owned by SYSTEM.md + `deploy/`, not by any single module. Canonical Pydantic models per `[D-046]` live here as the source-of-truth schema from which SP List provisioning script, Alembic migrations, and YAML template-schema spec are generated.

## Public surface

### Pydantic models (canonical schema source per `[D-046]`)

```python
class DocumentIndexRow:
    """One row per physical document (per FR-79 revised 2026-06-07 — file-centric model).
    Holds file-content + per-ingest properties only; per-(file, item) association data
    lives on DocumentItemAssociation. Same file_hash arriving via multiple channels is
    idempotent — first arrival wins on per-ingest fields.

    Natural key: file_hash (PK). Secondary unique constraint: (milestone_id, doc_id_slug, rev_number)
    preserves the FR-57 lookup contract — given (item, doc_id_slug, rev_number), JOIN to
    DocumentItemAssociation to resolve the file. Single-milestone invariant per FR-79 (Ph-1/Ph-2)
    keeps milestone_id stable per file_hash."""
    file_hash: str                 # PK — SHA-256 per [D-039] Step 0; cross-association identity key per FR-79
    milestone_id: str              # FK → MilestoneBase; denormalized — FR-79 single-milestone invariant guarantees stability across associations
    doc_type: DocType              # enum: test_report | tech_report | waiver | DEFAULT (per FR-7 amendment + [D-053] — catch-all; FR-16/FR-46/FR-53 do NOT fire for DEFAULT)
    doc_id_slug: str               # slugified first-received filename; stable across revisions per FR-57
    rev_number: int                # 1 for new, ≥2 for revisions per FR-17
    ingest_source: IngestSource    # enum: Email | CorporatePLM | NetworkSharedDrive | SharePointUI — channel of FIRST ingest (idempotent on retry)
    original_filename: str         # as-received; preserved without transformation per FR-57
    first_page_excerpt: str        # for [D-039] Tier-2 LLM new-vs-revision classification
    is_final: bool                 # FR-66 version-selection result; auto-true in Ph-1 (single revision)
    parser_result: dict | None     # FR-16 test-report parser output (null for tech_report / waiver / DEFAULT) — per-file property
    llm_review_findings: dict | None  # FR-53 LLM quality review; null when review_required=false OR doc_type == DEFAULT — per-file property
    from_zip: bool                 # FR-72 ZIP ingest source flag
    source_zip_filename: str | None  # original ZIP filename when from_zip=true
    # Per-ingest fields (computed once when the file is first ingested):
    inferred_tg_name: str | None   # per FR-78/FR-83 — TG resolved from inbound channel (NSD ingress_nsd / email_group_alias lookup / PLM-id reverse-lookup); null only for SP-UI direct uploads where TG is explicit on the targeted DeliveryItem. Consumed by FR-83 TPM reassignment to shortlist candidate work-items within the TG.
    routing_resolution: RoutingResolution  # per FR-52 — which 5-step pipeline step resolved this routing (per-ingest, not per-association — the pipeline runs once per ingest event regardless of how many items it resolves to)
    ingested_at: datetime          # timestamp of FIRST ingest
    # Fields explicitly dropped 2026-06-07: delivery_item_id, plm_id, plm_attachment_id,
    # local_classified_path, upload_timestamp (moved to DocumentItemAssociation per-association);
    # download_url (computed on demand via make_download_token); landing_item_id (redundant with
    # M:M delivery_item_id); is_primary_association (no primary/secondary semantic in symmetric M:M).

class RoutingResolution(str, Enum):
    """Per FR-52 5-step routing pipeline — records which step resolved the document's routing.
    Used for monitoring + dashboard surface (FR-83 unrouted-document tracking)."""
    SUBSTRING_MATCH      = "SubstringMatch"        # FR-52 step 1 — exact substring match on filename/subject vs item_name
    FUZZY_MATCH          = "FuzzyMatch"            # FR-52 step 2 — fuzzy match (rapidfuzz) above threshold
    FOLDER_ROUTING       = "FolderRouting"         # FR-77 Type-2 — ingress_folder → item_no via TGFolderRoutingRow
    LLM_ROUTE_ATTACHMENT = "LLMRouteAttachment"    # FR-52 step 4 — LLM TaskKind.ROUTE_ATTACHMENT
    STAGED_DEFAULT       = "StagedDefault"         # FR-52 step 5 / FR-78 — fell through to milestone's default work-item
    TPM_REASSIGNED       = "TPMReassigned"         # FR-83 — TPM-manual reassignment from default work-item to specific item

class DocumentItemAssociation:
    """Per FR-79 (revised 2026-06-07) — symmetric M:M between document file_hash and DeliveryItem
    within one milestone. Holds per-(file, item) properties: where the file lives on disk for
    this item, where it was uploaded in PLM for this item's owner, when. No primary/secondary
    distinction — all associations are equal.

    Composite PK: (file_hash, delivery_item_id). No synthetic association_id; CommunicationLog
    audit references the file_hash + delivery_item_id pair directly.

    Invariants:
    - All associations for a given file_hash share the same milestone_id (FR-79 same-milestone
      invariant Ph-1/Ph-2; cross-milestone deferred to Ph-3+). Enforced via STR-E005.
    - File physically exists at each association's local_classified_path — the same file may
      occupy 2+ NSD paths simultaneously (one per item). Per-item paths are independent.
    - PLM fan-out: documents upload to PLM for EVERY distinct (owner_email, plm_id) pair across
      the associations. issue_tracker iterates `fan_out_plm_associations(file_hash, milestone_id)`
      which returns DISTINCT (owner_email, plm_id) sets; each upload writes back its
      plm_attachment_id + upload_timestamp on the contributing association rows.
    - Two cases per FR-79:
        (a) one owner, N items in same TG, same file → N rows, all share (owner_email, plm_id,
            plm_attachment_id, upload_timestamp) — PLM upload happens once
        (b) two owners in same TG, N items, same file → N rows, distinct (owner_email, plm_id)
            pairs → 2 PLM uploads → distinct plm_attachment_id + upload_timestamp per row
    """
    file_hash:             str        # PK part 1 — FK → DocumentIndexRow.file_hash
    delivery_item_id:      str        # PK part 2 — FK → DeliveryItemBase
    milestone_id:          str        # denormalized from delivery_item_id → MilestoneBase; FR-79 same-milestone invariant
    local_classified_path: Path       # NSD classified path FOR THIS ITEM per FR-13 / [D-040]; file may exist at multiple paths (one per item) — user-confirmed 2026-06-07
    owner_email:           str        # denormalized from DeliveryItemBase.owner_email — fast PLM fan-out grouping
    plm_id:                str | None # PLM issue ID for THIS (owner × milestone) per FR-26 / FR-57 — denormalized from DeliveryItemBase.plm_id for query convenience
    plm_attachment_id:     str | None # per-association PLM attachment ID per FR-79 fan-out; same value across rows that share (owner_email, plm_id) within milestone (case (a)); distinct per (owner_email, plm_id) pair (case (b))
    upload_timestamp:      datetime | None  # when uploaded to THIS owner's PLM; null until issue_tracker confirms upload. Mirrors plm_attachment_id grouping per case (a)/(b)
    associated_at:         datetime  # when this association row was created
    associated_by:         str       # "auto" — FR-52 pipeline; "<pm_id>" — TPM-manual / FR-83 reassignment

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
    action_type: str | None        # free-string registry per FR-29 (revised 2026-06-05). Examples: submission | resubmission | bulk_close | get_credential | propagate_tags (FR-82) | notify_hilda_ops (FR-75) | milestone_storage_cleanup (FR-76) | halt_milestone_polling (FR-74) | final_sweep (FR-74) | instantiate_default_work_item (FR-78) | reassign_to_workitem (FR-83) | send_owner_routing_query (FR-83 Ph-2) | fan_out_plm_upload (FR-79 revised)
    attachments: list[dict]        # [{filename, download_url}, ...] — never file content
    timestamp: datetime

class BatchIdempotencyKey:
    """Per [D-012]. Stored in Redis (short-TTL), not Postgres."""
    batch_id: str
    item_index: int
    status: str
    ttl_seconds: int = 86400       # 24h max per [D-012] short-TTL invariant

class TGFolderRoutingRow:
    """Per FR-77 Type-2 routing — persisted form of TGFolderRouting/FolderRoutingEntry from
    template_schema. Loaded into routing pipeline cache at tracker creation; refreshed on
    TGGroupBase update. Replace-all write semantics — a TG's full table is overwritten on update."""
    milestone_id:    str       # PK part 1 — FK → MilestoneBase
    tg_name:         str       # PK part 2 — FK → TGGroupBase (within milestone)
    ingress_folder:  str       # PK part 3 — INBOUND folder path under TG's ingress_nsd (NOT customer-facing target_folder)
    item_no:         int       # → DeliveryItemBase.item_no within the milestone
    routing_notes:   str | None

class TagCatalogRow:
    """Per FR-82 (revised 2026-06-05) — customer-extensible tag catalog. Validated against
    DeliveryItemBase.item_description (comma-separated tag list) on ingest. Unknown tags raise
    TSC-W003 at template_schema validator + STR-W002 at storage layer (warnings, not errors —
    the catalog is customer-extensible)."""
    customer_id:  str          # PK part 1
    tag:          str          # PK part 2 — canonical tag string
    description:  str | None
    color:        str | None   # optional UI hint (hex code) for dashboard chips
    active:       bool = True  # soft-deactivate without delete; preserves historical references

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

# DocumentItemAssociation operations (FR-79 revised 2026-06-07 — symmetric M:M + PLM fan-out)
async def add_document_item_association(assoc: DocumentItemAssociation) -> None
    """Idempotent on composite PK (file_hash, delivery_item_id). Enforces same-milestone
    invariant — raises STR-E005 if file_hash already associates with a different milestone."""

async def list_associations_for_file(file_hash: str) -> list[DocumentItemAssociation]
    """Returns all (file, item) associations for a file. milestone_id is invariant across them
    per FR-79 same-milestone rule."""

async def list_associations_for_item(delivery_item_id: str) -> list[DocumentItemAssociation]
    """Used by FR-67 PLM cleanup — when an item is closed, find all documents associated."""

async def fan_out_plm_associations(file_hash: str) -> list[tuple[str, str | None]]
    """Per FR-79 revised — returns distinct (owner_email, plm_id) pairs across all associations
    of file_hash. issue_tracker iterates this list to upload to each owner's PLM (one PLM upload
    per distinct pair, regardless of how many items share that owner)."""

async def update_association_plm_attachment(
    file_hash: str, delivery_item_id: str,
    plm_attachment_id: str, upload_timestamp: datetime
) -> None
    """Called after issue_tracker successfully uploads to an owner's PLM. Updates the target
    association row + any other association rows sharing the same (owner_email, plm_id) within
    the milestone (case (a) one-owner-N-items fan-out result is replicated across all rows for
    that owner in the same transaction)."""

async def reassign_document_to_workitem(
    file_hash: str, source_delivery_item_id: str,
    target_delivery_item_id: str, pm_id: str
) -> None
    """FR-83 — TPM-manual reassignment from default work-item (or any item) to a specific
    work-item.
    1. Adds new DocumentItemAssociation row for (file_hash, target_delivery_item_id) using
       target item's owner_email + plm_id (NSD path constructed via NSDPath.internal_classified).
    2. Removes the source association row (default-work-item or prior item) — file is moved
       on NSD from source path to target path.
    3. Updates DocumentIndexRow.routing_resolution = TPMReassigned.
    4. Logs CommunicationLog action_type='reassign_to_workitem' with credential_id=pm_id and
       attachments=[{file_hash, source→target items}].
    Note: file_hash + delivery_item_id is the natural identity per the composite PK; no synthetic
    document_index_id / association_id is required."""

# Default work-item lookup (FR-78)
async def get_default_work_item_for_milestone(milestone_id: str) -> str | None
    """Returns the delivery_item_id of the milestone's auto-instantiated default work-item
    (item_type = ItemType.DEFAULT per [D-053]). Returns None if not yet instantiated — caller
    (FR-52 pipeline) should fire INSTANTIATE_DEFAULT_WORK_ITEM action via rule_engine."""

# Download token operations (FR-61 — HILDA-mediated download per NFR-16)
async def make_download_token(
    file_hash: str, delivery_item_id: str, ttl_seconds: int = 300
) -> str
    """Per FR-61 — generate a short-lived HILDA-mediated download token bound to a specific
    (file, item) association. Token encodes (file_hash, delivery_item_id) + signature + expiry.
    Token resolves server-side to DocumentItemAssociation.local_classified_path FOR THAT ITEM
    (each item's view of a shared file resolves to that item's own NSD copy).
    Used by dashboard / SP UI integration at page-render time; never persisted. Distinct from
    worker-internal SMB reads (submission assembly per FR-41 / FR-73) which use
    storage.read_file(NSDPath) directly without a URL layer."""

async def resolve_download_token(token: str) -> tuple[str, str, NSDPath]:
    """Verify token signature + TTL; return (file_hash, delivery_item_id, NSDPath) for the
    hilda-api download endpoint to stream from. Raises STR-E007 on invalid or expired token."""

# TG folder routing operations (FR-77 Type-2)
async def get_folder_routing_for_tg(milestone_id: str, tg_name: str) -> list[TGFolderRoutingRow]
    """Used by FR-52 step 3 routing pipeline. Cached in-process; refreshed on TGGroupBase update."""

async def set_folder_routing_for_tg(
    milestone_id: str, tg_name: str, entries: list[TGFolderRoutingRow]
) -> None
    """Replace-all semantics — TG's full routing table is overwritten atomically.
    Validates every entry's item_no exists on DeliveryItemBase within the milestone; raises
    STR-E006 if any item_no is unknown."""

# Tag catalog operations (FR-82 revised)
async def get_tag_catalog(customer_id: str) -> set[str]
    """Returns set of active tags for fast item_description validation. Cached in-process."""

async def upsert_tag(row: TagCatalogRow) -> None
async def deactivate_tag(customer_id: str, tag: str) -> None
    """Soft-deactivate; does not delete (preserves historical references in item_description)."""

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
        """HILDA-generated artifacts (QC reports, diagnostics, submission outputs); never owner deliverables.
        Note: FR-73 carrier-package zips are regenerated per-click and deleted on TPM download —
        callers should treat this path as transient for those artifacts."""

    @classmethod
    def internal_default_workitem(
        cls, carrier_slug, device_slug, milestone_slug, original_filename
    ) -> NSDPath:
        """Per FR-78 — landing path for documents routed to the milestone's default work-item
        (item_type = ItemType.DEFAULT per [D-053]). Path:
        ...\internal\<carrier>\<device>\<milestone>\_unrouted\<original_filename>
        The `_unrouted` sentinel mirrors the default work-item's sentinel tg_name per
        DefaultWorkItemConfig. The actual TG of the document IS recorded on
        DocumentIndexRow.inferred_tg_name (NOT on this path) per the channel→TG resolution rule."""

    @classmethod
    def ingress_folder(
        cls, carrier_slug, ingress_nsd: Literal["NSD1", "NSD2"], folder_path: str
    ) -> NSDPath:
        """Per FR-77 Type-2 routing — INBOUND folder under the TG's ingress_nsd.
        NSD1: \\share\hilda\inbound\nsd1\<carrier>\<folder_path>
        NSD2: \\share\hilda\inbound\nsd2\<carrier>\<folder_path>
        Distinct from outbound customer-portal upload destinations (FR-73 / FR-19 — those are
        not NSD paths at all; they live on the customer portal). The folder_path corresponds
        to TGFolderRoutingRow.ingress_folder."""

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
- **Error-code contract**: all module errors raised as `PipelineError` with `STR-E001..STR-W003` codes registered in `core/src/diagnostics/error_codes.py` per `[D-002]` + `[D-017]`. Compact reports (RPT/MET/QC) emitted per `[D-002]` use only counts, status flags, and bounded enum tokens — never file content or proprietary identifiers. Codes added 2026-06-07: `STR-E005` (cross-milestone association violation per FR-79), `STR-E006` (FolderRoutingTable references unknown item_no per FR-77), `STR-E007` (invalid or expired download token per FR-61), `STR-W002` (unknown tag per FR-82, mirrors TSC-W003), `STR-W003` (default work-item missing for milestone — instantiation race per FR-78).
- **NSD path-construction is deterministic from entity attributes**: given a fixed set of slugs, `NSDPath.internal_classified(...)` returns the same path on every host (lab, dev, test). No path mutation after entity creation.
- **`ingress_folder` vs `target_folder` — naming discipline** (2026-06-07): `ingress_folder` always refers to INBOUND NSD-side paths (HILDA-PC under `TGGroupBase.ingress_nsd`); `target_folder` is reserved for OUTBOUND customer-portal upload destinations (FR-73 / FR-19). The two namespaces are never conflated in storage APIs, models, or path helpers. `NSDPath.ingress_folder(...)` is inbound-only; outbound customer-portal paths are not NSD paths.
- **Symmetric M:M, no primary/secondary** (2026-06-07): `DocumentItemAssociation` is a pure M:M; no `is_primary` flag. The same file may exist at multiple NSD paths simultaneously (one per item's classified path per `[D-040]` / FR-13). Per-(file, item) properties (`local_classified_path`, `plm_id`, `plm_attachment_id`, `owner_email`, `upload_timestamp`) live exclusively on the M:M row; per-file properties (`parser_result`, `llm_review_findings`, `inferred_tg_name`, `routing_resolution`, etc.) live exclusively on `DocumentIndexRow`. No dual-write hazard across the two tables.
- **Single-milestone association scope** per FR-79 (Ph-1/Ph-2): all `DocumentItemAssociation` rows for a given `file_hash` share the same `milestone_id`. Cross-milestone associations deferred to Ph-3+; enforcement raises `STR-E005`.
- **Composite PK on M:M** (2026-06-07): `(file_hash, delivery_item_id)` is the natural PK; no synthetic `association_id`. CommunicationLog audit entries reference the pair directly.
- **PLM fan-out is per-(owner × PLM) pair within a milestone** per FR-79 (revised): `fan_out_plm_associations(file_hash)` returns DISTINCT (owner_email, plm_id) pairs. One PLM upload per pair. Result `plm_attachment_id` + `upload_timestamp` are replicated across all M:M rows sharing that pair (case (a) one-owner-N-items) or differ across rows (case (b) two-owners-N-items-two-PLMs).
- **Document TG-of-origin lives on `DocumentIndexRow.inferred_tg_name`** (2026-06-07): the TG IS knowable from the inbound channel (NSD ingress_nsd / email_group_alias / PLM-id reverse-lookup); recorded per-file (per-ingest), not per-association. Consumed by FR-83 TPM reassignment to shortlist candidate work-items within the TG.
- **`download_url` is never persisted** (2026-06-07): per FR-61 download tokens are short-lived (TTL ≤ 300s default) and computed at page-render time via `make_download_token(file_hash, delivery_item_id)`. Token resolves server-side to the per-item `local_classified_path` on the M:M row. Stored URLs would be stale on path migration, ambiguous in multi-item case, and defeat FR-61's short-lived intent. Worker-internal submission assembly (FR-41 / FR-73) reads NSD via SMB directly through `storage.read_file(NSDPath)` — no URL layer involved.
- **Rule action / trigger references validated against template_schema registries** (2026-06-05): `AutomationRuleOverride.rule_id` resolution and `CommunicationLogRow.action_type` values reference action / trigger registries owned by `template_schema` (`RuleActionRegistry`, `RuleTriggerRegistry`). Storage is opaque to action/trigger semantics but consumers validate at read time. Unknown action/trigger strings are not blocked at write time — registries are customer-extensible per FR-28/FR-29.
- **Tag catalog is customer-scoped, soft-deactivate-only** per FR-82 (revised): `TagCatalogRow.active = False` deactivates a tag without deleting; historical `item_description` references remain valid. Unknown tags raise `STR-W002` (warning), mirroring `TSC-W003` at the template_schema layer.

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
- `core/src/template_schema/` — canonical entity enums (DocType incl. DEFAULT per `[D-053]`, IngestSource, Channel, Direction, Scope, DeliveryState, ItemType incl. DEFAULT, RuleActionType, RuleTriggerType, RuleSubTriggerType) per `[D-028]`; Pydantic base classes for entity hierarchy; `RuleActionRegistry` + `RuleTriggerRegistry` consumed for read-time validation of `AutomationRuleOverride` and `CommunicationLogRow.action_type`; `TagCatalogEntry` model mirrored by `TagCatalogRow` here; `FolderRoutingEntry` / `TGFolderRouting` mirrored by `TGFolderRoutingRow`; `DefaultWorkItemConfig` lives on `MilestoneBase.default_work_item_config`

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
- **Cross-milestone `DocumentItemAssociation`** — Ph-1/Ph-2 enforces same-milestone scope per FR-79 (storage raises `STR-E005`). Ph-3+ revisit: document submitted in milestone N reused in milestone N+1 (e.g. carry-forward waivers, sustaining test reports). Revisit trigger: TPM reports double-uploading documents across milestones.
- **Tag catalog versioning / audit history** — Ph-1/Ph-2 uses a simple `active` flag for soft-deactivation (FR-82 revised). Full audit history of tag renames / merges / catalog snapshots deferred to Ph-3+ if catalog churn warrants. Revisit trigger: customer requests "as-of" reporting on tag membership.
- **Default work-item path namespace evolution** — Ph-1 uses the `_unrouted` sentinel under `internal\<milestone>\` per FR-78. If FR-83 TPM-reassignment volume grows or per-TG default work-items become desirable (FR-78 revisit), the path convention may need a TG-scoped variant; would require migration script + `NSDPath` update.

<!-- BEGIN:STRUCTURE -->
<!-- END:STRUCTURE -->
