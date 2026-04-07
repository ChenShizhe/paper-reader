#!/usr/bin/env python3
"""Step 6.6 — Reference List Resolution module.

Implements the Part 6 §5.2 Reference Resolution Protocol:
  * Finds reference-tagged segments in _catalog.yaml
  * Loads dummy notes from Citadel that cite the current paper
  * Resolves/enriches each dummy note's bibliographic metadata
  * Creates new dummy notes for highly-cited references not yet covered
  * Writes all updated/new dummy notes back to the Citadel vault
  * Writes ``_ref_resolution_output.json`` to paper-bank

Importable API
--------------
    from ref_resolver import run_ref_resolution
    result = run_ref_resolution("smith2024neural")

CLI
---
    python3 ref_resolver.py --cite-key <key> [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import yaml

# Ensure the scripts directory is on sys.path so sibling modules can be imported.
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from subagent_contracts import SubagentInput, SubagentOutput  # noqa: F401
from context_loader import load_layer_a

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

REFERENCES_SECTION_TYPES = {"references", "bibliography", "reference_list", "refs"}

DEFAULT_MODEL = os.environ.get("PAPER_READER_MODEL", "claude-opus-4-6")
DEFAULT_PAPER_BANK_ROOT = os.environ.get("PAPER_BANK", os.path.expanduser("~/Documents/paper-bank"))
DEFAULT_VAULT_ROOT = "~/Documents/citadel"
DEFAULT_SKILL_ROOT = "skills/paper-reader"

OUTPUT_JSON_NAME = "_ref_resolution_output.json"

FRONTMATTER_RE = re.compile(r"\A---\n(.*?)\n---\n?", re.DOTALL)


# ---------------------------------------------------------------------------
# Helpers: catalog and note loading
# ---------------------------------------------------------------------------

def _load_catalog(paper_bank_root: Path, cite_key: str) -> dict:
    """Load _catalog.yaml for *cite_key*."""
    catalog_path = paper_bank_root / cite_key / "_catalog.yaml"
    if not catalog_path.exists():
        return {}
    with catalog_path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def _find_reference_sections(catalog: dict) -> list[dict]:
    """Return sections tagged as references or bibliography."""
    sections = catalog.get("sections", [])
    return [s for s in sections if s.get("section_type") in REFERENCES_SECTION_TYPES]


def _load_segment_text(paper_bank_root: Path, cite_key: str, segment_id: str) -> str:
    """Load a segment file by ID (filename stem matches segment_id)."""
    seg_dir = paper_bank_root / cite_key / "segments"
    seg_path = seg_dir / f"{segment_id}.md"
    if seg_path.exists():
        return seg_path.read_text(encoding="utf-8")
    return ""


def _extract_frontmatter_field(text: str, key: str) -> str | None:
    """Extract a scalar field from YAML frontmatter."""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None
    pattern = re.compile(rf"^{re.escape(key)}:\s*(.+)$", re.MULTILINE)
    m = pattern.search(match.group(1))
    if not m:
        return None
    return m.group(1).strip().strip('"').strip("'")


def _load_dummy_notes(vault_root: Path, cite_key: str) -> list[dict]:
    """Load all dummy notes in the Citadel vault cited by *cite_key*.

    Scans ``<vault_root>/literature/papers/`` (top-level .md files only) for
    notes that have ``cited_by: <cite_key>`` or ``status: dummy`` with a
    matching ``cited_by`` field in their YAML frontmatter.

    Returns a list of dicts with ``path``, ``cite_key``, and ``frontmatter``.
    """
    papers_dir = vault_root / "literature" / "papers"
    if not papers_dir.exists():
        return []

    dummy_notes = []
    for note_path in sorted(papers_dir.glob("*.md")):
        try:
            text = note_path.read_text(encoding="utf-8")
        except OSError:
            continue

        cited_by = _extract_frontmatter_field(text, "cited_by")
        status = _extract_frontmatter_field(text, "status")

        if cited_by == cite_key or status == "dummy":
            note_cite_key = _extract_frontmatter_field(text, "cite_key") or note_path.stem
            dummy_notes.append({
                "path": str(note_path),
                "cite_key": note_cite_key,
                "status": status or "unknown",
                "cited_by": cited_by,
            })

    return dummy_notes


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

def _call_llm_json(prompt: str, model: str) -> Optional[dict]:
    """Call Anthropic API expecting a JSON response. Returns parsed dict or None."""
    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    response_text = message.content[0].text.strip()

    if response_text.startswith("```"):
        lines = response_text.splitlines()
        lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        response_text = "\n".join(lines)

    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Reference resolution protocol
# ---------------------------------------------------------------------------

def _build_ref_resolution_prompt(
    cite_key: str,
    segment_text: str,
    dummy_notes: list[dict],
    layer_a: dict,
) -> str:
    """Build the reference resolution prompt for a references segment."""
    constitution_excerpt = (layer_a.get("constitution") or "")[:1500]
    dummy_summary = json.dumps(
        [{"cite_key": n["cite_key"], "status": n["status"]} for n in dummy_notes],
        ensure_ascii=False,
    )[:1000]

    return f"""You are a research reading assistant running the REFERENCE RESOLUTION PROTOCOL (Part 6 §5.2).

