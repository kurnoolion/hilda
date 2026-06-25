# Module: messenger

> **Status:** Ph-1 dev complete 2026-06-25 (separate from the 13-module strict-order sweep). Greenfield module owning the corp messenger channel for FR-10 cross-channel escalation per architect direction 2026-06-25 (Q-M1..Q-M6 lock). Anchors `[D-027]` Teacher/Student split (HILDA-side base + per-customer subclass at `customizations/messenger/`); `[D-019]` credential_service ONLY if the binding requires HILDA-side credential injection (typically not -- the binding owns its own auth); FR-10 cross-channel escalation; FR-42 CommunicationLog audit trail; FR-31 AutomationRules trigger via `ESCALATE` ActionKind (workflow_engine dispatch); NFR-2 no proprietary content in compact reports. **TODO(D-121 candidate)**: ratify the architect locks (Q-M1..Q-M6) as a formal D-XXX entry on next close-session. Sections curated; code lands per "## Sub-modules" + "## Public surface" below.

**Purpose**: Single Protocol-mediated surface (`MessengerAdapter`) for HILDA's outbound corp messenger channel -- send one escalation message to one owner per rule-engine trigger + emit `CommunicationLog` row per FR-42 + enforce daily-limit + return `SendResult` with success bool + sent_at + bytes + error_code (None on success).

The single API HILDA wraps (per architect Q-M1 lock 2026-06-25):

```
bool sendMessage(owner_corp_id, message) -> True when owner RECEIVES the message;
                                            False on failure
```

**Workload assignment**: `hilda-worker` Celery task pool per `[D-021]` -- escalation tasks are background, sub-second per send (gateway call + audit-log write). `hilda-api` reads escalation history from `CommunicationLog` for SP UI dashboards but does not invoke the binding directly. **No HILDA-side session pool** -- each send is a per-call gateway invocation through the binding.

**Per-send latency (Ph-1 reality)**: ~100ms-2s per call via the corp messenger gateway binding (mostly network round-trip). Per-owner concurrency: HILDA enforces the 3-per-day cap at the messenger module layer (Q-M2 lock); inter-owner sends parallelize freely.

---

## Sub-modules (Ph-1)

```
core/src/messenger/
  __init__.py                          # clean public exports
  protocol.py                          # MessengerAdapter Protocol + EscalationReason enum + SendResult dataclass
  config.py                            # MessengerConfig (3-tier per [D-025] + [D-038])
  composer.py                          # compose_escalation(...) -> ComposeResult; Jinja2 escalation.j2 render
  daily_limit.py                       # DailyLimitChecker -- Q-M2 3-per-day enforcement + Q-M5 audit write
  service.py                           # MessengerService -- top-level orchestrator (workflow_engine ESCALATE entry)
  mocks.py                             # MockMessengerAdapter + InMemoryMessengerStorage (tests + --mock CLI)
  utils.py                             # validate_message_length / truncate_message / redact_owner_corp_id
  messenger_cli.py                     # --diagnostic / --mock / --send modes
  corp_messenger/
    __init__.py
    adapter.py                         # CorpMessengerAdapter -- HILDA-side thin wrapper base class
  templates/
    escalation.j2                      # Jinja2 escalation template (anonymous HILDA BOT signature per Q-M3)
  MODULE.md                            # this file

customizations/messenger/
  __init__.py
  example_corp_messenger_adapter.py    # per-customer subclass scaffold; # TODO(cline) markers per [D-027]
```

---

## Public surface

### `protocol.py`

```python
class EscalationReason(str, Enum):
    POST_REMINDER     = "post_reminder"        # FR-10 after N email reminders
    DEADLINE_IMMINENT = "deadline_imminent"    # <3 days to deadline + still open
    MANUAL_TPM        = "manual_tpm"           # TPM-triggered ad hoc (Ph-2)

@dataclass(frozen=True)
class SendResult:
    success:       bool                       # True = owner received per Q-M1
    owner_corp_id: str
    message_bytes: int
    error_code:    str | None                 # MSG-E001..MSG-E003 / MSG-W001 / None
    sent_at:       datetime

@runtime_checkable
class MessengerAdapter(Protocol):
    source_system: str
    async def send(self, owner_corp_id: str, message: str) -> bool: ...
```

### `service.py`

```python
class MessengerService:
    """workflow_engine ESCALATE ActionKind task body calls send_escalation."""
    async def send_escalation(
        self,
        item:          Any,                    # DeliveryItemBase-shaped
        batch_id:      str,
        reason:        EscalationReason,
        milestone_ctx: dict[str, Any],         # owner_name + milestone_name + device_id + deadline_date + corp_plm_link + reminder_count
    ) -> SendResult: ...
```

### `corp_messenger/adapter.py`

