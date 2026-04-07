#!/usr/bin/env python3
"""Build a lightweight theorem/assumption index from translated_full.md.

This script scans a paper-bank markdown artifact (typically produced by
translate_paper.py) and extracts "theorem-like" blocks (Theorem, Lemma,
Proposition, Corollary, Definition, Assumption, Remark).

Primary target format:
  > **Theorem** 1. ...

Fallback format (unquoted paragraphs) is also supported:
  **Assumption** (H1) ...

HTML div format produced by pandoc for custom LaTeX environments:
  <div class="assumptionA">
  **Assumption 1**. ...
  </div>

Custom environments defined via \\newtheorem in the LaTeX source are
detected dynamically: pass --latex-source for the standalone script, or
the markdown is scanned for \\newtheorem declarations and <div class="...">
patterns automatically.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path


THEOREM_TYPES = (
    "theorem", "lemma", "proposition", "corollary", "definition", "assumption", "remark"
)
_LABEL_RE = re.compile(
    r"^\s*(?:\*\*)?(?P<label>Theorem|Lemma|Proposition|Corollary|Definition|Assumption|Remark)(?:\*\*)?\b\s*(?P<rest>.*)$",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(r"^(?P<hashes>#{1,6})\s+(?P<title>.+?)\s*$")

# Matches all \newtheorem forms:
#   \newtheorem{name}{Display}
#   \newtheorem*{name}{Display}
#   \newtheorem{name}[shared_counter]{Display}
#   \newtheorem{name}{Display}[parent_counter]
_NEWTHEOREM_RE = re.compile(r"\\newtheorem\*?\{(\w+)\}")

# Matches \input{filename} or \input filename (with optional .tex extension)
_INPUT_RE = re.compile(r"\\input\s*\{([^}]+)\}")

# Matches pandoc-generated HTML div blocks for LaTeX theorem environments:
#   <div id="..." class="assumptionA">  or  <div class="theorem">
_DIV_OPEN_RE = re.compile(r'<div\b[^>]*\bclass="(\w+)"[^>]*>', re.IGNORECASE)
_DIV_CLOSE_RE = re.compile(r'</div\s*>', re.IGNORECASE)

# Substrings that indicate a div class names a theorem-like environment.
# Used to auto-discover custom environments (e.g. assumptionA, assumptionB).
_THEOREM_LIKE_WORDS = frozenset(
    ["theorem", "lemma", "proposition", "corollary", "definition", "assumption", "remark"]
)

# Number of lines to look ahead when capturing theorem statement bodies on the PDF path.
_PDF_LOOKAHEAD_LINES = 7


def collect_newtheorem_names(source: str) -> list[str]:
    """Return a list of environment names discovered via \\newtheorem declarations."""
    return _NEWTHEOREM_RE.findall(source)


def _clean_latex_body(text: str) -> str:
    """Strip basic LaTeX markup to produce a readable statement string."""
    # Remove display math environments
    text = re.sub(
        r'\\begin\{(?:align|equation|gather|multline)\*?\}.*?\\end\{(?:align|equation|gather|multline)\*?\}',
        ' [math] ', text, flags=re.DOTALL,
    )
    # Unwrap common text commands: \emph{X} -> X
    text = re.sub(
        r'\\(?:text|emph|textbf|textit|mathrm|mathbf|mathit|mathcal|mathbb)\{([^}]*)\}',
        r'\1', text,
    )
    # Remove remaining \command tokens
    text = re.sub(r'\\[a-zA-Z@]+\*?\s*', ' ', text)
    # Remove stray braces
    text = re.sub(r'[{}]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def extract_latex_env_bodies(latex_source: str, env_names: list[str]) -> dict[str, list[str]]:
    """Extract statement bodies from LaTeX theorem environments.

    Scans the assembled LaTeX source for each named environment.  For every
    occurrence of \\begin{envName}...\\end{envName} — for example
    \\begin{theorem}...\\end{theorem} or \\begin{lemma}...\\end{lemma} — the
    body text is captured and lightly cleaned of LaTeX markup.

    Returns a mapping: lowercase env_name -> list of statement strings in
    document order.
    """
    result: dict[str, list[str]] = {}
    for env in env_names:
        # Regex: \\begin\{theorem\}[optional_label] body \\end\{theorem\}
        pat = re.compile(
            r'\\begin\{' + re.escape(env) + r'\}'
            r'(?:\[[^\]]*\])?'
            r'\s*(.*?)\s*'
            r'\\end\{' + re.escape(env) + r'\}',
            re.DOTALL | re.IGNORECASE,
        )
        result[env.lower()] = [_clean_latex_body(m) for m in pat.findall(latex_source)]
    return result


def _discover_env_names_from_div_classes(markdown: str) -> list[str]:
    """Return env names found in <div class="..."> tags that look theorem-like.

    Pandoc wraps custom LaTeX theorem environments in HTML div blocks and uses
    the environment name as the CSS class (e.g. <div class="assumptionA">).
    Any div class containing a known theorem-type word as a substring is treated
    as a custom theorem environment and added to the detection set.
    """
    seen: set[str] = set()
    discovered: list[str] = []
    for m in _DIV_OPEN_RE.finditer(markdown):
        cls = m.group(1)
        if cls.lower() in seen:
            continue
        seen.add(cls.lower())
        if any(word in cls.lower() for word in _THEOREM_LIKE_WORDS):
            discovered.append(cls)
    return discovered


def load_latex_source(path: Path, _visited: set[Path] | None = None) -> str:
    """Load a LaTeX file and recursively expand \\input{} directives.

    Returns the assembled source text with all included files inlined.
    Circular includes are silently skipped.
    """
    if _visited is None:
        _visited = set()

    resolved = path.resolve()
    if resolved in _visited:
        return ""
    _visited.add(resolved)

    if not resolved.exists():
        return ""

    text = resolved.read_text(encoding="utf-8", errors="replace")
    base_dir = resolved.parent

    def _expand(match: re.Match) -> str:
        included = match.group(1).strip()
        inc_path = base_dir / included
        # Try with and without .tex extension
        if not inc_path.exists() and not included.endswith(".tex"):
            inc_path = base_dir / (included + ".tex")
        return load_latex_source(inc_path, _visited)

    return _INPUT_RE.sub(_expand, text)


def _build_label_re(env_names: tuple[str, ...]) -> re.Pattern:
    """Build a label-matching regex from the given environment names."""
    alternation = "|".join(re.escape(name) for name in env_names)
    return re.compile(
        rf"^\s*(?:\*\*)?(?P<label>{alternation})(?:\*\*)?\b\s*(?P<rest>.*)$",
        re.IGNORECASE,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a theorem index from translated_full.md.")
    parser.add_argument("--cite-key", required=True, help="Cite key used for the paper-bank folder")
    parser.add_argument(
        "--paper-bank-dir",
        required=True,
        help="Paper directory under paper-bank (e.g., $PAPER_BANK/<cite-key>)",
    )
    parser.add_argument(
        "--input",
        default="",
        help="Optional input markdown path. Default: <paper-bank-dir>/translated_full.md",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional output JSON path. Default: <paper-bank-dir>/_theorem_index.json",
    )
    parser.add_argument(
        "--max-preview-chars",
        type=int,
        default=280,
        help="Max characters for statement_preview.",
    )
    parser.add_argument(
        "--latex-source",
        default="",
        help=(
            "Optional path to the root LaTeX source file (e.g. main.tex). "
            "When provided, \\newtheorem declarations are parsed to detect "
            "custom theorem-like environments dynamically."
        ),
    )
    return parser.parse_args()


def _strip_blockquote_prefix(line: str) -> str:
    if not line.lstrip().startswith(">"):
        return line
    stripped = line.lstrip()
    stripped = stripped[1:]
    if stripped.startswith(" "):
        stripped = stripped[1:]
    return stripped


def _is_blockquote_block(block_lines: list[str]) -> bool:
    meaningful = [ln for ln in block_lines if ln.strip() != ""]
    if not meaningful:
        return False
    return all(ln.lstrip().startswith(">") for ln in meaningful)


def _normalize_section_title(header_title: str) -> str:
    return re.sub(r"\s+", " ", header_title).strip()


def _extract_number_and_statement(rest: str) -> tuple[str, str]:
    s = rest.strip()
    if not s:
        return "", ""

    # Common patterns: "1.", "2.1:", "(H1)", "A1."
    m = re.match(r"^(?P<num>\d+(?:\.\d+)*)\b[.:]?\s*(?P<after>.*)$", s)
    if m:
        return m.group("num"), m.group("after").strip()

    m = re.match(r"^\((?P<num>[^)]+)\)\s*(?P<after>.*)$", s)
    if m:
        return m.group("num").strip(), m.group("after").strip()

    m = re.match(r"^(?P<num>[A-Za-z]\d+)\b[.:]?\s*(?P<after>.*)$", s)
    if m:
        return m.group("num"), m.group("after").strip()

    return "", s


def _extract_title_and_statement(statement: str) -> tuple[str, str]:
    s = statement.strip()
    if not s:
        return "", ""

    # "(Title) Statement..."
    m = re.match(r"^\((?P<title>[^)]+)\)\s*(?P<after>.*)$", s)
    if m:
        return m.group("title").strip(), m.group("after").strip()

    # "Title: Statement..." (conservative: short title only)
    if ":" in s:
        left, right = s.split(":", 1)
        if 0 < len(left.strip()) <= 80:
            return left.strip(), right.strip()

    return "", s


def _statement_preview(text: str, max_chars: int) -> str:
    collapsed = re.sub(r"\s+", " ", text).strip()
    if len(collapsed) <= max_chars:
        return collapsed
    return collapsed[: max_chars - 1].rstrip() + "…"


def _parse_theorem_block(
    block_lines: list[str],
    *,
    source_section: str,
    max_preview_chars: int,
    label_re: re.Pattern,
    env_lower_to_orig: dict[str, str],
) -> dict | None:
    if not block_lines:
        return None

    is_blockquote = _is_blockquote_block(block_lines)
    content_lines = [_strip_blockquote_prefix(ln) for ln in block_lines] if is_blockquote else block_lines[:]
    content_lines = [ln.rstrip("\n") for ln in content_lines]

    first_nonempty_idx = next((i for i, ln in enumerate(content_lines) if ln.strip() != ""), None)
    if first_nonempty_idx is None:
        return None

    first = content_lines[first_nonempty_idx]
    match = label_re.match(first)
    if not match:
        return None

    raw_label = match.group("label")
    # Preserve original capitalisation of the label (e.g. AssumptionA stays AssumptionA).
    label_cased = raw_label[0].upper() + raw_label[1:]
    # Use the original env name from the declaration (e.g. "assumptionA" not "assumptiona").
    # env_lower_to_orig maps lowercase env name -> original-case env name from THEOREM_TYPES
    # or from a discovered \newtheorem declaration.
    raw_lower = raw_label.lower()
    if raw_lower not in env_lower_to_orig:
        return None
    theorem_type = env_lower_to_orig[raw_lower]

    number, remainder = _extract_number_and_statement(match.group("rest"))
    title, statement_first = _extract_title_and_statement(remainder)

    # Include remaining lines as part of the statement.
    tail_lines = content_lines[first_nonempty_idx + 1 :]
    statement_parts = [statement_first] if statement_first else []
    statement_parts.extend([ln.strip() for ln in tail_lines if ln.strip() != ""])
    statement_text = "\n".join(statement_parts).strip()

    label = label_cased + (f" {number}" if number else "")

    # type == 'assumption' override: label-pattern fallback for assumptionA/B on PDF path (PDF-ISSUE-011).
    # PDF-extracted markdown lacks div tags, so the div-class detector (M18-03) cannot fire.
    # Assumption subsets are identified instead by the label text (e.g. 'Assumption A1').
    # This override must not activate when div-class detection already produced 'assumptionA'/'assumptionB';
    # those entries bypass _parse_theorem_block's return value and have their type overridden externally.
    if theorem_type == "assumption":
        if re.match(r"Assumption\s+A\d+", label, re.IGNORECASE):
            theorem_type = "assumptionA"
        elif re.match(r"Assumption\s+B\d+", label, re.IGNORECASE):
            theorem_type = "assumptionB"

    return {
        "label": label,
        "type": theorem_type,
        "number": number,
        "title": title,
        "statement_preview": _statement_preview(statement_text, max_preview_chars),
        "source_section": source_section or "unknown",
    }


def _fill_statement_lookahead(
    entry: dict,
    lines: list[str],
    start_idx: int,
    label_re: re.Pattern,
    max_preview_chars: int,
) -> None:
    """PDF path: capture up to _PDF_LOOKAHEAD_LINES following lines as statement body.

    Called when a theorem entry has an empty statement_preview after block parsing.
    Looks ahead from start_idx without consuming lines from the main scan loop.
    Stops at section headers, div openings, or a new theorem label.
    """
    parts: list[str] = []
    collected = 0
    idx = start_idx
    while idx < len(lines) and collected < _PDF_LOOKAHEAD_LINES:
        ln = lines[idx]
        if _HEADER_RE.match(ln) or _DIV_OPEN_RE.search(ln):
            break
        stripped = ln.strip()
        if stripped and label_re.match(stripped):
            break
        if stripped:
            parts.append(stripped)
            collected += 1
        idx += 1
    if parts:
        entry["statement_preview"] = _statement_preview(" ".join(parts), max_preview_chars)


def build_theorem_index(
    markdown: str,
    *,
    max_preview_chars: int,
    extra_env_names: list[str] | None = None,
    latex_bodies: dict[str, list[str]] | None = None,
) -> list[dict]:
    # Build the dynamic environment list: start from standard types, then add
    # names from three sources (all deduplicated by lowercase key):
    #   1. extra_env_names passed by caller (e.g. from --latex-source)
    #   2. \newtheorem declarations found in the markdown text itself
    #   3. <div class="..."> class names that look like theorem environments
    all_env_names: list[str] = list(THEOREM_TYPES)
    known_lower: set[str] = {n.lower() for n in all_env_names}

    def _add_names(names: list[str]) -> None:
        for name in names:
            if name.lower() not in known_lower:
                all_env_names.append(name)
                known_lower.add(name.lower())

    if extra_env_names:
        _add_names(extra_env_names)

    # Preamble-parsing pass: scan the markdown for \newtheorem declarations
    # (e.g. from an injected LaTeX macro preamble block).
    _add_names(collect_newtheorem_names(markdown))

    # Div-class discovery pass: scan pandoc HTML div blocks for custom env names.
    _add_names(_discover_env_names_from_div_classes(markdown))

    # Map lowercase env name -> original-case env name so the 'type' field in
    # each index entry uses the raw env name (e.g. "assumptionA", not "assumptiona").
    env_lower_to_orig: dict[str, str] = {name.lower(): name for name in all_env_names}
    label_re = _build_label_re(tuple(all_env_names))

    lines = markdown.splitlines()
    theorems: list[dict] = []
    current_section = ""

    idx = 0
    while idx < len(lines):
        line = lines[idx]

        header_match = _HEADER_RE.match(line)
        if header_match:
            current_section = _normalize_section_title(header_match.group("title"))
            idx += 1
            continue

        # Div-aware scan: when pandoc wraps a theorem environment in an HTML div,
        # the CSS class carries the true environment name (e.g. "assumptionA").
        # We consume the entire div block and override the 'type' field so that
        # the entry reflects the raw environment name, not the text label.
        div_match = _DIV_OPEN_RE.search(line)
        if div_match:
            div_class = div_match.group(1)
            if div_class.lower() in env_lower_to_orig:
                env_type = env_lower_to_orig[div_class.lower()]
                idx += 1
                content_lines: list[str] = []
                while idx < len(lines):
                    if _DIV_CLOSE_RE.search(lines[idx]):
                        idx += 1
                        break
                    content_lines.append(lines[idx])
                    idx += 1
                # Parse the non-blank content lines as a single block.
                non_blank = [ln for ln in content_lines if ln.strip()]
                entry = _parse_theorem_block(
                    non_blank,
                    source_section=current_section,
                    max_preview_chars=max_preview_chars,
                    label_re=label_re,
                    env_lower_to_orig=env_lower_to_orig,
                )
                if entry is not None:
                    # Override type with the div class (raw env name from \newtheorem).
                    entry["type"] = env_type
                    theorems.append(entry)
                continue
            # Unknown div class: consume the whole block to avoid processing
            # its interior lines as standalone paragraphs.
            idx += 1
            while idx < len(lines):
                if _DIV_CLOSE_RE.search(lines[idx]):
                    idx += 1
                    break
                idx += 1
            continue

        if line.strip() == "":
            idx += 1
            continue

        # Normal paragraph / blockquote block (no enclosing div).
        block: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "":
            # Stop accumulating if the next line opens a div (handled above).
            if _DIV_OPEN_RE.search(lines[idx]):
                break
            block.append(lines[idx])
            idx += 1

        if block:
            entry = _parse_theorem_block(
                block,
                source_section=current_section,
                max_preview_chars=max_preview_chars,
                label_re=label_re,
                env_lower_to_orig=env_lower_to_orig,
            )
            if entry is not None:
                theorems.append(entry)
                # PDF path: fill empty statement by looking ahead N lines.
                if not entry["statement_preview"]:
                    _fill_statement_lookahead(entry, lines, idx, label_re, max_preview_chars)

    # LaTeX path: fill any remaining empty statements from extracted LaTeX bodies.
    if latex_bodies:
        body_counters: dict[str, int] = {}
        for entry in theorems:
            env_lower = entry["type"].lower()
            bodies_for_env = latex_bodies.get(env_lower, [])
            i = body_counters.get(env_lower, 0)
            if i < len(bodies_for_env) and not entry["statement_preview"]:
                entry["statement_preview"] = _statement_preview(
                    bodies_for_env[i], max_preview_chars
                )
            body_counters[env_lower] = i + 1

    return theorems


def main() -> int:
    args = parse_args()
    paper_bank_dir = Path(args.paper_bank_dir).expanduser()
    input_path = Path(args.input).expanduser() if args.input else paper_bank_dir / "translated_full.md"
    output_path = Path(args.output).expanduser() if args.output else paper_bank_dir / "_theorem_index.json"

    if not input_path.exists():
        raise FileNotFoundError(f"Input markdown not found: {input_path}")

    # Collect custom environment names from the LaTeX source when provided.
    extra_env_names: list[str] = []
    latex_bodies: dict[str, list[str]] | None = None
    if args.latex_source:
        latex_path = Path(args.latex_source).expanduser()
        assembled = load_latex_source(latex_path)
        extra_env_names = collect_newtheorem_names(assembled)
        known_lower = {t.lower() for t in THEOREM_TYPES}
        all_envs = list(THEOREM_TYPES) + [n for n in extra_env_names if n.lower() not in known_lower]
        latex_bodies = extract_latex_env_bodies(assembled, all_envs)

    markdown = input_path.read_text(encoding="utf-8", errors="replace")
    theorems = build_theorem_index(
        markdown,
        max_preview_chars=int(args.max_preview_chars),
        extra_env_names=extra_env_names if extra_env_names else None,
        latex_bodies=latex_bodies,
    )

    # Cross-validate: warn if fewer than 80% of entries have a non-empty statement.
    if theorems:
        filled = sum(1 for t in theorems if t.get("statement_preview"))
        if filled / len(theorems) < 0.8:
            print(
                f"Warning: only {filled}/{len(theorems)} ({filled / len(theorems):.0%}) "
                f"theorem entries have a non-empty statement field; expected \u226580%.",
                file=sys.stderr,
            )

    out_obj = {
        "cite_key": args.cite_key,
        "theorems": theorems,
    }

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(out_obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
