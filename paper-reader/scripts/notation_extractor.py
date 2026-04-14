#!/usr/bin/env python3
"""Step 4.3 — Notation extraction and dictionary rendering.

Searches for a dedicated notation segment in the paper bank; if none is found,
falls back to introduction segments, then to a full-text scan for inline
definitions.  Produces two artifacts:

  * ``notation_dict.yaml``  – structured notation dictionary in the paper bank
  * ``notation.md``         – rendered notation note in the Citadel vault

Notation entry types
--------------------
Valid type labels (used in YAML and in notation.md):

  function | variable | parameter | operator | set | constant

Importable API
--------------
    from notation_extractor import run_step43
    result = run_step43("smith2024neural")

CLI
---
    python3 notation_extractor.py --cite-key <key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Optional

import yaml

# Ensure the scripts directory is on sys.path so sibling modules can be imported.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Valid notation type labels for Step 4.3
NOTATION_TYPES = [
    "function",
    "variable",
    "parameter",
    "operator",
    "set",
    "constant",
]

DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"

# Keywords used to detect inline definitions when scanning free text
_DEFINITION_PATTERNS = [
    r"\bdenote\b",
    r"\blet\s+\S+\s+be\b",
    r"\bdefine\b",
    r"\bwe write\b",
    r"\bwe use\b",
    r"\bwe denote\b",
    r"\brepresent\b",
    r"\bwhere\s+\S+\s+(?:is|are)\b",
    r"\bstand for\b",
    r"\brefer to\b",
]
_DEF_REGEX = re.compile("|".join(_DEFINITION_PATTERNS), re.IGNORECASE)

# ---------------------------------------------------------------------------
# YAML frontmatter helpers (shared with other step modules)
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> dict:
    """Return YAML frontmatter dict from *text*, or {} if absent/invalid."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    end = None
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            end = i
            break
    if end is None:
        return {}
    raw = "\n".join(lines[1:end])
    try:
        result = yaml.safe_load(raw)
        return result if isinstance(result, dict) else {}
    except yaml.YAMLError:
        return {}


def _strip_frontmatter(text: str) -> str:
    """Return *text* with YAML frontmatter removed."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return text
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            return "\n".join(lines[i + 1:])
    return text


# ---------------------------------------------------------------------------
# Segment loaders
# ---------------------------------------------------------------------------


def _load_segments_by_type(paper_bank_root: Path, cite_key: str, section_type: str) -> list[dict]:
    """Return segments whose frontmatter matches *section_type*, sorted by filename."""
    seg_dir = paper_bank_root / cite_key / "segments"
    if not seg_dir.exists():
        return []
    segments = []
    for seg_path in sorted(seg_dir.glob("*.md")):
        try:
            text = seg_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        if fm.get("section_type") == section_type:
            segments.append({"path": seg_path, "text": text, "frontmatter": fm})
    return segments


def _load_all_segments(paper_bank_root: Path, cite_key: str) -> list[dict]:
    """Return all segments for *cite_key*, sorted by filename."""
    seg_dir = paper_bank_root / cite_key / "segments"
    if not seg_dir.exists():
        return []
    segments = []
    for seg_path in sorted(seg_dir.glob("*.md")):
        try:
            text = seg_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        segments.append({"path": seg_path, "text": text, "frontmatter": fm})
    return segments


# ---------------------------------------------------------------------------
# Inline definition counter (heuristic)
# ---------------------------------------------------------------------------


def _count_inline_definitions(segments: list[dict]) -> int:
    """Return a rough count of inline mathematical definitions across *segments*."""
    count = 0
    for seg in segments:
        body = _strip_frontmatter(seg["text"])
        matches = _DEF_REGEX.findall(body)
        count += len(matches)
    return count


# ---------------------------------------------------------------------------
# LLM-based notation extraction
# ---------------------------------------------------------------------------


def _build_notation_extraction_prompt(cite_key: str, segment_texts: list[str]) -> str:
    combined = "\n\n---\n\n".join(segment_texts)
    types_str = ", ".join(NOTATION_TYPES)
    return f"""You are a mathematical notation extraction assistant.

## Task
Extract all mathematical notation entries from the following text of paper `{cite_key}`.

For each notation symbol or expression found, produce a structured entry with:
- symbol: the LaTeX or plain-text symbol (e.g. "N_t", "\\lambda", "p")
- type: one of {types_str}
- description: a precise one-sentence description of what the symbol represents
- first_defined_in: the section or segment where it first appears (if determinable, else null)
- example: an optional short usage example (null if not available)

