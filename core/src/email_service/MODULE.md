# Module: email_service

> **Status:** Draft + 2026-06-09 cascade Group 4 of 4 applied (`[D-053]` impl note 2026-06-08 corrected model: 5-value DocType + alignment invariant + FR-85 2-step classification ladder + FR-86 4-path storage matrix + FR-87 SP UI A→B→C resolution). Initial draft 2026-05-28. Sections curated; pending section-by-section user review before contract is finalized. Code implementation begins after `/switch-phase development`.
>
> **Rollback log:**
> - **2026-06-09 (Phase B Module cascade — Group 4 of 4 against the corrected `[D-053]` model — after `template_schema/MODULE.md` 2026-06-08 + `storage/MODULE.md` + `llm/MODULE.md` cascades 2026-06-09)** — applied the requirements-phase redesign (`requirements.md` FR-7 + FR-77 + FR-85 + FR-86 + FR-87 + `DECISIONS.md` `[D-053]` impl note 2026-06-08 + `[D-060]` impl note 2026-06-08): **FR-52 pipeline 2-tier → 5-step** (substring → fuzzy → FR-77 Type-2 folder routing → LLM ROUTE_ATTACHMENT → staged-to-default-work-item per FR-78); **doc_type classification → separate FR-85 2-step ladder** (filename regex Step 1 covers all 4 actionable doc_types via `customizations/<slug>/doc_type_filename_rules.yaml` + LLM CLASSIFY_DOC_TYPE Step 2 with restricted candidate set `{test/tech/waiver}`; UNRESOLVED on low confidence); **classification + routing run independently per FR-85** (both feed FR-86 storage matrix); **FR-86 4-path NSD storage dispatch** (classified / unrouted / staged-not-revision-determined / staged-not-classified) via `NSDPath` helpers from `storage/MODULE.md`; **DocType enum 4→5 values** (rename `default`→`compliance_certification_release_notes`; add `unresolved`); **RoutedAttachment → multi-match per FR-79** via `list[RouteAttachmentMatch]` from `llm/MODULE.md` (one DocumentItemAssociation row per match per `[D-055]` symmetric M:M); **inferred_tg_name resolver** added (channel→TG mapping per `[D-060]` impl note 2026-06-08); **classification_source / item_routing_source** → align with `RoutingResolution` (storage) + new `ClassificationResolution` enum (FR-85 outcomes); **EML-W005 retired** (routing/type mismatch replaced by FR-86 storage-matrix landing rule); **new EML-W006 / EML-W007** (misalignment-staged + over-routing per FR-79); **Fr52Config additions** (filename_rules_path + classifier threshold + over-routing threshold); **sp_alert_parser** gains FR-87 A→B→C action handlers (`tpm_reassign_to_workitem`, `tpm_resolve_doc_type`, `tpm_resolve_revision`). Cascade chain complete (Group 1 template_schema 2026-06-08 + Group 2 storage 2026-06-09 + Group 3 llm 2026-06-09 + Group 4 email_service 2026-06-09).

