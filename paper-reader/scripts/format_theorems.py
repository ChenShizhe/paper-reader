#!/usr/bin/env python3
"""Format theorem-like blocks in markdown into a consistent blockquote style.

Pandoc's LaTeX->markdown conversion often renders theorem environments as plain
paragraphs that begin with "Theorem", "Lemma", etc. This helper detects such
blocks and rewrites them into a blockquote with a bolded lead label:

> **Theorem** 1. ...

The goal is a stable, grep-friendly convention rather than perfect semantic
reconstruction.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path


_THEOREM_LABEL_RE = re.compile(
    r"^\s*(?P<prefix>>\s*)?(?P<bold>\*\*)?(?P<label>Theorem|Lemma|Proposition|Corollary|Definition)\b",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Format theorem-like markdown blocks into blockquotes.")
    parser.add_argument("--input", required=True, help="Input markdown file")
    parser.add_argument("--output", required=True, help="Output markdown file")
    return parser.parse_args()


def _bold_leading_label(line: str) -> str:
    match = _THEOREM_LABEL_RE.match(line)
    if not match:
        return line
    if match.group("bold"):
        return line
    label = match.group("label")
    start = match.start("label")
    end = match.end("label")
    return line[:start] + f"**{label}**" + line[end:]


def _blockquote_block(lines: list[str]) -> list[str]:
    quoted: list[str] = []
    for line in lines:
        if line.startswith(">"):
            quoted.append(line)
        else:
            quoted.append("> " + line if line else ">")
    return quoted


def format_theorem_blocks(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []

    idx = 0
    while idx < len(lines):
        if lines[idx].strip() == "":
            out.append(lines[idx])
            idx += 1
            continue

        block: list[str] = []
        while idx < len(lines) and lines[idx].strip() != "":
            block.append(lines[idx])
            idx += 1

        first = block[0]
        match = _THEOREM_LABEL_RE.match(first)
        if match:
            block[0] = _bold_leading_label(first)
            out.extend(_blockquote_block(block))
        else:
            out.extend(block)

    return "\n".join(out) + ("\n" if markdown.endswith("\n") else "")


def main() -> int:
    args = parse_args()
    in_path = Path(args.input).expanduser()
    out_path = Path(args.output).expanduser()

    text = in_path.read_text(encoding="utf-8")
    formatted = format_theorem_blocks(text)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(formatted, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
