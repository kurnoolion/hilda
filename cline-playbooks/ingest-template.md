# Playbook: ingest-template

**Purpose**: run the Template Schema Ingestor on a customer's Excel delivery template;
capture column detection, entity mapping, and schema output stats.

**Input**: path to the customer template file on this machine, plus the customer slug.

**Prerequisites**: `template_schema_ingestor` module implemented (check `docs/compact/STATUS.md`).
Customer template file placed at `<env_dir>/input/templates/<TMPL0>.xlsx`.

> **Status**: `template_schema_ingestor` module not yet implemented as of 2026-05-08.
> Run this playbook once the module's MODULE.md has been drafted and code committed.

## Steps

1. Add the template file to the mapping (run `mapping.md` first if the file name is new).
   The file itself stays at `<env_dir>/input/templates/` — never copy it into the repo.

2. Run the ingestor in `--infer` mode (LLM-assisted column resolution):
   ```
   python -m core.src.template_schema_ingestor.template_schema_ingestor_cli \
     --infer \
     --input <env_dir>/input/templates/<real_filename>.xlsx \
     --customer <customer_slug> \
     --diagnostic
   ```

3. Capture from stdout:
   - Total columns detected, mapped, unmapped
   - Entity types recognized (Device / Milestone / Deliverable / DeliveryItem counts)
   - AutomationRules extracted (count + scope)
   - Output schema path written (redact customer parts)
   - Any error codes (`TSI-E*`, `TSC-E*`, `TSI-W*`)
   - LLM call count and elapsed time

4. Read the generated `customizations/template_schemas/<customer_slug>/schema.yaml`
   to verify it exists and has a non-zero column count. Do NOT read the cell values —
   only structural metadata.

5. Optionally run in `--validate` mode to confirm the generated schema validates against
   the meta-schema:
   ```
   python -m core.src.template_schema_ingestor.template_schema_ingestor_cli \
     --schema-file customizations/template_schemas/<customer_slug>/schema.yaml \
     --validate
   ```

## Output: `TSI-RPT` report shape

```
TSI-RPT v=1 tmpl=<TMPL0> cust=<CUST0> mode=infer|schema-file
cols:    total=<N> mapped=<N> unmapped=<N>
entities: Device=<N> Milestone=<N> Deliverable=<N> DeliveryItem=<N>
rules:   AutomationRules=<N> scope=<Global|Customer|Device>
schema:  written <path-in-repo>
llm:     calls=<N> elapsed=<s>s
validate: pass|fail [fail: <count> violations]
errors:  <CODE>:<count>; ... [or "(none)"]
```

## Constraints

- **Maximum 18 lines** in the output.
- The template file content (cell values, column name text) must NEVER appear. Report only
  counts and structural metadata.
- `<path-in-repo>` for the schema output is safe to include unredacted since it uses the
  customer slug from the repo, not the actual customer name.
- LLM calls should be counted and elapsed — this helps Teacher LLM tune the prompt
  without seeing the content.

## Common follow-ups Teacher LLM may request after TSI-RPT

- "Re-run with `--row-offset 3` if columns weren't detected on row 1" → another TSI-RPT.
- "Commit the generated schema.yaml" → `git add customizations/template_schemas/...`,
  then report the commit hash.
- "Show me the unmapped column names" → redact names as `<COL{N}>` and add to mapping;
  report as `MAP` update + `TSI-RPT` addendum.
