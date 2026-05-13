# Human In the Loop Deliverable Automation (HILDA) — Solution Proposal

## Automated Deliverable Tracking & Submission Platform

---

## 1. Executive Summary

This document proposes **DeliverableHub** — a unified, configurable platform that automates the end-to-end deliverable lifecycle for Project Managers (PMs) managing connected device programs across multiple customers. Each customer has a unique process, but the underlying workflow is the same: track a hierarchy of device milestones, deliverables, and delivery items; collect and quality-review those items from internal R&D teams; and submit them to the customer through agreed-upon modalities.

Today, PMs execute this workflow manually using Excel spreadsheets, emails, messenger, and multiple issue-tracking systems — leading to inefficiency, inconsistency, and limited scalability. DeliverableHub replaces this with a template-driven, automation-powered system where customer-specific processes are captured as reusable configuration, and routine tracking, follow-up, and submission tasks are handled by rule-based and AI-driven automation services.

The platform is built on two pillars of existing corporate infrastructure:

- **SharePoint** — serves as the PM dashboard/UI layer, the data store (SharePoint Lists as database tables), and the document repository for test reports, technical reports, waivers, and other artifacts.
- **On-premises Kubernetes cluster (25 nodes)** — runs the automation service layer: the Email Service, communication adapters, workflow orchestration engine, AI/LLM agents, and all backend services. These services read configuration from and write status updates to SharePoint, which the PM sees reflected in their dashboard in real time.

The system is designed to be **configuration-driven**: a one-time customer-specific deliverable template captures the milestones, deliverables, delivery items, tracking modalities, and customer delivery modalities for that customer. PMs use these templates as a starting point to create device-specific trackers, and the automation framework takes it from there.

---

## 2. Current Workflow vs. Automated Workflow

| Workflow Area                                 | Current (Manual)                                                                                                                                     | To-Be (DeliverableHub)                                                                                                                                                                                            |
| --------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Tracker creation**                          | PM manually builds a multi-sheet Excel file for each device × customer combination from scratch                                                      | PM selects a reusable customer template; system generates full hierarchy with static fields pre-populated. PM makes minor adjustments. Alternatively, PM imports from Excel.                                      |
| **Deliverable collection kickoff**            | PM manually sends emails, messenger messages, or creates issues in internal tracker to request items from R&D                                        | PM clicks "Start Collection"; automation sends requests to each owner via the configured tracking modality (email, messenger, issue tracker) with structured reference tags                                       |
| **Ongoing tracking & reminders**              | PM manually checks email, messenger, and issue trackers for updates; manually sends reminders; manually updates Excel rows                           | Automation continuously captures responses from all channels, updates SharePoint data in real time, and sends configurable reminders on schedule. Dashboard reflects live status.                                 |
| **Status visibility**                         | PM rolls up status manually from Excel; stakeholders request updates via email; status is stale by the time it's shared                              | Real-time dashboard in SharePoint with views per device, milestone, and deliverable. AI generates natural-language status summaries on demand or schedule.                                                        |
| **Test report review & issue identification** | PM manually reviews test reports, identifies open issues in labs, determines resolution path (fix, tech report, or waiver), and chases R&D via email | AI performs first-pass quality review against configurable checklists and flags gaps. PM reviews AI assessment, decides resolution path. System creates appropriate delivery items and tracks them automatically. |
| **Tech report & waiver quality review**       | PM reads each document, provides feedback via email, waits for revision, reviews again — multiple manual cycles                                      | AI reviews against customer-specific checklist and generates actionable feedback. PM sends back with one click. Revised versions auto-linked and re-reviewed. Fewer revision cycles.                              |
| **Cross-channel context**                     | PM mentally stitches together status from email threads, messenger conversations, and issue tracker comments for the same delivery item              | All communications for a delivery item (across all channels) are linked and visible in a unified timeline on the delivery item's detail panel                                                                     |
| **Customer submission**                       | PM manually logs into each customer's system (Jira, email, file storage), fills forms, uploads files, formats per customer requirements              | Automation assembles submission package per customer's format and delivery modality. PM previews and clicks "Submit." System submits via customer adapter using PM's stored credentials.                          |
| **Customer follow-up**                        | PM monitors customer system for feedback, copies questions to email, sends to R&D, copies answers back to customer system — all manually             | Automation captures customer feedback, routes to PM dashboard, auto-forwards to R&D owner. AI drafts professional response from R&D input. PM reviews and posts.                                                  |
| **Multi-customer consistency**                | Each customer's process handled differently with ad-hoc tools and trackers; no shared structure                                                      | Customer-specific templates capture process once. Same platform, same data model, same automation — different configuration per customer.                                                                         |
| **Onboarding a new device**                   | PM starts from scratch or copies/modifies a previous Excel file                                                                                      | PM instantiates from customer template in seconds; adjusts dates and owners; automation activates immediately                                                                                                     |
| **Credential management**                     | PM manually logs into each internal and external system with personal credentials                                                                    | PM registers credentials once in a secure vault. Automation authenticates on PM's behalf using stored, encrypted credentials with automatic token refresh.                                                        |

---

## 3. Data Model

The data model is the foundation of the system. It is hierarchical, extensible, and captures both the static configuration (what needs to be delivered) and the dynamic state (current tracking status).

### 3.1 Entity Hierarchy

```
Device (unique ID)
 └── Milestone (unique human-readable string)
      └── Delivery Item (human-readable string)  [grouped by TG Name]
           ├── Description
           ├── Delivery State
           ├── Expected Completion Date
           ├── Type
           ├── Owner Info
           ├── Tracking Modality
           ├── Actual Delivery Item Info
           ├── Customer Delivery Modality
           ├── Customer Delivery Info
           ├── Comment
           ├── Last Updated Timestamp
           └── Last Owner Contacted Timestamp
```

### 3.2 Entity Definitions

**Device:** The top-level entity representing a connected device program. Each device has a unique identifier and is associated with one customer. A device contains one or more milestones defined by the customer's certification or launch process.

**Milestone:** A phase or gate in the customer's device approval process, identified by a unique human-readable string (e.g., "Lab Entry", "Phase 1 Field Test", "Launch Approval"). Each milestone contains a set of delivery items that must be completed before the milestone is considered met.

**Delivery Item:** The atomic unit of work — the individual item that must be produced, tracked, reviewed, and potentially submitted to the customer. Delivery items belong directly to a milestone and are grouped for display purposes by their TG Name (technical group). Each delivery item carries the full set of tracking and delivery metadata described below.

### 3.3 Delivery Item Fields

All fields are defined below. The data model is designed to be **extensible** — new fields can be added in the future without structural changes.