Return ONLY a JSON object with this structure:
{{
  "entries": [
    {{
      "symbol": "<symbol>",
      "type": "<one of {types_str}>",
      "description": "<one sentence>",
      "first_defined_in": "<section name or null>",
      "example": "<example or null>"
    }}
  ]
}}

## Paper Text
{combined[:6000]}
"""



# ---------------------------------------------------------------------------
# Heuristic fallback extraction (no LLM)
# ---------------------------------------------------------------------------


def _heuristic_extract_notation(segments: list[dict]) -> list[dict]:
    """Extract notation entries using regex heuristics when LLM is unavailable.

    Scans each segment for common definition patterns and classifies symbols
    with a best-effort type label from NOTATION_TYPES.
    """
    entries: list[dict] = []
    seen_symbols: set[str] = set()

    # Patterns for "let X be ...", "denote X by ...", "define X as ...", etc.
    let_be = re.compile(
        r'\b(?:let|denote|define|set)\s+\$?([A-Za-z][A-Za-z0-9_\^{}\\]*)\$?\s+(?:be|by|as|=)',
        re.IGNORECASE,
    )
    where_is = re.compile(
        r'\bwhere\s+\$?([A-Za-z][A-Za-z0-9_\^{}\\]*)\$?\s+(?:is|are|denotes?|represents?)',
        re.IGNORECASE,
    )

    def _guess_type(symbol: str, context: str) -> str:
        ctx = context.lower()
        if any(w in ctx for w in ["function", "process", "kernel", "density"]):
            return "function"
        if any(w in ctx for w in ["set", "space", "collection", "class"]):
            return "set"
        if any(w in ctx for w in ["parameter", "threshold", "bandwidth", "rate", "penalty"]):
            return "parameter"
        if any(w in ctx for w in ["constant", "fixed", "universal"]):
            return "constant"
        if any(w in ctx for w in ["operator", "norm", "matrix", "transform"]):
            return "operator"
        return "variable"

    for seg in segments:
        body = _strip_frontmatter(seg["text"])
        section = seg["frontmatter"].get("title", seg["path"].stem)

        for pattern in (let_be, where_is):
            for match in pattern.finditer(body):
                symbol = match.group(1).strip()
                if not symbol or symbol in seen_symbols:
                    continue
                seen_symbols.add(symbol)
                # Grab context: the sentence containing the match
                start = max(0, match.start() - 20)
                end = min(len(body), match.end() + 120)
                context_snippet = body[start:end].replace("\n", " ")
                entry_type = _guess_type(symbol, context_snippet)
                entries.append({
                    "symbol": symbol,
                    "type": entry_type,
                    "description": context_snippet.strip(),
                    "first_defined_in": section,
                    "example": None,
                })

    return entries


# ---------------------------------------------------------------------------
# YAML and Markdown writers
# ---------------------------------------------------------------------------


def _write_notation_dict_yaml(
    paper_bank_root: Path,
    cite_key: str,
    entries: list[dict],
    source_label: str,
) -> Path:
    """Write notation_dict.yaml to the paper bank. Returns the written path."""
    out_dir = paper_bank_root / cite_key
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "notation_dict.yaml"

    data = {
        "cite_key": cite_key,
        "extraction_step": "4.3",
        "notation_source": source_label,
        "notation_types": NOTATION_TYPES,
        "entries": entries,
    }
    out_path.write_text(
        yaml.dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )
    return out_path


def _render_notation_md(
    vault_root: Path,
    cite_key: str,
    entries: list[dict],
    source_label: str,
) -> Path:
    """Render notation.md in the Citadel vault. Returns the written path."""
    note_dir = vault_root / "literature" / "papers" / cite_key
    note_dir.mkdir(parents=True, exist_ok=True)
    out_path = note_dir / "notation.md"

    lines: list[str] = [
        f"# Notation — {cite_key}",
        "",
        f"*Source: {source_label} (Step 4.3 extraction)*",
        "",
        "## Symbol Table",
        "",
        "| Symbol | Type | Description |",
        "| --- | --- | --- |",
    ]

    for e in entries:
        sym = e.get("symbol", "")
        etype = e.get("type", "variable")
        desc = e.get("description", "").replace("|", "\\|").replace("\n", " ")
        lines.append(f"| `{sym}` | {etype} | {desc} |")

    if not entries:
        lines.append("| *(none extracted)* | — | — |")

    lines += [
        "",
        "## Notes",
        "",
        "- Valid notation types: " + ", ".join(NOTATION_TYPES),
        f"- Total entries: {len(entries)}",
        "",
    ]

    out_path.write_text("\n".join(lines), encoding="utf-8")
    return out_path


# ---------------------------------------------------------------------------
# Segment selection with fallback
# ---------------------------------------------------------------------------


def _select_segments(paper_bank_root: Path, cite_key: str) -> tuple[list[dict], str, bool]:
    """Select segments for notation extraction using the following priority:

    1. Segments with ``section_type: notation`` (primary source).
    2. Fallback: segments with ``section_type: introduction``.
    3. Fallback: all segments (full-text scan for inline definitions).

    Returns (segments, source_label, notation_segment_found).
    ``notation_segment_found`` is True when either dedicated notation or
    introduction segments are located (i.e. a structured source was found).
    """
    # 1. Dedicated notation segments
    notation_segs = _load_segments_by_type(paper_bank_root, cite_key, "notation")
    if notation_segs:
        return notation_segs, "notation section", True

    # Fallback: introduction segments
    intro_segs = _load_segments_by_type(paper_bank_root, cite_key, "introduction")
    if intro_segs:
        return intro_segs, "introduction section (fallback)", True

    # Fallback: full scan — introduction not found, use all segments
    all_segs = _load_all_segments(paper_bank_root, cite_key)
    return all_segs, "full-text scan (fallback — introduction not found)", False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def run_step43(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Step 4.3: extract notation dictionary and render notation.md.

    Searches for a dedicated notation segment, falls back to introduction
    segments, and further falls back to a full-text scan for inline
    definitions if introduction is not found.  Writes ``notation_dict.yaml``
    to the paper bank and ``notation.md`` to the Citadel vault.

    Parameters
    ----------
    cite_key:
        Paper cite key (e.g. ``"smith2024neural"``).
    paper_bank_root:
        Root of the paper bank; default ``~/Documents/paper-bank``.
    vault_root:
        Root of the Citadel vault; default ``~/Documents/citadel``.
    model:
        Anthropic model ID; overridden by ``PAPER_READER_MODEL`` env var.
    dry_run:
        When True, skip all file writes and LLM calls; return a summary dict.

    Returns
    -------
    dict with keys:
        - ``cite_key``                 – echo of input
        - ``notation_segment_found``   – bool: True when a structured source was found
        - ``inline_definitions_count`` – int: heuristic count of inline definitions seen
        - ``entries_written``          – int: notation entries written (0 in dry-run)
        - ``output_paths``             – dict: notation_dict_yaml / notation_md paths
    """
    bank_root = Path(os.path.expanduser(paper_bank_root))
    vroot = Path(os.path.expanduser(vault_root))

    expected_yaml_path = str(bank_root / cite_key / "notation_dict.yaml")
    expected_md_path = str(vroot / "literature" / "papers" / cite_key / "notation.md")

    # Select segments using priority / fallback logic
    segments, source_label, notation_segment_found = _select_segments(bank_root, cite_key)

    # Count inline definitions as a heuristic signal
    inline_definitions_count = _count_inline_definitions(segments)

    if dry_run:
        return {
            "cite_key": cite_key,
            "notation_segment_found": notation_segment_found,
            "inline_definitions_count": inline_definitions_count,
            "output_paths": {
                "notation_dict_yaml": expected_yaml_path,
                "notation_md": expected_md_path,
            },
        }

    # Non-dry-run: extract notation entries via LLM or heuristic fallback
    entries: list[dict] = []

    if segments:
        entries = _heuristic_extract_notation(segments)

    # Write artifacts
    yaml_path = _write_notation_dict_yaml(bank_root, cite_key, entries, source_label)
    md_path = _render_notation_md(vroot, cite_key, entries, source_label)

    return {
        "cite_key": cite_key,
        "notation_segment_found": notation_segment_found,
        "inline_definitions_count": inline_definitions_count,
        "entries_written": len(entries),
        "output_paths": {
            "notation_dict_yaml": str(yaml_path),
            "notation_md": str(md_path),
        },
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Step 4.3: Extract notation dictionary and render notation.md."
    )
    parser.add_argument(
        "--cite-key",
        required=True,
        help="Paper cite key (e.g. smith2024neural).",
    )
    parser.add_argument(
        "--paper-bank-root",
        default=DEFAULT_PAPER_BANK_ROOT,
        help=f"Path to the paper bank root (default: {DEFAULT_PAPER_BANK_ROOT}).",
    )
    parser.add_argument(
        "--vault-root",
        default=DEFAULT_VAULT_ROOT,
        help=f"Path to the Citadel vault root (default: {DEFAULT_VAULT_ROOT}).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model ID (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Skip file writes and LLM calls; print a JSON summary and exit.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    result = run_step43(
        cite_key=args.cite_key,
        paper_bank_root=args.paper_bank_root,
        vault_root=args.vault_root,
        model=args.model,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
