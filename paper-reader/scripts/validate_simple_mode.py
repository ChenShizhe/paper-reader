#!/usr/bin/env python3
"""validate_simple_mode.py — validate a simple-mode summary note (proposal 06).

Checks performed:
  1. Frontmatter is present and carries all required keys with valid values.
  2. The six required section headings appear, in order, and are non-empty
     (None. / None noted. are acceptable placeholders).
  3. No per-section subdirectory exists at <vault-root>/<papers-dir>/<cite_key>/
     (simple mode is single-file).
  4. word_count frontmatter field matches the actual body word count within 5%.

Exit 0 on pass; exit 1 on any error. Warnings print to stdout but do not fail.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REQUIRED_FRONTMATTER_KEYS = {
    "cite_key",
    "source_type",
    "source_path",
    "source_format",
    "date",
    "mode",
    "word_count",
}

ALLOWED_SOURCE_TYPES = {
    "primer",
    "rating-action",
    "sell-side-note",
    "earnings-commentary",
    "industry-press",
    "regulatory-bulletin",
    "short-academic-note",
    "other",
}

ALLOWED_SOURCE_FORMATS = {"markdown", "html", "pdf", "text"}

REQUIRED_SECTIONS = [
    "Overview",
    "Key Claims",
    "Methodology Guidance",
    "Verbatim Quotes",
    "Cross-References",
    "Gaps and Limitations",
]

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a simple-mode summary note.")
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--vault-root",
        required=True,
        help="Citadel vault root (e.g., ~/Documents/citadel).",
    )
    parser.add_argument(
        "--summary-path",
        default="",
        help="Explicit summary note path (overrides vault-root lookup).",
    )
    return parser.parse_args()


def resolve_papers_dir(vault_root: Path) -> Path:
    v2 = vault_root / "literature" / "papers"
    if v2.exists():
        return v2
    return vault_root / "papers"


def parse_frontmatter(text: str) -> tuple[str, dict[str, str]]:
    match = FRONTMATTER_RE.match(text)
    if not match:
        return "", {}
    body = match.group(1)
    parsed: dict[str, str] = {}
    for line in body.splitlines():
        if ":" not in line or line.startswith(" ") or line.startswith("-"):
            continue
        key, _, value = line.partition(":")
        parsed[key.strip()] = value.strip().strip('"').strip("'")
    return body, parsed


def count_body_words(text: str) -> int:
    match = FRONTMATTER_RE.match(text)
    body = text[match.end():] if match else text
    return len(re.findall(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?", body))


def check_sections_in_order(text: str) -> list[str]:
    errors: list[str] = []
    seen_positions: list[tuple[int, str]] = []
    for heading in REQUIRED_SECTIONS:
        pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$", re.MULTILINE)
        match = pattern.search(text)
        if not match:
            errors.append(f"missing required section: ## {heading}")
        else:
            seen_positions.append((match.start(), heading))
    # Check ordering among the sections that are present.
    for i in range(1, len(seen_positions)):
        if seen_positions[i][0] < seen_positions[i - 1][0]:
            errors.append(
                f"section order violated: ## {seen_positions[i][1]} appears before "
                f"## {seen_positions[i - 1][1]}"
            )
            break
    # Check each present section is non-empty.
    for i, (pos, heading) in enumerate(seen_positions):
        next_pos = seen_positions[i + 1][0] if i + 1 < len(seen_positions) else len(text)
        section_body = text[pos:next_pos]
        # Strip the heading line.
        body_after_heading = section_body.split("\n", 1)[1] if "\n" in section_body else ""
        stripped = body_after_heading.strip()
        if not stripped:
            errors.append(f"section is empty: ## {heading}")
    return errors


def main() -> int:
    args = parse_args()
    vault_root = Path(args.vault_root).expanduser()
    if args.summary_path:
        summary_path = Path(args.summary_path).expanduser()
    else:
        papers_dir = resolve_papers_dir(vault_root)
        summary_path = papers_dir / f"{args.cite_key}.md"

    if not summary_path.exists():
        print(f"ERROR: summary note not found at {summary_path}")
        return 1

    text = summary_path.read_text(encoding="utf-8", errors="replace")
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Frontmatter completeness + enum validation.
    _, frontmatter = parse_frontmatter(text)
    missing_keys = REQUIRED_FRONTMATTER_KEYS - frontmatter.keys()
    if missing_keys:
        errors.append(
            f"frontmatter missing required keys: {sorted(missing_keys)}"
        )
    if frontmatter.get("mode") and frontmatter["mode"] != "simple":
        errors.append(
            f"frontmatter mode must be 'simple', got {frontmatter.get('mode')!r}"
        )
    source_type = frontmatter.get("source_type", "")
    if source_type and source_type not in ALLOWED_SOURCE_TYPES:
        errors.append(
            f"source_type {source_type!r} is not in allowed set {sorted(ALLOWED_SOURCE_TYPES)}"
        )
    source_format = frontmatter.get("source_format", "")
    if source_format and source_format not in ALLOWED_SOURCE_FORMATS:
        errors.append(
            f"source_format {source_format!r} is not in allowed set {sorted(ALLOWED_SOURCE_FORMATS)}"
        )

    # 2. Required sections + ordering + non-empty bodies.
    errors.extend(check_sections_in_order(text))

    # 3. No per-section subdirectory created.
    sub_dir = summary_path.parent / args.cite_key
    if sub_dir.exists() and sub_dir.is_dir():
        errors.append(
            f"simple mode must not create a per-section directory, found: {sub_dir}"
        )

    # 4. word_count tolerance check.
    declared = frontmatter.get("word_count", "")
    if declared.isdigit():
        declared_int = int(declared)
        actual = count_body_words(text)
        if declared_int == 0 and actual == 0:
            pass
        elif declared_int > 0:
            delta = abs(actual - declared_int) / max(1, declared_int)
            if delta > 0.05:
                warnings.append(
                    f"word_count mismatch: declared {declared_int}, actual body {actual} "
                    f"(delta {delta:.1%}; tolerance 5%)"
                )

    for w in warnings:
        print(f"WARNING: {w}")
    for e in errors:
        print(f"ERROR: {e}")
    if errors:
        return 1
    print(f"Simple-mode summary valid: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
