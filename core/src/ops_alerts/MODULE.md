# Module: ops_alerts

> **Status:** Ph-1 dev complete 2026-06-26 (initial draft + dev landed same session per architect Q1+Q2+Q3+Q4 locks). Single ingress for HILDA-internal anomaly/failure signals; fans out to ONE email (HILDA OPS BOT alias) + N messenger DMs (recipients.yaml broadcast_corp_ids). Per architect direction `[D-127]` Ratified 2026-06-26: NO per-severity routing (Q1 — all severities go to same recipient set; severity surfaces visually only); rate limit `int|None` per-(source, error_code) (Q2 — default null = pass-through); LOCAL recipients.yaml per `[D-125]` Point 3 (Q3); ALL HILDA modules wire emit_alert at known failure sites (Q4 — issue_tracker / customer_adapter / sharepoint_integration / workflow_engine / rule_engine / tracker / storage / credential_service / llm / dashboard; diagnostics + email_service + messenger excluded per recursion guard Invariant 9). **Ph-1 dev landed**: protocol.py + config.py + recipients_loader.py + rate_limiter.py + composer.py + service.py + mocks.py + __init__.py (~430 lines); 42 tests in `test_ops_alerts.py` all passing; OPS-EXXX prefix registered in diagnostics (5 errors + 2 warnings); sanitized recipients.yaml placeholder + test fixture committed. **Call-site wiring deferred to next session** -- emit_alert call sites in issue_tracker / customer_adapter / sharepoint_integration / etc. are 1-line additions per site (Q4 baseline conservative growth).
>
> **Rollback log:**
> - **2026-06-26 (initial draft)** — captured architect's HILDA OPS alert mechanism design from the close-session evening conversation. Resolved the "HILDA OPS alert mechanism" TODO that had been accumulating across multiple modules (issue_tracker ITR-W004 + FR-87 SP audit writeback silent failures + future signal sites). Per architect: ONE module owns the alert-emit surface; fans out to email + messenger; subject-prefix tag conveys severity (RFC 5322 plain-text subject); HTML body badge for rich rendering. Q4 expanded scope from "ITR-W004 + FR-87 only" → "all failures" — call-site wiring is conservative Ph-1 baseline at known-loud sites (Errors only, not Warnings/Info initially) with growth path Ph-2+.

## Purpose

Single ingress point for HILDA-internal anomaly + failure signals. Modules that detect a failure they cannot recover from (PLM retries exhausted, SP REST writeback failures, IMAP disconnect, browser-binding upload failures, etc.) call `ops_alerts.emit_alert(source, error_code, context, severity)` and forget. ops_alerts owns:

- Composition of the alert payload (subject tag + HTML body badge + plaintext body for messenger DM);
- Per-(source, error_code) rate limiting (config-driven);
- Fan-out to ONE email (HILDA OPS BOT alias) via `email_service` + N messenger DMs (broadcast_corp_ids) via `messenger`;
- Best-effort dispatch — failures in the alert channel itself never propagate back to the signal site (callers cannot have their primary work disrupted by an alert-channel failure).

Per `[D-127]`: this module IS the destination for "operational signals likely to surface" that was an open TODO across 2026-06-25 + 2026-06-26 architect-review sessions.

## Public surface

```python
class Severity(str, Enum):
    INFO     = "info"
    WARNING  = "warning"
    ERROR    = "error"
    CRITICAL = "critical"


@dataclass(frozen=True)
class OpsAlertResult:
    """Per Q1 -- single recipient set; result reports per-channel outcome
    so callers can tell whether the alert physically landed (debug aid only;
    callers should NOT branch on this -- emit_alert is fire-and-forget)."""
    email_sent:           bool                # True = delivered to OPS BOT alias
    messenger_dm_count:   int                 # number of corp_ids the DM landed on
    suppressed_by_rate_limit: bool            # True = rate limit dropped this call
    error_codes:          list[str]           # OPS-EXXX captured during fan-out (best-effort)


class OpsAlerts(Protocol):
    """Fire-and-forget alert emitter. NEVER raises to callers per Invariant 4."""

    async def emit_alert(
        self,
        source: str,                          # e.g. "issue_tracker.ITR-W004"
        error_code: str,                      # e.g. "ITR-W004" or "DSB-W003"
        context: dict[str, Any],              # bounded payload per NFR-2 (no PII)
        severity: Severity = Severity.WARNING,
    ) -> OpsAlertResult: ...

    async def health(self) -> dict[str, Any]:
        """Returns {ready: bool, ops_bot_email: str, broadcast_corp_id_count: int,
        rate_limit_per_minute: int | None}. Best-effort; never raises."""
        ...
```

