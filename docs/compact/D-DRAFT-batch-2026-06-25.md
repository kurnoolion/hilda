# D-DRAFT batch 2026-06-25 -- PENDING ITEMS

> 21 ADRs (D-094..D-111, D-113, D-114, D-115) appended to DECISIONS.md 2026-06-25 after architect review. Two ADRs RETAINED here pending architect resolution:
>
> - **D-112** -- code drift surfaced (pause_state.py still defines PauseStateLookup Protocol contrary to STATUS Done entry). Architect must decide: (a) clean up code per Done entry; (b) revise ADR to reflect Protocol-survives-as-read-interface; (c) backward-compat retention for tests.
> - **D-116** -- 2 TODO fields (user-binding signature shape + selector-pack-versioning + session-pool ownership applicability) -- only the architect knows the existing Google Drive code shape.

---

## D-112: `PauseStateLookup` Protocol DROPPED — replaced by `rules_paused` SP column read

**Date**: 2026-06-23
**Status**: Ratified

**Context**: `PauseStateLookup` Protocol was specified during earlier rule_engine + storage MODULE.md design as the abstraction layer for FR-31 sub-1 per-item pause-state reads. The 2026-06-23 rule_engine arch revisit (with `[D-108]` `rules_paused` SP-column mechanism) eliminated the need for the Protocol entirely — item snapshot already flows through the dispatcher and carries the boolean.

**Decision**: `PauseStateLookup` Protocol is DROPPED. Replaced by direct read of `item_snapshot.rules_paused` (per `[D-108]`). `rule_engine.RuleEngine` constructor no longer takes a `pause_lookup` parameter. `rule_engine.evaluate(event, item_snapshot=...)` reads `item_snapshot.rules_paused` directly. Item-less events pass `item_snapshot=None` and skip the pause check entirely.

**Why**:
- (a) Adding the Protocol abstraction on top of the new `rules_paused` SP column would be ceremony without value — the item snapshot already carries the field.
- (b) Simplifies `RuleEngine` construction (1-arg constructor vs 2-arg with lookup).
- (c) Eliminates `NoPauseState` no-op implementation + InMemoryOverrideStore-style fixtures.
- (d) Aligns with `[D-113]` TriggerDispatcher item_snapshot flow.

**Consequences**:
- (a) `rule_engine.evaluator.RuleEngine` constructor: `pause_lookup` parameter removed; new `item_snapshot` kwarg on `evaluate()` + `explain()` (commit `b6c13a6`).
- (b) `rule_engine/__init__.py`: `PauseStateLookup` + `NoPauseState` dropped from `__all__` (still importable from sub-modules for Ph-2 dev) (commit `b6c13a6`).
- (c) `workflow_engine.TriggerDispatcher.__init__` drops `pause_lookup` param (commit `11f5e5d`, `1e7e8a0` D2).
- (d) Tests: `test_paused_item_flagged_not_dropped` rewritten to pass `item_snapshot=SimpleNamespace(rules_paused=True)`; `test_milestone_pause_is_ph2_deferred` verifies item-less events skip pause check (commit `b6c13a6`).
- (e) `diagnostics_cli.py`: `NoPauseState` import dropped; `RuleEngine()` 1-arg constructor (commit `b6c13a6`).

**Anchors**: FR-31 sub-1, `[D-108]` (`rules_paused` SP column origin), `[D-113]` (TriggerDispatcher item_snapshot flow; sibling decision), `rule_engine/MODULE.md` D5, commits `20aa181` / `b6c13a6` / `11f5e5d`.

---

## D-116: customer_adapter thin-wrapper strategy — Protocol contract + thin wrapper + user-owned binding per Teacher/Student split

**Date**: 2026-06-25
**Status**: Provisional — bulk-captured 2026-06-25; rationale captured fresh from this session's discussion

