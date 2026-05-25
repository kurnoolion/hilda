# Human In the Loop Deliverable Automation (HILDA) — Solution Proposal

## Automated Deliverable Tracking & Submission Platform

---

## 1. Executive Summary

This document proposes **DeliverableHub** — a unified, configurable platform that automates the end-to-end deliverable lifecycle for Project Managers (PMs) managing connected device programs across multiple customers. Each customer has a unique process, but the underlying workflow is the same: track a hierarchy of device milestones, deliverables, and delivery items; collect and quality-review those items from internal R&D teams; and submit them to the customer through agreed-upon modalities.

Today, PMs execute this workflow manually using Excel spreadsheets, emails, messenger, and multiple issue-tracking systems — leading to inefficiency, inconsistency, and limited scalability. DeliverableHub replaces this with a template-driven, automation-powered system where customer-specific processes are captured as reusable configuration, and routine tracking, follow-up, and submission tasks are handled by rule-based and AI-driven automation services.

The platform is built on three pillars of existing corporate infrastructure:

- **SharePoint** — serves as the PM/TPM dashboard/UI layer and the runtime data store (SharePoint Lists as database tables for `Customers`, `Devices`, `Milestones`, `DeliveryItems`, `Users`, `PMCredentials`, `CommunicationLog`). SharePoint does **not** hold document artifacts (test reports, tech reports, waivers, software binaries).
- **On-prem Network Shared Drive (NSD)** — authoritative document store for all owner deliverables and HILDA-generated artifacts per `[D-013]` / `[D-041]`. Two-tree structure (`\\share\hilda\inbound\...` for owner drops, `\\share\hilda\internal\...` for HILDA-classified storage). HILDA-mediated download URLs (`https://hilda.corp/dl/<scoped_token>`) authenticated via on-prem AD per NFR-16.
- **Containerized automation services on bare-metal Linux PC** (Ph-1 / Ph-2 — Docker Compose; Ph-3+ — MicroK8s single-node per `[D-022]`, `[D-025]`) — runs the automation service layer: the Email Service, communication adapters, workflow orchestration engine (Celery), AI/LLM agents, and backend services. These services read configuration (YAML files under `customizations/`) and runtime data (SharePoint Lists via Graph API, plus a PostgreSQL mirror for fast queries), and write updates back to SharePoint, which the PM/TPM sees reflected in their dashboard in real time.

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
| **Tracking Modality**              | Communication channels used to track this item with the internal R&D owner — **multi-value list** per FR-7 (at least one status-capable + one document-capable for artifact items) | Email, CorporateMessenger, CorporatePLM, NetworkSharedDrive, CustomerJIRA (extensible)                  | Static (agreed per customer/device/item type) |
| **doc_count**                      | Number of `test_report` documents required before item advances to `DocumentReceived` state — per FR-7; 0 for Confirmation items | Integer (default 1)                                                                                      | Static (set in template)                      |
| **review_required**                | Whether LLM quality review (FR-53) fires on received documents — per FR-2/FR-53; always false for Confirmation items (non-editable) | Boolean (default false)                                                                                  | Static (set in template; TPM-overridable per FR-14) |
| **review_status**                  | State of LLM quality review for this item — per FR-53                                | Enum: `pending`, `complete`, `not_required`                                                              | Dynamic                                       |
| **item_completion_pct**            | Document-review completion percentage across all received documents — per FR-7       | Integer 0–100 (computed)                                                                                 | Dynamic (computed)                            |
| **email_cc_list**                  | Per-item CC distribution list — pre-populated from template per-TG `default_cc_list` (FR-2); TPM-overridable per-item (FR-14) | JSON array of `{name, email, role}`                                                                      | Static (template default; TPM-overridable)    |
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

**TG-group-level fields (per-template, not per-item):** In addition to the per-DeliveryItem fields above, each customer template defines a set of fields at the **TG-group level** (one record per `tg_name` within the template) — pre-populated at tracker creation per FR-2:

| Field | Description |
| ----- | ----------- |
| **tg_owner** | TG coordinator who knows current engineer assignments for the group (distinct from per-item `owner` — the delivery engineer); TPM-overridable per FR-71 |
| **email_group_alias** | Optional TG corporate email distribution alias (e.g. `ims.corp@corp.com`); when set, FR-9 sends one consolidated outreach to the alias instead of per-owner emails |
| **corp_id_list** | Optional list of corp messenger IDs for all TG members; used by FR-10 escalation when set |
| **default_cc_list** | Default CC distribution list for the TG; copied to each item's `email_cc_list` at tracker creation; TPM-overridable per item (FR-14) |

These fields are stored within the customer template (YAML, see §3.4) and applied identically to all items sharing the same `tg_name` at tracker instantiation.

### 3.4 Database Design

The data model spans **three storage tiers** with deliberate separation between runtime data, configuration, and the mirror used for fast service queries:

1. **SharePoint Lists** (runtime / transactional, PM-facing) — hold `Customers`, `Devices`, `Milestones`, `DeliveryItems`, `Users`, `PMCredentials` (metadata only — see Section 10), `CommunicationLog`. SharePoint Lists provide the PM/TPM dashboard surface and authoritative entity rows.
2. **YAML files under `customizations/`** (configuration, code-release-time gated) — hold `CustomerTemplates` (per FR-39/40/41 under `customizations/template_schemas/<customer>/`) and `AutomationRules` (per FR-30 under `customizations/rules/{global,<customer>,<customer>/<device>}/`). These files are bind-mounted into the HILDA service containers per `[D-025]` and read directly by HILDA at startup. **SharePoint does not read YAML files** — HILDA services do.
3. **PostgreSQL mirror** (fast-query cache, runtime overrides) — runs as a container in the Docker Compose stack (Ph-1/Ph-2; MicroK8s StatefulSet Ph-3+); mirrors critical SharePoint tables (`DeliveryItems`, `CommunicationLog`, `AutomationRules` snapshot) for high-throughput service-layer reads and holds PM/TPM runtime overrides per FR-31.