**Public exported from `core.src.ops_alerts`:** `OpsAlerts`, `Severity`, `OpsAlertResult`, `OpsAlertsService` (concrete impl), `MockOpsAlerts` (test double), `build_ops_alerts(config, email_service, messenger, ...)` (composition root helper).

## Invariants

- **Single recipient set per Q1** — ALL severities route to: (a) ONE email to `ops_bot_email` from recipients.yaml; (b) ONE messenger DM to EACH `corp_id` in `broadcast_corp_ids` from recipients.yaml. No per-severity routing in Ph-1.

- **Subject tag + HTML body badge per Q1** — email subject prefix is `[<SEVERITY>] HILDA: <source> <error_code>` (e.g., `[CRITICAL] HILDA: issue_tracker ITR-W004`); HTML body opens with a severity badge `<span style="background:{color}; ...">` where color is `red` (critical, error), `orange` (warning), `gray` (info). Messenger DM body = email plaintext part WITHOUT color/badge (per architect "no differentiation in corp messenger communication").

- **Rate limit `int | None` per Q2** — `rate_limit_per_minute` in recipients.yaml: `null` = no rate limit (Ph-1 default; pass every call); positive int N = at most N alerts per (source, error_code) tuple per rolling 60-second window. Excess alerts: `OpsAlertResult.suppressed_by_rate_limit=True`, no fan-out. NO summary alert at end of window (Ph-1 simplicity).

- **Recipients.yaml LOCAL per `[D-125]` Point 3 (Q3)** — `customizations/ops_alerts/recipients.yaml` lives on architect's Linux deployment box; public github gets sanitized placeholder + Point 3 header. Format:
  ```yaml
  # customizations/ops_alerts/recipients.yaml (LOCAL ONLY; do not commit to public github)
  ops_bot_email: hilda-ops-bot@mock-corp.com           # single email destination
  broadcast_corp_ids:                                  # N messenger DM destinations
    - y.yikilev
    - a.john
  rate_limit_per_minute: null                          # null = no rate limit (default); int = N
  ```

- **Fire-and-forget per Invariant 4** — `emit_alert` MUST NOT raise to the caller. Any internal failure (recipients.yaml load error, email send failure, messenger send failure) is caught + recorded in `OpsAlertResult.error_codes` + best-effort logged to local NSD ops-log file. The signal site continues unaffected. Justification: alerts must NEVER break the system they monitor.

- **Best-effort fan-out** — if email send fails but 2 of 3 messenger DMs succeed, `OpsAlertResult.email_sent=False` + `messenger_dm_count=2` + `error_codes=["OPS-E001"]`. Caller cannot branch on this; it's a debug aid only.

- **Bounded `context` payload per NFR-2** — caller passes `context` dict; ops_alerts truncates values >200 chars + drops keys not in a whitelist + redacts known credential fields (`password`, `totp_seed`, `totp_code`, `cred.*`). Final HTML body shows context as a key-value table; messenger DM shows it as `key=value; key=value; ...`.

- **NEVER logs credential material** — `password`, `totp_code`, `totp_seed`, `auth_header`, `bearer_token`, `cookie_value` keys are redacted to `<REDACTED>` regardless of severity.

- **NEVER depends on the signal site's module's state** — `emit_alert` accepts plain primitives (str, dict). No DeliveryItemBase / Credential / SpClient references. Keeps the dep graph clean: every HILDA module can call ops_alerts without circular import risk.

