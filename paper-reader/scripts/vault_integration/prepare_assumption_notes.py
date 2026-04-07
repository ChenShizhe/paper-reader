"""Assumption Note Preparation module.

Stages assumption notes or update patches in paper-bank/assumptions from
theory.md and _vault_search_results.json.
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

def _slug(name: str) -> str:
    """Convert an assumption name to a filesystem-safe slug.

    Examples:
        "Gaussian noise assumption" -> "gaussian-noise-assumption"
        "Assumption 1" -> "assumption-1"
    """
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = s.strip("-")
    s = re.sub(r"-+", "-", s)
    return s or "unknown"


def _infer_strength_classification(text: str) -> str:
    """Infer strength_classification from assumption text.

    Returns 'weak', 'strong', or 'standard'.
    """
    lower = text.lower()
    if any(w in lower for w in ["weakening", "weaker", "relaxed", "mild", "weak assumption"]):
        return "weak"
    if any(w in lower for w in ["stronger", "strict", "strong assumption", "tighter"]):
        return "strong"
    return "standard"


# ---------------------------------------------------------------------------
# Assumption extraction from theory.md
# ---------------------------------------------------------------------------

def _extract_assumptions_from_theory(theory_text: str) -> list[dict]:
    """Extract assumptions from theory.md content.

    Looks for:
    1. ## Assumptions section with list items or sub-headings
    2. **Assumption N:** inline blocks anywhere in the file
    """
    assumptions: list[dict] = []
    seen_slugs: set[str] = set()

    # Pattern 1: ## Assumptions section — capture everything until next ## heading or EOF
    section_match = re.search(
        r"##\s+Assumptions\s*\n(.*?)(?=\n##\s|\Z)",
        theory_text,
        re.DOTALL | re.IGNORECASE,
    )
    if section_match:
        section_text = section_match.group(1)
        # Match list items: "- title: description" or "* title" or "1. title"
        for m in re.finditer(
            r"^[-*]|\d+\.",
            section_text,
            re.MULTILINE,
        ):
            line_start = m.start()
            line_end = section_text.find("\n", line_start)
            line = section_text[line_start: line_end if line_end != -1 else None].strip()
            # Strip leading bullet/number
            line = re.sub(r"^[-*\d]+\.?\s*", "", line).strip()
            # Extract title (text before first colon or the full line)
            colon_idx = line.find(":")
            if colon_idx > 0:
                title = line[:colon_idx].strip().strip("*").strip()
                description = line[colon_idx + 1:].strip()
            else:
                title = line.strip("*").strip()
                description = ""
            if not title:
                continue
            s = _slug(title)
            if s in seen_slugs:
                continue
            seen_slugs.add(s)
            full_text = title + " " + description
            assumptions.append({
                "title": title,
                "description": description,
                "strength_classification": _infer_strength_classification(full_text),
                "source": "theory.md#Assumptions-section",
            })

    # Pattern 2: **Assumption N:** or **Assumption Name:** inline blocks anywhere
    for m in re.finditer(
        r"\*\*Assumption\s+([^*:]+?)\s*:?\*\*\s*:?\s*([^\n]*)",
        theory_text,
        re.IGNORECASE,
    ):
        label = m.group(1).strip()
        description = m.group(2).strip()
        title = f"Assumption {label}"
        s = _slug(title)
        if s in seen_slugs:
            continue
        seen_slugs.add(s)
        full_text = title + " " + description
        assumptions.append({
            "title": title,
            "description": description,
            "strength_classification": _infer_strength_classification(full_text),
            "source": "theory.md#inline",
        })

    return assumptions


# ---------------------------------------------------------------------------
# Vault assumption lookup
# ---------------------------------------------------------------------------

def _existing_vault_assumption_slugs(vault_search_results: dict) -> dict[str, str]:
    """Return mapping of slug -> note_path for assumption notes already in the vault."""
    slugs: dict[str, str] = {}
    for record in (vault_search_results.get("results", {}).get("assumptions") or []):
        note_path = record.get("note_path") or ""
        stem = Path(note_path).stem
        if stem:
            slugs[stem.lower()] = note_path
    return slugs


# ---------------------------------------------------------------------------
# Note content builders
# ---------------------------------------------------------------------------

def _new_assumption_frontmatter(
    cite_key: str,
    title: str,
    strength_classification: str,
    today_iso: str,
) -> str:
    data = {
        "type": "assumption",
        "title": title,
        "date": today_iso,
        "tags": ["assumption", "statistics"],
        "status": "active",
        "category": "assumption",
        "seen_in_papers": [cite_key],
        "strength_classification": strength_classification,
    }
    return "---\n" + yaml.dump(data, default_flow_style=False, allow_unicode=True) + "---\n"


def _new_assumption_body(title: str, description: str) -> str:
    desc_content = f"\n{description}\n" if description else "\n<!-- TODO: Add description -->\n"
    return (
        f"\n## Description\n{desc_content}\n"
        "## Standard Formulation\n\n<!-- TODO: Add standard formulation -->\n\n"
        "## Variants\n\n<!-- TODO: Add variants -->\n\n"
        "## Papers Using This Assumption\n\n<!-- TODO: Add paper references -->\n\n"
        "## Known Weakenings\n\n<!-- TODO: Add known weakenings -->\n\n"
        "## Seen In\n\n<!-- TODO: Add cross-references -->\n"
    )


def _update_patch_content(
    cite_key: str,
    title: str,
    strength_classification: str,
    vault_path_str: str,
) -> str:
    """Build an update patch file content for an existing vault assumption note."""
    patch_data = {
        "patch_type": "assumption_update",
        "target_vault_path": vault_path_str,
        "append_seen_in_papers": [cite_key],
        "strength_comparison": strength_classification,
    }
    header = "---\n" + yaml.dump(patch_data, default_flow_style=False, allow_unicode=True) + "---\n"
    body = (
        f"\n<!-- Update patch for: {title} -->\n"
        f"<!-- Add to seen_in_papers: {cite_key} -->\n"
        f"<!-- strength_classification in this paper: {strength_classification} -->\n"
    )
    return header + body


# ---------------------------------------------------------------------------
# Core function
# ---------------------------------------------------------------------------

def prepare_assumption_notes(
    work_dir: str | Path,
    vault_path: str | Path,
    vault_search_results_path: str | Path,
    cite_key: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Prepare staged assumption notes from theory.md and vault search results.

    Reads theory.md from <vault_path>/literature/papers/<cite_key>/theory.md.
    In dry_run mode no files are written; returns a summary dict.
    Raises SystemExit(1) on missing vault_search_results or missing theory.md (live mode).
    In dry_run mode, missing theory.md returns inputs_valid: False without exiting 1.
    """
    work_dir = Path(work_dir)
    vault_path = Path(vault_path)
    vault_search_results_path = Path(vault_search_results_path)

    # --- Validate required: vault_search_results ---
    if not vault_search_results_path.exists():
        print(
            f"ERROR: vault search results file not found: {vault_search_results_path}\n"
            "Run Task 01 (search_vault) first to generate this file.",
            file=sys.stderr,
        )
        sys.exit(1)

    vault_search_results = json.loads(vault_search_results_path.read_text(encoding="utf-8"))
    if not cite_key:
        cite_key = vault_search_results.get("cite_key") or work_dir.name

    # --- Locate theory.md ---
    theory_path = vault_path / "literature" / "papers" / cite_key / "theory.md"
    if not theory_path.exists():
        if dry_run:
            # In dry-run mode, report invalid inputs without hard exit
            return {
                "cite_key": cite_key,
                "assumptions_new_count": 0,
                "assumptions_updated_count": 0,
                "inputs_valid": False,
            }
        print(
            f"ERROR: theory.md not found: {theory_path}\n"
            "Run M6 (paper comprehension) first to generate theory.md for this paper.",
            file=sys.stderr,
        )
        sys.exit(1)

    theory_text = theory_path.read_text(encoding="utf-8")

    # --- Extract assumptions from theory.md ---
    assumptions = _extract_assumptions_from_theory(theory_text)
    if not assumptions:
        print(
            f"WARNING: No assumptions found in {theory_path} "
            "(no ## Assumptions section or **Assumption N:** blocks). "
            "Writing empty report.",
            file=sys.stderr,
        )

    # --- Check which assumptions already exist in vault ---
    existing_vault_slugs = _existing_vault_assumption_slugs(vault_search_results)

    today_iso = datetime.now(tz=timezone.utc).date().isoformat()

    new_assumptions: list[dict] = []
    updated_assumptions: list[dict] = []

    for assumption in assumptions:
        title = assumption["title"]
        description = assumption.get("description", "")
        strength = assumption["strength_classification"]
        s = _slug(title)

        if s in existing_vault_slugs:
            # Assumption exists in vault — create update patch
            vault_note_path = existing_vault_slugs[s]
            update_filename = f"{s}-update.md"
            update_path = work_dir / "assumptions" / update_filename

            if not dry_run:
                update_path.parent.mkdir(parents=True, exist_ok=True)
                patch_content = _update_patch_content(
                    cite_key, title, strength, vault_note_path
                )
                update_path.write_text(patch_content, encoding="utf-8")

            updated_assumptions.append({
                "slug": s,
                "vault_path": vault_note_path,
                "update_path": str(update_path),
            })
        else:
            # New assumption — create staged note
            staged_filename = f"{s}.md"
            staged_path = work_dir / "assumptions" / staged_filename

            if not dry_run:
                staged_path.parent.mkdir(parents=True, exist_ok=True)
                frontmatter = _new_assumption_frontmatter(
                    cite_key, title, strength, today_iso
                )
                body = _new_assumption_body(title, description)
                staged_path.write_text(frontmatter + body, encoding="utf-8")

            new_assumptions.append({
                "slug": s,
                "staged_path": str(staged_path),
            })

    if dry_run:
        return {
            "cite_key": cite_key,
            "assumptions_new_count": len(new_assumptions),
            "assumptions_updated_count": len(updated_assumptions),
            "inputs_valid": True,
        }

    # --- Write report ---
    report = {
        "schemaVersion": "1.0",
        "cite_key": cite_key,
        "generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "assumptions_new": new_assumptions,
        "assumptions_updated": updated_assumptions,
        "total_assumptions": len(new_assumptions) + len(updated_assumptions),
    }
    report_path = work_dir / "_assumption_prep_report.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    return report


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Prepare staged assumption notes from theory.md and vault search results."
    )
    p.add_argument("--work-dir", required=True, help="Paper-bank directory for the paper.")
    p.add_argument("--vault-path", required=True, help="Root of the Obsidian vault (citadel/).")
    p.add_argument(
        "--vault-search-results",
        required=True,
        help="Path to _vault_search_results.json produced by Task 01.",
    )
    p.add_argument(
        "--cite-key",
        required=False,
        default=None,
        help="Cite key used to locate theory.md in the vault. Defaults to cite_key from vault_search_results.",
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
    result = prepare_assumption_notes(
        work_dir=args.work_dir,
        vault_path=args.vault_path,
        vault_search_results_path=args.vault_search_results,
        cite_key=args.cite_key,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
