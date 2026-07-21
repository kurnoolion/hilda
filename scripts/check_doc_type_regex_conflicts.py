"""check_doc_type_regex_conflicts.py -- detect classification conflicts in
doc_type_filename_rules.yaml.

Load a doc_type_filename_rules.yaml file and report:

  1. LITERAL DUPLICATES -- exact same regex string appears in two doc_type
     sections. Easy fix: pick one, delete the other.

  2. TOKEN OVERLAPS -- distinctive tokens appearing in patterns across two
     doc_types. Heuristic (regex intersection is theoretically undecidable),
     but useful in practice for spotting ambiguities like `.*sig.*` in
     compliance vs a hypothetical `.*sig.*` regex accidentally added to
     test_report.

  3. CORPUS MULTI-MATCH (optional; the most important check when you have
     real filenames) -- given a filenames.txt corpus, test each filename
     against ALL compiled regexes and report filenames that match rules in
     multiple doc_types. These are the real production problem:
     attachment_router.py _classify_doc_type returns UNRESOLVED when
     multiple doc_type patterns match, so the file gets sent to
     STAGED_NOT_CLASSIFIED NSD path per FR-86 -- misrouted.

USAGE:

    # Minimum: check literal + token overlaps
    python scripts/check_doc_type_regex_conflicts.py \\
        --rules customizations/template_schemas/MMK/doc_type_filename_rules.yaml

    # Also check corpus (best signal): pass one or more filename corpora
    python scripts/check_doc_type_regex_conflicts.py \\
        --rules customizations/template_schemas/MMK/doc_type_filename_rules.yaml \\
        --corpus filenames_test_report.txt filenames_compliance.txt

    # Emit conflicts as JSON for downstream tooling
    python scripts/check_doc_type_regex_conflicts.py \\
        --rules ... --format json > conflicts.json

EXIT CODE:

    0 -- no conflicts of any kind (or --format json, always 0 unless script fails)
    1 -- one or more conflicts detected
    2 -- invalid input (bad yaml / bad regex / file not found)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

import yaml


# Words extracted from regex patterns that we treat as too generic to flag
# as an overlap signal. Same list as the generator's noise, extended a bit.
_TOKEN_NOISE = {
    "pdf", "doc", "docx", "xls", "xlsx", "xlsm", "ppt", "pptx", "html", "htm", "txt",
    "the", "and", "or", "of", "in", "to", "for",
    "report",  # too generic if used as sole intersection signal
}

_TOKEN_EXTRACT_RE = re.compile(r"[A-Za-z][A-Za-z0-9]+")


def _extract_key_tokens(pattern: str) -> set[str]:
    """Pull identifier-like tokens from a regex pattern, excluding common
    regex metasyntax + noise. Approximation: strip ^ $ \\. .* .+ ? | (...)
    and read whatever alphanumeric substrings remain."""
    # Drop file-extension alternation section '.(pdf|doc|...)$' at end
    without_ext = re.sub(r"\\?\.\([^)]+\)\$?$", "", pattern)
    tokens = _TOKEN_EXTRACT_RE.findall(without_ext.lower())
    return {t for t in tokens if len(t) >= 3 and t not in _TOKEN_NOISE}


def _compile_rules(rules_data: dict[str, list[dict]]) -> dict[str, list[tuple[str, re.Pattern[str]]]]:
    """Compile YAML regex entries. Returns {doc_type: [(raw_pattern, compiled), ...]}."""
    compiled: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
    for doc_type, entries in rules_data.items():
        if not isinstance(entries, list):
            continue
        rows: list[tuple[str, re.Pattern[str]]] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            pat = entry.get("regex")
            if not pat:
                continue
            flags_str = (entry.get("flags") or "").upper()
            flags = 0
            if "IGNORECASE" in flags_str:
                flags |= re.IGNORECASE
            try:
                rows.append((pat, re.compile(pat, flags)))
            except re.error as exc:
                print(f"WARN: invalid regex in {doc_type}: {pat!r} -- {exc}",
                      file=sys.stderr)
        compiled[doc_type] = rows
    return compiled


def find_literal_duplicates(compiled: dict[str, list[tuple[str, re.Pattern[str]]]]) -> list[dict]:
    """Same regex string across multiple doc_types."""
    pattern_to_types: dict[str, list[str]] = defaultdict(list)
    for doc_type, rows in compiled.items():
        for pat, _ in rows:
            pattern_to_types[pat].append(doc_type)
    dupes = []
    for pat, types in pattern_to_types.items():
        if len(types) > 1:
            dupes.append({"regex": pat, "doc_types": types})
    return dupes


def find_token_overlaps(compiled: dict[str, list[tuple[str, re.Pattern[str]]]]) -> list[dict]:
    """Heuristic: distinctive tokens shared across regexes in different doc_types."""
    token_to_locations: dict[str, list[dict]] = defaultdict(list)
    for doc_type, rows in compiled.items():
        for pat, _ in rows:
            for tok in _extract_key_tokens(pat):
                token_to_locations[tok].append({"doc_type": doc_type, "regex": pat})
    overlaps = []
    for tok, locs in token_to_locations.items():
        types = {loc["doc_type"] for loc in locs}
        if len(types) > 1:
            overlaps.append({"token": tok, "locations": locs})
    return overlaps


def find_corpus_conflicts(
    compiled: dict[str, list[tuple[str, re.Pattern[str]]]],
    corpus_paths: list[Path],
) -> list[dict]:
    """The real check: for each filename in each corpus, test against ALL
    doc_type regexes; report filenames matching in multiple doc_types."""
    # Aggregate all filenames across all corpora, remembering which corpus each came from.
    filenames: list[tuple[str, str]] = []  # (source_corpus, filename)
    for p in corpus_paths:
        if not p.is_file():
            print(f"WARN: corpus file not found: {p}", file=sys.stderr)
            continue
        source = p.name
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                name = Path(line).name
                if not name or "." not in name:
                    continue
                filenames.append((source, name))

    conflicts = []
    for source, fn in filenames:
        matches: dict[str, list[str]] = defaultdict(list)  # doc_type -> matched patterns
        for doc_type, rows in compiled.items():
            for pat, cre in rows:
                if cre.search(fn):
                    matches[doc_type].append(pat)
                    break  # first match per doc_type is enough
        # Multi-doc-type match = production classification conflict
        if len(matches) > 1:
            conflicts.append({
                "filename": fn,
                "source_corpus": source,
                "matches": {dt: pats[0] for dt, pats in matches.items()},
            })
    return conflicts


def _human_report(
    literals: list[dict], overlaps: list[dict], corpus: list[dict],
    corpus_paths: list[Path],
) -> str:
    lines: list[str] = []
    lines.append("=" * 60)
    lines.append("doc_type_filename_rules.yaml conflict report")
    lines.append("=" * 60)

    lines.append("")
    lines.append(f"1. LITERAL DUPLICATES: {len(literals)}")
    lines.append("-" * 60)
    if literals:
        lines.append("   Same regex appears in multiple doc_type sections.")
        lines.append("   Fix: delete the entry from the wrong section.")
        lines.append("")
        for d in literals:
            lines.append(f"   regex: {d['regex']}")
            lines.append(f"   doc_types: {', '.join(d['doc_types'])}")
            lines.append("")
    else:
        lines.append("   (none)")

    lines.append("")
    lines.append(f"2. TOKEN OVERLAPS: {len(overlaps)}")
    lines.append("-" * 60)
    if overlaps:
        lines.append("   Distinctive tokens shared across doc_types (heuristic).")
        lines.append("   Review each: is the overlap intentional or misclassifying?")
        lines.append("")
        for o in overlaps:
            lines.append(f"   token '{o['token']}' appears in {len({loc['doc_type'] for loc in o['locations']})} doc_types:")
            for loc in o["locations"]:
                lines.append(f"     [{loc['doc_type']}] {loc['regex']}")
            lines.append("")
    else:
        lines.append("   (none)")

    lines.append("")
    lines.append(f"3. CORPUS MULTI-MATCH: {len(corpus)}")
    lines.append("-" * 60)
    if not corpus_paths:
        lines.append("   (skipped -- no --corpus files provided)")
        lines.append("   Pass --corpus <file>... to enable this check. This is the")
        lines.append("   most important signal: real filenames matching in multiple")
        lines.append("   doc_types will classify as UNRESOLVED at runtime and land")
        lines.append("   on the staged_not_classified NSD path per FR-86.")
    elif corpus:
        lines.append("   Filenames matching regexes in multiple doc_types.")
        lines.append("   Runtime effect: attachment_router._classify_doc_type ->")
        lines.append("   UNRESOLVED -> file goes to staged_not_classified NSD path.")
        lines.append("   Fix: tighten the offending regex(es) to disambiguate.")
        lines.append("")
        for c in corpus:
            lines.append(f"   filename: {c['filename']}")
            lines.append(f"   from    : {c['source_corpus']}")
            for dt, pat in c["matches"].items():
                lines.append(f"     [{dt}] {pat}")
            lines.append("")
    else:
        lines.append("   (no multi-match filenames found -- classification is clean)")

    lines.append("")
    lines.append("=" * 60)
    total_conflicts = len(literals) + len(corpus)  # tokens are heuristic, not counted as errors
    if total_conflicts == 0:
        lines.append("VERDICT: no blocking conflicts detected.")
        if overlaps:
            lines.append(f"        ({len(overlaps)} token-overlap warnings -- review recommended)")
    else:
        lines.append(f"VERDICT: {total_conflicts} conflict(s) that need attention.")
    lines.append("=" * 60)
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--rules", required=True,
                    help="Path to doc_type_filename_rules.yaml.")
    ap.add_argument("--corpus", nargs="*", default=[],
                    help="Optional filenames corpus files (one filename per line). "
                         "Enables the CORPUS MULTI-MATCH check -- the most important signal.")
    ap.add_argument("--format", choices=("human", "json"), default="human",
                    help="Output format. Default 'human'.")
    args = ap.parse_args()

    rules_path = Path(args.rules)
    if not rules_path.is_file():
        print(f"ERROR: rules file not found: {rules_path}", file=sys.stderr)
        return 2
    try:
        with rules_path.open("r", encoding="utf-8") as f:
            rules_data = yaml.safe_load(f) or {}
    except yaml.YAMLError as exc:
        print(f"ERROR: invalid YAML in {rules_path}: {exc}", file=sys.stderr)
        return 2

    if not isinstance(rules_data, dict) or not rules_data:
        print(f"ERROR: rules file has no doc_type sections: {rules_path}", file=sys.stderr)
        return 2

    compiled = _compile_rules(rules_data)
    corpus_paths = [Path(p) for p in args.corpus]

    literals = find_literal_duplicates(compiled)
    overlaps = find_token_overlaps(compiled)
    corpus = find_corpus_conflicts(compiled, corpus_paths) if corpus_paths else []

    if args.format == "json":
        payload = {
            "rules_file": str(rules_path),
            "corpus_files": [str(p) for p in corpus_paths],
            "doc_types_scanned": list(compiled.keys()),
            "regex_counts": {dt: len(rows) for dt, rows in compiled.items()},
            "literal_duplicates": literals,
            "token_overlaps": overlaps,
            "corpus_multi_matches": corpus,
        }
        print(json.dumps(payload, indent=2))
        return 0

    print(_human_report(literals, overlaps, corpus, corpus_paths))
    return 1 if (literals or corpus) else 0


if __name__ == "__main__":
    raise SystemExit(main())
