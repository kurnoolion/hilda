# Journal — storage-v1

## 2026-06-11 — session 1: `core/src/storage/` Ph-1 implemented end-to-end

**Strand opened, bound, development phase.** Parallel with teammate's
`sharepoint-integration-drift-sweep` (since landed). Module is the largest Batch 1
contract (~780-line MODULE.md).

**Implementation — `core/src/storage/` (11 new files):**
- `models.py` — canonical rows per `[D-046]`: file-centric `DocumentIndexRow` (PK
  `file_hash`, 5-value DocType, nullable slug/rev for FR-86 staged-fill), symmetric
  M:M `DocumentItemAssociation` (`NSDPathType` state, PLM fan-out), `CommunicationLogRow`
  (append-only), `TGFolderRoutingRow`, `TagCatalogRow`, `AutomationRuleOverride`,
  `PLMFanOutTarget`, storage-side enums (Channel/Direction/NSDPathType/RoutingResolution).
- `db.py` — async SQLAlchemy 2.x engine/session, 6 ORM tables, **partial unique index**
  `(milestone_id, doc_id_slug, rev_number) WHERE … IS NOT NULL`, `session_scope()` error
  contract, StaticPool for in-memory sqlite.
- `document_ops.py` — index/association ops: cross-channel idempotency (first-write-wins),
  STR-E005 same-milestone guard, FR-79 fan-out (case a/b replication), FR-83 transactional
  reassignment (caller-resolved target attrs; write-before-delete NSD move + audit),
  FR-61 HMAC download tokens (never persisted).
- `audit_ops.py` — append-only CommunicationLog + filtered query (cap 1000), FR-31
  overrides (expiry filter + audit + cache evict), FR-77 replace-all folder routing
  (caller-supplied `valid_item_nos`, STR-E006), FR-82 soft-deactivate tags.
- `redis_client.py` — broker URL, 24h-capped cache (STR-E008), BATCH idempotency;
  `redis.asyncio` (aioredis absorbed upstream).
- `nsd.py` — `NSDPath` (9 constructors incl. 4 FR-86 path types), `aiofiles` host-mount
  IO per `[D-013]` (no smbprotocol on routine path), `to_relative()`/`from_relative()`
  as persisted form, `to_unc()` diagnostic-only, SHA-256, FR-55 drop listing.
- `config.py` — `GlobalStorageConfig` (3-tier precedence), `config/storage.json`.
- `qc_templates.py` (`STR:schema_roundtrip`), `storage_cli.py` (--diagnostic/--mock/
  --mock-postgres/--validate/--alembic-roundtrip), `migrations/` (Alembic baseline).
- `diagnostics/error_codes.py` +15 STR codes; `requirements.txt` storage deps + aiofiles
  + **starlette<1 pin** (closes teammate's test_mock_server httpx2 flag).

**7 architect-review rulings applied** (each a proper architecture↔development phase
toggle when the contract changed; 8-entry MODULE.md rollback log):
1. **Caller-resolves** — no DeliveryItem mirror in storage; `get_default_work_item_for_milestone`
   removed, `reassign_document_to_workitem`/`set_folder_routing_for_tg` take explicit
   target attrs / `valid_item_nos`. → DRAFT-1.
2. **slug/rev nullability** — match FR-86 staged-fill; partial unique index. → DRAFT-2.
3. **aioredis → redis.asyncio** naming (no-flag, rollback-logged).
4. **extract_first_page** txt/xlsx only; PDF/DOCX → STR-E004 → open `[D-011]` decision.
5. **error-code split** — E001–E004/W001 texts now; E005+ ship with their raise paths.
6. **clear_override** gained required `pm_id` kwarg (signature↔audit-docstring alignment).
7. **NSD-IO host-mount alignment** — `HILDA_NSD_MOUNT_ROOT` + aiofiles; persisted
   `local_nsd_path` is share-relative POSIX (mount-root-independent); smbprotocol
   diagnostic/fallback only. + Invariant + set_storage_config TEST-ONLY docstring.

**2 soft-flag additions** (architect pre-cleared as "accept as idiomatic"):
NSDPath `to_relative`/`from_relative`; `GlobalStorageConfig` + `get`/`set_storage_config`.

**Problems resolved:** fresh `hilda-env/` venv (uv, py3.12); starlette 1.x breaking
fresh installs; `get_session` async-gen can't intercept errors → `session_scope()`;
in-memory sqlite per-connection → StaticPool; config caching vs test mount → set/reset.

**Process correction:** first contract change was made without the architecture-phase
toggle (hard-flag rule) — user caught it; retro architecture pass run; every later
ruling used the proper toggle. Ripple checks during those passes surfaced 4 cross-module
findings (see open items).

**Verification:** full suite **302 passed**; mock 9/9; Alembic roundtrip 3/3.

**Open items (carry to next session / architect):**
- **2 draft decisions** (DRAFT-1 caller-resolves, DRAFT-2 nullability) → promote at land-strand.
- **4 FR-87/email_service ripple findings** from the retro pass: (1) email_service cites
  old 4-arg `reassign_document_to_workitem`; (2) email_service expects `storage.update_doc_type`
  + `storage.set_revision_resolution` (FR-87 steps B/C) that don't exist in storage's
  contract — genuine forward-drift; (3) dual reassignment entry path (tracker action vs
  direct storage call) needs one front door; (4) workflow_engine task-body docs need the
  SP-lookup-before-storage-call ordering. → architect ruling, then likely storage Ph-1.5.
- **`[D-011]` extraction-library decision** (pypdf/pdfplumber/pymupdf) — first strand needing
  `extract_first_page(pdf)` unblocks; already a STATUS.md Next item.
- `--mock-postgres` / `--validate` need `HILDA_TEST_POSTGRES_URL` / real customer schemas.
- session_scope additive surface — acknowledged this session; no DECISIONS entry.