**Documents are never stored in SharePoint** — all owner deliverables and HILDA-generated artifacts live on the on-prem Network Shared Drive (NSD) per `[D-013]` / `[D-041]`. SharePoint Lists hold metadata and reference URLs only.

Below is the formal relational design for the SharePoint-resident tables, with primary keys, foreign keys, indexes, and column specifications. The YAML-resident entities (`CustomerTemplates`, `AutomationRules`) are documented as YAML file layouts further below.

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
| item_type                       | String                  | NOT NULL                                       | Confirmation, CompletionPct, TestReport, SoftwareBinary, TechReport, Waiver (extensible — per FR-7) |
| owner_name                      | String                  |                                                | R&D owner name                                                                     |
| owner_email                     | String                  |                                                | R&D owner email                                                                    |
| tracking_modality               | JSON (array)            | NOT NULL                                       | Multi-value list per FR-7: Email, CorporateMessenger, CorporatePLM, NetworkSharedDrive, CustomerJIRA (extensible) |
| doc_count                       | Integer                 | NOT NULL, DEFAULT 1                            | Number of `test_report` documents required before `DocumentReceived` per FR-7; 0 for Confirmation items |
| review_required                 | Boolean                 | NOT NULL, DEFAULT FALSE                        | Gates LLM quality review per FR-2/FR-53; always FALSE and non-editable for Confirmation items |
| review_status                   | String                  | NOT NULL, DEFAULT "pending"                    | Enum: `pending`, `complete`, `not_required` per FR-53                              |
| item_completion_pct             | Integer                 | NULLABLE                                       | Computed document-review completion percentage per FR-7                            |
| email_cc_list                   | JSON                    |                                                | Per-item CC distribution list (array of `{name, email, role}`); pre-populated from template per-TG `default_cc_list` per FR-2; TPM-overridable per FR-14 |
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

#### YAML configuration: CustomerTemplates

Reusable templates that capture the standard milestone → delivery-item hierarchy for a customer are stored as **YAML files** under `customizations/template_schemas/<customer_slug>/` (per FR-39/40/41), not as SharePoint List rows. Templates are bind-mounted into HILDA service containers per `[D-025]` and read at tracker-creation time.

**Directory layout:**

```
customizations/template_schemas/
├── <customer_slug>/
│   ├── template.yaml              # milestone → delivery-item hierarchy with static fields
│   ├── tg_groups.yaml             # tg_name → {tg_owner, email_group_alias, corp_id_list, default_cc_list}
│   └── parser_schema.yaml         # per-customer test-report parser spec per [D-011]
```

**`template.yaml` shape (per-customer):**

```yaml
template_name: "Carrier Alpha Standard v2"
template_version: 2
milestones:
  - milestone_name: "Lab Entry"
    sort_order: 1
    delivery_items:
      - item_no: 1
        item_name: "Band-1 RF Conformance"
        tg_name: "RF"
        item_type: "TestReport"
        tracking_modality: ["Email", "NetworkSharedDrive"]
        customer_delivery_modality: "CustomerTrackingSystem"
        doc_count: 1
        review_required: true
        handset: true
        tablet: false
        # ... other static fields per §3.3
```

**Governance:** Template content changes (adding items, changing modalities, updating per-TG CC lists) are routine customer-config edits in YAML, gated by the template-authoring workflow. **Schema changes** (introducing a new field on DeliveryItems, a new `item_type` value, a new `delivery_state`) require a HILDA code release — see §3.5 *Schema Evolution & Field Lifecycle*.

#### YAML configuration: AutomationRules

Configurable IF/THEN rules that drive the workflow engine are stored as **YAML files** under `customizations/rules/` (per FR-30), not as SharePoint List rows. Files are bind-mounted into HILDA service containers per `[D-025]` and read at startup. **Runtime overrides** (PM/TPM pause/resume + parameter customization per FR-31) are stored in PostgreSQL and take precedence over YAML values at evaluation time.

**Directory layout (3-tier resolution: Device → Customer → Global):**

```
customizations/rules/
├── global/
│   └── defaults.yaml              # baseline rules applied to all customers/devices
├── <customer_slug>/
│   ├── customer_rules.yaml        # per-customer overrides + customer-specific rules
│   └── <device_slug>/
│       └── device_rules.yaml      # per-device overrides
```

**Resolution order:** Device-tier values override Customer-tier; Customer-tier overrides Global. PM/TPM runtime overrides from PostgreSQL (FR-31) take precedence over all three YAML tiers.

**Rule shape:**

```yaml
rules:
  - rule_id: "reminder_open_item"
    rule_name: "Send reminder to owner for stale open items"
    trigger_event: "ScheduledTick"
    trigger_condition:
      delivery_state: "Open"
      days_since_last_contact_gt: 3
    action_type: "SendReminder"
    action_parameters:
      channel: "TrackingModality"
      template_id: "owner_reminder_v1"
    priority: 100
    is_active: true
```

**Beat schedule** (per-customer / per-device polling cadence used by FR-23/FR-26/FR-55) is loaded from these YAML files by `hilda-beat` at startup per `[D-022]` implementation note.

**Governance:** Rule content changes are routine customer-config edits in YAML. Adding a new `trigger_event` or `action_type` requires a HILDA code release (rule engine code change) — see §3.5 *Schema Evolution & Field Lifecycle*.

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

**SharePoint implementation notes:** SharePoint Lists support lookup columns (which function as foreign keys in the UI) and indexed columns. For columns that reference other lists (e.g., `device_id` referencing the Devices list), SharePoint Lookup columns are used to enforce referential integrity and enable cross-list filtering. SharePoint holds entity rows only — `CustomerTemplates` and `AutomationRules` are YAML files (above), not SharePoint Lists. The PostgreSQL container in the Docker Compose stack (Ph-1/Ph-2; MicroK8s StatefulSet Ph-3+) mirrors critical SharePoint tables (`DeliveryItems`, `CommunicationLog`) and an in-memory snapshot of resolved `AutomationRules` for high-performance service-layer queries; a sync service maintains consistency between SharePoint and PostgreSQL.

