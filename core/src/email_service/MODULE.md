# Module: email_service

**Purpose**: All email-mediated communication for HILDA — inbound owner replies (FR-12), inbound SP-alert notifications (`[D-047]`), outbound owner outreach (FR-9), outbound reminders + escalations (FR-10), and the FR-52 attachment-routing driver that fans out classified documents to `storage` + the LLM gateway. Hosts the `sp_alert_parser` sub-module per `[D-047]` (parses SharePoint alert emails so HILDA learns of PM/TPM edits on SP lists). Anchors `[D-016]` (IMAP/SMTP), `[D-034]` (FR-12 path c fused LLM call), `[D-047]` (SP alert email channel); serves FR-9, FR-10, FR-12, FR-23, FR-24, FR-52, FR-54 (Ph-2), FR-55 trigger (NSD `inbound/` files are owner deliverables; emails are a sibling ingest channel), NFR-1, NFR-2.

**Workload assignment** per `[D-021]` impl note 2026-05-24:
- `hilda-worker` — IMAP receiver loop, inbound parsing (FR-12 / `[D-047]`), FR-52 attachment-routing driver, outbound SMTP send (FR-9 / FR-10).
- `hilda-beat` — periodic poll-trigger schedule (FR-23 short-poll fallback + deadline-tiered third tier; not used when IMAP IDLE is healthy).
- `hilda-api` — no runtime entrypoints in Ph-1 (no inbound push webhooks; IMAP is pull-based).

**Inbound reception** per FR-23 + `[D-016]` + `[D-047]` impl note:
1. **IMAP IDLE primary** — long-lived connection to corp Exchange mailbox; ~1–2 s notification latency when supported by corp Exchange admin.
2. **Short-interval polling fallback** — `poll_interval_s = 60` (configurable) when IDLE unavailable.
3. **Deadline-tiered third tier** — fallback poll cadence keyed on per-item `expected_completion_date − today` per FR-23 deadline-tiered schedule.

**Field reality (2026-05-28 review)**: IMAP IDLE is believed unsupported by current corp Exchange configuration; short-poll (`poll_interval_s = 60`) is expected to be the production primary in Ph-1. Final confirmation pending Exchange-admin response per STATUS.md flag. Code structure remains tier-aware so flipping IDLE on/off is env-config only — no code change.

**Source-of-truth for email content**: Inbound email body + attachments + headers are stored once at receipt time in NSD `<tg>/<item>/email/<message_id>/` (audit) and indexed in `CommunicationLog` (Postgres). Mailbox is treated as a transient queue — once processed and acknowledged in `CommunicationLog`, **two IMAP actions fire**: (1) the `\Seen` flag is set on the message so subsequent `SEARCH UNSEEN` polls skip it; (2) the message is moved out of INBOX into `INBOX/processed` (per `ImapConfig.folder_processed`) via IMAP MOVE (or COPY+DELETE for legacy servers without MOVE). The processed-folder copy is preserved for audit / replay (PM/ops can browse it directly via Outlook). Re-fetching is by `CommunicationLog.message_id` lookup against NSD audit storage, not by mailbox re-poll.

---

## Sub-modules

```
core/src/email_service/
  __init__.py
  protocol.py                       ← interfaces: EmailReceiver, EmailSender, AttachmentRouter
  inbound/
    receiver.py                     ← IMAP receiver (IDLE primary / poll fallback)
    classifier.py                   ← email-kind discriminator: owner_reply | sp_alert | other
    subject_parser.py               ← FR-24 — extracts BATCH-id, ITEM-n, sender
    body_parser_structured.py       ← FR-12 path (a) — regex on structured reply block
    body_parser_freetext.py         ← FR-12 path (c) — fused LLM call (calls llm.invoke)
    attachment_router.py            ← FR-52 driver — Tier 1 fuzzy + Tier 2 LLM; calls storage + llm
  sp_alert_parser/                  ← [D-047] — parses SP alert emails on entity changes
    parser.py                       ← regex-driven alert subject + key:value body parser
    routing_key.py                  ← (ProjectID, MinorMilestone, ItemNumber) → entity ref
  outbound/
    sender.py                       ← SMTP send wrapper (sync wrapped via asyncio.to_thread)
    composer_outreach.py            ← FR-9 — initial outreach per modality
    composer_reminder.py            ← FR-10 — scheduled reminder
    composer_escalation.py          ← FR-10 cross-channel escalation enqueue → messenger
  templates/                        ← Jinja2 outbound email body templates
    outreach_individual.j2
    outreach_tg_alias.j2
    reminder.j2
  email_service_cli.py              ← --diagnostic / --once / --mock / --send
  tests/
  MODULE.md                         ← this file
```

