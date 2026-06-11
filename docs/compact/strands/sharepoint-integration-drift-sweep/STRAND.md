# sharepoint-integration-drift-sweep

**Status:** in-flight
**Opened:** 2026-06-10
**Landed:**
**Assignees:** architect (user — strand 1)
**Target modules:** core/src/sharepoint_integration/
**Active phase:** development

## Summary

sharepoint_integration Ph-1 drift sweep — May 2026 implementation reconciled against the post-2026-06-08 cascade model + today's [D-064]/[D-065]/[D-066] additions. The module's curated MODULE.md sections were heavily updated in session 1 (drift-sweep commit 54819a8 morning of 2026-06-10): 8-list canonical entity set per [D-051]; SpCrud.delete_item added to Public surface; SharePointListProvider.from_sp_fields added to Protocol; mock_server/ sub-module documented; FR-84 outbound writeback invariant; FR-87 TPM resolution writeback path invariant (strict A→B→C ordering); column-map append-only invariant for 2026-06-08 cascade fields; SP Choice-field value sync added to Non-goals (SP UI engineer owns Choice values per [D-065]); SP-alert email channel added to Non-goals; Depended-on-by extended (issue_tracker, customizations/issue_tracker, indirect customer_adapter); two-halves-of-the-same-conversation positioning note.

THIS STRAND verifies + reconciles the implementation code against those MODULE.md updates. Scope:
1. Verify SpCrud has `delete_item` method per Public surface
2. Verify SharePointListProvider Protocol has `from_sp_fields` method
3. Confirm 8-list canonical entity set in FileBasedListProvider matches `[D-051]`
4. Align with `template_schema.CustomerDeliveryModality.GOOGLE_DRIVE` rename (verify any `FILE_STORAGE` references gone)
5. Verify `TrackingModality: list[str]` handling per `[D-037]` doesn't break SpCrud
6. Verify `ItemType.CONFIRMATION` + 4-value enum handled (column mappings and Choice column expectations)
7. Confirm full 50-test suite still passes against template_schema's new enums (175/175 already confirmed at template-schema-v2-rewrite land)
8. Potentially add new tests for `delete_item` + `from_sp_fields` if they were stubs
9. CLI smoke test `--diagnostic` + `--mock` + `--dry-run` still works

Effort: 2-3 hours; mostly verification + small additions. Most code likely already correct since 175/175 tests passed after template_schema rewrite.

Land trigger: all drift areas verified or fixed + 50+ tests passing + `sharepoint_integration_cli --diagnostic` + `--mock` + `--dry-run` smoke tests confirm clean output.

Coordination notes: Strand 2 (`storage-v1-implementation`) running in parallel on teammate's machine; truly independent at Python import level — neither imports the other. Both consume template_schema (just landed). Land sequentially or independently — no dependency edge between this strand and storage. Storage is foundational for tracker/rule_engine/workflow_engine/email_service downstream; sharepoint_integration is used by tracker/dashboard/email_service/customer_adapter (indirectly) for SP writebacks per `[D-064]` and CommunicationLog writes per FR-42. Teammate to pull main after either lands; rebase + continue strand work as usual.

## Notes
