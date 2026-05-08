# Playbook: sp-connect

**Purpose**: test real SharePoint connectivity for a customer; inspect list inventory,
schemas, and item counts. Surface auth errors, missing lists, or column-mapping drift.

**Input**: customer slug (e.g., the slug in `customizations/sharepoint_config/customers/<slug>.yaml`).

**Prerequisites**: SP environment vars set (`HILDA_SP_SITE_URL`, `HILDA_SP_AUTH_TYPE`,
`HILDA_SP_USERNAME`, `HILDA_SP_PASSWORD` or keytab). Customer YAML exists under
`customizations/sharepoint_config/customers/<slug>.yaml`.

## Steps

1. Confirm env vars are set:
   ```
   echo $HILDA_SP_SITE_URL   # should print real URL — redact in report as <SPURL0>
   echo $HILDA_SP_AUTH_TYPE  # ntlm or kerberos
   ```
2. Run the SP diagnostic CLI (read-only, no writes):
   ```
   python -m core.src.sharepoint_integration.sharepoint_integration_cli \
     --diagnostic --customer <customer_slug>
   ```
3. Capture from stdout:
   - Connection status and round-trip time
   - Lists found vs lists expected (from customer YAML)
   - For each list: item count, column count
   - Any error codes emitted (`SHP-E*`, `SHP-W*`)
4. For each list that has a column mismatch (expected column not in SP): note column index
   number (`<COL{N}>` from mapping) and mismatch type (`missing` / `wrong_type`).
5. If auth fails (`SHP-E004`): note the auth method attempted and the error detail line
   (redact any credential fragment).

## Output: `SP-RPT` report shape

```
SP-RPT v=1 cust=<CUST0> auth=<auth_type>
url:     <SPURL0>
connect: ok|fail elapsed=<ms>ms
lists:   <found>/<expected>
  <LIST0>: items=<N> cols=<N> [drift: <COL{N}>=missing|wrong_type, ...]
  <LIST1>: items=<N> cols=<N>
  ...
errors:  <CODE>:<count>; ... [or "(none)"]
notes:   <≤15-word observation, optional>
```

## Example (illustrative)

```
SP-RPT v=1 cust=<CUST0> auth=ntlm
url:     <SPURL0>
connect: ok elapsed=241ms
lists:   5/5
  <LIST0>: items=23 cols=8
  <LIST1>: items=4 cols=6
  <LIST2>: items=156 cols=11
  <LIST3>: items=12 cols=7
  <LIST4>: items=0 cols=5
errors:  (none)
```

## Constraints

- **Maximum 15 lines** in the output.
- Never include actual SP site URL (always `<SPURL{N}>`).
- Never include actual list names (always `<LIST{N}>`).
- Column mismatch lines: use `<COL{N}>` for customer-specific column names only; standard
  SP columns (`Title`, `Status`, `ID`) may appear unredacted.
- If `auth=kerberos` and kinit is needed, note `kinit_required: yes` — do NOT include
  keytab path or username in the report.

## Common follow-ups Teacher LLM may request after SP-RPT

- "Add the missing list to the customer YAML and re-run" → edit YAML, re-run sp-connect.
- "What is the column type for `<COL0>`?" → run with `--list <LIST0>` flag (if supported)
  or inspect the SP admin UI and report as `SP-LIST`.
- "Run with `--dry-run` and verify a single read round-trip" → another SP-RPT.
