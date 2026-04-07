"""Concept Note Preparation module.

Stages concept notes or update patches in paper-bank/concepts from
notation_dict.yaml and _vault_search_results.json.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_yaml(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _slug(name: str) -> str:
    """Convert a canonical name to a filesystem-safe slug.

    Examples:
        "Bernstein Inequality" -> "bernstein-inequality"
        "dt" -> "dt"
        "bCl" -> "bcl"
    """
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    s = re.sub(r"-+", "-", s)
    return s or "unknown"


def _infer_category(entry_type: str) -> str:
    """Map a notation entry type to a concept category."""
    mapping = {
        "function": "notation",
        "variable": "notation",
        "parameter": "notation",
        "operator": "notation",
        "set": "notation",
        "constant": "notation",
        "method": "method",
        "model": "model",
        "assumption": "assumption",
        "proof-pattern": "proof-pattern",
    }
    return mapping.get(entry_type, "notation")


def _infer_section_category(section_type: str) -> str:
    """Map a catalog section type to a concept category."""
    mapping = {
        "methods": "method",
        "theory": "proof-pattern",
        "background": "notation",
        "results": "notation",
        "proofs": "proof-pattern",
        "assumptions": "assumption",
    }
    return mapping.get(section_type, "notation")


# ---------------------------------------------------------------------------
# Concept candidate extraction
# ---------------------------------------------------------------------------

def _collect_candidates(notation: dict, catalog: dict) -> list[dict]:
    """Return concept candidates from notation_dict and catalog key_terms.

    Each candidate is a dict with keys:
        canonical_name, symbol, category, source
    """
    candidates: list[dict] = []
    seen_slugs: set[str] = set()

    # From notation_dict.yaml entries
    notation_entries = notation if isinstance(notation, list) else (notation.get("entries") or [])
    for entry in notation_entries:
        name = entry.get("name") or entry.get("symbol")
        if not name:
            continue
        s = _slug(name)
        if s in seen_slugs:
            continue
        seen_slugs.add(s)
        candidates.append({
            "canonical_name": str(name),
            "symbol": str(entry.get("symbol") or name),
            "category": _infer_category(str(entry.get("type") or "")),
            "source": "notation_dict.yaml",
        })

    # From catalog key_terms per section
    for section in (catalog.get("sections") or []):
        section_type = str(section.get("section_type") or "")
        category = _infer_section_category(section_type)
        for term in (section.get("key_terms") or []):
            if not term:
                continue
            s = _slug(str(term))
            if s in seen_slugs:
                continue
            seen_slugs.add(s)
            candidates.append({
                "canonical_name": str(term),
                "symbol": str(term),
                "category": category,
                "source": "catalog_key_terms",
            })

    return candidates


# ---------------------------------------------------------------------------
# Vault concept lookup
# ---------------------------------------------------------------------------

def _existing_vault_slugs(vault_search_results: dict) -> set[str]:
    """Return the set of concept slugs already present in the vault."""
    slugs: set[str] = set()
    for record in (vault_search_results.get("results", {}).get("concepts") or []):
        note_path = record.get("note_path") or ""
        stem = Path(note_path).stem
        if stem:
            slugs.add(stem.lower())
    return slugs


# ---------------------------------------------------------------------------
# Note content builders
# ---------------------------------------------------------------------------

def _new_concept_frontmatter(
    cite_key: str,
    canonical_name: str,
    symbol: str,
    category: str,
    today_iso: str,
) -> str:
    data = {
        "type": "concept",
        "title": canonical_name,
        "date": today_iso,
        "tags": [category, "statistics"],
        "status": "active",
        "category": category,
        "canonical_notation": symbol,
        "notation_variants": [
            {"paper": cite_key, "notation": symbol}
        ],
        "applies_to_field": ["statistics"],
        "seen_in_papers": [cite_key],
    }
    return "---\n" + yaml.dump(data, default_flow_style=False, allow_unicode=True) + "---\n"


def _new_concept_body(canonical_name: str) -> str:
    return (
        f"\n## Description\n\n<!-- TODO: Add description of {canonical_name} -->\n\n"
        "## Standard Formulation\n\n<!-- TODO: Add standard formulation -->\n\n"
        "## Variants and Generalizations\n\n<!-- TODO: Add variants -->\n\n"
        "## Known Applications in Statistics\n\n<!-- TODO: Add applications -->\n\n"
        "## Seen In\n\n<!-- TODO: Add paper references -->\n\n"
        "## Knowledge Gaps\n\n<!-- TODO: Add known gaps -->\n"
    )


def _update_patch_content(
    cite_key: str,
    canonical_name: str,
    symbol: str,
    vault_path_str: str,
) -> str:
    """Build an update patch file content."""
    patch_data = {
        "patch_type": "concept_update",
        "target_vault_path": vault_path_str,
        "append_seen_in_papers": [cite_key],
        "append_notation_variants": [
            {"paper": cite_key, "notation": symbol}
        ],
    }
    header = "---\n" + yaml.dump(patch_data, default_flow_style=False, allow_unicode=True) + "---\n"
    body = (
        f"\n<!-- Update patch for: {canonical_name} -->\n"
        f"<!-- Add to seen_in_papers: {cite_key} -->\n"
        f"<!-- Add to notation_variants: paper={cite_key}, notation={symbol} -->\n"
    )
    return header + body


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def prepare_concept_notes(
    work_dir: str | Path,
    vault_path: str | Path,
    vault_search_results_path: str | Path,
    dry_run: bool = False,
) -> dict:
    """Prepare staged concept notes from notation_dict.yaml and vault search results.

    In dry_run mode no files are written; returns a summary dict.
    Raises SystemExit(1) on invalid inputs (missing vault_search_results).
    """
    work_dir = Path(work_dir)
    vault_path = Path(vault_path)
    vault_search_results_path = Path(vault_search_results_path)

    # --- Validate required inputs ---
    if not vault_search_results_path.exists():
        print(
            f"ERROR: vault search results file not found: {vault_search_results_path}\n"
            "Run Task 01 (search_vault) first to generate this file.",
            file=sys.stderr,
        )
        sys.exit(1)

    vault_search_results = json.loads(vault_search_results_path.read_text(encoding="utf-8"))
    cite_key = vault_search_results.get("cite_key") or work_dir.name

    # Load notation_dict.yaml (optional — no error if absent)
    notation_path = work_dir / "notation_dict.yaml"
    notation: dict = {}
    if notation_path.exists():
        notation = _load_yaml(notation_path) or {}

    # Load catalog for key_terms (optional)
    catalog_path = work_dir / "_catalog.yaml"
    catalog: dict = {}
    if catalog_path.exists():
        catalog = _load_yaml(catalog_path) or {}

    # Collect concept candidates
    candidates = _collect_candidates(notation, catalog)

    # Determine which concepts already exist in vault
    existing_slugs = _existing_vault_slugs(vault_search_results)

    today_iso = datetime.now(tz=timezone.utc).date().isoformat()

    new_concepts: list[dict] = []
    updated_concepts: list[dict] = []

    for candidate in candidates:
        canonical_name = candidate["canonical_name"]
        symbol = candidate["symbol"]
        category = candidate["category"]
        s = _slug(canonical_name)

        if s in existing_slugs:
            # Concept exists in vault — create update patch
            vault_note_path = f"literature/concepts/{s}.md"
            update_filename = f"{s}-update.md"
            update_path = work_dir / "concepts" / update_filename

            if not dry_run:
                update_path.parent.mkdir(parents=True, exist_ok=True)
                patch_content = _update_patch_content(
                    cite_key, canonical_name, symbol, vault_note_path
                )
                update_path.write_text(patch_content, encoding="utf-8")

            updated_concepts.append({
                "slug": s,
                "vault_path": vault_note_path,
                "update_path": str(update_path),
            })
        else:
            # New concept — create staged note
            staged_filename = f"{s}.md"
            staged_path = work_dir / "concepts" / staged_filename

            if not dry_run:
                staged_path.parent.mkdir(parents=True, exist_ok=True)
                frontmatter = _new_concept_frontmatter(
                    cite_key, canonical_name, symbol, category, today_iso
                )
                body = _new_concept_body(canonical_name)
                staged_path.write_text(frontmatter + body, encoding="utf-8")

            new_concepts.append({
                "slug": s,
                "staged_path": str(staged_path),
            })

    if dry_run:
        summary = {
            "cite_key": cite_key,
            "concepts_new_count": len(new_concepts),
            "concepts_updated_count": len(updated_concepts),
            "inputs_valid": True,
        }
        return summary

    # Write report
    report = {
        "schemaVersion": "1.0",
        "cite_key": cite_key,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "concepts_new": new_concepts,
        "concepts_updated": updated_concepts,
        "total_concepts": len(new_concepts) + len(updated_concepts),
    }
    report_path = work_dir / "_concept_prep_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Prepare staged concept notes from notation_dict.yaml and vault search results."
    )
    p.add_argument("--work-dir", required=True, help="Paper-bank directory for the paper.")
    p.add_argument("--vault-path", required=True, help="Root of the Obsidian vault (citadel/).")
    p.add_argument(
        "--vault-search-results",
        required=True,
        help="Path to _vault_search_results.json produced by Task 01.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Validate inputs and report counts; write no files.",
    )
    return p


def main() -> None:
    args = _build_parser().parse_args()
    result = prepare_concept_notes(
        work_dir=args.work_dir,
        vault_path=args.vault_path,
        vault_search_results_path=args.vault_search_results,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