| Field                              | Description                                                                          | Values / Format                                                                                          | Static vs. Dynamic                            |
| ---------------------------------- | ------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------- | --------------------------------------------- |
| **Item No**                        | Human-readable sequential number for the item within its milestone                   | Integer                                                                                                  | Static (auto-assigned on creation)            |
| **TG Name**                        | Technical group responsible for this item (e.g., "Hardware", "Software") — registry-controlled, extensible via config | Text (validated against TGNameRegistry)                                                                  | Static (set in template)                      |
| **Item Description**               | Free-text description of what this delivery item is                                  | Text                                                                                                     | Static (set in template)                      |
| **Delivery State**                 | Current status of the item                                                           | Not Started, Open, Closed, Delayed (extensible)                                                          | Dynamic                                       |
| **Expected Completion Date**       | Target date for completion                                                           | MM/DD/YYYY                                                                                               | Dynamic                                       |
| **Type**                           | Category of the delivery item, determines how it is tracked and reviewed             | Confirmation (Yes/No), Completion %, Test Report, Software Binary, Tech Report, Waiver (extensible)      | Static (set in template)                      |
| **Owner Info**                     | Person responsible for producing this item                                           | Name and/or email address                                                                                | Dynamic                                       |
| **Tracking Modality**              | Communication channel used to track this item with the internal R&D owner            | Email, Messenger, Internal Issue Tracking System (extensible)                                            | Static (agreed per customer/device/item type) |
| **Actual Delivery Item Info**      | HILDA-mediated download URL for artifacts on the shared network drive, or URL to internal system for non-file items | `https://hilda.corp/dl/<token>` per `[D-013]`, or URL to internal system                                 | Dynamic                                       |
| **PLM ID**                         | PLM system document/issue ID for this item — permanent source of truth reference (e.g. Jira-style ID); one per owner typically, flexible for exceptions | Text (e.g. "PROJ-1234")                                                                                  | Dynamic                                       |
| **Handset**                        | This work item applies to handset form factor                                        | Yes / No                                                                                                 | Static (set in template)                      |
| **Tablet**                         | This work item applies to tablet form factor                                         | Yes / No                                                                                                 | Static (set in template)                      |
| **Wearable**                       | This work item applies to wearable form factor                                       | Yes / No                                                                                                 | Static (set in template)                      |
| **MR**                             | This work item applies to MR (Mixed Reality) form factor                             | Yes / No                                                                                                 | Static (set in template)                      |
| **HMR/SMR**                        | This work item applies to HMR/SMR form factor                                        | Yes / No                                                                                                 | Static (set in template)                      |
| **Customer Delivery Modality**     | How this item will be delivered to the customer                                      | None, Email, Customer's Tracking System, Our Own File Storage System (extensible)                        | Static (agreed per customer)                  |
| **Customer Delivery Info**         | Routing information for customer delivery, depends on the Customer Delivery Modality | Empty (if None), email address (if Email), Credential ID (if Customer's Tracking System or File Storage) | Static (set in template per customer)         |
| **Owner Status Note**              | Latest interim status update provided by the item owner                              | Text                                                                                                     | Dynamic (auto-populated from inbound owner message or manual PM entry) |
| **Comment**                        | Free-form notes from PM or automation                                                | Text                                                                                                     | Dynamic                                       |
| **Last Updated Timestamp**         | When this record was last modified                                                   | MM/DD/YYYY - HH:MM                                                                                       | Dynamic (auto-updated)                        |
| **Actual Completion Date**         | Date the delivery item was actually completed (state moved to Closed)                | MM/DD/YYYY                                                                                               | Dynamic (auto-set on closure)                 |
| **Last Owner Contacted Timestamp** | When the PM (or automation) last contacted the owner                                 | MM/DD/YYYY - HH:MM                                                                                       | Dynamic (auto-updated)                        |

**Static vs. Dynamic distinction:** Fields marked "Static" are typically set once in the customer template and carried over when a device tracker is created. Fields marked "Dynamic" change as the delivery item progresses through its lifecycle. The automation services primarily operate on dynamic fields (updating state, timestamps, etc.) while reading static fields to determine how to communicate, track, and deliver.

### 3.4 Database Design

The data model is implemented as **SharePoint Lists** (which function as database tables), organized within a dedicated DeliverableHub SharePoint site. Below is the formal relational design with primary keys, foreign keys, indexes, and column specifications.

#### Table: Customers

Stores one row per customer (e.g., MNO) that the company works with.

| Column                | Type                    | Constraints            | Description                                          |
| --------------------- | ----------------------- | ---------------------- | ---------------------------------------------------- |
| customer_id           | String (auto-generated) | **PK**                 | Unique identifier for the customer                   |
| customer_name         | String                  | NOT NULL, UNIQUE       | Human-readable customer name (e.g., "Carrier Alpha") |
| customer_code         | String                  | NOT NULL, UNIQUE       | Short code used in reference tags (e.g., "CALPHA")   |
| primary_contact_name  | String                  |                        | Main contact person at the customer                  |
| primary_contact_email | String                  |                        | Main contact email                                   |
| notes                 | Text                    |                        | Free-form notes                                      |
| created_date          | DateTime                | NOT NULL, DEFAULT NOW  | Record creation timestamp                            |
| is_active             | Boolean                 | NOT NULL, DEFAULT TRUE | Soft-delete flag                                     |

#### Table: Devices

Stores one row per device program. A device belongs to exactly one customer and is assigned to one PM.

| Column             | Type                    | Constraints                                      | Description                                                        |
| ------------------ | ----------------------- | ------------------------------------------------ | ------------------------------------------------------------------ |
| device_id          | String (auto-generated) | **PK**                                           | Unique device identifier                                           |
| device_name        | String                  | NOT NULL                                         | Human-readable device name (e.g., "ModelZ-5G")                     |
| customer_id        | String                  | **FK → Customers.customer_id**, NOT NULL         | The customer this device program is for                            |
| assigned_pm_id     | String                  | **FK → Users.user_id**, NOT NULL                 | PM responsible for this device                                     |
| status             | String                  | NOT NULL, DEFAULT "Active"                       | Active, Completed, Archived                                        |
| template_id        | String                  | **FK → CustomerTemplates.template_id**, NULLABLE | Template used to create this tracker (NULL if manual/Excel import) |
| created_date       | DateTime                | NOT NULL, DEFAULT NOW                            | Record creation timestamp                                          |
| target_launch_date | Date                    |                                                  | Device launch target date                                          |

**Indexes:** customer_id, assigned_pm_id, status

#### Table: Milestones

One row per milestone within a device. A milestone belongs to exactly one device.

| Column         | Type                    | Constraints                          | Description                                           |
| -------------- | ----------------------- | ------------------------------------ | ----------------------------------------------------- |
| milestone_id   | String (auto-generated) | **PK**                               | Unique milestone identifier                           |
| device_id      | String                  | **FK → Devices.device_id**, NOT NULL | Parent device                                         |
| milestone_name | String                  | NOT NULL                             | Human-readable name (e.g., "Lab Entry", "Field Test") |
| sort_order     | Integer                 | NOT NULL                             | Display order within the device                       |
| target_date    | Date                    |                                      | Milestone target completion date                      |
| status         | String                  | NOT NULL, DEFAULT "Not Started"      | Not Started, In Progress, Completed, Delayed          |
| email_cc_list  | JSON                    |                                      | CC distribution list for all emails in this milestone — array of {name, email, role} |

**Indexes:** device_id, status
**Unique constraint:** (device_id, milestone_name)

#### Table: DeliveryItems

The core tracking table. One row per delivery item — the atomic unit of work. Contains all fields from Section 3.3.

| Column                          | Type                    | Constraints                                    | Description                                                                        |
| ------------------------------- | ----------------------- | ---------------------------------------------- | ---------------------------------------------------------------------------------- |
| item_id                         | String (auto-generated) | **PK**                                         | Unique delivery item identifier                                                    |
| item_no                         | Integer                 | NOT NULL                                       | Sequential number within the milestone (auto-assigned on creation)                 |
| tg_name                         | String                  |                                                | Technical group responsible for this item (e.g., "Hardware", "Software")          |
| milestone_id                    | String                  | **FK → Milestones.milestone_id**, NOT NULL     | Parent milestone                                                                   |
| item_name                       | String                  | NOT NULL                                       | Human-readable name                                                                |
| item_description                | Text                    |                                                | What this item is (static, from template)                                          |
| delivery_state                  | String                  | NOT NULL, DEFAULT "Not Started"                | Not Started, Open, Closed, Delayed (extensible)                                    |
| expected_completion_date        | Date                    |                                                | Target date (MM/DD/YYYY)                                                           |
| item_type                       | String                  | NOT NULL                                       | ConfirmationYesNo, CompletionPct, TestReport, SoftwareBinary, TechReport, Waiver (extensible) |
| owner_name                      | String                  |                                                | R&D owner name                                                                     |
| owner_email                     | String                  |                                                | R&D owner email                                                                    |
| tracking_modality               | String                  | NOT NULL                                       | Email, Messenger, InternalIssueTracker (extensible)                                |
| actual_item_info                | String                  |                                                | HILDA-mediated download URL (`https://hilda.corp/dl/<token>`) per [D-013], or URL to internal system |
| plm_id                          | String                  |                                                | PLM system document/issue ID (e.g. Jira-style ID); permanent source of truth reference for this item |
| handset                         | Boolean                 | NOT NULL, DEFAULT FALSE                        | Work item applies to handset form factor                                           |
| tablet                          | Boolean                 | NOT NULL, DEFAULT FALSE                        | Work item applies to tablet form factor                                            |
| wearable                        | Boolean                 | NOT NULL, DEFAULT FALSE                        | Work item applies to wearable form factor                                          |
| mr                              | Boolean                 | NOT NULL, DEFAULT FALSE                        | Work item applies to MR (Mixed Reality) form factor                                |
| hmr_smr                         | Boolean                 | NOT NULL, DEFAULT FALSE                        | Work item applies to HMR/SMR form factor                                           |
| customer_delivery_modality      | String                  | NOT NULL, DEFAULT "None"                       | None, Email, CustomerTrackingSystem, OurFileStorage (extensible)                   |
| customer_delivery_info          | String                  |                                                | Email address, credential set ID, or empty — depends on modality                   |
| customer_delivery_credential_id | String                  | **FK → PMCredentials.credential_id**, NULLABLE | Credential set used to authenticate with customer system for this item's delivery  |
| owner_status_note               | Text                    |                                                | Latest interim status update from the item owner                                   |
| comment                         | Text                    |                                                | Free-form notes from PM or automation                                              |
| last_updated                    | DateTime                | NOT NULL, DEFAULT NOW                          | Auto-updated on any change                                                         |
| actual_completion_date          | Date                    |                                                | Date item was actually completed (auto-set when delivery_state → Closed)           |
| last_owner_contacted            | DateTime                |                                                | When PM/automation last contacted owner                                            |
| sort_order                      | Integer                 | NOT NULL                                       | Display order within the milestone                                                 |

**Indexes:** milestone_id, delivery_state, item_type, tracking_modality, customer_delivery_modality, owner_email, expected_completion_date, tg_name
**Unique constraint:** (milestone_id, item_name), (milestone_id, item_no)

#### Table: Users

All PMs and administrators who use DeliverableHub.

| Column       | Type                    | Constraints            | Description                             |
| ------------ | ----------------------- | ---------------------- | --------------------------------------- |
| user_id      | String (auto-generated) | **PK**                 | Unique user identifier                  |
| display_name | String                  | NOT NULL               | Full name                               |
| email        | String                  | NOT NULL, UNIQUE       | Corporate email (used for SSO matching) |
| role         | String                  | NOT NULL               | PM, TeamLead, Admin                     |
| is_active    | Boolean                 | NOT NULL, DEFAULT TRUE | Soft-delete flag                        |

#### Table: PMCredentials

Stores encrypted credential sets that PMs register for internal and external systems. The automation layer uses these to authenticate on the PM's behalf. See Section 10 for full credential management details.

| Column                | Type                    | Constraints                      | Description                                                                                             |
| --------------------- | ----------------------- | -------------------------------- | ------------------------------------------------------------------------------------------------------- |
| credential_id         | String (auto-generated) | **PK**                           | Unique credential set identifier                                                                        |
| user_id               | String                  | **FK → Users.user_id**, NOT NULL | PM who owns these credentials                                                                           |
| system_type           | String                  | NOT NULL                         | InternalIssueTracker, CustomerJira, CustomerPortal, CustomerFileStorage, InternalMessenger (extensible) |
| system_name           | String                  | NOT NULL                         | Human-readable system name (e.g., "Carrier Alpha Jira", "Internal Bugzilla")                            |
| auth_method           | String                  | NOT NULL                         | OAuth2, APIToken, BasicAuth, SessionCookie (extensible)                                                 |
| encrypted_credentials | Binary/Text             | NOT NULL                         | AES-256 encrypted credential blob (see Section 10)                                                      |
| token_expiry          | DateTime                | NULLABLE                         | When the current access/refresh token expires (for OAuth2 flows)                                        |
| last_validated        | DateTime                |                                  | Last time credential was successfully used                                                              |
| status                | String                  | NOT NULL, DEFAULT "Active"       | Active, Expired, Revoked                                                                                |
| created_date          | DateTime                | NOT NULL, DEFAULT NOW            | When credential was registered                                                                          |
| updated_date          | DateTime                | NOT NULL, DEFAULT NOW            | Last modification timestamp                                                                             |

**Indexes:** user_id, system_type, status
**Unique constraint:** (user_id, system_type, system_name)

#### Table: CustomerTemplates

Reusable templates that capture the standard milestone/deliverable/delivery-item hierarchy for a customer.

| Column           | Type                    | Constraints                              | Description                                                                       |
| ---------------- | ----------------------- | ---------------------------------------- | --------------------------------------------------------------------------------- |
| template_id      | String (auto-generated) | **PK**                                   | Unique template identifier                                                        |
| customer_id      | String                  | **FK → Customers.customer_id**, NOT NULL | Customer this template is for                                                     |
| template_name    | String                  | NOT NULL                                 | Human-readable name (e.g., "Carrier Alpha Standard v2")                           |
| template_version | Integer                 | NOT NULL, DEFAULT 1                      | Version number for tracking revisions                                             |
| template_data    | JSON/Text               | NOT NULL                                 | Full hierarchy: milestones → delivery items with all static fields                |
| created_by       | String                  | **FK → Users.user_id**, NOT NULL         | Who created this template                                                         |
| created_date     | DateTime                | NOT NULL, DEFAULT NOW                    | Creation timestamp                                                                |
| is_active        | Boolean                 | NOT NULL, DEFAULT TRUE                   | Whether this template is available for use                                        |

**Indexes:** customer_id, is_active
**Unique constraint:** (customer_id, template_name, template_version)

#### Table: AutomationRules

Configurable IF/THEN rules that drive the workflow engine.

| Column            | Type                    | Constraints            | Description                                                                            |
| ----------------- | ----------------------- | ---------------------- | -------------------------------------------------------------------------------------- |
| rule_id           | String (auto-generated) | **PK**                 | Unique rule identifier                                                                 |
| rule_name         | String                  | NOT NULL               | Human-readable rule name                                                               |
| scope             | String                  | NOT NULL               | Global, Customer, Device                                                               |
| scope_id          | String                  | NULLABLE               | customer_id or device_id depending on scope (NULL if Global)                           |
| trigger_event     | String                  | NOT NULL               | Event type that activates the rule                                                     |
| trigger_condition | JSON/Text               | NOT NULL               | Structured condition (e.g., "delivery_state = 'Open' AND days_since_last_contact > 3") |
| action_type       | String                  | NOT NULL               | SendReminder, Escalate, UpdateState, TriggerAIReview, QueueSubmission, etc.            |
| action_parameters | JSON/Text               | NOT NULL               | Action-specific parameters (channel, recipients, message template, etc.)               |
| priority          | Integer                 | NOT NULL, DEFAULT 100  | Execution priority (lower = higher priority)                                           |
| is_active         | Boolean                 | NOT NULL, DEFAULT TRUE | Enable/disable flag                                                                    |

**Indexes:** scope, scope_id, trigger_event, is_active

#### Table: CommunicationLog

Audit trail of all automated communications sent and received by the system.

| Column              | Type                    | Constraints                                    | Description                                                         |
| ------------------- | ----------------------- | ---------------------------------------------- | ------------------------------------------------------------------- |
| log_id              | String (auto-generated) | **PK**                                         | Unique log entry identifier                                         |
| item_id             | String                  | **FK → DeliveryItems.item_id**, NULLABLE       | Linked delivery item (NULL if not yet classified)                   |
| device_id           | String                  | **FK → Devices.device_id**, NULLABLE           | Linked device (for faster queries)                                  |
| channel             | String                  | NOT NULL                                       | Email, Messenger, InternalIssueTracker, CustomerSystem              |
| direction           | String                  | NOT NULL                                       | Inbound, Outbound                                                   |
| sender              | String                  |                                                | Sender address/name                                                 |
| recipients          | String                  |                                                | Recipient address(es)/name(s)                                       |
| subject             | String                  |                                                | Email subject or message title                                      |
| summary             | Text                    |                                                | Brief content summary (generated by LLM for inbound)                |
| external_message_id | String                  |                                                | ID in the external system (email message ID, Jira comment ID, etc.) |
| attachments         | JSON/Text               |                                                | List of attachment filenames and SharePoint URLs                    |
| credential_id       | String                  | **FK → PMCredentials.credential_id**, NULLABLE | Credential set used for this communication (if external system)     |
| timestamp           | DateTime                | NOT NULL, DEFAULT NOW                          | When the communication occurred                                     |

**Indexes:** item_id, device_id, channel, direction, timestamp

#### Entity-Relationship Summary

```
Customers 1──────────M Devices
Customers 1──────────M CustomerTemplates
Users     1──────────M Devices (via assigned_pm_id)
Users     1──────────M PMCredentials
Devices   1──────────M Milestones
Milestones 1─────────M DeliveryItems
DeliveryItems 1──────M CommunicationLog
PMCredentials 1──────M DeliveryItems (via customer_delivery_credential_id)
PMCredentials 1──────M CommunicationLog (via credential_id)
```

**SharePoint implementation notes:** SharePoint Lists support lookup columns (which function as foreign keys in the UI), indexed columns, and JSON-type columns for the template_data and condition/parameter fields. For columns that reference other lists (e.g., device_id referencing the Devices list), SharePoint Lookup columns are used to enforce referential integrity and enable cross-list filtering. The PostgreSQL instance on the K8s cluster mirrors critical tables (DeliveryItems, CommunicationLog, AutomationRules) for high-performance query access by the automation services, with a sync service maintaining consistency between SharePoint and PostgreSQL.

Document libraries within the same SharePoint site store the actual files (test reports, tech reports, waivers, software binaries), linked to delivery items via the "actual_item_info" URL field.

---

## 4. Customer-Specific Templates & Device Tracker Creation

### 4.1 The Template Concept

Each customer has a recurring, well-understood certification process. While the specific delivery items may vary slightly between devices, the overall structure of milestones, deliverables, and delivery item types is stable for a given customer. DeliverableHub captures this structure as a **Customer Deliverable Template**.

A template defines:

- The standard set of **milestones** for that customer's process.
- Within each milestone, the standard **deliverables**.
- Within each deliverable, the standard **delivery items** with all **static fields** pre-populated: description, type, tracking modality, customer delivery modality, and customer delivery info.

For example, a template for "Customer Alpha" might define:

```
Customer Alpha Template
├── Milestone: "Lab Entry"
│   ├── Deliverable: "RF Test Results"
│   │   ├── Delivery Item: "Band-1 RF Conformance" (Type: Test Report, Track via: Email, Deliver via: Customer Jira)
│   │   ├── Delivery Item: "Band-3 RF Conformance" (Type: Test Report, Track via: Email, Deliver via: Customer Jira)
│   │   └── Delivery Item: "RF Summary Status" (Type: Completion %, Track via: Messenger, Deliver via: None)
│   └── Deliverable: "Known Issues Package"
│       ├── Delivery Item: "Camera Known Issues" (Type: Tech Report, Track via: Email, Deliver via: Customer Jira)
│       └── Delivery Item: "Modem Known Issues" (Type: Tech Report, Track via: Internal Issue Tracker, Deliver via: Customer Jira)
├── Milestone: "Field Test"
│   └── ...
└── Milestone: "Launch Approval"
    └── Deliverable: "Waivers"
        └── Delivery Item: "Post-Launch Fix Waiver" (Type: Waiver, Track via: Email, Deliver via: Email)
```

Templates are stored in the **CustomerTemplates** SharePoint List as structured data and are created/maintained by PM team leads or system administrators via the DeliverableHub UI.

### 4.2 Creating a Device Tracker

When a PM starts work on a new device for a given customer, they create a **Device Tracker** through one of two methods:

**Method 1 — From Template (Recommended):**

1. PM selects "Create New Device Tracker" in the DeliverableHub UI.
2. PM chooses the customer and selects the corresponding template.
3. The system generates the full milestone → deliverable → delivery item hierarchy, pre-populating all static fields from the template.
4. PM reviews and makes minor adjustments: adding or removing delivery items that are specific to this device, updating expected completion dates, assigning owners.
5. PM confirms. The system creates all corresponding rows in the SharePoint Lists (Devices, Milestones, Deliverables, DeliveryItems).

**Method 2 — From Excel Import:**

1. PM selects "Import from Excel."
2. PM uploads an Excel file structured with the expected columns matching the data model (the system provides a downloadable Excel template aligned with the data model).
3. The system parses the Excel, validates the data, and creates the corresponding SharePoint List entries.
4. PM reviews and confirms.

**Method 3 — Manual Entry:**

1. PM manually creates milestones, deliverables, and delivery items row by row in the DeliverableHub UI.
2. Suitable for small programs or one-off adjustments.

Once a device tracker is created, the **automation framework activates** — reading the static configuration fields (tracking modality, customer delivery modality, etc.) to determine how to automate each delivery item's lifecycle.

---

## 5. Generalized Workflow

Although each customer has a unique process, every engagement follows the same generalized workflow. This section describes the end-to-end process, from device schedule creation through customer approval.

### 5.1 Workflow Stages

**Stage 1 — Device Schedule & Tracker Setup**

A device schedule with customer-specific milestones is created. The PM creates a device tracker (from a customer template, Excel import, or manual entry) that captures all milestones, deliverables, and delivery items. Static fields (modalities, delivery info) are pre-populated from the template.

**Stage 2 — Deliverable Collection Kickoff**

Based on the device schedule and understanding of R&D lead times, the PM initiates deliverable collection. The automation framework sends initial requests to delivery item owners via their configured tracking modality (email, messenger, or internal issue tracker). Each request references the specific delivery item, expected completion date, and type of artifact required.

**Stage 3 — Ongoing Tracking & Follow-Up**

The automation framework continuously tracks all delivery items by sending periodic reminders to owners, capturing status updates received through any modality, and updating the dynamic fields in the SharePoint DeliveryItems list. PMs monitor progress through the SharePoint dashboard, which reflects real-time status. PMs can manually override any field, trigger ad-hoc reminders, or adjust deadlines.

**Stage 4 — PM Review & Quality Assurance**

As R&D teams deliver items (test reports, tech reports, software binaries), PMs review them for quality and completeness. The review process differs by delivery item type:

- **Test Reports:** PM reviews test results, identifies any open issues still in labs, and works with R&D to resolve them. If issues cannot be resolved before the milestone, PM determines the appropriate resolution path (see below).
- **Tech Reports (Known Issues):** PM solicits known issues from R&D module teams. R&D teams provide a technical report for each issue. PM reviews each report for clarity, completeness, and customer-readiness.
- **Waivers:** For issues that will be fixed post-launch, R&D creates a waiver document explaining why the issue is not critical for launch. PM reviews and ensures the waiver meets customer acceptance criteria.
- **Software Binaries / Completion % / Binary values:** PM verifies the artifact is delivered and records status.

**Issue Resolution Paths (determined during PM review):**

| Scenario                                                   | Resolution                                                 | Delivery Item Created |
| ---------------------------------------------------------- | ---------------------------------------------------------- | --------------------- |
| Issue will be fixed before device launch                   | R&D fixes the issue; no additional delivery item needed    | None                  |
| Issue is due to network behavior, not device               | R&D creates a tech report explaining the analysis          | Tech Report           |
| Issue is by-design and no customer requirement is violated | R&D creates a tech report explaining the intended behavior | Tech Report           |
| Issue will be fixed post-launch                            | R&D creates a waiver document justifying post-launch fix   | Waiver                |

PMs follow up with R&D teams through the configured tracking modality to bring all reports and waivers to the required quality level. The AI/LLM layer assists by performing first-pass quality reviews against configurable checklists and suggesting improvements.

**Stage 5 — Customer Submission**

Once all delivery items for a milestone (or deliverable) are ready, the PM submits them to the customer through the configured customer delivery modality for each item. The automation framework handles the actual submission mechanics: posting to the customer's tracking system via API, sending formatted emails, or uploading to the company's customer-facing file storage. PMs review and approve each submission before it goes out. Waivers are submitted separately through the agreed-upon modality.

**Stage 6 — Customer Follow-Up & Closure**

After submission, the customer reviews and may request clarifications or additional information. The automation framework captures incoming feedback (from the customer's tracking system, email, etc.), routes it to the PM dashboard, and can auto-forward questions to the relevant R&D owner. PMs coordinate responses between R&D and the customer until all items are accepted or resolved. Delivery items are marked "Closed" as they are approved by the customer.

### 5.2 Process Flow Diagram

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    STAGE 1: SETUP                                       │
│                                                                         │
│  ┌──────────────┐     ┌──────────────────┐     ┌────────────────────┐  │
│  │ Device       │────▶│ PM Selects        │────▶│ Device Tracker     │  │
│  │ Schedule     │     │ Customer Template │     │ Created in         │  │
│  │ Established  │     │ (or imports XL)   │     │ SharePoint Lists   │  │
│  └──────────────┘     └──────────────────┘     └─────────┬──────────┘  │
└──────────────────────────────────────────────────────────┬──────────────┘
                                                           │
                                                           ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STAGE 2: COLLECTION KICKOFF                          │
│                                                                         │
│  ┌──────────────────┐     ┌────────────────────────────────────────┐   │
│  │ PM Triggers       │────▶│ Automation sends requests to owners   │   │
│  │ Collection Start  │     │ via configured Tracking Modality:     │   │
│  │ (or auto-trigger  │     │   • Email ──▶ Email Service           │   │
│  │  by schedule)     │     │   • Messenger ──▶ Messenger Adapter   │   │
│  └──────────────────┘     │   • Issue Tracker ──▶ Tracker Adapter │   │
│                            └────────────────────────────────────────┘   │
└────────────────────────────────────────────────────┬────────────────────┘
                                                     │
                                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STAGE 3: ONGOING TRACKING                            │
│                                                                         │
│  ┌─────────────────────────────────────────────────────────────────┐   │
│  │                   Automation Loop                                │   │
│  │                                                                  │   │
│  │  ┌─────────────┐    ┌──────────────┐    ┌───────────────────┐  │   │
│  │  │ Send         │──▶│ Capture      │──▶│ Update SharePoint │  │   │
│  │  │ Reminders    │   │ Responses &  │   │ Lists (state, %,  │  │   │
│  │  │ (per config  │   │ Attachments  │   │ timestamps, links)│  │   │
│  │  │  schedule)   │   │ via all      │   │                   │  │   │
│  │  └─────────────┘   │ modalities   │   └───────────────────┘  │   │
│  │         ▲           └──────────────┘            │              │   │
│  │         └───────────────────────────────────────┘              │   │
│  │                   (repeat until complete)                      │   │
│  └─────────────────────────────────────────────────────────────────┘   │
│                                                                         │
│  ┌───────────────────────────────────────────────┐                     │
│  │ PM Dashboard (SharePoint)                      │                     │
│  │ • Real-time status per milestone/deliverable   │                     │
│  │ • Manual overrides (adjust dates, re-assign)   │                     │
│  │ • Trigger ad-hoc reminders                     │                     │
│  └───────────────────────────────────────────────┘                     │
└────────────────────────────────────────────────┬────────────────────────┘
                                                 │
                                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STAGE 4: PM REVIEW & QUALITY ASSURANCE               │
│                                                                         │
│  ┌─────────────────┐                                                   │
│  │ R&D delivers    │                                                   │
│  │ item (report,   │                                                   │
│  │ binary, etc.)   │                                                   │
│  └────────┬────────┘                                                   │
│           │                                                             │
│           ▼                                                             │
│  ┌─────────────────┐     ┌──────────────────────────────────────────┐  │
│  │ AI performs      │────▶│ PM reviews AI assessment + artifact      │  │
│  │ first-pass       │     │                                          │  │
│  │ quality check    │     │  ┌─ Quality OK? ─── YES ──▶ Approve ──┐ │  │
│  └─────────────────┘     │  │                                     │ │  │
│                           │  └─── NO                               │ │  │
│                           │       │                                │ │  │
│                           │       ▼                                │ │  │
│                           │  Send feedback to R&D owner            │ │  │
│                           │  via Tracking Modality                 │ │  │
│                           │       │                                │ │  │
│                           │       ▼                                │ │  │
│                           │  R&D revises and resubmits ────────────┘ │  │
│                           └──────────────────────────────────────────┘  │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐  │
│  │  Issue Resolution (during test report review):                    │  │
│  │                                                                    │  │
│  │  Open issue found ──▶ Will fix pre-launch? ── YES ──▶ No action  │  │
│  │                       │                                            │  │
│  │                       NO                                           │  │
│  │                       │                                            │  │
│  │                       ├── Network-side issue? ── YES ──▶ R&D      │  │
│  │                       │                          creates Tech     │  │
│  │                       │                          Report (analysis)│  │
│  │                       │                                            │  │
│  │                       ├── By-design behavior? ── YES ──▶ R&D     │  │
│  │                       │                          creates Tech     │  │
│  │                       │                          Report (design)  │  │
│  │                       │                                            │  │
│  │                       └── Fix post-launch? ──── YES ──▶ R&D      │  │
│  │                                                 creates Waiver    │  │
│  └──────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────┬────────────────────────┘
                                                 │
                                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STAGE 5: CUSTOMER SUBMISSION                         │
│                                                                         │
│  ┌─────────────────┐     ┌────────────────────────────────────────┐   │
│  │ All items for    │────▶│ Automation assembles submission per    │   │
│  │ milestone ready  │     │ Customer Delivery Modality:            │   │
│  └─────────────────┘     │   • Email ──▶ Email Service            │   │
│                           │   • Customer Tracking System           │   │
│                           │     ──▶ Customer Adapter (Jira, etc.) │   │
│                           │   • File Storage ──▶ Upload Service   │   │
│                           └──────────────┬─────────────────────────┘   │
│                                          │                              │
│                                          ▼                              │
│                           ┌──────────────────────────┐                  │
│                           │ PM reviews & approves     │                  │
│                           │ submission preview         │                  │
│                           └──────────────┬───────────┘                  │
│                                          │                              │
│                                          ▼                              │
│                           ┌──────────────────────────┐                  │
│                           │ System submits to         │                  │
│                           │ customer                  │                  │
│                           └──────────────────────────┘                  │
└────────────────────────────────────────────────┬────────────────────────┘
                                                 │
                                                 ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    STAGE 6: CUSTOMER FOLLOW-UP & CLOSURE               │
│                                                                         │
│  ┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐  │
│  │ Customer sends   │────▶│ Automation       │────▶│ PM coordinates  │  │
│  │ feedback / RFI   │     │ captures &       │     │ response with   │  │
│  │ via their system │     │ routes to PM     │     │ R&D team        │  │
│  └─────────────────┘     │ dashboard +      │     └────────┬────────┘  │
│                           │ forwards to R&D  │              │           │
│                           └─────────────────┘              ▼           │
│                                               ┌─────────────────────┐  │
│                                               │ AI drafts response  │  │
│                                               │ PM reviews & posts  │  │
│                                               │ to customer system  │  │
│                                               └─────────┬───────────┘  │
│                                                         │              │
│                                                         ▼              │
│                                               ┌─────────────────────┐  │
│                                               │ Customer approves   │  │
│                                               │ ──▶ Item = Closed   │  │
│                                               │ ──▶ Update SharePt  │  │
│                                               └─────────────────────┘  │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## 6. Solution Architecture

### 6.1 Two-Pillar Architecture

The platform is built on two layers with a clear separation of concerns:

**Layer 1 — SharePoint (UI + Data + Documents)**

SharePoint is the PM-facing layer. PMs interact with the system entirely through SharePoint. This includes the dashboard views (built on SharePoint Lists and custom SharePoint pages/web parts), the deliverable tracker data (stored as SharePoint List rows), and all document artifacts (stored in SharePoint Document Libraries). SharePoint provides native capabilities for access control, version history, co-authoring, search, and Office integration — all of which the platform leverages.

**Layer 2 — Kubernetes Cluster (Automation Services)**

The 25-node on-premises Kubernetes cluster runs all backend automation: the Email Service, communication adapters (messenger, internal issue tracker, customer systems), the workflow orchestration engine, AI/LLM agents, and the rule engine. These services read configuration and static fields from SharePoint Lists, perform their work (sending emails, parsing responses, running quality reviews), and write results back to SharePoint Lists — which are then immediately visible in the PM's dashboard.

```
┌──────────────────────────────────────────────────────────────────┐
│                      PM LAYER (SharePoint)                       │
│                                                                  │
│  ┌──────────────┐  ┌─────────────────┐  ┌────────────────────┐  │
│  │  Dashboard   │  │  SharePoint     │  │  Document          │  │
│  │  Views       │  │  Lists          │  │  Libraries         │  │
│  │  (Web Parts) │  │  (Data Tables)  │  │  (Reports,Waivers) │  │
│  └──────┬───────┘  └────────┬────────┘  └─────────┬──────────┘  │
│         │                   │                      │             │
└─────────┼───────────────────┼──────────────────────┼─────────────┘
          │    Microsoft Graph API / SharePoint REST API
          │                   │                      │
┌─────────┼───────────────────┼──────────────────────┼─────────────┐
│         ▼                   ▼                      ▼             │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Orchestration & Workflow Engine              │   │
│  │           (Rule Engine + AI/LLM Agent Layer)             │   │
│  └──────┬────────────┬────────────┬────────────┬────────────┘   │
│         │            │            │            │                 │
│    ┌────▼───┐  ┌────▼────┐  ┌───▼─────┐  ┌──▼──────────┐      │
│    │ Email  │  │Internal │  │Internal │  │  Customer   │      │
│    │Service │  │Messenger│  │Issue    │  │  System     │      │
│    │(Ded.   │  │Adapter  │  │Tracker  │  │  Adapters   │      │
│    │Mailbox)│  │         │  │Adapter  │  │  (per cust.)│      │
│    └────────┘  └─────────┘  └─────────┘  └─────────────┘      │
│                                                                  │
│    ┌──────────┐  ┌──────────┐                                   │
│    │PostgreSQL│  │  Redis   │                                   │
│    │(workflow │  │(cache,   │                                   │
│    │ state)   │  │ queues)  │                                   │
│    └──────────┘  └──────────┘                                   │
│                                                                  │
│              AUTOMATION LAYER (Kubernetes Cluster, 25 nodes)     │
└──────────────────────────────────────────────────────────────────┘
```

### 6.2 Why SharePoint as Both UI and Data Store

- **PMs already know SharePoint.** No new tool to learn for the dashboard — it is just custom views on SharePoint Lists.
- **SharePoint Lists act as lightweight database tables** with built-in features: column types, calculated fields, filtering, grouping, sorting, conditional formatting, and custom views — all configurable without code.
- **Document Libraries** provide native versioning, access control, metadata tagging, and full-text search for all artifacts.
- **Custom SharePoint Web Parts / Power Apps** can be built on top of the Lists to create rich dashboard experiences (Kanban boards, status summaries, charts) without a separate frontend application.
- **Microsoft Graph API** provides a robust, well-documented API for the K8s services to read from and write to SharePoint Lists and Libraries programmatically.
- **Existing infrastructure** — no additional hosting, licensing, or maintenance for the UI layer.

### 6.3 Why Kubernetes for the Automation Layer

- **Services need to run continuously** — the Email Service must poll the mailbox 24/7; adapters must listen for webhooks; the workflow engine must process event queues.
- **Each service is independently deployable and scalable** — adding a new customer adapter means deploying a new pod, not modifying existing services.
- **25-node cluster provides ample capacity** — baseline DeliverableHub services will use approximately 8–12 nodes, leaving headroom for growth.
- **Existing infrastructure** — the cluster is already provisioned and operational.

---

## 7. Communication Adapters (Channel Integrations)

Each adapter is a bidirectional connector that syncs messages and attachments between the automation layer and a communication channel, without changing how internal or external teams work.

### 7.1 Email Service (Dedicated Mailbox)

A dedicated email address (e.g., `deliverablehub@company.com`) serves as the single point of contact for all automated email communication. The Email Service runs on the K8s cluster and owns this mailbox.

- **Technology:** Microsoft Graph API connected to the dedicated mailbox; persistent pod with push notification subscription or scheduled polling.
- **Structured templates:** All outbound emails embed a machine-readable reference tag in the subject line (e.g., `[DH-DeviceX-PM042-M01-D03-I07]`) that encodes the device, PM, milestone, deliverable, and delivery item. When recipients reply, the Email Service parses the tag to route the response to the correct SharePoint List row.
- **Inbound parsing:** Rule-based parser extracts the reference tag; LLM fallback for malformed threads. Attachments are auto-uploaded to SharePoint Document Libraries and linked to the delivery item.
- **Outbound sending:** Templates are populated from SharePoint List data (delivery item description, expected date, owner, etc.) and sent from the dedicated address with the PM's name in the signature.

### 7.2 Internal Messenger Adapter

- **Technology:** Bot/webhook API of the messenger platform.
- **Inbound:** Captures status updates and file shares from designated channels or DM threads; links them to delivery items via reference tags or keyword matching.
- **Outbound:** Sends reminders, status requests, and follow-ups on behalf of the PM.

### 7.3 Internal Issue Tracker Adapter

- **Technology:** REST API of the internal issue tracker.
- **Bi-directional sync:** Delivery item fields in SharePoint map to issue fields in the tracker. Status changes, attachments, and comments sync in near-real-time via webhooks or polling.
- **Auto-linking:** When a delivery item's tracking modality is "Internal Issue Tracker," the system can auto-create a linked issue or associate an existing one.

### 7.4 Customer System Adapters (Pluggable per Customer)

Each customer's external system gets a dedicated adapter implementing a common interface: `submitItem`, `getStatus`, `postComment`, `uploadAttachment`.

- **Customer using Jira:** Adapter uses Jira REST API.
- **Customer using proprietary portal:** Browser-automation layer (Playwright/Selenium) if no API is available.
- **Customer using email submission:** The Email Service handles it with a customer-specific template.
- **Customer using our file storage:** Adapter uploads to the designated shared storage location.

Each adapter is registered via configuration in the SharePoint **AutomationRules** list, specifying endpoint URL, field mappings, and templates.

**Authentication:** When an adapter needs to interact with a customer system, it calls the Credential Service (see Section 10) to retrieve the assigned PM's credentials for that system. The adapter authenticates as the PM — all actions in the customer's system appear under the PM's identity. The delivery item's `customer_delivery_credential_id` field specifies which credential set to use, ensuring the correct PM's credentials are used even when multiple PMs work with the same customer.

---

## 8. Orchestration & Automation Engine

### 8.1 Rule-Based Automation

Configurable IF/THEN rules stored in the SharePoint **AutomationRules** list, executed by the workflow engine on the K8s cluster.

| Trigger                                                                                                | Action                                                                              |
| ------------------------------------------------------------------------------------------------------ | ----------------------------------------------------------------------------------- |
| Delivery item created with state "Not Started" and collection date reached                             | Send initial request to owner via configured Tracking Modality                      |
| Delivery item state is "Open" and Last Owner Contacted > N days ago                                    | Send reminder to owner via Tracking Modality; update Last Owner Contacted timestamp |
| Delivery item state is "Open" and Expected Completion Date is within N days                            | Send urgency escalation to owner and PM                                             |
| Attachment received (via any modality) for a delivery item of type Test Report, Tech Report, or Waiver | Upload to SharePoint; link to delivery item; trigger AI quality review              |
| AI quality review passes                                                                               | Update state; notify PM that item is ready for final review                         |
| PM approves delivery item and Customer Delivery Modality ≠ None                                        | Queue item for customer submission                                                  |
| Customer system status changes (captured by customer adapter)                                          | Update delivery item state in SharePoint; notify PM                                 |
| All delivery items in a deliverable reach state "Closed"                                               | Mark deliverable complete; notify PM                                                |

Rules reference the data model fields directly, making them customer-agnostic. The same rule ("send reminder when Last Owner Contacted > N days") works regardless of customer, because the Tracking Modality field tells the rule which channel to use.

### 8.2 AI/LLM Automation Layer

LLM capabilities augment the PM at stages requiring judgment or content generation:

**a) Technical Report & Waiver Quality Review:** When a report or waiver is attached, the LLM reviews it against a configurable checklist (stored per customer in SharePoint): Does it identify the issue clearly? Are reproduction steps included? Is root-cause analysis present? Is the fix timeline or waiver justification adequate? The LLM generates a review summary for the PM.

**b) Intelligent Message Classification:** Incoming emails and messages are classified by the LLM (status update, RFI response, new issue report, etc.) and routed to the correct delivery item. Confidence thresholds control whether auto-routing or PM confirmation is required.