```python
class CorpMessengerAdapter:
    """Per-customer subclass at customizations/messenger/<customer_id>_corp_messenger_adapter.py."""
    source_system: str = "corp_messenger"
    async def send(self, owner_corp_id: str, message: str) -> bool: ...
    async def _invoke_send_message(self, owner_corp_id: str, message: str) -> bool:
        raise NotImplementedError("MSG-E003 ...")
```

---

## Invariants

- **4K byte cap per message (Q-M2)** -- `compose_escalation` truncates with `... [truncated]` marker + flags via MSG-W002 if the rendered body exceeds `config.max_message_bytes` (default 4000). The truncated message still sends.
- **3 messages per owner_corp_id per UTC calendar day (Q-M2)** -- `DailyLimitChecker.can_send` pre-checks via CommunicationLog query; 4th attempt blocked + MSG-W001 emitted + blocked-attempt audit row written + `SendResult(success=False, error_code='MSG-W001')` returned. Adapter NOT invoked when blocked.
- **Anonymous HILDA BOT attribution (Q-M3)** -- composed message body ends with `config.bot_signature` (default "-- HILDA BOT"). No TPM-identity preamble like "Hi, this is HILDA on behalf of TPM X". CommunicationLog row's `sender` field is None for the same reason. Privacy + scope-of-impact considerations both addressed.
- **Bool-only outcome from binding, no message_id Ph-1 (Q-M5)** -- `external_message_id` field on the CommunicationLogRow is None Ph-1. Thread-continuity via reply-to-this-message-id is a Ph-2 forward-looking feature; FR-54 inbound replies via messenger DROPPED per architect direction 2026-06-19.
- **Messenger OWNS composition, not email_service (Q-M6)** -- the Ph-1 stub at `core/src/email_service/outbound/composer_escalation.py` is vestigial / should be removed in a follow-up sweep. messenger's `compose_escalation` is the canonical Ph-1 path.
- **Retry semantics** -- on adapter `send` returning False (transport failure / gateway-rejected), `MessengerService` retries up to `config.retry_attempts` times with `config.retry_backoff_seconds` sleep between attempts; emits MSG-W003 per retry. After all retries exhausted: SendResult(success=False, error_code='MSG-E001') + one audit row recording the final failure.
- **NFR-2 redaction discipline** -- `owner_corp_id` is the raw value in the audit-storage `recipients` field (for ops triage); compact reports + log lines use `redact_owner_corp_id(owner_corp_id)` -> "o***". Message body content NEVER appears in summary / RPT / log lines -- only bounded enum tokens (channel / reason / size bucket / success bool) + size bytes.
- **CommunicationLog write per FR-42 + Q-M5** -- every `send_escalation` call appends one CommunicationLog row with channel=`Messenger`, direction=`Outbound`, action_type=`messenger_escalation`, attachments=[{batch_id}]. Records intent + outcome (success / blocked-by-limit / composer-fail / adapter-fail). Non-blocking; survives any send failure.
- **Per-call binding invocation, no HILDA-side session pool (Q-M1 + [D-027])** -- HILDA does NOT cache binding state. Binding internally caches its own gateway session if it chooses; per `[D-027]` that's the proprietary subclass body's concern.
- **Per-customer subclass pattern at `customizations/`** -- each per-customer subclass at `customizations/messenger/<customer_id>_corp_messenger_adapter.py` provides the binding-import line + `_invoke_send_message` body. Aligns with `[D-003]` adapter pattern + `[D-027]` Teacher/Student split. NEVER lands on public github per NFR-2.
- **Async-native** per `[D-008]` pattern -- `MessengerAdapter.send` is `async def`; sync binding internals wrapped in `asyncio.to_thread` per `structure-conventions.md` Sync-API wrapping convention.

---

## Error codes (MSG prefix -- registered in `diagnostics/error_codes.py`)

```
MSG-E001  sendMessage failed: {reason_token}
MSG-E002  Composer template rendering failed for owner '{owner_id}'
MSG-E003  CorpMessengerAdapter binding not configured for deployment (subclass missing or stub)
MSG-W001  Daily limit reached ({daily_limit_per_owner}/day) for owner_corp_id='{redacted}'
MSG-W002  Message truncated: rendered '{n}' bytes exceeds max_message_bytes='{max}'
MSG-W003  Retry attempt {attempt}/{max_attempts} for owner_corp_id='{redacted}'
```

---

## Key choices

