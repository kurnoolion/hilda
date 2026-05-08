# Playbook: mapping

**Purpose**: seed or update the redaction mapping at `<env_dir>/state/hilda-mapping.json`.
Run this before the first on-prem session involving a new customer, device, PM, or
proprietary system name.

**Input**: the real names/values to add to the mapping (provided by the user).

## Steps

1. If `<env_dir>/state/hilda-mapping.json` exists, read it. Otherwise start from:
   ```json
   { "version": 1, "mappings": {} }
   ```
2. For each real value the user provides, check if it is already in the mapping.
   - If yes: note the existing placeholder, no change needed.
   - If no: allocate the next free index for that category and add the entry.
3. Write the updated mapping back to `<env_dir>/state/hilda-mapping.json`.
4. Apply longest-match-first ordering mentally — ensure multi-word names are listed before
   their substrings so substitution works correctly.

## Category index table (maintain across sessions)

| Category | Prefix | Next free index |
|---|---|---|
| Customer name | `CUST` | (check mapping) |
| Device name | `DEV` | (check mapping) |
| Milestone name | `MIL` | (check mapping) |
| PM / TPM name or email | `PM` | (check mapping) |
| SP site URL | `SPURL` | (check mapping) |
| SP list name | `LIST` | (check mapping) |
| SP column name (customer-specific) | `COL` | (check mapping) |
| Customer template file | `TMPL` | (check mapping) |
| Test report file | `TRPT` | (check mapping) |
| Proprietary system name | `SYS` | (check mapping) |
| Customer slug | `CSLUG` | (check mapping) |

## Output: `MAP` report shape

```
MAP v=<new-version>
added: <count> entries
  <real-value> → <placeholder>    (one line per new entry, real values shown only here)
total: <N> entries
```

Emit the `MAP` report to the screen — the user reads it to verify the mapping is correct.
Do NOT include this report in any `BUNDLE` passed to Teacher LLM; the real values appear
here and must stay on-prem.

## Constraints

- The mapping file is **on-prem only**. Never commit it to git. It must live entirely
  under `<env_dir>/state/`.
- The `MAP` report itself (with real values on the `added:` lines) is also **on-prem only**
  — the user verifies it locally, does NOT hand-type it to Teacher LLM.
- When bundling for Teacher LLM, only report the count: `mapping: updated v=<N> +<count>`.
