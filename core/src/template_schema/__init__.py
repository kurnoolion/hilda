"""template_schema — canonical data model for HILDA's entity hierarchy.

See core/src/template_schema/MODULE.md.
"""
# Register error codes on import.
from core.src.template_schema import error_codes  # noqa: F401
from core.src.template_schema.enums import (
    CustomerDeliveryModality,
    DeliveryState,
    DocType,
    IngestSource,
    ItemType,
    MilestoneStatus,
    RuleActionType,
    RuleScope,
    RuleSubTriggerType,
    RuleTriggerType,
    TestReportClassification,
    TestReportItemStatus,
    TrackingModality,
)
from core.src.template_schema.models import (
    AutomationRuleBase,
    ColumnMapping,
    CustomerSchema,
    CustomerTemplateBase,
    DefaultWorkItemConfig,
    DeliveryItemBase,
    DeviceBase,
    EntitySchemaConfig,
    FolderRoutingEntry,
    MilestoneBase,
    TagCatalogEntry,
    TGFolderRouting,
)
# TGGroupBase DROPPED 2026-06-21 per [D-051] denormalization + architect lock
# (TG fields denormalized onto DeliveryItemBase).
from core.src.template_schema.registry import (
    CustomerDeliveryModalityRegistry,
    DeliveryStateRegistry,
    DocTypeRegistry,
    ItemTypeRegistry,
    RuleActionRegistry,
    RuleTriggerRegistry,
    TGNameRegistry,
    TrackingModalityRegistry,
    extend_registry,
    validate_in_registry,
)
from core.src.template_schema.slug import SLUG_PATTERN, make_slug, validate_slug

__all__ = [
    "AutomationRuleBase",
    "ColumnMapping",
    "CustomerDeliveryModality",
    "CustomerDeliveryModalityRegistry",
    "CustomerSchema",
    "CustomerTemplateBase",
    "DefaultWorkItemConfig",
    "DeliveryItemBase",
    "DeliveryState",
    "DeliveryStateRegistry",
    "DeviceBase",
    "DocType",
    "DocTypeRegistry",
    "EntitySchemaConfig",
    "FolderRoutingEntry",
    "IngestSource",
    "ItemType",
    "ItemTypeRegistry",
    "MilestoneBase",
    "MilestoneStatus",
    "RuleActionRegistry",
    "RuleActionType",
    "RuleScope",
    "RuleSubTriggerType",
    "RuleTriggerRegistry",
    "RuleTriggerType",
    "SLUG_PATTERN",
    "TagCatalogEntry",
    "TGFolderRouting",
    "TGNameRegistry",
    "TestReportClassification",
    "TestReportItemStatus",
    "TrackingModality",
    "TrackingModalityRegistry",
    "extend_registry",
    "make_slug",
    "validate_in_registry",
    "validate_slug",
]