- **Architect Q-M1..Q-M6 lock 2026-06-25** -- the messenger module's Ph-1 surface is fully driven by the six-question architect lock-in (delivery semantics / constraints / attribution / trigger / audit / routing). TODO(D-121 candidate): formalize as a ratified D-XXX decision on next close-session.
- **`[D-027]` Teacher/Student thin-wrapper strategy** -- HILDA-side `CorpMessengerAdapter` is a Protocol-conformant thin wrapper around the corp messenger gateway binding. HILDA owns Protocol contract + CommunicationLog discipline + daily-limit check + retry. Binding owns the actual `sendMessage(owner_corp_id, message) -> bool` call body. Per-customer subclass at `customizations/messenger/<customer_id>_corp_messenger_adapter.py` fills the binding import + `_invoke_send_message` body. Mirrors `customer_adapter` `GoogleDriveBaseAdapter` + `issue_tracker.corp_plm.CorpPlmAdapterBase` patterns.
- **Composition lives in messenger, not email_service (Q-M6)** -- escalation template + Jinja2 rendering moves under `core/src/messenger/templates/` + `composer.py`. The email_service Ph-1 stub at `outbound/composer_escalation.py` is vestigial; flag for follow-up removal.
- **3-per-day cap enforced at messenger module level, not at the gateway** -- HILDA pre-checks via CommunicationLog query before invoking the gateway. Avoids a wasted RPC + gives HILDA full audit of blocked attempts. Adopts the same UTC-calendar-day window as FR-42 timestamp normalization.
- **4K byte cap enforced via truncate-with-marker (not hard reject)** -- truncated messages still send; ops surface the warning via MSG-W002 + size-bucket in CommunicationLog summary. Rationale: a truncated escalation is still useful to the owner; a rejected one wastes the daily-limit quota.

---

## Non-goals

- **Not the email channel.** Email-side outreach + reminder live in `email_service.outbound.composer_outreach` + `composer_reminder` + `SmtpSender`. Messenger is the cross-channel escalation path per FR-10, not a general-purpose outbound text-message library.
- **Not the issue tracker.** PLM-side messaging (corp PLM comments / status updates) lives in `issue_tracker.corp_plm`. Messenger is a separate channel for owner-direct messaging.
- **Not a credential store.** The corp messenger gateway binding owns its own auth (typically gateway-side service account); HILDA does NOT inject credentials via `credential_service` Ph-1. If a future per-customer subclass requires it, `[D-019]` `get_credential(pm_id, SystemType.MESSENGER, customer_id=...)` slot is available.
- **Not a message_id thread tracker.** Per Q-M5 Ph-1: bool-only outcome; `external_message_id` is None. Thread continuity via reply-to-message-id is Ph-2 forward-looking.
- **Not the FR-31 rule engine.** Rule-driven escalation triggers (e.g., "after 2 reminders + <=3 days to deadline") are evaluated by `rule_engine` per FR-31; messenger's `send_escalation` is the terminal task body that `workflow_engine.ESCALATE` ActionKind dispatches.
- **Not a TPM identity-disclosure surface.** Per Q-M3: anonymous HILDA BOT attribution. Owner-facing message body NEVER reveals the dispatching TPM's identity.

---

## Depends on

- `diagnostics` -- `ErrorCode`, `ReportWriter`, `PipelineError`. MSG codes registered in `error_codes.py`.
- `storage` -- `CommunicationLogRow` + `Channel.MESSENGER` + `Direction.OUTBOUND` enums + `log_communication(...)` + `query_communications(...)` for FR-42 audit trail + Q-M2 daily-limit precheck.
- `Jinja2` (already a project dependency via email_service) -- escalation.j2 template rendering.
- **Corp messenger gateway binding** (`customizations/messenger/<customer_id>_corp_messenger_adapter.py` import target) -- pre-existing module providing `sendMessage(owner_corp_id, message) -> bool` per architect spec 2026-06-25. Filled in by Cline on Work PC; NEVER imported in HILDA `core/`.

---

## Depended on by

- `workflow_engine` -- fires `ESCALATE` ActionKind which dispatches to `messenger.MessengerService.send_escalation(item, batch_id, reason, milestone_ctx)`. Per architect Q-M4 lock 2026-06-25: rule_engine evaluates FR-31 AutomationRules -> workflow_engine dispatches ESCALATE -> task body calls into messenger. The workflow_engine ESCALATE task binding is stub-pending until this module lands (similar pattern to customer_adapter QUEUE_SUBMISSION binding).
- `dashboard` -- surfaces messenger escalation history from `CommunicationLog` rows written by this module (read-only). Renders escalation count + last-sent timestamp in the per-item dashboard section.
- `rule_engine` -- consumes `messenger_escalation` rows from `CommunicationLog` to compute `reminder_count` / `escalation_count` for rule conditions (e.g., "stop escalating after 3 messenger-sends today" -- already enforced at messenger module level, but rule_engine reads the count too).

---

## Deferred

