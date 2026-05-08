# Playbook: debug-pipeline

**Purpose**: run one or more HILDA processing stages against a customer's data; capture
per-stage statistics and surface drift from expected counts or error codes.

**Input**: stage(s) to run, customer slug, and optionally a device or milestone scope.

**Note**: full end-to-end pipeline assembly is deferred until Layer 4–6 modules are
implemented. At current (Layer 1–2), you can run individual module CLIs in sequence.
As more modules land, this playbook grows to cover them.

## Stages (add rows as modules are implemented)

| Stage | CLI | Available |
|---|---|---|
| SP connectivity | `sharepoint_integration_cli --diagnostic` | yes |
| Schema validate | `template_schema_cli --validate` | yes |
| Error-code registry | `diagnostics_cli --validate` | yes |
| Template ingest | `template_schema_ingestor_cli --infer` | pending |
| Test report profile | `test_report_profiler_cli --profile` | pending |
| API spec ingest | `api_spec_ingestor_cli --ingest` | pending |
| Rule engine eval | `rule_engine_cli --eval` | pending |
| Email dispatch | `email_service_cli --dry-run` | pending |

## Steps

1. For each stage in the requested range, run the CLI with `--diagnostic`:
   ```
   python -m core.src.<module>.<module>_cli --diagnostic [--customer <slug>]
   ```
2. Capture per-stage output:
   - Key counts (items processed, errors, warnings)
   - Elapsed time
   - Error codes emitted
3. If two runs are available for comparison, compute deltas for salient counters.
4. If any stage fails, capture the error code(s) and note the stage name. Do NOT include
   the exception message body or stack trace — error codes only.

## Output: `PIPE-RPT` report shape

```
PIPE-RPT v=1 cust=<CUST0> stages=<first>..<last>
<stage>: <metric>=<value> <metric>=<value> elapsed=<ms>ms
<stage>: ...
errors:  <CODE>:<count>; ... [or "(none)"]
delta:   <metric>=<+/-N> vs prior [if prior run exists]
notes:   <≤15-word observation, optional>
```

## Example (illustrative, available stages only)

```
PIPE-RPT v=1 cust=<CUST0> stages=sp-connect..schema-validate
sp-connect:      lists=5/5 items_total=195 elapsed=241ms
schema-validate: entities=4 rules=2 warnings=1 elapsed=12ms
errors:  TSC-W001:1
delta:   items_total=+3 vs prior_run
```

## Constraints

- **Maximum 25 lines** in the output.
- Apply mapping to all customer/device identifiers.
- For error codes: list code + count only. Never the exception body or stack trace.
- Delta lines: only include metrics that changed from prior run.
- If a stage is pending implementation: omit it from the report (do not run, do not list).

## Common follow-ups Teacher LLM may request after PIPE-RPT

- "Re-run sp-connect with `--verbose` and report item count per list" → SP-RPT with
  per-list item counts.
- "Which rules fired during rule-engine eval?" → note rule count and scope only, no
  rule names if they contain customer-specific terminology.
- "Run with `--dry-run` to confirm no writes occurred" → another PIPE-RPT with a
  `dry_run: yes` field on the first line.