**Purpose**: All email-mediated communication for HILDA — inbound owner replies (FR-12), inbound SP-alert notifications (`[D-047]` + FR-84 + FR-87), outbound owner outreach (FR-9), outbound reminders + escalations (FR-10), the **FR-52 5-step routing pipeline driver** (per `[D-053]` impl note 2026-06-08), the **FR-85 doc_type classification driver** (2-step ladder: filename regex + restricted-candidate LLM), and the **FR-86 storage matrix dispatcher** that places each inbound document into one of 4 NSD paths (classified / unrouted / staged-not-revision-determined / staged-not-classified). Hosts the `sp_alert_parser` sub-module per `[D-047]` (parses SharePoint alert emails so HILDA learns of PM/TPM edits on SP lists, including FR-87 strict A→B→C TPM resolution actions). Anchors `[D-016]` (IMAP/SMTP), `[D-034]` (FR-12 path c fused LLM call), `[D-047]` (SP alert email channel); serves FR-9, FR-10, FR-12, FR-23, FR-24, FR-52, FR-54 (Ph-2), FR-55 trigger (NSD `inbound/` files are owner deliverables; emails are a sibling ingest channel), FR-77 (Type-2 ingress_folder routing as FR-52 step 3), FR-83 (TPM reassignment via FR-84 SP-alert channel), FR-84 (SP→HILDA inbound email channel), FR-85 (doc_type classification 2-step ladder), FR-86 (storage matrix dispatch), FR-87 (SP UI strict A→B→C TPM-resolution action handlers), NFR-1, NFR-2.

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
    attachment_router.py            ← FR-52 5-step routing driver + FR-85 doc_type classification + FR-86 storage matrix dispatch; calls storage + llm
    tg_resolver.py                  ← `[D-060]` impl note 2026-06-08 — channel→TG resolution (NSD ingress_nsd / email_group_alias / owner_email / PLM-id reverse-lookup) producing inferred_tg_name; surfaced on DocumentIndexRow + in unrouted NSD path segment per FR-86
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

**Test-scenario minimum subset** (per user 2026-05-28 use case — incoming email parsing for 80+ test reports with structured response): doc_type classification per FR-85 (filename regex + LLM CLASSIFY_DOC_TYPE) + attachment-to-item routing per FR-52 5-step pipeline + FR-86 storage matrix dispatch **are exercised**. What's **out of scope** for this test and why:
- **No LLM-based content review (FR-53)** — config-gated off via `Fr52Config.review_required_enabled = False`. The `REVIEW_DOCUMENT` TaskKind never fires. The `llm_review_findings` field stays null on every document index row.
- **No test-case extraction (FR-16)** — the per-customer rule-based parser per `[D-011]` isn't implemented yet (requires `test_report_profiler`, Layer 3). No test-case counts, no pass/fail tallies, no `parser_result` written.
- **No multi-revision detection (`[D-039]` Step 2+)** — every incoming report treated as first-receipt (`rev1/`). `CLASSIFY_DOC` TaskKind not invoked. The FR-86 storage matrix skip rule for `[D-039]` on Default-routed docs still applies.
- **No PLM upload (FR-13 Step 5a)** — config-gated off via `Fr52Config.plm_upload_enabled = False`. `issue_tracker.upload_attachment` not called; `plm_attachment_id` stays null in document_item_association rows.
- **FR-77 Type-2 folder routing** — exercised IF the test fixture customer has `TGGroupBase.folder_routing_enabled = True` AND a populated `folder_routing.yaml`; otherwise step B3 falls through silently to B4 (LLM ROUTE_ATTACHMENT).
- **FR-87 SP UI A→B→C TPM resolution** — out of scope for this test (no TPM-resolution save events; `sp_alert_parser` action handlers skipped).

**Active sub-modules for the test**: `inbound/receiver.py` (one-shot poll mode), `inbound/classifier.py`, `inbound/subject_parser.py`, `inbound/body_parser_structured.py`, `inbound/attachment_router.py`, `inbound/tg_resolver.py` (for inferred_tg_name on default-routed docs). **Skipped for test**: `body_parser_freetext.py` (FR-12 path c — fused LLM call), entire `sp_alert_parser/`, entire `outbound/`.

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
    """FR-52 5-step routing pipeline + FR-85 doc_type classification + FR-86 storage matrix
    output per inbound attachment (revised 2026-06-09 per `[D-053]` impl note 2026-06-08):
    routing produces zero-or-more item matches (per FR-79 multi-item association); doc_type
    classified independently per FR-85; FR-86 alignment determines which NSD path the file
    lands on; one DocumentItemAssociation row per match per `[D-055]` symmetric M:M model."""
    file_hash:                       str                          # SHA-256 per `[D-039]` Step 0; PK on DocumentIndexRow
    matches:                         list["AttachmentItemMatch"]  # FR-79 — zero matches → unrouted to Default work-item per FR-78; N matches → N DocumentItemAssociation rows
    doc_type:                        Literal["test_report", "tech_report", "waiver", "compliance_certification_release_notes", "unresolved"]  # 5-value per `[D-053]` impl note 2026-06-08
    doc_id_slug:                     str | None                   # derived from filename per `[D-039]` Step 1; None when staged-not-revision-determined OR doc_type=unresolved
    rev_number:                      int | None                   # `[D-039]` outcome; None until determination passes
    document_index_row_file_hash:    str                          # storage.add_document_index_row PK = file_hash
    classification_resolution:       ClassificationResolution     # FR-85 outcome (FilenameRegex | LLMClassified | UnresolvedLowConfidence) — see ClassificationResolution enum
    routing_resolution:              "RoutingResolution"          # FR-52 step that won — imported from storage.RoutingResolution
    inferred_tg_name:                str | None                   # `[D-060]` channel-to-TG resolution; null only for SP-UI direct upload
    nsd_path_type:                   "NSDPathType"                # FR-86 — classified | unrouted | staged_not_classified | staged_not_revision — imported from storage.NSDPathType
    is_duplicate:                    bool                         # `[D-039]` Step 0 — true if file_hash already in document_index

