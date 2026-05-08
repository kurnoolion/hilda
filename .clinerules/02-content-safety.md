# Content safety: nothing proprietary leaves the on-prem machine

The user reads your reports off your screen and hand-types into Teacher LLM. Teacher LLM
must NEVER see verbatim customer names, device names, PM identities, delivery item content,
template cell values, test report body text, proprietary system names, or any SP site URL.

## Redaction protocol

You maintain a literal-string mapping at `<env_dir>/state/hilda-mapping.json`. Apply it
forward (real → placeholder) before emitting any report, and reverse (placeholder → real)
when acting on Teacher LLM's response.

**Mapping schema**:

```json
{
  "version": 1,
  "mappings": {
    "<real-string>": "<placeholder>"
  }
}
```

**Placeholder format** — angle-bracketed, category-prefixed, stable index:

| Category | Pattern | Example |
|---|---|---|
| Customer name | `<CUST{N}>` | `Acme Corp` → `<CUST0>` |
| Device name | `<DEV{N}>` | `Widget Pro X1` → `<DEV0>` |
| Milestone name | `<MIL{N}>` | (only if milestone names are customer-proprietary) |
| PM / TPM name or email | `<PM{N}>` | `john.doe@corp.com` → `<PM0>` |
| SP site URL | `<SPURL{N}>` | `https://sp2017.corp/sites/acme` → `<SPURL0>` |
| SP list name (customer-specific) | `<LIST{N}>` | `Acme - Device Tracker` → `<LIST0>` |
| SP column name (customer-specific) | `<COL{N}>` | `Acme_Launch_Gate` → `<COL0>` |
| Customer template file | `<TMPL{N}>` | `acme_template_v3.xlsx` → `<TMPL0>` |
| Test report file | `<TRPT{N}>` | `OA_baseline_2024.xlsx` → `<TRPT0>` |
| Proprietary system name | `<SYS{N}>` | `CorpMessenger` → `<SYS0>` |
| Customer slug (if not already a CUST placeholder) | `<CSLUG{N}>` | `acme-corp` → `<CSLUG0>` |

`{N}` is a stable index — once allocated, never changes. New entries get the next free index.

Apply substitution **longest-match-first** so multi-word names match before their substrings.

The mapping playbook (`cline-playbooks/mapping.md`) describes how to seed and grow this file.

## Hard rules — never include in your report

- Verbatim customer names, device names, PM names or email addresses
- Delivery item description text or status notes (any length)
- Test report section headings, body text, or requirement IDs
- Template cell values, column names that are customer-specific business terminology
- SP site URLs or list names that identify a customer
- Proprietary system names, API endpoint paths, or field names
- Un-redacted file paths under `<env_dir>/input/<...>`

## Standard SP column names (OK to include without redaction)

These are SharePoint infrastructure names, not customer data:
`Title`, `ID`, `Created`, `Modified`, `Author`, `Editor`, `ContentType`, `Status`,
`_ModerationStatus`, `GUID`.

Generic HILDA column names defined in core (not customer-specific) are also OK:
`PM_Owner`, `Target_Launch_Date`, `Path_Slug`, `Owner_Email`, `Expected_Completion`,
`Tracking_Modality`, `Customer_Delivery`.

When in doubt: redact.

## OK to include (after redaction)

- Counts, percentages, ratios
- HILDA error codes (`SHP-E001`, `TSC-W001`, etc.)
- SP field types (`Text`, `Number`, `DateTime`, `Choice`, `Lookup`)
- Schema structural shape — column count, entity-type count, list count
- Generic regex patterns — `^\d{4}-\d{2}-\d{2}$` is fine
- Source code paths inside the repo — `core/src/sharepoint_integration/list_crud.py:42`
- Public standards references — SP 2017 REST API endpoint patterns are public knowledge

## What this protects against

A passing observer of the user's hand-typed reports — and Teacher LLM itself, which logs
conversations — never sees any string that uniquely identifies a customer, PM, device, or
specific delivery item. Patterns and counts are sufficient for Teacher LLM to update code;
concrete values stay on-prem.