**Document storage:** All document artifacts (test reports, tech reports, waivers, software binaries) live on the on-prem Network Shared Drive (NSD) per `[D-013]` / `[D-041]`, **not** in SharePoint. The `actual_item_info` field on DeliveryItems holds the PLM issue URL per FR-57; HILDA-mediated NSD download URLs (`https://hilda.corp/dl/<scoped_token>`) are returned by the document enumeration API (FR-57) for the SharePoint UI document section. SharePoint Document Libraries are not used.

### 3.5 Schema Evolution & Field Lifecycle

The data model spans two distinct change-governance zones, and the boundary between them is an architectural invariant:

**Zone A — Code-release-gated (data model schema):**
- New columns on SharePoint Lists, new `item_type` / `delivery_state` enum values, new entities
- Requires a versioned HILDA code release: update Pydantic / SQLAlchemy models in `core/src`, run SharePoint List provisioning, run PostgreSQL migration, update YAML template-schema spec so customer YAML can populate the field, update template loader and all downstream consumers
- Gated by HILDA dev/ops team
- **SharePoint admins cannot add columns by clicking in the SP UI** — any field not in the canonical schema will not be picked up by HILDA services and will not be reflected in Postgres

**Zone B — YAML-edit-gated (configuration content within existing schema):**
- New customer template instance, new automation rule, new TG group, modified CC list, adjusted polling schedule
- Requires only a YAML edit under `customizations/`; no code release
- Gated by customer-config / template-authoring workflow

**Adding a new DeliveryItems field — release-time propagation checklist:**

1. Update the canonical schema definition in `core/src` (Pydantic model)
2. Update SharePoint List provisioning script → run against the deployment
3. Run PostgreSQL mirror migration (alembic)
4. Update the YAML template-schema spec so customer templates can populate the new field
5. Update existing customer template YAML files with default values (or document explicit omission)
6. Update template loader to wire the new field through
7. Update consumers (rule engine, outreach formatter, dashboard view, document enumeration API)
8. Publish a versioned release; coordinate with deployment of customer YAML updates

The same release process applies to enum additions (new `item_type`, new `delivery_state`, new `tracking_modality` value, new `doc_type`).

---

## 4. Customer-Specific Templates & Device Tracker Creation

### 4.1 The Template Concept

Each customer has a recurring, well-understood certification process. While the specific delivery items may vary slightly between devices, the overall structure of milestones, deliverables, and delivery item types is stable for a given customer. DeliverableHub captures this structure as a **Customer Deliverable Template**.

A template defines:

- The standard set of **milestones** for that customer's process.
- Within each milestone, the standard **delivery items** (grouped for display by `tg_name`) with all **static fields** pre-populated: `tg_name`, description, type, tracking modality (multi-value), customer delivery modality, `doc_count`, `review_required`, form-factor flags, etc. (per FR-2; see §3.3 for the full field list).
- A separate per-TG group block defining `tg_owner`, `email_group_alias`, `corp_id_list`, and `default_cc_list` for each `tg_name` used in the template (per FR-2).

For example, a template for "Customer Alpha" might define:

```
Customer Alpha Template
├── Milestone: "Lab Entry"
│   ├── Delivery Item: "Band-1 RF Conformance"   (TG: RF, Type: TestReport, Track via: [Email, NetworkSharedDrive], Deliver via: CustomerTrackingSystem)
│   ├── Delivery Item: "Band-3 RF Conformance"   (TG: RF, Type: TestReport, Track via: [Email, NetworkSharedDrive], Deliver via: CustomerTrackingSystem)
│   ├── Delivery Item: "RF Summary Status"        (TG: RF, Type: CompletionPct, Track via: [Email], Deliver via: None)
│   ├── Delivery Item: "Camera Known Issues"     (TG: Camera, Type: TechReport, Track via: [Email, CorporatePLM], Deliver via: CustomerTrackingSystem)
│   └── Delivery Item: "Modem Known Issues"      (TG: Modem, Type: TechReport, Track via: [Email, CorporatePLM], Deliver via: CustomerTrackingSystem)
├── Milestone: "Field Test"
│   └── ...
└── Milestone: "Launch Approval"
    └── Delivery Item: "Post-Launch Fix Waiver"  (TG: PM, Type: Waiver, Track via: [Email], Deliver via: Email)
```

The intermediate "Deliverable" grouping level was removed per `[D-028]` — delivery items belong directly to a milestone and are grouped for display purposes by their `tg_name`.

Templates are stored as **YAML files** under `customizations/template_schemas/<customer_slug>/` (per FR-39/40/41, see §3.4) and are created/maintained by PM team leads or system administrators via the template-authoring workflow.

### 4.2 Creating a Device Tracker

When a PM/TPM starts work on a new device for a given customer, they create a **Device Tracker** through one of three methods (FR-1):

**Method 1 — From Template (`[Ph-1]`, Recommended):**

1. PM/TPM selects "Create New Device Tracker" in the DeliverableHub SharePoint UI.
2. PM/TPM chooses the customer and selects the corresponding template (resolved from `customizations/template_schemas/<customer_slug>/`).
3. HILDA reads the YAML template, generates the full milestone → delivery-item hierarchy, and pre-populates all static fields per FR-2 (TG groupings, `tg_owner`, `email_group_alias`, `corp_id_list`, `default_cc_list` per TG; `doc_count`, `review_required`, `email_cc_list` per item; `expected_completion_date` set from parent `Milestone.target_date`). `plm_id` is **not** set at tracker creation — it is assigned per (owner × milestone) at Start Collection per FR-8.
4. PM/TPM reviews and makes adjustments: adding/removing items per FR-3, overriding `tg_owner` per FR-71, overriding `email_cc_list` or `expected_completion_date` per FR-14.
5. PM/TPM confirms. HILDA creates the corresponding rows in the SharePoint Lists (Devices, Milestones, DeliveryItems) — no `Deliverables` table (D-028).

