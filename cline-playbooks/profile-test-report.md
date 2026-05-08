# Playbook: profile-test-report

**Purpose**: run the Test Report Document Profiler on historical proprietary test reports;
capture document format detection, parse coverage, and `final | interim` classification stats.

**Input**: path to test report file(s) on this machine, plus the customer slug.

**Prerequisites**: `test_report_profiler` module implemented (check `docs/compact/STATUS.md`).
Test report files placed under `<env_dir>/input/test-reports/`.

> **Status**: `test_report_profiler` module not yet implemented as of 2026-05-08.
> Run this playbook once the module's MODULE.md has been drafted and code committed.
> Architecture-phase decision needed first: PDF extraction library (`pdfplumber` / `pypdf` /
> `pymupdf`) and legacy `.doc` handler (`antiword` / LibreOffice headless).

## Steps

1. Add the report file name to the mapping (run `mapping.md` first if new).
   The file stays at `<env_dir>/input/test-reports/` — never copy into the repo.

2. Run the profiler in diagnostic mode:
   ```
   python -m core.src.test_report_profiler.test_report_profiler_cli \
     --profile <env_dir>/input/test-reports/<real_filename> \
     --customer <customer_slug> \
     --diagnostic
   ```

3. Capture from stdout:
   - Document format detected (PDF / Excel / Word / legacy .doc)
   - Parse coverage: sections found, tables found, items extracted
   - Classification result: `final` or `interim` + confidence
   - Any error codes (`TRP-E*`, `TRP-W*`)
   - LLM call count and elapsed time (if LLM was used)

4. If multiple files: run per-file and aggregate counts for the report.

## Output: `TRP-RPT` report shape

```
TRP-RPT v=1 trpt=<TRPT0> cust=<CUST0>
format:  pdf|xlsx|docx|doc
pages:   <N> [or sheets=<N> for xlsx]
parse:   sections=<N> tables=<N> items=<N>
classify: final|interim confidence=<float>
llm:     calls=<N> elapsed=<s>s
errors:  <CODE>:<count>; ... [or "(none)"]
```

For multiple files, summarize as a batch:

```
TRP-RPT v=1 batch cust=<CUST0> files=<N>
formats: pdf=<N> xlsx=<N> docx=<N> doc=<N>
parse:   items_total=<N> items_avg=<N>
classify: final=<N> interim=<N> ambiguous=<N>
errors:  <CODE>:<count>; ... [or "(none)"]
```

## Constraints

- **Maximum 15 lines** in the output.
- Never include: document headings, requirement IDs, plan names, section titles, test results
  as text, or any prose from the report.
- Classification confidence is a number (0.0–1.0), not a label like "high confidence".
- If parse coverage is low (<50%), note `parse_coverage=<N>%` on the errors line.

## Common follow-ups Teacher LLM may request after TRP-RPT

- "Which document format had the lowest parse coverage?" → re-run with `--verbose` per
  format type and report aggregate coverage by format.
- "What's the distribution of items per section?" → `parse: sections=<N> items=<N>
  items_per_sec_avg=<float>` — counts only, no section names.
- "Re-run the classification with confidence threshold 0.8" → another TRP-RPT with
  `ambiguous` count at that threshold.
