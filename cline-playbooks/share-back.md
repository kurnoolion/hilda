# Playbook: share-back

**Purpose**: bundle multiple reports from this session into a single `BUNDLE` for one
typing trip into Teacher LLM.

**Input**: the reports produced so far this session (ORIENT, SP-RPT, TSI-RPT, etc.).

## When to use

Use `share-back` when you have produced 2+ reports in a session and want to hand them to
Teacher LLM in a single paste rather than multiple separate conversations. A BUNDLE is
≤40 lines — if the combined reports exceed that, split into two bundles.

## Steps

1. Collect all reports from this session that need to go to Teacher LLM. Exclude:
   - `MAP` reports (the `added:` lines contain real values — map reports stay on-prem;
     only include the summary line: `mapping: updated v=<N> +<count>`).
2. For each report, verify all tokens are redacted (no real customer names, URLs, etc.).
3. Emit the BUNDLE in section order: ORIENT first, then other reports, mapping summary last.
4. Add a one-line `context:` at the top if Teacher LLM needs to know which task prompted
   the run (e.g., `context: first real SP connect for <CUST0>`).

## Output: `BUNDLE` report shape

```
BUNDLE v=1 session=<YYYY-MM-DD> reports=<N>
context: <≤15-word task description>

--- ORIENT ---
<ORIENT report lines>

--- SP-RPT ---
<SP-RPT report lines>

--- TSI-RPT ---
<TSI-RPT report lines>

mapping: updated v=<N> +<count>
```

## Constraints

- **Maximum 40 lines** total across the BUNDLE.
- Each embedded report keeps its own `--- TYPE ---` header so Teacher LLM can parse it.
- If including a partial or abbreviated report, prefix the header:
  `--- SP-RPT (summary only) ---`
- Never include raw mapping entries (real→placeholder pairs). Only the `mapping:` summary.
- If total lines would exceed 40, split the BUNDLE: emit BUNDLE-1 and BUNDLE-2 separately;
  the user types them in sequence.

## Constraints on what to omit

If any single report is very long (>15 lines), trim it by:
- Dropping `notes:` lines if they were informational-only
- Collapsing per-list lines to a single `lists: <N>/N ok` if no drift
- Keeping the `errors:` line always

## Example (illustrative)

```
BUNDLE v=1 session=2026-05-09 reports=2
context: first real SP connect + template ingest for <CUST0>

--- ORIENT ---
ORIENT v=1
phase: architecture
in_progress: 0
next: 7
flags: 2
mapping: v=1 entries=5
ready: yes

--- SP-RPT ---
SP-RPT v=1 cust=<CUST0> auth=ntlm
url:     <SPURL0>
connect: ok elapsed=241ms
lists:   5/5
errors:  (none)

--- TSI-RPT ---
TSI-RPT v=1 tmpl=<TMPL0> cust=<CUST0> mode=infer
cols:    total=14 mapped=14 unmapped=0
entities: Device=3 Milestone=2 Deliverable=4 DeliveryItem=5
rules:   AutomationRules=2 scope=Customer
schema:  written customizations/template_schemas/<CSLUG0>/schema.yaml
llm:     calls=6 elapsed=8.4s
errors:  (none)

mapping: updated v=1 +5
```