See FR-1 and FR-2 for the complete behavioral specification.

**Method 2 — From Excel Import (deferred):**

Deferred per DEF-15 (originally FR-4, struck 2026-05-12) — requires the Template Schema Ingestor `[D-010]` to validate Excel against per-customer schema. Implementation phase TBD; revisit in Ph-2 or later.

**Method 3 — Manual Entry (`[Ph-2]`):**

1. PM/TPM manually creates milestones and delivery items row-by-row in the DeliverableHub UI.
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

*Phase scope: in Ph-1 the PM handles all paths manually outside HILDA. In Ph-2 the PM annotates the chosen path in the dashboard per FR-47 — HILDA records the annotation but takes no automated downstream action. Ph-3+ automation (auto-create Tech Report DeliveryItems, monitor `waiver_ref`) is deferred per DEF-17.*

| Scenario                                                   | Resolution                                                 | Resulting Artifact    |
| ---------------------------------------------------------- | ---------------------------------------------------------- | --------------------- |
| Issue will be fixed before device launch                   | R&D fixes the issue; no additional delivery item needed    | None                  |
| Issue is due to network behavior, not device               | R&D creates a tech report explaining the analysis          | Tech Report           |
| Issue is by-design and no customer requirement is violated | R&D creates a tech report explaining the intended behavior | Tech Report           |
| Issue will be fixed post-launch                            | R&D (owner) creates a waiver document justifying post-launch fix | Waiver          |

*Footnote: Waiver artifacts are always **owner-created** — HILDA tracks the `waiver_ref` once supplied by the owner and does not auto-create Waiver DeliveryItems. Tech Report DeliveryItem auto-creation by HILDA is Ph-3+ scope (DEF-17). FR-47 promotes the resolution choice to a formal enum: `resolution_path ∈ {fix_pre_launch, tech_report, waiver}` — the network-behavior and by-design rows above are both sub-cases of the `tech_report` path.*

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

### 6.1 Three-Pillar Architecture

The platform is built on three layers with a clear separation of concerns:

**Layer 1 — SharePoint (UI + Entity Data)**

SharePoint is the PM/TPM-facing layer. PM/TPMs interact with the system entirely through SharePoint. This includes the dashboard views (built on SharePoint Lists and custom web parts) and the runtime entity rows for Customers, Devices, Milestones, DeliveryItems, Users, PMCredentials (metadata only), and CommunicationLog. SharePoint provides native capabilities for access control, version history, search, and Office integration. SharePoint does **not** store document artifacts and does **not** read YAML configuration files.

**Layer 2 — Network Shared Drive (Documents)**

The on-prem NSD per `[D-013]` / `[D-041]` is the authoritative store for all owner deliverables (test reports, tech reports, waivers, software binaries) and HILDA-generated artifacts. Two-tree structure: `\\share\hilda\inbound\...` for owner drops; `\\share\hilda\internal\...` for HILDA-classified storage organized by `<tg_name_slug>/<item_slug>/<doc_type_slug>/<doc_id_slug>/revN/`. Document access is HILDA-mediated via `https://hilda.corp/dl/<scoped_token>` authenticated by on-prem AD per NFR-16.

**Layer 3 — Containerized Automation Services**

Backend automation runs as Docker containers on a single bare-metal Linux PC in Ph-1/Ph-2 (per `[D-022]`, `[D-025]`); migrating to MicroK8s single-node in Ph-3+. Services: `hilda-api`, `hilda-worker` (Celery), `hilda-beat` (Celery scheduler), `hilda-llm-gateway`, plus `postgres` and `redis` containers (Redis as Celery broker and cache per `[D-022]`). YAML configuration files under `customizations/` (`template_schemas/`, `rules/`, `sharepoint_config/`) are bind-mounted read-only into the application services per `[D-025]`. These services read SharePoint entity rows via Microsoft Graph API, read documents from the NSD, perform their work (outreach, parsing, LLM review, submission packaging), and write updates back to SharePoint Lists and the NSD — immediately visible on the PM/TPM dashboard.

```
┌──────────────────────────────────────────────────────────────────┐
│                  PM / TPM LAYER (SharePoint)                     │
│                                                                  │
│  ┌──────────────┐  ┌─────────────────────────────────────────┐  │
│  │  Dashboard   │  │  SharePoint Lists (entity rows)          │  │
│  │  Views       │  │  Customers, Devices, Milestones,         │  │
│  │  (Web Parts) │  │  DeliveryItems, Users, PMCredentials,    │  │
│  │              │  │  CommunicationLog                        │  │
│  └──────┬───────┘  └────────┬─────────────────────────────────┘  │
│         │                   │                                    │
└─────────┼───────────────────┼────────────────────────────────────┘
          │    Microsoft Graph API / SharePoint REST API
          │                   │
┌─────────┼───────────────────┼────────────────────────────────────┐
│         ▼                   ▼                                    │
│  ┌──────────────────────────────────────────────────────────┐   │
│  │              Orchestration & Workflow Engine              │   │
│  │           (Celery + Rule Engine + LLM Gateway)            │   │
│  └──────┬────────────┬────────────┬────────────┬────────────┘   │
│         │            │            │            │                 │
│    ┌────▼───┐  ┌────▼────┐  ┌───▼─────┐  ┌──▼──────────┐      │
│    │ Email  │  │Corp     │  │Corp PLM │  │  Customer   │      │
│    │Service │  │Messenger│  │+ Customer│  │  System     │      │
│    │(Ded.   │  │Adapter  │  │JIRA      │  │  Adapters   │      │
│    │Mailbox)│  │         │  │Adapters  │  │  (per cust.)│      │
│    └────────┘  └─────────┘  └─────────┘  └─────────────┘      │
│                                                                  │
│    ┌──────────┐  ┌──────────┐  ┌─────────────────────────────┐  │
│    │postgres  │  │  redis   │  │  customizations/ (YAML)     │  │
│    │(mirror + │  │(broker + │  │  template_schemas/, rules/, │  │
│    │ overrides)│  │ cache)   │  │  sharepoint_config/         │  │
│    └──────────┘  └──────────┘  │  (bind-mounted [D-025])     │  │
│                                 └─────────────────────────────┘  │
│                                                                  │
│       AUTOMATION LAYER (Docker Compose Ph-1/Ph-2;                │
│                         MicroK8s single-node Ph-3+)              │
└──────────────────────────────────────────────────────────────────┘
                                 │
                                 ▼
                ┌────────────────────────────────────┐
                │  NETWORK SHARED DRIVE (NSD)        │
                │  \\share\hilda\inbound\...         │
                │  \\share\hilda\internal\...        │
                │  (all document artifacts; D-013)   │
                └────────────────────────────────────┘
```

