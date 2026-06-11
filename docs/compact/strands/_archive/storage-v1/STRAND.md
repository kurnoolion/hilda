# storage-v1

**Status:** landed
**Opened:** 2026-06-11
**Landed:** 2026-06-11
**Assignees:** trepository
**Target modules:** storage, diagnostics, template_schema
**Active phase:** development

## Summary

Implement `core/src/storage/` per the reviewed MODULE.md (drafted 2026-05-26, post-refactor API surface review 2026-06-07, D-053 cascade Group 2 applied 2026-06-09): Postgres persistence via SQLAlchemy 2.x async + Alembic (document index per `[D-055]`/`[D-056]` — `DocumentIndexRow` + M:M `DocumentItemAssociation`, CommunicationLog, BATCH-id idempotency cache, FR-31 `AutomationRuleOverride`), Redis client (Celery broker Ph-1/Ph-2 per `[D-022]`), NSD SMB client for the two-tree document store per `[D-013]`/`[D-041]`, STO error codes, and the module CLI per `[D-005]`. Part of Batch 1 development. Runs in parallel with teammate's `sharepoint-integration-drift-sweep` strand.

## Notes

Landed on 2026-06-11 with 2 promoted decisions: D-071, D-072

