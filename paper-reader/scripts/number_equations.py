#!/usr/bin/env python3
"""Number display equations in-place and rewrite LaTeX \\eqref{...} links.

This script post-processes a translated markdown file (typically produced by
Pandoc) to:

- Add an HTML comment immediately before each display-math block:
  `<!-- eq:label (N) -->`
- Replace LaTeX cross-references like `\\eqref{eq:main}` with `Eq. (N)`.

Only display math blocks are numbered (i.e., `$$ ... $$`), and reruns are
idempotent: if all equation markers already contain numbers, numbering is not
recomputed.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path


_FENCE_RE = re.compile(r"^\s*(```+|~~~+)")
_MARKER_RE = re.compile(
    r"^\s*<!--\s*eq:(?P<label>[^()<>]*?)(?:\s*\((?P<num>\d+)\))?\s*-->\s*$",
)
_LATEX_LABEL_RE = re.compile(r"\\label\{(?P<label>[^}]+)\}")
_EQREF_RE = re.compile(r"\\+eqref\s*\{(?P<label>[^}]+)\}")


@dataclass(frozen=True)
class EquationBlock:
    start_idx: int
    end_idx: int
    marker_idx: int | None
    marker_label: str | None
    marker_num: int | None
    latex_label: str | None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Number $$...$$ display equations and rewrite \\eqref{...} links in-place.",
    )
    parser.add_argument("--file", help="Markdown file to process in-place")
    parser.add_argument(
        "--source-file",
        help="Markdown file to process in-place (preferred alias for PDF inputs; equivalent to --file)",
    )
    parser.add_argument(
        "--label-prefix",
        default="",
        help="Prefix for synthetic equation labels (e.g. 'pdf_' yields eq:pdf_001, eq:pdf_002, …)",
    )
    parser.add_argument(
        "--cite-key",
        default="",
        help="Citation key of the paper (informational; not required for numbering)",
    )
    return parser.parse_args()


def _normalize_label(label: str) -> str:
    label = label.strip()
    if label.startswith("eq:"):
        return label[len("eq:") :]
    return label


def _iter_equation_blocks(lines: list[str]) -> list[EquationBlock]:
    blocks: list[EquationBlock] = []
    in_fence = False

    idx = 0
    while idx < len(lines):
        line = lines[idx]

        fence_match = _FENCE_RE.match(line)
        if fence_match:
            in_fence = not in_fence
            idx += 1
            continue
        if in_fence:
            idx += 1
            continue

        stripped = line.strip()
        if stripped == "$$":
            start_idx = idx
            end_idx = idx
            idx += 1
            while idx < len(lines):
                if lines[idx].strip() == "$$":
                    end_idx = idx
                    break
                idx += 1
            else:
                # Unclosed display math; treat as non-equation for safety.
                break

            marker_idx = start_idx - 1 if start_idx > 0 else None
            marker_label: str | None = None
            marker_num: int | None = None
            if marker_idx is not None:
                match = _MARKER_RE.match(lines[marker_idx])
                if match:
                    marker_label = match.group("label").strip() or None
                    num = match.group("num")
                    marker_num = int(num) if num else None
                else:
                    marker_idx = None

            latex_label: str | None = None
            for j in range(start_idx, end_idx + 1):
                m = _LATEX_LABEL_RE.search(lines[j])
                if m:
                    latex_label = m.group("label")
                    break

            blocks.append(
                EquationBlock(
                    start_idx=start_idx,
                    end_idx=end_idx,
                    marker_idx=marker_idx,
                    marker_label=marker_label,
                    marker_num=marker_num,
                    latex_label=latex_label,
                )
            )
            idx = end_idx + 1
            continue

        # One-line $$ ... $$ (rare, but handle for robustness)
        if stripped.startswith("$$") and stripped.endswith("$$") and stripped != "$$":
            start_idx = idx
            end_idx = idx
            marker_idx = start_idx - 1 if start_idx > 0 else None
            marker_label: str | None = None
            marker_num: int | None = None
            if marker_idx is not None:
                match = _MARKER_RE.match(lines[marker_idx])
                if match:
                    marker_label = match.group("label").strip() or None
                    num = match.group("num")
                    marker_num = int(num) if num else None
                else:
                    marker_idx = None

            latex_label: str | None = None
            m = _LATEX_LABEL_RE.search(line)
            if m:
                latex_label = m.group("label")

            blocks.append(
                EquationBlock(
                    start_idx=start_idx,
                    end_idx=end_idx,
                    marker_idx=marker_idx,
                    marker_label=marker_label,
                    marker_num=marker_num,
                    latex_label=latex_label,
                )
            )
            idx += 1
            continue

        idx += 1

    return blocks


def _build_mapping_from_existing(blocks: list[EquationBlock]) -> dict[str, int]:
    mapping: dict[str, int] = {}
    for block in blocks:
        if block.marker_label and block.marker_num:
            mapping[_normalize_label(block.marker_label)] = block.marker_num
    return mapping


def _number_equations(
    lines: list[str],
    blocks: list[EquationBlock],
    label_prefix: str = "",
) -> tuple[list[str], dict[str, int]]:
    all_numbered = blocks and all(block.marker_label and block.marker_num for block in blocks)
    if all_numbered:
        return lines, _build_mapping_from_existing(blocks)

    mapping: dict[str, int] = {}
    unlabeled_counter = 0

    # Collect updates (in-place replacements) and insertions separately so we
    # can apply insertions in reverse order – avoiding the need to track index
    # offsets while building the list.
    updates: dict[int, str] = {}           # original idx → replacement line
    insertions: list[tuple[int, str]] = [] # (original idx, new line to insert before it)

    for eq_num, block in enumerate(blocks, start=1):
        label: str | None = block.marker_label
        if not label and block.latex_label:
            label = _normalize_label(block.latex_label)
        if not label:
            unlabeled_counter += 1
            prefix = label_prefix if label_prefix else "unlabeled_"
            label = f"{prefix}{unlabeled_counter:03d}"

        mapping[_normalize_label(label)] = eq_num
        marker_line = f"<!-- eq:{label} ({eq_num}) -->"

        if block.marker_idx is not None:
            # Existing marker slot found – update it unless it already has a number.
            match = _MARKER_RE.match(lines[block.marker_idx])
            if match and match.group("num"):
                continue
            updates[block.marker_idx] = marker_line
        else:
            # No existing marker – insert one before the opening $$.
            insertions.append((block.start_idx, marker_line))

            # PDF mode: also insert an identical marker before the closing $$
            # so that every standalone $$ delimiter is preceded by a marker.
            if label_prefix and block.end_idx != block.start_idx:
                insertions.append((block.end_idx, marker_line))

    # Apply in-place replacements first (indices are stable at this point).
    out = list(lines)
    for idx, text in updates.items():
        out[idx] = text

    # Apply insertions in descending index order so earlier insertions don't
    # shift the positions of later ones.
    insertions.sort(key=lambda x: x[0], reverse=True)
    for idx, text in insertions:
        out.insert(idx, text)

    return out, mapping


def _rewrite_eqrefs(text: str, label_to_num: dict[str, int]) -> str:
    def repl(match: re.Match[str]) -> str:
        raw = match.group("label")
        key = _normalize_label(raw)
        num = label_to_num.get(key)
        if num is None:
            return "Eq. (?)"
        return f"Eq. ({num})"

    return _EQREF_RE.sub(repl, text)


def process_markdown(markdown: str, label_prefix: str = "") -> str:
    had_trailing_newline = markdown.endswith("\n")
    lines = markdown.splitlines()

    blocks = _iter_equation_blocks(lines)
    numbered_lines, mapping = _number_equations(lines, blocks, label_prefix=label_prefix)
    rewritten = _rewrite_eqrefs("\n".join(numbered_lines), mapping)

    if had_trailing_newline and not rewritten.endswith("\n"):
        rewritten += "\n"
    return rewritten


def main() -> int:
    args = parse_args()

    raw_path = getattr(args, "source_file", None) or args.file
    if not raw_path:
        print("error: --file or --source-file is required", file=sys.stderr)
        return 2

    path = Path(raw_path).expanduser()
    if not path.exists():
        print(f"error: file not found: {path}", file=sys.stderr)
        return 2
    if not path.is_file():
        print(f"error: not a file: {path}", file=sys.stderr)
        return 2

    label_prefix: str = args.label_prefix or ""

    original = path.read_text(encoding="utf-8")
    processed = process_markdown(original, label_prefix=label_prefix)

    if processed != original:
        path.write_text(processed, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