**Test-scenario minimum subset** (per user 2026-05-28 use case — incoming email parsing for 80+ test reports with structured response): doc_type classification + attachment-to-item routing **are exercised** (both use LLM Tier-2 when filename/fuzzy fail). What's **out of scope** for this test and why:
- **No LLM-based content review (FR-53)** — config-gated off via `Fr52Config.review_required_enabled = False`. The `REVIEW_DOCUMENT` TaskKind never fires. The `llm_review_findings` field stays null on every document index row.
- **No test-case extraction (FR-16)** — the per-customer rule-based parser per `[D-011]` isn't implemented yet (requires `test_report_profiler`, Layer 3). No test-case counts, no pass/fail tallies, no `parser_result` written.
- **No multi-revision detection (D-039 Step 2+)** — every incoming report treated as first-receipt (`rev1/`). `CLASSIFY_DOC` TaskKind not invoked.
- **No PLM upload (FR-13 Step 5a)** — config-gated off via `Fr52Config.plm_upload_enabled = False`. `issue_tracker.upload_attachment` not called; `plm_attachment_id` stays null in document index.

**Active sub-modules for the test**: `inbound/receiver.py` (one-shot poll mode), `inbound/classifier.py`, `inbound/subject_parser.py`, `inbound/body_parser_structured.py`, `inbound/attachment_router.py`. **Skipped for test**: `body_parser_freetext.py` (FR-12 path c — fused LLM call), entire `sp_alert_parser/`, entire `outbound/`.

---

## Public surface

### `protocol.py`

```python
@dataclass(frozen=True)
class InboundMessage:
    """One inbound email after IMAP fetch, before parsing."""
    message_id:    str                   # RFC 5322 Message-ID header
    received_at:   datetime
    sender:        str                   # email address (raw header)
    subject:       str
    body_text:     str
    body_html:     str | None
    attachments:   list["InboundAttachment"]

@dataclass(frozen=True)
class InboundAttachment:
    filename:      str
    content:       bytes
    content_type:  str                   # MIME type
    file_hash:     str                   # sha256 of content; used by D-039 Step 0 dedup

class EmailKind(str, Enum):
    """Discriminator output of inbound/classifier.py."""
    OWNER_REPLY     = "owner_reply"      # subject contains BATCH-<id> per FR-24
    SP_ALERT        = "sp_alert"         # subject matches D-047 alert pattern
    OTHER           = "other"            # neither — surfaced as 'unrecognized inbound' for PM review

@dataclass(frozen=True)
class StructuredReplyBlock:
    """FR-12 path (a) parse output."""
    batch_id:        str
    sender_email:    str
    sender_match:    Literal["owner", "tg_alias", "cc", "mismatch"]
    per_item_updates: list["PerItemReplyUpdate"]

@dataclass(frozen=True)
class PerItemReplyUpdate:
    item_no:           int
    delivery_state:    str | None        # OPEN / OWNER_CLOSED / DELAYED / BLOCKED / etc.
    owner_status_note: str | None
    confidence:        float             # 1.0 for structured-block parse; LLM-derived for path (c)

@dataclass(frozen=True)
class RoutedAttachment:
    """FR-52 driver output per inbound attachment."""
    item_id:                 str | None  # None when LLM ambiguous → staged
    doc_type:                str         # test_report | tech_report | waiver | other
    doc_id_slug:             str         # derived from filename per [D-039] Step 1
    nsd_classified_path:     Path        # <tg>/<item>/<doc_type>/<doc_id_slug>/rev1/
    document_index_row_id:   str         # storage.add_document_index_row return value
    doc_type_source:         Literal["filename_rule", "llm_classify_doc_type"]
                                         # how doc_type was resolved (rule-based first,
                                         # LLM CLASSIFY_DOC_TYPE TaskKind on opaque filenames)
    item_routing_source:     Literal["tier1_fuzzy", "llm_route_attachment", "staged_pm"]
                                         # how item_id was resolved (rapidfuzz first,
                                         # LLM ROUTE_ATTACHMENT TaskKind on low-confidence,
                                         # staged_pm when LLM also ambiguous)
    is_duplicate:            bool        # D-039 Step 0 — true if file_hash already in document_index

class EmailReceiver(Protocol):
    async def fetch_new(self) -> AsyncIterator[InboundMessage]: ...
    async def mark_processed(self, message_id: str) -> None: ...

class EmailSender(Protocol):
    async def send(
        self, to: list[str], cc: list[str], subject: str, body: str,
        in_reply_to: str | None = None,
    ) -> str: ...    # returns Message-ID of sent email

class AttachmentRouter(Protocol):
    async def route(
        self,
        attachment:        InboundAttachment,
        batch_id:          str,
        candidate_items:   list[dict],     # [{item_id, item_name, item_description, item_type}]
    ) -> RoutedAttachment: ...
```