### 6.2 Why SharePoint as UI and Entity Store

- **PM/TPMs already know SharePoint.** No new tool to learn for the dashboard — it is just custom views on SharePoint Lists.
- **SharePoint Lists act as lightweight database tables** with built-in features: column types, calculated fields, filtering, grouping, sorting, conditional formatting, and custom views.
- **Custom SharePoint Web Parts** provide rich dashboard experiences (milestone Kanban, document section, action buttons per FR-56/FR-63/FR-64/FR-65) without a separate frontend application.
- **Microsoft Graph API** provides a robust, well-documented API for the HILDA services to read from and write to SharePoint Lists programmatically.
- **Existing infrastructure** — no additional hosting, licensing, or maintenance for the UI layer.
- *Note: document artifacts live on the NSD per `[D-013]`, not in SharePoint Document Libraries. SharePoint holds metadata and reference URLs only.*

### 6.3 Why Containerized Automation Layer

- **Services need to run continuously** — the Email Service must poll the mailbox 24/7; adapters must listen for webhooks; the workflow engine (Celery beat + workers) must process scheduled tasks and event queues.
- **Each service is independently deployable** — adding a new customer adapter means deploying a new image or extending an existing one via configuration (YAML), not modifying core services.
- **Docker Compose on bare-metal Linux PC (Ph-1/Ph-2)** — operationally simple, no orchestrator overhead, suitable for the v1 scale of a single deployment. All HILDA services + postgres + redis run on one PC per `[D-022]`.
- **MicroK8s single-node migration (Ph-3+)** — adds self-healing, RBAC, secrets/ConfigMap management, and a path to multi-node scaling while preserving the same container images. RabbitMQ Quorum Queues replace Redis as the Celery broker; Rook/Ceph provides durable PVCs; MetalLB provides the external VIP per `[D-022]`. Customer YAML bind-mounts migrate to ConfigMap volumes per `[D-025]`. No Python code change.
- **Existing infrastructure** — the bare-metal PC is already provisioned for Ph-1/Ph-2; MicroK8s rollout is part of the Ph-3+ release.

---

## 7. Communication Adapters (Channel Integrations)

Each adapter is a bidirectional connector that syncs messages and attachments between the automation layer and a communication channel, without changing how internal or external teams work.

### 7.1 Email Service (Dedicated Mailbox)

A dedicated email address (e.g., `deliverablehub@company.com`) serves as the single point of contact for all automated email communication. The Email Service runs on the K8s cluster and owns this mailbox.

- **Technology:** Microsoft Graph API connected to the dedicated mailbox; persistent container with push notification subscription (IMAP IDLE / mail-server webhook) or deadline-tiered polling per FR-23.
- **Structured templates — outbound is per-owner-batch, not per-item:** outreach is consolidated per FR-9 into one outbound email per `(owner × milestone)` round, identified by a stable `BATCH-<id>` reference tag in the subject line (e.g., `[HILDA] BATCH-<id>`). The email body contains one structured reply block per delivery item in the batch, grouped by `tg_name`. When `email_group_alias` is set for the TG, a single outreach is sent to the TG alias containing all items in the TG grouped by owner — each block carries its own `BATCH-<id>` so any TG member can fill in any owner's block, and HILDA routes responses by the `BATCH-<id>` in the filled block (FR-9). The BATCH-id, not a per-item tag, is the routing key.
- **Inbound parsing:** Three convergent paths per FR-12 — `[Ph-1]` structured reply block edited in place (regex-parsed); `[Ph-2]` per-item `mailto:` tap-links with subject-encoded status; `[Ph-1]` free-text replies via rule-based parsing then runtime-LLM classification when rule-based fails. Attachments are routed per FR-52 (two-tier classification + D-039 new-vs-revision), uploaded to the owner's PLM issue (per FR-13 phase-split rules), written to the NSD classified path `<doc_type_slug>/<doc_id_slug>/revN/`, and linked to the delivery item via the document index (FR-57). Documents are **not** uploaded to SharePoint Document Libraries.
- **Outbound sending:** Templates are populated from SharePoint List data and YAML rule configuration, sent from the dedicated address with the PM/TPM's name in the signature; CC field populated with the union of `email_cc_list` across the batch items per FR-9. Outbound is sent `multipart/alternative`; the structured block is ASCII-only for round-trip safety.

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

Each adapter is registered via configuration in the **AutomationRules YAML files** under `customizations/rules/` (per FR-30) and the per-customer config under `customizations/template_schemas/<customer>/`, specifying endpoint URL, field mappings, and outbound templates per FR-27.

**Authentication:**