- **Ph-2: message_id thread continuity** -- per Q-M5 lock 2026-06-25, Ph-1 bool-only outcome means no `external_message_id` on CommunicationLog rows. Ph-2 may extend the gateway binding API to return a message_id + re-use it for `replyTo` chained sends; revisit when owner-side reply discovery is requested.
- **Ph-2: Per-PM messenger identity vs anonymous (Q-M3)** -- per architect lock 2026-06-25, all messages come from "anonymous HILDA BOT". Ph-2 may revisit if TPMs want per-PM attribution for accountability / scope-of-impact triage; requires gateway binding API extension.
- **Ph-3+: Inbound replies via FR-54** -- DROPPED per architect direction 2026-06-19. Messenger is outbound-only Ph-1/Ph-2; if FR-54 inbound is revisited, this module would gain a `receive` Protocol method + a Celery poller analogous to `email_service.ImapReceiver` / `issue_tracker.corp_plm.CorpPlmPoller`.
- **Ph-3+: Multi-customer messenger gateway routing** -- if multiple corp messenger gateways need to coexist (e.g., per-customer escalation channels), a `MessengerRouter` peer to `MessengerService` selects the per-customer subclass. Ph-1 assumes one gateway per HILDA deployment.

---

## Test interface

```
python -m core.src.messenger.messenger_cli --diagnostic
```
Validates config + template loading + base adapter availability. Emits one MSG-RPT:
```
RPT|MSG|run-abc12345|2026-06-25T10:00:00Z|mode=diagnostic|daily_limit_per_owner=3|max_message_bytes=4000|retry_attempts=3|templates_dir_exists=true|template_loaded=true|template_error=none|required_vars_count=9|base_adapter_available=true
```

```
python -m core.src.messenger.messenger_cli --mock
```
Spins up `MockMessengerAdapter` + `InMemoryMessengerStorage`; runs one synthetic `send_escalation` end-to-end (composer + daily-limit + audit). Emits one MSG-MET:
```
MET|MSG|run-abc12345|2026-06-25T10:00:00Z|mode=mock|owner_redacted=t***|success=true|message_bytes=312|error_code=none|audit_rows_written=1|adapter_calls=1
```

```
python -m core.src.messenger.messenger_cli --send --owner <id> --message <text>
```
Real send harness -- requires a per-customer subclass at `customizations/messenger/`. Without one, surfaces MSG-E003 cleanly. Per NFR-2: `owner_corp_id` is redacted in the emitted MSG-MET (full value flows only through the binding call frame).

**QC template** (`MSG:escalation_quality` -- registered in `diagnostics/qc.py` once dev lands):
```
Fields: reason (enum: post_reminder | deadline_imminent | manual_tpm),
        size_bucket (enum: xs | s | m | l), truncated (bool), success (bool),
        attempt_count (int 1..config.retry_attempts), daily_count_at_send (int 0..3),
        result (enum: OK / WARN / FAIL -- FAIL when success=false AND error_code!=MSG-W001;
        WARN when truncated=true OR daily_count_at_send=3)
```

---

<!-- BEGIN:STRUCTURE -->

- `ComposeResult` — class — pub — Composition output -- rendered message + size accounting + truncation flag.
- `DailyLimitChecker` — class — pub — Pre-check + audit-write helper for the Q-M2 daily-limit invariant.
- `EscalationReason` — class — pub — Why an escalation message is being sent. Bounded enum tokens per NFR-2.
- `InMemoryMessengerStorage` — class — pub — Minimal MessengerStorageProtocol impl for tests + --mock CLI.
- `MessengerAdapter` — class — pub — All callers depend on this Protocol, not on a concrete subclass.
- `MessengerConfig` — class — pub — Messenger module config per Q-M2 / Q-M3 architect lock 2026-06-25.
- `MessengerService` — class — pub — Composes + checks daily-limit + invokes adapter + logs. Caller-facing.
- `MessengerStorageProtocol` — class — pub — Subset of `storage` audit-log interface this module depends on.
- `MockMessengerAdapter` — class — pub — In-memory MessengerAdapter -- returns canned bool responses.
- `SendResult` — class — pub — Result shape from MessengerService.send_escalation.
- `compose_escalation` — func — pub — Render escalation.j2 and enforce the max_message_bytes invariant.
- `get_template_env` — func — pub — Jinja2 Environment loading from messenger/templates/ by default.
- `redact_owner_corp_id` — func — pub — Per NFR-2 -- mask owner_corp_id for compact reports + log lines.
- `truncate_message` — func — pub — Truncate UTF-8 message to fit within `max_bytes`, appending TRUNCATION_MARKER.
- `validate_message_length` — func — pub — Return (within_limit, byte_count) -- UTF-8 byte size, not char count.

<!-- END:STRUCTURE -->
