# Playbook: develop-issue-tracker-adapter

**When to run**: Teacher LLM has committed a new or updated IssueTracker adapter scaffold
(either `core/src/issue_tracker/` implementation or `customizations/issue_tracker/<slug>_adapter.py`).
Run to verify it compiles, passes unit tests, and passes the full C01–C10 contract suite.

**Adapter slug**: the `<slug>` used below is the filename prefix of the adapter, e.g.:
- `jira` → `core/src/issue_tracker/jira_adapter.py`
- `proprietary` → `customizations/issue_tracker/proprietary_adapter.py`

---

## Steps

### 1. Pull latest code

```
git pull
```

Confirm new/changed files in `core/src/issue_tracker/` or `customizations/issue_tracker/`.

### 2. Verify module imports cleanly

```
python -c "from core.src.issue_tracker import IssueTracker, IssueRef, MockIssueTracker; print('OK')"
```

If this fails: stop and report the import error. Do not proceed.

### 3. Run unit tests (core module)

```
pytest core/tests/test_issue_tracker.py -v
```

Capture: number passed / failed. If any fail: stop and report.

### 4. Run CLI diagnostic (mock mode — no external calls)

```
python -m core.src.issue_tracker.issue_tracker_cli --diagnostic
```

Capture the ITR-RPT line emitted.

### 5. Run mock dry-run cycle

```
python -m core.src.issue_tracker.issue_tracker_cli --mock --dry-run
```

Capture the ITR-RPT line. Expected: `ops_attempted=4|ops_ok=4|ops_fail=0`.

### 6. Run contract suite — mock adapter (always runs, no credentials needed)

```
pytest customizations/issue_tracker/tests/test_contract.py \
    --adapter mock --project TEST -v
```

Capture: tests passed / failed. All 10 contract test classes must pass before testing a real adapter.

### 7. Run contract suite — target adapter (skip if adapter is "mock")

Set required env vars for the target adapter, then:

```
python -m core.src.issue_tracker.issue_tracker_cli \
    --contract --adapter <slug>
```

This runs the 10-check CLI contract suite (C01–C10) against the real system and emits an ITR-RPT:
```
RPT|ITR|run-XXXXX|<timestamp>|adapter=<slug>|tests=10|passed=N|failed=M|fail_methods=<list>
```

Also run the pytest contract suite against the real adapter:

```
pytest customizations/issue_tracker/tests/test_contract.py \
    --adapter <slug> --project <PROJECT_KEY> -v
```

### 8. Produce ITR-RPT for Teacher

Bundle the results for Teacher LLM. Use `cline-playbooks/share-back.md` if combining with
other reports.

---

## Report format (ITR-RPT)

One block per step:

```
--- ITR-RPT ---
step: import_check  result: ok|fail
step: unit_tests    passed: N  failed: M
step: cli_diag      <paste RPT line>
step: mock_dry_run  <paste RPT line>
step: mock_contract passed: N  failed: M
step: real_contract adapter=<slug>  tests=10  passed=N  failed=M  fail_methods=<C03,C09,...>
```

If `failed > 0` at any step: stop at that step and report. Do not proceed to the next step.
The `fail_methods` field tells Teacher exactly which contract checks to fix.

---

## Troubleshooting guide

| Symptom | Likely cause | What to report to Teacher |
|---|---|---|
| `ImportError` on step 2 | Missing `__init__.py` or wrong import path | Full traceback |
| `ITR-E006` on `--contract` | `make_adapter()` not exported from `<slug>_adapter.py` | Error line + adapter file path |
| C01 fails (create → get) | `get_issue` not returning correct fields | fail_methods=C01 + any error code emitted |
| C03 fails (transition) | `transition_map` not configured for "start"/"resolve" | fail_methods=C03 + ITR-E004 if raised |
| C09 fails (idempotency) | Adapter creates duplicates instead of returning existing ref | fail_methods=C09 |
| C10 fails (error surface) | Adapter raises generic exception instead of ITR-E002 | fail_methods=C10 |
| Rate limit hit during contract | ITR-W001 in output | Report `retry_after_s` value; re-run after delay |
| Auth failure | ITR-E001 in output | Check env vars; do not include credential values in report |

---

## Redaction rules

- Report adapter slug (e.g. `adapter=jira`) — OK.
- Report error codes (e.g. `ITR-E001`) — OK.
- **Do NOT include**: issue titles, descriptions, comment bodies, attachment filenames from the real system, or any credential values.
- Test issue content created by this playbook uses only `"C01 summary"` etc. — those are safe to include.