@dataclass(frozen=True)
class AttachmentItemMatch:
    """One (item_id, confidence) match per FR-79 multi-item association. Mirrors
    llm.RouteAttachmentMatch but adds the routing step that produced it (for diagnostic /
    audit). One DocumentItemAssociation row written per match per `[D-055]`."""
    item_id:    str
    confidence: float                                      # [0.0, 1.0]
    source:    "RoutingResolution"                         # which FR-52 step produced this match (SubstringMatch / FuzzyMatch / FolderRouting / LLMRouteAttachment); imported from storage.RoutingResolution

class ClassificationResolution(str, Enum):
    """Per FR-85 2-step doc_type classification ladder — records which step resolved the
    classification (or UnresolvedLowConfidence on Step 2 below-threshold). Added 2026-06-09."""
    FILENAME_REGEX            = "FilenameRegex"             # FR-85 Step 1 — per-customer YAML regex match (covers all 4 actionable doc_types incl. compliance_certification_release_notes)
    LLM_CLASSIFIED            = "LLMClassified"             # FR-85 Step 2 — CLASSIFY_DOC_TYPE TaskKind above threshold; restricted to {test/tech/waiver}
    UNRESOLVED_LOW_CONFIDENCE = "UnresolvedLowConfidence"   # FR-85 Step 2 below threshold → doc_type = DocType.UNRESOLVED sentinel → FR-86 staged-not-classified path → FR-87 step (B) TPM resolution

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
        candidate_items:   list[dict],     # [{item_id, item_name, item_description, item_type, tg_name}]; item_type per the 4-value enum {Confirmation, TEST_TECH_WAIVER_REPORT, COMPLIANCE_CERTIFICATION_RELEASE_NOTES, Default}
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
    """Per FR-52 5-step routing pipeline (`[D-053]` impl note 2026-06-08) + FR-85 2-step doc_type
    classification ladder + FR-86 4-path storage matrix. Implements the full inbound-attachment
    pipeline for the standalone (non-fused) path. The fused path lives in body_parser_freetext.py.

    **Per-attachment pipeline (revised 2026-06-09)**:

      Step 0: file_hash dedup (`[D-039]` Step 0)
        - storage.get_document_index_row_by_hash(file_hash=...)
        - if found → skip; emit FIX record; return RoutedAttachment(is_duplicate=True)

      **Parallel branches** (independent per FR-85; ordering and parallelization are
       implementation choice — at the requirements layer, classification + routing have no
       dependency in either direction; both feed the FR-86 storage matrix below):

      Branch A — **FR-85 doc_type classification (2-step ladder)**:
        Step A1: Filename regex (FR-85 Step 1)
          - Load per-customer rules from `customizations/<customer_slug>/doc_type_filename_rules.yaml`
            (universal fallback at `default_doc_type_rules.yaml`)
          - Patterns cover all 4 actionable doc_types `{test_report, tech_report, waiver,
            compliance_certification_release_notes}` — note `compliance_certification_release_notes`
            is detected by regex ONLY (LLM never classifies into the bundle).
          - Top single-match wins → classification_resolution = `FilenameRegex`; doc_type set.
          - Multi-match → fall through to Step A2.
        Step A2: LLM CLASSIFY_DOC_TYPE (FR-85 Step 2)
          - Invoke llm.CLASSIFY_DOC_TYPE with first_page_excerpt + restricted candidate set
            `{test_report, tech_report, waiver}` (3 values; LLM never returns
            `compliance_certification_release_notes` nor `unresolved`).
          - Above threshold (default 0.85 per `Fr52Config.doc_type_classifier_threshold`) →
            classification_resolution = `LLMClassified`; doc_type set.
          - Below threshold → doc_type = `DocType.UNRESOLVED` sentinel;
            classification_resolution = `UnresolvedLowConfidence`.

      Branch B — **FR-52 5-step item routing pipeline**:
        Step B1: Strict substring match — `item_description` is comma-separated tag list per
          template_schema FR-82; every tag must appear in filename (AND logic); top
          tag-count match wins → matches list populated; routing_resolution = `SubstringMatch`.
        Step B2: Fuzzy match — rapidfuzz score(filename, item_name) per candidate; top scorer
          above `Fr52Config.fuzzy_threshold` wins → matches list populated; routing_resolution
          = `FuzzyMatch`.
        Step B3: **FR-77 Type-2 source-folder → work-item template** (NEW 2026-06-09; TG opt-in
          via `TGGroupBase.folder_routing_enabled = True`) — for NSD-direct files: outermost
          folder name relative to milestone root; for zip-extracted: outermost folder name
          relative to extracted-zip root; substring match against entries in `TGFolderRouting`
          (loaded from `customizations/template_schemas/<customer_slug>/folder_routing.yaml`);
          longest-substring-wins → matches list populated; routing_resolution = `FolderRouting`.
        Step B4: LLM ROUTE_ATTACHMENT — invoke llm.ROUTE_ATTACHMENT with first_page_excerpt +
          candidate_items (narrowed set surviving steps B1-B3); returns
          `list[RouteAttachmentMatch]` per FR-79 multi-item association (above-threshold matches
          committed); routing_resolution = `LLMRouteAttachment`. Caller emits LLG-W008 / EML-W007
          if summed-confidence exceeds `Fr52Config.route_attachment_over_routing_threshold`.
        Step B5: Staged → milestone's default work-item (FR-78) — if steps B1-B4 produced zero
          matches → matches = [(Default work-item delivery_item_id)]; routing_resolution =
          `StagedDefault`.

      **`inferred_tg_name` resolution** (per `[D-060]` impl note 2026-06-08): independent of
      Branch A/B outcomes — derived from inbound channel at receipt time (email_group_alias /
      owner_email reverse-lookup via TGGroupBase). Recorded on DocumentIndexRow.inferred_tg_name
      AND surfaced in the unrouted NSD path segment per FR-86 + `NSDPath.internal_default_workitem`
      signature. See `inbound/tg_resolver.py`.

      Step C: **`[D-039]` new-vs-revision determination** (FR-86 conditional)
        - GATED on (`doc_type != UNRESOLVED` AND `item_type != Default`) per FR-86 skip rule.
          When gate fails → SKIP entirely; deferred to FR-83 reassignment / FR-87 step (B)
          TPM resolution time.
        - When gate passes: Step 0 hash (`[D-039]`); Step 1 slug match via
          storage.find_doc_id_slugs_for_item(item_id, doc_type); Step 2 LLM CLASSIFY_DOC on
          ambiguity; Step 3 staged on Step-2 low-confidence.

      Step D: **FR-86 storage matrix dispatch** (the path the file actually lands at)
        - **classified** (alignment passes per FR-86 AND `[D-039]` Step 3 didn't stage) →
          `NSDPath.internal_classified(carrier, device, milestone, tg, item, doc_type,
          doc_id_slug, rev_number)`; nsd_path_type = `CLASSIFIED`.
        - **unrouted** (routing_resolution = StagedDefault) → for each item match (the
          single Default work-item match):
          `NSDPath.internal_default_workitem(carrier, device, milestone, inferred_tg_name,
          original_filename)` per `[D-060]` impl note 2026-06-08; nsd_path_type = `UNROUTED`.
        - **staged-not-revision-determined** (alignment passes BUT `[D-039]` Step 3 staged) →
          `NSDPath.internal_staged_revision(carrier, device, milestone, tg, item, doc_type,
          original_filename)`; nsd_path_type = `STAGED_NOT_REVISION`; emit EML-W003 (renamed).
        - **staged-not-classified** (alignment FAILS per FR-86 — (item_type, doc_type) pair
          misaligned, e.g., TEST_TECH_WAIVER_REPORT item with doc_type=
          compliance_certification_release_notes OR any non-Default item with
          doc_type=unresolved) → `NSDPath.internal_staged_classification(carrier, device,
          milestone, tg, item, original_filename)`; nsd_path_type = `STAGED_NOT_CLASSIFIED`;
          emit EML-W006.

      Step E: Write
        - storage NSD write to the path resolved in Step D
        - storage.add_document_index_row(file_hash=..., doc_type=..., classification_resolution=...,
          routing_resolution=..., inferred_tg_name=..., ...)
        - For each match in matches list: storage.add_document_item_association(file_hash,
          delivery_item_id, local_nsd_path=resolved_path, nsd_path_type=NSDPathType.X, ...)
          per `[D-055]` / `[D-056]` composite PK.
        - storage.log_communication(kind='attachment_classified', ...)

      Step F: Post-write (deferred / config-gated; same as prior)
        - PLM upload via issue_tracker.upload_attachment per `[D-055]` fan-out across distinct
          (owner_email, plm_id) pairs over the DocumentItemAssociation matches —
          fan_out_plm_associations(file_hash) returns the deduplicated upload targets.
          Gated by `Fr52Config.plm_upload_enabled` flag.
        - FR-53 LLM review trigger — gated by per-association `item.review_required` (true on
          TEST_TECH_WAIVER_REPORT items only per FR-7) + `doc_type ∈ {test/tech/waiver}` per
          FR-85 + `Fr52Config.review_required_enabled` flag. NOT fired for doc_type ∈
          {compliance_certification_release_notes, unresolved} nor for Default work-item docs.

      **Net for FR-79 multi-item case**: a single file_hash matched to N items produces N
      DocumentItemAssociation rows in the same transaction; PLM fan-out per `[D-055]`
      deduplicates by distinct (owner_email, plm_id) — typically 1-2 actual PLM uploads even
      when N=5 work-items share the same owner.
    """

    def __init__(
        self,
        storage:                                StorageBackend,
        llm:                                    LLMProvider,
        tg_resolver:                            "TgResolver",                  # per `[D-060]` impl note 2026-06-08 — channel→TG resolution (NSD ingress_nsd / email_group_alias / owner_email / PLM-id reverse-lookup)
        doc_type_filename_rules_path:           Path,                          # per-customer FR-85 Step 1 YAML
        doc_type_classifier_threshold:          float = 0.85,                   # FR-85 Step 2 confidence threshold
        route_attachment_over_routing_threshold: float = 2.0,                  # FR-79 — summed-confidence threshold for EML-W007 / LLG-W008
        issue_tracker:                          "IssueTracker | None" = None,  # None in test scenario
        fuzzy_threshold:                        float = 0.85,
        llm_confidence_threshold:               float = 0.75,
        plm_upload_enabled:                     bool = True,
        review_required_enabled:                bool = True,
    ) -> None: ...

    async def route(
        self,
        attachment: InboundAttachment,
        batch_id:   str,
        candidate_items: list[dict],   # [{item_id, item_name, item_description, item_type, tg_name}]; item_type per the 4-value enum {Confirmation, TEST_TECH_WAIVER_REPORT, COMPLIANCE_CERTIFICATION_RELEASE_NOTES, Default}
    ) -> RoutedAttachment: ...
