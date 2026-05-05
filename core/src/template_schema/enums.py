"""Canonical enums for HILDA's entity hierarchy. Anchors FR-7, NFR-14, [D-011]."""
from __future__ import annotations

from enum import Enum


class DeliveryState(str, Enum):
    """Extensible via DeliveryStateRegistry."""

    NOT_STARTED = "Not Started"
    OPEN = "Open"
    CLOSED = "Closed"
    DELAYED = "Delayed"


class ItemType(str, Enum):
    BINARY = "Binary"
    COMPLETION_PCT = "CompletionPct"
    TEST_REPORT = "TestReport"
    SOFTWARE_BINARY = "SoftwareBinary"
    TECH_REPORT = "TechReport"
    WAIVER = "Waiver"


class TrackingModality(str, Enum):
    EMAIL = "Email"
    MESSENGER = "Messenger"
    INTERNAL_ISSUE_TRACKER = "InternalIssueTracker"


class CustomerDeliveryModality(str, Enum):
    NONE = "None"
    EMAIL = "Email"
    CUSTOMER_TRACKING_SYS = "CustomerTrackingSystem"
    FILE_STORAGE = "FileStorage"


class MilestoneStatus(str, Enum):
    NOT_STARTED = "Not Started"
    IN_PROGRESS = "In Progress"
    COMPLETED = "Completed"
    DELAYED = "Delayed"


class RuleScope(str, Enum):
    GLOBAL = "Global"
    CUSTOMER = "Customer"
    DEVICE = "Device"


class RuleActionType(str, Enum):
    SEND_REMINDER = "SendReminder"
    ESCALATE = "Escalate"
    UPDATE_STATE = "UpdateState"
    TRIGGER_AI_REVIEW = "TriggerAIReview"
    QUEUE_SUBMISSION = "QueueSubmission"


class TestReportItemStatus(str, Enum):
    """Per-item status vocabulary for test reports. Anchors [D-011] FR-16."""

    PASSED = "passed"
    FAILED = "failed"
    NON_APPLICABLE = "non-applicable"
    WAIVED = "waived"
    NOT_STARTED = "not-started"


class TestReportClassification(str, Enum):
    """final | interim classifier output. Anchors FR-46."""

    FINAL = "final"
    INTERIM = "interim"
