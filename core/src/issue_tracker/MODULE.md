# Module: issue_tracker

> **Status:** **2026-06-25 strict-order Module #13 architect cascade revisit (D1-D15) + Ph-1 dev pass**. Closes HILDA's strict-order module sweep #1-13. Architectural framing pivots from a single generic `IssueTracker` Protocol to **two surface-distinct Protocols** — `CorpPlmAdapter` (5-API thin wrapper per `[D-027]` Teacher/Student over the corp PLM gateway) and `CustomerJiraAdapter` (standard JIRA REST, Ph-1 INFORMATIONAL POLLING ONLY per `[D-092]`). Generic `IssueTracker` Protocol is **soft-retained Ph-1 for legacy adapter compatibility** (`jira_adapter.py`, `mock_adapter.py`, `customizations/issue_tracker/defecttrack_adapter.py`) and **scheduled for removal Ph-2** once defecttrack + the legacy Jira adapter are rewired onto the two new Protocols. New sub-module layout (`corp_plm/`, `customer_jira/`) reflects the Q1-Q7 architect locks 2026-06-25.
>
> **Rollback log:**
> - **2026-06-25 (Module #13 strict-order architect cascade + Ph-1 dev — 15 drift items applied)** — sweep close-out. **D1 — Protocol split**: `CorpPlmAdapter` (5 methods matching corp PLM API spec: `create_plm`, `close_plm`, `get_documents_list`, `download_file`, `upload_file`) + `CustomerJiraAdapter` (Ph-1 informational surface: `search_issues`, `get_issue`, `list_comments`; Ph-2 stubs documented but not declared on the Protocol). Generic `IssueTracker` Protocol with 10+ methods retained for Ph-1 legacy compat (defecttrack + jira_adapter use it) — **slated for removal Ph-2** per `[D-092]` and the corp PLM 5-API surface lock. **D2 — Sub-module restructure**: `corp_plm/` (adapter.py thin-wrap per `[D-027]`; poller.py deadline-tiered per FR-26; in_flight_tracker.py per Q6) + `customer_jira/` (adapter.py standard JIRA REST; poller.py Ph-1 informational per `[D-092]`) + `utils.py` (`derive_tpm_corp_id`) + `mocks.py` (`MockCorpPlmAdapter` + `MockCustomerJiraAdapter`). Existing `jira_adapter.py` + `mock_adapter.py` retained Ph-1 for back-compat. **D3 — Corp PLM 5-API surface** per architect Q4 spec: thin wrapper with abstract `_invoke_*` methods raising `NotImplementedError ITR-E001` by default; concrete binding body filled by Cline on Work PC per `[D-027]`. HILDA owns retry + in-flight tracking + CommunicationLog discipline; binding owns auth/protocol/errors. **D4 — Customer JIRA standard surface** per `[D-092]` Ph-1 informational polling lock. Ph-2 mutation methods documented as `NotImplementedError ITR-E007` stubs for forward-looking awareness. **D5 — Dataclasses**: `PlmDocumentNode(document_id, document_name, file_id)` per Q7 both fields persist; `JiraIssue(issue_key, summary, status, owner_corp_email, last_updated, project_key, raw)` + `JiraComment(comment_id, author_corp_email, body, created_at)` Ph-1 informational fields only. **D6 — FR-26 PLM polling**: `CorpPlmPoller` deadline-tiered cadence per Q3 + FR-23 cross-FR cadence consistency lock (baseline 60 min; `≤14d → 30`, `≤7d → 15`, `≤3d → 5`, `≤1d → 1`); per active item with `plm_id` set + non-terminal `delivery_state`; `get_documents_list` → diff against `DocumentIndexRow` → `download_file` to NSD audit path `<tg>/<item>/plm/<plm_id>/<file_id>/` → emit TriggerEvent (`AttachmentReceived`) for downstream FR-86 routing. **D7 — `InFlightDownloadTracker`** per Q6: asyncio.Lock-guarded dict keyed on `(plm_id, file_id)` with `acquire()` async context manager; concurrent acquire on same key is no-op skip emitting `ITR-W003` warning code (in-flight skip warning). Ph-1 in-memory; **Ph-2 Postgres-backed for restart resilience** (TODO noted). **D8 — `derive_tpm_corp_id(projects_tpm_email) -> str`** helper in `utils.py`: splits on `@` and takes local part. Used by workflow_engine ActionKinds + tracker setup before calling corp PLM. Per Q4: PER-CUSTOMER (not shared HILDA ops-team), reported to corp PLM as ACTION ATTRIBUTION parameter (HILDA's actual auth flows via `corp_plm_gateway` PC per `[D-019]` + `[D-021]`); `tpm_corp_id` itself is NOT a credential. **D9 — Customer JIRA Ph-1 informational polling** per `[D-092]`: `CustomerJiraPoller` searches JQL per `Projects.TPM` filter + delivery_item references; collects into in-memory state for FR-9 outreach attachments (PM has JIRA URL in outreach per `[D-092]`); **NO SP write-back Ph-1**. Per FR-25 (b) close-intent flows via email per FR-12. Ph-2 will add `jira_open_ticket_count` + `jira_ticket_summary_json` columns on Deliverables per `[D-092]`. **D10 — `[D-105]` 4-field owner identity**: `owner_corp_id` is the parameter passed to corp PLM `create_plm`; `owner_corp_email` is the JIRA author lookup; both narrative updated. **D11 — `[D-091]` customer_id throughout**: per-customer adapter file paths `customizations/issue_tracker/<customer_id>_corp_plm_adapter.py` + `<customer_id>_customer_jira_adapter.py`; YAML config keys use `customer_id`. **D12 — `[D-117]` SP NTLM digest-dance for plm_id writeback**: after `create_plm` returns plm_id, HILDA writes it back to `Deliverables_<customer_id>` SP row via `SpClient` (digest-dance per `[D-117]`); this write happens in `workflow_engine` ActionKind (e.g., `START_ITEM_COLLECTION` or new `CREATE_PLM` ActionKind), **NOT directly in issue_tracker**. **D13 — Anchors refreshed**: drop generic `IssueTracker` framing as the primary contract; add `[D-091]`, `[D-092]` (customer JIRA Ph-1 informational), `[D-094] SUPERSEDED` (legacy ImapInboundChannel framing dropped), `[D-098]` (FR-68 hash-match dropped — UI file-exists check sufficient), `[D-104]`, `[D-105]` (4-field owner), `[D-106]` (TGGroupBase DROPPED), `[D-117]` (SP NTLM digest), `[D-118]` (SP UI engineer provisioning), `[D-119]` (tpm_resolved_doc_type 4-value). **D14 — Status header + 2026-06-25 rollback log entry (this entry)**. **D15 — Error codes**: add `ITR-W003` (PLM download in-flight; skipping duplicate per Q6), `ITR-W004` (PLM API N-retries-exhausted — HILDA OPS alert mechanism TBD per Q5 architect discussion; **TODO(architect-discussion)**), `ITR-W005` (PLM upload verification failure per `[D-098]` narrowing). Existing codes ITR-E001..E008, ITR-W001/W002 preserved; semantic stable per diagnostics Invariant.
> - **2026-06-09 (previous cascade)** — generic `IssueTracker` Protocol + jira_adapter + mock_adapter draft; PLM gateway framing per `[D-003]` impl note + `[D-021]` impl note. **Superseded 2026-06-25** by Module #13 cascade above; legacy artifacts retained Ph-1 for back-compat.

**Purpose**: Two adapter targets for HILDA's external issue/PLM tracking. **(a) Corp PLM** — wraps the 5 pre-existing corp PLM APIs (`createPLM`, `closePLM`, `getdocumentslist`, `downloadFile`, `uploadFile`) per architect 2026-06-25 spec via `CorpPlmAdapter` thin-wrapper per `[D-027]` Teacher/Student split (HILDA-side abstract `_invoke_*` methods + per-customer concrete bodies filled in by Cline on Work PC at `customizations/issue_tracker/<customer_id>_corp_plm_adapter.py`); auth flows via `corp_plm_gateway` PC per `[D-019]` no-credential pattern (HILDA passes `tpm_corp_id` only as action attribution, not as credential). **(b) Customer JIRA** — standard JIRA REST via `CustomerJiraAdapter`; **Ph-1 INFORMATIONAL POLLING ONLY per `[D-092]`** (HILDA polls customer JIRA for waiver/issue tickets but does **NOT** write back to SP; Ph-2 = SP write-back + ticket creation per `[D-092]`). Serves FR-25 (a)/(b), FR-26 (PLM polling), FR-67 (PLM stale-attachment deletion — Ph-2), FR-68 (PLM-NSD sync verification post-dispatch — Ph-2 reduced to UI file-exists check per `[D-098]`), NFR-2 (no proprietary content in compact reports), NFR-17, NFR-18; anchors `[D-019]`, `[D-021]`, `[D-027]`, `[D-035]`, `[D-040]`, `[D-041]`, `[D-091]`, `[D-092]`, `[D-098]`, `[D-105]`, `[D-117]`. **`[D-094]` SUPERSEDED** — legacy ImapInboundChannel framing dropped; inbound corp PLM events Ph-1 are POLLED by `CorpPlmPoller`, not pushed via webhook/long-poll. **`[D-106]` DROPPED** — TGGroupBase abstraction removed; PLM issue keying per `(device_id, milestone_name, owner_corp_id)` per FR-26 / FR-88 owner identity lock.

**Source-of-truth model** per `[D-035]` + `[D-040]` + `[D-041]`: corp PLM is the document source of truth for **submitted deliverables only** (post-OwnerClosed + TPM approval); the NSD classified path is the source of truth for in-progress deliverables. **Ph-1**: immediate PLM upload on every doc write per `[D-035]`. **Ph-2**: PLM upload deferred until OwnerClosed + TPM approval; FR-68 sync verification fires (UI file-exists check only per `[D-098]`).

**Workload assignment**: `hilda-worker` executes corp PLM API calls + JIRA REST calls + PLM polling loop + JIRA polling loop. `hilda-api` does not invoke either adapter directly Ph-1. Inbound corp PLM events arrive via HILDA's own polling per FR-26 (no webhook listener Ph-1; `[D-094]` SUPERSEDED).

---

## Sub-modules (Ph-1 layout per D2 cascade 2026-06-25)

```
core/src/issue_tracker/
  __init__.py                            ← exports new + legacy surfaces
  protocol.py                            ← CorpPlmAdapter + CustomerJiraAdapter Protocols + dataclasses;
                                           legacy IssueTracker Protocol + dataclasses retained Ph-1 for
                                           back-compat (defecttrack + jira_adapter); slated for Ph-2 removal
  utils.py                               ← derive_tpm_corp_id helper per D8
  mocks.py                               ← MockCorpPlmAdapter + MockCustomerJiraAdapter
  corp_plm/
    __init__.py
    adapter.py                           ← CorpPlmAdapter thin wrapper per [D-027]; HILDA-side abstract
                                           _invoke_create_plm / _invoke_close_plm / _invoke_get_documents_list /
                                           _invoke_download_file / _invoke_upload_file methods (raise
                                           NotImplementedError ITR-E001 by default); per-customer subclass
                                           overrides them with concrete binding body at
                                           customizations/issue_tracker/<customer_id>_corp_plm_adapter.py
    poller.py                            ← CorpPlmPoller deadline-tiered cadence per FR-26 / FR-23 lock
    in_flight_tracker.py                 ← InFlightDownloadTracker per Q6 (asyncio.Lock-guarded dict)
  customer_jira/
    __init__.py
    adapter.py                           ← CustomerJiraAdapter standard JIRA REST httpx-backed; Ph-1
                                           informational read-only (search_issues / get_issue / list_comments);
                                           Ph-2 mutation methods stubbed raise ITR-E007
    poller.py                            ← CustomerJiraPoller Ph-1 informational polling per [D-092]
  issue_tracker_cli.py                   ← UPDATED: --diagnostic / --mock / --contract (legacy) +
                                           --plm-diagnostic / --jira-diagnostic new modes

customizations/issue_tracker/
  <customer_id>_corp_plm_adapter.py     ← per-customer corp PLM subclass: concrete _invoke_* method bodies
                                           filled in by Cline on Work PC per [D-027] Teacher/Student split;
                                           NEVER lands on public github per NFR-2
  <customer_id>_customer_jira_adapter.py ← per-customer JIRA subclass: customer JIRA base_url + auth config
  defecttrack_adapter.py                 ← LEGACY (uses generic IssueTracker Protocol; Ph-2 rewire pending)
  tests/test_contract.py                 ← LEGACY contract suite C01-C10 (generic IssueTracker)
```

**Implementation notes:**

- **Thin wrapper per `[D-027]`** — `CorpPlmAdapter` HILDA-side body in `core/` owns retry + in-flight tracking + CommunicationLog discipline + parameter normalization. The 5 `_invoke_*` abstract methods carry the actual `corp_plm_gateway` PC call shape; concrete per-customer subclass at `customizations/issue_tracker/<customer_id>_corp_plm_adapter.py` overrides them. Default impls raise `NotImplementedError ITR-E001` so tests against the in-`core/` base class fail loudly if not subclassed.
- **In-flight tracker scope** — Ph-1 keyed on `(plm_id, file_id)` per Q6 + Q7; Ph-2 will key additionally on `download_path` once concurrent downloads to disjoint paths is a use case.
- **No deps beyond what's installed** — JIRA adapter uses `httpx` direct REST calls (no `jira` Python library dependency) for Ph-1 simplicity.

---

## Public surface

### `protocol.py` (new — Q1-Q7 locks)

```python
@dataclass(frozen=True)
class PlmDocumentNode:
    """One entry returned by getdocumentslist. Per Q7 both document_id + file_id persist
    on HILDA's DocumentIndexRow per file; both are required for downloadFile."""
    document_id:   str
    document_name: str
    file_id:       str


@dataclass(frozen=True)
class JiraIssue:
    """Ph-1 informational fields only per [D-092]. raw dict carries any
    customer-specific fields surfaced by FR-9 outreach attachments."""
    issue_key:         str
    summary:           str
    status:            str               # JIRA status string verbatim; HILDA does not normalize Ph-1
    owner_corp_email:  str | None        # JIRA assignee → corp email lookup per [D-105] 4-field identity
    last_updated:      datetime
    project_key:       str
    raw:               dict               # full JIRA issue response for forward-looking Ph-2 use


@dataclass(frozen=True)
class JiraComment:
    """Ph-1 informational. author_corp_email keyed on JIRA author → corp email mapping per [D-105]."""
    comment_id:        str
    author_corp_email: str | None
    body:              str
    created_at:        datetime


class CorpPlmAdapter(Protocol):
    """Thin wrapper around the 5 already-available corp PLM APIs per [D-027]
    Teacher/Student split. Concrete _invoke_* method body filled in by Cline on
    Work PC at customizations/issue_tracker/<customer_id>_corp_plm_adapter.py
    (or shared if PLM is multi-tenant). NEVER lands on public github per NFR-2.

    HILDA passes tpm_corp_id as ACTION ATTRIBUTION (NOT credential per [D-019]);
    actual auth flows via corp_plm_gateway PC per FR-25 (a)."""

    source_system: str   # immutable; equals customer_id

    async def create_plm(
        self,
        model_number:  str,    # device_id
        tpm_corp_id:   str,    # per-customer; derived from Projects.TPM local part per Q4
        title:         str,
        owner_corp_id: str,    # per [D-105] 4-field owner identity
        description:   str,
    ) -> str | None:
        """Returns plm_id; None on failure (ITR-E001 opaque per Q5). Caller writes
        the plm_id back to Deliverables_<customer_id> SP row via SpClient digest-dance per [D-117]."""
        ...

    async def close_plm(self, plm_id: str, tpm_corp_id: str) -> bool:
        """Returns True on success; False on failure. Called from workflow_engine.tasks/
        milestone.py FINAL_SWEEP ActionKind per Q2."""
        ...

    async def get_documents_list(
        self, plm_id: str, tpm_corp_id: str,
    ) -> list[PlmDocumentNode]:
        """Returns the list of {document_id, document_name, file_id} nodes for the PLM
        issue. Called by CorpPlmPoller per FR-26."""
        ...

    async def download_file(
        self,
        tpm_corp_id:   str,
        document_id:   str,
        document_name: str,
        file_id:       str,
        download_path: Path,
    ) -> bool:
        """Downloads one file to download_path. Returns True on success. Caller (poller)
        wraps this in InFlightDownloadTracker.acquire((plm_id, file_id)) per Q6."""
        ...

    async def upload_file(
        self, plm_id: str, file_name: str, file_path: Path,
    ) -> bool:
        """Uploads file_path to PLM issue plm_id. Returns True on success; ITR-W005 on
        post-upload verification failure per [D-098] narrowing."""
        ...


class CustomerJiraAdapter(Protocol):
    """Standard JIRA REST API. Ph-1 INFORMATIONAL POLLING ONLY per [D-092] —
    HILDA polls customer JIRA for waiver/issue tickets; close-intent flows via
    email reply path per FR-25 (b); no SP write-back in Ph-1 (Ph-2 adds
    jira_open_ticket_count + jira_ticket_summary_json columns on Deliverables
    per [D-092])."""

    source_system: str    # immutable; equals customer_id

    async def search_issues(self, jql: str) -> list[JiraIssue]: ...
    async def get_issue(self, issue_key: str) -> JiraIssue: ...
    async def list_comments(self, issue_key: str) -> list[JiraComment]: ...
    # Ph-2 deferred (raise NotImplementedError ITR-E007 stub):
    # async def create_issue(...), update_issue(...), add_comment(...),
    # transition_issue(...), upload_attachment(...), download_attachment(...)
```

### `utils.py` (D8 cascade)

```python
def derive_tpm_corp_id(projects_tpm_email: str) -> str:
    """Strip the domain from Projects.TPM email; return local part. Per Q4 architect lock 2026-06-25.

    Raises ITR-E002 if input is missing the '@' separator or is empty.
    """
```

### `corp_plm/poller.py` (D6 cascade)

```python
class CorpPlmPoller:
    """Deadline-tiered cadence per FR-26 + FR-23 cross-FR cadence consistency lock.

    Defaults (Ph-1 per Q3 architect direction; configurable per customer):
      baseline: 60 min        # >14 days from deadline
      ≤14 days → 30 min
      ≤7 days  → 15 min
      ≤3 days  → 5 min
      ≤1 day   → 1 min        # deadline-day

    Per active DeliveryItem with plm_id set + delivery_state ∈ {Open, OutreachSent,
    DocumentReceived, OwnerClosed} (NOT Closed / Cancelled):
      1. Resolve effective interval from days_to_deadline.
      2. Call CorpPlmAdapter.get_documents_list(plm_id, tpm_corp_id).
      3. Diff returned PlmDocumentNode list against persisted DocumentIndexRow entries
         keyed on (plm_id, file_id).
      4. For each new (plm_id, file_id): acquire InFlightDownloadTracker; on success,
         call CorpPlmAdapter.download_file(...) to NSD audit path
         <tg>/<item>/plm/<plm_id>/<file_id>/<document_name>.
      5. Emit TriggerEvent (kind=AttachmentReceived) for downstream FR-86 routing.
      6. Release InFlightDownloadTracker; log CommunicationLog with bounded enum tokens only.
    """
```

### `corp_plm/in_flight_tracker.py` (D7 / Q6 cascade)

```python
class InFlightDownloadTracker:
    """Per Q6 architect direction 2026-06-25 — prevents duplicate concurrent downloads
    keyed on (plm_id, file_id). Ph-1 in-memory asyncio.Lock-guarded dict;
    Ph-2 Postgres-backed for restart resilience (TODO).

    Usage:
        async with tracker.acquire((plm_id, file_id)) as acquired:
            if not acquired:
                logger.info("ITR-W003 in-flight skip")
                return
            await adapter.download_file(...)
        # release is automatic on context exit
    """

    async def acquire(
        self, key: tuple[str, str],
    ) -> AsyncContextManager[bool]:
        """Returns context manager. On enter: True if claim succeeded (caller proceeds);
        False if already in-flight (caller should skip + log ITR-W003)."""
        ...

    def in_flight(self) -> set[tuple[str, str]]:
        """Returns the set of (plm_id, file_id) keys currently in flight. For diagnostics
        + tests."""
        ...
```

### `customer_jira/adapter.py` (D4 cascade)

```python
class CustomerJiraAdapter:
    """Per-customer JIRA REST httpx wrapper. Ph-1 INFORMATIONAL READ-ONLY per [D-092].

    Per-customer config (base_url, auth, project_key) carried by per-customer subclass
    at customizations/issue_tracker/<customer_id>_customer_jira_adapter.py per [D-091].

    Auth: api_token via credential_service.get_credential(pm_id, system_type=JIRA,
    customer_id=...) per [D-107] credential scope; never stored on adapter instance.
    """
```

### `customer_jira/poller.py` (D9 cascade)

```python
class CustomerJiraPoller:
    """Per [D-092] Ph-1 informational polling lock — searches JQL per Projects.TPM
    filter + delivery_item references; collects results into in-memory state for FR-9
    outreach attachments. NO SP write-back in Ph-1. Close-intent flows via email
    reply path per FR-25 (b) + FR-12.

    Ph-2 will add SP write-back to Deliverables_<customer_id> columns:
      - jira_open_ticket_count (int)
      - jira_ticket_summary_json (JSON-as-string)
    """
```

### `mocks.py`

```python
class MockCorpPlmAdapter:
    """In-memory CorpPlmAdapter for tests. Counters per method invocation, deterministic
    plm_id generation, downloaded files tracked in-memory."""

class MockCustomerJiraAdapter:
    """In-memory CustomerJiraAdapter for tests. Stores issues + comments by issue_key;
    JQL search returns all by default."""
```

### `__init__.py` (re-exports)

```python
# New surfaces (Q1-Q7 locks 2026-06-25):
from core.src.issue_tracker.protocol import (
    CorpPlmAdapter, CustomerJiraAdapter,
    PlmDocumentNode, JiraIssue, JiraComment,
)
from core.src.issue_tracker.utils import derive_tpm_corp_id
from core.src.issue_tracker.corp_plm.in_flight_tracker import InFlightDownloadTracker
from core.src.issue_tracker.corp_plm.poller import CorpPlmPoller
from core.src.issue_tracker.customer_jira.poller import CustomerJiraPoller
from core.src.issue_tracker.mocks import MockCorpPlmAdapter, MockCustomerJiraAdapter

# Legacy Ph-1 back-compat surfaces (slated for Ph-2 removal):
from core.src.issue_tracker.protocol import (
    AttachmentInput, AttachmentRef, CommentRef, Issue, IssueChange,
    IssueQuery, IssueRef, IssuePriority, IssueStatus, IssueTracker, WebhookRef,
)
```

---

## Invariants

- **`source_system` is immutable** on both new Protocols — equals `customer_id` per `[D-091]`; set at adapter construction; appears verbatim on every record emitted by the adapter.
- **Async-native** — all Protocol methods on both new surfaces are `async def`.
- **`close_plm` is idempotent** — calling on an already-closed PLM returns `True` without error (binding may need to translate idempotence into the corp PLM API's semantics).
- **No credential material in reports/logs/repr** — `tpm_corp_id` MAY appear (not a secret per Q4); JIRA api_token NEVER appears. Per NFR-2.
- **No proprietary content in compact reports** — PLM document content / filenames / JIRA ticket bodies excluded from `ITR-RPT`; only counts + status enum tokens.
- **`derive_tpm_corp_id` is pure** — no I/O; transforms `email_local@domain.com` → `email_local` deterministically.
- **In-flight tracker idempotence** — `acquire(key)` from concurrent tasks: first wins (acquired=True); subsequent acquires return `acquired=False` until the first releases. Ph-1 in-memory (process-lifetime); Ph-2 Postgres-backed (restart-survivable).
- **`InFlightDownloadTracker` keys are `(plm_id, file_id)`** per Q6 + Q7; not `(plm_id, document_id)` — `file_id` is the unit of physical retrieval.

---

## Error codes (ITR prefix — registered in `diagnostics/error_codes.py`)

```
Existing (Ph-1 carry-over):
  ITR-E001  Unauthorized: credentials rejected by issue tracker '{system}'
            (REPURPOSED 2026-06-25: also = opaque corp PLM API call failure per Q5
             error handling lock — N retries exhausted → ITR-W004)
  ITR-E002  Issue not found / derive_tpm_corp_id input malformed
  ITR-E003  Customer JIRA auth expired for '{account_id}' on '{customer_id}'
  ITR-E004  Transition '{transition}' not available from current state on '{issue_id}'
  ITR-E005  Attachment upload failed for '{issue_id}': {reason}
  ITR-E006  Adapter '{slug}' not found in core/ or customizations/
  ITR-E007  Operation '{operation}' not supported by adapter '{adapter}'
            (CustomerJiraAdapter Ph-2 mutation stubs raise this Ph-1 per D4 cascade)
  ITR-E008  Conflict: idempotency key '{key}' already resolved to '{existing_id}'
            (renumbered from old ITR-E003 on 2026-06-21)
  ITR-W001  Rate limited by '{system}'; retry after {retry_after_s}s  (recoverable)
  ITR-W002  Webhook registration failed for '{system}'; falling back to poll_changes
            (recoverable; LEGACY: [D-094] SUPERSEDED — corp PLM Ph-1 has no webhook)

Added 2026-06-25 (Module #13 cascade D15):
  ITR-W003  PLM download in-flight for ({plm_id}, {file_id}); skipping duplicate (recoverable; Q6)
  ITR-W004  PLM API N-retries-exhausted for {plm_id} — HILDA OPS alert mechanism TBD
            (recoverable; Q5; TODO(architect-discussion): alert mechanism)
  ITR-W005  PLM upload verification failure for {plm_id} / {file_name}
            (recoverable; [D-098] narrowing)
```

---

## Key choices

- **`[D-027]` Teacher/Student** — corp PLM 5-API concrete binding body filled in by Cline on Work PC; HILDA scaffold ships abstract `_invoke_*` methods raising `NotImplementedError ITR-E001` by default. Per-customer subclass at `customizations/issue_tracker/<customer_id>_corp_plm_adapter.py` overrides them.
- **`[D-092]` customer JIRA Ph-1 = informational polling only** — HILDA polls JIRA, surfaces ticket URLs in FR-9 outreach attachments via in-memory state; does NOT write back to SP. Ph-2 will add SP columns + write-back.
- **`[D-098]` FR-68 hash-match dropped** — PLM upload verification reduces to `upload_file` return result + (Ph-2) UI file-exists check. Hash comparison out of scope per architect 2026-06-20.
- **`[D-105]` 4-field owner identity** — `owner_corp_id` is the PLM API parameter; `owner_corp_email` is the JIRA author lookup.
- **`[D-117]` SP NTLM digest-dance for plm_id writeback** — after `create_plm` returns, HILDA writes the plm_id back to `Deliverables_<customer_id>` SP row via `SpClient` (digest-dance flow); this happens in `workflow_engine` ActionKind, NOT in `issue_tracker`.
- **`[D-119]` tpm_resolved_doc_type 4-value** — orthogonal; influences FR-85 staged-fill semantics but not `issue_tracker` directly.
- **Deadline-tiered polling Q3** — matches FR-23 cross-FR cadence consistency lock (FR-23 is the canonical breakpoint spec; FR-26 PLM polling reuses it). Per-customer override via `polling_schedule` AutomationRule rows in storage.
- **In-flight tracker Q6** — in-memory asyncio.Lock-guarded dict Ph-1 (cleanest for single-process worker); Postgres-backed Ph-2 for restart resilience (each row keyed on `(plm_id, file_id)` with TTL).
- **`document_ID` vs `fileID` per Q7** — BOTH required for `download_file`; BOTH persist on `DocumentIndexRow`. No derivation.

---

## Non-goals

- **Not a full JIRA client** — Ph-1 surface is read-only (search / get / list_comments); Ph-2 mutation methods are documented stubs only.
- **Not a credentials store** — JIRA credentials flow through `credential_service.get_credential(pm_id, system_type=JIRA, customer_id=...)` per `[D-107]`; corp PLM auth flows via `corp_plm_gateway` PC per `[D-019]` (HILDA carries NO PLM credentials).
- **Not a retry orchestrator** — corp PLM retries are bounded (default N=5) inside `CorpPlmAdapter`; broader retry policy belongs to `workflow_engine`.
- **No corp PLM webhook listener Ph-1** — `[D-094]` SUPERSEDED. HILDA polls corp PLM per FR-26.
- **No SP write-back from customer JIRA Ph-1** — `[D-092]` defers to Ph-2.
- **No PLM-NSD hash-match verification** — `[D-098]` narrows FR-68 verification to `upload_file` return + UI file-exists check (Ph-2); Ph-1 records `plm_attachment_id` on `DocumentIndexRow`, nothing else.

---

## Depends on

- `diagnostics` — `ErrorCode`, `ReportWriter`, `PipelineError` (ITR codes registered in `diagnostics/error_codes.py`).
- `template_schema` — `TrackingModality.CorporatePLM` / `CustomerJIRA` enum members (guard before adapter calls per FR-26 + FR-25 (b)).
- `credential_service` — `get_credential(pm_id, system_type, customer_id=None)` per `[D-107]` scope (JIRA: PER_CUSTOMER; corp PLM: no creds).
- `storage` (light) — `DocumentIndexRow` schema for the doc-diff path; `polling_schedule` AutomationRule rows for deadline-tiered cadence config.

---

## Depended on by

- `workflow_engine` — calls `CorpPlmAdapter.create_plm` from `START_ITEM_COLLECTION` (or new `CREATE_PLM`) ActionKind per Q1; calls `close_plm` from `FINAL_SWEEP` per Q2; consumes `TriggerEvent(AttachmentReceived)` emitted by `CorpPlmPoller`.
- `tracker` — Ph-2: will consume JIRA + PLM events for DeliveryItem status sync (Ph-1: no integration).
- `messenger` — Ph-1: imports `AttachmentInput` from legacy `protocol.py` (back-compat preserved).

---

## Test interface

```
python -m core.src.issue_tracker.issue_tracker_cli --diagnostic
```
Adapter inventory RPT (legacy generic IssueTracker + new corp_plm + customer_jira):
```
RPT|ITR|run-00001|2026-06-25T10:00:00Z|adapters_found=4|adapters_valid=4|adapters_invalid=0
```

```
python -m core.src.issue_tracker.issue_tracker_cli --mock
```
Exercises `MockCorpPlmAdapter` + `MockCustomerJiraAdapter` end-to-end (create_plm → get_documents_list → download_file → close_plm + search_issues → get_issue → list_comments):
```
RPT|ITR|run-00001|2026-06-25T10:00:00Z|adapter=mock|ops_attempted=8|ops_ok=8|ops_fail=0
```

```
python -m core.src.issue_tracker.issue_tracker_cli --plm-diagnostic --customer-id <customer_id>
```
Loads per-customer corp PLM adapter from `customizations/issue_tracker/<customer_id>_corp_plm_adapter.py`; runs a `_invoke_*`-resolution check (no actual API call); emits `ITR-RPT` with method-by-method availability.

```
python -m core.src.issue_tracker.issue_tracker_cli --jira-diagnostic --customer-id <customer_id>
```
Loads per-customer JIRA adapter + runs Ph-1 informational read-only smoke (`search_issues` with trivial JQL); emits `ITR-RPT`.

**Legacy contract suite C01-C10** still runs against any legacy `IssueTracker` adapter (mock / jira / defecttrack) per `customizations/issue_tracker/tests/test_contract.py` until Ph-2 removal.

---

## TODOs flagged for next close-session

- **D-120 candidate**: corp PLM 5-API thin-wrapper + tpm_corp_id-as-attribution + in-flight tracking pattern. Surface in close-session triage; this cascade does NOT auto-promote ADR.
- **Q5 architect discussion**: HILDA OPS alert mechanism for `ITR-W004` (PLM API N-retries-exhausted). TBD; Ph-1 logs to `HildaOpsAlert` per FR-75 as fallback; explicit mechanism (email? messenger? dashboard banner?) needs ratification.
- **Ph-2 sub-modules**: Postgres-backed `InFlightDownloadTracker`; SP write-back from `CustomerJiraPoller` per `[D-092]`; full JIRA mutation surface; corp PLM webhook listener (revisit `[D-094]` SUPERSEDED).
- **Legacy removal**: `IssueTracker` Protocol + `jira_adapter.py` + `mock_adapter.py` + `customizations/issue_tracker/defecttrack_adapter.py` rewire onto new Protocols Ph-2.

---

<!-- BEGIN:STRUCTURE -->

- `AttachmentRef` — class — pub
- `CommentRef` — class — pub
- `CorpPlmAdapter` — class — pub — Thin wrapper around the 5 already-available corp PLM APIs per `[D-027]`.
- `CustomerJiraAdapter` — class — pub — Standard JIRA REST API. Ph-1 INFORMATIONAL POLLING ONLY per `[D-092]`.
- `Issue` — class — pub
- `IssueChange` — class — pub
- `IssuePriority` — class — pub
- `IssueQuery` — class — pub
- `IssueRef` — class — pub — LEGACY -- used by `IssueTracker` Protocol + `jira_adapter` + `mock_adapter`
- `IssueStatus` — class — pub
- `IssueTracker` — class — pub — LEGACY generic Protocol -- retained Ph-1 for back-compat (`jira_adapter`,
- `JiraComment` — class — pub — Ph-1 informational. author_corp_email keyed on JIRA author → corp email
- `JiraIssue` — class — pub — Ph-1 informational fields only per `[D-092]`. `raw` carries any customer-
- `MockCorpPlmAdapter` — class — pub — In-memory CorpPlmAdapter for tests. Implements the new 5-method Protocol.
- `MockCustomerJiraAdapter` — class — pub — In-memory CustomerJiraAdapter for tests. Implements Ph-1 informational
- `PlmDocumentNode` — class — pub — One entry returned by `getdocumentslist` per Q7 architect spec 2026-06-25.
- `WebhookRef` — class — pub
- `derive_tpm_corp_id` — func — pub — Per Q4 architect lock 2026-06-25 -- strip the domain from Projects.TPM

<!-- END:STRUCTURE -->