```

### `sp_alert_parser/` *(Ph-1 production; skipped for test scenario)*

Per `[D-047]` + FR-84 + FR-87. SP sends one alert email per entity change (configured "Anything changes" on each list). Subject format `Alert_<List>_<Suffix> - <ItemTitle>`; body carries key:value pairs identifying the changed entity. Sub-module extracts the routing key `(ProjectID, MinorMilestone, ItemNumber)` from the body, resolves to a SP list + row, and emits an `SpEntityChangedEvent` to `workflow_engine` for downstream processing.

**FR-87 strict A→B→C TPM resolution action handlers (added 2026-06-09 per `[D-053]` impl note 2026-06-08)**: When TPM saves a resolution via the SP UI, the SP-alert email carries an action verb in the sub-header (per FR-84). The parser routes to one of these handlers based on action_type recognized in the body key:value:
- **`tpm_reassign_to_workitem`** (FR-87 step A; also fires for FR-83 default→real reassignment) — handler: storage.reassign_document_to_workitem(file_hash, source_delivery_item_id, target_delivery_item_id, pm_id); HILDA re-runs FR-86 storage matrix → moves file between paths; runs `[D-039]` revision determination if gate now permits.
- **`tpm_resolve_doc_type`** (FR-87 step B — NEW 2026-06-09) — handler: storage.update_doc_type(file_hash, target_doc_type, pm_id); HILDA validates FR-86 alignment invariant against current item_type; re-runs FR-86 storage matrix; runs `[D-039]` if doc_type just became non-UNRESOLVED and item is non-Default; emits FR-77 carrier-upload trigger if file reaches classified path.
- **`tpm_resolve_revision`** (FR-87 step C — NEW 2026-06-09) — handler: storage.set_revision_resolution(file_hash, verdict ∈ {NEW_DOCUMENT, REVISION_OF}, target_doc_id_slug, pm_id); HILDA assigns doc_id_slug + rev_number; moves file from `_staged_revision/` to canonical classified path; emits FR-77 carrier-upload trigger.

Each handler emits a `CommunicationLog` entry with `action_type` matching the verb + `credential_id=pm_id` for FR-31 audit accountability. Misaligned saves (e.g., TPM tries to set doc_type=compliance_certification_release_notes on a TEST_TECH_WAIVER_REPORT item) are accepted (SP-side state stands) but doc lands on `staged-not-classified` per FR-86 — TPM must complete additional resolution steps.

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
    fuzzy_threshold:                         float = 0.85   # FR-52 step B2 fuzzy match threshold
    llm_confidence_threshold:                float = 0.75   # FR-52 step B4 ROUTE_ATTACHMENT threshold
    # FR-85 doc_type classification ladder config (added 2026-06-09 per `[D-053]` impl note 2026-06-08):
    doc_type_filename_rules_path:            Path = Path("customizations/<customer_slug>/doc_type_filename_rules.yaml")   # FR-85 Step 1 — per-customer YAML; universal fallback at `core/src/email_service/default_doc_type_rules.yaml`
    doc_type_classifier_threshold:           float = 0.85   # FR-85 Step 2 — LLM CLASSIFY_DOC_TYPE confidence threshold; below → doc_type = DocType.UNRESOLVED sentinel; configurable per customer at `customizations/<slug>/doc_type_classifier_config.yaml`
    # FR-79 multi-item over-routing detection (added 2026-06-09):
    route_attachment_over_routing_threshold: float = 2.0    # FR-79 — summed confidence across multiple matches; above → emit LLG-W008 / EML-W007 for ops visibility (all matches still committed per FR-79 contract)
    # FR-86 storage matrix dispatch:
    plm_upload_enabled:                      bool = True    # gates Step F PLM fan-out via storage.fan_out_plm_associations()
    review_required_enabled:                 bool = True    # gates Step F FR-53 LLM review trigger (additional gate beyond item.review_required + doc_type ∈ {test/tech/waiver})
    nsd_root:                                Path = Path("/mnt/hilda/internal")
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
EML-W003  Attachment revision determination ambiguous — written to `_staged_revision/` path per FR-86 (NSDPathType=STAGED_NOT_REVISION); awaits FR-87 step (C) TPM resolution (revised 2026-06-09: path renamed from prior `<doc_type>/staged/` per FR-86 4-path matrix)
EML-W004  Email classified 'OTHER' — neither SP alert nor BATCH-id; surfaced for PM triage (recoverable)
EML-W005  RETIRED 2026-06-09 per `[D-053]` impl note 2026-06-08 — the prior "routing/type mismatch" warning is replaced by the FR-86 storage matrix landing rule (misaligned (item_type, doc_type) pairs land on `staged-not-classified` path per EML-W006 instead of being raised as a warning). Code reserved; do not reuse.
EML-W006  Attachment misaligned per FR-86 alignment invariant — (item_type='{item_type}', doc_type='{doc_type}') pair landed on `staged-not-classified` path (NSDPathType=STAGED_NOT_CLASSIFIED); awaits FR-87 step (B) TPM doc_type resolution OR step (A) work-item reassignment (recoverable; added 2026-06-09)
EML-W007  Attachment over-routed per FR-79 — N={n} matches above threshold with summed confidence {score} on file_hash='{file_hash}'; all matches committed per `[D-055]` symmetric M:M contract but flagged for ops review against `Fr52Config.route_attachment_over_routing_threshold` (recoverable; added 2026-06-09)
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
- **`[D-053]` impl note 2026-06-08 corrected model** (cascaded 2026-06-09) — `doc_type` classification + work-item routing are now **independent parallel pipelines** that feed the FR-86 storage matrix at the end (was: single bundled pipeline with doc_type as a routing input). FR-85 2-step doc_type ladder (regex covers all 4 actionable doc_types; LLM restricted to `{test/tech/waiver}`) + FR-52 5-step routing pipeline (substring → fuzzy → FR-77 Type-2 folder routing → LLM ROUTE_ATTACHMENT → staged-to-default) run with no dependency between them at the requirements layer. FR-86 alignment invariant determines which of 4 NSD paths (classified / unrouted / staged-not-revision-determined / staged-not-classified) the file lands on. `RoutedAttachment` returns multi-match per FR-79 (`list[AttachmentItemMatch]` — was single `item_id`).
- **FR-77 Type-2 folder routing as FR-52 step B3** (added 2026-06-09) — per-TG opt-in via `TGGroupBase.folder_routing_enabled`. Source-folder name (outermost relative to milestone root for NSD-direct files, or relative to extracted-zip root for zip-extracted files) substring-matched against `TGFolderRouting` entries at `customizations/template_schemas/<customer_slug>/folder_routing.yaml`. Longest-substring-wins on multi-match. When TG opts out (default), step B3 is a no-op pass-through to step B4 (LLM ROUTE_ATTACHMENT).
- **`inferred_tg_name` resolved at receipt time** per `[D-060]` impl note 2026-06-08 — `inbound/tg_resolver.py` derives TG from inbound channel (email_group_alias on the To/CC list; owner_email reverse-lookup via TGGroupBase). Surfaced on both `DocumentIndexRow.inferred_tg_name` (authoritative) AND `NSDPath.internal_default_workitem` path segment (mirrors row for filesystem-level TPM browsing). Required for unrouted-to-Default-work-item docs so TPM can group unrouted docs by TG at FR-83 reassignment time.

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
- `storage` — `add_document_index_row`, `get_document_index_row_by_hash` (Step 0 hash dedup per `[D-058]`), `find_doc_id_slugs_for_item` (`[D-039]` Step 1 slug match), `add_document_item_association` (one row per FR-79 match per `[D-055]` symmetric M:M), `fan_out_plm_associations` (Step F PLM upload), `reassign_document_to_workitem` (FR-87 step A handler), `update_doc_type` (FR-87 step B handler — NEW), `set_revision_resolution` (FR-87 step C handler — NEW), `log_communication`. NSD client used for: audit (`email/<message_id>/`), classified (`<tg>/<item>/<doc_type>/<doc_id_slug>/rev1/`), unrouted (`<inferred_tg_name>/_unrouted/<filename>` per `[D-060]` impl note 2026-06-08), staged-not-revision-determined (`<tg>/<item>/<doc_type>/_staged_revision/<filename>`), and staged-not-classified (`<tg>/<item>/_staged_classification/<filename>`) writes per FR-86 storage matrix. Consumed enums: `RoutingResolution`, `NSDPathType`.
- `llm` — `LLMProvider.invoke()` with `CLASSIFY_DOC_TYPE` (FR-85 Step 2 — restricted candidate set `{test/tech/waiver}`), `ROUTE_ATTACHMENT` (FR-52 step B4 — returns `list[RouteAttachmentMatch]` per FR-79), `CLASSIFY_DOC` (`[D-039]` Step 2), `CLASSIFY_MESSAGE` (FR-12 path c) TaskKinds. 5 TaskKinds total per `[D-053]` impl note 2026-06-08.
- `template_schema` — 5-value `DocType` enum (test_report / tech_report / waiver / compliance_certification_release_notes / unresolved); 4-value `ItemType` enum (Confirmation / TEST_TECH_WAIVER_REPORT / COMPLIANCE_CERTIFICATION_RELEASE_NOTES / Default); `DeliveryItemBase` (for `candidate_items` shape); `TGGroupBase.ingress_nsd` + `folder_routing_enabled` (for FR-77 Type-2 step B3 + tg_resolver); `TGFolderRouting` (loaded from `customizations/template_schemas/<customer_slug>/folder_routing.yaml`); slug helpers.
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
RPT|EML|run-00001|2026-05-28T10:00:00Z|messages_fetched=84|owner_reply=80|sp_alert=2|other=2|attachments_total=147|dedup_skipped=3|classification_filename_regex=120|classification_llm_classified=15|classification_unresolved=12|routing_substring_match=85|routing_fuzzy_match=31|routing_folder_routing=12|routing_llm_route_attachment=14|routing_staged_default=5|nsd_classified=125|nsd_unrouted=5|nsd_staged_not_classified=12|nsd_staged_not_revision=5|multi_item_associations=8|over_routed_w007=1
```