## Reading Constitution (excerpt)
{constitution_excerpt}

## Paper: {cite_key}

## Existing Dummy Notes (to resolve/enrich)
{dummy_summary}

## References Segment Text
{segment_text}

## Task
For each reference entry in the segment:
1. Match to an existing dummy note if possible (by author/year/title).
2. Extract: full title, all authors, venue, year, DOI/URL if present.
3. Flag discrepancies between any existing dummy note description and the actual entry.
4. Assign resolution_status: "resolved" or "unresolved" with an explanation.
5. Identify references cited ≥2 times or in theorem statements not yet in dummy notes — list as new_dummy_candidates.

Output Format (JSON only):
{{
  "resolved": [
    {{
      "cite_key": "<existing_dummy_cite_key>",
      "full_title": "<title>",
      "authors": ["<author>"],
      "venue": "<venue>",
      "year": <year>,
      "doi_or_url": "<doi or null>",
      "resolution_status": "resolved",
      "discrepancies": []
    }}
  ],
  "unresolved": [
    {{
      "cite_key": "<existing_dummy_cite_key>",
      "resolution_status": "unresolved",
      "reason": "<why unresolved>"
    }}
  ],
  "new_dummy_candidates": [
    {{
      "cited_as": "<Author YYYY>",
      "title_hint": "<partial title>",
      "importance": "high|medium|low",
      "classification": "foundation|alternative|background|technical_tool",
      "section_relevance_hints": ["<section>"]
    }}
  ]
}}
"""


def _build_dummy_note_content(
    ref_cite_key: str,
    cite_key: str,
    candidate: dict,
) -> str:
    """Build YAML-frontmatted Markdown for a new dummy note."""
    today = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
    importance = candidate.get("importance", "medium")
    classification = candidate.get("classification", "background")
    section_hints = candidate.get("section_relevance_hints", [])
    cited_as = candidate.get("cited_as", "")
    title_hint = candidate.get("title_hint", "")

    return f"""---
type: paper
title: "{title_hint}"
cite_key: {ref_cite_key}
status: dummy
cited_by: {cite_key}
cited_as: "{cited_as}"
importance: {importance}
classification: {classification}
section_relevance_hints: {json.dumps(section_hints)}
date: {today}
last_updated: {today}
---

# {title_hint or ref_cite_key}

*(Dummy note — created by ref_resolver. Requires full vault upgrade in M10.)*

## Bibliographic Hint