**Context**: Original `[D-054]` 2026-06-05 implementation note positioned HILDA as owner of the full Google Drive selenium / playwright headless Chromium stack for FR-19 customer-delivery upload — selenium amendment + `GoogleDriveBaseAdapter` base class + selector versioning + Chromium operational dependency + PM session management + capability flags all in HILDA scope. During the 2026-06-24 customer_adapter arch revisit (commit `a833b85`), the architect surfaced a different implementation strategy: a pre-existing Google Drive API binding (developed independently by the user on Work PC) can serve as the actual upload mechanism; HILDA wraps it thinly to enforce the Protocol contract + audit discipline.

**Decision**: HILDA's `GoogleDriveBaseAdapter` is a **Protocol-conformant thin wrapper** around the user's pre-existing Google Drive API binding. Ownership split:
- **HILDA owns**: (a) `CustomerAdapter` Protocol contract; (b) `CarrierUploadResult` shape; (c) `CommunicationLog` discipline per FR-42; (d) `CarrierCapabilityFlags` per-customer surface; (e) selector pack versioning + session pool management.
- **User-provided binding owns**: actual `upload(file_bytes, target_folder) → (file_id, file_url)` call.

Per `[D-027]` Teacher/Student split: HILDA-side Protocol scaffold + thin wrapper authored on Personal PC (Claude); concrete API call body filled in by Cline on Work PC using the user's existing implementation. No proprietary API details land on public GitHub per NFR-2.

**Why**:
- (a) **Avoids duplication** — user already has a working Google Drive API binding; rewriting it in HILDA would be redundant.
- (b) **Air-gap discipline preserved** — proprietary API binding details stay on Work PC; HILDA's public scaffold carries only the Protocol contract.
- (c) **Faster Ph-1 delivery** — thin wrapper is a small module; full selenium/playwright stack from scratch would be significant operational dev work.
- (d) **Discipline boundaries preserved** — HILDA still owns the FR-42 `CommunicationLog` audit discipline + `CarrierCapabilityFlags` per-customer surface + selector pack versioning. The user's binding is treated as an external dependency that conforms to a HILDA-defined adapter signature.
- (e) **Reverses `[D-054]` 2026-06-05 impl note** — the selenium/headless-Chromium stack ownership claim is no longer Ph-1 scope; the wrapper pattern replaces it.

**Consequences**:
- (a) `customer_adapter/MODULE.md` D11 cascade documents thin-wrapper strategy (commit `a833b85` D11).
- (b) Ph-1 dev plan for customer_adapter: build `protocol.py` (CustomerAdapter Protocol + CarrierUploadResult + CarrierCapabilityFlags), `google_drive_base.py` (thin-wrapper reference class), `session_manager.py`, `selector_loader.py`, `capability_flags.py`, `MockCustomerAdapter`, `diagnostics_cli.py`, tests. Concrete API call body filled in by Cline on Work PC.
- (c) `[D-054]` selenium / headless-Chromium / Chromium-operational-dependency claims SUPERSEDED for Ph-1; Ph-2+ may revisit if a non-binding-backed customer modality (web portal scraping) is needed.
- (d) workflow_engine `tasks/submission.py QUEUE_SUBMISSION` task body remains stub-pending until customer_adapter dev complete (commit `96a498f`).
- (e) NFR-2 (no proprietary content on public github) preserved via `[D-027]` boundary.
- **TODO**: confirm the user-provided binding's signature shape matches `CustomerAdapter.upload_attachment(file_bytes, target_folder) → CarrierUploadResult` — checked: customer_adapter D11 cascade text references the signature abstractly; concrete signature confirmation pending Cline's Work PC integration pass.
- **TODO**: selector-pack-versioning + session-pool-management ownership lines need confirmation that they apply to the thin-wrapper pattern (originally specified for the full selenium stack per `[D-054]`) — checked: customer_adapter D11 lists them as HILDA-owned but their applicability when the actual upload runs through a user-binding (potentially API-based, not Selenium-based) is not explicitly resolved.

**Anchors**: FR-19, FR-42, FR-77, NFR-2, `[D-027]` (Teacher/Student LLM scaffold split — load-bearing for this ownership boundary), `[D-054]` (selenium amendment; this ADR partially reverses the 2026-06-05 impl note for Ph-1), `customer_adapter/MODULE.md` D11, commit `a833b85`.

---
