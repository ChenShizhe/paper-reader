#!/usr/bin/env python3
"""Step 4.1 — Metadata extraction and vault positioning for the intro subagent.

Loads the abstract segment and catalog metadata for a paper, queries the Citadel
vault read-only for related notes, calls the Anthropic LLM to produce a §Positioning
paragraph with typed vault relationship links, and writes intro.md.

Typed relationship labels: extends, cited-by-vault, uses-same-technique, contradicts, background

Importable API
--------------
    from intro_positioner import run_step41
    result = run_step41("smith2024neural")

CLI
---
    python3 intro_positioner.py --cite-key <key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import re
import yaml

# Ensure the scripts directory is on sys.path so sibling modules can be imported.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from subagent_contracts import SubagentInput, SubagentOutput  # noqa: F401  (referenced for contracts)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RELATIONSHIP_LABELS = ["extends", "cited-by-vault", "uses-same-technique", "contradicts", "background"]

DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"


# ---------------------------------------------------------------------------
# YAML frontmatter helpers
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
# Catalog loader
# ---------------------------------------------------------------------------

def _load_catalog(paper_bank_root: Path, cite_key: str) -> dict:
    """Load and return _catalog.yaml for *cite_key*."""
    catalog_path = paper_bank_root / cite_key / "_catalog.yaml"
    with open(catalog_path, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _extract_paper_meta(catalog: dict) -> dict:
    """Return the ``paper`` sub-dict from *catalog*, or {} if absent."""
    return catalog.get("paper") or {}


# ---------------------------------------------------------------------------
# Abstract segment loader
# ---------------------------------------------------------------------------

def _find_abstract_segment(paper_bank_root: Path, cite_key: str) -> Optional[str]:
    """Return the text of the first segment with ``section_type: abstract``, or None."""
    seg_dir = paper_bank_root / cite_key / "segments"
    if not seg_dir.exists():
        return None
    for seg_path in sorted(seg_dir.glob("*.md")):
        try:
            text = seg_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        if fm.get("section_type") == "abstract":
            return text
    return None


# ---------------------------------------------------------------------------
# Vault query helpers (read-only)
# ---------------------------------------------------------------------------

def _scan_vault_papers(vault_root: Path, cite_key: str, search_text: str) -> list[dict]:
    """Scan vault literature/papers/ for notes related to *cite_key*.

    A note is considered a match if:
    - its ``cite_key`` frontmatter value appears in *search_text* (title/abstract), OR
    - its ``cited_by`` frontmatter list includes *cite_key*.

    Returns a list of dicts with keys: path, cite_key, title, match_reason.
    Vault is accessed read-only — no files are modified.
    """
    papers_dir = vault_root / "literature" / "papers"
    matches: list[dict] = []

    if not papers_dir.exists():
        return matches

    # Collect both .md files and subdirectory index notes
    md_paths: list[Path] = list(papers_dir.glob("*.md"))
    for sub in papers_dir.iterdir():
        if sub.is_dir():
            for candidate in [sub / "index.md", sub / f"{sub.name}.md"]:
                if candidate.exists():
                    md_paths.append(candidate)

    for md_path in md_paths:
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        note_cite_key = fm.get("cite_key", "")
        title = fm.get("title", md_path.stem)

        reason: Optional[str] = None
        if note_cite_key and note_cite_key in search_text:
            reason = "cite_key_in_abstract"
        cited_by = fm.get("cited_by") or []
        if isinstance(cited_by, str):
            cited_by = [cited_by]
        if cite_key in cited_by:
            reason = "cited_by_reference"

        if reason:
            matches.append(
                {
                    "path": str(md_path),
                    "cite_key": note_cite_key,
                    "title": title,
                    "match_reason": reason,
                }
            )

    return matches


def _scan_vault_authors(vault_root: Path, authors: list[str]) -> list[dict]:
    """Scan vault literature/authors/ for entries matching *authors*.

    Returns a list of dicts with keys: path, name, match.
    Vault is accessed read-only — no files are modified.
    """
    authors_dir = vault_root / "literature" / "authors"
    matches: list[dict] = []

    if not authors_dir.exists():
        return matches

    authors_lower = [a.lower() for a in authors]
    for md_path in authors_dir.glob("*.md"):
        try:
            text = md_path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm = _parse_frontmatter(text)
        name = fm.get("name", md_path.stem)
        if any(a in name.lower() for a in authors_lower) or any(
            name.lower() in a for a in authors_lower
        ):
            matches.append({"path": str(md_path), "name": name, "match": True})

    return matches


# ---------------------------------------------------------------------------
# Notation entry extraction (heuristic, LaTeX-aware)
# ---------------------------------------------------------------------------

_LATEX_DEF_PATTERNS = [
    # "let $X$" or "define $X$"
    re.compile(r'(?:let|define)\s+\$([^$]{1,60})\$', re.IGNORECASE),
    # "we denote/write/use $X$"
    re.compile(r'\bwe\s+(?:denote|write|use)\s+\$([^$]{1,60})\$', re.IGNORECASE),
    # "denoted/represented/indicated by $X$"
    re.compile(r'(?:denoted|represented|indicated)\s+(?:by|as)\s+\$([^$]{1,60})\$', re.IGNORECASE),
    # "where $X$ is/are/denotes/..."
    re.compile(r'\bwhere\s+\$([^$]{1,60})\$\s+\w+', re.IGNORECASE),
    # "$X$ is/are a/an/the/each"
    re.compile(r'\$([^$]{1,60})\$[,]?\s+(?:is|are)\s+(?:a\s|an\s|the\s|each\s)', re.IGNORECASE),
    # "variable/process/function/parameter/operator $X$"
    re.compile(r'(?:variable|process|function|parameter|operator)\s+\$([^$]{1,60})\$', re.IGNORECASE),
]


def extract_notation_entries(text: str, section_label: str = "introduction") -> list[dict]:
    """Extract notation entries from *text* using LaTeX-aware heuristic patterns.

    Scans for inline LaTeX symbols ($...$) in definition contexts such as
    "let $X$ be", "denoted by $X$", "$X$ is a ...", etc.

    Returns a list of entry dicts with keys: symbol, type, description,
    first_defined_in, example — compatible with notation_dict.yaml entries.
    """
    entries: list[dict] = []
    seen: set[str] = set()

    for pattern in _LATEX_DEF_PATTERNS:
        for match in pattern.finditer(text):
            symbol = match.group(1).strip()
            if not symbol or symbol in seen or len(symbol) > 60:
                continue
            seen.add(symbol)
            start = max(0, match.start() - 30)
            end = min(len(text), match.end() + 120)
            ctx = text[start:end].replace("\n", " ").strip()
            entries.append({
                "symbol": symbol,
                "type": "variable",
                "description": ctx,
                "first_defined_in": section_label,
                "example": None,
            })

    return entries


# ---------------------------------------------------------------------------
# LLM positioning call
# ---------------------------------------------------------------------------

def _build_positioning_prompt(
    cite_key: str,
    paper_meta: dict,
    abstract_text: str,
    vault_paper_matches: list[dict],
    vault_author_matches: list[dict],
) -> str:
    """Construct the LLM prompt for the §Positioning paragraph."""
    title = paper_meta.get("title") or cite_key
    authors = paper_meta.get("authors") or []
    year = paper_meta.get("year") or "unknown"
    venue = paper_meta.get("journal") or paper_meta.get("venue") or "unknown venue"

    vault_context_lines = []
    for m in vault_paper_matches:
        vault_context_lines.append(
            f"- cite_key: {m['cite_key']} | title: {m['title']} | reason: {m['match_reason']}"
        )
    for m in vault_author_matches:
        vault_context_lines.append(f"- author in vault: {m['name']}")
    vault_context = "\n".join(vault_context_lines) if vault_context_lines else "(none found)"

    relationship_labels_str = ", ".join(RELATIONSHIP_LABELS)

    return f"""You are a research positioning assistant. Given a paper's metadata, abstract, and a list of related vault notes, produce a concise §Positioning paragraph for an Obsidian research note.

