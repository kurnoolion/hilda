# Playbook: ingest-api-spec

**Purpose**: run the API Spec Ingestor on a proprietary API spec file; capture endpoint
detection, adapter scaffold generation, and conformance-check stats against the `IssueTracker`
or `Messenger` Protocol.

**Input**: path to API spec file on this machine, target Protocol (`issue_tracker` or
`messenger`), and the system name.

**Prerequisites**: `api_spec_ingestor` module implemented (check `docs/compact/STATUS.md`).
API spec file placed under `<env_dir>/input/api-specs/`.

> **Status**: `api_spec_ingestor` module not yet implemented as of 2026-05-08.
> Run this playbook once the module's MODULE.md has been drafted and code committed.
> The first end-to-end exercise is the proprietary internal messenger adapter per `[D-016]`.

## Steps

1. Add the system name and spec file name to the mapping (run `mapping.md` first if new).
   The spec file stays at `<env_dir>/input/api-specs/` — never copy into the repo.

2. If not already OpenAPI 3.x format, run the normalizer first:
   ```
   python -m core.src.api_spec_ingestor.api_spec_ingestor_cli \
     --normalize <env_dir>/input/api-specs/<real_spec_file> \
     --output <env_dir>/input/api-specs/<real_spec_file>.normalized.yaml \
     --diagnostic
   ```

3. Run the ingestor against the (normalized) spec:
   ```
   python -m core.src.api_spec_ingestor.api_spec_ingestor_cli \
     --ingest <env_dir>/input/api-specs/<normalized_spec> \
     --protocol <issue_tracker|messenger> \
     --system <system_slug> \
     --output customizations/<system>/adapter.py \
     --diagnostic
   ```

4. Capture from stdout:
   - Endpoints detected in spec
   - Protocol methods covered vs total required
   - Adapter lines generated
   - Protocol conformance check result (pass / fail + missing methods)
   - LLM call count and elapsed time
   - Any error codes (`ASI-E*`, `ASI-W*`, `ITR-E*`, `MSG-E*`)

5. If conformance check fails: note missing method names (they are Protocol-defined,
   not proprietary — safe to include unredacted, e.g., `send_message`, `list_thread`).

## Output: `ASI-RPT` report shape

```
ASI-RPT v=1 sys=<SYS0> protocol=issue_tracker|messenger
spec:    endpoints=<N> format=openapi3|other
norm:    pass|skip [other→openapi3 in <s>s]
generate: lines=<N> methods=<N>/<N>_required
conform: pass|fail [missing: <method1>, <method2>]
output:  customizations/<system>/adapter.py
llm:     calls=<N> elapsed=<s>s
errors:  <CODE>:<count>; ... [or "(none)"]
```

## Constraints

- **Maximum 15 lines** in the output.
- Never include: endpoint URL paths, request/response field names, auth scheme details,
  or any other proprietary spec content.
- `protocol` method names (e.g., `send_message`, `get_issue`, `list_thread`) are defined
  in `core/src/` and are safe to include unredacted.
- The output adapter path (`customizations/<system>/adapter.py`) is safe — it uses the
  system slug, not a verbatim proprietary name.

## Common follow-ups Teacher LLM may request after ASI-RPT

- "Re-run normalizer with `--strict` and check if endpoint count changes" → another ASI-RPT
  with `norm` field showing before/after endpoint counts.
- "Show me which Protocol methods are unimplemented" → conformance fail lines already
  list missing method names — relay the `conform:` line only.
- "Commit the generated adapter" → `git add customizations/<system>/adapter.py`, then
  report commit hash (not the adapter content).