**c) Customer Response Drafting:** When a customer requests clarification, the system forwards the question to the R&D owner. Once R&D responds, the LLM drafts a professional response suitable for the customer system. PM reviews and approves before posting.

**d) Status Summarization:** On demand or on schedule, the LLM generates natural-language status summaries per device, milestone, or deliverable for stakeholder reporting.

---

## 9. Human-in-the-Loop Design

Automation handles the toil; the PM retains authority over every outward-facing action.

| Action                                                      | Automation Level                               | PM Role                                                           |
| ----------------------------------------------------------- | ---------------------------------------------- | ----------------------------------------------------------------- |
| Sending initial collection requests to R&D                  | Fully automated per schedule and config        | PM triggers collection start; system executes                     |
| Sending reminders and follow-ups to R&D                     | Rule-automated per configured schedule         | PM can pause, customize, or manually trigger                      |
| Capturing responses and updating delivery item status       | Fully automated                                | PM sees updated dashboard; can override any field                 |
| First-pass quality review of reports and waivers            | AI-automated (review + score + feedback draft) | PM reviews AI assessment, decides to approve or return            |
| Identifying issue resolution paths (tech report vs. waiver) | PM decision, AI suggests                       | PM determines the path; system creates delivery items accordingly |
| Submitting delivery items to customer                       | System prepares and packages submission        | PM previews and clicks "Submit"                                   |
| Responding to customer follow-ups                           | AI drafts response from R&D input              | PM reviews, edits, and approves before posting                    |
| Creating and editing customer templates                     | Manual (one-time setup per customer)           | PM lead or admin creates template; PMs use it                     |
| Creating device trackers                                    | Semi-automated (from template or Excel)        | PM selects template, adjusts, confirms                            |