- **Messenger recursion guard** — `messenger` module CANNOT call `ops_alerts.emit_alert` for messenger's own send-failures (would create infinite recursion if the alert messenger DM also fails). Messenger logs its failures to local NSD ops-log instead per Invariant 4 fallback. Same applies to `email_service` outbound-email failures: email_service logs locally rather than recursing.

- **OPS-E error code prefix** — `OPS-E001` recipients_yaml_load_failure; `OPS-E002` email_send_failure; `OPS-E003` messenger_dm_fanout_partial; `OPS-E004` messenger_dm_fanout_total; `OPS-E005` context_payload_too_large; `OPS-W001` rate_limit_suppressed; `OPS-W002` credential_field_redacted. Registered in `diagnostics/error_codes.py` `PREFIX_REGISTRY["OPS"] = "ops_alerts"`.

- **No per-source recipient overrides Ph-1** — every alert goes to the same recipients. Ph-2 forward-looking: per-(source, severity) routing tables. Captured in Deferred.

## Key choices

- **Single module owning alert fan-out** over **helper function in diagnostics / email_service / messenger** — diagnostics is a foundation that every module depends on (would invert dep graph); email_service + messenger are channel-owners (asymmetric fit for fan-out). New module isolates the fan-out logic + recipients config + rate limiter cleanly. ~150 lines justifies the boundary.

- **Same recipient set for all severities (Q1)** over **severity-tiered routing** — keeps Ph-1 simple + matches architect's actual ops surface (one BOT email, one broadcast list). Severity-driven routing IS captured in Deferred for Ph-2 if ops surface grows (e.g., dedicated on-call corp_id list for critical-only).

