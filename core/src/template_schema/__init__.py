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
    DeliverableBase,
    DeliveryItemBase,
    DeviceBase,
    EntitySchemaConfig,
    MilestoneBase,
)
from core.src.template_schema.registry import (
    CustomerDeliveryModalityRegistry,
    DeliveryStateRegistry,
    ItemTypeRegistry,
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
    "DeliverableBase",
    "DeliveryItemBase",
    "DeliveryState",
    "DeliveryStateRegistry",
    "DeviceBase",
    "DocType",
    "EntitySchemaConfig",
    "IngestSource",
    "ItemType",
    "ItemTypeRegistry",
    "MilestoneBase",
    "MilestoneStatus",
    "RuleActionType",
    "RuleScope",
    "RuleSubTriggerType",
    "RuleTriggerType",
    "SLUG_PATTERN",
    "TestReportClassification",
    "TestReportItemStatus",
    "TrackingModality",
    "TrackingModalityRegistry",
    "extend_registry",
    "make_slug",
    "validate_in_registry",
    "validate_slug",
]