The guiding principle: **no delivery item is submitted to a customer and no external communication is sent without explicit PM approval.**

---

## 10. Credential Management & Authentication

PMs log into internal and external systems (internal issue tracker, internal messenger, customer Jira, customer portals, customer file storage) using their own personal credentials. The DeliverableHub automation layer acts **on behalf of the PM** — using the PM's credentials to authenticate with these systems to read status, post comments, upload artifacts, and submit deliverables. This ensures that all actions taken by the automation are attributable to the responsible PM, maintaining accountability and audit trails in every external system.

### 10.1 Design Principles

**PM credentials are used, not service accounts.** When the automation services interact with a customer's Jira (for example), they authenticate as the specific PM assigned to that device — not as a generic service account. This means actions appear in the customer's system under the PM's name, which is both an accountability requirement and a customer expectation. The same applies to internal systems: reminders and status queries in the internal issue tracker appear as the PM's actions.

**Credentials are encrypted at rest and in transit.** No credential is ever stored in plaintext. All credential data in the PMCredentials table is encrypted with AES-256 before being written to storage. The encryption key is managed by a dedicated secrets infrastructure (see below). Credentials in transit between the K8s automation services and external systems are always over TLS.

**PMs manage their own credentials.** Each PM is responsible for registering, updating, and revoking their credentials for each system they use. The system provides a secure UI for this and alerts PMs when credentials are approaching expiry.