### `inbound/receiver.py`

```python
class ImapReceiver(EmailReceiver):
    """IMAP IDLE primary, short-poll fallback, deadline-tiered third tier per FR-23 + [D-016].
    Credentials via credential_service.get_credential(pm_id='ops', SystemType.EMAIL).
    sync IMAP library (imap-tools or aioimaplib) wrapped in asyncio.to_thread per [D-008] pattern.
    For test-scenario: --once mode fetches all unread, processes, exits; no IDLE loop."""

    def __init__(self, config: ImapConfig, credential_service: CredentialService) -> None: ...
    async def fetch_new(self) -> AsyncIterator[InboundMessage]: ...
    async def fetch_once(self) -> list[InboundMessage]: ...     # test mode
    async def mark_processed(self, message_id: str) -> None: ...
```

### `inbound/classifier.py`

```python
def classify(msg: InboundMessage) -> EmailKind:
    """Discriminator. Order of checks (first match wins):
    1. Subject matches D-047 SP-alert pattern `Alert_<List>_<Suffix> - <ItemTitle>` → SP_ALERT
    2. Subject contains `BATCH-<id>` per FR-24 → OWNER_REPLY
    3. Otherwise → OTHER (logged in CommunicationLog with kind='other'; PM dashboard surfaces)"""
```

### `inbound/subject_parser.py`

```python
@dataclass(frozen=True)
class ParsedSubject:
    batch_id:    str
    item_no:     int | None      # set when path (b) mailto tap-link subject; Ph-2
    status:      str | None      # set when path (b)
    raw_subject: str

def parse_subject(subject: str) -> ParsedSubject:
    """FR-24. Extracts BATCH-<id> from subject. Returns ITEM-<n> + STATUS when present
    (FR-12 path (b) Ph-2 mailto tap-link subjects: `[HILDA] BATCH-<id> ITEM-<n> <STATUS>`).
    Raises EML-E001 if BATCH-id not findable."""
```

### `inbound/body_parser_structured.py`

```python
def parse_structured_block(
    msg: InboundMessage, batch_id: str, expected_items: list[dict]
) -> StructuredReplyBlock | None:
    """FR-12 path (a). Regex-parses the structured reply block embedded in outbound emails.
    Returns None when the structured block is not detected (caller falls back to path c).
    Captures sender_email from msg.sender; resolves sender_match against owner_email +
    email_group_alias + cc list per FR-12 sender-mismatch logic."""
```

### `inbound/body_parser_freetext.py` *(Ph-1; skipped for test scenario)*

```python
async def parse_freetext_with_attachments(
    msg: InboundMessage,
    batch_id: str,
    expected_items: list[dict],
    llm: LLMProvider,
) -> tuple[StructuredReplyBlock, list[RoutedAttachment]]:
    """FR-12 path (c) per [D-034]. When the email contains both free-text body AND attachments,
    a single fused LLM call processes body + first-page attachment excerpts + expected_items
    in one pass — message classification and attachment routing share context.
    When no attachments present, falls back to the message-only CLASSIFY_MESSAGE TaskKind."""
```

### `inbound/attachment_router.py` — **the FR-52 driver (heart of the test scenario)**

