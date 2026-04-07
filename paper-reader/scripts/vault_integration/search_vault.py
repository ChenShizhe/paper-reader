"""Pre-Integration Vault Search module.

Derives search terms from _catalog.yaml and notation_dict.yaml, scans
vault categories for keyword matches, and writes _vault_search_results.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Input loading
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _extract_search_terms(catalog: dict, notation: dict) -> list[str]:
    """Return deduplicated search terms from catalog key_terms + notation symbols."""
    terms: list[str] = []

    for section in catalog.get("sections", []):
        for term in section.get("key_terms", []) or []:
            if term:
                terms.append(str(term).strip())

    notation_entries = notation if isinstance(notation, list) else notation.get("entries", [])
    for entry in notation_entries:
        name = entry.get("name") or entry.get("symbol")
        if name:
            terms.append(str(name).strip())
        desc = entry.get("description")
        if desc:
            words = str(desc).split()[:3]
            pass  # only symbol/name used per intent

    seen: set[str] = set()
    deduped: list[str] = []
    for t in terms:
        lc = t.lower()
        if lc not in seen and lc:
            seen.add(lc)
            deduped.append(t)
    return deduped


# ---------------------------------------------------------------------------
# Vault scanning
# ---------------------------------------------------------------------------

CATEGORY_MAP = {
    "papers": "paper",
    "concepts": "concept",
    "assumptions": "assumption",
    "proof_patterns": "proof-pattern",
}

VAULT_SUBDIR_MAP = {
    "papers": "literature/papers",
    "concepts": "literature/concepts",
    "assumptions": "literature/assumptions",
    "proof_patterns": "literature/proof-patterns",
}


def _parse_frontmatter(md_text: str) -> dict:
    """Extract YAML frontmatter from a Markdown file."""
    if not md_text.startswith("---"):
        return {}
    end = md_text.find("\n---", 3)
    if end == -1:
        return {}
    try:
        return yaml.safe_load(md_text[3:end]) or {}
    except yaml.YAMLError:
        return {}


def _file_matches(md_file: Path, vault_root: Path, search_terms: list[str]) -> tuple[list[str], str] | None:
    """Return (match_terms, relevance_reason) if the file matches any search term, else None."""
    try:
        text = md_file.read_text(encoding="utf-8")
    except OSError:
        return None

    fm = _parse_frontmatter(text)

    # Build a candidate string corpus from the note
    candidates: list[str] = []
    for field in ("title", "cite_key", "canonical_notation"):
        val = fm.get(field)
        if val:
            candidates.append(str(val).lower())
    for tag in fm.get("tags", []) or []:
        candidates.append(str(tag).lower())
    for author in fm.get("authors", []) or []:
        candidates.append(str(author).lower())

    # Also include the stem of the filename itself
    candidates.append(md_file.stem.lower())

    full_corpus = " ".join(candidates)

    match_terms: list[str] = []
    for term in search_terms:
        if term.lower() in full_corpus:
            match_terms.append(term)

    if not match_terms:
        return None

    note_title = fm.get("title") or md_file.stem
    reason = f"Matched on: {', '.join(match_terms[:3])} in note '{note_title}'"
    return match_terms, reason


def _scan_category(
    vault_root: Path,
    subdir: str,
    note_type: str,
    search_terms: list[str],
) -> list[dict]:
    """Scan a vault subdirectory and return matching note records."""
    category_path = vault_root / subdir
    if not category_path.exists():
        return []

    results: list[dict] = []
    md_files = list(category_path.rglob("*.md"))
    for md_file in sorted(md_files):
        match = _file_matches(md_file, vault_root, search_terms)
        if match is None:
            continue
        match_terms, reason = match
        note_path = str(md_file.relative_to(vault_root))
        results.append({
            "note_path": note_path,
            "note_type": note_type,
            "match_terms": match_terms,
            "relevance_reason": reason,
        })
    return results


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def search_vault(
    work_dir: str | Path,
    vault_path: str | Path,
    output: str | Path,
    dry_run: bool = False,
) -> dict:
    """Run vault search and return the result payload.

    In dry_run mode no files are written and a summary dict is returned.
    Raises SystemExit(1) on invalid inputs (work_dir not found / no catalog).
    """
    work_dir = Path(work_dir)
    vault_path = Path(vault_path)
    output = Path(output)

    catalog_path = work_dir / "_catalog.yaml"
    notation_path = work_dir / "notation_dict.yaml"

    # Validate required inputs
    if not catalog_path.exists():
        print(
            f"ERROR: _catalog.yaml not found in work-dir: {work_dir}",
            file=sys.stderr,
        )
        sys.exit(1)

    catalog = _load_yaml(catalog_path)
    cite_key = catalog.get("paper", {}).get("cite_key") or work_dir.name

    notation: dict = {}
    if notation_path.exists():
        notation = _load_yaml(notation_path) or {}

    search_terms = _extract_search_terms(catalog, notation)

    # Count vault directories that exist
    vault_dirs_found = sum(
        1
        for subdir in VAULT_SUBDIR_MAP.values()
        if (vault_path / subdir).exists()
    )

    if dry_run:
        summary = {
            "cite_key": cite_key,
            "vault_directories_found": vault_dirs_found,
            "search_terms_count": len(search_terms),
            "inputs_valid": True,
        }
        return summary

    # --- live run ---
    category_results: dict[str, list[dict]] = {}
    zero_hits: list[str] = []

    for category, subdir in VAULT_SUBDIR_MAP.items():
        note_type = CATEGORY_MAP[category]
        hits = _scan_category(vault_path, subdir, note_type, search_terms)
        category_results[category] = hits
        if not hits:
            zero_hits.append(category)

    total_hits = sum(len(v) for v in category_results.values())

    payload = {
        "schemaVersion": "1.0",
        "cite_key": cite_key,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "search_terms": search_terms,
        "results": {
            "papers": category_results["papers"],
            "concepts": category_results["concepts"],
            "assumptions": category_results["assumptions"],
            "proof_patterns": category_results["proof_patterns"],
        },
        "total_hits": total_hits,
        "zero_hits_categories": zero_hits,
    }

    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)

    return payload


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Pre-Integration Vault Search: find vault notes relevant to a paper.",
    )
    p.add_argument("--work-dir", required=True, help="Path to paper-bank/<cite_key>/")
    p.add_argument("--vault-path", required=True, help="Path to citadel vault root")
    p.add_argument("--output", required=True, help="Path to write _vault_search_results.json")
    p.add_argument("--dry-run", action="store_true", help="Validate inputs and print summary; do not write files")
    return p


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    result = search_vault(
        work_dir=args.work_dir,
        vault_path=args.vault_path,
        output=args.output,
        dry_run=args.dry_run,
    )

    if args.dry_run:
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