### 10.2 Credential Storage Architecture

Credentials are stored in the **PMCredentials** table (see Section 3.4 for schema). However, the `encrypted_credentials` field is not stored in SharePoint — it is stored in a **dedicated secrets store** on the K8s cluster, with only a reference pointer stored in SharePoint. This separation ensures that even if SharePoint data is accessed by unauthorized users, credential material is not exposed.

```
┌────────────────────────┐        ┌─────────────────────────────┐
│   SharePoint           │        │   K8s Cluster               │
│   (PMCredentials List) │        │                             │
│                        │        │  ┌───────────────────────┐  │
│  credential_id  ──────────────▶│  │  HashiCorp Vault      │  │
│  user_id               │        │  │  (or K8s Secrets +    │  │
│  system_type           │        │  │   Sealed Secrets)     │  │
│  system_name           │        │  │                       │  │
│  auth_method           │        │  │  Stores:              │  │
│  token_expiry          │        │  │  • Encrypted cred     │  │
│  status                │        │  │    blobs keyed by     │  │
│  (NO credential data)  │        │  │    credential_id      │  │
│                        │        │  │  • Encryption keys    │  │
│                        │        │  │  • OAuth tokens       │  │
└────────────────────────┘        │  └───────────────────────┘  │
                                  │                             │
                                  │  ┌───────────────────────┐  │
                                  │  │  Credential Service   │  │
                                  │  │  (API pod)            │  │
                                  │  │  • Stores new creds   │  │
                                  │  │  • Retrieves for use  │  │
                                  │  │  • Refreshes tokens   │  │
                                  │  │  • Validates health   │  │
                                  │  └───────────────────────┘  │
                                  └─────────────────────────────┘
```