```python
class Fr52AttachmentRouter(AttachmentRouter):
    """Per FR-52 + [D-033] + [D-039]. Implements the full inbound-attachment pipeline
    for the standalone (non-fused) path. The fused path lives in body_parser_freetext.py.

    Per-attachment pipeline:
      Step 0: file_hash dedup
        - storage.get_document_index_row(file_hash=...)
        - if found → skip; emit FIX record; return RoutedAttachment(is_duplicate=True)

      Step 1: filename-rule doc_type classification
        - deterministic regex on filename → doc_type ∈ {test_report, tech_report, waiver, ...}
        - if matched → doc_type resolved; classification_source = 'filename_rule'
        - else → invoke llm.CLASSIFY_DOC_TYPE on first_page_excerpt; classification_source = 'tier2_llm'

      Step 2: item routing
        - Tier 1: rapidfuzz score(filename, item_name | item_description | item_type) per item
                  in candidate_items; if max > FUZZY_THRESHOLD → item resolved
        - Tier 2: invoke llm.ROUTE_ATTACHMENT with first_page_excerpt + candidate_items
                  - high-confidence → item resolved; classification_source = 'tier2_llm'
                  - low-confidence → item = None; written to `<doc_type>/staged/`;
                    PM dashboard flag 'Item ambiguous'; classification_source = 'staged_pm'

      Step 3: new-vs-revision (D-039 Step 1+)
        - SKIPPED in test scenario (no multi-revision)
        - Production Ph-1: derive doc_id_slug = slugify(original_filename)
        - if storage.find_doc_id_slugs_for_item(item_id, doc_type) contains derived slug
          → revision (call llm.CLASSIFY_DOC for confirmation; not in test scenario)
        - else if no priors for (item, doc_type) → NEW_DOCUMENT, no LLM call

      Step 4: write
        - storage NSD write to <tg>/<item>/<doc_type>/<doc_id_slug>/rev1/
        - storage.add_document_index_row(...)
        - storage.log_communication(kind='attachment_classified', ...)

      Step 5: post-write (deferred / config-gated)
        - PLM upload via issue_tracker.upload_attachment — gated by PLM_UPLOAD_ENABLED config flag
          (TEST scenario: PLM_UPLOAD_ENABLED=false — issue_tracker not implemented yet)
        - FR-53 LLM review trigger — gated by item.review_required + REVIEW_REQUIRED_ENABLED flag
          (TEST scenario: REVIEW_REQUIRED_ENABLED=false — review never fires)
    """

    def __init__(
        self,
        storage:           StorageBackend,
        llm:               LLMProvider,
        issue_tracker:     IssueTracker | None = None,    # None in test scenario
        fuzzy_threshold:   float = 0.85,
        llm_confidence_threshold: float = 0.75,
        plm_upload_enabled: bool = True,
        review_required_enabled: bool = True,
    ) -> None: ...

    async def route(
        self,
        attachment: InboundAttachment,
        batch_id:   str,
        candidate_items: list[dict],
    ) -> RoutedAttachment: ...
```

### `sp_alert_parser/` *(Ph-1 production; skipped for test scenario)*

Per `[D-047]`. SP sends one alert email per entity change (configured "Anything changes" on each list). Subject format `Alert_<List>_<Suffix> - <ItemTitle>`; body carries key:value pairs identifying the changed entity. Sub-module extracts the routing key `(ProjectID, MinorMilestone, ItemNumber)` from the body, resolves to a SP list + row, and emits an `SpEntityChangedEvent` to `workflow_engine` for downstream processing.

### `outbound/` *(Ph-1 production; skipped for test scenario)*

- `composer_outreach.py` — FR-9 initial outreach: builds the structured reply block with one BATCH per owner; honours TG `email_group_alias` (one outreach email to alias with multiple per-owner BATCH blocks) vs individual owner (one outreach per recipient).
- `composer_reminder.py` — FR-10 scheduled reminder; reuses BATCH-id from original outreach for thread continuity.
- `composer_escalation.py` — FR-10 cross-channel escalation: enqueues a `messenger` task when reminders yield no response after `reminder_count ≥ M`.
- `sender.py` — SMTP send wrapper; appends `CommunicationLog` entry per FR-42; never logs body content (NFR-2).

### Configuration

Per `[D-025]` + `[D-038]` + nora 3-tier (CLI arg → env var → `config/email_service.json`):