- **Ph-1 / Ph-2 (current scope):** A single shared HILDA-ops-team credential set is provisioned per customer system (per `[D-019]` v1 — `credential_service` uses K8s Secrets / ops-provisioned credentials). All adapter calls authenticate using this shared credential — there is no per-PM credential mapping at runtime. Actions in customer systems appear under the ops-team service identity, not the individual PM's identity. The `PMCredentials` SharePoint List (§3.4) is provisioned for the Ph-3+ data model but its per-PM rows are not consumed by adapters in Ph-1/Ph-2.
- **Ph-3+ (target):** Per-PM credentials are introduced with full PM-owned OAuth2 / API-token flows, per-item `customer_delivery_credential_id` field mapping, and the Credential Health Monitor (see Section 10 — most of which is Ph-3+ scope). Adapters authenticate as the specific PM assigned to the device; the `customer_delivery_credential_id` on DeliveryItems specifies which credential set to use. *(Note: `customer_delivery_credential_id` is shown in the §3.4 DeliveryItems schema for forward-compatibility but is **not yet captured in requirements.md** as a Ph-1/Ph-2 obligation — it activates with Section 10's Ph-3+ scope.)*

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

> **Phase scope** — *Most of this section describes the **Ph-3+ target state**: per-PM OAuth2 / API-token flows, encrypted PM-owned credential blobs in Vault, automated token refresh, Credential Health Monitor, per-item `customer_delivery_credential_id` mapping.*
>
> *In **Ph-1 / Ph-2**, the credential service uses a **single shared HILDA-ops-team credential set per customer system**, provisioned via `K8s Secrets` / ops-managed sops-encrypted files per `[D-019]` v1. There is no per-PM credential capture, no OAuth2 consent UI for individual PMs, no PMCredentials List population at runtime, and no Credential Health Monitor. Adapter actions appear under the shared ops-team service identity in external systems. The full per-PM model below activates with Ph-3+ migration (D-019 v2 — Vault-backed implementation).*
>
> *Sub-sections §10.1 through §10.7 describe the Ph-3+ target; where Ph-1/Ph-2 behavior differs (single shared credential, no health monitor, no per-PM OAuth flow), the operational reality supersedes the description until the Ph-3+ release lands.*

PMs log into internal and external systems (internal issue tracker, internal messenger, customer Jira, customer portals, customer file storage) using their own personal credentials. The DeliverableHub automation layer acts **on behalf of the PM** — using the PM's credentials to authenticate with these systems to read status, post comments, upload artifacts, and submit deliverables. This ensures that all actions taken by the automation are attributable to the responsible PM, maintaining accountability and audit trails in every external system. *(Ph-3+ target — see scope note above.)*

### 10.1 Design Principles

**PM credentials are used, not service accounts.** When the automation services interact with a customer's Jira (for example), they authenticate as the specific PM assigned to that device — not as a generic service account. This means actions appear in the customer's system under the PM's name, which is both an accountability requirement and a customer expectation. The same applies to internal systems: reminders and status queries in the internal issue tracker appear as the PM's actions.

**Credentials are encrypted at rest and in transit.** No credential is ever stored in plaintext. All credential data in the PMCredentials table is encrypted with AES-256 before being written to storage. The encryption key is managed by a dedicated secrets infrastructure (see below). Credentials in transit between the K8s automation services and external systems are always over TLS.

**PMs manage their own credentials.** Each PM is responsible for registering, updating, and revoking their credentials for each system they use. The system provides a secure UI for this and alerts PMs when credentials are approaching expiry.

### 10.2 Credential Storage Architecture

Credentials are stored in the **PMCredentials** table (see Section 3.4 for schema). However, the `encrypted_credentials` field is not stored in SharePoint — it is stored in a **dedicated secrets store** on the K8s cluster, with only a reference pointer stored in SharePoint. This separation ensures that even if SharePoint data is accessed by unauthorized users, credential material is not exposed.

```
┌────────────────────────┐        ┌────────────────────────────────────┐
│   SharePoint           │        │  HILDA Containerized Services      │
│   (PMCredentials List) │        │  (Docker Compose Ph-1/Ph-2;        │
│                        │        │   MicroK8s Ph-3+)                  │
│  credential_id  ──────────────▶│  ┌──────────────────────────────┐  │
│  user_id               │        │  │  Secrets backend             │  │
│  system_type           │        │  │  Ph-1/Ph-2: sops-encrypted   │  │
│  system_name           │        │  │    files / K8s Secrets       │  │
│  auth_method           │        │  │    [D-019] v1                │  │
│  token_expiry          │        │  │  Ph-3+: HashiCorp Vault      │  │
│  status                │        │  │    [D-019] v2                │  │
│  (NO credential data)  │        │  │                              │  │
│                        │        │  │  Stores:                     │  │
│                        │        │  │  • Ph-1/Ph-2: one shared     │  │
│                        │        │  │    ops-team cred per system  │  │
│                        │        │  │  • Ph-3+: encrypted per-PM   │  │
│                        │        │  │    blobs keyed by            │  │
│                        │        │  │    credential_id             │  │
└────────────────────────┘        │  └──────────────────────────────┘  │
                                  │                                    │
                                  │  ┌──────────────────────────────┐  │
                                  │  │  Credential Service          │  │
                                  │  │  (hilda-api container)       │  │
                                  │  │  Ph-1/Ph-2: retrieve shared  │  │
                                  │  │    ops-team cred             │  │
                                  │  │  Ph-3+: store/retrieve/      │  │
                                  │  │    refresh per-PM creds      │  │
                                  │  └──────────────────────────────┘  │
                                  └────────────────────────────────────┘
```

**Secrets backend by phase:** Ph-1/Ph-2 uses sops-encrypted files committed to the repository, decrypted at deploy time by ops, and provided to the HILDA containers as environment variables / mounted files per `[D-019]` v1. Ph-3+ migrates to HashiCorp Vault (self-hosted on MicroK8s) for encrypted storage, access policies, audit logging, and automatic secret rotation per `[D-019]` v2.

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
| Access control        | Only the Credential Service container can access the secrets store; service-to-service auth via Docker Compose network isolation (Ph-1/Ph-2) or K8s ServiceAccounts + mTLS (Ph-3+) |
| Audit logging         | Every credential retrieval, refresh, and use is logged (without exposing credential material) in the CommunicationLog with the credential_id |
| Credential isolation  | Each PM's credentials are stored under their own path in the secrets store; no PM can access another PM's credentials                        |
| No credential sharing | Automation services never cache decrypted credentials; each use requires a fresh retrieval from the Credential Service                       |
| Revocation            | PM can revoke any credential at any time via the UI; revocation takes effect immediately for all future automation actions                   |
| Rotation support      | PMs are encouraged to rotate API tokens on a configurable schedule; the system tracks last_validated and alerts when tokens are aging        |

---

## 11. Deployment Architecture

### 11.1 Ph-1 / Ph-2 — Docker Compose on bare-metal Linux PC

All automation services run as Docker containers on a single bare-metal Linux PC, orchestrated via `docker-compose.yaml` per `[D-022]` / `[D-025]`.

| Container             | Image source            | Notes                                                                                        |
| --------------------- | ----------------------- | -------------------------------------------------------------------------------------------- |
| `hilda-api`           | `hilda:<version>`       | FastAPI app — REST endpoints for SP web-part calls (FR-56), document enumeration (FR-57), downloads (FR-61), Credential Service surface. Single replica. |
| `hilda-worker`        | `hilda:<version>`       | Celery worker — executes activities (email outreach, LLM review, adapter calls, document classification, submission packaging). Single replica; concurrency tuned per workload. |
| `hilda-beat`          | `hilda:<version>`       | Celery beat scheduler — loads schedule from YAML rule files per `[D-022]` implementation note (Device → Customer → Global resolution per FR-30). Single replica. |
| `hilda-llm-gateway`   | `hilda:<version>`       | LLM Gateway — rate limiting, prompt management, retries; routes to on-prem LLM endpoint per `[D-007]`. Single replica. |
| `postgres`            | official `postgres`     | SharePoint mirror (DeliveryItems, CommunicationLog), FR-31 runtime overrides, resolved AutomationRules snapshot. Single replica with volume-mounted persistence. |
| `redis`               | official `redis`        | Celery broker + cache + dedup per `[D-022]`. Single replica. |

**Volumes / bind-mounts (per `[D-025]`):**
- `./customizations/sharepoint_config/` → read-only mount into `hilda-api`, `hilda-worker`, `hilda-beat`
- `./customizations/template_schemas/` → read-only mount (FR-39/40/41)
- `./customizations/rules/` → read-only mount (FR-30)
- `\\share\hilda\` (NSD) → SMB mount into `hilda-api`, `hilda-worker` for document I/O per `[D-013]`

**Secrets (Ph-1/Ph-2):** sops-encrypted files committed to repo; decrypted at deploy time and provided as env vars / mounted files per `[D-019]` v1. No Vault container in Ph-1/Ph-2.

**Total: 6 containers on a single host.** No HA, no horizontal scaling; sized for v1 single-deployment scale.

### 11.2 Ph-3+ — MicroK8s single-node

Migration to MicroK8s single-node per `[D-022]` / `[D-025]`. Same container images; orchestration upgraded to provide self-healing, RBAC, secrets/ConfigMap management, and a path to multi-node scaling.

| Service                   | K8s Resource                       | Notes                                                                                |
| ------------------------- | ---------------------------------- | ------------------------------------------------------------------------------------ |
| `hilda-api`               | Deployment                         | Same image as Ph-1/Ph-2; service exposed via MetalLB LoadBalancer VIP                |
| `hilda-worker`            | Deployment                         | Scaled by replica count                                                              |
| `hilda-beat`              | Deployment (single replica)         | Celery beat — singleton                                                              |
| `hilda-llm-gateway`       | Deployment                         | LLM rate limiting & retry                                                            |
| Credential Service        | Deployment (singleton)             | Per `[D-019]` v2 — Vault-backed                                                      |
| Credential Health Monitor | CronJob                            | Periodic token refresh and credential validation (Ph-3+ scope per §10)               |
| HashiCorp Vault           | StatefulSet                        | `[D-019]` v2 — encrypted PM credential blobs                                         |
| PostgreSQL                | StatefulSet                        | Rook/Ceph RBD PVC for durable storage                                                |
| RabbitMQ Quorum Queues    | RabbitMQ Cluster Operator          | Celery broker per `[D-022]` Ph-3 migration; replaces Redis-as-broker                 |
| Redis                     | Deployment                         | Cache-only role in Ph-3 (no broker duty)                                             |
| Customizations            | ConfigMap volumes per `[D-025]`    | YAML files migrated from bind-mounts to ConfigMap volumes — no Python change         |
| NSD access                | CSI driver for SMB                 | Same `\\share\hilda\` mount surface                                                  |

The SharePoint layer runs on existing on-prem SharePoint 2017 infrastructure in both phases — no HILDA-side resources required for the UI layer. The NSD runs on existing on-prem file-server infrastructure.

---

## 12. Configurability Architecture

The system uses a **three-tier configuration model**:

**Tier 1 — Global Config:** Shared infrastructure settings (SharePoint site URL, Graph API credentials, LLM API keys, K8s namespace, SSO provider). Set once by platform admins.

**Tier 2 — Customer Config (Templates):** Each customer gets a deliverable template that defines milestones, deliverables, delivery item types, tracking modalities, and customer delivery modalities. These templates live in the SharePoint **CustomerTemplates** list and serve as the reusable blueprint for all devices with that customer. Customer-specific automation rules are also defined at this tier.

**Tier 3 — Device Config (Trackers):** When a PM creates a device tracker from a template, they can override or extend any setting for that specific device — adding delivery items, changing deadlines, reassigning owners. These overrides are captured in the device-specific rows in the SharePoint Lists.

This hierarchy means onboarding a new customer is a **template creation exercise**, and onboarding a new device is a **template instantiation exercise** — no code changes, no new deployments.

**Schema vs. content boundary:** The three-tier configurability above moves **content** within an existing schema (templates, rules, scheduling, CC lists, modalities, TG groupings). **Schema changes** — adding a new field on DeliveryItems, a new `item_type` value, a new `delivery_state`, a new `trigger_event` — are a different category of change that requires a HILDA code release per §3.5. The YAML files in `customizations/` are deliberately scoped to the "what can change without core code" zone; the canonical schema is gated by HILDA dev/ops and propagates through Pydantic models → SP List provisioning → Postgres migration → YAML template-schema spec at release time.

---

## 13. Implementation Roadmap

The roadmap uses phase-based scoping (Ph-1 / Ph-2 / Ph-3+) rather than calendar-month commitments. Each phase corresponds to a versioned release. Items are scoped to phases by `[Ph-N]` tags in `requirements.md`; this section summarizes per-phase deliverables. Detailed FR scoping in `requirements.md` is authoritative — this section is a narrative overview.

### Ph-1 — Single-customer foundation

**Goal:** end-to-end tracker → outreach → ingest → review → submit loop for a single customer on Docker Compose, single deployment.

- **Infrastructure:** bare-metal Linux PC; Docker Compose stack with `hilda-api`, `hilda-worker`, `hilda-beat`, `hilda-llm-gateway`, `postgres`, `redis` per §11.1. sops-encrypted secrets per `[D-019]` v1.
- **Data model:** SharePoint Lists for Customers, Devices, Milestones, DeliveryItems, Users, PMCredentials (metadata), CommunicationLog. No Deliverables table (D-028). YAML files in `customizations/template_schemas/` and `customizations/rules/` per FR-30 / FR-39/40/41 / `[D-025]`. Postgres mirror for fast queries.
- **NSD:** two-tree structure (`inbound/` + `internal/`) per `[D-013]` / `[D-041]`; HILDA-mediated downloads (`https://hilda.corp/dl/<token>`) per FR-61 / NFR-16.
- **Tracker creation:** from-template flow per FR-1(a) / FR-2 (Excel import deferred per DEF-15).
- **Email Service:** dedicated mailbox, per-owner BATCH-id reference tags (FR-9 / FR-24), structured reply blocks, free-text fallback parsing per FR-12.
- **PLM adapter:** corp PLM via API Spec Ingestor `[D-003]`; one issue per (owner × milestone) per FR-26 / `[D-035]`.
- **NSD ingest:** owner-drop monitoring per FR-55; document classification per FR-52 + `[D-039]`.
- **Customer JIRA adapter:** read-only polling per FR-25.
- **Document review:** rule-based test-report parser per FR-16 + `[D-011]`; LLM quality review per FR-53 (gated by `review_required` per FR-2).
- **PM/TPM SharePoint UI:** milestone view (FR-56), document section with parser + LLM findings (FR-59/FR-60), Start Collection (FR-8), Approve (FR-56), Submit to Carrier (FR-63), Close All Items (FR-64), Send Reminder (FR-65).
- **Customer adapter:** first-customer submission per FR-18/FR-19/FR-20; carrier `portal_structure.yaml` per FR-69.
- **Credentials:** single shared ops-team credential set per system per `[D-019]` v1; no per-PM flows.
- **Submission flow:** PLM upload immediate on ingest; assembly from NSD; carrier dispatch.

### Ph-2 — Multi-source intelligence, multi-revision

**Goal:** richer document handling (revisions, ambiguity resolution), corp messenger inbound, multi-customer, self-close UI.

- **Multi-revision document handling** per FR-17 / `[D-039]` / `[D-040]` (NSD source-of-truth; deferred PLM upload; sync verification per FR-68).
- **Mailto: tap-link replies** per FR-12 path (b); subject-encoded status per FR-24.
- **Corp messenger inbound** per FR-54 — LLM classification, manual-triage flag on dashboard.
- **ZIP archive ingest** per FR-72; `staged/` holding for ambiguous documents per FR-52 Step 3.
- **Owner self-close** in SP UI per FR-56; **version-selection workflow** for multi-revision items per FR-66.
- **SP UI document upload** per FR-62; **PM annotates resolution path** per FR-47.
- **Customer adapter expansion:** second and third customers; AI-drafted customer responses (deferred pending DEF-19/DEF-20 revisit).
- **Template library:** multiple customer templates in production; validate template-to-tracker scaling.
- **Excel-import flow** per FR-1(b) revisit pending DEF-15.

### Ph-3+ — Per-PM credentials, MicroK8s, automated closure

**Goal:** operational maturity — credential delegation, orchestration upgrade, automated downstream actions.

- **MicroK8s single-node migration** per `[D-022]` / `[D-025]` §11.2. RabbitMQ Quorum Queues replace Redis-as-broker; Rook/Ceph PVCs; MetalLB VIP; ConfigMap volumes for customizations.
- **Per-PM credential management** — Section 10 target state: OAuth2 / API-token flows, PM-owned encrypted blobs in HashiCorp Vault per `[D-019]` v2, Credential Health Monitor, per-item `customer_delivery_credential_id` mapping.
- **Automated customer feedback capture** per DEF-19 (formerly FR-21) — carrier portal + email feedback ingestion.
- **Automated `Closed` transition** per DEF-20 (formerly FR-22) — carrier-acceptance signal detection + PM confirmation.
- **Automated resolution-path actions** per DEF-17 — auto-create Tech Report DeliveryItems, monitor `waiver_ref`.
- **Filesystem identity attribution for NSD drops** per DEF-16.
- **Per-DeliveryItem ACL** on HILDA-mediated download links per DEF-18 (currently any authenticated corp AD user per FR-61).
- **Browser-automation adapter** for customers without APIs.
- **Advanced analytics, AI status summarization, self-service template wizard.**

**Deferred items**: see `## Deferred` in `requirements.md` for the canonical list with DEF-N IDs.

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