**Recommended secrets backend:** HashiCorp Vault (self-hosted on the K8s cluster) provides encrypted storage, access policies, audit logging, and automatic secret rotation. If Vault is not available, Kubernetes Secrets with Sealed Secrets (Bitnami) provide a simpler alternative with encryption at rest via etcd encryption.

### 10.3 Credential Registration Flow

When a PM needs to register credentials for a system:

1. **PM opens Credential Management** in the DeliverableHub UI (SharePoint page / Power App embedded in the site).
2. **PM selects the system type** (e.g., "Customer Alpha Jira", "Internal Bugzilla") and the authentication method.
3. **Depending on auth method:**
   - **OAuth2:** The UI redirects the PM to the external system's OAuth consent page. The PM logs in with their credentials and grants DeliverableHub delegated access. The system receives an access token and refresh token, which are encrypted and stored in the secrets store. The PM never enters their password into DeliverableHub.
   - **API Token:** The PM generates a personal API token in the external system (e.g., Jira personal access token) and pastes it into the registration form. The token is immediately encrypted and stored; the plaintext is never persisted.
   - **Basic Auth (legacy):** The PM enters their username and password. These are immediately encrypted and stored. This method is discouraged in favor of OAuth2 or API tokens.
4. **Credential Service validates** the credentials by making a test API call to the target system.
5. **On success:** The credential_id is recorded in the PMCredentials SharePoint List (metadata only — no credential material), and the encrypted credential blob is stored in the secrets store. The credential is now available for automation services.
6. **On failure:** The PM is informed and can retry or correct the credentials.