```python
class ImapConfig(BaseModel):
    host:                str                # e.g. "exchange.corp"
    port:                int   = 993
    use_idle:            bool  = True       # falls back to poll automatically when False
    poll_interval_s:     int   = 60         # short-poll fallback cadence
    mailbox:             str   = "INBOX"
    folder_processed:    str   = "INBOX/processed"   # move-after-read target

class SmtpConfig(BaseModel):
    host:                str
    port:                int   = 587
    use_tls:             bool  = True
    from_addr:           str                # e.g. "hilda@corp"

class Fr52Config(BaseModel):
    fuzzy_threshold:           float = 0.85
    llm_confidence_threshold:  float = 0.75
    plm_upload_enabled:        bool  = True
    review_required_enabled:   bool  = True
    nsd_root:                  Path  = Path("/mnt/hilda/internal")
```

Credentials: `credential_service.get_credential(pm_id='ops', SystemType.EMAIL)` returns IMAP + SMTP credentials.

---

## Invariants

- **No standalone Postgres table for inbound email bodies.** `CommunicationLog` rows carry `(message_id, sender, kind, batch_id, received_at, processing_outcome)` only — bounded, structured, NFR-2-compliant. Full body text (`body.txt`, `body.html`) + raw attachment bytes live at NSD `<tg>/<item>/email/<message_id>/` per FR-13's audit-path convention. Reconstructing an `InboundMessage` post-receipt requires the `CommunicationLog` row + the NSD audit directory; no third store.
- **All inbound email content stays inside HILDA-PC boundary.** Email bodies and attachments are written to NSD audit path + `CommunicationLog` immediately; raw IMAP messages are not retained in the mailbox after processing (move-to-folder per `ImapConfig.folder_processed`). Mailbox is a transient queue.
- **No proprietary content in compact reports.** EML-RPT / -MET / -FIX / -QC records emit counts, EmailKind discriminator value, BATCH-id, status enum tokens, sender-match enum, and bounded counts only — never subject text, body content, sender address, or attachment filenames. Anchors NFR-2. (Filenames may carry customer/owner identifiers per the placeholder convention — they go to `CommunicationLog` for audit but not to compact reports.)
- **Inbound classification is exhaustive.** Every fetched message receives an `EmailKind` value; messages classified `OTHER` are logged in `CommunicationLog` and surfaced on PM dashboard for manual triage — never silently discarded.
- **Idempotency on `(message_id, attachment_hash)`.** Reprocessing the same email + attachment combination is a no-op: D-039 Step 0 hash dedup short-circuits and emits a FIX record. Required because IMAP IDLE / poll restart can re-deliver the same UID.
- **Sender attribution required on every inbound path.** `sender_email` captured from RFC 5322 headers and recorded in `CommunicationLog` with sender_match enum (`owner | tg_alias | cc | mismatch`) per FR-12. CC sender-mismatch is *not* a hard rejection — the update is applied and a flag is raised per FR-12.
- **FR-52 driver never bypasses storage idempotency.** Even when classification falls through to `staged/`, `storage.add_document_index_row` is called with `classification_source='staged_pm'` so PM dashboard has the document to triage. No silent drops.
- **No credential material in any log, body parse, or compact report.** IMAP / SMTP credentials retrieved per-call from `credential_service`; never stored on receiver / sender instance after use.
- **PLM upload + FR-53 review are config-gated post-write steps.** Per `Fr52Config.plm_upload_enabled` / `review_required_enabled` — test scenarios can disable both without code change.

---

## Error codes (EML prefix — registered in `diagnostics/error_codes.py`)

```
EML-E001  Subject does not contain BATCH-<id> per FR-24 — message_id='{mid}' sender='{sender_redacted}'
EML-E002  IMAP fetch failed: {reason}
EML-E003  IMAP authentication rejected (credential_service may need refresh)
EML-E004  SMTP send failed for outbound message: {reason}
EML-E005  FR-12 path (a) structured block expected but not found; falling back to path (c)
EML-E006  Attachment classification failed for '{filename_slug}' on message_id='{mid}': {reason}
EML-E007  SP alert subject matched pattern but routing key not resolvable (D-047)
EML-W001  Sender mismatch — sender '{sender_redacted}' is not owner / tg_alias / cc for BATCH-<id> (FR-12; recoverable, surfaced as PM flag)
EML-W002  Duplicate attachment skipped — file_hash already in document_index for item_id='{item}' (D-039 Step 0)
EML-W003  Attachment routing ambiguous — written to '{doc_type}/staged/'; PM triage required (recoverable)
EML-W004  Email classified 'OTHER' — neither SP alert nor BATCH-id; surfaced for PM triage (recoverable)
```