## Paper
- cite_key: {cite_key}
- title: {title}
- authors: {", ".join(str(a) for a in authors)}
- year: {year}
- venue: {venue}

## Abstract
{abstract_text.strip()}

## Vault Matches (read from vault, read-only)
{vault_context}

## Instructions
Write a §Positioning paragraph (3–5 sentences) that:
1. Situates this paper in the research landscape relative to the vault matches above.
2. Ends with a typed link list using ONLY these relationship labels: {relationship_labels_str}
3. Uses Obsidian wikilink syntax: [[cite_key|label]] e.g. [[somepaper2020|extends]]
4. After the paragraph, on a new line, state: confidence: high | medium | low

Format your response as:

<paragraph>
(3-5 sentence positioning paragraph here)

Typed links:
- [[cite_key|extends]] (if applicable)
- [[cite_key|cited-by-vault]] (if applicable)
- [[cite_key|uses-same-technique]] (if applicable)
- [[cite_key|contradicts]] (if applicable)
- [[cite_key|background]] (if applicable)
</paragraph>

confidence: <high|medium|low>
"""


def _call_llm_positioning(prompt: str, model: str) -> tuple[str, str]:
    """Call Anthropic API and return (positioning_text, confidence).

    Returns ("(LLM unavailable)", "low") if the SDK is not installed.
    """
    try:
        import anthropic
    except ImportError:
        return "(Anthropic SDK not installed — positioning not generated)", "low"

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=1024,
        messages=[{"role": "user", "content": prompt}],
    )
    response_text = message.content[0].text.strip()

    # Extract confidence level
    confidence = "low"
    for line in response_text.splitlines():
        stripped = line.strip().lower()
        if stripped.startswith("confidence:"):
            val = stripped.split(":", 1)[1].strip()
            if val in ("high", "medium", "low"):
                confidence = val
            break

    return response_text, confidence


# ---------------------------------------------------------------------------
# intro.md writer
# ---------------------------------------------------------------------------

def _write_intro_md(
    vault_root: Path,
    cite_key: str,
    positioning_text: str,
    positioning_confidence: str,
) -> Path:
    """Create intro.md at vault_root/literature/papers/<cite_key>/intro.md.

    The parent directory is created if it does not exist.
    Returns the path to the written file.
    """
    note_dir = vault_root / "literature" / "papers" / cite_key
    note_dir.mkdir(parents=True, exist_ok=True)
    intro_path = note_dir / "intro.md"

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    frontmatter = {
        "cite_key": cite_key,
        "status": "draft",
        "type": "intro",
        "created": now,
        "updated": now,
    }

    # Strip <paragraph>/<paragraph> XML wrapper tags from LLM response before writing.
    positioning_text = positioning_text.replace("<paragraph>", "").replace("</paragraph>", "")

    content_lines = [
        "---",
        yaml.dump(frontmatter, default_flow_style=False).rstrip(),
        "---",
        "",
        f"# {cite_key} — Intro",
        "",
        "## Positioning",
        "",
        positioning_text,
        "",
    ]

    intro_path.write_text("\n".join(content_lines), encoding="utf-8")
    return intro_path


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_step41(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run Step 4.1: load metadata, query vault, generate §Positioning, write intro.md.

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
        When True, skip all file writes and LLM calls; return a dry-run summary dict.

    Returns
    -------
    dict with keys:
        - ``cite_key``                  – echo of input
        - ``paper_type``                – value from catalog (or "unknown")
        - ``positioning_confidence``    – "high" | "medium" | "low"
        - ``vault_matches_found``       – int count of vault matches
        - ``intro_md_path``             – absolute path of written intro.md (or None in dry-run)
        - ``abstract_segment_found``    – bool (dry-run also includes this key)
        - ``output_path``               – same as ``intro_md_path`` (dry-run: expected path str)
    """
    bank_root = Path(os.path.expanduser(paper_bank_root))
    vroot = Path(os.path.expanduser(vault_root))

    # 1. Load catalog metadata
    catalog = _load_catalog(bank_root, cite_key)
    paper_meta = _extract_paper_meta(catalog)
    paper_type = paper_meta.get("source_format") or "unknown"
    authors: list[str] = paper_meta.get("authors") or []

    # 2. Find abstract segment
    abstract_segment = _find_abstract_segment(bank_root, cite_key)
    abstract_segment_found = abstract_segment is not None
    abstract_text = _strip_frontmatter(abstract_segment) if abstract_segment else ""

    # Build a search corpus: abstract text + title
    title = paper_meta.get("title") or ""
    search_text = f"{title} {abstract_text}"

    # 3. Query vault read-only
    vault_paper_matches = _scan_vault_papers(vroot, cite_key, search_text)
    vault_author_matches = _scan_vault_authors(vroot, authors)
    vault_matches_found = len(vault_paper_matches) + len(vault_author_matches)

    # Expected output path
    expected_intro_path = str(vroot / "literature" / "papers" / cite_key / "intro.md")

    if dry_run:
        return {
            "cite_key": cite_key,
            "abstract_segment_found": abstract_segment_found,
            "vault_matches_found": vault_matches_found,
            "output_path": expected_intro_path,
            "paper_type": paper_type,
            "positioning_confidence": None,
            "intro_md_path": None,
            "notation_entries": [],
        }

    # 4. LLM call for §Positioning
    prompt = _build_positioning_prompt(
        cite_key=cite_key,
        paper_meta=paper_meta,
        abstract_text=abstract_text,
        vault_paper_matches=vault_paper_matches,
        vault_author_matches=vault_author_matches,
    )
    positioning_text, positioning_confidence = _call_llm_positioning(prompt, model)

    # 5. Write intro.md
    intro_path = _write_intro_md(
        vault_root=vroot,
        cite_key=cite_key,
        positioning_text=positioning_text,
        positioning_confidence=positioning_confidence,
    )

    # Extract notation entries from abstract text as a heuristic seed for the notation pipeline
    notation_entries = extract_notation_entries(abstract_text, "abstract")

    return {
        "cite_key": cite_key,
        "paper_type": paper_type,
        "positioning_confidence": positioning_confidence,
        "vault_matches_found": vault_matches_found,
        "intro_md_path": str(intro_path),
        "abstract_segment_found": abstract_segment_found,
        "output_path": str(intro_path),
        "notation_entries": notation_entries,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Step 4.1: Load abstract + catalog metadata, query vault, write intro.md §Positioning."
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan without writing files or calling the LLM; print JSON to stdout.",
    )
    parser.add_argument(
        "--paper-bank-root",
        default=DEFAULT_PAPER_BANK_ROOT,
        help=f"Root of paper bank (default: {DEFAULT_PAPER_BANK_ROOT}).",
    )
    parser.add_argument(
        "--vault-root",
        default=DEFAULT_VAULT_ROOT,
        help=f"Root of Citadel vault (default: {DEFAULT_VAULT_ROOT}).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Anthropic model ID (default: {DEFAULT_MODEL}).",
    )
    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    result = run_step41(
        cite_key=args.cite_key,
        paper_bank_root=args.paper_bank_root,
        vault_root=args.vault_root,
        model=args.model,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
