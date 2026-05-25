# Placeholder Convention for Proprietary Identifiers in Scaffolds

**Audience**: Claude (Teacher) when writing scaffolds for `customizations/`; Cline (Student) when reading scaffolds and producing redacted compact reports.

**Anchored by**: `[D-002]` (no-proprietary-content invariant), `[D-027]` (Teacher↔Cline collaboration), `.clinerules/02-content-safety.md` (11 redaction categories).

---

## Purpose

When Claude writes scaffolds for files under `customizations/` (adapter implementations, test fixtures, MODULE.md examples, YAML schemas) that need to reference proprietary external systems, customers, devices, or people, **the proprietary identifier never appears in the scaffold itself**. A stable placeholder convention takes its place so:

- Cline can find-and-replace at TODO-completion time without reading proprietary content into the LLM context window
- Compact reports (`RPT`, `MET`, `FIX`, `QC`) can reference the same placeholder consistently
- Public github commits never contain proprietary identifiers
- The mapping from placeholder → real identifier lives only on Work PC (in env vars, sops-encrypted `.env` files, or corp git config)

## Categories and naming

Use category prefix + numeric suffix:

| Placeholder | Replaces | Examples (illustrative — never the actual real names) |
|---|---|---|
| `<SYS0>`, `<SYS1>`, ... | Proprietary external system identifier (PLM, messenger, defect tracker) | "the corp PLM system", "the proprietary internal messenger" |
| `<CUST0>`, `<CUST1>`, ... | Proprietary customer codename (carrier identifier) | "the first carrier customer onboarded in Ph-1" |
| `<DEV0>`, `<DEV1>`, ... | Proprietary device codename / model | "the first device tracker exercised in Ph-1" |
| `<TG0>`, `<TG1>`, ... | Proprietary technical-group name (if redaction needed; `tg_name` values like "HW" / "SW" are generally non-proprietary and don't need a placeholder) | rarely needed |
| `<PERSON0>`, `<PERSON1>`, ... | Proprietary individual name | rarely needed; usually scrubbed to role + initial (e.g., "TPM M.") |
| `<URL0>`, `<URL1>`, ... | Proprietary endpoint / hostname / file-server path | "the corp PLM REST endpoint base URL" |

Numbering is **per-file, stable within that file**. The first proprietary system referenced in a scaffold is `<SYS0>`; if a second appears, it's `<SYS1>`. Numbering is not global across the repo.

## Placeholder registry header

Every scaffold file that uses placeholders **must include a registry comment block** at the top, immediately after the module docstring (Python) or front-matter (Markdown/YAML). Format:

```python
"""
defecttrack_adapter — IssueTracker Protocol adapter for <SYS0>.

...rest of module docstring...
"""

# ---------------------------------------------------------------------------
# Placeholder registry (per cline-playbooks/placeholder-convention.md)
#
# <SYS0>  Proprietary defect tracking system; slug = "defecttrack"
# <URL0>  <SYS0> REST API base URL; set via env HILDA_DEFECTTRACK_BASE_URL
# ---------------------------------------------------------------------------
```

The slug ("defecttrack" above) is a non-proprietary code-friendly identifier — it appears in env var names (`HILDA_DEFECTTRACK_*`), in `customizations/issue_tracker/defecttrack_adapter.py` filename, in test parametrization (`--adapter defecttrack`), and in error context. Slugs are agreed once per system and committed in this header; they are **not proprietary** because they're chosen by the team for code use, not the system's actual public name.

## Where placeholders DO and DO NOT get substituted

**Placeholders are never substituted in the scaffold file itself.** They remain forever. The scaffold is the public artifact; placeholders are part of its identity.

Real proprietary values flow in at runtime via:

| Channel | Example |
|---|---|
| Environment variables | `HILDA_DEFECTTRACK_BASE_URL=https://...` (real URL replacing `<URL0>`) |
| sops-encrypted `.env` files per `[D-038]` | `HILDA_DEFECTTRACK_API_TOKEN=...` |
| `customizations/sharepoint_config/<deployment>.yaml` | per-deployment site URL, list internal names |
| Cline-completed TODO markers (with the values still being placeholder-relative — e.g., the TODO is an API path like `/issues/{id}/transitions` which is a fact about the system's interface shape, not the system's identity) | filled-in `_status_transition_map` dict body, filled-in `register_webhook` request body |

**Cline never substitutes a placeholder with the real proprietary identifier in any file that will be committed to public github.** If Cline produces an artifact that mentions the real identifier, it stays on company git only.

## What Cline writes when filling TODOs

When Cline opens a scaffold to fill `# TODO` markers, the workflow is:

1. Read the placeholder registry header. Note the slug.
2. Look up the real identifier via the slug → env-var-name mapping in the registry. Real value is in `HILDA_<SLUG_UPPER>_*` env on Work PC.
3. Use the real value at runtime via `os.environ["HILDA_DEFECTTRACK_BASE_URL"]`, **never hardcoded**.
4. Fill TODOs with interface-shape facts (API paths, response field names, status enum strings) — these are non-proprietary because they describe the system's *interface*, not its *identity*.
5. **Do not modify the placeholder registry header.** If a new placeholder is needed for a new identifier, add to the registry — but `<SYS0>` etc. stay stable forever.

## What Claude does NOT include in scaffolds

Even with placeholders, Claude must not infer or speculate about proprietary specifics. The scaffold should:

- Use the Protocol surface from `core/src/<module>/protocol.py` as the source of truth for method signatures (the Protocol is non-proprietary)
- Use **generic API conventions** in TODO comments (e.g., `# TODO: GET base_url + "/issues/{id}" returning {"status": ..., "fields": {...}}` — speculative request/response shape that Cline corrects against the real spec)
- Use error codes from `core/src/diagnostics/error_codes.py` (the module's ITR-/MSG-/etc. prefix per `[D-002]`)
- **Never** include guessed-at status values, transition names, or field names that pretend to be the real proprietary system's specifics. If Claude doesn't know, it writes `# TODO: <enumerate real status values from <SYS0> spec>` and lets Cline fill it.

## Examples

### Example 1: Adapter scaffold

`customizations/issue_tracker/defecttrack_adapter.py`:

```python
"""defecttrack_adapter — IssueTracker Protocol adapter for <SYS0>."""

# Placeholder registry
# <SYS0>  Proprietary defect tracking system; slug = "defecttrack"
# <URL0>  <SYS0> REST API base; env HILDA_DEFECTTRACK_BASE_URL

import os
from core.src.issue_tracker.protocol import IssueTracker, ...

class DefectTrackAdapter:
    """Adapter for <SYS0> via REST. Implements IssueTracker [D-008]."""
    def __init__(self, ...):
        self._base_url = os.environ["HILDA_DEFECTTRACK_BASE_URL"]  # <URL0>
        ...

    async def open_issue(self, ...) -> IssueRef:
        # TODO: POST <URL0>/<endpoint-from-spec> with body shape from spec
        ...
```

### Example 2: Compact report (RPT) referencing the same system

```
ITR-RPT defecttrack 2026-05-24
  tests_run=10 passed=7 failed=3 fail_methods=C02,C07,C08
  base_url=<URL0> auth=basic_redacted
  notes=<SYS0> lacks update endpoint (ITR-E007 raised on update_issue)
```

The RPT uses the same `<SYS0>` / `<URL0>` placeholders as the scaffold, even though Cline is generating it on Work PC where the real URL is known. The real URL is never in the RPT — Cline writes `<URL0>` and `<SYS0>` to make the RPT safe to paste into Claude chat.

## Boundary check

Before any file is committed to public github (whether by Claude or by hand), scan for:

- Real proprietary system names (anything that looks like a brand name, a registered trademark, a corp-internal codename without `<...>` brackets)
- Real URLs / hostnames pointing to corp-internal infrastructure
- Real customer names
- Real person names not in standard role-with-initial form

If any are found, the file does not commit. The placeholder convention is the substitution.

## Non-goals

- This convention does not replace `.clinerules/02-content-safety.md` (the 11 redaction categories) — it complements it. Redaction categories say *what* to scrub; this convention says *what to write instead*.
- This convention does not apply to `core/src/` files — those are non-proprietary by construction and use real type / module / function names freely.
- This convention does not apply to docs under `docs/compact/` — those reference systems generically ("the corp PLM adapter", "the proprietary internal messenger"); placeholders are not needed because there's no system-specific identity in the prose.