---

## Key choices

- **`[D-016]`** — IMAP/SMTP for the mailbox channel; rejected EWS / Graph API as they fail NFR-1 (no SaaS LLM is unrelated, but the Graph endpoint adds external surface area). IMAP IDLE remains pending Exchange-admin confirmation per STATUS.md Flag.
- **`[D-034]`** — FR-12 path (c) uses a fused LLM call (one call processes body + attachment first-page excerpts together) when an email arrives with both free-text body AND attachments. Anchored here because both inbound surfaces are owned by this module. Fused call is implemented in `body_parser_freetext.py`; standalone (no-attachment) path c calls `CLASSIFY_MESSAGE` task only; standalone (no-message) attachment-only path goes through `attachment_router.py` directly.
- **`[D-047]`** — `sp_alert_parser` co-located in this module rather than in a sibling module. SP alerts arrive as emails; the parser is an inbound-email kind, naturally shares IMAP machinery and `CommunicationLog` write path. Co-location avoids two IMAP receivers.
- **Sync IMAP wrapped in `asyncio.to_thread`** — Python's mature IMAP libraries (imap-tools, imaplib) are sync; wrapping is simpler than maintaining an asyncio-native IMAP client. Same pattern as `JiraAdapter` per `[D-008]` and `SpClient` for NTLM.
- **NSD audit storage per email separate from classified storage** — `<tg>/<item>/email/<message_id>/` carries the raw email + every attachment in receipt form; `<tg>/<item>/<doc_type>/<doc_id_slug>/rev1/` holds the classified copy per FR-13. Two writes per attachment, two purposes (audit immutable vs classified mutable).
- **Test-mode `--once`** — explicit batch-process-and-exit mode bypasses IDLE / poll loops for offline-fixture testing per the 2026-05-28 use case (80+ test reports). Same code path as production; just one fetch + process pass.
- **PLM upload + FR-53 review are config-gated** — both are post-classification side effects in the FR-52 pipeline. Gating with boolean config flags lets the test scenario run end-to-end without requiring `issue_tracker` implementation or LLM review wiring.

---

## Non-goals

- **Not the LLM gateway.** Tier-2 LLM calls (`CLASSIFY_DOC_TYPE`, `ROUTE_ATTACHMENT`, `CLASSIFY_MESSAGE`, fused-call composition) flow through `llm.LLMProvider`. This module assembles the request and consumes the response; the prompt/template/backend selection lives in `core/src/llm/`.
- **Not the issue tracker / PLM uploader.** PLM upload (FR-13) is delegated to `issue_tracker.upload_attachment`; this module passes the classified-doc path and document_index_row reference.
- **Not the rule engine.** State transitions following `AttachmentReceived` (FR-28 rules 15/16/17) fire through `workflow_engine` / `rule_engine` after this module writes to storage. This module does not evaluate rules.
- **Not a deadline scheduler.** Reminder cadence (FR-10) is set by `workflow_engine` / `hilda-beat` per the `polling_schedule` AutomationRule; this module sends what `workflow_engine` enqueues.
- **Not the FR-16 / FR-46 test report parser.** Per the parser-strategy Flag (STATUS.md 2026-05-28), FR-16 is rule-based per `[D-011]` and is implemented by the per-customer parser. This module classifies + routes but does not extract test cases.
- **Not a NSD `inbound/` poller.** FR-55 owner-NSD-drop monitoring lives in `storage` (or a dedicated sub-module of it); this module handles only the email-channel ingest.
- **Not a credentials store.** IMAP/SMTP credentials flow through `credential_service` per `[D-019]`.
- **Not a corp messenger sender.** Cross-channel escalation (FR-10) enqueues a task to `messenger`; the messenger transport is owned by that module.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `QCTemplate` (EML codes registered in `error_codes.py`).
- `credential_service` — `get_credential(pm_id, SystemType.EMAIL)` for IMAP/SMTP auth.
- `storage` — `add_document_index_row`, `get_document_index_row` (Step 0 hash dedup), `find_doc_id_slugs_for_item` (Step 1 slug match), `log_communication`. NSD client used for both audit (`email/<message_id>/`) and classified (`<doc_type>/<doc_id_slug>/rev1/`) writes.
- `llm` — `LLMProvider.invoke()` with `CLASSIFY_DOC_TYPE`, `ROUTE_ATTACHMENT`, `CLASSIFY_MESSAGE` TaskKinds.
- `template_schema` — `DocType`, `DeliveryItemBase` (for `candidate_items` shape), slug helpers.
- `issue_tracker` — `IssueTracker.upload_attachment` (gated by `plm_upload_enabled`; stubbed in test scenarios).
- `rapidfuzz` (3rd party) — Tier 1 fuzzy matching for FR-52 item routing.
- `imap-tools` or `aioimaplib` (3rd party) — IMAP client; sync calls wrapped in `asyncio.to_thread`.

