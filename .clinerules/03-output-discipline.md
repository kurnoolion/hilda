# Output discipline: compact, hand-typeable reports

The user reads your report off your screen and **hand-types** the redacted version into
Teacher LLM. Reports MUST be short, structured, and easy to read off a single screen.

## Constraints

- **Maximum 30 lines** per report (target 15)
- **Tabular over prose** wherever possible
- **One observation per line**
- **Fixed format per playbook** — each playbook defines its report shape exactly
- **Numbers, not adjectives** — `3/5 lists` not `most lists`; `412ms` not `slow`

## Standard report types

Each playbook produces one of these:

| Type | Used by | Lines | Shape |
|---|---|---|---|
| `ORIENT` | orient | 5–8 | session-bootstrap confirmation |
| `MAP` | mapping | 2–4 | mapping diff confirmation |
| `SP-RPT` | sp-connect | 10–15 | SP connection health + list inventory |
| `SP-LIST` | sp-connect (detailed) | 8–12 | per-list schema + item count |
| `TSI-RPT` | ingest-template | 12–18 | Template Schema Ingestor run stats |
| `TRP-RPT` | profile-test-report | 12–15 | Test Report Profiler run stats |
| `ASI-RPT` | ingest-api-spec | 10–15 | API Spec Ingestor run stats |
| `PIPE-RPT` | debug-pipeline | 15–25 | multi-stage pipeline stats |
| `BUNDLE` | share-back | ≤40 | aggregation of multiple reports |

The exact field set per type is defined in the corresponding playbook.

## Conventions

- **Leading line is the report-type marker**: `SP-RPT v=1 cust=<CUST0>` — first token
  names the type so Teacher LLM can parse instantly.
- **Field=value pairs**: `lists=5 items=312 auth=ntlm` — shorter than prose, no ambiguity.
- **Placeholders only** for any redacted token: never emit a real value.
- **No prose conclusions**: don't write "this looks misconfigured" — Teacher LLM interprets.
- **Emit `MAPPING:` lines inline** when you add a new entry to the redaction mapping
  during this report: `MAPPING: added "Acme Corp"→<CUST0>` (one line per addition).
- **HILDA error codes only** for errors: `SHP-E001`, `TSC-W002` — never raw exception text.

## What NOT to include

- Prose explanations of what a result means
- Speculation, interpretation, recommendations
- Full file contents (paths only)
- Any token in unredacted form
- Per-instance breakdowns (aggregate by category, never list verbatim instances)
- Long examples — if Teacher LLM needs an example, they will ask for one specific case

## Example layouts

**SP-RPT** (good):
```
SP-RPT v=1 cust=<CUST0> auth=ntlm
url:     <SPURL0>
connect: ok elapsed=241ms
lists:   5 found 5 expected
  <LIST0>: items=23 cols=8
  <LIST1>: items=4 cols=6
  <LIST2>: items=156 cols=11
  <LIST3>: items=12 cols=7
  <LIST4>: items=0 cols=5
errors:  (none)
```

**SP-RPT** (bad — leaks content):
```
SP-RPT v=1 cust=Acme Corp
url: https://sp2017.corp/sites/acme
connect: ok
lists: "Acme - Device Tracker" has 23 items   ← verbatim list name
```

**TSI-RPT** (good):
```
TSI-RPT v=1 tmpl=<TMPL0> cust=<CUST0> mode=infer
cols:    total=14 mapped=14 unmapped=0
entities: Device=3 Milestone=2 Deliverable=4 DeliveryItem=5
schema:  written customizations/template_schemas/<CSLUG0>/schema.yaml
rules:   AutomationRules=2 scope=Customer
errors:  TSC-W001:1
elapsed: 8.4s
```