```
python -m core.src.email_service.email_service_cli --mock
```
Spins up a stub `EmailReceiver` returning fixture emails; pairs with `MockLLM` + `storage` test instance. End-to-end pipeline runs in-memory without IMAP / SMTP / LLM gateway. Useful for CI.

```
python -m core.src.email_service.email_service_cli --send --to <addr> --subject "<text>" --body-file <path>
```
Outbound send harness — sends one message via SMTP using the configured credentials; emits `EML-MET` with delivery confirmation. Body content read from file (not echoed to RPT). Used for manual outreach + outbound-path testing.

**QC template** (`EML:inbound_processing` — registered in `diagnostics/qc.py`; revised 2026-06-09 for FR-85 / FR-86 / FR-79 fields):
```
Fields: messages_fetched (int), owner_reply (int), sp_alert (int), other (int),
        attachments_total (int), dedup_skipped (int),
        classification_filename_regex (int), classification_llm_classified (int),
        classification_unresolved (int),
        routing_substring_match (int), routing_fuzzy_match (int), routing_folder_routing (int),
        routing_llm_route_attachment (int), routing_staged_default (int),
        nsd_classified (int), nsd_unrouted (int), nsd_staged_not_classified (int),
        nsd_staged_not_revision (int),
        multi_item_associations (int), over_routed_w007 (int),
        result (enum: OK / WARN / FAIL — FAIL when other_rate > 10% OR
                nsd_staged_not_classified_rate > 20% OR nsd_staged_not_revision_rate > 20%)
```

---

<!-- BEGIN:STRUCTURE -->
<!-- END:STRUCTURE -->