---

## Depended on by

- `workflow_engine` — receives `AttachmentReceived` / `OwnerStatusConfirmed` / `SpEntityChangedEvent` events from this module's writes; dispatches downstream rule actions.
- `tracker` — reads `CommunicationLog` entries for milestone-view rendering + soft-poll status (FR-56 `last_poll_timestamp`).
- `messenger` — receives cross-channel escalation enqueues for FR-10 owner messenger outreach when reminders go unanswered.

---

## Deferred (Ph-2 / Ph-3+)

- **FR-12 path (b)** — `mailto:` tap-link tiny-email subject parsing (Ph-2 per requirements.md).
- **FR-23 IMAP IDLE confirmation** — pending Exchange admin response; if IDLE rejected, short-poll fallback is the production primary (STATUS.md Flag).
- **FR-54** — corp messenger inbound replies (Ph-2; owned by `messenger`, not this module).
- **NSD per-owner filesystem identity attribution in `CommunicationLog`** — Ph-3+ per DEF-16 (`[D-013]` impl note).

---

## Test interface

```
python -m core.src.email_service.email_service_cli --diagnostic
```
Connects to IMAP + SMTP, validates credentials, reports mailbox reachability. Emits no message content:
```
RPT|EML|run-00001|2026-05-28T10:00:00Z|imap_reachable=true|smtp_reachable=true|idle_supported=true|mailbox_unread=42|templates_loaded=3
```

```
python -m core.src.email_service.email_service_cli --once
```
**Test-scenario mode (2026-05-28 use case)**: fetches all unread mailbox messages once, runs them through `classifier → subject_parser → body_parser_structured → attachment_router` pipeline, processes attachments via the FR-52 driver (with `plm_upload_enabled=false`, `review_required_enabled=false`), writes results to `storage`, exits. Emits a summary:
```
RPT|EML|run-00001|2026-05-28T10:00:00Z|messages_fetched=84|owner_reply=80|sp_alert=2|other=2|attachments_total=147|dedup_skipped=3|routed_filename_rule=98|routed_tier1_fuzzy=31|routed_tier2_llm=12|staged_pm=3
```

```
python -m core.src.email_service.email_service_cli --mock
```
Spins up a stub `EmailReceiver` returning fixture emails; pairs with `MockLLM` + `storage` test instance. End-to-end pipeline runs in-memory without IMAP / SMTP / LLM gateway. Useful for CI.

```
python -m core.src.email_service.email_service_cli --send --to <addr> --subject "<text>" --body-file <path>
```
Outbound send harness — sends one message via SMTP using the configured credentials; emits `EML-MET` with delivery confirmation. Body content read from file (not echoed to RPT). Used for manual outreach + outbound-path testing.

**QC template** (`EML:inbound_processing` — registered in `diagnostics/qc.py`):
```
Fields: messages_fetched (int), owner_reply (int), sp_alert (int), other (int),
        attachments_total (int), dedup_skipped (int),
        routed_filename_rule (int), routed_tier1_fuzzy (int), routed_tier2_llm (int),
        staged_pm (int),
        result (enum: OK / WARN / FAIL — FAIL when other_rate > 10% or staged_pm_rate > 20%)
```

---

<!-- BEGIN:STRUCTURE -->
<!-- END:STRUCTURE -->