- **Subject tag `[CRITICAL]`/`[ERROR]`/...` + HTML body badge** over **subject-color** — RFC 5322 email subjects are plain text; `[<SEVERITY>]` prefix is universal across all mail clients (Outlook / Gmail / Mac Mail / terminal mutt all render plain subject identically). Subject tag enables inbox-scan + filter rules; HTML body badge enables visual severity recognition on open. Architect approved 2026-06-26 dual pattern.

- **Per-(source, error_code) rate limit window** over **per-source** OR **per-(source, error_code, severity)** — granularity matters: a 50× burst of `ITR-W004` is rate-limit-worthy; but if the same source emits a separate `ITR-E002` simultaneously, both signals are operationally distinct + both should land. Per-severity adds noise + matches no real operational need (severity escalation of same error_code is rare).

- **Rolling 60-second window over fixed 1-min buckets** — slightly more complex impl (need deque of timestamps per (source, error_code)) but avoids the "59 alerts in 1 bucket boundary second" pathology. Default null skips this entirely.

- **Fire-and-forget API surface** over **raises-on-failure** — alerts must NEVER break the system they monitor (Invariant 4). `OpsAlertResult` is a debug aid; callers should NOT branch on it.

- **`emit_alert` accepts plain primitives** over **typed payload objects** — keeps every caller's import surface tiny (no DeliveryItemBase / Credential references); avoids circular-dep risk; bounded `context` dict per NFR-2 also prevents callers from accidentally dumping large objects.

- **Messenger recursion guard at the module level** over **runtime ops_alerts.emit_alert detection** — design-time guarantee that messenger never imports ops_alerts (enforced via dependency review). Same for email_service. Simpler than runtime detection + provably no-recursion.

- **LOCAL recipients.yaml** (Q3 + `[D-125]` Point 3) over **public github commit** — contains real corp_ids (`y.yikilev`, `a.john` style); architect maintains on Linux deployment box; public github gets sanitized placeholder showing format.

- **OPS-E prefix in diagnostics** over **DSB / IMS / shared prefix** — distinct namespace for "the alert channel itself failed"; distinguishes from the alert's PAYLOAD (which carries the source-module's own error code).

- **Sync recipients.yaml load at module construction** over **per-alert reload** — recipients are stable across deployment lifetime; load once at `build_ops_alerts(...)` composition root; SIGHUP-triggered reload deferred Ph-2.

## Non-goals

- **NOT a general logging facility** — HILDA's normal logging goes through `diagnostics` + Python stdlib `logging`. ops_alerts is for SIGNAL-WORTHY events (failures the operator needs to act on), not routine telemetry.

- **NOT an incident management system** — no ticketing, no escalation chains, no acknowledge workflow. If/when HILDA grows into Ph-3+ enterprise ops, integrate with PagerDuty / ServiceNow (Deferred).

- **NOT a customer-facing alert channel** — TPM-facing reminders go through `email_service` directly (FR-9, FR-11). Owner-facing escalations go through `messenger` directly (Module #20). ops_alerts is HILDA-team-facing only.

- **NOT a metrics aggregator** — no counts, no dashboards, no Prometheus exposition. Metrics belong in `diagnostics` reports.

- **NOT integrated with Slack / PagerDuty / SMS Ph-1** — single email + corp messenger DMs only. Other channels deferred.

- **NOT a deduplication engine beyond per-minute rate limit** — fancy fingerprint-based dedup (collapse semantically-equivalent alerts into one) is Ph-2+.

## Depends on

- `core.src.email_service` — outbound email composition + dispatch (one email per alert to `ops_bot_email`). HTML multipart with text/plain + text/html bodies.
- `core.src.messenger` (Module #20) — outbound messenger DM dispatch (N DMs per alert). Same body content as email plaintext.
- `core.src.credential_service` — sops-encrypted SMTP creds for the OPS BOT email account (per `[D-019]` shared HILDA ops-team identity); messenger uses its own bot identity per Module #20.
- `core.src.diagnostics` — `ERROR_CODES` registry + `PipelineError` (for context bounding + redaction); `PREFIX_REGISTRY["OPS"] = "ops_alerts"` registration.
- `customizations/ops_alerts/recipients.yaml` — LOCAL per `[D-125]` Point 3; loaded at composition-root time.

## Depended on by

- `core.src.issue_tracker` — `ITR-W004` (PLM N-retries-exhausted per Q5 lock 2026-06-25); other ITR-EXXX failures Ph-1.
- `core.src.customer_adapter` — `CAD-EXXX` upload failures (CAD-E004 binding_failure, CAD-E005 post_verify_failed, CAD-E008 cred_unavailable, CAD-E009 binding_not_implemented, CAD-E010 customer_delivery_info_missing).
- `core.src.sharepoint_integration` — `SHP-EXXX` write/auth failures (SHP-E001 list_op_failure, SHP-E004 ntlm_failure).
- `core.src.workflow_engine` — `WFE-EXXX` task body failures (retry-exhausted).
- `core.src.rule_engine` — rule evaluation failures (RUL-EXXX).
- `core.src.tracker` — state transition failures (TRK-EXXX).
- `core.src.storage` — NSD failures (STG-EXXX).
- `core.src.llm` — classifier failures (LLM-EXXX).
- `core.src.dashboard` — FR-87 SP audit writeback silent failures (best-effort try/except per `[D-064]` + `[D-117]` secondary channel).

**Excluded (per Invariant 9 — recursion guard):**
- `core.src.email_service` (cannot alert about email failures via email)
- `core.src.messenger` (cannot alert about messenger failures via messenger)
- `core.src.diagnostics` (foundation; never depends outward)

## Sub-modules (Ph-1)

- `protocol.py` — `OpsAlerts` Protocol + `Severity` enum + `OpsAlertResult` dataclass.
- `service.py` — `OpsAlertsService` concrete impl (composition root accepts email_service, messenger, credential_service, rate_limiter, composer, recipients).
- `composer.py` — subject tag prefix + HTML body badge + plaintext body composition; context payload bounding + redaction per NFR-2.
- `rate_limiter.py` — per-(source, error_code) rolling 60-second window; deque-of-timestamps; `null` config = pass-through.
- `recipients_loader.py` — load + validate `customizations/ops_alerts/recipients.yaml` at composition-root time; surface `OPS-E001` on load failure.
- `mock_ops_alerts.py` — test double; captures all `emit_alert(...)` calls in `self.calls` for assertion.
- `config.py` — `OpsAlertsConfig` dataclass (paths to recipients.yaml + OPS BOT smtp config).

**Ph-1 size estimate**: ~150 lines core impl + ~25 lines mock + ~120 lines tests.

## Deferred (Ph-2+ forward-looking)

- **Severity-driven routing** — separate `escalation_corp_ids` list for `severity=critical` only; on-call rotation; per-severity broadcast lists.
- **Per-source recipient overrides** — `customer_adapter` failures → carrier-ops team; `sharepoint_integration` failures → SP UI engineer team; etc.
- **Fingerprint-based deduplication** — collapse semantically-equivalent alerts (same source + same error_code + same first-3-context-keys hash) into a summary alert.
- **Summary alert at rate-limit window end** — when N alerts/min suppressed, emit one summary alert "ITR-W004 fired 47× in last hour (47 suppressed)".
- **Slack / PagerDuty / SMS channels** — pluggable channel architecture; channel-per-severity routing.
- **SIGHUP-triggered recipients.yaml reload** — pick up new corp_ids without HILDA restart.
- **Acknowledge / mute workflow** — ops engineer marks an alert "acknowledged" to suppress further alerts for a window.
- **Alert correlation** — group related alerts (same workflow run, same milestone) into a single notification.
- **Metrics surface** — count + last-fire-timestamp per (source, error_code) for `--diagnostic` reports.

## Anchors

`[D-019]` (shared HILDA ops-team identity Ph-1/Ph-2 — same SMTP + messenger account for OPS BOT), `[D-027]` (Teacher/Student split — recipients.yaml LOCAL), `[D-064]` (HILDA → SP REST writeback secondary channel — failed writebacks emit via ops_alerts), `[D-117]` (SpSession NTLM digest dance — failed digests emit via ops_alerts), `[D-122]` (FR-87 direct POST — SP audit writeback silent failures emit via ops_alerts), `[D-125]` (Point 3 policy — recipients.yaml LOCAL), `[D-127]` (this module — Ratified 2026-06-26).

**Issue tracker context**: ITR-W004 PLM retries exhausted (per issue_tracker Q5 lock 2026-06-25); FR-87 SP audit writeback silent failures (per dashboard cascade 2026-06-26).

**Test fixture**: `core/tests/fixtures/ops_alerts/test_recipients.yaml` — placeholder ops_bot_email + 2 broadcast_corp_ids + `rate_limit_per_minute: null`; safe for public github (no corp data).

## Structure

<!-- BEGIN:STRUCTURE -->

- `ComposedAlert` — class — pub — Output of compose_alert -- ready for email + messenger dispatch.
- `MockOpsAlerts` — class — pub — Test double -- captures + replays.
- `OpsAlertResult` — class — pub — Per [D-127] -- result reports per-channel outcome (debug aid only).
- `OpsAlerts` — class — pub — Fire-and-forget alert emitter per [D-127] Invariant 4.
- `OpsAlertsConfig` — class — pub — Composition-root config.
- `OpsAlertsService` — class — pub — Fire-and-forget alert emitter. Constructed via build_ops_alerts.
- `RateLimiter` — class — pub — In-memory rolling-window per-(source, error_code) limiter.
- `Recipients` — class — pub — Parsed + validated recipients.yaml shape.
- `Severity` — class — pub — Per [D-127] Q1 lock 2026-06-26 -- visual-only differentiation.
- `build_ops_alerts` — func — pub — Composition-root helper -- standard wire-up.
- `compose_alert` — func — pub — Compose an alert payload. Pure function -- no I/O.
- `load_recipients` — func — pub — Load + validate recipients.yaml; raises PipelineError(OPS-E001) on failure.

<!-- END:STRUCTURE -->