### 10.4 Credential Usage by Automation Services

When an automation service needs to interact with an external system on behalf of a PM:

1. **Service determines which PM** is assigned to the device (from the Devices table).
2. **Service looks up the credential_id** for that PM and system type (from PMCredentials or from the delivery item's `customer_delivery_credential_id` field).
3. **Service calls the Credential Service API** (internal K8s service, not exposed externally) with the credential_id.
4. **Credential Service retrieves** the encrypted blob from the secrets store, decrypts it in memory, and returns the usable credential (access token, API key, or session cookie) to the calling service. The decrypted credential is never written to disk or logged.
5. **Calling service authenticates** with the external system using the credential and performs the required action (submit delivery item, post comment, upload attachment, etc.).
6. **For OAuth2 tokens:** If the access token has expired, the Credential Service automatically uses the refresh token to obtain a new access token before returning it. The new token is encrypted and stored, and the token_expiry field is updated.

### 10.5 Token Refresh & Health Monitoring

A background **Credential Health Monitor** runs on the K8s cluster and periodically:

- **Checks token expiry** for all OAuth2 credentials. If a token will expire within a configurable window (e.g., 24 hours), it proactively refreshes it using the stored refresh token.
- **Validates credential health** by making a lightweight test call (e.g., "get current user" API call) to each system. Credentials that fail validation are marked with status = "Expired" or "Revoked" in the PMCredentials list.
- **Alerts the PM** via the DeliverableHub notification center and optionally via email when a credential is expiring, expired, or revoked. The alert includes a direct link to the Credential Management page to re-authenticate.
- **Blocks automation** for affected delivery items: if a PM's credential for a customer system is expired, the automation will not attempt to submit items to that system. Instead, it queues the submission and alerts the PM that credential renewal is required before the submission can proceed.

### 10.6 Credential Scoping & Mapping

Credentials are scoped by PM, system type, and system name. The mapping from delivery item to credential works as follows:

- When a device tracker is created from a template, the system automatically associates the assigned PM's credentials with each delivery item based on the item's `customer_delivery_modality` and the customer's configured system name.
- If the PM has not yet registered credentials for a required system, the system flags the delivery item and prompts the PM to register credentials before automation can activate for that item.
- If a device is reassigned to a different PM, the credential associations are automatically updated to use the new PM's credentials. The system checks whether the new PM has valid credentials for all required systems and alerts if any are missing.

### 10.7 Security Controls

| Control               | Implementation                                                                                                                               |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------- |
| Encryption at rest    | AES-256 in secrets store; no plaintext credentials anywhere in SharePoint or PostgreSQL                                                      |
| Encryption in transit | TLS for all API calls between services and to external systems                                                                               |
| Access control        | Only the Credential Service pod can access the secrets store; service-to-service auth via K8s service accounts and mTLS                      |
| Audit logging         | Every credential retrieval, refresh, and use is logged (without exposing credential material) in the CommunicationLog with the credential_id |
| Credential isolation  | Each PM's credentials are stored under their own path in the secrets store; no PM can access another PM's credentials                        |
| No credential sharing | Automation services never cache decrypted credentials; each use requires a fresh retrieval from the Credential Service                       |
| Revocation            | PM can revoke any credential at any time via the UI; revocation takes effect immediately for all future automation actions                   |
| Rotation support      | PMs are encouraged to rotate API tokens on a configurable schedule; the system tracks last_validated and alerts when tokens are aging        |

---

## 11. Deployment Architecture (On-Premises Kubernetes Cluster)

All automation services run on the existing 25-node K8s cluster.

| Service                    | K8s Resource              | Replicas    | Notes                                                                              |
| -------------------------- | ------------------------- | ----------- | ---------------------------------------------------------------------------------- |
| Workflow Engine (Temporal) | StatefulSet               | 3           | Durable orchestration with persistent volumes                                      |
| Temporal Workers           | Deployment                | 2–4         | Execute activities (email, LLM, adapter calls); scale with load                    |
| Email Service              | Deployment                | 2           | Active-passive or partitioned by customer for HA                                   |
| Messenger Adapter          | Deployment                | 1–2         | Webhook listener + outbound sender                                                 |
| Issue Tracker Adapter      | Deployment                | 1–2         | Polling + webhook receiver                                                         |
| Customer Adapter(s)        | Deployment (per customer) | 1–2 each    | New customer = new deployment from config                                          |
| Credential Service         | Deployment                | 2           | Handles credential storage, retrieval, refresh; only pod with secrets store access |
| Credential Health Monitor  | CronJob / Deployment      | 1           | Periodic token refresh and credential validation                                   |
| HashiCorp Vault            | StatefulSet               | 3           | Encrypted secrets store for PM credentials; HA mode                                |
| LLM Gateway                | Deployment                | 2           | Rate limiting, prompt management, retries                                          |
| PostgreSQL                 | StatefulSet               | 1+1 replica | Workflow state, message queues, cache metadata                                     |
| Redis                      | Deployment                | 1–2         | Caching, pub/sub, job queues                                                       |

The SharePoint layer (UI + data + documents) runs on existing SharePoint infrastructure — no cluster resources required for the front end.

Baseline estimate: 8–12 nodes for DeliverableHub services, leaving 13–17 nodes for other workloads and growth.

---

## 12. Configurability Architecture

The system uses a **three-tier configuration model**:

**Tier 1 — Global Config:** Shared infrastructure settings (SharePoint site URL, Graph API credentials, LLM API keys, K8s namespace, SSO provider). Set once by platform admins.

**Tier 2 — Customer Config (Templates):** Each customer gets a deliverable template that defines milestones, deliverables, delivery item types, tracking modalities, and customer delivery modalities. These templates live in the SharePoint **CustomerTemplates** list and serve as the reusable blueprint for all devices with that customer. Customer-specific automation rules are also defined at this tier.

**Tier 3 — Device Config (Trackers):** When a PM creates a device tracker from a template, they can override or extend any setting for that specific device — adding delivery items, changing deadlines, reassigning owners. These overrides are captured in the device-specific rows in the SharePoint Lists.

This hierarchy means onboarding a new customer is a **template creation exercise**, and onboarding a new device is a **template instantiation exercise** — no code changes, no new deployments.

---

## 13. Implementation Roadmap

### Phase 1 — Foundation (Months 1–3)

- **Infrastructure:** Provision DeliverableHub SharePoint site, Lists, and Document Libraries per the data model. Set up K8s namespace, Helm charts, CI/CD, PostgreSQL, Redis, HashiCorp Vault.
- **Data model implementation:** SharePoint Lists for Customers, Devices, Milestones, Deliverables, DeliveryItems, CustomerTemplates, AutomationRules, CommunicationLog, Users, PMCredentials.
- **Credential management:** Credential Service, Vault integration, PM credential registration UI (OAuth2 + API token flows), credential health monitoring.
- **Template & tracker creation UI:** SharePoint-based interface for creating customer templates and instantiating device trackers (from template, from Excel, or manual entry).
- **Email Service:** Dedicated mailbox, configurable templates, reference-tag parsing, inbound/outbound automation.
- **Basic rule engine:** Reminders, status sync, deadline tracking.
- **PM Dashboard MVP:** SharePoint views/web parts showing deliverable hierarchy, status roll-ups, and manual override capabilities.

### Phase 2 — Intelligence & Adapters (Months 4–6)

- **LLM integration:** Message classification, tech report and waiver quality review, response drafting.
- **Messenger adapter:** Bi-directional communication support.
- **Internal issue tracker adapter:** Bi-directional sync with internal systems.
- **First customer adapter:** Build adapter for the highest-volume customer's external system (e.g., Jira).
- **AI-drafted customer responses** with PM approval flow.

### Phase 3 — Scale & Multi-Customer (Months 7–9)

- Additional customer adapters (second and third customers).
- **Template library:** Multiple customer templates in production; validate that template-to-tracker flow works seamlessly.
- Onboard a second PM team using configuration only.
- AI status summarization and stakeholder reporting.
- Advanced dashboard views (milestone Kanban, cross-device status matrix).

### Phase 4 — Optimize (Months 10–12)

- Browser-automation adapter for customers without APIs.
- Advanced analytics (cycle time per delivery item type, customer response SLAs, R&D team performance).
- Feedback loop: LLM learns from PM edits to improve draft quality over time.
- Full audit trail and compliance reporting.
- Self-service customer template creation UI with guided wizard.

---

## 14. Key Risks and Mitigations

| Risk                                                                  | Mitigation                                                                                                                                                                    |
| --------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Customer systems lack APIs or change frequently                       | Browser-automation fallback; adapter versioning with change detection alerts                                                                                                  |
| LLM hallucination in quality reviews or response drafting             | Human-in-the-loop approval for all customer-facing content; confidence scoring; RAG grounded in actual documents                                                              |
| Internal team resistance                                              | R&D teams' workflows don't change — they keep using email, messenger, and issue trackers as before. Only PMs use DeliverableHub.                                              |
| SharePoint List performance at scale (large number of delivery items) | Index key columns; use filtered views; archive completed devices; Redis caching in the service layer                                                                          |
| SharePoint API rate limits                                            | Exponential backoff and batching in Graph API client; Redis cache for frequently accessed data                                                                                |
| Data sensitivity                                                      | All services on-prem; documents in corporate SharePoint; LLM calls via corporate proxy or on-prem model                                                                       |
| PM credential compromise                                              | Credentials encrypted at rest (AES-256) in Vault, never stored in SharePoint; credential isolation per PM; audit logging; PMs can revoke instantly; token rotation encouraged |
| Credential expiry disrupting automation                               | Health Monitor proactively refreshes tokens and alerts PMs before expiry; automation queues submissions rather than failing silently                                          |
| Template rigidity (new customer process doesn't fit model)            | Data model is extensible (new fields, new states, new modalities can be added); templates are flexible starting points, not rigid constraints                                 |

---

## 15. Success Metrics

- **Time to submission:** Reduction in average days from collection kickoff to customer submission per milestone.
- **Reminder-to-response time:** Reduction in average days between owner reminder and delivery item completion.
- **Report rework rate:** Fewer revision cycles before customer submission, driven by AI pre-review.
- **PM hours per device program:** Reduction in manual tracking, context-switching, and copy-paste effort.
- **Template reuse rate:** Percentage of device trackers created from templates vs. manual/Excel entry.
- **Customer follow-up turnaround:** Reduction in hours from customer question to posted response.
- **Onboarding time:** Days to onboard a new customer (template creation) or a new device (tracker instantiation).