Cited as: {cited_as}
"""


def _update_dummy_note_frontmatter(note_path: Path, resolved: dict) -> None:
    """Update frontmatter fields in an existing dummy note file."""
    try:
        text = note_path.read_text(encoding="utf-8")
    except OSError:
        return

    match = FRONTMATTER_RE.match(text)
    if not match:
        return

    fm_text = match.group(1)
    body = text[match.end():]

    try:
        fm = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError:
        fm = {}

    fm["resolution_status"] = resolved.get("resolution_status", "resolved")
    if resolved.get("full_title"):
        fm["title"] = resolved["full_title"]
    if resolved.get("authors"):
        fm["authors"] = resolved["authors"]
    if resolved.get("venue"):
        fm["venue"] = resolved["venue"]
    if resolved.get("year"):
        fm["year"] = resolved["year"]
    if resolved.get("doi_or_url"):
        fm["doi"] = resolved["doi_or_url"]
    if resolved.get("discrepancies"):
        fm["discrepancies"] = resolved["discrepancies"]

    fm["last_updated"] = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")

    new_fm = yaml.dump(fm, allow_unicode=True, default_flow_style=False, sort_keys=False)
    new_text = f"---\n{new_fm}---\n{body}"
    note_path.write_text(new_text, encoding="utf-8")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_ref_resolution(
    cite_key: str,
    paper_bank_root: str = DEFAULT_PAPER_BANK_ROOT,
    vault_root: str = DEFAULT_VAULT_ROOT,
    skill_root: str = DEFAULT_SKILL_ROOT,
    model: str = DEFAULT_MODEL,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Run the Reference List Resolution protocol (Part 6 §5.2).

    Parameters
    ----------
    cite_key:
        Paper cite key (e.g. ``"smith2024neural"``).
    paper_bank_root:
        Root of the paper bank; default ``~/Documents/paper-bank``.
    vault_root:
        Root of the Citadel vault; default ``~/Documents/citadel``.
    skill_root:
        Root of the paper-reader skill directory (for Layer A context);
        default ``skills/paper-reader`` (relative to cwd).
    model:
        Anthropic model ID; overridden by ``PAPER_READER_MODEL`` env var.
    dry_run:
        When True, validate inputs and return dispatch plan as JSON; no file
        writes or LLM calls.

    Returns
    -------
    dict with keys:
        - ``cite_key``
        - ``refs_resolved``
        - ``refs_unresolved``
        - ``new_dummy_notes_created``
        - ``discrepancies_flagged``
    """
    bank_root = Path(os.path.expanduser(paper_bank_root))
    vroot = Path(os.path.expanduser(vault_root))

    sroot = Path(skill_root)
    if not sroot.is_absolute():
        sroot = Path.cwd() / sroot

    # Validate paper-bank entry exists
    paper_dir = bank_root / cite_key
    catalog_path = paper_dir / "_catalog.yaml"
    inputs_valid = paper_dir.exists() and catalog_path.exists()

    if not inputs_valid:
        print(
            f"Error: paper-bank entry not found for cite_key '{cite_key}'. "
            f"Expected directory: {paper_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    # Load catalog and find reference sections
    catalog = _load_catalog(bank_root, cite_key)
    ref_sections = _find_reference_sections(catalog)
    reference_segments_found = sum(len(s.get("segments", [])) for s in ref_sections)

    # Load dummy notes in Citadel cited by this paper
    dummy_notes = _load_dummy_notes(vroot, cite_key)
    dummy_notes_found = len(dummy_notes)

    # Planned output paths
    output_json_path = str(paper_dir / OUTPUT_JSON_NAME)
    vault_papers_dir = str(vroot / "literature" / "papers")
    outputs_planned = [output_json_path, vault_papers_dir]

    if dry_run:
        return {
            "cite_key": cite_key,
            "reference_segments_found": reference_segments_found,
            "dummy_notes_found": dummy_notes_found,
            "outputs_planned": outputs_planned,
            "inputs_valid": inputs_valid,
            "model": model,
        }

    # Live run: load Layer A context
    layer_a = load_layer_a(str(sroot))

    # If no references segment exists, write a warning and exit 0
    if reference_segments_found == 0:
        output = {
            "cite_key": cite_key,
            "refs_resolved": 0,
            "refs_unresolved": 0,
            "new_dummy_notes_created": 0,
            "discrepancies_flagged": 0,
            "warning": "No references-tagged segment found in catalog.",
        }
        out_path = paper_dir / OUTPUT_JSON_NAME
        out_path.write_text(json.dumps(output, indent=2, ensure_ascii=False), encoding="utf-8")
        return output

    # Load all reference segment texts
    all_ref_text_parts = []
    for section in ref_sections:
        for seg_id in section.get("segments", []):
            text = _load_segment_text(bank_root, cite_key, seg_id)
            if text:
                all_ref_text_parts.append(text)
    combined_ref_text = "\n\n".join(all_ref_text_parts)

    # Run resolution protocol via LLM
    prompt = _build_ref_resolution_prompt(cite_key, combined_ref_text, dummy_notes, layer_a)
    resolution = _call_llm_json(prompt, model)

    if resolution is None:
        resolution = {"resolved": [], "unresolved": [], "new_dummy_candidates": []}

    resolved_list: list[dict] = resolution.get("resolved", [])
    unresolved_list: list[dict] = resolution.get("unresolved", [])
    new_candidates: list[dict] = resolution.get("new_dummy_candidates", [])

    # Build cite_key lookup for existing dummy notes
    dummy_by_key: dict[str, dict] = {n["cite_key"]: n for n in dummy_notes}

    # Update existing dummy notes
    discrepancies_flagged = 0
    for ref in resolved_list:
        ref_ck = ref.get("cite_key", "")
        if ref_ck and ref_ck in dummy_by_key:
            note_path = Path(dummy_by_key[ref_ck]["path"])
            _update_dummy_note_frontmatter(note_path, ref)
        if ref.get("discrepancies"):
            discrepancies_flagged += len(ref["discrepancies"])

    for ref in unresolved_list:
        ref_ck = ref.get("cite_key", "")
        if ref_ck and ref_ck in dummy_by_key:
            note_path = Path(dummy_by_key[ref_ck]["path"])
            _update_dummy_note_frontmatter(note_path, ref)

    # Create new dummy notes
    new_dummy_notes_created = 0
    vault_papers_path = vroot / "literature" / "papers"
    vault_papers_path.mkdir(parents=True, exist_ok=True)
    notes_written: list[str] = []

    for candidate in new_candidates:
        cited_as = candidate.get("cited_as", "")
        # Derive a cite_key slug from cited_as
        ref_cite_key = re.sub(r"[^a-z0-9]", "", cited_as.lower()) or f"ref_{new_dummy_notes_created}"
        note_path = vault_papers_path / f"{ref_cite_key}.md"

        # Skip if a note for this key already exists
        if note_path.exists():
            existing_ck = _extract_frontmatter_field(
                note_path.read_text(encoding="utf-8"), "cite_key"
            )
            if existing_ck:
                continue

        content = _build_dummy_note_content(ref_cite_key, cite_key, candidate)
        note_path.write_text(content, encoding="utf-8")
        notes_written.append(str(note_path))
        new_dummy_notes_created += 1

    # Write output JSON
    output_json = {
        "cite_key": cite_key,
        "refs_resolved": len(resolved_list),
        "refs_unresolved": len(unresolved_list),
        "new_dummy_notes_created": new_dummy_notes_created,
        "discrepancies_flagged": discrepancies_flagged,
    }
    out_path = paper_dir / OUTPUT_JSON_NAME
    out_path.write_text(json.dumps(output_json, indent=2, ensure_ascii=False), encoding="utf-8")
    notes_written.append(str(out_path))

    # Return SubagentOutput-compatible dict
    subagent_result = SubagentOutput(
        cite_key=cite_key,
        section_type="references",
        status="completed",
        notes_written=notes_written,
        catalog_updates={},
        flags=[f"discrepancies:{discrepancies_flagged}"] if discrepancies_flagged else [],
        extra=output_json,
    )

    return {
        **output_json,
        "notes_written": subagent_result.notes_written,
        "flags": subagent_result.flags,
    }


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Step 6.6: Reference List Resolution — resolve references against dummy notes "
            "and emit _ref_resolution_output.json."
        )
    )
    parser.add_argument("--cite-key", required=True, help="Paper cite key.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate inputs and print dispatch plan as JSON; no file writes or LLM calls.",
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
        "--skill-root",
        default=DEFAULT_SKILL_ROOT,
        help=f"Root of paper-reader skill directory (default: {DEFAULT_SKILL_ROOT}).",
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

    result = run_ref_resolution(
        cite_key=args.cite_key,
        paper_bank_root=args.paper_bank_root,
        vault_root=args.vault_root,
        skill_root=args.skill_root,
        model=args.model,
        dry_run=args.dry_run,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
