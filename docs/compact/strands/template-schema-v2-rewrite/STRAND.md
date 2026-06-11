# template-schema-v2-rewrite

**Status:** in-flight
**Opened:** 2026-06-10
**Landed:**
**Assignees:** architect (user)
**Target modules:** core/src/template_schema/
**Active phase:** development

## Summary

template_schema Ph-1 implementation — drift reconciliation against MODULE.md current intent (May 2026 code → post-2026-06-08 cascade model). Scope: 11 enums refresh (DeliveryState 4→11; ItemType 6→4 collapse; TrackingModality 3→5 rename+expand; CustomerDeliveryModality FILE_STORAGE→GOOGLE_DRIVE; RuleActionType 5→24 expansion; plus 4 NEW enums: IngestSource, DocType, RuleTriggerType, RuleSubTriggerType); 5 new Pydantic models (TGGroupBase, DefaultWorkItemConfig, FolderRoutingEntry, TGFolderRouting, TagCatalogEntry); DeliverableBase DELETION per [D-028]; DeliveryItemBase reparent (deliverable_id → milestone_id) + ~18 new fields including pm_approval_at + pm_approval_pm_id per [D-068]; alignment-invariant validator per FR-86; Confirmation+no_customer_upload Pydantic model_validator (TSC-W004); 4 new registries (TGNameRegistry, RuleActionRegistry, RuleTriggerRegistry, DocTypeRegistry); error codes TSC-W003 + TSC-W004 registered; CLI smoke test; full unit test suite green.

Effort estimate: 6-10 hours across 7 phases (enums.py → registry.py → error_codes.py → slug.py verify → models.py → template_schema_cli.py → tests).

Land trigger: all 7 phases complete + tests passing + template_schema_cli --diagnostic + --validate emit TSC-RPT and TSC-QC compact reports without errors.

Coordination notes: Strand 2 (credential-service-v1-implementation) running in parallel on teammate's machine; truly independent at Python import level per credential_service/MODULE.md Depends-on review 2026-06-10. Land sequentially — this strand lands first since template_schema is foundational for storage/sharepoint_integration/llm/rule_engine/workflow_engine/tracker downstream consumers. Teammate to pull main after this strand lands, then land their strand.

## Notes
