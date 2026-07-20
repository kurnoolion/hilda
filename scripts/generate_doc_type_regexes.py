"""generate_doc_type_regexes.py -- suggest doc_type_filename_rules.yaml regex
patterns from a corpus of real filenames.

Reads a text file with one filename per line (or full path per line -- script
strips directories automatically) and emits candidate YAML regex entries
grouped by "recommend" / "consider" tiers based on how many filenames each
suggested token would match. User reviews the output, keeps the useful
patterns, discards the noise, pastes into
`customizations/template_schemas/<customer_id>/doc_type_filename_rules.yaml`
under the appropriate `<doc_type>:` section.

USAGE:

    python scripts/generate_doc_type_regexes.py \\
        --input filenames_test_report.txt \\
        --doc-type test_report \\
        --output test_report_candidates.yaml

    # Then review test_report_candidates.yaml, prune, paste into the target
    # doc_type_filename_rules.yaml.

    # Tuning: raise --recommend-freq if you want fewer RECOMMENDED entries
    # (only the most common tokens); lower --min-freq if you're OK seeing
    # more CONSIDER candidates (including tokens matching only 1-2 files).

INPUT FORMAT:

    Plain text file, one filename per line. Full paths OK -- script uses
    Path(line).name. Blank lines + lines starting with # ignored.

TOKEN EXTRACTION:

    1. Strip file extension (recorded separately; extensions across corpus
       become the regex '(pdf|doc|docx|...)$' alternation).
    2. Split filename stem on: underscores, hyphens, spaces, dots, and
       camelCase boundaries.
    3. Normalize to lowercase.
    4. Discard tokens that are:
       - Pure numeric (versions, dates, ticket-IDs)
       - Common noise (v, ver, rev, final, draft, copy, new, old, v1, v2,
         date-like YYYYMMDD forms, etc.)
       - Too short (< 3 chars) unless they're known domain acronyms (see
         _KEEP_SHORT_TOKENS below -- extend as needed).

OUTPUT:

    YAML snippet. Each candidate token gets:
      - A comment line: `# Token 'foo' appears in N/M filenames`
      - A comment line: `# Sample matches: <up to 3 example filenames>`
      - A regex entry: `- regex: '.*foo.*\\.(ext1|ext2|...)$'` + `flags: IGNORECASE`

    Two tiers:
      - RECOMMENDED -- token count >= --recommend-freq (default 5). Emitted
        as active YAML.
      - CONSIDER -- token count in [--min-freq, --recommend-freq). Emitted
        as commented-out YAML so user can uncomment selectively.
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

# Tokens shorter than 3 chars are dropped by default. These are known
# domain-specific acronyms worth keeping even when short. Extend for your
# corpus (e.g. add 'sw', 'hw', 'fw' if you see them a lot).
_KEEP_SHORT_TOKENS = {
    "bt", "sw", "hw", "fw", "5g", "4g", "3g", "lte", "wifi", "ir",
    "mms", "sms", "mp3", "gpu", "cpu", "usb", "hdmi", "nfc", "gps",
    "vzw", "att", "tmo", "spr",  # US carriers commonly seen
    "srn", "eu", "us", "kr", "jp",
    "cp", "pl", "qa",  # short team names
}

# Tokens matching these patterns are dropped as noise.
_NOISE_PATTERNS = [
    re.compile(r"^v\d+$", re.IGNORECASE),           # v1, v2, v10
    re.compile(r"^ver\d+$", re.IGNORECASE),         # ver1, ver2
    re.compile(r"^rev\d+$", re.IGNORECASE),         # rev1, rev2
    re.compile(r"^r\d+$", re.IGNORECASE),           # r1, r2
    re.compile(r"^\d+$"),                            # pure digits
    re.compile(r"^\d{6,}$"),                         # long numeric IDs
    re.compile(r"^\d{4}[-_]?\d{2}[-_]?\d{2}$"),     # dates YYYY-MM-DD / YYYYMMDD
    re.compile(r"^\d{2}[-_]?\d{2}[-_]?\d{2,4}$"),   # dates MM-DD-YY(YY)
]

# Common English/office noise words. Case-insensitive.
_NOISE_WORDS = {
    "final", "draft", "copy", "backup", "old", "new", "temp", "test",
    "wip", "todo", "note", "notes", "misc", "misc1", "misc2",
    "a", "an", "the", "and", "or", "for", "with", "in", "of", "to",
    "file", "files", "document", "documents", "doc", "docs",
    "report", "reports",  # too generic when standalone; but reappear via multi-token regex
    "review", "reviewed", "sent", "received",
}

# Common file extensions expected in the corpus. Any file extension found
# in the input beyond this set is added dynamically.
_KNOWN_EXTENSIONS = {"pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx", "html", "htm", "txt"}

_SPLIT_RE = re.compile(r"[_\-\s\.]+")
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")


def _tokenize(stem: str) -> list[str]:
    """Split filename stem into normalized lowercase tokens."""
    # First split on visible separators
    parts = _SPLIT_RE.split(stem)
    # Then split each on camelCase boundaries
    tokens: list[str] = []
    for p in parts:
        if not p:
            continue
        for t in _CAMEL_RE.split(p):
            if t:
                tokens.append(t.lower())
    return tokens


def _is_useful_token(tok: str) -> bool:
    """Return True if `tok` is likely a meaningful classification signal."""
    if not tok:
        return False
    if tok in _NOISE_WORDS:
        return False
    for pat in _NOISE_PATTERNS:
        if pat.match(tok):
            return False
    if len(tok) < 3:
        return tok in _KEEP_SHORT_TOKENS
    return True


def _regex_for_token(tok: str, extensions: list[str]) -> str:
    """Build the regex string for a single token + extension alternation."""
    # Escape regex specials in the token (unlikely for word tokens but defensive).
    tok_esc = re.escape(tok)
    ext_alt = "|".join(sorted(extensions))
    return f".*{tok_esc}.*\\.({ext_alt})$"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    ap.add_argument("--input", required=True, help="Text file with one filename per line.")
    ap.add_argument("--doc-type", required=True,
                    help="doc_type key (e.g., 'test_report' or 'compliance_certification_release_notes').")
    ap.add_argument("--output", default="-",
                    help="Output YAML file path. Default '-' = stdout.")
    ap.add_argument("--min-freq", type=int, default=2,
                    help="Minimum filename count for a token to be included as a CONSIDER candidate. Default 2.")
    ap.add_argument("--recommend-freq", type=int, default=5,
                    help="Token count threshold for RECOMMENDED tier (active YAML). Default 5.")
    ap.add_argument("--max-recommended", type=int, default=15,
                    help="Cap on RECOMMENDED entries emitted; top-N by frequency. Default 15.")
    ap.add_argument("--sample-count", type=int, default=3,
                    help="Number of sample filenames to show per token in comments. Default 3.")
    args = ap.parse_args()

    in_path = Path(args.input)
    if not in_path.is_file():
        print(f"ERROR: input file not found: {in_path}", file=sys.stderr)
        return 2

    # Read filenames -- strip paths, skip blanks + comments + directory entries.
    filenames: list[str] = []
    with in_path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            name = Path(line).name  # strips directory portion if line is a full path
            if not name or "." not in name:
                continue  # skip directory entries
            filenames.append(name)

    if not filenames:
        print(f"ERROR: no filenames found in {in_path}", file=sys.stderr)
        return 3

    # Extract extensions across the corpus + tokens per filename.
    ext_counts: Counter[str] = Counter()
    token_to_files: dict[str, list[str]] = defaultdict(list)
    for fn in filenames:
        stem, dot, ext = fn.rpartition(".")
        if not stem:
            continue
        ext_l = ext.lower()
        ext_counts[ext_l] += 1
        seen_in_this_file = set()
        for tok in _tokenize(stem):
            if not _is_useful_token(tok):
                continue
            if tok in seen_in_this_file:
                continue  # don't double-count same token in one filename
            seen_in_this_file.add(tok)
            token_to_files[tok].append(fn)

    # Build extension alternation. Prefer known extensions in canonical order;
    # append any unknowns found in the corpus.
    extensions: list[str] = sorted(ext_counts.keys())
    unknowns = sorted(set(extensions) - _KNOWN_EXTENSIONS)
    if unknowns:
        # Keep them; emit a comment so user knows.
        pass

    # Sort tokens by frequency descending, then alphabetical for stable output.
    ranked = sorted(
        token_to_files.items(),
        key=lambda kv: (-len(kv[1]), kv[0]),
    )

    # Split into tiers.
    recommended = [(tok, files) for tok, files in ranked if len(files) >= args.recommend_freq]
    consider    = [(tok, files) for tok, files in ranked
                   if args.min_freq <= len(files) < args.recommend_freq]

    # Cap RECOMMENDED to keep output manageable.
    recommended = recommended[: args.max_recommended]

    # -- Render YAML -------------------------------------------------------
    total = len(filenames)
    out_lines: list[str] = []
    out_lines.append(f"# doc_type_filename_rules.yaml candidate patterns")
    out_lines.append(f"# Generated by scripts/generate_doc_type_regexes.py")
    out_lines.append(f"# Input     : {in_path}")
    out_lines.append(f"# Doc type  : {args.doc_type}")
    out_lines.append(f"# Filenames : {total} scanned")
    out_lines.append(f"# Extensions: ({'|'.join(sorted(extensions))})")
    if unknowns:
        out_lines.append(f"# Note: unknown extensions in corpus: {unknowns}")
    out_lines.append(f"# Thresholds: min-freq={args.min_freq}  recommend-freq={args.recommend_freq}"
                     f"  max-recommended={args.max_recommended}")
    out_lines.append(f"#")
    out_lines.append(f"# HOW TO USE:")
    out_lines.append(f"#   1. Review each RECOMMENDED entry. Delete any that don't fit.")
    out_lines.append(f"#   2. Uncomment CONSIDER entries you want to keep.")
    out_lines.append(f"#   3. Paste the surviving entries into your doc_type_filename_rules.yaml")
    out_lines.append(f"#      under `{args.doc_type}:` section.")
    out_lines.append("")
    out_lines.append(f"{args.doc_type}:")
    out_lines.append("")

    def _emit_tier(entries, header, active: bool):
        out_lines.append(f"  # ---- {header} ----")
        if not entries:
            out_lines.append(f"  # (no entries matched)")
            out_lines.append("")
            return
        for tok, files in entries:
            samples = files[: args.sample_count]
            prefix = "  " if active else "  # "
            out_lines.append(f"  # Token '{tok}' appears in {len(files)}/{total} filenames")
            out_lines.append(f"  # Samples: {', '.join(samples)}")
            regex = _regex_for_token(tok, extensions)
            # YAML single-quoted string: literal '\' is fine; single-quotes inside would need doubling.
            out_lines.append(f"{prefix}- regex: '{regex}'")
            out_lines.append(f"{prefix}  flags: IGNORECASE")
            out_lines.append("")

    _emit_tier(recommended, f"RECOMMENDED (>= {args.recommend_freq} filenames each)", active=True)
    _emit_tier(consider,    f"CONSIDER (>= {args.min_freq}, < {args.recommend_freq} filenames each) -- uncomment as desired",
               active=False)

    body = "\n".join(out_lines) + "\n"

    if args.output == "-":
        sys.stdout.write(body)
    else:
        Path(args.output).write_text(body, encoding="utf-8")
        print(f"[done] wrote {len(recommended)} RECOMMENDED + {len(consider)} CONSIDER candidates to {args.output}",
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
